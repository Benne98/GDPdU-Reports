"""API tests for ingest version history + restore (mocked DB)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.auth import User, current_user, require_admin
from app.db import get_session
from app.main import app

_ADMIN = User(user_id=1, email="admin@test", display_name="Admin", is_admin=True)
_NON_ADMIN = User(user_id=2, email="user@test", display_name="User", is_admin=False)


def _client_as(user: User) -> TestClient:
    mock_session = MagicMock()
    app.dependency_overrides[get_session] = lambda: mock_session
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


class TestRestoreVersion:
    def test_restore_requires_admin(self):
        client = _client_as(_NON_ADMIN)
        resp = client.post(
            "/api/v1/ingest/versions/1/restore",
            json={"confirm": True},
        )
        assert resp.status_code == 403

    def test_restore_404_when_load_missing(self):
        client = _client_as(_ADMIN)
        with patch("etl.versioning.get_load_meta", return_value=None):
            resp = client.post(
                "/api/v1/ingest/versions/99/restore",
                json={"confirm": True},
            )
        assert resp.status_code == 404

    def test_restore_409_without_snapshot(self):
        client = _client_as(_ADMIN)
        meta = {
            "load_id": 5,
            "dataset": "gl",
            "scope_entity_prefixes": ["01"],
            "scope_fiscal_years": [2024],
            "commit_mode": "replace",
            "snapshot_captured": False,
            "row_count": 100,
            "loaded_at": "2026-01-01T00:00:00Z",
            "loaded_by": "admin@test",
        }
        with patch("etl.versioning.get_load_meta", return_value=meta):
            resp = client.post(
                "/api/v1/ingest/versions/5/restore",
                json={"confirm": True},
            )
        assert resp.status_code == 409

    def test_restore_requires_confirm(self):
        client = _client_as(_ADMIN)
        meta = {
            "load_id": 5,
            "dataset": "gl",
            "scope_entity_prefixes": ["01"],
            "scope_fiscal_years": [2024],
            "commit_mode": "replace",
            "snapshot_captured": True,
            "row_count": 100,
            "loaded_at": "2026-01-01T00:00:00Z",
            "loaded_by": "admin@test",
        }
        with patch("etl.versioning.get_load_meta", return_value=meta):
            resp = client.post(
                "/api/v1/ingest/versions/5/restore",
                json={"confirm": False},
            )
        assert resp.status_code == 422
