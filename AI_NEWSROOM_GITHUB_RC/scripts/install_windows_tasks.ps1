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
$verifierScript = Join-Path $WorkDir "scripts\verify_windows_runtime.ps1"
if (-not (Test-Path -LiteralPath $PythonExe)) { throw "Python executable not found: $PythonExe" }
if (-not (Test-Path -LiteralPath $runnerScript)) { throw "Production runner script not found: $runnerScript" }
if (-not (Test-Path -LiteralPath $verifierScript)) { throw "Runtime verifier script not found: $verifierScript" }

# Fail before task registration if the selected interpreter cannot import the
# production runtime and dashboard dependencies from this checkout.
Push-Location $WorkDir
try {
    $probeOutput = & $PythonExe -c "import fastapi, uvicorn, requests; import app.production_runner, app.main" 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Python runtime preflight failed: $($probeOutput -join ' ')"
    }
}
finally {
    Pop-Location
}

# SYSTEM does not inherit per-user environment variables. Runtime credentials
# and a custom database path therefore have to be machine-scoped. If no custom
# database path is configured, both tasks use the deterministic Windows default.
$dartKey = [Environment]::GetEnvironmentVariable("DART_API_KEY", "Machine")
$kisKey = [Environment]::GetEnvironmentVariable("KIS_APP_KEY", "Machine")
$kisSecret = [Environment]::GetEnvironmentVariable("KIS_APP_SECRET", "Machine")
$machineDbPath = [Environment]::GetEnvironmentVariable("AI_NEWSROOM_DB_PATH", "Machine")
$dbPath = if ($machineDbPath) { $machineDbPath } else { "C:\AI_NEWSROOM_DATA\newsroom.db" }
$dbDir = Split-Path -Parent $dbPath

if (-not $DryRun) {
    if (-not $dartKey) { throw "DART_API_KEY must be configured at Machine scope before installing the production runner." }
    if (-not $kisKey -or -not $kisSecret) { throw "KIS_APP_KEY and KIS_APP_SECRET must be configured at Machine scope before installing the production runner." }
    if (-not $dbDir) { throw "AI_NEWSROOM_DB_PATH must include a parent directory: $dbPath" }
    New-Item -ItemType Directory -Path $dbDir -Force | Out-Null
    $writeProbe = Join-Path $dbDir ".ai-newsroom-write-probe"
    try {
        Set-Content -LiteralPath $writeProbe -Value "ok" -Encoding ASCII
    }
    finally {
        Remove-Item -LiteralPath $writeProbe -Force -ErrorAction SilentlyContinue
    }
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
    runtime_status = "http://127.0.0.1:$ApiPort/api/runtime/status"
    db_path = $dbPath
    kis_token_cache = (Join-Path $dbDir "kis-token.json")
    verifier = "scripts\verify_windows_runtime.ps1 -ApiPort $ApiPort"
    python_preflight = $true
    start_immediately = $true
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

# AtStartup protects reboot recovery; explicit starts make a fresh install usable now.
Start-ScheduledTask -TaskName $TaskName
Start-ScheduledTask -TaskName $ApiTaskName
$planned | ConvertTo-Json -Compress
