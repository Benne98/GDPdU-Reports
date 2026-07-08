"""Tests for etl/statement_backfill.py — the NULL-``level_0`` (statement) repair.

Root fix for the "silently dropped mapped account" bug: a CoA export/re-ingest
round-trip on a double-duplicate-hierarchy account (e.g. GL 48100 "Afa Gebaeude",
``level_0=level_1='PL'`` AND ``level_3=level_4='Depreciation & amortisation'``) shifts
the levels up and leaves ``dim_gl_account.level_0 = NULL``, so every P&L/BS reader
(``WHERE level_0='PL'/'BS'``) silently drops the still-posted account.

Two layers (all synthetic, in-memory SQLite — no live PG; CLAUDE.md rule):
  * PURE resolver: source priority (sibling → library → structure), never 'CF'.
  * DB backfill (B): the double-duplicate account is auto-repaired to 'PL'; non-NULL
    rows are never overwritten; idempotent; a strict no-op on a clean CoA; accounts
    with no GL activity are left alone.
  * Guardrail (C): WARNs (via caplog) about an account that still has GL activity but
    NULL ``level_0`` when nothing can resolve it.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from etl import rebuild as R
from etl.statement_backfill import (
    _norm_statement,
    _pick_sibling_statement,
    backfill_null_statement,
    resolve_null_statement,
    warn_unresolved_null_statement,
)


# =========================================================================== #
# PURE resolver
# =========================================================================== #
class TestPureResolver:
    def test_sibling_wins_first(self):
        # sibling present -> used even if library/structure also resolve.
        stmt, src = resolve_null_statement(["BS"], library_level_0="PL", structure_statement="PL")
        assert (stmt, src) == ("BS", "sibling")

    def test_library_when_no_sibling(self):
        stmt, src = resolve_null_statement([], library_level_0="PL", structure_statement="BS")
        assert (stmt, src) == ("PL", "library")

    def test_structure_when_no_sibling_or_library(self):
        stmt, src = resolve_null_statement([], library_level_0=None, structure_statement="BS")
        assert (stmt, src) == ("BS", "structure")

    def test_unresolved_returns_none(self):
        assert resolve_null_statement([], None, None) == (None, None)

    def test_never_assigns_cf(self):
        # a stray 'CF' sibling/library value normalises to None (accounts never carry CF).
        assert _norm_statement("CF") is None
        stmt, src = resolve_null_statement(["CF"], library_level_0="CF", structure_statement=None)
        assert (stmt, src) == (None, None)

    def test_sibling_most_frequent_and_deterministic(self):
        # 2×PL vs 1×BS -> PL; order independent.
        assert _pick_sibling_statement(["PL", "BS", "PL"]) == "PL"
        assert _pick_sibling_statement(["PL", "BS"]) == "BS"  # tie -> lexicographic 'BS'<'PL'
        assert _pick_sibling_statement(["BS", "PL"]) == "BS"

    def test_norm_spellings(self):
        assert _norm_statement("Bilanz") == "BS"
        assert _norm_statement("GuV") == "PL"
        assert _norm_statement("  pl ") == "PL"
        assert _norm_statement(None) is None
        assert _norm_statement("") is None


# =========================================================================== #
# SQLite synthetic schema
# =========================================================================== #
_SCHEMA = [
    """
    CREATE TABLE dim_gl_account (
        account_number_group TEXT NOT NULL,
        fiscal_year INTEGER NOT NULL,
        gl_account_id TEXT,
        account_name TEXT,
        level_0 TEXT, level_1 TEXT, level_2 TEXT, level_3 TEXT, level_4 TEXT,
        l4_sub TEXT, level_2_sort INTEGER, level_3_sort INTEGER,
        is_ic INTEGER DEFAULT 0, source_system TEXT,
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
        PRIMARY KEY (account_name, level_0, level_2, level_3, level_4)
    )
    """,
    """
    CREATE TABLE dim_pl_structure (
        pl_line_id INTEGER, sort_order INTEGER, line_code TEXT, row_type TEXT,
        level_2 TEXT, level_3 TEXT, level_4 TEXT, gl_account_id TEXT
    )
    """,
    """
    CREATE TABLE dim_bs_structure (
        pl_line_id INTEGER, sort_order INTEGER, line_code TEXT, row_type TEXT,
        level_2 TEXT, level_3 TEXT, level_4 TEXT, gl_account_id TEXT
    )
    """,
]

_DEPREC = "Depreciation & amortisation"


def _make_session() -> Session:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for ddl in _SCHEMA:
            conn.execute(text(ddl))
    return Session(engine)


def _ins_acct(session, ang, fy, *, level_0, name="Afa Gebaeude",
              gl_id="48100", level_2=_DEPREC, level_3=_DEPREC, level_4=None):
    session.execute(text(
        "INSERT INTO dim_gl_account (account_number_group, fiscal_year, gl_account_id, "
        "account_name, level_0, level_1, level_2, level_3, level_4, source_system) "
        "VALUES (:a,:fy,:g,:n,:l0,'Expense',:l2,:l3,:l4,'mapping_file')"
    ), {"a": ang, "fy": fy, "g": gl_id, "n": name,
        "l0": level_0, "l2": level_2, "l3": level_3, "l4": level_4})


def _post(session, ang, fy, amount=1000.0, ln=1):
    session.execute(text(
        "INSERT INTO fact_gl_line (journal_entry_group_number, fiscal_year, line_number, "
        "account_number_group, amount) VALUES (:j,:fy,:ln,:a,:amt)"
    ), {"j": f"{ang}-{fy}-{ln}", "fy": fy, "ln": ln, "a": ang, "amt": amount})


def _seed_pl_deprec_structure(session):
    """PL structure mapping row that the 48100 grain matches (source 3)."""
    session.execute(text(
        "INSERT INTO dim_pl_structure (pl_line_id, sort_order, line_code, row_type, "
        "level_2, level_3, level_4, gl_account_id) "
        "VALUES (1, 100, 'DEPRECIATION_AMORTISATION', 'mapping', :l2, NULL, NULL, NULL)"
    ), {"l2": _DEPREC})


def _level0_rows(session, ang):
    return session.execute(text(
        "SELECT fiscal_year, level_0 FROM dim_gl_account WHERE account_number_group=:a "
        "ORDER BY fiscal_year"
    ), {"a": ang}).fetchall()


# =========================================================================== #
# (B) backfill — double-duplicate account, all years NULL, resolved by LIBRARY
# =========================================================================== #
class TestBackfillLibrary:
    def _seed(self, session):
        # Two entities (01, 04) of GL 48100; ALL fiscal years level_0 IS NULL (the
        # round-trip nulled every year -> no sibling), but GL-active + library-known.
        session.execute(text(
            "INSERT INTO lib_account_mapping (account_name, level_0, level_1, level_2, "
            "level_3, level_4, occurrences) VALUES ('Afa Gebaeude','PL','Expense',:l2,:l3,NULL,4)"
        ), {"l2": _DEPREC, "l3": _DEPREC})
        for ang in ("01048100", "04048100"):
            for fy in (2022, 2023, 2024, 2025):
                _ins_acct(session, ang, fy, level_0=None)
                _post(session, ang, fy)
        session.commit()

    def test_backfill_repairs_to_pl_via_library(self):
        session = _make_session()
        self._seed(session)
        out = backfill_null_statement(session)
        session.commit()

        assert out["backfilled"] == 8  # 2 entities × 4 years
        assert out["from_library"] == 2  # resolved once per group
        assert out["from_sibling"] == 0
        assert out["unresolved"] == 0
        for ang in ("01048100", "04048100"):
            assert all(lv == "PL" for (_fy, lv) in _level0_rows(session, ang))

    def test_idempotent_second_run_is_noop(self):
        session = _make_session()
        self._seed(session)
        backfill_null_statement(session)
        session.commit()
        out2 = backfill_null_statement(session)
        assert out2["backfilled"] == 0
        assert out2["noop"] is True

    def test_structure_resolves_when_no_library(self):
        # No library row -> falls through to source 3 (PL structure grain match).
        session = _make_session()
        _seed_pl_deprec_structure(session)
        for fy in (2024, 2025):
            _ins_acct(session, "04048100", fy, level_0=None)
            _post(session, "04048100", fy)
        session.commit()
        out = backfill_null_statement(session)
        session.commit()
        assert out["backfilled"] == 2
        assert out["from_structure"] == 1
        assert all(lv == "PL" for (_fy, lv) in _level0_rows(session, "04048100"))


# =========================================================================== #
# (B) sibling source + safety (no-overwrite / no-GL-activity / no-op)
# =========================================================================== #
class TestBackfillSafety:
    def test_sibling_year_resolves_and_non_null_untouched(self):
        session = _make_session()
        # 2022-2024 classified 'PL'; 2025 nulled by the round-trip; all GL-active.
        for fy in (2022, 2023, 2024):
            _ins_acct(session, "04048100", fy, level_0="PL")
            _post(session, "04048100", fy)
        _ins_acct(session, "04048100", 2025, level_0=None)
        _post(session, "04048100", 2025)
        session.commit()

        out = backfill_null_statement(session)
        session.commit()
        assert out["backfilled"] == 1
        assert out["from_sibling"] == 1
        rows = dict(_level0_rows(session, "04048100"))
        assert rows == {2022: "PL", 2023: "PL", 2024: "PL", 2025: "PL"}

    def test_no_overwrite_of_non_null(self):
        # A fully-classified account is never touched (additive-only).
        session = _make_session()
        for fy in (2024, 2025):
            _ins_acct(session, "04048100", fy, level_0="PL")
            _post(session, "04048100", fy)
        session.commit()
        out = backfill_null_statement(session)
        assert out["backfilled"] == 0
        assert out["noop"] is True

    def test_structure_ambiguous_both_match_left_unresolved(self):
        # SAFETY (CLAUDE.md rule 1): a grain that matches a mapping row in BOTH
        # dim_pl_structure AND dim_bs_structure is AMBIGUOUS — the backfill must NEVER
        # guess. level_0 stays NULL and the account is reported as unresolved.
        session = _make_session()
        for tbl in ("dim_pl_structure", "dim_bs_structure"):
            session.execute(text(
                f"INSERT INTO {tbl} (pl_line_id, sort_order, line_code, row_type, "
                "level_2, level_3, level_4, gl_account_id) "
                "VALUES (1, 100, 'AMBIG', 'mapping', 'Ambiguous', NULL, NULL, NULL)"
            ))
        # No sibling (all years NULL) and no library row for this name -> only source 3
        # applies, and it is ambiguous.
        for fy in (2024, 2025):
            _ins_acct(session, "06060606", fy, level_0=None, name="Zzz No Library",
                      gl_id="60606", level_2="Ambiguous", level_3="Ambiguous")
            _post(session, "06060606", fy)
        session.commit()

        out = backfill_null_statement(session)
        session.commit()
        assert out["backfilled"] == 0
        assert out["unresolved"] == 1
        assert "06060606" in out["unresolved_groups"]
        assert all(lv is None for (_fy, lv) in _level0_rows(session, "06060606"))

    def test_no_gl_activity_left_null(self):
        # NULL level_0 but NO posting -> not a used account -> left alone.
        session = _make_session()
        _ins_acct(session, "09099999", 2025, level_0=None, name="Ghost", gl_id="99999")
        session.commit()
        out = backfill_null_statement(session)
        assert out["backfilled"] == 0
        assert out["noop"] is True
        assert _level0_rows(session, "09099999") == [(2025, None)]

    def test_strict_noop_on_clean_coa(self):
        # v5 parity: every account already classified -> a strict no-op.
        session = _make_session()
        _ins_acct(session, "03048100", 2025, level_0="PL")
        _post(session, "03048100", 2025)
        session.commit()
        out = backfill_null_statement(session)
        assert out == {
            "backfilled": 0, "from_sibling": 0, "from_library": 0, "from_structure": 0,
            "unresolved": 0, "unresolved_groups": [], "resolved": [],
            "noop": True, "dry_run": False,
        }


# =========================================================================== #
# (C) guardrail
# =========================================================================== #
class TestGuardrail:
    def test_warns_on_unresolvable_account(self, caplog):
        session = _make_session()
        # GL-active, NULL level_0, no name/library/structure precedent -> unresolvable.
        _ins_acct(session, "07070707", 2025, level_0=None, name=None, gl_id="70707",
                  level_2="Mystery", level_3="Mystery")
        _post(session, "07070707", 2025)
        session.commit()

        # Backfill leaves it unresolved...
        out = backfill_null_statement(session)
        assert out["unresolved"] == 1
        assert "07070707" in out["unresolved_groups"]

        # ...and the guardrail WARNs, naming the offender.
        with caplog.at_level(logging.WARNING):
            g = warn_unresolved_null_statement(session)
        assert g["unresolved"] == 1
        assert "07070707" in g["sample"]
        assert any("07070707" in rec.getMessage() and "SILENTLY DROPPED" in rec.getMessage()
                   for rec in caplog.records), caplog.text

    def test_guardrail_silent_after_successful_backfill(self, caplog):
        session = _make_session()
        session.execute(text(
            "INSERT INTO lib_account_mapping (account_name, level_0, level_1, level_2, "
            "level_3, level_4, occurrences) VALUES ('Afa Gebaeude','PL','Expense',:l2,:l3,NULL,4)"
        ), {"l2": _DEPREC, "l3": _DEPREC})
        for fy in (2024, 2025):
            _ins_acct(session, "04048100", fy, level_0=None)
            _post(session, "04048100", fy)
        session.commit()
        backfill_null_statement(session)
        session.commit()
        with caplog.at_level(logging.WARNING):
            g = warn_unresolved_null_statement(session)
        assert g["unresolved"] == 0
        assert not any("SILENTLY DROPPED" in rec.getMessage() for rec in caplog.records)


# =========================================================================== #
# rebuild wiring: B runs BEFORE structure seed/realign; both keys present
# =========================================================================== #
class TestRebuildWiring:
    def test_structure_stage_invokes_backfill_before_seed(self, monkeypatch):
        order: list[str] = []

        def _fake_backfill(session, scope, **kw):
            order.append("backfill")
            return {"backfilled": 0, "noop": True}

        def _fake_warn(session, scope):
            order.append("warn")
            return {"unresolved": 0, "sample": []}

        import etl.statement_backfill as SB
        monkeypatch.setattr(SB, "backfill_null_statement", _fake_backfill)
        monkeypatch.setattr(SB, "warn_unresolved_null_statement", _fake_warn)

        import scripts.seed_bs_structure as sbs
        import scripts.realign_pl_structure as rps
        monkeypatch.setattr(sbs, "seed_bs_structure", lambda session: order.append("seed") or 0)
        monkeypatch.setattr(rps, "realign_pl_structure", lambda session, scope: order.append("realign") or 0)

        session = MagicMock()
        out = R._stage_structure_recon_refresh(session, R.RebuildScope())
        # backfill (B) precedes the structure seed/realign; guardrail (C) runs last.
        assert order == ["backfill", "seed", "realign", "warn"]
        assert "statement_backfill" in out
        assert "statement_unresolved" in out
