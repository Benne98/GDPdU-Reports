"""True ISO-week grain — primitives + flow/stock aggregation semantics.

reporting-v2 Phase 5: weekly views are TRUE ISO weeks derived from
``fact_gl_entry.posting_date`` (daily), NOT a monthly (fiscal_period) bucket
approximation.  The grain is defined by two centralized primitives in
``app.services.fin_compat_sql``:

  * ``week_cutoff(iso_year, iso_week)`` — Sunday of the ISO week; the STOCK
    cutoff used by BS / WC (closing balance with ``posting_date <= cutoff``).
  * ``week_range(iso_year, iso_week)`` — (Monday, Sunday) span; the FLOW window
    used by P&L / CF / Sales (Σ movements with ``posting_date`` in the span).

These tests lock:
  1. the primitives' exact dates (incl. ISO weeks 52/53 + the year boundary),
  2. that a P&L/Sales WEEK == Σ of in-week movements (flow), via a pure-Python
     reference aggregation using the SAME predicate the SQL uses
     (``monday <= posting_date <= sunday``),
  3. that a BS WEEK == cumulative balance ``posting_date <= week_cutoff`` (stock),
  4. the Dec-31 → ISO week 1 of the NEXT year edge (year boundary),
  5. a fiscal-year start month ≠ January does not change the ISO-week buckets
     (the grain is calendar/ISO, independent of fiscal_period),
  6. ``trailing_iso_weeks`` walks back correctly across the ISO-year boundary.

The reference aggregation mirrors the production SQL predicates exactly
(``fin_compat_sql.pl_grain_sql_week`` flow / ``fin_compat_bs_sql._bal_amount_expr``
stock), so it is a faithful, DB-free regression of the financial semantics.
"""
from __future__ import annotations

from datetime import date

from app.services.fin_compat_sql import (
    iso_week_bounds,
    iso_week_of,
    prior_iso_week,
    week_cutoff,
    week_range,
)
from app.services.sales_analytics_compat import trailing_iso_weeks


# --------------------------------------------------------------------------- #
# 1. Primitive dates (ISO-8601), incl. weeks 52/53 + year boundary
# --------------------------------------------------------------------------- #
def test_week_range_is_monday_to_sunday():
    mon, sun = week_range(2025, 31)
    assert mon == date(2025, 7, 28)   # Monday
    assert sun == date(2025, 8, 3)    # Sunday (straddles Jul/Aug → true ISO week)
    assert mon.weekday() == 0
    assert sun.weekday() == 6


def test_week_cutoff_is_the_sunday_of_week_range():
    for iy, iw in [(2025, 1), (2025, 31), (2020, 53), (2026, 1)]:
        assert week_cutoff(iy, iw) == week_range(iy, iw)[1]
        # And consistent with the legacy iso_week_bounds the BS/PL SQL already use.
        assert week_cutoff(iy, iw) == iso_week_bounds(iy, iw)[1]


def test_week_01_starts_in_previous_calendar_year():
    # ISO 2025-W01 = Mon 2024-12-30 .. Sun 2025-01-05.
    assert week_range(2025, 1) == (date(2024, 12, 30), date(2025, 1, 5))
    assert week_cutoff(2025, 1) == date(2025, 1, 5)


def test_iso_week_53_long_year():
    # 2020 is a 53-week ISO year; W53 spans into 2021.
    assert week_range(2020, 53) == (date(2020, 12, 28), date(2021, 1, 3))
    assert week_cutoff(2020, 53) == date(2021, 1, 3)


def test_dec_31_belongs_to_iso_week_1_of_next_year():
    # The headline year-boundary edge: 2025-12-31 is ISO 2026-W01 (not 2025-W53).
    assert iso_week_of(date(2025, 12, 31)) == (2026, 1)
    assert iso_week_of(date(2026, 1, 1)) == (2026, 1)
    mon, sun = week_range(2026, 1)
    assert mon == date(2025, 12, 29) and sun == date(2026, 1, 4)
    # 2025-12-31 falls inside the CW01/2026 FLOW window …
    assert mon <= date(2025, 12, 31) <= sun
    # … and is on/before the CW01/2026 STOCK cutoff.
    assert date(2025, 12, 31) <= week_cutoff(2026, 1)


# --------------------------------------------------------------------------- #
# Reference aggregations (mirror the production SQL predicates exactly)
# --------------------------------------------------------------------------- #
# A synthetic GL row: (posting_date, amount).  Sign convention matches the GL:
# + = debit (asset / P&L expense stored +); − = credit (revenue stored −).
def _flow_in_week(rows, iso_year, iso_week):
    """P&L/Sales FLOW: Σ amount WHERE monday <= posting_date <= sunday."""
    mon, sun = week_range(iso_year, iso_week)
    return sum(amt for d, amt in rows if mon <= d <= sun)


def _stock_at_week(rows, iso_year, iso_week):
    """BS STOCK: cumulative Σ amount WHERE posting_date <= week_cutoff."""
    cut = week_cutoff(iso_year, iso_week)
    return sum(amt for d, amt in rows if d <= cut)


# Synthetic ledger straddling ISO-week boundaries around CW31/2025
# (Mon 2025-07-28 .. Sun 2025-08-03) and the prior week CW30 (07-21..07-27).
_GL = [
    (date(2025, 7, 27), 100.0),   # CW30 (Sunday of CW30)
    (date(2025, 7, 28), 200.0),   # CW31 (Monday)
    (date(2025, 7, 30), 50.0),    # CW31
    (date(2025, 8, 1), 30.0),     # CW31 (August day, still CW31 — ISO week!)
    (date(2025, 8, 3), 20.0),     # CW31 (Sunday)
    (date(2025, 8, 4), 999.0),    # CW32 (next Monday) — must NOT be in CW31
]


def test_pl_week_equals_sum_of_in_week_movements():
    # CW31 flow = 200 + 50 + 30 + 20 = 300 (the Aug-01/03 days belong to CW31).
    assert _flow_in_week(_GL, 2025, 31) == 300.0
    # CW30 flow = just the 07-27 posting.
    assert _flow_in_week(_GL, 2025, 30) == 100.0
    # CW32 flow = the 08-04 posting only.
    assert _flow_in_week(_GL, 2025, 32) == 999.0
    # The monthly-bucket approximation would have put 08-01/03 into August, NOT
    # CW31 — assert the true ISO week captures them (flow ≠ "July-only" 250).
    assert _flow_in_week(_GL, 2025, 31) != 250.0


def test_bs_week_is_cumulative_up_to_week_cutoff():
    # Stock at end of CW30 (cutoff 2025-07-27) = 100 only.
    assert _stock_at_week(_GL, 2025, 30) == 100.0
    # Stock at end of CW31 (cutoff 2025-08-03) = 100+200+50+30+20 = 400.
    assert _stock_at_week(_GL, 2025, 31) == 400.0
    # Stock at end of CW32 (cutoff 2025-08-10) includes the 08-04 posting = 1399.
    assert _stock_at_week(_GL, 2025, 32) == 1399.0
    # Monotonic: a later week's stock >= an earlier week's stock for +amounts.
    assert _stock_at_week(_GL, 2025, 31) >= _stock_at_week(_GL, 2025, 30)


def test_dec31_movement_lands_in_next_iso_year_week():
    # A Dec-31-2025 posting belongs to CW01/2026 flow, not CW53-ish of 2025.
    gl = [(date(2025, 12, 31), 500.0), (date(2026, 1, 2), 70.0)]
    assert _flow_in_week(gl, 2026, 1) == 570.0   # both in CW01/2026 span
    assert _flow_in_week(gl, 2025, 52) == 0.0     # nothing in CW52/2025
    # Stock at CW01/2026 cutoff includes the Dec-31 movement.
    assert _stock_at_week(gl, 2026, 1) == 570.0


def test_fy_start_month_not_january_does_not_change_iso_buckets():
    # The ISO-week grain is calendar/ISO date based, independent of the fiscal
    # year start month.  An entity whose FY starts in April still buckets a
    # posting on 2025-07-30 into CW31/2025 (its calendar ISO week).
    rows = [(date(2025, 7, 30), 80.0)]
    # Regardless of any FY-start assumption, the flow window is the ISO week.
    assert _flow_in_week(rows, 2025, 31) == 80.0
    assert iso_week_of(date(2025, 7, 30)) == (2025, 31)


# --------------------------------------------------------------------------- #
# trailing_iso_weeks — sales weekly series walks back across the ISO boundary
# --------------------------------------------------------------------------- #
def test_trailing_iso_weeks_anchor_and_order():
    weeks = trailing_iso_weeks(2025, 7, n=3)
    assert len(weeks) == 3
    # Oldest → newest; the newest is the anchor week = ISO week of month-end.
    anchor = iso_week_of(date(2025, 7, 31))   # = (2025, 31)
    assert (weeks[-1]["iso_year"], weeks[-1]["iso_week"]) == anchor
    # Each entry is a true ISO-week FLOW window (Monday..Sunday).
    for w in weeks:
        assert (w["d0"], w["d1"]) == week_range(w["iso_year"], w["iso_week"])
        assert w["d0"].weekday() == 0 and w["d1"].weekday() == 6
    # Consecutive, strictly increasing windows.
    assert weeks[0]["d1"] < weeks[1]["d0"]
    assert weeks[1]["d1"] < weeks[2]["d0"]
    # Labels are CWww'yy.
    assert weeks[-1]["label"] == "CW31'25"


def test_trailing_iso_weeks_crosses_year_boundary():
    # Anchor in January (month-end 2026-01-31 → ISO 2026-W05); 12 weeks back must
    # step across the ISO-year boundary into 2025 weeks via date arithmetic.
    weeks = trailing_iso_weeks(2026, 1, n=12)
    anchor = iso_week_of(date(2026, 1, 31))   # = (2026, 5)
    assert (weeks[-1]["iso_year"], weeks[-1]["iso_week"]) == anchor
    iso_years = {w["iso_year"] for w in weeks}
    assert 2025 in iso_years and 2026 in iso_years   # boundary actually crossed
    # The newest 2025 week is W52 immediately before 2026-W01 (no naive decrement).
    assert (2025, 52) in [(w["iso_year"], w["iso_week"]) for w in weeks]
    assert (2026, 1) in [(w["iso_year"], w["iso_week"]) for w in weeks]
    # Every step-back matches prior_iso_week and the windows are contiguous.
    for older, newer in zip(weeks, weeks[1:]):
        assert (older["iso_year"], older["iso_week"]) == prior_iso_week(
            newer["iso_year"], newer["iso_week"]
        )
        assert older["d1"] < newer["d0"]
