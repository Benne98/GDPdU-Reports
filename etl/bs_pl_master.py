"""Parse BS/PL Master workbooks (Master_BS + Master_PL) for dim_gl_account updates.

Used by the Data Update wizard when the upload contains both standard sheets.
No DB writes here — callers pass a session only for entity-prefix lookup.

Template convention (Excel):
  L1–L4 required  →  level_0 … level_3
  L5, L6, … optional  →  level_4, l4_sub (L7+ ignored until DB supports more)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
from sqlalchemy import text

from etl.entity_resolve import build_entity_lookup
from etl.mapping_account import AccountMappingProfile, apply_account_mapping

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

BS_SHEET = "Master_BS"
PL_SHEET = "Master_PL"

ID_COLS = ["Entity", "Account", "Account description"]
REQUIRED_LEVEL_COLS = ["L1", "L2", "L3", "L4"]
OPTIONAL_LEVEL_DB = {5: "level_4", 6: "l4_sub"}
_LEVEL_COL_RE = re.compile(r"^L(\d+)$", re.IGNORECASE)

PL_L1_COL = "L1 - BS/PL"
ENTITY_PREFIX_COL = "_entity_prefix"

BS_PL_REPLACE_MODES = frozenset({"replace", "append"})


def is_bs_pl_master_workbook(sheets: list[str]) -> bool:
    """True when both Master_BS and Master_PL are present."""
    return BS_SHEET in sheets and PL_SHEET in sheets


def _level_number(col: str) -> int | None:
    m = _LEVEL_COL_RE.match(str(col).strip())
    return int(m.group(1)) if m else None


def detect_level_columns(columns: list[str]) -> list[str]:
    """Return sorted Excel level columns present (L1, L2, …)."""
    found: dict[int, str] = {}
    for col in columns:
        n = _level_number(col)
        if n is not None and n >= 1:
            found[n] = str(col).strip()
    missing = [c for c in REQUIRED_LEVEL_COLS if _level_number(c) not in found]
    if missing:
        raise KeyError(f"BS/PL Master sheet missing required level columns: {missing}")
    return [found[n] for n in sorted(found)]


def bs_pl_master_profile(columns: list[str] | None = None) -> AccountMappingProfile:
    """Column mapping for BS/PL Master; optional L5+ when present in *columns*."""
    level_cols = detect_level_columns(list(columns or REQUIRED_LEVEL_COLS))
    col_map: dict[str, str] = {
        "account_number": "Account",
        "account_name": "Account description",
        "level_0": "L1",
        "level_1": "L2",
        "level_2": "L3",
        "level_3": "L4",
    }
    for excel_col in level_cols:
        n = _level_number(excel_col)
        if n is None or n < 5:
            continue
        db_field = OPTIONAL_LEVEL_DB.get(n)
        if db_field:
            col_map[db_field] = excel_col
    return AccountMappingProfile(
        entity={"mode": "resolved_column", "value": ENTITY_PREFIX_COL},
        fiscal_year={"mode": "fixed", "value": 2024},
        columns=col_map,
        source_system="bs_pl_master",
    )


def read_bs_pl_master(path: Path | str) -> pd.DataFrame:
    """Load Master_BS + Master_PL, keep hierarchy columns only, concat."""
    path = Path(path)
    bs = pd.read_excel(path, sheet_name=BS_SHEET, header=0, dtype=str, na_values=[""])
    pl = pd.read_excel(path, sheet_name=PL_SHEET, header=0, dtype=str, na_values=[""])
    if PL_L1_COL in pl.columns:
        pl = pl.rename(columns={PL_L1_COL: "L1"})
    bs = _trim_keep_cols(bs)
    pl = _trim_keep_cols(pl)
    out = pd.concat([bs, pl], ignore_index=True)
    out = out.dropna(subset=["Entity", "Account"], how="any")
    out = out[out["Account"].astype(str).str.strip() != ""]
    return out.reset_index(drop=True)


def _trim_keep_cols(df: pd.DataFrame) -> pd.DataFrame:
    missing_id = [c for c in ID_COLS if c not in df.columns]
    if missing_id:
        raise KeyError(f"BS/PL Master sheet missing columns: {missing_id}")
    level_cols = detect_level_columns(list(df.columns))
    return df[ID_COLS + level_cols].copy()


def _insert_details(
    mapping_df: pd.DataFrame,
    insert_keys: set[tuple[str, int]],
    *,
    limit: int = 500,
) -> list[dict]:
    """Rows that would be inserted (key absent from dim_gl_account)."""
    if not insert_keys:
        return []
    out: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for _, row in mapping_df.iterrows():
        key = (str(row["account_number_group"]), int(row["fiscal_year"]))
        if key not in insert_keys or key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "account_number_group": key[0],
                "fiscal_year": key[1],
                "gl_account_id": str(row["gl_account_id"]),
                "account_name": ""
                if pd.isna(row.get("account_name"))
                else str(row["account_name"]),
                "level_0": ""
                if pd.isna(row.get("level_0"))
                else str(row["level_0"]),
                "level_1": ""
                if pd.isna(row.get("level_1"))
                else str(row["level_1"]),
                "level_2": ""
                if pd.isna(row.get("level_2"))
                else str(row["level_2"]),
            }
        )
        if len(out) >= limit:
            break
    return sorted(out, key=lambda r: (r["account_number_group"], r["fiscal_year"]))


def _duplicate_row_details(mapping_df: pd.DataFrame) -> list[dict]:
    """One preview row per duplicate occurrence, in file order."""
    dup_mask = mapping_df.duplicated(
        subset=["account_number_group", "fiscal_year"], keep=False
    )
    dup_df = mapping_df.loc[dup_mask]
    if dup_df.empty:
        return []
    out: list[dict] = []
    for _, row in dup_df.iterrows():
        out.append(
            {
                "account_number_group": str(row["account_number_group"]),
                "fiscal_year": int(row["fiscal_year"]),
                "gl_account_id": str(row["gl_account_id"]),
                "account_name": ""
                if pd.isna(row.get("account_name"))
                else str(row["account_name"]),
                "level_0": ""
                if pd.isna(row.get("level_0"))
                else str(row["level_0"]),
                "level_1": ""
                if pd.isna(row.get("level_1"))
                else str(row["level_1"]),
                "level_2": ""
                if pd.isna(row.get("level_2"))
                else str(row["level_2"]),
                "level_3": ""
                if pd.isna(row.get("level_3"))
                else str(row["level_3"]),
            }
        )
    return out


def _duplicate_key_summaries(mapping_df: pd.DataFrame) -> list[dict]:
    """Unique duplicate (account_number_group, fiscal_year) keys for blocker counts."""
    dup_mask = mapping_df.duplicated(
        subset=["account_number_group", "fiscal_year"], keep=False
    )
    dup_df = mapping_df.loc[dup_mask]
    if dup_df.empty:
        return []
    out: list[dict] = []
    for (ang, fy), grp in dup_df.groupby(
        ["account_number_group", "fiscal_year"], sort=True
    ):
        level_0s = sorted(
            {str(v).strip() for v in grp["level_0"].dropna() if str(v).strip()}
        )
        name = grp["account_name"].dropna()
        out.append(
            {
                "account_number_group": str(ang),
                "fiscal_year": int(fy),
                "gl_account_id": str(grp["gl_account_id"].iloc[0]),
                "account_name": str(name.iloc[0]) if len(name) else "",
                "occurrences": int(len(grp)),
                "level_0_values": level_0s,
            }
        )
    return out


def resolve_entity_prefixes(
    raw_df: pd.DataFrame,
    lookup: dict[str, str],
) -> tuple[pd.DataFrame, list[str]]:
    """Add ``_entity_prefix`` column; return unknown entity names."""
    unknown: list[str] = []

    def _resolve(name) -> str | None:
        key = str(name).strip()
        if not key or key.lower() == "nan":
            return None
        prefix = lookup.get(key)
        if prefix is None:
            unknown.append(key)
            return None
        return str(prefix).zfill(2)

    out = raw_df.copy()
    out[ENTITY_PREFIX_COL] = out["Entity"].map(_resolve)
    return out, unknown


def build_mapping_frames(
    raw_df: pd.DataFrame,
    session: Session,
    fiscal_years: list[int],
    profile: AccountMappingProfile | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Resolve entities, apply profile per fiscal year, return canonical frame + meta."""
    if not fiscal_years:
        raise ValueError("fiscal_years must contain at least one year")
    lookup = build_entity_lookup(session)
    resolved, unknown = resolve_entity_prefixes(raw_df, lookup)
    skipped_entities = sorted(set(unknown))
    skipped_rows = int(resolved[ENTITY_PREFIX_COL].isna().sum())
    kept = resolved.dropna(subset=[ENTITY_PREFIX_COL]).reset_index(drop=True)
    if kept.empty:
        raise ValueError(
            "No rows with resolvable entities. "
            f"Unknown in dim_legal_entity: {skipped_entities}"
        )

    prof = profile or bs_pl_master_profile(list(kept.columns))
    parts: list[pd.DataFrame] = []
    for fy in fiscal_years:
        parts.append(apply_account_mapping(kept, prof, fiscal_year=int(fy)))
    mapping_df = pd.concat(parts, ignore_index=True)
    l1 = kept["L1"].astype(str).str.strip()
    meta = {
        "skipped_entities": skipped_entities,
        "skipped_rows": skipped_rows,
        "source_bs_rows": int((l1 == "BS").sum()),
        "source_pl_rows": int((l1 == "PL").sum()),
        "warnings": [],
        "level_columns": detect_level_columns(list(kept.columns)),
    }
    extra_levels = [
        c for c in meta["level_columns"] if (_level_number(c) or 0) > 6
    ]
    if extra_levels:
        meta["warnings"].append(
            "Optional Excel columns ignored (no DB field yet): "
            + ", ".join(extra_levels)
        )
    if skipped_entities:
        meta["warnings"].append(
            f"{skipped_rows} row(s) skipped — entity not in dim_legal_entity: "
            + ", ".join(skipped_entities)
        )
    return mapping_df, meta


def _scope_keys_from_db(
    session: Session,
    fiscal_years: list[int],
    entity_prefixes: list[str],
) -> set[tuple[str, int]]:
    """All dim_gl_account keys for selected fiscal years and entity prefixes."""
    if not fiscal_years or not entity_prefixes:
        return set()
    rows = session.execute(
        text(
            "SELECT account_number_group, fiscal_year FROM dim_gl_account "
            "WHERE fiscal_year = ANY(:fys) AND entity_prefix = ANY(:prefixes)"
        ),
        {"fys": fiscal_years, "prefixes": entity_prefixes},
    ).fetchall()
    return {(str(r[0]), int(r[1])) for r in rows}


def _keys_with_gl_postings(
    session: Session,
    keys: set[tuple[str, int]],
) -> set[tuple[str, int]]:
    """Subset of keys referenced by fact_gl_line."""
    if not keys:
        return set()
    ang_list = list({k[0] for k in keys})
    fy_list = list({k[1] for k in keys})
    rows = session.execute(
        text(
            "SELECT DISTINCT account_number_group, fiscal_year FROM fact_gl_line "
            "WHERE account_number_group = ANY(:angs) AND fiscal_year = ANY(:fys)"
        ),
        {"angs": ang_list, "fys": fy_list},
    ).fetchall()
    return {(str(r[0]), int(r[1])) for r in rows}


def _delete_details(
    session: Session,
    delete_keys: set[tuple[str, int]],
    *,
    limit: int = 500,
) -> list[dict]:
    """Preview rows for accounts that would be removed in replace mode."""
    if not delete_keys:
        return []
    ang_list = list({k[0] for k in delete_keys})
    fy_list = list({k[1] for k in delete_keys})
    rows = session.execute(
        text(
            "SELECT account_number_group, fiscal_year, gl_account_id, account_name, "
            "level_0, level_1, level_2 "
            "FROM dim_gl_account "
            "WHERE account_number_group = ANY(:angs) AND fiscal_year = ANY(:fys) "
            "ORDER BY account_number_group, fiscal_year"
        ),
        {"angs": ang_list, "fys": fy_list},
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        key = (str(r[0]), int(r[1]))
        if key not in delete_keys:
            continue
        out.append(
            {
                "account_number_group": key[0],
                "fiscal_year": key[1],
                "gl_account_id": str(r[2]),
                "account_name": "" if r[3] is None else str(r[3]),
                "level_0": "" if r[4] is None else str(r[4]),
                "level_1": "" if r[5] is None else str(r[5]),
                "level_2": "" if r[6] is None else str(r[6]),
            }
        )
        if len(out) >= limit:
            break
    return out


def filter_mapping_append_only(
    mapping_df: pd.DataFrame,
    session: Session,
    fiscal_years: list[int],
) -> pd.DataFrame:
    """Keep only rows whose key is not already in dim_gl_account."""
    keys_in_file = set(
        zip(
            mapping_df["account_number_group"].astype(str),
            mapping_df["fiscal_year"].astype(int),
        )
    )
    if not keys_in_file:
        return mapping_df.iloc[0:0].copy()
    ang_list = list({k[0] for k in keys_in_file})
    rows = session.execute(
        text(
            "SELECT account_number_group, fiscal_year FROM dim_gl_account "
            "WHERE account_number_group = ANY(:angs) AND fiscal_year = ANY(:fys)"
        ),
        {"angs": ang_list, "fys": list(fiscal_years)},
    ).fetchall()
    existing_keys = {(str(r[0]), int(r[1])) for r in rows}
    mask = mapping_df.apply(
        lambda row: (
            str(row["account_number_group"]),
            int(row["fiscal_year"]),
        )
        not in existing_keys,
        axis=1,
    )
    return mapping_df.loc[mask].reset_index(drop=True)


def preview_stats(
    mapping_df: pd.DataFrame,
    session: Session,
    fiscal_years: list[int],
    source_bs_rows: int,
    source_pl_rows: int,
    *,
    replace_mode: str = "append",
    skipped_entities: list[str] | None = None,
    skipped_rows: int = 0,
    warnings: list[str] | None = None,
) -> dict:
    """Compute read-only preview numbers for the wizard."""
    if replace_mode not in BS_PL_REPLACE_MODES:
        raise ValueError(f"replace_mode must be one of {sorted(BS_PL_REPLACE_MODES)}")

    keys_in_file = set(
        zip(
            mapping_df["account_number_group"].astype(str),
            mapping_df["fiscal_year"].astype(int),
        )
    )
    prefixes = sorted(
        {str(p) for p in mapping_df["account_number_group"].str[:2].unique()}
    )
    entities = (
        session.execute(
            text(
                "SELECT legal_entity_code, entity_name, entity_prefix "
                "FROM dim_legal_entity WHERE entity_prefix = ANY(:prefixes)"
            ),
            {"prefixes": prefixes},
        ).fetchall()
        if prefixes
        else []
    )
    entity_rows = [
        {"legal_entity_code": r[0], "entity_name": r[1], "entity_prefix": r[2]}
        for r in entities
    ]

    existing_keys: set[tuple[str, int]] = set()
    if keys_in_file:
        ang_list = list({k[0] for k in keys_in_file})
        fy_list = list(fiscal_years)
        rows = session.execute(
            text(
                "SELECT account_number_group, fiscal_year FROM dim_gl_account "
                "WHERE account_number_group = ANY(:angs) AND fiscal_year = ANY(:fys)"
            ),
            {"angs": ang_list, "fys": fy_list},
        ).fetchall()
        existing_keys = {(str(r[0]), int(r[1])) for r in rows}

    would_update = len(keys_in_file & existing_keys)
    would_insert = len(keys_in_file - existing_keys)
    would_skip = would_update if replace_mode == "append" else 0
    insert_keys = keys_in_file - existing_keys

    would_delete = 0
    delete_details: list[dict] = []
    blockers: list[str] = []
    if replace_mode == "replace":
        scope_keys = _scope_keys_from_db(session, fiscal_years, prefixes)
        delete_keys = scope_keys - keys_in_file
        would_delete = len(delete_keys)
        if delete_keys:
            delete_details = _delete_details(session, delete_keys)
            gl_blocked = _keys_with_gl_postings(session, delete_keys)
            if gl_blocked:
                blockers.append(
                    f"{len(gl_blocked)} account(s) would be removed but still have GL postings"
                )

    duplicate_keys = _duplicate_key_summaries(mapping_df)
    duplicate_details = _duplicate_row_details(mapping_df)
    insert_details = _insert_details(mapping_df, insert_keys)

    sample = mapping_df.head(5)[
        [
            "account_number_group",
            "fiscal_year",
            "account_name",
            "level_0",
            "level_1",
            "level_2",
            "level_3",
        ]
    ].fillna("").to_dict(orient="records")

    return {
        "row_count_total": len(mapping_df),
        "row_count_bs": source_bs_rows,
        "row_count_pl": source_pl_rows,
        "source_rows_per_fy": source_bs_rows + source_pl_rows,
        "fiscal_years": fiscal_years,
        "entity_prefixes": prefixes,
        "entities": entity_rows,
        "replace_mode": replace_mode,
        "would_update": would_update,
        "would_insert": would_insert,
        "would_skip": would_skip,
        "would_delete": would_delete,
        "duplicate_keys": duplicate_keys,
        "duplicate_details": duplicate_details,
        "insert_details": insert_details,
        "delete_details": delete_details,
        "sample_rows": sample,
        "skipped_entities": skipped_entities or [],
        "skipped_rows": skipped_rows,
        "warnings": warnings or [],
        "blockers": blockers,
    }

