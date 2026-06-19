"""P5 / Phase C4 — Working Capital statement + KPI ratios (DSO/DIO/DPO/CCC).

Builds the Net Working Capital lines from BS CUMULATIVE balances (reusing the BS
service ``aggregate_bs`` — same cumulative stock + BS sign convention) and the
flow figures Revenue / COGS from the P&L service (``aggregate_pl`` — same flow +
P&L sign convention).  NO formula is forked: balances come from the BS core,
flows from the P&L core; this module only combines them into NWC and the ratios.

The pure core (``compute_working_capital``) takes the already-built BsStatement +
PlStatement + PeriodPlan and is fully golden-testable with no DB.

======================================================================= NWC LINES (per column, from BS presented balances — positive magnitudes)
    Receivables (AR)   = BS line 'AR'        (asset, presented +)
    Inventory          = BS line 'INVENTORY' (asset, presented +)
    Payables (AP)      = BS line 'AP'        (liability, presented +)
    NWC                = AR + Inventory − AP
        worked: AR=500, Inventory=200, AP=500 → NWC = 500 + 200 − 500 = 200.
        AP is SUBTRACTED because it is a credit-side balance that finances WC.

======================================================================= DAYS / ANNUALIZATION RULE (chosen, documented, tested)
``days_in_period`` is derived from the period column, NOT a flat 365 everywhere:
    • Full-year columns (FY-2, FY-1, FY, LTM, LTM_PY): the window spans 12 fiscal
      periods → days = 365.
    • Year-to-date columns (YTD, YTD_PY): the window spans L fiscal periods
      (L = number of buckets in the column = last_closed_period) → we use the
      PROPORTIONAL rule  days = L × (365 / 12).
      (We deliberately use 365/12 per fiscal period rather than actual calendar
      days, because the GL grain is fiscal-period, not calendar-day; this is the
      one documented choice and it is unit-tested.  L=0 → days=0 → ratios None.)
    • Month/week view columns (one bucket each): days = 365/12 per period.
The rule is implemented in ``_days_in_period(column)`` purely from len(buckets).

Why proportional and not "actual closed days": the engine works on fiscal periods
1..12 (docs/PLAN §4); a fiscal period need not be a calendar month, so 365/12 per
period is the consistent, ERP-neutral choice.  Documented so a reviewer can swap
it for calendar-day counting later without guessing intent.

======================================================================= KPI FORMULAS (each: formula → worked example → edge cases → golden test)
Let  AR, INV, AP        = presented BS balances (positive magnitudes),
     REV  = presented revenue flow for the column (P&L 'REVENUE', positive),
     COGS = positive magnitude of cost of materials for the column
          = − (presented P&L 'COGS')   [P&L COGS is presented negative],
     D    = days_in_period for the column.

  DSO (Days Sales Outstanding) = (AR / REV) × D
        meaning: how many days of revenue are tied up in receivables.
        worked (FY, D=365): (500 / 3000) × 365 = 60.8333… days.
        edge: REV == 0  → None (no div-by-zero).  REV < 0 → computed with sign
              (informational).  AR < 0 (credit balance) → negative DSO (informational).

  DIO (Days Inventory Outstanding) = (INV / COGS) × D
        meaning: how many days of cost sit in inventory.
        worked (FY, D=365): (200 / 500) × 365 = 146.0 days.
        edge: COGS == 0 → None.

  DPO (Days Payable Outstanding) = (AP / COGS) × D
        meaning: how many days the firm takes to pay suppliers.
        worked (FY, D=365): (500 / 500) × 365 = 365.0 days.
        edge: COGS == 0 → None.

  CCC (Cash Conversion Cycle) = DSO + DIO − DPO
        meaning: net days cash is tied up in operations.
        worked (FY): 60.8333… + 146.0 − 365.0 = −158.1667… days.
        edge: if ANY component is None, CCC = None (cannot net an undefined term).

======================================================================= EDGE CASES (summary)
  • Zero revenue  → DSO None;  zero COGS → DIO None, DPO None;  → CCC None.
  • Empty period (no buckets / L=0): D=0 → all ratios 0/None per the formula; NWC
    lines = 0 (BS stock over empty cutoff is 0).
  • Negative balances or negative flow: ratios computed with sign (informational),
    never clamped, never silently zeroed.
  • Partial year (YTD): D scales with L (L×365/12), so the ratio annualizes the
    partial flow correctly (a half-year of revenue uses ~182.5 days, not 365).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.services.statements import StatementCell, StatementLine

DAYS_PER_YEAR = 365.0
PERIODS_PER_YEAR = 12
DAYS_PER_PERIOD = DAYS_PER_YEAR / PERIODS_PER_YEAR  # 30.4166…


@dataclass
class WcStatement:
    view_mode: str
    coverage: float
    column_keys: list[str]
    column_labels: dict[str, str]
    lines: list[StatementLine]        # NWC lines + ratio lines (ratios as 'calc')
    days_in_period: dict[str, float]  # the D used per column (for transparency)


# --------------------------------------------------------------------------- #
# Days / annualization
# --------------------------------------------------------------------------- #
def _days_in_period(col: Any) -> float:
    """Days represented by a column, from its bucket count (see module docstring).

    12 buckets → full year → 365.  Otherwise L buckets → L × (365/12).
    0 buckets → 0.0 (→ ratios become None).
    """
    n = len(col.buckets)
    if n >= PERIODS_PER_YEAR:
        return DAYS_PER_YEAR
    return n * DAYS_PER_PERIOD


# --------------------------------------------------------------------------- #
# Ratio helpers — each returns None on a zero/undefined denominator.
# --------------------------------------------------------------------------- #
def _ratio_days(numerator: Optional[float], denom: Optional[float], days: float) -> Optional[float]:
    """(numerator / denom) × days, or None when denom is None/0 or days is 0."""
    if denom is None or denom == 0.0:
        return None
    if days == 0.0:
        return None
    if numerator is None:
        numerator = 0.0
    return (numerator / denom) * days


def _cell_value(stmt: Any, line_code: Any, column_key: str) -> Optional[float]:
    """Lookup a (line, column) value in a Bs/Pl statement; None if absent.

    ``line_code`` may be a single code OR a tuple/list of CANDIDATE codes tried in
    order (first present wins).  Candidates let the same WC core resolve either the
    synthetic-fixture codes (REVENUE/COGS) OR the real seeded P&L codes
    (NET_SALES/COST_OF_MATERIALS) without forking the formula.
    """
    candidates = (line_code,) if isinstance(line_code, str) else tuple(line_code)
    for code in candidates:
        for ln in stmt.lines:
            if ln.line_code == code:
                for c in ln.cells:
                    if c.column_key == column_key:
                        return c.value
    return None


# --------------------------------------------------------------------------- #
# Pure core
# --------------------------------------------------------------------------- #
def compute_working_capital(
    bs: Any,   # BsStatement (cumulative balances, presented positive)
    pl: Any,   # PlStatement (flow, P&L sign)
    period_plan: Any,
    *,
    ar_code: Any = "AR",
    inventory_code: Any = "INVENTORY",
    ap_code: Any = "AP",
    # Revenue / COGS are looked up by CANDIDATE codes: the real seeded P&L codes
    # (NET_SALES / COST_OF_MATERIALS) first, then the synthetic-fixture codes
    # (REVENUE / COGS) so both the live structure and the golden tests resolve.
    revenue_code: Any = ("NET_SALES", "REVENUE"),
    cogs_code: Any = ("COST_OF_MATERIALS", "COGS"),
) -> WcStatement:
    """Combine BS balances + P&L flows into NWC lines and DSO/DIO/DPO/CCC.  PURE.

    Reuses the BS cumulative balances and the P&L flows verbatim — no re-derivation
    of either.  Ratios per the module-docstring formulas; None on zero denominators.
    """
    columns = list(period_plan.columns)
    days: dict[str, float] = {col.key: _days_in_period(col) for col in columns}

    # Gather per-column inputs.
    ar: dict[str, Optional[float]] = {}
    inv: dict[str, Optional[float]] = {}
    ap: dict[str, Optional[float]] = {}
    nwc: dict[str, Optional[float]] = {}
    rev: dict[str, Optional[float]] = {}
    cogs_mag: dict[str, Optional[float]] = {}
    dso: dict[str, Optional[float]] = {}
    dio: dict[str, Optional[float]] = {}
    dpo: dict[str, Optional[float]] = {}
    ccc: dict[str, Optional[float]] = {}

    for col in columns:
        k = col.key
        a = _cell_value(bs, ar_code, k) or 0.0
        i = _cell_value(bs, inventory_code, k) or 0.0
        p = _cell_value(bs, ap_code, k) or 0.0
        ar[k], inv[k], ap[k] = a, i, p
        nwc[k] = a + i - p

        r = _cell_value(pl, revenue_code, k)
        c_presented = _cell_value(pl, cogs_code, k)
        # COGS positive magnitude = −(presented COGS) (P&L presents cost negative).
        c_mag = (-(c_presented)) if c_presented is not None else None
        rev[k] = r
        cogs_mag[k] = c_mag

        d = days[k]
        dso[k] = _ratio_days(a, r, d)
        dio[k] = _ratio_days(i, c_mag, d)
        dpo[k] = _ratio_days(p, c_mag, d)
        # CCC: None if any component undefined (cannot net an undefined term).
        if dso[k] is None or dio[k] is None or dpo[k] is None:
            ccc[k] = None
        else:
            ccc[k] = dso[k] + dio[k] - dpo[k]

    def _line(code: str, label: str, vals: dict[str, Optional[float]], *, row_type: str,
              is_bold: bool = False, kpi_code: Optional[str] = None) -> StatementLine:
        return StatementLine(
            line_code=code,
            label=label,
            row_type=row_type,
            is_bold=is_bold,
            kpi_code=kpi_code,
            cells=[StatementCell(column_key=col.key, value=vals[col.key]) for col in columns],
        )

    lines = [
        _line("AR", "Accounts receivable", ar, row_type="mapping"),
        _line("INVENTORY", "Inventory", inv, row_type="mapping"),
        _line("AP", "Accounts payable", ap, row_type="mapping"),
        _line("NWC", "Net working capital", nwc, row_type="subtotal", is_bold=True),
        _line("DSO", "DSO (days)", dso, row_type="calc", kpi_code="DSO"),
        _line("DIO", "DIO (days)", dio, row_type="calc", kpi_code="DIO"),
        _line("DPO", "DPO (days)", dpo, row_type="calc", kpi_code="DPO"),
        _line("CCC", "CCC (days)", ccc, row_type="calc", kpi_code="CCC", is_bold=True),
    ]

    return WcStatement(
        view_mode=period_plan.view_mode,
        coverage=period_plan.coverage,
        column_keys=[c.key for c in columns],
        column_labels={c.key: c.label for c in columns},
        lines=lines,
        days_in_period=days,
    )


# --------------------------------------------------------------------------- #
# Orchestration (thin; reuses BS + P&L builders)
# --------------------------------------------------------------------------- #
def build_wc_statement(
    session: Any,
    *,
    view_mode: str,
    current_fy: int,
    last_closed_period: int,
    entity_prefix: Optional[str] = None,
    scenario: Optional[str] = None,
    fy_start_month: int = 1,
) -> WcStatement:
    """Build BS (cumulative) + P&L (flow) then combine into the WC statement."""
    from app.services.balance_sheet import build_bs_statement
    from app.services.periods import build_period_plan
    from app.services.statements import build_pl_statement

    plan = build_period_plan(
        view_mode,  # type: ignore[arg-type]
        current_fy,
        last_closed_period,
        fy_start_month=fy_start_month,
    )
    bs = build_bs_statement(
        session,
        view_mode=view_mode,
        current_fy=current_fy,
        last_closed_period=last_closed_period,
        entity_prefix=entity_prefix,
        scenario=scenario,
        fy_start_month=fy_start_month,
    )
    pl = build_pl_statement(
        session,
        view_mode=view_mode,
        current_fy=current_fy,
        last_closed_period=last_closed_period,
        entity_prefix=entity_prefix,
        scenario=scenario,
        fy_start_month=fy_start_month,
    )
    return compute_working_capital(bs, pl, plan)
