"""Fail-closed entity-visibility contract for the metrics ar_aging/ap_aging series.

Covers the scoping added to ``app.routers.metrics_compat.get_metric`` (Fix 1):
the ar_aging/ap_aging OPOS series must be wrapped in the shared ``aging_scope``
so a RESTRICTED user can never receive all-entity (all-tenant) aging buckets,
exactly like ``sales_compat`` aging endpoints.

The ``get_metric`` function is invoked DIRECTLY (not via HTTP) because the
``/api/v1/metrics`` path is currently served by ``meta_compat`` first — see the
router-shadowing note in the change HANDOFF.  Testing the function directly pins
the security behaviour of the code under change regardless of route ordering.

``build_metrics_aging_series`` is monkeypatched to capture the bound
``_ALLOWED_PREFIXES`` ContextVar at call time (mirrors test_aging_entity_auth).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import app.routers.metrics_compat as mc
from app.auth import User
from app.services.opos_aging import _ALLOWED_PREFIXES, clear_aging_cache

_ADMIN = User(user_id=1, email="admin@finssentials.com", display_name="Admin", is_admin=True)
_RESTRICTED = User(user_id=2, email="user@finssentials.com", display_name="User", is_admin=False)


def _make_session(visibility_codes=None, code_to_prefix=None) -> MagicMock:
    """Session double: admin_role_entity_visibility → codes; dim_legal_entity → prefixes."""
    code_to_prefix = code_to_prefix or {}
    session = MagicMock()

    def _execute(stmt, params=None):
        sql = str(stmt)
        result = MagicMock()
        if "admin_role_entity_visibility" in sql:
            rows = [(c,) for c in (visibility_codes or [])]
        elif "dim_legal_entity" in sql:
            if params and "e" in params:
                c = params["e"]
                rows = [(code_to_prefix[c],)] if c in code_to_prefix else []
            else:
                codes = (params or {}).get("codes", [])
                rows = [(code_to_prefix[c],) for c in codes if c in code_to_prefix]
        else:
            rows = []
        result.fetchall.return_value = rows
        result.fetchone.return_value = rows[0] if rows else None
        return result

    session.execute.side_effect = _execute
    return session


@pytest.fixture(autouse=True)
def _reset():
    clear_aging_cache()
    yield
    clear_aging_cache()


def _capturing_series(captured: dict):
    def _fake(session, metric, entity):
        captured["allowed"] = _ALLOWED_PREFIXES.get()
        captured["entity"] = entity
        return [{"band": "not_yet_due", "label": "Not yet due", "value": 0.0}]
    return _fake


@pytest.mark.parametrize("metric", ["ar_aging", "ap_aging"])
def test_admin_scope_is_none(monkeypatch, metric):
    captured: dict = {}
    monkeypatch.setattr(mc, "build_metrics_aging_series", _capturing_series(captured))
    out = mc.get_metric(_ADMIN, _make_session(), metric=metric, entity=None)
    assert out["metric"] == metric
    assert captured["allowed"] is None  # admin unrestricted → all entities


@pytest.mark.parametrize("metric", ["ar_aging", "ap_aging"])
def test_restricted_user_sees_own_prefixes_only(monkeypatch, metric):
    captured: dict = {}
    monkeypatch.setattr(mc, "build_metrics_aging_series", _capturing_series(captured))
    session = _make_session(
        visibility_codes=["DE", "AT"],
        code_to_prefix={"DE": "10", "AT": "20", "US": "30"},  # US not granted
    )
    mc.get_metric(_RESTRICTED, session, metric=metric, entity=None)
    assert captured["allowed"] == frozenset({"10", "20"})
    assert "30" not in (captured["allowed"] or set())


@pytest.mark.parametrize("metric", ["ar_aging", "ap_aging"])
def test_empty_visibility_fails_closed(monkeypatch, metric):
    """Zero grants → bound scope is an empty frozenset (deny-all → zeroed, no SQL)."""
    captured: dict = {}
    monkeypatch.setattr(mc, "build_metrics_aging_series", _capturing_series(captured))
    session = _make_session(visibility_codes=[])  # deny-all
    mc.get_metric(_RESTRICTED, session, metric=metric, entity=None)
    assert captured["allowed"] == frozenset()  # fail-closed empty scope, not None


def test_unsupported_metric_rejected():
    import fastapi
    with pytest.raises(fastapi.HTTPException) as ei:
        mc.get_metric(_ADMIN, _make_session(), metric="revenue_total", entity=None)
    assert ei.value.status_code == 400
