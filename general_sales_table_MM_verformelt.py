import os
import sys
import json
from copy import copy
import re

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(BASE_DIR)
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")

# Repo root must be on path (Linux case-sensitive imports: funktionssammlung).
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from funktionssammlung import (
    apply_filters,
    safe_month_day_ts,
    fiscal_year_bounds,
    fdd_as_of_end,
    fdd_current_fy_end_year,
    fdd_as_of_is_fy_end,
    fdd_ytd_bounds,
    fdd_ltm_bounds,
    recognized_value,
    fy_label,
    ytd_label,
    ltm_label,
    to_kEUR,
    ensure_output_writable,
    build_output_file_path,
    ensure_source_sheet_in_output,
    refresh_source_sheet_from_df,
    source_range_ref,
    source_column_range_ref,
    build_formula_filter_branches,
    build_sumifs_formula_body,
    build_chunked_row_sum_formula,
    write_export_df_to_sheet,
    get_next_sheet_name_from_wb,
    ensure_text_filter_helper_columns_from_cfg_in_ws,
    add_accrual_col_in_ws,
    _get_sheet_header_map,
    parse_invoice_fy_year,
)

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

from gst_excel_theme import THEME  # noqa: E402 — Income Statement–aligned Excel theme

# ------------------------
# Config laden
# ------------------------
if len(sys.argv) < 2:
    raise ValueError("Bitte den Pfad zur Config-JSON als Argument übergeben.")

CONFIG_PATH = sys.argv[1]

if not os.path.exists(CONFIG_PATH):
    raise FileNotFoundError(f"Config-Datei nicht gefunden: {CONFIG_PATH}")

with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
    CONFIG = json.load(f)

# ------------------------
# Helpers / Validation
# ------------------------
# Validiert und normalisiert die Konfiguration und ergänzt Standardwerte.
def normalize_config(cfg: dict) -> dict:
    out = dict(cfg)

    required = [
        "file_path",
        "sheet_name",
        "output_file_path",
        "case_id",
        "calc_mode",
        "invoice_col",
        "value_cols",
        "group_cols",
        "current_year",
        "current_month",
        "fiscal_year_end_month",
        "fiscal_year_end_day",
        "profit_mode",
    ]
    for k in required:
        if k not in out or out[k] in (None, "", []):
            raise ValueError(f"CONFIG['{k}'] muss gesetzt sein.")

    if out["calc_mode"] not in {"invoice", "accrual"}:
        raise ValueError("CONFIG['calc_mode'] muss 'invoice' oder 'accrual' sein.")

    if out["calc_mode"] == "accrual":
        for k in ["start_col", "end_col"]:
            if k not in out or out[k] in (None, ""):
                raise ValueError(f"CONFIG['{k}'] muss bei calc_mode='accrual' gesetzt sein.")

    group_cols = list(out["group_cols"])
    if len(group_cols) < 1 or len(group_cols) > 3:
        raise ValueError("CONFIG['group_cols'] muss 1 bis 3 Spalten enthalten.")
    out["group_cols"] = group_cols

    out["profit_mode"] = str(out["profit_mode"]).strip().lower()
    if out["profit_mode"] not in {"profit", "cost"}:
        raise ValueError("CONFIG['profit_mode'] muss 'profit' oder 'cost' sein.")

    value_cols = dict(out["value_cols"])

    if "revenue" not in value_cols or value_cols["revenue"] in (None, ""):
        raise ValueError("Bei CONFIG['value_cols']['revenue'] muss gesetzt sein.")

    if out["profit_mode"] == "cost":
        if "cost" not in value_cols or value_cols["cost"] in (None, ""):
            raise ValueError("Bei profit_mode='cost' muss CONFIG['value_cols']['cost'] gesetzt sein.")
    elif out["profit_mode"] == "profit":
        if "profit" not in value_cols or value_cols["profit"] in (None, ""):
            raise ValueError("Bei profit_mode='profit' muss CONFIG['value_cols']['profit'] gesetzt sein.")

    out["value_cols"] = value_cols

    out.setdefault("run_id", "")
    out.setdefault("base_sheet_name", "hierarchy_report")

    out.setdefault("invoice_mapping_mode", "date")
    out["invoice_mapping_mode"] = str(out["invoice_mapping_mode"]).strip().lower()

    if out["invoice_mapping_mode"] not in {"year", "date"}:
        raise ValueError("CONFIG['invoice_mapping_mode'] muss 'year' oder 'date' sein.")

    if out["calc_mode"] == "accrual" and out["invoice_mapping_mode"] != "date":
        raise ValueError("CONFIG['invoice_mapping_mode']='year' ist nur bei calc_mode='invoice' unterstützt.")

    if out["invoice_mapping_mode"] == "year" and (out.get("show_ytd", False) or out.get("show_ltm", False)):
        raise ValueError("CONFIG['invoice_mapping_mode']='year' unterstützt nur FY-Perioden, nicht YTD/LTM.")

    out.setdefault("title", "")
    out.setdefault("table", "")
    out.setdefault("company", "")
    out.setdefault("subtitle_suffix", "")

    out.setdefault("show_ytd", False)
    out.setdefault("show_ltm", False)
    out.setdefault("show_ytd_delta", False)
    out.setdefault("show_ltm_delta", False)
    out.setdefault("show_ytd_cagr", False)
    out.setdefault("show_ltm_cagr", False)

    out.setdefault("show_revenue", True)
    out.setdefault("show_gp", False)
    out.setdefault("show_gm", False)
    out.setdefault("total_label", "Total")
    out.setdefault("missing_top_group_label", "Other")
    out.setdefault("sort_mode", "FY_LAST")
    out.setdefault("hide_empty_delta_cagr_for_gp_gm", True)
    out.setdefault("hatch_only_fy_columns", False)
    out.setdefault("first_fy", int(out["current_year"]) - 3)

    rep = dict(out.get("reported_numbers", {}) or {})

    for key in ["sales", "profit"]:
        sec = rep.get(key, {}) or {}

        if isinstance(sec, dict):
            fy_vals = list(sec.get("fy", []) or [])
            ytd_vals = list(sec.get("ytd", []) or [])
            ltm_vals = list(sec.get("ltm", []) or [])
        elif isinstance(sec, (list, tuple)):
            fy_vals = list(sec)
            ytd_vals = []
            ltm_vals = []
        else:
            fy_vals = []
            ytd_vals = []
            ltm_vals = []

        rep[key] = {
            "fy": fy_vals,
            "ytd": ytd_vals,
            "ltm": ltm_vals,
        }

    out["reported_numbers"] = rep

    if out["sort_mode"] not in {"FY_LAST", "YTD_LAST", "LTM_LAST", "ALPHABETICAL"}:
        raise ValueError("CONFIG['sort_mode'] muss 'FY_LAST', 'YTD_LAST', 'LTM_LAST' oder 'ALPHABETICAL' sein.")

    raw_filters = out.get("filters", {}) or {}
    if isinstance(raw_filters, list):
        filters = {"enabled": len(raw_filters) > 0, "rules": raw_filters}
    else:
        filters = dict(raw_filters)
    filters.setdefault("enabled", False)
    filters.setdefault("rules", [])
    out["filters"] = filters

    hcfg = dict(out.get("hierarchy_limits", {}) or {})
    for i in range(1, 4):
        key = f"level_{i}"
        lvl = dict(hcfg.get(key, {}) or {})
        lvl.setdefault("max_items", None)
        lvl.setdefault("other_label", f"Other L{i}")
        hcfg[key] = lvl
    out["hierarchy_limits"] = hcfg

    out.setdefault("missing_group_label", "(n/a)")

    fmt = dict(out.get("excel_formatting", {}) or {})
    fmt.setdefault("enabled", True)
    fmt.setdefault("row_offset", 4)
    # Ergänzt Defaults und prüft Voraussetzungen für den Formelmodus.
    out.setdefault("formula_mode", True)
    n_gc = len(out["group_cols"])
    if out["formula_mode"]:
        # Mindestens n_gc versteckte Schlüsselspalten; optional größerer col_offset aus Config.
        if "col_offset" in fmt and fmt["col_offset"] is not None and fmt["col_offset"] != "":
            try:
                co = int(fmt["col_offset"])
            except (TypeError, ValueError):
                co = n_gc
            fmt["col_offset"] = max(n_gc, co)
        else:
            fmt["col_offset"] = n_gc
    else:
        fmt.setdefault("col_offset", 3)

    widths = dict(fmt.get("column_widths", {}) or {})
    widths.setdefault("label", 17.0)
    widths.setdefault("fy", 6)
    widths.setdefault("ytd", 7)
    widths.setdefault("ltm", 7)
    widths.setdefault("delta", 15.5)
    widths.setdefault("cagr", 10.5)
    widths.setdefault("other_value", 6.0)
    fmt["column_widths"] = widths

    out["excel_formatting"] = fmt

    out["current_year"] = int(out["current_year"])
    out["current_month"] = int(out["current_month"])
    if not 1 <= out["current_month"] <= 12:
        raise ValueError("CONFIG['current_month'] muss 1..12 sein.")

    out["fiscal_year_end_month"] = int(out["fiscal_year_end_month"])
    out["fiscal_year_end_day"] = int(out["fiscal_year_end_day"])
    if not 1 <= out["fiscal_year_end_month"] <= 12:
        raise ValueError("CONFIG['fiscal_year_end_month'] muss 1..12 sein.")
    if not 1 <= out["fiscal_year_end_day"] <= 31:
        raise ValueError("CONFIG['fiscal_year_end_day'] muss 1..31 sein.")

    out["first_fy"] = int(out["first_fy"])
    if out["first_fy"] < 1900:
        raise ValueError("CONFIG['first_fy'] muss ein plausibles Geschäftsjahr sein.")

    if not out["show_revenue"] and not out["show_gp"] and not out["show_gm"]:
        raise ValueError("Mindestens eine Kennzahl muss angezeigt werden (Revenue/GP/GM).")

    return out


# Normalisiert Texte für einen robusten Periodenvergleich ohne Leerzeichen und Sonderzeichen.
def normalize_period_match_text(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(text or "").upper())


# Erzeugt zulässige Suchvarianten für ein Periodenlabel im Spaltennamen.
def build_period_match_variants(period_label: str) -> set[str]:
    raw = str(period_label or "").strip().upper()
    variants = {normalize_period_match_text(raw)}

    if raw.startswith("FY") and raw.endswith("A"):
        variants.add(normalize_period_match_text(raw[:-1]))  # z. B. FY25 statt FY25A

    return {v for v in variants if v}


# Erkennt, ob ein Quellspaltenname genau einer Report-Periode zugeordnet werden kann.
def infer_single_period_from_colname(col_name: str, period_defs: dict) -> set[str] | None:
    normalized_col = normalize_period_match_text(col_name)
    if not normalized_col:
        return None

    matches = []

    for period_label in period_defs["period_map"].keys():
        variants = build_period_match_variants(period_label)
        if any(v in normalized_col for v in variants):
            matches.append(period_label)

    matches = list(dict.fromkeys(matches))

    if len(matches) == 1:
        return {matches[0]}

    return None


# Liefert die fachlich gültigen Perioden einer Kennzahl auf Basis ihrer Quellspaltennamen.
def get_valid_periods_for_metric(metric: str, cfg: dict, period_defs: dict) -> set[str] | None:
    value_cols = dict(cfg.get("value_cols", {}) or {})

    if metric == "revenue":
        return infer_single_period_from_colname(value_cols.get("revenue"), period_defs)

    if metric == "gp":
        if cfg["profit_mode"] == "profit":
            return infer_single_period_from_colname(value_cols.get("profit"), period_defs)

        if cfg["profit_mode"] == "cost":
            rev_periods = infer_single_period_from_colname(value_cols.get("revenue"), period_defs)
            cost_periods = infer_single_period_from_colname(value_cols.get("cost"), period_defs)

            if rev_periods is None and cost_periods is None:
                return None
            if rev_periods is None:
                return cost_periods
            if cost_periods is None:
                return rev_periods

            overlap = rev_periods & cost_periods
            if not overlap:
                raise ValueError(
                    "Automatische Periodenerkennung ist widersprüchlich: "
                    "Revenue- und Cost-Spalte verweisen auf unterschiedliche Einzelperioden."
                )

            return overlap

    return None


# Setzt fachlich unzulässige Periodenspalten auf NaN, damit später n/a und Schraffur greifen.
def apply_metric_period_restrictions(
    wide_df: pd.DataFrame,
    metric: str,
    cfg: dict,
    period_defs: dict
) -> pd.DataFrame:
    out = wide_df.copy()
    valid_periods = get_valid_periods_for_metric(metric, cfg, period_defs)

    if valid_periods is None:
        return out

    for p in period_defs["period_map"].keys():
        if p in out.columns and p not in valid_periods:
            out[p] = np.nan

    return out


# Bau delta label
def build_delta_display_label(newer_label: str, older_label: str) -> str:
    return f"Δ {newer_label}-{older_label}"


# Wandelt einen einzelnen Reported-Wert robust in eine Zahl oder NaN um.
def coerce_reported_number(value):
    return pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]


# Ordnet die Reported-Werte aus der Config positionsbasiert den aktuell angezeigten Perioden zu.
def build_reported_value_map(section_cfg: dict, period_defs: dict, cfg: dict) -> dict:
    out = {}

    section_cfg = dict(section_cfg or {})

    fy_vals = list(section_cfg.get("fy", []) or [])
    for i, label in enumerate(period_defs["fy_labels"]):
        out[label] = coerce_reported_number(fy_vals[i]) if i < len(fy_vals) else np.nan

    if cfg.get("show_ytd", False):
        ytd_vals = list(section_cfg.get("ytd", []) or [])
        for i, label in enumerate(period_defs["ytd_labels"]):
            out[label] = coerce_reported_number(ytd_vals[i]) if i < len(ytd_vals) else np.nan

    if cfg.get("show_ltm", False):
        ltm_vals = list(section_cfg.get("ltm", []) or [])
        for i, label in enumerate(period_defs["ltm_labels"]):
            out[label] = coerce_reported_number(ltm_vals[i]) if i < len(ltm_vals) else np.nan

    return out


# Baut die Beschriftung der Reported-Zeile abhängig von den aktiven Blöcken.
def build_reported_label(cfg: dict) -> str:
    show_revenue = bool(cfg.get("show_revenue", True))
    show_gp = bool(cfg.get("show_gp", False))

    if show_revenue and show_gp:
        return "Reported gross sales / GP"
    if show_revenue:
        return "Reported gross sales"
    if show_gp:
        return "Reported GP"

    return "Reported"


# Prüft, ob ein Excel-Zellwert für die Schraffur als fachlich leer gilt.
def is_hatch_empty_value(value) -> bool:
    if value is None:
        return True

    if isinstance(value, str):
        s = value.strip().lower()
        return s in {"", "n/a"}

    if isinstance(value, (int, float, np.integer, np.floating)):
        if pd.isna(value):
            return True
        return float(value) == 0.0

    return False


# Baut die beiden Zusatzzeilen für Recon. difference und Reported zwischen Total und KPI-Bereich.
def build_reported_rows(period_defs: dict, cfg: dict, blocks: list[tuple[str, str]]) -> pd.DataFrame:
    reported_row = {
        "level": 0,
        "_ui_level": 0,
        "key": "__REPORTED__",
        "parent_key": "",
        "label": build_reported_label(cfg),
        "row_type": "reported",
        "_sort_value": np.nan,
        "_display_order": 10**9 + 20,
    }

    recon_row = {
        "level": 0,
        "_ui_level": 0,
        "key": "__RECON__",
        "parent_key": "",
        "label": "Recon. difference",
        "row_type": "recon",
        "_sort_value": np.nan,
        "_display_order": 10**9 + 10,
    }

    sales_map = build_reported_value_map(cfg.get("reported_numbers", {}).get("sales", {}), period_defs, cfg)
    profit_map = build_reported_value_map(cfg.get("reported_numbers", {}).get("profit", {}), period_defs, cfg)

    for block_name, metric_kind in blocks:
        if block_name == "Revenue":
            source_map = sales_map
        elif block_name == "GP":
            source_map = profit_map
        else:
            source_map = {}

        for p in period_defs["period_map"].keys():
            reported_row[f"{block_name}__{p}"] = source_map.get(p, np.nan)
            recon_row[f"{block_name}__{p}"] = np.nan

    return pd.DataFrame([recon_row, reported_row])


# Baut ein Anzeige-Label für Delta-Spalten.
def build_period_definitions(cfg: dict) -> dict:
    cfg = normalize_config(cfg)

    CY = int(cfg["current_year"])
    m = int(cfg["current_month"])
    fy_end_m = int(cfg["fiscal_year_end_month"])
    fy_end_d = int(cfg["fiscal_year_end_day"])
    first_fy = int(cfg["first_fy"])

    as_of_end = fdd_as_of_end(CY, m)
    current_fy_end_year = fdd_current_fy_end_year(as_of_end, fy_end_m, fy_end_d)
    as_of_is_fy_end = fdd_as_of_is_fy_end(as_of_end, fy_end_m, fy_end_d)

    latest_closed_fy_end_year = current_fy_end_year if as_of_is_fy_end else current_fy_end_year - 1

    if first_fy > latest_closed_fy_end_year:
        raise ValueError(
            f"CONFIG['first_fy']={first_fy} liegt nach dem letzten abgeschlossenen FY "
            f"({latest_closed_fy_end_year})."
        )

    periods = {}

    fy_years = list(range(first_fy, latest_closed_fy_end_year + 1))
    fy_labels = [fy_label(y) for y in fy_years]

    for y in fy_years:
        s, e = fiscal_year_bounds(y, fy_end_m, fy_end_d)
        periods[fy_label(y)] = (s, e)

    fy_delta_calc_label = None
    fy_delta_display_label = None
    fy_cagr_label = None

    if len(fy_labels) >= 2:
        fy_delta_calc_label = f"Δ {fy_labels[-1]} - {fy_labels[-2]}"
        fy_delta_display_label = build_delta_display_label(fy_labels[-1], fy_labels[-2])
        fy_cagr_label = f"CAGR {str(fy_labels[0])[-3:-1]}A-{str(fy_labels[-1])[-3:-1]}A"

    out = {
        "as_of_end": as_of_end,
        "current_fy_end_year": current_fy_end_year,
        "latest_closed_fy_end_year": latest_closed_fy_end_year,
        "as_of_is_fy_end": as_of_is_fy_end,
        "period_map": periods,
        "fy_labels": fy_labels,
        "fy_delta_calc_label": fy_delta_calc_label,
        "fy_delta_display_label": fy_delta_display_label,
        "fy_cagr_label": fy_cagr_label,
        "ytd_labels": [],
        "ltm_labels": [],
        "ytd_delta_calc_label": None,
        "ltm_delta_calc_label": None,
        "ytd_delta_display_label": None,
        "ltm_delta_display_label": None,
        "ytd_cagr_label": None,
        "ltm_cagr_label": None,
    }

    if cfg.get("show_ytd", False):
        end_cy = as_of_end
        end_py = fdd_as_of_end(end_cy.year - 1, end_cy.month)

        start_fy_cy, _ = fdd_ytd_bounds(end_cy, fy_end_m, fy_end_d)
        start_fy_py, _ = fdd_ytd_bounds(end_py, fy_end_m, fy_end_d)

        y11 = ytd_label(end_py.year)
        y12 = ytd_label(end_cy.year)

        periods[y11] = (start_fy_py, end_py)
        periods[y12] = (start_fy_cy, end_cy)

        out["ytd_labels"] = [y11, y12]
        out["ytd_delta_calc_label"] = f"Δ {y12} - {y11}"
        out["ytd_delta_display_label"] = build_delta_display_label(y12, y11)
        out["ytd_cagr_label"] = f"CAGR YTD {str(y11)[-3:-1]}A-{str(y12)[-3:-1]}A"

    if cfg.get("show_ltm", False):
        end_cy = as_of_end
        end_py = fdd_as_of_end(end_cy.year - 1, end_cy.month)

        start_cy, _ = fdd_ltm_bounds(end_cy)
        start_py, _ = fdd_ltm_bounds(end_py)

        l11 = ltm_label(end_py.year)
        l12 = ltm_label(end_cy.year)

        periods[l11] = (start_py, end_py)
        periods[l12] = (start_cy, end_cy)

        out["ltm_labels"] = [l11, l12]
        out["ltm_delta_calc_label"] = f"Δ {l12} - {l11}"
        out["ltm_delta_display_label"] = build_delta_display_label(l12, l11)
        out["ltm_cagr_label"] = f"CAGR LTM {str(l11)[-3:-1]}A-{str(l12)[-3:-1]}A"

    return out


def block_to_logical_metric(block_name: str) -> str:
    mapping = {
        "Revenue": "revenue",
        "GP": "gp",
        "GM": "gm",
    }
    return mapping[block_name]


def get_metric_valid_periods(metric: str, cfg: dict, period_defs: dict) -> set[str] | None:
    if metric == "gm":
        return get_valid_periods_for_metric("gp", cfg, period_defs)
    return get_valid_periods_for_metric(metric, cfg, period_defs)


def get_displayable_section_labels(metric: str, section: str, cfg: dict, period_defs: dict) -> list[str]:
    if section == "fy":
        base_labels = list(period_defs["fy_labels"])
    elif section == "ytd":
        base_labels = list(period_defs["ytd_labels"])
    elif section == "ltm":
        base_labels = list(period_defs["ltm_labels"])
    else:
        raise ValueError(f"Unbekannte Section: {section}")

    valid_periods = get_metric_valid_periods(metric, cfg, period_defs)
    if valid_periods is None:
        return base_labels

    return [lbl for lbl in base_labels if lbl in valid_periods]


# Baut korrekte CAGR labels
def get_cagr_display_text_for_block(
    ws,
    block_name: str,
    raw_label: str,
    export_headers: dict,
    data_start_row: int,
    table_bottom_row: int,
    tech_cols: dict,
    period_defs: dict,
    cfg: dict,
) -> str:
    if period_defs.get("fy_cagr_label") and raw_label == period_defs["fy_cagr_label"]:
        candidate_labels = list(period_defs["fy_labels"])
        prefix = ""
    elif cfg.get("show_ytd", False) and period_defs.get("ytd_cagr_label") and raw_label == period_defs["ytd_cagr_label"]:
        candidate_labels = list(period_defs["ytd_labels"])
        prefix = "YTD "
    elif cfg.get("show_ltm", False) and period_defs.get("ltm_cagr_label") and raw_label == period_defs["ltm_cagr_label"]:
        candidate_labels = list(period_defs["ltm_labels"])
        prefix = "LTM "
    else:
        return raw_label.replace("CAGR ", "", 1)

    header_to_col = {
        header: col_idx
        for col_idx, header in export_headers.items()
        if header not in (None, "")
    }

    decision_excluded_row_types = {"kpi", "kpi_section", "reported", "recon"}
    usable_labels = []

    for lbl in candidate_labels:
        col_idx = header_to_col.get(f"{block_name}__{lbl}")
        if not col_idx:
            continue

        values = []
        for r in range(data_start_row, table_bottom_row + 1):
            row_type = str(ws.cell(row=r, column=tech_cols["row_type"]).value or "").strip().lower()
            if row_type in decision_excluded_row_types:
                continue
            values.append(ws.cell(row=r, column=col_idx).value)

        if len(values) > 0 and not all(is_hatch_empty_value(v) for v in values):
            usable_labels.append(lbl)

    if len(usable_labels) >= 2:
        return f"{prefix}{str(usable_labels[0])[-3:-1]}A-{str(usable_labels[-1])[-3:-1]}A"

    return raw_label.replace("CAGR ", "", 1)


def compute_dynamic_cagr_from_values(values: list) -> float:
    vals = pd.to_numeric(pd.Series(values), errors="coerce").tolist()

    first_idx = next((i for i, v in enumerate(vals) if pd.notna(v) and v > 0), None)
    last_idx = next((i for i in range(len(vals) - 1, -1, -1) if pd.notna(vals[i]) and vals[i] >= 0), None)

    if first_idx is None or last_idx is None or last_idx <= first_idx:
        return np.nan

    start_val = vals[first_idx]
    end_val = vals[last_idx]
    years = last_idx - first_idx

    if start_val <= 0 or end_val < 0:
        return np.nan

    return (end_val / start_val) ** (1 / years) - 1


def compute_dynamic_cagr_series(df: pd.DataFrame, ordered_labels: list[str]) -> pd.Series:
    labels = [lbl for lbl in ordered_labels if lbl in df.columns]

    if len(labels) < 2:
        return pd.Series(np.nan, index=df.index, dtype="float64")

    arr = np.column_stack([
        pd.to_numeric(df[lbl], errors="coerce").to_numpy(dtype=float)
        for lbl in labels
    ])

    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)

    n_rows, n_cols = arr.shape

    start_ok = np.isfinite(arr) & (arr > 0)
    end_ok = np.isfinite(arr) & (arr >= 0)

    has_start = start_ok.any(axis=1)
    has_end = end_ok.any(axis=1)

    first_pos = start_ok.argmax(axis=1)
    last_pos = n_cols - 1 - end_ok[:, ::-1].argmax(axis=1)

    span = last_pos - first_pos

    result = np.full(n_rows, np.nan, dtype=float)
    valid = has_start & has_end & (span > 0)

    if valid.any():
        row_idx = np.arange(n_rows)[valid]
        start_vals = arr[row_idx, first_pos[valid]]
        end_vals = arr[row_idx, last_pos[valid]]
        years = span[valid].astype(float)

        result[valid] = np.where(
            (start_vals > 0) & (end_vals >= 0),
            (end_vals / start_vals) ** (1 / years) - 1,
            np.nan
        )

    return pd.Series(result, index=df.index, dtype="float64")


# ------------------------
# Input / Aggregation
# ------------------------
# Bereinigt das Input-DataFrame, prüft Pflichtspalten und erzeugt Standardspalten.
def preprocess_input(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    cfg = normalize_config(cfg)

    d = df.copy()
    d.columns = d.columns.astype(str).str.replace("\u00a0", " ", regex=False).str.strip()

    d = apply_filters(d, cfg)

    needed_cols = [cfg["invoice_col"], cfg["value_cols"]["revenue"]] + cfg["group_cols"]

    if cfg["profit_mode"] == "cost":
        needed_cols += [cfg["value_cols"]["cost"]]
    else:
        needed_cols += [cfg["value_cols"]["profit"]]

    if cfg["calc_mode"] == "accrual":
        needed_cols += [cfg["start_col"], cfg["end_col"]]

    for c in needed_cols:
        if c not in d.columns:
            raise ValueError(f"Spalte '{c}' fehlt im Input.")

    date_cols = []
    if cfg["calc_mode"] == "invoice" and cfg["invoice_mapping_mode"] == "date":
        date_cols.append(cfg["invoice_col"])

    if cfg["calc_mode"] == "accrual":
        # invoice_col muss datetime sein (recognized_value vergleicht mit period_start/end)
        date_cols += [cfg["invoice_col"], cfg["start_col"], cfg["end_col"]]

    for c in date_cols:
        d[c] = pd.to_datetime(d[c], errors="coerce", dayfirst=True)

    d["revenue"] = pd.to_numeric(d[cfg["value_cols"]["revenue"]], errors="coerce").fillna(0.0)

    if cfg["profit_mode"] == "cost":
        d["cost"] = pd.to_numeric(d[cfg["value_cols"]["cost"]], errors="coerce").fillna(0.0)
        d["gp"] = d["revenue"] - d["cost"]
    else:
        d["gp"] = pd.to_numeric(d[cfg["value_cols"]["profit"]], errors="coerce").fillna(0.0)
        d["cost"] = d["revenue"] - d["gp"]

    missing_group_label = str(cfg.get("missing_group_label", "(n/a)")).strip() or "(n/a)"
    missing_top_group_label = str(cfg.get("missing_top_group_label", "Other")).strip() or "Other"

    for i, c in enumerate(cfg["group_cols"], start=1):
        s = d[c].astype(str).replace("nan", "", regex=False).str.strip()
        s = s.replace("", np.nan)

        if i == 1:
            d[f"group_{i}"] = s.fillna(missing_top_group_label)
        else:
            d[f"group_{i}"] = s.fillna(missing_group_label)

    if cfg["calc_mode"] == "invoice":
        if cfg["invoice_mapping_mode"] == "date":
            d = d.dropna(subset=[cfg["invoice_col"]]).copy()
        else:
            d["_invoice_fy_year"] = parse_invoice_fy_year(d[cfg["invoice_col"]])
            d = d.dropna(subset=["_invoice_fy_year"]).copy()
    else:
        d = d.dropna(subset=[cfg["invoice_col"]]).copy()

    if cfg["calc_mode"] == "accrual":
        d = d.dropna(subset=[cfg["start_col"], cfg["end_col"]]).copy()
        d = d[d[cfg["end_col"]] >= d[cfg["start_col"]]].copy()

    return d


# Aggregiert eine einzelne Kennzahl für eine einzelne Periode über alle Hierarchieebenen.
def aggregate_period_metric(
    d: pd.DataFrame,
    period_start: pd.Timestamp,
    period_end: pd.Timestamp,
    cfg: dict,
    metric: str
) -> pd.DataFrame:
    if metric not in {"revenue", "gp"}:
        raise ValueError("aggregate_period_metric unterstützt nur 'revenue' und 'gp'.")

    tmp = d.copy()

    if cfg["calc_mode"] == "invoice":
        if cfg["invoice_mapping_mode"] == "date":
            mask = (tmp[cfg["invoice_col"]] >= period_start) & (tmp[cfg["invoice_col"]] <= period_end)
        else:
            target_fy_year = int(period_end.year)
            mask = pd.to_numeric(tmp["_invoice_fy_year"], errors="coerce") == target_fy_year
        tmp["amount"] = pd.to_numeric(tmp[metric], errors="coerce").where(mask, 0.0).fillna(0.0)
    else:
        tmp["value"] = pd.to_numeric(tmp[metric], errors="coerce").fillna(0.0)
        tmp["amount"] = recognized_value(tmp, period_start, period_end, cfg).fillna(0.0)

    res = {}

    g1 = tmp.groupby(["group_1"], dropna=False, as_index=False)["amount"].sum()
    g1["level"] = 1
    g1["key"] = g1["group_1"]
    g1["parent_key"] = ""
    g1["label"] = g1["group_1"]
    res[1] = g1[["level", "key", "parent_key", "label", "amount"]]

    if len(cfg["group_cols"]) >= 2:
        g2 = tmp.groupby(["group_1", "group_2"], dropna=False, as_index=False)["amount"].sum()
        g2["level"] = 2
        g2["key"] = g2["group_1"] + " | " + g2["group_2"]
        g2["parent_key"] = g2["group_1"]
        g2["label"] = g2["group_2"]
        res[2] = g2[["level", "key", "parent_key", "label", "amount"]]

    if len(cfg["group_cols"]) >= 3:
        g3 = tmp.groupby(["group_1", "group_2", "group_3"], dropna=False, as_index=False)["amount"].sum()
        g3["level"] = 3
        g3["key"] = g3["group_1"] + " | " + g3["group_2"] + " | " + g3["group_3"]
        g3["parent_key"] = g3["group_1"] + " | " + g3["group_2"]
        g3["label"] = g3["group_3"]
        res[3] = g3[["level", "key", "parent_key", "label", "amount"]]

    return pd.concat([res[k] for k in sorted(res.keys())], ignore_index=True)


# Baut aus den periodischen Aggregationen eine Wide-Tabelle pro Kennzahl.
def build_metric_wide_table(d: pd.DataFrame, period_defs: dict, cfg: dict, metric: str) -> pd.DataFrame:
    period_map = period_defs["period_map"]

    wide = None
    meta_parts = []

    for p_label, (start, end) in period_map.items():
        agg = aggregate_period_metric(d, start, end, cfg, metric)

        meta_parts.append(
            agg[["level", "key", "parent_key", "label"]].drop_duplicates(subset=["level", "key"])
        )

        agg = agg.rename(columns={"amount": p_label})

        if wide is None:
            wide = agg[["level", "key", p_label]].copy()
        else:
            wide = wide.merge(
                agg[["level", "key", p_label]],
                on=["level", "key"],
                how="outer"
            )

    meta_cols = ["level", "key", "parent_key", "label"]
    if wide is None:
        return pd.DataFrame(columns=meta_cols)

    base = (
        pd.concat(meta_parts, ignore_index=True)
        .sort_values(["level", "key"], kind="mergesort")
        .drop_duplicates(subset=["level", "key"], keep="first")
    )

    wide = base.merge(
        wide,
        on=["level", "key"],
        how="left"
    )

    for p in period_map.keys():
        if p not in wide.columns:
            wide[p] = np.nan

    wide["__orig_key"] = wide["key"]

    return wide


# Ergänzt Delta- und CAGR-Spalten auf Basis vorhandener Periodenspalten.
def add_delta_and_cagr_columns(df: pd.DataFrame, period_defs: dict, cfg: dict, metric_type: str) -> pd.DataFrame:
    out = df.copy()

    fy_labels = list(period_defs["fy_labels"])
    if len(fy_labels) >= 2 and period_defs.get("fy_delta_calc_label"):
        older_label = fy_labels[-2]
        newer_label = fy_labels[-1]
        out[period_defs["fy_delta_calc_label"]] = (
            pd.to_numeric(out[newer_label], errors="coerce")
            - pd.to_numeric(out[older_label], errors="coerce")
        )

    if len(fy_labels) >= 2 and period_defs.get("fy_cagr_label"):
        out[period_defs["fy_cagr_label"]] = compute_dynamic_cagr_series(out, fy_labels)

    if cfg.get("show_ytd", False):
        ytd_labels = list(period_defs["ytd_labels"])
        if len(ytd_labels) >= 2:
            y11, y12 = ytd_labels
            if cfg.get("show_ytd_delta", False) and period_defs.get("ytd_delta_calc_label"):
                out[period_defs["ytd_delta_calc_label"]] = (
                    pd.to_numeric(out[y12], errors="coerce")
                    - pd.to_numeric(out[y11], errors="coerce")
                )

            if cfg.get("show_ytd_cagr", False) and period_defs.get("ytd_cagr_label"):
                out[period_defs["ytd_cagr_label"]] = compute_dynamic_cagr_series(out, ytd_labels)

    if cfg.get("show_ltm", False):
        ltm_labels = list(period_defs["ltm_labels"])
        if len(ltm_labels) >= 2:
            l11, l12 = ltm_labels
            if cfg.get("show_ltm_delta", False) and period_defs.get("ltm_delta_calc_label"):
                out[period_defs["ltm_delta_calc_label"]] = (
                    pd.to_numeric(out[l12], errors="coerce")
                    - pd.to_numeric(out[l11], errors="coerce")
                )

            if cfg.get("show_ltm_cagr", False) and period_defs.get("ltm_cagr_label"):
                out[period_defs["ltm_cagr_label"]] = compute_dynamic_cagr_series(out, ltm_labels)

    return out


# Berechnet Margen als GP geteilt durch Revenue für alle Perioden.
def compute_margin_wide(rev_wide: pd.DataFrame, gp_wide: pd.DataFrame, period_defs: dict, cfg: dict) -> pd.DataFrame:
    meta = rev_wide[["level", "key", "parent_key", "label"]].copy()
    period_cols = list(period_defs["period_map"].keys())
    out = meta.copy()

    rev_idx = rev_wide.set_index(["level", "key"])
    gp_idx = gp_wide.set_index(["level", "key"])

    for c in period_cols:
        rv = pd.to_numeric(rev_idx[c], errors="coerce")
        gp = pd.to_numeric(gp_idx[c], errors="coerce")
        out[c] = np.where(rv != 0, gp / rv, np.nan)

    out = add_delta_and_cagr_columns(out, period_defs, cfg, metric_type="margin")
    return out


# Legt pro Zeile den Sortierwert auf Basis des gewählten Sortiermodus fest.
def add_delta_sort_value(df: pd.DataFrame, period_defs: dict, cfg: dict) -> pd.DataFrame:
    out = df.copy()
    sort_mode = cfg["sort_mode"]

    if sort_mode == "FY_LAST":
        out["_sort_value"] = pd.to_numeric(out[period_defs["fy_labels"][-1]], errors="coerce").fillna(-np.inf)
    elif sort_mode == "YTD_LAST" and cfg.get("show_ytd", False):
        out["_sort_value"] = pd.to_numeric(out[period_defs["ytd_labels"][-1]], errors="coerce").fillna(-np.inf)
    elif sort_mode == "LTM_LAST" and cfg.get("show_ltm", False):
        out["_sort_value"] = pd.to_numeric(out[period_defs["ltm_labels"][-1]], errors="coerce").fillna(-np.inf)
    elif sort_mode == "ALPHABETICAL":
        out["_sort_value"] = pd.to_numeric(out[period_defs["fy_labels"][-1]], errors="coerce").fillna(-np.inf)
    else:
        out["_sort_value"] = pd.to_numeric(out[period_defs["fy_labels"][-1]], errors="coerce").fillna(-np.inf)

    return out


# Erstellt den Limit-Plan je Parent und Level auf Basis des Revenue-Werts.
def build_limit_plan_from_revenue(rev_df: pd.DataFrame, cfg: dict, period_defs: dict) -> dict:
    """
    Erstellt einen Plan, welche Keys je Level/Parent behalten werden und
    welche im Other-Bucket landen.
    Sortierung IMMER nach Revenue des letzten FY.
    """
    out = {}

    fy_last = period_defs["fy_labels"][-1]
    work = rev_df.copy()
    work["_limit_sort_value"] = pd.to_numeric(work[fy_last], errors="coerce").fillna(-np.inf)

    max_level = len(cfg["group_cols"])

    for level in range(1, max_level + 1):
        lvl_cfg = cfg["hierarchy_limits"].get(f"level_{level}", {}) or {}
        max_items = lvl_cfg.get("max_items")
        other_label = str(lvl_cfg.get("other_label", f"Other L{level}")).strip()

        if max_items in (None, "", 0):
            continue

        max_items = int(max_items)
        lvl = work[work["level"] == level].copy()

        if level == 1:
            parent_groups = [("", lvl)]
        else:
            parent_groups = list(lvl.groupby("parent_key", dropna=False))

        for parent_key, block in parent_groups:
            block = block.sort_values(
                ["_limit_sort_value", "label"],
                ascending=[False, True],
                kind="mergesort"
            )

            keep = block.head(max_items).copy()
            rest = block.iloc[max_items:].copy()

            out[(level, str(parent_key))] = {
                "keep_keys": keep["key"].astype(str).tolist(),
                "rest_keys": rest["key"].astype(str).tolist(),
                "other_label": other_label,
            }

    return out


def sum_or_nan(series) -> float:
    """
    Summiert numerische Werte, lässt aber 'echtes Missing' als NaN bestehen.
    Wenn ALLE Werte missing sind, bleibt das Ergebnis NaN.
    """
    vals = pd.to_numeric(pd.Series(series), errors="coerce")
    if vals.notna().sum() == 0:
        return np.nan
    return vals.fillna(0.0).sum()


def ensure_ui_level(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "_ui_level" not in out.columns:
        out["_ui_level"] = pd.to_numeric(out["level"], errors="coerce")
    return out


def replace_path_prefix(series: pd.Series, old_prefix: str, new_prefix: str) -> pd.Series:
    s = series.astype(str)

    exact_mask = s == old_prefix
    pref_mask = s.str.startswith(old_prefix + " | ", na=False)

    s = s.where(~exact_mask, new_prefix)
    s = s.where(
        ~pref_mask,
        s.str.replace(
            f"^{re.escape(old_prefix)}\\s\\|\\s",
            f"{new_prefix} | ",
            regex=True
        )
    )
    return s


# Verschiebt einen kompletten Teilbaum unter einen Other-Knoten und passt nur das UI Level an.
def shift_subtree_under_other(current: pd.DataFrame, old_key: str, other_key: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Verschiebt den kompletten Teilbaum unter einen Other-Knoten.
    Fachliches level bleibt unverändert.
    Nur _ui_level wird um +1 ggü. dem Other-Knoten verschoben.
    """
    out = ensure_ui_level(current)

    subtree_mask = (
        out["key"].astype(str).eq(old_key) |
        out["key"].astype(str).str.startswith(old_key + " | ", na=False)
    )
    subtree = out[subtree_mask].copy()
    if "__orig_key" not in subtree.columns:
        subtree["__orig_key"] = subtree["key"]

    if subtree.empty:
        return out, pd.DataFrame(columns=out.columns)

    root = subtree[subtree["key"].astype(str) == old_key].copy()
    if root.empty:
        return out, pd.DataFrame(columns=out.columns)

    root_label = str(root.iloc[0]["label"]).strip()
    root_new_key = f"{other_key} | {root_label}"

    other_ui_vals = pd.to_numeric(
        out.loc[out["key"].astype(str) == other_key, "_ui_level"],
        errors="coerce"
    ).dropna()

    root_ui_vals = pd.to_numeric(root["_ui_level"], errors="coerce").dropna()

    if len(other_ui_vals) == 0 or len(root_ui_vals) == 0:
        return out, pd.DataFrame(columns=out.columns)

    other_ui = int(other_ui_vals.iloc[0])
    root_ui = int(root_ui_vals.iloc[0])

    ui_shift = (other_ui + 1) - root_ui

    subtree["key"] = replace_path_prefix(subtree["key"], old_key, root_new_key)
    subtree["parent_key"] = replace_path_prefix(subtree["parent_key"], old_key, root_new_key)

    subtree.loc[subtree["key"].astype(str) == root_new_key, "parent_key"] = other_key
    subtree["_ui_level"] = pd.to_numeric(subtree["_ui_level"], errors="coerce").fillna(
        pd.to_numeric(subtree["level"], errors="coerce")
    ) + ui_shift

    subtree["row_type"] = "other_detail"

    out = out[~subtree_mask].copy()
    return out, subtree


# Konsolidiert nach Remaps identische Zeilen wieder zusammen.
def consolidate_hierarchy_rows_after_remap(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    if out.empty:
        return out

    if "row_type" not in out.columns:
        out["row_type"] = "normal"

    out = ensure_ui_level(out)

    meta_cols = ["level", "_ui_level", "key", "parent_key", "label"]
    other_cols = [c for c in out.columns if c not in meta_cols + ["row_type"]]

    def collapse_row_type(s: pd.Series) -> str:
        vals = [str(v).strip().lower() for v in s if pd.notna(v)]
        if "other" in vals:
            return "other"
        if "other_detail" in vals:
            return "other_detail"
        if len(vals) == 0:
            return "normal"
        return vals[0]

    def first_non_null(s: pd.Series):
        for v in s:
            if pd.notna(v):
                return v
        return np.nan

    agg_map = {"row_type": collapse_row_type}

    for c in other_cols:
        if c == "__orig_key":
            agg_map[c] = first_non_null
        else:
            agg_map[c] = sum_or_nan

    result = (
        out.groupby(meta_cols, dropna=False, as_index=False)
        .agg(agg_map)
    )

    return result


# Wendet den Limit-Plan an und erzeugt Other-Zeilen samt Detail-Unterbäumen.
def apply_hierarchy_limits_by_plan(df: pd.DataFrame, limit_plan: dict, period_defs: dict, cfg: dict) -> pd.DataFrame:
    out = ensure_ui_level(df.copy())

    if "row_type" not in out.columns:
        out["row_type"] = "normal"

    period_cols = list(period_defs["period_map"].keys())
    derived_cols = [period_defs["fy_delta_calc_label"], period_defs["fy_cagr_label"]]

    if cfg.get("show_ytd", False):
        if cfg.get("show_ytd_delta", False):
            derived_cols.append(period_defs["ytd_delta_calc_label"])
        if cfg.get("show_ytd_cagr", False):
            derived_cols.append(period_defs["ytd_cagr_label"])

    if cfg.get("show_ltm", False):
        if cfg.get("show_ltm_delta", False):
            derived_cols.append(period_defs["ltm_delta_calc_label"])
        if cfg.get("show_ltm_cagr", False):
            derived_cols.append(period_defs["ltm_cagr_label"])

    value_cols = [c for c in period_cols + derived_cols if c in out.columns]

    max_level = len(cfg["group_cols"])
    current = out.copy()

    for level in range(1, max_level + 1):
        lvl = current[current["level"] == level].copy()
        if lvl.empty:
            continue

        if level == 1:
            parent_groups = [("", lvl)]
        else:
            parent_groups = list(lvl.groupby("parent_key", dropna=False))

        other_rows = []
        shift_jobs = []

        for parent_key, block in parent_groups:
            plan = limit_plan.get((level, str(parent_key)))
            if not plan:
                continue

            rest_keys = set(plan["rest_keys"])
            other_label = plan["other_label"]

            if len(rest_keys) == 0:
                continue

            rest_block = block[block["key"].astype(str).isin(rest_keys)].copy()
            if rest_block.empty:
                continue

            other_key = (str(parent_key) + " | " + other_label).strip(" |")

            row = {c: np.nan for c in current.columns}
            row["level"] = level
            row["parent_key"] = parent_key if level > 1 else ""
            row["label"] = other_label
            row["key"] = other_key
            row["row_type"] = "other"

            if level == 1:
                row["_ui_level"] = 1
            else:
                parent_ui = pd.to_numeric(
                    current.loc[current["key"].astype(str) == str(parent_key), "_ui_level"],
                    errors="coerce"
                ).dropna()
                row["_ui_level"] = int(parent_ui.iloc[0]) + 1 if len(parent_ui) else level

            for c in value_cols:
                if c in rest_block.columns:
                    if c == period_defs.get("fy_cagr_label") or \
                       c == period_defs.get("ytd_cagr_label") or \
                       c == period_defs.get("ltm_cagr_label"):
                        continue
                    row[c] = sum_or_nan(rest_block[c])

            other_rows.append(row)

            for old_key in rest_keys:
                shift_jobs.append((str(old_key), other_key))

        if other_rows:
            current = pd.concat([current, pd.DataFrame(other_rows)], ignore_index=True)

        shifted_parts = []
        for old_key, other_key in sorted(shift_jobs, key=lambda x: len(x[0]), reverse=True):
            current, shifted = shift_subtree_under_other(current, old_key, other_key)
            if not shifted.empty:
                shifted_parts.append(shifted)

        if shifted_parts:
            current = pd.concat([current] + shifted_parts, ignore_index=True)

        current = consolidate_hierarchy_rows_after_remap(current)

    return current


# Berechnet Delta- und CAGR-Spalten nach den Limits erneut.
def recompute_delta_and_cagr_after_limits(df: pd.DataFrame, period_defs: dict, cfg: dict, metric_type: str) -> pd.DataFrame:
    out = df.copy()

    fy_labels = list(period_defs["fy_labels"])
    if len(fy_labels) >= 2 and period_defs.get("fy_delta_calc_label"):
        older_label = fy_labels[-2]
        newer_label = fy_labels[-1]
        out[period_defs["fy_delta_calc_label"]] = (
            pd.to_numeric(out[newer_label], errors="coerce")
            - pd.to_numeric(out[older_label], errors="coerce")
        )

    if len(fy_labels) >= 2 and period_defs.get("fy_cagr_label"):
        out[period_defs["fy_cagr_label"]] = compute_dynamic_cagr_series(out, fy_labels)

    if cfg.get("show_ytd", False):
        ytd_labels = list(period_defs["ytd_labels"])
        if len(ytd_labels) >= 2:
            y11, y12 = ytd_labels
            if cfg.get("show_ytd_delta", False) and period_defs.get("ytd_delta_calc_label"):
                out[period_defs["ytd_delta_calc_label"]] = (
                    pd.to_numeric(out[y12], errors="coerce")
                    - pd.to_numeric(out[y11], errors="coerce")
                )

            if cfg.get("show_ytd_cagr", False) and period_defs.get("ytd_cagr_label"):
                out[period_defs["ytd_cagr_label"]] = compute_dynamic_cagr_series(out, ytd_labels)

    if cfg.get("show_ltm", False):
        ltm_labels = list(period_defs["ltm_labels"])
        if len(ltm_labels) >= 2:
            l11, l12 = ltm_labels
            if cfg.get("show_ltm_delta", False) and period_defs.get("ltm_delta_calc_label"):
                out[period_defs["ltm_delta_calc_label"]] = (
                    pd.to_numeric(out[l12], errors="coerce")
                    - pd.to_numeric(out[l11], errors="coerce")
                )

            if cfg.get("show_ltm_cagr", False) and period_defs.get("ltm_cagr_label"):
                out[period_defs["ltm_cagr_label"]] = compute_dynamic_cagr_series(out, ltm_labels)

    return out


# Baut die finale Anzeige-Reihenfolge der Hierarchie rekursiv auf.
def build_display_order(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = df.copy()
    sort_mode = cfg["sort_mode"]

    if "row_type" not in out.columns:
        out["row_type"] = "normal"

    def sort_block(block: pd.DataFrame) -> pd.DataFrame:
        if block.empty:
            return block

        tmp = block.copy()
        tmp["_is_other"] = (tmp["row_type"].astype(str).str.lower() == "other").astype(int)

        if sort_mode == "ALPHABETICAL":
            tmp = tmp.sort_values(
                ["_is_other", "label"],
                ascending=[True, True],
                kind="mergesort"
            )
        else:
            tmp = tmp.sort_values(
                ["_is_other", "_sort_value", "label"],
                ascending=[True, False, True],
                kind="mergesort"
            )

        return tmp.drop(columns=["_is_other"], errors="ignore")

    order_rows = []
    visited = set()
    pos = 0

    def walk(parent_key: str):
        nonlocal pos

        block = out[out["parent_key"].astype(str) == str(parent_key)].copy()
        block = block[~block["key"].astype(str).isin(visited)]
        block = sort_block(block)

        for _, row in block.iterrows():
            key = str(row["key"])
            walk(key)
            if key not in visited:
                pos += 1
                order_rows.append((key, pos))
                visited.add(key)

    walk("")

    leftovers = out[~out["key"].astype(str).isin(visited)].copy()
    leftovers = sort_block(leftovers)

    for _, r in leftovers.iterrows():
        pos += 1
        order_rows.append((r["key"], pos))

    order_df = pd.DataFrame(order_rows, columns=["key", "_display_order"])
    out = out.merge(order_df, on="key", how="left")
    out = out.sort_values("_display_order", kind="mergesort").reset_index(drop=True)
    return out


# Markiert je Periodenspalte, ob der Wert fachlich fehlt.
def mark_missing_periods(df: pd.DataFrame, period_defs: dict) -> pd.DataFrame:
    out = df.copy()
    for p in period_defs["period_map"].keys():
        out[f"_missing_{p}"] = out[p].isna()
    return out


# Bringt die finalen Exportspalten in die gewünschte Spaltenstruktur und versieht ihn mit Prefixen.
def finalize_metric_block(df: pd.DataFrame, block_name: str, metric_kind: str, period_defs: dict, cfg: dict) -> pd.DataFrame:
    out = df.copy()

    ordered_cols = ["level", "_ui_level", "key", "parent_key", "label", "row_type", "_sort_value", "_display_order", "__orig_key"]
    ordered_cols += period_defs["fy_labels"]
    ordered_cols += [period_defs["fy_delta_calc_label"], period_defs["fy_cagr_label"]]

    if cfg.get("show_ytd", False):
        ordered_cols += period_defs["ytd_labels"]
        if cfg.get("show_ytd_delta", False):
            ordered_cols += [period_defs["ytd_delta_calc_label"]]
        if cfg.get("show_ytd_cagr", False):
            ordered_cols += [period_defs["ytd_cagr_label"]]

    if cfg.get("show_ltm", False):
        ordered_cols += period_defs["ltm_labels"]
        if cfg.get("show_ltm_delta", False):
            ordered_cols += [period_defs["ltm_delta_calc_label"]]
        if cfg.get("show_ltm_cagr", False):
            ordered_cols += [period_defs["ltm_cagr_label"]]

    missing_cols = [c for c in out.columns if str(c).startswith("_missing_")]
    ordered_cols += missing_cols
    ordered_cols = [c for c in ordered_cols if c in out.columns]
    out = out[ordered_cols].copy()

    rename_map = {}
    for c in out.columns:
        if c in {"level", "_ui_level", "key", "parent_key", "label", "row_type", "_sort_value", "_display_order", "__orig_key"}:
            continue
        rename_map[c] = f"{block_name}__{c}"

    out = out.rename(columns=rename_map)
    return out


# Ermittelt echte Blattzzeilen, die für Total-Berechnungen relevant sind.
def get_leaf_rows_for_total(base_rows: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = base_rows.copy()
    out = out[~out["row_type"].astype(str).str.lower().isin({"total", "kpi", "kpi_section", "section"})].copy()

    parent_keys = set(
        pk for pk in out["parent_key"].astype(str).tolist()
        if str(pk).strip() != ""
    )

    out = out[~out["key"].astype(str).isin(parent_keys)].copy()
    return out


# Baut die Total-Zeile für Amount-Blöcke wie Revenue und GP.
def build_total_row(base_rows: pd.DataFrame, blocks: list[tuple[str, str]], period_defs: dict, cfg: dict) -> pd.DataFrame:
    source = get_leaf_rows_for_total(base_rows, cfg)

    row = {
        "level": 0,
        "_ui_level": 0,
        "key": "__TOTAL__",
        "parent_key": "",
        "label": str(cfg.get("total_label", "Total")),
        "row_type": "total",
        "_sort_value": np.nan,
        "_display_order": 10**9,
    }

    for block_name, metric_kind in blocks:
        if metric_kind != "amount":
            continue

        for p in period_defs["period_map"].keys():
            col = f"{block_name}__{p}"
            if col in source.columns:
                row[col] = sum_or_nan(source[col])

        fy_labels = list(period_defs["fy_labels"])

        if len(fy_labels) >= 2 and period_defs.get("fy_delta_calc_label"):
            older_label = fy_labels[-2]
            newer_label = fy_labels[-1]
            row[f"{block_name}__{period_defs['fy_delta_calc_label']}"] = (
                row.get(f"{block_name}__{newer_label}", np.nan)
                - row.get(f"{block_name}__{older_label}", np.nan)
            )

        if len(fy_labels) >= 2 and period_defs.get("fy_cagr_label"):
            row[f"{block_name}__{period_defs['fy_cagr_label']}"] = compute_dynamic_cagr_from_values(
                [row.get(f"{block_name}__{lbl}", np.nan) for lbl in fy_labels]
            )

        if cfg.get("show_ytd", False):
            ytd_labels = list(period_defs["ytd_labels"])

            if len(ytd_labels) >= 2:
                y11, y12 = ytd_labels

                if cfg.get("show_ytd_delta", False) and period_defs.get("ytd_delta_calc_label"):
                    row[f"{block_name}__{period_defs['ytd_delta_calc_label']}"] = (
                        row.get(f"{block_name}__{y12}", np.nan)
                        - row.get(f"{block_name}__{y11}", np.nan)
                    )

                if cfg.get("show_ytd_cagr", False) and period_defs.get("ytd_cagr_label"):
                    row[f"{block_name}__{period_defs['ytd_cagr_label']}"] = compute_dynamic_cagr_from_values(
                        [row.get(f"{block_name}__{lbl}", np.nan) for lbl in ytd_labels]
                    )

        if cfg.get("show_ltm", False):
            ltm_labels = list(period_defs["ltm_labels"])

            if len(ltm_labels) >= 2:
                l11, l12 = ltm_labels

                if cfg.get("show_ltm_delta", False) and period_defs.get("ltm_delta_calc_label"):
                    row[f"{block_name}__{period_defs['ltm_delta_calc_label']}"] = (
                        row.get(f"{block_name}__{l12}", np.nan)
                        - row.get(f"{block_name}__{l11}", np.nan)
                    )

                if cfg.get("show_ltm_cagr", False) and period_defs.get("ltm_cagr_label"):
                    row[f"{block_name}__{period_defs['ltm_cagr_label']}"] = compute_dynamic_cagr_from_values(
                        [row.get(f"{block_name}__{lbl}", np.nan) for lbl in ltm_labels]
                    )

    return pd.DataFrame([row])


# Baut die Total-Zeile speziell für den Margin-Block GM.
def build_gm_total_row(base_rows: pd.DataFrame, period_defs: dict, cfg: dict) -> pd.DataFrame:
    source = get_leaf_rows_for_total(base_rows, cfg)

    row = {
        "level": 0,
        "_ui_level": 0,
        "key": "__TOTAL__",
        "parent_key": "",
        "label": str(cfg.get("total_label", "Total")),
        "row_type": "total",
        "_sort_value": np.nan,
        "_display_order": 10**9,
    }

    for p in period_defs["period_map"].keys():
        gp_col = f"GP__{p}"
        rev_col = f"Revenue__{p}"
        gm_col = f"GM__{p}"

        gp = sum_or_nan(source[gp_col]) if gp_col in source.columns else np.nan
        rv = sum_or_nan(source[rev_col]) if rev_col in source.columns else np.nan
        row[gm_col] = gp / rv if pd.notna(gp) and pd.notna(rv) and rv != 0 else np.nan

    fy_labels = list(period_defs["fy_labels"])

    if len(fy_labels) >= 2 and period_defs.get("fy_delta_calc_label"):
        older_label = fy_labels[-2]
        newer_label = fy_labels[-1]
        row[f"GM__{period_defs['fy_delta_calc_label']}"] = (
            row.get(f"GM__{newer_label}", np.nan)
            - row.get(f"GM__{older_label}", np.nan)
        )

    if len(fy_labels) >= 2 and period_defs.get("fy_cagr_label"):
        row[f"GM__{period_defs['fy_cagr_label']}"] = compute_dynamic_cagr_from_values(
            [row.get(f"GM__{lbl}", np.nan) for lbl in fy_labels]
        )

    if cfg.get("show_ytd", False):
        ytd_labels = list(period_defs["ytd_labels"])

        if len(ytd_labels) >= 2:
            y11, y12 = ytd_labels

            if cfg.get("show_ytd_delta", False) and period_defs.get("ytd_delta_calc_label"):
                row[f"GM__{period_defs['ytd_delta_calc_label']}"] = (
                    row.get(f"GM__{y12}", np.nan)
                    - row.get(f"GM__{y11}", np.nan)
                )

            if cfg.get("show_ytd_cagr", False) and period_defs.get("ytd_cagr_label"):
                row[f"GM__{period_defs['ytd_cagr_label']}"] = compute_dynamic_cagr_from_values(
                    [row.get(f"GM__{lbl}", np.nan) for lbl in ytd_labels]
                )

    if cfg.get("show_ltm", False):
        ltm_labels = list(period_defs["ltm_labels"])

        if len(ltm_labels) >= 2:
            l11, l12 = ltm_labels

            if cfg.get("show_ltm_delta", False) and period_defs.get("ltm_delta_calc_label"):
                row[f"GM__{period_defs['ltm_delta_calc_label']}"] = (
                    row.get(f"GM__{l12}", np.nan)
                    - row.get(f"GM__{l11}", np.nan)
                )

            if cfg.get("show_ltm_cagr", False) and period_defs.get("ltm_cagr_label"):
                row[f"GM__{period_defs['ltm_cagr_label']}"] = compute_dynamic_cagr_from_values(
                    [row.get(f"GM__{lbl}", np.nan) for lbl in ltm_labels]
                )

    return pd.DataFrame([row])


# Baut den KPI-Abschnitt mit YoY-Wachstumszeilen für Top-Level-Elemente.
def build_kpi_section(df: pd.DataFrame, period_defs: dict, cfg: dict, blocks: list[str]) -> pd.DataFrame:
    top = df[(df["level"] == 1) & (df["row_type"] != "total")].copy()
    rows = []

    section = {
        "level": -2,
        "_ui_level": 0,
        "key": "__KPI_SECTION__",
        "parent_key": "",
        "label": "KPIs",
        "row_type": "kpi_section",
        "_sort_value": np.nan,
        "_display_order": 10**9 + 50,
    }
    rows.append(section)

    for _, r in top.iterrows():
        row = {
            "level": -1,
            "_ui_level": 0,
            "key": f"__KPI_{r['key']}",
            "parent_key": "",
            "label": f"Y-o-y {r['label']}",
            "row_type": "kpi",
            "_sort_value": np.nan,
            "_display_order": 10**9 + 100 + len(rows),
        }

        for block_name in blocks:
            for p in period_defs["fy_labels"]:
                row[f"{block_name}__{p}"] = np.nan

            if len(period_defs["fy_labels"]) >= 2 and period_defs.get("fy_delta_calc_label"):
                row[f"{block_name}__{period_defs['fy_delta_calc_label']}"] = np.nan

            if len(period_defs["fy_labels"]) >= 2 and period_defs.get("fy_cagr_label"):
                row[f"{block_name}__{period_defs['fy_cagr_label']}"] = np.nan

            if cfg.get("show_ytd", False):
                for p in period_defs["ytd_labels"]:
                    row[f"{block_name}__{p}"] = np.nan

                if cfg.get("show_ytd_delta", False) and period_defs.get("ytd_delta_calc_label"):
                    row[f"{block_name}__{period_defs['ytd_delta_calc_label']}"] = np.nan

                if cfg.get("show_ytd_cagr", False) and period_defs.get("ytd_cagr_label"):
                    row[f"{block_name}__{period_defs['ytd_cagr_label']}"] = np.nan

            if cfg.get("show_ltm", False):
                for p in period_defs["ltm_labels"]:
                    row[f"{block_name}__{p}"] = np.nan

                if cfg.get("show_ltm_delta", False) and period_defs.get("ltm_delta_calc_label"):
                    row[f"{block_name}__{period_defs['ltm_delta_calc_label']}"] = np.nan

                if cfg.get("show_ltm_cagr", False) and period_defs.get("ltm_cagr_label"):
                    row[f"{block_name}__{period_defs['ltm_cagr_label']}"] = np.nan

        prev_val = None
        for i, p in enumerate(period_defs["fy_labels"]):
            col = f"{block_name}__{p}"
            cur_val = pd.to_numeric(pd.Series([r.get(col, np.nan)]), errors="coerce").iloc[0]
            if i == 0:
                row[col] = np.nan
            else:
                if pd.notna(prev_val) and prev_val != 0 and pd.notna(cur_val):
                    row[col] = ((cur_val / prev_val) - 1) * 100
            prev_val = cur_val

        if cfg.get("show_ytd", False):
            prev_val = None
            for i, p in enumerate(period_defs["ytd_labels"]):
                col = f"{block_name}__{p}"
                cur_val = pd.to_numeric(pd.Series([r.get(col, np.nan)]), errors="coerce").iloc[0]
                if i == 0:
                    row[col] = np.nan
                else:
                    if pd.notna(prev_val) and prev_val != 0 and pd.notna(cur_val):
                        row[col] = ((cur_val / prev_val) - 1) * 100
                prev_val = cur_val

        if cfg.get("show_ltm", False):
            prev_val = None
            for i, p in enumerate(period_defs["ltm_labels"]):
                col = f"{block_name}__{p}"
                cur_val = pd.to_numeric(pd.Series([r.get(col, np.nan)]), errors="coerce").iloc[0]
                if i == 0:
                    row[col] = np.nan
                else:
                    if pd.notna(prev_val) and prev_val != 0 and pd.notna(cur_val):
                        row[col] = ((cur_val / prev_val) - 1) * 100
                prev_val = cur_val

        rows.append(row)

    return pd.DataFrame(rows)


# Baut die komplette finale Report-Tabelle inklusive Blocks, Totals und KPI-Bereich.
def build_final_table(d: pd.DataFrame, period_defs: dict, cfg: dict) -> tuple[pd.DataFrame, list[tuple[str, str]]]:
    # Basis-Wide-Tabellen NUR mit Periodenspalten
    rev_wide = build_metric_wide_table(d, period_defs, cfg, metric="revenue")
    gp_wide = build_metric_wide_table(d, period_defs, cfg, metric="gp")

    rev_wide = apply_metric_period_restrictions(rev_wide, "revenue", cfg, period_defs)
    gp_wide = apply_metric_period_restrictions(gp_wide, "gp", cfg, period_defs)

    # Revenue-basierter Limit-Plan
    limit_plan = build_limit_plan_from_revenue(rev_wide, cfg, period_defs)

    # Limits identisch auf Revenue / GP anwenden
    rev_limited = apply_hierarchy_limits_by_plan(rev_wide, limit_plan, period_defs, cfg)

    gp_limited = apply_hierarchy_limits_by_plan(gp_wide, limit_plan, period_defs, cfg)

    # GM erst NACH den Limits aus limitiertem Revenue / GP berechnen
    gm_limited = compute_margin_wide(rev_limited, gp_limited, period_defs, cfg)

    # Delta/CAGR sauber neu berechnen
    rev_limited = recompute_delta_and_cagr_after_limits(rev_limited, period_defs, cfg, metric_type="amount")
    gp_limited = recompute_delta_and_cagr_after_limits(gp_limited, period_defs, cfg, metric_type="amount")
    gm_limited = recompute_delta_and_cagr_after_limits(gm_limited, period_defs, cfg, metric_type="percent")

    # Sortierung / Reihenfolge IMMER nach Revenue letzten FY
    rev_limited = add_delta_sort_value(rev_limited, period_defs, cfg)
    rev_limited = build_display_order(rev_limited, cfg)

    order_cols = rev_limited[["level", "_ui_level", "key", "parent_key", "_display_order", "_sort_value"]].copy()

    def attach_order(metric_df: pd.DataFrame) -> pd.DataFrame:
        out = metric_df.merge(
            order_cols,
            on=["level", "key", "parent_key"],
            how="left",
            suffixes=("", "_ord")
        )
        if "_display_order_ord" in out.columns:
            out["_display_order"] = out["_display_order_ord"]
            out = out.drop(columns=["_display_order_ord"])
        if "_sort_value_ord" in out.columns:
            out["_sort_value"] = out["_sort_value_ord"]
            out = out.drop(columns=["_sort_value_ord"])
        return out

    rev = attach_order(rev_limited)
    gp = attach_order(gp_limited)
    gm = attach_order(gm_limited)

    rev = mark_missing_periods(rev, period_defs)
    gp = mark_missing_periods(gp, period_defs)
    gm = mark_missing_periods(gm, period_defs)

    blocks = []
    block_frames = []

    if cfg.get("show_revenue", True):
        blocks.append(("Revenue", "amount"))
        block_frames.append(finalize_metric_block(rev, "Revenue", "amount", period_defs, cfg))

    if cfg.get("show_gp", False):
        blocks.append(("GP", "amount"))
        block_frames.append(finalize_metric_block(gp, "GP", "amount", period_defs, cfg))

    if cfg.get("show_gm", False):
        blocks.append(("GM", "percent"))
        block_frames.append(finalize_metric_block(gm, "GM", "percent", period_defs, cfg))

    base = None
    meta_cols = ["level", "_ui_level", "key", "parent_key", "label", "row_type", "_sort_value", "_display_order", "__orig_key"]

    for i, bf in enumerate(block_frames):
        metric_meta_cols = [c for c in meta_cols if c in bf.columns]
        metric_only = [c for c in bf.columns if c not in metric_meta_cols]
        if i == 0:
            base = bf[metric_meta_cols + metric_only].copy()
        else:
            merge_keys = ["level", "_ui_level", "key", "parent_key", "label", "row_type", "_sort_value", "_display_order", "__orig_key"]
            merge_keys = [c for c in merge_keys if c in base.columns and c in bf.columns]

            base = base.merge(
                bf[merge_keys + metric_only],
                on=merge_keys,
                how="left"
            )

    base["row_type"] = base["row_type"].fillna("normal")
    base = base.sort_values("_display_order", kind="mergesort").reset_index(drop=True)

    total_parts = []
    if cfg.get("show_revenue", True) or cfg.get("show_gp", False):
        total_parts.append(build_total_row(base, [(b, k) for b, k in blocks if k == "amount"], period_defs, cfg))

    if cfg.get("show_gm", False):
        total_gm = build_gm_total_row(base, period_defs, cfg)
        if len(total_parts) == 0:
            total_parts.append(total_gm)
        else:
            total_parts[0] = total_parts[0].merge(
                total_gm,
                on=["level", "_ui_level", "key", "parent_key", "label", "row_type", "_sort_value", "_display_order"],
                how="outer"
            )

    total_row = total_parts[0] if total_parts else pd.DataFrame()

    reported_rows = build_reported_rows(period_defs, cfg, blocks)

    kpi_blocks = [b for b, _ in blocks]
    kpi_df = build_kpi_section(base, period_defs, cfg, kpi_blocks)

    final = pd.concat([base, total_row, reported_rows, kpi_df], ignore_index=True, sort=False)
    final = final.sort_values("_display_order", kind="mergesort").reset_index(drop=True)
    return final, blocks


# Ordnet die finalen Exportspalten in die gewünschte Reihenfolge.
def arrange_columns(final: pd.DataFrame, period_defs: dict, cfg: dict, blocks: list[tuple[str, str]]) -> pd.DataFrame:
    cols = ["level", "_ui_level", "key", "parent_key", "row_type", "_display_order", "_sort_value", "label", "__orig_key"]

    for block_name, metric_kind in blocks:
        metric_name = block_to_logical_metric(block_name)

        # FY-Perioden immer behalten
        for c in period_defs["fy_labels"]:
            full_col = f"{block_name}__{c}"
            if full_col in final.columns:
                cols.append(full_col)

        fy_displayable_labels = get_displayable_section_labels(metric_name, "fy", cfg, period_defs)

        if period_defs.get("fy_delta_calc_label"):
            fy_delta_col = f"{block_name}__{period_defs['fy_delta_calc_label']}"
            if fy_delta_col in final.columns and len(fy_displayable_labels) >= 2:
                cols.append(fy_delta_col)

        if period_defs.get("fy_cagr_label"):
            fy_cagr_col = f"{block_name}__{period_defs['fy_cagr_label']}"
            if fy_cagr_col in final.columns and len(fy_displayable_labels) >= 2:
                cols.append(fy_cagr_col)

        if cfg.get("show_ytd", False):
            for c in period_defs["ytd_labels"]:
                full_col = f"{block_name}__{c}"
                if full_col in final.columns:
                    cols.append(full_col)

            ytd_displayable_labels = get_displayable_section_labels(metric_name, "ytd", cfg, period_defs)

            if cfg.get("show_ytd_delta", False) and period_defs.get("ytd_delta_calc_label"):
                ytd_delta_col = f"{block_name}__{period_defs['ytd_delta_calc_label']}"
                if ytd_delta_col in final.columns and len(ytd_displayable_labels) >= 2:
                    cols.append(ytd_delta_col)

            if cfg.get("show_ytd_cagr", False) and period_defs.get("ytd_cagr_label"):
                ytd_cagr_col = f"{block_name}__{period_defs['ytd_cagr_label']}"
                if ytd_cagr_col in final.columns and len(ytd_displayable_labels) >= 2:
                    cols.append(ytd_cagr_col)

        if cfg.get("show_ltm", False):
            for c in period_defs["ltm_labels"]:
                full_col = f"{block_name}__{c}"
                if full_col in final.columns:
                    cols.append(full_col)

            ltm_displayable_labels = get_displayable_section_labels(metric_name, "ltm", cfg, period_defs)

            if cfg.get("show_ltm_delta", False) and period_defs.get("ltm_delta_calc_label"):
                ltm_delta_col = f"{block_name}__{period_defs['ltm_delta_calc_label']}"
                if ltm_delta_col in final.columns and len(ltm_displayable_labels) >= 2:
                    cols.append(ltm_delta_col)

            if cfg.get("show_ltm_cagr", False) and period_defs.get("ltm_cagr_label"):
                ltm_cagr_col = f"{block_name}__{period_defs['ltm_cagr_label']}"
                if ltm_cagr_col in final.columns and len(ltm_displayable_labels) >= 2:
                    cols.append(ltm_cagr_col)

        for p in period_defs["period_map"].keys():
            miss_col = f"{block_name}___missing_{p}"
            if miss_col in final.columns:
                cols.append(miss_col)

    cols = [c for c in cols if c in final.columns]
    return final[cols].copy()


# Skaliert Werte für die Anzeige, z. B. Amounts in kEUR und Raten in Prozent.
def convert_display_values(final: pd.DataFrame, period_defs: dict, cfg: dict, blocks: list[tuple[str, str]]) -> pd.DataFrame:
    out = final.copy()
    period_names = set(period_defs["period_map"].keys())
    normal_mask = ~out["row_type"].astype(str).str.lower().isin({"kpi", "kpi_section"})

    for block_name, metric_kind in blocks:
        for c in list(out.columns):
            if not c.startswith(f"{block_name}__"):
                continue

            raw_name = c.replace(f"{block_name}__", "", 1)
            if raw_name.startswith("_missing_"):
                continue

            # Amount-Spalten in kEUR
            if metric_kind == "amount" and raw_name in period_names:
                out.loc[normal_mask, c] = to_kEUR(out.loc[normal_mask, c])

            if metric_kind == "amount" and raw_name.startswith("Δ "):
                out.loc[normal_mask, c] = to_kEUR(out.loc[normal_mask, c])

            # Prozent-/Rate-Spalten auf Prozentpunkte skalieren
            is_percent_block = (metric_kind == "percent")
            is_cagr_col = raw_name.startswith("CAGR ")
            is_percent_delta = is_percent_block and raw_name.startswith("Δ ")

            if is_percent_block or is_cagr_col or is_percent_delta:
                out.loc[normal_mask, c] = pd.to_numeric(out.loc[normal_mask, c], errors="coerce") * 100

    return out


# Ermittelt anhand einer Spaltenposition den zugehörigen Kennzahlenblock.
def block_name_from_col(col_idx: int, block_ranges: list[tuple[str, int, int]]) -> str:
    for block_name, start, end in block_ranges:
        if start <= col_idx <= end:
            return block_name
    return ""


# Zerlegt eine Exportspalte in Blockname und Roh-Label.
def parse_export_column(col_name: str):
    """
    Zerlegt Exportspalten wie:
      Revenue__FY23A
      GP__Δ FY25A - FY24A
      GM__CAGR 23A-25A

    Rückgabe:
      (block_name, raw_label) oder (None, None)
    """
    col_name = str(col_name or "").strip()
    if "__" not in col_name:
        return None, None

    left, right = col_name.split("__", 1)
    return left, right


# Baut die finale Exporttabelle mit Technikspalten und sichtbaren Wertespalten.
def prepare_export_table(final: pd.DataFrame, blocks: list[tuple[str, str]], period_defs: dict, cfg: dict) -> pd.DataFrame:
    out = final.copy()
    export = pd.DataFrame()

    export["kEUR"] = out["label"]

    for block_name, metric_kind in blocks:
        metric_name = block_to_logical_metric(block_name)

        # FY-Perioden immer exportieren
        for c in period_defs["fy_labels"]:
            col = f"{block_name}__{c}"
            if col in out.columns:
                export[col] = out[col]

        fy_displayable_labels = get_displayable_section_labels(metric_name, "fy", cfg, period_defs)

        if period_defs.get("fy_delta_calc_label"):
            fy_delta_col = f"{block_name}__{period_defs['fy_delta_calc_label']}"
            if fy_delta_col in out.columns and len(fy_displayable_labels) >= 2:
                export[fy_delta_col] = out[fy_delta_col]

        if period_defs.get("fy_cagr_label"):
            fy_cagr_col = f"{block_name}__{period_defs['fy_cagr_label']}"
            if fy_cagr_col in out.columns and len(fy_displayable_labels) >= 2:
                export[fy_cagr_col] = out[fy_cagr_col]

        if cfg.get("show_ytd", False):
            for c in period_defs["ytd_labels"]:
                col = f"{block_name}__{c}"
                if col in out.columns:
                    export[col] = out[col]

            ytd_displayable_labels = get_displayable_section_labels(metric_name, "ytd", cfg, period_defs)

            if cfg.get("show_ytd_delta", False) and period_defs.get("ytd_delta_calc_label"):
                ytd_delta_col = f"{block_name}__{period_defs['ytd_delta_calc_label']}"
                if ytd_delta_col in out.columns and len(ytd_displayable_labels) >= 2:
                    export[ytd_delta_col] = out[ytd_delta_col]

            if cfg.get("show_ytd_cagr", False) and period_defs.get("ytd_cagr_label"):
                ytd_cagr_col = f"{block_name}__{period_defs['ytd_cagr_label']}"
                if ytd_cagr_col in out.columns and len(ytd_displayable_labels) >= 2:
                    export[ytd_cagr_col] = out[ytd_cagr_col]

        if cfg.get("show_ltm", False):
            for c in period_defs["ltm_labels"]:
                col = f"{block_name}__{c}"
                if col in out.columns:
                    export[col] = out[col]

            ltm_displayable_labels = get_displayable_section_labels(metric_name, "ltm", cfg, period_defs)

            if cfg.get("show_ltm_delta", False) and period_defs.get("ltm_delta_calc_label"):
                ltm_delta_col = f"{block_name}__{period_defs['ltm_delta_calc_label']}"
                if ltm_delta_col in out.columns and len(ltm_displayable_labels) >= 2:
                    export[ltm_delta_col] = out[ltm_delta_col]

            if cfg.get("show_ltm_cagr", False) and period_defs.get("ltm_cagr_label"):
                ltm_cagr_col = f"{block_name}__{period_defs['ltm_cagr_label']}"
                if ltm_cagr_col in out.columns and len(ltm_displayable_labels) >= 2:
                    export[ltm_cagr_col] = out[ltm_cagr_col]

    blank_row_types = {"reported", "recon"}
    blank_mask = out["row_type"].astype(str).str.lower().isin(blank_row_types)

    for c in export.columns:
        if c.startswith("__") or c == "kEUR":
            continue

        s = export[c].astype(object)

        s.loc[(~blank_mask) & s.isna()] = "n/a"
        s.loc[blank_mask & s.isna()] = None

        export[c] = s

    export["__level"] = out["level"]
    export["__ui_level"] = out["_ui_level"] if "_ui_level" in out.columns else out["level"]
    export["__row_type"] = out["row_type"]
    export["__key"] = out["key"]
    export["__parent_key"] = out["parent_key"]
    export["__orig_key"] = out["__orig_key"]

    return export


# Liest den aktuellen Layoutzustand des Report-Sheets inklusive sichtbarer und technischer Spalten.
def get_report_layout_context(ws, cfg: dict) -> dict:
    col_offset = int(cfg.get("excel_formatting", {}).get("col_offset", 3))
    table_left_col = 1 + col_offset
    label_col = table_left_col

    header_row_2 = None
    for r in range(1, min(ws.max_row, 50) + 1):
        if ws.cell(row=r, column=label_col).value == "kEUR":
            header_row_2 = r
            break

    if header_row_2 is None:
        raise ValueError("HEADER_ROW_2 mit 'kEUR' konnte im Report-Sheet nicht gefunden werden.")

    header_row_1 = header_row_2 - 1
    data_start_row = header_row_2 + 1
    table_right_col = ws.max_column

    export_headers = {}
    for c in range(table_left_col, table_right_col + 1):
        export_headers[c] = ws.cell(row=header_row_2, column=c).value

    def find_col(header_name: str) -> int:
        for col_idx, header in export_headers.items():
            if header == header_name:
                return col_idx
        raise ValueError(f"Technische Spalte '{header_name}' wurde im Report-Sheet nicht gefunden.")

    tech_cols = {
        "level": find_col("__level"),
        "ui_level": find_col("__ui_level"),
        "row_type": find_col("__row_type"),
        "key": find_col("__key"),
        "parent_key": find_col("__parent_key"),
        "orig_key": find_col("__orig_key"),
    }

    if "__orig_key" in export_headers.values():
        tech_cols["orig_key"] = find_col("__orig_key")

    first_tech_col = min(tech_cols.values())
    visible_right_col = first_tech_col - 1

    table_bottom_row = data_start_row - 1
    for r in range(ws.max_row, data_start_row - 1, -1):
        if ws.cell(row=r, column=tech_cols["row_type"]).value not in (None, ""):
            table_bottom_row = r
            break

    return {
        "HEADER_ROW_1": header_row_1,
        "HEADER_ROW_2": header_row_2,
        "DATA_START_ROW": data_start_row,
        "TABLE_LEFT_COL": table_left_col,
        "LABEL_COL": label_col,
        "TABLE_RIGHT_COL": table_right_col,
        "TABLE_BOTTOM_ROW": table_bottom_row,
        "VISIBLE_RIGHT_COL": visible_right_col,
        "TECH_COLS": tech_cols,
        "export_headers": export_headers,
        **_period_row_context(cfg),
    }


def _period_row_context(cfg: dict) -> dict:
    """Hidden rows for period bounds (same positions as format_hierarchy_report_excel)."""
    if not cfg.get("formula_mode", True):
        return {
            "PERIOD_YEAR_ROW": None,
            "PERIOD_START_ROW": None,
            "PERIOD_END_ROW": None,
        }
    extra = 1 if cfg.get("invoice_mapping_mode") == "year" else 2
    return {
        "PERIOD_YEAR_ROW": 3 if extra == 1 else None,
        "PERIOD_START_ROW": 3 if extra == 2 else None,
        "PERIOD_END_ROW": 4 if extra == 2 else None,
    }


# Zerlegt einen Hierarchie-Key in genau drei Ebenen (Legacy / interne Nutzung).
def split_key_to_abc(key: str) -> tuple[str | None, str | None, str | None]:
    a, b, c = split_key_to_n_parts(key, 3)
    return a, b, c


def split_key_to_n_parts(key: str, n: int) -> list[str | None]:
    """Erste n Segmente eines 'a | b | c'-Keys; fehlende Segmente werden None."""
    raw = [p.strip() for p in str(key or "").split(" | ")]
    out: list[str | None] = []
    for i in range(n):
        if i < len(raw) and raw[i] != "":
            out.append(raw[i])
        else:
            out.append(None)
    return out


# Befüllt die Hilfsspalten 1..n (n = len(group_cols)) für SUMIFS und setzt Header.
def write_formula_key_columns(ws, cfg: dict, ctx: dict):
    red_font = Font(name="GT Walsheim LC Light", size=8, color="FFFF5149")
    red_bold_font = Font(name="GT Walsheim LC Light", size=8, color="FFFF5149", bold=True)

    header_row_1 = ctx["HEADER_ROW_1"]
    header_row_2 = ctx["HEADER_ROW_2"]
    data_start_row = ctx["DATA_START_ROW"]
    table_bottom_row = ctx["TABLE_BOTTOM_ROW"]
    tech_cols = ctx["TECH_COLS"]

    group_cols = list(cfg["group_cols"])
    n = len(group_cols)
    if n < 1 or n > 3:
        raise ValueError("write_formula_key_columns: group_cols muss Länge 1..3 haben.")

    col_offset = int(cfg.get("excel_formatting", {}).get("col_offset", n))

    for c in range(1, col_offset + 1):
        ws.cell(row=header_row_1, column=c).value = ""

    for c in range(1, n + 1):
        ws.cell(row=header_row_2, column=c).value = group_cols[c - 1]

    for c in range(n + 1, col_offset + 1):
        ws.cell(row=header_row_2, column=c).value = None

    for c in range(1, n + 1):
        ws.cell(row=header_row_2, column=c).font = red_bold_font
        ws.cell(row=header_row_2, column=c).alignment = Alignment(horizontal="left", vertical="bottom", wrap_text=True)
        ws.column_dimensions[get_column_letter(c)].hidden = True
        ws.column_dimensions[get_column_letter(c)].outlineLevel = 1

    for c in range(n + 1, col_offset + 1):
        ws.column_dimensions[get_column_letter(c)].hidden = True
        ws.column_dimensions[get_column_letter(c)].outlineLevel = 1

    ws.column_dimensions[get_column_letter(col_offset + 1)].collapsed = True

    for r in range(data_start_row, table_bottom_row + 1):
        row_type = str(ws.cell(row=r, column=tech_cols["row_type"]).value or "").strip().lower()

        for c in range(1, col_offset + 1):
            ws.cell(row=r, column=c).font = red_font
            ws.cell(row=r, column=c).alignment = Alignment(horizontal="left", vertical="center")

        if row_type in {"total", "reported", "recon", "kpi", "kpi_section"}:
            for c in range(1, col_offset + 1):
                ws.cell(row=r, column=c).value = None
            continue

        if row_type == "other_detail":
            raw_key = ws.cell(row=r, column=tech_cols["orig_key"]).value
        else:
            raw_key = ws.cell(row=r, column=tech_cols["key"]).value

        parts = split_key_to_n_parts(str(raw_key or ""), n)
        for i, val in enumerate(parts, start=1):
            ws.cell(row=r, column=i).value = val


# Funktion, die überprüft ob eine salte schraffiert ist und dann keine Formeln einsetzt
def is_hatched(ws, col_idx: int, ctx: dict) -> bool:
    for r in range(ctx["DATA_START_ROW"], ctx["TABLE_BOTTOM_ROW"] + 1):
        fill = ws.cell(row=r, column=col_idx).fill
        if fill is None:
            continue

        pattern = str(fill.patternType or "").strip().lower()
        if pattern == "lightdown":
            return True

    return False


# Schreibt die versteckten Jahres- oder Datumswerte oben in die echten Periodenspalten.
def _write_period_bounds_to_columns(
    ws,
    cfg: dict,
    ctx: dict,
    col_indices: list[int],
    period_start,
    period_end,
    red_font: Font,
) -> None:
    for col_idx in col_indices:
        if cfg["invoice_mapping_mode"] == "year":
            year_row = ctx.get("PERIOD_YEAR_ROW")
            if year_row is None:
                continue
            cell = ws.cell(row=year_row, column=col_idx)
            cell.value = int(period_end.year)
            cell.font = red_font
            cell.alignment = Alignment(horizontal="right", vertical="center")
            cell.number_format = "0"
        else:
            start_row = ctx.get("PERIOD_START_ROW")
            end_row = ctx.get("PERIOD_END_ROW")
            if start_row is None or end_row is None:
                continue
            cell_start = ws.cell(row=start_row, column=col_idx)
            cell_end = ws.cell(row=end_row, column=col_idx)
            cell_start.value = pd.Timestamp(period_start).to_pydatetime()
            cell_end.value = pd.Timestamp(period_end).to_pydatetime()
            cell_start.font = red_font
            cell_end.font = red_font
            cell_start.alignment = Alignment(horizontal="right", vertical="center")
            cell_end.alignment = Alignment(horizontal="right", vertical="center")
            cell_start.number_format = "DD.MM.YYYY"
            cell_end.number_format = "DD.MM.YYYY"


def _gst_active_period_labels(export_columns: list[str], period_defs: dict) -> list[str]:
    """Period labels present in export columns, in sheet order (FY/YTD/LTM only)."""
    period_map = period_defs.get("period_map") or {}
    seen: set[str] = set()
    labels: list[str] = []
    for col_name in export_columns:
        _block, raw = parse_export_column(str(col_name or ""))
        if not raw or raw in seen or raw not in period_map:
            continue
        if raw.startswith("Δ ") or raw.startswith("CAGR "):
            continue
        seen.add(raw)
        labels.append(raw)
    return labels


def _gst_period_column_map(export_columns: list[str], table_left_col: int) -> dict[str, list[int]]:
    """Map period label → all export column indices (Revenue/GP/GM blocks)."""
    by_label: dict[str, list[int]] = {}
    for i, col_name in enumerate(export_columns):
        _block, raw_label = parse_export_column(col_name)
        if not raw_label:
            continue
        by_label.setdefault(raw_label, []).append(table_left_col + i)
    return by_label


def ensure_gst_period_bounds(
    ws,
    cfg: dict,
    period_defs: dict,
    export_columns: list[str],
    ctx: dict,
) -> None:
    """Write period start/end (or year) into each period column — same column as SUMIFS bounds."""
    red_font = Font(name="GT Walsheim LC Light", size=8, color="FFFF5149")
    period_map = period_defs.get("period_map") or {}
    col_map = _gst_period_column_map(export_columns, ctx["TABLE_LEFT_COL"])

    for period_label, bounds in period_map.items():
        col_indices = col_map.get(period_label, [])
        if not col_indices:
            continue
        period_start, period_end = bounds
        _write_period_bounds_to_columns(
            ws, cfg, ctx, col_indices, period_start, period_end, red_font,
        )


def write_period_helper_rows(ws, cfg: dict, period_defs: dict, export_headers: dict, LABEL_COL: int, VISIBLE_RIGHT_COL: int, NEG_COLOR: str, year_row=None, start_row=None, end_row=None):
    red_font = Font(name="GT Walsheim LC Light", size=8, color=NEG_COLOR)
    ctx = {
        "PERIOD_YEAR_ROW": year_row,
        "PERIOD_START_ROW": start_row,
        "PERIOD_END_ROW": end_row,
    }

    period_map = period_defs.get("period_map") or {}
    by_label: dict[str, list[int]] = {}
    for c in range(LABEL_COL + 1, VISIBLE_RIGHT_COL + 1):
        raw_export_header = export_headers.get(c)
        block_name, raw_label = parse_export_column(raw_export_header)
        if not raw_label and isinstance(raw_export_header, str):
            raw_label = raw_export_header.strip()
        if raw_label not in period_map:
            continue
        by_label.setdefault(raw_label, []).append(c)

    for period_label, col_indices in by_label.items():
        period_start, period_end = period_map[period_label]
        _write_period_bounds_to_columns(
            ws, cfg, ctx, col_indices, period_start, period_end, red_font,
        )


# Formula helpers
def excel_abs_ref(col_idx: int, row_idx: int) -> str:
    return f"${get_column_letter(col_idx)}${row_idx}"


def build_report_sum_formula(row_numbers: list[int], col_idx: int) -> str:
    return build_chunked_row_sum_formula(row_numbers, col_idx)


def build_ratio_formula(num_col_idx: int, den_col_idx: int, row_idx: int) -> str:
    num_ref = f"{get_column_letter(num_col_idx)}{row_idx}"
    den_ref = f"{get_column_letter(den_col_idx)}{row_idx}"
    return f'=IF(OR(COUNT({num_ref})=0,COUNT({den_ref})=0,{den_ref}=0),"n/a",({num_ref}/{den_ref})*100)'


def build_delta_formula(new_col_idx: int, old_col_idx: int, row_idx: int) -> str:
    new_ref = f"{get_column_letter(new_col_idx)}{row_idx}"
    old_ref = f"{get_column_letter(old_col_idx)}{row_idx}"
    return f'=IF(OR(COUNT({new_ref})=0,COUNT({old_ref})=0),"n/a",{new_ref}-{old_ref})'


def build_dynamic_cagr_formula(col_indices: list[int], row_idx: int) -> str:
    if len(col_indices) < 2:
        return '=""'

    refs = [f"{get_column_letter(c)}{row_idx}" for c in col_indices]

    candidates = []
    for i in range(len(refs) - 1):
        for j in range(len(refs) - 1, i, -1):
            start_ref = refs[i]
            end_ref = refs[j]
            years = j - i

            cond = f"AND(COUNT({start_ref})>0,COUNT({end_ref})>=0,{start_ref}>0,{end_ref}>=0)"
            expr = f"(({end_ref}/{start_ref})^(1/{years})-1)*100"
            candidates.append((cond, expr))

    formula = '"n/a"'
    for cond, expr in reversed(candidates):
        formula = f'IF({cond},{expr},{formula})'

    return f"={formula}"


# zur zahlenformatierung nach formeleinsatz
def apply_formula_result_number_format(cell, block_name: str, raw_label: str, row_type: str, period_defs: dict):
    fmt_money = '#,##0;(#,##0);"-"'
    fmt_number = '0.0;(0.0);"-"'

    row_type = str(row_type or "").strip().lower()

    if row_type == "kpi":
        cell.number_format = fmt_number
        return

    if raw_label.startswith("CAGR "):
        cell.number_format = fmt_number
        return

    if raw_label.startswith("Δ "):
        if block_name == "GM":
            cell.number_format = fmt_number
        else:
            cell.number_format = fmt_money
        return

    if raw_label in period_defs["period_map"]:
        if block_name == "GM":
            cell.number_format = fmt_number
        else:
            cell.number_format = fmt_money
        return


def build_kpi_growth_formula(prev_col_idx: int, curr_col_idx: int, source_row_idx: int) -> str:
    prev_ref = f"{get_column_letter(prev_col_idx)}{source_row_idx}"
    curr_ref = f"{get_column_letter(curr_col_idx)}{source_row_idx}"
    return f'=IF(OR(COUNT({prev_ref})=0,COUNT({curr_ref})=0,{prev_ref}=0),"n/a",(({curr_ref}/{prev_ref})-1)*100)'


# Perioden-/KPI mapping helper
def get_delta_labels(raw_label: str, period_defs: dict, cfg: dict):
    if raw_label == period_defs["fy_delta_calc_label"]:
        return period_defs["fy_labels"][-2], period_defs["fy_labels"][-1]

    if cfg.get("show_ytd", False) and raw_label == period_defs.get("ytd_delta_calc_label"):
        return period_defs["ytd_labels"][0], period_defs["ytd_labels"][1]

    if cfg.get("show_ltm", False) and raw_label == period_defs.get("ltm_delta_calc_label"):
        return period_defs["ltm_labels"][0], period_defs["ltm_labels"][1]

    return None


def get_cagr_labels(raw_label: str, period_defs: dict, cfg: dict):
    if period_defs.get("fy_cagr_label") and raw_label == period_defs["fy_cagr_label"]:
        return list(period_defs["fy_labels"])

    if cfg.get("show_ytd", False) and period_defs.get("ytd_cagr_label") and raw_label == period_defs["ytd_cagr_label"]:
        return list(period_defs["ytd_labels"])

    if cfg.get("show_ltm", False) and period_defs.get("ltm_cagr_label") and raw_label == period_defs["ltm_cagr_label"]:
        return list(period_defs["ltm_labels"])

    return None


def get_kpi_previous_label(raw_label: str, period_defs: dict, cfg: dict):
    if raw_label in period_defs["fy_labels"]:
        labels = period_defs["fy_labels"]
        idx = labels.index(raw_label)
        return None if idx == 0 else labels[idx - 1]

    if cfg.get("show_ytd", False) and raw_label in period_defs["ytd_labels"]:
        labels = period_defs["ytd_labels"]
        idx = labels.index(raw_label)
        return None if idx == 0 else labels[idx - 1]

    if cfg.get("show_ltm", False) and raw_label in period_defs["ltm_labels"]:
        labels = period_defs["ltm_labels"]
        idx = labels.index(raw_label)
        return None if idx == 0 else labels[idx - 1]

    return None


def build_leaf_amount_formula(
    *,
    ws_source,
    source_sheet_name: str,
    source_header_map: dict,
    cfg: dict,
    source_df: pd.DataFrame,
    period_defs: dict,
    block_name: str,
    period_label: str,
    row_idx: int,
    period_col_idx: int,
    filter_branches: list[list[tuple[str, str]]],
    ctx: dict,
    accrual_sum_ranges: dict | None = None,
) -> str:
    n = len(cfg["group_cols"])
    base_pairs = []
    for i in range(n):
        col_letter = get_column_letter(i + 1)
        grp_rng = source_range_ref(source_sheet_name, source_header_map, cfg["group_cols"][i])
        base_pairs.append((grp_rng, f"${col_letter}{row_idx}"))

    period_start, period_end = period_defs["period_map"][period_label]

    def build_body_for_value_col(value_col_name: str) -> str:
        sum_rng = source_range_ref(source_sheet_name, source_header_map, value_col_name)

        if cfg["calc_mode"] == "invoice":
            if cfg["invoice_mapping_mode"] == "date":
                invoice_rng = source_range_ref(source_sheet_name, source_header_map, cfg["invoice_col"])
                start_row = ctx.get("PERIOD_START_ROW")
                end_row = ctx.get("PERIOD_END_ROW")
                start_ref = excel_abs_ref(period_col_idx, start_row)
                end_ref = excel_abs_ref(period_col_idx, end_row)

                pairs = base_pairs + [
                    (invoice_rng, f'">="&{start_ref}'),
                    (invoice_rng, f'"<="&{end_ref}'),
                ]
            else:
                invoice_year_col = cfg.get("invoice_year_col", cfg["invoice_col"])
                invoice_year_rng = source_range_ref(source_sheet_name, source_header_map, invoice_year_col)
                year_row = ctx.get("PERIOD_YEAR_ROW")
                year_ref = excel_abs_ref(period_col_idx, year_row)

                pairs = base_pairs + [
                    (invoice_year_rng, year_ref),
                ]

            return build_sumifs_formula_body(
                sum_rng,
                pairs,
                filter_branches,
                divide_by_1000=True,
            )

        # Accrual mode: reuse the helper column + range that was materialized once
        # in apply_report_formulas. Falling back to per-cell creation only if the
        # cache is missing keeps this function safe to call standalone.
        cache_key = (period_label, value_col_name)
        if accrual_sum_ranges is not None and cache_key in accrual_sum_ranges:
            sum_rng = accrual_sum_ranges[cache_key]
        else:
            _helper_col, helper_idx = add_accrual_col_in_ws(
                ws_source=ws_source,
                source_df=source_df,
                value_col=value_col_name,
                invoice_col=cfg["invoice_col"],
                start_col=cfg["start_col"],
                end_col=cfg["end_col"],
                period_label=period_label,
                period_start=period_start,
                period_end=period_end,
            )
            sum_rng = source_column_range_ref(source_sheet_name, helper_idx)

        return build_sumifs_formula_body(
            sum_rng,
            base_pairs,
            filter_branches,
            divide_by_1000=True,
        )

    if block_name == "Revenue":
        body = build_body_for_value_col(cfg["value_cols"]["revenue"])
        return f'=IFERROR({body},"n/a")'

    if block_name == "GP":
        if cfg["profit_mode"] == "profit":
            body = build_body_for_value_col(cfg["value_cols"]["profit"])
            return f'=IFERROR({body},"n/a")'

        rev_body = build_body_for_value_col(cfg["value_cols"]["revenue"])
        cost_body = build_body_for_value_col(cfg["value_cols"]["cost"])
        return f'=IFERROR(({rev_body})-({cost_body}),"n/a")'

    raise ValueError(f"Unbekannter Amount-Block: {block_name}")


def collect_report_formula_context(ws, ctx: dict, cfg: dict) -> dict:
    tech_cols = ctx["TECH_COLS"]

    rows = []
    children_by_parent = {}
    top_row_by_key = {}
    total_leaf_rows = []

    max_level = len(cfg["group_cols"])

    total_row_idx = None
    reported_row_idx = None
    recon_row_idx = None

    special_row_types = {"total", "reported", "recon", "kpi", "kpi_section"}

    for r in range(ctx["DATA_START_ROW"], ctx["TABLE_BOTTOM_ROW"] + 1):
        level_val = ws.cell(row=r, column=tech_cols["level"]).value
        row_type = str(ws.cell(row=r, column=tech_cols["row_type"]).value or "").strip().lower()
        key = str(ws.cell(row=r, column=tech_cols["key"]).value or "").strip()
        parent_key = str(ws.cell(row=r, column=tech_cols["parent_key"]).value or "").strip()

        try:
            level = int(level_val)
        except Exception:
            level = None

        info = {
            "row_idx": r,
            "level": level,
            "row_type": row_type,
            "key": key,
            "parent_key": parent_key,
        }
        rows.append(info)

        if row_type == "total":
            total_row_idx = r
        elif row_type == "reported":
            reported_row_idx = r
        elif row_type == "recon":
            recon_row_idx = r

        if row_type not in special_row_types:
            if parent_key:
                children_by_parent.setdefault(parent_key, []).append(r)

            if level == 1:
                top_row_by_key[key] = r

            if level == max_level and row_type != "other_detail":
                total_leaf_rows.append(r)

    return {
        "rows": rows,
        "children_by_parent": children_by_parent,
        "top_row_by_key": top_row_by_key,
        "total_leaf_rows": total_leaf_rows,
        "total_row_idx": total_row_idx,
        "reported_row_idx": reported_row_idx,
        "recon_row_idx": recon_row_idx,
    }


def _warn_gst_formula_integrity(ws, label: str = "GST report") -> None:
    """Lightweight post-formula sanity check (logs only)."""
    n = 0
    for r in range(1, min(ws.max_row or 1, 3000) + 1):
        for c in range(1, min(ws.max_column or 1, 80) + 1):
            v = ws.cell(row=r, column=c).value
            if isinstance(v, str) and v.startswith("="):
                n += 1
                if "#REF!" in v.upper():
                    print(f"[WARN] {label}: broken formula (#REF!) at {get_column_letter(c)}{r}")
                    return
    if n == 0:
        print(f"[WARN] {label}: no formulas found on report sheet.")


def apply_report_formulas(
    wb,
    report_sheet_name: str,
    source_sheet_name: str,
    period_defs: dict,
    cfg: dict,
    blocks: list[tuple[str, str]],
    source_df: pd.DataFrame,
    export_columns: list[str],
):
    if not cfg.get("formula_mode", True):
        return

    ws = wb[report_sheet_name]
    ws_source = wb[source_sheet_name]

    # Align with TOP: flat source values before helper columns (avoids stale accrual headers).
    refresh_source_sheet_from_df(ws_source, source_df, cfg)

    text_helper_cols_by_index = ensure_text_filter_helper_columns_from_cfg_in_ws(
        ws_source=ws_source,
        source_df=source_df,
        cfg=cfg,
    )

    source_header_map = _get_sheet_header_map(ws_source)

    filter_branches = build_formula_filter_branches(
        cfg=cfg,
        source_sheet_name=source_sheet_name,
        source_header_map=source_header_map,
        text_helper_cols_by_index=text_helper_cols_by_index,
    )

    ctx = get_report_layout_context(ws, cfg)
    row_ctx = collect_report_formula_context(ws, ctx, cfg)

    ensure_gst_period_bounds(
        ws, cfg, period_defs, export_columns, ctx,
    )

    # Pre-materialize accrual helper columns ONCE per (period, value column).
    # Previously these were created inside the per-cell formula loop, so a large
    # source x many leaves x many periods caused thousands of full-source accrual
    # recomputes and header rescans (the 15+ min regression). The output formulas
    # are identical; only the helper-column creation moves out of the hot loop.
    accrual_sum_ranges: dict[tuple[str, str], str] = {}
    if cfg["calc_mode"] == "accrual":
        needed_value_cols: list[str] = []
        for vc in (
            cfg["value_cols"]["revenue"],
            cfg["value_cols"]["profit"] if cfg["profit_mode"] == "profit" else cfg["value_cols"]["cost"],
        ):
            if vc and vc not in needed_value_cols:
                needed_value_cols.append(vc)

        active_period_labels = _gst_active_period_labels(export_columns, period_defs)
        for period_label in active_period_labels:
            p_start, p_end = period_defs["period_map"][period_label]
            for vc in needed_value_cols:
                _helper_col, helper_idx = add_accrual_col_in_ws(
                    ws_source=ws_source,
                    source_df=source_df,
                    value_col=vc,
                    invoice_col=cfg["invoice_col"],
                    start_col=cfg["start_col"],
                    end_col=cfg["end_col"],
                    period_label=period_label,
                    period_start=p_start,
                    period_end=p_end,
                )
                accrual_sum_ranges[(period_label, vc)] = source_column_range_ref(
                    source_sheet_name, helper_idx
                )

    export_headers_raw = {
        ctx["TABLE_LEFT_COL"] + i: col_name
        for i, col_name in enumerate(export_columns)
    }

    export_col_map = {
        header: col_idx
        for col_idx, header in export_headers_raw.items()
        if header not in (None, "")
    }

    block_metric_map = dict(blocks)

    for col_idx in range(ctx["LABEL_COL"] + 1, ctx["VISIBLE_RIGHT_COL"] + 1):
        if is_hatched(ws, col_idx, ctx):
            if row_ctx["reported_row_idx"] is not None:
                ws.cell(row=row_ctx["reported_row_idx"], column=col_idx).value = None
            continue

        export_header = export_headers_raw.get(col_idx)
        block_name, raw_label = parse_export_column(export_header)

        if not block_name or not raw_label:
            continue

        if block_name not in block_metric_map:
            continue

        is_period_col = raw_label in period_defs["period_map"]
        delta_labels = get_delta_labels(raw_label, period_defs, cfg)
        cagr_labels = get_cagr_labels(raw_label, period_defs, cfg)

        for info in row_ctx["rows"]:
            r = info["row_idx"]
            row_type = info["row_type"]
            key = info["key"]

            cell = ws.cell(row=r, column=col_idx)

            if row_type == "reported":
                continue

            if row_type == "kpi_section":
                continue

            if is_period_col:
                if row_type == "recon":
                    if block_name in {"Revenue", "GP"} and row_ctx["reported_row_idx"] and row_ctx["total_row_idx"]:
                        rep_ref = f"{get_column_letter(col_idx)}{row_ctx['reported_row_idx']}"
                        tot_ref = f"{get_column_letter(col_idx)}{row_ctx['total_row_idx']}"
                        cell.value = f'=IF(OR(COUNT({rep_ref})=0,COUNT({tot_ref})=0),"",{rep_ref}-{tot_ref})'
                        apply_formula_result_number_format(cell, block_name, raw_label, row_type, period_defs)
                    continue

                if row_type == "kpi":
                    target_key = key.replace("__KPI_", "", 1)
                    prev_label = get_kpi_previous_label(raw_label, period_defs, cfg)
                    source_row_idx = row_ctx["top_row_by_key"].get(target_key)

                    if prev_label and source_row_idx:
                        prev_col_idx = export_col_map.get(f"{block_name}__{prev_label}")
                        curr_col_idx = export_col_map.get(f"{block_name}__{raw_label}")

                        if prev_col_idx and curr_col_idx:
                            cell.value = build_kpi_growth_formula(prev_col_idx, curr_col_idx, source_row_idx)
                            apply_formula_result_number_format(cell, block_name, raw_label, row_type, period_defs)
                    continue

                if block_name == "GM":
                    gp_col_idx = export_col_map.get(f"GP__{raw_label}")
                    rev_col_idx = export_col_map.get(f"Revenue__{raw_label}")

                    if gp_col_idx and rev_col_idx:
                        cell.value = build_ratio_formula(gp_col_idx, rev_col_idx, r)
                        apply_formula_result_number_format(cell, block_name, raw_label, row_type, period_defs)
                    continue

                if row_type == "total":
                    cell.value = build_report_sum_formula(row_ctx["total_leaf_rows"], col_idx)
                    apply_formula_result_number_format(cell, block_name, raw_label, row_type, period_defs)
                    continue

                child_rows = row_ctx["children_by_parent"].get(key, [])

                if child_rows:
                    cell.value = build_report_sum_formula(child_rows, col_idx)
                    apply_formula_result_number_format(cell, block_name, raw_label, row_type, period_defs)
                else:
                    cell.value = build_leaf_amount_formula(
                        ws_source=ws_source,
                        source_sheet_name=source_sheet_name,
                        source_header_map=source_header_map,
                        cfg=cfg,
                        source_df=source_df,
                        period_defs=period_defs,
                        block_name=block_name,
                        period_label=raw_label,
                        row_idx=r,
                        period_col_idx=col_idx,
                        filter_branches=filter_branches,
                        ctx=ctx,
                        accrual_sum_ranges=accrual_sum_ranges,
                    )
                    apply_formula_result_number_format(cell, block_name, raw_label, row_type, period_defs)

                continue

            if delta_labels:
                if row_type == "kpi":
                    cell.value = None
                    continue

                if row_type in {"recon", "kpi_section"}:
                    continue

                old_label, new_label = delta_labels
                old_col_idx = export_col_map.get(f"{block_name}__{old_label}")
                new_col_idx = export_col_map.get(f"{block_name}__{new_label}")

                if old_col_idx and new_col_idx:
                    cell.value = build_delta_formula(new_col_idx, old_col_idx, r)
                    apply_formula_result_number_format(cell, block_name, raw_label, row_type, period_defs)

                continue

            if cagr_labels:
                if row_type == "kpi":
                    cell.value = None
                    continue

                if row_type in {"recon", "kpi_section"}:
                    continue

                cagr_col_indices = [
                    export_col_map.get(f"{block_name}__{lbl}")
                    for lbl in cagr_labels
                ]
                cagr_col_indices = [idx for idx in cagr_col_indices if idx]

                if len(cagr_col_indices) >= 2:
                    cell.value = build_dynamic_cagr_formula(cagr_col_indices, r)
                    apply_formula_result_number_format(cell, block_name, raw_label, row_type, period_defs)

                continue

    _warn_gst_formula_integrity(ws)


# Bereitet das Report-Sheet für formula_mode vor: Source-Helper prüfen, Periodenzeilen einfügen und A-C befüllen.
def prepare_formula_mode_layout(
    wb,
    report_sheet_name: str,
    source_sheet_name: str,
    period_defs: dict,
    cfg: dict,
    source_df: pd.DataFrame,
):
    if source_sheet_name not in wb.sheetnames:
        raise ValueError(f"Source-Sheet '{source_sheet_name}' wurde nicht gefunden.")

    if report_sheet_name not in wb.sheetnames:
        raise ValueError(f"Report-Sheet '{report_sheet_name}' wurde nicht gefunden.")

    ws_source = wb[source_sheet_name]

    ensure_text_filter_helper_columns_from_cfg_in_ws(
        ws_source=ws_source,
        source_df=source_df,
        cfg=cfg,
    )

    ws = wb[report_sheet_name]

    ctx = get_report_layout_context(ws, cfg)

    write_formula_key_columns(
        ws=ws,
        cfg=cfg,
        ctx=ctx,
    )

    wb.calculation.forceFullCalc = True
    wb.calculation.fullCalcOnLoad = True
    wb.calculation.calcMode = "auto"


def format_hierarchy_report_excel(
    wb,
    target_sheet_name: str,
    period_defs: dict,
    cfg: dict,
    blocks: list[tuple[str, str]],
):
    if target_sheet_name not in wb.sheetnames:
        raise ValueError(f"Sheet '{target_sheet_name}' nicht gefunden.")

    ws = wb[target_sheet_name]

    row_offset = int(cfg.get("excel_formatting", {}).get("row_offset", 4))
    col_offset = int(cfg.get("excel_formatting", {}).get("col_offset", 3))
    width_cfg = dict(cfg.get("excel_formatting", {}).get("column_widths", {}) or {})

    extra_period_rows = 0
    if cfg.get("formula_mode", True):
        extra_period_rows = 1 if cfg["invoice_mapping_mode"] == "year" else 2

    ws.insert_rows(1, amount=row_offset + 1 + extra_period_rows)
    ws.insert_cols(1, amount=col_offset)

    PERIOD_YEAR_ROW = 3 if extra_period_rows == 1 else None
    PERIOD_START_ROW = 3 if extra_period_rows == 2 else None
    PERIOD_END_ROW = 4 if extra_period_rows == 2 else None

    COMPANY_ROW = 4 + extra_period_rows
    HEADER_ROW_1 = 5 + extra_period_rows
    HEADER_ROW_2 = 6 + extra_period_rows
    DATA_START_ROW = 7 + extra_period_rows

    TABLE_LEFT_COL = 1 + col_offset
    TABLE_RIGHT_COL = ws.max_column
    TABLE_BOTTOM_ROW = ws.max_row

    LABEL_COL = TABLE_LEFT_COL

    fmt_money = '#,##0;(#,##0);"-"'
    fmt_number = '0.0;(0.0);"-"'

    base_font = THEME.font_base
    bold_font = THEME.font_bold
    header_fill = THEME.fill_header
    white_fill = THEME.fill_white
    fill_subtotal = THEME.fill_subtotal

    dashed = Side(style="dashed", color=THEME.border_color)
    border_subtotal_top = THEME.border_subtotal_top
    border_header_bottom = THEME.border_header_bottom
    border_kpi_top = THEME.border_kpi_section_top

    POS_COLOR = THEME.delta_positive
    NEG_COLOR = THEME.delta_negative
    GREY_TEXT = THEME.delta_zero
    CAGR_TEXT = THEME.text_cagr
    BLUE_TEXT = THEME.text_brand_title

    # Grundformat
    paint_rows = ws.max_row + 50
    paint_cols = ws.max_column + 50

    for r in range(1, paint_rows + 1):
        for c in range(1, paint_cols + 1):
            ws.cell(row=r, column=c).font = base_font
            ws.cell(row=r, column=c).fill = white_fill

    for r in range(1, paint_rows + 1):
        for c in range(1, 1 + col_offset):
            ws.cell(row=r, column=c).fill = THEME.fill_tech

    ws.sheet_properties.outlinePr.summaryRight = True
    ws.sheet_properties.outlinePr.summaryBelow = True
    for c in range(1, 1 + col_offset):
        letter = get_column_letter(c)
        ws.column_dimensions[letter].outlineLevel = 1
        ws.column_dimensions[letter].hidden = True
    ws.column_dimensions[get_column_letter(1 + col_offset)].collapsed = True

    # Titel
    title = str(cfg.get("title", "")).strip()
    table = str(cfg.get("table", "")).strip()
    company = str(cfg.get("company", "")).strip()
    subtitle_suffix = str(cfg.get("subtitle_suffix", "")).strip()

    company_line = company
    if subtitle_suffix:
        company_line = f"{company} | {subtitle_suffix}" if company else subtitle_suffix

    ws.cell(row=1, column=LABEL_COL).value = title
    ws.cell(row=1, column=LABEL_COL).font = Font(
        name=THEME.font_name, size=THEME.font_size_title, color=BLUE_TEXT
    )

    ws.cell(row=2, column=LABEL_COL).value = table
    ws.cell(row=2, column=LABEL_COL).font = Font(
        name=THEME.font_name, size=THEME.font_size_subtitle, color=BLUE_TEXT
    )

    ws.cell(row=COMPANY_ROW, column=LABEL_COL).value = company_line
    ws.cell(row=COMPANY_ROW, column=LABEL_COL).font = Font(
        name=THEME.font_name, size=THEME.font_size_company, color=BLUE_TEXT, bold=True
    )

    ws.row_dimensions[HEADER_ROW_1].height = 12
    ws.row_dimensions[HEADER_ROW_2].height = 12

    # --- echte Exportspalten aus HEADER_ROW_2 lesen ---
    export_headers = {}
    for c in range(TABLE_LEFT_COL, TABLE_RIGHT_COL + 1):
        export_headers[c] = ws.cell(row=HEADER_ROW_2, column=c).value

    def find_col(header_name: str) -> int:
        for col_idx, header in export_headers.items():
            if header == header_name:
                return col_idx
        raise ValueError(f"Technische Spalte '{header_name}' nicht gefunden.")

    TECH_COLS = {
        "level": find_col("__level"),
        "ui_level": find_col("__ui_level"),
        "row_type": find_col("__row_type"),
        "key": find_col("__key"),
        "parent_key": find_col("__parent_key"),
        "orig_key": find_col("__orig_key"),
    }

    FIRST_TECH_COL = min(TECH_COLS.values())
    VISIBLE_RIGHT_COL = FIRST_TECH_COL - 1

    if extra_period_rows > 0:
        helper_rows = [PERIOD_YEAR_ROW] if cfg["invoice_mapping_mode"] == "year" else [PERIOD_START_ROW, PERIOD_END_ROW]

        for r in helper_rows:
            ws.row_dimensions[r].hidden = True
            ws.row_dimensions[r].outlineLevel = 1
            ws.row_dimensions[r].collapsed = False
            ws.row_dimensions[r].height = 12

        ws.row_dimensions[helper_rows[-1]].collapsed = True

        write_period_helper_rows(
            ws=ws,
            cfg=cfg,
            period_defs=period_defs,
            export_headers=export_headers,
            LABEL_COL=LABEL_COL,
            VISIBLE_RIGHT_COL=VISIBLE_RIGHT_COL,
            NEG_COLOR=NEG_COLOR,
            year_row=PERIOD_YEAR_ROW,
            start_row=PERIOD_START_ROW,
            end_row=PERIOD_END_ROW,
        )

    delta_cols = []
    cagr_cols = []
    dashed_after_cols = []
    block_ranges = []

    # Technische Spalten / Labelspalte
    ws.cell(row=HEADER_ROW_1, column=LABEL_COL).value = ""
    ws.cell(row=HEADER_ROW_2, column=LABEL_COL).value = "kEUR"

    # Mapping Blocktitel
    block_title_map = {
        "Revenue": "Gross sales",
        "GP": "Gross profit",
        "GM": "Gross margin",
    }

    # Nur echte Datenblöcke nach LABEL_COL auswerten
    current_block = None
    current_block_start = None

    for c in range(LABEL_COL + 1, VISIBLE_RIGHT_COL + 1):
        raw_export_header = export_headers.get(c)
        block_name, raw_label = parse_export_column(raw_export_header)

        if not block_name or not raw_label:
            ws.cell(row=HEADER_ROW_1, column=c).value = ""
            continue

        # Neuer Block
        if block_name != current_block:
            if current_block is not None:
                block_ranges.append((current_block, current_block_start, c - 1))
                dashed_after_cols.append(c - 1)

            current_block = block_name
            current_block_start = c

        if raw_label.startswith("CAGR "):
            ws.cell(row=HEADER_ROW_1, column=c).value = "CAGR"
            ws.cell(row=HEADER_ROW_1, column=c).alignment = Alignment(horizontal="right", vertical="bottom", wrap_text=True)
            ws.cell(row=HEADER_ROW_2, column=c).value = get_cagr_display_text_for_block(
                ws=ws,
                block_name=block_name,
                raw_label=raw_label,
                export_headers=export_headers,
                data_start_row=DATA_START_ROW,
                table_bottom_row=TABLE_BOTTOM_ROW,
                tech_cols=TECH_COLS,
                period_defs=period_defs,
                cfg=cfg,
            )
            cagr_cols.append(c)
        else:
            ws.cell(row=HEADER_ROW_1, column=c).value = block_title_map.get(block_name, block_name)
            ws.cell(row=HEADER_ROW_2, column=c).value = raw_label

        if raw_label.startswith("Δ "):
            delta_cols.append(c)

    if current_block is not None:
        block_ranges.append((current_block, current_block_start, VISIBLE_RIGHT_COL))

    if dashed_after_cols:
        dashed_after_cols = dashed_after_cols[:-1]  # nach letztem Block keine Trennlinien

    for r in [HEADER_ROW_1, HEADER_ROW_2]:
        for c in range(LABEL_COL, VISIBLE_RIGHT_COL + 1):
            cell = ws.cell(row=r, column=c)
            cell.fill = header_fill
            cell.font = THEME.font_header
    for c in range(LABEL_COL, VISIBLE_RIGHT_COL + 1):
        ws.cell(row=HEADER_ROW_2, column=c).border = border_header_bottom

    # Merge nur für zusammenhängende gleiche Überschriften in Header Row 1
    start_c = LABEL_COL + 1
    while start_c <= VISIBLE_RIGHT_COL:
        val = ws.cell(row=HEADER_ROW_1, column=start_c).value
        end_c = start_c
        while end_c + 1 <= VISIBLE_RIGHT_COL and ws.cell(row=HEADER_ROW_1, column=end_c + 1).value == val:
            end_c += 1

        if val not in (None, "") and end_c > start_c:
            ws.merge_cells(start_row=HEADER_ROW_1, start_column=start_c, end_row=HEADER_ROW_1, end_column=end_c)

        start_c = end_c + 1

    for c in range(LABEL_COL, VISIBLE_RIGHT_COL + 1):
        ws.cell(row=HEADER_ROW_1, column=c).alignment = Alignment(horizontal="center", vertical="bottom", wrap_text=True)
        ws.cell(row=HEADER_ROW_2, column=c).alignment = Alignment(
            horizontal="left" if c == LABEL_COL else "right",
            vertical="bottom",
            wrap_text=True
        )

    group_count = len(cfg.get("group_cols") or [])

    for r in range(DATA_START_ROW, TABLE_BOTTOM_ROW + 1):
        ws.row_dimensions[r].height = 12

        level = ws.cell(row=r, column=TECH_COLS["level"]).value
        ui_level = ws.cell(row=r, column=TECH_COLS["ui_level"]).value
        row_type = ws.cell(row=r, column=TECH_COLS["row_type"]).value
        label_cell = ws.cell(row=r, column=LABEL_COL)

        try:
            level = int(level)
        except Exception:
            level = None

        try:
            ui_level = int(ui_level)
        except Exception:
            ui_level = level if level is not None else 1

        row_type = str(row_type).strip().lower() if row_type is not None else ""

        hier_block = row_type not in {"total", "reported", "recon", "kpi", "kpi_section"}

        for c in range(LABEL_COL, VISIBLE_RIGHT_COL + 1):
            ws.cell(row=r, column=c).alignment = Alignment(
                horizontal="left" if c == LABEL_COL else "right",
                vertical="center"
            )

        is_total = row_type == "total"
        is_kpi = row_type == "kpi"
        is_kpi_section = row_type == "kpi_section"
        is_reported = row_type == "reported"
        is_recon = row_type == "recon"

        is_level1_subtotal = hier_block and level == 1

        if is_level1_subtotal:
            for c in range(LABEL_COL, VISIBLE_RIGHT_COL + 1):
                cell = ws.cell(row=r, column=c)
                cell.fill = fill_subtotal
                cell.font = bold_font
                cell.border = border_subtotal_top

        if is_total:
            for c in range(LABEL_COL, VISIBLE_RIGHT_COL + 1):
                cell = ws.cell(row=r, column=c)
                cell.fill = fill_subtotal
                cell.font = bold_font
                cell.border = border_subtotal_top

        if is_reported:
            for c in range(LABEL_COL, VISIBLE_RIGHT_COL + 1):
                cell = ws.cell(row=r, column=c)
                cell.fill = fill_subtotal
                cell.font = bold_font

        if is_recon:
            for c in range(LABEL_COL, VISIBLE_RIGHT_COL + 1):
                cell = ws.cell(row=r, column=c)
                cell.fill = white_fill
                cell.font = base_font

        if is_kpi:
            for c in range(LABEL_COL, VISIBLE_RIGHT_COL + 1):
                ws.cell(row=r, column=c).fill = header_fill

        if is_kpi_section:
            for c in range(LABEL_COL, VISIBLE_RIGHT_COL + 1):
                cell = ws.cell(row=r, column=c)
                cell.fill = header_fill
                cell.border = border_kpi_top
                if c != LABEL_COL:
                    cell.value = None
            label_cell.font = Font(
                name=THEME.font_name, size=THEME.font_size, bold=True, color=THEME.text_brand_title
            )

        if (
            hier_block
            and not is_kpi_section
            and level is not None
            and group_count >= 2
            and 1 <= level < group_count
        ):
            for c in range(LABEL_COL, VISIBLE_RIGHT_COL + 1):
                ws.cell(row=r, column=c).font = bold_font

        indent = 0
        if group_count >= 3 and ui_level is not None and ui_level >= 3:
            indent = max(1, ui_level - 2)
        elif group_count == 2 and level == 2:
            try:
                uiv = int(ui_level)
            except (TypeError, ValueError):
                uiv = 0
            if uiv >= 2:
                indent = 1

        if row_type == "other_detail":
            indent += 1

        if indent > 0:
            label_cell.alignment = Alignment(
                horizontal="left",
                vertical="center",
                indent=indent
            )

        if is_kpi:
            label_cell.font = Font(
                name=THEME.font_name, size=THEME.font_size, italic=True, color=THEME.text_kpi
            )

        if row_type == "other_detail":
            label_cell.font = Font(
                name=THEME.font_name, size=THEME.font_size, color=THEME.text_account
            )

        for c in cagr_cols:
            if hier_block and not is_total and not is_kpi_section and not is_recon:
                ws.cell(row=r, column=c).fill = header_fill

        # GM period/delta columns: white like Revenue/GP; only CAGR columns use header_fill above.
        if hier_block and not is_level1_subtotal and not is_total and not is_reported and not is_kpi and not is_kpi_section and not is_recon:
            for block_name, start, end in block_ranges:
                if block_name != "GM":
                    continue
                for c in range(start, end + 1):
                    if c not in cagr_cols:
                        ws.cell(row=r, column=c).fill = white_fill

        for c in range(LABEL_COL + 1, VISIBLE_RIGHT_COL + 1):
            cell = ws.cell(row=r, column=c)
            block_name = block_name_from_col(c, block_ranges)

            if isinstance(cell.value, (int, float)):
                is_kpi_row = (row_type == "kpi")
                is_gm_block = (block_name == "GM")
                is_cagr_col = (c in cagr_cols)
                is_delta_col = (c in delta_cols)

                if is_kpi_row:
                    cell.number_format = fmt_number
                elif is_gm_block or is_cagr_col:
                    cell.number_format = fmt_number
                else:
                    cell.number_format = fmt_money

                if is_delta_col:
                    f = copy(cell.font)
                    if cell.value > 0:
                        f.color = POS_COLOR
                    elif cell.value < 0:
                        f.color = NEG_COLOR
                    else:
                        f.color = GREY_TEXT
                    cell.font = f

                elif is_cagr_col:
                    f = copy(cell.font)
                    f.color = CAGR_TEXT
                    cell.font = f

                elif isinstance(cell.value, str) and cell.value.lower() == "n/a":
                    f = copy(cell.font)
                    f.color = GREY_TEXT
                    cell.font = f

        # Outline
        other_parent_keys_with_hidden_children = set()

    for r in range(DATA_START_ROW, TABLE_BOTTOM_ROW + 1):
        rt = str(ws.cell(row=r, column=TECH_COLS["row_type"]).value or "").strip().lower()
        pk = str(ws.cell(row=r, column=TECH_COLS["parent_key"]).value or "").strip()

        if rt == "other_detail" and pk:
            other_parent_keys_with_hidden_children.add(pk)

    for r in range(DATA_START_ROW, TABLE_BOTTOM_ROW + 1):
        level = ws.cell(row=r, column=TECH_COLS["level"]).value
        ui_level = ws.cell(row=r, column=TECH_COLS["ui_level"]).value
        row_type = str(ws.cell(row=r, column=TECH_COLS["row_type"]).value or "").strip().lower()
        key = str(ws.cell(row=r, column=TECH_COLS["key"]).value or "").strip()

        try:
            level = int(level)
        except Exception:
            level = None

        try:
            ui_level = int(ui_level)
        except Exception:
            ui_level = level if level is not None else 1

        rd = ws.row_dimensions[r]
        rd.collapsed = False

        if row_type in {"total", "reported", "recon", "kpi", "kpi_section"}:
            rd.outlineLevel = 0
            rd.hidden = False
        else:
            rd.outlineLevel = max(0, min(7, int(ui_level) - 1))
            rd.hidden = (row_type == "other_detail")

            if row_type == "other" and key in other_parent_keys_with_hidden_children:
                rd.collapsed = True

    # gestrichelte Blocktrennung
    for r in range(HEADER_ROW_1, TABLE_BOTTOM_ROW + 1):
        for c in dashed_after_cols:
            cell = ws.cell(row=r, column=c)
            b = copy(cell.border)
            cell.border = Border(
                left=b.left,
                right=dashed,
                top=b.top,
                bottom=b.bottom
            )

    # Schraffur
    hatch_fill = PatternFill(
        patternType="lightDown",
        fgColor="FFBDBDBD",
        bgColor="FFFFFFFF"
    )

    if cfg.get("hatch_only_fy_columns", False):
        hatch_period_set = set(period_defs["fy_labels"])
    else:
        hatch_period_set = set(period_defs["period_map"].keys())

    decision_excluded_row_types = {"kpi", "kpi_section", "reported", "recon"}
    apply_excluded_row_types = {"kpi", "kpi_section"}

    for c in range(LABEL_COL + 1, VISIBLE_RIGHT_COL + 1):
        header2 = ws.cell(row=HEADER_ROW_2, column=c).value
        if header2 not in hatch_period_set:
            continue

        values = []
        for r in range(DATA_START_ROW, TABLE_BOTTOM_ROW + 1):
            row_type = str(ws.cell(row=r, column=TECH_COLS["row_type"]).value or "").strip().lower()
            if row_type in decision_excluded_row_types:
                continue
            values.append(ws.cell(row=r, column=c).value)

        if len(values) > 0 and all(is_hatch_empty_value(v) for v in values):
            for r in range(DATA_START_ROW, TABLE_BOTTOM_ROW + 1):
                row_type = str(ws.cell(row=r, column=TECH_COLS["row_type"]).value or "").strip().lower()
                if row_type in apply_excluded_row_types:
                    continue
                cell = ws.cell(row=r, column=c)
                cell.fill = hatch_fill
                if is_hatch_empty_value(cell.value):
                    cell.value = None

    total_row_idx = None
    reported_row_idx = None
    recon_row_idx = None

    for r in range(DATA_START_ROW, TABLE_BOTTOM_ROW + 1):
        rt = str(ws.cell(row=r, column=TECH_COLS["row_type"]).value or "").strip().lower()
        if rt == "total" and total_row_idx is None:
            total_row_idx = r
        elif rt == "reported":
            reported_row_idx = r
        elif rt == "recon":
            recon_row_idx = r

    if total_row_idx is not None and reported_row_idx is not None and recon_row_idx is not None:
        period_set = set(period_defs["period_map"].keys())

        for c in range(LABEL_COL + 1, VISIBLE_RIGHT_COL + 1):
            raw_export_header = export_headers.get(c)
            block_name, raw_label = parse_export_column(raw_export_header)

            if block_name not in {"Revenue", "GP"}:
                continue
            if raw_label not in period_set:
                continue

            total_val = ws.cell(row=total_row_idx, column=c).value
            reported_val = ws.cell(row=reported_row_idx, column=c).value

            if isinstance(total_val, (int, float)) and isinstance(reported_val, (int, float)):
                col_letter = get_column_letter(c)
                formula_cell = ws.cell(row=recon_row_idx, column=c)
                formula_cell.value = f"={col_letter}{reported_row_idx}-{col_letter}{total_row_idx}"
                formula_cell.number_format = fmt_money
                formula_cell.alignment = Alignment(horizontal="right", vertical="center")
            else:
                ws.cell(row=recon_row_idx, column=c).value = None

    fy_set = set(period_defs["fy_labels"])
    ytd_set = set(period_defs["ytd_labels"])
    ltm_set = set(period_defs["ltm_labels"])

    for c in range(LABEL_COL, VISIBLE_RIGHT_COL + 1):
        header2 = ws.cell(row=HEADER_ROW_2, column=c).value
        letter = get_column_letter(c)

        if c == LABEL_COL:
            ws.column_dimensions[letter].width = float(width_cfg["label"])
        elif header2 in fy_set:
            ws.column_dimensions[letter].width = float(width_cfg["fy"])
        elif header2 in ytd_set:
            ws.column_dimensions[letter].width = float(width_cfg["ytd"])
        elif header2 in ltm_set:
            ws.column_dimensions[letter].width = float(width_cfg["ltm"])
        elif c in delta_cols:
            ws.column_dimensions[letter].width = float(width_cfg["delta"])
        elif c in cagr_cols:
            ws.column_dimensions[letter].width = float(width_cfg["cagr"])
        else:
            ws.column_dimensions[letter].width = float(width_cfg["other_value"])

    # Versteckt technische Spalten; geleert werden sie nur ohne formula_mode sofort.
    for c in TECH_COLS.values():
        ws.column_dimensions[get_column_letter(c)].hidden = True

    if not cfg.get("formula_mode", True):
        for c in TECH_COLS.values():
            for r in range(1, TABLE_BOTTOM_ROW + 1):
                ws.cell(row=r, column=c).value = None


def main():
    cfg = normalize_config(CONFIG)

    df = pd.read_excel(
        cfg["file_path"],
        sheet_name=cfg["sheet_name"],
        engine="openpyxl"
    )

    period_defs = build_period_definitions(cfg)
    d = preprocess_input(df, cfg)

    final, blocks = build_final_table(d, period_defs, cfg)

    final = arrange_columns(final, period_defs, cfg, blocks)
    final = convert_display_values(final, period_defs, cfg, blocks)

    export_df = prepare_export_table(final, blocks, period_defs, cfg)

    output_dir = cfg["output_file_path"]
    os.makedirs(output_dir, exist_ok=True)

    output_path = build_output_file_path(cfg)
    ensure_output_writable(output_path)

    source_sheet_name = ensure_source_sheet_in_output(cfg, output_path)

    wb = load_workbook(output_path)

    target_sheet_name = get_next_sheet_name_from_wb(
        wb,
        cfg.get("base_sheet_name", "hierarchy_report")
    )

    write_export_df_to_sheet(wb, export_df, target_sheet_name)

    print(f"Excel erfolgreich geschrieben: {output_path}")
    print(f"Neues Sheet: {target_sheet_name}")

    format_hierarchy_report_excel(
        wb=wb,
        target_sheet_name=target_sheet_name,
        period_defs=period_defs,
        cfg=cfg,
        blocks=blocks,
    )

    print("Formatierung angewendet.")

    if cfg.get("formula_mode", True):
        prepare_formula_mode_layout(
            wb=wb,
            report_sheet_name=target_sheet_name,
            source_sheet_name=source_sheet_name,
            period_defs=period_defs,
            cfg=cfg,
            source_df=df.copy(),
        )

        print("Formula-Mode Layout vorbereitet.")

        apply_report_formulas(
            wb=wb,
            report_sheet_name=target_sheet_name,
            source_sheet_name=source_sheet_name,
            period_defs=period_defs,
            cfg=cfg,
            blocks=blocks,
            source_df=df.copy(),
            export_columns=export_df.columns.tolist(),
        )

        print("Formeln eingesetzt.")

    wb.save(output_path)
    wb.close()


if __name__ == "__main__":
    main()
