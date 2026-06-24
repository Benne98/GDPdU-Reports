# FDD Rasa action server for GDPdU merge stack — port 5065 (re-ported off the SELFMADE stack on 5055).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$rasaDir = Join-Path $root "rasa"

$pythonCandidates = @(
  (Join-Path $rasaDir ".venv\Scripts\python.exe"),
  "C:\Users\bened\OneDrive\Desktop\Project SELFMADE\Cursor\rasa\.venv\Scripts\python.exe",
  "C:\Users\bened\OneDrive\Finssentials\finssentials\rasa\.venv\Scripts\python.exe"
)
$python = $pythonCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $python) {
  throw "No Rasa Python found. Create rasa/.venv or install Rasa in one of the candidate paths."
}

# Merge stack: action server must call the GDPdU+Mathis FastAPI on :8010, NOT the default :8000
# (rasa/actions/config.py defaults FASTAPI_BASE_URL to http://localhost:8000 = the other/old backend).
if (-not $env:FASTAPI_BASE_URL) { $env:FASTAPI_BASE_URL = "http://127.0.0.1:8010" }

Set-Location $rasaDir
& $python -m rasa run actions --port 5065
