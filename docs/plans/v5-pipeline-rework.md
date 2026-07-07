# v5 Pipeline Rework — dataset-agnostic Project-Setup → Reporting

> Reconstructed 2026-07-07 from memory `v5-pipeline-stack.md` + working-tree evidence.
> Stack: **Frontend 5181 / API 8015 / DB `finssentials_v5`** (fresh clone). The merged
> stack 5180/8014 stays as the numeric **parity baseline** — v5 must match it numerically
> (tolerance band for intentional fixes / un-parked forecast). Branch: `feat/v5-pipeline`.
> Free (breaking) migrations allowed on the v5 DB. Migration head 0029; new work chains 0030+.

## Prime directive
Make data provisioning **dataset-agnostic / swappable** — the same CoA-driven L1–L4 hierarchy
every time, regardless of client workbook. **Invariant everywhere:** FLOW (PL / Profitability /
Payroll-cost) = period sums; STOCK (BS / FA-NBV / OPOS) = opening balance + movements to cutoff.
Sign `+`=Soll / `−`=Haben, exactly one presentation flip.

## The 12 fixed decisions
1. PL/CF/BS structures in **physically separate tables** (`dim_pl_structure` = PL only, new
   `dim_cf_structure`, new `dim_bs_structure`) — CF can never appear in the Income Statement.
2. **Auto-extension** of structure = interactive Project-Setup step: user places unknown CoA
   positions before commit.
3. Real **ISO-weekly** for P&L (FLOW) + BS (STOCK); ISO-week SQL primitives exist — mostly routing.
4. **Versioned plans** via new `dim_plan_version`; active version + "include in reporting" toggle in
   the Plan-Budgeting tab; un-park the NaN annual forecast/coverage columns. Exactly ONE active plan
   version per (project, statement, fiscal_year) — scenario NOT in the unique key; forecast becomes a
   derived column of the single active version (drops today's side-by-side Budget+Forecast).
5. **OB mode per-project** configurable (`in_data` / `file` / `carry_forward`) in Project Setup.
6. Remove entity dropdown from IS/BS/CF/WC (group reports cover it).
7. Add **multi-select entity filter** to Profitability / Payroll / Fixed-Assets / OPOS sub-pages.
   GL + Profitability always shown; Payroll / Fixed-Assets / OPOS-Aging only when data is loaded
   (conditional display).
8. OPOS aging/DSO/DPO — already built/live/tested; **wire the GoBD gross-sales/purchases journal**
   (`Referenz` / `GoBD_Transaktionsnr`) for REAL DSO/DPO replacing the term-proxy (AR30/AP45); remove
   dead `opos.py:259 derive_aging` 501 stub.
9. Fixed-Assets NBV rollforward — already live but **PASS-THROUGH** (sums file's reported AfA/NBV).
   Keep depreciation pass-through; F1 closing-cost AHK + F2 depreciation-method **deferred**; transfers
   stay out of the bridge; FA page shows **ANNUAL only** (grey out Monthly/Weekly pills — snapshots are
   year-end only, no interim as-of).

## Drift correction (verified 2026-07-07) — re-ground every "build" phase as parity-first
Phases 7/8/9 are mostly **verify-parity + conditional-display + multi-entity-filter + carry into v5**,
NOT greenfield builds:
- **OPOS aging** fully built/live/tested — `opos_aging.py::compute_opos_aging` (Method A + FIFO + clamp
  + credit-balance + corrected DSO=30/DPO=45), wired DB→`sales_compat.py`→`api.ts`→`SalesAgingTab.tsx`;
  F1–F5 approved in `docs/financial-logic.md` (2026-07-01), 12 tests green. Old `opos.py` derive_aging is dead.
- **Fixed-assets NBV** live (`fixed_asset_rollforward.py` + `fixed_asset_calc/movements/report*.py`, router
  `/api/v1/fixed-assets/*`, `GlFixedAssetsTab`) — pass-through only.
- **Weekly ISO grain** already documented/built (reporting-v2 Phase 5): `week_cutoff` / `week_range`,
  `pl/bs_grain_sql_week`. Verify `periods.py` routing before rebuilding.

## Sequencing rule
Work out & approve financial formulas in `docs/financial-logic.md` **FIRST** (biggest gate) before
implementing. Status: OPOS F1–F4 ✅ approved; FA F1/F2 deliberately **deferred** (decision 9).

## Phase state (reconstructed)
| Phase | Scope | State |
|---|---|---|
| 0 | Bootstrap v5 stack | ✅ committed `4c7412c` |
| 1 | Split PL/CF/BS structures (dec. 1) | ✅ **code-complete + gate-green** (2026-07-07); migration `0030` authored; CF→`dim_cf_structure`, BS/WC→`dim_bs_structure` readers repointed (`fin_compat_cf/bs`, `budget_service._load_positions`, `balance_sheet`, seed scripts); `dim_bs/cf_structure` added to `RESET_KEEP_TABLES`; new `test_structure_split.py`. **Live-DB steps still owed** (see below). Offline gate: 276 passed / 5 skipped |
| 2 | CF structure + CF key-space bug | CF key-space fallback ✅ in `fin_compat_pl.py` L481 (`by_direct` index) |
| 3 | ISO-weekly routing (dec. 3) | primitives exist; routing verify/wire pending |
| 4 | `dim_plan_version` + active-version toggle (dec. 4) | pending |
| 5 | Auto-extension Project-Setup step (dec. 2) | pending |
| 6 | OB mode carry_forward (dec. 5) | ✅ committed `d389c30` |
| 7 | Remove entity dropdown; multi-select filter; conditional display (dec. 6/7) | pending |
| 8 | Real DSO/DPO via GoBD journal; remove dead opos stub (dec. 8) | formulas ✅; wiring pending |
| 9 | FA annual-only pills; keep pass-through (dec. 9) | pending |

**Interlude done (uncommitted → being committed):** Annual Consolidation P&L L4 detail-child
drill-down — `_annual_l4_children` wired into `build_pl_annual_consolidation`; frontend
`maxDepthOverride` (depth 0–2 auto-expand). Reconciles Σ children == parent per entity (residual
"(no L4)" bucket); 5 regression tests in `test_compat_layer.py`.

## Phase 1 — LIVE-DB steps still owed (need `DB_PASSWORD` + running servers)
Code is done + offline-gate-green; these require the live v5 DB, which the offline test process
cannot reach:
1. **Apply migration 0030** to `finssentials_v5`: `.\scripts\run-v5-api.ps1` style env, then
   `cd backend && alembic upgrade head` (chains `0029 → 0030`). Confirm `dim_bs_structure` /
   `dim_cf_structure` exist and are row-populated (0030 copies from `dim_pl_structure`).
2. **Seed** the split structures if needed: `python -m backend.scripts.seed_bs_structure` /
   `seed_cf_structure` (now target the split tables).
3. **Restart API 8015** (OneDrive `--reload` unreliable → stale code).
4. **Parity check** IS/BS/CF on 5181 vs the 5180 baseline — numbers must match (tolerance band). The
   split relocates *where* structure rows are read; it must not change any statement number.

## Next up (candidates — pick after Phase 1 live-parity)
- **Phase 4** `dim_plan_version` + single active-version toggle (needs a migration; un-parks forecast/coverage).
- **Phase 7** remove entity dropdown from IS/BS/CF/WC; multi-select entity filter + conditional display
  (frontend + read-only backend; NO migration → verifiable offline; but beware the uncommitted
  ~110-file typography pass touching the same frontend tree).
- **Phase 5** auto-extension interactive Project-Setup step (dec. 2).
- **Phase 3** ISO-weekly routing verify (dec. 3).

## Known pre-existing test-suite fragility (NOT introduced by this rework)
The full backend suite has order-dependent test-isolation leaks (`app.dependency_overrides` /
shared SQLite schema state) + live-DB-required tests that hang offline (`no password supplied`).
Symptoms seen: `test_budget_api::test_get_requires_auth` 500 (fixed here via a `_clear_dependency_overrides`
autouse teardown in `test_compat_layer.py`), plus `test_anomaly_entity_auth`, `test_granularity_view`
reconciliation, `test_ingest_issue_rows` failing only under full-suite ordering. Verify phases with the
**mock-based touched-file gate**, not the full offline suite. Candidate cleanup: harden auth-test
isolation (test-engineer).

## Guardrails
- Hard rule #1: no financial logic without formula + worked example + edge cases + test.
- Parity proof: golden-file snapshots of 5180 endpoints; v5 must match (tolerance band).
- Restart API **8015** after backend/etl Python edits (OneDrive `--reload` unreliable → stale code).
- No push without explicit approval. Keep the merged 5180 stack untouched as baseline.
- Tenant isolation gap persists (GL/plan facts are `entity_prefix`-scoped, not `project_id`) — out of
  scope unless a phase specifically addresses it.
