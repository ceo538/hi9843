[CmdletBinding()]
param(
    [string]$PythonExe = "",
    [string]$WorkDir = "",
    [ValidateRange(5, 1440)][int]$NewsIntervalMinutes = 5,
    [ValidateRange(5, 1440)][int]$MarketIntervalMinutes = 5,
    [switch]$EnableMarket,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$NewsTaskName = "AI NEWSROOM - Newsroom Cycle"
$MarketTaskName = "AI NEWSROOM - Market Cycle"

if (-not $WorkDir) {
    $WorkDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
if (-not $PythonExe) {
    $pythonCommand = Get-Command python -ErrorAction Stop
    $PythonExe = $pythonCommand.Source
}

$newsScript = Join-Path $WorkDir "scripts\newsroom_cycle.py"
$marketScript = Join-Path $WorkDir "scripts\market_cycle.py"
if (-not (Test-Path -LiteralPath $PythonExe)) { throw "Python executable not found: $PythonExe" }
if (-not (Test-Path -LiteralPath $newsScript)) { throw "Newsroom cycle script not found: $newsScript" }
if ($EnableMarket -and -not (Test-Path -LiteralPath $marketScript)) { throw "Market cycle script not found: $marketScript" }

# SYSTEM tasks do not inherit interactive-user environment variables. Require
# machine-scoped credentials rather than writing secrets into task arguments.
$dartKey = [Environment]::GetEnvironmentVariable("DART_API_KEY", "Machine")
if (-not $dartKey -and -not $DryRun) {
    throw "DART_API_KEY is not configured at Machine scope. Set it before installing the production task."
}
if ($EnableMarket -and -not $DryRun) {
    $kisKey = [Environment]::GetEnvironmentVariable("KIS_APP_KEY", "Machine")
    $kisSecret = [Environment]::GetEnvironmentVariable("KIS_APP_SECRET", "Machine")
    if (-not $kisKey -or -not $kisSecret) {
        throw "KIS_APP_KEY and KIS_APP_SECRET must be configured at Machine scope before enabling the market task."
    }
}

$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 4)

function New-RepeatingTriggers([int]$Minutes) {
    $startup = New-ScheduledTaskTrigger -AtStartup
    $repeat = New-ScheduledTaskTrigger `
        -Once `
        -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes $Minutes)
    return @($startup, $repeat)
}

$newsAction = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "scripts\newsroom_cycle.py --bootstrap-defaults --report reports\newsroom-cycle.json" `
    -WorkingDirectory $WorkDir
$newsTask = New-ScheduledTask `
    -Action $newsAction `
    -Trigger (New-RepeatingTriggers $NewsIntervalMinutes) `
    -Principal $principal `
    -Settings $settings `
    -Description "AI NEWSROOM source collection and deterministic editorial pipeline"

$planned = @{
    news_task = $NewsTaskName
    news_interval_minutes = $NewsIntervalMinutes
    market_enabled = [bool]$EnableMarket
    market_task = if ($EnableMarket) { $MarketTaskName } else { $null }
    market_interval_minutes = if ($EnableMarket) { $MarketIntervalMinutes } else { $null }
    work_dir = $WorkDir
    python = $PythonExe
    run_as = "SYSTEM"
    multiple_instances = "IgnoreNew"
}

if ($DryRun) {
    $planned | ConvertTo-Json -Compress
    exit 0
}

Register-ScheduledTask -TaskName $NewsTaskName -InputObject $newsTask -Force | Out-Null

if ($EnableMarket) {
    $marketAction = New-ScheduledTaskAction `
        -Execute $PythonExe `
        -Argument "scripts\market_cycle.py --report reports\market-cycle.json" `
        -WorkingDirectory $WorkDir
    $marketTask = New-ScheduledTask `
        -Action $marketAction `
        -Trigger (New-RepeatingTriggers $MarketIntervalMinutes) `
        -Principal $principal `
        -Settings $settings `
        -Description "AI NEWSROOM periodic KIS market snapshot collection"
    Register-ScheduledTask -TaskName $MarketTaskName -InputObject $marketTask -Force | Out-Null
}

$planned | ConvertTo-Json -Compress
