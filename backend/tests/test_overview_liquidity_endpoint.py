"""Overview v2 — Area-2 liquidity endpoint + fail-closed tenant isolation (P5).

Covers ``GET /api/v1/financials/overview/liquidity``, which serves
``app.services.liquidity.build_liquidity_available`` (cash + AR collectibility
haircut − outstanding AP) in one round-trip.

DB-FREE: the service's internal cash + aging seams are monkeypatched so we
OBSERVE the ``eff`` (entity_prefix) filter each one receives — the test-visible
seam the security review relies on — and never touch real data.

Security contract (must hold — SAME pattern as /overview/summary):
  * admin (visible_entity_codes → None)      → every sub-query gets eff=None.
  * restricted user (codes → prefixes)       → every sub-query gets that prefix set.
  * entity narrow within visibility          → every sub-query gets the single prefix.
  * entity narrow OUTSIDE visibility         → zeroed payload; NEITHER sub-query runs.
  * restricted user, ZERO visibility         → zeroed payload; NEITHER sub-query runs.
  * prefix-mapping error (non-admin)         → zeroed 200 (fail closed), NOT a 500.

The endpoint passes the RAW mapped prefix set as ``allowed_entities`` and lets the
service apply the ``entity`` intersection ONCE (no double-narrowing) — verified by
the entity-narrow cases below.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import app.services.liquidity as liq
from app.auth import User, current_user
from app.db import get_read_session, get_session
from app.main import app

_ADMIN = User(user_id=1, email="admin@finssentials.com", display_name="Admin", is_admin=True)
_RESTRICTED = User(user_id=2, email="user@finssentials.com", display_name="User", is_admin=False)

_PATH = "/api/v1/financials/overview/liquidity"

# Worked example (kEUR) from app/services/liquidity.py docstring:
#   bands 400/100/50/40/20/10  → CollectibleAR 585
#   cash 120 (EUR 120_000 → /1000), AP 300 → LiquidityAvailable 405
_CANNED_BANDS = {
    "not_yet_due": 400.0,
    "overdue_1_30": 100.0,
    "overdue_31_60": 50.0,
    "overdue_61_90": 40.0,
    "overdue_91_180": 20.0,
    "overdue_over_180": 10.0,
}
_CANNED_AP = 300.0


# ---------------------------------------------------------------------------
# Row + session doubles (mirror test_overview_summary_endpoint.py)
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
    """Replace the cash + aging seams with fakes recording the ``eff`` they receive."""
    seen: dict[str, object] = {}

    def _cash_frag(eff, builder_entity, session):
        seen["cash_eff"] = eff
        return "AND 1 = 1"

    def _cash_headline(session, *, year, month, ent_frag):
        # EUR — the service scales by 1/1000 → 120.0 kEUR.
        return {"level": 120_000.0, "delta_month": 0.0, "delta_yoy": 0.0}

    def _aging_scope(session, *, year, month, eff, builder_entity):
        seen["aging_eff"] = eff
        return dict(_CANNED_BANDS), _CANNED_AP

    monkeypatch.setattr(liq, "_cash_ent_frag", _cash_frag)
    monkeypatch.setattr(liq, "_cash_headline", _cash_headline)
    monkeypatch.setattr(liq, "_aging_scope", _aging_scope)
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
# (1) Assembly — service applies the intersection, worked example holds
# ===========================================================================
class TestAssembly:

    def test_admin_injects_none_into_every_subquery(self, monkeypatch):
        seen = _install_fakes(monkeypatch)
        session = _make_session()  # admin
        out = liq.build_liquidity_available(
            session, entity=None, year=2026, month=6, allowed_entities=None,
        )
        assert seen["cash_eff"] is None
        assert seen["aging_eff"] is None
        # Worked example (kEUR): cash 120, collectible 585, AP 300 → 405.
        assert out["cash"] == 120.0
        assert out["collectible_ar"] == 585.0
        assert out["outstanding_ap"] == 300.0
        assert out["liquidity_available"] == 405.0
        assert out["meta"]["visibility"] == "admin"

    def test_restricted_injects_prefix_set_into_every_subquery(self, monkeypatch):
        seen = _install_fakes(monkeypatch)
        session = _make_session()
        out = liq.build_liquidity_available(
            session, entity=None, year=2026, month=6, allowed_entities={"10", "20"},
        )
        assert seen["cash_eff"] == {"10", "20"}
        assert seen["aging_eff"] == {"10", "20"}
        assert out["meta"]["visibility"] == "restricted"

    def test_entity_narrow_within_visibility_restricts_to_single_prefix(self, monkeypatch):
        seen = _install_fakes(monkeypatch)
        session = _make_session(code_to_prefix={"DE": "10"})
        liq.build_liquidity_available(
            session, entity="DE", year=2026, month=6, allowed_entities={"10", "20"},
        )
        # Intersection applied ONCE by the service (not double-narrowed).
        assert seen["cash_eff"] == {"10"}
        assert seen["aging_eff"] == {"10"}

    def test_entity_narrow_outside_visibility_fails_closed(self, monkeypatch):
        seen = _install_fakes(monkeypatch)
        session = _make_session(code_to_prefix={"US": "30"})
        out = liq.build_liquidity_available(
            session, entity="US", year=2026, month=6, allowed_entities={"10", "20"},
        )
        assert out["meta"]["source"] == "fail_closed"
        assert seen == {}  # NO sub-query ran → no cross-entity data
        assert out["liquidity_available"] == 0.0


# ===========================================================================
# (2) Fail-closed on empty visibility
# ===========================================================================
class TestFailClosed:

    def test_empty_visibility_is_zeroed_and_runs_no_subquery(self, monkeypatch):
        seen = _install_fakes(monkeypatch)
        session = _make_session()
        out = liq.build_liquidity_available(
            session, entity=None, year=2026, month=6, allowed_entities=set(),
        )
        assert seen == {}  # deny-all short-circuits BEFORE any sub-query
        assert out["meta"]["source"] == "fail_closed"
        assert out["cash"] == 0.0
        assert out["collectible_ar"] == 0.0
        assert out["liquidity_available"] == 0.0


# ===========================================================================
# (3) Endpoint — visible_entity_codes invoked, shape, fail-closed 200 (not 500)
# ===========================================================================
class TestEndpoint:

    def test_admin_200_full_shape(self, monkeypatch):
        _install_fakes(monkeypatch)
        session = _make_session()  # admin
        resp = _client(session, _ADMIN).get(_PATH, params={"year": 2026, "month": 6})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["liquidity_available"] == 405.0
        assert body["collectible_ar"] == 585.0
        assert body["meta"]["visibility"] == "admin"
        # Field list from the FCE return survives to the response.
        assert set(body) >= {
            "meta", "cash", "ar_bands", "raw_ar", "collectible_ar",
            "outstanding_ap", "liquidity_available",
        }
        assert body["ar_bands"] and "credit_flag" in body["ar_bands"][0]

    def test_restricted_200_and_prefix_filter_reaches_subqueries(self, monkeypatch):
        seen = _install_fakes(monkeypatch)
        session = _make_session(
            visibility_codes=["DE", "AT"],
            code_to_prefix={"DE": "10", "AT": "20", "US": "30"},
        )
        resp = _client(session, _RESTRICTED).get(_PATH, params={"year": 2026, "month": 6})
        assert resp.status_code == 200, resp.text
        # visible_entity_codes → {DE,AT} → prefixes {10,20}; never US ('30').
        assert seen["cash_eff"] == {"10", "20"}
        assert seen["aging_eff"] == {"10", "20"}
        assert resp.json()["meta"]["visibility"] == "restricted"

    def test_restricted_entity_narrow_within_visibility_single_prefix(self, monkeypatch):
        seen = _install_fakes(monkeypatch)
        session = _make_session(
            visibility_codes=["DE", "AT"],
            code_to_prefix={"DE": "10", "AT": "20"},
        )
        resp = _client(session, _RESTRICTED).get(
            _PATH, params={"year": 2026, "month": 6, "entity": "DE"})
        assert resp.status_code == 200, resp.text
        assert seen["cash_eff"] == {"10"}
        assert seen["aging_eff"] == {"10"}

    def test_restricted_zero_visibility_is_zeroed_200_not_500(self, monkeypatch):
        seen = _install_fakes(monkeypatch)
        session = _make_session(visibility_codes=[])  # deny-all
        resp = _client(session, _RESTRICTED).get(_PATH, params={"year": 2026, "month": 6})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["meta"]["source"] == "fail_closed"
        assert body["liquidity_available"] == 0.0
        assert seen == {}  # no sub-query ran → no cross-entity leak

    def test_restricted_entity_narrow_outside_visibility_is_zeroed_no_subquery(self, monkeypatch):
        seen = _install_fakes(monkeypatch)
        session = _make_session(
            visibility_codes=["DE"], code_to_prefix={"DE": "10", "US": "30"},
        )
        resp = _client(session, _RESTRICTED).get(
            _PATH, params={"year": 2026, "month": 6, "entity": "US"})
        assert resp.status_code == 200, resp.text
        assert seen == {}  # narrow outside visibility → NO sub-query ran
        body = resp.json()
        assert body["meta"]["source"] == "fail_closed"
        assert body["liquidity_available"] == 0.0

    def test_restricted_prefix_mapping_error_is_zeroed_200_not_500(self, monkeypatch):
        """A dim_legal_entity lookup failure fails closed (deny), not 500."""
        seen = _install_fakes(monkeypatch)
        session = _make_session(
            visibility_codes=["DE", "AT"], raise_on_prefix_map=True,
        )
        resp = _client(session, _RESTRICTED).get(_PATH, params={"year": 2026, "month": 6})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["meta"]["source"] == "fail_closed"
        assert body["liquidity_available"] == 0.0
        assert seen == {}  # mapping error → fail closed → no cross-entity leak


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
