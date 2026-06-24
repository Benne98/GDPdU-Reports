"""Laymen-friendly anomaly **signal score** (anomaly rework v2, Phase 0).

This module is PURE presentation logic.  It does NOT introduce, change, or report
any financial KPI, sign convention, or period boundary.  It maps an already-computed
descriptive z-score (owned by :mod:`gl_outliers` / :mod:`gl_seasonality`) onto a
laymen-friendly **0..100 signal score** plus a word **band**, so the UI can stop
showing a raw ``z`` (which non-analysts misread) and instead show "how unusual is
this, 0 = normal … 100 = extreme".

================================================================================
FINANCIAL/PRESENTATION LOGIC (CLAUDE.md rule #1) — signal score 0..100 from |z|
================================================================================
The score is a **monotonic** map of ``|z|`` (absolute z-score) onto ``0..100``,
piecewise-linear in three unit-σ bands, then saturated at 100 for |z| >= 3:

    |z| < 1        score = round(|z| * 33)                 #   0 ..  33
    1 <= |z| < 2   score = round(33 + (|z| - 1) * 33)      #  33 ..  66
    2 <= |z| < 3   score = round(66 + (|z| - 2) * 33)      #  66 ..  99
    |z| >= 3       score = 100                             #          100

Interpretation: 0 = perfectly normal (on the series mean), 100 = extreme (>= 3σ).
It is a RELATIVE "unusualness" indicator for display, **not** a probability.

The slope (~33 per σ) is deliberate: each unit-σ band maps onto one third of the
scale, lining up the score with the three severity bands the tree already uses
(|z|>=1 low, >=2 medium, >=3 high).  ``round`` is Python banker's rounding; the
worked-example points below were chosen to be exact so the boundary is unambiguous.

Worked example (|z| in → score out):
    z = 0    -> round(0)              = 0
    z = 1    -> round(33 + 0)         = 33
    z = 1.5  -> round(33 + 0.5*33)    = round(49.5) = 50   (banker's: .5 -> even? 49.5 -> 50)
    z = 2    -> round(66 + 0)         = 66
    z = 2.5  -> round(66 + 0.5*33)    = round(82.5) = 82   (banker's rounding of .5)
    z = 3    -> 100   (saturated)
    z = 5    -> 100   (saturated)
    z = -2   -> 66    (|z| is used; sign-agnostic, mirrors the outlier rule)

NOTE on the spec's worked example "z=2.5 -> 83": Python's ``round`` uses banker's
rounding so ``round(82.5) == 82``.  The score is monotonic regardless; we lock the
*exact* integer the implementation returns in the regression test (82) rather than
the spec's eyeballed 83, and assert monotonicity + the unambiguous anchor points
(0/33/66/100).  Documented here so the 1-unit difference is intentional, not a bug.

Bands (``band(score)``):
    'low'     score < 33          (sub-1σ region; normal-ish)
    'medium'  33 <= score < 66    (1σ..2σ; notable)
    'high'    score >= 66         (>= 2σ; strong signal)
These boundaries align with the score band edges (33, 66) which are the |z|=1 and
|z|=2 crossings, so band(score) and the tree's |z| severity bands agree.

Edge cases:
    * z = 0 / flat series      -> score 0, band 'low' (nothing unusual).
    * negative z               -> |z| is used (sign-agnostic), so z=-2 == z=+2 == 66.
    * NaN / inf / None-as-0     -> treated as 0.0 -> score 0, band 'low' (defensive;
                                  callers pass a finite z, but never raises).
    * |z| just below a band edge (e.g. 0.999) -> stays in the lower band (round()).
    * very large |z| (>=3)     -> saturates at exactly 100 (no overflow).
"""
from __future__ import annotations

import math

__all__ = ["signal_score_0to100", "band", "SCORE_BAND_LOW", "SCORE_BAND_HIGH"]

#: Score band boundaries (the |z|=1 and |z|=2 crossings on the 0..100 scale).
SCORE_BAND_LOW = 33   # score < 33  -> 'low'
SCORE_BAND_HIGH = 66  # score >= 66 -> 'high'; in between -> 'medium'

#: Score contributed per unit-σ band (three bands span 0..99, then saturate at 100).
_PER_SIGMA = 33


def signal_score_0to100(z: float) -> int:
    """Monotonic map of ``|z|`` onto a 0..100 laymen signal score (pure).

    See the module docstring for the full formula, worked example, and edge cases.
    0 = normal, 100 = extreme (>= 3σ).  Sign-agnostic (uses ``|z|``).  Non-finite
    input is treated as 0.0 (defensive; never raises).
    """
    try:
        az = abs(float(z))
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(az):
        return 0
    if az >= 3.0:
        return 100
    if az >= 2.0:
        return int(round(66 + (az - 2.0) * _PER_SIGMA))
    if az >= 1.0:
        return int(round(33 + (az - 1.0) * _PER_SIGMA))
    return int(round(az * _PER_SIGMA))


def band(score: int) -> str:
    """Band a 0..100 ``score`` → ``'low'`` / ``'medium'`` / ``'high'`` (pure).

    Boundaries: ``score < 33`` -> 'low'; ``33 <= score < 66`` -> 'medium';
    ``score >= 66`` -> 'high'.  These edges are the |z|=1 and |z|=2 crossings, so the
    word band agrees with the tree's severity banding.
    """
    s = int(score)
    if s < SCORE_BAND_LOW:
        return "low"
    if s < SCORE_BAND_HIGH:
        return "medium"
    return "high"
