"""Tests for etl/cf_fill.py — the ``dim_gl_cf`` population that makes the CF render.

Root fix for the "Cash-Flow-statement-empty" bug: after a fresh Project-Setup load
``dim_gl_cf`` (the account→CF-line mapping the CF reader joins against) was never
populated by the rebuild — the two set-based UPSERTs lived only in the standalone
``backend/scripts/populate_dim_gl_cf.py``.  ``etl.cf_fill.populate_dim_gl_cf`` wires
them into the rebuild as an idempotent stage.

All synthetic, in-memory SQLite — no live PG (CLAUDE.md rule).  SQLite supports the
same ``INSERT … ON CONFLICT (cols) DO UPDATE SET … EXCLUDED.…`` upsert as Postgres,
so the production SQL runs unchanged.
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
from etl.cf_fill import populate_dim_gl_cf


# =========================================================================== #
# SQLite synthetic schema — the four tables the two UPSERTs touch.
# =========================================================================== #
_SCHEMA = [
    """
    CREATE TABLE dim_gl_cf (
        account_number_group TEXT NOT NULL,
        fiscal_year INTEGER NOT NULL,
        l1 TEXT, l2 TEXT, l3 TEXT, l4 TEXT, l5 TEXT, cf_mapping TEXT,
        PRIMARY KEY (account_number_group, fiscal_year)
    )
    """,
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
        level_0 TEXT, level_3 TEXT,
        PRIMARY KEY (account_number_group, fiscal_year)
    )
    """,
    """
    CREATE TABLE lib_cf_mapping (
        key_kind TEXT NOT NULL, key_1 TEXT, key_2 TEXT,
        l1 TEXT, l2 TEXT, l3 TEXT, l4 TEXT, l5 TEXT, cf_mapping TEXT
    )
    """,
]


def _make_session(*, with_lib=True) -> Session:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for ddl in _SCHEMA:
            conn.execute(text(ddl))
    return Session(engine)


def _seed_library(session):
    # One NA band and one P&L band.
    session.execute(text(
        "INSERT INTO lib_cf_mapping (key_kind, key_1, key_2, l1, l2, l3, l4, l5, cf_mapping) "
        "VALUES ('na', 'TWC', 'Trade receivables', "
        "'Cash flow from operating activities', 'Change in working capital', "
        "'Trade receivables', NULL, NULL, 'CF_WC_TRADE_RECEIVABLES')"
    ))
    session.execute(text(
        "INSERT INTO lib_cf_mapping (key_kind, key_1, key_2, l1, l2, l3, l4, l5, cf_mapping) "
        "VALUES ('pl_level3', 'PL', 'Depreciation & amortisation', "
        "'Cash flow from operating activities', 'EBITDA', "
        "'Depreciation & amortisation', NULL, NULL, 'CF_EBITDA')"
    ))


def _seed_sources(session):
    # NA/BS account -> matches the 'na' band.
    session.execute(text(
        "INSERT INTO dim_gl_na (account_number_group, fiscal_year, l6_na_mapping, l7_na_description) "
        "VALUES ('01014000', 2024, 'TWC', 'Trade receivables')"
    ))
    # PL account -> matches the 'pl_level3' band (level_0='PL').
    session.execute(text(
        "INSERT INTO dim_gl_account (account_number_group, fiscal_year, level_0, level_3) "
        "VALUES ('01048100', 2024, 'PL', 'Depreciation & amortisation')"
    ))
    # A PL account whose level_3 has NO library band -> stays out of dim_gl_cf.
    session.execute(text(
        "INSERT INTO dim_gl_account (account_number_group, fiscal_year, level_0, level_3) "
        "VALUES ('01099999', 2024, 'PL', 'Other taxes')"
    ))


def _cf_count(session) -> int:
    return int(session.execute(text("SELECT COUNT(*) FROM dim_gl_cf")).scalar() or 0)


def _cf_rows(session):
    return session.execute(text(
        "SELECT account_number_group, cf_mapping FROM dim_gl_cf ORDER BY account_number_group"
    )).fetchall()


# =========================================================================== #
# (a) populates from lib_cf_mapping
# =========================================================================== #
class TestPopulate:
    def test_populates_both_sides(self):
        session = _make_session()
        _seed_library(session)
        _seed_sources(session)
        session.commit()

        out = populate_dim_gl_cf(session)
        session.commit()

        assert out["noop"] is False
        assert out["skipped"] is False
        assert out["total"] == 2  # one NA row + one PL row (the unmapped PL account is excluded)
        rows = dict(_cf_rows(session))
        assert rows == {
            "01014000": "CF_WC_TRADE_RECEIVABLES",  # NA side
            "01048100": "CF_EBITDA",                 # PL side
        }

    def test_pl_side_ignores_non_pl_and_unmapped(self):
        session = _make_session()
        _seed_library(session)
        _seed_sources(session)
        # A BS-classified dim_gl_account row must NOT be pulled by the PL UPSERT.
        session.execute(text(
            "INSERT INTO dim_gl_account (account_number_group, fiscal_year, level_0, level_3) "
            "VALUES ('01020000', 2024, 'BS', 'Depreciation & amortisation')"
        ))
        session.commit()
        populate_dim_gl_cf(session)
        session.commit()
        angs = {r[0] for r in _cf_rows(session)}
        assert "01020000" not in angs  # BS account excluded by WHERE level_0='PL'
        assert angs == {"01014000", "01048100"}


# =========================================================================== #
# (b) idempotent — re-run reproduces identical rows (count unchanged)
# =========================================================================== #
class TestIdempotent:
    def test_second_run_same_rows(self):
        session = _make_session()
        _seed_library(session)
        _seed_sources(session)
        session.commit()

        out1 = populate_dim_gl_cf(session)
        session.commit()
        first = dict(_cf_rows(session))
        count1 = out1["total"]

        out2 = populate_dim_gl_cf(session)
        session.commit()
        second = dict(_cf_rows(session))

        assert count1 == out2["total"] == 2
        assert first == second  # ON CONFLICT DO UPDATE -> byte-identical rows


# =========================================================================== #
# (c) no-op when lib_cf_mapping is empty / absent, or source dim absent
# =========================================================================== #
class TestNoop:
    def test_noop_when_lib_empty(self):
        session = _make_session()
        _seed_sources(session)  # sources present, but NO library rows
        session.commit()
        out = populate_dim_gl_cf(session)
        session.commit()
        assert out["skipped"] is True
        assert out["noop"] is True
        assert out["total"] == 0
        assert _cf_count(session) == 0

    def test_noop_when_lib_table_absent(self):
        # Partial schema: lib_cf_mapping does not exist at all.
        engine = create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(text(_SCHEMA[0]))  # dim_gl_cf only
        session = Session(engine)
        out = populate_dim_gl_cf(session)
        assert out["skipped"] is True
        assert out["noop"] is True
        assert out["total"] == 0

    def test_na_side_skipped_when_dim_gl_na_absent(self):
        # lib + dim_gl_account present, dim_gl_na absent -> only the PL side runs.
        engine = create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(text(_SCHEMA[0]))  # dim_gl_cf
            conn.execute(text(_SCHEMA[2]))  # dim_gl_account
            conn.execute(text(_SCHEMA[3]))  # lib_cf_mapping
        session = Session(engine)
        _seed_library(session)
        session.execute(text(
            "INSERT INTO dim_gl_account (account_number_group, fiscal_year, level_0, level_3) "
            "VALUES ('01048100', 2024, 'PL', 'Depreciation & amortisation')"
        ))
        session.commit()
        out = populate_dim_gl_cf(session)
        session.commit()
        assert out["noop"] is False
        assert out["na_rows"] == 0
        assert out["total"] == 1
        assert {r[0] for r in _cf_rows(session)} == {"01048100"}


# =========================================================================== #
# rebuild wiring: cf_fill runs AFTER structure_recon (inputs ready) on full mode
# =========================================================================== #
class TestRebuildWiring:
    def test_cf_fill_delegates_to_populate(self, monkeypatch):
        called = {}

        def _fake_populate(session, scope=None):
            called["hit"] = True
            return {"na_rows": 1, "pl_rows": 2, "total": 3, "noop": False, "skipped": False}

        import etl.cf_fill as CF
        monkeypatch.setattr(CF, "populate_dim_gl_cf", _fake_populate)
        out = R._stage_cf_fill(MagicMock(), R.RebuildScope())
        assert called.get("hit") is True
        assert out["total"] == 3

    def test_cf_fill_runs_after_structure_recon(self, monkeypatch):
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
            ("_stage_cf_fill", "cf_fill"),
        ]:
            monkeypatch.setattr(R, attr, _rec(name))

        session = MagicMock()
        R.rebuild_project(session, scope=None, mode="full", commit=False)

        assert "cf_fill" in order
        # cf_fill is the LAST full-mode stage — after structure_recon, which is after
        # the classification / account-library fill (its UPSERT inputs).
        assert order.index("cf_fill") > order.index("structure_recon")
        assert order.index("structure_recon") > order.index("account_fill")
