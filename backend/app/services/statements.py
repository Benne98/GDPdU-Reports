"""P5 / Phase C1 — P&L statement service.

Aggregates ``fact_gl_line`` over ``dim_gl_account`` (level_0 = 'PL') per period
column from the period engine, builds the P&L presentation via ``dim_pl_structure``,
and computes derived lines (Gross profit, Gross margin %, EBITDA, …).

The CALCULATION core (``aggregate_pl``) is PURE: it operates on already-fetched
rows + a structure definition + a PeriodPlan, with NO database access, so it is
fully golden-testable with a tiny synthetic fixture.  A thin DB helper
(``fetch_pl_movements``) does the SQL and feeds the pure core.

====================================================================== SIGN CONVENTION (adopted — never flipped)
Authority: docs/db/gl-target-structure.md §6.
  Storage  : fact_gl_line.amount is signed → ``+`` = Soll (debit), ``−`` = Haben (credit).
  P&L view : we present every P&L line as  ``presented = − amount``.
             ⇒ revenue (credit, stored −3000) presents as **+3000** (income, positive).
             ⇒ cost    (debit,  stored  +500) presents as  **−500** (reduces profit).
  This is the SAME rule as fact_sales.gross_sales = −amount.  It is documented HERE
  once and applied in exactly one place (``_present``).  It is NEVER flipped elsewhere.

Consequence for subtotals: with ``presented = −amount``, a P&L line's presented
value is its contribution to profit (income +, expense −), so any subtotal /
EBITDA / EBIT is simply the SUM of its member presented values — no per-line sign
juggling.  Gross margin and other ratios divide presented profit by presented
revenue.

====================================================================== STRUCTURE MODEL (dim_pl_structure)
Each structure row drives one output line.  Fields used (schema 0001):
  sort_order   : presentation order (ascending).
  line_code    : stable id of the line (e.g. 'REVENUE', 'GROSS_PROFIT').
  balance_title: display label.
  row_type     : 'mapping'  → value = Σ presented(GL) for accounts matching the
                              line's level_2/level_3/level_4 filter.
                 'calc'     → derived line.  A *_PCT kpi_code makes it a RATIO
                              (computed by formula); ANY OTHER calc is the running
                              cumulative sum of all mapping lines above it (same as
                              'subtotal').  Real Decidra structures define no _PCT
                              line, so every non-mapping row is a running cumulative
                              sum there.
                 'subtotal' → running cumulative sum of EVERY preceding 'mapping'
                              line from the TOP of the statement (subtotals/calcs are
                              never re-counted).
  calc_type    : for row_type='calc', selects the derived formula (see below).
  level_2/3/4  : GL hierarchy filter for 'mapping' lines (None = no constraint on
                 that level).  A GL account matches a line when every non-null
                 level_n on the line equals the account's level_n.
  kpi_code     : optional explicit metric id; takes precedence over calc_type when
                 set (e.g. 'GROSS_MARGIN_PCT', 'EBITDA', 'GROSS_PROFIT').
  is_bold      : presentation hint, passed through.

====================================================================== DERIVED LINES (formulas)
All operate per period column.  The P&L uses STANDARD RUNNING-TOTAL semantics:
every non-mapping, non-ratio row is the CUMULATIVE SUM of all mapping lines above
it, presented sign.  Formally, for a subtotal/calc at ``sort_order = S``, column C:

  value(S, C) = Σ value(mapping line L, C)   for all mapping L with sort_order < S

Subtotals/calcs are NEVER re-counted (only mapping rows contribute).  Because every
mapping value is already a profit contribution (income +, expense −), the running
sum needs no per-line sign juggling.  This single rule covers TOTAL_OUTPUT,
GROSS_PROFIT, EBITDA, EBIT, EBT, NET_PROFIT, … — they differ only by where they sit
in the structure (which mapping lines fall above them).

  GROSS_PROFIT (Decidra sort 6) = NET_SALES + FINISHED_GOODS_WIP +
        OWN_WORK_CAPITALISED + COST_OF_MATERIALS  (the four mapping lines above it).
        worked: 1000 + 100 + 0 + (−400) = +700.

  NET_PROFIT (last subtotal) = Σ of EVERY mapping line in the statement.  KEY
        INVARIANT: NET_PROFIT == total presented P&L movement.

RATIO lines (row_type='calc' with a *_PCT kpi_code, e.g. GROSS_MARGIN_PCT) are the
ONLY exception — they are computed by formula, not by running sum:

  GROSS_MARGIN_PCT  = GROSS_PROFIT / NET_SALES × 100      (None if denominator == 0)
        edge: denominator == 0  ⇒ None (never divide by zero, never crash).
        edge: denominator < 0 (net credit-note period)  ⇒ ratio still computed
              (sign carried), documented as informational.
  Real Decidra structures define NO _PCT line, so running-sum covers every
  non-mapping row there; the ratio path is kept for structures that DO define one.

====================================================================== WORKED EXAMPLE (golden)
Synthetic GL (presented = −amount), Decidra-shaped structure, one column:
  NET_SALES presented +1000, FINISHED_GOODS_WIP +100, COST_OF_MATERIALS −400,
  PERSONNEL_EXPENSES −200.
Structure: NET_SALES(mapping), FINISHED_GOODS_WIP(mapping), TOTAL_OUTPUT(subtotal),
  COST_OF_MATERIALS(mapping), GROSS_PROFIT(calc), PERSONNEL_EXPENSES(mapping),
  NET_PROFIT(subtotal).
  ⇒ TOTAL_OUTPUT = 1000 + 100              = 1100
    GROSS_PROFIT = 1000 + 100 + (−400)     = 700
    NET_PROFIT   = 1000 + 100 − 400 − 200  = 500  == Σ all mapping lines.

====================================================================== EDGE CASES
  • Subtotal/calc with no mapping lines above it → 0.0 (empty running sum).
  • Ratio (_PCT) with zero denominator → None (not 0, not crash).
  • Missing account / empty period window: that line's value = 0.0 (the bucket sum
    over no rows is 0); a ratio then None if its denominator is 0.
  • Negative revenue (net credit notes): values flow through; ratios computed with
    sign; documented as informational, never silently zeroed.
  • Plan/scenario column: same aggregation over fact_gl_plan movements (presented
    = −amount likewise); is_plan flag set on the PeriodColumn.
  • Unmapped GL account (no structure line matches): contributes to NO line — it is
    surfaced via ``unmapped_total`` so the caller can reconcile against the GL sum
    (Σ presented over all PL rows == Σ over all lines + unmapped_total).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# --------------------------------------------------------------------------- #
# Data shapes (pure)
# --------------------------------------------------------------------------- #

# A GL movement row fed to the pure core: one (fy, period, level_2, level_3,
# level_4, amount) tuple.  ``amount`` is the STORED signed amount (+debit/-credit).
@dataclass(frozen=True)
class GlMovement:
    fiscal_year: int
    fiscal_period: int
    level_2: Optional[str]
    level_3: Optional[str]
    level_4: Optional[str]
    amount: float  # stored sign (+debit, -credit)


@dataclass(frozen=True)
class StructureLine:
    sort_order: int
    line_code: str
    balance_title: str
    row_type: str                 # 'mapping' | 'subtotal' | 'calc'
    level_2: Optional[str] = None
    level_3: Optional[str] = None
    level_4: Optional[str] = None
    calc_type: Optional[int] = None
    kpi_code: Optional[str] = None
    is_bold: bool = False


@dataclass
class StatementCell:
    """One (line × column) value plus the column key it belongs to."""

    column_key: str
    value: Optional[float]        # presented value; None only for undefined ratios


@dataclass
class StatementLine:
    line_code: str
    label: str
    row_type: str
    is_bold: bool
    kpi_code: Optional[str]
    cells: list[StatementCell]
    # Structure filter keys — forwarded to the API so the client can drill to bookings.
    level_2: Optional[str] = None
    level_3: Optional[str] = None
    level_4: Optional[str] = None


@dataclass
class PlStatement:
    view_mode: str
    coverage: float
    column_keys: list[str]
    column_labels: dict[str, str]
    lines: list[StatementLine]
    # Σ presented over GL rows that matched NO mapping line, per column → reconciliation.
    unmapped_total: dict[str, float]


# --------------------------------------------------------------------------- #
# Sign convention — the ONE place we apply it.
# --------------------------------------------------------------------------- #
def _present(stored_amount: float) -> float:
    """Convert a STORED signed GL amount to the P&L PRESENTED value.

    presented = -amount  (revenue credit → +income, cost debit → -expense).
    This is the single, documented application of the sign convention.
    """
    return -float(stored_amount)


# --------------------------------------------------------------------------- #
# Matching a GL movement to a mapping structure line
# --------------------------------------------------------------------------- #
def _matches(line: StructureLine, mv: GlMovement) -> bool:
    """A GL movement matches a mapping line when every non-null level_n on the
    line equals the movement's level_n.  A null level_n on the line is a wildcard.
    """
    if line.level_2 is not None and line.level_2 != mv.level_2:
        return False
    if line.level_3 is not None and line.level_3 != mv.level_3:
        return False
    if line.level_4 is not None and line.level_4 != mv.level_4:
        return False
    return True


# --------------------------------------------------------------------------- #
# KPI / calc resolution
# --------------------------------------------------------------------------- #
def _effective_kpi(line: StructureLine) -> Optional[str]:
    """kpi_code wins; else map known calc_type ints to a kpi id.

    calc_type legend (this module's convention, documented here):
       1 = plain mapping line (handled by row_type, not a derived kpi)
       2 = GROSS_PROFIT (running-sum subtotal; kpi label kept for display)
       3 = GROSS_MARGIN_PCT (ratio)
       4 = EBITDA (running-sum subtotal; kpi label kept for display)
    """
    if line.kpi_code:
        return line.kpi_code
    return {2: "GROSS_PROFIT", 3: "GROSS_MARGIN_PCT", 4: "EBITDA"}.get(line.calc_type or 0)


def _is_ratio(kpi: Optional[str]) -> bool:
    """A calc line is a RATIO iff its effective kpi_code ends with '_PCT'.

    Ratios (e.g. GROSS_MARGIN_PCT) are computed by formula; every other non-mapping
    row is the running cumulative sum of the mapping lines above it.  Real Decidra
    structures define no _PCT line, so this returns False for all of them.
    """
    return bool(kpi) and kpi.upper().endswith("_PCT")


# --------------------------------------------------------------------------- #
# Pure aggregation core
# --------------------------------------------------------------------------- #
def aggregate_pl(
    movements: list[GlMovement],
    structure: list[StructureLine],
    period_plan: Any,  # app.services.periods.PeriodPlan (avoid import cycle for typing)
) -> PlStatement:
    """Build the P&L matrix.  PURE — no DB, deterministic.

    For each period column (from period_plan) and each structure line:
      • 'mapping'  → Σ presented(amount) over movements in the column's buckets that
                     match the line's level filter.
      • 'subtotal' → running cumulative Σ of EVERY preceding 'mapping' line from the
                     top of the statement (subtotals/calcs are never re-counted).
      • 'calc'     → a *_PCT kpi → ratio formula (GROSS_MARGIN_PCT); ANY OTHER calc
                     (e.g. GROSS_PROFIT, EBITDA) → same running cumulative sum as a
                     subtotal.

    Reconciliation: every column also reports ``unmapped_total`` = Σ presented over
    movements that matched no mapping line, so
        Σ(mapping line values) + unmapped_total == Σ presented(all movements).
    """
    columns = list(period_plan.columns)
    structure = sorted(structure, key=lambda s: s.sort_order)

    # Index movements by bucket for fast per-column summation.
    # bucket = (fiscal_year, fiscal_period)
    by_bucket: dict[tuple[int, int], list[GlMovement]] = {}
    for mv in movements:
        by_bucket.setdefault((mv.fiscal_year, mv.fiscal_period), []).append(mv)

    # Pre-compute, per column, the list of movements in that column.
    col_movements: dict[str, list[GlMovement]] = {}
    for col in columns:
        mvs: list[GlMovement] = []
        for bucket in col.buckets:
            mvs.extend(by_bucket.get(bucket, []))
        col_movements[col.key] = mvs

    out_lines: list[StatementLine] = []
    # Per-column: value of each emitted line (by line_code) for ratio references.
    emitted: dict[str, dict[str, Optional[float]]] = {col.key: {} for col in columns}
    # Per-column: which GL movements were claimed by some mapping line (for unmapped).
    claimed: dict[str, set[int]] = {col.key: set() for col in columns}
    # Per-column running cumulative sum of all MAPPING lines seen so far (presented).
    # This is the value of any non-ratio subtotal/calc encountered at this point.
    running_sum: dict[str, float] = {col.key: 0.0 for col in columns}

    for line in structure:
        cells: list[StatementCell] = []
        kpi = _effective_kpi(line)

        for col in columns:
            mvs = col_movements[col.key]
            if line.row_type == "mapping":
                total = 0.0
                for mv in mvs:
                    if _matches(line, mv):
                        total += _present(mv.amount)
                        claimed[col.key].add(id(mv))
                running_sum[col.key] += total
                value: Optional[float] = total
            elif line.row_type in ("subtotal", "calc"):
                if line.row_type == "calc" and _is_ratio(kpi):
                    # Ratio line: computed by formula from already-emitted values.
                    value = _compute_ratio(kpi, emitted[col.key])
                else:
                    # Running-total semantics: cumulative sum of all mapping lines
                    # above this row (subtotals/calcs not re-counted).
                    value = running_sum[col.key]
            else:  # unknown row_type → treated as empty, never crash
                value = None
            emitted[col.key][line.line_code] = value
            cells.append(StatementCell(column_key=col.key, value=value))

        out_lines.append(
            StatementLine(
                line_code=line.line_code,
                label=line.balance_title,
                row_type=line.row_type,
                is_bold=line.is_bold,
                kpi_code=kpi,
                cells=cells,
                level_2=line.level_2,
                level_3=line.level_3,
                level_4=line.level_4,
            )
        )

    # Unmapped reconciliation per column.
    unmapped: dict[str, float] = {}
    for col in columns:
        claimed_ids = claimed[col.key]
        total = 0.0
        for mv in col_movements[col.key]:
            if id(mv) not in claimed_ids:
                total += _present(mv.amount)
        unmapped[col.key] = total

    return PlStatement(
        view_mode=period_plan.view_mode,
        coverage=period_plan.coverage,
        column_keys=[c.key for c in columns],
        column_labels={c.key: c.label for c in columns},
        lines=out_lines,
        unmapped_total=unmapped,
    )


def _compute_ratio(kpi: Optional[str], col_values: dict[str, Optional[float]]) -> Optional[float]:
    """Ratio (*_PCT) metrics within a single column from already-emitted line values.

    Only RATIO lines reach here; all other subtotal/calc rows use the running
    cumulative sum (see ``aggregate_pl``).  Currently the one supported ratio is
    GROSS_MARGIN_PCT = GROSS_PROFIT / revenue × 100, where ``revenue`` is the first
    available of 'NET_SALES' / 'REVENUE' (None when the denominator is 0/None).
    """
    if kpi == "GROSS_MARGIN_PCT":
        gp = col_values.get("GROSS_PROFIT")
        if gp is None:
            # Fallback for the minimal default structure: REVENUE + COGS.
            gp = (col_values.get("REVENUE") or 0.0) + (col_values.get("COGS") or 0.0)
        rev = col_values.get("NET_SALES")
        if rev is None:
            rev = col_values.get("REVENUE")
        if not rev:  # None or 0.0 → undefined margin
            return None
        return gp / rev * 100.0
    return None


# --------------------------------------------------------------------------- #
# Default in-code P&L structure (used until dim_pl_structure is seeded).
# --------------------------------------------------------------------------- #
def default_pl_structure() -> list[StructureLine]:
    """Minimal P&L structure matching the synthetic fixture's level_3 labels.

    This is a FALLBACK used when dim_pl_structure has no rows yet (the table exists
    in schema 0001 but is unseeded).  When the table is seeded, ``fetch_structure``
    returns the DB rows instead and this is not used.
    """
    return [
        StructureLine(10, "REVENUE", "Revenue", "mapping", level_3="Net sales"),
        StructureLine(20, "COGS", "Cost of materials", "mapping", level_3="Cost of materials"),
        StructureLine(30, "GROSS_PROFIT", "Gross profit", "calc", kpi_code="GROSS_PROFIT", is_bold=True),
        StructureLine(40, "GROSS_MARGIN_PCT", "Gross margin %", "calc", kpi_code="GROSS_MARGIN_PCT"),
        StructureLine(50, "EBITDA", "EBITDA", "calc", kpi_code="EBITDA", is_bold=True),
    ]


# --------------------------------------------------------------------------- #
# DB read helpers (thin; not unit-tested without a DB — exercised via overrides)
# --------------------------------------------------------------------------- #
def fetch_structure(session: Any) -> list[StructureLine]:
    """Load dim_pl_structure; fall back to default_pl_structure() when empty."""
    from sqlalchemy import text

    rows = session.execute(
        text(
            "SELECT sort_order, line_code, COALESCE(balance_title,'') AS balance_title, "
            "row_type, level_2, level_3, level_4, calc_type, kpi_code, is_bold "
            "FROM dim_pl_structure ORDER BY sort_order"
        )
    ).fetchall()
    if not rows:
        return default_pl_structure()
    return [
        StructureLine(
            sort_order=int(r[0]),
            line_code=r[1],
            balance_title=r[2],
            row_type=r[3],
            level_2=r[4],
            level_3=r[5],
            level_4=r[6],
            calc_type=int(r[7]) if r[7] is not None else None,
            kpi_code=r[8],
            is_bold=bool(r[9]),
        )
        for r in rows
    ]


def fetch_pl_movements(
    session: Any,
    fiscal_years: tuple[int, ...],
    *,
    entity_prefix: Optional[str] = None,
    scenario: Optional[str] = None,
) -> list[GlMovement]:
    """Read P&L GL movements (level_0='PL') per (fy, period, level_2/3/4).

    When ``scenario`` is given, read plan movements from fact_gl_plan instead of
    actuals from fact_gl_line; both carry the SAME stored sign convention, so the
    pure core treats them identically.

    Only level_0 = 'PL' accounts are returned.  Period 13 (consolidation) is
    excluded — the period engine never references it anyway.
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
        params["scenario"] = scenario
        sql = text(f"""
            SELECT pl.fiscal_year, pl.fiscal_period,
                   a.level_2, a.level_3, a.level_4,
                   SUM(pl.amount) AS amount
            FROM fact_gl_plan pl
            JOIN dim_gl_account a
              ON a.account_number_group = pl.account_number_group
             AND a.fiscal_year = pl.fiscal_year
            WHERE a.level_0 = 'PL'
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
            WHERE a.level_0 = 'PL'
              AND e.fiscal_year = ANY(:years)
              AND e.fiscal_period BETWEEN 1 AND 12
              {entity_clause}
            GROUP BY e.fiscal_year, e.fiscal_period, a.level_2, a.level_3, a.level_4
        """)

    rows = session.execute(sql, params).fetchall()
    return [
        GlMovement(
            fiscal_year=int(r[0]),
            fiscal_period=int(r[1]),
            level_2=r[2],
            level_3=r[3],
            level_4=r[4],
            amount=float(r[5]),
        )
        for r in rows
    ]


def build_pl_statement(
    session: Any,
    *,
    view_mode: str,
    current_fy: int,
    last_closed_period: int,
    entity_prefix: Optional[str] = None,
    scenario: Optional[str] = None,
    fy_start_month: int = 1,
) -> PlStatement:
    """Orchestrate: period plan → fetch movements → pure aggregate.

    Thin glue; all math is in ``aggregate_pl``.  Used by the router.
    """
    from app.services.periods import build_period_plan

    plan = build_period_plan(
        view_mode,  # type: ignore[arg-type]
        current_fy,
        last_closed_period,
        fy_start_month=fy_start_month,
    )
    # Every fiscal year referenced by any column.
    years: set[int] = set()
    for col in plan.columns:
        years.update(col.fiscal_years())

    structure = fetch_structure(session)
    movements = fetch_pl_movements(
        session,
        tuple(sorted(years)),
        entity_prefix=entity_prefix,
        scenario=scenario,
    )
    return aggregate_pl(movements, structure, plan)
