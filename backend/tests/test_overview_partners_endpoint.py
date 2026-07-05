"""Overview v2 — batched partners endpoint + fail-closed tenant isolation (P4).

Covers ``GET /api/v1/financials/overview/partners`` (Areas 4 & 5), which serves
``{customers: build_customer_development, suppliers: build_supplier_development}``
in one round-trip.

DB-FREE: the two service builders are monkeypatched so we OBSERVE the
``allowed_entities`` (entity_prefix) filter each one receives — the test-visible
seam the security review relies on — and never touch real data.

Security contract (must hold — SAME pattern as /overview/summary):
  * admin (visible_entity_codes → None)      → BOTH services get allowed_entities=None.
  * restricted user (codes → prefixes)       → BOTH services get that prefix set.
  * entity narrow within visibility          → BOTH services get the single prefix.
  * entity narrow OUTSIDE visibility         → zeroed payload; NEITHER service runs.
  * restricted user, ZERO visibility         → zeroed payload; NEITHER service runs.
  * prefix-mapping error (non-admin)         → zeroed 200 (fail closed), NOT a 500.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import app.services.partner_development as pdev
from app.auth import User, current_user
from app.db import get_read_session, get_session
from app.main import app

_ADMIN = User(user_id=1, email="admin@finssentials.com", display_name="Admin", is_admin=True)
_RESTRICTED = User(user_id=2, email="user@finssentials.com", display_name="User", is_admin=False)

_PATH = "/api/v1/financials/overview/partners"


# ---------------------------------------------------------------------------
# Row + session doubles (mirrors test_overview_summary_endpoint.py)
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
    raise_on_prefix_map: bool = False,
) -> MagicMock:
    """Session double: visibility → codes; dim_legal_entity → prefixes."""
    code_to_prefix = code_to_prefix or {}
    session = MagicMock()

    def _execute(stmt, params=None):
        sql = str(stmt)
        result = MagicMock()
        if "admin_role_entity_visibility" in sql:
            rows = [_Row({"legal_entity_code": c}) for c in (visibility_codes or [])]
        elif "dim_legal_entity" in sql:
            if raise_on_prefix_map and not (params and "e" in params):
                raise RuntimeError("dim_legal_entity unavailable")
            if params and "e" in params:  # resolve_entity_prefix single lookup
                c = params["e"]
                rows = ([_Row({"entity_prefix": code_to_prefix[c]})]
                        if c in code_to_prefix else [])
            else:  # map_codes_to_prefixes ANY(:codes)
                codes = (params or {}).get("codes", [])
                rows = [_Row({"entity_prefix": code_to_prefix[c]})
                        for c in codes if c in code_to_prefix]
        else:
            rows = []
        result.fetchall.return_value = rows
        result.fetchone.return_value = rows[0] if rows else None
        return result

    session.execute.side_effect = _execute
    return session


def _install_fakes(monkeypatch) -> dict:
    """Replace both service builders with fakes recording their allowed_entities."""
    seen: dict[str, object] = {}

    def _fake(id_field: str, with_lost: bool, key: str):
        def _inner(session, *, entity=None, year, month, allowed_entities=None, top_n=10):
            seen[key] = allowed_entities
            seen[f"{key}_top_n"] = top_n
            out = {
                "period": {"year": year, "month": month},
                "id_field": id_field,
                "biggest": [{"rank": 1, id_field: 7, "name": f"{key}-A"}],
                "increase": [],
                "won": [],
            }
            if with_lost:
                out["lost"] = []
            return out
        return _inner

    monkeypatch.setattr(pdev, "build_customer_development",
                        _fake("customer_id", True, "customers"))
    monkeypatch.setattr(pdev, "build_supplier_development",
                        _fake("supplier_id", False, "suppliers"))
    return seen


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _client(session: MagicMock, user: User) -> TestClient:
    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[get_read_session] = lambda: session
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app, raise_server_exceptions=False)


# ===========================================================================
# (1) Admin — both services called unrestricted (allowed_entities=None)
# ===========================================================================
def test_admin_200_injects_none_into_both(monkeypatch):
    seen = _install_fakes(monkeypatch)
    session = _make_session()  # admin never queries visibility rows
    resp = _client(session, _ADMIN).get(_PATH, params={"year": 2026, "month": 6})
    assert resp.status_code == 200, resp.text
    assert seen["customers"] is None
    assert seen["suppliers"] is None
    body = resp.json()
    assert body["customers"]["id_field"] == "customer_id"
    assert body["suppliers"]["id_field"] == "supplier_id"
    assert body["customers"]["biggest"][0]["name"] == "customers-A"


# ===========================================================================
# (2) Restricted — mapped prefix set injected into BOTH services
# ===========================================================================
def test_restricted_injects_prefix_set_into_both(monkeypatch):
    seen = _install_fakes(monkeypatch)
    session = _make_session(
        visibility_codes=["DE", "AT"],
        code_to_prefix={"DE": "10", "AT": "20", "US": "30"},
    )
    resp = _client(session, _RESTRICTED).get(
        _PATH, params={"year": 2026, "month": 6, "top_n": 5})
    assert resp.status_code == 200, resp.text
    assert seen["customers"] == {"10", "20"}   # never US ('30')
    assert seen["suppliers"] == {"10", "20"}
    assert seen["customers_top_n"] == 5 and seen["suppliers_top_n"] == 5


def test_entity_narrow_within_visibility_restricts_to_single_prefix(monkeypatch):
    seen = _install_fakes(monkeypatch)
    session = _make_session(
        visibility_codes=["DE", "AT"],
        code_to_prefix={"DE": "10", "AT": "20"},
    )
    resp = _client(session, _RESTRICTED).get(
        _PATH, params={"year": 2026, "month": 6, "entity": "DE"})
    assert resp.status_code == 200, resp.text
    assert seen["customers"] == {"10"}
    assert seen["suppliers"] == {"10"}


# ===========================================================================
# (3) Fail-closed — zeroed 200, NEITHER service runs, NEVER a 500
# ===========================================================================
def test_entity_narrow_outside_visibility_is_zeroed_no_service(monkeypatch):
    seen = _install_fakes(monkeypatch)
    session = _make_session(
        visibility_codes=["DE"], code_to_prefix={"DE": "10", "US": "30"},
    )
    resp = _client(session, _RESTRICTED).get(
        _PATH, params={"year": 2026, "month": 6, "entity": "US"})
    assert resp.status_code == 200, resp.text
    assert seen == {}  # narrow outside visibility → NO service ran
    body = resp.json()
    assert body["customers"]["biggest"] == [] and body["customers"]["lost"] == []
    assert body["suppliers"]["biggest"] == []
    assert body["customers"]["id_field"] == "customer_id"


def test_empty_visibility_is_zeroed_200_no_service(monkeypatch):
    seen = _install_fakes(monkeypatch)
    session = _make_session(visibility_codes=[])  # deny-all
    resp = _client(session, _RESTRICTED).get(_PATH, params={"year": 2026, "month": 6})
    assert resp.status_code == 200, resp.text
    assert seen == {}  # deny-all → NO service ran → no cross-entity leak
    body = resp.json()
    assert body["customers"]["biggest"] == []
    assert body["suppliers"]["biggest"] == []


def test_prefix_mapping_error_is_zeroed_200_not_500(monkeypatch):
    seen = _install_fakes(monkeypatch)
    session = _make_session(
        visibility_codes=["DE", "AT"], raise_on_prefix_map=True,
    )
    resp = _client(session, _RESTRICTED).get(_PATH, params={"year": 2026, "month": 6})
    assert resp.status_code == 200, resp.text
    assert seen == {}  # mapping error → fail closed → NO service ran
    body = resp.json()
    assert body["customers"]["biggest"] == []
    assert body["suppliers"]["biggest"] == []


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
