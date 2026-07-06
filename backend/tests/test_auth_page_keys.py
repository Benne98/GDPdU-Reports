"""Tests for auth router — UserOut.page_keys serialization and _load_page_keys helper.

Three layers, all DB-free (sessions are mocked or bypassed):

  1. Pydantic model — UserOut serialises page_keys correctly (default []).
  2. Unit — _load_page_keys returns [] fail-open on exception / no rows;
             returns the correct list when mock session yields rows.
  3. API contract — GET /me includes page_keys in the response JSON; fail-open
             under DB error; auth-gated (401 when no token).

Coverage gap (consciously deferred): _load_page_keys with a real DB executing the
JOIN against user_role + admin_role_page_visibility — no live DB fixture is wired
in the backend test suite at this tier; omitted in favour of the unit-level
mock-session coverage above.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.auth import User, current_user
from app.db import get_session
from app.main import app
from app.routers.auth import UserOut, _load_page_keys

_USER = User(user_id=42, email="tester@test.com", display_name="Tester", is_admin=False)
_ADMIN = User(user_id=1, email="admin@test.com", display_name="Admin", is_admin=True)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# ===========================================================================
# 1. UserOut serialization
# ===========================================================================

class TestUserOutModel:
    def test_default_page_keys_is_empty_list(self):
        """Field default must be [] — not None, not missing."""
        out = UserOut(user_id=1, email="a@b.com", display_name=None, is_admin=False)
        assert out.page_keys == []

    def test_model_dump_includes_page_keys_field(self):
        """model_dump() must contain the page_keys key (JSON contract)."""
        out = UserOut(user_id=1, email="a@b.com", display_name=None, is_admin=False)
        data = out.model_dump()
        assert "page_keys" in data
        assert data["page_keys"] == []

    def test_page_keys_preserved_when_explicitly_set(self):
        out = UserOut(
            user_id=2,
            email="b@c.com",
            display_name="Bob",
            is_admin=True,
            page_keys=["overview", "fdd-bot"],
        )
        assert out.page_keys == ["overview", "fdd-bot"]
        data = out.model_dump()
        assert data["page_keys"] == ["overview", "fdd-bot"]

    def test_page_keys_single_entry(self):
        out = UserOut(user_id=3, email="c@d.com", display_name=None, is_admin=False,
                      page_keys=["ingestion"])
        assert out.page_keys == ["ingestion"]


# ===========================================================================
# 2. _load_page_keys unit — fail-open contract and happy path
# ===========================================================================

class TestLoadPageKeys:
    def _session_raising(self, exc: Exception) -> MagicMock:
        s = MagicMock()
        s.execute.side_effect = exc
        return s

    def _session_with_rows(self, keys: list[str]) -> MagicMock:
        s = MagicMock()
        result = MagicMock()
        result.fetchall.return_value = [(k,) for k in keys]
        s.execute.return_value = result
        return s

    # --- fail-open on exception types ---

    def test_returns_empty_on_runtime_error(self):
        s = self._session_raising(RuntimeError("db gone"))
        assert _load_page_keys(s, user_id=99) == []

    def test_returns_empty_on_attribute_error(self):
        s = self._session_raising(AttributeError("no attr"))
        assert _load_page_keys(s, user_id=99) == []

    def test_returns_empty_on_exception_base(self):
        s = self._session_raising(Exception("unexpected"))
        assert _load_page_keys(s, user_id=99) == []

    # --- no rows ---

    def test_returns_empty_when_no_rows(self):
        s = self._session_with_rows([])
        assert _load_page_keys(s, user_id=99) == []

    # --- happy path ---

    def test_returns_keys_from_rows(self):
        s = self._session_with_rows(["overview", "fdd-bot"])
        result = _load_page_keys(s, user_id=42)
        assert result == ["overview", "fdd-bot"]

    def test_returns_single_key(self):
        s = self._session_with_rows(["ingestion"])
        assert _load_page_keys(s, user_id=5) == ["ingestion"]

    def test_returns_multiple_keys(self):
        keys = ["overview", "fdd-bot", "ingestion", "role-management"]
        s = self._session_with_rows(keys)
        assert _load_page_keys(s, user_id=10) == keys

    # --- query correctness ---

    def test_execute_called_with_correct_user_id(self):
        """The SQL query must receive the user_id as the :uid param."""
        s = self._session_with_rows([])
        _load_page_keys(s, user_id=77)
        s.execute.assert_called_once()
        call_args = s.execute.call_args
        # Second positional arg is the params dict: execute(text_stmt, {"uid": ...})
        params = call_args[0][1]
        assert params.get("uid") == 77

    def test_never_raises(self):
        """The function must never propagate an exception — not even unusual ones."""
        s = MagicMock()
        s.execute.side_effect = MemoryError("oom")
        # Must return [] rather than re-raise
        result = _load_page_keys(s, user_id=1)
        assert result == []


# ===========================================================================
# 3. GET /me API contract
# ===========================================================================

class TestMeEndpoint:
    def _session_yielding(self, keys: list[str]) -> MagicMock:
        """A mock session whose execute().fetchall() returns the given page_key rows."""
        s = MagicMock()
        result = MagicMock()
        result.fetchall.return_value = [(k,) for k in keys]
        s.execute.return_value = result
        return s

    def test_me_requires_auth(self):
        """GET /me without a token must return 401."""
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    def test_me_returns_page_keys_field_when_empty(self):
        """GET /me always includes page_keys in the JSON body, even when []."""
        app.dependency_overrides[current_user] = lambda: _USER
        app.dependency_overrides[get_session] = lambda: self._session_yielding([])
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 200
        body = resp.json()
        assert "page_keys" in body
        assert body["page_keys"] == []

    def test_me_returns_populated_page_keys(self):
        """GET /me reflects page_keys returned by _load_page_keys."""
        app.dependency_overrides[current_user] = lambda: _USER
        app.dependency_overrides[get_session] = lambda: self._session_yielding(
            ["overview", "fdd-bot"]
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 200
        body = resp.json()
        assert body["page_keys"] == ["overview", "fdd-bot"]

    def test_me_is_fail_open_on_db_error(self):
        """GET /me must return 200 with page_keys=[] even when the DB query raises."""
        s = MagicMock()
        s.execute.side_effect = RuntimeError("connection lost")
        app.dependency_overrides[current_user] = lambda: _USER
        app.dependency_overrides[get_session] = lambda: s
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 200
        assert resp.json()["page_keys"] == []

    def test_me_response_includes_all_user_fields(self):
        """GET /me response includes every UserOut field (user_id, email, is_admin, page_keys)."""
        app.dependency_overrides[current_user] = lambda: _ADMIN
        app.dependency_overrides[get_session] = lambda: self._session_yielding(
            ["role-management"]
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_id"] == 1
        assert body["email"] == "admin@test.com"
        assert body["is_admin"] is True
        assert body["display_name"] == "Admin"
        assert body["page_keys"] == ["role-management"]

    def test_me_non_admin_user_fields(self):
        """GET /me for a non-admin user serializes is_admin as False."""
        app.dependency_overrides[current_user] = lambda: _USER
        app.dependency_overrides[get_session] = lambda: self._session_yielding([])
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_id"] == 42
        assert body["is_admin"] is False
        assert body["page_keys"] == []
