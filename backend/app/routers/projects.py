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
    VALID_ACCOUNT_MAPPING_MODES,
    VALID_NET_PROFIT_SOURCES,
    VALID_OPENING_BALANCE_MODES,
    read_project_config,
    upsert_project_config,
    upsert_project_entities,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
#: Mapping-library alias whitelist + loader now live in app.coa_template
#: (single source of truth, shared with the ingest apply-library endpoint).

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
    account_mapping_mode: str = Field(default="library")


class ProjectResponse(BaseModel):
    project_id: str
    name: Optional[str] = None
    fy_start_month: int = 1
    config: ProjectConfig
    #: Capability flag for the frontend: True only when the data-reset env flag is
    #: ON *and* the connected DB is NOT the live "Finssentials" DB.  The frontend
    #: uses this to decide whether to render the destructive "reset data" control.
    data_reset_allowed: bool = False


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
    account_mapping_mode: Optional[str] = None


class RebuildRequest(BaseModel):
    confirm: bool = False


class RebuildResponse(BaseModel):
    project_id: str
    mode: str
    summary: dict[str, Any]


class ResetDataRequest(BaseModel):
    confirm: bool = False


class ResetDataResponse(BaseModel):
    deleted: dict[str, int]
    database: str


# --------------------------------------------------------------------------- #
# Data-reset configuration (destructive — be conservative & explicit)
# --------------------------------------------------------------------------- #
#: The LIVE production database name.  The reset endpoint HARD-refuses against
#: this DB regardless of ALLOW_DATA_RESET (case-insensitive exact match).
LIVE_DB_NAME = "Finssentials"

#: PROJECT / INGESTED data tables to EMPTY on reset, in FK-safe (child→parent)
#: order.  Verified against the finssentials_v4 schema + the FK graph
#: (information_schema) so a single-pass DELETE never trips a constraint.
#: Resilient: a table missing on an older schema version is skipped + logged.
RESET_EMPTY_TABLES: tuple[str, ...] = (
    # ── snapshots (FK → org_meta_dataset_load, ON DELETE CASCADE; delete first) ──
    "snap_fact_gl_line",
    "snap_fact_gl_entry",
    "snap_dim_gl_account",
    "snap_dim_gl_na",
    "snap_dim_gl_cf",
    "anomaly_analysis_snapshot",
    "compat_narrative_snapshot",
    # ── derived / sub-ledger facts (FK → fact_gl_line) ──
    "fact_ar",
    "fact_ap",
    "fact_sales",
    "fact_com",
    "fact_anomaly",
    "fact_gl_counter_cooccurrence",
    # ── manual / plan facts (no inbound FKs) ──
    "fact_gl_plan",
    "fact_sales_plan",
    "fact_com_plan",
    "fact_position_plan",
    # ── GL facts (fact_gl_line → fact_gl_entry; delete line first) ──
    "fact_gl_line",
    "fact_gl_entry",
    # ── per-project CoA override (project-specific) ──
    "dim_project_coa_override",
    # ── account-mapping overrides (project-specific pins) ──
    "ovr_account_mapping",
    "ovr_na_mapping",
    # ── GL account dims (dim_gl_na / dim_gl_cf FK → dim_gl_account; delete first) ──
    "dim_gl_na",
    "dim_gl_cf",
    "dim_gl_account",
    # ── partner masters ──
    "dim_customer",
    "dim_supplier",
    # ── project legal entities ──
    #   NOTE: admin_role_entity_visibility has an FK → dim_legal_entity with
    #   ON DELETE CASCADE.  Deleting an ingested entity therefore auto-removes
    #   the visibility rows that point AT that entity (you cannot keep a rule
    #   targeting a deleted entity).  We do NOT delete admin_role_entity_visibility
    #   directly — the cascade only prunes rows tied to now-gone project data,
    #   leaving the admin table itself (and any non-entity-scoped rows) intact.
    "dim_legal_entity",
    # ── ingestion bookkeeping (org_meta_dataset_load self-FKs handled by a
    #    single unscoped DELETE inside one transaction) ──
    "org_ingest_mapping_profile",
    "org_meta_dataset_load",
)

#: Tables that must NEVER be touched (documented for the reviewer / tests).
#: Libraries, admin/auth, app workspace, and STRUCTURAL reference dims.
RESET_KEEP_TABLES: tuple[str, ...] = (
    # libraries (reusable across projects)
    "lib_account_mapping",
    "lib_cf_mapping",
    "lib_na_mapping",
    # admin config + project identity
    "admin_role_page_visibility",
    "admin_project_config",
    "dim_project",
    # auth
    "dim_user",
    "dim_role",
    "user_role",
    "auth_session",
    # structural / reference dims + presentation templates
    "dim_pl_structure",
    "dim_bs_structure",
    "dim_cf_structure",
    "dim_pl_recon_mapping",
    "dim_bs_recon_mapping",
    "dim_country",
    "dim_region",
    "dim_document_type",
    "dim_org_role",
    "dim_internal_contact",
    # app workspace (user notes — not ingested data)
    "app_note_session",
    "app_note",
    "app_note_pin",
    "app_email_draft",
    "app_action_board",
    "alembic_version",
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _current_database_name(session: Session) -> str:
    """Return the connected PostgreSQL database name (authoritative, from the live
    connection — not the config string).  Best-effort: falls back to the engine
    URL database, else "" so callers fail CLOSED (treated as possibly-live)."""
    from sqlalchemy import text

    try:
        row = session.execute(text("SELECT current_database()")).fetchone()
        if row and row[0]:
            return str(row[0])
    except Exception:  # noqa: BLE001
        logger.warning("current_database() lookup failed; falling back to engine URL")
    try:
        return str(session.get_bind().url.database or "")
    except Exception:  # noqa: BLE001
        return ""


def _is_live_database(session: Session) -> bool:
    """True when the connected DB is the LIVE production DB (case-insensitive).

    Fails CLOSED: an unknown/empty DB name is treated as NOT-live ONLY for the
    capability flag, but the reset endpoint additionally requires the explicit
    env flag, so an empty name can never enable a destructive reset on its own.
    """
    return _current_database_name(session).strip().casefold() == LIVE_DB_NAME.casefold()


def _data_reset_allowed(session: Session) -> bool:
    """Capability flag surfaced to the frontend: env flag ON *and* not the live DB."""
    return bool(settings.allow_data_reset) and not _is_live_database(session)


def _record_to_response(
    record: dict[str, Any], *, data_reset_allowed: bool = False
) -> ProjectResponse:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(record.get("config") or {})
    return ProjectResponse(
        project_id=record["project_id"],
        name=record.get("name"),
        fy_start_month=int(record.get("fy_start_month") or cfg.get("fy_start_month") or 1),
        config=ProjectConfig(**cfg),
        data_reset_allowed=data_reset_allowed,
    )


def _load_mapping_library(library: str) -> dict[str, Any]:
    """Load a mapping-library JSON by friendly alias (read-only).

    Delegates to ``coa_template.load_mapping_library`` so the alias whitelist and
    loader live in ONE place (shared with the ingest apply-library endpoint).
    Raises HTTPException(422) for an unknown / unsafe library name.
    """
    return coa_template.load_mapping_library(library)


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
    if body.account_mapping_mode is not None:
        if body.account_mapping_mode not in VALID_ACCOUNT_MAPPING_MODES:
            raise HTTPException(
                status_code=422,
                detail=f"account_mapping_mode must be one of {sorted(VALID_ACCOUNT_MAPPING_MODES)}",
            )
        cfg["account_mapping_mode"] = body.account_mapping_mode
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
    statement: Optional[str] = Query(None, description="'bs' or 'pl' for a single-sheet template; omit for both"),
) -> StreamingResponse:
    """Stream a CoA Master template (.xlsx) the user fills and re-uploads.

    Two sheets (Master_BS, Master_PL) with the EXACT bs_pl_master column schema so
    the file round-trips through POST /ingest/mapping/commit?format=bs_pl_master.

    Prefill precedence (READ-ONLY — never writes):
      1. existing dim_gl_account rows for (entity, fiscal_year), else
      2. a mapping library (when ``library`` is given), else
      3. an empty template with example rows.
    """
    stmt = (statement or "").strip().lower() or None
    if stmt is not None and stmt not in ("bs", "pl"):
        raise HTTPException(status_code=422, detail="statement must be 'bs' or 'pl'")

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

        # Single-sheet template: keep only the prefill rows for that statement.
        if stmt == "bs":
            rows = [r for r in rows if not r.is_pl()]
        elif stmt == "pl":
            rows = [r for r in rows if r.is_pl()]

        wb = coa_template.build_coa_template_workbook(rows, statement=stmt)
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
    stmt_tag = f"_{stmt}" if stmt in ("bs", "pl") else ""
    filename = f"coa_master_template_{scope}_{fy_tag}{stmt_tag}.xlsx"
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
    return _record_to_response(record, data_reset_allowed=_data_reset_allowed(session))


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
        # Phase-2 ordering fix: create the project's legal entities in
        # dim_legal_entity up front so the later CoA (bs_pl_master) commit can
        # resolve entity references on a fresh project (config → entities → CoA → GL).
        # Same transaction as the config upsert (commit=True commits both atomically).
        upsert_project_entities(session, merged.get("entities"))
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
    return _record_to_response(record, data_reset_allowed=_data_reset_allowed(session))


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


# --------------------------------------------------------------------------- #
# POST /api/v1/projects/{project_id}/reset-data
# --------------------------------------------------------------------------- #
@router.post("/{project_id}/reset-data", response_model=ResetDataResponse)
def reset_project_data_endpoint(
    project_id: str,
    body: ResetDataRequest,
    _admin: _AdminDep,
    session: _SessionDep,
) -> ResetDataResponse:
    """Reset a project to the BLANK baseline: empty ALL ingested / project data
    while keeping libraries, admin/auth, and structural reference dims intact.

    DESTRUCTIVE — guarded by two INDEPENDENT layers (admin-only on top):

      1. Env flag: 403 unless ``settings.allow_data_reset`` (ALLOW_DATA_RESET).
      2. Live-DB hard refuse: 403 whenever the connected DB is the live
         "Finssentials" DB, regardless of the env flag (belt-and-suspenders so
         the shared frontend can NEVER wipe production even if misconfigured).

    Plus an explicit ``{"confirm": true}`` body (422 otherwise).

    Deletes run as ORM-issued, parameterless ``DELETE FROM`` statements in
    FK-safe order inside ONE transaction; a table missing on an older schema is
    skipped and logged.  Project_id is currently global-scope (single-tenant);
    it is accepted for forward compatibility and surfaced in logs.
    """
    from sqlalchemy import text

    # ── Layer 1: env flag ──────────────────────────────────────────────────
    if not settings.allow_data_reset:
        raise HTTPException(
            status_code=403,
            detail="Data reset is disabled on this stack (settings.allow_data_reset is OFF)",
        )
    # ── Layer 2: live-DB hard refuse (independent of the env flag) ──────────
    dbname = _current_database_name(session)
    if dbname.strip().casefold() == LIVE_DB_NAME.casefold():
        logger.warning(
            "REFUSED data reset against the LIVE database %r (project_id=%s)",
            dbname,
            project_id,
        )
        raise HTTPException(
            status_code=403,
            detail="Refusing to reset data: connected to the live production database",
        )
    # ── Explicit confirm ───────────────────────────────────────────────────
    if not body.confirm:
        raise HTTPException(status_code=422, detail="Set confirm=true to reset all data")

    logger.warning(
        "Admin %s requested data reset on DB %r (project_id=%s) — emptying %d tables",
        getattr(_admin, "email", "?"),
        dbname,
        project_id,
        len(RESET_EMPTY_TABLES),
    )

    # Pre-filter to tables that actually exist on THIS schema version, so a table
    # missing on an older schema is skipped (logged) without aborting the single
    # transaction below.  pg_tables read is parameterless + read-only.
    present = {
        r[0]
        for r in session.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        ).fetchall()
    }

    deleted: dict[str, int] = {}
    try:
        for table in RESET_EMPTY_TABLES:
            if table not in present:
                logger.info("reset-data: skipping missing table %r", table)
                continue
            # table names come from a fixed in-code allowlist (never user input).
            result = session.execute(text(f'DELETE FROM "{table}"'))
            deleted[table] = int(result.rowcount or 0)
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.exception("reset_project_data_endpoint error")
        raise HTTPException(
            status_code=500,
            detail="Data reset failed; transaction rolled back. Check server logs.",
        ) from exc

    return ResetDataResponse(deleted=deleted, database=dbname)
