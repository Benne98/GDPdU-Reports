"""Phase 4 financial regression: forecast/coverage from the SINGLE active plan version.

Locks decision 4 (docs/plans/v5-pipeline-rework.md) + docs/financial-logic.md
"Phase 4 — Versioned plans".  DB-FREE: the resolution layer
(plan_version.resolve_plan_scope) and the readers (load_position_plan_map_pref,
_apply_annual_fy_forecast, build_statement_plan_response) are exercised via
monkeypatch seams — this pins the PRODUCTION lines, not copies.

===================================================================== FORMULA
Forecast_FY = ytd_actual + ytg_plan, where ytg_plan is the ACTIVE version's
remaining-month plan (parked → ytd_actual only).  coverage_pct =
actual_cm / |plan_cm| * 100, None when |plan_cm| <= 1e-6.  Worked (L=6):
YTD 600 + active-version plan 540 → 1140; actual_cm 110 / plan_cm 100 → 110%.

Scope states (plan_version.resolve_plan_scope):
  legacy          — table absent → pre-Phase-4 path (byte-identical).
  active_included — active + include_in_reporting → read the version's plan.
  active_parked   — active + NOT include_in_reporting → forecast/coverage None.
  no_version      — table present, none active → forecast/coverage None.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest


# ===========================================================================
# resolve_plan_scope — the read gate
# ===========================================================================
class TestResolvePlanScope:
    def test_legacy_when_table_absent(self, monkeypatch):
        from app.services import plan_version

        monkeypatch.setattr(plan_version, "table_exists", lambda s: False)
        state, ver = plan_version.resolve_plan_scope(MagicMock(), "PL", 2025)
        assert state == plan_version.LEGACY
        assert ver is None

    def test_no_version_when_table_present_none_active(self, monkeypatch):
        from app.services import plan_version

        monkeypatch.setattr(plan_version, "table_exists", lambda s: True)
        monkeypatch.setattr(plan_version, "active_version", lambda *a, **k: None)
        state, ver = plan_version.resolve_plan_scope(MagicMock(), "PL", 2025)
        assert state == plan_version.NO_VERSION
        assert ver is None

    def test_active_included(self, monkeypatch):
        from app.services import plan_version

        v = plan_version.PlanVersion(1, None, "PL", 2025, "v1", True, True)
        monkeypatch.setattr(plan_version, "table_exists", lambda s: True)
        monkeypatch.setattr(plan_version, "active_version", lambda *a, **k: v)
        state, ver = plan_version.resolve_plan_scope(MagicMock(), "PL", 2025)
        assert state == plan_version.ACTIVE_INCLUDED
        assert ver is v

    def test_active_parked_when_not_included(self, monkeypatch):
        from app.services import plan_version

        v = plan_version.PlanVersion(2, None, "PL", 2025, "v2", True, False)
        monkeypatch.setattr(plan_version, "table_exists", lambda s: True)
        monkeypatch.setattr(plan_version, "active_version", lambda *a, **k: v)
        state, ver = plan_version.resolve_plan_scope(MagicMock(), "PL", 2025)
        assert state == plan_version.ACTIVE_PARKED

    def test_reporting_included_tristate(self, monkeypatch):
        from app.services import plan_version

        # legacy → None; included → True; parked → False.
        monkeypatch.setattr(plan_version, "table_exists", lambda s: False)
        assert plan_version.reporting_included(MagicMock(), "PL", 2025) is None

        v_inc = plan_version.PlanVersion(1, None, "PL", 2025, "v1", True, True)
        monkeypatch.setattr(plan_version, "table_exists", lambda s: True)
        monkeypatch.setattr(plan_version, "active_version", lambda *a, **k: v_inc)
        assert plan_version.reporting_included(MagicMock(), "PL", 2025) is True

        v_park = plan_version.PlanVersion(1, None, "PL", 2025, "v1", True, False)
        monkeypatch.setattr(plan_version, "active_version", lambda *a, **k: v_park)
        assert plan_version.reporting_included(MagicMock(), "PL", 2025) is False


# ===========================================================================
# load_position_plan_map_pref — version-aware resolution
# ===========================================================================
class TestLoadPositionPlanMapPref:
    def _patch_loader(self, monkeypatch, *, budget_map, forecast_map):
        """Canned load_position_plan_map keyed on the scenario kwarg."""
        from app.services import fin_compat_pl

        def _fake(session, statement, year, month, ent_frag, *, scenario="budget", prefixes=None):
            return forecast_map if scenario == "forecast" else budget_map

        monkeypatch.setattr(fin_compat_pl, "load_position_plan_map", _fake)

    def _patch_scope(self, monkeypatch, state):
        from app.services import fin_compat_pl, plan_version

        monkeypatch.setattr(
            plan_version, "resolve_plan_scope", lambda s, st, fy, **k: (state, None)
        )
        return fin_compat_pl

    def test_active_included_reads_budget_band(self, monkeypatch):
        from app.services import plan_version

        budget = {"NET_SALES": {"plan_cm": 100.0, "ytd_plan": 600.0, "ytg": 540.0}}
        forecast = {"NET_SALES": {"plan_cm": 999.0, "ytd_plan": 0.0, "ytg": 0.0}}
        self._patch_loader(monkeypatch, budget_map=budget, forecast_map=forecast)
        fc = self._patch_scope(monkeypatch, plan_version.ACTIVE_INCLUDED)
        out = fc.load_position_plan_map_pref(MagicMock(), "PL", 2025, 6, "")
        assert out == budget  # the active version's budget band, NOT the forecast band

    def test_active_parked_returns_empty(self, monkeypatch):
        from app.services import plan_version

        budget = {"NET_SALES": {"plan_cm": 100.0, "ytd_plan": 600.0, "ytg": 540.0}}
        self._patch_loader(monkeypatch, budget_map=budget, forecast_map=budget)
        fc = self._patch_scope(monkeypatch, plan_version.ACTIVE_PARKED)
        assert fc.load_position_plan_map_pref(MagicMock(), "PL", 2025, 6, "") == {}

    def test_no_version_returns_empty(self, monkeypatch):
        from app.services import plan_version

        budget = {"NET_SALES": {"plan_cm": 100.0, "ytd_plan": 600.0, "ytg": 540.0}}
        self._patch_loader(monkeypatch, budget_map=budget, forecast_map=budget)
        fc = self._patch_scope(monkeypatch, plan_version.NO_VERSION)
        assert fc.load_position_plan_map_pref(MagicMock(), "PL", 2025, 6, "") == {}

    def test_legacy_prefers_forecast_then_budget(self, monkeypatch):
        """Un-migrated DB → pre-Phase-4 forecast→budget resolution VERBATIM."""
        from app.services import plan_version

        budget = {"NET_SALES": {"plan_cm": 100.0, "ytd_plan": 600.0, "ytg": 540.0}}
        forecast = {"NET_SALES": {"plan_cm": 77.0, "ytd_plan": 0.0, "ytg": 0.0}}
        # forecast has signal → legacy returns the forecast band.
        self._patch_loader(monkeypatch, budget_map=budget, forecast_map=forecast)
        fc = self._patch_scope(monkeypatch, plan_version.LEGACY)
        assert fc.load_position_plan_map_pref(MagicMock(), "PL", 2025, 6, "") == forecast

    def test_legacy_falls_back_to_budget_when_no_forecast(self, monkeypatch):
        from app.services import plan_version

        budget = {"NET_SALES": {"plan_cm": 100.0, "ytd_plan": 600.0, "ytg": 540.0}}
        self._patch_loader(monkeypatch, budget_map=budget, forecast_map={})
        fc = self._patch_scope(monkeypatch, plan_version.LEGACY)
        assert fc.load_position_plan_map_pref(MagicMock(), "PL", 2025, 6, "") == budget


# ===========================================================================
# Forecast_FY = ytd ⊕ active-version ytg  (worked example + parked edge)
# ===========================================================================
class TestForecastFromActiveVersion:
    def test_worked_example_ytd_600_plus_active_ytg_540(self):
        from app.services.fin_compat_pl import _apply_annual_fy_forecast

        line_vals = {"NET_SALES": {"ytd": 600.0}}
        active_plan = {"NET_SALES": {"ytg": 540.0}}   # active version's remaining months
        _apply_annual_fy_forecast(line_vals, active_plan)
        assert line_vals["NET_SALES"]["fy_f"] == pytest.approx(1140.0)

    def test_parked_collapses_to_ytd(self):
        """active_parked / no_version → empty plan_map → fy_f == ytd (not stale)."""
        from app.services.fin_compat_pl import _apply_annual_fy_forecast

        line_vals = {"NET_SALES": {"ytd": 600.0}}
        _apply_annual_fy_forecast(line_vals, {})   # parked passes {}
        assert line_vals["NET_SALES"]["fy_f"] == pytest.approx(600.0)

    def test_anchor_month_12_forecast_equals_ytd(self):
        """L=12: no open period → active version has ytg 0 → fy_f == full-year actual."""
        from app.services.fin_compat_pl import _apply_annual_fy_forecast

        line_vals = {"NET_SALES": {"ytd": 1200.0}}
        _apply_annual_fy_forecast(line_vals, {"NET_SALES": {"ytg": 0.0}})
        assert line_vals["NET_SALES"]["fy_f"] == pytest.approx(1200.0)


# ===========================================================================
# coverage_pct through build_statement_plan_response (active version plan_cm)
# ===========================================================================
def _drive_plan_response(monkeypatch, *, scope_state, plan_map, actuals, codes, statement="BS"):
    from app.services import fin_compat_pl, plan_version

    struct = [
        {"line_code": c, "row_type": "mapping", "sort_order": i * 10, "kpi_code": None,
         "level_2": None, "level_3": c, "level_4": None, "balance_title": c,
         "gl_account_id": None}
        for i, c in enumerate(codes, start=1)
    ]
    monkeypatch.setattr(fin_compat_pl, "_statement_structure_rows", lambda s, st: struct)
    monkeypatch.setattr(fin_compat_pl, "_statement_actual_running",
                        lambda s, st, y, m, ef, struct: actuals)
    monkeypatch.setattr(fin_compat_pl, "resolve_entity_prefix", lambda s, e: None)
    monkeypatch.setattr(fin_compat_pl, "entity_sql_fragment", lambda ep: "")
    # Drive the version scope + canned budget band through the version-aware pref.
    monkeypatch.setattr(plan_version, "resolve_plan_scope",
                        lambda s, st, fy, **k: (scope_state, None))

    def _fake_map(session, statement, year, month, ent_frag, *, scenario="budget", prefixes=None):
        return plan_map

    monkeypatch.setattr(fin_compat_pl, "load_position_plan_map", _fake_map)
    # Guard: a migrated + parked PL must NOT fall back to the legacy _load_plan_map.
    monkeypatch.setattr(
        fin_compat_pl, "_load_plan_map",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("_load_plan_map must not run when parked")),
    )
    return fin_compat_pl.build_statement_plan_response(MagicMock(), statement, 2025, 6, None)


def _line(out, code):
    return next(l for l in out["lines"] if l["line_code"] == code)


class TestCoverageFromActiveVersion:
    def test_active_included_coverage(self, monkeypatch):
        from app.services import plan_version

        out = _drive_plan_response(
            monkeypatch, scope_state=plan_version.ACTIVE_INCLUDED,
            plan_map={"AR": {"plan_cm": 100.0, "ytd_plan": 100.0, "ytg": 0.0}},
            actuals={"AR": 110.0}, codes=["AR"],
        )
        assert out["has_plan_data"] is True
        line = _line(out, "AR")
        assert line["coverage_pct"] == pytest.approx(110.0)
        assert line["plan_vs_actual"] == pytest.approx(10.0)

    def test_parked_coverage_is_none(self, monkeypatch):
        from app.services import plan_version

        out = _drive_plan_response(
            monkeypatch, scope_state=plan_version.ACTIVE_PARKED,
            plan_map={"AR": {"plan_cm": 100.0, "ytd_plan": 100.0, "ytg": 0.0}},
            actuals={"AR": 110.0}, codes=["AR"],
        )
        assert out["has_plan_data"] is False
        assert _line(out, "AR")["coverage_pct"] is None
        # Parked → plan_cm 0 → plan_vs_actual == actual (no stale plan influence).
        assert _line(out, "AR")["plan_vs_actual"] == pytest.approx(110.0)

    def test_no_version_coverage_is_none(self, monkeypatch):
        from app.services import plan_version

        out = _drive_plan_response(
            monkeypatch, scope_state=plan_version.NO_VERSION,
            plan_map={"AR": {"plan_cm": 100.0, "ytd_plan": 100.0, "ytg": 0.0}},
            actuals={"AR": 110.0}, codes=["AR"],
        )
        assert out["has_plan_data"] is False
        assert _line(out, "AR")["coverage_pct"] is None

    def test_pl_parked_does_not_fall_back_to_gl_plan(self, monkeypatch):
        """A migrated + parked PL parks the columns — it must NOT read _load_plan_map
        (the legacy fact_gl_plan forecast/plan band).  The _load_plan_map guard in
        _drive_plan_response raises if that fallback runs."""
        from app.services import plan_version

        out = _drive_plan_response(
            monkeypatch, statement="PL", scope_state=plan_version.ACTIVE_PARKED,
            plan_map={},   # pref returns {} when parked
            actuals={"NET_SALES": 900.0}, codes=["NET_SALES"],
        )
        assert out["has_plan_data"] is False
        assert _line(out, "NET_SALES")["coverage_pct"] is None
        assert _line(out, "NET_SALES")["plan_vs_actual"] == pytest.approx(900.0)

    def test_pl_active_included_reads_version_plan(self, monkeypatch):
        from app.services import plan_version

        out = _drive_plan_response(
            monkeypatch, statement="PL", scope_state=plan_version.ACTIVE_INCLUDED,
            plan_map={"NET_SALES": {"plan_cm": 800.0, "ytd_plan": 800.0, "ytg": 0.0}},
            actuals={"NET_SALES": 900.0}, codes=["NET_SALES"],
        )
        assert out["has_plan_data"] is True
        assert _line(out, "NET_SALES")["coverage_pct"] == pytest.approx(112.5)
