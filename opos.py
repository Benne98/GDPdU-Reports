"""OPOS aging report — trade debtors / creditors (detail + summary sheets)."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
SCRIPTS_DIR = BASE_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from funktionssammlung import (  # noqa: E402
    _get_sheet_header_map,
    apply_filters,
    build_chunked_row_sum_formula,
    build_output_file_path,
    build_sumifs_formula_body,
    ensure_output_writable,
    source_range_ref,
    write_source_df_to_ws,
)
from gst_excel_theme import (  # noqa: E402
    THEME,
    apply_recon_portfolio_layout,
    apply_zero_row_conditional_formatting,
)

MONTH_ABBR = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}

DEFAULT_THRESHOLDS = (30, 60, 90, 180)
DEFAULT_SORT_BUCKETS = (
    "overdue_1_30", "overdue_31_60", "overdue_61_90", "overdue_91_180", "overdue_over_180",
)
SORT_METRIC_TOTAL = "__total__"

SIDE_LABELS = {
    "debitor": {
        "partner_header": "Debitors",
        "detail_sheet": "Trade debtors ageing detail",
        "summary_sheet": "Trade debtors ageing",
        "sum_label": "Trade receivables (sum)",
        "reported_label": "Trade receivables (reported)",
        "subtitle": "Trade debtor's ageing",
    },
    "kreditor": {
        "partner_header": "Creditors",
        "detail_sheet": "Trade creditors ageing detail",
        "summary_sheet": "Trade creditors ageing",
        "sum_label": "Trade payables (sum)",
        "reported_label": "Trade payables (reported)",
        "subtitle": "Trade creditor's ageing",
    },
}

SUMMARY_ROLLUP = (
    ("current", "Not yet due", ("not_yet_due",)),
    ("1_30", "1-30 days", ("overdue_1_30",)),
    ("31_60", "31-60 days", ("overdue_31_60",)),
    ("over_60", ">60 days", ("overdue_61_90", "overdue_91_180", "overdue_over_180")),
)

POS_COL = 10
SPACER_WIDTH = 1.14
VALUE_COL_WIDTH = 8
REPEAT_POS_WIDTH = 28
BUCKET_COL_WIDTH_OVERRIDES = {"overdue_91_180": 11.0}
SUMMARY_LABEL_COL_WIDTH = REPEAT_POS_WIDTH * 0.7
SUMMARY_VALUE_COL_WIDTH = VALUE_COL_WIDTH * 2
ROW_HEIGHT = 12
NUM_FMT = "#,##0;(#,##0);-"
UNIT_LABEL = "kEUR"
FILL_PADDING_COLS = 40
AUTOSIZE_MIN_WIDTH = 8
AUTOSIZE_MAX_WIDTH = 32
LABEL_COL_MAX_WIDTH = 36

PROJECT_TITLE_ROW = 1
SUBTITLE_ROW = 2
BUCKET_BOUND_ROW_LO = 3
BUCKET_BOUND_ROW_HI = 4
BLOCK_TITLE_ROW = 7
HEADER_ROW = 8
DATA_START_ROW = 9
KEY_COL = 2

FONT_BASE = THEME.font_base
FONT_BOLD = THEME.font_bold
FONT_HEADER = THEME.font_header
FONT_TITLE = Font(name=THEME.font_name, size=THEME.font_size, bold=True, color=THEME.text_brand_title)
FONT_PROJECT = Font(name=THEME.font_name, size=THEME.font_size_title, color=THEME.text_brand_title)
FONT_SUBTITLE = Font(name=THEME.font_name, size=THEME.font_size_subtitle, color=THEME.text_brand_title)
FILL_HEADER = THEME.fill_header
FILL_SUBTOTAL = THEME.fill_subtotal
FILL_WHITE = THEME.fill_white
FILL_TECH = THEME.fill_tech
ALIGN_LEFT = Alignment(horizontal="left", vertical="center")
ALIGN_RIGHT = Alignment(horizontal="right", vertical="center")
ALIGN_CENTER = Alignment(horizontal="center", vertical="center")
THIN = Side(style="thin", color=THEME.border_color)
BORDER_SUBTOTAL_TOP = THEME.border_subtotal_top
BORDER_SUBTOTAL_TB = Border(
    top=THEME.border_subtotal_top.top,
    bottom=THEME.border_subtotal_top.top,
)
BORDER_MEDIUM_LEFT = Side(style="medium", color=THEME.border_strong)

_ZERO_TOL = 1e-9


@dataclass(frozen=True)
class AgingBucket:
    key: str
    label: str
    min_days: int
    max_days: int | None


@dataclass(frozen=True)
class AsOfPeriod:
    label: str
    date: pd.Timestamp


@dataclass(frozen=True)
class OposDetailLine:
    kind: str  # partner | top_bucket
    partner_id: str | None = None
    bucket_label: str | None = None


def col_letter(idx: int) -> str:
    return get_column_letter(idx)


def as_of_period_label(dt: pd.Timestamp) -> str:
    return f"{MONTH_ABBR[int(dt.month)]}{str(dt.year)[-2:]}A"


def detect_side_from_partner_col(partner_col: str) -> str:
    s = str(partner_col or "").strip().lower()
    if "kreditor" in s or "creditor" in s or "payable" in s:
        return "kreditor"
    if "debitor" in s or "debtor" in s or "receivable" in s:
        return "debitor"
    return "debitor"


def normalize_sort_basis(raw: str) -> str:
    basis = str(raw or "most_recent").strip().lower()
    if basis in ("latest_fy", "most_recent"):
        return "most_recent"
    if basis in ("all_fys", "all_dates"):
        return "all_dates"
    return "most_recent"


def _coerce_snapshots(cfg: dict) -> list[dict]:
    raw = cfg.get("snapshots")
    if isinstance(raw, list) and raw:
        out: list[dict] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            fp = str(item.get("file_path") or "").strip()
            as_of = str(item.get("as_of") or "").strip()
            if not fp or not as_of:
                raise ValueError("Each snapshot requires as_of and file_path")
            out.append(
                {
                    "as_of": as_of,
                    "file_path": fp,
                    "sheet_name": str(item.get("sheet_name") or "").strip(),
                }
            )
        return out
    fp = str(cfg.get("file_path") or "").strip()
    as_of = str(cfg.get("as_of") or "").strip()
    if fp and as_of:
        return [
            {
                "as_of": as_of,
                "file_path": fp,
                "sheet_name": str(cfg.get("sheet_name") or "").strip(),
            }
        ]
    raise ValueError("CONFIG missing snapshots[] or file_path+as_of")


def resolve_snapshot_sheet_name(file_path: str, partner_col: str, preferred: str = "") -> str:
    if str(preferred or "").strip():
        return str(preferred).strip()
    xl = pd.ExcelFile(file_path, engine="openpyxl")
    names = xl.sheet_names
    if not names:
        raise ValueError(f"No worksheets in {file_path}")
    side = detect_side_from_partner_col(partner_col)
    hints = (
        ["kreditor", "creditor", "payable"] if side == "kreditor" else ["debitor", "debtor", "receivable"]
    )
    stem = Path(file_path).stem.lower()
    for hint in hints:
        if hint in stem:
            for sn in names:
                if hint in str(sn).lower():
                    return sn
    for hint in hints:
        for sn in names:
            if hint in str(sn).lower():
                return sn
    return names[0]


def source_sheet_name_for_label(period_label: str) -> str:
    return f"__SOURCE__{period_label}"[:31]


def _bucket_col_width(bucket_key: str | None) -> float:
    if bucket_key and bucket_key in BUCKET_COL_WIDTH_OVERRIDES:
        return BUCKET_COL_WIDTH_OVERRIDES[bucket_key]
    return VALUE_COL_WIDTH


def _apply_block_value_col_widths(ws, block: dict, buckets: list[AgingBucket]) -> None:
    cc = block["year_startcol"]
    for bucket in buckets:
        ws.column_dimensions[col_letter(cc)].width = _bucket_col_width(bucket.key)
        cc += 1
    ws.column_dimensions[col_letter(cc)].width = VALUE_COL_WIDTH


def _normalize_top_bucket_cfg(cfg: dict) -> dict:
    tb = dict(cfg.get("top_bucket") or {})
    legacy_n = int(cfg.get("top_n_subtotal") or 0)
    if legacy_n > 0 and not cfg.get("top_bucket"):
        tb = {
            "enabled": True,
            "numbers": (legacy_n,),
            "create_other_bucket": False,
            "other_bucket_label": "Other",
        }
    tb.setdefault("enabled", bool(tb.get("enabled", False)))
    tb.setdefault("numbers", (10, 20, 50))
    tb.setdefault("create_other_bucket", False)
    tb.setdefault("other_bucket_label", "Other")
    tb["numbers"] = tuple(int(x) for x in (tb.get("numbers") or (10, 20, 50)))
    tb["enabled"] = bool(tb.get("enabled", False))
    tb["create_other_bucket"] = bool(tb.get("create_other_bucket", False))
    tb["other_bucket_label"] = str(tb.get("other_bucket_label") or "Other").strip() or "Other"
    return tb


def normalize_config(raw: dict) -> dict:
    cfg = dict(raw or {})
    cols = dict(cfg.get("columns") or {})
    required = ("partner_id", "partner_name", "amount", "due_date")
    missing = [k for k in required if not str(cols.get(k) or "").strip()]
    if missing:
        raise ValueError(f"CONFIG columns missing: {missing}")

    partner_id = str(cols["partner_id"]).strip()
    side = str(cfg.get("side") or detect_side_from_partner_col(partner_id)).strip().lower()
    if side not in SIDE_LABELS:
        raise ValueError(f"CONFIG side must be debitor|kreditor, got {side!r}")

    snapshots = _coerce_snapshots(cfg)
    for snap in snapshots:
        snap["sheet_name"] = resolve_snapshot_sheet_name(
            snap["file_path"], partner_id, snap.get("sheet_name", "")
        )

    ab = dict(cfg.get("aging_buckets") or {})
    include_current = bool(ab.get("include_current", True))

    sort_cfg = dict(cfg.get("sort") or {})
    sort_basis = normalize_sort_basis(sort_cfg.get("basis"))
    sort_metric = str(sort_cfg.get("metric") or "").strip().lower()
    raw_bucket_keys = list(sort_cfg.get("bucket_keys") or [])
    if sort_metric in ("total", "total_amount") or (
        not sort_metric and not raw_bucket_keys
    ):
        sort_metric = "total"
        bucket_keys = [SORT_METRIC_TOTAL]
    elif sort_metric in ("buckets", "bucket") or raw_bucket_keys:
        sort_metric = "buckets"
        bucket_keys = raw_bucket_keys or list(DEFAULT_SORT_BUCKETS)
    else:
        sort_metric = "total"
        bucket_keys = [SORT_METRIC_TOTAL]
    top_bucket = _normalize_top_bucket_cfg(cfg)

    filters = dict(cfg.get("filters") or {})
    filters.setdefault("enabled", False)
    filters.setdefault("rules", [])

    cfg.update(
        {
            "side": side,
            "columns": {k: str(cols[k]).strip() for k in required},
            "snapshots": snapshots,
            "aging_buckets": {
                "mode": "fixed",
                "thresholds": DEFAULT_THRESHOLDS,
                "boundaries": DEFAULT_THRESHOLDS,
                "include_current": include_current,
            },
            "sort": {"basis": sort_basis, "metric": sort_metric, "bucket_keys": bucket_keys},
            "top_bucket": top_bucket,
            "top_n_subtotal": int(cfg.get("top_n_subtotal") or 0),
            "filters": filters,
            "formula_mode": bool(cfg.get("formula_mode", True)),
            "title": str(cfg.get("title") or cfg.get("project_name") or "Project").strip(),
            "company": str(cfg.get("company") or cfg.get("company_name") or "Group").strip(),
            "base_sheet_name": str(cfg.get("base_sheet_name") or "Source").strip(),
        }
    )
    for key in ("output_file_path", "case_id"):
        if not str(cfg.get(key) or "").strip():
            raise ValueError(f"CONFIG missing required key: {key}")
    # Legacy top-level fields for backend file injection
    cfg["file_path"] = snapshots[0]["file_path"]
    cfg["sheet_name"] = snapshots[0]["sheet_name"]
    return cfg


def build_aging_bucket_defs(cfg: dict) -> list[AgingBucket]:
    """Fixed overdue-day buckets (30/60/90/180) — not configurable."""
    ab = cfg.get("aging_buckets") or {}
    include_current = bool(ab.get("include_current", True))
    out: list[AgingBucket] = []
    if include_current:
        out.append(AgingBucket("not_yet_due", "Not yet due", 0, 0))
    specs = [
        ("overdue_1_30", "1-30 days", 1, 30),
        ("overdue_31_60", "31-60 days", 31, 60),
        ("overdue_61_90", "61-90 days", 61, 90),
        ("overdue_91_180", "91-180 days", 91, 180),
        ("overdue_over_180", ">180 days", 181, None),
    ]
    for key, label, lo, hi in specs:
        out.append(AgingBucket(key, label, lo, hi))
    return out


def build_opos_periods(cfg: dict) -> list[AsOfPeriod]:
    periods: list[AsOfPeriod] = []
    for snap in sorted(cfg["snapshots"], key=lambda s: pd.Timestamp(s["as_of"])):
        dt = pd.Timestamp(snap["as_of"]).normalize()
        periods.append(AsOfPeriod(as_of_period_label(dt), dt))
    if not periods:
        raise ValueError("CONFIG snapshots produced no periods")
    return periods


def build_opos_as_of_periods(cfg: dict) -> list[AsOfPeriod]:
    """Backward-compatible alias."""
    return build_opos_periods(cfg)


def _parse_date_series(s: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(s):
        parsed = pd.to_datetime(s, errors="coerce", unit="D", origin="1899-12-30")
        if int(parsed.notna().sum()) > 0:
            return parsed
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    as_str = s.astype(str).str.strip()
    iso_mask = as_str.str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)
    if iso_mask.any():
        out.loc[iso_mask] = pd.to_datetime(s.loc[iso_mask], errors="coerce", format="%Y-%m-%d")
    rest = s.notna() & ~iso_mask
    if rest.any():
        out.loc[rest] = pd.to_datetime(s.loc[rest], errors="coerce", dayfirst=True)
    return out


def _clean_text_series(s: pd.Series) -> pd.Series:
    out = s.astype(object).where(s.notna(), "")
    out = out.astype(str).str.strip()
    return out.replace({"nan": "", "NaN": "", "None": "", "<NA>": ""})


def _display_partner_name(pid: str, name: str) -> str:
    n = str(name or "").strip()
    if not n or n.lower() in ("nan", "none", "<na>"):
        return str(pid)
    return n


def build_partner_meta_from_snapshots(cfg: dict) -> dict[str, str]:
    """Partner display names from full snapshot rows (incl. rows without due date)."""
    cols = cfg["columns"]
    meta: dict[str, str] = {}
    for snap in cfg["snapshots"]:
        df = pd.read_excel(
            snap["file_path"],
            sheet_name=snap["sheet_name"],
            engine="openpyxl",
        )
        d = apply_filters(df.copy(), cfg)
        ids = _clean_text_series(d[cols["partner_id"]])
        names = _clean_text_series(d[cols["partner_name"]])
        for pid, name in zip(ids, names):
            if not pid:
                continue
            if name and (pid not in meta or meta[pid] == pid):
                meta[pid] = name
            elif pid not in meta:
                meta[pid] = pid
    return {pid: _display_partner_name(pid, name) for pid, name in meta.items()}


def _fill_partner_names_in_df(
    df: pd.DataFrame, cfg: dict, partner_meta: dict[str, str]
) -> pd.DataFrame:
    """Propagate display name to every source row so SUMIFS can match on name only."""
    cols = cfg["columns"]
    out = df.copy()
    ids = _clean_text_series(out[cols["partner_id"]])
    out[cols["partner_name"]] = [
        _display_partner_name(pid, partner_meta.get(pid, pid)) for pid in ids
    ]
    return out


def preprocess_opos_input(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    cols = cfg["columns"]
    d = apply_filters(df.copy(), cfg)

    d["_partner_id"] = _clean_text_series(d[cols["partner_id"]])
    d["_partner_name"] = _clean_text_series(d[cols["partner_name"]])
    d["_amount"] = pd.to_numeric(d[cols["amount"]], errors="coerce").fillna(0.0)
    d["_due_date"] = _parse_date_series(d[cols["due_date"]])

    d = d[
        d["_partner_id"].notna()
        & (d["_partner_id"] != "")
        & d["_due_date"].notna()
    ]
    return d


def audit_missing_due_date_rows(
    snapshots: list[dict],
    columns: dict[str, str],
) -> dict:
    """Rows with partner id but missing due date — same rule as preprocess_opos_input."""
    required = ("partner_id", "partner_name", "amount", "due_date")
    missing_cols = [k for k in required if not str(columns.get(k) or "").strip()]
    if missing_cols:
        raise ValueError(f"columns missing for due-date audit: {missing_cols}")

    col_map = {k: str(columns[k]).strip() for k in required}
    total_excluded = 0
    details: list[dict] = []
    for snap in snapshots:
        fp = str(snap.get("file_path") or "").strip()
        as_of = str(snap.get("as_of") or "").strip()
        if not fp or not os.path.isfile(fp):
            continue
        sheet = str(snap.get("sheet_name") or "").strip()
        if not sheet:
            sheet = resolve_snapshot_sheet_name(fp, col_map["partner_id"], "")
        df = pd.read_excel(fp, sheet_name=sheet, engine="openpyxl")
        tmp_cfg = {"columns": col_map, "filters": {"enabled": False}}
        d = apply_filters(df.copy(), tmp_cfg)
        ids = _clean_text_series(d[col_map["partner_id"]])
        due = _parse_date_series(d[col_map["due_date"]])
        valid_id = ids.notna() & (ids != "")
        excluded = int((valid_id & due.isna()).sum())
        if excluded > 0:
            details.append({"as_of": as_of, "excluded_rows": excluded})
            total_excluded += excluded
    return {"total_excluded": total_excluded, "snapshots": details}


def assign_bucket_for_due(
    due: pd.Timestamp, as_of: pd.Timestamp, buckets: list[AgingBucket]
) -> str:
    due_n = pd.Timestamp(due).normalize()
    as_of_n = pd.Timestamp(as_of).normalize()
    if due_n > as_of_n:
        return "not_yet_due"
    days_overdue = int((as_of_n - due_n).days)
    if days_overdue <= 0:
        days_overdue = 1
    return assign_bucket_key(days_overdue, buckets)


def assign_bucket_key(days_overdue: int, buckets: list[AgingBucket]) -> str:
    if days_overdue <= 0:
        for b in buckets:
            if b.key == "not_yet_due":
                return b.key
        return buckets[0].key if buckets else "not_yet_due"
    for b in buckets:
        if b.key == "not_yet_due":
            continue
        lo = b.min_days
        hi = b.max_days
        if hi is None and days_overdue >= lo:
            return b.key
        if hi is not None and lo <= days_overdue <= hi:
            return b.key
    return buckets[-1].key if buckets else "overdue_over_180"


def overdue_bucket_keys(buckets: list[AgingBucket]) -> list[str]:
    return [b.key for b in buckets if b.key != "not_yet_due"]


def _opos_sort_context(
    cfg: dict, periods: list[AsOfPeriod], buckets: list[AgingBucket]
) -> tuple[list[str], list[AsOfPeriod]]:
    metric = str(cfg.get("sort", {}).get("metric") or "total").strip().lower()
    raw_keys = list(cfg.get("sort", {}).get("bucket_keys") or [])
    if metric == "total" or raw_keys == [SORT_METRIC_TOTAL]:
        sort_keys = [SORT_METRIC_TOTAL]
    else:
        sort_keys = [k for k in raw_keys if any(b.key == k for b in buckets)]
        if not sort_keys:
            sort_keys = [SORT_METRIC_TOTAL]
    sort_periods = periods[-1:] if cfg["sort"]["basis"] == "most_recent" else periods
    return sort_keys, sort_periods


def _partner_sort_score(
    partner_key: str,
    grid: dict[str, dict[str, dict[str, float]]],
    sort_periods: list[AsOfPeriod],
    sort_keys: list[str],
    buckets: list[AgingBucket],
) -> float:
    """Ranking metric — total open balance or selected bucket keys."""
    total = 0.0
    for period in sort_periods:
        bmap = grid.get(period.label, {}).get(partner_key, {})
        if SORT_METRIC_TOTAL in sort_keys:
            total += sum(float(bmap.get(b.key, 0.0)) for b in buckets)
        else:
            total += sum(float(bmap.get(k, 0.0)) for k in sort_keys)
    return total


def _label_for_top_range(start: int, end: int) -> str:
    a = start + 1
    b = end
    if a == b:
        return f"Top {a}"
    if a == 1:
        return f"Top {b}"
    return f"Top {a}-{b}"


def compute_top_bucket_range_ends(
    partner_order: list[str],
    partner_scores: list[float],
    cfg: dict,
) -> list[tuple[int, str]]:
    """(end_exclusive, label) — fixed partner counts per bucket, optional Other tail."""
    tb = cfg.get("top_bucket") or {}
    if not tb.get("enabled"):
        return []
    n = len(partner_order)
    if n == 0:
        return []

    numbers = tuple(tb.get("numbers", ()) or (10, 20, 50))
    create_other = bool(tb.get("create_other_bucket", False))
    other_label = str(tb.get("other_bucket_label", "Other")).strip() or "Other"

    out: list[tuple[int, str]] = []
    start = 0
    for num in numbers:
        end = min(start + int(num), n)
        if end > start:
            out.append((end, _label_for_top_range(start, end)))
        start = end
        if start >= n:
            break
    if create_other and start < n:
        out.append((n, other_label))
    return out


def build_opos_detail_lines(
    partner_order: list[str],
    grid: dict[str, dict[str, dict[str, float]]],
    cfg: dict,
    periods: list[AsOfPeriod],
    buckets: list[AgingBucket],
) -> list[OposDetailLine]:
    sort_keys, sort_periods = _opos_sort_context(cfg, periods, buckets)
    scores = [
        _partner_sort_score(pid, grid, sort_periods, sort_keys, buckets) for pid in partner_order
    ]
    segments = compute_top_bucket_range_ends(partner_order, scores, cfg)
    if not segments:
        return [OposDetailLine("partner", partner_id=pid) for pid in partner_order]

    lines: list[OposDetailLine] = []
    prev = 0
    for end, label in segments:
        for i in range(prev, end):
            lines.append(OposDetailLine("partner", partner_id=partner_order[i]))
        lines.append(OposDetailLine("top_bucket", bucket_label=label))
        prev = end
    if prev < len(partner_order):
        for i in range(prev, len(partner_order)):
            lines.append(OposDetailLine("partner", partner_id=partner_order[i]))
    return lines


def _partner_display_meta(
    cfg: dict, snapshot_dfs: dict[str, pd.DataFrame]
) -> dict[str, str]:
    """partner_id -> display name (Text), from snapshot files or preprocessed frames."""
    snaps = cfg.get("snapshots") or []
    if snaps and all(
        os.path.isfile(str(s.get("file_path") or "").strip()) for s in snaps
    ):
        return build_partner_meta_from_snapshots(cfg)

    out: dict[str, str] = {}
    for d in snapshot_dfs.values():
        if d is None or d.empty:
            continue
        for pid, grp in d.groupby("_partner_id", dropna=False):
            pid_s = str(pid).strip()
            if not pid_s:
                continue
            names = grp["_partner_name"]
            names = names[(names != "") & names.notna()]
            raw_name = names.iloc[0] if len(names) else pid_s
            out[pid_s] = _display_partner_name(pid_s, raw_name)
    return out


def aggregate_opos(
    snapshot_dfs: dict[str, pd.DataFrame],
    cfg: dict,
    periods: list[AsOfPeriod],
    buckets: list[AgingBucket],
) -> tuple[dict[str, dict[str, dict[str, float]]], list[str], dict[str, str]]:
    """period_label -> partner_name -> bucket_key -> kEUR amount (grouped by display name)."""
    meta = _partner_display_meta(cfg, snapshot_dfs)
    sort_keys, sort_periods = _opos_sort_context(cfg, periods, buckets)
    bucket_key_list = [b.key for b in buckets]

    partner_meta: dict[str, str] = {}
    grid: dict[str, dict[str, dict[str, float]]] = {p.label: {} for p in periods}

    for period in periods:
        d = snapshot_dfs.get(period.label)
        if d is None or d.empty:
            continue
        as_of = period.date.normalize()
        sub = d.copy()
        sub["_bucket"] = [
            assign_bucket_for_due(row["_due_date"], as_of, buckets) for _, row in sub.iterrows()
        ]
        sub["_keur"] = sub["_amount"] / 1000.0
        for pid, grp in sub.groupby("_partner_id", dropna=False):
            pid_s = str(pid).strip()
            if not pid_s:
                continue
            names = grp["_partner_name"]
            names = names[(names != "") & names.notna()]
            raw_name = names.iloc[0] if len(names) else pid_s
            gkey = _display_partner_name(pid_s, meta.get(pid_s, raw_name))
            bucket_sums = grp.groupby("_bucket")["_keur"].sum().to_dict()
            row = grid[period.label].setdefault(
                gkey, {k: 0.0 for k in bucket_key_list}
            )
            for bk, val in bucket_sums.items():
                if bk in row:
                    row[bk] = float(row.get(bk, 0.0)) + float(val)
            partner_meta[gkey] = gkey

    scores: dict[str, float] = {}
    for gkey in partner_meta:
        scores[gkey] = _partner_sort_score(gkey, grid, sort_periods, sort_keys, buckets)

    partner_order = sorted(partner_meta.keys(), key=lambda k: (-scores.get(k, 0.0), k.lower()))
    return grid, partner_order, partner_meta


def get_opos_excel_layout(cfg: dict) -> dict:
    """A–C collapsed; formula key in B; partner label in J; bucket bounds rows 3–4."""
    use_formulas = bool(cfg.get("formula_mode", True))
    return {
        "key_col": KEY_COL,
        "label_col": POS_COL,
        "col_offset": 3,
        "bucket_bound_lo_row": BUCKET_BOUND_ROW_LO if use_formulas else None,
        "bucket_bound_hi_row": BUCKET_BOUND_ROW_HI if use_formulas else None,
        "header_row": HEADER_ROW,
        "data_start_row": DATA_START_ROW,
        "helper_right_col": 3,
    }


def _cell_display_width(value) -> int:
    if value is None:
        return 0
    if isinstance(value, str) and value.startswith("="):
        return 12
    return len(str(value))


def _autosize_ws_columns(
    ws,
    col_indices: list[int],
    *,
    min_width: float = AUTOSIZE_MIN_WIDTH,
    max_width: float = AUTOSIZE_MAX_WIDTH,
) -> None:
    for col_idx in col_indices:
        letter = col_letter(col_idx)
        best = min_width
        for row in ws.iter_rows(min_col=col_idx, max_col=col_idx):
            for cell in row:
                best = max(best, _cell_display_width(cell.value))
        ws.column_dimensions[letter].width = min(
            max(best * 1.05, min_width),
            max_width if col_idx != POS_COL else LABEL_COL_MAX_WIDTH,
        )


def write_opos_formula_key_column(
    ws,
    layout: dict,
    cfg: dict,
    partner_row_names: dict[int, str],
    footer_rows: dict[str, int],
) -> None:
    """Collapsed helper column B (same partner text as visible column J)."""
    key_col = layout["key_col"]
    header_row = layout["header_row"]
    helper_right = layout["helper_right_col"]
    partner_col_name = str(cfg["columns"]["partner_name"]).strip()

    hdr = ws.cell(header_row, key_col, partner_col_name)
    hdr.font = FONT_HEADER
    hdr.alignment = ALIGN_LEFT

    for c in range(1, helper_right + 1):
        if c != key_col:
            ws.cell(header_row, c).value = None

    for row_idx, pname in partner_row_names.items():
        for c in range(1, helper_right + 1):
            if c != key_col:
                ws.cell(row_idx, c).value = None
        cell = ws.cell(row_idx, key_col, pname)
        cell.font = FONT_BASE
        cell.alignment = ALIGN_LEFT

    for row_idx in footer_rows.values():
        for c in range(1, helper_right + 1):
            ws.cell(row_idx, c).value = None


def _opos_red_fonts():
    """Bucket-bound helper dates only (hidden rows)."""
    red = "FFFF5149"
    red_font = Font(name=THEME.font_name, size=THEME.font_size, color=red)
    return red_font, red_font


def bucket_due_bounds(
    bucket: AgingBucket, as_of: pd.Timestamp
) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    """Due-date bounds for Excel SUMIFS (aligned with assign_bucket_for_due)."""
    as_of = pd.Timestamp(as_of).normalize()
    if bucket.key == "not_yet_due":
        return as_of, None
    lo_days = int(bucket.min_days)
    hi_days = bucket.max_days
    if hi_days is not None:
        hi_date = as_of if bucket.key == "overdue_1_30" else as_of - pd.Timedelta(days=lo_days)
        return as_of - pd.Timedelta(days=int(hi_days)), hi_date
    # >180 days overdue → due on or before as_of - 181 days
    return None, as_of - pd.Timedelta(days=181)


def _value_col_count(buckets: list[AgingBucket]) -> int:
    return len(buckets) + 1


def build_opos_blocks(periods: list[AsOfPeriod], buckets: list[AgingBucket]) -> list[dict]:
    """First block: partner col J + values; later blocks: values only + spacers."""
    blocks: list[dict] = []
    n_val = _value_col_count(buckets)
    current_col = POS_COL
    for i, period in enumerate(periods):
        if i == 0:
            blocks.append(
                {
                    "period": period,
                    "has_plpos": True,
                    "kind": "period",
                    "key": period.label,
                    "source_sheet": source_sheet_name_for_label(period.label),
                    "startcol": POS_COL,
                    "poscol": POS_COL,
                    "year_startcol": POS_COL + 1,
                    "year_endcol": POS_COL + n_val,
                    "spacer_col": POS_COL + n_val + 1,
                }
            )
            current_col = POS_COL + n_val + 2
        else:
            blocks.append(
                {
                    "period": period,
                    "has_plpos": False,
                    "kind": "period",
                    "key": period.label,
                    "source_sheet": source_sheet_name_for_label(period.label),
                    "startcol": current_col,
                    "year_startcol": current_col,
                    "year_endcol": current_col + n_val - 1,
                    "spacer_col": current_col + n_val,
                }
            )
            current_col = current_col + n_val + 1
    return blocks


def enrich_opos_source_df(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Tag rows included by filters (for optional future use)."""
    cols = cfg["columns"]
    d = df.copy()
    d["_partner_id"] = d[cols["partner_id"]].astype(str).str.strip()
    if cfg.get("filters", {}).get("enabled"):
        kept = apply_filters(df.copy(), cfg)
        d["_opos_include"] = d.index.isin(set(kept.index))
    else:
        d["_opos_include"] = True
    return d


def load_snapshot_dataframes(cfg: dict) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for snap in cfg["snapshots"]:
        dt = pd.Timestamp(snap["as_of"]).normalize()
        label = as_of_period_label(dt)
        df = pd.read_excel(
            snap["file_path"],
            sheet_name=snap["sheet_name"],
            engine="openpyxl",
        )
        out[label] = preprocess_opos_input(df, cfg)
    return out


def _coerce_excel_date_value(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, pd.Timestamp):
        return val.to_pydatetime()
    from datetime import date, datetime

    if isinstance(val, datetime):
        return val
    if isinstance(val, date):
        return datetime(val.year, val.month, val.day)
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        ts = pd.to_datetime(val, unit="D", origin="1899-12-30", errors="coerce")
        if pd.notna(ts):
            return ts.to_pydatetime()
    text = str(val).strip()
    if text and text.lower() not in ("nan", "none", "<na>"):
        if pd.Series([text]).str.match(r"^\d{4}-\d{2}-\d{2}$", na=False).iloc[0]:
            ts = pd.to_datetime(text, errors="coerce", format="%Y-%m-%d")
        else:
            ts = pd.to_datetime(val, errors="coerce", dayfirst=True)
        if pd.notna(ts):
            return ts.to_pydatetime()
    return None


def _format_due_date_column(ws, df: pd.DataFrame, cfg: dict) -> None:
    due_col_name = cfg["columns"]["due_date"]
    if due_col_name not in df.columns:
        return
    col_idx = list(df.columns).index(due_col_name) + 1
    for r in range(2, (ws.max_row or 1) + 1):
        cell = ws.cell(r, col_idx)
        dt = _coerce_excel_date_value(cell.value)
        cell.value = dt
        if dt is not None:
            cell.number_format = "DD.MM.YYYY"


def bootstrap_opos_workbook(
    cfg: dict,
    output_path: str,
    *,
    partner_meta: dict[str, str] | None = None,
) -> None:
    """Write one __SOURCE__{label} sheet per snapshot (values only)."""
    if partner_meta is None:
        partner_meta = build_partner_meta_from_snapshots(cfg)
    wb = Workbook()
    wb.remove(wb.active)
    for snap in sorted(cfg["snapshots"], key=lambda s: pd.Timestamp(s["as_of"])):
        dt = pd.Timestamp(snap["as_of"]).normalize()
        label = as_of_period_label(dt)
        title = source_sheet_name_for_label(label)
        ws = wb.create_sheet(title)
        df = pd.read_excel(
            snap["file_path"],
            sheet_name=snap["sheet_name"],
            engine="openpyxl",
        )
        df = _fill_partner_names_in_df(df, cfg, partner_meta)
        snap_cfg = {**cfg, "file_path": snap["file_path"], "sheet_name": snap["sheet_name"]}
        write_source_df_to_ws(ws, df, snap_cfg)
        _format_due_date_column(ws, df, cfg)
    wb.save(output_path)
    wb.close()


def _partner_has_any_balance(
    pid: str,
    grid: dict[str, dict[str, dict[str, float]]],
    periods: list[AsOfPeriod],
    buckets: list[AgingBucket],
) -> bool:
    for period in periods:
        bmap = grid.get(period.label, {}).get(pid, {})
        if any(abs(float(bmap.get(b.key, 0.0))) > _ZERO_TOL for b in buckets):
            return True
    return False


def filter_zero_partners(
    partner_order: list[str],
    grid: dict[str, dict[str, dict[str, float]]],
    periods: list[AsOfPeriod],
    buckets: list[AgingBucket],
) -> list[str]:
    return [pid for pid in partner_order if _partner_has_any_balance(pid, grid, periods, buckets)]


def _excel_date_literal(dt: pd.Timestamp) -> str:
    ts = pd.Timestamp(dt).normalize()
    return f"DATE({ts.year},{ts.month},{ts.day})"


def _set_cell_formula(cell, formula: str, *, bold: bool = False) -> None:
    cell.value = formula
    cell.number_format = NUM_FMT
    cell.font = FONT_BOLD if bold else FONT_BASE
    cell.alignment = ALIGN_RIGHT


def _opos_sumifs_criteria(
    source_sheet: str,
    src_header: dict[str, int],
    cfg: dict,
    period: AsOfPeriod,
    bucket: AgingBucket,
    col_idx: int | None,
    partner_row: int | None,
) -> str:
    cols = cfg["columns"]
    amount_rng = source_range_ref(source_sheet, src_header, cols["amount"])
    due_rng = source_range_ref(source_sheet, src_header, cols["due_date"])

    base_pairs: list[tuple[str, str]] = []
    if partner_row is not None:
        partner_rng = source_range_ref(source_sheet, src_header, cols["partner_name"])
        partner_ref = f"${col_letter(POS_COL)}{partner_row}"
        base_pairs.append((partner_rng, partner_ref))

    lo_date, hi_date = bucket_due_bounds(bucket, period.date)
    if bucket.key == "not_yet_due":
        if col_idx is not None:
            lo_ref = f"${col_letter(col_idx)}${BUCKET_BOUND_ROW_LO}"
            base_pairs.append((due_rng, f'">"&{lo_ref}'))
        elif lo_date is not None:
            base_pairs.append((due_rng, f'">"&{_excel_date_literal(lo_date)}'))
    else:
        if lo_date is not None:
            if col_idx is not None:
                lo_ref = f"${col_letter(col_idx)}${BUCKET_BOUND_ROW_LO}"
                base_pairs.append((due_rng, f'">="&{lo_ref}'))
            else:
                base_pairs.append((due_rng, f'">="&{_excel_date_literal(lo_date)}'))
        if hi_date is not None:
            if col_idx is not None:
                hi_ref = f"${col_letter(col_idx)}${BUCKET_BOUND_ROW_HI}"
                base_pairs.append((due_rng, f'"<="&{hi_ref}'))
            else:
                base_pairs.append((due_rng, f'"<="&{_excel_date_literal(hi_date)}'))

    return build_sumifs_formula_body(amount_rng, base_pairs, [[]], divide_by_1000=True)


def _opos_sumifs_formula(
    source_sheet: str,
    src_header: dict[str, int],
    cfg: dict,
    period: AsOfPeriod,
    bucket: AgingBucket,
    col_idx: int,
    partner_row: int | None,
) -> str:
    body = _opos_sumifs_criteria(
        source_sheet, src_header, cfg, period, bucket, col_idx, partner_row
    )
    return f'=IFERROR({body},"")'


def _rollup_sumifs_formula(
    source_sheet: str,
    src_header: dict[str, int],
    cfg: dict,
    period: AsOfPeriod,
    bucket_keys: tuple[str, ...],
    buckets: list[AgingBucket],
    col_idx: int | None,
) -> str:
    bucket_by_key = {b.key: b for b in buckets}
    parts: list[str] = []
    for bkey in bucket_keys:
        bucket = bucket_by_key.get(bkey)
        if bucket is None:
            continue
        body = _opos_sumifs_criteria(
            source_sheet, src_header, cfg, period, bucket, col_idx, partner_row=None
        )
        parts.append(f"({body})")
    if not parts:
        return "=0"
    if len(parts) == 1:
        return f'=IFERROR({parts[0]},"")'
    return f'=IFERROR({"+".join(parts)},"")'


def _row_bucket_sum_formula(row_idx: int, col_indices: list[int]) -> str:
    if not col_indices:
        return "=0"
    refs = [f"{col_letter(c)}{row_idx}" for c in col_indices]
    if len(refs) == 1:
        return f"={refs[0]}"
    inner = "+".join(f"IFERROR({r},0)" for r in refs)
    return f"=IFERROR({inner},\"\")"


def _count_sheet_formulas(ws) -> int:
    n = 0
    for row in ws.iter_rows():
        for cell in row:
            v = cell.value
            if isinstance(v, str) and v.startswith("="):
                n += 1
    return n


def _bucket_amounts(bmap: dict[str, float], buckets: list[AgingBucket]) -> dict[str, float]:
    return {b.key: float(bmap.get(b.key, 0.0)) for b in buckets}


def _overdue_total(bmap: dict[str, float], buckets: list[AgingBucket]) -> float:
    od_keys = overdue_bucket_keys(buckets)
    return sum(float(bmap.get(k, 0.0)) for k in od_keys)


def _row_total(bmap: dict[str, float], buckets: list[AgingBucket]) -> float:
    return sum(_bucket_amounts(bmap, buckets).values())


def summary_from_grid(
    grid: dict[str, dict[str, dict[str, float]]],
    periods: list[AsOfPeriod],
    buckets: list[AgingBucket],
) -> dict[str, dict[str, float]]:
    """period -> bucket_key / footer_key -> kEUR."""
    out: dict[str, dict[str, float]] = {}
    for period in periods:
        totals = {b.key: 0.0 for b in buckets}
        for bmap in grid.get(period.label, {}).values():
            for b in buckets:
                totals[b.key] += float(bmap.get(b.key, 0.0))
        row: dict[str, float] = {b.key: totals[b.key] for b in buckets}
        row["sum"] = sum(totals.values())
        row["reported"] = 0.0
        row["recon"] = row["sum"] - row["reported"]
        out[period.label] = row
    return out


def _set_num(cell, value, bold: bool = False):
    cell.value = value
    cell.number_format = NUM_FMT
    cell.font = FONT_BOLD if bold else FONT_BASE
    cell.alignment = ALIGN_RIGHT


def _write_block_headers(ws, block: dict, buckets: list[AgingBucket]) -> None:
    value_headers = [b.label for b in buckets] + ["Total"]
    plabel = block["period"].label

    if block.get("has_plpos"):
        ws.merge_cells(
            start_row=BLOCK_TITLE_ROW,
            start_column=block["year_startcol"],
            end_row=BLOCK_TITLE_ROW,
            end_column=block["year_endcol"],
        )
        title = ws.cell(BLOCK_TITLE_ROW, block["year_startcol"], f"Days overdue as of {plabel}")
        title.font = FONT_TITLE
        title.alignment = ALIGN_CENTER
        pos_title = ws.cell(BLOCK_TITLE_ROW, block["poscol"])
        pos_title.value = None
        pos_title.fill = FILL_HEADER

        hpos = ws.cell(HEADER_ROW, block["poscol"], UNIT_LABEL)
        hpos.font = FONT_HEADER
        hpos.fill = FILL_HEADER
        hpos.alignment = ALIGN_LEFT
    else:
        ws.merge_cells(
            start_row=BLOCK_TITLE_ROW,
            start_column=block["year_startcol"],
            end_row=BLOCK_TITLE_ROW,
            end_column=block["year_endcol"],
        )
        title = ws.cell(BLOCK_TITLE_ROW, block["year_startcol"], f"Days overdue as of {plabel}")
        title.font = FONT_TITLE
        title.alignment = ALIGN_CENTER

    cc = block["year_startcol"]
    for hdr in value_headers:
        hc = ws.cell(HEADER_ROW, cc, hdr)
        hc.font = FONT_HEADER
        hc.fill = FILL_HEADER
        hc.alignment = ALIGN_RIGHT
        cc += 1

    ws.column_dimensions[col_letter(block["spacer_col"])].width = SPACER_WIDTH
    if block.get("has_plpos"):
        ws.column_dimensions[col_letter(block["poscol"])].width = REPEAT_POS_WIDTH
    _apply_block_value_col_widths(ws, block, buckets)


def write_opos_bucket_bound_rows(
    ws,
    blocks: list[dict],
    buckets: list[AgingBucket],
) -> None:
    red_font, _ = _opos_red_fonts()
    for block in blocks:
        as_of = block["period"].date
        cc = block["year_startcol"]
        for bucket in buckets:
            lo_date, hi_date = bucket_due_bounds(bucket, as_of)
            c_lo = ws.cell(BUCKET_BOUND_ROW_LO, cc)
            c_hi = ws.cell(BUCKET_BOUND_ROW_HI, cc)
            if lo_date is not None:
                c_lo.value = pd.Timestamp(lo_date).to_pydatetime()
                c_lo.number_format = "DD.MM.YYYY"
                c_lo.font = red_font
                c_lo.alignment = ALIGN_RIGHT
            if hi_date is not None:
                c_hi.value = pd.Timestamp(hi_date).to_pydatetime()
                c_hi.number_format = "DD.MM.YYYY"
                c_hi.font = red_font
                c_hi.alignment = ALIGN_RIGHT
            cc += 1

    for r in (BUCKET_BOUND_ROW_LO, BUCKET_BOUND_ROW_HI):
        ws.row_dimensions[r].hidden = True
        ws.row_dimensions[r].outlineLevel = 1


def _merge_cell_border(cell, *, top=None, bottom=None, left=None, right=None) -> None:
    b = cell.border
    cell.border = Border(
        top=top if top is not None else b.top,
        bottom=bottom if bottom is not None else b.bottom,
        left=left if left is not None else b.left,
        right=right if right is not None else b.right,
    )


def _opos_table_bounds(blocks: list[dict]) -> tuple[int, int]:
    table_left = POS_COL
    table_right = max(b["year_endcol"] for b in blocks)
    return table_left, table_right


def _opos_bucket_col_indices(blocks: list[dict]) -> list[int]:
    cols: list[int] = []
    for block in blocks:
        cols.extend(range(block["year_startcol"], block["year_endcol"]))
    return cols


def _opos_total_col_indices(blocks: list[dict]) -> list[int]:
    return [b["year_endcol"] for b in blocks]


def _style_opos_row_band(
    ws,
    row: int,
    *,
    table_left: int,
    table_right: int,
    bold: bool = False,
    fill_subtotal: bool = False,
    border_top: bool = False,
    border_bottom: bool = False,
) -> None:
    medium = THEME.border_subtotal_top.top
    for cc in range(table_left, table_right + 1):
        cell = ws.cell(row, cc)
        if bold:
            cell.font = FONT_BOLD
        if fill_subtotal:
            cell.fill = FILL_SUBTOTAL
        if border_top:
            _merge_cell_border(cell, top=medium)
        if border_bottom:
            _merge_cell_border(cell, bottom=medium)


def _apply_opos_total_col_left_border(
    ws,
    blocks: list[dict],
    *,
    first_row: int,
    last_row: int,
) -> None:
    medium = BORDER_MEDIUM_LEFT
    for block in blocks:
        total_col = block["year_endcol"]
        for rr in range(first_row, last_row + 1):
            _merge_cell_border(ws.cell(rr, total_col), left=medium)


def apply_opos_detail_row_styles(
    ws,
    blocks: list[dict],
    *,
    top_bucket_rows: list[dict] | None,
    footer_rows: dict[str, int],
) -> None:
    """Subtotal borders and bold rows — aligned with top_report bucket/total lines."""
    table_left, table_right = _opos_table_bounds(blocks)

    for spec in top_bucket_rows or []:
        _style_opos_row_band(
            ws,
            int(spec["row"]),
            table_left=table_left,
            table_right=table_right,
            bold=True,
            fill_subtotal=False,
            border_top=True,
        )

    total_row = footer_rows["total"]
    recon_row = footer_rows["recon"]
    reported_row = footer_rows["reported"]
    for row in (total_row, reported_row):
        _style_opos_row_band(
            ws,
            row,
            table_left=table_left,
            table_right=table_right,
            bold=True,
            fill_subtotal=True,
            border_top=True,
        )
    _style_opos_row_band(
        ws,
        reported_row,
        table_left=table_left,
        table_right=table_right,
        border_bottom=True,
    )

    _apply_opos_total_col_left_border(
        ws,
        blocks,
        first_row=HEADER_ROW,
        last_row=reported_row,
    )


def apply_opos_sheet_formatting(
    ws,
    blocks: list[dict],
    buckets: list[AgingBucket],
    *,
    last_row: int,
    last_col: int,
    data_start_row: int = DATA_START_ROW,
    top_bucket_rows: list[dict] | None = None,
    footer_rows: dict[str, int] | None = None,
) -> None:
    spacer_cols = {b["spacer_col"] for b in blocks}
    fill_end_row = last_row + 100
    fill_end_col = last_col + FILL_PADDING_COLS

    for rr in range(1, fill_end_row + 1):
        for cc in range(1, POS_COL):
            ws.cell(rr, cc).fill = FILL_TECH
    for rr in range(1, fill_end_row + 1):
        for cc in range(POS_COL, fill_end_col + 1):
            ws.cell(rr, cc).fill = FILL_WHITE

    ws.column_dimensions[col_letter(POS_COL)].width = REPEAT_POS_WIDTH
    for block in blocks:
        if block.get("has_plpos"):
            ws.column_dimensions[col_letter(block["poscol"])].width = REPEAT_POS_WIDTH
        _apply_block_value_col_widths(ws, block, buckets)
        ws.column_dimensions[col_letter(block["spacer_col"])].width = SPACER_WIDTH

    apply_recon_portfolio_layout(
        ws,
        blocks=blocks,
        pos_col=POS_COL,
        header_row=HEADER_ROW,
        block_title_row=BLOCK_TITLE_ROW,
        entity_code_row=BUCKET_BOUND_ROW_LO,
        last_used_col=last_col,
        spacer_cols=spacer_cols,
        visible_block_kinds=frozenset(),
        collapsed_poscol_keys=(),
    )

    for block in blocks:
        if block.get("has_plpos"):
            ws.cell(BLOCK_TITLE_ROW, block["poscol"]).fill = FILL_HEADER
            ws.cell(HEADER_ROW, block["poscol"]).fill = FILL_HEADER

    ws.row_dimensions[HEADER_ROW].height = ROW_HEIGHT
    ws.row_dimensions[BLOCK_TITLE_ROW].height = ROW_HEIGHT
    for rr in range(1, fill_end_row + 1):
        if rr == PROJECT_TITLE_ROW:
            continue
        ws.row_dimensions[rr].height = ROW_HEIGHT

    bucket_cols = _opos_bucket_col_indices(blocks)
    apply_zero_row_conditional_formatting(
        ws,
        first_row=data_start_row,
        last_row=last_row,
        year_col_indices=bucket_cols,
        style_start_col=POS_COL,
        style_end_col=last_col,
        exclude_cols=spacer_cols | set(_opos_total_col_indices(blocks)),
        gray_font=False,
    )

    value_cols: list[int] = [POS_COL]
    for block in blocks:
        value_cols.extend(range(block["year_startcol"], block["year_endcol"] + 1))
    _autosize_ws_columns(ws, [POS_COL], max_width=LABEL_COL_MAX_WIDTH)

    if footer_rows:
        apply_opos_detail_row_styles(
            ws,
            blocks,
            top_bucket_rows=top_bucket_rows,
            footer_rows=footer_rows,
        )


def write_detail_sheet(
    ws,
    cfg: dict,
    periods: list[AsOfPeriod],
    buckets: list[AgingBucket],
    grid: dict[str, dict[str, dict[str, float]]],
    partner_order: list[str],
    partner_meta: dict[str, str],
) -> dict:
    labels = SIDE_LABELS[cfg["side"]]
    od_keys = overdue_bucket_keys(buckets)
    bucket_cols = list(buckets)
    use_formulas = bool(cfg.get("formula_mode", True))
    detail_lines = build_opos_detail_lines(partner_order, grid, cfg, periods, buckets)

    ws.cell(PROJECT_TITLE_ROW, POS_COL, f"Project {cfg['title']}").font = FONT_PROJECT
    ws.cell(SUBTITLE_ROW, POS_COL, f"{cfg['company']} | {labels['subtitle']}").font = FONT_SUBTITLE

    blocks = build_opos_blocks(periods, buckets)
    for block in blocks:
        _write_block_headers(ws, block, buckets)
    if use_formulas:
        write_opos_bucket_bound_rows(ws, blocks, buckets)

    last_row = DATA_START_ROW - 1
    partner_rows: list[int] = []
    partner_row_pids: dict[int, str] = {}
    partner_row_names: dict[int, str] = {}
    top_bucket_rows: list[dict] = []
    segment_partner_rows: list[int] = []
    row_idx = DATA_START_ROW

    for line in detail_lines:
        if line.kind == "partner":
            pid = str(line.partner_id)
            pname = _display_partner_name(pid, partner_meta.get(pid, pid))
            ws.row_dimensions[row_idx].height = ROW_HEIGHT
            ws.cell(row_idx, POS_COL, pname).font = FONT_BASE
            ws.cell(row_idx, POS_COL).alignment = ALIGN_LEFT

            for block in blocks:
                plabel = block["period"].label
                bmap = grid.get(plabel, {}).get(pid, {})
                amounts = _bucket_amounts(bmap, buckets)
                cc = block["year_startcol"]
                for b in bucket_cols:
                    if not use_formulas:
                        _set_num(ws.cell(row_idx, cc), amounts.get(b.key, 0.0))
                    cc += 1
                if not use_formulas:
                    _set_num(ws.cell(row_idx, cc), _row_total(amounts, buckets))

            partner_rows.append(row_idx)
            partner_row_pids[row_idx] = pid
            partner_row_names[row_idx] = pname
            segment_partner_rows.append(row_idx)
            last_row = row_idx
            row_idx += 1
        elif line.kind == "top_bucket":
            ws.row_dimensions[row_idx].height = ROW_HEIGHT
            ws.cell(row_idx, POS_COL, line.bucket_label).font = FONT_BOLD
            if not use_formulas:
                seg_pids = [
                    partner_row_pids[r]
                    for r in segment_partner_rows
                ]
                for block in blocks:
                    cc = block["year_startcol"]
                    for b in bucket_cols:
                        vals = [
                            _bucket_amounts(
                                grid.get(block["period"].label, {}).get(p, {}), buckets
                            ).get(b.key, 0.0)
                            for p in seg_pids
                        ]
                        _set_num(ws.cell(row_idx, cc), sum(vals), bold=True)
                        cc += 1
                    tot_vals = [
                        _row_total(
                            _bucket_amounts(
                                grid.get(block["period"].label, {}).get(p, {}), buckets
                            ),
                            buckets,
                        )
                        for p in seg_pids
                    ]
                    _set_num(ws.cell(row_idx, cc), sum(tot_vals), bold=True)
            top_bucket_rows.append(
                {"row": row_idx, "partner_rows": list(segment_partner_rows)}
            )
            segment_partner_rows = []
            last_row = row_idx
            row_idx += 1

    footer_labels = ["Total", "Recon. Difference", labels["reported_label"]]
    footer_rows: dict[str, int] = {}
    for flabel in footer_labels:
        last_row += 1
        key = "total" if flabel == "Total" else ("recon" if "Recon" in flabel else "reported")
        footer_rows[key] = last_row
        ws.row_dimensions[last_row].height = ROW_HEIGHT
        is_recon = key == "recon"
        ws.cell(last_row, POS_COL, flabel).font = FONT_BASE if is_recon else FONT_BOLD

    if not use_formulas:
        for block in blocks:
            plabel = block["period"].label
            totals_per_bucket = {b.key: 0.0 for b in buckets}
            for pid in partner_order:
                bmap = grid.get(plabel, {}).get(pid, {})
                for b in buckets:
                    totals_per_bucket[b.key] += float(bmap.get(b.key, 0.0))
            total_row = footer_rows["total"]
            recon_row = footer_rows["recon"]
            reported_row = footer_rows["reported"]
            cc = block["year_startcol"]
            for b in buckets:
                _set_num(ws.cell(total_row, cc), totals_per_bucket[b.key], bold=True)
                cc += 1
            grand = sum(totals_per_bucket.values())
            _set_num(ws.cell(total_row, cc), grand, bold=True)
            _set_num(ws.cell(recon_row, cc), grand, bold=False)
            _set_num(ws.cell(reported_row, cc), 0.0, bold=True)

    excel_layout = get_opos_excel_layout(cfg)
    if use_formulas:
        write_opos_formula_key_column(
            ws, excel_layout, cfg, partner_row_names, footer_rows
        )

    last_col = max(b["spacer_col"] for b in blocks)
    apply_opos_sheet_formatting(
        ws,
        blocks,
        buckets,
        last_row=last_row,
        last_col=last_col,
        top_bucket_rows=top_bucket_rows if not use_formulas else None,
        footer_rows=footer_rows if not use_formulas else None,
    )

    return {
        "blocks": blocks,
        "partner_rows": partner_rows,
        "partner_row_pids": partner_row_pids,
        "top_bucket_rows": top_bucket_rows,
        "footer_rows": footer_rows,
        "last_row": last_row,
        "last_col": last_col,
        "bucket_cols": bucket_cols,
        "od_keys": od_keys,
        "excel_layout": excel_layout,
    }


def write_summary_sheet(
    ws,
    cfg: dict,
    periods: list[AsOfPeriod],
    buckets: list[AgingBucket],
    summary: dict[str, dict[str, float]],
) -> dict:
    labels = SIDE_LABELS[cfg["side"]]
    use_formulas = bool(cfg.get("formula_mode", True))

    ws.cell(PROJECT_TITLE_ROW, POS_COL, f"Project {cfg['title']}").font = FONT_PROJECT
    ws.cell(SUBTITLE_ROW, POS_COL, f"{cfg['company']} | {labels['subtitle']}").font = FONT_SUBTITLE

    value_start = POS_COL + 1
    last_col = POS_COL + len(periods)
    title_cell = ws.cell(BLOCK_TITLE_ROW, POS_COL, labels["summary_sheet"])
    title_cell.font = FONT_BASE
    title_cell.alignment = ALIGN_LEFT
    title_cell.fill = FILL_WHITE

    hpos = ws.cell(HEADER_ROW, POS_COL, UNIT_LABEL)
    hpos.font = FONT_HEADER
    hpos.fill = FILL_HEADER
    hpos.alignment = ALIGN_LEFT
    hpos.border = THEME.border_header_bottom

    for i, period in enumerate(periods):
        c = value_start + i
        hc = ws.cell(HEADER_ROW, c, period.label)
        hc.font = FONT_HEADER
        hc.fill = FILL_HEADER
        hc.alignment = ALIGN_RIGHT
        hc.border = THEME.border_header_bottom

    summary_rows: dict[str, int] = {}
    row = DATA_START_ROW
    for bucket in buckets:
        summary_rows[bucket.key] = row
        ws.row_dimensions[row].height = ROW_HEIGHT
        ws.cell(row, POS_COL, bucket.label).font = FONT_BASE
        ws.cell(row, POS_COL).alignment = ALIGN_LEFT
        if not use_formulas:
            for i, period in enumerate(periods):
                val = summary.get(period.label, {}).get(bucket.key, 0.0)
                _set_num(ws.cell(row, value_start + i), val)
        row += 1

    footer_rows: dict[str, int] = {}
    for flabel, key in (
        (labels["sum_label"], "sum"),
        ("Recon. Difference", "recon"),
        (labels["reported_label"], "reported"),
    ):
        footer_rows[key] = row
        ws.row_dimensions[row].height = ROW_HEIGHT
        is_recon = key == "recon"
        ws.cell(row, POS_COL, flabel).font = FONT_BASE if is_recon else FONT_BOLD
        if not use_formulas:
            for i, period in enumerate(periods):
                _set_num(
                    ws.cell(row, value_start + i),
                    summary.get(period.label, {}).get(key, 0.0),
                    bold=not is_recon,
                )
        row += 1

    fill_end_row = row + 100
    fill_end_col = last_col + FILL_PADDING_COLS
    for rr in range(1, fill_end_row + 1):
        for cc in range(1, POS_COL):
            ws.cell(rr, cc).fill = FILL_TECH
    for rr in range(1, fill_end_row + 1):
        for cc in range(POS_COL, fill_end_col + 1):
            ws.cell(rr, cc).fill = FILL_WHITE
    for rr in range(1, fill_end_row + 1):
        if rr != PROJECT_TITLE_ROW:
            ws.row_dimensions[rr].height = ROW_HEIGHT

    for cc in range(POS_COL, last_col + 1):
        ws.cell(HEADER_ROW, cc).border = THEME.border_header_bottom
        ws.cell(HEADER_ROW, cc).fill = FILL_HEADER

    apply_zero_row_conditional_formatting(
        ws,
        first_row=DATA_START_ROW,
        last_row=row - 1,
        year_col_indices=[value_start + i for i in range(len(periods))],
        style_start_col=POS_COL,
        style_end_col=last_col,
        gray_font=False,
    )

    table_left, table_right = POS_COL, last_col
    for footer_key in ("sum", "reported"):
        frow = footer_rows[footer_key]
        _style_opos_row_band(
            ws,
            frow,
            table_left=table_left,
            table_right=table_right,
            bold=True,
            fill_subtotal=True,
            border_top=True,
        )
    _style_opos_row_band(
        ws,
        footer_rows["reported"],
        table_left=table_left,
        table_right=table_right,
        border_bottom=True,
    )

    _autosize_ws_columns(ws, [POS_COL], max_width=LABEL_COL_MAX_WIDTH)
    ws.column_dimensions[col_letter(POS_COL)].width = SUMMARY_LABEL_COL_WIDTH
    for i in range(len(periods)):
        ws.column_dimensions[col_letter(value_start + i)].width = SUMMARY_VALUE_COL_WIDTH

    return {
        "summary_rows": summary_rows,
        "footer_rows": footer_rows,
        "last_row": row - 1,
        "last_col": last_col,
        "bucket_cols": list(buckets),
    }


def apply_opos_detail_formulas(
    wb,
    cfg: dict,
    periods: list[AsOfPeriod],
    buckets: list[AgingBucket],
    layout: dict,
) -> None:
    labels = SIDE_LABELS[cfg["side"]]
    ws = wb[labels["detail_sheet"]]

    blocks = layout["blocks"]
    bucket_cols = layout["bucket_cols"]
    od_keys = layout["od_keys"]
    partner_rows = layout["partner_rows"]
    partner_row_pids = layout["partner_row_pids"]
    top_bucket_rows = layout.get("top_bucket_rows") or []
    footer_rows = layout["footer_rows"]

    for row_idx in partner_row_pids:
        for block in blocks:
            period = block["period"]
            source_sheet = block["source_sheet"]
            src_header = _get_sheet_header_map(wb[source_sheet])
            cc = block["year_startcol"]
            bucket_ccs: list[int] = []
            for b in bucket_cols:
                formula = _opos_sumifs_formula(
                    source_sheet, src_header, cfg, period, b, cc, row_idx
                )
                _set_cell_formula(ws.cell(row_idx, cc), formula)
                bucket_ccs.append(cc)
                cc += 1
            _set_cell_formula(ws.cell(row_idx, cc), _row_bucket_sum_formula(row_idx, bucket_ccs))

    sum_partner_rows = list(partner_rows)
    for spec in top_bucket_rows:
        br = int(spec["row"])
        seg_rows = list(spec.get("partner_rows") or [])
        for block in blocks:
            cc = block["year_startcol"]
            for _ in bucket_cols:
                _set_cell_formula(
                    ws.cell(br, cc),
                    build_chunked_row_sum_formula(seg_rows, cc),
                    bold=True,
                )
                cc += 1
            _set_cell_formula(
                ws.cell(br, cc),
                build_chunked_row_sum_formula(seg_rows, cc),
                bold=True,
            )

    total_row = footer_rows["total"]
    recon_row = footer_rows["recon"]
    reported_row = footer_rows["reported"]
    for block in blocks:
        cc = block["year_startcol"]
        for _ in bucket_cols:
            _set_cell_formula(
                ws.cell(total_row, cc),
                build_chunked_row_sum_formula(sum_partner_rows, cc),
                bold=True,
            )
            cc += 1
        tot_col = cc
        bucket_ccs_on_total = list(range(block["year_startcol"], tot_col))
        _set_cell_formula(
            ws.cell(total_row, tot_col),
            _row_bucket_sum_formula(total_row, bucket_ccs_on_total),
            bold=True,
        )
        tot_ref = f"{col_letter(tot_col)}{total_row}"
        rep_ref = f"{col_letter(tot_col)}{reported_row}"
        _set_cell_formula(ws.cell(recon_row, tot_col), f"={tot_ref}-{rep_ref}", bold=False)
        ws.cell(reported_row, tot_col).value = 0
        ws.cell(reported_row, tot_col).number_format = NUM_FMT
        ws.cell(reported_row, tot_col).font = FONT_BOLD


def apply_opos_summary_formulas(
    wb,
    cfg: dict,
    periods: list[AsOfPeriod],
    buckets: list[AgingBucket],
    layout: dict,
) -> None:
    labels = SIDE_LABELS[cfg["side"]]
    ws = wb[labels["summary_sheet"]]
    summary_rows = layout["summary_rows"]
    footer_rows = layout["footer_rows"]
    bucket_cols = layout.get("bucket_cols") or list(buckets)
    value_start = POS_COL + 1

    for bucket in bucket_cols:
        row_idx = summary_rows[bucket.key]
        for i, period in enumerate(periods):
            col_idx = value_start + i
            source_sheet = source_sheet_name_for_label(period.label)
            src_header = _get_sheet_header_map(wb[source_sheet])
            body = _opos_sumifs_criteria(
                source_sheet,
                src_header,
                cfg,
                period,
                bucket,
                col_idx=None,
                partner_row=None,
            )
            _set_cell_formula(ws.cell(row_idx, col_idx), f'=IFERROR({body},"")')

    for i, period in enumerate(periods):
        col_idx = value_start + i
        sum_row = footer_rows["sum"]
        recon_row = footer_rows["recon"]
        reported_row = footer_rows["reported"]
        bucket_row_indices = [summary_rows[b.key] for b in bucket_cols]
        _set_cell_formula(
            ws.cell(sum_row, col_idx),
            build_chunked_row_sum_formula(bucket_row_indices, col_idx),
            bold=True,
        )
        tot_ref = f"{col_letter(col_idx)}{sum_row}"
        rep_ref = f"{col_letter(col_idx)}{reported_row}"
        _set_cell_formula(ws.cell(recon_row, col_idx), f"={tot_ref}-{rep_ref}", bold=False)
        ws.cell(reported_row, col_idx).value = 0
        ws.cell(reported_row, col_idx).number_format = NUM_FMT
        ws.cell(reported_row, col_idx).font = FONT_BOLD


def run_opos(cfg: dict) -> str:
    buckets = build_aging_bucket_defs(cfg)
    periods = build_opos_periods(cfg)
    labels = SIDE_LABELS[cfg["side"]]
    use_formulas = bool(cfg.get("formula_mode", True))

    snapshot_dfs = load_snapshot_dataframes(cfg)
    full_partner_meta = build_partner_meta_from_snapshots(cfg)
    grid, partner_order, partner_meta = aggregate_opos(snapshot_dfs, cfg, periods, buckets)
    partner_order = filter_zero_partners(partner_order, grid, periods, buckets)
    partner_meta = {
        pid: full_partner_meta.get(pid, partner_meta.get(pid, pid)) for pid in partner_order
    }
    summary = summary_from_grid(grid, periods, buckets)

    output_path = build_output_file_path(cfg)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    ensure_output_writable(output_path)

    bootstrap_opos_workbook(cfg, output_path, partner_meta=full_partner_meta)
    wb = load_workbook(output_path)

    for sn in (labels["detail_sheet"], labels["summary_sheet"]):
        if sn in wb.sheetnames:
            del wb[sn]

    ws_detail = wb.create_sheet(labels["detail_sheet"])
    ws_summary = wb.create_sheet(labels["summary_sheet"])
    detail_layout = write_detail_sheet(
        ws_detail, cfg, periods, buckets, grid, partner_order, partner_meta
    )
    summary_layout = write_summary_sheet(ws_summary, cfg, periods, buckets, summary)

    if use_formulas:
        apply_opos_detail_formulas(wb, cfg, periods, buckets, detail_layout)
        apply_opos_summary_formulas(wb, cfg, periods, buckets, summary_layout)
        apply_opos_detail_row_styles(
            ws_detail,
            detail_layout["blocks"],
            top_bucket_rows=detail_layout.get("top_bucket_rows"),
            footer_rows=detail_layout["footer_rows"],
        )
        n_formulas = _count_sheet_formulas(ws_detail)
        if partner_order and n_formulas == 0:
            raise RuntimeError("OPOS formula_mode: no formulas written despite partner rows.")

    for name in wb.sheetnames:
        if name.startswith("__SOURCE__"):
            wb[name].sheet_state = "visible"

    wb.calculation.fullCalcOnLoad = True
    wb.save(output_path)
    wb.close()
    return output_path


def main():
    if len(sys.argv) < 2:
        raise ValueError("Usage: python opos.py <config.json>")
    with open(sys.argv[1], encoding="utf-8-sig") as f:
        raw = json.load(f)
    cfg = normalize_config(raw)
    out = run_opos(cfg)
    print(f"OPOS report saved: {out}")
    print(f"Side: {cfg['side']}")
    print(f"Periods: {[p.label for p in build_opos_periods(cfg)]}")


if __name__ == "__main__":
    main()
