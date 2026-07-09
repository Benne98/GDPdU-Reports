"""Tests for etl/retained_earnings.py — OPTIONAL retained-earnings roll (v5 Phase 8).

Financial-correctness tests for the year-end close roll that books each completed
fiscal year's P&L result into the entity's retained-earnings equity account as an
opening balance.  In-memory SQLite, synthetic data only (CLAUDE.md rule).

Formula under test (canonical stored sign: + debit / − credit):

    NP_stored[e, y]      = Σ amount over level_0='PL' rows of (e, y)   (−presented)
    RE_roll_stored[e, N] = opening_stored[e] + Σ_{first ≤ y < N} NP_stored[e, y]

booked as an opening_balance (fiscal_period=0, Jan-1 of N) on A_e.

Coverage:
  - enabled → cumulative prior-NP opening per year, correct sign; first year has
    no row without an opening; worked example.
  - opening value seeds the first year and shifts every later year.
  - loss year (positive NP_stored) → the cumulative roll moves the right way.
  - idempotent re-run → identical rows.
  - auto-resolve of the Gewinn-/Verlustvortrag account (MIN when >1).
  - account cannot be resolved → skip + warn (no crash, no rows).
  - config override account wins over auto-resolve.
  - carry-forward does NOT double-count the RE-roll rows (see also
    test_opening_balance.TestCarryForwardExcludesRetainedEarnings).
"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from etl import retained_earnings as RE

_SCHEMA = [
    """
    CREATE TABLE dim_gl_account (
        account_number_group TEXT NOT NULL,
        fiscal_year          INTEGER NOT NULL,
        level_0              TEXT,
        level_1              TEXT,
        level_2              TEXT,
        level_3              TEXT,
        level_4              TEXT,
        entity_prefix        TEXT,
        account_name         TEXT,
        source_system        TEXT,
        PRIMARY KEY (account_number_group, fiscal_year)
    )
    """,
    """
    CREATE TABLE fact_gl_entry (
        journal_entry_group_number TEXT NOT NULL,
        fiscal_year   INTEGER NOT NULL,
        fiscal_period INTEGER,
        entry_type    TEXT,
        posting_date  TEXT,
        currency_code TEXT,
        header_note   TEXT,
        source_system TEXT,
        PRIMARY KEY (journal_entry_group_number, fiscal_year)
    )
    """,
    """
    CREATE TABLE fact_gl_line (
        journal_entry_group_number TEXT NOT NULL,
        fiscal_year   INTEGER NOT NULL,
        line_number   INTEGER NOT NULL,
        booking_line_id INTEGER NOT NULL UNIQUE,
        account_number_group TEXT NOT NULL,
        amount        REAL NOT NULL,
        line_note     TEXT,
        source_system TEXT,
        PRIMARY KEY (journal_entry_group_number, fiscal_year, line_number)
    )
    """,
]

_bid = [1]


def _make_session() -> Session:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for ddl in _SCHEMA:
            conn.execute(text(ddl))
    return Session(engine)


def _add_account(session, ang, fy, *, level_0, level_3="", account_name=""):
    session.execute(
        text(
            "INSERT OR IGNORE INTO dim_gl_account "
            "(account_number_group, fiscal_year, level_0, level_1, level_2, level_3, "
            " level_4, entity_prefix, account_name, source_system) "
            "VALUES (:a, :y, :l0, 'Equity & liabilities', 'Equity', :l3, '', :e, :nm, 'real')"
        ),
        {"a": ang, "y": fy, "l0": level_0, "l3": level_3, "e": ang[:2], "nm": account_name},
    )


def _add_pl(session, ang, fy, amount):
    """A P&L movement (drives NP_stored)."""
    _add_account(session, ang, fy, level_0="PL")
    bid = _bid[0]; _bid[0] += 1
    jegn = f"{ang[:2]}PL{bid:07d}"
    session.execute(
        text(
            "INSERT OR IGNORE INTO fact_gl_entry "
            "(journal_entry_group_number, fiscal_year, fiscal_period, entry_type, "
            " posting_date, currency_code, source_system) "
            "VALUES (:j, :y, 6, 'actual', :pd, 'EUR', 'real')"
        ),
        {"j": jegn, "y": fy, "pd": f"{fy}-06-15"},
    )
    session.execute(
        text(
            "INSERT INTO fact_gl_line "
            "(journal_entry_group_number, fiscal_year, line_number, booking_line_id, "
            " account_number_group, amount, source_system) "
            "VALUES (:j, :y, 1, :b, :a, :amt, 'real')"
        ),
        {"j": jegn, "y": fy, "b": bid, "a": ang, "amt": amount},
    )


def _re_rows(session):
    rows = session.execute(
        text(
            "SELECT account_number_group, fiscal_year, amount "
            "FROM fact_gl_line WHERE source_system = :s "
            "ORDER BY account_number_group, fiscal_year"
        ),
        {"s": RE.SYNTHETIC_RE_SOURCE},
    ).fetchall()
    return [{"ang": r[0], "fy": r[1], "amount": round(float(r[2]), 6)} for r in rows]


class _Scope:
    def __init__(self, years=None, prefixes=None):
        self.years = years or []
        self.prefixes = prefixes or []


def _seed_entity_01(session):
    """Entity 01: PL results 2022/2023/2024; a Gewinnvortrag equity account each yr."""
    # NP_stored: 2022 = -1000 (profit), 2023 = -1500 (profit), 2024 = +400 (loss).
    _add_pl(session, "01400000", 2022, -1000.0)
    _add_pl(session, "01400000", 2023, -1500.0)
    _add_pl(session, "01400000", 2024, +400.0)
    for fy in (2022, 2023, 2024):
        _add_account(session, "01030515", fy, level_0="BS",
                     level_3="Retained earnings", account_name="Gewinn-/Verlustvortrag")


class TestWorkedExample:
    def setup_method(self):
        _bid[0] = 1

    def test_cumulative_prior_np_per_year_correct_sign(self):
        s = _make_session()
        _seed_entity_01(s)
        RE.synthesize_retained_earnings(s, _Scope())
        rows = _re_rows(s)
        # No 2022 row (first year, no opening).  2023 = NP(2022) = -1000.
        # 2024 = NP(2022)+NP(2023) = -1000 + -1500 = -2500.
        assert rows == [
            {"ang": "01030515", "fy": 2023, "amount": -1000.0},
            {"ang": "01030515", "fy": 2024, "amount": -2500.0},
        ]

    def test_rows_are_opening_balances_fiscal_period_0(self):
        s = _make_session()
        _seed_entity_01(s)
        RE.synthesize_retained_earnings(s, _Scope())
        hdr = s.execute(
            text(
                "SELECT entry_type, fiscal_period, posting_date FROM fact_gl_entry "
                "WHERE source_system = :s ORDER BY fiscal_year"
            ),
            {"s": RE.SYNTHETIC_RE_SOURCE},
        ).fetchall()
        assert all(r[0] == "opening_balance" and r[1] == 0 for r in hdr)
        assert hdr[0][2] == "2023-01-01"

    def test_opening_value_seeds_first_year_and_shifts_all(self):
        s = _make_session()
        _seed_entity_01(s)
        RE.synthesize_retained_earnings(s, _Scope(), opening={"01": -250.0})
        rows = _re_rows(s)
        # 2022 = opening = -250 ; 2023 = -250 + -1000 = -1250 ;
        # 2024 = -250 + -1000 + -1500 = -2750.
        assert rows == [
            {"ang": "01030515", "fy": 2022, "amount": -250.0},
            {"ang": "01030515", "fy": 2023, "amount": -1250.0},
            {"ang": "01030515", "fy": 2024, "amount": -2750.0},
        ]

    def test_loss_year_moves_roll_up(self):
        s = _make_session()
        _seed_entity_01(s)
        # Extend to 2025: prior cumulative includes the 2024 loss (+400).
        _add_pl(s, "01400000", 2025, -100.0)
        _add_account(s, "01030515", 2025, level_0="BS",
                     level_3="Retained earnings", account_name="Gewinn-/Verlustvortrag")
        RE.synthesize_retained_earnings(s, _Scope())
        rows = {r["fy"]: r["amount"] for r in _re_rows(s)}
        # 2025 roll = NP(2022)+NP(2023)+NP(2024) = -1000 -1500 +400 = -2100.
        assert rows[2025] == -2100.0

    def test_idempotent_rerun(self):
        s = _make_session()
        _seed_entity_01(s)
        RE.synthesize_retained_earnings(s, _Scope())
        first = _re_rows(s)
        out = RE.synthesize_retained_earnings(s, _Scope())
        assert _re_rows(s) == first
        assert out["retained_earnings_deleted"] == 2  # deleted the 2 prior rows


class TestAccountResolution:
    def setup_method(self):
        _bid[0] = 1

    def test_auto_resolve_min_when_multiple(self):
        s = _make_session()
        _add_pl(s, "01400000", 2022, -1000.0)
        _add_pl(s, "01400000", 2023, -500.0)
        # Two Gewinnvortrag candidates → MIN account_number_group wins.
        for fy in (2022, 2023):
            _add_account(s, "01030599", fy, level_0="BS",
                         level_3="Retained earnings", account_name="Gewinn-/Verlustvortrag")
            _add_account(s, "01030515", fy, level_0="BS",
                         level_3="Retained earnings", account_name="Gewinn-/Verlustvortrag")
        RE.synthesize_retained_earnings(s, _Scope())
        rows = _re_rows(s)
        assert {r["ang"] for r in rows} == {"01030515"}

    def test_config_account_override_wins(self):
        s = _make_session()
        _add_pl(s, "01400000", 2022, -1000.0)
        _add_pl(s, "01400000", 2023, -500.0)
        for fy in (2022, 2023):
            _add_account(s, "01099999", fy, level_0="BS",
                         level_3="Retained earnings", account_name="Custom RE")
        RE.synthesize_retained_earnings(s, _Scope(), accounts={"01": "01099999"})
        rows = _re_rows(s)
        assert rows == [{"ang": "01099999", "fy": 2023, "amount": -1000.0}]

    def test_unresolvable_account_skips_without_crash(self):
        s = _make_session()
        _add_pl(s, "01400000", 2022, -1000.0)
        _add_pl(s, "01400000", 2023, -500.0)
        # No Retained-earnings account at all for entity 01.
        out = RE.synthesize_retained_earnings(s, _Scope())
        assert _re_rows(s) == []
        assert "01" in out["skipped_no_account"]


class TestScope:
    def setup_method(self):
        _bid[0] = 1

    def test_year_scope_restricts_targets(self):
        s = _make_session()
        _seed_entity_01(s)
        RE.synthesize_retained_earnings(s, _Scope(years=[2024]))
        rows = _re_rows(s)
        assert [r["fy"] for r in rows] == [2024]
        # Still the full cumulative value (Σ prior NP), just only the 2024 row.
        assert rows[0]["amount"] == -2500.0
