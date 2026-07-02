import re
import sys
from pathlib import Path

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.styles import Border, Side
from openpyxl.formatting.rule import CellIsRule

PROJECT_ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from gst_excel_theme import THEME, apply_recon_portfolio_layout, apply_zero_row_conditional_formatting  # noqa: E402
from report_row_layout import build_bs_row_structure, l2_l3_order_from_mapping  # noqa: E402
from databook_workbook import MASTER_WORKBOOK_STR  # noqa: E402

# =============================================
# CONFIG (BS) — Desktop work defaults
# =============================================
DESKTOP_DIR = PROJECT_ROOT / "Desktop"

PROJECT_NAME = "Desktop Test"
GROUP_NAME   = "Group"

YEARS = []  # detected from Master_BS FY columns
UNIT_LABEL = "kEUR"

REC_PERIOD_FROM = ""
REC_PERIOD_TO   = ""

ENTITY_SORT_ORDER = {}

ENTITY_RENAME_MAP = {}

IC_MASTER_ENTITY = "Consolidation"
IC_DISPLAY_NAME  = "IC eliminations"

DIFF_KEY = "Difference"
FS_KEY   = "Financial statements"

# =============================================
# BS TOTALS CONFIG (components refer to L2 labels)
# =============================================
BS_TOTALS_CONFIG = [
    {"label": "Total assets",             "components": ["Fixed assets", "Current assets"], "insert_after": "Current assets"},
    {"label": "Total equity & liabilities","components": ["Equity", "Liabilities"],          "insert_after": "Liabilities"},
]

# =============================================
# BS CHECK VALUES (Financial statements) - per year
# =============================================
SHOW_BS_CHECKS = False

BS_FS_CHECK_TOTAL_ASSETS = {}

# =============================================
# PATHS (BS)
# =============================================
SOURCE_FILE     = MASTER_WORKBOOK_STR
MAPPING_FILE_BS = str(DESKTOP_DIR / "BS_recon_Mapping.xlsx")
TARGET_FILE     = MASTER_WORKBOOK_STR

REPORT_SHEET_BS   = "BS_Reconciliation"
MASTER_SHEET_BS_OUT = "Master_BS"

SOURCE_SHEET_BS = "Master_BS"
SOURCE_HEADER_ROW = 0
SOURCE_COL_CANDIDATES = ("L5", "L6")
REPORTED_FILTER_VALUE = "reported"
L4_SORT_BASIS = "latest_fy"

# =============================================
# LAYOUT (same general geometry as PL)
# =============================================
MAP_START_COL    = 5   # E
POS_COL          = 10  # J
FIRST_ENTITY_COL = 11  # K

PROJECT_TITLE_ROW = 1
SUBTITLE_ROW      = 2

ENTITY_CODE_ROW = 3
HEADER_ROW      = 8
BLOCK_TITLE_ROW = HEADER_ROW - 1
DATA_START_ROW  = HEADER_ROW + 1

# BS mapping columns E..I: Reported, blank, L2, L3, L4
BS_MAP_START_COL = MAP_START_COL
BS_MAP_END_COL   = MAP_START_COL + 4  # I
BS_L2_COL        = MAP_START_COL + 2  # G
BS_L3_COL        = MAP_START_COL + 3  # H
BS_L4_COL        = MAP_START_COL + 4  # I

SPACER_WIDTH     = 1.14
VALUE_COL_WIDTH  = 7.86
ROW_HEIGHT       = 12
REPEAT_POS_WIDTH = 32

# =============================================
# STYLES — gst_excel_theme / Finssentials output
# =============================================
FONT_BASE = THEME.font_base
FONT_BASE_BOLD = THEME.font_bold
FONT_HEADER = THEME.font_header
FONT_MAPPING = Font(name=THEME.font_name, size=THEME.font_size, color=THEME.text_header)
FONT_TITLE = Font(
    name=THEME.font_name,
    size=THEME.font_size,
    bold=True,
    color=THEME.text_brand_title,
)
FONT_PROJECT_TITLE = Font(
    name=THEME.font_name,
    size=THEME.font_size_title,
    color=THEME.text_brand_title,
    bold=False,
)
FONT_SUBTITLE = Font(
    name=THEME.font_name,
    size=THEME.font_size_subtitle,
    color=THEME.text_brand_title,
    bold=False,
)

FILL_TECH = THEME.fill_tech
FILL_WHITE = THEME.fill_white
FILL_HEADER = THEME.fill_header
FILL_SUBTOTAL = THEME.fill_subtotal
CHECK_FILL = PatternFill("solid", fgColor="FFFEF3C7")

ALIGN_LEFT = Alignment(horizontal="left", vertical="center")
ALIGN_CENTER = Alignment(horizontal="center", vertical="center")
ALIGN_RIGHT = Alignment(horizontal="right", vertical="center")

BORDER_HEADER_BOTTOM = THEME.border_header_bottom
BORDER_SUBTOTAL_TOP = THEME.border_subtotal_top
DIFF_FONT = Font(name=THEME.font_name, size=THEME.font_size, color=THEME.delta_negative)

NUM_FMT_INT = '#,##0;(#,##0);"-"'

# =============================================
# HELPERS
# =============================================
def col_letter(idx: int) -> str:
    return get_column_letter(idx)

def is_blank_entity(x):
    if x is None or pd.isna(x):
        return True
    s = str(x).strip()
    return (s == "") or (s.lower() in {"nan", "none", "null"})

def detect_year_columns(df_: pd.DataFrame) -> list[str]:
    pattern = re.compile(r"^FY(\d{2}|\d{4})A$", re.IGNORECASE)
    years = [str(c).strip() for c in df_.columns if pattern.match(str(c).strip())]
    if not years:
        raise ValueError("Keine FY-Spalten im Master_BS gefunden (erwartet z. B. FY22A, FY23A).")

    def year_sort_key(x: str):
        m = pattern.match(x)
        return int(m.group(1))

    return sorted(dict.fromkeys(years), key=year_sort_key)


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


def detect_source_col(df_):
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

def ic_sign(block):
    return 1 if block["kind"] == "ic" else 1

# =============================================
# LOAD BS MAPPING (L2/L3 hierarchy order; L4 ignored)
# =============================================
bs_map_df = pd.read_excel(MAPPING_FILE_BS, sheet_name=0, engine="openpyxl")
bs_map_df = bs_map_df.loc[:, [c for c in bs_map_df.columns if not str(c).startswith("Unnamed")]]
if not {"L2", "L3"}.issubset(bs_map_df.columns):
    raise ValueError("Sortierung_BS_Datenbank muss Spalten 'L2' und 'L3' enthalten.")

bs_map_df = bs_map_df.copy()
bs_map_df["L2"] = bs_map_df["L2"].astype(str)
bs_map_df["L3"] = bs_map_df["L3"].astype(str)
bs_l2_l3_order = l2_l3_order_from_mapping(bs_map_df)

# =============================================
# LOAD MASTER_BS + NORMALIZE ENTITY
# =============================================
df_bs = pd.read_excel(
    SOURCE_FILE,
    sheet_name=SOURCE_SHEET_BS,
    engine="openpyxl",
    header=SOURCE_HEADER_ROW,
)
YEARS = detect_year_columns(df_bs)
REC_PERIOD_FROM = YEARS[0]
REC_PERIOD_TO = YEARS[-1]

source_col_bs = resolve_source_section_column(df_bs)
print(f"Verwende Source-Spalte für Reported/Adjusted: {source_col_bs}")

bs_row_structure = build_bs_row_structure(
    df_bs,
    bs_l2_l3_order,
    {"l4_sort_basis": L4_SORT_BASIS},
    source_col=source_col_bs or SOURCE_COL_CANDIDATES[0],
)

def insert_after_anchor_bs(struct, anchor_label, new_row):
    for i, row in enumerate(struct):
        if row["type"] == "subtotal_l2" and row["label"] == anchor_label:
            struct.insert(i + 1, new_row)
            return True
    return False

existing_labels = {r["label"] for r in bs_row_structure}
for t in BS_TOTALS_CONFIG:
    comps = [c for c in t["components"] if c in existing_labels]
    if not comps:
        continue
    total_row = {
        "type": "total",
        "label": t["label"],
        "components": comps,
        "L2": "",
        "L3": "",
        "L4": "",
    }
    if not insert_after_anchor_bs(bs_row_structure, t["insert_after"], total_row):
        raise RuntimeError(f"Anchor '{t['insert_after']}' for BS total '{t['label']}' not found.")

required_bs = {"Entity", "L2", "L3", "L4"} | set(YEARS)
missing_bs  = required_bs - set(df_bs.columns)
if missing_bs:
    raise ValueError(f"Master_BS fehlt Spalten: {missing_bs}")

df_bs = df_bs.copy()

def normalize_entity_keep_ic(x):
    if x is None or pd.isna(x):
        return x
    s = str(x).strip()
    if s == IC_MASTER_ENTITY:
        return s
    return ENTITY_RENAME_MAP.get(s, s) if ENTITY_RENAME_MAP else s

if ENTITY_RENAME_MAP:
    df_bs["Entity"] = df_bs["Entity"].apply(normalize_entity_keep_ic)
    ENTITY_SORT_ORDER = {ENTITY_RENAME_MAP.get(k, k): v for k, v in ENTITY_SORT_ORDER.items()}

# =============================================
# ENTITY LIST (robust - no order assumptions)
# =============================================
raw_entities = []
for v in pd.unique(df_bs["Entity"]):
    if not is_blank_entity(v):
        raw_entities.append(str(v).strip())

# identify IC bucket explicitly
has_ic = IC_MASTER_ENTITY in raw_entities

# individual entities = all except IC master
individual_entities = [e for e in raw_entities if e != IC_MASTER_ENTITY]

# sort entities by defined order
def entity_sort_key(e):
    return ENTITY_SORT_ORDER.get(e, raw_entities.index(e) if e in raw_entities else 9999)

if ENTITY_SORT_ORDER:
    individual_entities = sorted(individual_entities, key=entity_sort_key)

# =============================================
# WORKBOOK + SHEETS
# =============================================
if Path(TARGET_FILE).is_file():
    wb = load_workbook(TARGET_FILE)
    for sn in (REPORT_SHEET_BS, MASTER_SHEET_BS_OUT):
        if sn in wb.sheetnames:
            del wb[sn]
    ws_bs = wb.create_sheet(REPORT_SHEET_BS)
else:
    wb = Workbook()
    ws_bs = wb.active
    ws_bs.title = REPORT_SHEET_BS

# Copy Master_BS into output workbook (audit)
ws_master_bs = wb.create_sheet(MASTER_SHEET_BS_OUT)
for c_idx, colname in enumerate(df_bs.columns, start=1):
    ws_master_bs.cell(1, c_idx, colname)
for r_idx, row in enumerate(df_bs.itertuples(index=False, name=None), start=2):
    for c_idx, val in enumerate(row, start=1):
        ws_master_bs.cell(r_idx, c_idx, val)

MASTER_START = 2
MASTER_END   = len(df_bs) + 1

def master_range_bs(colname):
    L = get_column_letter(df_bs.columns.get_loc(colname) + 1)
    return f"{MASTER_SHEET_BS_OUT}!${L}${MASTER_START}:${L}${MASTER_END}"

src_rng = master_range_bs(source_col_bs) if source_col_bs is not None else None
ent_rng = master_range_bs("Entity")
l2_rng  = master_range_bs("L2")
l3_rng  = master_range_bs("L3")
l4_rng  = master_range_bs("L4")

# =============================================
# TITLES
# =============================================
pt = ws_bs.cell(PROJECT_TITLE_ROW, POS_COL, f"Project {PROJECT_NAME}")
pt.font      = FONT_PROJECT_TITLE
pt.alignment = ALIGN_LEFT
ws_bs.row_dimensions[PROJECT_TITLE_ROW].height = 36

st = ws_bs.cell(SUBTITLE_ROW, POS_COL, f"{GROUP_NAME} Reconciliation - Balance sheet")
st.font      = FONT_SUBTITLE
st.alignment = ALIGN_LEFT

# =============================================
# MAIN RECONCILIATION TITLE (row 6, column J)
# =============================================
rec_title_text = (
    f"{GROUP_NAME} | Reconciliation (Trial Balances) "
    f"{REC_PERIOD_FROM} - {REC_PERIOD_TO}"
)

rec_title           = ws_bs.cell(6, POS_COL, rec_title_text)
rec_title.font      = FONT_TITLE
rec_title.alignment = ALIGN_LEFT

# =============================================
# HEADERS (E..I mapping, J label)
# =============================================
ws_bs.cell(HEADER_ROW, BS_MAP_START_COL + 0, "Reported").font = FONT_HEADER
ws_bs.cell(HEADER_ROW, BS_MAP_START_COL + 1, "").font         = FONT_HEADER
ws_bs.cell(HEADER_ROW, BS_L2_COL, "L2").font                  = FONT_HEADER
ws_bs.cell(HEADER_ROW, BS_L3_COL, "L3").font                  = FONT_HEADER
ws_bs.cell(HEADER_ROW, BS_L4_COL, "L4").font                  = FONT_HEADER

for c in range(BS_MAP_START_COL, BS_MAP_END_COL + 1):
    ws_bs.cell(HEADER_ROW, c).alignment = ALIGN_LEFT

ph           = ws_bs.cell(HEADER_ROW, POS_COL, UNIT_LABEL)
ph.font      = FONT_HEADER
ph.alignment = ALIGN_LEFT

# =============================================
# BLOCK PLAN (Entities / Aggregated / IC / Consolidation / Difference / FS)
# =============================================
blocks = []
for e in individual_entities:
    blocks.append({"kind": "entity", "key": e, "code": e, "title": e, "has_plpos": False})

if len(individual_entities) > 0:
    blocks.append({"kind": "aggregated", "key": "Aggregated", "code": "Aggregated", "title": "Aggregated", "has_plpos": False})

if has_ic:
    blocks.append({"kind": "ic", "key": IC_DISPLAY_NAME, "code": IC_MASTER_ENTITY, "title": IC_DISPLAY_NAME, "has_plpos": False})

if len(individual_entities) > 0 and has_ic:
    blocks.append({"kind": "consolidation", "key": "Consolidation", "code": "Consolidation", "title": "Consolidation", "has_plpos": False})

blocks.append({"kind": "difference", "key": DIFF_KEY, "code": DIFF_KEY, "title": DIFF_KEY, "has_plpos": True})
blocks.append({"kind": "fs",         "key": FS_KEY,   "code": FS_KEY,   "title": FS_KEY,   "has_plpos": True})

block_startcol = {}
current_col    = FIRST_ENTITY_COL

for b in blocks:
    b["startcol"]        = current_col
    block_startcol[b["key"]] = current_col

    extra              = 1 if b.get("has_plpos") else 0
    b["poscol"]        = current_col if extra == 1 else None
    b["year_startcol"] = current_col + extra
    b["year_endcol"]   = b["year_startcol"] + len(YEARS) - 1
    b["spacer_col"]    = b["year_endcol"] + 1

    current_col = b["spacer_col"] + 1

LAST_USED_COL = current_col - 1

# block title + entity code row + year headers
for b in blocks:
    if b.get("has_plpos"):
        ws_bs.merge_cells(start_row=BLOCK_TITLE_ROW, start_column=b["year_startcol"],
                          end_row=BLOCK_TITLE_ROW,   end_column=b["year_endcol"])
        t           = ws_bs.cell(BLOCK_TITLE_ROW, b["year_startcol"], b["title"])
        t.font      = FONT_BASE_BOLD
        t.alignment = ALIGN_CENTER
        ws_bs.cell(BLOCK_TITLE_ROW, b["poscol"]).value = None
        ws_bs.cell(BLOCK_TITLE_ROW, b["poscol"]).fill = FILL_WHITE
    else:
        ws_bs.merge_cells(start_row=BLOCK_TITLE_ROW, start_column=b["startcol"],
                          end_row=BLOCK_TITLE_ROW,   end_column=b["year_endcol"])
        t           = ws_bs.cell(BLOCK_TITLE_ROW, b["startcol"], b["title"])
        t.font      = FONT_BASE_BOLD
        t.alignment = ALIGN_CENTER

    if b.get("has_plpos"):
        ws_bs.merge_cells(start_row=ENTITY_CODE_ROW, start_column=b["year_startcol"],
                          end_row=ENTITY_CODE_ROW,   end_column=b["year_endcol"])
        ec = ws_bs.cell(ENTITY_CODE_ROW, b["year_startcol"], b["code"])
    else:
        ws_bs.merge_cells(start_row=ENTITY_CODE_ROW, start_column=b["startcol"],
                          end_row=ENTITY_CODE_ROW,   end_column=b["year_endcol"])
        ec = ws_bs.cell(ENTITY_CODE_ROW, b["startcol"], b["code"])

    ec.font      = FONT_BASE
    ec.alignment = ALIGN_CENTER

    if b.get("has_plpos"):
        hpos           = ws_bs.cell(HEADER_ROW, b["poscol"], UNIT_LABEL)
        hpos.font      = FONT_HEADER
        hpos.alignment = ALIGN_LEFT

    for y_idx, year in enumerate(YEARS):
        h           = ws_bs.cell(HEADER_ROW, b["year_startcol"] + y_idx, year)
        h.font      = FONT_HEADER
        h.alignment = ALIGN_CENTER

    ws_bs.column_dimensions[col_letter(b["spacer_col"])].width = SPACER_WIDTH

# =============================================
# WRITE ROWS (mapping + labels + repeat positions in Difference/FS)
# =============================================
for i, r in enumerate(bs_row_structure):
    r["_idx"]      = i
    excel_row      = DATA_START_ROW + i
    r["_excel_row"] = excel_row

    # mapping columns
    ws_bs.cell(excel_row, BS_MAP_START_COL + 0, "Reported").font = FONT_MAPPING
    ws_bs.cell(excel_row, BS_MAP_START_COL + 1, "").font         = FONT_MAPPING
    ws_bs.cell(excel_row, BS_L2_COL, r["L2"]).font               = FONT_MAPPING
    ws_bs.cell(excel_row, BS_L3_COL, r["L3"]).font               = FONT_MAPPING
    ws_bs.cell(excel_row, BS_L4_COL, r["L4"]).font               = FONT_MAPPING

    for c in range(BS_MAP_START_COL, BS_MAP_END_COL + 1):
        ws_bs.cell(excel_row, c).alignment = ALIGN_LEFT

    # main label column J
    pc = ws_bs.cell(excel_row, POS_COL, r["label"])

    if (
        r["type"] == "detail"
        and str(r["L3"]).strip() != str(r["L4"]).strip()
    ):
        pc.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    else:
        pc.alignment = ALIGN_LEFT

    if r["type"] in {"subtotal_l2", "total"}:
        pc.font = FONT_BASE_BOLD
    else:
        pc.font = FONT_BASE

    # repeat BS positions in Difference / FS blocks (has_plpos=True)
    for b in blocks:
        if not b.get("has_plpos"):
            continue

        pcell       = ws_bs.cell(excel_row, b["poscol"])
        pcell.value = f"={col_letter(POS_COL)}{excel_row}"

        # alignment: mirror indentation rule (do NOT copy StyleProxy)
        if (
            r["type"] == "detail"
            and str(r["L3"]).strip() != str(r["L4"]).strip()
        ):
            pcell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        else:
            pcell.alignment = ALIGN_LEFT

        # font: same rule as main label column
        if r["type"] in {"subtotal_l2", "total"}:
            pcell.font = FONT_BASE_BOLD
        else:
            pcell.font = FONT_BASE

    # row height + outline
    ws_bs.row_dimensions[excel_row].height = ROW_HEIGHT
    if r["type"] == "detail":
        ws_bs.row_dimensions[excel_row].outlineLevel = 1
        ws_bs.row_dimensions[excel_row].hidden = False
    elif r["type"] == "detail_single" and str(r["L3"]).strip() != str(r["L4"]).strip():
        ws_bs.row_dimensions[excel_row].outlineLevel = 1
        ws_bs.row_dimensions[excel_row].hidden = False
    elif r["type"] in {"detail_single", "subtotal_l3", "subtotal_l2", "total"}:
        ws_bs.row_dimensions[excel_row].outlineLevel = 0
        ws_bs.row_dimensions[excel_row].hidden = False

# =============================================
# VALUES (entity / aggregated / consolidation / difference / FS)
# =============================================
bs_l2_col = col_letter(BS_L2_COL)  # G
bs_l3_col = col_letter(BS_L3_COL)  # H
bs_l4_col = col_letter(BS_L4_COL)  # I

def block_lookup(key):
    return next(bb for bb in blocks if bb["key"] == key)

for b in blocks:
    if b["kind"] in {"entity", "ic"}:
        entity_ref = f"${col_letter(b['startcol'])}${ENTITY_CODE_ROW}"
    else:
        entity_ref = None

    sign = ic_sign(b)

    for r in bs_row_structure:
        excel_row = r["_excel_row"]

        for y_idx, year in enumerate(YEARS):
            cell                = ws_bs.cell(excel_row, b["year_startcol"] + y_idx)
            cell.alignment      = ALIGN_RIGHT
            cell.number_format  = NUM_FMT_INT

            # values bold: L2 + totals only
            if r["type"] in {"subtotal_l2", "total"}:
                cell.font = FONT_BASE_BOLD
            else:
                cell.font = FONT_BASE

            if b["kind"] in {"entity", "ic"}:

                if r["type"] in {"detail", "detail_single"}:
                    sum_rng    = master_range_bs(year)
                    l2_crit    = f"${bs_l2_col}${excel_row}"
                    l3_crit    = f"${bs_l3_col}${excel_row}"
                    l4_crit    = f"${bs_l4_col}${excel_row}"
                    bs_rep_col = col_letter(BS_MAP_START_COL)
                    rep_crit   = f"${bs_rep_col}${excel_row}"

                    if src_rng is not None:
                        cell.value = (
                            f"={sign}*SUMIFS({sum_rng},"
                            f"{ent_rng},{entity_ref},"
                            f"{src_rng},{rep_crit},"
                            f"{l2_rng},{l2_crit},"
                            f"{l3_rng},{l3_crit},"
                            f"{l4_rng},{l4_crit}"
                            f")/1000"
                        )
                    else:
                        cell.value = (
                            f"={sign}*SUMIFS({sum_rng},"
                            f"{ent_rng},{entity_ref},"
                            f"{l2_rng},{l2_crit},"
                            f"{l3_rng},{l3_crit},"
                            f"{l4_rng},{l4_crit}"
                            f")/1000"
                        )

                elif r["type"] == "subtotal_l3":
                    idx = r["_idx"]
                    k = idx - 1
                    while (
                        k >= 0
                        and bs_row_structure[k]["type"] == "detail"
                        and bs_row_structure[k]["L3"] == r["L3"]
                        and bs_row_structure[k]["L2"] == r["L2"]
                    ):
                        k -= 1
                    detail_start_idx = k + 1
                    detail_end_idx = idx - 1

                    colL = col_letter(b["year_startcol"] + y_idx)
                    if detail_end_idx >= detail_start_idx:
                        start_row = DATA_START_ROW + detail_start_idx
                        end_row = DATA_START_ROW + detail_end_idx
                        cell.value = f"=SUM({colL}{start_row}:{colL}{end_row})"
                    else:
                        cell.value = 0

                elif r["type"] == "subtotal_l2":
                    colL = col_letter(b["year_startcol"] + y_idx)
                    refs = []
                    for rr in bs_row_structure:
                        if rr["L2"] != r["L2"]:
                            continue
                        if rr["type"] == "subtotal_l3":
                            refs.append(f"{colL}{rr['_excel_row']}")
                        elif (
                            rr["type"] == "detail_single"
                            and str(rr["L3"]).strip() == str(rr["L4"]).strip()
                        ):
                            refs.append(f"{colL}{rr['_excel_row']}")
                    cell.value = f"=SUM({','.join(refs)})" if refs else 0

                elif r["type"] == "total":
                    colL = col_letter(b["year_startcol"] + y_idx)
                    refs = []
                    for comp in r.get("components", []):
                        for rr in bs_row_structure:
                            if rr["type"] == "subtotal_l2" and rr["label"] == comp:
                                refs.append(f"{colL}{rr['_excel_row']}")
                                break
                    cell.value = f"=SUM({','.join(refs)})" if refs else 0

            elif b["kind"] == "aggregated":
                refs = []
                for e in individual_entities:
                    bb = block_lookup(e)
                    refs.append(f"{col_letter(bb['year_startcol'] + y_idx)}{excel_row}")
                cell.value = f"=SUM({','.join(refs)})" if refs else 0

            elif b["kind"] == "consolidation":
                agg_block = block_lookup("Aggregated")
                ic_block  = block_lookup(IC_DISPLAY_NAME)
                agg_ref   = f"{col_letter(agg_block['year_startcol'] + y_idx)}{excel_row}"
                ic_ref    = f"{col_letter(ic_block['year_startcol'] + y_idx)}{excel_row}"
                cell.value = f"={agg_ref}+{ic_ref}"

            elif b["kind"] == "fs":
                cell.value = None

            elif b["kind"] == "difference":
                fs_block  = block_lookup(FS_KEY)
                con_block = block_lookup("Consolidation")
                fs_ref    = f"{col_letter(fs_block['year_startcol'] + y_idx)}{excel_row}"
                con_ref   = f"{col_letter(con_block['year_startcol'] + y_idx)}{excel_row}"
                cell.value = f"={fs_ref}-{con_ref}"

# =============================================
# ROW FILLS + BORDERS
# =============================================
LAST_TABLE_ROW = DATA_START_ROW + len(bs_row_structure) - 1

for r in bs_row_structure:
    excel_row = r["_excel_row"]
    if r["type"] in {"subtotal_l2", "total"}:
        fill = FILL_SUBTOTAL
    else:
        fill = FILL_WHITE
    for c in range(MAP_START_COL, LAST_USED_COL + 1):
        ws_bs.cell(excel_row, c).fill = fill

    if r["type"] in {"subtotal_l2", "total"}:
        for c in range(MAP_START_COL, LAST_USED_COL + 1):
            ws_bs.cell(excel_row, c).border = BORDER_SUBTOTAL_TOP

# =============================================
# BS CHECK VALUES
# =============================================
def setup_value_cell(cell, red=False):
    cell.alignment     = ALIGN_RIGHT
    cell.number_format = NUM_FMT_INT
    if red:
        cell.font = DIFF_FONT
    else:
        cell.font = FONT_BASE

CHECK_FS_ROW    = LAST_TABLE_ROW + 2
CHECK_DELTA_ROW = LAST_TABLE_ROW + 3
CHECK_BAL_ROW   = LAST_TABLE_ROW + 4

total_assets_row = next(
    (r["_excel_row"] for r in bs_row_structure
     if r["type"] == "total" and r["label"] == "Total assets"),
    None
)
total_el_row = next(
    (r["_excel_row"] for r in bs_row_structure
     if r["type"] == "total" and r["label"] == "Total equity & liabilities"),
    None
)

if SHOW_BS_CHECKS:
    for b in blocks:
        if b["kind"] not in {"entity", "ic", "aggregated", "consolidation"}:
            continue

        fy_vals = BS_FS_CHECK_TOTAL_ASSETS.get(b["key"], {})

        for y_idx, year in enumerate(YEARS):
            # 1) FS total assets value
            c_fs = ws_bs.cell(CHECK_FS_ROW, b["year_startcol"] + y_idx)
            fy_val = fy_vals.get(year, None)
            if fy_val is not None:
                c_fs.value = fy_val / 1000
            else:
                c_fs.value = None
            setup_value_cell(c_fs, red=False)

            # 2) Delta: Trial balance total assets - FS total assets
            if total_assets_row is not None:
                assets_cell = ws_bs.cell(total_assets_row, b["year_startcol"] + y_idx)
                delta_cell  = ws_bs.cell(CHECK_DELTA_ROW, b["year_startcol"] + y_idx)
                delta_cell.value = f"={assets_cell.coordinate}-{c_fs.coordinate}"
                setup_value_cell(delta_cell, red=False)

                # Conditional formatting: red if != 0
                ws_bs.conditional_formatting.add(
                    delta_cell.coordinate,
                    CellIsRule(
                        operator="notEqual",
                        formula=["0"],
                        font=DIFF_FONT,
                    )
                )

            # 3) Balance check: A = E + L  (Assets + Equity & Liabilities = 0)
            el_cell  = ws_bs.cell(total_el_row, b["year_startcol"] + y_idx)
            bal_cell = ws_bs.cell(CHECK_BAL_ROW, b["year_startcol"] + y_idx)
            bal_cell.value = f"={assets_cell.coordinate}+{el_cell.coordinate}"
            setup_value_cell(bal_cell, red=True)

# =============================================
# BASIC FORMATTING (widths, heights, fills, header fill, header bottom border, hide A..I)
# =============================================
ws_bs.column_dimensions[col_letter(POS_COL)].width = 32
for b in blocks:
    if b.get("has_plpos"):
        ws_bs.column_dimensions[col_letter(b["poscol"])].width = REPEAT_POS_WIDTH
    for y_idx in range(len(YEARS)):
        ws_bs.column_dimensions[col_letter(b["year_startcol"] + y_idx)].width = VALUE_COL_WIDTH
    ws_bs.column_dimensions[col_letter(b["spacer_col"])].width = SPACER_WIDTH

FILL_PADDING_COLS = 40

FILL_END_ROW = LAST_TABLE_ROW + 100
FILL_END_COL = LAST_USED_COL + FILL_PADDING_COLS

for rr in range(1, FILL_END_ROW + 1):
    for cc in range(1, POS_COL):
        ws_bs.cell(rr, cc).fill = FILL_TECH

for rr in range(1, FILL_END_ROW + 1):
    for cc in range(POS_COL, FILL_END_COL + 1):
        ws_bs.cell(rr, cc).fill = FILL_WHITE

for rr in range(DATA_START_ROW, LAST_TABLE_ROW + 1):
    for cc in range(BS_MAP_START_COL, BS_MAP_END_COL + 1):
        ws_bs.cell(rr, cc).font = FONT_MAPPING

spacer_cols = {b["spacer_col"] for b in blocks}

apply_recon_portfolio_layout(
    ws_bs,
    blocks=blocks,
    pos_col=POS_COL,
    header_row=HEADER_ROW,
    block_title_row=BLOCK_TITLE_ROW,
    entity_code_row=ENTITY_CODE_ROW,
    last_used_col=LAST_USED_COL,
    spacer_cols=spacer_cols,
    collapsed_poscol_keys=(DIFF_KEY, FS_KEY),
)

ws_bs.row_dimensions[HEADER_ROW].height = ROW_HEIGHT
ws_bs.row_dimensions[BLOCK_TITLE_ROW].height = ROW_HEIGHT
for rr in range(1, FILL_END_ROW + 1):
    if rr == PROJECT_TITLE_ROW:
        continue
    ws_bs.row_dimensions[rr].height = ROW_HEIGHT

bs_year_cf_cols = []
for b in blocks:
    for y_idx in range(len(YEARS)):
        bs_year_cf_cols.append(b["year_startcol"] + y_idx)

apply_zero_row_conditional_formatting(
    ws_bs,
    first_row=DATA_START_ROW,
    last_row=LAST_TABLE_ROW,
    year_col_indices=bs_year_cf_cols,
    style_start_col=POS_COL,
    style_end_col=LAST_USED_COL,
    exclude_cols=spacer_cols,
)

# =============================================
# SAVE
# =============================================
wb.save(TARGET_FILE)
print(f"Saved: {TARGET_FILE}")
