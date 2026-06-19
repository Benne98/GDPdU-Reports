"""P5 / C3 — GOLDEN tests for the GL-DERIVED Balance-Sheet structure.

NO DB.  Two layers are tested:
  1. ``scripts.seed_bs_structure.build_bs_rows`` — PURE generation of the BS report
     structure from an ordered GL (level_1, level_2, level_3) hierarchy: one mapping
     row per L3 (wired by level_3), one subtotal per L2, one grandtotal per L1 side,
     with the fixed WC/CF aliases (AR/INVENTORY/AP/CASH/EQUITY).
  2. ``app.services.balance_sheet.aggregate_bs`` run over that generated structure +
     a synthetic GL-only movement set, asserting:
       • L3 mapping reconciles to the GL by level_3 (cumulative cutoff),
       • each L2 subtotal == Σ its L3 mapping rows,
       • Total assets == Σ asset L2 subtotals == Σ asset L3 lines,
       • Total equity & liabilities == Σ E&L L3 lines,
       • imbalance == Total assets − Total E&L, MATERIALLY NON-ZERO on GL-only data
         (no opening balances / no P&L roll-forward) — surfaced honestly, not hidden,
       • cumulative cutoff: an earlier cutoff excludes later movements.

Worked example (full FY cutoff): Fixed assets 300 + Current assets 1000 → Total
assets 1300 ; Equity 200 + Liabilities 50 → Total E&L 250 ; imbalance = 1300 − 250
= +1050 (the GL-only residual: no opening balances, no P&L roll-forward to equity).
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
from app.services.balance_sheet import BsMovement, BsStructureLine, aggregate_bs
from scripts.seed_bs_structure import build_bs_rows


# --------------------------------------------------------------------------- #
# A small but realistic slice of the real BS hierarchy (ordered by L2/L3 sort).
# --------------------------------------------------------------------------- #
def _hierarchy() -> list[tuple[str, str, str, float, float]]:
    return [
        ("Assets", "Fixed assets", "Tangible assets", 1, 1),
        ("Assets", "Fixed assets", "Intangible assets", 1, 2),
        ("Assets", "Current assets", "Inventories", 2, 1),
        ("Assets", "Current assets", "Trade receivables", 2, 2),
        ("Assets", "Current assets", "Cash & cash equivalents", 2, 3),
        ("Equity & liabilities", "Equity", "Retained earnings", 5, 1),
        ("Equity & liabilities", "Liabilities", "Trade payables", 7, 1),
    ]


def _structure() -> list[BsStructureLine]:
    """Convert generated _Row rows into BsStructureLine (as fetch_bs_structure does)."""
    out: list[BsStructureLine] = []
    for r in build_bs_rows(_hierarchy()):
        out.append(
            BsStructureLine(
                sort_order=r.sort_order,
                line_code=r.line_code,
                balance_title=r.balance_title,
                row_type=r.row_type,
                section=r.section,
                level_2=r.level_2,
                level_3=r.level_3,
                is_bold=r.is_bold,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Synthetic GL-only movements (stored sign: +debit/asset, -credit/E&L).  NO
# opening balances and NO P&L roll-forward → assets and E&L do NOT net to zero
# (exactly the real GL-only situation).
#   Tangible      +200  ; Intangible   +100   → Fixed assets   300
#   Inventories   +300  ; Receivables  +400 (incl. +100 in P3) ; Cash +300
#                                              → Current assets 1000... see cutoffs
#   Retained (E&L) -200 ; Payables (E&L) -50  → Total E&L 250
# --------------------------------------------------------------------------- #
def _movements() -> list[BsMovement]:
    return [
        BsMovement(2024, 1, "Fixed assets", "Tangible assets", "x", 200.0),
        BsMovement(2024, 1, "Fixed assets", "Intangible assets", "x", 100.0),
        BsMovement(2024, 1, "Current assets", "Inventories", "x", 300.0),
        BsMovement(2024, 1, "Current assets", "Trade receivables", "x", 300.0),
        BsMovement(2024, 3, "Current assets", "Trade receivables", "x", 100.0),  # later P3
        BsMovement(2024, 1, "Current assets", "Cash & cash equivalents", "x", 300.0),
        BsMovement(2024, 1, "Equity", "Retained earnings", "x", -200.0),
        BsMovement(2024, 1, "Liabilities", "Trade payables", "x", -50.0),
    ]


def _cell(stmt, code, key):
    line = next(l for l in stmt.lines if l.line_code == code)
    return next(c for c in line.cells if c.column_key == key).value


# =========================================================================== #
# 1. Generation shape
# =========================================================================== #
def test_generation_emits_mappings_subtotals_grandtotals():
    rows = build_bs_rows(_hierarchy())
    by_type: dict[str, list[str]] = {}
    for r in rows:
        by_type.setdefault(r.row_type, []).append(r.line_code)
    # 7 L3 mappings, 4 L2 subtotals, 2 L1 grandtotals.
    assert len(by_type["mapping"]) == 7
    assert len(by_type["subtotal"]) == 4
    assert len(by_type["grandtotal"]) == 2
    # WC/CF aliases present and wired to the right L3.
    codes = {r.line_code: r for r in rows}
    assert codes["AR"].level_3 == "Trade receivables" and codes["AR"].section == "asset"
    assert codes["INVENTORY"].level_3 == "Inventories"
    assert codes["AP"].level_3 == "Trade payables" and codes["AP"].section == "credit"
    assert codes["CASH"].level_3 == "Cash & cash equivalents"
    assert codes["EQUITY"].level_3 == "Retained earnings" and codes["EQUITY"].section == "credit"


def test_grandtotal_titles_and_sections():
    rows = {r.row_type: r for r in build_bs_rows(_hierarchy()) if r.row_type == "grandtotal"}
    gts = [r for r in build_bs_rows(_hierarchy()) if r.row_type == "grandtotal"]
    assert gts[0].balance_title == "Total assets" and gts[0].section == "asset"
    assert gts[1].balance_title == "Total equity & liabilities" and gts[1].section == "credit"


# =========================================================================== #
# 2. aggregate_bs over the generated structure — full FY2024 cutoff
# =========================================================================== #
class TestBsStructureFullColumn:
    def setup_method(self):
        # current_fy 2024, last_closed_period 12 → FY/YTD cutoff (2024,12): all P1..P3 in.
        self.plan = P.build_period_plan("year", 2024, 12)
        self.stmt = aggregate_bs(_movements(), _structure(), self.plan)

    def test_l3_mapping_reconciles_to_gl_by_level_3(self):
        # AR (level_3 'Trade receivables') = 300 (P1) + 100 (P3) = 400, presented + (asset).
        assert _cell(self.stmt, "AR", "FY") == pytest.approx(400.0)
        assert _cell(self.stmt, "INVENTORY", "FY") == pytest.approx(300.0)
        assert _cell(self.stmt, "CASH", "FY") == pytest.approx(300.0)
        # AP (credit) stored -50 → presented +50.
        assert _cell(self.stmt, "AP", "FY") == pytest.approx(50.0)
        assert _cell(self.stmt, "EQUITY", "FY") == pytest.approx(200.0)

    def test_l2_subtotal_equals_sum_of_its_l3(self):
        # Fixed assets = Tangible 200 + Intangible 100 = 300.
        assert _cell(self.stmt, "BS_TOTAL_FIXED_ASSETS", "FY") == pytest.approx(300.0)
        # Current assets = Inventories 300 + AR 400 + Cash 300 = 1000.
        assert _cell(self.stmt, "BS_TOTAL_CURRENT_ASSETS", "FY") == pytest.approx(1000.0)
        # Equity = Retained 200 ; Liabilities = Payables 50.
        assert _cell(self.stmt, "BS_TOTAL_EQUITY", "FY") == pytest.approx(200.0)
        assert _cell(self.stmt, "BS_TOTAL_LIABILITIES", "FY") == pytest.approx(50.0)

    def test_total_assets_equals_sum_of_asset_l2_subtotals(self):
        fixed = _cell(self.stmt, "BS_TOTAL_FIXED_ASSETS", "FY")
        current = _cell(self.stmt, "BS_TOTAL_CURRENT_ASSETS", "FY")
        total_assets = _cell(self.stmt, "BS_GRANDTOTAL_ASSETS", "FY")
        assert total_assets == pytest.approx(fixed + current)
        assert total_assets == pytest.approx(1300.0)  # 300 + 1000

    def test_total_equity_liabilities_equals_sum_of_its_l2(self):
        eq = _cell(self.stmt, "BS_TOTAL_EQUITY", "FY")
        liab = _cell(self.stmt, "BS_TOTAL_LIABILITIES", "FY")
        total_el = _cell(self.stmt, "BS_GRANDTOTAL_EQUITY_LIABILITIES", "FY")
        assert total_el == pytest.approx(eq + liab)
        assert total_el == pytest.approx(250.0)

    def test_imbalance_is_materially_nonzero_on_gl_only_data(self):
        # GL-only: no opening balances / no P&L roll-forward → assets ≠ E&L.
        # imbalance = Total assets − Total E&L = 1300 − 250 = 1050.
        assets = _cell(self.stmt, "BS_GRANDTOTAL_ASSETS", "FY")
        total_el = _cell(self.stmt, "BS_GRANDTOTAL_EQUITY_LIABILITIES", "FY")
        assert self.stmt.imbalance["FY"] == pytest.approx(assets - total_el)
        assert self.stmt.imbalance["FY"] == pytest.approx(1050.0)
        assert abs(self.stmt.imbalance["FY"]) > 1.0  # honestly surfaced, not hidden


# =========================================================================== #
# 3. Cumulative cutoff — earlier cutoff excludes later movements
# =========================================================================== #
def test_cumulative_cutoff_excludes_later_periods():
    # last_closed_period 2 → YTD cutoff (2024,2): AR P3 (+100) excluded → AR 300.
    plan = P.build_period_plan("year", 2024, 2)
    stmt = aggregate_bs(_movements(), _structure(), plan)
    assert _cell(stmt, "AR", "YTD") == pytest.approx(300.0)
    # current assets then = Inv 300 + AR 300 + Cash 300 = 900.
    assert _cell(stmt, "BS_TOTAL_CURRENT_ASSETS", "YTD") == pytest.approx(900.0)
    # total assets = 300 (fixed) + 900 = 1200 ; E&L still 250 → imbalance 950.
    assert _cell(stmt, "BS_GRANDTOTAL_ASSETS", "YTD") == pytest.approx(1200.0)
    assert stmt.imbalance["YTD"] == pytest.approx(950.0)


# =========================================================================== #
# 4. Empty column → all zero, imbalance 0
# =========================================================================== #
def test_empty_column_zero():
    plan = P.build_period_plan("year", 2024, 0)  # YTD empty
    stmt = aggregate_bs(_movements(), _structure(), plan)
    assert _cell(stmt, "AR", "YTD") == pytest.approx(0.0)
    assert _cell(stmt, "BS_GRANDTOTAL_ASSETS", "YTD") == pytest.approx(0.0)
    assert stmt.imbalance["YTD"] == pytest.approx(0.0)


# =========================================================================== #
# 5. Determinism
# =========================================================================== #
def test_structure_generation_deterministic():
    a = [(r.sort_order, r.line_code, r.row_type, r.section) for r in build_bs_rows(_hierarchy())]
    b = [(r.sort_order, r.line_code, r.row_type, r.section) for r in build_bs_rows(_hierarchy())]
    assert a == b


# =========================================================================== #
# 6. DB round-trip — fetch_bs_structure parses seeded BS rows (incl. grandtotal)
# =========================================================================== #
class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeSession:
    """Returns the SAME rows seed_bs_structure would have written to dim_pl_structure
    (column order matching fetch_bs_structure's SELECT:
     sort_order, line_code, balance_title, row_type, level_2, level_3, level_4,
     kpi_code, is_bold)."""

    def __init__(self):
        self.rows = []
        for r in build_bs_rows(_hierarchy()):
            self.rows.append(
                (r.sort_order, r.line_code, r.balance_title, r.row_type,
                 r.level_2, r.level_3, None, f"BS:{r.section}", r.is_bold)
            )

    def execute(self, statement, params=None):
        return _FakeResult(self.rows)


def test_fetch_bs_structure_roundtrips_grandtotal():
    from app.services.balance_sheet import fetch_bs_structure

    structure = fetch_bs_structure(_FakeSession())
    by_type = {s.row_type for s in structure}
    assert "grandtotal" in by_type
    # The parsed structure produces the SAME aggregation as the in-memory one.
    plan = P.build_period_plan("year", 2024, 12)
    stmt = aggregate_bs(_movements(), structure, plan)
    assert _cell(stmt, "BS_GRANDTOTAL_ASSETS", "FY") == pytest.approx(1300.0)
    assert _cell(stmt, "BS_GRANDTOTAL_EQUITY_LIABILITIES", "FY") == pytest.approx(250.0)
    assert stmt.imbalance["FY"] == pytest.approx(1050.0)


# =========================================================================== #
# 7. WC picks up REAL BS balances (AR/INV/AP aliases) + REAL P&L codes
#    (NET_SALES / COST_OF_MATERIALS via candidate resolution).
# =========================================================================== #
def test_wc_resolves_real_bs_and_pl_codes():
    from app.services.statements import GlMovement, StructureLine, aggregate_pl
    from app.services.working_capital import compute_working_capital

    plan = P.build_period_plan("year", 2024, 12)
    bs = aggregate_bs(_movements(), _structure(), plan)

    # A P&L using the REAL Decidra codes (NET_SALES / COST_OF_MATERIALS), NOT the
    # synthetic REVENUE/COGS — WC must still resolve them via candidate codes.
    pl_struct = [
        StructureLine(10, "NET_SALES", "Net sales", "mapping", level_3="Net sales"),
        StructureLine(20, "COST_OF_MATERIALS", "Cost of materials", "mapping", level_3="Cost of materials"),
    ]
    pl_mov = [
        GlMovement(2024, 1, "Umsatzerlöse", "Net sales", "x", -1000.0),       # presented +1000
        GlMovement(2024, 1, "Materialaufwand", "Cost of materials", "x", 200.0),  # presented -200
    ]
    pl = aggregate_pl(pl_mov, pl_struct, plan)
    wc = compute_working_capital(bs, pl, plan)

    # AR 400, INV 300, AP 50 from the real BS aliases → NWC = 400 + 300 − 50 = 650.
    assert _cell(wc, "NWC", "FY") == pytest.approx(650.0)
    # DSO uses real NET_SALES (1000); DIO/DPO use real COST_OF_MATERIALS magnitude (200).
    assert _cell(wc, "DSO", "FY") == pytest.approx(400 / 1000 * 365)
    assert _cell(wc, "DIO", "FY") == pytest.approx(300 / 200 * 365)
    assert _cell(wc, "DPO", "FY") == pytest.approx(50 / 200 * 365)
