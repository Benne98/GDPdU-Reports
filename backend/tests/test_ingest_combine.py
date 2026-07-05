"""POST /api/v1/ingest/gl/combine — combine staged per-year GL files (one entity).

Covers (DB-free; staging only):
  * auth gate (current_user)
  * column add + row stack + /upload-compatible response shape
  * fiscal_year column is authoritative (overwrites a pre-existing one; reported)
  * column-order differences are reconciled (not a mismatch)
  * mismatched column SET -> 422 with the diff message
  * combined staged file is re-readable by the shared loader (downstream contract)
"""
from __future__ import annotations

import io
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.auth import User, current_user, require_admin
from app.db import get_session
from app.main import app

_ADMIN = User(user_id=1, email="admin@test", display_name="Admin", is_admin=True)


def _client_as(user: User) -> TestClient:
    app.dependency_overrides[get_session] = lambda: MagicMock()
    app.dependency_overrides[current_user] = lambda: user
    if user.is_admin:
        app.dependency_overrides[require_admin] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _csv_bytes(rows: list[dict]) -> bytes:
    cols = list(rows[0].keys())
    lines = [",".join(cols)]
    for r in rows:
        lines.append(",".join(str(r[c]) for c in cols))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _upload(client: TestClient, rows: list[dict], name: str = "gl.csv") -> str:
    resp = client.post(
        "/api/v1/ingest/upload",
        files={"file": (name, _csv_bytes(rows), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["file_id"]


_ROWS_2024 = [{"JE": "1", "Date": "01.03.2024", "Account": "8400",
               "AccName": "Sales", "Amount": "100.00"}]
_ROWS_2025 = [{"JE": "2", "Date": "01.03.2025", "Account": "8400",
               "AccName": "Sales", "Amount": "200.00"}]


class TestCombine:
    def test_combine_adds_fiscal_year_and_stacks(self):
        client = _client_as(_ADMIN)
        a = _upload(client, _ROWS_2024)
        b = _upload(client, _ROWS_2025)
        resp = client.post("/api/v1/ingest/gl/combine", json={
            "inputs": [
                {"file_id": a, "fiscal_year": 2024},
                {"file_id": b, "fiscal_year": 2025},
            ],
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # /upload-compatible shape
        assert body["file_id"] and body["columns"] and isinstance(body["sample"], list)
        assert "fiscal_year" in body["columns"]
        assert body["row_count"] == 2
        fys = {str(r["fiscal_year"]) for r in body["sample"]}
        assert fys == {"2024", "2025"}
        assert body["overwritten_fiscal_year_files"] == []

    def test_column_order_difference_reconciled(self):
        client = _client_as(_ADMIN)
        a = _upload(client, [{"JE": "1", "Date": "01.03.2024", "Account": "8400",
                              "AccName": "Sales", "Amount": "100.00"}])
        # same SET of columns, different ORDER -> must reconcile, not 422
        b = _upload(client, [{"Amount": "200.00", "AccName": "Sales", "Account": "8400",
                              "Date": "01.03.2025", "JE": "2"}])
        resp = client.post("/api/v1/ingest/gl/combine", json={
            "inputs": [
                {"file_id": a, "fiscal_year": 2024},
                {"file_id": b, "fiscal_year": 2025},
            ],
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["row_count"] == 2
        # first file's order preserved, fiscal_year appended last
        assert body["columns"] == ["JE", "Date", "Account", "AccName", "Amount", "fiscal_year"]

    def test_existing_fiscal_year_column_overwritten(self):
        client = _client_as(_ADMIN)
        # source file already has a 'fiscal_year' column with the WRONG value
        a = _upload(client, [{"JE": "1", "Date": "01.03.2024", "Account": "8400",
                              "AccName": "Sales", "Amount": "100.00", "fiscal_year": "1999"}])
        resp = client.post("/api/v1/ingest/gl/combine", json={
            "inputs": [{"file_id": a, "fiscal_year": 2024}],
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["overwritten_fiscal_year_files"] == [a]
        assert {str(r["fiscal_year"]) for r in body["sample"]} == {"2024"}

    def test_mismatched_columns_422(self):
        client = _client_as(_ADMIN)
        a = _upload(client, _ROWS_2024)
        # different column SET (Account -> Konto)
        b = _upload(client, [{"JE": "2", "Date": "01.03.2025", "Konto": "8400",
                              "AccName": "Sales", "Amount": "200.00"}])
        resp = client.post("/api/v1/ingest/gl/combine", json={
            "inputs": [
                {"file_id": a, "fiscal_year": 2024},
                {"file_id": b, "fiscal_year": 2025},
            ],
        })
        assert resp.status_code == 422, resp.text
        assert "Column mismatch" in resp.json()["detail"]

    def test_combined_file_is_reloadable_by_shared_loader(self):
        """Downstream contract: the new file_id must be readable like any staged file."""
        import app.routers.ingest as ing

        client = _client_as(_ADMIN)
        a = _upload(client, _ROWS_2024)
        b = _upload(client, _ROWS_2025)
        body = client.post("/api/v1/ingest/gl/combine", json={
            "inputs": [
                {"file_id": a, "fiscal_year": 2024},
                {"file_id": b, "fiscal_year": 2025},
            ],
        }).json()
        df = ing._load_staged_frame(body["file_id"], None)
        assert list(df.columns) == ["JE", "Date", "Account", "AccName", "Amount", "fiscal_year"]
        assert len(df) == 2
        assert sorted(df["fiscal_year"].tolist()) == ["2024", "2025"]

    def test_no_column_warning_for_normal_file(self):
        """A small, well-formed layout must NOT raise the misparse heads-up."""
        client = _client_as(_ADMIN)
        a = _upload(client, _ROWS_2024)
        b = _upload(client, _ROWS_2025)
        body = client.post("/api/v1/ingest/gl/combine", json={
            "inputs": [
                {"file_id": a, "fiscal_year": 2024},
                {"file_id": b, "fiscal_year": 2025},
            ],
        }).json()
        assert body["column_warning"] is None

    def test_column_warning_on_inflated_sparse_columns(self):
        """A wide file with many near-empty columns (wrong-delimiter signature) warns."""
        client = _client_as(_ADMIN)
        # 20 columns, only 8 populated; 12 are entirely empty fragment columns.
        def _wide_row(i: int) -> dict:
            row = {
                "JE": str(i), "Date": "01.03.2024", "Account": "8400",
                "AccName": "Sales", "Amount": "100.00", "Cost": "10.00",
                "Tax": "19.00", "Ref": f"R{i}",
            }
            for k in range(1, 13):  # 12 empty columns
                row[f"Empty{k}"] = ""
            return row
        rows = [_wide_row(i) for i in range(12)]  # >10 rows so populated cols clear the floor
        a = _upload(client, rows, name="wide.csv")
        body = client.post("/api/v1/ingest/gl/combine", json={
            "inputs": [{"file_id": a, "fiscal_year": 2024}],
        }).json()
        assert body["column_warning"] is not None
        assert "delimiter" in body["column_warning"]

    def test_requires_auth(self):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/v1/ingest/gl/combine", json={
            "inputs": [{"file_id": "x", "fiscal_year": 2024}],
        })
        assert resp.status_code == 401

    def test_empty_inputs_422(self):
        client = _client_as(_ADMIN)
        resp = client.post("/api/v1/ingest/gl/combine", json={"inputs": []})
        assert resp.status_code == 422
