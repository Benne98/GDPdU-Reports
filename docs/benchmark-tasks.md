# Benchmark Tasks — Orchestrator v2 vs Opus Baseline

Five representative GDPdU-Reports tasks for comparing orchestrated workflow vs
single Opus 4.8 without orchestration.

## Scoring dimensions

| Dimension | How to measure |
|-----------|----------------|
| Correctness | Acceptance criteria met; tests pass |
| Diff size | Lines changed in product code (orchestrator should contribute 0) |
| Latency | Wall-clock to declared done |
| Ceremony fit | Right tier used (L0/L1/L2) |

## Tasks

### B1 — FDD Rasa proxy (L1)

**Prompt:** "FDD bot shows Expert welcome instead of FDD welcome on port 5176."

**Acceptance:** `vite.config.ts` / `.env.fdd-merge` proxy `/rasa` to **5005** not 5006;
`/greet` returns FDD `main_welcome` card.

**Touch:** `frontend/vite.config.ts`, `frontend/.env.fdd-merge*`, `scripts/run-fdd-merge-rasa.ps1`

**Expected tier:** L1 → `frontend-engineer`

---

### B2 — GL ingest error handling (L1)

**Prompt:** "POST ingest returns 500 without message when GL file has bad encoding."

**Acceptance:** Clear 4xx/422 with message; no stack trace leak; pytest for error path.

**Touch:** `etl/load.py`, `backend/app/routers/ingest.py`, `etl/tests/`

**Expected tier:** L1 → `data-transformation-engineer` + `backend-engineer`

---

### B3 — Financials empty state (L0)

**Prompt:** "Financials PL view shows blank panel when no GL data — add empty state."

**Acceptance:** Visible empty state component; `npm run build` passes.

**Touch:** `frontend/src/components/financials/**`

**Expected tier:** L0 → `frontend-engineer`

---

### B4 — Sign convention in margin helper (L2)

**Prompt:** "Add gross margin % column to GST output using existing margin helpers."

**Acceptance:** Formula documented, worked example, edge cases, regression test;
sign convention unchanged.

**Touch:** `funktionssammlung.py`, `general_sales_table_MM_verformelt.py`, tests

**Expected tier:** L2 → full loop + `financial-calculation-engineer`

---

### B5 — Version restore roundtrip (L2)

**Prompt:** "Restore API must re-derive GL after snapshot restore."

**Acceptance:** `etl/versioning.py` restore triggers derive; roundtrip pytest passes.

**Touch:** `etl/versioning.py`, `etl/derive.py`, `backend/app/routers/`, `etl/tests/test_versioning.py`

**Expected tier:** L2 → `data-transformation-engineer` + `test-engineer`

---

## Baseline comparison template

| Task | Tier | Opus solo (est.) | Orchestrator v2 (est.) | Notes |
|------|------|------------------|------------------------|-------|
| B1 | L1 | Fast, may miss scripts | Routes to frontend; verifies proxy + scripts | v2 catches PS1 scripts |
| B2 | L1 | May fix router only | ETL + router split | v2 better boundary |
| B3 | L0 | Over-engineers | L0 direct engineer | v2 less ceremony |
| B4 | L2 | Strong if uninterrupted | Mandatory financial proof + test | v2 safer |
| B5 | L2 | Risk missing derive | Architect + ETL specialist | v2 better for GL pipeline |

## v2 design intent

Orchestrator should **not** beat Opus on raw speed for L0/L1. It should match or
beat Opus on **correctness and diff discipline** for L2 and cross-module tasks, with
≤1.5× latency acceptable for L0.

Run benchmarks manually after install; record results in a dated section below.

### Results (2026-06-14)

Static design validation complete — see `docs/benchmark-results.md`.
Timed Opus-vs-orchestrator runs: **pending manual sessions** in GDPdU-Reports workspace.
