# Overview-Page Improvements — Implementation Plan (reporting-v2 / Port 5177)

> **Status:** PLAN ONLY. No production code has been edited. This document is the sole deliverable.
> **Scope guardrails:** all changes land on the **reporting-v2** stack only (frontend mode `reporting-v2` → port **5177**, API **8011**, DB `finssentials_v2`). **Do NOT touch** 5178/v4 (8012), 5176/fdd-merge (8010), or the default 5174/8008 stack. Isolation is proven in §11.
> **Authors:** software-architect (lead), financial-calculation-engineer (items 3+4 narratives), data-transformation-engineer (items 5+6 data). Grounded by reading the live code/data on 2026-06-27.

---

## 0. Two corrections to the brief (read first)

Grounding surfaced two premises that do not match the repo. Both change the effort profile and are reflected throughout:

1. **The Plan/Budget "Phase 5" spec does not exist.** There is **no** `.claude/plans/abundant-noodling-river.md` (the `.claude/plans/` dir has no `.md` files), and **no** `build_plan_outlook` / `PlanOutlookBlock` anywhere in the repo. `docs/overview-v2-redesign-plan.md` is phased P0–P6, and **its "P5" = the Cash & Liquidity block**, not a planning-outlook card. So item 8 must be **authored fresh** (design below in §9). The *reusable backend* it would build on (`build_statement_plan_response`, `load_position_plan_map`, and the `/…/plan` endpoints) **does exist and is confirmed** — that half of the premise is correct.

2. **The subledger loaders + tables already exist and already target `finssentials_v2`.** `load_fixed_asset_subledger.py` → `fact_fixed_asset` (migrations `0022`/`0027`/`0028`) and `load_personnel_subledger.py` → `fact_personnel_employee` (migration `0025`) are **already built**. Items 5+6 are therefore **"run + reconcile + build the Overview block,"** not "write a loader from scratch." A few mapping gaps are flagged in §7/§8.

---

## 1. Executive summary

Eight improvements to the reporting-v2 Overview page (`frontend/src/pages/OverviewPageV2.tsx`), grouped into four phases. The dominant theme: the page currently fires several heavy live GL scans per load and surfaces narratives that restate charts. We fix latency by **enabling + populating the already-built `mart_overview_period` snapshot** and reducing round-trips; we upgrade the DuPont and partner narratives to **deterministic, causal, golden-testable** engines; we convert deep-link buttons into **in-page accordions**; we **remove the anomalies panel** (which also kills a live 422 bug); we surface the **already-loaded** fixed-asset and payroll subledgers as new blocks; and we author the **plan-outlook card** fresh on top of the confirmed plan-endpoint backend.

| # | Improvement | Tier | Net-new work | Phase |
|---|-------------|------|--------------|-------|
| 7 | Remove anomalies panel (fixes 422) | L0/L1 | Delete panel + params | **P0** |
| 1 | Speed up Overview (enable mart, fewer round-trips) | L2 | Populate/refresh mart, batch endpoint | **P1** |
| 2 | "Detailed tables/charts" → inline accordion | L1 | Convert 7 deep-links to collapsibles | **P1** |
| 3 | DuPont narrative (Shapley causal decomposition) | L2 | New attribution core + sentences | **P2** |
| 4 | Customer/Supplier narrative process | L2 | New insight builder + 2 backend fields | **P2** |
| 5 | Fixed-assets block (load + register) | L2 | Run loader, new block + endpoint | **P3** |
| 6 | Payroll block (high-level) | L1/L2 | Run loader, compact block + endpoint | **P3** |
| 8 | Plan-outlook card (authored fresh) | L2 | `build_plan_outlook` + endpoint + block | **P4** |

Expected headline latency win (item 1): cold `/overview/summary` from **multi-second live compute over ~1.3M rows** down to an **indexed mart read (sub-second for the EBIT hero)**, plus one fewer network round-trip by batching partners+liquidity. See §5 for the measurement protocol.

---

## 2. Current architecture (verified map)

**Page:** `frontend/src/pages/OverviewPageV2.tsx` (351 lines). Gated to reporting-v2 by `frontend/src/lib/overviewV2Mode.ts` (`IS_OVERVIEW_V2 = import.meta.env.MODE === 'reporting-v2'`), branched at `frontend/src/pages/OverviewPage.tsx:66-67`.

**Render tree** (inside `AnalyticsPageShell`): header → boot-error banner → `CollapsibleModuleFiltersCard` → `OverviewHeroStrip` → **`AnomaliesPanel`** (compact rail, L253-258) → `OverviewFindingsFeed` → 8-block grid (`PerformanceBlock`, `CashLiquidityBlock`, `WorkingCapitalBlock`, `DriverFindingsBlock`, `CustomerBlock`, `SupplierBlock`, `FixedAssetsPlaceholder`) → `LegacyDetailSection` (lazy).

**Hooks** (in `frontend/src/components/financials/overview-v2/hooks/`): `useOverviewSummaryV2.ts` → `GET /overview/summary`; `useOverviewPartnersV2.ts` → `/overview/partners`; `useOverviewLiquidityV2.ts` → `/overview/liquidity`. Three independent fetches.

**Backend router:** all three endpoints live in `backend/app/routers/financials_compat.py` (summary L1519-1564, partners L1606-1676, liquidity L1705-1753), each `Depends(get_read_session)`. They delegate to **services** (not routers) under `backend/app/services/`: `overview_summary.py`, `partner_development.py`, `liquidity.py`, `mart_overview.py`.

**Read-session guard:** `backend/app/db.py:35-49` — `get_read_session()` sets `SET LOCAL statement_timeout` (`read_statement_timeout_ms` default **90000**, `config.py:40`) and `max_parallel_workers_per_gather = 0` (L46), scoped per request txn.

**Narrative engines:** `frontend/src/components/cockpit/dupontNarrativeEngine.ts` (consumed by `DuPontTree.tsx:655`), `topEntitiesReportInsights.ts` (consumed by `TopCustomerTable.tsx`/`TopSupplierTable.tsx`), and the v2 primary `frontend/src/components/financials/overview-v2/findingsBuilders.ts` (`buildFindingsFromSummary`, called at `OverviewPageV2.tsx:177`).

---

## 3. Item 7 — Remove anomalies from the Overview (P0, do first)

**Why first:** it is the smallest change, it removes a live **422 bug**, and it de-clutters the page before the redesign.

**The 422 bug (confirmed):** `OverviewPageV2.tsx:168-173` builds `anomalyParams`; the `'year'` branch (L172) sends `{ period_grain:'year', year }` **without `month`**, but the backend requires both → `"period_grain='year' requires year and month"`. Removing the panel removes the offending call entirely.

**Exact deletions in `OverviewPageV2.tsx` (v2 only):**
- Imports L32 (`import AnomaliesPanel from '../components/financials/AnomaliesPanel'`) and L33 (`type AnomalyPeriodParams`).
- `anomalyParams` computation L168-173.
- Render block L253-258.

**Do NOT touch:** `frontend/src/components/financials/AnomaliesPanel.tsx` itself (still used by the legacy `OverviewPage.tsx` L244-249 and by the anomaly-detection pages `components/anomaly/*`, `components/financials/{ForensicTab,SeasonalityTab,OutliersTab}.tsx`).

**Findings-feed anomaly references** (decide via OPEN QUESTION Q1): `findingsBuilders.ts:253-262` (`summary-alerts` finding, `route:'/anomaly-detection'`) and the `anomaly-count` slot at L177-184. These do **not** import the panel — they read `summary.alerts`. Default recommendation: **keep the alerts finding but drop its `route`** (consistent with the "no navigate hints" rule in items 3+4), so anomalies are de-featured but a one-line signal survives. Full removal is also viable.

**Effort:** ~0.5 day. **Tier L1** (touches page wiring, no formula).

---

## 4. Item 2 — "Detailed tables and charts" → inline accordion (P1)

**Finding:** the literal **"Detailed tables and charts"** text is already an expand-in-place accordion — `frontend/src/components/financials/overview-v2/LegacyDetailSection.tsx:113-147` (`useState(open)` + `ChevronDown/Right`, body mounts only when open at L144). No change needed there; it is the **reuse template**.

**The actual navigations to convert** are the **"View detail →"** buttons rendered by the shared shell `frontend/src/components/financials/overview-v2/OverviewAnalysisBlock.tsx:76-86` (`onClick={() => navigate(deepLink)}`). Each block passes a `deepLink`:

| Block | file:line | current `deepLink` |
|-------|-----------|--------------------|
| `PerformanceBlock` | `blocks/PerformanceBlock.tsx:9` | `/income-statement` |
| `CashLiquidityBlock` | `blocks/CashLiquidityBlock.tsx:381` | `/cash-flow` |
| `WorkingCapitalBlock` | `blocks/WorkingCapitalBlock.tsx:508` | `/working-capital` |
| `DriverFindingsBlock` | `blocks/DriverFindingsBlock.tsx:172` | `/income-statement` |
| `CustomerBlock` | `blocks/CustomerBlock.tsx:201` | `/income-statement` |
| `SupplierBlock` | `blocks/SupplierBlock.tsx:222` | `/income-statement` |
| `FixedAssetsPlaceholder` | `blocks/FixedAssetsPlaceholder.tsx:12` | `/balance-sheet` |

**Approach:**
1. Extract the `LegacyDetailSection` open/toggle pattern into a small reusable primitive `frontend/src/components/financials/overview-v2/InlineDetailSection.tsx` (`{title, defaultOpen?, children}`) — no generic `Accordion` exists in the codebase, so this becomes the shared one (checked: only bespoke collapsibles exist).
2. Change `OverviewAnalysisBlock` to accept an optional `detail?: ReactNode`. When present, replace the `navigate(deepLink)` button (L76-86) with an inline `<InlineDetailSection>` that renders `detail` **on the same page** (accordion), lazy-mounted so heavy tables/charts only fetch/render on expand.
3. Each block supplies its `detail` region (the deep tables/charts it used to navigate to). To avoid duplicating the full statement pages, the detail region reuses the same data the block already holds plus, where a fuller table is wanted, a scoped fetch fired **only on expand** (`enabled: open`).
4. **Also remove the per-finding-chip navigations** (`navigate(route)` in `CustomerBlock` FindingChip L103, `SupplierBlock` L127, `WorkingCapitalBlock` L211, `CashLiquidityBlock` L307, `DriverFindingsBlock` L63, `OverviewFindingsFeed` L36) per the "no navigate hints" requirement — chips become non-navigating emphasis, or expand the same inline detail. (Coordinate with items 3+4, which also strip route hints.)

**Effort:** ~2–3 days (one shared primitive + 7 block detail regions; the FA/payroll blocks in P3 will supply their own detail region natively). **Tier L1** per block; the shared-primitive extraction is L1.

---

## 5. Item 1 — Speed up the Overview analyses (P1)

### 5.1 Root cause (confirmed)
`/overview/summary` runs **live builders sequentially** on the sync read session on every cache miss (`overview_summary.py` docstring L43-51). The heavy sub-queries: `build_wc_statement_compat`/`build_wc_snapshot_annual` (BS/WC `bal_mov` cumulative scans, L403-409), `build_ebit_table` (L217), inline `_cash_headline` BS-cumulative scan (L234-251), `build_top_entities`×2 (L439-446), `build_dupont` (L449), `build_recent_month_alerts` (L452-455). `docs/overview-v2-redesign-plan.md:161` documents the ~1.3M-row figure. Partners and liquidity are **two additional independent heavy fetches**.

The materialized snapshot `mart_overview_period` (migration `0026`, service `mart_overview.py`) exists but is **DEFAULT-OFF** (`settings.overview_summary_use_mart = False`, `config.py:95-97`) and, critically, is **populated lazily and by nothing today** — there is **no refresh endpoint or script**, so the tables are empty. Only the EBIT/revenue hero is mart-accelerated when on (`overview_summary.py:202-218`); WC/cash/top-entities/DuPont/alerts always run live.

### 5.2 Levers (in priority order)

**Lever A — Populate + enable the mart (biggest win, EBIT hero).**
- Add a **refresh entry point**. `mart_overview.refresh_mart_overview_period(session, allowed_entities, project_id)` already exists (L338-403, idempotent delete+insert, stamps `source_load_id`). It just has no caller. Add **either** (recommended) a management script `backend/scripts/refresh_mart_overview.py` (mirrors the loader-script pattern, runs post-ingest), **or** an admin endpoint `POST /overview/mart/refresh` guarded by admin auth. Recommend the script + wiring it into the ingest completion path so the mart is always fresh after a GL load (see OPEN QUESTION Q5 on cadence).
- Flip `OVERVIEW_SUMMARY_USE_MART=true` **for the v2 stack only** (env on the 8011 process; default stays `False` so 8008/8010/8012 are untouched).
- **Validate equivalence** before trusting it: `backend/tests/test_mart_overview_equivalence.py` already exists (referenced `overview_summary.py:24`) — extend it to assert mart EBIT rows == live `build_ebit_table` rows for ≥2 entities × ≥2 periods, within 0.01 kEUR. Freshness fallback is already built (`mart_is_fresh` L319-335 → falls back to live builder when stale), so a stale mart degrades safely rather than lying.

**Lever B — Extend the mart to WC + cash (second-order win).**
- `mart_overview_period` already carries a BS-movement table (`mart_overview_bs_balance`, `0026` L92-110) via `_BS_MOVEMENT_SQL`/`cumulate_bs_movements`. The WC snapshot and `_cash_headline` are the next-heaviest live scans. **Phase-1.5 (optional):** route `build_wc_snapshot_annual` and the cash headline through the BS-balance mart. Requires an equivalence test per metric (financial-calculation-engineer sign-off, since WC/cash carry sign conventions). Gated behind Q5.

**Lever C — Reduce round-trips (batch partners + liquidity).**
- Today: 3 fetches (`summary`, `partners`, `liquidity`). Add a **single batched endpoint** `GET /overview/bundle` in `financials_compat.py` that returns `{summary, partners, liquidity}` computed on one read session (one visibility resolution, one txn), and a hook `useOverviewBundleV2`. This removes 2 network round-trips and repeated auth/visibility overhead. Keep the three granular endpoints for the accordion lazy-loads. **Alternative (lower effort):** keep three endpoints but fire them in parallel from the client (they already are separate hooks) and add HTTP caching headers — but the batch is the cleaner latency win.

**Lever D — Caching + skeletons.**
- The summary already has a TTL cache (`overview_summary_cache_ttl_s` default 45s, `config.py:100-102`). Extend the same short-TTL cache to the new bundle. Ensure the cache key includes entity-visibility scope (tenant-safe) — verify against `allowed_entities=eff`.
- Frontend: render **per-block skeletons** immediately (each block already receives `loading`); the hero + findings should paint from cache instantly while blocks stream. This is perceived-latency, not compute.

### 5.3 Measurement protocol (define "expected improvement")
Before/after, on the v2 stack (8011, `finssentials_v2`), measure with the read guard in place:
1. Cold `/overview/summary` (cache cleared, mart OFF) — baseline.
2. Cold `/overview/summary` with mart ON + populated — target: EBIT-hero portion drops from a live multi-join scan to an indexed lookup on `mart_overview_period` (unique key `(project_id, entity_prefix, fiscal_year, fiscal_period, level_3)`), expected **sub-second** for that portion; total summary bounded by the remaining live WC/cash scans until Lever B.
3. `/overview/bundle` vs 3× granular — target: **1 round-trip, ~1 txn setup** instead of 3.
Record p50/p95 in the PR description. **No hard SLA is claimed here** — the plan commits to the *method*; the number is produced by the measurement, not asserted.

**Effort:** Lever A ~2 days (script + enable + equivalence test); Lever C ~1.5 days (batch endpoint + hook); Lever B optional ~2–3 days (needs financial sign-off). **Tier L2** (mart correctness = financial equivalence).

---

## 6. Item 3 — DuPont "Performance Overview" narrative (P2) — financial-calculation-engineer

### 6.1 Identity
Three-factor DuPont, matching the exact keys present in `DuPontData.metrics` (`frontend/src/lib/api.ts:854`, each metric `{value, py, pm}`):
```
ROE = ROS × AT × EM
  ROS = Net income / Net sales        (metrics.ros)
  AT  = Net sales / Total assets      (metrics.asset_turnover)
  EM  = Total assets / Equity         (metrics.equity_multiplier)
  ROI = ROS × AT = Net income/Assets  (metrics.roi)   ← intermediate, preserved
```

### 6.2 Attribution — Shapley finite-change decomposition (PRIMARY)
For `F = x·y·z` moving from baseline `0` to current `1`, each factor's exact contribution to `ΔF = F₁−F₀` (closed form, n=3):
```
C_x = Δx · [ (1/3)·y₀z₀ + (1/6)·(y₁z₀ + y₀z₁) + (1/3)·y₁z₁ ]
C_y = Δy · [ (1/3)·x₀z₀ + (1/6)·(x₁z₀ + x₀z₁) + (1/3)·x₁z₁ ]
C_z = Δz · [ (1/3)·x₀y₀ + (1/6)·(x₁y₀ + x₀y₁) + (1/3)·x₁y₁ ]
     (x=ROS, y=AT, z=EM)
INVARIANT: C_ROS + C_AT + C_EM = ROE₁ − ROE₀   (assert < 1e-9)
```
Contributions are already in ROE units → report in **pp** (×100). **Why Shapley, not LMDI/sequential:** order-independent AND defined for zero/negative/sign-flipping factors — essential for loss years where ROS and ROE go negative. This removes the current engine's positivity guard entirely. Run three passes: baseline = `pm` (period move), `py` (YoY), and `plan` (when plan factors supplied). LMDI is documented as an equivalent alternative only when all levels are strictly positive.

### 6.3 Ranking + thresholds
Rank by `|C_factor|` desc. Mention a factor iff `|C_factor| ≥ 0.30 pp` **AND** `|C_factor|/|ΔROE| ≥ 0.15`. Mention top 1–3. If `|ΔROE| < 0.30 pp` → single "broadly stable" verdict, stop.

**Entity/region attribution** requires a companion input `entityFactors?: Array<{entity, ros, at, em, roe}>` (per-entity DuPont slice) — **not present in `DuPontData` today**. For the dominant factor `f*`: `contribᵉ = weightᵉ·(fᵉ*₁ − fᵉ*₀)`, `weightᵉ = Net_salesᵉ/ΣNet_sales`; report argmax entity if `entity_share ≥ 0.25`. **Graceful degrade:** if `entityFactors` absent, drop the entity sentence (no fabrication). Enabling backend task: **add a per-entity DuPont slice to the summary payload** (flagged as a dependency, see Q6).

Sub-factor drill (reuses existing `rankPeriodDrivers`): dominant ROS → largest |Δ| of `net_sales`/`cost_of_materials`/`personnel_expenses`; dominant AT → largest |Δ| of `trade_receivables`/`inventories`/`trade_payables` + matching `dso/dio/dpo`; dominant EM → `equity` Δ.

### 6.4 Output ordering
1. **Verdict:** ROE level + direction + Δ vs pm (pp) + Δ vs py (pp).
2. **Biggest driver, with numbers:** dominant factor, its pp contribution, its own level change, one-line sub-factor cause; add 2nd factor if material.
3. **Entity/region** (only if `entityFactors` and share ≥ 0.25).
4. **Watch-item:** the factor moving *against* ROE, or "leverage propping up a weakening operation" when `C_EM` is the only positive contributor while `C_ROS`/`C_AT` are negative.

### 6.5 Worked example A (margin-led, baseline pm)
`ROS 5.0→6.0%`, `AT 1.20→1.22`, `EM 2.00→1.98`. ROE 12.00%→14.4936%, ΔROE **+2.4936 pp**. Shapley: `C_ROS=+2.408`, `C_AT=+0.219`, `C_EM=−0.133` → sum +2.494 ✓. Dominant ROS (96.5% share). Output:
> "Return on equity improved to 14.5% (+2.5 pp vs prior month). The gain is led by net margin, which added +2.4 pp as return on sales rose 5.0%→6.0%; capital efficiency added +0.2 pp (asset turnover 1.20→1.22). Financial leverage was the only factor working against ROE (equity multiplier 2.00→1.98, −0.1 pp)."

### 6.6 Worked example B (WC-led decline, sign-robust)
`ROS 8.0→7.6%`, `AT 1.50→1.30`, `EM 2.50→2.55`. ROE 30.00%→25.194%, ΔROE **−4.806 pp**. Shapley: `C_ROS=−1.414`, `C_AT=−3.939`, `C_EM=+0.546` → sum −4.807 ✓. Dominant AT (82%). Output:
> "Return on equity weakened to 25.2% (−4.8 pp vs prior month). The decline is driven by capital efficiency: asset turnover fell 1.50→1.30, subtracting −3.9 pp, on rising receivables days. Net margin removed a further −1.4 pp (8.0%→7.6%). Higher leverage (2.50→2.55) added back +0.5 pp — the sole positive contributor, propping up ROE while both operating factors deteriorated: a low-quality composition to watch."

### 6.7 Edge cases (golden test)
Loss year with `ROE₀>0, ROE₁<0` (Shapley still exact); `null` metric → skip that pass / headline-only; `ΔROE < θ` → stable verdict; missing plan factor → omit vs-plan clause; reconciliation assertion in every branch.

### 6.8 Plug-in point
`frontend/src/components/cockpit/dupontNarrativeEngine.ts`: add pure `attributeRoeChange(m, basis)` (the testable core); **replace** `roeBridgeSentence` with the ordered-sentence builder; keep `rankPeriodDrivers` but relabel as sub-factor cause and **strip the "Confirm in the general ledger…/Trace the movement…" tails** (L156-158, L193-194, L219-220, L233-235). Extend `buildDuPontNarrative` to accept optional `entityFactors`. Downstream `buildDuPontFindings` (`findingsBuilders.ts:247`) inherits the improved first sentence.

**Effort:** ~3 days incl. golden tests. **Tier L2** (formula change → financial-metric-test required).

---

## 7. Item 4 — Customer & Supplier narrative process (P2) — financial-calculation-engineer

### 7.1 Metric set + formulas (kEUR; magnitudes positive, no sign flip per `partner_development.py` docstrings)
Let `dev = build_customer_development(...)` / `build_supplier_development(...)`, `geo = SalesBreakdownResponse` (region via `l1`), `churn = SalesChurnBridgeResponse`.
```
portfolio_total_ytd = Σ over ALL partners of rev_ytd_keur      [*needs backend totals field]
top_share           = biggest[0].rev_ytd_keur / portfolio_total_ytd
NEW customer  = argmax over won[] of rev_cur_keur (won ⇔ rev_py≈0); qualify iff ≥ θ_material
LOST customer = lost[0] (sorted desc by rev_prior); loss = rev_prior − rev_current
GROWTH        = increase[0] (sorted desc); delta_yoy = rev_cur − rev_py
TICKET (avg)  = argmax over biggest[] of avg_per_invoice_keur = rev_ytd / invoice_count
                invoice_count = COUNT(DISTINCT journal_entry_group_number) YTD
TICKET (max)  = argmax over partners of max_ticket_keur          [*NEW backend metric, 7.2]
                max_ticket_keur = MAX over groups g of (Σ_{line∈g} gross_sales)/1000
REGION r      share_r = gs_cm_r / Σ gs_cm_r ; growth_r = (gs_cm_r−gs_pm_r)/|gs_pm_r| (guard |gs_pm|≥θ_region)
ENTITY e      same two formulas on entity dim (SalesTopEntity.entity)
CHURN (bridge k) opening=period_totals[k-1]; net = new+upsell+cross_sell−downsell−lost;
                 closing=period_totals[k]; assert |closing−(opening+net)|<1e-6; dominant=argmax|component|
```
**Supplier mirror** (`fav="minus"`, raw signed delta): biggest by `cost_ytd`; biggest cost increase by `delta_yoy` (unfavourable); new spend from `won[]`; avg per purchase = `cost_ytd/purchase_txns`. **No lost/churn on cost side** → those sentences skipped.

### 7.2 Two enabling backend additions
- **Development totals:** `dev.biggest` is only `top_n`, so `Σ biggest.rev_ytd` understates the book. Add `totals:{ ytd_keur, ytd_py_keur, partner_count }` to the development payload for exact `portfolio_total_ytd`/`top_share`.
- **`max_ticket_keur`:** add a group-level pre-agg (`GROUP BY customer_id, journal_entry_group_number → SUM(gross_sales)/1000`, then `MAX` per customer), attach per partner in `biggest[]`. Pure, golden-testable; sign already positive.

Both live in `backend/app/services/partner_development.py` (additive fields, do not change existing shapes).

### 7.3 Thresholds
`θ_material = 5.0 kEUR` (reuse `_T_MATERIAL_KEUR`); `θ_region = 10.0 kEUR` floor on PM base before % (replaces the bare `|base|<1e-6` guard); ticket-concentration flag: `avg_per_invoice ≥ 2 × median(avg_per_invoice over biggest)`.

### 7.4 Ordered sentence template (top-line → deep)
Customer: `top-line → NEW → LOST → GROWTH → TICKET → REGION → ENTITY → CHURN bridge`.
Supplier: `cost top-line → biggest supplier → biggest cost increase YoY → new spend → avg per purchase → REGION/ENTITY`.

### 7.5 Worked customer example (abridged)
portfolio 3,050; biggest Muster GmbH 820 (27%); new Neuhaus AG 140 (py 0); lost Altkunde KG 240→3 (−237); growth Wachstum SE +110 (300→410); ticket GrossDeal 68/invoice (median 22 → flag); region DACH leads, Nordics +18% (base 95≥θ); churn May→Jun opening 2900, +140 new, +40 upsell, +10 cross, −30 downsell, −250 lost, net −90 → 2810. Produces the 7 ordered sentences (e.g. "Largest lost customer: Altkunde KG, down from 240 kEUR to effectively nil (3 kEUR) — a 237 kEUR revenue loss, the biggest in the book.").

### 7.6 Worked supplier example (abridged)
cost 1,980; biggest Rohstoff AG 610 (31%, 33 txns, 18.5/purchase); biggest increase Energie GmbH +80 (140→220, +57%, unfavourable); new NeuLieferant KG 95 (py 0). Produces the 4 ordered sentences.

### 7.7 Edge cases (golden test)
`allowed_entities=set()` → empty lists → explicit "no data" sentence, no crash; empty won/lost/increase → skip with fallback text; `avg_per_invoice=None` → excluded from argmax (service returns None); region mover base < θ_region → leader only; churn missing prior entry → net only; supplier lost/churn slots omitted.

### 7.8 Plug-in point
New pure builder `frontend/src/components/financials/overview-v2/partnerDevelopmentInsights.ts` exporting `buildPartnerDevelopmentInsights({mode, development, geo, churn}) → {intro, bullets[]}` (the development shape differs from `SalesTopEntity`, so a sibling builder is cleaner than overloading `topEntitiesReportInsights.ts`). Reuse its `signed`/`pct` helpers with the `θ_region` floor. Wire the single highest-priority partner insight into `buildFindingsFromSummary` (`findingsBuilders.ts` ~L266) and **remove the `route:'/working-capital'` deep-link** on the top-customer finding (no-navigate rule).

**Effort:** ~4 days (2 backend fields + builder + goldens). **Tier L2**.

---

## 8. Items 5+6 — Fixed-assets & Payroll blocks (P3) — data-transformation-engineer

### 8.1 Loaders + tables ALREADY EXIST (target `finssentials_v2`)
- Fixed assets: `backend/scripts/load_fixed_asset_subledger.py` → `fact_fixed_asset` (migrations `0022_draft_fixed_asset_register.py`, `0027_fact_fixed_asset_as_of.py`, `0028_fact_fixed_asset_label.py`); mapping `backend/app/services/fixed_asset_ingest.py`.
- Payroll: `backend/scripts/load_personnel_subledger.py` → `fact_personnel_employee` (migration `0025_fact_personnel_employee.py`); mapping `backend/app/services/personnel_ingest.py`.
- Both mirror the OPOS pattern (`load_opos_subledger.py`): env-driven DSN (`DB_NAME=finssentials_v2`, `DB_PASSWORD`), `_db_safety.assert_not_live_db` guard (guards only against literal `Finssentials` — operator must set v2 explicitly), delete-by-key + chunked insert, entity linkage via `Entity`-name → `ENTITY_NAME_TO_PREFIX`. Idempotency key = `as_of_date = date(year,12,31)`.

**So items 5+6 are: (a) run the loaders against v2, (b) reconcile, (c) build the Overview blocks + read endpoints.** No new loader/migration required (one optional schema tweak below).

### 8.2 Source column structure (read from the actual xlsx — 2022 + 2024)
Files: `C:\Users\bened\OneDrive\Desktop\Finssentials - Setup\subledgers\{2022,2023,2024,2025}\{anlagengitter,personaltable}.xlsx` (2025 partial: stale `~$` lock + `.tmp`).

**anlagengitter.xlsx** — sheet `Anlagengitter <year>`, header row 1, 25 cols, ~1349 rows (2022) / ~1978 (2024). Linkage `Entity` + `GoBD_Anlagennr` (== `Anlage` in current data). Key columns: `Anlagenklasse` (asset class), `Aktivierung am` (cap. date), `AHK GJ-BEG` (opening cost), **`Zugang` (additions/purchases)**, **`Abgang` (disposals)**, `Umbuchung` (transfers), `aktuelle AHK` (closing cost), `Afa des Jahres` (current-yr depreciation), `kumulierte AfA` (accumulated dep.), `Buchwert GJ-Beg` (opening NBV), **`Lfd Buchwert` (closing NBV/Restbuchwert)**, `Entity`, `GoBD_Anlagennr`.

**personaltable.xlsx** — sheet `Personal <year>`, header row 1, 43 cols, ~394 rows (2022) / ~433 (2024). Linkage `Entity` + `Personalnummer`. Key columns: `Bereich`/`Bereichuntergruppe`, `Gew./Ang.` (blue/white collar), `Zugehörigkeit` (seniority **year** — hire-year proxy), `Beschäftigungsgrad` (employment %), monthly `Januar…Dezember` (1/0 flags), `Summe` (active-month count → headcount), `gehalt mon.` (monthly salary), `Grundgehalt`, `Gesamtsumme` (total personnel cost), `Entity`, `Personalnummer`. **No `Eintrittsdatum`/`Austrittsdatum`** — hire timing only via `Zugehörigkeit` (year); **no exit date at all → leaver analysis cannot be derived from this source** (constrains item 6, see Q3).

### 8.3 Mapping gaps to flag (as-built)
- Fixed assets: `GoBD_Anlagennr` is **not persisted** — `asset_id` stores `Anlage` (they coincide today). `kumulierte AfA`, `aktuelle AHK`, `Afa Abgang`, `AfA Umbuchung`, `Invest. Förderung`, `Währung`, `Quelle` **not mapped** (consistent with `0022`'s "no roll-forward, F1/F2 deferred" guarantee). `depreciation` = current-year AfA only.
- Payroll: `Objektkürzel`, `OrgEinheitenkurztext`, and the 12 monthly flags **not mapped** (only `Summe → months_active`).
- **Optional schema change (raise with architect + financial-engineer, L2):** if `GoBD_Anlagennr` must be the persisted asset key rather than `Anlage`, that touches identity → new migration + mapping change. Default: keep as-is (equivalent today).

### 8.4 Load + reconcile (run on v2 only)
1. `--dry-run` both loaders against `finssentials_v2`: expect ~1349/1978 assets, ~394/433 employees for 2022/2024.
2. Load, then reconcile:
   - **Fixed assets NBV roll-forward** per entity×year: `opening_nbv + additions_zugang − disposals_abgang − |depreciation| ± transfers ≈ nbv (Lfd Buchwert)`; cost roll-forward `opening_cost_ahk + Zugang − Abgang ± Umbuchung ≈ aktuelle AHK`. Tie entity sums to `subledgers/reconciliation.xlsx` (helpers `subledgers/build/anlagen_recon.py`, `bs_recon.py`).
   - **Payroll**: headcount = `COUNT(personalnummer)`, FTE = `SUM(beschaeftigungsgrad/100)`, cost = `SUM(gesamtsumme)` per entity×year tied to `reconciliation.xlsx`; assert `Personalnummer` uniqueness within (entity, as_of_date) (matches DB unique constraint).
   - Sign semantics (AfA negative in source, `Abgang` signs) are financial-calculation-engineer's call before any derived metric ships.

### 8.5 Fixed-assets Overview block (item 5)
New read endpoint `GET /overview/fixed-assets` in `financials_compat.py` (visibility-filtered, `get_read_session`) returning per-entity/period: **additions (Σ Zugang), disposals (Σ Abgang), opening/closing NBV, current-year depreciation, register rows**. New block `frontend/src/components/financials/overview-v2/blocks/FixedAssetsBlock.tsx` **replacing** the current `FixedAssetsPlaceholder` — top-line KPIs (purchases, disposals, net FA development) with the **register table in the inline accordion detail** (§4). Depth per Q2.

### 8.6 Payroll Overview block (item 6, high-level)
New read endpoint `GET /overview/payroll` returning per-entity/period: **headcount (`COUNT` / `SUM(months_active)`), FTE (`SUM(beschaeftigungsgrad/100)`), total personnel cost (`SUM(gesamtsumme)`), avg salary (`SUM(gesamtsumme)/headcount` or `AVG(gehalt_mon)`), YoY salary development, new-hire proxy (`Zugehörigkeit == fiscal_year` count)**. Compact block/finding `PayrollBlock.tsx` — very high level. **New hires** are a proxy only (no true hire date); **leavers not derivable** (no exit date). Metric set per Q3.

**Effort:** load+reconcile ~1.5 days; FA block+endpoint ~3 days; payroll block+endpoint ~2 days. **Tier L2** for FA (roll-forward/signs), **L1/L2** for payroll (mostly counts/sums).

---

## 9. Item 8 — Plan-outlook card (P4) — AUTHORED FRESH

**No prior spec exists** (§0). Design from scratch on the confirmed plan backend.

**Confirmed reusable backend** (all in `backend/app/services/fin_compat_pl.py` + `routers/financials_compat.py`):
- `build_statement_plan_response(session, statement, year, month, entity, *, allowed_prefixes=None)` (`fin_compat_pl.py:1102-1108`) → `{year, month, entity, has_plan_data, lines:[{line_code, plan_cm, plan_vs_actual, ytd_plan, ytg, coverage_pct}]}`; handles `PL|BS|CF|WC`; fail-closed on empty visibility.
- `load_position_plan_map(session, statement, year, month, ent_frag, *, scenario="budget", prefixes=None)` (`fin_compat_pl.py:348-356`).
- Endpoints exist: `/pl-statement/plan`, `/balance-sheet/plan`, `/working-capital/plan`, `/cash-flow/plan` (`financials_compat.py:201-312`), with `_resolve_plan_allowed_prefixes` visibility helper (L225-240).

**New design:**
- Service `build_plan_outlook(session, year, month, entity, grain, *, allowed_prefixes)` in a new `backend/app/services/plan_outlook.py`, calling `build_statement_plan_response` per statement. Output: an outlook series for the **next 5 periods** by filter grain — **next 5 months** when grain=month, **next 5 years** when grain=year (guard week-grain per Q7). Each point: `{period, plan_value, actual_or_forecast, coverage_pct, var_abs}` using the confirmed `Var_abs = actual − plan`, `Coverage% = 100·actual/|plan|`.
- Endpoint `GET /overview/plan-outlook` (or fold into `/overview/bundle` from §5.2 Lever C).
- Frontend `PlanOutlookBlock.tsx` in `overview-v2/blocks/` — a forward-looking chart (Recharts) of plan vs actual/forecast for the next 5 periods, top-line coverage verdict, detail table in the accordion.
- **Coordinate with the plan-layer redesign** (`docs/overview-v2-redesign-plan.md` Area 1 "YoY + vs-Plan"): the `PerformanceBlock` is still a P2 "coming soon" placeholder (`blocks/PerformanceBlock.tsx:7-13`); vs-Plan there and the outlook card should share the plan services and the sign conventions.

**Effort:** ~4 days (service + endpoint + chart block + golden on `build_plan_outlook`). **Tier L2** (period logic + variance signs → financial-metric-test).

---

## 10. Phased delivery order + dependencies

| Phase | Items | Depends on | Rationale |
|-------|-------|------------|-----------|
| **P0** | 7 (remove anomalies / fix 422) | — | Smallest, fixes a live bug, de-clutters first |
| **P1** | 1 (perf) + 2 (accordions) | P0 done | Perf + layout foundation; accordion primitive is reused by every later block. Batch endpoint (1C) should land before FA/payroll blocks so they join the bundle |
| **P2** | 3 (DuPont) + 4 (partners) | P1 (accordion for detail regions); item 4 needs 2 backend fields | Narratives are independent of data-load; can run parallel to P3 |
| **P3** | 5 (fixed assets) + 6 (payroll) | Loaders run on v2; FA/payroll blocks use the P1 accordion + ideally the bundle | Data must be loaded + reconciled before the blocks render real numbers |
| **P4** | 8 (plan outlook) | Confirmed plan backend (done); shares bundle from P1; coordinate with plan-layer Area 1 | Forward-looking card; last because it benefits from the settled block/accordion patterns |

**Cross-cutting dependency for narratives:** item 3's entity-attribution sentence and item 4's `top_share`/`max_ticket` need **small additive backend fields** (per-entity DuPont slice; development totals + max ticket). These degrade gracefully if not yet present, so P2 can ship the narratives first and light up the deeper sentences when the backend fields land.

---

## 11. reporting-v2 mode-gating & proof-of-no-impact

**Frontend gate:** `frontend/src/lib/overviewV2Mode.ts` → `IS_OVERVIEW_V2 = import.meta.env.MODE === 'reporting-v2'`; branched once at `OverviewPage.tsx:66-67`. Vite inlines `import.meta.env.MODE` at build time, so in the `v4` (5178/8012), `fdd-merge` (5176/8010), `test` (5175/8009) and default (5174/8008) bundles `IS_OVERVIEW_V2` folds to `false`, the branch is dead-code-eliminated, and the **entire `OverviewPageV2` + `overview-v2/` import graph is tree-shaken out** (`docs/overview-v2-redesign-plan.md:175-196`). Port/proxy map: `frontend/vite.config.ts:10-25` (reporting-v2 → 5177 / proxy 8011).

**Rules to keep isolation:**
1. Touch **only**: `frontend/src/pages/OverviewPageV2.tsx`, files under `frontend/src/components/financials/overview-v2/**`, and the **v2-scoped** narrative additions. Every new v2 file carries the `SCOPE: reporting-v2 / port 5177 only` header.
2. **Never** edit shared `frontend/src/lib/api.ts` response shapes, `App.tsx`, or the legacy `OverviewPage.tsx` body (used by other modes). New hooks are self-contained fetches (the existing v2 hooks already are).
3. Backend additions are **additive endpoints/fields** on `financials_compat.py` + new services; `overview_summary_use_mart` stays `default=False` in `config.py` and is enabled via **env on the 8011 process only** (`OVERVIEW_SUMMARY_USE_MART=true`), so 8008/8010/8012 are unaffected even after the mart is populated. Migrations `0022/0025/0026/0027/0028` are already additive-inert.
4. **DuPont/partner narrative files** (`dupontNarrativeEngine.ts`, `topEntitiesReportInsights.ts`) are shared with legacy consumers (`DuPontTree.tsx`, `TopCustomer/SupplierTable.tsx`). Preferred: put the new logic in **new v2 files** (`partnerDevelopmentInsights.ts`) and the additive `attributeRoeChange` core; only modify the shared engine's prose-stripping in a way that is safe for legacy callers (verify `DuPontTree` still renders). If risk is unacceptable, gate the new DuPont narrative behind `IS_OVERVIEW_V2` at the `DuPontTree` call site. (See Q4.)

**Proof steps in the PR:** build all four modes (`v4`, `fdd-merge`, `test`, default) and diff bundle output for the non-v2 modes to confirm zero change; run backend import + the touched-area pytest; confirm `/overview/*` responses on 8008/8010/8012 are byte-identical (no shape change).

---

## 12. Quality-gate & verification per phase
Each phase closes with the standard gate (`/run-quality-gate` → `/verify-done`):
- `cd frontend && npm run build` (all four modes for P0–P2 layout changes; at minimum reporting-v2 + one other).
- `cd backend && python -c "from app import main"`.
- `pytest` for touched areas: `test_mart_overview_equivalence.py` (P1), new DuPont + partner goldens (P2), FA/payroll reconciliation + `test_fixed_asset*`/`test_personnel*` (P3), `build_plan_outlook` golden (P4).
- Financial items (1B, 3, 4, 5, 8) require **financial-metric-test** (formula + worked example + edge cases + golden) — already drafted in §6/§7/§9.
- `security-privacy-reviewer` on P1 (mart cache key must include visibility scope), P3 (subledger PII in payroll — salaries), and any endpoint touching visibility.

---

## 13. OPEN QUESTIONS (decide before implementation)

**Q1 — Anomalies: fully remove vs relocate?** Default recommendation: remove the `AnomaliesPanel` rail from the Overview (kills the 422) but **keep a single de-featured alerts *finding*** with its `route` dropped. Alternative: remove all anomaly references from the Overview entirely (also drop the `summary-alerts` finding at `findingsBuilders.ts:253-262`). Which?

**Q2 — Fixed-assets block depth.** Minimum = 3 top-line KPIs (purchases/disposals/net FA development) + register table in the accordion. Options to add: per-asset-class breakdown, CapEx trend chart, depreciation schedule, largest additions/disposals list. How deep for v1?

**Q3 — Payroll metric set + the missing-date constraint.** Source has **no hire/exit dates** — "new hires" can only be a `Zugehörigkeit == fiscal_year` proxy, and **leavers/headcount-churn are not derivable**. Acceptable to ship new-hire-proxy + headcount + FTE + salary development + total personnel cost, and explicitly **omit leaver analysis**? Also: is displaying **salary figures** on the Overview acceptable given payroll PII sensitivity (informs the security review)?

**Q4 — Shared narrative engines: modify in place vs v2-gate?** DuPont/top-entity engines are shared with legacy pages. Prefer new v2 files + additive core (safe), or are we cleared to modify `dupontNarrativeEngine.ts` prose directly (simpler, but the change also shows in legacy `DuPontTree` on 5176/5178)? Recommend new-files + gate.

**Q5 — Mart refresh cadence + scope.** (a) Refresh via **management script wired into post-ingest** (recommended) vs **admin endpoint**? (b) Do we extend the mart to **WC + cash** (Lever B, larger win but needs per-metric financial equivalence sign-off), or ship EBIT-hero-only for v1?

**Q6 — Backend fields for deeper narratives.** Approve the two additive backend fields now — **per-entity DuPont slice** (enables the entity-attribution sentence in item 3) and **development `totals` + `max_ticket_keur`** (enables `top_share` and max-ticket in item 4)? Without them the narratives ship but silently drop those specific sentences.

**Q7 — Plan-outlook grain + horizon.** Confirm "next **5 months** (grain=month) / next **5 years** (grain=year)"; what should week-grain do (suppress the card, or show next 5 weeks)? And should the outlook use **plan vs actuals-to-date only**, or also a **forecast** line for future periods (scenario order `budget→forecast→plan`)?

**Q8 — Narrative length/tone.** Target the ordered templates as designed (verdict + 1–3 drivers + watch-item ≈ 3–5 sentences per block). Confirm tone (terse advisor vs fuller prose) and whether German or English is required for these strings (memory note says all user-facing strings must be English — assumed English).

**Q9 — `GoBD_Anlagennr` persistence.** Keep `asset_id = Anlage` (equivalent today, no migration) or persist `GoBD_Anlagennr` as the canonical asset key (identity change → new migration + mapping, L2)?
