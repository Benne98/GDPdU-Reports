"""Column mapping and row normalisation for personaltable / personnel subledger loads."""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

import pandas as pd

from app.services.draft_ingest import to_number
from app.services.entities import ENTITY_PREFIX

# Entity display name (personaltable ``Entity`` column) -> entity_prefix
ENTITY_NAME_TO_PREFIX: dict[str, str] = {name: prefix for prefix, name in ENTITY_PREFIX.items()}

# Source header aliases -> normalised DB field
_COLUMN_ALIASES: dict[str, str] = {
    "entity": "entity_name",
    "personalnummer": "personalnummer",
    "bereich": "bereich",
    "bereichuntergruppe": "bereichuntergruppe",
    "gew./ang.": "gew_ang",
    "gew/ang.": "gew_ang",
    "zugehörigkeit": "zugehoerigkeit",
    "zugehorigkeit": "zugehoerigkeit",
    "beschäftigungsgrad": "beschaeftigungsgrad",
    "beschaftigungsgrad": "beschaeftigungsgrad",
    "kommentar": "kommentar",
    "kstname": "kst_name",
    "kostenstelle": "kostenstelle",
    "summe": "months_active",
    "lohnstunden": "lohnstunden",
    "era-gruppe": "era_gruppe",
    "gehalt mon.": "gehalt_mon",
    "grundgehalt": "grundgehalt",
    "prämie": "praemie",
    "praemie": "praemie",
    "urlaubsgeld": "urlaubsgeld",
    "tzug": "tzug",
    "transformationsgeld": "transformationsgeld",
    "sozialversicherung kalk.": "sozialversicherung",
    "sozialversicherung": "sozialversicherung",
    "kontoführungsgebühr": "kontofuehrungsgebuehr",
    "kontofuhrungsgebuhr": "kontofuehrungsgebuehr",
    "schichtzulagen": "schichtzulagen",
    "fahrgeldzuschuss": "fahrgeldzuschuss",
    "kosten leihpersonal": "kosten_leihpersonal",
    "pausch. offene verhandlungen": "pausch_offene_verhandlungen",
    "tariferhöhungen ab xx": "tariferhoehungen",
    "tariferhohungen ab xx": "tariferhoehungen",
    "gesamtsumme": "gesamtsumme",
    "quelle": "quelle",
}

_NUMERIC_FIELDS = {
    "zugehoerigkeit", "beschaeftigungsgrad", "months_active", "lohnstunden",
    "gehalt_mon", "grundgehalt", "praemie", "urlaubsgeld", "tzug",
    "transformationsgeld", "sozialversicherung", "kontofuehrungsgebuehr",
    "schichtzulagen", "fahrgeldzuschuss", "kosten_leihpersonal",
    "pausch_offene_verhandlungen", "tariferhoehungen", "gesamtsumme",
}

_INSERT_COLUMNS = [
    "project_id", "dataset_version_id", "source_file_id", "row_no",
    "as_of_date", "entity_prefix", "entity_name", "fy_label",
    "personalnummer", "bereich", "bereichuntergruppe", "gew_ang",
    "zugehoerigkeit", "beschaeftigungsgrad", "kommentar", "kst_name",
    "kostenstelle", "months_active", "lohnstunden", "era_gruppe",
    "gehalt_mon", "grundgehalt", "praemie", "urlaubsgeld", "tzug",
    "transformationsgeld", "sozialversicherung", "kontofuehrungsgebuehr",
    "schichtzulagen", "fahrgeldzuschuss", "kosten_leihpersonal",
    "pausch_offene_verhandlungen", "tariferhoehungen", "gesamtsumme", "quelle",
]


def _norm_header(col: str) -> str:
    return str(col).strip().lower().replace("\xa0", " ")


def _find_source_col(cols: list[str], target_field: str) -> str | None:
    for col in cols:
        if _COLUMN_ALIASES.get(_norm_header(col)) == target_field:
            return col
    return None


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


def map_personnel_rows(
    df: pd.DataFrame,
    *,
    year: int,
    source_file_id: str,
    project_id: str = "default",
) -> list[dict[str, Any]]:
    """Map a personaltable dataframe to fact_personnel_employee insert dicts."""
    cols = [str(c) for c in df.columns]
    col_map: dict[str, str] = {}
    for field in set(_COLUMN_ALIASES.values()):
        src = _find_source_col(cols, field)
        if src:
            col_map[field] = src

    if "entity_name" not in col_map or "personalnummer" not in col_map:
        raise ValueError(f"personaltable missing Entity or Personalnummer columns in {cols[:12]}")

    as_of = as_of_date_for_year(year)
    out: list[dict[str, Any]] = []
    unresolved = 0

    for idx, row in df.iterrows():
        ent_name = _clean_id(row.get(col_map["entity_name"]))
        prefix = ENTITY_NAME_TO_PREFIX.get(ent_name or "", None) if ent_name else None
        if ent_name and prefix is None:
            unresolved += 1
        pno = _clean_id(row.get(col_map["personalnummer"]))
        if not pno:
            continue

        rec: dict[str, Any] = {
            "project_id": project_id,
            "dataset_version_id": None,
            "source_file_id": source_file_id,
            "row_no": int(idx) if isinstance(idx, int) else len(out),
            "as_of_date": as_of,
            "entity_prefix": prefix,
            "entity_name": ent_name,
            "fy_label": str(year),
            "personalnummer": pno,
        }
        for field, src_col in col_map.items():
            if field in ("entity_name", "personalnummer"):
                continue
            raw = row.get(src_col)
            if field in _NUMERIC_FIELDS:
                rec[field] = to_number(raw)
            else:
                rec[field] = _clean_id(raw) if field not in ("kommentar",) else (
                    None if raw is None or (isinstance(raw, float) and pd.isna(raw)) else str(raw).strip()
                )
        out.append(rec)

    if unresolved:
        print(f"    WARNING: {unresolved} rows had unmapped Entity name")
    return out
