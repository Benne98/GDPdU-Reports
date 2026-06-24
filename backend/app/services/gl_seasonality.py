"""GL seasonality analysis (Journal Agent, Phase 2) — additive decomposition.

Builds, per material GL account, the ALL-HISTORY monthly value series (from
``gl_analysis_common.build_account_monthly_series``) and decomposes it ADDITIVELY
into trend + seasonal + residual, then attaches a per-point z-score on the residual
so the client can re-threshold with a σ slider **without a round-trip**.  The
backend NEVER decides which months are "off-season" — it returns the full series +
per-point ``trend``/``seasonal_index``/``expected``/``residual``/``z``, the 12
seasonal factors, and ``stats``; the client flags ``|z| >= σ`` live in the browser.

================================================================================
FINANCIAL LOGIC (CLAUDE.md rule #1)  —  additive seasonal decomposition
================================================================================
For one account let ``x = [x_1, …, x_n]`` be its presented monthly values (kEUR,
sign convention owned by ``gl_analysis_common``: PL revenue +, BS assets +) ordered
chronologically by ``(fiscal_year, fiscal_period)``:

  1. trend_t      = rolling mean over a CENTERED 12-month window
                    ``pandas.Series(x).rolling(12, center=True, min_periods=6).mean()``
                    → NaN only where the centered window holds < 6 valid points.  For a
                    typical multi-year series this is defined everywhere; it bites only
                    very short series (≤ 5 points) or interior gaps.
  2. detrended_t  = x_t − trend_t            (NaN wherever trend is NaN)
  3. seasonal_index[m] = mean over all YEARS of ``detrended_t`` whose calendar month
                    is ``m`` (m = 1..12, NaNs ignored), THEN normalised so the 12
                    indices sum to 0:  ``s[m] = raw[m] − mean(raw[1..12])``.
                    A month with no finite detrended sample → raw 0 before centering.
  4. expected_t   = trend_t + seasonal_index[month(t)]   (NaN where trend is NaN)
  5. residual_t   = x_t − expected_t          (NaN where expected is NaN)
     resid_std    = numpy.nanstd(residual, ddof=1)   (sample std over finite residuals)
     z_t          = residual_t / resid_std            (None where resid_std == 0,
                    or where residual_t is NaN — i.e. the NaN-trend series ends)
  6. off-season  ⇔ |z_t| >= σ                  (σ = slider, CLIENT-side; default 1.0)

ADDITIVE (not multiplicative) is deliberate: GL account values can be **zero or
negative** (e.g. a cost line presented negative, a contra account), so a ratio
``actual / trend`` is undefined / sign-unstable.  An additive model ``actual =
trend + seasonal + residual`` is well-defined for any sign.

Normalising the seasonal indices to sum 0 means the seasonal component contributes
nothing on average over a full year — the trend carries the level, the seasonal
indices only redistribute within the year.  ``expected`` therefore reconciles:
``Σ_t expected_t ≈ Σ_t trend_t`` over any whole number of years.

================================================================================
WHY THE RESIDUAL z FLAGS BOTH "missing peak" AND "unexpected spike"
================================================================================
``z`` measures how far the ACTUAL month sits from what trend+season predict:

  * A normally-strong December (seasonal_index[12] large +) that comes in FLAT has a
    large NEGATIVE residual → ``z`` very negative → flagged: the expected peak is
    missing.
  * A random mid-year month that spikes far above trend+season has a large POSITIVE
    residual → ``z`` very positive → flagged: an unexpected spike.

Both are off-season events; ``|z|`` is sign-agnostic so the slider catches each.

================================================================================
INSUFFICIENT HISTORY
================================================================================
A reliable seasonal index needs at least ``min_years`` (default 2) of history so
each calendar month is observed in more than one year.  Accounts with fewer
distinct fiscal years are still returned (so the client can list them), but with
``insufficient_history: true``, all 12 ``seasonal_index`` = 0, ``expected`` = trend,
and ``z`` is reported but the UI surfaces an "insufficient history" state instead of
off-season flags.

================================================================================
GRAIN / WEEK-GRAIN NOTE  (plan M4)
================================================================================
Seasonality ALWAYS operates on the MONTHLY series, irrespective of the page's
``period_grain``.  The ``period`` argument only *labels the view* (and resolves a
week to its anchor month for material bounding); there is no "weekly seasonality"
here — a calendar-month seasonal pattern is the whole point.
``build_account_monthly_series`` returns the full month history; the σ-slider then
re-thresholds the residual z on those months.
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.services.fin_compat_sql import period_label, plan_anchor_for_week
from app.services.gl_analysis_common import (
    AccountSeries,
    bound_material_accounts,
    build_account_monthly_series,
)

ALGORITHM_VERSION = "gl_seasonality_v1"

#: Slider bounds + default (client re-thresholds; server only advertises them).
SIGMA_DEFAULT = 1.0
SIGMA_MIN = 0.5
SIGMA_MAX = 3.0

#: Minimum distinct fiscal years of history for a trustworthy seasonal index.
MIN_YEARS = 2

#: Booking-density gate defaults (see ``booking_density_ok``).  A seasonal pattern
#: is only meaningful for an account that is actually booked in most months of most
#: of its years; sparse / punctual accounts (e.g. one booking in 3 years) are
#: EXCLUDED from the seasonality tree and surface instead in the outlier tree.
DENSITY_MIN_MONTHS = 8        # distinct booked months that make a YEAR "active"
DENSITY_MIN_YEAR_RATIO = 0.5  # fraction of years that must be active
DENSITY_MIN_YEARS = 2         # distinct fiscal years of history required

#: Centered rolling-window length (months) and the minimum non-NaN observations
#: required inside the window for the trend to be defined at a point.
TREND_WINDOW = 12
TREND_MIN_PERIODS = 6

#: Cap the number of accounts returned (after material bounding).
MAX_ACCOUNTS = 200

_ROUND = 4


def _round_or_none(v: Optional[float], digits: int = _ROUND) -> Optional[float]:
    """Round, mapping NaN/inf/None → None (JSON-safe; the client treats None as a gap)."""
    if v is None:
        return None
    fv = float(v)
    if not np.isfinite(fv):
        return None
    return round(fv, digits)


def compute_seasonal_decomposition(
    values: list[float],
    months: list[int],
    *,
    window: int = TREND_WINDOW,
    min_periods: int = TREND_MIN_PERIODS,
) -> dict[str, Any]:
    """Additive trend/seasonal/residual decomposition of a monthly series (pure).

    Args:
        values: chronologically ordered presented values (kEUR).
        months: parallel list of calendar months (1..12) for each value.
        window/min_periods: centered rolling-trend parameters.

    Returns a dict with parallel lists ``trend``/``seasonal_index``/``expected``/
    ``residual``/``z`` (floats or ``None`` for NaN gaps), the 12-entry
    ``month_index`` (``{month, seasonal_index}``, summing to 0), and
    ``stats:{resid_std_keur, n}`` where ``n`` is the count of finite residuals.

    No DB.  Never raises on short / flat / NaN-heavy input — NaNs become ``None``.
    """
    n = len(values)
    if n != len(months):
        raise ValueError("values and months must be the same length")

    # 12-entry seasonal vector (index 0 unused; months are 1..12) so the empty /
    # single-point branches share one return shape.
    def _flat_month_index() -> list[dict[str, Any]]:
        return [{"month": m, "seasonal_index": 0.0} for m in range(1, 13)]

    if n == 0:
        return {
            "trend": [], "seasonal_index": [], "expected": [],
            "residual": [], "z": [],
            "month_index": _flat_month_index(),
            "stats": {"resid_std_keur": 0.0, "n": 0},
        }

    x = pd.Series(np.asarray(values, dtype=float))

    # 1. centered rolling-mean trend (NaN at the ends where < min_periods present).
    trend = x.rolling(window=window, center=True, min_periods=min_periods).mean()

    # 2. detrended = actual − trend (NaN where trend is NaN).
    detrended = x - trend

    # 3. seasonal_index[m] = mean over years of detrended for calendar month m,
    #    then normalise so the 12 indices sum to 0.
    month_arr = np.asarray(months, dtype=int)
    raw_index = np.zeros(12, dtype=float)          # raw[i] for month i+1
    det = detrended.to_numpy()
    for i, m in enumerate(range(1, 13)):
        mask = (month_arr == m) & np.isfinite(det)
        raw_index[i] = float(np.mean(det[mask])) if mask.any() else 0.0
    seasonal_vec = raw_index - float(np.mean(raw_index))   # centre to sum 0

    # month → seasonal index lookup (1..12).
    season_by_month = {m: seasonal_vec[m - 1] for m in range(1, 13)}

    # 4. expected = trend + seasonal_index[month] (NaN where trend is NaN).
    expected = trend.to_numpy() + np.asarray(
        [season_by_month[int(m)] for m in month_arr], dtype=float
    )

    # 5. residual = actual − expected; resid_std over finite residuals; z.
    residual = x.to_numpy() - expected
    finite = np.isfinite(residual)
    n_resid = int(finite.sum())
    if n_resid >= 2:
        resid_std = float(np.nanstd(residual[finite], ddof=1))
    else:
        resid_std = 0.0
    if not np.isfinite(resid_std):
        resid_std = 0.0

    if resid_std > 0:
        z_arr = residual / resid_std
    else:
        z_arr = np.full(n, np.nan)            # flat residual → no spread → no flags

    trend_np = trend.to_numpy()
    month_index = [
        {"month": m, "seasonal_index": round(float(season_by_month[m]), _ROUND)}
        for m in range(1, 13)
    ]

    return {
        "trend": [_round_or_none(v) for v in trend_np],
        "seasonal_index": [
            round(float(season_by_month[int(m)]), _ROUND) for m in month_arr
        ],
        "expected": [_round_or_none(v) for v in expected],
        "residual": [_round_or_none(v) for v in residual],
        "z": [_round_or_none(v) for v in z_arr],
        "month_index": month_index,
        "stats": {"resid_std_keur": round(resid_std, _ROUND), "n": n_resid},
    }


def _series_seasonality_payload(
    series: list[Any], *, min_years: int = MIN_YEARS,
) -> dict[str, Any]:
    """Seasonality payload for ANY ``MonthPoint``-like series (account OR aggregated).

    ``series`` is any list of objects exposing ``fiscal_year`` / ``fiscal_period`` /
    ``period_key`` / ``label`` / ``value_keur`` (``MonthPoint`` or an aggregated
    ``AggSeries`` node's ``series``).  Returns::

        {"insufficient_history": bool,
         "series": [{period_key, label, fiscal_period, actual_keur, trend_keur,
                     seasonal_index, expected_keur, residual_keur, z}, ...],
         "month_index": [{month, seasonal_index}, ...],   # 12 entries, sum 0
         "stats": {resid_std_keur, n}}

    Pure (DB-free).  Reuses :func:`compute_seasonal_decomposition` and the EXACT
    insufficient-history fallback (< ``min_years`` distinct fiscal years → flat
    seasonal indices, expected == trend) so L3/L4 aggregates and accounts share the
    same decomposition as :func:`build_seasonality`.
    """
    distinct_years = len({p.fiscal_year for p in series})
    insufficient = distinct_years < min_years

    values = [p.value_keur for p in series]
    months = [p.fiscal_period for p in series]
    if insufficient:
        decomp = _insufficient_decomp(values)
    else:
        decomp = compute_seasonal_decomposition(values, months)

    points: list[dict[str, Any]] = []
    for i, p in enumerate(series):
        points.append({
            "period_key": p.period_key,
            "label": p.label,
            "fiscal_period": p.fiscal_period,
            "actual_keur": p.value_keur,
            "trend_keur": decomp["trend"][i],
            "seasonal_index": decomp["seasonal_index"][i],
            "expected_keur": decomp["expected"][i],
            "residual_keur": decomp["residual"][i],
            "z": decomp["z"][i],
        })
    return {
        "insufficient_history": insufficient,
        "series": points,
        "month_index": decomp["month_index"],
        "stats": decomp["stats"],
    }


def _insufficient_decomp(values: list[float]) -> dict[str, Any]:
    """The < ``min_years`` decomposition (flat seasonal indices, expected == trend).

    Factored out of :func:`_insufficient_payload` so both the account serialiser and
    the generic :func:`_series_seasonality_payload` share ONE fallback.  ``values``
    is the chronologically ordered presented series (kEUR).
    """
    n = len(values)
    if n == 0:
        return compute_seasonal_decomposition([], [])
    x = pd.Series(np.asarray(values, dtype=float))
    trend = x.rolling(
        window=TREND_WINDOW, center=True, min_periods=TREND_MIN_PERIODS
    ).mean()
    trend_np = trend.to_numpy()
    residual = x.to_numpy() - trend_np
    finite = np.isfinite(residual)
    n_resid = int(finite.sum())
    resid_std = (
        float(np.nanstd(residual[finite], ddof=1)) if n_resid >= 2 else 0.0
    )
    if not np.isfinite(resid_std):
        resid_std = 0.0
    z_arr = residual / resid_std if resid_std > 0 else np.full(n, np.nan)
    return {
        "trend": [_round_or_none(v) for v in trend_np],
        "seasonal_index": [0.0] * n,
        "expected": [_round_or_none(v) for v in trend_np],   # expected == trend
        "residual": [_round_or_none(v) for v in residual],
        "z": [_round_or_none(v) for v in z_arr],
        "month_index": [
            {"month": m, "seasonal_index": 0.0} for m in range(1, 13)
        ],
        "stats": {"resid_std_keur": round(resid_std, _ROUND), "n": n_resid},
    }


def booking_density_ok(
    series: list[Any],
    *,
    min_months: int = DENSITY_MIN_MONTHS,
    min_year_ratio: float = DENSITY_MIN_YEAR_RATIO,
    min_years: int = DENSITY_MIN_YEARS,
) -> bool:
    """Does this series have enough bookings to show a REAL seasonal pattern? (pure).

    ================================================================================
    RULE (CLAUDE.md rule #1)  —  booking-density gate
    ================================================================================
    ``series`` is any list of ``MonthPoint``-like objects (``fiscal_year`` /
    ``fiscal_period`` / ``value_keur``).  A month counts as **booked** when it is
    present in the series with a non-zero value (the series only carries months that
    have bookings; a zero value means a booking netted to 0 and is treated as NOT a
    real booking month).  Group booked months by fiscal year and let, for each year,
    ``active_months(year)`` = the count of DISTINCT booked calendar months.  Then::

        active_year(y)  ⇔  active_months(y) >= min_months        (default 8 of 12)
        ratio           =  (# active years) / (# distinct years)
        density_ok      ⇔  (# distinct years) >= min_years        (default 2)
                           AND ratio >= min_year_ratio            (default 0.5)

    Both conditions must hold: at least ``min_years`` of history AND, in at least
    ``min_year_ratio`` of those years, the account was active in ``min_months``
    distinct months.  Accounts that fail are EXCLUDED from the seasonality tree
    (they remain in the outlier tree, which material-bounds the same accounts).

    Worked example (default 8/12, ratio 0.5, 2 years):
      * 3 years active in 9 / 10 / 2 months → years active = 2 (the 9- and 10-month
        years; the 2-month year is not) → ratio 2/3 ≈ 0.67 ≥ 0.5 AND 3 ≥ 2 → **OK**.
      * 1 booked month in EACH of 3 years → 0 active years → ratio 0/3 = 0 < 0.5
        → **FAILS** (sparse / punctual: excluded from seasonality).
      * 1 year active in 12 months → only 1 distinct year < 2 → **FAILS** (the
        seasonal index needs ≥ 2 years to compare a month across years).

    Edge: an empty series → 0 years < min_years → False.  ``min_year_ratio`` <= 0 is
    still gated by ``min_years`` (you cannot pass with zero years).
    """
    booked_by_year: dict[int, set[int]] = {}
    for p in series:
        try:
            v = float(p.value_keur)
        except (TypeError, ValueError):
            v = 0.0
        if v == 0.0 or not np.isfinite(v):
            continue                      # not a real booking month
        booked_by_year.setdefault(int(p.fiscal_year), set()).add(int(p.fiscal_period))

    distinct_years = len(booked_by_year)
    if distinct_years < min_years:
        return False
    active_years = sum(
        1 for months in booked_by_year.values() if len(months) >= min_months
    )
    return (active_years / distinct_years) >= min_year_ratio


def insufficient_booking_density(
    series: list[Any],
    *,
    min_months: int = DENSITY_MIN_MONTHS,
    min_year_ratio: float = DENSITY_MIN_YEAR_RATIO,
    min_years: int = DENSITY_MIN_YEARS,
) -> bool:
    """Convenience negation of :func:`booking_density_ok` (pure).

    ``True`` ⇔ the account is too sparsely / punctually booked to carry a meaningful
    seasonal pattern and should be excluded from the seasonality tree.
    """
    return not booking_density_ok(
        series,
        min_months=min_months,
        min_year_ratio=min_year_ratio,
        min_years=min_years,
    )


def _distinct_years(acc: AccountSeries) -> int:
    """Count distinct fiscal years present in the account's series."""
    return len({p.fiscal_year for p in acc.series})


def _insufficient_payload(acc: AccountSeries) -> dict[str, Any]:
    """Decomposition payload for an account with < MIN_YEARS of history.

    Seasonal indices are all 0 (no trustworthy pattern), ``expected`` falls back to
    the bare trend, and ``z`` is still reported (residual = actual − trend) so the
    chart degrades gracefully — but ``insufficient_history`` tells the UI to show the
    "insufficient history" state instead of off-season flags.
    """
    return _insufficient_decomp(acc.values())


def _account_payload(acc: AccountSeries, *, min_years: int) -> dict[str, Any]:
    """Serialise one account: full series + per-point decomposition + 12 factors."""
    insufficient = _distinct_years(acc) < min_years
    if insufficient:
        decomp = _insufficient_payload(acc)
    else:
        decomp = compute_seasonal_decomposition(
            acc.values(), [p.fiscal_period for p in acc.series]
        )

    points: list[dict[str, Any]] = []
    for i, p in enumerate(acc.series):
        points.append({
            "period_key": p.period_key,
            "label": p.label,
            "fiscal_period": p.fiscal_period,
            "actual_keur": p.value_keur,
            "trend_keur": decomp["trend"][i],
            "seasonal_index": decomp["seasonal_index"][i],
            "expected_keur": decomp["expected"][i],
            "residual_keur": decomp["residual"][i],
            "z": decomp["z"][i],
        })

    statement = (
        "pl" if acc.level_0 == "PL"
        else ("bs" if acc.level_0 == "BS" else acc.level_0)
    )
    return {
        "gl_account_id": acc.gl_account_id,
        "account_name": acc.account_name,
        "statement": statement,
        "level_2": acc.level_2,
        "level_3": acc.level_3,
        "entity_prefix": acc.entity_prefix,
        "insufficient_history": insufficient,
        "series": points,
        "month_index": decomp["month_index"],
        "stats": decomp["stats"],
    }


def _resolve_anchor(
    period: dict[str, Any],
) -> tuple[Optional[int], Optional[int], str]:
    """Resolve ``period`` → (anchor_year, anchor_month, label) for bounding+view.

    Week grain resolves to the ISO week's anchor month (its Thursday's month) so
    material bounding uses a real month; the monthly series is unaffected.
    """
    grain = period.get("grain", "month")
    if grain == "week":
        iy, iw = period.get("iso_year"), period.get("iso_week")
        if iy is not None and iw is not None:
            ay, am = plan_anchor_for_week(int(iy), int(iw))
            return ay, am, f"CW{iw}'{str(iy)[-2:]}"
        return None, None, "week"
    year, month = period.get("year"), period.get("month")
    if grain == "year" and year is not None:
        return int(year), 12, f"FY{year}"
    if year is not None and month is not None:
        return int(year), int(month), period_label(int(year), int(month))
    return year, month, ""


def build_seasonality(
    session: Session,
    period: dict[str, Any],
    entity: Optional[str],
    statement: str = "all",
    *,
    min_years: int = MIN_YEARS,
) -> dict[str, Any]:
    """Build the seasonality payload for ``period`` / ``entity`` / ``statement``.

    ``statement`` ∈ {'all','pl','bs'} filters by ``level_0`` ('all' = both).  The
    returned ``accounts`` carry the FULL monthly series with per-point
    ``trend_keur``/``seasonal_index``/``expected_keur``/``residual_keur``/``z``, the
    12-entry ``month_index`` (factors summing to 0) and ``stats``; the σ threshold is
    applied CLIENT-side (``|z| >= σ``).  Accounts with < ``min_years`` history are
    returned with ``insufficient_history: true`` (flat indices, expected == trend).

    PURE READ — builds nothing, writes nothing.
    """
    stmt = (statement or "all").strip().lower()
    if stmt not in ("all", "pl", "bs"):
        raise ValueError(f"Unsupported statement '{statement}' (expected all|pl|bs)")
    level_0 = {"pl": "PL", "bs": "BS"}.get(stmt)  # None for 'all'

    ay, am, label = _resolve_anchor(period)
    ent = entity
    if ent and str(ent).strip().lower() in ("", "all"):
        ent = None

    accounts = build_account_monthly_series(session, entity=ent, level_0=level_0)

    # Bound to material accounts at the anchor month (when known); otherwise keep
    # all (still capped).  The monthly series itself is always all-history.
    if ay is not None and am is not None:
        accounts = bound_material_accounts(
            accounts, current_year=ay, current_period=am, top_n=MAX_ACCOUNTS,
        )
    else:
        accounts = accounts[:MAX_ACCOUNTS]

    payload_accounts = [_account_payload(a, min_years=min_years) for a in accounts]

    return {
        "period": {
            "grain": period.get("grain", "month"),
            "year": period.get("year"),
            "month": period.get("month"),
            "iso_year": period.get("iso_year"),
            "iso_week": period.get("iso_week"),
            "label": label,
        },
        "entity": ent or "all",
        "statement": stmt,
        "accounts": payload_accounts,
        "meta": {
            "sigma_default": SIGMA_DEFAULT,
            "sigma_min": SIGMA_MIN,
            "sigma_max": SIGMA_MAX,
            "min_years": min_years,
            "algorithm_version": ALGORITHM_VERSION,
        },
    }
