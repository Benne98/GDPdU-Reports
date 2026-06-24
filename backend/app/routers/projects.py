"""Per-project config persistence API (reporting-v2 Phase 7).

Prefix: /api/v1/projects

Endpoints:
  GET  /api/v1/projects/{project_id}          Read a project's config (default 'default').
  PUT  /api/v1/projects/{project_id}          Upsert a project's identity + config.
  POST /api/v1/projects/{project_id}/rebuild  Trigger a full deterministic rebuild
                                              for the project scope (admin-guarded).

The persisted config is the no-code wizard's source of truth: it drives the
rebuild flags (opening_balance_mode, net_profit_source) so a one-time setup is
reused on every update.

Design rules (CLAUDE.md):
  - Thin router; persistence + flag logic live in ``etl.project_config``.
  - Explicit Pydantic models; auth/session pattern follows financials_compat.py.
  - Additive: these tables/endpoints are never read by existing endpoints, so the
    golden live-vs-v2 equivalence is preserved.
"""
from __future__ import annotations

import io
import json
import logging
import sys
from pathlib import Path
from typing import Annotated, Any, Optional

# etl/ lives at the repo root, one level above backend/ — mirror ingest.py so
# `etl` is importable when launched from backend/.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import coa_template
from app.auth import User, current_user, require_admin
from app.config import settings
from app.db import get_session
from app.services.fin_compat_sql import resolve_entity_prefix
from etl.project_config import (
    DEFAULT_CONFIG,
    VALID_NET_PROFIT_SOURCES,
    VALID_OPENING_BALANCE_MODES,
    read_project_config,
    upsert_project_config,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_MAPPING_LIBRARY_DIR = _REPO_ROOT / "etl" / "mapping_library"
#: Friendly library aliases → JSON filename in etl/mapping_library/.
_LIBRARY_ALIASES: dict[str, str] = {
    "finssentials_standard": "finssentials_standard_v1.json",
    "finssentials_standard_v1": "finssentials_standard_v1.json",
    "skr03": "finssentials_standard_v1.json",
}

_UserDep = Annotated[User, Depends(current_user)]
_AdminDep = Annotated[User, Depends(require_admin)]
_SessionDep = Annotated[Session, Depends(get_session)]


# --------------------------------------------------------------------------- #
# Pydantic models — the frontend wizard contract
# --------------------------------------------------------------------------- #
class EntityConfig(BaseModel):
    code: str = Field(..., max_length=64)
    prefix: str = Field("", max_length=8)
    name: str = Field("", max_length=200)


class ProjectConfig(BaseModel):
    """Wizard answers persisted per project (mirrors etl.project_config.DEFAULT_CONFIG)."""

    entities: list[EntityConfig] = Field(default_factory=list)
    fy_start_month: int = Field(default=1, ge=1, le=12)
    opening_balance_mode: str = Field(default="in_data")
    net_profit_source: str = Field(default="report_inject")
    mapping_source: str = Field(default="library")
    partner_master_source: str = Field(default="files")
    sales_label: str = Field(default="Sales", max_length=200)
    cost_label: str = Field(default="Cost of materials", max_length=200)


class ProjectResponse(BaseModel):
    project_id: str
    name: Optional[str] = None
    fy_start_month: int = 1
    config: ProjectConfig


class ProjectPutRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    fy_start_month: Optional[int] = Field(None, ge=1, le=12)
    entities: Optional[list[EntityConfig]] = None
    opening_balance_mode: Optional[str] = None
    net_profit_source: Optional[str] = None
    mapping_source: Optional[str] = None
    partner_master_source: Optional[str] = None
    sales_label: Optional[str] = None
    cost_label: Optional[str] = None


class RebuildRequest(BaseModel):
    confirm: bool = False


class RebuildResponse(BaseModel):
    project_id: str
    mode: str
    summary: dict[str, Any]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _record_to_response(record: dict[str, Any]) -> ProjectResponse:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(record.get("config") or {})
    return ProjectResponse(
        project_id=record["project_id"],
        name=record.get("name"),
        fy_start_month=int(record.get("fy_start_month") or cfg.get("fy_start_month") or 1),
        config=ProjectConfig(**cfg),
    )


def _load_mapping_library(library: str) -> dict[str, Any]:
    """Load a mapping-library JSON by friendly alias (read-only).

    Raises HTTPException(422) for an unknown / unsafe library name.
    """
    key = (library or "").strip().lower()
    filename = _LIBRARY_ALIASES.get(key)
    if not filename:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown library; choose one of {sorted(set(_LIBRARY_ALIASES))}",
        )
    path = _MAPPING_LIBRARY_DIR / filename
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=422, detail="Mapping library not available") from exc


def _entity_label_for(session: Session, entity: Optional[str]) -> str:
    """Resolve a legal_entity_code to its dim_legal_entity name (best-effort, read-only)."""
    if not entity or entity.strip().lower() in ("", "all"):
        return ""
    from sqlalchemy import text

    row = session.execute(
        text(
            "SELECT entity_name FROM dim_legal_entity "
            "WHERE legal_entity_code = :e LIMIT 1"
        ),
        {"e": entity.strip()},
    ).fetchone()
    return str(row[0]) if row and row[0] else entity.strip()


def _validated_config_from_put(body: ProjectPutRequest) -> dict[str, Any]:
    """Build a merged config dict from the PUT body, validating enum flags."""
    cfg: dict[str, Any] = {}
    if body.entities is not None:
        cfg["entities"] = [e.model_dump() for e in body.entities]
    if body.fy_start_month is not None:
        cfg["fy_start_month"] = body.fy_start_month
    if body.opening_balance_mode is not None:
        if body.opening_balance_mode not in VALID_OPENING_BALANCE_MODES:
            raise HTTPException(
                status_code=422,
                detail=f"opening_balance_mode must be one of {sorted(VALID_OPENING_BALANCE_MODES)}",
            )
        cfg["opening_balance_mode"] = body.opening_balance_mode
    if body.net_profit_source is not None:
        if body.net_profit_source not in VALID_NET_PROFIT_SOURCES:
            raise HTTPException(
                status_code=422,
                detail=f"net_profit_source must be one of {sorted(VALID_NET_PROFIT_SOURCES)}",
            )
        cfg["net_profit_source"] = body.net_profit_source
    if body.mapping_source is not None:
        cfg["mapping_source"] = body.mapping_source
    if body.partner_master_source is not None:
        cfg["partner_master_source"] = body.partner_master_source
    if body.sales_label is not None:
        cfg["sales_label"] = body.sales_label
    if body.cost_label is not None:
        cfg["cost_label"] = body.cost_label
    return cfg


# --------------------------------------------------------------------------- #
# GET /api/v1/projects/coa-template  (CoA Master L1-L4 template download)
#
# Declared BEFORE the "/{project_id}" route so the static path wins (FastAPI
# matches in declaration order; otherwise "coa-template" reads as a project_id).
# --------------------------------------------------------------------------- #
@router.get("/coa-template")
def download_coa_template(
    _user: _UserDep,
    session: _SessionDep,
    entity: Optional[str] = Query(None, description="legal_entity_code; '' / 'all' = all entities"),
    fiscal_year: Optional[int] = Query(None, ge=2000, le=2100),
    library: Optional[str] = Query(None, description="Mapping library alias for an empty-DB starting suggestion"),
) -> StreamingResponse:
    """Stream a CoA Master template (.xlsx) the user fills and re-uploads.

    Two sheets (Master_BS, Master_PL) with the EXACT bs_pl_master column schema so
    the file round-trips through POST /ingest/mapping/commit?format=bs_pl_master.

    Prefill precedence (READ-ONLY — never writes):
      1. existing dim_gl_account rows for (entity, fiscal_year), else
      2. a mapping library (when ``library`` is given), else
      3. an empty template with example rows.
    """
    try:
        entity_prefix = resolve_entity_prefix(session, entity)
        entity_label = _entity_label_for(session, entity)

        rows: list[coa_template.CoaRow] = []
        if fiscal_year is not None:
            rows = coa_template.rows_from_dim_gl_account(
                session, entity_prefix, int(fiscal_year)
            )
        if not rows and library:
            lib = _load_mapping_library(library)
            rows = coa_template.rows_from_library(lib, entity_label)
        if not rows:
            rows = coa_template.empty_template_rows(entity_label)

        wb = coa_template.build_coa_template_workbook(rows)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("download_coa_template error")
        raise HTTPException(
            status_code=500,
            detail="Failed to build CoA template. Check server logs.",
        ) from exc

    scope = entity.strip() if (entity and entity.strip().lower() not in ("", "all")) else "all"
    fy_tag = str(fiscal_year) if fiscal_year is not None else "all"
    filename = f"coa_master_template_{scope}_{fy_tag}.xlsx"
    return StreamingResponse(
        buf,
        media_type=_XLSX_MEDIA,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --------------------------------------------------------------------------- #
# GET /api/v1/projects/{project_id}
# --------------------------------------------------------------------------- #
@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: str,
    _user: _UserDep,
    session: _SessionDep,
) -> ProjectResponse:
    """Return a project's persisted config (synthetic default when absent)."""
    try:
        record = read_project_config(session, project_id)
    except Exception as exc:
        logger.exception("get_project error")
        raise HTTPException(status_code=500, detail="Failed to read project config") from exc
    return _record_to_response(record)


# --------------------------------------------------------------------------- #
# PUT /api/v1/projects/{project_id}
# --------------------------------------------------------------------------- #
@router.put("/{project_id}", response_model=ProjectResponse)
def put_project(
    project_id: str,
    body: ProjectPutRequest,
    _admin: _AdminDep,
    session: _SessionDep,
) -> ProjectResponse:
    """Upsert a project's identity + config blob (admin only)."""
    cfg = _validated_config_from_put(body)
    # Merge onto the existing stored config so a partial PUT keeps untouched fields.
    existing = read_project_config(session, project_id).get("config") or {}
    merged = dict(DEFAULT_CONFIG)
    merged.update(existing)
    merged.update(cfg)
    # fy_start_month from the dim_project column takes precedence unless overridden.
    if body.fy_start_month is not None:
        merged["fy_start_month"] = body.fy_start_month

    try:
        record = upsert_project_config(
            session, project_id, name=body.name, config=merged
        )
    except RuntimeError as exc:
        session.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        session.rollback()
        logger.exception("put_project error")
        raise HTTPException(status_code=500, detail="Failed to persist project config") from exc
    return _record_to_response(record)


# --------------------------------------------------------------------------- #
# POST /api/v1/projects/{project_id}/rebuild
# --------------------------------------------------------------------------- #
@router.post("/{project_id}/rebuild", response_model=RebuildResponse)
def rebuild_project_endpoint(
    project_id: str,
    body: RebuildRequest,
    _admin: _AdminDep,
    session: _SessionDep,
) -> RebuildResponse:
    """Trigger a FULL deterministic rebuild for the project scope (admin-guarded).

    The project's persisted config drives the rebuild flags so the setup is reused.
    Gated by ``settings.rebuild_on_commit`` (default OFF → 503) so the live 5176
    stack — where the rebuild is intentionally disabled — never runs a rebuild.
    """
    if not body.confirm:
        raise HTTPException(status_code=422, detail="Set confirm=true to trigger a rebuild")
    if not settings.rebuild_on_commit:
        raise HTTPException(
            status_code=503,
            detail="Rebuild is disabled on this stack (settings.rebuild_on_commit is OFF)",
        )

    try:
        from etl.rebuild import rebuild_project

        # Full rebuild over the whole project scope (scope=None => global, the
        # Phase-1 derive-facts behaviour).  Flags resolved from project config.
        summary = rebuild_project(session, scope=None, mode="full", project_id=project_id)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        session.rollback()
        logger.exception("rebuild_project_endpoint error")
        raise HTTPException(
            status_code=500,
            detail="Rebuild failed; transaction rolled back. Check server logs.",
        ) from exc

    return RebuildResponse(project_id=project_id, mode="full", summary=summary)
