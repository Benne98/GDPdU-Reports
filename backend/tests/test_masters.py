"""Customer / Supplier MASTER editor API tests (app.routers.masters).

Layers:
  A. ROUTING / GATING (MagicMock session): unknown side 404, admin-gate on
     writes, auth on reads, restricted-user entity-scope denial + deny-all list,
     id construction (entity_prefix + normalized number), bad-prefix 422.
  B. ROUND-TRIP (live v2 DB, opt-in): POST builds the right id + upserts via the
     reused load_partners; a 2nd POST updates via COALESCE; PATCH updates fields;
     DELETE guard (409 when facts reference, 200 with force); GET list + search.
     SKIPPED unless DB_NAME=finssentials_v2.  ALWAYS cleans up (finally) so the
     golden ``compare live v2`` stays EQUIVALENT.

Mirrors backend/tests/test_ob_partner_ingest.py (mock visibility / v2-gated DB).
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.auth import User, current_user, require_admin
from app.db import get_session
from app.main import app

_ADMIN = User(user_id=1, email="admin@test", display_name="Admin", is_admin=True)
_NON_ADMIN = User(user_id=2, email="user@test", display_name="User", is_admin=False)


def _client_as(user: User, session=None) -> TestClient:
    app.dependency_overrides[get_session] = lambda: (session or MagicMock())
    app.dependency_overrides[current_user] = lambda: user
    if user.is_admin:
        app.dependency_overrides[require_admin] = lambda: user
    else:
        app.dependency_overrides.pop(require_admin, None)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# =========================================================================== #
# A) Routing / gating — MagicMock session
# =========================================================================== #
class TestRoutingAndGating:
    def test_unknown_side_404(self, monkeypatch):
        import app.routers.masters as m
        # admin → unrestricted, so the only failure is the unknown side.
        monkeypatch.setattr(m, "visible_entity_codes", lambda s, u: None)
        client = _client_as(_ADMIN)
        resp = client.get("/api/v1/masters/widgets")
        assert resp.status_code == 404

    def test_list_requires_auth(self):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/masters/customers")
        assert resp.status_code == 401

    def test_post_requires_admin(self):
        client = _client_as(_NON_ADMIN)
        resp = client.post("/api/v1/masters/customers", json={
            "entity_prefix": "01", "number": "100", "name_line_1": "X",
        })
        assert resp.status_code == 403

    def test_patch_requires_admin(self):
        client = _client_as(_NON_ADMIN)
        resp = client.patch("/api/v1/masters/customers/01100", json={"name_line_1": "X"})
        assert resp.status_code == 403

    def test_delete_requires_admin(self):
        client = _client_as(_NON_ADMIN)
        resp = client.delete("/api/v1/masters/customers/01100")
        assert resp.status_code == 403

    def test_restricted_user_empty_grants_lists_nothing(self, monkeypatch):
        """A non-admin with ZERO grants gets an empty, fail-closed list (no DB hit)."""
        import app.routers.masters as m
        monkeypatch.setattr(m, "visible_entity_codes", lambda s, u: set())
        session = MagicMock()
        client = _client_as(_NON_ADMIN, session=session)
        resp = client.get("/api/v1/masters/customers")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"rows": [], "total": 0}
        # deny-all short-circuits before any query
        session.execute.assert_not_called()

    def test_restricted_list_scopes_to_visible_prefixes(self, monkeypatch):
        """A restricted user's list query is scoped to their resolved prefixes."""
        import app.routers.masters as m
        monkeypatch.setattr(m, "visible_entity_codes", lambda s, u: {"ATLAS"})
        captured: list[dict] = []

        class _Result:
            def __init__(self, kind):
                self.kind = kind

            def scalar(self):
                return 0

            def mappings(self):
                return self

            def fetchall(self):
                return []

        class _Session:
            def execute(self, stmt, params=None):
                sql = str(stmt)
                captured.append({"sql": sql, "params": params})
                if "DISTINCT entity_prefix" in sql:
                    # prefix resolution for ATLAS → 01
                    return _PrefixResult()
                return _Result("data")

        class _PrefixResult:
            def fetchall(self):
                return [("01",)]

        client = _client_as(_NON_ADMIN, session=_Session())
        resp = client.get("/api/v1/masters/customers")
        assert resp.status_code == 200, resp.text
        # The data queries must carry the scope param = ['01'].
        data_calls = [c for c in captured if "FROM dim_customer" in c["sql"]]
        assert data_calls, "expected dim_customer queries"
        assert all("entity_prefix = ANY(:scope)" in c["sql"] for c in data_calls)
        assert data_calls[0]["params"]["scope"] == ["01"]

    def test_post_admin_bad_prefix_422(self, monkeypatch):
        import app.routers.masters as m
        monkeypatch.setattr(m, "visible_entity_codes", lambda s, u: None)
        client = _client_as(_ADMIN)
        resp = client.post("/api/v1/masters/customers", json={
            "entity_prefix": "ABC", "number": "100", "name_line_1": "X",
        })
        assert resp.status_code == 422
        assert "prefix" in resp.json()["detail"].lower()

    def test_post_restricted_out_of_scope_403(self, monkeypatch):
        """A restricted user may not POST into an entity outside their grants."""
        import app.routers.masters as m
        # grants resolve to {'01'}; request targets '02'
        monkeypatch.setattr(m, "_visible_prefixes_or_none", lambda s, u: {"01"})
        # require_admin override would block a non-admin, so test the admin path
        # with a restricted visibility set (admin can still be entity-scoped).
        client = _client_as(_ADMIN)
        resp = client.post("/api/v1/masters/customers", json={
            "entity_prefix": "02", "number": "100", "name_line_1": "X",
        })
        assert resp.status_code == 403


class TestIdConstruction:
    """The single-row POST must build the SAME id the ETL builds."""

    def test_customer_id_matches_etl(self):
        import pandas as pd
        from etl.transform import build_partner_id, normalize_prefix
        prefix = normalize_prefix("1")  # '01'
        cid = str(build_partner_id(prefix, pd.Series(["10000"])).iloc[0])
        assert prefix == "01"
        assert cid == "0110000"

    def test_supplier_id_matches_etl(self):
        import pandas as pd
        from etl.transform import build_partner_id, normalize_prefix
        prefix = normalize_prefix("2")  # '02'
        sid = str(build_partner_id(prefix, pd.Series(["500"])).iloc[0])
        assert sid == "02500"

    def test_number_float_artifact_normalized(self):
        import pandas as pd
        from etl.transform import build_partner_id
        # Excel '.0' artifact must be stripped (matches normalize_token).
        cid = str(build_partner_id("01", pd.Series(["10000.0"])).iloc[0])
        assert cid == "0110000"


# =========================================================================== #
# B) ROUND-TRIP — live v2 DB (opt-in; auto-clean)
# =========================================================================== #
def _v2_session_or_skip():
    from sqlalchemy import text
    from app.db import SessionLocal
    try:
        s = SessionLocal()
        s.execute(text("SELECT 1 FROM dim_customer LIMIT 1"))
        return s
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"v2 DB not reachable: {exc}")


# Round-trip runs only against a disposable test DB (it writes/cleans prefix '97').
# Enabled for finssentials_v2 (mirrors test_ob_partner_ingest) OR the blank
# finssentials_v4 test DB.  NEVER runs against the live 'Finssentials' DB.
_RT_DBS = {"finssentials_v2", "finssentials_v4"}
_SKIP_RT = os.getenv("DB_NAME", "Finssentials") not in _RT_DBS


@pytest.mark.skipif(_SKIP_RT, reason="round-trip runs only against a disposable test DB (set DB_NAME=finssentials_v4)")
class TestRoundTripV2:
    _PFX = "97"  # test-only entity prefix unlikely to collide with seeded data

    def _clean(self, session):
        from sqlalchemy import text
        for tbl, col in (("dim_customer", "customer_id"), ("dim_supplier", "supplier_id")):
            session.execute(text(
                f"DELETE FROM {tbl} WHERE SUBSTR({col},1,2)=:p"
            ), {"p": self._PFX})
        session.commit()

    def test_post_builds_id_and_upserts_then_coalesce(self):
        from sqlalchemy import text
        import app.routers.masters as m

        session = _v2_session_or_skip()
        client = _client_as(_ADMIN, session=session)
        try:
            self._clean(session)
            # ---- first POST: create ----
            r1 = client.post("/api/v1/masters/customers", json={
                "entity_prefix": self._PFX, "number": "10000",
                "name_line_1": "Test Kunde", "city": "Berlin",
                "country_code": "DEU", "source_system": "manual_entry",
            })
            assert r1.status_code == 200, r1.text
            body = r1.json()
            assert body["customer_id"] == f"{self._PFX}10000"
            assert body["debtor_number"] == "10000"
            assert body["entity_prefix"] == self._PFX
            assert body["name_line_1"] == "Test Kunde"
            assert body["city"] == "Berlin"

            # ---- second POST: COALESCE keeps city when not re-sent, updates name ----
            r2 = client.post("/api/v1/masters/customers", json={
                "entity_prefix": self._PFX, "number": "10000",
                "name_line_1": "Test Kunde II",
            })
            assert r2.status_code == 200, r2.text
            b2 = r2.json()
            assert b2["name_line_1"] == "Test Kunde II"  # updated
            assert b2["city"] == "Berlin"               # preserved via COALESCE
        finally:
            self._clean(session)
            session.close()

    def test_patch_updates_fields(self):
        import app.routers.masters as m

        session = _v2_session_or_skip()
        client = _client_as(_ADMIN, session=session)
        cid = f"{self._PFX}20000"
        try:
            self._clean(session)
            client.post("/api/v1/masters/customers", json={
                "entity_prefix": self._PFX, "number": "20000", "name_line_1": "Before",
            })
            r = client.patch(f"/api/v1/masters/customers/{cid}", json={
                "name_line_1": "After", "postal_code": "10115",
            })
            assert r.status_code == 200, r.text
            b = r.json()
            assert b["name_line_1"] == "After"
            assert b["postal_code"] == "10115"

            # PATCH on a non-existent id → 404
            r404 = client.patch(f"/api/v1/masters/customers/{self._PFX}99999", json={
                "name_line_1": "Nope",
            })
            assert r404.status_code == 404
        finally:
            self._clean(session)
            session.close()

    def test_supplier_post_with_purchasing_org(self):
        session = _v2_session_or_skip()
        client = _client_as(_ADMIN, session=session)
        try:
            self._clean(session)
            r = client.post("/api/v1/masters/suppliers", json={
                "entity_prefix": self._PFX, "number": "30000",
                "name_line_1": "Test Lieferant", "purchasing_org": "ORG1",
            })
            assert r.status_code == 200, r.text
            b = r.json()
            assert b["supplier_id"] == f"{self._PFX}30000"
            assert b["creditor_number"] == "30000"
            assert b["purchasing_org"] == "ORG1"
        finally:
            self._clean(session)
            session.close()

    def _clean_facts(self, session):
        """Remove the seeded GL usage chain (lines -> entries -> account)."""
        from sqlalchemy import text
        session.execute(text(
            "DELETE FROM fact_gl_line WHERE source_system='masters_test'"
        ))
        session.execute(text(
            "DELETE FROM fact_gl_entry WHERE source_system='masters_test'"
        ))
        session.execute(text(
            "DELETE FROM dim_gl_account WHERE source_system='masters_test'"
        ))
        session.commit()

    def test_delete_guard_then_force(self):
        from sqlalchemy import text

        session = _v2_session_or_skip()
        client = _client_as(_ADMIN, session=session)
        cid = f"{self._PFX}40000"
        ang = f"{self._PFX}008000"
        jegn = f"{self._PFX}1000040001"
        try:
            self._clean_facts(session)
            self._clean(session)
            client.post("/api/v1/masters/customers", json={
                "entity_prefix": self._PFX, "number": "40000", "name_line_1": "Ref",
            })
            # Seed a GL usage chain (dim_gl_account -> fact_gl_entry -> fact_gl_line)
            # where fact_gl_line.customer_id references the master (no FK to masters).
            session.execute(text(
                "INSERT INTO dim_gl_account "
                "(account_number_group, fiscal_year, gl_account_id, is_ic, source_system) "
                "VALUES (:ang, 2023, '8000', FALSE, 'masters_test') "
                "ON CONFLICT (account_number_group, fiscal_year) DO NOTHING"
            ), {"ang": ang})
            session.execute(text(
                "INSERT INTO fact_gl_entry "
                "(journal_entry_group_number, fiscal_year, fiscal_period, entry_type, "
                " posting_date, currency_code, source_system) "
                "VALUES (:j, 2023, 1, 'actual', '2023-01-15', 'EUR', 'masters_test') "
                "ON CONFLICT (journal_entry_group_number, fiscal_year) DO NOTHING"
            ), {"j": jegn})
            session.execute(text(
                "INSERT INTO fact_gl_line "
                "(journal_entry_group_number, fiscal_year, line_number, booking_line_id, "
                " account_number_group, amount, customer_id, source_system) "
                "VALUES (:j, 2023, 1, 970000040001, :ang, 100, :cid, 'masters_test') "
                "ON CONFLICT (journal_entry_group_number, fiscal_year, line_number) DO NOTHING"
            ), {"j": jegn, "ang": ang, "cid": cid})
            session.commit()

            # Guarded delete → 409 with usage counts.
            r409 = client.delete(f"/api/v1/masters/customers/{cid}")
            assert r409.status_code == 409, r409.text
            detail = r409.json()["detail"]
            assert detail["usage"]["fact_gl_line"] >= 1
            assert detail["total"] >= 1
            # row still present
            assert client.get(f"/api/v1/masters/customers?search={cid}").json()["total"] == 1

            # Forced delete → 200, row gone.
            r200 = client.delete(f"/api/v1/masters/customers/{cid}?force=true")
            assert r200.status_code == 200, r200.text
            assert r200.json()["deleted"] is True
            assert client.get(f"/api/v1/masters/customers?search={cid}").json()["total"] == 0
        finally:
            self._clean_facts(session)
            self._clean(session)
            session.close()

    def test_list_and_search(self):
        session = _v2_session_or_skip()
        client = _client_as(_ADMIN, session=session)
        try:
            self._clean(session)
            client.post("/api/v1/masters/customers", json={
                "entity_prefix": self._PFX, "number": "50000", "name_line_1": "Findable Co",
            })
            client.post("/api/v1/masters/customers", json={
                "entity_prefix": self._PFX, "number": "50001", "name_line_1": "Other Co",
            })
            # entity filter narrows to the test prefix
            full = client.get(f"/api/v1/masters/customers?entity={self._PFX}").json()
            assert full["total"] == 2
            # name search (case-insensitive)
            found = client.get("/api/v1/masters/customers?search=findable").json()
            assert found["total"] == 1
            assert found["rows"][0]["name_line_1"] == "Findable Co"
            # id search
            byid = client.get(f"/api/v1/masters/customers?search={self._PFX}50001").json()
            assert byid["total"] == 1
        finally:
            self._clean(session)
            session.close()

    def test_ref_shapes(self):
        session = _v2_session_or_skip()
        client = _client_as(_ADMIN, session=session)
        try:
            r = client.get("/api/v1/masters/ref")
            assert r.status_code == 200, r.text
            body = r.json()
            assert "entity_prefixes" in body and "countries" in body
            assert isinstance(body["entity_prefixes"], list)
            assert isinstance(body["countries"], list)
        finally:
            session.close()
