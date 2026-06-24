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
  throw "DB_PASSWORD not set. Run:  `$env:DB_PASSWORD = '<postgres-password>'  before this script."
}

$env:PYTHONPATH = $backend
$env:DB_NAME = "finssentials_v2"
if (-not $env:BS_NET_PROFIT_SOURCE) { $env:BS_NET_PROFIT_SOURCE = "report_inject" }  # legacy until gate passes
if (-not $env:OPENING_BALANCE_MODE) { $env:OPENING_BALANCE_MODE = "in_data" }
if (-not $env:REBUILD_ON_COMMIT)    { $env:REBUILD_ON_COMMIT = "0" }

Set-Location $backend
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8011 --reload
