# Merged API — port 8014 against the cloned database "finssentials_merged".
# The unified stack: reporting-v2 + v4 project-setup + sandbox budget in ONE experience
# (frontend `vite --mode merged` on 5180). Parallel to all other stacks; live stays untouched.
#
# DB_PASSWORD is NOT hardcoded (secret). Set it before running, e.g.:
#   $env:DB_PASSWORD = "<your-postgres-password>"; .\scripts\run-merged-api.ps1
#
# Merged-stack flag choices (see docs plan): the merged stack BOTH ingests and reports, so
# REBUILD_ON_COMMIT=1 (post-commit rebuild feeds reporting) and ALLOW_DATA_RESET=1 (project-setup).
# OVERVIEW_SUMMARY_USE_MART=1 serves the fast Overview from the mart (refresh_mart_overview.py
# after data changes). BS_NET_PROFIT_SOURCE stays report_inject (the BS reader injects the
# virtual net-profit row and never reads gl_rows -> flipping would double-count equity).
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

# A stale or duplicate uvicorn on 8014 serves old code; kill listeners + orphan workers first.
$portPids = @(Get-NetTCPConnection -LocalPort 8014 -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique | Where-Object { $_ })
foreach ($procId in $portPids) {
  Write-Host "Stopping existing listener on port 8014 (PID $procId)..."
  Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match '--port 8014' } |
  ForEach-Object {
    Write-Host "Stopping uvicorn on 8014 (PID $($_.ProcessId))..."
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
$env:DB_NAME = "finssentials_merged"
if (-not $env:BS_NET_PROFIT_SOURCE)      { $env:BS_NET_PROFIT_SOURCE = "report_inject" }
if (-not $env:OPENING_BALANCE_MODE)      { $env:OPENING_BALANCE_MODE = "in_data" }
if (-not $env:REBUILD_ON_COMMIT)         { $env:REBUILD_ON_COMMIT = "1" }
if (-not $env:OVERVIEW_SUMMARY_USE_MART) { $env:OVERVIEW_SUMMARY_USE_MART = "1" }
if (-not $env:ALLOW_DATA_RESET)          { $env:ALLOW_DATA_RESET = "1" }

Set-Location $backend
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8014 --reload --reload-dir app --reload-dir ../etl
