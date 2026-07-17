#!/usr/bin/env bash
# One-shot setup for colleagues: Python venvs, npm deps, Rasa train, PM2 stack start.
#
# Usage (from repo root):
#   npm run setup
#   ./scripts/setup-gdpdu-stack.sh
#   ./scripts/setup-gdpdu-stack.sh --force-train   # always retrain Rasa
#   ./scripts/setup-gdpdu-stack.sh --no-start      # setup + train only
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FORCE_TRAIN=0
NO_START=0

for arg in "$@"; do
  case "$arg" in
    --force-train|-f) FORCE_TRAIN=1 ;;
    --no-start) NO_START=1 ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 2
      ;;
  esac
done

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

pick_python() {
  if [[ -n "${PYTHON:-}" ]] && command -v "$PYTHON" >/dev/null 2>&1; then
    command -v "$PYTHON"
    return
  fi
  for cand in python3.10 python3.11 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
      command -v "$cand"
      return
    fi
  done
  die "Python 3.10+ not found. Install Python 3.10 and retry."
}

ensure_venv() {
  local dir="$1"
  local req="$2"
  local py="$3"
  local venv_py="$dir/.venv/bin/python"

  if [[ ! -x "$venv_py" ]]; then
    log "Creating venv in $dir/.venv"
    "$py" -m venv "$dir/.venv"
  fi
  log "Installing Python deps for $(basename "$dir")"
  "$venv_py" -m pip install --upgrade pip >/dev/null
  "$venv_py" -m pip install -r "$req"
}

model_needs_train() {
  local models_dir="$ROOT/rasa/models"
  local newest=""
  shopt -s nullglob
  local archives=("$models_dir"/*.tar.gz)
  shopt -u nullglob
  if ((${#archives[@]} == 0)); then
    return 0
  fi
  newest="$(ls -t "${archives[@]}" | head -1)"

  local sources=(
    "$ROOT/rasa/domain.yml"
    "$ROOT/rasa/config.yml"
    "$ROOT/rasa/data/nlu.yml"
    "$ROOT/rasa/data/rules.yml"
    "$ROOT/rasa/data/stories.yml"
  )
  local src
  for src in "${sources[@]}"; do
    [[ -f "$src" ]] || continue
    if [[ "$src" -nt "$newest" ]]; then
      return 0
    fi
  done
  return 1
}

need_cmd npm
need_cmd node
PY="$(pick_python)"
log "Using Python: $PY ($("$PY" --version 2>&1))"

# ── Root (pm2) ────────────────────────────────────────────────────────────────
log "Installing root npm dependencies (pm2)"
cd "$ROOT"
npm install --silent

# ── Frontend ──────────────────────────────────────────────────────────────────
if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
  log "Installing frontend npm dependencies"
  (cd "$ROOT/frontend" && npm install --silent)
else
  log "Frontend node_modules present — skipping npm install"
fi

# ── Backend venv ──────────────────────────────────────────────────────────────
ensure_venv "$ROOT/backend" "$ROOT/backend/requirements.txt" "$PY"

# ── Rasa venv ─────────────────────────────────────────────────────────────────
ensure_venv "$ROOT/rasa" "$ROOT/rasa/requirements.txt" "$PY"
RASA_PY="$ROOT/rasa/.venv/bin/python"

# ── Rasa train ────────────────────────────────────────────────────────────────
mkdir -p "$ROOT/rasa/models"
if ((FORCE_TRAIN == 1)) || model_needs_train; then
  log "Training Rasa model (this can take a few minutes)…"
  (
    cd "$ROOT/rasa"
    "$RASA_PY" -m rasa train --fixed-model-name gdpdu-bot
  )
  log "Rasa train finished → rasa/models/gdpdu-bot.tar.gz"
else
  newest="$(ls -t "$ROOT/rasa/models"/*.tar.gz | head -1)"
  log "Rasa model is up to date ($(basename "$newest")) — skip train (use --force-train to rebuild)"
fi

# ── Start stack ───────────────────────────────────────────────────────────────
if ((NO_START == 1)); then
  log "Setup complete (--no-start). Start later with: npm run stack:start"
  exit 0
fi

log "Starting GDPdU stack (API 8010, Rasa 5005/5055, UI 5176)"
cd "$ROOT"
# Restart if already registered, otherwise start fresh
if npx pm2 describe gdpdu-api >/dev/null 2>&1; then
  npm run stack:restart
else
  npm run stack:start
fi

sleep 2
npx pm2 status | grep -E 'gdpdu-|---|id' || true

cat <<EOF

Setup done.

  UI:     http://127.0.0.1:5176
  API:    http://127.0.0.1:8010/docs
  Rasa:   http://127.0.0.1:5005

Useful commands:
  npm run stack:status
  npm run stack:logs
  npm run stack:stop
  npm run train          # retrain Rasa only
EOF
