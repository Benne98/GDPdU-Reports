"""P5 / C4 — GOLDEN tests for Working Capital + DSO/DIO/DPO/CCC (pure core).

NO DB.  Builds a BsStatement (cumulative balances) and a PlStatement (flows) from
tiny synthetic fixtures, then runs ``compute_working_capital``.  Asserts the exact
worked-example numbers from working_capital.py, the days/annualization rule, and
the documented edge cases (zero denominator → None, partial-year scaling).

Worked example (FY column, D=365): AR=500, INV=200, AP=500, REV=3000, COGS=500
  NWC = 500 + 200 − 500 = 200
  DSO = 500/3000 × 365 = 60.8333…
  DIO = 200/500  × 365 = 146.0
  DPO = 500/500  × 365 = 365.0
  CCC = 60.8333… + 146 − 365 = −158.1667…
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKEND = _REPO_ROOT / "backend"
for p in (str(_REPO_ROOT), str(_BACKEND)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.services import periods as P
from app.services.balance_sheet import BsMovement, aggregate_bs, default_bs_structure
from app.services.statements import GlMovement, aggregate_pl, default_pl_structure
from app.services.working_capital import (
    DAYS_PER_PERIOD,
    DAYS_PER_YEAR,
    _days_in_period,
    compute_working_capital,
)


# --------------------------------------------------------------------------- #
# Fixtures: BS balances (cumulative) + P&L flows, same as the worked example.
# --------------------------------------------------------------------------- #
def _bs_movements() -> list[BsMovement]:
    return [
        BsMovement(2025, 1, "Current assets", "Receivables", "Trade", 500.0),   # AR 500
        BsMovement(2025, 1, "Current assets", "Inventory", "Raw", 200.0),       # INV 200
        BsMovement(2025, 1, "Current liabilities", "Payables", "Trade", -500.0),  # AP 500
        BsMovement(2025, 1, "Current assets", "Cash", "Bank", 1300.0),
        BsMovement(2025, 1, "Equity", "Equity", "Share capital", -1500.0),
    ]


def _pl_movements() -> list[GlMovement]:
    rows: list[GlMovement] = []
    # Revenue -600/period P1..P5 → 3000 presented ; Material +100/period → 500 magnitude
    for p in range(1, 6):
        rows.append(GlMovement(2025, p, "Umsatzerlöse", "Net sales", "X", -600.0))
        rows.append(GlMovement(2025, p, "Materialaufwand", "Cost of materials", "Y", 100.0))
    return rows


def _build(plan):
    bs = aggregate_bs(_bs_movements(), default_bs_structure(), plan)
    pl = aggregate_pl(_pl_movements(), default_pl_structure(), plan)
    return compute_working_capital(bs, pl, plan)


def _cell(stmt, code, key):
    line = next(l for l in stmt.lines if l.line_code == code)
    return next(c for c in line.cells if c.column_key == key).value


# =========================================================================== #
# Days / annualization rule
# =========================================================================== #
def test_days_full_year_is_365():
    plan = P.build_period_plan("year", 2025, 5)
    fy_col = next(c for c in plan.columns if c.key == "FY")
    assert _days_in_period(fy_col) == pytest.approx(DAYS_PER_YEAR)


def test_days_ytd_is_proportional():
    plan = P.build_period_plan("year", 2025, 5)
    ytd_col = next(c for c in plan.columns if c.key == "YTD")  # 5 buckets
    assert _days_in_period(ytd_col) == pytest.approx(5 * DAYS_PER_PERIOD)


# =========================================================================== #
# Golden — worked example (FY column, full year, D=365)
# =========================================================================== #
class TestWcWorkedExample:
    def setup_method(self):
        self.plan = P.build_period_plan("year", 2025, 5)
        self.stmt = _build(self.plan)

    def test_nwc_is_200(self):
        assert _cell(self.stmt, "NWC", "FY") == pytest.approx(200.0)

    def test_dso(self):
        assert _cell(self.stmt, "DSO", "FY") == pytest.approx(500 / 3000 * 365)

    def test_dio(self):
        assert _cell(self.stmt, "DIO", "FY") == pytest.approx(200 / 500 * 365)

    def test_dpo(self):
        assert _cell(self.stmt, "DPO", "FY") == pytest.approx(500 / 500 * 365)

    def test_ccc(self):
        expected = (500 / 3000 * 365) + (200 / 500 * 365) - (500 / 500 * 365)
        assert _cell(self.stmt, "CCC", "FY") == pytest.approx(expected)
        assert expected == pytest.approx(-158.16666666, rel=1e-6)


# =========================================================================== #
# YTD column — partial-year annualization uses D = 5 × 365/12
# =========================================================================== #
def test_ytd_dso_uses_proportional_days():
    plan = P.build_period_plan("year", 2025, 5)
    stmt = _build(plan)
    d = 5 * DAYS_PER_PERIOD
    # YTD revenue flow = 3000 (P1..5), AR cumulative @ (2025,5) = 500
    assert _cell(stmt, "DSO", "YTD") == pytest.approx(500 / 3000 * d)


# =========================================================================== #
# Edge cases
# =========================================================================== #
class TestWcEdgeCases:
    def test_zero_revenue_dso_none(self):
        plan = P.build_period_plan("year", 2025, 5)
        bs = aggregate_bs(_bs_movements(), default_bs_structure(), plan)
        pl = aggregate_pl([], default_pl_structure(), plan)  # no revenue
        stmt = compute_working_capital(bs, pl, plan)
        assert _cell(stmt, "DSO", "FY") is None
        # COGS also 0 → DIO/DPO None → CCC None
        assert _cell(stmt, "DIO", "FY") is None
        assert _cell(stmt, "DPO", "FY") is None
        assert _cell(stmt, "CCC", "FY") is None

    def test_zero_cogs_dio_dpo_none_ccc_none(self):
        plan = P.build_period_plan("year", 2025, 5)
        bs = aggregate_bs(_bs_movements(), default_bs_structure(), plan)
        # revenue only, no material → COGS 0
        pl_mov = [GlMovement(2025, 1, "Umsatzerlöse", "Net sales", "X", -600.0)]
        pl = aggregate_pl(pl_mov, default_pl_structure(), plan)
        stmt = compute_working_capital(bs, pl, plan)
        assert _cell(stmt, "DSO", "FY") is not None  # revenue present
        assert _cell(stmt, "DIO", "FY") is None
        assert _cell(stmt, "DPO", "FY") is None
        assert _cell(stmt, "CCC", "FY") is None

    def test_empty_window_days_zero_ratios_none(self):
        plan = P.build_period_plan("year", 2025, 0)  # YTD empty
        stmt = _build(plan)
        assert _cell(stmt, "DSO", "YTD") is None
        assert _cell(stmt, "NWC", "YTD") == pytest.approx(0.0)

    def test_negative_balance_ratio_signed(self):
        # Negative AR (credit balance on receivables) → negative DSO, informational.
        plan = P.build_period_plan("year", 2025, 5)
        bs_mov = [
            BsMovement(2025, 1, "Current assets", "Receivables", "Trade", -100.0),
            BsMovement(2025, 1, "Current liabilities", "Payables", "Trade", -50.0),
            BsMovement(2025, 1, "Equity", "Equity", "X", 150.0),
        ]
        bs = aggregate_bs(bs_mov, default_bs_structure(), plan)
        pl = aggregate_pl(_pl_movements(), default_pl_structure(), plan)
        stmt = compute_working_capital(bs, pl, plan)
        assert _cell(stmt, "DSO", "FY") == pytest.approx(-100 / 3000 * 365)


# =========================================================================== #
# Determinism
# =========================================================================== #
def test_wc_deterministic():
    plan = P.build_period_plan("year", 2025, 5)
    a = _build(plan)
    b = _build(plan)
    assert [(l.line_code, [c.value for c in l.cells]) for l in a.lines] == \
           [(l.line_code, [c.value for c in l.cells]) for l in b.lines]
