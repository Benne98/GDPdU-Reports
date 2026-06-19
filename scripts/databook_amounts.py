"""Shared amount scaling and Excel formats for databook master scripts."""
from __future__ import annotations

import pandas as pd

# SuSa master stores amounts in kEUR (values divided by 1000 at ingest).
FMT_KEUR = '#,##0;(#,##0);"-"'


def scale_to_keur(
    df: pd.DataFrame,
    amount_columns: list[str],
    *,
    enabled: bool = True,
) -> pd.DataFrame:
    """Divide amount columns by 1000 to match master workbook storage."""
    if not enabled or not amount_columns:
        return df
    out = df.copy()
    for col in amount_columns:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce") / 1000
    return out
