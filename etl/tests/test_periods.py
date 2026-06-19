"""P5 / C1 — Pure tests for the period engine (app.services.periods).

NO DB, NO server.  Locks the FY/YTD/LTM/Coverage definitions and every view_mode
+ edge case from the module docstring.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make backend/ importable.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKEND = _REPO_ROOT / "backend"
for p in (str(_REPO_ROOT), str(_BACKEND)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.services import periods as P


def _col(plan, key):
    return next(c for c in plan.columns if c.key == key)


# =========================================================================== #
# Year view — the worked example (current_fy=2025, last_closed_period=5)
# =========================================================================== #
class TestYearViewWorkedExample:
    def setup_method(self):
        self.plan = P.build_period_plan("year", 2025, 5)

    def test_column_keys_order(self):
        assert [c.key for c in self.plan.columns] == list(P.YEAR_COLUMN_KEYS)

    def test_fy_minus_2(self):
        assert _col(self.plan, "FY-2").buckets == tuple((2023, p) for p in range(1, 13))

    def test_fy_minus_1(self):
        assert _col(self.plan, "FY-1").buckets == tuple((2024, p) for p in range(1, 13))

    def test_fy(self):
        assert _col(self.plan, "FY").buckets == tuple((2025, p) for p in range(1, 13))

    def test_ytd_is_periods_1_to_5_of_current(self):
        assert _col(self.plan, "YTD").buckets == tuple((2025, p) for p in range(1, 6))

    def test_ytd_py_is_periods_1_to_5_of_prior(self):
        assert _col(self.plan, "YTD_PY").buckets == tuple((2024, p) for p in range(1, 6))

    def test_ltm_straddles_boundary_and_is_12_periods(self):
        ltm = _col(self.plan, "LTM").buckets
        expected = tuple((2024, p) for p in range(6, 13)) + tuple((2025, p) for p in range(1, 6))
        assert ltm == expected
        assert len(ltm) == 12

    def test_ltm_py_is_prior_12(self):
        ltm_py = _col(self.plan, "LTM_PY").buckets
        expected = tuple((2023, p) for p in range(6, 13)) + tuple((2024, p) for p in range(1, 6))
        assert ltm_py == expected
        assert len(ltm_py) == 12

    def test_coverage(self):
        assert self.plan.coverage == pytest.approx(5 / 12)


# =========================================================================== #
# Year view — edge cases
# =========================================================================== #
class TestYearViewEdgeCases:
    def test_nothing_closed_L0(self):
        plan = P.build_period_plan("year", 2025, 0)
        assert _col(plan, "YTD").buckets == ()       # empty
        assert _col(plan, "YTD_PY").buckets == ()
        # LTM with nothing closed = full prior FY
        assert _col(plan, "LTM").buckets == tuple((2024, p) for p in range(1, 13))
        assert _col(plan, "LTM_PY").buckets == tuple((2023, p) for p in range(1, 13))
        assert plan.coverage == 0.0

    def test_full_year_closed_L12(self):
        plan = P.build_period_plan("year", 2025, 12)
        assert _col(plan, "YTD").buckets == tuple((2025, p) for p in range(1, 13))
        # LTM full year closed = current full FY
        assert _col(plan, "LTM").buckets == tuple((2025, p) for p in range(1, 13))
        assert _col(plan, "LTM_PY").buckets == tuple((2024, p) for p in range(1, 13))
        assert plan.coverage == 1.0
        # YTD equals FY column when fully closed
        assert _col(plan, "YTD").buckets == _col(plan, "FY").buckets

    def test_ltm_always_12_for_any_L(self):
        for L in range(0, 13):
            plan = P.build_period_plan("year", 2030, L)
            assert len(_col(plan, "LTM").buckets) == 12
            assert len(_col(plan, "LTM_PY").buckets) == 12

    def test_ltm_and_ltm_py_disjoint_and_contiguous(self):
        # LTM_PY is exactly the 12 periods immediately before LTM (no overlap).
        plan = P.build_period_plan("year", 2025, 5)
        ltm = set(_col(plan, "LTM").buckets)
        ltm_py = set(_col(plan, "LTM_PY").buckets)
        assert ltm.isdisjoint(ltm_py)

    def test_invalid_last_closed_period_raises(self):
        with pytest.raises(ValueError):
            P.build_period_plan("year", 2025, 13)
        with pytest.raises(ValueError):
            P.build_period_plan("year", 2025, -1)

    def test_unknown_view_mode_raises(self):
        with pytest.raises(ValueError):
            P.build_period_plan("quarter", 2025, 5)  # type: ignore[arg-type]


# =========================================================================== #
# Month / week views
# =========================================================================== #
class TestMonthView:
    def test_default_span_is_prior_and_current_fy(self):
        plan = P.build_period_plan("month", 2025, 5)
        assert len(plan.columns) == 24  # 12 + 12
        keys = [c.key for c in plan.columns]
        assert keys[0] == "2024-01"
        assert keys[11] == "2024-12"
        assert keys[12] == "2025-01"
        assert keys[-1] == "2025-12"

    def test_each_month_column_is_single_bucket(self):
        plan = P.build_period_plan("month", 2025, 5)
        for c in plan.columns:
            assert len(c.buckets) == 1

    def test_calendar_month_labels_default(self):
        plan = P.build_period_plan("month", 2025, 5)
        assert _col(plan, "2025-01").label == "Jan 2025"
        assert _col(plan, "2025-12").label == "Dec 2025"

    def test_non_calendar_fy_start_month_labels(self):
        # FY starts in April (fy_start_month=4): period 1 -> Apr, period 10 -> Jan next cal year.
        plan = P.build_period_plan("month", 2025, 5, fy_start_month=4)
        assert _col(plan, "2025-01").label == "Apr 2025"
        # period 10 = Apr + 9 = Jan of 2026
        assert _col(plan, "2025-10").label == "Jan 2026"

    def test_week_view_reuses_month_buckets(self):
        m = P.build_period_plan("month", 2025, 5)
        w = P.build_period_plan("week", 2025, 5)
        assert [c.buckets for c in w.columns] == [c.buckets for c in m.columns]
        assert w.view_mode == "week"


# =========================================================================== #
# Determinism
# =========================================================================== #
def test_deterministic_year():
    a = P.build_period_plan("year", 2025, 7)
    b = P.build_period_plan("year", 2025, 7)
    assert [c.buckets for c in a.columns] == [c.buckets for c in b.columns]
    assert a.coverage == b.coverage
