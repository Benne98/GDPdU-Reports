"""Phase 2 (Finssentials budget heuristics) — prior_year / trend_cagr / run_rate.

Financial-metric gated (CLAUDE.md rule #1): each heuristic's formula + worked
example + edge cases live in ``budget_heuristics`` docstrings AND in
``docs/financial-logic.md``; this file is the locking regression.

All tests are PURE (operate on an in-memory actuals map ``{fy: {period: value}}``)
plus one monkeypatched-DB dispatcher test — no live calls.
"""
from __future__ import annotations

import math

import pytest

from app.services import budget_heuristics as bh
from app.services import budget_service
from app.services.budget_service import (
    PERIODS,
    UNIFORM_WEIGHT,
    _all_zero_months,
    seasonalize,
)


def _flat_fy(annual: float) -> dict[int, float]:
    """A FY month map spreading ``annual`` uniformly over all 12 periods."""
    return {p: annual / 12.0 for p in PERIODS}


def _seasoned_fy(months: list[float]) -> dict[int, float]:
    assert len(months) == 12
    return {p: float(months[p - 1]) for p in PERIODS}


# =========================================================================== #
# (1) prior_year
# =========================================================================== #
def test_prior_year_base_times_one_plus_growth():
    """suggestion = base*(1+g); weights sum to the suggestion when seasonalized."""
    actuals = {2024: _flat_fy(1000.0)}
    res = bh.prior_year(actuals, growth_pct=0.10)
    assert res["suggestion_annual"] == pytest.approx(1100.0)
    assert sum(res["weights"].values()) == pytest.approx(1.0, abs=1e-9)
    months = seasonalize(res["suggestion_annual"], res["weights"])
    assert sum(months.values()) == pytest.approx(1100.0, abs=1e-6)
    exp = res["explanation"]
    assert exp["method"] == "prior_year"
    assert exp["base_fy"] == 2024
    assert exp["base_annual"] == pytest.approx(1000.0)
    assert exp["growth_pct"] == pytest.approx(0.10)
    assert exp["season_source"] == 2024


def test_prior_year_default_growth_is_zero():
    res = bh.prior_year({2024: _flat_fy(1000.0)})
    assert res["suggestion_annual"] == pytest.approx(1000.0)
    assert res["explanation"]["growth_pct"] == pytest.approx(0.0)


def test_prior_year_uses_last_full_fy():
    actuals = {2022: _flat_fy(500.0), 2023: _flat_fy(800.0), 2024: _flat_fy(1000.0)}
    res = bh.prior_year(actuals)
    assert res["explanation"]["base_fy"] == 2024
    assert res["suggestion_annual"] == pytest.approx(1000.0)


def test_prior_year_seasonal_weights_from_base_fy():
    # Jan carries double weight (2/13 of the annual).
    base = _seasoned_fy([200.0] + [100.0] * 11)  # annual 1300
    res = bh.prior_year({2024: base}, growth_pct=0.0)
    assert res["weights"][1] == pytest.approx(200.0 / 1300.0)
    assert res["weights"][2] == pytest.approx(100.0 / 1300.0)


def test_prior_year_negative_base_preserves_sign():
    res = bh.prior_year({2024: _flat_fy(-1000.0)}, growth_pct=0.10)
    assert res["suggestion_annual"] == pytest.approx(-1100.0)


def test_prior_year_no_history_zero_plus_note():
    res = bh.prior_year({})
    assert res["suggestion_annual"] == 0.0
    assert "note" in res["explanation"]
    assert all(w == pytest.approx(UNIFORM_WEIGHT) for w in res["weights"].values())


def test_prior_year_zero_base_yields_zero():
    res = bh.prior_year({2024: _flat_fy(0.0)}, growth_pct=0.25)
    assert res["suggestion_annual"] == pytest.approx(0.0)


# =========================================================================== #
# (2) trend_cagr
# =========================================================================== #
def test_trend_cagr_worked_example_100_to_121():
    """100 → 121 over 3 FYs → cagr = 10% → suggestion = 121*1.1 = 133.1."""
    actuals = {2022: _flat_fy(100.0), 2023: _flat_fy(110.0), 2024: _flat_fy(121.0)}
    res = bh.trend_cagr(actuals, years=3)
    assert res["explanation"]["cagr"] == pytest.approx(0.10, abs=1e-9)
    assert res["suggestion_annual"] == pytest.approx(133.1, abs=1e-6)
    exp = res["explanation"]
    assert exp["method"] == "trend_cagr"
    assert exp["window"] == [2022, 2024]
    assert exp["window_years"] == 3
    assert exp["first_annual"] == pytest.approx(100.0)
    assert exp["last_annual"] == pytest.approx(121.0)
    assert exp["base_fy"] == 2024
    assert exp["season_source"] == 2024


def test_trend_cagr_two_years():
    actuals = {2023: _flat_fy(100.0), 2024: _flat_fy(120.0)}
    res = bh.trend_cagr(actuals, years=3)
    # n=2 → cagr = (120/100)^(1/1) - 1 = 0.20 → suggestion = 120*1.2 = 144.
    assert res["explanation"]["cagr"] == pytest.approx(0.20, abs=1e-9)
    assert res["suggestion_annual"] == pytest.approx(144.0, abs=1e-6)


def test_trend_cagr_window_caps_to_years():
    actuals = {
        2021: _flat_fy(50.0), 2022: _flat_fy(100.0),
        2023: _flat_fy(110.0), 2024: _flat_fy(121.0),
    }
    res = bh.trend_cagr(actuals, years=3)  # uses 2022..2024 only
    assert res["explanation"]["window"] == [2022, 2024]
    assert res["explanation"]["cagr"] == pytest.approx(0.10, abs=1e-9)


def test_trend_cagr_fewer_than_two_fys_falls_back():
    res = bh.trend_cagr({2024: _flat_fy(1000.0)})
    assert res["suggestion_annual"] == pytest.approx(1000.0)
    assert res["explanation"]["method"] == "trend_cagr"
    assert "note" in res["explanation"]


def test_trend_cagr_first_year_zero_cagr_zero():
    res = bh.trend_cagr({2023: _flat_fy(0.0), 2024: _flat_fy(100.0)})
    assert res["explanation"]["cagr"] == pytest.approx(0.0)
    assert res["suggestion_annual"] == pytest.approx(100.0)
    assert "note" in res["explanation"]


def test_trend_cagr_sign_change_cagr_zero():
    res = bh.trend_cagr({2023: _flat_fy(-100.0), 2024: _flat_fy(100.0)})
    assert res["explanation"]["cagr"] == pytest.approx(0.0)
    assert res["suggestion_annual"] == pytest.approx(100.0)
    assert "note" in res["explanation"]


def test_trend_cagr_both_negative_real_rate():
    # -100 → -121 over 3y: ratio 1.21 → cagr 10% → suggestion -121*1.1 = -133.1.
    actuals = {2022: _flat_fy(-100.0), 2023: _flat_fy(-110.0), 2024: _flat_fy(-121.0)}
    res = bh.trend_cagr(actuals, years=3)
    assert res["explanation"]["cagr"] == pytest.approx(0.10, abs=1e-9)
    assert res["suggestion_annual"] == pytest.approx(-133.1, abs=1e-6)


# =========================================================================== #
# (1b) / (2b) COMPLETE-FY rule — no partial (YTD) year as a base or CAGR member
#   Bug evidence: a FY2026 plan returned base_fy=2025 with base_annual=-63,433,637,
#   but FY2025 was INCOMPLETE (only Jan..Jul) → that "annual" was a partial figure.
#   A fiscal year is COMPLETE iff it is strictly BEFORE current_fy (the latest
#   ledger anchor year); prior_year/trend_cagr must drop any fy >= current_fy.
# =========================================================================== #
def _partial_fy(jan_to_jul_total: float) -> dict[int, float]:
    """A PARTIAL FY: only periods 1..7 (Jan..Jul) booked, 8..12 unbooked (0)."""
    per = jan_to_jul_total / 7.0
    return {p: (per if p <= 7 else 0.0) for p in PERIODS}


def test_prior_year_drops_partial_current_fy():
    """prior_year must base on the last COMPLETE FY, NOT the partial current year.

    FY2026 plan, anchor 2025/Jul → current_fy=2025.  Actuals carry complete FY2024
    plus a partial FY2025 (only Jan..Jul, summing to a misleading -63,433,637).  The
    base must be FY2024 (complete), never FY2025 (partial)."""
    actuals = {
        2024: _flat_fy(1000.0),
        2025: _partial_fy(-63_433_637.0),  # partial — must be excluded
    }
    res = bh.prior_year(actuals, growth_pct=0.0, current_fy=2025)
    assert res["explanation"]["base_fy"] == 2024
    assert res["suggestion_annual"] == pytest.approx(1000.0)
    assert res["explanation"]["base_annual"] == pytest.approx(1000.0)


def test_trend_cagr_window_excludes_partial_current_fy():
    """The CAGR window uses ONLY complete FYs; the partial current year is excluded.

    FY2026 plan, current_fy=2025.  Complete FY2023=100, FY2024=121 + partial FY2025.
    The window is [2023, 2024] (n=2) → cagr = (121/100)^(1/1)-1 = 0.21 → suggestion =
    121*1.21 = 146.41; base_fy/last_annual are FY2024, NOT the partial FY2025."""
    actuals = {
        2023: _flat_fy(100.0),
        2024: _flat_fy(121.0),
        2025: _partial_fy(-63_433_637.0),  # partial — never first/last_annual
    }
    res = bh.trend_cagr(actuals, years=3, current_fy=2025)
    exp = res["explanation"]
    assert exp["window"] == [2023, 2024]
    assert exp["base_fy"] == 2024
    assert exp["last_annual"] == pytest.approx(121.0)
    assert exp["first_annual"] == pytest.approx(100.0)
    assert exp["cagr"] == pytest.approx(0.21, abs=1e-9)
    assert res["suggestion_annual"] == pytest.approx(146.41, abs=1e-6)


def test_trend_cagr_fy2026_plan_from_complete_fy2023_fy2024():
    """Worked example from the spec: FY2026 plan off complete FY2023/FY2024 (+earlier
    complete years), partial FY2025 excluded.  FY2022=100/FY2023=110/FY2024=121 →
    cagr 10% → 133.1; the partial FY2025 must not pollute base_annual/cagr."""
    actuals = {
        2022: _flat_fy(100.0),
        2023: _flat_fy(110.0),
        2024: _flat_fy(121.0),
        2025: _partial_fy(999_999.0),
    }
    res = bh.trend_cagr(actuals, years=3, current_fy=2025)
    assert res["explanation"]["window"] == [2022, 2024]
    assert res["explanation"]["cagr"] == pytest.approx(0.10, abs=1e-9)
    assert res["suggestion_annual"] == pytest.approx(133.1, abs=1e-6)


def test_trend_cagr_only_one_complete_fy_falls_back_to_prior_year():
    """EDGE: exactly 1 complete FY (the rest partial) → cannot compute a rate →
    fall back to prior_year off that complete FY (growth 0)."""
    actuals = {2024: _flat_fy(1000.0), 2025: _partial_fy(500.0)}
    res = bh.trend_cagr(actuals, years=3, current_fy=2025)
    assert res["suggestion_annual"] == pytest.approx(1000.0)
    assert res["explanation"]["base_fy"] == 2024
    assert "note" in res["explanation"]


def test_heuristics_no_complete_fy_graceful():
    """EDGE: NO complete FY (only the partial current year) → graceful 0 + note."""
    actuals = {2025: _partial_fy(500.0)}
    pr = bh.prior_year(actuals, current_fy=2025)
    assert pr["suggestion_annual"] == 0.0
    assert "note" in pr["explanation"]
    tc = bh.trend_cagr(actuals, years=3, current_fy=2025)
    assert tc["suggestion_annual"] == 0.0
    assert "note" in tc["explanation"]


def test_complete_actuals_strictly_before_current_fy():
    """``_complete_actuals`` keeps fy < current_fy; None → no filtering (pure path)."""
    actuals = {2023: _flat_fy(1.0), 2024: _flat_fy(2.0), 2025: _partial_fy(3.0)}
    kept = bh._complete_actuals(actuals, current_fy=2025)
    assert sorted(kept.keys()) == [2023, 2024]
    # A real all-zero (but complete) FY is KEPT — completeness keys off the anchor
    # year, not the position's own values.
    z = {2023: _flat_fy(0.0), 2024: _flat_fy(2.0), 2025: _partial_fy(3.0)}
    assert sorted(bh._complete_actuals(z, current_fy=2025).keys()) == [2023, 2024]
    # current_fy None → identity (unit-test path passes curated complete maps).
    assert sorted(bh._complete_actuals(actuals, current_fy=None).keys()) == [2023, 2024, 2025]


def test_suggest_trend_cagr_drops_partial_year_via_anchor(monkeypatch):
    """Dispatcher: with the ledger anchor at 2025/Jul (current_fy=2025) and a partial
    FY2025 in the pull, trend_cagr's base_fy/window exclude the partial year."""
    actuals = {
        2023: _flat_fy(100.0),
        2024: _flat_fy(121.0),
        2025: _partial_fy(-63_433_637.0),
    }
    monkeypatch.setattr(bh, "_position_actuals_by_fy", lambda *a, **k: actuals)
    monkeypatch.setattr(bh, "latest_anchor", lambda *a, **k: (2025, 7))
    res = bh.suggest(
        session=None, line_code="COST_OF_MATERIALS", statement="PL",
        fiscal_year=2026, method="trend_cagr", years=3,
    )
    exp = res["explanation"]
    assert exp["base_fy"] == 2024          # COMPLETE year, NOT the partial 2025
    assert exp["window"] == [2023, 2024]
    assert exp["last_annual"] == pytest.approx(121.0)
    assert res["suggestion_annual"] == pytest.approx(121.0 * 1.21, abs=1e-6)


def test_suggest_prior_year_drops_partial_year_via_anchor(monkeypatch):
    actuals = {2024: _flat_fy(1000.0), 2025: _partial_fy(-63_433_637.0)}
    monkeypatch.setattr(bh, "_position_actuals_by_fy", lambda *a, **k: actuals)
    monkeypatch.setattr(bh, "latest_anchor", lambda *a, **k: (2025, 7))
    res = bh.suggest(
        session=None, line_code="COST_OF_MATERIALS", statement="PL",
        fiscal_year=2026, method="prior_year",
    )
    assert res["explanation"]["base_fy"] == 2024
    assert res["suggestion_annual"] == pytest.approx(1000.0)


# =========================================================================== #
# (3) run_rate
# =========================================================================== #
def test_run_rate_last_three_months_times_12_over_3():
    """Last 3 observed months 100,120,140 → Σ=360 → 360*12/3 = 1440."""
    fy = _seasoned_fy([0.0] * 9 + [100.0, 120.0, 140.0])  # Oct/Nov/Dec booked
    res = bh.run_rate({2024: fy}, months=3)
    assert res["explanation"]["run_rate_sum"] == pytest.approx(360.0)
    assert res["explanation"]["observed_months"] == 3
    assert res["suggestion_annual"] == pytest.approx(1440.0, abs=1e-6)
    exp = res["explanation"]
    assert exp["method"] == "run_rate"
    assert exp["window_months"] == 3
    assert exp["base_fy"] == 2024
    assert exp["season_source"] == 2024


def test_run_rate_spans_fy_boundary():
    # Last booked month of 2023 is Dec (12), all of 2024 Jan..Mar booked.
    fy23 = _seasoned_fy([0.0] * 11 + [90.0])
    fy24 = _seasoned_fy([100.0, 120.0, 140.0] + [0.0] * 9)
    # observed chrono trailing: 2023 has last_booked=12 → 12 months (only Dec=90);
    # 2024 last_booked=3 → Jan..Mar.  Last 3 observed = Jan/Feb/Mar 2024.
    res = bh.run_rate({2023: fy23, 2024: fy24}, months=3)
    assert res["explanation"]["run_rate_sum"] == pytest.approx(360.0)
    assert res["suggestion_annual"] == pytest.approx(1440.0, abs=1e-6)


def test_run_rate_fewer_than_k_months_uses_available():
    fy = _seasoned_fy([100.0, 120.0] + [0.0] * 10)  # only Jan/Feb booked
    res = bh.run_rate({2024: fy}, months=3)
    assert res["explanation"]["observed_months"] == 2
    assert res["suggestion_annual"] == pytest.approx(220.0 * 12.0 / 2.0, abs=1e-6)
    assert "note" in res["explanation"]


def test_run_rate_no_history_zero_plus_note():
    res = bh.run_rate({}, months=3)
    assert res["suggestion_annual"] == 0.0
    assert "note" in res["explanation"]


def test_run_rate_negative_months_preserve_sign():
    fy = _seasoned_fy([0.0] * 9 + [-100.0, -120.0, -140.0])
    res = bh.run_rate({2024: fy}, months=3)
    assert res["suggestion_annual"] == pytest.approx(-1440.0, abs=1e-6)


# =========================================================================== #
# explanation completeness + weights reconcile for every heuristic
# =========================================================================== #
@pytest.mark.parametrize("fn", [bh.prior_year, bh.trend_cagr, bh.run_rate])
def test_weights_sum_to_one_and_finite(fn):
    actuals = {2022: _flat_fy(100.0), 2023: _flat_fy(110.0), 2024: _flat_fy(121.0)}
    res = fn(actuals)
    assert set(res["weights"].keys()) == set(PERIODS)
    assert sum(res["weights"].values()) == pytest.approx(1.0, abs=1e-9)
    assert math.isfinite(res["suggestion_annual"])
    assert res["explanation"]["method"] in {"prior_year", "trend_cagr", "run_rate"}


# =========================================================================== #
# Dispatcher (monkeypatched bounded DB pull)
# =========================================================================== #
@pytest.fixture
def _patched_actuals(monkeypatch):
    actuals = {2022: _flat_fy(100.0), 2023: _flat_fy(110.0), 2024: _flat_fy(121.0)}
    monkeypatch.setattr(
        bh, "_position_actuals_by_fy", lambda *a, **k: actuals
    )
    return actuals


def test_suggest_dispatch_prior_year(_patched_actuals):
    res = bh.suggest(
        session=None, line_code="NET_SALES", statement="PL",
        fiscal_year=2025, method="prior_year", growth_pct=0.0,
    )
    assert res["suggestion_annual"] == pytest.approx(121.0)
    assert res["explanation"]["method"] == "prior_year"


def test_suggest_dispatch_trend_cagr(_patched_actuals):
    res = bh.suggest(
        session=None, line_code="NET_SALES", statement="PL",
        fiscal_year=2025, method="trend_cagr", years=3,
    )
    assert res["suggestion_annual"] == pytest.approx(133.1, abs=1e-6)
    assert res["explanation"]["method"] == "trend_cagr"


def test_suggest_dispatch_run_rate(monkeypatch):
    fy = _seasoned_fy([0.0] * 9 + [100.0, 120.0, 140.0])
    monkeypatch.setattr(bh, "_position_actuals_by_fy", lambda *a, **k: {2024: fy})
    res = bh.suggest(
        session=None, line_code="NET_SALES", statement="PL",
        fiscal_year=2025, method="run_rate", months=3,
    )
    assert res["suggestion_annual"] == pytest.approx(1440.0, abs=1e-6)
    assert res["explanation"]["method"] == "run_rate"


def test_suggest_unknown_method_raises(_patched_actuals):
    with pytest.raises(ValueError):
        bh.suggest(
            session=None, line_code="NET_SALES", statement="PL",
            fiscal_year=2025, method="nonsense",
        )


# =========================================================================== #
# (4) seed_budget SKIP-ZERO — materialize-suggestion must not persist a 0
#     suggestion (else the budget→forecast→plan reader overrides forecast/plan
#     with 0 for that line_code).  DB-free: monkeypatch build_grid + a fake
#     session capturing the upserted line_codes.
# =========================================================================== #
class _CaptureSession:
    """Minimal Session double: records the (line_code, fiscal_period) of every
    INSERT issued by ``_upsert_rows`` so a test can assert exactly which positions
    were written, with no live DB."""

    def __init__(self):
        self.rows: list[dict] = []

    def execute(self, stmt, params=None):
        if params is not None and "lc" in params and "fp" in params:
            self.rows.append(params)
        return _Result()

    def commit(self):  # noqa: D401 — no-op for the double
        pass

    def rollback(self):
        pass


class _Result:
    """A SQLAlchemy-result double for the capture session (rowcount/scalar/fetchall)."""

    rowcount = 0

    def scalar(self):
        return 0

    def fetchall(self):
        return []


def _grid_with_suggestions(positions: list[dict]) -> dict:
    """A minimal L3 grid (the shape seed_budget consumes) from {line_code, months,
    suggestion_months} specs."""
    out = []
    for p in positions:
        out.append({
            "line_code": p["line_code"],
            "label": p["line_code"],
            "level_3": p["line_code"],
            "annual": sum(p["months"]),
            "months": p["months"],
            "synthetic_annual": sum(p["months"]),
            "is_partner_driven": False,
            "suggestion": {
                "annual": sum(p["suggestion_months"]),
                "months": p["suggestion_months"],
            },
            "explanation": {"method": "prior_year"},
        })
    return {
        "statement": "PL", "fiscal_year": 2025, "entity": "", "top_n": 20,
        "level": "L3", "positions": out,
    }


def _seed_capture(monkeypatch, grid: dict, **seed_kwargs) -> _CaptureSession:
    monkeypatch.setattr(budget_service, "build_grid", lambda *a, **k: grid)
    sess = _CaptureSession()
    budget_service.seed_budget(
        sess, statement="PL", fiscal_year=2025, entity="", **seed_kwargs
    )
    return sess


def _written_line_codes(sess: _CaptureSession) -> set[str]:
    return {r["lc"] for r in sess.rows}


def test_all_zero_months_helper():
    assert _all_zero_months({p: 0.0 for p in PERIODS}) is True
    assert _all_zero_months({p: 0.0 for p in PERIODS} | {1: 1e-9}) is True  # within EPS
    assert _all_zero_months({p: 0.0 for p in PERIODS} | {1: 100.0}) is False
    # nets to ~0 but has real monthly signal → NOT all-zero (must be persisted).
    offsetting = {p: 0.0 for p in PERIODS} | {1: 100.0, 2: -100.0}
    assert _all_zero_months(offsetting) is False


def test_materialize_skips_zero_suggestions(monkeypatch):
    """Apply-heuristic writes ONLY positions with a non-zero suggestion.

    Two of three positions suggest 0 → only the one non-zero position is written
    (12 rows = 1 position × 12 months); the zero positions have no budget row."""
    grid = _grid_with_suggestions([
        {"line_code": "NET_SALES", "months": [100.0] * 12,
         "suggestion_months": [110.0] * 12},          # non-zero → written
        {"line_code": "OTHER_INCOME", "months": [5.0] * 12,
         "suggestion_months": [0.0] * 12},            # zero suggestion → skipped
        {"line_code": "MISC_EXPENSE", "months": [7.0] * 12,
         "suggestion_months": [0.0] * 12},            # zero suggestion → skipped
    ])
    sess = _seed_capture(
        monkeypatch, grid,
        materialize_suggestion=True, heuristic="prior_year", growth_pct=0.0,
    )
    assert _written_line_codes(sess) == {"NET_SALES"}
    assert len(sess.rows) == 1 * 12  # exactly non-zero positions × 12 months


def test_materialize_keeps_offsetting_nonzero_suggestion(monkeypatch):
    """A suggestion that nets to ~0 from offsetting months IS a real signal → written."""
    grid = _grid_with_suggestions([
        {"line_code": "NET_SALES",
         "months": [0.0] * 12,
         "suggestion_months": [100.0, -100.0] + [0.0] * 10},  # Σ≈0 but real months
    ])
    sess = _seed_capture(
        monkeypatch, grid, materialize_suggestion=True,
    )
    assert _written_line_codes(sess) == {"NET_SALES"}
    assert len(sess.rows) == 12


def test_legacy_seed_writes_every_position(monkeypatch):
    """materialize_suggestion=False (legacy synthetic seed) is UNCHANGED: it writes
    every position from its ``months`` regardless of the suggestion value, including
    positions whose months are 0 (byte-identical to the prior behaviour)."""
    grid = _grid_with_suggestions([
        {"line_code": "NET_SALES", "months": [100.0] * 12,
         "suggestion_months": [0.0] * 12},   # suggestion 0 is irrelevant in legacy mode
        {"line_code": "OTHER_INCOME", "months": [0.0] * 12,
         "suggestion_months": [0.0] * 12},   # even an all-zero legacy month is written
    ])
    sess = _seed_capture(monkeypatch, grid, materialize_suggestion=False)
    # Both positions written (legacy seed does not skip) → 2 × 12 rows.
    assert _written_line_codes(sess) == {"NET_SALES", "OTHER_INCOME"}
    assert len(sess.rows) == 2 * 12


def test_manual_upsert_zero_still_persists():
    """A user-entered 0 is a REAL budget and MUST persist: upsert_cell with an
    explicit annual=0 writes all 12 rows (only the AUTO-materialize of a zero
    SUGGESTION is skipped, never a manual write).  DB-free via the capture session;
    a non-partner-driven line_code keeps the path off the partner re-derive."""
    sess = _CaptureSession()
    res = budget_service.upsert_cell(
        sess, statement="PL", line_code="MISC_EXPENSE_LINE", entity="",
        partner_id=None, partner_kind=None, fiscal_year=2025,
        annual=0.0, weights=None, updated_by="user@finssentials",
    )
    assert res["rows_upserted"] == 12
    assert len(sess.rows) == 12
    assert all(r["amt"] == 0.0 for r in sess.rows)  # the explicit 0 is stored
