# Profile — finssentials monorepo (secondary)

> Use when working in `Benne98/finssentials` or paths without `backend/app/`.
> GDPdU-Reports is the default profile — see `docs/architecture.md`.

## System overview

```
Browser :5173 (Vite)
  ├─ /api → FastAPI :8000 (backend/main.py)
  └─ Cockpit, Financials, Sales, Exit Readiness, FDD panel, Marketing (branch-dependent)

FastAPI (backend/)
  ├─ routers/, services/
  └─ fdd_bot at backend/routers/fdd_bot.py (3 levels up to repo root)

3× Rasa:
  FDD      rasa/           :5005 / actions :5055
  Expert   rasa-expert/    :5006 / actions :5056
  Readiness rasa-readiness/ :5007 / actions :5057
```

## Start commands

```bash
npm run stack:start      # PM2: API + Vite + all 3 Rasa
npm run dev:always       # API + Vite only
cd backend && ./start.sh # uvicorn :8000
cd frontend && npm run dev # :5173
```

## Modules beyond GDPdU

| Module | Path |
|--------|------|
| Analytics layer | `analytics-layer/` — canonical ERP-neutral DDL |
| Expert bot | `rasa-expert/` |
| Readiness bot | `rasa-readiness/` |
| Marketing/Pitch | `frontend/marketing/` (some branches) |
| PM2 stack | `ecosystem.config.cjs` |

## Quality gate differences

```bash
cd backend && python -c "import main"    # NOT from app import main
cd rasa && rasa data validate
cd rasa-expert && rasa data validate
cd rasa-readiness && rasa data validate
```

## Detection heuristic

Orchestrator / `session-start` hook: if `backend/app/main.py` exists → GDPdU profile;
if `backend/main.py` exists without `backend/app/` → this profile.
