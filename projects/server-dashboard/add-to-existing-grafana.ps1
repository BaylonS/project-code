param(
  [string]$GrafanaUrl = "http://127.0.0.1:3000",
  [string]$DashboardUid = "homelab-hardware",
  [string]$OutputJson = "homelab-hardware.codex-patched.json",
  [string]$Token = $env:GRAFANA_TOKEN,
  [string]$User = $env:GRAFANA_USER,
  [string]$Password = $env:GRAFANA_PASSWORD
)

$ErrorActionPreference = "Stop"

function Invoke-Grafana {
  param(
    [string]$Method,
    [string]$Path,
    [object]$Body = $null
  )

  $headers = @{}
  $params = @{
    Method = $Method
    Uri = "$GrafanaUrl$Path"
    Headers = $headers
    ContentType = "application/json"
    ErrorAction = "Stop"
  }

  if ($Token) {
    $headers.Authorization = "Bearer $Token"
  } elseif ($User -and $Password) {
    $pair = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("${User}:${Password}"))
    $headers.Authorization = "Basic $pair"
  } else {
    throw "Set GRAFANA_TOKEN, or set GRAFANA_USER and GRAFANA_PASSWORD."
  }

  if ($null -ne $Body) {
    $params.Body = ($Body | ConvertTo-Json -Depth 100)
  }

  Invoke-RestMethod @params
}

function New-Target {
  param([string]$Expr, [string]$Legend = "")
  [ordered]@{
    datasource = $script:Datasource
    editorMode = "code"
    expr = $Expr
    legendFormat = $Legend
    range = $true
    refId = [string][char](65 + $script:TargetIndex)
  }
  $script:TargetIndex += 1
}

function New-Panel {
  param(
    [string]$Title,
    [string]$Type,
    [int]$X,
    [int]$Y,
    [int]$W,
    [int]$H,
    [string]$Expr,
    [string]$Legend = "",
    [string]$Unit = "none",
    [array]$Mappings = @(),
    [object]$Thresholds = $null
  )

  $script:TargetIndex = 0
  $fieldConfig = [ordered]@{
    defaults = [ordered]@{
      color = [ordered]@{ mode = "thresholds" }
      mappings = $Mappings
      thresholds = if ($Thresholds) { $Thresholds } else { [ordered]@{ mode = "absolute"; steps = @(@{ color = "green"; value = $null }) } }
      unit = $Unit
    }
    overrides = @()
  }

  [ordered]@{
    datasource = $script:Datasource
    fieldConfig = $fieldConfig
    gridPos = @{ h = $H; w = $W; x = $X; y = $Y }
    id = $script:NextId
    options = [ordered]@{
      colorMode = "value"
      graphMode = "area"
      justifyMode = "auto"
      orientation = "auto"
      reduceOptions = @{ calcs = @("lastNotNull"); fields = ""; values = $false }
      showPercentChange = $false
      textMode = "auto"
      wideLayout = $true
    }
    pluginVersion = "13.0.1"
    targets = @(New-Target -Expr $Expr -Legend $Legend)
    title = $Title
    type = $Type
  }
  $script:NextId += 1
}

$datasources = Invoke-Grafana -Method GET -Path "/api/datasources"
$prometheus = $datasources | Where-Object { $_.type -eq "prometheus" } | Select-Object -First 1
if (-not $prometheus) {
  throw "No Prometheus datasource found in Grafana."
}

$script:Datasource = @{ type = "prometheus"; uid = $prometheus.uid }
$response = Invoke-Grafana -Method GET -Path "/api/dashboards/uid/$DashboardUid"
$dashboard = $response.dashboard
$meta = $response.meta

if ($dashboard.panels | Where-Object { $_.title -eq "Windows PC + Star Citizen" -or $_.title -eq "Star Citizen Status" }) {
  throw "Codex panels already appear to exist on this dashboard. Remove the old row first if you want to reapply."
}

$maxY = 0
$maxId = 0
foreach ($panel in $dashboard.panels) {
  if ($panel.gridPos) {
    $bottom = [int]$panel.gridPos.y + [int]$panel.gridPos.h
    if ($bottom -gt $maxY) { $maxY = $bottom }
  }
  if ([int]$panel.id -gt $maxId) { $maxId = [int]$panel.id }
}

$script:NextId = $maxId + 1
$row = [ordered]@{
  collapsed = $false
  gridPos = @{ h = 1; w = 24; x = 0; y = $maxY }
  id = $script:NextId
  panels = @()
  title = "Windows PC + Star Citizen"
  type = "row"
}
$script:NextId += 1

$statusMappings = @(
  @{ options = @{ "0" = @{ color = "green"; index = 0; text = "Operational" }; "1" = @{ color = "yellow"; index = 1; text = "Maintenance" }; "2" = @{ color = "red"; index = 2; text = "Issue" } }; type = "value" }
)
$upMappings = @(
  @{ options = @{ "0" = @{ color = "red"; index = 0; text = "Down" }; "1" = @{ color = "green"; index = 1; text = "Up" } }; type = "value" }
)
$sshMappings = @(
  @{ options = @{ "0" = @{ color = "yellow"; index = 0; text = "Needs SSH" }; "1" = @{ color = "green"; index = 1; text = "Online" } }; type = "value" }
)
$percentThresholds = @{ mode = "absolute"; steps = @(@{ color = "green"; value = $null }, @{ color = "yellow"; value = 70 }, @{ color = "red"; value = 90 }) }
$tempThresholds = @{ mode = "absolute"; steps = @(@{ color = "green"; value = $null }, @{ color = "yellow"; value = 65 }, @{ color = "red"; value = 80 }) }

$newPanels = @(
  $row,
  (New-Panel -Title "Star Citizen Status" -Type "stat" -X 0 -Y ($maxY + 1) -W 6 -H 4 -Expr "star_citizen_operational * 0 + star_citizen_maintenance * 1 + star_citizen_issue * 2" -Mappings $statusMappings),
  (New-Panel -Title "This PC CPU" -Type "gauge" -X 6 -Y ($maxY + 1) -W 6 -H 4 -Expr "dashboard_local_cpu_percent" -Unit "percent" -Thresholds $percentThresholds),
  (New-Panel -Title "This PC Memory" -Type "gauge" -X 12 -Y ($maxY + 1) -W 6 -H 4 -Expr "dashboard_local_memory_percent" -Unit "percent" -Thresholds $percentThresholds),
  (New-Panel -Title "Pi SSH Probe" -Type "stat" -X 18 -Y ($maxY + 1) -W 6 -H 4 -Expr "dashboard_pi_up" -Mappings $sshMappings),
  (New-Panel -Title "This PC Disk Used" -Type "bargauge" -X 0 -Y ($maxY + 5) -W 8 -H 5 -Expr "100 * (1 - dashboard_local_disk_free_bytes / dashboard_local_disk_size_bytes)" -Legend "{{drive}}" -Unit "percent" -Thresholds $percentThresholds),
  (New-Panel -Title "Pi Temperature" -Type "gauge" -X 8 -Y ($maxY + 5) -W 8 -H 5 -Expr "dashboard_pi_cpu_temperature_celsius" -Unit "celsius" -Thresholds $tempThresholds),
  (New-Panel -Title "Homelab Grafana Reachable" -Type "stat" -X 16 -Y ($maxY + 5) -W 8 -H 5 -Expr "grafana_homelab_up" -Mappings $upMappings)
)

$dashboard.panels = @($dashboard.panels) + $newPanels
$dashboard.version = $null

if ($meta.provisioned) {
  $dashboard | ConvertTo-Json -Depth 100 | Set-Content -Path $OutputJson -Encoding UTF8
  Write-Host "Dashboard is provisioned. Wrote patched dashboard JSON to: $OutputJson"
  Write-Host "Replace provisionedExternalId '$($meta.provisionedExternalId)' on the Grafana server with this JSON, then restart/reload Grafana provisioning."
  exit 0
}

$payload = @{
  dashboard = $dashboard
  folderUid = $meta.folderUid
  message = "Add Windows PC, Pi probe, and Star Citizen panels"
  overwrite = $true
}

try {
  Invoke-Grafana -Method POST -Path "/api/dashboards/db" -Body $payload | Out-Null
  Write-Host "Updated dashboard: $GrafanaUrl/d/$DashboardUid"
} catch {
  if ($_.Exception.Message -match "Cannot save provisioned dashboard") {
    $dashboard | ConvertTo-Json -Depth 100 | Set-Content -Path $OutputJson -Encoding UTF8
    Write-Host "Dashboard is provisioned. Wrote patched dashboard JSON to: $OutputJson"
    Write-Host "Replace provisionedExternalId '$($meta.provisionedExternalId)' on the Grafana server with this JSON, then restart/reload Grafana provisioning."
  } else {
    throw
  }
}
