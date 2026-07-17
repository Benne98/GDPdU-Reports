"""Render visible Excel sheet ranges to PNG screenshots (Pillow)."""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from openpyxl.utils import get_column_letter
from PIL import Image, ImageDraw, ImageFont

# Fixed rendering scale: 2x ≈ Calibri 9pt look; PDF placer must not upscale beyond this.
RENDER_SCALE = 2
BODY_FONT_PX = 9 * RENDER_SCALE
TITLE_FONT_PX = 10 * RENDER_SCALE

HEADER_FILL_FALLBACK = (232, 238, 244)
BORDER_COLOR = (180, 190, 200)
NAVY = (15, 45, 90)

_PERIOD_RE = re.compile(r"^(FY|YTD|LTM)\d", re.I)
_SNAPSHOT_RE = re.compile(r"^[A-Za-z]{3}\d{2}A$", re.I)
_DELTA_RE = re.compile(r"^(Δ|delta|\u0394)", re.I)


def _is_row_hidden(ws, row: int) -> bool:
    dim = ws.row_dimensions.get(row)
    return bool(dim and dim.hidden)


def _is_col_hidden(ws, col: int) -> bool:
    letter = get_column_letter(col)
    dim = ws.column_dimensions.get(letter)
    return bool(dim and dim.hidden)


def _outline_level(ws, row: int) -> int:
    dim = ws.row_dimensions.get(row)
    try:
        return int(getattr(dim, "outlineLevel", 0) or 0) if dim else 0
    except Exception:
        return 0


def _argb_to_rgb(color) -> Optional[tuple[int, int, int]]:
    if color is None:
        return None
    rgb = getattr(color, "rgb", None)
    if not rgb or not isinstance(rgb, str):
        return None
    c = rgb.upper()
    if len(c) == 8:
        c = c[2:]
    if len(c) != 6:
        return None
    try:
        return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    except Exception:
        return None


_THEME_COLORS = {
    # Standard Office theme (theme index: base ARGB without tint)
    0: (255, 255, 255),  # Background 1 (white)
    1: (0, 0, 0),        # Text 1 (black)
    2: (238, 236, 225),  # Background 2
    3: (31, 73, 125),    # Text 2 (dark blue)
    4: (79, 129, 189),   # Accent 1 (blue)
    5: (192, 80, 77),    # Accent 2 (red)
    6: (155, 187, 89),   # Accent 3 (green)
    7: (128, 100, 162),  # Accent 4 (purple)
    8: (75, 172, 198),   # Accent 5 (teal)
    9: (247, 150, 70),   # Accent 6 (orange)
}


def _apply_tint(base: tuple[int, int, int], tint: float) -> tuple[int, int, int]:
    if tint >= 0:
        return tuple(int(b + (255 - b) * tint) for b in base)  # type: ignore[return-value]
    return tuple(int(b * (1 + tint)) for b in base)  # type: ignore[return-value]


def _theme_tint_rgb(color) -> Optional[tuple[int, int, int]]:
    """Best-effort theme/indexed color → approximate RGB."""
    if color is None:
        return None
    theme = getattr(color, "theme", None)
    indexed = getattr(color, "indexed", None)
    if theme is not None:
        base = _THEME_COLORS.get(int(theme), HEADER_FILL_FALLBACK)
        tint = float(getattr(color, "tint", 0) or 0)
        if tint != 0.0:
            return _apply_tint(base, tint)
        return base
    if indexed is not None:
        return HEADER_FILL_FALLBACK
    return None


def _fill_info(cell) -> tuple[tuple[int, int, int], Optional[str]]:
    """Return (rgb, pattern_type). pattern_type None/solid = flat fill."""
    fill = cell.fill
    if not fill:
        return (255, 255, 255), None
    fill_type = getattr(fill, "fill_type", None) or getattr(fill, "patternType", None)
    if fill_type in (None, "none"):
        return (255, 255, 255), None
    fg = getattr(fill, "fgColor", None)
    rgb = _argb_to_rgb(fg) or _theme_tint_rgb(fg)
    if rgb is None and fill_type == "solid":
        return (255, 255, 255), None
    if rgb is None:
        rgb = HEADER_FILL_FALLBACK
    if fill_type and fill_type not in ("solid", "none"):
        return rgb, str(fill_type)
    return rgb, None


def _fill_rgb(cell) -> tuple[int, int, int]:
    return _fill_info(cell)[0]


def _font_rgb(cell) -> tuple[int, int, int]:
    font = cell.font
    if font:
        rgb = _argb_to_rgb(getattr(font, "color", None))
        if rgb:
            return rgb
    return (17, 24, 39)


def _eu_thousands(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _eu_decimal(v: float, decimals: int = 1) -> str:
    s = f"{abs(v):,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return s


def format_cell_value(value: Any, number_format: str = "General") -> str:
    """Format like Finssentials screenshots: 31.337 / (2.819)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, (int, float)):
        fmt = str(number_format or "General")
        fl = fmt.lower()
        try:
            v = float(value)
        except Exception:
            return str(value)
        if "%" in fl:
            pct = v * 100.0 if abs(v) <= 2 else v
            return f"{_eu_decimal(pct, 1)}%"
        if ";(" in fmt.replace(" ", "") or "(#,##0)" in fmt or "#,##0" in fmt:
            if "0.0" in fmt and "%" not in fl:
                if abs(v) < 1e-12:
                    return "-"
                s = _eu_decimal(v, 1)
                return f"({s})" if v < 0 else s
            iv = int(round(v))
            if abs(v) < 0.5:
                return "-"
            return f"({_eu_thousands(abs(iv))})" if iv < 0 else _eu_thousands(iv)
        if abs(v - round(v)) < 1e-9:
            iv = int(round(v))
            return f"({_eu_thousands(abs(iv))})" if iv < 0 else _eu_thousands(iv)
        s = _eu_decimal(v, 1 if abs(v) >= 100 else 2)
        return f"({s})" if v < 0 else s
    text = str(value).strip()
    if text.startswith("="):
        return ""
    return text.replace("\n", " ")


def find_company_table_title_row(
    ws, visible_rows: list[int], visible_cols: list[int], header_row: int
) -> Optional[int]:
    """
    Row like 'PDF AG | PVM' / 'PDF AG | TOP' directly above the column header.
    Skips project name / Finssentials / date bands.
    """
    candidates = [r for r in visible_rows if r < header_row and header_row - r <= 4]
    fallback: Optional[int] = None
    for r in reversed(candidates):
        texts: list[str] = []
        for c in visible_cols:
            v = ws.cell(r, c).value
            if v in (None, "") or isinstance(v, (datetime, date)):
                continue
            texts.append(str(v).strip())
        if not texts:
            continue
        joined = " ".join(texts).lower()
        if any(t in joined for t in ("finssentials", "project ", "fast track")):
            continue
        for txt in texts:
            if "|" in txt and len(txt) <= 120:
                return r
        if (
            fallback is None
            and
            len(texts) == 1
            and 3 <= len(texts[0]) <= 80
            and not texts[0].lower().startswith("fy")
            and texts[0].strip().lower() != "cagr"
            and not texts[0].strip().lower().startswith("days overdue")
        ):
            fallback = r
    return fallback


def _expand_outline_rows_for_pdf(ws) -> None:
    """Unhide rows collapsed by Excel outline so PDF shows full hierarchy."""
    max_r = min(int(ws.max_row or 1), 8000)
    for r in range(1, max_r + 1):
        dim = ws.row_dimensions.get(r)
        if not dim or not dim.hidden:
            continue
        if int(getattr(dim, "outlineLevel", 0) or 0) >= 1:
            dim.hidden = False


def _is_helper_meta_col(ws, col: int, header_row: int) -> bool:
    """Detect trailing metadata columns (__level, leaf, outline keys, …)."""
    for hr in range(max(1, header_row - 2), header_row + 1):
        v = ws.cell(hr, col).value
        if v in (None, ""):
            continue
        t = str(v).strip().lower()
        if t.startswith("__") or t in {"row_type", "ui_level", "leaf", "normal"}:
            return True
    # No header token in header band → likely metadata after real table cols
    if header_row:
        hdr = ws.cell(header_row, col).value
        if hdr in (None, ""):
            for r in range(header_row + 1, min(header_row + 6, int(ws.max_row or 1) + 1)):
                v = ws.cell(r, col).value
                if v in (None, ""):
                    continue
                t = str(v).strip()
                if t in {"leaf", "normal", "other_detail"}:
                    return True
                if isinstance(v, int) and 0 < v <= 6:
                    return True
                if "|" in t and not _PERIOD_RE.match(t):
                    return True
    return False


def _row_has_content(ws, row: int, cols: list[int]) -> bool:
    return any(ws.cell(row, c).value not in (None, "") for c in cols)


def apply_opos_detail_collapse_inplace(ws, *, keep_first_n: int = 2) -> None:
    """
    For existing OPOS detail sheets: collapse partner groups under bucket index >= keep_first_n.
    """
    name = str(getattr(ws, "title", "") or "").lower()
    if "aging detail" not in name:
        return

    max_r = min(int(ws.max_row or 1), 5000)
    groups: list[tuple[list[int], int]] = []
    pending: list[int] = []
    for r in range(1, max_r + 1):
        lvl = _outline_level(ws, r)
        if lvl >= 1:
            pending.append(r)
            continue
        if pending:
            groups.append((pending, r))
            pending = []
    if not groups:
        return

    ws.sheet_properties.outlinePr.summaryBelow = True
    for i, (partners, summary_row) in enumerate(groups):
        collapse = i >= keep_first_n
        for pr in partners:
            rd = ws.row_dimensions[pr]
            rd.outlineLevel = 1
            rd.hidden = collapse
            rd.collapsed = False
        rd_b = ws.row_dimensions[summary_row]
        rd_b.outlineLevel = 0
        rd_b.hidden = False
        rd_b.collapsed = collapse


def find_table_header_row(ws, visible_rows: list[int], visible_cols: list[int]) -> int:
    """First row that looks like a real column header (FY/YTD/kEUR/…)."""
    best_fallback = visible_rows[0] if visible_rows else 1
    for r in visible_rows:
        vals = []
        for c in visible_cols:
            v = ws.cell(r, c).value
            if v is None or v == "":
                continue
            vals.append(str(v).strip())
        if not vals:
            continue
        period_cells = sum(
            1
            for v in vals
            if _PERIOD_RE.match(v) or _SNAPSHOT_RE.match(v) or v.upper().startswith("Δ")
        )
        low = {v.lower() for v in vals}
        strong_tokens = {"keur", "account", "partner", "rank", "abc", "quantity", "price"}
        if period_cells >= 2 or (low & strong_tokens):
            return r
        if len(vals) >= 4 and all(len(v) <= 24 for v in vals):
            best_fallback = r
            continue
    for r in visible_rows:
        vals = [
            ws.cell(r, c).value
            for c in visible_cols
            if ws.cell(r, c).value not in (None, "")
        ]
        if len(vals) >= 3:
            return r
    return best_fallback


def _sheet_name_lower(ws) -> str:
    return str(getattr(ws, "title", "") or "").strip().lower()


def _detect_lead_is_proforma_cols(ws, max_c: int) -> Optional[tuple[int, int]]:
    """Return (start_col, end_col) for Pro forma block, or None."""
    title_row = 6
    proforma_start = None
    reported_start = None
    for cc in range(1, max_c + 1):
        v = ws.cell(title_row, cc).value
        if not isinstance(v, str):
            continue
        vv = v.lower()
        if reported_start is None and "reported income statement" in vv:
            reported_start = cc
        if proforma_start is None and "pro forma income statement" in vv:
            proforma_start = cc
    if proforma_start is None:
        # Fallback: second kEUR column
        keurs = []
        for cc in range(1, max_c + 1):
            for r in range(6, 12):
                if str(ws.cell(r, cc).value or "").strip().lower() == "keur":
                    keurs.append(cc)
                    break
        if len(keurs) >= 2:
            proforma_start = keurs[1]
        else:
            return None
    # End at last period/CAGR column of the pro forma block (before helper cols)
    end = proforma_start
    for cc in range(proforma_start, max_c + 1):
        for r in range(6, 12):
            v = ws.cell(r, cc).value
            if v in (None, ""):
                continue
            t = str(v).strip()
            if t.lower().startswith("__") or t.lower() in {"row_type", "ui_level"}:
                return proforma_start, end
            if _PERIOD_RE.match(t) or _SNAPSHOT_RE.match(t) or "cagr" in t.lower() or t.lower() == "keur" or "Δ" in t or "-" in t:
                end = cc
            break
    return proforma_start, end


def _detect_group_recon_from_aggregated(ws, max_c: int) -> Optional[tuple[int, int]]:
    """POS/kEUR label col + Aggregated block through right edge (IC/Consol/Diff/FS)."""
    try:
        from databook_excel_layout import find_recon_block_start_col
    except ImportError:
        return None
    agg = find_recon_block_start_col(ws, "Aggregated")
    if agg is None:
        return None
    # Shared label column (kEUR) left of all portfolios
    keur = _anchor_start_col(ws, max_c)
    start = min(keur, agg)
    # If kEUR is far left among entities, still take kEUR + jump to Aggregated:
    # return cols as continuous range from Aggregated only would drop labels —
    # so we keep kEUR and then Aggregated…end by filtering in visible_content_columns
    return start, max_c


def _filter_group_recon_cols(ws, cols: list[int]) -> list[int]:
    """Keep kEUR/label cols left of Aggregated, drop entity portfolio value cols."""
    try:
        from databook_excel_layout import find_recon_block_start_col
    except ImportError:
        return cols
    agg = find_recon_block_start_col(ws, "Aggregated")
    if agg is None:
        return cols
    kept: list[int] = []
    for c in cols:
        # Always keep early label/title cols (kEUR band) before first entity years
        text_hits = []
        for r in range(6, 10):
            v = ws.cell(r, c).value
            if v not in (None, ""):
                text_hits.append(str(v).strip())
        is_label = any(t.lower() == "keur" for t in text_hits) or (
            len(text_hits) == 1 and "|" in text_hits[0]
        )
        if c >= agg or is_label:
            kept.append(c)
    return kept or cols


def _detect_bs_bucket_latest_na_cols(ws, max_c: int) -> Optional[tuple[int, int]]:
    """Latest FY Stichtag block of the Net assets classification tables."""
    blocks: list[tuple[int, str]] = []
    for c in range(1, max_c + 1):
        for r in range(1, 15):
            v = ws.cell(r, c).value
            if isinstance(v, str) and "net assets classification" in v.lower():
                blocks.append((c, v.strip()))
                break
    if not blocks:
        return None

    fy_blocks = [
        (c, title)
        for c, title in blocks
        if not any(month in title.lower() for month in ("may", "mai", "jun", "jul", "aug"))
    ]
    start, _title = (fy_blocks or blocks)[-1]
    next_starts = [c for c, _ in blocks if c > start]
    end = (min(next_starts) - 2) if next_starts else min(max_c, start + 6)
    return start, end


_FY_PERIOD_RE = re.compile(r"^FY\d", re.I)
_YTD_LTM_RE = re.compile(r"^(YTD|LTM)\d", re.I)


def _detect_gst_fy_only_cols(ws, max_c: int) -> Optional[list[int]]:
    """For General sales table: keep label col + FY/delta-FY columns; drop YTD/LTM."""
    # Find header row range
    header_rows = list(range(6, 12))
    label_col: Optional[int] = None
    kept: list[int] = []
    for c in range(1, max_c + 1):
        if _is_col_hidden(ws, c):
            continue
        headers_here: list[str] = []
        for r in header_rows:
            v = ws.cell(r, c).value
            if isinstance(v, str) and v.strip():
                headers_here.append(v.strip())
        if not headers_here:
            continue
        is_label = any(h.lower() == "keur" for h in headers_here) or (
            len(headers_here) == 1 and "|" in headers_here[0]
        )
        if is_label:
            label_col = c
            kept.append(c)
            continue
        if label_col is None:
            continue
        # Determine if this col is FY or delta-FY
        is_fy = any(
            _FY_PERIOD_RE.match(h) or ("Δ" in h and re.search(r"FY\d", h, re.I)) for h in headers_here
        )
        is_ytd_ltm = any(_YTD_LTM_RE.match(h) for h in headers_here)
        if is_fy and not is_ytd_ltm:
            kept.append(c)
        elif not is_fy and not is_ytd_ltm and kept:
            # Non-period col after label (e.g. CAGR) — include unless it's a YTD
            pass  # Skip CAGR columns entirely for cleaner output
    return kept if len(kept) > 1 else None


# Month abbreviations (German + English) for common FY year-end months
_FY_YEAREND_MONTHS = frozenset(
    {"jan", "feb", "mar", "apr", "jun", "sep", "oct", "okt", "nov", "dec", "dez"}
)
# Months that strongly suggest a mid-year snapshot (YTD)
_MID_YEAR_MONTHS = frozenset({"may", "mai", "jun", "jul", "aug"})


def _detect_aging_detail_latest_cols(ws, max_c: int) -> Optional[list[int]]:
    """Return explicit column list: kEUR/partner + latest FY Stichtag block only.

    Prefers the last block whose month abbreviation is a typical FY year-end
    (e.g. Dez / Dec) over a mid-year YTD snapshot (Jul, Aug, etc.).
    """
    period_starts: list[tuple[int, str]] = []
    for cc in range(1, max_c + 1):
        for r in (6, 7, 8):
            v = ws.cell(r, cc).value
            if not isinstance(v, str):
                continue
            text = v.strip()
            m = re.search(r"([A-Za-z]{3}\d{2}A)", text)
            if m:
                period_starts.append((cc, m.group(1)))
                break
            if _SNAPSHOT_RE.match(text):
                period_starts.append((cc, text))
                break
    if not period_starts:
        return None
    uniq: list[tuple[int, str]] = []
    for cc, lab in period_starts:
        if uniq and uniq[-1][1].lower() == lab.lower():
            continue
        uniq.append((cc, lab))
    # Prefer a year-end Stichtag over a mid-year YTD snapshot
    # Try to find the latest block that is NOT a mid-year month
    fy_blocks = [
        (cc, lab)
        for cc, lab in uniq
        if lab[:3].lower() not in _MID_YEAR_MONTHS
    ]
    chosen = (fy_blocks[-1] if fy_blocks else uniq[-1])
    latest_col, _latest_lab = chosen
    keur = None
    for cc in range(1, latest_col + 1):
        for r in range(6, 10):
            if str(ws.cell(r, cc).value or "").strip().lower() == "keur":
                keur = cc
                break
    # End at next period or at "Total" header of this block
    end = max_c
    for cc, _lab in uniq:
        if cc > latest_col:
            end = cc - 1
            break
    total_col = None
    for cc in range(latest_col, end + 1):
        for r in (7, 8):
            if str(ws.cell(r, cc).value or "").strip().lower() == "total":
                total_col = cc
                break
        if total_col is not None:
            break
    if total_col is not None:
        end = total_col
    cols = []
    if keur is not None and keur < latest_col:
        cols.append(keur)
    for c in range(latest_col, end + 1):
        if _is_col_hidden(ws, c):
            continue
        cols.append(c)
    return cols


def _is_group_recon_sheet(name: str) -> bool:
    n = name.strip().lower()
    return n in {"bs_reconciliation", "pl_reconciliation"}


def _is_entity_recon_sheet_name(name: str) -> bool:
    n = name.strip()
    if _is_group_recon_sheet(n):
        return False
    return n.endswith("_BS_Reconciliation") or n.endswith("_PL_Reconciliation")


def _anchor_start_col(ws, max_c: int) -> int:
    scan_rows = list(range(1, 14))
    keur_col: Optional[int] = None
    pipe_col: Optional[int] = None
    for c in range(1, max_c + 1):
        if _is_col_hidden(ws, c):
            continue
        for r in scan_rows:
            v = ws.cell(r, c).value
            if v in (None, ""):
                continue
            t = str(v).strip()
            if t.lower() == "keur" and keur_col is None:
                keur_col = c
            if "|" in t and len(t) <= 120 and pipe_col is None:
                pipe_col = c
        if keur_col is not None:
            break
    return keur_col or pipe_col or next(
        (c for c in range(1, max_c + 1) if not _is_col_hidden(ws, c)), 1
    )


def visible_content_columns(
    ws,
    max_scan_cols: int = 120,
    *,
    include_hidden: bool = False,
    force_start: Optional[int] = None,
    force_end: Optional[int] = None,
    force_cols: Optional[list[int]] = None,
) -> list[int]:
    """
    Skip left-side formula helper area: start at display table, take content cols.
    Sheet-specific crops may force start/end or an explicit column list.
    """
    max_c = min(int(ws.max_column or 1), max_scan_cols)
    name = _sheet_name_lower(ws)

    if name == "lead_is":
        pf = _detect_lead_is_proforma_cols(ws, max_c)
        if pf:
            force_start, force_end = pf
    elif _is_group_recon_sheet(name):
        agg = _detect_group_recon_from_aggregated(ws, max_c)
        if agg:
            force_start, force_end = agg
    elif "aging detail" in name:
        force_cols = _detect_aging_detail_latest_cols(ws, max_c)
    elif name in ("bs_bucket", "bucket_bs"):
        na_cols = _detect_bs_bucket_latest_na_cols(ws, max_c)
        if na_cols:
            force_cols = list(range(na_cols[0], na_cols[1] + 1))
    elif _is_entity_recon_sheet_name(getattr(ws, "title", "")):
        include_hidden = True

    if force_cols:
        return [c for c in force_cols if 1 <= c <= max_c]

    start = force_start if force_start is not None else _anchor_start_col(ws, max_c)
    end = force_end if force_end is not None else max_c

    scan_rows = [r for r in range(1, min(int(ws.max_row or 1), 250) + 1) if not _is_row_hidden(ws, r)]
    header_row = find_table_header_row(ws, scan_rows, list(range(start, min(end, start + 40) + 1))) or 8

    cols: list[int] = []
    empty_streak = 0
    for c in range(start, end + 1):
        if cols and _is_helper_meta_col(ws, c, header_row):
            break
        if _is_col_hidden(ws, c) and not include_hidden:
            sample = str(ws.cell(8, c).value or ws.cell(6, c).value or "").lower()
            if cols and sample in {"row_type", "ui_level", "__row_type", "__level"}:
                break
            continue
        # Stop before formula helper metadata columns (__level, __row_type, …)
        for hr in (6, 7, 8):
            hv = str(ws.cell(hr, c).value or "").strip().lower()
            if hv.startswith("__") or hv in {"row_type", "ui_level"}:
                if cols:
                    return cols
                break
        has = False
        for r in range(1, min(int(ws.max_row or 1), 250) + 1):
            if _is_row_hidden(ws, r):
                continue
            if ws.cell(r, c).value not in (None, ""):
                has = True
                break
        if not has and not (_is_col_hidden(ws, c) and include_hidden):
            empty_streak += 1
            if cols and empty_streak >= 4:
                break
            continue
        empty_streak = 0
        cols.append(c)

    if _is_group_recon_sheet(name):
        cols = _filter_group_recon_cols(ws, cols)
    return cols


def table_rows_for_pdf(ws, *, max_scan_rows: int = 800) -> tuple[list[int], list[int]]:
    """Visible content cols + rows from company|table title through data."""
    max_r = min(int(ws.max_row or 1), max_scan_rows)
    visible_cols = visible_content_columns(ws)
    if not visible_cols:
        return [], []

    all_vis_rows = [r for r in range(1, max_r + 1) if not _is_row_hidden(ws, r)]
    if not all_vis_rows:
        return [], visible_cols

    header = find_table_header_row(ws, all_vis_rows, visible_cols)
    title_row = find_company_table_title_row(ws, all_vis_rows, visible_cols, header)
    start = title_row if title_row is not None else header

    rows: list[int] = []
    for r in all_vis_rows:
        if r < start or not _row_has_content(ws, r, visible_cols):
            continue
        texts = [
            str(ws.cell(r, c).value or "").strip().lower()
            for c in visible_cols
            if ws.cell(r, c).value not in (None, "")
        ]
        if any(
            text == "check"
            or text.startswith("source -")
            or text.startswith("check source")
            for text in texts
        ):
            break
        rows.append(r)
    return rows, visible_cols


def read_sheet_titles(ws, max_rows: int = 8) -> list[str]:
    """Prefer the company|table title (PDF AG | …) for PDF chrome."""
    cols = visible_content_columns(ws, max_scan_cols=40)
    vis = [r for r in range(1, max_rows + 1) if not _is_row_hidden(ws, r)]
    if not vis or not cols:
        return [str(getattr(ws, "title", "Table"))]
    header = find_table_header_row(ws, vis, cols)
    title_row = find_company_table_title_row(ws, vis, cols, header)
    titles: list[str] = []
    if title_row is not None:
        for c in cols:
            v = ws.cell(title_row, c).value
            if v not in (None, ""):
                titles.append(str(v).strip())
                break
    name = _sheet_name_lower(ws)
    if titles:
        return titles
    for r in vis[:6]:
        for c in cols:
            v = ws.cell(r, c).value
            if v in (None, "") or isinstance(v, (datetime, date)):
                continue
            text = str(v).strip()
            if "|" in text:
                return [text]
    return [str(getattr(ws, "title", "Table"))]


def _load_font(bold: bool, size: int) -> ImageFont.ImageFont:
    candidates = []
    if bold:
        candidates += [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
        ]
    candidates += [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=max(6, int(size)))
        except Exception:
            continue
    return ImageFont.load_default()


def _draw_hatch(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], color: tuple[int, int, int], scale: int) -> None:
    x0, y0, x1, y1 = box
    step = max(3, 4 * scale)
    for i in range(x0 - (y1 - y0), x1 + 1, step):
        draw.line([(i, y0), (i + (y1 - y0), y1)], fill=color, width=1)


def _side_style(side) -> Optional[tuple[int, int, int]]:
    if side is None or getattr(side, "style", None) in (None, "none"):
        return None
    rgb = _argb_to_rgb(getattr(side, "color", None))
    return rgb or BORDER_COLOR


def _draw_cell_borders(
    draw: ImageDraw.ImageDraw,
    cell,
    x: int,
    y: int,
    cw: int,
    rh: int,
) -> None:
    border = cell.border
    if not border:
        return
    left = _side_style(getattr(border, "left", None))
    right = _side_style(getattr(border, "right", None))
    top = _side_style(getattr(border, "top", None))
    bottom = _side_style(getattr(border, "bottom", None))
    if left:
        draw.line([(x, y), (x, y + rh)], fill=left, width=1)
    if right:
        draw.line([(x + cw - 1, y), (x + cw - 1, y + rh)], fill=right, width=1)
    if top:
        draw.line([(x, y), (x + cw, y)], fill=top, width=1)
    if bottom:
        draw.line([(x, y + rh - 1), (x + cw, y + rh - 1)], fill=bottom, width=1)


def _header_band_rows(ws, rows: list[int], cols: list[int], header_row: int) -> set[int]:
    """Header row plus immediately preceding band rows that look like section headers."""
    band = {header_row}
    idx = rows.index(header_row) if header_row in rows else -1
    if idx > 0:
        prev = rows[idx - 1]
        # Include one multi-header band row above (Net sales / CAGR etc.)
        vals = [str(ws.cell(prev, c).value or "").strip() for c in cols if ws.cell(prev, c).value not in (None, "")]
        if vals and not any("|" in v for v in vals):
            band.add(prev)
    return band


def _col_excel_width(ws, col: int, *, is_label_col: bool, is_delta: bool) -> float:
    letter = get_column_letter(col)
    dim = ws.column_dimensions.get(letter)
    excel_w = float(getattr(dim, "width", None) or 11.0)
    if is_label_col:
        excel_w = max(excel_w * 1.35, 16.0)
    elif is_delta:
        excel_w = min(excel_w, 7.5)
    return excel_w


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_tw: float,
    *,
    bold: bool,
    base_size: int,
) -> tuple[str, ImageFont.ImageFont]:
    """Shrink font to fit; never truncate with ellipsis. Soft-wrap only if still too long."""
    if not text:
        return text, font
    size = base_size
    cur = font
    while size >= 6 and draw.textlength(text, font=cur) > max_tw:
        size -= 1
        cur = _load_font(bold, size)
    if draw.textlength(text, font=cur) <= max_tw:
        return text, cur
    # Last resort: wrap onto one visual line by inserting space breaks is hard in single-line cells;
    # keep full text and let it slightly overflow rather than ellipsis.
    return text, cur


def render_rows_to_png(
    ws,
    out_path: str | Path,
    rows: list[int],
    cols: list[int],
    *,
    scale: int = RENDER_SCALE,
) -> Optional[str]:
    if not rows or not cols:
        return None

    header_row = find_table_header_row(ws, rows, cols)
    title_row = find_company_table_title_row(ws, rows, cols, header_row)
    header_band = _header_band_rows(ws, rows, cols, header_row)

    # Detect delta columns from header labels
    delta_cols: set[int] = set()
    for c in cols:
        for r in header_band:
            v = str(ws.cell(r, c).value or "").strip()
            if _DELTA_RE.match(v) or "Δ" in v or v.lower().startswith("delta"):
                delta_cols.add(c)

    label_col = cols[0]
    col_widths_px: list[int] = []
    for c in cols:
        excel_w = _col_excel_width(ws, c, is_label_col=(c == label_col), is_delta=(c in delta_cols))
        col_widths_px.append(int(max(20, min(excel_w * 7.0, 220)) * scale))

    row_heights_px: list[int] = []
    for r in rows:
        dim = ws.row_dimensions.get(r)
        h = float(getattr(dim, "height", None) or 14.0)
        if r in header_band:
            # Give header rows more breathing room so text isn't clipped at bottom
            h = max(h, 20.0)
            row_heights_px.append(int(max(20, min(h * 1.35, 60)) * scale))
        else:
            row_heights_px.append(int(max(12, min(h * 1.15, 32)) * scale))

    pad = 4 * scale
    width = sum(col_widths_px) + pad * 2
    height = sum(row_heights_px) + pad * 2
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    font_reg = _load_font(False, BODY_FONT_PX)
    font_bold = _load_font(True, BODY_FONT_PX)
    font_title = _load_font(True, TITLE_FONT_PX)

    y = pad
    for ri, r in enumerate(rows):
        x = pad
        rh = row_heights_px[ri]
        level = _outline_level(ws, r)
        is_title = title_row is not None and r == title_row
        is_headerish = r in header_band

        for ci, c in enumerate(cols):
            cw = col_widths_px[ci]
            cell = ws.cell(r, c)
            bg, pattern = _fill_info(cell)
            if bg == (255, 255, 255) and is_headerish:
                bg = HEADER_FILL_FALLBACK
            if bg != (255, 255, 255):
                draw.rectangle([x, y, x + cw, y + rh], fill=bg, outline=None)
            if pattern:
                _draw_hatch(draw, (x, y, x + cw, y + rh), (160, 170, 185), scale)

            raw = format_cell_value(cell.value, str(cell.number_format or "General"))
            if raw:
                fg = _font_rgb(cell)
                if is_title and fg == (17, 24, 39):
                    fg = NAVY
                bold = bool(cell.font and cell.font.bold) or is_title or is_headerish
                size = float(getattr(cell.font, "size", 9) or 9) if cell.font else 9
                base_font = font_title if (is_title or size >= 12) else (font_bold if bold else font_reg)
                base_size = TITLE_FONT_PX if base_font is font_title else BODY_FONT_PX
                indent = (level * 5 * scale) if ci == 0 else 0

                if is_headerish:
                    align = "left" if c == label_col else "right"
                    valign = "bottom"
                else:
                    align = (cell.alignment.horizontal if cell.alignment else None) or "left"
                    if isinstance(cell.value, (int, float)) and align == "left":
                        align = "right"
                    valign = "middle"

                max_tw = cw - 4 * scale - indent
                text, font = _fit_text(draw, raw, base_font, max_tw, bold=bold, base_size=base_size)
                tw = draw.textlength(text, font=font) if text else 0
                try:
                    bbox = font.getbbox(text)
                    # bbox = (left, top, right, bottom) relative to draw origin
                    # top can be negative; height = bottom - top
                    b_top = bbox[1]
                    b_bottom = bbox[3]
                    th = bbox[3] - b_top
                except Exception:
                    b_top = 0
                    b_bottom = BODY_FONT_PX
                    th = BODY_FONT_PX
                if valign == "bottom":
                    # PIL positions text from the font origin; align actual glyph bottom.
                    ty = y + rh - b_bottom - 4 * scale
                else:
                    ty = y + max(1, (rh - th) // 2) - b_top
                if align == "right":
                    tx = x + cw - 3 * scale - tw
                elif align == "center":
                    tx = x + (cw - tw) / 2
                else:
                    tx = x + 3 * scale + indent
                draw.text((tx, ty), text, fill=fg, font=font)

            _draw_cell_borders(draw, cell, x, y, cw, rh)
            x += cw
        y += rh

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG", optimize=True)
    return str(out_path)


def render_sheet_screenshots_paged(
    ws,
    out_dir: str | Path,
    *,
    prefix: str = "sheet",
    rows_per_page: int = 200,
    max_cols: int = 80,
    scale: int = RENDER_SCALE,
    prefer_single_page: bool = True,
) -> list[str]:
    """Render table to PNG. Prefer one page (scaled down in PDF) unless the table is huge."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    visible_rows, visible_cols = table_rows_for_pdf(ws, max_scan_rows=800)
    if not visible_rows or not visible_cols:
        return []
    if len(visible_cols) > max_cols:
        visible_cols = visible_cols[:max_cols]

    if prefer_single_page and len(visible_rows) <= max(rows_per_page, 120):
        p = render_rows_to_png(
            ws, out_dir / f"{prefix}_0.png", visible_rows, visible_cols, scale=scale
        )
        return [p] if p else []

    paths: list[str] = []
    sticky_n = min(3, len(visible_rows))
    sticky = visible_rows[:sticky_n]
    body = visible_rows[sticky_n:]
    if not body:
        p = render_rows_to_png(ws, out_dir / f"{prefix}_0.png", visible_rows, visible_cols, scale=scale)
        return [p] if p else []

    chunk = max(12, rows_per_page - len(sticky))
    for i in range(0, len(body), chunk):
        rows = sticky + body[i : i + chunk]
        p = render_rows_to_png(
            ws,
            out_dir / f"{prefix}_{i // chunk}.png",
            rows,
            visible_cols,
            scale=scale,
        )
        if p:
            paths.append(p)
    return paths
