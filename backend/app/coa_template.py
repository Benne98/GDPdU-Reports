"""Build a downloadable CoA Master (L1-L4) template workbook (Project Setup rework).

Read-only / DB-free core: ``build_coa_template_workbook`` takes plain Python rows
(already prefilled from ``dim_gl_account`` or a mapping library, or empty) and emits
an openpyxl ``Workbook`` with two sheets — ``Master_BS`` and ``Master_PL`` — using the
EXACT column schema that ``etl.bs_pl_master.read_bs_pl_master`` expects, so the produced
file round-trips through the existing ``/ingest/mapping/commit?format=bs_pl_master`` path.

Master sheet column schema (importer contract)
----------------------------------------------
``read_bs_pl_master`` requires the ID columns ``Account`` and
``Account description`` plus level columns ``L1``-``L4`` (``L5``/``L6`` optional,
mapped to DB ``level_4`` / ``l4_sub``).  Templates carry NO ``Entity`` column — the
entity prefix is supplied externally at commit time.  The PL sheet's first level column is named
``L1 - BS/PL`` (the importer renames it to ``L1``).  Hierarchy mapping the importer
applies: ``L1->level_0, L2->level_1, L3->level_2, L4->level_3, L5->level_4, L6->l4_sub``.

The prefill / DB-query helpers are thin and read-only (no writes).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Optional

from openpyxl import Workbook

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Sheet / column schema  (must match etl.bs_pl_master.read_bs_pl_master)
# --------------------------------------------------------------------------- #
BS_SHEET = "Master_BS"
PL_SHEET = "Master_PL"

#: BS sheet header row — ID cols + L1..L4 required, L5/L6 optional (DB level_4/l4_sub).
#: NO "Entity" column: the entity prefix is supplied externally (by the frontend at
#: commit time) and injected into account_number_group; templates carry accounts only.
BS_HEADERS: list[str] = [
    "Account",
    "Account description",
    "L1",
    "L2",
    "L3",
    "L4",
    "L5",
    "L6",
]

#: PL sheet header row — identical except the first level col is "L1 - BS/PL"
#: (read_bs_pl_master renames it back to "L1").
PL_HEADERS: list[str] = [
    "Account",
    "Account description",
    "L1 - BS/PL",
    "L2",
    "L3",
    "L4",
    "L5",
    "L6",
]

#: Canonical hierarchy fields a prefill row carries, in BS-sheet column order
#: (Entity / Account / Account description handled separately).
#: index 0 -> L1 (level_0), 1 -> L2 (level_1), ... 5 -> L6 (l4_sub).
_LEVEL_FIELDS: tuple[str, ...] = (
    "level_0",
    "level_1",
    "level_2",
    "level_3",
    "level_4",
    "l4_sub",
)


@dataclass
class CoaRow:
    """One chart-of-accounts row, statement-agnostic.

    ``level_0`` ("BS"/"PL") decides which sheet the row lands on.
    """

    entity: str = ""
    account: str = ""
    account_name: str = ""
    level_0: str = ""
    level_1: str = ""
    level_2: str = ""
    level_3: str = ""
    level_4: str = ""
    l4_sub: str = ""

    def is_pl(self) -> bool:
        return str(self.level_0).strip().upper() == "PL"

    def cells(self) -> list[str]:
        """Row values in sheet-column order: Account, descr, L1..L6.

        The ``entity`` attribute is retained on the dataclass (used for filtering
        in ``rows_from_dim_gl_account``) but is NOT emitted as a column — the entity
        prefix is supplied externally at commit time.
        """
        return [
            _clean(self.account),
            _clean(self.account_name),
            _clean(self.level_0),
            _clean(self.level_1),
            _clean(self.level_2),
            _clean(self.level_3),
            _clean(self.level_4),
            _clean(self.l4_sub),
        ]


def _clean(value: Any) -> str:
    """Normalise a cell value to a trimmed string ('' for None / NaN)."""
    if value is None:
        return ""
    s = str(value).strip()
    if s.lower() in ("nan", "none", "<na>"):
        return ""
    return s


# --------------------------------------------------------------------------- #
# Example rows (empty-template path)
# --------------------------------------------------------------------------- #
def _example_rows(entity_label: str) -> list[CoaRow]:
    """A few illustrative rows so a blank template is self-documenting.

    These are CLEARLY FLAGGED as examples — the account number is prefixed
    ``EXAMPLE`` and the description tells the user to delete the row. The user
    replaces them with their own chart of accounts before re-uploading.
    """
    ent = entity_label or "EXAMPLE"
    note = " — EXAMPLE row, delete before upload"
    return [
        CoaRow(
            entity=ent, account="EXAMPLE-1000", account_name="Intangible asset" + note,
            level_0="BS", level_1="Assets", level_2="Fixed assets",
            level_3="Intangible assets",
        ),
        CoaRow(
            entity=ent, account="EXAMPLE-1600", account_name="Trade payables" + note,
            level_0="BS", level_1="Equity & liabilities", level_2="Liabilities",
            level_3="Trade payables",
        ),
        CoaRow(
            entity=ent, account="EXAMPLE-8000", account_name="Domestic revenue" + note,
            level_0="PL", level_1="Revenue", level_2="Sales", level_3="Domestic sales",
        ),
        CoaRow(
            entity=ent, account="EXAMPLE-4000", account_name="Cost of materials" + note,
            level_0="PL", level_1="Expenses", level_2="Material expenses",
            level_3="Cost of materials",
        ),
    ]


# --------------------------------------------------------------------------- #
# Workbook builder (DB-free, pure)
# --------------------------------------------------------------------------- #
def build_coa_template_workbook(
    rows: Iterable[CoaRow],
    style: bool = True,
    statement: str | None = None,
) -> Workbook:
    """Build a Master_BS + Master_PL workbook from CoA rows.

    Rows with ``level_0 == 'PL'`` go to Master_PL; everything else to Master_BS.
    When ``style`` is True (default), header styling reuses
    ``databook_helpers.format_databook_template_sheet`` (GST theme) so the file
    looks like the existing Master/consolidation templates.  ``style=False`` skips
    the cosmetic canvas (used by tests that count data rows).

    ``statement`` selects which sheet(s) to emit:
      * ``'bs'`` -> only the Master_BS sheet (single active sheet),
      * ``'pl'`` -> only the Master_PL sheet (single active sheet),
      * ``None`` -> both sheets (default, backward-compatible two-sheet layout).
    Single-sheet workbooks are still styled and carry the hierarchy dropdowns.
    """
    rows = list(rows)
    bs_rows = [r for r in rows if not r.is_pl()]
    pl_rows = [r for r in rows if r.is_pl()]

    stmt = (statement or "").strip().lower()

    wb = Workbook()
    ws_active = wb.active

    if stmt == "bs":
        ws_active.title = BS_SHEET
        ws_bs, ws_pl = ws_active, None
        _fill_sheet(ws_bs, BS_HEADERS, bs_rows)
    elif stmt == "pl":
        ws_active.title = PL_SHEET
        ws_bs, ws_pl = None, ws_active
        _fill_sheet(ws_pl, PL_HEADERS, pl_rows)
    else:
        # Replace the default sheet with the BS sheet, then add PL.
        ws_bs = ws_active
        ws_bs.title = BS_SHEET
        ws_pl = wb.create_sheet(PL_SHEET)
        _fill_sheet(ws_bs, BS_HEADERS, bs_rows)
        _fill_sheet(ws_pl, PL_HEADERS, pl_rows)

    if style:
        # Apply the shared template theme to the header row of each sheet.  Imported
        # lazily so the pure builder has no hard dependency when the theme is absent.
        try:
            from databook_helpers import format_databook_template_sheet

            if ws_bs is not None:
                format_databook_template_sheet(ws_bs, BS_HEADERS)
            if ws_pl is not None:
                format_databook_template_sheet(ws_pl, PL_HEADERS)
        except Exception:  # noqa: BLE001 - styling is cosmetic; never block the export
            logger.exception("coa_template: header styling skipped")

    # Excel dropdowns for the hierarchy columns (L2/L3/L4) from our canonical labels.
    try:
        _add_hierarchy_dropdowns(wb, ws_bs, ws_pl)
    except Exception:  # noqa: BLE001 - dropdowns are optional; never block the export
        logger.exception("coa_template: hierarchy dropdowns skipped")

    return wb


# --------------------------------------------------------------------------- #
# Hierarchy dropdowns (Excel data validation) from our canonical labels
# --------------------------------------------------------------------------- #
#: Standard mapping library = source of truth for the canonical L1..L4 labels.
_STANDARD_LIBRARY = (
    Path(__file__).resolve().parents[2]
    / "etl" / "mapping_library" / "finssentials_standard_v1.json"
)

#: Sheet column letters for the level columns (same on Master_BS and Master_PL).
#: No Entity column, so everything shifts one left vs. the old layout:
#: L1=C (level_0), L2=D (level_1), L3=E (level_2), L4=F (level_3).
_DROPDOWN_COLS: dict[str, str] = {"level_1": "D", "level_2": "E", "level_3": "F"}


@lru_cache(maxsize=1)
def _canonical_levels() -> dict[str, dict[str, list[str]]]:
    """Distinct canonical labels per statement (BS/PL) and level (level_1/2/3),
    read from the standard mapping library and ordered by its sort fields then name.

    Returns ``{"BS": {"level_1": [...], "level_2": [...], "level_3": [...]}, "PL": {...}}``.
    """
    empty = {s: {"level_1": [], "level_2": [], "level_3": []} for s in ("BS", "PL")}
    try:
        data = json.loads(_STANDARD_LIBRARY.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - dropdowns are optional; never block the export
        logger.exception("coa_template: standard library not loaded for dropdowns")
        return empty

    BIG = float("inf")
    acc: dict[str, dict[str, dict[str, float]]] = {
        s: {"level_1": {}, "level_2": {}, "level_3": {}} for s in ("BS", "PL")
    }
    for e in data.get("entries", []):
        stmt = str(e.get("level_0") or "").strip().upper()
        if stmt not in ("BS", "PL"):
            continue
        for lvl, sort_field in (
            ("level_1", "level_1_sort"),
            ("level_2", "level_2_sort"),
            ("level_3", "level_3_sort"),
        ):
            val = _clean(e.get(lvl))
            if not val:
                continue
            srt = e.get(sort_field)
            srt = float(srt) if isinstance(srt, (int, float)) else BIG
            prev = acc[stmt][lvl].get(val)
            if prev is None or srt < prev:
                acc[stmt][lvl][val] = srt

    return {
        stmt: {
            lvl: [v for v, _ in sorted(vals.items(), key=lambda kv: (kv[1], kv[0]))]
            for lvl, vals in levels.items()
        }
        for stmt, levels in acc.items()
    }


def _add_hierarchy_dropdowns(wb: "Workbook", ws_bs, ws_pl) -> None:
    """Attach list dropdowns to the L2/L3/L4 columns (D/E/F now that there is no
    Entity column) of both Master sheets, sourced from a hidden ``Lists`` sheet
    holding our canonical labels (per BS/PL)."""
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    levels = _canonical_levels()
    if not any(levels[s][l] for s in ("BS", "PL") for l in _DROPDOWN_COLS):
        return  # no labels available → nothing to add

    ws_lists = wb.create_sheet("Lists")
    ws_lists.sheet_state = "hidden"

    # One column per (statement, level); remember each value range for the validation.
    ranges: dict[tuple[str, str], str] = {}
    col_idx = 1
    for stmt in ("BS", "PL"):
        for lvl in _DROPDOWN_COLS:
            vals = levels[stmt][lvl]
            letter = get_column_letter(col_idx)
            ws_lists.cell(row=1, column=col_idx, value=f"{stmt}_{lvl}")
            for i, v in enumerate(vals, start=2):
                ws_lists.cell(row=i, column=col_idx, value=v)
            if vals:
                ranges[(stmt, lvl)] = f"Lists!${letter}$2:${letter}${1 + len(vals)}"
            col_idx += 1

    DATA_LAST = 1000  # cover the blank rows the user fills in
    for ws, stmt in ((ws_bs, "BS"), (ws_pl, "PL")):
        if ws is None:  # single-statement workbook: the other sheet is absent
            continue
        for lvl, col in _DROPDOWN_COLS.items():
            rng = ranges.get((stmt, lvl))
            if not rng:
                continue
            dv = DataValidation(type="list", formula1=rng, allow_blank=True)
            dv.errorStyle = "warning"  # warn but still allow a custom value
            dv.showErrorMessage = True
            dv.errorTitle = "Not a standard label"
            dv.error = "Choose a value from the Finssentials standard list (or keep your own)."
            dv.promptTitle = "Standard hierarchy"
            dv.prompt = "Pick from our standard designations."
            ws.add_data_validation(dv)
            dv.add(f"{col}2:{col}{DATA_LAST}")


def _fill_sheet(ws, headers: list[str], rows: list[CoaRow]) -> None:
    """Write the header row + data rows for one sheet."""
    ws.append(headers)
    for row in rows:
        ws.append(row.cells())


# --------------------------------------------------------------------------- #
# Prefill sources (read-only)
# --------------------------------------------------------------------------- #
def rows_from_dim_gl_account(
    session: "Session",
    entity_prefix: Optional[str],
    fiscal_year: int,
) -> list[CoaRow]:
    """Prefill from existing dim_gl_account for (entity_prefix, fiscal_year).

    ``entity_prefix`` None means all entities for the year.  Entity label resolves
    from dim_legal_entity via the 2-char prefix of account_number_group.  READ-ONLY.
    """
    from sqlalchemy import text

    sql = (
        "SELECT le.entity_name, le.legal_entity_code, ga.gl_account_id, "
        "       ga.account_name, ga.level_0, ga.level_1, ga.level_2, ga.level_3, "
        "       ga.level_4, ga.l4_sub "
        "FROM dim_gl_account ga "
        "LEFT JOIN dim_legal_entity le "
        "       ON le.entity_prefix = LEFT(ga.account_number_group, 2) "
        "WHERE ga.fiscal_year = :fy "
    )
    params: dict[str, Any] = {"fy": int(fiscal_year)}
    if entity_prefix:
        sql += "AND LEFT(ga.account_number_group, 2) = :pfx "
        params["pfx"] = str(entity_prefix)[:2].zfill(2)
    sql += "ORDER BY le.entity_name, ga.level_0, ga.gl_account_id"

    result = session.execute(text(sql), params).fetchall()
    out: list[CoaRow] = []
    for r in result:
        out.append(
            CoaRow(
                entity=_clean(r[0]) or _clean(r[1]),
                account=_clean(r[2]),
                account_name=_clean(r[3]),
                level_0=_clean(r[4]) or "BS",
                level_1=_clean(r[5]),
                level_2=_clean(r[6]),
                level_3=_clean(r[7]),
                level_4=_clean(r[8]),
                l4_sub=_clean(r[9]),
            )
        )
    return out


#: Friendly library aliases → JSON filename in etl/mapping_library/. Single source of
#: truth shared by projects.py and the ingest apply-library recovery endpoint so the
#: alias whitelist never drifts across call sites. Strict whitelist = no path traversal.
LIBRARY_ALIASES: dict[str, str] = {
    "finssentials_standard": "finssentials_standard_v1.json",
    "finssentials_standard_v1": "finssentials_standard_v1.json",
    "skr03": "finssentials_standard_v1.json",
}

#: Directory holding the mapping-library JSON files (repo-root/etl/mapping_library).
_MAPPING_LIBRARY_DIR = Path(__file__).resolve().parents[2] / "etl" / "mapping_library"


def load_mapping_library(alias: str) -> dict:
    """Load a mapping-library JSON by friendly alias (read-only, whitelist only).

    Shared loader for both the project-setup wizard (projects.py) and the ingest
    apply-library recovery endpoint. Raises ``HTTPException(422)`` for an unknown or
    unsafe alias — strict whitelist, so no path traversal is possible.
    """
    from fastapi import HTTPException

    key = (alias or "").strip().lower()
    filename = LIBRARY_ALIASES.get(key)
    if not filename:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown library; choose one of {sorted(set(LIBRARY_ALIASES))}",
        )
    path = _MAPPING_LIBRARY_DIR / filename
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=422, detail="Mapping library not available"
        ) from exc


def rows_from_library(library: dict, entity_label: str) -> list[CoaRow]:
    """Prefill from a mapping library dict ({version, entries:[...]}).

    Each entry carries gl_account_id, account_name and level_0..level_4 / l4_sub.
    The chosen *entity_label* fills the Entity column for every row (a starting
    suggestion the user can edit before re-upload).  PURE / DB-FREE.
    """
    entries = library.get("entries") or []
    ent = entity_label or "Entity"
    out: list[CoaRow] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        out.append(
            CoaRow(
                entity=ent,
                account=_clean(entry.get("gl_account_id")),
                account_name=_clean(entry.get("account_name")),
                level_0=_clean(entry.get("level_0")) or "BS",
                level_1=_clean(entry.get("level_1")),
                level_2=_clean(entry.get("level_2")),
                level_3=_clean(entry.get("level_3")),
                level_4=_clean(entry.get("level_4")),
                l4_sub=_clean(entry.get("l4_sub")),
            )
        )
    return out


def empty_template_rows(entity_label: str) -> list[CoaRow]:
    """Header-only template plus a couple of example rows."""
    return _example_rows(entity_label)
