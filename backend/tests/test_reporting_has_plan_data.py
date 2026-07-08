"""Phase 4 extension — the annual/snapshot builders expose ``has_plan_data``.

The Forecast (fy_f) + Coverage columns must render ONLY when a real active +
include_in_reporting plan version supplied plan values.  Each annual builder now
returns a top-level ``has_plan_data`` bool that drives DISPLAY (the fy_f numerics
are unchanged).  This pins the definition per builder:

  * PL annual (build_pl_annual_compat)  → bool(plan_map): parked → {} (False),
    no plan rows → {} (False), active+included/legacy with rows → non-empty (True).
  * CF annual (build_cf_annual_compat)  → bool(load_position_plan_map_pref("CF")):
    the version-gated reader returns {} for parked/no-version/no-rows.

DB-FREE: production builders are driven through monkeypatch seams (no Postgres).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ===========================================================================
# PL annual — has_plan_data == bool(plan_map) (already version-gated inside)
# ===========================================================================
def _drive_pl_annual(monkeypatch, *, included, plan_map):
    from app.services import fin_compat_pl, plan_version

    monkeypatch.setattr(fin_compat_pl, "resolve_entity_prefix", lambda s, e: None)
    monkeypatch.setattr(fin_compat_pl, "entity_sql_fragment", lambda ep: "")
    monkeypatch.setattr(fin_compat_pl, "pl_grain_sql_annual", lambda y, m, ef: ("", {}))
    monkeypatch.setattr(fin_compat_pl, "_load_structure", lambda s: [object()])
    monkeypatch.setattr(
        fin_compat_pl, "_build_annual_rows",
        lambda *a, **k: {"statement": "pl", "rows": []},
    )
    monkeypatch.setattr(plan_version, "reporting_included", lambda s, st, y: included)
    monkeypatch.setattr(fin_compat_pl, "_load_plan_map", lambda *a, **k: plan_map)

    session = MagicMock()
    session.execute.return_value.fetchall.return_value = []
    return fin_compat_pl.build_pl_annual_compat(session, year=2025, month=6)


class TestPlAnnualHasPlanData:
    def test_parked_no_plan_data(self, monkeypatch):
        # included False (parked / no active version) → plan_map forced to {}.
        out = _drive_pl_annual(monkeypatch, included=False, plan_map={"X": {"ytg": 5.0}})
        assert out["has_plan_data"] is False

    def test_included_but_no_rows_no_plan_data(self, monkeypatch):
        # active+included but the plan store has no signal → _load_plan_map returns {}.
        out = _drive_pl_annual(monkeypatch, included=True, plan_map={})
        assert out["has_plan_data"] is False

    def test_included_with_rows_has_plan_data(self, monkeypatch):
        out = _drive_pl_annual(
            monkeypatch, included=True,
            plan_map={"NET_SALES": {"plan_cm": 100.0, "ytd_plan": 600.0, "ytg": 540.0}},
        )
        assert out["has_plan_data"] is True

    def test_legacy_with_rows_has_plan_data(self, monkeypatch):
        # included None (un-migrated DB) → _load_plan_map result drives the flag.
        out = _drive_pl_annual(
            monkeypatch, included=None,
            plan_map={"NET_SALES": {"plan_cm": 100.0, "ytg": 540.0}},
        )
        assert out["has_plan_data"] is True


# ===========================================================================
# CF annual — has_plan_data == bool(load_position_plan_map_pref("CF"))
# ===========================================================================
def _drive_cf_annual(monkeypatch, *, plan_map):
    from app.services import fin_compat_cf

    monkeypatch.setattr(fin_compat_cf, "resolve_entity_prefix", lambda s, e: None)
    monkeypatch.setattr(fin_compat_cf, "entity_sql_fragment", lambda ep: "")
    monkeypatch.setattr(fin_compat_cf, "cf_grain_sql_annual", lambda y, m, ef: ("", {}))
    monkeypatch.setattr(fin_compat_cf, "_cf_struct_rows", lambda s: [])
    monkeypatch.setattr(fin_compat_cf, "_build_cf_rows", lambda *a, **k: [])
    monkeypatch.setattr(
        fin_compat_cf, "load_position_plan_map_pref", lambda *a, **k: plan_map
    )

    session = MagicMock()
    session.execute.return_value.fetchall.return_value = []
    return fin_compat_cf.build_cf_annual_compat(session, year=2025, month=6)


class TestCfAnnualHasPlanData:
    def test_no_plan_map_no_plan_data(self, monkeypatch):
        # parked / no-version / no-rows → pref returns {} → flag False.
        out = _drive_cf_annual(monkeypatch, plan_map={})
        assert out["has_plan_data"] is False

    def test_plan_map_has_plan_data(self, monkeypatch):
        out = _drive_cf_annual(
            monkeypatch,
            plan_map={"CF_TRADE_RECEIVABLES": {"plan_cm": 10.0, "ytg": 5.0}},
        )
        assert out["has_plan_data"] is True
