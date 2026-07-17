"""Finssentials visual tokens for Fast Track PDF reports."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PdfTheme:
    brand_navy: str = "#1E3A5F"
    brand_navy_mid: str = "#2E5A8A"
    text_primary: str = "#111827"
    text_muted: str = "#475569"
    text_light: str = "#64748B"
    surface: str = "#F8FAFC"
    line: str = "#E2E8F0"
    white: str = "#FFFFFF"
    commentary_bg: str = "#FAFBFC"

    font_name: str = "Helvetica"  # reportlab built-in; Calibri may be absent
    font_name_bold: str = "Helvetica-Bold"

    # Match Excel table body size (THEME.font_size = 9)
    font_size_body: float = 9.0
    font_size_small: float = 8.0
    font_size_title: float = 22.0
    font_size_section: float = 28.0
    font_size_subtitle: float = 12.0
    font_size_caption: float = 8.0

    # Landscape A4 (points)
    page_w: float = 841.89
    page_h: float = 595.28
    margin: float = 36.0

    # Left content ~58%, right commentary ~38% of usable width
    left_frac: float = 0.58
    gutter: float = 16.0


THEME = PdfTheme()


def hex_to_rgb(color: str) -> tuple[float, float, float]:
    c = str(color or "").strip().lstrip("#")
    if len(c) == 8:
        c = c[2:]
    if len(c) != 6:
        return (0.12, 0.23, 0.37)
    return (int(c[0:2], 16) / 255.0, int(c[2:4], 16) / 255.0, int(c[4:6], 16) / 255.0)
