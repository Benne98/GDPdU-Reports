"""Worked-example tests for the synthetic SUPPLIER plan (``plan_synth.com_plan``).

DB-free, deterministic, synthetic fixtures only — the symmetric sibling of
``test_plan_synth.py``'s sales-plan tests.  ``com_plan`` is the supplier/
cost-of-materials mirror of ``sales_plan``; it reuses the SAME pure primitives
(``seasonal_index`` / ``plan_years`` / ``forecast_open_periods``), so these tests
assert structural equivalence with the sales path plus the cost-of-materials
sign/magnitude semantics.

Sign convention
---------------
``fact_com.cost_of_materials = amount`` (material is a debit) → already a POSITIVE
magnitude.  Every formula multiplies the actual by non-negative growth/seasonal
weights, so the plan stays positive — exactly like ``gross_sales_plan``.

Worked example (supplier ``01200``, base FY 2024)
-------------------------------------------------
base FY 2024 cost_of_materials = 200 (p1) + 300 (p2) = 500.
  FIRST_PLAN_FY = 2026, k=1 → PlanAnnual(2026) = 500 * 1.05 = 525.0 (positive).
  Period split by base-FY seasonal index: p1 = 200/500 = 2/5, p2 = 300/500 = 3/5.
    FY26P p1 = 525 * 2/5 = 210.0 ; p2 = 525 * 3/5 = 315.0 ; Σ = 525 ✓ (reconciles).
"""
from __future__ import annotations

import pandas as pd
import pytest

from etl import plan_synth as P

SUP = "01200"
COM_COLS = ["supplier_id", "fiscal_year", "fiscal_period", "cost_of_materials"]


def _com_actuals() -> pd.DataFrame:
    """Supplier cost-of-materials actuals (positive) per supplier × fiscal_period.

    Mirrors ``fixtures.plan_sales_actuals`` shape: a base FY 2024 with a 200/300
    monthly spread plus current-year (FY2025) closed periods 1..2 for the forecast.
    """
    return pd.DataFrame(
        [
            (SUP, 2024, 1, 200.0),
            (SUP, 2024, 2, 300.0),
            # current-year (FY2025) closed periods for the forecast path
            (SUP, 2025, 1, 110.0),
            (SUP, 2025, 2, 165.0),
        ],
        columns=COM_COLS,
    )


# --------------------------------------------------------------------------- #
# Structure mirrors sales_plan
# --------------------------------------------------------------------------- #
def test_com_plan_output_columns_mirror_sales_plan():
    com = P.com_plan(_com_actuals(), base_fy=2024, horizon=4, growth_rate=0.05)
    # Same shape as sales_plan but on supplier_id + cost_of_materials_plan.
    for col in ("supplier_id", "fiscal_year", "fiscal_period", "scenario",
                "cost_of_materials_plan", "is_synthetic"):
        assert col in com.columns
    assert "customer_id" not in com.columns
    assert "gross_sales_plan" not in com.columns
    assert com["is_synthetic"].all()


def test_com_plan_horizon_years_match_plan_path():
    com = P.com_plan(_com_actuals(), base_fy=2024, horizon=4, growth_rate=0.05)
    plan = com[com["scenario"] == "plan"]
    assert set(plan["fiscal_year"]) == {2026, 2027, 2028, 2029}


# --------------------------------------------------------------------------- #
# Worked example + seasonal reconciliation (Σ months == annual)
# --------------------------------------------------------------------------- #
def test_com_plan_worked_example_fy26_is_525():
    com = P.com_plan(_com_actuals(), base_fy=2024, horizon=4, growth_rate=0.05)
    fy26 = com[(com["supplier_id"] == SUP) & (com["fiscal_year"] == 2026)]
    assert fy26["cost_of_materials_plan"].sum() == pytest.approx(525.0)   # 500 * 1.05
    # cost-of-materials magnitude stays positive (debit)
    assert (fy26["cost_of_materials_plan"] >= 0).all()
    # period split: 2/5, 3/5 of 525
    p1 = fy26[fy26["fiscal_period"] == 1]["cost_of_materials_plan"].iloc[0]
    p2 = fy26[fy26["fiscal_period"] == 2]["cost_of_materials_plan"].iloc[0]
    assert p1 == pytest.approx(210.0)
    assert p2 == pytest.approx(315.0)
    # split reconciles to the annual
    assert p1 + p2 == pytest.approx(fy26["cost_of_materials_plan"].sum())


def test_com_plan_seasonal_split_reconciles_every_year():
    com = P.com_plan(_com_actuals(), base_fy=2024, horizon=4, growth_rate=0.07)
    plan = com[com["scenario"] == "plan"]
    for (sup, fy), grp in plan.groupby(["supplier_id", "fiscal_year"]):
        k = fy - (2024 + 2) + 1
        expected = 500.0 * 1.07 ** k
        assert grp["cost_of_materials_plan"].sum() == pytest.approx(expected)
        # 12 monthly rows per (supplier, year)
        assert len(grp) == 12


# --------------------------------------------------------------------------- #
# Growth compounding
# --------------------------------------------------------------------------- #
def test_com_plan_growth_compounds_across_horizon():
    com = P.com_plan(_com_actuals(), base_fy=2024, horizon=4, growth_rate=0.05)
    by_year = (
        com[com["scenario"] == "plan"]
        .groupby("fiscal_year")["cost_of_materials_plan"].sum()
    )
    assert by_year[2026] == pytest.approx(500 * 1.05 ** 1)
    assert by_year[2027] == pytest.approx(500 * 1.05 ** 2)
    assert by_year[2028] == pytest.approx(500 * 1.05 ** 3)
    assert by_year[2029] == pytest.approx(500 * 1.05 ** 4)


def test_com_plan_growth_zero_is_flat():
    com = P.com_plan(_com_actuals(), base_fy=2024, horizon=2, growth_rate=0.0)
    plan = com[com["scenario"] == "plan"]
    for fy in (2026, 2027):
        assert plan[plan["fiscal_year"] == fy]["cost_of_materials_plan"].sum() == pytest.approx(500.0)


# --------------------------------------------------------------------------- #
# Forecast of OPEN periods
# --------------------------------------------------------------------------- #
def test_com_plan_includes_forecast_open_periods_only():
    com = P.com_plan(
        _com_actuals(), base_fy=2024, horizon=4, growth_rate=0.05,
        current_fy=2025, last_closed_period=2, prior_fy=2024,
    )
    fc = com[com["scenario"] == "forecast"]
    assert not fc.empty
    assert (fc["fiscal_year"] == 2025).all()
    # closed periods 1,2 are never emitted; open periods 3..12 are present
    assert fc[fc["fiscal_period"] <= 2].empty
    assert sorted(fc["fiscal_period"].unique()) == list(range(3, 13))


def test_com_plan_forecast_run_rate_reconciles_full_year():
    # base FY 2024: 200/300 spread (coverage of closed p1..p2 == 1.0 of the annual)
    com = P.com_plan(
        _com_actuals(), base_fy=2024, horizon=1, growth_rate=0.05,
        current_fy=2025, last_closed_period=2, prior_fy=2024,
    )
    fc = com[com["scenario"] == "forecast"]
    # prior seasonal index covers exactly p1..p2 (200/500 + 300/500 = 1.0)
    # YTD(FY2025, p1..p2) = 110 + 165 = 275 ; proj_annual = 275 / 1.0 = 275
    ytd = 275.0
    proj_annual = 275.0
    assert ytd + fc["cost_of_materials_plan"].sum() == pytest.approx(proj_annual)
    # all open-period forecast values are 0 here (prior had no p3..p12 weight) but positive-safe
    assert (fc["cost_of_materials_plan"] >= 0).all()


def test_com_plan_no_forecast_when_year_closed():
    com = P.com_plan(
        _com_actuals(), base_fy=2024, horizon=2, growth_rate=0.05,
        current_fy=2025, last_closed_period=12, prior_fy=2024,
    )
    assert com[com["scenario"] == "forecast"].empty


# --------------------------------------------------------------------------- #
# Empty / zero fallback
# --------------------------------------------------------------------------- #
def test_com_plan_empty_input_yields_empty_frame():
    empty = pd.DataFrame(columns=COM_COLS)
    com = P.com_plan(empty, base_fy=2024, horizon=4, growth_rate=0.05)
    assert com.empty
    assert "cost_of_materials_plan" in com.columns


def test_com_plan_zero_base_yields_zero_no_div_by_zero():
    df = pd.DataFrame(
        [("01999", 2024, 1, 0.0), ("01999", 2024, 2, 0.0)],
        columns=COM_COLS,
    )
    com = P.com_plan(df, base_fy=2024, horizon=2, growth_rate=0.05)
    sup = com[com["supplier_id"] == "01999"]
    assert not sup.empty                                       # supplier still present
    assert sup["cost_of_materials_plan"].abs().sum() == 0.0    # all zero, no NaN
    assert sup["cost_of_materials_plan"].notna().all()


def test_com_plan_new_supplier_absent_from_base_excluded():
    df = pd.DataFrame(
        [("01200", 2024, 1, 500.0), ("01777", 2025, 1, 999.0)],
        columns=COM_COLS,
    )
    com = P.com_plan(df, base_fy=2024, horizon=1, growth_rate=0.05)
    assert "01777" not in set(com["supplier_id"])   # no base-FY rows → not invented
    assert "01200" in set(com["supplier_id"])


# --------------------------------------------------------------------------- #
# generate_plan returns the third (com) frame
# --------------------------------------------------------------------------- #
def test_generate_plan_returns_com_frame():
    from etl.tests import fixtures as F

    gl, sales, com = P.generate_plan(
        F.plan_gl_actuals(), F.plan_sales_actuals(), _com_actuals(),
        base_fy=2024, current_fy=2025, last_closed_period=2,
        horizon_years=4, growth_rate=0.05, group_col="pl_group",
    )
    assert "cost_of_materials_plan" in com.columns
    assert com["is_synthetic"].all()
    assert set(com["scenario"]) == {"forecast", "plan"}
    # GL/sales outputs are unchanged vs the 2-arg path
    gl2, sales2, com2 = P.generate_plan(
        F.plan_gl_actuals(), F.plan_sales_actuals(),
        base_fy=2024, current_fy=2025, last_closed_period=2,
        horizon_years=4, growth_rate=0.05, group_col="pl_group",
    )
    pd.testing.assert_frame_equal(gl, gl2)
    pd.testing.assert_frame_equal(sales, sales2)
    assert com2.empty   # no com_actuals → empty com frame, GL/sales intact


def test_generate_plan_com_mirrors_standalone_com_plan():
    """The com frame from generate_plan equals com_plan() called directly (same args)."""
    from etl.tests import fixtures as F

    _, _, com_via_gen = P.generate_plan(
        F.plan_gl_actuals(), F.plan_sales_actuals(), _com_actuals(),
        base_fy=2024, current_fy=2025, last_closed_period=2,
        horizon_years=4, growth_rate=0.05,
    )
    com_direct = P.com_plan(
        _com_actuals(), base_fy=2024, horizon=4, growth_rate=0.05,
        current_fy=2025, last_closed_period=2, prior_fy=2024,
    )
    # generate_plan sorts by supplier_id; align before comparing
    a = com_via_gen.sort_values(
        ["supplier_id", "fiscal_year", "fiscal_period", "scenario"], kind="mergesort"
    ).reset_index(drop=True)
    b = com_direct.sort_values(
        ["supplier_id", "fiscal_year", "fiscal_period", "scenario"], kind="mergesort"
    ).reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)
