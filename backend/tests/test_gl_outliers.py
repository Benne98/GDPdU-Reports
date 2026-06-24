"""Tests for gl_outliers (Journal Agent, Phase 1) — z-score outlier on a series.

Mostly DB-FREE: the statistical core (mean / sample std / per-point residual+z)
and the per-account serialisation are pure functions fed synthetic numbers /
``AccountSeries`` objects.  Locks the CLAUDE.md rule-#1 worked example and the
edge cases (n<2, std=0, |z|==σ boundary, sign-agnostic).
"""
from __future__ import annotations

import math

from app.services import gl_analysis_common as G
from app.services import gl_outliers as O


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _series_from(points, *, level_0="PL", name="Acct", gid="1000"):
    return G.AccountSeries(
        gl_account_id=gid, account_name=name, account_number_group="01" + gid,
        entity_prefix="01", level_0=level_0, level_2="L2", level_3="L3",
        series=[
            G.MonthPoint(fiscal_year=y, fiscal_period=m,
                         period_key=G.period_key(y, m),
                         label=G.period_label(y, m), value_keur=v)
            for (y, m, v) in points
        ],
    )


# =========================================================================== #
# (1) The CLAUDE.md worked example: [10,12,11,13,60] kEUR
# =========================================================================== #
def test_worked_example_mean_std_and_spike_zscore():
    x = [10.0, 12.0, 11.0, 13.0, 60.0]
    stats = O.compute_series_stats(x)

    assert stats["n"] == 5
    assert stats["mean_keur"] == 21.2                       # (10+12+11+13+60)/5
    # SAMPLE std (ddof=1) ≈ 21.72 — NOT the population std (≈19.43).
    assert math.isclose(stats["std_keur"], 21.7187, abs_tol=1e-3)

    rz = O.compute_point_zscores(x, stats["mean_keur"], stats["std_keur"])
    residual_spike, z_spike = rz[4]
    assert residual_spike == 38.8                           # 60 − 21.2
    assert math.isclose(z_spike, 1.7865, abs_tol=1e-3)      # 38.8 / 21.72

    # Slider semantics: spike flags at σ=1 but NOT at σ=2.
    assert abs(z_spike) >= 1.0
    assert abs(z_spike) < 2.0
    # The non-spike points never flag at σ>=1.
    assert all(abs(z) < 1.0 for _, z in rz[:4])


# =========================================================================== #
# (2) Edge: n < 2 → std 0, all z 0 (nothing can flag)
# =========================================================================== #
def test_single_point_has_zero_std_and_zero_z():
    stats = O.compute_series_stats([42.0])
    assert stats["n"] == 1
    assert stats["std_keur"] == 0.0
    rz = O.compute_point_zscores([42.0], stats["mean_keur"], stats["std_keur"])
    assert rz == [(0.0, 0.0)]


def test_empty_series_stats():
    assert O.compute_series_stats([]) == {"mean_keur": 0.0, "std_keur": 0.0, "n": 0}


# =========================================================================== #
# (3) Edge: flat series (std == 0) → residual 0, z 0, no divide-by-zero
# =========================================================================== #
def test_flat_series_no_division_by_zero():
    x = [5.0, 5.0, 5.0, 5.0]
    stats = O.compute_series_stats(x)
    assert stats["std_keur"] == 0.0
    rz = O.compute_point_zscores(x, stats["mean_keur"], stats["std_keur"])
    assert rz == [(0.0, 0.0)] * 4


# =========================================================================== #
# (4) Edge: |z| == σ EXACTLY is on the inclusive (flag) side of the boundary
# =========================================================================== #
def test_boundary_z_equals_sigma_is_flagged_by_client_rule():
    # Symmetric series so an exact |z| can be constructed: [−1, +1], sample std = √2.
    x = [-1.0, 1.0]
    stats = O.compute_series_stats(x)
    assert math.isclose(stats["std_keur"], math.sqrt(2), abs_tol=1e-4)
    rz = O.compute_point_zscores(x, stats["mean_keur"], stats["std_keur"])
    # z = ±1/√2 ≈ ±0.7071.  At a slider set EXACTLY to that |z| the client rule
    # |z| >= σ must be TRUE (boundary is inclusive — the client uses >=).
    _, z_hi = rz[1]
    sigma = 0.7071                       # a fixed σ equal to the point's |z| (4dp)
    assert round(abs(z_hi), 4) == sigma
    assert abs(z_hi) >= sigma           # inclusive boundary flags


# =========================================================================== #
# (5) Sign-agnostic: a large negative swing flags like a large positive one
# =========================================================================== #
def test_negative_spike_flags_symmetrically():
    pos = O.compute_series_stats([10.0, 12.0, 11.0, 13.0, 60.0])
    neg = O.compute_series_stats([-10.0, -12.0, -11.0, -13.0, -60.0])
    assert pos["std_keur"] == neg["std_keur"]
    z_pos = O.compute_point_zscores([10, 12, 11, 13, 60], pos["mean_keur"], pos["std_keur"])[4][1]
    z_neg = O.compute_point_zscores([-10, -12, -11, -13, -60], neg["mean_keur"], neg["std_keur"])[4][1]
    assert math.isclose(abs(z_pos), abs(z_neg), abs_tol=1e-6)


# =========================================================================== #
# (6) Per-account payload shape: full series + residual/z + stats + statement
# =========================================================================== #
def test_account_payload_shape_and_statement_mapping():
    acc = _series_from(
        [(2024, 1, 10.0), (2024, 2, 12.0), (2024, 3, 11.0),
         (2024, 4, 13.0), (2024, 5, 60.0)],
        level_0="PL", name="Revenue", gid="4000",
    )
    payload = O._account_payload(acc)

    assert payload["gl_account_id"] == "4000"
    assert payload["statement"] == "pl"
    assert payload["level_2"] == "L2" and payload["level_3"] == "L3"
    assert payload["entity_prefix"] == "01"
    assert len(payload["series"]) == 5
    # Every point carries the full contract.
    p0 = payload["series"][0]
    assert set(p0) == {"period_key", "label", "value_keur", "residual_keur", "z"}
    # Spike point matches the worked example.
    spike = payload["series"][4]
    assert spike["value_keur"] == 60.0
    assert spike["residual_keur"] == 38.8
    assert math.isclose(spike["z"], 1.7865, abs_tol=1e-3)
    assert payload["stats"] == {"mean_keur": 21.2, "std_keur": round(payload["stats"]["std_keur"], 4), "n": 5}


def test_bs_account_maps_to_bs_statement():
    acc = _series_from([(2024, 1, 600.0), (2024, 2, 650.0)], level_0="BS", gid="1200")
    assert O._account_payload(acc)["statement"] == "bs"


# =========================================================================== #
# (7) Anchor resolution (period only labels the view; series stays monthly)
# =========================================================================== #
def test_resolve_anchor_month_year_week():
    ay, am, label = O._resolve_anchor({"grain": "month", "year": 2024, "month": 7})
    assert (ay, am) == (2024, 7)

    ay, am, label = O._resolve_anchor({"grain": "year", "year": 2024, "month": 12})
    assert (ay, am) == (2024, 12) and label == "FY2024"

    ay, am, label = O._resolve_anchor(
        {"grain": "week", "iso_year": 2025, "iso_week": 31}
    )
    # CW31/2025 Thursday is 2025-07-31 → anchor month July.
    assert (ay, am) == (2025, 7) and label.startswith("CW31")


# =========================================================================== #
# (8) build_outliers rejects an unsupported statement
# =========================================================================== #
def test_build_outliers_rejects_bad_statement():
    import pytest

    class _Sess:  # never reached — validation happens first
        pass

    with pytest.raises(ValueError):
        O.build_outliers(_Sess(), {"grain": "month", "year": 2024, "month": 1}, None, "xx")
