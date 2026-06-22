import pandas as pd
import sys
from pathlib import Path
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.styles import Border, Side
from openpyxl.formatting.rule import CellIsRule

PROJECT_ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from report_row_layout import build_bs_row_structure, l2_l3_order_from_mapping  # noqa: E402

L4_SORT_BASIS = "latest_fy"

# ==================================================
# CONFIG (BS)
# ==================================================
PROJECT_NAME = "Apollo"
GROUP_NAME   = "WoSH Group"

YEARS = ["FY23A", "FY24A", "FY25A"]
UNIT_LABEL = "kEUR"

REC_PERIOD_FROM = YEARS[0].replace("FY", "Jul")
REC_PERIOD_TO   = YEARS[-1].replace("FY", "Jul")

ENTITY_SORT_ORDER = {
    "Faller": 2,
    "Sonoro Audio": 1,
    "WoSH": 3,
}

ENTITY_RENAME_MAP = {
    "Sonoro Audio": "sonoro",
    "WoSH": "WosH",
    "Faller": "faller",
}

IC_MASTER_ENTITY = "Consolidation"   # entity name in Master_BS for IC bucket
IC_DISPLAY_NAME  = "IC eliminations" # display name in output

DIFF_KEY = "Difference"
FS_KEY   = "Financial statements"

# ==================================================
# BS TOTALS CONFIG (components refer to L2 labels)
# ==================================================
BS_TOTALS_CONFIG = [
    {"label": "Total assets", "components": ["Fixed assets", "Current assets"], "insert_after": "Current assets"},
    {"label": "Total equity & liabilities", "components": ["Equity", "Liabilities"], "insert_after": "Liabilities"},
]


# ==================================================
# BS CHECK VALUES (Financial statements) - per year
# ==================================================
SHOW_BS_CHECKS = True

# Values in kEUR, keyed by entity and year.
# Keys must match block keys (normalized entities) and optionally "Consolidation"/"Aggregated".
BS_FS_CHECK_TOTAL_ASSETS = {
    # examples - fill with your real FS total assets values
    "sonoro": {"FY23A": 13710.14165, "FY24A": 16134.22175, "FY25A": 15910.91936},
    "faller": {"FY23A": 26.20073, "FY24A": 289.6485, "FY25A": 340.86132},
    "WosH": {"FY23A": 4099.94647, "FY24A": 4789.3806, "FY25A": 4512.42626},
    "Consolidation": {"FY23A": 14610.71139, "FY24A": 15795.0222, "FY25A": 15256.44372},
    # "Aggregated": {"FY23A": 0, "FY24A": 0, "FY25A": 0},  # optional
}

# ==================================================
# PATHS (BS)
# ==================================================
SOURCE_FILE = r"C:\Users\bhoffart\Grant Thornton Germany\Data Operations - Dokumente\Projekt Mathis\Dateien_Ben\Apollo - Databook_redacted.xlsb"

MAPPING_FILE_BS = r"C:\Users\bhoffart\Grant Thornton Germany\Data Operations - Dokumente\Projekt Mathis\Dateien_Ben\Python Scripts\Reconciliation_BS_Mapping.xlsx"
TARGET_FILE     = r"C:\Users\bhoffart\Grant Thornton Germany\Data Operations - Dokumente\Projekt Mathis\Dateien_Ben\Apollo - Databook_BS_recon.xlsx"

REPORT_SHEET_BS     = "BS_Reconciliation"
MASTER_SHEET_BS_OUT = "Master_BS"
SOURCE_SHEET_BS     = "BS_Data"  # sheet name in SOURCE_FILE containing the BS data

# ==================================================
# LAYOUT (same general geometry as PL)
# ==================================================
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
BS_MAP_START_COL = MAP_START_COL          # E
BS_MAP_END_COL   = MAP_START_COL + 4     # I
BS_L2_COL = MAP_START_COL + 2            # G
BS_L3_COL = MAP_START_COL + 3            # H
BS_L4_COL = MAP_START_COL + 4            # I

SPACER_WIDTH     = 1.14
VALUE_COL_WIDTH  = 7.86
ROW_HEIGHT       = 12
REPEAT_POS_WIDTH = 32

# ==================================================
# STYLES
# ==================================================
FONT_NAME = "GT Walsheim LC Light"
FONT_SIZE = 8

COLOR_GREY   = "F4F3F2"
COLOR_WHITE  = "FFFFFF"
COLOR_RED    = "FF0000"
COLOR_BLACK  = "000000"
COLOR_VIOLET = "4F2D7F"

FONT_BASE        = Font(name=FONT_NAME, size=FONT_SIZE, color=COLOR_BLACK)
FONT_BASE_BOLD   = Font(name=FONT_NAME, size=FONT_SIZE, color=COLOR_BLACK, bold=True)
FONT_MAPPING_RED = Font(name=FONT_NAME, size=FONT_SIZE, color=COLOR_RED)

FILL_GREY   = PatternFill("solid", fgColor=COLOR_GREY)
FILL_WHITE  = PatternFill("solid", fgColor=COLOR_WHITE)
FILL_HEADER = PatternFill("solid", fgColor="F2F2F2")
FILL_YELLOW = PatternFill("solid", fgColor="FFFF00")

ALIGN_LEFT   = Alignment(horizontal="left", vertical="center")
ALIGN_CENTER = Alignment(horizontal="center", vertical="center")
ALIGN_RIGHT  = Alignment(horizontal="right", vertical="center")

THIN_SIDE     = Side(style="thin")
TOP_BORDER    = Border(top=THIN_SIDE)
BOTTOM_BORDER = Border(bottom=THIN_SIDE)

NUM_FMT_INT = "#,##0;(#,##0);-"

# ==================================================
# HELPERS
# ==================================================
def col_letter(idx: int) -> str:
    return get_column_letter(idx)

def is_blank_entity(x):
    if x is None or pd.isna(x):
        return True
    s = str(x).strip()
    return (s == "") or (s.lower() in {"nan", "none", "null"})

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

# ==================================================
# LOAD BS MAPPING (L2/L3 hierarchy order; L4 ignored)
# ==================================================
bs_map_df = pd.read_excel(MAPPING_FILE_BS, sheet_name=0, engine="openpyxl")
if not {"L2", "L3"}.issubset(bs_map_df.columns):
    raise ValueError("Sortierung_BS_Datenbank muss Spalten 'L2' und 'L3' enthalten.")

bs_map_df = bs_map_df.copy()
bs_map_df["L2"] = bs_map_df["L2"].astype(str)
bs_map_df["L3"] = bs_map_df["L3"].astype(str)
bs_l2_l3_order = l2_l3_order_from_mapping(bs_map_df)

# ==================================================
# LOAD MASTER_BS + NORMALIZE ENTITY
# ==================================================
df_bs = pd.read_excel(SOURCE_FILE, sheet_name=SOURCE_SHEET_BS, engine="pyxlsb", header=1)
source_col_bs = detect_source_col(df_bs)

bs_row_structure = build_bs_row_structure(
    df_bs,
    bs_l2_l3_order,
    {"l4_sort_basis": L4_SORT_BASIS},
    source_col=source_col_bs or "L5",
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
missing_bs = required_bs - set(df_bs.columns)
if missing_bs:
    raise ValueError(f"Master_BS fehlt Spalten: {missing_bs}")

df_bs = df_bs.copy()

def normalize_entity_keep_ic(x):
    if x is None or pd.isna(x):
        return x
    s = str(x).strip()
    if s == IC_MASTER_ENTITY:
        return s
    return ENTITY_RENAME_MAP.get(s, s)

df_bs["Entity"] = df_bs["Entity"].apply(normalize_entity_keep_ic)
ENTITY_SORT_ORDER = {ENTITY_RENAME_MAP.get(k, k): v for k, v in ENTITY_SORT_ORDER.items()}


# ==================================================
# ENTITY LIST (robust - no order assumptions)
# ==================================================
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
    return ENTITY_SORT_ORDER.get(e, 9999)

individual_entities = sorted(individual_entities, key=entity_sort_key)


# ==================================================
# WORKBOOK + SHEETS
# ==================================================
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
MASTER_END = len(df_bs) + 1

def master_range_bs(colname):
    L = get_column_letter(df_bs.columns.get_loc(colname) + 1)
    return f"{MASTER_SHEET_BS_OUT}!${L}${MASTER_START}:${L}${MASTER_END}"

src_rng = master_range_bs(source_col_bs) if source_col_bs is not None else None
ent_rng = master_range_bs("Entity")
l2_rng  = master_range_bs("L2")
l3_rng  = master_range_bs("L3")
l4_rng  = master_range_bs("L4")

# ==================================================
# TITLES
# ==================================================
pt = ws_bs.cell(PROJECT_TITLE_ROW, POS_COL, f"Project {PROJECT_NAME}")
pt.font = Font(name=FONT_NAME, size=24, color=COLOR_VIOLET, bold=False)
pt.alignment = ALIGN_LEFT
ws_bs.row_dimensions[PROJECT_TITLE_ROW].height = 36

st = ws_bs.cell(SUBTITLE_ROW, POS_COL, f"{GROUP_NAME} Reconciliation - Balance sheet")
st.font = Font(name=FONT_NAME, size=12, color=COLOR_VIOLET, bold=False)
st.alignment = ALIGN_LEFT

# ==================================================
# MAIN RECONCILIATION TITLE (row 6, column J)
# ==================================================
rec_title_text = (
    f"{GROUP_NAME} | Reconciliation (Trial Balances) "
    f"{REC_PERIOD_FROM} - {REC_PERIOD_TO}"
)

rec_title = ws_bs.cell(6, POS_COL, rec_title_text)
rec_title.font = Font(name=FONT_NAME, size=FONT_SIZE, color=COLOR_VIOLET, bold=True)
rec_title.alignment = ALIGN_LEFT

# ==================================================
# HEADERS (E..I mapping, J label)
# ==================================================
ws_bs.cell(HEADER_ROW, BS_MAP_START_COL + 0, "Reported").font = FONT_BASE_BOLD
ws_bs.cell(HEADER_ROW, BS_MAP_START_COL + 1, "").font = FONT_BASE_BOLD
ws_bs.cell(HEADER_ROW, BS_L2_COL, "L2").font = FONT_BASE_BOLD
ws_bs.cell(HEADER_ROW, BS_L3_COL, "L3").font = FONT_BASE_BOLD
ws_bs.cell(HEADER_ROW, BS_L4_COL, "L4").font = FONT_BASE_BOLD

for c in range(BS_MAP_START_COL, BS_MAP_END_COL + 1):
    ws_bs.cell(HEADER_ROW, c).alignment = ALIGN_LEFT

ph = ws_bs.cell(HEADER_ROW, POS_COL, UNIT_LABEL)
ph.font = FONT_BASE_BOLD
ph.alignment = ALIGN_LEFT

# ==================================================
# BLOCK PLAN (Entities / Aggregated / IC / Consolidation / Difference / FS)
# ==================================================
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
current_col = FIRST_ENTITY_COL

for b in blocks:
    b["startcol"] = current_col
    block_startcol[b["key"]] = current_col

    extra = 1 if b.get("has_plpos") else 0
    b["poscol"] = current_col if extra == 1 else None
    b["year_startcol"] = current_col + extra
    b["year_endcol"] = b["year_startcol"] + len(YEARS) - 1
    b["spacer_col"] = b["year_endcol"] + 1

    current_col = b["spacer_col"] + 1

LAST_USED_COL = current_col - 1

# block title + entity code row + year headers
for b in blocks:
    if b.get("has_plpos"):
        ws_bs.merge_cells(start_row=BLOCK_TITLE_ROW, start_column=b["year_startcol"],
                          end_row=BLOCK_TITLE_ROW, end_column=b["year_endcol"])
        t = ws_bs.cell(BLOCK_TITLE_ROW, b["year_startcol"], b["title"])
        t.font = FONT_BASE_BOLD
        t.alignment = ALIGN_CENTER
        ws_bs.cell(BLOCK_TITLE_ROW, b["poscol"]).value = None
    else:
        ws_bs.merge_cells(start_row=BLOCK_TITLE_ROW, start_column=b["startcol"],
                          end_row=BLOCK_TITLE_ROW, end_column=b["year_endcol"])
        t = ws_bs.cell(BLOCK_TITLE_ROW, b["startcol"], b["title"])
        t.font = FONT_BASE_BOLD
        t.alignment = ALIGN_CENTER

    if b.get("has_plpos"):
        ws_bs.merge_cells(start_row=ENTITY_CODE_ROW, start_column=b["year_startcol"],
                          end_row=ENTITY_CODE_ROW, end_column=b["year_endcol"])
        ec = ws_bs.cell(ENTITY_CODE_ROW, b["year_startcol"], b["code"])
    else:
        ws_bs.merge_cells(start_row=ENTITY_CODE_ROW, start_column=b["startcol"],
                          end_row=ENTITY_CODE_ROW, end_column=b["year_endcol"])
        ec = ws_bs.cell(ENTITY_CODE_ROW, b["startcol"], b["code"])

    ec.font = FONT_BASE
    ec.alignment = ALIGN_CENTER

    if b.get("has_plpos"):
        hpos = ws_bs.cell(HEADER_ROW, b["poscol"], UNIT_LABEL)
        hpos.font = FONT_BASE_BOLD
        hpos.alignment = ALIGN_LEFT

    for y_idx, year in enumerate(YEARS):
        h = ws_bs.cell(HEADER_ROW, b["year_startcol"] + y_idx, year)
        h.font = FONT_BASE_BOLD
        h.alignment = ALIGN_CENTER

    ws_bs.column_dimensions[col_letter(b["spacer_col"])].width = SPACER_WIDTH

ws_bs.row_dimensions[ENTITY_CODE_ROW].outlineLevel = 2
ws_bs.row_dimensions[ENTITY_CODE_ROW].hidden = True
ws_bs.sheet_view.showOutlineSymbols = True

# counts for indentation logic
l3_count = defaultdict(int)
for rr in bs_row_structure:
    if rr["type"] in {"detail", "detail_single"}:
        l3_count[(rr["L2"], rr["L3"])] += 1

# ==================================================
# WRITE ROWS (mapping + labels + repeat positions in Difference/FS)
# ==================================================
for i, r in enumerate(bs_row_structure):
    r["_idx"] = i
    excel_row = DATA_START_ROW + i
    r["_excel_row"] = excel_row

    # mapping columns
    ws_bs.cell(excel_row, BS_MAP_START_COL + 0, "Reported").font = FONT_MAPPING_RED
    ws_bs.cell(excel_row, BS_MAP_START_COL + 1, "").font = FONT_MAPPING_RED
    ws_bs.cell(excel_row, BS_L2_COL, r["L2"]).font = FONT_MAPPING_RED
    ws_bs.cell(excel_row, BS_L3_COL, r["L3"]).font = FONT_MAPPING_RED
    ws_bs.cell(excel_row, BS_L4_COL, r["L4"]).font = FONT_MAPPING_RED

    for c in range(BS_MAP_START_COL, BS_MAP_END_COL + 1):
        ws_bs.cell(excel_row, c).alignment = ALIGN_LEFT

    # main label column J
    pc = ws_bs.cell(excel_row, POS_COL, r["label"])

    if (
        r["type"] == "detail"
        and r["L3"] != r["L4"]
        and l3_count[(r["L2"], r["L3"])] > 1
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

        pcell = ws_bs.cell(excel_row, b["poscol"])
        pcell.value = f"={col_letter(POS_COL)}{excel_row}"

        # alignment: mirror indentation rule (do NOT copy StyleProxy)
        if (
            r["type"] == "detail"
            and r["L3"] != r["L4"]
            and l3_count[(r["L2"], r["L3"])] > 1
        ):
            pcell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        else:
            pcell.alignment = ALIGN_LEFT

        # font: same rule as main label column
        if r["type"] in {"subtotal_l2", "total"}:
            pcell.font = FONT_BASE_BOLD
        else:
            pcell.font = FONT_BASE

    # outline
    if r["type"] == "detail":
        ws_bs.row_dimensions[excel_row].outlineLevel = 1
        ws_bs.row_dimensions[excel_row].hidden = False
    else:
        ws_bs.row_dimensions[excel_row].outlineLevel = 0
        ws_bs.row_dimensions[excel_row].hidden = False

LAST_TABLE_ROW = DATA_START_ROW + len(bs_row_structure) - 1

# ==================================================
# VALUES (SUMIFS for detail/detail_single, SUM for subtotals/totals, IC negative sign)
# ==================================================
bs_rep_col = col_letter(BS_MAP_START_COL + 0)  # E
bs_l2_col  = col_letter(BS_L2_COL)              # G
bs_l3_col  = col_letter(BS_L3_COL)              # H
bs_l4_col  = col_letter(BS_L4_COL)              # I

def block_lookup(key):
    return next(bb for bb in blocks if bb["key"] == key)

for b in blocks:
    if b["kind"] in {"entity", "ic"}:
        entity_ref = f"${col_letter(b['startcol'])}{ENTITY_CODE_ROW}"
    else:
        entity_ref = None

    sign = ic_sign(b)

    for r in bs_row_structure:
        excel_row = r["_excel_row"]

        for y_idx, year in enumerate(YEARS):
            cell = ws_bs.cell(excel_row, b["year_startcol"] + y_idx)
            cell.alignment = ALIGN_RIGHT
            cell.number_format = NUM_FMT_INT

            # values bold: L2 + totals only
            if r["type"] in {"subtotal_l2", "total"}:
                cell.font = FONT_BASE_BOLD
            else:
                cell.font = FONT_BASE

            if b["kind"] in {"entity", "ic"}:

                if r["type"] in {"detail", "detail_single"}:
                    sum_rng  = master_range_bs(year)
                    l2_crit  = f"${bs_l2_col}${excel_row}"
                    l3_crit  = f"${bs_l3_col}${excel_row}"
                    l4_crit  = f"${bs_l4_col}${excel_row}"
                    rep_crit = f"${bs_rep_col}${excel_row}"

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
                    while k >= 0 and bs_row_structure[k]["type"] == "detail" and bs_row_structure[k]["L3"] == r["label"] and bs_row_structure[k]["L2"] == r["L2"]:
                        k -= 1
                    detail_start_idx = k + 1
                    detail_end_idx = idx - 1

                    coll = col_letter(b["year_startcol"] + y_idx)
                    if detail_end_idx >= detail_start_idx:
                        start_row = DATA_START_ROW + detail_start_idx
                        end_row   = DATA_START_ROW + detail_end_idx
                        cell.value = f"=SUM({coll}{start_row}:{coll}{end_row})"
                    else:
                        cell.value = 0

                elif r["type"] == "subtotal_l2":
                    coll = col_letter(b["year_startcol"] + y_idx)
                    refs = []
                    for rr in bs_row_structure:
                        if rr["L2"] == r["L2"] and rr["type"] in {"subtotal_l3", "detail_single"}:
                            refs.append(f"{coll}{rr['_excel_row']}")
                    cell.value = f"=SUM({','.join(refs)})" if refs else 0

                elif r["type"] == "total":
                    coll = col_letter(b["year_startcol"] + y_idx)
                    refs = []
                    for comp in r.get("components", []):
                        for rr in bs_row_structure:
                            if rr["type"] == "subtotal_l2" and rr["label"] == comp:
                                refs.append(f"{coll}{rr['_excel_row']}")
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
                agg_ref = f"{col_letter(agg_block['year_startcol'] + y_idx)}{excel_row}"
                ic_ref  = f"{col_letter(ic_block['year_startcol'] + y_idx)}{excel_row}"
                cell.value = f"={agg_ref}+{ic_ref}"

            elif b["kind"] == "fs":
                cell.value = None

            elif b["kind"] == "difference":
                fs_block  = block_lookup(FS_KEY)
                con_block = block_lookup("Consolidation")
                fs_ref  = f"{col_letter(fs_block['year_startcol'] + y_idx)}{excel_row}"
                con_ref = f"{col_letter(con_block['year_startcol'] + y_idx)}{excel_row}"
                cell.value = f"={fs_ref}-{con_ref}"

# ==================================================
# TOP BORDER for Totals (label + positions + all year cells)
# ==================================================
for rr in bs_row_structure:
    if rr["type"] != "total":
        continue

    tr = rr["_excel_row"]

    # main BS position (column J)
    ws_bs.cell(tr, POS_COL).border = TOP_BORDER

    for bb in blocks:
        # BS position column in Difference / FS
        if bb.get("has_plpos") and bb.get("poscol") is not None:
            ws_bs.cell(tr, bb["poscol"]).border = TOP_BORDER

        # all year value columns
        for y_idx in range(len(YEARS)):
            ws_bs.cell(tr, bb["year_startcol"] + y_idx).border = TOP_BORDER

# ==================================================
# CHECKS (start 4 rows below table)
# ==================================================
if SHOW_BS_CHECKS:

    CHECK_FS_ROW    = LAST_TABLE_ROW + 4  # row 1 of check section
    CHECK_DELTA_ROW = LAST_TABLE_ROW + 5  # row 2 (delta to FS)
    CHECK_BAL_ROW   = LAST_TABLE_ROW + 6  # row 3 (assets vs E&L) always red

    # helper: find the excel rows of the total lines
    total_row_map = {r["label"]: r["_excel_row"] for r in bs_row_structure if r["type"] == "total"}

    TOTAL_ASSETS_LABEL = "Total assets"
    TOTAL_EL_LABEL     = "Total equity & liabilities"

    if TOTAL_ASSETS_LABEL not in total_row_map:
        raise RuntimeError("Total assets row not found in bs_row_structure (type='total').")
    if TOTAL_EL_LABEL not in total_row_map:
        raise RuntimeError("Total equity & liabilities row not found in bs_row_structure (type='total').")

    total_assets_row = total_row_map[TOTAL_ASSETS_LABEL]
    total_el_row     = total_row_map[TOTAL_EL_LABEL]

    # --- Row titles in column J ---
    ws_bs.cell(CHECK_FS_ROW, POS_COL, "Source - Financial statements").alignment = ALIGN_LEFT
    ws_bs.cell(CHECK_DELTA_ROW, POS_COL, "Check").alignment = ALIGN_LEFT
    ws_bs.cell(CHECK_BAL_ROW, POS_COL, "A = E + L").alignment = ALIGN_LEFT

    # default fonts for titles
    ws_bs.cell(CHECK_FS_ROW, POS_COL).font = FONT_BASE
    ws_bs.cell(CHECK_DELTA_ROW, POS_COL).font = FONT_BASE
    ws_bs.cell(CHECK_BAL_ROW, POS_COL).font = Font(name=FONT_NAME, size=FONT_SIZE, color=COLOR_RED)

    # number format & alignment for value cells
    def setup_value_cell(c, red=False):
        c.number_format = NUM_FMT_INT
        c.alignment = ALIGN_RIGHT
        c.font = Font(name=FONT_NAME, size=FONT_SIZE, color=COLOR_RED) if red else FONT_BASE

    # --- Write FS check values (row 1) + delta formulas (row 2) ---
    # we fill checks for blocks where a config entry exists (entities + consolidation; aggregated optional)
    for b in blocks:
        # checks apply to the same visible table columns (year columns)
        key = b["key"]
        year_map = BS_FS_CHECK_TOTAL_ASSETS.get(key)
        if year_map is None:
            continue

        for y_idx, year in enumerate(YEARS):
            # 1) FS input value
            val = year_map.get(year, 0)
            c_fs = ws_bs.cell(CHECK_FS_ROW, b["year_startcol"] + y_idx, val)
            setup_value_cell(c_fs, red=False)

            # 2) Delta = Total assets (table) - FS check
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
                    font=Font(name=FONT_NAME, size=FONT_SIZE, color=COLOR_RED)
                )
            )

            # 3) Balance check: A = E + L  (Assets + Equity & Liabilities = 0)
            el_cell  = ws_bs.cell(total_el_row, b["year_startcol"] + y_idx)
            bal_cell = ws_bs.cell(CHECK_BAL_ROW, b["year_startcol"] + y_idx)
            bal_cell.value = f"={assets_cell.coordinate}+{el_cell.coordinate}"
            setup_value_cell(bal_cell, red=True)

# ==================================================
# BASIC FORMATTING (widths, heights, fills, header fill, header bottom border, hide A..I)
# ==================================================
ws_bs.column_dimensions[col_letter(POS_COL)].width = 32
for b in blocks:
    if b.get("has_plpos"):
        ws_bs.column_dimensions[col_letter(b["poscol"])].width = REPEAT_POS_WIDTH
    for y_idx in range(len(YEARS)):
        ws_bs.column_dimensions[col_letter(b["year_startcol"] + y_idx)].width = VALUE_COL_WIDTH
    ws_bs.column_dimensions[col_letter(b["spacer_col"])].width = SPACER_WIDTH

FILL_END_ROW = LAST_TABLE_ROW + 100
for rr in range(1, FILL_END_ROW + 1):
    if rr == PROJECT_TITLE_ROW:
        continue
    ws_bs.row_dimensions[rr].height = ROW_HEIGHT

FILL_END_COL = LAST_USED_COL + 40
for rr in range(1, FILL_END_ROW + 1):
    for cc in range(1, POS_COL):
        ws_bs.cell(rr, cc).fill = FILL_GREY
for rr in range(1, FILL_END_ROW + 1):
    for cc in range(POS_COL, FILL_END_COL + 1):
        ws_bs.cell(rr, cc).fill = FILL_WHITE

# header fill excluding spacer columns
spacer_cols = {b["spacer_col"] for b in blocks}
for rrr in (BLOCK_TITLE_ROW, HEADER_ROW):
    for cc in range(1, LAST_USED_COL + 1):
        if cc in spacer_cols:
            continue
        ws_bs.cell(rrr, cc).fill = FILL_HEADER

# NEW: bottom border for header row 8 (exclude spacer columns)
for cc in range(1, LAST_USED_COL + 1):
    if cc in spacer_cols:
        continue
    ws_bs.cell(HEADER_ROW, cc).border = BOTTOM_BORDER

# hide A..I like PL
for cc in range(1, 10):  # A..I
    col = col_letter(cc)
    ws_bs.column_dimensions[col].outlineLevel = 2
    ws_bs.column_dimensions[col].hidden = True
ws_bs.column_dimensions[col_letter(POS_COL)].outlineLevel = 0
ws_bs.column_dimensions[col_letter(POS_COL)].hidden = False


# ==================================================
# YELLOW FILL for "Source - Financial statements" row
# (from BS position column to end of Consolidation table, EXCLUDE spacer columns)
# ==================================================

con_block = next(b for b in blocks if b["kind"] == "consolidation")
CONSOLIDATION_LAST_COL = con_block["year_endcol"]

for cc in range(POS_COL, CONSOLIDATION_LAST_COL + 1):
    if cc in spacer_cols:
        # keep spacer columns white
        ws_bs.cell(CHECK_FS_ROW, cc).fill = FILL_WHITE
        continue
    ws_bs.cell(CHECK_FS_ROW, cc).fill = FILL_YELLOW


# ==================================================
# OUTLINE / COLLAPSE: Entity, IC eliminations, Aggregated
# ==================================================
for b in blocks:
    c_first = b["startcol"]
    c_last  = b["spacer_col"]

    if b["kind"] in {"entity", "ic", "aggregated"}:
        for cc in range(c_first, c_last + 1):
            col = col_letter(cc)
            ws_bs.column_dimensions[col].outlineLevel = 1
            ws_bs.column_dimensions[col].hidden = False
        # collapse the block
        ws_bs.column_dimensions[col_letter(c_last)].collapsed = True

    elif b["kind"] in {"consolidation", "difference", "fs"}:
        for cc in range(c_first, c_last + 1):
            col = col_letter(cc)
            ws_bs.column_dimensions[col].outlineLevel = 0
            ws_bs.column_dimensions[col].hidden = False

# ==================================================
# OUTLINE / COLLAPSE: Check rows (same level as ENTITY_CODE_ROW)
# ==================================================
for rr in (CHECK_FS_ROW, CHECK_DELTA_ROW, CHECK_BAL_ROW):
    ws_bs.row_dimensions[rr].outlineLevel = 2
    ws_bs.row_dimensions[rr].hidden = True

# ==================================================
# COLLAPSE BS POSITION COLUMNS for Difference & FS
# ==================================================
for key in (DIFF_KEY, FS_KEY):
    b = next(bb for bb in blocks if bb["key"] == key)

    poscol = b.get("poscol")
    if poscol is None:
        continue

    coll = col_letter(poscol)
    ws_bs.column_dimensions[coll].outlineLevel = 1
    ws_bs.column_dimensions[coll].hidden = True
    ws_bs.column_dimensions[coll].collapsed = True
    # ensure value columns stay visible
    for cc in range(b["year_startcol"], b["year_endcol"] + 1):
        cl = col_letter(cc)
        ws_bs.column_dimensions[cl].outlineLevel = 0
        ws_bs.column_dimensions[cl].hidden = False

# ==================================================
# SAVE
# ==================================================
wb.save(TARGET_FILE)
print(f"Fertig. Datei gespeichert unter: {TARGET_FILE}")
if source_col_bs is None:
    print("HINWEIS: Keine Quelle/Reported-Spalte im Master_BS gefunden; SUMIFS filtert daher nicht auf 'Reported'.")
else:
    print(f"Quelle-Spalte im Master_BS erkannt: '{source_col_bs}' (SUMIFS filtert auf 'Reported').")
