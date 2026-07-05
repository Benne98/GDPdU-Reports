"""Optional row exclusions before GL validation / commit."""
from __future__ import annotations

import pandas as pd

from etl.checks import _is_empty_series

ZERO_AMOUNT_TOL = 0.01

NEGLIGIBLE_S1_REASON = (
    "Empty account in source and zero amount — negligible for import"
)


def apply_line_exclusions(
    lines: pd.DataFrame, exclude_line_ids: list[str | int] | set[str | int]
) -> pd.DataFrame:
    """Drop rows whose booking_line_id is in exclude_line_ids.

    booking_line_id is a 62-bit BLAKE2b hash (> 2**53). Matching is done by STRING
    comparison on the exact digit sequence so an id is NEVER routed through
    float64 / pd.to_numeric (which would round a huge id to the wrong value and
    exclude the wrong row). Items may be str or int — both are normalised to str.
    """
    if not exclude_line_ids or "booking_line_id" not in lines.columns:
        return lines
    ids = {str(x) for x in exclude_line_ids}
    if not ids:
        return lines
    return lines.loc[~lines["booking_line_id"].astype("int64").astype(str).isin(ids)].copy()


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


def suggest_negligible_s1_exclusions(lines: pd.DataFrame) -> list[str]:
    """booking_line_ids that can be excluded to clear typical S1 noise rows.

    Returned as exact digit STRINGS. booking_line_id is a 62-bit BLAKE2b hash
    (> 2**53); a JSON number would lose precision on the JavaScript float64
    boundary, so the UI must receive — and later echo back in exclude_line_ids —
    the exact id string (never a Number). Sorted numerically for determinism.
    """
    if "booking_line_id" not in lines.columns:
        return []
    mask = negligible_s1_exclusion_mask(lines)
    if not mask.any():
        return []
    ids = lines.loc[mask, "booking_line_id"].astype("int64").astype(str).tolist()
    return sorted(ids, key=int)


def exclusion_summary(
    lines: pd.DataFrame,
    exclude_line_ids: list[str | int] | set[str | int],
) -> dict:
    """Counts for validation UI.

    ``active_line_ids`` and ``suggested.line_ids`` are exact digit STRINGS so a
    62-bit booking_line_id (> 2**53) survives the JSON -> JavaScript float64
    boundary intact (see ``suggest_negligible_s1_exclusions``). Inputs may be str
    or int — both are normalised to str. Sorted numerically for determinism.
    """
    active = sorted({str(x) for x in (exclude_line_ids or [])}, key=int)
    suggested = suggest_negligible_s1_exclusions(lines)
    suggested_set = set(suggested)
    active_set = set(active)
    still_suggested = sorted(suggested_set - active_set, key=int)
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
