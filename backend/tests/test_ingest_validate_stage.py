"""POST /api/v1/ingest/validate — stage-aware check selection (D2).
POST /api/v1/ingest/validate/issue-rows — reference_rows in response (D3a).

Covers (DB-free; staging only — MagicMock session like test_ingest_combine):
  * stage='gl'     : results contain ONLY S1,S2,B1,B3,B2,Q2; M1 and R1-R4 absent.
  * stage='gl'     : succeeds against empty/mocked DB (no dim_gl_account query made).
  * stage=None     : REGRESSION — full catalog returned, same id-set and order as
                     STAGE_CHECKS['all'] (Data Update no-regression guard).
  * stage='partner': exactly R1, R2 in results; nothing else.
  * stage='coa'    : exactly M1, R3, R4 in results; nothing else.
  * issue-rows     : response schema includes reference_rows as a list (D3a contract).

Design notes
------------
All tests are DB-free: get_session is overridden with MagicMock().  MagicMock
iteration returns iter([]) by default, so dim_gl_account / dim_legal_entity
queries gracefully return empty sequences rather than raising.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

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


# ---------------------------------------------------------------------------
# Synthetic GL fixture
# ---------------------------------------------------------------------------
# Two balanced bookings, March 2024 (fiscal_period=3 derived from posting_date).
#   Booking 1: account 10000 +600, account 80000 -600  -> sum = 0
#   Booking 2: account 30000 +400, account 70000 -400  -> sum = 0
# No real customer data; account numbers are standard DATEV ranges.
_GL_CSV = """\
Tx,Account,Amount,PostingDate,SourceType,SourceNo
1,10000,600.00,15.03.2024,Debitor,100
1,80000,-600.00,15.03.2024,,
2,30000,400.00,20.03.2024,,
2,70000,-400.00,20.03.2024,Kreditor,200
"""

# Mapping profile that matches the synthetic CSV above.
_PROFILE: dict = {
    "entity": {"mode": "fixed", "value": "01"},
    "fiscal_year": {"mode": "from_date", "value": None},
    "sign": {"mode": "signed", "amount": "Amount"},
    "decimal": ".",
    "thousands": ",",
    "date_dayfirst": True,
    "columns": {
        "journal_entry_number": "Tx",
        "account_number": "Account",
        "posting_date": "PostingDate",
        "source_type": "SourceType",
        "source_no": "SourceNo",
    },
    "linking_strategy": "txn",
    "entry_type": "actual",
    "source_system": "test",
}

# Sentinel so _validate() can distinguish "caller did not pass stage" from stage=None.
_UNSET = object()


def _upload(client: TestClient, csv_content: str = _GL_CSV) -> str:
    """Upload synthetic GL CSV and return the staging file_id."""
    resp = client.post(
        "/api/v1/ingest/upload",
        files={"file": ("test_gl.csv", csv_content.encode("utf-8"), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["file_id"]


def _validate(client: TestClient, file_id: str, stage=_UNSET):
    """POST /validate; omit 'stage' key entirely when caller did not pass one."""
    body: dict = {"file_id": file_id, "profile": _PROFILE}
    if stage is not _UNSET:
        body["stage"] = stage
    return client.post("/api/v1/ingest/validate", json=body)


# ---------------------------------------------------------------------------
# Feature A — stage-aware check catalog (D2)
# ---------------------------------------------------------------------------
class TestValidateStage:
    def test_stage_gl_only_returns_gl_check_ids(self):
        """stage='gl' => results contain only S1,S2,B1,B3,B2,Q2; M1 and R1-R4 absent."""
        client = _client_as(_ADMIN)
        fid = _upload(client)
        resp = _validate(client, fid, stage="gl")
        assert resp.status_code == 200, resp.text
        ids = {r["id"] for r in resp.json()["results"]}
        assert ids == {"S1", "S2", "B1", "B3", "B2", "Q2"}
        assert "M1" not in ids
        for rid in ("R1", "R2", "R3", "R4"):
            assert rid not in ids, f"{rid} must not appear for stage='gl'"

    def test_stage_gl_succeeds_without_dim_gl_account_table(self):
        """stage='gl' must NOT query dim_gl_account at all — succeeds on a fresh/empty DB."""
        client = _client_as(_ADMIN)
        fid = _upload(client)
        # MagicMock session: no real DB; M1 would need dim_gl_account but is not in scope
        resp = _validate(client, fid, stage="gl")
        assert resp.status_code == 200, resp.text
        # Confirm M1 is entirely absent — not just failing, but not run at all
        result_ids = {r["id"] for r in resp.json()["results"]}
        assert "M1" not in result_ids

    def test_no_stage_returns_full_catalog_id_set(self):
        """REGRESSION: omitting stage must return all check IDs (Data Update no-regression guard)."""
        from etl.checks import STAGE_CHECKS
        client = _client_as(_ADMIN)
        fid = _upload(client)
        resp = _validate(client, fid)  # stage key not sent -> ValidateRequest.stage defaults to None
        assert resp.status_code == 200, resp.text
        ids = {r["id"] for r in resp.json()["results"]}
        assert ids == set(STAGE_CHECKS["all"])

    def test_no_stage_order_matches_stage_checks_all(self):
        """REGRESSION: response id order must match STAGE_CHECKS['all'] exactly (byte-stable)."""
        from etl.checks import STAGE_CHECKS
        client = _client_as(_ADMIN)
        fid = _upload(client)
        resp = _validate(client, fid)  # no stage
        ids_ordered = [r["id"] for r in resp.json()["results"]]
        assert ids_ordered == STAGE_CHECKS["all"], (
            f"Order mismatch.\n  Got:      {ids_ordered}\n  Expected: {STAGE_CHECKS['all']}"
        )

    def test_stage_partner_returns_r1_r2_only(self):
        """stage='partner' => results contain exactly R1 and R2; no other check ids."""
        client = _client_as(_ADMIN)
        fid = _upload(client)
        resp = _validate(client, fid, stage="partner")
        assert resp.status_code == 200, resp.text
        ids = {r["id"] for r in resp.json()["results"]}
        assert ids == {"R1", "R2"}

    def test_stage_coa_returns_m1_r3_r4_only(self):
        """stage='coa' => results contain exactly M1, R3, R4; no other check ids."""
        client = _client_as(_ADMIN)
        fid = _upload(client)
        resp = _validate(client, fid, stage="coa")
        assert resp.status_code == 200, resp.text
        ids = {r["id"] for r in resp.json()["results"]}
        assert ids == {"M1", "R3", "R4"}

    def test_requires_auth(self):
        """Unauthenticated requests must be rejected (401)."""
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/v1/ingest/validate",
            json={"file_id": "dummy", "profile": _PROFILE, "stage": "gl"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Feature B — reference_rows in issue-rows response (D3a)
# ---------------------------------------------------------------------------
class TestExclusionsIdsAreJsonStrings:
    """exclusions.active_line_ids must serialize as JSON STRINGS (quoted, exact
    digits) so a 62-bit booking_line_id (> 2**53) survives the JS float64 boundary.

    The Finish commit sources exclude_line_ids from active_line_ids, so a Number
    here would round a huge id and re-commit the wrong row (the live bug)."""

    _BID_HUGE = "2003686083711855065"  # > 2**53; float64 would round it

    def test_active_line_ids_serialize_as_quoted_strings(self):
        client = _client_as(_ADMIN)
        fid = _upload(client)
        # active_line_ids is derived from exclude_line_ids regardless of whether the
        # id matches a staged row, so this exercises the serialization contract.
        resp = client.post(
            "/api/v1/ingest/validate",
            json={
                "file_id": fid,
                "profile": _PROFILE,
                "stage": "gl",
                "exclude_line_ids": [self._BID_HUGE],
            },
        )
        assert resp.status_code == 200, resp.text
        # Quoted exact digits present in the raw JSON (not a bare Number).
        assert f'"{self._BID_HUGE}"' in resp.text
        active = resp.json()["exclusions"]["active_line_ids"]
        assert active == [self._BID_HUGE]
        assert all(isinstance(x, str) for x in active)


# ---------------------------------------------------------------------------
# Feature C — validate / commit error CONSISTENCY (defense-in-depth)
# ---------------------------------------------------------------------------
class TestValidateCommitErrorConsistency:
    """INVARIANT: when a required GoBD field (posting_date) is unmapped, the
    validate path and the commit path must fail the SAME way.

    Both endpoints funnel through _load_and_apply -> apply_profile, so validate
    can never silently pass while commit fails. This pins that contract and also
    guards that the surfaced message is plain-language (no Python syntax such as
    `profile.columns[...]` leaks to the user)."""

    @staticmethod
    def _profile_without_posting_date() -> dict:
        import copy

        prof = copy.deepcopy(_PROFILE)
        prof["columns"].pop("posting_date", None)
        return prof

    def test_validate_and_commit_fail_identically_when_posting_date_unmapped(self):
        client = _client_as(_ADMIN)
        fid = _upload(client)
        profile = self._profile_without_posting_date()

        v = client.post(
            "/api/v1/ingest/validate",
            json={"file_id": fid, "profile": profile, "stage": "gl"},
        )
        c = client.post(
            "/api/v1/ingest/commit",
            json={"file_id": fid, "profile": profile, "dataset": "gl"},
        )

        # Same status: validate cannot pass (2xx) while commit rejects (422).
        assert v.status_code == 422, v.text
        assert c.status_code == 422, c.text
        # Same human message — identical profile -> identical failure.
        assert v.json()["detail"] == c.json()["detail"]

    def test_required_field_error_is_plain_language(self):
        """Even if a raw message reaches the user, it must be readable: human field
        label present, no `profile.columns[...]` Python syntax."""
        client = _client_as(_ADMIN)
        fid = _upload(client)
        profile = self._profile_without_posting_date()

        v = client.post(
            "/api/v1/ingest/validate",
            json={"file_id": fid, "profile": profile, "stage": "gl"},
        )
        assert v.status_code == 422, v.text
        detail = v.json()["detail"]
        assert "profile.columns" not in detail
        assert "'Posting date'" in detail


class TestIssueRowsReferenceRows:
    def test_issue_rows_response_includes_reference_rows_key(self):
        """IssueRowsResponse must contain 'reference_rows' as a list (D3a schema contract)."""
        client = _client_as(_ADMIN)
        fid = _upload(client)
        resp = client.post("/api/v1/ingest/validate/issue-rows", json={
            "file_id": fid,
            "profile": _PROFILE,
            "check_id": "S1",
            "field": "account_number_group",
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "reference_rows" in body, "reference_rows key missing from IssueRowsResponse"
        assert isinstance(body["reference_rows"], list)
