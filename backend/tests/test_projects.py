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
            "sales_label", "cost_label", "account_mapping_mode",
            "retained_earnings_roll",
        }
        # OPTIONAL retained-earnings roll ships OFF by default (golden parity).
        assert cfg["retained_earnings_roll"] == {
            "enabled": False, "accounts": {}, "opening": {},
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

    def test_put_upserts_entities_into_dim_legal_entity(self, monkeypatch):
        """Phase-2 ordering fix: PUT creates the project's entities up front so the
        later CoA (bs_pl_master) commit can resolve them on a fresh DB."""
        from app.routers import projects as projects_router
        from etl.project_config import DEFAULT_CONFIG

        monkeypatch.setattr(
            projects_router, "read_project_config",
            lambda s, p: {"project_id": p, "name": None, "fy_start_month": 1,
                          "config": dict(DEFAULT_CONFIG)},
        )
        monkeypatch.setattr(
            projects_router, "upsert_project_config",
            lambda s, pid, *, name=None, config=None: {
                "project_id": pid, "name": name,
                "fy_start_month": config["fy_start_month"], "config": config},
        )
        upserted = {}

        def _fake_upsert_entities(session, entities, *, source_system="project_setup"):
            upserted["entities"] = entities
            return [str(e.get("prefix") or e.get("code")) for e in (entities or [])]

        monkeypatch.setattr(projects_router, "upsert_project_entities", _fake_upsert_entities)

        client = _client_as(_ADMIN)
        resp = client.put(
            "/api/v1/projects/acme",
            json={"entities": [{"code": "01", "prefix": "01", "name": "Acme DE"},
                               {"code": "02", "prefix": "02", "name": "Acme AT"}]},
        )
        assert resp.status_code == 200, resp.text
        # entities forwarded to the dim_legal_entity upsert
        assert [e["prefix"] for e in upserted["entities"]] == ["01", "02"]

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
# POST /api/v1/projects/{id}/reset-data — destructive, two-layer gated
# ===========================================================================
class TestResetDataEndpoint:
    """Unit-level (mocked session) coverage of the gating + delete contract.

    Integration coverage (real DELETE against finssentials_v4, KEEP/EMPTY
    invariants) is in the throwaway-worker verification; here we pin the guards
    and the response/contract using a MagicMock session.
    """

    def _session(self, *, dbname: str) -> MagicMock:
        """A mock session whose current_database() returns ``dbname`` and whose
        pg_tables / DELETE calls are stubbed so the loop runs without a real DB."""
        s = MagicMock()

        def _execute(stmt, *a, **kw):
            sql = str(stmt)
            result = MagicMock()
            if "current_database()" in sql:
                result.fetchone.return_value = (dbname,)
            elif "pg_tables" in sql:
                # Pretend every EMPTY-list table exists.
                from app.routers.projects import RESET_EMPTY_TABLES
                result.fetchall.return_value = [(t,) for t in RESET_EMPTY_TABLES]
            elif sql.startswith("DELETE FROM"):
                result.rowcount = 3
            else:
                result.fetchone.return_value = None
                result.fetchall.return_value = []
            return result

        s.execute.side_effect = _execute
        return s

    def test_reset_requires_admin(self, monkeypatch):
        from app.routers import projects as pr

        monkeypatch.setattr(pr.settings, "allow_data_reset", True)
        client = _client_as(_NON_ADMIN)
        resp = client.post("/api/v1/projects/default/reset-data", json={"confirm": True})
        assert resp.status_code == 403

    def test_reset_403_when_env_flag_off(self, monkeypatch):
        from app.routers import projects as pr

        monkeypatch.setattr(pr.settings, "allow_data_reset", False)
        app.dependency_overrides[get_session] = lambda: self._session(dbname="finssentials_v4")
        app.dependency_overrides[current_user] = lambda: _ADMIN
        app.dependency_overrides[require_admin] = lambda: _ADMIN
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/v1/projects/default/reset-data", json={"confirm": True})
        assert resp.status_code == 403
        assert "disabled" in resp.json()["detail"].lower()

    def test_reset_403_hard_refuse_live_db(self, monkeypatch):
        """Belt-and-suspenders: even with the env flag ON, the live DB is refused."""
        from app.routers import projects as pr

        monkeypatch.setattr(pr.settings, "allow_data_reset", True)
        app.dependency_overrides[get_session] = lambda: self._session(dbname="Finssentials")
        app.dependency_overrides[current_user] = lambda: _ADMIN
        app.dependency_overrides[require_admin] = lambda: _ADMIN
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/v1/projects/default/reset-data", json={"confirm": True})
        assert resp.status_code == 403
        assert "live" in resp.json()["detail"].lower()

    def test_reset_403_hard_refuse_live_db_case_insensitive(self, monkeypatch):
        from app.routers import projects as pr

        monkeypatch.setattr(pr.settings, "allow_data_reset", True)
        app.dependency_overrides[get_session] = lambda: self._session(dbname="FINSSENTIALS")
        app.dependency_overrides[current_user] = lambda: _ADMIN
        app.dependency_overrides[require_admin] = lambda: _ADMIN
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/v1/projects/default/reset-data", json={"confirm": True})
        assert resp.status_code == 403

    def test_reset_422_when_confirm_missing(self, monkeypatch):
        from app.routers import projects as pr

        monkeypatch.setattr(pr.settings, "allow_data_reset", True)
        app.dependency_overrides[get_session] = lambda: self._session(dbname="finssentials_v4")
        app.dependency_overrides[current_user] = lambda: _ADMIN
        app.dependency_overrides[require_admin] = lambda: _ADMIN
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/v1/projects/default/reset-data", json={"confirm": False})
        assert resp.status_code == 422

    def test_reset_200_empties_project_tables(self, monkeypatch):
        from app.routers import projects as pr

        monkeypatch.setattr(pr.settings, "allow_data_reset", True)
        app.dependency_overrides[get_session] = lambda: self._session(dbname="finssentials_v4")
        app.dependency_overrides[current_user] = lambda: _ADMIN
        app.dependency_overrides[require_admin] = lambda: _ADMIN
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/v1/projects/default/reset-data", json={"confirm": True})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["database"] == "finssentials_v4"
        # every EMPTY-list table reported with a rowcount; no KEEP table present.
        assert set(body["deleted"]) == set(pr.RESET_EMPTY_TABLES)
        assert all(v == 3 for v in body["deleted"].values())
        keep = set(pr.RESET_KEEP_TABLES)
        assert keep.isdisjoint(set(body["deleted"]))

    def test_empty_and_keep_lists_are_disjoint(self):
        """Static safety invariant: no table is both emptied and kept."""
        from app.routers import projects as pr

        assert set(pr.RESET_EMPTY_TABLES).isdisjoint(set(pr.RESET_KEEP_TABLES))

    def test_keep_list_covers_libraries_admin_auth_structure(self):
        from app.routers import projects as pr

        keep = set(pr.RESET_KEEP_TABLES)
        for t in ("lib_account_mapping", "lib_cf_mapping", "lib_na_mapping",
                  "admin_project_config", "admin_role_page_visibility",
                  "dim_user", "dim_role", "user_role", "auth_session",
                  "dim_pl_structure", "dim_bs_structure", "dim_cf_structure",
                  "dim_project"):
            assert t in keep, t

    def test_capability_flag_true_on_nonlive_when_enabled(self, monkeypatch):
        from app.routers import projects as pr
        from etl.project_config import DEFAULT_CONFIG

        monkeypatch.setattr(pr.settings, "allow_data_reset", True)
        monkeypatch.setattr(
            pr, "read_project_config",
            lambda s, p: {"project_id": p, "name": None, "fy_start_month": 1,
                          "config": dict(DEFAULT_CONFIG)},
        )
        app.dependency_overrides[get_session] = lambda: self._session(dbname="finssentials_v4")
        app.dependency_overrides[current_user] = lambda: _ADMIN
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/projects/default")
        assert resp.status_code == 200
        assert resp.json()["data_reset_allowed"] is True

    def test_capability_flag_false_on_live_db(self, monkeypatch):
        from app.routers import projects as pr
        from etl.project_config import DEFAULT_CONFIG

        monkeypatch.setattr(pr.settings, "allow_data_reset", True)
        monkeypatch.setattr(
            pr, "read_project_config",
            lambda s, p: {"project_id": p, "name": None, "fy_start_month": 1,
                          "config": dict(DEFAULT_CONFIG)},
        )
        app.dependency_overrides[get_session] = lambda: self._session(dbname="Finssentials")
        app.dependency_overrides[current_user] = lambda: _ADMIN
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/projects/default")
        assert resp.status_code == 200
        assert resp.json()["data_reset_allowed"] is False

    def test_capability_flag_false_when_env_off(self, monkeypatch):
        from app.routers import projects as pr
        from etl.project_config import DEFAULT_CONFIG

        monkeypatch.setattr(pr.settings, "allow_data_reset", False)
        monkeypatch.setattr(
            pr, "read_project_config",
            lambda s, p: {"project_id": p, "name": None, "fy_start_month": 1,
                          "config": dict(DEFAULT_CONFIG)},
        )
        app.dependency_overrides[get_session] = lambda: self._session(dbname="finssentials_v4")
        app.dependency_overrides[current_user] = lambda: _ADMIN
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/projects/default")
        assert resp.status_code == 200
        assert resp.json()["data_reset_allowed"] is False


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
                         "net_profit_source": "report_inject",
                         "account_mapping_mode": "library"}

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
                         "net_profit_source": "gl_rows",
                         "account_mapping_mode": "library"}

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


# ===========================================================================
# project_config.upsert_project_entities — fresh-DB entity creation
# ===========================================================================
class TestUpsertProjectEntities:
    def test_upserts_normalized_prefixes(self, monkeypatch):
        import etl.project_config as pc
        import etl.load as load_mod

        calls = []
        monkeypatch.setattr(
            load_mod, "load_legal_entity",
            lambda session, *, entity_prefix, entity_name, source_system=None, **kw:
                calls.append((entity_prefix, entity_name)),
        )
        prefixes = pc.upsert_project_entities(
            MagicMock(),
            [{"code": "01", "prefix": "1", "name": "Acme DE"},
             {"code": "02", "prefix": "", "name": "Acme AT"}],  # prefix falls back to code
        )
        assert prefixes == ["01", "02"]
        assert calls == [("01", "Acme DE"), ("02", "Acme AT")]

    def test_empty_list_is_noop(self):
        import etl.project_config as pc

        assert pc.upsert_project_entities(MagicMock(), None) == []
        assert pc.upsert_project_entities(MagicMock(), []) == []

    def test_non_numeric_entity_skipped(self, monkeypatch):
        import etl.project_config as pc
        import etl.load as load_mod

        calls = []
        monkeypatch.setattr(
            load_mod, "load_legal_entity",
            lambda session, *, entity_prefix, entity_name, source_system=None, **kw:
                calls.append(entity_prefix),
        )
        # 'DEXX' is non-numeric → cannot be a prefix → skipped (no crash)
        prefixes = pc.upsert_project_entities(
            MagicMock(), [{"code": "DEXX", "prefix": "", "name": "Bad"}]
        )
        assert prefixes == []
        assert calls == []
