# FDD Rasa for GDPdU merge stack — REST :5005, actions :5055 (Mathis_18_06_26 ports).
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

Set-Location $rasaDir
& $python -m rasa run --enable-api --cors "*" --port 5005
