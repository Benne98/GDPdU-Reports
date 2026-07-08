# v5 Pipeline E2E Smoke Harness

Headless, **real-HTTP** proof that the reworked v5 Project-Setup → Reporting
pipeline is **dataset-agnostic**: a fresh synthetic GL dataset — with chart-of-
accounts positions **deliberately absent from the seed structure** — is
provisioned through the REAL ingest endpoints, auto-extension places the unknown
positions, and reporting reflects them.

> **Author-only deliverable.** These scripts were written without DB access and
> have **not** been run against a database. You (coordinator) run them against a
> fresh clone `finssentials_v5_e2e` on a test API (port 8016). No mocks.

## Files

| File | Purpose |
|------|---------|
| `gen_e2e_gl_fixture.py` | Deterministic synthetic fixture generator + the shared dataset spec (constants the driver/asserter import). |
| `run_pipeline_e2e.py` | HTTP driver — runs the exact wizard ingest sequence, verbose per step. |
| `assert_pipeline_e2e.py` | Assertions A1–A7 (imported by the driver; also runnable on a saved context JSON). |
| `_fixture/` | Generated fixture files (git-ignored working dir; regenerated each run). |

Everything is **synthetic**. Never point these at, or replace their output with, real client data.

---

## Prerequisites (IMPORTANT)

1. **A test API on port 8016** pointed at the fresh clone `finssentials_v5_e2e`.
   Launch uvicorn **without** `--env-file` (see MEMORY: the hardcoded `--env-file`
   makes the API fail to start). Example:
   ```powershell
   cd backend
   $env:DB_NAME = "finssentials_v5_e2e"
   python -m uvicorn app.main:app --host 127.0.0.1 --port 8016
   ```
   (Set `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` for the clone as needed.)

2. **The seed statement structure MUST be populated** in `finssentials_v5_e2e`
   (`dim_pl_structure` + `dim_bs_structure`). Auto-extension is only meaningful
   against an existing structure; if the tables are empty the
   `structure/unknown-positions` endpoint returns `structure_available=false` and
   the harness reports **A3 FAIL** with a clear message.
   Seed them if the clone doesn't already have them:
   ```powershell
   cd backend
   python scripts/seed_pl_structure.py    # needs the Decidra "PL Structure" workbook
   python scripts/seed_bs_structure.py    # derives BS rows from dim_gl_account
   ```
   A clone made from a project that already ran setup will have these. `seed_bs_structure.py`
   derives from `dim_gl_account`, so it must run **after** at least one CoA is present — a
   clone of an already-provisioned `finssentials_v5` is the simplest way to have both.

3. **An admin login** for that API (the CoA/GL/extend endpoints require admin).
   Auth is `POST /api/v1/auth/login` with a JSON body `{email, password}`; the
   response carries `access_token`.

4. Python deps: `requests`, `openpyxl`, `pandas` — already in the backend env.

---

## Run it

```powershell
cd backend
python scripts/e2e/run_pipeline_e2e.py `
    --api-base http://127.0.0.1:8016 `
    --email admin@yourdomain.com --password "<admin-password>"
```

Or with a pre-obtained bearer token (skips login):

```powershell
python scripts/e2e/run_pipeline_e2e.py --api-base http://127.0.0.1:8016 --token "<JWT>"
```

Useful flags:

- `--fixture-dir <dir>` — where fixture files are written/read (default `scripts/e2e/_fixture`).
- `--no-gen` — reuse an existing fixture instead of regenerating.
- `--no-assert` — run the pipeline only (skip the assertion phase).

You can also regenerate the fixture by hand to inspect it:

```powershell
python scripts/e2e/gen_e2e_gl_fixture.py --outdir scripts/e2e/_fixture
```

---

## The dataset

- **1 entity** (prefix `01`), **2 fiscal years** (2024, 2025).
- **6 balanced double-entry bookings per year** (12 GL lines/year → 24 total). Every
  booking nets to zero, so the booking / monthly / ledger balance checks pass cleanly.
- **9 accounts**, mapped in a `bs_pl_master` CoA workbook (`Master_BS` + `Master_PL`).
- **3 DELIBERATELY NOVEL positions** whose grain uses **brand-new `level_2` labels**
  that appear in no seed structure, so they classify nowhere:

  | Account | Statement | Novel grain (`level_2`) | `level_3` | Why it won't classify |
  |---------|-----------|-------------------------|-----------|------------------------|
  | 90001 | PL | **Research grants** | R&D tax credits | `level_2` "Research grants" (Excel `L3`) is in no seeded `dim_pl_structure` mapping row. |
  | 90002 | PL | **Crypto trading gains** | — | Novel `level_2`; parent `L2` "Digital assets" is also absent, so no broad L2-only row can catch it. |
  | 90003 | BS | **Crypto holdings** | Wallet balances | Novel `level_2` under a novel `L2` "Digital assets"; absent from `dim_bs_structure`. |

  (Excel→DB level shift: Excel `L3` → DB `level_2`, the grain key that
  `structure/unknown-positions` groups on. See `structure_autoextend.detect_unknown_positions`.)

The other 6 accounts use ordinary paths (Net sales / Cost of materials / Personnel /
Cash / Trade receivables / Trade payables). Whether they classify is **not**
hard-asserted — the proof rides entirely on the guaranteed-novel positions, which is
what makes the harness dataset-agnostic.

---

## Endpoint sequence the driver runs (mirrors the wizard)

```
POST /api/v1/auth/login                            -> bearer token
POST /api/v1/ingest/upload            (x2)         -> per-year GL file_ids
POST /api/v1/ingest/gl/combine                     -> combined GL file_id (+ injected fiscal_year col)
POST /api/v1/ingest/validate          stage=gl     -> summary.passed + exclusions
POST /api/v1/ingest/upload            (CoA xlsx)   -> coa file_id
POST /api/v1/ingest/mapping/commit    bs_pl_master -> dim_gl_account (accounts > 0)
POST /api/v1/ingest/commit            dataset=gl   -> fact_gl_* (lines > 0)
POST /api/v1/ingest/structure/unknown-positions    -> novel positions detected (>0)
POST /api/v1/ingest/structure/extend               -> placed (source='auto_extend')
POST /api/v1/ingest/structure/unknown-positions    -> re-check: novel now resolved
GET  /api/v1/statements/pl , /statements/bs        -> reporting reflects placed positions
GET  /api/v1/financials/pl-statement               -> has_plan_data must be False (best-effort)
```

**Ordering note.** The UI wizard runs `structure/unknown-positions` in its step 7,
*before* the step-8 commits — on a fresh project nothing is committed yet, so it
auto-passes. This harness deliberately runs detection **after** the CoA + GL commits,
because that is the only point at which the novel CoA positions actually exist to be
detected and placed. That post-commit detect → extend → reporting loop is exactly the
behaviour being proven. The CoA commit must precede the GL commit regardless (the GL
commit's FK pre-flight rejects accounts not yet in `dim_gl_account`).

The GL path is faithful to the wizard: per-year upload → `/gl/combine` (which injects a
`fiscal_year` column) → the GL profile reads the fiscal year from that column
(`fiscal_year: {mode:"column", value:"fiscal_year"}`), signed amount, German day-first dates.

---

## What each assertion checks

| Check | Proves |
|-------|--------|
| **A1** | GL rows landed — `POST /ingest/commit` returned `entries > 0` and `lines > 0`. |
| **A2** | CoA mapping landed — `POST /ingest/mapping/commit` returned `accounts > 0`. |
| **A3** | `unknown-positions` detected **every** novel grain (`structure_available=true`, each novel `level_2` present, `total > 0`). |
| **A4** | `structure/extend` placed the positions into the **correct per-statement split table** (PL→`dim_pl_structure`, BS→`dim_bs_structure`) — `inserted`/`skipped` non-empty, target tables present in `table_counts`. |
| **A5** | After extend, the novel positions are **no longer unknown** — the placed rows are exactly what the reader classifies (reader-faithful, no drift). |
| **A6** | Reporting reflects the placed positions — each novel `level_2` appears as a line in `/statements/pl` or `/statements/bs` `lines[]` (L1–L4 hierarchy). |
| **A7** | The fresh dataset has **no plan/forecast** — fin-compat `has_plan_data=False`; falls back to asserting `/statements/pl` carries no forecast/plan column when that endpoint isn't reachable. |

### Expected PASS output (tail)

```
======================================================================
ASSERTIONS
======================================================================
  [PASS] A1: GL landed: entries=24, lines=24
  [PASS] A2: CoA mapping landed: accounts=18
  [PASS] A3: all 3 novel grains detected as unknown (total unknown=3): [...]
  [PASS] A4: placed 3 position(s); inserted=[...] tables={'dim_pl_structure': N, 'dim_bs_structure': M}
  [PASS] A5: novel positions no longer unknown after extend (...)
  [PASS] A6: all novel positions surface as reporting lines (level_2): [...]
  [PASS] A7: fin-compat has_plan_data=False (fresh dataset, no plan)
----------------------------------------------------------------------
RESULT: PASS  (7 passed, 0 failed, 0 skipped, 7 total)
======================================================================
```

Exit code: `0` on all-pass, `1` if any assertion fails, `2` if a pipeline step
stops the run, `3` on an HTTP/connection error.

---

## DB spot-checks (optional, confirm `source='auto_extend'`)

A4 confirms placement through the API. To confirm the provenance column at the DB
layer, query the clone directly:

```sql
SELECT 'pl' AS tbl, line_code, level_2, level_3, source
FROM dim_pl_structure WHERE source = 'auto_extend'
UNION ALL
SELECT 'bs', line_code, level_2, level_3, source
FROM dim_bs_structure WHERE source = 'auto_extend'
ORDER BY 1, 2;
```
Expect rows for `Research grants`, `Crypto trading gains` (PL) and `Crypto holdings` (BS).

Confirm GL rows landed:
```sql
SELECT fiscal_year, count(*) FROM fact_gl_line
WHERE account_number_group LIKE '01%' GROUP BY 1 ORDER BY 1;
```

---

## Endpoints I was unsure about (watch these when running)

1. **`GET /api/v1/financials/pl-statement` query params** — confirmed from
   `financials_compat.py`: the driver sends `period_grain=year, year=<fy>, month=12,
   entity=01` (note `_validate_period` requires **both** `year` and `month` even for
   the `year` grain), and reads the top-level `has_plan_data` bool
   (`out["has_plan_data"] = bool(plan_map)`). If your build changes this signature it
   returns non-200 and **A7 falls back** to checking `/statements/pl` columns for a
   forecast column, so the run still completes — but the direct `has_plan_data=False`
   proof is the stronger one.

2. **`/gl/combine` header confirmation** — the wizard optionally calls
   `apply-headers` on the combined file. The harness skips it because the per-year
   CSVs already have correct headers and combine preserves them; if your build
   requires an explicit `apply-headers` before validate, add it after `combine_gl()`.

3. **`entity` filter on `/statements/*`** — the harness omits `entity` (aggregates
   all; the dataset is single-entity). If your reporting requires an entity code
   rather than the prefix `01`, pass it — but omitting is the safe default here.

4. **`PUT /api/v1/projects/default`** — the wizard's step-8.1 project save is
   **skipped**; `mapping/commit` with `entity_prefixes` already upserts
   `dim_legal_entity` (proven by `test_ingest_coa.py::test_A_...`). If your reporting
   needs a project row (e.g. for `fy_start_month`), the harness passes
   `fy_start_month=1` as a query param instead.
```
