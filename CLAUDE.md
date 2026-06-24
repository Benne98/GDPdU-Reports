# AGENTS.md — GDPdU Reports

> Project memory for Cursor (auto-loaded). Keep **short**. Details in `docs/` and skills.

## What this is

**GDPdU-Reports** — lean GDPdU reporting + **Mathis FDD bot** on GL data.
React/Vite frontend, FastAPI (`backend/app/`), GL ETL (`etl/`), single FDD Rasa bot.

## Dev stack (fdd-merge, port 5176)

| Service | Port | Command |
|---------|------|---------|
| Frontend | **5176** | `cd frontend && npm run dev:fdd-merge` |
| API | **8010** | `.\scripts\run-fdd-merge-api.ps1` |
| FDD Rasa | **5005** | `.\scripts\run-fdd-merge-rasa.ps1` |
| Actions | **5055** | `.\scripts\run-fdd-merge-rasa-actions.ps1` |

Open http://localhost:5176 → Login → `/fdd-bot`.

## Orchestrator workflow

Multi-step tasks: use **`orchestrator`** agent — classify tier → delegate → verify.

| Tier | When | Loop |
|------|------|------|
| L0 | Typo, CSS, docs | 1 engineer → gate |
| L1 | Router, component, ETL (no new KPI) | engineer → gate |
| L2 | KPI, GL mapping, schema, signs | plan → architect → financial + engineer → test → gate → security |

Skills: `/run-quality-gate`, `/verify-done`, `/plan-feature`, `/financial-metric-test`.
Subagents: `.cursor/agents/` (10). Architecture: `docs/architecture.md`.

## Quality gate

```bash
cd frontend && npm run build
cd backend && python -c "from app import main"
cd backend && python -m pytest -q backend/tests/test_<area>.py
cd etl && python -m pytest -q etl/tests/test_<area>.py   # if ETL touched
cd rasa && rasa data validate   # if rasa/ touched
```

## Hard rules

1. **No financial logic without proof** — formula, example, edge cases, test (`docs/financial-logic.md`).
2. **No secrets / real customer data** — hooks enforce; synthetic fixtures only.
3. **DB read-only by default** — migrations need plan + rollback.
4. **Small diffs** — reuse `funktionssammlung.py` (lowercase module name).
5. **Rasa proxy 5005** — never 5006 (Expert bot).

## Protected paths

`.env*`, secrets, `docker-compose*.yml`, `uploads/**`, `*.xlsx`/`*.csv` data files.

## Secondary profile

Full finssentials monorepo (3× Rasa, :5173/:8000): `docs/profiles/finssentials.md`.
