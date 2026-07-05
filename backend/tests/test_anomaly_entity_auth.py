"""Param-free anomaly tree endpoints + per-user entity-prefix scoping (Phase 4).

Covers the reworked Anomaly Detection GETs in app.routers.financials_compat:
    GET /api/v1/financials/anomalies/overview
    GET /api/v1/financials/anomalies/outliers
    GET /api/v1/financials/anomalies/seasonality
    GET /api/v1/financials/anomalies/forensic
    GET /api/v1/financials/anomalies/bookings

These REPLACED the earlier per-account/period outliers/seasonality/forensic
handlers (and their _guard_entity single-entity guard).  The new endpoints take
NO entity/period/statement params; visibility is mapped to ANALYSIS entity
prefixes via _anomaly_entity_prefixes and forwarded to the Phase 3 read-through
orchestrators (anomaly_compute) / the live bookings drill.

DB-FREE: auth + DB deps are overridden; the orchestrators / bookings drill are
monkeypatched so we observe the entity_prefixes the route forwards and never
touch real data.

Scope contract (_anomaly_entity_prefixes, backed by
app.services.entity_visibility.visible_entity_codes + dim_legal_entity):
    - admin (no grants)              → None (consolidated over ALL entities).
    - restricted user                → sorted set of their OWN entity prefixes
                                       (union; never the whole ledger / cross-tenant).
    - restricted user, zero grants   → [] (deny-all, fail-closed).

500 contract: an unexpected failure returns a GENERIC detail — never str(exc).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import app.routers.financials_compat as fc
from app.auth import User, current_user
from app.db import get_session
from app.main import app


_ADMIN = User(user_id=1, email="admin@finssentials.com", display_name="Admin", is_admin=True)
_RESTRICTED = User(user_id=2, email="user@finssentials.com", display_name="User", is_admin=False)


class _DictRow:
    def __init__(self, values: tuple):
        self._values = tuple(values)

    def __getitem__(self, key):
        return self._values[key]

    def __iter__(self):
        return iter(self._values)


def _make_session(
    visibility_codes: list[str] | None = None,
    code_to_prefix: dict[str, str] | None = None,
) -> MagicMock:
    """MagicMock session: admin_role_entity_visibility → codes; dim_legal_entity → prefixes."""
    code_to_prefix = code_to_prefix or {}
    session = MagicMock()

    def _execute(stmt, params=None):
        sql = str(stmt)
        result = MagicMock()
        if "admin_role_entity_visibility" in sql:
            rows = [_DictRow((c,)) for c in (visibility_codes or [])]
        elif "dim_legal_entity" in sql:
            codes = (params or {}).get("codes", [])
            rows = [
                _DictRow((code_to_prefix[c],))
                for c in codes
                if c in code_to_prefix
            ]
        else:
            rows = []
        result.fetchall.return_value = rows
        result.fetchone.return_value = rows[0] if rows else None
        return result

    session.execute.side_effect = _execute
    return session


def _client(session: MagicMock, user: User) -> TestClient:
    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _capturing_orchestrator(captured: dict):
    """A fake anomaly_compute.get_* that records entity_prefixes + returns a payload."""

    def _fake(session, *, entity_prefixes=None, **kwargs):
        captured["entity_prefixes"] = entity_prefixes
        return {"analysis": "x", "cards": [], "tree": [], "cache_hit": False}

    return _fake


# Each tree endpoint + the anomaly_compute orchestrator it calls.
_ROUTES = [
    ("/api/v1/financials/anomalies/overview", "get_overview", "overview"),
    ("/api/v1/financials/anomalies/outliers", "get_outliers", "outliers"),
    ("/api/v1/financials/anomalies/seasonality", "get_seasonality", "seasonality"),
    ("/api/v1/financials/anomalies/forensic", "get_forensic", "forensic"),
]


@pytest.mark.parametrize("path,orch_name,label", _ROUTES)
class TestTreeEndpoints:
    def test_returns_orchestrator_payload_with_cache_hit(
        self, monkeypatch, path, orch_name, label
    ):
        captured: dict = {}
        monkeypatch.setattr(
            fc.anomaly_compute, orch_name, _capturing_orchestrator(captured)
        )
        session = _make_session()  # admin
        resp = _client(session, _ADMIN).get(path)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "cache_hit" in body

    def test_admin_scope_is_none(self, monkeypatch, path, orch_name, label):
        captured: dict = {}
        monkeypatch.setattr(
            fc.anomaly_compute, orch_name, _capturing_orchestrator(captured)
        )
        session = _make_session()  # admin → visible_entity_codes None
        resp = _client(session, _ADMIN).get(path)
        assert resp.status_code == 200, resp.text
        assert captured["entity_prefixes"] is None

    def test_restricted_user_sees_only_own_prefixes(
        self, monkeypatch, path, orch_name, label
    ):
        captured: dict = {}
        monkeypatch.setattr(
            fc.anomaly_compute, orch_name, _capturing_orchestrator(captured)
        )
        session = _make_session(
            visibility_codes=["DE", "AT"],
            code_to_prefix={"DE": "10", "AT": "20", "US": "30"},
        )
        resp = _client(session, _RESTRICTED).get(path)
        assert resp.status_code == 200, resp.text
        # sorted prefixes for the user's OWN entities only — never US ('30').
        assert captured["entity_prefixes"] == ["10", "20"]

    def test_restricted_user_zero_grants_is_403(
        self, monkeypatch, path, orch_name, label
    ):
        captured: dict = {}
        monkeypatch.setattr(
            fc.anomaly_compute, orch_name, _capturing_orchestrator(captured)
        )
        session = _make_session(visibility_codes=[])  # deny-all
        resp = _client(session, _RESTRICTED).get(path)
        # Fail-closed: an empty prefix set must NOT fall open to all entities.
        assert resp.status_code == 403, resp.text
        assert "entity_prefixes" not in captured

    def test_500_returns_generic_detail(self, monkeypatch, path, orch_name, label):
        secret = "secret SQL / tenant detail 0xDEADBEEF"

        def _boom(*a, **k):
            raise RuntimeError(secret)

        monkeypatch.setattr(fc.anomaly_compute, orch_name, _boom)
        session = _make_session()
        resp = _client(session, _ADMIN).get(path)
        assert resp.status_code == 500, resp.text
        detail = resp.json()["detail"]
        assert detail == f"Internal error building {label}."
        assert secret not in detail


class TestEntityPrefixMapping:
    """Direct unit coverage of _anomaly_entity_prefixes."""

    def test_admin_returns_none(self):
        session = _make_session()  # admin
        assert fc._anomaly_entity_prefixes(session, _ADMIN) is None

    def test_restricted_returns_sorted_own_prefixes(self):
        session = _make_session(
            visibility_codes=["AT", "DE"],
            code_to_prefix={"DE": "10", "AT": "20"},
        )
        assert fc._anomaly_entity_prefixes(session, _RESTRICTED) == ["10", "20"]

    def test_restricted_zero_grants_raises_403(self):
        from fastapi import HTTPException

        session = _make_session(visibility_codes=[])
        with pytest.raises(HTTPException) as exc:
            fc._anomaly_entity_prefixes(session, _RESTRICTED)
        assert exc.value.status_code == 403

    def test_restricted_unresolvable_codes_raises_403(self):
        # Granted codes that resolve to no prefix in dim_legal_entity → deny.
        session = _make_session(visibility_codes=["XX"], code_to_prefix={})
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            fc._anomaly_entity_prefixes(session, _RESTRICTED)
        assert exc.value.status_code == 403


class TestBookingsEndpoint:
    _PATH = "/api/v1/financials/anomalies/bookings"

    def test_requires_account_number_group(self, monkeypatch):
        called: dict = {}

        def _fake(session, ang, *, entity_prefixes=None, limit=200):
            called["ang"] = ang
            return {"account_number_group": ang, "bookings": [], "stats": {"n": 0}}

        monkeypatch.setattr(fc, "list_account_bookings", _fake)
        session = _make_session()
        resp = _client(session, _ADMIN).get(self._PATH)  # no account_number_group
        assert resp.status_code == 422, resp.text
        assert "ang" not in called

    def test_forwards_account_and_prefixes(self, monkeypatch):
        called: dict = {}

        def _fake(session, ang, *, entity_prefixes=None, limit=200):
            called["ang"] = ang
            called["entity_prefixes"] = entity_prefixes
            called["limit"] = limit
            return {"account_number_group": ang, "bookings": [], "stats": {"n": 0}}

        monkeypatch.setattr(fc, "list_account_bookings", _fake)
        session = _make_session(
            visibility_codes=["DE"], code_to_prefix={"DE": "10"},
        )
        resp = _client(session, _RESTRICTED).get(
            self._PATH, params={"account_number_group": "4000", "limit": 50}
        )
        assert resp.status_code == 200, resp.text
        assert called["ang"] == "4000"
        assert called["entity_prefixes"] == ["10"]
        assert called["limit"] == 50

    def test_caps_limit_above_500(self, monkeypatch):
        monkeypatch.setattr(
            fc, "list_account_bookings",
            lambda *a, **k: {"bookings": [], "stats": {"n": 0}},
        )
        session = _make_session()
        resp = _client(session, _ADMIN).get(
            self._PATH, params={"account_number_group": "4000", "limit": 9999}
        )
        # FastAPI Query(le=500) rejects out-of-range → 422.
        assert resp.status_code == 422, resp.text

    def test_500_returns_generic_detail(self, monkeypatch):
        secret = "secret 0xCAFE"

        def _boom(*a, **k):
            raise RuntimeError(secret)

        monkeypatch.setattr(fc, "list_account_bookings", _boom)
        session = _make_session()
        resp = _client(session, _ADMIN).get(
            self._PATH, params={"account_number_group": "4000"}
        )
        assert resp.status_code == 500, resp.text
        detail = resp.json()["detail"]
        assert detail == "Internal error listing bookings."
        assert secret not in detail
