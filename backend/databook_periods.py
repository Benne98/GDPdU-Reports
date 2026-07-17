"""Consolidation template period columns — master-first, date-settings fallback."""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from susa_column_mapping import EN_MONTH_ABBR

_FY_COL_RE = re.compile(r"^FY\d{2}A$", re.IGNORECASE)
_YTD_COL_RE = re.compile(r"^YTD\d{2}A$", re.IGNORECASE)
_YTD_GRID_RE = re.compile(r"^YTD(19|20)\d{2}$", re.IGNORECASE)
_MONTH_COL_RE = re.compile(r"^[A-Za-z]{3}-\d{4}$")
_COMPACT_MONTH_COL_RE = re.compile(
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(\d{2})A$",
    re.IGNORECASE,
)


def make_period_str(year: int, month: int) -> str:
    return f"{EN_MONTH_ABBR[month - 1]}-{year}"


def is_fy_period_column(header: str) -> bool:
    return bool(_FY_COL_RE.match(str(header or "").strip()))


def is_ytd_period_column(header: str) -> bool:
    return bool(_YTD_COL_RE.match(str(header or "").strip()))


def is_ytd_grid_label(label: str) -> bool:
    return bool(_YTD_GRID_RE.match(str(label or "").strip()))


def is_month_period_column(header: str) -> bool:
    s = str(header or "").strip()
    if not _MONTH_COL_RE.match(s):
        return False
    abbr = s.split("-", 1)[0]
    return abbr in EN_MONTH_ABBR


def is_compact_month_period_column(header: str) -> bool:
    s = str(header or "").strip()
    m = _COMPACT_MONTH_COL_RE.match(s)
    if not m:
        return False
    return m.group(1).capitalize() in EN_MONTH_ABBR or m.group(1) in EN_MONTH_ABBR


def is_master_amount_period_column(header: str) -> bool:
    s = str(header or "").strip()
    return (
        is_fy_period_column(s)
        or is_ytd_period_column(s)
        or is_month_period_column(s)
        or is_compact_month_period_column(s)
    )


def filter_master_period_columns(headers: list, period_level: str) -> list[str]:
    """Keep FY or month columns in master header order."""
    level = str(period_level or "yearly").strip().lower()
    out: list[str] = []
    for h in headers:
        s = str(h or "").strip()
        if not s:
            continue
        if level == "yearly" and (is_fy_period_column(s) or is_ytd_period_column(s)):
            out.append(s)
        elif level == "monthly" and (
            is_month_period_column(s) or is_compact_month_period_column(s)
        ):
            out.append(s)
    return out


def ordered_master_period_columns_from_headers(headers: list) -> list[str]:
    """All amount period columns (FY, YTD, months) in master header order."""
    out: list[str] = []
    for h in headers:
        s = str(h or "").strip()
        if is_master_amount_period_column(s):
            out.append(s)
    return out


def ordered_master_period_columns_from_df(df) -> list[str]:
    return ordered_master_period_columns_from_headers(list(df.columns))


def reporting_fy_from_period(calendar_year: int, month_num: int, fy_end_month: int) -> int:
    if month_num <= fy_end_month:
        return calendar_year
    return calendar_year + 1


def fy_month_order(month_num: int, fiscal_start_month: int) -> int:
    return (month_num - fiscal_start_month) % 12


def first_period_of_reporting_fy(
    first_fy: int,
    fiscal_start_month: int,
    fy_end_month: int,
) -> tuple[int, int]:
    """Calendar (year, month) of the first month in reporting FY first_fy."""
    if fiscal_start_month > fy_end_month:
        return first_fy - 1, fiscal_start_month
    return first_fy, fiscal_start_month


def _eom_date(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _safe_fy_end_date(fy_end_year: int, fy_end_m: int, fy_end_d: int) -> date:
    last_d = calendar.monthrange(fy_end_year, fy_end_m)[1]
    return date(fy_end_year, fy_end_m, min(int(fy_end_d), last_d))


def _last_completed_fy_end_calendar_year(
    ltm_y: int, ltm_m: int, fy_end_m: int, fy_end_d: int
) -> int:
    as_of_end = _eom_date(ltm_y, ltm_m)
    fye_this_cal_year = _safe_fy_end_date(ltm_y, fy_end_m, fy_end_d)
    return ltm_y if as_of_end >= fye_this_cal_year else ltm_y - 1


def current_fy_end_year_containing_as_of(
    ltm_y: int, ltm_m: int, fy_end_m: int, fy_end_d: int
) -> int:
    """Calendar year of the FY-end for the fiscal year that contains the as-of month-end."""
    as_of_end = _eom_date(ltm_y, ltm_m)
    fye_this_cal_year = _safe_fy_end_date(ltm_y, fy_end_m, fy_end_d)
    return ltm_y if as_of_end <= fye_this_cal_year else ltm_y + 1


def as_of_is_fy_end(ltm_y: int, ltm_m: int, fy_end_m: int, fy_end_d: int) -> bool:
    """True when the as-of month-end lands exactly on a fiscal year-end (Stichtag)."""
    as_of_end = _eom_date(ltm_y, ltm_m)
    cur = current_fy_end_year_containing_as_of(ltm_y, ltm_m, fy_end_m, fy_end_d)
    return as_of_end == _safe_fy_end_date(cur, fy_end_m, fy_end_d)


def grid_ytd_label(reporting_fy_end_year: int) -> str:
    return f"YTD{int(reporting_fy_end_year)}"


def master_ytd_label(reporting_fy_end_year: int) -> str:
    return f"YTD{str(int(reporting_fy_end_year))[-2:]}A"


def ytd_reporting_fy_end_year(
    ltm_month: str | None, fy_end_m: int, fy_end_d: int
) -> int | None:
    """Reporting FY-end calendar year for the open YTD column, or None when as-of is FY-end."""
    ltm = _parse_ltm_month(ltm_month)
    if not ltm:
        return None
    ltm_y, ltm_m = ltm
    if as_of_is_fy_end(ltm_y, ltm_m, fy_end_m, fy_end_d):
        return None
    return current_fy_end_year_containing_as_of(ltm_y, ltm_m, fy_end_m, fy_end_d)


def compute_databook_grid_labels(
    first_fy: int,
    ltm_month: str | None,
    fy_end_m: int,
    fy_end_d: int,
) -> list[str]:
    """Upload-grid period labels: FY{yyyy}… plus YTD{yyyy} when as-of is not FY-end."""
    ltm = _parse_ltm_month(ltm_month)
    if not ltm:
        return [f"FY{first_fy}"]
    ltm_y, ltm_m = ltm
    last_fy = _last_completed_fy_end_calendar_year(ltm_y, ltm_m, fy_end_m, fy_end_d)
    if last_fy < first_fy:
        labels: list[str] = [f"FY{first_fy}"]
    else:
        labels = [f"FY{y}" for y in range(first_fy, last_fy + 1)]
    ytd_fy = ytd_reporting_fy_end_year(ltm_month, fy_end_m, fy_end_d)
    if ytd_fy is not None:
        labels.append(grid_ytd_label(ytd_fy))
    return labels


def ordered_reporting_columns_from_headers(headers: list) -> list[str]:
    """FY and YTD master columns in header order."""
    out: list[str] = []
    for h in headers:
        s = str(h or "").strip()
        if is_fy_period_column(s) or is_ytd_period_column(s):
            out.append(s)
    return out


def ordered_reporting_columns_from_df(df) -> list[str]:
    return ordered_reporting_columns_from_headers(list(df.columns))


def display_reporting_columns_from_headers(headers: list) -> list[str]:
    """FY + latest YTD for databook output sheets (prior-year YTD stays master-only)."""
    fy_cols, ytd_cols = split_fy_and_ytd(ordered_reporting_columns_from_headers(headers))
    out = list(fy_cols)
    if ytd_cols:
        out.append(ytd_cols[-1])
    return out


def display_reporting_columns_from_df(df) -> list[str]:
    return display_reporting_columns_from_headers(list(df.columns))


def ordered_month_columns_from_headers(headers: list) -> list[str]:
    """Month columns (Jan-2024 or Jan22A) in master header order."""
    out: list[str] = []
    for h in headers:
        s = str(h or "").strip()
        if is_month_period_column(s) or is_compact_month_period_column(s):
            out.append(s)
    return out


def ordered_month_columns_from_df(df) -> list[str]:
    return ordered_month_columns_from_headers(list(df.columns))


def _reporting_fy_from_yy_suffix(yy: int) -> int:
    return 2000 + yy if yy < 70 else 1900 + yy


def calendar_year_from_reporting_fy_month(
    reporting_fy: int, month_num: int, fy_end_month: int
) -> int:
    if month_num <= fy_end_month:
        return reporting_fy
    return reporting_fy - 1


def parse_period_column_info(header: str, fy_end_month: int = 12) -> dict:
    """Classify a master period column (FY / YTD / month)."""
    s = str(header or "").strip()
    if is_fy_period_column(s):
        yy = int(re.search(r"(\d{2})", s, re.IGNORECASE).group(1))
        rfy = _reporting_fy_from_yy_suffix(yy)
        return {"kind": "fy", "reporting_fy": rfy, "month_num": None, "calendar_year": None}
    if is_ytd_period_column(s):
        yy = int(re.search(r"(\d{2})", s, re.IGNORECASE).group(1))
        rfy = _reporting_fy_from_yy_suffix(yy)
        return {"kind": "ytd", "reporting_fy": rfy, "month_num": None, "calendar_year": None}
    if is_month_period_column(s):
        cal_y, cal_m = parse_month_period_column(s)
        rfy = reporting_fy_from_period(cal_y, cal_m, fy_end_month)
        return {
            "kind": "month",
            "reporting_fy": rfy,
            "month_num": cal_m,
            "calendar_year": cal_y,
        }
    m = _COMPACT_MONTH_COL_RE.match(s)
    if m:
        abbr = m.group(1).capitalize()
        month_num = EN_MONTH_ABBR.index(abbr) + 1
        rfy = _reporting_fy_from_yy_suffix(int(m.group(2)))
        cal_y = calendar_year_from_reporting_fy_month(rfy, month_num, fy_end_month)
        return {
            "kind": "month",
            "reporting_fy": rfy,
            "month_num": month_num,
            "calendar_year": cal_y,
        }
    raise ValueError(f"Not a master period column: {header!r}")


def parse_month_period_column(header: str) -> tuple[int, int]:
    """Parse 'Jan-2024' -> (calendar_year, month_num)."""
    s = str(header or "").strip()
    if not is_month_period_column(s):
        raise ValueError(f"Not a month period column: {header!r}")
    abbr, year_s = s.split("-", 1)
    month_num = EN_MONTH_ABBR.index(abbr) + 1
    return int(year_s), month_num


def group_month_columns_by_reporting_fy(
    period_cols: list[str],
    fy_end_month: int,
) -> dict[int, list[str]]:
    """Map reporting FY-end year -> month column labels (skips FY/YTD cols)."""
    groups: dict[int, list[str]] = {}
    for col in period_cols:
        info = parse_period_column_info(col, fy_end_month)
        if info["kind"] != "month":
            continue
        groups.setdefault(int(info["reporting_fy"]), []).append(col)
    return dict(sorted(groups.items()))


def master_fy_label(reporting_fy_end_year: int) -> str:
    return f"FY{str(int(reporting_fy_end_year))[-2:]}A"


def is_open_ytd_fy_group(
    fy_end_year: int,
    month_cols: list[str],
    all_periods: list[str],
    fy_end_month: int,
) -> bool:
    """True when the FY month group is the open YTD interval (not a completed fiscal year)."""
    if not month_cols or not all_periods:
        return False
    last_global = all_periods[-1]
    last_in_group = month_cols[-1]
    if last_in_group != last_global:
        return False
    info = parse_period_column_info(last_global, fy_end_month)
    if int(info["reporting_fy"]) != int(fy_end_year):
        return False
    month_num = info.get("month_num")
    if month_num is None:
        return False
    return int(month_num) != int(fy_end_month)


def yearly_average_period_label(
    fy_end_year: int,
    month_cols: list[str],
    all_periods: list[str],
    fy_end_month: int,
) -> str:
    if is_open_ytd_fy_group(fy_end_year, month_cols, all_periods, fy_end_month):
        return master_ytd_label(fy_end_year)
    return master_fy_label(fy_end_year)


_DEC_SNAPSHOT_RE = re.compile(r"^[A-Z][a-z]{2}\d{2}A$")


def display_bs_snapshot_label(fy_header: str, fy_end_month: int = 12) -> str:
    """Map master FY column (FY23A) to balance-sheet snapshot label (Dec23A, Mar23A, …)."""
    s = str(fy_header or "").strip()
    if not is_fy_period_column(s):
        return s
    yy = s[2:4]
    month = min(max(int(fy_end_month or 12), 1), 12)
    abbr = EN_MONTH_ABBR[month - 1]
    return f"{abbr}{yy}A"


def display_bs_ytd_snapshot_label(
    ytd_header: str,
    ltm_month: str | None = None,
    fy_end_month: int = 12,
) -> str:
    """YTD master column -> month snapshot from date settings (YTD23A + Jul -> Jul23A)."""
    s = str(ytd_header or "").strip()
    if not is_ytd_period_column(s):
        return s
    yy = int(re.search(r"(\d{2})", s, re.IGNORECASE).group(1))
    ltm = _parse_ltm_month(ltm_month)
    if ltm:
        abbr = EN_MONTH_ABBR[ltm[1] - 1]
    else:
        abbr = EN_MONTH_ABBR[min(max(int(fy_end_month or 12), 1), 12) - 1]
    return f"{abbr}{yy:02d}A"


def display_bs_period_labels(
    headers: list[str],
    fy_end_month: int = 12,
    ltm_month: str | None = None,
) -> list[str]:
    out: list[str] = []
    for h in headers:
        if is_fy_period_column(h):
            out.append(display_bs_snapshot_label(h, fy_end_month))
        elif is_ytd_period_column(h):
            out.append(display_bs_ytd_snapshot_label(h, ltm_month, fy_end_month))
        else:
            out.append(str(h))
    return out


def wc_snapshot_periods(
    fy_groups: dict[int, list[str]],
    all_periods: list[str],
    *,
    fy_end_month: int = 12,
    ltm_month: str | None = None,
) -> list[str]:
    """One Stichtag month per reporting FY (FY-end snapshot or LTM month for open YTD)."""
    out: list[str] = []
    for fy_end_year, month_cols in fy_groups.items():
        if not month_cols:
            continue
        if is_open_ytd_fy_group(fy_end_year, month_cols, all_periods, fy_end_month):
            target = display_bs_ytd_snapshot_label(
                master_ytd_label(fy_end_year), ltm_month, fy_end_month
            )
        else:
            target = display_bs_snapshot_label(master_fy_label(fy_end_year), fy_end_month)
        out.append(target if target in all_periods else month_cols[-1])
    return out


def bs_bucket_display_label_for_wc_snapshot(
    month_period: str,
    all_month_periods: list[str],
    *,
    fy_end_month: int = 12,
    ltm_month: str | None = None,
) -> str:
    """Map WC monthly snapshot column to BS_Bucket NA classification period title."""
    info = parse_period_column_info(month_period, fy_end_month)
    if info["kind"] != "month":
        return str(month_period).strip()
    rfy = int(info["reporting_fy"])
    fy_groups = group_month_columns_by_reporting_fy(all_month_periods, fy_end_month)
    month_cols = fy_groups.get(rfy, [])
    if is_open_ytd_fy_group(rfy, month_cols, all_month_periods, fy_end_month):
        return display_bs_ytd_snapshot_label(
            master_ytd_label(rfy), ltm_month, fy_end_month
        )
    return display_bs_snapshot_label(master_fy_label(rfy), fy_end_month)


def days_in_month_formula(month_header: str, fy_end_month: int = 12) -> str:
    """Excel formula for calendar days in a month column header."""
    info = parse_period_column_info(month_header, fy_end_month)
    if info["kind"] != "month":
        return "365"
    cal_y, cal_m = info["calendar_year"], info["month_num"]
    return f"DAY(EOMONTH(DATE({cal_y},{cal_m},1),0))"


def days_in_period_formula(period_header: str, fy_end_month: int = 12) -> str:
    """Days multiplier for KPI formulas: actual month length or 365 for FY/YTD."""
    return days_in_month_formula(period_header, fy_end_month)


def split_fy_and_ytd(columns: list[str]) -> tuple[list[str], list[str]]:
    fy = [c for c in columns if is_fy_period_column(c)]
    ytd = [c for c in columns if is_ytd_period_column(c)]
    return fy, ytd


def cashflow_display_periods(periods: list[str]) -> tuple[list[str], list[str]]:
    """Cashflow shows FY/YTD from the second period onward; return display cols and priors."""
    if len(periods) < 2:
        return [], []
    display = periods[1:]
    priors = [periods[i] for i in range(len(display))]
    return display, priors


def prior_year_month_column(month_col: str) -> str:
    """Shift a master month column one calendar year back (Jan25A -> Jan24A)."""
    s = str(month_col or "").strip()
    m = _COMPACT_MONTH_COL_RE.match(s)
    if m:
        abbr = m.group(1).capitalize()
        yy = int(m.group(2))
        return f"{abbr}{(yy - 1) % 100:02d}A"
    if is_month_period_column(s):
        cal_y, cal_m = parse_month_period_column(s)
        return make_period_str(cal_y - 1, cal_m)
    raise ValueError(f"Not a month period column: {month_col!r}")


def prior_year_month_columns(month_cols: list[str], all_columns: list[str]) -> list[str]:
    """Same-month prior-year columns that exist in the master."""
    col_set = {str(c).strip() for c in all_columns}
    out: list[str] = []
    for mc in month_cols:
        try:
            prior = prior_year_month_column(mc)
        except ValueError:
            continue
        if prior in col_set:
            out.append(prior)
    return out


def ytd_month_columns_for_period(
    ytd_label: str,
    all_columns: list[str],
    fy_end_month: int = 12,
) -> list[str]:
    """Month columns in the YTD reporting FY up to the last available month."""
    if not is_ytd_period_column(ytd_label):
        return []
    yy = int(re.search(r"(\d{2})", ytd_label, re.IGNORECASE).group(1))
    rfy = _reporting_fy_from_yy_suffix(yy)
    month_cols = [
        str(c).strip()
        for c in all_columns
        if is_compact_month_period_column(c) or is_month_period_column(c)
    ]
    groups = group_month_columns_by_reporting_fy(month_cols, fy_end_month)
    return list(groups.get(rfy, []))


def prior_ytd_column(ytd_label: str, all_columns: list[str]) -> str | None:
    """Prior-year YTD column label (YTD25A -> YTD24A) when present in master."""
    if not is_ytd_period_column(ytd_label):
        return None
    yy = int(re.search(r"(\d{2})", ytd_label, re.IGNORECASE).group(1))
    prior = f"YTD{(yy - 1) % 100:02d}A"
    col_set = {str(c).strip() for c in all_columns}
    return prior if prior in col_set else None


@dataclass(frozen=True)
class CashflowDeltaSpec:
    kind: str
    curr_col: str | None = None
    prior_col: str | None = None
    curr_months: tuple[str, ...] = ()
    prior_months: tuple[str, ...] = ()


def cashflow_delta_spec(
    display_period: str,
    prior_period: str | None,
    all_columns: list[str],
    *,
    fy_end_month: int = 12,
) -> CashflowDeltaSpec:
    """FY: column pair; YTD: prefer YTD curr/prior columns, else month sums."""
    if is_ytd_period_column(display_period):
        prior_ytd = prior_ytd_column(display_period, all_columns)
        if prior_ytd and display_period in {str(c).strip() for c in all_columns}:
            return CashflowDeltaSpec(
                kind="ytd",
                curr_col=display_period,
                prior_col=prior_ytd,
            )
        curr_months = ytd_month_columns_for_period(display_period, all_columns, fy_end_month)
        prior_months = prior_year_month_columns(curr_months, all_columns)
        return CashflowDeltaSpec(
            kind="ytd",
            curr_months=tuple(curr_months),
            prior_months=tuple(prior_months),
        )
    return CashflowDeltaSpec(
        kind="fy",
        curr_col=display_period,
        prior_col=prior_period,
    )


def _parse_ltm_month(ltm_month: str | None) -> tuple[int, int] | None:
    if ltm_month in (None, ""):
        return None
    parts = str(ltm_month).strip().split("-")
    try:
        y, m = int(parts[0]), int(parts[1])
        if 1 <= m <= 12:
            return y, m
    except (ValueError, IndexError):
        return None
    return None


def _iter_calendar_months(y1: int, m1: int, y2: int, m2: int):
    y, m = y1, m1
    while (y, m) <= (y2, m2):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def _date_settings(config: dict) -> tuple[int, int, int, int, int]:
    first_fy = int(config.get("first_fy") or 2020)
    fy_end_m = int(config.get("fy_end_month") or 12)
    fy_end_d = int(config.get("fy_end_day") or 31)
    fiscal_start = config.get("fiscal_start_month")
    if fiscal_start in (None, ""):
        fiscal_start_m = (fy_end_m % 12) + 1
    else:
        fiscal_start_m = int(fiscal_start)
    return first_fy, fy_end_m, fy_end_d, fiscal_start_m, first_fy


def fallback_yearly_columns(config: dict) -> list[str]:
    """FY24A-style labels from Date Settings (aligned with databook FY grid logic)."""
    first_fy, fy_end_m, fy_end_d, _, _ = _date_settings(config)
    ltm = _parse_ltm_month(config.get("ltm_month"))
    if not ltm:
        return [f"FY{str(first_fy)[-2:]}A"]
    ltm_y, ltm_m = ltm
    last_fy = _last_completed_fy_end_calendar_year(ltm_y, ltm_m, fy_end_m, fy_end_d)
    if last_fy < first_fy:
        return [f"FY{str(first_fy)[-2:]}A"]
    cols = [f"FY{str(y)[-2:]}A" for y in range(first_fy, last_fy + 1)]
    ytd_fy = ytd_reporting_fy_end_year(config.get("ltm_month"), fy_end_m, fy_end_d)
    if ytd_fy is not None:
        cols.append(master_ytd_label(ytd_fy))
    return cols


def fallback_monthly_columns(config: dict) -> list[str]:
    """Month labels from first FY month through ltm_month, fiscal order within each FY."""
    first_fy, fy_end_m, _, fiscal_start_m, _ = _date_settings(config)
    ltm = _parse_ltm_month(config.get("ltm_month"))
    if not ltm:
        sy, sm = first_period_of_reporting_fy(first_fy, fiscal_start_m, fy_end_m)
        return [make_period_str(sy, sm)]

    ltm_y, ltm_m = ltm
    start_y, start_m = first_period_of_reporting_fy(first_fy, fiscal_start_m, fy_end_m)

    months = list(_iter_calendar_months(start_y, start_m, ltm_y, ltm_m))
    months.sort(
        key=lambda ym: (
            reporting_fy_from_period(ym[0], ym[1], fy_end_m),
            fy_month_order(ym[1], fiscal_start_m),
            ym[0],
            ym[1],
        )
    )
    return [make_period_str(y, m) for y, m in months]


def read_master_bs_headers(master_path: Path) -> list[str]:
    import pandas as pd

    if not master_path.is_file():
        raise FileNotFoundError(f"Master workbook not found: {master_path}")
    df = pd.read_excel(master_path, sheet_name="Master_BS", engine="openpyxl", nrows=0)
    return [str(c) for c in df.columns]


def resolve_consolidation_period_columns(config: dict) -> list[str]:
    period_level = str(config.get("period_level") or "yearly").strip().lower()
    master_raw = str(config.get("master_path") or "").strip()
    if master_raw:
        try:
            cols = filter_master_period_columns(
                read_master_bs_headers(Path(master_raw).expanduser().resolve()),
                period_level,
            )
            if cols:
                return cols
        except FileNotFoundError:
            pass

    if period_level == "yearly":
        return fallback_yearly_columns(config)
    if period_level == "monthly":
        return fallback_monthly_columns(config)
    raise ValueError(f"Unsupported period_level: {period_level!r}")


def resolve_adjustments_period_columns(config: dict) -> list[str]:
    """FY columns only (yearly), master-first then date-settings fallback."""
    yearly_config = {**config, "period_level": "yearly"}
    return resolve_consolidation_period_columns(yearly_config)
