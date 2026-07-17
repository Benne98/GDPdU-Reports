"""Shared helpers for source-file column audits and empty grouping normalization."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl.utils import column_index_from_string, get_column_letter

MISSING_GROUP_LABEL = "n/a"

ISSUE_MISSING_OR_INVALID = "missing_or_invalid"
ISSUE_NON_NUMERIC = "non_numeric"
ISSUE_EMPTY_GROUPED_AS_NA = "empty_grouped_as_na"


def normalize_group_value(val: Any) -> str:
    """Map blank/NaN grouping values to the shared aggregate label."""
    if val is None:
        return MISSING_GROUP_LABEL
    if isinstance(val, float) and pd.isna(val):
        return MISSING_GROUP_LABEL
    s = str(val).strip()
    if not s or s.lower() in {"nan", "none", "<na>", "<na>"}:
        return MISSING_GROUP_LABEL
    return s


def normalize_group_tuple(key: tuple[Any, ...]) -> tuple[str, ...]:
    return tuple(normalize_group_value(v) for v in key)


def is_blank_group_value(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, float) and pd.isna(val):
        return True
    s = str(val).strip()
    return not s or s.lower() in {"nan", "none", "<na>", "<na>"}


def column_letter_for_header(
    df: pd.DataFrame,
    header: str,
    *,
    header_row: int = 0,
) -> str | None:
    """Best-effort Excel column letter for a mapped header name."""
    target = str(header or "").strip().lower()
    if not target:
        return None
    for idx, col in enumerate(df.columns, start=1):
        if str(col).strip().lower() == target:
            return get_column_letter(idx)
    return None


def column_letter_from_map(
    role: str,
    header: str,
    letter_map: dict[str, str] | None,
) -> str | None:
    if letter_map and role in letter_map:
        return str(letter_map[role] or "").strip().upper() or None
    return None


def audit_column_entry(
    *,
    role: str,
    header: str,
    count: int,
    issue: str,
    letter: str | None = None,
) -> dict[str, Any]:
    if count <= 0:
        return {}
    entry: dict[str, Any] = {
        "role": role,
        "header": header,
        "issue": issue,
        "count": int(count),
    }
    if letter:
        entry["letter"] = letter
    return entry


def merge_audit_file_result(
    files: list[dict[str, Any]],
    *,
    label: str,
    file_path: str,
    sheet_name: str,
    columns: list[dict[str, Any]],
    excluded_rows: int = 0,
    notes: list[str] | None = None,
) -> None:
    cols = [c for c in columns if c]
    note_list = [n for n in (notes or []) if n]
    if not cols and excluded_rows <= 0 and not note_list:
        return
    files.append(
        {
            "label": label,
            "file_path": file_path,
            "sheet_name": sheet_name,
            "excluded_rows": int(excluded_rows),
            "columns": cols,
            "notes": note_list,
        }
    )


def finalize_audit_payload(
    files: list[dict[str, Any]],
    *,
    total_excluded: int = 0,
) -> dict[str, Any]:
    total_issues = 0
    for f in files:
        for col in f.get("columns") or []:
            issue = str(col.get("issue") or "")
            count = int(col.get("count") or 0)
            if issue == ISSUE_EMPTY_GROUPED_AS_NA:
                continue
            total_issues += count
    return {
        "total_issues": total_issues,
        "total_excluded": int(total_excluded),
        "files": files,
        # Backward-compatible aliases used by existing mapper UIs
        "total_invalid": total_issues,
        "snapshots": files,
        "periods": files,
    }


def count_missing_series(series: pd.Series) -> int:
    if series is None:
        return 0
    s = series.astype(str).str.strip()
    blank = series.isna() | (s == "") | s.str.lower().isin({"nan", "none", "<na>"})
    return int(blank.sum())


def count_non_numeric_series(series: pd.Series) -> int:
    if series is None:
        return 0
    coerced = pd.to_numeric(series, errors="coerce")
    s = series.astype(str).str.strip()
    has_value = series.notna() & (s != "") & ~s.str.lower().isin({"nan", "none", "<na>"})
    return int((has_value & coerced.isna()).sum())


def group_sumifs_criterion(crit_ref: str, crit_value: str | None) -> str:
    """SUMIFS criterion token: blank source cells when report row groups as n/a."""
    if str(crit_value or "").strip() == MISSING_GROUP_LABEL:
        return '""'
    return crit_ref


def read_excel_period_df(
    file_path: str,
    sheet_name: str,
    *,
    header_row: int = 0,
) -> pd.DataFrame:
    sheet = str(sheet_name or "").strip()
    if not sheet:
        sheet = pd.ExcelFile(file_path, engine="openpyxl").sheet_names[0]
    return pd.read_excel(file_path, sheet_name=sheet, header=header_row, engine="openpyxl")


def resolve_header_letter(
    df: pd.DataFrame,
    header: str,
    *,
    role: str,
    letter_map: dict[str, str] | None = None,
) -> str | None:
    return column_letter_from_map(role, header, letter_map) or column_letter_for_header(df, header)
