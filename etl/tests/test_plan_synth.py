"""Golden-file / worked-example tests for the deterministic plan synthesizer (DF4).

NO DB, NO randomness.  Every numeric expectation is the one written out in the
``etl/plan_synth`` module docstring.  Sign convention is asserted explicitly
(credit revenue stays negative through growth).
"""
from __future__ import annotations

import pandas as pd
import pytest

from etl import plan_synth as P
from etl.tests import fixtures as F

REV = "0180000"   # revenue account (credit, negative amount)
MAT = "0130000"   # material account (debit, positive amount)
CUST = "01100"


# --------------------------------------------------------------------------- #
# (2) Seasonal index
# --------------------------------------------------------------------------- #
def test_seasonal_index_matches_base_year_profile():
    s = P.seasonal_index(F.plan_gl_actuals(), base_fy=2024)
    # revenue: -1000/-3000, -2000/-3000
    assert s[REV][1] == pytest.approx(1 / 3)
    assert s[REV][2] == pytest.approx(2 / 3)
    assert sum(s[REV].values()) == pytest.approx(1.0)
    # material: 200/500, 300/500
    assert s[MAT][1] == pytest.approx(2 / 5)
    assert s[MAT][2] == pytest.approx(3 / 5)
    assert sum(s[MAT].values()) == pytest.approx(1.0)
    # untouched periods are zero
    assert s[REV][3] == 0.0


def test_seasonal_index_uniform_fallback_when_annual_zero():
    df = pd.DataFrame(
        [("0199999", 2024, 1, 100.0), ("0199999", 2024, 2, -100.0)],
        columns=["account_number_group", "fiscal_year", "fiscal_period", "amount"],
    )
    s = P.seasonal_index(df, base_fy=2024)
    assert all(w == pytest.approx(1 / 12) for w in s["0199999"].values())
    assert sum(s["0199999"].values()) == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# (3) Plan years — the WORKED EXAMPLE from the docstring
# --------------------------------------------------------------------------- #
def test_plan_worked_example_fy26_revenue_is_minus_3150():
    plan = P.plan_years(F.plan_gl_actuals(), base_fy=2024, horizon_years=4, growth_rate=0.05)
    fy26_rev = plan[(plan["account_number_group"] == REV) & (plan["fiscal_year"] == 2026)]
    # Annual reconciles: -3000 * 1.05^1 = -3150
    assert fy26_rev["amount"].sum() == pytest.approx(-3150.0)
    # SIGN preserved: revenue is credit -> stays negative
    assert fy26_rev["amount"].sum() < 0
    # Period split follows base-FY seasonality
    p1 = fy26_rev[fy26_rev["fiscal_period"] == 1]["amount"].iloc[0]
    p2 = fy26_rev[fy26_rev["fiscal_period"] == 2]["amount"].iloc[0]
    assert p1 == pytest.approx(-1050.0)   # -3150 * 1/3
    assert p2 == pytest.approx(-2100.0)   # -3150 * 2/3
    # split reconciles to the annual
    assert p1 + p2 == pytest.approx(fy26_rev["amount"].sum())


def test_plan_compounding_across_horizon():
    plan = P.plan_years(F.plan_gl_actuals(), base_fy=2024, horizon_years=4, growth_rate=0.05)
    rev = plan[plan["account_number_group"] == REV]
    by_year = rev.groupby("fiscal_year")["amount"].sum()
    assert by_year[2026] == pytest.approx(-3000 * 1.05 ** 1)
    assert by_year[2027] == pytest.approx(-3000 * 1.05 ** 2)
    assert by_year[2028] == pytest.approx(-3000 * 1.05 ** 3)
    assert by_year[2029] == pytest.approx(-3000 * 1.05 ** 4)
    # horizon FY26..FY29 only
    assert set(by_year.index) == {2026, 2027, 2028, 2029}


def test_growth_rate_zero_is_flat_equal_to_base():
    plan = P.plan_years(F.plan_gl_actuals(), base_fy=2024, horizon_years=2, growth_rate=0.0)
    rev = plan[plan["account_number_group"] == REV]
    for fy in (2026, 2027):
        assert rev[rev["fiscal_year"] == fy]["amount"].sum() == pytest.approx(-3000.0)


def test_growth_rate_ten_percent_compounds():
    plan = P.plan_years(F.plan_gl_actuals(), base_fy=2024, horizon_years=3, growth_rate=0.10)
    rev = plan[plan["account_number_group"] == REV].groupby("fiscal_year")["amount"].sum()
    assert rev[2026] == pytest.approx(-3000 * 1.10 ** 1)
    assert rev[2027] == pytest.approx(-3000 * 1.10 ** 2)
    assert rev[2028] == pytest.approx(-3000 * 1.10 ** 3)


def test_group_growth_override_applied_per_pl_group():
    # default 5%, but material group gets 20%
    plan = P.plan_years(
        F.plan_gl_actuals(), base_fy=2024, horizon_years=1, growth_rate=0.05,
        group_growth={"Materialaufwand": 0.20}, group_col="pl_group",
    )
    rev = plan[(plan["account_number_group"] == REV) & (plan["fiscal_year"] == 2026)]
    mat = plan[(plan["account_number_group"] == MAT) & (plan["fiscal_year"] == 2026)]
    assert rev["amount"].sum() == pytest.approx(-3000 * 1.05)   # default
    assert mat["amount"].sum() == pytest.approx(500 * 1.20)     # overridden
    # material stays positive (debit)
    assert mat["amount"].sum() > 0


def test_plan_seasonality_sums_to_annual_per_year():
    plan = P.plan_years(F.plan_gl_actuals(), base_fy=2024, horizon_years=4, growth_rate=0.07)
    for (acct, fy), grp in plan.groupby(["account_number_group", "fiscal_year"]):
        base_annual = {REV: -3000.0, MAT: 500.0}[acct]
        k = fy - (2024 + 2) + 1
        expected = base_annual * 1.07 ** k
        assert grp["amount"].sum() == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# (4) Forecast of OPEN periods
# --------------------------------------------------------------------------- #
def test_forecast_fills_only_open_periods():
    fc = P.forecast_open_periods(
        F.plan_gl_actuals(), current_fy=2025, last_closed_period=2, prior_fy=2024,
    )
    rev = fc[fc["account_number_group"] == REV]
    # closed periods 1,2 are never emitted
    assert rev[rev["fiscal_period"] <= 2].empty
    # open periods 3..12 are present
    assert sorted(rev["fiscal_period"].unique()) == list(range(3, 13))
    assert (rev["scenario"] == "forecast").all()


def test_forecast_run_rate_projection_reconciles_full_year():
    # Build a base FY 2024 spread across 4 periods so open-period seasonality is non-zero.
    base = pd.DataFrame(
        [
            ("0177777", 2024, 1, -100.0),
            ("0177777", 2024, 2, -100.0),
            ("0177777", 2024, 3, -100.0),
            ("0177777", 2024, 4, -100.0),
            # current year, closed periods 1..2 at double the base run-rate
            ("0177777", 2025, 1, -200.0),
            ("0177777", 2025, 2, -200.0),
        ],
        columns=["account_number_group", "fiscal_year", "fiscal_period", "amount"],
    )
    fc = P.forecast_open_periods(base, current_fy=2025, last_closed_period=2, prior_fy=2024)
    ytd = -400.0
    # prior seasonal index: 1/4 each for p1..p4; coverage(closed 1..2) = 1/2
    # proj_annual = -400 / 0.5 = -800
    proj_annual = -800.0
    open_sum = fc["amount"].sum()
    assert ytd + open_sum == pytest.approx(proj_annual)
    # only periods 3 and 4 carry weight (prior had no p5..p12); they are negative
    p3 = fc[fc["fiscal_period"] == 3]["amount"].iloc[0]
    assert p3 == pytest.approx(-800.0 * 0.25)
    assert p3 < 0   # sign preserved (credit)


def test_forecast_no_prior_year_uniform_fallback():
    # No FY2024 actuals at all -> coverage 0 -> uniform pro-rata run-rate.
    cur = pd.DataFrame(
        [
            ("0166666", 2025, 1, -300.0),
            ("0166666", 2025, 2, -300.0),
        ],
        columns=["account_number_group", "fiscal_year", "fiscal_period", "amount"],
    )
    fc = P.forecast_open_periods(cur, current_fy=2025, last_closed_period=2, prior_fy=2024)
    # proj_annual = YTD * 12 / L = -600 * 12 / 2 = -3600; uniform 1/12 over open p3..12
    assert sorted(fc["fiscal_period"].unique()) == list(range(3, 13))
    per_period = -3600.0 / 12
    assert fc["amount"].iloc[0] == pytest.approx(per_period)
    # the 10 open periods sum to 10/12 of -3600
    assert fc["amount"].sum() == pytest.approx(-3600.0 * 10 / 12)


def test_forecast_nothing_open_when_year_closed():
    fc = P.forecast_open_periods(
        F.plan_gl_actuals(), current_fy=2025, last_closed_period=12, prior_fy=2024,
    )
    assert fc.empty


# --------------------------------------------------------------------------- #
# Sales plan
# --------------------------------------------------------------------------- #
def test_sales_plan_worked_example():
    sp = P.sales_plan(F.plan_sales_actuals(), base_fy=2024, horizon=4, growth_rate=0.05)
    fy26 = sp[(sp["customer_id"] == CUST) & (sp["fiscal_year"] == 2026)]
    assert fy26["gross_sales_plan"].sum() == pytest.approx(3150.0)   # 3000 * 1.05
    assert (fy26["gross_sales_plan"] >= 0).all()   # gross sales stays positive
    # period split: 1/3, 2/3 of 3150
    p1 = fy26[fy26["fiscal_period"] == 1]["gross_sales_plan"].iloc[0]
    p2 = fy26[fy26["fiscal_period"] == 2]["gross_sales_plan"].iloc[0]
    assert p1 == pytest.approx(1050.0)
    assert p2 == pytest.approx(2100.0)


def test_sales_plan_includes_forecast_when_current_year_given():
    sp = P.sales_plan(
        F.plan_sales_actuals(), base_fy=2024, horizon=4, growth_rate=0.05,
        current_fy=2025, last_closed_period=2, prior_fy=2024,
    )
    fc = sp[sp["scenario"] == "forecast"]
    assert not fc.empty
    assert (fc["fiscal_year"] == 2025).all()
    assert fc[fc["fiscal_period"] <= 2].empty   # only open periods


# --------------------------------------------------------------------------- #
# Orchestrator + edge cases
# --------------------------------------------------------------------------- #
def test_generate_plan_flags_synthetic_and_scenarios():
    gl, sales = P.generate_plan(
        F.plan_gl_actuals(), F.plan_sales_actuals(),
        base_fy=2024, current_fy=2025, last_closed_period=2,
        horizon_years=4, growth_rate=0.05, group_col="pl_group",
    )
    assert gl["is_synthetic"].all()
    assert sales["is_synthetic"].all()
    assert set(gl["scenario"]) == {"forecast", "plan"}
    # forecast -> 2025, plan -> 2026..2029
    assert set(gl[gl["scenario"] == "plan"]["fiscal_year"]) == {2026, 2027, 2028, 2029}
    assert set(gl[gl["scenario"] == "forecast"]["fiscal_year"]) == {2025}


def test_generate_plan_is_deterministic():
    args = dict(base_fy=2024, current_fy=2025, last_closed_period=2,
                horizon_years=4, growth_rate=0.05, group_col="pl_group")
    a_gl, a_s = P.generate_plan(F.plan_gl_actuals(), F.plan_sales_actuals(), **args)
    b_gl, b_s = P.generate_plan(F.plan_gl_actuals(), F.plan_sales_actuals(), **args)
    pd.testing.assert_frame_equal(a_gl, b_gl)
    pd.testing.assert_frame_equal(a_s, b_s)


def test_zero_base_yields_zero_plan_no_div_by_zero():
    df = pd.DataFrame(
        [("0188888", 2024, 1, 0.0), ("0188888", 2024, 2, 0.0)],
        columns=["account_number_group", "fiscal_year", "fiscal_period", "amount"],
    )
    plan = P.plan_years(df, base_fy=2024, horizon_years=2, growth_rate=0.05)
    acct = plan[plan["account_number_group"] == "0188888"]
    assert not acct.empty                      # account still present
    assert acct["amount"].abs().sum() == 0.0   # all zero, no NaN
    assert acct["amount"].notna().all()


def test_new_account_absent_from_base_is_excluded():
    # account only exists in FY2025 (not base FY2024) -> not in plan output
    plan = P.plan_years(F.plan_gl_actuals(), base_fy=2024, horizon_years=2, growth_rate=0.05)
    # 0180000 has FY2025 rows but ALSO FY2024 base rows -> included.
    # A truly new account with no base rows would be absent. Verify by adding one.
    df = pd.concat(
        [
            F.plan_gl_actuals(),
            pd.DataFrame(
                [("0155555", 2025, 1, -500.0)],
                columns=["account_number_group", "fiscal_year", "fiscal_period", "amount", "pl_group"][:4],
            ),
        ],
        ignore_index=True,
    )
    plan2 = P.plan_years(df, base_fy=2024, horizon_years=2, growth_rate=0.05)
    assert "0155555" not in set(plan2["account_number_group"])


def test_partial_stub_base_year_inherits_shape():
    # only period 3 booked in base -> all plan weight on period 3
    df = pd.DataFrame(
        [("0144444", 2024, 3, -900.0)],
        columns=["account_number_group", "fiscal_year", "fiscal_period", "amount"],
    )
    plan = P.plan_years(df, base_fy=2024, horizon_years=1, growth_rate=0.0)
    fy26 = plan[plan["account_number_group"] == "0144444"]
    assert fy26[fy26["fiscal_period"] == 3]["amount"].iloc[0] == pytest.approx(-900.0)
    assert fy26[fy26["fiscal_period"] != 3]["amount"].abs().sum() == 0.0


def test_credit_sign_preserved_through_growth():
    plan = P.plan_years(F.plan_gl_actuals(), base_fy=2024, horizon_years=4, growth_rate=0.05)
    rev = plan[plan["account_number_group"] == REV]
    # every non-zero revenue plan cell stays negative (credit)
    nonzero = rev[rev["amount"] != 0.0]
    assert (nonzero["amount"] < 0).all()
