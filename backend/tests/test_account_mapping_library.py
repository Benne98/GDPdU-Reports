"""Tests for the account (chart-of-accounts) mapping library + fill — migration 0021.

All synthetic data (CLAUDE.md rule), in-memory SQLite mirroring the production
columns the modules touch.

Covered
-------
resolve.resolve_account_most_frequent (PURE):
  - argmax occurrences; single row wins; empty -> None; deterministic tiebreaker;
    dict / tuple coercion; occurrences None -> 0.

account_fill.fill_missing_account_rows (SQLite synthetic GL):
  - account mapped in FY1 only -> FY2 (posted in fact_gl_line) is FILLED with the
    most-frequent hierarchy resolved by account_name (the WORKED EXAMPLE).
  - ovr_account_mapping pin takes PRECEDENCE over the library.
  - EXCLUSIVE mode (the rebuild gate) leaves FY2 UNMAPPED.
  - ADDITIVE: an existing dim_gl_account row is never mutated; idempotent re-run
    fills 0 (golden-equivalence property).
  - no account_name anywhere / no library precedent -> skipped, not mis-classified.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from etl.account_fill import fill_missing_account_rows
from etl.mapping_library.resolve import (
    AccountRow,
    resolve_account_most_frequent,
)


# =========================================================================== #
# PURE resolver
# =========================================================================== #
def _row(l3, occ, l0="PL", l2="Revenue", l4=""):
    return (l0, "Income", l2, l3, l4, "", 10, 20, False, occ)


def test_resolver_argmax_occurrences():
    rows = [_row("Sales", 8), _row("Discounts", 4)]
    w = resolve_account_most_frequent(rows)
    assert w.level_3 == "Sales" and w.occurrences == 8


def test_resolver_single_row_wins():
    w = resolve_account_most_frequent([_row("Only", 1)])
    assert w.level_3 == "Only"


def test_resolver_empty_is_none():
    assert resolve_account_most_frequent([]) is None


def test_resolver_tiebreak_is_deterministic_and_order_independent():
    a = _row("Sales", 5, l4="Bbb")
    b = _row("Sales", 5, l4="Aaa")
    # equal occurrences -> smallest (level_0, level_2, level_3, level_4) wins = "Aaa"
    assert resolve_account_most_frequent([a, b]).level_4 == "Aaa"
    assert resolve_account_most_frequent([b, a]).level_4 == "Aaa"


def test_resolver_accepts_dicts_and_none_occurrences():
    rows = [
        {"level_0": "PL", "level_1": "I", "level_2": "Rev", "level_3": "X",
         "level_4": "", "l4_sub": "", "level_2_sort": 1, "level_3_sort": 2,
         "is_ic": False, "occurrences": None},  # -> 0
        {"level_0": "PL", "level_1": "I", "level_2": "Rev", "level_3": "Y",
         "level_4": "", "l4_sub": "", "level_2_sort": 1, "level_3_sort": 2,
         "is_ic": False, "occurrences": 3},
    ]
    assert resolve_account_most_frequent(rows).level_3 == "Y"


def test_resolver_returns_accountrow_type():
    assert isinstance(resolve_account_most_frequent([_row("Z", 1)]), AccountRow)


# =========================================================================== #
# SQLite synthetic schema for the fill
# =========================================================================== #
_SCHEMA = [
    """
    CREATE TABLE dim_gl_account (
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
    )
    """,
    """
    CREATE TABLE fact_gl_line (
        journal_entry_group_number TEXT NOT NULL,
        fiscal_year INTEGER NOT NULL,
        line_number INTEGER NOT NULL,
        account_number_group TEXT NOT NULL,
        amount REAL NOT NULL,
        PRIMARY KEY (journal_entry_group_number, fiscal_year, line_number)
    )
    """,
    """
    CREATE TABLE lib_account_mapping (
        account_name TEXT NOT NULL,
        level_0 TEXT, level_1 TEXT, level_2 TEXT, level_3 TEXT, level_4 TEXT,
        l4_sub TEXT, level_2_sort INTEGER, level_3_sort INTEGER,
        is_ic INTEGER DEFAULT 0, occurrences INTEGER DEFAULT 0,
        source TEXT, updated_at TEXT,
        PRIMARY KEY (account_name, level_0, level_2, level_3, level_4)
    )
    """,
    """
    CREATE TABLE ovr_account_mapping (
        account_number_group TEXT NOT NULL,
        fiscal_year INTEGER NOT NULL,
        level_0 TEXT, level_1 TEXT, level_2 TEXT, level_3 TEXT, level_4 TEXT,
        l4_sub TEXT, level_2_sort INTEGER, level_3_sort INTEGER,
        is_ic INTEGER DEFAULT 0, source TEXT, updated_at TEXT,
        PRIMARY KEY (account_number_group, fiscal_year)
    )
    """,
]

_ANG = "01001200"  # entity 01, account 001200
_NAME = "Trade receivables"


def _make_session() -> Session:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for ddl in _SCHEMA:
            conn.execute(text(ddl))
    return Session(engine)


def _seed_fy1_only(session: Session) -> None:
    """Account mapped in FY2023 only; posted in BOTH FY2023 and FY2024.

    WORKED EXAMPLE: '01001200' / 'Trade receivables' is classified
    BS/Assets/Current assets/Receivables in FY2023.  Its FY2024 GL postings have no
    dim row -> the fill must classify FY2024 the same way (most-frequent by name).
    """
    session.execute(text(
        """
        INSERT INTO dim_gl_account
          (account_number_group, fiscal_year, gl_account_id, account_name,
           level_0, level_1, level_2, level_3, level_4, l4_sub,
           level_2_sort, level_3_sort, is_ic, source_system)
        VALUES (:ang, 2023, '1200', :nm, 'BS', 'Assets', 'Current assets',
                'Receivables', 'Trade', NULL, 30, 40, 0, 'mapping_file')
        """
    ), {"ang": _ANG, "nm": _NAME})
    # GL postings in BOTH years (FY2024 has no dim row).
    for fy, ln in ((2023, 1), (2024, 1)):
        session.execute(text(
            "INSERT INTO fact_gl_line (journal_entry_group_number, fiscal_year, "
            "line_number, account_number_group, amount) "
            "VALUES (:j, :fy, :ln, :ang, 100.0)"
        ), {"j": f"0100000{fy}", "fy": fy, "ln": ln, "ang": _ANG})
    # Library: most-frequent hierarchy for the name (seeded as if from FY2023 data).
    session.execute(text(
        """
        INSERT INTO lib_account_mapping
          (account_name, level_0, level_1, level_2, level_3, level_4, l4_sub,
           level_2_sort, level_3_sort, is_ic, occurrences, source, updated_at)
        VALUES (:nm, 'BS', 'Assets', 'Current assets', 'Receivables', 'Trade', NULL,
                30, 40, 0, 9, 'seed', '2026-01-01')
        """
    ), {"nm": _NAME})


def _fy2024_row(session: Session):
    return session.execute(text(
        "SELECT level_0, level_1, level_2, level_3, level_4, level_2_sort, "
        "level_3_sort, gl_account_id, source_system FROM dim_gl_account "
        "WHERE account_number_group = :ang AND fiscal_year = 2024"
    ), {"ang": _ANG}).fetchone()


def test_fill_classifies_missing_year_from_library():
    s = _make_session()
    _seed_fy1_only(s)
    assert _fy2024_row(s) is None  # FY2024 unmapped before the fill

    summary = fill_missing_account_rows(s)
    assert summary["missing_keys"] == 1
    assert summary["filled"] == 1 and summary["from_library"] == 1

    r = _fy2024_row(s)
    assert r is not None
    assert (r.level_0, r.level_2, r.level_3) == ("BS", "Current assets", "Receivables")
    assert r.level_2_sort == 30 and r.level_3_sort == 40
    assert r.gl_account_id == "1200"  # reused from the FY2023 business account
    assert r.source_system == "account_library_fill"


def test_fill_does_not_mutate_existing_rows_and_is_idempotent():
    s = _make_session()
    _seed_fy1_only(s)
    before = s.execute(text(
        "SELECT source_system FROM dim_gl_account WHERE account_number_group=:a "
        "AND fiscal_year=2023"), {"a": _ANG}).scalar()

    fill_missing_account_rows(s)
    # existing FY2023 row untouched
    after = s.execute(text(
        "SELECT source_system FROM dim_gl_account WHERE account_number_group=:a "
        "AND fiscal_year=2023"), {"a": _ANG}).scalar()
    assert before == after == "mapping_file"

    # second run: nothing missing -> 0 filled (golden-safe, idempotent)
    second = fill_missing_account_rows(s)
    assert second["missing_keys"] == 0 and second["filled"] == 0


def test_override_takes_precedence_over_library():
    s = _make_session()
    _seed_fy1_only(s)
    s.execute(text(
        "INSERT INTO ovr_account_mapping (account_number_group, fiscal_year, "
        "level_0, level_1, level_2, level_3, level_4, l4_sub, level_2_sort, "
        "level_3_sort, is_ic, source, updated_at) "
        "VALUES (:a, 2024, 'BS', 'Assets', 'Non-current assets', 'Loans', NULL, "
        "NULL, 99, 98, 0, 'project_setup', '2026-01-01')"
    ), {"a": _ANG})

    summary = fill_missing_account_rows(s)
    assert summary["from_override"] == 1 and summary["from_library"] == 0
    r = _fy2024_row(s)
    assert (r.level_2, r.level_3) == ("Non-current assets", "Loans")  # the pin


def test_exclusive_mode_leaves_missing_year_unmapped():
    """The rebuild gate: in exclusive mode the fill stage is not called, so the
    missing year stays unmapped.  This asserts the gate behaviour directly."""
    s = _make_session()
    _seed_fy1_only(s)

    from etl.rebuild import _stage_account_library_fill, RebuildScope

    res = _stage_account_library_fill(s, RebuildScope(), "exclusive")
    assert res["skipped"] is True and res["filled"] == 0
    assert _fy2024_row(s) is None  # FY2024 still unmapped

    # sanity: library mode WOULD have filled it
    res2 = _stage_account_library_fill(s, RebuildScope(), "library")
    assert res2["filled"] == 1
    assert _fy2024_row(s) is not None


def test_no_account_name_anywhere_is_skipped():
    s = _make_session()
    # GL posting for an account that has NO dim row in ANY year -> no name to resolve.
    s.execute(text(
        "INSERT INTO fact_gl_line (journal_entry_group_number, fiscal_year, "
        "line_number, account_number_group, amount) "
        "VALUES ('0100002024', 2024, 1, '01009999', 5.0)"
    ))
    summary = fill_missing_account_rows(s)
    assert summary["filled"] == 0 and summary["skipped_no_name"] == 1


def test_no_library_precedent_is_skipped():
    s = _make_session()
    # dim row exists in FY2023 (so we HAVE a name) but the library has no entry for it.
    s.execute(text(
        """
        INSERT INTO dim_gl_account
          (account_number_group, fiscal_year, gl_account_id, account_name,
           level_0, level_1, level_2, level_3, source_system)
        VALUES ('01007777', 2023, '7777', 'Mystery account', 'PL', 'X', 'Y', 'Z', 'm')
        """
    ))
    s.execute(text(
        "INSERT INTO fact_gl_line (journal_entry_group_number, fiscal_year, "
        "line_number, account_number_group, amount) "
        "VALUES ('0100002024', 2024, 1, '01007777', 5.0)"
    ))
    summary = fill_missing_account_rows(s)
    assert summary["filled"] == 0 and summary["skipped_no_resolution"] == 1
