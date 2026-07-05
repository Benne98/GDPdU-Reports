"""Customer / Supplier MASTER editor API.

Prefix: /api/v1/masters

Endpoints:
  GET    /{side}            List / search master rows (entity-scoped). Auth required.
  POST   /{side}            Single-row upsert (admin + entity-scoped).
  PATCH  /{side}/{id}       Update descriptive fields (admin + entity-scoped).
  DELETE /{side}/{id}       Usage-guarded delete (admin + entity-scoped).
  GET    /ref               Dropdown reference data (entity prefixes + countries).

``side`` is the plural path segment ('customers' | 'suppliers'); it maps
internally to dim_customer (debtor_number) / dim_supplier (creditor_number).

Design rules (CLAUDE.md):
  - Routers are thin; the partner-id construction and the master-aware UPSERT are
    REUSED from etl (``etl.transform.build_partner_id`` + ``etl.load.load_partners``)
    so the id rules + COALESCE/master name rules can never drift from the bulk
    partner-master path (POST /api/v1/ingest/partner-master/commit).
  - Writes are admin-gated AND entity-scoped (a restricted user may only touch
    their visible entity prefixes); reads are auth-required and entity-scoped.
  - No secrets / stack traces in responses; the full error stays in the logs.
  - DB session via Depends(get_session).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# etl/ lives at the repo root, one level above backend/ — make it importable when
# the backend is launched from backend/ (mirrors app.routers.ingest).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Path as PathParam, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import User, current_user, require_admin
from app.db import get_session
from app.services.entity_visibility import visible_entity_codes
from etl.load import load_partners
from etl.transform import build_partner_id, normalize_prefix

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/masters", tags=["masters"])

DEFAULT_SOURCE_SYSTEM = "manual_entry"

# side(plural) -> table metadata
_SIDE_META: dict[str, dict[str, str]] = {
    "customers": {"table": "dim_customer", "id_col": "customer_id", "number_col": "debtor_number"},
    "suppliers": {"table": "dim_supplier", "id_col": "supplier_id", "number_col": "creditor_number"},
}

# Descriptive columns a PATCH may touch (purchasing_org is supplier-only).
_PATCHABLE_COMMON = [
    "name_line_1", "name_line_2", "country_code", "region_code",
    "city", "postal_code", "default_currency",
]


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class CustomerDetail(BaseModel):
    customer_id: str
    debtor_number: str | None = None
    entity_prefix: str | None = None
    name_line_1: str | None = None
    name_line_2: str | None = None
    country_code: str | None = None
    region_code: str | None = None
    city: str | None = None
    postal_code: str | None = None
    default_currency: str | None = None
    source_system: str | None = None
    updated_at: str | None = None


class SupplierDetail(BaseModel):
    supplier_id: str
    creditor_number: str | None = None
    entity_prefix: str | None = None
    name_line_1: str | None = None
    name_line_2: str | None = None
    country_code: str | None = None
    region_code: str | None = None
    city: str | None = None
    postal_code: str | None = None
    default_currency: str | None = None
    purchasing_org: str | None = None
    source_system: str | None = None
    updated_at: str | None = None


class MasterListResponse(BaseModel):
    rows: list[dict]
    total: int


class MasterUpsertRequest(BaseModel):
    entity_prefix: str = Field(..., max_length=8)
    number: str = Field(..., max_length=64)  # debtor_number | creditor_number
    name_line_1: str | None = Field(None, max_length=200)
    name_line_2: str | None = Field(None, max_length=200)
    country_code: str | None = Field(None, max_length=3)
    region_code: str | None = Field(None, max_length=10)
    city: str | None = Field(None, max_length=120)
    postal_code: str | None = Field(None, max_length=20)
    default_currency: str | None = Field(None, max_length=3)
    purchasing_org: str | None = Field(None, max_length=40)  # supplier only
    source_system: str | None = Field(None, max_length=80)


class MasterPatchRequest(BaseModel):
    name_line_1: str | None = Field(None, max_length=200)
    name_line_2: str | None = Field(None, max_length=200)
    country_code: str | None = Field(None, max_length=3)
    region_code: str | None = Field(None, max_length=10)
    city: str | None = Field(None, max_length=120)
    postal_code: str | None = Field(None, max_length=20)
    default_currency: str | None = Field(None, max_length=3)
    purchasing_org: str | None = Field(None, max_length=40)  # supplier only


class RefEntityPrefix(BaseModel):
    code: str
    prefix: str
    name: str | None = None


class RefCountry(BaseModel):
    code: str
    name: str | None = None


class MasterRefResponse(BaseModel):
    entity_prefixes: list[RefEntityPrefix]
    countries: list[RefCountry]


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #
def _meta(side: str) -> dict[str, str]:
    meta = _SIDE_META.get(side)
    if meta is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown side {side!r}; expected one of {sorted(_SIDE_META)}",
        )
    return meta


def _visible_prefixes_or_none(session: Session, user: User) -> set[str] | None:
    """Allowed 2-char entity prefixes for *user*, or None for admin/unrestricted.

    Mirrors ``app.routers.ingest._visible_entity_prefixes_or_none`` so the
    multi-tenant rule cannot drift across write paths.
    """
    allowed_codes = visible_entity_codes(session, user)
    if allowed_codes is None:
        return None  # admin / unrestricted
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


def _assert_prefix_visible(session: Session, user: User, prefix: str) -> None:
    """Fail-closed: a restricted user may only write within their entity prefixes."""
    allowed = _visible_prefixes_or_none(session, user)
    if allowed is None:
        return  # admin / unrestricted
    p = str(prefix).strip()[:2]
    if not p or p not in allowed:
        raise HTTPException(
            status_code=403,
            detail="Not permitted to write master data for this entity.",
        )


def _row_to_detail(side: str, row) -> dict:
    """Map a DB row (RowMapping) to the side-appropriate detail dict."""
    d = dict(row)
    if d.get("updated_at") is not None:
        d["updated_at"] = str(d["updated_at"])
    if d.get("entity_prefix") is not None:
        d["entity_prefix"] = str(d["entity_prefix"]).strip()
    model = CustomerDetail if side == "customers" else SupplierDetail
    return model(**d).model_dump()


def _select_columns(side: str) -> str:
    if side == "customers":
        return (
            "customer_id, debtor_number, entity_prefix, name_line_1, name_line_2, "
            "country_code, region_code, city, postal_code, default_currency, "
            "source_system, updated_at"
        )
    return (
        "supplier_id, creditor_number, entity_prefix, name_line_1, name_line_2, "
        "country_code, region_code, city, postal_code, default_currency, "
        "purchasing_org, source_system, updated_at"
    )


def _fetch_one(session: Session, side: str, partner_id: str) -> dict | None:
    meta = _meta(side)
    row = session.execute(
        text(f"SELECT {_select_columns(side)} FROM {meta['table']} WHERE {meta['id_col']} = :id"),
        {"id": partner_id},
    ).mappings().fetchone()
    return _row_to_detail(side, row) if row else None


# --------------------------------------------------------------------------- #
# GET /ref — dropdown reference data
# --------------------------------------------------------------------------- #
# NOTE: declared BEFORE GET /{side} so the literal '/ref' path is matched first
# and not captured by the '{side}' path parameter.
@router.get("/ref", response_model=MasterRefResponse)
def masters_ref(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> MasterRefResponse:
    """Entity prefixes + country codes for the editor dropdowns (entity-scoped)."""
    allowed = _visible_prefixes_or_none(session, user)

    ent_rows = session.execute(
        text(
            "SELECT legal_entity_code, entity_prefix, entity_name "
            "FROM dim_legal_entity ORDER BY entity_prefix"
        )
    ).fetchall()
    entity_prefixes: list[RefEntityPrefix] = []
    for r in ent_rows:
        prefix = str(r[1]).strip() if r[1] is not None else ""
        if allowed is not None and prefix[:2] not in allowed:
            continue  # hide entities the restricted user cannot see
        entity_prefixes.append(
            RefEntityPrefix(code=str(r[0]), prefix=prefix, name=r[2])
        )

    country_rows = session.execute(
        text("SELECT country_code, name_en FROM dim_country ORDER BY country_code")
    ).fetchall()
    countries = [
        RefCountry(code=str(r[0]).strip(), name=r[1])
        for r in country_rows if r[0] is not None
    ]

    return MasterRefResponse(entity_prefixes=entity_prefixes, countries=countries)


# --------------------------------------------------------------------------- #
# GET /{side} — list / search (entity-scoped, auth required)
# --------------------------------------------------------------------------- #
@router.get("/{side}", response_model=MasterListResponse)
def list_masters(
    side: str = PathParam(...),
    search: str | None = Query(None, max_length=200),
    entity: str | None = Query(None, max_length=8, description="2-char entity prefix filter"),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> MasterListResponse:
    """List master rows, filtered by search/entity, scoped to the user's entities."""
    meta = _meta(side)
    table, id_col = meta["table"], meta["id_col"]

    where: list[str] = []
    params: dict = {}

    # Entity scope: restricted users see only their visible prefixes; admin -> all.
    allowed = _visible_prefixes_or_none(session, user)
    if allowed is not None:
        if not allowed:
            return MasterListResponse(rows=[], total=0)  # deny-all, fail-closed
        where.append("entity_prefix = ANY(:scope)")
        params["scope"] = sorted(allowed)

    if entity:
        where.append("entity_prefix = :entity")
        params["entity"] = str(entity).strip()[:2]

    if search:
        where.append(f"({id_col} ILIKE :q OR name_line_1 ILIKE :q OR name_line_2 ILIKE :q)")
        params["q"] = f"%{search.strip()}%"

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    total = int(session.execute(
        text(f"SELECT COUNT(*) FROM {table}{where_sql}"), params
    ).scalar() or 0)

    rows = session.execute(
        text(
            f"SELECT {_select_columns(side)} FROM {table}{where_sql} "
            f"ORDER BY {id_col} LIMIT :limit OFFSET :offset"
        ),
        {**params, "limit": limit, "offset": offset},
    ).mappings().fetchall()

    return MasterListResponse(
        rows=[_row_to_detail(side, r) for r in rows],
        total=total,
    )


# --------------------------------------------------------------------------- #
# POST /{side} — single upsert (admin + entity-scoped)
# --------------------------------------------------------------------------- #
@router.post("/{side}", response_model=dict)
def upsert_master(
    body: MasterUpsertRequest,
    side: str = PathParam(...),
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
) -> dict:
    """Single-row upsert reusing the ETL id construction + master-aware UPSERT."""
    meta = _meta(side)
    id_col, number_col = meta["id_col"], meta["number_col"]

    try:
        prefix = normalize_prefix(body.entity_prefix)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _assert_prefix_visible(session, admin, prefix)

    number = str(body.number).strip()
    if not number:
        raise HTTPException(status_code=422, detail="number must not be empty")

    # SAME id construction as the GL / bulk partner-master path.
    partner_id = str(build_partner_id(prefix, pd.Series([number])).iloc[0])
    if not partner_id or partner_id == "<NA>":
        raise HTTPException(status_code=422, detail="Could not construct a partner id")

    source_system = (body.source_system or DEFAULT_SOURCE_SYSTEM).strip() or DEFAULT_SOURCE_SYSTEM

    # Build the single-row frame load_partners expects (customer_id/debtor_number
    # or supplier_id/creditor_number + descriptive cols).
    record = {
        id_col: partner_id,
        number_col: number,
        "name_line_1": body.name_line_1,
        "name_line_2": body.name_line_2,
        "country_code": body.country_code,
        "region_code": body.region_code,
        "city": body.city,
        "postal_code": body.postal_code,
        "default_currency": body.default_currency,
        "source_system": source_system,
    }
    df = pd.DataFrame([record])
    empty = pd.DataFrame()

    try:
        if side == "customers":
            load_partners(session, df, empty)
        else:
            load_partners(session, empty, df)
            # purchasing_org is supplier-only and not covered by load_partners;
            # apply it as a scoped follow-up UPDATE (COALESCE keeps prior value
            # when not supplied) so the reused name/COALESCE rules stay intact.
            if body.purchasing_org is not None:
                session.execute(
                    text(
                        "UPDATE dim_supplier SET purchasing_org = :po, updated_at = NOW() "
                        "WHERE supplier_id = :id"
                    ),
                    {"po": body.purchasing_org, "id": partner_id},
                )
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.exception("masters upsert failed (side=%s id=%s)", side, partner_id)
        raise HTTPException(
            status_code=500, detail="Master upsert failed; check server logs."
        ) from exc

    detail = _fetch_one(session, side, partner_id)
    if detail is None:
        raise HTTPException(status_code=500, detail="Upserted row could not be read back.")
    return detail


# --------------------------------------------------------------------------- #
# PATCH /{side}/{id} — update descriptive fields (admin + entity-scoped)
# --------------------------------------------------------------------------- #
@router.patch("/{side}/{partner_id}", response_model=dict)
def patch_master(
    body: MasterPatchRequest,
    side: str = PathParam(...),
    partner_id: str = PathParam(..., max_length=64),
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
) -> dict:
    """Update a subset of descriptive fields on an existing master row."""
    meta = _meta(side)
    table, id_col = meta["table"], meta["id_col"]

    existing = _fetch_one(session, side, partner_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"{side[:-1]} {partner_id!r} not found")

    # Entity scope from the id's prefix (id == entity_prefix(2) + number).
    _assert_prefix_visible(session, admin, str(partner_id)[:2])

    fields = dict(body.model_dump(exclude_unset=True))
    # purchasing_org only exists on dim_supplier.
    if side == "customers":
        fields.pop("purchasing_org", None)
    if not fields:
        raise HTTPException(status_code=422, detail="No updatable fields supplied.")

    set_sql = ", ".join(f"{col} = :{col}" for col in fields)
    params = {**fields, "id": partner_id}
    try:
        session.execute(
            text(f"UPDATE {table} SET {set_sql}, updated_at = NOW() WHERE {id_col} = :id"),
            params,
        )
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.exception("masters patch failed (side=%s id=%s)", side, partner_id)
        raise HTTPException(
            status_code=500, detail="Master update failed; check server logs."
        ) from exc

    detail = _fetch_one(session, side, partner_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"{side[:-1]} {partner_id!r} not found")
    return detail


# --------------------------------------------------------------------------- #
# DELETE /{side}/{id} — usage-guarded (admin + entity-scoped)
# --------------------------------------------------------------------------- #
@router.delete("/{side}/{partner_id}")
def delete_master(
    side: str = PathParam(...),
    partner_id: str = PathParam(..., max_length=64),
    force: bool = Query(False),
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
) -> dict:
    """Delete a master row, guarded by fact-table usage unless force=true."""
    meta = _meta(side)
    table, id_col = meta["table"], meta["id_col"]

    existing = _fetch_one(session, side, partner_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"{side[:-1]} {partner_id!r} not found")

    _assert_prefix_visible(session, admin, str(partner_id)[:2])

    # Count fact rows that soft-reference this id (no FK; safe enrichment).
    if side == "customers":
        usage_tables = {"fact_ar": "customer_id", "fact_sales": "customer_id",
                        "fact_gl_line": "customer_id"}
    else:
        usage_tables = {"fact_ap": "supplier_id", "fact_com": "supplier_id",
                        "fact_gl_line": "supplier_id"}

    counts: dict[str, int] = {}
    for tbl, col in usage_tables.items():
        try:
            n = int(session.execute(
                text(f"SELECT COUNT(*) FROM {tbl} WHERE {col} = :id"),
                {"id": partner_id},
            ).scalar() or 0)
        except Exception:  # noqa: BLE001 — a missing optional fact table is not fatal
            n = 0
        counts[tbl] = n

    total_usage = sum(counts.values())
    if total_usage > 0 and not force:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Master is referenced by fact rows; pass force=true to delete anyway.",
                "usage": counts,
                "total": total_usage,
            },
        )

    try:
        session.execute(
            text(f"DELETE FROM {table} WHERE {id_col} = :id"),
            {"id": partner_id},
        )
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.exception("masters delete failed (side=%s id=%s)", side, partner_id)
        raise HTTPException(
            status_code=500, detail="Master delete failed; check server logs."
        ) from exc

    return {"deleted": True, "id": partner_id, "usage": counts, "forced": bool(force)}
