# Golden-snapshot harness — reporting-v2 Phase 1 equivalence gate

`backend/scripts/golden_snapshot.py` captures the reporting outputs the frontend
consumes and diffs two captures numerically. It is the **gate** that proves the
Phase 1 refactor (`etl/rebuild.py` folding in `derive_facts.py`) is
behaviour-preserving before any financial behaviour changes (Phases 2/3).

It is **HTTP-only** — it never writes the database, so it is safe against the live
stack.

## Prerequisites

- Live stack on `:8010` (DB `Finssentials`) — the source of truth.
- v2 stack on `:8011` (DB `finssentials_v2`) — started via
  `scripts/run-reporting-v2-api.ps1` with `$env:DB_PASSWORD` set and **LEGACY**
  flags (`BS_NET_PROFIT_SOURCE=report_inject`, `REBUILD_ON_COMMIT=0`).
- Run python from the backend venv: `backend/.venv/Scripts/python.exe`.

Credentials default to the dev seed admin; override with `--email/--password` or
`GOLDEN_EMAIL`/`GOLDEN_PASSWORD`.

## Run the gate

```bash
cd backend

# 1. Capture the live baseline (8010) -> backend/golden/live/
./.venv/Scripts/python.exe scripts/golden_snapshot.py capture --base-url http://127.0.0.1:8010 --name live

# 2. Re-derive the v2 DB (idempotent), then capture (8011) -> backend/golden/v2/
#    (run rebuild_project on finssentials_v2 first — see below)
./.venv/Scripts/python.exe scripts/golden_snapshot.py capture --base-url http://127.0.0.1:8011 --name v2

# 3. Compare — exit 0 == EQUIVALENT, exit 1 == material diff
./.venv/Scripts/python.exe scripts/golden_snapshot.py compare live v2
```

### Re-derive the v2 DB before capturing

```bash
cd backend
DB_PASSWORD='...' DB_NAME='finssentials_v2' \
  ./.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'..'); \
  from sqlalchemy.orm import Session; from app.db import engine; \
  from etl.rebuild import rebuild_project; \
  import json; \
  (lambda s: print(json.dumps(rebuild_project(s, scope=None, mode='full'), indent=2)))(Session(engine))"
```

## What is captured (164 payloads, anchor = latest period)

- **Financials** (`pl-statement`, `balance-sheet`, `working-capital`, `cash-flow`):
  statement (grains `month`/`year`/`week`, consolidated + each entity),
  `consolidation`, `monthly`, and `weekly` breakdowns (PL/CF).
- **Sales** (deterministic subset): `top-entities` (customer+supplier),
  `headline-kpis`, `receivables-aging`, `payables-aging`, `geography/countries`,
  `analytics/composition-breakdown` — consolidated + per entity.

Narrative/LLM endpoints are **excluded** (non-deterministic; not part of the
derive path under test).

## How `compare` avoids false positives

The compat layer mints **synthetic per-request row ids** (e.g.
`pl-NET_SALES-l4-89477`) whose numeric suffix is a per-process hash, and some
statement/account child arrays come back in a **SQL order that is not fully
tie-broken** (two independent server processes can permute identical rows).
`compare` therefore:

- ignores the volatile `id` key, and
- matches sibling list elements **order-insensitively** on a stable business key
  (`line_code` / `gl_account_id` / `code` / `label` / …) when present,

so only genuine financial divergence (values/deltas outside a 1e-6 epsilon) is
reported. These are comparison artifacts, **not** data differences — verified by
an order-independent set check of `(gl_account_id, amounts)` leaves.
