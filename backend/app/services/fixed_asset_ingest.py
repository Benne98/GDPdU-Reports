"""Column mapping for anlagengitter.xlsx -> fact_fixed_asset."""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from app.services.draft_ingest import to_number
from app.services.entities import ENTITY_PREFIX

ENTITY_NAME_TO_PREFIX: dict[str, str] = {name: prefix for prefix, name in ENTITY_PREFIX.items()}

_COLUMN_ALIASES: dict[str, str] = {
    "anlage": "asset_id",
    "unternummer": "asset_sub_no",
    "bilanzposition": "bilanzposition",
    "buchungskreis": "buchungskreis",
    "geschäftsbereich": "segment",
    "geschaftsbereich": "segment",
    "anlagenklasse": "asset_class",
    "aktivierung am": "capitalization_date",
    "anlagenbezeichnung": "asset_label",
    "bezeichnung": "asset_label",
    "ahk gj-beg": "opening_cost_ahk",
    "zugang": "additions_zugang",
    "abgang": "disposals_abgang",
    "umbuchung": "transfers_umbuchung",
    "afa des jahres": "depreciation",
    "buchwert gj-beg": "opening_nbv",
    "lfd buchwert": "nbv",
    "entity": "entity_name",
}

_NUMERIC_FIELDS = {
    "opening_cost_ahk", "opening_nbv", "additions_zugang", "disposals_abgang",
    "transfers_umbuchung", "depreciation", "nbv", "buchungskreis",
}

_INSERT_COLUMNS = [
    "project_id", "dataset_version_id", "source_file_id", "row_no",
    "as_of_date", "entity_prefix", "fy_label",
    "asset_id", "asset_sub_no", "asset_class", "asset_label", "segment", "bilanzposition",
    "capitalization_date",
    "opening_cost_ahk", "opening_nbv", "additions_zugang", "disposals_abgang",
    "transfers_umbuchung", "depreciation", "nbv",
]


def _norm_header(col: str) -> str:
    return str(col).strip().lower().replace("\xa0", " ")


def _find_source_col(cols: list[str], target_field: str) -> str | None:
    for col in cols:
        if _COLUMN_ALIASES.get(_norm_header(col)) == target_field:
            return col
    return None


def _clean_text(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    return s or None


def _clean_id(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return None
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def as_of_date_for_year(year: int) -> date:
    return date(year, 12, 31)


def map_fixed_asset_rows(
    df: pd.DataFrame,
    *,
    year: int,
    source_file_id: str,
    project_id: str = "default",
) -> list[dict[str, Any]]:
    cols = [str(c) for c in df.columns]
    col_map: dict[str, str] = {}
    for field in set(_COLUMN_ALIASES.values()):
        src = _find_source_col(cols, field)
        if src:
            col_map[field] = src

    if "asset_id" not in col_map:
        raise ValueError(f"anlagengitter missing Anlage column in {cols[:12]}")

    as_of = as_of_date_for_year(year)
    out: list[dict[str, Any]] = []

    for idx, row in df.iterrows():
        asset_id = _clean_id(row.get(col_map["asset_id"]))
        if not asset_id:
            continue

        ent_name = _clean_id(row.get(col_map["entity_name"])) if "entity_name" in col_map else None
        prefix = ENTITY_NAME_TO_PREFIX.get(ent_name or "", None) if ent_name else None

        rec: dict[str, Any] = {
            "project_id": project_id,
            "dataset_version_id": None,
            "source_file_id": source_file_id,
            "row_no": int(idx) if isinstance(idx, int) else len(out),
            "as_of_date": as_of,
            "entity_prefix": prefix,
            "fy_label": f"FY{str(year)[-2:]}A",
            "asset_id": asset_id,
        }

        for field, src_col in col_map.items():
            if field in ("entity_name", "asset_id"):
                continue
            raw = row.get(src_col)
            if field in _NUMERIC_FIELDS:
                rec[field] = to_number(raw)
            elif field == "capitalization_date":
                if raw is not None and not (isinstance(raw, float) and pd.isna(raw)):
                    try:
                        rec[field] = pd.to_datetime(raw).date()
                    except Exception:
                        rec[field] = None
            elif field == "asset_label":
                rec[field] = _clean_text(raw)
            else:
                rec[field] = _clean_id(raw)

        out.append(rec)
    return out


__all__ = ["_INSERT_COLUMNS", "as_of_date_for_year", "map_fixed_asset_rows"]
