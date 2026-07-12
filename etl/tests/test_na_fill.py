"""Tests for etl/na_fill.py — the ``dim_gl_na`` BS-classification fill.

Root fix for the "empty balance-sheet-movement / CF working-capital Δ" bug: after a
Project-Setup load with the ``bs_pl_master`` CoA (which carries no NA dimension),
``dim_gl_na.l7_na_description`` is NULL, so the NA UPSERT in ``etl.cf_fill`` matches
0 rows and the CF working-capital lines + WC statement are empty.  ``fill_dim_gl_na``
fills the missing NA classification (library l7 else level_3) as an idempotent,
additive rebuild stage that NEVER flips an existing l6 (WC membership).

All synthetic, in-memory SQLite (no live PG — CLAUDE.md rule).  SQLite supports the same
``INSERT … ON CONFLICT (cols) DO UPDATE SET … EXCLUDED.…`` upsert as Postgres.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from etl import rebuild as R
from etl.na_fill import fill_dim_gl_na, resolve_na_fill
from etl.mapping_library.resolve import NaRow


# =========================================================================== #
# SQLite synthetic schema — the three tables fill_dim_gl_na touches.
# =========================================================================== #
_SCHEMA = [
    """
    CREATE TABLE dim_gl_na (
        account_number_group TEXT NOT NULL,
        fiscal_year INTEGER NOT NULL,
        l6_na_mapping TEXT, l7_na_description TEXT,
        PRIMARY KEY (account_number_group, fiscal_year)
    )
    """,
    """
    CREATE TABLE dim_gl_account (
        account_number_group TEXT NOT NULL,
        fiscal_year INTEGER NOT NULL,
        account_name TEXT, level_0 TEXT, level_3 TEXT,
        PRIMARY KEY (account_number_group, fiscal_year)
    )
    """,
    """
    CREATE TABLE lib_na_mapping (
        account_name TEXT NOT NULL, na_mapping TEXT, na_description TEXT,
        occurrences INTEGER DEFAULT 0
    )
    """,
]


def _make_session(tables=None) -> Session:
    engine = create_engine("sqlite://")
    ddls = _SCHEMA if tables is None else [_SCHEMA[i] for i in tables]
    with engine.begin() as conn:
        for ddl in ddls:
            conn.execute(text(ddl))
    return Session(engine)


def _ins_acct(session, ang, fy, *, name, level_3, level_0="BS"):
    session.execute(text(
        "INSERT INTO dim_gl_account (account_number_group, fiscal_year, account_name, "
        "level_0, level_3) VALUES (:a, :f, :n, :s, :l3)"
    ), {"a": ang, "f": fy, "n": name, "s": level_0, "l3": level_3})


def _ins_na(session, ang, fy, l6, l7):
    session.execute(text(
        "INSERT INTO dim_gl_na (account_number_group, fiscal_year, l6_na_mapping, "
        "l7_na_description) VALUES (:a, :f, :l6, :l7)"
    ), {"a": ang, "f": fy, "l6": l6, "l7": l7})


def _ins_lib(session, name, na_mapping, na_description, occurrences):
    session.execute(text(
        "INSERT INTO lib_na_mapping (account_name, na_mapping, na_description, occurrences) "
        "VALUES (:n, :m, :d, :o)"
    ), {"n": name, "m": na_mapping, "d": na_description, "o": occurrences})


def _na_rows(session):
    return {
        (r[0], r[1]): (r[2], r[3])
        for r in session.execute(text(
            "SELECT account_number_group, fiscal_year, l6_na_mapping, l7_na_description "
            "FROM dim_gl_na"
        )).fetchall()
    }


# =========================================================================== #
# PURE resolver
# =========================================================================== #
class TestPureResolver:
    def test_a_fills_l7_from_library(self):
        # l6 present, l7 NULL; library winner shares l6 -> the refined l7 wins.
        win = NaRow("TWC", "Advance payments received", 8)
        out = resolve_na_fill("TWC", None, win, level_3="Inventories")
        assert out == ("TWC", "Advance payments received")

    def test_b_falls_back_to_level_3_when_no_library(self):
        out = resolve_na_fill("TWC", None, None, level_3="Inventories")
        assert out == ("TWC", "Inventories")

    def test_b_falls_back_to_level_3_when_library_membership_differs(self):
        # Library winner's l6 ('OWC') != kept l6 ('TWC') -> refinement would land in a
        # different membership, so ignore it and use level_3.
        win = NaRow("OWC", "Other assets", 8)
        out = resolve_na_fill("TWC", None, win, level_3="Inventories")
        assert out == ("TWC", "Inventories")

    def test_e_never_flips_existing_l6(self):
        win = NaRow("OWC", "Other assets", 99)
        out_l6, _ = resolve_na_fill("TWC", None, win, level_3="Inventories")
        assert out_l6 == "TWC"

    def test_present_values_are_kept(self):
        win = NaRow("OWC", "Other assets", 99)
        out = resolve_na_fill("TWC", "Trade receivables", win, level_3="Inventories")
        assert out == ("TWC", "Trade receivables")  # both present -> unchanged

    def test_fresh_l6_null_filled_from_library(self):
        # Truly-fresh: l6 & l7 NULL -> both come from the library winner.
        win = NaRow("TWC", "Trade receivables", 5)
        out = resolve_na_fill(None, None, win, level_3="Inventories")
        assert out == ("TWC", "Trade receivables")

    def test_d_noop_when_no_library_and_no_level_3(self):
        out = resolve_na_fill("TWC", None, None, level_3=None)
        assert out == ("TWC", None)  # l7 stays NULL — nothing resolvable


# =========================================================================== #
# DB fill — (a) library, (b) level_3 fallback
# =========================================================================== #
class TestDbFill:
    def test_a_fills_from_library_and_b_from_level_3(self):
        session = _make_session()
        # (a) library-refined: l6 present, l7 NULL, library shares l6.
        _ins_acct(session, "01014000", 2024, name="Erhaltene Anzahlungen", level_3="Inventories")
        _ins_na(session, "01014000", 2024, "TWC", None)
        _ins_lib(session, "Erhaltene Anzahlungen", "TWC", "Advance payments received", 8)
        # (b) level_3 fallback: l6 present, l7 NULL, no library precedent.
        _ins_acct(session, "01015000", 2024, name="Vorraete", level_3="Inventories")
        _ins_na(session, "01015000", 2024, "TWC", None)
        session.commit()

        out = fill_dim_gl_na(session)
        session.commit()

        assert out["noop"] is False
        assert out["filled_l7"] == 2
        rows = _na_rows(session)
        assert rows[("01014000", 2024)] == ("TWC", "Advance payments received")  # (a)
        assert rows[("01015000", 2024)] == ("TWC", "Inventories")                # (b)

    def test_e_never_flips_existing_l6_membership(self):
        session = _make_session()
        # Account is currently ND (NOT in WC); the name's most-frequent library row is
        # OWC (IN WC).  The fill must keep l6=ND (no WC-membership flip → NWC unchanged).
        _ins_acct(session, "03026135", 2024, name="Darlehen Arbeitnehm.", level_3="Loans")
        _ins_na(session, "03026135", 2024, "ND", None)
        _ins_lib(session, "Darlehen Arbeitnehm.", "OWC", "Other assets", 8)
        _ins_lib(session, "Darlehen Arbeitnehm.", "ND", "Loan to employees", 4)
        session.commit()

        fill_dim_gl_na(session)
        session.commit()

        l6, l7 = _na_rows(session)[("03026135", 2024)]
        assert l6 == "ND"          # membership NOT flipped to OWC
        assert l7 == "Loans"       # winner's l6 (OWC) != kept l6 (ND) -> level_3 fallback

    def test_fresh_load_inserts_from_library(self):
        # dim_gl_na EMPTY (bs_pl_master carried no NA cols) — insert from the library.
        session = _make_session()
        _ins_acct(session, "01014000", 2024, name="Forderungen", level_3="Trade receivables")
        _ins_lib(session, "Forderungen", "TWC", "Trade receivables", 10)
        session.commit()

        out = fill_dim_gl_na(session)
        session.commit()

        assert out["inserted"] == 1
        assert _na_rows(session)[("01014000", 2024)] == ("TWC", "Trade receivables")

    def test_pl_accounts_are_ignored(self):
        session = _make_session()
        _ins_acct(session, "08400000", 2024, name="Umsatz", level_3="Revenue", level_0="PL")
        _ins_na(session, "08400000", 2024, "TWC", None)  # stray NA row on a PL account
        session.commit()
        out = fill_dim_gl_na(session)
        session.commit()
        # PL account not selected -> its NA row is left untouched.
        assert _na_rows(session)[("08400000", 2024)] == ("TWC", None)
        assert out["filled_l7"] == 0


# =========================================================================== #
# (c) idempotent — re-run reproduces identical rows
# =========================================================================== #
class TestIdempotent:
    def test_second_run_no_changes(self):
        session = _make_session()
        _ins_acct(session, "01015000", 2024, name="Vorraete", level_3="Inventories")
        _ins_na(session, "01015000", 2024, "TWC", None)
        session.commit()

        fill_dim_gl_na(session)
        session.commit()
        first = _na_rows(session)

        out2 = fill_dim_gl_na(session)
        session.commit()
        second = _na_rows(session)

        assert first == second
        assert out2["noop"] is True          # nothing left to fill
        assert out2["filled_l7"] == 0


# =========================================================================== #
# (d) no-op on absent sources
# =========================================================================== #
class TestNoop:
    def test_noop_when_no_library_and_no_level_3(self):
        session = _make_session()
        # l7 NULL, no library precedent, level_3 NULL -> nothing resolvable.
        _ins_acct(session, "01015000", 2024, name="Vorraete", level_3=None)
        _ins_na(session, "01015000", 2024, "TWC", None)
        session.commit()
        out = fill_dim_gl_na(session)
        session.commit()
        assert out["noop"] is True
        assert out["filled_l7"] == 0
        assert _na_rows(session)[("01015000", 2024)] == ("TWC", None)

    def test_noop_when_dim_gl_na_absent(self):
        session = _make_session(tables=[1])  # dim_gl_account only
        out = fill_dim_gl_na(session)
        assert out["skipped"] is True
        assert out["noop"] is True

    def test_noop_when_dim_gl_account_absent(self):
        session = _make_session(tables=[0])  # dim_gl_na only
        out = fill_dim_gl_na(session)
        assert out["skipped"] is True
        assert out["noop"] is True

    def test_noop_when_lib_table_absent(self):
        # No lib_na_mapping table at all -> level_3 fallback still works, no raise.
        session = _make_session(tables=[0, 1])
        _ins_acct(session, "01015000", 2024, name="Vorraete", level_3="Inventories")
        _ins_na(session, "01015000", 2024, "TWC", None)
        session.commit()
        out = fill_dim_gl_na(session)
        session.commit()
        assert out["filled_l7"] == 1
        assert _na_rows(session)[("01015000", 2024)] == ("TWC", "Inventories")


# =========================================================================== #
# rebuild wiring: na_fill runs AFTER structure_recon and BEFORE cf_fill
# =========================================================================== #
class TestRebuildWiring:
    def test_na_fill_delegates_to_fill(self, monkeypatch):
        called = {}

        def _fake_fill(session, scope=None):
            called["hit"] = True
            return {"filled_l7": 5, "filled_l6": 0, "inserted": 0,
                    "total_na": 5, "noop": False, "skipped": False}

        import etl.na_fill as NA
        monkeypatch.setattr(NA, "fill_dim_gl_na", _fake_fill)
        out = R._stage_na_fill(MagicMock(), R.RebuildScope())
        assert called.get("hit") is True
        assert out["filled_l7"] == 5

    def test_na_fill_between_structure_recon_and_cf_fill(self, monkeypatch):
        order: list[str] = []

        def _rec(name):
            def _f(session, *a, **k):
                order.append(name)
                return {}
            return _f

        for attr, name in [
            ("_stage_classification_refresh", "classification"),
            ("_stage_account_library_fill", "account_fill"),
            ("_stage_partner_backfill", "partner"),
            ("_stage_derived_facts", "facts"),
            ("_stage_opening_balances", "opening"),
            ("_stage_net_profit", "net_profit"),
            ("_stage_retained_earnings", "retained"),
            ("_stage_structure_recon_refresh", "structure_recon"),
            ("_stage_na_fill", "na_fill"),
            ("_stage_cf_fill", "cf_fill"),
        ]:
            monkeypatch.setattr(R, attr, _rec(name))

        R.rebuild_project(MagicMock(), scope=None, mode="full", commit=False)

        assert order.index("na_fill") > order.index("structure_recon")
        assert order.index("cf_fill") > order.index("na_fill")
