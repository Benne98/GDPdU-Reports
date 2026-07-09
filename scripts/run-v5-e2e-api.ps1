# v5 E2E API — port 8016 against the cloned database "finssentials_v5_e2e".
# Round-trip / dataset-agnostic proof stack: drives the REAL ingest endpoints on a
# fresh clone (template-cloned from finssentials_v5, facts truncated) so a full
# Project-Setup -> Reporting run can be exercised end-to-end. Frontend `vite --mode
# v5-e2e` on 5182. Parallel to the v5 stack (8015) which stays the clean baseline.
#
# DB_PASSWORD is NOT hardcoded (secret). Set it before running, e.g.:
#   $env:DB_PASSWORD = "<your-postgres-password>"; .\scripts\run-v5-e2e-api.ps1
#
# Flags mirror run-v5-api.ps1 (v5 superset) — the stack BOTH ingests and reports, so
# REBUILD_ON_COMMIT=1 and ALLOW_DATA_RESET=1; OVERVIEW_SUMMARY_USE_MART=1 serves the
# fast Overview from the mart; BS_NET_PROFIT_SOURCE stays report_inject.
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

# A stale or duplicate uvicorn on 8016 serves old code; kill listeners + orphan workers first.
$portPids = @(Get-NetTCPConnection -LocalPort 8016 -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique | Where-Object { $_ })
foreach ($procId in $portPids) {
  Write-Host "Stopping existing listener on port 8016 (PID $procId)..."
  Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match '--port 8016' } |
  ForEach-Object {
    Write-Host "Stopping uvicorn on 8016 (PID $($_.ProcessId))..."
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
if ($portPids.Count -gt 0) { Start-Sleep -Seconds 1 }

$env:PYTHONPATH = $backend
$env:DB_NAME = "finssentials_v5_e2e"
if (-not $env:BS_NET_PROFIT_SOURCE)      { $env:BS_NET_PROFIT_SOURCE = "report_inject" }
# v5 reworked pipeline: BS current-year result = Σ P&L (income-statement bottom line),
# NOT the Assets−(Equity+Liab) balancing plug, so a real imbalance stays visible.
if (-not $env:BS_CURRENT_YEAR_RESULT_MODE) { $env:BS_CURRENT_YEAR_RESULT_MODE = "pl_sum" }
if (-not $env:OPENING_BALANCE_MODE)      { $env:OPENING_BALANCE_MODE = "in_data" }
if (-not $env:REBUILD_ON_COMMIT)         { $env:REBUILD_ON_COMMIT = "1" }
if (-not $env:OVERVIEW_SUMMARY_USE_MART) { $env:OVERVIEW_SUMMARY_USE_MART = "1" }
if (-not $env:ALLOW_DATA_RESET)          { $env:ALLOW_DATA_RESET = "1" }

Set-Location $backend
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8016 --reload --reload-dir app --reload-dir ../etl
