"""GoBD GL CSV preprocessing — retain Jan-1 opening balances dropped by txn filter.

Decidra exports carry annual Anfangsbestände as 01.01 rows with Entity No + Year but
no Transaction number. The reference loader used to drop them; this module keeps them,
assigns synthetic transaction numbers, and tags rows for opening_balance ingest.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

_OPENING_LINE_NOTE = "Start value"


def _txn_series(df: pd.DataFrame) -> pd.Series:
    if "Transaction number" not in df.columns:
        return pd.Series([""] * len(df), index=df.index, dtype="string")
    return df["Transaction number"].fillna("").astype(str).str.strip()


def _posting_dates(df: pd.DataFrame) -> pd.Series:
    if "Posting date" not in df.columns:
        return pd.Series([pd.NaT] * len(df), index=df.index)
    return pd.to_datetime(df["Posting date"], dayfirst=True, errors="coerce")


def is_opening_balance_mask(df: pd.DataFrame) -> pd.Series:
    """Rows that are Jan-1 opening balances (no txn, real account, not summary account 1)."""
    txn = _txn_series(df)
    pdates = _posting_dates(df)
    has_entity = df["Entity No"].notna() if "Entity No" in df.columns else pd.Series(False, index=df.index)
    has_year = df["Year"].notna() if "Year" in df.columns else pd.Series(False, index=df.index)
    acct = df["Account number"].astype(str).str.strip() if "Account number" in df.columns else pd.Series("", index=df.index)
    jan1 = pdates.dt.month.eq(1) & pdates.dt.day.eq(1)
    return has_entity & has_year & txn.eq("") & jan1 & acct.ne("1") & acct.ne("")


def _synthetic_opening_txn(entity_no: str, year: str, account: str) -> str:
    """10-digit journal entry number (entity prefix applied separately in mapping)."""
    ent = re.sub(r"\D", "", str(entity_no)) or "0"
    ent2 = ent.zfill(2)[-2:]
    yy = str(year).zfill(4)[-2:]
    acct5 = re.sub(r"\D", "", str(account)).zfill(5)[-5:]
    return f"9{yy}{ent2}{acct5}"


def prepare_gobd_gl_frame(gl_df: pd.DataFrame) -> pd.DataFrame:
    """Filter loadable rows and synthesize keys + labels for opening balances."""
    df = gl_df.copy()
    txn = _txn_series(df)
    has_keys = (
        df["Entity No"].notna()
        & df["Year"].notna()
        & txn.ne("")
    )
    if "Account number" not in df.columns:
        raise KeyError("Account number column required in GoBD GL CSV")

    ob_mask = is_opening_balance_mask(df)
    valid = has_keys | ob_mask
    n_drop = int((~valid).sum())
    if n_drop:
        drop_amt = pd.to_numeric(df.loc[~valid, "Amount"], errors="coerce").sum()
        print(
            f"    [GL filter] dropped {n_drop}/{len(df)} rows lacking "
            f"Entity No/Year/Transaction number (sum Amount={drop_amt:,.2f})"
        )

    out = df.loc[valid].reset_index(drop=True)
    ob_mask = is_opening_balance_mask(out)
    out["_is_opening_balance"] = ob_mask

    if ob_mask.any():
        syn = out.loc[ob_mask].apply(
            lambda r: _synthetic_opening_txn(r["Entity No"], r["Year"], r["Account number"]),
            axis=1,
        )
        out.loc[ob_mask, "Transaction number"] = syn.values
        bt = out["Booking text"].fillna("").astype(str).str.strip() if "Booking text" in out.columns else pd.Series("", index=out.index)
        fill_note = ob_mask & bt.eq("")
        if "Booking text" not in out.columns:
            out["Booking text"] = ""
        out.loc[fill_note, "Booking text"] = _OPENING_LINE_NOTE
        print(f"    [GL opening] retained {int(ob_mask.sum())} Jan-1 opening balance row(s)")

    return out


def prepare_gobd_gl_csv(gl_path: str | Path) -> pd.DataFrame:
    """Read GoBD CSV and apply ``prepare_gobd_gl_frame``."""
    gl_df = pd.read_csv(gl_path, encoding="utf-8-sig", sep=",", dtype=str, na_values=[""])
    return prepare_gobd_gl_frame(gl_df)


def tag_opening_balances_in_canonical(
    canonical: pd.DataFrame,
    is_opening: pd.Series,
) -> pd.DataFrame:
    """Set fiscal_period=0 and entry_type=opening_balance on opening rows."""
    out = canonical.copy()
    ob = is_opening.fillna(False).values
    if ob.any():
        out.loc[ob, "fiscal_period"] = 0
        out.loc[ob, "entry_type"] = "opening_balance"
    return out
