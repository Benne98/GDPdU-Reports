"""Tests for etl/client_coa.py + etl/project_coa_override.py — Phase 4.

Two areas, all synthetic data (CLAUDE.md rule), in-memory SQLite mirroring the
production columns the modules touch.

client_coa.load_client_coa:
  - 1:1 load of a client Sachkontenstamm into dim_gl_account via the canonical
    apply_account_mapping -> load_account_mapping path (level_* populated verbatim).
  - accepts a DataFrame directly and via scope (entity_prefix/fiscal_year override).
  - idempotent (re-load upserts, no duplicate rows).
  - requires a profile (ValueError without one).

project_coa_override.replay_project_overrides:
  - NO-OP when the override table is empty (the golden-equivalence guarantee).
  - replays level_* / *_sort onto matching dim_gl_account rows.
  - restricted to the scope's fiscal years.
  - missing override table is treated as a no-op.
  - capture_overrides_from_accounts upserts override rows (idempotent).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from etl import client_coa as CC
from etl import project_coa_override as PCO
from etl.mapping_account import AccountMappingProfile


# --------------------------------------------------------------------------- #
# Minimal in-memory schema (dim_gl_account + dim_gl_na/cf touched by loader,
# + dim_project_coa_override for the replay tests)
# --------------------------------------------------------------------------- #
_SCHEMA = [
    """
    CREATE TABLE dim_gl_account (
        account_number_group TEXT NOT NULL,
        fiscal_year          INTEGER NOT NULL,
        gl_account_id        TEXT,
        account_name         TEXT,
        level_0 TEXT, level_1 TEXT, level_2 TEXT, level_3 TEXT, level_4 TEXT,
        l4_sub TEXT,
        level_1_sort INTEGER, level_2_sort INTEGER, level_3_sort INTEGER, level_4_sort INTEGER,
        is_ic INTEGER DEFAULT 0,
        source_system TEXT,
        entity_prefix TEXT,
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
    CREATE TABLE dim_gl_cf (
        account_number_group TEXT NOT NULL,
        fiscal_year INTEGER NOT NULL,
        l1 TEXT, l2 TEXT, l3 TEXT, l4 TEXT, l5 TEXT, cf_mapping TEXT,
        PRIMARY KEY (account_number_group, fiscal_year)
    )
    """,
    """
    CREATE TABLE dim_project_coa_override (
        project_id TEXT NOT NULL DEFAULT 'default',
        account_number_group TEXT NOT NULL,
        fiscal_year INTEGER NOT NULL,
        level_0 TEXT, level_1 TEXT, level_2 TEXT, level_3 TEXT, level_4 TEXT,
        l4_sub TEXT,
        level_1_sort INTEGER, level_2_sort INTEGER, level_3_sort INTEGER, level_4_sort INTEGER,
        updated_at TEXT,
        PRIMARY KEY (project_id, account_number_group, fiscal_year)
    )
    """,
]


def _make_session(with_override_table: bool = True) -> Session:
    engine = create_engine("sqlite://")
    ddls = _SCHEMA if with_override_table else _SCHEMA[:-1]
    with engine.begin() as conn:
        for ddl in ddls:
            conn.execute(text(ddl))
    return Session(engine)


def _client_profile() -> AccountMappingProfile:
    """Map an arbitrary client Sachkontenstamm's columns to canonical fields."""
    return AccountMappingProfile(
        entity={"mode": "fixed", "value": "01"},
        fiscal_year={"mode": "fixed", "value": 2024},
        columns={
            "account_number": "Konto",
            "account_name": "Bezeichnung",
            "level_0": "Statement",
            "level_1": "Gruppe",
            "level_2": "Klasse",
            "level_3": "Position",
        },
        source_system="client_sachkontenstamm",
    )


def _client_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Konto": ["16100", "44000"],
            "Bezeichnung": ["Bau- und Wohnwagen", "Umsatzerlöse"],
            "Statement": ["BS", "PL"],
            "Gruppe": ["Assets", "P&L"],
            "Klasse": ["Fixed assets", "Revenue"],
            "Position": ["Tangible assets", "Net sales"],
        }
    )


def _accounts(session: Session) -> dict:
    rows = session.execute(
        text(
            "SELECT account_number_group, fiscal_year, level_0, level_2, level_3, "
            "account_name, source_system FROM dim_gl_account ORDER BY account_number_group"
        )
    ).fetchall()
    return {r[0]: dict(r._mapping) for r in rows}


# --------------------------------------------------------------------------- #
# client_coa.load_client_coa
# --------------------------------------------------------------------------- #
class TestLoadClientCoa:
    def test_one_to_one_load_populates_levels(self):
        s = _make_session()
        out = CC.load_client_coa(s, _client_df(), profile=_client_profile())
        assert out["rows"] == 2
        assert out["accounts"] == 2

        accs = _accounts(s)
        # account_number_group = entity_prefix(2) + zfill(account,6) -> 8 chars
        assert "01016100" in accs and "01044000" in accs
        assert accs["01016100"]["level_0"] == "BS"
        assert accs["01016100"]["level_3"] == "Tangible assets"
        assert accs["01044000"]["level_0"] == "PL"
        assert accs["01044000"]["level_2"] == "Revenue"
        assert accs["01016100"]["source_system"] == "client_sachkontenstamm"

    def test_scope_overrides_entity_and_year(self):
        s = _make_session()
        # profile says entity 01 / 2024; scope forces entity 02 / 2023
        CC.load_client_coa(
            s, _client_df(),
            scope=CC.ClientCoaScope(entity_prefix="02", fiscal_year=2023),
            profile=_client_profile(),
        )
        accs = _accounts(s)
        assert "02016100" in accs
        assert accs["02016100"]["fiscal_year"] == 2023

    def test_scope_dict_and_tuple_forms(self):
        s = _make_session()
        CC.load_client_coa(s, _client_df(), scope={"entity_prefix": "03", "fiscal_year": 2022},
                           profile=_client_profile())
        s2 = _make_session()
        CC.load_client_coa(s2, _client_df(), scope=("03", 2022), profile=_client_profile())
        assert "03016100" in _accounts(s)
        assert "03016100" in _accounts(s2)

    def test_idempotent_reload(self):
        s = _make_session()
        CC.load_client_coa(s, _client_df(), profile=_client_profile())
        CC.load_client_coa(s, _client_df(), profile=_client_profile())
        accs = _accounts(s)
        assert len(accs) == 2  # upsert, not duplicate

    def test_profile_required(self):
        s = _make_session()
        with pytest.raises(ValueError):
            CC.load_client_coa(s, _client_df(), profile=None)

    def test_profile_as_dict(self):
        s = _make_session()
        from etl.mapping_account import account_profile_to_dict

        prof_dict = account_profile_to_dict(_client_profile())
        CC.load_client_coa(s, _client_df(), profile=prof_dict)
        assert "01016100" in _accounts(s)


# --------------------------------------------------------------------------- #
# project_coa_override.replay_project_overrides
# --------------------------------------------------------------------------- #
def _seed_account(s: Session, ang: str, fy: int, **levels) -> None:
    cols = ["account_number_group", "fiscal_year", *levels.keys()]
    ph = ", ".join(f":{c}" for c in cols)
    params = {"account_number_group": ang, "fiscal_year": fy, **levels}
    s.execute(text(f"INSERT INTO dim_gl_account ({', '.join(cols)}) VALUES ({ph})"), params)


def _seed_override(s: Session, ang: str, fy: int, *, project_id="default", **levels) -> None:
    cols = ["project_id", "account_number_group", "fiscal_year", *levels.keys()]
    ph = ", ".join(f":{c}" for c in cols)
    params = {"project_id": project_id, "account_number_group": ang, "fiscal_year": fy, **levels}
    s.execute(text(f"INSERT INTO dim_project_coa_override ({', '.join(cols)}) VALUES ({ph})"), params)


def _scope(years=None):
    class _S:
        def __init__(self, ys):
            self.years = ys or []
    return _S(years)


class TestReplayOverride:
    def test_noop_when_table_empty(self):
        s = _make_session()
        _seed_account(s, "0116100", 2024, level_2="Original", level_3_sort=13)
        out = PCO.replay_project_overrides(s, _scope())
        assert out["classification_refreshed"] == 0
        accs = _accounts(s)
        assert accs["0116100"]["level_2"] == "Original"  # untouched

    def test_replay_applies_levels_and_sort(self):
        s = _make_session()
        _seed_account(s, "0116100", 2024, level_2="Original", level_3_sort=13)
        _seed_override(s, "0116100", 2024, level_2="Remapped", level_3_sort=99)
        out = PCO.replay_project_overrides(s, _scope())
        assert out["classification_refreshed"] == 1
        accs = _accounts(s)
        assert accs["0116100"]["level_2"] == "Remapped"
        sort_val = s.execute(
            text("SELECT level_3_sort FROM dim_gl_account WHERE account_number_group='0116100'")
        ).scalar()
        assert sort_val == 99

    def test_scope_years_restrict_replay(self):
        s = _make_session()
        _seed_account(s, "0116100", 2023, level_2="Orig23")
        _seed_account(s, "0116100", 2024, level_2="Orig24")
        _seed_override(s, "0116100", 2023, level_2="New23")
        _seed_override(s, "0116100", 2024, level_2="New24")
        out = PCO.replay_project_overrides(s, _scope(years=[2024]))
        assert out["classification_refreshed"] == 1
        rows = s.execute(
            text("SELECT fiscal_year, level_2 FROM dim_gl_account WHERE account_number_group='0116100' ORDER BY fiscal_year")
        ).fetchall()
        d = {r[0]: r[1] for r in rows}
        assert d[2023] == "Orig23"  # out of scope, untouched
        assert d[2024] == "New24"   # in scope, replayed

    def test_override_for_absent_account_is_ignored(self):
        s = _make_session()
        _seed_account(s, "0116100", 2024, level_2="Original")
        _seed_override(s, "0199999", 2024, level_2="Ghost")  # no matching account
        out = PCO.replay_project_overrides(s, _scope())
        assert out["classification_refreshed"] == 0

    def test_missing_override_table_is_noop(self):
        s = _make_session(with_override_table=False)
        _seed_account(s, "0116100", 2024, level_2="Original")
        out = PCO.replay_project_overrides(s, _scope())
        assert out.get("override_table_missing") is True
        assert out["classification_refreshed"] == 0


class TestCaptureOverrides:
    def test_capture_upserts(self):
        s = _make_session()
        n = PCO.capture_overrides_from_accounts(
            s,
            [{"account_number_group": "0116100", "fiscal_year": 2024,
              "level_2": "Fixed assets", "level_3_sort": 13}],
        )
        assert n == 1
        row = s.execute(
            text("SELECT level_2, level_3_sort FROM dim_project_coa_override "
                 "WHERE account_number_group='0116100' AND fiscal_year=2024")
        ).fetchone()
        assert row[0] == "Fixed assets" and row[1] == 13

    def test_capture_idempotent(self):
        s = _make_session()
        acc = [{"account_number_group": "0116100", "fiscal_year": 2024, "level_2": "A"}]
        PCO.capture_overrides_from_accounts(s, acc)
        acc[0]["level_2"] = "B"
        PCO.capture_overrides_from_accounts(s, acc)
        rows = s.execute(text("SELECT COUNT(*) FROM dim_project_coa_override")).scalar()
        assert rows == 1
        val = s.execute(
            text("SELECT level_2 FROM dim_project_coa_override WHERE account_number_group='0116100'")
        ).scalar()
        assert val == "B"

    def test_capture_skips_rows_without_keys(self):
        s = _make_session()
        n = PCO.capture_overrides_from_accounts(
            s, [{"level_2": "x"}, {"account_number_group": "0116100", "fiscal_year": None}]
        )
        assert n == 0
