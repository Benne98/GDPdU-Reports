"""Tests for per-project config persistence + rebuild-mode selection (Phase 7).

Two layers, both DB-free (sessions are mocked):

  1. API contract — GET/PUT/POST /api/v1/projects/{id} via TestClient with the
     auth/session deps overridden (same pattern as test_versioning.py).  Pins the
     frontend wizard contract, auth-guarding, and enum validation.

  2. Mode-selection logic — ``ingest._select_rebuild_mode`` (auto => incremental
     for an append on already-mapped accounts, full otherwise) and
     ``project_config.resolve_rebuild_flags`` (config drives flags; legacy default
     preserved).

Additive guarantee: the seeded 'default' config mirrors the LEGACY settings
defaults, so resolved flags equal the current behaviour (golden equivalence).
"""
from __future__ import annotations

from unittest.mock import MagicMock

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


# ===========================================================================
# GET /api/v1/projects/{id}
# ===========================================================================
class TestGetProject:
    def test_get_returns_default_shape(self, monkeypatch):
        """Absent/default project returns the full canonical config shape."""
        from app.routers import projects as projects_router
        from etl.project_config import DEFAULT_CONFIG

        record = {
            "project_id": "default",
            "name": "Default project",
            "fy_start_month": 1,
            "config": dict(DEFAULT_CONFIG),
        }
        monkeypatch.setattr(projects_router, "read_project_config", lambda s, p: record)

        client = _client_as(_NON_ADMIN)
        resp = client.get("/api/v1/projects/default")
        assert resp.status_code == 200
        body = resp.json()
        assert body["project_id"] == "default"
        cfg = body["config"]
        # Contract: every wizard field present, legacy defaults preserved.
        assert cfg["opening_balance_mode"] == "in_data"
        assert cfg["net_profit_source"] == "report_inject"
        assert cfg["mapping_source"] == "library"
        assert cfg["partner_master_source"] == "files"
        assert set(cfg) == {
            "entities", "fy_start_month", "opening_balance_mode",
            "net_profit_source", "mapping_source", "partner_master_source",
            "sales_label", "cost_label",
        }

    def test_get_requires_auth(self):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/projects/default")
        assert resp.status_code == 401


# ===========================================================================
# PUT /api/v1/projects/{id}
# ===========================================================================
class TestPutProject:
    def test_put_requires_admin(self):
        client = _client_as(_NON_ADMIN)
        resp = client.put("/api/v1/projects/acme", json={"name": "Acme"})
        assert resp.status_code == 403

    def test_put_upserts_and_returns_config(self, monkeypatch):
        from app.routers import projects as projects_router
        from etl.project_config import DEFAULT_CONFIG

        # read (for merge) returns the default config; upsert echoes what it stored.
        monkeypatch.setattr(
            projects_router, "read_project_config",
            lambda s, p: {"project_id": p, "name": None, "fy_start_month": 1,
                          "config": dict(DEFAULT_CONFIG)},
        )
        captured = {}

        def _fake_upsert(session, project_id, *, name=None, config=None):
            captured["name"] = name
            captured["config"] = config
            return {"project_id": project_id, "name": name,
                    "fy_start_month": config["fy_start_month"], "config": config}

        monkeypatch.setattr(projects_router, "upsert_project_config", _fake_upsert)

        client = _client_as(_ADMIN)
        resp = client.put(
            "/api/v1/projects/acme",
            json={
                "name": "Acme GmbH",
                "fy_start_month": 4,
                "entities": [{"code": "DE01", "prefix": "01", "name": "Acme DE"}],
                "opening_balance_mode": "carry_forward",
                "net_profit_source": "gl_rows",
                "sales_label": "Umsatz",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "Acme GmbH"
        assert body["config"]["fy_start_month"] == 4
        assert body["config"]["opening_balance_mode"] == "carry_forward"
        assert body["config"]["net_profit_source"] == "gl_rows"
        assert body["config"]["sales_label"] == "Umsatz"
        assert body["config"]["entities"][0]["prefix"] == "01"
        # untouched field keeps its default (partial PUT merge)
        assert body["config"]["mapping_source"] == "library"
        assert captured["name"] == "Acme GmbH"

    def test_put_rejects_invalid_opening_balance_mode(self, monkeypatch):
        from app.routers import projects as projects_router
        from etl.project_config import DEFAULT_CONFIG

        monkeypatch.setattr(
            projects_router, "read_project_config",
            lambda s, p: {"project_id": p, "name": None, "fy_start_month": 1,
                          "config": dict(DEFAULT_CONFIG)},
        )
        client = _client_as(_ADMIN)
        resp = client.put("/api/v1/projects/x", json={"opening_balance_mode": "bogus"})
        assert resp.status_code == 422

    def test_put_rejects_invalid_net_profit_source(self, monkeypatch):
        from app.routers import projects as projects_router
        from etl.project_config import DEFAULT_CONFIG

        monkeypatch.setattr(
            projects_router, "read_project_config",
            lambda s, p: {"project_id": p, "name": None, "fy_start_month": 1,
                          "config": dict(DEFAULT_CONFIG)},
        )
        client = _client_as(_ADMIN)
        resp = client.put("/api/v1/projects/x", json={"net_profit_source": "bogus"})
        assert resp.status_code == 422


# ===========================================================================
# POST /api/v1/projects/{id}/rebuild
# ===========================================================================
class TestRebuildEndpoint:
    def test_rebuild_requires_admin(self):
        client = _client_as(_NON_ADMIN)
        resp = client.post("/api/v1/projects/default/rebuild", json={"confirm": True})
        assert resp.status_code == 403

    def test_rebuild_requires_confirm(self):
        client = _client_as(_ADMIN)
        resp = client.post("/api/v1/projects/default/rebuild", json={"confirm": False})
        assert resp.status_code == 422

    def test_rebuild_503_when_disabled(self, monkeypatch):
        from app.routers import projects as projects_router

        monkeypatch.setattr(projects_router.settings, "rebuild_on_commit", False)
        client = _client_as(_ADMIN)
        resp = client.post("/api/v1/projects/default/rebuild", json={"confirm": True})
        assert resp.status_code == 503

    def test_rebuild_runs_when_enabled(self, monkeypatch):
        from app.routers import projects as projects_router
        import etl.rebuild as rebuild_mod

        monkeypatch.setattr(projects_router.settings, "rebuild_on_commit", True)
        called = {}

        def _fake_rebuild(session, scope=None, mode="full", *, project_id=None, commit=True):
            called["mode"] = mode
            called["project_id"] = project_id
            return {"mode": mode, "project_id": project_id, "derived_facts": {}}

        monkeypatch.setattr(rebuild_mod, "rebuild_project", _fake_rebuild)
        client = _client_as(_ADMIN)
        resp = client.post("/api/v1/projects/acme/rebuild", json={"confirm": True})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["mode"] == "full"
        assert body["project_id"] == "acme"
        assert called == {"mode": "full", "project_id": "acme"}


# ===========================================================================
# ingest._select_rebuild_mode — incremental vs full selection
# ===========================================================================
class TestSelectRebuildMode:
    def _session_with_mapped(self, mapped_count: int) -> MagicMock:
        session = MagicMock()
        session.execute.return_value.fetchone.return_value = (mapped_count,)
        return session

    def test_explicit_full_honored(self):
        from app.routers.ingest import _select_rebuild_mode

        s = self._session_with_mapped(999)
        assert _select_rebuild_mode(s, "full", "append", (["01"], [2025])) == "full"

    def test_explicit_incremental_honored(self):
        from app.routers.ingest import _select_rebuild_mode

        s = self._session_with_mapped(0)  # ignored because explicit
        assert _select_rebuild_mode(s, "incremental", "append", (["01"], [2025])) == "incremental"

    def test_auto_replace_is_full(self):
        from app.routers.ingest import _select_rebuild_mode

        s = self._session_with_mapped(999)
        assert _select_rebuild_mode(s, "auto", "replace", (["01"], [2025])) == "full"

    def test_auto_append_mapped_is_incremental(self):
        from app.routers.ingest import _select_rebuild_mode

        s = self._session_with_mapped(42)
        assert _select_rebuild_mode(s, "auto", "append", (["01"], [2025])) == "incremental"

    def test_auto_append_unmapped_is_full(self):
        from app.routers.ingest import _select_rebuild_mode

        s = self._session_with_mapped(0)
        assert _select_rebuild_mode(s, "auto", "append", (["01"], [2025])) == "full"

    def test_auto_empty_scope_is_full(self):
        from app.routers.ingest import _select_rebuild_mode

        s = self._session_with_mapped(42)
        assert _select_rebuild_mode(s, "auto", "append", ([], [])) == "full"

    def test_probe_failure_falls_back_to_full(self):
        from app.routers.ingest import _select_rebuild_mode

        s = MagicMock()
        s.execute.side_effect = RuntimeError("db down")
        assert _select_rebuild_mode(s, "auto", "append", (["01"], [2025])) == "full"


# ===========================================================================
# project_config.resolve_rebuild_flags — config drives flags
# ===========================================================================
class TestResolveRebuildFlags:
    def test_default_config_yields_legacy_flags(self, monkeypatch):
        import etl.project_config as pc

        monkeypatch.setattr(
            pc, "read_project_config",
            lambda s, p: {"project_id": p, "config": dict(pc.DEFAULT_CONFIG)},
        )
        flags = pc.resolve_rebuild_flags(MagicMock(), "default")
        assert flags == {"opening_balance_mode": "in_data",
                         "net_profit_source": "report_inject"}

    def test_config_overrides_flags(self, monkeypatch):
        import etl.project_config as pc

        cfg = dict(pc.DEFAULT_CONFIG)
        cfg["opening_balance_mode"] = "carry_forward"
        cfg["net_profit_source"] = "gl_rows"
        monkeypatch.setattr(
            pc, "read_project_config", lambda s, p: {"project_id": p, "config": cfg}
        )
        flags = pc.resolve_rebuild_flags(MagicMock(), "acme")
        assert flags == {"opening_balance_mode": "carry_forward",
                         "net_profit_source": "gl_rows"}

    def test_invalid_stored_flag_falls_back_to_settings(self, monkeypatch):
        import etl.project_config as pc

        cfg = dict(pc.DEFAULT_CONFIG)
        cfg["opening_balance_mode"] = "garbage"
        monkeypatch.setattr(
            pc, "read_project_config", lambda s, p: {"project_id": p, "config": cfg}
        )
        monkeypatch.setattr(pc, "_settings_defaults", lambda: ("in_data", "report_inject"))
        flags = pc.resolve_rebuild_flags(MagicMock(), "acme")
        assert flags["opening_balance_mode"] == "in_data"
