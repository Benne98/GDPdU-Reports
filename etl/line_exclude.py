"""Optional row exclusions before GL validation / commit."""
from __future__ import annotations

import pandas as pd

from etl.checks import _is_empty_series

ZERO_AMOUNT_TOL = 0.01

NEGLIGIBLE_S1_REASON = (
    "Empty account in source and zero amount — negligible for import"
)


def apply_line_exclusions(lines: pd.DataFrame, exclude_line_ids: list[int] | set[int]) -> pd.DataFrame:
    """Drop rows whose booking_line_id is in exclude_line_ids."""
    if not exclude_line_ids or "booking_line_id" not in lines.columns:
        return lines
    ids = {int(x) for x in exclude_line_ids}
    if not ids:
        return lines
    return lines.loc[~lines["booking_line_id"].astype(int).isin(ids)].copy()


def negligible_s1_exclusion_mask(lines: pd.DataFrame) -> pd.Series:
    """Rows safe to drop: no account number and amount is zero (or missing)."""
    if lines.empty:
        return pd.Series(dtype=bool)

    if "gl_account_id" in lines.columns:
        empty_account = _is_empty_series(lines["gl_account_id"])
    elif "account_number_group" in lines.columns:
        empty_account = _is_empty_series(lines["account_number_group"])
    else:
        empty_account = pd.Series(False, index=lines.index)

    if "amount" in lines.columns:
        amount = pd.to_numeric(lines["amount"], errors="coerce").fillna(0.0)
        zero_amount = amount.abs() <= ZERO_AMOUNT_TOL
    else:
        zero_amount = pd.Series(True, index=lines.index)

    return empty_account & zero_amount


def suggest_negligible_s1_exclusions(lines: pd.DataFrame) -> list[int]:
    """booking_line_ids that can be excluded to clear typical S1 noise rows."""
    if "booking_line_id" not in lines.columns:
        return []
    mask = negligible_s1_exclusion_mask(lines)
    if not mask.any():
        return []
    return sorted(int(x) for x in lines.loc[mask, "booking_line_id"].astype(int).tolist())


def exclusion_summary(
    lines: pd.DataFrame,
    exclude_line_ids: list[int] | set[int],
) -> dict:
    """Counts for validation UI."""
    active = sorted({int(x) for x in (exclude_line_ids or [])})
    suggested = suggest_negligible_s1_exclusions(lines)
    suggested_set = set(suggested)
    active_set = set(active)
    still_suggested = sorted(suggested_set - active_set)
    return {
        "active_count": len(active),
        "active_line_ids": active,
        "suggested": {
            "count": len(still_suggested),
            "reason": NEGLIGIBLE_S1_REASON,
            "line_ids": still_suggested,
        }
        if still_suggested
        else None,
    }
