"""POST /api/v1/ingest/mapping/apply-library — no-file recovery endpoint.

Applies the configured mapping library to specific (account_number_group,
fiscal_year) keys and upserts the resolved rows into dim_gl_account, so a GL
commit blocked by the unmapped-accounts pre-flight can proceed without re-running
the file-based CoA mapping step.

Resolution is by EXACT gl_account_id in the JSON library: the bare account
(account_number_group with the 2-char entity prefix stripped) must match an
``entries[].gl_account_id``.  Accounts not present are returned in ``unresolved``
and never written.  The write is admin-gated and entity-visibility fail-closed.

DB-free: load_account_mapping is patched (its real ON CONFLICT DO UPDATE provides
idempotency); the library JSON is read for real (synthetic standard library).
Restricted-user fixture mirrors backend/tests/test_anomaly_entity_auth.py.

Usable library ids (etl/mapping_library/finssentials_standard_v1.json):
  '10000' (Intangible assets), '11227' (Tangible assets).  '999999' is absent.
account_number_group = entity_prefix(2) + bare_account, so prefix '01' + '10000'
-> '0110000' (bare = '10000').
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.auth import User, current_user, require_admin
from app.db import get_session
from app.main import app

_ADMIN = User(user_id=1, email="admin@test", display_name="Admin", is_admin=True)
_RESTRICTED = User(user_id=2, email="user@test", display_name="User", is_admin=False)

_PATH = "/api/v1/ingest/mapping/apply-library"


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


class _AdminSession:
    """Admin path needs no visibility query (visible_entity_codes -> None)."""

    def execute(self, stmt, params=None):
        return MagicMock()

    def commit(self):
        pass

    def rollback(self):
        pass


class _DictRow:
    def __init__(self, values):
        self._values = tuple(values)

    def __getitem__(self, key):
        return self._values[key]

    def __iter__(self):
        return iter(self._values)


def _restricted_session(visibility_codes, code_to_prefix):
    """MagicMock session: admin_role_entity_visibility -> codes; dim_legal_entity -> prefixes."""
    session = MagicMock()

    def _execute(stmt, params=None):
        sql = str(stmt)
        result = MagicMock()
        if "admin_role_entity_visibility" in sql:
            rows = [_DictRow((c,)) for c in visibility_codes]
        elif "dim_legal_entity" in sql:
            codes = (params or {}).get("codes", [])
            rows = [_DictRow((code_to_prefix[c],)) for c in codes if c in code_to_prefix]
        else:
            rows = []
        result.fetchall.return_value = rows
        result.fetchone.return_value = rows[0] if rows else None
        return result

    session.execute.side_effect = _execute
    return session


def _client(session, user: User) -> TestClient:
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[current_user] = lambda: user
    if user.is_admin:
        app.dependency_overrides[require_admin] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


# =========================================================================== #
# (a) resolvable keys -> rows inserted (GL commit can then proceed)
# =========================================================================== #
@patch("app.routers.ingest.load_account_mapping")
def test_apply_library_inserts_resolved_rows(mock_load):
    mock_load.return_value = {"accounts": 2, "na": 0, "cf": 0}
    client = _client(_AdminSession(), _ADMIN)

    resp = client.post(
        _PATH,
        json={
            "library": "skr03",
            "keys": [
                {"account_number_group": "0110000", "fiscal_year": 2024},
                {"account_number_group": "0111227", "fiscal_year": 2024},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["inserted"] == 2
    assert body["resolved_keys"] == 2
    assert body["unresolved"] == []

    # The df handed to the writer carries the resolved library classification.
    assert mock_load.called
    mapping_df = mock_load.call_args[0][1]
    assert set(mapping_df["account_number_group"]) == {"0110000", "0111227"}
    assert set(mapping_df["gl_account_id"]) == {"10000", "11227"}
    assert all(str(s) == "library:skr03" for s in mapping_df["source_system"])
    # Resolved from the real library entry, not blank.
    row = mapping_df[mapping_df["gl_account_id"] == "10000"].iloc[0]
    assert row["level_1"] == "Assets"
    assert row["account_name"]


# =========================================================================== #
# (b) idempotent re-apply -> same result both times
# =========================================================================== #
@patch("app.routers.ingest.load_account_mapping")
def test_apply_library_is_idempotent(mock_load):
    mock_load.return_value = {"accounts": 1, "na": 0, "cf": 0}
    client = _client(_AdminSession(), _ADMIN)
    payload = {
        "library": "skr03",
        "keys": [{"account_number_group": "0110000", "fiscal_year": 2024}],
    }
    first = client.post(_PATH, json=payload)
    second = client.post(_PATH, json=payload)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json() == second.json()
    assert first.json()["inserted"] == 1


# =========================================================================== #
# (c) account not in library -> unresolved, NOT written
# =========================================================================== #
@patch("app.routers.ingest.load_account_mapping")
def test_unknown_account_goes_to_unresolved(mock_load):
    mock_load.return_value = {"accounts": 1, "na": 0, "cf": 0}
    client = _client(_AdminSession(), _ADMIN)

    resp = client.post(
        _PATH,
        json={
            "library": "skr03",
            "keys": [
                {"account_number_group": "0110000", "fiscal_year": 2024},   # resolvable
                {"account_number_group": "01999999", "fiscal_year": 2024},  # not in lib
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resolved_keys"] == 1
    assert body["inserted"] == 1
    assert len(body["unresolved"]) == 1
    bad = body["unresolved"][0]
    assert bad["account_number_group"] == "01999999"
    assert bad["account"] == "999999"
    assert bad["fiscal_year"] == 2024

    # The unresolvable account must never reach the writer.
    mapping_df = mock_load.call_args[0][1]
    assert "01999999" not in set(mapping_df["account_number_group"])


# =========================================================================== #
# (d) restricted user, out-of-scope prefix -> 403, no write
# =========================================================================== #
@patch("app.routers.ingest.load_account_mapping")
def test_restricted_user_out_of_scope_prefix_is_403(mock_load):
    # User may see entity prefix '10' only; the keys carry prefix '01'.
    session = _restricted_session(
        visibility_codes=["DE"], code_to_prefix={"DE": "10"}
    )
    client = _client(session, _RESTRICTED)

    resp = client.post(
        _PATH,
        json={
            "library": "skr03",
            "keys": [{"account_number_group": "0110000", "fiscal_year": 2024}],
        },
    )
    assert resp.status_code == 403, resp.text
    # Fail-closed BEFORE any write.
    assert not mock_load.called


# =========================================================================== #
# (e) unknown alias -> 422, no write
# =========================================================================== #
@patch("app.routers.ingest.load_account_mapping")
def test_unknown_library_alias_is_422(mock_load):
    client = _client(_AdminSession(), _ADMIN)
    resp = client.post(
        _PATH,
        json={
            "library": "not_a_real_library",
            "keys": [{"account_number_group": "0110000", "fiscal_year": 2024}],
        },
    )
    assert resp.status_code == 422, resp.text
    assert not mock_load.called
