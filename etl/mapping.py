"""etl/mapping.py — Turn a raw uploaded DataFrame + a MappingProfile into a
canonical GL lines DataFrame (P1b/P1f).

No DB, no I/O; operates on pandas DataFrames so every step is unit-testable.

MappingProfile drives:
  - entity resolution (fixed value or column)
  - fiscal_year resolution (fixed, column, or derived from posting_date)
  - sign logic  (signed / soll_haben / amount_dc) via transform.signed_amount
  - decimal / thousands / date parsing locale
  - column mapping (source column names -> canonical target fields)
  - linking strategy ('txn' | 'gegenkonto' | 'none')
  - entry_type default ('actual')

Output canonical columns (all present, nullable where schema allows):
  journal_entry_group_number, fiscal_year, fiscal_period, line_number,
  booking_line_id, account_number_group, gl_account_id, amount, vat_amount,
  line_note, customer_id, supplier_id, posting_type, posting_date,
  document_date, document_type_code, reference_document_number, currency_code,
  header_note, entry_type, account_class, source_system
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from etl import transform as T


# --------------------------------------------------------------------------- #
# MappingProfile dataclass
# --------------------------------------------------------------------------- #
@dataclass
class MappingProfile:
    """Portable description of how a source file maps to the canonical schema.

    Serialisable to/from JSON via profile_to_dict / profile_from_dict.

    Fields
    ------
    entity : dict
        {'mode': 'fixed', 'value': '01'}  — fixed entity_prefix for the whole file.
        {'mode': 'column', 'value': '<col_name>'} — read entity_prefix per row from
        a source column (must be 1-2 digit numeric after normalization).

    fiscal_year : dict
        {'mode': 'fixed',    'value': 2024}
        {'mode': 'column',   'value': '<col_name>'}
        {'mode': 'from_date', 'value': None}  — derive from posting_date.year

    sign : dict
        Describes how to compute the signed `amount` (+ = debit, - = credit).
        mode 'signed':     {'mode': 'signed',     'amount': '<col>'}
        mode 'soll_haben': {'mode': 'soll_haben',  'soll': '<col>', 'haben': '<col>'}
        mode 'amount_dc':  {'mode': 'amount_dc',   'amount': '<col>', 'dc_flag': '<col>',
                            'debit_value': 'S'}  # debit_value default 'S'

    decimal : str
        Decimal separator in source numbers, e.g. '.' or ','.  Default '.'.
    thousands : str | None
        Thousands separator, e.g. ',' or '.'.  Default ','.  None = no stripping.
    date_dayfirst : bool
        Passed to pandas parse_date; True for DD.MM.YYYY (DATEV).  Default True.

    columns : dict[str, str | None]
        Maps each canonical *optional* target field name to the source column name.
        Keys: journal_entry_number, account_number, vat_amount, line_note,
              source_type, source_no, posting_type, posting_date, document_date,
              document_type, reference_document_number, header_note, currency.
        Value None means field is not mapped (column will be null in output).

    linking_strategy : str
        'txn' | 'gegenkonto' | 'none'.  Default 'txn'.

    entry_type : str
        Default entry_type for all rows.  Default 'actual'.

    source_system : str
        Identifies the source ERP / file type (stored on every row).  Default 'unknown'.

    entity_assignments : dict[str, str] | None
        Optional mapping of source entity label → 2-char prefix, confirmed in the wizard
        when labels are not yet in dim_legal_entity.
    """

    entity: dict = field(default_factory=lambda: {"mode": "fixed", "value": "01"})
    fiscal_year: dict = field(default_factory=lambda: {"mode": "from_date", "value": None})
    sign: dict = field(default_factory=lambda: {"mode": "signed", "amount": None})
    decimal: str = "."
    thousands: str | None = ","
    date_dayfirst: bool = True
    columns: dict[str, str | None] = field(default_factory=dict)
    linking_strategy: str = "txn"
    entry_type: str = "actual"
    source_system: str = "unknown"
    entity_assignments: dict[str, str] | None = None


def profile_from_dict(d: dict) -> MappingProfile:
    """Deserialise a JSON-compatible dict to a MappingProfile.

    Unknown keys are silently ignored so older serialised profiles can be loaded
    against a newer MappingProfile definition.
    """
    p = MappingProfile()
    if "entity" in d:
        p.entity = d["entity"]
    if "fiscal_year" in d:
        p.fiscal_year = d["fiscal_year"]
    if "sign" in d:
        p.sign = d["sign"]
    if "decimal" in d:
        p.decimal = d["decimal"]
    if "thousands" in d:
        p.thousands = d["thousands"]
    if "date_dayfirst" in d:
        p.date_dayfirst = bool(d["date_dayfirst"])
    if "columns" in d:
        p.columns = d["columns"]
    if "linking_strategy" in d:
        p.linking_strategy = d["linking_strategy"]
    if "entry_type" in d:
        p.entry_type = d["entry_type"]
    if "source_system" in d:
        p.source_system = d["source_system"]
    if "entity_assignments" in d:
        p.entity_assignments = d["entity_assignments"]
    return p


def profile_to_dict(p: MappingProfile) -> dict:
    """Serialise a MappingProfile to a JSON-compatible dict."""
    return {
        "entity": p.entity,
        "fiscal_year": p.fiscal_year,
        "sign": p.sign,
        "decimal": p.decimal,
        "thousands": p.thousands,
        "date_dayfirst": p.date_dayfirst,
        "columns": p.columns,
        "linking_strategy": p.linking_strategy,
        "entry_type": p.entry_type,
        "source_system": p.source_system,
        "entity_assignments": p.entity_assignments,
    }


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #
def _col(raw: pd.DataFrame, col_name: str | None) -> pd.Series | None:
    """Return a Series for col_name if it is non-None and present in raw, else None."""
    if col_name is None or col_name not in raw.columns:
        return None
    return raw[col_name]


def _nullable(raw: pd.DataFrame, col_name: str | None) -> pd.Series:
    """Return a string Series for col_name, or a null-filled Series if absent."""
    s = _col(raw, col_name)
    if s is None:
        return pd.Series([pd.NA] * len(raw), index=raw.index, dtype="string")
    return T.normalize_token(s)


# --------------------------------------------------------------------------- #
# apply_profile
# --------------------------------------------------------------------------- #
def apply_profile(
    raw_df: pd.DataFrame,
    profile: MappingProfile,
    account_class_lookup: dict[str, str] | None = None,
    entity_lookup: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Apply a MappingProfile to a raw uploaded DataFrame.

    Parameters
    ----------
    raw_df : pd.DataFrame
        The raw source data as loaded from a CSV/XLSX upload.  Column names are
        those in the original file; no pre-processing expected.
    profile : MappingProfile
        Fully configured mapping profile.
    account_class_lookup : dict[str, str] | None
        Optional mapping {gl_account_id -> account_class}.  If provided, the
        canonical `account_class` column is populated by looking up the derived
        `gl_account_id`.  Falls back to None/'other' for unknown accounts.

    Returns
    -------
    pd.DataFrame
        Canonical lines DataFrame with all columns listed in the module docstring.
        Rows retain the same order as raw_df.  Dtypes are best-effort (strings as
        StringDtype, numerics as float64, dates as datetime64).

    Raises
    ------
    KeyError
        If a mandatory source column named in the profile is not found in raw_df.
        Optional columns (columns dict values) silently yield nulls if absent.
    ValueError
        If entity mode / fiscal_year mode / sign mode is invalid.
    """
    cols = profile.columns
    n = len(raw_df)
    idx = raw_df.index

    # ------------------------------------------------------------------ entity prefix
    from etl.entity_resolve import prefix_series_from_entity_config

    lookup = entity_lookup if entity_lookup is not None else {}
    prefix_s = prefix_series_from_entity_config(
        raw_df,
        profile.entity,
        lookup,
        profile.entity_assignments,
    )

    # ------------------------------------------------------------------ posting_date (required)
    pd_col = cols.get("posting_date")
    if pd_col is None or pd_col not in raw_df.columns:
        raise KeyError("posting_date is required; map it in profile.columns['posting_date']")
    posting_date = T.parse_date(raw_df[pd_col], dayfirst=profile.date_dayfirst)

    # ------------------------------------------------------------------ fiscal_year
    fy_cfg = profile.fiscal_year
    if fy_cfg["mode"] == "fixed":
        fiscal_year_s = pd.Series([int(fy_cfg["value"])] * n, index=idx)
    elif fy_cfg["mode"] == "column":
        src_col = fy_cfg["value"]
        if src_col not in raw_df.columns:
            raise KeyError(f"fiscal_year column {src_col!r} not found in source file")
        fiscal_year_s = pd.to_numeric(raw_df[src_col], errors="coerce").astype("Int64")
    elif fy_cfg["mode"] == "from_date":
        fiscal_year_s = posting_date.dt.year.astype("Int64")
    else:
        raise ValueError(f"unknown fiscal_year mode {fy_cfg['mode']!r}")

    # ------------------------------------------------------------------ journal_entry_number (required)
    jen_col = cols.get("journal_entry_number")
    if jen_col is None or jen_col not in raw_df.columns:
        raise KeyError("journal_entry_number is required; map it in profile.columns['journal_entry_number']")
    jegn = T.build_journal_entry_group_number(prefix_s, raw_df[jen_col])

    # ------------------------------------------------------------------ account_number (required)
    acct_col = cols.get("account_number")
    if acct_col is None or acct_col not in raw_df.columns:
        raise KeyError("account_number is required; map it in profile.columns['account_number']")
    ang = T.build_account_number_group(prefix_s, raw_df[acct_col])
    gl_account_id = T.normalize_token(raw_df[acct_col])

    # ------------------------------------------------------------------ amount (signed)
    sign_cfg = profile.sign
    s_mode = sign_cfg.get("mode", "signed")
    if s_mode == "signed":
        amount_s = T.signed_amount(
            "signed",
            amount=raw_df[sign_cfg["amount"]],
            decimal=profile.decimal,
            thousands=profile.thousands,
        )
    elif s_mode == "soll_haben":
        amount_s = T.signed_amount(
            "soll_haben",
            soll=raw_df[sign_cfg["soll"]],
            haben=raw_df[sign_cfg["haben"]],
            decimal=profile.decimal,
            thousands=profile.thousands,
        )
    elif s_mode == "amount_dc":
        amount_s = T.signed_amount(
            "amount_dc",
            amount=raw_df[sign_cfg["amount"]],
            dc_flag=raw_df[sign_cfg["dc_flag"]],
            debit_value=sign_cfg.get("debit_value", "S"),
            decimal=profile.decimal,
            thousands=profile.thousands,
        )
    else:
        raise ValueError(f"unknown sign mode {s_mode!r}")

    # ------------------------------------------------------------------ fiscal_period (from posting_date month)
    fiscal_period_s = posting_date.dt.month.astype("Int64")

    # ------------------------------------------------------------------ optional columns
    vat_amount_col = cols.get("vat_amount")
    vat_amount_s: pd.Series
    if vat_amount_col and vat_amount_col in raw_df.columns:
        vat_amount_s = T.parse_decimal(raw_df[vat_amount_col], profile.decimal, profile.thousands)
    else:
        vat_amount_s = pd.Series([pd.NA] * n, index=idx, dtype="Float64")

    document_date_col = cols.get("document_date")
    if document_date_col and document_date_col in raw_df.columns:
        document_date_s = T.parse_date(raw_df[document_date_col], dayfirst=profile.date_dayfirst)
    else:
        document_date_s = pd.Series([pd.NaT] * n, index=idx, dtype="datetime64[ns]")

    # ------------------------------------------------------------------ partner columns
    source_type_col = cols.get("source_type")
    source_no_col = cols.get("source_no")
    if source_type_col and source_no_col and \
            source_type_col in raw_df.columns and source_no_col in raw_df.columns:
        customer_id_s, supplier_id_s = T.derive_partner_ids(
            prefix_s,
            raw_df[source_type_col],
            raw_df[source_no_col],
        )
    else:
        customer_id_s = pd.Series([pd.NA] * n, index=idx, dtype="string")
        supplier_id_s = pd.Series([pd.NA] * n, index=idx, dtype="string")

    # ------------------------------------------------------------------ line_number within booking
    # Temporary frame for groupby; line_numbers assigned per (jegn) group in input order.
    _tmp = pd.DataFrame({"jegn": jegn}, index=idx)
    line_number_s = T.assign_line_numbers(_tmp, ["jegn"])

    # ------------------------------------------------------------------ booking_line_id (1-based global)
    booking_line_id_s = pd.Series(range(1, n + 1), index=idx, dtype="int64")

    # ------------------------------------------------------------------ account_class
    if account_class_lookup:
        account_class_s = gl_account_id.map(lambda v: account_class_lookup.get(str(v), "other")).astype("string")
    else:
        account_class_s = pd.Series(["other"] * n, index=idx, dtype="string")

    # ------------------------------------------------------------------ assemble canonical frame
    out = pd.DataFrame(
        {
            "journal_entry_group_number": jegn,
            "fiscal_year": fiscal_year_s,
            "fiscal_period": fiscal_period_s,
            "line_number": line_number_s,
            "booking_line_id": booking_line_id_s,
            "account_number_group": ang,
            "gl_account_id": gl_account_id,
            "amount": amount_s,
            "vat_amount": vat_amount_s,
            "line_note": _nullable(raw_df, cols.get("line_note")),
            "customer_id": customer_id_s,
            "supplier_id": supplier_id_s,
            "posting_type": _nullable(raw_df, cols.get("posting_type")),
            "posting_date": posting_date,
            "document_date": document_date_s,
            "document_type_code": _nullable(raw_df, cols.get("document_type")),
            "reference_document_number": _nullable(raw_df, cols.get("reference_document_number")),
            "currency_code": _nullable(raw_df, cols.get("currency")).fillna("EUR"),
            "header_note": _nullable(raw_df, cols.get("header_note")),
            "entry_type": pd.Series([profile.entry_type] * n, index=idx, dtype="string"),
            "account_class": account_class_s,
            "source_system": pd.Series([profile.source_system] * n, index=idx, dtype="string"),
        },
        index=idx,
    )

    return out
