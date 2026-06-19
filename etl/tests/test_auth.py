"""P4 Auth — pure unit tests for auth.py.

NO live DB, NO network. All tests run with the default venv.

Coverage:
  - hash_password / verify_password: roundtrip and rejection.
  - create_access_token / decode_token: roundtrip, expired token, tampered token.
  - current_user / require_admin: dependency behaviour via FastAPI TestClient +
    app.dependency_overrides (no DB required).
  - Auth router: login/me/logout routes registered in app.main.
  - Protected endpoint returns 401 when no auth token supplied.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make backend/ importable from the repo root (pytest.ini sets pythonpath=.).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


# ===========================================================================
# Password hashing
# ===========================================================================

class TestPasswordHashing:
    def test_hash_returns_bcrypt_prefix(self):
        from app.auth import hash_password
        h = hash_password("secret123")
        assert h.startswith("$2b$") or h.startswith("$2a$")

    def test_verify_correct_password_returns_true(self):
        from app.auth import hash_password, verify_password
        h = hash_password("my-password")
        assert verify_password("my-password", h) is True

    def test_verify_wrong_password_returns_false(self):
        from app.auth import hash_password, verify_password
        h = hash_password("correct-horse")
        assert verify_password("battery-staple", h) is False

    def test_verify_empty_password_returns_false(self):
        from app.auth import hash_password, verify_password
        h = hash_password("notempty")
        assert verify_password("", h) is False

    def test_verify_invalid_hash_returns_false_never_raises(self):
        """verify_password must not propagate passlib errors."""
        from app.auth import verify_password
        assert verify_password("anything", "not-a-hash") is False

    def test_two_hashes_of_same_password_are_distinct(self):
        """bcrypt uses a random salt each time."""
        from app.auth import hash_password
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2


# ===========================================================================
# JWT — create / decode
# ===========================================================================

class TestJWT:
    def _make_token(self, user_id: int = 42, minutes: int = 30) -> tuple[str, uuid.UUID, datetime]:
        from app.auth import create_access_token
        sid = uuid.uuid4()
        exp = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        token = create_access_token(user_id, sid, exp)
        return token, sid, exp

    def test_roundtrip_claims(self):
        from app.auth import decode_token
        token, sid, _ = self._make_token(user_id=7)
        claims = decode_token(token)
        assert claims["sub"] == "7"
        assert claims["sid"] == str(sid)

    def test_expired_token_raises_expired_signature_error(self):
        from jose import ExpiredSignatureError
        from app.auth import create_access_token, decode_token
        sid = uuid.uuid4()
        exp = datetime.now(timezone.utc) - timedelta(seconds=1)  # already expired
        token = create_access_token(user_id=1, session_id=sid, expires=exp)
        with pytest.raises(ExpiredSignatureError):
            decode_token(token)

    def test_tampered_token_raises_jwt_error(self):
        from jose import JWTError
        from app.auth import decode_token
        token, _, _ = self._make_token()
        # Flip a character in the signature part
        parts = token.split(".")
        parts[-1] = parts[-1][:-3] + "XXX"
        bad_token = ".".join(parts)
        with pytest.raises(JWTError):
            decode_token(bad_token)

    def test_token_with_wrong_secret_raises_jwt_error(self):
        from jose import JWTError, jwt
        from app.auth import decode_token
        sid = uuid.uuid4()
        exp = datetime.now(timezone.utc) + timedelta(minutes=60)
        # Encode with a different secret
        bad_token = jwt.encode(
            {"sub": "1", "sid": str(sid), "exp": exp},
            "wrong-secret",
            algorithm="HS256",
        )
        with pytest.raises(JWTError):
            decode_token(bad_token)

    def test_short_lived_token_still_valid_before_expiry(self):
        from app.auth import decode_token
        token, sid, _ = self._make_token(minutes=5)
        claims = decode_token(token)
        assert claims["sub"] == "42"


# ===========================================================================
# FastAPI dependency overrides — no DB needed
# ===========================================================================

def _make_app_with_override(user_override=None, admin_override=None):
    """Return the FastAPI app with current_user and require_admin overridden."""
    import app.main as m
    from app.auth import User, current_user, require_admin

    _active_user = User(user_id=1, email="test@example.com", display_name="Test", is_admin=False)
    _admin_user = User(user_id=2, email="admin@example.com", display_name="Admin", is_admin=True)

    m.app.dependency_overrides[current_user] = user_override or (lambda: _active_user)
    m.app.dependency_overrides[require_admin] = admin_override or (lambda: _admin_user)
    return m.app


def _clear_overrides():
    import app.main as m
    m.app.dependency_overrides.clear()


# ===========================================================================
# Auth routes registered
# ===========================================================================

class TestAuthRoutesRegistered:
    def test_login_route_exists(self):
        import app.main as m
        paths = [r.path for r in m.app.routes]
        assert "/api/v1/auth/login" in paths

    def test_me_route_exists(self):
        import app.main as m
        paths = [r.path for r in m.app.routes]
        assert "/api/v1/auth/me" in paths

    def test_logout_route_exists(self):
        import app.main as m
        paths = [r.path for r in m.app.routes]
        assert "/api/v1/auth/logout" in paths


# ===========================================================================
# Protected endpoints reject unauthenticated requests
# ===========================================================================

class TestProtectedEndpoints:
    """Verify that auth-protected endpoints return 401 when no token is supplied.

    We do NOT override the dependency here — we want the real dependency to fire
    and reject the unauthenticated request. The real current_user will try to
    query the DB, but HTTPBearer returns None before any DB call when the header
    is missing, so it raises 401 immediately.
    """

    def setup_method(self):
        _clear_overrides()

    def teardown_method(self):
        _clear_overrides()

    def test_ingest_runs_requires_auth(self):
        from fastapi.testclient import TestClient
        import app.main as m
        client = TestClient(m.app, raise_server_exceptions=False)
        r = client.get("/api/v1/ingest/runs")
        assert r.status_code == 401

    def test_ingest_profiles_get_requires_auth(self):
        from fastapi.testclient import TestClient
        import app.main as m
        client = TestClient(m.app, raise_server_exceptions=False)
        r = client.get("/api/v1/ingest/profiles")
        assert r.status_code == 401

    def test_ingest_commit_requires_auth(self):
        from fastapi.testclient import TestClient
        import app.main as m
        client = TestClient(m.app, raise_server_exceptions=False)
        r = client.post("/api/v1/ingest/commit", json={})
        assert r.status_code == 401

    def test_plan_summary_requires_auth(self):
        from fastapi.testclient import TestClient
        import app.main as m
        client = TestClient(m.app, raise_server_exceptions=False)
        r = client.get("/api/v1/plan/summary")
        assert r.status_code == 401

    def test_plan_generate_requires_auth(self):
        from fastapi.testclient import TestClient
        import app.main as m
        client = TestClient(m.app, raise_server_exceptions=False)
        r = client.post("/api/v1/plan/generate", json={})
        assert r.status_code == 401

    def test_health_is_open(self):
        """GET /health must NOT require auth."""
        from fastapi.testclient import TestClient
        import app.main as m
        client = TestClient(m.app, raise_server_exceptions=False)
        r = client.get("/api/v1/health")
        # 200 (DB up) or 200 with db:false — either way, not 401
        assert r.status_code == 200

    def test_login_is_open(self):
        """POST /auth/login must NOT require auth (but will 422 on missing body)."""
        from fastapi.testclient import TestClient
        import app.main as m
        client = TestClient(m.app, raise_server_exceptions=False)
        r = client.post("/api/v1/auth/login", json={})
        # 422 = Pydantic validation error (missing email/password), not 401
        assert r.status_code == 422


# ===========================================================================
# Non-admin user is rejected from admin-only endpoints
# ===========================================================================

class TestRequireAdmin:
    """require_admin returns 403 for authenticated non-admin users."""

    def setup_method(self):
        _clear_overrides()

    def teardown_method(self):
        _clear_overrides()

    def test_non_admin_cannot_commit(self):
        from fastapi.testclient import TestClient
        import app.main as m
        from app.auth import User, current_user, require_admin

        non_admin = User(user_id=99, email="u@example.com", display_name=None, is_admin=False)
        # Override only current_user; leave require_admin as the real dep
        m.app.dependency_overrides[current_user] = lambda: non_admin
        # Ensure require_admin is NOT overridden so it fires for real
        m.app.dependency_overrides.pop(require_admin, None)

        client = TestClient(m.app, raise_server_exceptions=False)
        # require_admin depends on current_user, so our override propagates
        r = client.post("/api/v1/ingest/commit", json={
            "file_id": "fake", "profile": {}, "dataset": "gl",
        })
        assert r.status_code == 403
        _clear_overrides()

    def test_admin_user_passes_require_admin(self):
        """An admin user gets past require_admin (may fail later for other reasons)."""
        from fastapi.testclient import TestClient
        import app.main as m
        from app.auth import User, current_user, require_admin

        admin = User(user_id=1, email="a@example.com", display_name=None, is_admin=True)
        m.app.dependency_overrides[current_user] = lambda: admin
        m.app.dependency_overrides[require_admin] = lambda: admin

        client = TestClient(m.app, raise_server_exceptions=False)
        # Will fail with 422 (missing file_id resolution) or 404 — NOT 401/403
        r = client.post("/api/v1/ingest/commit", json={
            "file_id": "nonexistent", "profile": {}, "dataset": "gl",
        })
        assert r.status_code not in (401, 403)
        _clear_overrides()


# ===========================================================================
# /me endpoint with overridden current_user
# ===========================================================================

class TestMeEndpoint:
    def setup_method(self):
        _clear_overrides()

    def teardown_method(self):
        _clear_overrides()

    def test_me_returns_user_data(self):
        from fastapi.testclient import TestClient
        import app.main as m
        from app.auth import User, current_user

        user = User(user_id=5, email="user@test.com", display_name="Alice", is_admin=False)
        m.app.dependency_overrides[current_user] = lambda: user

        client = TestClient(m.app)
        r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer faketoken"})
        assert r.status_code == 200
        data = r.json()
        assert data["user_id"] == 5
        assert data["email"] == "user@test.com"
        assert data["display_name"] == "Alice"
        assert data["is_admin"] is False
        _clear_overrides()


# ===========================================================================
# Fix 2 regression — _DUMMY_HASH is a well-formed bcrypt hash
# ===========================================================================

class TestDummyHashWellFormed:
    """_DUMMY_HASH must be a real bcrypt hash so verify_password never raises."""

    def test_dummy_hash_has_bcrypt_prefix(self):
        from app.routers.auth import _DUMMY_HASH
        assert _DUMMY_HASH.startswith("$2b$") or _DUMMY_HASH.startswith("$2a$"), (
            f"_DUMMY_HASH must be a real bcrypt hash; got: {_DUMMY_HASH[:20]!r}"
        )

    def test_verify_password_against_dummy_hash_returns_false_not_raises(self):
        """verify_password with the dummy hash must return False for any user-submitted
        password and must NOT raise — proves the hash is well-formed and the not-found
        code path pays a full bcrypt cycle instead of short-circuiting on a bad hash."""
        from app.auth import verify_password
        from app.routers.auth import _DUMMY_HASH
        # Arbitrary submitted passwords must not raise and must not match
        assert verify_password("anything", _DUMMY_HASH) is False
        assert verify_password("", _DUMMY_HASH) is False
        # A correct bcrypt verify call returns True for the exact input used to
        # produce the hash.  That's fine — no attacker submits "timing-equalizer".
        # The key property is that False is returned for realistic inputs and that
        # no exception is thrown (the old malformed hash raised immediately).


# ===========================================================================
# Fix 1 regression — Settings guard rejects default secret outside dev
# ===========================================================================

class TestSettingsGuard:
    """Settings must raise RuntimeError when APP_ENV != 'dev' and AUTH_SECRET is default."""

    def test_non_dev_env_with_default_secret_raises(self, monkeypatch):
        import importlib
        import app.config as cfg_module

        # Patch env so pydantic-settings reads non-dev APP_ENV and default secret
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.delenv("AUTH_SECRET", raising=False)

        with pytest.raises((RuntimeError, Exception)) as exc_info:
            # Re-instantiate Settings directly (avoids mutating the module singleton)
            cfg_module.Settings()

        assert "AUTH_SECRET" in str(exc_info.value)

    def test_dev_env_with_default_secret_allowed(self, monkeypatch):
        import app.config as cfg_module

        monkeypatch.setenv("APP_ENV", "dev")
        monkeypatch.delenv("AUTH_SECRET", raising=False)

        # Should not raise
        s = cfg_module.Settings()
        assert s.auth_secret == cfg_module._DEFAULT_SECRET

    def test_non_dev_env_with_custom_secret_allowed(self, monkeypatch):
        import app.config as cfg_module

        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("AUTH_SECRET", "a-very-strong-production-secret-value")

        # Should not raise
        s = cfg_module.Settings()
        assert s.auth_secret == "a-very-strong-production-secret-value"


# ===========================================================================
# Fix 3 regression — POST /ingest/upload requires authentication
# ===========================================================================

class TestUploadRequiresAuth:
    """POST /ingest/upload must return 401 when no auth token is provided."""

    def setup_method(self):
        _clear_overrides()

    def teardown_method(self):
        _clear_overrides()

    def test_upload_without_token_returns_401(self):
        import io
        from fastapi.testclient import TestClient
        import app.main as m

        client = TestClient(m.app, raise_server_exceptions=False)
        r = client.post(
            "/api/v1/ingest/upload",
            files={"file": ("test.csv", io.BytesIO(b"col\nval"), "text/csv")},
        )
        assert r.status_code == 401
