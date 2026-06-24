"""Tests for the FDD-bot GL pipeline endpoints.

Covers (synthetic fixtures only — no real DB, no real client data):
  GET  /api/v1/fdd/gl/data-status
    - happy path: gl_loaded, entities, fiscal_years, period_min/max
    - sales_loaded True / False (incl. fact_sales table missing → False)
    - entity visibility: admin sees all; restricted user sees only allowed;
      user restricted to no entities sees nothing.
  POST /api/v1/fdd/run/databook/gl-master
    - result shape exactly mirrors run_databook_susa (keys the Rasa
      _databook_after_susa_run consumer reads).
    - writes the workbook to master_workbook_path with both Master_BS/Master_PL.
    - rejects entities outside the user's allow-list.

Strategy: FastAPI TestClient with the auth dependency overridden to a dummy user
and the DB dependency overridden to a MagicMock session that dispatches canned
rows on SQL substrings. The gl-master integration test does NOT mock the Phase-1
service — it monkeypatches the trial-balance data source so a real workbook is
written through fin_compat_master_from_gl.write_master_workbook.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.auth import User, current_user
from app.db import get_session
from app.main import app


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
_ADMIN = User(user_id=1, email="admin@finssentials.com", display_name="Admin", is_admin=True)
_RESTRICTED = User(user_id=2, email="user@finssentials.com", display_name="User", is_admin=False)


class _DictRow:
    """Minimal SQLAlchemy Row-like supporting index access and .get()."""

    def __init__(self, values: tuple):
        self._values = tuple(values)

    def __getitem__(self, key):
        return self._values[key]

    def __iter__(self):
        return iter(self._values)


def _make_session(
    *,
    entity_rows: list[tuple] | None = None,
    visibility_codes: list[str] | None = None,
    fiscal_years: list[int] | None = None,
    bounds: tuple | None = ("2022-01-15", "2024-11-30"),
    sales_loaded: bool = True,
    sales_table_missing: bool = False,
) -> MagicMock:
    """MagicMock Session whose execute() returns canned rows keyed on SQL substrings."""
    session = MagicMock()

    def _execute(stmt, params=None):
        sql = str(stmt)
        result = MagicMock()
        rows: list[Any] = []

        if "role_entity_visibility" in sql:
            rows = [_DictRow((c,)) for c in (visibility_codes or [])]
        elif "fiscal_year" in sql and "dim_gl_account" in sql:
            # DISTINCT fiscal_year query (may also join dim_legal_entity when scoped).
            rows = [_DictRow((y,)) for y in (fiscal_years or [])]
        elif "dim_legal_entity" in sql and "dim_gl_account" in sql:
            rows = [_DictRow(r) for r in (entity_rows or [])]
        elif "fact_gl_entry" in sql:
            rows = [_DictRow(bounds)] if bounds else []
        elif "fact_sales" in sql:
            if sales_table_missing:
                raise RuntimeError('relation "fact_sales" does not exist')
            rows = [_DictRow((1,))] if sales_loaded else []
        else:
            rows = []

        result.fetchall.return_value = rows
        result.fetchone.return_value = rows[0] if rows else None
        return result

    session.execute.side_effect = _execute
    return session


def _client(session: MagicMock, user: User) -> TestClient:
    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# data-status — happy path
# ---------------------------------------------------------------------------
class TestDataStatusHappyPath:
    def test_admin_sees_all_loaded_entities(self):
        session = _make_session(
            entity_rows=[("AT", "Austria GmbH"), ("DE", "Germany GmbH")],
            fiscal_years=[2022, 2023, 2024],
        )
        client = _client(session, _ADMIN)
        resp = client.get("/api/v1/fdd/gl/data-status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["gl_loaded"] is True
        assert body["entities"] == [
            {"code": "AT", "name": "Austria GmbH"},
            {"code": "DE", "name": "Germany GmbH"},
        ]
        assert body["fiscal_years"] == [2022, 2023, 2024]
        assert body["period_min"] == "2022-01"
        assert body["period_max"] == "2024-11"

    def test_no_gl_data_reports_not_loaded(self):
        session = _make_session(entity_rows=[], fiscal_years=[], bounds=None)
        client = _client(session, _ADMIN)
        body = client.get("/api/v1/fdd/gl/data-status").json()
        assert body["gl_loaded"] is False
        assert body["entities"] == []
        assert body["period_min"] is None and body["period_max"] is None


# ---------------------------------------------------------------------------
# data-status — sales_loaded
# ---------------------------------------------------------------------------
class TestSalesLoaded:
    def test_sales_loaded_true(self):
        session = _make_session(entity_rows=[("AT", "Austria GmbH")], sales_loaded=True)
        body = _client(session, _ADMIN).get("/api/v1/fdd/gl/data-status").json()
        assert body["sales_loaded"] is True

    def test_sales_loaded_false_when_empty(self):
        session = _make_session(entity_rows=[("AT", "Austria GmbH")], sales_loaded=False)
        body = _client(session, _ADMIN).get("/api/v1/fdd/gl/data-status").json()
        assert body["sales_loaded"] is False

    def test_sales_loaded_false_when_table_missing(self):
        session = _make_session(
            entity_rows=[("AT", "Austria GmbH")], sales_table_missing=True
        )
        body = _client(session, _ADMIN).get("/api/v1/fdd/gl/data-status").json()
        assert body["sales_loaded"] is False


# ---------------------------------------------------------------------------
# data-status — entity visibility
# ---------------------------------------------------------------------------
class TestEntityVisibility:
    def test_restricted_user_sees_only_allowed(self):
        # Visibility grants AT only; the entity query is asked to honour it.
        session = _make_session(
            entity_rows=[("AT", "Austria GmbH")],
            visibility_codes=["AT"],
            fiscal_years=[2024],
        )
        body = _client(session, _RESTRICTED).get("/api/v1/fdd/gl/data-status").json()
        assert [e["code"] for e in body["entities"]] == ["AT"]
        # The entity query must have been called with the IN-list bound to allowed codes.
        calls = [c for c in session.execute.call_args_list
                 if "dim_legal_entity" in str(c.args[0]) and "dim_gl_account" in str(c.args[0])]
        assert calls, "entity query was not executed"
        params = calls[0].args[1] if len(calls[0].args) > 1 else calls[0].kwargs.get("params")
        assert params == {"codes": ["AT"]}

    def test_user_with_no_grants_sees_nothing(self):
        # M1 regression: a non-admin with ZERO role_entity_visibility rows must be
        # denied all entities (fail-closed), NOT treated as unrestricted.
        session = _make_session(
            entity_rows=[("AT", "Austria GmbH"), ("DE", "Germany GmbH")],
            visibility_codes=[],
            fiscal_years=[2024],
        )
        body = _client(session, _RESTRICTED).get("/api/v1/fdd/gl/data-status").json()
        assert body["gl_loaded"] is False
        assert body["entities"] == []
        assert body["fiscal_years"] == []
        assert body["period_min"] is None and body["period_max"] is None

    def test_admin_with_no_grants_still_sees_all(self):
        # Admins remain unrestricted (None), unaffected by the M1 deny-all change.
        session = _make_session(
            entity_rows=[("AT", "Austria GmbH"), ("DE", "Germany GmbH")],
            visibility_codes=[],
            fiscal_years=[2024],
        )
        body = _client(session, _ADMIN).get("/api/v1/fdd/gl/data-status").json()
        assert [e["code"] for e in body["entities"]] == ["AT", "DE"]

    def test_restricted_user_fy_and_bounds_are_scoped(self):
        # H2 regression: fiscal_years (dim_gl_account) and period bounds
        # (fact_gl_entry) must be scoped to the user's allowed codes, not global.
        session = _make_session(
            entity_rows=[("AT", "Austria GmbH")],
            visibility_codes=["AT"],
            fiscal_years=[2024],
        )
        _client(session, _RESTRICTED).get("/api/v1/fdd/gl/data-status").json()

        # The fiscal-year query must join dim_legal_entity and bind the allowed codes.
        fy_calls = [
            c for c in session.execute.call_args_list
            if "fiscal_year" in str(c.args[0])
            and "dim_gl_account" in str(c.args[0])
            and "dim_legal_entity" in str(c.args[0])
        ]
        assert fy_calls, "fiscal-year query was not scoped to dim_legal_entity"
        fy_params = fy_calls[0].args[1] if len(fy_calls[0].args) > 1 else fy_calls[0].kwargs.get("params")
        assert fy_params == {"codes": ["AT"]}

        # The period-bounds query must join to dim_legal_entity and bind allowed codes.
        bounds_calls = [
            c for c in session.execute.call_args_list
            if "fact_gl_entry" in str(c.args[0]) and "dim_legal_entity" in str(c.args[0])
        ]
        assert bounds_calls, "period-bounds query was not scoped to dim_legal_entity"
        b_params = bounds_calls[0].args[1] if len(bounds_calls[0].args) > 1 else bounds_calls[0].kwargs.get("params")
        assert b_params == {"codes": ["AT"]}


# ---------------------------------------------------------------------------
# gl-master — integration: real workbook via Phase-1 service
# ---------------------------------------------------------------------------
def _pl_tb():
    return {
        "rows": [
            {
                "entity": "AT", "level_1": None, "level_2": "Income",
                "level_3": "Net sales", "level_4": "Net sales",
                "gl_account_id": "8000", "account_name": "Umsatzerlöse",
                "amounts": {"FY2023": 1000.0, "YTD2024": 1200.0, "2024-12": 100.0},
            },
        ],
    }


def _bs_tb():
    return {
        "rows": [
            {
                "entity": "AT", "level_1": "Assets", "level_2": "Current assets",
                "level_3": "Trade receivables", "level_4": "Receivables",
                "gl_account_id": "1200", "account_name": "Forderungen",
                "amounts": {"DEC2023": 600.0, "CM2024-12": 650.0, "2024-12": 650.0},
            },
        ],
    }


class TestGlMasterRun:
    def test_writes_workbook_and_returns_susa_shape(self, tmp_path, monkeypatch):
        from app.services import fin_compat_master_from_gl as mfg
        from databook_helpers import master_workbook_path

        # Real reshape + real workbook write; only the TB data source is stubbed.
        monkeypatch.setattr(mfg, "build_pl_trial_balance", lambda *a, **k: _pl_tb())
        monkeypatch.setattr(mfg, "build_bs_trial_balance", lambda *a, **k: _bs_tb())

        out_folder = tmp_path / "out"
        session = _make_session(entity_rows=[("AT", "Austria GmbH")])
        client = _client(session, _ADMIN)

        resp = client.post(
            "/api/v1/fdd/run/databook/gl-master",
            json={
                "session_id": "case123",
                "entity_codes": ["AT"],
                "fiscal_years": [2023, 2024],
                "fy_end_month": 12,
                "output_folder": str(out_folder),
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Result shape mirrors run_databook_susa (keys _databook_after_susa_run reads).
        for key in ("success", "output_path", "master_path", "output_file", "output_filename"):
            assert key in body, f"missing result key {key}"
        assert body["success"] is True

        expected = master_workbook_path("case123", str(out_folder))
        assert body["master_path"] == str(expected)
        assert expected.is_file()

        from openpyxl import load_workbook

        wb = load_workbook(expected)
        try:
            assert "Master_BS" in wb.sheetnames
            assert "Master_PL" in wb.sheetnames
        finally:
            wb.close()

    def test_rejects_entities_outside_allow_list(self, monkeypatch):
        from app.services import fin_compat_master_from_gl as mfg

        called = {"build": False}

        def _should_not_run(*a, **k):
            called["build"] = True
            return [], []

        monkeypatch.setattr(mfg, "build_master_frames_from_gl", _should_not_run)

        # Restricted user may see AT only, but requests DE.
        session = _make_session(visibility_codes=["AT"])
        client = _client(session, _RESTRICTED)
        resp = client.post(
            "/api/v1/fdd/run/databook/gl-master",
            json={"session_id": "case1", "entity_codes": ["DE"], "fiscal_years": [2024]},
        )
        assert resp.status_code == 400
        assert called["build"] is False

    def test_rejects_empty_fiscal_years(self):
        session = _make_session()
        client = _client(session, _ADMIN)
        resp = client.post(
            "/api/v1/fdd/run/databook/gl-master",
            json={"session_id": "case1", "entity_codes": ["AT"], "fiscal_years": []},
        )
        assert resp.status_code == 400

    def test_rejects_session_id_path_traversal(self, tmp_path, monkeypatch):
        # L1 regression: a session_id that escapes the output root must be rejected
        # (400) before any build runs.
        from app.services import fin_compat_master_from_gl as mfg

        called = {"build": False}

        def _should_not_run(*a, **k):
            called["build"] = True
            return [], []

        monkeypatch.setattr(mfg, "build_master_frames_from_gl", _should_not_run)

        out_folder = tmp_path / "out"
        session = _make_session(entity_rows=[("AT", "Austria GmbH")])
        client = _client(session, _ADMIN)
        resp = client.post(
            "/api/v1/fdd/run/databook/gl-master",
            json={
                "session_id": "../../../../etc/evil",
                "entity_codes": ["AT"],
                "fiscal_years": [2024],
                "output_folder": str(out_folder),
            },
        )
        assert resp.status_code == 400
        assert called["build"] is False
