"""D3 DRAFT — Anlagenregister (fixed-asset register) ingest API.

Prefix: /api/v1/anlagen

Endpoints (mirror the GL ingest shape):
  POST /upload    Upload a register file; return sheets/columns/sample preview.
  POST /preview   Re-read a staged file (pick a sheet) -> columns/sample.
  POST /commit    Map source columns -> fact_fixed_asset target fields and store
                  PASS-THROUGH rows. NO roll-forward / depreciation is computed.

DRAFT — see ``docs/financial-logic.md`` (F1 closing-cost roll-forward, F2
accumulated depreciation / NBV).  This router stores raw values only; no derived
column is written and there is no calc endpoint.

Design rules (CLAUDE.md): thin router, explicit Pydantic models, no path
traversal (UPLOAD_DIR + UUID), admin-gated + entity-scoped writes.
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import User, current_user, require_admin
from app.db import get_session
from app.services import draft_ingest as di
from app.services.fixed_asset_ingest import as_of_date_for_year

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/anlagen", tags=["anlagen"])

# Target field -> kind. Identity/text fields are pass-through strings; the rest are
# coerced for their DB column type. NO derived/closing/accum-depreciation field.
_NUMBER_FIELDS = {
    "opening_cost_ahk", "opening_nbv", "additions_zugang", "disposals_abgang",
    "transfers_umbuchung", "depreciation", "nbv",
}
_DATE_FIELDS = {"capitalization_date"}
_TEXT_FIELDS = {"asset_id", "asset_sub_no", "asset_class", "asset_label", "segment", "bilanzposition"}
_TARGET_FIELDS = _NUMBER_FIELDS | _DATE_FIELDS | _TEXT_FIELDS

_INSERT_COLUMNS = [
    "project_id", "dataset_version_id", "source_file_id", "row_no",
    "as_of_date", "entity_prefix", "fy_label",
    "asset_id", "asset_sub_no", "asset_class", "asset_label", "segment", "bilanzposition",
    "capitalization_date",
    "opening_cost_ahk", "opening_nbv", "additions_zugang", "disposals_abgang",
    "transfers_umbuchung", "depreciation", "nbv",
]


def _fy_end_year(fy_label: str) -> int:
    """Derive the ending 4-digit calendar year from a FY label.

    The stored fiscal year is always the calendar ENDING year, so:
      'FY2024' -> 2024; 'FY23/24' -> 2024; 'FY24A' -> 2024.
    The last numeric group in the label is the ending year (2-digit -> 2000+).
    """
    groups = re.findall(r"\d+", fy_label or "")
    if not groups:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot derive fiscal year from fy_label {fy_label!r}",
        )
    year = int(groups[-1])
    if year < 100:
        year += 2000
    return year


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class AnlagenUploadResponse(BaseModel):
    file_id: str
    filename: str
    sheets: list[str] = []
    columns: list[str]
    sample: list[dict]


class AnlagenPreviewRequest(BaseModel):
    file_id: str
    sheet: str | None = None


class AnlagenPreviewResponse(BaseModel):
    file_id: str
    sheets: list[str] = []
    sheet: str | None = None
    columns: list[str]
    sample: list[dict]


class AnlagenCommitRequest(BaseModel):
    file_id: str
    sheet: str | None = None
    fy_label: str = Field(..., max_length=20)
    # entity source mode: 'per_entity' (inject entity_prefix) | 'combined' (entity_column)
    entity_mode: str = "per_entity"
    entity_prefix: str | None = Field(None, max_length=2)
    entity_column: str | None = None
    project_id: str = Field("default", max_length=64)
    # target field -> source column header
    column_map: dict[str, str] = Field(default_factory=dict)


class AnlagenCommitResponse(BaseModel):
    inserted: int
    fy_label: str
    entity_prefixes: list[str]
    project_id: str


# --------------------------------------------------------------------------- #
# POST /upload
# --------------------------------------------------------------------------- #
@router.post("/upload", response_model=AnlagenUploadResponse)
async def upload(
    file: UploadFile = File(...),
    _user: User = Depends(current_user),
) -> AnlagenUploadResponse:
    """Stage a fixed-asset register file; return a sheet/column preview."""
    return AnlagenUploadResponse(**await di.save_upload(file))


# --------------------------------------------------------------------------- #
# POST /preview
# --------------------------------------------------------------------------- #
@router.post("/preview", response_model=AnlagenPreviewResponse)
def preview(
    body: AnlagenPreviewRequest,
    _user: User = Depends(current_user),
) -> AnlagenPreviewResponse:
    """Read a staged register file's sheet/columns/sample (no DB write)."""
    return AnlagenPreviewResponse(**di.preview(body.file_id, body.sheet))


# --------------------------------------------------------------------------- #
# POST /commit
# --------------------------------------------------------------------------- #
@router.post("/commit", response_model=AnlagenCommitResponse)
def commit(
    body: AnlagenCommitRequest,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
) -> AnlagenCommitResponse:
    """Store PASS-THROUGH register rows into fact_fixed_asset. No computation."""
    column_map = {k: v for k, v in body.column_map.items() if k in _TARGET_FIELDS}
    if not column_map:
        raise HTTPException(
            status_code=422,
            detail=f"column_map must map at least one of: {sorted(_TARGET_FIELDS)}",
        )

    # Snapshot date the rollforward report filters on (as_of_date = Dec-31 of the
    # fiscal-year ENDING year). build_mapped_rows has no source row in its
    # extra_per_row hook, so inject the request-derived date on every row.
    as_of = as_of_date_for_year(_fy_end_year(body.fy_label))

    df = di.load_staged(body.file_id, body.sheet)
    rows, prefixes = di.build_mapped_rows(
        df,
        column_map=column_map,
        number_fields=_NUMBER_FIELDS,
        date_fields=_DATE_FIELDS,
        entity_mode=body.entity_mode,
        entity_column=body.entity_column,
        entity_prefix=body.entity_prefix,
        fy_label=body.fy_label,
        file_id=body.file_id,
        extra_per_row=lambda out, _prefix: out.__setitem__("as_of_date", as_of),
    )

    di.assert_prefixes_visible(session, admin, prefixes)
    di.assert_entities_resolved(session, admin, rows, entity_column=body.entity_column)

    if not rows:
        return AnlagenCommitResponse(
            inserted=0, fy_label=body.fy_label,
            entity_prefixes=sorted(p for p in prefixes if p), project_id=body.project_id,
        )

    cols = list(_INSERT_COLUMNS)
    placeholders = ", ".join(f":{c}" for c in cols)
    insert_sql = text(
        f"INSERT INTO fact_fixed_asset ({', '.join(cols)}) VALUES ({placeholders})"
    )
    params = []
    for r in rows:
        rec = {c: r.get(c) for c in cols}
        rec["project_id"] = body.project_id
        params.append(rec)

    try:
        session.execute(insert_sql, params)
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.exception("anlagen commit failed (file_id=%s)", body.file_id)
        raise HTTPException(
            status_code=500, detail="Fixed-asset commit failed; check server logs."
        ) from exc

    return AnlagenCommitResponse(
        inserted=len(rows),
        fy_label=body.fy_label,
        entity_prefixes=sorted(p for p in prefixes if p),
        project_id=body.project_id,
    )
