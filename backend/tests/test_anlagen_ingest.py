"""D3 DRAFT — Anlagenregister ingest tests (SYNTHETIC, isolated).

Covers the upload -> preview -> commit roundtrip for ``fact_fixed_asset`` against a
throwaway in-memory SQLite DB (no Postgres, no real client data). Asserts:
  * pass-through rows are stored with the injected entity_prefix + fy_label,
  * combined mode resolves the per-row entity_prefix from a source column,
  * NO computed roll-forward column (closing cost / accumulated depreciation)
    exists on the table — this is DRAFT scaffolding only.
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import User, current_user, require_admin
from app.db import get_session
from app.main import app

_ADMIN = User(user_id=1, email="admin@test", display_name="Admin", is_admin=True)
# A non-admin caller -> visible_entity_codes resolves to a concrete (here empty,
# fail-closed) set, i.e. a RESTRICTED scope (not the admin "see-all" None).
_RESTRICTED = User(user_id=2, email="user@test", display_name="User", is_admin=False)

# SQLite-equivalent of migration 0022's fact_fixed_asset (no derived columns).
_DDL = """
CREATE TABLE fact_fixed_asset (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id          TEXT NOT NULL DEFAULT 'default',
    dataset_version_id  INTEGER,
    source_file_id      TEXT,
    row_no              INTEGER,
    created_at          TEXT,
    entity_prefix       TEXT,
    fy_label            TEXT,
    asset_id            TEXT,
    asset_sub_no        TEXT,
    asset_class         TEXT,
    segment             TEXT,
    bilanzposition      TEXT,
    capitalization_date TEXT,
    opening_cost_ahk    NUMERIC,
    additions_zugang    NUMERIC,
    disposals_abgang    NUMERIC,
    transfers_umbuchung NUMERIC,
    depreciation        NUMERIC,
    nbv                 NUMERIC
);
"""


@pytest.fixture()
def sqlite_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    with engine.begin() as conn:
        conn.execute(text(_DDL))
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    sess = Session()
    try:
        yield sess
    finally:
        sess.close()
        engine.dispose()


@pytest.fixture()
def client(sqlite_session):
    app.dependency_overrides[get_session] = lambda: sqlite_session
    app.dependency_overrides[current_user] = lambda: _ADMIN
    app.dependency_overrides[require_admin] = lambda: _ADMIN
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.fixture()
def restricted_client(sqlite_session):
    """Same DB, but the caller is a RESTRICTED (non-admin) user."""
    app.dependency_overrides[get_session] = lambda: sqlite_session
    app.dependency_overrides[current_user] = lambda: _RESTRICTED
    app.dependency_overrides[require_admin] = lambda: _RESTRICTED
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _xlsx_bytes(rows: list[dict]) -> bytes:
    wb = Workbook()
    ws = wb.active
    cols = list(rows[0].keys())
    ws.append(cols)
    for r in rows:
        ws.append([r[c] for c in cols])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_REGISTER_ROWS = [
    {
        "Buchungskreis": "1", "AnlNr": "A-100", "UnterNr": "0",
        "AnlKlasse": "Maschinen", "Segment": "Prod", "Bilanzposition": "Tech. Anlagen",
        "AktivDatum": "15.03.2021",
        "AHK_Anfang": "10.000,00", "Zugang": "2.500,00", "Abgang": "0,00",
        "Umbuchung": "0,00", "Abschreibung": "1.250,00", "Restbuchwert": "11.250,00",
    },
    {
        "Buchungskreis": "1", "AnlNr": "A-101", "UnterNr": "1",
        "AnlKlasse": "Fuhrpark", "Segment": "Logistik", "Bilanzposition": "Fuhrpark",
        "AktivDatum": "01.07.2022",
        "AHK_Anfang": "30.000,00", "Zugang": "0,00", "Abgang": "5.000,00",
        "Umbuchung": "0,00", "Abschreibung": "6.000,00", "Restbuchwert": "19.000,00",
    },
]

_COLUMN_MAP = {
    "asset_id": "AnlNr",
    "asset_sub_no": "UnterNr",
    "asset_class": "AnlKlasse",
    "segment": "Segment",
    "bilanzposition": "Bilanzposition",
    "capitalization_date": "AktivDatum",
    "opening_cost_ahk": "AHK_Anfang",
    "additions_zugang": "Zugang",
    "disposals_abgang": "Abgang",
    "transfers_umbuchung": "Umbuchung",
    "depreciation": "Abschreibung",
    "nbv": "Restbuchwert",
}


def _upload(client) -> str:
    resp = client.post(
        "/api/v1/anlagen/upload",
        files={"file": ("assets.xlsx", _xlsx_bytes(_REGISTER_ROWS),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "AHK_Anfang" in body["columns"]
    assert len(body["sample"]) == 2
    return body["file_id"]


def test_upload_preview_commit_roundtrip(client, sqlite_session):
    file_id = _upload(client)

    pv = client.post("/api/v1/anlagen/preview", json={"file_id": file_id})
    assert pv.status_code == 200, pv.text
    assert "Zugang" in pv.json()["columns"]

    resp = client.post(
        "/api/v1/anlagen/commit",
        json={
            "file_id": file_id,
            "fy_label": "2023",
            "entity_mode": "per_entity",
            "entity_prefix": "01",
            "column_map": _COLUMN_MAP,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["inserted"] == 2
    assert body["entity_prefixes"] == ["01"]

    rows = sqlite_session.execute(
        text("SELECT asset_id, entity_prefix, fy_label, opening_cost_ahk, "
             "additions_zugang, depreciation FROM fact_fixed_asset ORDER BY asset_id")
    ).fetchall()
    assert len(rows) == 2
    # Pass-through values normalized from German number format, NOT computed.
    assert rows[0] == ("A-100", "01", "2023", 10000.0, 2500.0, 1250.0)
    assert rows[1][0] == "A-101"
    assert rows[1][1] == "01"


def test_combined_mode_resolves_prefix_from_column(client, sqlite_session):
    file_id = _upload(client)
    resp = client.post(
        "/api/v1/anlagen/commit",
        json={
            "file_id": file_id,
            "fy_label": "2023",
            "entity_mode": "combined",
            "entity_column": "Buchungskreis",
            "column_map": {"asset_id": "AnlNr", "opening_cost_ahk": "AHK_Anfang"},
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["entity_prefixes"] == ["01"]  # "1" -> normalized "01"
    prefixes = {r[0] for r in sqlite_session.execute(
        text("SELECT DISTINCT entity_prefix FROM fact_fixed_asset")).fetchall()}
    assert prefixes == {"01"}


def test_no_computed_rollforward_columns_exist(sqlite_session):
    """DRAFT guarantee: the table carries NO derived closing/accumulated columns."""
    cols = {r[1] for r in sqlite_session.execute(
        text("PRAGMA table_info(fact_fixed_asset)")).fetchall()}
    for forbidden in ("closing_cost_ahk", "accumulated_depreciation", "closing_nbv"):
        assert forbidden not in cols


def test_commit_requires_a_mapping(client):
    file_id = _upload(client)
    resp = client.post(
        "/api/v1/anlagen/commit",
        json={"file_id": file_id, "fy_label": "2023", "entity_prefix": "01",
              "column_map": {}},
    )
    assert resp.status_code == 422


# Whitespace-only Buchungskreis cells resolve to a NULL (unattributed) entity
# prefix in combined mode — the tenant-isolation gap under test.
_REGISTER_ROWS_NULL_ENTITY = [
    {"Buchungskreis": " ", "AnlNr": "A-100", "AHK_Anfang": "10.000,00"},
    {"Buchungskreis": " ", "AnlNr": "A-101", "AHK_Anfang": "30.000,00"},
]


def _upload_null_entity(client) -> str:
    resp = client.post(
        "/api/v1/anlagen/upload",
        files={"file": ("assets.xlsx", _xlsx_bytes(_REGISTER_ROWS_NULL_ENTITY),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["file_id"]


def _commit_combined(client, file_id: str):
    return client.post(
        "/api/v1/anlagen/commit",
        json={
            "file_id": file_id,
            "fy_label": "2023",
            "entity_mode": "combined",
            "entity_column": "Buchungskreis",
            "column_map": {"asset_id": "AnlNr", "opening_cost_ahk": "AHK_Anfang"},
        },
    )


def test_combined_null_entity_rejected_for_restricted_user(restricted_client, sqlite_session):
    """SECURITY: a RESTRICTED user may not persist unattributed (NULL-entity) rows."""
    file_id = _upload_null_entity(restricted_client)
    resp = _commit_combined(restricted_client, file_id)
    assert resp.status_code == 422, resp.text
    assert "no resolvable entity in column 'Buchungskreis'" in resp.json()["detail"]
    sqlite_session.rollback()
    n = sqlite_session.execute(text("SELECT COUNT(*) FROM fact_fixed_asset")).scalar()
    assert n == 0


def test_combined_null_entity_allowed_for_unrestricted_admin(client, sqlite_session):
    """An unrestricted admin (see-all) may legitimately commit NULL-entity rows."""
    file_id = _upload_null_entity(client)
    resp = _commit_combined(client, file_id)
    assert resp.status_code == 200, resp.text
    assert resp.json()["inserted"] == 2
    rows = sqlite_session.execute(
        text("SELECT entity_prefix FROM fact_fixed_asset")
    ).fetchall()
    assert len(rows) == 2
    assert all(r[0] is None for r in rows)
