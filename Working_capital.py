"""Working capital monthly report — TWC/OWC hierarchy from Master_BS (formula mode)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

PROJECT_ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from gst_excel_theme import THEME, apply_zero_row_conditional_formatting  # noqa: E402
from databook_periods import (  # noqa: E402
    days_in_month_formula,
    group_month_columns_by_reporting_fy,
    master_fy_label,
    ordered_month_columns_from_df,
)
from report_row_layout import (  # noqa: E402
    WC_BUCKET_LABELS,
    build_working_capital_row_structure,
    l3_order_from_mapping,
)

from databook_workbook import MASTER_WORKBOOK_STR  # noqa: E402

DESKTOP_DIR = PROJECT_ROOT / "Desktop"
DEFAULT_INPUT = MASTER_WORKBOOK_STR
DEFAULT_MAPPING_BS = str(DESKTOP_DIR / "BS_recon_Mapping.xlsx")

SHEET_MASTER_BS = "Master_BS"
SHEET_MASTER_PL = "Master_PL"
SHEET_PL_RECON = "PL_Reconciliation"
SHEET_OUT = "Working_Capital"

SOURCE_COL_CANDIDATES = ("L5", "L6")
REPORTED_FILTER_VALUE = "reported"

MAP_START_COL = 5
MAP_END_COL = 9
POS_COL = 10

PROJECT_TITLE_ROW = 1
SUBTITLE_ROW = 2
TITLE_ROW = 6
HEADER_ROW = 8
HEADER_ROW_7 = 7
DATA_START_ROW = 9

ROW_HEIGHT = 12
POS_COL_WIDTH = 32
MAP_COL_WIDTH = 12
VALUE_COL_WIDTH = 7.86

FONT_BASE = THEME.font_base
FONT_BASE_BOLD = THEME.font_bold
FONT_HEADER = THEME.font_header
FONT_MAPPING = Font(name=THEME.font_name, size=THEME.font_size, color=THEME.text_header)
FONT_TITLE = Font(
    name=THEME.font_name, size=THEME.font_size, color=THEME.text_brand_title, bold=True
)
FONT_PROJECT = Font(
    name=THEME.font_name, size=THEME.font_size_title, color=THEME.text_brand_title
)
FONT_SUBTITLE = Font(
    name=THEME.font_name, size=THEME.font_size_subtitle, color=THEME.text_brand_title
)
FONT_KPI = Font(name=THEME.font_name, size=THEME.font_size, color=THEME.text_kpi)
FONT_KPI_ITALIC = Font(
    name=THEME.font_name, size=THEME.font_size, color=THEME.text_kpi, italic=True
)
FONT_KPI_SECTION = Font(
    name=THEME.font_name, size=THEME.font_size, color=THEME.text_brand_title, bold=True
)

FILL_GREY = THEME.fill_tech
FILL_WHITE = THEME.fill_white
FILL_HEADER = THEME.fill_header
FILL_SUBTOTAL = THEME.fill_subtotal
FILL_KPI = THEME.fill_subtotal
FILL_PERIOD = THEME.fill_period

ALIGN_LEFT = Alignment(horizontal="left", vertical="center")
ALIGN_RIGHT = Alignment(horizontal="right", vertical="center")

THIN_SIDE = Side(style="thin", color=THEME.border_color)
TOP_BORDER = Border(top=THIN_SIDE)
BOTTOM_BORDER = Border(bottom=THIN_SIDE)
TOP_BOTTOM_BORDER = Border(top=THIN_SIDE, bottom=THIN_SIDE)
BORDER_SUBTOTAL_TOP = THEME.border_subtotal_top
BORDER_KPI_SECTION = THEME.border_kpi_section_top

NUM_FMT_INT = "#,##0;(#,##0);-"
NUM_FMT_KPI = "0.0;(0.0);-"

KPI_SPECS = [
    ("DIO - Days Inventory Outstanding", "dio"),
    ("DSO - Days Sales Outstanding", "dso"),
    ("DPO - Days Payable Outstanding", "dpo"),
    ("CCC - Cash Conversion Cycle", "ccc"),
]

PL_LABEL_FALLBACKS = {
    "cogs": "cost of goods sold",
    "net_sales": "net sales",
}


def col_letter(idx: int) -> str:
    return get_column_letter(idx)


def load_argv_config() -> dict:
    if len(sys.argv) < 2:
        return {}
    path = Path(sys.argv[1])
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def path_value(cfg: dict, key: str, default: str) -> str:
    paths = cfg.get("paths") or {}
    val = paths.get(key)
    return str(val) if val else default


def resolve_config() -> dict:
    raw = load_argv_config()
    paths = raw.get("paths") or {}
    return {
        "input_file": path_value(raw, "input_file", path_value(raw, "master_file", DEFAULT_INPUT)),
        "mapping_file": paths.get("mapping_file") or DEFAULT_MAPPING_BS,
        "project_name": str(raw.get("project_name") or "Desktop Test").strip(),
        "company_name": str(raw.get("company_name") or "Group").strip(),
        "fy_end_month": int(raw.get("fy_end_month") or 12),
        "fiscal_start_month": raw.get("fiscal_start_month"),
    }


def detect_source_col(df_: pd.DataFrame) -> str | None:
    candidates = ["Quelle", "Source", "Reported/Adjusted", "Type", "Scenario"]
    sc = next((c for c in candidates if c in df_.columns), None)
    if sc is not None:
        return sc
    for c in df_.columns:
        try:
            s = df_[c].astype(str).str.strip().str.lower()
            if (s == "reported").any():
                return c
        except Exception:
            pass
    return None


def resolve_source_section_column(df_: pd.DataFrame) -> str | None:
    markers = {REPORTED_FILTER_VALUE, "adjusted", "consolidation"}

    def marker_count(col: str) -> int:
        if col not in df_.columns:
            return 0
        series = df_[col].astype(str).str.strip().str.lower()
        return int(series.isin(markers).sum())

    best_col = None
    best_count = 0
    for col in SOURCE_COL_CANDIDATES:
        count = marker_count(col)
        if count > best_count:
            best_col = col
            best_count = count
    if best_col and best_count > 0:
        return best_col
    legacy = detect_source_col(df_)
    if legacy:
        return legacy
    for col in SOURCE_COL_CANDIDATES:
        if col in df_.columns:
            return col
    return None


def detect_na_bucket_col(df_: pd.DataFrame) -> str:
    for cand in ["NA", "L5 - GT NA View", "L5 - GT NA view", "L5 - GT NA", "L5"]:
        if cand in df_.columns:
            return cand
    for c in df_.columns:
        if str(c).strip() == "NA" or str(c).strip().startswith("L5"):
            return c
    raise ValueError("Keine NA-/L5-Bucket-Spalte gefunden.")


def normalize_na_bucket(x) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "Other"
    s = str(x).strip()
    if s in ("TWC", "OWC"):
        return s
    if s.startswith("TWC"):
        return "TWC"
    if s.startswith("OWC"):
        return "OWC"
    return "Other"


def find_row_by_label_contains(ws_, col_idx: int, needle: str) -> int | None:
    needle_l = needle.lower()
    for rr in range(1, ws_.max_row + 1):
        v = ws_.cell(rr, col_idx).value
        if isinstance(v, str) and needle_l in v.lower():
            return rr
    return None


def resolve_pl_l3_label(wb, kind: str) -> str:
    """Resolve PL L3 label from PL_Reconciliation Aggregated block or fallback."""
    fallback_needle = PL_LABEL_FALLBACKS[kind]
    if SHEET_PL_RECON in wb.sheetnames:
        ws_pl = wb[SHEET_PL_RECON]
        for col in range(1, min(ws_pl.max_column or 0, 30) + 1):
            hdr = ws_pl.cell(HEADER_ROW, col).value
            if isinstance(hdr, str) and "aggregated" in hdr.lower():
                row = find_row_by_label_contains(ws_pl, col, fallback_needle)
                if row is not None:
                    val = ws_pl.cell(row, col).value
                    if isinstance(val, str) and val.strip():
                        return val.strip()
        row = find_row_by_label_contains(ws_pl, POS_COL, fallback_needle)
        if row is not None:
            val = ws_pl.cell(row, POS_COL).value
            if isinstance(val, str) and val.strip():
                return val.strip()
    return "Cost of goods sold" if kind == "cogs" else "Net sales"


def subtotal_span_for_l3(struct, idx: int, bucket: str, l3_label: str) -> tuple[int, int]:
    k = idx - 1
    while (
        k >= 0
        and struct[k]["type"] == "detail"
        and struct[k].get("NA") == bucket
        and struct[k].get("L3") == l3_label
    ):
        k -= 1
    start = DATA_START_ROW + (k + 1)
    end = DATA_START_ROW + (idx - 1)
    return start, end


def bucket_l4_refs(struct, bucket: str, col_l: str) -> list[str]:
    refs: list[str] = []
    for rr in struct:
        if rr.get("NA") != bucket:
            continue
        if rr["type"] in {"detail", "detail_single"}:
            refs.append(f"{col_l}{rr['_excel_row']}")
    return refs


def main() -> None:
    cfg = resolve_config()
    input_file = cfg["input_file"]
    mapping_file = cfg["mapping_file"]
    project_name = cfg["project_name"]
    group_name = cfg["company_name"]
    fy_end_month = cfg["fy_end_month"]

    print("Working_capital —")
    print(f"  input:   {input_file}")
    print(f"  mapping: {mapping_file}")

    wb = load_workbook(input_file)
    if SHEET_MASTER_BS not in wb.sheetnames:
        raise RuntimeError(f"Sheet '{SHEET_MASTER_BS}' nicht gefunden in {input_file}")

    if SHEET_OUT in wb.sheetnames:
        del wb[SHEET_OUT]
    ws = wb.create_sheet(SHEET_OUT)

    df_bs = pd.read_excel(input_file, sheet_name=SHEET_MASTER_BS, engine="openpyxl")
    MONTHS = ordered_month_columns_from_df(df_bs)
    if not MONTHS:
        raise ValueError("Keine Monats-Spalten im Master_BS gefunden.")

    Y_COLS = list(range(POS_COL + 1, POS_COL + 1 + len(MONTHS)))
    source_col = resolve_source_section_column(df_bs)
    na_col = detect_na_bucket_col(df_bs)

    required = {"Entity", "L2", "L3", "L4", na_col} | set(MONTHS)
    missing = required - set(df_bs.columns)
    if missing:
        raise ValueError(f"Master_BS fehlt Spalten: {missing}")

    map_df = pd.read_excel(mapping_file, sheet_name=0, engine="openpyxl")
    if "L3" not in map_df.columns:
        raise ValueError("BS-Mapping muss Spalte 'L3' enthalten.")
    mapping_l3_order = l3_order_from_mapping(map_df)

    master_bs_ref = SHEET_MASTER_BS
    master_start = 2
    master_end = len(df_bs) + 1

    def master_bs_range(colname: str) -> str:
        loc = df_bs.columns.get_loc(colname) + 1
        letter = get_column_letter(loc)
        return f"{master_bs_ref}!${letter}${master_start}:${letter}${master_end}"

    na_rng = master_bs_range(na_col)
    l3_rng = master_bs_range("L3")
    l4_rng = master_bs_range("L4")
    src_rng = master_bs_range(source_col) if source_col else None
    month_rng = {m: master_bs_range(m) for m in MONTHS}

    df_pl = None
    pl_l3_rng = None
    pl_src_rng = None
    pl_month_rng: dict[str, str] = {}
    pl_l3_cogs = resolve_pl_l3_label(wb, "cogs")
    pl_l3_ns = resolve_pl_l3_label(wb, "net_sales")

    if SHEET_MASTER_PL in wb.sheetnames:
        df_pl = pd.read_excel(input_file, sheet_name=SHEET_MASTER_PL, engine="openpyxl")
        pl_source = resolve_source_section_column(df_pl)
        if pl_source and "L3" in df_pl.columns:
            pl_master_ref = SHEET_MASTER_PL
            pl_end = len(df_pl) + 1

            def master_pl_range(colname: str) -> str:
                loc = df_pl.columns.get_loc(colname) + 1
                letter = get_column_letter(loc)
                return f"{pl_master_ref}!${letter}${master_start}:${letter}${pl_end}"

            pl_l3_rng = master_pl_range("L3")
            pl_src_rng = master_pl_range(pl_source)
            for m in MONTHS:
                if m in df_pl.columns:
                    pl_month_rng[m] = master_pl_range(m)

    row_structure = build_working_capital_row_structure(
        df_bs,
        mapping_l3_order,
        {},
        source_col=source_col or na_col,
        bucket_col=na_col,
        normalize_bucket_fn=normalize_na_bucket,
    )

    fy_groups = group_month_columns_by_reporting_fy(MONTHS, fy_end_month)
    for fy_end_year, cols in fy_groups.items():
        row_structure.append(
            {
                "type": "fy_avg",
                "label": f"Yearly average {master_fy_label(fy_end_year)}",
                "fy_end_year": fy_end_year,
                "month_cols": cols,
            }
        )

    row_structure.append({"type": "kpi_section", "label": "KPIs"})
    for label, key in KPI_SPECS:
        row_structure.append({"type": "kpi", "label": label, "kpi_key": key})

    for i, r in enumerate(row_structure):
        r["_idx"] = i
        r["_excel_row"] = DATA_START_ROW + i

    LAST_TABLE_ROW = DATA_START_ROW + len(row_structure) - 1
    bucket_total_row: dict[str, int] = {}
    nwc_rownum: int | None = None
    for r in row_structure:
        if r["type"] == "total_na":
            bucket_total_row[r.get("bucket", "")] = r["_excel_row"]
        if r["type"] == "total_nwc":
            nwc_rownum = r["_excel_row"]

    # Header / titles
    pt = ws.cell(PROJECT_TITLE_ROW, POS_COL, f"Project {project_name}")
    pt.font = FONT_PROJECT
    pt.alignment = ALIGN_LEFT
    ws.row_dimensions[PROJECT_TITLE_ROW].height = 36

    st = ws.cell(SUBTITLE_ROW, POS_COL, f"{group_name} Working capital")
    st.font = FONT_SUBTITLE
    st.alignment = ALIGN_LEFT

    tt = ws.cell(TITLE_ROW, POS_COL, f"{group_name} | Working capital {MONTHS[0]} - {MONTHS[-1]}")
    tt.font = FONT_TITLE
    tt.alignment = ALIGN_LEFT

    for hdr_col, hdr_txt in enumerate(["Reported", "", "NA", "L3", "L4"], start=MAP_START_COL):
        ws.cell(HEADER_ROW, hdr_col, hdr_txt).font = FONT_HEADER

    for c in range(MAP_START_COL, MAP_END_COL + 1):
        ws.cell(HEADER_ROW, c).alignment = ALIGN_LEFT
        ws.cell(HEADER_ROW, c).fill = FILL_HEADER
        ws.cell(HEADER_ROW_7, c).fill = FILL_HEADER

    for cc in [POS_COL] + Y_COLS:
        ws.cell(HEADER_ROW_7, cc).fill = FILL_HEADER

    ws.cell(HEADER_ROW, POS_COL, "kEUR").font = FONT_HEADER
    ws.cell(HEADER_ROW, POS_COL).alignment = ALIGN_LEFT
    ws.cell(HEADER_ROW, POS_COL).fill = FILL_HEADER

    for idx, month in enumerate(MONTHS):
        cc = Y_COLS[idx]
        h = ws.cell(HEADER_ROW, cc, month)
        h.font = FONT_HEADER
        h.alignment = ALIGN_RIGHT
        h.fill = FILL_PERIOD

    for cc in range(MAP_START_COL, Y_COLS[-1] + 1):
        ws.cell(HEADER_ROW, cc).border = BOTTOM_BORDER

    for c in range(MAP_START_COL, MAP_END_COL + 1):
        ws.column_dimensions[col_letter(c)].width = MAP_COL_WIDTH
    ws.column_dimensions[col_letter(POS_COL)].width = POS_COL_WIDTH
    for c in Y_COLS:
        ws.column_dimensions[col_letter(c)].width = VALUE_COL_WIDTH

    for c in range(MAP_START_COL, MAP_END_COL + 1):
        ws.column_dimensions[col_letter(c)].hidden = True
        ws.column_dimensions[col_letter(c)].outlineLevel = 1
    ws.column_dimensions[col_letter(MAP_START_COL)].collapsed = True

    map_rep_col = col_letter(MAP_START_COL)
    map_na_col = col_letter(MAP_START_COL + 2)
    map_l3_col = col_letter(MAP_START_COL + 3)
    map_l4_col = col_letter(MAP_START_COL + 4)

    ws.sheet_properties.outlinePr.summaryBelow = True
    ws.sheet_properties.outlinePr.summaryRight = True

    # Data rows
    for i, r in enumerate(row_structure):
        excel_row = r["_excel_row"]
        ws.row_dimensions[excel_row].height = ROW_HEIGHT
        rt = r["type"]

        if rt not in {"kpi_section"}:
            ws.cell(excel_row, MAP_START_COL, "Reported").font = FONT_MAPPING
            ws.cell(excel_row, MAP_START_COL + 1, "").font = FONT_MAPPING
            ws.cell(excel_row, MAP_START_COL + 2, r.get("NA", "")).font = FONT_MAPPING
            ws.cell(excel_row, MAP_START_COL + 3, r.get("L3", "")).font = FONT_MAPPING
            ws.cell(excel_row, MAP_START_COL + 4, r.get("L4", "")).font = FONT_MAPPING
            for c in range(MAP_START_COL, MAP_END_COL + 1):
                ws.cell(excel_row, c).alignment = ALIGN_LEFT

        pc = ws.cell(excel_row, POS_COL, r["label"])
        pc.alignment = ALIGN_LEFT

        is_bold = rt in {"total_na", "total_nwc"}
        is_kpi = rt == "kpi"
        is_fy_avg = rt == "fy_avg"

        if rt in {"detail", "detail_single"}:
            pc.alignment = Alignment(horizontal="left", vertical="center", indent=1)
            ws.row_dimensions[excel_row].outlineLevel = 1
            ws.row_dimensions[excel_row].collapsed = True
        elif rt == "subtotal_l3":
            ws.row_dimensions[excel_row].outlineLevel = 0
        elif rt == "total_na":
            for c in range(MAP_START_COL, Y_COLS[-1] + 1):
                ws.cell(excel_row, c).fill = FILL_SUBTOTAL
            pc.border = BORDER_SUBTOTAL_TOP
            ws.row_dimensions[excel_row].outlineLevel = 0
        elif rt == "total_nwc":
            for c in range(MAP_START_COL, Y_COLS[-1] + 1):
                ws.cell(excel_row, c).fill = FILL_SUBTOTAL
            pc.font = FONT_BASE_BOLD
            for cc in Y_COLS:
                ws.cell(excel_row, cc).border = TOP_BOTTOM_BORDER
            pc.border = TOP_BOTTOM_BORDER
            ws.row_dimensions[excel_row].outlineLevel = 0
        elif rt == "fy_avg":
            pc.font = FONT_BASE
            ws.row_dimensions[excel_row].outlineLevel = 0
        elif rt == "kpi_section":
            pc.font = FONT_KPI_SECTION
            pc.fill = FILL_HEADER
            pc.border = BORDER_KPI_SECTION
            for cc in Y_COLS:
                ws.cell(excel_row, cc).fill = FILL_HEADER
                ws.cell(excel_row, cc).border = BORDER_KPI_SECTION
        elif rt == "kpi":
            pc.font = FONT_KPI
            for cc in Y_COLS:
                ws.cell(excel_row, cc).fill = FILL_KPI

        pc.font = FONT_BASE_BOLD if is_bold else (FONT_KPI if is_kpi else FONT_BASE)

        rep_crit = f"${map_rep_col}${excel_row}"
        na_crit = f"${map_na_col}${excel_row}"
        l3_crit = f"${map_l3_col}${excel_row}"
        l4_crit = f"${map_l4_col}${excel_row}"

        for m_idx, month in enumerate(MONTHS):
            cell = ws.cell(excel_row, Y_COLS[m_idx])
            cell.alignment = ALIGN_RIGHT
            cell.number_format = NUM_FMT_KPI if is_kpi else NUM_FMT_INT
            col_l = col_letter(Y_COLS[m_idx])

            if rt in {"detail", "detail_single"}:
                sum_rng = month_rng[month]
                if src_rng is not None:
                    cell.value = (
                        f"=SUMIFS({sum_rng},"
                        f"{src_rng},{rep_crit},"
                        f"{na_rng},{na_crit},"
                        f"{l3_rng},{l3_crit},"
                        f"{l4_rng},{l4_crit}"
                        f")/1000"
                    )
                else:
                    cell.value = (
                        f"=SUMIFS({sum_rng},"
                        f"{na_rng},{na_crit},"
                        f"{l3_rng},{l3_crit},"
                        f"{l4_rng},{l4_crit}"
                        f")/1000"
                    )
                cell.fill = FILL_PERIOD
            elif rt == "subtotal_l3":
                start, end = subtotal_span_for_l3(row_structure, i, r["NA"], r["label"])
                cell.value = f"=SUM({col_l}{start}:{col_l}{end})" if end >= start else 0
            elif rt == "total_na":
                refs = bucket_l4_refs(row_structure, r["bucket"], col_l)
                cell.value = f"=SUM({','.join(refs)})" if refs else 0
                cell.border = BORDER_SUBTOTAL_TOP
            elif rt == "total_nwc":
                twc = bucket_total_row.get("TWC")
                owc = bucket_total_row.get("OWC")
                parts = []
                if twc:
                    parts.append(f"{col_l}{twc}")
                if owc:
                    parts.append(f"{col_l}{owc}")
                cell.value = f"=SUM({','.join(parts)})" if parts else 0
                cell.border = TOP_BOTTOM_BORDER
            elif rt == "fy_avg" and nwc_rownum:
                if month in r.get("month_cols", []):
                    refs = [
                        f"{col_letter(Y_COLS[MONTHS.index(mc)])}{nwc_rownum}"
                        for mc in r["month_cols"]
                    ]
                    cell.value = f"=AVERAGE({','.join(refs)})"
                else:
                    cell.value = ""
            elif rt == "kpi":
                cell.font = FONT_KPI

        if rt == "subtotal_l3":
            for cc in range(MAP_START_COL, Y_COLS[-1] + 1):
                ws.cell(excel_row, cc).fill = FILL_WHITE
        elif rt in {"detail", "detail_single"}:
            for cc in range(MAP_START_COL, MAP_END_COL + 1):
                ws.cell(excel_row, cc).fill = FILL_WHITE

    # PL helper rows (hidden) for KPI denominators
    helper_start = LAST_TABLE_ROW + 2
    cogs_row = helper_start
    ns_row = helper_start + 1
    for rr, label in [(cogs_row, pl_l3_cogs), (ns_row, pl_l3_ns)]:
        ws.row_dimensions[rr].hidden = True
        ws.row_dimensions[rr].outlineLevel = 2
        ws.cell(rr, POS_COL, label).font = FONT_KPI
        ws.cell(rr, MAP_START_COL + 3, label).font = FONT_MAPPING

    for m_idx, month in enumerate(MONTHS):
        cc = Y_COLS[m_idx]
        rep_cogs = f"${map_rep_col}${cogs_row}"
        rep_ns = f"${map_rep_col}${ns_row}"
        l3_cogs = f"${map_l3_col}${cogs_row}"
        l3_ns = f"${map_l3_col}${ns_row}"
        if pl_month_rng.get(month) and pl_l3_rng and pl_src_rng:
            ws.cell(cogs_row, cc).value = (
                f"=SUMIFS({pl_month_rng[month]},{pl_src_rng},{rep_cogs},"
                f"{pl_l3_rng},{l3_cogs})/1000"
            )
            ws.cell(ns_row, cc).value = (
                f"=SUMIFS({pl_month_rng[month]},{pl_src_rng},{rep_ns},"
                f"{pl_l3_rng},{l3_ns})/1000"
            )
        else:
            ws.cell(cogs_row, cc).value = 0
            ws.cell(ns_row, cc).value = 0

    row_inventory = find_row_by_label_contains(ws, POS_COL, "inventories")
    row_recv = find_row_by_label_contains(ws, POS_COL, "trade receivables")
    row_pay = find_row_by_label_contains(ws, POS_COL, "trade payables")

    kpi_rows_by_key: dict[str, int] = {}
    for r in row_structure:
        if r["type"] == "kpi":
            kpi_rows_by_key[r.get("kpi_key", "")] = r["_excel_row"]

    for r in row_structure:
        if r["type"] != "kpi":
            continue
        rr = r["_excel_row"]
        key = r.get("kpi_key", "")
        for m_idx, month in enumerate(MONTHS):
            cc = Y_COLS[m_idx]
            col_l = col_letter(cc)
            days = days_in_month_formula(month)
            cogs_cell = f"{col_l}{cogs_row}"
            ns_cell = f"{col_l}{ns_row}"
            inv_cell = f"{col_l}{row_inventory}" if row_inventory else None
            recv_cell = f"{col_l}{row_recv}" if row_recv else None
            pay_cell = f"{col_l}{row_pay}" if row_pay else None

            if key == "dio" and inv_cell:
                ws.cell(rr, cc).value = f'=IFERROR({inv_cell}/{cogs_cell}*{days},"n/a")'
            elif key == "dso" and recv_cell:
                ws.cell(rr, cc).value = f'=IFERROR({recv_cell}/{ns_cell}*{days},"n/a")'
            elif key == "dpo" and pay_cell:
                ws.cell(rr, cc).value = f'=IFERROR({pay_cell}/{cogs_cell}*{days},"n/a")'
            elif key == "ccc":
                dio_r = kpi_rows_by_key.get("dio")
                dso_r = kpi_rows_by_key.get("dso")
                dpo_r = kpi_rows_by_key.get("dpo")
                if dio_r and dso_r and dpo_r:
                    ws.cell(rr, cc).value = (
                        f'=IFERROR({col_l}{dso_r}+{col_l}{dio_r}-{col_l}{dpo_r},"n/a")'
                    )

    apply_zero_row_conditional_formatting(
        ws,
        first_row=DATA_START_ROW,
        last_row=LAST_TABLE_ROW,
        year_col_indices=Y_COLS,
        style_start_col=POS_COL,
        style_end_col=Y_COLS[-1],
    )

    wb.calculation.fullCalcOnLoad = True
    wb.save(input_file)
    print(f"Saved sheet '{SHEET_OUT}' to {input_file}")


if __name__ == "__main__":
    main()
