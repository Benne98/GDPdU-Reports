"""P5 / C3 — GOLDEN tests for the pure Balance-Sheet aggregation (cumulative).

NO DB.  A tiny synthetic BS-movement fixture is built in-test and aggregated by
the pure core ``aggregate_bs``.  Asserts:
  • the CUMULATIVE (stock) behaviour: a column's value is the closing balance as
    of the column's latest period (opening + all prior + in-period movements),
  • the BS sign convention (assets +amount, liab/equity −amount → all positive),
  • the accounting identity Assets = Liabilities + Equity per column (imbalance 0),
  • documented edge cases (empty column, negative balance, unmapped reconciliation).

Worked example (see balance_sheet.py docstring), YTD column @ (2025,5):
  Total assets=2100, Total liabilities=500, Total equity=1600, identity ties out.
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
from app.services.balance_sheet import (
    BsMovement,
    BsStructureLine,
    _present_bs,
    aggregate_bs,
    default_bs_structure,
)


# --------------------------------------------------------------------------- #
# Synthetic BS fixture (STORED sign: + debit/asset, - credit/liab+equity).
# Opening balances in P1, then a few in-period movements, all in FY2025 so the
# cumulative behaviour is visible across YTD/FY columns.  Σ stored == 0 (balanced).
# --------------------------------------------------------------------------- #
def _movements() -> list[BsMovement]:
    m: list[BsMovement] = []
    # Cash (asset): opening +1000 in P1, +100 each P2..P5 → cum@P5 = 1400
    m.append(BsMovement(2025, 1, "Current assets", "Cash", "Bank", 1000.0))
    for p in range(2, 6):
        m.append(BsMovement(2025, p, "Current assets", "Cash", "Bank", 100.0))
    # Receivables (asset): opening +300 P1, +200 P3 → cum@P5 = 500
    m.append(BsMovement(2025, 1, "Current assets", "Receivables", "Trade", 300.0))
    m.append(BsMovement(2025, 3, "Current assets", "Receivables", "Trade", 200.0))
    # Inventory (asset): opening +200 P1 → cum@P5 = 200
    m.append(BsMovement(2025, 1, "Current assets", "Inventory", "Raw", 200.0))
    # Payables (credit): opening -400 P1, -100 P4 → cum@P5 = -500 → presented +500
    m.append(BsMovement(2025, 1, "Current liabilities", "Payables", "Trade", -400.0))
    m.append(BsMovement(2025, 4, "Current liabilities", "Payables", "Trade", -100.0))
    # Equity (credit): opening -1100 P1 → cum -1100 → presented +1100
    m.append(BsMovement(2025, 1, "Equity", "Equity", "Share capital", -1100.0))
    # Retained earnings / net income (credit): -500 P1 → presented +500
    m.append(BsMovement(2025, 1, "Equity", "Retained earnings", "P/L", -500.0))
    return m


def _cell(stmt, line_code, column_key):
    line = next(l for l in stmt.lines if l.line_code == line_code)
    return next(c for c in line.cells if c.column_key == column_key).value


# =========================================================================== #
# Sign convention
# =========================================================================== #
def test_present_bs_asset_keeps_sign_credit_flips():
    assert _present_bs(1000.0, "asset") == pytest.approx(1000.0)
    assert _present_bs(-500.0, "credit") == pytest.approx(500.0)
    # unconfigured side never silently flips
    assert _present_bs(-7.0, "unknown") == pytest.approx(-7.0)


# =========================================================================== #
# Golden — cumulative worked example (YTD @ 2025,5)
# =========================================================================== #
class TestBsWorkedExample:
    def setup_method(self):
        self.plan = P.build_period_plan("year", 2025, 5)
        self.stmt = aggregate_bs(_movements(), default_bs_structure(), self.plan)

    def test_cash_cumulative_is_1400(self):
        # opening 1000 + 4×100 = 1400 (cumulative, not just in-period)
        assert _cell(self.stmt, "CASH", "YTD") == pytest.approx(1400.0)

    def test_ar_cumulative_is_500(self):
        assert _cell(self.stmt, "AR", "YTD") == pytest.approx(500.0)

    def test_inventory_is_200(self):
        assert _cell(self.stmt, "INVENTORY", "YTD") == pytest.approx(200.0)

    def test_total_assets_is_2100(self):
        assert _cell(self.stmt, "TOTAL_ASSETS", "YTD") == pytest.approx(2100.0)

    def test_payables_presented_positive_500(self):
        # stored -500 (credit) → presented +500
        assert _cell(self.stmt, "AP", "YTD") == pytest.approx(500.0)

    def test_total_liabilities_is_500(self):
        assert _cell(self.stmt, "TOTAL_LIABILITIES", "YTD") == pytest.approx(500.0)

    def test_total_equity_is_1600(self):
        # Equity 1100 + Retained 500
        assert _cell(self.stmt, "TOTAL_EQUITY", "YTD") == pytest.approx(1600.0)

    def test_accounting_identity_assets_equals_liab_plus_equity(self):
        # 2100 == 500 + 1600  → imbalance 0
        assets = _cell(self.stmt, "TOTAL_ASSETS", "YTD")
        liab = _cell(self.stmt, "TOTAL_LIABILITIES", "YTD")
        eq = _cell(self.stmt, "TOTAL_EQUITY", "YTD")
        assert assets == pytest.approx(liab + eq)
        assert self.stmt.imbalance["YTD"] == pytest.approx(0.0)


# =========================================================================== #
# Cumulative vs flow — the FY column (cutoff 2025,12) equals YTD here (no P6..12),
# and YTD with an EARLIER cutoff captures fewer movements.
# =========================================================================== #
def test_fy_column_same_as_ytd_when_no_later_periods():
    plan = P.build_period_plan("year", 2025, 5)
    stmt = aggregate_bs(_movements(), default_bs_structure(), plan)
    assert _cell(stmt, "CASH", "FY") == pytest.approx(1400.0)
    assert _cell(stmt, "TOTAL_ASSETS", "FY") == pytest.approx(2100.0)


def test_earlier_cutoff_excludes_later_movements():
    # last_closed_period=2 → YTD cutoff (2025,2): cash = 1000 + 100 = 1100;
    # AR P3 (+200) and AR opening: only opening 300 (P3 is after cutoff) → AR 300;
    # Payables P4 (-100) excluded → AP only opening 400.
    plan = P.build_period_plan("year", 2025, 2)
    stmt = aggregate_bs(_movements(), default_bs_structure(), plan)
    assert _cell(stmt, "CASH", "YTD") == pytest.approx(1100.0)
    assert _cell(stmt, "AR", "YTD") == pytest.approx(300.0)
    assert _cell(stmt, "AP", "YTD") == pytest.approx(400.0)
    # identity still ties out at this cutoff (fixture balanced at every cutoff? NO —
    # only the fully-booked set balances; here the cut is mid-stream).  We assert the
    # ACTUAL imbalance value so the relationship is locked, not assumed zero.
    assets = _cell(stmt, "TOTAL_ASSETS", "YTD")
    liab = _cell(stmt, "TOTAL_LIABILITIES", "YTD")
    eq = _cell(stmt, "TOTAL_EQUITY", "YTD")
    # assets = 1100 + 300 + 200 = 1600 ; liab = 400 ; equity = 1100 + 500 = 1600
    # imbalance = 1600 - (400 + 1600) = -400  (the not-yet-booked P3..P5 net)
    assert assets == pytest.approx(1600.0)
    assert liab == pytest.approx(400.0)
    assert eq == pytest.approx(1600.0)
    assert stmt.imbalance["YTD"] == pytest.approx(-400.0)


# =========================================================================== #
# Identity ties out per column for every FULLY-captured column
# =========================================================================== #
def test_identity_ties_out_for_full_columns():
    plan = P.build_period_plan("year", 2025, 5)
    stmt = aggregate_bs(_movements(), default_bs_structure(), plan)
    # FY, YTD, LTM all have cutoff >= (2025,5) → capture the full balanced set.
    for col_key in ("FY", "YTD", "LTM"):
        assert stmt.imbalance[col_key] == pytest.approx(0.0), col_key


# =========================================================================== #
# Edge cases
# =========================================================================== #
class TestBsEdgeCases:
    def test_empty_column_is_zero(self):
        # last_closed_period=0 → YTD has no buckets → stock 0, identity 0=0.
        plan = P.build_period_plan("year", 2025, 0)
        stmt = aggregate_bs(_movements(), default_bs_structure(), plan)
        assert _cell(stmt, "CASH", "YTD") == pytest.approx(0.0)
        assert _cell(stmt, "TOTAL_ASSETS", "YTD") == pytest.approx(0.0)
        assert stmt.imbalance["YTD"] == pytest.approx(0.0)

    def test_negative_asset_balance_carries_sign(self):
        # Overdrawn cash: net credit on an asset account → presented negative.
        movements = [BsMovement(2025, 1, "Current assets", "Cash", "Bank", -50.0)]
        plan = P.build_period_plan("year", 2025, 5)
        stmt = aggregate_bs(movements, default_bs_structure(), plan)
        assert _cell(stmt, "CASH", "YTD") == pytest.approx(-50.0)

    def test_unmapped_account_surfaces(self):
        movements = _movements() + [
            BsMovement(2025, 1, "Other", "Goodwill", "Intangible", 999.0),  # no structure line
        ]
        plan = P.build_period_plan("year", 2025, 5)
        stmt = aggregate_bs(movements, default_bs_structure(), plan)
        # raw stored sign surfaced in unmapped (no section to flip by)
        assert stmt.unmapped_total["YTD"] == pytest.approx(999.0)


# =========================================================================== #
# Determinism + structure shape
# =========================================================================== #
def test_aggregate_bs_deterministic():
    plan = P.build_period_plan("year", 2025, 5)
    a = aggregate_bs(_movements(), default_bs_structure(), plan)
    b = aggregate_bs(_movements(), default_bs_structure(), plan)
    assert [(l.line_code, [c.value for c in l.cells]) for l in a.lines] == \
           [(l.line_code, [c.value for c in l.cells]) for l in b.lines]


def test_default_bs_structure_sections():
    codes = [(s.line_code, s.section, s.row_type) for s in default_bs_structure()]
    assert ("TOTAL_ASSETS", "asset", "subtotal") in codes
    assert ("TOTAL_LIABILITIES", "liability", "subtotal") in codes
    assert ("TOTAL_EQUITY", "equity", "subtotal") in codes
