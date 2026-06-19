#!/usr/bin/env bash
# GDPdU + Mathis FDD merge API — port 8010
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${FINSSENTIALS_PYTHON:-$ROOT/backend/.venv/bin/python}"
export PYTHONPATH="$ROOT/backend"
cd "$ROOT/backend"
exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload --env-file .env
