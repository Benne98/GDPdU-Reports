"""Tests for POST /api/v1/ingest/dataset/rows — server-paginated dataset viewer.

Covers (synthetic fixtures only — no real client data, no DB connection):
  1.  Pagination: offset/limit slices; total stable across pages; row-count == limit
      or remainder.
  2.  String filter contains + equals: correct filtered_total and row contents.
  3.  Numeric filter between/gte/lte: correct filtered rows.
  4.  Sort asc/desc on numeric and string column: ordering correct.
  5.  Mapped-columns-only: ignored source column absent; __entity__ + fiscal_year
      present; unmapped optional canonical field absent.
  6.  total vs filtered_total: diverge under a filter; equal with no filter.
  7.  Multi-member concat: two members produce rows tagged with their entity;
      total == sum of member rows.
  8.  Entity-visibility: non-admin user lacking a member entity grant -> 403.
  9.  Cache reuse: two identical requests return identical payloads.

Strategy:
- FastAPI TestClient with auth + DB deps overridden (same pattern as other ingest tests).
- UPLOAD_DIR is monkeypatched to tmp_path so no files leak to the real upload area.
- _CONCAT_CACHE is cleared before/after each test (autouse fixture).
- Synthetic CSV fixtures: JEGN, Date, Account, Amount, Note, IgnoredCol.
  Only line_note is mapped as an optional field; IgnoredCol and vat_amount are NOT
  mapped, so they must never appear in the response.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.auth import User, current_user
from app.db import get_session
from app.main import app

# ---------------------------------------------------------------------------
# Shared users
# ---------------------------------------------------------------------------
_ADMIN = User(user_id=1, email="admin@test", display_name="Admin", is_admin=True)
_NON_ADMIN = User(user_id=2, email="user@test", display_name="Non-Admin", is_admin=False)


# ---------------------------------------------------------------------------
# Minimal SQLAlchemy-Row stand-in
# ---------------------------------------------------------------------------
class _Row:
    """Lightweight Row proxy — supports index-access and iteration."""

    def __init__(self, *vals):
        self._vals = vals

    def __getitem__(self, i):
        return self._vals[i]

    def __iter__(self):
        return iter(self._vals)


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------
def _make_session(visibility_codes: list[str] | None = None) -> MagicMock:
    """Mock DB session for dataset_rows tests.

    Handles two SQL patterns:
      * dim_legal_entity  → build_entity_lookup (returns empty; fixed entity prefix
                            is numeric so the lookup is not needed).
      * admin_role_entity_visibility → visible_entity_codes for non-admin users.

    For admin users, visible_entity_codes returns None immediately without any DB
    query, so only the dim_legal_entity branch matters for admin paths.
    """
    session = MagicMock()

    def _execute(stmt, params=None):
        sql = str(stmt)
        result = MagicMock()
        if "dim_legal_entity" in sql:
            result.fetchall.return_value = []
        elif "admin_role_entity_visibility" in sql:
            result.fetchall.return_value = [
                _Row(c) for c in (visibility_codes or [])
            ]
        else:
            result.fetchall.return_value = []
        result.fetchone.return_value = None
        return result

    session.execute.side_effect = _execute
    return session


# ---------------------------------------------------------------------------
# TestClient factory
# ---------------------------------------------------------------------------
def _client_as(user: User, session: MagicMock) -> TestClient:
    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# CSV staging helper
# ---------------------------------------------------------------------------
def _stage_csv(directory: Path, file_id: str, rows: list[dict]) -> str:
    """Write *rows* as a UTF-8 CSV to directory/<file_id>_gl.csv.

    The naming convention ``{file_id}_<anything>`` is what _file_path_from_id
    uses to resolve a file_id to a physical path.
    """
    cols = list(rows[0].keys())
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    w.writerows(rows)
    (directory / f"{file_id}_gl.csv").write_text(buf.getvalue(), encoding="utf-8")
    return file_id


# ---------------------------------------------------------------------------
# Canonical profile (shared across tests)
# Maps:
#   line_note  -> "Note"       (an optional canonical field that IS mapped → shown)
#   NOT mapping vat_amount     (optional canonical field NOT mapped → NOT shown)
#   IgnoredCol is a raw source column with no canonical mapping → NOT shown
# ---------------------------------------------------------------------------
_PROFILE = {
    "entity": {"mode": "fixed", "value": "01"},  # overridden per member at runtime
    "fiscal_year": {"mode": "fixed", "value": 2024},
    "sign": {"mode": "signed", "amount": "Amount"},
    "decimal": ".",
    "thousands": ",",
    "date_dayfirst": True,
    "columns": {
        "posting_date": "Date",
        "journal_entry_number": "JEGN",
        "account_number": "Account",
        "line_note": "Note",  # mapped optional — MUST appear in response
        # vat_amount: deliberately NOT mapped — MUST NOT appear in response
    },
    "linking_strategy": "txn",
    "entry_type": "actual",
    "source_system": "test",
}

# ---------------------------------------------------------------------------
# Synthetic entity-01 rows (5 rows for pagination + filter + sort tests)
# Amount values: [100.0, -50.0, 200.0, -75.0, 30.0]
# Note values:   ["Alpha widget", "Beta item", "Alpha gadget", "Gamma part", "Alpha chip"]
# ---------------------------------------------------------------------------
_ROWS_01 = [
    {"JEGN": "J001", "Date": "01.01.2024", "Account": "1200", "Amount": "100.00",
     "Note": "Alpha widget", "IgnoredCol": "IGNORE_ME"},
    {"JEGN": "J001", "Date": "02.01.2024", "Account": "1300", "Amount": "-50.00",
     "Note": "Beta item",   "IgnoredCol": "IGNORE_ME"},
    {"JEGN": "J002", "Date": "03.01.2024", "Account": "1200", "Amount": "200.00",
     "Note": "Alpha gadget","IgnoredCol": "IGNORE_ME"},
    {"JEGN": "J002", "Date": "04.01.2024", "Account": "1400", "Amount": "-75.00",
     "Note": "Gamma part",  "IgnoredCol": "IGNORE_ME"},
    {"JEGN": "J003", "Date": "05.01.2024", "Account": "1200", "Amount": "30.00",
     "Note": "Alpha chip",  "IgnoredCol": "IGNORE_ME"},
]

# Synthetic entity-02 rows (3 rows for multi-member test)
_ROWS_02 = [
    {"JEGN": "J101", "Date": "01.01.2024", "Account": "2100", "Amount": "500.00",
     "Note": "Delta note",   "IgnoredCol": "IGNORE_ME"},
    {"JEGN": "J102", "Date": "02.01.2024", "Account": "2200", "Amount": "-150.00",
     "Note": "Epsilon note", "IgnoredCol": "IGNORE_ME"},
    {"JEGN": "J103", "Date": "03.01.2024", "Account": "2100", "Amount": "250.00",
     "Note": "Zeta note",    "IgnoredCol": "IGNORE_ME"},
]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def upload_dir(tmp_path, monkeypatch):
    """Patch UPLOAD_DIR in the ingest router to an isolated tmp directory."""
    import app.routers.ingest as _mod
    monkeypatch.setattr(_mod, "UPLOAD_DIR", tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_state():
    """Clear dependency overrides and the concat-cache before and after each test."""
    import app.routers.ingest as _mod
    _mod._CONCAT_CACHE.clear()
    yield
    app.dependency_overrides.clear()
    _mod._CONCAT_CACHE.clear()


# ---------------------------------------------------------------------------
# Convenience: build the standard POST body for a single entity-01 member
# ---------------------------------------------------------------------------
def _body_01(file_id: str, *, offset: int = 0, limit: int = 100,
             sort_by: str | None = None, sort_dir: str = "asc",
             filters: list[dict] | None = None) -> dict:
    return {
        "members": [{"file_id": file_id, "entity": "01", "sheet": None}],
        "profile": _PROFILE,
        "offset": offset,
        "limit": limit,
        "sort_by": sort_by,
        "sort_dir": sort_dir,
        "filters": filters or [],
    }


# ===========================================================================
# Test 1 — Pagination
# ===========================================================================
class TestPagination:
    def test_first_page_returns_limit_rows(self, upload_dir):
        fid = _stage_csv(upload_dir, "pag01", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid, limit=2, offset=0))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 5
        assert body["filtered_total"] == 5
        assert len(body["rows"]) == 2
        assert body["offset"] == 0
        assert body["limit"] == 2

    def test_second_page_returns_limit_rows(self, upload_dir):
        fid = _stage_csv(upload_dir, "pag02", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid, limit=2, offset=2))
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 5
        assert len(body["rows"]) == 2
        assert body["offset"] == 2

    def test_last_page_returns_remainder_rows(self, upload_dir):
        fid = _stage_csv(upload_dir, "pag03", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid, limit=2, offset=4))
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 5
        assert len(body["rows"]) == 1  # only 1 row left

    def test_offset_beyond_total_returns_empty(self, upload_dir):
        fid = _stage_csv(upload_dir, "pag04", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid, limit=10, offset=50))
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 5
        assert body["rows"] == []

    def test_total_stable_across_pages(self, upload_dir):
        """total must not change between page requests for the same query."""
        fid = _stage_csv(upload_dir, "pag05", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        totals = []
        for offset in (0, 2, 4):
            r = client.post("/api/v1/ingest/dataset/rows",
                            json=_body_01(fid, limit=2, offset=offset))
            assert r.status_code == 200
            totals.append(r.json()["total"])
        assert totals == [5, 5, 5]

    def test_limit_gt_1000_rejected(self, upload_dir):
        """Pydantic le=1000 rejects limit=1001 with 422."""
        fid = _stage_csv(upload_dir, "pag06", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid, limit=1001))
        assert resp.status_code == 422

    def test_limit_0_rejected(self, upload_dir):
        """Pydantic ge=1 rejects limit=0 with 422."""
        fid = _stage_csv(upload_dir, "pag07", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid, limit=0))
        assert resp.status_code == 422


# ===========================================================================
# Test 2 — String filters (contains, equals)
# ===========================================================================
class TestStringFilter:
    def test_contains_alpha_matches_three_rows(self, upload_dir):
        fid = _stage_csv(upload_dir, "sf01", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        body = _body_01(fid, filters=[{"field": "line_note", "op": "contains",
                                        "value": "Alpha", "value2": None}])
        resp = client.post("/api/v1/ingest/dataset/rows", json=body)
        assert resp.status_code == 200, resp.text
        r = resp.json()
        assert r["total"] == 5
        assert r["filtered_total"] == 3
        notes = [row["line_note"] for row in r["rows"]]
        assert all("Alpha" in n for n in notes)

    def test_contains_case_insensitive(self, upload_dir):
        fid = _stage_csv(upload_dir, "sf02", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        body = _body_01(fid, filters=[{"field": "line_note", "op": "contains",
                                        "value": "alpha", "value2": None}])
        resp = client.post("/api/v1/ingest/dataset/rows", json=body)
        assert resp.status_code == 200
        assert resp.json()["filtered_total"] == 3

    def test_equals_returns_exact_match(self, upload_dir):
        fid = _stage_csv(upload_dir, "sf03", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        body = _body_01(fid, filters=[{"field": "line_note", "op": "equals",
                                        "value": "Beta item", "value2": None}])
        resp = client.post("/api/v1/ingest/dataset/rows", json=body)
        assert resp.status_code == 200, resp.text
        r = resp.json()
        assert r["filtered_total"] == 1
        assert r["rows"][0]["line_note"] == "Beta item"

    def test_equals_no_match_returns_zero(self, upload_dir):
        fid = _stage_csv(upload_dir, "sf04", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        body = _body_01(fid, filters=[{"field": "line_note", "op": "equals",
                                        "value": "NO_SUCH_VALUE", "value2": None}])
        resp = client.post("/api/v1/ingest/dataset/rows", json=body)
        assert resp.status_code == 200
        assert resp.json()["filtered_total"] == 0

    def test_unknown_filter_field_returns_400(self, upload_dir):
        fid = _stage_csv(upload_dir, "sf05", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        body = _body_01(fid, filters=[{"field": "NONEXISTENT_COL", "op": "contains",
                                        "value": "x", "value2": None}])
        resp = client.post("/api/v1/ingest/dataset/rows", json=body)
        assert resp.status_code == 400


# ===========================================================================
# Test 3 — Numeric filters (gte, lte, between)
# ===========================================================================
class TestNumericFilter:
    # Amounts: [100, -50, 200, -75, 30]

    def test_gte_50_matches_two_rows(self, upload_dir):
        # amounts >= 50 → 100, 200
        fid = _stage_csv(upload_dir, "nf01", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        body = _body_01(fid, filters=[{"field": "amount", "op": "gte",
                                        "value": 50.0, "value2": None}])
        resp = client.post("/api/v1/ingest/dataset/rows", json=body)
        assert resp.status_code == 200, resp.text
        r = resp.json()
        assert r["filtered_total"] == 2
        amounts = [row["amount"] for row in r["rows"]]
        assert all(a >= 50.0 for a in amounts)

    def test_lte_minus60_matches_one_row(self, upload_dir):
        # amounts <= -60 → only -75
        fid = _stage_csv(upload_dir, "nf02", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        body = _body_01(fid, filters=[{"field": "amount", "op": "lte",
                                        "value": -60.0, "value2": None}])
        resp = client.post("/api/v1/ingest/dataset/rows", json=body)
        assert resp.status_code == 200, resp.text
        r = resp.json()
        assert r["filtered_total"] == 1
        assert r["rows"][0]["amount"] == pytest.approx(-75.0)

    def test_between_minus100_and_50_matches_three_rows(self, upload_dir):
        # amounts in [-100, 50] → -50, -75, 30
        fid = _stage_csv(upload_dir, "nf03", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        body = _body_01(fid, filters=[{"field": "amount", "op": "between",
                                        "value": -100.0, "value2": 50.0}])
        resp = client.post("/api/v1/ingest/dataset/rows", json=body)
        assert resp.status_code == 200, resp.text
        r = resp.json()
        assert r["filtered_total"] == 3
        amounts = [row["amount"] for row in r["rows"]]
        assert all(-100.0 <= a <= 50.0 for a in amounts)

    def test_numeric_op_with_null_value_returns_400(self, upload_dir):
        """gte/lte/between with value=None → 400 (guard in _dataset_apply_filters)."""
        fid = _stage_csv(upload_dir, "nf04", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        body = _body_01(fid, filters=[{"field": "amount", "op": "gte",
                                        "value": None, "value2": None}])
        resp = client.post("/api/v1/ingest/dataset/rows", json=body)
        assert resp.status_code == 400


# ===========================================================================
# Test 4 — Sort
# ===========================================================================
class TestSort:
    # Amounts: [100, -50, 200, -75, 30]
    # Sorted asc: [-75, -50, 30, 100, 200]
    # Sorted desc: [200, 100, 30, -50, -75]

    def test_sort_amount_asc(self, upload_dir):
        fid = _stage_csv(upload_dir, "so01", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid, sort_by="amount", sort_dir="asc"))
        assert resp.status_code == 200, resp.text
        amounts = [row["amount"] for row in resp.json()["rows"]]
        assert amounts == sorted(amounts)
        assert amounts[0] == pytest.approx(-75.0)

    def test_sort_amount_desc(self, upload_dir):
        fid = _stage_csv(upload_dir, "so02", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid, sort_by="amount", sort_dir="desc"))
        assert resp.status_code == 200, resp.text
        amounts = [row["amount"] for row in resp.json()["rows"]]
        assert amounts == sorted(amounts, reverse=True)
        assert amounts[0] == pytest.approx(200.0)

    def test_sort_line_note_asc(self, upload_dir):
        # Notes: Alpha widget, Beta item, Alpha gadget, Gamma part, Alpha chip
        # Sorted asc: Alpha chip, Alpha gadget, Alpha widget, Beta item, Gamma part
        fid = _stage_csv(upload_dir, "so03", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid, sort_by="line_note", sort_dir="asc"))
        assert resp.status_code == 200, resp.text
        notes = [row["line_note"] for row in resp.json()["rows"]]
        assert notes == sorted(notes, key=lambda s: (s or ""))

    def test_sort_line_note_desc(self, upload_dir):
        fid = _stage_csv(upload_dir, "so04", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid, sort_by="line_note", sort_dir="desc"))
        assert resp.status_code == 200, resp.text
        notes = [row["line_note"] for row in resp.json()["rows"]]
        assert notes == sorted(notes, key=lambda s: (s or ""), reverse=True)

    def test_unknown_sort_by_returns_400(self, upload_dir):
        fid = _stage_csv(upload_dir, "so05", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid, sort_by="NONEXISTENT_COLUMN"))
        assert resp.status_code == 400


# ===========================================================================
# Test 5 — Mapped-columns-only / ignored field exclusion
# ===========================================================================
class TestMappedColumnsOnly:
    def test_ignored_source_column_absent_from_response_columns(self, upload_dir):
        """IgnoredCol is a raw source column with no canonical mapping — absent."""
        fid = _stage_csv(upload_dir, "mc01", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid))
        assert resp.status_code == 200, resp.text
        col_keys = {c["key"] for c in resp.json()["columns"]}
        assert "IgnoredCol" not in col_keys

    def test_ignored_source_column_absent_from_row_dicts(self, upload_dir):
        fid = _stage_csv(upload_dir, "mc02", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid))
        assert resp.status_code == 200
        for row in resp.json()["rows"]:
            assert "IgnoredCol" not in row

    def test_unmapped_optional_canonical_field_absent(self, upload_dir):
        """vat_amount is a canonical optional field that is NOT mapped in the profile."""
        fid = _stage_csv(upload_dir, "mc03", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid))
        assert resp.status_code == 200
        col_keys = {c["key"] for c in resp.json()["columns"]}
        assert "vat_amount" not in col_keys
        for row in resp.json()["rows"]:
            assert "vat_amount" not in row

    def test_entity_column_present_as_first_column(self, upload_dir):
        fid = _stage_csv(upload_dir, "mc04", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid))
        assert resp.status_code == 200
        cols = resp.json()["columns"]
        assert cols[0]["key"] == "__entity__"
        assert cols[0]["type"] == "string"

    def test_fiscal_year_column_present(self, upload_dir):
        fid = _stage_csv(upload_dir, "mc05", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid))
        assert resp.status_code == 200
        col_keys = {c["key"] for c in resp.json()["columns"]}
        assert "fiscal_year" in col_keys

    def test_mapped_optional_line_note_present(self, upload_dir):
        """line_note IS mapped via profile.columns['line_note'] — must appear."""
        fid = _stage_csv(upload_dir, "mc06", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid))
        assert resp.status_code == 200
        col_keys = {c["key"] for c in resp.json()["columns"]}
        assert "line_note" in col_keys
        for row in resp.json()["rows"]:
            assert "line_note" in row

    def test_all_required_fields_present(self, upload_dir):
        """Required canonical fields must always appear regardless of profile."""
        required = {
            "journal_entry_group_number", "fiscal_period", "line_number",
            "booking_line_id", "account_number_group", "gl_account_id",
            "amount", "posting_date",
        }
        fid = _stage_csv(upload_dir, "mc07", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid))
        assert resp.status_code == 200
        col_keys = {c["key"] for c in resp.json()["columns"]}
        missing = required - col_keys
        assert not missing, f"Required fields missing from columns: {missing}"

    def test_entity_and_fiscal_year_in_every_row(self, upload_dir):
        fid = _stage_csv(upload_dir, "mc08", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid))
        assert resp.status_code == 200
        for row in resp.json()["rows"]:
            assert "__entity__" in row
            assert "fiscal_year" in row
            assert row["__entity__"] == "01"
            assert row["fiscal_year"] == 2024


# ===========================================================================
# Test 6 — total vs filtered_total
# ===========================================================================
class TestTotalVsFilteredTotal:
    def test_no_filter_total_equals_filtered_total(self, upload_dir):
        fid = _stage_csv(upload_dir, "tv01", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid))
        assert resp.status_code == 200
        r = resp.json()
        assert r["total"] == r["filtered_total"]

    def test_filter_diverges_total_and_filtered_total(self, upload_dir):
        # contains "Alpha" matches 3 of 5 rows
        fid = _stage_csv(upload_dir, "tv02", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        body = _body_01(fid, filters=[{"field": "line_note", "op": "contains",
                                        "value": "Alpha", "value2": None}])
        resp = client.post("/api/v1/ingest/dataset/rows", json=body)
        assert resp.status_code == 200
        r = resp.json()
        assert r["total"] == 5
        assert r["filtered_total"] == 3
        assert r["total"] != r["filtered_total"]


# ===========================================================================
# Test 7 — Multi-member concat
# ===========================================================================
class TestMultiMemberConcat:
    def test_two_members_produce_combined_total(self, upload_dir):
        fid1 = _stage_csv(upload_dir, "mm01a", _ROWS_01)
        fid2 = _stage_csv(upload_dir, "mm01b", _ROWS_02)
        client = _client_as(_ADMIN, _make_session())
        body = {
            "members": [
                {"file_id": fid1, "entity": "01", "sheet": None},
                {"file_id": fid2, "entity": "02", "sheet": None},
            ],
            "profile": _PROFILE,
            "offset": 0,
            "limit": 100,
            "sort_by": None,
            "sort_dir": "asc",
            "filters": [],
        }
        resp = client.post("/api/v1/ingest/dataset/rows", json=body)
        assert resp.status_code == 200, resp.text
        r = resp.json()
        # total == 5 entity-01 rows + 3 entity-02 rows
        assert r["total"] == 8
        assert len(r["rows"]) == 8

    def test_rows_tagged_with_correct_entity(self, upload_dir):
        fid1 = _stage_csv(upload_dir, "mm02a", _ROWS_01)
        fid2 = _stage_csv(upload_dir, "mm02b", _ROWS_02)
        client = _client_as(_ADMIN, _make_session())
        body = {
            "members": [
                {"file_id": fid1, "entity": "01", "sheet": None},
                {"file_id": fid2, "entity": "02", "sheet": None},
            ],
            "profile": _PROFILE,
            "offset": 0,
            "limit": 100,
            "sort_by": None,
            "sort_dir": "asc",
            "filters": [],
        }
        resp = client.post("/api/v1/ingest/dataset/rows", json=body)
        assert resp.status_code == 200, resp.text
        rows = resp.json()["rows"]
        entities = {row["__entity__"] for row in rows}
        assert entities == {"01", "02"}
        assert sum(1 for r in rows if r["__entity__"] == "01") == 5
        assert sum(1 for r in rows if r["__entity__"] == "02") == 3

    def test_members_required_min_1(self, upload_dir):
        """members list must have at least 1 member (Pydantic min_length=1)."""
        client = _client_as(_ADMIN, _make_session())
        body = {
            "members": [],
            "profile": _PROFILE,
            "offset": 0, "limit": 10,
            "sort_by": None, "sort_dir": "asc",
            "filters": [],
        }
        resp = client.post("/api/v1/ingest/dataset/rows", json=body)
        assert resp.status_code == 422


# ===========================================================================
# Test 8 — Entity visibility (authz)
# ===========================================================================
class TestEntityVisibility:
    def test_admin_can_access_any_entity(self, upload_dir):
        fid = _stage_csv(upload_dir, "ev01", _ROWS_01)
        # Admin → visible_entity_codes returns None (unrestricted, no DB query needed)
        client = _client_as(_ADMIN, _make_session())
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid))
        assert resp.status_code == 200

    def test_non_admin_with_grant_can_access_entity(self, upload_dir):
        fid = _stage_csv(upload_dir, "ev02", _ROWS_01)
        # Non-admin granted ["01"] → entity "01" is allowed
        session = _make_session(visibility_codes=["01"])
        client = _client_as(_NON_ADMIN, session)
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid))
        assert resp.status_code == 200

    def test_non_admin_without_grant_gets_403(self, upload_dir):
        fid = _stage_csv(upload_dir, "ev03", _ROWS_02)
        # Non-admin granted ["01"] but requests entity "02" → 403
        session = _make_session(visibility_codes=["01"])
        client = _client_as(_NON_ADMIN, session)
        body = {
            "members": [{"file_id": fid, "entity": "02", "sheet": None}],
            "profile": _PROFILE,
            "offset": 0, "limit": 10,
            "sort_by": None, "sort_dir": "asc",
            "filters": [],
        }
        resp = client.post("/api/v1/ingest/dataset/rows", json=body)
        assert resp.status_code == 403

    def test_non_admin_with_no_grants_gets_403_for_any_entity(self, upload_dir):
        """Fail-closed: non-admin with ZERO grants denied everything."""
        fid = _stage_csv(upload_dir, "ev04", _ROWS_01)
        session = _make_session(visibility_codes=[])  # empty grants
        client = _client_as(_NON_ADMIN, session)
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid))
        assert resp.status_code == 403

    def test_non_admin_mixed_members_denied_if_any_forbidden(self, upload_dir):
        """If ANY member entity is not in the grant set the whole request is 403."""
        fid1 = _stage_csv(upload_dir, "ev05a", _ROWS_01)
        fid2 = _stage_csv(upload_dir, "ev05b", _ROWS_02)
        # Granted only "01"; requesting "01" + "02" → must be 403
        session = _make_session(visibility_codes=["01"])
        client = _client_as(_NON_ADMIN, session)
        body = {
            "members": [
                {"file_id": fid1, "entity": "01", "sheet": None},
                {"file_id": fid2, "entity": "02", "sheet": None},
            ],
            "profile": _PROFILE,
            "offset": 0, "limit": 10,
            "sort_by": None, "sort_dir": "asc",
            "filters": [],
        }
        resp = client.post("/api/v1/ingest/dataset/rows", json=body)
        assert resp.status_code == 403


# ===========================================================================
# Test 9 — Cache reuse
# ===========================================================================
class TestCacheReuse:
    def test_identical_requests_return_identical_payloads(self, upload_dir):
        """Second call to the same member+profile is served from _CONCAT_CACHE."""
        import app.routers.ingest as _mod

        fid = _stage_csv(upload_dir, "cr01", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        body = _body_01(fid, limit=3, offset=0)

        resp1 = client.post("/api/v1/ingest/dataset/rows", json=body)
        assert resp1.status_code == 200, resp1.text
        data1 = resp1.json()

        # Cache should now have one entry
        assert len(_mod._CONCAT_CACHE) == 1

        resp2 = client.post("/api/v1/ingest/dataset/rows", json=body)
        assert resp2.status_code == 200
        data2 = resp2.json()

        assert data1["columns"] == data2["columns"]
        assert data1["rows"] == data2["rows"]
        assert data1["total"] == data2["total"]
        assert data1["filtered_total"] == data2["filtered_total"]

    def test_different_offsets_hit_same_cache_entry(self, upload_dir):
        """Pagination requests (different offset) share one cache entry (same members+profile)."""
        import app.routers.ingest as _mod

        fid = _stage_csv(upload_dir, "cr02", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())

        r1 = client.post("/api/v1/ingest/dataset/rows",
                         json=_body_01(fid, limit=2, offset=0))
        assert r1.status_code == 200
        cache_size_after_first = len(_mod._CONCAT_CACHE)

        r2 = client.post("/api/v1/ingest/dataset/rows",
                         json=_body_01(fid, limit=2, offset=2))
        assert r2.status_code == 200
        # A second page request (same members+profile, different offset) must NOT
        # add a new cache entry.
        assert len(_mod._CONCAT_CACHE) == cache_size_after_first

        # Both pages draw from the same total
        assert r1.json()["total"] == r2.json()["total"] == 5


# ===========================================================================
# Test 10 — Column metadata types
# ===========================================================================
class TestColumnTypes:
    def test_amount_column_has_type_number(self, upload_dir):
        fid = _stage_csv(upload_dir, "ct01", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid))
        assert resp.status_code == 200
        cols = {c["key"]: c for c in resp.json()["columns"]}
        assert cols["amount"]["type"] == "number"

    def test_posting_date_column_has_type_date(self, upload_dir):
        fid = _stage_csv(upload_dir, "ct02", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid))
        assert resp.status_code == 200
        cols = {c["key"]: c for c in resp.json()["columns"]}
        assert cols["posting_date"]["type"] == "date"

    def test_entity_column_has_type_string(self, upload_dir):
        fid = _stage_csv(upload_dir, "ct03", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid))
        assert resp.status_code == 200
        cols = {c["key"]: c for c in resp.json()["columns"]}
        assert cols["__entity__"]["type"] == "string"

    def test_line_note_column_has_type_string(self, upload_dir):
        fid = _stage_csv(upload_dir, "ct04", _ROWS_01)
        client = _client_as(_ADMIN, _make_session())
        resp = client.post("/api/v1/ingest/dataset/rows",
                           json=_body_01(fid))
        assert resp.status_code == 200
        cols = {c["key"]: c for c in resp.json()["columns"]}
        assert cols["line_note"]["type"] == "string"
