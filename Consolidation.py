"""
Append consolidation block to Master_BS / Master_PL in the SuSa master workbook.
Driven by JSON config (first CLI argument) from the FDD backend.

Changes vs. previous version:
- The consolidation input template does NOT need L5/L6 columns anymore.
- Generated consolidation rows receive L5 empty and L6 = "Reported".
- FY columns are detected robustly, e.g. FY23A, FY24A, FY2024A.
- Month columns are detected robustly, e.g. Jan-2024, Feb-2024, Dec-2024.
- The input template may contain either FY columns or month columns, but not both.
- If month columns are provided, yearly FY values are derived automatically:
  - PL: sum of months per reporting FY (fiscal year, not calendar year).
  - BS: balance at fy_end_month per reporting FY (from Date Settings); if that month
    is missing, the last available month in the fiscal year is used.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, PatternFill, Side
from openpyxl.utils import get_column_letter

PROJECT_ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from gst_excel_theme import THEME
from databook_amounts import FMT_KEUR, scale_to_keur

BASE_FONT = THEME.font_base
HEADER_FONT = THEME.font_header
BOLD_FONT = THEME.font_bold
FMT_AMOUNT = FMT_KEUR

WHITE_FILL = THEME.fill_white
HEADER_FILL = THEME.fill_header
PURPLE_FILL = PatternFill("solid", fgColor="C9A0DC")
CHECK_FILL = PatternFill("solid", fgColor="FFFEF3C7")
HEADER_BORDER = Border(bottom=Side(style="thin", color="FFE2E8F0"))

CANVAS_EXTRA_COLS = 20
L6_CONSOLIDATION_VALUE = "Reported"

TEXT_COLS = {
    "Entity",
    "Account",
    "Account description",
    "L1 - BS/PL",
    "L2",
    "L3",
    "L4",
    "L5",
    "L6",
    "NA",
    "Comments",
    "Q&A",
    "Answer of Target",
}

MONTH_ORDER = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}
MONTH_PATTERN = re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-(\d{4})$")
FY_PATTERN = re.compile(r"^FY(\d{2}|\d{4})A?$")


def find_row_optional(ws, text):
    for r in range(1, ws.max_row + 1):
        if ws.cell(r, 1).value == text:
            return r
    return None


def get_table_col_limit(ws) -> int:
    """Last column with a non-empty header in row 1."""
    last = 1
    for c in range(1, ws.max_column + 1):
        val = ws.cell(1, c).value
        if val is not None and str(val).strip():
            last = c
    return last


def get_format_limit_col(ws, l6_col: int | None = None) -> int:
    """
    Rightmost column to paint white / purple / check backgrounds.
    Covers FY columns, period columns and SuSa canvas extension.
    """
    table = get_table_col_limit(ws)
    limit = min(max(ws.max_column, table), table + CANVAS_EXTRA_COLS)
    if l6_col is not None:
        limit = max(limit, l6_col)
    return limit


def get_l5_col(ws) -> int | None:
    for c in range(1, ws.max_column + 1):
        if ws.cell(1, c).value == "L5":
            return c
    return None


def get_l6_col(ws) -> int | None:
    for c in range(1, ws.max_column + 1):
        if ws.cell(1, c).value == "L6":
            return c
    return None


def require_l5_col(ws) -> int:
    """The master generator is expected to create L5 (may be empty). Fail if missing."""
    l5_col = get_l5_col(ws)
    if l5_col is None:
        raise ValueError(f"Sheet '{ws.title}' must contain an 'L5' column in row 1.")
    return l5_col


def require_l6_col(ws) -> int:
    """The master generator is expected to create L6. Fail loudly if it is missing."""
    l6_col = get_l6_col(ws)
    if l6_col is None:
        raise ValueError(f"Sheet '{ws.title}' must contain an 'L6' column in row 1.")
    return l6_col


def normalize_header(header) -> str:
    return str(header).strip() if header is not None else ""


def is_fy_header(header) -> bool:
    return bool(FY_PATTERN.fullmatch(normalize_header(header)))


def parse_month_header(header) -> tuple[int, int] | None:
    """Return (year, month_number) for headers like 'Jan-2024'; otherwise None."""
    match = MONTH_PATTERN.fullmatch(normalize_header(header))
    if not match:
        return None
    month_name, year = match.groups()
    return int(year), MONTH_ORDER[month_name]


def is_month_header(header) -> bool:
    return parse_month_header(header) is not None


def is_amount_header(header) -> bool:
    if header is None:
        return False
    h = normalize_header(header)
    if not h or h in TEXT_COLS:
        return False
    return is_fy_header(h) or is_month_header(h)


def get_fy_cols(columns) -> list:
    return [c for c in columns if is_fy_header(c)]


def get_month_cols(columns) -> list:
    return [c for c in columns if is_month_header(c)]


def get_value_cols_from_input(cons: pd.DataFrame) -> tuple[list, list]:
    """
    The consolidation template is expected to contain either FY columns or month columns.
    It should not contain both at the same time.
    """
    fy_cols = get_fy_cols(cons.columns)
    month_cols = get_month_cols(cons.columns)

    if fy_cols and month_cols:
        raise ValueError(
            "Consolidation input must contain either FY columns or month columns, not both. "
            f"Found FY columns: {fy_cols}; month columns: {month_cols}."
        )

    if not fy_cols and not month_cols:
        raise ValueError(
            "No value columns found in consolidation input. Expected FY columns like 'FY24A' "
            "or month columns like 'Jan-2024'."
        )

    return fy_cols, month_cols


def year_from_fy_header(header) -> int | None:
    match = FY_PATTERN.fullmatch(normalize_header(header))
    if not match:
        return None

    year_token = match.group(1)
    if len(year_token) == 4:
        return int(year_token)
    return 2000 + int(year_token)


def reporting_fy_from_period(calendar_year: int, month_num: int, fy_end_month: int) -> int:
    """Map a calendar month to Reporting FY (same convention as SuSabyYear FY labels)."""
    if month_num <= fy_end_month:
        return calendar_year
    return calendar_year + 1


def fy_month_order(month_num: int, fiscal_start_month: int) -> int:
    return (month_num - fiscal_start_month) % 12


def find_target_fy_header(target_headers, year: int) -> str | None:
    """
    Find the matching FY column in the master output for a calendar year.
    Prefers the expected format FY24A, but also accepts FY2024A, FY24, FY2024.
    """
    headers = {normalize_header(h): h for h in target_headers if h is not None}
    short = str(year)[-2:]
    candidates = [f"FY{short}A", f"FY{year}A", f"FY{short}", f"FY{year}"]
    for candidate in candidates:
        if candidate in headers:
            return headers[candidate]
    return None


def derive_fy_values_from_months(
    part: pd.DataFrame,
    month_cols: list,
    l1_value: str,
    target_headers,
    *,
    fy_end_month: int = 12,
    fiscal_start_month: int | None = None,
) -> list:
    """
    Create FY columns from monthly input values if matching FY columns exist in the master.

    Months are grouped by reporting FY (fiscal year), using the same fy_end_month convention
    as SuSabyYear. PL: sum of fiscal-year months. BS: balance at fy_end_month.
    """
    fy_end_month = int(fy_end_month or 12)
    if fiscal_start_month is None:
        fiscal_start_month = (fy_end_month % 12) + 1
    else:
        fiscal_start_month = int(fiscal_start_month)

    months_by_fy: dict[int, list[tuple[int, int, str]]] = {}
    for col in month_cols:
        parsed = parse_month_header(col)
        if parsed is None:
            continue
        cal_year, month_num = parsed
        reporting_fy = reporting_fy_from_period(cal_year, month_num, fy_end_month)
        months_by_fy.setdefault(reporting_fy, []).append(
            (fy_month_order(month_num, fiscal_start_month), month_num, col)
        )

    derived_fy_cols = []
    for reporting_fy, entries in sorted(months_by_fy.items()):
        fy_header = find_target_fy_header(target_headers, reporting_fy)
        if fy_header is None:
            continue

        entries = sorted(entries, key=lambda item: (item[0], item[1]))
        ordered_cols = [col for _, _, col in entries]

        if l1_value == "PL":
            part[fy_header] = part[ordered_cols].apply(pd.to_numeric, errors="coerce").sum(
                axis=1,
                min_count=1,
            )
        elif l1_value == "BS":
            fy_end_entries = [entry for entry in entries if entry[1] == fy_end_month]
            source_col = fy_end_entries[-1][2] if fy_end_entries else ordered_cols[-1]
            part[fy_header] = pd.to_numeric(part[source_col], errors="coerce")
        else:
            raise ValueError(f"Unsupported L1 value '{l1_value}'. Expected 'BS' or 'PL'.")

        derived_fy_cols.append(fy_header)

    return derived_fy_cols


def apply_header_style(ws, l6_col: int | None = None) -> None:
    limit = get_format_limit_col(ws, l6_col=l6_col)
    for c in range(1, limit + 1):
        cell = ws.cell(1, c)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.border = HEADER_BORDER
        header = cell.value
        cell.alignment = Alignment(
            horizontal="left" if header in TEXT_COLS else "right",
            vertical="center",
        )


def style_cell(cell, *, header=None, fill=WHITE_FILL, bold: bool = False):
    cell.font = BOLD_FONT if bold else BASE_FONT
    cell.fill = fill
    cell.alignment = Alignment(
        horizontal="left" if header in TEXT_COLS or header is None else "right",
        vertical="center",
    )
    if header is not None and is_amount_header(header):
        cell.number_format = FMT_AMOUNT


def write_df(ws, start_row, df, l6_col: int | None = None):
    header_map = {
        ws.cell(1, c).value: c
        for c in range(1, ws.max_column + 1)
        if ws.cell(1, c).value is not None
    }
    limit = get_format_limit_col(ws, l6_col=l6_col)

    for r_offset, (_, row) in enumerate(df.iterrows()):
        r = start_row + r_offset
        for col_name, value in row.items():
            if col_name not in header_map:
                continue
            c = header_map[col_name]
            cell = ws.cell(r, c, value)
            style_cell(cell, header=col_name)

        # Format remaining blank cells in the inserted consolidation block.
        for c in range(1, limit + 1):
            header = ws.cell(1, c).value
            cell = ws.cell(r, c)
            if cell.value is None:
                style_cell(cell, header=header)


def fill_row(ws, row, *, fill=WHITE_FILL, bold: bool = False, l6_col: int | None = None):
    limit = get_format_limit_col(ws, l6_col=l6_col)
    for c in range(1, limit + 1):
        header = ws.cell(1, c).value
        cell = ws.cell(row, c)
        style_cell(cell, header=header, fill=fill, bold=bold)


def fill_block(ws, start_row, end_row, *, fill=WHITE_FILL, l6_col: int | None = None):
    if end_row < start_row:
        return
    for r in range(start_row, end_row + 1):
        fill_row(ws, r, fill=fill, l6_col=l6_col)


def add_consolidation_header(ws, row, l6_col: int | None = None):
    limit = get_format_limit_col(ws, l6_col=l6_col)
    for c in range(1, limit + 1):
        cell = ws.cell(row, c)
        cell.fill = PURPLE_FILL
        cell.font = BASE_FONT
        cell.alignment = Alignment(horizontal="left", vertical="center")
        if c == 1:
            cell.value = "Consolidation"


def subtotal(col, r1, r2):
    return f"SUBTOTAL(9,{col}{r1}:{col}{r2})"


def add_check_row(ws, row, bspl_start, bspl_end, cons_start, cons_end, l6_col: int | None = None):
    ws.cell(row, 1, "Check")
    fill_row(ws, row, fill=CHECK_FILL, bold=True, l6_col=l6_col)

    for c in range(1, get_format_limit_col(ws, l6_col=l6_col) + 1):
        header = ws.cell(1, c).value
        cell = ws.cell(row, c)
        if not is_amount_header(header):
            continue

        col = get_column_letter(c)
        expr = f"{subtotal(col, bspl_start, bspl_end)}-{subtotal(col, cons_start, cons_end)}"
        cell.value = f'=IF(ABS({expr})<0.0000001,"-",{expr})'
        cell.number_format = FMT_AMOUNT
        cell.alignment = Alignment(horizontal="right", vertical="center")


def add_total_pl_row(ws, row, pl_start, pl_end, cons_start, cons_end, l6_col: int | None = None):
    ws.cell(row, 1, "Total PL")
    fill_row(ws, row, fill=CHECK_FILL, bold=True, l6_col=l6_col)

    for c in range(1, get_format_limit_col(ws, l6_col=l6_col) + 1):
        header = ws.cell(1, c).value
        cell = ws.cell(row, c)
        if not is_amount_header(header):
            continue

        col = get_column_letter(c)
        expr = f"{subtotal(col, pl_start, pl_end)}-{subtotal(col, cons_start, cons_end)}"
        cell.value = f"={expr}"
        cell.number_format = FMT_AMOUNT
        cell.alignment = Alignment(horizontal="right", vertical="center")


def _prepare_cons_df(
    cons: pd.DataFrame,
    l1_value: str,
    target_headers,
    *,
    fy_end_month: int = 12,
    fiscal_start_month: int | None = None,
    scale_to_keur_enabled: bool = True,
) -> pd.DataFrame:
    l1_col = "L1" if "L1" in cons.columns else None

    if l1_col:
        l1_normalized = cons[l1_col].astype(str).str.strip().str.upper()
        part = cons[l1_normalized == l1_value].copy()
    elif "L1 - BS/PL" in cons.columns:
        l1_normalized = cons["L1 - BS/PL"].astype(str).str.strip().str.upper()
        part = cons[l1_normalized == l1_value].copy()
    else:
        raise ValueError("Consolidation input must contain 'L1' or 'L1 - BS/PL'.")

    part["L1 - BS/PL"] = l1_value
    part["L5"] = ""
    part["L6"] = L6_CONSOLIDATION_VALUE

    fy_cols, month_cols = get_value_cols_from_input(cons)
    if fy_cols:
        value_cols = fy_cols
    else:
        derived_fy_cols = derive_fy_values_from_months(
            part=part,
            month_cols=month_cols,
            l1_value=l1_value,
            target_headers=target_headers,
            fy_end_month=fy_end_month,
            fiscal_start_month=fiscal_start_month,
        )
        # Keep monthly values and additionally write derived FY values where a matching
        # FY column exists in the master output.
        value_cols = month_cols + derived_fy_cols

    base_cols = [
        "Entity",
        "Account",
        "Account description",
        "L1 - BS/PL",
        "L2",
        "L3",
        "L4",
        "L5",
        "L6",
    ]
    result = part.reindex(columns=base_cols + value_cols)
    return scale_to_keur(result, value_cols, enabled=scale_to_keur_enabled)


def get_headers(ws) -> list:
    return [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]


def run(config: dict) -> None:
    master_path = str(config.get("master_path") or config.get("output_path") or "").strip()
    cons_path = str(
        config.get("consolidation_path") or config.get("cons_path") or ""
    ).strip()
    if not master_path or not cons_path:
        raise SystemExit("Config must define master_path and consolidation_path.")

    fy_end_month = int(config.get("fy_end_month") or 12)
    fiscal_start_month = config.get("fiscal_start_month")
    if fiscal_start_month in (None, ""):
        fiscal_start_month = (fy_end_month % 12) + 1
    else:
        fiscal_start_month = int(fiscal_start_month)

    scale_to_keur_enabled = bool(config.get("scale_to_keur", True))

    cons = pd.read_excel(cons_path, engine="openpyxl")
    wb = load_workbook(master_path)

    # Master_BS
    ws_bs = wb["Master_BS"]
    require_l5_col(ws_bs)
    l6_col_bs = require_l6_col(ws_bs)
    apply_header_style(ws_bs, l6_col=l6_col_bs)
    bspl_start = 2
    old_check = find_row_optional(ws_bs, "Check")

    if old_check:
        bspl_end = old_check - 1
        ws_bs.delete_rows(old_check, 1)
        insert_row = old_check
    else:
        bspl_end = ws_bs.max_row
        insert_row = ws_bs.max_row + 2

    cons_bs = _prepare_cons_df(
        cons,
        "BS",
        target_headers=get_headers(ws_bs),
        fy_end_month=fy_end_month,
        fiscal_start_month=fiscal_start_month,
        scale_to_keur_enabled=scale_to_keur_enabled,
    )
    ws_bs.insert_rows(insert_row, amount=len(cons_bs) + 5)
    fill_row(ws_bs, insert_row, l6_col=l6_col_bs)
    add_consolidation_header(ws_bs, insert_row + 1, l6_col=l6_col_bs)
    fill_row(ws_bs, insert_row + 2, l6_col=l6_col_bs)
    cons_start = insert_row + 3
    write_df(ws_bs, cons_start, cons_bs, l6_col=l6_col_bs)
    cons_end = cons_start + len(cons_bs) - 1
    fill_block(ws_bs, cons_start, cons_end, l6_col=l6_col_bs)
    fill_row(ws_bs, cons_end + 1, l6_col=l6_col_bs)
    check_row = cons_end + 2
    add_check_row(ws_bs, check_row, bspl_start, bspl_end, cons_start, cons_end, l6_col=l6_col_bs)

    # Master_PL
    ws_pl = wb["Master_PL"]
    require_l5_col(ws_pl)
    l6_col_pl = require_l6_col(ws_pl)
    apply_header_style(ws_pl, l6_col=l6_col_pl)
    pl_start = 2
    old_total = find_row_optional(ws_pl, "Total PL")

    if old_total:
        pl_end = old_total - 1
        ws_pl.delete_rows(old_total, 1)
        insert_row = old_total
    else:
        pl_end = ws_pl.max_row
        insert_row = ws_pl.max_row + 2

    cons_pl = _prepare_cons_df(
        cons,
        "PL",
        target_headers=get_headers(ws_pl),
        fy_end_month=fy_end_month,
        fiscal_start_month=fiscal_start_month,
        scale_to_keur_enabled=scale_to_keur_enabled,
    )
    ws_pl.insert_rows(insert_row, amount=len(cons_pl) + 5)
    fill_row(ws_pl, insert_row, l6_col=l6_col_pl)
    add_consolidation_header(ws_pl, insert_row + 1, l6_col=l6_col_pl)
    fill_row(ws_pl, insert_row + 2, l6_col=l6_col_pl)
    cons_start = insert_row + 3
    write_df(ws_pl, cons_start, cons_pl, l6_col=l6_col_pl)
    cons_end = cons_start + len(cons_pl) - 1
    fill_block(ws_pl, cons_start, cons_end, l6_col=l6_col_pl)
    fill_row(ws_pl, cons_end + 1, l6_col=l6_col_pl)
    total_row = cons_end + 2
    add_total_pl_row(ws_pl, total_row, pl_start, pl_end, cons_start, cons_end, l6_col=l6_col_pl)

    wb.save(master_path)
    print(f"Consolidation applied to {master_path}")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python Consolidation.py <config.json>")
    config_path = Path(sys.argv[1])
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    run(config)


if __name__ == "__main__":
    main()