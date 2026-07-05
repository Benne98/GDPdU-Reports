"""Financial sign-off regression: Coverage % + Forecast (actuals ⊕ plan).

This file LOCKS two already-shipped deterministic metrics against silent drift.
It changes NO production formula — it only pins their documented behaviour.

===================================================================== FORMULA 1
Coverage %  (backend/app/services/fin_compat_pl.py :: build_statement_plan_response)

    coverage_pct = round(actual_cm / abs(plan_cm) * 100, 2)   if abs(plan_cm) > 1e-6
                 = None                                        otherwise

  - Per ``line_code``; CURRENT-MONTH actual (``actual_cm``) vs current-month plan
    (``plan_cm``).
  - Denominator is ``abs(plan_cm)`` → the SIGN of ``actual_cm`` is preserved in the
    ratio (a negative actual over a negative plan yields a NEGATIVE coverage — the
    metric is "how much of |plan| did signed actual reach", NOT an absolute ratio).
  - None-guard when ``|plan_cm| <= 1e-6`` (no divide-by-zero, no misleading ∞).
  - IDENTICAL across PL / BS / WC / CF: all four route through
    build_statement_plan_response; WC maps to the BS branch (``effstmt``), so the
    coverage arithmetic is one code path, not four.

  Worked example: actual 500, plan 450 → 500/|450|*100 = 111.11 (2 dp).

===================================================================== FORMULA 2
Forecast = actuals ⊕ plan  (the load-bearing stitching rule)

    Forecast_FY = Σ_{p ≤ L} Actual(p) + Σ_{p > L} Plan(p) = ytd + ytg
                  where L = last CLOSED fiscal period.

  Implemented as:
    - backend/app/services/fin_compat_pl.py :: _apply_annual_fy_forecast
        fy_f = round(ytd + ytg, 2)   when ANY line has a YTG plan
             = round(ytd, 2)         otherwise   (← golden-safety: byte-identical
                                                   to the pre-plan world)
    - etl/plan_synth.py :: forecast_open_periods
        CLOSED periods (p ≤ L) are NEVER emitted (they stay actuals); only OPEN
        periods (p > L) get a projected plan value. Full year closed (L ≥ 12) →
        empty frame ⇒ forecast == actuals.
    - backend/app/services/fin_compat_bs.py :: _apply_snapshot_fy_forecast_amounts
        BS is a STOCK: fy_f defaults to the current month-end closing balance
        (``cm``) and equity/asset totals are bumped by the plan YTG net profit —
        not tested here (BS snapshot needs a session); the flow rule above is the
        one this file locks.

  Worked example (L=6): YTD actuals 600 + remaining-6-months plan 540 → 1140.

Edge cases locked below:
  * plan_cm = 0            → coverage None (no div-by-zero).
  * negative plan / cost   → coverage uses abs(plan) denominator, actual sign kept.
  * WC == BS == PL == CF   → one coverage code path.
  * no plan rows (ytg = 0) → forecast == ytd, byte-identical (GOLDEN-SAFETY).
  * L = 12 (year closed)   → no plan emitted → forecast == actual.
  * sign conventions       → revenue (+) and cost (−) lines both preserved.

Strategy: DB-free.  The pure helpers (_apply_annual_fy_forecast,
plan_synth.forecast_open_periods) are imported directly.  Coverage is exercised
through the real build_statement_plan_response via the monkeypatch seam already
used by test_plan_overlay.py — this locks the PRODUCTION line, not a copy of it.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pandas as pd
import pytest


# ===========================================================================
# FORMULA 1 — Coverage %  (via production build_statement_plan_response)
# ===========================================================================

def _call_plan(
    monkeypatch,
    statement: str,
    plan_map: dict[str, dict[str, float]],
    actuals: dict[str, float],
    struct_codes: list[str],
) -> dict[str, Any]:
    """Drive the real build_statement_plan_response with canned plan + actuals.

    Mirrors the seam in test_plan_overlay.py: patch structure / plan / actual
    loaders and entity resolution so the DB is never touched, but the coverage
    arithmetic (production L~1206) runs verbatim.
    """
    from app.services import fin_compat_pl

    _STRUCT: list[dict[str, Any]] = [
        {
            "line_code": code, "row_type": "mapping", "sort_order": i * 10,
            "kpi_code": None, "level_2": None, "level_3": code, "level_4": None,
            "balance_title": code, "gl_account_id": None,
        }
        for i, code in enumerate(struct_codes, start=1)
    ]
    monkeypatch.setattr(fin_compat_pl, "_statement_structure_rows", lambda s, st: _STRUCT)
    # PL branch loader:
    monkeypatch.setattr(fin_compat_pl, "_load_plan_map", lambda s, y, m, ef: plan_map)
    # BS / WC / CF branch loader:
    monkeypatch.setattr(fin_compat_pl, "load_position_plan_map",
                        lambda s, st, y, m, ef, **kw: plan_map)
    monkeypatch.setattr(fin_compat_pl, "_statement_actual_running",
                        lambda s, st, y, m, ef, struct: actuals)
    monkeypatch.setattr(fin_compat_pl, "resolve_entity_prefix", lambda s, e: None)
    monkeypatch.setattr(fin_compat_pl, "entity_sql_fragment", lambda ep: "")

    return fin_compat_pl.build_statement_plan_response(
        MagicMock(), statement, 2025, 6, None
    )


def _line(out: dict[str, Any], code: str) -> dict[str, Any]:
    return next(l for l in out["lines"] if l["line_code"] == code)


class TestCoveragePct:
    """coverage_pct = round(actual_cm / abs(plan_cm) * 100, 2), None-guarded."""

    def test_worked_example_actual_500_plan_450(self, monkeypatch):
        """actual 500, plan 450 → 500/450*100 = 111.11 (2 dp)."""
        out = _call_plan(
            monkeypatch, "PL",
            {"NET_SALES": {"plan_cm": 450.0, "ytd_plan": 450.0, "ytg": 0.0}},
            {"NET_SALES": 500.0}, ["NET_SALES"],
        )
        assert out["has_plan_data"] is True
        assert _line(out, "NET_SALES")["coverage_pct"] == pytest.approx(111.11, abs=1e-9)

    def test_over_delivery_above_100(self, monkeypatch):
        """actual 1200, plan 1000 → 120.00 (coverage can exceed 100)."""
        out = _call_plan(
            monkeypatch, "PL",
            {"NET_SALES": {"plan_cm": 1000.0, "ytd_plan": 1000.0, "ytg": 0.0}},
            {"NET_SALES": 1200.0}, ["NET_SALES"],
        )
        assert _line(out, "NET_SALES")["coverage_pct"] == pytest.approx(120.0)

    def test_plan_zero_gives_none_coverage(self, monkeypatch):
        """|plan_cm| <= 1e-6 → coverage_pct is None (no divide-by-zero)."""
        out = _call_plan(
            monkeypatch, "PL",
            {"NET_SALES": {"plan_cm": 0.0, "ytd_plan": 0.0, "ytg": 0.0}},
            {"NET_SALES": 500.0}, ["NET_SALES"],
        )
        assert _line(out, "NET_SALES")["coverage_pct"] is None

    def test_plan_below_epsilon_gives_none(self, monkeypatch):
        """A sub-epsilon plan (5e-7 < 1e-6) also trips the None-guard."""
        out = _call_plan(
            monkeypatch, "PL",
            {"NET_SALES": {"plan_cm": 5e-7, "ytd_plan": 0.0, "ytg": 0.0}},
            {"NET_SALES": 500.0}, ["NET_SALES"],
        )
        assert _line(out, "NET_SALES")["coverage_pct"] is None

    def test_negative_cost_line_uses_abs_denominator(self, monkeypatch):
        """Cost line plan -500, actual -450 → -450/500*100 = -90.0.

        Denominator is abs(plan); the actual's sign is preserved so an
        under-spent cost line reads as a NEGATIVE coverage (documented, not a bug).
        """
        out = _call_plan(
            monkeypatch, "PL",
            {"COGS": {"plan_cm": -500.0, "ytd_plan": -500.0, "ytg": 0.0}},
            {"COGS": -450.0}, ["COGS"],
        )
        line = _line(out, "COGS")
        assert line["coverage_pct"] == pytest.approx(-90.0)
        # plan_vs_actual = actual - plan = -450 - (-500) = +50 (beat the cost plan)
        assert line["plan_vs_actual"] == pytest.approx(50.0)

    def test_coverage_identical_across_pl_bs_wc_cf(self, monkeypatch):
        """Same (actual, plan) → identical coverage for PL, BS, WC, CF.

        Locks the "one code path" invariant: WC folds into the BS branch, and all
        four statements compute coverage in build_statement_plan_response.
        """
        plan = {"X": {"plan_cm": 450.0, "ytd_plan": 450.0, "ytg": 0.0}}
        act = {"X": 500.0}
        cov = {}
        for stmt in ("PL", "BS", "WC", "CF"):
            out = _call_plan(monkeypatch, stmt, plan, act, ["X"])
            cov[stmt] = _line(out, "X")["coverage_pct"]
        assert cov["PL"] == cov["BS"] == cov["WC"] == cov["CF"] == pytest.approx(111.11)


# ===========================================================================
# FORMULA 2a — Forecast stitching, PL flow (_apply_annual_fy_forecast, pure)
# ===========================================================================

class TestApplyAnnualFyForecast:
    """fy_f = ytd + ytg (any plan) else ytd (golden-safety), signs preserved."""

    def test_worked_example_ytd_600_ytg_540(self):
        """L=6: YTD 600 + remaining plan 540 → fy_f 1140."""
        from app.services.fin_compat_pl import _apply_annual_fy_forecast

        line_vals = {"NET_SALES": {"ytd": 600.0}}
        plan_map = {"NET_SALES": {"ytg": 540.0}}
        _apply_annual_fy_forecast(line_vals, plan_map)
        assert line_vals["NET_SALES"]["fy_f"] == pytest.approx(1140.0)

    def test_no_plan_is_byte_identical_to_ytd(self):
        """GOLDEN-SAFETY: empty plan_map → fy_f == ytd (no plan influence)."""
        from app.services.fin_compat_pl import _apply_annual_fy_forecast

        line_vals = {"NET_SALES": {"ytd": 600.0}, "COGS": {"ytd": -220.0}}
        _apply_annual_fy_forecast(line_vals, {})
        assert line_vals["NET_SALES"]["fy_f"] == pytest.approx(600.0)
        assert line_vals["COGS"]["fy_f"] == pytest.approx(-220.0)

    def test_all_ytg_zero_treated_as_no_plan(self):
        """A plan_map whose every YTG is 0 → has_ytg_plan False → fy_f == ytd."""
        from app.services.fin_compat_pl import _apply_annual_fy_forecast

        line_vals = {"NET_SALES": {"ytd": 600.0}}
        plan_map = {"NET_SALES": {"ytg": 0.0}}
        _apply_annual_fy_forecast(line_vals, plan_map)
        assert line_vals["NET_SALES"]["fy_f"] == pytest.approx(600.0)

    def test_L12_year_closed_forecast_equals_actual(self):
        """L=12: no open periods ⇒ no YTG plan rows ⇒ fy_f == ytd == full-year actual."""
        from app.services.fin_compat_pl import _apply_annual_fy_forecast

        # Whole year is actual; plan_map carries no YTG (nothing left to forecast).
        line_vals = {"NET_SALES": {"ytd": 1200.0}}
        _apply_annual_fy_forecast(line_vals, {"NET_SALES": {"ytg": 0.0}})
        assert line_vals["NET_SALES"]["fy_f"] == pytest.approx(1200.0)

    def test_sign_conventions_preserved(self):
        """Revenue (+) grows, cost (−) stays negative when both carry a YTG plan."""
        from app.services.fin_compat_pl import _apply_annual_fy_forecast

        line_vals = {"REVENUE": {"ytd": 600.0}, "COGS": {"ytd": -300.0}}
        plan_map = {"REVENUE": {"ytg": 540.0}, "COGS": {"ytg": -260.0}}
        _apply_annual_fy_forecast(line_vals, plan_map)
        assert line_vals["REVENUE"]["fy_f"] == pytest.approx(1140.0)   # +
        assert line_vals["COGS"]["fy_f"] == pytest.approx(-560.0)      # − preserved

    def test_global_flag_mixed_lines(self):
        """has_ytg_plan is global: a line with ytg=0 alongside a planned line

        still uses ytd + ytg (its own ytg is 0 → fy_f == ytd), while the planned
        line gets ytd + ytg. Locks the global-flag semantics.
        """
        from app.services.fin_compat_pl import _apply_annual_fy_forecast

        line_vals = {"REVENUE": {"ytd": 600.0}, "OTHER": {"ytd": 100.0}}
        plan_map = {"REVENUE": {"ytg": 540.0}, "OTHER": {"ytg": 0.0}}
        _apply_annual_fy_forecast(line_vals, plan_map)
        assert line_vals["REVENUE"]["fy_f"] == pytest.approx(1140.0)
        assert line_vals["OTHER"]["fy_f"] == pytest.approx(100.0)  # ytd + 0


# ===========================================================================
# FORMULA 2b — Forecast stitching, ETL projection (plan_synth, pure DataFrame)
# ===========================================================================

def _uniform_actuals(fy: int, periods: range, per_period: float) -> pd.DataFrame:
    """One synthetic account booked ``per_period`` in each listed period of ``fy``."""
    return pd.DataFrame(
        {
            "account_number_group": ["0180000"] * len(periods),
            "fiscal_year": [fy] * len(periods),
            "fiscal_period": list(periods),
            "amount": [per_period] * len(periods),
        }
    )


class TestPlanSynthForecastStitching:
    """CLOSED periods stay actuals; only OPEN periods (p > L) get a plan value."""

    def test_closed_periods_never_emitted(self):
        """L=6 → forecast rows exist ONLY for periods 7..12, never 1..6."""
        from etl.plan_synth import forecast_open_periods, SCENARIO_FORECAST

        prior = _uniform_actuals(2024, range(1, 13), 100.0)     # uniform seasonality
        current = _uniform_actuals(2025, range(1, 7), 100.0)    # YTD = 600 over p1..6
        actuals = pd.concat([prior, current], ignore_index=True)

        out = forecast_open_periods(actuals, current_fy=2025,
                                    last_closed_period=6, prior_fy=2024)
        emitted = sorted(out["fiscal_period"].tolist())
        assert emitted == [7, 8, 9, 10, 11, 12]
        assert set(out["scenario"]) == {SCENARIO_FORECAST}
        assert (out["fiscal_period"] > 6).all()

    def test_full_year_reconciles_to_run_rate(self):
        """ytd + Σ_{p>L} forecast(p) == ProjAnnual (Forecast_FY reconciliation).

        Uniform prior seasonality ⇒ coverage = 6/12 = 0.5, ProjAnnual =
        YTD/coverage = 600/0.5 = 1200, so the 6 open months carry 600 total.
        """
        from etl.plan_synth import forecast_open_periods

        prior = _uniform_actuals(2024, range(1, 13), 100.0)
        current = _uniform_actuals(2025, range(1, 7), 100.0)
        actuals = pd.concat([prior, current], ignore_index=True)

        out = forecast_open_periods(actuals, current_fy=2025,
                                    last_closed_period=6, prior_fy=2024)
        ytd = 600.0
        ytg = float(out["amount"].sum())
        assert ytg == pytest.approx(600.0)
        assert ytd + ytg == pytest.approx(1200.0)   # full-year run-rate projection

    def test_full_year_closed_emits_nothing(self):
        """L=12 (year closed) → empty frame ⇒ forecast == actuals (golden-safe)."""
        from etl.plan_synth import forecast_open_periods

        prior = _uniform_actuals(2024, range(1, 13), 100.0)
        current = _uniform_actuals(2025, range(1, 13), 100.0)
        actuals = pd.concat([prior, current], ignore_index=True)

        out = forecast_open_periods(actuals, current_fy=2025,
                                    last_closed_period=12, prior_fy=2024)
        assert out.empty
        # Columns preserved so downstream concat is byte-stable.
        assert list(out.columns) == [
            "account_number_group", "fiscal_year", "fiscal_period",
            "scenario", "amount", "is_synthetic", "source_system",
        ]

    def test_sign_preserved_for_credit_revenue(self):
        """Credit revenue (−) forecasts stay negative (sign never flips)."""
        from etl.plan_synth import forecast_open_periods

        prior = _uniform_actuals(2024, range(1, 13), -100.0)   # credit revenue
        current = _uniform_actuals(2025, range(1, 7), -100.0)  # YTD = -600
        actuals = pd.concat([prior, current], ignore_index=True)

        out = forecast_open_periods(actuals, current_fy=2025,
                                    last_closed_period=6, prior_fy=2024)
        assert (out["amount"] < 0).all()
        assert float(out["amount"].sum()) == pytest.approx(-600.0)
