"""Pure transform functions for GL ingestion (P1b).

No DB, no I/O — operates on pandas Series/DataFrames so every step is unit-testable.

Canonical formulas (see docs/db/gl-target-structure.md):
  entity_prefix              = 2-digit, zero-padded (e.g. "01")
  account_number_group       = entity_prefix(2) + zfill(account_number, 6)        -> 8 chars
  journal_entry_group_number = entity_prefix(2) + zfill(journal_entry_number, 10) -> 12 chars
  customer_id / supplier_id  = entity_prefix(2) + normalized source_no
  amount (signed): + = Soll/Debit, - = Haben/Credit
"""
from __future__ import annotations

import re

import pandas as pd

PREFIX_WIDTH = 2
ACCT_WIDTH = 6
JEN_WIDTH = 10

_TRAILING_DOT_ZERO = re.compile(r"\.0$")


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #
def normalize_token(s: pd.Series) -> pd.Series:
    """Strip whitespace and the Excel float artifact '.0' (e.g. 38235.0 -> 38235)."""
    return s.astype("string").str.strip().str.replace(_TRAILING_DOT_ZERO, "", regex=True)


def normalize_prefix(value) -> str:
    """Normalise a single entity number to a fixed 2-digit string prefix.

    Raises if the value is not numeric or exceeds the prefix width.
    """
    p = _TRAILING_DOT_ZERO.sub("", str(value).strip())
    if not p.isdigit():
        raise ValueError(f"entity prefix must be numeric, got {value!r}")
    if len(p) > PREFIX_WIDTH:
        raise ValueError(f"entity prefix {p!r} exceeds {PREFIX_WIDTH} digits")
    return p.zfill(PREFIX_WIDTH)


def parse_decimal(s: pd.Series, decimal: str = ".", thousands: str | None = ",") -> pd.Series:
    """Parse a numeric string Series to float, honouring locale separators."""
    t = s.astype("string").str.strip()
    if thousands:
        t = t.str.replace(thousands, "", regex=False)
    if decimal != ".":
        t = t.str.replace(decimal, ".", regex=False)
    return pd.to_numeric(t, errors="coerce")


def parse_date(s: pd.Series, dayfirst: bool = True) -> pd.Series:
    """Parse a date Series (default DD.MM.YYYY) to pandas datetime (NaT on failure)."""
    return pd.to_datetime(s, dayfirst=dayfirst, errors="coerce")


# --------------------------------------------------------------------------- #
# Key construction
# --------------------------------------------------------------------------- #
def _prefix_series(prefix, index) -> pd.Series:
    """Broadcast a scalar prefix to a Series, or normalise an existing prefix Series."""
    if isinstance(prefix, pd.Series):
        return prefix.map(normalize_prefix).astype("string")
    return pd.Series([normalize_prefix(prefix)] * len(index), index=index, dtype="string")


def build_account_number_group(prefix, account: pd.Series, acct_width: int = ACCT_WIDTH) -> pd.Series:
    """entity_prefix(2) + zfill(account, acct_width). Default width 6 -> 8-char key."""
    acct = normalize_token(account).str.zfill(acct_width)
    return (_prefix_series(prefix, account.index) + acct).astype("string")


def build_journal_entry_group_number(prefix, jen: pd.Series, jen_width: int = JEN_WIDTH) -> pd.Series:
    """entity_prefix(2) + zfill(journal_entry_number, jen_width). Default -> 12-char key."""
    j = normalize_token(jen).str.zfill(jen_width)
    return (_prefix_series(prefix, jen.index) + j).astype("string")


def build_partner_id(prefix, source_no: pd.Series) -> pd.Series:
    """entity_prefix(2) + normalized source_no -> customer_id / supplier_id."""
    return (_prefix_series(prefix, source_no.index) + normalize_token(source_no)).astype("string")


# --------------------------------------------------------------------------- #
# Amount sign logic
# --------------------------------------------------------------------------- #
def signed_amount(
    mode: str,
    *,
    amount: pd.Series | None = None,
    soll: pd.Series | None = None,
    haben: pd.Series | None = None,
    dc_flag: pd.Series | None = None,
    debit_value: str = "S",
    decimal: str = ".",
    thousands: str | None = ",",
) -> pd.Series:
    """Return a signed amount Series (+ = debit, - = credit) for the chosen mode.

    modes:
      'signed'     -> one already-signed amount column
      'soll_haben' -> amount = Soll - Haben (DATEV)
      'amount_dc'  -> amount = +|x| if dc_flag == debit_value else -|x|
    """
    if mode == "signed":
        return parse_decimal(amount, decimal, thousands)
    if mode == "soll_haben":
        s = parse_decimal(soll, decimal, thousands).fillna(0.0)
        h = parse_decimal(haben, decimal, thousands).fillna(0.0)
        return s - h
    if mode == "amount_dc":
        amt = parse_decimal(amount, decimal, thousands).abs()
        is_debit = dc_flag.astype("string").str.strip().str.upper().eq(debit_value.upper())
        return amt * is_debit.map({True: 1, False: -1})
    raise ValueError(f"unknown sign mode {mode!r}")


def derive_partner_ids(
    prefix,
    source_type: pd.Series,
    source_no: pd.Series,
    debtor_label: str = "debitor",
    creditor_label: str = "kreditor",
) -> tuple[pd.Series, pd.Series]:
    """Split source_type/source_no into (customer_id, supplier_id), entity-aware."""
    st = source_type.astype("string").str.strip().str.lower()
    pid = build_partner_id(prefix, source_no)
    customer = pid.where(st.eq(debtor_label))
    supplier = pid.where(st.eq(creditor_label))
    return customer, supplier


def assign_line_numbers(df: pd.DataFrame, group_cols: list[str]) -> pd.Series:
    """1-based line number within each booking group (stable order = input order)."""
    return df.groupby(group_cols, sort=False).cumcount() + 1
