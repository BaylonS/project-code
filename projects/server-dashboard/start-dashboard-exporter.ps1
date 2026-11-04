$ErrorActionPreference = "Stop"

$dashboardDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$port = 4177

$listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
  exit 0
}

$node = (Get-Command node -ErrorAction Stop).Source
$out = Join-Path $dashboardDir "dashboard.out.log"
$err = Join-Path $dashboardDir "dashboard.err.log"

Start-Process `
  -FilePath $node `
  -ArgumentList "server.js" `
  -WorkingDirectory $dashboardDir `
  -RedirectStandardOutput $out `
  -RedirectStandardError $err `
  -WindowStyle Hidden
