"""etl/partner_master_mapping.py — Column-mapped Partner-Master ingestion.

Pure transform (`apply_partner_profile`) + thin loader (`load_partner_master`),
mirroring ``etl.mapping_account`` style.  No DB / no I/O in the pure function so
it is fully unit-testable.

This is the *column-mapped* counterpart to ``etl.load_partner_masters`` (which
hardcodes the BC ``Customer Master.csv`` / ``Vendor Master.csv`` columns).  The
Project-Setup wizard uploads an arbitrary partner-master file and supplies a
``PartnerMappingProfile`` describing which source columns hold the join key
(debtor / creditor number) and the descriptive fields.

Key construction (identical to ``etl.transform.build_partner_id`` and the GL side)
    customer_id / supplier_id = entity_prefix(2) + normalized join_key value

UPSERT targets (see migrations 0001 dim_customer / dim_supplier):
    customer_id, debtor_number, name_line_1, name_line_2, country_code,
    region_code, city, postal_code, default_currency, source_system
    (supplier uses supplier_id / creditor_number).

The loader delegates the actual UPSERT to ``etl.load.load_partners`` so the
master-aware COALESCE semantics (BC names win, placeholders overwritten) stay in
ONE place.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from etl import transform as T
from etl.geo_reference import normalize_country_code

#: Sides this profile can target.
VALID_SIDES = frozenset({"customer", "supplier"})

#: Descriptive (non-key) target columns the profile may map.  ``name_line_1`` is
#: required; the rest are optional and yield NULL when unmapped/absent.
DESCRIPTIVE_FIELDS: tuple[str, ...] = (
    "name_line_1",
    "name_line_2",
    "country_code",
    "region_code",
    "city",
    "postal_code",
    "default_currency",
)

#: Max source-string widths (match dim_customer / dim_supplier column sizes).
_WIDTHS = {
    "name_line_1": 200,
    "name_line_2": 200,
    "region_code": 20,
    "city": 120,
    "postal_code": 20,
    "default_currency": 5,
}


@dataclass
class PartnerMappingProfile:
    """How a raw partner-master file maps to dim_customer / dim_supplier.

    Fields
    ------
    side : str
        'customer' -> dim_customer (debtor_number),
        'supplier' -> dim_supplier (creditor_number).
    entity : dict
        {'mode': 'fixed',  'value': '01'}  — one prefix for the whole file.
        {'mode': 'column', 'value': '<col>'} — per-row entity number column.
    join_key : dict
        {'column': '<col>'} — source column holding the debtor / creditor number.
    columns : dict[str, str | None]
        Maps each descriptive target field (DESCRIPTIVE_FIELDS) to a source column.
        ``name_line_1`` is required; the rest optional.
    source_system : str
        Stored on every upserted row.  Default 'partner_master_upload'.
    """

    side: str = "customer"
    entity: dict = field(default_factory=lambda: {"mode": "fixed", "value": "01"})
    join_key: dict = field(default_factory=dict)
    columns: dict[str, str | None] = field(default_factory=dict)
    source_system: str = "partner_master_upload"


def partner_profile_from_dict(d: dict) -> PartnerMappingProfile:
    """Deserialise a JSON-compatible dict to a PartnerMappingProfile.

    Unknown keys are silently ignored (forward/backward compatible).
    """
    p = PartnerMappingProfile()
    if "side" in d:
        p.side = str(d["side"]).strip().lower()
    if "entity" in d:
        p.entity = d["entity"]
    if "join_key" in d:
        p.join_key = d["join_key"]
    if "columns" in d:
        p.columns = d["columns"]
    if "source_system" in d and d["source_system"]:
        p.source_system = str(d["source_system"])
    return p


def partner_profile_to_dict(p: PartnerMappingProfile) -> dict:
    """Serialise a PartnerMappingProfile to a JSON-compatible dict."""
    return {
        "side": p.side,
        "entity": p.entity,
        "join_key": p.join_key,
        "columns": p.columns,
        "source_system": p.source_system,
    }


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #
def _prefix_series(raw_df: pd.DataFrame, entity: dict) -> pd.Series:
    """Resolve a 2-char entity-prefix Series from the entity config."""
    n = len(raw_df)
    idx = raw_df.index
    mode = (entity or {}).get("mode")
    if mode == "fixed":
        return pd.Series(
            [T.normalize_prefix(entity["value"])] * n, index=idx, dtype="string"
        )
    if mode == "column":
        col = entity.get("value")
        if col not in raw_df.columns:
            raise KeyError(f"entity column {col!r} not found in source file")
        return raw_df[col].map(T.normalize_prefix).astype("string")
    raise ValueError(f"unknown entity mode {mode!r}")


def _opt_str(series: pd.Series | None, n: int, idx, width: int | None) -> pd.Series:
    if series is None:
        return pd.Series([pd.NA] * n, index=idx, dtype="string")
    out = T.normalize_token(series)
    if width:
        out = out.str.slice(0, width)
    return out


# --------------------------------------------------------------------------- #
# apply_partner_profile (pure)
# --------------------------------------------------------------------------- #
def apply_partner_profile(
    raw_df: pd.DataFrame,
    profile: PartnerMappingProfile,
) -> pd.DataFrame:
    """Apply a PartnerMappingProfile to a raw partner-master DataFrame.

    Returns a DataFrame shaped for ``etl.load.load_partners``:

    side='customer' → columns: customer_id, debtor_number, name_line_1,
        name_line_2, country_code, region_code, city, postal_code,
        default_currency, source_system.
    side='supplier' → columns: supplier_id, creditor_number, …(same).

    Rows whose join-key value is empty are dropped; duplicate ids keep the first.

    Raises
    ------
    ValueError
        invalid side / entity mode / non-numeric entity prefix.
    KeyError
        join_key column or a mapped source column is missing.
    """
    side = (profile.side or "").strip().lower()
    if side not in VALID_SIDES:
        raise ValueError(f"side must be one of {sorted(VALID_SIDES)}, got {profile.side!r}")

    join_col = (profile.join_key or {}).get("column")
    if not join_col:
        raise KeyError("join_key.column is required")
    if join_col not in raw_df.columns:
        raise KeyError(f"join_key column {join_col!r} not found in source file")

    name_col = (profile.columns or {}).get("name_line_1")
    if not name_col:
        raise KeyError("columns.name_line_1 is required")

    n = len(raw_df)
    idx = raw_df.index

    prefix_s = _prefix_series(raw_df, profile.entity)
    join_number = T.normalize_token(raw_df[join_col])
    partner_id = (prefix_s + join_number).astype("string")

    cols = profile.columns or {}

    def _src(field_name: str) -> pd.Series | None:
        c = cols.get(field_name)
        if c is None:
            return None
        if c not in raw_df.columns:
            raise KeyError(
                f"source column {c!r} (mapped to {field_name!r}) not found in source file"
            )
        return raw_df[c]

    name_line_1 = _opt_str(_src("name_line_1"), n, idx, _WIDTHS["name_line_1"])
    name_line_2 = _opt_str(_src("name_line_2"), n, idx, _WIDTHS["name_line_2"])
    region_code = _opt_str(_src("region_code"), n, idx, _WIDTHS["region_code"])
    city = _opt_str(_src("city"), n, idx, _WIDTHS["city"])
    postal_code = _opt_str(_src("postal_code"), n, idx, _WIDTHS["postal_code"])

    # country_code: normalise to ISO3 via geo_reference.
    cc_src = _src("country_code")
    if cc_src is None:
        country_code = pd.Series([pd.NA] * n, index=idx, dtype="string")
    else:
        country_code = (
            T.normalize_token(cc_src)
            .map(lambda v: normalize_country_code(v) if pd.notna(v) else None)
            .astype("string")
        )

    cur_src = _src("default_currency")
    if cur_src is None:
        default_currency = pd.Series(["EUR"] * n, index=idx, dtype="string")
    else:
        default_currency = (
            _opt_str(cur_src, n, idx, _WIDTHS["default_currency"]).fillna("EUR")
        )

    id_col = "customer_id" if side == "customer" else "supplier_id"
    number_col = "debtor_number" if side == "customer" else "creditor_number"

    out = pd.DataFrame(
        {
            id_col: partner_id,
            number_col: join_number,
            "name_line_1": name_line_1,
            "name_line_2": name_line_2,
            "country_code": country_code,
            "region_code": region_code,
            "city": city,
            "postal_code": postal_code,
            "default_currency": default_currency,
            "source_system": pd.Series([profile.source_system] * n, index=idx, dtype="string"),
        },
        index=idx,
    )

    # Drop rows with empty join key; keep first per id (idempotent UPSERT key).
    out = out[join_number.fillna("").str.strip() != ""]
    out = out.drop_duplicates(subset=[id_col], keep="first").reset_index(drop=True)
    # Convert pandas <NA> (StringDtype) to Python None so the DBAPI driver can
    # adapt the values (psycopg2 cannot adapt pd.NA).  object dtype + None.
    out = out.astype(object).where(pd.notna(out), None)
    return out


# --------------------------------------------------------------------------- #
# Thin loader
# --------------------------------------------------------------------------- #
def load_partner_master(session, df: pd.DataFrame, side: str) -> dict[str, int]:
    """UPSERT a mapped partner-master frame into dim_customer / dim_supplier.

    Delegates to ``etl.load.load_partners`` (master-aware COALESCE semantics).
    The caller owns commit / rollback.

    Returns {'customers': n, 'suppliers': 0} or {'customers': 0, 'suppliers': n}.
    """
    from etl.load import load_partners

    side = (side or "").strip().lower()
    if side not in VALID_SIDES:
        raise ValueError(f"side must be one of {sorted(VALID_SIDES)}, got {side!r}")

    empty = pd.DataFrame()
    if side == "customer":
        return load_partners(session, df, empty)
    return load_partners(session, empty, df)
