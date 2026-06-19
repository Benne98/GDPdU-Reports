"""P5 / C5 — GOLDEN tests for the indirect-method Cash Flow (pure core).

NO DB.  A two-period synthetic fixture (closed books: net income carried into
Retained earnings each period) is aggregated cumulatively by the BS core at the
closing AND opening cutoffs, the P&L core gives net income, and
``compute_cash_flow`` derives CFO/CFI/CFF.  Asserts the worked-example numbers and
the TIE-OUT: (CFO + CFI + CFF) == Δ Cash per column.

Fixture (entity 01, month view, FY2025):
  Period 1 (opening, balanced): Cash +1000, Equity −1000.
  Period 2 (balanced, closed books):
    Sale on credit ............ AR +100 (BS) ; Revenue −100 (PL)
    Cash collection ........... Cash +40 ; AR −40
    Buy inventory on credit ... Inventory +60 ; AP −60
    Close P/L to equity ....... Retained −100 (BS credit)  (NI of the period)
  Σ BS p2 = 100 + 40 − 40 + 60 − 60 − 100 = 0  → balanced.
Window = period 2 (column '2025-02', opening cutoff (2025,1)):
  ΔCash=40, ΔAR=60, ΔInv=60, ΔAP=60, NI=100
  CFO = NI − ΔNWC = 100 − (60 + 60 − 60) = 100 − 60 = 40
  CFI = 0 (no PP&E) ; CFF = ΔEquity_line = 0
  Σ CF = 40 = ΔCash  → ties out.
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
from app.services.cash_flow import _opening_plan, compute_cash_flow
from app.services.statements import GlMovement, aggregate_pl, default_pl_structure


def _bs_movements() -> list[BsMovement]:
    return [
        # period 1 opening
        BsMovement(2025, 1, "Current assets", "Cash", "Bank", 1000.0),
        BsMovement(2025, 1, "Equity", "Equity", "Share capital", -1000.0),
        # period 2
        BsMovement(2025, 2, "Current assets", "Receivables", "Trade", 100.0),   # sale on credit
        BsMovement(2025, 2, "Current assets", "Cash", "Bank", 40.0),            # collection
        BsMovement(2025, 2, "Current assets", "Receivables", "Trade", -40.0),  # collection
        BsMovement(2025, 2, "Current assets", "Inventory", "Raw", 60.0),        # buy inv on credit
        BsMovement(2025, 2, "Current liabilities", "Payables", "Trade", -60.0),
        BsMovement(2025, 2, "Equity", "Retained earnings", "P/L", -100.0),      # NI closed to equity
    ]


def _pl_movements() -> list[GlMovement]:
    # Revenue −100 in P2 → GROSS_PROFIT = 100 = NI for period 2.
    return [GlMovement(2025, 2, "Umsatzerlöse", "Net sales", "X", -100.0)]


def _build(plan):
    bs_closing = aggregate_bs(_bs_movements(), default_bs_structure(), plan)
    bs_opening = aggregate_bs(_bs_movements(), default_bs_structure(), _opening_plan(plan))
    pl = aggregate_pl(_pl_movements(), default_pl_structure(), plan)
    return compute_cash_flow(bs_closing, bs_opening, pl, plan)


def _cell(stmt, code, key):
    line = next(l for l in stmt.lines if l.line_code == code)
    return next(c for c in line.cells if c.column_key == key).value


# Month view so each column is exactly one period (clean window = single period).
def _plan():
    return P.build_period_plan("month", 2025, 5, years=(2025,))


# =========================================================================== #
# Golden — worked example (column 2025-02)
# =========================================================================== #
class TestCfWorkedExample:
    def setup_method(self):
        self.plan = _plan()
        self.stmt = _build(self.plan)

    def test_net_income_is_100(self):
        assert _cell(self.stmt, "NI", "2025-02") == pytest.approx(100.0)

    def test_cfo_is_40(self):
        assert _cell(self.stmt, "CFO", "2025-02") == pytest.approx(40.0)

    def test_cfi_is_zero_stub(self):
        assert _cell(self.stmt, "CFI", "2025-02") == pytest.approx(0.0)

    def test_cff_is_zero(self):
        assert _cell(self.stmt, "CFF", "2025-02") == pytest.approx(0.0)

    def test_net_change_in_cash_is_40(self):
        assert _cell(self.stmt, "NET_CF", "2025-02") == pytest.approx(40.0)

    def test_delta_cash_bs_is_40(self):
        assert _cell(self.stmt, "CHG_CASH_BS", "2025-02") == pytest.approx(40.0)


# =========================================================================== #
# TIE-OUT — (CFO + CFI + CFF) == Δ Cash for EVERY column
# =========================================================================== #
def test_cashflow_ties_out_per_column():
    plan = _plan()
    stmt = _build(plan)
    for key in stmt.column_keys:
        assert stmt.tieout_residual[key] == pytest.approx(0.0), key
        # cross-check: Σ blocks equals the Cash BS Δ
        net = _cell(stmt, "NET_CF", key)
        dcash = _cell(stmt, "CHG_CASH_BS", key)
        assert net == pytest.approx(dcash), key


# =========================================================================== #
# Edge cases
# =========================================================================== #
class TestCfEdgeCases:
    def test_period_one_window_opening_zero(self):
        # Column 2025-01: opening cutoff = (2024,12) → no movements there → opening 0.
        # ΔCash = closing(2025,1) − 0 = 1000.  NI(P1)=0 (no PL in P1).
        # ΔNWC = 0 ; CFF = ΔEquity_line = 1000 (opening equity raise).  CFO=0, CFI=0.
        # Σ CF = 0 + 0 + 1000 = 1000 = ΔCash → ties out.
        plan = _plan()
        stmt = _build(plan)
        assert _cell(stmt, "CHG_CASH_BS", "2025-01") == pytest.approx(1000.0)
        assert _cell(stmt, "CFF", "2025-01") == pytest.approx(1000.0)
        assert stmt.tieout_residual["2025-01"] == pytest.approx(0.0)

    def test_da_and_ppe_and_debt_are_stub_zero(self):
        plan = _plan()
        stmt = _build(plan)
        # No D&A / PP&E / debt accounts in the fixture → those lines are 0.
        assert _cell(stmt, "DA", "2025-02") == pytest.approx(0.0)

    def test_empty_window_zero(self):
        # year view, last_closed_period 0 → YTD empty window → all flows 0, ties out.
        plan = P.build_period_plan("year", 2025, 0)
        bs_c = aggregate_bs(_bs_movements(), default_bs_structure(), plan)
        bs_o = aggregate_bs(_bs_movements(), default_bs_structure(), _opening_plan(plan))
        pl = aggregate_pl(_pl_movements(), default_pl_structure(), plan)
        stmt = compute_cash_flow(bs_c, bs_o, pl, plan)
        assert _cell(stmt, "NET_CF", "YTD") == pytest.approx(0.0)
        assert stmt.tieout_residual["YTD"] == pytest.approx(0.0)


# =========================================================================== #
# Determinism
# =========================================================================== #
def test_cf_deterministic():
    plan = _plan()
    a = _build(plan)
    b = _build(plan)
    assert [(l.line_code, [c.value for c in l.cells]) for l in a.lines] == \
           [(l.line_code, [c.value for c in l.cells]) for l in b.lines]
