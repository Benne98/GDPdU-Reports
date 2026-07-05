"""Overview v2 — recent-months exception alerting (Area 1, reporting-v2 only).

Signed-off math lives in ``docs/financial-logic.md`` → "Overview Page v2 — KPI
sign-offs" → Area 1 ("Recent-months 3-trigger alert"); this module IMPLEMENTS it
as a pure service function.  It touches NO existing financial result: it only
re-reads the already-signed-off revenue / gross-margin series and flags months.

================================================================================
FINANCIAL LOGIC (CLAUDE.md rule #1) — recent-months 3-trigger alert
================================================================================
For the last ``N = n_months`` calendar months ``m`` (anchor month back), for each
visible entity × metric ∈ {revenue, gross margin}, month ``m`` is FLAGGED when any
trigger fires (``x`` = the metric value at month ``m``):

    T1 (vs prior-year month)  |x − x_py|  / |x_py|  ≥ 0.20
    T2 (vs plan)              |x − plan|  / |plan|  ≥ 0.10
    T3 (z-score)              |z| ≥ 2.0,  z = (x − μ) / σ̂

where ``μ, σ̂`` are the mean and SAMPLE std (``ddof=1``) over the trailing-12
months EXCLUDING ``m`` — computed via ``gl_outliers.compute_series_stats`` /
``compute_point_zscores`` so the z-score is byte-identical to the GL outlier page.

    severity  = number of triggers fired (0..3); a month with severity 0 is dropped.
    direction = sign(x − ref):  "up" (x > ref), "down" (x < ref), "flat" (x == ref),
                where ref = the reference of the HIGHEST-PRIORITY fired trigger
                (T1 prior-year > T2 plan > T3 mean) — deterministic & testable.

A trigger is SKIPPED (never a divide-by-zero) when its denominator/σ is 0:
    T1 when |x_py| < eps · T2 when |plan| < eps · T3 when σ̂ == 0 (flat series).

Sign conventions are REUSED verbatim (never re-derived here):
    revenue      = Σ fact_sales.gross_sales / 1000  (kEUR, already +; sales_analytics_compat /
                   overview_top_entities L14) restricted to level_3 = 'Net sales'.
    gross margin = 100 · (Rev − COM) / Rev  (None if |Rev| < eps), COM = Σ
                   fact_com.cost_of_materials / 1000 (kEUR, already +) over
                   level_3 = 'Cost of materials' (same grain as Area 1 sign-off).
    plan         = fact_sales_plan / fact_com_plan (scenario budget→forecast→plan;
                   same resolution order as overview_top_entities._load_partner_plan_cm),
                   in kEUR; plan gross margin from plan Rev / plan COM.

Period math REUSES the shared calendar helpers ``fin_compat_sql.pm`` / ``last_day``
/ ``period_label`` (the canonical posting_date-window regime the sales-fact
modules already use, per plan §2 "two period regimes kept separate") — never
inlined here.

Worked example (severity-3 case, matches the doc to the cent):
    x = 60, x_py = 95, plan = 90, μ = 100, σ̂ = 10
    T1 = 35/95 = 0.368 ≥ 0.20 ✓   T2 = 30/90 = 0.333 ≥ 0.10 ✓
    T3 = |(60−100)/10| = 4.0 ≥ 2.0 ✓   → severity 3, ref = x_py = 95, "down".

Edge cases: x_py 0 → T1 skipped; plan 0 → T2 skipped; σ̂ 0 → T3 skipped;
missing plan → T2 skipped; undefined margin (|Rev| < eps) → that month/metric is
not evaluated; empty visible set → [] (see SECURITY below).

================================================================================
SECURITY — FAIL-CLOSED tenant isolation contract (MANDATORY)
================================================================================
``allowed_entities`` is the visible-entity set (entity_prefix codes) the ENDPOINT
resolves from ``entity_visibility.visible_entity_codes()`` and passes in — this
function NEVER resolves visibility itself:

    None       → admin / unrestricted → no entity filter applied.
    empty set  → FAIL-CLOSED → return [] immediately (query nothing).
    non-empty  → every SQL query is filtered to ``entity_prefix IN allowed_entities``.

The optional ``entity`` argument narrows within that boundary; if it resolves to a
prefix outside ``allowed_entities`` the effective set is empty → [] (fail-closed).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.fin_compat_sql import (
    last_day,
    period_label,
    pm,
    resolve_entity_prefix,
)
from app.services.gl_outliers import compute_point_zscores, compute_series_stats

EPS = 1e-9

# Approved thresholds (docs/financial-logic.md, Area 1 — do NOT change silently).
T1_THRESHOLD = 0.20   # vs prior-year month
T2_THRESHOLD = 0.10   # vs plan
Z_THRESHOLD = 2.0     # |z| trigger

_METRICS = ("revenue", "gross_margin")

# Plan scenario preference (first with data wins) — mirrors overview_top_entities.
_PLAN_SCENARIOS = ("budget", "forecast", "plan")


# ---------------------------------------------------------------------------
# Pure trigger evaluation (DB-free, the heart of the signed-off formula)
# ---------------------------------------------------------------------------

def evaluate_month_alert(
    x: float,
    *,
    x_py: Optional[float],
    plan: Optional[float],
    mean: float,
    std: float,
    t1_threshold: float = T1_THRESHOLD,
    t2_threshold: float = T2_THRESHOLD,
    z_threshold: float = Z_THRESHOLD,
) -> Optional[dict[str, Any]]:
    """Evaluate the 3 triggers for one (metric, month) point; return None if none fire.

    ``mean`` / ``std`` are the trailing-12 (EXCLUDING m) stats from
    :func:`gl_outliers.compute_series_stats`.  Each trigger is skipped when its
    denominator/σ is 0 (never raises).  ``severity`` = number fired; ``direction``
    = sign(x − ref) with ref = the highest-priority fired trigger's reference
    (T1 prior-year > T2 plan > T3 mean).  See module docstring for the formula.
    """
    triggers: list[str] = []

    # T1 — vs prior-year month (skip when |x_py| == 0).
    if x_py is not None and abs(x_py) > EPS:
        if abs(x - x_py) / abs(x_py) >= t1_threshold:
            triggers.append("T1")

    # T2 — vs plan (skip when |plan| == 0 or plan missing).
    if plan is not None and abs(plan) > EPS:
        if abs(x - plan) / abs(plan) >= t2_threshold:
            triggers.append("T2")

    # T3 — z-score over trailing-12 excluding m (skip when σ̂ == 0; reuse gl_outliers).
    z = compute_point_zscores([x], mean, std)[0][1] if std > 0 else 0.0
    if std > 0 and abs(z) >= z_threshold:
        triggers.append("T3")

    severity = len(triggers)
    if severity == 0:
        return None

    # Reference for direction — highest-priority fired trigger.
    if "T1" in triggers:
        ref, ref_kind = float(x_py), "prior_year"  # type: ignore[arg-type]
    elif "T2" in triggers:
        ref, ref_kind = float(plan), "plan"         # type: ignore[arg-type]
    else:
        ref, ref_kind = float(mean), "mean"

    delta = x - ref
    direction = "up" if delta > EPS else ("down" if delta < -EPS else "flat")

    return {
        "severity": severity,
        "direction": direction,
        "triggers": triggers,
        "reference": round(ref, 4),
        "reference_kind": ref_kind,
        "z": round(z, 4),
    }


# ---------------------------------------------------------------------------
# Calendar helpers (reuse fin_compat_sql.pm — never inline month arithmetic)
# ---------------------------------------------------------------------------

def _step_back(year: int, month: int, k: int) -> tuple[int, int]:
    """(year, month) exactly ``k`` calendar months before ``(year, month)``."""
    y, m = int(year), int(month)
    for _ in range(int(k)):
        y, m = pm(y, m)
    return y, m


# ---------------------------------------------------------------------------
# Fail-closed entity-filter helpers
# ---------------------------------------------------------------------------

def _prefix_in_clause(
    col_expr: str, prefixes: Optional[set[str]], key: str,
) -> tuple[str, dict[str, Any]]:
    """AND-fragment restricting ``col_expr`` to ``prefixes`` (None → no filter)."""
    if prefixes is None:
        return "", {}
    params: dict[str, Any] = {}
    names: list[str] = []
    for i, p in enumerate(sorted(prefixes)):
        pk = f"{key}{i}"
        params[pk] = str(p)[:2]
        names.append(f":{pk}")
    return f"AND {col_expr} IN ({', '.join(names)})", params


def _int_in_clause(col: str, values: list[int], key: str) -> tuple[str, dict[str, Any]]:
    params: dict[str, Any] = {}
    names: list[str] = []
    for i, v in enumerate(sorted({int(x) for x in values})):
        pk = f"{key}{i}"
        params[pk] = int(v)
        names.append(f":{pk}")
    return f"{col} IN ({', '.join(names)})", params


# ---------------------------------------------------------------------------
# Monthly-series loaders (posting_date window; sign per module docstring)
# ---------------------------------------------------------------------------

def _load_monthly_actual(
    session: Session, *, fact: str, value_col: str, level_3: str,
    d0: str, d1: str, prefixes: Optional[set[str]],
) -> dict[tuple[str, int, int], float]:
    """{(entity_prefix, year, month): Σ value/1000 (kEUR)} over posting_date window."""
    ent_clause, params = _prefix_in_clause("LEFT(f.account_number_group, 2)", prefixes, "ep")
    params.update({"d0": d0, "d1": d1})
    sql = text(f"""
        SELECT LEFT(f.account_number_group, 2)              AS ep,
               CAST(EXTRACT(YEAR  FROM f.posting_date) AS INT) AS yr,
               CAST(EXTRACT(MONTH FROM f.posting_date) AS INT) AS mo,
               SUM(f.{value_col}) / 1000.0                   AS val
        FROM {fact} f
        JOIN dim_gl_account a
          ON a.account_number_group = f.account_number_group
         AND a.fiscal_year          = f.fiscal_year
        WHERE f.posting_date BETWEEN :d0 AND :d1
          AND TRIM(a.level_3) = '{level_3}'
          {ent_clause}
        GROUP BY 1, 2, 3
    """)
    out: dict[tuple[str, int, int], float] = {}
    for r in session.execute(sql, params).fetchall():
        out[(str(r[0]), int(r[1]), int(r[2]))] = float(r[3] or 0.0)
    return out


def _load_monthly_plan(
    session: Session, *, fact: str, id_col: str, value_col: str,
    years: list[int], months: list[int], prefixes: Optional[set[str]],
) -> dict[tuple[str, int, int], float]:
    """{(entity_prefix, fiscal_year, fiscal_period): Σ plan/1000} — scenario fallback.

    Tries scenarios budget→forecast→plan; the first that returns any row wins (same
    order as overview_top_entities._load_partner_plan_cm).  Plan magnitudes are
    already positive (mirror fact_sales / fact_com) → no sign flip.
    """
    yr_sql, yr_params = _int_in_clause("fiscal_year", years, "y")
    mo_sql, mo_params = _int_in_clause("fiscal_period", months, "m")
    ent_clause, ent_params = _prefix_in_clause(f"LEFT({id_col}, 2)", prefixes, "ep")
    for scenario in _PLAN_SCENARIOS:
        params: dict[str, Any] = {"scn": scenario}
        params.update(yr_params)
        params.update(mo_params)
        params.update(ent_params)
        sql = text(f"""
            SELECT LEFT({id_col}, 2) AS ep, fiscal_year AS yr, fiscal_period AS mo,
                   SUM({value_col}) / 1000.0 AS val
            FROM {fact}
            WHERE scenario = :scn AND {yr_sql} AND {mo_sql}
              {ent_clause}
            GROUP BY 1, 2, 3
        """)
        rows = session.execute(sql, params).fetchall()
        if rows:
            return {
                (str(r[0]), int(r[1]), int(r[2])): float(r[3] or 0.0) for r in rows
            }
    return {}


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def build_recent_month_alerts(
    session: Session,
    *,
    entity: Optional[str],
    year: int,
    month: int,
    allowed_entities: Optional[set[str]],
    n_months: int = 3,
    max_items: int = 5,
) -> list[dict[str, Any]]:
    """Recent-months exception alerts for the last ``n_months`` × {revenue, margin}.

    Returns at most ``max_items`` dicts, highest severity first.  PURE READ — builds
    nothing, writes nothing.

    SECURITY (fail-closed): ``allowed_entities`` = visible entity_prefix set from
    ``entity_visibility.visible_entity_codes()`` (endpoint-resolved).  None =
    unrestricted; empty set = deny-all → []; otherwise every query is filtered to
    ``entity_prefix IN allowed_entities`` (see module docstring).
    """
    # --- FAIL-CLOSED gate #1: empty visible set → deny all, query nothing. -------
    if allowed_entities is not None and len(allowed_entities) == 0:
        return []

    n = max(1, int(n_months))

    # Effective prefix set = visibility boundary ∩ optional single-entity narrow.
    prefixes: Optional[set[str]]
    if allowed_entities is None:
        prefixes = None
    else:
        prefixes = {str(p)[:2] for p in allowed_entities}

    ep = resolve_entity_prefix(session, entity)  # None for all/"" (no DB hit)
    if ep is not None:
        ep2 = str(ep)[:2]
        if prefixes is None:
            prefixes = {ep2}
        else:
            prefixes = prefixes & {ep2}
            # --- FAIL-CLOSED gate #2: narrow outside visibility → deny all. ------
            if not prefixes:
                return []

    # --- Recent months (anchor back) and the full series window needed. ----------
    recent = [_step_back(year, month, i) for i in range(n)]  # i=0 = anchor
    oldest_y, oldest_m = recent[-1]
    earliest_y, earliest_m = _step_back(oldest_y, oldest_m, 12)
    d0 = date(earliest_y, earliest_m, 1).isoformat()
    d1 = last_day(year, month).isoformat()

    # Actual monthly series (kEUR): revenue (Net sales) and COM (Cost of materials).
    rev_map = _load_monthly_actual(
        session, fact="fact_sales", value_col="gross_sales",
        level_3="Net sales", d0=d0, d1=d1, prefixes=prefixes,
    )
    com_map = _load_monthly_actual(
        session, fact="fact_com", value_col="cost_of_materials",
        level_3="Cost of materials", d0=d0, d1=d1, prefixes=prefixes,
    )

    # Plan monthly series for the recent months only (scenario budget→forecast→plan).
    rec_years = [ry for ry, _ in recent]
    rec_months = [rm for _, rm in recent]
    plan_rev_map = _load_monthly_plan(
        session, fact="fact_sales_plan", id_col="customer_id",
        value_col="gross_sales_plan", years=rec_years, months=rec_months,
        prefixes=prefixes,
    )
    plan_com_map = _load_monthly_plan(
        session, fact="fact_com_plan", id_col="supplier_id",
        value_col="cost_of_materials_plan", years=rec_years, months=rec_months,
        prefixes=prefixes,
    )

    entities = sorted({k[0] for k in rev_map} | {k[0] for k in com_map})

    def _revenue(e: str, y: int, m: int) -> Optional[float]:
        return rev_map.get((e, y, m), 0.0)

    def _margin(e: str, y: int, m: int) -> Optional[float]:
        rev = rev_map.get((e, y, m), 0.0)
        com = com_map.get((e, y, m), 0.0)
        if abs(rev) < EPS:
            return None
        return 100.0 * (rev - com) / rev

    def _revenue_plan(e: str, y: int, m: int) -> Optional[float]:
        return plan_rev_map.get((e, y, m))

    def _margin_plan(e: str, y: int, m: int) -> Optional[float]:
        prev = plan_rev_map.get((e, y, m))
        if prev is None or abs(prev) < EPS:
            return None
        pcom = plan_com_map.get((e, y, m), 0.0)
        return 100.0 * (prev - pcom) / prev

    metric_fns = {
        "revenue": (_revenue, _revenue_plan),
        "gross_margin": (_margin, _margin_plan),
    }

    alerts: list[dict[str, Any]] = []
    for e in entities:
        for metric in _METRICS:
            value_fn, plan_fn = metric_fns[metric]
            for recency_idx, (ry, rm) in enumerate(recent):
                x = value_fn(e, ry, rm)
                if x is None:  # undefined margin month → not evaluable
                    continue
                py_y, py_m = _step_back(ry, rm, 12)
                x_py = value_fn(e, py_y, py_m)
                plan = plan_fn(e, ry, rm)

                # Trailing-12 EXCLUDING m (months m-1 .. m-12), drop None (margin gaps).
                trailing: list[float] = []
                for k in range(1, 13):
                    ty, tm = _step_back(ry, rm, k)
                    v = value_fn(e, ty, tm)
                    if v is not None:
                        trailing.append(v)
                stats = compute_series_stats(trailing)

                res = evaluate_month_alert(
                    x, x_py=x_py, plan=plan,
                    mean=stats["mean_keur"], std=stats["std_keur"],
                )
                if res is None:
                    continue

                alerts.append({
                    "entity": e,
                    "metric": metric,
                    "year": ry,
                    "month": rm,
                    "label": period_label(ry, rm),
                    "value": round(float(x), 4),
                    "recency_idx": recency_idx,
                    **res,
                })

    # Highest severity first; then most-recent month; then stable by entity/metric.
    alerts.sort(key=lambda a: (-a["severity"], a["recency_idx"], a["entity"], a["metric"]))
    return alerts[:max_items]
