"""Workstream 4 — Project-Setup FTE/Payroll commit into ``fact_personnel_employee``.

Prefix: /api/v1/personnel  (co-located with the read-side ``personnel`` router)

Endpoint:
  POST /commit   Map the wizard's generic FTE/payroll column selection onto the
                 fixed ``fact_personnel_employee`` schema and DB-ingest the rows
                 the Payroll page reads.  Models the Anlagen inline commit 1:1:
                 generic ``column_map`` + ``entity_mode`` + ``fy_label`` ->
                 ``build_mapped_rows`` -> visibility gates -> idempotent replace.

Unlike the Anlagen/OPOS DRAFT ingests the source files live in the FDD session
store (uploaded via ``uploadFddFile``), NOT the draft-ingest staged store — so
bytes are resolved via ``fdd_bot._file_path_for_id``, not ``draft_ingest.load_staged``.

Design rules (CLAUDE.md): thin router, explicit Pydantic models, admin-gated +
entity-scoped writes, PRESERVE source sign (the Payroll aggregate negates).
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import User, require_admin
from app.db import get_session
from app.services import draft_ingest as di
from app.services.personnel_ingest import (
    _INSERT_COLUMNS,
    as_of_date_for_year,
    build_personnel_rows,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/personnel", tags=["personnel"])

_CHUNK = 500
_EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls"}
# Lineage/scope columns build_personnel_rows/inject always populate.
_BASE_COLUMNS = {
    "project_id", "dataset_version_id", "source_file_id", "row_no",
    "as_of_date", "entity_prefix", "entity_name", "fy_label", "personalnummer",
}


class PersonnelCommitRequest(BaseModel):
    session_id: str
    file_ids: list[str]
    entity_mode: str = "per_entity"
    entity_prefix: Optional[str] = Field(None, max_length=2)
    entity_column: Optional[str] = None
    year: int
    fy_label: str = Field(..., max_length=20)
    project_id: str = Field("default", max_length=64)
    tenure_mode: str
    payroll_mode: str
    employment_pct_col: Optional[str] = None
    months_col: Optional[str] = None
    entry_col: Optional[str] = None
    exit_col: Optional[str] = None
    total_col: Optional[str] = None
    monthly_col: Optional[str] = None
    component_cols: Optional[list[str]] = None
    social_col: Optional[str] = None
    personalnummer_col: Optional[str] = None
    bereich_col: Optional[str] = None
    bereichuntergruppe_col: Optional[str] = None
    kst_name_col: Optional[str] = None
    gew_ang_col: Optional[str] = None


class PersonnelCommitResponse(BaseModel):
    inserted: int
    as_of_date: str
    entity_prefix: Optional[str]
    project_id: str


def _read_df(path: Path) -> pd.DataFrame:
    """Read a staged FTE workbook/CSV into a DataFrame (first sheet for Excel)."""
    if path.suffix.lower() in _EXCEL_SUFFIXES:
        return pd.read_excel(path, sheet_name=0)
    return pd.read_csv(path, sep=None, engine="python")


def _name_by_prefix(session: Session) -> dict[str, str]:
    rows = session.execute(
        text("SELECT entity_prefix, entity_name FROM dim_legal_entity")
    ).fetchall()
    return {str(r[0]).strip(): r[1] for r in rows if r and r[0] is not None}


@router.post("/commit", response_model=PersonnelCommitResponse)
def commit_personnel(
    body: PersonnelCommitRequest,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
) -> PersonnelCommitResponse:
    """Ingest wizard FTE/payroll rows into fact_personnel_employee (idempotent)."""
    # Lazy import: fdd_bot pulls a heavy dependency tree; keep module import light.
    from app.routers.fdd_bot import _file_path_for_id

    as_of = as_of_date_for_year(body.year)
    names = _name_by_prefix(session)

    all_rows: list[dict] = []
    all_prefixes: set[str] = set()
    mapped_keys: set[str] = set()
    for fid in body.file_ids:
        path = _file_path_for_id(body.session_id, fid)
        if path is None:
            raise HTTPException(
                status_code=404,
                detail=f"file_id {fid!r} not found in session {body.session_id!r}",
            )
        try:
            df = _read_df(path)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=422, detail=f"Could not parse file {fid!r}: {exc}") from exc

        try:
            rows, prefixes, column_map = build_personnel_rows(
                df,
                year=body.year,
                fy_label=body.fy_label,
                file_id=fid,
                tenure_mode=body.tenure_mode,
                payroll_mode=body.payroll_mode,
                entity_mode=body.entity_mode,
                entity_prefix=body.entity_prefix,
                entity_column=body.entity_column,
                employment_pct_col=body.employment_pct_col,
                months_col=body.months_col,
                entry_col=body.entry_col,
                exit_col=body.exit_col,
                total_col=body.total_col,
                monthly_col=body.monthly_col,
                component_cols=body.component_cols,
                social_col=body.social_col,
                personalnummer_col=body.personalnummer_col,
                bereich_col=body.bereich_col,
                bereichuntergruppe_col=body.bereichuntergruppe_col,
                kst_name_col=body.kst_name_col,
                gew_ang_col=body.gew_ang_col,
                name_by_prefix=names,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        all_rows.extend(rows)
        all_prefixes |= prefixes
        mapped_keys |= set(column_map.keys())

    di.assert_prefixes_visible(session, admin, all_prefixes)
    di.assert_entities_resolved(session, admin, all_rows, entity_column=body.entity_column)

    if not all_rows:
        return PersonnelCommitResponse(
            inserted=0, as_of_date=as_of.isoformat(),
            entity_prefix=body.entity_prefix, project_id=body.project_id,
        )

    # Insert only the columns actually populated (subset of the offline loader's).
    populated = _BASE_COLUMNS | mapped_keys
    cols = [c for c in _INSERT_COLUMNS if c in populated]
    placeholders = ", ".join(f":{c}" for c in cols)
    insert_sql = text(
        f"INSERT INTO fact_personnel_employee ({', '.join(cols)}) VALUES ({placeholders})"
    )
    params = []
    for r in all_rows:
        rec = {c: r.get(c) for c in cols}
        rec["project_id"] = body.project_id
        params.append(rec)

    distinct_prefixes = {r.get("entity_prefix") for r in all_rows}
    try:
        # Idempotent replace (mirrors load_personnel_subledger.py) scoped per prefix.
        for ep in distinct_prefixes:
            session.execute(
                text(
                    "DELETE FROM fact_personnel_employee "
                    "WHERE project_id = :pid AND as_of_date = :as_of "
                    "AND entity_prefix IS NOT DISTINCT FROM :ep"
                ),
                {"pid": body.project_id, "as_of": as_of, "ep": ep},
            )
        for i in range(0, len(params), _CHUNK):
            session.execute(insert_sql, params[i:i + _CHUNK])
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.exception("personnel commit failed (session=%s)", body.session_id)
        raise HTTPException(
            status_code=500, detail="Personnel commit failed; check server logs."
        ) from exc

    ep_out = body.entity_prefix
    if len(distinct_prefixes) == 1:
        ep_out = next(iter(distinct_prefixes))
    return PersonnelCommitResponse(
        inserted=len(all_rows),
        as_of_date=as_of.isoformat(),
        entity_prefix=ep_out,
        project_id=body.project_id,
    )
