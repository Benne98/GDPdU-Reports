"""Phase 5 (decision 2) — auto-extension of the statement structure.

Three layers, all DB-free (sessions mocked):

  1. PURE detection — normalize_statement, grain matching (reuses the readers'
     _match_grain), detect_unknown_positions grouping + suggestions.
  2. Placement writer — extend_structure over a stateful fake session: correct
     target table per statement (split invariant), reader-faithful line_code
     markers, idempotency (skip duplicates), sort_order insert/append, and the
     PlacementError validation contract.
  3. API contract — POST /ingest/structure/unknown-positions + /structure/extend
     via TestClient with auth/session deps overridden (same pattern as
     test_projects.py): auth-guarding, legacy-DB auto-pass, invariant, 422.

No financial numbers change — this is a data-classification (structure) change;
the guarantee under test is that a placed position lands in EXACTLY ONE statement
table and is never dropped/duplicated (split invariant + idempotency).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.auth import User, current_user, require_admin
from app.db import get_session
from app.main import app
from app.services import structure_autoextend as ax


_ADMIN = User(user_id=1, email="admin@test", display_name="Admin", is_admin=True)
_NON_ADMIN = User(user_id=2, email="user@test", display_name="User", is_admin=False)


def _client_as(user: User, session) -> TestClient:
    app.dependency_overrides[get_session] = lambda: session
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
# 1.  Pure detection
# ===========================================================================
class TestNormalizeStatement:
    @pytest.mark.parametrize("raw,expected", [
        ("BS", "BS"), ("bs", "BS"), ("Bilanz", "BS"), ("Balance Sheet", "BS"),
        ("PL", "PL"), ("P&L", "PL"), ("GuV", "PL"), ("Income Statement", "PL"),
        ("", "UNKNOWN"), (None, "UNKNOWN"), ("mystery", "UNKNOWN"),
    ])
    def test_maps(self, raw, expected):
        assert ax.normalize_statement(raw) == expected


# Structure: PL has a broad 'Income' L2 row (matches any grain under Income) and a
# specific 'Cost of materials' leaf.  BS has a Trade receivables leaf.
_PL_STRUCT = [
    {"sort_order": 10, "line_code": "NET_SALES", "row_type": "mapping",
     "level_2": "Income", "level_3": "", "level_4": "", "gl_account_id": None, "kpi_code": None},
    {"sort_order": 20, "line_code": "COST_OF_MATERIALS", "row_type": "mapping",
     "level_2": "Cost of materials", "level_3": "Raw materials", "level_4": "",
     "gl_account_id": None, "kpi_code": None},
    {"sort_order": 30, "line_code": "GROSS_PROFIT", "row_type": "subtotal",
     "level_2": None, "level_3": None, "level_4": None, "gl_account_id": None, "kpi_code": None},
]
_BS_STRUCT = [
    {"sort_order": 1000, "line_code": "AR", "row_type": "mapping",
     "level_2": "Assets", "level_3": "Trade receivables", "level_4": "",
     "gl_account_id": None, "kpi_code": "BS:asset"},
    {"sort_order": 1010, "line_code": "BS_GRANDTOTAL_ASSETS", "row_type": "grandtotal",
     "level_2": "Assets", "level_3": None, "level_4": None, "gl_account_id": None, "kpi_code": "BS:asset"},
]
_STRUCT = {"PL": _PL_STRUCT, "BS": _BS_STRUCT, "CF": []}


def _acct(ang, gid, name, l0, l1, l2, l3, l4=""):
    return {"account_number_group": ang, "gl_account_id": gid, "account_name": name,
            "level_0": l0, "level_1": l1, "level_2": l2, "level_3": l3, "level_4": l4}


class TestGrainKnown:
    def test_broad_l2_row_covers_grain(self):
        # Grain under Income with any L3 → matched by the L2-only NET_SALES row.
        a = _acct("018400", "8400", "Revenue DE", "PL", "Ertrag", "Income", "Domestic")
        assert ax.grain_is_known(a, ax._mapping_rows(_PL_STRUCT)) is True

    def test_specific_leaf_requires_full_path(self):
        a = _acct("015000", "5000", "Raw mat", "PL", "Aufwand", "Cost of materials", "Raw materials")
        assert ax.grain_is_known(a, ax._mapping_rows(_PL_STRUCT)) is True

    def test_unknown_grain_matches_nothing(self):
        a = _acct("016800", "6800", "Ads", "PL", "Aufwand", "Marketing", "Advertising")
        assert ax.grain_is_known(a, ax._mapping_rows(_PL_STRUCT)) is False

    def test_subtotal_rows_never_match(self):
        # GROSS_PROFIT is a subtotal (no level path) — excluded by _mapping_rows.
        assert all(r["row_type"] == "mapping" for r in ax._mapping_rows(_PL_STRUCT))


class TestDetectUnknownPositions:
    def test_known_accounts_yield_no_positions(self):
        accts = [
            _acct("018400", "8400", "Revenue", "PL", "Ertrag", "Income", "Domestic"),
            _acct("011400", "1400", "AR", "BS", "Aktiva", "Assets", "Trade receivables"),
        ]
        assert ax.detect_unknown_positions(accts, _STRUCT) == []

    def test_unknown_pl_position_detected_and_grouped(self):
        accts = [
            _acct("016800", "6800", "Ads DE", "PL", "Aufwand", "Marketing", "Advertising"),
            _acct("026800", "6800", "Ads AT", "PL", "Aufwand", "Marketing", "Advertising"),
        ]
        pos = ax.detect_unknown_positions(accts, _STRUCT)
        assert len(pos) == 1
        p = pos[0]
        assert p["statement"] == "PL"
        assert (p["level_2"], p["level_3"], p["level_4"]) == ("Marketing", "Advertising", None)
        assert p["account_count"] == 2
        assert {a["account_number_group"] for a in p["accounts"]} == {"016800", "026800"}
        assert p["suggested_statement"] == "PL"

    def test_unknown_bs_position_suggests_bs(self):
        accts = [_acct("013500", "3500", "Deferred", "BS", "Aktiva", "Assets", "Prepaid expenses")]
        pos = ax.detect_unknown_positions(accts, _STRUCT)
        assert len(pos) == 1
        assert pos[0]["statement"] == "BS"
        assert pos[0]["suggested_statement"] == "BS"
        # Anchor is a row sharing level_2 'Assets'.
        assert pos[0]["suggested_after_line_code"] in {"AR", "BS_GRANDTOTAL_ASSETS"}

    def test_unknown_level0_defaults_to_pl_and_is_unknown(self):
        accts = [_acct("019999", "9999", "Mystery", "", "", "Sonstiges", "Diverses")]
        pos = ax.detect_unknown_positions(accts, _STRUCT)
        assert len(pos) == 1
        assert pos[0]["statement"] == "UNKNOWN"
        assert pos[0]["suggested_statement"] == "PL"

    def test_stable_position_id(self):
        a = _acct("016800", "6800", "Ads", "PL", "Aufwand", "Marketing", "Advertising")
        p1 = ax.detect_unknown_positions([a], _STRUCT)[0]["id"]
        p2 = ax.detect_unknown_positions([a], _STRUCT)[0]["id"]
        assert p1 == p2 and p1.startswith("pos_")


class TestMakeLineCode:
    def test_pl_has_no_bs_cf_prefix(self):
        code = ax.make_line_code("PL", "Marketing", "Advertising", "")
        assert not code.startswith("BS_") and not code.startswith("CF_")
        assert code == "ADVERTISING"

    def test_bs_prefixed(self):
        assert ax.make_line_code("BS", "Assets", "Prepaid", "").startswith("BS_")

    def test_cf_prefixed(self):
        assert ax.make_line_code("CF", "Ops", "Working capital", "").startswith("CF_")

    def test_gl_account_suffix_and_cap(self):
        code = ax.make_line_code("PL", "Marketing", "", "", gl_account_id="6800")
        assert code.endswith("6800")
        assert len(code) <= 64


# ===========================================================================
# 2.  Placement writer — stateful fake session
# ===========================================================================
class _FakeStructResult:
    def __init__(self, fetchone=None, fetchall=None):
        self._one = fetchone
        self._all = fetchall or []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


class _FakeStructSession:
    """Minimal stateful session emulating the SQL extend_structure issues.

    Tracks per-table existing rows (line_code → sort_order) so idempotency and
    the split invariant (which table an INSERT targets) can be asserted.
    """

    def __init__(self, tables: dict[str, dict[str, int]], *, has_source=True):
        # tables: {table_name: {line_code: sort_order}}
        self.tables = {t: dict(rows) for t, rows in tables.items()}
        self.has_source = has_source
        self.inserts: list[tuple[str, dict]] = []
        self.updates: list[tuple[str, dict]] = []
        self.committed = False
        self.rolled_back = False

    # -- helpers ------------------------------------------------------------
    def _table_in(self, sql: str) -> str | None:
        for t in self.tables:
            if t in sql:
                return t
        return None

    def execute(self, stmt, params=None):
        sql = str(stmt)
        params = params or {}
        if "information_schema.columns" in sql:
            return _FakeStructResult(fetchone=(1,) if self.has_source else None)
        table = self._table_in(sql)
        if table is None:
            return _FakeStructResult()

        if sql.strip().startswith("INSERT INTO"):
            self.inserts.append((table, dict(params)))
            self.tables[table][params["line_code"]] = params["sort_order"]
            return _FakeStructResult()
        if sql.strip().startswith("UPDATE"):
            self.updates.append((table, dict(params)))
            # apply the shift so subsequent MIN/MAX are consistent
            for lc, so in list(self.tables[table].items()):
                if so == params["old"]:
                    self.tables[table][lc] = params["new"]
            return _FakeStructResult()
        if "SELECT line_code FROM" in sql:
            return _FakeStructResult(fetchall=[(lc,) for lc in self.tables[table]])
        if "row_type = 'mapping'" in sql and "SELECT 1" in sql:
            # duplicate-mapping probe — this fake keys on line_code only, so never dup here
            return _FakeStructResult(fetchone=None)
        if "WHERE line_code = :lc" in sql and "sort_order" in sql:
            so = self.tables[table].get(params.get("lc"))
            return _FakeStructResult(fetchone=(so,) if so is not None else None)
        if "MIN(sort_order)" in sql:
            so = params.get("so")
            later = [v for v in self.tables[table].values() if v > so]
            return _FakeStructResult(fetchone=(min(later),) if later else (None,))
        if "MAX(sort_order)" in sql:
            vals = list(self.tables[table].values())
            return _FakeStructResult(fetchone=(max(vals),) if vals else (None,))
        if "ORDER BY sort_order DESC" in sql:
            so = params.get("so")
            later = sorted((v for v in self.tables[table].values() if v > so), reverse=True)
            return _FakeStructResult(fetchall=[(v,) for v in later])
        if "COUNT(*)" in sql:
            return _FakeStructResult(fetchone=(len(self.tables[table]),))
        return _FakeStructResult()

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def _base_tables():
    return {
        "dim_pl_structure": {"NET_SALES": 10, "COST_OF_MATERIALS": 20, "GROSS_PROFIT": 30},
        "dim_bs_structure": {"AR": 1000, "BS_GRANDTOTAL_ASSETS": 1010},
        "dim_cf_structure": {"CF_OPERATING": 2000},
    }


class TestExtendStructure:
    def test_pl_placement_targets_pl_table_only(self):
        s = _FakeStructSession(_base_tables())
        res = ax.extend_structure(s, [{
            "statement": "PL", "level_2": "Marketing", "level_3": "Advertising",
            "level_4": None, "row_type": "mapping", "after_line_code": "COST_OF_MATERIALS",
        }])
        assert len(res["inserted"]) == 1
        assert all(t == "dim_pl_structure" for t, _ in s.inserts)
        # split invariant: nothing written to BS or CF
        assert not any(t in ("dim_bs_structure", "dim_cf_structure") for t, _ in s.inserts)

    def test_cf_placement_never_lands_in_pl(self):
        s = _FakeStructSession(_base_tables())
        res = ax.extend_structure(s, [{
            "statement": "CF", "level_2": "Ops", "level_3": "Working capital",
            "level_4": None, "row_type": "mapping",
        }])
        assert res["inserted"][0].startswith("CF_")
        assert [t for t, _ in s.inserts] == ["dim_cf_structure"]

    def test_bs_placement_prefixed_and_section_kpi(self):
        s = _FakeStructSession(_base_tables())
        ax.extend_structure(s, [{
            "statement": "BS", "level_2": "Assets", "level_3": "Prepaid expenses",
            "row_type": "mapping", "section": "asset", "after_line_code": "AR",
        }])
        table, params = s.inserts[0]
        assert table == "dim_bs_structure"
        assert params["line_code"].startswith("BS_")
        assert params["kpi_code"] == "BS:asset"

    def test_insert_after_anchor_with_gap_uses_next_slot(self):
        # gap between NET_SALES(10) and COST_OF_MATERIALS(20)
        s = _FakeStructSession(_base_tables())
        ax.extend_structure(s, [{
            "statement": "PL", "level_2": "Other income", "row_type": "mapping",
            "after_line_code": "NET_SALES",
        }])
        _, params = s.inserts[0]
        assert params["sort_order"] == 11
        assert s.updates == []   # no shift needed (gap existed)

    def test_insert_after_anchor_no_gap_shifts_later_rows(self):
        # dense table: 1,2,3 — inserting after 1 must shift 2,3 → 3,4
        s = _FakeStructSession({
            "dim_pl_structure": {"A": 1, "B": 2, "C": 3},
            "dim_bs_structure": {}, "dim_cf_structure": {},
        })
        ax.extend_structure(s, [{
            "statement": "PL", "level_2": "X", "row_type": "mapping", "after_line_code": "A",
        }])
        _, params = s.inserts[0]
        assert params["sort_order"] == 2
        # B and C shifted up
        assert s.tables["dim_pl_structure"]["B"] == 3
        assert s.tables["dim_pl_structure"]["C"] == 4

    def test_append_when_no_anchor(self):
        s = _FakeStructSession(_base_tables())
        ax.extend_structure(s, [{
            "statement": "BS", "level_2": "Assets", "level_3": "Goodwill", "row_type": "mapping",
        }])
        _, params = s.inserts[0]
        assert params["sort_order"] == 1020   # max(1010)+10

    def test_idempotent_skip_existing_line_code(self):
        s = _FakeStructSession(_base_tables())
        res = ax.extend_structure(s, [{
            "statement": "PL", "line_code": "NET_SALES", "level_2": "Income", "row_type": "mapping",
        }])
        assert res["inserted"] == []
        assert res["skipped"] == ["NET_SALES"]
        assert s.inserts == []

    def test_batch_internally_idempotent(self):
        s = _FakeStructSession(_base_tables())
        p = {"statement": "PL", "level_2": "Marketing", "level_3": "Advertising",
             "row_type": "mapping", "after_line_code": "COST_OF_MATERIALS"}
        res = ax.extend_structure(s, [dict(p), dict(p)])
        assert len(res["inserted"]) == 1 and len(res["skipped"]) == 1

    def test_source_written_when_column_present(self):
        s = _FakeStructSession(_base_tables(), has_source=True)
        ax.extend_structure(s, [{"statement": "PL", "level_2": "New", "row_type": "mapping"}])
        _, params = s.inserts[0]
        assert params.get("source") == "auto_extend"

    def test_source_omitted_when_column_absent(self):
        s = _FakeStructSession(_base_tables(), has_source=False)
        ax.extend_structure(s, [{"statement": "PL", "level_2": "New", "row_type": "mapping"}])
        _, params = s.inserts[0]
        assert "source" not in params

    def test_pl_ceiling_guard(self):
        s = _FakeStructSession({
            "dim_pl_structure": {"LAST": 999}, "dim_bs_structure": {}, "dim_cf_structure": {},
        })
        with pytest.raises(ax.PlacementError):
            ax.extend_structure(s, [{"statement": "PL", "level_2": "X", "row_type": "mapping"}])


class TestPlacementValidation:
    def test_bad_statement_raises(self):
        with pytest.raises(ax.PlacementError):
            ax.extend_structure(MagicMock(), [{"statement": "XX", "level_2": "A"}])

    def test_non_mapping_row_type_raises(self):
        with pytest.raises(ax.PlacementError):
            ax.extend_structure(MagicMock(), [{"statement": "PL", "level_2": "A", "row_type": "subtotal"}])

    def test_empty_path_raises(self):
        with pytest.raises(ax.PlacementError):
            ax.extend_structure(MagicMock(), [{"statement": "PL", "row_type": "mapping"}])


# ===========================================================================
# 3.  API contract
# ===========================================================================
class TestUnknownPositionsEndpoint:
    def test_requires_auth(self):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/v1/ingest/structure/unknown-positions", json={})
        assert resp.status_code == 401

    def test_legacy_db_auto_passes(self, monkeypatch):
        # No structure tables + no accounts → structure_available False, zero positions.
        from app.routers import ingest as ingest_router
        monkeypatch.setattr(ingest_router._autoextend, "load_structure_rows", lambda s, stmt: [])
        monkeypatch.setattr(ingest_router._autoextend, "load_coa_accounts", lambda s, **k: [])
        client = _client_as(_NON_ADMIN, MagicMock())
        resp = client.post("/api/v1/ingest/structure/unknown-positions", json={"project_id": "default"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["structure_available"] is False
        assert body["total"] == 0 and body["positions"] == []

    def test_surfaces_unknown_position(self, monkeypatch):
        from app.routers import ingest as ingest_router
        monkeypatch.setattr(ingest_router._autoextend, "load_structure_rows",
                            lambda s, stmt: _STRUCT[stmt])
        monkeypatch.setattr(ingest_router._autoextend, "load_coa_accounts",
                            lambda s, **k: [_acct("016800", "6800", "Ads", "PL", "Aufwand", "Marketing", "Advertising")])
        client = _client_as(_NON_ADMIN, MagicMock())
        resp = client.post("/api/v1/ingest/structure/unknown-positions",
                           json={"project_id": "default", "fiscal_years": [2025]})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert body["structure_available"] is True
        assert body["counts_by_statement"] == {"PL": 1}
        assert body["positions"][0]["level_3"] == "Advertising"


class TestExtendEndpoint:
    def test_requires_admin(self):
        client = _client_as(_NON_ADMIN, MagicMock())
        resp = client.post("/api/v1/ingest/structure/extend",
                           json={"placements": [{"statement": "PL", "level_2": "X"}]})
        assert resp.status_code == 403

    def test_rejects_empty_placements(self):
        client = _client_as(_ADMIN, MagicMock())
        resp = client.post("/api/v1/ingest/structure/extend", json={"placements": []})
        assert resp.status_code == 422

    def test_bad_statement_422(self):
        client = _client_as(_ADMIN, MagicMock())
        resp = client.post("/api/v1/ingest/structure/extend",
                           json={"placements": [{"statement": "ZZ", "level_2": "X"}]})
        # Literal type → pydantic 422 before it reaches the service
        assert resp.status_code == 422

    def test_happy_path_commits(self):
        s = _FakeStructSession(_base_tables())
        client = _client_as(_ADMIN, s)
        resp = client.post("/api/v1/ingest/structure/extend", json={
            "placements": [{
                "statement": "CF", "level_2": "Ops", "level_3": "Working capital",
                "row_type": "mapping",
            }],
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["inserted"]) == 1
        assert s.committed is True
        assert [t for t, _ in s.inserts] == ["dim_cf_structure"]
