"""Client chart-of-accounts 1:1 ingest (reporting-v2 Phase 4).

Alternative to the library auto-suggest (``etl/mapping_suggest.py``): take a
client's OWN chart of accounts (Sachkontenstamm) — account → financial-statement
position — and populate ``dim_gl_account`` level_* directly, 1:1, with no guessing.

This is a thin, behaviour-preserving wrapper around the canonical mapping path the
ingest router already uses:

    raw file/df  →  etl.mapping_account.apply_account_mapping(profile)
                 →  etl.load.load_account_mapping(session)  (UPSERT dim_gl_account)

so it reuses the same column-mapping, key construction
(``entity_prefix(2)+zfill(account,6)``) and idempotent UPSERT as a normal mapping
upload — nothing new in the write path.

EXPECTED INPUT COLUMNS (Sachkontenstamm)
────────────────────────────────────────
The *raw* file uses arbitrary client column names; the mapping from those to the
canonical fields is carried by the ``AccountMappingProfile`` (``profile.columns``),
exactly as in the generic mapping wizard.  The canonical fields the profile must
map are:

  REQUIRED:  account_number, level_0, level_1, level_2, level_3
  OPTIONAL:  level_4, l4_sub, level_2_sort, level_3_sort, account_name,
             gl_account_id, is_ic, and the NA/CF columns
             (see etl.mapping_account.REQUIRED_FIELDS / OPTIONAL_FIELDS)

``entity_prefix`` and ``fiscal_year`` come from the ``scope`` (preferred) or the
profile.  When the client file already carries a ready-made level_0 (BS/PL) split
use ``profile.fixed_level_0`` / a mapped ``level_0`` column.

DIFFERENCE FROM THE LIBRARY PATH
────────────────────────────────
The library path proposes a hierarchy for accounts that have NO client mapping;
this path takes the client's authoritative mapping verbatim.  Both ultimately land
in ``dim_gl_account``; they are mutually-exclusive setup choices per project.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class ClientCoaScope:
    """Entity/year context for a client-CoA load.

    ``entity_prefix`` / ``fiscal_year``, when set, OVERRIDE the profile's entity /
    fiscal_year resolution (passed straight to ``apply_account_mapping``).  Leave
    either None to let the profile resolve it (fixed value or a source column).
    """

    entity_prefix: str | None = None
    fiscal_year: int | None = None


def _coerce_scope(scope) -> ClientCoaScope:
    if scope is None:
        return ClientCoaScope()
    if isinstance(scope, ClientCoaScope):
        return scope
    if isinstance(scope, dict):
        return ClientCoaScope(
            entity_prefix=scope.get("entity_prefix"),
            fiscal_year=(int(scope["fiscal_year"]) if scope.get("fiscal_year") is not None else None),
        )
    if isinstance(scope, tuple) and len(scope) == 2:
        ep, fy = scope
        return ClientCoaScope(entity_prefix=ep, fiscal_year=(int(fy) if fy is not None else None))
    raise TypeError(
        f"unsupported scope {scope!r}; pass None, ClientCoaScope, dict, or (entity_prefix, fiscal_year)"
    )


def _read_file(path) -> pd.DataFrame:
    """Read a client CoA file to an all-string DataFrame (mirrors ingest._load_file)."""
    from pathlib import Path

    p = Path(path)
    ext = p.suffix.lower()
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(p, sheet_name=0, dtype=str, na_values=[""])
    return pd.read_csv(p, dtype=str, na_values=[""])


def load_client_coa(
    session: Session,
    file_or_df,
    scope=None,
    profile=None,
    *,
    auto_commit: bool = False,
) -> dict[str, Any]:
    """Load a client's own chart of accounts 1:1 into ``dim_gl_account``.

    Parameters
    ----------
    session : Session
        Open SQLAlchemy session.  By default the caller owns the transaction
        (``auto_commit=False``) so this can run inside a rebuild / ingest commit.
    file_or_df : str | Path | pandas.DataFrame
        The raw Sachkontenstamm.  A path is read as an all-string DataFrame
        (XLSX/CSV); a DataFrame is used as-is (already-read upload).
    scope : None | ClientCoaScope | dict | (entity_prefix, fiscal_year)
        Entity/year context; overrides the profile's entity/fiscal_year when set.
    profile : AccountMappingProfile | dict | None
        How the client's columns map to the canonical fields.  A dict is
        deserialised via ``account_profile_from_dict``.  Required (there is no
        sensible default column mapping for an arbitrary client file).

    Returns
    -------
    dict with keys: ``accounts``, ``na``, ``cf`` (rows upserted per table — from
    ``load_account_mapping``), plus ``rows`` (input row count).

    Raises
    ------
    ValueError
        If *profile* is None.
    KeyError
        If a required canonical field is unmapped or its source column is absent
        (propagated from ``apply_account_mapping``).
    """
    from etl.mapping_account import (
        AccountMappingProfile,
        account_profile_from_dict,
        apply_account_mapping,
    )
    from etl.load import load_account_mapping

    if profile is None:
        raise ValueError(
            "load_client_coa requires an AccountMappingProfile mapping the client's "
            "columns to the canonical fields (account_number, level_0..level_3, ...)."
        )
    if isinstance(profile, dict):
        profile = account_profile_from_dict(profile)
    elif not isinstance(profile, AccountMappingProfile):
        raise TypeError(f"profile must be AccountMappingProfile or dict, got {type(profile)!r}")

    sc = _coerce_scope(scope)

    raw_df = file_or_df if isinstance(file_or_df, pd.DataFrame) else _read_file(file_or_df)

    mapping_df = apply_account_mapping(
        raw_df,
        profile,
        entity_prefix=sc.entity_prefix,
        fiscal_year=sc.fiscal_year,
    )

    counts = load_account_mapping(session, mapping_df, auto_commit=auto_commit)
    out = {"rows": int(len(mapping_df)), **counts}
    logger.info("load_client_coa: %s", out)
    return out
