"""OB commit integration tests: account-fill pre-flight in opening_balance_commit.

All tests in this module write to Postgres and are SKIPPED unless
DB_NAME=finssentials_v2 (consistent with TestRoundTripV2 in
test_ob_partner_ingest.py).  Both tests clean up via finally blocks so the
golden live-vs-v2 stays EQUIVALENT after a run.

D5 — Cross-year fill success:
  OB account '1200' (ang '98001200') exists in dim_gl_account for FY 2022 only.
  The OB file targets FY 2023.  An ovr_account_mapping pin for (ang, 2023)
  lets fill_account_rows_for_keys clone the missing dim row.  Router returns 200
  and fact_gl_line carries the OB row for FY 2023.

D6 — Unresolved account -> HTTP 422 with friendly message:
  Account '7777' (ang '98007777') has NO dim row in any year and no library or
  override entry.  fill_account_rows_for_keys returns unresolved_no_name.
  Router raises HTTPException(422) whose detail names the bare account number
  ('7777') and does NOT contain 'Check server logs'.

Entity prefix '98' is test-only and distinct from TestRoundTripV2 ('97') to
avoid contention when both test classes run against the same DB.

All data is synthetic — project rule (no real customer data).
"""
from __future__ import annotations

import io
import os
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import text

from app.auth import User, current_user, require_admin
from app.db import get_session
from app.main import app

_ADMIN = User(user_id=1, email="admin@test", display_name="Admin", is_admin=True)
_V2 = os.getenv("DB_NAME", "Finssentials") != "finssentials_v2"


# --------------------------------------------------------------------------- #
# Shared helpers — mirror test_ob_partner_ingest.py conventions
# --------------------------------------------------------------------------- #

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


def _v2_session_or_skip():
    from sqlalchemy import text
    from app.db import SessionLocal
    try:
        s = SessionLocal()
        s.execute(text("SELECT 1 FROM fact_gl_entry LIMIT 1"))
        return s
    except Exception as exc:
        pytest.skip(f"v2 DB not reachable: {exc}")


def _testdb_session_or_skip():
    """Connect to the configured Postgres, but REFUSE to write against the LIVE
    ``Finssentials`` DB (protected data).  Runs on any dedicated test DB reachable
    via DB_NAME (e.g. finssentials_v4 / finssentials_v2); skips otherwise.
    """
    from sqlalchemy import text
    from app.db import SessionLocal
    try:
        s = SessionLocal()
        dbname = s.execute(text("SELECT current_database()")).scalar()
        s.execute(text("SELECT 1 FROM fact_gl_entry LIMIT 1"))
    except Exception as exc:
        pytest.skip(f"test DB not reachable: {exc}")
    if str(dbname).strip().casefold() == "finssentials":
        s.close()
        pytest.skip(
            "refusing to run a write-test against LIVE Finssentials; "
            "set DB_NAME to a dedicated test DB (e.g. finssentials_v4)"
        )
    return s


def _gl_profile(entity_prefix: str) -> dict:
    return {
        "entity": {"mode": "fixed", "value": entity_prefix},
        "fiscal_year": {"mode": "from_date", "value": None},
        "sign": {"mode": "signed", "amount": "Amount"},
        "decimal": ".", "thousands": ",", "date_dayfirst": True,
        "columns": {
            "journal_entry_number": "Doc",
            "account_number": "Account",
            "posting_date": "Date",
        },
        "linking_strategy": "none",
        "entry_type": "actual",
        "source_system": "ob_account_fill_test",
    }


# --------------------------------------------------------------------------- #
# Round-trip tests (Postgres only)
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(_V2, reason="round-trip runs only against finssentials_v2 (set DB_NAME)")
class TestOBAccountFillRoundTrip:
    """Integration tests for the fill_account_rows_for_keys pre-flight in OB commit.

    Uses entity prefix '98' (distinct from TestRoundTripV2 which uses '97').
    """

    _PFX = "98"

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _stage_ob(self, client: TestClient, rows: list[dict]) -> str:
        raw = _xlsx_bytes(rows)
        resp = client.post(
            "/api/v1/ingest/opening-balance/upload",
            files={"file": ("ob.xlsx", raw, "application/octet-stream")},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["file_id"]

    def _clean(
        self, session, *, extra_angs: list[str] | None = None
    ) -> None:
        """Remove all test rows for prefix _PFX (fact_gl_*, dim_gl_account,
        org_meta_dataset_load) and optionally named ovr_account_mapping rows."""
        from sqlalchemy import text
        p = self._PFX
        session.execute(text(
            "DELETE FROM fact_gl_line "
            "WHERE SUBSTR(journal_entry_group_number,1,2)=:p"
        ), {"p": p})
        session.execute(text(
            "DELETE FROM fact_gl_entry "
            "WHERE SUBSTR(journal_entry_group_number,1,2)=:p"
        ), {"p": p})
        session.execute(text(
            "DELETE FROM org_meta_dataset_load "
            "WHERE dataset='opening_balance' AND :p = ANY(scope_entity_prefixes)"
        ), {"p": p})
        session.execute(text(
            "DELETE FROM dim_gl_account WHERE SUBSTR(account_number_group,1,2)=:p"
        ), {"p": p})
        if extra_angs:
            session.execute(text(
                "DELETE FROM ovr_account_mapping "
                "WHERE account_number_group = ANY(:angs)"
            ), {"angs": extra_angs})
        session.commit()

    # ------------------------------------------------------------------ #
    # D5: OB commit succeeds when account exists only in a different year
    # ------------------------------------------------------------------ #

    def test_ob_commit_succeeds_when_account_exists_in_different_year(self):
        """D5: OB account '1200' (ang '98001200') has a dim row for FY 2022 only.
        The OB file targets FY 2023.  An ovr_account_mapping pin for FY 2023 lets
        fill_account_rows_for_keys clone the missing dim row before the FK insert.
        Commit returns 200 and fact_gl_line carries the FY 2023 OB row."""
        from sqlalchemy import text
        import app.routers.ingest as ing

        ang = f"{self._PFX}001200"
        session = _v2_session_or_skip()
        client = _client_as(_ADMIN, session=session)
        try:
            self._clean(session, extra_angs=[ang])

            # Seed dim row for FY 2022 ONLY — FY 2023 intentionally absent.
            session.execute(text(
                "INSERT INTO dim_gl_account "
                "(account_number_group, fiscal_year, gl_account_id, account_name, "
                " level_0, level_1, level_2, level_3, source_system) "
                "VALUES (:ang, 2022, '1200', 'Bank', 'BS', 'Assets', 'Cash', "
                "        'Bank', 'ob_fill_test') "
                "ON CONFLICT (account_number_group, fiscal_year) DO NOTHING"
            ), {"ang": ang})

            # Override pin for FY 2023 so fill can resolve the missing year.
            session.execute(text(
                "INSERT INTO ovr_account_mapping "
                "(account_number_group, fiscal_year, "
                " level_0, level_1, level_2, level_3, level_4, l4_sub, "
                " level_2_sort, level_3_sort, is_ic) "
                "VALUES (:ang, 2023, 'BS', 'Assets', 'Cash', 'Bank', "
                "        NULL, NULL, 20, 201, FALSE) "
                "ON CONFLICT (account_number_group, fiscal_year) DO NOTHING"
            ), {"ang": ang})
            session.commit()

            rows_data = [
                {"Doc": "1", "Account": "1200", "Date": "01.01.2023", "Amount": "500"}
            ]
            fid = self._stage_ob(client, rows_data)
            body = ing.OpeningBalanceCommitRequest(
                file_id=fid, profile=_gl_profile(self._PFX), scope="all"
            )
            res = ing.opening_balance_commit(body, session=session, _admin=_ADMIN)

            # Router returned a success response.
            assert res.fiscal_years == [2023]
            assert res.lines == 1

            # The OB fact_gl_line row must exist for FY 2023 under prefix _PFX.
            fact_cnt = session.execute(text(
                "SELECT COUNT(*) FROM fact_gl_line "
                "WHERE SUBSTR(journal_entry_group_number,1,2)=:p "
                "AND fiscal_year=2023"
            ), {"p": self._PFX}).fetchone()[0]
            assert int(fact_cnt) >= 1, (
                f"expected at least one OB fact_gl_line row for FY 2023 "
                f"under prefix {self._PFX!r}"
            )

        finally:
            self._clean(session, extra_angs=[ang])
            session.close()

    # ------------------------------------------------------------------ #
    # D6: Unresolved account -> HTTP 422 with friendly message
    # ------------------------------------------------------------------ #

    def test_ob_commit_422_when_account_absent_from_all_years(self):
        """D6: Account '7777' (ang '98007777') has NO dim row in any year, no
        library entry, no override.  fill_account_rows_for_keys returns
        unresolved_no_name.  Router raises HTTPException(422) whose detail names
        the bare account number ('7777') and does NOT contain 'Check server logs'.
        """
        from sqlalchemy import text
        import app.routers.ingest as ing
        from fastapi import HTTPException

        ang = f"{self._PFX}007777"
        session = _v2_session_or_skip()
        client = _client_as(_ADMIN, session=session)
        try:
            # Remove any stale rows from previous runs.
            self._clean(session)
            session.execute(text(
                "DELETE FROM dim_gl_account WHERE account_number_group=:ang"
            ), {"ang": ang})
            session.execute(text(
                "DELETE FROM ovr_account_mapping WHERE account_number_group=:ang"
            ), {"ang": ang})
            session.commit()

            rows_data = [
                {"Doc": "1", "Account": "7777", "Date": "01.01.2022", "Amount": "500"}
            ]
            fid = self._stage_ob(client, rows_data)
            body = ing.OpeningBalanceCommitRequest(
                file_id=fid, profile=_gl_profile(self._PFX), scope="all"
            )

            with pytest.raises(HTTPException) as ei:
                ing.opening_balance_commit(body, session=session, _admin=_ADMIN)

            exc = ei.value
            assert exc.status_code == 422, (
                f"expected 422 for unresolvable account; got {exc.status_code}"
            )
            # Detail must name the bare account number stripped of prefix and
            # leading zeros: ang '98007777' -> bare '7777'.
            assert "7777" in exc.detail, (
                f"expected bare account '7777' in 422 detail; got: {exc.detail!r}"
            )
            # Must be a user-friendly message, not a traceback hint.
            assert "Check server logs" not in exc.detail, (
                f"detail must not say 'Check server logs'; got: {exc.detail!r}"
            )

        finally:
            # No fact rows were written (422 rolled back); only clean seed data.
            session.execute(text(
                "DELETE FROM dim_gl_account WHERE account_number_group=:ang"
            ), {"ang": ang})
            session.execute(text(
                "DELETE FROM ovr_account_mapping WHERE account_number_group=:ang"
            ), {"ang": ang})
            session.commit()
            session.close()


# --------------------------------------------------------------------------- #
# Cross-entity shared-group-chart clone — FINANCIAL PROOF
# --------------------------------------------------------------------------- #
#
# RULE (in words): a group member sharing ONE chart of accounts may carry an
# opening balance for a balance-sheet account that has NO GL movement in this
# entity (so no dim_gl_account row and no library precedent).  On OB commit we
# take ONLY the chart CLASSIFICATION (level_0..4 / l4_sub / sort / is_ic + NA/CF)
# from a sibling entity that shares the 6-char account suffix (classification is
# entity-invariant).  The opening-balance VALUE is NEVER sourced from another
# entity — it comes SOLELY from THIS entity's own OB row.
#
# WORKED EXAMPLE (mirrors Calypto / entity 02 account 11720):
#   sibling entity '95' has '95009901' (bare '9901') classified BS / Fixed assets
#   / Machinery.  Committing entity '96' uploads an OB for '9901' worth 777.00 and
#   has NO dim row for it anywhere.  After commit: dim '96009901' carries the
#   BS/Fixed-assets classification CLONED from '95', while the loaded OB amount
#   equals ONLY '96''s own 777.00 and ZERO OB value is written under '95'.
#
# SCOPE (M2): this class proves the sibling clone — the
# ``fill_account_rows_for_keys`` "no name for THIS ang anywhere" path.  The
# DISTINCT branch — an account that DOES have a name from another year of the
# SAME committing entity but no library/override precedent — resolves to
# ``unresolved_no_resolution`` (etl/account_fill.py:261-268) and is covered by
# etl/tests/test_account_fill.py::TestUnresolvedNoResolution, not here, since this
# proof class's subject is entity-invariance of the borrowed classification.
class TestOBCrossEntityCloneProof:
    """Proves the shared-group-chart fallback: classification is borrowed, VALUE is not."""

    _SRC = "95"   # sibling with the classified chart row (the '01' analogue)
    _TGT = "96"   # committing entity (the '02' analogue)
    _SUF = "009901"  # obscure suffix -> bare account '9901', unlikely to collide
    _OB_AMOUNT = 777.0

    def _src_ang(self) -> str:
        return f"{self._SRC}{self._SUF}"

    def _tgt_ang(self) -> str:
        return f"{self._TGT}{self._SUF}"

    def _clean(self, session) -> None:
        for p in (self._SRC, self._TGT):
            session.execute(text(
                "DELETE FROM fact_gl_line "
                "WHERE SUBSTR(journal_entry_group_number,1,2)=:p"
            ), {"p": p})
            session.execute(text(
                "DELETE FROM fact_gl_entry "
                "WHERE SUBSTR(journal_entry_group_number,1,2)=:p"
            ), {"p": p})
            session.execute(text(
                "DELETE FROM org_meta_dataset_load "
                "WHERE dataset='opening_balance' AND :p = ANY(scope_entity_prefixes)"
            ), {"p": p})
            session.execute(text(
                "DELETE FROM dim_gl_account WHERE SUBSTR(account_number_group,1,2)=:p"
            ), {"p": p})
            session.execute(text(
                "DELETE FROM dim_legal_entity WHERE entity_prefix=:p"
            ), {"p": p})
        session.commit()

    def _stage_ob(self, client: TestClient, rows: list[dict]) -> str:
        raw = _xlsx_bytes(rows)
        resp = client.post(
            "/api/v1/ingest/opening-balance/upload",
            files={"file": ("ob.xlsx", raw, "application/octet-stream")},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["file_id"]

    def test_classification_cloned_but_ob_value_never_borrowed(self):
        import app.routers.ingest as ing

        session = _testdb_session_or_skip()
        client = _client_as(_ADMIN, session=session)
        src, tgt = self._src_ang(), self._tgt_ang()
        try:
            self._clean(session)

            # Sibling '95' carries the FULL BS/Fixed-asset classification for FY2022.
            # The committing entity '96' has NO dim row for the account anywhere.
            session.execute(text(
                "INSERT INTO dim_gl_account "
                "(account_number_group, fiscal_year, gl_account_id, account_name, "
                " level_0, level_1, level_2, level_3, level_4, l4_sub, "
                " level_2_sort, level_3_sort, is_ic, source_system) "
                "VALUES (:ang, 2022, '9901', 'Machinery', "
                "        'BS', 'Assets', 'Fixed assets', 'Machinery', NULL, NULL, "
                "        30, 301, FALSE, 'group_chart_proof') "
                "ON CONFLICT (account_number_group, fiscal_year) DO NOTHING"
            ), {"ang": src})
            session.commit()

            # '96' uploads an OB for the same bare account '9901' worth 777.00.
            rows_data = [
                {"Doc": "1", "Account": "9901", "Date": "01.01.2022",
                 "Amount": str(self._OB_AMOUNT)}
            ]
            fid = self._stage_ob(client, rows_data)
            body = ing.OpeningBalanceCommitRequest(
                file_id=fid, profile=_gl_profile(self._TGT), scope="all"
            )
            res = ing.opening_balance_commit(body, session=session, _admin=_ADMIN)

            assert res.fiscal_years == [2022]
            assert res.lines == 1

            # (1) CLASSIFICATION borrowed: dim '96009901' now exists and its
            #     hierarchy equals the SIBLING '95''s classification.
            dim = session.execute(text(
                "SELECT level_0, level_1, level_2, level_3, level_2_sort, "
                "       level_3_sort, is_ic "
                "FROM dim_gl_account WHERE account_number_group=:ang AND fiscal_year=2022"
            ), {"ang": tgt}).fetchone()
            assert dim is not None, "cross-entity clone must create the target dim row"
            assert dim.level_0 == "BS"
            assert dim.level_2 == "Fixed assets"
            assert dim.level_3 == "Machinery"
            src_dim = session.execute(text(
                "SELECT level_0, level_2, level_3 FROM dim_gl_account "
                "WHERE account_number_group=:ang AND fiscal_year=2022"
            ), {"ang": src}).fetchone()
            assert (dim.level_0, dim.level_2, dim.level_3) == (
                src_dim.level_0, src_dim.level_2, src_dim.level_3
            ), "target classification must equal the sibling's (entity-invariant)"

            # (2) VALUE never borrowed: the loaded OB amount under '96' equals ONLY
            #     '96''s own OB row (777.00), and ZERO OB value is written under '95'.
            tgt_sum = session.execute(text(
                "SELECT COALESCE(SUM(amount),0) FROM fact_gl_line "
                "WHERE SUBSTR(journal_entry_group_number,1,2)=:p AND fiscal_year=2022"
            ), {"p": self._TGT}).scalar()
            assert abs(float(tgt_sum)) == self._OB_AMOUNT, (
                f"committing entity's OB value must equal its own row {self._OB_AMOUNT}; "
                f"got {tgt_sum}"
            )
            src_cnt = session.execute(text(
                "SELECT COUNT(*) FROM fact_gl_line "
                "WHERE SUBSTR(journal_entry_group_number,1,2)=:p"
            ), {"p": self._SRC}).scalar()
            assert int(src_cnt) == 0, (
                "NO opening-balance value may be written under the sibling entity "
                f"'{self._SRC}'; the classification is shared, the amount is not"
            )

            # (3) Idempotent re-run: 0 new dim rows, unchanged fact value.
            fid2 = self._stage_ob(client, rows_data)
            body2 = ing.OpeningBalanceCommitRequest(
                file_id=fid2, profile=_gl_profile(self._TGT), scope="all"
            )
            ing.opening_balance_commit(body2, session=session, _admin=_ADMIN)
            dim_cnt = session.execute(text(
                "SELECT COUNT(*) FROM dim_gl_account WHERE account_number_group=:ang"
            ), {"ang": tgt}).scalar()
            assert int(dim_cnt) == 1, "re-commit must not duplicate the cloned dim row"
            tgt_sum2 = session.execute(text(
                "SELECT COALESCE(SUM(amount),0) FROM fact_gl_line "
                "WHERE SUBSTR(journal_entry_group_number,1,2)=:p AND fiscal_year=2022"
            ), {"p": self._TGT}).scalar()
            assert abs(float(tgt_sum2)) == self._OB_AMOUNT

        finally:
            self._clean(session)
            session.close()

    def test_account_absent_from_every_entity_still_422(self):
        """Edge: a suffix that exists under NO entity stays truly_unmapped -> 422."""
        import app.routers.ingest as ing
        from fastapi import HTTPException

        session = _testdb_session_or_skip()
        client = _client_as(_ADMIN, session=session)
        bare = "9902"
        ang = f"{self._TGT}00{bare}"
        try:
            self._clean(session)
            session.execute(text(
                "DELETE FROM dim_gl_account WHERE SUBSTR(account_number_group,3)=:s"
            ), {"s": f"00{bare}"})
            session.commit()

            rows_data = [
                {"Doc": "1", "Account": bare, "Date": "01.01.2022", "Amount": "100"}
            ]
            fid = self._stage_ob(client, rows_data)
            body = ing.OpeningBalanceCommitRequest(
                file_id=fid, profile=_gl_profile(self._TGT), scope="all"
            )
            with pytest.raises(HTTPException) as ei:
                ing.opening_balance_commit(body, session=session, _admin=_ADMIN)
            assert ei.value.status_code == 422
            assert bare in ei.value.detail
        finally:
            self._clean(session)
            session.close()
