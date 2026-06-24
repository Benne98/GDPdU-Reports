"""Tests for the regression/histogram/distribution helpers (anomaly rework v2, Phase 0).

PURE / DB-FREE.  Locks:
  * ``compute_linear_regression`` — slope/intercept/r² on a known line, flat series,
    n<2, negative slope.
  * ``compute_histogram`` — Σ count == n, equal-width bins, degenerate single bin.
  * ``distribution_stats`` — percentiles on a known series, edges.
  * NODE WIRING — the tree node payload now carries per-node ``signal_score``/``band``,
    per-point ``signal_score``, and ``payload.regression``/``histogram``/``distribution``
    (n>=6) or None (n<6).  Exercised via a hand-built AccountSeries through the tree
    builder helper (synthetic only — CLAUDE.md rule #2).
"""
from __future__ import annotations

import pytest

from app.services import gl_anomaly_tree as T
from app.services.gl_analysis_common import AccountSeries, MonthPoint
from app.services.fin_compat_sql import period_key, period_label
from app.services.gl_outliers import (
    compute_histogram,
    compute_linear_regression,
    distribution_stats,
)


# --------------------------------------------------------------------------- #
# compute_linear_regression
# --------------------------------------------------------------------------- #
def test_regression_perfect_line_slope_10():
    out = compute_linear_regression([0, 10, 20, 30, 40])
    assert out["slope"] == pytest.approx(10.0)
    assert out["intercept"] == pytest.approx(0.0)
    assert out["r_squared"] == pytest.approx(1.0)
    assert out["trend_start"] == pytest.approx(0.0)
    assert out["trend_end"] == pytest.approx(40.0)  # slope*(n-1)+intercept = 10*4


def test_regression_intercept_offset_line():
    # y = 5x + 100 → slope 5, intercept 100, r²=1
    out = compute_linear_regression([100, 105, 110, 115])
    assert out["slope"] == pytest.approx(5.0)
    assert out["intercept"] == pytest.approx(100.0)
    assert out["r_squared"] == pytest.approx(1.0)
    assert out["trend_end"] == pytest.approx(115.0)


def test_regression_negative_slope_preserved():
    out = compute_linear_regression([40, 30, 20, 10, 0])
    assert out["slope"] == pytest.approx(-10.0)
    assert out["r_squared"] == pytest.approx(1.0)


def test_regression_flat_series_zero_slope_r2_zero():
    out = compute_linear_regression([7, 7, 7, 7])
    assert out["slope"] == pytest.approx(0.0)
    assert out["intercept"] == pytest.approx(7.0)
    assert out["r_squared"] == 0.0  # no variance to explain → 0, not NaN


def test_regression_n_lt_2():
    assert compute_linear_regression([]) == {
        "slope": 0.0, "intercept": 0.0, "r_squared": 0.0,
        "trend_start": 0.0, "trend_end": 0.0,
    }
    one = compute_linear_regression([42.0])
    assert one["slope"] == 0.0 and one["intercept"] == pytest.approx(42.0)
    assert one["trend_start"] == pytest.approx(42.0) and one["trend_end"] == pytest.approx(42.0)


# --------------------------------------------------------------------------- #
# compute_histogram
# --------------------------------------------------------------------------- #
def test_histogram_count_sums_to_n():
    vals = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    hist = compute_histogram(vals, bins=10)
    assert sum(b["count"] for b in hist) == len(vals)
    assert len(hist) == 10


def test_histogram_arbitrary_series_sum_invariant():
    vals = [3.2, -1.0, 5.5, 5.5, 0.0, 12.7, -4.4, 8.1]
    for bins in (3, 5, 10, 20):
        hist = compute_histogram(vals, bins=bins)
        assert sum(b["count"] for b in hist) == len(vals)


def test_histogram_degenerate_all_equal_single_bin():
    hist = compute_histogram([5, 5, 5, 5], bins=10)
    assert len(hist) == 1
    assert hist[0]["count"] == 4
    assert hist[0]["bin_lo"] == hist[0]["bin_hi"] == 5.0


def test_histogram_n_lt_2_single_bin():
    assert compute_histogram([9.0]) == [{"bin_lo": 9.0, "bin_hi": 9.0, "count": 1}]
    assert compute_histogram([]) == []


def test_histogram_max_value_is_counted():
    # Right edge of the last bin must include the max (no off-by-one drop).
    hist = compute_histogram([0, 1, 2, 3, 4], bins=4)
    assert sum(b["count"] for b in hist) == 5


# --------------------------------------------------------------------------- #
# distribution_stats
# --------------------------------------------------------------------------- #
def test_distribution_known_series():
    out = distribution_stats([1, 2, 3, 4, 5])
    assert out["min"] == 1.0
    assert out["max"] == 5.0
    assert out["median"] == 3.0
    assert out["q1"] == 2.0
    assert out["q3"] == 4.0
    assert out["iqr"] == 2.0


def test_distribution_empty_and_single():
    assert distribution_stats([]) == {
        "min": 0.0, "max": 0.0, "median": 0.0, "q1": 0.0, "q3": 0.0, "iqr": 0.0,
    }
    one = distribution_stats([7.0])
    assert one["min"] == one["max"] == one["median"] == 7.0
    assert one["iqr"] == 0.0


# --------------------------------------------------------------------------- #
# NODE WIRING — through the tree builder helpers
# --------------------------------------------------------------------------- #
def _acc(points, *, l0="PL", l4="", ep="01") -> AccountSeries:
    series = [
        MonthPoint(
            fiscal_year=y, fiscal_period=m, period_key=period_key(y, m),
            label=period_label(y, m), value_keur=v,
        )
        for (y, m, v) in points
    ]
    return AccountSeries(
        gl_account_id="40000", account_name="acct 40000",
        account_number_group="0140000", entity_prefix=ep,
        level_0=l0, level_2="Income", level_3="Net sales", level_4=l4, l4_sub="",
        series=series,
    )


def _points_12(spike_idx=11, spike=60.0, base=10.0):
    pts = []
    for i in range(12):
        y, m = 2023, i + 1
        v = spike if i == spike_idx else base
        pts.append((y, m, v))
    return pts


def test_node_payload_carries_score_band_and_stats_when_n_ge_6():
    acc = _acc(_points_12())
    node = T._account_node(acc, "outliers", min_years=T.MIN_YEARS)

    # node-level score + band derived from max_abs_z
    assert "signal_score" in node and "band" in node
    assert 0 <= node["signal_score"] <= 100
    assert node["band"] in ("low", "medium", "high")
    assert node["signal_score"] == T.signal_score_0to100(node["max_abs_z"])

    payload = node["payload"]
    # per-point signal_score on every series point
    assert all("signal_score" in p for p in payload["series"])
    # the spike point should score higher than a baseline point
    spike_p = max(payload["series"], key=lambda p: abs(p.get("z") or 0))
    assert spike_p["signal_score"] > 0

    # n=12 >= MIN_STATS_POINTS → regression/histogram/distribution present
    assert payload["regression"] is not None
    assert payload["histogram"] is not None
    assert payload["distribution"] is not None
    assert sum(b["count"] for b in payload["histogram"]) == 12
    assert set(payload["distribution"]) == {"min", "max", "median", "q1", "q3", "iqr"}


def test_node_stats_omitted_when_n_lt_6():
    # 4 points (< MIN_STATS_POINTS) → stats are None, but scores still attached.
    acc = _acc([(2023, 1, 10.0), (2023, 2, 12.0), (2023, 3, 11.0), (2023, 4, 60.0)])
    node = T._account_node(acc, "outliers", min_years=T.MIN_YEARS)
    payload = node["payload"]
    assert payload["regression"] is None
    assert payload["histogram"] is None
    assert payload["distribution"] is None
    assert all("signal_score" in p for p in payload["series"])
    assert "signal_score" in node and "band" in node


def test_back_compat_z_and_max_abs_z_retained():
    acc = _acc(_points_12())
    node = T._account_node(acc, "outliers", min_years=T.MIN_YEARS)
    # max_abs_z + per-point z stay in the payload (UI hides them, but kept internal).
    assert "max_abs_z" in node
    assert all("z" in p for p in node["payload"]["series"])
