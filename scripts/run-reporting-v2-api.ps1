# Reporting-v2 API — port 8011 against the cloned database "Finssentials_v2".
# Parallel to the live GDPdU stack (8010 / Finssentials). The live stack stays untouched.
#
# DB_PASSWORD is NOT hardcoded (secret). Set it before running, e.g.:
#   $env:DB_PASSWORD = "<your-postgres-password>"; .\scripts\run-reporting-v2-api.ps1
# Behavior flags default to LEGACY so Phase-1 golden-equivalence can be proven first;
# flip them (e.g. BS_NET_PROFIT_SOURCE=gl_rows) only after the equivalence gate passes.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"

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

# A stale or duplicate uvicorn on 8011 (e.g. system Python alongside .venv) serves old
# WC builders — wrong TWC row order and missing account names in consolidation.
$portPids = @(Get-NetTCPConnection -LocalPort 8011 -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique | Where-Object { $_ })
foreach ($procId in $portPids) {
  Write-Host "Stopping existing listener on port 8011 (PID $procId)..."
  Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match '--port 8011' } |
  ForEach-Object {
    Write-Host "Stopping uvicorn on 8011 (PID $($_.ProcessId))..."
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
# Orphan uvicorn workers survive when the reloader parent dies (--reload on Windows).
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match 'GDPdU-Reports\\backend\\.venv.*spawn_main' } |
  ForEach-Object {
    Write-Host "Stopping orphan API worker (PID $($_.ProcessId))..."
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
if ($portPids.Count -gt 0) { Start-Sleep -Seconds 1 }

$env:PYTHONPATH = $backend
$env:DB_NAME = "finssentials_v2"
if (-not $env:BS_NET_PROFIT_SOURCE) { $env:BS_NET_PROFIT_SOURCE = "report_inject" }  # legacy until gate passes
if (-not $env:OPENING_BALANCE_MODE) { $env:OPENING_BALANCE_MODE = "in_data" }
if (-not $env:REBUILD_ON_COMMIT)    { $env:REBUILD_ON_COMMIT = "0" }

Set-Location $backend
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8011 --reload --reload-dir app --reload-dir ../etl
