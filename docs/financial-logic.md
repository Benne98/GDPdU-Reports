# Financial Logic — rules for Claude Code

> This is the most safety-critical document in the setup. Finssentials produces
> numbers that consultants put in front of clients. A wrong sign or period boundary
> is a product defect, not a cosmetic bug. Treat every formula change as risky.

## The iron rule

**No new or changed financial calculation without all four of these, in writing,
before the code:**

1. **Formula** — the exact definition (symbols + words).
2. **Worked example** — a tiny numeric case (3–5 rows) with the expected result.
3. **Edge cases** — zeros, negatives, missing values, partial years, sign flips.
4. **Regression test** — a `pytest` (or golden-file) test that locks the result.

If any of the four is missing, **stop and ask** instead of guessing. This is what
the `financial-metric-test` skill and the `financial-calculation-engineer` agent
enforce.

## Period model (FY / YTD / LTM)

Finssentials supports a configurable fiscal year (`fiscal_year_end_month`,
`fiscal_year_end_day`). Helpers live in `funktionssammlung.py`:
`fiscal_year_bounds`, `get_period`, `fy_label`, `ytd_label`, `ltm_label`,
`recognized_value`, `safe_month_day_ts`.

- **FY** — full fiscal year between the configured bounds.
- **YTD** — start of fiscal year up to the current month/day (`current_year`,
  `current_month`).
- **LTM** — last twelve months ending at the current period.
- `invoice_mapping_mode = "year"` supports **only FY**, not YTD/LTM (the GST script
  raises if you combine them). `accrual` calc requires `start_col`/`end_col`.

When you touch period logic, verify against the existing helper semantics — do not
re-derive year boundaries inline.

## Sign conventions

- Be explicit about whether costs are stored **positive** (and subtracted) or
  **negative** (and added). Mixing these is the classic FDD bug.
- Margins: Gross Profit = Revenue − Cost (or Revenue + Cost if cost is negative —
  know which). Gross Margin = GP / Revenue. Guard against divide-by-zero.
- Bridges (PVM = Price/Volume/Mix) must reconcile: the sum of effects equals the
  total delta. Add a reconciliation assertion in tests.
- **Never** silently change a sign or a rounding rule. Flag ambiguous accounting
  semantics and ask.

## Profit modes

Scripts use `profit_mode ∈ {profit, cost}`:
- `cost` → `value_cols.cost` must be set; GP derived from revenue − cost.
- `profit` → `value_cols.profit` must be set directly.
Revenue (`value_cols.revenue`) is always required.

## Data realities to defend against

- Real client files use arbitrary column names (e.g. COGS may be
  `Third-Party Contract Value`, not `Cost of Goods Sold`). Mapping is config-driven —
  never hardcode client column names into logic.
- Numbers may arrive as text, with thousands separators, `(123)` negatives, or
  locale decimals. Normalize centrally, test the normalizer.
- `to_kEUR` scaling: be consistent about units in a single output.

## Testing pattern (golden files)

For data-book outputs, prefer **golden-file regression tests**: a small synthetic
input workbook + an expected output snapshot. Re-running must reproduce the snapshot
byte-for-relevant-value. Store fixtures as synthetic data only (see `docs/security.md`).

## Master_BS / Master_PL derived from GL (pipeline data path)

`backend/app/services/fin_compat_master_from_gl.py` builds the databook Master sheets
from already-loaded GDPdU GL data by **reshaping** the trial-balance services
(`build_pl_trial_balance` / `build_bs_trial_balance`) into the SuSabyYear Master format.
It does **not** call `SuSabyYear.build_final_output` (that consumes raw long-format and
applies its own PL sign flip → chaining would double-flip). SuSabyYear is the format
reference only; `write_master_workbook` reuses `SuSabyYear.write_output` so the on-disk
workbook is structurally identical to the upload path (downstream recon re-reads it).

### Formulas

- **Master_PL[account, col]** = TB presented value for that account/column, copied
  verbatim — **no further sign flip** (the TB already presents revenue +, expense −).
  - FY column `FYyyA` ← TB summary key `FY{y}` (completed prior year) or `YTD{anchor}`
    (anchor year). Monthly column `Mon-YYYY` ← TB monthly key `YYYY-MM`.
- **Master_BS[account, col]** = TB cumulative month-end **closing balance**, raw stored
  sign (assets +, equity & liabilities −). No EB / opening-balance column (BS is already
  cumulative; verified no downstream script needs `EB-{year}`).
  - FY column `FYyyA` ← TB `DEC{y}` (Dec-31 year-end) or `CM{anchor}-{month}` (anchor
    fiscal year-end). Monthly column ← TB `YYYY-MM`.
- **Net income (BS equity row)** = Σ Master_PL value in the same column, per entity:
  `net_income[entity, col] = Σ_a Master_PL[a, col]`. Account `2869`,
  `L2="Equity" / L3="Net retained profits" / L4="Net income"` (verbatim recon labels).
  Injected **exactly once per entity**.

### L-hierarchy mapping (DB `dim_gl_account.level_*` → Master `L*`)

| Master col   | PL source          | BS source          |
|--------------|--------------------|--------------------|
| `L1 - BS/PL` | "PL" (const)       | "BS" (const)       |
| `L2`         | `level_2`          | `level_2`          |
| `L3`         | `level_3`          | `level_3`          |
| `L4`         | `level_4`          | `level_4`          |
| `L5`         | "" (synth)         | "" (synth)         |
| `L6`         | "Reported" (synth) | "Reported" (synth) |
| `NA`         | n/a                | `level_1` (Assets / Equity & liab. split; no L-slot) |

PL recon keys on `(L3, L4)`; BS recon keys on `(L2, L3, L4)` (`recon_mapping_loader.py`)
— the mapping keeps recon/lead non-empty. `Account` = bare `gl_account_id` (numeric sort),
not the TB `"gid | name"` field. DB `l4_sub` is not exposed by the TB services → dropped.

### Worked example (anchor 2024, fy_end_month 12, FYs [2023, 2024])

PL presented: REV `FY2023=+1000, YTD2024=+1200`; COGS `FY2023=−400, YTD2024=−500`.
→ Master_PL: REV `FY23A=+1000 FY24A=+1200`; COGS `FY23A=−400 FY24A=−500` (no re-flip).
→ Net income BS row: `FY23A=600 (1000−400)`, `FY24A=700 (1200−500)`.
BS raw: AR `DEC2023=+600, CM2024-12=+650` → `FY23A=+600 FY24A=+650`, `NA="Assets"`.

### Edge cases (tested)

zero column → 0.0 (no NaN); negative PL (expense) stays negative; missing TB key for a
requested period → 0.0; empty PL → no net-income row; FY outside the TB anchor span
(±3 years) → silently skipped.

### Regression test

`backend/tests/test_master_from_gl.py` — reshape unit, per-period reconciliation vs TB
sums, single net-income row + labels, workbook integration (both sheets + full META).

## Opening balances — carry-forward (reporting-v2 Phase 2)

`etl/opening_balance.py::synthesize_opening_balances(session, scope, mode)` owns
rebuild **stage 4**, gated by `settings.opening_balance_mode`
(`in_data` default | `file` | `carry_forward`). Wired in `etl/rebuild.py`.

### Sign convention (unchanged, matches `fin_compat_bs_sql.py`)

`fact_gl_line.amount`: **+ = debit** (assets), **− = credit** (equity & liabilities).
Opening-balance rows are **single-sided** (one line per account, no balancing
counter-line) — exactly like the real Jan-1 rows in the source GL — so they are
exempt from B1/B2/B3 via `etl/checks.py::_opening_exempt_mask` (keyed on
`fiscal_period=0` / `entry_type='opening_balance'`).

### Modes

- **`in_data` (DEFAULT, legacy 5176):** NO-OP. Opening balances already exist as
  `entry_type='opening_balance'` / `fiscal_period=0` rows from the load step
  (`etl/gobd_gl_prepare.py`). Touching nothing keeps the golden live-vs-v2
  equivalence intact.
- **`file`:** a separate first-year opening-balance file is loaded through the
  canonical loader (its synthetic group number is minted by
  `gobd_gl_prepare._synthetic_opening_txn`, leading `9`). This mode only *ensures
  tagging*: sets `entry_type='opening_balance'`, `fiscal_period=0` on the matching
  `fact_gl_entry` headers. It synthesizes nothing and never touches `amount`.
- **`carry_forward`:** synthesizes opening balances for years after the first.

### OB-mode exclusivity (file-OB vs in-data-OB)

File-OB (the Project-Setup `/api/v1/ingest/opening-balance/commit` endpoint) and
in-data-OB are **mutually exclusive per `(account_number_group, fiscal_year)`**: a
file-OB commit must NOT add a second opening-balance set for an account/year that
already carries an **in-data** opening_balance row (else the BS opening stock is
double-counted). The commit detects this — it probes `fact_gl_line ⋈ fact_gl_entry`
for `entry_type='opening_balance'` / `fiscal_period=0` rows whose JEGN is NOT a
file-OB synthetic (the file-OB convention is a `9` at char 3, `<EE>9<acct>`; in-data
rows do not carry that), restricted to the scope's prefixes/years — and **skips**
the colliding rows (logging a warning). If every row collides the commit returns
**409** rather than writing a duplicate OB set. Re-committing the same file-OB is
still idempotent (the synthetic `9`-tagged rows are deleted-by-JEGN then re-inserted)
and does not trip this guard.

The three synthetic-OB conventions occupy DISTINCT `booking_line_id` bands so they
never collide: load-path real lines are 1-based; **file-OB** (ingest router) uses
the `800_000_000_000` band with JEGN `<EE>9<acct>`; **carry_forward** uses the
`900_000_000_000` band (`etl.opening_balance._SYNTHETIC_BID_BASE`) with JEGN
`<EE>8<YY><acct>`.

### Formula (carry_forward)

For a **balance-sheet** account `a` of entity `e` (`dim_gl_account.level_0='BS'`)
and fiscal year `fy` strictly greater than that entity's first year:

```
OB[e, a, fy] = Σ amount  over all real fact_gl_line rows of (e, a)
                        with fiscal_year ≤ fy-1
```

i.e. the cumulative prior closing balance carried into `fy`. "Real" = excludes our
own synthetic marker (`source_system <> 'synthetic_carry_forward_ob'`) so the sum
recomputes identically on every run. **PL accounts reset each year** — never carried
forward. Zero carry-forwards produce no row.

Each OB is one single-line synthetic journal entry: `fiscal_period=0`,
`entry_type='opening_balance'`, deterministic 12-char
`journal_entry_group_number = EE 8 YY AAAAAA` (entity, `8` discriminator so it never
collides with the load-path `9`, year, account), `posting_date = Jan-1 of fy`,
`source_system='synthetic_carry_forward_ob'`. **Idempotent**: a re-run first DELETEs
rows carrying that marker (in scope) then re-inserts.

### Worked example (entity `01`, BS account `01-1200` Trade receivables, FYs 2022–2024)

Real ledger movements on `011200` (`+`=debit):

| fiscal_year | Σ amount (movements) |
|-------------|----------------------|
| 2022        | +600                 |
| 2023        | +50                  |
| 2024        | −30                  |

- 2022 is the entity's **first** year → **no** carry-forward OB synthesized.
- OB[01, 011200, **2023**] = Σ(fy ≤ 2022) = **+600** → synthetic credit/debit row,
  `posting_date=2023-01-01`, amount `+600`.
- OB[01, 011200, **2024**] = Σ(fy ≤ 2023) = 600 + 50 = **+650** →
  `posting_date=2024-01-01`, amount `+650`.

A liability account `01-1600` with movements 2022 `−400`, 2023 `−100`:
- OB[2023] = **−400**, OB[2024] = **−500** (credit, stored negative).

### Edge cases (tested)

- **First year**: no carry-forward row (only years `> MIN(fiscal_year)` per entity).
- **Account with only an opening balance and no later movement**: still carried —
  the cumulative sum includes the prior OB it was built on, so the closing balance
  rolls forward unchanged.
- **Zero carry-forward** (`|Σ| ≤ 1e-6`): no row emitted (no noise).
- **PL account**: excluded (`level_0='BS'` filter) — resets to zero each year.
- **FY start month ≠ January**: the DB groups on `fact_gl_entry.fiscal_year`
  (authoritative), independent of the calendar month; the synthetic `posting_date`
  is set to Jan-1 of `fy` purely to match the existing in-data OB convention and is
  exempt from S2 (posting-year check). The carry-forward sum is by `fiscal_year`, so
  a non-January FY start does not change the cumulative result.
- **Per-entity balance (B2)**: a balanced prior year (`Σ amount = 0` across ALL
  accounts) carries forward balanced *across BS accounts only* minus the year's P&L
  result; the residual is equity/net-profit and is Phase 3 (NOT booked here). OB rows
  are single-sided and B2-exempt, so synthesizing them cannot break B2.
- **Idempotency**: second run deletes-by-marker then re-inserts → identical rows.

### Regression test

`etl/tests/test_opening_balance.py` (SQLite in-memory, synthetic GL): carry_forward
produces prior-year closing as next-year OB; first year has none; idempotent (run
twice = identical); `in_data` is a true no-op; PL excluded; zero skipped; B2 preserved.
Plus the **golden equivalence gate**: with default `OPENING_BALANCE_MODE=in_data`,
`golden_snapshot.py compare live v2` must stay exit 0 after a v2 rebuild.

## Net profit — synthetic GL bookings (reporting-v2 Phase 3)

`etl/net_profit.py::synthesize_net_profit(session, scope)` owns rebuild **stage 5**,
gated by `settings.bs_net_profit_source` (`report_inject` default | `gl_rows`).
Wired in `etl/rebuild.py::_stage_net_profit`.

### Sign convention (unchanged, matches `fin_compat_bs_sql.py`)

`fact_gl_line.amount`: **+ = debit** (assets), **− = credit** (equity & liabilities).
The BS reads the raw stored sign; the credit side is **display-flipped ONCE** in
`fin_compat_bs._flip_row_tree`. The net-profit row is single-sided (one equity
credit, no balancing counter-line) — exactly like opening balances — so it is exempt
from B1/B2/B3 via `etl/checks.py::_opening_exempt_mask` (now keyed on
`fiscal_period=0` / opening `entry_type` **OR** `entry_type='net_profit'`).

### Two sources (presentation is IDENTICAL in both — the gate)

- **`report_inject` (DEFAULT, live 8010 / legacy 5176):** NO-OP in the ETL. The BS
  net profit stays a *virtual* report-layer injection: `fin_compat_bs._inject_net_profit`
  appends a `line_code='NET_PROFIT'` child under Equity and bumps the equity +
  grandtotal subtotals, sourced from the P&L SQL (`bs_net_profit_sql_*`). No GL rows
  are written; the stored ledger does **not** net to zero per FY (the residual is the
  year's net profit). Touching nothing keeps live behaviour and the golden gate.
- **`gl_rows` (reporting-v2):** synthesize one balancing equity row per (entity, FY)
  so the **ledger itself balances** (`Σ BS amount incl. NP = 0` per FY) for B-checks /
  a future cutover. The synthetic rows carry `entry_type='net_profit'` and are
  **EXCLUDED from the BS cumulative-balance grain** (`fin_compat_bs_sql.py`, the
  `_bal_amount_expr` / `_bal_amount_expr_fy` family + the line-detail raw sums) via an
  `AND COALESCE(e.entry_type,'') <> 'net_profit'` guard, so the equity hierarchy nodes
  and subtotals stay **byte-identical** to `report_inject`. The presented `NET_PROFIT`
  line is still produced by `_inject_net_profit` from the same P&L SQL in **both**
  modes (it is NOT gated off). **The only difference in `gl_rows` is the presence of
  the excluded-from-presentation balancing GL rows** — hence the displayed Balance
  Sheet is identical and `golden_snapshot.py compare live <gl_rows>` is exit 0.

### Formula

Presented net profit per (entity, fy) is the P&L YTD result with the P&L sign rule
applied (income +). The stored equity booking is the **credit** value (= −presented):

```
NP_presented[e, fy] = Σ (PL amount × -1)  over level_0='PL', that (e, fy)
NP_stored[e, fy]    = Σ  PL amount         over level_0='PL', that (e, fy)
                    = -NP_presented[e, fy]            (credit; negative for a profit)
```

Booking `NP_stored` (a credit) onto an equity account makes the balance identity hold:
on the live DB, per FY `Σ(BS amount) == NP_presented == -Σ(PL amount)`, so
`Σ(BS amount) + NP_stored == 0` ⇒ **Assets = Equity & liabilities** once booked. After
the BS credit-side display flip, `NP_stored` presents as `+NP_presented`.

### Equity target (mirrored from the live chart)

The booking lands on the entity's canonical **Net profit** equity account:
`dim_gl_account` with `level_0='BS'`, `level_1='Equity & liabilities'`,
`level_2='Equity'`, `level_3='Net profit'` (account_number_group ending `000001`, e.g.
`01000001`…`05000001`, plus sub-entity `02020001`/`05030001`). When an entity has more
than one such account in a year, the canonical one is chosen deterministically as
`MIN(account_number_group)` for that `entity_prefix`/`fiscal_year` (so `02000001` wins
over `02020001`). One single-line journal entry per (entity, fy): `fiscal_period=12`
(year-end equity result), `entry_type='net_profit'`, `posting_date = Dec-31 of fy`,
deterministic 12-char `journal_entry_group_number = EE 7 YY 000001` (entity, `7`
discriminator so it never collides with the load-path `9` or carry-forward OB `8`,
year, account), `source_system='synthetic_net_profit'`. **Idempotent**: a re-run first
DELETEs rows carrying that marker (in scope) then re-inserts.

### Worked example (entity `01`, FY2024)

Canonical P&L sign: revenue is credit-normal (stored negative), expense debit-normal
(stored positive). Revenue **−1200**, COGS **+500**:

```
Σ PL amount  = -1200 + 500 = -700   →  NP_stored  = -700  (credit; profit)
NP_presented = -(-700)     = +700
```

Book `-700` onto `01000001` (Net profit). If the profit produced a `+700` asset debit,
the BS side now sums `+700 (asset) + (-700) (equity NP) = 0` — Assets = Equity & liab.
After the display flip the injected/booked Net profit presents as **+700**.

### Edge cases (tested)

- **Loss** (`Σ PL amount > 0`): stored **positive**, presented **negative** (e.g.
  entity 02 FY2025: `Σ PL = +948 784.33` → stored `+948 784.33`, presented `−948 784.33`).
- **Zero net profit** (`|Σ PL| ≤ 1e-6`): no row emitted.
- **Multi-entity**: independent per `entity_prefix` (one canonical row each).
- **No seeded Net-profit account** for an entity/fy with non-zero P&L: skipped + logged
  (no silent mis-booking).
- **Current-year vs cumulative**: the ETL stores per-FY `Σ PL amount` (the FY result).
  The **presentation MIRRORS the report injection** — `_inject_net_profit` is fed by the
  per-column P&L YTD SQL in both modes — so for every column key (py_cm/pm/cm/ytd/ytd_py,
  monthly `YYYY-MM`, snapshot fy_py/fy/cm_py/cm, consolidation per entity) the displayed
  Net profit is identical to legacy. Confirmed gate-identical.
- **Idempotency**: second run deletes-by-marker then re-inserts → identical rows; the
  PL source sum excludes the synthetic marker so a re-run never inflates the amount.
- **B-check exemption**: single-sided by design; `entry_type='net_profit'` is exempt
  from B1/B2/B3 (and S1 amount / S2) via `_opening_exempt_mask`. PL SQL is `WHERE
  level_0='PL'` and the row is on a BS equity account, so the P&L is unaffected.

### Regression test

`etl/tests/test_net_profit.py` (SQLite in-memory, synthetic GL): value & sign
(`Σ PL amount` stored, credit-negative for profit), balance identity (`Σ BS incl NP = 0`),
single row per entity/year onto the `MIN` Net-profit account, fiscal_period=12 /
entry_type / Dec-31 tagging, idempotency (run twice = identical, no double count), loss
case (positive stored), zero → no row, multi-entity, B-check exemption (B1/B2/B3 pass
with a net_profit row; `_opening_exempt_mask` exempts `entry_type='net_profit'`). Plus
the **golden equivalence gate** (BOTH directions): `report_inject` → `compare live
v2_reportinject` exit 0 (no regression); `gl_rows` → `compare live v2_glrows` exit 0
(BS identical though the ledger now balances).

## Mapping library + per-project CoA override + client-CoA (reporting-v2 Phase 4)

Phase 4 is **classification/presentation only** — it sets `dim_gl_account.level_*`
hierarchy + sort columns. It introduces **no new KPI / sign / period formula**; the
amounts and signs read by every statement service are unchanged. Its safety
property is *additive no-op equivalence*, locked by the golden gate.

### Mapping library file format (`etl/mapping_library/finssentials_standard_v1.json`)

Single JSON object minted by `etl/mapping_library/export_standard.py` from a
live/clone `dim_gl_account` (run against `finssentials_v2` for v1):

```
{ "version": "finssentials_standard_v1", "source_db": ..., "generated_at": ...,
  "entry_count": N,
  "entries": [ { "gl_account_id": "16100", "account_name": "...",
                 "level_0".."level_4", "l4_sub",
                 "level_1_sort".."level_4_sort" }, ... ] }
```

Keyed per **distinct business account** — collapsed to ONE entry per
`gl_account_id` (fallback `account_name`), keeping the **most recent fiscal year**
so the latest agreed hierarchy wins; `entries` sorted by `gl_account_id` then
`account_name` (stable diff). This is the auto-suggest source for new clients — the
same concept as the FDD bot's `BS_/PL_Kontenmapping.xlsx` (alternative source).

### Auto-suggest (`etl/mapping_suggest.py::suggest_account_mapping`)

Pure function. Matches a new dataset's accounts to the library: **(1)** exact
`gl_account_id` (`match='gl_account_id'`) → **(2)** exact normalized `account_name`
(`match='name'`) → else **unmatched** (handed to the CoA editor). Normalisation:
casefold + drop punctuation + collapse whitespace + strip `.0` artifact. Never
cross-guesses; blank keys never match. Returns `{proposals, unmatched}`.

### Per-project CoA override (`dim_project_coa_override`, migration `0009`)

The CoA editor persists edits into `dim_gl_account` directly; they are ALSO captured
into `dim_project_coa_override` (`project_id` default `'default'`; full `dim_project`
out of scope) and **replayed onto `dim_gl_account`** at the start of every rebuild
(`etl/project_coa_override.py::replay_project_overrides`, wired as rebuild **stage 1**)
so editor edits survive a data reload. Replay is a set-based `UPDATE … FROM`,
idempotent, scope-year-restricted.

**No-op equivalence (the gate):** with **no override rows** (table empty) the replay
updates 0 rows and touches nothing. A missing table (partially-migrated/test schema)
is also a no-op. Therefore a full `rebuild_project` on `finssentials_v2` with the
empty override table keeps `golden_snapshot.py compare live v2` at **exit 0**
(verified: 164 common files, 0 diffs).

### Client-CoA 1:1 (`etl/client_coa.py::load_client_coa`)

Alternative to the library suggest: ingest a client's own Sachkontenstamm verbatim.
Thin wrapper over the canonical path `apply_account_mapping(profile)` →
`load_account_mapping` (same key construction `entity_prefix(2)+zfill(account,6)`,
same idempotent UPSERT into `dim_gl_account`). Profile maps the client's arbitrary
columns to the canonical fields (`account_number`, `level_0..level_3` required;
`level_4`, `l4_sub`, `*_sort`, `account_name`, `gl_account_id`, NA/CF optional);
`entity_prefix`/`fiscal_year` come from the scope.

### Regression tests

`etl/tests/test_mapping_suggest.py` (pure: id/name precedence, unmatched routing,
normalisation tolerance, deterministic first-wins, robustness) and
`etl/tests/test_client_coa.py` (SQLite synthetic: 1:1 load populates levels, scope
override, idempotent reload, profile required; plus override replay no-op/empty,
applies levels+sort, scope-year restriction, absent-account ignored, missing-table
no-op, capture upsert idempotency). Plus the **golden equivalence gate** above.

## True ISO-week grain — week_cutoff / week_range (reporting-v2 Phase 5)

Weekly views for **all** statements (BS / IS / WC / CF) **and Sales** are TRUE
ISO weeks derived from `fact_gl_entry.posting_date` (BS/IS/WC/CF) and
`fact_sales.posting_date` (Sales) — **not** a monthly (`fiscal_period`) bucket
approximation. Two centralized primitives in
`backend/app/services/fin_compat_sql.py` express the only two ways an ISO week is
used, so every weekly path is unambiguous about STOCK vs FLOW:

- `week_cutoff(iso_year, iso_week)` → the **Sunday** of the ISO week. The STOCK
  cutoff: a balance-sheet / working-capital column is the cumulative closing
  balance with `posting_date <= week_cutoff`.
- `week_range(iso_year, iso_week)` → `(Monday, Sunday)`. The FLOW window: a P&L /
  cash-flow / sales column is `Σ` of movements with `posting_date` **between**
  Monday and Sunday (inclusive).
- `iso_week_of(d)` → `(iso_year, iso_week)` the calendar date `d` belongs to
  (ISO-8601 `date.isocalendar()`); used to walk a trailing week series and to
  label sales buckets by the week their `posting_date` falls in.

Both primitives are thin wrappers over `iso_week_monday` (Mon-start) /
`iso_week_bounds` (Mon..Sun), so they are **behaviour-identical** to the dates the
existing BS/PL/WC/CF weekly SQL already computes via `iso_week_bounds`. They are
the single canonical entry point so new weekly paths (Sales) route through the
same math instead of re-deriving ISO weeks inline.

### Formulas

```
week_cutoff(iy, iw) = iso_week_monday(iy, iw) + 6 days            (Sunday)
week_range(iy, iw)  = (iso_week_monday(iy, iw), week_cutoff(iy, iw))
BS/WC week value (STOCK) = Σ amount WHERE posting_date <= week_cutoff(iy, iw)
PL/CF/Sales week value (FLOW) = Σ amount WHERE posting_date BETWEEN monday AND sunday
```

Sign conventions are **unchanged**: P&L/CF/Sales present `amount * -1` (or
`gross_sales` for sales); BS/WC keep the raw stored sign. Only the date predicate
changes (posting_date window vs the old fiscal_period bucket for Sales).

### Worked example (anchor 2025, month 7; CW31 straddles Jul/Aug)

`week_range(2025, 31) = (2025-07-28, 2025-08-03)` — a true ISO week that crosses
the month boundary. Synthetic gross-sales / GL postings:

| posting_date | amount | ISO week     | in CW31 flow? |
|--------------|--------|--------------|---------------|
| 2025-07-27   | 100    | CW30         | no            |
| 2025-07-28   | 200    | CW31 (Mon)   | yes           |
| 2025-07-30   |  50    | CW31         | yes           |
| 2025-08-01   |  30    | CW31 (Aug!)  | yes           |
| 2025-08-03   |  20    | CW31 (Sun)   | yes           |
| 2025-08-04   | 999    | CW32 (Mon)   | no            |

- **FLOW** CW31 (PL/CF/Sales) = 200+50+30+20 = **300** (the Aug-01/03 days belong
  to CW31 — a monthly bucket would have mis-assigned them to August → 250).
- **STOCK** at `week_cutoff(2025, 31)=2025-08-03` (BS/WC) = 100+200+50+30+20 =
  **400** (cumulative `<=` the week-end). Stock at CW30 (cutoff 2025-07-27) = 100.

### Edge cases (tested)

- **ISO week 1 starts in the prior calendar year**: `week_range(2025, 1) =
  (2024-12-30, 2025-01-05)`.
- **ISO week 53** (long years, e.g. 2020): `week_range(2020, 53) = (2020-12-28,
  2021-01-03)` — valid, returned naturally when stepped over.
- **Year boundary / Dec-31**: `iso_week_of(2025-12-31) = (2026, 1)` — a Dec-31
  posting lands in ISO **2026-W01** (Mon 2025-12-29..Sun 2026-01-04), *not*
  2025-W53. It is inside the CW01/2026 flow window and on/before its stock cutoff.
- **FY start month ≠ January**: the grain is a pure calendar/ISO date window,
  independent of `fiscal_period` — a posting on 2025-07-30 buckets into CW31/2025
  whatever the entity's fiscal-year start month.
- **Partial / trailing weeks** (Sales `trailing_iso_weeks`): the anchor week is
  the ISO week of the anchor month-end; walking back uses `prior_iso_week`
  (date arithmetic), so it steps across the ISO-year boundary correctly (e.g.
  2026-W01 → 2025-W52) instead of a naive week-number decrement.
- **Zero / missing**: a week with no postings → `Σ = 0` (no row noise).

### What changed vs legacy (intended behaviour change)

- **Sales weekly** (`sales_analytics_compat.build_geo_trend`, `grain='week'`):
  previously used **rolling-7-day windows ending at the month-end date** (not
  Monday-aligned, not ISO weeks) — a monthly-anchored approximation. Now uses true
  ISO weeks via `trailing_iso_weeks` → `week_range`. This is an **intended**
  change; sales weekly trend buckets differ from the old output.
- **BS / IS / WC / CF weekly** were already `posting_date` / ISO-week-bounds based
  (`fin_compat_sql.pl_grain_sql_week`, `fin_compat_bs_sql.bs_grain_sql_week`,
  `fin_compat_wc_sql.wc_grain_sql_week`, `fin_compat_cf_sql.cf_grain_sql_week`).
  They are **unchanged** (same math as `week_cutoff`/`week_range`), so their weekly
  payloads stay byte-identical and the ANNUAL + MONTHLY golden stays equivalent.

### Regression test

`backend/tests/test_weekly_grain.py` (DB-free, synthetic GL): primitive dates
(incl. ISO 1/52/53 + the Dec-31 year boundary); PL/Sales week == Σ in-week
movements (flow predicate `monday <= posting_date <= sunday`); BS week ==
cumulative `posting_date <= week_cutoff` (stock), monotone; Dec-31 movement lands
in next ISO-year W01; FY-start ≠ Jan does not move buckets; `trailing_iso_weeks`
anchor/order + ISO-year-boundary walk-back. The reference aggregation mirrors the
production SQL predicates exactly.

## GL outlier z-score — Outliers tab (Journal Agent, Phase 1)

`backend/app/services/gl_outliers.py::build_outliers` builds, per **material GL
account**, the ALL-HISTORY monthly value series (from
`gl_analysis_common.build_account_monthly_series`) and attaches a per-point
mean-residual **z-score** so the client can re-threshold with a σ slider without a
refetch. The backend NEVER decides which points are outliers — it returns the full
series + per-point `residual_keur`/`z` + `stats:{mean_keur, std_keur, n}`; the
client flags `|z| >= σ` live. Endpoint `GET /api/v1/financials/anomalies/outliers`
(params `period_grain, year, month, iso_year, iso_week, entity, statement∈{all,pl,bs}`),
PURE READ, NOT in the golden catalogue.

This is an analysis overlay, **not a new monetary formula**: the values come from
`gl_analysis_common` (sign convention there: PL revenue +, BS assets +). The only
new math is the descriptive z-score.

### Formula

For an account's monthly value series `x = [x_1 … x_n]` (kEUR):

```
mean        μ  = numpy.mean(x)
std (sample) σ̂ = numpy.std(x, ddof=1)        # n-1 denominator (unbiased sample std)
residual_i     = x_i − μ
z_i            = residual_i / σ̂              # 0 when σ̂ == 0 or n < 2
outlier_i     ⇔ |z_i| >= σ                    # σ = slider, CLIENT-side (0.5..3, default 1.0)
```

Sample std (`ddof=1`) — the monthly series is a *sample* of the account's
behaviour, so the unbiased estimator is used (it differs materially from the
population std on short series).

### Worked example — `[10, 12, 11, 13, 60]` kEUR (one spike)

```
μ  = (10+12+11+13+60)/5 = 21.2
σ̂ = std(…, ddof=1)      ≈ 21.72        (population std ≈ 19.43 — different!)
residual(60) = 60 − 21.2 = 38.8
z(60)        = 38.8 / 21.72 ≈ 1.787
```

At **σ = 1.0** the 60 IS flagged (1.787 ≥ 1); at **σ = 2.0** it is NOT (1.787 < 2).
The other four points have |z| ≤ |11−21.2|/21.72 ≈ 0.47 — never flagged at σ ≥ 1.

### Edge cases (tested)

- **n < 2** → σ̂ undefined → `std_keur = 0.0`, all `z = 0` → nothing flags.
- **σ̂ == 0** (flat series) → residual 0, z 0 → nothing flags; no divide-by-zero.
- **|z| == σ exactly** → flagged (the client rule is `>=`, boundary inclusive;
  e.g. series `[−1, +1]` has |z| = 1/√2 ≈ 0.7071 on both points).
- **empty ledger** → `accounts: []`.
- **sign flip / large negative swing** → handled upstream by the presented-amount
  sign rule; the z-score is sign-agnostic (`|z|`), so a large negative swing flags
  exactly like a large positive one.

### Week grain (plan M4)

Outliers ALWAYS operate on the **monthly** series, regardless of the page
`period_grain`. The `period` argument only labels the view (a week resolves to its
anchor month via `plan_anchor_for_week` for material bounding + the label); there
is no "weekly outlier" — a week has far too few points for a stable mean/σ.

### Regression test

`backend/tests/test_gl_outliers.py` (DB-free): the worked example (mean/sample std/
z of the spike), n<2 → std 0 / z 0, flat series no divide-by-zero, |z|==σ boundary
inclusive, sign-agnostic symmetry, per-account payload shape (full series +
residual/z + stats + statement mapping pl/bs), anchor resolution (month/year/week),
and `build_outliers` rejecting an unsupported `statement`.

## GL seasonality (additive decomposition) — Seasonality tab (Journal Agent, Phase 2)

`backend/app/services/gl_seasonality.py::build_seasonality` builds, per **material GL
account**, the ALL-HISTORY monthly value series (from
`gl_analysis_common.build_account_monthly_series`) and decomposes it **additively**
into trend + seasonal + residual, attaching a per-point residual **z-score** plus the
12 seasonal factors so the client can re-threshold off-season months with a σ slider
without a refetch. The backend NEVER decides which months are off-season — it returns
the full series + per-point `actual_keur`/`trend_keur`/`seasonal_index`/`expected_keur`/
`residual_keur`/`z`, the 12-entry `month_index`, and `stats:{resid_std_keur, n}`; the
client flags `|z| >= σ` live. Endpoint `GET /api/v1/financials/anomalies/seasonality`
(params `period_grain, year, month, iso_year, iso_week, entity, statement∈{all,pl,bs}`),
PURE READ, NOT in the golden catalogue.

This is an analysis overlay, **not a new monetary formula**: the values come from
`gl_analysis_common` (sign convention there: PL revenue +, BS assets +). The new math is
the descriptive decomposition + residual z-score.

### Formula

For an account's chronologically ordered monthly value series `x = [x_1 … x_n]` (kEUR)
with parallel calendar months `month_t ∈ {1..12}`:

```
trend_t        = pandas rolling(window=12, center=True, min_periods=6).mean(x)   # NaN at ends
detrended_t    = x_t − trend_t                                                   # NaN where trend NaN
raw_index[m]   = mean over years of detrended_t for calendar month m            # NaNs ignored; 0 if none
seasonal_index[m] = raw_index[m] − mean(raw_index[1..12])                       # normalise → Σ = 0
expected_t     = trend_t + seasonal_index[month_t]                              # NaN where trend NaN
residual_t     = x_t − expected_t                                               # NaN where expected NaN
resid_std      = numpy.nanstd(residual, ddof=1)                                 # sample std over finite residuals
z_t            = residual_t / resid_std                                         # None if resid_std==0 or residual NaN
off-season_t   ⇔ |z_t| >= σ                                                     # σ = slider, CLIENT-side (0.5..3, default 1.0)
```

**Additive (not multiplicative)** is deliberate: GL values can be zero or negative
(cost lines presented negative, contra accounts), so `actual / trend` is undefined /
sign-unstable. Normalising the 12 indices to **sum 0** means the seasonal component
nets to zero over a full year (trend carries the level), so `Σ expected ≈ Σ trend`
over whole years.

### Worked example — December-peak series (3 years), σ = 1.0

Build a synthetic level of 100 each month plus a recurring +60 in December, then in the
3rd year (a) flatten December to its base 100 (missing peak) and (b) spike a random
mid-year month (e.g. May → 200):

```
seasonal_index[12]  large positive (the recurring December peak)
seasonal_index[m≠12] small negative (sum to 0)
December (3rd yr) = 100 but expected ≈ trend + ~+55 → residual ≈ −55 → z very negative → FLAGGED (missing peak)
May (3rd yr)      = 200 but expected ≈ trend + ~−5 → residual ≈ +105 → z very positive → FLAGGED (unexpected spike)
```

Because `|z|` is sign-agnostic, both the **missing expected peak** (large negative
residual) and the **unexpected spike** (large positive residual) flag at σ = 1.0.

### Edge cases (tested)

- **< min_years (default 2) distinct fiscal years** → `insufficient_history: true`; all
  12 `seasonal_index = 0`, `expected = trend`, z still reported; UI shows the
  "insufficient history" state instead of off-season flags.
- **NaN trend** (the centered rolling window holds < 6 valid points) → `trend`,
  `expected`, `residual`, `z` are `null` there → those months never flag. For a typical
  multi-year series the trend is defined everywhere; this only bites very short series
  (≤ 5 points) or interior gaps.
- **resid_std == 0** (perfectly fitted / < 2 finite residuals) → `z` all `null` → nothing
  flags; no divide-by-zero.
- **empty ledger** → `accounts: []`.
- **zero / negative values** → fully supported (the model is additive); a large negative
  swing flags exactly like a large positive one.

### Week grain (plan M4)

Seasonality ALWAYS operates on the **monthly** series, regardless of the page
`period_grain` — calendar-month seasonality is the whole point. The `period` argument
only labels the view (a week resolves to its anchor month via `plan_anchor_for_week` for
material bounding + the label).

### Regression test

`backend/tests/test_gl_seasonality.py` (DB-free): seasonal indices sum to 0; the
December-peak worked example (large positive `seasonal_index[12]`, missing-December-peak
→ negative-z flag, mid-year spike → positive-z flag at the σ boundary); insufficient
history (< min_years → flat indices + `insufficient_history`); NaN trend (short series) →
`z = None`; resid_std == 0 → no flags; per-account payload shape and `build_seasonality`
rejecting an unsupported `statement`.

## Manual budget — seasonalize, partner roll-up, sign-flip (Phase 4)

`backend/app/services/budget_service.py`. The manual-budget feature persists a
`scenario='budget'` plan per BS/PL reporting position and per debtor/creditor in
`fact_position_plan`. Three financial primitives are gated here.

### Seasonalize (annual → 12 months)

**Formula.** Given an annual value `A` and 12 weights `w[1..12]`:

```
norm(p)  = w[p] / Σ_q w[q]        (Σ norm == 1)
month(p) = A * norm(p)            ⇒ Σ_p month(p) == A   (to 1e-6)
```

Fallback: when `Σ w == 0` (or weights absent) use uniform `norm(p) = 1/12`. The
weight source is the position's / partner's actual monthly profile in the base FY
(reuses `plan_synth.seasonal_index`, identical to the synthetic plan).

**Worked example.** `A = 1200`, `w = [2,1,1,…,1]` (Σ = 13): `month(1) = 1200·2/13
= 184.615…`, the other 11 months `1200/13 = 92.307…`; Σ = 1200.0. Uniform weights
→ every month 100.0, Σ = 1200.0.

**Edge cases (tested).** `A = 0` → all-zero (no div-by-zero); `Σ w = 0` → uniform
fallback; a **negative weight** (a month opposing the annual sign, e.g. a credit
note) is preserved — `norm` may be negative for that month and the split still
sums to `A` (sign-flip month). Storage is always monthly (12 rows); `annual = Σ
months`; a fresh annual edit overwrites all 12, a month edit writes one.

### Partner roll-up (Σ partners + Other == position)

**Formula.** Position total `P`, named Top-N partners `s_i`:

```
Other = P − Σ_i s_i              ⇒ Σ_i s_i + Other == P   (invariant)
```

Two edit directions, both re-established server-side: editing a partner keeps `P`
and re-derives `Other`; editing the position total moves the delta into `Other`
(named untouched). `Other` may be negative (new partner with no base, or named >
total) — **not clamped**.

**Worked example.** `P = 1000`, named A = 300 B = 200 → `Other = 500` (Σ = 1000).
Edit B → 250 → `Other = 450`. Edit total → 900 (named unchanged) → `Other = 350`.

**Edge cases (tested).** No named partners → `Other = P`; `Σ(named) > P` → `Other`
negative; `top_n` keeps the largest by `|annual|`, the rest fold into `Other`.

### Sign flip (presented ↔ stored), applied ONCE on write

`fact_position_plan.amount` is the STORED GL sign (+ debit / − credit), exactly
like `fact_gl_plan`. The grid exchanges PRESENTED values; the flip is the inverse
of the readers' flip and happens once, in `present_to_stored`:

```
PL          : stored = −presented        (inverse of plan_grain_sql's amount*-1)
BS asset (AR): stored = +presented        (inverse of _present_bs asset = +amount)
BS credit(AP): stored = −presented        (inverse of _present_bs credit = −amount)
```

**Worked example.** Revenue presented +3000 → stored −3000; expense presented
−500 → stored +500; AR presented +100 → stored +100; AP presented +100 → stored
−100. `stored_to_present(present_to_stored(v)) == v` for every case.

The BS side is derived from the partner kind (customer → asset/AR, supplier →
credit/AP), matching `balance_sheet.bs_side`. A divergence here would desync
plan-vs-actual, so it is unit-tested against the reader convention.

### Regression tests

`backend/tests/test_budget_service.py` (DB-free): seasonalize (Σ==annual,
uniform, annual=0, sign-flip month), roll-up (both directions, Top-N remainder,
negative Other), sign-flip round-trip (PL / BS asset / BS credit),
`budget_positions` mapping. `backend/tests/test_budget_api.py`: admin-gating,
GET pure-read, and a v2 round-trip (seed → PUT → GET reflects → Σ invariant →
DELETE reverts) that always cleans up so the golden stays EQUIVALENT.

## Manual budget — L4 distribution, growth resolution, consolidated entity-sum (Phase 1)

`backend/app/services/budget_service.py` (pure) + the 3 budget readers. Additive;
storage stays **absolute** (growth is resolved to absolute amounts BEFORE write).
GOLDEN-CRITICAL: with NO budget rows every reader returns nothing → byte-identical.

### L4 distribution (`distribute_to_l4`)

Top-down planning: an L3 position value `V` is split across its discovered L4
children by historical share `w[k]` (each L4's prior-year actual annual):

```
share(k) = w[k] / Σ_j w[j]        (Σ share == 1)
out[k]   = V * share(k)           ⇒ Σ_k out[k] == V   (to 1e-6)
```

Fallback: `Σ w == 0` (or all-zero) → uniform `1/n`. Sign preserved (negative `V`
→ negative L4; an opposing L4 weight, e.g. discounts, keeps its negative share).

- **Worked example:** `V=1000`, `w={Gross sales:800, Discounts:-100, Freight:300}`
  (Σ=1000) → `{800, -100, 300}`, Σ=1000 ✓.
- **Edges:** empty weights → `{}`; Σ w == 0 → uniform; `V=0` → all 0; negative `V`
  → signs preserved.

The L4 shares + per-L4 seasonal weights come from `_l4_actual_profiles` (the L4
analogue of `_position_actual_profile`, grouped by `dim_gl_account.level_4` under
the position's level_3). `upsert_cell(distribute_l4=True)` on an L3 edit writes the
L4 rows (each seasonalized by its own profile) and XOR-clears the `''` L3 row, so
the reader's per-`line_code` SUM = Σ(L4) == `V` exactly once (no double count).

### Growth resolution (`resolve_input`)

Resolves a budget input to absolute monthly amounts before write (4 modes):

```
absolute_annual  : out = seasonalize(value, weights)
absolute_monthly : out[p] = value[p]                       (12 values as-is)
growth_annual    : A = base_annual * (1 + value); out = seasonalize(A, weights)
growth_monthly   : out[p] = base_month[p] * (1 + g[p])     (g scalar or per-month)
```

`base` = prior-year actual (`{annual, months}`) supplied by the caller. `value`
for growth modes is a fractional rate (`0.10` = +10%).

- **Worked example:** `growth_annual`, `base_annual=1000`, `value=0.10`, uniform →
  `A=1100`, every month `91.666…`, Σ=1100 ✓. `growth_monthly`, `base={1:100,2:200}`,
  scalar `0.05` → `{1:105, 2:210}`.
- **Edges:** `base_annual=0` + growth → `A=0` (growth off a zero base yields **0**,
  not the rate — surface a note); negative base → sign preserved (`-1000*1.10=-1100`);
  missing weights → seasonalize uniform 1/12; missing growth_monthly base month → 0.

### Consolidated entity-sum (the reader change)

Per-entity is the planning default; the **consolidated** view of a `line_code`
(partner) is its direct `entity_prefix=''` row when one exists (an explicit
consolidated override), ELSE the **SUM of the per-entity rows** (`entity_prefix<>''`):

```
consolidated(line_code) = ''-row            if a '' row exists
                        = Σ entity rows      otherwise
```

A row participates in the consolidated read iff it is a `''` row OR a per-entity
row with no `''` row for the same key (`NOT EXISTS … c.entity_prefix=''`), then the
existing `GROUP BY` sums the survivors. Per-entity reads (`entity_prefix=:ep`) are
unchanged.

- **Worked example:** entity `01` annual 12000 + entity `02` annual 8000, no `''`
  row → consolidated 20000. Add a `''` row of 25000 → consolidated 25000 (override).
- **Golden-safety:** the clause only changes how EXISTING budget rows aggregate;
  with no budget rows the `scenario='budget'` WHERE matches nothing → empty → readers
  fall through to forecast/plan → byte-identical (`compare live v2` EQUIVALENT).

Applied at the 3 sites: `fin_compat_sql.position_plan_grain_sql` (keyed by
`line_code`), `balance_sheet._fetch_bs_budget_movements` (keyed by `line_code, fy,
period` then delta-encoded), and `budget_service._read_budget_position_months` (the
GET overlay, keyed by `line_code, partner_id`).

### Regression tests

`backend/tests/test_budget_distribute_growth.py` (DB-free pure-math + opt-in v2
round-trip): `distribute_to_l4` (Σ==parent, share fidelity, uniform fallback,
sign, empty, zero/negative parent); `resolve_input` (all 4 modes, base=0, negative
base, per-month + scalar growth, missing base month, unknown mode); consolidated
entity-sum SQL shape + a v2 round-trip (Σ entities == consolidated; `''` override
beats sum; no rows → empty; top-down distribute_l4 XOR) that ALWAYS cleans up so v2
ends with NO budget rows. `backend/tests/test_budget_readers.py` extends the SQL-shape
assertions for all 3 reader sites (consolidated + per-entity branches).

## Finssentials budget heuristics — prior_year / trend_cagr / run_rate (Phase 2)

`backend/app/services/budget_heuristics.py` produces a per-position **suggestion**
(presented annual + seasonal weights + an explanation) the user can review, `Apply`
(materialise via `budget_service.seed_budget(materialize_suggestion=True)`) and then
adjust. All three operate on a bounded actuals map `{fiscal_year: {period: presented}}`
pulled once (`_position_actuals_by_fy`, ≤ 6 full FYs strictly before the plan year,
one grouped query, same sign convention as `_position_actual_profile`). Every result is
`{suggestion_annual, weights(Σ=1, uniform 1/12 fallback), explanation}`, so
`seasonalize(suggestion_annual, weights)` reconciles to the annual.

**COMPLETE-FY rule (no partial/YTD year as a base or in the CAGR window).** A fiscal
year is COMPLETE iff it is strictly BEFORE the current (latest-ledger-anchor) fiscal
year `current_fy` (equivalently: all 12 monthly buckets present). The bounded pull
reaches FYs strictly before the PLAN year, but `plan_year-1` may still be the
in-progress year when the ledger is mid-close (e.g. FY2026 plan, fy_hi=2025, data only
Jan..Jul 2025) — summing it over 12 periods gives a PARTIAL "annual". `prior_year` and
`trend_cagr` DROP any `fy >= current_fy` (`_complete_actuals`); `run_rate` keeps the
full map (it annualises the latest months, partial year included, by design). The
dispatcher derives `current_fy` from `latest_anchor` (falls back to the plan year when
the ledger is empty). Bug fixed: a FY2026 plan previously returned `base_fy=2025`,
`base_annual=-63,433,637` off the partial FY2025 — now `base_fy=2024` (the last full FY).

**(1) prior_year (primary).** `base` = last **COMPLETE** FY actual annual; `g` = growth (default 0.0):

    suggestion_annual = base * (1 + g)
    weights           = base FY seasonal profile

- Worked example (FY2026 plan, current_fy=2025): complete FY2024 = 1000 (partial FY2025
  excluded), g = 0.10 → 1000 × 1.10 = **1100.0**; g = 0 → 1000.
- Edges: no COMPLETE FY → 0.0 + note; base = 0 → 0.0 (growth off zero is 0, not the rate);
  negative base → sign preserved (−1000 × 1.10 = −1100).
- explanation: `{method:'prior_year', base_fy, base_annual, growth_pct, season_source}`.

**(2) trend_cagr.** Over the last `n` **COMPLETE** FYs (chronological annuals `A_first … A_last`;
the partial in-progress year is excluded from the window):

    cagr              = (A_last / A_first) ** (1 / (n - 1)) - 1
    suggestion_annual = A_last * (1 + cagr)
    weights           = last COMPLETE FY seasonal profile

- Worked example (FY2026 plan, current_fy=2025): complete FY2022=100, FY2023=110,
  FY2024=121 (n=3; partial FY2025 excluded) → cagr = 1.21^(1/2) − 1 = 0.10 → suggestion =
  121 × 1.10 = **133.1**. The partial FY2025 is never `A_last`.
- Edges: only 1 COMPLETE FY (rest partial) → fall back to prior_year off it (note); no
  COMPLETE FY → prior_year path → 0.0 + note; `A_first = 0` or a sign change first↔last →
  ratio undefined → cagr = 0 → suggestion = `A_last` (note); both negative → positive
  ratio → real cagr, sign preserved (−100→−121 → cagr 10% → −133.1).
- explanation: `{method:'trend_cagr', base_fy, base_annual, cagr, window:[first,last],
  window_years, first_annual, last_annual, season_source}`.

**(3) run_rate.** Annualise the last `k` OBSERVED months (chronological, only trailing the
last booked month per FY) with values `m_i` — a RUN-RATE, not a complete-FY base (it
intentionally includes the partial year's latest months):

    run_rate_sum      = Σ_{i=1..k} m_i
    suggestion_annual = run_rate_sum * 12 / k
    weights           = last FY seasonal profile

- Worked example: last 3 observed months 100, 120, 140 → Σ = 360 → 360 × 12/3 = **1440.0**.
- Edges: fewer than k observed → use k′ < k (note); no observed months → 0.0 + note;
  negative months → sign preserved.
- explanation: `{method:'run_rate', base_fy, window_months, observed_months, run_rate_sum,
  season_source}`.

`build_grid` attaches `suggestion` (+ `explanation`) to every position via
`budget_heuristics.suggest` (default `prior_year`, +0%; `heuristic`/`growth_pct` params drive
the chooser mode). "Apply" = `seed_budget(materialize_suggestion=True, heuristic, growth_pct)`
— writes the suggested L3-level (`''`) rows, idempotent (UPSERT), XOR-honoured (a later L4
edit clears the `''` row). The legacy synthetic seed (`materialize_suggestion=False`, default)
is byte-identical to before, so the golden stays EQUIVALENT with no budget rows.

Tests: `backend/tests/test_budget_heuristics.py` — pure prior_year/trend_cagr/run_rate
(base×(1+g), CAGR worked example 100→121→133.1, run-rate last-3 ×12/3, no-history → 0 + note,
sign preservation, weights Σ=1, explanation fields) + a monkeypatched-DB dispatcher.

## Anomaly signal score + chart stats — presentation overlay (anomaly rework v2, Phase 0)

`backend/app/services/anomaly_score.py` + new pure helpers in
`gl_outliers.py`, hooked into every node `payload` by `gl_anomaly_tree.py`. This is
**presentation logic, not a financial KPI**: it derives a laymen-friendly score +
chart stats from the EXISTING per-account/aggregated monthly z-score series. No
reported revenue/cost/margin/period changes; sign convention is owned upstream by
`gl_analysis_common` (PL revenue +, BS assets +). Analyses are cached + not in the
golden catalogue → `compare live v2` stays EQUIVALENT (exit 0). The
`ALGORITHM_VERSION` bump (`gl_anomaly_tree_v1` → `_v2`, which also composes into
`anomaly_compute.OVERVIEW_ALGORITHM_VERSION`) invalidates `anomaly_snapshot_cache`.

### Signal score 0..100 (`signal_score_0to100(z)`) + band

Monotonic map of `|z|` onto 0..100, piecewise-linear in three unit-σ bands then
saturated:

```
|z| < 1        score = round(|z| * 33)                 #   0 ..  33
1 <= |z| < 2   score = round(33 + (|z| - 1) * 33)      #  33 ..  66
2 <= |z| < 3   score = round(66 + (|z| - 2) * 33)      #  66 ..  99
|z| >= 3       score = 100
band:  score < 33 → 'low' ; 33..65 → 'medium' ; >= 66 → 'high'
```

0 = normal, 100 = extreme (>= 3σ); a RELATIVE unusualness indicator, **not** a
probability. Sign-agnostic (`|z|`), aligning with the outlier `|z| >= σ` rule.

**Worked example.** z=0 → 0; z=1 → 33; z=1.5 → 50; z=2 → 66; z=2.5 → **82**
(`round(82.5)`, Python banker's rounding — the spec's eyeballed 83 differs by 1, the
test locks 82); z=3 → 100; z=5 → 100; z=−2 → 66.

**Edge cases.** z=0/flat → 0/'low'; negative z uses `|z|`; NaN/inf/None → 0
(defensive, never raises); `|z|` just under a band edge stays in the lower band but
the rounded score reaches the edge a hair early (`round(0.99*33)=33`) — monotonic
either way; `|z| >= 3` saturates at exactly 100.

### Linear regression trend (`compute_linear_regression(values)`)

Least-squares `y = slope·x + intercept` over `x = 0..n-1` (one step per month):

```
slope/intercept = numpy.polyfit(x, y, 1)
r_squared       = 1 − SS_res/SS_tot   (SS_tot = Σ(y−ȳ)²; 0.0 when SS_tot==0)
trend_start     = intercept            (ŷ at x=0)
trend_end       = slope·(n-1)+intercept (ŷ at x=n-1)   # two points draw the line
```

**Worked example.** `[0,10,20,30,40]` → slope 10, intercept 0, r²=1, trend_start 0,
trend_end 40.

**Edge cases.** n<2 → all zeros (n=1 → flat line at the value), r²=0; flat series →
slope 0, intercept = value, r²=0 (no variance to explain, no 0/0); negative slope
preserved.

### Histogram (`compute_histogram(values, bins=10)`) + distribution

`numpy.histogram` over `[min,max]` into equal-width bins → `[{bin_lo,bin_hi,count}]`
with **Σ count == n** (last bin right-edge inclusive). Degenerate (n<2 or all equal)
→ one bin holding all n. `distribution_stats` → `{min,max,median,q1,q3,iqr}` via
numpy percentiles (skew/kurtosis omitted — `scipy` is not a backend dep).

**Worked example.** histogram `[0..9]` bins=10 → 10 bins each count 1, Σ=10;
distribution `[1,2,3,4,5]` → min 1, q1 2, median 3, q3 4, max 5, iqr 2.

**Edge cases.** empty → `[]` / all-0.0; single value → min=max=median=q1=q3=v, iqr 0.

### Node payload wiring (`gl_anomaly_tree._enrich_payload`)

Every node (account + L3/L4 aggregate, and per-entity split) now carries:
node-level `signal_score`+`band` (from `max_abs_z`), per-series-point `signal_score`
(from each point's `z`), and `payload.regression`/`histogram`/`distribution` over the
node value series **only when n >= 6** (`MIN_STATS_POINTS`), else `null`. `max_abs_z`
and per-point `z` are RETAINED in the payload (internal / back-compat; UI hides them).

### Regression tests

`backend/tests/test_anomaly_score.py` (score monotonicity, worked-example anchors,
saturation, sign-agnosticism, non-finite→0, band boundaries 33/66) and
`backend/tests/test_anomaly_regression_histogram.py` (regression on a known line +
flat + n<2 + negative slope; histogram Σ count==n + degenerate; distribution
percentiles; node payload carries score/band/per-point-score/regression/histogram/
distribution when n>=6 and null when n<6; `max_abs_z`/`z` retained). Golden:
`compare live v2` EQUIVALENT exit 0.

## Cash Flow mapping library — `dim_gl_cf` reference data (CF restore)

The indirect-method Cash Flow reads GL movements grouped by `dim_gl_cf.cf_mapping`
(the leaf the flat CF structure matches), sign `presented = amount * -1` (income +,
asset increase −, liability increase +). `dim_gl_cf` is **derived reference data**
(one row per `account_number_group, fiscal_year`); when empty the CF join matched
nothing and the whole statement fell to 0. It is repopulated from a reusable,
accumulating **`cf_mapping_library`** (migration `0017`, PK `(key_kind, key_1,
key_2)`) keyed by the *classification* (not the account number), so any project
whose accounts carry the same classification auto-matches.

### Two library sources + column correspondence (verified)

- **BS / Net-asset side** (`key_kind='na'`): from `CF Mapping.xlsx` sheet `Tabelle1`,
  keyed by `(NA, NA Description)` == `dim_gl_na (l6_na_mapping, l7_na_description)`.
  Columns: `l1←'L11 CF 1'`, `l2←'L11 CF 1.2'` (`l2_sort←'L11 CF 1.2 sort'`),
  `l3←'L11 CF 1.3'` (`l3_sort←'L11 CF 1.3 sort'`), `l4←'L12 CF 2'`, `l5←'L13 CF 3'`,
  `cf_mapping←'CF Mapping'` (verified against `etl.mapping_account.CF_FIELDS` +
  `fin_compat_cf_sql` which groups by `cf.l1/l2/l3` and the `cf_mapping` leaf).
- **P&L side** (`key_kind='pl_level3'`, `key_2=level_3`): DERIVED from the existing
  income-statement structure (the workbook covers ONLY the BS/NA side).

### P&L → CF leaf formula (derived, NOT invented)

The P&L EBITDA calc row (sort 10) is the running Σ of every P&L `mapping` line above
it, so the EBITDA build-up maps to the `EBITDA` leaf and reconciles **by
construction** to the statement EBITDA:

```
EBITDA leaf  = Σ amount over level_3 ∈ {Net sales, Δ Finished goods & WIP,
               Own work capitalised, Cost of materials, Personnel expenses,
               Other operating income, Other operating expenses}   (all ABOVE sort 10)
Depreciation & amortisation  = level_3 'Depreciation & amortisation'   (sort 11)
Financial result  = level_3 ∈ {Interest income, Income from investments,
                               Interest expenses, Write-offs on financial assets}
Taxes on income   = level_3 'Taxes on income'
Other taxes       = UNMAPPED (excluded — see below)
```

**`Other taxes` is intentionally excluded** (no CF structure leaf; it sits below
EBITDA between EBT and Net profit). Folding it into EBITDA would break
`CF EBITDA == P&L EBITDA`; folding it into `Taxes on income` would break
`Gross cash flow == EBITDA + Taxes on income`. Confirmed by the
financial-calculation-engineer. Sign convention unchanged (single `× −1` in SQL).

### Worked example (cm, presented; from the engineer ruling)

Net sales +1000, Δ FG&WIP +50, Cost of materials −400, Personnel −300, Other op
income +30, Other op expenses −80 → CF `EBITDA` = **+300** == P&L EBITDA. D&A −120 →
`Depreciation & amortisation`. Interest income +10, Income from investments +15,
Interest expenses −40 → `Financial result` = **−15**. Taxes on income −100 →
`Taxes on income`. Other taxes −5 → in **no** leaf. `Gross cash flow` = EBITDA +
Taxes = 300 − 100 = **+200**.

Live recon (FY2025, month 7): CF EBITDA YTD = **5,653,120.22** == P&L EBITDA YTD;
Gross cash flow YTD = 5,106,235.07; Net cash flow YTD = −13,479,020.15 (non-zero).

### Duplicate / gap handling + extensibility

- Ambiguous `(NA, NA Description)` workbook keys (e.g. `ND/Shareholder loans`,
  `ND/Loan RCLB to BUB` — same key, different `l4/l5` side label, identical
  `cf_mapping`) are resolved deterministically (keep the row whose `L13 CF 3` sorts
  first) and LOGGED. The matched leaf is identical, so CF AMOUNTS are unaffected.
- Two real `dim_gl_na` classifications absent from the workbook
  (`Equity/Profit distribution`, `ND/Provisions for onerous contracts`) are filled
  **by workbook precedent** (`source='na_gap_precedent'`) — reusing an existing
  Equity / same-description row, never inventing a leaf. Result: **0 unmatched NA**.
- Add future mappings: extend the workbook (NA) or `_PL_LEVEL3_TO_CF` (P&L) and
  re-run `load_cf_mapping_library.py` (idempotent UPSERT) + `populate_dim_gl_cf.py`.

### Scripts + golden

`backend/scripts/load_cf_mapping_library.py` (seed/extend, re-runnable) →
`backend/scripts/populate_dim_gl_cf.py` (idempotent set-based UPSERT joining
`dim_gl_na` and PL `dim_gl_account.level_3` → library; logs unmatched, reports
counts). Run identically on `finssentials_v2` AND `Finssentials` (live) — the
reference data is shared, so `dim_gl_cf` is byte-identical across both DBs
(SHA-256 verified) and `golden_snapshot.py compare live v2` stays EQUIVALENT.

### Regression test

`backend/tests/test_compat_cf.py::TestCfReconciliationV2` (`DB_NAME=finssentials_v2`):
`dim_gl_cf` non-empty; CF EBITDA (cm + YTD) == P&L EBITDA; `Gross cash flow =
EBITDA + Taxes`; Net cash flow non-zero == Σ all mapped leaves; year-grain EBITDA
non-zero. (Recommended add: a negative assertion locking `Other taxes` exclusion.)

## Checklist before approving a financial change

- [ ] Formula written out and matches FDD intent
- [ ] Worked numeric example included
- [ ] Edge cases enumerated (zero/negative/missing/partial period)
- [ ] Sign convention stated and unchanged (or change justified + tested)
- [ ] Regression/golden test added and passing
- [ ] Reuses `funktionssammlung` helpers instead of re-deriving
- [ ] Units (EUR vs kEUR) consistent in the output
