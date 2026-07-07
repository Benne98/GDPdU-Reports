"""
Tests for the legacy compatibility layer.

Covers:
  (a) pl-statement response shape (keys / col_labels / rows tree)
  (b) latest_period metric
  (c) gl-journal-lines cursor pagination

Strategy: FastAPI TestClient with auth dependency overridden to a dummy user,
and DB dependency overridden to an in-memory SQLite session populated with
synthetic fixture rows.  No real client data.

SQLite quirks handled:
  - TO_CHAR, EXTRACT: mocked at the SQL layer by patching service functions.
  - Since the services use raw SQL (SQLAlchemy text()), we monkeypatch the
    key builder functions so tests exercise router → service glue without a
    live Postgres DB.
"""
from __future__ import annotations

import base64
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.auth import User
from app.db import get_read_session, get_session
from app.main import app


# ---------------------------------------------------------------------------
# Auth override — always returns a dummy user
# ---------------------------------------------------------------------------
_DUMMY_USER = User(
    user_id=1,
    email="test@finssentials.com",
    display_name="Test User",
    is_admin=True,
)


def _override_current_user():
    return _DUMMY_USER


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    """Prevent this module's ``_make_client`` overrides (get_session /
    get_read_session / current_user) from leaking into later test files in the
    same process — otherwise a plain ``TestClient`` elsewhere (e.g.
    test_budget_api::test_get_requires_auth) inherits an authed dummy user and a
    canned session, turning an expected 401 into a 500."""
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------

_STRUCTURE_ROWS = [
    {
        "pl_line_id": 1, "sort_order": 10, "line_code": "NET_SALES",
        "row_type": "mapping", "balance_title": "Net Sales", "details": None,
        "calc_type": None, "level_2": "Income", "level_3": "Net sales",
        "level_4": None, "gl_account_id": None, "invert_delta": False,
        "is_bold": True, "kpi_code": None,
    },
    {
        "pl_line_id": 2, "sort_order": 20, "line_code": "GROSS_PROFIT",
        "row_type": "computed", "balance_title": "Gross Profit", "details": None,
        "calc_type": "sum", "level_2": None, "level_3": None, "level_4": None,
        "gl_account_id": None, "invert_delta": False, "is_bold": True, "kpi_code": None,
    },
]

_GRAIN_ROWS = [
    {
        "level_2": "Income", "level_3": "Net sales", "level_4": None,
        "gl_account_id": None, "account_number_group": "AT4000",
        "account_name": "Revenue DE",
        "py_cm": -10000.0, "pm": -8000.0, "cm": -9000.0,
        "ytd": -90000.0, "ytd_py": -95000.0,
    }
]

# Presented = amount * -1, so py_cm=-10000 → presented +10000
_EXPECTED_CM = 9000.0   # after * -1 of -9000


# ---------------------------------------------------------------------------
# Worked-example fixtures — GDPdU P&L structure + running-sum subtotals.
# Mirrors the real dim_pl_structure semantics (statements.py): subtotals/calcs
# are the running cumulative sum of the mapping lines above them.  Includes two
# balance-sheet rows (line_code 'BS_*', sort_order >= 1010) that MUST be excluded
# from the P&L response.  Amounts in grains are already PRESENTED (income +,
# expense −); the builder never re-signs them.
# ---------------------------------------------------------------------------

def _struct(pl_line_id, sort_order, line_code, row_type, *,
            level_2=None, level_3=None, level_4=None, is_bold=False):
    return {
        "pl_line_id": pl_line_id, "sort_order": sort_order, "line_code": line_code,
        "row_type": row_type, "balance_title": line_code.replace("_", " ").title(),
        "details": None, "calc_type": None,
        "level_2": level_2, "level_3": level_3, "level_4": level_4,
        "gl_account_id": None, "invert_delta": False, "is_bold": is_bold,
        "kpi_code": None,
    }


_WE_STRUCTURE = [
    # --- P&L (sort_order < 1000) ---
    _struct(1, 1, "NET_SALES", "mapping", level_2="Income", level_3="Net sales", is_bold=True),
    _struct(2, 2, "FINISHED_GOODS_WIP", "mapping", level_2="Income", level_3="Finished goods WIP"),
    _struct(3, 3, "OWN_WORK_CAPITALISED", "mapping", level_2="Income", level_3="Own work capitalised"),
    _struct(4, 4, "TOTAL_OUTPUT", "subtotal", is_bold=True),
    _struct(5, 5, "COST_OF_MATERIALS", "mapping", level_2="Expense", level_3="Cost of materials"),
    _struct(6, 6, "GROSS_PROFIT", "calc", is_bold=True),
    # --- Balance sheet (sort_order >= 1010, BS_ prefix) — must NOT appear ---
    _struct(40, 1010, "BS_ASSETS_TOTAL", "grandtotal", is_bold=True),
    _struct(41, 1020, "BS_CASH", "mapping", level_2="Assets", level_3="Cash"),
]


def _grain(level_2, level_3, cm, ang):
    return {
        "level_2": level_2, "level_3": level_3, "level_4": None,
        "gl_account_id": None, "account_number_group": ang,
        "account_name": level_3,
        "py_cm": 0.0, "pm": 0.0, "cm": cm, "ytd": cm, "ytd_py": 0.0,
    }


_WE_GRAIN = [
    _grain("Income", "Net sales", 6_550_772.0, "AT4000"),
    _grain("Income", "Finished goods WIP", 10_014_538.0, "AT4100"),
    # OWN_WORK_CAPITALISED has no grain → contributes 0.
    _grain("Expense", "Cost of materials", -10_182_291.0, "AT5000"),
    # Balance-sheet movement: must never leak into the P&L running sum.
    _grain("Assets", "Cash", 999_999.0, "AT2700"),
]


def _make_mock_session(
    structure=None, grain_rows=None, plan_rows=None, entity_rows=None,
):
    """Return a MagicMock Session whose execute().fetchall() returns canned data."""
    session = MagicMock()

    def _execute(stmt, params=None):
        sql = str(stmt) if not isinstance(stmt, str) else stmt
        result = MagicMock()
        rows: list[Any] = []

        if any(t in sql for t in ("dim_pl_structure", "dim_bs_structure", "dim_cf_structure")):
            rows = [_dict_row(r) for r in (structure or _STRUCTURE_ROWS)]
        elif "fact_gl_entry" in sql and "posting_date" in sql:
            # latest_period query
            rows = [_dict_row({"period": "2023-12", "iso_year": 2023, "iso_week": 49})]
        elif "fact_gl_line" in sql or "pl_grain" in sql or "fact_gl_entry" in sql:
            rows = [_dict_row(r) for r in (grain_rows or _GRAIN_ROWS)]
        elif "fact_gl_plan" in sql:
            rows = plan_rows or []
        elif "dim_legal_entity" in sql:
            rows = entity_rows or [
                _dict_row({"legal_entity_code": "AT", "entity_name": "Austria GmbH",
                           "entity_prefix": "AT", "is_consolidation": False}),
            ]
        elif "v_gl_line_enriched" in sql:
            rows = [_dict_row({
                "booking_line_id": 1,
                "legal_entity_code": "AT",
                "fiscal_year": 2023,
                "journal_entry_group_number": "AT0001",
                "line_number": 1,
                "posting_date": "2023-12-15",
                "document_date": "2023-12-15",
                "reference_document_number": "DOC001",
                "document_type_code": "SA",
                "gl_account_id": "AT40001",
                "account_name": "Revenue",
                "level_0": "PL", "level_1": "Income",
                "level_2": "Income", "level_3": "Net sales",
                "level_4": None,
                "amount": -9000.0,
                "line_note": "Test booking",
                "posting_type": "normal",
                "customer_id": None, "supplier_id": None,
            })]
        else:
            rows = []

        result.fetchall.return_value = rows
        result.fetchone.return_value = rows[0] if rows else None
        return result

    session.execute.side_effect = _execute
    return session


class _DictRow:
    """Minimal SQLAlchemy Row-like with _mapping attribute."""
    def __init__(self, d: dict):
        self._mapping = d
        for k, v in d.items():
            setattr(self, k, v)

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self._mapping.values())[key]
        return self._mapping[key]

    def get(self, key, default=None):
        return self._mapping.get(key, default)


def _dict_row(d: dict) -> _DictRow:
    return _DictRow(d)


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------

def _make_client(session=None):
    mock_session = session or _make_mock_session()
    app.dependency_overrides[get_session] = lambda: mock_session
    # The compat routers depend on get_read_session (scoped statement_timeout);
    # override it to the same mock so tests don't hit a real DB.
    app.dependency_overrides[get_read_session] = lambda: mock_session
    from app.auth import current_user
    app.dependency_overrides[current_user] = _override_current_user
    client = TestClient(app, raise_server_exceptions=False)
    return client, mock_session


# ---------------------------------------------------------------------------
# (a) pl-statement response shape
# ---------------------------------------------------------------------------

class TestPlStatementShape:

    def test_keys_present(self):
        """Response must contain statement, period_grain, year, month, col_labels, rows."""
        with patch(
            "app.services.fin_compat_pl.pl_grain_sql_month",
            return_value=("SELECT 1", {}),
        ), patch(
            "app.services.fin_compat_pl._load_plan_map",
            return_value={},
        ):
            client, _ = _make_client()
            resp = client.get(
                "/api/v1/financials/pl-statement",
                params={"year": 2023, "month": 12},
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "statement" in data
        assert data["statement"] == "pl"
        assert "col_labels" in data
        assert "rows" in data
        assert isinstance(data["rows"], list)

    def test_col_labels_structure(self):
        """col_labels must include py_cm, pm, cm, ytd, ytd_py."""
        with patch(
            "app.services.fin_compat_pl.pl_grain_sql_month",
            return_value=("SELECT 1", {}),
        ), patch(
            "app.services.fin_compat_pl._load_plan_map",
            return_value={},
        ):
            client, _ = _make_client()
            resp = client.get(
                "/api/v1/financials/pl-statement",
                params={"year": 2023, "month": 12},
            )

        data = resp.json()
        labels = data["col_labels"]
        for key in ("py_cm", "pm", "cm", "ytd", "ytd_py"):
            assert key in labels, f"col_labels missing: {key}"

    def test_rows_have_required_fields(self):
        """Each row must have line_code, label, row_kind, amounts."""
        with patch(
            "app.services.fin_compat_pl.pl_grain_sql_month",
            return_value=("SELECT 1", {}),
        ), patch(
            "app.services.fin_compat_pl._load_plan_map",
            return_value={},
        ):
            client, _ = _make_client()
            resp = client.get(
                "/api/v1/financials/pl-statement",
                params={"year": 2023, "month": 12},
            )

        data = resp.json()
        for row in data["rows"]:
            assert "line_code" in row, f"Row missing line_code: {row}"
            assert "label" in row
            assert "row_kind" in row
            assert "amounts" in row
            amounts = row["amounts"]
            for k in ("py_cm", "pm", "cm", "ytd", "ytd_py"):
                assert k in amounts, f"amounts missing {k} in row {row.get('line_code')}"

    def test_period_grain_week_requires_iso_params(self):
        """period_grain=week without iso_year/iso_week should return 422."""
        client, _ = _make_client()
        resp = client.get(
            "/api/v1/financials/pl-statement",
            params={"period_grain": "week"},
        )
        assert resp.status_code == 422

    def test_month_grain_requires_year_month(self):
        """period_grain=month without year/month should return 422."""
        client, _ = _make_client()
        resp = client.get("/api/v1/financials/pl-statement")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# (b) latest_period metric
# ---------------------------------------------------------------------------

class TestLatestPeriod:

    def test_returns_latest_period_shape(self):
        """GET /api/v1/metrics?metric=latest_period must return correct shape."""
        client, _ = _make_client()
        resp = client.get("/api/v1/metrics", params={"metric": "latest_period"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["metric"] == "latest_period"
        assert "period" in data
        assert "year" in data
        assert "month" in data
        assert "iso_year" in data
        assert "iso_week" in data

    def test_latest_period_values_from_db(self):
        """Values should match what the mock DB returns (2023-12)."""
        client, _ = _make_client()
        resp = client.get("/api/v1/metrics", params={"metric": "latest_period"})
        data = resp.json()
        assert data["period"] == "2023-12"
        assert data["year"] == 2023
        assert data["month"] == 12

    def test_latest_period_excludes_synthetic_net_profit_rows(self):
        """reporting-v2 Phase 3: the synthetic net-profit equity rows (posted at the
        FY year-end, entry_type='net_profit') must NOT drive the reporting anchor.
        The latest_period SQL must filter them out so the anchor stays on the last
        REAL movement — keeping gl_rows mode anchor-identical to report_inject/live.
        """
        client, session = _make_client()
        resp = client.get("/api/v1/metrics", params={"metric": "latest_period"})
        assert resp.status_code == 200, resp.text
        # the executed latest_period SQL must exclude net_profit rows
        executed = [
            str(call.args[0]) for call in session.execute.call_args_list if call.args
        ]
        lp_sql = [s for s in executed if "fact_gl_entry" in s and "posting_date" in s]
        assert lp_sql, "latest_period query was not executed"
        assert any("<> 'net_profit'" in s for s in lp_sql), (
            "latest_period must exclude entry_type='net_profit' rows"
        )

    def test_unknown_metric_returns_400(self):
        """Non-implemented metric should return 400."""
        client, _ = _make_client()
        resp = client.get("/api/v1/metrics", params={"metric": "revenue_total"})
        assert resp.status_code == 400

    def test_entities_endpoint(self):
        """GET /api/v1/entities should return a list with legal_entity_code."""
        client, _ = _make_client()
        resp = client.get("/api/v1/entities")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert "legal_entity_code" in data[0]
        assert "entity_name" in data[0]

    def test_health_ready_no_auth(self):
        """GET /health/ready should work without auth token."""
        # Don't pass auth override — the endpoint has no auth
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health/ready")
        # Should return 200 regardless of DB (status 'ok' or 'degraded')
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert data["status"] in ("ok", "degraded")
        assert "database" in data


# ---------------------------------------------------------------------------
# (c) gl-journal-lines cursor pagination
# ---------------------------------------------------------------------------

class TestGlJournalLinesPagination:

    def _encode_cursor(self, id_: int) -> str:
        return base64.urlsafe_b64encode(str(id_).encode()).decode()

    def test_returns_correct_shape(self):
        """Response must have grain, total_returned, has_more, next_cursor, rows."""
        client, _ = _make_client()
        resp = client.get("/api/v1/facts/gl-journal-lines")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "grain" in data
        assert data["grain"] == "line"
        assert "total_returned" in data
        assert "has_more" in data
        assert "next_cursor" in data
        assert "rows" in data
        assert isinstance(data["rows"], list)

    def test_cursor_advances_page(self):
        """When a cursor is provided, it should add booking_line_id > X filter."""
        # Build a session that records what conditions were passed
        session = MagicMock()
        captured_sql: list[str] = []

        def _execute(stmt, params=None):
            sql_str = str(stmt)
            captured_sql.append(sql_str)
            result = MagicMock()
            result.fetchall.return_value = []
            return result

        session.execute.side_effect = _execute
        # We also need entity resolution to return None
        session.execute.side_effect = _execute

        client, _ = _make_client(session)
        cursor = self._encode_cursor(42)
        resp = client.get("/api/v1/facts/gl-journal-lines", params={"cursor": cursor, "sort_by": "date"})
        assert resp.status_code == 200

        # The SQL executed against v_gl_line_enriched should include cursor_id param
        all_sql = " ".join(captured_sql)
        assert "booking_line_id" in all_sql

    def test_has_more_false_when_within_limit(self):
        """When returned rows ≤ limit, has_more should be False."""
        client, _ = _make_client()
        resp = client.get("/api/v1/facts/gl-journal-lines", params={"limit": 100})
        data = resp.json()
        # Our mock returns 1 row, far less than limit=100
        assert data["has_more"] is False
        assert data["next_cursor"] is None

    def test_statement_type_param_maps_to_level_0(self):
        """statement_type='PL' should be mapped to level_0 filter."""
        session = MagicMock()
        captured_params: list[dict] = []

        def _execute(stmt, params=None):
            if params:
                captured_params.append(dict(params))
            result = MagicMock()
            result.fetchall.return_value = []
            return result

        session.execute.side_effect = _execute

        client, _ = _make_client(session)
        client.get("/api/v1/facts/gl-journal-lines", params={"statement_type": "PL"})

        # Check that level_0='PL' was passed in params (not statement_type)
        all_params = {}
        for p in captured_params:
            all_params.update(p)
        assert all_params.get("level_0") == "PL"

    def test_invalid_cursor_returns_400(self):
        """An invalid cursor string should return 400."""
        client, _ = _make_client()
        resp = client.get(
            "/api/v1/facts/gl-journal-lines",
            params={"cursor": "!!!not-valid-base64!!!", "sort_by": "date"},
        )
        assert resp.status_code == 400

    def test_amount_abs_sort_no_cursor(self):
        """sort_by=amount_abs should not apply cursor filter."""
        session = MagicMock()
        captured_params: list[dict] = []

        def _execute(stmt, params=None):
            if params:
                captured_params.append(dict(params))
            result = MagicMock()
            result.fetchall.return_value = []
            return result

        session.execute.side_effect = _execute

        client, _ = _make_client(session)
        cursor = self._encode_cursor(99)
        client.get(
            "/api/v1/facts/gl-journal-lines",
            params={"sort_by": "amount_abs", "cursor": cursor},
        )

        all_params = {}
        for p in captured_params:
            all_params.update(p)
        # cursor_id should NOT be in params when sort_by=amount_abs
        assert "cursor_id" not in all_params


# ---------------------------------------------------------------------------
# (d) P&L running-sum subtotals (worked example) + BS exclusion
# ---------------------------------------------------------------------------

class TestPlRunningSumSubtotals:
    """Subtotals/calcs are running cumulative sums of the mapping lines above
    them (mirrors app/services/statements.py) — no hard-coded legacy formula."""

    def _build(self):
        from app.services.fin_compat_pl import build_pl_statement_compat

        session = _make_mock_session(structure=_WE_STRUCTURE, grain_rows=_WE_GRAIN)
        out = build_pl_statement_compat(
            session, period_grain="month", year=2025, month=7,
        )
        return {r["line_code"]: r for r in out["rows"]}

    def test_total_output_is_running_sum(self):
        """TOTAL_OUTPUT = NET_SALES + FG&WIP + OWN_WORK = 6,550,772 + 10,014,538 + 0."""
        rows = self._build()
        assert rows["NET_SALES"]["amounts"]["cm"] == 6_550_772.0
        assert rows["FINISHED_GOODS_WIP"]["amounts"]["cm"] == 10_014_538.0
        assert rows["OWN_WORK_CAPITALISED"]["amounts"]["cm"] == 0.0
        assert rows["TOTAL_OUTPUT"]["amounts"]["cm"] == 16_565_310.0

    def test_gross_profit_running_sum(self):
        """GROSS_PROFIT = TOTAL_OUTPUT + COST_OF_MATERIALS = 16,565,310 − 10,182,291."""
        rows = self._build()
        assert rows["COST_OF_MATERIALS"]["amounts"]["cm"] == -10_182_291.0
        assert rows["GROSS_PROFIT"]["amounts"]["cm"] == 6_383_019.0

    def test_ytd_column_also_running_sum(self):
        """Running-sum applies per column: YTD TOTAL_OUTPUT mirrors the cm chain."""
        rows = self._build()
        assert rows["TOTAL_OUTPUT"]["amounts"]["ytd"] == 16_565_310.0
        assert rows["GROSS_PROFIT"]["amounts"]["ytd"] == 6_383_019.0

    def test_subtotal_row_kind(self):
        """mapping → 'line'; subtotal/calc → 'subtotal'."""
        rows = self._build()
        assert rows["NET_SALES"]["row_kind"] == "line"
        assert rows["TOTAL_OUTPUT"]["row_kind"] == "subtotal"
        assert rows["GROSS_PROFIT"]["row_kind"] == "subtotal"

    def test_balance_sheet_rows_excluded(self):
        """No BS_ row may appear in the P&L statement response."""
        rows = self._build()
        assert "BS_ASSETS_TOTAL" not in rows
        assert "BS_CASH" not in rows
        for code in rows:
            assert not code.startswith("BS_"), f"BS row leaked into P&L: {code}"

    def test_bs_movement_not_in_running_sum(self):
        """The 999,999 Cash (BS) movement must NOT inflate any P&L subtotal."""
        rows = self._build()
        # If the BS grain leaked in, TOTAL_OUTPUT/GROSS_PROFIT would be off by 999,999.
        assert rows["TOTAL_OUTPUT"]["amounts"]["cm"] == 16_565_310.0
        assert rows["GROSS_PROFIT"]["amounts"]["cm"] == 6_383_019.0

    def test_consolidation_running_sum(self):
        """Consolidation builder uses running-sum per entity column too."""
        from app.services.fin_compat_pl import build_pl_consolidation

        consl_grain = [dict(g, entity_prefix="AT") for g in _WE_GRAIN]
        entity_rows = [
            _dict_row({"legal_entity_code": "AT", "entity_name": "Austria GmbH",
                       "entity_prefix": "AT", "is_consolidation": False}),
        ]
        session = _make_mock_session(
            structure=_WE_STRUCTURE, grain_rows=consl_grain, entity_rows=entity_rows,
        )
        out = build_pl_consolidation(session, period_grain="month", year=2025, month=7)
        rows = {r["id"]: r for r in out["rows"]}
        assert rows["pl-TOTAL_OUTPUT"]["entity_amounts"]["AT"] == 16_565_310.0
        assert rows["pl-GROSS_PROFIT"]["entity_amounts"]["AT"] == 6_383_019.0
        assert "pl-BS_CASH" not in rows


# ---------------------------------------------------------------------------
# (e) Annual exit-readiness flow P&L (FY/YTD/LTM running-sum) + BS exclusion
# ---------------------------------------------------------------------------
# Mirrors the GDPdU dim_pl_structure semantics for the exit-readiness
# "Jahresscheiben" view: subtotals/calcs are the running cumulative sum of the
# mapping lines above them, evaluated independently per FLOW_KEY.  Amounts in
# the grain dicts are already PRESENTED (income +, expense −); the builder never
# re-signs them.  A balance-sheet grain (BS_*, sort_order >= 1010) must never
# leak into a P&L subtotal.

_ER_FLOW_KEY_SET = ("fy1", "fy2", "fy3", "ytd", "ltm", "ytd_py", "ltm_py")


def _annual_grain(level_2, level_3, ang, **vals):
    g = {
        "level_2": level_2, "level_3": level_3, "level_4": None,
        "gl_account_id": None, "account_number_group": ang,
        "account_name": level_3,
    }
    for k in _ER_FLOW_KEY_SET:
        g[k] = float(vals.get(k, 0.0))
    return g


# Worked example (see build_pl_annual_compat docstring):
#   NET_SALES fy3=10, FINISHED_GOODS_WIP fy3=6, OWN_WORK fy3=0 → TOTAL_OUTPUT fy3=16
#   COST_OF_MATERIALS fy3=-10                                  → GROSS_PROFIT  fy3=6
_ANNUAL_GRAIN = [
    _annual_grain("Income", "Net sales", "AT4000",
                  fy1=8, fy2=9, fy3=10, ytd=7, ytd_py=6, ltm=11, ltm_py=10),
    _annual_grain("Income", "Finished goods WIP", "AT4100",
                  fy1=3, fy2=4, fy3=6, ytd=3, ytd_py=2, ltm=5, ltm_py=4),
    # OWN_WORK_CAPITALISED has no grain → contributes 0.
    _annual_grain("Expense", "Cost of materials", "AT5000",
                  fy1=-6, fy2=-8, fy3=-10, ytd=-5, ytd_py=-4, ltm=-9, ltm_py=-7),
    # Balance-sheet movement: must never leak into the P&L running sum.
    _annual_grain("Assets", "Cash", "AT2700", fy3=999_999.0),
]


class TestPlAnnualRunningSum:
    """Annual ErFlow P&L: running-sum subtotals per FLOW_KEY + BS exclusion."""

    def _build(self, structure=None, grains=None):
        from app.services.fin_compat_pl import _build_annual_rows

        out = _build_annual_rows(
            structure or _WE_STRUCTURE, grains or _ANNUAL_GRAIN, 2025, 7,
        )
        return {r["line_code"]: r for r in out["rows"]}, out

    def test_total_output_running_sum_fy3(self):
        """fy3: NET_SALES 10 + FG&WIP 6 + OWN_WORK 0 = TOTAL_OUTPUT 16."""
        rows, _ = self._build()
        assert rows["NET_SALES"]["amounts"]["fy3"] == 10
        assert rows["FINISHED_GOODS_WIP"]["amounts"]["fy3"] == 6
        assert rows["OWN_WORK_CAPITALISED"]["amounts"]["fy3"] == 0
        assert rows["TOTAL_OUTPUT"]["amounts"]["fy3"] == 16

    def test_gross_profit_running_sum_fy3(self):
        """fy3: TOTAL_OUTPUT 16 + COST_OF_MATERIALS (−10) = GROSS_PROFIT 6."""
        rows, _ = self._build()
        assert rows["COST_OF_MATERIALS"]["amounts"]["fy3"] == -10
        assert rows["GROSS_PROFIT"]["amounts"]["fy3"] == 6

    def test_full_ytd_and_ltm_chains(self):
        """Running-sum applies independently to every FLOW_KEY column."""
        rows, _ = self._build()
        # YTD: 7 + 3 + 0 = 10 ; 10 + (−5) = 5
        assert rows["TOTAL_OUTPUT"]["amounts"]["ytd"] == 10
        assert rows["GROSS_PROFIT"]["amounts"]["ytd"] == 5
        # LTM: 11 + 5 + 0 = 16 ; 16 + (−9) = 7
        assert rows["TOTAL_OUTPUT"]["amounts"]["ltm"] == 16
        assert rows["GROSS_PROFIT"]["amounts"]["ltm"] == 7
        # fy2: 9 + 4 + 0 = 13 ; 13 + (−8) = 5
        assert rows["TOTAL_OUTPUT"]["amounts"]["fy2"] == 13
        assert rows["GROSS_PROFIT"]["amounts"]["fy2"] == 5

    def test_deltas(self):
        """delta_fy = fy3−fy2, delta_ytd = ytd−ytd_py, delta_ltm = fy_f−ltm_py."""
        rows, _ = self._build()
        gp = rows["GROSS_PROFIT"]["deltas"]
        assert gp["delta_fy"] == 1     # 6 − 5
        assert gp["delta_ytd"] == 1    # 5 − 4
        assert gp["delta_ltm"] == -2   # fy_f (ytd 5) − ltm_py 7

    def test_col_labels_and_fy1_unlabelled(self):
        """col_labels match legacy ErFlow (6 cols); fy1 carried in amounts only."""
        _, out = self._build()
        assert out["col_labels"] == {
            "fy2": "FY23A", "fy3": "FY24A",
            "ytd_py": "YTDJul24A", "ytd": "YTDJul25A",
            "ltm_py": "LTMJul24A", "ltm": "LTMJul25A",
            "fy_f": "FY25F",
            "plan_cm": "FY26P",
        }
        rows = {r["line_code"]: r for r in out["rows"]}
        assert "fy1" in rows["NET_SALES"]["amounts"]
        assert "fy1" not in out["col_labels"]

    def test_row_kind(self):
        """mapping → 'line'; subtotal/calc → 'subtotal'."""
        rows, _ = self._build()
        assert rows["NET_SALES"]["row_kind"] == "line"
        assert rows["TOTAL_OUTPUT"]["row_kind"] == "subtotal"
        assert rows["GROSS_PROFIT"]["row_kind"] == "subtotal"

    def test_no_bs_rows_and_no_bs_leak(self):
        """No BS_ row appears, and the 999,999 Cash movement inflates nothing."""
        rows, _ = self._build()
        assert "BS_ASSETS_TOTAL" not in rows
        assert "BS_CASH" not in rows
        for code in rows:
            assert not code.startswith("BS_"), f"BS row leaked into P&L: {code}"
        assert rows["TOTAL_OUTPUT"]["amounts"]["fy3"] == 16
        assert rows["GROSS_PROFIT"]["amounts"]["fy3"] == 6

    def test_invert_delta_sign(self):
        """invert_delta negates the three deltas (cost line example)."""
        structure = [dict(r) for r in _WE_STRUCTURE]
        for r in structure:
            if r["line_code"] == "COST_OF_MATERIALS":
                r["invert_delta"] = True
        rows, _ = self._build(structure=structure)
        com = rows["COST_OF_MATERIALS"]
        assert com["invert_delta"] is True
        # raw delta_fy = fy3 − fy2 = −10 − (−8) = −2 → inverted → +2
        assert com["deltas"]["delta_fy"] == 2
        # raw delta_ytd = −5 − (−4) = −1 → +1 ; raw delta_ltm = −9 − (−7) = −2 → +2
        assert com["deltas"]["delta_ytd"] == 1
        assert com["deltas"]["delta_ltm"] == -2  # fy_f (−5) − ltm_py (−7) = +2 → inverted

    def test_build_pl_annual_compat_glue(self):
        """End-to-end through build_pl_annual_compat with a mock session."""
        from app.services.fin_compat_pl import build_pl_annual_compat

        def _execute(stmt, params=None):
            sql = str(stmt)
            result = MagicMock()
            if any(t in sql for t in ("dim_pl_structure", "dim_bs_structure", "dim_cf_structure")):
                rows = [_dict_row(r) for r in _WE_STRUCTURE]
            else:
                rows = [_dict_row(r) for r in _ANNUAL_GRAIN]
            result.fetchall.return_value = rows
            result.fetchone.return_value = rows[0] if rows else None
            return result

        session = MagicMock()
        session.execute.side_effect = _execute

        out = build_pl_annual_compat(session, year=2025, month=7, entity=None)
        assert out["statement"] == "pl"
        assert out["year"] == 2025 and out["month"] == 7
        assert out["col_labels"]["ltm"] == "LTMJul25A"
        assert out["col_labels"]["fy_f"] == "FY25F"
        rows = {r["line_code"]: r for r in out["rows"]}
        assert rows["TOTAL_OUTPUT"]["amounts"]["fy3"] == 16
        assert rows["GROSS_PROFIT"]["amounts"]["fy3"] == 6
        assert rows["GROSS_PROFIT"]["amounts"]["fy_f"] == rows["GROSS_PROFIT"]["amounts"]["ytd"]
        assert rows["GROSS_PROFIT"]["amounts"]["ltm"] == 7
        assert all(not c.startswith("BS_") for c in rows)


# ---------------------------------------------------------------------------
# (f) Annual ENTITY-BREAKDOWN consolidation (full last FY, per legal entity)
# ---------------------------------------------------------------------------
# Per-entity running-sum over the LAST FULL fiscal year (fy_year = year - 1).
# Each grain row carries a single ``fy`` value per (level filter, entity_prefix).
# Amounts are already PRESENTED (income +, expense −); the builder never
# re-signs.  BS grains (BS_*, sort_order >= 1010) must never leak into a P&L
# subtotal, and presentation-only 'title'/'kpi' rows are dropped (flat table).
#
# Worked example (entities AT + DE):
#   line                 AT     DE    aggregated
#   NET_SALES            100    60    160
#   FINISHED_GOODS_WIP    20    10     30
#   OWN_WORK_CAPITALISED   0     0      0   (no grain)
#   TOTAL_OUTPUT (sub)   120    70    190
#   COST_OF_MATERIALS    -40   -25    -65
#   GROSS_PROFIT (calc)   80    45    125

def _consl_ytd_grain(level_2, level_3, ang, entity_prefix, ytd):
    return {
        "level_2": level_2, "level_3": level_3, "level_4": None,
        "account_number_group": ang, "entity_prefix": entity_prefix,
        "ytd": float(ytd),
    }


_CONSOL_YTD_GRAIN = [
    # AT entity
    _consl_ytd_grain("Income", "Net sales", "AT4000", "AT", 100),
    _consl_ytd_grain("Income", "Finished goods WIP", "AT4100", "AT", 20),
    _consl_ytd_grain("Expense", "Cost of materials", "AT5000", "AT", -40),
    # DE entity
    _consl_ytd_grain("Income", "Net sales", "DE4000", "DE", 60),
    _consl_ytd_grain("Income", "Finished goods WIP", "DE4100", "DE", 10),
    _consl_ytd_grain("Expense", "Cost of materials", "DE5000", "DE", -25),
    # OWN_WORK_CAPITALISED has no grain → contributes 0 for both entities.
    # Balance-sheet movement: must never leak into a P&L subtotal.
    _consl_ytd_grain("Assets", "Cash", "AT2700", "AT", 999_999.0),
]

_CONSL_ENTITY_ROWS = [
    _dict_row({"legal_entity_code": "AT", "entity_prefix": "AT", "entity_name": "Austria GmbH"}),
    _dict_row({"legal_entity_code": "DE", "entity_prefix": "DE", "entity_name": "Germany GmbH"}),
]


class TestPlAnnualConsolidation:
    """Annual YTD entity breakdown: running-sum per entity column."""

    def _build(self, structure=None, grains=None, ent_rows=None):
        from app.services.fin_compat_pl import _build_annual_consolidation_rows

        out = _build_annual_consolidation_rows(
            structure or _WE_STRUCTURE,
            grains if grains is not None else _CONSOL_YTD_GRAIN,
            ent_rows or _CONSL_ENTITY_ROWS,
            2025, 7,
        )
        return {r["id"]: r for r in out["rows"]}, out

    def test_running_sum_per_entity_column(self):
        """TOTAL_OUTPUT = Σ mapping lines, computed independently per entity."""
        rows, _ = self._build()
        assert rows["pl-NET_SALES"]["entity_amounts"] == {"AT": 100, "DE": 60}
        assert rows["pl-FINISHED_GOODS_WIP"]["entity_amounts"] == {"AT": 20, "DE": 10}
        assert rows["pl-OWN_WORK_CAPITALISED"]["entity_amounts"] == {"AT": 0, "DE": 0}
        # 100+20+0 = 120 (AT); 60+10+0 = 70 (DE)
        assert rows["pl-TOTAL_OUTPUT"]["entity_amounts"] == {"AT": 120, "DE": 70}
        # 120-40 = 80 (AT); 70-25 = 45 (DE)
        assert rows["pl-GROSS_PROFIT"]["entity_amounts"] == {"AT": 80, "DE": 45}

    def test_aggregated_is_sum_of_entities(self):
        """aggregated == Σ entity_amounts and consolidation == aggregated."""
        rows, _ = self._build()
        for row in rows.values():
            ea = row["entity_amounts"]
            assert row["aggregated"] == round(sum(ea.values()), 2), row["id"]
            assert row["consolidation"] == row["aggregated"], row["id"]
            assert row["ic_eliminations"] == 0.0
        assert rows["pl-TOTAL_OUTPUT"]["aggregated"] == 190
        assert rows["pl-GROSS_PROFIT"]["consolidation"] == 125

    def test_no_balance_sheet_rows(self):
        """No BS_ row may appear, and the 999,999 Cash movement inflates nothing."""
        rows, _ = self._build()
        assert "pl-BS_ASSETS_TOTAL" not in rows
        assert "pl-BS_CASH" not in rows
        for rid in rows:
            assert "BS_" not in rid, f"BS row leaked into consolidation: {rid}"
        assert rows["pl-TOTAL_OUTPUT"]["entity_amounts"] == {"AT": 120, "DE": 70}

    def test_row_kind_and_flat_shape(self):
        """mapping → 'line'; subtotal/calc → 'subtotal'; rows are flat."""
        rows, _ = self._build()
        assert rows["pl-NET_SALES"]["row_kind"] == "line"
        assert rows["pl-TOTAL_OUTPUT"]["row_kind"] == "subtotal"
        assert rows["pl-GROSS_PROFIT"]["row_kind"] == "subtotal"
        for row in rows.values():
            assert row["has_children"] is False
            assert row["children"] == []
            assert row["row_kind"] in ("line", "subtotal")

    def test_response_envelope(self):
        """col_label, entities order, and top-level keys per the contract."""
        _, out = self._build()
        assert out["statement"] == "pl"
        assert out["year"] == 2025 and out["month"] == 7
        # Annual consolidation column is YTD-through-anchor-month (see
        # build_pl_annual_consolidation docstring), not the last full FY.
        assert out["col_label"] == "YTDJul25A"
        assert out["entities"] == [
            {"code": "AT", "label": "Austria GmbH"},
            {"code": "DE", "label": "Germany GmbH"},
        ]

    def test_missing_fy_for_entity_is_zero(self):
        """An entity with no grain rows gets 0 across every line."""
        # Drop DE grains; DE column must be all zeros.
        at_only = [g for g in _CONSOL_YTD_GRAIN if g["entity_prefix"] != "DE"]
        rows, _ = self._build(grains=at_only)
        assert rows["pl-NET_SALES"]["entity_amounts"]["DE"] == 0
        assert rows["pl-TOTAL_OUTPUT"]["entity_amounts"]["DE"] == 0
        assert rows["pl-GROSS_PROFIT"]["entity_amounts"]["DE"] == 0
        # AT unaffected.
        assert rows["pl-GROSS_PROFIT"]["entity_amounts"]["AT"] == 80

    def test_build_pl_annual_consolidation_glue(self):
        """End-to-end through build_pl_annual_consolidation with a mock session."""
        from app.services.fin_compat_pl import build_pl_annual_consolidation

        def _execute(stmt, params=None):
            sql = str(stmt)
            result = MagicMock()
            if any(t in sql for t in ("dim_pl_structure", "dim_bs_structure", "dim_cf_structure")):
                rows = [_dict_row(r) for r in _WE_STRUCTURE]
            elif "dim_legal_entity" in sql:
                rows = _CONSL_ENTITY_ROWS
            else:
                rows = [_dict_row(r) for r in _CONSOL_YTD_GRAIN]
            result.fetchall.return_value = rows
            result.fetchone.return_value = rows[0] if rows else None
            return result

        session = MagicMock()
        session.execute.side_effect = _execute

        out = build_pl_annual_consolidation(session, year=2025, month=7)
        assert out["col_label"] == "YTDJul25A"
        rows = {r["id"]: r for r in out["rows"]}
        assert rows["pl-TOTAL_OUTPUT"]["entity_amounts"] == {"AT": 120, "DE": 70}
        assert rows["pl-GROSS_PROFIT"]["aggregated"] == 125
        assert all("BS_" not in rid for rid in rows)


# ---------------------------------------------------------------------------
# (g) KPI line_code resolution (pass-through, no KPI_SOURCES indirection)
# ---------------------------------------------------------------------------

class TestResolveKpiLineCode:
    """kpi_code values are real structure line_codes → identity resolution."""

    def test_pass_through_known_codes(self):
        from app.services.fin_compat_pl import _resolve_kpi_line_code
        for code in ("GROSS_PROFIT", "EBITDA", "EBIT", "NET_PROFIT"):
            assert _resolve_kpi_line_code(code, "NET_PROFIT") == code

    def test_blank_or_none_uses_fallback(self):
        from app.services.fin_compat_pl import _resolve_kpi_line_code
        assert _resolve_kpi_line_code("", "NET_PROFIT") == "NET_PROFIT"
        assert _resolve_kpi_line_code("   ", "NET_PROFIT") == "NET_PROFIT"
        assert _resolve_kpi_line_code(None, "NET_PROFIT") == "NET_PROFIT"


# ---------------------------------------------------------------------------
# Shared KPI structure helper + grain-with-arbitrary-keys helper
# ---------------------------------------------------------------------------

def _struct_kpi(pl_line_id, sort_order, line_code, balance_title, kpi_code):
    return {
        "pl_line_id": pl_line_id, "sort_order": sort_order, "line_code": line_code,
        "row_type": "kpi", "balance_title": balance_title, "details": None,
        "calc_type": None, "level_2": None, "level_3": None, "level_4": None,
        "gl_account_id": None, "invert_delta": False, "is_bold": False,
        "kpi_code": kpi_code,
    }


def _grain_keys(level_2, level_3, ang, vals):
    """A grain dict carrying arbitrary value columns (e.g. month / FY / week keys)."""
    g = {
        "level_2": level_2, "level_3": level_3, "level_4": None,
        "gl_account_id": None, "account_number_group": ang, "account_name": level_3,
    }
    g.update(vals)
    return g


def _mock_struct_grain_session(structure, grains, entity_rows=None):
    """Mock Session: dim_pl_structure → structure, dim_legal_entity → entity_rows,
    everything else (any grain SQL, incl. posting_date-based) → grains."""
    session = MagicMock()

    def _execute(stmt, params=None):
        sql = str(stmt)
        result = MagicMock()
        if any(t in sql for t in ("dim_pl_structure", "dim_bs_structure", "dim_cf_structure")):
            rows = [_dict_row(r) for r in structure]
        elif "dim_legal_entity" in sql:
            rows = entity_rows or []
        else:
            rows = [_dict_row(r) for r in grains]
        result.fetchall.return_value = rows
        result.fetchone.return_value = rows[0] if rows else None
        return result

    session.execute.side_effect = _execute
    return session


# ---------------------------------------------------------------------------
# (h) Annual consolidation KPI rows (% of total output)
# ---------------------------------------------------------------------------
# Reuses _CONSOL_YTD_GRAIN / _CONSL_ENTITY_ROWS:
#   AT: TOTAL_OUTPUT 120, GROSS_PROFIT 80 → 80/120*100  = 66.67
#   DE: TOTAL_OUTPUT  70, GROSS_PROFIT 45 → 45/70 *100  = 64.29
#   group: Σ GROSS_PROFIT 125 / Σ TOTAL_OUTPUT 190 *100 = 65.79

class TestPlAnnualConsolidationKpi:

    def _build(self):
        from app.services.fin_compat_pl import _build_annual_consolidation_rows
        struct = list(_WE_STRUCTURE) + [
            _struct_kpi(50, 21, "GROSS_MARGIN_PCT", "Gross margin %", "GROSS_PROFIT"),
        ]
        out = _build_annual_consolidation_rows(
            struct, _CONSOL_YTD_GRAIN, _CONSL_ENTITY_ROWS, 2025, 7,
        )
        return {r["id"]: r for r in out["rows"]}, out

    def test_kpi_header_present(self):
        rows, _ = self._build()
        assert "pl-kpi-header" in rows
        assert rows["pl-kpi-header"]["row_kind"] == "kpi_header"
        assert rows["pl-kpi-header"]["label"] == "KPIs — as % of total output"

    def test_gross_margin_per_entity_and_group(self):
        """Gross margin% = GROSS_PROFIT / TOTAL_OUTPUT * 100 per entity + aggregated."""
        rows, _ = self._build()
        gm = rows["pl-GROSS_MARGIN_PCT"]
        assert gm["row_kind"] == "kpi"
        assert gm["entity_amounts"] == {"AT": 66.67, "DE": 64.29}
        assert gm["aggregated"] == 65.79
        assert gm["consolidation"] == 65.79
        assert gm["ic_eliminations"] == 0.0

    def test_value_rows_unaffected(self):
        rows, _ = self._build()
        assert rows["pl-TOTAL_OUTPUT"]["entity_amounts"] == {"AT": 120, "DE": 70}
        assert rows["pl-GROSS_PROFIT"]["entity_amounts"] == {"AT": 80, "DE": 45}


# ---------------------------------------------------------------------------
# (i) Monthly extended view (span='fy3'): FY-span periods + summary totals
# ---------------------------------------------------------------------------

class TestFySpanPeriods:

    def test_31_periods_2025_07(self):
        from app.services.fin_compat_sql import _fy_span_periods
        periods = _fy_span_periods(2025, 7)
        assert len(periods) == 31
        assert periods[0] == (2023, 1)
        assert periods[-1] == (2025, 7)
        # FY2023 = 12 months, FY2024 = 12 months, YTD2025 = 7 months
        assert sum(1 for y, _ in periods if y == 2023) == 12
        assert sum(1 for y, _ in periods if y == 2024) == 12
        assert sum(1 for y, _ in periods if y == 2025) == 7

    def test_totals_keys_and_labels(self):
        from app.services.fin_compat_sql import _fy_span_totals
        totals = _fy_span_totals(2025, 7)
        assert [t["key"] for t in totals] == ["FY2023", "FY2024", "YTD2025"]
        assert [t["label"] for t in totals] == ["FY23A", "FY24A", "YTD25A"]
        assert [t["kind"] for t in totals] == ["fy", "fy", "ytd"]


class TestMonthlyExtended:
    """build_pl_monthly(span='fy3'): running-sum subtotals cover the total keys.

    Worked example (anchor 2025/07):
        NET_SALES          FY2024=110  YTD2025=70  2025-07=10
        FINISHED_GOODS_WIP FY2024= 22  YTD2025=14  2025-07= 2
        OWN_WORK           (none)      → 0
      → TOTAL_OUTPUT       FY2024=132  YTD2025=84  2025-07=12
        COST_OF_MATERIALS  FY2024=-66  YTD2025=-42 2025-07=-6
      → GROSS_PROFIT       FY2024= 66  YTD2025=42  2025-07= 6
    """

    _GRAINS = [
        _grain_keys("Income", "Net sales", "AT4000",
                    {"FY2023": 100, "FY2024": 110, "YTD2025": 70, "2025-07": 10}),
        _grain_keys("Income", "Finished goods WIP", "AT4100",
                    {"FY2023": 20, "FY2024": 22, "YTD2025": 14, "2025-07": 2}),
        _grain_keys("Expense", "Cost of materials", "AT5000",
                    {"FY2023": -60, "FY2024": -66, "YTD2025": -42, "2025-07": -6}),
        # Balance-sheet movement must never leak into a P&L subtotal.
        _grain_keys("Assets", "Cash", "AT2700", {"FY2024": 999_999.0}),
    ]

    def _build(self, span):
        from app.services.fin_compat_pl import build_pl_monthly
        session = _make_mock_session(structure=_WE_STRUCTURE, grain_rows=self._GRAINS)
        return build_pl_monthly(
            session, period_grain="month", year=2025, month=7, span=span,
        )

    def test_default_12m_has_no_totals(self):
        out = self._build("12m")
        assert "totals" not in out
        assert len(out["periods"]) == 12

    def test_fy3_periods_and_totals(self):
        out = self._build("fy3")
        assert len(out["periods"]) == 31
        assert "totals" in out
        assert [t["key"] for t in out["totals"]] == ["FY2023", "FY2024", "YTD2025"]
        assert [t["label"] for t in out["totals"]] == ["FY23A", "FY24A", "YTD25A"]

    def test_fy3_running_sum_over_total_keys(self):
        out = self._build("fy3")
        rows = {r["id"]: r for r in out["rows"]}
        to = rows["pl-TOTAL_OUTPUT"]["amounts"]
        gp = rows["pl-GROSS_PROFIT"]["amounts"]
        assert to["FY2024"] == 132
        assert to["YTD2025"] == 84
        assert to["2025-07"] == 12
        assert gp["FY2024"] == 66
        assert gp["YTD2025"] == 42
        assert gp["2025-07"] == 6

    def test_fy3_bs_excluded(self):
        out = self._build("fy3")
        rows = {r["id"]: r for r in out["rows"]}
        assert "pl-BS_CASH" not in rows
        # The 999,999 BS cash movement must not inflate the FY2024 subtotal.
        assert rows["pl-TOTAL_OUTPUT"]["amounts"]["FY2024"] == 132


# ---------------------------------------------------------------------------
# (j) Weekly-breakdown view (M-2/M-1 full + M0 partial, by ISO week)
# ---------------------------------------------------------------------------

class TestWeeklyBreakdownLayout:

    def test_three_groups_kinds_and_totals(self):
        from app.services.fin_compat_sql import weekly_breakdown_layout
        layout = weekly_breakdown_layout(2025, 28)
        assert len(layout) == 3
        assert [g["kind"] for g in layout] == ["full", "full", "partial"]
        assert layout[0]["total"]["kind"] == "month"
        assert layout[1]["total"]["kind"] == "month"
        assert layout[2]["total"]["kind"] == "mtd"
        assert layout[2]["total"]["label"].startswith("MTD ")

    def test_full_month_weeks_anchored_by_thursday(self):
        from datetime import timedelta
        from app.services.fin_compat_sql import weekly_breakdown_layout
        layout = weekly_breakdown_layout(2025, 28)
        for g in layout[:2]:
            gy, gm = int(g["month_key"][:4]), int(g["month_key"][5:7])
            assert g["weeks"], "full month should have at least one week"
            for w in g["weeks"]:
                # ISO-8601: a week belongs to the month of its Thursday
                # (= Sunday − 3 days), not necessarily its Sunday.
                thu = w["sunday"] - timedelta(days=3)
                assert (thu.year, thu.month) == (gy, gm)

    def test_partial_month_weeks_up_to_anchor(self):
        from app.services.fin_compat_sql import (
            iso_week_thursday, weekly_breakdown_layout,
        )
        from datetime import timedelta
        anchor_thu = iso_week_thursday(2025, 28)
        layout = weekly_breakdown_layout(2025, 28)
        m0 = layout[2]
        for w in m0["weeks"]:
            assert (w["sunday"] - timedelta(days=3)) <= anchor_thu
        # The last partial-month week is the anchor week itself.
        assert m0["weeks"][-1]["sunday"] == anchor_thu + timedelta(days=3)

    def test_cw31_2025_anchor_month_is_july(self):
        """CW31/2025 straddles the month boundary (Mon 2025-07-28 .. Sun
        2025-08-03). Its Thursday (2025-07-31) is in July, so M0 must be July —
        NOT August (which has no data) — and July must hold CW27..CW31."""
        from app.services.fin_compat_sql import (
            plan_anchor_for_week, weekly_breakdown_layout, iso_week_thursday,
        )
        from datetime import date

        assert iso_week_thursday(2025, 31) == date(2025, 7, 31)
        assert plan_anchor_for_week(2025, 31) == (2025, 7)

        layout = weekly_breakdown_layout(2025, 31)
        m2, m1, m0 = layout
        assert m0["month_key"] == "2025-07" and m0["kind"] == "partial"
        assert m1["month_key"] == "2025-06"
        assert m2["month_key"] == "2025-05"
        # July ISO weeks (by Thursday) are CW27..CW31 → 5 weeks up to the anchor.
        m0_weeks = [w["iso_week"] for w in m0["weeks"]]
        assert m0_weeks == [27, 28, 29, 30, 31]
        # MTD total range ends on the anchor Sunday (2025-08-03) so it covers
        # the whole of July despite the anchor week spilling into August.
        assert m0["total"]["kind"] == "mtd"
        assert m0["total"]["mtd_end"] == date(2025, 8, 3)


class TestWeeklyBreakdownBuild:
    """build_pl_weekly_breakdown: running-sum over week + total columns, KPI %,
    BS exclusion, and the documented response envelope."""

    def _layout(self):
        from app.services.fin_compat_sql import weekly_breakdown_layout
        return weekly_breakdown_layout(2025, 28)

    def _build(self, structure=None):
        from app.services.fin_compat_pl import build_pl_weekly_breakdown
        layout = self._layout()
        m0 = layout[2]
        wk_key = m0["weeks"][-1]["key"]   # anchor-week column key
        mtd_key = m0["total"]["key"]      # MTD total column key
        grains = [
            _grain_keys("Income", "Net sales", "AT4000",
                        {wk_key: 100, mtd_key: 100}),
            _grain_keys("Income", "Finished goods WIP", "AT4100",
                        {wk_key: 20, mtd_key: 20}),
            _grain_keys("Expense", "Cost of materials", "AT5000",
                        {wk_key: -40, mtd_key: -40}),
            # Balance-sheet movement must never leak into a P&L subtotal.
            _grain_keys("Assets", "Cash", "AT2700", {wk_key: 999_999.0}),
        ]
        session = _mock_struct_grain_session(structure or _WE_STRUCTURE, grains)
        out = build_pl_weekly_breakdown(session, iso_year=2025, iso_week=28)
        return out, wk_key, mtd_key

    def test_envelope_and_groups_shape(self):
        out, _, _ = self._build()
        assert out["statement"] == "pl"
        assert out["iso_year"] == 2025 and out["iso_week"] == 28
        assert [g["kind"] for g in out["groups"]] == ["full", "full", "partial"]
        for g in out["groups"]:
            assert set(g.keys()) == {"month_key", "month_label", "kind", "weeks", "total"}
            for w in g["weeks"]:
                assert set(w.keys()) == {"key", "label"}
            assert set(g["total"].keys()) == {"key", "label", "kind"}

    def test_running_sum_week_column(self):
        out, wk_key, _ = self._build()
        rows = {r["id"]: r for r in out["rows"]}
        assert rows["pl-NET_SALES"]["amounts"][wk_key] == 100
        assert rows["pl-TOTAL_OUTPUT"]["amounts"][wk_key] == 120
        assert rows["pl-GROSS_PROFIT"]["amounts"][wk_key] == 80

    def test_running_sum_mtd_total_column(self):
        out, _, mtd_key = self._build()
        rows = {r["id"]: r for r in out["rows"]}
        assert rows["pl-TOTAL_OUTPUT"]["amounts"][mtd_key] == 120
        assert rows["pl-GROSS_PROFIT"]["amounts"][mtd_key] == 80

    def test_bs_excluded(self):
        out, wk_key, _ = self._build()
        rows = {r["id"]: r for r in out["rows"]}
        assert "pl-BS_CASH" not in rows
        assert rows["pl-TOTAL_OUTPUT"]["amounts"][wk_key] == 120

    def test_kpi_rows_percent_of_total_output(self):
        """With a seeded GROSS_MARGIN_PCT kpi row: 80/120*100 = 66.67 per column."""
        structure = list(_WE_STRUCTURE) + [
            _struct_kpi(50, 21, "GROSS_MARGIN_PCT", "Gross margin %", "GROSS_PROFIT"),
        ]
        out, wk_key, mtd_key = self._build(structure=structure)
        rows = {r["id"]: r for r in out["rows"]}
        assert "pl-kpi-header" in rows
        gm = rows["pl-GROSS_MARGIN_PCT"]
        assert gm["row_kind"] == "kpi"
        assert gm["amounts"][wk_key] == 66.67
        assert gm["amounts"][mtd_key] == 66.67


# ---------------------------------------------------------------------------
# Overview — annual period_grain must be accepted (regression for 422)
# ---------------------------------------------------------------------------

class TestOverviewYearGrain:

    def test_overview_accepts_year_grain(self):
        """period_grain=year must not fail Query validation (was ^(month|week)$ only)."""
        client, _ = _make_client()
        with patch(
            "app.routers.financials_compat.build_overview_response",
            return_value={"statement": "overview", "period_grain": "year"},
        ):
            resp = client.get(
                "/api/v1/financials/overview",
                params={"period_grain": "year", "year": 2025, "month": 7},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["period_grain"] == "year"

    def test_overview_highlights_accepts_year_grain(self):
        client, _ = _make_client()
        with patch(
            "app.routers.financials_compat.build_overview_highlights",
            return_value={"highlights": []},
        ):
            resp = client.get(
                "/api/v1/financials/overview/highlights",
                params={"period_grain": "year", "year": 2025, "month": 7},
            )
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# (n) Annual consolidation — L4 detail children under L3-only mapping rows
#
# Feature: the annual (group) consolidation P&L now drills an L3-only mapping row
# into per-level_4 detail children, mirroring the monthly/BS view.  The financial
# INVARIANT that must hold: per entity column and in the aggregate,
#     Σ children == parent
# guaranteed by partitioning the SAME matched grain set into level_4 buckets plus
# a residual "(no L4)" bucket for grains that match the L3 row but carry no L4.
# ---------------------------------------------------------------------------

def _cons_struct(line_code, sort_order, row_type, *,
                 level_2=None, level_3=None, level_4=None, is_bold=False):
    return {
        "pl_line_id": sort_order, "sort_order": sort_order, "line_code": line_code,
        "row_type": row_type, "balance_title": line_code.replace("_", " ").title(),
        "details": None, "calc_type": None,
        "level_2": level_2, "level_3": level_3, "level_4": level_4,
        "gl_account_id": None, "invert_delta": False, "is_bold": is_bold,
        "kpi_code": None,
    }


def _cons_grain(entity_prefix, level_2, level_3, level_4, ytd):
    return {
        "level_2": level_2, "level_3": level_3, "level_4": level_4,
        "gl_account_id": None, "account_number_group": None,
        "account_name": level_4 or level_3, "entity_prefix": entity_prefix,
        "py_cm": 0.0, "pm": 0.0, "cm": ytd, "ytd": ytd, "ytd_py": 0.0,
    }


class TestAnnualConsolidationL4Children:

    # One L3-only mapping row (Personnel) that should fan out into L4 children,
    # across two entities.  AT has a residual (no-L4) grain; DE has one too.
    _STRUCT = [
        _cons_struct("PERSONNEL", 1, "mapping", level_2="Expense", level_3="Personnel"),
        _cons_struct("NET_PROFIT", 2, "subtotal", is_bold=True),
    ]
    _ENT = [("AT", "AT", "Austria GmbH"), ("DE", "DE", "Germany GmbH")]
    _GRAIN = [
        _cons_grain("AT", "Expense", "Personnel", "Wages", -100.0),
        _cons_grain("AT", "Expense", "Personnel", "Salaries", -50.0),
        _cons_grain("AT", "Expense", "Personnel", None, -10.0),   # residual (no L4)
        _cons_grain("DE", "Expense", "Personnel", "Wages", -200.0),
        _cons_grain("DE", "Expense", "Personnel", None, -5.0),    # residual (no L4)
    ]

    def _build(self):
        from app.services.fin_compat_pl import _build_annual_consolidation_rows
        out = _build_annual_consolidation_rows(
            self._STRUCT, self._GRAIN, self._ENT, year=2025, month=12,
        )
        parent = next(r for r in out["rows"] if r["id"] == "pl-PERSONNEL")
        return out, parent

    def test_l3_only_mapping_row_gets_children(self):
        _out, parent = self._build()
        assert parent["has_children"] is True
        assert len(parent["children"]) >= 1
        # Labels: the two real L4 groups + the residual bucket.
        labels = {c["label"] for c in parent["children"]}
        assert {"Wages", "Salaries", "(no L4)"} <= labels

    def test_children_reconcile_to_parent_per_entity(self):
        _out, parent = self._build()
        for ec in ("AT", "DE"):
            child_sum = round(sum(c["entity_amounts"][ec] for c in parent["children"]), 2)
            assert child_sum == parent["entity_amounts"][ec], (
                f"entity {ec}: Σ children {child_sum} != parent {parent['entity_amounts'][ec]}"
            )
        # Expected column totals: AT = -160, DE = -205.
        assert parent["entity_amounts"]["AT"] == -160.0
        assert parent["entity_amounts"]["DE"] == -205.0

    def test_children_reconcile_to_parent_aggregate(self):
        _out, parent = self._build()
        child_agg = round(sum(c["aggregated"] for c in parent["children"]), 2)
        assert child_agg == parent["aggregated"] == -365.0

    def test_residual_bucket_omitted_when_all_l4_present(self):
        """No residual '(no L4)' child when every matching grain has a level_4."""
        from app.services.fin_compat_pl import _build_annual_consolidation_rows
        grain = [g for g in self._GRAIN if g["level_4"] is not None]
        out = _build_annual_consolidation_rows(
            self._STRUCT, grain, self._ENT, year=2025, month=12,
        )
        parent = next(r for r in out["rows"] if r["id"] == "pl-PERSONNEL")
        assert "(no L4)" not in {c["label"] for c in parent["children"]}
        for ec in ("AT", "DE"):
            child_sum = round(sum(c["entity_amounts"][ec] for c in parent["children"]), 2)
            assert child_sum == parent["entity_amounts"][ec]
