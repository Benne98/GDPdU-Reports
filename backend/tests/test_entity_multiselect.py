"""Phase-7 backend: multi-entity (comma-separated ``entity``) filter parsing +
the reporting-availability meta endpoint.

Strategy (matches test_compat_layer.py): NO live Postgres.  MagicMock sessions
whose ``execute`` dispatches on the SQL text return canned rows, and dependency
overrides inject a dummy authed user + the mock session into the FastAPI app.

The multi-entity tests assert on the SQL fragment + bound params produced by the
service ``_fetch_rows`` choke points (personnel / fixed-assets / OPOS): a single
code keeps the byte-for-byte ``= :ep`` single-prefix path; a comma-list switches
to the ``= ANY(:eps)`` IN-list; empty/'all'/None applies NO entity filter.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.auth import User, current_user
from app.db import get_read_session, get_session
from app.main import app

_DUMMY_USER = User(
    user_id=1, email="test@finssentials.com", display_name="Test", is_admin=True,
)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Mock session that resolves dim_legal_entity codes → prefixes and captures the
# fact-table query SQL + params.
# ---------------------------------------------------------------------------
def _capture_session():
    """(session, captured) — dim_legal_entity resolves each code to its 2-char
    prefix (prefix := code[:2]); the fact query is captured, not executed."""
    captured: dict = {"sql": None, "params": None}
    session = MagicMock()

    def _execute(stmt, params=None):
        sql = str(stmt)
        result = MagicMock()
        if "dim_legal_entity" in sql:
            codes = (params or {}).get("codes", []) or []
            result.fetchall.return_value = [(c[:2],) for c in codes]
            return result
        # any fact-table query — capture and return no rows
        captured["sql"] = sql
        captured["params"] = params
        result.fetchall.return_value = []
        result.fetchone.return_value = None
        return result

    session.execute.side_effect = _execute
    return session, captured


# ---------------------------------------------------------------------------
# (a) Multi-entity parsing — personnel + fixed-assets share the same pattern.
# ---------------------------------------------------------------------------
class TestMultiEntityFetchRows:

    def _cases(self):
        from app.services.fixed_asset_rollforward import _fetch_rows as fa_fetch
        from app.services.personnel_accounting import _fetch_rows as pers_fetch
        return (("personnel", pers_fetch), ("fixed_assets", fa_fetch))

    def test_single_entity_keeps_single_prefix_path(self):
        for name, fetch in self._cases():
            session, cap = _capture_session()
            fetch(session, date(2024, 12, 31), "AT")
            assert "AND entity_prefix = :ep" in cap["sql"], name
            assert "ANY" not in cap["sql"], name
            assert cap["params"]["ep"] == "AT", name
            assert "eps" not in cap["params"], name

    def test_comma_entity_uses_any_in_filter(self):
        for name, fetch in self._cases():
            session, cap = _capture_session()
            fetch(session, date(2024, 12, 31), "AT,DE")
            assert "AND entity_prefix = ANY(:eps)" in cap["sql"], name
            assert cap["params"]["eps"] == ["AT", "DE"], name
            assert "ep" not in cap["params"], name

    def test_empty_all_none_apply_no_filter(self):
        for name, fetch in self._cases():
            for ent in (None, "", "all"):
                session, cap = _capture_session()
                fetch(session, date(2024, 12, 31), ent)
                assert "entity_prefix" not in cap["sql"], f"{name}:{ent!r}"


# ---------------------------------------------------------------------------
# (b) Multi-entity parsing — OPOS aging read layer (fetch_opos_rows).
# ---------------------------------------------------------------------------
class TestOposFetchRowsEntity:

    def test_single_entity_single_prefix(self):
        from app.services.opos_aging import fetch_opos_rows
        session, cap = _capture_session()
        fetch_opos_rows(session, "AR", 2024, 12, "AT")
        assert "AND o.entity_prefix = :ep" in cap["sql"]
        assert cap["params"]["ep"] == "AT"

    def test_comma_entity_any_filter(self):
        from app.services.opos_aging import fetch_opos_rows
        session, cap = _capture_session()
        fetch_opos_rows(session, "AR", 2024, 12, "AT,DE")
        assert "AND o.entity_prefix = ANY(:eps)" in cap["sql"]
        assert cap["params"]["eps"] == ["AT", "DE"]

    def test_no_entity_no_filter(self):
        from app.services.opos_aging import fetch_opos_rows
        session, cap = _capture_session()
        fetch_opos_rows(session, "AR", 2024, 12, None)
        # No entity narrow → no WHERE filter fragment (the SELECT list still
        # projects o.entity_prefix, so assert on the filter predicates only).
        assert "AND o.entity_prefix" not in cap["sql"]
        assert "ep" not in (cap["params"] or {})
        assert "eps" not in (cap["params"] or {})

    def test_visibility_scope_intersects_comma_narrow(self):
        """allowed={AT} ∩ entity='AT,DE' → only AT survives (no cross-entity leak)."""
        from app.services.opos_aging import fetch_opos_rows
        session, cap = _capture_session()
        fetch_opos_rows(session, "AR", 2024, 12, "AT,DE", allowed=frozenset({"AT"}))
        assert "AND o.entity_prefix = ANY(:allowed)" in cap["sql"]
        assert cap["params"]["allowed"] == ["AT"]

    def test_visibility_scope_disjoint_narrow_fails_closed(self):
        """allowed={DE} ∩ entity='AT' → empty → no rows, NO fact SQL issued."""
        from app.services.opos_aging import fetch_opos_rows
        session, cap = _capture_session()
        rows, meta = fetch_opos_rows(
            session, "AR", 2024, 12, "AT", allowed=frozenset({"DE"}),
        )
        assert rows == [] and meta == {}
        assert cap["sql"] is None  # fail-closed before building the fact query


# ---------------------------------------------------------------------------
# (c) Reporting-availability meta endpoint.
# ---------------------------------------------------------------------------
def _avail_session(*, payroll=True, fixed_assets=True, opos=True, raises=False):
    session = MagicMock()

    def _execute(stmt, params=None):
        if raises:
            raise RuntimeError("table does not exist")
        sql = str(stmt)
        result = MagicMock()
        result.fetchall.return_value = []
        result.fetchone.return_value = None
        if "fact_personnel_employee" in sql:
            result.fetchall.return_value = [(date(2024, 12, 31),)] if payroll else []
        elif "fact_fixed_asset" in sql:
            result.fetchall.return_value = [(date(2024, 12, 31),)] if fixed_assets else []
        elif "fact_opos_debitor" in sql:
            result.fetchone.return_value = (bool(opos),)
        elif "fact_opos_kreditor" in sql:
            result.fetchone.return_value = (False,)
        return result

    session.execute.side_effect = _execute
    return session


def _client(session):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_read_session] = lambda: session
    app.dependency_overrides[current_user] = lambda: _DUMMY_USER
    return TestClient(app, raise_server_exceptions=False)


class TestReportingAvailability:

    def test_shape_all_present(self):
        client = _client(_avail_session())
        resp = client.get("/api/v1/meta/reporting-availability")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert set(data) == {"payroll", "fixed_assets", "opos"}
        assert all(isinstance(v, bool) for v in data.values())
        assert data == {"payroll": True, "fixed_assets": True, "opos": True}

    def test_individual_falses(self):
        client = _client(_avail_session(payroll=False, fixed_assets=True, opos=False))
        data = client.get("/api/v1/meta/reporting-availability").json()
        assert data == {"payroll": False, "fixed_assets": True, "opos": False}

    def test_graceful_degradation_to_false_on_error(self):
        """A DB/table error must degrade each probe to False, never 500."""
        client = _client(_avail_session(raises=True))
        resp = client.get("/api/v1/meta/reporting-availability")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"payroll": False, "fixed_assets": False, "opos": False}

    def test_requires_auth(self):
        """Without the auth override the endpoint must not be anonymously readable."""
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/meta/reporting-availability")
        assert resp.status_code in (401, 403)
