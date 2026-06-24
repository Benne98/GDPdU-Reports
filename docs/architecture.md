# Architecture — GDPdU-Reports (default profile)

> Agent-facing map for the **GDPdU-Reports** repo (lean GDPdU + Mathis FDD stack).
> For the full finssentials monorepo (3× Rasa, Cockpit, Sales), see
> `docs/profiles/finssentials.md`.

## System overview

```
Browser :5176 (vite --mode fdd-merge)
  ├─ /api  → FastAPI :8010 (backend/app/main.py)
  ├─ /rasa → FDD Rasa :5005 (NOT Expert :5006)
  └─ UI: Financials, Data Update, Mapping, Version History, FDD Bot (/fdd-bot)

FastAPI (backend/app/)
  ├─ etl/ GL ingestion, derive, versioning (snapshots, restore)
  ├─ compat routers (mirror legacy finssentials API for frontend/src/lib/api.ts)
  └─ fdd_bot → subprocess root scripts (*.py) → Excel output

FDD loop: Upload → Rasa cards (:5005) → POST /api/v1/fdd/run/* → scripts → result card
```

## Ports (fdd-merge dev)

| Service | Port | Start |
|---------|------|-------|
| Frontend | **5176** | `cd frontend && npm run dev:fdd-merge` |
| FastAPI | **8010** | `.\scripts\run-fdd-merge-api.ps1` |
| FDD Rasa REST | **5005** | `.\scripts\run-fdd-merge-rasa.ps1` |
| FDD Actions | **5055** | `.\scripts\run-fdd-merge-rasa-actions.ps1` |
| Postgres | 5432 | DB `Finssentials` (local) |

Other Vite modes: `dev` → 5174/8008, `dev:test` → 5175/8009.

## Modules

| Module | Path | Responsibility |
|--------|------|----------------|
| Frontend | `frontend/src/` | GDPdU dashboards, Financials two-view, Data Update, FDD bot (`components/fdd-bot/`), export libs |
| Backend API | `backend/app/` | Auth, ingest, statements, compat layer, FDD router |
| FDD router | `backend/app/routers/fdd_bot.py` | Uploads, `_run_script`, async job polling; `PROJECT_ROOT` = 4 levels up from router |
| ETL | `etl/` | GL prepare → load → derive → versioning; mapping; partner masters |
| Rasa FDD | `rasa/` only | Guided FDD conversation; actions call FastAPI |
| FDD scripts | repo root `*.py` | GST, PVM, TOP, SuSa, Databook, PDF extraction, … |
| Shared helpers | `funktionssammlung.py` | Periods, filters, `to_kEUR` — **lowercase imports** |

**Not in this repo:** Expert/Readiness bots, Marketing/Pitch, legacy ARR/Churn pipelines.

## GL data flow

1. Admin uploads GDPdU/GL file → `ingest` router → `etl/load.py`.
2. `derive.py` builds reporting tables; mapping via `mapping.py` / editor API.
3. `versioning.py` snapshots after commit (`commit_mode=replace` default on GL);
   restore from snapshot re-runs `derive.py`.
4. Frontend compat endpoints serve Financials/Sales views from derived data.

**GL commit modes:** `append` deduplicates rows; `replace` scopes delete + full reload.
Post-commit snapshots per `load_id` enable version history + restore API.

## FDD data flow

1. User uploads XLSX via FDD panel → `uploads/<session>/`.
2. Rasa collects config via adaptive cards (sheet, columns, periods, filters).
3. `rasa/actions/actions.py` POSTs JSON to `/api/v1/fdd/run/<script>`.
4. `fdd_bot._run_script` runs root script with `PYTHONPATH` = repo root + `backend/`.
5. Script writes Excel; API returns path; Rasa shows result card.

## Critical pitfalls (from production bugs)

| Issue | Fix / rule |
|-------|------------|
| Expert bot responses on FDD UI | `VITE_RASA_PROXY` / vite proxy → **5005**, not 5006 |
| `filters` TypeError | Must be `{enabled, rules}` — wrap list in Rasa actions |
| Wrong `PROJECT_ROOT` in fdd_bot | Four levels up from `backend/app/routers/` |
| `Funktionssammlung.py` | Breaks imports — file must be `funktionssammlung.py` |
| Rasa models missing | `rasa/models/` gitignored — junction from finssentials clone |
| GST formula_mode timeouts | Prefer async endpoints or non-formula for bot runs |
| Import check | `python -c "from app import main"` not `import main` |

## Service boundaries

- Frontend never queries Postgres directly.
- Rasa actions are thin — heavy logic in scripts or `etl/`.
- Financial formulas live in scripts + `funktionssammlung.py`, not scattered in routers.

## Tests

- `backend/tests/` — API, FDD jobs, compat payloads
- `etl/tests/` — ingest, derive, versioning roundtrips
- Gate: `frontend npm run build`, backend import, targeted pytest
