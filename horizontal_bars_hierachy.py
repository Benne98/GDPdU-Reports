import os
import re
import ast
import textwrap
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional, Set

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.drawing.image import Image as XLImage

from matplotlib.patches import Rectangle, Patch
from matplotlib.colors import to_hex, to_rgba


def _xl_image_from_path(path: str) -> XLImage:
    """Embed PNG bytes so temp files can be deleted before workbook.save()."""
    return XLImage(BytesIO(Path(path).read_bytes()))

# -------------------------
# Imports aus Funktionssammlung (Code 3)
# -------------------------
from funktionssammlung import (
    apply_filters,
    safe_month_day_ts,
    fiscal_year_bounds,
    recognized_value,
    fy_label,
    pick_colors_hardcoded,
    get_period,
    compute_amount,
    compute_current_fy_end_year,
)

# ==========================
# CONFIG
# ==========================
CONFIG: Dict[str, Any] = {

    # Font (family comes from gst_excel_theme; size/weight only)
    "font": {
        "size": 7,
        "weight": 100,
    },

    # -------------------------
    # Header / Excel
    # -------------------------
    "title": "Project Draft",                    # Excel Header (Zeile 1)
    "table": "Gross sales breakdown",            # Tabellenname
    "company": "Draft AG",                       # Company Label
    "subtitle_suffix": "Revenue share by dimension by invoiced amounts",  # Klammerzusatz hinter table
    "period_label_prefix": "",                   # optionaler Prefix vor table/period (z.B. "YTD", "LTM" etc.)

    # -------------------------
    # Zeit / Periode (as-of immer Monatsende)
    # -------------------------
    "current_year": 2025,                        # Betrachtungsjahr
    "current_month": 7,                          # Betrachtungsmonat (1..12)
    "fiscal_year_end_month": 7,                  # FY End Monat (z.B. 7 => Juli)
    "fiscal_year_end_day": 31,                   # FY End Tag (z.B. 31)

    "period_mode": "FY",                         # "FY" | "YTD" | "LTM"
    "calc_mode": "invoice",                      # "invoice" | "accrual"
    "invoice_mapping_mode": "date",              # "date" | "year" (period column)

    # -------------------------
    # Input Spalten
    # -------------------------
    "value_col": "Revenue",                      # Wert (numerisch)
    "invoice_col": "Invoice date",
    "start_col": "Contract Start Date",          # Contract Start (nur accrual)
    "end_col": "Contract End Date",              # Contract End (nur accrual)

    # -------------------------
    # Filter (nutzt apply_filters aus Funktionssammlung)
    # -------------------------
    "filters": {
        "enabled": True,
        "rules": [{"col": "Fiscal year", "op": "in", "values": {2025}},
                  {"col": "Core products IM", "op": "not_in", "values": {"Other", "Smart home"}},
                  {"col": "Service Type", "op": "in", "values": {"Hardware", "License"}},
                  {"col": "Entity",       "op": "not_in", "values": {"Internal"}},
                  {"col": "Revenue",      "op": ">=", "value": 0},
                  {"col": "Invoice Date", "op": "between", "start": "2024-01-01", "end": "2024-12-31"},
                  {"col": "Customer Name", "op": "regex", "pattern": r"^SAP|^Oracle"},
                  {"col": "Quantity", "op": "Qutna"}
                  ],
    },

    # -------------------------
    # FX (optional)
    # -------------------------
    "apply_fx": False,                           # FX anwenden an/aus
    "fx_col": "Functional FX Rate",             # FX Rate Spalte

    # -------------------------
    # Chart-Dimensionen
    # -------------------------
    "dimensions": {
        "bar_col": "BP Channel Mapping",         # <- links: die Balken-Gruppierung (z.B. Customer / Sales Channel)
        "parent_col": "Core products IM",        # <- Oberfamilie (Parent) für Farben/Legend-Cluster
        "child_col": "Product group new",        # <- Unterfamilie (Child) für Farben/Legend-Einträge
        # bei keiner Hierarchie:
        # parent_col leer lassen und nur child_col setzen
    },

    # -------------------------
    # Segment-Auswahl / Other-Logik
    # -------------------------
    "bars": {
        "top_n": 5,                              # max. Balken (inkl. Rest)
        "create_other_bar": True,                # bei > top_n Entities: letzter Balken = Rest
        "other_label": "Rest",
        "bar_order": "value_desc",
    },

    "segmenting": {
        "rank_by": "amount",                     # "amount" (bis jetzt keine anderen Möglichkeiten)
        "max_segments_per_bar": 16,              # Top-N Segmente pro Balken (inkl. Other-Bucket wenn aktiv)
        "sort_mode": "parent",                   # "parent" | "child"  Sortierung immer gleich (balken), aber gaclustart oder nicht?

        "create_other_bucket": True,             # Rest-Bucket erzeugen
        "other_label": "Rest",                   # Label für Other im Output
        "other_selection_mode": "top_n_per_parent",  # "min_share_to_other" oder "top_n_per_parent"
        "other_position_mode": "global_end",     # "after_parent" oder "global_end"
        "min_share_to_other": 0.00,              # z.B. 0.03 => alles <=3% wird zusätzlich in Other aggregiert
        "top_n_per_parent": 5,

        "combine_input_other": False,            # Input-"Other" in den Rest schieben
        "input_other_tokens": {"Other"},         # Tokens im Input, die als Other gelten
        "missing_token": "__MISSING__",          # Missing Token (für NaN/Blank)
        "hatch_other": "////",                   # Hatch für Other Segment
        "other_edge_lw": 0.0,                    # Linienbreite für Other Hatch Rand

        # Negative: standardmäßig verboten. Wenn True, werden negative in Other netto aggregiert.
        "allow_negative_by_netting_into_other": True,
    },

    # -------------------------
    # Plot
    # -------------------------
    "plot": {
        # Dynamische FIGURE-HÖHE (wie dein Bridge-Code, nur gedreht):
        "fixed_width_in": 4.2,
        "bars_per_inch": 2.65,                   # Breite fix (inches)
        "min_height_in": 1,                      # Mindesthöhe
        "max_height_in": 120.0,                  # Maximalhöhe (Deckel)

        # Rendering / Saving
        "render_dpi": 180,                       # Figure DPI im Speicher
        "save_dpi": 450,                         # PNG DPI (hoch für Excel-Schärfe)

        # Layout
        "bar_height": 0.62,                      # Balkendicke (0..1)
        "left_label_wrap": 16,                   # Umbruchbreite für Y-Achsenlabels
        "left_label_max_lines": 2,               # Max Zeilen für Y-Achsenlabels
        "left_label_fontsize": 5.5,              # Y-Achsen-Schrift etwas kleiner
        "x_margin_left": 0.30,                   # Platz links (Fig fraction) — Labels nicht abschneiden
        "x_margin_right": 0.98,                  # Platz rechts
        "y_margin_top": 0.82,                    # Platz oben (für Periodentitel)
        "y_margin_bottom": 0.02,                 # Platz unten (Legend ist separat)

        # Optional: Prozentewerte in den Segmenten anzeigen
        "show_in_bar_labels": True,              # True => Prozent in Segmenten anzeigen
        "label_min_share": 0.05,                 # nur ab X Anteil labeln
        "label_fontsize": 6.5,                   # Font size Labels

        # Optik
        "segment_edge_lw": 0.6,                  # Segment-Trennlinien
        "segment_edge_color": "white",
        "show_box_around_bar": False,            # Rahmen um kompletten Balken

        # Periodentitel über jedem Chart (wie Excel-Überschrift: Calibri / navy)
        "show_period_title": True,
        "period_title_fontsize": None,           # default: same as left_label_fontsize
        "show_plot_title": False,
        "plot_title_size": 11,
    },

    # -------------------------
    # Legend: eigenes PNG, Parent = Spalte
    # -------------------------
    "legend": {
        "enabled": True,                         # Legend erzeugen
        "group_mode": "parent_cluster",          # "flat" | "parent_cluster"
        "sort_by": "amount",                     # "amount" | "share"
        "font_size": 5,                          # Legend Font
        "max_items": 60,                         # harte Kappe
        "ncol_flat": 6,                          # nur relevant bei flat
        "marker": {
            "shape": "rect",                     # "rect"
            "width_pt": 10.0,                    # Rechteck-Breite (points)
            "height_pt": 6.0,                    # Rechteck-Höhe (points)
            "edgecolor": "none",                 # Randfarbe Marker
            "edgewidth": 0.0,                    # Randbreite
        },
        "cluster": {
            "box_linestyle": "--",
            "box_linewidth": 0.5,
            "gap_px": 8,                         # minimaler Abstand zwischen Parent-Boxen (px)
            "box_pad_px": 2,                     # Padding um Legend-BBox (px)
            "title_gap_px": 2,                   # Abstand Parent-Titel über Box (px)
            "top_pad_px": 2,                     # extra oben in Figure (px)
        },
        "save_dpi": 450,                         # DPI für Legend-PNG
        "pad_inches": 0.05,                      # Padding beim Speichern
    },

    # -------------------------
    # Output
    # -------------------------
    "output_dir": ".",
    "output_prefix": "hbar_breakdown",

    # -------------------------
    # Excel embedding
    # -------------------------
    "excel": {
        "enabled": True,                         # Excel erzeugen
        "sheet_name": "HBAR",                    # Tabellenblattname
        "col_offset": 3,                         # Graue linke Spalten A..C
        "image_anchor_cell": "D6",               # Plot-Anker
        "legend_anchor_cell": "D18",             # Legend-Anker (näher unter Charts)
        "set_column_widths": True,               # Spaltenbreiten setzen

        # Feste Anzeigegroße in Excel (unabhangig von PNG DPI)
        "display_dpi": 180,
        "image_scale": 0.50,                     # Plot Skalierung in Excel
        "legend_scale": 0.3,                     # Legend Skalierung in Excel

        # optional: harte Max-Breite der Legend (px) nach Skalierung
        "legend_max_width_px": None,
    },
}

# ==========================
# Utilities
# ==========================

# ==========================
# FONT / GLOBAL STYLE
# ==========================
from gst_excel_theme import THEME, apply_matplotlib_theme, matplotlib_font_properties

font_cfg = CONFIG.get("font", {}) or {}
apply_matplotlib_theme(
    size=int(font_cfg.get("size", 7)),
    weight=int(font_cfg.get("weight", 100)),
)
FONT_PROP = matplotlib_font_properties(
    size=int(font_cfg.get("size", 7)),
    weight=int(font_cfg.get("weight", 100)),
)

BRAND_BLUE = "#1E3A5F"


def get_comparison_periods(cfg: Dict[str, Any]) -> List[Tuple[pd.Timestamp, pd.Timestamp, str]]:
    """
    Three comparison windows left→right (oldest→newest), same as vertical_bars:
    - At FYE: last 3 completed FYs
    - Else: previous FY, last completed FY, YTD of the open FY
    - invoice_mapping_mode='year': always last 3 completed FYs (no YTD)
    """
    CY = int(cfg["current_year"])
    m = int(cfg["current_month"])
    fy_end_m = int(cfg.get("fiscal_year_end_month", 12))
    fy_end_d = int(cfg.get("fiscal_year_end_day", 31))

    as_of_end = pd.Timestamp(CY, m, 1) + pd.offsets.MonthEnd(0)
    fye_this_calendar_year = safe_month_day_ts(as_of_end.year, fy_end_m, fy_end_d)
    at_fiscal_year_end = int(as_of_end.month) == int(fy_end_m)
    year_mapping = str(cfg.get("invoice_mapping_mode") or "date").strip().lower() == "year"

    def _label(y: int) -> str:
        return f"FY{str(y)[-2:]}A"

    last_completed = as_of_end.year if as_of_end >= fye_this_calendar_year else as_of_end.year - 1
    if at_fiscal_year_end or year_mapping:
        years = [last_completed - 2, last_completed - 1, last_completed]
        out: List[Tuple[pd.Timestamp, pd.Timestamp, str]] = []
        for y in years:
            s, e = fiscal_year_bounds(y, fy_end_m, fy_end_d)
            out.append((s, e, _label(y)))
        return out

    cur_fy_end_year = compute_current_fy_end_year(as_of_end, fy_end_m, fy_end_d)
    prev_fy = last_completed
    older_fy = last_completed - 1
    ytd_start, _ = fiscal_year_bounds(cur_fy_end_year, fy_end_m, fy_end_d)
    ytd_label = f"YTD{str(cur_fy_end_year)[-2:]}"

    s0, e0 = fiscal_year_bounds(older_fy, fy_end_m, fy_end_d)
    s1, e1 = fiscal_year_bounds(prev_fy, fy_end_m, fy_end_d)
    return [
        (s0, e0, _label(older_fy)),
        (s1, e1, _label(prev_fy)),
        (ytd_start, as_of_end, ytd_label),
    ]

def normalize_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(cfg)

    # -------------------------
    # Required top-level keys
    # -------------------------
    required = [
        "current_year",
        "current_month",
        "period_mode",
        "calc_mode",
        "value_col",
        "invoice_col",
        "output_dir",
        "output_prefix",
    ]
    for k in required:
        if k not in out or out[k] in (None, ""):
            raise ValueError(f"CONFIG['{k}'] muss gesetzt sein.")

    # -------------------------
    # Optional string defaults
    # -------------------------
    out["title"] = str(out.get("title", ""))
    out["table"] = str(out.get("table", ""))
    out["company"] = str(out.get("company", ""))
    out["subtitle_suffix"] = str(out.get("subtitle_suffix", ""))
    out["period_label_prefix"] = str(out.get("period_label_prefix", ""))

    out["value_col"] = str(out["value_col"])
    out["invoice_col"] = str(out["invoice_col"])
    out["start_col"] = str(out.get("start_col", "Contract Start Date"))
    out["end_col"] = str(out.get("end_col", "Contract End Date"))

    out["output_dir"] = str(out["output_dir"])
    out["output_prefix"] = str(out["output_prefix"])

    # -------------------------
    # Mode checks
    # -------------------------
    out["calc_mode"] = str(out["calc_mode"]).lower()
    if out["calc_mode"] not in {"invoice", "accrual"}:
        raise ValueError("CONFIG['calc_mode'] muss 'invoice' oder 'accrual' sein.")

    out["period_mode"] = str(out["period_mode"]).upper()
    if out["period_mode"] not in {"FY", "YTD", "LTM"}:
        raise ValueError("CONFIG['period_mode'] muss 'FY', 'YTD' oder 'LTM' sein.")

    # -------------------------
    # Date sanity
    # -------------------------
    out["current_year"] = int(out["current_year"])
    out["current_month"] = int(out["current_month"])
    if not (1 <= out["current_month"] <= 12):
        raise ValueError("CONFIG['current_month'] muss 1..12 sein.")

    fy_end_m = int(out.get("fiscal_year_end_month", 12))
    fy_end_d = int(out.get("fiscal_year_end_day", 31))
    if not (1 <= fy_end_m <= 12):
        raise ValueError("CONFIG['fiscal_year_end_month'] muss 1..12 sein.")
    if not (1 <= fy_end_d <= 31):
        raise ValueError("CONFIG['fiscal_year_end_day'] muss 1..31 sein.")

    out["fiscal_year_end_month"] = fy_end_m
    out["fiscal_year_end_day"] = fy_end_d

    # -------------------------
    # Filters
    # -------------------------
    filt = out.get("filters") or {}
    out["filters"] = filt
    filt["enabled"] = bool(filt.get("enabled", False))

    rules = filt.get("rules", [])
    if rules is None:
        rules = []
    if not isinstance(rules, list):
        raise ValueError("CONFIG['filters']['rules'] muss eine Liste sein.")
    filt["rules"] = rules

    # -------------------------
    # FX
    # -------------------------
    out["apply_fx"] = bool(out.get("apply_fx", False))
    if out["apply_fx"]:
        if not out.get("fx_col"):
            raise ValueError("apply_fx=True, aber fx_col fehlt.")
        out["fx_col"] = str(out["fx_col"])
    else:
        out["fx_col"] = str(out.get("fx_col", "Functional FX Rate"))

    # -------------------------
    # Dimensions
    # -------------------------
    dims = out.get("dimensions") or {}
    out["dimensions"] = dims

    if not dims.get("bar_col"):
        raise ValueError("CONFIG['dimensions']['bar_col'] muss gesetzt sein.")

    dims["bar_col"] = str(dims["bar_col"])

    parent_col = dims.get("parent_col", "")
    child_col = dims.get("child_col", "")

    dims["parent_col"] = "" if parent_col in (None, "") else str(parent_col)
    dims["child_col"] = "" if child_col in (None, "") else str(child_col)

    if dims["parent_col"] == "" and dims["child_col"] == "":
        raise ValueError("Mindestens dimensions['child_col'] oder dimensions['parent_col'] muss gesetzt sein.")

    # Falls nur parent_col gesetzt ist, child_col automatisch darauf setzen
    if dims["child_col"] == "" and dims["parent_col"] != "":
        dims["child_col"] = dims["parent_col"]

    # -------------------------
    # Bars
    # -------------------------
    bars = out.get("bars") or {}
    out["bars"] = bars

    bars["top_n"] = int(bars.get("top_n", 5))
    if bars["top_n"] < 1:
        raise ValueError("bars.top_n muss >= 1 sein.")

    bars["create_other_bar"] = bool(bars.get("create_other_bar", True))
    bars["other_label"] = str(bars.get("other_label", "Rest"))

    bars["bar_order"] = str(bars.get("bar_order", "value_desc")).lower()
    if bars["bar_order"] not in {"value_desc", "value_asc"}:
        raise ValueError("bars.bar_order muss 'value_desc' oder 'value_asc' sein.")

    out["invoice_mapping_mode"] = str(out.get("invoice_mapping_mode") or "date").strip().lower()
    if out["invoice_mapping_mode"] not in {"year", "date"}:
        raise ValueError("CONFIG['invoice_mapping_mode'] muss 'year' oder 'date' sein.")
    if out["invoice_mapping_mode"] == "year" and out["calc_mode"] != "invoice":
        raise ValueError("invoice_mapping_mode='year' ist nur bei calc_mode='invoice' unterstützt.")

    # -------------------------
    # Segmenting
    # -------------------------
    seg = out.get("segmenting") or {}
    out["segmenting"] = seg

    seg["rank_by"] = str(seg.get("rank_by", "amount")).lower()
    if seg["rank_by"] not in {"amount"}:
        raise ValueError("segmenting.rank_by muss aktuell 'amount' sein.")

    seg["max_segments_per_bar"] = int(seg.get("max_segments_per_bar", 10))
    if seg["max_segments_per_bar"] < 1:
        raise ValueError("segmenting.max_segments_per_bar muss >= 1 sein.")

    seg["create_other_bucket"] = bool(seg.get("create_other_bucket", True))
    seg["other_label"] = str(seg.get("other_label", "Other"))
    seg["combine_input_other"] = bool(seg.get("combine_input_other", False))

    tokens = seg.get("input_other_tokens", {"Other"})
    seg["input_other_tokens"] = set(map(str, tokens))

    seg["missing_token"] = str(seg.get("missing_token", "__MISSING__"))
    seg["hatch_other"] = str(seg.get("hatch_other", "////"))
    seg["other_edge_lw"] = float(seg.get("other_edge_lw", 0.0))
    if seg["other_edge_lw"] < 0:
        raise ValueError("segmenting.other_edge_lw muss >= 0 sein.")

    seg["other_selection_mode"] = str(seg.get("other_selection_mode", "min_share_to_other")).lower()
    if seg["other_selection_mode"] not in {"min_share_to_other", "top_n_per_parent"}:
        raise ValueError(
            "segmenting.other_selection_mode muss 'min_share_to_other' oder 'top_n_per_parent' sein."
        )

    seg["top_n_per_parent"] = int(seg.get("top_n_per_parent", 6))
    if seg["top_n_per_parent"] < 1:
        raise ValueError("segmenting.top_n_per_parent muss >= 1 sein.")

    seg["other_position_mode"] = str(seg.get("other_position_mode", "global_end")).lower()
    if seg["other_position_mode"] not in {"global_end", "after_parent"}:
        raise ValueError(
            "segmenting.other_position_mode muss 'global_end' oder 'after_parent' sein."
        )

    seg["min_share_to_other"] = float(seg.get("min_share_to_other", 0.0))
    if not (0.0 <= seg["min_share_to_other"] <= 1.0):
        raise ValueError("segmenting.min_share_to_other muss zwischen 0.0 und 1.0 liegen.")

    seg["sort_mode"] = str(seg.get("sort_mode", "parent")).lower()
    if seg["sort_mode"] not in {"parent", "child"}:
        raise ValueError("segmenting.sort_mode muss 'parent' oder 'child' sein.")

    seg["allow_negative_by_netting_into_other"] = bool(
        seg.get("allow_negative_by_netting_into_other", False)
    )

    # -------------------------
    # Plot
    # -------------------------
    p = out.get("plot") or {}
    out["plot"] = p

    p["fixed_width_in"] = float(p.get("fixed_width_in", 4.2))
    p["bars_per_inch"] = float(p.get("bars_per_inch", 2.65))
    p["min_height_in"] = float(p.get("min_height_in", 1.0))
    p["max_height_in"] = float(p.get("max_height_in", 120.0))

    if p["fixed_width_in"] <= 0:
        raise ValueError("plot.fixed_width_in muss > 0 sein.")
    if p["bars_per_inch"] <= 0:
        raise ValueError("plot.bars_per_inch muss > 0 sein.")
    if p["min_height_in"] <= 0:
        raise ValueError("plot.min_height_in muss > 0 sein.")
    if p["max_height_in"] < p["min_height_in"]:
        raise ValueError("plot.max_height_in muss >= plot.min_height_in sein.")

    p["render_dpi"] = int(p.get("render_dpi", 180))
    p["save_dpi"] = int(p.get("save_dpi", 450))

    p["bar_height"] = float(p.get("bar_height", 0.62))
    if not (0 < p["bar_height"] <= 1.0):
        raise ValueError("plot.bar_height muss > 0 und <= 1.0 sein.")

    p["left_label_wrap"] = int(p.get("left_label_wrap", 14))
    p["left_label_max_lines"] = int(p.get("left_label_max_lines", 2))
    if p["left_label_wrap"] < 1:
        raise ValueError("plot.left_label_wrap muss >= 1 sein.")
    if p["left_label_max_lines"] < 1:
        raise ValueError("plot.left_label_max_lines muss >= 1 sein.")

    p["x_margin_left"] = float(p.get("x_margin_left", 0.15))
    p["x_margin_right"] = float(p.get("x_margin_right", 0.98))
    p["y_margin_top"] = float(p.get("y_margin_top", 0.95))
    p["y_margin_bottom"] = float(p.get("y_margin_bottom", 0.02))

    for k in ["x_margin_left", "x_margin_right", "y_margin_top", "y_margin_bottom"]:
        if not (0.0 <= p[k] <= 1.0):
            raise ValueError(f"plot.{k} muss zwischen 0.0 und 1.0 liegen.")

    if p["x_margin_left"] >= p["x_margin_right"]:
        raise ValueError("plot.x_margin_left muss < plot.x_margin_right sein.")
    if p["y_margin_bottom"] >= p["y_margin_top"]:
        raise ValueError("plot.y_margin_bottom muss < plot.y_margin_top sein.")

    p["show_in_bar_labels"] = bool(p.get("show_in_bar_labels", True))
    p["label_min_share"] = float(p.get("label_min_share", 0.05))
    if not (0.0 <= p["label_min_share"] <= 1.0):
        raise ValueError("plot.label_min_share muss zwischen 0.0 und 1.0 liegen.")

    p["label_fontsize"] = int(p.get("label_fontsize", 7))
    if p["label_fontsize"] < 1:
        raise ValueError("plot.label_fontsize muss >= 1 sein.")

    p["segment_edge_lw"] = float(p.get("segment_edge_lw", 0.6))
    if p["segment_edge_lw"] < 0:
        raise ValueError("plot.segment_edge_lw muss >= 0 sein.")

    p["segment_edge_color"] = str(p.get("segment_edge_color", "white"))
    p["show_box_around_bar"] = bool(p.get("show_box_around_bar", False))

    p["show_plot_title"] = bool(p.get("show_plot_title", False))
    p["plot_title_size"] = int(p.get("plot_title_size", 11))
    if p["plot_title_size"] < 1:
        raise ValueError("plot.plot_title_size muss >= 1 sein.")

    # -------------------------
    # Legend
    # -------------------------
    leg = out.get("legend") or {}
    out["legend"] = leg

    leg["enabled"] = bool(leg.get("enabled", True))
    leg["group_mode"] = str(leg.get("group_mode", "parent_cluster")).lower()
    if leg["group_mode"] not in {"flat", "parent_cluster"}:
        raise ValueError("legend.group_mode muss 'flat' oder 'parent_cluster' sein.")

    leg["sort_by"] = str(leg.get("sort_by", "amount")).lower()
    if leg["sort_by"] not in {"amount", "share"}:
        raise ValueError("legend.sort_by muss 'amount' oder 'share' sein.")

    leg["font_size"] = int(leg.get("font_size", 5))
    leg["max_items"] = int(leg.get("max_items", 60))
    leg["ncol_flat"] = int(leg.get("ncol_flat", 6))

    if leg["font_size"] < 1:
        raise ValueError("legend.font_size muss >= 1 sein.")
    if leg["max_items"] < 1:
        raise ValueError("legend.max_items muss >= 1 sein.")
    if leg["ncol_flat"] < 1:
        raise ValueError("legend.ncol_flat muss >= 1 sein.")

    marker = leg.get("marker") or {}
    leg["marker"] = marker
    marker["shape"] = str(marker.get("shape", "rect")).lower()
    if marker["shape"] not in {"rect"}:
        raise ValueError("legend.marker.shape muss aktuell 'rect' sein.")

    marker["width_pt"] = float(marker.get("width_pt", 10.0))
    marker["height_pt"] = float(marker.get("height_pt", 6.0))
    marker["edgecolor"] = str(marker.get("edgecolor", "none"))
    marker["edgewidth"] = float(marker.get("edgewidth", 0.0))

    if marker["width_pt"] <= 0:
        raise ValueError("legend.marker.width_pt muss > 0 sein.")
    if marker["height_pt"] <= 0:
        raise ValueError("legend.marker.height_pt muss > 0 sein.")
    if marker["edgewidth"] < 0:
        raise ValueError("legend.marker.edgewidth muss >= 0 sein.")

    cluster = leg.get("cluster") or {}
    leg["cluster"] = cluster
    cluster["box_linestyle"] = str(cluster.get("box_linestyle", "--"))
    cluster["box_linewidth"] = float(cluster.get("box_linewidth", 0.5))
    cluster["gap_px"] = float(cluster.get("gap_px", 8))
    cluster["box_pad_px"] = float(cluster.get("box_pad_px", 2))
    cluster["title_gap_px"] = float(cluster.get("title_gap_px", 2))
    cluster["top_pad_px"] = float(cluster.get("top_pad_px", 2))

    for k in ["box_linewidth", "gap_px", "box_pad_px", "title_gap_px", "top_pad_px"]:
        if cluster[k] < 0:
            raise ValueError(f"legend.cluster.{k} muss >= 0 sein.")

    leg["save_dpi"] = int(leg.get("save_dpi", 450))
    leg["pad_inches"] = float(leg.get("pad_inches", 0.05))
    if leg["pad_inches"] < 0:
        raise ValueError("legend.pad_inches muss >= 0 sein.")

    # -------------------------
    # Excel
    # -------------------------
    e = out.get("excel") or {}
    out["excel"] = e

    e["enabled"] = bool(e.get("enabled", True))
    e["sheet_name"] = str(e.get("sheet_name", "HBAR"))
    e["col_offset"] = int(e.get("col_offset", 3))
    e["image_anchor_cell"] = str(e.get("image_anchor_cell", "D6"))
    e["legend_anchor_cell"] = str(e.get("legend_anchor_cell", "D35"))
    e["set_column_widths"] = bool(e.get("set_column_widths", True))

    e["display_dpi"] = int(e.get("display_dpi", 180))
    e["image_scale"] = float(e.get("image_scale", 0.5))
    e["legend_scale"] = float(e.get("legend_scale", 0.3))

    if e["col_offset"] < 0:
        raise ValueError("excel.col_offset muss >= 0 sein.")
    if e["image_scale"] <= 0:
        raise ValueError("excel.image_scale muss > 0 sein.")
    if e["legend_scale"] <= 0:
        raise ValueError("excel.legend_scale muss > 0 sein.")

    legend_max_width_px = e.get("legend_max_width_px", None)
    if legend_max_width_px is not None:
        legend_max_width_px = int(legend_max_width_px)
        if legend_max_width_px <= 0:
            raise ValueError("excel.legend_max_width_px muss > 0 sein oder None.")
    e["legend_max_width_px"] = legend_max_width_px

    return out


def clean_filename(s: str) -> str:
    s = str(s).strip().replace(" ", "_")
    return re.sub(r"[^\w\-_.]", "", s)


def compute_dynamic_figsize(cfg: Dict[str, Any], n_bars: int) -> Tuple[float, float]:
    """
    Horizontal bar chart => Breite fix, Höhe dynamisch
    """
    pcfg = cfg.get("plot", {}) or {}
    w = float(pcfg.get("fixed_width_in", 4.0))

    bars_per_inch = float(pcfg.get("bars_per_inch", 1.6))
    if bars_per_inch <= 0:
        raise ValueError("plot.bars_per_inch muss > 0 sein.")

    h = n_bars / bars_per_inch

    min_h = pcfg.get("min_height_in", None)
    max_h = pcfg.get("max_height_in", None)
    if min_h is not None:
        h = max(float(min_h), h)
    if max_h is not None:
        h = min(float(max_h), h)

    return w, h


def preprocess_input(df: pd.DataFrame, cfg: Dict[str, Any]) -> pd.DataFrame:
    d = df.copy()
    d.columns = d.columns.str.strip()

    # Multi-Filter aus Funktionssammlung
    d = apply_filters(d, cfg)

    inv = cfg["invoice_col"]
    mapping = str(cfg.get("invoice_mapping_mode") or "date").strip().lower()
    if mapping != "year":
        if inv not in d.columns:
            raise ValueError(f"invoice_col '{inv}' fehlt im Input.")
        d[inv] = pd.to_datetime(d[inv], errors="coerce", dayfirst=True)
    elif inv not in d.columns:
        raise ValueError(f"invoice_col '{inv}' fehlt im Input.")

    if str(cfg.get("calc_mode", "invoice")).lower() == "accrual":
        for c in [cfg["start_col"], cfg["end_col"]]:
            if c not in d.columns:
                raise ValueError(f"calc_mode='accrual' braucht Spalte '{c}' im Input.")
            d[c] = pd.to_datetime(d[c], errors="coerce", dayfirst=True)

    # value
    vcol = cfg["value_col"]
    if vcol not in d.columns:
        raise ValueError(f"value_col '{vcol}' fehlt im Input.")
    d["value"] = pd.to_numeric(d[vcol], errors="coerce")

    # FX optional
    if bool(cfg.get("apply_fx", False)):
        fx_col = cfg.get("fx_col")
        if fx_col not in d.columns:
            raise ValueError(f"apply_fx=True, aber fx_col '{fx_col}' fehlt im Input.")
        fx = pd.to_numeric(d[fx_col], errors="coerce")
        fx = fx.where(fx.notna() & (fx != 0), 1.0)
        d["value"] = d["value"] * fx

    # Drop minimal requirements
    req = [inv, "value"]
    d = d.dropna(subset=req).copy()

    if str(cfg.get("calc_mode", "invoice")).lower() == "accrual":
        d = d.dropna(subset=[cfg["start_col"], cfg["end_col"]]).copy()
        d = d[d[cfg["end_col"]] >= d[cfg["start_col"]]].copy()

    return d


def _clean_dim_series(s: pd.Series, missing_token: str) -> pd.Series:
    x = s.astype(str).str.strip()
    x = x.replace({"": missing_token, "nan": missing_token, "None": missing_token, "NaT": missing_token})
    return x


def wrap_label(s: str, width: int, max_lines: int) -> str:
    s = str(s)
    lines = textwrap.wrap(s, width=width)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".") + "..."
    return "\n".join(lines)


@dataclass
class SegmentRow:
    bar_key: str
    parent: str
    child: str
    seg_key: str        # (Parent, Child) oder flat
    amount: float
    share: float
    is_other: bool
    other_parent: str = ""


def build_segments_table(d: pd.DataFrame, cfg: Dict[str, Any]) -> Tuple[pd.DataFrame, List[str]]:
    """
    Baut eine Tabelle:
    | pro bar_key (links) -> Segmente (parent/child) mit amount + share.
    Wendet pro Balken:
      - missing -> other
      - combine_input_other -> other
      - top-N + other bucket
      - min_share_to_other
      - negative handling (optional: in other netto)
    Gibt außerdem eine "global key order" zurück (für stable colors / legend ordering).
    """
    dimcfg = cfg.get("dimensions", {}) or {}
    segcfg = cfg.get("segmenting", {}) or {}

    bar_col = str(dimcfg.get("bar_col"))
    parent_col = str(dimcfg.get("parent_col", "") or "")
    child_col = str(dimcfg.get("child_col", "") or "")

    if bar_col not in d.columns:
        raise ValueError(f"dimensions.bar_col '{bar_col}' fehlt im Input.")

    missing_token = str(segcfg.get("missing_token", "__MISSING__"))
    other_label = str(segcfg.get("other_label", "Other"))
    max_n = int(segcfg.get("max_segments_per_bar", 10))
    create_other_bucket = bool(segcfg.get("create_other_bucket", True))

    combine_input_other = bool(segcfg.get("combine_input_other", True))
    input_other_tokens: Set[str] = set(map(str, segcfg.get("input_other_tokens", {other_label})))

    other_selection_mode = str(segcfg.get("other_selection_mode", "min_share_to_other")).lower()
    other_position_mode = str(segcfg.get("other_position_mode", "global_end")).lower()
    top_n_per_parent = int(segcfg.get("top_n_per_parent", 6))

    min_share_to_other = float(segcfg.get("min_share_to_other", 0.0) or 0.0)
    sort_mode = str(segcfg.get("sort_mode", "value_desc")).lower()
    allow_negative_net = bool(segcfg.get("allow_negative_by_netting_into_other", True))

    tmp = d.copy()

    tmp["__bar__"] = _clean_dim_series(tmp[bar_col], missing_token)

    # parent/child bauen (falls nicht gegeben => flat)
    if parent_col and (parent_col in tmp.columns) and child_col and (child_col in tmp.columns):
        tmp["__parent__"] = _clean_dim_series(tmp[parent_col], missing_token)
        tmp["__child__"] = _clean_dim_series(tmp[child_col], missing_token)
        tmp["__seg_key__"] = tmp["__parent__"] + " | " + tmp["__child__"]
    elif child_col and (child_col in tmp.columns):
        tmp["__parent__"] = ""
        tmp["__child__"] = _clean_dim_series(tmp[child_col], missing_token)
        tmp["__seg_key__"] = tmp["__child__"]
    else:
        # fallback: child_col = parent_col = bar_col (sinnlos, aber robust)
        tmp["__parent__"] = ""
        tmp["__child__"] = _clean_dim_series(tmp[bar_col], missing_token)
        tmp["__seg_key__"] = tmp["__child__"]

    # Aggregation
    g = tmp.groupby(["__bar__", "__parent__", "__child__", "__seg_key__"], as_index=False)["amount"].sum()

    # -------------------------
    # Globale Reihenfolgen:
    # 1) Auswahl-Reihenfolge für Top-N
    # 2) Darstellungs-Reihenfolge für Plot
    # -------------------------
    g_global = (
        g.groupby(["__parent__", "__child__", "__seg_key__"], as_index=False)["amount"]
        .sum()
    )
    g_global["amount_for_sort"] = pd.to_numeric(g_global["amount"], errors="coerce").fillna(0.0)

    # Parent-Reihenfolge global nach Parent-Umsatz
    parent_totals = (
        g_global.groupby("__parent__", as_index=False)["amount_for_sort"]
        .sum()
        .sort_values("amount_for_sort", ascending=False)
        .reset_index(drop=True)
    )
    parent_order = parent_totals["__parent__"].astype(str).tolist()

    # Child-Reihenfolge global nach Child-Umsatz
    child_totals = (
        g_global.groupby(["__parent__", "__child__", "__seg_key__"], as_index=False)["amount_for_sort"]
        .sum()
        .sort_values("amount_for_sort", ascending=False)
        .reset_index(drop=True)
    )

    # Auswahl: IMMER nach größten Children insgesamt
    selection_order = child_totals["__seg_key__"].astype(str).tolist()
    selection_order_map = {k: i for i, k in enumerate(selection_order)}

    # Darstellung: je nach sort_mode
    if sort_mode == "parent":
        display_order: List[str] = []
        for p in parent_order:
            subp = child_totals[child_totals["__parent__"].astype(str) == str(p)].copy()
            for _, r in subp.iterrows():
                k = str(r["__seg_key__"])
                if k not in display_order:
                    display_order.append(k)
    else:  # sort_mode == "child"
        display_order = selection_order.copy()

    # -------------------------
    # NEW: BAR TOP-N + OTHER-BAR + SORTING
    # -------------------------
    barscfg = cfg.get("bars", {}) or {}

    bars_top_n = int(barscfg.get("top_n", 0) or 0)  # 0 => alle
    bars_create_other = bool(barscfg.get("create_other_bar", True))
    bars_other_label = str(barscfg.get("other_label", "Rest"))

    bars_sort_mode = str(barscfg.get("bar_order", "value_desc")).lower().strip()
    if bars_sort_mode not in {"value_desc", "value_asc"}:
        raise ValueError("bars.sort_mode muss einer von: value_desc, value_asc sein.")

    # Bar-Totals (net) berechnen
    bar_totals = (
        g.groupby("__bar__", as_index=False)["amount"]
        .sum()
        .rename(columns={"amount": "bar_amount"})
    )

    # Sortieren
    if bars_sort_mode.startswith("abs_"):
        bar_totals["_k"] = bar_totals["bar_amount"].abs()
    else:
        bar_totals["_k"] = bar_totals["bar_amount"]

    asc = bars_sort_mode.endswith("_asc")
    bar_totals = bar_totals.sort_values("_k", ascending=asc).drop(columns=["_k"]).reset_index(drop=True)

    # Max bars_top_n total bars; with Rest that means (top_n - 1) named + Rest
    n_all = len(bar_totals)
    if bars_top_n > 0 and n_all > bars_top_n:
        if bars_create_other:
            named_n = max(1, bars_top_n - 1)
            top_bar_keys = bar_totals.head(named_n)["__bar__"].tolist()
            rest_bar_keys = bar_totals.iloc[named_n:]["__bar__"].tolist()
        else:
            top_bar_keys = bar_totals.head(bars_top_n)["__bar__"].tolist()
            rest_bar_keys = []
    else:
        top_bar_keys = bar_totals["__bar__"].tolist()
        rest_bar_keys = []

    # Bar-Reihenfolge: Top (named) bars, optionally + Rest as last bar
    bar_iteration_order: List[str] = [str(x) for x in top_bar_keys]

    if bars_create_other and len(rest_bar_keys) > 0:
        bar_iteration_order.append(bars_other_label)

    # Per bar: top-N + other
    out_rows: List[SegmentRow] = []
    global_keys_in_order: List[str] = list(display_order)

    # -------------------------
    # NEW: iterate bars in configured order (Top-N + Other-Bar)
    # -------------------------
    for bar_key in bar_iteration_order:

        # sub bestimmen:
        if bars_create_other and (str(bar_key) == str(bars_other_label)):
            # Other-Bar: alles, was NICHT in top_bar_keys ist
            if len(rest_bar_keys) == 0:
                continue  # safety
            sub = g.loc[g["__bar__"].astype(str).isin([str(x) for x in rest_bar_keys])].copy()
            bar_key_out = str(bars_other_label)
        else:
            # Normaler Bar
            sub = g.loc[g["__bar__"].astype(str) == str(bar_key)].copy()
            bar_key_out = str(bar_key)

        if sub.empty:
            continue

        # total (net)
        total = float(sub["amount"].sum())
        if abs(total) < 1e-12:
            # degeneriert -> alles other 100%
            out_rows.append(SegmentRow(
                bar_key=bar_key_out, parent=other_label, child=other_label, seg_key=other_label,
                amount=0.0, share=1.0, is_other=True
            ))
            continue

        # Missing -> Other
        miss_mask = (sub["__child__"].astype(str) == missing_token) | (sub["__seg_key__"].astype(str) == missing_token)
        missing_amt = float(sub.loc[miss_mask, "amount"].sum())
        sub = sub.loc[~miss_mask].copy()

        # Input-other -> Other
        input_other_amt = 0.0
        if combine_input_other:
            m_other = sub[sub["__child__"].astype(str).isin(input_other_tokens) | sub["__seg_key__"].astype(str).isin(input_other_tokens)]
            input_other_amt = float(m_other["amount"].sum())
            sub = sub.loc[~m_other].copy()

        # Für die Top-N-Auswahl immer globale Child-Reihenfolge verwenden
        sub_sorted = sub.copy()
        sub_sorted["__sel_order__"] = sub_sorted["__seg_key__"].map(
            lambda k: selection_order_map.get(str(k), 10**9)
        )
        sub_sorted = sub_sorted.sort_values("__sel_order__", ascending=True).drop(columns=["__sel_order__"])

        display_order_map = {k: i for i, k in enumerate(display_order)}

        # -------------------------
        # FALL 1: Other global am Ende (wie bisher, erweitert)
        # -------------------------
        if other_position_mode == "global_end":
            if create_other_bucket:
                top_cap = max(1, max_n - 1)
            else:
                top_cap = max(1, max_n)

            top = sub_sorted.head(top_cap).copy()
            rest = sub_sorted.iloc[top_cap:].copy()

            # Auswahlregel A: min_share_to_other
            if create_other_bucket and other_selection_mode == "min_share_to_other" and min_share_to_other > 0:
                top["share_tmp"] = top["amount"] / total
                move_mask = top["share_tmp"].abs() <= min_share_to_other
                moved = top.loc[move_mask].copy()
                if not moved.empty:
                    rest = pd.concat([rest, moved.drop(columns=["share_tmp"])], ignore_index=True)
                    top = top.loc[~move_mask].copy()
                else:
                    top = top.drop(columns=["share_tmp"]).copy()

            # Auswahlregel B: top_n_per_parent
            elif create_other_bucket and other_selection_mode == "top_n_per_parent":
                top = top.copy()
                top["__parent_rank__"] = top.groupby("__parent__").cumcount() + 1
                move_mask = top["__parent_rank__"] > top_n_per_parent
                moved = top.loc[move_mask].drop(columns=["__parent_rank__"]).copy()
                top = top.loc[~move_mask].drop(columns=["__parent_rank__"]).copy()
                if not moved.empty:
                    rest = pd.concat([rest, moved], ignore_index=True)

            # Negative handling in top
            neg_top = top[top["amount"] < 0].copy()

            if not allow_negative_net:
                if not neg_top.empty:
                    details = ", ".join(
                        [f"{r['__seg_key__']} ({(r['amount']/total)*100:.1f}%)" for _, r in neg_top.iterrows()]
                    )
                    raise ValueError(
                        f"Negative Segmente in Top-N für '{bar_key_out}': {details}. "
                        f"Reduziere Segmente oder erlaube Netting."
                    )
            else:
                if not neg_top.empty:
                    top = top[top["amount"] >= 0].copy()
                    rest = pd.concat([rest, neg_top], ignore_index=True)

            # Darstellungssortierung
            top = top.copy()
            top["__disp_order__"] = top["__seg_key__"].map(
                lambda k: display_order_map.get(str(k), 10**9)
            )
            top = top.sort_values("__disp_order__", ascending=True).drop(columns=["__disp_order__"])

            # Emit top
            for _, r in top.iterrows():
                seg_key = str(r["__seg_key__"])
                sh = float(r["amount"] / total)
                out_rows.append(SegmentRow(
                    bar_key=bar_key_out,
                    parent=str(r["__parent__"]),
                    child=str(r["__child__"]),
                    seg_key=seg_key,
                    amount=float(r["amount"]),
                    share=sh,
                    is_other=False,
                    other_parent=""
                ))

            # Global other
            if create_other_bucket:
                other_amt = float(rest["amount"].sum() + missing_amt + input_other_amt)
                sh_o = float(other_amt / total)
                out_rows.append(SegmentRow(
                    bar_key=bar_key_out,
                    parent=other_label,
                    child=other_label,
                    seg_key=other_label,
                    amount=other_amt,
                    share=sh_o,
                    is_other=True,
                    other_parent=""
                ))

        # -------------------------
        # FALL 2: Other direkt nach jedem Parent
        # -------------------------
        elif other_position_mode == "after_parent":
            sub2 = sub_sorted.copy()

            # Missing/Input-other sofort global in Rest-Parent "__UNASSIGNED__"
            extra_other_amt = float(missing_amt + input_other_amt)

            # Top-Auswahl pro Parent
            top_parts = []
            rest_parts = []

            for p in parent_order:
                subp = sub2[sub2["__parent__"].astype(str) == str(p)].copy()
                if subp.empty:
                    continue

                subp["__sel_order_in_parent__"] = subp["__seg_key__"].map(
                    lambda k: selection_order_map.get(k, 10**9)
                )
                subp = subp.sort_values("__sel_order_in_parent__", ascending=True).drop(columns=["__sel_order_in_parent__"])

                if other_selection_mode == "top_n_per_parent":
                    keep = subp.head(top_n_per_parent).copy()
                    restp = subp.iloc[top_n_per_parent:].copy()

                elif other_selection_mode == "min_share_to_other":
                    keep = subp.copy()
                    keep["share_tmp"] = keep["amount"] / total
                    move_mask = keep["share_tmp"].abs() <= min_share_to_other
                    restp = keep.loc[move_mask].drop(columns=["share_tmp"]).copy()
                    keep = keep.loc[~move_mask].drop(columns=["share_tmp"]).copy()

                else:
                    keep = subp.copy()
                    restp = subp.iloc[0:0].copy()

                # Negative in keep -> in restp netten
                neg_keep = keep[keep["amount"] < 0].copy()

                if not allow_negative_net:
                    if not neg_keep.empty:
                        details = ", ".join(
                            [f"{r['__seg_key__']} ({(r['amount']/total)*100:.1f}%)" for _, r in neg_keep.iterrows()]
                        )
                        raise ValueError(
                            f"Negative Segmente in Parent '{p}' für '{bar_key_out}': {details}. "
                            f"Reduziere Segmente oder erlaube Netting."
                        )
                else:
                    if not neg_keep.empty:
                        keep = keep[keep["amount"] >= 0].copy()
                        restp = pd.concat([restp, neg_keep], ignore_index=True)

                keep["__disp_order__"] = keep["__seg_key__"].map(
                    lambda k: display_order_map.get(str(k), 10**9)
                )
                keep = keep.sort_values("__disp_order__", ascending=True).drop(columns=["__disp_order__"])

                top_parts.append(keep)
                rest_parts.append((str(p), restp))

            # Emit Parent für Parent
            for p in parent_order:
                keep = pd.concat(
                    [x for x in top_parts if not x.empty and str(x.iloc[0]["__parent__"]) == str(p)],
                    ignore_index=True
                ) if any((not x.empty and str(x.iloc[0]["__parent__"]) == str(p)) for x in top_parts) else pd.DataFrame()

                if not keep.empty:
                    for _, r in keep.iterrows():
                        seg_key = str(r["__seg_key__"])
                        sh = float(r["amount"] / total)
                        out_rows.append(SegmentRow(
                            bar_key=bar_key_out,
                            parent=str(r["__parent__"]),
                            child=str(r["__child__"]),
                            seg_key=seg_key,
                            amount=float(r["amount"]),
                            share=sh,
                            is_other=False,
                            other_parent=""
                        ))

                if create_other_bucket:
                    restp = next((rp for parent_name, rp in rest_parts if str(parent_name) == str(p)), pd.DataFrame())
                    rest_amt = float(restp["amount"].sum()) if not restp.empty else 0.0

                    if rest_amt != 0.0:
                        rest_seg_key = f"{p} | {other_label}"
                        sh_r = float(rest_amt / total)
                        out_rows.append(SegmentRow(
                            bar_key=bar_key_out,
                            parent=str(p),
                            child=other_label,
                            seg_key=rest_seg_key,
                            amount=rest_amt,
                            share=sh_r,
                            is_other=True,
                            other_parent=str(p)
                        ))

            # globales missing/input-other ohne Parent an ganzes Rest hängen
            if create_other_bucket and extra_other_amt != 0.0:
                sh_extra = float(extra_other_amt / total)
                out_rows.append(SegmentRow(
                    bar_key=bar_key_out,
                    parent=other_label,
                    child=other_label,
                    seg_key=other_label,
                    amount=extra_other_amt,
                    share=sh_extra,
                    is_other=True,
                    other_parent=""
                ))

    df_out = pd.DataFrame([r.__dict__ for r in out_rows])
    # sort segments inside each bar: share desc (damit visuell wie "Top links")
    # Bar-Reihenfolge fixieren (damit nicht alphabetisch sortiert wird)
    bar_order_map = {k: i for i, k in enumerate(bar_iteration_order)}
    df_out["__bar_order__"] = df_out["bar_key"].map(lambda x: bar_order_map.get(str(x), 10**9))

    # Segmente innerhalb Bar: erst Non-Other, dann Other, nach Share desc
    display_order_map = {k: i for i, k in enumerate(global_keys_in_order)}
    df_out["__seg_order__"] = df_out["seg_key"].map(lambda k: display_order_map.get(str(k), 10**9))

    if other_position_mode == "after_parent":
        # Reihenfolge exakt nach global_keys_in_order, ohne is_other künstlich ans Ende zu drücken
        df_out = (
            df_out.sort_values(["__bar_order__", "__seg_order__"], ascending=[True, True])
            .drop(columns=["__bar_order__", "__seg_order__"])
            .reset_index(drop=True)
        )
    else:
        # global_end bleibt wie bisher
        df_out = (
            df_out.sort_values(["__bar_order__", "is_other", "__seg_order__"], ascending=[True, True, True])
            .drop(columns=["__bar_order__", "__seg_order__"])
            .reset_index(drop=True)
        )

    if create_other_bucket:
        if other_position_mode == "global_end":
            if other_label not in global_keys_in_order:
                global_keys_in_order.append(other_label)
        elif other_position_mode == "after_parent":
            # Parent-spezifische Rest-Keys immer GANZ ans Ende ihrer Familie
            parent_rest_keys = {f"{p} | {other_label}" for p in parent_order}

            new_order = []

            for p in parent_order:
                # erst alle normalen Keys dieses Parents
                parent_normal_keys = [
                    k for k in global_keys_in_order
                    if (" | " in str(k))
                    and (str(k).split(" | ", 1)[0] == str(p))
                    and (str(k) != f"{p} | {other_label}")
                ]

                for k in parent_normal_keys:
                    if k not in new_order:
                        new_order.append(k)

                # danach genau EINMAL der Rest dieses Parents
                rest_k = f"{p} | {other_label}"
                if rest_k in parent_rest_keys and rest_k not in new_order:
                    new_order.append(rest_k)

            # Flat-/sonstige Keys, die nicht Parent|Child sind, hinten anhängen
            for k in global_keys_in_order:
                if k not in new_order and str(k) != other_label:
                    new_order.append(k)

            # globales Other ganz am Schluss
            if other_label not in new_order:
                new_order.append(other_label)

            global_keys_in_order = new_order

    return df_out, global_keys_in_order


def _parent_clustered_seg_keys(df_segments: pd.DataFrame, cfg: Dict[str, Any]) -> List[str]:
    """
    Global segment order: parents by total amount, children clustered under each parent.
    Rest/Other stuck at the end of each parent (or globally if parent == other_label).
    """
    if df_segments is None or df_segments.empty:
        return []

    segcfg = cfg.get("segmenting", {}) or {}
    other_label = str(segcfg.get("other_label", "Other"))
    other_position_mode = str(segcfg.get("other_position_mode", "global_end")).lower()

    g = (
        df_segments.groupby(["parent", "child", "seg_key"], as_index=False)["amount"]
        .sum()
    )
    g["amount"] = pd.to_numeric(g["amount"], errors="coerce").fillna(0.0)
    g["parent"] = g["parent"].astype(str)
    g["child"] = g["child"].astype(str)
    g["seg_key"] = g["seg_key"].astype(str)

    parent_totals = (
        g.groupby("parent", as_index=False)["amount"]
        .sum()
        .sort_values("amount", ascending=False)
    )
    parent_order = parent_totals["parent"].tolist()
    if other_label in parent_order:
        parent_order = [p for p in parent_order if p != other_label] + [other_label]

    keys: List[str] = []
    for p in parent_order:
        sub = g[g["parent"] == p].copy()
        sub["__is_other__"] = (sub["child"] == other_label) | (sub["seg_key"] == other_label)
        sub = sub.sort_values(["__is_other__", "amount"], ascending=[True, False])
        for k in sub["seg_key"].tolist():
            if k not in keys:
                keys.append(k)

    if other_position_mode == "global_end" and other_label in keys:
        keys = [k for k in keys if k != other_label] + [other_label]

    return keys


def build_color_map(cfg: Dict[str, Any], seg_keys_in_order: List[str], df_segments: pd.DataFrame) -> Dict[str, tuple]:
    """
    Verwendet pick_colors_hardcoded aus Funktionssammlung (dein Farbstandard).
    - Wenn Hierarchie aktiv: group_cols = [parent_col, child_col]
    - und keys müssen "Parent | Child" sein.
    - Other bekommt neutral (hellgrau) - wir zeichnen Other sowieso hatch + transparent.
    """
    dimcfg = cfg.get("dimensions", {}) or {}
    parent_col = str(dimcfg.get("parent_col", "") or "")
    child_col = str(dimcfg.get("child_col", "") or "")

    # Hierarchie aktiv, wenn wir auch wirklich Parent|Child Keys haben
    hierarchy_active = False
    if parent_col and child_col and any((" | " in k) for k in seg_keys_in_order):
        hierarchy_active = True

    # keys ohne "Other" für pick_colors
    segcfg = cfg.get("segmenting", {}) or {}
    other_label = str(segcfg.get("other_label", "Other"))
    other_position_mode = str(segcfg.get("other_position_mode", "global_end")).lower()

    keys_for_color = [k for k in seg_keys_in_order if str(k) != other_label]

    group_cols = [parent_col, child_col] if hierarchy_active else [child_col or "flat"]

    cmap = pick_colors_hardcoded(
        group_keys=keys_for_color,
        group_cols=group_cols,
        max_bubbles=max(36, len(keys_for_color)),
        max_parents=6,
        max_children_per_parent=6
    )

    # Globales Other: neutral
    cmap[other_label] = to_rgba("#FFFFFF")

    # Parent-spezifische Rest-Keys übernehmen Parent-Farbe
    if other_position_mode == "after_parent":
        parent_base_color = {}
        for k in keys_for_color:
            if " | " in str(k):
                p, c = str(k).split(" | ", 1)
                if c != other_label and p not in parent_base_color:
                    parent_base_color[p] = cmap[k]

        for p, col in parent_base_color.items():
            cmap[f"{p} | {other_label}"] = col

    return cmap


# ==========================
# Plot: Horizontal stacked bars
# ==========================
def best_text_color_for_bg(rgba) -> str:
    if rgba is None:
        return "white"
    r, g, b = float(rgba[0]), float(rgba[1]), float(rgba[2])

    def to_linear(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    R = to_linear(r)
    G = to_linear(g)
    B = to_linear(b)

    L = 0.2126 * R + 0.7152 * G + 0.0722 * B
    return "black" if L > 0.4 else "white"


def plot_hbar_breakdown(
    df_segments: pd.DataFrame,
    seg_keys_in_order: List[str],
    color_map: Dict[str, tuple],
    cfg: Dict[str, Any],
    period_label: str
) -> plt.Figure:
    pcfg = cfg.get("plot", {}) or {}
    dpi = int(pcfg.get("render_dpi", 180))

    # bars: unique order (as seen)
    bar_keys = df_segments["bar_key"].astype(str).unique().tolist()
    n_bars = len(bar_keys)

    fig_w, fig_h = compute_dynamic_figsize(cfg, n_bars=n_bars)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=dpi)

    # Margins — leave headroom for the period label above the axes
    fig.subplots_adjust(
        left=float(pcfg.get("x_margin_left", 0.30)),
        right=float(pcfg.get("x_margin_right", 0.98)),
        top=float(pcfg.get("y_margin_top", 0.82)),
        bottom=float(pcfg.get("y_margin_bottom", 0.02)),
    )

    bar_h = float(pcfg.get("bar_height", 0.62))
    edge_lw = float(pcfg.get("segment_edge_lw", 0.6))
    edge_col = str(pcfg.get("segment_edge_color", "white"))

    segcfg = cfg.get("segmenting", {}) or {}
    other_label = str(segcfg.get("other_label", "Other"))
    hatch = str(segcfg.get("hatch_other", "////"))
    other_edge_lw = float(segcfg.get("other_edge_lw", 0.8))

    show_labels = bool(pcfg.get("show_in_bar_labels", False))
    label_min = float(pcfg.get("label_min_share", 0.08))
    label_fs = int(pcfg.get("label_fontsize", 7))

    # y positions top->bottom like your screenshot
    y = np.arange(n_bars)[::-1]

    # Parent rank from global order (first occurrence of each parent) — once for all bars
    parent_rank: Dict[str, int] = {}
    for k in seg_keys_in_order:
        match = df_segments.loc[df_segments["seg_key"].astype(str) == str(k), "parent"]
        if len(match):
            p = str(match.iloc[0])
        elif " | " in str(k):
            p = str(k).split(" | ", 1)[0]
        else:
            p = str(k)
        if p not in parent_rank:
            parent_rank[p] = len(parent_rank)

    # For each bar: stack left→right with children clustered by parent
    for i, bar_key in enumerate(bar_keys):
        rowset = df_segments[df_segments["bar_key"].astype(str) == str(bar_key)].copy()

        order_map = {k: j for j, k in enumerate(seg_keys_in_order)}
        rowset["ord"] = rowset["seg_key"].map(lambda k: order_map.get(str(k), 10_000))
        rowset["parent_ord"] = rowset["parent"].astype(str).map(lambda p: parent_rank.get(p, 10_000))

        other_position_mode = str(segcfg.get("other_position_mode", "global_end")).lower()
        if other_position_mode == "after_parent":
            rowset = rowset.sort_values(["parent_ord", "ord"], ascending=[True, True])
        else:
            # Keep parent clusters intact; only push a global Other to the end
            rowset = rowset.sort_values(
                ["is_other", "parent_ord", "ord"], ascending=[True, True, True]
            )

        left = 0.0
        for _, r in rowset.iterrows():
            sh = float(r["share"])
            if sh <= 0:
                continue

            key = str(r["seg_key"])
            is_other = bool(r["is_other"])

            if is_other:
                other_position_mode = str(segcfg.get("other_position_mode", "global_end")).lower()

                # after_parent: gleicher Look wie bisher, aber Hatch/Farbe in Parent-Farbe
                if other_position_mode == "after_parent" and str(r.get("other_parent", "")) != "":
                    parent_rest_key = str(r["seg_key"])
                    hatch_color = color_map.get(parent_rest_key, (0, 0, 0, 1))
                    ax.barh(
                        y[i], sh,
                        height=bar_h,
                        left=left,
                        facecolor=(1, 1, 1, 0),     # transparent/weiß wie bisher
                        edgecolor=hatch_color,       # Hatch-/Randfarbe in Parent-Farbe
                        linewidth=other_edge_lw,     # DIREKT aus der Config
                        hatch=hatch
                    )
                else:
                    # global_end unverändert wie bisher
                    ax.barh(
                        y[i], sh,
                        height=bar_h,
                        left=left,
                        facecolor=(1, 1, 1, 0),
                        edgecolor="black",
                        linewidth=other_edge_lw,
                        hatch=hatch
                    )
            else:
                ax.barh(
                    y[i], sh,
                    height=bar_h,
                    left=left,
                    color=color_map.get(key),
                    edgecolor=edge_col,
                    linewidth=edge_lw
                )

            if show_labels and (sh >= label_min):
                txt = f"{sh*100:.0f}%"

                if is_other:
                    # "Aussparung"/Box für Lesbarkeit im Hatch
                    ax.text(
                        left + sh / 2,
                        y[i],
                        txt,
                        ha="center",
                        va="center",
                        fontsize=label_fs,
                        color="black",
                        clip_on=True,
                        bbox=dict(
                            facecolor="white",
                            edgecolor="none",
                            alpha=0.80,
                            boxstyle="round,pad=0.25"
                        ),
                        zorder=10
                    )
                else:
                    # normales Segment (wie vorher)
                    ax.text(
                        left + sh / 2,
                        y[i],
                        txt,
                        ha="center",
                        va="center",
                        fontsize=label_fs,
                        color=best_text_color_for_bg(color_map.get(key)),
                        clip_on=True,
                        zorder=10
                    )

            left += sh

    # Y labels
    wrap_w = int(pcfg.get("left_label_wrap", 16))
    max_lines = int(pcfg.get("left_label_max_lines", 2))
    y_fs = float(pcfg.get("left_label_fontsize", 5.5))
    ylabels = [wrap_label(k, width=wrap_w, max_lines=max_lines) for k in bar_keys]
    ax.set_yticks(y)
    ax.set_yticklabels(ylabels, fontproperties=FONT_PROP)
    # Labels only — no tick dashes (the tiny centered mark often comes from leftover ticks)
    ax.tick_params(axis="y", labelsize=y_fs, pad=4, length=0, width=0)
    ax.tick_params(axis="x", length=0, width=0, bottom=False, top=False)

    # X axis: 0..100%
    ax.set_xlim(0, 1.0)
    ax.set_xticks([])
    ax.set_xlabel("")
    ax.xaxis.set_ticks_position("none")
    ax.yaxis.set_ticks_position("none")

    # Clean spines (incl. top — avoids a short centered line under a clipped title)
    for side in ("top", "right", "bottom", "left"):
        ax.spines[side].set_visible(False)

    # Period label — same size as bar (y-axis) labels, heading colour
    if bool(pcfg.get("show_period_title", True)) and str(period_label or "").strip():
        ax.set_title("")  # clear any residual axes title
        title_fs = pcfg.get("period_title_fontsize", None)
        title_fs = float(y_fs if title_fs in (None, "") else title_fs)
        # Centered above the plot area (aligned with chart content, not full figure incl. y-labels)
        mid_x = 0.5 * (
            float(pcfg.get("x_margin_left", 0.30)) + float(pcfg.get("x_margin_right", 0.98))
        )
        fig.text(
            mid_x,
            0.97,
            str(period_label),
            ha="center",
            va="top",
            fontsize=title_fs,
            color=BRAND_BLUE,
            fontproperties=FONT_PROP,
            fontweight="normal",
            transform=fig.transFigure,
            clip_on=False,
        )
    elif bool(pcfg.get("show_plot_title", False)):
        company = str(cfg.get("company", "")).strip()
        title = str(cfg.get("table", "")).strip()
        prefix = str(cfg.get("period_label_prefix", "")).strip()
        parts = [p for p in [company, prefix, title, period_label] if p]
        ax.set_title(
            " | ".join(parts),
            fontsize=int(pcfg.get("plot_title_size", 11)),
            fontproperties=FONT_PROP,
            color=BRAND_BLUE,
        )

    return fig


# ==========================
# Legend PNG (Parent cluster + rectangle markers)
# ==========================
def _coerce_hex_color(c) -> str:
    if c is None:
        return "#999999"
    if isinstance(c, (tuple, list)):
        try:
            return to_hex(c, keep_alpha=False)
        except Exception:
            return "#999999"
    if isinstance(c, str):
        s = c.strip()
        if s.startswith("#"):
            return s
        if s.startswith("(") and s.endswith(")"):
            try:
                t = ast.literal_eval(s)
                if isinstance(t, (tuple, list)):
                    return to_hex(t, keep_alpha=False)
            except Exception:
                pass
        try:
            return to_hex(to_rgba(s), keep_alpha=False)
        except Exception:
            return "#999999"
    try:
        return to_hex(to_rgba(c), keep_alpha=False)
    except Exception:
        return "#999999"


def save_legend_png(
    df_segments: pd.DataFrame,
    color_map: Dict[str, tuple],
    cfg: Dict[str, Any],
    out_png: str
) -> Optional[str]:
    lcfg = cfg.get("legend", {}) or {}
    if not bool(lcfg.get("enabled", True)):
        return None

    segcfg = cfg.get("segmenting", {}) or {}
    other_label = str(segcfg.get("other_label", "Other"))

    # Build legend entries from df_segments:
    # - unique seg_key across ALL bars
    # - parent/child from first occurrence (for cluster)
    uniq = (
        df_segments[["seg_key", "parent", "child", "amount"]]
        .copy()
    )
    uniq = uniq.groupby(["seg_key", "parent", "child"], as_index=False)["amount"].sum()

    # Sorting
    sort_by = str(lcfg.get("sort_by", "amount")).lower()
    if sort_by == "share":
        # share ist bar-spezifisch; global sort_by share macht wenig Sinn -> fallback amount
        sort_by = "amount"
    uniq = uniq.sort_values("amount", ascending=False)

    max_items = int(lcfg.get("max_items", 60))
    uniq = uniq.head(max_items).reset_index(drop=True)

    # Determine if hierarchy keys exist
    group_mode = str(lcfg.get("group_mode", "parent_cluster")).lower()

    # Marker config
    mcfg = (lcfg.get("marker") or {})
    rect_w_pt = float(mcfg.get("width_pt", 10.0))
    rect_h_pt = float(mcfg.get("height_pt", 6.0))
    mec = str(mcfg.get("edgecolor", "none"))
    mew = float(mcfg.get("edgewidth", 0.0))

    font_size = int(lcfg.get("font_size", 5))
    dpi = int(lcfg.get("save_dpi", 450))
    pad_inches = float(lcfg.get("pad_inches", 0.05))

    # Flat legend
    if group_mode != "parent_cluster":
        ncol = int(lcfg.get("ncol_flat", 6))
        handles: List[Patch] = []
        labels: List[str] = []
        for _, r in uniq.iterrows():
            k = str(r["seg_key"])
            col = _coerce_hex_color(color_map.get(k))
            if k == other_label:
                # Other als Hatch-Proxy
                handles.append(Patch(facecolor="white", edgecolor="black", hatch=str(segcfg.get("hatch_other", "////"))))
            else:
                handles.append(Patch(facecolor=col, edgecolor=mec, linewidth=mew))
            labels.append(k)

        fig = plt.figure(figsize=(6, 0.6), dpi=dpi)
        ax = fig.add_subplot(111)
        ax.axis("off")
        ax.legend(
            handles=handles, labels=labels,
            loc="center",
            ncol=ncol,
            frameon=False,
            fontsize=font_size,
            handlelength=rect_w_pt / 10.0,
            handleheight=rect_h_pt / 10.0,
            handletextpad=0.35,
            columnspacing=0.8,
            borderaxespad=0.0,
        )
        fig.savefig(out_png, dpi=dpi, bbox_inches="tight", pad_inches=pad_inches, transparent=True)
        plt.close(fig)
        return out_png

    # Parent cluster: 1 Parent = 1 Spalte
    c = (lcfg.get("cluster") or {})
    box_ls = str(c.get("box_linestyle", "--"))
    box_lw = float(c.get("box_linewidth", 0.5))
    gap_px = float(c.get("gap_px", 8))
    box_pad_px = float(c.get("box_pad_px", 2))
    title_gap_px = float(c.get("title_gap_px", 2))
    top_pad_px = float(c.get("top_pad_px", 2))
    # Minimal visible gap so boxed clusters never look glued / overlapping
    gap_px = max(6.0, gap_px)

    # Parent order by total amount
    parent_order = (
        uniq.groupby("parent", as_index=False)["amount"]
        .sum()
        .sort_values("amount", ascending=False)["parent"]
        .astype(str).tolist()
    )

    parent_order = [p for p in parent_order if p != other_label] + (
        [other_label] if other_label in parent_order else []
    )

    blocks = []
    for p in parent_order:
        sub = uniq[uniq["parent"].astype(str) == str(p)].copy()
        sub["__is_other__"] = sub["child"].astype(str) == other_label
        sub = sub.sort_values(["__is_other__", "amount"], ascending=[True, False]).drop(columns=["__is_other__"])

        # parent color = first child's color
        p_first_key = str(sub.iloc[0]["seg_key"]) if len(sub) else "#999999"
        pcol = _coerce_hex_color(color_map.get(p_first_key, "#999999"))

        handles, labels = [], []
        other_position_mode = str(segcfg.get("other_position_mode", "global_end")).lower()
        hatch_other = str(segcfg.get("hatch_other", "////"))

        for _, r in sub.iterrows():
            k = str(r["seg_key"])
            child_label = str(r["child"]) if str(r["child"]) not in {"", "nan"} else k

            # globales Other unverändert schwarz schraffiert
            if k == other_label:
                handles.append(
                    Patch(
                        facecolor="white",
                        edgecolor="black",
                        hatch=hatch_other,
                        linewidth=max(mew, 0.6)
                    )
                )
                labels.append(other_label)

            # parent-spezifisches Other: schraffiert in Parent-Farbe
            elif other_position_mode == "after_parent" and child_label == other_label:
                col = _coerce_hex_color(color_map.get(k))
                handles.append(
                    Patch(
                        facecolor="white",
                        edgecolor=col,
                        hatch=hatch_other,
                        linewidth=max(mew, 0.6)
                    )
                )
                labels.append(other_label)
            else:
                col = _coerce_hex_color(color_map.get(k))
                handles.append(Patch(facecolor=col, edgecolor=mec, linewidth=mew))
                labels.append(child_label)

        blocks.append((str(p), pcol, handles, labels))

    n_parents = max(1, len(blocks))

    # 1) Probe-Figure
    fig = plt.figure(figsize=(12, 2.5), dpi=dpi)
    axes, legends = [], []

    for i, (p, pcol, handles, labels) in enumerate(blocks):
        ax = fig.add_axes([0.05 + i * 0.9 / n_parents, 0.1, 0.9 / n_parents, 0.85])
        ax.axis("off")
        leg = ax.legend(
            handles=handles,
            labels=labels,
            loc="upper left",
            bbox_to_anchor=(0.0, 1.0),
            frameon=False,
            fontsize=font_size,
            handlelength=rect_w_pt / 10.0,
            handleheight=rect_h_pt / 10.0,
            handletextpad=0.35,
            columnspacing=0.6,
            borderaxespad=0.0,
            labelspacing=0.25,
        )
        axes.append(ax)
        legends.append((leg, p, pcol))

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()

    draw_box = []
    widths, heights, title_hs = [], [], []
    for ax, (leg, p, pcol) in zip(axes, legends):
        bb = leg.get_window_extent(renderer=renderer)
        child_count = len(getattr(leg, "texts", []))
        has_cluster = child_count > 1
        draw_box.append(has_cluster)

        widths.append(bb.width + 2 * box_pad_px)
        heights.append(bb.height + 2 * box_pad_px)
        title_hs.append(((font_size + 2) * (dpi / 72.0) + title_gap_px) if has_cluster else 0.0)

    max_h = max(heights) if heights else 1.0
    title_h = max(title_hs) if title_hs else 0.0

    total_h_px = top_pad_px + title_h + max_h + 2
    total_w_px = sum(widths) + gap_px * (n_parents - 1) + 2

    # 2) Figure size exakt
    fig.set_size_inches(total_w_px / dpi, total_h_px / dpi)

    # 3) Axes platzieren (erster Pass)
    x_cursor_px = 1.0
    y0_px = 1.0
    content_h_px = total_h_px - y0_px
    for i, ax in enumerate(axes):
        w_px = widths[i]
        left = x_cursor_px / total_w_px
        width = w_px / total_w_px
        bottom = y0_px / total_h_px
        height = content_h_px / total_h_px
        ax.set_position([left, bottom, width, height])
        x_cursor_px += w_px + gap_px

    # 4) neu messen; Figurebreite an echte Boxbreiten + Gap anpassen (kein Overlap)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    measured: List[float] = []
    for ax, (leg, _p, _pcol) in zip(axes, legends):
        bb = leg.get_window_extent(renderer=renderer)
        measured.append(float(bb.width) + 2 * box_pad_px)

    total_w_px = sum(measured) + gap_px * max(0, n_parents - 1) + 4
    fig.set_size_inches(max(total_w_px, 1.0) / dpi, total_h_px / dpi)

    x_cursor_px = 1.0
    for i, ax in enumerate(axes):
        w_px = measured[i]
        left = x_cursor_px / total_w_px
        width_frac = w_px / total_w_px
        bottom = y0_px / total_h_px
        height_frac = content_h_px / total_h_px
        ax.set_position([left, bottom, width_frac, height_frac])
        x_cursor_px += w_px + gap_px

    # 5) final + Boxen / Parent Titles
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    inv_fig = fig.transFigure.inverted()

    for i, (ax, (leg, P, pcol)) in enumerate(zip(axes, legends)):
        if not draw_box[i]:
            continue

        bb = leg.get_window_extent(renderer=renderer)

        x0 = bb.x0 - box_pad_px
        y0 = bb.y0 - box_pad_px
        x1 = bb.x1 + box_pad_px
        y1 = bb.y1 + box_pad_px

        (fx0, fy0) = inv_fig.transform((x0, y0))
        (fx1, fy1) = inv_fig.transform((x1, y1))

        rect = Rectangle(
            (fx0, fy0),
            (fx1 - fx0),
            (fy1 - fy0),
            transform=fig.transFigure,
            fill=False,
            linestyle=box_ls,
            linewidth=box_lw,
            edgecolor=_coerce_hex_color(pcol),
        )
        rect.set_clip_on(False)
        fig.patches.append(rect)

        (tx, ty) = inv_fig.transform((x0, y1 + title_gap_px))
        fig.text(
            tx, ty,
            str(P),
            transform=fig.transFigure,
            ha="left",
            va="bottom",
            fontsize=font_size,
            color=_coerce_hex_color(pcol),
            fontproperties=FONT_PROP,
        )

    fig.savefig(out_png, dpi=dpi, bbox_inches="tight", pad_inches=pad_inches, transparent=True)
    plt.close(fig)
    return out_png


# ==========================
# Output paths
# ==========================
def build_output_paths(cfg: Dict[str, Any], period_label: str) -> Tuple[str, str]:
    out_dir = cfg.get("output_dir", ".")
    os.makedirs(out_dir, exist_ok=True)

    prefix = clean_filename(cfg.get("output_prefix", "hbar_breakdown"))
    mode = str(cfg.get("calc_mode", "invoice"))
    pm = str(cfg.get("period_mode", "FY"))
    y = int(cfg.get("current_year", 2024))
    m = int(cfg.get("current_month", 1))

    base = f"{prefix}_{clean_filename(period_label)}_{pm}_cy{str(y)[-2:]}_{m:02d}_{mode}"
    png_path = os.path.join(out_dir, base + ".png")
    xlsx_path = os.path.join(out_dir, base + ".xlsx")
    return png_path, xlsx_path


# ==========================
# Excel export (Plot + Legend)
# ==========================
def _write_hbar_sheet(
    wb,
    cfg: Dict[str, Any],
    plot_paths: List[str],
    legend_path: Optional[str],
    period_label: str,
    fig_size_in: Tuple[float, float],
    sheet_name: str,
) -> str:
    from funktionssammlung import replace_workbook_sheet

    excfg = cfg.get("excel", {}) or {}
    col_offset = int(excfg.get("col_offset", 3))
    set_widths = bool(excfg.get("set_column_widths", True))

    display_dpi = int(excfg.get("display_dpi", 180))
    image_scale = float(excfg.get("image_scale", 0.5))
    legend_scale = float(excfg.get("legend_scale", 0.45))
    legend_max_width_px = excfg.get("legend_max_width_px", None)

    fig_w, fig_h = fig_size_in
    img_w = int(float(fig_w) * display_dpi * image_scale)
    img_h = int(float(fig_h) * display_dpi * image_scale)
    approx_px_per_col = 7.0 * 14.0
    auto_step = max(1, int(np.ceil(float(img_w) / approx_px_per_col)))
    col_step = int(excfg["chart_col_step"]) if excfg.get("chart_col_step") not in (None, "") else auto_step
    start_col = 4  # D
    plot_row = 6
    # Place legend just under the chart (Excel row ≈ 20px displayed height).
    legend_row = plot_row + max(1, int(np.ceil(float(img_h) / 20.0))) + 1

    title = str(sheet_name or excfg.get("sheet_name") or "Horizontal bars")[:31]
    ws = replace_workbook_sheet(wb, title)

    brand = BRAND_BLUE.replace("#", "FF")
    title_font = Font(name=THEME.font_name, size=24, color=brand)
    sub_font = Font(name=THEME.font_name, size=12, color=brand)
    info_font = Font(name=THEME.font_name, size=9, color=brand, bold=True)

    ws["D1"].value = cfg.get("title", "")
    ws["D1"].font = title_font

    suffix = str(cfg.get("subtitle_suffix", "")).strip()
    suffix_txt = f" ({suffix})" if suffix else ""
    table_name = f"{cfg.get('table','')}{suffix_txt}"

    ws["D2"].value = f"{table_name} | {period_label}"
    ws["D2"].font = sub_font

    ws["D4"].value = f"{cfg.get('company','')} | {table_name}"
    ws["D4"].font = info_font

    fill_grey = PatternFill(fill_type="solid", fgColor="FFF3F1EF")
    for r in range(1, 200):
        for c in range(1, 1 + col_offset):
            ws.cell(row=r, column=c).fill = fill_grey

    fill_white = PatternFill(fill_type="solid", fgColor="FFFFFFFF")
    for r in range(1, 200):
        for c in range(1 + col_offset, 40):
            ws.cell(row=r, column=c).fill = fill_white

    if not plot_paths:
        ws.cell(row=plot_row, column=start_col).value = "(Plot-Bild nicht gefunden)"

    for i, path in enumerate(plot_paths):
        anchor = f"{get_column_letter(start_col + i * col_step)}{plot_row}"
        if path and os.path.exists(path):
            img = _xl_image_from_path(path)
            img.width = img_w
            img.height = img_h
            ws.add_image(img, anchor)
        else:
            ws[anchor].value = f"(Plot-Bild nicht gefunden: {path})"

    if legend_path and os.path.exists(legend_path):
        leg = _xl_image_from_path(legend_path)
        leg.width = int(leg.width * legend_scale)
        leg.height = int(leg.height * legend_scale)

        if legend_max_width_px is not None:
            legend_max_width_px_i = int(legend_max_width_px)
            if leg.width > legend_max_width_px_i:
                ratio = legend_max_width_px_i / leg.width
                leg.width = int(leg.width * ratio)
                leg.height = int(leg.height * ratio)

        leg_anchor = f"{get_column_letter(start_col)}{legend_row}"
        ws.add_image(leg, leg_anchor)

    if set_widths:
        for c in range(1, 40):
            ws.column_dimensions[get_column_letter(c)].width = 14
        ws.column_dimensions["A"].width = 3
        ws.column_dimensions["B"].width = 3
        ws.column_dimensions["C"].width = 3
        ws.column_dimensions["D"].width = 20

    ws.sheet_properties.outlinePr.summaryRight = True
    for col_letter in ["A", "B", "C"]:
        ws.column_dimensions[col_letter].outlineLevel = 1
        ws.column_dimensions[col_letter].hidden = True
    ws.column_dimensions["D"].collapsed = True
    return ws.title


def export_excel_with_images(
    cfg: Dict[str, Any],
    plot_png_path: Any,
    legend_png_path: Any,
    xlsx_path: str,
    period_label: str,
    fig_size_in: Tuple[float, float],
    wb=None,
    *,
    save: bool = True,
    sheet_name: Optional[str] = None,
) -> Optional[str]:
    excfg = cfg.get("excel", {}) or {}
    if not bool(excfg.get("enabled", True)):
        return None

    plot_paths = list(plot_png_path) if isinstance(plot_png_path, (list, tuple)) else [plot_png_path]
    if legend_png_path is None:
        legend_path: Optional[str] = None
    elif isinstance(legend_png_path, (list, tuple)):
        legend_path = next((p for p in legend_png_path if p), None)
    else:
        legend_path = legend_png_path

    target = sheet_name or excfg.get("sheet_name") or cfg.get("base_sheet_name") or "Horizontal bars"
    owns_wb = wb is None
    if owns_wb:
        wb = Workbook()
        default = wb.active
        if default is not None and default.title == "Sheet":
            wb.remove(default)

    written = _write_hbar_sheet(
        wb, cfg, plot_paths, legend_path, period_label, fig_size_in, str(target)
    )
    if save and xlsx_path:
        wb.save(xlsx_path)
    return written


def _compute_period_amount(
    d: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, cfg: Dict[str, Any]
) -> pd.Series:
    """Invoice date / accrual via compute_amount; year mapping supports period columns."""
    if (
        str(cfg.get("calc_mode")).lower() == "invoice"
        and str(cfg.get("invoice_mapping_mode") or "date").strip().lower() == "year"
    ):
        try:
            from funktionssammlung import parse_invoice_fy_year

            inv_y = parse_invoice_fy_year(d[cfg["invoice_col"]])
        except Exception:
            inv_y = pd.to_numeric(d[cfg["invoice_col"]], errors="coerce")
        amt = pd.to_numeric(d["value"], errors="coerce").fillna(0.0)
        return amt.where(inv_y == int(end.year), 0.0)
    return compute_amount(d, start, end, cfg, "value")


def _cleanup_temp_pngs(paths: List[Optional[str]]) -> None:
    for p in paths:
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except Exception as e:
                print("Warnung: Konnte temporäres PNG nicht löschen:", e)


def _render_hbar_assets(df: pd.DataFrame, cfg: Dict[str, Any]):
    cfg = normalize_config(cfg)
    d_base = preprocess_input(df, cfg)

    periods = get_comparison_periods(cfg)
    period_tables: List[Tuple[str, pd.DataFrame, List[str]]] = []
    for start, end, p_label in periods:
        d = d_base.copy()
        d["amount"] = _compute_period_amount(d, start, end, cfg)
        df_seg, keys = build_segments_table(d, cfg)
        period_tables.append((p_label, df_seg, keys))

    combined = pd.concat([t[1] for t in period_tables], ignore_index=True) if period_tables else pd.DataFrame()
    if combined.empty:
        raise ValueError("Keine Segmente für die Vergleichsperioden — prüfe Daten / Filter / Datum.")

    global_keys = _parent_clustered_seg_keys(combined, cfg)
    color_map = build_color_map(cfg, global_keys, combined)

    period_label = " | ".join(p for p, _, _ in period_tables)
    png_path, xlsx_path = build_output_paths(cfg, period_label)
    save_dpi = int((cfg.get("plot", {}) or {}).get("save_dpi", 450))

    plot_pngs: List[str] = []
    fig_sizes: List[Tuple[float, float]] = []
    for idx, (p_label, df_seg, _keys) in enumerate(period_tables):
        bar_keys = df_seg["bar_key"].astype(str).unique().tolist()
        fig_w, fig_h = compute_dynamic_figsize(cfg, n_bars=max(1, len(bar_keys)))
        fig_sizes.append((fig_w, fig_h))
        fig = plot_hbar_breakdown(df_seg, global_keys, color_map, cfg, period_label=p_label)
        plot_png = png_path.replace(".png", f"_{idx + 1}_{clean_filename(p_label)}_plot.png")
        fig.savefig(plot_png, dpi=save_dpi, bbox_inches=None)
        plt.close(fig)
        plot_pngs.append(plot_png)

    fig_w = max(w for w, _ in fig_sizes) if fig_sizes else 4.2
    fig_h = max(h for _, h in fig_sizes) if fig_sizes else 3.0

    legend_png = png_path.replace(".png", "_legend.png")
    legend_path_out = None
    if bool((cfg.get("legend", {}) or {}).get("enabled", True)):
        legend_path_out = save_legend_png(combined, color_map, cfg, legend_png)

    return cfg, period_label, plot_pngs, legend_path_out, xlsx_path, (fig_w, fig_h)


# ==========================
# MAIN runner
# ==========================
def run_hbar_breakdown(df: pd.DataFrame, cfg: Dict[str, Any]) -> Tuple[None, Optional[str]]:
    cfg, period_label, plot_pngs, legend_path_out, xlsx_path, fig_size = _render_hbar_assets(df, cfg)
    excel_out = None
    try:
        if bool((cfg.get("excel", {}) or {}).get("enabled", True)):
            export_excel_with_images(
                cfg=cfg,
                plot_png_path=plot_pngs,
                legend_png_path=legend_path_out,
                xlsx_path=xlsx_path,
                period_label=period_label,
                fig_size_in=fig_size,
            )
            excel_out = xlsx_path
    finally:
        _cleanup_temp_pngs(list(plot_pngs) + [legend_path_out])
    return None, excel_out


def run_hbar_breakdown_in_workbook(cfg: dict, wb) -> str:
    """Fast Track / session mode: write Horizontal bars into an open workbook."""
    file_path = str(cfg.get("file_path") or "").strip()
    sheet_name = str(cfg.get("sheet_name") or "").strip()
    if not file_path:
        raise ValueError("horizontal_bars_hierachy requires file_path")
    df = pd.read_excel(file_path, sheet_name=sheet_name or 0, engine="openpyxl")

    out_dir = str(cfg.get("output_file_path") or cfg.get("output_dir") or ".")
    cfg = {**cfg, "output_dir": out_dir, "output_prefix": cfg.get("output_prefix") or "hbar_breakdown"}
    excel_cfg = dict(cfg.get("excel") or {})
    excel_cfg.setdefault("sheet_name", cfg.get("base_sheet_name") or "Horizontal bars")
    excel_cfg["enabled"] = True
    cfg["excel"] = excel_cfg

    cfg, period_label, plot_pngs, legend_path_out, _xlsx, fig_size = _render_hbar_assets(df, cfg)
    sheet_title = str(
        (cfg.get("excel") or {}).get("sheet_name")
        or cfg.get("base_sheet_name")
        or "Horizontal bars"
    )
    try:
        from funktionssammlung import get_next_sheet_name_from_wb

        target = get_next_sheet_name_from_wb(wb, sheet_title)
    except Exception:
        target = sheet_title
        if target in wb.sheetnames:
            target = f"{target}_1"

    try:
        written = export_excel_with_images(
            cfg,
            plot_pngs,
            legend_path_out,
            xlsx_path="",
            period_label=period_label,
            fig_size_in=fig_size,
            wb=wb,
            save=False,
            sheet_name=target,
        )
        return str(written or target)
    finally:
        _cleanup_temp_pngs(list(plot_pngs) + [legend_path_out])


# ==========================
# usage
# ==========================
if __name__ == "__main__":
    _desktop = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Desktop")
    _src = os.path.join(_desktop, "Sales data", "SaaS-Sales.xlsx")
    _cfg = dict(CONFIG)
    _cfg.update(
        {
            "filters": {"enabled": False, "rules": []},
            "calc_mode": "accrual",
            "value_col": "Contract Value After Discount",
            "invoice_col": "Invoice Date",
            "start_col": "Contract Start Date",
            "end_col": "Contract End Date",
            "current_year": 2025,
            "current_month": 7,
            "fiscal_year_end_month": 12,
            "fiscal_year_end_day": 31,
            "dimensions": {
                "bar_col": "Entity",
                "parent_col": "Segment",
                "child_col": "Product Name",
            },
            "table": "Gross sales hierarchy",
            "subtitle_suffix": "by accrued amounts",
            "output_dir": _desktop if os.path.isdir(_desktop) else ".",
            "output_prefix": "hbar_entity_segment_product",
            "excel": {
                **(CONFIG.get("excel") or {}),
                "sheet_name": "Horizontal bars",
                "enabled": True,
            },
        }
    )
    if not os.path.isfile(_src):
        raise SystemExit(f"Demo file not found: {_src}")
    _df = pd.read_excel(_src, sheet_name="Database", engine="openpyxl")
    _, xlsx_path = run_hbar_breakdown(_df, _cfg)
    print("Excel gespeichert unter:", xlsx_path)
