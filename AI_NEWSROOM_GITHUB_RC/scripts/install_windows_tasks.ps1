[CmdletBinding()]
param(
    [string]$PythonExe = "",
    [string]$WorkDir = "",
    [ValidateRange(30, 3600)][int]$PollSeconds = 60,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$TaskName = "AI NEWSROOM - Production Runner"

if (-not $WorkDir) {
    $WorkDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
if (-not $PythonExe) {
    $pythonCommand = Get-Command python -ErrorAction Stop
    $PythonExe = $pythonCommand.Source
}

$runnerScript = Join-Path $WorkDir "scripts\production_runner.py"
if (-not (Test-Path -LiteralPath $PythonExe)) { throw "Python executable not found: $PythonExe" }
if (-not (Test-Path -LiteralPath $runnerScript)) { throw "Production runner script not found: $runnerScript" }

# SYSTEM does not inherit per-user secrets. Require machine-scoped credentials
# and never place secret values in task arguments or task metadata.
$dartKey = [Environment]::GetEnvironmentVariable("DART_API_KEY", "Machine")
$kisKey = [Environment]::GetEnvironmentVariable("KIS_APP_KEY", "Machine")
$kisSecret = [Environment]::GetEnvironmentVariable("KIS_APP_SECRET", "Machine")
if (-not $DryRun) {
    if (-not $dartKey) { throw "DART_API_KEY must be configured at Machine scope before installing the production runner." }
    if (-not $kisKey -or -not $kisSecret) { throw "KIS_APP_KEY and KIS_APP_SECRET must be configured at Machine scope before installing the production runner." }
}

$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
$trigger = New-ScheduledTaskTrigger -AtStartup
$action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "scripts\production_runner.py --poll-seconds $PollSeconds --report reports\production-runner.json" `
    -WorkingDirectory $WorkDir
$task = New-ScheduledTask `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "AI NEWSROOM single-instance production runtime"

$planned = @{
    task_name = $TaskName
    poll_seconds = $PollSeconds
    work_dir = $WorkDir
    python = $PythonExe
    run_as = "SYSTEM"
    multiple_instances = "IgnoreNew"
    execution_time_limit = "unlimited"
    runner = "scripts\production_runner.py"
}

if ($DryRun) {
    $planned | ConvertTo-Json -Compress
    exit 0
}

# Remove legacy split tasks before enabling the consolidated runner.
foreach ($legacy in @("AI NEWSROOM - Newsroom Cycle", "AI NEWSROOM - Market Cycle")) {
    $existing = Get-ScheduledTask -TaskName $legacy -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        Unregister-ScheduledTask -TaskName $legacy -Confirm:$false
    }
}

Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
$planned | ConvertTo-Json -Compress
