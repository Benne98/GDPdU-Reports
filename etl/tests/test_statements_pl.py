"""P5 / C1 — GOLDEN tests for the pure P&L aggregation (app.services.statements).

NO DB.  A tiny synthetic GL-movement fixture is built in-test and aggregated by
the pure core ``aggregate_pl``.  Asserts the exact worked-example numbers from the
statements.py docstring for the YTD column (current_fy=2025, last_closed_period=5):
  REVENUE=+3000, COGS=-500, GROSS_PROFIT=+2500, GROSS_MARGIN_PCT=83.333…, EBITDA=+2500.
Also covers the documented edge cases (div-by-zero margin, negative revenue,
unmapped reconciliation, plan column).
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
from app.services import statements as S
from app.services.statements import GlMovement, StructureLine, aggregate_pl, default_pl_structure


# --------------------------------------------------------------------------- #
# Synthetic GL fixture (STORED sign: + debit, - credit)
# --------------------------------------------------------------------------- #
# Revenue (Net sales, credit) and Material (Cost of materials, debit) spread over
# FY2025 P1..P5 so the YTD window (1..5) captures the full worked example, and
# FY2024 has a full prior year for FY-1/LTM_PY columns.
def _movements() -> list[GlMovement]:
    rows: list[GlMovement] = []
    # FY2025 revenue: -600 each in P1..P5 -> sum -3000 (presented +3000)
    for p in range(1, 6):
        rows.append(GlMovement(2025, p, "Umsatzerlöse", "Net sales", "Inlandsumsatz", -600.0))
    # FY2025 material: +100 each in P1..P5 -> sum +500 (presented -500)
    for p in range(1, 6):
        rows.append(GlMovement(2025, p, "Materialaufwand", "Cost of materials", "Rohstoffe", 100.0))
    # FY2024 full prior year (for FY-1/LTM columns): revenue -200/period, material +50/period
    for p in range(1, 13):
        rows.append(GlMovement(2024, p, "Umsatzerlöse", "Net sales", "Inlandsumsatz", -200.0))
        rows.append(GlMovement(2024, p, "Materialaufwand", "Cost of materials", "Rohstoffe", 50.0))
    return rows


def _cell(stmt, line_code, column_key):
    line = next(l for l in stmt.lines if l.line_code == line_code)
    cell = next(c for c in line.cells if c.column_key == column_key)
    return cell.value


# =========================================================================== #
# Golden — worked example (YTD column)
# =========================================================================== #
class TestPlWorkedExample:
    def setup_method(self):
        self.plan = P.build_period_plan("year", 2025, 5)
        self.stmt = aggregate_pl(_movements(), default_pl_structure(), self.plan)

    def test_revenue_ytd_is_3000(self):
        assert _cell(self.stmt, "REVENUE", "YTD") == pytest.approx(3000.0)

    def test_cogs_ytd_is_negative_500(self):
        assert _cell(self.stmt, "COGS", "YTD") == pytest.approx(-500.0)

    def test_gross_profit_ytd_is_2500(self):
        assert _cell(self.stmt, "GROSS_PROFIT", "YTD") == pytest.approx(2500.0)

    def test_gross_margin_ytd_is_83_33(self):
        assert _cell(self.stmt, "GROSS_MARGIN_PCT", "YTD") == pytest.approx(2500 / 3000 * 100)

    def test_ebitda_ytd_is_2500(self):
        # EBITDA = revenue + cogs (only two mapping lines) = 3000 - 500 = 2500
        assert _cell(self.stmt, "EBITDA", "YTD") == pytest.approx(2500.0)

    def test_sign_convention_revenue_positive_cost_negative(self):
        # Documented: revenue (credit) presents positive, cost (debit) negative.
        assert _cell(self.stmt, "REVENUE", "YTD") > 0
        assert _cell(self.stmt, "COGS", "YTD") < 0

    def test_fy_full_2025_equals_ytd_here(self):
        # Only P1..P5 have 2025 data, so FY column (P1..12) == YTD here.
        assert _cell(self.stmt, "REVENUE", "FY") == pytest.approx(3000.0)

    def test_fy_minus_1_full_prior_year(self):
        # FY2024: revenue -200*12 = -2400 -> presented +2400
        assert _cell(self.stmt, "REVENUE", "FY-1") == pytest.approx(2400.0)
        assert _cell(self.stmt, "COGS", "FY-1") == pytest.approx(-600.0)  # 50*12 -> -600
        assert _cell(self.stmt, "GROSS_PROFIT", "FY-1") == pytest.approx(1800.0)


# =========================================================================== #
# Reconciliation — Σ(lines) + unmapped == Σ presented(all movements)
# =========================================================================== #
class TestReconciliation:
    def test_mapping_lines_plus_unmapped_equal_total_presented(self):
        plan = P.build_period_plan("year", 2025, 5)
        stmt = aggregate_pl(_movements(), default_pl_structure(), plan)
        for col_key in stmt.column_keys:
            # Σ presented over all movements in this column
            col = next(c for c in plan.columns if c.key == col_key)
            buckets = set(col.buckets)
            total_presented = sum(
                -mv.amount for mv in _movements()
                if (mv.fiscal_year, mv.fiscal_period) in buckets
            )
            rev = _cell(stmt, "REVENUE", col_key) or 0.0
            cogs = _cell(stmt, "COGS", col_key) or 0.0
            assert (rev + cogs + stmt.unmapped_total[col_key]) == pytest.approx(total_presented)

    def test_unmapped_account_surfaces_in_unmapped_total(self):
        movements = _movements() + [
            # An account with a level_3 that no structure line matches.
            GlMovement(2025, 1, "Sonstiges", "Other income", "Misc", -111.0),
        ]
        plan = P.build_period_plan("year", 2025, 5)
        stmt = aggregate_pl(movements, default_pl_structure(), plan)
        # presented = -(-111) = +111 lands in unmapped for YTD (P1 in window)
        assert stmt.unmapped_total["YTD"] == pytest.approx(111.0)


# =========================================================================== #
# Edge cases
# =========================================================================== #
class TestEdgeCases:
    def test_zero_revenue_margin_is_none(self):
        # No revenue movements at all -> revenue 0 -> margin None (no div-by-zero).
        movements = [GlMovement(2025, 1, "Materialaufwand", "Cost of materials", "Rohstoffe", 100.0)]
        plan = P.build_period_plan("year", 2025, 5)
        stmt = aggregate_pl(movements, default_pl_structure(), plan)
        assert _cell(stmt, "REVENUE", "YTD") == pytest.approx(0.0)
        assert _cell(stmt, "GROSS_MARGIN_PCT", "YTD") is None

    def test_empty_movements_no_crash(self):
        plan = P.build_period_plan("year", 2025, 5)
        stmt = aggregate_pl([], default_pl_structure(), plan)
        assert _cell(stmt, "REVENUE", "YTD") == pytest.approx(0.0)
        assert _cell(stmt, "GROSS_MARGIN_PCT", "YTD") is None
        assert stmt.unmapped_total["YTD"] == pytest.approx(0.0)

    def test_negative_revenue_ratio_still_computed(self):
        # Net credit-note period: revenue stored positive (+debit) -> presented negative.
        movements = [
            GlMovement(2025, 1, "Umsatzerlöse", "Net sales", "X", 1000.0),   # presented -1000
            GlMovement(2025, 1, "Materialaufwand", "Cost of materials", "Y", 100.0),  # presented -100
        ]
        plan = P.build_period_plan("year", 2025, 5)
        stmt = aggregate_pl(movements, default_pl_structure(), plan)
        rev = _cell(stmt, "REVENUE", "YTD")
        gp = _cell(stmt, "GROSS_PROFIT", "YTD")
        margin = _cell(stmt, "GROSS_MARGIN_PCT", "YTD")
        assert rev == pytest.approx(-1000.0)
        assert gp == pytest.approx(-1100.0)
        # ratio computed with sign (informational, not zeroed)
        assert margin == pytest.approx(-1100.0 / -1000.0 * 100)

    def test_wildcard_level_filter_matches_any(self):
        # A line with all level filters None matches every movement.
        struct = [StructureLine(10, "ALL", "All", "mapping")]
        movements = [
            GlMovement(2025, 1, "A", "B", "C", -50.0),
            GlMovement(2025, 2, "D", "E", "F", 20.0),
        ]
        plan = P.build_period_plan("year", 2025, 5)
        stmt = aggregate_pl(movements, struct, plan)
        # presented = 50 - 20 = 30 ; both in YTD window (P1,P2)
        assert _cell(stmt, "ALL", "YTD") == pytest.approx(30.0)
        assert stmt.unmapped_total["YTD"] == pytest.approx(0.0)


# =========================================================================== #
# Determinism
# =========================================================================== #
def test_aggregate_pl_deterministic():
    plan = P.build_period_plan("year", 2025, 5)
    a = aggregate_pl(_movements(), default_pl_structure(), plan)
    b = aggregate_pl(_movements(), default_pl_structure(), plan)
    assert [(l.line_code, [c.value for c in l.cells]) for l in a.lines] == \
           [(l.line_code, [c.value for c in l.cells]) for l in b.lines]


# =========================================================================== #
# default_pl_structure shape
# =========================================================================== #
def test_default_structure_has_expected_lines():
    codes = [s.line_code for s in default_pl_structure()]
    assert codes == ["REVENUE", "COGS", "GROSS_PROFIT", "GROSS_MARGIN_PCT", "EBITDA"]


# =========================================================================== #
# RUNNING-TOTAL P&L semantics — the REAL Decidra structure (subtotal/calc rows
# are the cumulative sum of all mapping lines above them, presented sign).
# =========================================================================== #
# A Decidra-shaped structure (every row_type present): mapping lines feed the
# running sum; TOTAL_OUTPUT/EBIT/EBT/NET_PROFIT are 'subtotal', GROSS_PROFIT and
# EBITDA are 'calc' (NON-ratio → also running sum).  No _PCT line exists, exactly
# like the live structure.
def _decidra_structure() -> list[StructureLine]:
    return [
        StructureLine(1,  "NET_SALES",                 "Net sales",                "mapping", level_3="Net sales"),
        StructureLine(2,  "FINISHED_GOODS_WIP",        "Finished goods / WIP",     "mapping", level_3="Finished goods/WIP"),
        StructureLine(3,  "OWN_WORK_CAPITALISED",      "Own work capitalised",     "mapping", level_3="Own work capitalised"),
        StructureLine(4,  "TOTAL_OUTPUT",              "Total output",             "subtotal", is_bold=True),
        StructureLine(5,  "COST_OF_MATERIALS",         "Cost of materials",        "mapping", level_3="Cost of materials"),
        StructureLine(6,  "GROSS_PROFIT",              "Gross profit",             "calc", kpi_code="GROSS_PROFIT", is_bold=True),
        StructureLine(7,  "PERSONNEL_EXPENSES",        "Personnel expenses",       "mapping", level_3="Personnel expenses"),
        StructureLine(8,  "OTHER_OPERATING_INCOME",    "Other operating income",   "mapping", level_3="Other operating income"),
        StructureLine(9,  "OTHER_OPERATING_EXPENSES",  "Other operating expenses", "mapping", level_3="Other operating expenses"),
        StructureLine(10, "EBITDA",                    "EBITDA",                   "calc", kpi_code="EBITDA", is_bold=True),
        StructureLine(11, "DEPRECIATION_AMORTISATION", "Depreciation/amort.",      "mapping", level_3="Depreciation/amortisation"),
        StructureLine(12, "EBIT",                      "EBIT",                     "subtotal", is_bold=True),
        StructureLine(13, "INTEREST_INCOME",           "Interest income",          "mapping", level_3="Interest income"),
        StructureLine(14, "INTEREST_EXPENSES",         "Interest expenses",        "mapping", level_3="Interest expenses"),
        StructureLine(15, "EBT",                       "EBT",                      "subtotal", is_bold=True),
        StructureLine(16, "OTHER_TAXES",               "Other taxes",              "mapping", level_3="Other taxes"),
        StructureLine(17, "TAXES_ON_INCOME",           "Taxes on income",          "mapping", level_3="Taxes on income"),
        StructureLine(18, "NET_PROFIT",                "Net profit",               "subtotal", is_bold=True),
    ]


# Synthetic GL keyed by level_3 (single column).  presented = -amount, so stored
# amounts are the NEGATIVE of the presented values below.
# Presented: NET_SALES +1000, FINISHED_GOODS_WIP +100, OWN_WORK 0,
#   COST_OF_MATERIALS -400, PERSONNEL -200, OTHER_INC +50, OTHER_EXP -90,
#   DEPRECIATION -60, INTEREST_INC +10, INTEREST_EXP -8, OTHER_TAXES -5, TAX -120.
_DECIDRA_PRESENTED = {
    "Net sales": 1000.0,
    "Finished goods/WIP": 100.0,
    "Own work capitalised": 0.0,
    "Cost of materials": -400.0,
    "Personnel expenses": -200.0,
    "Other operating income": 50.0,
    "Other operating expenses": -90.0,
    "Depreciation/amortisation": -60.0,
    "Interest income": 10.0,
    "Interest expenses": -8.0,
    "Other taxes": -5.0,
    "Taxes on income": -120.0,
}


def _decidra_movements() -> list[GlMovement]:
    # All in FY2025 P1 so the YTD window (1..5) captures everything once.
    rows: list[GlMovement] = []
    for level_3, presented in _DECIDRA_PRESENTED.items():
        rows.append(GlMovement(2025, 1, level_3, level_3, level_3, -presented))  # stored = -presented
    return rows


class TestRunningTotalSemantics:
    def setup_method(self):
        self.plan = P.build_period_plan("year", 2025, 5)
        self.stmt = aggregate_pl(_decidra_movements(), _decidra_structure(), self.plan)

    def test_mapping_lines_unchanged(self):
        assert _cell(self.stmt, "NET_SALES", "YTD") == pytest.approx(1000.0)
        assert _cell(self.stmt, "COST_OF_MATERIALS", "YTD") == pytest.approx(-400.0)
        assert _cell(self.stmt, "OWN_WORK_CAPITALISED", "YTD") == pytest.approx(0.0)

    def test_total_output_is_running_sum_of_mappings_above(self):
        # NET_SALES + FINISHED_GOODS_WIP + OWN_WORK = 1000 + 100 + 0 = 1100
        assert _cell(self.stmt, "TOTAL_OUTPUT", "YTD") == pytest.approx(1100.0)

    def test_gross_profit_is_running_sum_not_rev_plus_cogs_special(self):
        # 1000 + 100 + 0 + (-400) = 700  (running cumulative, NOT a code-keyed formula)
        assert _cell(self.stmt, "GROSS_PROFIT", "YTD") == pytest.approx(700.0)

    def test_ebitda_is_running_sum_through_other_opex(self):
        # 1000+100+0-400-200+50-90 = 460
        assert _cell(self.stmt, "EBITDA", "YTD") == pytest.approx(460.0)

    def test_ebit_includes_depreciation(self):
        # EBITDA 460 - 60 depreciation = 400
        assert _cell(self.stmt, "EBIT", "YTD") == pytest.approx(400.0)

    def test_ebt_includes_financial_result(self):
        # EBIT 400 + 10 interest income - 8 interest expense = 402
        assert _cell(self.stmt, "EBT", "YTD") == pytest.approx(402.0)

    def test_net_profit_equals_sum_of_all_mappings(self):
        # The KEY INVARIANT: NET_PROFIT == Σ of every mapping line (presented).
        expected = sum(_DECIDRA_PRESENTED.values())  # = 277.0
        net = _cell(self.stmt, "NET_PROFIT", "YTD")
        assert net == pytest.approx(expected)
        assert net == pytest.approx(277.0)

    def test_subtotals_do_not_recount_other_subtotals(self):
        # If subtotals re-counted earlier subtotals/calcs, NET_PROFIT would explode.
        # The invariant above (== Σ mappings) proves only mapping lines contribute.
        mapping_codes = [
            l.line_code for l in self.stmt.lines if l.row_type == "mapping"
        ]
        col = next(c for c in self.plan.columns if c.key == "YTD")
        mapped_sum = sum((_cell(self.stmt, c, "YTD") or 0.0) for c in mapping_codes)
        assert _cell(self.stmt, "NET_PROFIT", "YTD") == pytest.approx(mapped_sum)

    def test_subtotal_with_no_mappings_above_is_zero(self):
        # A subtotal placed at the very top (sort 0) has no mapping line above it → 0.
        struct = [StructureLine(0, "OPENING_SUBTOTAL", "Opening", "subtotal")] + _decidra_structure()
        stmt = aggregate_pl(_decidra_movements(), struct, self.plan)
        assert _cell(stmt, "OPENING_SUBTOTAL", "YTD") == pytest.approx(0.0)


class TestRatioRowStillFormula:
    """A structure that DOES define a _PCT ratio keeps ratio semantics (not summed)."""

    def _struct(self):
        return [
            StructureLine(1, "NET_SALES", "Net sales", "mapping", level_3="Net sales"),
            StructureLine(2, "COST_OF_MATERIALS", "Cost of materials", "mapping", level_3="Cost of materials"),
            StructureLine(3, "GROSS_PROFIT", "Gross profit", "calc", kpi_code="GROSS_PROFIT"),
            StructureLine(4, "GROSS_MARGIN_PCT", "Gross margin %", "calc", kpi_code="GROSS_MARGIN_PCT"),
        ]

    def _movements(self):
        return [
            GlMovement(2025, 1, "Net sales", "Net sales", "X", -1000.0),          # presented +1000
            GlMovement(2025, 1, "Cost of materials", "Cost of materials", "Y", 400.0),  # presented -400
        ]

    def test_ratio_is_formula_not_running_sum(self):
        stmt = aggregate_pl(self._movements(), self._struct(), P.build_period_plan("year", 2025, 5))
        # GROSS_PROFIT (non-ratio calc) = running sum = 1000 - 400 = 600
        assert _cell(stmt, "GROSS_PROFIT", "YTD") == pytest.approx(600.0)
        # GROSS_MARGIN_PCT (ratio) = 600 / 1000 * 100 = 60.0 (NOT a running sum)
        assert _cell(stmt, "GROSS_MARGIN_PCT", "YTD") == pytest.approx(60.0)

    def test_ratio_zero_denominator_is_none(self):
        movements = [GlMovement(2025, 1, "Cost of materials", "Cost of materials", "Y", 400.0)]
        stmt = aggregate_pl(movements, self._struct(), P.build_period_plan("year", 2025, 5))
        assert _cell(stmt, "NET_SALES", "YTD") == pytest.approx(0.0)
        assert _cell(stmt, "GROSS_MARGIN_PCT", "YTD") is None
