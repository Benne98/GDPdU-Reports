"""Column mapping and row normalisation for personaltable / personnel subledger loads."""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

import pandas as pd

# Leading YYYY-MM-DD / YYYY/MM/DD -> ISO (year-first); anything else is treated as
# day-first (German DD.MM.YYYY), matching the rest of the ingest.
_ISO_DATE_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}")

from app.services.draft_ingest import build_mapped_rows, to_number
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


# --------------------------------------------------------------------------- #
# Generic wizard commit — map arbitrary FTE/payroll columns to the fixed schema
# --------------------------------------------------------------------------- #
# Target fields coerced through ``to_number`` by ``build_mapped_rows``.
_COMMIT_NUMBER_FIELDS = {
    "beschaeftigungsgrad", "months_active", "gesamtsumme", "sozialversicherung",
}


def _as_pydate(value: Any) -> Optional[date]:
    """Lenient single-value date parse, else None.

    ISO ``YYYY-MM-DD`` values parse year-first; everything else (German dotted
    ``DD.MM.YYYY``) parses day-first, consistent with ``draft_ingest.to_date``.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    dayfirst = not _ISO_DATE_RE.match(s)
    ts = pd.to_datetime(s, errors="coerce", dayfirst=dayfirst)
    if pd.isna(ts):
        return None
    return ts.date()


def _months_overlap(entry: Any, exit_: Any, year: int) -> float:
    """Inclusive whole-month overlap of [entry, exit] within FY ``year`` (0..12).

    Missing entry -> Jan 1; missing exit -> Dec 31 (still employed at year end).
    Both endpoints are clamped into [Jan 1, Dec 31] of ``year``; a non-overlapping
    range yields 0.  Inclusive month count: Jan..Dec = 12, Mar..Dec = 10.
    """
    fy_start = date(year, 1, 1)
    fy_end = date(year, 12, 31)
    start = _as_pydate(entry) or fy_start
    end = _as_pydate(exit_) or fy_end
    if start < fy_start:
        start = fy_start
    if end > fy_end:
        end = fy_end
    if end < start:
        return 0.0
    months = (end.year - start.year) * 12 + (end.month - start.month) + 1
    return float(min(12, max(0, months)))


def build_personnel_rows(
    df: pd.DataFrame,
    *,
    year: int,
    fy_label: str,
    file_id: str,
    tenure_mode: str,
    payroll_mode: str,
    entity_mode: str = "per_entity",
    entity_prefix: Optional[str] = None,
    entity_column: Optional[str] = None,
    employment_pct_col: Optional[str] = None,
    months_col: Optional[str] = None,
    entry_col: Optional[str] = None,
    exit_col: Optional[str] = None,
    total_col: Optional[str] = None,
    monthly_col: Optional[str] = None,
    component_cols: Optional[list[str]] = None,
    social_col: Optional[str] = None,
    personalnummer_col: Optional[str] = None,
    bereich_col: Optional[str] = None,
    bereichuntergruppe_col: Optional[str] = None,
    kst_name_col: Optional[str] = None,
    gew_ang_col: Optional[str] = None,
    name_by_prefix: Optional[dict[str, str]] = None,
) -> tuple[list[dict[str, Any]], set[str], dict[str, str]]:
    """Map a wizard FTE/payroll frame onto ``fact_personnel_employee`` insert dicts.

    Reuses the generic ``draft_ingest.build_mapped_rows`` 1:1 (same as the Anlagen
    commit): the caller-selected source headers are mapped onto the fixed target
    fields, and synthetic ``__payroll__`` / ``__months__`` columns are pre-computed
    on ``df`` so the mapping stays a pure column pass-through.

    PRESERVES the source sign — payroll cost is stored positive as supplied (the
    Payroll page's aggregate applies its own sign), matching the offline loader.

    Returns ``(rows, distinct_prefixes, column_map)``.
    """
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    # NaN -> None so number/text coercion behaves like the string-typed GL loader.
    df = df.where(pd.notna(df), None)

    column_map: dict[str, str] = {}
    if employment_pct_col:
        column_map["beschaeftigungsgrad"] = employment_pct_col
    if social_col:
        column_map["sozialversicherung"] = social_col
    if bereich_col:
        column_map["bereich"] = bereich_col
    if bereichuntergruppe_col:
        column_map["bereichuntergruppe"] = bereichuntergruppe_col
    if kst_name_col:
        column_map["kst_name"] = kst_name_col
    if gew_ang_col:
        column_map["gew_ang"] = gew_ang_col
    if personalnummer_col:
        column_map["personalnummer"] = personalnummer_col

    # ----- Payroll basis -> synthetic gesamtsumme (build_mapped_rows is 1:1) -----
    if payroll_mode == "sum_components" and component_cols:
        acc = None
        for c in component_cols:
            if c not in df.columns:
                raise ValueError(f"component column {c!r} not found in file")
            s = df[c].map(lambda v: to_number(v) or 0.0)
            acc = s if acc is None else acc + s
        df["__payroll__"] = acc if acc is not None else 0.0
        column_map["gesamtsumme"] = "__payroll__"
    elif payroll_mode == "total_col" and total_col:
        column_map["gesamtsumme"] = total_col
    elif payroll_mode == "monthly_col" and monthly_col:
        def _annualize(v: Any) -> Optional[float]:
            n = to_number(v)
            return None if n is None else n * 12.0
        df["__payroll__"] = df[monthly_col].map(_annualize)
        column_map["gesamtsumme"] = "__payroll__"

    # ----- Tenure basis -> synthetic months_active ------------------------------
    if tenure_mode == "months_col" and months_col:
        column_map["months_active"] = months_col
    elif tenure_mode == "entry_exit_dates":
        records = df.to_dict(orient="records")
        df["__months__"] = [
            _months_overlap(
                r.get(entry_col) if entry_col else None,
                r.get(exit_col) if exit_col else None,
                year,
            )
            for r in records
        ]
        column_map["months_active"] = "__months__"

    names = name_by_prefix or {}
    as_of = as_of_date_for_year(year)

    def _inject(out: dict[str, Any], prefix: Optional[str]) -> None:
        out["as_of_date"] = as_of
        out["entity_name"] = names.get(prefix or "") or ENTITY_PREFIX.get(prefix or "")
        # personalnummer is NOT NULL: synthesise from row_no when unmapped or blank.
        if not out.get("personalnummer"):
            out["personalnummer"] = str(out.get("row_no"))

    rows, prefixes = build_mapped_rows(
        df,
        column_map=column_map,
        number_fields=_COMMIT_NUMBER_FIELDS,
        date_fields=set(),
        entity_mode=entity_mode,
        entity_column=entity_column,
        entity_prefix=entity_prefix,
        fy_label=fy_label,
        file_id=file_id,
        extra_per_row=_inject,
    )
    return rows, prefixes, column_map
