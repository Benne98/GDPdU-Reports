# v5 API — port 8015 against the cloned database "finssentials_v5".
# The fundamentally-reworked, dataset-agnostic Project-Setup -> Reporting pipeline
# (frontend `vite --mode v5` on 5181). Parallel to all other stacks; the merged 5180 stack
# stays UNTOUCHED as the numeric parity baseline.
#
# DB_PASSWORD is NOT hardcoded (secret). Set it before running, e.g.:
#   $env:DB_PASSWORD = "<your-postgres-password>"; .\scripts\run-v5-api.ps1
#
# Flag choices mirror the merged stack (v5 is a merged superset): the stack BOTH ingests and reports,
# so REBUILD_ON_COMMIT=1 and ALLOW_DATA_RESET=1 (project-setup). OVERVIEW_SUMMARY_USE_MART=1 serves
# the fast Overview from the mart. BS_NET_PROFIT_SOURCE stays report_inject (avoids equity double-count).
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

# A stale or duplicate uvicorn on 8015 serves old code; kill listeners + orphan workers first.
$portPids = @(Get-NetTCPConnection -LocalPort 8015 -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique | Where-Object { $_ })
foreach ($procId in $portPids) {
  Write-Host "Stopping existing listener on port 8015 (PID $procId)..."
  Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match '--port 8015' } |
  ForEach-Object {
    Write-Host "Stopping uvicorn on 8015 (PID $($_.ProcessId))..."
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
$env:DB_NAME = "finssentials_v5"
if (-not $env:BS_NET_PROFIT_SOURCE)      { $env:BS_NET_PROFIT_SOURCE = "report_inject" }
if (-not $env:OPENING_BALANCE_MODE)      { $env:OPENING_BALANCE_MODE = "in_data" }
if (-not $env:REBUILD_ON_COMMIT)         { $env:REBUILD_ON_COMMIT = "1" }
if (-not $env:OVERVIEW_SUMMARY_USE_MART) { $env:OVERVIEW_SUMMARY_USE_MART = "1" }
if (-not $env:ALLOW_DATA_RESET)          { $env:ALLOW_DATA_RESET = "1" }

Set-Location $backend
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8015 --reload --reload-dir app --reload-dir ../etl
