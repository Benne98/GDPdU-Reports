"""Legacy-compat SQL helpers for **Working Capital** (GDPdU backend).

Working capital is a SUBSET of the balance-sheet balances: only GL accounts whose
``dim_gl_na.l6_na_mapping`` is ``'TWC'`` (Trade Working Capital) or ``'OWC'`` (Other
Working Capital).  Everything else about the column maths is identical to the
balance sheet (see ``fin_compat_bs_sql`` — authoritative):

=== 1. CUMULATIVE STOCK, NOT PERIOD FLOW (same as BS) ===
Each column value is the CLOSING BALANCE as of the END of that column's period —
the cumulative Σ of ALL movements with ``posting_date <= cutoff``.  Every CASE WHEN
is ``SUM(CASE WHEN e.posting_date <= '<cutoff>' THEN l.amount ELSE 0 END)`` (reuses
``fin_compat_bs_sql._bal_case``).  Column cutoffs are the SAME as the BS:
    py_cm / ytd_py = last_day(year-1, month)
    pm             = last_day(prior month)
    cm / ytd       = last_day(year, month)
Week grain adds ``mtd`` = balance at the anchor ISO-week Sunday (== cm for a stock).

=== 2. NO ``amount * -1`` INVERSION — RAW STORED SIGN (mirrors LEGACY WC) ===
Like the BS, the working-capital balances keep the **raw stored sign** here
(``SUM(l.amount)``, no ``* -1``): assets (debit-normal: inventories, receivables,
other assets) come out POSITIVE, liabilities (credit-normal: payables, advance
payments received, other liabilities) come out NEGATIVE.

IMPORTANT DEVIATION FROM THE BS COMPAT LAYER: the legacy working-capital endpoints
(``routers/financials.py::get_working_capital`` & friends) present these RAW signed
balances WITHOUT the BS credit-side display flip — Net working capital is then the
straight Σ of the raw TWC+OWC balances (assets + minus liabilities −), which is the
economically correct NWC.  ``fin_compat_wc`` therefore does NOT call the BS
``_flip_row_tree``; it carries the raw signs through, exactly like the legacy WC.

The DAYS KPIs (DSO/DIO/DPO) take the ABSOLUTE magnitude of the relevant balances so
they read as positive day-counts regardless of the stored sign (see
``fin_compat_wc.compute_wc_kpis``) — the same choice the legacy ``_compute_wc_kpis``
callers make for the WC statement.

=== SCHEMA MAPPING ===
  fact_gl_line l  JOIN fact_gl_entry e
      ON e.journal_entry_group_number = l.journal_entry_group_number
     AND e.fiscal_year = l.fiscal_year
  JOIN dim_gl_account a
      ON a.account_number_group = l.account_number_group AND a.fiscal_year = l.fiscal_year
  JOIN dim_gl_na na                                       -- the WC classifier
      ON na.account_number_group = l.account_number_group AND na.fiscal_year = l.fiscal_year
  a.level_0 = 'BS'  AND  na.l6_na_mapping IN ('TWC','OWC')
  entity filter via l.entity_prefix ({ent_frag})

=== WORKED EXAMPLE (cumulative, month grain, real synthetic GL @ 2024-12-31) ===
  TWC = -3,369,432.70 ; OWC = +1,847,715.67 ; NWC = TWC + OWC = -1,521,717.03.
  Inventories raw = -4,577,601.43 ; Trade receivables = +5,206,174.71 ;
  Trade payables = -3,282,140.15 (credit, negative).  The DAYS KPIs use the ABS of
  each (see fin_compat_wc).

=== EDGE CASES ===
  * No movements up to a cutoff → COALESCE(SUM,0) → 0 balance for that column.
  * Entity filter matching nothing → all columns 0 for that bucket.
  * An account with no dim_gl_na row → excluded (INNER JOIN on dim_gl_na) — correct,
    because only TWC/OWC accounts are working capital.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.services.fin_compat_bs_sql import (
    _bal_case,
    _bal_case_fy,
    _bs_snapshot_bal_cases,
    _BS_FROM_BAL,
    _wrap_bs_bal_sql,
)
from app.services.fin_compat_sql import (
    _esc,
    _fy_span_periods,
    _fy_span_totals,
    _last_12_periods,
    _trend_windows,
    iso_week_bounds,
    last_day,
    plan_anchor_for_week,
    pm,
    prior_iso_week,
    same_week_prior_year,
)

# Standard month/week column keys (identical to the BS statement schema).
_WC_KEYS_MONTH = ["py_cm", "pm", "cm", "ytd", "ytd_py"]
_WC_KEYS_WEEK = ["py_cm", "pm", "cm", "ytd", "ytd_py", "mtd"]
# Annual exit-readiness snapshot column keys (same as BS snapshot).
_WC_SNAP_KEYS = ["dec_py2", "fy_py", "fy", "cm_py", "cm"]

# Working-capital classifier values (dim_gl_na.l6_na_mapping).
WC_MAPPINGS = ("TWC", "OWC")

# level_3 names that drive the DSO/DIO/DPO numerators (same names as the legacy).
WC_INV_L3 = "Inventories"
WC_REC_L3 = "Trade receivables"
WC_PAY_L3 = "Trade payables"
# P&L level_3 names that drive the LTM denominators (same names as the legacy).
PL_REV_L3 = "Net sales"
PL_COGS_L3 = "Cost of materials"

# Per-line WC grain dims (section = l6_na_mapping, line = level_3 / l7 description).
_WC_GRAIN_DIMS = """
            na.l6_na_mapping            AS l6_na_mapping,
            na.l7_na_description        AS l7_na_description,
            a.level_2,
            a.level_3,
            NULLIF(TRIM(a.level_4), '') AS level_4,
            MAX(a.gl_account_id)        AS gl_account_id,
            l.account_number_group,
            MAX(a.account_name)         AS account_name"""

_WC_CONSL_GRAIN_DIMS = """
            na.l6_na_mapping            AS l6_na_mapping,
            na.l7_na_description        AS l7_na_description,
            a.level_2,
            a.level_3,
            NULLIF(TRIM(a.level_4), '') AS level_4,
            l.account_number_group,
            l.entity_prefix,
            MAX(a.gl_account_id)        AS gl_account_id,
            MAX(a.account_name)         AS account_name"""

_WC_FROM = """
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        JOIN dim_gl_na na
          ON na.account_number_group = l.account_number_group
         AND na.fiscal_year = l.fiscal_year"""

_WC_GROUP_BY = (
    "GROUP BY na.l6_na_mapping, na.l7_na_description, a.level_2, a.level_3, "
    "NULLIF(TRIM(a.level_4), ''), l.account_number_group"
)

_WC_WHERE = "WHERE a.level_0 = 'BS' AND na.l6_na_mapping IN ('TWC','OWC')"

_WC_FROM_BAL = (
    _WC_FROM
    + """
        LEFT JOIN bal_mov bm
          ON bm.account_number_group = l.account_number_group"""
)


# ---------------------------------------------------------------------------
# Statement grain — month
# ---------------------------------------------------------------------------

def wc_grain_sql_month(year: int, month: int, ent_frag: str) -> tuple[str, dict[str, Any]]:
    """Cumulative WC balances for the 5 standard columns (raw sign, no ``*-1``).

    Same cutoffs as ``bs_grain_sql_month``; restricted to TWC/OWC accounts.
    """
    pm_y, pm_m = pm(year, month)
    d_py = last_day(year - 1, month)
    d_pm = last_day(pm_y, pm_m)
    d_cm = last_day(year, month)
    cases = ", ".join([
        _bal_case(d_py, "py_cm"),
        _bal_case(d_pm, "pm"),
        _bal_case(d_cm, "cm"),
        _bal_case(d_cm, "ytd"),
        _bal_case(d_py, "ytd_py"),
    ])
    sql = f"""
        SELECT
            {_WC_GRAIN_DIMS},
            {cases}
        {_WC_FROM_BAL}
        {_WC_WHERE}
          AND e.posting_date <= '{d_cm.isoformat()}'
          {ent_frag}
        {_WC_GROUP_BY}
    """
    return _wrap_bs_bal_sql(sql), {}


# ---------------------------------------------------------------------------
# Statement grain — week
# ---------------------------------------------------------------------------

def wc_grain_sql_week(iso_year: int, iso_week: int, ent_frag: str) -> tuple[str, dict[str, Any]]:
    """Cumulative WC balances at ISO-week Sunday cutoffs (raw sign, + ``mtd``)."""
    _, d_cm = iso_week_bounds(iso_year, iso_week)
    pw_y, pw_w = prior_iso_week(iso_year, iso_week)
    _, d_pm = iso_week_bounds(pw_y, pw_w)
    spy_y, spy_w = same_week_prior_year(iso_year, iso_week)
    _, d_py = iso_week_bounds(spy_y, spy_w)
    cases = ", ".join([
        _bal_case(d_py, "py_cm"),
        _bal_case(d_pm, "pm"),
        _bal_case(d_cm, "cm"),
        _bal_case(d_cm, "ytd"),
        _bal_case(d_py, "ytd_py"),
        _bal_case(d_cm, "mtd"),
    ])
    sql = f"""
        SELECT
            {_WC_GRAIN_DIMS},
            {cases}
        {_WC_FROM_BAL}
        {_WC_WHERE}
          AND e.posting_date <= '{d_cm.isoformat()}'
          {ent_frag}
        {_WC_GROUP_BY}
    """
    return _wrap_bs_bal_sql(sql), {}


# ---------------------------------------------------------------------------
# Consolidation grain (per entity_prefix) — single cumulative ``cm`` column
# ---------------------------------------------------------------------------

def wc_consl_grain_sql_annual(year: int, month: int) -> tuple[str, dict[str, Any]]:
    """FY-scoped WC balances at ER snapshot cutoffs, per entity_prefix (raw sign).

    Same cutoffs as :func:`wc_snapshot_grain_sql` (dec_py2 / fy_py / fy / cm_py / cm).
    """
    d_cm = last_day(year, month)
    cases = _bs_snapshot_bal_cases(year, month)
    sql = f"""
        SELECT
            {_WC_CONSL_GRAIN_DIMS},
            {cases}
        {_WC_FROM}
        {_WC_WHERE}
          AND e.posting_date <= '{d_cm.isoformat()}'
        GROUP BY na.l6_na_mapping, na.l7_na_description, a.level_2, a.level_3,
                 NULLIF(TRIM(a.level_4), ''), l.account_number_group, l.entity_prefix
    """
    return sql, {}


def wc_consl_grain_sql_month(year: int, month: int) -> tuple[str, dict[str, Any]]:
    """FY-scoped WC balance at the month cutoff, per entity_prefix (raw sign)."""
    d_cm = last_day(year, month)
    sql = f"""
        SELECT
            {_WC_CONSL_GRAIN_DIMS},
            {_bal_case_fy(year, d_cm, 'cm')}
        {_WC_FROM}
        {_WC_WHERE}
          AND e.posting_date <= '{d_cm.isoformat()}'
        GROUP BY na.l6_na_mapping, na.l7_na_description, a.level_2, a.level_3,
                 NULLIF(TRIM(a.level_4), ''), l.account_number_group, l.entity_prefix
    """
    return sql, {}


def wc_consl_grain_sql_week(iso_year: int, iso_week: int) -> tuple[str, dict[str, Any]]:
    """FY-scoped WC balance at the anchor Sunday, per entity_prefix (raw sign)."""
    yr, _ = plan_anchor_for_week(iso_year, iso_week)
    _, d_cm = iso_week_bounds(iso_year, iso_week)
    sql = f"""
        SELECT
            {_WC_CONSL_GRAIN_DIMS},
            {_bal_case_fy(yr, d_cm, 'cm')}
        {_WC_FROM}
        {_WC_WHERE}
          AND e.posting_date <= '{d_cm.isoformat()}'
        GROUP BY na.l6_na_mapping, na.l7_na_description, a.level_2, a.level_3,
                 NULLIF(TRIM(a.level_4), ''), l.account_number_group, l.entity_prefix
    """
    return sql, {}


# ---------------------------------------------------------------------------
# Monthly grain (one cumulative-balance column per month-end)
# ---------------------------------------------------------------------------

def wc_monthly_grain_sql(
    year: int, month: int, ent_frag: str, *, span: str = "12m",
) -> tuple[str, dict[str, Any]]:
    """One column per period = the month-END FY-scoped WC balance (raw sign).

    Each ``YYYY-MM`` column uses Jan-1 opening balance of that calendar year plus
    in-year movements through month-end (same rule as ``bs_monthly_grain_sql``).

    span='12m' (default): last 12 month-end balances ending at (year, month).
    span='fy3': all months of :func:`_fy_span_periods` PLUS three summary balance
        columns (FY-2, FY-1, YTD at anchor month-end) — FY-scoped stocks, not sums.
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
        cases.append(_bal_case_fy(y, last_day(y, m), pk))
    for t in totals:
        if t["kind"] == "fy":
            fy = int(t["year"])
            cases.append(_bal_case_fy(fy, last_day(fy, 12), t["key"]))
        else:
            cases.append(_bal_case_fy(year, last_day(year, month), t["key"]))

    d_max = last_day(year, month)
    sql = f"""
        SELECT
            {_WC_GRAIN_DIMS},
            {', '.join(cases)}
        {_WC_FROM}
        {_WC_WHERE}
          AND e.posting_date <= '{d_max.isoformat()}'
          {ent_frag}
        {_WC_GROUP_BY}
    """
    return sql, {}


# ---------------------------------------------------------------------------
# L4 trend (cumulative balance at each window END — NOT a period sum)
# ---------------------------------------------------------------------------

def wc_l4_trend_sql(
    year: int, month: int, grain: str,
    level_2: str, level_3: str, level_4: str,
    ent_frag: str,
) -> tuple[str, list[dict]]:
    """WC L4 trend with CUMULATIVE balances (raw sign): each point = balance at the
    window's END date.  Like ``bs_l4_trend_sql`` but restricted to TWC/OWC accounts
    (mirrors legacy ``_bs_wc_l4_trend_sql`` with ``l6_na_mapping IN ('TWC','OWC')``).
    """
    windows = _trend_windows(year, month, grain)
    l2_f = f"AND TRIM(a.level_2) = '{_esc(level_2)}'" if level_2 else ""
    l3_f = f"AND TRIM(a.level_3) = '{_esc(level_3)}'" if level_3 else ""
    l4_f = f"AND NULLIF(TRIM(a.level_4),'') = '{_esc(level_4)}'" if level_4 else ""
    d_max = last_day(year, month)

    cases: list[str] = []
    for w in windows:
        cases.append(_bal_case(w["cur_end"], w["pk"]))
        cases.append(_bal_case(w["prev_end"], w["prev_pk"]))

    sql = f"""
        SELECT {', '.join(cases)}
        {_WC_FROM_BAL}
        {_WC_WHERE}
          {l2_f} {l3_f} {l4_f}
          AND e.posting_date <= '{d_max.isoformat()}'
          {ent_frag}
    """
    return _wrap_bs_bal_sql(sql), windows


# ---------------------------------------------------------------------------
# Annual exit-readiness snapshot grain (fy_py / fy / cm_py / cm)
# ---------------------------------------------------------------------------

def wc_snapshot_grain_sql(year: int, month: int, ent_frag: str) -> tuple[str, dict[str, Any]]:
    """FY-scoped WC balances at the 5 ER snapshot cutoffs (raw sign, no ``*-1``).

    Cutoffs identical to ``bs_snapshot_grain_sql``; restricted to TWC/OWC.
    """
    d_cm = last_day(year, month)
    cases = _bs_snapshot_bal_cases(year, month)
    sql = f"""
        SELECT
            {_WC_GRAIN_DIMS},
            {cases}
        {_WC_FROM}
        {_WC_WHERE}
          AND e.posting_date <= '{d_cm.isoformat()}'
          {ent_frag}
        {_WC_GROUP_BY}
    """
    return sql, {}


# ---------------------------------------------------------------------------
# WC timeline (running cumulative TWC components at each period end)
# ---------------------------------------------------------------------------

def _wc_timeline_cuts(year: int, month: int, grain: str) -> list[date]:
    """Period-end cut dates for the WC timeline (mirrors legacy windows).

    month → 36 month-ends (3 × 12) ; week → 39 ISO-week Sundays (3 × 13) ;
    day → 60 calendar days (2 × 30).  All ending at last_day(year, month).
    """
    from datetime import timedelta

    if grain == "month":
        periods = _last_12_periods(year, month)
        y0, m0 = periods[0]
        extra: list[tuple[int, int]] = []
        y_, m_ = y0, m0
        for _ in range(24):
            m_ -= 1
            if m_ == 0:
                m_ = 12
                y_ -= 1
            extra.insert(0, (y_, m_))
        all_periods = extra + list(periods)
        return [last_day(y, m) for y, m in all_periods]

    if grain == "week":
        d_end = last_day(year, month)
        cuts: list[date] = []
        d = d_end
        for _ in range(39):
            days_to_sun = (6 - d.weekday()) % 7
            cuts.insert(0, d + timedelta(days=days_to_sun))
            d -= timedelta(weeks=1)
        return sorted(set(cuts))[-39:]

    # day — 60 days
    d_end = last_day(year, month)
    return [d_end - timedelta(days=i) for i in range(59, -1, -1)]


def wc_timeline_period_meta(year: int, month: int, grain: str) -> list[dict[str, str]]:
    """Display meta (label + ISO date) per timeline cut date — DB-free, testable."""
    cuts = _wc_timeline_cuts(year, month, grain)
    if grain == "month":
        from app.services.fin_compat_sql import period_label
        return [{"label": period_label(c.year, c.month), "date": c.isoformat()} for c in cuts]
    if grain == "week":
        return [{"label": f"W{c.isocalendar()[1]:02d} {c.year}", "date": c.isoformat()} for c in cuts]
    return [{"label": c.strftime("%d.%m"), "date": c.isoformat()} for c in cuts]


def wc_timeline_sql(year: int, month: int, grain: str, ent_frag: str) -> tuple[str, list[dict[str, str]]]:
    """Running cumulative TWC components per period end (ported from legacy).

    Per posting_date the four TWC/OWC component magnitudes (``ABS(l.amount)``) are
    summed, then a window running-sum gives the cumulative balance, sampled at each
    cut date.  Components: Inventories / Trade receivables / Trade payables (TWC) +
    everything OWC.  Magnitudes (ABS) match the legacy ``_wc_timeline_sql``.
    """
    periods_meta = wc_timeline_period_meta(year, month, grain)
    cuts = _wc_timeline_cuts(year, month, grain)
    d_max = max(cuts)
    values_clause = ", ".join(f"('{c.isoformat()}'::date)" for c in cuts)
    sql = f"""
        WITH daily_net AS (
            SELECT e.posting_date,
                SUM(CASE WHEN TRIM(a.level_3) = '{WC_INV_L3}'
                         AND na.l6_na_mapping = 'TWC' THEN ABS(l.amount) ELSE 0 END) AS inv,
                SUM(CASE WHEN TRIM(a.level_3) = '{WC_REC_L3}'
                         AND na.l6_na_mapping = 'TWC' THEN ABS(l.amount) ELSE 0 END) AS rec,
                SUM(CASE WHEN TRIM(a.level_3) = '{WC_PAY_L3}'
                         AND na.l6_na_mapping = 'TWC' THEN ABS(l.amount) ELSE 0 END) AS pay,
                SUM(CASE WHEN na.l6_na_mapping = 'OWC'        THEN ABS(l.amount) ELSE 0 END) AS owc
            {_WC_FROM}
            {_WC_WHERE}
              AND e.posting_date <= '{d_max.isoformat()}'
              {ent_frag}
            GROUP BY e.posting_date
        ),
        running AS (
            SELECT posting_date,
                SUM(inv) OVER (ORDER BY posting_date) AS inventories,
                SUM(rec) OVER (ORDER BY posting_date) AS trade_receivables,
                SUM(pay) OVER (ORDER BY posting_date) AS trade_payables,
                SUM(owc) OVER (ORDER BY posting_date) AS other_wc
            FROM daily_net
        ),
        period_list (cut_date) AS (
            VALUES {values_clause}
        )
        SELECT DISTINCT ON (pl.cut_date)
            pl.cut_date,
            COALESCE(r.inventories,       0) AS inventories,
            COALESCE(r.trade_receivables, 0) AS trade_receivables,
            COALESCE(r.trade_payables,    0) AS trade_payables,
            COALESCE(r.other_wc,          0) AS other_wc
        FROM period_list pl
        LEFT JOIN running r ON r.posting_date <= pl.cut_date
        ORDER BY pl.cut_date, r.posting_date DESC
    """
    return sql, periods_meta


# ---------------------------------------------------------------------------
# WC KPI denominators — LTM Revenue / COGS from the P&L (per cutoff window)
# ---------------------------------------------------------------------------

def wc_pl_window_sql(
    start: date, end: date, ent_frag: str, *, by_entity: bool = False,
) -> tuple[str, dict[str, Any]]:
    """(revenue, cogs) over a posting_date window [start, end] from the P&L.

        revenue = Σ ( l.amount * -1 )  where level_3 = 'Net sales'   (presented +)
        cogs    = |Σ l.amount|         where level_3 = 'Cost of materials' (positive magnitude)

    Mirrors legacy ``_wc_kpi_pl_ltm_through``.  When ``by_entity`` the result also
    carries ``entity_prefix`` (one row per entity) for the consolidation KPIs.
    """
    sel_entity = "l.entity_prefix," if by_entity else ""
    grp = "GROUP BY l.entity_prefix" if by_entity else ""
    sql = f"""
        SELECT
            {sel_entity}
            COALESCE(SUM(CASE WHEN TRIM(a.level_3) = '{PL_REV_L3}'
                         THEN l.amount * -1 ELSE 0 END), 0)::float8 AS revenue,
            ABS(COALESCE(SUM(CASE WHEN TRIM(a.level_3) = '{PL_COGS_L3}'
                         THEN l.amount ELSE 0 END), 0))::float8     AS cogs
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE a.level_0 = 'PL'
          AND TRIM(a.level_3) IN ('{PL_REV_L3}', '{PL_COGS_L3}')
          AND e.posting_date >= '{start.isoformat()}'
          AND e.posting_date <= '{end.isoformat()}'
          {ent_frag}
        {grp}
    """
    return sql, {}


def ltm_window(end: date) -> tuple[date, date]:
    """Rolling-12-month window ending at ``end`` (legacy LTM rule).

    start = (end.year-1, end.month+1, 1)  [or Jan 1 of end.year when month == 12];
    the window is the 12 months up to and including ``end``.
    """
    y, m = end.year, end.month
    start = date(y - 1, m + 1, 1) if m < 12 else date(y, 1, 1)
    return start, end
