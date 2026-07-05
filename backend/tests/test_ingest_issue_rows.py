"""POST /validate/issue-rows — S1 field failures + B1 booking drill-down.

Boundary: monkeypatch ``_load_and_apply_cached`` so the handler runs on a
synthetic canonical frame (no file IO / DB). Profile uses linking_strategy
'none' so ``D.link_partners`` passes the frame through unchanged.
Synthetic fixtures only — no real client data.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app.routers.ingest as ing
from app.auth import User, current_user, require_admin
from app.db import get_session
from app.main import app

_ADMIN = User(user_id=1, email="admin@test", display_name="Admin", is_admin=True)

_URL = "/api/v1/ingest/validate/issue-rows"


def _client() -> TestClient:
    app.dependency_overrides[get_session] = lambda: MagicMock()
    app.dependency_overrides[current_user] = lambda: _ADMIN
    app.dependency_overrides[require_admin] = lambda: _ADMIN
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _gl_frame():
    """Two bookings under entity 01: B100 balances; B200 is a B1 offender (+30).

    Row 4 (booking B200) has an empty account_number_group -> a single S1 failure
    on that field while rows 1-3 pass (reference rows available).
    """
    canonical = pd.DataFrame({
        "booking_line_id": [1, 2, 3, 4],
        "journal_entry_group_number": ["01B100", "01B100", "01B200", "01B200"],
        "fiscal_year": [2020, 2020, 2020, 2020],
        "line_number": [1, 2, 1, 2],
        "account_number_group": ["01041100", "01070000", "01041100", ""],
        "amount": [100.0, -100.0, 50.0, -20.0],
        "posting_date": ["2020-03-01", "2020-03-01", "2020-03-01", "2020-03-01"],
        "fiscal_period": [3, 3, 3, 3],
        "gl_account_id": ["41100", "70000", "41100", "70000"],
        "account_class": ["other", "other", "other", "other"],
    })
    raw = pd.DataFrame({
        "Account": ["41100", "70000", "41100", ""],
        "Amount": [100, -100, 50, -20],
    })
    return canonical, raw


def _patch_frames(monkeypatch):
    canonical, raw = _gl_frame()
    monkeypatch.setattr(ing, "_load_and_apply_cached", lambda *a, **k: (canonical, {}, raw))


def test_issue_rows_b1_returns_booking_lines_and_summary(monkeypatch):
    _patch_frames(monkeypatch)
    resp = _client().post(_URL, json={
        "file_id": "x", "profile": {"linking_strategy": "none"},
        "check_id": "B1", "field": "amount",
        "journal_entry_group_number": "01B200", "fiscal_year": 2020,
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert [r["booking_line_id"] for r in body["rows"]] == ["3", "4"]
    # 50 + (-20) = 30 — the imbalance, not 0
    assert body["summary_row"] == {"amount": 30.0}
    assert body["stats"]["amount_sum"] == 30.0
    # Reference rows are not meaningful for a single booking.
    assert body["reference_available"] is False
    assert body["reference_rows"] == []


def test_issue_rows_b1_respects_exclusions(monkeypatch):
    _patch_frames(monkeypatch)
    resp = _client().post(_URL, json={
        "file_id": "x", "profile": {"linking_strategy": "none"},
        "check_id": "B1", "field": "amount",
        "journal_entry_group_number": "01B200", "fiscal_year": 2020,
        "exclude_line_ids": [4],
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [r["booking_line_id"] for r in body["rows"]] == ["3"]
    assert body["summary_row"] == {"amount": 50.0}


def test_issue_rows_s1_unchanged_and_reference_available(monkeypatch):
    _patch_frames(monkeypatch)
    resp = _client().post(_URL, json={
        "file_id": "x", "profile": {"linking_strategy": "none"},
        "check_id": "S1", "field": "account_number_group",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["rows"][0]["booking_line_id"] == "4"
    # additive contract fields present; S1 has no per-booking summary
    assert body["summary_row"] is None
    assert body["reference_available"] is True   # rows 1-3 pass the field
    assert body["reference_kind"] == "same_field"
    assert len(body["reference_rows"]) == 3
    # FULL offender set across all pages (here just the one empty row).
    assert body["offender_ids"] == ["4"]
    assert body["total_offenders"] == 1


def test_issue_rows_s1_all_failing_field_still_has_reference(monkeypatch):
    """Bug B fix: when the checked field is ENTIRELY empty there is no same-field
    passing row, but the other required fields are populated so a reference is
    still surfaced via the 'overall' (best-populated) fallback."""
    canonical, raw = _gl_frame()
    canonical["account_number_group"] = ["", "", "", ""]   # every row fails this field
    raw["Account"] = ["", "", "", ""]
    monkeypatch.setattr(ing, "_load_and_apply_cached", lambda *a, **k: (canonical, {}, raw))
    resp = _client().post(_URL, json={
        "file_id": "x", "profile": {"linking_strategy": "none"},
        "check_id": "S1", "field": "account_number_group",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 4
    assert body["offender_ids"] == ["1", "2", "3", "4"]
    assert body["total_offenders"] == 4
    assert body["reference_available"] is True
    assert body["reference_kind"] == "overall"
    assert body["reference_rows"]
    for ref in body["reference_rows"]:
        assert set(ref.keys()) == set(body["columns"])


def test_issue_rows_unsupported_check_400(monkeypatch):
    _patch_frames(monkeypatch)
    resp = _client().post(_URL, json={
        "file_id": "x", "profile": {"linking_strategy": "none"},
        "check_id": "B2", "field": "amount",
    })
    assert resp.status_code == 400
    assert "B2" in resp.json()["detail"]


def test_s1_regression_guard_no_degenerate_response(monkeypatch):
    """Regression guard for the 'empty rows' S1 UI-blank bug.

    The degenerate response shape that blanked the UI was:
        columns == ["booking_line_id"]  AND  reference_rows == []

    This test asserts the COMPLETE contract for an S1 failure on
    account_number_group so any future regression that returns the degenerate
    shape fails here immediately.

    Fixture: 4 synthetic rows — rows 1-3 pass account_number_group; row 4
    (booking B200, line 2) has an empty account_number_group (S1 failure) and
    amount=-20.0.  The passing rows become reference_rows (same_field strategy).
    """
    _patch_frames(monkeypatch)
    resp = _client().post(_URL, json={
        "file_id": "x", "profile": {"linking_strategy": "none"},
        "check_id": "S1", "field": "account_number_group",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # --- 1. columns is non-degenerate (THE regression guard) -----------------
    columns = body["columns"]
    assert columns != ["booking_line_id"], (
        "Degenerate columns shape detected: S1 response returned only "
        "['booking_line_id']. This is exactly the regression that blanked the UI."
    )
    assert len(columns) > 1, "columns must contain more than just booking_line_id"
    assert "reference" in columns, (
        "'reference' synthetic display column must always be present"
    )
    assert "account_number_group" in columns, (
        "the failing field must appear in the projected columns"
    )
    assert "amount" in columns, (
        "'amount' must be a projected canonical column"
    )

    # --- 2. rows non-empty; first failing row carries REAL values ------------
    assert body["rows"], "rows must not be empty when S1 offenders exist"
    first_row = body["rows"][0]
    # The failing row (B200 line 2) has amount=-20.0 in the fixture.
    assert first_row.get("amount") is not None, (
        "first failing row must carry a real 'amount' value (not None)"
    )
    # The S1-failing field must be empty or null on the offending row.
    assert first_row.get("account_number_group") in (None, ""), (
        "first failing row must have account_number_group empty/null "
        f"(got {first_row.get('account_number_group')!r})"
    )

    # --- 3. reference_rows non-empty with REAL values for failing field ------
    assert body["reference_rows"], (
        "reference_rows must not be empty when passing rows exist"
    )
    first_ref = body["reference_rows"][0]
    assert first_ref.get("account_number_group") not in (None, ""), (
        "reference row must carry a real (non-empty) account_number_group; "
        f"got {first_ref.get('account_number_group')!r}"
    )
    # Reference rows must be projected onto the SAME column set as rows.
    assert set(first_ref.keys()) == set(columns), (
        "reference_rows must use the same column set as rows"
    )

    # --- 4. reference_kind and reference_available ---------------------------
    assert body["reference_kind"] in ("same_field", "overall"), (
        f"reference_kind must be 'same_field' or 'overall', "
        f"got {body['reference_kind']!r}"
    )
    assert body["reference_available"] is True, (
        "reference_available must be True when reference_rows exist"
    )

    # --- 5. Complete contract: every key the frontend consumes ---------------
    required_keys = {
        "stats", "columns", "rows", "total",
        "reference_rows", "reference_kind", "reference_available",
        "offender_ids", "total_offenders", "failing_field", "column_labels",
    }
    missing_keys = required_keys - set(body.keys())
    assert not missing_keys, (
        f"Missing required contract keys in S1 response: {sorted(missing_keys)}"
    )

    # --- 6. column_labels non-empty; offender counts consistent --------------
    assert body["column_labels"], "column_labels must not be empty"
    assert body["offender_ids"], "offender_ids must not be empty"
    assert body["total_offenders"] == len(body["offender_ids"]), (
        "total_offenders must equal len(offender_ids)"
    )

    # --- 7. failing_field matches the requested field ------------------------
    assert body["failing_field"] == "account_number_group", (
        f"failing_field must equal the requested field, "
        f"got {body['failing_field']!r}"
    )


# ================================================================= Precision bug: 62-bit ids serialized as JSON numbers (JS float64 loss)
#
# booking_line_id is a 62-bit BLAKE2b hash masked to 62 bits
# (etl/mapping._BLID_MASK = (1<<62)-1).  Values > 2^53 = 9007199254740991
# (= JS Number.MAX_SAFE_INTEGER) are rounded when a JSON NUMBER is parsed by
# JavaScript's float64:
#
#   e.g. 2003686083711855065  -> float64 -> 2003686083711855104 (WRONG)
#
# IssueRowsResponse.offender_ids is currently typed list[int] -> Pydantic
# serializes as JSON numbers -> JS reads the wrong id -> posts the wrong id back
# in exclude_line_ids -> server can't match -> "Exclude all N" is a no-op.
#
# The fix: offender_ids and per-row booking_line_id must be STRINGS in the JSON
# response; exclude_line_ids must accept strings; server matches by string.
#
# RED today: tests asserting the STRING contract (fail until fix applied).
# GREEN today: tests documenting the live bug / already-correct behaviour.

_BLID = 2003686083711855065    # > 2^53; float64 rounds to wrong value
_BLID_REF = _BLID + 1000       # passing row; also > 2^53, distinct


def _huge_blid_frame():
    """One S1-failing row (empty account_number_group) with booking_line_id=_BLID;
    one passing row with id=_BLID_REF that becomes a reference row."""
    canonical = pd.DataFrame({
        "booking_line_id": pd.Series([_BLID, _BLID_REF], dtype="int64"),
        "journal_entry_group_number": ["01B100", "01B200"],
        "fiscal_year": [2020, 2020],
        "line_number": [1, 1],
        "account_number_group": ["", "01041100"],   # first fails, second passes
        "amount": [10.0, 20.0],
        "posting_date": ["2020-01-15", "2020-01-15"],
        "fiscal_period": [1, 1],
        "gl_account_id": ["41100", "41100"],
        "account_class": ["other", "other"],
    })
    raw = pd.DataFrame({"Account": ["", "41100"]})
    return canonical, raw


def _patch_huge(monkeypatch):
    canonical, raw = _huge_blid_frame()
    monkeypatch.setattr(ing, "_load_and_apply_cached", lambda *a, **k: (canonical, {}, raw))


def test_huge_id_offender_ids_are_json_strings(monkeypatch):
    """RED today: IssueRowsResponse.offender_ids is list[int] -> JSON numbers.

    After the fix offender_ids must be JSON STRINGS so the exact digit sequence
    round-trips through JavaScript without float64 precision loss.
    """
    import json as _json

    assert int(float(_BLID)) != _BLID, (
        "sanity: _BLID must be unrepresentable in float64 (proves the bug is real)"
    )
    _patch_huge(monkeypatch)
    resp = _client().post(_URL, json={
        "file_id": "x", "profile": {"linking_strategy": "none"},
        "check_id": "S1", "field": "account_number_group",
    })
    assert resp.status_code == 200, resp.text

    expected_str = str(_BLID)

    # The digit string must appear QUOTED in the raw JSON text (as a JSON string,
    # not a bare JSON number).  A bare JSON number would NOT have surrounding quotes.
    assert f'"{expected_str}"' in resp.text, (
        f"offender_ids[0] must appear as a JSON string (quoted) in the response. "
        f"Found unquoted number instead -> JavaScript will round it to the wrong value. "
        f"Response excerpt: {resp.text[:500]}"
    )

    # Parse with json.loads (standard library) to check the Python types that mirror
    # what JavaScript sees when it reads the JSON.
    body = _json.loads(resp.text)
    assert body["offender_ids"], "offender_ids must not be empty"
    for oid in body["offender_ids"]:
        assert isinstance(oid, str), (
            f"offender_ids elements must be JSON strings (parsed as str). "
            f"Got {type(oid).__name__}: {oid!r}. "
            "RED: current Pydantic model has list[int] -> JSON number."
        )
    assert body["offender_ids"] == [expected_str], (
        f"offender_ids must be ['{expected_str}'] (exact string). "
        f"Got: {body['offender_ids']!r}"
    )


def test_huge_id_rows_booking_line_id_is_json_string(monkeypatch):
    """RED today: _booking_line_id_at returns int -> serialized as JSON number.

    After the fix rows[*].booking_line_id must be a JSON STRING in the response.
    """
    import json as _json

    _patch_huge(monkeypatch)
    resp = _client().post(_URL, json={
        "file_id": "x", "profile": {"linking_strategy": "none"},
        "check_id": "S1", "field": "account_number_group",
    })
    assert resp.status_code == 200, resp.text

    body = _json.loads(resp.text)
    assert body["rows"], "expected at least one failing row"
    for row in body["rows"]:
        assert isinstance(row["booking_line_id"], str), (
            f"rows[*].booking_line_id must be JSON string (str). "
            f"Got {type(row['booking_line_id']).__name__}: {row['booking_line_id']!r}. "
            "RED: current code returns int from _booking_line_id_at."
        )
    assert body["rows"][0]["booking_line_id"] == str(_BLID), (
        f"rows[0].booking_line_id must equal '{_BLID}'. "
        f"Got: {body['rows'][0]['booking_line_id']!r}"
    )


def test_huge_id_reference_rows_booking_line_id_is_json_string(monkeypatch):
    """RED today: reference row booking_line_id is also serialized as JSON number.

    After the fix reference_rows[*].booking_line_id must be a JSON STRING.
    """
    import json as _json

    _patch_huge(monkeypatch)
    resp = _client().post(_URL, json={
        "file_id": "x", "profile": {"linking_strategy": "none"},
        "check_id": "S1", "field": "account_number_group",
    })
    assert resp.status_code == 200, resp.text

    body = _json.loads(resp.text)
    assert body["reference_rows"], "expected at least one reference row"
    for ref in body["reference_rows"]:
        assert isinstance(ref["booking_line_id"], str), (
            f"reference_rows[*].booking_line_id must be JSON string (str). "
            f"Got {type(ref['booking_line_id']).__name__}: {ref['booking_line_id']!r}. "
            "RED: same _booking_line_id_at -> int path."
        )


def test_huge_id_js_rounded_exclusion_is_noop(monkeypatch):
    """GREEN today (documents the live bug) — must stay GREEN after the fix.

    Sending the JS-rounded (float64) id in exclude_line_ids does not remove the
    offender because the rounded value differs from the exact row id.
    After the fix the browser will send the string id (exact), so this broken path
    is never exercised — but the assertion must remain true.
    """
    import json as _json

    _patch_huge(monkeypatch)
    js_rounded = int(float(_BLID))     # simulate JavaScript float64 coercion
    assert js_rounded != _BLID, "sanity: float64 must round this id"

    resp = _client().post(_URL, json={
        "file_id": "x", "profile": {"linking_strategy": "none"},
        "check_id": "S1", "field": "account_number_group",
        "exclude_line_ids": [js_rounded],   # wrong rounded id (what JS currently sends)
    })
    assert resp.status_code == 200, resp.text
    body = _json.loads(resp.text)
    assert body["total"] == 1, (
        f"Exclusion with JS-rounded id {js_rounded} must be a no-op "
        f"(exact id is {_BLID}). Got total={body['total']}."
    )


def test_huge_id_exact_string_exclusion_removes_offender(monkeypatch):
    """Locks the string-exclusion round-trip required after the fix.

    After the fix: the browser reads offender_ids as JSON strings, sends them back
    as strings in exclude_line_ids, the server matches by string comparison and
    drops the offending row.

    Currently: IssueRowsRequest.exclude_line_ids is list[int]; Pydantic v2 default
    (lax) mode coerces str -> int exactly for a properly-formatted digit string, so
    apply_line_exclusions receives the exact id and the exclusion works.  This test
    is GREEN today and must remain GREEN after the fix (via string comparison).
    """
    import json as _json

    _patch_huge(monkeypatch)
    resp = _client().post(_URL, json={
        "file_id": "x", "profile": {"linking_strategy": "none"},
        "check_id": "S1", "field": "account_number_group",
        "exclude_line_ids": [str(_BLID)],   # exact string id (what browser sends after fix)
    })
    assert resp.status_code == 200, resp.text
    body = _json.loads(resp.text)
    assert body["total"] == 0, (
        f"Posting the exact string id '{_BLID}' in exclude_line_ids must remove "
        f"the offending row. Got total={body['total']}."
    )
    assert body["offender_ids"] == [], (
        "offender_ids must be empty after the offender is excluded"
    )


# ================================================================= Commit-path round-trip (new)
_COMMIT_URL = "/api/v1/ingest/commit"


def _commit_frame():
    """One S1 offender (62-bit id, EMPTY account_number_group) + one valid row.

    The offender's empty account triggers commit()'s NULL-account HARD BLOCK
    (a 422 that fires REGARDLESS of confirm_soft) UNLESS it is excluded first, so
    a successful commit proves the offender was dropped by the exclusion.
    """
    canonical = pd.DataFrame({
        "booking_line_id": pd.Series([_BLID, _BLID + 7], dtype="int64"),
        "journal_entry_group_number": ["01B100", "01B200"],
        "fiscal_year": [2020, 2020],
        "line_number": [1, 1],
        "account_number_group": ["", "01041100"],   # offender has NO account
        "amount": [0.0, 100.0],
        "posting_date": ["2020-01-15", "2020-01-15"],
        "fiscal_period": [1, 1],
        "gl_account_id": ["", "41100"],
        "account_class": ["other", "other"],
    })
    raw = pd.DataFrame({"Account": ["", "41100"]})
    return canonical, raw


def _capture_load_canonical(monkeypatch) -> dict:
    """Patch frames + capture the frame that reaches load_canonical (no DB)."""
    # Disable the post-commit background narrative warm — it spawns a thread that
    # opens a real DB connection (not relevant to this exclusion round-trip test).
    monkeypatch.setenv("NARRATIVE_WARM_ON_INGEST", "0")
    canonical, raw = _commit_frame()
    monkeypatch.setattr(ing, "_load_and_apply_cached", lambda *a, **k: (canonical, {}, raw))
    captured: dict = {}

    def _fake_load_canonical(session, lines, **kwargs):
        captured["lines"] = lines.copy()
        return {
            "load_id": 1, "entries": 1, "lines": int(len(lines)),
            "ar": 0, "ap": 0, "sales": 0, "com": 0, "skipped": 0,
            "loaded_at": "2026-01-01T00:00:00+00:00", "commit_mode": "replace",
        }

    monkeypatch.setattr(ing, "load_canonical", _fake_load_canonical)
    return captured


def test_commit_exact_string_exclusion_drops_offender_from_loaded_rows(monkeypatch):
    """Commit-path: posting the exact 62-bit id STRING in exclude_line_ids drops the
    offending row BEFORE load. A 200 (not the NULL-account 422) plus the offender's
    absence from the captured load frame proves the string round-trip works."""
    captured = _capture_load_canonical(monkeypatch)
    resp = _client().post(_COMMIT_URL, json={
        "file_id": "x",
        "profile": {"linking_strategy": "none"},
        "exclude_line_ids": [str(_BLID)],   # exact digit string (what the browser sends)
        "confirm_soft": True,
    })
    assert resp.status_code == 200, resp.text
    loaded = captured["lines"]
    loaded_ids = set(loaded["booking_line_id"].astype("int64").astype(str))
    assert str(_BLID) not in loaded_ids, (
        f"offender id {_BLID} must be dropped from the loaded rows by string exclusion"
    )
    assert int(len(loaded)) == 1
    assert list(loaded["account_number_group"]) == ["01041100"]


def test_commit_without_exclusion_blocks_on_null_account(monkeypatch):
    """Control: with NO exclusion the empty-account offender survives and commit()
    hard-blocks (422) — proving the offending row is genuinely present pre-exclusion."""
    _capture_load_canonical(monkeypatch)
    resp = _client().post(_COMMIT_URL, json={
        "file_id": "x",
        "profile": {"linking_strategy": "none"},
        "confirm_soft": True,
    })
    assert resp.status_code == 422, resp.text
    assert "no account number" in resp.text
