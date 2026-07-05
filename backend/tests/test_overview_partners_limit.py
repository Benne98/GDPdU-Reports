"""Regression tests for the `top_n` bound on the Overview partner endpoints.

Item 2 (backend half): the Overview "Show 5 more" UI reveals partner rows 5 at a
time up to ~50, so the API's `top_n` ceiling was raised from 50 to 100 on BOTH
``GET /api/v1/financials/overview/partners`` and
``GET /api/v1/financials/overview/bundle``.  These tests pin the new boundary:

    * ``top_n=100``  → accepted (HTTP 200)
    * ``top_n=101``  → rejected by FastAPI query validation (HTTP 422)
    * ``top_n=50``   → still accepted (old ceiling, additive change)
    * ``top_n=0``    → rejected (ge=1 unchanged)

APPROACH — validation-boundary test via TestClient + dependency_overrides.
A fully synthetic *builder* test is not feasible without a live DB: the happy
path of ``build_customer/supplier_development`` issues real SQL through
``_resolve_entity_frag``.  Instead we exercise the endpoints through a restricted
(non-admin) user with ZERO entity-visibility rows.  That is the documented
fail-closed path: ``visible_entity_codes`` → empty set →
``map_codes_to_prefixes`` short-circuits WITHOUT SQL → ``_effective_prefixes``
returns ``denied=True`` → the handler returns the builders' zeroed shape and
runs NO partner SQL.  This lets us assert the query-validation boundary (the only
production change) deterministically, using synthetic data only — no real DB, no
real client data — while also confirming fail-closed visibility is untouched.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.auth import User, current_user
from app.db import get_read_session
from app.main import app

# Non-admin with ZERO visibility grants → fail-closed deny-all (no partner SQL).
_RESTRICTED = User(user_id=2, email="user@finssentials.com", display_name="User", is_admin=False)

_PARTNERS_URL = "/api/v1/financials/overview/partners"
_BUNDLE_URL = "/api/v1/financials/overview/bundle"


def _make_session() -> MagicMock:
    """Session whose visibility query returns NO rows → empty allow-set (deny-all)."""
    session = MagicMock()
    result = MagicMock()
    result.fetchall.return_value = []
    result.fetchone.return_value = None
    session.execute.return_value = result
    return session


def _client(user: User = _RESTRICTED) -> TestClient:
    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[get_read_session] = lambda: _make_session()
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _params(top_n: int) -> dict:
    return {"year": 2024, "month": 6, "top_n": top_n}


# ---------------------------------------------------------------------------
# /overview/partners
# ---------------------------------------------------------------------------
class TestPartnersTopNBoundary:
    def test_top_n_100_accepted(self):
        resp = _client().get(_PARTNERS_URL, params=_params(100))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Fail-closed deny-all → zeroed shape, no cross-entity data.
        assert body["customers"]["biggest"] == []
        assert body["suppliers"]["biggest"] == []

    def test_top_n_50_still_accepted(self):
        # Old ceiling remains valid (change is additive).
        resp = _client().get(_PARTNERS_URL, params=_params(50))
        assert resp.status_code == 200, resp.text

    def test_top_n_101_rejected(self):
        resp = _client().get(_PARTNERS_URL, params=_params(101))
        assert resp.status_code == 422

    def test_top_n_zero_rejected(self):
        # ge=1 is unchanged.
        resp = _client().get(_PARTNERS_URL, params=_params(0))
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /overview/bundle
# ---------------------------------------------------------------------------
class TestBundleTopNBoundary:
    def test_top_n_100_accepted(self):
        resp = _client().get(_BUNDLE_URL, params=_params(100))
        assert resp.status_code == 200, resp.text

    def test_top_n_101_rejected(self):
        resp = _client().get(_BUNDLE_URL, params=_params(101))
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# fail-closed visibility is untouched
# ---------------------------------------------------------------------------
class TestFailClosedUntouched:
    def test_restricted_user_gets_zeroed_partners(self):
        resp = _client(_RESTRICTED).get(_PARTNERS_URL, params=_params(100))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["customers"]["biggest"] == []
        assert body["suppliers"]["biggest"] == []
