[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$taskNames = @(
    "AI NEWSROOM - Production Runner",
    "AI NEWSROOM - Dashboard API",
    "AI NEWSROOM - Newsroom Cycle",
    "AI NEWSROOM - Market Cycle"
)

$removed = @()
foreach ($name in $taskNames) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($null -ne $task) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        $removed += $name
    }
}

@{ removed = $removed } | ConvertTo-Json -Compress
