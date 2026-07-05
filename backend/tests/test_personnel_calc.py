"""Regression tests for personnel FTE / avg-cost-per-FTE metrics.

Bug lock — "Avg cost per FTE shows 0":
  Root cause was a DATA GAP (fact_personnel_employee empty / months_active
  missing) -> fte_sum == 0 -> the guard ``if fte_sum <= 0: return 0.0`` returned
  0. The formula and units were correct; once real snapshots are loaded the
  metric renders a sensible non-zero value (~ -100 kEUR/FTE on finssentials_v2).

FTE semantic (verified against the source personaltable.xlsx):
  months_active = source column "Summe" = COUNT of active months (sum of the 12
  monthly 1/0 presence flags, range 1-12) — NOT a EUR sum, NOT weighted by
  Beschaeftigungsgrad. Beschaeftigungsgrad is a separate percent column.

  FTE            = (months_active / 12) * (besch_pct / 100)
  payroll (kEUR) = -sum(payroll_eur) / 1000      (cost / credit convention)
  avg cost/FTE   = -sum(payroll_eur) / 1000 / fte_sum

Worked example (two employees, one snapshot):
  A: months=12, besch=100, gesamtsumme=90_000 EUR  -> fte 1.00, payroll 90_000
  B: months= 6, besch= 50, gesamtsumme=20_000 EUR  -> fte 0.25, payroll 20_000
  fte_sum = 1.25 ; payroll_eur = 110_000
  avg cost/FTE = -110_000 / 1000 / 1.25 = -88.0 kEUR/FTE

All tests are pure (synthetic dict rows) — no DB, sub-millisecond.
"""
from __future__ import annotations

from app.services.personnel_accounting import _DEFAULT_METRICS
from app.services.personnel_calc import aggregate_metric, row_fte


def _emp(months, besch, gesamtsumme):
    return {"months_active": months, "beschaeftigungsgrad": besch, "gesamtsumme": gesamtsumme}


_WORKED = [
    _emp(12, 100, 90_000.0),
    _emp(6, 50, 20_000.0),
]


# ── row_fte semantic ──────────────────────────────────────────────────────────
def test_row_fte_full_year_full_grade():
    assert row_fte(12, 100) == 1.0


def test_row_fte_half_year_half_grade():
    assert row_fte(6, 50) == 0.25


def test_row_fte_single_month():
    assert row_fte(1, 100) == 1.0 / 12.0


def test_row_fte_besch_null_defaults_to_100():
    # Missing Beschaeftigungsgrad is treated as full-time (100%).
    assert row_fte(12, None) == 1.0
    assert row_fte(12, 0) == 1.0  # falsy -> defaults to 100 by design


def test_row_fte_months_null_is_zero():
    assert row_fte(None, 100) == 0.0


# ── aggregate_metric: worked example ─────────────────────────────────────────
def test_fte_sum_worked_example():
    # round(1.25) -> 1 (integer FTE count for the headline row).
    assert aggregate_metric(_WORKED, "fte") == 1


def test_payroll_worked_example():
    assert aggregate_metric(_WORKED, "payroll") == -110.0


def test_avg_cost_per_fte_worked_example():
    # -110 kEUR / 1.25 FTE = -88.0 kEUR/FTE  (non-zero, cost sign preserved).
    assert aggregate_metric(_WORKED, "avg_cost_per_fte") == -88.0


def test_avg_cost_per_fte_is_nonzero_with_data():
    # The core bug lock: with real-ish data present it is never 0.
    assert aggregate_metric(_WORKED, "avg_cost_per_fte") != 0.0


# ── edge cases ───────────────────────────────────────────────────────────────
def test_avg_cost_per_fte_empty_rows_returns_zero_not_crash():
    assert aggregate_metric([], "avg_cost_per_fte") == 0.0


def test_avg_cost_per_fte_zero_fte_returns_zero():
    # months_active == 0 for every employee -> fte_sum == 0 -> guarded 0.0.
    rows = [_emp(0, 100, 50_000.0)]
    assert aggregate_metric(rows, "avg_cost_per_fte") == 0.0


def test_avg_cost_per_fte_single_month_employee():
    # 1 month @ 100% -> fte 1/12; payroll 12_000 EUR -> -12 kEUR / (1/12) = -144.0
    rows = [_emp(1, 100, 12_000.0)]
    assert aggregate_metric(rows, "avg_cost_per_fte") == -144.0


# ── section ordering (task 7): Average FTE -> Avg cost/FTE -> Payroll ─────────
def test_default_metric_order_fte_avgcost_payroll_first():
    assert _DEFAULT_METRICS[:3] == ["fte", "avg_cost_per_fte", "payroll"]
    # avg_cost_per_fte must sit strictly between fte and payroll.
    assert _DEFAULT_METRICS.index("fte") < _DEFAULT_METRICS.index("avg_cost_per_fte")
    assert _DEFAULT_METRICS.index("avg_cost_per_fte") < _DEFAULT_METRICS.index("payroll")
    # Footer KPIs still trail the metric blocks.
    assert _DEFAULT_METRICS[-2:] == ["personnel_expenses", "personnel_pct_output"]


def test_build_accounting_table_reorders_requested_metrics():
    """Client metric order must not change rendered section order."""
    from datetime import date
    from unittest.mock import MagicMock

    import app.services.personnel_accounting as pa
    from app.services.personnel_accounting import build_accounting_table

    rows = [
        {
            "months_active": 12,
            "beschaeftigungsgrad": 100,
            "gesamtsumme": 90_000,
            "bereich": "Buntmetallguss",
        },
    ]

    def fake_execute(_sql, params):
        r = MagicMock()
        r.fetchall.return_value = [MagicMock(_mapping=row) for row in rows]
        return r

    session = MagicMock()
    session.execute.side_effect = fake_execute
    pa.resolve_entity_prefix = lambda _s, _e: None
    pa.build_pl_annual_compat = lambda *a, **k: {"rows": []}

    result = build_accounting_table(
        session,
        anchor_date=date(2022, 12, 31),
        compare_dates=[],
        metrics=["payroll", "avg_cost_per_fte", "fte"],
    )
    headers = [r["label"] for r in result["rows"] if r["row_kind"] == "section_header"]
    assert headers == ["Average FTEs #", "Average cost per FTE", "Payroll accounting"]
