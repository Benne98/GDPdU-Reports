"""Unit tests for etl/account_fill.py — fill_account_rows_for_keys.

Uses in-memory SQLite: every SQL statement in account_fill.py is standard
(no Postgres-specific syntax), so the fill/resolver path is fully testable
without a live DB.  All data is synthetic — project rule (no real customer data).

Coverage:
  - Clone (library path): a key whose account exists in a DIFFERENT year gets a
    new dim row with the classification borrowed from lib_account_mapping.
  - Idempotency: a second call with the same keys is a strict no-op
    (filled==0, missing_keys==0, no duplicate row in dim_gl_account).
  - Unresolved no_name: a key with no dim row in ANY year lands in
    unresolved_no_name; fill inserts nothing for it.
"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from etl.account_fill import fill_account_rows_for_keys


# --------------------------------------------------------------------------- #
# Minimal SQLite schema — only the columns that account_fill.py queries/inserts
# --------------------------------------------------------------------------- #
_SCHEMA = [
    """
    CREATE TABLE dim_gl_account (
        account_number_group TEXT    NOT NULL,
        fiscal_year          INTEGER NOT NULL,
        gl_account_id        TEXT,
        account_name         TEXT,
        level_0              TEXT,
        level_1              TEXT,
        level_2              TEXT,
        level_3              TEXT,
        level_4              TEXT,
        l4_sub               TEXT,
        level_2_sort         INTEGER,
        level_3_sort         INTEGER,
        is_ic                INTEGER,
        source_system        TEXT,
        PRIMARY KEY (account_number_group, fiscal_year)
    )
    """,
    """
    CREATE TABLE lib_account_mapping (
        account_name  TEXT,
        level_0       TEXT,
        level_1       TEXT,
        level_2       TEXT,
        level_3       TEXT,
        level_4       TEXT,
        l4_sub        TEXT,
        level_2_sort  INTEGER,
        level_3_sort  INTEGER,
        is_ic         INTEGER,
        occurrences   INTEGER
    )
    """,
    """
    CREATE TABLE ovr_account_mapping (
        account_number_group TEXT,
        fiscal_year          INTEGER,
        level_0              TEXT,
        level_1              TEXT,
        level_2              TEXT,
        level_3              TEXT,
        level_4              TEXT,
        l4_sub               TEXT,
        level_2_sort         INTEGER,
        level_3_sort         INTEGER,
        is_ic                INTEGER
    )
    """,
]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _make_session() -> Session:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for ddl in _SCHEMA:
            conn.execute(text(ddl))
    return Session(engine)


def _seed_dim(
    session: Session,
    ang: str,
    fy: int,
    *,
    name: str = "Bank",
    gid: str = "1200",
) -> None:
    """Insert one dim_gl_account row (OR IGNORE is idempotent for the PK)."""
    session.execute(text(
        "INSERT OR IGNORE INTO dim_gl_account "
        "(account_number_group, fiscal_year, gl_account_id, account_name, "
        " level_0, level_1, level_2, level_3, level_4, l4_sub, "
        " level_2_sort, level_3_sort, is_ic, source_system) "
        "VALUES (:a, :y, :g, :nm, 'BS', 'Assets', 'Cash', 'Bank', NULL, NULL, "
        "        20, 201, 0, 'seed')"
    ), {"a": ang, "y": fy, "g": gid, "nm": name})


def _seed_lib(session: Session, *, name: str = "Bank") -> None:
    """Insert one lib_account_mapping row (library resolution source)."""
    session.execute(text(
        "INSERT INTO lib_account_mapping "
        "(account_name, level_0, level_1, level_2, level_3, level_4, l4_sub, "
        " level_2_sort, level_3_sort, is_ic, occurrences) "
        "VALUES (:nm, 'BS', 'Assets', 'Cash', 'Bank', NULL, NULL, 20, 201, 0, 5)"
    ), {"nm": name})


def _dim_count(session: Session, ang: str, fy: int) -> int:
    """Number of dim_gl_account rows for the given (ang, fy) key."""
    row = session.execute(text(
        "SELECT COUNT(*) FROM dim_gl_account "
        "WHERE account_number_group=:a AND fiscal_year=:y"
    ), {"a": ang, "y": fy}).fetchone()
    return int(row[0])


def _dim_row(session: Session, ang: str, fy: int) -> dict | None:
    """Fetch level_0 / level_3 / account_name / source_system for (ang, fy)."""
    row = session.execute(text(
        "SELECT level_0, level_3, account_name, source_system "
        "FROM dim_gl_account "
        "WHERE account_number_group=:a AND fiscal_year=:y"
    ), {"a": ang, "y": fy}).fetchone()
    if row is None:
        return None
    return {
        "level_0":       row[0],
        "level_3":       row[1],
        "account_name":  row[2],
        "source_system": row[3],
    }


# =========================================================================== #
# 1. Clone from a different year — library resolution path
# =========================================================================== #

class TestFillFromDifferentYear:
    """fill_account_rows_for_keys synthesises a dim row for a key absent in the
    target year by borrowing account_name from an existing dim row in a DIFFERENT
    year and resolving classification from lib_account_mapping."""

    def test_summary_reflects_one_fill_via_library(self):
        """A missing FY 2023 key whose account exists in FY 2022 is filled once
        via the library path (filled=1, from_library=1, nothing skipped)."""
        s = _make_session()
        _seed_dim(s, "01001200", 2022)   # existing row for FY 2022 only
        _seed_lib(s, name="Bank")        # library has a hierarchy for "Bank"

        result = fill_account_rows_for_keys(s, [("01001200", 2023)])

        assert result["missing_keys"] == 1
        assert result["filled"] == 1
        assert result["from_library"] == 1
        assert result["from_override"] == 0
        assert result["skipped_no_name"] == 0
        assert result["skipped_no_resolution"] == 0
        assert result["dry_run"] is False

    def test_new_dim_row_has_library_classification_and_borrowed_name(self):
        """The created dim row inherits level_0/level_3 from the library entry
        and account_name from the FY 2022 existing row; source_system is the
        default 'account_library_fill'."""
        s = _make_session()
        _seed_dim(s, "01001200", 2022, name="Bank", gid="1200")
        _seed_lib(s, name="Bank")

        fill_account_rows_for_keys(s, [("01001200", 2023)])

        row = _dim_row(s, "01001200", 2023)
        assert row is not None, "expected new dim_gl_account row for (01001200, 2023)"
        assert row["level_0"] == "BS",              "level_0 must match library entry"
        assert row["level_3"] == "Bank",            "level_3 must match library entry"
        assert row["account_name"] == "Bank",       "account_name borrowed from FY 2022 dim"
        assert row["source_system"] == "account_library_fill"


# =========================================================================== #
# 2. Idempotency
# =========================================================================== #

class TestFillIdempotency:
    """A second call with the same keys produces filled=0, missing_keys=0 and
    does not create a duplicate dim_gl_account row."""

    def test_second_call_yields_zero_filled(self):
        s = _make_session()
        _seed_dim(s, "01001200", 2022)
        _seed_lib(s, name="Bank")

        r1 = fill_account_rows_for_keys(s, [("01001200", 2023)])
        assert r1["filled"] == 1, "first call must fill the missing key"

        r2 = fill_account_rows_for_keys(s, [("01001200", 2023)])
        assert r2["missing_keys"] == 0, "second call: key already present -> 0 missing"
        assert r2["filled"] == 0,       "second call: idempotent -> 0 inserted"

    def test_no_duplicate_row_after_second_call(self):
        s = _make_session()
        _seed_dim(s, "01001200", 2022)
        _seed_lib(s, name="Bank")

        fill_account_rows_for_keys(s, [("01001200", 2023)])
        fill_account_rows_for_keys(s, [("01001200", 2023)])

        assert _dim_count(s, "01001200", 2023) == 1, \
            "idempotency: must not create a duplicate dim_gl_account row"


# =========================================================================== #
# 3. Unresolved no_name — account absent from ALL dim rows in ALL years
# =========================================================================== #

class TestUnresolvedNoName:
    """A key whose account_number_group has NO dim_gl_account row in any year
    lands in unresolved_no_name; fill inserts nothing for it."""

    def test_absent_account_lands_in_unresolved_no_name(self):
        """'01001300' has zero dim rows anywhere -> goes to unresolved_no_name."""
        s = _make_session()
        # No dim row, no lib entry — truly absent.

        result = fill_account_rows_for_keys(s, [("01001300", 2022)])

        assert result["unresolved_no_name"] == [("01001300", 2022)]
        assert result["skipped_no_name"] == 1
        assert result["filled"] == 0

    def test_nothing_inserted_for_unresolved_no_name(self):
        s = _make_session()

        fill_account_rows_for_keys(s, [("01001300", 2022)])

        assert _dim_count(s, "01001300", 2022) == 0, \
            "unresolved no_name: must not insert any dim_gl_account row"

    def test_empty_key_list_is_immediate_noop(self):
        """Calling with an empty list returns the zero summary without querying."""
        s = _make_session()

        result = fill_account_rows_for_keys(s, [])

        assert result["missing_keys"] == 0
        assert result["filled"] == 0
        assert result["unresolved_no_name"] == []
        assert result["unresolved_no_resolution"] == []


# =========================================================================== #
# 4. Unresolved no_resolution — name exists (other year, SAME account) but no
#    library / override precedent to classify by
# =========================================================================== #

class TestUnresolvedNoResolution:
    """A key whose account_number_group HAS a name (from a different year of the
    SAME account) but has neither an ovr_account_mapping pin nor a
    lib_account_mapping precedent for that name lands in unresolved_no_resolution;
    fill inserts nothing and never guesses a classification."""

    def test_named_account_without_precedent_lands_in_no_resolution(self):
        """'01001200' exists in FY 2022 (name 'Bank') but the library has NO entry
        for 'Bank' and there is no override -> the FY 2023 key is reported as
        unresolved_no_resolution, not unresolved_no_name."""
        s = _make_session()
        _seed_dim(s, "01001200", 2022, name="Bank")   # name present (other year)
        # No _seed_lib and no override -> no precedent for 'Bank'.

        result = fill_account_rows_for_keys(s, [("01001200", 2023)])

        assert result["missing_keys"] == 1
        assert result["unresolved_no_resolution"] == [("01001200", 2023)]
        assert result["skipped_no_resolution"] == 1
        assert result["unresolved_no_name"] == []   # a name DID exist
        assert result["skipped_no_name"] == 0
        assert result["filled"] == 0

    def test_nothing_inserted_for_unresolved_no_resolution(self):
        s = _make_session()
        _seed_dim(s, "01001200", 2022, name="Bank")

        fill_account_rows_for_keys(s, [("01001200", 2023)])

        assert _dim_count(s, "01001200", 2023) == 0, \
            "unresolved no_resolution: must not insert any dim_gl_account row"
