"""POST /api/v1/ingest/commit — informative structured 422 for unmapped GL accounts.

The GL commit FK pre-flight verifies that EVERY observed
(account_number_group, fiscal_year) pair is mapped in dim_gl_account before the
load. The check is an ANTI-JOIN over the observed GL key pairs, so it covers BOTH
the all-unmapped case AND the partial-mapped case (the latter previously 500'd
later inside load_canonical on the FK).

When some keys are unmapped the endpoint raises a 422 whose detail is a structured
dict (code='gl_accounts_unmapped') LISTING the offending accounts so the frontend
can offer to apply the configured mapping library.  When all keys are mapped the
pre-flight passes and the commit proceeds unchanged.

Privacy: the pre-flight logs ONLY counts — never the account list or line_note
(booking text may carry PII).

DB-free: a small stub session returns the "mapped" key rows for the pre-flight
SELECT; the upload + parse path is real (synthetic CSV, no real customer data).

account_number_group = entity_prefix(2) + account.zfill(6)  (etl/transform.py),
so entity '01' + account '10000' -> '01010000'.
"""
from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.auth import User, current_user, require_admin
from app.db import get_session
from app.main import app

_ADMIN = User(user_id=1, email="admin@test", display_name="Admin", is_admin=True)

# Balanced-enough synthetic GL (confirm_soft bypasses soft blocking checks).
# Four standard DATEV-range accounts; no real customer data.
_GL_CSV = """\
Tx,Account,Amount,PostingDate,SourceType,SourceNo
1,10000,600.00,15.03.2024,,
2,80000,-600.00,15.03.2024,,
3,30000,400.00,20.03.2024,,
4,70000,-400.00,20.03.2024,,
"""

# Variant carrying a line note (booking text) to prove it never reaches the logs.
_PII_NOTE = "John Doe private invoice ref 0xDEADBEEF"
_GL_CSV_WITH_NOTE = f"""\
Tx,Account,Amount,PostingDate,SourceType,SourceNo,Note
1,10000,600.00,15.03.2024,,,{_PII_NOTE}
2,80000,-600.00,15.03.2024,,,ordinary booking
"""

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


def _profile_with_note() -> dict:
    prof = {k: (dict(v) if isinstance(v, dict) else v) for k, v in _PROFILE.items()}
    cols = dict(prof["columns"])
    cols["line_note"] = "Note"
    prof["columns"] = cols
    return prof


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _StubSession:
    """Returns configurable mapped (ang, fy) rows for the FK pre-flight SELECT.

    Recognised query patterns (in priority order):
    1. "fiscal_year FROM dim_gl_account" — used by:
         • the auto-expand INITIAL mapped-keys check (GL commit)
         • the FK pre-flight mapped-keys check (GL commit)
         • the preexisting-keys check INSIDE _replicate_dim_across_entities
       Returns _mapped_fn(angs, fys) when _mapped_fn is set, else [].

    2. "substr(a.account_number_group" — the source-lookup query inside
       _replicate_dim_across_entities that searches for a sibling entity's row
       to clone.  Returns [] so the auto-expand finds no source and every
       unmapped account passes through to the FK pre-flight 422 unchanged.

    3. Everything else (enrichment SELECT, visibility queries, etc.) -> [].

    The enrichment SELECT is NOT matched by pattern 1 because its column list
    is "account_number_group, fiscal_year, level_1, ..." (extra level cols
    appear BEFORE "FROM dim_gl_account"), so "fiscal_year FROM dim_gl_account"
    does not appear in that string.
    """

    def __init__(self, mapped_fn=None):
        self._mapped_fn = mapped_fn

    def execute(self, stmt, params=None):
        sql = str(stmt)
        # Pattern 1: mapped-keys checks (auto-expand initial pass, FK pre-flight,
        # preexisting check inside _replicate_dim_across_entities).
        if "fiscal_year FROM dim_gl_account" in sql and self._mapped_fn is not None:
            p = params or {}
            return _Result(self._mapped_fn(p.get("angs", []), p.get("fys", [])))
        # Pattern 2: source-lookup inside _replicate_dim_across_entities.
        # Returns [] so auto-expand finds no source row and never suppresses a
        # true orphan — the FK pre-flight 422 fires for it as normal.
        if "substr(a.account_number_group" in sql:
            return _Result([])
        return _Result([])

    def rollback(self):
        pass

    def commit(self):
        pass


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _client(session) -> TestClient:
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[current_user] = lambda: _ADMIN
    app.dependency_overrides[require_admin] = lambda: _ADMIN
    return TestClient(app, raise_server_exceptions=False)


def _upload(client: TestClient, csv: str) -> str:
    resp = client.post(
        "/api/v1/ingest/upload",
        files={"file": ("gl.csv", csv.encode("utf-8"), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["file_id"]


def _commit(client: TestClient, file_id: str, profile: dict | None = None):
    return client.post(
        "/api/v1/ingest/commit",
        json={
            "file_id": file_id,
            "profile": profile or _PROFILE,
            "dataset": "test_ds",
            "confirm_soft": True,
            "commit_mode": "replace",
        },
    )


# =========================================================================== #
# (a) empty dim -> 422, lists every account
# =========================================================================== #
def test_all_unmapped_returns_structured_422():
    session = _StubSession(mapped_fn=lambda angs, fys: [])  # nothing mapped
    client = _client(session)
    fid = _upload(client, _GL_CSV)

    resp = _commit(client, fid)
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, dict), detail
    assert detail["code"] == "gl_accounts_unmapped"
    assert detail["total_unmapped"] == 4
    assert detail["truncated"] is False
    # No endpoint path / SQL in the user-facing message.
    msg = detail["message"]
    assert msg.startswith("4 account(s) in this GL file aren't mapped yet")
    for leak in ("/api/", "SELECT", "dim_gl_account", "POST"):
        assert leak not in msg, f"leak in message: {msg!r}"

    angs = {u["account_number_group"] for u in detail["unmapped"]}
    assert angs == {"01010000", "01080000", "01030000", "01070000"}
    # Per-row shape: ORIGINAL account number + entity prefix + fiscal year.
    # The list is sorted by (entity_prefix, account, fiscal_year); account is now the
    # original source number (gl_account_id), so '10000' sorts first.
    first = detail["unmapped"][0]
    assert first["entity_prefix"] == "01"
    assert first["account_number_group"] == "01010000"
    # ORIGINAL account number (e.g. '10000'), NOT the zero-padded suffix '010000'.
    assert first["account"] == "10000"
    assert first["fiscal_year"] == 2024


# =========================================================================== #
# (b) partial mapping -> 422 lists ONLY the unmapped subset (NOT a 500)
# =========================================================================== #
def test_partial_mapping_lists_only_unmapped_subset():
    # Map every account EXCEPT '01010000' (entity '01' + account 10000).
    def _mapped(angs, fys):
        return [
            (str(a), int(f))
            for a in angs
            for f in fys
            if str(a) != "01010000"
        ]

    session = _StubSession(mapped_fn=_mapped)
    client = _client(session)
    fid = _upload(client, _GL_CSV)

    resp = _commit(client, fid)
    # The partial case must NOT 500 (the old FK failure inside load_canonical).
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "gl_accounts_unmapped"
    assert detail["total_unmapped"] == 1
    assert [u["account_number_group"] for u in detail["unmapped"]] == ["01010000"]
    # ORIGINAL account number ('10000'), NOT the zero-padded suffix '010000'.
    assert detail["unmapped"][0]["account"] == "10000"


# =========================================================================== #
# (c) full mapping -> pre-flight passes, commit proceeds (200)
# =========================================================================== #
def test_full_mapping_commit_proceeds():
    # Map the full cartesian of observed angs x fys -> nothing unmapped.
    def _mapped(angs, fys):
        return [(str(a), int(f)) for a in angs for f in fys]

    session = _StubSession(mapped_fn=_mapped)
    client = _client(session)
    fid = _upload(client, _GL_CSV)

    fake_result = {
        "load_id": 1,
        "entries": 4,
        "lines": 4,
        "ar": 0,
        "ap": 0,
        "sales": 0,
        "com": 0,
        "skipped": 0,
        "loaded_at": "2024-03-15T00:00:00+00:00",
        "commit_mode": "replace",
    }
    with patch("app.routers.ingest.load_canonical", return_value=fake_result):
        resp = _commit(client, fid)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["lines"] == 4
    assert body["commit_mode"] == "replace"


# =========================================================================== #
# (d) account numbers + line_note must NOT appear in the logs
# =========================================================================== #
def test_unmapped_does_not_log_accounts_or_line_note(caplog):
    session = _StubSession(mapped_fn=lambda angs, fys: [])
    client = _client(session)
    fid = _upload(client, _GL_CSV_WITH_NOTE)

    with caplog.at_level(logging.WARNING):
        resp = _commit(client, fid, profile=_profile_with_note())

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "gl_accounts_unmapped"
    # The pre-flight logs ONLY the count.
    assert "unmapped account/year combos" in caplog.text
    # NEVER the account numbers or the booking text (PII).
    assert _PII_NOTE not in caplog.text
    assert "01010000" not in caplog.text
    assert "01080000" not in caplog.text


# =========================================================================== #
# (e) auto-expand fires but finds no source -> true orphan still 422s
# =========================================================================== #
def test_true_orphan_still_422_when_auto_expand_finds_no_source():
    """Auto-expand that fires but finds no source row must NOT suppress the FK 422.

    Scenario: GL file for entity '01' has four accounts.  Three are already in
    dim_gl_account (mapped_fn returns them); one ('01030000', bare account '30000')
    is NOT mapped and has no sibling row under any other entity prefix (stub source-
    lookup returns []).  _replicate_dim_across_entities places it in truly_unmapped
    and exits without writing anything.  The FK pre-flight then raises 422 listing
    EXACTLY that one orphan — the auto-expand path must never silently drop it.

    This is a non-regression test for the guarantee that GL commit auto-expand is
    ADDITIVE ONLY and never suppresses unmapped accounts that have no source row.
    """
    # Map all four GL accounts EXCEPT the orphan '01030000' (bare account '30000').
    def _partial(angs, fys):
        return [(a, f) for a in angs for f in fys if str(a) != "01030000"]

    session = _StubSession(mapped_fn=_partial)
    client = _client(session)
    fid = _upload(client, _GL_CSV)

    resp = _commit(client, fid)
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "gl_accounts_unmapped"

    # The auto-expand fired for '01030000' (unmapped initial check) but the stub
    # source-lookup returned [] (no sibling entity has this account) -> truly_unmapped.
    # The FK pre-flight must list it.
    assert detail["total_unmapped"] == 1, detail
    assert detail["truncated"] is False

    angs = {u["account_number_group"] for u in detail["unmapped"]}
    assert "01030000" in angs, f"orphan missing from 422 unmapped list: {angs}"
    # ORIGINAL bare account number, not the zero-padded suffix.
    assert detail["unmapped"][0]["account"] == "30000"
