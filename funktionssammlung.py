from __future__ import annotations
import os
import pandas as pd
import numpy as np

from typing import Any, Dict, List, Optional, Tuple
from matplotlib.colors import to_rgba
from openpyxl import load_workbook

RGBA = Tuple[float, float, float, float]

# ============================================================
# FARBCONFIG für Parent CHILD bzw. auch ohne Hierarchie für alles Graphen, dei Farben nutzen
# ============================================================


#Hier helpers um Farben schreiben zu können als Farbe + 20%heller bspw.
def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.strip().lstrip("#")
    if len(h) != 6:
        raise ValueError(f"Ungültiger Hexcode: {hex_color}")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02X}{g:02X}{b:02X}"

def _mix_rgb(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    # t=0 -> a, t=1 -> b
    r = round(a[0] + (b[0] - a[0]) * t)
    g = round(a[1] + (b[1] - a[1]) * t)
    b_ = round(a[2] + (b[2] - a[2]) * t)
    return (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b_)))

def tint(hex_color: str, t: float) -> str:
    """
    t in [-1..1]
    t > 0 : heller (Richtung Weiß)
    t < 0 : dunkler (Richtung Schwarz)
    t = 0 : unverändert
    """
    t = float(t)
    if t > 1 or t < -1:
        raise ValueError(f"t muss in [-1..1] sein, bekommen: {t}")

    base = _hex_to_rgb(hex_color)
    target = (255, 255, 255) if t >= 0 else (0, 0, 0)
    out = _mix_rgb(base, target, abs(t))
    return _rgb_to_hex(*out)

def resolve_color_expr(expr: str) -> str:
    """
    Unterstützt:
        - "#RRGGBB" -> 그대로
        - "#RRGGBB@0.2" -> tint(..., +0.2)
        - "#RRGGBB@-0.2" -> tint(..., -0.2)
    """
    s = str(expr).strip()
    if "@" not in s:
        # already a hex
        return s.upper()

    hex_part, t_part = s.split("@", 1)
    hex_part = hex_part.strip()
    t_part = t_part.strip().replace(",", ".")  # falls du "0,2" schreibst
    return tint(hex_part, float(t_part)).upper()

# 1) FALL: KEINE HIERARCHIE (0/1 group_cols)
# Reihenfolge: Shade-Stufe → Über alle Parents
FLAT_COLORS_HEX: List[str] = [

    "#4F2D7F@0.0",
    "#FFC23D@0.0",
    "#FF5149@0.0",
    "#00A4B3@0.0",
    "#CCC4BD@0.0",
    "#7F7F7F@0.5",

    "#4F2D7F@0.8",
    "#FFC23D@0.6",
    "#FF5149@0.4",
    "#00A4B3@0.4",
    "#F2F0EE@-0.75",
    "#7F7F7F@0.25",

    "#4F2D7F@0.4",
    "#FFC23D@-0.5",
    "#FF5149@-0.5",
    "#00A4B3@-0.5",
    "#CCC4BD@-0.4",
    "#BFBFBF@-0.25",

    "#4F2D7F@0.6",
    "#FFC23D@0.4",
    "#FF5149@0.6",
    "#00A4B3@0.8",
    "#CCC4BD@-0.25",
    "#BFBFBF@-0.5",

    "#4F2D7F@-0.5",
    "#FFC23D@-0.25",
    "#FF5149@-0.25",
    "#00A4B3@-0.25",
    "#CCC4BD@-0.5",
    "#BFBFBF@-0.35",

    "#4F2D7F@0.2",
    "#FFC23D@0.2",
    "#FF5149@0.8",
    "#00A4B3@0.6",
    "#F2F0EE@-0.1",
    "#7F7F7F@0.35",
]

# 2) FALL: HIERARCHIE (2 group_cols) → bis zu 6 Parents, pro Parent bis zu 6 Children
# Idee: pro Parent eine "Familie" aus ähnlichen Shades (leicht unterschiedliche tint-Werte)
HIERARCHY_COLORS_HEX: List[List[str]] = [
    # Parent 1 — Lila (#4F2D7F)
    ["#4F2D7F@0.8", "#4F2D7F@0.6", "#4F2D7F@0.5", "#4F2D7F@0.4", "#4F2D7F@0.2", "#4F2D7F@-0.25"],
    # Parent 2 — Gelb (#FFC23D)
    ["#FFC23D@0.0", "#FFC23D@0.6", "#FFC23D@0.4", "#FFC23D@0.2", "#FFC23D@-0.25", "#FFC23D@-0.5"],
    # Parent 3 — Rot (#FF5149)
    ["#FF5149@0.0", "#FF5149@0.8", "#FF5149@0.6", "#FF5149@0.4", "#FF5149@0.2", "#FF5149@-0.25"],
    # Parent 4 — Türkis (#00A4B3)
    ["#00A4B3@0.0", "#00A4B3@0.8", "#00A4B3@0.6", "#00A4B3@0.4", "#00A4B3@-0.25", "#00A4B3@-0.5"],
    # Parent 5 — Greige (#CCC4BD / #F2F0EE)
    ["#CCC4BD@0.0", "#CCC4BD@0.75", "#CCC4BD@0.4", "#CCC4BD@-0.25", "#CCC4BD@-0.5", "#F2F0EE@-0.1"],
    # Parent 6 — Grau (#7F7F7F / #BFBFBF)
    ["#7F7F7F@0.5", "#7F7F7F@0.25", "#BFBFBF@-0.25", "#BFBFBF@-0.5", "#BFBFBF@-0.35", "#7F7F7F@0.35"],
]

def pick_colors_hardcoded(
    group_keys: List[str],
    group_cols: List[str],
    *,
    max_bubbles: int = 36,
    max_parents: int = 6,
    max_children_per_parent: int = 6,
) -> Dict[str, RGBA]:
    """
    Einzige Funktion, die du importierst.
    Gibt dict: group_key -> RGBA (matplotlib-kompatibel).

    - group_cols <= 1:
        Bubble i -> FLAT_COLORS_HEX[i]
    - group_cols == 2:
        Erwartet group_keys im Format "Parent | Child" (so wie du es baust).
        Parent-Reihenfolge = first-seen order (im Top-N Output).
        Child-Reihenfolge je Parent = first-seen order.
        Child 1..6 bekommen pro Parent die Shades aus HIERARCHY_COLORS_HEX[parent_index][child_index].

    Fallbacks:
    - Mehr als max_parents Parents -> fallback auf Flat_COLORS_HEX (nach globaler Reihenfolge)
    - Mehr als max_children_per_parent Children in einem Parent -> fallback auf Parent-Child[0] (oder fallback color)
    - Zu wenig definierte Farben -> ValueError (damit es nicht "stumm" falsch wird)
    """

    # --------------------
    # kleine inline helpers
    # --------------------
    def _stable_unique(seq: List[str]) -> List[str]:
        seen = set()
        out: List[str] = []
        for x in seq:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    def _split_parent_child(key: str) -> Tuple[str, str]:
        key = str(key)
        if " | " in key:
            p, c = key.split(" | ", 1)
            return p.strip(), c.strip()
        return key.strip(), key.strip()

    # --------------------
    # Normalize keys + cap
    # --------------------
    keys = _stable_unique([str(k) for k in group_keys])[:max_bubbles]

    if len(keys) == 0:
        return {}

    # --------------------
    # Case 1: no hierarchy
    # --------------------
    if len(group_cols or []) <= 1:
        if len(FLAT_COLORS_HEX) < len(keys):
            raise ValueError(
                f"Zu wenig FLAT_COLORS_HEX: {len(FLAT_COLORS_HEX)} für {len(keys)} Bubbles "
                f"(max_bubbles={max_bubbles})."
            )
        return {k: to_rgba(resolve_color_expr(FLAT_COLORS_HEX[i])) for i, k in enumerate(keys)}

    # --------------------
    # Case 2: parent-child
    # --------------------
    if len(group_cols) != 2:
        raise ValueError("pick_colors_hardcoded unterstützt nur group_cols mit Länge 0/1/2.")

    if len(HIERARCHY_COLORS_HEX) < max_parents:
        # nicht zwingend nötig, aber verhindert "komische" teilweise Belegungen
        raise ValueError(
            f"HIERARCHY_COLORS_HEX hat nur {len(HIERARCHY_COLORS_HEX)} Parents, "
            f"aber max_parents={max_parents}."
        )

    # check child length per parent
    for i in range(max_parents):
        if len(HIERARCHY_COLORS_HEX[i]) < max_children_per_parent:
            raise ValueError(
                f"HIERARCHY_COLORS_HEX[{i}] hat nur {len(HIERARCHY_COLORS_HEX[i])} Child-Farben, "
                f"aber max_children_per_parent={max_children_per_parent}."
            )

    # Parents + children order (first-seen)
    parents_in_order: List[str] = []
    children_by_parent: Dict[str, List[str]] = {}

    for k in keys:
        p, c = _split_parent_child(k)
        if p not in parents_in_order:
            parents_in_order.append(p)
        children_by_parent.setdefault(p, [])
        if c not in children_by_parent[p]:
            children_by_parent[p].append(c)

    # only first max_parents are "officially mapped"
    mapped_parents = parents_in_order[:max_parents]
    parent_index = {p: i for i, p in enumerate(mapped_parents)}

    out: Dict[str, RGBA] = {}

    for i, k in enumerate(keys):
        p, c = _split_parent_child(k)

        # Parent overflow ->
        if p not in parent_index:
            out[k] = to_rgba(resolve_color_expr(FLAT_COLORS_HEX[i % len(FLAT_COLORS_HEX)]))
            continue

        pi = parent_index[p]
        child_list = children_by_parent.get(p, [])
        ci = child_list.index(c) if c in child_list else 0

        # Child overflow -> fallback within parent (z.B. Child1) oder fallback color
        if ci >= max_children_per_parent:
            out[k] = to_rgba(resolve_color_expr(HIERARCHY_COLORS_HEX[pi][0]))
        else:
            out[k] = to_rgba(resolve_color_expr(HIERARCHY_COLORS_HEX[pi][ci]))

    return out


def apply_filters(d: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Unterstützt mehrere Filterregeln via cfg["filters"].

    Erwartete Config-Formate:

    A) Multi Filter (empfohlen):
    "filters": {
        "enabled": True,
        "rules": [
            {"col": "Service Type", "op": "in", "values": ["Hardware","License"]},
            {"col": "Revenue", "op": ">=", "value": 0, "type": "numeric"},
            {"col": "Customer Name", "op": "regex", "pattern": r"^(SAP|Oracle)"},
            {"col": "Customer Region", "op": "isna"},
            {"col": "Some Col", "op": "notblank"},
        ]
    }

    Ops:
    - equality: eq,"==","ne","!="
    - set: "in","not_in"
    - numeric/date: "gt",">","gte",">=","lt","<","lte","<=","between"
    - text: "contains","startswith","endswith","regex"
    - null: "isna","isnull","notna","notnull"
    - blank strings: "isblank","notblank"
    - zero: "is_zero","nonzero"
    """
    fcfg = cfg.get("filters") or {}
    if not fcfg.get("enabled", False):
        return d

    rules = fcfg.get("rules", None)
    if not isinstance(rules, list) or len(rules) == 0:
        return d

    out = d.copy()

    def _as_series(col: str) -> pd.Series:
        if col not in out.columns:
            raise ValueError(f"Filter-Spalte '{col}' fehlt im Input.")
        return out[col]

    def _coerce_series(s: pd.Series, typ: str | None) -> pd.Series:
        typ = (typ or "").lower().strip()
        if typ in {"num", "numeric", "number", "float", "int"}:
            return pd.to_numeric(s, errors="coerce")
        if typ in {"date", "datetime"}:
            return pd.to_datetime(s, errors="coerce", dayfirst=True)
        if typ in {"str", "string", "text"}:
            return s.astype(str)
        return s

    mask_all = pd.Series(True, index=out.index)

    for r in rules:
        if not isinstance(r, dict):
            continue

        col = r.get("col")
        if not col:
            raise ValueError("Filter-Regel ohne 'col' gefunden.")

        op = str(r.get("op", "in")).strip().lower()
        typ = r.get("type", None)
        case = bool(r.get("case", True))
        na = bool(r.get("na", False))

        s_raw = _as_series(col)
        s = _coerce_series(s_raw, typ)

        if op in {"isna", "isnull"}:
            m = s_raw.isna()
        elif op in {"notna", "notnull"}:
            m = ~s_raw.isna()

        elif op in {"isblank"}:
            ss = s_raw.astype(str)
            m = s_raw.isna() | (ss.str.strip() == "")
        elif op in {"notblank"}:
            ss = s_raw.astype(str)
            m = (~s_raw.isna()) & (ss.str.strip() != "")

        elif op in {"is_zero"}:
            sn = pd.to_numeric(s_raw, errors="coerce")
            m = sn.fillna(np.nan) == 0
        elif op in {"nonzero"}:
            sn = pd.to_numeric(s_raw, errors="coerce")
            m = sn.notna() & (sn != 0)

        elif op in {"in"}:
            vals = r.get("values", r.get("value", None))
            if vals is None:
                raise ValueError(f"Filter '{col}' op=in braucht 'values'.")
            if not isinstance(vals, (list, set, tuple)):
                vals = [vals]

            if (typ or "").lower() in {"numeric", "num", "number", "float", "int"}:
                vals = [pd.to_numeric(v, errors="coerce") for v in vals]
                sn = pd.to_numeric(s_raw, errors="coerce")
                m = sn.isin(vals)
            else:
                m = s_raw.isin(vals)

        elif op in {"not_in"}:
            vals = r.get("values", r.get("value", None))
            if vals is None:
                raise ValueError(f"Filter '{col}' op=not_in braucht 'values'.")
            if not isinstance(vals, (list, set, tuple)):
                vals = [vals]
            m = ~s_raw.isin(vals)

        elif op in {"eq", "=="}:
            v = r.get("value", None)
            if v is None:
                raise ValueError(f"Filter '{col}' op=eq braucht 'value'.")
            m = s == v
        elif op in {"ne", "!="}:
            v = r.get("value", None)
            if v is None:
                raise ValueError(f"Filter '{col}' op=ne braucht 'value'.")
            m = s != v

        elif op in {"gt", ">"}:
            v = r.get("value", None)
            if v is None:
                raise ValueError(f"Filter '{col}' op=gt braucht 'value'.")
            m = s > v
        elif op in {"gte", ">="}:
            v = r.get("value", None)
            if v is None:
                raise ValueError(f"Filter '{col}' op=gte braucht 'value'.")
            m = s >= v
        elif op in {"lt", "<"}:
            v = r.get("value", None)
            if v is None:
                raise ValueError(f"Filter '{col}' op=lt braucht 'value'.")
            m = s < v
        elif op in {"lte", "<="}:
            v = r.get("value", None)
            if v is None:
                raise ValueError(f"Filter '{col}' op=lte braucht 'value'.")
            m = s <= v
        elif op in {"between"}:
            lo = r.get("lo", None)
            hi = r.get("hi", None)
            if lo is None or hi is None:
                raise ValueError(f"Filter '{col}' op=between braucht 'lo' und 'hi'.")
            m = (s >= lo) & (s <= hi)

        elif op in {"contains"}:
            pat = r.get("pattern", r.get("value", None))
            if pat is None:
                raise ValueError(f"Filter '{col}' op=contains braucht 'pattern' oder 'value'.")
            ss = s_raw.astype(str)
            m = ss.str.contains(str(pat), case=case, regex=False, na=na)

        elif op in {"startswith"}:
            pat = r.get("pattern", r.get("value", None))
            if pat is None:
                raise ValueError(f"Filter '{col}' op=startswith braucht 'pattern' oder 'value'.")
            ss = s_raw.astype(str)
            if case:
                m = ss.str.startswith(str(pat), na=False)
            else:
                m = ss.str.lower().str.startswith(str(pat).lower(), na=False)

        elif op in {"endswith"}:
            pat = r.get("pattern", r.get("value", None))
            if pat is None:
                raise ValueError(f"Filter '{col}' op=endswith braucht 'pattern' oder 'value'.")
            ss = s_raw.astype(str)
            if case:
                m = ss.str.endswith(str(pat), na=False)
            else:
                m = ss.str.lower().str.endswith(str(pat).lower(), na=False)

        elif op in {"regex"}:
            pat = r.get("pattern", None)
            if pat is None:
                raise ValueError(f"Filter '{col}' op=regex braucht 'pattern'.")
            ss = s_raw.astype(str)
            m = ss.str.contains(pat, case=case, regex=True, na=na)

        else:
            raise ValueError(f"Unbekannter Filter-Operator '{op}' für Spalte '{col}'.")

        m = m.fillna(False)
        mask_all &= m

    return out.loc[mask_all].copy()


def days_inclusive(a, b) -> pd.Series:
    """Datumdiff in Tagen inkl. beider Enden (+1). Gibt eine Series zurück."""
    a = pd.to_datetime(a)
    b = pd.to_datetime(b)

    days = (b - a) / np.timedelta64(1, "D")
    days = np.floor(days) + 1

    if isinstance(days, pd.Series):
        out = days
    else:
        idx = getattr(a, "index", None) or getattr(b, "index", None)
        out = pd.Series(days, index=idx)

    return out.clip(lower=0)


def safe_month_day_ts(year: int, month: int, day: int) -> pd.Timestamp:
    """Sicherer Timestamp für '31.04' etc. (clamp auf Monatsende)."""
    first = pd.Timestamp(year, month, 1)
    last = first + pd.offsets.MonthEnd(0)
    d = min(int(day), int(last.day))
    return pd.Timestamp(year, month, d)


def fiscal_year_bounds(fy_end_year: int, fy_end_m: int, fy_end_d: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Start/Ende eines Fiscal Years (Ende = fy_end_year/fy_end_m/fy_end_d)."""
    end = safe_month_day_ts(fy_end_year, fy_end_m, fy_end_d)
    start = (end - pd.DateOffset(years=1)) + pd.Timedelta(days=1)
    return start, end


# ─── Canonical FDD period helpers (shared by GST / TOP / PVM) ──────────────────
# Single source of truth so every strand computes FY / YTD / LTM bounds the same
# way, especially when the fiscal year-end (Stichtag) differs from the as-of /
# last-month-to-include date.

def fdd_as_of_end(year: int, month: int) -> pd.Timestamp:
    """Month-end timestamp of the as-of / last-month-to-include date."""
    return pd.Timestamp(int(year), int(month), 1) + pd.offsets.MonthEnd(0)


def fdd_current_fy_end_year(as_of_end: pd.Timestamp, fy_end_m: int, fy_end_d: int) -> int:
    """Calendar year of the FY-end of the fiscal year that CONTAINS the as-of date."""
    fye_this_year = safe_month_day_ts(as_of_end.year, fy_end_m, fy_end_d)
    return as_of_end.year if as_of_end <= fye_this_year else as_of_end.year + 1


def fdd_as_of_is_fy_end(as_of_end: pd.Timestamp, fy_end_m: int, fy_end_d: int) -> bool:
    """True when the as-of date lands exactly on a fiscal year-end (Stichtag)."""
    cur = fdd_current_fy_end_year(as_of_end, fy_end_m, fy_end_d)
    return bool(
        as_of_end.normalize() == safe_month_day_ts(cur, fy_end_m, fy_end_d).normalize()
    )


def fdd_ytd_bounds(as_of_end: pd.Timestamp, fy_end_m: int, fy_end_d: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """YTD ending at the as-of date; start = fiscal-year start of the FY containing it.

    Example: FY-end 31 Jul, as-of 31 Dec 2024 -> (2024-08-01, 2024-12-31).
    """
    cur = fdd_current_fy_end_year(as_of_end, fy_end_m, fy_end_d)
    start, _ = fiscal_year_bounds(cur, fy_end_m, fy_end_d)
    return start, as_of_end


def fdd_ltm_bounds(as_of_end: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Rolling last-twelve-months window ending at the as-of date.

    Example: as-of 31 Dec 2024 -> (2024-01-01, 2024-12-31). FY-end does not shift
    the LTM window (only the label year follows the as-of year).
    """
    start = as_of_end - pd.DateOffset(months=12) + pd.Timedelta(days=1)
    return start, as_of_end


def recognized_value(          #accrual Logik
    d: pd.DataFrame,
    period_start: pd.Timestamp,
    period_end: pd.Timestamp,
    cfg: dict
) -> pd.Series:
    """
    Accrual-Logik: liefert pro Zeile den realisierten Anteil im Zeitraum.

    Erwartet in cfg:
        start_col, end_col, invoice_col
    und in d:
        'value' (numerisch)
    """
    I = pd.to_datetime(d[cfg["invoice_col"]], errors="coerce", dayfirst=True)
    S = pd.to_datetime(d[cfg["start_col"]], errors="coerce", dayfirst=True)
    E = pd.to_datetime(d[cfg["end_col"]], errors="coerce", dayfirst=True)

    aktiv_von = np.where(
        I >= period_start,
        S,
        np.where(S > period_start, S, period_start)
    )
    aktiv_von = pd.to_datetime(aktiv_von)

    aktiv_bis = pd.to_datetime(
        np.minimum(E.values.astype("datetime64[ns]"), np.datetime64(period_end))
    )

    tage_realisiert = days_inclusive(aktiv_von, aktiv_bis)
    contract_days = days_inclusive(S, E).clip(lower=1)

    value = pd.to_numeric(d["value"], errors="coerce").fillna(0.0)

    ko1 = (S > period_end) | (I > period_end)
    ko2 = (I < period_start) & (E < period_start)

    out = value * (tage_realisiert / contract_days)
    out = out.where(~(ko1 | ko2), 0.0)
    return out


def fy_label(y): return f"FY{str(y)[-2:]}A"
def ytd_label(y): return f"YTD{str(y)[-2:]}A"
def ltm_label(y): return f"LTM{str(y)[-2:]}A"


def to_kEUR(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").div(1000).round(0)


# Period logic

def compute_current_fy_end_year(as_of_end: pd.Timestamp, fy_end_m: int, fy_end_d: int) -> int:
    CY = as_of_end.year
    fye_this_year = safe_month_day_ts(CY, fy_end_m, fy_end_d)
    return CY if as_of_end <= fye_this_year else CY + 1


def get_period(cfg: Dict[str, Any]) -> Tuple[pd.Timestamp, pd.Timestamp, str]:
    CY = int(cfg["current_year"])
    m = int(cfg["current_month"])

    fy_end_m = int(cfg["fiscal_year_end_month"])
    fy_end_d = int(cfg["fiscal_year_end_day"])

    as_of_end = pd.Timestamp(CY, m, 1) + pd.offsets.MonthEnd(0)
    cur_fy_end_year = compute_current_fy_end_year(as_of_end, fy_end_m, fy_end_d)

    mode = cfg["period_mode"]

    if mode == "FY":
        fye_this_year = safe_month_day_ts(as_of_end.year, fy_end_m, fy_end_d)
        last_completed_fy_end_year = as_of_end.year if as_of_end >= fye_this_year else as_of_end.year - 1
        s, e = fiscal_year_bounds(last_completed_fy_end_year, fy_end_m, fy_end_d)
        return s, e, fy_label(last_completed_fy_end_year)

    if mode == "YTD":
        s, _ = fiscal_year_bounds(cur_fy_end_year, fy_end_m, fy_end_d)
        return s, as_of_end, f"YTD{str(cur_fy_end_year)[-2:]}"

    if mode == "LTM":
        start_ltm = as_of_end - pd.DateOffset(years=1) + pd.Timedelta(days=1)
        return start_ltm, as_of_end, f"LTM {m}-{str(as_of_end.year)[-2:]}"

    raise ValueError("Unbekannter period_mode.")


def compute_amount(d: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, cfg: Dict[str, Any], value_col: str) -> pd.Series:
    if cfg["calc_mode"] == "invoice":
        mask = (d[cfg["invoice_col"]] >= start) & (d[cfg["invoice_col"]] <= end)
        amt = pd.to_numeric(d[value_col], errors="coerce").fillna(0.0)
        return amt.where(mask, 0.0)

    tmp = d.copy()
    tmp["value"] = pd.to_numeric(tmp[value_col], errors="coerce").fillna(0.0)
    return recognized_value(tmp, start, end, cfg)


def get_next_sheet_name(xlsx_path: str, base_sheet_name: str) -> str:
    if not os.path.exists(xlsx_path):
        return base_sheet_name

    wb = load_workbook(xlsx_path)
    existing = set(wb.sheetnames)

    if base_sheet_name not in existing:
        return base_sheet_name

    i = 2
    while f"{base_sheet_name}_{i}" in existing:
        i += 1
    return f"{base_sheet_name}_{i}"


def ensure_output_writable(output_path: str):
    if not os.path.exists(output_path):
        return

    try:
        with open(output_path, "a+b"):
            pass
    except PermissionError:
        raise PermissionError(
            f"Die Ausgabedatei ist aktuell geöffnet oder gesperrt: {output_path}. "
            f"Bitte die Datei schließen und den Vorgang erneut starten."
        )

# appended to funktionssammlung.py — Excel / GST / PVM helpers
import hashlib
import re
import shutil
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.dataframe import dataframe_to_rows


# ─── Output paths & source sheet copy ──────────────────────────────────────────


def build_output_file_path(cfg: dict) -> str:
    output_dir = str(cfg["output_file_path"]).strip()
    case_id = str(cfg.get("case_id", "")).strip()
    if not output_dir:
        raise ValueError("CONFIG['output_file_path'] muss gesetzt sein.")
    if not case_id:
        raise ValueError("CONFIG['case_id'] muss gesetzt sein, um den Output-Dateinamen zu bilden.")
    os.makedirs(output_dir, exist_ok=True)
    return os.path.join(output_dir, f"{case_id}_Output.xlsx")


def build_source_sheet_name(cfg: dict) -> str:
    return "__SOURCE__"


def _source_date_columns(cfg: dict) -> list[str]:
    """Columns that must be real Excel dates for SUMIFS date criteria."""
    cols: list[str] = []
    inv = str(cfg.get("invoice_col") or "").strip()
    if inv:
        cols.append(inv)
    if str(cfg.get("invoice_mapping_mode", "")).strip().lower() == "accrual":
        for key in ("start_col", "end_col"):
            c = str(cfg.get(key) or "").strip()
            if c:
                cols.append(c)
    return cols


def _prepare_source_df_for_excel(source_df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Parse date columns so SUMIFS can compare against period bounds (not text)."""
    out = source_df.copy()
    out.columns = out.columns.astype(str).str.replace("\u00a0", " ", regex=False).str.strip()
    for col in _source_date_columns(cfg):
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce", dayfirst=True)
    return out


def _is_source_helper_header(name) -> bool:
    """True for appended formula helper columns (accrual, text filter, PVM FX, …)."""
    s = str(name or "").strip()
    if not s:
        return False
    if s.startswith("__AUDIT_"):
        return True
    if s.startswith("Accrual ·"):
        return True
    if s.startswith("_PVM_FX_"):
        return True
    if s.startswith("_churn_arr_"):
        return True
    return False


def _count_source_helper_columns(ws_source, base_width: int) -> int:
    n = 0
    for c in range(base_width + 1, (ws_source.max_column or 0) + 1):
        if _is_source_helper_header(ws_source.cell(row=1, column=c).value):
            n += 1
    return n


def _apply_date_formats_to_base_cols(
    ws_source,
    *,
    date_cols: set[str],
    col_index: dict[str, int],
) -> None:
    for col_name in date_cols:
        col_idx = col_index.get(col_name)
        if not col_idx:
            continue
        for r in range(2, (ws_source.max_row or 1) + 1):
            cell = ws_source.cell(row=r, column=col_idx)
            val = cell.value
            if isinstance(val, pd.Timestamp):
                cell.value = val.to_pydatetime()
            cell.number_format = "DD.MM.YYYY"


def write_source_df_to_ws(ws_source, source_df: pd.DataFrame, cfg: dict) -> None:
    """Write flat source values; invoice (and accrual) dates as Excel datetimes."""
    df = _prepare_source_df_for_excel(source_df, cfg)
    date_cols = {c for c in _source_date_columns(cfg) if c in df.columns}
    col_index = {name: idx + 1 for idx, name in enumerate(df.columns)}

    if ws_source.max_row:
        ws_source.delete_rows(1, ws_source.max_row)
    if ws_source.max_column:
        ws_source.delete_cols(1, ws_source.max_column)

    for row in dataframe_to_rows(df, index=False, header=True):
        ws_source.append(row)

    _apply_date_formats_to_base_cols(ws_source, date_cols=date_cols, col_index=col_index)


def sync_source_sheet_from_df(ws_source, source_df: pd.DataFrame, cfg: dict) -> None:
    """Update base source columns in place; never delete appended helper columns."""
    df = _prepare_source_df_for_excel(source_df, cfg)
    base_cols = list(df.columns)
    base_width = len(base_cols)
    date_cols = {c for c in _source_date_columns(cfg) if c in df.columns}
    col_index = {name: idx + 1 for idx, name in enumerate(base_cols)}

    helpers_before = _count_source_helper_columns(ws_source, base_width)

    first_cell = ws_source.cell(row=1, column=1).value
    sheet_empty = (ws_source.max_row or 0) < 1 or first_cell in (None, "")

    if sheet_empty and helpers_before == 0:
        write_source_df_to_ws(ws_source, source_df, cfg)
        return

    for c_idx, col_name in enumerate(base_cols, start=1):
        ws_source.cell(row=1, column=c_idx).value = col_name

    n_rows = len(df)
    for r_off, row in enumerate(df.itertuples(index=False), start=0):
        r = 2 + r_off
        for c_idx, val in enumerate(row, start=1):
            col_name = base_cols[c_idx - 1]
            cell = ws_source.cell(row=r, column=c_idx)
            if col_name in date_cols and pd.notna(val):
                cell.value = (
                    val.to_pydatetime() if isinstance(val, pd.Timestamp) else val
                )
                cell.number_format = "DD.MM.YYYY"
            elif pd.isna(val):
                cell.value = None
            else:
                cell.value = val

    old_max_row = ws_source.max_row or 1
    for r in range(2 + n_rows, old_max_row + 1):
        for c in range(1, base_width + 1):
            ws_source.cell(row=r, column=c).value = None

    _apply_date_formats_to_base_cols(ws_source, date_cols=date_cols, col_index=col_index)


def refresh_source_sheet_from_df(ws_source, source_df: pd.DataFrame, cfg: dict) -> None:
    """Sync flat base source values; preserve helper columns from prior scripts in the same run."""
    sync_source_sheet_from_df(ws_source, source_df, cfg)


def ensure_source_sheet_in_output(cfg: dict, output_path: str) -> str:
    """
    Stellt sicher, dass das Quell-Sheet im Output-Workbook existiert.
    Existiert output_path noch nicht: Input-Datei kopieren, alle anderen Sheets löschen,
    gewünschtes Sheet umbenennen auf __SOURCE__ und nach vorne ziehen.
    """
    source_sheet_name = build_source_sheet_name(cfg)
    input_sheet_name = str(cfg["sheet_name"]).strip()
    file_path = str(cfg["file_path"]).strip()

    if not os.path.exists(output_path):
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        # Values-only source sheet: copying the full input workbook and re-saving
        # with openpyxl often corrupts complex source files (formulas, extensions)
        # and triggers Excel repair that strips report formulas.
        df = pd.read_excel(file_path, sheet_name=input_sheet_name, engine="openpyxl")
        wb = Workbook()
        ws = wb.active
        ws.title = source_sheet_name
        write_source_df_to_ws(ws, df, cfg)
        wb.save(output_path)
        wb.close()
        return source_sheet_name

    wb = load_workbook(output_path)
    if source_sheet_name not in wb.sheetnames:
        wb.close()
        raise ValueError(
            f"Output-Datei existiert bereits, aber Source-Sheet '{source_sheet_name}' fehlt: {output_path}"
        )
    wb.close()
    return source_sheet_name


def get_next_sheet_name_from_wb(wb, base_sheet_name: str) -> str:
    existing = set(wb.sheetnames)
    if base_sheet_name not in existing:
        return base_sheet_name
    i = 2
    while f"{base_sheet_name}_{i}" in existing:
        i += 1
    return f"{base_sheet_name}_{i}"


def write_export_df_to_sheet(wb, export_df, target_sheet_name: str, **kwargs):
    """Write a pandas DataFrame to a new sheet (kwargs ignored for GST compatibility)."""
    if target_sheet_name in wb.sheetnames:
        del wb[target_sheet_name]
    ws = wb.create_sheet(target_sheet_name)
    for row in dataframe_to_rows(export_df, index=False, header=True):
        ws.append(row)


# ─── Header map & Excel refs ─────────────────────────────────────────────────


def _normalize_header_name(name: str) -> str:
    s = str(name).strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def resolve_column_names_to_df(df: pd.DataFrame, names: list[str]) -> list[str]:
    """Map config column labels to exact DataFrame header strings (nbsp/case tolerant)."""
    col_map = {
        _normalize_header_name(str(c)): str(c).replace("\u00a0", " ").strip()
        for c in df.columns
    }
    resolved: list[str] = []
    for raw in names:
        key = _normalize_header_name(str(raw).replace("\u00a0", " "))
        if key not in col_map:
            raise ValueError(f"Spalte '{raw}' nicht in Quelldaten gefunden.")
        resolved.append(col_map[key])
    return resolved


def _get_sheet_header_map(ws) -> dict[str, int]:
    """Map normalized header text -> 1-based column index (row 1)."""
    m: dict[str, int] = {}
    for c in range(1, (ws.max_column or 0) + 1):
        v = ws.cell(row=1, column=c).value
        if v is None or str(v).strip() == "":
            continue
        key = _normalize_header_name(str(v))
        m[key] = c
    return m


def source_range_ref(source_sheet_name: str, source_header_map: dict, header_name: str) -> str:
    key = _normalize_header_name(header_name)
    if key not in source_header_map:
        raise ValueError(
            f"Source-Header '{header_name}' nicht gefunden im Sheet '{source_sheet_name}'."
        )
    col = source_header_map[key]
    return source_column_range_ref(source_sheet_name, col)


def source_column_range_ref(source_sheet_name: str, col_idx: int) -> str:
    letter = get_column_letter(col_idx)
    safe = str(source_sheet_name).replace("'", "''")
    return f"'{safe}'!${letter}:${letter}"


# ─── FY parsing (invoice column) ─────────────────────────────────────────────


def parse_invoice_fy_year(series: pd.Series) -> pd.Series:
    """Liest FY-Jahresangaben aus Zahlen, Strings oder Datumswerten robust als Int64 aus."""
    raw = series.copy()
    num = pd.to_numeric(raw, errors="coerce")
    out = pd.Series(pd.NA, index=raw.index, dtype="Int64")
    mnum = num.notna() & (num >= 1900) & (num <= 2200)
    out = out.mask(~mnum, out).where(~mnum, num.astype("Int64"))

    sstr = raw.astype(str).str.strip()
    mtxt = sstr.str.extract(r"^\D*(\d{4})\D*$", expand=False)
    ytxt = pd.to_numeric(mtxt, errors="coerce").astype("Int64")
    out = ytxt.where(ytxt.notna(), out)

    dt = pd.to_datetime(raw, errors="coerce", dayfirst=True)
    ydt = dt.dt.year.astype("Int64")
    out = ydt.where(ydt.notna(), out)
    return out


# ─── Excel formula tokens ────────────────────────────────────────────────────


def excel_formula_value_token(value) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)) or pd.isna(value):
        return '""'
    if isinstance(value, pd.Timestamp):
        return f"DATE({value.year},{value.month},{value.day})"
    if isinstance(value, bool):
        return '"TRUE"' if value else '"FALSE"'
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        fv = float(value)
        if abs(fv - round(fv)) < 1e-9:
            return str(int(round(fv)))
        return str(fv).replace(",", ".")
    s = str(value).replace('"', '""')
    return f'"{s}"'


def excel_sumifs_criteria_token(op: str, value=None) -> str:
    op = str(op).strip().lower()
    tok = excel_formula_value_token(value)
    if op in {"eq", "=="}:
        return tok
    if op in {"ne", "!="}:
        return f'"<>"&{tok}'
    if op in {"gt", ">"}:
        return f'">&{tok}'
    if op in {"gte", ">="}:
        return f'">=&{tok}'
    if op in {"lt", "<"}:
        return f'"<&{tok}'
    if op in {"lte", "<="}:
        return f'"<=&{tok}'
    if op in {"isna", "isnull", "isblank"}:
        return '""'
    if op in {"notna", "notnull", "notblank"}:
        return '"<>"'
    if op in {"is_zero"}:
        return "0"
    if op in {"nonzero"}:
        return '"<>0"'
    raise ValueError(f"Nicht unterstützter SUMIFS-Operator: {op}")


# ─── Filter splitting for formula mode ───────────────────────────────────────


def split_filter_rules_for_formula_usage(cfg: dict) -> dict:
    fcfg = cfg.get("filters") or {}
    rules = fcfg.get("rules") or []
    if not isinstance(rules, list):
        rules = []

    direct_ops = {
        "eq", "==", "ne", "!=", "gt", ">", "gte", ">=", "lt", "<", "lte", "<=",
        "between", "in", "not_in", "isna", "isnull", "notna", "notnull",
        "isblank", "notblank", "is_zero", "nonzero",
    }
    text_ops = {"contains", "startswith", "endswith", "regex"}

    direct_rules: list = []
    text_helper_rules: list = []
    unsupported_rules: list = []

    for r in rules:
        if not isinstance(r, dict):
            continue
        op = str(r.get("op", "")).strip().lower()
        if op in direct_ops:
            direct_rules.append(r)
        elif op in text_ops:
            text_helper_rules.append(r)
        else:
            unsupported_rules.append(r)

    return {
        "direct_rules": direct_rules,
        "text_helper_rules": text_helper_rules,
        "unsupported_rules": unsupported_rules,
        "enabled": bool(fcfg.get("enabled", True)),
    }


def build_formula_filter_branches(
    cfg: dict,
    source_sheet_name: str,
    source_header_map: dict,
    text_helper_cols_by_index: dict | None = None,
) -> list[list[tuple[str, str]]]:
    text_helper_cols_by_index = text_helper_cols_by_index or {}
    split = split_filter_rules_for_formula_usage(cfg)
    if split["unsupported_rules"]:
        bad_ops = [str(r.get("op", "")).strip().lower() for r in split["unsupported_rules"]]
        raise ValueError(
            f"formula_mode unterstützt diese Filter aktuell nicht: {sorted(set(bad_ops))}"
        )

    branches: list[list[tuple[str, str]]] = [[]]

    for rule in split.get("direct_rules", []):
        col = rule.get("col")
        op = str(rule.get("op", "")).strip().lower()
        rng = source_range_ref(source_sheet_name, source_header_map, col)

        if op == "in":
            vals = rule.get("values", rule.get("value", None))
            if vals is None:
                raise ValueError(f"Filter '{col}' mit op='in' braucht 'values'.")
            if not isinstance(vals, (list, tuple, set)):
                vals = [vals]
            new_branches: list = []
            for branch in branches:
                for v in vals:
                    new_branches.append(branch + [(rng, excel_sumifs_criteria_token("eq", v))])
            branches = new_branches
            continue

        if op == "not_in":
            vals = rule.get("values", rule.get("value", None))
            if vals is None:
                raise ValueError(f"Filter '{col}' mit op='not_in' braucht 'values'.")
            if not isinstance(vals, (list, tuple, set)):
                vals = [vals]
            extra_pairs = [(rng, excel_sumifs_criteria_token("ne", v)) for v in vals]
            branches = [branch + extra_pairs for branch in branches]
            continue

        if op == "between":
            lo = rule.get("lo", None)
            hi = rule.get("hi", None)
            if lo is None or hi is None:
                raise ValueError(f"Filter '{col}' mit op=between braucht 'lo' und 'hi'.")
            new_branches = []
            for branch in branches:
                new_branches.append(
                    branch
                    + [
                        (rng, excel_sumifs_criteria_token("gte", lo)),
                        (rng, excel_sumifs_criteria_token("lte", hi)),
                    ]
                )
            branches = new_branches
            continue

        if op in {"isna", "isnull"}:
            branches = [b + [(rng, excel_sumifs_criteria_token("isna", None))] for b in branches]
            continue
        if op in {"notna", "notnull"}:
            branches = [b + [(rng, excel_sumifs_criteria_token("notna", None))] for b in branches]
            continue
        if op in {"isblank"}:
            branches = [b + [(rng, excel_sumifs_criteria_token("isblank", None))] for b in branches]
            continue
        if op in {"notblank"}:
            branches = [b + [(rng, excel_sumifs_criteria_token("notblank", None))] for b in branches]
            continue
        if op in {"is_zero"}:
            branches = [b + [(rng, excel_sumifs_criteria_token("is_zero", None))] for b in branches]
            continue
        if op in {"nonzero"}:
            branches = [b + [(rng, excel_sumifs_criteria_token("nonzero", None))] for b in branches]
            continue

        if op in {"eq", "==", "ne", "!=", "gt", ">", "gte", ">=", "lt", "<", "lte", "<="}:
            v = rule.get("value", None)
            if v is None and op not in {"isna", "isnull"}:
                raise ValueError(f"Filter '{col}' braucht 'value'.")
            cop = {"eq": "eq", "==": "eq", "ne": "ne", "!=": "ne", "gt": "gt", ">": "gt",
                   "gte": "gte", ">=": "gte", "lt": "lt", "<": "lt", "lte": "lte", "<=": "lte"}[op]
            branches = [b + [(rng, excel_sumifs_criteria_token(cop, v))] for b in branches]
            continue

        raise ValueError(f"Unbekannter direkter Filter-Operator: {op}")

    # Text helper columns: add criteria pairs referencing helper column ranges
    for i, rule in enumerate(split.get("text_helper_rules", [])):
        hcol = text_helper_cols_by_index.get(i)
        if not hcol:
            continue
        rng = source_range_ref(source_sheet_name, source_header_map, hcol)
        branches = [b + [(rng, '"1"')] for b in branches]

    return branches


def build_sumifs_formula_body(
    sum_rng: str,
    base_pairs: list[tuple[str, str]],
    filter_branches: list[list[tuple[str, str]]],
    divide_by_1000: bool = True,
) -> str:
    if not filter_branches:
        filter_branches = [[]]
    parts: list[str] = []
    for branch in filter_branches:
        args: list[str] = [sum_rng]
        for crange, crit in list(base_pairs) + list(branch):
            args.append(crange)
            args.append(crit)
        inner = "SUMIFS(" + ",".join(args) + ")"
        if divide_by_1000:
            inner = f"(({inner})/1000)"
        parts.append(inner)
    if len(parts) == 1:
        return parts[0]
    return "(" + "+".join(parts) + ")"


_ROW_SUM_CHUNK_SIZE = 200


def _chunk_list(values: list, size: int = _ROW_SUM_CHUNK_SIZE) -> list[list]:
    return [values[i : i + size] for i in range(0, len(values), size)]


def _row_numbers_to_col_refs(row_numbers: list[int], col_idx: int) -> list[str]:
    """Collapse contiguous row indices into range refs (e.g. E14:E1857)."""
    if not row_numbers:
        return []

    col_letter = get_column_letter(col_idx)
    rows = sorted({int(r) for r in row_numbers})
    refs: list[str] = []
    start = prev = rows[0]
    for row in rows[1:]:
        if row == prev + 1:
            prev = row
            continue
        refs.append(
            f"{col_letter}{start}:{col_letter}{prev}"
            if start != prev
            else f"{col_letter}{start}"
        )
        start = prev = row
    refs.append(
        f"{col_letter}{start}:{col_letter}{prev}"
        if start != prev
        else f"{col_letter}{start}"
    )
    return refs


def build_chunked_row_sum_formula(
    row_numbers: list[int],
    col_idx: int,
    *,
    na_label: str = "n/a",
    chunk_size: int = _ROW_SUM_CHUNK_SIZE,
) -> str:
    """SUM/COUNT over many report rows without exceeding Excel's per-function arg limit."""
    if not row_numbers:
        return f'="{na_label}"'

    refs = _row_numbers_to_col_refs(row_numbers, col_idx)

    def _single_sum_count(args: str) -> str:
        return f'=IF(COUNT({args})=0,"{na_label}",SUM({args}))'

    if len(refs) <= chunk_size:
        return _single_sum_count(",".join(refs))

    sum_parts: list[str] = []
    count_parts: list[str] = []
    for chunk in _chunk_list(refs, chunk_size):
        args = ",".join(chunk)
        sum_parts.append(f"SUM({args})")
        count_parts.append(f"COUNT({args})")

    sum_expr = "+".join(sum_parts)
    count_expr = "+".join(count_parts)
    return f'=IF(({count_expr})=0,"{na_label}",{sum_expr})'


# ─── Text / accrual helper columns on source sheet ──────────────────────────


def _sanitize_helper_token(s: str) -> str:
    t = re.sub(r"[^A-Za-z0-9_]+", "_", str(s))
    return t.strip("_")[:80] or "X"


def _stable_short_hash(payload: str, length: int = 12) -> str:
    h = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return h[:length]


def build_text_filter_col_name(
    *,
    source_col: str,
    op: str,
    value: str,
    case: bool = True,
    na: bool = False,
) -> str:
    source_col_t = _sanitize_helper_token(source_col)
    op_t = _sanitize_helper_token(op).upper()
    value_raw = str(value)
    value_t = _sanitize_helper_token(value_raw)
    payload = (
        f"col={_normalize_header_name(source_col)}|"
        f"op={str(op).lower().strip()}|"
        f"value={value_raw}|"
        f"case={bool(case)}|"
        f"na={bool(na)}"
    )
    h = _stable_short_hash(payload, length=12)
    return (
        "__AUDIT_TXTFILTER__"
        f"{op_t}__"
        f"SRC_{source_col_t}__"
        f"VAL_{value_t}__"
        f"CASE_{int(bool(case))}__"
        f"NA_{int(bool(na))}__"
        f"H_{h}"
    )


def _validate_required_columns(df: pd.DataFrame, cols: list[str], *, context: str) -> None:
    for c in cols:
        if _normalize_header_name(c) not in {_normalize_header_name(x) for x in df.columns}:
            if c not in df.columns:
                raise ValueError(f"[{context}] Spalte '{c}' fehlt.")


def add_text_filter_col_in_ws(
    *,
    ws_source,
    source_df: pd.DataFrame,
    source_col: str,
    op: str,
    value: str,
    case: bool = True,
    na: bool = False,
) -> str:
    op_norm = str(op).strip().lower()
    if op_norm not in {"contains", "startswith", "endswith", "regex"}:
        raise ValueError(
            "add_text_filter_col_in_ws unterstützt nur 'contains', 'startswith', 'endswith', 'regex'."
        )
    col_name = build_text_filter_col_name(
        source_col=source_col, op=op_norm, value=value, case=case, na=na
    )
    header_map = _get_sheet_header_map(ws_source)
    if _normalize_header_name(col_name) in {_normalize_header_name(k) for k in header_map}:
        return col_name

    df = source_df.copy()
    df.columns = [_normalize_header_name(c) for c in df.columns]
    source_col_n = _normalize_header_name(source_col)
    _validate_required_columns(df, [source_col_n], context="add_text_filter_col_in_ws")

    ss = df[source_col_n].astype(str)
    pat = str(value)
    if op_norm == "contains":
        m = ss.str.contains(re.escape(pat), case=case, regex=True, na=na)
    elif op_norm == "regex":
        m = ss.str.contains(pat, case=case, regex=True, na=na)
    elif op_norm == "startswith":
        if case:
            m = ss.str.startswith(pat, na=False)
        else:
            m = ss.str.lower().str.startswith(pat.lower(), na=False)
    else:
        if case:
            m = ss.str.endswith(pat, na=False)
        else:
            m = ss.str.lower().str.endswith(pat.lower(), na=False)

    flags = m.astype(int).tolist()

    target_col = ws_source.max_column + 1
    hdr = ws_source.cell(row=1, column=target_col)
    hdr.value = col_name
    for i, fv in enumerate(flags, start=2):
        ws_source.cell(row=i, column=target_col).value = int(fv)
    return col_name


def ensure_text_filter_helper_columns_from_cfg_in_ws(
    *, ws_source, source_df: pd.DataFrame, cfg: dict
) -> dict[int, str]:
    split = split_filter_rules_for_formula_usage(cfg)
    out: dict[int, str] = {}
    for i, rule in enumerate(split["text_helper_rules"]):
        source_col = rule.get("col")
        op = str(rule.get("op", "")).strip().lower()
        case = bool(rule.get("case", True))
        na = bool(rule.get("na", False))
        value = rule.get("pattern", rule.get("value", None))
        if value is None:
            raise ValueError(f"Textfilter-Regel ohne value/pattern gefunden: {rule}")
        helper = add_text_filter_col_in_ws(
            ws_source=ws_source,
            source_df=source_df,
            source_col=str(source_col),
            op=op,
            value=str(value),
            case=case,
            na=na,
        )
        out[i] = helper
    return out


def build_accrual_col_name(
    *,
    value_col: str,
    invoice_col: str,
    start_col: str,
    end_col: str,
    period_label: str,
) -> str:
    """Human-readable Excel header; uniqueness via stable hash of full signature."""
    legacy = (
        "__AUDIT_ACCRUAL__"
        f"VAL_{_sanitize_helper_token(value_col)}__"
        f"INV_{_sanitize_helper_token(invoice_col)}__"
        f"START_{_sanitize_helper_token(start_col)}__"
        f"END_{_sanitize_helper_token(end_col)}__"
        f"PERIOD_{_sanitize_helper_token(period_label)}"
    )
    hid = _stable_short_hash(legacy, length=10)

    def short(name: str, max_len: int = 22) -> str:
        t = _sanitize_helper_token(name).replace("_", " ")
        if len(t) <= max_len:
            return t
        return t[: max_len - 1] + "…"

    per = short(period_label, 16)
    return (
        f"Accrual · recognized in {per} · Rev {short(value_col)} · "
        f"Inv {short(invoice_col)} · {short(start_col)}→{short(end_col)} · [{hid}]"
    )


def add_accrual_col_in_ws(
    *,
    ws_source,
    source_df: pd.DataFrame,
    value_col: str,
    invoice_col: str,
    start_col: str,
    end_col: str,
    period_label: str,
    period_start: pd.Timestamp,
    period_end: pd.Timestamp,
) -> tuple[str, int]:
    """Create or refresh one accrual helper column; returns (header name, 1-based col index)."""
    col_name = build_accrual_col_name(
        value_col=value_col,
        invoice_col=invoice_col,
        start_col=start_col,
        end_col=end_col,
        period_label=period_label,
    )
    col_key = _normalize_header_name(col_name)
    header_map = _get_sheet_header_map(ws_source)
    target_col = header_map.get(col_key)
    if target_col is None:
        target_col = (ws_source.max_column or 0) + 1

    df = source_df.copy()
    df.columns = [_normalize_header_name(c) for c in df.columns]
    req = [
        _normalize_header_name(value_col),
        _normalize_header_name(invoice_col),
        _normalize_header_name(start_col),
        _normalize_header_name(end_col),
    ]
    _validate_required_columns(df, req, context="add_accrual_col_in_ws")
    value_col_n, invoice_col_n, start_col_n, end_col_n = req

    tmp = df.copy()
    tmp["value"] = pd.to_numeric(tmp[value_col_n], errors="coerce").fillna(0.0)
    mini_cfg = {
        "invoice_col": invoice_col_n,
        "start_col": start_col_n,
        "end_col": end_col_n,
    }
    tmp[invoice_col_n] = pd.to_datetime(tmp[invoice_col_n], errors="coerce", dayfirst=True)
    tmp[start_col_n] = pd.to_datetime(tmp[start_col_n], errors="coerce", dayfirst=True)
    tmp[end_col_n] = pd.to_datetime(tmp[end_col_n], errors="coerce", dayfirst=True)

    amounts = recognized_value(tmp, period_start, period_end, mini_cfg)
    vals = amounts.fillna(0.0).tolist()

    ws_source.cell(row=1, column=target_col).value = col_name
    for i, v in enumerate(vals, start=2):
        ws_source.cell(row=i, column=target_col).value = float(v) if pd.notna(v) else None
    return col_name, target_col
