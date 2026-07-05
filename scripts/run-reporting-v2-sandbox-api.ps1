# Reporting-v2 sandbox API — port 8013 against finssentials_v2_sandbox.
# Independent copy of the 5177/8011 stack for parallel experiments.
#
#   .\scripts\clone-db-v2-sandbox.ps1   # once (or to refresh from v2)
#   .\scripts\run-reporting-v2-sandbox-api.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$ApiPort = 8013

if (-not $env:DB_PASSWORD) {
  $dotenvLoader = Join-Path $backend ".venv\Scripts\python.exe"
  $envCandidates = @(
    (Join-Path $backend ".env"),
    (Join-Path (Split-Path -Parent $root) "Finssentials_GDPDU\backend\.env")
  )
  foreach ($envFile in $envCandidates) {
    if (-not (Test-Path $envFile)) { continue }
    $env:DB_PASSWORD = & $dotenvLoader -c "from pathlib import Path; from dotenv import dotenv_values; print(dotenv_values(Path(r'$envFile')).get('DB_PASSWORD','') or '')"
    if ($env:DB_PASSWORD) { break }
  }
}
if (-not $env:DB_PASSWORD) {
  throw "DB_PASSWORD not set. Run:  `$env:DB_PASSWORD = '<postgres-password>'  before this script."
}

$portPids = @(Get-NetTCPConnection -LocalPort $ApiPort -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique | Where-Object { $_ })
foreach ($procId in $portPids) {
  Write-Host "Stopping existing listener on port $ApiPort (PID $procId)..."
  Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match "--port $ApiPort" } |
  ForEach-Object {
    Write-Host "Stopping uvicorn on $ApiPort (PID $($_.ProcessId))..."
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
if ($portPids.Count -gt 0) { Start-Sleep -Seconds 1 }

$env:PYTHONPATH = $backend
$env:DB_NAME = "finssentials_v2_sandbox"
if (-not $env:BS_NET_PROFIT_SOURCE) { $env:BS_NET_PROFIT_SOURCE = "report_inject" }
if (-not $env:OPENING_BALANCE_MODE) { $env:OPENING_BALANCE_MODE = "in_data" }
if (-not $env:REBUILD_ON_COMMIT)    { $env:REBUILD_ON_COMMIT = "0" }

Set-Location $backend
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port $ApiPort --reload --reload-dir app --reload-dir ../etl
