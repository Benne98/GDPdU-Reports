"""CoA mapping commit endpoint tests (new wizard: entity_prefix / statement params).

Covers:
  A. 422 validation — no DB access needed (returned before any write).
  B. entity_prefix path — skips two-sheet gate + Entity col; account_number_group
     is prefixed correctly (01xxxx vs 02xxxx for disjoint sets).
  C. Legacy Entity-column path — two-sheet file with Entity='Atlas' still works.
  D. apply_account_mapping unit test: fiscal_year={mode:'fixed',value:''} with no
     override raises ValueError — the function-level contract; still correct.
  E. Router-level (generic + empty profile fiscal_year + non-empty body.fiscal_years):
     the router overrides the profile's empty value, replicates the mapping across
     each requested year, and returns 200. The ValueError is NOT triggered because
     body.fiscal_years provides an explicit per-year override that short-circuits
     the empty fixed value in the profile.

No DB required: load_account_mapping is patched; _MockSession returns empty sets.
"""
from __future__ import annotations

import io
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.auth import User, current_user, require_admin
from app.db import get_session
from app.main import app
from app.routers.ingest import UPLOAD_DIR

_ADMIN = User(user_id=1, email="admin@test", display_name="Admin", is_admin=True)


# =========================================================================== #
# Fixtures / helpers
# =========================================================================== #

@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


class _MockSession:
    """Session stub for mapping commit tests.

    * SELECTs on dim_gl_account / account_number_group → empty (all rows new).
    * org_meta_dataset_load INSERT → fetchone() returns None → load_id stays
      None → snapshot step is skipped entirely.
    * dim_legal_entity → configurable via entity_rows (for legacy Entity path).
    """

    def __init__(self, entity_rows=None):
        self._entity_rows = entity_rows or []

    def execute(self, stmt, params=None):
        sql = str(stmt)

        class _R:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows

            def fetchone(self):
                return self._rows[0] if self._rows else None

        if "org_meta_dataset_load" in sql:
            return _R([])            # fetchone() → None → load_id stays None
        if "dim_legal_entity" in sql:
            return _R(self._entity_rows)
        return _R([])                # all other SELECTs → empty

    def rollback(self):
        pass

    def commit(self):
        pass


def _client_as_admin(session) -> TestClient:
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[current_user] = lambda: _ADMIN
    app.dependency_overrides[require_admin] = lambda: _ADMIN
    return TestClient(app, raise_server_exceptions=False)


def _write_to_upload_dir(xlsx_bytes: bytes, filename: str) -> tuple[str, Path]:
    """Write xlsx_bytes to UPLOAD_DIR with a deterministic test prefix; return (file_id, path)."""
    file_id = "tstcoa" + uuid.uuid4().hex[:8]
    dest = UPLOAD_DIR / f"{file_id}_{filename}"
    dest.write_bytes(xlsx_bytes)
    return file_id, dest


# =========================================================================== #
# Synthetic workbooks
# =========================================================================== #

def _xlsx_bs_only() -> bytes:
    """Single-sheet workbook: only Master_BS, no Entity column (new wizard output)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Master_BS"
    ws.append(["Account", "Account description", "L1", "L2", "L3", "L4"])
    ws.append(["16100", "Trade receivables", "BS", "Assets", "Current", "Trade AR"])
    ws.append(["18000", "Bank", "BS", "Assets", "Current", "Cash"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _xlsx_two_sheet_no_entity() -> bytes:
    """Two-sheet workbook with NO Entity column (new wizard upload, prefix omitted)."""
    wb = Workbook()
    ws_bs = wb.active
    ws_bs.title = "Master_BS"
    ws_bs.append(["Account", "Account description", "L1", "L2", "L3", "L4"])
    ws_bs.append(["16100", "Trade receivables", "BS", "Assets", "Current", "Trade AR"])
    ws_pl = wb.create_sheet("Master_PL")
    ws_pl.append(["Account", "Account description", "L1 - BS/PL", "L2", "L3", "L4"])
    ws_pl.append(["81910", "Net sales", "PL", "Revenue", "Sales", "Net sales"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _xlsx_two_sheet_with_entity() -> bytes:
    """Classic two-sheet workbook WITH Entity column (legacy path)."""
    wb = Workbook()
    ws_bs = wb.active
    ws_bs.title = "Master_BS"
    ws_bs.append(["Entity", "Account", "Account description", "L1", "L2", "L3", "L4"])
    ws_bs.append(["Atlas", "16100", "Trade receivables", "BS", "Assets", "Current", "Trade AR"])
    ws_pl = wb.create_sheet("Master_PL")
    ws_pl.append(["Entity", "Account", "Account description", "L1 - BS/PL", "L2", "L3", "L4"])
    ws_pl.append(["Atlas", "81910", "Net sales", "PL", "Revenue", "Sales", "Net sales"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _xlsx_generic_coa() -> bytes:
    """Single-sheet workbook for the generic format mapping path.

    Columns (Acct, L0, L1, L2, L3) match the profile.columns mapping used by
    tests in section E.  One data row is enough: the replication-across-years
    assertion only needs the row count per year, not specific account values.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Acct", "L0", "L1", "L2", "L3"])
    ws.append(["16100", "BS", "Assets", "Current", "Trade AR"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# =========================================================================== #
# A) 422 validation
# =========================================================================== #

class TestMappingCommit422:
    """Router rejects uploads that have neither Entity column nor entity_prefix."""

    def test_422_two_sheet_no_entity_no_prefix(self):
        """Two Master sheets present but no Entity col and no entity_prefix → 422."""
        session = _MockSession()
        client = _client_as_admin(session)
        file_id, path = _write_to_upload_dir(
            _xlsx_two_sheet_no_entity(), "no_entity.xlsx"
        )
        try:
            resp = client.post(
                "/api/v1/ingest/mapping/commit",
                json={
                    "file_id": file_id,
                    "format": "bs_pl_master",
                    "fiscal_years": [2024],
                    # entity_prefix intentionally omitted → legacy gate triggers
                },
            )
            assert resp.status_code == 422, resp.text
            detail = resp.json()["detail"]
            assert "entity_prefix" in detail or "Entity" in detail
        finally:
            path.unlink(missing_ok=True)

    def test_422_single_sheet_bs_no_entity_no_prefix(self):
        """BS-only file with no entity_prefix → legacy gate → missing Master_PL → 422."""
        session = _MockSession()
        client = _client_as_admin(session)
        file_id, path = _write_to_upload_dir(_xlsx_bs_only(), "bs_only_no_pfx.xlsx")
        try:
            resp = client.post(
                "/api/v1/ingest/mapping/commit",
                json={
                    "file_id": file_id,
                    "format": "bs_pl_master",
                    "fiscal_years": [2024],
                },
            )
            assert resp.status_code == 422, resp.text
        finally:
            path.unlink(missing_ok=True)


# =========================================================================== #
# B) entity_prefix path (new wizard)
# =========================================================================== #

class TestMappingCommitEntityPrefix:
    """entity_prefix skips two-sheet gate and Entity-column resolution."""

    @patch("app.routers.ingest.load_account_mapping")
    def test_commit_bs_entity_prefix_01_succeeds(self, mock_load):
        """entity_prefix='01', statement='bs' → 200; account_number_group starts '01'."""
        mock_load.return_value = {"accounts": 2, "na": 0, "cf": 0}
        session = _MockSession()
        client = _client_as_admin(session)
        file_id, path = _write_to_upload_dir(_xlsx_bs_only(), "bs_ep01.xlsx")
        try:
            resp = client.post(
                "/api/v1/ingest/mapping/commit",
                json={
                    "file_id": file_id,
                    "format": "bs_pl_master",
                    "fiscal_years": [2024],
                    "entity_prefix": "01",
                    "statement": "bs",
                },
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["accounts"] == 2

            # Verify the DataFrame handed to load_account_mapping uses prefix '01'.
            assert mock_load.called
            mapping_df = mock_load.call_args[0][1]
            assert all(
                str(v).startswith("01")
                for v in mapping_df["account_number_group"]
            )
        finally:
            path.unlink(missing_ok=True)

    @patch("app.routers.ingest.load_account_mapping")
    def test_commit_disjoint_keys_for_different_prefixes(self, mock_load):
        """entity_prefix='01' and entity_prefix='02' on same accounts → disjoint keys.

        The same file committed under two different entity prefixes must produce
        non-overlapping account_number_group sets — each prefix occupies its own
        namespace in dim_gl_account.
        """
        mock_load.return_value = {"accounts": 2, "na": 0, "cf": 0}
        session = _MockSession()
        file_id, path = _write_to_upload_dir(_xlsx_bs_only(), "bs_disjoint.xlsx")
        try:
            # Commit with entity_prefix='01'
            client = _client_as_admin(session)
            resp1 = client.post(
                "/api/v1/ingest/mapping/commit",
                json={
                    "file_id": file_id,
                    "format": "bs_pl_master",
                    "fiscal_years": [2024],
                    "entity_prefix": "01",
                    "statement": "bs",
                },
            )
            assert resp1.status_code == 200, resp1.text
            keys1 = set(
                mock_load.call_args[0][1]["account_number_group"].astype(str)
            )

            mock_load.reset_mock()
            mock_load.return_value = {"accounts": 2, "na": 0, "cf": 0}

            # Commit with entity_prefix='02'
            client2 = _client_as_admin(session)
            resp2 = client2.post(
                "/api/v1/ingest/mapping/commit",
                json={
                    "file_id": file_id,
                    "format": "bs_pl_master",
                    "fiscal_years": [2024],
                    "entity_prefix": "02",
                    "statement": "bs",
                },
            )
            assert resp2.status_code == 200, resp2.text
            keys2 = set(
                mock_load.call_args[0][1]["account_number_group"].astype(str)
            )

            assert keys1.isdisjoint(keys2), (
                f"Expected disjoint key sets but got overlap: {keys1 & keys2}"
            )
            assert all(k.startswith("01") for k in keys1)
            assert all(k.startswith("02") for k in keys2)
        finally:
            path.unlink(missing_ok=True)


# =========================================================================== #
# C) Legacy Entity-column path (backward compat)
# =========================================================================== #

class TestMappingCommitLegacyEntityColumn:
    """Classic two-sheet + Entity column path still resolves prefix via dim_legal_entity."""

    @patch("app.routers.ingest.load_account_mapping")
    def test_legacy_entity_column_two_sheet(self, mock_load):
        """Entity='Atlas' resolves to '01' via dim_legal_entity → account_number_group '01xxxx'."""
        mock_load.return_value = {"accounts": 2, "na": 0, "cf": 0}
        # Session returns Atlas → '01' for build_entity_lookup.
        session = _MockSession(entity_rows=[("Atlas", "Atlas GmbH", "01")])
        client = _client_as_admin(session)
        file_id, path = _write_to_upload_dir(
            _xlsx_two_sheet_with_entity(), "legacy_entity.xlsx"
        )
        try:
            resp = client.post(
                "/api/v1/ingest/mapping/commit",
                json={
                    "file_id": file_id,
                    "format": "bs_pl_master",
                    "fiscal_years": [2024],
                    # No entity_prefix: uses Entity column + dim_legal_entity lookup.
                },
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["accounts"] == 2

            mapping_df = mock_load.call_args[0][1]
            assert all(
                str(v).startswith("01")
                for v in mapping_df["account_number_group"]
            )
        finally:
            path.unlink(missing_ok=True)


# =========================================================================== #
# E) Router-level: generic format + empty profile fiscal_year + body.fiscal_years
# =========================================================================== #

class TestMappingCommitGenericFiscalYears:
    """POST /mapping/commit with format='generic' and a non-empty body.fiscal_years.

    When the profile carries fiscal_year={'mode':'fixed','value':''} (the shape
    the CoA wizard sends) but the request body supplies fiscal_years=[2023, 2024],
    the router must:
      - NOT call apply_account_mapping with the bare empty value (which would
        raise ValueError at function level),
      - instead replicate the mapping across each requested year (mirroring the
        bs_pl_master path),
      - return a 2xx response,
      - pass a mapping_df to load_account_mapping whose fiscal_year column
        contains rows for BOTH requested years.
    """

    @patch("app.routers.ingest.load_account_mapping")
    def test_generic_empty_fiscal_year_overridden_by_body_fiscal_years(self, mock_load):
        """format='generic', profile fiscal_year='', fiscal_years=[2023,2024] → 200 + rows per year."""
        mock_load.return_value = {"accounts": 1, "na": 0, "cf": 0}
        session = _MockSession()
        client = _client_as_admin(session)
        file_id, path = _write_to_upload_dir(_xlsx_generic_coa(), "generic_coa.xlsx")
        try:
            resp = client.post(
                "/api/v1/ingest/mapping/commit",
                json={
                    "file_id": file_id,
                    "format": "generic",
                    "fiscal_years": [2023, 2024],
                    "profile": {
                        "entity": {"mode": "fixed", "value": "01"},
                        # Empty fixed value — the exact shape the frontend sends for a
                        # generic CoA profile when fiscal_years is passed separately.
                        "fiscal_year": {"mode": "fixed", "value": ""},
                        "columns": {
                            "account_number": "Acct",
                            "level_0": "L0",
                            "level_1": "L1",
                            "level_2": "L2",
                            "level_3": "L3",
                        },
                    },
                },
            )
            assert resp.status_code == 200, (
                f"Expected 200 but got {resp.status_code}: {resp.text}"
            )

            # The mapping passed to load_account_mapping must contain one row per
            # source row × per requested fiscal year (1 source row × 2 years = 2 rows).
            assert mock_load.called, "load_account_mapping should have been called"
            mapping_df = mock_load.call_args[0][1]

            years_in_df = sorted(
                mapping_df["fiscal_year"].dropna().astype(int).unique().tolist()
            )
            assert years_in_df == [2023, 2024], (
                f"Expected rows for fiscal years [2023, 2024]; got {years_in_df}"
            )

            # Each year block must carry the same row count as the source (1 row each).
            counts_per_year = mapping_df.groupby(
                mapping_df["fiscal_year"].astype(int)
            ).size().to_dict()
            assert counts_per_year == {2023: 1, 2024: 1}, (
                f"Expected 1 row per year; got {counts_per_year}"
            )
        finally:
            path.unlink(missing_ok=True)


# =========================================================================== #
# F) Router-level: generic + empty profile fiscal_year + NO body.fiscal_years
#    must return a FRIENDLY 422 (no raw Python text).
# =========================================================================== #

@patch("app.routers.ingest.load_account_mapping")
def test_generic_missing_fiscal_years_returns_friendly_message(mock_load):
    """format='generic', profile fiscal_year='', NO fiscal_years → friendly 422.

    This reproduces what the wizard sends when the user forgets to pick a fiscal
    year: a generic profile with fiscal_year={'mode':'fixed','value':''} and no
    body.fiscal_years. The raw int('') ValueError must NOT leak to the user;
    the detail must be plain natural-language English.
    """
    mock_load.return_value = {"accounts": 1, "na": 0, "cf": 0}
    session = _MockSession()
    client = _client_as_admin(session)
    file_id, path = _write_to_upload_dir(_xlsx_generic_coa(), "generic_no_fy.xlsx")
    try:
        resp = client.post(
            "/api/v1/ingest/mapping/commit",
            json={
                "file_id": file_id,
                "format": "generic",
                # fiscal_years intentionally omitted → blank profile value is used.
                "profile": {
                    "entity": {"mode": "fixed", "value": "01"},
                    "fiscal_year": {"mode": "fixed", "value": ""},
                    "columns": {
                        "account_number": "Acct",
                        "level_0": "L0",
                        "level_1": "L1",
                        "level_2": "L2",
                        "level_3": "L3",
                    },
                },
            },
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert isinstance(detail, str), detail
        lower = detail.lower()
        # Must not leak raw Python / technical text.
        for leak in ("invalid literal", "int(", "base 10", "traceback"):
            assert leak not in lower, f"raw technical text leaked: {detail!r}"
        # Should give plain guidance mentioning a fiscal year.
        assert "fiscal year" in lower, f"expected fiscal-year guidance, got: {detail!r}"
    finally:
        path.unlink(missing_ok=True)


# =========================================================================== #
# D) apply_account_mapping unit test: empty fixed fiscal_year raises ValueError
# =========================================================================== #

def test_empty_fixed_fiscal_year_in_generic_profile_raises_value_error():
    """AccountMappingProfile fiscal_year={mode:'fixed',value:''} → ValueError.

    The router catches (KeyError, ValueError) and returns 422 — not 500.
    This test confirms the underlying ValueError is raised cleanly by
    apply_account_mapping so the router path is correct.

    Risk: the frontend sends this profile shape for generic CoA when fiscal_years
    is passed separately. The bs_pl_master path is SAFE (fiscal_year is always
    supplied as an explicit override that short-circuits the profile value). Only
    the generic format path is at risk, and it correctly surfaces a 422.
    """
    import pandas as pd
    from etl.mapping_account import AccountMappingProfile, apply_account_mapping

    raw = pd.DataFrame({
        "Acct": ["16100"],
        "L0": ["BS"],
        "L1": ["Assets"],
        "L2": ["Current"],
        "L3": ["Trade AR"],
    })
    prof = AccountMappingProfile(
        entity={"mode": "fixed", "value": "01"},
        fiscal_year={"mode": "fixed", "value": ""},   # empty string — the flagged shape
        columns={
            "account_number": "Acct",
            "level_0": "L0",
            "level_1": "L1",
            "level_2": "L2",
            "level_3": "L3",
        },
    )
    # No fiscal_year override → profile's empty value is used → int("") raises ValueError.
    # The router catches this and returns 422, NOT 500.
    with pytest.raises(ValueError, match="invalid literal"):
        apply_account_mapping(raw, prof)


# =========================================================================== #
# G + H) DB-LAYER tests against a throwaway PostgreSQL cluster
# ---------------------------------------------------------------------------
# Helper-only unit tests passed before while the real flow stayed broken, so the
# two flows below (atomic multi-prefix commit; cross-entity replicate) are driven
# end-to-end through the REAL endpoints and asserted at the DB layer.
#
# An ephemeral PostgreSQL cluster is built from the locally-installed PG/EDB
# binaries (initdb + pg_ctl, trust auth, ephemeral port) and migrated with
# `alembic upgrade head`.  This never touches the protected live DB and needs no
# password.  If no server binaries are present the DB tests SKIP (CI-portable).
# Synthetic fixtures only.
# =========================================================================== #
import os
import shutil
import socket
import subprocess
import tempfile
import time

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

_BACKEND_DIR = Path(__file__).resolve().parents[1]
os.environ.setdefault("NARRATIVE_WARM_ON_INGEST", "0")


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
def pg_sessionmaker():
    """Spin up a throwaway PG cluster, migrate it, yield a sessionmaker (lazy:
    only created when a DB test requests it)."""
    bin_dir = _find_pg_bin()
    if bin_dir is None:
        pytest.skip("no local PostgreSQL/EDB binaries found for an ephemeral cluster")

    base = Path(tempfile.mkdtemp(prefix="pgtest_coa_"))
    data = base / "data"
    log = base / "log.txt"
    port = _free_port()
    started = False
    try:
        r = subprocess.run(
            [_pg_exe(bin_dir, "initdb"), "-D", str(data), "-U", "postgres",
             "-A", "trust", "-E", "UTF8"], capture_output=True, text=True,
        )
        if r.returncode != 0:
            pytest.skip(f"initdb failed: {r.stderr[-400:]}")

        # NB: pg_ctl spawns a LONG-LIVED postgres child that inherits this call's
        # stdio. With a pipe (capture_output=True) the child keeps the write end open
        # for its whole lifetime, so subprocess.run() blocks on EOF forever. Send
        # stdio to DEVNULL (server output already goes to ``-l log``) to avoid that
        # deadlock; read the log file for diagnostics on failure.
        r = subprocess.run(
            [_pg_exe(bin_dir, "pg_ctl"), "-D", str(data), "-l", str(log),
             "-o", f"-p {port} -c listen_addresses=127.0.0.1", "-w", "start"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if r.returncode != 0:
            tail = log.read_text()[-400:] if log.exists() else ""
            pytest.skip(f"pg_ctl start failed (rc={r.returncode}): {tail}")
        started = True

        admin = create_engine(
            f"postgresql+psycopg2://postgres@127.0.0.1:{port}/postgres",
            isolation_level="AUTOCOMMIT", future=True,
        )
        with admin.connect() as c:
            c.execute(text("CREATE DATABASE testdb"))
        admin.dispose()

        env = dict(os.environ)
        env.update({
            "DB_HOST": "127.0.0.1", "DB_PORT": str(port), "DB_USER": "postgres",
            "DB_PASSWORD": "", "DB_NAME": "testdb", "DATABASE_URL": "",
        })
        r = subprocess.run(
            ["python", "-m", "alembic", "upgrade", "head"],
            cwd=str(_BACKEND_DIR), env=env, capture_output=True, text=True,
        )
        if r.returncode != 0:
            pytest.skip(f"alembic upgrade head failed: {r.stdout[-300:]}\n{r.stderr[-600:]}")

        engine = create_engine(
            f"postgresql+psycopg2://postgres@127.0.0.1:{port}/testdb", future=True
        )
        Session = sessionmaker(bind=engine, autoflush=False, future=True)
        yield Session
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
def clean_db(pg_sessionmaker):
    """Truncate the CoA / GL tables touched by these tests (NOT autouse, so the
    DB-free tests above never trigger the cluster)."""
    s = pg_sessionmaker()
    try:
        s.execute(text(
            "TRUNCATE dim_gl_account, dim_gl_na, dim_gl_cf, dim_legal_entity, "
            "fact_gl_line, fact_gl_entry, org_meta_dataset_load, "
            "snap_dim_gl_account, snap_dim_gl_na, snap_dim_gl_cf RESTART IDENTITY CASCADE"
        ))
        s.commit()
    finally:
        s.close()
    yield pg_sessionmaker


def _db_client(pg_sessionmaker, user) -> TestClient:
    def _ov_session():
        db = pg_sessionmaker()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = _ov_session
    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[require_admin] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def _master_workbook_bytes() -> bytes:
    """Shared Entity-LESS BS/PL Master workbook (Master_BS + Master_PL)."""
    wb = Workbook()
    bs = wb.active
    bs.title = "Master_BS"
    bs.append(["Account", "Account description", "L1", "L2", "L3", "L4"])
    bs.append(["10000", "Cash", "BS", "Assets", "Current", "Cash"])
    bs.append(["12000", "Receivables", "BS", "Assets", "Current", "AR"])
    pl = wb.create_sheet("Master_PL")
    pl.append(["Account", "Account description", "L1 - BS/PL", "L2", "L3", "L4"])
    pl.append(["80000", "Revenue", "PL", "Income", "Sales", "Revenue"])
    pl.append(["70000", "COGS", "PL", "Expense", "Material", "COGS"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_GL_CSV_ENTITY_02 = (
    "Tx,Account,Amount,PostingDate,SourceType,SourceNo\n"
    "1,10000,600.00,15.03.2024,,\n"
    "2,80000,-600.00,15.03.2024,,\n"
)

_GL_PROFILE_02 = {
    "entity": {"mode": "fixed", "value": "02"},
    "fiscal_year": {"mode": "from_date", "value": None},
    "sign": {"mode": "signed", "amount": "Amount"},
    "decimal": ".", "thousands": ",", "date_dayfirst": True,
    "columns": {
        "journal_entry_number": "Tx", "account_number": "Account",
        "posting_date": "PostingDate", "source_type": "SourceType",
        "source_no": "SourceNo",
    },
    "linking_strategy": "txn", "entry_type": "actual", "source_system": "test",
}


# =========================================================================== #
# TEST A (reproduce-first): one shared CoA -> ALL members in ONE atomic commit
# =========================================================================== #
def test_A_multi_prefix_commit_maps_all_members(clean_db):
    """A single Entity-less Master workbook committed for prefixes 01 AND 02 must
    populate dim_gl_account for BOTH, register BOTH in dim_legal_entity, and let a
    subsequent GL commit for entity 02 pass the unmapped pre-flight.

    FAILS before Task 3 (entity_prefixes ignored -> legacy Entity-column 422);
    PASSES after.
    """
    pg = clean_db
    client = _db_client(pg, _ADMIN)

    up = client.post(
        "/api/v1/ingest/upload",
        files={"file": ("master.xlsx", _master_workbook_bytes(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert up.status_code == 200, up.text
    fid = up.json()["file_id"]

    resp = client.post(
        "/api/v1/ingest/mapping/commit",
        json={
            "file_id": fid, "format": "bs_pl_master",
            "entity_prefixes": ["01", "02"],
            "fiscal_years": [2023, 2024], "replace_mode": "replace",
        },
    )
    assert resp.status_code == 200, resp.text

    chk = pg()
    try:
        n01 = chk.execute(text(
            "SELECT count(*) FROM dim_gl_account WHERE account_number_group LIKE '01%'"
        )).scalar()
        n02 = chk.execute(text(
            "SELECT count(*) FROM dim_gl_account WHERE account_number_group LIKE '02%'"
        )).scalar()
        assert n01 > 0 and n02 > 0, f"expected both prefixes mapped (01={n01}, 02={n02})"
        ents = {str(r[0]) for r in chk.execute(
            text("SELECT entity_prefix FROM dim_legal_entity")).fetchall()}
        assert {"01", "02"}.issubset(ents), f"dim_legal_entity missing members: {ents}"
    finally:
        chk.close()

    up2 = client.post(
        "/api/v1/ingest/upload",
        files={"file": ("gl02.csv", _GL_CSV_ENTITY_02.encode(), "text/csv")},
    )
    assert up2.status_code == 200, up2.text
    gl_fid = up2.json()["file_id"]

    fake = {
        "load_id": 1, "entries": 2, "lines": 2, "ar": 0, "ap": 0, "sales": 0,
        "com": 0, "skipped": 0, "loaded_at": "2024-03-15T00:00:00+00:00",
        "commit_mode": "replace",
    }
    with patch("app.routers.ingest.load_canonical", return_value=fake):
        gl_resp = client.post(
            "/api/v1/ingest/commit",
            json={
                "file_id": gl_fid, "profile": _GL_PROFILE_02,
                "dataset": "test_gl_02", "confirm_soft": True,
                "commit_mode": "replace",
            },
        )
    if gl_resp.status_code == 422:
        detail = gl_resp.json().get("detail")
        code = detail.get("code") if isinstance(detail, dict) else None
        assert code != "gl_accounts_unmapped", f"entity 02 still unmapped: {detail}"
    assert gl_resp.status_code == 200, gl_resp.text


# =========================================================================== #
# TEST B: cross-entity replicate (additive, no-overwrite, security)
# =========================================================================== #
def _seed_account(session, ang, fy, *, level_4="L4", account_name="Acct",
                  source_system="seed", na=None, cf=None):
    """Insert a synthetic dim_gl_account row and optional dim_gl_na / dim_gl_cf rows.

    Parameters
    ----------
    na : tuple(l6_na_mapping, l7_na_description) | None
    cf : tuple(l1, l2, l3, l4, l5, cf_mapping) | None
        When provided, a dim_gl_cf row is inserted alongside the account row.
        Used by CF-copy regression tests (Test C).
    """
    session.execute(text(
        "INSERT INTO dim_gl_account "
        "(account_number_group, fiscal_year, gl_account_id, account_name, "
        " level_0, level_1, level_2, level_3, level_4, source_system) "
        "VALUES (:ang,:fy,:glid,:an,'BS','Assets','Current',:l3,:l4,:ss)"
    ), {"ang": ang, "fy": fy, "glid": ang[2:].lstrip("0") or "0", "an": account_name,
        "l3": "Cash", "l4": level_4, "ss": source_system})
    if na is not None:
        session.execute(text(
            "INSERT INTO dim_gl_na "
            "(account_number_group, fiscal_year, l6_na_mapping, l7_na_description) "
            "VALUES (:ang,:fy,:l6,:l7)"
        ), {"ang": ang, "fy": fy, "l6": na[0], "l7": na[1]})
    if cf is not None:
        session.execute(text(
            "INSERT INTO dim_gl_cf "
            "(account_number_group, fiscal_year, l1, l2, l3, l4, l5, cf_mapping) "
            "VALUES (:ang,:fy,:l1,:l2,:l3,:l4,:l5,:cfm)"
        ), {"ang": ang, "fy": fy,
            "l1": cf[0], "l2": cf[1], "l3": cf[2],
            "l4": cf[3], "l5": cf[4], "cfm": cf[5]})
    session.commit()


def test_B_replicate_across_entities(clean_db):
    pg = clean_db
    fy = 2023
    seed = pg()
    try:
        # Seed '01010000' with NA + CF so we can assert both are copied to '02010000'
        # (CF-copy regression guard — the old replicate endpoint omitted dim_gl_cf).
        _seed_account(seed, "01010000", fy, level_4="CASH_L4",
                      account_name="Cash 01", na=("NA6", "NA7 desc"),
                      cf=("CF_L1_SRC", "CF_L2_SRC", None, None, None, "CF_KEY_SRC"))
        _seed_account(seed, "01020000", fy, level_4="AR_L4", account_name="AR 01")
    finally:
        seed.close()

    client = _db_client(pg, _ADMIN)
    resp = client.post(
        "/api/v1/ingest/mapping/replicate-across-entities",
        json={"keys": [
            {"account_number_group": "02010000", "fiscal_year": fy},   # source 01010000
            {"account_number_group": "02020000", "fiscal_year": fy},   # source 01020000
            {"account_number_group": "02099999", "fiscal_year": fy},   # NO source
        ]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 2, body
    assert {k["account_number_group"] for k in body["created_keys"]} == {"02010000", "02020000"}
    assert [u["account_number_group"] for u in body["truly_unmapped"]] == ["02099999"]
    assert body["truly_unmapped"][0]["account"] == "99999"  # same display as the 422

    chk = pg()
    try:
        row = chk.execute(text(
            "SELECT level_4, account_name FROM dim_gl_account "
            "WHERE account_number_group='02010000' AND fiscal_year=:fy"
        ), {"fy": fy}).fetchone()
        assert row is not None and row[0] == "CASH_L4" and row[1] == "Cash 01"
        na = chk.execute(text(
            "SELECT l6_na_mapping, l7_na_description FROM dim_gl_na "
            "WHERE account_number_group='02010000' AND fiscal_year=:fy"
        ), {"fy": fy}).fetchone()
        assert na is not None and na[0] == "NA6" and na[1] == "NA7 desc"
        # TEST C — CF-copy regression: _replicate_dim_across_entities must also copy
        # dim_gl_cf from the same source row (fixed the pre-existing CF-omission bug).
        cf = chk.execute(text(
            "SELECT l1, l2, cf_mapping FROM dim_gl_cf "
            "WHERE account_number_group='02010000' AND fiscal_year=:fy"
        ), {"fy": fy}).fetchone()
        assert cf is not None, (
            "dim_gl_cf row for '02010000' must be created by replicate-across-entities "
            "(CF-copy regression guard: the helper must copy l1..l5 + cf_mapping from "
            "the same source account_number_group as the account/NA copy)"
        )
        assert cf[0] == "CF_L1_SRC", f"dim_gl_cf.l1 mismatch: {cf[0]!r}"
        assert cf[1] == "CF_L2_SRC", f"dim_gl_cf.l2 mismatch: {cf[1]!r}"
        assert cf[2] == "CF_KEY_SRC", f"dim_gl_cf.cf_mapping mismatch: {cf[2]!r}"
        src = chk.execute(text(
            "SELECT level_4 FROM dim_gl_account WHERE account_number_group='01010000'"
        )).scalar()
        assert src == "CASH_L4"  # source 01 row unchanged
    finally:
        chk.close()

    # --- NO-OVERWRITE: an existing target row must never be clobbered/counted. ---
    seed2 = pg()
    try:
        _seed_account(seed2, "01030000", fy, level_4="SRC_L4", account_name="Src 03")
        _seed_account(seed2, "02030000", fy, level_4="KEEPME", account_name="Existing 02")
    finally:
        seed2.close()

    resp2 = client.post(
        "/api/v1/ingest/mapping/replicate-across-entities",
        json={"keys": [{"account_number_group": "02030000", "fiscal_year": fy}]},
    )
    assert resp2.status_code == 200, resp2.text
    body2 = resp2.json()
    assert body2["created"] == 0, body2
    assert body2["created_keys"] == [], body2

    chk2 = pg()
    try:
        kept = chk2.execute(text(
            "SELECT level_4, account_name FROM dim_gl_account "
            "WHERE account_number_group='02030000' AND fiscal_year=:fy"
        ), {"fy": fy}).fetchone()
        assert kept[0] == "KEEPME" and kept[1] == "Existing 02", kept
    finally:
        chk2.close()


def test_B_guard_restricted_user_forbidden(clean_db):
    """Restricted (non-admin) user without visibility for prefix 02 -> 403, BEFORE
    any write (empty visibility tables => deny-all fail-closed)."""
    pg = clean_db
    restricted = User(user_id=7, email="restricted@test", display_name="R", is_admin=False)
    client = _db_client(pg, restricted)
    resp = client.post(
        "/api/v1/ingest/mapping/replicate-across-entities",
        json={"keys": [{"account_number_group": "02010000", "fiscal_year": 2023}]},
    )
    assert resp.status_code == 403, resp.text
