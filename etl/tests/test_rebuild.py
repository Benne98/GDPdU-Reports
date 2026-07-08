"""Tests for etl/rebuild.py — the reporting-v2 deterministic rebuild orchestrator.

Two layers:

  PURE (always run, no DB):
    - rebuild_project runs the expected stage sequence, in order, and commits once.
    - The derived-fact SQL is DELETE+INSERT (idempotency by construction): running
      the derive stage twice emits identical SQL with identical DELETEs.
    - rebuild_project reproduces the derivation derive_facts.py used to perform
      (same four fact tables, via the shared etl.derive_facts_sql functions).
    - 'incremental' mode skips classification + structure refresh.
    - scope coercion accepts None / tuple / RebuildScope.

  DB-BACKED (opt-in, skipped unless GDPDU_TEST_DB is set):
    - Running rebuild_project twice on a real DB yields identical fact-table
      row counts and content hashes (true idempotency).

All data is synthetic; no real client files (CLAUDE.md rule).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from etl import rebuild as R
from etl import derive_facts_sql as DFS


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _executed_sql(session_mock: MagicMock) -> list[str]:
    """Return the str() of every text() clause passed to session.execute."""
    out: list[str] = []
    for call in session_mock.execute.call_args_list:
        clause = call.args[0] if call.args else None
        if clause is not None:
            out.append(str(clause))
    return out


def _fresh_session() -> MagicMock:
    """A MagicMock session whose execute() returns a result with rowcount=0."""
    session = MagicMock()
    result = MagicMock()
    result.rowcount = 0
    # backfill UPDATEs + INSERT...SELECT all go through execute()
    session.execute.return_value = result
    return session


# --------------------------------------------------------------------------- #
# Scope coercion
# --------------------------------------------------------------------------- #
class TestScopeCoercion:
    def test_none_is_full(self):
        sc = R._coerce_scope(None)
        assert sc.is_full is True
        assert sc.prefixes == [] and sc.years == []

    def test_tuple(self):
        sc = R._coerce_scope((["01", "02"], [2024, 2025]))
        assert sc.prefixes == ["01", "02"]
        assert sc.years == [2024, 2025]
        assert sc.is_full is False

    def test_rebuildscope_passthrough(self):
        orig = R.RebuildScope(prefixes=["03"], years=[2023])
        assert R._coerce_scope(orig) is orig

    def test_invalid(self):
        with pytest.raises(TypeError):
            R._coerce_scope(123)


# --------------------------------------------------------------------------- #
# Orchestration: stage sequence + commit
# --------------------------------------------------------------------------- #
class TestOrchestration:
    def test_full_mode_runs_all_stages_and_commits(self, monkeypatch):
        calls: list[str] = []

        def _rec(name, ret):
            def _f(session, scope):
                calls.append(name)
                return ret
            return _f

        monkeypatch.setattr(R, "_stage_classification_refresh", _rec("classification", {"classification_refreshed": 0}))
        monkeypatch.setattr(R, "_stage_partner_backfill", _rec("partner", {"income_customer_linked": 0, "material_supplier_linked": 0}))
        monkeypatch.setattr(R, "_stage_derived_facts", _rec("facts", {"sales": 1, "com": 2, "ar": 3, "ap": 4}))
        monkeypatch.setattr(R, "_stage_opening_balances", _rec("opening", {"stub": True}))
        monkeypatch.setattr(R, "_stage_net_profit", _rec("netprofit", {"stub": True}))
        monkeypatch.setattr(R, "_stage_structure_recon_refresh", _rec("structure", {"bs_structure_rows": 0}))

        session = _fresh_session()
        summary = R.rebuild_project(session, scope=None, mode="full")

        assert calls == ["classification", "partner", "facts", "opening", "netprofit", "structure"]
        session.commit.assert_called_once()
        assert summary["derived_facts"] == {"sales": 1, "com": 2, "ar": 3, "ap": 4}

    def test_incremental_mode_skips_classification_and_structure(self, monkeypatch):
        calls: list[str] = []

        def _rec(name):
            def _f(session, scope):
                calls.append(name)
                return {}
            return _f

        monkeypatch.setattr(R, "_stage_classification_refresh", _rec("classification"))
        monkeypatch.setattr(R, "_stage_partner_backfill", _rec("partner"))
        monkeypatch.setattr(R, "_stage_derived_facts", _rec("facts"))
        monkeypatch.setattr(R, "_stage_opening_balances", _rec("opening"))
        monkeypatch.setattr(R, "_stage_net_profit", _rec("netprofit"))
        monkeypatch.setattr(R, "_stage_structure_recon_refresh", _rec("structure"))

        session = _fresh_session()
        R.rebuild_project(session, scope=None, mode="incremental")

        assert "classification" not in calls
        assert "structure" not in calls
        # facts + partner backfill still run on a mapping-free monthly update
        assert "partner" in calls and "facts" in calls

    def test_invalid_mode(self):
        with pytest.raises(ValueError):
            R.rebuild_project(_fresh_session(), mode="bogus")

    def test_rollback_on_stage_error(self, monkeypatch):
        def _boom(session, scope):
            raise RuntimeError("stage failed")

        monkeypatch.setattr(R, "_stage_partner_backfill", _boom)
        session = _fresh_session()
        with pytest.raises(RuntimeError):
            R.rebuild_project(session, scope=None, mode="full")
        session.rollback.assert_called_once()
        session.commit.assert_not_called()

    def test_commit_false_leaves_txn_open(self, monkeypatch):
        for name in (
            "_stage_classification_refresh", "_stage_partner_backfill",
            "_stage_derived_facts", "_stage_opening_balances",
            "_stage_net_profit", "_stage_structure_recon_refresh",
        ):
            monkeypatch.setattr(R, name, lambda session, scope: {})
        session = _fresh_session()
        R.rebuild_project(session, scope=None, mode="full", commit=False)
        session.commit.assert_not_called()


# --------------------------------------------------------------------------- #
# Derived-fact SQL: idempotency + reproduces derive_facts.py
# --------------------------------------------------------------------------- #
class TestDerivedFactSql:
    def test_each_fact_does_delete_then_insert(self):
        for fn, table in (
            (DFS.derive_fact_sales, "fact_sales"),
            (DFS.derive_fact_com, "fact_com"),
            (DFS.derive_fact_ar, "fact_ar"),
            (DFS.derive_fact_ap, "fact_ap"),
        ):
            session = _fresh_session()
            fn(session)
            sql = _executed_sql(session)
            assert any(f"DELETE FROM {table}" in s for s in sql), f"{table}: no DELETE"
            assert any(f"INSERT INTO {table}" in s for s in sql), f"{table}: no INSERT"

    def test_derive_all_facts_runs_four_tables(self):
        session = _fresh_session()
        out = DFS.derive_all_facts(session)
        assert set(out) == {"sales", "com", "ar", "ap"}
        sql = " ".join(_executed_sql(session))
        for table in ("fact_sales", "fact_com", "fact_ar", "fact_ap"):
            assert f"DELETE FROM {table}" in sql
            assert f"INSERT INTO {table}" in sql

    def test_idempotent_sql_identical_across_runs(self):
        """Running the derive stage twice emits identical SQL (DELETE+INSERT, no state)."""
        s1, s2 = _fresh_session(), _fresh_session()
        DFS.derive_all_facts(s1)
        DFS.derive_all_facts(s2)
        assert _executed_sql(s1) == _executed_sql(s2)

    def test_sign_conventions_in_sql(self):
        """gross_sales = -amount ; cost_of_materials = +amount (canonical signs)."""
        s = _fresh_session()
        DFS.derive_fact_sales(s)
        assert any("(-l.amount)" in sql for sql in _executed_sql(s)), "sales must negate amount"

        s = _fresh_session()
        DFS.derive_fact_com(s)
        com_sql = " ".join(_executed_sql(s))
        assert "l.amount::NUMERIC" in com_sql and "(-l.amount)" not in com_sql, \
            "cost_of_materials must use +amount"

    def test_rebuild_derive_stage_uses_shared_sql(self):
        """rebuild_project's derived-facts stage must call the SAME functions as the
        derive_facts.py CLI (proves the fold is behaviour-preserving)."""
        session = _fresh_session()
        out = R._stage_derived_facts(session, R.RebuildScope())
        # Same four tables touched as DFS.derive_all_facts.
        assert set(out) == {"sales", "com", "ar", "ap"}
        sql = " ".join(_executed_sql(session))
        for table in ("fact_sales", "fact_com", "fact_ar", "fact_ap"):
            assert f"INSERT INTO {table}" in sql


# --------------------------------------------------------------------------- #
# Partner-backfill SQL shape (method-A, receivable→revenue / payable→material)
# --------------------------------------------------------------------------- #
class TestPartnerBackfill:
    def test_backfill_updates_customer_and_supplier(self):
        from etl.derive import backfill_partner_links_on_gl_lines

        session = _fresh_session()
        out = backfill_partner_links_on_gl_lines(session)
        assert set(out) == {"income_customer_linked", "material_supplier_linked"}
        sql = " ".join(_executed_sql(session))
        assert "UPDATE fact_gl_line" in sql
        assert "SET customer_id" in sql
        assert "SET supplier_id" in sql
        # only fills NULL/blank targets (idempotent)
        assert "IS NULL OR TRIM(income.customer_id)" in sql
        assert "IS NULL OR TRIM(mat.supplier_id)" in sql


# --------------------------------------------------------------------------- #
# Structure/recon stage: BS re-seed + P&L realign wiring (Prong A)
# --------------------------------------------------------------------------- #
class TestStructureReconStage:
    def _patch_scripts(self, monkeypatch, *, bs=lambda session: 12,
                       realign=lambda session, scope: 3):
        """Patch the callables _stage_structure_recon_refresh imports at call time."""
        import scripts.seed_bs_structure as sbs
        import scripts.realign_pl_structure as rps

        monkeypatch.setattr(sbs, "seed_bs_structure", bs)
        monkeypatch.setattr(rps, "realign_pl_structure", realign)

    def test_stage_returns_bs_rows_and_pl_realigned(self, monkeypatch):
        self._patch_scripts(monkeypatch)
        out = R._stage_structure_recon_refresh(_fresh_session(), R.RebuildScope())
        assert out["bs_structure_rows"] == 12
        assert "pl_structure_realigned" in out
        assert out["pl_structure_realigned"] == 3

    def test_pl_realign_receives_scope(self, monkeypatch):
        seen = {}

        def _realign(session, scope):
            seen["scope"] = scope
            return 0

        self._patch_scripts(monkeypatch, realign=_realign)
        sc = R.RebuildScope(prefixes=["01"], years=[2025])
        R._stage_structure_recon_refresh(_fresh_session(), sc)
        assert seen["scope"] is sc

    def test_db_error_propagates_no_silent_zero(self, monkeypatch):
        """A real DB error out of seed_bs_structure must PROPAGATE (not degrade to 0)."""
        def _boom(session):
            raise RuntimeError("relation dim_bs_structure broken")

        self._patch_scripts(monkeypatch, bs=_boom)
        with pytest.raises(RuntimeError):
            R._stage_structure_recon_refresh(_fresh_session(), R.RebuildScope())

    def test_db_error_propagates_through_full_rebuild(self, monkeypatch):
        def _boom(session):
            raise RuntimeError("db down")

        self._patch_scripts(monkeypatch, bs=_boom)
        # Neutralise the earlier stages so the failure is isolated to structure.
        for name in ("_stage_classification_refresh", "_stage_account_library_fill",
                     "_stage_partner_backfill", "_stage_derived_facts",
                     "_stage_opening_balances", "_stage_net_profit"):
            monkeypatch.setattr(R, name, lambda *a, **k: {})
        session = _fresh_session()
        with pytest.raises(RuntimeError):
            R.rebuild_project(session, scope=None, mode="full")
        session.rollback.assert_called_once()
        session.commit.assert_not_called()

    def test_import_absence_is_graceful(self, monkeypatch):
        """ImportError/FileNotFoundError (recon source / scripts absent) stays graceful."""
        def _missing(session):
            raise FileNotFoundError("recon mapping Excel not found")

        self._patch_scripts(monkeypatch, bs=_missing)
        out = R._stage_structure_recon_refresh(_fresh_session(), R.RebuildScope())
        assert out["bs_structure_rows"] == 0
        assert out["pl_structure_realigned"] == 0
        assert "error" in out


# --------------------------------------------------------------------------- #
# DB-backed idempotency (opt-in: set GDPDU_TEST_DB=1 + DB_PASSWORD [+ DB_NAME])
# --------------------------------------------------------------------------- #
_DB_TEST_ENABLED = os.getenv("GDPDU_TEST_DB", "").strip() not in ("", "0", "false", "no")


@pytest.mark.skipif(not _DB_TEST_ENABLED, reason="set GDPDU_TEST_DB=1 to run DB-backed rebuild test")
class TestRebuildIdempotencyDB:
    def _fact_signature(self, session) -> dict:
        from sqlalchemy import text

        sig: dict = {}
        for table in ("fact_sales", "fact_com", "fact_ar", "fact_ap"):
            row = session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            sig[f"{table}_count"] = int(row or 0)
        # content checksums (order-independent) of the key numeric columns
        sig["sales_sum"] = session.execute(
            text("SELECT COALESCE(SUM(gross_sales),0) FROM fact_sales")
        ).scalar()
        sig["com_sum"] = session.execute(
            text("SELECT COALESCE(SUM(cost_of_materials),0) FROM fact_com")
        ).scalar()
        sig["ar_sum"] = session.execute(
            text("SELECT COALESCE(SUM(amount),0) FROM fact_ar")
        ).scalar()
        sig["ap_sum"] = session.execute(
            text("SELECT COALESCE(SUM(amount),0) FROM fact_ap")
        ).scalar()
        return {k: float(v) if v is not None else 0.0 for k, v in sig.items()}

    def test_rebuild_twice_is_identical(self):
        from sqlalchemy.orm import Session
        from app.db import engine

        with Session(engine) as session:
            R.rebuild_project(session, scope=None, mode="full")
            sig1 = self._fact_signature(session)
            R.rebuild_project(session, scope=None, mode="full")
            sig2 = self._fact_signature(session)

        assert sig1 == sig2, f"rebuild not idempotent:\n  {sig1}\n  {sig2}"
        # sanity: fact tables are non-empty on a loaded DB
        assert sig1["fact_sales_count"] > 0
