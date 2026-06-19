"""Fetch all source rows for a validation issue (S1 field failures)."""
from __future__ import annotations

import pandas as pd

from etl.checks import s1_empty_mask


def _serialize_cell(value) -> str | int | float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        try:
            return str(value.date()) if hasattr(value, "date") else str(value)
        except Exception:
            return str(value)
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value).strip()


def source_row_at_line(raw_df: pd.DataFrame, booking_line_id: int) -> dict[str, str | int | float | None] | None:
    """Map 1-based booking_line_id to the aligned source row."""
    try:
        pos = int(booking_line_id) - 1
    except (TypeError, ValueError):
        return None
    if pos < 0 or pos >= len(raw_df):
        return None
    row = raw_df.iloc[pos]
    return {str(k): _serialize_cell(row[k]) for k in raw_df.columns}


def collect_s1_issue_rows(
    linked: pd.DataFrame,
    raw_df: pd.DataFrame,
    field: str,
    exclude_line_ids: list[int] | set[int] | None = None,
    search: str | None = None,
    limit: int = 10_000,
    offset: int = 0,
) -> dict:
    """Return flat source rows + stats for one S1 failing field."""
    mask = s1_empty_mask(linked, field)
    if exclude_line_ids and "booking_line_id" in linked.columns:
        excluded = {int(x) for x in exclude_line_ids}
        mask &= ~linked["booking_line_id"].astype(int).isin(excluded)

    failing = linked.loc[mask]
    total = int(len(failing))

    amount_sum: float | None = None
    if "amount" in failing.columns and not failing.empty:
        amount_sum = round(
            float(pd.to_numeric(failing["amount"], errors="coerce").fillna(0.0).sum()),
            2,
        )

    fiscal_years: list[int] = []
    if "fiscal_year" in failing.columns and not failing.empty:
        fiscal_years = sorted(
            int(x)
            for x in pd.to_numeric(failing["fiscal_year"], errors="coerce").dropna().unique()
        )

    rows: list[dict] = []
    for idx in failing.index:
        bid = None
        if "booking_line_id" in linked.columns:
            val = linked.at[idx, "booking_line_id"]
            if pd.notna(val):
                bid = int(val)
        row: dict = {"booking_line_id": bid}
        if bid is not None:
            source = source_row_at_line(raw_df, bid)
            if source:
                row.update(source)
        rows.append(row)

    if search:
        q = search.strip().lower()
        if q:
            rows = [
                r
                for r in rows
                if any(q in str(v).lower() for v in r.values() if v is not None and v != "")
            ]

    columns: list[str] = []
    if rows:
        all_keys: set[str] = set()
        for r in rows:
            all_keys.update(r.keys())
        columns = ["booking_line_id"] + sorted(k for k in all_keys if k != "booking_line_id")

    filtered_total = len(rows)
    page = rows[offset : offset + limit]

    return {
        "stats": {
            "row_count": total,
            "amount_sum": amount_sum,
            "fiscal_years": fiscal_years,
        },
        "columns": columns,
        "rows": page,
        "total": filtered_total,
    }
