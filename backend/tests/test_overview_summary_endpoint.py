"""Overview v2 — batched summary endpoint + fail-closed tenant isolation (P3).

Covers ``GET /api/v1/financials/overview/summary`` and the assembly function
``app.services.overview_summary.build_overview_summary``.

DB-FREE: the heavy sub-builders are monkeypatched so we OBSERVE the
``allowed_entities`` (entity_prefix) filter each one receives — this is the
test-visible seam the security review relies on — and never touch real data.

Security contract (must hold):
  * admin (visible_entity_codes → None)      → every sub-query gets allowed_entities=None.
  * restricted user (codes → prefixes)       → every sub-query gets that prefix set.
  * restricted user, ZERO visibility         → a ZEROED summary (no cross-entity
                                               data, no 500); NO sub-builder runs.
  * ``visible_entity_codes`` IS invoked and the mapped prefix set reaches the
    sub-queries.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import app.services.overview_summary as osum
from app.auth import User, current_user
from app.db import get_read_session, get_session
from app.main import app
from app.services.fin_compat_wc_sql import WC_INV_L3, WC_PAY_L3, WC_REC_L3

_ADMIN = User(user_id=1, email="admin@finssentials.com", display_name="Admin", is_admin=True)
_RESTRICTED = User(user_id=2, email="user@finssentials.com", display_name="User", is_admin=False)


# ---------------------------------------------------------------------------
# Row + session doubles
# ---------------------------------------------------------------------------
class _Row:
    def __init__(self, mapping: dict):
        self._mapping = dict(mapping)

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self._mapping.values())[key]
        return self._mapping[key]

    def __iter__(self):
        return iter(self._mapping.values())


def _make_session(
    visibility_codes: list[str] | None = None,
    code_to_prefix: dict[str, str] | None = None,
) -> MagicMock:
    """Session double: visibility → codes; dim_legal_entity → prefixes; cash → canned."""
    code_to_prefix = code_to_prefix or {}
    session = MagicMock()

    def _execute(stmt, params=None):
        sql = str(stmt)
        result = MagicMock()
        if "admin_role_entity_visibility" in sql:
            rows = [_Row({"legal_entity_code": c}) for c in (visibility_codes or [])]
        elif "dim_legal_entity" in sql:
            if params and "e" in params:  # resolve_entity_prefix single lookup
                c = params["e"]
                rows = ([_Row({"entity_prefix": code_to_prefix[c]})]
                        if c in code_to_prefix else [])
            else:  # map_codes_to_prefixes ANY(:codes)
                codes = (params or {}).get("codes", [])
                rows = [_Row({"entity_prefix": code_to_prefix[c]})
                        for c in codes if c in code_to_prefix]
        elif "Cash & cash equivalents" in sql:
            rows = [_Row({"cash_cm": 500.0, "cash_pm": 400.0, "cash_py": 300.0})]
        else:
            rows = []
        result.fetchall.return_value = rows
        result.fetchone.return_value = rows[0] if rows else None
        return result

    session.execute.side_effect = _execute
    return session


# ---------------------------------------------------------------------------
# Sub-builder fakes that RECORD the allowed_entities they receive
# ---------------------------------------------------------------------------
def _install_fakes(monkeypatch) -> dict:
    seen: dict[str, object] = {}

    def _ebit(session, entity, year, month, *, allowed_entities=None):
        seen["ebit"] = allowed_entities
        return {"rows": [{"entity_code": "__total__", "to_cm": 1000.0,
                          "to_cm_py": 800.0, "to_ytd": 5000.0,
                          "ebit_cm": 90.0, "ebit_ytd": 450.0}]}

    def _wc_stmt(session, *, period_grain="month", year=None, month=None,
                 allowed_entities=None, **kw):
        seen["wc_stmt"] = allowed_entities
        return {"rows": [
            {"line_code": "WC_DSO", "amounts": {"cm": 91.2}, "children": []},
            {"line_code": "WC_DPO", "amounts": {"cm": 80.0}, "children": []},
            {"line_code": "WC_DIO", "amounts": {"cm": 121.7}, "children": []},
            {"line_code": "WC_CCC", "amounts": {"cm": 132.9}, "children": []},
            {"line_code": "NWC", "amounts": {"cm": 6500.0}, "children": []},
            {"line_code": "x", "drill": {"level_3": WC_INV_L3},
             "amounts": {"cm": 4000.0, "pm": 3800.0}, "children": []},
            {"line_code": "x", "drill": {"level_3": WC_REC_L3},
             "amounts": {"cm": 5000.0, "pm": 4900.0}, "children": []},
            {"line_code": "x", "drill": {"level_3": WC_PAY_L3},
             "amounts": {"cm": -3000.0, "pm": -2900.0}, "children": []},
        ]}

    def _wc_snap(session, *, year=None, month=None, allowed_entities=None, **kw):
        seen["wc_snap"] = allowed_entities
        return {"rows": [
            {"drill": {"level_3": WC_INV_L3}, "deltas": {"delta_fy": 200.0}, "children": []},
            {"drill": {"level_3": WC_REC_L3}, "deltas": {"delta_fy": 150.0}, "children": []},
            {"drill": {"level_3": WC_PAY_L3}, "deltas": {"delta_fy": -50.0}, "children": []},
        ]}

    def _top(session, *, year, month, type="customer", allowed_entities=None, **kw):
        seen[f"top_{type}"] = allowed_entities
        return {"rows": [{"rank": 1, "name": f"{type}-A", "cm": 500.0,
                          "delta_cm_py": 100.0, "delta_ytd": 300.0}]}

    def _dupont(session, entity, year, month, *, allowed_entities=None):
        seen["dupont"] = allowed_entities
        return {"metrics": {"roe": {"value": 11.25}}}

    def _alerts(session, *, entity, year, month, allowed_entities=None, **kw):
        seen["alerts"] = allowed_entities
        return [{"entity": "10", "metric": "revenue", "severity": 3, "direction": "down"}]

    def _performance(session, **kw):
        seen["performance"] = kw.get("eff")
        return {
            "revenue": {
                "cm": 1000.0, "cm_py": 800.0, "yoy_pct": 25.0,
                "has_plan": True, "plan_cm": 900.0, "plan_vs_actual": 100.0,
                "var_pct": 11.11, "coverage_pct": 111.11,
            },
            "gross_margin": {
                "pct": 38.0, "yoy_pp": 2.0, "plan_pct": 36.0,
                "plan_vs_actual_pp": 2.0,
            },
            "ebit": {"cm": 90.0, "margin_pct": 9.0},
        }

    monkeypatch.setattr(osum, "build_ebit_table", _ebit)
    monkeypatch.setattr(osum, "build_wc_statement_compat", _wc_stmt)
    monkeypatch.setattr(osum, "build_wc_snapshot_annual", _wc_snap)
    monkeypatch.setattr(osum, "build_top_entities", _top)
    monkeypatch.setattr(osum, "build_dupont", _dupont)
    monkeypatch.setattr(osum, "build_recent_month_alerts", _alerts)
    monkeypatch.setattr(osum, "_performance_block", _performance)
    return seen


@pytest.fixture(autouse=True)
def _no_cache_no_overrides(monkeypatch):
    monkeypatch.setattr(osum.settings, "overview_summary_cache_ttl_s", 0)
    monkeypatch.setattr(osum.settings, "overview_summary_use_mart", False)
    osum.clear_overview_summary_cache()
    yield
    app.dependency_overrides.clear()
    osum.clear_overview_summary_cache()


def _client(session: MagicMock, user: User) -> TestClient:
    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[get_read_session] = lambda: session
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app, raise_server_exceptions=False)


_PATH = "/api/v1/financials/overview/summary"


# ===========================================================================
# (1) Assembly — sections + visibility injection
# ===========================================================================
class TestAssembly:

    def test_admin_assembles_all_sections_and_injects_none(self, monkeypatch):
        seen = _install_fakes(monkeypatch)
        session = _make_session()  # admin
        out = osum.build_overview_summary(
            session, entity=None, year=2026, month=3,
            allowed_prefixes=None, cache_ttl_s=0,
        )
        # Every sub-query received allowed_entities=None (unrestricted).
        for k in ("ebit", "wc_stmt", "wc_snap", "top_customer", "top_supplier",
                  "dupont", "alerts", "performance"):
            assert seen[k] is None, k
        # Hero revenue + YoY.
        assert out["hero"]["revenue"]["cm"] == 1000.0
        assert out["hero"]["revenue"]["yoy_pct"] == 25.0        # (1000-800)/800*100
        assert out["hero"]["ebit"]["margin_pct"] == 9.0         # 90/1000*100
        # Cash headline (signed, from canned BS query): 500, Δm 100, Δyoy 200.
        assert out["cash"] == {"level": 500.0, "delta_month": 100.0, "delta_yoy": 200.0}
        # WC ratios + deep-dive levels (Δmonth from stmt, Δfy from snapshot).
        wc = out["working_capital"]
        assert (wc["dso"], wc["dpo"], wc["dio"], wc["ccc"], wc["nwc"]) == \
            (91.2, 80.0, 121.7, 132.9, 6500.0)
        inv = next(l for l in wc["levels"] if l["key"] == "inventories")
        assert inv["level"] == 4000.0 and inv["delta_month"] == 200.0
        assert inv["delta_fy"] == 200.0
        # Top entities + dupont + alerts present.
        assert out["top_customer"]["name"] == "customer-A"
        assert out["top_supplier"]["name"] == "supplier-A"
        assert out["dupont"]["metrics"]["roe"]["value"] == 11.25
        assert out["performance"]["revenue"]["has_plan"] is True
        assert out["performance"]["revenue"]["plan_vs_actual"] == 100.0
        assert out["performance"]["gross_margin"]["pct"] == 38.0
        assert out["alerts"] and out["alerts"][0]["severity"] == 3
        assert out["meta"]["source"] == "builder"

    def test_restricted_injects_prefix_set_into_every_subquery(self, monkeypatch):
        seen = _install_fakes(monkeypatch)
        session = _make_session()
        osum.build_overview_summary(
            session, entity=None, year=2026, month=3,
            allowed_prefixes={"10", "20"}, cache_ttl_s=0,
        )
        for k in ("ebit", "wc_stmt", "wc_snap", "top_customer", "top_supplier",
                  "dupont", "alerts", "performance"):
            assert seen[k] == {"10", "20"}, k

    def test_entity_narrow_within_visibility_restricts_to_single_prefix(self, monkeypatch):
        seen = _install_fakes(monkeypatch)
        # dim_legal_entity resolves DE→10 for the entity narrow.
        session = _make_session(code_to_prefix={"DE": "10"})
        osum.build_overview_summary(
            session, entity="DE", year=2026, month=3,
            allowed_prefixes={"10", "20"}, cache_ttl_s=0,
        )
        assert seen["ebit"] == {"10"}       # narrowed to the single visible prefix
        assert seen["alerts"] == {"10"}

    def test_entity_narrow_outside_visibility_fails_closed(self, monkeypatch):
        seen = _install_fakes(monkeypatch)
        session = _make_session(code_to_prefix={"US": "30"})
        out = osum.build_overview_summary(
            session, entity="US", year=2026, month=3,
            allowed_prefixes={"10", "20"}, cache_ttl_s=0,
        )
        assert out["meta"]["source"] == "fail_closed"
        assert seen == {}  # NO sub-builder ran → no cross-entity data


# ===========================================================================
# (2) Fail-closed on empty visibility
# ===========================================================================
class TestFailClosed:

    def test_empty_visibility_is_zeroed_and_runs_no_builder(self, monkeypatch):
        seen = _install_fakes(monkeypatch)
        session = _make_session()
        out = osum.build_overview_summary(
            session, entity=None, year=2026, month=3,
            allowed_prefixes=set(), cache_ttl_s=0,
        )
        assert seen == {}  # deny-all short-circuits BEFORE any sub-query
        assert out["meta"]["source"] == "fail_closed"
        assert out["hero"]["revenue"]["cm"] == 0.0
        assert out["cash"] == {"level": 0.0, "delta_month": 0.0, "delta_yoy": 0.0}
        assert out["working_capital"]["levels"] == []
        assert out["top_customer"] is None and out["alerts"] == []


# ===========================================================================
# (3) Endpoint — visible_entity_codes invoked, shape, fail-closed 200 (not 500)
# ===========================================================================
class TestEndpoint:

    def test_admin_200_full_shape(self, monkeypatch):
        _install_fakes(monkeypatch)
        session = _make_session()  # admin
        resp = _client(session, _ADMIN).get(_PATH, params={"year": 2026, "month": 3})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["hero"]["revenue"]["yoy_pct"] == 25.0
        assert body["working_capital"]["ccc"] == 132.9
        # visible_entity_codes was invoked (admin path never queries visibility rows,
        # but the endpoint must still return the admin summary).
        assert body["meta"]["visibility"] == "admin"

    def test_restricted_200_and_prefix_filter_reaches_subqueries(self, monkeypatch):
        seen = _install_fakes(monkeypatch)
        session = _make_session(
            visibility_codes=["DE", "AT"],
            code_to_prefix={"DE": "10", "AT": "20", "US": "30"},
        )
        resp = _client(session, _RESTRICTED).get(_PATH, params={"year": 2026, "month": 3})
        assert resp.status_code == 200, resp.text
        # visible_entity_codes → {DE,AT} → prefixes {10,20}; never US ('30').
        assert seen["ebit"] == {"10", "20"}
        assert seen["dupont"] == {"10", "20"}
        assert resp.json()["meta"]["visibility"] == "restricted"

    def test_restricted_zero_visibility_is_zeroed_200_not_500(self, monkeypatch):
        seen = _install_fakes(monkeypatch)
        session = _make_session(visibility_codes=[])  # deny-all
        resp = _client(session, _RESTRICTED).get(_PATH, params={"year": 2026, "month": 3})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["meta"]["source"] == "fail_closed"
        assert body["hero"]["revenue"]["cm"] == 0.0
        assert seen == {}  # no sub-builder ran → no cross-entity leak

    def test_restricted_prefix_mapping_error_is_zeroed_200_not_500(self, monkeypatch):
        """FIX L1 — a dim_legal_entity lookup failure fails closed (deny), not 500."""
        seen = _install_fakes(monkeypatch)

        # Session double: visibility resolves to codes, but the prefix mapping
        # (dim_legal_entity ANY(:codes)) blows up → must fail closed, not 500.
        session = MagicMock()

        def _execute(stmt, params=None):
            sql = str(stmt)
            result = MagicMock()
            if "admin_role_entity_visibility" in sql:
                rows = [_Row({"legal_entity_code": c}) for c in ("DE", "AT")]
                result.fetchall.return_value = rows
                result.fetchone.return_value = rows[0]
                return result
            if "dim_legal_entity" in sql:
                raise RuntimeError("dim_legal_entity unavailable")
            result.fetchall.return_value = []
            result.fetchone.return_value = None
            return result

        session.execute.side_effect = _execute

        resp = _client(session, _RESTRICTED).get(_PATH, params={"year": 2026, "month": 3})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["meta"]["source"] == "fail_closed"
        assert body["hero"]["revenue"]["cm"] == 0.0
        assert seen == {}  # deny-all → no sub-builder ran → no cross-entity leak


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
