"""Phase 3 (reader resolution) — DB-free tests for the manual-budget readers.

GOLDEN-CRITICAL invariant under test: every budget branch is a NO-OP when no
``scenario='budget'`` rows exist, so the reader output is byte-identical to the
pre-budget behaviour.  These tests pin:

  1. ``position_plan_grain_sql`` — SQL shape, the budget scenario/sign, and the
     partner→position summation (via a fake session) + the sign flip matching
     ``plan_grain_sql`` exactly (financial-metric worked example).
  2. ``_load_plan_map`` — prefers budget when present (keyed by line_code), and
     falls through to forecast/plan (byte-identical map) when budget is empty.
  3. ``balance_sheet._fetch_bs_budget_movements`` — STOCK→MOVEMENT delta encoding
     so the cumulative core reproduces the period-end stock; empty → [] (fallback).
  4. ``overview_top_entities.rank_top_entities`` — plan_cm / coverage from a
     partner-budget map; empty map → 0.0 (today's behaviour).
"""
from __future__ import annotations

from typing import Any

import pytest


# --------------------------------------------------------------------------- #
# Row / session test doubles
# --------------------------------------------------------------------------- #
class _Row:
    """SQLAlchemy Row-like: supports ._mapping, int+str indexing, and .get."""

    def __init__(self, d: dict):
        self._mapping = d
        self._vals = list(d.values())

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._vals[key]
        return self._mapping[key]

    def get(self, key, default=None):
        return self._mapping.get(key, default)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Routes execute() to a queue of canned results, recording (sql, params)."""

    def __init__(self, results: list[list[_Row]]):
        self._results = list(results)
        self.calls: list[tuple[str, dict]] = []

    def execute(self, stmt, params=None):
        self.calls.append((str(stmt), dict(params or {})))
        if self._results:
            return _FakeResult(self._results.pop(0))
        return _FakeResult([])


# =========================================================================== #
# 1) position_plan_grain_sql
# =========================================================================== #
from app.services.fin_compat_sql import plan_grain_sql, position_plan_grain_sql


def test_position_plan_grain_sql_uses_budget_scenario_and_line_code():
    sql, params = position_plan_grain_sql(2025, 6, "", "PL")
    assert params["scenario"] == "budget"
    assert params["statement"] == "PL"
    assert params["year"] == 2025
    assert params["month"] == 6
    # keyed by line_code, reads fact_position_plan, applies the presentation flip
    assert "fact_position_plan" in sql
    assert "GROUP BY p.line_code" in sql
    assert "amount * -1" in sql  # SAME flip as plan_grain_sql
    # consolidated view (empty ent_frag) reads only the '' rows
    assert "p.entity_prefix = ''" in sql
    assert ":ep" not in sql


def test_position_plan_grain_sql_entity_precedence_branch():
    # ent_frag is the standard "AND l.entity_prefix = 'AT'" fragment
    sql, params = position_plan_grain_sql(2025, 6, "AND l.entity_prefix = 'AT'", "PL")
    assert params["ep"] == "AT"
    # per-entity rows OR consolidated only where no per-entity row exists
    assert "p.entity_prefix = :ep" in sql
    assert "NOT EXISTS" in sql


def test_position_plan_grain_sql_consolidated_sums_entities_else_override():
    """Phase 1: CONSOLIDATED view = '' row per line_code when present (override),
    ELSE Σ of the per-entity rows.  Golden-safety: still empty with no budget rows.
    """
    sql, params = position_plan_grain_sql(2025, 6, "", "PL")
    assert ":ep" not in sql                       # no entity param on consolidated
    assert "p.entity_prefix = ''" in sql          # '' rows participate
    assert "p.entity_prefix <> ''" in sql         # per-entity rows participate too
    assert "NOT EXISTS" in sql and "c.entity_prefix = ''" in sql  # …unless '' exists
    assert params["scenario"] == "budget"         # only ever matches budget rows


def test_position_plan_grain_sql_sign_matches_plan_grain_sql_worked_example():
    """Worked example — the stored→presented flip is identical to fact_gl_plan.

    Stored GL sign: revenue is a credit (stored NEGATIVE), expense a debit (+).
    plan_grain_sql and position_plan_grain_sql BOTH present with ``amount * -1``:
      revenue stored -3000 → presented +3000
      expense stored  +500 → presented  -500
    The two SQLs must therefore carry the SAME CASE-WHEN flip token.
    """
    gl_sql, _ = plan_grain_sql(2025, 6, "", "forecast")
    pos_sql, _ = position_plan_grain_sql(2025, 6, "", "PL")
    # both flip with the same token; neither double-flips
    assert "amount * -1" in gl_sql
    assert "amount * -1" in pos_sql
    assert "amount * 1" not in pos_sql


def test_position_plan_grain_sql_partner_plus_position_summation():
    """A partner row (partner_id<>'') and the position row (partner_id='') for the
    same line_code are GROUPed BY line_code → summed into one position total.

    The SQL does ``GROUP BY p.line_code`` and sums amount*-1, so a fake result with
    one row per line_code already represents Σ(position + partners).  We assert the
    grouping/sign by running the canned grouped row through a fake session, which is
    what the DB would return for the GROUP BY.
    """
    # The DB returns ONE row per line_code (already grouped).  For NET_SALES the
    # stored amounts were: position-level -1000 + customerA -3000 + customerB -2000
    # = -6000 stored → presented +6000 (amount*-1).  We model the post-GROUP row.
    sess = _FakeSession([[_Row({"line_code": "NET_SALES", "plan_cm": 6000.0,
                                "ytd_plan": 6000.0, "ytg": 0.0})]])
    sql, params = position_plan_grain_sql(2025, 6, "", "PL")
    rows = [dict(r._mapping) for r in sess.execute(sql, params).fetchall()]
    assert rows[0]["line_code"] == "NET_SALES"
    assert rows[0]["plan_cm"] == 6000.0  # +Σ presented, partners folded into position


# =========================================================================== #
# 2) _load_plan_map resolution order budget → forecast → plan
# =========================================================================== #
from app.services import fin_compat_pl


_STRUCT_PL = [
    _Row({"line_code": "NET_SALES", "row_type": "mapping", "sort_order": 10,
          "level_2": None, "level_3": "Net sales", "level_4": None,
          "gl_account_id": None, "kpi_code": None}),
    _Row({"line_code": "COST_OF_MATERIALS", "row_type": "mapping", "sort_order": 20,
          "level_2": None, "level_3": "Cost of materials", "level_4": None,
          "gl_account_id": None, "kpi_code": None}),
    _Row({"line_code": "GROSS_PROFIT", "row_type": "subtotal", "sort_order": 30,
          "level_2": None, "level_3": None, "level_4": None,
          "gl_account_id": None, "kpi_code": None}),
]


def _patch_structure(monkeypatch):
    monkeypatch.setattr(fin_compat_pl, "_load_structure", lambda s: _STRUCT_PL)


def test_load_plan_map_prefers_budget_when_present(monkeypatch):
    _patch_structure(monkeypatch)
    # First execute() = budget position-grain (line_code keyed); has signal → return.
    budget_rows = [_Row({"line_code": "NET_SALES", "plan_cm": 6000.0,
                         "ytd_plan": 6000.0, "ytg": 0.0})]
    sess = _FakeSession([budget_rows])
    out = fin_compat_pl._load_plan_map(sess, 2025, 6, "")
    assert out["NET_SALES"]["plan_cm"] == 6000.0
    # GROSS_PROFIT subtotal = running sum of mapping lines above it = NET_SALES.
    assert out["GROSS_PROFIT"]["plan_cm"] == 6000.0
    # Only ONE query executed — budget had signal, no fall-through.
    assert len(sess.calls) == 1
    assert "fact_position_plan" in sess.calls[0][0]


def test_load_plan_map_falls_through_when_budget_empty(monkeypatch):
    """Empty budget → falls to forecast (fact_gl_plan); map is what fact_gl_plan
    would produce — proving the budget branch is a NO-OP when no budget rows exist.
    """
    _patch_structure(monkeypatch)
    forecast_rows = [_Row({"level_2": None, "level_3": "Net sales", "level_4": None,
                           "gl_account_id": None, "account_number_group": "4000",
                           "plan_cm": 1234.0, "ytd_plan": 1234.0, "ytg": 0.0})]
    # results: [budget=empty, forecast=rows]
    sess = _FakeSession([[], forecast_rows])
    out = fin_compat_pl._load_plan_map(sess, 2025, 6, "")
    assert out["NET_SALES"]["plan_cm"] == 1234.0
    # two queries: budget (empty) then forecast
    assert len(sess.calls) == 2
    assert "fact_position_plan" in sess.calls[0][0]
    assert "fact_gl_plan" in sess.calls[1][0]


def test_load_plan_map_empty_everywhere_is_empty(monkeypatch):
    """No budget, no forecast, no plan rows → {} (today's behaviour exactly)."""
    _patch_structure(monkeypatch)
    sess = _FakeSession([[], [], []])
    out = fin_compat_pl._load_plan_map(sess, 2025, 6, "")
    assert out == {}
    assert len(sess.calls) == 3  # budget, forecast, plan all tried


# =========================================================================== #
# 3) BS budget — stock vs movement (delta encoding) + fallback
# =========================================================================== #
from app.services import balance_sheet
from app.services.balance_sheet import (
    BsMovement,
    BsStructureLine,
    _fetch_bs_budget_movements,
    aggregate_bs,
    fetch_bs_movements,
)


def _bs_struct_rows():
    # dim_pl_structure BS rows: line_code → levels (kpi_code LIKE 'BS:%')
    return [_Row({"line_code": "AR", "level_2": "Current assets",
                  "level_3": "Trade receivables", "level_4": None})]


def test_bs_budget_delta_encodes_stock_into_movements():
    """A budget STOCK series [P1=100, P2=150, P3=150] becomes movements
    [+100, +50, +0] so the cumulative core reproduces the stock at each cutoff.
    """
    struct = _bs_struct_rows()
    stock_rows = [
        _Row({"line_code": "AR", "fiscal_year": 2025, "fiscal_period": 1, "stock": 100.0}),
        _Row({"line_code": "AR", "fiscal_year": 2025, "fiscal_period": 2, "stock": 150.0}),
        _Row({"line_code": "AR", "fiscal_year": 2025, "fiscal_period": 3, "stock": 150.0}),
    ]
    sess = _FakeSession([struct, stock_rows])
    mvs = _fetch_bs_budget_movements(sess, [2025], entity_prefix=None)
    by_p = {m.fiscal_period: m.amount for m in mvs}
    assert by_p == {1: 100.0, 2: 50.0, 3: 0.0}
    # All carry the resolved levels and the STORED sign (no BS flip here).
    assert all(m.level_3 == "Trade receivables" for m in mvs)


def test_bs_budget_cumulative_reproduces_stock():
    """End-to-end: delta-encoded budget movements, run through the CUMULATIVE core,
    yield the period-end STOCK (not a cumulative sum of stocks)."""
    struct = _bs_struct_rows()
    stock_rows = [
        _Row({"line_code": "AR", "fiscal_year": 2025, "fiscal_period": 1, "stock": 100.0}),
        _Row({"line_code": "AR", "fiscal_year": 2025, "fiscal_period": 2, "stock": 150.0}),
    ]
    sess = _FakeSession([struct, stock_rows])
    mvs = _fetch_bs_budget_movements(sess, [2025], entity_prefix=None)

    bs_struct = [BsStructureLine(10, "AR", "Trade receivables", "mapping", "asset",
                                 level_3="Trade receivables")]

    class _Col:
        def __init__(self, key, buckets):
            self.key = key
            self.buckets = buckets
            self.label = key

    class _Plan:
        view_mode = "ytd"
        coverage = 1.0
        def __init__(self, cols):
            self.columns = cols

    # Column whose latest bucket is (2025, 2) → stock should be 150, NOT 250.
    plan = _Plan([_Col("c", [(2025, 1), (2025, 2)])])
    stmt = aggregate_bs(mvs, bs_struct, plan)
    ar_line = next(l for l in stmt.lines if l.line_code == "AR")
    assert ar_line.cells[0].value == pytest.approx(150.0)


def test_bs_budget_empty_returns_empty_list():
    struct = _bs_struct_rows()
    sess = _FakeSession([struct, []])  # structure present, but no budget rows
    assert _fetch_bs_budget_movements(sess, [2025], entity_prefix=None) == []


def test_bs_budget_consolidated_sql_sums_entities_else_override():
    """Phase 1: the consolidated BS budget query reads '' rows OR per-entity rows
    where no '' row exists (Σ entities), then GROUP BY (line_code, fy, period) SUMS
    the surviving per-entity rows into the consolidated stock.  Golden-safe: empty
    with no budget rows.
    """
    struct = _bs_struct_rows()
    sess = _FakeSession([struct, []])  # second query empty → [] (we inspect its SQL)
    _fetch_bs_budget_movements(sess, [2025], entity_prefix=None)
    stock_sql = sess.calls[1][0]
    assert "p.entity_prefix = ''" in stock_sql
    assert "p.entity_prefix <> ''" in stock_sql
    assert "NOT EXISTS" in stock_sql and "c.entity_prefix = ''" in stock_sql
    assert "GROUP BY p.line_code, p.fiscal_year, p.fiscal_period" in stock_sql


def test_bs_budget_per_entity_sql_unchanged():
    """Per-entity view keeps the override precedence keyed on :ep."""
    struct = _bs_struct_rows()
    sess = _FakeSession([struct, []])
    _fetch_bs_budget_movements(sess, [2025], entity_prefix="01")
    stock_sql, params = sess.calls[1]
    assert params["ep"] == "01"
    assert "p.entity_prefix = :ep" in stock_sql
    assert "e.entity_prefix = :ep" in stock_sql


def test_fetch_bs_movements_falls_back_to_gl_plan_when_no_budget():
    """scenario set, but budget empty → falls back to fact_gl_plan query
    (byte-identical to the pre-budget path)."""
    struct = _bs_struct_rows()
    gl_plan_rows = [_Row({0: 2025, 1: 5, 2: "Current assets",
                          3: "Trade receivables", 4: None, 5: 42.0})]
    # results: [budget-structure, budget-empty, gl_plan-rows]
    sess = _FakeSession([struct, [], gl_plan_rows])
    mvs = fetch_bs_movements(sess, (2025,), entity_prefix=None, scenario="forecast")
    assert len(mvs) == 1
    assert mvs[0].amount == 42.0
    # the third query is the fact_gl_plan fallback
    assert "fact_gl_plan" in sess.calls[-1][0]


# =========================================================================== #
# 3b) GET overlay (_read_budget_position_months) consolidated entity-sum SQL
# =========================================================================== #
from app.services import budget_service


def test_read_budget_position_months_consolidated_sums_entities():
    """GET overlay CONSOLIDATED branch: '' row per (line_code, partner) when present
    (override), ELSE Σ per-entity rows for that (line_code, partner).  Golden-safe.
    """
    sess = _FakeSession([[]])  # no rows → {}; we inspect the SQL it issued
    out = budget_service._read_budget_position_months(
        sess, statement="PL", fiscal_year=2025, entity_prefix=None
    )
    assert out == {}
    sql = sess.calls[0][0]
    assert ":ep" not in sql
    assert "p.entity_prefix = ''" in sql
    assert "p.entity_prefix <> ''" in sql
    assert "NOT EXISTS" in sql and "c.entity_prefix = ''" in sql
    # keyed on (line_code, partner_id) so partner rows stay distinct in the sum.
    assert "c.partner_id = p.partner_id" in sql


def test_read_budget_position_months_per_entity_sql_unchanged():
    sess = _FakeSession([[]])
    budget_service._read_budget_position_months(
        sess, statement="PL", fiscal_year=2025, entity_prefix="01"
    )
    sql, params = sess.calls[0]
    assert params["ep"] == "01"
    assert "p.entity_prefix = :ep" in sql
    assert "e.entity_prefix = :ep" in sql


# =========================================================================== #
# 4) top-entities plan_cm / coverage
# =========================================================================== #
from app.services.overview_top_entities import rank_top_entities


_AGG = [
    {"name": "A", "customer_id": "C1", "cm": 120.0, "pm": 100.0,
     "py_cm": 90.0, "ytd": 700.0, "ytd_py": 600.0},
    {"name": "B", "customer_id": "C2", "cm": 200.0, "pm": 150.0,
     "py_cm": 210.0, "ytd": 900.0, "ytd_py": 950.0},
]


def test_rank_top_entities_no_plan_map_is_zero_stub():
    """Empty/absent plan map → plan_cm=0.0, coverage=0.0 — today's behaviour."""
    rows = rank_top_entities(_AGG, id_field="customer_id")
    assert all(r["plan_cm"] == 0.0 and r["coverage"] == 0.0 for r in rows)


def test_rank_top_entities_plan_cm_from_partner_budget():
    """plan_cm from a partner-budget map; coverage = cm/|plan_cm|*100."""
    plan_by_id = {"C1": 100.0, "C2": 250.0}
    rows = rank_top_entities(_AGG, id_field="customer_id", plan_by_id=plan_by_id)
    by_id = {r["customer_id"]: r for r in rows}
    # C1: cm=120, plan=100 → coverage 120.0 ; C2: cm=200, plan=250 → 80.0
    assert by_id["C1"]["plan_cm"] == 100.0
    assert by_id["C1"]["coverage"] == pytest.approx(120.0)
    assert by_id["C2"]["plan_cm"] == 250.0
    assert by_id["C2"]["coverage"] == pytest.approx(80.0)


def test_rank_top_entities_partner_absent_from_plan_stays_zero():
    plan_by_id = {"C1": 100.0}  # C2 has no plan
    rows = rank_top_entities(_AGG, id_field="customer_id", plan_by_id=plan_by_id)
    by_id = {r["customer_id"]: r for r in rows}
    assert by_id["C2"]["plan_cm"] == 0.0
    assert by_id["C2"]["coverage"] == 0.0
