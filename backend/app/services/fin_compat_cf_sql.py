"""Legacy-compat SQL helpers for the **Cash Flow statement** (GDPdU backend).

Companion to ``fin_compat_sql`` (P&L) and ``fin_compat_bs_sql`` (Balance Sheet).
These helpers produce the grain rows that ``fin_compat_cf`` turns into the legacy
FinancialStatementResponse / ConsolidationResponse / MonthlyResponse /
L4TrendResponse / ErFlowResponse shapes — with **cash-flow** semantics.

=== 1. CF IS A FLOW STATEMENT (like the P&L, NOT the Balance Sheet) ===
A column is the Σ of the IN-PERIOD GL movements, exactly like the P&L (period
sums py_cm/pm/cm/ytd/ytd_py, +mtd for week), NOT cumulative balances.  The
indirect-method cash flow is read straight off the GL movements grouped by their
CF mapping, so an account's period movement IS its cash-flow contribution.

=== 2. MAPPED VIA ``dim_gl_cf`` (NOT ``dim_gl_account`` level matching) ===
The grain JOINs ``dim_gl_cf`` (PK account_number_group + fiscal_year — same as
``dim_gl_account``) and groups by the CF hierarchy
``l1, l2, l3, l4, l5, cf_mapping``.  ``cf_mapping`` is the leaf that the flat CF
structure rows (``dim_pl_structure`` kpi_code LIKE 'CF:%') are matched against.
The ``dim_gl_account`` join is kept only to carry ``gl_account_id`` /
``account_name`` for the account-level drill rows.

=== 3. SIGN CONVENTION — ``amount * -1`` (the SAME single P&L inversion) ===
``presented = l.amount * -1``  (this is the legacy CF convention, identical to
the P&L rule and applied EXACTLY ONCE here).  In cash-flow terms this yields the
correct inflow(+)/outflow(−) signs for the indirect method:

  * EBITDA / income (credit accounts, stored −) → +  (cash generated)
  * an INCREASE in an asset (debit, stored +)   → −  (cash used, e.g. Δ Inventories↑)
  * an INCREASE in a liability (credit, stored −) → + (cash freed, e.g. Δ Trade payables↑)

Never flip this sign again downstream.

=== 4. EXCLUSIONS ===
``cf_mapping`` values 'Exclude' / 'Exlude' (typo present in the source data) and
NULL/empty are filtered out in SQL — they carry no CF meaning and must never
reach a subtotal.

=== SCHEMA MAPPING (same joins as fin_compat_sql) ===
  fact_gl_line l  JOIN fact_gl_entry e
      ON e.journal_entry_group_number = l.journal_entry_group_number
     AND e.fiscal_year = l.fiscal_year
  JOIN dim_gl_cf cf
      ON cf.account_number_group = l.account_number_group
     AND cf.fiscal_year = l.fiscal_year
  JOIN dim_gl_account a            (display dims only: gl_account_id, account_name)
      ON a.account_number_group = l.account_number_group
     AND a.fiscal_year = l.fiscal_year
  entity filter via l.entity_prefix ({ent_frag}; resolved from legal_entity_code)
  fiscal_period BETWEEN 1 AND 12   (drops the period-13 consolidation rows)

=== WORKED EXAMPLE (month grain, year=2025, month=07) ===
  cf_mapping='EBITDA' grain rows summing (amount*-1) to +900 in period 2025-07
    → cm(EBITDA) = +900 (cash generated).
  cf_mapping='∆ Inventories' (inventory rose by 50, stored +50) → cm = −50
    (cash used). cf_mapping='∆ Trade payables' (payables rose 80, stored −80)
    → cm = +80 (cash freed).

=== EDGE CASES ===
  * No movement in a period → COALESCE(SUM, 0) → 0 for that column.
  * fiscal_period = 13 rows never counted.
  * cf_mapping 'Exclude'/'Exlude'/NULL → filtered out entirely.
  * month == 12 → LTM / LTM_PY are full calendar years (annual grain).
  * Entity filter matching nothing → all columns 0 for that bucket.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.services.fin_compat_sql import (
    _esc,
    _fy_span_periods,
    _fy_span_totals,
    _last_12_periods,
    _trend_windows,
    iso_week_bounds,
    last_day,
    pm,
    plan_anchor_for_week,
    prior_iso_week,
    same_week_prior_year,
)

# Standard month/week column keys (identical to the P&L statement schema).
_CF_KEYS_MONTH = ["py_cm", "pm", "cm", "ytd", "ytd_py"]
_CF_KEYS_WEEK = ["py_cm", "pm", "cm", "ytd", "ytd_py", "mtd"]

# CF hierarchy + display dims selected on every grain (l1..l5 + cf_mapping, plus
# the GL account id/name used only for the account-level drill rows).
_CF_GRAIN_DIMS = """
            COALESCE(NULLIF(TRIM(cf.l1), ''), '—') AS cf_l11_1,
            COALESCE(NULLIF(TRIM(cf.l2), ''), '—') AS cf_l11_2,
            COALESCE(NULLIF(TRIM(cf.l3), ''), '—') AS cf_l11_3,
            COALESCE(NULLIF(TRIM(cf.cf_mapping), ''), '—') AS cf_mapping,
            a.gl_account_id,
            MAX(a.account_name) AS account_name"""

_CF_FROM = """
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_cf cf
          ON cf.account_number_group = l.account_number_group
         AND cf.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year"""

# cf_mapping must be present and not one of the explicit exclusion tokens.
_CF_MAPPED = (
    "cf.cf_mapping IS NOT NULL AND TRIM(cf.cf_mapping) <> '' "
    "AND lower(TRIM(cf.cf_mapping)) NOT IN ('exclude', 'exlude')"
)

_CF_GROUP_BY = (
    "GROUP BY cf.l1, cf.l2, cf.l3, cf.cf_mapping, a.gl_account_id"
)


# ---------------------------------------------------------------------------
# Statement grain — month
# ---------------------------------------------------------------------------

def cf_grain_sql_month(year: int, month: int, ent_frag: str) -> tuple[str, dict[str, Any]]:
    """CF period-flow grain for the 5 standard columns (presented, ``amount*-1``).

    Columns use fiscal_year / fiscal_period (period 13 excluded), exactly like
    :func:`fin_compat_sql.pl_grain_sql_month`:
        py_cm  = fiscal_year year-1, fiscal_period == month
        pm     = prior month
        cm     = fiscal_year year,   fiscal_period == month
        ytd    = fiscal_year year,   fiscal_period <= month
        ytd_py = fiscal_year year-1, fiscal_period <= month
    """
    pm_y, pm_m = pm(year, month)
    py = year - 1
    years_arr = sorted({py, pm_y, year})
    params: dict[str, Any] = {"years": years_arr}
    sql = f"""
        SELECT
            {_CF_GRAIN_DIMS},
            COALESCE(SUM(CASE WHEN e.fiscal_year = {py}   AND e.fiscal_period = {month}
                         THEN l.amount * -1 ELSE 0 END), 0) AS py_cm,
            COALESCE(SUM(CASE WHEN e.fiscal_year = {pm_y} AND e.fiscal_period = {pm_m}
                         THEN l.amount * -1 ELSE 0 END), 0) AS pm,
            COALESCE(SUM(CASE WHEN e.fiscal_year = {year} AND e.fiscal_period = {month}
                         THEN l.amount * -1 ELSE 0 END), 0) AS cm,
            COALESCE(SUM(CASE WHEN e.fiscal_year = {year} AND e.fiscal_period <= {month}
                         THEN l.amount * -1 ELSE 0 END), 0) AS ytd,
            COALESCE(SUM(CASE WHEN e.fiscal_year = {py}   AND e.fiscal_period <= {month}
                         THEN l.amount * -1 ELSE 0 END), 0) AS ytd_py
        {_CF_FROM}
        WHERE {_CF_MAPPED}
          AND e.fiscal_year = ANY(:years)
          AND e.fiscal_period BETWEEN 1 AND 12
          {ent_frag}
        {_CF_GROUP_BY}
    """
    return sql, params


# ---------------------------------------------------------------------------
# Statement grain — week (posting_date ranges, +mtd)
# ---------------------------------------------------------------------------

def cf_grain_sql_week(iso_year: int, iso_week: int, ent_frag: str) -> tuple[str, dict[str, Any]]:
    """CF period-flow grain over ISO-week date ranges (+ ``mtd``), presented.

    Mirrors :func:`fin_compat_sql.pl_grain_sql_week` (posting_date windows): cm =
    anchor week, pm = prior week, py_cm = same week prior year, ytd = year-start
    to anchor Sunday, ytd_py = prior-year-start to spy Sunday, mtd = 1st of the
    anchor month (Thursday rule) to the anchor Sunday.
    """
    cw_f, cw_t = iso_week_bounds(iso_year, iso_week)
    pw_y, pw_w = prior_iso_week(iso_year, iso_week)
    pw_f, pw_t = iso_week_bounds(pw_y, pw_w)
    spy_y, spy_w = same_week_prior_year(iso_year, iso_week)
    spy_f, spy_t = iso_week_bounds(spy_y, spy_w)
    ytd_f = date(iso_year, 1, 1)
    ytd_py_f = date(spy_y, 1, 1)
    anchor_y, anchor_m = plan_anchor_for_week(iso_year, iso_week)
    mtd_f = date(anchor_y, anchor_m, 1)

    min_d = min(spy_f, pw_f, cw_f, ytd_f, ytd_py_f, mtd_f)
    max_d = max(spy_t, pw_t, cw_t)

    sql = f"""
        SELECT
            {_CF_GRAIN_DIMS},
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{spy_f}' AND '{spy_t}'
                         THEN l.amount * -1 ELSE 0 END), 0) AS py_cm,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{pw_f}' AND '{pw_t}'
                         THEN l.amount * -1 ELSE 0 END), 0) AS pm,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{cw_f}' AND '{cw_t}'
                         THEN l.amount * -1 ELSE 0 END), 0) AS cm,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{ytd_f}' AND '{cw_t}'
                         THEN l.amount * -1 ELSE 0 END), 0) AS ytd,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{ytd_py_f}' AND '{spy_t}'
                         THEN l.amount * -1 ELSE 0 END), 0) AS ytd_py,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{mtd_f}' AND '{cw_t}'
                         THEN l.amount * -1 ELSE 0 END), 0) AS mtd
        {_CF_FROM}
        WHERE {_CF_MAPPED}
          AND e.posting_date BETWEEN '{min_d}' AND '{max_d}'
          {ent_frag}
        {_CF_GROUP_BY}
    """
    return sql, {}


# ---------------------------------------------------------------------------
# Consolidation grain (per entity_prefix) — single cm flow column
# ---------------------------------------------------------------------------

def cf_consl_grain_sql_month(year: int, month: int) -> tuple[str, dict[str, Any]]:
    """CF cm-flow per entity_prefix (presented), all entities in one query."""
    params: dict[str, Any] = {"year": year}
    sql = f"""
        SELECT
            cf.l1, cf.l2, cf.l3, cf.l4, cf.l5,
            COALESCE(NULLIF(TRIM(cf.cf_mapping), ''), '—') AS cf_mapping,
            l.account_number_group,
            l.entity_prefix,
            COALESCE(SUM(CASE WHEN e.fiscal_year = {year} AND e.fiscal_period = {month}
                         THEN l.amount * -1 ELSE 0 END), 0) AS cm
        {_CF_FROM}
        WHERE {_CF_MAPPED}
          AND e.fiscal_year = :year
          AND e.fiscal_period BETWEEN 1 AND 12
        GROUP BY cf.l1, cf.l2, cf.l3, cf.l4, cf.l5,
                 COALESCE(NULLIF(TRIM(cf.cf_mapping), ''), '—'),
                 l.account_number_group, l.entity_prefix
    """
    return sql, params


def cf_consl_grain_sql_week(iso_year: int, iso_week: int) -> tuple[str, dict[str, Any]]:
    """CF cm-flow per entity_prefix over the anchor ISO week (presented)."""
    cw_f, cw_t = iso_week_bounds(iso_year, iso_week)
    sql = f"""
        SELECT
            cf.l1, cf.l2, cf.l3, cf.l4, cf.l5,
            COALESCE(NULLIF(TRIM(cf.cf_mapping), ''), '—') AS cf_mapping,
            l.account_number_group,
            l.entity_prefix,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{cw_f}' AND '{cw_t}'
                         THEN l.amount * -1 ELSE 0 END), 0) AS cm
        {_CF_FROM}
        WHERE {_CF_MAPPED}
          AND e.posting_date BETWEEN '{cw_f}' AND '{cw_t}'
        GROUP BY cf.l1, cf.l2, cf.l3, cf.l4, cf.l5,
                 COALESCE(NULLIF(TRIM(cf.cf_mapping), ''), '—'),
                 l.account_number_group, l.entity_prefix
    """
    return sql, {}


def cf_consl_grain_sql_annual_ytd(year: int, month: int) -> tuple[str, dict[str, Any]]:
    """Consolidation grain for YTD flow of the anchor fiscal year (entity breakdown).

    Emits a single ``cm`` column = Σ presented flow for fiscal_period 1..month of
    ``year`` (same alias as month grain for downstream builders).
    """
    params: dict[str, Any] = {"year": year, "month": month}
    sql = f"""
        SELECT
            cf.l1, cf.l2, cf.l3, cf.l4, cf.l5,
            COALESCE(NULLIF(TRIM(cf.cf_mapping), ''), '—') AS cf_mapping,
            l.account_number_group,
            l.entity_prefix,
            COALESCE(SUM(CASE WHEN e.fiscal_year = :year
                          AND e.fiscal_period BETWEEN 1 AND :month
                         THEN l.amount * -1 ELSE 0 END), 0) AS cm
        {_CF_FROM}
        WHERE {_CF_MAPPED}
          AND e.fiscal_year = :year
          AND e.fiscal_period BETWEEN 1 AND 12
        GROUP BY cf.l1, cf.l2, cf.l3, cf.l4, cf.l5,
                 COALESCE(NULLIF(TRIM(cf.cf_mapping), ''), '—'),
                 l.account_number_group, l.entity_prefix
    """
    return sql, params


# ---------------------------------------------------------------------------
# Monthly grain (one period-flow column per month)
# ---------------------------------------------------------------------------

def cf_monthly_grain_sql(
    year: int, month: int, ent_frag: str, *, span: str = "12m",
) -> tuple[str, dict[str, Any]]:
    """One column per period = that month's CF flow (presented, ``amount*-1``).

    span='12m' (default): the last 12 month flows ending at (year, month).
    span='fy3': all :func:`_fy_span_periods` months PLUS three summary flow
        columns (FY-2 / FY-1 full-year Σ, YTD Σ to the anchor month).
    """
    if span == "fy3":
        periods = _fy_span_periods(year, month)
        totals = _fy_span_totals(year, month)
    else:
        periods = _last_12_periods(year, month)
        totals = []

    cases: list[str] = []
    for y, m in periods:
        pk = f"{y:04d}-{m:02d}"
        cases.append(
            f"COALESCE(SUM(CASE WHEN e.fiscal_year = {y} AND e.fiscal_period = {m} "
            f"THEN l.amount * -1 ELSE 0 END), 0) AS \"{pk}\""
        )
    for t in totals:
        if t["kind"] == "fy":
            cases.append(
                f"COALESCE(SUM(CASE WHEN e.fiscal_year = {t['year']} "
                f"AND e.fiscal_period BETWEEN 1 AND 12 "
                f"THEN l.amount * -1 ELSE 0 END), 0) AS \"{t['key']}\""
            )
        else:  # ytd
            cases.append(
                f"COALESCE(SUM(CASE WHEN e.fiscal_year = {t['year']} "
                f"AND e.fiscal_period <= {month} "
                f"THEN l.amount * -1 ELSE 0 END), 0) AS \"{t['key']}\""
            )

    years_set = sorted({y for y, _ in periods} | {t["year"] for t in totals})
    params: dict[str, Any] = {"years": years_set}
    sql = f"""
        SELECT
            {_CF_GRAIN_DIMS},
            {', '.join(cases)}
        {_CF_FROM}
        WHERE {_CF_MAPPED}
          AND e.fiscal_year = ANY(:years)
          AND e.fiscal_period BETWEEN 1 AND 12
          {ent_frag}
        {_CF_GROUP_BY}
    """
    return sql, params


# ---------------------------------------------------------------------------
# Annual exit-readiness flow grain (fy1/fy2/fy3/ytd/ytd_py/ltm/ltm_py)
# ---------------------------------------------------------------------------

def cf_grain_sql_annual(year: int, month: int, ent_frag: str) -> tuple[str, dict[str, Any]]:
    """CF flow into the 7 annual exit-readiness columns (presented, ``amount*-1``).

    Mirrors :func:`fin_compat_sql.pl_grain_sql_annual` exactly (full FYs by
    fiscal_year; ytd/ytd_py by fiscal_period<=month; ltm/ltm_py by posting_date
    rolling-12 windows; month==12 collapses LTM to full calendar years), but on
    the CF (``dim_gl_cf``) mapping instead of the P&L levels.
    """
    fy1_y, fy2_y, fy3_y = year - 3, year - 2, year - 1
    ltm_start = date(fy3_y, month + 1, 1) if month < 12 else date(year, 1, 1)
    ltm_end = last_day(year, month)
    ltm_py_start = date(fy2_y, month + 1, 1) if month < 12 else date(fy3_y, 1, 1)
    ltm_py_end = last_day(fy3_y, month)
    years_arr = [fy1_y, fy2_y, fy3_y, year]
    params: dict[str, Any] = {"years": years_arr}
    sql = f"""
        SELECT
            {_CF_GRAIN_DIMS},
            COALESCE(SUM(CASE WHEN e.fiscal_year = {fy1_y}
                         THEN l.amount * -1 ELSE 0 END), 0) AS fy1,
            COALESCE(SUM(CASE WHEN e.fiscal_year = {fy2_y}
                         THEN l.amount * -1 ELSE 0 END), 0) AS fy2,
            COALESCE(SUM(CASE WHEN e.fiscal_year = {fy3_y}
                         THEN l.amount * -1 ELSE 0 END), 0) AS fy3,
            COALESCE(SUM(CASE WHEN e.fiscal_year = {year} AND e.fiscal_period <= {month}
                         THEN l.amount * -1 ELSE 0 END), 0) AS ytd,
            COALESCE(SUM(CASE WHEN e.fiscal_year = {fy3_y} AND e.fiscal_period <= {month}
                         THEN l.amount * -1 ELSE 0 END), 0) AS ytd_py,
            COALESCE(SUM(CASE WHEN e.posting_date >= '{ltm_start.isoformat()}'
                          AND e.posting_date <= '{ltm_end.isoformat()}'
                         THEN l.amount * -1 ELSE 0 END), 0) AS ltm,
            COALESCE(SUM(CASE WHEN e.posting_date >= '{ltm_py_start.isoformat()}'
                          AND e.posting_date <= '{ltm_py_end.isoformat()}'
                         THEN l.amount * -1 ELSE 0 END), 0) AS ltm_py
        {_CF_FROM}
        WHERE {_CF_MAPPED}
          AND e.fiscal_year = ANY(:years)
          AND e.fiscal_period BETWEEN 1 AND 12
          {ent_frag}
        {_CF_GROUP_BY}
    """
    return sql, params


# ---------------------------------------------------------------------------
# L4 trend (period-flow at each window; presented, ``amount*-1``)
# ---------------------------------------------------------------------------

def cf_l4_trend_sql(
    year: int, month: int, grain: str,
    level_2: str, level_3: str, level_4: str,
    ent_frag: str,
) -> tuple[str, list[dict]]:
    """CF L4 trend: each point is the in-window flow for a CF position.

    The frontend maps the CF drill keys to the trend filters:
        level_2 → dim_gl_cf.l1   (cf_l11_1)
        level_3 → dim_gl_cf.l2   (cf_l11_2)
        level_4 → dim_gl_cf.cf_mapping
    Reuses :func:`fin_compat_sql._trend_windows` (year/quarter/month grains) and
    the P&L flow sign (``amount*-1``).
    """
    windows = _trend_windows(year, month, grain)
    l1_f = f"AND TRIM(cf.l1) = '{_esc(level_2)}'" if level_2 else ""
    l2_f = f"AND TRIM(cf.l2) = '{_esc(level_3)}'" if level_3 else ""
    cfm_f = f"AND TRIM(cf.cf_mapping) = '{_esc(level_4)}'" if level_4 else ""

    cases: list[str] = []
    where_parts: list[str] = []
    for w in windows:
        s, e = w["cur_start"], w["cur_end"]
        ps, pe = w["prev_start"], w["prev_end"]
        if s == e:
            cases.append(f"COALESCE(SUM(CASE WHEN e.posting_date = '{s}' THEN l.amount*-1 ELSE 0 END),0) AS \"{w['pk']}\"")
            where_parts.append(f"e.posting_date = '{s}'")
        else:
            cases.append(f"COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{s}' AND '{e}' THEN l.amount*-1 ELSE 0 END),0) AS \"{w['pk']}\"")
            where_parts.append(f"e.posting_date BETWEEN '{s}' AND '{e}'")
        if ps == pe:
            cases.append(f"COALESCE(SUM(CASE WHEN e.posting_date = '{ps}' THEN l.amount*-1 ELSE 0 END),0) AS \"{w['prev_pk']}\"")
            where_parts.append(f"e.posting_date = '{ps}'")
        else:
            cases.append(f"COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{ps}' AND '{pe}' THEN l.amount*-1 ELSE 0 END),0) AS \"{w['prev_pk']}\"")
            where_parts.append(f"e.posting_date BETWEEN '{ps}' AND '{pe}'")
    where_clause = f"AND ({' OR '.join(where_parts)})" if where_parts else ""

    sql = f"""
        SELECT {', '.join(cases)}
        {_CF_FROM}
        WHERE {_CF_MAPPED}
          {l1_f} {l2_f} {cfm_f}
          {where_clause}
          {ent_frag}
    """
    return sql, windows


# ---------------------------------------------------------------------------
# Weekly-breakdown grain (one column per ISO week + month / MTD totals)
# ---------------------------------------------------------------------------

def cf_weekly_breakdown_sql(
    layout: list[dict[str, Any]], ent_frag: str,
) -> tuple[str, dict[str, Any]]:
    """Wide CF SQL for the weekly-breakdown ``layout`` (one CASE column per key).

    Mirrors :func:`fin_compat_sql.pl_weekly_breakdown_sql` but aggregates via
    ``dim_gl_cf`` (cf_mapping) instead of P&L levels.  Sign: ``amount * -1``.
    """
    cases: list[str] = []
    scan_years: set[int] = set()
    for g in layout:
        for w in g["weeks"]:
            cases.append(
                f"COALESCE(SUM(CASE WHEN e.posting_date BETWEEN "
                f"'{w['monday'].isoformat()}' AND '{w['sunday'].isoformat()}' "
                f"THEN l.amount * -1 ELSE 0 END), 0) AS \"{w['key']}\""
            )
            scan_years.add(w["monday"].year)
            scan_years.add(w["sunday"].year)
        t = g["total"]
        if t["kind"] == "month":
            cases.append(
                f"COALESCE(SUM(CASE WHEN e.fiscal_year = {t['year']} "
                f"AND e.fiscal_period = {t['month']} "
                f"THEN l.amount * -1 ELSE 0 END), 0) AS \"{t['key']}\""
            )
        else:  # mtd
            mtd_start = date(t["year"], t["month"], 1)
            cases.append(
                f"COALESCE(SUM(CASE WHEN e.posting_date BETWEEN "
                f"'{mtd_start.isoformat()}' AND '{t['mtd_end'].isoformat()}' "
                f"THEN l.amount * -1 ELSE 0 END), 0) AS \"{t['key']}\""
            )
            scan_years.add(mtd_start.year)
        scan_years.add(t["year"])

    years_arr = sorted(scan_years)
    params: dict[str, Any] = {"years": years_arr}

    sql = f"""
        SELECT
            {_CF_GRAIN_DIMS},
            {', '.join(cases)}
        {_CF_FROM}
        WHERE {_CF_MAPPED}
          AND e.fiscal_year = ANY(:years)
          AND e.fiscal_period BETWEEN 1 AND 12
          {ent_frag}
        {_CF_GROUP_BY}
    """
    return sql, params
