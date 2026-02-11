# test_requests.ps1 (PS 5.1 compatible)
# Run:
#   powershell -ExecutionPolicy Bypass -File .\test_requests.ps1

$ErrorActionPreference = "Stop"

$baseUrl = if ($env:AGENT_BASE_URL) { $env:AGENT_BASE_URL } else { "http://localhost:5050" }
$apiKey  = if ($env:AGENT_API_KEY)  { $env:AGENT_API_KEY }  else { "dev-secret" }

$headers = @{
  "Content-Type"    = "application/json"
  "x-agent-api-key" = $apiKey
}

$users = @(
  @{ name="User1"; id="a7fd367c-beb6-424e-9403-dd8a12c8c13c" },
  @{ name="User2"; id="62ce4732-69cc-4d16-9b38-8d5960076c35" },
  @{ name="User3"; id="ec0ffd17-a295-453b-8434-d176aa1c2c4a" }
)

$months = @("2026-01", "2026-02", "2026-03")
$weekStarts = @("2026-01-06", "2026-02-10", "2026-03-10")

function PostJson($url, $obj) {
  $json = ($obj | ConvertTo-Json -Depth 10)
  return Invoke-RestMethod -Method Post -Uri $url -Headers $headers -Body $json
}

function SafeJoin($arr) {
  if ($null -eq $arr) { return "" }
  try { return ($arr -join ", ") } catch { return "" }
}

function Get-TopCategoriesFromMap($map, $n=5) {
  if ($null -eq $map) { return "" }

  $pairs = @()
  foreach ($p in $map.PSObject.Properties) {
    $v = 0.0
    try { $v = [double]$p.Value } catch { $v = 0.0 }
    $pairs += [PSCustomObject]@{ Key = $p.Name; Value = $v }
  }

  return ($pairs | Sort-Object Value -Descending | Select-Object -First $n |
    ForEach-Object { "$($_.Key)=$([math]::Round($_.Value, 2))" }) -join " | "
}

Write-Host "== Health check ==" -ForegroundColor Cyan
$health = Invoke-RestMethod -Method Get -Uri "$baseUrl/health"
$health | ConvertTo-Json -Depth 5

Write-Host "`n== Test 1: Monthly deterministic (no Gemini) ==" -ForegroundColor Cyan
foreach ($u in $users) {
  foreach ($m in $months) {
    Write-Host "`n[$($u.name)] month=$m" -ForegroundColor Yellow

    $res = PostJson "$baseUrl/agent/monthly" @{
      user_id    = $u.id
      month      = $m
      include_ai = $false
    }

    $ins = $res.insights
    if (-not $ins) { throw "No insights returned for user=$($u.id) month=$m" }

    $budget = 0.0
    $spent = 0.0
    try { $budget = [double]$ins.budget_amount } catch { $budget = 0.0 }
    try { $spent  = [double]$ins.spent_total } catch { $spent = 0.0 }

    $warnings = SafeJoin $ins.warnings
    $homeCity = $ins.home_city

    Write-Host ("home_city={0}  budget={1}  spent={2}  warnings=[{3}]" -f $homeCity, $budget, $spent, $warnings)

    if ($ins.totals_by_category) {
      Write-Host ("top_categories: " + (Get-TopCategoriesFromMap $ins.totals_by_category 5))
    }
  }
}

Write-Host "`n== Test 2: Monthly with Gemini summary ==" -ForegroundColor Cyan
foreach ($u in $users) {
  foreach ($m in $months) {
    Write-Host "`n[$($u.name)] month=$m" -ForegroundColor Yellow

    $res = PostJson "$baseUrl/agent/monthly" @{
      user_id    = $u.id
      month      = $m
      include_ai = $true
    }

    $ins = $res.insights
    $ai  = $res.ai

    $spent = 0.0
    try { $spent  = [double]$ins.spent_total } catch { $spent = 0.0 }

    $warnings = SafeJoin $ins.warnings
    Write-Host ("spent={0}  warnings=[{1}]" -f $spent, $warnings)

    if ($null -eq $ai) {
      Write-Host "ai=null (Gemini call failed, rate-limited, or missing key)" -ForegroundColor DarkYellow
    } else {
      Write-Host ("AI headline: {0}" -f $ai.headline) -ForegroundColor Green
      Write-Host ("AI risk_level: {0}" -f $ai.risk_level) -ForegroundColor Green
      if ($ai.bullets) {
        Write-Host "AI bullets:"
        $ai.bullets | ForEach-Object { Write-Host (" - " + $_) }
      }
      if ($ai.actions) {
        Write-Host "AI actions:"
        $ai.actions | ForEach-Object { Write-Host (" - " + $_) }
      }
    }
  }
}

Write-Host "`n== Test 3: Weekly with Gemini summary ==" -ForegroundColor Cyan
foreach ($u in $users) {
  foreach ($ws in $weekStarts) {
    Write-Host "`n[$($u.name)] week_start=$ws" -ForegroundColor Yellow

    $res = PostJson "$baseUrl/agent/weekly" @{
      user_id    = $u.id
      week_start = $ws
      include_ai = $true
    }

    $ins = $res.insights
    $ai  = $res.ai

    $spent = 0.0
    try { $spent  = [double]$ins.spent_total } catch { $spent = 0.0 }

    $warnings = SafeJoin $ins.warnings
    Write-Host ("spent={0}  warnings=[{1}]" -f $spent, $warnings)

    if ($null -eq $ai) {
      Write-Host "ai=null (Gemini call failed, rate-limited, or missing key)" -ForegroundColor DarkYellow
    } else {
      Write-Host ("AI headline: {0}" -f $ai.headline) -ForegroundColor Green
      Write-Host ("AI risk_level: {0}" -f $ai.risk_level) -ForegroundColor Green
    }
  }
}

Write-Host "`n== Test 4: Auto-run endpoint (/agent/on-entry-created) deterministic ==" -ForegroundColor Cyan
foreach ($u in $users) {
  $m = $months[1]
  Write-Host "`n[$($u.name)] month=$m" -ForegroundColor Yellow

  $res = PostJson "$baseUrl/agent/on-entry-created" @{
    user_id    = $u.id
    month      = $m
    include_ai = $false
  }

  $ins = $res.insights
  Write-Host ("warnings=[{0}]" -f (SafeJoin $ins.warnings))
}

Write-Host "`nAll tests completed." -ForegroundColor Cyan
