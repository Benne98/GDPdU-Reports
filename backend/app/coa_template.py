"""Build a downloadable CoA Master (L1-L4) template workbook (Project Setup rework).

Read-only / DB-free core: ``build_coa_template_workbook`` takes plain Python rows
(already prefilled from ``dim_gl_account`` or a mapping library, or empty) and emits
an openpyxl ``Workbook`` with two sheets — ``Master_BS`` and ``Master_PL`` — using the
EXACT column schema that ``etl.bs_pl_master.read_bs_pl_master`` expects, so the produced
file round-trips through the existing ``/ingest/mapping/commit?format=bs_pl_master`` path.

Master sheet column schema (importer contract)
----------------------------------------------
``read_bs_pl_master`` requires the ID columns ``Entity``, ``Account``,
``Account description`` plus level columns ``L1``-``L4`` (``L5``/``L6`` optional,
mapped to DB ``level_4`` / ``l4_sub``).  The PL sheet's first level column is named
``L1 - BS/PL`` (the importer renames it to ``L1``).  Hierarchy mapping the importer
applies: ``L1->level_0, L2->level_1, L3->level_2, L4->level_3, L5->level_4, L6->l4_sub``.

The prefill / DB-query helpers are thin and read-only (no writes).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
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
BS_HEADERS: list[str] = [
    "Entity",
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
    "Entity",
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
        """Row values in sheet-column order: Entity, Account, descr, L1..L6."""
        return [
            _clean(self.entity),
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
    """A couple of illustrative rows so an empty template is self-documenting."""
    ent = entity_label or "Example Entity"
    return [
        CoaRow(
            entity=ent,
            account="10000",
            account_name="Example intangible asset",
            level_0="BS",
            level_1="Assets",
            level_2="Fixed assets",
            level_3="Intangible assets",
        ),
        CoaRow(
            entity=ent,
            account="80000",
            account_name="Example revenue",
            level_0="PL",
            level_1="Revenue",
            level_2="Sales",
            level_3="Domestic sales",
        ),
    ]


# --------------------------------------------------------------------------- #
# Workbook builder (DB-free, pure)
# --------------------------------------------------------------------------- #
def build_coa_template_workbook(rows: Iterable[CoaRow], style: bool = True) -> Workbook:
    """Build a Master_BS + Master_PL workbook from CoA rows.

    Rows with ``level_0 == 'PL'`` go to Master_PL; everything else to Master_BS.
    When ``style`` is True (default), header styling reuses
    ``databook_helpers.format_databook_template_sheet`` (GST theme) so the file
    looks like the existing Master/consolidation templates.  ``style=False`` skips
    the cosmetic canvas (used by tests that count data rows).
    """
    rows = list(rows)
    bs_rows = [r for r in rows if not r.is_pl()]
    pl_rows = [r for r in rows if r.is_pl()]

    wb = Workbook()
    # Replace the default sheet with the BS sheet, then add PL.
    ws_bs = wb.active
    ws_bs.title = BS_SHEET
    ws_pl = wb.create_sheet(PL_SHEET)

    _fill_sheet(ws_bs, BS_HEADERS, bs_rows)
    _fill_sheet(ws_pl, PL_HEADERS, pl_rows)

    if style:
        # Apply the shared template theme to the header row of each sheet.  Imported
        # lazily so the pure builder has no hard dependency when the theme is absent.
        try:
            from databook_helpers import format_databook_template_sheet

            format_databook_template_sheet(ws_bs, BS_HEADERS)
            format_databook_template_sheet(ws_pl, PL_HEADERS)
        except Exception:  # noqa: BLE001 - styling is cosmetic; never block the export
            logger.exception("coa_template: header styling skipped")

    return wb


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
