"""Tests for gl_seasonality (Journal Agent, Phase 2) — additive decomposition.

DB-FREE: the statistical core (centered rolling trend, per-month seasonal index
normalised to sum 0, expected/residual/z) and the per-account serialisation are pure
functions fed synthetic numbers / ``AccountSeries`` objects. Locks the CLAUDE.md
rule-#1 worked example (December peak: large positive seasonal_index[12]; a flat
December → large negative residual z → flagged "missing peak"; a mid-year spike →
positive residual z → flagged) and the edge cases (insufficient history, NaN trend
at the series ends → z None, resid_std == 0 → no flags).
"""
from __future__ import annotations

import math

from app.services import gl_analysis_common as G
from app.services import gl_seasonality as S


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


def _december_peak_points(years, *, base=100.0, peak=60.0):
    """3+ years of monthly data: ``base`` every month + ``peak`` extra each December."""
    pts = []
    for y in years:
        for m in range(1, 13):
            v = base + (peak if m == 12 else 0.0)
            pts.append((y, m, v))
    return pts


# =========================================================================== #
# (1) Seasonal indices ALWAYS sum to 0 (the normalisation invariant)
# =========================================================================== #
def test_seasonal_indices_sum_to_zero():
    pts = _december_peak_points([2021, 2022, 2023])
    values = [v for (_, _, v) in pts]
    months = [m for (_, m, _) in pts]
    decomp = S.compute_seasonal_decomposition(values, months)

    factors = [f["seasonal_index"] for f in decomp["month_index"]]
    assert len(factors) == 12
    # Factors are rounded to 4 dp in the payload, so allow rounding slack (≤ 12 × 5e-5);
    # the underlying (unrounded) vector is centred to sum exactly 0.
    assert math.isclose(sum(factors), 0.0, abs_tol=1e-3)


def test_empty_series_returns_zeroed_shape():
    decomp = S.compute_seasonal_decomposition([], [])
    assert decomp["trend"] == [] and decomp["z"] == []
    assert [f["seasonal_index"] for f in decomp["month_index"]] == [0.0] * 12
    assert decomp["stats"] == {"resid_std_keur": 0.0, "n": 0}


# =========================================================================== #
# (2) December peak → seasonal_index[12] is the largest positive factor
# =========================================================================== #
def test_december_is_the_dominant_positive_factor():
    pts = _december_peak_points([2021, 2022, 2023])
    values = [v for (_, _, v) in pts]
    months = [m for (_, m, _) in pts]
    decomp = S.compute_seasonal_decomposition(values, months)

    by_month = {f["month"]: f["seasonal_index"] for f in decomp["month_index"]}
    dec = by_month[12]
    assert dec > 0
    # December is strictly the largest factor of all 12.
    assert dec == max(by_month.values())
    # The non-December months are all (weakly) negative, summing to −dec.
    assert all(by_month[m] <= 0 for m in range(1, 12))


# =========================================================================== #
# (3) Worked example: a FLAT December where a peak is expected → negative-z flag,
#     and a mid-year SPIKE → positive-z flag, both at σ = 1.0.
# =========================================================================== #
def test_missing_peak_and_spike_both_flag_at_sigma_one():
    # 3 clean December-peak years, then a 4th year where December stays flat (100,
    # missing peak) and May spikes to 200 (unexpected).
    pts = _december_peak_points([2021, 2022, 2023])
    for m in range(1, 13):
        v = 100.0
        if m == 5:
            v = 200.0            # unexpected mid-year spike
        # December left at base 100 → the expected ~+ peak is MISSING
        pts.append((2024, m, v))

    values = [v for (_, _, v) in pts]
    months = [m for (_, m, _) in pts]
    decomp = S.compute_seasonal_decomposition(values, months)

    # Index of 2024-05 (spike) and 2024-12 (missing peak) in the flat list.
    idx_may = months.index(5, 36)        # first month-5 at or after the 4th year start
    # 2024-12 is the last element.
    idx_dec = len(months) - 1

    z_may = decomp["z"][idx_may]
    z_dec = decomp["z"][idx_dec]

    assert z_may is not None and z_dec is not None
    # Spike sits far ABOVE expected → positive residual/z, flagged at σ=1.
    assert z_may > 0 and abs(z_may) >= 1.0
    # Missing December peak sits far BELOW expected → negative residual/z, flagged.
    assert z_dec < 0 and abs(z_dec) >= 1.0


# =========================================================================== #
# (4) Residual reconciliation: actual = expected + residual at every finite point
# =========================================================================== #
def test_actual_equals_expected_plus_residual():
    pts = _december_peak_points([2021, 2022, 2023])
    values = [v for (_, _, v) in pts]
    months = [m for (_, m, _) in pts]
    decomp = S.compute_seasonal_decomposition(values, months)

    for i, x in enumerate(values):
        exp = decomp["expected"][i]
        res = decomp["residual"][i]
        if exp is None or res is None:
            continue          # NaN-trend ends — not reconciled (by design)
        assert math.isclose(x, exp + res, abs_tol=1e-6)


# =========================================================================== #
# (5) NaN trend (rolling window has < min_periods valid points) → trend/expected/
#     residual/z are all None there, so those months never flag.
# =========================================================================== #
def test_nan_trend_yields_none_z():
    # A SHORT 5-month series: with a centered 12-window and min_periods=6, NO point has
    # 6 neighbours → trend is NaN everywhere → expected/residual/z all None.
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    months = [1, 2, 3, 4, 5]
    decomp = S.compute_seasonal_decomposition(values, months)

    assert all(t is None for t in decomp["trend"])
    assert all(e is None for e in decomp["expected"])
    assert all(z is None for z in decomp["z"])      # NaN trend → no flags possible
    assert decomp["stats"]["n"] == 0                # zero finite residuals


def test_dense_series_trend_is_defined_everywhere():
    # The realistic case: a 36-month series has ≥ 6 points in every centered window,
    # so trend is defined at every point (NaN-trend ends only bite very short series).
    pts = _december_peak_points([2021, 2022, 2023])
    decomp = S.compute_seasonal_decomposition(
        [v for (_, _, v) in pts], [m for (_, m, _) in pts]
    )
    assert all(t is not None for t in decomp["trend"])


# =========================================================================== #
# (6) resid_std == 0 (perfectly fitted / < 2 finite residuals) → no flags
# =========================================================================== #
def test_flat_residual_gives_no_flags():
    # A perfectly linear-with-season series whose residual is identically 0 in the
    # interior. Use a pure constant: trend == value, seasonal == 0, residual == 0.
    pts = [(y, m, 50.0) for y in (2021, 2022, 2023) for m in range(1, 13)]
    values = [v for (_, _, v) in pts]
    months = [m for (_, m, _) in pts]
    decomp = S.compute_seasonal_decomposition(values, months)

    assert decomp["stats"]["resid_std_keur"] == 0.0
    # resid_std == 0 → every z is None → nothing can flag.
    assert all(z is None for z in decomp["z"])


# =========================================================================== #
# (7) Insufficient history (< min_years) → flat indices + insufficient_history flag
# =========================================================================== #
def test_insufficient_history_account_payload():
    # Only ONE fiscal year → < min_years(2).
    acc = _series_from(
        [(2024, m, 100.0 + (60.0 if m == 12 else 0.0)) for m in range(1, 13)],
        name="OneYear", gid="4000",
    )
    payload = S._account_payload(acc, min_years=S.MIN_YEARS)

    assert payload["insufficient_history"] is True
    # All 12 seasonal factors are 0 (no trustworthy pattern).
    assert all(f["seasonal_index"] == 0.0 for f in payload["month_index"])
    # Per-point seasonal_index is 0 and expected falls back to the bare trend.
    for p in payload["series"]:
        assert p["seasonal_index"] == 0.0
        if p["trend_keur"] is not None:
            assert p["expected_keur"] == p["trend_keur"]


def test_sufficient_history_clears_the_flag():
    acc = _series_from(_december_peak_points([2022, 2023, 2024]), gid="4100")
    payload = S._account_payload(acc, min_years=S.MIN_YEARS)
    assert payload["insufficient_history"] is False
    # December factor is the dominant positive one for a real decomposition.
    by_month = {f["month"]: f["seasonal_index"] for f in payload["month_index"]}
    assert by_month[12] == max(by_month.values())


# =========================================================================== #
# (8) Per-account payload contract
# =========================================================================== #
def test_account_payload_shape_and_statement_mapping():
    acc = _series_from(
        _december_peak_points([2022, 2023, 2024]),
        level_0="PL", name="Revenue", gid="4000",
    )
    payload = S._account_payload(acc, min_years=S.MIN_YEARS)

    assert payload["gl_account_id"] == "4000"
    assert payload["statement"] == "pl"
    assert payload["entity_prefix"] == "01"
    assert len(payload["series"]) == 36
    assert len(payload["month_index"]) == 12
    p0 = payload["series"][0]
    assert set(p0) == {
        "period_key", "label", "fiscal_period", "actual_keur", "trend_keur",
        "seasonal_index", "expected_keur", "residual_keur", "z",
    }
    assert set(payload["stats"]) == {"resid_std_keur", "n"}


def test_bs_account_maps_to_bs_statement():
    acc = _series_from(
        [(y, m, 600.0) for y in (2022, 2023, 2024) for m in range(1, 13)],
        level_0="BS", gid="1200",
    )
    assert S._account_payload(acc, min_years=S.MIN_YEARS)["statement"] == "bs"


# =========================================================================== #
# (9) Anchor resolution (period only labels the view; series stays monthly)
# =========================================================================== #
def test_resolve_anchor_month_year_week():
    ay, am, _ = S._resolve_anchor({"grain": "month", "year": 2024, "month": 7})
    assert (ay, am) == (2024, 7)

    ay, am, label = S._resolve_anchor({"grain": "year", "year": 2024, "month": 12})
    assert (ay, am) == (2024, 12) and label == "FY2024"

    ay, am, label = S._resolve_anchor({"grain": "week", "iso_year": 2025, "iso_week": 31})
    # CW31/2025 Thursday is 2025-07-31 → anchor month July.
    assert (ay, am) == (2025, 7) and label.startswith("CW31")


# =========================================================================== #
# (10) build_seasonality rejects an unsupported statement (before touching DB)
# =========================================================================== #
def test_build_seasonality_rejects_bad_statement():
    import pytest

    class _Sess:  # never reached — validation happens first
        pass

    with pytest.raises(ValueError):
        S.build_seasonality(_Sess(), {"grain": "month", "year": 2024, "month": 1}, None, "xx")
