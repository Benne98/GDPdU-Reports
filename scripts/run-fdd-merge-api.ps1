# GDPdU + Mathis FDD merge API — port 8010 (parallel to 8008/8009).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$env:PYTHONPATH = $backend
Set-Location $backend
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload --reload-dir app --reload-dir ../etl --env-file .env
