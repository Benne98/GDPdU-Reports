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
    _encode_bs_ob_rows,
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


# =========================================================================== #
# Opening-balance encoding (fiscal_period=0 real GoBD OBs) — the fix for the
# OB-only-account-vanishes / movement-account-loses-opening-stock bug.
#
# Real opening balances are stored at fiscal_period=0 (Jan-1, entry_type=
# 'opening_balance') as the FULL cumulative closing balance PER YEAR (NOT a
# per-year delta).  ``_encode_bs_ob_rows`` anchors/delta-encodes them so the
# cumulative core reconstructs the stock without double counting, matching the
# GDPdU compat reader (fin_compat_bs_sql._bal_amount_expr).
#
# Formula (BS value at cutoff (fy*, p*)):
#   movement account : earliest-year OB anchor (embeds all pre-window closings)
#                      + Σ raw non-OB movements with (fy,p) <= (fy*, p*)
#   OB-only account  : latest OB <= cutoff  (via delta-encoded OB series)
#
# The synthetic figures mirror the LIVE finssentials_v5 archetypes:
#   • 30515/03 "Retained earnings" (movement + OB): OB 2023=1000, 2024=3560,
#     2025=6120 with +2560 movements in 2023 & 2024 → closing 6120 @ FY2025.
#   • 38818/03 "Profit distribution" (OB-only, varying): OB 0 → -3400 → -5700.
# =========================================================================== #

# entry_type constants for readability in the raw-row fixtures.
_OB = "opening_balance"
_MV = "actual"


def _ob_rows():
    """Raw actuals rows as fetch_bs_movements' SQL returns them:
    (account_number_group, fiscal_year, fiscal_period, entry_type, l2, l3, l4, amount).
    Window = FYs 2023..2025 (a 2025 'year' view fetches FY-2..FY)."""
    rows = []
    # A: movement + per-year cumulative OB (like 30515 Retained earnings, credit).
    rows += [
        ("A", 2023, 0, _OB, "Equity", "Retained earnings", None, 1000.0),
        ("A", 2024, 0, _OB, "Equity", "Retained earnings", None, 3560.0),
        ("A", 2025, 0, _OB, "Equity", "Retained earnings", None, 6120.0),
        ("A", 2023, 12, _MV, "Equity", "Retained earnings", None, 2560.0),
        ("A", 2024, 12, _MV, "Equity", "Retained earnings", None, 2560.0),
    ]
    # B: OB-only, VARYING across years (like 38818 Profit distribution, credit).
    rows += [
        ("B", 2023, 0, _OB, "Equity", "Profit distribution", None, 0.0),
        ("B", 2024, 0, _OB, "Equity", "Profit distribution", None, -3400.0),
        ("B", 2025, 0, _OB, "Equity", "Profit distribution", None, -5700.0),
    ]
    # C: first-year account (2025), OB + a mid-year (period 6) movement (asset).
    rows += [
        ("C", 2025, 0, _OB, "Current assets", "Cash", "Bank", 500.0),
        ("C", 2025, 6, _MV, "Current assets", "Cash", "Bank", 100.0),
    ]
    # D: pure movement, NO opening balance (Decidra GL-only / golden fixture shape).
    rows += [
        ("D", 2024, 3, _MV, "Current assets", "Receivables", "Trade", 300.0),
        ("D", 2025, 4, _MV, "Current assets", "Receivables", "Trade", 200.0),
    ]
    return rows


def _bs_test_structure():
    return [
        BsStructureLine(10, "CASH", "Cash", "mapping", "asset", level_3="Cash"),
        BsStructureLine(20, "AR", "Receivables", "mapping", "asset", level_3="Receivables"),
        BsStructureLine(30, "RETAINED", "Retained earnings", "mapping", "equity", level_3="Retained earnings"),
        BsStructureLine(40, "PROFIT_DIST", "Profit distribution", "mapping", "equity", level_3="Profit distribution"),
    ]


class TestBsOpeningBalanceEncoding:
    # ---- pure encoder ---------------------------------------------------- #
    def test_movement_account_keeps_only_earliest_ob_anchor(self):
        # A has movements → only the earliest-year OB (2023=1000) survives as an
        # anchor; the redundant 2024/2025 OBs are dropped (no double count).
        enc = _encode_bs_ob_rows(_ob_rows())
        a_obs = [m for m in enc if m.level_3 == "Retained earnings" and m.fiscal_period == 0]
        assert len(a_obs) == 1
        assert a_obs[0].fiscal_year == 2023
        assert a_obs[0].amount == pytest.approx(1000.0)
        # all raw movements preserved
        a_mvs = sorted(
            (m.fiscal_year, m.amount)
            for m in enc if m.level_3 == "Retained earnings" and m.fiscal_period != 0
        )
        assert a_mvs == [(2023, 2560.0), (2024, 2560.0)]

    def test_ob_only_account_is_delta_encoded(self):
        # B is OB-only with a varying series 0 → -3400 → -5700; delta-encode gives
        # 0, -3400, -2300 so the cumulative Σ telescopes back to the stock.
        enc = _encode_bs_ob_rows(_ob_rows())
        b = sorted(
            (m.fiscal_year, m.amount)
            for m in enc if m.level_3 == "Profit distribution"
        )
        assert b == [(2023, pytest.approx(0.0)),
                     (2024, pytest.approx(-3400.0)),
                     (2025, pytest.approx(-2300.0))]

    def test_pure_movement_account_unchanged(self):
        # D has no OB → its raw movements pass through untouched (golden safety:
        # pre-fix behaviour for OB-less data is byte-identical).
        enc = _encode_bs_ob_rows(_ob_rows())
        d = sorted(
            (m.fiscal_year, m.fiscal_period, m.amount)
            for m in enc if m.level_3 == "Receivables"
        )
        assert d == [(2024, 3, 300.0), (2025, 4, 200.0)]

    def test_no_opening_rows_is_identity(self):
        # With zero OB rows the encoder returns exactly the raw movements.
        raw = [
            ("D", 2024, 3, _MV, "Current assets", "Receivables", "Trade", 300.0),
            ("D", 2025, 4, _MV, "Current assets", "Receivables", "Trade", 200.0),
        ]
        enc = _encode_bs_ob_rows(raw)
        assert sorted((m.fiscal_year, m.fiscal_period, m.amount) for m in enc) == [
            (2024, 3, 300.0), (2025, 4, 200.0)
        ]

    # ---- end-to-end through the cumulative core -------------------------- #
    def test_fy2025_matches_closing_balance_no_double_count(self):
        enc = _encode_bs_ob_rows(_ob_rows())
        plan = P.build_period_plan("year", 2025, 12)
        stmt = aggregate_bs(enc, _bs_test_structure(), plan)
        # Movement account: closing 6120 (opening 1000 + 2×2560), presented credit
        # → -6120.  NOT 6120+5120 (no double count of the opening stock).
        assert _cell(stmt, "RETAINED", "FY") == pytest.approx(-6120.0)
        # OB-only account: latest OB -5700 presented credit → +5700 (NOT 0/vanished).
        assert _cell(stmt, "PROFIT_DIST", "FY") == pytest.approx(5700.0)
        # First-year asset: opening 500 + mid-year 100 = 600 (asset keeps sign).
        assert _cell(stmt, "CASH", "FY") == pytest.approx(600.0)

    def test_ob_only_account_would_vanish_without_fix(self):
        # Regression anchor: pre-fix the reader dropped fiscal_period=0 rows, so the
        # OB-only account produced NO movements and its cumulative value was 0.0.
        # With the fix it is its real stock.  (Feeding the encoded rows proves the
        # account is present and non-zero — the exact symptom the bug caused.)
        enc = _encode_bs_ob_rows(_ob_rows())
        plan = P.build_period_plan("year", 2025, 12)
        stmt = aggregate_bs(enc, _bs_test_structure(), plan)
        assert _cell(stmt, "PROFIT_DIST", "FY") != pytest.approx(0.0)

    def test_prior_year_and_partial_period_cutoffs(self):
        enc = _encode_bs_ob_rows(_ob_rows())
        # FY-2 column cutoff (2023,12): A = anchor 1000 + M2023 2560 = 3560 → -3560.
        # FY-1 column cutoff (2024,12): A = 1000 + 2560 + 2560 = 6120 → -6120.
        stmt = aggregate_bs(enc, _bs_test_structure(), P.build_period_plan("year", 2025, 12))
        assert _cell(stmt, "RETAINED", "FY-2") == pytest.approx(-3560.0)
        assert _cell(stmt, "RETAINED", "FY-1") == pytest.approx(-6120.0)
        # B OB-only: cutoff (2023,12) → 0; (2024,12) → -(-3400)=+3400.
        assert _cell(stmt, "PROFIT_DIST", "FY-2") == pytest.approx(0.0)
        assert _cell(stmt, "PROFIT_DIST", "FY-1") == pytest.approx(3400.0)
        # C first-year: a YTD cutoff at period 5 excludes the period-6 movement →
        # only the opening 500 (period-0 anchor <= cutoff), NOT 600.
        ytd5 = aggregate_bs(enc, _bs_test_structure(), P.build_period_plan("year", 2025, 5))
        assert _cell(ytd5, "CASH", "YTD") == pytest.approx(500.0)
