"""
Excel theme for General Sales Table — aligned with Financials → Income Statement (frontend).

Reference: frontend/src/components/financials/FinancialStatementTable.tsx
"""

from __future__ import annotations

from dataclasses import dataclass
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side


@dataclass(frozen=True)
class GstExcelTheme:
    """ARGB hex without # prefix (openpyxl fgColor)."""

    font_name: str = "GT Walsheim LC Light"
    font_size: int = 8
    font_size_title: int = 24
    font_size_subtitle: int = 12
    font_size_company: int = 9

    # Surfaces
    header_bg: str = "FFF8FAFC"
    subtotal_bg: str = "FFF8FAFC"
    gm_column_bg: str = "FFF8FAFC"
    tech_col_bg: str = "FFF1F5F9"
    white_bg: str = "FFFFFFFF"

    zero_row_bg: str = "FFF1F5F9"

    # Text
    text_primary: str = "FF111827"
    text_header: str = "FF475569"
    text_kpi: str = "FF64748B"
    text_account: str = "FF475569"
    text_cagr: str = "FF64748B"
    text_brand_title: str = "FF1E3A5F"

    # Deltas (Income Statement)
    delta_positive: str = "FF10B981"
    delta_negative: str = "FFDC2626"
    delta_zero: str = "FF94A3B8"

    # Borders
    border_color: str = "FFE2E8F0"
    border_strong: str = "FFE2E8F0"

    @property
    def font_base(self) -> Font:
        return Font(name=self.font_name, size=self.font_size, color=self.text_primary)

    @property
    def font_bold(self) -> Font:
        return Font(name=self.font_name, size=self.font_size, bold=True, color=self.text_primary)

    @property
    def font_header(self) -> Font:
        return Font(name=self.font_name, size=self.font_size, bold=True, color=self.text_header)

    @property
    def fill_header(self) -> PatternFill:
        return PatternFill(fill_type="solid", fgColor=self.header_bg)

    @property
    def fill_subtotal(self) -> PatternFill:
        return PatternFill(fill_type="solid", fgColor=self.subtotal_bg)

    @property
    def fill_gm(self) -> PatternFill:
        return PatternFill(fill_type="solid", fgColor=self.gm_column_bg)

    @property
    def fill_white(self) -> PatternFill:
        return PatternFill(fill_type="solid", fgColor=self.white_bg)

    @property
    def fill_tech(self) -> PatternFill:
        return PatternFill(fill_type="solid", fgColor=self.tech_col_bg)

    @property
    def fill_zero_row(self) -> PatternFill:
        return PatternFill(fill_type="solid", fgColor=self.zero_row_bg)

    @property
    def font_zero_row(self) -> Font:
        return Font(name=self.font_name, size=self.font_size, color=self.delta_zero)

    @property
    def border_header_bottom(self) -> Border:
        s = Side(style="medium", color=self.border_strong)
        return Border(bottom=s)

    @property
    def border_subtotal_top(self) -> Border:
        s = Side(style="medium", color=self.border_strong)
        return Border(top=s)

    @property
    def border_kpi_section_top(self) -> Border:
        return self.border_subtotal_top


THEME = GstExcelTheme()

SCHEMA_ROWS = [
    ("Header row", "Background", "F8FAFC", THEME.header_bg),
    ("Header row", "Text", "475569 (all period columns)", THEME.text_header),
    ("Header row", "Bottom border", "2px E2E8F0", THEME.border_strong),
    ("Period data columns", "Background", "FFFFFF (no highlight on latest period)", THEME.white_bg),
    ("Data body", "Text", "111827", THEME.text_primary),
    ("Account / detail label", "Text", "475569", THEME.text_account),
    ("Level-1 subtotal row", "Background", "F8FAFC", THEME.subtotal_bg),
    ("Level-1 subtotal row", "Top border", "2px E2E8F0", THEME.border_strong),
    ("Block total (row_type=total)", "Background", "F8FAFC", THEME.subtotal_bg),
    ("Block total", "Top border", "2px E2E8F0", THEME.border_strong),
    ("Detail rows", "Row borders", "none", ""),
    ("Delta positive", "Font", "10B981", THEME.delta_positive),
    ("Delta negative", "Font", "DC2626", THEME.delta_negative),
    ("Delta zero", "Font", "94A3B8", THEME.delta_zero),
    ("GM CAGR column only", "Background", "F8FAFC", THEME.gm_column_bg),
    ("KPI section", "Background", "F8FAFC", THEME.header_bg),
    ("KPI section title", "Text", "1E3A5F", THEME.text_brand_title),
    ("KPI rows", "Text italic", "64748B", THEME.text_kpi),
    ("Hidden key columns", "Background", "F1F5F9", THEME.tech_col_bg),
    ("Title / subtitle", "Text", "1E3A5F", THEME.text_brand_title),
    ("Font family", "All cells", "GT Walsheim LC Light", ""),
    ("Font size data", "All cells", "8pt", ""),
    ("All-zero data row", "Background", "F1F5F9", THEME.zero_row_bg),
    ("All-zero data row", "Text", "94A3B8", THEME.delta_zero),
]


def apply_zero_row_conditional_formatting(
    ws,
    *,
    first_row: int,
    last_row: int,
    year_col_indices: list[int],
    style_start_col: int,
    style_end_col: int,
    exclude_cols: set[int] | None = None,
) -> None:
    """
    Gray out rows where every year value column is exactly 0.
    Uses one CF rule; Excel adjusts the row reference per row in the range.
    """
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.utils import get_column_letter

    if first_row > last_row or not year_col_indices:
        return

    year_cols = sorted({c for c in year_col_indices if c > 0})
    if not year_cols:
        return

    skip = exclude_cols or set()
    parts = [f"{get_column_letter(c)}{first_row}=0" for c in year_cols]
    formula = f"AND({','.join(parts)})"

    rule = FormulaRule(
        formula=[formula],
        fill=THEME.fill_zero_row,
        font=THEME.font_zero_row,
    )

    style_cols = [c for c in range(style_start_col, style_end_col + 1) if c not in skip]
    if not style_cols:
        return

    # Merge contiguous column runs (e.g. skip spacer column in Lead_IS).
    runs: list[tuple[int, int]] = []
    run_start = style_cols[0]
    prev = style_cols[0]
    for c in style_cols[1:]:
        if c == prev + 1:
            prev = c
            continue
        runs.append((run_start, prev))
        run_start = c
        prev = c
    runs.append((run_start, prev))

    for start_c, end_c in runs:
        start_cell = f"{get_column_letter(start_c)}{first_row}"
        end_cell = f"{get_column_letter(end_c)}{last_row}"
        ws.conditional_formatting.add(f"{start_cell}:{end_cell}", rule)
