"""P5 / Phase C1 — Period engine (PURE, no DB).

Generalises the Exit-Readiness year-slice periodicity (docs/PLAN.md §4) into a
deterministic, fully unit-tested set of period definitions used by every
statement endpoint.  This module NEVER touches the database: it only computes
which (fiscal_year, fiscal_period) buckets belong to each presentation column.

A consumer (statements.py) feeds these definitions to a SQL/aggregation layer.

====================================================================== FISCAL MODEL
- A fiscal year has 12 booking periods (1..12).  Period 13 in the schema is the
  consolidation period and is EXCLUDED from every period column here (it is not a
  calendar month).  The period engine only ever references periods 1..12.
- ``current_fy``         : the in-progress fiscal year (e.g. 2025).
- ``last_closed_period`` : the last period of ``current_fy`` that is fully booked
  (0 = nothing closed yet; 12 = the whole current FY is closed).
- Non-calendar fiscal years: the engine is period-based (1..12), not month-based,
  so a fiscal year that starts in, say, April is handled identically — period 1 is
  simply the first fiscal month.  ``fy_start_month`` is carried through only for
  LABELLING the month view; it does not change which periods belong to a column.

====================================================================== YEAR-VIEW COLUMNS (formulas)
Let  C  = current_fy,  L = last_closed_period  (0 ≤ L ≤ 12).

  FY-2   = all periods 1..12 of year (C-2)                         [full prior-prior FY]
  FY-1   = all periods 1..12 of year (C-1)                         [full prior FY]
  FY     = all periods 1..12 of year  C                            [current FY, may be partial in reality]
  YTD    = periods 1..L of year C                                  [year-to-date actuals]
           (L=0 → empty: nothing closed yet)
  YTD_PY = periods 1..L of year (C-1)                              [same window, prior year → comparable]
  LTM    = the last 12 CLOSED periods ending at (C, L):
             if L = 12 →  periods 1..12 of year C
             if L = 0  →  periods 1..12 of year (C-1)              [no current closed → equals full prior FY]
             else      →  periods (L+1..12) of year (C-1)  ∪  periods (1..L) of year C
                          (a 12-period window straddling the FY boundary)
  LTM_PY = the 12 closed periods immediately BEFORE the LTM window (the prior LTM):
             the same straddle shifted back exactly one year.
  Coverage = L / 12   (fraction of the current FY that is actually closed; 0..1).

Worked example (current_fy=2025, last_closed_period=5):
  FY-2     = FY2023 P1..P12
  FY-1     = FY2024 P1..P12
  FY       = FY2025 P1..P12
  YTD      = FY2025 P1..P5
  YTD_PY   = FY2024 P1..P5
  LTM      = FY2024 P6..P12  (7 periods)  ∪  FY2025 P1..P5  (5 periods)   = 12 periods
  LTM_PY   = FY2023 P6..P12  ∪  FY2024 P1..P5                              = 12 periods
  Coverage = 5/12 ≈ 0.4167

====================================================================== EDGE CASES
  • L = 0  (nothing closed): YTD/YTD_PY are empty; LTM = full FY(C-1); Coverage = 0.
  • L = 12 (full year closed): YTD = FY P1..12 (= FY column); LTM = FY P1..12; Coverage = 1.
  • Missing PY data: a column may reference a year with no rows — that is the
    aggregation layer's concern; the period engine still emits the column (the
    statement will simply show 0/None for an empty window).  The engine never
    crashes on missing data because it only produces *definitions*.
  • Non-calendar FY: period indices 1..12 are fiscal, not calendar; the straddle
    math is unaffected.  fy_start_month only affects month labels.

====================================================================== VIEW MODES
``view_mode ∈ {year, month, week}``:
  • year  : the 8 columns above (FY-2, FY-1, FY, YTD, YTD_PY, LTM, LTM_PY) + Coverage.
  • month : one column per fiscal period of the requested actual years, each column
            = exactly one (fiscal_year, fiscal_period).  Default span = FY-1 and FY
            (24 monthly columns) so a rolling year is visible; periods are 1..12.
  • week  : ISO-week granularity.  The GDPdU GL is booked per fiscal_period (month),
            NOT per week, so the engine exposes the SAME period buckets as ``month``
            but tags them view_mode='week' for the frontend toggle; true weekly
            resolution requires posting_date bucketing handled downstream (documented
            limitation — see PLAN §4; not part of C1's GL grain).

All functions are pure and deterministic: same inputs → identical PeriodColumn list.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ViewMode = Literal["year", "month", "week"]

N_PERIODS = 12
PERIODS: tuple[int, ...] = tuple(range(1, N_PERIODS + 1))

# Stable column keys for the year view (frontend relies on these identifiers).
YEAR_COLUMN_KEYS: tuple[str, ...] = ("FY-2", "FY-1", "FY", "YTD", "YTD_PY", "LTM", "LTM_PY")


@dataclass(frozen=True)
class PeriodColumn:
    """A single presentation column = a set of (fiscal_year, fiscal_period) buckets.

    ``buckets`` is the explicit, deterministic list of (year, period) pairs that the
    aggregation layer must SUM for this column.  It is the single source of truth —
    no consumer re-derives period math.
    """

    key: str                       # stable identifier, e.g. "FY", "YTD", "2025-05"
    label: str                     # human label, e.g. "FY 2025", "YTD 2025", "May 2025"
    buckets: tuple[tuple[int, int], ...]  # ((fiscal_year, fiscal_period), ...)
    is_plan: bool = False          # plan/forecast column (filled by statements layer)

    def fiscal_years(self) -> tuple[int, ...]:
        """Distinct fiscal years referenced by this column (sorted)."""
        return tuple(sorted({fy for fy, _ in self.buckets}))


@dataclass(frozen=True)
class PeriodPlan:
    """The full set of columns for a view plus the coverage indicator."""

    view_mode: ViewMode
    current_fy: int
    last_closed_period: int
    columns: tuple[PeriodColumn, ...]
    coverage: float                # fraction of current FY closed (year view); 0..1
    fy_start_month: int = 1        # 1 = calendar year; carried for month labels only


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _validate(current_fy: int, last_closed_period: int) -> None:
    if not isinstance(current_fy, int):
        raise TypeError("current_fy must be int")
    if not (0 <= last_closed_period <= N_PERIODS):
        raise ValueError(
            f"last_closed_period must be in 0..{N_PERIODS}, got {last_closed_period}"
        )


def _full_year(fy: int) -> tuple[tuple[int, int], ...]:
    return tuple((fy, p) for p in PERIODS)


def _periods_upto(fy: int, last: int) -> tuple[tuple[int, int], ...]:
    """Periods 1..last of *fy* (empty when last <= 0)."""
    return tuple((fy, p) for p in PERIODS if p <= last)


def _ltm_window(current_fy: int, last_closed_period: int) -> tuple[tuple[int, int], ...]:
    """The 12 closed periods ending at (current_fy, last_closed_period).

    See module docstring for the straddle formula.  Always returns exactly 12
    (year, period) pairs.
    """
    L = last_closed_period
    if L >= N_PERIODS:
        return _full_year(current_fy)
    if L <= 0:
        return _full_year(current_fy - 1)
    # straddle: tail of prior FY (periods L+1..12) + head of current FY (1..L)
    prior_tail = tuple((current_fy - 1, p) for p in PERIODS if p > L)
    current_head = tuple((current_fy, p) for p in PERIODS if p <= L)
    return prior_tail + current_head


# --------------------------------------------------------------------------- #
# Year view
# --------------------------------------------------------------------------- #
def _month_label(fy: int, period: int, fy_start_month: int) -> str:
    """Label a fiscal period as a calendar month when fy_start_month is known.

    fy_start_month=1 (calendar): period p -> month p of year fy.
    Otherwise the calendar month wraps; the calendar YEAR may roll into fy+1.
    Pure arithmetic, no locale.
    """
    _MONTHS = (
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    )
    # calendar month index 0..11
    cal_idx = (fy_start_month - 1 + (period - 1)) % 12
    # how many calendar-year rollovers
    year_offset = (fy_start_month - 1 + (period - 1)) // 12
    cal_year = fy + year_offset
    return f"{_MONTHS[cal_idx]} {cal_year}"


def year_columns(current_fy: int, last_closed_period: int) -> tuple[PeriodColumn, ...]:
    """The 7 year-slice columns (FY-2..LTM_PY).  Coverage is separate."""
    C, L = current_fy, last_closed_period
    return (
        PeriodColumn("FY-2", f"FY {C - 2}", _full_year(C - 2)),
        PeriodColumn("FY-1", f"FY {C - 1}", _full_year(C - 1)),
        PeriodColumn("FY", f"FY {C}", _full_year(C)),
        PeriodColumn("YTD", f"YTD {C}", _periods_upto(C, L)),
        PeriodColumn("YTD_PY", f"YTD {C - 1}", _periods_upto(C - 1, L)),
        PeriodColumn("LTM", "LTM", _ltm_window(C, L)),
        PeriodColumn("LTM_PY", "LTM PY", _ltm_window(C - 1, L)),
    )


def coverage(last_closed_period: int) -> float:
    """Fraction of the current FY that is closed (0..1)."""
    return last_closed_period / N_PERIODS


# --------------------------------------------------------------------------- #
# Month / week view
# --------------------------------------------------------------------------- #
def month_columns(
    current_fy: int,
    last_closed_period: int,
    *,
    years: tuple[int, ...] | None = None,
    fy_start_month: int = 1,
    view_mode: ViewMode = "month",
) -> tuple[PeriodColumn, ...]:
    """One column per (fiscal_year, fiscal_period) for the requested years.

    Default span = (current_fy - 1, current_fy) → a rolling 24-period view.  Each
    column is exactly one period bucket, so the aggregation layer sums a single
    (year, period).  ``view_mode='week'`` reuses the same buckets (GL is monthly;
    true weekly resolution is a documented downstream concern — see module docstring).
    """
    if years is None:
        years = (current_fy - 1, current_fy)
    cols: list[PeriodColumn] = []
    for fy in years:
        for p in PERIODS:
            cols.append(
                PeriodColumn(
                    key=f"{fy}-{p:02d}",
                    label=_month_label(fy, p, fy_start_month),
                    buckets=((fy, p),),
                )
            )
    return tuple(cols)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def build_period_plan(
    view_mode: ViewMode,
    current_fy: int,
    last_closed_period: int,
    *,
    years: tuple[int, ...] | None = None,
    fy_start_month: int = 1,
) -> PeriodPlan:
    """Return the column definitions + coverage for *view_mode*.

    Deterministic and pure.  Raises ValueError/TypeError on invalid inputs so a
    bad request fails fast at the service boundary rather than producing silently
    wrong columns.
    """
    _validate(current_fy, last_closed_period)
    if view_mode == "year":
        cols = year_columns(current_fy, last_closed_period)
    elif view_mode in ("month", "week"):
        cols = month_columns(
            current_fy,
            last_closed_period,
            years=years,
            fy_start_month=fy_start_month,
            view_mode=view_mode,
        )
    else:  # pragma: no cover - guarded, but explicit
        raise ValueError(f"unknown view_mode {view_mode!r}; expected year|month|week")

    return PeriodPlan(
        view_mode=view_mode,
        current_fy=current_fy,
        last_closed_period=last_closed_period,
        columns=cols,
        coverage=coverage(last_closed_period),
        fy_start_month=fy_start_month,
    )
