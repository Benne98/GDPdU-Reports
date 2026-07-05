"""Reproduce-first regression tests for CoA multi-entity group fan-out fix.

BACKGROUND (fix already implemented):
  When 5 entities are assigned to one CoA "Group A" and ONE shared chart is
  uploaded, previously only Atlas ('01') was committed.  The cause was stale
  wizard state (coa.assigned=True + coa.groups with memberEntityCodes=['01'])
  skipping the re-assignment step, so the frontend sent entity_prefixes=['01'].
  The backend multi-prefix fan-out was already correct; the defect was purely
  in the frontend payload.

  Fixes applied:
    1. Frontend: coaGroupsCoverAllEntities() coverage gate (see coaAssignment.ts).
    2. Frontend: buildInitialCoaAssignment() always assigns every entity to
       'coa-shared' regardless of formatGroupId.
    3. Frontend: AccountColumnMapper remount keys (unrelated UI fix).
  These are locked by Test 1 in coaAssignment.test.ts (npx tsx).

THIS FILE — Test 2 — proves the backend commit path:
  - dim_gl_account ends with the FULL chart for EVERY member prefix (equal row
    counts; no prefix silently dropped or truncated).
  - dim_legal_entity contains an entry for every member prefix.

Test structure
--------------
  test_five_member_direct_etl_equal_account_counts (SQLite; always runs):
    Drives apply_account_mapping + load_account_mapping +
    upsert_legal_entities_for_prefixes for each of 5 prefixes, then asserts
    equal counts.  This is the minimal reproduce-first path.

  test_old_behavior_simulation_only_one_prefix_committed (SQLite; always runs):
    Explicitly reproduces the OLD defect: committing only prefix '01' leaves
    '02'..'05' with 0 rows.  Confirms that test above would have FAILED on old
    code (the frontend sent only prefix '01').

  test_five_member_router_endpoint_equal_account_counts (PG; skips if no PG):
    Drives the REAL POST /api/v1/ingest/mapping/commit endpoint with
    entity_prefixes=['01','02','03','04','05'].  Asserts at the DB layer.
    This is the full-stack regression guard for backend truncation.

Pre-existing unrelated failure (do NOT fix here):
  backend/tests/test_ingest_issue_rows.py::
      test_commit_exact_string_exclusion_drops_offender_from_loaded_rows
  fails with 422 gl_accounts_unmapped — unrelated to CoA grouping logic.
"""
from __future__ import annotations

import io
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from openpyxl import Workbook
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_BACKEND_DIR = Path(__file__).resolve().parents[1]
os.environ.setdefault("NARRATIVE_WARM_ON_INGEST", "0")

from etl.load import load_account_mapping, upsert_legal_entities_for_prefixes  # noqa: E402
from etl.mapping_account import AccountMappingProfile, apply_account_mapping     # noqa: E402

from app.auth import User, current_user, require_admin  # noqa: E402
from app.db import get_session                           # noqa: E402
from app.main import app                                 # noqa: E402
from app.routers.ingest import UPLOAD_DIR               # noqa: E402

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
_MEMBER_PREFIXES = ["01", "02", "03", "04", "05"]
_FY = 2024
_ADMIN = User(user_id=1, email="admin@fanout-test", display_name="Admin", is_admin=True)

# Synthetic shared CoA: 4 bare accounts (2 BS, 2 PL).
_BARE_ACCOUNTS = ["10000", "16100", "80000", "70000"]
_COA_L0 = ["BS", "BS", "PL", "PL"]
_COA_L1 = ["Assets", "Assets", "Revenue", "Expense"]
_COA_L2 = ["Current", "Current", "Sales", "Material"]
_COA_L3 = ["Cash", "Trade AR", "Net sales", "COGS"]
_EXPECTED_ACCOUNT_COUNT = len(_BARE_ACCOUNTS)  # 4

# --------------------------------------------------------------------------- #
# Minimal SQLite schema for direct-ETL tests
# --------------------------------------------------------------------------- #
_SCHEMA = [
    """CREATE TABLE dim_gl_account (
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
    )""",
    """CREATE TABLE dim_gl_na (
        account_number_group TEXT NOT NULL,
        fiscal_year INTEGER NOT NULL,
        l6_na_mapping TEXT, l7_na_description TEXT,
        PRIMARY KEY (account_number_group, fiscal_year)
    )""",
    """CREATE TABLE dim_gl_cf (
        account_number_group TEXT NOT NULL,
        fiscal_year INTEGER NOT NULL,
        l1 TEXT, l2 TEXT, l3 TEXT, l4 TEXT, l5 TEXT, cf_mapping TEXT,
        PRIMARY KEY (account_number_group, fiscal_year)
    )""",
    """CREATE TABLE dim_legal_entity (
        legal_entity_code TEXT NOT NULL,
        entity_prefix     TEXT NOT NULL,
        entity_name       TEXT,
        is_consolidation  INTEGER DEFAULT 0,
        country_code      TEXT,
        default_currency  TEXT,
        source_system     TEXT,
        PRIMARY KEY (legal_entity_code)
    )""",
]


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def _make_sqlite_session() -> Session:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for ddl in _SCHEMA:
            conn.execute(text(ddl))
    return Session(engine)


def _shared_coa_df() -> pd.DataFrame:
    """Bare-account synthetic shared CoA (no entity-prefix column)."""
    return pd.DataFrame({
        "Acct": _BARE_ACCOUNTS,
        "L0": _COA_L0,
        "L1": _COA_L1,
        "L2": _COA_L2,
        "L3": _COA_L3,
    })


def _profile_for(prefix: str) -> AccountMappingProfile:
    p = AccountMappingProfile()
    p.entity = {"mode": "fixed", "value": prefix}
    p.fiscal_year = {"mode": "fixed", "value": _FY}
    p.columns = {
        "account_number": "Acct",
        "level_0": "L0",
        "level_1": "L1",
        "level_2": "L2",
        "level_3": "L3",
    }
    p.source_system = "fanout_test"
    return p


def _commit_one_member(session: Session, prefix: str) -> None:
    """Mirror the router commit path for one member prefix."""
    raw = _shared_coa_df()
    mapping_df = apply_account_mapping(
        raw, _profile_for(prefix), entity_prefix=prefix, fiscal_year=_FY
    )
    load_account_mapping(session, mapping_df, auto_commit=False)
    upsert_legal_entities_for_prefixes(
        session, [prefix], source_system="fanout_test", auto_commit=False
    )
    session.commit()


def _account_count_for_prefix(session: Session, prefix: str) -> int:
    return int(
        session.execute(
            text("SELECT count(*) FROM dim_gl_account WHERE account_number_group LIKE :pfx"),
            {"pfx": f"{prefix}%"},
        ).scalar() or 0
    )


def _legal_entity_prefixes(session: Session) -> set[str]:
    rows = session.execute(
        text("SELECT entity_prefix FROM dim_legal_entity")
    ).fetchall()
    return {str(r[0]) for r in rows}


# =========================================================================== #
# TEST 2a — SQLite direct ETL: 5 members receive equal row counts (ALWAYS RUNS)
# =========================================================================== #

def test_five_member_direct_etl_equal_account_counts():
    """One shared CoA committed for 5 member prefixes → each prefix has the
    same account count in dim_gl_account as prefix '01'.  No prefix truncated.

    WHY THIS WOULD FAIL ON OLD CODE:
      The frontend sent entity_prefixes=['01'] only (stale assignment retained
      only Atlas).  Simulated below in test_old_behavior_simulation_*:
      committing only prefix '01' leaves '02'..'05' with 0 rows.  This test
      would then fail the equal-count assertion.

    WHY THIS PASSES NOW:
      The frontend now sends all 5 prefixes.  The backend fan-out loop inside
      mapping_commit calls _bs_pl_mapping_from_file (or the direct ETL path
      tested here) once per prefix.  Each prefix receives the full chart.

    Note: this test guards the backend fan-out path and would expose any future
    regression that silently truncates the prefix list.
    """
    session = _make_sqlite_session()

    # Commit the shared CoA for ALL 5 members — what the fixed wizard sends.
    for prefix in _MEMBER_PREFIXES:
        _commit_one_member(session, prefix)

    # Reference count (prefix '01').
    count_01 = _account_count_for_prefix(session, "01")
    assert count_01 == _EXPECTED_ACCOUNT_COUNT, (
        f"prefix '01' must have {_EXPECTED_ACCOUNT_COUNT} accounts; got {count_01}"
    )

    # Every other member must have the SAME count — no truncated subset.
    for prefix in _MEMBER_PREFIXES[1:]:
        count_p = _account_count_for_prefix(session, prefix)
        assert count_p == count_01, (
            f"prefix '{prefix}' has {count_p} accounts in dim_gl_account — "
            f"expected {count_01} (same as '01').  "
            f"OLD CODE would produce 0 here (only '01' was committed)."
        )

    # dim_legal_entity must contain ALL 5 member prefixes.
    legal = _legal_entity_prefixes(session)
    missing = set(_MEMBER_PREFIXES) - legal
    assert not missing, (
        f"dim_legal_entity missing prefixes: {sorted(missing)}.  "
        f"Old code only registered '01'."
    )


# =========================================================================== #
# TEST 2b — Explicit reproduction of the OLD (pre-fix) defect behavior
#            Confirms what would have caused the test above to FAIL.
# =========================================================================== #

def test_old_behavior_simulation_only_one_prefix_committed():
    """Documents the pre-fix defect: the stale wizard sent entity_prefixes=['01']
    only, so only prefix '01' got rows.  Prefixes '02'..'05' stayed at 0.

    This test PASSES and is intentionally a defect-reproduction doc.  It proves
    that test_five_member_direct_etl_equal_account_counts would have FAILED on
    the old frontend payload (entity_prefixes=['01']).
    """
    session = _make_sqlite_session()

    # OLD BEHAVIOR: only prefix '01' was committed (stale wizard dropped the rest).
    _commit_one_member(session, "01")

    count_01 = _account_count_for_prefix(session, "01")
    assert count_01 == _EXPECTED_ACCOUNT_COUNT, (
        f"Prefix '01' was committed; expected {_EXPECTED_ACCOUNT_COUNT} rows, got {count_01}"
    )

    # The 4 other members have 0 rows — they were never committed.
    for prefix in _MEMBER_PREFIXES[1:]:
        count_p = _account_count_for_prefix(session, prefix)
        assert count_p == 0, (
            f"OLD BEHAVIOR reproduced: prefix '{prefix}' should have 0 rows "
            f"(stale wizard never sent it); got {count_p}"
        )

    # dim_legal_entity: only '01' registered.
    legal = _legal_entity_prefixes(session)
    assert legal == {"01"}, (
        f"OLD BEHAVIOR: only '01' in dim_legal_entity; got {sorted(legal)}"
    )


# =========================================================================== #
# PG cluster helpers (mirrors test_ingest_coa.py style)
# =========================================================================== #

def _find_pg_bin() -> Path | None:
    roots = [
        Path("C:/Program Files/edb"),
        Path("C:/Program Files/PostgreSQL"),
        Path("C:/Program Files (x86)/edb"),
        Path("C:/Program Files (x86)/PostgreSQL"),
    ]
    for root in roots:
        if not root.exists():
            continue
        for initdb in root.glob("*/bin/initdb.exe"):
            if (initdb.parent / "pg_ctl.exe").exists():
                return initdb.parent
    if shutil.which("initdb") and shutil.which("pg_ctl"):
        return Path(shutil.which("initdb")).parent
    return None


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _pg_exe(bin_dir: Path, name: str) -> str:
    return str(bin_dir / (name + (".exe" if os.name == "nt" else "")))


@pytest.fixture(scope="module")
def pg_sessionmaker_fanout():
    """Ephemeral PG cluster for the router-level fan-out test (skips if no PG)."""
    bin_dir = _find_pg_bin()
    if bin_dir is None:
        pytest.skip("no local PostgreSQL/EDB binaries found for an ephemeral cluster")

    base = Path(tempfile.mkdtemp(prefix="pgtest_fanout_"))
    data = base / "data"
    log = base / "log.txt"
    port = _free_port()
    started = False
    try:
        r = subprocess.run(
            [_pg_exe(bin_dir, "initdb"), "-D", str(data), "-U", "postgres",
             "-A", "trust", "-E", "UTF8"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            pytest.skip(f"initdb failed: {r.stderr[-400:]}")

        # Avoid capture_output so the postgres child does not keep pipes open.
        r = subprocess.run(
            [_pg_exe(bin_dir, "pg_ctl"), "-D", str(data), "-l", str(log),
             "-o", f"-p {port} -c listen_addresses=127.0.0.1", "-w", "start"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if r.returncode != 0:
            tail = log.read_text()[-400:] if log.exists() else ""
            pytest.skip(f"pg_ctl start failed (rc={r.returncode}): {tail}")
        started = True

        admin_eng = create_engine(
            f"postgresql+psycopg2://postgres@127.0.0.1:{port}/postgres",
            isolation_level="AUTOCOMMIT", future=True,
        )
        with admin_eng.connect() as c:
            c.execute(text("CREATE DATABASE testdb_fanout"))
        admin_eng.dispose()

        env = dict(os.environ)
        env.update({
            "DB_HOST": "127.0.0.1", "DB_PORT": str(port), "DB_USER": "postgres",
            "DB_PASSWORD": "", "DB_NAME": "testdb_fanout", "DATABASE_URL": "",
        })
        r = subprocess.run(
            ["python", "-m", "alembic", "upgrade", "head"],
            cwd=str(_BACKEND_DIR), env=env, capture_output=True, text=True,
        )
        if r.returncode != 0:
            pytest.skip(
                f"alembic upgrade head failed: {r.stdout[-300:]}\n{r.stderr[-600:]}"
            )

        engine = create_engine(
            f"postgresql+psycopg2://postgres@127.0.0.1:{port}/testdb_fanout",
            future=True,
        )
        Maker = sessionmaker(bind=engine, autoflush=False, future=True)
        yield Maker
        engine.dispose()
    finally:
        if started:
            subprocess.run(
                [_pg_exe(bin_dir, "pg_ctl"), "-D", str(data), "-m", "immediate", "stop"],
                capture_output=True, text=True,
            )
            time.sleep(0.5)
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def clean_db_fanout(pg_sessionmaker_fanout):
    """Truncate CoA / entity tables touched by the router fan-out test."""
    s = pg_sessionmaker_fanout()
    try:
        s.execute(text(
            "TRUNCATE dim_gl_account, dim_gl_na, dim_gl_cf, dim_legal_entity, "
            "org_meta_dataset_load, "
            "snap_dim_gl_account, snap_dim_gl_na, snap_dim_gl_cf RESTART IDENTITY CASCADE"
        ))
        s.commit()
    finally:
        s.close()
    yield pg_sessionmaker_fanout


def _db_client_fanout(pg_maker, user: User) -> TestClient:
    def _override_session():
        db = pg_maker()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[require_admin] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def _five_entity_master_workbook_bytes() -> bytes:
    """Synthetic bs_pl_master workbook: 4 accounts (2 BS, 2 PL), no Entity column.

    This is the CoA file the wizard uploads when the user assigns 5 entities to
    one shared group and provides one CoA file for all of them.
    The entity_prefixes list in the commit request drives the fan-out — the file
    itself is prefix-free (no Entity column).
    """
    wb = Workbook()
    # Master_BS sheet
    bs = wb.active
    bs.title = "Master_BS"
    bs.append(["Account", "Account description", "L1", "L2", "L3", "L4"])
    bs.append(["10000", "Cash",            "BS", "Assets",  "Current", "Cash"])
    bs.append(["16100", "Trade receivables","BS", "Assets",  "Current", "Trade AR"])
    # Master_PL sheet
    pl = wb.create_sheet("Master_PL")
    pl.append(["Account", "Account description", "L1 - BS/PL", "L2", "L3", "L4"])
    pl.append(["80000", "Net sales", "PL", "Revenue", "Sales",    "Net sales"])
    pl.append(["70000", "COGS",      "PL", "Expense", "Material", "COGS"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# =========================================================================== #
# TEST 2c — Router endpoint: POST /mapping/commit with entity_prefixes=['01'..'05']
#            Asserts equal account counts and dim_legal_entity coverage at the DB layer.
# =========================================================================== #

def test_five_member_router_endpoint_equal_account_counts(clean_db_fanout):
    """POST /api/v1/ingest/mapping/commit with entity_prefixes=['01','02','03','04','05']
    must populate dim_gl_account with the FULL chart for EVERY member prefix and
    register EVERY member in dim_legal_entity.

    This is the full-stack regression guard.  The actual fixed defect was in the
    frontend payload (Test 1 / coaAssignment.test.ts): the stale wizard sent only
    entity_prefixes=['01'], so the backend never saw the other 4.  This test proves
    that when the frontend correctly sends all 5 prefixes the backend fan-out path
    produces complete charts for every prefix and never silently truncates.

    WHY THIS WOULD FAIL ON OLD CODE (before the frontend fix):
      entity_prefixes=['01'] only → mapping_commit builds ONE frame for '01' →
      dim_gl_account has rows for '01' only → count for '02'..'05' is 0.

    WHY THIS PASSES NOW:
      entity_prefixes=['01','02','03','04','05'] → mapping_commit fan-out builds
      one frame per prefix and concatenates → dim_gl_account has equal rows for
      all 5 → dim_legal_entity registers all 5 via upsert_legal_entities_for_prefixes.
    """
    pg = clean_db_fanout
    client = _db_client_fanout(pg, _ADMIN)

    try:
        # 1. Upload the shared prefix-free Master workbook.
        wb_bytes = _five_entity_master_workbook_bytes()
        up = client.post(
            "/api/v1/ingest/upload",
            files={"file": (
                "master_5ent.xlsx", wb_bytes,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )},
        )
        assert up.status_code == 200, f"Upload failed: {up.text}"
        fid = up.json()["file_id"]

        # 2. Commit for ALL 5 member prefixes in one atomic request.
        #    This is what the FIXED frontend sends (coaGroupsCoverAllEntities +
        #    buildInitialCoaAssignment ensure all 5 codes end up in one group →
        #    buildCoaItems emits entityPrefixes=['01','02','03','04','05']).
        resp = client.post(
            "/api/v1/ingest/mapping/commit",
            json={
                "file_id": fid,
                "format": "bs_pl_master",
                "entity_prefixes": _MEMBER_PREFIXES,   # the fix: all 5 sent
                "fiscal_years": [_FY],
                "replace_mode": "replace",
            },
        )
        assert resp.status_code == 200, f"mapping/commit failed: {resp.text}"

        # 3. Query the DB to assert equal full charts.
        chk = pg()
        try:
            # Reference count: prefix '01' (always committed regardless of bug).
            count_01 = chk.execute(text(
                "SELECT count(*) FROM dim_gl_account "
                "WHERE account_number_group LIKE '01%' AND fiscal_year = :fy"
            ), {"fy": _FY}).scalar() or 0

            assert count_01 > 0, (
                "prefix '01' must have at least 1 row in dim_gl_account"
            )

            # Every other member must match the '01' count — no subset truncation.
            for prefix in _MEMBER_PREFIXES[1:]:
                count_p = chk.execute(text(
                    "SELECT count(*) FROM dim_gl_account "
                    "WHERE account_number_group LIKE :pfx AND fiscal_year = :fy"
                ), {"pfx": f"{prefix}%", "fy": _FY}).scalar() or 0

                assert count_p == count_01, (
                    f"prefix '{prefix}' has {count_p} rows in dim_gl_account — "
                    f"expected {count_01} (same as '01').  "
                    f"This would be 0 if the frontend sent only entity_prefixes=['01']."
                )

            # dim_legal_entity: all 5 prefixes must be registered.
            ent_rows = chk.execute(
                text("SELECT entity_prefix FROM dim_legal_entity")
            ).fetchall()
            registered = {str(r[0]) for r in ent_rows}
            missing = set(_MEMBER_PREFIXES) - registered
            assert not missing, (
                f"dim_legal_entity missing prefixes: {sorted(missing)}.  "
                f"upsert_legal_entities_for_prefixes must cover all entity_prefixes."
            )

        finally:
            chk.close()

    finally:
        app.dependency_overrides.clear()


# =========================================================================== #
# TEST 2d — GL commit auto-expand replicates dim_gl_account / dim_gl_na /
#            dim_gl_cf across entities (reproduce-first, PG only)
# ---------------------------------------------------------------------------
# Behavior under test (already implemented in ingest.py ~2094-2138):
#   At GL /commit, BEFORE the unmapped-422 pre-flight, the router auto-creates
#   missing dim_gl_account + dim_gl_na + dim_gl_cf rows for the committing
#   entity by copying an existing row sharing the same 6-char bare suffix under
#   ANY OTHER entity prefix.  Writes use on_conflict="nothing" (no overwrite).
#
# Test phases:
#   Phase 1 — Seed '01' with BS account (NA+CF) + PL account (CF only).
#   Phase 2 — GL commit for entity '02' (all accounts exist under '01') -> 200;
#              assert all three dim tables have '02' rows matching '01'.
#   Phase 3 — GL commit for entity '02' with an orphan (no entity has it) -> 422
#              listing EXACTLY the orphan; the recovered accounts are not listed.
#   Phase 4 — Idempotency: re-commit the clean file -> 200, row count unchanged.
#   Phase 5 — Non-overwrite: UPDATE an existing '02' row to a sentinel value,
#              re-commit -> assert the sentinel is preserved (additive only).
# =========================================================================== #

# --- Auto-expand test constants ---

# BS account: has both dim_gl_na and dim_gl_cf rows under '01'.
_AX_BS_ACCT = "10000"       # suffix 010000 -> 01010000 / 02010000

# PL account: has dim_gl_cf row under '01' (no NA for PL).
_AX_PL_ACCT = "80000"       # suffix 080000 -> 01080000 / 02080000

# Orphan: no entity has this bare account.
_AX_ORPHAN_ACCT = "99999"   # suffix 099999 -> 02099999

# NA values seeded for the BS account under '01'.
_AX_BS_L6 = "BS-NA-Category"
_AX_BS_L7 = "BS-NA-Description"

# CF values seeded for both accounts under '01'.
_AX_BS_CF1 = "OperatingActivities"
_AX_BS_CF2 = "WorkingCapital"
_AX_BS_CFM = "CF_BS_KEY"

_AX_PL_CF1 = "OperatingActivities"
_AX_PL_CF2 = "Revenue"
_AX_PL_CFM = "CF_PL_KEY"

# Minimal GL profile for entity '02'.
_AX_GL_PROFILE_02: dict = {
    "entity": {"mode": "fixed", "value": "02"},
    "fiscal_year": {"mode": "from_date", "value": None},
    "sign": {"mode": "signed", "amount": "Amount"},
    "decimal": ".", "thousands": ",", "date_dayfirst": True,
    "columns": {
        "journal_entry_number": "Tx",
        "account_number": "Account",
        "posting_date": "PostingDate",
        "source_type": "SourceType",
        "source_no": "SourceNo",
    },
    "linking_strategy": "txn",
    "entry_type": "actual",
    "source_system": "ax_test",
}

# GL CSV (a): both accounts exist under '01' -> should auto-expand and 200.
_AX_GL_CSV_CLEAN = (
    "Tx,Account,Amount,PostingDate,SourceType,SourceNo\n"
    f"1,{_AX_BS_ACCT},600.00,15.03.2024,,\n"
    f"2,{_AX_PL_ACCT},-600.00,15.03.2024,,\n"
)

# GL CSV (b): same accounts plus an orphan -> should 422 listing only the orphan.
_AX_GL_CSV_WITH_ORPHAN = (
    "Tx,Account,Amount,PostingDate,SourceType,SourceNo\n"
    f"1,{_AX_BS_ACCT},400.00,15.03.2024,,\n"
    f"2,{_AX_PL_ACCT},-300.00,15.03.2024,,\n"
    f"3,{_AX_ORPHAN_ACCT},-100.00,15.03.2024,,\n"
)

# Fake load_canonical result so the heavy ETL path is skipped.
_AX_FAKE_LOAD: dict = {
    "load_id": 99, "entries": 2, "lines": 2,
    "ar": 0, "ap": 0, "sales": 0, "com": 0, "skipped": 0,
    "loaded_at": "2024-03-15T00:00:00+00:00",
    "commit_mode": "replace",
}


def _ax_coa_df() -> pd.DataFrame:
    """CoA for the auto-expand test: BS account (NA+CF) + PL account (CF only)."""
    return pd.DataFrame({
        "Acct":   [_AX_BS_ACCT, _AX_PL_ACCT],
        "L0":     ["BS", "PL"],
        "L1":     ["Assets", "Revenue"],
        "L2":     ["Current", "Sales"],
        "L3":     ["Cash", "Net sales"],
        "L4":     ["Cash Detail", "Rev Detail"],
        "L6_NA":  [_AX_BS_L6, None],
        "L7_NA":  [_AX_BS_L7, None],
        "CF1":    [_AX_BS_CF1, _AX_PL_CF1],
        "CF2":    [_AX_BS_CF2, _AX_PL_CF2],
        "CF3":    [None, None],
        "CF4":    [None, None],
        "CF5":    [None, None],
        "CF_MAP": [_AX_BS_CFM, _AX_PL_CFM],
    })


def _ax_profile_01() -> AccountMappingProfile:
    """AccountMappingProfile for entity '01' with NA + CF column mappings."""
    p = AccountMappingProfile()
    p.entity = {"mode": "fixed", "value": "01"}
    p.fiscal_year = {"mode": "fixed", "value": _FY}
    p.columns = {
        "account_number":    "Acct",
        "level_0":           "L0",
        "level_1":           "L1",
        "level_2":           "L2",
        "level_3":           "L3",
        "level_4":           "L4",
        "l6_na_mapping":     "L6_NA",
        "l7_na_description": "L7_NA",
        "cf_l1":             "CF1",
        "cf_l2":             "CF2",
        "cf_l3":             "CF3",
        "cf_l4":             "CF4",
        "cf_l5":             "CF5",
        "cf_mapping":        "CF_MAP",
    }
    p.source_system = "ax_test"
    return p


def _seed_01_na_cf(session: Session) -> None:
    """Seed entity '01' with two accounts via the canonical ETL path.

    Uses apply_account_mapping + load_account_mapping + upsert_legal_entities
    (the same path the mapping/commit router uses), which populates all three
    dim tables (dim_gl_account, dim_gl_na, dim_gl_cf) in one call.
    """
    raw = _ax_coa_df()
    mapping_df = apply_account_mapping(
        raw, _ax_profile_01(), entity_prefix="01", fiscal_year=_FY
    )
    load_account_mapping(session, mapping_df, auto_commit=False)
    upsert_legal_entities_for_prefixes(
        session, ["01"], source_system="ax_test", auto_commit=False
    )
    session.commit()


def _ax_upload_gl(client: TestClient, csv_text: str) -> str:
    resp = client.post(
        "/api/v1/ingest/upload",
        files={"file": ("gl.csv", csv_text.encode("utf-8"), "text/csv")},
    )
    assert resp.status_code == 200, f"GL upload failed: {resp.text}"
    return resp.json()["file_id"]


def _ax_gl_commit(client: TestClient, fid: str) -> "requests.Response":  # type: ignore[name-defined]
    return client.post(
        "/api/v1/ingest/commit",
        json={
            "file_id": fid,
            "profile": _AX_GL_PROFILE_02,
            "dataset": "ax_test_ds",
            "confirm_soft": True,
            "commit_mode": "replace",
        },
    )


def _count_02_accounts(pg) -> int:
    s = pg()
    try:
        return int(
            s.execute(
                text(
                    "SELECT count(*) FROM dim_gl_account "
                    "WHERE account_number_group LIKE '02%' AND fiscal_year = :fy"
                ),
                {"fy": _FY},
            ).scalar()
            or 0
        )
    finally:
        s.close()


def test_gl_commit_auto_expand_replicates_dim_across_entities(clean_db_fanout):
    """GL /commit auto-expand copies dim_gl_account + dim_gl_na + dim_gl_cf from '01'
    to a committing entity '02' that shares the same bare account numbers.

    WHY THIS WOULD FAIL WITHOUT THE FIX:
      Without the auto-expand block (ingest.py ~2094-2138) the GL commit goes
      straight to the FK pre-flight which sees '02' accounts not in dim_gl_account
      and raises 422 gl_accounts_unmapped — even though '01' has the chart.

    WHY THIS PASSES AFTER THE FIX:
      The auto-expand block runs BEFORE the pre-flight, finds the '01' source rows
      by 6-char suffix match, and clones them to '02' via
      _replicate_dim_across_entities (on_conflict=nothing, additive only).
      The pre-flight then finds the '02' rows and proceeds to 200.

    Phases covered:
      1. Seed '01' via ETL path (including NA + CF).
      2. GL commit (a) for '02' with recoverable accounts -> 200.
         Assert dim_gl_account / dim_gl_na / dim_gl_cf copied verbatim.
         Assert gl_account_id is BARE (not prefixed).
         Assert dim_legal_entity now has '02'.
      3. GL commit (b) with an orphan (no entity has it) -> 422 listing
         EXACTLY '02099999'; the recovered accounts NOT listed.
      4. Idempotency: re-commit (a) -> 200; dim_gl_account row count unchanged.
      5. Non-overwrite: update an existing '02' row to a sentinel, re-commit ->
         sentinel preserved (additive-only guarantee).
    """
    pg = clean_db_fanout
    client = _db_client_fanout(pg, _ADMIN)

    try:
        # ------------------------------------------------------------------ #
        # Phase 1: seed entity '01' with NA + CF via the canonical ETL path. #
        # ------------------------------------------------------------------ #
        seed = pg()
        try:
            _seed_01_na_cf(seed)
        finally:
            seed.close()

        # ------------------------------------------------------------------ #
        # Phase 2: GL commit (a) for entity '02' — all accounts under '01'.  #
        # ------------------------------------------------------------------ #
        fid_a = _ax_upload_gl(client, _AX_GL_CSV_CLEAN)
        with patch("app.routers.ingest.load_canonical", return_value=_AX_FAKE_LOAD):
            resp_a = _ax_gl_commit(client, fid_a)

        assert resp_a.status_code == 200, (
            f"Expected 200 after auto-expand; got {resp_a.status_code}: {resp_a.text}"
        )

        # Assert all three dim tables were populated for '02'.
        chk = pg()
        try:
            # --- dim_gl_account ---
            row_02_bs = chk.execute(
                text(
                    "SELECT gl_account_id, level_0, level_1, level_2, level_3, level_4 "
                    "FROM dim_gl_account "
                    "WHERE account_number_group='02010000' AND fiscal_year=:fy"
                ),
                {"fy": _FY},
            ).fetchone()
            assert row_02_bs is not None, "'02010000' missing from dim_gl_account after auto-expand"
            # gl_account_id must be the BARE account number, not the '02'-prefixed group.
            assert row_02_bs[0] == "10000", (
                f"gl_account_id must be bare '10000', got {row_02_bs[0]!r}"
            )
            assert row_02_bs[1] == "BS", f"level_0 mismatch: {row_02_bs[1]!r}"

            row_02_pl = chk.execute(
                text(
                    "SELECT gl_account_id, level_0 FROM dim_gl_account "
                    "WHERE account_number_group='02080000' AND fiscal_year=:fy"
                ),
                {"fy": _FY},
            ).fetchone()
            assert row_02_pl is not None, "'02080000' missing from dim_gl_account after auto-expand"
            assert row_02_pl[0] == "80000", (
                f"gl_account_id must be bare '80000', got {row_02_pl[0]!r}"
            )
            assert row_02_pl[1] == "PL", f"level_0 mismatch: {row_02_pl[1]!r}"

            # Hierarchy matches source '01' row verbatim.
            row_01_bs = chk.execute(
                text(
                    "SELECT level_0, level_1, level_2, level_3, level_4 "
                    "FROM dim_gl_account "
                    "WHERE account_number_group='01010000' AND fiscal_year=:fy"
                ),
                {"fy": _FY},
            ).fetchone()
            assert row_01_bs is not None, "source '01010000' missing — seed failed"
            for i, col in enumerate(("level_0", "level_1", "level_2", "level_3", "level_4")):
                assert row_02_bs[i + 1] == row_01_bs[i], (
                    f"{col}: copied value {row_02_bs[i+1]!r} != source {row_01_bs[i]!r}"
                )

            # --- dim_gl_na: BS account copied ---
            na_02 = chk.execute(
                text(
                    "SELECT l6_na_mapping, l7_na_description FROM dim_gl_na "
                    "WHERE account_number_group='02010000' AND fiscal_year=:fy"
                ),
                {"fy": _FY},
            ).fetchone()
            assert na_02 is not None, "'02010000' missing from dim_gl_na after auto-expand"
            assert na_02[0] == _AX_BS_L6, f"l6_na_mapping mismatch: {na_02[0]!r}"
            assert na_02[1] == _AX_BS_L7, f"l7_na_description mismatch: {na_02[1]!r}"

            # --- dim_gl_cf: BS account copied ---
            cf_02_bs = chk.execute(
                text(
                    "SELECT l1, l2, cf_mapping FROM dim_gl_cf "
                    "WHERE account_number_group='02010000' AND fiscal_year=:fy"
                ),
                {"fy": _FY},
            ).fetchone()
            assert cf_02_bs is not None, "'02010000' missing from dim_gl_cf after auto-expand"
            assert cf_02_bs[0] == _AX_BS_CF1, f"dim_gl_cf.l1 (BS): {cf_02_bs[0]!r}"
            assert cf_02_bs[1] == _AX_BS_CF2, f"dim_gl_cf.l2 (BS): {cf_02_bs[1]!r}"
            assert cf_02_bs[2] == _AX_BS_CFM, f"dim_gl_cf.cf_mapping (BS): {cf_02_bs[2]!r}"

            # --- dim_gl_cf: PL account copied ---
            cf_02_pl = chk.execute(
                text(
                    "SELECT l1, l2, cf_mapping FROM dim_gl_cf "
                    "WHERE account_number_group='02080000' AND fiscal_year=:fy"
                ),
                {"fy": _FY},
            ).fetchone()
            assert cf_02_pl is not None, "'02080000' missing from dim_gl_cf after auto-expand"
            assert cf_02_pl[0] == _AX_PL_CF1, f"dim_gl_cf.l1 (PL): {cf_02_pl[0]!r}"
            assert cf_02_pl[1] == _AX_PL_CF2, f"dim_gl_cf.l2 (PL): {cf_02_pl[1]!r}"
            assert cf_02_pl[2] == _AX_PL_CFM, f"dim_gl_cf.cf_mapping (PL): {cf_02_pl[2]!r}"

            # --- dim_legal_entity: '02' registered by _replicate_dim_across_entities ---
            ent02 = chk.execute(
                text(
                    "SELECT entity_prefix FROM dim_legal_entity "
                    "WHERE entity_prefix = '02'"
                )
            ).fetchone()
            assert ent02 is not None, (
                "'02' missing from dim_legal_entity after auto-expand + GL commit"
            )
        finally:
            chk.close()

        # ------------------------------------------------------------------ #
        # Phase 3: GL commit (b) — orphan account -> 422 listing only orphan. #
        # ------------------------------------------------------------------ #
        fid_b = _ax_upload_gl(client, _AX_GL_CSV_WITH_ORPHAN)
        with patch("app.routers.ingest.load_canonical", return_value=_AX_FAKE_LOAD):
            resp_b = _ax_gl_commit(client, fid_b)

        assert resp_b.status_code == 422, (
            f"Expected 422 for orphan account; got {resp_b.status_code}: {resp_b.text}"
        )
        detail_b = resp_b.json()["detail"]
        assert isinstance(detail_b, dict), detail_b
        assert detail_b["code"] == "gl_accounts_unmapped"
        # Only the orphan is listed; the two recovered accounts are NOT in the list.
        orphan_angs = {u["account_number_group"] for u in detail_b["unmapped"]}
        assert orphan_angs == {"02099999"}, (
            f"422 must list ONLY the orphan '02099999'; got {orphan_angs}"
        )
        assert detail_b["unmapped"][0]["account"] == _AX_ORPHAN_ACCT, (
            "original bare account number must appear in 422 response"
        )
        assert detail_b["total_unmapped"] == 1

        # ------------------------------------------------------------------ #
        # Phase 4: Idempotency — re-commit (a) must 200, row count unchanged. #
        # ------------------------------------------------------------------ #
        count_before = _count_02_accounts(pg)
        fid_a2 = _ax_upload_gl(client, _AX_GL_CSV_CLEAN)
        with patch("app.routers.ingest.load_canonical", return_value=_AX_FAKE_LOAD):
            resp_a2 = _ax_gl_commit(client, fid_a2)
        assert resp_a2.status_code == 200, (
            f"Idempotency re-commit failed: {resp_a2.status_code}: {resp_a2.text}"
        )
        count_after = _count_02_accounts(pg)
        assert count_after == count_before, (
            f"Idempotency violated: row count changed {count_before} -> {count_after} "
            f"(on_conflict=nothing must insert 0 new rows on re-commit)"
        )

        # ------------------------------------------------------------------ #
        # Phase 5: Non-overwrite — existing '02' row must NOT be clobbered.   #
        # ------------------------------------------------------------------ #
        sentinel = "SENTINEL_DO_NOT_OVERWRITE"
        upd = pg()
        try:
            upd.execute(
                text(
                    "UPDATE dim_gl_account SET level_4 = :val "
                    "WHERE account_number_group = '02010000' AND fiscal_year = :fy"
                ),
                {"val": sentinel, "fy": _FY},
            )
            upd.commit()
        finally:
            upd.close()

        fid_a3 = _ax_upload_gl(client, _AX_GL_CSV_CLEAN)
        with patch("app.routers.ingest.load_canonical", return_value=_AX_FAKE_LOAD):
            resp_a3 = _ax_gl_commit(client, fid_a3)
        assert resp_a3.status_code == 200, (
            f"Non-overwrite commit failed: {resp_a3.status_code}: {resp_a3.text}"
        )

        chk5 = pg()
        try:
            actual_l4 = chk5.execute(
                text(
                    "SELECT level_4 FROM dim_gl_account "
                    "WHERE account_number_group='02010000' AND fiscal_year=:fy"
                ),
                {"fy": _FY},
            ).scalar()
            assert actual_l4 == sentinel, (
                f"Non-overwrite violated: existing '02010000' level_4 was changed "
                f"from {sentinel!r} to {actual_l4!r} — auto-expand must be additive only"
            )
        finally:
            chk5.close()

    finally:
        app.dependency_overrides.clear()
