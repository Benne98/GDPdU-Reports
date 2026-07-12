#!/usr/bin/env bash
# FDD Rasa actions — port 5055 (GDPdU merge stack)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${FINSSENTIALS_RASA_PYTHON:-$ROOT/rasa/.venv/bin/python}"
cd "$ROOT/rasa"
export FASTAPI_BASE_URL="${FASTAPI_BASE_URL:-http://127.0.0.1:8010}"
export UPLOAD_BASE_DIR="${UPLOAD_BASE_DIR:-/Users/mathi/finssentials-wt-mathis/uploads}"
exec "$PY" -m rasa run actions --port 5055
