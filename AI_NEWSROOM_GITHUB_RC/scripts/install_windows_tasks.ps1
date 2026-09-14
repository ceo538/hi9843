[CmdletBinding()]
param(
    [string]$PythonExe = "",
    [string]$WorkDir = "",
    [ValidateRange(30, 3600)][int]$PollSeconds = 60,
    [ValidateRange(1024, 65535)][int]$ApiPort = 8000,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$TaskName = "AI NEWSROOM - Production Runner"
$ApiTaskName = "AI NEWSROOM - Dashboard API"

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

$runnerAction = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "scripts\production_runner.py --poll-seconds $PollSeconds --report reports\production-runner.json" `
    -WorkingDirectory $WorkDir
$runnerTask = New-ScheduledTask `
    -Action $runnerAction `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "AI NEWSROOM single-instance production runtime"

# Keep the operational dashboard/API local-only. Remote exposure must be configured
# separately behind an authenticated reverse proxy instead of binding 0.0.0.0 here.
$apiAction = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "-m uvicorn app.main:app --host 127.0.0.1 --port $ApiPort" `
    -WorkingDirectory $WorkDir
$apiTask = New-ScheduledTask `
    -Action $apiAction `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "AI NEWSROOM local dashboard and API"

$planned = @{
    task_name = $TaskName
    api_task_name = $ApiTaskName
    poll_seconds = $PollSeconds
    api_port = $ApiPort
    api_host = "127.0.0.1"
    work_dir = $WorkDir
    python = $PythonExe
    run_as = "SYSTEM"
    multiple_instances = "IgnoreNew"
    execution_time_limit = "unlimited"
    runner = "scripts\production_runner.py"
    dashboard = "http://127.0.0.1:$ApiPort/dashboard"
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

Register-ScheduledTask -TaskName $TaskName -InputObject $runnerTask -Force | Out-Null
Register-ScheduledTask -TaskName $ApiTaskName -InputObject $apiTask -Force | Out-Null
$planned | ConvertTo-Json -Compress
