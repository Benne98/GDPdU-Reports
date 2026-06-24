"""Tests for etl/net_profit.py — reporting-v2 Phase 3 (synthetic net-profit rows).

Financial-correctness tests for the net-profit synthesis (``gl_rows`` source).
Uses an in-memory SQLite database with a MINIMAL schema mirroring the production
columns the module touches (fact_gl_entry / fact_gl_line / dim_gl_account).  All
data is synthetic (CLAUDE.md rule).

Formula under test:

    NP_stored[e, fy] = Σ amount over level_0='PL' rows of (e, fy)
                     = -NP_presented[e, fy]      (credit value; negative for a profit)

The stored credit value is booked onto the entity's canonical 'Net profit' equity
account so that after the BS credit-side display flip it presents as +NP_presented
AND the ledger balances (Σ BS incl. NP == 0 per FY).

Coverage:
  - value & sign: stored amount == Σ PL amount (credit; negative for profit).
  - single row per (entity, fiscal_year) onto the canonical (MIN) NP account.
  - idempotency: delete-by-marker then reinsert; run twice = identical.
  - B-check exemption: net_profit rows don't break B1/B2/B3 (via etl.checks).
  - _opening_exempt_mask exempts entry_type='net_profit'.
  - multi-entity: independent per entity.
  - loss case: positive PL sum -> positive stored amount (presented negative).
  - zero net profit -> no row.
  - tagging: fiscal_period=12 / entry_type='net_profit' / posting_date Dec-31.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from etl import checks as C
from etl import net_profit as NP


# --------------------------------------------------------------------------- #
# Minimal in-memory schema + fixtures
# --------------------------------------------------------------------------- #
_SCHEMA = [
    """
    CREATE TABLE dim_gl_account (
        account_number_group TEXT NOT NULL,
        fiscal_year          INTEGER NOT NULL,
        level_0              TEXT,
        level_1              TEXT,
        level_2              TEXT,
        level_3              TEXT,
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
        entity_prefix TEXT,
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
        entity_prefix TEXT,
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


def _add_account(
    session: Session,
    ang: str,
    fy: int,
    level_0: str,
    *,
    level_1: str = "",
    level_2: str = "",
    level_3: str = "",
) -> None:
    session.execute(
        text(
            "INSERT OR IGNORE INTO dim_gl_account "
            "(account_number_group, fiscal_year, level_0, level_1, level_2, level_3, entity_prefix) "
            "VALUES (:a, :y, :l0, :l1, :l2, :l3, :e)"
        ),
        {"a": ang, "y": fy, "l0": level_0, "l1": level_1, "l2": level_2,
         "l3": level_3, "e": ang[:2]},
    )


def _add_np_account(session: Session, ang: str, fy: int) -> None:
    """Seed an Equity & liabilities / Equity / Net profit equity account."""
    _add_account(
        session, ang, fy, "BS",
        level_1="Equity & liabilities", level_2="Equity", level_3="Net profit",
    )


_bid_counter = [1]


def _add_pl_movement(session: Session, ang: str, fy: int, amount: float) -> None:
    """Insert a real P&L GL movement (entry + line) on (account, fy)."""
    _add_account(session, ang, fy, "PL", level_1="Income statement")
    bid = _bid_counter[0]
    _bid_counter[0] += 1
    jegn = f"{ang[:2]}TX{bid:07d}"
    session.execute(
        text(
            "INSERT OR IGNORE INTO fact_gl_entry "
            "(journal_entry_group_number, fiscal_year, fiscal_period, entry_type, "
            " posting_date, currency_code, source_system, entity_prefix) "
            "VALUES (:j, :y, 6, 'actual', :pd, 'EUR', 'real', :e)"
        ),
        {"j": jegn, "y": fy, "pd": f"{fy}-06-15", "e": ang[:2]},
    )
    session.execute(
        text(
            "INSERT INTO fact_gl_line "
            "(journal_entry_group_number, fiscal_year, line_number, booking_line_id, "
            " account_number_group, amount, source_system, entity_prefix) "
            "VALUES (:j, :y, 1, :b, :a, :amt, 'real', :e)"
        ),
        {"j": jegn, "y": fy, "b": bid, "a": ang, "amt": amount, "e": ang[:2]},
    )


def _synthetic_rows(session: Session) -> list[dict]:
    # entity_prefix is a GENERATED column in Postgres (omitted on insert); in this
    # SQLite test schema it is plain + left NULL by the synthesis, so derive the
    # entity from the account_number_group prefix (== the production generated rule).
    rows = session.execute(
        text(
            "SELECT account_number_group, fiscal_year, amount "
            "FROM fact_gl_line WHERE source_system = :s "
            "ORDER BY account_number_group, fiscal_year"
        ),
        {"s": NP.SYNTHETIC_NP_SOURCE},
    ).fetchall()
    return [
        {"ang": r[0], "fy": r[1], "amount": round(float(r[2]), 6), "ep": str(r[0])[:2]}
        for r in rows
    ]


def _scope(years=None):
    class _S:
        def __init__(self, ys):
            self.years = ys or []
    return _S(years)


def _lines_df(session: Session) -> pd.DataFrame:
    """All GL lines joined with their entry header tags (for etl.checks)."""
    rows = session.execute(
        text(
            "SELECT l.journal_entry_group_number, l.fiscal_year, e.fiscal_period, "
            "       e.entry_type, e.posting_date, l.booking_line_id, "
            "       l.account_number_group, l.amount "
            "FROM fact_gl_line l "
            "JOIN fact_gl_entry e "
            "  ON e.journal_entry_group_number = l.journal_entry_group_number "
            " AND e.fiscal_year = l.fiscal_year"
        )
    ).fetchall()
    return pd.DataFrame(
        rows,
        columns=[
            "journal_entry_group_number", "fiscal_year", "fiscal_period",
            "entry_type", "posting_date", "booking_line_id",
            "account_number_group", "amount",
        ],
    )


# --------------------------------------------------------------------------- #
# Value & sign (worked example)
# --------------------------------------------------------------------------- #
class TestValueAndSign:
    def setup_method(self):
        _bid_counter[0] = 1

    def test_profit_stored_as_credit_negative(self):
        """Worked example: revenue +1200 stored -1200, COGS +500 -> Σ PL = -700.

        Wait — canonical P&L sign: revenue is credit-normal (stored NEGATIVE),
        expense is debit-normal (stored POSITIVE).  Revenue -1200, COGS +500 ->
        Σ PL amount = -700 (stored credit, a PROFIT).  Presented = +700.
        """
        s = _make_session()
        _add_np_account(s, "01000001", 2024)
        _add_pl_movement(s, "014000", 2024, -1200.0)  # revenue (credit, stored -)
        _add_pl_movement(s, "015000", 2024, 500.0)    # COGS (debit, stored +)

        out = NP.synthesize_net_profit(s, _scope())
        assert out["source"] == "gl_rows"
        rows = _synthetic_rows(s)
        assert len(rows) == 1
        r = rows[0]
        assert r["ang"] == "01000001"
        assert r["fy"] == 2024
        # NP_stored = Σ PL amount = -1200 + 500 = -700 (credit; profit)
        assert r["amount"] == -700.0

    def test_balance_identity_holds_after_booking(self):
        """Σ(BS incl. NP) == 0 per FY once the synthetic equity row is booked."""
        s = _make_session()
        _add_np_account(s, "01000001", 2024)
        # BS side: asset +700 debit (the cash the profit produced), so BS sum=+700
        _add_account(s, "011000", 2024, "BS", level_1="Assets", level_2="Current assets")
        bid = _bid_counter[0]; _bid_counter[0] += 1
        s.execute(text(
            "INSERT INTO fact_gl_entry (journal_entry_group_number, fiscal_year, "
            "fiscal_period, entry_type, posting_date, currency_code, source_system, entity_prefix) "
            "VALUES ('01ASSET', 2024, 6, 'actual', '2024-06-15', 'EUR', 'real', '01')"
        ))
        s.execute(text(
            "INSERT INTO fact_gl_line (journal_entry_group_number, fiscal_year, line_number, "
            "booking_line_id, account_number_group, amount, source_system, entity_prefix) "
            "VALUES ('01ASSET', 2024, 1, :b, '011000', 700.0, 'real', '01')"
        ), {"b": bid})
        # P&L: revenue -1200, COGS +500 -> Σ PL = -700 (profit)
        _add_pl_movement(s, "014000", 2024, -1200.0)
        _add_pl_movement(s, "015000", 2024, 500.0)

        NP.synthesize_net_profit(s, _scope())

        bs_sum = s.execute(text(
            "SELECT COALESCE(SUM(l.amount),0) FROM fact_gl_line l "
            "JOIN dim_gl_account a ON a.account_number_group=l.account_number_group "
            " AND a.fiscal_year=l.fiscal_year WHERE a.level_0='BS' AND l.fiscal_year=2024"
        )).scalar()
        assert round(float(bs_sum), 6) == 0.0  # asset +700 + equity -700 = 0


# --------------------------------------------------------------------------- #
# One row per entity/year + canonical account + tagging
# --------------------------------------------------------------------------- #
class TestRowShape:
    def setup_method(self):
        _bid_counter[0] = 1

    def test_one_row_per_entity_year(self):
        s = _make_session()
        for fy in (2023, 2024):
            _add_np_account(s, "01000001", fy)
            _add_pl_movement(s, "014000", fy, -100.0)
        out = NP.synthesize_net_profit(s, _scope())
        rows = _synthetic_rows(s)
        assert {(r["ang"], r["fy"]) for r in rows} == {("01000001", 2023), ("01000001", 2024)}
        assert out["net_profit_rows"] == 2

    def test_canonical_account_is_min(self):
        """Entity with two NP accounts -> book onto MIN(account_number_group)."""
        s = _make_session()
        _add_np_account(s, "02000001", 2024)
        _add_np_account(s, "02020001", 2024)  # sub-entity NP account
        _add_pl_movement(s, "024000", 2024, -300.0)
        NP.synthesize_net_profit(s, _scope())
        rows = _synthetic_rows(s)
        assert len(rows) == 1
        assert rows[0]["ang"] == "02000001"  # MIN wins
        assert rows[0]["amount"] == -300.0

    def test_tagging_fiscal_period_and_posting_date(self):
        s = _make_session()
        _add_np_account(s, "01000001", 2024)
        _add_pl_movement(s, "014000", 2024, -100.0)
        NP.synthesize_net_profit(s, _scope())
        hdr = s.execute(text(
            "SELECT fiscal_period, entry_type, posting_date FROM fact_gl_entry "
            "WHERE source_system = :s"
        ), {"s": NP.SYNTHETIC_NP_SOURCE}).fetchone()
        assert hdr[0] == NP.NET_PROFIT_FISCAL_PERIOD == 12
        assert hdr[1] == NP.NET_PROFIT_ENTRY_TYPE == "net_profit"
        assert hdr[2].endswith("-12-31")

    def test_skips_when_no_np_account(self):
        s = _make_session()
        # P&L exists but NO seeded 'Net profit' account for the entity/year
        _add_pl_movement(s, "014000", 2024, -100.0)
        out = NP.synthesize_net_profit(s, _scope())
        assert _synthetic_rows(s) == []
        assert ("01", 2024) in out["skipped_no_account"]


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #
class TestEdgeCases:
    def setup_method(self):
        _bid_counter[0] = 1

    def test_loss_case_positive_stored(self):
        """A loss: Σ PL amount positive -> stored positive (presented negative)."""
        s = _make_session()
        _add_np_account(s, "02000001", 2024)
        _add_pl_movement(s, "024000", 2024, -400.0)  # revenue
        _add_pl_movement(s, "025000", 2024, 900.0)   # expense > revenue -> loss
        NP.synthesize_net_profit(s, _scope())
        rows = _synthetic_rows(s)
        assert rows[0]["amount"] == 500.0  # Σ PL = -400 + 900 = +500 (loss, stored +)

    def test_zero_net_profit_no_row(self):
        s = _make_session()
        _add_np_account(s, "01000001", 2024)
        _add_pl_movement(s, "014000", 2024, -500.0)
        _add_pl_movement(s, "015000", 2024, 500.0)  # nets to zero
        NP.synthesize_net_profit(s, _scope())
        assert _synthetic_rows(s) == []

    def test_multi_entity_independent(self):
        s = _make_session()
        _add_np_account(s, "01000001", 2024)
        _add_np_account(s, "03000001", 2024)
        _add_pl_movement(s, "014000", 2024, -100.0)  # entity 01 profit
        _add_pl_movement(s, "034000", 2024, 250.0)   # entity 03 loss
        NP.synthesize_net_profit(s, _scope())
        rows = {(r["ep"], r["fy"]): r["amount"] for r in _synthetic_rows(s)}
        assert rows[("01", 2024)] == -100.0
        assert rows[("03", 2024)] == 250.0

    def test_scope_years_restrict_targets(self):
        s = _make_session()
        for fy in (2023, 2024):
            _add_np_account(s, "01000001", fy)
            _add_pl_movement(s, "014000", fy, -100.0)
        NP.synthesize_net_profit(s, _scope(years=[2024]))
        rows = _synthetic_rows(s)
        assert {(r["ang"], r["fy"]) for r in rows} == {("01000001", 2024)}


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #
class TestIdempotency:
    def setup_method(self):
        _bid_counter[0] = 1

    def test_run_twice_identical(self):
        s = _make_session()
        _add_np_account(s, "01000001", 2024)
        _add_np_account(s, "02000001", 2024)
        _add_pl_movement(s, "014000", 2024, -700.0)
        _add_pl_movement(s, "024000", 2024, 300.0)

        NP.synthesize_net_profit(s, _scope())
        first = _synthetic_rows(s)
        out2 = NP.synthesize_net_profit(s, _scope())
        second = _synthetic_rows(s)

        assert first == second, "net-profit synthesis must be idempotent"
        assert out2["synthetic_net_profit_deleted"] >= len(first)
        total = s.execute(text(
            "SELECT COUNT(*) FROM fact_gl_line WHERE source_system = :s"
        ), {"s": NP.SYNTHETIC_NP_SOURCE}).scalar()
        assert total == len(first)

    def test_rerun_does_not_double_count(self):
        """Synthetic NP rows are EXCLUDED from the PL source sum, so a second run
        does not inflate the stored amount."""
        s = _make_session()
        _add_np_account(s, "01000001", 2024)
        _add_pl_movement(s, "014000", 2024, -700.0)
        NP.synthesize_net_profit(s, _scope())
        NP.synthesize_net_profit(s, _scope())
        rows = _synthetic_rows(s)
        assert len(rows) == 1
        assert rows[0]["amount"] == -700.0  # NOT inflated


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
        _add_np_account(s, "01000001", 2024)
        _add_np_account(s, "02000001", 2024)
        _add_pl_movement(s, "014000", 2024, -100.0)  # entity 01 profit -> stored -100
        _add_pl_movement(s, "024000", 2024, -250.0)  # entity 02 profit -> stored -250
        return s

    def test_entity_scoped_rebuild_preserves_other_entity_rows(self):
        """Global rebuild seeds both entities' NP rows; an entity-scoped rebuild of
        '01' must re-create ONLY 01's NP row and leave 02's untouched (M1)."""
        s = self._two_entity_session()
        NP.synthesize_net_profit(s, _scope())
        rows0 = {(r["ep"], r["fy"]): r["amount"] for r in _synthetic_rows(s)}
        assert rows0[("01", 2024)] == -100.0
        assert rows0[("02", 2024)] == -250.0

        out = NP.synthesize_net_profit(s, _scope_pfx(prefixes=["01"]))
        rows1 = {(r["ep"], r["fy"]): r["amount"] for r in _synthetic_rows(s)}
        # 02's NP row must STILL be present and unchanged.
        assert rows1[("02", 2024)] == -250.0, "entity 02's NP row was wrongly deleted"
        assert rows1[("01", 2024)] == -100.0
        # The delete was confined to '01' (did not wipe 02).
        assert out["synthetic_net_profit_deleted"] == 1

    def test_entity_scoped_rebuild_only_synthesizes_in_scope(self):
        """On a fresh DB, an entity-scoped rebuild of '01' synthesizes ONLY 01."""
        s = self._two_entity_session()
        NP.synthesize_net_profit(s, _scope_pfx(prefixes=["01"]))
        rows = {(r["ep"], r["fy"]): r["amount"] for r in _synthetic_rows(s)}
        assert set(rows) == {("01", 2024)}


# --------------------------------------------------------------------------- #
# B-check exemption (etl.checks)
# --------------------------------------------------------------------------- #
class TestBalanceCheckExemption:
    def setup_method(self):
        _bid_counter[0] = 1

    def test_opening_exempt_mask_exempts_net_profit(self):
        df = pd.DataFrame({
            "fiscal_period": [12, 6],
            "entry_type": ["net_profit", "actual"],
        })
        mask = C._opening_exempt_mask(df)
        assert bool(mask.iloc[0]) is True   # net_profit exempt
        assert bool(mask.iloc[1]) is False  # normal row not exempt

    def test_net_profit_row_does_not_break_balance_checks(self):
        """A single-sided net_profit row must not fail B1/B2/B3 (it is exempt).

        One balanced 2-line booking (asset debit +700 / revenue credit -700,
        same journal entry) makes B1/B2/B3 all pass on the movable rows.  The
        synthesized single-sided net_profit equity row must stay exempt and not
        break any of them.
        """
        s = _make_session()
        _add_np_account(s, "01000001", 2024)
        _add_account(s, "011000", 2024, "BS", level_1="Assets", level_2="Current assets")
        _add_account(s, "014000", 2024, "PL", level_1="Income statement")
        s.execute(text(
            "INSERT INTO fact_gl_entry (journal_entry_group_number, fiscal_year, "
            "fiscal_period, entry_type, posting_date, currency_code, source_system, entity_prefix) "
            "VALUES ('01BK', 2024, 6, 'actual', '2024-06-15', 'EUR', 'real', '01')"
        ))
        for ln, (ang, amt) in enumerate([("011000", 700.0), ("014000", -700.0)], start=1):
            bid = _bid_counter[0]; _bid_counter[0] += 1
            s.execute(text(
                "INSERT INTO fact_gl_line (journal_entry_group_number, fiscal_year, "
                "line_number, booking_line_id, account_number_group, amount, source_system, entity_prefix) "
                "VALUES ('01BK', 2024, :ln, :b, :a, :amt, 'real', '01')"
            ), {"ln": ln, "b": bid, "a": ang, "amt": amt})

        # sanity: movable ledger balances BEFORE synthesis
        df0 = _lines_df(s)
        assert C.check_booking_balance(df0).passed
        assert C.check_ledger_balance(df0).passed
        assert C.check_monthly_balance(df0).passed

        NP.synthesize_net_profit(s, _scope())

        df = _lines_df(s)
        # the synthetic net_profit row was actually written and is single-sided
        assert any(r["amount"] == -700.0 for r in _synthetic_rows(s))
        assert C.check_booking_balance(df).passed   # B1
        assert C.check_ledger_balance(df).passed     # B2
        assert C.check_monthly_balance(df).passed     # B3
