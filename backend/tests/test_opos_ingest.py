"""D4 DRAFT — OPOS Debitor/Kreditor ingest tests (SYNTHETIC, isolated).

Covers the two-sided upload -> commit roundtrip for ``fact_opos_debitor`` /
``fact_opos_kreditor`` against a throwaway in-memory SQLite DB. Asserts:
  * pass-through postings are stored with partner_id = entity_prefix(2)||konto,
  * the DRAFT analytic columns ``is_open`` + ``aging_band`` remain NULL,
  * the aging-derivation stub raises NotImplementedError / returns HTTP 501.
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

# SQLite-equivalent of migrations 0023 + 0024's two OPOS tables (analytic
# is_open/aging_band and all as-of columns NULLABLE).
_DDL_TEMPLATE = """
CREATE TABLE {table} (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id          TEXT NOT NULL DEFAULT 'default',
    dataset_version_id  INTEGER,
    source_file_id      TEXT,
    row_no              INTEGER,
    created_at          TEXT,
    entity_prefix       TEXT,
    fy_label            TEXT,
    partner_id          TEXT,
    konto               TEXT,
    belegart            TEXT,
    beleg_no            TEXT,
    referenz            TEXT,
    net_due_date        TEXT,
    amount_hauswaehrung NUMERIC,
    posting_date        TEXT,
    -- 0024 as-of columns
    buchungskreis       INTEGER,
    satzart             TEXT,
    partner_no          TEXT,
    partner_key         TEXT,
    beleg_date          TEXT,
    mahnstufe           INTEGER,
    waehrung            TEXT,
    geschaeftsbereich   TEXT,
    buchungsschluessel  TEXT,
    konto_gegenbuchung  TEXT,
    gobd_transaktionsnr TEXT,
    is_open             BOOLEAN,
    aging_band          TEXT
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
        for table in ("fact_opos_debitor", "fact_opos_kreditor"):
            conn.execute(text(_DDL_TEMPLATE.format(table=table)))
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


_OPOS_ROWS = [
    {"BuKr": "1", "Konto": "10000", "Belegart": "RV", "BelegNr": "1900001",
     "Referenz": "INV-1", "Nettofaelligkeit": "30.04.2023",
     "Betrag": "1.190,00", "Buchungsdatum": "31.03.2023"},
    {"BuKr": "1", "Konto": "10000", "Belegart": "ZA", "BelegNr": "5000009",
     "Referenz": "INV-1", "Nettofaelligkeit": "30.04.2023",
     "Betrag": "-1.190,00", "Buchungsdatum": "28.04.2023"},
]

_COLUMN_MAP = {
    "konto": "Konto",
    "belegart": "Belegart",
    "beleg_no": "BelegNr",
    "referenz": "Referenz",
    "net_due_date": "Nettofaelligkeit",
    "amount_hauswaehrung": "Betrag",
    "posting_date": "Buchungsdatum",
}


# Whitespace-only Buchungskreis cells resolve to a NULL (unattributed) entity
# prefix in combined mode — the tenant-isolation gap under test.
_OPOS_ROWS_NULL_ENTITY = [
    {"BuKr": " ", "Konto": "10000", "Belegart": "RV", "BelegNr": "1900001",
     "Referenz": "INV-1", "Nettofaelligkeit": "30.04.2023",
     "Betrag": "1.190,00", "Buchungsdatum": "31.03.2023"},
    {"BuKr": " ", "Konto": "10001", "Belegart": "ZA", "BelegNr": "5000009",
     "Referenz": "INV-1", "Nettofaelligkeit": "30.04.2023",
     "Betrag": "-1.190,00", "Buchungsdatum": "28.04.2023"},
]


def _upload(client, side: str) -> str:
    resp = client.post(
        f"/api/v1/opos/{side}/upload",
        files={"file": ("opos.xlsx", _xlsx_bytes(_OPOS_ROWS),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["file_id"]


def _commit(client, side: str, file_id: str):
    return client.post(
        f"/api/v1/opos/{side}/commit",
        json={
            "file_id": file_id,
            "fy_label": "2023",
            "entity_mode": "per_entity",
            "entity_prefix": "01",
            "column_map": _COLUMN_MAP,
        },
    )


@pytest.mark.parametrize("side,table", [
    ("debitor", "fact_opos_debitor"),
    ("kreditor", "fact_opos_kreditor"),
])
def test_upload_commit_roundtrip_leaves_analytics_null(client, sqlite_session, side, table):
    file_id = _upload(client, side)
    resp = _commit(client, side, file_id)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["side"] == side
    assert body["inserted"] == 2

    rows = sqlite_session.execute(
        text(f"SELECT partner_id, konto, belegart, amount_hauswaehrung, "
             f"is_open, aging_band FROM {table} ORDER BY beleg_no")
    ).fetchall()
    assert len(rows) == 2
    # partner_id = entity_prefix(2) || konto
    assert all(r[0] == "0110000" for r in rows)
    assert rows[0][2] == "RV"
    assert rows[0][3] == 1190.0
    assert rows[1][3] == -1190.0
    # DRAFT: analytic columns are LEFT NULL (no settlement matching / bucketing).
    assert all(r[4] is None for r in rows)  # is_open
    assert all(r[5] is None for r in rows)  # aging_band


def test_derive_aging_endpoint_returns_501(client):
    for side in ("debitor", "kreditor"):
        resp = client.post(f"/api/v1/opos/{side}/derive-aging")
        assert resp.status_code == 501, resp.text


def test_derive_aging_function_raises_not_implemented():
    from app.routers.opos import derive_aging

    with pytest.raises(NotImplementedError):
        derive_aging(session=None, side="debitor")


def test_unknown_side_is_404(client):
    file_id = _upload(client, "debitor")
    resp = _commit(client, "unknown", file_id)
    assert resp.status_code == 404


def _upload_null_entity(client, side: str) -> str:
    resp = client.post(
        f"/api/v1/opos/{side}/upload",
        files={"file": ("opos.xlsx", _xlsx_bytes(_OPOS_ROWS_NULL_ENTITY),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["file_id"]


def _commit_combined(client, side: str, file_id: str):
    return client.post(
        f"/api/v1/opos/{side}/commit",
        json={
            "file_id": file_id,
            "fy_label": "2023",
            "entity_mode": "combined",
            "entity_column": "BuKr",
            "column_map": _COLUMN_MAP,
        },
    )


def test_combined_null_entity_rejected_for_restricted_user(restricted_client, sqlite_session):
    """SECURITY: a RESTRICTED user may not persist unattributed (NULL-entity) rows."""
    file_id = _upload_null_entity(restricted_client, "debitor")
    resp = _commit_combined(restricted_client, "debitor", file_id)
    assert resp.status_code == 422, resp.text
    assert "no resolvable entity in column 'BuKr'" in resp.json()["detail"]
    # Nothing was persisted (fail-closed before INSERT).
    sqlite_session.rollback()
    n = sqlite_session.execute(
        text("SELECT COUNT(*) FROM fact_opos_debitor")
    ).scalar()
    assert n == 0


def test_combined_null_entity_allowed_for_unrestricted_admin(client, sqlite_session):
    """An unrestricted admin (see-all) may legitimately commit NULL-entity rows."""
    file_id = _upload_null_entity(client, "debitor")
    resp = _commit_combined(client, "debitor", file_id)
    assert resp.status_code == 200, resp.text
    assert resp.json()["inserted"] == 2
    prefixes = sqlite_session.execute(
        text("SELECT entity_prefix FROM fact_opos_debitor")
    ).fetchall()
    assert len(prefixes) == 2
    assert all(r[0] is None for r in prefixes)


# --------------------------------------------------------------------------- #
# combined/commit: ONE file, both sides, split on a discriminator column.
# --------------------------------------------------------------------------- #
_OPOS_ROWS_COMBINED = [
    # side=D -> debitor
    {"Side": "D", "Konto": "10000", "Belegart": "RV", "BelegNr": "1900001",
     "Referenz": "INV-1", "Nettofaelligkeit": "30.04.2023",
     "Betrag": "1.190,00", "Buchungsdatum": "31.03.2023"},
    # side=" K " (whitespace-padded) -> kreditor (robust trimmed compare)
    {"Side": " K ", "Konto": "70000", "Belegart": "KR", "BelegNr": "1900002",
     "Referenz": "INV-2", "Nettofaelligkeit": "30.05.2023",
     "Betrag": "-500,00", "Buchungsdatum": "30.04.2023"},
    # side=X -> matches neither -> skipped, NOT committed
    {"Side": "X", "Konto": "99999", "Belegart": "SA", "BelegNr": "1900003",
     "Referenz": "INV-3", "Nettofaelligkeit": "30.06.2023",
     "Betrag": "10,00", "Buchungsdatum": "31.05.2023"},
]


def _upload_combined(client) -> str:
    resp = client.post(
        "/api/v1/opos/debitor/upload",
        files={"file": ("opos.xlsx", _xlsx_bytes(_OPOS_ROWS_COMBINED),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["file_id"]


def test_combined_commit_splits_rows_to_correct_tables_and_skips_unmatched(client, sqlite_session):
    file_id = _upload_combined(client)
    resp = client.post(
        "/api/v1/opos/combined/commit",
        json={
            "file_id": file_id,
            "fy_label": "2023",
            "entity_mode": "per_entity",
            "entity_prefix": "01",
            "column_map": _COLUMN_MAP,
            "side_column": "Side",
            "debitor_value": "D",
            "kreditor_value": "K",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["debitor_inserted"] == 1
    assert body["kreditor_inserted"] == 1
    assert body["skipped_unmatched"] == 1
    assert body["entity_prefixes"] == ["01"]

    deb = sqlite_session.execute(
        text("SELECT konto, amount_hauswaehrung FROM fact_opos_debitor")
    ).fetchall()
    kre = sqlite_session.execute(
        text("SELECT konto, amount_hauswaehrung FROM fact_opos_kreditor")
    ).fetchall()
    assert [(r[0], r[1]) for r in deb] == [("10000", 1190.0)]
    assert [(r[0], r[1]) for r in kre] == [("70000", -500.0)]
    # The unmatched (Side=X) row landed in NEITHER table.
    assert not any(r[0] == "99999" for r in deb + kre)


def test_combined_commit_rejects_equal_side_values(client):
    file_id = _upload_combined(client)
    resp = client.post(
        "/api/v1/opos/combined/commit",
        json={
            "file_id": file_id, "fy_label": "2023",
            "entity_mode": "per_entity", "entity_prefix": "01",
            "column_map": _COLUMN_MAP,
            "side_column": "Side", "debitor_value": "D", "kreditor_value": "D",
        },
    )
    assert resp.status_code == 422, resp.text


def test_combined_commit_unknown_side_column_is_422(client):
    file_id = _upload_combined(client)
    resp = client.post(
        "/api/v1/opos/combined/commit",
        json={
            "file_id": file_id, "fy_label": "2023",
            "entity_mode": "per_entity", "entity_prefix": "01",
            "column_map": _COLUMN_MAP,
            "side_column": "Nope", "debitor_value": "D", "kreditor_value": "K",
        },
    )
    assert resp.status_code == 422, resp.text


def test_combined_commit_no_matches_is_422(client, sqlite_session):
    file_id = _upload_combined(client)
    resp = client.post(
        "/api/v1/opos/combined/commit",
        json={
            "file_id": file_id, "fy_label": "2023",
            "entity_mode": "per_entity", "entity_prefix": "01",
            "column_map": _COLUMN_MAP,
            "side_column": "Side", "debitor_value": "AAA", "kreditor_value": "BBB",
        },
    )
    assert resp.status_code == 422, resp.text
    sqlite_session.rollback()
    n_deb = sqlite_session.execute(text("SELECT COUNT(*) FROM fact_opos_debitor")).scalar()
    n_kre = sqlite_session.execute(text("SELECT COUNT(*) FROM fact_opos_kreditor")).scalar()
    assert n_deb == 0 and n_kre == 0


# --------------------------------------------------------------------------- #
# 0024: canonical Buchungskreis -> entity_prefix / partner_key resolution.
# Locks in the architect-resolved map (3000 -> "02" -> Meridian, NOT Calypto).
# --------------------------------------------------------------------------- #
_OPOS_ROWS_BUKRS = [
    {"BuKr": "3000", "Konto": "24000", "Belegart": "RV", "BelegNr": "1900001",
     "Referenz": "INV-1", "Nettofaelligkeit": "30.04.2023", "Betrag": "1.190,00",
     "Buchungsdatum": "31.03.2023", "Debitor": "240155", "Satzart": "Bewegung"},
]
_COLUMN_MAP_BUKRS = {
    **_COLUMN_MAP,
    "buchungskreis": "BuKr",
    "partner_no": "Debitor",
    "satzart": "Satzart",
}


def test_buchungskreis_resolves_canonical_prefix_and_partner_key(client, sqlite_session):
    """buchungskreis 3000 -> entity_prefix '02' (Meridian) and partner_key '02'||Debitor."""
    resp = client.post(
        "/api/v1/opos/debitor/upload",
        files={"file": ("opos.xlsx", _xlsx_bytes(_OPOS_ROWS_BUKRS),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200, resp.text
    file_id = resp.json()["file_id"]
    commit = client.post(
        "/api/v1/opos/debitor/commit",
        json={
            "file_id": file_id,
            "fy_label": "2023",
            "entity_mode": "combined",
            "entity_column": "BuKr",
            "column_map": _COLUMN_MAP_BUKRS,
        },
    )
    assert commit.status_code == 200, commit.text
    assert commit.json()["entity_prefixes"] == ["02"]
    row = sqlite_session.execute(
        text("SELECT buchungskreis, entity_prefix, partner_no, partner_key, satzart "
             "FROM fact_opos_debitor")
    ).fetchone()
    assert row[0] == 3000            # buchungskreis stored explicitly as INTEGER
    assert row[1] == "02"            # 02 = Meridian (resolved conflict)
    assert row[2] == "240155"
    assert row[3] == "02240155"      # partner_key = entity_prefix(2) || partner_no
    assert row[4] == "Bewegung"
