"""Phase 4 — manual-budget API tests.

Two layers:

  A. CONTRACT (DB-free): TestClient with auth/session deps overridden and the
     budget_service monkeypatched.  Pins admin-gating (writes 403 for non-admins,
     401 unauthenticated), GET pure-read (never calls a write service fn), request
     validation, and the response shapes.

  B. ROUND-TRIP (live v2 DB, opt-in): seed → PUT → GET reflects → Σ invariant →
     DELETE reverts.  SKIPPED unless the v2 DB is reachable.  ALWAYS cleans up the
     budget rows it writes so the golden ``compare live v2`` stays EQUIVALENT.
     Enable by pointing the env at finssentials_v2 (DB_NAME=finssentials_v2,
     DB_PASSWORD=...).
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
# A) CONTRACT — DB-free
# =========================================================================== #
class TestContract:
    def test_get_requires_auth(self):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/budget?statement=PL&fiscal_year=2025")
        assert resp.status_code == 401

    def test_get_is_pure_read(self, monkeypatch):
        """GET calls build_grid only — never a write service function."""
        from app.services import budget_service

        grid = {
            "statement": "PL", "fiscal_year": 2025, "entity": "", "top_n": 20,
            "level": "L4",
            "positions": [{
                "line_code": "NET_SALES", "label": "Net sales", "level_3": "Net sales",
                "annual": 1200.0, "months": [100.0] * 12,
                "synthetic_annual": 1200.0, "is_partner_driven": True,
                "partners": [], "other": 1200.0,
            }],
        }
        monkeypatch.setattr(budget_service, "build_grid", lambda *a, **k: grid)
        for fn in ("upsert_cell", "patch_position", "delete_budget", "seed_budget"):
            monkeypatch.setattr(budget_service, fn, MagicMock(side_effect=AssertionError(f"{fn} called on GET")))

        client = _client_as(_ADMIN)  # admin may READ consolidated
        resp = client.get("/api/v1/budget?statement=PL&fiscal_year=2025")
        assert resp.status_code == 200
        body = resp.json()
        assert body["positions"][0]["line_code"] == "NET_SALES"
        assert body["positions"][0]["is_partner_driven"] is True

    def test_get_invalid_statement_422(self, monkeypatch):
        from app.services import budget_service
        monkeypatch.setattr(budget_service, "build_grid", lambda *a, **k: {})
        client = _client_as(_ADMIN)
        resp = client.get("/api/v1/budget?statement=XX&fiscal_year=2025")
        assert resp.status_code == 422

    def test_put_cell_requires_admin(self, monkeypatch):
        from app.services import budget_service
        monkeypatch.setattr(budget_service, "upsert_cell", lambda *a, **k: {"rows_upserted": 12})
        client = _client_as(_NON_ADMIN)
        resp = client.put("/api/v1/budget/cell", json={
            "statement": "PL", "line_code": "NET_SALES", "fiscal_year": 2025,
            "annual": 1200.0,
        })
        assert resp.status_code == 403

    def test_put_cell_admin_ok(self, monkeypatch):
        from app.services import budget_service
        called = {}

        def _fake(session, **kw):
            called.update(kw)
            return {"rows_upserted": 12}

        monkeypatch.setattr(budget_service, "upsert_cell", _fake)
        client = _client_as(_ADMIN)
        resp = client.put("/api/v1/budget/cell", json={
            "statement": "PL", "line_code": "NET_SALES", "fiscal_year": 2025,
            "annual": 1200.0,
        })
        assert resp.status_code == 200
        assert resp.json()["rows_upserted"] == 12
        assert called["updated_by"] == "admin@test"  # user.email stamped

    def test_seed_requires_admin(self, monkeypatch):
        from app.services import budget_service
        monkeypatch.setattr(budget_service, "seed_budget", lambda *a, **k: {"rows_seeded": 24})
        client = _client_as(_NON_ADMIN)
        resp = client.post("/api/v1/budget/seed", json={
            "statement": "PL", "fiscal_year": 2025,
        })
        assert resp.status_code == 403

    def test_seed_default_is_legacy(self, monkeypatch):
        """No heuristic params → seed_budget called with legacy defaults
        (materialize_suggestion=False, prior_year, 0.0) — byte-identical legacy seed."""
        from app.services import budget_service
        seen = {}

        def _fake(session, **kw):
            seen.update(kw)
            return {"rows_seeded": 24}

        monkeypatch.setattr(budget_service, "seed_budget", _fake)
        client = _client_as(_ADMIN)
        resp = client.post("/api/v1/budget/seed", json={
            "statement": "PL", "fiscal_year": 2025,
        })
        assert resp.status_code == 200
        assert seen["materialize_suggestion"] is False
        assert seen["heuristic"] == "prior_year"
        assert seen["growth_pct"] == 0.0

    def test_seed_materializes_heuristic(self, monkeypatch):
        """Apply-heuristic params flow through to seed_budget so the chosen
        suggestion (heuristic + growth_pct) is materialized."""
        from app.services import budget_service
        seen = {}

        def _fake(session, **kw):
            seen.update(kw)
            return {"rows_seeded": 24}

        monkeypatch.setattr(budget_service, "seed_budget", _fake)
        client = _client_as(_ADMIN)
        resp = client.post("/api/v1/budget/seed", json={
            "statement": "PL", "fiscal_year": 2025,
            "materialize_suggestion": True, "heuristic": "trend_cagr", "growth_pct": 0.12,
        })
        assert resp.status_code == 200
        assert resp.json()["rows_seeded"] == 24
        assert seen["materialize_suggestion"] is True
        assert seen["heuristic"] == "trend_cagr"
        assert seen["growth_pct"] == 0.12

    def test_seed_rejects_unknown_heuristic_422(self, monkeypatch):
        from app.services import budget_service
        monkeypatch.setattr(budget_service, "seed_budget", lambda *a, **k: {"rows_seeded": 0})
        client = _client_as(_ADMIN)
        resp = client.post("/api/v1/budget/seed", json={
            "statement": "PL", "fiscal_year": 2025, "heuristic": "bogus",
        })
        assert resp.status_code == 422

    def test_patch_position_admin_ok(self, monkeypatch):
        from app.services import budget_service
        monkeypatch.setattr(budget_service, "patch_position", lambda *a, **k: {"rows_upserted": 36})
        client = _client_as(_ADMIN)
        resp = client.patch("/api/v1/budget/position", json={
            "statement": "PL", "line_code": "NET_SALES", "fiscal_year": 2025,
            "position": {"annual": 1000.0},
            "partners": [{"partner_id": "C1", "annual": 600.0}],
        })
        assert resp.status_code == 200
        assert resp.json()["rows_upserted"] == 36

    def test_cell_request_rejects_non_finite_annual(self):
        """M1: the request model rejects NaN/Infinity annual at the schema edge
        (``allow_inf_nan=False`` → ValidationError → 422)."""
        import math

        from pydantic import ValidationError

        from app.routers.budget import CellRequest

        for bad in (math.nan, math.inf, -math.inf):
            with pytest.raises(ValidationError):
                CellRequest(statement="PL", line_code="NET_SALES", fiscal_year=2025, annual=bad)

    def test_put_cell_rejects_wrong_months_length(self, monkeypatch):
        """L3: a 13-length months array is rejected at the schema edge (422)."""
        from app.services import budget_service
        monkeypatch.setattr(
            budget_service, "upsert_cell",
            MagicMock(side_effect=AssertionError("upsert_cell called on invalid input")),
        )
        client = _client_as(_ADMIN)
        resp = client.put("/api/v1/budget/cell", json={
            "statement": "PL", "line_code": "NET_SALES", "fiscal_year": 2025,
            "months": [100.0] * 13,
        })
        assert resp.status_code == 422

    def test_patch_position_rejects_reserved_partner(self, monkeypatch):
        """L1: writing the '__OTHER__' sentinel via the service raises → 422."""
        from app.services import budget_service
        monkeypatch.setattr(
            budget_service, "patch_position",
            lambda *a, **k: (_ for _ in ()).throw(
                ValueError(f"partner_id '{budget_service.OTHER_PARTNER_ID}' is reserved")
            ),
        )
        client = _client_as(_ADMIN)
        resp = client.patch("/api/v1/budget/position", json={
            "statement": "PL", "line_code": "NET_SALES", "fiscal_year": 2025,
            "partners": [{"partner_id": budget_service.OTHER_PARTNER_ID, "annual": 500.0}],
        })
        assert resp.status_code == 422

    def test_delete_requires_admin(self, monkeypatch):
        from app.services import budget_service
        monkeypatch.setattr(budget_service, "delete_budget", lambda *a, **k: {"deleted": 12})
        client = _client_as(_NON_ADMIN)
        resp = client.delete("/api/v1/budget?statement=PL&fiscal_year=2025")
        assert resp.status_code == 403

    def test_delete_admin_ok(self, monkeypatch):
        from app.services import budget_service
        monkeypatch.setattr(budget_service, "delete_budget", lambda *a, **k: {"deleted": 12})
        # entity=None → resolve_ep not consulted; mock session is fine.
        client = _client_as(_ADMIN)
        resp = client.delete("/api/v1/budget?statement=PL&fiscal_year=2025")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 12


def _tree_grid(level: str = "L4") -> dict:
    """A representative tree grid (one partner-driven L3 with L4 children + partners,
    one plain L3) the GET handler will return verbatim from a monkeypatched build_grid."""
    ns = {
        "line_code": "NET_SALES", "label": "Net sales", "level_3": "Net sales",
        "annual": 1200.0, "months": [100.0] * 12, "synthetic_annual": 1200.0,
        "is_partner_driven": True,
        "suggestion": {"annual": 1320.0, "months": [110.0] * 12},
        "explanation": {"method": "prior_year", "base_fy": 2024, "growth_pct": 0.0},
        "partners": [{"partner_id": "C1", "name": "Cust 1", "annual": 800.0, "months": [66.67] * 12}],
        "other": 400.0,
    }
    cogs = {
        "line_code": "COST_OF_MATERIALS", "label": "Cost of materials",
        "level_3": "Cost of materials", "annual": 600.0, "months": [50.0] * 12,
        "synthetic_annual": 600.0, "is_partner_driven": False,
        "suggestion": {"annual": 600.0, "months": [50.0] * 12},
        "explanation": {"method": "prior_year", "base_fy": 2024, "growth_pct": 0.0},
    }
    if level == "L4":
        ns["children"] = [{"level_4": "Gross sales", "label": "Gross sales",
                           "annual": 1200.0, "months": [100.0] * 12}]
        cogs["children"] = [{"level_4": "Raw materials", "label": "Raw materials",
                            "annual": 600.0, "months": [50.0] * 12}]
    return {
        "statement": "PL", "fiscal_year": 2025, "entity": "", "top_n": 20,
        "level": level, "positions": [ns, cogs],
    }


class TestTreeContract:
    """The reshaped GET returns a hierarchical tree (L3 + children + partners +
    suggestion/explanation); level gates the L4 children; params flow to build_grid."""

    def test_get_returns_tree_shape(self, monkeypatch):
        from app.services import budget_service
        monkeypatch.setattr(budget_service, "build_grid", lambda *a, **k: _tree_grid("L4"))
        client = _client_as(_ADMIN)
        resp = client.get("/api/v1/budget?statement=PL&fiscal_year=2025&level=L4")
        assert resp.status_code == 200
        body = resp.json()
        assert body["level"] == "L4"
        ns = body["positions"][0]
        assert ns["line_code"] == "NET_SALES"
        assert ns["level_3"] == "Net sales"
        assert ns["suggestion"]["annual"] == 1320.0
        assert ns["explanation"]["method"] == "prior_year"
        assert ns["children"][0]["level_4"] == "Gross sales"
        assert ns["partners"][0]["partner_id"] == "C1"
        assert ns["other"] == 400.0

    def test_level_l3_omits_children(self, monkeypatch):
        from app.services import budget_service
        monkeypatch.setattr(budget_service, "build_grid", lambda *a, **k: _tree_grid("L3"))
        client = _client_as(_ADMIN)
        resp = client.get("/api/v1/budget?statement=PL&fiscal_year=2025&level=L3")
        assert resp.status_code == 200
        body = resp.json()
        assert body["level"] == "L3"
        assert body["positions"][0].get("children") is None

    def test_params_flow_to_build_grid(self, monkeypatch):
        from app.services import budget_service
        seen = {}

        def _fake(session, **kw):
            seen.update(kw)
            return _tree_grid("L4")

        monkeypatch.setattr(budget_service, "build_grid", _fake)
        client = _client_as(_ADMIN)
        resp = client.get(
            "/api/v1/budget?statement=PL&fiscal_year=2025&level=L4"
            "&heuristic=trend_cagr&growth_pct=0.15&top_n=7"
        )
        assert resp.status_code == 200
        assert seen["heuristic"] == "trend_cagr"
        assert seen["growth_pct"] == 0.15
        assert seen["level"] == "L4"
        assert seen["top_n"] == 7

    def test_get_invalid_level_422(self, monkeypatch):
        from app.services import budget_service
        monkeypatch.setattr(budget_service, "build_grid", lambda *a, **k: _tree_grid("L4"))
        client = _client_as(_ADMIN)
        resp = client.get("/api/v1/budget?statement=PL&fiscal_year=2025&level=L9")
        assert resp.status_code == 422


class TestVisibility:
    """Fail-closed entity-visibility on GET (mirrors the anomaly / Excel guard)."""

    def _session_granting(self, prefixes: list[str]):
        """A session whose visibility lookup yields the given codes AND resolves
        them to ``prefixes`` (dim_legal_entity)."""
        session = MagicMock()

        def _execute(stmt, params=None):
            sql = str(stmt)
            res = MagicMock()
            if "role_entity_visibility" in sql:
                res.fetchall.return_value = [(f"LE-{p}",) for p in prefixes]
            elif "dim_legal_entity" in sql:
                res.fetchall.return_value = [(p,) for p in prefixes]
            else:
                res.fetchall.return_value = []
            return res

        session.execute.side_effect = _execute
        return session

    def test_restricted_user_disallowed_entity_403(self, monkeypatch):
        from app.services import budget_service
        from app.services.fin_compat_sql import resolve_entity_prefix as _orig  # noqa: F401
        import app.routers.budget as budget_router

        monkeypatch.setattr(budget_service, "build_grid", lambda *a, **k: _tree_grid("L4"))
        # Restricted to prefix '01'; resolve the requested code 'LE-02' → '02'.
        monkeypatch.setattr(budget_router, "resolve_entity_prefix", lambda s, e: "02")
        session = self._session_granting(["01"])
        client = _client_as(_NON_ADMIN, session=session)
        resp = client.get("/api/v1/budget?statement=PL&fiscal_year=2025&entity=LE-02")
        assert resp.status_code == 403

    def test_restricted_user_consolidated_403(self, monkeypatch):
        from app.services import budget_service
        monkeypatch.setattr(budget_service, "build_grid", lambda *a, **k: _tree_grid("L4"))
        session = self._session_granting(["01"])
        client = _client_as(_NON_ADMIN, session=session)
        resp = client.get("/api/v1/budget?statement=PL&fiscal_year=2025")  # entity='' = consolidated
        assert resp.status_code == 403

    def test_admin_consolidated_ok(self, monkeypatch):
        from app.services import budget_service
        monkeypatch.setattr(budget_service, "build_grid", lambda *a, **k: _tree_grid("L4"))
        client = _client_as(_ADMIN)  # admin → unrestricted, no session lookup
        resp = client.get("/api/v1/budget?statement=PL&fiscal_year=2025")
        assert resp.status_code == 200


class TestEntitiesEndpoint:
    def _entity_session(self, rows, vis_codes=None):
        session = MagicMock()

        def _execute(stmt, params=None):
            sql = str(stmt)
            res = MagicMock()
            if "role_entity_visibility" in sql:
                res.fetchall.return_value = [(c,) for c in (vis_codes or [])]
            elif "FROM dim_legal_entity WHERE legal_entity_code" in sql:
                # _visible_entity_prefixes resolution
                res.fetchall.return_value = [(c[2],) for c in rows if c[0] in (vis_codes or [])]
            elif "FROM dim_legal_entity" in sql:
                res.fetchall.return_value = rows
            else:
                res.fetchall.return_value = []
            return res

        session.execute.side_effect = _execute
        return session

    def test_admin_sees_all_plannable_entities(self):
        rows = [
            ("LE01", "Entity One", "01", False),
            ("LE02", "Entity Two", "02", False),
            ("CONS", "Consolidated", "99", True),  # excluded (consolidation pseudo)
        ]
        client = _client_as(_ADMIN, session=self._entity_session(rows))
        resp = client.get("/api/v1/budget/entities")
        assert resp.status_code == 200
        body = resp.json()
        assert body["can_consolidate"] is True
        codes = {e["code"] for e in body["entities"]}
        assert codes == {"LE01", "LE02"}  # consolidation entity dropped

    def test_restricted_user_sees_only_granted(self):
        rows = [
            ("LE01", "Entity One", "01", False),
            ("LE02", "Entity Two", "02", False),
        ]
        session = self._entity_session(rows, vis_codes=["LE01"])
        client = _client_as(_NON_ADMIN, session=session)
        resp = client.get("/api/v1/budget/entities")
        assert resp.status_code == 200
        body = resp.json()
        assert body["can_consolidate"] is False
        codes = {e["code"] for e in body["entities"]}
        assert codes == {"LE01"}


# =========================================================================== #
# B) ROUND-TRIP — live v2 DB (opt-in; auto-skip when unreachable)
# =========================================================================== #
def _v2_session_or_skip():
    """Return a live Session bound to a DB that has fact_position_plan, else skip."""
    from sqlalchemy import text

    from app.db import SessionLocal

    try:
        s = SessionLocal()
        s.execute(text("SELECT 1 FROM fact_position_plan LIMIT 1"))
        return s
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"v2 budget DB not reachable / table absent: {exc}")


_FY = 2025
_ENT = "01"  # an entity_prefix present in the v2 fixture


@pytest.mark.skipif(
    os.getenv("DB_NAME", "Finssentials") != "finssentials_v2",
    reason="budget round-trip runs only against finssentials_v2 (set DB_NAME)",
)
class TestRoundTripV2:
    """seed → PUT → GET reflects → Σ invariant → DELETE reverts; always cleans up."""

    def _clean(self, session):
        from sqlalchemy import text
        session.execute(
            text("DELETE FROM fact_position_plan WHERE scenario='budget' "
                 "AND statement='PL' AND fiscal_year=:fy"),
            {"fy": _FY},
        )
        session.commit()

    def test_pl_round_trip(self):
        from app.services import budget_service

        session = _v2_session_or_skip()
        try:
            self._clean(session)
            # GET seed (pure-read) — no budget rows yet.
            before = budget_service.build_grid(
                session, statement="PL", fiscal_year=_FY, entity_prefix=None, top_n=5
            )
            assert before["positions"], "expected PL positions in v2 structure"
            assert budget_service.has_budget_rows(session, statement="PL", fiscal_year=_FY) == 0

            # PUT one position cell (annual → seasonalized server-side).
            res = budget_service.upsert_cell(
                session, statement="PL", line_code="NET_SALES", entity="",
                partner_id=None, partner_kind=None, fiscal_year=_FY,
                annual=12000.0, weights=None, updated_by="test@finssentials",
            )
            assert res["rows_upserted"] == 12

            # GET reflects the saved budget.
            after = budget_service.build_grid(
                session, statement="PL", fiscal_year=_FY, entity_prefix=None, top_n=5
            )
            ns = next(p for p in after["positions"] if p["line_code"] == "NET_SALES")
            assert ns["annual"] == pytest.approx(12000.0, abs=1e-2)
            assert sum(ns["months"]) == pytest.approx(12000.0, abs=1e-2)
            # Σ invariant for the partner-driven position (partners + Other == annual).
            if ns.get("partners") is not None:
                inv = sum(p["annual"] for p in ns["partners"]) + (ns.get("other") or 0.0)
                assert inv == pytest.approx(ns["annual"], abs=1e-2)

            # DELETE reverts (no budget rows remain).
            d = budget_service.delete_budget(session, statement="PL", fiscal_year=_FY, entity=None)
            assert d["deleted"] >= 12
            assert budget_service.has_budget_rows(session, statement="PL", fiscal_year=_FY) == 0

            # SEED materialises the synthetic grid; GET after seed must reproduce it
            # (the 'Other' remainder must NOT be re-shown as a named partner → no
            # double count): Σ(named)+Other == annual for every partner-driven line.
            budget_service.seed_budget(
                session, statement="PL", fiscal_year=_FY, entity="", top_n=5,
                updated_by="test@finssentials",
            )
            seeded = budget_service.build_grid(
                session, statement="PL", fiscal_year=_FY, entity_prefix=None, top_n=5
            )
            for pos in seeded["positions"]:
                if pos.get("partners") is not None:
                    inv = sum(p["annual"] for p in pos["partners"]) + (pos.get("other") or 0.0)
                    assert inv == pytest.approx(pos["annual"], abs=1e-2), pos["line_code"]
                    other_ids = [p["partner_id"] for p in pos["partners"]
                                 if p["partner_id"] == budget_service.OTHER_PARTNER_ID]
                    assert other_ids == [], "Other must not appear as a named partner"
        finally:
            # GUARANTEE v2 is left clean so the golden stays green.
            self._clean(session)
            session.close()

    def test_apply_heuristic_skips_zero_positions(self):
        """Bounded round-trip: applying a heuristic for ONE entity persists ONLY
        positions whose suggestion is non-zero (never one row per position × 12),
        GET reflects them, DELETE reverts to 0 rows.  Always cleans up so v2 ends
        empty and the golden ``compare live v2`` stays EQUIVALENT."""
        from sqlalchemy import text

        from app.services import budget_service

        session = _v2_session_or_skip()
        try:
            self._clean(session)
            assert budget_service.has_budget_rows(
                session, statement="PL", fiscal_year=_FY, entity_prefix=_ENT
            ) == 0

            # The grid the apply will materialise (suggestion mode), for one entity.
            grid = budget_service.build_grid(
                session, statement="PL", fiscal_year=_FY, entity_prefix=_ENT,
                top_n=5, heuristic="prior_year", growth_pct=0.0, level="L3",
            )
            assert grid["positions"], "expected PL positions in v2 structure"
            nonzero_codes = {
                p["line_code"]
                for p in grid["positions"]
                if not budget_service._all_zero_months(
                    {q: p["suggestion"]["months"][q - 1] for q in budget_service.PERIODS}
                )
            }

            # APPLY heuristic (materialize_suggestion=True) for the single entity.
            seeded = budget_service.seed_budget(
                session, statement="PL", fiscal_year=_FY, entity=_ENT, top_n=5,
                updated_by="test@finssentials",
                materialize_suggestion=True, heuristic="prior_year", growth_pct=0.0,
            )
            # Exactly the non-zero positions, 12 rows each — never every position.
            assert seeded["rows_seeded"] == len(nonzero_codes) * 12

            # The persisted line_codes are precisely the non-zero ones (no zero rows).
            persisted = {
                str(r[0])
                for r in session.execute(
                    text("SELECT DISTINCT line_code FROM fact_position_plan "
                         "WHERE scenario='budget' AND statement='PL' "
                         "AND fiscal_year=:fy AND entity_prefix=:ep"),
                    {"fy": _FY, "ep": _ENT},
                ).fetchall()
            }
            assert persisted == nonzero_codes

            # DELETE reverts → 0 rows for the entity.
            budget_service.delete_budget(
                session, statement="PL", fiscal_year=_FY, entity=_ENT
            )
            assert budget_service.has_budget_rows(
                session, statement="PL", fiscal_year=_FY, entity_prefix=_ENT
            ) == 0
        finally:
            # GUARANTEE v2 is left clean (drops any entity scope) so the golden stays green.
            from sqlalchemy import text as _text
            session.execute(
                _text("DELETE FROM fact_position_plan WHERE scenario='budget' "
                      "AND statement='PL' AND fiscal_year=:fy"),
                {"fy": _FY},
            )
            session.commit()
            session.close()
