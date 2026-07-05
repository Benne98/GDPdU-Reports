"""Manual-budget CRUD endpoints (Plan/Forecast extension, Phase 4).

Prefix: /api/v1/budget   (admin-only writes via ``require_admin``).

Lets Finssentials staff plan individual BS/PL **reporting positions** and plan
**per debtor/creditor**, persisted under ``scenario='budget'`` in
``fact_position_plan``.  The Phase-3 readers resolve budget → forecast → plan, so
once budget rows exist the statements / top-entities reflect them automatically.

Endpoints
  GET    /api/v1/budget   — editable grid (synthetic seed overlaid by saved
                            budget); PURE-READ, never writes.
  POST   /api/v1/budget/seed     — materialise the synthetic seed (idempotent).
  PUT    /api/v1/budget/cell     — save one cell (months[12] | annual).
  PATCH  /api/v1/budget/position — bulk-save a position + its partners (roll-up).
  DELETE /api/v1/budget          — drop budget rows → revert to forecast/plan.

Design rules (mirrors plan.py / financials_compat.py):
  - thin router; all logic in app.services.budget_service.
  - explicit Pydantic models; DB session via Depends(get_session).
  - no secrets / customer PII in logs; generic 500 detail + server-side logging.
"""
from __future__ import annotations

import io
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.auth import User, current_user, require_admin
from app.db import get_session
from app.services import budget_excel, budget_service
from app.services.entity_visibility import visible_entity_codes
from app.services.fin_compat_sql import resolve_entity_prefix

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/budget", tags=["budget"])

_STATEMENTS = ("PL", "BS")
_LEVELS = ("L3", "L4")

# Excel uploads land under backend/uploads/{file_id}_... (mirrors ingest.py).
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent  # backend/
_UPLOAD_DIR = _BACKEND_DIR / "uploads" / "budget"
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# Budget templates are small (positions + Top-N partners). Cap WELL below the
# 256 MB GL-ingest limit so a single budget upload cannot exhaust memory.
_MAX_UPLOAD_BYTES = 16 * 1024 * 1024  # 16 MiB
# Saved upload TTL: a previewed-but-never-committed file is swept on the next
# upload after this age (best-effort hygiene; /commit deletes its own file).
_UPLOAD_TTL_SECONDS = 6 * 60 * 60  # 6h
# Read the body in chunks so we can abort before buffering an oversized payload.
_UPLOAD_CHUNK = 1024 * 1024  # 1 MiB


# --------------------------------------------------------------------------- #
# Response / request models
# --------------------------------------------------------------------------- #
class BudgetPartner(BaseModel):
    partner_id: str
    name: str
    annual: float
    months: list[float]


class BudgetChild(BaseModel):
    """An L4 sub-position node (PL only; present when level=L4)."""

    level_4: str
    label: str
    annual: float
    months: list[float]


class BudgetPosition(BaseModel):
    """An L3 reporting-position node (mirrors the IS/BS tree)."""

    line_code: str
    label: str
    level_3: str = ""
    annual: float
    months: list[float]
    synthetic_annual: float
    is_partner_driven: bool
    suggestion: Optional[dict[str, Any]] = None
    explanation: Optional[dict[str, Any]] = None
    children: Optional[list[BudgetChild]] = None
    partners: Optional[list[BudgetPartner]] = None
    other: Optional[float] = None


class BudgetGridResponse(BaseModel):
    statement: str
    fiscal_year: int
    entity: str
    top_n: int
    level: str = "L3"
    positions: list[BudgetPosition]


class BudgetEntity(BaseModel):
    code: str
    label: str
    prefix: str


class BudgetEntitiesResponse(BaseModel):
    entities: list[BudgetEntity]
    can_consolidate: bool


class CellRequest(BaseModel):
    # Reject Infinity/NaN at the schema edge (integrity): non-finite amounts would
    # poison the stored fact and any downstream aggregation.
    model_config = ConfigDict(allow_inf_nan=False)

    statement: str
    line_code: str
    entity: str = ""
    partner_id: Optional[str] = None
    partner_kind: Optional[Literal["customer", "supplier"]] = None
    fiscal_year: int
    months: Optional[list[float]] = Field(
        None, min_length=12, max_length=12, description="12 presented monthly values"
    )
    annual: Optional[float] = Field(None, description="annual presented value (seasonalized server-side)")
    weights: Optional[dict[int, float]] = Field(None, description="optional seasonal weights for annual edit")
    level_4: str = Field("", description="L4 sub-position key; '' = L3-level row")


class PositionPatchRequest(BaseModel):
    # Reject Infinity/NaN at the schema edge (integrity). months/annual live inside
    # the position/partners dicts, so finiteness there is enforced service-side too.
    model_config = ConfigDict(allow_inf_nan=False)

    statement: str
    line_code: str
    entity: str = ""
    fiscal_year: int
    position: Optional[dict[str, Any]] = Field(None, description="{months[12]} | {annual}")
    partners: Optional[list[dict[str, Any]]] = Field(None, description="[{partner_id, months|annual}]")
    weights: Optional[dict[int, float]] = None
    level_4: str = Field("", description="L4 sub-position key; '' = L3-level row")


class SeedRequest(BaseModel):
    statement: str
    fiscal_year: int
    entity: str = ""
    top_n: int = Field(20, ge=1, le=500)
    # "Apply Finssentials heuristics": when materialize_suggestion is True the seed
    # writes each position's heuristic suggestion (prior_year / trend_cagr / run_rate
    # with growth_pct) instead of the legacy synthetic months. Defaults reproduce
    # the legacy seed byte-for-byte (materialize_suggestion=False, prior_year, 0.0).
    materialize_suggestion: bool = False
    heuristic: Literal["prior_year", "trend_cagr", "run_rate"] = "prior_year"
    growth_pct: float = Field(0.0, description="growth assumption for the suggestion")


class WriteResponse(BaseModel):
    ok: bool = True
    rows_upserted: int = 0
    rows_seeded: int = 0
    deleted: int = 0


class BudgetUploadResponse(BaseModel):
    """Preview-diff result of an upload (NO write)."""

    file_id: str
    statement: str
    fiscal_year: int
    entity: str
    changes: list[dict[str, Any]]
    unknown_line_codes: list[str]
    summary: dict[str, Any]


class CommitRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    file_id: str
    statement: str
    entity: str = ""
    fiscal_year: int = Field(..., ge=2000, le=2100)


class CommitResponse(BaseModel):
    ok: bool = True
    rows_written: int = 0


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _validate_statement(statement: str) -> str:
    s = (statement or "").upper()
    if s not in _STATEMENTS:
        raise HTTPException(status_code=422, detail="statement must be 'PL' or 'BS'")
    return s


def _resolve_ep(session: Session, entity: Optional[str]) -> Optional[str]:
    """Resolve a legal_entity_code to entity_prefix; '' / 'all' / None → consolidated."""
    if not entity or entity.strip().lower() in ("", "all"):
        return None
    return resolve_entity_prefix(session, entity)


def _visible_entity_prefixes(session: Session, user: User) -> Optional[set[str]]:
    """Resolve the user's visibility to a set of allowed 2-char entity prefixes.

    Returns ``None`` for an admin / unrestricted user (no restriction — any entity
    and the consolidated scope are allowed).  A restricted user gets the (possibly
    empty) set of ``entity_prefix`` values their roles grant — the SAME mapping
    ``financials_compat._anomaly_entity_prefixes`` uses, so visibility can never
    drift across the budget / anomaly read paths.
    """
    allowed_codes = visible_entity_codes(session, user)
    if allowed_codes is None:
        return None  # admin / unrestricted
    from sqlalchemy import text

    prefixes: set[str] = set()
    if allowed_codes:
        rows = session.execute(
            text(
                "SELECT DISTINCT entity_prefix FROM dim_legal_entity "
                "WHERE legal_entity_code = ANY(:codes)"
            ),
            {"codes": sorted(allowed_codes)},
        ).fetchall()
        prefixes = {str(r[0]).strip()[:2] for r in rows if r and r[0] is not None}
        prefixes.discard("")
    return prefixes


def _assert_entity_visible(session: Session, user: User, entity_prefix: Optional[str]) -> None:
    """Fail-closed multi-tenant guard for an entity scope (GET tree / Excel / commit).

    Admins / unrestricted users (``_visible_entity_prefixes`` → None) see everything.
    A restricted user may only access a scope whose ``entity_prefix`` is one of their
    granted entities; a consolidated ('all') scope is allowed only for unrestricted
    users (it would otherwise span tenants).
    """
    allowed_prefixes = _visible_entity_prefixes(session, user)
    if allowed_prefixes is None:
        return  # admin / unrestricted
    if entity_prefix is None:
        raise HTTPException(
            status_code=403,
            detail="Consolidated ('all') budget scope is restricted; select one of your entities.",
        )
    if str(entity_prefix)[:2] not in allowed_prefixes:
        raise HTTPException(status_code=403, detail="You are not permitted to access this entity.")


def _safe_file_id(file_id: str) -> str:
    """Sanitize a ``file_id`` to alnum (defends against path traversal)."""
    fid = "".join(ch for ch in str(file_id) if ch.isalnum())
    if not fid:
        raise HTTPException(status_code=404, detail="file_id not found")
    return fid


def _budget_file_path(file_id: str) -> Path:
    """Resolve an upload ``file_id`` to its saved path under the budget upload dir."""
    fid = _safe_file_id(file_id)
    for p in _UPLOAD_DIR.iterdir():
        if p.is_file() and p.suffix.lower() == ".xlsx" and p.name.startswith(fid + "_"):
            return p
    raise HTTPException(status_code=404, detail=f"file_id {file_id!r} not found")


def _scope_sidecar_path(file_id: str) -> Path:
    """Path to the ``.json`` scope sidecar persisted next to an uploaded file."""
    return _UPLOAD_DIR / f"{_safe_file_id(file_id)}.scope.json"


def _delete_upload_files(file_id: str) -> None:
    """Best-effort delete of the saved upload + its scope sidecar for ``file_id``."""
    try:
        fid = "".join(ch for ch in str(file_id) if ch.isalnum())
        if not fid:
            return
        for p in _UPLOAD_DIR.iterdir():
            if p.is_file() and (p.name.startswith(fid + "_") or p.name == f"{fid}.scope.json"):
                p.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001 — cleanup must never break the request
        logger.warning("budget upload cleanup failed: %s", type(exc).__name__)


def _sweep_stale_uploads() -> None:
    """Best-effort TTL sweep: delete budget upload files older than the TTL.

    Runs on each upload so previewed-but-never-committed files do not accumulate.
    Never raises — upload hygiene must not break the upload path itself.
    """
    cutoff = time.time() - _UPLOAD_TTL_SECONDS
    try:
        for p in _UPLOAD_DIR.iterdir():
            try:
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
            except OSError:
                continue
    except Exception as exc:  # noqa: BLE001
        logger.warning("budget upload TTL sweep failed: %s", type(exc).__name__)


# --------------------------------------------------------------------------- #
# GET /api/v1/budget  (pure-read)
# --------------------------------------------------------------------------- #
@router.get("", response_model=BudgetGridResponse)
def get_budget(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
    statement: str = Query("PL"),
    fiscal_year: int = Query(..., ge=2000, le=2100),
    entity: Optional[str] = Query(None, description="'' / 'all' = consolidated; a code = per-entity"),
    level: Literal["L3", "L4"] = Query("L4", description="L4 expands PL L4 children in the payload"),
    heuristic: Literal["prior_year", "trend_cagr", "run_rate"] = Query("prior_year"),
    growth_pct: float = Query(0.0, description="growth assumption for the suggestion recompute"),
    top_n: int = Query(20, ge=1, le=500),
    light: bool = Query(
        False,
        description="Skip per-position heuristic suggestions (faster blank/manual grids)",
    ),
) -> BudgetGridResponse:
    """Editable budget TREE for a statement + year, seeded with the synthetic plan
    where no budget row exists.  PURE-READ (no writes).

    Top level = the statement's reporting positions (L3) in statement order; PL
    positions carry their discovered L4 ``children`` when ``level=L4`` (omitted for
    ``level=L3``).  Partner-driven positions (Net sales, Cost of materials) carry
    ``partners`` (Top-N + 'Other').  Each position carries the Finssentials-heuristic
    ``suggestion`` + ``explanation``.  Entity-visibility is enforced fail-closed."""
    stmt = _validate_statement(statement)
    ep = _resolve_ep(session, entity)
    _assert_entity_visible(session, user, ep)
    try:
        grid = budget_service.build_grid(
            session, statement=stmt, fiscal_year=fiscal_year,
            entity_prefix=ep, top_n=top_n, level=level,
            heuristic=heuristic, growth_pct=growth_pct,
            light=light,
        )
    except Exception as exc:  # noqa: BLE001
        # M1: never log the full exception (may carry partner names / bound SQL
        # params). Log the type + first line, truncated, like the sibling handlers.
        logger.error("get_budget failed: %s: %s", type(exc).__name__, str(exc).split(chr(10))[0][:200])
        raise HTTPException(status_code=500, detail="Failed to build budget grid. Check server logs.") from exc
    return BudgetGridResponse(**grid)


# --------------------------------------------------------------------------- #
# GET /api/v1/budget/entities  (entities the user may PLAN, visibility-scoped)
# --------------------------------------------------------------------------- #
@router.get("/entities", response_model=BudgetEntitiesResponse)
def get_budget_entities(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> BudgetEntitiesResponse:
    """List the legal entities the caller may plan, scoped by entity-visibility.

    Admins / unrestricted users see ALL real (non-consolidation) entities and may
    plan the consolidated scope (``can_consolidate=True``).  A restricted user sees
    only the entities their roles grant and may NOT plan consolidated (it would span
    tenants).  ``prefix`` is the 2-char ``entity_prefix`` the FE passes back to the
    other budget endpoints via ``entity`` (the FE may send the code; both resolve)."""
    from sqlalchemy import text

    allowed_prefixes = _visible_entity_prefixes(session, user)
    can_consolidate = allowed_prefixes is None
    rows = session.execute(
        text(
            "SELECT legal_entity_code, COALESCE(entity_name, legal_entity_code) AS name, "
            "       entity_prefix, COALESCE(is_consolidation, FALSE) AS is_consol "
            "FROM dim_legal_entity ORDER BY entity_name"
        )
    ).fetchall()
    entities: list[BudgetEntity] = []
    for r in rows:
        code, name, prefix, is_consol = str(r[0]), str(r[1]), str(r[2] or "").strip()[:2], bool(r[3])
        if is_consol or not prefix:
            continue  # the consolidation pseudo-entity is not a plannable scope
        if allowed_prefixes is not None and prefix not in allowed_prefixes:
            continue  # restricted: hide entities the user may not plan
        entities.append(BudgetEntity(code=code, label=name, prefix=prefix))
    return BudgetEntitiesResponse(entities=entities, can_consolidate=can_consolidate)


# --------------------------------------------------------------------------- #
# POST /api/v1/budget/seed
# --------------------------------------------------------------------------- #
@router.post("/seed", response_model=WriteResponse)
def seed_budget(
    body: SeedRequest,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
) -> WriteResponse:
    """Materialise the synthetic seed into fact_position_plan (idempotent)."""
    stmt = _validate_statement(body.statement)
    ep = _resolve_ep(session, body.entity)
    try:
        res = budget_service.seed_budget(
            session, statement=stmt, fiscal_year=body.fiscal_year,
            entity=(ep or ""), top_n=body.top_n, updated_by=admin.email,
            materialize_suggestion=body.materialize_suggestion,
            heuristic=body.heuristic, growth_pct=body.growth_pct,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("seed_budget failed: %s", str(exc).split(chr(10))[0][:200])
        raise HTTPException(status_code=500, detail="Seed failed; transaction rolled back.") from exc
    return WriteResponse(rows_seeded=res["rows_seeded"])


# --------------------------------------------------------------------------- #
# PUT /api/v1/budget/cell
# --------------------------------------------------------------------------- #
@router.put("/cell", response_model=WriteResponse)
def put_cell(
    body: CellRequest,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
) -> WriteResponse:
    """Save one cell (position-level or single-partner); annual is seasonalized."""
    stmt = _validate_statement(body.statement)
    ep = _resolve_ep(session, body.entity)
    try:
        res = budget_service.upsert_cell(
            session, statement=stmt, line_code=body.line_code,
            entity=(ep or ""), partner_id=body.partner_id,
            partner_kind=body.partner_kind, fiscal_year=body.fiscal_year,
            months=body.months, annual=body.annual, weights=body.weights,
            level_4=body.level_4,
            updated_by=admin.email,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("put_cell failed: %s", str(exc).split(chr(10))[0][:200])
        raise HTTPException(status_code=500, detail="Save failed; transaction rolled back.") from exc
    return WriteResponse(rows_upserted=res["rows_upserted"])


# --------------------------------------------------------------------------- #
# PATCH /api/v1/budget/position
# --------------------------------------------------------------------------- #
@router.patch("/position", response_model=WriteResponse)
def patch_position(
    body: PositionPatchRequest,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
) -> WriteResponse:
    """Bulk-save a position total + its partners in one transaction (roll-up)."""
    stmt = _validate_statement(body.statement)
    ep = _resolve_ep(session, body.entity)
    try:
        res = budget_service.patch_position(
            session, statement=stmt, line_code=body.line_code,
            entity=(ep or ""), fiscal_year=body.fiscal_year,
            position=body.position, partners=body.partners, weights=body.weights,
            level_4=body.level_4,
            updated_by=admin.email,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("patch_position failed: %s", str(exc).split(chr(10))[0][:200])
        raise HTTPException(status_code=500, detail="Save failed; transaction rolled back.") from exc
    return WriteResponse(rows_upserted=res["rows_upserted"])


# --------------------------------------------------------------------------- #
# DELETE /api/v1/budget
# --------------------------------------------------------------------------- #
@router.delete("", response_model=WriteResponse)
def delete_budget(
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    statement: str = Query(...),
    fiscal_year: int = Query(..., ge=2000, le=2100),
    entity: Optional[str] = Query(None),
) -> WriteResponse:
    """Drop budget rows for the scope → readers revert to forecast/plan."""
    stmt = _validate_statement(statement)
    # entity=None → drop ALL entity scopes; explicit → resolve to prefix.
    if entity is None or entity.strip().lower() in ("", "all"):
        ent: Optional[str] = None
    else:
        ent = _resolve_ep(session, entity) or ""
    try:
        res = budget_service.delete_budget(
            session, statement=stmt, fiscal_year=fiscal_year, entity=ent,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("delete_budget failed: %s", str(exc).split(chr(10))[0][:200])
        raise HTTPException(status_code=500, detail="Delete failed; transaction rolled back.") from exc
    return WriteResponse(deleted=res["deleted"])


# --------------------------------------------------------------------------- #
# GET /api/v1/budget/template  (Excel round-trip — download prefilled scope)
# --------------------------------------------------------------------------- #
@router.get("/template")
def download_template(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
    statement: str = Query("PL"),
    fiscal_year: int = Query(..., ge=2000, le=2100),
    entity: Optional[str] = Query(None),
    level: Literal["L3", "L4"] = Query("L3"),
    top_n: int = Query(20, ge=1, le=500),
) -> StreamingResponse:
    """Stream a PREFILLED budget planning template (.xlsx) for a scope.

    Sheet 1 'Positions' (one row per L3, plus per discovered L4 child when
    ``level=L4``) + Sheet 2 'Partners' (Top-N + 'Other').  Prefilled from the
    current budget or the Finssentials-heuristic suggestion.  Entity-visibility
    enforced (restricted users may not download a consolidated/cross-tenant scope).
    """
    stmt = _validate_statement(statement)
    ep = _resolve_ep(session, entity)
    _assert_entity_visible(session, user, ep)
    try:
        wb = budget_excel.build_template_workbook(
            session, statement=stmt, fiscal_year=fiscal_year,
            entity_prefix=ep, level=level, top_n=top_n,
        )
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
    except Exception as exc:  # noqa: BLE001
        logger.error("budget template build failed: %s", str(exc).split(chr(10))[0][:200])
        raise HTTPException(status_code=500, detail="Failed to build template. Check server logs.") from exc
    filename = budget_excel.template_filename(stmt, ep, fiscal_year)
    return StreamingResponse(
        buf, media_type=_XLSX_MEDIA,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --------------------------------------------------------------------------- #
# POST /api/v1/budget/upload  (parse + PREVIEW DIFF; NO write)
# --------------------------------------------------------------------------- #
@router.post("/upload", response_model=BudgetUploadResponse)
async def upload_template(
    file: UploadFile = File(...),
    statement: str = Form("PL"),
    fiscal_year: int = Form(...),
    entity: str = Form(""),
    top_n: int = Form(20),
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
) -> BudgetUploadResponse:
    """Upload a filled template, validate it, and return a PREVIEW DIFF vs the
    current budget.  Saves the file under a ``file_id`` so /commit can re-read it.
    NO write to fact_position_plan.

    Admin-only (H1): only admins can /commit, so a non-admin upload would be pure
    attack surface against the parse/storage path.
    """
    stmt = _validate_statement(statement)
    if not (2000 <= int(fiscal_year) <= 2100):
        raise HTTPException(status_code=422, detail="fiscal_year out of range")
    ep = _resolve_ep(session, entity or None)
    _assert_entity_visible(session, admin, ep)

    # H1: sweep previewed-but-never-committed files older than the TTL (best-effort).
    _sweep_stale_uploads()

    name = (file.filename or "budget.xlsx")
    if Path(name).suffix.lower() != ".xlsx":
        raise HTTPException(status_code=415, detail="Only .xlsx budget templates are accepted")

    # H2: enforce the size cap BEFORE buffering the whole body. Reject early on a
    # content-length / file.size hint, then read in chunks and abort once over cap
    # so a zip-bomb cannot exhaust memory before the check.
    declared = getattr(file, "size", None)
    if isinstance(declared, int) and declared > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File exceeds {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"File exceeds {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
        chunks.append(chunk)
    raw = b"".join(chunks)

    # Parse with openpyxl (data-only: read formula results / typed values).
    try:
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(raw), data_only=True)
        budget_excel.assert_parse_bounds(wb)  # H2: reject oversized sheets pre-iteration
        parsed = budget_excel.parse_workbook(wb)
    except budget_excel.TemplateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("budget upload parse failed: %s", str(exc).split(chr(10))[0][:200])
        raise HTTPException(status_code=422, detail="Could not parse the uploaded workbook.") from exc

    # Build the diff baseline + the set of valid line_codes for this statement.
    try:
        grid = budget_service.build_grid(
            session, statement=stmt, fiscal_year=int(fiscal_year),
            entity_prefix=ep, top_n=int(top_n),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("budget upload baseline failed: %s", str(exc).split(chr(10))[0][:200])
        raise HTTPException(status_code=500, detail="Failed to build diff baseline.") from exc
    known = [str(p["line_code"]) for p in grid.get("positions", [])]
    diff = budget_excel.diff_against_grid(parsed, grid, known_line_codes=known)
    if diff["unknown_line_codes"]:
        raise HTTPException(
            status_code=422,
            detail=f"unknown line codes for {stmt}: {', '.join(diff['unknown_line_codes'][:10])}",
        )

    # Persist the validated file so /commit re-reads exactly the same bytes.
    file_id = uuid.uuid4().hex[:16]
    safe = "".join(ch for ch in Path(name).name if ch.isalnum() or ch in ("_", "-", "."))[:120] or "budget.xlsx"
    if Path(safe).suffix.lower() != ".xlsx":
        safe = f"{safe}.xlsx"
    (_UPLOAD_DIR / f"{file_id}_{safe}").write_bytes(raw)
    # M3: persist the validated scope alongside the file so /commit can assert the
    # body's (statement, entity, fiscal_year) MATCHES what was previewed — preventing
    # an admin from committing a file previewed for one scope to a different scope.
    _scope_sidecar_path(file_id).write_text(
        json.dumps({"statement": stmt, "entity": (ep or ""), "fiscal_year": int(fiscal_year)}),
        encoding="utf-8",
    )

    return BudgetUploadResponse(
        file_id=file_id, statement=stmt, fiscal_year=int(fiscal_year),
        entity=(ep or ""), changes=diff["changes"],
        unknown_line_codes=diff["unknown_line_codes"], summary=diff["summary"],
    )


# --------------------------------------------------------------------------- #
# POST /api/v1/budget/commit  (admin — write the confirmed scope)
# --------------------------------------------------------------------------- #
@router.post("/commit", response_model=CommitResponse)
def commit_template(
    body: CommitRequest,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
) -> CommitResponse:
    """Re-read the uploaded file and WRITE the confirmed scope (admin-only).

    Months are written as absolute presented values via the XOR-aware
    ``patch_position``.  Validates finiteness + entity-visibility again (defense in
    depth) and overwrites exactly that scope in the service transaction."""
    stmt = _validate_statement(body.statement)
    ep = _resolve_ep(session, body.entity or None)
    _assert_entity_visible(session, admin, ep)

    path = _budget_file_path(body.file_id)

    # M3: assert the committed scope MATCHES what was previewed for this file_id.
    # The sidecar holds the validated (statement, entity, fiscal_year) from /upload;
    # a mismatch means the caller is committing a file to a scope it was not
    # previewed against — reject (409) rather than write the wrong scope.
    sidecar = _scope_sidecar_path(body.file_id)
    if sidecar.exists():
        try:
            previewed = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("budget commit sidecar unreadable: %s", type(exc).__name__)
            previewed = None
        if previewed is not None:
            if (
                str(previewed.get("statement")) != stmt
                or str(previewed.get("entity") or "") != (ep or "")
                or int(previewed.get("fiscal_year")) != int(body.fiscal_year)
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Commit scope does not match the previewed upload scope.",
                )

    try:
        from openpyxl import load_workbook

        wb = load_workbook(path, data_only=True)
        parsed = budget_excel.parse_workbook(wb)
    except budget_excel.TemplateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("budget commit re-read failed: %s", str(exc).split(chr(10))[0][:200])
        raise HTTPException(status_code=422, detail="Could not re-read the uploaded workbook.") from exc

    try:
        grid = budget_service.build_grid(
            session, statement=stmt, fiscal_year=body.fiscal_year, entity_prefix=ep,
        )
        known = [str(p["line_code"]) for p in grid.get("positions", [])]
        res = budget_excel.commit_records(
            session, parsed, statement=stmt, entity=(ep or ""),
            fiscal_year=body.fiscal_year, known_line_codes=known,
            updated_by=admin.email,
        )
    except budget_excel.TemplateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("budget commit failed: %s", str(exc).split(chr(10))[0][:200])
        raise HTTPException(status_code=500, detail="Commit failed; transaction rolled back.") from exc
    # H1: a committed file has served its purpose — delete it + its scope sidecar so
    # confirmed financial uploads do not linger on disk.
    _delete_upload_files(body.file_id)
    return CommitResponse(rows_written=res["rows_written"])
