"""Fail-closed entity-visibility contract for AR/AP aging endpoints (Phase 4).

Covers the entity-prefix scoping added to the aging router in
``app.routers.sales_compat`` (``_aging_scope`` context manager) and the
fail-closed short-circuit in ``app.services.opos_aging._partner_view``
(``_ALLOWED_PREFIXES`` ContextVar + ``_empty_partner_view``).

Endpoints under test:
    GET /api/v1/sales/receivables-aging
    GET /api/v1/sales/payables-aging
    GET /api/v1/sales/receivables-aging/customers

Security contract (mirrors test_overview_partners_endpoint.py and
test_anomaly_entity_auth.py):
    * admin (visible_entity_codes → None)         → _ALLOWED_PREFIXES is None
                                                    (all entities, unchanged).
    * restricted user (codes → prefixes)          → _ALLOWED_PREFIXES is the
                                                    user-specific frozenset;
                                                    cross-entity prefix absent.
    * restricted user, ZERO visibility            → 200, zeroed totals, NO SQL
                                                    to data tables (fail-closed
                                                    short-circuit before
                                                    fetch_opos_rows).

DB-FREE: auth + DB deps are overridden; builder functions in ``sales_compat``
are monkeypatched for cases 1 and 2 so we observe the ContextVar state the
endpoint injects without touching real data.  For case 3, ``fetch_opos_rows``
in ``opos_aging`` is patched to raise — a 200 response proves it was never
reached (the short-circuit fired inside ``_partner_view``).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import app.routers.sales_compat as sc
import app.services.opos_aging as opos_mod
from app.auth import User, current_user
from app.db import get_read_session, get_session
from app.main import app
from app.services.opos_aging import _ALLOWED_PREFIXES, clear_aging_cache


# ---------------------------------------------------------------------------
# Shared user singletons
# ---------------------------------------------------------------------------
_ADMIN = User(
    user_id=1, email="admin@finssentials.com", display_name="Admin", is_admin=True
)
_RESTRICTED = User(
    user_id=2, email="user@finssentials.com", display_name="User", is_admin=False
)


# ---------------------------------------------------------------------------
# Session double
# ---------------------------------------------------------------------------
def _make_session(
    visibility_codes: list[str] | None = None,
    code_to_prefix: dict[str, str] | None = None,
) -> MagicMock:
    """MagicMock session: admin_role_entity_visibility → codes; dim_legal_entity → prefixes.

    Handles all three SQL shapes that ``_aging_scope`` may issue:
      * admin_role_entity_visibility join  → visible entity codes for a user.
      * dim_legal_entity ANY(:codes)       → map_codes_to_prefixes bulk lookup.
      * dim_legal_entity = :e              → resolve_entity_prefix single lookup.
    Everything else returns an empty result so unexpected SQL fails silently.
    """
    code_to_prefix = code_to_prefix or {}
    session = MagicMock()

    def _execute(stmt, params=None):
        sql = str(stmt)
        result = MagicMock()
        if "admin_role_entity_visibility" in sql:
            # visible_entity_codes() — returns the granted legal_entity_codes.
            rows: list = [(c,) for c in (visibility_codes or [])]
        elif "dim_legal_entity" in sql:
            if params and "e" in params:
                # resolve_entity_prefix — single-code lookup.
                c = params["e"]
                rows = [(code_to_prefix[c],)] if c in code_to_prefix else []
            else:
                # map_codes_to_prefixes — ANY(:codes) bulk lookup.
                codes = (params or {}).get("codes", [])
                rows = [(code_to_prefix[c],) for c in codes if c in code_to_prefix]
        else:
            rows = []
        result.fetchall.return_value = rows
        result.fetchone.return_value = rows[0] if rows else None
        return result

    session.execute.side_effect = _execute
    return session


# ---------------------------------------------------------------------------
# TestClient factory
# ---------------------------------------------------------------------------
def _client(session: MagicMock, user: User) -> TestClient:
    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[get_read_session] = lambda: session
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Autouse fixture — clear dep overrides + OPOS cache between tests
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset():
    clear_aging_cache()
    yield
    app.dependency_overrides.clear()
    clear_aging_cache()


# ---------------------------------------------------------------------------
# Capturing fake builders (observe _ALLOWED_PREFIXES ContextVar at call time)
# ---------------------------------------------------------------------------
def _ar_builder(captured: dict):
    """Replacement for ``sc.build_receivables_aging`` — records scope + returns minimal response."""
    def _fake(session, year, month, entity=None, source="opos"):
        captured["allowed"] = _ALLOWED_PREFIXES.get()
        captured["entity"] = entity
        return {
            "year": year, "month": month,
            "as_of": f"{year}-{month:02d}-28",
            "source": "opos",
            "total_receivables": 999.0,
            "subledger_total": 999.0,
            "total_open_gross": 999.0,
            "credit_balances": 0.0,
            "reconciliation_mode": "opos_method_a",
            "series": [],
            "kpis": {
                "before_due": 900.0, "overdue": 99.0, "overdue_pct": 9.9,
                "dso_days": 30.0, "open_documents": 2,
            },
            "kpi_metrics": {}, "status_split": {},
        }
    return _fake


def _ap_builder(captured: dict):
    """Replacement for ``sc.build_payables_aging`` — records scope + returns minimal response."""
    def _fake(session, year, month, entity=None, source="opos"):
        captured["allowed"] = _ALLOWED_PREFIXES.get()
        captured["entity"] = entity
        return {
            "year": year, "month": month,
            "as_of": f"{year}-{month:02d}-28",
            "source": "opos",
            "total_payables": 888.0,
            "subledger_total": 888.0,
            "total_open_gross": 888.0,
            "credit_balances": 0.0,
            "reconciliation_mode": "opos_method_a",
            "series": [],
            "kpis": {
                "before_due": 800.0, "overdue": 88.0, "overdue_pct": 9.9,
                "dpo_days": 45.0, "open_documents": 1,
            },
            "kpi_metrics": {}, "status_split": {},
        }
    return _fake


def _customers_builder(captured: dict):
    """Replacement for ``sc.build_receivables_customers`` — records scope + returns minimal response."""
    def _fake(session, year, month, entity=None, limit=50):
        captured["allowed"] = _ALLOWED_PREFIXES.get()
        captured["entity"] = entity
        return {
            "year": year, "month": month, "source": "opos",
            "register": [{"partner_key": "CUST-1", "balance": 77.0}],
            "scatter": [], "combo": [],
        }
    return _fake


# ===========================================================================
# (1) Admin user — _ALLOWED_PREFIXES is None (all entities, no extra filter)
# ===========================================================================
class TestAdminScope:
    """Admin users must have _ALLOWED_PREFIXES=None so the builder sees every entity."""

    def test_receivables_aging_admin_scope_is_none(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(sc, "build_receivables_aging", _ar_builder(captured))
        session = _make_session()  # admin path never queries visibility rows
        resp = _client(session, _ADMIN).get(
            "/api/v1/sales/receivables-aging",
            params={"year": 2026, "month": 6},
        )
        assert resp.status_code == 200, resp.text
        assert captured["allowed"] is None, (
            f"Admin request must inject None (unrestricted) into aging_visibility, "
            f"got: {captured['allowed']!r}"
        )
        assert resp.json()["total_receivables"] == 999.0

    def test_payables_aging_admin_scope_is_none(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(sc, "build_payables_aging", _ap_builder(captured))
        session = _make_session()
        resp = _client(session, _ADMIN).get(
            "/api/v1/sales/payables-aging",
            params={"year": 2026, "month": 6},
        )
        assert resp.status_code == 200, resp.text
        assert captured["allowed"] is None
        assert resp.json()["total_payables"] == 888.0

    def test_receivables_customers_admin_scope_is_none(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(sc, "build_receivables_customers", _customers_builder(captured))
        session = _make_session()
        resp = _client(session, _ADMIN).get(
            "/api/v1/sales/receivables-aging/customers",
            params={"year": 2026, "month": 6},
        )
        assert resp.status_code == 200, resp.text
        assert captured["allowed"] is None


# ===========================================================================
# (2) Restricted user — _ALLOWED_PREFIXES is the mapped prefix frozenset
# ===========================================================================
class TestRestrictedScope:
    """Restricted users must have _ALLOWED_PREFIXES equal to ONLY their own prefixes."""

    def test_receivables_aging_scoped_user_sees_own_prefixes_only(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(sc, "build_receivables_aging", _ar_builder(captured))
        session = _make_session(
            visibility_codes=["DE", "AT"],
            code_to_prefix={"DE": "10", "AT": "20", "US": "30"},  # US not in grants
        )
        resp = _client(session, _RESTRICTED).get(
            "/api/v1/sales/receivables-aging",
            params={"year": 2026, "month": 6},
        )
        assert resp.status_code == 200, resp.text
        # Must receive exactly the user's own prefixes — US ('30') must be absent.
        assert captured["allowed"] == frozenset({"10", "20"}), (
            f"Expected frozenset({{'10','20'}}), got: {captured['allowed']!r}"
        )
        assert "30" not in (captured["allowed"] or set())

    def test_payables_aging_scoped_user_sees_own_prefixes_only(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(sc, "build_payables_aging", _ap_builder(captured))
        session = _make_session(
            visibility_codes=["DE"],
            code_to_prefix={"DE": "10", "AT": "20"},  # AT not in grants
        )
        resp = _client(session, _RESTRICTED).get(
            "/api/v1/sales/payables-aging",
            params={"year": 2026, "month": 6},
        )
        assert resp.status_code == 200, resp.text
        assert captured["allowed"] == frozenset({"10"})
        assert "20" not in (captured["allowed"] or set())

    def test_receivables_customers_scoped_user_sees_own_prefixes_only(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(sc, "build_receivables_customers", _customers_builder(captured))
        session = _make_session(
            visibility_codes=["AT"],
            code_to_prefix={"DE": "10", "AT": "20"},  # DE not in grants
        )
        resp = _client(session, _RESTRICTED).get(
            "/api/v1/sales/receivables-aging/customers",
            params={"year": 2026, "month": 6},
        )
        assert resp.status_code == 200, resp.text
        assert captured["allowed"] == frozenset({"20"})
        assert "10" not in (captured["allowed"] or set())


# ===========================================================================
# (3) Fail-closed — zeroed 200, fetch_opos_rows NOT called, never a 500
# ===========================================================================
class TestFailClosed:
    """Empty visibility → _ALLOWED_PREFIXES = frozenset() → _partner_view short-circuits
    to _empty_partner_view BEFORE reaching fetch_opos_rows.

    ``fetch_opos_rows`` is patched to raise RuntimeError.  A 200 response proves
    the short-circuit fired; a 500 would mean the raise was reached (a bug).
    """

    def test_receivables_aging_empty_visibility_is_zeroed_200_no_sql(self, monkeypatch):
        def _bomb(*a, **kw):
            raise RuntimeError(
                "SECURITY: fetch_opos_rows called despite empty visibility scope"
            )
        monkeypatch.setattr(opos_mod, "fetch_opos_rows", _bomb)
        session = _make_session(visibility_codes=[])  # deny-all — no grants
        resp = _client(session, _RESTRICTED).get(
            "/api/v1/sales/receivables-aging",
            params={"year": 2026, "month": 6},
        )
        # Must be 200, NOT 500 (which would signal fetch_opos_rows was reached).
        assert resp.status_code == 200, (
            f"Fail-closed must return 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        # All monetary totals must be zero — no cross-entity data leaked.
        assert body.get("total_receivables") == 0.0, body
        assert body["kpis"]["overdue"] == 0.0
        assert body["kpis"]["open_documents"] == 0
        assert all(s["amount"] == 0.0 for s in body["series"]), body["series"]

    def test_payables_aging_empty_visibility_is_zeroed_200_no_sql(self, monkeypatch):
        def _bomb(*a, **kw):
            raise RuntimeError(
                "SECURITY: fetch_opos_rows called despite empty visibility scope"
            )
        monkeypatch.setattr(opos_mod, "fetch_opos_rows", _bomb)
        session = _make_session(visibility_codes=[])
        resp = _client(session, _RESTRICTED).get(
            "/api/v1/sales/payables-aging",
            params={"year": 2026, "month": 6},
        )
        assert resp.status_code == 200, (
            f"Fail-closed must return 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body.get("total_payables") == 0.0, body
        assert body["kpis"]["overdue"] == 0.0
        assert all(s["amount"] == 0.0 for s in body["series"]), body["series"]

    def test_receivables_customers_empty_visibility_is_zeroed_200_no_sql(self, monkeypatch):
        def _bomb(*a, **kw):
            raise RuntimeError(
                "SECURITY: fetch_opos_rows called despite empty visibility scope"
            )
        monkeypatch.setattr(opos_mod, "fetch_opos_rows", _bomb)
        session = _make_session(visibility_codes=[])
        resp = _client(session, _RESTRICTED).get(
            "/api/v1/sales/receivables-aging/customers",
            params={"year": 2026, "month": 6},
        )
        assert resp.status_code == 200, (
            f"Fail-closed must return 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        # All partner lists must be empty — no cross-entity partner leaked.
        assert body["register"] == [], body
        assert body["scatter"] == [], body


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
