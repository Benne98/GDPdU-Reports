"""Integration: one shared chart of accounts committed per member of an entity group.

Reproduce-first guard for DEFECT A (2026-06): the account-mapping commit
(``load_account_mapping``) writes only dim_gl_account / dim_gl_na / dim_gl_cf — it
does NOT register the committed entity in dim_legal_entity.  When a single shared CoA
(bare account numbers, no entity column) is committed once per group member
(entity_prefix '01' replace, then '02' append), member '02' ended up with
dim_gl_account rows but ZERO dim_legal_entity rows, so the member was effectively
missing downstream.

The fix mirrors the GL ``/commit`` path: ``mapping_commit`` now calls
``etl.load.upsert_legal_entities_for_prefixes`` after ``load_account_mapping``.  This
test drives the SAME real load sequence the router performs, against a REAL SQLite
engine (the test-DB pattern of test_account_mapping_library.py — no mock session, real
``etl.load.load_account_mapping`` and real ``apply_account_mapping``).

Asserts at the end (per the scenario):
  1. dim_gl_account contains BOTH '01…' and '02…' rows  (regression guard).
  2. dim_legal_entity contains BOTH entity '01' and '02' (FAILS pre-fix, PASS after).
  3. The two prefixes' key sets are DISJOINT and the replace→append second commit
     did NOT wipe the first member's rows (no cross-entity wipe).

Formula (unchanged): account_number_group = entity_prefix(2) + zfill(account, 6).
Synthetic data only (CLAUDE.md rule).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from etl.load import (  # noqa: E402
    load_account_mapping,
    upsert_legal_entities_for_prefixes,
)
from etl.mapping_account import AccountMappingProfile, apply_account_mapping  # noqa: E402

from app.auth import User, current_user, require_admin  # noqa: E402
from app.db import get_session  # noqa: E402
from app.main import app  # noqa: E402


# --------------------------------------------------------------------------- #
# Minimal SQLite schema (mirrors the production columns the loaders touch).
# --------------------------------------------------------------------------- #
_SCHEMA = [
    """
    CREATE TABLE dim_gl_account (
        account_number_group TEXT NOT NULL,
        fiscal_year          INTEGER NOT NULL,
        gl_account_id        TEXT NOT NULL,
        account_name         TEXT,
        level_0 TEXT, level_1 TEXT, level_2 TEXT, level_3 TEXT, level_4 TEXT,
        l4_sub TEXT,
        level_2_sort INTEGER, level_3_sort INTEGER,
        is_ic INTEGER DEFAULT 0,
        source_system TEXT,
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
    CREATE TABLE dim_legal_entity (
        legal_entity_code TEXT NOT NULL,
        entity_prefix     TEXT NOT NULL,
        entity_name       TEXT,
        is_consolidation  INTEGER DEFAULT 0,
        country_code      TEXT,
        default_currency  TEXT,
        source_system     TEXT,
        PRIMARY KEY (legal_entity_code)
    )
    """,
]

_FY = 2024
# One shared, prefix-less group chart of accounts (bare German GL numbers).
_BARE_ACCOUNTS = ["11701", "10000", "30000"]


def _make_session() -> Session:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for ddl in _SCHEMA:
            conn.execute(text(ddl))
    return Session(engine)


def _shared_coa() -> pd.DataFrame:
    """Single shared CoA: bare account numbers, NO entity-prefix column."""
    n = len(_BARE_ACCOUNTS)
    return pd.DataFrame({
        "Konto":  _BARE_ACCOUNTS,
        "Ebene0": ["BS", "BS", "PL"][:n],
        "Ebene1": ["Umlaufvermögen", "Umlaufvermögen", "Erträge"][:n],
        "Ebene2": ["Forderungen", "Forderungen", "Umsatzerlöse"][:n],
        "Ebene3": ["Trade receivables", "Cash", "Net sales"][:n],
    })


def _profile() -> AccountMappingProfile:
    p = AccountMappingProfile()
    p.entity = {"mode": "fixed", "value": "01"}      # overridden per member at call time
    p.fiscal_year = {"mode": "fixed", "value": _FY}
    p.columns = {
        "account_number": "Konto",
        "level_0":        "Ebene0",
        "level_1":        "Ebene1",
        "level_2":        "Ebene2",
        "level_3":        "Ebene3",
    }
    p.source_system = "shared_coa_test"
    return p


def _commit_member(session: Session, prefix: str) -> None:
    """Replicate the router commit for one member: build the prefixed mapping from the
    shared CoA, load it, then register the member in dim_legal_entity (the fix)."""
    raw = _shared_coa()
    mapping_df = apply_account_mapping(raw, _profile(), entity_prefix=prefix, fiscal_year=_FY)
    load_account_mapping(session, mapping_df, auto_commit=False)
    upsert_legal_entities_for_prefixes(
        session, [prefix], source_system="shared_coa_test", auto_commit=False
    )
    session.commit()


def _ang_keys(session: Session) -> set[str]:
    rows = session.execute(text("SELECT account_number_group FROM dim_gl_account")).fetchall()
    return {str(r[0]) for r in rows}


def _legal_prefixes(session: Session) -> set[str]:
    rows = session.execute(text("SELECT entity_prefix FROM dim_legal_entity")).fetchall()
    return {str(r[0]) for r in rows}


# =========================================================================== #
# Reproduce-first integration test
# =========================================================================== #

def test_shared_coa_commit_registers_every_member_entity():
    """One shared CoA committed per member ('01' replace, then '02' append) must
    populate BOTH dim_gl_account AND dim_legal_entity for every member."""
    session = _make_session()

    _commit_member(session, "01")          # first member — "replace" semantics
    keys_after_01 = _ang_keys(session)
    _commit_member(session, "02")          # second member — "append" semantics

    ang_keys = _ang_keys(session)
    keys_01 = {k for k in ang_keys if k.startswith("01")}
    keys_02 = {k for k in ang_keys if k.startswith("02")}

    # (1) dim_gl_account has BOTH members' rows (regression guard).
    assert keys_01 == {"01011701", "01010000", "01030000"}, keys_01
    assert keys_02 == {"02011701", "02010000", "02030000"}, keys_02

    # (2) dim_legal_entity has BOTH members — the DEFECT A fix.
    assert _legal_prefixes(session) == {"01", "02"}

    # (3) Disjoint key sets AND the append commit did not wipe member '01'.
    assert keys_01.isdisjoint(keys_02)
    assert keys_01 <= ang_keys
    assert keys_after_01 == {"01011701", "01010000", "01030000"}, (
        "first member's keys must survive the second (append) commit"
    )


def test_repeat_commit_is_idempotent_on_dim_legal_entity():
    """Re-committing the same member must not error (ON CONFLICT) and must not
    create duplicate dim_legal_entity rows — replace/append safe."""
    session = _make_session()
    _commit_member(session, "01")
    _commit_member(session, "01")          # idempotent re-commit
    rows = session.execute(
        text("SELECT COUNT(*) FROM dim_legal_entity WHERE entity_prefix = '01'")
    ).fetchone()
    assert int(rows[0]) == 1


def test_reproduces_defect_without_the_fix():
    """Documents the pre-fix behaviour: load_account_mapping ALONE (the old commit
    path) leaves dim_legal_entity EMPTY even though dim_gl_account is populated.
    The fix is exactly the additional upsert_legal_entities_for_prefixes call."""
    session = _make_session()
    raw = _shared_coa()
    mapping_df = apply_account_mapping(raw, _profile(), entity_prefix="02", fiscal_year=_FY)
    load_account_mapping(session, mapping_df, auto_commit=True)

    # dim_gl_account got the member's rows ...
    assert {k for k in _ang_keys(session) if k.startswith("02")}
    # ... but WITHOUT the fix, dim_legal_entity stays empty (the defect).
    assert _legal_prefixes(session) == set()


# =========================================================================== #
# SECURITY regression — placeholder upsert must NOT clobber confirmed metadata
# =========================================================================== #

def test_placeholder_upsert_preserves_confirmed_entity_metadata():
    """A later placeholder mapping-commit upsert for prefix '01' must NOT overwrite a
    REAL entity_name / is_consolidation already confirmed by the GL ``/commit`` path.

    Pre-fix (plain ``ON CONFLICT DO UPDATE SET entity_name = EXCLUDED.entity_name``)
    clobbered the real name back to the bare prefix '01' and reset is_consolidation to
    False — a non-destructive register-if-absent upsert keeps the confirmed values.
    This FAILS before the ``preserve_existing`` fix and PASSES after.
    """
    session = _make_session()
    # Seed dim_legal_entity exactly as the GL /commit path would: a REAL name (wizard
    # label) plus a confirmed consolidation flag and metadata.
    session.execute(
        text(
            "INSERT INTO dim_legal_entity "
            "(legal_entity_code, entity_prefix, entity_name, is_consolidation, "
            " country_code, default_currency, source_system) "
            "VALUES ('01', '01', 'Muster GmbH', 1, 'DEU', 'EUR', 'gl_commit')"
        )
    )
    session.commit()

    # Run the placeholder member upsert (entity_name defaults to the bare prefix '01').
    upsert_legal_entities_for_prefixes(
        session, ["01"], source_system="shared_coa_test", auto_commit=True
    )

    row = session.execute(
        text(
            "SELECT entity_name, is_consolidation, country_code, source_system "
            "FROM dim_legal_entity WHERE legal_entity_code = '01'"
        )
    ).fetchone()
    assert row is not None
    # Real metadata SURVIVES (would be clobbered to '01' / 0 / NULL before the fix).
    assert row[0] == "Muster GmbH", f"entity_name clobbered to {row[0]!r}"
    assert int(row[1]) == 1, "is_consolidation must be preserved (True)"
    assert row[2] == "DEU", "country_code must be preserved"
    assert row[3] == "gl_commit", "source_system must be preserved"


# =========================================================================== #
# M-2 — router-level integration: POST /mapping/commit registers the entity
# =========================================================================== #

# One shared, prefix-less group chart of accounts (bare German GL numbers).
_COA_CSV = (
    "Konto,Ebene0,Ebene1,Ebene2,Ebene3\n"
    "11701,BS,Umlaufvermoegen,Forderungen,Trade receivables\n"
    "10000,BS,Umlaufvermoegen,Forderungen,Cash\n"
    "30000,PL,Ertraege,Umsatzerloese,Net sales\n"
)

_COA_PROFILE: dict = {
    "entity": {"mode": "fixed", "value": "07"},
    "fiscal_year": {"mode": "fixed", "value": 2024},
    "columns": {
        "account_number": "Konto",
        "level_0": "Ebene0",
        "level_1": "Ebene1",
        "level_2": "Ebene2",
        "level_3": "Ebene3",
    },
    "source_system": "router_shared_coa_test",
}

_ADMIN = User(user_id=1, email="admin@test", display_name="Admin", is_admin=True)


class _RecordingResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _RecordingSession:
    """Records every executed statement; returns empty results.

    fetchone() -> None so the org_meta_dataset_load INSERT ... RETURNING yields no
    load_id (snapshot capture is skipped), keeping the commit fully DB-free.  The
    real ``upsert_legal_entities_for_prefixes`` still runs, so its INSERT INTO
    dim_legal_entity is recorded here — that is what this test asserts.
    """

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def execute(self, stmt, params=None):
        self.calls.append((str(stmt), dict(params or {})))
        return _RecordingResult([])

    def commit(self):
        pass

    def rollback(self):
        pass


def test_router_mapping_commit_registers_legal_entity():
    """DEFECT A router wiring: POST /api/v1/ingest/mapping/commit must register the
    committed prefix in dim_legal_entity THROUGH THE ROUTER.

    Unlike the direct-helper tests above, this drives the real endpoint, so it pins
    the ``upsert_legal_entities_for_prefixes`` call inside ``mapping_commit``: if that
    call were removed, NO 'INSERT INTO dim_legal_entity' would be recorded and this
    fails.  ``load_account_mapping`` is patched (DB-heavy) so the test stays DB-free
    and focuses on the legal-entity wiring; the upsert helper runs for real.
    """
    session = _RecordingSession()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[current_user] = lambda: _ADMIN
    app.dependency_overrides[require_admin] = lambda: _ADMIN
    try:
        client = TestClient(app, raise_server_exceptions=False)
        up = client.post(
            "/api/v1/ingest/upload",
            files={"file": ("coa.csv", _COA_CSV.encode("utf-8"), "text/csv")},
        )
        assert up.status_code == 200, up.text
        file_id = up.json()["file_id"]

        with patch(
            "app.routers.ingest.load_account_mapping",
            return_value={"accounts": 3, "na": 0, "cf": 0},
        ):
            resp = client.post(
                "/api/v1/ingest/mapping/commit",
                json={
                    "file_id": file_id,
                    "format": "generic",
                    "profile": _COA_PROFILE,
                    "fiscal_years": [2024],
                    "entity_prefix": "07",
                },
            )
        assert resp.status_code == 200, resp.text

        le_inserts = [
            params
            for sql, params in session.calls
            if "INSERT INTO dim_legal_entity" in sql
        ]
        assert le_inserts, (
            "mapping_commit must upsert dim_legal_entity for the committed prefix "
            "(the upsert_legal_entities_for_prefixes call was removed?)"
        )
        assert any(
            params.get("ep") == "07" or params.get("code") == "07"
            for params in le_inserts
        ), le_inserts
    finally:
        app.dependency_overrides.clear()
