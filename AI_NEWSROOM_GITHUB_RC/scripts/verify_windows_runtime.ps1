[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)][int]$ApiPort = 8000,
    [ValidateRange(1, 120)][int]$TimeoutSeconds = 20,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$TaskName = "AI NEWSROOM - Production Runner"
$ApiTaskName = "AI NEWSROOM - Dashboard API"
$endpoint = "http://127.0.0.1:$ApiPort/api/runtime/status"
$machineDbPath = [Environment]::GetEnvironmentVariable("AI_NEWSROOM_DB_PATH", "Machine")
$dbPath = if ($machineDbPath) { $machineDbPath } else { "C:\AI_NEWSROOM_DATA\newsroom.db" }
$tokenCache = Join-Path (Split-Path -Parent $dbPath) "kis-token.json"

$plan = [ordered]@{
    task_name = $TaskName
    api_task_name = $ApiTaskName
    endpoint = $endpoint
    db_path = $dbPath
    kis_token_cache = $tokenCache
    timeout_seconds = $TimeoutSeconds
    checks = @(
        "scheduled_tasks_present",
        "dashboard_api_reachable",
        "publication_gate_closed",
        "human_approval_required",
        "machine_credentials_present"
    )
}

if ($DryRun) {
    $plan | ConvertTo-Json -Depth 5 -Compress
    exit 0
}

$runnerTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$apiTask = Get-ScheduledTask -TaskName $ApiTaskName -ErrorAction SilentlyContinue

$health = $null
$apiError = $null
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
do {
    try {
        $health = Invoke-RestMethod -Uri $endpoint -Method Get -TimeoutSec 5
        $apiError = $null
        break
    }
    catch {
        $apiError = $_.Exception.Message
        Start-Sleep -Seconds 1
    }
} while ((Get-Date) -lt $deadline)

$dartConfigured = -not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable("DART_API_KEY", "Machine"))
$kisKeyConfigured = -not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable("KIS_APP_KEY", "Machine"))
$kisSecretConfigured = -not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable("KIS_APP_SECRET", "Machine"))

$publicationGateClosed = $false
$humanApprovalRequired = $false
$runtimeStatus = $null
if ($null -ne $health) {
    $publicationGateClosed = ($health.publication_allowed -eq $false)
    $humanApprovalRequired = ($health.human_approval_required -eq $true)
    $runtimeStatus = $health.status
}

$ok = ($null -ne $runnerTask) -and
      ($null -ne $apiTask) -and
      ($null -ne $health) -and
      $publicationGateClosed -and
      $humanApprovalRequired -and
      $dartConfigured -and
      $kisKeyConfigured -and
      $kisSecretConfigured

$result = [ordered]@{
    ok = $ok
    tasks = [ordered]@{
        production_runner = [ordered]@{
            present = ($null -ne $runnerTask)
            state = if ($null -ne $runnerTask) { [string]$runnerTask.State } else { $null }
        }
        dashboard_api = [ordered]@{
            present = ($null -ne $apiTask)
            state = if ($null -ne $apiTask) { [string]$apiTask.State } else { $null }
        }
    }
    api = [ordered]@{
        reachable = ($null -ne $health)
        endpoint = $endpoint
        error = $apiError
    }
    runtime = [ordered]@{
        status = $runtimeStatus
        publication_gate_closed = $publicationGateClosed
        human_approval_required = $humanApprovalRequired
    }
    credentials = [ordered]@{
        dart_api_key = $dartConfigured
        kis_app_key = $kisKeyConfigured
        kis_app_secret = $kisSecretConfigured
    }
    storage = [ordered]@{
        db_path = $dbPath
        db_exists = (Test-Path -LiteralPath $dbPath)
        kis_token_cache = $tokenCache
        kis_token_cache_exists = (Test-Path -LiteralPath $tokenCache)
    }
}

$result | ConvertTo-Json -Depth 6 -Compress
if ($ok) { exit 0 }
exit 1
