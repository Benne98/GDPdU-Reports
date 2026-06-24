"""Legacy-compat SQL helpers for the GDPdU backend.

Bridges the GDPdU schema (fact_gl_line / fact_gl_entry / dim_gl_account)
to the response shapes expected by the verbatim-ported legacy frontend.

=== SIGN CONVENTION (unchanged from app/services/statements.py) ===
  Storage : fact_gl_line.amount is signed → + = Soll (debit), − = Haben (credit).
  P&L view: presented = −amount.
             revenue (credit, stored −3000) → +3000 (income, positive).
             cost    (debit,  stored  +500) → −500 (expense, negative).
This module applies `amount * -1` in SQL CASE WHEN expressions — the ONLY
place this inversion happens.  Never flip it again elsewhere.

=== SCHEMA MAPPING ===
  Legacy                        → GDPdU
  fact_gl_journal_line l        → fact_gl_line l
  fact_gl_journal_entry e       → fact_gl_entry e
  JOIN ON legal_entity_code + fiscal_year + journal_entry_number
                                → JOIN ON journal_entry_group_number + fiscal_year
  dim_gl_account JOIN ON gl_account_id (unique in legacy)
                                → JOIN ON account_number_group + fiscal_year (PK)
  a.statement_type = 'PL'/'BS' → a.level_0 = 'PL'/'BS'
  l.legal_entity_code filter   → l.entity_prefix (= LEFT(account_number_group,2))
  entity param is legal_entity_code → resolve to entity_prefix via dim_legal_entity
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ---------------------------------------------------------------------------
# Entity resolution
# ---------------------------------------------------------------------------

def resolve_entity_prefix(session: Session, entity: Optional[str]) -> Optional[str]:
    """Map legal_entity_code → entity_prefix (2-char). Returns None for no filter."""
    if not entity or entity.strip().lower() in ("", "all"):
        return None
    # Multi-entity: comma-separated legal_entity_codes → IN-list handled separately.
    if "," in entity:
        return None
    row = session.execute(
        text("SELECT entity_prefix FROM dim_legal_entity WHERE legal_entity_code = :e LIMIT 1"),
        {"e": entity},
    ).fetchone()
    return row[0] if row else None


def resolve_entity_prefixes(session: Session, entity: Optional[str]) -> list[str]:
    """Resolve one or many legal_entity_code values to entity_prefix list."""
    if not entity or entity.strip().lower() in ("", "all"):
        return []
    codes = [c.strip() for c in entity.split(",") if c.strip()]
    if not codes:
        return []
    rows = session.execute(
        text(
            "SELECT entity_prefix FROM dim_legal_entity "
            "WHERE legal_entity_code = ANY(:codes)"
        ),
        {"codes": codes},
    ).fetchall()
    return [r[0] for r in rows if r[0]]


def entity_sql_fragment(entity_prefix: Optional[str], table_alias: str = "l") -> str:
    """Return a safe SQL AND-fragment for entity_prefix filter (no injection risk — 2-char CHAR)."""
    if entity_prefix is None:
        return ""
    # entity_prefix is a 2-char CHAR; safe to inline after resolution via parameterised query
    safe = str(entity_prefix).replace("'", "")[:2]
    return f"AND {table_alias}.entity_prefix = '{safe}'"


def entities_sql_fragment(entity_prefixes: list[str], table_alias: str = "l") -> str:
    """SQL AND-fragment for multiple entity_prefix values."""
    if not entity_prefixes:
        return ""
    safe = [str(p).replace("'", "")[:2] for p in entity_prefixes if p]
    if not safe:
        return ""
    if len(safe) == 1:
        return f"AND {table_alias}.entity_prefix = '{safe[0]}'"
    inner = ", ".join(f"'{p}'" for p in safe)
    return f"AND {table_alias}.entity_prefix IN ({inner})"


# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------

def pm(year: int, month: int) -> tuple[int, int]:
    """Prior month (year, month)."""
    return (year, month - 1) if month > 1 else (year - 1, 12)


def last_day(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def short_label(year: int, month: int) -> str:
    return f"{_MONTH_ABBR[month - 1]}{str(year)[-2:]}"


def label_actual(label: str) -> str:
    """Historical actual column label — append A (FY24 → FY24A, YTDJul25 → YTDJul25A)."""
    s = (label or "").strip()
    if not s:
        return s
    if len(s) > 1 and s[-1].upper() in ("A", "P", "F"):
        return s
    return f"{s}A"


def label_plan_period(label: str) -> str:
    """Plan column for a period token — Jul25A → Jul25P."""
    s = (label or "").strip()
    if not s:
        return s
    base = s[:-1] if len(s) > 1 and s[-1].upper() in ("A", "P", "F") else s
    return f"{base}P"


def label_plan_fy(year: int) -> str:
    """Full-year plan label for fiscal year after anchor (2025 → FY26P)."""
    return f"FY{str(year + 1)[-2:]}P"


def label_forecast_fy(year: int) -> str:
    """Current fiscal-year forecast label (2025 → FY25F)."""
    return f"FY{str(year)[-2:]}F"


def col_labels_month(year: int, month: int) -> dict[str, str]:
    pm_y, pm_m = pm(year, month)
    py = year - 1
    return {
        "py_cm": label_actual(short_label(py, month)),
        "pm": label_actual(short_label(pm_y, pm_m)),
        "cm": label_actual(short_label(year, month)),
        "ytd": label_actual(f"YTD{short_label(year, month)}"),
        "ytd_py": label_actual(f"YTD{short_label(py, month)}"),
    }


def col_labels_annual(year: int, month: int) -> dict[str, str]:
    """Column labels for the annual (exit-readiness flow) P&L view.

    Mirrors legacy routers/exit_readiness.py::_er_flow_col_labels (authoritative,
    lines 36-50):
      fy2 = FY(year-2), fy3 = FY(year-1)            — full fiscal years;
      ytd / ytd_py     = YTD through ``month`` of year / (year-1);
      ltm              = rolling 12 months ending ``month`` of year (LTMJul25A);
      ltm_py           = rolling 12 months ending ``month`` of (year-1);
      fy_f             = full-year forecast (FY25F): YTD + plan YTG after anchor.

    ``fy1`` (= FY(year-3)) is aggregated by :func:`pl_grain_sql_annual` and carried
    in every row's ``amounts``, but — exactly like the legacy ErFlowResponse — it
    is intentionally NOT given a column label here; the verbatim-ported frontend
    renders only these labelled columns.
    """
    abbr = _MONTH_ABBR[month - 1]
    yr2 = str(year)[-2:]
    py2 = str(year - 1)[-2:]
    fy2_yr2 = str(year - 2)[-2:]
    fy3_yr2 = str(year - 1)[-2:]
    return {
        "fy2": label_actual(f"FY{fy2_yr2}"),
        "fy3": label_actual(f"FY{fy3_yr2}"),
        "ytd_py": label_actual(f"YTD{abbr}{py2}"),
        "ytd": label_actual(f"YTD{abbr}{yr2}"),
        "ltm_py": label_actual(f"LTM{abbr}{py2}"),
        "ltm": label_actual(f"LTM{abbr}{yr2}"),
        "fy_f": label_forecast_fy(year),
        "plan_cm": label_plan_fy(year),
    }


# ---------------------------------------------------------------------------
# ISO week helpers (for week-grain)
# ---------------------------------------------------------------------------

def iso_week_monday(iso_year: int, iso_week: int) -> date:
    """Return Monday of the given ISO week."""
    jan4 = date(iso_year, 1, 4)
    day_of_week = jan4.weekday()  # 0=Mon
    week_1_mon = jan4 - timedelta(days=day_of_week)
    return week_1_mon + timedelta(weeks=iso_week - 1)


def iso_week_thursday(iso_year: int, iso_week: int) -> date:
    """Return Thursday of the given ISO week (= Monday + 3 days).

    ISO-8601 attributes a week to the year/month that contains its Thursday,
    so this is the canonical anchor day for week→month assignment.
    """
    return iso_week_monday(iso_year, iso_week) + timedelta(days=3)


def iso_week_bounds(iso_year: int, iso_week: int) -> tuple[date, date]:
    mon = iso_week_monday(iso_year, iso_week)
    return mon, mon + timedelta(days=6)


# ---------------------------------------------------------------------------
# Centralized ISO-week grain primitives (reporting-v2 Phase 5)
# ---------------------------------------------------------------------------
# Two named primitives express the TWO different ways an ISO week is used so that
# every weekly SQL path is unambiguous about STOCK vs FLOW and never falls back to
# a monthly-bucket (fiscal_period) approximation:
#
#   * STOCK  (balance-sheet / working-capital): the value is a CLOSING BALANCE
#     "as of" a single date — the END of the ISO week.  Use ``week_cutoff``.
#   * FLOW   (P&L / cash flow / sales): the value is the Σ of in-period movements
#     whose ``posting_date`` falls inside the ISO week.  Use ``week_range``.
#
# Both are thin, deterministic wrappers over :func:`iso_week_monday` /
# :func:`iso_week_bounds` (Monday-start, Sunday-end, ISO-8601).  They are
# behaviour-IDENTICAL to the dates the existing BS/PL/WC/CF weekly SQL already
# computes via ``iso_week_bounds`` — introduced as the single canonical entry
# point so new weekly paths (Sales) route through the same math.


def week_cutoff(iso_year: int, iso_week: int) -> date:
    """Last day (Sunday) of the given ISO week — the STOCK cutoff date.

    For a balance-sheet / working-capital column the value is the cumulative
    CLOSING BALANCE with ``posting_date <= week_cutoff(iso_year, iso_week)``.

    == FORMULA ==
        week_cutoff = iso_week_monday(iso_year, iso_week) + 6 days  (the Sunday)

    == WORKED EXAMPLE ==
        week_cutoff(2025, 1)  → 2025-01-05  (ISO 2025-W01 is Mon 2024-12-30..Sun
                                              2025-01-05; a BS balance "at W01" is
                                              the stock through 2025-01-05)
        week_cutoff(2020, 53) → 2021-01-03  (2020 has an ISO week 53)

    == EDGE CASES ==
        * ISO week 53 (long years 2020/2026/…): valid, returns that week's Sunday.
        * Year boundary: week_cutoff(2026, 1) → 2026-01-04 (the W01 Sunday), and a
          posting on 2025-12-31 (ISO 2026-W01) is INCLUDED because 2025-12-31 <=
          2026-01-04.  A non-January fiscal-year start does not affect the cutoff —
          it is a pure calendar/ISO date, independent of fiscal_period.
    """
    return iso_week_monday(iso_year, iso_week) + timedelta(days=6)


def week_range(iso_year: int, iso_week: int) -> tuple[date, date]:
    """(Monday, Sunday) span of the given ISO week — the FLOW window.

    For a P&L / cash-flow / sales column the value is the Σ of movements whose
    ``posting_date`` is BETWEEN the two returned dates (inclusive).

    == FORMULA ==
        week_range = (iso_week_monday, iso_week_monday + 6 days)
        flow = Σ amount WHERE posting_date BETWEEN monday AND sunday   (inclusive)

    == WORKED EXAMPLE ==
        week_range(2025, 31) → (2025-07-28, 2025-08-03)  — note this straddles the
            Jul/Aug month boundary; a true ISO-week flow therefore counts postings
            on Aug-01..03 in CW31 (a monthly bucket would have mis-assigned them).
        A €1,000 gross-sales invoice posted 2025-07-30 lands in CW31's flow; one
        posted 2025-08-04 lands in CW32 (the next Monday).

    == EDGE CASES ==
        * The Sunday equals :func:`week_cutoff` (same end date; flow uses the span,
          stock uses only the end).
        * Year boundary: week_range(2026, 1) → (2025-12-29, 2026-01-04); a posting
          on 2025-12-31 falls in this ISO week even though its calendar year is
          2025 — matching :func:`datetime.date.isocalendar`.
        * ISO week 53: valid (returns the 7-day span of that long-year week).
    """
    mon = iso_week_monday(iso_year, iso_week)
    return mon, mon + timedelta(days=6)


def iso_week_of(d: date) -> tuple[int, int]:
    """(iso_year, iso_week) that the calendar date ``d`` belongs to (ISO-8601).

    Thin wrapper over :meth:`datetime.date.isocalendar` returning just the
    (year, week) pair — used to walk a trailing series of ISO weeks back from an
    anchor date and to label sales weekly buckets by the week their posting_date
    falls in.  E.g. ``iso_week_of(date(2025, 12, 31)) == (2026, 1)``.
    """
    ic = d.isocalendar()
    return ic[0], ic[1]


def prior_iso_week(iso_year: int, iso_week: int) -> tuple[int, int]:
    mon = iso_week_monday(iso_year, iso_week) - timedelta(weeks=1)
    ic = mon.isocalendar()
    return ic[0], ic[1]


def same_week_prior_year(iso_year: int, iso_week: int) -> tuple[int, int]:
    mon = iso_week_monday(iso_year, iso_week) - timedelta(weeks=52)
    ic = mon.isocalendar()
    return ic[0], ic[1]


def plan_anchor_for_week(iso_year: int, iso_week: int) -> tuple[int, int]:
    """Anchor (year, month) = calendar month that the week's THURSDAY falls in.

    Uses the ISO-8601 rule (a week belongs to the month/year of its Thursday),
    NOT the Sunday week-end. This keeps the anchor month consistent across the
    year boundary and prevents the MTD column from jumping into a (data-less)
    next month for weeks that straddle a month boundary.

    Worked example — CW31/2025 (Mon 2025-07-28, Thu 2025-07-31, Sun 2025-08-03):
        Thursday is 2025-07-31 → anchor = (2025, 7), NOT (2025, 8). The Sunday
        falls in August, but the week is a July week, so MTD covers all of July.
    """
    thu = iso_week_thursday(iso_year, iso_week)
    return thu.year, thu.month


def col_labels_week(iso_year: int, iso_week: int) -> dict[str, str]:
    _, cw_t = iso_week_bounds(iso_year, iso_week)
    pw_y, pw_w = prior_iso_week(iso_year, iso_week)
    _, pw_t = iso_week_bounds(pw_y, pw_w)
    spy_y, spy_w = same_week_prior_year(iso_year, iso_week)
    _, spy_t = iso_week_bounds(spy_y, spy_w)
    yr2 = str(iso_year)[-2:]
    py2 = str(spy_y)[-2:]
    return {
        "py_cm": label_actual(f"CW{spy_w:02d}'{py2}"),
        "pm": label_actual(f"CW{pw_w:02d}'{str(pw_y)[-2:]}"),
        "cm": label_actual(f"CW{iso_week:02d}'{yr2}"),
        "ytd": label_actual(f"YTDCW{iso_week:02d}'{yr2}"),
        "ytd_py": label_actual(f"YTDCW{iso_week:02d}'{py2}"),
        "mtd": label_actual(f"MTD-CW{iso_week:02d}'{yr2}"),
    }


# ---------------------------------------------------------------------------
# P&L grain SQL — month grain
# ---------------------------------------------------------------------------

def pl_grain_sql_month(
    year: int, month: int, ent_frag: str, *, extra_cols: bool = False,
) -> tuple[str, dict[str, Any]]:
    """
    Return (sql, params) to aggregate P&L GL amounts for the 5 standard columns
    (py_cm, pm, cm, ytd, ytd_py) using fiscal_period / fiscal_year.

    Groups by level_2 / level_3 / level_4 / account_number_group so that each
    GL account bucket appears as a separate grain row (matches legacy gl_account_id grain).

    Sign convention: amount * -1 → revenue credit becomes positive presented value.
    """
    pm_y, pm_m = pm(year, month)
    py = year - 1
    # Collect all relevant fiscal years to limit the scan
    years_set = {py, pm_y, year}
    years_arr = sorted(years_set)
    params: dict[str, Any] = {"years": years_arr}

    sql = f"""
        SELECT
            a.level_2,
            a.level_3,
            NULLIF(TRIM(a.level_4), '') AS level_4,
            MAX(a.gl_account_id)        AS gl_account_id,
            l.account_number_group,
            MAX(a.account_name)         AS account_name,
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
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE a.level_0 = 'PL'
          AND e.fiscal_year = ANY(:years)
          AND e.fiscal_period BETWEEN 1 AND 12
          {ent_frag}
        GROUP BY a.level_2, a.level_3, NULLIF(TRIM(a.level_4), ''), l.account_number_group
    """
    return sql, params


# ---------------------------------------------------------------------------
# P&L grain SQL — week grain
# ---------------------------------------------------------------------------

def pl_grain_sql_week(
    iso_year: int, iso_week: int, ent_frag: str,
) -> tuple[str, dict[str, Any]]:
    """Aggregate P&L amounts over ISO-week date ranges (uses posting_date, not fiscal_period)."""
    cw_f, cw_t = iso_week_bounds(iso_year, iso_week)
    pw_y, pw_w = prior_iso_week(iso_year, iso_week)
    pw_f, pw_t = iso_week_bounds(pw_y, pw_w)
    spy_y, spy_w = same_week_prior_year(iso_year, iso_week)
    spy_f, spy_t = iso_week_bounds(spy_y, spy_w)
    ytd_f = date(iso_year, 1, 1)
    ytd_py_f = date(spy_y, 1, 1)
    # MTD: 1st of the anchor month (the calendar month of the week's THURSDAY,
    # per plan_anchor_for_week) up to the anchor Sunday (cw_t). For a week that
    # straddles a month boundary (e.g. CW31/2025) the anchor stays in the
    # Thursday's month, so MTD captures that whole month (no jump to a data-less
    # next month). No separate Sunday-based logic here.
    anchor_y, anchor_m = plan_anchor_for_week(iso_year, iso_week)
    mtd_f = date(anchor_y, anchor_m, 1)

    min_d = min(spy_f, pw_f, cw_f, ytd_f, ytd_py_f, mtd_f)
    max_d = max(spy_t, pw_t, cw_t)

    sql = f"""
        SELECT
            a.level_2,
            a.level_3,
            NULLIF(TRIM(a.level_4), '') AS level_4,
            MAX(a.gl_account_id)        AS gl_account_id,
            l.account_number_group,
            MAX(a.account_name)         AS account_name,
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
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE a.level_0 = 'PL'
          AND e.posting_date BETWEEN '{min_d}' AND '{max_d}'
          {ent_frag}
        GROUP BY a.level_2, a.level_3, NULLIF(TRIM(a.level_4), ''), l.account_number_group
    """
    return sql, {}


# ---------------------------------------------------------------------------
# P&L grain SQL — annual (exit-readiness flow) grain
# ---------------------------------------------------------------------------

def pl_grain_sql_annual(
    year: int, month: int, ent_frag: str,
) -> tuple[str, dict[str, Any]]:
    """
    Aggregate P&L GL amounts into the 7 annual exit-readiness flow columns:
        fy1, fy2, fy3, ytd, ytd_py, ltm, ltm_py.

    Mirrors legacy routers/exit_readiness.py::_er_flow_grain_sql (lines 139-201),
    ported to the GDPdU schema (fact_gl_line / fact_gl_entry / dim_gl_account,
    joined on journal_entry_group_number + fiscal_year and
    account_number_group + fiscal_year — same join shape as pl_grain_sql_month).

    === COLUMN FORMULAS ===
      Full fiscal years (fiscal_year, fiscal_period 1..12):
        fy1    = fiscal_year == year-3
        fy2    = fiscal_year == year-2
        fy3    = fiscal_year == year-1
        ytd    = fiscal_year == year   AND fiscal_period <= month
        ytd_py = fiscal_year == year-1 AND fiscal_period <= month
      Rolling 12 months (posting_date, like the legacy LTM):
        ltm    = posting_date in [ (year-1, month+1, 1) .. last_day(year,   month) ]
        ltm_py = posting_date in [ (year-2, month+1, 1) .. last_day(year-1, month) ]
        month == 12  →  the window collapses to the full calendar year
                        (ltm = Jan..Dec of `year`; ltm_py = Jan..Dec of year-1).

    === SIGN CONVENTION ===
      presented = ``l.amount * -1``  (revenue credit → +, cost debit → −).
      This is the ONLY inversion — never flip it again downstream.

    === SCAN / FILTERS ===
      a.level_0 = 'PL';  ``fiscal_period BETWEEN 1 AND 12`` excludes period 13
      (consolidation) for EVERY column, including the posting_date-based LTM;
      ``e.fiscal_year = ANY(:years)`` with years = [year-3 .. year] bounds the
      scan (every LTM posting_date falls inside those fiscal years).
      Entity filter via ``{ent_frag}`` (l.entity_prefix).

    === WORKED EXAMPLE (year=2025, month=7) ===
      A revenue account (credit) with stored amounts summing to -10 in FY2024
      → fy3 = +10.  A material-cost account (debit) +10 in FY2024 → fy3 = -10.

    === EDGE CASES ===
      * Missing FY (no rows for year-3) → COALESCE(SUM(...),0) → fy1 = 0.
      * Entity filter matching nothing → all 7 columns 0 for that bucket.
      * fiscal_period = 13 rows never counted (consolidation excluded).
      * month == 12 → LTM / LTM_PY are full calendar years (year / year-1).
    """
    fy1_y, fy2_y, fy3_y = year - 3, year - 2, year - 1
    ltm_start    = date(fy3_y, month + 1, 1) if month < 12 else date(year, 1, 1)
    ltm_end      = last_day(year, month)
    ltm_py_start = date(fy2_y, month + 1, 1) if month < 12 else date(fy3_y, 1, 1)
    ltm_py_end   = last_day(fy3_y, month)
    years_arr = [fy1_y, fy2_y, fy3_y, year]
    params: dict[str, Any] = {"years": years_arr}

    sql = f"""
        SELECT
            a.level_2,
            a.level_3,
            NULLIF(TRIM(a.level_4), '') AS level_4,
            MAX(a.gl_account_id)        AS gl_account_id,
            l.account_number_group,
            MAX(a.account_name)         AS account_name,
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
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE a.level_0 = 'PL'
          AND e.fiscal_year = ANY(:years)
          AND e.fiscal_period BETWEEN 1 AND 12
          {ent_frag}
        GROUP BY a.level_2, a.level_3, NULLIF(TRIM(a.level_4), ''), l.account_number_group
    """
    return sql, params


# ---------------------------------------------------------------------------
# Monthly grain SQL (12 periods)
# ---------------------------------------------------------------------------

def _fy_span_periods(year: int, month: int) -> list[tuple[int, int]]:
    """All calendar months from (year-2)-01 through (year)-month inclusive.

    Used by the monthly "extended" view (span='fy3'): the two full fiscal years
    preceding the anchor plus the YTD months of the anchor year.

    WORKED EXAMPLE (year=2025, month=7):
        FY2023 Jan..Dec (12) + FY2024 Jan..Dec (12) + 2025 Jan..Jul (7) = 31 periods,
        first = (2023, 1), last = (2025, 7).

    EDGE CASES:
        month == 12 → the anchor year contributes all 12 months (36 periods total).
        month == 1  → the anchor year contributes a single month (25 periods total).
    """
    periods: list[tuple[int, int]] = []
    for y in (year - 2, year - 1):
        for m in range(1, 13):
            periods.append((y, m))
    for m in range(1, month + 1):
        periods.append((year, m))
    return periods


def _fy_span_totals(year: int, month: int) -> list[dict[str, Any]]:
    """Summary (total) columns for the span='fy3' monthly view, left→right.

    Returns three dicts (FY-2, FY-1, YTD), each with:
        key   : column key carried in rows[].amounts (e.g. 'FY2023','YTD2025')
        label : short display label (e.g. 'FY23','YTD25')
        kind  : 'fy' (full fiscal year Σ period 1..12) or 'ytd' (Σ period 1..month)
        year  : the fiscal_year the total aggregates

    FORMULAS (per P&L line, presented amounts):
        FY{year-2} = Σ fiscal_period 1..12 of fiscal_year (year-2)
        FY{year-1} = Σ fiscal_period 1..12 of fiscal_year (year-1)
        YTD{year}  = Σ fiscal_period 1..month of fiscal_year year
    """
    return [
        {"key": f"FY{year - 2}", "label": label_actual(f"FY{str(year - 2)[-2:]}"),
         "kind": "fy", "year": year - 2},
        {"key": f"FY{year - 1}", "label": label_actual(f"FY{str(year - 1)[-2:]}"),
         "kind": "fy", "year": year - 1},
        {"key": f"YTD{year}", "label": label_actual(f"YTD{str(year)[-2:]}"),
         "kind": "ytd", "year": year},
    ]


def pl_monthly_grain_sql(
    year: int, month: int, ent_frag: str, *, span: str = "12m",
) -> tuple[str, dict[str, Any]]:
    """Build SQL for the monthly view (one column per period key YYYY-MM).

    span='12m' (default): last 12 months ending at (year, month) — UNCHANGED
        legacy behaviour, no summary columns.
    span='fy3': all months of :func:`_fy_span_periods` PLUS the three summary
        (total) columns of :func:`_fy_span_totals` (FY-2, FY-1, YTD), each emitted
        as an extra CASE column aliased by its total key (e.g. "FY2023","YTD2025").

    Sign convention: the single ``l.amount * -1`` inversion (revenue +, expense −).
    """
    if span == "fy3":
        periods = _fy_span_periods(year, month)
        totals = _fy_span_totals(year, month)
    else:
        periods = _last_12_periods(year, month)
        totals = []

    cases = []
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
            a.level_2,
            a.level_3,
            NULLIF(TRIM(a.level_4), '') AS level_4,
            MAX(a.gl_account_id)        AS gl_account_id,
            l.account_number_group,
            MAX(a.account_name)         AS account_name,
            {', '.join(cases)}
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE a.level_0 = 'PL'
          AND e.fiscal_year = ANY(:years)
          AND e.fiscal_period BETWEEN 1 AND 12
          {ent_frag}
        GROUP BY a.level_2, a.level_3, NULLIF(TRIM(a.level_4), ''), l.account_number_group
    """
    return sql, params


def _last_12_periods(year: int, month: int) -> list[tuple[int, int]]:
    periods: list[tuple[int, int]] = []
    y, m = year, month
    for _ in range(12):
        periods.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    periods.reverse()
    return periods


def period_key(y: int, m: int) -> str:
    return f"{y:04d}-{m:02d}"


def period_label(y: int, m: int) -> str:
    return f"{_MONTH_ABBR[m - 1]}{str(y)[-2:]}"


# ---------------------------------------------------------------------------
# P&L consolidation grain SQL (all entities in one query)
# ---------------------------------------------------------------------------

def pl_consl_grain_sql_month(year: int, month: int) -> tuple[str, dict[str, Any]]:
    """Consolidation grain: same 5 columns but also includes entity_prefix."""
    pm_y, pm_m = pm(year, month)
    py = year - 1
    years_arr = sorted({py, pm_y, year})
    params: dict[str, Any] = {"years": years_arr}

    sql = f"""
        SELECT
            a.level_2,
            a.level_3,
            NULLIF(TRIM(a.level_4), '') AS level_4,
            l.account_number_group,
            l.entity_prefix,
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
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE a.level_0 = 'PL'
          AND e.fiscal_year = ANY(:years)
          AND e.fiscal_period BETWEEN 1 AND 12
        GROUP BY a.level_2, a.level_3, NULLIF(TRIM(a.level_4), ''), l.account_number_group, l.entity_prefix
    """
    return sql, params


def pl_consl_grain_sql_annual_fullfy(fy_year: int) -> tuple[str, dict[str, Any]]:
    """Consolidation grain for ONE full fiscal year — entity breakdown.

    Like :func:`pl_consl_grain_sql_month`, but emits a single value column
    ``fy`` holding the WHOLE fiscal year ``fy_year`` (the last completed FY for
    the annual "Jahresscheiben" view).  Used by
    :func:`app.services.fin_compat_pl.build_pl_annual_consolidation` to build the
    per-legal-entity column breakdown.

    === COLUMN FORMULA ===
      fy = Σ ( l.amount * -1 )  over
             e.fiscal_year   == fy_year
         AND e.fiscal_period BETWEEN 1 AND 12          (period 13 excluded)
      grouped by (level_2, level_3, level_4, account_number_group, entity_prefix).

    === SIGN CONVENTION ===
      presented = ``l.amount * -1``  (revenue credit → +, cost debit → −).
      Applied EXACTLY ONCE here; never flip again downstream.

    === SCAN / FILTERS ===
      a.level_0 = 'PL';  e.fiscal_year = :fy_year bounds the scan;
      ``fiscal_period BETWEEN 1 AND 12`` drops the period-13 consolidation rows.

    === WORKED EXAMPLE (fy_year=2024) ===
      A revenue account (credit) stored summing to -100 across periods 1..12 of
      FY2024 for entity_prefix 'AT' → fy = +100 in that grain bucket.
      A material-cost account (debit) +40 → fy = -40.

    === EDGE CASES ===
      * No rows for fy_year (missing FY)  → COALESCE(SUM(...),0) → fy = 0.
      * fiscal_period = 13 rows           → never counted.
      * entity_prefix with no GL activity → simply absent from the grain.
    """
    params: dict[str, Any] = {"fy_year": fy_year}
    sql = f"""
        SELECT
            a.level_2,
            a.level_3,
            NULLIF(TRIM(a.level_4), '') AS level_4,
            l.account_number_group,
            l.entity_prefix,
            COALESCE(SUM(CASE WHEN e.fiscal_year = {fy_year}
                          AND e.fiscal_period BETWEEN 1 AND 12
                         THEN l.amount * -1 ELSE 0 END), 0) AS fy
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE a.level_0 = 'PL'
          AND e.fiscal_year = :fy_year
          AND e.fiscal_period BETWEEN 1 AND 12
        GROUP BY a.level_2, a.level_3, NULLIF(TRIM(a.level_4), ''), l.account_number_group, l.entity_prefix
    """
    return sql, params


def pl_consl_grain_sql_annual_ytd(year: int, month: int) -> tuple[str, dict[str, Any]]:
    """Consolidation grain for YTD of the anchor fiscal year (entity breakdown).

    Emits a single ``ytd`` column = Σ presented flow for fiscal_period 1..month of
    ``year`` (not full fiscal year).
    """
    params: dict[str, Any] = {"year": year, "month": month}
    sql = f"""
        SELECT
            a.level_2,
            a.level_3,
            NULLIF(TRIM(a.level_4), '') AS level_4,
            l.account_number_group,
            l.entity_prefix,
            COALESCE(SUM(CASE WHEN e.fiscal_year = :year
                          AND e.fiscal_period BETWEEN 1 AND :month
                         THEN l.amount * -1 ELSE 0 END), 0) AS ytd
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE a.level_0 = 'PL'
          AND e.fiscal_year = :year
          AND e.fiscal_period BETWEEN 1 AND 12
        GROUP BY a.level_2, a.level_3, NULLIF(TRIM(a.level_4), ''), l.account_number_group, l.entity_prefix
    """
    return sql, params


def pl_consl_grain_sql_week(iso_year: int, iso_week: int) -> tuple[str, dict[str, Any]]:
    cw_f, cw_t = iso_week_bounds(iso_year, iso_week)
    pw_y, pw_w = prior_iso_week(iso_year, iso_week)
    pw_f, pw_t = iso_week_bounds(pw_y, pw_w)
    spy_y, spy_w = same_week_prior_year(iso_year, iso_week)
    spy_f, spy_t = iso_week_bounds(spy_y, spy_w)
    ytd_f = date(iso_year, 1, 1)
    ytd_py_f = date(spy_y, 1, 1)
    min_d = min(spy_f, pw_f, cw_f, ytd_f, ytd_py_f)
    max_d = max(spy_t, pw_t, cw_t)

    sql = f"""
        SELECT
            a.level_2,
            a.level_3,
            NULLIF(TRIM(a.level_4), '') AS level_4,
            l.account_number_group,
            l.entity_prefix,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{spy_f}' AND '{spy_t}'
                         THEN l.amount * -1 ELSE 0 END), 0) AS py_cm,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{pw_f}' AND '{pw_t}'
                         THEN l.amount * -1 ELSE 0 END), 0) AS pm,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{cw_f}' AND '{cw_t}'
                         THEN l.amount * -1 ELSE 0 END), 0) AS cm,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{ytd_f}' AND '{cw_t}'
                         THEN l.amount * -1 ELSE 0 END), 0) AS ytd,
            COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{ytd_py_f}' AND '{spy_t}'
                         THEN l.amount * -1 ELSE 0 END), 0) AS ytd_py
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE a.level_0 = 'PL'
          AND e.posting_date BETWEEN '{min_d}' AND '{max_d}'
        GROUP BY a.level_2, a.level_3, NULLIF(TRIM(a.level_4), ''), l.account_number_group, l.entity_prefix
    """
    return sql, {}


# ---------------------------------------------------------------------------
# Weekly-breakdown helpers (M-2 / M-1 full months + M0 partial, by ISO week)
# ---------------------------------------------------------------------------

def _iso_weeks_in_month(year: int, month: int) -> list[tuple[int, int, date]]:
    """ISO weeks attributed to calendar (year, month) by their THURSDAY.

    Returns (iso_year, iso_week, sunday) tuples in chronological order.  A week
    belongs to the month that contains its Thursday — the ISO-8601 rule, same as
    :func:`plan_anchor_for_week`.  The Sunday (week-end) is still carried in the
    tuple because the weekly SQL ranges run Monday..Sunday.

    Worked example (year=2025, month=7): the Thursdays in July 2025 are Jul 3,
    10, 17, 24, 31 → ISO weeks CW27..CW31. Note CW31's Sunday (2025-08-03) is in
    August, but the week is still a July week (Thursday rule).
    """
    first = date(year, month, 1)
    # weekday(): Mon=0 .. Thu=3 → days until the first Thursday on/after the 1st.
    offset = (3 - first.weekday()) % 7
    thu = first + timedelta(days=offset)
    out: list[tuple[int, int, date]] = []
    while thu.year == year and thu.month == month:
        ic = thu.isocalendar()
        sun = thu + timedelta(days=3)  # Thursday + 3 = Sunday (week-end)
        out.append((ic[0], ic[1], sun))
        thu += timedelta(days=7)
    return out


# Backwards-compatible alias (the old name described Sunday-anchoring, which is
# no longer how weeks are attributed; kept only so any stray reference resolves).
_iso_weeks_with_sunday_in_month = _iso_weeks_in_month


def weekly_breakdown_layout(iso_year: int, iso_week: int) -> list[dict[str, Any]]:
    """Column layout for the weekly-breakdown view (DB-free, testable).

    Anchor = the given ISO week.  Its month (M0, partial / in-progress) is the
    calendar month that contains the week's THURSDAY (ISO-8601 rule, same as
    :func:`plan_anchor_for_week`), NOT its Sunday.  M-1 and M-2 are the two
    preceding FULL calendar months.

    Returns three group dicts (left→right: M-2, M-1, M0), each:
        month_key   : 'YYYY-MM'
        month_label : short month label, e.g. 'May25'
        kind        : 'full' (M-2, M-1) or 'partial' (M0)
        weeks       : [{key:'W{iso_year}-{iso_week}', label:'CW{ww}',
                        monday, sunday, iso_year, iso_week}, ...]
                      For 'full' months: every ISO week whose Thursday is in the
                      month.
                      For M0: weeks whose Thursday is in M0 up to AND INCLUDING
                      the anchor week.
        total       : full → {key:'M{y}-{mm}', label:month_label, kind:'month',
                              year, month}
                      partial → {key:'MTD{y}-{mm}', label:'MTD {month_label}',
                              kind:'mtd', year, month, mtd_end:anchor_sunday}

    === WORKED EXAMPLE (anchor CW31/2025: Mon 2025-07-28, Thu 2025-07-31,
        Sun 2025-08-03) ===
        Thursday 2025-07-31 is in July → M0 = Jul25 (partial). July ISO weeks (by
        Thursday) are CW27..CW31, so M0 holds CW27..CW31 (up to & incl. anchor)
        plus the MTD total whose SQL range is 2025-07-01 .. 2025-08-03 (anchor
        Sunday) → full July data.  M-1 = Jun25 (full), M-2 = May25 (full).
    """
    anchor_thu = iso_week_thursday(iso_year, iso_week)
    anchor_sun = anchor_thu + timedelta(days=3)
    m0 = (anchor_thu.year, anchor_thu.month)
    m1 = pm(*m0)
    m2 = pm(*m1)

    groups: list[dict[str, Any]] = []
    for (gy, gm), kind in ((m2, "full"), (m1, "full"), (m0, "partial")):
        weeks_raw = _iso_weeks_in_month(gy, gm)
        if kind == "partial":
            # week's Thursday = its Sunday − 3 days; keep weeks up to the anchor.
            weeks_raw = [w for w in weeks_raw
                         if (w[2] - timedelta(days=3)) <= anchor_thu]
        weeks = []
        for wy, ww, sun in weeks_raw:
            mon = sun - timedelta(days=6)
            weeks.append({
                "key": f"W{wy}-{ww}", "label": f"CW{ww:02d}",
                "monday": mon, "sunday": sun, "iso_year": wy, "iso_week": ww,
            })
        month_label = short_label(gy, gm)
        if kind == "full":
            total = {"key": f"M{gy}-{gm:02d}", "label": month_label,
                     "kind": "month", "year": gy, "month": gm}
        else:
            total = {"key": f"MTD{gy}-{gm:02d}", "label": f"MTD {month_label}",
                     "kind": "mtd", "year": gy, "month": gm, "mtd_end": anchor_sun}
        groups.append({
            "month_key": f"{gy:04d}-{gm:02d}", "month_label": month_label,
            "kind": kind, "weeks": weeks, "total": total,
        })
    return groups


def pl_weekly_breakdown_sql(
    layout: list[dict[str, Any]], ent_frag: str,
) -> tuple[str, dict[str, Any]]:
    """Wide P&L SQL for the weekly-breakdown ``layout`` (one CASE column per key).

    Week columns aggregate by posting_date (Monday..Sunday).  'month' totals
    aggregate the whole calendar month via fiscal_year/fiscal_period; 'mtd' totals
    aggregate posting_date from the 1st of M0 to the anchor Sunday.

    The scan is bounded by ``e.fiscal_year = ANY(:years)`` (all fiscal years that
    any week or total touches) AND ``fiscal_period BETWEEN 1 AND 12`` (drops the
    period-13 consolidation rows for every column).

    Sign convention: the single ``l.amount * -1`` inversion (revenue +, expense −).
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
            a.level_2,
            a.level_3,
            NULLIF(TRIM(a.level_4), '') AS level_4,
            MAX(a.gl_account_id)        AS gl_account_id,
            l.account_number_group,
            MAX(a.account_name)         AS account_name,
            {', '.join(cases)}
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE a.level_0 = 'PL'
          AND e.fiscal_year = ANY(:years)
          AND e.fiscal_period BETWEEN 1 AND 12
          {ent_frag}
        GROUP BY a.level_2, a.level_3, NULLIF(TRIM(a.level_4), ''), l.account_number_group
    """
    return sql, params


# ---------------------------------------------------------------------------
# L4 trend helpers
# ---------------------------------------------------------------------------

def _esc(s: str) -> str:
    return s.replace("'", "''")


def pl_l4_trend_sql(
    year: int, month: int, grain: str,
    level_2: str, level_3: str, level_4: str,
    ent_frag: str,
) -> tuple[str, list[dict]]:
    """Return (sql, windows) for the PL l4-trend chart."""
    windows = _trend_windows(year, month, grain)
    l2_f = f"AND TRIM(a.level_2) = '{_esc(level_2)}'" if level_2 else ""
    l3_f = f"AND TRIM(a.level_3) = '{_esc(level_3)}'" if level_3 else ""
    l4_f = f"AND NULLIF(TRIM(a.level_4),'') = '{_esc(level_4)}'" if level_4 else ""
    cases = []
    for w in windows:
        s, e = w["cur_start"], w["cur_end"]
        ps, pe = w["prev_start"], w["prev_end"]
        if s == e:
            cases.append(f"COALESCE(SUM(CASE WHEN e.posting_date = '{s}' THEN l.amount*-1 ELSE 0 END),0) AS \"{w['pk']}\"")
        else:
            cases.append(f"COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{s}' AND '{e}' THEN l.amount*-1 ELSE 0 END),0) AS \"{w['pk']}\"")
        if ps == pe:
            cases.append(f"COALESCE(SUM(CASE WHEN e.posting_date = '{ps}' THEN l.amount*-1 ELSE 0 END),0) AS \"{w['prev_pk']}\"")
        else:
            cases.append(f"COALESCE(SUM(CASE WHEN e.posting_date BETWEEN '{ps}' AND '{pe}' THEN l.amount*-1 ELSE 0 END),0) AS \"{w['prev_pk']}\"")
    where_parts = []
    for w in windows:
        s, e = w["cur_start"], w["cur_end"]
        ps, pe = w["prev_start"], w["prev_end"]
        if s == e:
            where_parts.append(f"e.posting_date = '{s}'")
        else:
            where_parts.append(f"e.posting_date BETWEEN '{s}' AND '{e}'")
        if ps == pe:
            where_parts.append(f"e.posting_date = '{ps}'")
        else:
            where_parts.append(f"e.posting_date BETWEEN '{ps}' AND '{pe}'")
    where_clause = f"AND ({' OR '.join(where_parts)})" if where_parts else ""

    sql = f"""
        SELECT {', '.join(cases)}
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE a.level_0 = 'PL'
          {l2_f} {l3_f} {l4_f}
          {where_clause}
          {ent_frag}
    """
    return sql, windows


def _trend_windows(year: int, month: int, grain: str) -> list[dict]:
    """Return window dicts for the L4 trend chart (year / quarter / month grains)."""
    if grain == "year":
        periods = _last_12_periods(year, month)
        py_periods = _last_12_periods(year - 1, month)
        windows = []
        for (cy, cm_), (py_, pm_) in zip(periods, py_periods):
            pk = period_key(cy, cm_)
            windows.append({
                "pk": pk, "prev_pk": f"prev_{pk}",
                "label": period_label(cy, cm_),
                "date_from": date(cy, cm_, 1).isoformat(),
                "date_to": last_day(cy, cm_).isoformat(),
                "cur_start": date(cy, cm_, 1), "cur_end": last_day(cy, cm_),
                "prev_start": date(py_, pm_, 1), "prev_end": last_day(py_, pm_),
            })
        return windows

    if grain == "quarter":
        d_end = last_day(year, month)
        days_to_sun = (6 - d_end.weekday()) % 7
        w_sun = d_end + timedelta(days=days_to_sun)
        windows = []
        d_sun = w_sun
        for _ in range(13):
            d_mon = d_sun - timedelta(days=6)
            w_actual_end = min(d_sun, d_end)
            iso_c = d_sun.isocalendar()
            iso_y, iso_w = iso_c[0], iso_c[1]
            pk = f"W{iso_w:02d}-{iso_y}"
            pw_start = d_mon - timedelta(weeks=52)
            pw_end = w_actual_end - timedelta(weeks=52)
            windows.append({
                "pk": pk, "prev_pk": f"prev_{pk}",
                "label": f"CW{iso_w:02d} '{str(iso_y)[2:]}",
                "date_from": d_mon.isoformat(), "date_to": w_actual_end.isoformat(),
                "cur_start": d_mon, "cur_end": w_actual_end,
                "prev_start": pw_start, "prev_end": pw_end,
            })
            d_sun -= timedelta(weeks=1)
        windows.reverse()
        return windows

    # month grain — daily points in selected month
    _, n_days = calendar.monthrange(year, month)
    windows = []
    for d in range(1, n_days + 1):
        day = date(year, month, d)
        try:
            prev_day = date(year - 1, month, d)
        except ValueError:
            prev_day = date(year - 1, month, n_days)
        pk = day.isoformat()
        windows.append({
            "pk": pk, "prev_pk": f"prev_{pk}",
            "label": day.strftime("%d.%m"),
            "date_from": day.isoformat(), "date_to": day.isoformat(),
            "cur_start": day, "cur_end": day,
            "prev_start": prev_day, "prev_end": prev_day,
        })
    return windows


def build_trend_response(rows: list[Any], windows: list[dict], year: int, month: int) -> dict:
    if not rows:
        return {"series": [], "col_label": period_label(year, month), "prev_label": ""}
    row = rows[0]
    row_d = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
    series = []
    for w in windows:
        cur = float(row_d.get(w["pk"]) or 0)
        prev = float(row_d.get(w["prev_pk"]) or 0)
        delta = round((cur - prev) / abs(prev) * 100, 1) if abs(prev) > 1e-6 else None
        series.append({
            "label": w["label"],
            "current": round(cur, 2),
            "previous": round(prev, 2),
            "delta_pct": delta,
            "date_from": w["date_from"],
            "date_to": w["date_to"],
        })
    last_w = windows[-1]
    prev_end = last_w["prev_end"]
    prev_label = prev_end.strftime("%b %Y") if hasattr(prev_end, "strftime") else str(prev_end)
    return {"series": series, "col_label": period_label(year, month), "prev_label": prev_label}


# ---------------------------------------------------------------------------
# Plan grain SQL
# ---------------------------------------------------------------------------

def plan_grain_sql(year: int, month: int, ent_frag: str, scenario: str) -> tuple[str, dict]:
    """Aggregate plan amounts from fact_gl_plan for a given scenario."""
    params: dict[str, Any] = {"year": year, "scenario": scenario}
    ent_frag_plan = ent_frag.replace("l.entity_prefix", "a.entity_prefix")

    sql = f"""
        SELECT
            a.level_2,
            a.level_3,
            NULLIF(TRIM(a.level_4), '') AS level_4,
            pl.account_number_group,
            MAX(a.gl_account_id)        AS gl_account_id,
            COALESCE(SUM(CASE WHEN pl.fiscal_period = {month}
                         THEN pl.amount * -1 ELSE 0 END), 0) AS plan_cm,
            COALESCE(SUM(CASE WHEN pl.fiscal_period <= {month}
                         THEN pl.amount * -1 ELSE 0 END), 0) AS ytd_plan,
            COALESCE(SUM(CASE WHEN pl.fiscal_period > {month}
                         THEN pl.amount * -1 ELSE 0 END), 0) AS ytg
        FROM fact_gl_plan pl
        JOIN dim_gl_account a
          ON a.account_number_group = pl.account_number_group
         AND a.fiscal_year = pl.fiscal_year
        WHERE pl.fiscal_year = :year
          AND pl.scenario = :scenario
          AND a.level_0 = 'PL'
          {ent_frag_plan}
        GROUP BY a.level_2, a.level_3, NULLIF(TRIM(a.level_4), ''), pl.account_number_group
    """
    return sql, params


def position_plan_grain_sql(
    year: int,
    month: int,
    ent_frag: str,
    statement: str = "PL",
) -> tuple[str, dict]:
    """Aggregate manual-budget plan amounts from ``fact_position_plan`` by line_code.

    SIBLING of :func:`plan_grain_sql` for the new Phase-3 position-grain budget.
    Differences from the ``fact_gl_plan`` path:

      * keyed by ``line_code`` DIRECTLY — no level_2/3/4 join (the position is the
        grain), so the caller maps grain → structure by line_code identity.
      * partner-driven positions store BOTH a position-level row (partner_id='')
        and per-partner rows (partner_id<>'').  We **sum every row that rolls into
        a line_code together** (position-level + all its partners) so the position
        total = Σ partners + position remainder.  (The roll-up invariant guarantees
        Σ(partners)+"Other" == position, so summing all rows reproduces the
        position without double counting.)
      * SAME presentation sign as ``plan_grain_sql``: ``amount * -1`` (stored GL
        sign → presented; revenue + / expense −).  This is the single sign flip,
        identical to the fact_gl_plan path — a divergence here would desync
        plan-vs-actual, so it MUST match.

    Entity precedence (per the plan): prefer per-entity rows (entity_prefix=:ep)
    for a given line_code; fall back to consolidated rows (entity_prefix='') ONLY
    for line_codes that have NO per-entity row.  When no entity filter is requested
    (``ent_frag`` empty → CONSOLIDATED view) the consolidated total per line_code is
    EITHER its direct ('') row when one exists (an explicit consolidated override),
    OR the SUM of the per-entity rows (entity_prefix<>'') when no '' row exists.  So
    a consolidated plan = Σ entity plans unless staff entered a direct consolidated
    number.  ``ent_frag`` is the standard ``AND l.entity_prefix = '<ep>'`` fragment;
    we parse the 2-char prefix out of it (safe: it is a resolved CHAR(2)).

    GOLDEN-SAFETY: this only ever returns rows when fact_position_plan has
    scenario='budget' rows for the scope; with none, the result set is empty and
    every caller falls through to forecast/plan → byte-identical output.  The
    consolidated entity-sum only changes HOW existing budget rows aggregate (Σ
    per-entity vs '' override) — with no budget rows the WHERE still matches nothing.
    """
    params: dict[str, Any] = {
        "year": year,
        "scenario": "budget",
        "statement": statement,
    }

    # Parse the resolved 2-char entity_prefix out of the standard ent_frag, if any.
    # ent_frag looks like "AND l.entity_prefix = 'XX'" (or '' for consolidated).
    ep: Optional[str] = None
    if ent_frag and "=" in ent_frag:
        frag_val = ent_frag.split("=", 1)[1].strip().strip("'").strip()
        if frag_val:
            ep = frag_val[:2]

    sel_cm = "SUM(CASE WHEN p.fiscal_period = :month THEN p.amount * -1 ELSE 0 END)"
    sel_ytd = "SUM(CASE WHEN p.fiscal_period <= :month THEN p.amount * -1 ELSE 0 END)"
    sel_ytg = "SUM(CASE WHEN p.fiscal_period > :month THEN p.amount * -1 ELSE 0 END)"
    params["month"] = month

    if ep is None:
        # Consolidated view: the '' row per line_code when it exists (explicit
        # consolidated override), ELSE the SUM of the per-entity rows.  A row
        # participates iff it is a '' row, OR it is a per-entity row and NO '' row
        # exists for the same line_code.  (The GROUP BY line_code then sums the
        # surviving per-entity rows into the consolidated total.)
        ent_clause = (
            "AND ( p.entity_prefix = '' "
            "      OR ( p.entity_prefix <> '' "
            "           AND NOT EXISTS ( "
            "             SELECT 1 FROM fact_position_plan c "
            "             WHERE c.statement = p.statement "
            "               AND c.line_code = p.line_code "
            "               AND c.scenario = p.scenario "
            "               AND c.fiscal_year = p.fiscal_year "
            "               AND c.entity_prefix = '' ) ) )"
        )
    else:
        # Per-entity view: per-entity rows, plus consolidated rows ONLY for
        # line_codes that have no per-entity row (precedence, no double count).
        params["ep"] = ep
        ent_clause = (
            "AND ( p.entity_prefix = :ep "
            "      OR ( p.entity_prefix = '' "
            "           AND NOT EXISTS ( "
            "             SELECT 1 FROM fact_position_plan e "
            "             WHERE e.statement = p.statement "
            "               AND e.line_code = p.line_code "
            "               AND e.scenario = p.scenario "
            "               AND e.fiscal_year = p.fiscal_year "
            "               AND e.entity_prefix = :ep ) ) )"
        )

    sql = f"""
        SELECT
            p.line_code,
            COALESCE({sel_cm},  0) AS plan_cm,
            COALESCE({sel_ytd}, 0) AS ytd_plan,
            COALESCE({sel_ytg}, 0) AS ytg
        FROM fact_position_plan p
        WHERE p.fiscal_year = :year
          AND p.scenario = :scenario
          AND p.statement = :statement
          {ent_clause}
        GROUP BY p.line_code
    """
    return sql, params
