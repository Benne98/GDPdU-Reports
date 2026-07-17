"""Extract visible workbook cells for PDF tables (respect hidden / outline)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import coordinate_from_string, column_index_from_string


@dataclass
class CellData:
    value: Any
    bold: bool = False
    fill_hex: Optional[str] = None
    font_hex: Optional[str] = None
    align: str = "left"
    number_format: str = "General"


@dataclass
class ExtractedTable:
    sheet_name: str
    title_lines: list[str] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)
    rows: list[list[CellData]] = field(default_factory=list)
    outline_levels: list[int] = field(default_factory=list)
    col_widths: list[float] = field(default_factory=list)


def _argb_to_hex(color) -> Optional[str]:
    if color is None:
        return None
    rgb = getattr(color, "rgb", None)
    if not rgb or not isinstance(rgb, str):
        return None
    c = rgb.upper()
    if c.startswith("FF") and len(c) == 8:
        c = c[2:]
    if len(c) == 6 and c != "000000":
        return f"#{c}"
    if len(c) == 6:
        return f"#{c}"
    return None


def _is_row_hidden(ws, row: int) -> bool:
    dim = ws.row_dimensions.get(row)
    return bool(dim and dim.hidden)


def _is_col_hidden(ws, col: int) -> bool:
    letter = get_column_letter(col)
    dim = ws.column_dimensions.get(letter)
    return bool(dim and dim.hidden)


def _outline_level(ws, row: int) -> int:
    dim = ws.row_dimensions.get(row)
    if dim is None:
        return 0
    try:
        return int(dim.outlineLevel or 0)
    except Exception:
        return 0


def _cell_data(cell) -> CellData:
    font = cell.font
    fill = cell.fill
    align = (cell.alignment.horizontal or "left") if cell.alignment else "left"
    if align not in {"left", "center", "right"}:
        align = "left"
    fill_hex = None
    if fill and getattr(fill, "fill_type", None) == "solid":
        fill_hex = _argb_to_hex(getattr(fill, "fgColor", None))
    font_hex = _argb_to_hex(getattr(font, "color", None)) if font else None
    return CellData(
        value=cell.value,
        bold=bool(font and font.bold),
        fill_hex=fill_hex,
        font_hex=font_hex,
        align=align,
        number_format=str(cell.number_format or "General"),
    )


def _format_display(value: Any, number_format: str) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        fmt = (number_format or "").lower()
        if "%" in fmt:
            return f"{float(value) * 100:,.1f}%"
        if abs(float(value)) >= 1000 or float(value) == int(float(value)):
            try:
                if float(value) == int(float(value)):
                    return f"{int(value):,}"
            except Exception:
                pass
            return f"{float(value):,.1f}"
        return f"{float(value):,.2f}"
    return str(value).strip()


def read_title_block(ws, max_scan_rows: int = 8) -> list[str]:
    """Collect non-empty title cells from typical Fast Track header area (cols D–F)."""
    lines: list[str] = []
    seen: set[str] = set()
    for row in range(1, max_scan_rows + 1):
        for col in range(1, min(8, (ws.max_column or 1) + 1)):
            if _is_col_hidden(ws, col):
                continue
            cell = ws.cell(row, col)
            if cell.value is None:
                continue
            text = str(cell.value).strip()
            if not text or text in seen:
                continue
            # Skip pure section filler noise
            if text.lower() in {"finssentials"} and row > 2:
                continue
            font = cell.font
            size = float(getattr(font, "size", 0) or 0) if font else 0
            bold = bool(font and font.bold)
            if size >= 11 or bold or col >= 4:
                lines.append(text)
                seen.add(text)
                if len(lines) >= 4:
                    return lines
    return lines


def extract_sheet_table(ws, *, max_rows: int = 400, max_cols: int = 40) -> ExtractedTable:
    """
    Extract a printable table from a worksheet.
    Hidden rows/columns are omitted (collapsed outline / helper cols stay out).
    """
    max_r = min(int(ws.max_row or 1), max_rows)
    max_c = min(int(ws.max_column or 1), max_cols)

    visible_cols = [c for c in range(1, max_c + 1) if not _is_col_hidden(ws, c)]
    if not visible_cols:
        visible_cols = list(range(1, max_c + 1))

    title_lines = read_title_block(ws)

    # Find first data-ish row: prefer row after titles, or first row with ≥2 non-empty cells
    data_start = 1
    for row in range(1, min(12, max_r) + 1):
        if _is_row_hidden(ws, row):
            continue
        nonempty = sum(
            1
            for c in visible_cols
            if ws.cell(row, c).value not in (None, "")
        )
        if nonempty >= 2:
            # If this looks like a title-only row in col D, keep scanning
            data_start = row
            break

    rows: list[list[CellData]] = []
    outline_levels: list[int] = []
    headers: list[str] = []

    first_data = True
    for row in range(data_start, max_r + 1):
        if _is_row_hidden(ws, row):
            continue
        cells = [_cell_data(ws.cell(row, c)) for c in visible_cols]
        if all(c.value in (None, "") for c in cells):
            continue
        if first_data:
            headers = [_format_display(c.value, c.number_format) for c in cells]
            first_data = False
            # Keep header as first visual row too for styling later
            rows.append(cells)
            outline_levels.append(0)
            continue
        rows.append(cells)
        outline_levels.append(_outline_level(ws, row))

    # Column widths (approx character widths → relative)
    col_widths: list[float] = []
    for c in visible_cols:
        letter = get_column_letter(c)
        dim = ws.column_dimensions.get(letter)
        w = float(getattr(dim, "width", None) or 12.0)
        col_widths.append(max(4.0, min(w, 28.0)))

    return ExtractedTable(
        sheet_name=str(ws.title),
        title_lines=title_lines,
        headers=headers,
        rows=rows,
        outline_levels=outline_levels,
        col_widths=col_widths,
    )


def cell_display(cell: CellData) -> str:
    return _format_display(cell.value, cell.number_format)
