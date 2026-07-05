# Overview Page Redesign — Implementation Plan (reporting-v2 / Port 5177)

> Status: **PLAN — awaiting review/approval. No production code changed.**
> Scope: redesign the Overview Page on the reporting-v2 stack only (Vite mode `reporting-v2` → API 8011 → DB `finssentials_v2`).
> Hard guarantee: **Port 5178 (`v4`), Port 5176 (`fdd-merge`), and live API 8010 render byte-identical to today.**
> Authors: software-architect (lead, IA/gating/perf/phasing) + financial-calculation-engineer (KPI formulas) + data-transformation-engineer (data sourcing).

---

## 0. Executive summary

The current Overview (`frontend/src/pages/OverviewPage.tsx`) shows Group overview · DuPont ("Performance Overview") · Top customers · Top suppliers, loads slowly (each block fires its own unbounded fin-compat `bal_mov` BS/WC scans over ~1.3M GL rows), and reads as tables + prose, not decisions.

The redesign flips the page to a **decision-first IA**: a hero KPI + alert strip → a findings feed (one-liners each with a deep-link to the detail page) → 8 compact analysis blocks → the existing exhaustive tables moved to a collapsed lower section. It is delivered entirely behind a **new single mode flag** so nothing outside 5177 changes, and its slowness is fixed with **one batched summary endpoint + a pre-aggregated period/entity snapshot** rather than the current fan-out.

Two financial metrics require owner sign-off before their blocks can ship (they introduce net-new monetary logic): the **AR collectibility haircut** ("Zeitverkauf"/liquidity) and the **fixed-asset roll-forward** (transfer sign + depreciation method). Both are flagged and deferred to later phases.

---

## 1. Information architecture + wireframe

Principle: top of page answers "is anything wrong and where do I click"; bottom holds the exhaustive tables. Findings are one-liners with a deep-link, never "go navigate to X" prose.

```
┌───────────────────────────────────────────────────────────────────────┐
│  HEADER  "Reporting / Overview" · period label            [reporting-v2]│
├───────────────────────────────────────────────────────────────────────┤
│  CollapsibleModuleFiltersCard  (entity · grain · period)   [UNCHANGED] │
├───────────────────────────────────────────────────────────────────────┤
│  ▓ HERO STRIP ▓                                                         │
│  ┌─────────────┬─────────────┬─────────────┬─────────────┐             │
│  │ Revenue YoY │ EBIT/Margin │ Cash & Liq. │ CCC / WC    │  KPI cards  │
│  │ +Δ vs Plan  │ +Δ vs Plan  │ runway      │ DSO/DPO/DIO │             │
│  └─────────────┴─────────────┴─────────────┴─────────────┘             │
│  ⚠ ALERT RAIL: recent-months exceptions · vs-plan breaches · anomalies │
├───────────────────────────────────────────────────────────────────────┤
│  ▓ FINDINGS FEED ▓  (≤5 cards, each: 1 sentence + [deep-link →])       │
│   • "EBIT −8% YoY, −5% vs plan, driven by materials"   → /income-statement
│   • "DSO up 6d over 3 months"                          → /working-capital
│   • "Cash −€1.2m QoQ; runway tightening"               → /cash-flow      │
│   • "Customer A concentration 34%"                     → /working-capital│
│   • "3 posting anomalies flagged"                      → /anomaly-detection
├───────────────────────────────────────────────────────────────────────┤
│  ▓ 8 ANALYSIS BLOCKS ▓  (2-col grid, each = compact viz + findings)     │
│  ┌───────────────────────────┬───────────────────────────┐            │
│  │ (1) Performance YoY/Plan  │ (2) Cash & Liquidity      │            │
│  ├───────────────────────────┼───────────────────────────┤            │
│  │ (8) Working Capital DSO.. │ (7) Driver/DuPont FINDINGS │            │
│  ├───────────────────────────┼───────────────────────────┤            │
│  │ (4) Customer development  │ (5) Supplier development   │            │
│  ├───────────────────────────┼───────────────────────────┤            │
│  │ (3) Fixed assets [PLACEHOLDER — later phase]           │            │
│  └────────────────────────────────────────────────────────┘            │
├───────────────────────────────────────────────────────────────────────┤
│  ▓ LEGACY / DETAIL (moved DOWN, collapsed by default) ▓  (req 6)        │
│   ▸ Group overview  (OverviewGroupTile / Executive summary / EBIT)     │
│   ▸ Top customers   (TopCustomerTable)                                 │
│   ▸ Top suppliers   (TopSupplierTable)                                 │
│   ▸ Full DuPont tree (chart view — on demand)                          │
└───────────────────────────────────────────────────────────────────────┘
```

### Confirmed deep-link routes (`frontend/src/App.tsx`)
`/income-statement`, `/balance-sheet`, `/working-capital`, `/cash-flow` (Cash & debt is a sub-tab; `/cash-debt`→`/cash-flow`), `/account-statement`, `/anomaly-detection` (+ `/outliers`, `/seasonality`, `/forensic`).

**Open question:** there is **no standalone Sales/Aging route**. AR/AP aging is served by `api.arAging`/`api.apAging` inside `/working-capital`. Customer/supplier findings therefore deep-link to `/working-capital` (aging/DSO/DPO) + `/income-statement` (partner sales / cost of materials). Confirm this, or whether a new `/sales` route is intended.

### 8 areas → blocks → data → deep-link

| # | Requirement | Block | Data source (mostly exists) | Findings deep-link |
|---|---|---|---|---|
| 1 | YoY + vs-Plan + recent-months alerting | Hero + Block (1) + alert rail | `overview_metrics.build_ebit_table`, plan tables, `financialsOverview(Highlights)` | `/income-statement` |
| 2 | Cash development & liquidity | Hero (Cash) + Block (2) | GL BS cash balance + OPOS AR/AP aging | `/cash-flow` |
| 3 | Fixed assets | Block (3) **placeholder, later phase** | `fact_fixed_asset` (0022, empty) | `/balance-sheet` (when built) |
| 4 | Customer development | Block (4) | `fact_sales` + `dim_customer`, OPOS AR | `/working-capital` + `/income-statement` |
| 5 | Supplier development | Block (5) | `fact_com` + `dim_supplier`, OPOS AP | `/working-capital` + `/income-statement` |
| 6 | Legacy tables moved down | Lower section | existing GroupTile/MarketWatch/tables | in-place expand |
| 7 | Driver / DuPont — short FINDINGS | Block (7) | `overview_metrics.build_dupont` + `buildDuPontFindings` | `/income-statement`, `/working-capital`, `/balance-sheet` |
| 8 | Working Capital DSO/DPO/DIO + deep-dives | Hero (CCC) + Block (8) | `fin_compat_wc_sql` / `wcRatios` + CCC | `/working-capital` |

---

## 2. Metric definitions (financial-calculation-engineer)

**Grounding conventions reused verbatim (do NOT change):**
- GL P&L sign (`fin_compat_sql.py`): stored `fact_gl_line.amount` = `+debit/−credit`; presented = `amount * -1` (the only inversion). Revenue → positive, cost → negative.
- Sales facts: `fact_sales.gross_sales` already `−amount` (positive); `fact_com.cost_of_materials` already `+amount` (positive). All reported in **kEUR = Σ/1000**.
- BS/WC (`fin_compat_wc.py`): raw stored sign, cumulative stock `posting_date <= cutoff`; days use ABS magnitude, 365 basis, LTM denominator.
- Deltas: `mom = cm−pm`, `yoy = cm−py_cm`, `ytd = ytd−ytd_py`; `invert_delta` negates cost-line deltas so a shrinking expense reads favorable.
- Period basis via `funktionssammlung.get_period` / `fiscal_year_bounds` only — never inline.
- **Two period regimes kept separate:** GL modules key on `fiscal_year`/`fiscal_period`; sales-fact modules key on calendar `posting_date` windows (`cm/pm/py_cm/ytd/ytd_py`) because derived facts lack `fiscal_period`.

**Sign convention for every %/variance:** `FAV+` = larger-positive is favorable (revenue, profit, margin, growth); `FAV−` = smaller is favorable (cost, DSO, DIO, days-overdue). `FAV−` metrics use `invert_delta=True` so displayed positive = good.

### Area 1 — YoY + vs-Plan performance
- **Revenue by entity/region/customer** — `Rev[dim,w] = Σ gross_sales/1000` over the window; dims via `_dim_expr` (entity = `LEFT(account_number_group,2)`, region = `sql_end_customer_region_expr`, customer = `dim_customer.name_line_1`). `FAV+`. Missing customer → `'Unknown'` bucket. Example (Jun-2026): A=500, B=120, C=0 kEUR.
- **Margin** — headline = **Gross margin** at customer/region grain, **EBIT margin** at entity grain only. `GrossProfit = Rev − COM`; `GrossMargin% = 100·GP/Rev` (`None` if `|Rev|<1e-6`); `EBITMargin% = 100·EBIT/|TOTAL_OUTPUT|`. Example: Rev 1000, COM 620 → GP 380, GM 38.0%; EBIT 90 / output 1050 → 8.57%. `FAV+`. Do not blend GL-based and fact_sales-based revenue in one number.
- **YoY %** — YTD-aligned: month → `cm` vs `py_cm`; YTD → `ytd` vs `ytd_py` (same month count). `YoY% = 100·(cur−py)/|py|` (`None` if `|py|<1e-6`). Example: 500 vs 400 → +25.0%. Revenue `FAV+`; cost `FAV−` (invert). Sign-change (py<0, cur>0) → annotate "sign change".
- **vs-Plan** — plan from `fact_gl_plan`/`fact_position_plan` (scenario `budget`→`forecast`→`plan`). `Var_abs = actual−plan`; `Var_pct = 100·(actual−plan)/|plan|`; `Coverage% = 100·actual/|plan|` (all `None`/flag when `|plan|<1e-6`). Example: 500 vs 450 → +50, +11.1%, 111.1%. Revenue `FAV+`; cost `FAV−`. Missing plan → mark "no plan", do not show −100%.
- **Recent-months alerting** — for last **N=3 months** × {revenue, gross margin} per entity, flag month `m` if any trigger fires: T1 vs prior-year month `|Δ|/|x_py| ≥ 0.20`; T2 vs plan `|Δ|/|plan| ≥ 0.10`; T3 z-score `|z| ≥ 2.0` (sample std ddof=1 over trailing-12 excluding m, per `gl_outliers`). Severity = # triggers; direction = sign of `x−ref`. Skip a trigger when its denominator/σ is 0. Example: x=60, μ=100, σ=10, py=95, plan=90 → all three fire, severity 3, "down".

### Area 2 — Cash development & liquidity
- **Cash level (period-end)** — BS stock: `Cash[t] = Σ amount WHERE account ∈ CASH_SET AND posting_date ≤ month_end(t)`. CASH_SET = `dim_gl_account.level_3='Cash & cash equivalents'` (no hardcoded konto list). `FAV+`; overdraft may be negative (do not ABS). Example (cumulative): Jan 100, Feb 70, Mar 120 kEUR.
- **Cash available incl. "Zeitverkauf"** — interpret Zeitverkauf as **receivables realizable over time = AR net of an aging-based collectibility haircut**. `LiquidityAvailable = Cash + CollectibleAR − OutstandingAP`, `CollectibleAR = Σ_band AR_band·(1−haircut_band)`. Proposed haircut ladder tied to `AR_BANDS`: not_yet_due 0% · 1-30 0% · 31-60 10% · 61-90 25% · 91-180 50% · >180 100%. Example: AR bands 400/100/50/40/20/10 → CollectibleAR 585; Cash 120, AP 300 → Liquidity 405 kEUR.
- **⛔ IRON-RULE STOP-AND-ASK:** no doubtful-debt/haircut logic exists in the codebase. The ladder is a **proposal that changes a monetary result** and must be confirmed (or replaced by the client's provision policy) before implementation. Haircut must be a config table, not inline constants.

### Area 3 — Fixed assets (define now, build later)
- Source `fact_fixed_asset` (pass-through, **empty/not loaded**). `Additions = Σ additions_zugang`; `Disposals = Σ disposals_abgang`; `DepPeriod = Σ depreciation`; `ClosingCost_AHK = opening_cost_ahk + additions − disposals ± transfers_umbuchung` (F1); `AccumDep[fy] = AccumDep[fy-1] + DepPeriod − DepOnDisposals` (F2); `NBV_derived = ClosingCost − AccumDep`; `NBV_dev = NBV[fy] − NBV[fy-1]`.
- **⛔ IRON-RULE STOP-AND-ASK:** `transfers_umbuchung` sign convention and depreciation method are undecided in `docs/financial-logic.md` (F1/F2). Both change monetary results ⇒ confirm before coding. Until then surface `Σ nbv` pass-through only.

### Area 4 — Customer development
- **Biggest customers** — `Rev[cust, ytd|cm]` desc, top N (drop all-zero `abs>1e-6`). `FAV+`.
- **Largest increase** — `ΔYoY = Rev[cur] − Rev[py]` desc; new customer (py=0) → Δ = +cur, reported separately as "won".
- **Lost customers** — `Rev[prior_window] ≥ T_material AND Rev[current_window] ≤ ε`; defaults prior `[-24..-13]` vs current `[-12..-1]` months, `T=5 kEUR`, `ε=max(1 kEUR, 0.10·prior)`. `FAV−`. Example: prior 40, current 0 → lost; prior 3 → immaterial; current 6 → declining not lost.
- **Invoices/order size (transaction-number derivation)** — `InvoiceCount = COUNT(DISTINCT journal_entry_group_number)` (existing "# Invoices"); `AvgPerInvoice = Rev/InvoiceCount` (`None` if 0). Example: 500 kEUR over 4 distinct jegn → 125 kEUR/invoice.

### Area 5 — Supplier development (symmetric, AP side)
- **Most-delivering** — `Cost[supplier, ytd] = Σ cost/1000` desc.
- **Biggest cost increase** — `ΔYoY = Cost[cur]−Cost[py]` desc; `FAV−` (invert). New supplier (py=0) → "new spend".
- **Order-quantity development** — `PurchaseTxns = COUNT(DISTINCT journal_entry_group_number)` on AP/purchase lines; `AvgPerPurchase = Cost/PurchaseTxns` (`None` if 0). *Confirm `fact_com` carries jegn; else derive from GL creditor lines.*

### Area 7 — Driver / DuPont (≤5 headline findings)
- ROE = NetIncome/Equity = NetMargin × AssetTurnover × EquityMultiplier (`FAV+`); NetMargin(ROS)=NI/Rev; AssetTurnover=Rev/Assets; EquityMultiplier=Assets/Equity (leverage, neutral); ROCE = EBIT/(Assets−CurrentLiab) (`FAV+`).
- **DuPont identity reconciliation assertion:** `ROE == NetMargin·AssetTurnover·EquityMultiplier` within 1e-6 — lock in test. Example: NI 90 / Rev 1000 / Assets 2000 / Equity 800 / CurrLiab 500 / EBIT 120 → NM 9%, AT 0.50, EM 2.5, ROE 11.25% ✓; ROCE 8.0%. Denominators `<1e-6` or negative equity → `None` + warn.

### Area 8 — Working Capital
- Restate to match `compute_wc_kpis` exactly: `DSO = |TradeRec|·365/Rev_LTM` (`FAV−`); `DIO = |Inv|·365/COGS_LTM` (`FAV−`); `DPO = |TradePay|·365/COGS_LTM` (`FAV+`); `NWC = Σ raw-signed TWC+OWC`; `CCC = DSO + DIO − DPO` (`FAV−`). Denominator `≤1e-6 → 0.0`. Example: rec 5.0m, inv 4.0m, pay 3.0m, owc 0.5m, Rev_LTM 20m, COGS_LTM 12m → NWC +6.5m, DSO 91.2, DIO 121.7, DPO 91.2, CCC 121.7 days.
- **Deep-dives:** `Level[l3,cm]` (raw stock) + `Δmonth = cm−pm`, `Δfy = fy−fy_py` for Inventories / Trade receivables / Trade payables. Trend basis = last 12 month-ends.

**Every metric ships with formula + worked example + edge cases + a named future test** (per `docs/financial-logic.md`). Test files enumerated per area in the specialist output (e.g. `backend/tests/test_overview_yoy.py`, `test_liquidity_available.py`, `test_wc_overview_kpis.py`, `test_dupont_drivers.py`, `test_customer_development.py`, `test_recent_month_alerts.py`).

---

## 3. Data sourcing (data-transformation-engineer)

**Confirmed primitives:**
- **Transaction number = `journal_entry_group_number`** (VARCHAR(12) = entity_prefix(2)⊕10-digit journal no) on GL; carried on OPOS as `gobd_transaktionsnr`.
- `fact_gl_line` (amount signed, `account_number_group`, `customer_id`, `supplier_id`, generated `entity_prefix`) + `fact_gl_entry` (`fiscal_period`, `entry_type`, `posting_date`) + `dim_gl_account` (`level_0..4`, `is_ic`) + `dim_gl_na` (`l6_na_mapping` ∈ FA/TWC/OWC) + `dim_gl_cf` (`cf_mapping`).
- Derived facts (`etl/derive_facts_sql.py`, classified by hardcoded `level_3` sets): `fact_sales` (gross_sales=−amount), `fact_com` (cost_of_materials=+amount), `fact_ar`/`fact_ap`. **These carry `posting_date` but NOT `fiscal_period`** → posting_date windows only.
- Dims: `dim_customer`/`dim_supplier` (name, `country_code`, `region_code`), geo snowflake `dim_region`/`dim_country`.
- Plan: `fact_gl_plan`, `fact_position_plan`, `fact_sales_plan`, `fact_com_plan` (scenarios `actual|plan|forecast|budget`).
- OPOS: `fact_opos_debitor`/`fact_opos_kreditor` (`partner_key`, `konto`, `belegart`, `net_due_date`, `amount_hauswaehrung` signed, `gobd_transaktionsnr`). Trade scope = explicit konto allow-lists (`AR_LUL_KONTOS`, `AP_LUL_KONTOS`), not prefix.
- Fixed assets `fact_fixed_asset` (0022, **EMPTY**), linkable via `entity_prefix` + `GoBD_Anlagennr`.

**Canonical patterns:** GL 3-way join + `dim_gl_na`/`dim_gl_cf` for WC/CF; entity filter via `resolve_entity_prefix()`; **entity visibility via `entity_visibility.visible_entity_codes()` (fail-closed) must be injected into every Overview query** — today only FDD GL endpoints honor it (tenant-isolation gap).

**Transaction-number derivations (explicit + caveats):**
- Invoice/order count = `COUNT(DISTINCT journal_entry_group_number)` on `fact_sales` (revenue side already isolated by `level_3='Net sales'` — do NOT count on raw `fact_gl_line` or VAT/AR lines inflate it). AvgPerInvoice = `SUM(gross_sales)/count`.
- One jegn spanning multiple revenue lines (19%+7% split) = one invoice (DISTINCT handles it). No-partner cash sales → `customer_id IS NULL` → "(no partner)" bucket. jegn is entity-prefixed → no cross-entity collision. AP side symmetric on `fact_com` (confirm jegn present).

**Aging → collectible/outstanding (`opos_aging.py`):** per-partner Method-A net-open, FIFO oldest-first across RV/RG invoices, due = `COALESCE(net_due_date, posting_date+terms)` (AR 30d/AP 45d), canonical `AR_BANDS` from `gl_aging.py` (reuse, never invent). CollectibleAR = positive net-open (credit balances → 0, flagged). Open-doc count = `COUNT(DISTINCT beleg_no) WHERE belegart IN ('RV','RG')`.

**Churn (`sales_analytics_compat.build_churn_bridge`):** FULL OUTER JOIN prior vs current `fact_sales` groups; lost = `pm NOT NULL AND cm NULL`, new = `pm NULL AND cm NOT NULL`, retained delta = both non-null.

**Fixed assets (blocked-on-load):** take target columns from `0022_draft_fixed_asset_register.py` (authorized source of truth). **Do not read the real client `anlagengitter.xlsx`** — protected real-customer path. Surface "module unavailable until register loaded".

---

## 4. Performance strategy

Root cause: `useOverviewBriefing` + every block independently fire `bal_mov` fin-compat BS/WC scans over ~1.3M rows, unbounded and in parallel → contention/deadlock.

| Item | Approach | Effort |
|---|---|---|
| **ONE batched summary endpoint** | New `GET /api/v1/financials/overview/summary` (8011) returns hero KPIs + WC + cash/liquidity headline + top-customer/supplier deltas + DuPont finding inputs + recent-months alerts in one round-trip, replacing ~6 client calls. Biggest single win. | **L** (backend) + **M** (frontend hook) |
| **Server-side pre-aggregation / snapshot** | Materialized `mart_overview_period` keyed `(entity_prefix, fiscal_year, fiscal_period, level_3)` for P&L sums + `(entity, cutoff, level_3)` BS balances, refreshed on ingest. Collapses Areas 1/2/6 to index lookups. No materialized report tables exist today — main scaling lever. | **L** |
| **Cache keyed by period+entity** | Short-TTL cache on the summary endpoint keyed `period+entity+plan_version`. | **S–M** |
| **Bounded concurrency** | Cap any residual parallel client calls (p-limit); server-side serialize the heavy BS/WC query behind the snapshot; always pass an entity/visibility filter (never scan all entities). | **S** |
| **Skeleton / lazy legacy** | Hero + findings paint from the summary payload first; heavy legacy section (GroupTile/EBIT/full DuPont) `React.lazy` on expand; reuse `AnalyticsPageShell` + `ChartLoadReporter` skeletons. | **S–M** |

Also: defer the 12-point OPOS aging trend (`opos_aging._trend` loops `_partner_view` up to 12×) to drill-down; show only the anchor Stichtag on Overview. Assert derived-fact coverage (`preflight_account_counts`) and fail loud rather than render zeros if a `level_3` label rename silently empties `fact_sales/com`.

---

## 5. reporting-v2 mode-gating (byte-identical guarantee)

**New single flag** — `frontend/src/lib/overviewV2Mode.ts` (mirrors `dataUpdateMode.ts`, does NOT reuse the v4 flag):
```ts
// TODO(un-gate after 5177 acceptance): remove this flag and inline OverviewPageV2 as the default.
export const IS_OVERVIEW_V2 = import.meta.env.MODE === 'reporting-v2';
```

**Branch** — one line at the top of the existing `OverviewPage` in `frontend/src/pages/OverviewPage.tsx`:
```ts
export default function OverviewPage() {
  if (IS_OVERVIEW_V2) return <OverviewPageV2 />   // reporting-v2 (5177/8011) only
  // ...existing body UNCHANGED below...
}
```
`App.tsx` routing untouched — same `<OverviewPage />` element for all modes; all new IA lives in `OverviewPageV2` + `overview-v2/`.

**Why byte-identical off-5177:** `import.meta.env.MODE` is statically inlined by Vite at build time. In the `v4` (5178), `fdd-merge` (5176), and default/8010 bundles, `IS_OVERVIEW_V2` folds to `false`, the branch is dead-code-eliminated, and the entire `OverviewPageV2` / `overview-v2/` import graph (plus the new summary endpoint calls) is tree-shaken out. No runtime branch, no bundle bloat.

**Untouched:** existing `OverviewPage` body, all `financials/overview/` + `cockpit/` components as consumed today, `App.tsx`, `vite.config.ts` (mode already maps 5177→8011), API 8010 responses, the `v4` flag. New backend endpoints are **additive on 8011 only** — no change to existing `/financials/overview` responses.

**Un-gate point:** after 5177 acceptance, delete `overviewV2Mode.ts`, promote `OverviewPageV2` to default, drop the legacy body (clean greppable removal, matching the `TODO(remove after 5177 acceptance)` convention already in `dataUpdateMode.ts`).

---

## 6. Component inventory

**Existing — verdict:**

| Component (`frontend/src/components/`) | Verdict | Note |
|---|---|---|
| `financials/overview/OverviewKpiStrip.tsx` | REUSE → hero | via `buildKpiStripItems` |
| `financials/overview/OverviewLeadCard.tsx` | REUSE (demote) | into findings/lower |
| `financials/overview/useOverviewBriefing.ts` | REUSE→replace | superseded by `useOverviewSummaryV2` (Phase 3) |
| `financials/overview/overviewBriefingUtils.ts` | REUSE | `computeCccFromSeries`, margin builders |
| `financials/overview/OverviewInsightsSection.tsx` | REUSE (relocate) | → Blocks (4)/(1) |
| `financials/overview/OverviewMarketWatch.tsx` | MOVE DOWN | legacy Top customers/suppliers |
| `financials/overview/OverviewGroupTile.tsx` | MOVE DOWN | legacy Group overview |
| `cockpit/DuPontTree.tsx` | MOVE DOWN + shrink | full tree only in legacy section |
| `cockpit/dupontNarrativeEngine.ts` | REUSE (core of req 7) | add `buildDuPontFindings(data,{max:3})` → `{severity,text,route}[]` |
| `cockpit/TopCustomerTable.tsx`, `TopSupplierTable.tsx` | MOVE DOWN | legacy section |
| `cockpit/DrillDownTable.tsx` | REUSE | from legacy group tile |
| `financials/AnomaliesPanel.tsx` | REUSE (promote) | compact mode → hero alert rail |
| `cockpit/EbitTable.tsx`, `ebitColumnRegistry.ts`, `EbitColumnEditor.tsx` | REUSE in place | inside GroupTile (legacy) |
| `cockpit/TopEntityTable.tsx`, `topEntitiesReportInsights.ts` | REUSE | customer/supplier findings |
| `cockpit/KpiCard.tsx` | REUSE | hero card primitive |

**New frontend:** `pages/OverviewPageV2.tsx`; `components/financials/overview-v2/` → `OverviewHeroStrip.tsx`, `OverviewFindingsFeed.tsx`, `OverviewAnalysisBlock.tsx` (generic shell), `blocks/{PerformanceBlock,CashLiquidityBlock,WorkingCapitalBlock,DriverFindingsBlock,CustomerBlock,SupplierBlock,FixedAssetsPlaceholder}.tsx`, `LegacyDetailSection.tsx`, `findingsModel.ts` (`OverviewFinding` type + route constants); `lib/overviewV2Mode.ts`; `hooks/useOverviewSummaryV2` (Phase 3).

**New/changed backend on 8011 (additive):** `GET /api/v1/financials/overview/summary` (batched — hero + all block inputs + alerts); optional `GET /api/v1/financials/overview/alerts` split; later-phase `GET /api/v1/financials/fixed-assets/summary`. All must honor `visible_entity_codes()`.

---

## 7. Phased delivery plan

| Phase | Scope | Size | Depends on |
|---|---|---|---|
| **P0 — Scaffold & gate** | `overviewV2Mode.ts`, `OverviewPageV2.tsx` (renders current tree verbatim), one-line branch, `overview-v2/` dir + `findingsModel.ts`. Proof: 5177 looks identical, 5176/5178 tree-shaken (build check). | **S** | none |
| **P1 — Hero + findings + IA reshuffle** | `OverviewHeroStrip`, `OverviewFindingsFeed`, `LegacyDetailSection` (move GroupTile+MarketWatch+full DuPontTree down, collapsed), alert rail from `AnomaliesPanel`. Uses existing endpoints. First real UX for review. | **M** | P0 |
| **P2 — DuPont findings shrink (7) + WC block (8)** | `buildDuPontFindings` (routes only, no formula change — finance-calc reviews), `DriverFindingsBlock`, `WorkingCapitalBlock` (reuse `wcRatios`/CCC). | **M** | P1 |
| **P3 — Batched endpoint + perf hardening** | `GET /financials/overview/summary` on 8011, `mart_overview_period` snapshot, cache, `useOverviewSummaryV2`, lazy legacy, skeletons. Biggest slice. **security-privacy-reviewer must verify `role_entity_visibility` on the new endpoint.** | **L** | backend + data + finance-calc |
| **P4 — Customer (4) + Supplier (5) blocks** | `CustomerBlock`/`SupplierBlock` (reuse `salesTopEntities` + aging + churn); findings → `/working-capital` + `/income-statement`. | **M** | P1 (benefits from P3) |
| **P5 — Cash & liquidity block (2)** | `CashLiquidityBlock` → `/cash-flow`. **Blocked on haircut-ladder sign-off.** | **M** | haircut approval |
| **P6 (LATER) — Fixed-asset register (3)** | Load `fact_fixed_asset` (approved migration + ingest), real block + `/fixed-assets/summary`. **Blocked on transfer-sign + depreciation-method sign-off.** Placeholder ships in P1. | **L** | register load + F1/F2 approval |

**Ships first for review:** P0 (gate proof) then P1 (visible new UX on existing data). Financial-heavy work (P3/P5/P6) gated behind formula sign-off + `financial-calculation-engineer` + `security-privacy-reviewer`.

---

## 8. Open questions / assumptions to confirm before build

1. **Sales/Aging deep-link target** — no standalone route exists; assume `/working-capital` + `/income-statement`, or is a new `/sales` planned?
2. **AR collectibility haircut ("Zeitverkauf")** — ⛔ approve the ladder (or supply client provision policy) before P5. Net-new monetary logic, no precedent.
3. **Fixed-asset transfer sign + depreciation method** — ⛔ undecided in `docs/financial-logic.md` (F1/F2); approve before P6.
4. **Recent-months alert thresholds** — confirm defaults (N=3, YoY 20%, plan 10%, z ≥ 2.0).
5. **Lost-customer windows/thresholds** — confirm `[-24..-13]` vs `[-12..-1]`, `T=5 kEUR`, `ε=max(1 kEUR, 10%)`.
6. **≤N finding counts** — hero alert rail ≤5, DuPont findings ≤3?
7. **`fact_com` transaction number** — confirm `journal_entry_group_number` present for the AP-side avg-per-invoice, else derive from GL creditor lines.
8. **Endpoint ownership split** — backend-engineer owns endpoint shell, financial-calculation-engineer owns KPI math (per hard rule 1).
9. **Tenant isolation** — new 8011 endpoints must apply `visible_entity_codes()`; route to security-privacy-reviewer (L2 gate) before merge.

---

## 9. Explicitly untouched

- **Port 5178 (`v4`)**, **Port 5176 (`fdd-merge`)**, **live API 8010** — guaranteed byte-identical: the new flag folds to `false` in those bundles and the entire v2 subtree is tree-shaken (§5). No process restarts, no route changes, no shared-state edits.
- Existing `OverviewPage` body, `financials/overview/*`, `cockpit/*` as consumed today, `App.tsx`, `vite.config.ts`, the `IS_DATA_UPDATE_V4` flag.
- New backend endpoints are additive on 8011 only — existing `/financials/overview` responses unchanged.
