"""POST /api/v1/ingest/mapping/{candidate-positions,assign} — manual GL mapping.

The user hand-picks a target statement position for a GL account that failed the
``gl_accounts_unmapped`` pre-flight (absent from dim_gl_account) and assigns it.
The assignment is written into dim_gl_account (so the blocked commit can proceed)
AND upserted into dim_project_coa_override (so it survives a full data reload).

Unlike test_ingest_apply_library (which patches the writer), these tests drive
``load_account_mapping`` + ``capture_overrides_from_accounts`` against a REAL
in-memory sqlite session so BOTH tables can be asserted end-to-end. The schema
mirrors etl/tests/test_client_coa.py.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth import User, current_user, require_admin
from app.db import get_session
from app.main import app

_ADMIN = User(user_id=1, email="admin@test", display_name="Admin", is_admin=True)
_RESTRICTED = User(user_id=2, email="user@test", display_name="User", is_admin=False)

_CAND = "/api/v1/ingest/mapping/candidate-positions"
_ASSIGN = "/api/v1/ingest/mapping/assign"

# Schema mirrors etl/tests/test_client_coa.py (the loader + override writer targets)
# plus the PL structure table read by /candidate-positions.
_SCHEMA = [
    """
    CREATE TABLE dim_gl_account (
        account_number_group TEXT NOT NULL,
        fiscal_year          INTEGER NOT NULL,
        gl_account_id        TEXT,
        account_name         TEXT,
        level_0 TEXT, level_1 TEXT, level_2 TEXT, level_3 TEXT, level_4 TEXT,
        l4_sub TEXT,
        level_1_sort INTEGER, level_2_sort INTEGER, level_3_sort INTEGER, level_4_sort INTEGER,
        is_ic INTEGER DEFAULT 0,
        source_system TEXT,
        entity_prefix TEXT,
        PRIMARY KEY (account_number_group, fiscal_year)
    )
    """,
    """
    CREATE TABLE dim_gl_na (
        account_number_group TEXT NOT NULL,
        fiscal_year INTEGER NOT NULL,
        l6_na_mapping TEXT, l7_na_description TEXT,
        PRIMARY KEY (account_number_group, fiscal_year)
    )
    """,
    """
    CREATE TABLE dim_gl_cf (
        account_number_group TEXT NOT NULL,
        fiscal_year INTEGER NOT NULL,
        l1 TEXT, l2 TEXT, l3 TEXT, l4 TEXT, l5 TEXT, cf_mapping TEXT,
        PRIMARY KEY (account_number_group, fiscal_year)
    )
    """,
    """
    CREATE TABLE dim_project_coa_override (
        project_id TEXT NOT NULL DEFAULT 'default',
        account_number_group TEXT NOT NULL,
        fiscal_year INTEGER NOT NULL,
        level_0 TEXT, level_1 TEXT, level_2 TEXT, level_3 TEXT, level_4 TEXT,
        l4_sub TEXT,
        level_1_sort INTEGER, level_2_sort INTEGER, level_3_sort INTEGER, level_4_sort INTEGER,
        updated_at TEXT,
        PRIMARY KEY (project_id, account_number_group, fiscal_year)
    )
    """,
    """
    CREATE TABLE dim_pl_structure (
        pl_line_id INTEGER PRIMARY KEY,
        sort_order INTEGER,
        line_code TEXT,
        row_type TEXT,
        balance_title TEXT,
        details TEXT,
        calc_type TEXT,
        level_2 TEXT, level_3 TEXT, level_4 TEXT,
        gl_account_id TEXT,
        invert_delta INTEGER,
        is_bold INTEGER,
        kpi_code TEXT
    )
    """,
]


def _make_session() -> Session:
    # StaticPool + check_same_thread=False: the TestClient dispatches the request
    # on a worker thread, so the in-memory DB connection must be shareable.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        for ddl in _SCHEMA:
            conn.execute(text(ddl))
        # One mapping row + one subtotal row (subtotal must NOT be a candidate).
        conn.execute(text(
            "INSERT INTO dim_pl_structure "
            "(pl_line_id, sort_order, line_code, row_type, balance_title, level_2, level_3, level_4) "
            "VALUES "
            "(1, 10, 'PL_REV', 'mapping', 'Revenue', 'Sales', 'Net sales', NULL),"
            "(2, 20, 'PL_SUB', 'subtotal', 'Gross profit', NULL, NULL, NULL)"
        ))
    return Session(engine)


@pytest.fixture()
def session() -> Session:
    s = _make_session()
    yield s
    s.close()
    app.dependency_overrides.clear()


def _client(session: Session, user: User) -> TestClient:
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[current_user] = lambda: user
    if user.is_admin:
        app.dependency_overrides[require_admin] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


# =========================================================================== #
# candidate-positions: only row_type='mapping' rows, with the level path.
# =========================================================================== #
def test_candidate_positions_returns_mapping_rows_only(session):
    client = _client(session, _ADMIN)
    resp = client.post(_CAND, json={})
    assert resp.status_code == 200, resp.text
    positions = resp.json()["positions"]
    assert len(positions) == 1
    pos = positions[0]
    assert pos["statement"] == "PL"
    assert pos["line_code"] == "PL_REV"
    assert pos["balance_title"] == "Revenue"
    assert pos["level_2"] == "Sales"
    assert pos["level_3"] == "Net sales"
    # The subtotal row must never be offered as an assignable position.
    assert all(p["line_code"] != "PL_SUB" for p in positions)


# =========================================================================== #
# assign: BOTH dim_gl_account AND dim_project_coa_override get the row.
# =========================================================================== #
def test_assign_writes_account_and_override(session):
    client = _client(session, _ADMIN)
    resp = client.post(
        _ASSIGN,
        json={
            "assignments": [
                {
                    "account_number_group": "0144000",
                    "gl_account_id": "44000",
                    "fiscal_year": 2024,
                    "account_name": "Umsatzerloese",
                    "level_0": "PL",
                    "level_2": "Sales",
                    "level_3": "Net sales",
                }
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"inserted": 1, "overrides_written": 1}

    acct = session.execute(text(
        "SELECT level_0, level_2, level_3, source_system FROM dim_gl_account "
        "WHERE account_number_group='0144000' AND fiscal_year=2024"
    )).fetchone()
    assert acct is not None
    assert acct[0] == "PL" and acct[1] == "Sales" and acct[2] == "Net sales"
    assert acct[3] == "manual"

    ovr = session.execute(text(
        "SELECT project_id, level_0, level_2, level_3 FROM dim_project_coa_override "
        "WHERE account_number_group='0144000' AND fiscal_year=2024"
    )).fetchone()
    assert ovr is not None
    assert ovr[0] == "default"
    assert ovr[1] == "PL" and ovr[2] == "Sales" and ovr[3] == "Net sales"


# =========================================================================== #
# assign is idempotent: re-assigning the same account updates, never duplicates.
# =========================================================================== #
def test_assign_is_idempotent(session):
    client = _client(session, _ADMIN)
    payload = {
        "assignments": [
            {
                "account_number_group": "0144000",
                "gl_account_id": "44000",
                "fiscal_year": 2024,
                "level_0": "PL",
                "level_2": "Sales",
                "level_3": "Net sales",
            }
        ]
    }
    first = client.post(_ASSIGN, json=payload)
    # Second time with a different target level -> update, not a duplicate row.
    payload["assignments"][0]["level_3"] = "Other income"
    second = client.post(_ASSIGN, json=payload)
    assert first.status_code == 200 and second.status_code == 200

    n_acct = session.execute(text(
        "SELECT COUNT(*) FROM dim_gl_account WHERE account_number_group='0144000'"
    )).scalar()
    n_ovr = session.execute(text(
        "SELECT COUNT(*) FROM dim_project_coa_override WHERE account_number_group='0144000'"
    )).scalar()
    assert n_acct == 1
    assert n_ovr == 1
    latest = session.execute(text(
        "SELECT level_3 FROM dim_project_coa_override WHERE account_number_group='0144000'"
    )).scalar()
    assert latest == "Other income"
