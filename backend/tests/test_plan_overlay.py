"""Cross-statement /plan-response + fail-closed entity visibility tests.

Covers all five areas mandated by the L2 Tier test brief:

  1. GOLDEN-SAFETY (highest priority)
     - ``load_position_plan_map`` returns ``{}`` for PL / BS / CF when no plan rows.
     - BS and CF narrative ``intro_facts["cm_vs_plan"]`` stay exactly ``0.0``
       when plan is empty (byte-identical to the pre-overlay behaviour).
     - ``build_pl_plan_response`` is byte-identical to
       ``build_statement_plan_response("PL", ...)`` — the thin-wrapper guarantee.
     - ``build_statement_plan_response`` with an empty plan returns
       ``has_plan_data=False`` and lines with ``plan_cm=0.0 / coverage_pct=None``.

  2. SCENARIO KWARG
     - ``position_plan_grain_sql`` default emits ``scenario='budget'`` in params.
     - Passing ``scenario='forecast'`` emits ``'forecast'``.
     - The ``amount * -1`` presentation flip is present regardless of scenario.

  3. CF SIGN ROUND-TRIP
     - ``present_to_stored(+900, "CF", ...) == -900``  (inflow)
     - ``present_to_stored(-300, "CF", ...) == +300``  (outflow)
     - Round-trip: ``stored_to_present(present_to_stored(x, "CF", c), "CF", c) == x``
     - CF == PL numerically (same sign convention, documented decision).
     - Explicit ``"CF"`` branch exists in ``present_to_stored`` (no silent fall-through).

  4. WORKED EXAMPLES (from financial sign-off)
     - PL Net sales:     plan +1000, actual +1200 → plan_vs_actual +200,  coverage 120.00
     - CF Net cash flow: plan  +610, actual  +550 → plan_vs_actual  -60,  coverage  90.16
     - BS AR:            plan  +500, actual  +540 → plan_vs_actual  +40,  coverage 108.00

  5. ENTITY-VISIBILITY FAIL-CLOSED
     - ``allowed_prefixes=set()`` → ``has_plan_data=False, lines=[]`` for all stmts.
     - ``allowed_prefixes=None`` (admin) → proceeds to plan loading (not short-circuited).
     - Non-empty prefixes pass through to plan loading with the restricted scope.

Design notes
------------
- DB-free: uses _FakeSession queue (mirrors test_budget_readers.py) for low-level tests
  and ``monkeypatch`` seam injections for higher-level ``build_statement_plan_response``.
- No production code changed; all patches are test-local.
- WC intentionally NOT tested for non-zero ``cm_vs_plan`` (WC is a known follow-up).
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest


# ===========================================================================
# Test-double helpers (mirror test_budget_readers.py pattern exactly)
# ===========================================================================

class _Row:
    """SQLAlchemy Row-like: supports ``._mapping``, int+str indexing, and ``.get``."""

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
    """Routes ``execute()`` to a queue of canned results, recording ``(sql, params)``."""

    def __init__(self, results: list[list[_Row]]):
        self._results = list(results)
        self.calls: list[tuple[str, dict]] = []

    def execute(self, stmt, params=None):
        self.calls.append((str(stmt), dict(params or {})))
        if self._results:
            return _FakeResult(self._results.pop(0))
        return _FakeResult([])


# ---------------------------------------------------------------------------
# Minimal structure-row factories (one row each, passes each statement filter)
# ---------------------------------------------------------------------------

def _pl_struct_row(line_code: str = "NET_SALES", row_type: str = "mapping",
                   sort_order: int = 10) -> _Row:
    """PL row: sort_order < 1000, line_code not starting with 'BS_'."""
    return _Row({
        "pl_line_id": 1, "sort_order": sort_order, "line_code": line_code,
        "row_type": row_type, "balance_title": line_code, "details": None,
        "calc_type": None, "level_2": None, "level_3": line_code,
        "level_4": None, "gl_account_id": None, "invert_delta": False,
        "is_bold": False, "kpi_code": None,
    })


def _bs_struct_row(line_code: str = "AR", row_type: str = "mapping",
                   sort_order: int = 1010) -> _Row:
    """BS row: sort_order >= 1000, kpi_code starts with 'BS:'."""
    return _Row({
        "pl_line_id": 2, "sort_order": sort_order, "line_code": line_code,
        "row_type": row_type, "balance_title": line_code, "details": None,
        "calc_type": None, "level_2": "Current assets", "level_3": line_code,
        "level_4": None, "gl_account_id": None, "invert_delta": False,
        "is_bold": False, "kpi_code": "BS:asset",
    })


def _cf_struct_row(line_code: str = "CF_NET_CASH_FLOW",
                   balance_title: str = "Net cash flow",
                   row_type: str = "mapping",
                   sort_order: int = 2010) -> _Row:
    """CF row: line_code starts with 'CF_', kpi_code starts with 'CF'."""
    return _Row({
        "pl_line_id": 3, "sort_order": sort_order, "line_code": line_code,
        "row_type": row_type, "balance_title": balance_title, "details": None,
        "calc_type": None, "level_2": None, "level_3": None,
        "level_4": None, "gl_account_id": None, "invert_delta": False,
        "is_bold": False, "kpi_code": "CF:detail",
    })


# ===========================================================================
# 1) GOLDEN-SAFETY — empty plan is a byte-identical no-op
# ===========================================================================

class TestGoldenSafetyEmptyPlan:
    """When no plan rows exist, every code-path must fall through byte-identically."""

    # --- load_position_plan_map returns {} for each statement ---

    def test_load_position_plan_map_pl_empty_returns_empty_dict(self):
        """PL: no plan rows → load_position_plan_map returns {} (has_signal fall-through)."""
        from app.services.fin_compat_pl import load_position_plan_map

        struct = [_pl_struct_row("NET_SALES")]
        # Queue: [dim_pl_structure rows, empty plan grain]
        sess = _FakeSession([struct, []])
        result = load_position_plan_map(sess, "PL", 2025, 6, "")

        assert result == {}
        # Exactly two DB round-trips: structure query + position_plan_grain_sql
        assert len(sess.calls) == 2
        assert "dim_pl_structure" in sess.calls[0][0]
        assert "fact_position_plan" in sess.calls[1][0]

    def test_load_position_plan_map_bs_empty_returns_empty_dict(self):
        """BS: no plan rows → load_position_plan_map returns {}."""
        from app.services.fin_compat_pl import load_position_plan_map

        struct = [_bs_struct_row("AR")]
        sess = _FakeSession([struct, []])
        result = load_position_plan_map(sess, "BS", 2025, 6, "")

        assert result == {}
        assert "fact_position_plan" in sess.calls[-1][0]
        # Statement param was set to BS
        assert sess.calls[-1][1].get("statement") == "BS"

    def test_load_position_plan_map_cf_empty_returns_empty_dict(self):
        """CF: no plan rows → load_position_plan_map returns {}."""
        from app.services.fin_compat_pl import load_position_plan_map

        # CF structure needs at least one CF row (line_code starts with CF_)
        # to satisfy _cf_struct_rows — otherwise it raises ValueError.
        struct = [_cf_struct_row("CF_NET_CASH_FLOW", "Net cash flow")]
        sess = _FakeSession([struct, []])
        result = load_position_plan_map(sess, "CF", 2025, 6, "")

        assert result == {}
        assert "fact_position_plan" in sess.calls[-1][0]
        assert sess.calls[-1][1].get("statement") == "CF"

    # --- build_statement_plan_response with empty plan ---

    def test_build_statement_plan_response_empty_plan_has_plan_data_false(
        self, monkeypatch
    ):
        """With an empty plan, response must have has_plan_data=False."""
        from app.services import fin_compat_pl

        _STRUCT = [{"line_code": "NET_SALES", "row_type": "mapping", "sort_order": 10,
                    "kpi_code": None, "level_2": None, "level_3": "Net sales",
                    "level_4": None, "balance_title": "Net sales", "gl_account_id": None}]

        monkeypatch.setattr(fin_compat_pl, "_statement_structure_rows", lambda s, st: _STRUCT)
        monkeypatch.setattr(fin_compat_pl, "_load_plan_map", lambda s, y, m, ef: {})
        monkeypatch.setattr(fin_compat_pl, "_statement_actual_running",
                            lambda s, st, y, m, ef, struct: {"NET_SALES": 0.0})
        monkeypatch.setattr(fin_compat_pl, "resolve_entity_prefix", lambda s, e: None)
        monkeypatch.setattr(fin_compat_pl, "entity_sql_fragment", lambda ep: "")

        out = fin_compat_pl.build_statement_plan_response(
            MagicMock(), "PL", 2025, 7, None
        )
        assert out["has_plan_data"] is False
        assert out["year"] == 2025
        assert out["month"] == 7
        assert out["entity"] is None

    def test_empty_plan_lines_have_zero_plan_cm_and_null_coverage(self, monkeypatch):
        """Empty plan → plan_cm=0.0, plan_vs_actual==actual, coverage_pct=None per line."""
        from app.services import fin_compat_pl

        _STRUCT = [{"line_code": "NET_SALES", "row_type": "mapping", "sort_order": 10,
                    "kpi_code": None, "level_2": None, "level_3": "Net sales",
                    "level_4": None, "balance_title": "Net sales", "gl_account_id": None}]

        monkeypatch.setattr(fin_compat_pl, "_statement_structure_rows", lambda s, st: _STRUCT)
        monkeypatch.setattr(fin_compat_pl, "_load_plan_map", lambda s, y, m, ef: {})
        monkeypatch.setattr(fin_compat_pl, "_statement_actual_running",
                            lambda s, st, y, m, ef, struct: {"NET_SALES": 1200.0})
        monkeypatch.setattr(fin_compat_pl, "resolve_entity_prefix", lambda s, e: None)
        monkeypatch.setattr(fin_compat_pl, "entity_sql_fragment", lambda ep: "")

        out = fin_compat_pl.build_statement_plan_response(
            MagicMock(), "PL", 2025, 7, None
        )
        line = next(l for l in out["lines"] if l["line_code"] == "NET_SALES")
        # plan_cm == 0 because plan_map is empty
        assert line["plan_cm"] == 0.0
        # plan_vs_actual = actual - 0 = actual
        assert line["plan_vs_actual"] == pytest.approx(1200.0)
        # coverage_pct is None because abs(plan_cm) <= 1e-6
        assert line["coverage_pct"] is None

    # --- build_pl_plan_response byte-identical to build_statement_plan_response("PL") ---

    def test_build_pl_plan_response_byte_identical_to_generic_pl_call(self, monkeypatch):
        """``build_pl_plan_response`` is a thin wrapper — output is the same dict."""
        from app.services import fin_compat_pl

        _STRUCT = [{"line_code": "NET_SALES", "row_type": "mapping", "sort_order": 10,
                    "kpi_code": None, "level_2": None, "level_3": "Net sales",
                    "level_4": None, "balance_title": "Net sales", "gl_account_id": None}]

        monkeypatch.setattr(fin_compat_pl, "_statement_structure_rows", lambda s, st: _STRUCT)
        monkeypatch.setattr(fin_compat_pl, "_load_plan_map", lambda s, y, m, ef: {})
        monkeypatch.setattr(fin_compat_pl, "_statement_actual_running",
                            lambda s, st, y, m, ef, struct: {"NET_SALES": 0.0})
        monkeypatch.setattr(fin_compat_pl, "resolve_entity_prefix", lambda s, e: None)
        monkeypatch.setattr(fin_compat_pl, "entity_sql_fragment", lambda ep: "")

        sess = MagicMock()
        generic = fin_compat_pl.build_statement_plan_response(
            sess, "PL", 2025, 7, None
        )
        wrapper = fin_compat_pl.build_pl_plan_response(sess, 2025, 7, None)

        # Full dict deep-equality — byte-identical output
        assert generic == wrapper

    # --- BS/CF narrative cm_vs_plan stays 0.0 with empty plan ---

    def test_bs_narrative_cm_vs_plan_stays_zero_with_empty_plan(self, monkeypatch):
        """BS narrative: empty plan → intro_facts['cm_vs_plan'] == 0.0 (golden-safe)."""
        from app.services import fin_compat_bs

        # Patch the plan loader bound into fin_compat_bs's namespace
        monkeypatch.setattr(fin_compat_bs, "load_position_plan_map", lambda *a, **k: {})

        # MagicMock session: statement_for_narrative fails gracefully → rows=[], labels={}
        result = fin_compat_bs.build_bs_narrative(MagicMock(), 2025, 7, None)

        assert "intro_facts" in result
        assert result["intro_facts"]["cm_vs_plan"] == 0.0

    def test_cf_narrative_cm_vs_plan_stays_zero_with_empty_plan(self, monkeypatch):
        """CF narrative: empty plan → intro_facts['cm_vs_plan'] == 0.0 (golden-safe)."""
        from app.services import fin_compat_cf

        monkeypatch.setattr(fin_compat_cf, "load_position_plan_map", lambda *a, **k: {})

        result = fin_compat_cf.build_cf_narrative(MagicMock(), 2025, 7, None)

        assert "intro_facts" in result
        assert result["intro_facts"]["cm_vs_plan"] == 0.0


# ===========================================================================
# 2) SCENARIO KWARG — position_plan_grain_sql
# ===========================================================================

class TestScenarioKwarg:
    """position_plan_grain_sql scenario kwarg behaviour and SQL invariants."""

    def test_default_scenario_is_budget(self):
        """Omitting scenario emits scenario='budget' in params."""
        from app.services.fin_compat_sql import position_plan_grain_sql

        _, params = position_plan_grain_sql(2025, 6, "", "PL")
        assert params["scenario"] == "budget"

    def test_forecast_scenario_emitted(self):
        """Passing scenario='forecast' emits 'forecast' in params."""
        from app.services.fin_compat_sql import position_plan_grain_sql

        _, params = position_plan_grain_sql(2025, 6, "", "PL", scenario="forecast")
        assert params["scenario"] == "forecast"

    def test_amount_flip_present_in_budget_sql(self):
        """The single ``amount * -1`` presentation flip is in the SQL for scenario='budget'."""
        from app.services.fin_compat_sql import position_plan_grain_sql

        sql, _ = position_plan_grain_sql(2025, 6, "", "PL", scenario="budget")
        assert "amount * -1" in sql

    def test_amount_flip_present_in_forecast_sql(self):
        """The flip is preserved when scenario='forecast' — same SQL template."""
        from app.services.fin_compat_sql import position_plan_grain_sql

        sql, _ = position_plan_grain_sql(2025, 6, "", "PL", scenario="forecast")
        assert "amount * -1" in sql

    def test_bs_statement_param_emitted(self):
        """BS statement emits statement='BS' in params."""
        from app.services.fin_compat_sql import position_plan_grain_sql

        _, params = position_plan_grain_sql(2025, 6, "", "BS")
        assert params["statement"] == "BS"

    def test_cf_statement_param_emitted(self):
        """CF statement emits statement='CF' in params."""
        from app.services.fin_compat_sql import position_plan_grain_sql

        _, params = position_plan_grain_sql(2025, 6, "", "CF")
        assert params["statement"] == "CF"

    def test_existing_callers_see_unchanged_budget_behaviour(self):
        """Omitting scenario is exactly the same as scenario='budget' — backward compat."""
        from app.services.fin_compat_sql import position_plan_grain_sql

        sql_default, params_default = position_plan_grain_sql(2025, 6, "", "PL")
        sql_explicit, params_explicit = position_plan_grain_sql(
            2025, 6, "", "PL", scenario="budget"
        )
        assert params_default["scenario"] == params_explicit["scenario"] == "budget"
        assert sql_default == sql_explicit  # identical SQL text


# ===========================================================================
# 3) CF SIGN ROUND-TRIP
# ===========================================================================

class TestCfSignRoundTrip:
    """CF present_to_stored / stored_to_present — worked examples + round-trips."""

    def test_cf_inflow_present_to_stored(self):
        """Inflow +900 (presented) → stored -900 (single flip, mirrors PL)."""
        from app.services.budget_service import present_to_stored

        result = present_to_stored(900.0, "CF", "CF_NET")
        assert result == pytest.approx(-900.0)

    def test_cf_outflow_present_to_stored(self):
        """Outflow -300 (presented) → stored +300."""
        from app.services.budget_service import present_to_stored

        result = present_to_stored(-300.0, "CF", "CF_OUT")
        assert result == pytest.approx(300.0)

    def test_cf_stored_to_present_inflow(self):
        """Stored -900 → presented +900 (inverse of present_to_stored)."""
        from app.services.budget_service import stored_to_present

        result = stored_to_present(-900.0, "CF", "CF_NET")
        assert result == pytest.approx(900.0)

    def test_cf_stored_to_present_outflow(self):
        """Stored +300 → presented -300."""
        from app.services.budget_service import stored_to_present

        result = stored_to_present(300.0, "CF", "CF_OUT")
        assert result == pytest.approx(-300.0)

    def test_cf_round_trip_present_then_stored(self):
        """stored_to_present(present_to_stored(x, 'CF', c), 'CF', c) == x for all x."""
        from app.services.budget_service import present_to_stored, stored_to_present

        test_values = [900.0, -300.0, 0.0, 123.456, -1.0, 1e6]
        code = "CF_NET"
        for x in test_values:
            stored = present_to_stored(x, "CF", code)
            back = stored_to_present(stored, "CF", code)
            assert back == pytest.approx(x, abs=1e-9), (
                f"Round-trip failed for x={x}: stored={stored}, back={back}"
            )

    def test_cf_round_trip_stored_then_present(self):
        """Inverse direction: present_to_stored(stored_to_present(s, 'CF', c), 'CF', c) == s."""
        from app.services.budget_service import present_to_stored, stored_to_present

        for s in [-900.0, 300.0, 0.0, -1234.5]:
            code = "CF_INV"
            presented = stored_to_present(s, "CF", code)
            back = present_to_stored(presented, "CF", code)
            assert back == pytest.approx(s, abs=1e-9)

    def test_cf_equals_pl_numerically(self):
        """CF and PL produce identical numerical results (documented decision: same sign)."""
        from app.services.budget_service import present_to_stored, stored_to_present

        for v in [1000.0, -500.0, 0.0, -999.99]:
            assert present_to_stored(v, "CF", "CF_ANY") == pytest.approx(
                present_to_stored(v, "PL", "NET_SALES")
            ), f"present_to_stored CF != PL for v={v}"
            # Inverse direction too
            assert stored_to_present(v, "CF", "CF_ANY") == pytest.approx(
                stored_to_present(v, "PL", "NET_SALES")
            ), f"stored_to_present CF != PL for v={v}"

    def test_explicit_cf_branch_in_present_to_stored(self):
        """An explicit 'CF' branch is in present_to_stored source — no silent fall-through.

        Documents the design decision: CF is equal to PL but is explicitly coded so
        that future divergence requires a deliberate code change, not a silent default.
        """
        import inspect
        from app.services.budget_service import present_to_stored

        source = inspect.getsource(present_to_stored)
        assert 'statement == "CF"' in source or "statement == 'CF'" in source, (
            "present_to_stored must have an explicit CF branch"
        )

    def test_explicit_cf_branch_in_stored_to_present(self):
        """An explicit 'CF' branch is in stored_to_present source."""
        import inspect
        from app.services.budget_service import stored_to_present

        source = inspect.getsource(stored_to_present)
        assert 'statement == "CF"' in source or "statement == 'CF'" in source, (
            "stored_to_present must have an explicit CF branch"
        )


# ===========================================================================
# 4) WORKED EXAMPLES — plan data with the financially signed-off numbers
# ===========================================================================

class TestPlanDataWorkedExamples:
    """Arithmetic: plan_cm / plan_vs_actual / coverage_pct per the spec worked examples.

    Formula (from build_statement_plan_response):
        plan_vs_actual  = round(actual_cm − plan_cm, 2)
        coverage_pct    = round(actual_cm / abs(plan_cm) * 100, 2)  if abs(plan_cm) > 1e-6

    Worked examples:
        PL Net sales      : plan +1000, actual +1200 → vs +200,  cov 120.00
        CF Net cash flow  : plan  +610, actual  +550 → vs  -60,  cov  90.16
        BS AR             : plan  +500, actual  +540 → vs  +40,  cov 108.00
    """

    def _inject_and_call(
        self,
        monkeypatch,
        statement: str,
        plan_map: dict,
        actuals: dict,
        struct_codes: list[str],
    ) -> dict:
        """Inject canned plan_map + actuals into build_statement_plan_response via monkeypatch."""
        from app.services import fin_compat_pl

        _STRUCT: list[dict[str, Any]] = [
            {
                "line_code": code, "row_type": "mapping",
                "sort_order": i * 10, "kpi_code": None,
                "level_2": None, "level_3": code, "level_4": None,
                "balance_title": code, "gl_account_id": None,
            }
            for i, code in enumerate(struct_codes, start=1)
        ]
        monkeypatch.setattr(fin_compat_pl, "_statement_structure_rows",
                            lambda s, st: _STRUCT)
        monkeypatch.setattr(fin_compat_pl, "_load_plan_map",
                            lambda s, y, m, ef: plan_map)
        # load_position_plan_map used for BS/CF (effstmt != "PL")
        monkeypatch.setattr(fin_compat_pl, "load_position_plan_map",
                            lambda s, st, y, m, ef, **kw: plan_map)
        monkeypatch.setattr(fin_compat_pl, "_statement_actual_running",
                            lambda s, st, y, m, ef, struct: actuals)
        monkeypatch.setattr(fin_compat_pl, "resolve_entity_prefix", lambda s, e: None)
        monkeypatch.setattr(fin_compat_pl, "entity_sql_fragment", lambda ep: "")

        return fin_compat_pl.build_statement_plan_response(
            MagicMock(), statement, 2025, 6, None
        )

    def test_pl_net_sales_plan_cm_and_vs_actual(self, monkeypatch):
        """PL Net sales: plan +1000, actual +1200 → plan_vs_actual +200."""
        plan_map = {"NET_SALES": {"plan_cm": 1000.0, "ytd_plan": 1000.0, "ytg": 0.0}}
        actuals = {"NET_SALES": 1200.0}
        out = self._inject_and_call(monkeypatch, "PL", plan_map, actuals, ["NET_SALES"])

        assert out["has_plan_data"] is True
        line = next(l for l in out["lines"] if l["line_code"] == "NET_SALES")
        assert line["plan_cm"] == pytest.approx(1000.0)
        assert line["plan_vs_actual"] == pytest.approx(200.0)

    def test_pl_net_sales_coverage_pct(self, monkeypatch):
        """PL Net sales: coverage_pct = 1200 / |1000| * 100 = 120.00."""
        plan_map = {"NET_SALES": {"plan_cm": 1000.0, "ytd_plan": 1000.0, "ytg": 0.0}}
        actuals = {"NET_SALES": 1200.0}
        out = self._inject_and_call(monkeypatch, "PL", plan_map, actuals, ["NET_SALES"])

        line = next(l for l in out["lines"] if l["line_code"] == "NET_SALES")
        assert line["coverage_pct"] == pytest.approx(120.0)

    def test_cf_net_cash_flow_plan_vs_actual(self, monkeypatch):
        """CF Net cash flow: plan +610, actual +550 → plan_vs_actual -60."""
        plan_map = {"CF_NET_CASH_FLOW": {"plan_cm": 610.0, "ytd_plan": 610.0, "ytg": 0.0}}
        actuals = {"CF_NET_CASH_FLOW": 550.0}
        out = self._inject_and_call(
            monkeypatch, "CF", plan_map, actuals, ["CF_NET_CASH_FLOW"]
        )

        assert out["has_plan_data"] is True
        line = next(l for l in out["lines"] if l["line_code"] == "CF_NET_CASH_FLOW")
        assert line["plan_cm"] == pytest.approx(610.0)
        assert line["plan_vs_actual"] == pytest.approx(-60.0)

    def test_cf_net_cash_flow_coverage_pct(self, monkeypatch):
        """CF Net cash flow: coverage_pct = 550 / |610| * 100 = 90.16 (2 dp)."""
        plan_map = {"CF_NET_CASH_FLOW": {"plan_cm": 610.0, "ytd_plan": 610.0, "ytg": 0.0}}
        actuals = {"CF_NET_CASH_FLOW": 550.0}
        out = self._inject_and_call(
            monkeypatch, "CF", plan_map, actuals, ["CF_NET_CASH_FLOW"]
        )

        line = next(l for l in out["lines"] if l["line_code"] == "CF_NET_CASH_FLOW")
        # 550 / 610 * 100 = 90.1639... → rounds to 90.16
        assert line["coverage_pct"] == pytest.approx(90.16, abs=0.01)

    def test_bs_ar_plan_vs_actual(self, monkeypatch):
        """BS AR: plan +500, actual +540 → plan_vs_actual +40."""
        plan_map = {"AR": {"plan_cm": 500.0, "ytd_plan": 500.0, "ytg": 0.0}}
        actuals = {"AR": 540.0}
        out = self._inject_and_call(monkeypatch, "BS", plan_map, actuals, ["AR"])

        assert out["has_plan_data"] is True
        line = next(l for l in out["lines"] if l["line_code"] == "AR")
        assert line["plan_cm"] == pytest.approx(500.0)
        assert line["plan_vs_actual"] == pytest.approx(40.0)

    def test_bs_ar_coverage_pct(self, monkeypatch):
        """BS AR: coverage_pct = 540 / |500| * 100 = 108.00."""
        plan_map = {"AR": {"plan_cm": 500.0, "ytd_plan": 500.0, "ytg": 0.0}}
        actuals = {"AR": 540.0}
        out = self._inject_and_call(monkeypatch, "BS", plan_map, actuals, ["AR"])

        line = next(l for l in out["lines"] if l["line_code"] == "AR")
        assert line["coverage_pct"] == pytest.approx(108.0)

    def test_coverage_pct_formula_direct(self):
        """Direct arithmetic verification of coverage_pct = actual / |plan| * 100."""
        # PL: 1200 / 1000 * 100 = 120.0
        assert round(1200.0 / abs(1000.0) * 100, 2) == 120.0
        # CF: 550 / 610 * 100 = 90.16 (2 dp)
        assert round(550.0 / abs(610.0) * 100, 2) == 90.16
        # BS AR: 540 / 500 * 100 = 108.0
        assert round(540.0 / abs(500.0) * 100, 2) == 108.0

    def test_plan_vs_actual_formula_direct(self):
        """Direct arithmetic: plan_vs_actual = round(actual − plan_cm, 2)."""
        assert round(1200.0 - 1000.0, 2) == 200.0   # PL
        assert round(550.0 - 610.0, 2) == -60.0      # CF
        assert round(540.0 - 500.0, 2) == 40.0       # BS AR

    def test_zero_plan_cm_gives_null_coverage(self, monkeypatch):
        """plan_cm == 0 → coverage_pct is None (no divide-by-zero)."""
        plan_map = {"NET_SALES": {"plan_cm": 0.0, "ytd_plan": 0.0, "ytg": 0.0}}
        actuals = {"NET_SALES": 500.0}
        out = self._inject_and_call(monkeypatch, "PL", plan_map, actuals, ["NET_SALES"])

        line = next(l for l in out["lines"] if l["line_code"] == "NET_SALES")
        assert line["coverage_pct"] is None  # abs(0) <= 1e-6 → no coverage

    def test_negative_plan_cm_coverage_uses_abs(self, monkeypatch):
        """Negative plan (expense plan): coverage uses abs(plan_cm) in denominator."""
        # Expense line: plan -500 (cost), actual -450 (under budget = better)
        plan_map = {"COGS": {"plan_cm": -500.0, "ytd_plan": -500.0, "ytg": 0.0}}
        actuals = {"COGS": -450.0}
        out = self._inject_and_call(monkeypatch, "PL", plan_map, actuals, ["COGS"])

        line = next(l for l in out["lines"] if l["line_code"] == "COGS")
        # plan_vs_actual = -450 - (-500) = +50 (actual beat plan on expense)
        assert line["plan_vs_actual"] == pytest.approx(50.0)
        # coverage = actual_cm / abs(plan_cm) * 100 = -450 / 500 * 100 = -90.0
        # (sign of actual is preserved; denominator is abs(plan))
        assert line["coverage_pct"] == pytest.approx(-90.0)


# ===========================================================================
# 5) ENTITY-VISIBILITY FAIL-CLOSED
# ===========================================================================

class TestEntityVisibilityFailClosed:
    """allowed_prefixes=set() short-circuits to deny-all before any plan loading."""

    def _call(self, statement: str, allowed_prefixes) -> dict:
        from app.services.fin_compat_pl import build_statement_plan_response
        return build_statement_plan_response(
            MagicMock(), statement, 2025, 7, None,
            allowed_prefixes=allowed_prefixes,
        )

    def test_deny_all_pl(self):
        """PL: allowed_prefixes=set() → has_plan_data=False, lines=[]."""
        out = self._call("PL", set())
        assert out["has_plan_data"] is False
        assert out["lines"] == []
        assert out["year"] == 2025 and out["month"] == 7

    def test_deny_all_bs(self):
        """BS: allowed_prefixes=set() → has_plan_data=False, lines=[]."""
        out = self._call("BS", set())
        assert out["has_plan_data"] is False
        assert out["lines"] == []

    def test_deny_all_wc(self):
        """WC: allowed_prefixes=set() → has_plan_data=False, lines=[]."""
        out = self._call("WC", set())
        assert out["has_plan_data"] is False
        assert out["lines"] == []

    def test_deny_all_cf(self):
        """CF: allowed_prefixes=set() → has_plan_data=False, lines=[]."""
        out = self._call("CF", set())
        assert out["has_plan_data"] is False
        assert out["lines"] == []

    def test_deny_all_never_leaks_lines_for_any_statement(self):
        """allowed_prefixes=set() must return lines=[] for ALL four statements."""
        for stmt in ("PL", "BS", "WC", "CF"):
            out = self._call(stmt, set())
            assert out["lines"] == [], (
                f"Statement {stmt!r} leaked lines on deny-all (allowed_prefixes=set())"
            )

    def test_deny_all_includes_correct_envelope_fields(self):
        """Deny-all response still has the correct year/month/entity envelope."""
        out = self._call("PL", set())
        assert out["year"] == 2025
        assert out["month"] == 7
        assert out["entity"] is None

    def test_admin_none_bypasses_short_circuit_and_loads_plan(self, monkeypatch):
        """allowed_prefixes=None (admin) must attempt plan loading, not short-circuit.

        Proof: with None, the function calls _load_plan_map (we track this).
        With set(), it returns immediately without calling it.
        """
        from app.services import fin_compat_pl

        _STRUCT = [{"line_code": "NET_SALES", "row_type": "mapping", "sort_order": 10,
                    "kpi_code": None, "level_2": None, "level_3": None, "level_4": None,
                    "balance_title": "Net sales", "gl_account_id": None}]
        plan_load_calls: list[bool] = []

        def _fake_load_plan_map(s, y, m, ef):
            plan_load_calls.append(True)
            return {}

        monkeypatch.setattr(fin_compat_pl, "_statement_structure_rows", lambda s, st: _STRUCT)
        monkeypatch.setattr(fin_compat_pl, "_load_plan_map", _fake_load_plan_map)
        monkeypatch.setattr(fin_compat_pl, "_statement_actual_running",
                            lambda s, st, y, m, ef, struct: {})
        monkeypatch.setattr(fin_compat_pl, "resolve_entity_prefix", lambda s, e: None)
        monkeypatch.setattr(fin_compat_pl, "entity_sql_fragment", lambda ep: "")

        out = fin_compat_pl.build_statement_plan_response(
            MagicMock(), "PL", 2025, 7, None, allowed_prefixes=None
        )

        # Admin path MUST have attempted plan loading
        assert plan_load_calls == [True], (
            "allowed_prefixes=None must call _load_plan_map; deny-all short-circuits it"
        )
        # Empty plan → has_plan_data=False (not deny-all, just no data)
        assert out["has_plan_data"] is False

    def test_non_empty_prefixes_not_short_circuited(self, monkeypatch):
        """Non-empty allowed_prefixes passes through to plan loading with restricted scope."""
        from app.services import fin_compat_pl

        _STRUCT = [{"line_code": "NET_SALES", "row_type": "mapping", "sort_order": 10,
                    "kpi_code": None, "level_2": None, "level_3": None, "level_4": None,
                    "balance_title": "Net sales", "gl_account_id": None}]
        ent_frags_seen: list[str] = []
        prefixes_seen: list[Any] = []

        def _fake_load_plan_map(s, y, m, ef, prefixes=None):
            ent_frags_seen.append(ef)
            prefixes_seen.append(prefixes)
            return {}

        monkeypatch.setattr(fin_compat_pl, "_statement_structure_rows", lambda s, st: _STRUCT)
        monkeypatch.setattr(fin_compat_pl, "_load_plan_map", _fake_load_plan_map)
        monkeypatch.setattr(fin_compat_pl, "_statement_actual_running",
                            lambda s, st, y, m, ef, struct: {})
        # Patch entities_sql_fragment so it returns a predictable fragment
        def _fake_ent_frag(prefixes):
            joined = "','".join(sorted(prefixes))
            return f"AND l.entity_prefix IN ('{joined}')"

        monkeypatch.setattr(fin_compat_pl, "entities_sql_fragment", _fake_ent_frag)

        fin_compat_pl.build_statement_plan_response(
            MagicMock(), "PL", 2025, 7, None, allowed_prefixes={"AT", "DE"}
        )

        # Plan loading was attempted (not short-circuited)
        assert ent_frags_seen, "Plan loading must be attempted for non-empty allowed_prefixes"
        # The fragment contains the granted prefixes
        frag = ent_frags_seen[0]
        assert "AT" in frag and "DE" in frag
        # The FULL restricted prefix set is threaded into the plan read (fix #1):
        # the loader receives prefixes=['AT','DE'] so position_plan_grain_sql can
        # BOUND-restrict (= ANY(:eps)) instead of falling back to consolidated.
        assert prefixes_seen[0] == ["AT", "DE"]

    # -----------------------------------------------------------------------
    # Test 4b — entity outside the allowed set → deny-all (non-empty set)
    # -----------------------------------------------------------------------

    def test_entity_outside_allowed_set_is_denied(self, monkeypatch):
        """Non-empty allowed_prefixes + entity that resolves OUTSIDE the set → deny-all.

        Simulates a user granted {'AA','BB'} requesting entity 'CC_entity'.
        _effective_prefixes narrows to ep2='CC', not in the set → denied=True.
        Monkeypatched so the test is DB-free; the early-return path in
        build_statement_plan_response catches ``denied=True`` and returns
        has_plan_data=False, lines=[] without calling any plan loader.
        """
        import app.services.overview_summary as ov_sum
        from app.services.fin_compat_pl import build_statement_plan_response

        monkeypatch.setattr(
            ov_sum, "_effective_prefixes",
            lambda s, *, entity, allowed_prefixes: (set(), None, True),
        )

        out = build_statement_plan_response(
            MagicMock(), "PL", 2025, 7, "CC_entity",
            allowed_prefixes={"AA", "BB"},
        )
        assert out["has_plan_data"] is False
        assert out["lines"] == []
        assert out["entity"] == "CC_entity"
        assert out["year"] == 2025 and out["month"] == 7

    def test_entity_outside_allowed_set_denied_for_bs(self, monkeypatch):
        """Denial holds for BS statement as well (not just PL)."""
        import app.services.overview_summary as ov_sum
        from app.services.fin_compat_pl import build_statement_plan_response

        monkeypatch.setattr(
            ov_sum, "_effective_prefixes",
            lambda s, *, entity, allowed_prefixes: (set(), None, True),
        )

        out = build_statement_plan_response(
            MagicMock(), "BS", 2025, 7, "CC_entity",
            allowed_prefixes={"AA", "BB"},
        )
        assert out["has_plan_data"] is False
        assert out["lines"] == []

    # -----------------------------------------------------------------------
    # Test 4c — non-empty set (≥2) + no entity → full prefix set threaded
    # -----------------------------------------------------------------------

    def test_multi_prefix_no_entity_threads_full_set_into_plan_read(self, monkeypatch):
        """Non-empty set (≥2) + entity=None → restrict_prefixes=['AA','BB'] threaded.

        When _effective_prefixes returns eff={'AA','BB'} and entity=None,
        len(eff) >= 2 so restrict_prefixes = sorted(eff) = ['AA','BB'] and
        load_position_plan_map is called with prefixes=['AA','BB'].  This routes
        into the = ANY(:eps) branch, never touching the '' consolidated row.
        We assert via spy capture: the spy records what prefixes kwarg is passed.
        """
        import app.services.overview_summary as ov_sum
        from app.services import fin_compat_pl

        monkeypatch.setattr(
            ov_sum, "_effective_prefixes",
            lambda s, *, entity, allowed_prefixes: ({"AA", "BB"}, None, False),
        )

        _STRUCT = [{"line_code": "AR", "row_type": "mapping", "sort_order": 1010,
                    "kpi_code": "BS:asset", "level_2": "Current assets",
                    "level_3": "AR", "level_4": None, "balance_title": "AR",
                    "gl_account_id": None}]
        monkeypatch.setattr(fin_compat_pl, "_statement_structure_rows",
                            lambda s, st: _STRUCT)

        prefixes_seen: list = []

        def _spy_load_plan(session, stmt, y, m, ef, *, prefixes=None, **kw):
            prefixes_seen.append(prefixes)
            return {}

        monkeypatch.setattr(fin_compat_pl, "load_position_plan_map", _spy_load_plan)
        monkeypatch.setattr(fin_compat_pl, "_statement_actual_running",
                            lambda s, st, y, m, ef, struct: {})

        fin_compat_pl.build_statement_plan_response(
            MagicMock(), "BS", 2025, 7, None,
            allowed_prefixes={"AA", "BB"},
        )

        assert prefixes_seen, "load_position_plan_map must be called for non-empty scope"
        # sorted({"AA","BB"}) is deterministically ["AA","BB"]
        assert prefixes_seen[0] == ["AA", "BB"], (
            f"Full visible prefix set must be threaded; got {prefixes_seen[0]!r}"
        )

    def test_multi_prefix_no_entity_ent_frag_uses_in_clause(self, monkeypatch):
        """Sanity: the actuals ent_frag for ≥2 prefixes uses IN(...) literal."""
        import app.services.overview_summary as ov_sum
        from app.services import fin_compat_pl

        monkeypatch.setattr(
            ov_sum, "_effective_prefixes",
            lambda s, *, entity, allowed_prefixes: ({"AA", "BB"}, None, False),
        )

        _STRUCT = [{"line_code": "AR", "row_type": "mapping", "sort_order": 1010,
                    "kpi_code": "BS:asset", "level_2": "Current assets",
                    "level_3": "AR", "level_4": None, "balance_title": "AR",
                    "gl_account_id": None}]
        monkeypatch.setattr(fin_compat_pl, "_statement_structure_rows",
                            lambda s, st: _STRUCT)

        ent_frags_seen: list = []

        def _spy_load_plan(session, stmt, y, m, ef, *, prefixes=None, **kw):
            ent_frags_seen.append(ef)
            return {}

        monkeypatch.setattr(fin_compat_pl, "load_position_plan_map", _spy_load_plan)
        monkeypatch.setattr(fin_compat_pl, "_statement_actual_running",
                            lambda s, st, y, m, ef, struct: {})

        fin_compat_pl.build_statement_plan_response(
            MagicMock(), "BS", 2025, 7, None,
            allowed_prefixes={"AA", "BB"},
        )

        assert ent_frags_seen, "load_position_plan_map must be called"
        frag = ent_frags_seen[0]
        assert "AA" in frag and "BB" in frag, (
            f"ent_frag must contain both AA and BB; got {frag!r}"
        )
        # entities_sql_fragment for ≥2 prefixes produces IN(...) not = 'XX'
        assert "IN" in frag.upper(), (
            f"ent_frag for ≥2 prefixes must use IN(...); got {frag!r}"
        )

    # -----------------------------------------------------------------------
    # Test 4d — entity inside set → single prefix path, prefixes=None
    # -----------------------------------------------------------------------

    def test_entity_inside_set_narrows_to_single_prefix_no_any_binding(self, monkeypatch):
        """Non-empty set + entity inside → single prefix, prefixes=None (= 'XX' path).

        When _effective_prefixes returns eff={'AA'} (entity narrowed to one
        prefix inside the visible set), len(eff)==1 so:
          * ent_frag = entity_sql_fragment('AA') = "AND l.entity_prefix = 'AA'"
          * restrict_prefixes stays None
          * load_position_plan_map called WITHOUT prefixes kwarg (prefixes=None)
          * → uses the existing per-entity = :ep path (unchanged behaviour).

        We assert both the absence of the prefixes kwarg (None) and that the
        ent_frag carries the single-entity literal, as proof the fix does NOT
        touch the single-entity path.
        """
        import app.services.overview_summary as ov_sum
        from app.services import fin_compat_pl

        monkeypatch.setattr(
            ov_sum, "_effective_prefixes",
            lambda s, *, entity, allowed_prefixes: ({"AA"}, None, False),
        )

        _STRUCT = [{"line_code": "AR", "row_type": "mapping", "sort_order": 1010,
                    "kpi_code": "BS:asset", "level_2": "Current assets",
                    "level_3": "AR", "level_4": None, "balance_title": "AR",
                    "gl_account_id": None}]
        monkeypatch.setattr(fin_compat_pl, "_statement_structure_rows",
                            lambda s, st: _STRUCT)

        call_args: list[dict] = []

        def _spy_load_plan(session, stmt, y, m, ef, *, prefixes=None, **kw):
            call_args.append({"ent_frag": ef, "prefixes": prefixes})
            return {}

        monkeypatch.setattr(fin_compat_pl, "load_position_plan_map", _spy_load_plan)
        monkeypatch.setattr(fin_compat_pl, "_statement_actual_running",
                            lambda s, st, y, m, ef, struct: {})

        fin_compat_pl.build_statement_plan_response(
            MagicMock(), "BS", 2025, 7, "AA_entity",
            allowed_prefixes={"AA", "BB"},
        )

        assert call_args, "load_position_plan_map must be called"
        # Single visible prefix → restrict_prefixes=None → no ANY(:eps) binding
        assert call_args[0]["prefixes"] is None, (
            f"Single-prefix path must pass prefixes=None; "
            f"got prefixes={call_args[0]['prefixes']!r}"
        )
        # ent_frag uses the = 'AA' literal (entity_sql_fragment("AA"))
        assert "= 'AA'" in call_args[0]["ent_frag"], (
            f"ent_frag must be the single-entity literal '= \\'AA\\''; "
            f"got {call_args[0]['ent_frag']!r}"
        )


# ===========================================================================
# REGRESSION: Cross-tenant plan leak — position_plan_grain_sql prefixes fix
# ===========================================================================

class TestCrossTenantPlanLeak:
    """Regression tests for the cross-tenant plan-leak fix.

    Tests 1–3 from the L2 security review brief:
      1. SQL shape — ``= ANY(:eps)`` present, ``NOT EXISTS`` absent on the
         prefixes branch, ``amount * -1`` flip preserved, and byte-identity
         guarantee when ``prefixes=None``.
      2. Sanitization — 2-char truncation + quote-strip applied before binding,
         matching the ``entities_sql_fragment`` convention.
      3. Map seam — forwarding assertion: ``load_position_plan_map`` threads the
         prefix list unchanged into ``position_plan_grain_sql`` (SQL/param
         capture approach, because the _FakeSession cannot emulate the = ANY
         row-filtering that a real Postgres engine would apply).

    Approach for test 3 documented here: the _FakeSession returns whatever is
    queued regardless of the SQL predicate, so row-level filtering (AA+BB vs CC)
    cannot be emulated without a real DB.  We therefore assert at two levels:
      (a) SQL/param: the SQL emitted to the DB contains '= ANY(:eps)' with
          eps=['AA','BB'] and no NOT EXISTS clause — proof that the DB engine
          WOULD exclude CC and the '' override row if it ran the query.
      (b) Forwarding: load_position_plan_map passes the same prefixes list
          unchanged into position_plan_grain_sql — proof there is no seam where
          the list is widened or dropped before reaching the SQL builder.
    """

    # -----------------------------------------------------------------------
    # Test 1 — SQL shape / no-leak
    # -----------------------------------------------------------------------

    def test_sql_contains_any_eps_clause(self):
        """prefixes=['AA','BB'] → SQL emits 'AND p.entity_prefix = ANY(:eps)'."""
        from app.services.fin_compat_sql import position_plan_grain_sql

        sql, params = position_plan_grain_sql(2025, 6, "", "BS", prefixes=["AA", "BB"])
        assert "= ANY(:eps)" in sql, (
            f"Expected '= ANY(:eps)' in SQL; sql snippet: {sql[:300]!r}"
        )
        assert params.get("eps") == ["AA", "BB"]

    def test_sql_no_not_exists_on_prefixes_branch(self):
        """The prefixes branch must NOT contain NOT EXISTS.

        The NOT EXISTS sub-select guards the consolidated '' row override.
        When prefixes is set we sum only the explicitly listed per-entity rows
        and NEVER consult the '' consolidated override — so NOT EXISTS must be
        absent.  Its presence would allow a user granted {AA,BB} to accidentally
        read the '' row (which covers all entities, including CC).
        """
        from app.services.fin_compat_sql import position_plan_grain_sql

        sql, _ = position_plan_grain_sql(2025, 6, "", "BS", prefixes=["AA", "BB"])
        assert "NOT EXISTS" not in sql, (
            "prefixes branch must NOT contain NOT EXISTS — "
            "the consolidated '' override row must never be consulted"
        )

    def test_amount_flip_still_present_on_prefixes_branch(self):
        """The single 'amount * -1' presentation flip is preserved on all branches."""
        from app.services.fin_compat_sql import position_plan_grain_sql

        sql, _ = position_plan_grain_sql(2025, 6, "", "BS", prefixes=["AA", "BB"])
        assert "amount * -1" in sql, (
            "presentation flip 'amount * -1' must be present regardless of prefixes"
        )

    def test_byte_identity_prefixes_none_vs_omitted(self):
        """prefixes=None is byte-identical to omitting the kwarg (existing callers safe).

        Every existing caller of position_plan_grain_sql omits the prefixes kwarg.
        Passing prefixes=None explicitly must yield the same SQL AND params,
        proving no default argument or branch divergence was introduced.
        """
        from app.services.fin_compat_sql import position_plan_grain_sql

        sql_none, params_none = position_plan_grain_sql(2025, 6, "", "PL", prefixes=None)
        sql_default, params_default = position_plan_grain_sql(2025, 6, "", "PL")

        assert sql_none == sql_default, (
            "prefixes=None must produce SQL byte-identical to the default (no kwarg)"
        )
        assert params_none == params_default, (
            "prefixes=None must produce params byte-identical to the default (no kwarg)"
        )

    def test_prefixes_branch_sql_differs_from_none_path(self):
        """Sanity: prefixes=['AA','BB'] SQL/params are DIFFERENT from the None path."""
        from app.services.fin_compat_sql import position_plan_grain_sql

        sql_p, params_p = position_plan_grain_sql(
            2025, 6, "", "PL", prefixes=["AA", "BB"]
        )
        sql_none, params_none = position_plan_grain_sql(2025, 6, "", "PL")

        # SQL must differ (the eps clause replaces the consolidated/per-entity clause)
        assert sql_p != sql_none, "prefixes branch SQL must differ from the None path"
        # eps key only present on the prefixes path
        assert "eps" in params_p, "params must have 'eps' key on the prefixes branch"
        assert "eps" not in params_none, "params must NOT have 'eps' key on the None path"

    # -----------------------------------------------------------------------
    # Test 2 — Sanitization
    # -----------------------------------------------------------------------

    def test_sanitization_strips_quotes_and_truncates_to_two_chars(self):
        """Prefixes are quote-stripped and truncated to 2 chars before binding.

        Production rule in position_plan_grain_sql:
          ``params['eps'] = [str(p).replace("'", "")[:2] for p in prefixes if p]``

        Input           → sanitized
        'A\\'A'         → 'AA'   (quote removed, len already 2)
        'BBB'           → 'BB'   (truncated from 3 to 2)
        'C'             → 'C'    (short prefix kept as-is)

        The bound params must carry the sanitized list, never the raw input.
        """
        from app.services.fin_compat_sql import position_plan_grain_sql

        _, params = position_plan_grain_sql(
            2025, 6, "", "PL", prefixes=["A'A", "BBB", "C"]
        )
        assert params.get("eps") == ["AA", "BB", "C"], (
            f"Sanitized eps should be ['AA','BB','C']; got {params.get('eps')!r}"
        )

    def test_sanitization_filters_out_empty_strings(self):
        """Empty / falsy entries in the prefixes list are dropped by the 'if p' guard."""
        from app.services.fin_compat_sql import position_plan_grain_sql

        _, params = position_plan_grain_sql(
            2025, 6, "", "PL", prefixes=["AA", "", "BB"]
        )
        assert "" not in params.get("eps", []), (
            "Empty string must be dropped from eps by the 'if p' guard"
        )
        assert params.get("eps") == ["AA", "BB"]

    # -----------------------------------------------------------------------
    # Test 3 — Map seam: forwarding assertion (SQL/param + spy capture)
    # -----------------------------------------------------------------------

    def test_load_position_plan_map_forwards_prefixes_to_grain_sql(self):
        """load_position_plan_map threads prefixes into position_plan_grain_sql unchanged.

        Approach: SQL/param capture via _FakeSession (see class docstring).
        We cannot emulate = ANY row-filtering at the FakeSession level, so we
        assert on the SQL and params captured at the execute() call:

          (a) SQL contains '= ANY(:eps)' — the correct branch was selected.
          (b) eps == ['AA','BB'] — no CC, no '' override row accessible.
          (c) NOT EXISTS absent — consolidated fallback never reached.

        The empty plan-grain result (second FakeSession slot is []) means the
        function returns {} after the execute; we still inspect calls[1].
        """
        from app.services.fin_compat_pl import load_position_plan_map

        struct = [_pl_struct_row("NET_SALES")]
        # Empty plan grains → function returns {} after execute (calls[1] captured)
        sess = _FakeSession([struct, []])

        load_position_plan_map(sess, "PL", 2025, 6, "", prefixes=["AA", "BB"])

        # Call 0 = dim_pl_structure query; Call 1 = fact_position_plan grain
        assert len(sess.calls) >= 2, "Expected at least 2 DB round-trips"
        plan_sql, plan_params = sess.calls[1]

        assert "fact_position_plan" in plan_sql, (
            "Second DB call must query fact_position_plan"
        )
        assert "= ANY(:eps)" in plan_sql, (
            "prefixes=['AA','BB'] must emit '= ANY(:eps)' in the plan grain SQL"
        )
        assert plan_params.get("eps") == ["AA", "BB"], (
            f"eps must be forwarded unchanged; got {plan_params.get('eps')!r}"
        )
        assert "NOT EXISTS" not in plan_sql, (
            "prefixes branch must not include NOT EXISTS — '' override must not be consulted"
        )

    def test_load_position_plan_map_without_prefixes_uses_consolidated_path(self):
        """Without prefixes the consolidated/NOT EXISTS path is unchanged.

        Verifies the existing call site (prefixes omitted → None) is byte-identical
        to before: the SQL uses the NOT EXISTS consolidated-override logic, and the
        params dict does NOT contain an eps key.
        """
        from app.services.fin_compat_pl import load_position_plan_map

        struct = [_pl_struct_row("NET_SALES")]
        sess = _FakeSession([struct, []])

        load_position_plan_map(sess, "PL", 2025, 6, "")  # no prefixes kwarg

        plan_sql, plan_params = sess.calls[1]

        assert "fact_position_plan" in plan_sql
        assert "NOT EXISTS" in plan_sql, (
            "Without prefixes the consolidated path must include NOT EXISTS"
        )
        assert "eps" not in plan_params, (
            "Consolidated path must NOT have an eps param in the bound dict"
        )
