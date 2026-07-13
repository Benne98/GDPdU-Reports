"""Legacy-compat SQL helpers for the **Balance Sheet** (GDPdU backend).

Companion to ``fin_compat_sql`` (which serves the P&L).  These helpers produce
the grain rows that ``fin_compat_bs`` turns into the legacy
FinancialStatementResponse / ConsolidationResponse / MonthlyResponse /
L4TrendResponse / ErSnapshotResponse shapes — but with **balance-sheet**
semantics, which differ from the P&L in two critical ways.

=== 1. CUMULATIVE STOCK, NOT PERIOD FLOW ===
P&L accounts are FLOW: a column is the Σ of in-period movements.  Balance-sheet
accounts are STOCK (cumulative balances): a column value is the CLOSING BALANCE
as of the END of that column's period — i.e. the cumulative Σ of ALL movements
with ``posting_date <= cutoff``.  Annual GoBD **opening_balance** rows (Jan-1
Anfangsbestand, one snapshot per account at ledger inception) are added once via
the earliest ``opening_balance`` posting date; later Jan-1 carry-forward rows
are excluded from the cumulative sum to avoid double-counting.

    COALESCE(SUM(movements <= cutoff), 0)
    + COALESCE(SUM(opening_balance at MIN(opening posting_date)), 0)
Column cutoffs (month grain):
    py_cm  = last_day(year-1, month)          # balance one year before the CM
    pm     = last_day(prior_month)            # balance at the prior month-end
    cm     = last_day(year, month)            # balance at the current month-end
    ytd    = cm                               # BS YTD == CM (a stock, not a sum)
    ytd_py = py_cm                            # BS YTD_PY == PY_CM
Week grain: cutoffs are the ISO-week Sunday ends (cw_t = anchor Sunday, pw_t =
prior week Sunday, spy_t = same week prior year Sunday); ``mtd`` = balance at the
anchor Sunday (== cm for a stock).

=== 2. NO ``amount * -1`` INVERSION ===
The P&L helpers apply ``amount * -1`` once in SQL (revenue + / expense −).  The
balance sheet keeps the **raw stored sign** here (``SUM(l.amount)``): assets
(debit-normal) come out POSITIVE, equity & liabilities (credit-normal) come out
NEGATIVE.  The display flip of the credit side happens ONCE in
``fin_compat_bs`` (``_flip_row_tree``), never in this SQL.  This is the
documented BS sign convention — deliberately DIFFERENT from the P&L rule.

Exception: the *net-profit* helpers (``bs_net_profit_sql_*``) read the **P&L**
(level_0='PL') YTD result and therefore DO use ``amount * -1`` (P&L rule), so the
income result is positive when injected into the equity section.

=== SCHEMA MAPPING (same as fin_compat_sql) ===
  fact_gl_line l  JOIN fact_gl_entry e
      ON e.journal_entry_group_number = l.journal_entry_group_number
     AND e.fiscal_year = l.fiscal_year
  JOIN dim_gl_account a
      ON a.account_number_group = l.account_number_group
     AND a.fiscal_year = l.fiscal_year
  a.level_0 = 'BS'              (P&L uses 'PL')
  entity filter via l.entity_prefix  ({ent_frag}; resolved from legal_entity_code)
Cumulative balances scan EVERY fiscal year up to the cutoff (the join on
fiscal_year picks each line's own account dims), so opening balances and all
prior periods accumulate into the current stock — exactly how a ledger balance
builds up.

=== WORKED EXAMPLE (cumulative, month grain, account Cash, asset) ===
  Stored movements (amount + = debit):
    2024-01 +1000 (opening), 2025-03 +100, 2025-07 +50
  For year=2025, month=07:
    cm (<= 2025-07-31)  = 1000 + 100 + 50 = 1150   (raw, positive asset)
    pm (<= 2025-06-30)  = 1000 + 100      = 1100
    py_cm (<= 2024-07-31)= 1000
    ytd = cm = 1150 ; ytd_py = py_cm = 1000
  A payables account (credit) stored −400 cumulative → cm = −400 (raw); the
  display flip in fin_compat_bs presents it as +400.

=== EDGE CASES ===
  * No movements up to a cutoff → COALESCE(SUM,0) → 0 balance for that column.
  * fiscal_period 13 (consolidation) is date-based here (posting_date), so it is
    naturally included only if it carries a real posting_date; the P&L net-profit
    helpers below keep the legacy posting_date windows (period-agnostic).
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
    period_key,
    plan_anchor_for_week,
    pm,
    prior_iso_week,
    same_week_prior_year,
)

# Standard month/week column keys (identical to the P&L statement schema).
_BS_KEYS_MONTH = ["py_cm", "pm", "cm", "ytd", "ytd_py"]
_BS_KEYS_WEEK = ["py_cm", "pm", "cm", "ytd", "ytd_py", "mtd"]
# Annual exit-readiness snapshot column keys.
_BS_SNAP_KEYS = ["dec_py2", "fy_py", "fy", "cm_py", "cm"]

# Common SELECT prefix + JOINs for the per-line BS grain (no entity_prefix col).
_BS_GRAIN_DIMS = """
            COALESCE(NULLIF(TRIM(a.level_1), ''), '—') AS level_1,
            COALESCE(NULLIF(TRIM(a.level_2), ''), '—') AS level_2,
            COALESCE(NULLIF(TRIM(a.level_3), ''), '—') AS level_3,
            COALESCE(NULLIF(TRIM(a.level_4), ''), '—') AS level_4,
            a.gl_account_id,
            MAX(a.account_name) AS account_name,
            COALESCE(MAX(a.level_1_sort::int), 9999) AS level_1_sort,
            COALESCE(MAX(a.level_2_sort::int), 9999) AS level_2_sort,
            COALESCE(MAX(a.level_3_sort::int), 9999) AS level_3_sort,
            COALESCE(MAX(a.level_4_sort::int), 9999) AS level_4_sort"""

# reporting-v2 Phase 3: synthetic net-profit equity rows (entry_type='net_profit')
# are filtered out at the JOIN so they NEVER produce a grain group.  The SUM-level
# guard alone is not enough: hierarchy_from_grains builds a node for EVERY distinct
# grain group, so a present-but-zero net-profit account row would create a phantom
# 'Net profit' hierarchy child (live has no such GL row → no node).  Filtering here
# reproduces live exactly.  Harmless on the P&L net-profit SQL (level_0='PL', no
# net_profit rows), so the report-injection path is unchanged.
_BS_FROM = """
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
         AND COALESCE(e.entry_type, '') <> 'net_profit'
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year"""

_BS_GROUP_BY = (
    "GROUP BY a.level_1, a.level_2, a.level_3, a.level_4, a.gl_account_id"
)


#: reporting-v2 Phase 3 guard: TRUE for every NON net-profit row.  Synthetic
#: net-profit equity bookings (etl.net_profit, entry_type='net_profit') are
#: single-sided rows that make the *ledger* balance per FY; they must be EXCLUDED
#: from the BS cumulative-balance grain so the displayed equity hierarchy nodes +
#: subtotals stay byte-identical to the legacy report-injection path (the
#: presented 'Net profit' line is still produced by _inject_net_profit from the
#: P&L SQL).  Used inside every BS balance SUM CASE WHEN.
_NOT_NET_PROFIT = "COALESCE(e.entry_type, '') <> 'net_profit'"


def _sql_alias(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _opening_snapshot_date_sql() -> str:
    """Earliest GoBD opening-balance snapshot date in fact_gl_entry."""
    return "(SELECT MIN(e0.posting_date) FROM fact_gl_entry e0 WHERE e0.entry_type = 'opening_balance')"


def _last_ob_date_sql(cutoff_iso: str) -> str:
    """Per account_number_group: latest opening_balance date on or before cutoff."""
    return f"""(
        SELECT MAX(e_lo.posting_date)
        FROM fact_gl_entry e_lo
        INNER JOIN fact_gl_line l_lo
          ON e_lo.journal_entry_group_number = l_lo.journal_entry_group_number
         AND e_lo.fiscal_year = l_lo.fiscal_year
        WHERE e_lo.entry_type = 'opening_balance'
          AND e_lo.posting_date <= '{cutoff_iso}'
          AND l_lo.account_number_group = l.account_number_group
    )"""


_BAL_MOV_CTE = """
    bal_mov AS (
        SELECT DISTINCT l_mv.account_number_group
        FROM fact_gl_line l_mv
        JOIN fact_gl_entry e_mv
          ON e_mv.journal_entry_group_number = l_mv.journal_entry_group_number
         AND e_mv.fiscal_year = l_mv.fiscal_year
        WHERE COALESCE(e_mv.entry_type, '') != 'opening_balance'
    )"""

_BS_FROM_BAL = (
    _BS_FROM
    + """
        LEFT JOIN bal_mov bm
          ON bm.account_number_group = l.account_number_group"""
)


def _wrap_bs_bal_sql(sql: str) -> str:
    """Prepend bal_mov CTE for hybrid opening-balance balance math."""
    return f"WITH {_BAL_MOV_CTE}\n{sql.strip()}"


def _bal_amount_expr(cutoff: date) -> str:
    """SQL expression: cumulative BS balance at cutoff (raw stored sign).

    Hybrid opening-balance policy (GoBD Decidra):
    * Accounts with **any** non-opening movement: earliest OB snapshot (2022-01-01)
      plus all subsequent non-OB postings through cutoff (stock built from movements).
    * **OB-only** accounts (e.g. 38818 Profit distribution — annual Start value only):
      balance = opening_balance on the latest OB date <= cutoff (no 2022 seed).
    """
    d = cutoff.isoformat()
    snap = _opening_snapshot_date_sql()
    last_ob = _last_ob_date_sql(d)
    # reporting-v2 Phase 3: synthetic net-profit equity rows (entry_type='net_profit')
    # are EXCLUDED from the BS cumulative balance so the equity hierarchy nodes +
    # subtotals stay byte-identical to the report-injection path (no double count).
    return (
        f"COALESCE(SUM(CASE WHEN {_NOT_NET_PROFIT} THEN "
        "CASE "
        "WHEN bm.account_number_group IS NOT NULL THEN "
        f"CASE "
        f"WHEN COALESCE(e.entry_type, '') != 'opening_balance' "
        f"AND e.posting_date <= '{d}' THEN l.amount "
        f"WHEN e.entry_type = 'opening_balance' AND e.posting_date = {snap} THEN l.amount "
        f"ELSE 0 END "
        "ELSE "
        f"CASE "
        f"WHEN e.entry_type = 'opening_balance' "
        f"AND e.posting_date = {last_ob} "
        f"AND e.posting_date <= '{d}' THEN l.amount "
        f"ELSE 0 END "
        "END ELSE 0 END), 0)"
    )


def _bal_case(cutoff: date, alias: str) -> str:
    """Cumulative balance CASE WHEN (raw stored sign, no ``* -1``)."""
    return f"{_bal_amount_expr(cutoff)} AS {_sql_alias(alias)}"


def _bal_amount_expr_fy(fiscal_year: int, cutoff: date) -> str:
    """SQL expression: fiscal-year-scoped BS balance at cutoff (raw stored sign).

    Balance within a single ``fiscal_year`` ledger context: Jan-1
    ``opening_balance`` for that FY plus all non-OB postings in the same
    ``fiscal_year`` through ``cutoff``.  Prior-FY postings on the same
    ``account_number_group`` (e.g. entity hand-off) do not carry into the
    anchor FY column — Jul25A is FY2025 YTD, not lifetime hybrid cumulative.
    """
    d = cutoff.isoformat()
    ob = f"DATE '{fiscal_year}-01-01'"
    # reporting-v2 Phase 3: exclude synthetic net-profit equity rows from the
    # FY-scoped BS balance (see _bal_amount_expr); keeps presentation identical.
    return (
        "COALESCE(SUM(CASE "
        f"WHEN e.fiscal_year = {fiscal_year} "
        f"AND {_NOT_NET_PROFIT} "
        f"AND e.entry_type = 'opening_balance' AND e.posting_date = {ob} THEN l.amount "
        f"WHEN e.fiscal_year = {fiscal_year} "
        f"AND {_NOT_NET_PROFIT} "
        f"AND COALESCE(e.entry_type, '') != 'opening_balance' "
        f"AND e.posting_date <= '{d}' THEN l.amount "
        "ELSE 0 END), 0)"
    )


def _bal_case_fy(fiscal_year: int, cutoff: date, alias: str) -> str:
    """FY-scoped balance CASE WHEN (raw stored sign, no ``* -1``)."""
    return f"{_bal_amount_expr_fy(fiscal_year, cutoff)} AS {_sql_alias(alias)}"


def _bs_snapshot_bal_cases(year: int, month: int) -> str:
    """Five ER snapshot columns — each scoped to its fiscal year."""
    d_dec_py2, d_fy_py, d_fy = _bs_snapshot_year_end_cutoffs(year)
    d_cm_py = last_day(year - 1, month)
    d_cm = last_day(year, month)
    pairs: list[tuple[int, date, str]] = [
        (year - 3, d_dec_py2, "dec_py2"),
        (year - 2, d_fy_py, "fy_py"),
        (year - 1, d_fy, "fy"),
        (year - 1, d_cm_py, "cm_py"),
        (year, d_cm, "cm"),
    ]
    return ", ".join(_bal_case_fy(fy, cutoff, alias) for fy, cutoff, alias in pairs)


# ---------------------------------------------------------------------------
# Statement grain — month
# ---------------------------------------------------------------------------

def bs_grain_sql_month(year: int, month: int, ent_frag: str) -> tuple[str, dict[str, Any]]:
    """Cumulative BS balances for the 5 standard columns (raw sign, no ``*-1``).

    Cutoffs: py_cm/ytd_py = last_day(year-1, month); pm = last_day(prior month);
    cm/ytd = last_day(year, month).  ytd == cm and ytd_py == py_cm because a BS
    column is a stock (the closing balance at the cutoff), not a period sum.

    EACH COLUMN IS FY-SCOPED (``_bal_case_fy``): that fiscal year's Jan-1 opening
    balance plus in-FY movements through the cutoff — IDENTICAL to
    :func:`bs_consl_grain_sql_month`, :func:`bs_snapshot_grain_sql`,
    :func:`bs_monthly_grain_sql` and ``build_bs_trial_balance``.  This is what makes
    the whole-group (entity=None) statement TIE OUT (Total assets == Total equity &
    liabilities) the same way the consolidation does under
    ``OPENING_BALANCE_MODE=in_data`` — where retained earnings are carried as a
    per-FY Jan-1 opening snapshot (Saldovortrag), NOT as closing movements.  The
    former lifetime-hybrid ``_bal_case`` kept only the EARLIEST opening snapshot and
    dropped every later carry-forward, so prior-year retained earnings vanished from
    equity while assets kept all cumulative movements → a spurious imbalance.

    Each column's FY is aligned to the matching P&L net-profit window in
    :func:`bs_net_profit_sql_month` (py_cm/ytd_py → FY ``year-1``; pm/cm/ytd → FY
    ``year``), so ``all_bs_raw == Σ P&L`` per column and ``bs_imbalance_from_grains``
    returns 0 on a balanced ledger.
    """
    pm_y, pm_m = pm(year, month)
    d_py = last_day(year - 1, month)
    d_pm = last_day(pm_y, pm_m)
    d_cm = last_day(year, month)
    cases = ", ".join([
        _bal_case_fy(year - 1, d_py, "py_cm"),
        _bal_case_fy(year, d_pm, "pm"),
        _bal_case_fy(year, d_cm, "cm"),
        _bal_case_fy(year, d_cm, "ytd"),
        _bal_case_fy(year - 1, d_py, "ytd_py"),
    ])
    sql = f"""
        SELECT
            {_BS_GRAIN_DIMS},
            {cases}
        {_BS_FROM}
        WHERE a.level_0 = 'BS'
          AND e.posting_date <= '{d_cm.isoformat()}'
          {ent_frag}
        {_BS_GROUP_BY}
    """
    return sql, {}


# ---------------------------------------------------------------------------
# Statement grain — week
# ---------------------------------------------------------------------------

def bs_grain_sql_week(iso_year: int, iso_week: int, ent_frag: str) -> tuple[str, dict[str, Any]]:
    """Cumulative BS balances at ISO-week Sunday cutoffs (raw sign, + ``mtd``).

    cm/ytd/mtd  = balance at the anchor Sunday (cw_t);
    pm          = balance at the prior week's Sunday (pw_t);
    py_cm/ytd_py= balance at the same-week-prior-year Sunday (spy_t).
    ``mtd`` (month-to-date) equals cm for a stock — the balance AT the anchor
    Sunday is the cumulative balance — and is carried so the week schema matches.

    EACH COLUMN IS FY-SCOPED (``_bal_case_fy``), exactly like the month grain and
    the consolidation, so the whole-group week statement ties out (Total assets ==
    Total equity & liabilities) under ``OPENING_BALANCE_MODE=in_data``.  Column FYs
    match the P&L net-profit windows in :func:`bs_net_profit_sql_week`
    (cm/ytd/mtd/pm → FY ``iso_year``; py_cm/ytd_py → FY ``spy_y``).  See
    :func:`bs_grain_sql_month` for the full rationale.
    """
    _, d_cm = iso_week_bounds(iso_year, iso_week)
    pw_y, pw_w = prior_iso_week(iso_year, iso_week)
    _, d_pm = iso_week_bounds(pw_y, pw_w)
    spy_y, spy_w = same_week_prior_year(iso_year, iso_week)
    _, d_py = iso_week_bounds(spy_y, spy_w)
    cases = ", ".join([
        _bal_case_fy(spy_y, d_py, "py_cm"),
        _bal_case_fy(iso_year, d_pm, "pm"),
        _bal_case_fy(iso_year, d_cm, "cm"),
        _bal_case_fy(iso_year, d_cm, "ytd"),
        _bal_case_fy(spy_y, d_py, "ytd_py"),
        _bal_case_fy(iso_year, d_cm, "mtd"),
    ])
    sql = f"""
        SELECT
            {_BS_GRAIN_DIMS},
            {cases}
        {_BS_FROM}
        WHERE a.level_0 = 'BS'
          AND e.posting_date <= '{d_cm.isoformat()}'
          {ent_frag}
        {_BS_GROUP_BY}
    """
    return sql, {}


# ---------------------------------------------------------------------------
# Net-profit grain (P&L YTD result at each BS balance date) — uses ``* -1``
# ---------------------------------------------------------------------------

def bs_net_profit_sql_month(year: int, month: int, ent_frag: str) -> tuple[str, dict[str, Any]]:
    """P&L net-profit (YTD) at each BS month cutoff, presented (income +).

    The injected equity "Net profit" row carries the YTD P&L result up to each
    balance date.  Uses the P&L sign rule (``amount * -1``) so income is +.
        py_cm  = Σ_{(year-1)-01-01 .. last_day(year-1, month)}
        pm     = Σ_{year-01-01     .. last_day(prior month)}
        cm     = Σ_{year-01-01     .. last_day(year, month)}
        ytd    = cm
        ytd_py = py_cm
    """
    pm_y, pm_m = pm(year, month)
    d_py = last_day(year - 1, month).isoformat()
    d_pm = last_day(pm_y, pm_m).isoformat()
    d_cm = last_day(year, month).isoformat()
    sql = f"""
        SELECT
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{year-1}-01-01' AND '{d_py}'
                THEN l.amount * -1 ELSE 0 END), 0) AS py_cm,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{year}-01-01' AND '{d_pm}'
                THEN l.amount * -1 ELSE 0 END), 0) AS pm,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{year}-01-01' AND '{d_cm}'
                THEN l.amount * -1 ELSE 0 END), 0) AS cm,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{year}-01-01' AND '{d_cm}'
                THEN l.amount * -1 ELSE 0 END), 0) AS ytd,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{year-1}-01-01' AND '{d_py}'
                THEN l.amount * -1 ELSE 0 END), 0) AS ytd_py
        {_BS_FROM}
        WHERE a.level_0 = 'PL'
          {ent_frag}
    """
    return sql, {}


def bs_net_profit_sql_week(iso_year: int, iso_week: int, ent_frag: str) -> tuple[str, dict[str, Any]]:
    """P&L net-profit (YTD) at each BS week cutoff, presented (income +). + ``mtd``."""
    _, d_cm = iso_week_bounds(iso_year, iso_week)
    pw_y, pw_w = prior_iso_week(iso_year, iso_week)
    _, d_pm = iso_week_bounds(pw_y, pw_w)
    spy_y, spy_w = same_week_prior_year(iso_year, iso_week)
    _, d_py = iso_week_bounds(spy_y, spy_w)
    ytd_f = date(iso_year, 1, 1).isoformat()
    ytd_py_f = date(spy_y, 1, 1).isoformat()
    sql = f"""
        SELECT
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{ytd_py_f}' AND '{d_py.isoformat()}'
                THEN l.amount * -1 ELSE 0 END), 0) AS py_cm,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{ytd_f}' AND '{d_pm.isoformat()}'
                THEN l.amount * -1 ELSE 0 END), 0) AS pm,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{ytd_f}' AND '{d_cm.isoformat()}'
                THEN l.amount * -1 ELSE 0 END), 0) AS cm,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{ytd_f}' AND '{d_cm.isoformat()}'
                THEN l.amount * -1 ELSE 0 END), 0) AS ytd,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{ytd_py_f}' AND '{d_py.isoformat()}'
                THEN l.amount * -1 ELSE 0 END), 0) AS ytd_py,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{ytd_f}' AND '{d_cm.isoformat()}'
                THEN l.amount * -1 ELSE 0 END), 0) AS mtd
        {_BS_FROM}
        WHERE a.level_0 = 'PL'
          {ent_frag}
    """
    return sql, {}


def bs_monthly_net_profit_sql(
    year: int, month: int, ent_frag: str, *, span: str = "12m",
) -> tuple[str, dict[str, Any]]:
    """P&L net-profit YTD at each monthly BS column cutoff, presented (income +).

    Each period column ``YYYY-MM`` = Σ P&L from Jan-1 of that year through
    ``last_day(y, m)``.  FY/YTD summary columns (span='fy3') use the same
    posting_date windows as :func:`bs_snapshot_net_profit_sql`.
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
        d_end = last_day(y, m).isoformat()
        cases.append(
            f"COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{y}-01-01' AND '{d_end}' "
            f"THEN l.amount * -1 ELSE 0 END), 0) AS \"{pk}\""
        )
    for t in totals:
        if t["kind"] == "fy":
            fy = int(t["year"])
            d_end = last_day(fy, 12).isoformat()
            cases.append(
                f"COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{fy}-01-01' AND '{d_end}' "
                f"THEN l.amount * -1 ELSE 0 END), 0) AS \"{t['key']}\""
            )
        else:  # ytd
            d_end = last_day(year, month).isoformat()
            cases.append(
                f"COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{year}-01-01' AND '{d_end}' "
                f"THEN l.amount * -1 ELSE 0 END), 0) AS \"{t['key']}\""
            )

    sql = f"""
        SELECT {', '.join(cases)}
        {_BS_FROM}
        WHERE a.level_0 = 'PL'
          {ent_frag}
    """
    return sql, {}


def bs_consl_net_profit_sql_month(year: int, month: int) -> tuple[str, dict[str, Any]]:
    """P&L net-profit YTD per entity_prefix at the month cutoff (presented, income +)."""
    d_cm = last_day(year, month).isoformat()
    sql = f"""
        SELECT
            l.entity_prefix,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{year}-01-01' AND '{d_cm}'
                THEN l.amount * -1 ELSE 0 END), 0) AS cm
        {_BS_FROM}
        WHERE a.level_0 = 'PL'
        GROUP BY l.entity_prefix
    """
    return sql, {}


def bs_consl_net_profit_sql_annual(year: int, month: int) -> tuple[str, dict[str, Any]]:
    """P&L net-profit at ER snapshot cutoffs, per entity_prefix (presented, income +)."""
    d_dec_py2, d_fy_py, d_fy = _bs_snapshot_year_end_cutoffs(year)
    d_cm_py = last_day(year - 1, month)
    d_cm = last_day(year, month)
    pairs = [
        ("dec_py2", year - 3, d_dec_py2),
        ("fy_py", year - 2, d_fy_py),
        ("fy", year - 1, d_fy),
        ("cm_py", year - 1, d_cm_py),
        ("cm", year, d_cm),
    ]
    cases = ", ".join(
        f"COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{fy}-01-01' AND '{cutoff.isoformat()}' "
        f"THEN l.amount * -1 ELSE 0 END), 0) AS \"{alias}\""
        for alias, fy, cutoff in pairs
    )
    sql = f"""
        SELECT l.entity_prefix, {cases}
        {_BS_FROM}
        WHERE a.level_0 = 'PL'
        GROUP BY l.entity_prefix
    """
    return sql, {}


def _bs_snapshot_year_end_cutoffs(year: int) -> tuple[date, date, date]:
    """Closing balances for Dec-labelled snapshot columns (not Jan-1 Saldovortrag).

    dec_py2 → Dec 31 of (year−3); fy_py → Dec 31 of (year−2); fy → Dec 31 of (year−1).
    Labels say ``Dec22A`` etc.; the stock is the year-end balance, excluding postings
    on the following Jan 1 (opening entries / early-year movements).
    """
    return last_day(year - 3, 12), last_day(year - 2, 12), last_day(year - 1, 12)


# ---------------------------------------------------------------------------
# Consolidation grain (per entity_prefix) — single cumulative ``cm`` column
# ---------------------------------------------------------------------------

_BS_CONSL_GRAIN_DIMS = """
            COALESCE(NULLIF(TRIM(a.level_1), ''), '—') AS level_1,
            COALESCE(NULLIF(TRIM(a.level_2), ''), '—') AS level_2,
            COALESCE(NULLIF(TRIM(a.level_3), ''), '—') AS level_3,
            NULLIF(TRIM(a.level_4), '') AS level_4,
            l.account_number_group,
            l.entity_prefix,
            MAX(a.gl_account_id) AS gl_account_id,
            MAX(a.account_name) AS account_name,
            COALESCE(MAX(a.level_1_sort::int), 9999) AS level_1_sort,
            COALESCE(MAX(a.level_2_sort::int), 9999) AS level_2_sort,
            COALESCE(MAX(a.level_3_sort::int), 9999) AS level_3_sort,
            COALESCE(MAX(a.level_4_sort::int), 9999) AS level_4_sort"""

_BS_CONSL_GROUP_BY = (
    "GROUP BY a.level_1, a.level_2, a.level_3, NULLIF(TRIM(a.level_4), ''), "
    "l.account_number_group, l.entity_prefix"
)


def bs_consl_grain_sql_annual(year: int, month: int) -> tuple[str, dict[str, Any]]:
    """FY-scoped BS balances at ER snapshot cutoffs, per entity_prefix (raw sign).

    Same cutoffs as :func:`bs_snapshot_grain_sql` (dec_py2 / fy_py / fy / cm_py / cm).
    """
    d_cm = last_day(year, month)
    cases = _bs_snapshot_bal_cases(year, month)
    sql = f"""
        SELECT
            {_BS_CONSL_GRAIN_DIMS},
            {cases}
        {_BS_FROM}
        WHERE a.level_0 = 'BS'
          AND e.posting_date <= '{d_cm.isoformat()}'
        {_BS_CONSL_GROUP_BY}
    """
    return sql, {}


def bs_consl_grain_sql_month(year: int, month: int) -> tuple[str, dict[str, Any]]:
    """FY-scoped BS balance at the month cutoff, per entity_prefix (raw sign)."""
    d_cm = last_day(year, month)
    sql = f"""
        SELECT
            {_BS_CONSL_GRAIN_DIMS},
            {_bal_case_fy(year, d_cm, 'cm')}
        {_BS_FROM}
        WHERE a.level_0 = 'BS'
          AND e.posting_date <= '{d_cm.isoformat()}'
        {_BS_CONSL_GROUP_BY}
    """
    return sql, {}


def bs_consl_grain_sql_week(iso_year: int, iso_week: int) -> tuple[str, dict[str, Any]]:
    """FY-scoped BS balance at the anchor Sunday, per entity_prefix (raw sign)."""
    yr, mo = plan_anchor_for_week(iso_year, iso_week)
    _, d_cm = iso_week_bounds(iso_year, iso_week)
    sql = f"""
        SELECT
            {_BS_CONSL_GRAIN_DIMS},
            {_bal_case_fy(yr, d_cm, 'cm')}
        {_BS_FROM}
        WHERE a.level_0 = 'BS'
          AND e.posting_date <= '{d_cm.isoformat()}'
        {_BS_CONSL_GROUP_BY}
    """
    return sql, {}


# ---------------------------------------------------------------------------
# Monthly grain (one cumulative-balance column per period)
# ---------------------------------------------------------------------------

def bs_monthly_grain_sql(
    year: int, month: int, ent_frag: str, *, span: str = "12m",
) -> tuple[str, dict[str, Any]]:
    """One column per period = the month-END cumulative balance (raw sign).

    Each ``YYYY-MM`` column is FY-scoped (Jan-1 OB of that calendar year plus
    in-year movements through month-end), matching the ER snapshot / statement
    anchor columns — not lifetime hybrid cumulative.

    span='12m' (default): the last 12 month-end balances ending at (year, month).
    span='fy3': all months of :func:`_fy_span_periods` PLUS three summary balance
        columns (FY-2, FY-1, YTD): year-end / YTD stocks, each FY-scoped.
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
            {_BS_GRAIN_DIMS},
            {', '.join(cases)}
        {_BS_FROM}
        WHERE a.level_0 = 'BS'
          AND e.posting_date <= '{d_max.isoformat()}'
          {ent_frag}
        {_BS_GROUP_BY}
    """
    return sql, {}


# ---------------------------------------------------------------------------
# L4 trend (cumulative balance at each window END — NOT a period sum)
# ---------------------------------------------------------------------------

def bs_l4_trend_sql(
    year: int, month: int, grain: str,
    level_2: str, level_3: str, level_4: str,
    ent_frag: str,
) -> tuple[str, list[dict]]:
    """L4 trend with CUMULATIVE balances (raw sign): each point = balance at the
    window's END date (``cur_end`` / ``prev_end``), NOT the in-window movement.

    Mirrors the legacy ``_bs_wc_l4_trend_sql`` (balance-case) ported to the GDPdU
    schema.  Reuses :func:`_trend_windows` (year/quarter/month grains).
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
        {_BS_FROM_BAL}
        WHERE a.level_0 = 'BS'
          {l2_f} {l3_f} {l4_f}
          AND e.posting_date <= '{d_max.isoformat()}'
          {ent_frag}
    """
    return _wrap_bs_bal_sql(sql), windows


# ---------------------------------------------------------------------------
# Annual exit-readiness snapshot grain (fy_py / fy / cm_py / cm)
# ---------------------------------------------------------------------------

def bs_snapshot_grain_sql(year: int, month: int, ent_frag: str) -> tuple[str, dict[str, Any]]:
    """FY-scoped BS balances at 5 ER snapshot cutoffs (raw sign, no ``*-1``).

    Each column is scoped to its fiscal year (Jan-1 OB + in-FY movements through
    cutoff), not lifetime hybrid cumulative.  dec_py2 / fy_py / fy = year-end
    closing balances (Dec 31 of FY year−3/−2/−1); cm_py = FY(year−1) YTD at
    last_day(year−1, month); cm = FY(year) YTD at last_day(year, month).
    """
    d_cm = last_day(year, month)
    cases = _bs_snapshot_bal_cases(year, month)
    sql = f"""
        SELECT
            {_BS_GRAIN_DIMS},
            {cases}
        {_BS_FROM}
        WHERE a.level_0 = 'BS'
          AND e.posting_date <= '{d_cm.isoformat()}'
          {ent_frag}
        {_BS_GROUP_BY}
    """
    return sql, {}


def bs_snapshot_net_profit_sql(year: int, month: int, ent_frag: str) -> tuple[str, dict[str, Any]]:
    """P&L net-profit at the 4 ER snapshot dates, presented (income +, ``*-1``).

        fy_py = Σ_{(year-2)-01-01 .. last_day(year-2, 12)}   (full FY year-2)
        fy    = Σ_{(year-1)-01-01 .. last_day(year-1, 12)}   (full FY year-1)
        cm_py = Σ_{(year-1)-01-01 .. last_day(year-1, month)}(YTD prior year)
        cm    = Σ_{year-01-01     .. last_day(year, month)}  (YTD current year)
    (Mirrors legacy exit_readiness._er_bs_net_profit_sql.)
    """
    d_dec_py2 = last_day(year - 3, 12).isoformat()
    d_fy_py = last_day(year - 2, 12).isoformat()
    d_fy = last_day(year - 1, 12).isoformat()
    d_cm_py = last_day(year - 1, month).isoformat()
    d_cm = last_day(year, month).isoformat()
    sql = f"""
        SELECT
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{year-3}-01-01' AND '{d_dec_py2}'
                THEN l.amount * -1 ELSE 0 END), 0) AS dec_py2,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{year-2}-01-01' AND '{d_fy_py}'
                THEN l.amount * -1 ELSE 0 END), 0) AS fy_py,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{year-1}-01-01' AND '{d_fy}'
                THEN l.amount * -1 ELSE 0 END), 0) AS fy,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{year-1}-01-01' AND '{d_cm_py}'
                THEN l.amount * -1 ELSE 0 END), 0) AS cm_py,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{year}-01-01' AND '{d_cm}'
                THEN l.amount * -1 ELSE 0 END), 0) AS cm
        {_BS_FROM}
        WHERE a.level_0 = 'PL'
          {ent_frag}
    """
    return sql, {}


# ---------------------------------------------------------------------------
# Provision roll-forward grain (Provisions & accruals; cumulative + movement)
# ---------------------------------------------------------------------------

def bs_provision_grain_sql(year: int, month: int, ent_frag: str) -> tuple[str, dict[str, Any]]:
    """GL-derived provision roll-forward grain (presented, ``*-1`` like legacy).

    Provisions are credit-normal liabilities; the legacy presents them with
    ``amount * -1`` so a provision balance reads positive.  Per (level_3, level_4):
        opening_balance = cumulative balance at last_day(prior month)
        closing_balance = cumulative balance at last_day(year, month)
        period_movement = Σ over [first-of-CM .. last_day(year, month)]
    Filtered to ``level_2 = 'Provisions & accruals'`` (level_0='BS').  Returns an
    empty result set when the GL has no such accounts (handled as a 0-fallback by
    the builder).
    """
    pm_y, pm_m = pm(year, month)
    d_pm = last_day(pm_y, pm_m).isoformat()
    d_cm = last_day(year, month).isoformat()
    ja = f"{year}-{month:02d}-01"
    d_open = last_day(pm_y, pm_m)
    d_close = last_day(year, month)
    bal_open = f"(({_bal_amount_expr(d_open)}) * -1)"
    bal_close = f"(({_bal_amount_expr(d_close)}) * -1)"
    sql = f"""
        SELECT
            TRIM(a.level_3) AS level_3,
            TRIM(COALESCE(NULLIF(a.level_4, ''), '')) AS level_4,
            {bal_open}::float8 AS opening_balance,
            {bal_close}::float8 AS closing_balance,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{ja}' AND '{d_cm}'
                AND COALESCE(e.entry_type, '') != 'opening_balance'
                THEN l.amount * -1 ELSE 0 END), 0)::float8 AS period_movement
        {_BS_FROM_BAL}
        WHERE a.level_0 = 'BS'
          AND TRIM(a.level_2) = 'Provisions & accruals'
          AND TRIM(COALESCE(a.level_3, '')) <> ''
          AND e.posting_date <= '{d_cm}'
          {ent_frag}
        GROUP BY TRIM(a.level_3), TRIM(COALESCE(NULLIF(a.level_4, ''), ''))
        HAVING ABS(({_bal_amount_expr(d_close)})) > 0.01
            OR ABS(COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{ja}' AND '{d_cm}'
                THEN l.amount * -1 ELSE 0 END), 0)) > 0.01
        ORDER BY ABS(({_bal_amount_expr(d_close)})) DESC
    """
    return _wrap_bs_bal_sql(sql), {}
