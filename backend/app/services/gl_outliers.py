"""GL outlier analysis (Journal Agent, Phase 1) — z-score over the monthly series.

Builds, per material GL account, the ALL-HISTORY monthly value series (from
``gl_analysis_common.build_account_monthly_series``) and attaches a per-point
mean-residual z-score so the client can re-threshold with a σ slider **without a
round-trip**.  The backend NEVER decides which points are "outliers" — it returns
the full series + per-point ``residual``/``z`` + ``stats`` (mean/std/n); the client
flags ``|z| >= σ`` live in the browser.

================================================================================
FINANCIAL LOGIC (CLAUDE.md rule #1)  —  z-score outlier on a monthly value series
================================================================================
For one account let ``x = [x_1, …, x_n]`` be its presented monthly values (kEUR,
sign convention owned by ``gl_analysis_common``: PL revenue +, BS assets +):

    mean        μ  = numpy.mean(x)
    std (sample) σ̂ = numpy.std(x, ddof=1)        # sample std, n-1 denominator
    residual_i     = x_i − μ
    z_i            = residual_i / σ̂              # 0 when σ̂ == 0 or n < 2
    outlier_i     ⇔ |z_i| >= σ                    # σ = slider (CLIENT-side)

Sample std (``ddof=1``) is used because the monthly series is a *sample* of the
account's behaviour, not the whole population; with one denominator fewer it is the
unbiased estimator and is what an analyst expects from "standard deviation".

Worked example — series ``[10, 12, 11, 13, 60]`` kEUR (one spike):
    μ  = (10+12+11+13+60)/5 = 21.2
    σ̂ = std([…], ddof=1)   ≈ 21.72  (population std would be ≈ 19.43 — different!)
    residual(60) = 60 − 21.2 = 38.8
    z(60)        = 38.8 / 21.72 ≈ 1.787
    → at σ = 1.0 the 60 IS flagged (1.787 ≥ 1); at σ = 2.0 it is NOT (1.787 < 2).
    The other four points have |z| ≤ |11−21.2|/21.72 ≈ 0.47, never flagged at σ≥1.

Edge cases:
    * n < 2          → σ̂ undefined → all z = 0 (nothing flags); stats.std = 0.0.
    * σ̂ == 0 (flat)  → every value equals μ → residual 0, z = 0 (nothing flags).
    * |z| == σ EXACTLY→ flagged (client uses ``>=``; boundary is inclusive).
    * empty ledger   → no accounts; ``accounts: []``.
    * sign flip      → handled upstream by the presented-amount sign rule; the
                       z-score is sign-agnostic (|z|), so a large negative swing
                       flags exactly like a large positive one.

================================================================================
GRAIN / WEEK-GRAIN NOTE  (plan M4)
================================================================================
Outliers ALWAYS operate on the MONTHLY series, irrespective of the page's
``period_grain``.  The ``period`` argument only *labels the view* (and resolves a
week to its anchor month for display); there is no such thing as a "weekly outlier"
here — a week has far too few points for a stable mean/σ.  ``build_account_monthly_series``
returns the full month history; the σ-slider then re-thresholds those months.
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
from sqlalchemy.orm import Session

from app.services.fin_compat_sql import period_label, plan_anchor_for_week
from app.services.gl_analysis_common import (
    AccountSeries,
    bound_material_accounts,
    build_account_monthly_series,
)

ALGORITHM_VERSION = "gl_outliers_v1"

#: Slider bounds + default (client re-thresholds; server only advertises them).
SIGMA_DEFAULT = 1.0
SIGMA_MIN = 0.5
SIGMA_MAX = 3.0

#: Cap the number of accounts returned (after material bounding) so the bounded
#: payload stays generous-but-finite.  Material bounding already drops noise.
MAX_ACCOUNTS = 200


def compute_series_stats(values: list[float]) -> dict[str, float]:
    """Return ``{mean_keur, std_keur, n}`` for a value series (pure, DB-free).

    ``std`` is the SAMPLE std (``ddof=1``); for ``n < 2`` it is reported as 0.0
    (undefined → treated as "no spread", so z collapses to 0 and nothing flags).
    """
    n = len(values)
    if n == 0:
        return {"mean_keur": 0.0, "std_keur": 0.0, "n": 0}
    arr = np.asarray(values, dtype=float)
    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1)) if n >= 2 else 0.0
    return {"mean_keur": round(mean, 4), "std_keur": round(std, 4), "n": n}


def compute_point_zscores(
    values: list[float], mean: float, std: float,
) -> list[tuple[float, float]]:
    """Per-point ``(residual, z)`` for ``values`` given ``mean``/``std`` (pure).

    ``residual_i = x_i − mean``; ``z_i = residual_i / std``, or 0.0 when
    ``std == 0`` (flat series) — never raises a divide-by-zero.
    """
    out: list[tuple[float, float]] = []
    spread = std if std > 0 else 0.0
    for x in values:
        residual = x - mean
        z = (residual / spread) if spread > 0 else 0.0
        out.append((round(residual, 4), round(z, 4)))
    return out


def compute_linear_regression(values: list[float]) -> dict[str, float]:
    """Ordinary least-squares trend over ``x = 0..n-1`` for a value series (pure).

    ================================================================================
    FINANCIAL/PRESENTATION LOGIC (CLAUDE.md rule #1) — least-squares trend line
    ================================================================================
    Fits ``y = slope * x + intercept`` over the index domain ``x = 0, 1, …, n-1``
    (one step per month — the series is already chronological).  Returns the slope,
    intercept, coefficient of determination ``r_squared`` and the two endpoints of
    the fitted line (``trend_start`` = ŷ at x=0 = intercept, ``trend_end`` = ŷ at
    x=n-1) so the client can draw the straight trend line with two points.

        slope     = Σ((x-x̄)(y-ȳ)) / Σ((x-x̄)²)        # numpy.polyfit deg 1
        intercept = ȳ − slope·x̄
        r_squared = 1 − SS_res/SS_tot                  # SS_tot = Σ(y-ȳ)²
        trend_start = intercept                        # ŷ at x=0
        trend_end   = slope·(n-1) + intercept          # ŷ at x=n-1

    This is a DESCRIPTIVE overlay for charting; it introduces no monetary formula
    (the values come from ``gl_analysis_common``, sign convention owned there).

    Worked example — perfect line ``[0, 10, 20, 30, 40]`` (slope 10):
        slope = 10.0, intercept = 0.0, r_squared = 1.0,
        trend_start = 0.0, trend_end = 10*4 + 0 = 40.0.

    Edge cases:
        * n < 2          → cannot fit a line → all zeros, ``r_squared = 0.0``.
        * SS_tot == 0    → flat series (all equal) → slope 0, intercept = the value,
                           ``r_squared = 0.0`` (no variance to explain; avoids 0/0).
        * negative slope → preserved (a declining series fits a negative slope).
    """
    n = len(values)
    zero = {
        "slope": 0.0, "intercept": 0.0, "r_squared": 0.0,
        "trend_start": 0.0, "trend_end": 0.0,
    }
    if n < 2:
        # n == 1: a single point has no slope; report the value as a flat line.
        if n == 1:
            v = round(float(values[0]), 4)
            return {
                "slope": 0.0, "intercept": v, "r_squared": 0.0,
                "trend_start": v, "trend_end": v,
            }
        return dict(zero)

    x = np.arange(n, dtype=float)
    y = np.asarray(values, dtype=float)
    slope, intercept = np.polyfit(x, y, 1)

    y_hat = slope * x + intercept
    ss_res = float(np.sum((y - y_hat) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = (1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0

    return {
        "slope": round(float(slope), 6),
        "intercept": round(float(intercept), 4),
        "r_squared": round(float(r_squared), 6),
        "trend_start": round(float(intercept), 4),
        "trend_end": round(float(slope * (n - 1) + intercept), 4),
    }


def compute_histogram(values: list[float], bins: int = 10) -> list[dict[str, Any]]:
    """Equal-width histogram over ``[min, max]`` (pure, DB-free).

    ================================================================================
    FINANCIAL/PRESENTATION LOGIC (CLAUDE.md rule #1) — equal-width value histogram
    ================================================================================
    Splits the value range ``[min, max]`` into ``bins`` equal-width buckets and
    counts how many monthly values fall in each (right-edge inclusive on the LAST
    bin so the max is counted).  Returns ``[{bin_lo, bin_hi, count}, …]`` with
    ``Σ count == n``.

    Worked example — ``[0, 1, 2, …, 9]``, bins=10 → 10 unit-wide bins each count 1,
    Σ count = 10.

    Edge cases:
        * n < 2 OR all values equal (range 0) → ONE degenerate bin
          ``{bin_lo=v, bin_hi=v, count=n}`` (can't make equal-width bins of zero
          range; avoids division by zero).
        * empty → ``[]``.
        * ``Σ count`` always equals ``n`` (locked by test).
    """
    n = len(values)
    if n == 0:
        return []
    arr = np.asarray(values, dtype=float)
    lo = float(np.min(arr))
    hi = float(np.max(arr))
    nb = max(1, int(bins))

    # Degenerate: no spread (all equal) or too few points → one bin holding all.
    if n < 2 or hi <= lo:
        return [{"bin_lo": round(lo, 4), "bin_hi": round(hi, 4), "count": n}]

    counts, edges = np.histogram(arr, bins=nb, range=(lo, hi))
    out: list[dict[str, Any]] = []
    for i in range(len(counts)):
        out.append({
            "bin_lo": round(float(edges[i]), 4),
            "bin_hi": round(float(edges[i + 1]), 4),
            "count": int(counts[i]),
        })
    return out


def distribution_stats(values: list[float]) -> dict[str, float]:
    """Five-number-ish summary ``{min,max,median,q1,q3,iqr}`` (pure, DB-free).

    ================================================================================
    FINANCIAL/PRESENTATION LOGIC (CLAUDE.md rule #1) — distribution percentiles
    ================================================================================
    Uses numpy's linear-interpolation percentiles (the default ``method='linear'``):

        q1  = percentile(x, 25);  median = percentile(x, 50);  q3 = percentile(x, 75)
        iqr = q3 − q1

    Skew / kurtosis are intentionally OMITTED: ``scipy`` is not a backend dependency
    (verified — no scipy import anywhere in backend), and we will not add a heavy dep
    for a presentation stat.  numpy covers min/max/median/quartiles/IQR.

    Worked example — ``[1, 2, 3, 4, 5]``:
        min 1, max 5, median 3, q1 2, q3 4, iqr 2.

    Edge cases:
        * empty → all 0.0.
        * single value → min=max=median=q1=q3=v, iqr 0.
    """
    n = len(values)
    if n == 0:
        return {"min": 0.0, "max": 0.0, "median": 0.0, "q1": 0.0, "q3": 0.0, "iqr": 0.0}
    arr = np.asarray(values, dtype=float)
    q1 = float(np.percentile(arr, 25))
    q3 = float(np.percentile(arr, 75))
    return {
        "min": round(float(np.min(arr)), 4),
        "max": round(float(np.max(arr)), 4),
        "median": round(float(np.percentile(arr, 50)), 4),
        "q1": round(q1, 4),
        "q3": round(q3, 4),
        "iqr": round(q3 - q1, 4),
    }


def _series_outlier_payload(series: list[Any]) -> dict[str, Any]:
    """Outlier payload for ANY ``MonthPoint``-like series (account OR aggregated).

    ``series`` is any list of objects exposing ``period_key`` / ``label`` /
    ``value_keur`` (``MonthPoint`` from ``gl_analysis_common`` or an aggregated
    ``AggSeries`` node's ``series``).  Returns::

        {"series": [{period_key, label, value_keur, residual_keur, z}, ...],
         "stats":  {mean_keur, std_keur, n}}

    Pure (DB-free); reuses :func:`compute_series_stats` /
    :func:`compute_point_zscores` so L3/L4 aggregates and accounts share the EXACT
    same z-score math as :func:`build_outliers`.
    """
    values = [p.value_keur for p in series]
    stats = compute_series_stats(values)
    rz = compute_point_zscores(values, stats["mean_keur"], stats["std_keur"])

    points: list[dict[str, Any]] = []
    for p, (residual, z) in zip(series, rz):
        points.append({
            "period_key": p.period_key,
            "label": p.label,
            "value_keur": p.value_keur,
            "residual_keur": residual,
            "z": z,
        })
    return {"series": points, "stats": stats}


def _account_payload(acc: AccountSeries) -> dict[str, Any]:
    """Serialise one account: full series + per-point residual/z + stats."""
    body = _series_outlier_payload(acc.series)
    points = body["series"]
    stats = body["stats"]

    statement = "pl" if acc.level_0 == "PL" else ("bs" if acc.level_0 == "BS" else acc.level_0)
    return {
        "gl_account_id": acc.gl_account_id,
        "account_name": acc.account_name,
        "statement": statement,
        "level_2": acc.level_2,
        "level_3": acc.level_3,
        "entity_prefix": acc.entity_prefix,
        "series": points,
        "stats": stats,
    }


def _resolve_anchor(period: dict[str, Any]) -> tuple[Optional[int], Optional[int], str]:
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


def build_outliers(
    session: Session,
    period: dict[str, Any],
    entity: Optional[str],
    statement: str = "all",
) -> dict[str, Any]:
    """Build the outlier payload for ``period`` / ``entity`` / ``statement``.

    ``statement`` ∈ {'all','pl','bs'} filters by ``level_0`` ('all' = both).  The
    returned ``accounts`` carry the FULL monthly series with per-point
    ``residual_keur``/``z`` and ``stats``; the σ threshold is applied CLIENT-side.

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

    payload_accounts = [_account_payload(a) for a in accounts]

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
            "algorithm_version": ALGORITHM_VERSION,
        },
    }
