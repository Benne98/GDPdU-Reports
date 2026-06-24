"""Finssentials budget heuristics — per-position suggestions with explanations.

This module backs the "Finssentials heuristics" suggestion feature of the budget
grid (Phase 2).  For each position it produces a SUGGESTION the user can review,
``Apply`` (materialise via the seed path) and then adjust.  Three heuristics:

  * ``prior_year`` (PRIMARY): last COMPLETE FY actual × (1 + growth), seasonalised
    by that FY's profile.  Default growth +0%.
  * ``trend_cagr``: project the last COMPLETE FY by the CAGR over the last N
    COMPLETE FYs.
  * ``run_rate``: annualise the last K observed months (a run-rate, NOT a base).

COMPLETE-FY RULE (no partial/YTD year as a base or in the CAGR window)
  A fiscal year is COMPLETE iff it is strictly BEFORE the current (latest-anchor)
  fiscal year — equivalently, all 12 monthly buckets are present.  The bounded pull
  reaches the FYs strictly before the PLAN year, but the most-recent of those
  (plan_year-1) may still be the in-progress year when the ledger is mid-close
  (e.g. FY2026 plan, fy_hi=2025, data only Jan..Jul 2025); summing it over 12
  periods gives a PARTIAL "annual".  ``prior_year`` and ``trend_cagr`` therefore
  DROP any ``fy >= current_fy`` (``_complete_actuals``); ``run_rate`` keeps the full
  map by design (it annualises the latest months, partial year included).

All three return PRESENTED values (the grid presents) and reuse the existing
``budget_service`` profile helpers for the base annual + seasonal weights, so the
suggestion shares the SAME sign convention and seasonality as the seed.

================================================================ DATA / BOUNDING
The pure heuristics operate on an actuals map ``{fiscal_year: {period(1..12):
presented_value}}`` plus the latest-FY seasonal ``weights``.  The single DB pull
(:func:`_position_actuals_by_fy`) fetches at most the last ``max_years`` full
fiscal years strictly BEFORE ``fiscal_year`` (the plan year), grouped by
(fiscal_year, fiscal_period) for the position's level_3 — bounded, one query.

Every result is ``{"suggestion_annual": float, "weights": {period: float},
"explanation": {...}}``.  ``weights`` always sum to 1 (uniform 1/12 fallback) so
``budget_service.seasonalize(suggestion_annual, weights)`` reconciles to the
annual.  No-history → ``suggestion_annual = 0.0`` + an explanation ``note``.

============================================================== (1) prior_year FORMULA
Let ``base`` = the last COMPLETE FY actual annual of the position (presented sign;
the partial in-progress year is excluded) and ``g`` the fractional growth
assumption (default 0.0):

    suggestion_annual = base * (1 + g)
    weights           = that base FY's seasonal profile (Σ = 1; uniform if flat)

  WORKED EXAMPLE (FY2026 plan; ledger anchor 2025/Jul → current_fy=2025)
    Complete FYs = {…, 2023, 2024}; 2025 is PARTIAL and dropped.
    base = FY2024 annual = 1000, g = 0.10 → suggestion = 1000 * 1.10 = 1100.0.
    weights = 2024 profile; seasonalize(1100, weights) sums to 1100.0.
    g = 0.0 (default) → suggestion = base = 1000.0.

  EDGE CASES
    * no COMPLETE FY      → 0.0 + note (the partial current year is never a base).
    * base = 0            → 0.0 (growth off a zero base is 0, not the rate).
    * negative base       → sign preserved: -1000 * 1.10 = -1100.0.

============================================================== (2) trend_cagr FORMULA
Over the last ``n`` COMPLETE FYs with annuals ``A_first … A_last`` (chronological;
the partial in-progress year is EXCLUDED from the window):

    cagr              = (A_last / A_first) ** (1 / (n - 1)) - 1
    suggestion_annual = A_last * (1 + cagr)
    weights           = last COMPLETE FY's seasonal profile

  WORKED EXAMPLE (FY2026 plan; current_fy=2025; complete FY2023/FY2024 + earlier)
    Window = complete FYs only.  Say FY2022=100, FY2023=110, FY2024=121 (n=3):
      cagr = (121 / 100) ** (1/2) - 1 = 1.21 ** 0.5 - 1 = 1.10 - 1 = 0.10  (+10%)
      suggestion = 121 * 1.10 = 133.1.
    The PARTIAL FY2025 (e.g. an annualised -63,433,637 from only Jan..Jul) is NEVER
    A_last — that was the bug: a partial year polluted base_annual and the CAGR.

  EDGE CASES
    * only 1 COMPLETE FY            → fall back to prior_year off that complete FY
                                      (CAGR needs 2; growth 0); note records it.
    * no COMPLETE FY               → prior_year path → 0.0 + note (graceful).
    * < 2 complete FYs              → fall back to prior_year (cannot compute a rate);
                                      note records the fallback.
    * A_first == 0                  → ratio undefined → cagr = 0 → suggestion = A_last
                                      (note "base year zero, CAGR undefined").
    * sign change first↔last (e.g.  → cagr undefined for a real exponent → cagr = 0 →
      A_first<0, A_last>0)             suggestion = A_last (note).
    * A_first, A_last both negative → ratio positive → real cagr; sign preserved.

============================================================== (3) run_rate FORMULA
Annualise the last ``k`` OBSERVED months (most recent months with actuals across
the pulled history, chronological), summing their presented values ``m_i``:

    run_rate_sum      = Σ_{i=1..k} m_i           (last k observed months)
    suggestion_annual = run_rate_sum * 12 / k
    weights           = last FY's seasonal profile (trailing if last FY incomplete)

  WORKED EXAMPLE
    last 3 observed months = 100, 120, 140  (k = 3)
      run_rate_sum = 360 ; suggestion = 360 * 12 / 3 = 1440.0.

  EDGE CASES
    * fewer than k observed months  → use however many exist (k' < k); note records k'.
    * no observed months            → 0.0 + note "no observed months".
    * negative months               → sign preserved (Σ may be negative).
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import budget_positions
from app.services.budget_service import (
    PERIODS,
    UNIFORM_WEIGHT,
    _EPS,
    stored_to_present,
)
from app.services.gl_analysis_common import latest_anchor

# Defaults (NOT baked into the formulas — caller-overridable, like plan_synth).
DEFAULT_GROWTH_PCT = 0.0
DEFAULT_CAGR_YEARS = 3
DEFAULT_RUN_RATE_MONTHS = 3
# How far back the single DB pull reaches (full FYs before the plan year).  Bounds
# the query and is an upper limit for the CAGR window.
MAX_HISTORY_YEARS = 6


# =========================================================================== #
# Bounded DB pull — multi-FY actuals for a position (one query).
# =========================================================================== #
def _position_actuals_by_fy(
    session: Session,
    *,
    line_code: str,
    statement: str,
    fiscal_year: int,
    entity_prefix: Optional[str],
    max_years: int = MAX_HISTORY_YEARS,
) -> dict[int, dict[int, float]]:
    """``{fiscal_year: {period(1..12): presented_value}}`` for the position's actuals.

    Aggregates GL movements for the position's level_3 (level_0 = statement) per
    (fiscal_year, fiscal_period), in the PRESENTED sign (mirror of
    ``budget_service._position_actual_profile`` but over MANY FYs in one query).
    Bounded to the ``max_years`` full FYs strictly BEFORE ``fiscal_year`` (the plan
    year), so it never reads the in-progress plan year and never scans all history.
    Returns ``{}`` when the position has no actuals in the window.
    """
    pos = budget_positions.position_for(line_code)
    level_3 = pos.level_3 if pos else None
    params: dict[str, Any] = {
        "stmt": statement,
        "fy_hi": int(fiscal_year) - 1,
        "fy_lo": int(fiscal_year) - int(max_years),
    }
    if level_3:
        params["l3"] = level_3
        l3_clause = "AND TRIM(a.level_3) = :l3"
    else:
        params["lc_l3"] = line_code
        l3_clause = "AND TRIM(a.level_3) = :lc_l3"
    ent_clause = ""
    if entity_prefix:
        params["ep"] = str(entity_prefix)[:2]
        ent_clause = "AND l.entity_prefix = :ep"

    sql = text(f"""
        SELECT e.fiscal_year AS fy, e.fiscal_period AS p, SUM(l.amount) AS amt
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE a.level_0 = :stmt
          AND e.fiscal_year BETWEEN :fy_lo AND :fy_hi
          AND e.fiscal_period BETWEEN 1 AND 12
          {l3_clause}
          {ent_clause}
        GROUP BY e.fiscal_year, e.fiscal_period
    """)
    out: dict[int, dict[int, float]] = {}
    for r in session.execute(sql, params).fetchall():
        fy = int(r[0])
        p = int(r[1])
        if 1 <= p <= 12:
            out.setdefault(fy, {q: 0.0 for q in PERIODS})[p] = stored_to_present(
                float(r[2] or 0.0), statement, line_code
            )
    return out


# =========================================================================== #
# Pure helpers over the actuals map.
# =========================================================================== #
def _annual(months: dict[int, float]) -> float:
    return float(sum(float(months.get(p, 0.0)) for p in PERIODS))


def _weights_of(months: dict[int, float]) -> dict[int, float]:
    """Seasonal profile of a month map (Σ = 1; uniform 1/12 when the annual is 0)."""
    annual = _annual(months)
    if abs(annual) < _EPS:
        return {p: UNIFORM_WEIGHT for p in PERIODS}
    return {p: float(months.get(p, 0.0)) / annual for p in PERIODS}


def _uniform_weights() -> dict[int, float]:
    return {p: UNIFORM_WEIGHT for p in PERIODS}


def _complete_actuals(
    actuals: dict[int, dict[int, float]],
    *,
    current_fy: Optional[int] = None,
) -> dict[int, dict[int, float]]:
    """Subset of ``actuals`` keeping ONLY COMPLETE fiscal years.

    A fiscal year is COMPLETE when it is strictly BEFORE the current (latest-anchor)
    fiscal year ``current_fy``.  The bounded DB pull
    (:func:`_position_actuals_by_fy`) reaches the FYs strictly before the PLAN year,
    but the most-recent of those (plan_year-1) may still be the in-progress year
    when the ledger is mid-close (e.g. for a FY2026 plan, fy_hi=2025, yet the data
    only runs Jan..Jul 2025).  Summing such a partial year over all 12 periods
    yields a PARTIAL "annual" that must NOT be used as a fiscal-year base or in the
    CAGR window — so any ``fy >= current_fy`` is dropped here.

    ``current_fy is None`` → NO filtering (the pure unit-test path passes a curated
    map of already-complete FYs).  Note we key completeness off the LEDGER anchor
    year, NOT the position's own values, so a real all-zero (but complete) FY is
    kept while the partial current year is dropped.
    """
    if current_fy is None:
        return dict(actuals)
    return {fy: months for fy, months in actuals.items() if int(fy) < int(current_fy)}


def _full_fys(actuals: dict[int, dict[int, float]]) -> list[int]:
    """Fiscal years present in the actuals map, chronological."""
    return sorted(actuals.keys())


def _observed_months_chrono(actuals: dict[int, dict[int, float]]) -> list[float]:
    """Flattened (fy, period)-ordered list of OBSERVED month values.

    A month is 'observed' when its FY appears in the pull; within an FY we keep the
    12 periods (0.0 for unbooked months that fall before the last booked one).  We
    only trail from the last booked month so a partially-closed last FY contributes
    just its real months.
    """
    series: list[float] = []
    for fy in _full_fys(actuals):
        months = actuals[fy]
        last_booked = max((p for p in PERIODS if abs(months.get(p, 0.0)) >= _EPS), default=0)
        for p in PERIODS:
            if p <= last_booked:
                series.append(float(months.get(p, 0.0)))
    return series


# =========================================================================== #
# (1) prior_year
# =========================================================================== #
def prior_year(
    actuals: dict[int, dict[int, float]],
    *,
    growth_pct: float = DEFAULT_GROWTH_PCT,
    current_fy: Optional[int] = None,
) -> dict[str, Any]:
    """PRIOR-YEAR heuristic: last COMPLETE FY actual × (1 + growth).  See module docstring.

    The base is the most recent COMPLETE fiscal year (strictly before
    ``current_fy``, the latest-anchor year); the partial in-progress year is
    excluded so the base is a full-year figure, never a year-to-date partial.
    """
    actuals = _complete_actuals(actuals, current_fy=current_fy)
    fys = _full_fys(actuals)
    if not fys:
        return {
            "suggestion_annual": 0.0,
            "weights": _uniform_weights(),
            "explanation": {
                "method": "prior_year",
                "base_fy": None,
                "base_annual": 0.0,
                "growth_pct": float(growth_pct),
                "season_source": None,
                "note": "no prior-year actuals; suggestion resolves to 0",
            },
        }
    base_fy = fys[-1]
    base_months = actuals[base_fy]
    base_annual = _annual(base_months)
    suggestion = base_annual * (1.0 + float(growth_pct))
    return {
        "suggestion_annual": float(suggestion),
        "weights": _weights_of(base_months),
        "explanation": {
            "method": "prior_year",
            "base_fy": base_fy,
            "base_annual": float(base_annual),
            "growth_pct": float(growth_pct),
            "season_source": base_fy,
        },
    }


# =========================================================================== #
# (2) trend_cagr
# =========================================================================== #
def trend_cagr(
    actuals: dict[int, dict[int, float]],
    *,
    years: int = DEFAULT_CAGR_YEARS,
    growth_pct: float = DEFAULT_GROWTH_PCT,
    current_fy: Optional[int] = None,
) -> dict[str, Any]:
    """TREND/CAGR heuristic: last FY projected by CAGR over the last N FYs.

    ``growth_pct`` is accepted for dispatcher uniformity but the CAGR drives the
    projection; falls back to :func:`prior_year` when fewer than 2 COMPLETE FYs
    exist.  The CAGR window uses ONLY complete fiscal years (the partial in-progress
    year is excluded), so ``first_annual``/``last_annual`` are full-year figures.
    See module docstring for the formula, worked example and edge cases.
    """
    actuals = _complete_actuals(actuals, current_fy=current_fy)
    fys = _full_fys(actuals)
    if len(fys) < 2:
        # actuals already filtered to complete FYs → prior_year must not re-filter.
        res = prior_year(actuals, growth_pct=growth_pct)
        res["explanation"]["method"] = "trend_cagr"
        res["explanation"]["note"] = "fewer than 2 complete FYs; fell back to prior_year"
        return res

    window = fys[-int(years):] if years and years >= 2 else fys
    if len(window) < 2:
        window = fys[-2:]
    first_fy, last_fy = window[0], window[-1]
    n = len(window)
    a_first = _annual(actuals[first_fy])
    a_last = _annual(actuals[last_fy])

    note: Optional[str] = None
    # Real exponent requires a strictly positive ratio.
    if abs(a_first) < _EPS:
        cagr = 0.0
        note = "first-year annual is zero; CAGR undefined → 0"
    elif (a_last / a_first) <= 0.0:
        cagr = 0.0
        note = "sign change between first and last FY; CAGR undefined → 0"
    else:
        cagr = (a_last / a_first) ** (1.0 / (n - 1)) - 1.0

    suggestion = a_last * (1.0 + cagr)
    explanation: dict[str, Any] = {
        "method": "trend_cagr",
        "base_fy": last_fy,
        "base_annual": float(a_last),
        "cagr": float(cagr),
        "window": [first_fy, last_fy],
        "window_years": n,
        "first_annual": float(a_first),
        "last_annual": float(a_last),
        "season_source": last_fy,
    }
    if note:
        explanation["note"] = note
    return {
        "suggestion_annual": float(suggestion),
        "weights": _weights_of(actuals[last_fy]),
        "explanation": explanation,
    }


# =========================================================================== #
# (3) run_rate
# =========================================================================== #
def run_rate(
    actuals: dict[int, dict[int, float]],
    *,
    months: int = DEFAULT_RUN_RATE_MONTHS,
    growth_pct: float = DEFAULT_GROWTH_PCT,
) -> dict[str, Any]:
    """RUN-RATE heuristic: annualise the last K observed months.

    Conceptually UNCHANGED: it deliberately uses the last OBSERVED months (which may
    include the partial, in-progress year) and annualises them.  The result is a
    RUN-RATE, NOT a full-year (complete-FY) base — it answers "if the latest months
    continued for a year, what would the annual be?", so it intentionally does NOT
    drop the partial year the way prior_year/trend_cagr do.  ``growth_pct`` is
    accepted for dispatcher uniformity (not applied).  See the module docstring for
    the formula, worked example and edge cases.
    """
    fys = _full_fys(actuals)
    series = _observed_months_chrono(actuals)
    k = int(months)
    if not series or k <= 0:
        return {
            "suggestion_annual": 0.0,
            "weights": _uniform_weights() if not fys else _weights_of(actuals[fys[-1]]),
            "explanation": {
                "method": "run_rate",
                "base_fy": fys[-1] if fys else None,
                "window_months": k,
                "observed_months": 0,
                "run_rate_sum": 0.0,
                "season_source": fys[-1] if fys else None,
                "note": "no observed months; suggestion resolves to 0",
            },
        }
    used = series[-k:]
    k_eff = len(used)
    run_rate_sum = float(sum(used))
    suggestion = run_rate_sum * 12.0 / k_eff
    explanation: dict[str, Any] = {
        "method": "run_rate",
        "base_fy": fys[-1] if fys else None,
        "window_months": k,
        "observed_months": k_eff,
        "run_rate_sum": run_rate_sum,
        "season_source": fys[-1] if fys else None,
    }
    if k_eff < k:
        explanation["note"] = f"only {k_eff} observed months available (requested {k})"
    return {
        "suggestion_annual": float(suggestion),
        "weights": _weights_of(actuals[fys[-1]]) if fys else _uniform_weights(),
        "explanation": explanation,
    }


# =========================================================================== #
# Dispatcher (one bounded DB pull, then a pure heuristic).
# =========================================================================== #
_METHODS = {
    "prior_year": prior_year,
    "trend_cagr": trend_cagr,
    "run_rate": run_rate,
}


def _current_fiscal_year(session: Session, *, fiscal_year: int) -> int:
    """The current (in-progress) fiscal year = the LEDGER anchor year.

    Any FY >= this is partial (the latest months are mid-close), so complete-FY
    heuristics drop it.  Falls back to the PLAN year ``fiscal_year`` when the anchor
    cannot be resolved (empty ledger / no session) — then ``current_fy ==
    plan_year`` keeps exactly the FYs the bounded pull already restricts to
    (fy <= plan_year-1), i.e. no over-dropping.
    """
    try:
        anchor = latest_anchor(session, "")
    except Exception:  # noqa: BLE001 — never let anchor lookup break a suggestion
        anchor = None
    if anchor is None:
        return int(fiscal_year)
    return int(anchor[0])


def suggest(
    session: Session,
    *,
    line_code: str,
    statement: str,
    fiscal_year: int,
    entity_prefix: Optional[str] = None,
    method: str = "prior_year",
    growth_pct: float = DEFAULT_GROWTH_PCT,
    years: int = DEFAULT_CAGR_YEARS,
    months: int = DEFAULT_RUN_RATE_MONTHS,
) -> dict[str, Any]:
    """Compute a suggestion for ONE position via the chosen heuristic.

    Performs the single bounded actuals pull, then dispatches to the pure
    heuristic.  Returns ``{"suggestion_annual", "weights", "explanation"}``.
    Unknown ``method`` raises ``ValueError``.
    """
    if method not in _METHODS:
        raise ValueError(f"unknown heuristic method: {method!r}")
    actuals = _position_actuals_by_fy(
        session, line_code=line_code, statement=statement,
        fiscal_year=fiscal_year, entity_prefix=entity_prefix,
    )
    # The current (in-progress) fiscal year = the ledger anchor year; any FY >= it
    # is partial and must NOT serve as a complete-FY base / CAGR window member.
    # Fall back to the plan year when the ledger is empty (no complete FY then).
    current_fy = _current_fiscal_year(session, fiscal_year=fiscal_year)
    if method == "prior_year":
        return prior_year(actuals, growth_pct=growth_pct, current_fy=current_fy)
    if method == "trend_cagr":
        return trend_cagr(
            actuals, years=years, growth_pct=growth_pct, current_fy=current_fy,
        )
    return run_rate(actuals, months=months, growth_pct=growth_pct)
