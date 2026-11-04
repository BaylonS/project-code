$ErrorActionPreference = "Stop"

$created = $false
$mutex = New-Object System.Threading.Mutex($true, "CodexHomelabDashboardExporterMonitor", [ref]$created)
if (-not $created) {
  exit 0
}

try {
  $dashboardDir = Split-Path -Parent $MyInvocation.MyCommand.Path
  $starter = Join-Path $dashboardDir "start-dashboard-exporter.ps1"

  while ($true) {
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File $starter
    Start-Sleep -Seconds 60
  }
} finally {
  $mutex.ReleaseMutex()
}
