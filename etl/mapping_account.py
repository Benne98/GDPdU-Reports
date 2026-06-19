"""etl/mapping_account.py — Parse an account-mapping file into canonical
dimension DataFrames (DF1, P2-P3-data-foundation).

No DB, no I/O; operates on pandas DataFrames so every step is unit-testable.

The account-mapping file carries one row per GL account per entity/year.  It
describes the financial-statement hierarchy (level_0 … level_4, l4_sub), sort
keys, intercompany flag, and optional NA / CF classification columns.

AccountMappingProfile drives:
  - entity resolution (fixed value or column) — same modes as MappingProfile
  - fiscal_year resolution (fixed or column)
  - column mapping (source column names -> canonical target fields)
  - source_system label

Target fields
-------------
Required (KeyError if absent after column mapping):
  account_number, level_0, level_1, level_2, level_3, level_4, l4_sub

Optional (null when absent):
  level_2_sort, level_3_sort, is_ic, account_name, gl_account_id,
  l6_na_mapping, l7_na_description,
  cf_l1, cf_l2, cf_l3, cf_l4, cf_l5, cf_mapping

Key construction
----------------
  account_number_group = entity_prefix(2) + zfill(account_number, 6)  -> 8 chars
  Delegates to etl.transform.build_account_number_group (identical to GL side).

account_class is NOT determined here — that belongs to DF2.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from etl import transform as T


# --------------------------------------------------------------------------- #
# Canonical target-field manifest
# --------------------------------------------------------------------------- #

#: Required source fields — apply_account_mapping raises KeyError if any are missing
#: after column mapping resolution.
REQUIRED_FIELDS: tuple[str, ...] = (
    "account_number",
    "level_0",
    "level_1",
    "level_2",
    "level_3",
)

#: Optional source fields — produce null column when absent.
OPTIONAL_FIELDS: tuple[str, ...] = (
    "level_4",
    "l4_sub",
    "level_2_sort",
    "level_3_sort",
    "is_ic",
    "account_name",
    "gl_account_id",
    "l6_na_mapping",
    "l7_na_description",
    "cf_l1",
    "cf_l2",
    "cf_l3",
    "cf_l4",
    "cf_l5",
    "cf_mapping",
)

#: NA-dimension columns (drive dim_gl_na upsert in load.py).
NA_FIELDS: tuple[str, ...] = ("l6_na_mapping", "l7_na_description")

#: CF-dimension columns (drive dim_gl_cf upsert in load.py).
CF_FIELDS: tuple[str, ...] = ("cf_l1", "cf_l2", "cf_l3", "cf_l4", "cf_l5", "cf_mapping")


# --------------------------------------------------------------------------- #
# AccountMappingProfile dataclass
# --------------------------------------------------------------------------- #

@dataclass
class AccountMappingProfile:
    """Portable description of how a source mapping file maps to the canonical schema.

    Serialisable to/from JSON via account_profile_to_dict / account_profile_from_dict.

    Fields
    ------
    entity : dict
        {'mode': 'fixed', 'value': '01'}
        {'mode': 'column', 'value': '<col_name>'}

    fiscal_year : dict
        {'mode': 'fixed',  'value': 2024}
        {'mode': 'column', 'value': '<col_name>'}

    columns : dict[str, str | None]
        Maps each canonical target field name to the source column name.
        Keys: any of REQUIRED_FIELDS + OPTIONAL_FIELDS.
        Value None means field is not mapped (required fields must be non-None).

    source_system : str
        Identifies the source ERP / file type.  Default 'unknown'.
    """

    entity: dict = field(default_factory=lambda: {"mode": "fixed", "value": "01"})
    fiscal_year: dict = field(default_factory=lambda: {"mode": "fixed", "value": 2024})
    columns: dict[str, str | None] = field(default_factory=dict)
    source_system: str = "unknown"
    fixed_level_0: str | None = None


def account_profile_from_dict(d: dict) -> AccountMappingProfile:
    """Deserialise a JSON-compatible dict to an AccountMappingProfile.

    Unknown keys are silently ignored so older profiles can be loaded against
    a newer AccountMappingProfile definition.
    """
    p = AccountMappingProfile()
    if "entity" in d:
        p.entity = d["entity"]
    if "fiscal_year" in d:
        p.fiscal_year = d["fiscal_year"]
    if "columns" in d:
        p.columns = d["columns"]
    if "source_system" in d:
        p.source_system = d["source_system"]
    if "fixed_level_0" in d:
        p.fixed_level_0 = d["fixed_level_0"]
    return p


def account_profile_to_dict(p: AccountMappingProfile) -> dict:
    """Serialise an AccountMappingProfile to a JSON-compatible dict."""
    return {
        "entity": p.entity,
        "fiscal_year": p.fiscal_year,
        "columns": p.columns,
        "source_system": p.source_system,
        "fixed_level_0": p.fixed_level_0,
    }


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #

def _resolve_col(raw: pd.DataFrame, target: str, columns: dict, required: bool) -> pd.Series | None:
    """Return the source Series for *target*, or None if unmapped/absent.

    Raises KeyError when *required* is True and the mapping is missing or the
    named source column is not in *raw*.
    """
    src = columns.get(target)
    if src is None:
        if required:
            raise KeyError(
                f"required field {target!r} is not mapped in profile.columns"
            )
        return None
    if src not in raw.columns:
        if required:
            raise KeyError(
                f"required source column {src!r} (mapped to {target!r}) not found in source file"
            )
        return None
    return raw[src]


def _nullable_str(series: pd.Series | None, n: int, index) -> pd.Series:
    """Return a normalised string Series, or a NA-filled Series when *series* is None."""
    if series is None:
        return pd.Series([pd.NA] * n, index=index, dtype="string")
    return T.normalize_token(series)


def _nullable_int(series: pd.Series | None, n: int, index) -> pd.Series:
    """Return a nullable Int64 Series, or a NA-filled Series when *series* is None."""
    if series is None:
        return pd.Series([pd.NA] * n, index=index, dtype="Int64")
    return pd.to_numeric(
        T.normalize_token(series), errors="coerce"
    ).astype("Int64")


# --------------------------------------------------------------------------- #
# apply_account_mapping
# --------------------------------------------------------------------------- #

def apply_account_mapping(
    raw_df: pd.DataFrame,
    profile: AccountMappingProfile,
    entity_prefix: str | None = None,
    fiscal_year: int | None = None,
) -> pd.DataFrame:
    """Apply an AccountMappingProfile to a raw mapping DataFrame.

    Parameters
    ----------
    raw_df : pd.DataFrame
        The raw source data as loaded from a CSV/XLSX upload.  Column names are
        those in the original file; no pre-processing expected.
    profile : AccountMappingProfile
        Fully configured mapping profile.  ``profile.columns`` maps canonical
        field names to source column names.
    entity_prefix : str | None
        Override for the entity prefix.  When provided, takes precedence over
        ``profile.entity`` (convenience for callers who already resolved the
        prefix from context).  Must be 1-2 digit numeric string.
    fiscal_year : int | None
        Override for the fiscal year.  When provided, takes precedence over
        ``profile.fiscal_year``.

    Returns
    -------
    pd.DataFrame
        Canonical mapping DataFrame with columns:

        account_number_group (8-char PK component, VARCHAR(8)),
        fiscal_year (int),
        gl_account_id (normalized account number string — same as account_number
            unless an explicit gl_account_id column is mapped),
        account_name, level_0, level_1, level_2, level_3, level_4, l4_sub,
        level_2_sort, level_3_sort (nullable Int64),
        is_ic (bool, default False),
        l6_na_mapping, l7_na_description,
        cf_l1, cf_l2, cf_l3, cf_l4, cf_l5, cf_mapping,
        source_system.

        Rows retain the same order as raw_df.

    Raises
    ------
    KeyError
        If a required field is not mapped or its source column is absent in raw_df.
    ValueError
        If entity mode or fiscal_year mode is invalid, or if entity_prefix value
        is non-numeric / exceeds 2 digits.
    """
    cols = profile.columns
    n = len(raw_df)
    idx = raw_df.index

    # ------------------------------------------------------------------ entity prefix
    if entity_prefix is not None:
        prefix_s = pd.Series([T.normalize_prefix(entity_prefix)] * n, index=idx, dtype="string")
    else:
        ent = profile.entity
        if ent["mode"] == "fixed":
            prefix_s = pd.Series(
                [T.normalize_prefix(ent["value"])] * n, index=idx, dtype="string"
            )
        elif ent["mode"] == "column":
            src_col = ent["value"]
            if src_col not in raw_df.columns:
                raise KeyError(f"entity column {src_col!r} not found in source file")
            prefix_s = raw_df[src_col].map(T.normalize_prefix).astype("string")
        elif ent["mode"] == "resolved_column":
            src_col = ent["value"]
            if src_col not in raw_df.columns:
                raise KeyError(f"resolved entity column {src_col!r} not found in source file")
            prefix_s = (
                raw_df[src_col]
                .astype("string")
                .str.strip()
                .str.zfill(T.PREFIX_WIDTH)
            )
        else:
            raise ValueError(f"unknown entity mode {ent['mode']!r}")

    # ------------------------------------------------------------------ fiscal_year
    if fiscal_year is not None:
        fiscal_year_s = pd.Series([int(fiscal_year)] * n, index=idx, dtype="Int64")
    else:
        fy_cfg = profile.fiscal_year
        if fy_cfg["mode"] == "fixed":
            fiscal_year_s = pd.Series([int(fy_cfg["value"])] * n, index=idx, dtype="Int64")
        elif fy_cfg["mode"] == "column":
            src_col = fy_cfg["value"]
            if src_col not in raw_df.columns:
                raise KeyError(f"fiscal_year column {src_col!r} not found in source file")
            fiscal_year_s = pd.to_numeric(raw_df[src_col], errors="coerce").astype("Int64")
        else:
            raise ValueError(f"unknown fiscal_year mode {fy_cfg['mode']!r}")

    # ------------------------------------------------------------------ required: account_number
    acct_series = _resolve_col(raw_df, "account_number", cols, required=True)
    # Build account_number_group via the same helper used on the GL side
    account_number_group = T.build_account_number_group(prefix_s, acct_series)
    # gl_account_id: use explicit mapping if available, otherwise normalize account_number
    gl_id_series = _resolve_col(raw_df, "gl_account_id", cols, required=False)
    if gl_id_series is not None:
        gl_account_id = T.normalize_token(gl_id_series)
    else:
        gl_account_id = T.normalize_token(acct_series)

    # ------------------------------------------------------------------ required hierarchy fields
    # level_0: use fixed value from profile if set; otherwise resolve from column
    if profile.fixed_level_0 is not None:
        level_0 = pd.Series([str(profile.fixed_level_0)] * n, index=idx, dtype="string")
    else:
        level_0 = T.normalize_token(_resolve_col(raw_df, "level_0", cols, required=True))
    level_1 = T.normalize_token(_resolve_col(raw_df, "level_1", cols, required=True))
    level_2 = T.normalize_token(_resolve_col(raw_df, "level_2", cols, required=True))
    level_3 = T.normalize_token(_resolve_col(raw_df, "level_3", cols, required=True))
    level_4 = _nullable_str(_resolve_col(raw_df, "level_4", cols, required=False), n, idx)
    l4_sub = _nullable_str(_resolve_col(raw_df, "l4_sub", cols, required=False), n, idx)

    # ------------------------------------------------------------------ optional sort / meta
    level_2_sort = _nullable_int(
        _resolve_col(raw_df, "level_2_sort", cols, required=False), n, idx
    )
    level_3_sort = _nullable_int(
        _resolve_col(raw_df, "level_3_sort", cols, required=False), n, idx
    )
    account_name = _nullable_str(
        _resolve_col(raw_df, "account_name", cols, required=False), n, idx
    )

    # ------------------------------------------------------------------ is_ic (bool, default False)
    is_ic_raw = _resolve_col(raw_df, "is_ic", cols, required=False)
    if is_ic_raw is None:
        is_ic = pd.Series([False] * n, index=idx, dtype=bool)
    else:
        # Coerce: True/1/"true"/"1"/"yes"/"x"/"ja" -> True, everything else -> False
        normalised = T.normalize_token(is_ic_raw).str.lower().str.strip()
        is_ic = normalised.isin({"true", "1", "yes", "x", "ja", "wahr"}).fillna(False)

    # ------------------------------------------------------------------ NA dimension (optional)
    l6_na_mapping = _nullable_str(
        _resolve_col(raw_df, "l6_na_mapping", cols, required=False), n, idx
    )
    l7_na_description = _nullable_str(
        _resolve_col(raw_df, "l7_na_description", cols, required=False), n, idx
    )

    # ------------------------------------------------------------------ CF dimension (optional)
    cf_l1 = _nullable_str(_resolve_col(raw_df, "cf_l1", cols, required=False), n, idx)
    cf_l2 = _nullable_str(_resolve_col(raw_df, "cf_l2", cols, required=False), n, idx)
    cf_l3 = _nullable_str(_resolve_col(raw_df, "cf_l3", cols, required=False), n, idx)
    cf_l4 = _nullable_str(_resolve_col(raw_df, "cf_l4", cols, required=False), n, idx)
    cf_l5 = _nullable_str(_resolve_col(raw_df, "cf_l5", cols, required=False), n, idx)
    cf_mapping = _nullable_str(
        _resolve_col(raw_df, "cf_mapping", cols, required=False), n, idx
    )

    # ------------------------------------------------------------------ assemble canonical frame
    out = pd.DataFrame(
        {
            "account_number_group": account_number_group,
            "fiscal_year": fiscal_year_s,
            "gl_account_id": gl_account_id,
            "account_name": account_name,
            "level_0": level_0,
            "level_1": level_1,
            "level_2": level_2,
            "level_3": level_3,
            "level_4": level_4,
            "l4_sub": l4_sub,
            "level_2_sort": level_2_sort,
            "level_3_sort": level_3_sort,
            "is_ic": is_ic,
            "l6_na_mapping": l6_na_mapping,
            "l7_na_description": l7_na_description,
            "cf_l1": cf_l1,
            "cf_l2": cf_l2,
            "cf_l3": cf_l3,
            "cf_l4": cf_l4,
            "cf_l5": cf_l5,
            "cf_mapping": cf_mapping,
            "source_system": pd.Series([profile.source_system] * n, index=idx, dtype="string"),
        },
        index=idx,
    )

    return out
