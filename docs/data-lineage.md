# GDPdU-Reports — Data Lineage (General Ledger → Reports)

> Baseline reference for designing a robust, scalable ingestion → reporting pipeline.
> Captures the end-to-end flow: raw GL → dimension/mapping/sorting tables → derived &
> plan facts → report builders. Table names, keys, transforms and the file/function
> that performs each step are named so the pipeline can be re-architected with confidence.
>
> **Stack:** PostgreSQL. ETL in `etl/` (pure-ish, DB writes). Read/report layer in
> `backend/app/services/`. One **tenant/engagement = one database** today (e.g. `Finssentials`,
> clone `finssentials_v2`).

---

## 0. The flow at a glance

```
 GDPdU export / Excel / CSV
        │
        │  (1) ingest GL            etl/load.py:load_canonical()
        ▼
 ┌───────────────┐   split per (jegn, fiscal_year)
 │ fact_gl_entry │◄─ header (1 row / entry)
 │ fact_gl_line  │◄─ lines  (N rows / entry)   amount = +debit / −credit  (CANONICAL SIGN)
 └───────┬───────┘
         │  (2) account mapping     etl/mapping_account.py:apply_account_mapping()
         │      upsert dims keyed (account_number_group, fiscal_year)
         ▼
 ┌──────────────────┐  ┌────────────┐  ┌────────────┐
 │ dim_gl_account   │  │ dim_gl_na  │  │ dim_gl_cf  │      classification / hierarchy
 │ level_0..4, sort │  │ l6/l7 (NA) │  │ l1..5, cf_ │
 └──────────────────┘  └─────┬──────┘  │  mapping   │
         ▲                    │         └─────▲──────┘
         │                    │ NA key        │ filled from workbook OR
 dim_pl_structure             └──────────────►│ lib_cf_mapping (key=NA / PL level_3)
 (sort_order, row_type,       lib_cf_mapping      ← reusable, accumulates per project
  level_2/3/4, kpi_code)
         │
         │  (3) versioning          etl/versioning.py:capture_gl_snapshot(load_id)
         ▼      immutable copy of fact_* + dim_* per load_id
 snap_fact_gl_entry / snap_fact_gl_line / snap_dim_gl_account / snap_dim_gl_na / snap_dim_gl_cf
         │            (org_meta_dataset_load = audit trail + content_hash dedup + restore)
         │
         │  (4) derive facts        etl/derive.py + etl/derive_facts_sql.py:derive_all_facts()
         ▼      classify by level_3 / level_2, link partners
 fact_sales (gross_sales=−amount) · fact_com (cost=amount) · fact_ar · fact_ap
         │
         │  (5) synthetic GL        etl/opening_balance.py · etl/net_profit.py
         │  (6) plan/budget         etl/plan_synth.py · backend/app/services/budget_service.py
         ▼                          → fact_gl_plan · fact_sales_plan · fact_position_plan
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  (7) REPORT BUILDERS  (read path, stateless per request)                   │
 │  fin_compat_pl / fin_compat_bs / fin_compat_cf / fin_compat_wc             │
 │  grain SQL (month/annual) → match dim_pl_structure → running-sum subtotals  │
 │  granularity_view (budget) · budget_service / budget_heuristics            │
 └──────────────────────────────────────────────────────────────────────────┘
         ▼
   FinancialStatementResponse  →  Reporting UI (IS / BS / CF / WC) · Budget chat
```

The whole report layer is **read-only and stateless**: every request re-aggregates
`fact_gl_line` joined to the dimension/mapping tables. There are no materialized report
tables — "report tables" are produced on the fly by the grain SQL + Python builders.

---

## 1. Ingestion — raw GL in

**Entry point:** `etl/load.py:load_canonical(lines_df, linking_strategy, account_classes, commit_mode)`

| Target table | Key | Written by | Notes |
|---|---|---|---|
| `fact_gl_entry` | (journal_entry_group_number, fiscal_year) | `_bulk_insert_entries()` | one header row per entry; `entity_prefix` = `LEFT(account_number_group,2)` (generated) |
| `fact_gl_line` | (jegn, fiscal_year, line_number); UNIQUE(booking_line_id) | `_bulk_insert_lines()` | the booking lines — the dominant table |

**Canonical input columns** — entry: `journal_entry_group_number, fiscal_year, fiscal_period,
entry_type, posting_date, document_date, document_type_code, reference_document_number,
currency_code, header_note, source_system`; line: `+ line_number, booking_line_id,
account_number_group, amount, vat_amount, line_note, customer_id, supplier_id, posting_type`.

**Sign (canonical, everywhere):** `amount = +debit (Soll) / −credit (Haben)`.

**Dedup:** `org_meta_dataset_load.content_hash` (SHA-256) — re-uploading the same file is skipped
(`dedup_check()`).

---

## 2. Account mapping — dimension & sorting tables

**Entry point:** `etl/mapping_account.py:apply_account_mapping(mapping_df, profile)` → upserts
(ON CONFLICT … DO UPDATE) keyed by **(account_number_group, fiscal_year)**.
`account_number_group = entity_prefix(2) + account_number.zfill(6)` (8 chars), via
`etl/transform.build_account_number_group()`.

| Table | Key | Columns (core) | Role |
|---|---|---|---|
| `dim_gl_account` | (ang, fiscal_year) | gl_account_id, account_name, **level_0 (BS/PL)**, level_1..4, l4_sub, **level_2_sort, level_3_sort**, is_ic | Chart-of-accounts hierarchy + display order; join key for GL lines & derived facts |
| `dim_gl_na` | (ang, fiscal_year) | **l6_na_mapping** (FA/TWC/OWC/ND/Equity/…), **l7_na_description** | Net-asset classification (BS accounts only); the **NA key** into the CF mapping library |
| `dim_gl_cf` | (ang, fiscal_year) | l1, l2, l3, l4, l5, **cf_mapping** | Per-account cash-flow mapping; `cf_mapping` = the leaf that matches the flat CF structure rows. **If empty → CF reports all zero.** |

**Sorting / structure templates (project-independent):**

| Table | Key | Role |
|---|---|---|
| `dim_pl_structure` | sort_order (UNIQUE) | The IS/BS/CF **report skeleton**: `row_type` ∈ {mapping, subtotal, calc, kpi, grandtotal, title}, `balance_title`, `level_2/3/4`, `kpi_code` (`CF:*` for CF leaves), `calc_type`, `invert_delta`, `is_bold`. `sort_order` drives statement order. Seeded by `scripts/seed_bs_structure.py` (+ CF rows). |
| `lib_cf_mapping` *(migration 0017; renamed from `cf_mapping_library` in 0019)* | (key_kind, key_1, key_2) | **Reusable, accumulating** CF mapping. `key_kind='na'` → keyed by (l6_na_mapping, l7_na_description) for BS accounts; `key_kind='pl_level3'` → keyed by P&L `level_3` for EBITDA/Taxes/D&A/Financial-result. New projects auto-match by classification; new mappings added here once and reused. Loaders: `scripts/load_cf_mapping_library.py` → `scripts/populate_dim_gl_cf.py`. |

**How an account gets classified:** the mapping file assigns each `account_number_group`
its P&L/BS hierarchy (`dim_gl_account.level_0..4` + sort), its NA class (`dim_gl_na`), and —
when present — its CF mapping (`dim_gl_cf`). The CF mapping is what was missing (empty
`dim_gl_cf`) and is now produced via `lib_cf_mapping` keyed on the NA / PL-level_3 class.

---

## 3. Versioning & snapshots

**Entry point:** `etl/versioning.py:capture_gl_snapshot(load_id, prefixes, years)` — copies the
live fact + dim rows **in scope** into `snap_*` tables, tagged by `load_id`.

| Snapshot | Mirrors | Key |
|---|---|---|
| `snap_fact_gl_entry` / `snap_fact_gl_line` | fact_gl_entry / fact_gl_line | (load_id, …live PK) |
| `snap_dim_gl_account` / `snap_dim_gl_na` / `snap_dim_gl_cf` | the three dims | (load_id, ang, fiscal_year) |

`org_meta_dataset_load` (PK `load_id`) is the audit trail: `dataset, legal_entity_code, fiscal_year,
row_count, content_hash, scope_entity_prefixes[], scope_fiscal_years[], commit_mode,
snapshot_captured, superseded_by_load_id, restored_from_load_id`. Enables dedup, restore-to-load,
and scope tracking.

> **Storage note:** `snap_*` roughly **doubles** the GL footprint (measured: `snap_fact_gl_line`
> ≈ `fact_gl_line`). Each retained load adds another in-scope copy → the main scaling lever.

---

## 4. Derived facts

**Entry points:** `etl/derive.py` (in-load) and `etl/derive_facts_sql.py:derive_all_facts(session)`
(idempotent DELETE+INSERT rebuild). Partner linking: `link_partners(strategy='txn'|'gegenkonto'|'none')`.

| Table | Key | Filter (classify on) | Stored value |
|---|---|---|---|
| `fact_sales` | booking_line_id | level_3='Net sales' / level_2='Income' | **gross_sales = −amount** (revenue presented +) |
| `fact_com` | booking_line_id | level_3='Cost of materials' | **cost_of_materials = amount** |
| `fact_ar` | booking_line_id | level_3='Trade receivables' | amount (asset side) |
| `fact_ap` | booking_line_id | level_3='Trade payables' | amount (liability side) |

Classification uses **hard-coded `level_3` label sets** (`REVENUE_L3`, `MATERIAL_L3`,
`RECEIVABLE_L3`, `PAYABLE_L3`) — a known fragility (see §8).

---

## 5. Synthetic GL & plan/budget

- **Opening balances** — `etl/opening_balance.py:synthesize_opening_balances(scope, mode)`
  (`carry_forward` | `file` | `in_data`), writes synthetic `fact_gl_entry/line`
  (`source_system=SYNTHETIC_OB_SOURCE`, `fiscal_period=0`).
- **Net profit** — `etl/net_profit.py:synthesize_net_profit(scope)` injects the balancing equity
  booking so BS ties to YTD P&L (gated by `settings.bs_net_profit_source`).
- **Synthetic plan/forecast** — `etl/plan_synth.py` (pure): `seasonal_index()` +
  `project_plan_annual()` + `project_forecast_open_periods()`. Scenarios: `actual | plan |
  forecast | budget`.
- **Manual budget** — `backend/app/services/budget_service.py` (`upsert_cell`, `patch_position`):
  presented grid sign → stored GL sign; writes `fact_position_plan` (+ synthetic
  `fact_gl_plan`/`fact_sales_plan`, scenario `budget`). Heuristic seeds:
  `budget_heuristics.py` (`prior_year`, `trend_cagr`, `run_rate` — **complete fiscal years only**).

| Plan table | Key | Sign |
|---|---|---|
| `fact_gl_plan` | (ang, fy, period, scenario) | GL sign (+debit/−credit) |
| `fact_sales_plan` | (customer_id, fy, period, scenario) | gross_sales_plan + |
| `fact_position_plan` | (line_code, fy, period, scenario) | GL sign; line_code = dim_pl_structure.line_code |

---

## 6. Report builders (read path)

Pattern for every statement: **grain SQL** (base tuples + period columns) → **Python row builder**
(nest hierarchy, running-sum subtotals, KPIs) → **FinancialStatementResponse**.

| Statement | Builder | Grain SQL | Join key | Sign rule |
|---|---|---|---|---|
| **P&L** | `fin_compat_pl.build_pl_monthly` / `build_pl_annual_compat` | `fin_compat_sql.pl_grain_sql_month/annual` | dim_gl_account `level_2/3/4` (level_0='PL') | `SUM(amount*−1)` once in SQL |
| **Balance Sheet** | `fin_compat_bs.build_bs_monthly` / `build_bs_snapshot_annual` | `fin_compat_bs_sql.bs_grain_sql_month` | dim_gl_account `level_2/3/4` (level_0='BS'), cumulative ≤ period-end | raw sign → flip credit side once in `_flip_row_tree()`; + net-profit injection into equity |
| **Cash Flow** | `fin_compat_cf.build_cf_statement_compat` / `build_cf_annual_compat` | `fin_compat_cf_sql.cf_grain_sql_month` | **`dim_gl_cf` (l1..5, cf_mapping)** — not level_* | `SUM(amount*−1)`; `cf_mapping` matched to structure (`_norm_cf_key` unifies ∆/Δ); 'Exclude' filtered; section-aware subtotals |
| **Working Capital** | `fin_compat_wc.py` | — | dim/fact WC lines | as BS |
| **Budget granularity** | `granularity_view.build_granularity_view` | reuses PL/BS builders | — | windows columns (month=last 24; year=3 complete FYs), drops kpi/title, annotates plannable/partner/has_l4, adds subtotal `components[]` for live FE recompute |

**Subtotals:** running cumulative sum in `sort_order` (P&L global; BS & CF section-aware,
reset per section). **KPIs:** ratios from `kpi_code` (e.g. `GROSS_MARGIN_PCT`, `EQUITY_RATIO`).

---

## 7. End-to-end order (new file → numbers)

1. **Ingest GL** — `load_canonical()` → `fact_gl_entry`, `fact_gl_line` (+ dedup).
2. **Ingest account mapping** — `apply_account_mapping()` → `dim_gl_account` / `dim_gl_na` / `dim_gl_cf`.
3. **Link partners + derive facts** — `derive.py` / `derive_facts_sql.derive_all_facts()` → `fact_sales/com/ar/ap`.
4. **Capture snapshots** — `capture_gl_snapshot(load_id)` → `snap_*` (+ `org_meta_dataset_load`).
5. **Rebuild** — `etl/rebuild.py:rebuild_project(scope)`: CoA overrides → backfill partner links → re-derive facts → opening balances → net profit → refresh structure/recon.
6. **Populate CF mapping** — `load_cf_mapping_library.py` → `populate_dim_gl_cf.py` (idempotent; **required for non-zero CF**).
7. **Plan/forecast (optional)** — `plan_synth.py` → `fact_gl_plan`, `fact_sales_plan`.
8. **Budget (user)** — `budget_service` → `fact_position_plan`.
9. **Report read-time** — resolve entity→prefix → grain SQL → match `dim_pl_structure` → build tree → `FinancialStatementResponse`.

---

## 8. Fragilities & scaling risks (for the new pipeline)

1. **Empty `dim_gl_cf` → CF all-zero** (the recent incident). CF depends entirely on a per-account
   mapping that is *separate* from the P&L/BS hierarchy. **Mitigation:** make the CF-populate step
   (`lib_cf_mapping` → `dim_gl_cf`) a mandatory, idempotent, monitored stage of every load;
   assert coverage (every NA/PL-level_3 class has a library match) and fail loud.
2. **Hard-coded `level_3` classification** for derived facts — a renamed label silently drops
   `fact_sales/com/ar/ap`. **Mitigation:** make classification config/data-driven per project.
3. **Per-(entity, fiscal_year) mapping cardinality** — every new entity/year needs its own mapping
   upload; a missing one blinds that slice (and append-mode GL can insert lines with no
   `dim_gl_account` match). **Mitigation:** enforce "mapping before GL"; carry a reusable mapping
   library (as done for CF) for accounts/NA too.
4. **Snapshot growth (~2× per retained load)** — `snap_*` outgrows live tables. **Mitigation:**
   retention policy via `superseded_by_load_id`; archive/cold-store old `load_id`s;
   partition `fact_gl_line` / `snap_fact_gl_line` by (entity_prefix, fiscal_year).
5. **Unicode ∆ (U+2206) vs Δ (U+0394)** in CF labels — normalized at *read* time
   (`_norm_cf_key`) only. **Mitigation:** normalize at write time in the CF populate step.
6. **Opening-balance / net-profit idempotency** keyed on `source_system` sentinels — corruption →
   duplicates. Keep delete-then-reinsert under test.
7. **`entity_prefix` = `LEFT(ang,2)`** (generated) — malformed `account_number_group` breaks the
   entity filter. Enforce at transform time.
8. **Multi-tenant isolation** — today one DB per tenant; cross-tenant filtering is only partially
   enforced in the app layer. A scalable multi-tenant design needs row-level tenant scoping (or
   schema/DB-per-tenant) made explicit in every fact/dim and every query.
9. **No materialized report tables** — every report re-aggregates the GL. Fine at one tenant /
   ~1.3M lines; at scale, consider materialized per-(entity, period, statement) aggregates or a
   pre-computed cube, with the grain SQL as the source of truth.

---

## 9. Sign-convention cheat-sheet (single source of truth)

`amount` everywhere = **+debit, −credit**. P&L & CF apply `*−1` **once** in the grain SQL. BS keeps
raw sign and flips the credit side **once** in `_flip_row_tree()`. Derived `fact_sales.gross_sales
= −amount`, `fact_com.cost_of_materials = amount`. Never double-flip.

| Item (account nature) | Stored | P&L presented | BS presented | CF presented |
|---|---|---|---|---|
| Revenue (credit) | − | + | — | + |
| Material/expense (debit) | + | − | — | − |
| Asset (debit) | + | — | + | (via Δ) |
| Liability/Equity (credit) | − | — | + (after flip) | (via Δ) |

---

## 10. Key table reference

**Facts:** `fact_gl_entry`, `fact_gl_line`, `fact_sales`, `fact_com`, `fact_ar`, `fact_ap`,
`fact_gl_plan`, `fact_sales_plan`, `fact_position_plan`.
**Dims / mapping / sorting:** `dim_gl_account`, `dim_gl_na`, `dim_gl_cf`, `dim_pl_structure`,
`lib_cf_mapping`, `dim_legal_entity`, `dim_customer`, `dim_supplier`.
**Snapshots:** `snap_fact_gl_entry`, `snap_fact_gl_line`, `snap_dim_gl_account`, `snap_dim_gl_na`,
`snap_dim_gl_cf`.
**Audit/auth:** `org_meta_dataset_load`, `dim_user`, `dim_role`, `user_role`, `admin_role_entity_visibility`,
`auth_session`.

---

*Generated as the baseline for the scalable-pipeline design. Verify table/column details against
the live schema and `backend/migrations/` before relying on any single field.*
