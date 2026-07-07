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

### File-OB fiscal year & posting date (no formula/sign change)

A file-OB upload carries only **account number + amount** — no posting date and no
transaction number. So `apply_profile(..., opening_balance=True)` makes those two
columns optional **for the OB path only** (GL stays strictly required). The fiscal
year is sourced **explicitly**, never derived from a (missing) date:

- **first-year mode** → `fiscal_year` is **fixed** to the project's first GL year.
- **all-years mode** → `fiscal_year` comes from a mapped **fiscal-year column**.

`from_date` fiscal-year mode is rejected for OB (there is no date to derive from).
The `posting_date` is then **synthesized as Jan 1 of that fiscal year** in
`opening_balance_commit` (the opening stock is read via `MIN(posting_date)` per
account). Opening stock belongs to the **first year only**; later years are **not**
re-loaded from a file — they carry forward from the prior year's GL closings
(`carry_forward` mode below). No sign or formula change.

### File-OB number-format parse rule + magnitude guard (no sign change)

A file-OB upload's amount column may arrive in **US** (`52803.84`) or **German**
(`52.803,84`) number format. Parsing US amounts with a German profile (strip `.` as a
thousands separator) silently inflates them by orders of magnitude (`52803.84` →
`5280384` → with more digits ~`5.3e16`), which overflows `fact_gl_line.amount`
`NUMERIC(18,6)` and used to surface as a generic 500. Two layers protect the
committed amount:

**1. Decimal-separator sniff (correctness).**
`etl/mapping.py::sniff_decimal_separator(values) -> (decimal, thousands) | None` votes
on a sample of amount-column strings (currency/sign stripped). Per value:

- both `.` and `,` present → the separator whose **last occurrence is rightmost** is
  the decimal, the other is thousands;
- only one separator, occurring **>1×** → thousands grouping only (no vote);
- only one separator, **exactly once**, trailing-digit count **≠ 3** → that char is the
  decimal;
- only one separator, exactly once, **exactly 3 trailing digits** → **ambiguous**,
  abstain (e.g. `1,234` could be grouped `1234` or decimal `1.234`);
- no separator → no vote.

Strict majority wins; a tie or all-abstain sample → `None`. In `opening_balance_commit`'s
loader (OB path only) the resolution is **layered**: a confident amount-column sniff
wins, else the delimiter/dialect guess, else `.`. GL and German DATEV parsing are
byte-identical to before (sniff is wired on the `opening_balance=True` path only).

Worked examples (input → resolved `(decimal, thousands)` → committed `amount`):

| input                | sniff        | committed amount |
|----------------------|--------------|------------------|
| `52803.84` (US)      | `('.', ',')` | `52803.84`       |
| `52803.840000000004` | `('.', ',')` | `52803.84`       |
| `52.803,84` (DE)     | `(',', '.')` | `52803.84`       |
| `1.234.567,89` (DE)  | `(',', '.')` | `1234567.89`     |
| `1,234,567.89` (US)  | `('.', ',')` | `1234567.89`     |
| `1,234` (ambiguous)  | `None` (abstain) | — falls to layered default |

**Edge cases:** empty/`None`/whitespace → abstain; pure thousands grouping
(`1.234.567`, `123,456,789`) → abstain (no false decimal vote); mixed sample → majority.
Known limitation: an **all-abstain** column (every value single-separator with exactly
3 trailing digits, e.g. only `52.803`-style whole-thousands German integers) cannot be
disambiguated from the amount column alone and falls through to the layered default —
by design, and backstopped by the magnitude guard below.

**2. Magnitude guard (defensive, `ingest.py::_assert_amount_magnitude`).**
Immediately after `_assert_finite_amounts`, before the DB insert, every parsed OB amount
must satisfy `|amount| < 1e12` (`_OB_AMOUNT_ABS_LIMIT`). Rationale: `NUMERIC(18,6)` =
18 total / 6 fractional digits → **12 integer digits**, so Postgres rejects `|amount| >=
1e12`. The largest amount in the real reference OB file is ~`5.29e7`, so the `1e12` bound
sits **>4 orders of magnitude** above any legitimate figure — it rejects nothing real and
turns a future mis-parse into a clear **422**: *"An opening-balance amount looks mis-scaled
and is too large to store (check the file's number format / decimal separator). Example
value(s): …"* (plain English, passes the frontend humanizer through unchanged instead of
the generic "Something went wrong"). The happy path for in-range amounts is untouched. No
sign convention or formula changes.

Regression tests: `etl/tests/test_mapping.py::TestSniffDecimalSeparator` (sniff + parse
roundtrip); `backend/tests/test_ob_amount_magnitude.py` (guard returns 422, not 500;
in-range US/German amounts pass).

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
accumulating **`lib_cf_mapping`** (migration `0017`, renamed from `cf_mapping_library`
in `0019`; PK `(key_kind, key_1,
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

## NA mapping library — name-keyed classification + totals-guard re-derive

The Net-asset / Working-capital classification `dim_gl_na (l6_na_mapping,
l7_na_description)` was per-account; it is now a reusable LIBRARY keyed by
`dim_gl_account.account_name`, resolved by MOST-FREQUENT, with a per-account
`ovr_na_mapping` pin (precedence). Migration `0018_na_mapping_library` (single head
after 0017) creates the library + override tables EMPTY (no-op, golden
unaffected); migration `0019` renames them to `lib_na_mapping` + `ovr_na_mapping`. Seed: `backend/scripts/load_na_mapping_library.py`; re-derive:
`backend/scripts/populate_dim_gl_na.py` (then re-runs `populate_dim_gl_cf.py`).

This is **classification/presentation only** — no new KPI/sign/period formula.
Its safety property is *roll-up totals unchanged*: sub-line reclassification is
allowed, but NWC + every WC/CF subtotal & grandtotal stay identical.

### Resolver (`etl/mapping_library/resolve.py::resolve_most_frequent`)

```
winner = argmax_over_rows( occurrences )       # most-frequent (name, mapping)
ties broken by  min (na_mapping, na_description)  lexicographic   # 50/50 stable
```

Pure, deterministic, order-independent → a 50/50 loan split never flips between
runs. Worked: `[(OWC,Other assets,8),(ND,Loan to employees,4)] → OWC/Other
assets`; tie `[(OWC,Liab. due to affiliates,4),(ND,Loan to BSG Seifersbach,4)] →
ND/Loan to BSG Seifersbach` (ND < OWC).

### Totals-guard (`populate_dim_gl_na.changes_total`)

For each account: final = `ovr_na_mapping` if present, else most-frequent(library).
A resolution that would CHANGE a total is auto-pinned to the account's CURRENT
mapping (`ovr_na_mapping`, `source='totals_guard'`). A total changes iff:

```
WC membership flips:  (cur_l6 ∈ {TWC,OWC}) != (new_l6 ∈ {TWC,OWC})   # NWC + WC subtotals
   OR CF band differs: cf_lib[cur_l6,cur_l7].(l1,l2) != cf_lib[new].(l1,l2)  # CF subtotals
```

(`NWC = Σ TWC + Σ OWC`, `fin_compat_wc_sql` filters `l6 IN ('TWC','OWC')`; CF
subtotals group on the `lib_cf_mapping` band `(l1,l2)`.)

**Worked example (real data).** Account `03026135` "Darlehen Arbeitnehm." is
currently ND/"Loan to employees" (NOT in WC). Its name's most-frequent mapping is
OWC/"Other assets" (occ 8 > 4). Blind resolution ND→OWC would pull it INTO working
capital → NWC changes. The guard detects the WC-membership flip, writes an
`ovr_na_mapping` pinning `03026135` to ND/"Loan to employees" → NWC unchanged.

### Verified result (v2 + live, FY2025/Jul, 5 entities + consolidated)

331 distinct names; 20 ambiguous; 8 names guarded (the 3 known loans
`Darl. Bet. GmbH an RC HS`, `Darl. RC BR an BSG Seifersbach`, `Darlehen
Arbeitnehm.` + `Delcredere`, `MwSt-Zahllast`, `unreal. MWSt AR 19 %`,
`Gewerbesteuerrückst`, `KöSt. Rückstellung`). Per account: 2900 unchanged, 68
safe-reshuffled, 40 totals-guard pinned, 0 no-library. **164 WC/CF
subtotal/grandtotal rows compared BEFORE vs AFTER on v2 → IDENTICAL to the cent;
live-vs-v2 → EQUIVALENT** (both DBs re-derived by the identical pipeline). CF
`[unmatched NA]=0` on both.

### Edge cases

* identical mapping → never changes a total (short-circuit, no override).
* within-WC reshuffle, same CF band (e.g. TWC Inventories ↔ TWC Advance payments)
  → safe, applied.
* within-WC reshuffle, different CF band → CF total would change → pinned.
* no library precedent for a name → keep current (cannot change a total).
* idempotent: re-running re-counts the library and re-resolves; guard overrides
  persist (UPSERT), so a second run is a no-op on totals.

### Regression test

`etl/tests/test_na_mapping_library.py` (DB-free, synthetic): `resolve_most_frequent`
(argmax + lexicographic tiebreaker + order-independence + dict/NamedTuple/missing
counts), `changes_total` (identity, WC-flip, within-WC same-band safe, within-WC
different-band, the 3 loan crossers), override precedence, and NWC-membership
identity on a small fixture (guarded crosser stays out of WC; safe name reshuffles).
Plus the DB-backed `backend/tests/test_compat_cf.py::TestCfReconciliationV2`
(green on the re-derived v2) and `test_compat_wc.py`.

## Account mapping library — fill missing (account, year) + exclusive mode (migration 0021)

Classification/presentation only — sets `dim_gl_account.level_*` hierarchy + sorts
for fiscal years a project's mapping file never covered.  Introduces NO new KPI /
sign / period formula.  Safety property: **additive no-op equivalence** (only
INSERTs missing rows, never mutates an existing one), locked by the golden gate.

### Tables (mirror the NA library, migration 0018/0019)

* `lib_account_mapping` — one row per OBSERVED `(account_name, level_0..4, l4_sub,
  sorts, is_ic)` with an `occurrences` COUNT, PK `(account_name, level_0, level_2,
  level_3, level_4)`.  Seeded by `backend/scripts/load_account_mapping_library.py`
  (GROUP BY over the current `dim_gl_account`).  Name-keyed because the hierarchy
  is a property of the business account, recurring across entities/years.
* `ovr_account_mapping` — per-account PIN `(account_number_group, fiscal_year)`,
  PRECEDENCE over the library (the future Project-Setup reclassification writes here).

### Resolver (pure) + fill

`etl.mapping_library.resolve.resolve_account_most_frequent` — `winner =
argmax(occurrences)`, deterministic tiebreaker on the smallest `(level_0, level_2,
level_3, level_4)` (the library PK), so the winner is stable regardless of row order.

`etl.account_fill.fill_missing_account_rows` — for every `(account, fy)` posted in
`fact_gl_line` with NO `dim_gl_account` row: `ovr_account_mapping` pin else the
resolver over `lib_account_mapping[account_name]`.  The `account_name` is borrowed
from any existing dim row of the SAME `account_number_group` (latest year); no name
or no library precedent → skipped + logged (never mis-classified).  Wired as
`rebuild.py` stage 1b (after the CoA-override replay, before partner backfill).

### Exclusive mode (the gate)

`admin_project_config.config -> account_mapping_mode ∈ {'library','exclusive'}`
(default `'library'`, resolved by `project_config.resolve_rebuild_flags`).  When
`'exclusive'` the fill stage is SKIPPED — only the provided per-(account, year)
mapping is used; missing years stay unmapped.

### Worked example

Account `01001200` "Trade receivables" is mapped `BS/Assets/Current assets/
Receivables` in FY2023 only, but has GL postings in FY2024 too.  Library mode fills
FY2024 with the SAME hierarchy (most-frequent by name, occurrences 9).  An
`ovr_account_mapping` pin to `Non-current assets/Loans` would win instead.  In
exclusive mode FY2024 is left unmapped.

### No-op / golden guarantee

On v2/live the mapping already covers every year and the FK `fact_gl_line ->
dim_gl_account` guarantees every posted `(account, fy)` already has a dim row, so
the "missing" set is EMPTY → the fill INSERTs 0 rows → `dim_gl_account` byte-
identical → `compare live v2` EQUIVALENT.  Verified: seed loads 1214 rows
identically on v2 and live (1155 names, 57 ambiguous); the fill dry-run reports 0
missing keys on both; `dim_gl_account` stays 11 328 rows with 0 `account_library_fill`
rows.  Idempotent (a second run finds nothing missing).

### Regression test

`backend/tests/test_account_mapping_library.py` (DB-free pure resolver + SQLite
synthetic fill): resolver argmax/tiebreaker/coercion; FY1-only → FY2 filled from
the library (worked example); override precedence; exclusive gate leaves FY2
unmapped (asserted against `rebuild._stage_account_library_fill`); additive (existing
row never mutated) + idempotent re-run fills 0; no-name / no-precedent skipped.

## AR/AP OPOS As-of Aging (F3/F4) — 2026-07-01

> **Authoritative spec for the OPOS subledger as-of (Stichtag) aging rebuild.**
> Supersedes the deferred F3/F4 draft stubs below. Method pinned + reconciled by
> the architect; formulas + worked example + edge cases + tests below satisfy the
> iron rule. Backend SQL is Phase 2 — it MUST match the executable reference in
> `backend/tests/test_opos_aging_financial.py`.

Source: `fact_opos` (debitor = AR, kreditor = AP), one row per subledger line with
`entity, partner_key (Debitor/Kreditor), konto, satzart, belegart, buchungsdatum,
nettofaelligkeit, betrag (amount_hauswaehrung), fy_label`. **Sign convention
(unchanged, matches the source ledger):** AR open item is a DEBIT → stored
**positive**; AP open item is a CREDIT → stored **negative**. Scope = **trade LuL
only**: AR konto `24xxx` family, AP konto `36xxx` family.

`Satzart ∈ {Vortrag (opening carry), Bewegung (RV invoice / ZA payment / SA / RG),
Fact-Ergaenzung (fact-linkage tie to net sales), Bilanzabstimmung (balance plug)}`.
`Belegart`: `RV`/`RG` = invoice, `ZA` = payment, `SV` = Saldovortrag.

### F1 — As-of open balance (Method A)

Open balance at Stichtag `S`, at `(entity, partner_key, konto)` grain:

```
net_open[e, p, k] = Σ betrag  over fact_opos rows
                    WHERE fy_label = year(S) AND buchungsdatum <= S
                    summing ALL Satzart (Vortrag + Bewegung + Fact-Ergaenzung + Bilanzabstimmung)
```

**Fiscal-year-anchored — never sum across years.** Each year's file carries its own
`Vortrag` = the prior-year close, so the within-year sum already includes the
opening stock. The two naive alternatives are WRONG:

- **Sum all years `<= S`** double-counts: year N's `Vortrag` re-includes year N-1's
  close, so adding both books the opening stock twice.
- **Sum only `Bewegung`** drops both the `Vortrag` opening and the `Fact-Ergaenzung`
  tie rows → understates the balance.

**Worked example (real data — Atlas AR 2024, konto `24xxx`, `S = 2024-12-31`):**

| Satzart          | Σ betrag (EUR)   |
|------------------|------------------|
| Vortrag          | +6,047,183.63    |
| Bewegung         | −4,114,067.13    |
| Fact-Ergaenzung  | +5,850,743.68    |
| **Method A total** | **+7,783,860.18** |

Ties to the reconciliation `Bilanz_AR_AP` GoBD balance (konto `24000` = +7,796,548.22;
`24905` other-AR = −12,688.04; family = 7,783,860.18). Naive "Bewegung only" =
−4,114,067.13 (wrong); "sum years ≤ S" = 5,897,923.10 (2023 close) + 7,783,860.18 =
13,681,783.28 (double-counts). Atlas AP 2024 (konto `36xxx`) Method A ties to
`Verbindlichkeiten LuL` = −15,683,208.09 (credit, stored negative).

### F2 — FIFO aging attach

The ledger is a running balance with **no invoice↔payment clearing key**, so aging
attaches by FIFO. Per partner group, allocate the Method-A net-open **magnitude**
(`+net_open` for AR, `−net_open` for AP) across that group's **invoice-type rows**
(`Belegart ∈ {RV, RG}`, this FY, `<= S`) ordered OLDEST-first by
`due = COALESCE(nettofaelligkeit, buchungsdatum + terms)`, `terms = 30d AR / 45d AP`.
Each residual slice buckets via the existing `gl_aging.AR_BANDS` boundaries
(`_band_case_sql`: `not_yet_due`, `1–30`, `31–60`, `61–90`, `91–180`, `>180`;
`due == S` falls to `>180` — mirrored from the SQL). **By construction Σ buckets =
total_open and overdue_open ≤ total_open.**

- **Assumption A4 (flagged):** the alternative pro-rata split (spread net-open across
  invoices by amount share) is NOT used; FIFO oldest-first is the pinned method.
- **Assumption A5 (flagged):** net-open that current-FY `RV/RG` invoices cannot cover
  is the carried-forward opening (`Vortrag`, `Belegart SV` — outside the FIFO pool).
  It predates the fiscal year, so the uncovered residual buckets into
  **`overdue_over_180`**. Keeps Σ buckets = total_open.

**Worked example (synthetic partner P1, AR, `S = 2024-12-31`):** net-open 600
(= Vortrag 100 + RV 500 + RV 300 − ZA 350 + Fact 50). Oldest invoice RV 500
(due 2024-07-01, 183d overdue) → 500 to `>180`; next RV 300 (due 2025-01-14) absorbs
the remaining 100 → `not_yet_due`. `total_open = 600, overdue_open = 500`.

### F3 — Overdue % (per customer AND page-level) — the 237% fix

```
overdue_pct = clamp( 100 × overdue_open / total_open , 0, 100 )     # total_open > 0
overdue_pct = 0                                                     # total_open <= 0
```

Both numerator and denominator come from the **same FIFO-allocated as-of base**
(F2), so the ratio is ALWAYS in `[0, 100]`. **Root cause of the old 237%**
(0.13k overdue / 0.05k open): overdue and total were computed on *different* bases —
overdue counted whole overdue invoices while total was the shrunken net-open (after a
payment / credit note / advance), so a positive overdue divided a small or negative
denominator and blew past 100%. **Fix, three layers:**

1. both figures from the same FIFO base (F2);
2. a partner whose net-open is a **CREDIT** (negative for AR / positive for AP —
   overpayment, advance, or net credit note) gets `total_open = 0, overdue_open = 0,
   credit_balance = true`, and is **EXCLUDED from the risk-matrix scatter**
   (`in_scatter = false`);
3. `clamp(…, 0, 100)` in the builder output — belt-and-suspenders. The frontend
   consumes an already-bounded value.

**Worked example (synthetic P3, the 237%-class case):** RV 130 overdue, ZA −80 →
net-open 50. OLD broken: 130 / 50 = **260 %**. NEW: FIFO allocates 50 against the
overdue invoice → 50 overdue / 50 total = **100.0 %** (bounded).

### F4 — DSO / DPO

```
DSO = total_AR_open / gross_sales_period × days_in_period
DPO = total_AP_open / purchases_period   × days_in_period
```

Gross sales / purchases come from the GoBD linkage (`Referenz` /
`GoBD_Transaktionsnr` → GDPdU journal). **Assumption A2:** if that journal table is
absent in `finssentials_v4`, fall back to a **payment-term proxy** = README terms:
**AR 30d, AP 45d**. ⚠️ **Regression — current inversion:** `gl_aging.py`
`build_receivables_aging` hardcodes `dso_days = 45.0` and `build_payables_aging`
`dpo_days = 30.0` — **inverted** (README is customer 30d / supplier 45d). Phase 2
must swap to AR 30 / AP 45. Locked by `test_current_gl_aging_proxy_is_inverted_BUG`
(strict `xfail` today → flips green when fixed).

### F5 — Total AR / AP KPI

```
Total_AR (kEUR) = ( Σ net_open over AR trade accounts ) / 1000      # positive
Total_AP (kEUR) = ( Σ net_open over AP trade accounts ) / 1000      # negative (credit)
```

Σ of both positive and negative net-open per partner (a partner-level credit balance
nets DOWN the total — it is NOT floored at 0 for the KPI; the floor-to-0 is only for
the per-partner overdue_pct / scatter of F3). Ties to reconciliation.xlsx
`Bilanz_AR_AP` "Subledger Saldo" per `(entity, year, Position)`.

### Edge cases (tested)

- **Credit balance** (customer overpaid / advance): net-open < 0 (AR) → `total_open =
  overdue_open = overdue_pct = 0`, `credit_balance = true`, excluded from scatter.
- **Missing `nettofaelligkeit`**: `due = buchungsdatum + terms` (30 AR / 45 AP).
- **Partial YTD Stichtag** (e.g. `S = 2025-07-31`): only rows with `buchungsdatum <=
  S` AND `fy_label = 2025` enter — an August invoice is excluded.
- **`not_yet_due`-only partner**: `overdue_open = 0`, `overdue_pct = 0` (no div-by-0).
- **Uncovered carried-forward residual (A5)**: → `overdue_over_180`; Σ buckets =
  total_open preserved.
- **Cross-year contamination**: a `fy_label = 2023` row never enters a 2024 as-of
  balance (fiscal-year anchor).
- **Zero denominator**: `overdue_pct = 0`, never raises.

### Regression test

`backend/tests/test_opos_aging_financial.py` (DB-free, synthetic OPOS fixture +
executable reference helper): Method A per-group (all-Satzart, FY-anchored,
Bewegung-only understatement), FIFO (Σ buckets = total_open, overdue ≤ total,
oldest-first spill, A5 residual), credit-balance zeroing + scatter exclusion,
the 237% bound + clamp helper, DSO/DPO proxy not-inverted (+ strict `xfail`
documenting the current `gl_aging.py` inversion), missing-due / partial-YTD /
not-yet-due-only edges, AP credit-magnitude. `test_phase2_backend_helper_contract`
(`importorskip`) pins the target interface `app.services.opos_aging.compute_opos_aging(
rows, as_of, side) -> {partner: {total_open, overdue_open, overdue_pct, credit_balance,
in_scatter, buckets}}` — skips until Phase 2, then is the acceptance gate.

## Overview Page v2 — KPI sign-offs (Areas 1, 2, 4, 5, 7, 8) — 2026-07-02

> **Owner sign-off (financial-calculation-engineer) for the reporting-v2 Overview
> redesign** (`docs/overview-v2-redesign-plan.md` §2). Documentation + test spec
> only — no service/router code changes here; the math lands in later phases
> (P3/P4/P5) behind the `IS_OVERVIEW_V2` flag. Every formula below reuses an
> EXISTING sign convention (cited per area) or is an approved net-new decision.
> **Area 3 (Fixed Assets) is OUT of scope** and intentionally omitted.
>
> **FAV convention (unchanged, from the plan §2):** `FAV+` = larger-positive is
> favourable (revenue, profit, margin, growth, ROE, DPO); `FAV−` = smaller is
> favourable (cost, DSO, DIO, CCC, days-overdue, lost customers). `FAV−` metrics
> are displayed with `invert_delta=True` at the **findings/colouring layer** so a
> shrinking bad number reads green. NOTE: the existing WC KPI rows
> (`fin_compat_wc._kpi_rows`) and top-entity delta rows emit `invert_delta=False`
> in the raw payload — the FAV direction is applied by the v2 findings layer, NOT
> by mutating those existing rows (keeps legacy payloads byte-identical).

### Area 1 — YoY + vs-Plan performance

**Revenue by entity/region/customer** (source `overview_top_entities` /
`sales_analytics_compat`, sign per `fact_sales.gross_sales = −amount` → positive,
kEUR = Σ/1000). `Rev[dim,w] = Σ gross_sales/1000` over posting_date window `w`;
dims: entity = `LEFT(account_number_group,2)`, region = `sql_end_customer_region_expr`,
customer = `dim_customer.name_line_1`. **`FAV+`, `invert_delta=False`.** Missing
customer → `'Unknown'`/`'(no partner)'` bucket (matches `build_top_entities` L331).
- *Worked (Jun-2026):* A=500, B=120, C=0 kEUR → C dropped (`|Rev|<1e-6`).

**Margin.** Two grains, never blended:
- Gross margin (customer/region grain, from `fact_sales`/`fact_com`, both stored
  positive): `GrossProfit = Rev − COM`; `GrossMargin% = 100·GP/Rev`, `None` if
  `|Rev|<1e-6`. *Worked:* Rev 1000, COM 620 → GP 380, GM **38.0%** ✓.
- EBIT margin (entity grain, from `overview_metrics.build_ebit_table`,
  presented `amount*-1`): `EBITMargin% = 100·EBIT_ytd/|to_ytd|` where `to` = the
  EBIT table "Total output" column = Σ `level_3='Net sales'` (`_ebit_filters`,
  overview_metrics.py L396-403). `None` if `|to|<1e-6`. `FAV+`.
  *Worked (code-faithful):* EBIT 90 / Net sales 1000 = **9.0%**.
  - ⚠️ **PLAN DISAGREEMENT (flagged, not silently changed):** the plan's example
    uses "EBIT 90 / output 1050 → 8.57%", i.e. a *broader* Total-output base
    (Net sales + Δ FG/WIP + own-work-capitalised). The **code today uses Net sales
    only.** Sign-off is for the Net-sales denominator (matches
    `build_ebit_table`). Widening the base to true "total output" is a **formula
    change requiring its own sign-off** — do NOT implement without it.

**YoY % (YTD-aligned).** month → `cm` vs `py_cm`; YTD → `ytd` vs `ytd_py` (equal
month count). `YoY% = 100·(cur−py)/|py|`, `None` if `|py|<1e-6`. Revenue `FAV+`;
cost `FAV−` (`invert_delta=True` at findings layer). Sign-change (`py<0, cur>0`) →
annotate `"sign change"`, do not colour. *Worked:* 500 vs 400 → **+25.0%** ✓.

**vs-Plan.** plan from `fact_position_plan`/`fact_gl_plan`, scenario
`budget→forecast→plan` (same resolution as `_load_partner_plan_cm`,
overview_top_entities.py L161). `Var_abs = actual−plan`; `Var_pct =
100·(actual−plan)/|plan|`; `Coverage% = 100·actual/|plan|` — all `None`+flag
`"no plan"` when `|plan|<1e-6` (never render −100%). Revenue `FAV+`; cost `FAV−`.
*Worked:* 500 vs 450 → **+50, +11.1%, 111.1%** ✓.

**Recent-months 3-trigger alert** (approved defaults: N=3 months, per entity ×
{revenue, gross margin}). Flag month `m` if any fires:
- **T1 (YoY)** `|x_m − x_py| / |x_py| ≥ 0.20`
- **T2 (plan)** `|x_m − plan_m| / |plan_m| ≥ 0.10`
- **T3 (z-score)** `|z| ≥ 2.0`, `z = (x_m − μ)/σ̂`, `μ,σ̂` over trailing-12
  **excluding m**, sample std `ddof=1` (reuses `gl_outliers` convention).

  `severity = #triggers` (≤3); `direction = sign(x_m − ref)`. Skip any trigger
  whose denominator/σ is 0 (no div-by-zero). Alert strip ≤5 items (approved).
  *Worked:* x=60, μ=100, σ̂=10, py=95, plan=90 → T1 35/95=0.368✓, T2 30/90=0.333✓,
  T3 |z|=4.0✓ → **severity 3, "down"**.
- **Test:** `backend/tests/test_overview_yoy.py` (YoY/variance/coverage + None
  guards), `backend/tests/test_recent_month_alerts.py` (3-trigger severity +
  denominator-skip).

### Area 2 — Cash development & liquidity

**Cash level (period-end BS stock).** `Cash[t] = Σ amount WHERE account ∈ CASH_SET
AND posting_date ≤ month_end(t)`, CASH_SET = `dim_gl_account.level_3='Cash & cash
equivalents'` (NO hardcoded konto list — same filter as
`overview_metrics._DUPONT_BS_FILTERS['cash']`, L81). RAW stored sign, **no `*-1`,
no ABS** (an overdraft is legitimately negative). `FAV+`, `invert_delta=False`.
*Worked (cumulative):* Jan 100, Feb 70, Mar 120 kEUR.

**Liquidity available incl. "Zeitverkauf"** (APPROVED net-new monetary logic —
decision #1). Interpret Zeitverkauf as AR realizable over time = AR net of an
aging-based collectibility haircut. Haircut is a **config table** (NOT inline
constants), keyed by the canonical `gl_aging.AR_BANDS` bucket ids:

```
AR_HAIRCUT_LADDER = {         # collectibility haircut per aging band (approved)
    "not_yet_due":      0.00,
    "overdue_1_30":     0.00,
    "overdue_31_60":    0.10,
    "overdue_61_90":    0.25,
    "overdue_91_180":   0.50,
    "overdue_over_180": 1.00,
}
CollectibleAR       = Σ_band AR_band · (1 − AR_HAIRCUT_LADDER[band])
LiquidityAvailable  = Cash + CollectibleAR − OutstandingAP
```

AR bands come from the F2 FIFO as-of aging (`opos_aging.compute_opos_aging`,
`buckets`); a partner whose net-open is a CREDIT is `total_open=0` (already
excluded, `credit_balance=True`) so it never adds negative "collectible". Cash is
the BS stock above; OutstandingAP = Σ AP net-open magnitude (F5). `FAV+`,
`invert_delta=False`.
- *Worked:* AR bands 400/100/50/40/20/10 (not_due…>180) → CollectibleAR =
  400 + 100 + 45 + 30 + 10 + 0 = **585**; Cash 120, AP 300 → LiquidityAvailable =
  120 + 585 − 300 = **405 kEUR** ✓.
- **Edge cases:** empty AR → CollectibleAR 0; all >180 → 0 collectible (100%
  haircut); negative Cash (overdraft) flows through raw (can make liquidity
  negative — surface, do not floor); missing AP → treat as 0; a band id absent
  from the ladder → **raise** (never default a haircut silently). Units kEUR
  throughout.
- **Test:** `backend/tests/test_liquidity_available.py` (worked example to the
  cent, per-band haircut, credit-balance partner excluded, empty/all->180/negative
  cash edges, unknown-band raises).

### Area 4 — Customer development

Source `fact_sales` + `dim_customer`; reuses `rank_top_entities`
(overview_top_entities.py L59) delta formulas verbatim.
- **Biggest customers** — `Rev[cust, ytd|cm]` desc, top-N, drop all-zero
  (`|metric|>1e-6`). `FAV+`.
- **Largest YoY increase** — `ΔYoY = Rev[cur] − Rev[py]` desc (= `delta_cm_py` /
  `delta_ytd` of `rank_top_entities`). New customer (`py=0`) → `Δ=+cur`, reported
  separately as **"won"**. `FAV+`.
- **Lost customers** (APPROVED windows #3): `Rev[prior] ≥ T_material AND
  Rev[current] ≤ ε`; prior `[-24..-13]` mo, current `[-12..-1]` mo,
  `T_material=5 kEUR`, `ε = max(1 kEUR, 0.10·Rev[prior])`. `FAV−`. Aligns with
  `build_churn_bridge` lost = `pm NOT NULL AND cm NULL` (sales_analytics_compat.py
  L733) but with material/ε thresholds. *Worked:* prior 40, current 0 →
  ε=max(1,4)=4, 0≤4 → **lost**; prior 3 (<5) → immaterial; prior 40, current 6
  (>4) → **declining, not lost**.
- **Invoice count / order size** — `InvoiceCount = COUNT(DISTINCT
  journal_entry_group_number)` on `fact_sales` (jegn confirmed present,
  `derive_facts_sql.derive_fact_sales` L47; revenue already isolated by
  `level_3='Net sales'`); `AvgPerInvoice = Rev/InvoiceCount`, `None` if 0. `FAV+`.
  *Worked:* 500 kEUR over 4 distinct jegn → **125 kEUR/invoice** ✓.
- **Test:** `backend/tests/test_customer_development.py` (biggest/increase/won via
  `rank_top_entities`, lost-customer window classification, avg-per-invoice + zero
  guard).

### Area 5 — Supplier development (symmetric, AP side)

Source `fact_com` + `dim_supplier`; `cost_of_materials = +amount` (positive),
kEUR = Σ/1000 (overview_top_entities.py L14). Reuses `rank_top_entities`.
- **Most-delivering suppliers** — `Cost[supplier, ytd] = Σ cost/1000` desc.
- **Biggest cost increase** — `ΔYoY = Cost[cur] − Cost[py]` desc. **`FAV−`
  (`invert_delta=True` at findings layer)** — a rising cost reads unfavourable.
  New supplier (`py=0`) → **"new spend"**.
- **Purchase txns / order size** — `PurchaseTxns = COUNT(DISTINCT
  journal_entry_group_number)` on `fact_com` (**CONFIRMED present**,
  `derive_facts_sql.derive_fact_com` L85; migration `0001_initial_schema`);
  `AvgPerPurchase = Cost/PurchaseTxns`, `None` if 0.
  *Worked:* 620 kEUR over 4 distinct jegn → **155 kEUR/purchase**.
- **Test:** `backend/tests/test_supplier_development.py` (Σcost ranking, cost
  increase `FAV−` invert flag, avg-per-purchase + zero guard).

### Area 7 — Driver / DuPont (findings re-route only)

**Confirmed: `buildDuPontFindings` re-routes EXISTING computed values — no Python
formula change.** It is a frontend selector (`cockpit/dupontNarrativeEngine.ts`)
that turns the already-computed `dupont_period_kpis` metric map
(`overview_metrics.py` L110) into ≤3 finding one-liners with deep-link routes; it
does NOT recompute any KPI. The values it consumes are the **EBIT-based** DuPont
the code already ships:

```
ROS(EBIT margin)   = EBIT / Net sales            (overview_metrics L124, _safe_div)
AssetTurnover      = ann_ns / Total assets       (ann_ns = ns·12/ann_month)
EquityMultiplier   = Total assets / Equity       (leverage — NEUTRAL, not FAV±)
ROE                = EBIT / Equity
ROI                = EBIT / Total assets
```

**Reconciliation the code actually satisfies (LOCKED in test):** at `ann_month=12`
(`ann_ns = ns`), `ROE ≡ ROS · AssetTurnover · EquityMultiplier` exactly (algebraic
identity on unrounded inputs, within 1e-6). *Worked:* EBIT 150, NS 1000, Assets
1250, Equity 500 → ROS 15%, AT 0.80, EM 2.5 → 0.15·0.80·2.5 = 0.30 = ROE
150/500 = **30%** ✓. **`FAV+`** for ROE/ROS/ROI; EquityMultiplier neutral.
Denominator `<1e-6` → that KPI `None` (`_safe_div`).

- ⚠️ **PLAN DISAGREEMENT (flagged, do NOT silently implement):** the plan's Area 7
  specifies a **net-income-based** DuPont — `NetMargin = NI/Rev`, `ROE = NI/Equity`
  (worked NI 90 → ROE 11.25%) — and a new **`ROCE = EBIT/(Assets − CurrentLiab)`
  = 8.0%**. Neither exists in the code: `dupont_period_kpis` has **no net income**,
  its "ROS" is an **EBIT** margin, and `_DUPONT_BS_FILTERS` has **no
  current-liabilities aggregate** (only `total_assets`, `equity`, `trade_payables`,
  …). Introducing NetMargin/NI-ROE and ROCE is **net-new monetary logic** →
  **stop-and-ask / separate sign-off** (needs a `current_liabilities` BS filter +
  a net-income figure). Until then the DuPont findings block re-routes the
  **existing EBIT-based** values and locks the EBIT identity above.
- **Test:** `backend/tests/test_dupont_drivers.py` (EBIT identity reconciliation
  within 1e-6 on `dupont_period_kpis`; findings re-route uses only existing metric
  keys, ≤3, no recompute — `importorskip` gate for the future finding builder).

### Area 8 — Working Capital

Restated to **match `fin_compat_wc.compute_wc_kpis` EXACTLY** (fin_compat_wc.py
L162-178) — raw stored sign for stocks, ABS magnitude for days, 365 basis, LTM
denominator:

```
DSO = |TradeRec| · 365 / Rev_LTM      (0 when Rev_LTM  ≤ 1e-6)   FAV−
DIO = |Inv|      · 365 / COGS_LTM     (0 when COGS_LTM ≤ 1e-6)   FAV−
DPO = |TradePay| · 365 / COGS_LTM     (0 when COGS_LTM ≤ 1e-6)   FAV+
NWC = Σ raw-signed TWC + OWC balances                            (context)
CCC = DSO + DIO − DPO                                            FAV−
```

*Worked (matches the module docstring to the decimal):* rec 5.0m, inv 4.0m, pay
3.0m, owc 0.5m, Rev_LTM 20m, COGS_LTM 12m → NWC **+6.5m**, DSO **91.2**, DIO
**121.7**, DPO **91.2**, CCC **121.7** days ✓.
- **Deep-dives:** `Level[l3, cm]` = raw cumulative stock per `level_3`
  (Inventories / Trade receivables / Trade payables) + `Δmonth = cm − pm`,
  `Δfy = fy − fy_py` (raw signed, `_snap_deltas` convention). Trend basis = last 12
  month-ends (`_last_12_periods`).
- **Edge cases:** denominator `≤1e-6 → 0.0` (no div-by-zero); negative stored
  balance feeds NWC raw, days take ABS → positive day counts.
- ⚠️ **Presentation note (not a formula change):** the existing WC KPI rows carry
  `invert_delta=False`; the v2 hero/findings layer applies the FAV− colouring for
  DSO/DIO/CCC and FAV+ for DPO **without** mutating those rows (legacy payload
  stays byte-identical).
- **Test:** `backend/tests/test_wc_overview_kpis.py` (worked example on
  `compute_wc_kpis`, zero-denominator → 0.0, ABS sign-agnosticism, CCC identity).

## Checklist before approving a financial change

- [ ] Formula written out and matches FDD intent
- [ ] Worked numeric example included
- [ ] Edge cases enumerated (zero/negative/missing/partial period)
- [ ] Sign convention stated and unchanged (or change justified + tested)
- [ ] Regression/golden test added and passing
- [ ] Reuses `funktionssammlung` helpers instead of re-deriving
- [ ] Units (EUR vs kEUR) consistent in the output

---

## ⚠️ DRAFT / FLAGGED — DEFERRED formulas (D3 Anlagen + D4 OPOS)

> **NOT YET IMPLEMENTED. These do NOT ship in this epic and NO value is computed.**
> The D3/D4 backend (migrations `0022_draft_fixed_asset_register`,
> `0023_draft_opos_aging`; routers `app/routers/anlagen.py`, `app/routers/opos.py`)
> only stores **pass-through** source rows. Every derived/analytic column is created
> **NULLABLE and left NULL/empty**. Implementing any formula below is a separate,
> approved financial-calculation-engineer task (formula + worked example + edge
> cases + regression/golden test — per the checklist above) and a follow-up
> migration that adds the derived columns.

### F1 — Fixed-asset roll-forward: closing cost (AHK)

```
closing_cost_ahk = opening_cost_ahk + additions_zugang − disposals_abgang ± transfers_umbuchung
```

- **Status:** NOT computed. `fact_fixed_asset` stores the inputs
  (`opening_cost_ahk`, `additions_zugang`, `disposals_abgang`,
  `transfers_umbuchung`) as pass-through; there is **no** `closing_cost_ahk`
  column yet.
- Sign convention for `transfers_umbuchung` (±) and the netting rule must be
  fixed with a worked example before implementation.

### F2 — Accumulated depreciation & net book value (NBV)

- `depreciation` and `nbv` are stored **pass-through only**.
- Accumulated depreciation and a *derived* NBV
  (`closing_cost_ahk − accumulated_depreciation`) are **NOT computed**;
  depreciation **method is TBD** (linear / declining-balance / per asset class).

> **F3/F4 UPDATE (2026-07-01):** the aging method is now formally specified in
> **"AR/AP OPOS As-of Aging (F3/F4)"** above (Method A as-of open balance + FIFO
> attach + bounded overdue% + DSO/DPO), with worked example, edge cases, and
> `backend/tests/test_opos_aging_financial.py`. The stubs below are historical.

### F3 — OPOS open-item determination (`is_open`)

- Determine whether an OPOS line is still open via **settlement matching**:
  invoice (RV) vs payment (ZA) by `beleg_no` / `referenz` — **TBD**.
- `fact_opos_debitor.is_open` / `fact_opos_kreditor.is_open` are created NULLABLE
  and **left NULL** by ingest. The derivation surface
  (`opos.derive_aging()` / `POST /api/v1/opos/{side}/derive-aging`) is an explicit
  stub (`NotImplementedError` / HTTP 501).

### F4 — AR/AP aging bucketing (`aging_band`)

- `aging_band` is created NULLABLE and **left NULL**. When implemented it **MUST
  reuse** `app/services/gl_aging.py` `AR_BANDS` + `_band_case_sql` (do **NOT**
  invent new buckets) and match the golden shape
  `backend/golden/v2/sales__{receivables,payables}-aging__*.json`.

---

## Personnel / Payroll (Income Statement sub-tab)

Source: `fact_personnel_employee` snapshots (`as_of_date`, v1 year-end `YYYY-12-31`).

### FTE per employee row

```
FTE_row = months_active × beschaeftigungsgrad / 100 / 12
FTE_display = ROUND(SUM(FTE_row), 0)   per Bereich × snapshot
```

**Worked example.** `months_active=12`, `beschäftigungsgrad=100` → `FTE_row=1.0`.
`months_active=6`, `beschäftigungsgrad=50` → `FTE_row=0.25`. Two rows → `ROUND(1.25)=1`.

### Payroll accounting (EURk)

```
Payroll_row = gesamtsumme  (or sum of Grundgehalt + Prämie + Sozialversicherung + …)
Payroll_EURk = −SUM(Payroll_row) / 1000     (costs shown negative)
Avg_cost_per_FTE = Payroll_EURk / FTE       (0 when FTE=0)
Personnel_expenses = Payroll + Social Security blocks (footer)
```

### KPI — personnel % of total output

```
personnel_pct_output = |personnel_expenses| / |TOTAL_OUTPUT| × 100
```

`TOTAL_OUTPUT` from `build_pl_annual_compat` (`line_code=TOTAL_OUTPUT`, `amounts.ytd` / 1000).

### Edge cases

- **FTE=0** (Leihpersonal-only row, zero months): avg cost/FTE → 0 (no divide-by-zero).
- **Part-time**: FTE scales with `beschäftigungsgrad`; payroll still sums full row amounts.
- **Leihpersonal**: included in payroll sums; FTE may be 0 if `months_active=0`.
- **Entity filter**: restricts rows by `entity_prefix` before aggregation.

Regression: `backend/tests/test_personnel_accounting.py`, `test_personnel_movements.py`.

---

## Fixed assets rollforward (Balance Sheet sub-tab)

Source: `fact_fixed_asset` snapshots (`as_of_date`, v1 year-end `YYYY-12-31`) from `anlagengitter.xlsx`.

### Per-asset pass-through (EUR)

Values stored as in the Anlagenregister; rollforward aggregates in **kEUR** (`value / 1000`, one decimal).

| Field | Source column |
|-------|----------------|
| `opening_nbv` | Buchwert GJ-Beg |
| `additions_zugang` | Zugang |
| `disposals_abgang` | Abgang |
| `depreciation` | \|Afa des Jahres\| |
| `nbv` (closing) | Lfd Buchwert |

### Rollforward bridge (grouped)

```
closing_nbv_kEUR = Σ nbv / 1000
additions_kEUR   = Σ additions_zugang / 1000
disposals_kEUR   = Σ disposals_abgang / 1000
depreciation_kEUR = Σ |depreciation| / 1000
opening_nbv_kEUR  = Σ opening_nbv / 1000   (from anchor-year file)
```

**Worked example.** One asset: opening 100 kEUR, add 20, disp 5, D&A 10 → closing 105 kEUR.

### Hierarchy

- **flat** (report view): group by `bilanzposition`
- **two_level** (table view, optional): `segment` (Geschäftsbereich) → category sub-rows + segment subtotal

### Edge cases

- Missing snapshot year → column omitted; partial year range still renders.
- Entity filter via `entity_prefix` before aggregation.
- Disposal year: `disposals_abgang` may equal remaining NBV on retired assets.

Regression: `backend/tests/test_fixed_asset_rollforward.py`.

## Cash & debt — net debt table (Cash flow tab)

**Scope:** Month-end cumulative BS balances (kEUR), entity-filtered.

**Sections:**
1. Cash & cash equivalents (`dim_gl_account.level_3`) → cash on hand / cash at banks
2. Bank liabilities — `l6_na_mapping='ND'` and/or `level_3='Liabilities due to banks'`, grouped by bank label from account name
3. Shareholder loan — ND / affiliate liabilities, grouped by counterparty
4. **Net financial debt** = Σ cash + Σ bank + Σ shareholder (signed)
5. **Debt-like items** (optional) — remaining ND accounts (e.g. severances); omit sections 5–6 when empty
6. **Net debt** = net financial debt + debt-like

**Worked example (kEUR):** cash 264 + bank (−4,299) + shareholder (−357) = net financial (−4,392); + severances (−210) → net debt (−4,602).

Regression: `backend/tests/test_fin_compat_cash_debt.py`.

## BS / WC / CF plan overlay — statement-generic position plan (reporting-v2 plan layer, Phase 1)

Generalizes the P&L plan seam to Balance Sheet, Working Capital and Cash Flow. The
manual budget lives once in `fact_position_plan` (`scenario='budget'`, per
`statement`); the statement bodies overlay `plan_cm` / `plan_vs_actual` **only when a
plan map has signal** — with no budget rows every existing response is byte-identical.

Services: `fin_compat_sql.position_plan_grain_sql` (added `scenario` param),
`fin_compat_pl.load_position_plan_map` / `build_statement_plan_response`,
`budget_service.present_to_stored` / `stored_to_present` (CF branch).

### The single sign flip (do NOT re-flip)

`position_plan_grain_sql` applies **exactly one** `amount * -1` presentation flip
(stored GL sign → presented). Overlay code must never flip again.

- **PL / CF** — the SQL `* -1` output IS the correct presented value. CF presents via
  `dim_gl_cf.amount * -1`, the same single flip, so the CF plan sign matches the CF
  actual sign for the same `cf_mapping` leaf.
- **BS** — presented convention is asset(+) / credit(−), not a uniform `* -1`. So we
  recover the stored amount (`stored = -sql_value`) and re-present it through the
  centralized `budget_service.stored_to_present` helper. No new sign literal is
  introduced; only the sanctioned BS helper (`_bs_side_for`: SUPPLIER→credit else asset).

### CF sign branch — `present_to_stored`/`stored_to_present` (DECISION: `-x`, == PL)

CF uses the **same** flip as PL: `stored = −presented`, `presented = −stored`. Proven
(no double flip):

| line | presented (plan) | `present_to_stored` → stored | `position_plan_grain_sql` (`stored*-1`) | matches CF actual sign? |
|------|------|------|------|------|
| inflow (e.g. EBITDA) | **+900** | −900 | −900·(−1) = **+900** | yes — inflow + |
| outflow (e.g. Δ Fixed assets) | **−300** | +300 | +300·(−1) = **−300** | yes — outflow − |

Round-trip `stored_to_present(present_to_stored(p,"CF",lc),"CF",lc) == p`. The explicit
`"CF"` branch is added for clarity and documents that it equals the PL path.

### WC-derived-from-BS (TWC/OWC subset)

There is **no separate WC plan store.** WC is the TWC/OWC subset of the balance sheet
(`l6_na_mapping IN ('TWC','OWC')`, `level_0='BS'`), so `build_statement_plan_response`
routes `statement="WC"` to the **BS** position plan (`effstmt="BS"`); the caller/frontend
projects the returned BS-keyed lines onto the WC nodes (same subset the actual WC body
uses). Net working capital plan = Σ plan of the TWC/OWC BS positions (e.g. AR + Inventory
− AP in presented terms).

### `plan_vs_actual = actual − plan` (PRESENTED terms) — worked micro-examples

Per line: `plan_vs_actual = actual_cm − plan_cm`; `coverage_pct = actual_cm / |plan_cm| ·
100` (None when `|plan_cm| ≤ 1e-6`). Actual is presented in the SAME convention as plan
(PL/CF: the `cm` grain column; BS: `stored_to_present(Σ raw balance)`), so they reconcile
with no re-flip.

- **PL** (Net sales): plan_cm = +1000, actual_cm = +1200 → `plan_vs_actual = +200`,
  coverage = 120%.
- **CF** (Net cash flow): plan_cm = +610, actual_cm = +550 → `plan_vs_actual = −60`,
  coverage = 90.16%.
- **BS** (AR asset): user plans AR = +500 (presented) → stored +500 → SQL `*-1` = −500 →
  `stored_to_present(−(−500),"BS","AR") = +500`; actual AR balance +540 →
  `plan_vs_actual = +40`. (AP credit reconciles the same way via `stored_to_present`.)

### Golden-safety (empty-plan fall-through)

`load_position_plan_map` returns `{}` unless `has_signal` (any `|plan_cm| > 1e-6`). On the
shared backend (no budget rows) every touch point keeps its prior output: `_attach_plan`
adds no keys for a missing line_code, and the BS/CF narrative `cm_vs_plan` stays `0.0`.
`build_statement_plan_response` with an empty `allowed_prefixes` set fails **closed**
(`has_plan_data=False, lines=[]`).

**Pinned signatures** (backend-engineer codes against these verbatim):
`load_position_plan_map(session, statement, year, month, ent_frag, *, scenario="budget") -> dict[str, dict[str, float]]`
and `build_statement_plan_response(session, statement, year, month, entity, *, allowed_prefixes=None) -> dict`.

**Regression (test-engineer to encode):** CF sign round-trip; PL byte-identical
(`build_pl_plan_response == build_statement_plan_response(...,"PL",...)`); empty-plan
byte-identical for `/balance-sheet`, `/cash-flow`, `/working-capital`; BS asset/credit
`plan_vs_actual`; `allowed_prefixes=set()` deny-all.

> ⚠️ **Deferred / needs_review:** (1) WC narrative `cm_vs_plan` (`fin_compat_wc.py:1392`)
> stays `0.0` pending the TWC/OWC→BS-line_code projection map (shipping a guessed NWC-plan
> number was declined). (2) BS `_bs_side_for` only classifies AR (asset) / AP (credit)
> precisely; equity and other non-partner credit positions default to `asset` — a
> pre-existing budget_service limitation that carries into the BS plan sign. Confirm the
> BS comparison convention against seeded v2 data before enabling BS plan columns.

---

## Phase 4 — Versioned plans: forecast/coverage from the single ACTIVE version

> Decision 4 (`docs/plans/v5-pipeline-rework.md`). Supersedes the side-by-side
> `budget`+`forecast` scenario resolution (`load_position_plan_map_pref`
> forecast→budget). Forecast is now a **derived column of the ONE active plan
> version** — there is no separately-stored `scenario='forecast'` band in the read
> path. Sign-off date 2026-07-07. Code: `backend/app/services/plan_version.py`
> (resolution) + `fin_compat_pl.load_position_plan_map_pref` /
> `_apply_annual_fy_forecast`. Test: `backend/tests/test_plan_version_forecast.py`.

### The active-version model

A **plan version** (`dim_plan_version`) is scoped to `(project_id, statement,
fiscal_year)`. **Exactly ONE** version per scope has `is_active = TRUE` (enforced by
a partial unique index — `scenario` is NOT in the key). Only a version with
`include_in_reporting = TRUE` feeds the reporting forecast/coverage columns. The
version owns the plan rows in `fact_position_plan` (linked by the nullable
`plan_version_id`; legacy rows were backfilled to a default active+included `v1`).

Resolution (`plan_version.resolve_plan_scope`) is tri-state + a legacy escape hatch:

| Scope state | Meaning | Forecast/coverage source |
|---|---|---|
| `legacy` | `dim_plan_version` table absent (un-migrated DB) | UNCHANGED legacy path (budget scenario) — byte-identical |
| `active_included` | active version exists, `include_in_reporting=TRUE` | the active version's plan (`scenario='budget'` band) |
| `active_parked` | active version exists, `include_in_reporting=FALSE` | **None** (parked — never stale) |
| `no_version` | table present, no active version for the scope | **None** (parked) |

### FORMULA — forecast (per line, per column grain)

Let `L` = last CLOSED fiscal period (the anchor month). For a reporting line and a
column grain:

```
Plan(p)      = the ACTIVE version's plan amount for fiscal_period p  (presented sign)
ytd_actual   = Σ_{p ≤ L} Actual(p)        (running-sum actual through the anchor)
ytg_plan     = Σ_{p > L} Plan(p)           (active version's remaining-months plan)

Forecast_FY  = ytd_actual + ytg_plan                          (annual column, "FY..F")
             = ytd_actual                     when the active version is parked /
                                              absent (no reporting signal)
```

Monthly grain: `Forecast(month=p) = Actual(p)` for `p ≤ L`, `= Plan(p)` for `p > L`
(a closed month is always the actual; an open month is the active version's plan).
Weekly grain routes through the same rule on the ISO-week actual/plan split (Phase 3).
For a STOCK statement (BS/FA/OPOS) the "actual through anchor" is the balance at the
anchor cutoff and the plan adds the active version's remaining-month movements (mirror
of `fin_compat_bs._apply_snapshot_fy_forecast_amounts`, unchanged).

This is exactly the pre-existing `_apply_annual_fy_forecast` stitching (`fy_f = ytd +
ytg`), but `ytg` is now sourced from the **single active version** instead of a
separate stored forecast band. The `has_ytg_plan` global gate is retained: with no
active-version plan signal, `Forecast_FY == ytd_actual` (parked, byte-identical to the
no-plan world — NOT a stale forecast-band value).

### FORMULA — coverage_pct (unchanged arithmetic, re-sourced denominator)

```
coverage_pct = round(actual_cm / abs(plan_cm) * 100, 2)   if abs(plan_cm) > 1e-6
             = None  (+ "no plan" flag)                    otherwise
```

`plan_cm` is now the ACTIVE version's current-month plan (was: forecast-preferred
band). Denominator is `abs(plan_cm)` so the sign of `actual_cm` is preserved
(a negative actual over a negative cost plan → negative coverage — documented, not a
bug). `plan_vs_actual = actual_cm − plan_cm` (presented terms, one sign path).

### WORKED EXAMPLE (anchor L = 6, Net sales, presented +)

```
Active version v1, include_in_reporting = TRUE.
Actual  p1..p6 = 100 each  → ytd_actual = 600
Plan    p7..p12 = 90 each  → ytg_plan  = 540      (active version 'budget' band)
Forecast_FY = 600 + 540 = 1140
Current-month (p6): actual_cm = 110, plan_cm = 100
  coverage_pct = 110 / |100| * 100 = 110.0
  plan_vs_actual = 110 − 100 = +10
```

### EDGE CASES (locked in test_plan_version_forecast.py)

* **No active version** (`no_version`) → forecast = `ytd_actual`, `plan_cm = 0` →
  coverage `None` + `"no plan"`. Never reads a stale/orphan plan row.
* **Active but `include_in_reporting = FALSE`** (`active_parked`) → identical to
  no-version: forecast = `ytd_actual`, coverage `None`. Un-checking the toggle parks
  the columns immediately (no stale value from the previously-included version).
* **Anchor = month 12** (year closed) → `ytg_plan = 0` → `Forecast_FY == ytd_actual`
  == full-year actual (no open period to project).
* **Partial open period** — the anchor `L` is the last CLOSED period; an in-progress
  month is treated as OPEN (`p > L` uses plan) until it closes, so a half-booked month
  never blends actual+plan for the same period.
* **Table absent** (`legacy`, un-migrated DB) → the entire path is byte-identical to
  the pre-Phase-4 budget resolution; a DB without `dim_plan_version` never 500s
  (`plan_version.resolve_plan_scope` catches the missing table and returns `legacy`).
* **`plan_cm = 0` / sub-epsilon** → coverage `None` (no divide-by-zero), forecast
  still = `ytd_actual` (the FY column degrades gracefully to actuals).

### Parity note (already-seeded v5 DB)

Before Phase 4, `load_position_plan_map_pref` preferred the stored `scenario='forecast'`
band, falling back to `budget`. After Phase 4 (migrated DB) it reads the ACTIVE version =
the `budget` band. Where a DB carried a `forecast` band that DIFFERS from `budget`, the
forecast/coverage columns change to reflect the active version — this is the intended
un-parking, not a regression. The default backfilled `v1` is active+included, so a DB
whose forecast≈budget is numerically unchanged. Un-migrated DBs (no table) are exactly
byte-identical via the `legacy` escape hatch.
