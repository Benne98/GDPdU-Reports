"""Budget planning Excel round-trip — build / parse / diff (Phase 3).

Pure-ish helpers backing the three ``/api/v1/budget`` Excel endpoints:

  ``build_template_workbook``  — a prefilled scope template (openpyxl Workbook):
      Sheet 1 "Positions"  : one row per L3 position (and per discovered L4 child
                             when ``level='L4'``), columns
                             [__KEY__, Entity, Line code, Label, Level, L4 name,
                              Annual, Jan..Dec].  Prefilled from the current budget
                             (``build_grid``) — saved value if present, else the
                             Finssentials-heuristic suggestion.
      Sheet 2 "Partners"   : Top-N named partners + "Other" for each partner-driven
                             position (NET_SALES customers, COST_OF_MATERIALS
                             suppliers), columns
                             [__KEY__, Line code, Partner id, Partner name,
                              Annual, Jan..Dec].

  ``parse_workbook`` / ``diff_against_grid`` — read an uploaded workbook back into
      structured records and compute a PREVIEW DIFF (no write) versus the current
      ``build_grid`` snapshot.

  ``commit_records`` — write parsed records into ``fact_position_plan`` via the
      XOR-aware ``patch_position`` service (months as absolute presented values),
      one position at a time, in the service's own transaction.

Reuse: ``databook_helpers.format_databook_template_sheet`` for the header band /
canvas styling, the GST theme greys for locked meta cells, and
``budget_service.build_grid`` for the prefill / diff baseline.  No new deps.

============================================================ KEY COLUMN
The hidden, locked ``__KEY__`` column makes a re-uploaded row map back to a
position unambiguously even if the human renamed a label or reordered rows:

    Positions sheet : "{line_code}|{level_4}"   (level_4 = '' for the L3 row)
    Partners sheet  : "{line_code}|{partner_id}"

The visible Entity / Line code / Label / Level / L4 columns are for the human; only
``__KEY__`` (plus Annual + the 12 months) is authoritative on re-upload.

============================================================ SIGN
The grid exchanges PRESENTED values with the client (the same numbers shown in the
reporting statements).  The template is prefilled with presented values and the
upload is interpreted as presented values; the single presented↔stored flip stays
in ``budget_service`` (``present_to_stored``) on the write path — this module never
touches stored GL signs.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Protection
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session

from app.services import budget_service

# 12 calendar month labels (Jan..Dec) — fiscal_period 1..12 in the grid order.
MONTH_LABELS: tuple[str, ...] = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
N_MONTHS = len(MONTH_LABELS)

# Sheet / column layout.
POSITIONS_SHEET = "Positions"
PARTNERS_SHEET = "Partners"

KEY_COL = "__KEY__"
POSITION_META_COLS = ["Entity", "Line code", "Label", "Level", "L4 name"]
PARTNER_META_COLS = ["Line code", "Partner id", "Partner name"]
VALUE_COLS = ["Annual", *MONTH_LABELS]

POSITION_HEADERS = [KEY_COL, *POSITION_META_COLS, *VALUE_COLS]
PARTNER_HEADERS = [KEY_COL, *PARTNER_META_COLS, *VALUE_COLS]

# Sentinel split between line_code and the secondary key (level_4 / partner_id).
KEY_SEP = "|"

_NUMBER_FORMAT = "#,##0.00"
_META_GREY = PatternFill(fill_type="solid", fgColor="FFEFEFEF")
_LOCKED = Protection(locked=True)
_UNLOCKED = Protection(locked=False)


# =========================================================================== #
# Template build
# =========================================================================== #
def _months_list(months: Any) -> list[float]:
    """Coerce a grid 'months' value (list[12]) to a clean list of 12 floats."""
    vals = list(months or [])
    out = [float(vals[i]) if i < len(vals) else 0.0 for i in range(N_MONTHS)]
    return out


def _prefill_value(saved_or_suggestion: dict[str, Any]) -> tuple[float, list[float]]:
    """(annual, months[12]) for a position prefill.

    A position carries its current value as ``annual`` + ``months`` (saved budget
    if present, else the seasonalized seed) AND a ``suggestion``.  We prefill with
    the current value when it is non-zero (a real saved/seed value), else fall back
    to the heuristic suggestion so the human starts from a sensible proposal.
    """
    annual = float(saved_or_suggestion.get("annual") or 0.0)
    months = _months_list(saved_or_suggestion.get("months"))
    if abs(annual) < 1e-9 and not any(abs(m) > 1e-9 for m in months):
        sug = saved_or_suggestion.get("suggestion") or {}
        annual = float(sug.get("annual") or 0.0)
        months = _months_list(sug.get("months"))
    return round(annual, 2), [round(m, 2) for m in months]


def _l4_rows_for_position(
    session: Session,
    *,
    line_code: str,
    statement: str,
    fiscal_year: int,
    entity_prefix: Optional[str],
) -> list[tuple[str, float, list[float]]]:
    """[(level_4, annual, months[12])] for a position's discovered L4 children.

    Built from the live L4 actuals profile (presented annual + own seasonal
    weights); empty when the position has no L4 history (then only the L3 row is
    emitted by the caller).
    """
    profiles = budget_service._l4_actual_profiles(
        session, line_code=line_code, statement=statement,
        fiscal_year=fiscal_year, entity_prefix=entity_prefix,
    )
    out: list[tuple[str, float, list[float]]] = []
    for l4, (annual, weights) in sorted(profiles.items()):
        months = budget_service.seasonalize(annual, weights)
        out.append((l4, round(annual, 2), [round(months[p], 2) for p in budget_service.PERIODS]))
    return out


def _write_header(ws, headers: list[str]) -> None:
    """Style the header band (GST theme via databook_helpers) and label each col."""
    from databook_helpers import format_databook_template_sheet

    format_databook_template_sheet(ws, headers)
    # format_databook_template_sheet only labels the FIRST len(headers) columns; it
    # already did so above, but re-assert the labels (it writes them) and keep the
    # header row bold.
    for c, label in enumerate(headers, start=1):
        ws.cell(row=1, column=c, value=label)


def _write_value_row(
    ws,
    row_idx: int,
    *,
    key: str,
    meta: list[Any],
    annual: float,
    months: list[float],
    value_start_col: int,
) -> None:
    """Write one data row: hidden+locked key, locked grey meta, unlocked values."""
    # Key (col 1) — hidden + locked.
    ws.cell(row=row_idx, column=1, value=key).protection = _LOCKED
    # Meta columns — locked + grey.
    for i, val in enumerate(meta):
        cell = ws.cell(row=row_idx, column=2 + i, value=val)
        cell.protection = _LOCKED
        cell.fill = _META_GREY
    # Annual + 12 months — unlocked, numeric format.
    values = [round(float(annual), 2), *[round(float(m), 2) for m in months]]
    for j, val in enumerate(values):
        cell = ws.cell(row=row_idx, column=value_start_col + j, value=val)
        cell.protection = _UNLOCKED
        cell.number_format = _NUMBER_FORMAT
        cell.alignment = Alignment(horizontal="right", vertical="center")


def _make_key(line_code: str, secondary: str) -> str:
    return f"{line_code}{KEY_SEP}{secondary or ''}"


def parse_key(key: str) -> tuple[str, str]:
    """Split a ``__KEY__`` value into (line_code, secondary).

    ``secondary`` is the level_4 (Positions sheet) or partner_id (Partners sheet).
    A key with no separator is treated as (key, '').
    """
    s = str(key or "")
    if KEY_SEP in s:
        lc, sec = s.split(KEY_SEP, 1)
        return lc.strip(), sec.strip()
    return s.strip(), ""


def build_template_workbook(
    session: Session,
    *,
    statement: str,
    fiscal_year: int,
    entity_prefix: Optional[str],
    level: str = "L3",
    top_n: int = 20,
    grid: Optional[dict[str, Any]] = None,
) -> Workbook:
    """Build the prefilled scope template workbook (caller streams it).

    ``level`` ∈ {'L3','L4'}: 'L4' adds one row per discovered L4 child below each
    position's L3 row (PL only meaningfully — BS positions rarely have L4 history,
    in which case only the L3 row is emitted).  ``grid`` may be injected (tests);
    otherwise it is built via :func:`budget_service.build_grid`.
    """
    statement = (statement or "PL").upper()
    if grid is None:
        grid = budget_service.build_grid(
            session, statement=statement, fiscal_year=fiscal_year,
            entity_prefix=entity_prefix, top_n=top_n,
        )
    entity_label = entity_prefix or "all"
    want_l4 = str(level or "L3").upper() == "L4"

    wb = Workbook()
    # Drop the default sheet; create our two named sheets in order.
    if "Sheet" in wb.sheetnames:
        del wb["Sheet"]
    ws_pos = wb.create_sheet(POSITIONS_SHEET)
    ws_par = wb.create_sheet(PARTNERS_SHEET)

    # ---- Sheet 1: Positions ------------------------------------------------- #
    _write_header(ws_pos, POSITION_HEADERS)
    value_start = len(POSITION_HEADERS) - len(VALUE_COLS) + 1  # 1-based col of "Annual"
    row = 2
    for pos in grid.get("positions", []):
        line_code = str(pos["line_code"])
        label = str(pos.get("label") or line_code)
        annual, months = _prefill_value(pos)
        _write_value_row(
            ws_pos, row,
            key=_make_key(line_code, ""),
            meta=[entity_label, line_code, label, "L3", ""],
            annual=annual, months=months, value_start_col=value_start,
        )
        row += 1
        if want_l4 and statement == "PL":
            for l4, l4_annual, l4_months in _l4_rows_for_position(
                session, line_code=line_code, statement=statement,
                fiscal_year=fiscal_year, entity_prefix=entity_prefix,
            ):
                _write_value_row(
                    ws_pos, row,
                    key=_make_key(line_code, l4),
                    meta=[entity_label, line_code, label, "L4", l4],
                    annual=l4_annual, months=l4_months, value_start_col=value_start,
                )
                row += 1

    # ---- Sheet 2: Partners -------------------------------------------------- #
    _write_header(ws_par, PARTNER_HEADERS)
    p_value_start = len(PARTNER_HEADERS) - len(VALUE_COLS) + 1
    prow = 2
    for pos in grid.get("positions", []):
        if not pos.get("is_partner_driven"):
            continue
        line_code = str(pos["line_code"])
        for pr in pos.get("partners") or []:
            pid = str(pr["partner_id"])
            _write_value_row(
                ws_par, prow,
                key=_make_key(line_code, pid),
                meta=[line_code, pid, str(pr.get("name") or pid)],
                annual=round(float(pr.get("annual") or 0.0), 2),
                months=_months_list(pr.get("months")),
                value_start_col=p_value_start,
            )
            prow += 1
        # "Other" reconciling remainder row (partner_id sentinel) — seasonalize
        # uniformly for display only (the commit re-derives Other server-side).
        other_annual = float(pos.get("other") or 0.0)
        o_months = budget_service.seasonalize(other_annual, None)
        _write_value_row(
            ws_par, prow,
            key=_make_key(line_code, budget_service.OTHER_PARTNER_ID),
            meta=[line_code, budget_service.OTHER_PARTNER_ID, "Other"],
            annual=round(other_annual, 2),
            months=[round(o_months[p], 2) for p in budget_service.PERIODS],
            value_start_col=p_value_start,
        )
        prow += 1

    # Hide + lock the key column on both sheets; protect the sheet so meta cells are
    # read-only while the value cells (unlocked above) remain editable.
    for ws in (ws_pos, ws_par):
        ws.column_dimensions["A"].hidden = True
        ws.protection.sheet = True
        ws.protection.enable()

    return wb


def template_filename(statement: str, entity_prefix: Optional[str], fiscal_year: int) -> str:
    """``budget_{statement}_{entity|all}_{fy}.xlsx``."""
    ent = (entity_prefix or "all").strip() or "all"
    safe = "".join(ch for ch in ent if ch.isalnum() or ch in ("_", "-")) or "all"
    return f"budget_{statement.upper()}_{safe}_{int(fiscal_year)}.xlsx"


# =========================================================================== #
# Parse + diff
# =========================================================================== #
class TemplateError(ValueError):
    """Raised when an uploaded workbook does not match the expected structure."""


# H2: sane ceilings for a budget template. The Positions sheet is one row per L3
# (+ discovered L4 children) and the Partners sheet Top-N + Other; even a large
# consolidated scope stays far below these. Reject anything bigger BEFORE iterating
# cells so a crafted workbook (zip bomb / dimension bomb) cannot blow up the parse.
# Note: ``format_databook_template_sheet`` styles a fixed ~100-column canvas, so the
# column ceiling is set above that band while still bounding a true dimension bomb.
MAX_PARSE_ROWS = 5000
MAX_PARSE_COLS = 120

# L1: bound the secondary key length so a hand-edited key cannot overflow the DB
# column. level_4 and partner_id map to bounded columns in fact_position_plan.
MAX_LEVEL_4_LEN = 120
MAX_PARTNER_ID_LEN = 64


def assert_parse_bounds(wb: Workbook) -> None:
    """Reject a workbook whose sheets exceed the budget-template ceilings (H2).

    Checks ``ws.max_row`` / ``ws.max_column`` on every worksheet BEFORE any cell
    iteration so a dimension/zip bomb is rejected cheaply.  Raises
    :class:`TemplateError` (→ clean 422) on violation.
    """
    for ws in wb.worksheets:
        max_row = ws.max_row or 0
        max_col = ws.max_column or 0
        if max_row > MAX_PARSE_ROWS:
            raise TemplateError(
                f"sheet '{ws.title}' has too many rows ({max_row} > {MAX_PARSE_ROWS})"
            )
        if max_col > MAX_PARSE_COLS:
            raise TemplateError(
                f"sheet '{ws.title}' has too many columns ({max_col} > {MAX_PARSE_COLS})"
            )


def _header_index(ws, expected: list[str]) -> dict[str, int]:
    """Map header label → 1-based column for ``ws`` row 1, validating expected cols.

    Raises :class:`TemplateError` when a required header is missing.
    """
    found: dict[str, int] = {}
    for c in range(1, ws.max_column + 1):
        label = ws.cell(row=1, column=c).value
        if label is not None and str(label).strip():
            found[str(label).strip()] = c
    missing = [h for h in expected if h not in found]
    if missing:
        raise TemplateError(
            f"sheet '{ws.title}' missing expected columns: {', '.join(missing)}"
        )
    return found


def _coerce_number(val: Any, *, where: str) -> float:
    """Coerce a cell to a finite float (blank → 0.0).  Rejects NaN/Inf/garbage."""
    if val is None or (isinstance(val, str) and not val.strip()):
        return 0.0
    try:
        # Tolerate thousands separators / stray spaces in hand-typed cells.
        if isinstance(val, str):
            f = float(val.replace(" ", "").replace(",", ""))
        else:
            f = float(val)
    except (TypeError, ValueError) as exc:
        raise TemplateError(f"non-numeric value {val!r} at {where}") from exc
    if not math.isfinite(f):
        raise TemplateError(f"non-finite value {val!r} at {where}")
    return f


def _parse_sheet(ws, headers: list[str], *, secondary_name: str) -> list[dict[str, Any]]:
    """Parse one value sheet into [{line_code, secondary, annual, months[12]}].

    ``secondary`` is named by the key column (level_4 or partner_id).  A row with a
    blank/absent key is skipped.  Months are read from the 12 month columns; annual
    is read from the Annual column (informational — the months are authoritative
    on commit, mirroring the grid's months-first contract).
    """
    idx = _header_index(ws, headers)
    key_c = idx[KEY_COL]
    annual_c = idx["Annual"]
    month_cols = [idx[m] for m in MONTH_LABELS]
    records: list[dict[str, Any]] = []
    for r in range(2, ws.max_row + 1):
        key = ws.cell(row=r, column=key_c).value
        if key is None or not str(key).strip():
            continue
        line_code, secondary = parse_key(key)
        if not line_code:
            continue
        # L1: bound the secondary key length (level_4 / partner_id) so a hand-edited
        # key cannot overflow the DB column — clean 422 instead of a DB error.
        if secondary_name == "level_4" and len(secondary) > MAX_LEVEL_4_LEN:
            raise TemplateError(
                f"level_4 too long ({len(secondary)} > {MAX_LEVEL_4_LEN}) at {ws.title}!row{r}"
            )
        if secondary_name == "partner_id" and len(secondary) > MAX_PARTNER_ID_LEN:
            raise TemplateError(
                f"partner_id too long ({len(secondary)} > {MAX_PARTNER_ID_LEN}) at {ws.title}!row{r}"
            )
        annual = _coerce_number(
            ws.cell(row=r, column=annual_c).value, where=f"{ws.title}!Annual:row{r}"
        )
        months = [
            _coerce_number(ws.cell(row=r, column=c).value, where=f"{ws.title}!{MONTH_LABELS[i]}:row{r}")
            for i, c in enumerate(month_cols)
        ]
        records.append({
            "line_code": line_code,
            secondary_name: secondary,
            "annual": annual,
            "months": months,
        })
    return records


def parse_workbook(wb: Workbook) -> dict[str, list[dict[str, Any]]]:
    """Parse an uploaded budget workbook into structured records.

    Returns ``{"positions": [...], "partners": [...]}``.  Raises
    :class:`TemplateError` on a missing sheet / column or a non-finite / non-numeric
    cell.
    """
    if POSITIONS_SHEET not in wb.sheetnames:
        raise TemplateError(f"workbook missing required sheet '{POSITIONS_SHEET}'")
    positions = _parse_sheet(wb[POSITIONS_SHEET], POSITION_HEADERS, secondary_name="level_4")
    partners: list[dict[str, Any]] = []
    if PARTNERS_SHEET in wb.sheetnames:
        partners = _parse_sheet(wb[PARTNERS_SHEET], PARTNER_HEADERS, secondary_name="partner_id")
    return {"positions": positions, "partners": partners}


def _grid_position_index(grid: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """{line_code: position dict} for the current grid (diff baseline)."""
    return {str(p["line_code"]): p for p in grid.get("positions", [])}


def _current_value(
    grid_index: dict[str, dict[str, Any]],
    *,
    line_code: str,
    partner_id: str = "",
) -> tuple[float, list[float]]:
    """(annual, months[12]) for the current grid value of a position or partner."""
    pos = grid_index.get(line_code)
    if pos is None:
        return 0.0, [0.0] * N_MONTHS
    if not partner_id:
        return float(pos.get("annual") or 0.0), _months_list(pos.get("months"))
    if partner_id == budget_service.OTHER_PARTNER_ID:
        other = float(pos.get("other") or 0.0)
        return other, [0.0] * N_MONTHS  # months not tracked for Other in the grid
    for pr in pos.get("partners") or []:
        if str(pr["partner_id"]) == partner_id:
            return float(pr.get("annual") or 0.0), _months_list(pr.get("months"))
    return 0.0, [0.0] * N_MONTHS


def diff_against_grid(
    parsed: dict[str, list[dict[str, Any]]],
    grid: dict[str, Any],
    *,
    known_line_codes: Optional[Iterable[str]] = None,
    eps: float = 1e-2,
) -> dict[str, Any]:
    """Compute a PREVIEW DIFF of parsed records versus the current grid.

    Returns ``{"changes": [...], "unknown_line_codes": [...], "summary": {...}}``.
    Each change is ``{line_code, level_4, partner_id, field, old, new}`` where
    ``field`` is ``"annual"`` or ``"month:<Jan..Dec>"``.  No write occurs.

    A parsed row whose ``line_code`` is not in ``known_line_codes`` (when given) is
    collected under ``unknown_line_codes`` and excluded from ``changes`` (the caller
    rejects unknown codes).
    """
    grid_index = _grid_position_index(grid)
    known = set(known_line_codes) if known_line_codes is not None else None
    changes: list[dict[str, Any]] = []
    unknown: set[str] = set()

    def _emit(line_code: str, level_4: str, partner_id: str, old_a: float,
              old_m: list[float], new_a: float, new_m: list[float]) -> None:
        if abs(new_a - old_a) > eps:
            changes.append({
                "line_code": line_code, "level_4": level_4, "partner_id": partner_id,
                "field": "annual", "old": round(old_a, 2), "new": round(new_a, 2),
            })
        for i in range(N_MONTHS):
            if abs(new_m[i] - old_m[i]) > eps:
                changes.append({
                    "line_code": line_code, "level_4": level_4, "partner_id": partner_id,
                    "field": f"month:{MONTH_LABELS[i]}",
                    "old": round(old_m[i], 2), "new": round(new_m[i], 2),
                })

    for rec in parsed.get("positions", []):
        lc = rec["line_code"]
        if known is not None and lc not in known:
            unknown.add(lc)
            continue
        # L4 rows have no separate grid baseline (the grid SUMs them into the
        # position); diff an L4 row against 0 so any value shows as a change.
        level_4 = rec.get("level_4", "")
        if level_4:
            old_a, old_m = 0.0, [0.0] * N_MONTHS
        else:
            old_a, old_m = _current_value(grid_index, line_code=lc)
        _emit(lc, level_4, "", old_a, old_m, rec["annual"], rec["months"])

    for rec in parsed.get("partners", []):
        lc = rec["line_code"]
        if known is not None and lc not in known:
            unknown.add(lc)
            continue
        pid = rec.get("partner_id", "")
        if pid == budget_service.OTHER_PARTNER_ID:
            continue  # Other is server-derived; never diffed/written from the sheet
        old_a, old_m = _current_value(grid_index, line_code=lc, partner_id=pid)
        _emit(lc, "", pid, old_a, old_m, rec["annual"], rec["months"])

    return {
        "changes": changes,
        "unknown_line_codes": sorted(unknown),
        "summary": {
            "changed_cells": len(changes),
            "changed_positions": len({(c["line_code"], c["level_4"], c["partner_id"]) for c in changes}),
        },
    }


# =========================================================================== #
# Commit
# =========================================================================== #
def commit_records(
    session: Session,
    parsed: dict[str, list[dict[str, Any]]],
    *,
    statement: str,
    entity: str,
    fiscal_year: int,
    known_line_codes: Iterable[str],
    updated_by: Optional[str] = None,
) -> dict[str, int]:
    """Write parsed records into ``fact_position_plan`` via ``patch_position``.

    Months are committed as ABSOLUTE presented values (the months in the sheet).
    Each position is written with its named partners (Partners sheet, excluding the
    server-derived ``Other``) so the XOR / roll-up logic in
    :func:`budget_service.patch_position` reconciles Σ(partners)+Other == position.
    L4 rows from the Positions sheet are written as L4 cells (``level_4`` set),
    which XOR-clears the L3 row for that scope per the service's guard.

    Rejects unknown line_codes and non-finite months (defense in depth — the parse
    already coerced).  Returns ``{"rows_written": n}``.
    """
    known = set(known_line_codes)
    # Group named partners by line_code (skip Other / sentinel).
    partners_by_lc: dict[str, list[dict[str, Any]]] = {}
    for rec in parsed.get("partners", []):
        lc = rec["line_code"]
        pid = rec.get("partner_id", "")
        if not pid or pid == budget_service.OTHER_PARTNER_ID:
            continue
        if lc not in known:
            raise TemplateError(f"unknown line_code {lc!r} in Partners sheet")
        _assert_finite(rec["months"], where=f"partner {pid}")
        partners_by_lc.setdefault(lc, []).append({
            "partner_id": pid,
            "months": [float(m) for m in rec["months"]],
        })

    rows_written = 0
    # Track which line_codes were written as L3 (so their partners ride along).
    for rec in parsed.get("positions", []):
        lc = rec["line_code"]
        if lc not in known:
            raise TemplateError(f"unknown line_code {lc!r} in Positions sheet")
        level_4 = rec.get("level_4", "")
        _assert_finite(rec["months"], where=f"position {lc} L4={level_4!r}")
        # Attach partners only to the L3 row (level_4 == '') of a partner-driven line.
        partners = partners_by_lc.pop(lc, None) if not level_4 else None
        res = budget_service.patch_position(
            session, statement=statement, line_code=lc, entity=(entity or ""),
            fiscal_year=fiscal_year,
            position={"months": [float(m) for m in rec["months"]]},
            partners=partners, level_4=(level_4 or ""), updated_by=updated_by,
        )
        rows_written += int(res.get("rows_upserted", 0))

    # Any partner groups whose L3 position row was NOT in the Positions sheet still
    # need writing (partner-only edit); write them with no position override.
    for lc, partners in partners_by_lc.items():
        res = budget_service.patch_position(
            session, statement=statement, line_code=lc, entity=(entity or ""),
            fiscal_year=fiscal_year, position=None, partners=partners,
            updated_by=updated_by,
        )
        rows_written += int(res.get("rows_upserted", 0))

    return {"rows_written": rows_written}


def _assert_finite(months: list[float], *, where: str) -> None:
    for i, m in enumerate(months):
        if not math.isfinite(float(m)):
            raise TemplateError(f"non-finite month value at {where} ({MONTH_LABELS[i] if i < N_MONTHS else i})")
