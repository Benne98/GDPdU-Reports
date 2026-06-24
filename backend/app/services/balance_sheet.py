"""P5 / Phase C3 — Balance Sheet statement service.

Aggregates ``fact_gl_line`` over ``dim_gl_account`` (level_0 = 'BS') per period
column, builds the BS presentation via ``dim_pl_structure`` (BS rows) with the
in-code fallback, and reconciles the accounting identity Assets = Liab + Equity.

Like the P&L service (``app.services.statements``) the CALCULATION core
(``aggregate_bs``) is PURE: it operates on already-fetched movement rows + a
structure definition + a PeriodPlan, with NO database access, so it is fully
golden-testable with a tiny synthetic fixture.  Thin DB helpers do the SQL.

======================================================================= STRUCTURE (GL-DERIVED — there is NO "BS Structure" sheet)
Unlike the P&L (whose structure comes from a "PL Structure" sheet), the BS report
structure is DERIVED from the live GL balance-sheet hierarchy in dim_gl_account
(level_0='BS'), ordered by (level_2_sort, level_3_sort), by
``scripts/seed_bs_structure.py`` and persisted as BS rows in dim_pl_structure
(kpi_code 'BS:<section>').  The generated structure has THREE row types:
  • 'mapping'    — one per L3 category, wired by ``level_3 = <L3 value>`` so
                   ``_matches`` aggregates exactly that L3's GL accounts.
  • 'subtotal'   — one per L2 group ("Total fixed assets", "Total current assets",
                   "Total equity", "Total liabilities", …) = Σ its L3 mapping rows
                   (a per-section running sum that RESETS at each L2 subtotal).
  • 'grandtotal' — one per L1 side: "Total assets" = Σ ALL asset L3 lines;
                   "Total equity & liabilities" = Σ ALL credit L3 lines (a per-section
                   CUMULATIVE sum that never resets).
The four WC/CF-critical balances carry FIXED line-code aliases so working_capital.py
and cash_flow.py pick up real values: Trade receivables→AR, Inventories→INVENTORY,
Trade payables→AP, Cash & cash equivalents→CASH, Retained earnings→EQUITY.
The in-code ``default_bs_structure()`` (flat asset/liability/equity, no grandtotal)
is the FALLBACK used until the BS rows are seeded and by the golden fixtures.

======================================================================= CRITICAL DIFFERENCE FROM P&L — STOCK vs FLOW
P&L accounts are FLOW: a column value is the sum of in-period movements only
(periods that belong to that column).  Balance-sheet accounts are STOCK
(cumulative balances): the value of a BS column is the CLOSING BALANCE as of the
END of that column's period — i.e. the cumulative sum of ALL movements from the
beginning of time up to and including the latest bucket in the column.

Concretely, for column C whose latest bucket is (fy*, p*):
    BS_value(line, C) = Σ presented_bs( amount )  over every movement with
                        (fiscal_year, fiscal_period) <= (fy*, p*)  AND matching
                        the line's level filter.
Ordering of buckets is lexicographic on (fiscal_year, fiscal_period): a movement
is "<= cutoff" iff (fy, p) <= (fy*, p*).  This is why opening balances and every
prior period flow into the current stock — exactly how a real ledger balance
accumulates.  (The synthetic fixture seeds an OPENING balance in period 1 so the
cumulative behaviour is observable and testable.)

For an EMPTY column (e.g. YTD with last_closed_period=0, which has no buckets) the
cutoff is undefined → the stock is 0.0 for every line (documented edge case).

======================================================================= SIGN CONVENTION (BS-specific — deliberately NOT the P&L _present)
Storage (docs/db/gl-target-structure.md §6):  amount  + = Soll/debit, − = Haben/credit.

A balance sheet has two sides with opposite natural balances:
  • ASSETS carry a DEBIT balance → cumulative Σ(amount) is naturally POSITIVE.
  • LIABILITIES & EQUITY carry a CREDIT balance → cumulative Σ(amount) is naturally NEGATIVE.

We present every BS line as a POSITIVE magnitude IN ITS OWN SECTION:
    presented_bs(amount, side='asset')  = + amount        (debit balance → +)
    presented_bs(amount, side='credit') = − amount        (credit balance → +)
where ``side`` is taken from the structure line (``bs_side`` ∈ {'asset','credit'}).
A line with no explicit side defaults to 'asset' (raw stored sign, no flip) so an
unconfigured line never silently changes sign.

This is the SINGLE, documented place the BS sign is applied (``_present_bs``).  It
is DIFFERENT from the P&L ``_present`` (which is always −amount) and is never
reused across the two.  Rationale: on the P&L every line is income(+)/expense(−)
under one rule; on the BS the two sides need opposite flips to both read positive.

======================================================================= ACCOUNTING IDENTITY (reconciliation, asserted in the golden test)
In STORED signs, a complete set of balanced journal entries satisfies
Σ(amount) = 0 over ALL accounts.  Restricting to BS accounts:
    Σ_BS(amount) = − Σ_PL(amount)
and Σ_PL(amount) = −(presented net income) (P&L presents −amount), so
    Σ_BS(amount) = presented net income  of the same periods.

In PRESENTED BS terms (assets +amount, liab/equity −amount):
    Assets_presented  =  + Σ_assets(amount)
    LiabEq_presented  =  − Σ_liabeq(amount)
    Assets − LiabEq   =  Σ_assets(amount) + Σ_liabeq(amount) = Σ_BS(amount)
                      =  net income not yet closed to equity.

So the identity that ALWAYS ties out exactly is:
    ASSETS = LIABILITIES + EQUITY + RETAINED_EARNINGS_MOVEMENT(=current net income)
The synthetic fixture is constructed so the current-year P&L result is carried as
an explicit equity movement (a "Net income / retained earnings" BS account), which
makes the strict identity hold:
    ASSETS = LIABILITIES + EQUITY   (exactly, per column).
The golden test asserts this exact balance per column.  If a real dataset has an
unclosed P&L, the residual equals Σ_PL(amount) and is surfaced via
``imbalance`` per column for reconciliation (documented, never hidden).

The ``imbalance`` formula on the GENERATED structure (sections 'asset' + 'credit')
is computed from the CUMULATIVE per-section sums:
    imbalance(C) = Σ_asset_cum(C) − ( Σ_liability_cum + Σ_equity_cum + Σ_credit_cum )
               = Total assets − Total equity & liabilities   (per column C).
The default fixture uses split sections (asset/liability/equity, no 'credit'); the
live structure uses asset/credit — ONE formula covers both (absent sections are 0).

REAL GL-ONLY DATA DOES NOT BALANCE (expected, surfaced honestly).  The Decidra
reference GL is GL-MOVEMENTS ONLY: it carries NO opening balances and NO
retained-earnings roll-forward of the P&L result into equity.  So on real data
Total assets (a debit balance) and Total equity & liabilities (a credit balance) do
NOT net to zero — ``imbalance`` is MATERIALLY non-zero (roughly the magnitude of the
unbooked opening equity + accumulated P&L, i.e. on the order of the asset base).
For FY2024 (current_fy=2024, last_closed_period=12) expect Total assets ≈ the summed
debit BS balances and Total E&L ≈ the summed credit BS balances, with their
difference (the imbalance) on the order of the equity/retained gap — NOT ~0.  The
frontend shows an amber reconciliation note; this is correct behaviour for GL-only
data, never hidden or force-balanced.

======================================================================= WORKED EXAMPLE (golden, cumulative)
Synthetic BS GL (entity 01, stored amount + = debit):
  Cash (asset)        : P1 +1000 (opening) , P2..P5 +100 each  → cum@P5 = +1400
  Receivables (asset) : P1 +300  (opening) , P3 +200           → cum@P5 = +500
  Inventory (asset)   : P1 +200  (opening)                     → cum@P5 = +200
  Payables (credit)   : P1 −400  (opening) , P4 −100           → cum@P5 = −500  → presented +500
  Equity   (credit)   : P1 −1100 (opening)                     → cum@P5 = −1100 → presented +1100
  Retained (credit)   : current net income carried, −500       → cum@P5 = −500  → presented +500
Stored Σ over all BS = 1400+500+200 −500 −1100 −500 = 0  → fixture is balanced.
YTD column (current_fy=2025, last_closed_period=5), cutoff (2025,5):
  Total assets      = 1400 + 500 + 200 = 2100
  Total liabilities = 500
  Total equity      = 1100 + 500 = 1600
  Identity: 2100 = 500 + 1600  ✓ , imbalance = 0.

======================================================================= EDGE CASES
  • Empty column (no buckets, e.g. YTD with L=0): every line = 0.0, identity 0=0.
  • Negative presented balance (e.g. overdrawn cash, contra-asset): the cumulative
    sum carries its sign; no clamping.  Documented as informational.
  • Unmapped BS account (matches no structure line): contributes to NO line; its
    cumulative presented value is surfaced via ``unmapped_total`` per column so the
    section totals + unmapped reconcile to Σ presented over all BS movements.
  • Period 13 (consolidation) excluded by the period engine; never in a cutoff.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.services.statements import (
    StatementCell,
    StatementLine,
    StructureLine,
)

# --------------------------------------------------------------------------- #
# Data shapes (pure) — a BS movement is identical in shape to a P&L movement,
# but carries a ``bs_side`` resolved from the matched structure line at present
# time, not stored on the movement.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BsMovement:
    fiscal_year: int
    fiscal_period: int
    level_2: Optional[str]
    level_3: Optional[str]
    level_4: Optional[str]
    amount: float  # stored sign (+debit, -credit)


@dataclass
class BsStatement:
    view_mode: str
    coverage: float
    column_keys: list[str]
    column_labels: dict[str, str]
    lines: list[StatementLine]
    # Σ presented_bs over BS movements (cumulative) matching NO mapping line, per column.
    unmapped_total: dict[str, float]
    # Assets − (Liabilities + Equity) per column.  0.0 when the identity ties out.
    imbalance: dict[str, float]


# --------------------------------------------------------------------------- #
# BS structure line — extends the P&L StructureLine semantics with a ``bs_side``
# and a ``section`` tag so subtotals know which section they close.  We model it
# by overloading the existing StructureLine via kpi_code/calc_type would be
# fragile, so BS uses its own lightweight dataclass.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BsStructureLine:
    sort_order: int
    line_code: str
    balance_title: str
    row_type: str                  # 'mapping' | 'subtotal' | 'grandtotal'
    section: str                   # 'asset' | 'liability' | 'equity' | 'credit' (sign + subtotal grouping)
    level_2: Optional[str] = None
    level_3: Optional[str] = None
    level_4: Optional[str] = None
    is_bold: bool = False
    # For 'grandtotal' (L1) rows only: the set of sections it rolls up cumulatively.
    # None → defaults to {self.section} (a grandtotal over its own section).  The live
    # BS uses {'asset'} for "Total assets" and {'credit'} for "Total equity & liab.".
    grandtotal_sections: Optional[tuple[str, ...]] = None

    @property
    def bs_side(self) -> str:
        """Sign side: assets keep stored sign; liabilities & equity are credit-side.

        Sections {'liability','equity','credit'} are all credit-side; only 'asset'
        keeps the stored sign.  ('credit' is the single combined equity&liabilities
        section used by the live GL-derived structure; 'liability'/'equity' are the
        split sections used by the in-code default fixture.)
        """
        return "asset" if self.section == "asset" else "credit"


# --------------------------------------------------------------------------- #
# Sign convention — the ONE place we apply the BS sign (NOT the P&L _present).
# --------------------------------------------------------------------------- #
def _present_bs(stored_amount: float, side: str) -> float:
    """Convert a STORED signed GL amount to the BS PRESENTED magnitude.

    side='asset'  → +amount  (assets carry a debit balance → positive).
    side='credit' → −amount  (liabilities/equity carry a credit balance → positive).
    Any other side defaults to no flip (+amount) so an unconfigured line never
    silently changes sign.  This is the single documented application of the
    BS-specific sign convention; it is DIFFERENT from the P&L _present (= −amount).
    """
    if side == "credit":
        return -float(stored_amount)
    return float(stored_amount)


# --------------------------------------------------------------------------- #
# Matching (same wildcard semantics as the P&L matcher)
# --------------------------------------------------------------------------- #
def _matches(line: BsStructureLine, mv: BsMovement) -> bool:
    if line.level_2 is not None and line.level_2 != mv.level_2:
        return False
    if line.level_3 is not None and line.level_3 != mv.level_3:
        return False
    if line.level_4 is not None and line.level_4 != mv.level_4:
        return False
    return True


def _cutoff(col: Any) -> Optional[tuple[int, int]]:
    """The latest (fiscal_year, fiscal_period) bucket of a column = the stock cutoff.

    Returns None for an empty column (no buckets) → stock is 0 there.
    Lexicographic max on (fy, period).
    """
    if not col.buckets:
        return None
    return max(col.buckets)


# --------------------------------------------------------------------------- #
# Pure aggregation core
# --------------------------------------------------------------------------- #
def aggregate_bs(
    movements: list[BsMovement],
    structure: list[BsStructureLine],
    period_plan: Any,
) -> BsStatement:
    """Build the Balance Sheet matrix.  PURE — no DB, deterministic, CUMULATIVE.

    For each column and each mapping line:
        value = Σ presented_bs(amount, line.bs_side) over movements that
                (a) match the line's level filter AND
                (b) have (fy, period) <= the column's cutoff (latest bucket).
    'subtotal' (L2) lines sum the mapping lines of their section emitted since the
    previous subtotal of that section (a per-section running sum that resets each
    subtotal — so a section may have several L2 subtotals).  'grandtotal' (L1) lines
    sum the CUMULATIVE total of every mapping line of the section(s) they roll up
    (``grandtotal_sections``, default = own section).  ``imbalance`` = Total assets −
    Total equity & liabilities per column, from the cumulative section sums
    (asset − (liability+equity+credit)); 0.0 only when the data actually balances
    (GL-only reference data does NOT — the residual is surfaced, never hidden).
    """
    columns = list(period_plan.columns)
    structure = sorted(structure, key=lambda s: s.sort_order)

    # Sections seen in the structure (asset/liability/equity for the default fixture;
    # asset/credit for the live GL-derived structure).  We accumulate per-section so a
    # single core supports BOTH shapes (flat 3-section default, and the nested
    # L2-subtotal + L1-grandtotal live structure) without a special case.
    _sections = {s.section for s in structure} | {"asset", "liability", "equity", "credit"}

    def _zero() -> dict[str, float]:
        return {s: 0.0 for s in _sections}

    out_lines: list[StatementLine] = []
    # Per-column emitted line values for subtotal references.
    emitted: dict[str, dict[str, Optional[float]]] = {col.key: {} for col in columns}
    # Per-column running section sums — RESET at each subtotal of that section, so a
    # section may have MULTIPLE subtotals (one per L2 group, e.g. "Total fixed assets"
    # then "Total current assets", each closing only the mapping lines since the last).
    section_sum: dict[str, dict[str, float]] = {col.key: _zero() for col in columns}
    # Per-column CUMULATIVE section sums — NEVER reset; the total of EVERY mapping line
    # of that section.  Drives L1 grandtotals and the accounting-identity imbalance.
    section_cum: dict[str, dict[str, float]] = {col.key: _zero() for col in columns}
    # Per-column claimed movement ids (for unmapped reconciliation), cumulative.
    claimed: dict[str, set[int]] = {col.key: set() for col in columns}
    col_cutoff: dict[str, Optional[tuple[int, int]]] = {
        col.key: _cutoff(col) for col in columns
    }

    for line in structure:
        cells: list[StatementCell] = []
        for col in columns:
            cutoff = col_cutoff[col.key]
            if line.row_type == "mapping":
                total = 0.0
                if cutoff is not None:
                    for mv in movements:
                        if (mv.fiscal_year, mv.fiscal_period) <= cutoff and _matches(line, mv):
                            total += _present_bs(mv.amount, line.bs_side)
                            claimed[col.key].add(id(mv))
                value: Optional[float] = total
                section_sum[col.key][line.section] += total
                section_cum[col.key][line.section] += total
            elif line.row_type == "subtotal":
                # L2 subtotal = mapping lines of this section since the last subtotal.
                value = section_sum[col.key][line.section]
                section_sum[col.key][line.section] = 0.0  # reset for the next L2 group
            elif line.row_type == "grandtotal":
                # L1 total = cumulative Σ of EVERY mapping line of the section(s) it rolls
                # up (defaults to its own section).  Never resets any running sum.
                sides = line.grandtotal_sections or (line.section,)
                value = sum(section_cum[col.key].get(s, 0.0) for s in sides)
            else:
                value = None
            emitted[col.key][line.line_code] = value
            cells.append(StatementCell(column_key=col.key, value=value))

        out_lines.append(
            StatementLine(
                line_code=line.line_code,
                label=line.balance_title,
                row_type=line.row_type,
                is_bold=line.is_bold,
                kpi_code=None,
                cells=cells,
                level_2=line.level_2,
                level_3=line.level_3,
                level_4=getattr(line, "level_4", None),
            )
        )

    # Unmapped reconciliation per column: cumulative presented over unmatched BS
    # movements (assets keep sign, credits flipped — but unmapped lines have no
    # side, so we present at raw stored sign to keep Σ comparable to total stored).
    unmapped: dict[str, float] = {}
    imbalance: dict[str, float] = {}
    for col in columns:
        cutoff = col_cutoff[col.key]
        u = 0.0
        if cutoff is not None:
            claimed_ids = claimed[col.key]
            for mv in movements:
                if (mv.fiscal_year, mv.fiscal_period) <= cutoff and id(mv) not in claimed_ids:
                    u += float(mv.amount)  # raw stored sign (no section to flip by)
        unmapped[col.key] = u
        # Accounting identity residual = Total assets − Total equity & liabilities,
        # from the CUMULATIVE section sums (robust to multiple subtotals per section).
        #   default fixture  : asset − (liability + equity)   [split sections]
        #   live GL structure: asset − credit                 [combined E&L section]
        cum = section_cum[col.key]
        imbalance[col.key] = cum["asset"] - (cum["liability"] + cum["equity"] + cum["credit"])

    return BsStatement(
        view_mode=period_plan.view_mode,
        coverage=period_plan.coverage,
        column_keys=[c.key for c in columns],
        column_labels={c.key: c.label for c in columns},
        lines=out_lines,
        unmapped_total=unmapped,
        imbalance=imbalance,
    )


# --------------------------------------------------------------------------- #
# Default in-code BS structure (used until dim_pl_structure has BS rows seeded).
# --------------------------------------------------------------------------- #
def default_bs_structure() -> list[BsStructureLine]:
    """Minimal BS structure matching the synthetic fixture's level_3 labels.

    Sections: assets (Cash, Receivables, Inventory) → Total assets; liabilities
    (Payables) → Total liabilities; equity (Equity, Retained earnings) → Total
    equity.  Used when dim_pl_structure has no BS rows yet.
    """
    return [
        BsStructureLine(10, "CASH", "Cash & equivalents", "mapping", "asset", level_3="Cash"),
        BsStructureLine(20, "AR", "Accounts receivable", "mapping", "asset", level_3="Receivables"),
        BsStructureLine(30, "INVENTORY", "Inventory", "mapping", "asset", level_3="Inventory"),
        BsStructureLine(40, "TOTAL_ASSETS", "Total assets", "subtotal", "asset", is_bold=True),
        BsStructureLine(50, "AP", "Accounts payable", "mapping", "liability", level_3="Payables"),
        BsStructureLine(60, "TOTAL_LIABILITIES", "Total liabilities", "subtotal", "liability", is_bold=True),
        BsStructureLine(70, "EQUITY", "Equity", "mapping", "equity", level_3="Equity"),
        BsStructureLine(80, "RETAINED", "Retained earnings / net income", "mapping", "equity", level_3="Retained earnings"),
        BsStructureLine(90, "TOTAL_EQUITY", "Total equity", "subtotal", "equity", is_bold=True),
    ]


# --------------------------------------------------------------------------- #
# DB read helpers (thin; exercised via dependency_overrides, not unit-tested raw)
# --------------------------------------------------------------------------- #
def fetch_bs_structure(session: Any) -> list[BsStructureLine]:
    """Load BS rows from dim_pl_structure; fall back to default_bs_structure().

    BS rows are distinguished by ``kpi_code='BS'`` OR a ``section`` carried in
    ``kpi_code`` (convention: kpi_code holds the section for BS rows).  Until the
    table is seeded with BS rows this returns the in-code default.
    """
    from sqlalchemy import text

    rows = session.execute(
        text(
            "SELECT sort_order, line_code, COALESCE(balance_title,'') AS balance_title, "
            "row_type, level_2, level_3, level_4, kpi_code, is_bold "
            "FROM dim_pl_structure WHERE kpi_code LIKE 'BS:%' ORDER BY sort_order"
        )
    ).fetchall()
    if not rows:
        return default_bs_structure()
    out: list[BsStructureLine] = []
    for r in rows:
        # kpi_code convention for BS rows: 'BS:<section>' e.g. 'BS:asset'.
        section = (r[7] or "BS:asset").split(":", 1)[1] if r[7] else "asset"
        out.append(
            BsStructureLine(
                sort_order=int(r[0]),
                line_code=r[1],
                balance_title=r[2],
                row_type=r[3],
                section=section,
                level_2=r[4],
                level_3=r[5],
                level_4=r[6],
                is_bold=bool(r[8]),
            )
        )
    return out


def _fetch_bs_budget_movements(
    session: Any,
    years_list: list[int],
    *,
    entity_prefix: Optional[str] = None,
) -> list[BsMovement]:
    """BS manual-budget reader (fact_position_plan, statement='BS', scenario='budget').

    STOCK vs MOVEMENT (plan risk, docs §risks):  AR/AP partner budget — and every
    BS budget position — is stored as a **closing balance (STOCK) per period**, NOT
    a per-period movement.  The pure BS core (:func:`aggregate_bs`) is CUMULATIVE:
    a column's value is Σ(movement) for ``(fy,period) <= cutoff``.  Feeding a stock
    in as if it were a movement would double-count (cumulative sum of stocks).

    So we **delta-encode** the stock into synthetic movements: for each
    (line_code, fy, period) we emit ``stock(p) − stock(prev)`` where ``prev`` is the
    immediately-preceding stored period for that line (prior period same year, else
    closing of the prior year, else 0).  Then the core's cumulative sum up to any
    cutoff p* exactly reconstructs ``stock(p*)`` — the intended BS budget balance —
    while still flowing correctly into multi-year cumulative columns.

    The budget is keyed by ``line_code`` (position grain).  The BS core matches on
    level_2/3/4, so we resolve each line_code → (level_2, level_3, level_4) via the
    BS rows of ``dim_pl_structure`` and emit movements at that grain.  Partner rows
    (partner_id<>'') and the position row (partner_id='') for the same line_code are
    summed together into the position's stock (the roll-up invariant guarantees
    Σ(partners)+Other == position).  ``amount`` is returned in the STORED GL sign
    (the BS sign flip lives solely in ``_present_bs``), exactly like the GL path.

    Entity precedence: per-entity rows (entity_prefix=:ep) override consolidated
    ('') rows for the same line_code; consolidated rows are used only where no
    per-entity row exists.  Consolidated view (entity_prefix None): the '' row per
    line_code when one exists (an explicit consolidated override), ELSE the SUM of
    the per-entity rows (Σ entity budgets) — mirroring the PL position reader.

    GOLDEN-SAFETY: returns [] when no budget BS rows exist for the scope → caller
    falls back to fact_gl_plan → byte-identical.  The consolidated entity-sum only
    changes how EXISTING budget rows aggregate; with none, the query matches nothing.
    """
    from sqlalchemy import text

    if not years_list:
        return []

    # line_code → (level_2, level_3, level_4) from the BS structure rows.
    struct_rows = session.execute(
        text(
            "SELECT line_code, level_2, level_3, level_4 "
            "FROM dim_pl_structure WHERE kpi_code LIKE 'BS:%'"
        )
    ).fetchall()
    code_levels: dict[str, tuple[Any, Any, Any]] = {
        str(r[0]): (r[1], r[2], r[3]) for r in struct_rows
    }

    ep = (entity_prefix or "").strip() or None
    params: dict[str, Any] = {"years": years_list}
    if ep is None:
        # Consolidated: '' row per line_code when present (explicit override), ELSE
        # Σ of the per-entity rows.  A row participates iff it is '' OR it is a
        # per-entity row with no '' row for the same line_code.
        ent_clause = (
            "AND ( p.entity_prefix = '' "
            "      OR ( p.entity_prefix <> '' "
            "           AND NOT EXISTS ( "
            "             SELECT 1 FROM fact_position_plan c "
            "             WHERE c.statement = 'BS' AND c.scenario = 'budget' "
            "               AND c.line_code = p.line_code "
            "               AND c.fiscal_year = p.fiscal_year "
            "               AND c.entity_prefix = '' ) ) )"
        )
    else:
        params["ep"] = ep
        ent_clause = (
            "AND ( p.entity_prefix = :ep "
            "      OR ( p.entity_prefix = '' "
            "           AND NOT EXISTS ( "
            "             SELECT 1 FROM fact_position_plan e "
            "             WHERE e.statement = 'BS' AND e.scenario = 'budget' "
            "               AND e.line_code = p.line_code "
            "               AND e.fiscal_year = p.fiscal_year "
            "               AND e.entity_prefix = :ep ) ) )"
        )

    # Σ position-level + partner rows into the line_code stock per (line_code, fy, p).
    sql = text(f"""
        SELECT p.line_code, p.fiscal_year, p.fiscal_period,
               SUM(p.amount) AS stock
        FROM fact_position_plan p
        WHERE p.statement = 'BS'
          AND p.scenario = 'budget'
          AND p.fiscal_year = ANY(:years)
          AND p.fiscal_period BETWEEN 1 AND 12
          {ent_clause}
        GROUP BY p.line_code, p.fiscal_year, p.fiscal_period
    """)
    rows = session.execute(sql, params).fetchall()
    if not rows:
        return []

    # stocks[line_code][(fy, period)] = closing balance (stored sign).
    stocks: dict[str, dict[tuple[int, int], float]] = {}
    for r in rows:
        code = str(r[0])
        stocks.setdefault(code, {})[(int(r[1]), int(r[2]))] = float(r[3])

    out: list[BsMovement] = []
    for code, by_period in stocks.items():
        levels = code_levels.get(code)
        if levels is None:
            # No structure mapping → cannot be matched by the core; skip (would be
            # surfaced as unmapped only if it were a movement — but a stock with no
            # home has no defined level, so we drop it rather than mis-bucket).
            continue
        l2, l3, l4 = levels
        ordered = sorted(by_period.keys())  # lexicographic (fy, period)
        prev_stock = 0.0
        for key in ordered:
            stock = by_period[key]
            delta = stock - prev_stock
            prev_stock = stock
            out.append(
                BsMovement(
                    fiscal_year=key[0],
                    fiscal_period=key[1],
                    level_2=l2,
                    level_3=l3,
                    level_4=l4,
                    amount=delta,
                )
            )
    return out


def fetch_bs_movements(
    session: Any,
    fiscal_years: tuple[int, ...],
    *,
    entity_prefix: Optional[str] = None,
    scenario: Optional[str] = None,
) -> list[BsMovement]:
    """Read BS GL movements (level_0='BS') per (fy, period, level_2/3/4).

    Returns RAW movements (stored sign).  The cumulative stock and the BS sign are
    applied in the pure core, not here.  ``scenario`` reads fact_gl_plan (movements,
    per docs §14.2 BS-plan is stored as movement so cumulative sum gives the stock).
    """
    from sqlalchemy import text

    if not fiscal_years:
        return []
    years_list = sorted(set(int(y) for y in fiscal_years))

    entity_clause = ""
    params: dict[str, Any] = {"years": years_list}
    if entity_prefix:
        entity_clause = "AND a.entity_prefix = :entity"
        params["entity"] = entity_prefix

    if scenario:
        # Resolution: a requested plan scenario FIRST tries the manual budget
        # (fact_position_plan, statement='BS') and falls back to fact_gl_plan when
        # the budget has no BS rows for the scope.  GOLDEN-SAFETY: with no budget
        # rows this returns [] and we fall through to the legacy fact_gl_plan path
        # → byte-identical output.
        budget_mvs = _fetch_bs_budget_movements(
            session, years_list, entity_prefix=entity_prefix
        )
        if budget_mvs:
            return budget_mvs

    if scenario:
        params["scenario"] = scenario
        sql = text(f"""
            SELECT pl.fiscal_year, pl.fiscal_period,
                   a.level_2, a.level_3, a.level_4,
                   SUM(pl.amount) AS amount
            FROM fact_gl_plan pl
            JOIN dim_gl_account a
              ON a.account_number_group = pl.account_number_group
             AND a.fiscal_year = pl.fiscal_year
            WHERE a.level_0 = 'BS'
              AND pl.scenario = :scenario
              AND pl.fiscal_year = ANY(:years)
              AND pl.fiscal_period BETWEEN 1 AND 12
              {entity_clause}
            GROUP BY pl.fiscal_year, pl.fiscal_period, a.level_2, a.level_3, a.level_4
        """)
    else:
        sql = text(f"""
            SELECT e.fiscal_year, e.fiscal_period,
                   a.level_2, a.level_3, a.level_4,
                   SUM(l.amount) AS amount
            FROM fact_gl_line l
            JOIN fact_gl_entry e
              ON e.journal_entry_group_number = l.journal_entry_group_number
             AND e.fiscal_year = l.fiscal_year
            JOIN dim_gl_account a
              ON a.account_number_group = l.account_number_group
             AND a.fiscal_year = l.fiscal_year
            WHERE a.level_0 = 'BS'
              AND e.fiscal_year = ANY(:years)
              AND e.fiscal_period BETWEEN 1 AND 12
              {entity_clause}
            GROUP BY e.fiscal_year, e.fiscal_period, a.level_2, a.level_3, a.level_4
        """)

    rows = session.execute(sql, params).fetchall()
    return [
        BsMovement(
            fiscal_year=int(r[0]),
            fiscal_period=int(r[1]),
            level_2=r[2],
            level_3=r[3],
            level_4=r[4],
            amount=float(r[5]),
        )
        for r in rows
    ]


def _all_years_for_cumulative(plan: Any) -> tuple[int, ...]:
    """BS is cumulative, so we must fetch movements for EVERY year from the earliest
    referenced year up to the latest, inclusive — otherwise opening balances from
    years not directly in a column's buckets would be missing from the stock.

    The earliest year any column references is the lower bound; we include all
    years up to the max referenced year.
    """
    years: set[int] = set()
    for col in plan.columns:
        years.update(col.fiscal_years())
    if not years:
        return tuple()
    lo, hi = min(years), max(years)
    return tuple(range(lo, hi + 1))


def build_bs_statement(
    session: Any,
    *,
    view_mode: str,
    current_fy: int,
    last_closed_period: int,
    entity_prefix: Optional[str] = None,
    scenario: Optional[str] = None,
    fy_start_month: int = 1,
) -> BsStatement:
    """Orchestrate: period plan → fetch CUMULATIVE-eligible movements → pure aggregate."""
    from app.services.periods import build_period_plan

    plan = build_period_plan(
        view_mode,  # type: ignore[arg-type]
        current_fy,
        last_closed_period,
        fy_start_month=fy_start_month,
    )
    structure = fetch_bs_structure(session)
    movements = fetch_bs_movements(
        session,
        _all_years_for_cumulative(plan),
        entity_prefix=entity_prefix,
        scenario=scenario,
    )
    return aggregate_bs(movements, structure, plan)
