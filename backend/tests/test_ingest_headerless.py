"""Headerless GL upload: detection, synthetic headers, suggest, apply-headers.

Covers (DB-free; staging only — MagicMock session like test_ingest_combine):
  * column_suggest pure helpers: detect_has_header / synthetic_headers /
    suggest_column_names
  * POST /upload auto-detects a headerless ';' .txt -> header_detected:false,
    synthetic "Column 1..N" columns, re-staged so the loader round-trips them
  * two same-layout headerless files combine (no "Column mismatch") and the
    combine response carries sensible suggested_headers
  * POST /suggest-headers + POST /apply-headers contract (incl. 422s)
  * regression: a normal HEADERED csv keeps its real headers (has_header true)
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.auth import User, current_user, require_admin
from app.column_suggest import (
    detect_has_header,
    looks_like_data_row,
    suggest_column_names,
    synthetic_headers,
)
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


# Eight-column headerless ';'-separated GL rows (date, code, amount, text,
# vat, source-type, source-no, doc-number) — mirrors the real client export.
_HL_2024 = (
    "01.01.2022;4219405;-15.895,29;Rechnung;-2.535,12;Debitor;BU9038316-10;36000\n"
    "15.03.2022;8400;1.190,00;Erlöse;190,00;Debitor;BU9038316-11;36001\n"
    "20.06.2022;1576;95,50;Vorsteuer;0,00;Kreditor;KR1002-3;36002\n"
)
_HL_2025 = (
    "02.02.2023;4219405;-2.000,00;Storno;-319,33;Debitor;BU9038316-20;47000\n"
    "11.07.2023;8400;500,00;Erlöse;79,83;Debitor;BU9038316-21;47001\n"
    "30.09.2023;1576;42,17;Vorsteuer;0,00;Kreditor;KR1002-9;47002\n"
)

_HEADERED_CSV = (
    "Transaction number,Posting date,Account number,Booking text,Amount\n"
    "1,01.03.2024,8400,Sales,100.00\n"
    "2,02.03.2024,8400,Sales,200.00\n"
)


def _upload_bytes(client: TestClient, data: bytes, name: str, *, has_header=None) -> dict:
    files = {"file": (name, data, "text/plain")}
    form = {} if has_header is None else {"has_header": str(has_header).lower()}
    resp = client.post("/api/v1/ingest/upload", files=files, data=form)
    assert resp.status_code == 200, resp.text
    return resp.json()


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
class TestHelpers:
    def test_synthetic_headers(self):
        assert synthetic_headers(3) == ["Column 1", "Column 2", "Column 3"]
        assert synthetic_headers(0) == []

    def test_looks_like_data_row(self):
        assert looks_like_data_row(["01.01.2022", "4219405", "-15.895,29", "Rechnung"])
        assert not looks_like_data_row(["Date", "Account", "Amount", "Text"])

    def test_detect_headerless_from_numeric_names(self):
        # pandas would have promoted the data row to numeric column names.
        cols = ["01.01.2022", "4219405", "-15.895,29", "Rechnung", "Unnamed: 4"]
        body = [["15.03.2022", "8400", "1.190,00", "Erlöse", "190,00"]]
        assert detect_has_header(cols, body, column_names=cols) is False

    def test_detect_real_header(self):
        cols = ["Transaction number", "Posting date", "Account number", "Amount"]
        body = [["1", "01.03.2024", "8400", "100.00"]]
        assert detect_has_header(cols, body, column_names=cols) is True

    def test_detect_real_german_gobd_export_headerless(self):
        # Regression: a real 22-column GoBD export with no header row. The first
        # data row was promoted to column names (note the German decimals, two
        # dates, Unnamed:N for empty cells, and the ".1" pandas dedup suffix).
        cols = [
            "351546", "36000", "11.01.2022", "Rechnung", "BC9007725",
            "Volkswagen Leasing GmbH", "Unnamed: 6", "-1.928,99", "AFHS\\MKA",
            "Ja", "0,00", " ", "Sachkonto", "97807", "0,00.1", "1.928,99",
            "01.01.2022", "10172790365", "Kreditor", "368903",
            "Unnamed: 20", "Unnamed: 21",
        ]
        assert detect_has_header(cols, None, column_names=cols) is False

    def test_suggest_aligns_to_gobd_labels(self):
        columns = synthetic_headers(7)
        rows = [
            {"Column 1": "01.01.2022", "Column 2": "4219405", "Column 3": "-15.895,29",
             "Column 4": "Rechnung lang text", "Column 5": "-2.535,12",
             "Column 6": "Debitor", "Column 7": "BU9038316-10"},
            {"Column 1": "15.03.2022", "Column 2": "8400", "Column 3": "1.190,00",
             "Column 4": "Erlöse buchung", "Column 5": "190,00",
             "Column 6": "Debitor", "Column 7": "BU9038316-11"},
        ]
        out = suggest_column_names(rows, columns)
        assert out["Column 1"] == "Posting date"
        assert out["Column 2"] == "Account number"
        assert out["Column 3"] == "Amount"          # largest-magnitude signed decimal
        assert out["Column 5"] == "VAT amount"       # smaller signed decimal
        assert out["Column 6"] == "Source type"      # Debitor/Kreditor
        assert out["Column 7"] == "Source No."       # BU.. partner token

    def test_suggest_keeps_synthetic_when_unknown(self):
        columns = synthetic_headers(1)
        rows = [{"Column 1": ""}, {"Column 1": ""}]
        assert suggest_column_names(rows, columns)["Column 1"] == "Column 1"


# --------------------------------------------------------------------------- #
# Upload detection + re-staging
# --------------------------------------------------------------------------- #
class TestUploadHeaderless:
    def test_headerless_txt_autodetected(self):
        client = _client_as(_ADMIN)
        body = _upload_bytes(client, _HL_2024.encode("utf-8"), "gl2024.txt")
        assert body["header_detected"] is False
        assert body["has_header"] is False
        assert body["columns"] == [f"Column {i}" for i in range(1, 9)]
        # re-staged: every sample row keys on the synthetic headers
        assert set(body["sample"][0].keys()) == set(body["columns"])

    def test_two_headerless_files_combine_no_mismatch(self):
        client = _client_as(_ADMIN)
        a = _upload_bytes(client, _HL_2024.encode("utf-8"), "gl2024.txt")["file_id"]
        b = _upload_bytes(client, _HL_2025.encode("utf-8"), "gl2025.txt")["file_id"]
        resp = client.post("/api/v1/ingest/gl/combine", json={
            "inputs": [
                {"file_id": a, "fiscal_year": 2024},
                {"file_id": b, "fiscal_year": 2025},
            ],
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["columns"] == [f"Column {i}" for i in range(1, 9)] + ["fiscal_year"]
        assert body["row_count"] == 6
        sug = body["suggested_headers"]
        assert sug["Column 1"] == "Posting date"
        assert sug["Column 2"] == "Account number"
        assert sug["Column 3"] == "Amount"

    def test_explicit_has_header_true_overrides_autodetect(self):
        client = _client_as(_ADMIN)
        # force header interpretation: first data row becomes the header
        body = _upload_bytes(client, _HL_2024.encode("utf-8"), "gl.txt", has_header=True)
        assert body["has_header"] is True
        assert body["columns"] != [f"Column {i}" for i in range(1, 9)]


# --------------------------------------------------------------------------- #
# /suggest-headers + /apply-headers
# --------------------------------------------------------------------------- #
class TestSuggestAndApply:
    def _combined(self, client: TestClient) -> dict:
        a = _upload_bytes(client, _HL_2024.encode("utf-8"), "gl2024.txt")["file_id"]
        b = _upload_bytes(client, _HL_2025.encode("utf-8"), "gl2025.txt")["file_id"]
        return client.post("/api/v1/ingest/gl/combine", json={
            "inputs": [
                {"file_id": a, "fiscal_year": 2024},
                {"file_id": b, "fiscal_year": 2025},
            ],
        }).json()

    def test_suggest_headers_endpoint(self):
        client = _client_as(_ADMIN)
        combined = self._combined(client)
        resp = client.post("/api/v1/ingest/suggest-headers",
                           json={"file_id": combined["file_id"]})
        assert resp.status_code == 200, resp.text
        sug = resp.json()["suggested_headers"]
        assert sug["Column 1"] == "Posting date"
        assert sug["fiscal_year"] == "fiscal_year"  # already named -> kept

    def test_apply_headers_roundtrip(self):
        import app.routers.ingest as ing

        client = _client_as(_ADMIN)
        combined = self._combined(client)
        headers = ["Posting date", "Account number", "Amount", "Booking text",
                   "VAT amount", "Source type", "Source No.", "Document number",
                   "fiscal_year"]
        resp = client.post("/api/v1/ingest/apply-headers", json={
            "file_id": combined["file_id"], "headers": headers,
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["columns"] == headers
        assert body["row_count"] == 6
        # downstream contract: re-readable by the shared loader with the new names
        df = ing._load_staged_frame(body["file_id"], None)
        assert list(df.columns) == headers
        assert sorted(df["fiscal_year"].unique().tolist()) == ["2024", "2025"]

    def test_apply_headers_wrong_count_422(self):
        client = _client_as(_ADMIN)
        combined = self._combined(client)
        resp = client.post("/api/v1/ingest/apply-headers", json={
            "file_id": combined["file_id"], "headers": ["only", "three", "names"],
        })
        assert resp.status_code == 422, resp.text

    def test_apply_headers_duplicates_422(self):
        client = _client_as(_ADMIN)
        combined = self._combined(client)
        headers = ["Amount", "Amount", "c3", "c4", "c5", "c6", "c7", "c8", "fiscal_year"]
        resp = client.post("/api/v1/ingest/apply-headers", json={
            "file_id": combined["file_id"], "headers": headers,
        })
        assert resp.status_code == 422, resp.text
        assert "unique" in resp.json()["detail"].lower()

    def test_requires_auth(self):
        client = TestClient(app, raise_server_exceptions=False)
        for path in ("/api/v1/ingest/suggest-headers", "/api/v1/ingest/apply-headers"):
            resp = client.post(path, json={"file_id": "x", "headers": ["a"]})
            assert resp.status_code == 401, path


# --------------------------------------------------------------------------- #
# Regression: a normal headered csv is unchanged
# --------------------------------------------------------------------------- #
class TestHeaderedRegression:
    def test_headered_csv_keeps_real_headers(self):
        client = _client_as(_ADMIN)
        body = _upload_bytes(client, _HEADERED_CSV.encode("utf-8"), "headered.csv")
        assert body["header_detected"] is True
        assert body["has_header"] is True
        assert body["columns"] == [
            "Transaction number", "Posting date", "Account number",
            "Booking text", "Amount",
        ]
