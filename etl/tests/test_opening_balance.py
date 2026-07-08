"""Tests for etl/opening_balance.py — reporting-v2 Phase 2 (opening balances).

Financial-correctness tests for the carry-forward synthesis plus the no-op /
tagging modes.  Uses an in-memory SQLite database with a MINIMAL schema that
mirrors the production columns the module touches (fact_gl_entry / fact_gl_line /
dim_gl_account).  All data is synthetic (CLAUDE.md rule).

Carry-forward formula under test:

    OB[e, a, fy] = Σ amount over real fact_gl_line rows of (e, a),
                            fiscal_year ≤ fy-1     (BS accounts only)

Coverage:
  - carry_forward produces prior-year closing as next-year OB (worked example).
  - first year of an entity gets NO carry-forward row.
  - idempotent: running twice yields identical rows.
  - in_data mode is a true no-op (touches nothing).
  - PL accounts are excluded (reset each year).
  - zero carry-forward emits no row.
  - per-entity B2 balance preserved (OB rows are single-sided / exempt; the BS
    carry-forward sum equals prior closing — total ledger unaffected by synthesis).
  - synthetic OB rows are tagged fiscal_period=0 / entry_type='opening_balance'.
  - file mode tags loosely-loaded opening rows; does not synthesize.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from etl import opening_balance as OB


# --------------------------------------------------------------------------- #
# Minimal in-memory schema + fixtures
# --------------------------------------------------------------------------- #
_SCHEMA = [
    """
    CREATE TABLE dim_gl_account (
        account_number_group TEXT NOT NULL,
        fiscal_year          INTEGER NOT NULL,
        level_0              TEXT,
        entity_prefix        TEXT,
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


def _make_session() -> Session:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for ddl in _SCHEMA:
            conn.execute(text(ddl))
    return Session(engine)


def _add_account(session: Session, ang: str, fy: int, level_0: str) -> None:
    session.execute(
        text(
            "INSERT OR IGNORE INTO dim_gl_account "
            "(account_number_group, fiscal_year, level_0, entity_prefix) "
            "VALUES (:a, :y, :l, :e)"
        ),
        {"a": ang, "y": fy, "l": level_0, "e": ang[:2]},
    )


_bid_counter = [1]


def _add_movement(session: Session, ang: str, fy: int, amount: float, *, level_0: str = "BS") -> None:
    """Insert a real GL movement (entry + line) on (account, fy)."""
    _add_account(session, ang, fy, level_0)
    bid = _bid_counter[0]
    _bid_counter[0] += 1
    jegn = f"{ang[:2]}TX{bid:07d}"
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


def _synthetic_rows(session: Session) -> list[dict]:
    rows = session.execute(
        text(
            "SELECT account_number_group, fiscal_year, amount, line_number "
            "FROM fact_gl_line WHERE source_system = :s "
            "ORDER BY account_number_group, fiscal_year"
        ),
        {"s": OB.SYNTHETIC_OB_SOURCE},
    ).fetchall()
    return [
        {"ang": r[0], "fy": r[1], "amount": round(float(r[2]), 6), "line": r[3]}
        for r in rows
    ]


def _scope(years=None):
    class _S:
        def __init__(self, ys):
            self.years = ys or []
    return _S(years)


# --------------------------------------------------------------------------- #
# Worked example: carry-forward = prior-year cumulative closing
# --------------------------------------------------------------------------- #
class TestCarryForwardWorkedExample:
    def setup_method(self):
        _bid_counter[0] = 1

    def test_receivable_and_payable_carry_forward(self):
        """Worked example from docs/financial-logic.md (entity 01, FYs 2022-2024)."""
        s = _make_session()
        # AR 011200: +600 (2022), +50 (2023), -30 (2024)
        _add_movement(s, "011200", 2022, 600.0)
        _add_movement(s, "011200", 2023, 50.0)
        _add_movement(s, "011200", 2024, -30.0)
        # AP 011600 (liability, stored negative): -400 (2022), -100 (2023)
        _add_movement(s, "011600", 2022, -400.0)
        _add_movement(s, "011600", 2023, -100.0)

        out = OB.synthesize_opening_balances(s, _scope(), "carry_forward")
        assert out["mode"] == "carry_forward"

        rows = {(r["ang"], r["fy"]): r["amount"] for r in _synthetic_rows(s)}
        # AR: OB[2023]=+600, OB[2024]=600+50=+650
        assert rows[("011200", 2023)] == 600.0
        assert rows[("011200", 2024)] == 650.0
        # AP: OB[2023]=-400, OB[2024]=-400-100=-500
        assert rows[("011600", 2023)] == -400.0
        assert rows[("011600", 2024)] == -500.0
        # no OB for the first year (2022)
        assert ("011200", 2022) not in rows
        assert ("011600", 2022) not in rows
        # exactly 4 synthetic rows
        assert len(rows) == 4

    def test_synthetic_rows_are_tagged_opening_balance(self):
        s = _make_session()
        _add_movement(s, "011200", 2022, 600.0)
        _add_movement(s, "011200", 2023, 50.0)
        OB.synthesize_opening_balances(s, _scope(), "carry_forward")
        hdr = s.execute(
            text(
                "SELECT fiscal_period, entry_type, posting_date FROM fact_gl_entry "
                "WHERE source_system = :s"
            ),
            {"s": OB.SYNTHETIC_OB_SOURCE},
        ).fetchall()
        assert hdr, "expected synthetic OB header(s)"
        for fp, et, pd_ in hdr:
            assert fp == OB.OPENING_FISCAL_PERIOD
            assert et == OB.OPENING_ENTRY_TYPE
            assert pd_.endswith("-01-01")  # Jan-1 of fy convention

    def test_account_with_only_opening_carries_unchanged(self):
        """An account with a balance but no later movement rolls forward unchanged."""
        s = _make_session()
        _add_movement(s, "011200", 2022, 600.0)
        # 2023 + 2024 exist for the entity (via another account) but 011200 has no
        # 2023/2024 movement -> its closing balance must still carry to both years.
        _add_movement(s, "011900", 2023, 10.0)
        _add_movement(s, "011900", 2024, 10.0)

        OB.synthesize_opening_balances(s, _scope(), "carry_forward")
        rows = {(r["ang"], r["fy"]): r["amount"] for r in _synthetic_rows(s)}
        assert rows[("011200", 2023)] == 600.0
        assert rows[("011200", 2024)] == 600.0


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #
class TestEdgeCases:
    def setup_method(self):
        _bid_counter[0] = 1

    def test_pl_accounts_excluded(self):
        s = _make_session()
        _add_movement(s, "014000", 2022, 1000.0, level_0="PL")
        _add_movement(s, "014000", 2023, 1000.0, level_0="PL")
        OB.synthesize_opening_balances(s, _scope(), "carry_forward")
        assert _synthetic_rows(s) == [], "PL accounts must never carry forward"

    def test_zero_carry_forward_emits_no_row(self):
        s = _make_session()
        # 2022 nets to zero -> nothing to carry into 2023
        _add_movement(s, "011200", 2022, 500.0)
        _add_movement(s, "011200", 2022, -500.0)
        _add_movement(s, "011200", 2023, 5.0)
        OB.synthesize_opening_balances(s, _scope(), "carry_forward")
        rows = {(r["ang"], r["fy"]): r["amount"] for r in _synthetic_rows(s)}
        assert ("011200", 2023) not in rows  # zero -> skipped

    def test_first_year_only_has_no_synthesis(self):
        s = _make_session()
        _add_movement(s, "011200", 2022, 600.0)
        OB.synthesize_opening_balances(s, _scope(), "carry_forward")
        assert _synthetic_rows(s) == []

    def test_scope_years_restrict_targets(self):
        s = _make_session()
        _add_movement(s, "011200", 2022, 600.0)
        _add_movement(s, "011200", 2023, 50.0)
        _add_movement(s, "011200", 2024, -30.0)
        OB.synthesize_opening_balances(s, _scope(years=[2024]), "carry_forward")
        rows = {(r["ang"], r["fy"]): r["amount"] for r in _synthetic_rows(s)}
        assert set(rows) == {("011200", 2024)}
        assert rows[("011200", 2024)] == 650.0

    def test_multi_entity_independent_first_years(self):
        s = _make_session()
        _add_movement(s, "011200", 2022, 600.0)
        _add_movement(s, "011200", 2023, 50.0)
        _add_movement(s, "021200", 2023, 700.0)  # entity 02 starts in 2023
        _add_movement(s, "021200", 2024, 20.0)
        OB.synthesize_opening_balances(s, _scope(), "carry_forward")
        rows = {(r["ang"], r["fy"]): r["amount"] for r in _synthetic_rows(s)}
        assert rows[("011200", 2023)] == 600.0          # entity 01 first year 2022
        assert ("021200", 2023) not in rows             # entity 02 first year 2023
        assert rows[("021200", 2024)] == 700.0


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #
class TestIdempotency:
    def setup_method(self):
        _bid_counter[0] = 1

    def test_run_twice_identical(self):
        s = _make_session()
        _add_movement(s, "011200", 2022, 600.0)
        _add_movement(s, "011200", 2023, 50.0)
        _add_movement(s, "011600", 2022, -400.0)
        _add_movement(s, "011600", 2023, -100.0)

        OB.synthesize_opening_balances(s, _scope(), "carry_forward")
        first = _synthetic_rows(s)
        out2 = OB.synthesize_opening_balances(s, _scope(), "carry_forward")
        second = _synthetic_rows(s)

        assert first == second, "carry_forward must be idempotent"
        assert out2["synthetic_ob_deleted"] >= len(first)
        # no duplicate (jegn, line) rows accumulated
        total = s.execute(
            text("SELECT COUNT(*) FROM fact_gl_line WHERE source_system = :s"),
            {"s": OB.SYNTHETIC_OB_SOURCE},
        ).scalar()
        assert total == len(first)

    def test_rerun_does_not_double_count_into_later_years(self):
        """Synthetic OB rows must be EXCLUDED from the carry-forward source sum,
        so a second run does not inflate later-year balances."""
        s = _make_session()
        _add_movement(s, "011200", 2022, 600.0)
        _add_movement(s, "011200", 2023, 50.0)
        _add_movement(s, "011200", 2024, -30.0)
        OB.synthesize_opening_balances(s, _scope(), "carry_forward")
        OB.synthesize_opening_balances(s, _scope(), "carry_forward")
        rows = {(r["ang"], r["fy"]): r["amount"] for r in _synthetic_rows(s)}
        assert rows[("011200", 2024)] == 650.0  # NOT 1250 (would be double-count)


# --------------------------------------------------------------------------- #
# in_data (no-op) + file (tag) modes
# --------------------------------------------------------------------------- #
class TestNonSynthesisModes:
    def setup_method(self):
        _bid_counter[0] = 1

    def test_in_data_is_true_noop(self):
        s = _make_session()
        _add_movement(s, "011200", 2022, 600.0)
        _add_movement(s, "011200", 2023, 50.0)
        before_lines = s.execute(text("SELECT COUNT(*) FROM fact_gl_line")).scalar()
        before_entries = s.execute(text("SELECT COUNT(*) FROM fact_gl_entry")).scalar()

        out = OB.synthesize_opening_balances(s, _scope(), "in_data")

        assert out["noop"] is True
        assert out["opening_balance_rows"] == 0
        assert s.execute(text("SELECT COUNT(*) FROM fact_gl_line")).scalar() == before_lines
        assert s.execute(text("SELECT COUNT(*) FROM fact_gl_entry")).scalar() == before_entries
        # no synthetic rows created
        assert _synthetic_rows(s) == []

    def test_file_mode_tags_opening_headers(self):
        s = _make_session()
        # an opening-balance header minted by gobd_gl_prepare: prefix(2) + '9' + ...
        s.execute(
            text(
                "INSERT INTO fact_gl_entry "
                "(journal_entry_group_number, fiscal_year, fiscal_period, entry_type, "
                " posting_date, currency_code, source_system) "
                "VALUES ('019220011200', 2022, 6, 'actual', '2022-01-01', 'EUR', 'file')"
            )
        )
        # a normal txn header must NOT be retagged
        s.execute(
            text(
                "INSERT INTO fact_gl_entry "
                "(journal_entry_group_number, fiscal_year, fiscal_period, entry_type, "
                " posting_date, currency_code, source_system) "
                "VALUES ('01TX0000001', 2022, 6, 'actual', '2022-06-15', 'EUR', 'real')"
            )
        )
        out = OB.synthesize_opening_balances(s, _scope(), "file")
        assert out["opening_balance_entries_tagged"] == 1

        tagged = s.execute(
            text(
                "SELECT fiscal_period, entry_type FROM fact_gl_entry "
                "WHERE journal_entry_group_number = '019220011200'"
            )
        ).fetchone()
        assert tagged == (OB.OPENING_FISCAL_PERIOD, OB.OPENING_ENTRY_TYPE)
        # untouched normal header
        normal = s.execute(
            text(
                "SELECT fiscal_period, entry_type FROM fact_gl_entry "
                "WHERE journal_entry_group_number = '01TX0000001'"
            )
        ).fetchone()
        assert normal == (6, "actual")


# --------------------------------------------------------------------------- #
# Per-entity balance (B2) preservation
# --------------------------------------------------------------------------- #
class TestBalancePreservation:
    def setup_method(self):
        _bid_counter[0] = 1

    def test_b2_movable_balance_unchanged_by_synthesis(self):
        """B2 sums NON-opening (movable) rows per entity.  Synthetic OB rows carry
        fiscal_period=0 / entry_type='opening_balance' and are exempt, so the
        movable per-entity sum is identical before and after synthesis."""
        s = _make_session()
        # entity 01: a balanced year (assets + equity/liab net to 0 across accounts)
        _add_movement(s, "011200", 2022, 600.0)    # asset debit
        _add_movement(s, "011600", 2022, -600.0)   # liab credit
        _add_movement(s, "011200", 2023, 50.0)
        _add_movement(s, "011600", 2023, -50.0)

        def movable_sum():
            # movable = exclude opening rows (fiscal_period=0 / entry_type tag)
            return s.execute(
                text(
                    "SELECT COALESCE(SUM(l.amount),0) FROM fact_gl_line l "
                    "JOIN fact_gl_entry e "
                    "  ON e.journal_entry_group_number = l.journal_entry_group_number "
                    " AND e.fiscal_year = l.fiscal_year "
                    "WHERE COALESCE(e.fiscal_period, -1) <> 0 "
                    "  AND COALESCE(e.entry_type,'') <> :ot "
                    "  AND substr(l.account_number_group,1,2) = '01'"
                ),
                {"ot": OB.OPENING_ENTRY_TYPE},
            ).scalar()

        before = round(float(movable_sum()), 6)
        OB.synthesize_opening_balances(s, _scope(), "carry_forward")
        after = round(float(movable_sum()), 6)
        assert before == after == 0.0


# --------------------------------------------------------------------------- #
# Entity-scoped rebuild (M1 regression): scoping A must NOT touch B's rows
# --------------------------------------------------------------------------- #
def _scope_pfx(prefixes=None, years=None):
    class _S:
        def __init__(self, p, y):
            self.prefixes = p or []
            self.years = y or []
    return _S(prefixes, years)


class TestEntityScopedRebuild:
    def setup_method(self):
        _bid_counter[0] = 1

    def _two_entity_session(self) -> Session:
        s = _make_session()
        # Entity 01: AR carries 600 -> OB[2023]=600
        _add_movement(s, "011200", 2022, 600.0)
        _add_movement(s, "011200", 2023, 50.0)
        # Entity 02: AR carries 700 -> OB[2023]=700
        _add_movement(s, "021200", 2022, 700.0)
        _add_movement(s, "021200", 2023, 90.0)
        return s

    def test_entity_scoped_rebuild_preserves_other_entity_rows(self):
        """Full rebuild seeds both entities; an entity-scoped rebuild of '01' must
        re-create ONLY 01's synthetic OB and leave 02's untouched (M1 data-loss)."""
        s = self._two_entity_session()
        # 1. Global rebuild seeds both entities' synthetic OB rows.
        OB.synthesize_opening_balances(s, _scope(), "carry_forward")
        rows0 = {(r["ang"], r["fy"]): r["amount"] for r in _synthetic_rows(s)}
        assert rows0[("011200", 2023)] == 600.0
        assert rows0[("021200", 2023)] == 700.0

        # 2. Entity-scoped rebuild of '01' only.
        out = OB.synthesize_opening_balances(
            s, _scope_pfx(prefixes=["01"]), "carry_forward"
        )
        rows1 = {(r["ang"], r["fy"]): r["amount"] for r in _synthetic_rows(s)}
        # 02's synthetic OB row must STILL be present and unchanged.
        assert rows1[("021200", 2023)] == 700.0, "entity 02's OB was wrongly deleted"
        # 01's row re-created identically.
        assert rows1[("011200", 2023)] == 600.0
        # The delete was confined to '01' (did not wipe 02).
        assert out["synthetic_ob_deleted"] == 1

    def test_entity_scoped_rebuild_only_synthesizes_in_scope(self):
        """On a fresh DB, an entity-scoped rebuild of '01' synthesizes ONLY 01."""
        s = self._two_entity_session()
        OB.synthesize_opening_balances(s, _scope_pfx(prefixes=["01"]), "carry_forward")
        rows = {(r["ang"], r["fy"]): r["amount"] for r in _synthetic_rows(s)}
        assert set(rows) == {("011200", 2023)}
        assert ("021200", 2023) not in rows


# --------------------------------------------------------------------------- #
# Dispatcher validation
# --------------------------------------------------------------------------- #
def test_invalid_mode_raises():
    s = _make_session()
    with pytest.raises(ValueError):
        OB.synthesize_opening_balances(s, _scope(), "bogus")


# --------------------------------------------------------------------------- #
# Regression: carry-forward into a year the account lacks a dim_gl_account row
# --------------------------------------------------------------------------- #
class TestCarryForwardFillsMissingDimRow:
    """A BS account carried forward into a later year where it has NO real movement
    has no dim_gl_account row for that year.  Without synthesizing that row first,
    the ``fact_gl_line (account_number_group, fiscal_year) -> dim_gl_account`` FK
    fails on insert in Postgres — the failure the separate-OB-file / round-trip
    provisioning path hit for entity 02 (account 02015951 in 2025)."""

    def setup_method(self):
        _bid_counter[0] = 1

    def test_missing_later_year_dim_row_is_cloned_from_own_account(self):
        s = _make_session()
        # Account A active 2022+2023 -> entity '01' has years {2022, 2023}.
        _add_movement(s, "019100", 2022, 100.0)
        _add_movement(s, "019100", 2023, 20.0)
        # Account B (BS) has a 2022 movement ONLY -> no dim row for 2023, yet it
        # carries a 500 balance forward into 2023.
        _add_movement(s, "019200", 2022, 500.0)
        assert (
            s.execute(
                text(
                    "SELECT count(*) FROM dim_gl_account "
                    "WHERE account_number_group='019200' AND fiscal_year=2023"
                )
            ).scalar()
            == 0
        )

        OB.synthesize_opening_balances(s, _scope(), "carry_forward")

        # the classification row was cloned into 2023 (level_0 / entity carried).
        row = s.execute(
            text(
                "SELECT level_0, entity_prefix FROM dim_gl_account "
                "WHERE account_number_group='019200' AND fiscal_year=2023"
            )
        ).first()
        assert row is not None, "missing dim_gl_account row was not synthesized"
        assert row[0] == "BS" and row[1] == "01"
        # and the carry-forward OB line (500) exists for (019200, 2023).
        synth = {(r["ang"], r["fy"]): r["amount"] for r in _synthetic_rows(s)}
        assert synth.get(("019200", 2023)) == 500.0
