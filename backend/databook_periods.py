"""Consolidation template period columns — master-first, date-settings fallback."""
from __future__ import annotations

import calendar
import re
from datetime import date
from pathlib import Path

from susa_column_mapping import EN_MONTH_ABBR

_FY_COL_RE = re.compile(r"^FY\d{2}A$", re.IGNORECASE)
_MONTH_COL_RE = re.compile(r"^[A-Za-z]{3}-\d{4}$")


def make_period_str(year: int, month: int) -> str:
    return f"{EN_MONTH_ABBR[month - 1]}-{year}"


def is_fy_period_column(header: str) -> bool:
    return bool(_FY_COL_RE.match(str(header or "").strip()))


def is_month_period_column(header: str) -> bool:
    s = str(header or "").strip()
    if not _MONTH_COL_RE.match(s):
        return False
    abbr = s.split("-", 1)[0]
    return abbr in EN_MONTH_ABBR


def filter_master_period_columns(headers: list, period_level: str) -> list[str]:
    """Keep FY or month columns in master header order."""
    level = str(period_level or "yearly").strip().lower()
    out: list[str] = []
    for h in headers:
        s = str(h or "").strip()
        if not s:
            continue
        if level == "yearly" and is_fy_period_column(s):
            out.append(s)
        elif level == "monthly" and is_month_period_column(s):
            out.append(s)
    return out


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
    return [f"FY{str(y)[-2:]}A" for y in range(first_fy, last_fy + 1)]


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
