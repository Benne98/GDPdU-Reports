"""POST /api/v1/ingest/upload — delimited-text (.txt / .csv) delimiter sniffing.

Covers (DB-free; staging only):
  * ';'-separated .txt  -> 5 distinct columns (NOT one giant column), dialect.delimiter == ';'
  * comma .csv          -> comma-split columns preserved (regression)
  * .txt is an accepted extension (no 415)
  * tab / pipe delimited .txt sniffed correctly
  * German ';' + ',' decimals -> dialect.decimal == ','
  * staged .txt is re-readable by the shared loader with the same columns
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import app.routers.ingest as ing
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


def _upload(client: TestClient, name: str, content: bytes, ctype: str = "text/plain"):
    return client.post(
        "/api/v1/ingest/upload",
        files={"file": (name, content, ctype)},
    )


_SEMI_TXT = (
    "JE;Date;Account;AccName;Amount\n"
    "1;01.03.2024;8400;Sales;100,00\n"
    "2;02.03.2024;1200;Bank;-100,00\n"
).encode("utf-8")


class TestTxtDelimiterSniffing:
    def test_semicolon_txt_splits_into_distinct_columns(self):
        client = _client_as(_ADMIN)
        resp = _upload(client, "gl.txt", _SEMI_TXT)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["columns"] == ["JE", "Date", "Account", "AccName", "Amount"]
        assert body["dialect"]["delimiter"] == ";"
        # German ';' delimiter implies ',' decimals downstream.
        assert body["dialect"]["decimal"] == ","
        # sample rows are split, not a single giant column
        first = body["sample"][0]
        assert first["JE"] == "1"
        assert first["Account"] == "8400"
        assert first["Amount"] == "100,00"

    def test_comma_csv_regression(self):
        client = _client_as(_ADMIN)
        content = (
            "JE,Date,Account,AccName,Amount\n"
            "1,01.03.2024,8400,Sales,100.00\n"
        ).encode("utf-8")
        resp = _upload(client, "gl.csv", content, ctype="text/csv")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["columns"] == ["JE", "Date", "Account", "AccName", "Amount"]
        assert body["dialect"]["delimiter"] == ","
        assert body["dialect"]["decimal"] == "."

    def test_txt_extension_accepted(self):
        client = _client_as(_ADMIN)
        resp = _upload(client, "gl.txt", _SEMI_TXT)
        assert resp.status_code != 415, resp.text
        assert resp.status_code == 200

    def test_tab_delimited_txt(self):
        client = _client_as(_ADMIN)
        content = (
            "JE\tDate\tAccount\n"
            "1\t01.03.2024\t8400\n"
        ).encode("utf-8")
        resp = _upload(client, "gl.txt", content)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["columns"] == ["JE", "Date", "Account"]
        assert body["dialect"]["delimiter"] == "\t"

    def test_pipe_delimited_txt(self):
        client = _client_as(_ADMIN)
        content = (
            "JE|Date|Account\n"
            "1|01.03.2024|8400\n"
        ).encode("utf-8")
        resp = _upload(client, "gl.txt", content)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["columns"] == ["JE", "Date", "Account"]
        assert body["dialect"]["delimiter"] == "|"

    def test_staged_txt_reloadable_by_shared_loader(self):
        client = _client_as(_ADMIN)
        body = _upload(client, "gl.txt", _SEMI_TXT).json()
        df = ing._load_staged_frame(body["file_id"], None)
        assert list(df.columns) == ["JE", "Date", "Account", "AccName", "Amount"]
        assert len(df) == 2


class TestSniffUnit:
    def test_sniff_semicolon(self):
        assert ing._sniff_delimiter("a;b;c\n1;2;3\n") == ";"

    def test_sniff_comma(self):
        assert ing._sniff_delimiter("a,b,c\n1,2,3\n") == ","

    def test_sniff_tab(self):
        assert ing._sniff_delimiter("a\tb\tc\n1\t2\t3\n") == "\t"

    def test_sniff_pipe(self):
        assert ing._sniff_delimiter("a|b|c\n1|2|3\n") == "|"

    def test_sniff_single_column_defaults_comma(self):
        # one column, no delimiter present -> safe comma default
        assert ing._sniff_delimiter("Header\nvalue1\nvalue2\n") == ","

    def test_semicolon_with_comma_decimals_not_confused(self):
        # ';' delimiter + ',' decimals: ';' must win even though commas appear
        text = "Account;Amount\n8400;1.234,56\n1200;-100,00\n"
        assert ing._sniff_delimiter(text) == ";"
