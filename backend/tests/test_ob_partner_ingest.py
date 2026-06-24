"""Opening-balance + Partner-master ingestion tests (Project Setup rework).

Layers:
  A. DB-FREE pure mapping: ``apply_partner_profile`` (id construction, country
     normalization, empty-key drop, dedup, required-field errors).
  B. UPLOAD endpoints (auth, NO write): OB + partner upload return columns/sample.
  C. UPLOAD-SECURITY: admin-gate on commit, wrong extension, oversize, non-finite.
  D. ROUND-TRIP (live v2 DB, opt-in): OB commit tags entry_type/period0/JEGN '9'
     and honours scope first_year vs all; partner commit join_key->id UPSERT.
     SKIPPED unless DB_NAME=finssentials_v2.  ALWAYS cleans up (finally) so the
     golden ``compare live v2`` stays EQUIVALENT.
"""
from __future__ import annotations

import io
import os
from unittest.mock import MagicMock

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.auth import User, current_user, require_admin
from app.db import get_session
from app.main import app

_ADMIN = User(user_id=1, email="admin@test", display_name="Admin", is_admin=True)
_NON_ADMIN = User(user_id=2, email="user@test", display_name="User", is_admin=False)


def _client_as(user: User, session=None) -> TestClient:
    app.dependency_overrides[get_session] = lambda: (session or MagicMock())
    app.dependency_overrides[current_user] = lambda: user
    if user.is_admin:
        app.dependency_overrides[require_admin] = lambda: user
    else:
        app.dependency_overrides.pop(require_admin, None)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
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


# =========================================================================== #
# A) apply_partner_profile — pure mapping (DB-free)
# =========================================================================== #
class TestApplyPartnerProfile:
    def _profile(self, **kw):
        from etl.partner_master_mapping import partner_profile_from_dict

        base = {
            "side": "customer",
            "entity": {"mode": "fixed", "value": "1"},
            "join_key": {"column": "No."},
            "columns": {"name_line_1": "Name", "country_code": "Ctry", "city": "City"},
            "source_system": "test",
        }
        base.update(kw)
        return partner_profile_from_dict(base)

    def test_join_key_builds_customer_id(self):
        from etl.partner_master_mapping import apply_partner_profile

        raw = pd.DataFrame({"No.": ["100", "200"], "Name": ["Acme", "Beta"],
                            "Ctry": ["DE", "AT"], "City": ["Berlin", "Wien"]})
        out = apply_partner_profile(raw, self._profile())
        by_id = {r["customer_id"]: r for r in out.to_dict("records")}
        assert set(by_id) == {"01100", "01200"}
        assert by_id["01100"]["debtor_number"] == "100"
        assert by_id["01100"]["name_line_1"] == "Acme"
        # country normalised to ISO3
        assert by_id["01100"]["country_code"] == "DEU"

    def test_supplier_side_builds_supplier_id(self):
        from etl.partner_master_mapping import apply_partner_profile

        raw = pd.DataFrame({"No.": ["77"], "Name": ["Vendor"], "Ctry": ["DE"], "City": ["X"]})
        out = apply_partner_profile(raw, self._profile(side="supplier"))
        rec = out.to_dict("records")[0]
        assert rec["supplier_id"] == "0177"
        assert rec["creditor_number"] == "77"

    def test_empty_join_key_dropped_and_dedup(self):
        from etl.partner_master_mapping import apply_partner_profile

        raw = pd.DataFrame({
            "No.": ["100", "", "100"],
            "Name": ["Acme", "Ghost", "Acme dup"],
            "Ctry": ["DE", "DE", "DE"], "City": ["B", "B", "B"],
        })
        out = apply_partner_profile(raw, self._profile())
        assert len(out) == 1  # empty dropped, duplicate id collapsed (keep first)
        assert out.iloc[0]["name_line_1"] == "Acme"

    def test_entity_column_mode(self):
        from etl.partner_master_mapping import apply_partner_profile

        raw = pd.DataFrame({"E": ["1", "2"], "No.": ["100", "100"],
                            "Name": ["A", "B"], "Ctry": ["DE", "DE"], "City": ["X", "Y"]})
        out = apply_partner_profile(raw, self._profile(entity={"mode": "column", "value": "E"}))
        assert set(out["customer_id"]) == {"01100", "02100"}

    def test_missing_join_key_column_raises(self):
        from etl.partner_master_mapping import apply_partner_profile

        raw = pd.DataFrame({"Name": ["A"]})
        with pytest.raises(KeyError):
            apply_partner_profile(raw, self._profile())

    def test_missing_name_raises(self):
        from etl.partner_master_mapping import apply_partner_profile

        raw = pd.DataFrame({"No.": ["1"]})
        with pytest.raises(KeyError):
            apply_partner_profile(raw, self._profile(columns={}))

    def test_invalid_side_raises(self):
        from etl.partner_master_mapping import apply_partner_profile

        raw = pd.DataFrame({"No.": ["1"], "Name": ["A"]})
        with pytest.raises(ValueError):
            apply_partner_profile(raw, self._profile(side="bogus"))


# =========================================================================== #
# B) Upload endpoints — preview only, NO write
# =========================================================================== #
def _gl_profile() -> dict:
    return {
        "entity": {"mode": "fixed", "value": "01"},
        "fiscal_year": {"mode": "from_date", "value": None},
        "sign": {"mode": "signed", "amount": "Amount"},
        "decimal": ".", "thousands": ",", "date_dayfirst": True,
        "columns": {
            "journal_entry_number": "Doc", "account_number": "Account",
            "posting_date": "Date",
        },
        "linking_strategy": "none", "entry_type": "actual", "source_system": "ob_test",
    }


class TestUploadPreview:
    def test_ob_upload_returns_preview_no_write(self):
        session = MagicMock(side_effect=AssertionError("no DB on upload"))
        client = _client_as(_ADMIN)
        raw = _xlsx_bytes([{"Doc": "1", "Account": "1200", "Date": "01.01.2023", "Amount": "500"}])
        resp = client.post(
            "/api/v1/ingest/opening-balance/upload",
            files={"file": ("ob.xlsx", raw, "application/octet-stream")},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["file_id"]
        assert "Account" in body["columns"] and "Amount" in body["columns"]
        assert len(body["sample"]) == 1

    def test_partner_upload_returns_preview_no_write(self):
        client = _client_as(_ADMIN)
        raw = _xlsx_bytes([{"No.": "100", "Name": "Acme", "Ctry": "DE"}])
        resp = client.post(
            "/api/v1/ingest/partner-master/upload",
            files={"file": ("partners.xlsx", raw, "application/octet-stream")},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["file_id"]
        assert {"No.", "Name", "Ctry"} <= set(body["columns"])

    def test_upload_requires_auth(self):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/v1/ingest/opening-balance/upload",
            files={"file": ("ob.xlsx", b"x", "application/octet-stream")},
        )
        assert resp.status_code == 401


# =========================================================================== #
# C) Upload security
# =========================================================================== #
class TestUploadSecurity:
    def test_ob_commit_requires_admin(self):
        client = _client_as(_NON_ADMIN)
        resp = client.post("/api/v1/ingest/opening-balance/commit", json={
            "file_id": "deadbeef", "profile": _gl_profile(), "scope": "first_year",
        })
        assert resp.status_code == 403

    def test_partner_commit_requires_admin(self):
        client = _client_as(_NON_ADMIN)
        resp = client.post("/api/v1/ingest/partner-master/commit", json={
            "file_id": "deadbeef", "profile": {"side": "customer"},
        })
        assert resp.status_code == 403

    def test_ob_upload_rejects_wrong_extension(self):
        client = _client_as(_ADMIN)
        resp = client.post(
            "/api/v1/ingest/opening-balance/upload",
            files={"file": ("ob.txt", b"x,y\n1,2", "text/plain")},
        )
        assert resp.status_code == 415

    def test_ob_upload_rejects_oversized(self, monkeypatch):
        # H2: the staging upload now enforces the OB/partner-specific cap WHILE
        # reading (chunked), aborting before the whole body is buffered.
        import app.routers.ingest as ing
        monkeypatch.setattr(ing, "OB_PARTNER_MAX_UPLOAD_BYTES", 1024)
        client = _client_as(_ADMIN)
        big = b"PK" + b"\x00" * 4096
        resp = client.post(
            "/api/v1/ingest/opening-balance/upload",
            files={"file": ("ob.xlsx", big, "application/octet-stream")},
        )
        assert resp.status_code == 413

    def test_partner_upload_rejects_oversized(self, monkeypatch):
        import app.routers.ingest as ing
        monkeypatch.setattr(ing, "OB_PARTNER_MAX_UPLOAD_BYTES", 1024)
        client = _client_as(_ADMIN)
        big = b"PK" + b"\x00" * 4096
        resp = client.post(
            "/api/v1/ingest/partner-master/upload",
            files={"file": ("p.xlsx", big, "application/octet-stream")},
        )
        assert resp.status_code == 413

    def test_save_upload_aborts_before_full_buffer(self, monkeypatch):
        """H2: the chunked read must abort once the running total exceeds the cap,
        WITHOUT first buffering the whole body (simulate via a small cap + a
        fake UploadFile that records how much was read)."""
        import asyncio
        import app.routers.ingest as ing
        from fastapi import HTTPException

        monkeypatch.setattr(ing, "OB_PARTNER_MAX_UPLOAD_BYTES", 4 * 1024)
        monkeypatch.setattr(ing, "_UPLOAD_CHUNK_BYTES", 1024)

        class _FakeUpload:
            filename = "ob.xlsx"
            size = None  # no declared size — force the chunked path to catch it

            def __init__(self, total_bytes: int):
                self._remaining = total_bytes
                self.read_bytes = 0

            async def read(self, n: int = -1) -> bytes:
                take = min(n if n and n > 0 else self._remaining, self._remaining)
                self._remaining -= take
                self.read_bytes += take
                return b"\x00" * take

        # 1 MiB body, 4 KiB cap → must 413 after reading just over the cap, never 1 MiB.
        # Run on a FRESH event loop (never touch/close the shared default loop, which
        # TestClient in other tests relies on).
        fake = _FakeUpload(1024 * 1024)
        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(HTTPException) as ei:
                loop.run_until_complete(ing._save_upload(fake))
        finally:
            loop.close()
        assert ei.value.status_code == 413
        # Aborted early: at most cap + one chunk read, far below the 1 MiB body.
        assert fake.read_bytes <= ing.OB_PARTNER_MAX_UPLOAD_BYTES + ing._UPLOAD_CHUNK_BYTES

    def test_ob_commit_missing_file_404(self):
        client = _client_as(_ADMIN)
        resp = client.post("/api/v1/ingest/opening-balance/commit", json={
            "file_id": "nonexistentfileid", "profile": _gl_profile(), "scope": "all",
        })
        assert resp.status_code == 404

    def test_ob_commit_rejects_bad_scope(self):
        client = _client_as(_ADMIN)
        resp = client.post("/api/v1/ingest/opening-balance/commit", json={
            "file_id": "deadbeef", "profile": _gl_profile(), "scope": "nonsense",
        })
        assert resp.status_code == 422

    def test_assert_finite_rejects_nan(self):
        import app.routers.ingest as ing
        from fastapi import HTTPException

        df = pd.DataFrame({"amount": [1.0, float("nan")]})
        with pytest.raises(HTTPException) as ei:
            ing._assert_finite_amounts(df)
        assert ei.value.status_code == 422

    def test_assert_finite_rejects_inf(self):
        import app.routers.ingest as ing
        from fastapi import HTTPException

        df = pd.DataFrame({"amount": [1.0, float("inf")]})
        with pytest.raises(HTTPException) as ei:
            ing._assert_finite_amounts(df)
        assert ei.value.status_code == 422


# =========================================================================== #
# C2) Hardening — parse bounds (H1), prefix fail-closed (M3), db-error (L2)
# =========================================================================== #
class TestParseBounds:
    def test_too_many_rows_422(self):
        import app.routers.ingest as ing
        from fastapi import HTTPException

        df = pd.DataFrame({"a": range(ing.MAX_PARSE_ROWS + 1)})
        with pytest.raises(HTTPException) as ei:
            ing._assert_parse_bounds(df)
        assert ei.value.status_code == 422
        assert "too many rows" in ei.value.detail

    def test_too_many_cols_422(self):
        import app.routers.ingest as ing
        from fastapi import HTTPException

        df = pd.DataFrame({f"c{i}": [0] for i in range(ing.MAX_PARSE_COLS + 1)})
        with pytest.raises(HTTPException) as ei:
            ing._assert_parse_bounds(df)
        assert ei.value.status_code == 422
        assert "too many columns" in ei.value.detail

    def test_within_bounds_ok(self):
        import app.routers.ingest as ing

        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        ing._assert_parse_bounds(df)  # no raise

    def test_oversized_ob_upload_frame_422(self, monkeypatch):
        """H1: a staged file whose parsed frame exceeds the row ceiling → 422."""
        import app.routers.ingest as ing
        monkeypatch.setattr(ing, "MAX_PARSE_ROWS", 5)
        client = _client_as(_ADMIN)
        rows = [{"Account": "1200", "Amount": "1"} for _ in range(20)]
        raw = _xlsx_bytes(rows)
        resp = client.post(
            "/api/v1/ingest/opening-balance/upload",
            files={"file": ("ob.xlsx", raw, "application/octet-stream")},
        )
        assert resp.status_code == 422, resp.text
        assert "too many rows" in resp.json()["detail"]


class TestPrefixFailClosed:
    """M3: _assert_prefixes_visible must reject an empty `requested` for a
    RESTRICTED user (no resolvable prefix), and pass for an admin."""

    def test_restricted_empty_prefix_403(self, monkeypatch):
        import app.routers.ingest as ing
        from fastapi import HTTPException

        monkeypatch.setattr(
            ing, "_visible_entity_prefixes_or_none", lambda s, u: {"01", "02"}
        )
        with pytest.raises(HTTPException) as ei:
            ing._assert_prefixes_visible(MagicMock(), _NON_ADMIN, [])
        assert ei.value.status_code == 403

    def test_restricted_out_of_scope_403(self, monkeypatch):
        import app.routers.ingest as ing
        from fastapi import HTTPException

        monkeypatch.setattr(
            ing, "_visible_entity_prefixes_or_none", lambda s, u: {"01"}
        )
        with pytest.raises(HTTPException) as ei:
            ing._assert_prefixes_visible(MagicMock(), _NON_ADMIN, ["02"])
        assert ei.value.status_code == 403

    def test_restricted_in_scope_ok(self, monkeypatch):
        import app.routers.ingest as ing

        monkeypatch.setattr(
            ing, "_visible_entity_prefixes_or_none", lambda s, u: {"01", "02"}
        )
        ing._assert_prefixes_visible(MagicMock(), _NON_ADMIN, ["01"])  # no raise

    def test_admin_empty_prefix_ok(self, monkeypatch):
        import app.routers.ingest as ing

        monkeypatch.setattr(ing, "_visible_entity_prefixes_or_none", lambda s, u: None)
        ing._assert_prefixes_visible(MagicMock(), _ADMIN, [])  # admin unaffected


class TestDbErrorDetail:
    def test_raise_db_error_generic_detail(self):
        """L2: _raise_db_error must NOT leak str(exc); detail is generic."""
        import app.routers.ingest as ing
        from fastapi import HTTPException

        secret = "FATAL: password authentication failed for user 'admin' on host db.internal"
        with pytest.raises(HTTPException) as ei:
            ing._raise_db_error(Exception(secret), "ctx")
        assert ei.value.status_code == 500
        assert ei.value.detail == "Database error; check server logs."
        assert "password" not in str(ei.value.detail)


# =========================================================================== #
# D) ROUND-TRIP — live v2 DB (opt-in; auto-clean)
# =========================================================================== #
def _v2_session_or_skip():
    from sqlalchemy import text
    from app.db import SessionLocal
    try:
        s = SessionLocal()
        s.execute(text("SELECT 1 FROM fact_gl_entry LIMIT 1"))
        return s
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"v2 DB not reachable: {exc}")


_V2 = os.getenv("DB_NAME", "Finssentials") != "finssentials_v2"


@pytest.mark.skipif(_V2, reason="round-trip runs only against finssentials_v2 (set DB_NAME)")
class TestRoundTripV2:
    """Stage a file via /upload, commit via the router function directly (real session)."""

    _PFX = "97"  # test-only entity prefix unlikely to collide with seeded data

    def _stage_ob(self, client, rows) -> str:
        raw = _xlsx_bytes(rows)
        resp = client.post(
            "/api/v1/ingest/opening-balance/upload",
            files={"file": ("ob.xlsx", raw, "application/octet-stream")},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["file_id"]

    def _seed_account(self, session):
        """OB lines FK to dim_gl_account; seed the test account for both years.

        In production the wizard commits GL + CoA before OB, so the mapping
        already exists.  The test seeds it explicitly and cleans it up.
        """
        from sqlalchemy import text
        ang = f"{self._PFX}001200"
        for fy in (2022, 2023):
            session.execute(text(
                "INSERT INTO dim_gl_account "
                "(account_number_group, fiscal_year, gl_account_id, account_name, "
                " level_0, level_1, level_2, level_3, source_system) "
                "VALUES (:ang,:fy,'1200','Bank','BS','Assets','Cash','Bank','ob_test') "
                "ON CONFLICT (account_number_group, fiscal_year) DO NOTHING"
            ), {"ang": ang, "fy": fy})
        session.commit()

    def _clean_ob(self, session):
        from sqlalchemy import text
        session.execute(text(
            "DELETE FROM fact_gl_line WHERE SUBSTR(journal_entry_group_number,1,2)=:p"
        ), {"p": self._PFX})
        session.execute(text(
            "DELETE FROM fact_gl_entry WHERE SUBSTR(journal_entry_group_number,1,2)=:p"
        ), {"p": self._PFX})
        session.execute(text(
            "DELETE FROM meta_dataset_load WHERE dataset='opening_balance' "
            "AND :p = ANY(scope_entity_prefixes)"
        ), {"p": self._PFX})
        session.execute(text(
            "DELETE FROM dim_gl_account WHERE SUBSTR(account_number_group,1,2)=:p"
        ), {"p": self._PFX})
        session.commit()

    def test_ob_commit_tags_and_scope(self):
        from sqlalchemy import text
        import app.routers.ingest as ing

        session = _v2_session_or_skip()
        client = _client_as(_ADMIN, session=session)
        prof = _gl_profile()
        prof["entity"] = {"mode": "fixed", "value": self._PFX}
        try:
            self._clean_ob(session)
            self._seed_account(session)
            rows = [
                {"Doc": "1", "Account": "1200", "Date": "01.01.2022", "Amount": "500"},
                {"Doc": "2", "Account": "1200", "Date": "01.01.2023", "Amount": "700"},
            ]
            # ---- scope=first_year -> only earliest FY (2022) ----
            fid = self._stage_ob(client, rows)
            body = ing.OpeningBalanceCommitRequest(file_id=fid, profile=prof, scope="first_year")
            res = ing.opening_balance_commit(body, session=session, _admin=_ADMIN)
            assert res.fiscal_years == [2022]
            assert res.lines == 1
            tagged = session.execute(text(
                "SELECT entry_type, fiscal_period, journal_entry_group_number, fiscal_year "
                "FROM fact_gl_entry WHERE SUBSTR(journal_entry_group_number,1,2)=:p"
            ), {"p": self._PFX}).fetchall()
            assert tagged, "expected OB rows"
            for et, fp, jegn, fy in tagged:
                assert et == "opening_balance"
                assert int(fp) == 0
                assert jegn[2] == "9", "synthetic JEGN must carry '9' after entity prefix"
                assert int(fy) == 2022

            # ---- scope=all -> both years ----
            self._clean_ob(session)
            self._seed_account(session)
            fid2 = self._stage_ob(client, rows)
            body2 = ing.OpeningBalanceCommitRequest(file_id=fid2, profile=prof, scope="all")
            res2 = ing.opening_balance_commit(body2, session=session, _admin=_ADMIN)
            assert sorted(res2.fiscal_years) == [2022, 2023]
            assert res2.lines == 2

            # ---- idempotent: re-commit same scope writes no duplicate lines ----
            fid3 = self._stage_ob(client, rows)
            body3 = ing.OpeningBalanceCommitRequest(file_id=fid3, profile=prof, scope="all")
            ing.opening_balance_commit(body3, session=session, _admin=_ADMIN)
            cnt = session.execute(text(
                "SELECT COUNT(*) FROM fact_gl_line WHERE SUBSTR(journal_entry_group_number,1,2)=:p"
            ), {"p": self._PFX}).fetchone()[0]
            assert int(cnt) == 2, "re-commit must be idempotent (no duplicate OB lines)"
        finally:
            self._clean_ob(session)
            session.close()

    def test_ob_commit_synthesizes_posting_date(self):
        """M2: a row with no posting_date gets Jan-1 of its fiscal_year so the
        BS opening-stock MIN(posting_date) read works."""
        from sqlalchemy import text
        import app.routers.ingest as ing

        session = _v2_session_or_skip()
        client = _client_as(_ADMIN, session=session)
        prof = _gl_profile()
        prof["entity"] = {"mode": "fixed", "value": self._PFX}
        # fiscal_year fixed so a blank Date still yields a FY for the Jan-1 synth.
        prof["fiscal_year"] = {"mode": "fixed", "value": 2022}
        # posting_date IS mapped (the loader requires it) but the cell is blank,
        # so M2 must synthesize Jan-1 of the fiscal_year.
        prof["columns"] = {
            "journal_entry_number": "Doc", "account_number": "Account",
            "posting_date": "Date",
        }
        try:
            self._clean_ob(session)
            self._seed_account(session)
            # Blank "Date" cell → posting_date must be synthesized to 2022-01-01.
            rows = [{"Doc": "1", "Account": "1200", "Date": "", "Amount": "500"}]
            fid = self._stage_ob(client, rows)
            body = ing.OpeningBalanceCommitRequest(file_id=fid, profile=prof, scope="all")
            res = ing.opening_balance_commit(body, session=session, _admin=_ADMIN)
            assert res.lines == 1
            pd_val = session.execute(text(
                "SELECT posting_date FROM fact_gl_entry "
                "WHERE SUBSTR(journal_entry_group_number,1,2)=:p"
            ), {"p": self._PFX}).fetchone()[0]
            assert str(pd_val).startswith("2022-01-01")
        finally:
            self._clean_ob(session)
            session.close()

    def test_ob_double_commit_against_in_data_ob_skipped(self):
        """M1: a file-OB commit must SKIP an (entity, account, fy) that already has
        an in-data opening_balance row (file-OB and in-data-OB are exclusive)."""
        from sqlalchemy import text
        import app.routers.ingest as ing

        session = _v2_session_or_skip()
        client = _client_as(_ADMIN, session=session)
        prof = _gl_profile()
        prof["entity"] = {"mode": "fixed", "value": self._PFX}
        ang = f"{self._PFX}001200"
        # In-data OB JEGN: leading '9' is NOT at position 3 (it's an in-data row),
        # entity prefix at chars 1-2, char 3 != '9'.
        in_data_jegn = f"{self._PFX}1000000001"
        try:
            self._clean_ob(session)
            self._seed_account(session)
            # Seed an EXISTING in-data opening_balance row for (pfx, 1200, 2022).
            session.execute(text(
                "INSERT INTO fact_gl_entry "
                "(journal_entry_group_number, fiscal_year, fiscal_period, entry_type, "
                " posting_date, currency_code, source_system) "
                "VALUES (:j, 2022, 0, 'opening_balance', '2022-01-01', 'EUR', 'in_data') "
                "ON CONFLICT (journal_entry_group_number, fiscal_year) DO NOTHING"
            ), {"j": in_data_jegn})
            session.execute(text(
                "INSERT INTO fact_gl_line "
                "(booking_line_id, journal_entry_group_number, fiscal_year, line_number, "
                " account_number_group, amount, source_system) "
                "VALUES (700000000001, :j, 2022, 1, :ang, 500, 'in_data')"
            ), {"j": in_data_jegn, "ang": ang})
            session.commit()

            # Now a file-OB commit for the SAME (pfx, 1200, 2022) must skip it.
            prof["fiscal_year"] = {"mode": "fixed", "value": 2022}
            prof["columns"] = {
                "journal_entry_number": "Doc", "account_number": "Account",
                "posting_date": "Date",
            }
            rows = [{"Doc": "1", "Account": "1200", "Date": "01.01.2022", "Amount": "999"}]
            fid = self._stage_ob(client, rows)
            body = ing.OpeningBalanceCommitRequest(file_id=fid, profile=prof, scope="all")
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as ei:
                ing.opening_balance_commit(body, session=session, _admin=_ADMIN)
            assert ei.value.status_code == 409  # all rows collided → nothing to write
            # No synthetic file-OB ('9' at char 3) row was created.
            synth = session.execute(text(
                "SELECT COUNT(*) FROM fact_gl_entry "
                "WHERE SUBSTR(journal_entry_group_number,1,2)=:p "
                "AND SUBSTR(journal_entry_group_number,3,1)='9'"
            ), {"p": self._PFX}).fetchone()[0]
            assert int(synth) == 0
        finally:
            self._clean_ob(session)
            session.close()

    def test_partner_commit_upsert(self):
        from sqlalchemy import text
        import app.routers.ingest as ing

        session = _v2_session_or_skip()
        client = _client_as(_ADMIN, session=session)
        try:
            session.execute(text(
                "DELETE FROM dim_customer WHERE SUBSTR(customer_id,1,2)=:p"
            ), {"p": self._PFX})
            session.commit()

            raw = _xlsx_bytes([
                {"No.": "100", "Name": "Acme GmbH", "Ctry": "DE", "City": "Berlin"},
                {"No.": "200", "Name": "Beta AG", "Ctry": "AT", "City": "Wien"},
            ])
            up = client.post(
                "/api/v1/ingest/partner-master/upload",
                files={"file": ("p.xlsx", raw, "application/octet-stream")},
            )
            assert up.status_code == 200, up.text
            fid = up.json()["file_id"]

            prof = {
                "side": "customer",
                "entity": {"mode": "fixed", "value": self._PFX},
                "join_key": {"column": "No."},
                "columns": {"name_line_1": "Name", "country_code": "Ctry", "city": "City"},
                "source_system": "partner_test",
            }
            body = ing.PartnerMasterCommitRequest(file_id=fid, profile=prof)
            res = ing.partner_master_commit(body, session=session, _admin=_ADMIN)
            assert res.side == "customer" and res.upserted == 2

            got = session.execute(text(
                "SELECT customer_id, debtor_number, name_line_1, country_code "
                "FROM dim_customer WHERE SUBSTR(customer_id,1,2)=:p ORDER BY customer_id"
            ), {"p": self._PFX}).fetchall()
            ids = {r[0] for r in got}
            assert ids == {f"{self._PFX}100", f"{self._PFX}200"}
            by_id = {r[0]: r for r in got}
            assert by_id[f"{self._PFX}100"][2] == "Acme GmbH"
            assert by_id[f"{self._PFX}100"][3] == "DEU"
        finally:
            session.execute(text(
                "DELETE FROM dim_customer WHERE SUBSTR(customer_id,1,2)=:p"
            ), {"p": self._PFX})
            session.commit()
            session.close()
