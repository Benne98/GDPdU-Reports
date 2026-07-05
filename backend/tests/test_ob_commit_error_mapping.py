"""Regression tests: OB commit error-mapping — helpers + end-to-end (DB-free).

Tested code path:
  backend/app/routers/ingest.py
    _pg_sqlstate     (~3449) — walks exc chain, returns first pgcode.
    _pg_constraint   (~3469) — walks exc chain, returns diag.constraint_name.
    _is_fk_violation (~3431) — detects SQLSTATE 23503 / ForeignKeyViolation.
    opening_balance_commit load except block (~3992) — maps known DB errors to 422.

Layer 1 (DB-free, pure unit tests):
  TestPgSqlstate / TestPgConstraint / TestIsFkViolation — direct calls to the
  three helper functions with fake exception objects that mimic the psycopg2 /
  SQLAlchemy exception shape (.pgcode, .diag.constraint_name, .orig, .__cause__).

Layer 2 (DB-free, monkeypatched):
  TestObCommitErrorMapping — calls opening_balance_commit() as a regular Python
  function (no FastAPI DI), monkeypatching three module-level names so no file
  I/O or DB is needed.  The MagicMock session raises a fake SA-wrapped exception
  on commit(); the test asserts the HTTPException is HTTP 422 with the expected
  human message and no raw SQLSTATE / exception class name in the detail.

All fixtures are synthetic — no real customer data (project rule).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest
from fastapi import HTTPException

import app.routers.ingest as ing
import etl.account_fill
from app.auth import User

# Admin user — mirrors the pattern in test_ob_partner_ingest.py
_ADMIN = User(user_id=1, email="admin@test", display_name="Admin", is_admin=True)


# ============================================================================ #
# Fake psycopg2 / SQLAlchemy exception surrogates
# ============================================================================ #

class _FakeDiag:
    """Minimal psycopg2 Diagnostics surrogate (constraint_name field only)."""

    def __init__(self, constraint_name: str | None = None) -> None:
        self.constraint_name = constraint_name


class _FakePgError(Exception):
    """Minimal psycopg2 driver-error surrogate with .pgcode and .diag."""

    def __init__(self, pgcode: str, constraint_name: str | None = None) -> None:
        super().__init__(f"DB error pgcode={pgcode}")
        self.pgcode = pgcode
        self.diag = _FakeDiag(constraint_name)


class _FakeSAError(Exception):
    """Minimal SQLAlchemy IntegrityError/DataError surrogate wrapping a driver error.

    Production SQLAlchemy wraps the psycopg2 driver error via .orig; the
    exception text mimics the SA formatting but is never exposed to clients.
    """

    def __init__(self, orig: Exception) -> None:
        super().__init__("(psycopg2.errors.X) [SQL: ...]")
        self.orig = orig


# ============================================================================ #
# Layer 1 — direct helper unit tests (DB-free, no monkeypatching)
# ============================================================================ #

class TestPgSqlstate:
    """_pg_sqlstate(exc) walks .orig / .__cause__ and returns the first pgcode."""

    def test_direct_pgcode(self) -> None:
        exc = _FakePgError("23505")
        assert ing._pg_sqlstate(exc) == "23505"

    def test_wrapped_via_orig(self) -> None:
        """SQLAlchemy pattern: SA wrapper holds psycopg2 error in .orig."""
        inner = _FakePgError("22003")
        outer = _FakeSAError(inner)
        assert ing._pg_sqlstate(outer) == "22003"

    def test_wrapped_via_cause(self) -> None:
        """Alternate chain: raise SA from pg_err → pg_err is .__cause__."""
        inner = _FakePgError("23502")
        outer = Exception("wrapper")
        outer.__cause__ = inner
        assert ing._pg_sqlstate(outer) == "23502"

    def test_none_input_returns_none(self) -> None:
        assert ing._pg_sqlstate(None) is None

    def test_plain_exception_returns_none(self) -> None:
        assert ing._pg_sqlstate(Exception("no pgcode here")) is None

    def test_unknown_sqlstate_returned_verbatim(self) -> None:
        """Any pgcode is returned as-is; the caller decides what to do with it."""
        exc = _FakePgError("99999")
        assert ing._pg_sqlstate(exc) == "99999"


class TestPgConstraint:
    """_pg_constraint(exc) walks .orig / .__cause__ and returns diag.constraint_name."""

    def test_direct_constraint_name(self) -> None:
        exc = _FakePgError("23505", "fact_gl_line_booking_line_id_key")
        assert ing._pg_constraint(exc) == "fact_gl_line_booking_line_id_key"

    def test_wrapped_via_orig(self) -> None:
        inner = _FakePgError("23505", "fact_gl_line_booking_line_id_key")
        outer = _FakeSAError(inner)
        assert ing._pg_constraint(outer) == "fact_gl_line_booking_line_id_key"

    def test_no_diag_attribute_returns_none(self) -> None:
        assert ing._pg_constraint(Exception("plain error")) is None

    def test_null_constraint_name_returns_none(self) -> None:
        exc = _FakePgError("23505", None)
        assert ing._pg_constraint(exc) is None

    def test_none_input_returns_none(self) -> None:
        assert ing._pg_constraint(None) is None


class TestIsFkViolation:
    """_is_fk_violation detects SQLSTATE 23503 or class name ForeignKeyViolation."""

    def test_direct_23503(self) -> None:
        assert ing._is_fk_violation(_FakePgError("23503")) is True

    def test_wrapped_23503_via_orig(self) -> None:
        inner = _FakePgError("23503")
        outer = _FakeSAError(inner)
        assert ing._is_fk_violation(outer) is True

    def test_unrelated_sqlstate_is_false(self) -> None:
        assert ing._is_fk_violation(_FakePgError("23505")) is False

    def test_none_is_false(self) -> None:
        assert ing._is_fk_violation(None) is False


# ============================================================================ #
# Layer 2 — end-to-end mapping via opening_balance_commit() (DB-free)
# ============================================================================ #

# --- shared helpers ---------------------------------------------------------

def _gl_profile() -> dict:
    """Minimal valid GL mapping profile (mirrors test_ob_partner_ingest._gl_profile)."""
    return {
        "entity": {"mode": "fixed", "value": "01"},
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
        "source_system": "ob_test",
    }


def _synthetic_canonical() -> pd.DataFrame:
    """Minimal canonical DataFrame that passes all pre-DB guards.

    Provides the columns that opening_balance_commit accesses before the load
    try block: account_number_group (for JEGN + prefix), fiscal_year (Int64),
    amount (magnitude guard), posting_date (M2 synthesis guard), currency_code
    and source_system (passed through to split_entry_line).
    """
    df = pd.DataFrame({
        "account_number_group": ["011200"],
        "fiscal_year": pd.array([2022], dtype="Int64"),
        "amount": [500.0],
        "posting_date": ["2022-01-01"],
        "journal_entry_number": ["DOC1"],
        "currency_code": ["EUR"],
        "source_system": ["ob_test"],
    })
    df.attrs["ignored_entity_labels"] = []
    return df


def _fake_load_and_apply(*args, **kwargs):
    """Stub for ing._load_and_apply: skip file I/O and ETL pipeline entirely."""
    return _synthetic_canonical(), {}, pd.DataFrame()


def _fake_fill(*args, **kwargs):
    """Stub for etl.account_fill.fill_account_rows_for_keys: all accounts resolved."""
    return {"unresolved_no_name": set(), "unresolved_no_resolution": set()}


def _session_raises_on_commit(exc: Exception) -> MagicMock:
    """MagicMock session that succeeds on all calls EXCEPT commit(), which raises exc.

    All session.execute() / .rollback() / .connection() calls return MagicMock so
    the delete-idempotency loop and _bulk_insert_* calls proceed without error.
    Only commit() raises, landing us in the except block we're testing.
    """
    session = MagicMock()
    session.commit.side_effect = exc
    return session


def _ob_body(scope: str = "all") -> ing.OpeningBalanceCommitRequest:
    return ing.OpeningBalanceCommitRequest(
        file_id="fake-file-id-error-mapping-test",
        profile=_gl_profile(),
        scope=scope,
    )


def _apply_common_patches(monkeypatch) -> None:
    """Common monkeypatches that bypass file I/O, ETL, account fill, and DB visibility.

    After these patches, opening_balance_commit() can run its full logic up to and
    including the load try block without touching the filesystem or a DB connection.
    """
    # Skip file I/O and ETL pipeline; return synthetic canonical DataFrame
    monkeypatch.setattr(ing, "_load_and_apply", _fake_load_and_apply)
    # All OB accounts are already in dim_gl_account; no unresolved keys
    monkeypatch.setattr(etl.account_fill, "fill_account_rows_for_keys", _fake_fill)
    # Admin user → no entity-visibility DB query needed
    monkeypatch.setattr(ing, "_visible_entity_prefixes_or_none", lambda s, u: None)


# --- test class -------------------------------------------------------------

class TestObCommitErrorMapping:
    """opening_balance_commit maps Postgres SQLSTATE / constraint to specific 422s.

    Seam: monkeypatched _load_and_apply returns a synthetic canonical frame so no
    file is needed; session.commit() raises a fake psycopg2/SA-shaped exception;
    the test asserts the resulting HTTPException has status 422 with a human
    message that does NOT contain the original SQLSTATE code or exception class name.
    """

    def test_23505_sqlstate_maps_to_duplicate_ids(self, monkeypatch) -> None:
        """SQLSTATE 23505 unique_violation → 422 'duplicate internal ids'."""
        _apply_common_patches(monkeypatch)
        fake_exc = _FakeSAError(_FakePgError("23505", "fact_gl_line_booking_line_id_key"))
        session = _session_raises_on_commit(fake_exc)

        with pytest.raises(HTTPException) as ei:
            ing.opening_balance_commit(_ob_body(), session=session, _admin=_ADMIN)

        assert ei.value.status_code == 422
        assert "duplicate internal ids" in ei.value.detail
        # No raw SQLSTATE or exception class name in client-facing message
        assert "23505" not in ei.value.detail
        assert "FakeSAError" not in ei.value.detail

    def test_constraint_name_without_pgcode_maps_to_duplicate_ids(self, monkeypatch) -> None:
        """constraint fact_gl_line_booking_line_id_key → 422 even when pgcode absent.

        The condition is 'sqlstate == "23505" OR _pg_constraint(exc) == "...key"'.
        This test exercises the second branch.
        """
        _apply_common_patches(monkeypatch)
        # Inner has .diag.constraint_name but NO .pgcode → sqlstate = None
        inner = Exception("constraint violation (no pgcode)")
        inner.diag = _FakeDiag("fact_gl_line_booking_line_id_key")  # type: ignore[attr-defined]
        fake_exc = _FakeSAError(inner)
        session = _session_raises_on_commit(fake_exc)

        with pytest.raises(HTTPException) as ei:
            ing.opening_balance_commit(_ob_body(), session=session, _admin=_ADMIN)

        assert ei.value.status_code == 422
        assert "duplicate internal ids" in ei.value.detail

    def test_22003_sqlstate_maps_to_misscaled_amount(self, monkeypatch) -> None:
        """SQLSTATE 22003 numeric_value_out_of_range → 422 mis-scaled amount."""
        _apply_common_patches(monkeypatch)
        fake_exc = _FakeSAError(_FakePgError("22003"))
        session = _session_raises_on_commit(fake_exc)

        with pytest.raises(HTTPException) as ei:
            ing.opening_balance_commit(_ob_body(), session=session, _admin=_ADMIN)

        assert ei.value.status_code == 422
        detail = ei.value.detail
        # Message must point the user at the number format / decimal separator
        assert "mis-scaled" in detail or "decimal separator" in detail.lower()
        assert "22003" not in detail
        assert "FakeSAError" not in detail

    def test_23502_sqlstate_maps_to_no_account_number(self, monkeypatch) -> None:
        """SQLSTATE 23502 not_null_violation → 422 'have no account number'."""
        _apply_common_patches(monkeypatch)
        fake_exc = _FakeSAError(_FakePgError("23502"))
        session = _session_raises_on_commit(fake_exc)

        with pytest.raises(HTTPException) as ei:
            ing.opening_balance_commit(_ob_body(), session=session, _admin=_ADMIN)

        assert ei.value.status_code == 422
        assert "no account number" in ei.value.detail
        assert "23502" not in ei.value.detail

    def test_23503_fk_violation_maps_to_chart_of_accounts(self, monkeypatch) -> None:
        """SQLSTATE 23503 foreign_key_violation → 422 'chart of accounts'."""
        _apply_common_patches(monkeypatch)
        fake_exc = _FakeSAError(_FakePgError("23503"))
        session = _session_raises_on_commit(fake_exc)

        with pytest.raises(HTTPException) as ei:
            ing.opening_balance_commit(_ob_body(), session=session, _admin=_ADMIN)

        assert ei.value.status_code == 422
        assert "chart of accounts" in ei.value.detail
        assert "23503" not in ei.value.detail

    def test_unknown_sqlstate_maps_to_ob_specific_fallback(self, monkeypatch) -> None:
        """Unknown SQLSTATE → OB-specific 422 mentioning 'opening balance'.

        The fallback must:
        1. Be HTTP 422 (not 500).
        2. Reference 'opening balance' / 'opening-balance' so the frontend
           humanizer can recognise it as OB-specific.
        3. NOT contain the other four specific human messages (wrong branch).
        4. NOT contain raw exception text (SQLSTATE code, class name, traceback).
        """
        _apply_common_patches(monkeypatch)
        fake_exc = _FakeSAError(_FakePgError("99999"))
        session = _session_raises_on_commit(fake_exc)

        with pytest.raises(HTTPException) as ei:
            ing.opening_balance_commit(_ob_body(), session=session, _admin=_ADMIN)

        assert ei.value.status_code == 422
        detail = ei.value.detail
        assert "opening balance" in detail.lower()
        # Must not be one of the other known messages
        assert "duplicate internal ids" not in detail
        assert "no account number" not in detail
        assert "chart of accounts" not in detail
        assert "mis-scaled" not in detail
        # Must not leak raw exception internals
        assert "99999" not in detail
        assert "FakeSAError" not in detail
        assert "FakePgError" not in detail
        assert "Traceback" not in detail

    def test_all_five_sqlstates_yield_422_not_500(self, monkeypatch) -> None:
        """Every SQLSTATE branch (including unknown fallback) yields 422, never 500."""
        _apply_common_patches(monkeypatch)
        for code in ("23505", "22003", "23502", "23503", "99999"):
            session = _session_raises_on_commit(_FakeSAError(_FakePgError(code)))
            with pytest.raises(HTTPException) as ei:
                ing.opening_balance_commit(_ob_body(), session=session, _admin=_ADMIN)
            assert ei.value.status_code == 422, (
                f"SQLSTATE {code!r} mapped to HTTP {ei.value.status_code}, expected 422"
            )
