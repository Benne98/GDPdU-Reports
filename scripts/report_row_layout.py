"""Dynamic L4 row discovery and amount-based sorting from master trial balances."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

import pandas as pd

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from databook_periods import ordered_reporting_columns_from_df, split_fy_and_ytd  # noqa: E402

BLANK_TOKENS = {"", "nan", "none", "null"}
DEFAULT_REPORTED_VALUE = "reported"
VALID_L4_SORT_BASES = frozenset({"all_fy", "latest_fy", "ytd"})


def is_blank_label(x: Any) -> bool:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return True
    s = str(x).strip()
    return s == "" or s.lower() in BLANK_TOKENS


def normalize_l4_sort_basis(cfg: dict | str | None, default: str = "latest_fy") -> str:
    raw = cfg if isinstance(cfg, str) else (cfg or {}).get("l4_sort_basis")
    basis = str(raw or default).strip().lower()
    if basis not in VALID_L4_SORT_BASES:
        return default
    return basis


def reported_mask(df: pd.DataFrame, source_col: str, reported_value: str = DEFAULT_REPORTED_VALUE) -> pd.Series:
    if source_col not in df.columns:
        return pd.Series(False, index=df.index)
    return (
        df[source_col]
        .astype(str)
        .str.strip()
        .str.lower()
        .eq(str(reported_value).strip().lower())
    )


def period_columns_for_sort(master_df: pd.DataFrame, basis: str) -> list[str]:
    all_periods = ordered_reporting_columns_from_df(master_df)
    fy_cols, ytd_cols = split_fy_and_ytd(all_periods)
    sort_basis = normalize_l4_sort_basis(basis)
    if sort_basis == "all_fy":
        return fy_cols
    if sort_basis == "latest_fy":
        return [fy_cols[-1]] if fy_cols else []
    if sort_basis == "ytd":
        return ytd_cols[:1] if ytd_cols else []
    return fy_cols


def compute_l4_sort_metric(
    master_df: pd.DataFrame,
    l3: str,
    l4: str,
    period_cols: list[str],
    source_col: str,
    *,
    reported_value: str = DEFAULT_REPORTED_VALUE,
) -> float:
    if not period_cols:
        return 0.0
    rep = reported_mask(master_df, source_col, reported_value)
    l3_mask = master_df["L3"].astype(str).str.strip().eq(str(l3).strip())
    l4_series = master_df["L4"]
    if is_blank_label(l4):
        l4_mask = l4_series.isna() | l4_series.astype(str).str.strip().str.lower().isin(BLANK_TOKENS)
    else:
        l4_mask = l4_series.astype(str).str.strip().eq(str(l4).strip())
    mask = rep & l3_mask & l4_mask
    if not mask.any():
        return 0.0
    vals = master_df.loc[mask, period_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    return float(vals.to_numpy().sum())


def sort_l4_labels(labels: list[str], metrics: dict[str, float]) -> list[str]:
    return sorted(
        labels,
        key=lambda lbl: (-abs(float(metrics.get(lbl, 0.0))), str(lbl).lower()),
    )


def discover_l4_under_l3(
    master_df: pd.DataFrame,
    l3: str,
    source_col: str,
    *,
    reported_value: str = DEFAULT_REPORTED_VALUE,
) -> list[str]:
    rep = reported_mask(master_df, source_col, reported_value)
    l3_mask = master_df["L3"].astype(str).str.strip().eq(str(l3).strip())
    subset = master_df.loc[rep & l3_mask, "L4"]
    out: list[str] = []
    seen: set[str] = set()
    for raw in subset.tolist():
        if is_blank_label(raw):
            key = ""
            label = ""
        else:
            label = str(raw).strip()
            key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(label)
    return out


def l3_order_from_mapping(map_df: pd.DataFrame) -> list[str]:
    order: list[str] = []
    for v in map_df["L3"].tolist():
        s = str(v).strip()
        if s and s not in order:
            order.append(s)
    return order


def l2_l3_order_from_mapping(map_df: pd.DataFrame) -> list[tuple[str, str]]:
    if "L2" not in map_df.columns or "L3" not in map_df.columns:
        raise ValueError("BS mapping must contain columns 'L2' and 'L3'.")
    out: list[tuple[str, str]] = []
    l2_order: list[str] = []
    for v in map_df["L2"].tolist():
        s = str(v).strip()
        if s and s not in l2_order:
            l2_order.append(s)
    for l2 in l2_order:
        sub = map_df[map_df["L2"].astype(str).str.strip().eq(l2)]
        l3_order: list[str] = []
        for v in sub["L3"].tolist():
            s = str(v).strip()
            if s and s not in l3_order:
                l3_order.append(s)
        for l3 in l3_order:
            out.append((l2, l3))
    return out


def _sorted_l4_for_l3(
    master_df: pd.DataFrame,
    l3: str,
    source_col: str,
    period_cols: list[str],
    *,
    reported_value: str = DEFAULT_REPORTED_VALUE,
) -> list[str]:
    l4_labels = discover_l4_under_l3(master_df, l3, source_col, reported_value=reported_value)
    if not l4_labels:
        return []
    metrics = {
        l4: compute_l4_sort_metric(
            master_df, l3, l4, period_cols, source_col, reported_value=reported_value
        )
        for l4 in l4_labels
    }
    return sort_l4_labels(l4_labels, metrics)


def _append_bs_l3_block(
    row_structure: list[dict],
    l2: str,
    l3: str,
    sorted_l4s: list[str],
) -> None:
    l3_label = str(l3).strip()
    if not sorted_l4s:
        return

    if len(sorted_l4s) == 1:
        only_l4 = sorted_l4s[0]
        if str(only_l4).strip() == l3_label:
            row_structure.append(
                {
                    "type": "detail_single",
                    "label": l3_label,
                    "L2": l2,
                    "L3": l3_label,
                    "L4": only_l4,
                }
            )
            return
        row_structure.append(
            {
                "type": "detail",
                "label": only_l4,
                "L2": l2,
                "L3": l3_label,
                "L4": only_l4,
            }
        )
        row_structure.append(
            {
                "type": "subtotal_l3",
                "label": l3_label,
                "L2": l2,
                "L3": l3_label,
                "L4": "",
            }
        )
        return

    for l4 in sorted_l4s:
        row_structure.append(
            {
                "type": "detail",
                "label": l4,
                "L2": l2,
                "L3": l3_label,
                "L4": l4,
            }
        )
    row_structure.append(
        {
            "type": "subtotal_l3",
            "label": l3_label,
            "L2": l2,
            "L3": l3_label,
            "L4": "",
        }
    )


def build_bs_row_structure(
    master_df: pd.DataFrame,
    l2_l3_order: list[tuple[str, str]],
    cfg: dict,
    *,
    source_col: str,
    reported_value: str = DEFAULT_REPORTED_VALUE,
) -> list[dict]:
    period_cols = period_columns_for_sort(master_df, normalize_l4_sort_basis(cfg))
    row_structure: list[dict] = []
    current_l2: str | None = None

    for l2, l3 in l2_l3_order:
        if current_l2 is not None and l2 != current_l2:
            row_structure.append(
                {
                    "type": "subtotal_l2",
                    "label": current_l2,
                    "L2": current_l2,
                    "L3": "",
                    "L4": "",
                }
            )
        current_l2 = l2
        sorted_l4s = _sorted_l4_for_l3(
            master_df, l3, source_col, period_cols, reported_value=reported_value
        )
        _append_bs_l3_block(row_structure, l2, l3, sorted_l4s)

    if current_l2 is not None:
        row_structure.append(
            {
                "type": "subtotal_l2",
                "label": current_l2,
                "L2": current_l2,
                "L3": "",
                "L4": "",
            }
        )
    return row_structure


def build_pl_row_structure(
    master_df: pd.DataFrame,
    l3_order: list[str],
    cfg: dict,
    *,
    source_col: str,
    display_label_fn: Callable[[str], str],
    reported_value: str = DEFAULT_REPORTED_VALUE,
) -> list[dict]:
    period_cols = period_columns_for_sort(master_df, normalize_l4_sort_basis(cfg))
    row_structure: list[dict] = []

    for l3 in l3_order:
        l3_label = str(l3).strip()
        sorted_l4s = _sorted_l4_for_l3(
            master_df, l3_label, source_col, period_cols, reported_value=reported_value
        )
        if not sorted_l4s:
            continue

        detail_l4s = [x for x in sorted_l4s if str(x).strip() != l3_label]
        if not detail_l4s:
            row_structure.append(
                {
                    "type": "subtotal",
                    "tech_label": l3_label,
                    "display_label": display_label_fn(l3_label),
                    "L3": l3_label,
                    "L4": l3_label,
                    "from_mapping": True,
                }
            )
            continue

        for l4 in detail_l4s:
            row_structure.append(
                {
                    "type": "detail",
                    "tech_label": l4,
                    "display_label": display_label_fn(l4),
                    "L3": l3_label,
                    "L4": l4,
                }
            )
        row_structure.append(
            {
                "type": "subtotal",
                "tech_label": l3_label,
                "display_label": display_label_fn(l3_label),
                "L3": l3_label,
                "L4": l3_label,
                "from_mapping": True,
            }
        )
    return row_structure


def build_lead_is_row_structure(
    master_df: pd.DataFrame,
    l3_order: list[str],
    cfg: dict,
    *,
    source_col: str,
    label_fn: Callable[[str], str] | None = None,
    reported_value: str = DEFAULT_REPORTED_VALUE,
) -> list[dict]:
    normalize = label_fn or (lambda x: x)
    period_cols = period_columns_for_sort(master_df, normalize_l4_sort_basis(cfg))
    row_structure: list[dict] = []

    for l3 in l3_order:
        l3_label = str(l3).strip()
        sorted_l4s = _sorted_l4_for_l3(
            master_df, l3_label, source_col, period_cols, reported_value=reported_value
        )
        if not sorted_l4s:
            continue

        detail_l4s = [x for x in sorted_l4s if str(x).strip() != l3_label]
        if not detail_l4s:
            row_structure.append(
                {"type": "subtotal", "label": normalize(l3_label), "L3": l3_label, "L4": l3_label}
            )
            continue

        for l4 in detail_l4s:
            row_structure.append(
                {
                    "type": "detail",
                    "label": normalize(l4),
                    "L3": l3_label,
                    "L4": l4,
                }
            )
        row_structure.append(
            {"type": "subtotal", "label": normalize(l3_label), "L3": l3_label, "L4": l3_label}
        )
    return row_structure


def discover_l3_l4_pairs_by_bucket(
    master_df: pd.DataFrame,
    l3_order: list[str],
    source_col: str,
    bucket_col: str,
    *,
    bucket_order: list[str],
    normalize_bucket_fn: Callable[[Any], str],
    reported_value: str = DEFAULT_REPORTED_VALUE,
) -> dict[str, list[tuple[str, str]]]:
    """Group (L3, L4) pairs from master by bucket column, preserving L3 order."""
    rep = reported_mask(master_df, source_col, reported_value)
    work = master_df.loc[rep].copy()
    work["L3"] = work["L3"].astype(str).str.strip()
    work["L4"] = work["L4"].apply(lambda x: "" if is_blank_label(x) else str(x).strip())
    if bucket_col not in work.columns:
        work[bucket_col] = "Other"
    work["_bucket"] = work[bucket_col].apply(normalize_bucket_fn)

    pairs_by_bucket: dict[str, list[tuple[str, str]]] = {b: [] for b in bucket_order}
    seen: dict[str, set[tuple[str, str]]] = {b: set() for b in bucket_order}

    for l3 in l3_order:
        l3_rows = work[work["L3"].eq(l3)]
        if l3_rows.empty:
            continue
        l4_seen: set[str] = set()
        for l4 in l3_rows["L4"].tolist():
            if l4 in l4_seen:
                continue
            l4_seen.add(l4)
            buckets = l3_rows.loc[l3_rows["L4"].eq(l4), "_bucket"].unique().tolist()
            bucket = buckets[0] if buckets else "Other"
            if bucket not in pairs_by_bucket:
                pairs_by_bucket[bucket] = []
                seen[bucket] = set()
            key = (l3, l4)
            if key in seen[bucket]:
                continue
            seen[bucket].add(key)
            pairs_by_bucket[bucket].append(key)

    return pairs_by_bucket


def build_lead_bs_row_structure(
    master_df: pd.DataFrame,
    l3_order: list[str],
    cfg: dict,
    *,
    source_col: str,
    bucket_col: str,
    bucket_order: list[str],
    normalize_bucket_fn: Callable[[Any], str],
    l5_total_labels: dict[str, str],
    reported_value: str = DEFAULT_REPORTED_VALUE,
) -> list[dict]:
    period_cols = period_columns_for_sort(master_df, normalize_l4_sort_basis(cfg))
    pairs_by_bucket = discover_l3_l4_pairs_by_bucket(
        master_df,
        l3_order,
        source_col,
        bucket_col,
        bucket_order=bucket_order,
        normalize_bucket_fn=normalize_bucket_fn,
        reported_value=reported_value,
    )

    row_structure: list[dict] = []
    for bucket in bucket_order:
        pairs = pairs_by_bucket.get(bucket, [])
        if not pairs:
            continue

        l3_to_l4: dict[str, list[str]] = {}
        for l3, l4 in pairs:
            l3_to_l4.setdefault(l3, [])
            if l4 not in l3_to_l4[l3]:
                l3_to_l4[l3].append(l4)

        for l3 in l3_order:
            l4_list = l3_to_l4.get(l3, [])
            if not l4_list:
                continue
            metrics = {
                l4: compute_l4_sort_metric(
                    master_df, l3, l4, period_cols, source_col, reported_value=reported_value
                )
                for l4 in l4_list
            }
            sorted_l4s = sort_l4_labels(l4_list, metrics)
            l3_label = str(l3).strip()

            if len(sorted_l4s) == 1:
                only_l4 = sorted_l4s[0]
                if str(only_l4).strip() == l3_label:
                    row_structure.append(
                        {
                            "type": "detail_single",
                            "label": l3_label,
                            "L5": bucket,
                            "L3": l3_label,
                            "L4": only_l4,
                        }
                    )
                else:
                    row_structure.append(
                        {
                            "type": "detail",
                            "label": only_l4,
                            "L5": bucket,
                            "L3": l3_label,
                            "L4": only_l4,
                        }
                    )
                    row_structure.append(
                        {
                            "type": "subtotal_l3",
                            "label": l3_label,
                            "L5": bucket,
                            "L3": l3_label,
                            "L4": "",
                        }
                    )
            else:
                for l4 in sorted_l4s:
                    row_structure.append(
                        {
                            "type": "detail",
                            "label": l4,
                            "L5": bucket,
                            "L3": l3_label,
                            "L4": l4,
                        }
                    )
                row_structure.append(
                    {
                        "type": "subtotal_l3",
                        "label": l3_label,
                        "L5": bucket,
                        "L3": l3_label,
                        "L4": "",
                    }
                )

        row_structure.append(
            {
                "type": "total_l5",
                "label": l5_total_labels.get(bucket, bucket),
                "L5": bucket,
                "L3": "",
                "L4": "",
                "bucket": bucket,
            }
        )
    return row_structure
