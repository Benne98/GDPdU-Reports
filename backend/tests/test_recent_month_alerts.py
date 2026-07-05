"""Tests for the Overview v2 recent-months 3-trigger alert (Area 1).

Signed-off math: docs/financial-logic.md → "Overview Page v2 — KPI sign-offs" →
Area 1 ("Recent-months 3-trigger alert").  Implemented by
``app.services.overview_alerts.build_recent_month_alerts`` (+ the pure
``evaluate_month_alert``).

=== WORKED EXAMPLE (severity-3, from the doc, to the cent) ===
  x = 60, x_py = 95, plan = 90, μ = 100, σ̂ = 10
    T1 = |60-95|/|95|   = 0.368 ≥ 0.20  ✓
    T2 = |60-90|/|90|   = 0.333 ≥ 0.10  ✓
    T3 = |(60-100)/10|  = 4.0   ≥ 2.0   ✓
    → severity 3 ; ref = x_py = 95 ; direction "down".

=== TRIGGER-SKIP EDGE CASES ===
  x_py = 0 → T1 skipped (denominator 0).  plan = 0 / None → T2 skipped.
  σ̂ = 0 (flat trailing) → T3 skipped.  severity always == #triggers fired.

=== FAIL-CLOSED (tenant isolation) ===
  allowed_entities = set()  → []  (deny-all, no query issued).
  allowed_entities = {'10'} → only entity '10' rows survive the entity_prefix
  IN-filter (mock Session honours the bound :ep* params).
"""
from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import MagicMock

from app.services.overview_alerts import (
    build_recent_month_alerts,
    evaluate_month_alert,
)


# ---------------------------------------------------------------------------
# Row helper (SQLAlchemy Row-like with _mapping) — mirrors test_overview_metrics
# ---------------------------------------------------------------------------
class _DictRow:
    def __init__(self, d: dict):
        self._mapping = d
        for k, v in d.items():
            setattr(self, k, v)

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self._mapping.values())[key]
        return self._mapping[key]

    def get(self, key, default=None):
        return self._mapping.get(key, default)


# A trailing-12 series with sample mean 100 and sample std ~= 10.44 (ddof=1);
# |z| for x=60 is ~= 3.8 (≥ 2), so T3 still fires — the doc's exact σ̂ = 10 is
# illustrative, the trigger outcome is what is locked.
_TRAIL12_MEAN100 = [90.0, 110.0] * 6  # 12 points, mean 100


# ===========================================================================
# (1) Pure evaluator — the signed-off worked example + trigger skips
# ===========================================================================
class TestEvaluateMonthAlert:

    def test_worked_example_severity_3_down(self):
        """x=60, x_py=95, plan=90, μ=100, σ̂=10 → severity 3, ref=95, 'down'."""
        res = evaluate_month_alert(60.0, x_py=95.0, plan=90.0, mean=100.0, std=10.0)
        assert res is not None
        assert res["severity"] == 3
        assert res["triggers"] == ["T1", "T2", "T3"]
        assert res["direction"] == "down"
        assert res["reference"] == 95.0          # T1 has priority
        assert res["reference_kind"] == "prior_year"
        assert res["z"] == -4.0

    def test_no_trigger_returns_none(self):
        # x sits on μ, equals py and plan → nothing fires → None.
        assert evaluate_month_alert(100.0, x_py=100.0, plan=100.0,
                                    mean=100.0, std=10.0) is None

    def test_t1_skipped_when_py_zero(self):
        # x_py = 0 → T1 denominator 0 → skipped; only T2 + T3 can fire.
        res = evaluate_month_alert(60.0, x_py=0.0, plan=90.0, mean=100.0, std=10.0)
        assert res is not None
        assert "T1" not in res["triggers"]
        assert res["triggers"] == ["T2", "T3"]
        assert res["severity"] == 2
        # ref falls to the highest remaining priority (T2 plan = 90).
        assert res["reference"] == 90.0
        assert res["reference_kind"] == "plan"
        assert res["direction"] == "down"

    def test_t2_skipped_when_plan_zero_or_missing(self):
        for plan in (0.0, None):
            res = evaluate_month_alert(60.0, x_py=95.0, plan=plan,
                                       mean=100.0, std=10.0)
            assert res is not None
            assert "T2" not in res["triggers"]
            assert res["triggers"] == ["T1", "T3"]
            assert res["severity"] == 2

    def test_t3_skipped_when_std_zero(self):
        # σ̂ = 0 (flat trailing) → T3 skipped, no divide-by-zero.
        res = evaluate_month_alert(60.0, x_py=95.0, plan=90.0, mean=100.0, std=0.0)
        assert res is not None
        assert "T3" not in res["triggers"]
        assert res["triggers"] == ["T1", "T2"]
        assert res["severity"] == 2
        assert res["z"] == 0.0

    def test_severity_equals_number_of_triggers(self):
        # Only T3 fires (py & plan close to x, big z).
        res = evaluate_month_alert(60.0, x_py=61.0, plan=61.0, mean=100.0, std=10.0)
        assert res is not None
        assert res["severity"] == 1
        assert res["triggers"] == ["T3"]

    def test_direction_up_when_x_above_ref(self):
        # x well above py → direction 'up'; sign(x − ref) positive.
        res = evaluate_month_alert(140.0, x_py=100.0, plan=None, mean=100.0, std=0.0)
        assert res is not None
        assert res["direction"] == "up"
        assert res["reference"] == 100.0

    def test_boundary_inclusive_at_threshold(self):
        # |z| == 2.0 exactly fires (>=); T1/T2 off (x == py == plan).
        res = evaluate_month_alert(120.0, x_py=120.0, plan=120.0, mean=100.0, std=10.0)
        assert res is not None
        assert res["triggers"] == ["T3"]  # z == 2.0 → fires


# ===========================================================================
# Mock-Session builder for build_recent_month_alerts
# ===========================================================================
def _make_session(
    rev_rows: list[dict], com_rows: list[dict],
    plan_rev_rows: list[dict] | None = None,
    plan_com_rows: list[dict] | None = None,
):
    """Mock Session dispatching on SQL substrings + honouring :ep* entity filter.

    Each canned row is {ep, yr, mo, val}.  When the executed SQL binds any :ep*
    params (the entity_prefix IN-filter), rows whose ``ep`` is not among those
    bound values are dropped — this is what proves the fail-closed filter is
    actually injected into every query (not merely accepted as an argument).
    """
    plan_rev_rows = plan_rev_rows or []
    plan_com_rows = plan_com_rows or []

    def _filter(rows: list[dict], params: dict[str, Any]) -> list[_DictRow]:
        ep_vals = {v for k, v in (params or {}).items() if k.startswith("ep")}
        out = []
        for r in rows:
            if ep_vals and r["ep"] not in ep_vals:
                continue
            out.append(_DictRow(r))
        return out

    def _execute(stmt, params=None):
        sql = str(stmt)
        result = MagicMock()
        if "fact_sales_plan" in sql:
            rows = _filter(plan_rev_rows, params)
        elif "fact_com_plan" in sql:
            rows = _filter(plan_com_rows, params)
        elif "fact_sales" in sql:
            rows = _filter(rev_rows, params)
        elif "fact_com" in sql:
            rows = _filter(com_rows, params)
        else:  # resolve_entity_prefix or anything else → empty
            rows = []
        result.fetchall.return_value = rows
        result.fetchone.return_value = rows[0] if rows else None
        return result

    session = MagicMock()
    session.execute.side_effect = _execute
    return session


def _monthly_rows(ep: str, anchor_y: int, anchor_m: int, val_by_offset: dict[int, float]):
    """Build {ep,yr,mo,val} rows: offset 0 = anchor month, k = k months before."""
    from app.services.overview_alerts import _step_back
    rows = []
    for off, val in val_by_offset.items():
        y, m = _step_back(anchor_y, anchor_m, off)
        rows.append({"ep": ep, "yr": y, "mo": m, "val": val})
    return rows


# ===========================================================================
# (2) build_recent_month_alerts — DB glue, severity, cap, fail-closed
# ===========================================================================
class TestBuildRecentMonthAlerts:

    ANCHOR_Y, ANCHOR_M = 2026, 3

    def _revenue_scenario(self, ep: str) -> list[dict]:
        # Trailing (offsets 1..12) = mean 100; anchor (offset 0) revenue = 60;
        # prior-year (offset 12 is inside trailing; py for anchor = offset 12 = 90).
        # Give the anchor a clear anomaly and a real py value.
        offsets = {0: 60.0}
        # months 1..11 before anchor alternate 90/110 → mean ~100
        for k in range(1, 12):
            offsets[k] = 90.0 if k % 2 else 110.0
        offsets[12] = 95.0   # prior-year month value for the anchor
        return _monthly_rows(ep, self.ANCHOR_Y, self.ANCHOR_M, offsets)

    def test_revenue_alert_fires_with_severity(self):
        rev = self._revenue_scenario("10")
        # COM zero everywhere → gross margin defined (100%) but flat → margin
        # alerts may or may not fire; we assert on the revenue alert.
        plan_rev = _monthly_rows("10", self.ANCHOR_Y, self.ANCHOR_M, {0: 90.0})
        session = _make_session(rev, [], plan_rev_rows=plan_rev)
        out = build_recent_month_alerts(
            session, entity=None, year=self.ANCHOR_Y, month=self.ANCHOR_M,
            allowed_entities={"10"},
        )
        rev_alerts = [a for a in out if a["metric"] == "revenue" and a["month"] == self.ANCHOR_M]
        assert rev_alerts, "expected a revenue alert at the anchor month"
        a = rev_alerts[0]
        # T1 (60 vs 95), T2 (60 vs 90), T3 (60 vs ~100) all fire → severity 3, down.
        assert a["severity"] == 3
        assert a["direction"] == "down"
        assert a["entity"] == "10"
        assert a["value"] == 60.0
        # severity == number of triggers listed
        assert a["severity"] == len(a["triggers"])

    def test_result_capped_at_max_items(self):
        rev = self._revenue_scenario("10")
        plan_rev = _monthly_rows("10", self.ANCHOR_Y, self.ANCHOR_M, {0: 90.0})
        session = _make_session(rev, [], plan_rev_rows=plan_rev)
        out = build_recent_month_alerts(
            session, entity=None, year=self.ANCHOR_Y, month=self.ANCHOR_M,
            allowed_entities={"10"}, max_items=1,
        )
        assert len(out) <= 1

    def test_sorted_highest_severity_first(self):
        rev = self._revenue_scenario("10")
        plan_rev = _monthly_rows("10", self.ANCHOR_Y, self.ANCHOR_M, {0: 90.0})
        session = _make_session(rev, [], plan_rev_rows=plan_rev)
        out = build_recent_month_alerts(
            session, entity=None, year=self.ANCHOR_Y, month=self.ANCHOR_M,
            allowed_entities={"10"}, max_items=10,
        )
        sevs = [a["severity"] for a in out]
        assert sevs == sorted(sevs, reverse=True)

    # --- FAIL-CLOSED ------------------------------------------------------
    def test_fail_closed_empty_set_returns_empty_and_no_query(self):
        session = _make_session(self._revenue_scenario("10"), [])
        out = build_recent_month_alerts(
            session, entity=None, year=self.ANCHOR_Y, month=self.ANCHOR_M,
            allowed_entities=set(),
        )
        assert out == []
        session.execute.assert_not_called()  # short-circuits before any SQL

    def test_allowed_entities_restricts_rows(self):
        # Two entities in the canned data; only '10' is visible.
        rev = self._revenue_scenario("10") + self._revenue_scenario("20")
        plan_rev = (
            _monthly_rows("10", self.ANCHOR_Y, self.ANCHOR_M, {0: 90.0})
            + _monthly_rows("20", self.ANCHOR_Y, self.ANCHOR_M, {0: 90.0})
        )
        session = _make_session(rev, [], plan_rev_rows=plan_rev)
        out = build_recent_month_alerts(
            session, entity=None, year=self.ANCHOR_Y, month=self.ANCHOR_M,
            allowed_entities={"10"}, max_items=50,
        )
        assert out, "expected alerts for the visible entity"
        assert {a["entity"] for a in out} == {"10"}  # '20' filtered out

    def test_admin_unrestricted_none_still_returns(self):
        rev = self._revenue_scenario("10")
        plan_rev = _monthly_rows("10", self.ANCHOR_Y, self.ANCHOR_M, {0: 90.0})
        session = _make_session(rev, [], plan_rev_rows=plan_rev)
        out = build_recent_month_alerts(
            session, entity=None, year=self.ANCHOR_Y, month=self.ANCHOR_M,
            allowed_entities=None, max_items=50,
        )
        assert any(a["entity"] == "10" for a in out)
