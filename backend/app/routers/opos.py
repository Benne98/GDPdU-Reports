"""D4 DRAFT — OPOS (open items) Debitor + Kreditor ingest API.

Prefix: /api/v1/opos

Endpoints (two-sided, mirror the GL ingest shape):
  POST /{side}/upload    Stage an OPOS file; return sheets/columns/sample.
  POST /{side}/preview   Re-read a staged file (pick a sheet).
  POST /{side}/commit    Map source columns -> fact_opos_{debitor|kreditor} and
                         store PASS-THROUGH postings. is_open + aging_band are
                         LEFT NULL (no settlement matching / bucketing computed).
  POST /{side}/derive-aging   Explicit STUB — raises NotImplementedError (F3/F4).

``side`` is 'debitor' | 'kreditor' (receivables / payables), mirroring the
customer/supplier partner split.

DRAFT — see ``docs/financial-logic.md``:
  F3 open-item determination (settlement matching RV vs ZA by beleg_no/referenz)
  F4 AR/AP aging bucketing — MUST reuse ``app.services.gl_aging.AR_BANDS`` +
     ``_band_case_sql`` and the golden shape
     ``backend/golden/v2/sales__{receivables,payables}-aging__*.json``.
No value is computed in this epic.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, Path as PathParam, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import User, current_user, require_admin
from app.db import get_session
from app.services import draft_ingest as di
from app.services.entities import BUKRS_TO_PREFIX

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/opos", tags=["opos"])

# side -> target table
_SIDE_TABLE: dict[str, str] = {
    "debitor": "fact_opos_debitor",
    "kreditor": "fact_opos_kreditor",
}

# Integer-typed source fields (Buchungskreis, dunning level). Parsed leniently as
# numbers then cast to int on the row so INTEGER/SMALLINT columns get clean values.
_INT_FIELDS = {"buchungskreis", "mahnstufe"}
_NUMBER_FIELDS = {"amount_hauswaehrung"} | _INT_FIELDS
_DATE_FIELDS = {"net_due_date", "posting_date", "beleg_date"}
_TEXT_FIELDS = {
    "konto", "belegart", "beleg_no", "referenz",
    # 0024 as-of columns (pass-through text)
    "satzart", "partner_no", "waehrung", "geschaeftsbereich",
    "buchungsschluessel", "konto_gegenbuchung", "gobd_transaktionsnr",
}
_TARGET_FIELDS = _NUMBER_FIELDS | _DATE_FIELDS | _TEXT_FIELDS

_INSERT_COLUMNS = [
    "project_id", "dataset_version_id", "source_file_id", "row_no",
    "entity_prefix", "fy_label",
    "partner_id", "konto", "belegart", "beleg_no", "referenz",
    "net_due_date", "amount_hauswaehrung", "posting_date",
    # 0024 as-of columns
    "buchungskreis", "satzart", "partner_no", "partner_key", "beleg_date",
    "mahnstufe", "waehrung", "geschaeftsbereich", "buchungsschluessel",
    "konto_gegenbuchung", "gobd_transaktionsnr",
    "is_open", "aging_band",
]


def _table(side: str) -> str:
    tbl = _SIDE_TABLE.get(side)
    if tbl is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown side {side!r}; expected one of {sorted(_SIDE_TABLE)}",
        )
    return tbl


def _prefix_from_bukrs(bukrs, bukrs_map: dict | None = None) -> str | None:
    """Resolve an entity_prefix from a Buchungskreis.

    Prefers an optional per-project ``bukrs_map`` (future wizard buchungskreis
    field), else the canonical ``entities.BUKRS_TO_PREFIX``.  Byte-identical no-op
    when ``bukrs_map`` is None — the only caller for now.  The two orderings differ,
    so this is the ONLY sanctioned bridge (never derive the prefix arithmetically).
    """
    if bukrs_map:
        got = bukrs_map.get(bukrs)
        if got:
            return got
    return BUKRS_TO_PREFIX.get(bukrs)


def _inject_partner_id(out: dict, prefix) -> None:
    # Canonical identity: when Buchungskreis is mapped, the entity_prefix is
    # resolved from it via entities.BUKRS_TO_PREFIX (the two orderings differ;
    # never derive one arithmetically). Otherwise fall back to the prefix the
    # generic builder resolved (per_entity / entity_column) so the existing
    # pass-through API is unchanged.
    for f in _INT_FIELDS:
        v = out.get(f)
        out[f] = int(round(v)) if v is not None else None
    bukrs = out.get("buchungskreis")
    if bukrs is not None:
        resolved = _prefix_from_bukrs(bukrs, bukrs_map=None)
        if resolved:
            out["entity_prefix"] = resolved
            prefix = resolved
    # partner_no (Debitor/Kreditor); partner_key = entity_prefix(2) || partner_no
    partner_no = out.get("partner_no")
    out["partner_key"] = f"{prefix}{partner_no}" if (prefix and partner_no) else None
    # partner_id = entity_prefix(2) || konto — legacy join-key shape (unchanged).
    konto = out.get("konto")
    out["partner_id"] = f"{prefix}{konto}" if (prefix and konto) else None
    out["is_open"] = None       # F3: settlement matching NOT implemented
    out["aging_band"] = None    # F4: bucketing NOT implemented (reuse AR_BANDS)


def _commit_side_rows(
    session: Session,
    admin: User,
    *,
    table: str,
    df,
    column_map: dict[str, str],
    entity_mode: str,
    entity_column: str | None,
    entity_prefix: str | None,
    fy_label: str,
    file_id: str,
    project_id: str,
) -> tuple[int, set[str]]:
    """Map ``df`` -> ``table`` rows and INSERT them (single source of truth).

    Shared by ``/{side}/commit`` and ``/combined/commit``. Runs the mapped-row
    build, the per-row Buchungskreis/partner-id injection, and the fail-closed
    visibility asserts, then executes the INSERT on the session. Does NOT commit
    or roll back — the caller owns the transaction boundary so multiple sides can
    be committed atomically. Returns ``(inserted, distinct_entity_prefixes)``.
    """
    rows, _prefixes = di.build_mapped_rows(
        df,
        column_map=column_map,
        number_fields=_NUMBER_FIELDS,
        date_fields=_DATE_FIELDS,
        entity_mode=entity_mode,
        entity_column=entity_column,
        entity_prefix=entity_prefix,
        fy_label=fy_label,
        file_id=file_id,
        extra_per_row=_inject_partner_id,
    )

    # The per-row hook may have overridden entity_prefix from Buchungskreis, so
    # recompute the distinct-prefix set from the final rows before visibility checks.
    prefixes = {str(r.get("entity_prefix")) for r in rows if r.get("entity_prefix")}

    di.assert_prefixes_visible(session, admin, prefixes)
    di.assert_entities_resolved(session, admin, rows, entity_column=entity_column)

    if not rows:
        return 0, prefixes

    cols = list(_INSERT_COLUMNS)
    placeholders = ", ".join(f":{c}" for c in cols)
    insert_sql = text(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})")
    params = []
    for r in rows:
        rec = {c: r.get(c) for c in cols}
        rec["project_id"] = project_id
        params.append(rec)

    session.execute(insert_sql, params)
    return len(rows), prefixes


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class OposUploadResponse(BaseModel):
    file_id: str
    filename: str
    sheets: list[str] = []
    columns: list[str]
    sample: list[dict]


class OposPreviewRequest(BaseModel):
    file_id: str
    sheet: str | None = None


class OposPreviewResponse(BaseModel):
    file_id: str
    sheets: list[str] = []
    sheet: str | None = None
    columns: list[str]
    sample: list[dict]


class OposCommitRequest(BaseModel):
    file_id: str
    sheet: str | None = None
    fy_label: str = Field(..., max_length=20)
    entity_mode: str = "per_entity"
    entity_prefix: str | None = Field(None, max_length=2)
    entity_column: str | None = None  # Buchungskreis (combined mode)
    project_id: str = Field("default", max_length=64)
    # target field -> source column header. Recognized targets:
    #   konto, belegart, beleg_no, referenz, net_due_date,
    #   amount_hauswaehrung, posting_date
    column_map: dict[str, str] = Field(default_factory=dict)


class OposCommitResponse(BaseModel):
    side: str
    inserted: int
    fy_label: str
    entity_prefixes: list[str]
    project_id: str


class OposCombinedCommitRequest(BaseModel):
    file_id: str
    sheet: str | None = None
    fy_label: str = Field(..., max_length=20)
    entity_mode: str = "per_entity"
    entity_prefix: str | None = Field(None, max_length=2)
    entity_column: str | None = None  # Buchungskreis (combined entity mode)
    project_id: str = Field("default", max_length=64)
    # Same target-field -> source-column mapping applied to BOTH sides.
    column_map: dict[str, str] = Field(default_factory=dict)
    # Side discriminator: which source column identifies debitor vs kreditor rows,
    # and the value in that column meaning each side.
    side_column: str = Field(..., min_length=1)
    debitor_value: str = Field(..., min_length=1)
    kreditor_value: str = Field(..., min_length=1)


class OposCombinedCommitResponse(BaseModel):
    debitor_inserted: int
    kreditor_inserted: int
    skipped_unmatched: int
    fy_label: str
    entity_prefixes: list[str]
    project_id: str


# --------------------------------------------------------------------------- #
# POST /{side}/upload
# --------------------------------------------------------------------------- #
@router.post("/{side}/upload", response_model=OposUploadResponse)
async def upload(
    side: str = PathParam(...),
    file: UploadFile = File(...),
    _user: User = Depends(current_user),
) -> OposUploadResponse:
    """Stage an OPOS file (debitor|kreditor); return a sheet/column preview."""
    _table(side)
    return OposUploadResponse(**await di.save_upload(file))


# --------------------------------------------------------------------------- #
# POST /{side}/preview
# --------------------------------------------------------------------------- #
@router.post("/{side}/preview", response_model=OposPreviewResponse)
def preview(
    body: OposPreviewRequest,
    side: str = PathParam(...),
    _user: User = Depends(current_user),
) -> OposPreviewResponse:
    """Read a staged OPOS file's sheet/columns/sample (no DB write)."""
    _table(side)
    return OposPreviewResponse(**di.preview(body.file_id, body.sheet))


# --------------------------------------------------------------------------- #
# POST /combined/commit  — ONE file, both sides, split on a discriminator column
# NOTE: registered BEFORE /{side}/commit so the literal "combined" path is not
# swallowed by the {side} path parameter (Starlette matches in declaration order).
# --------------------------------------------------------------------------- #
@router.post("/combined/commit", response_model=OposCombinedCommitResponse)
def commit_combined(
    body: OposCombinedCommitRequest,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
) -> OposCombinedCommitResponse:
    """Ingest ONE combined OPOS file: split rows on ``side_column`` into debitor
    (AR) and kreditor (AP), then INSERT each into its fact table in a SINGLE
    transaction. Rows whose ``side_column`` matches NEITHER value are dropped and
    counted as ``skipped_unmatched``. Pass-through only (is_open/aging_band NULL).
    """
    column_map = {k: v for k, v in body.column_map.items() if k in _TARGET_FIELDS}
    if not column_map:
        raise HTTPException(
            status_code=422,
            detail=f"column_map must map at least one of: {sorted(_TARGET_FIELDS)}",
        )

    deb_val = body.debitor_value.strip()
    kre_val = body.kreditor_value.strip()
    if deb_val == kre_val:
        raise HTTPException(
            status_code=422,
            detail="debitor_value and kreditor_value must differ.",
        )

    df = di.load_staged(body.file_id, body.sheet)
    if body.side_column not in df.columns:
        raise HTTPException(
            status_code=422,
            detail=f"side_column {body.side_column!r} not found in file columns.",
        )

    # Robust trimmed string compare (source cells are string-typed; None -> "").
    side_norm = df[body.side_column].map(
        lambda v: "" if v is None else str(v).strip()
    )
    is_deb = side_norm == deb_val
    is_kre = side_norm == kre_val
    df_deb = df[is_deb]
    df_kre = df[is_kre]
    skipped_unmatched = int((~(is_deb | is_kre)).sum())

    if df_deb.empty and df_kre.empty:
        raise HTTPException(
            status_code=422,
            detail=(
                f"No rows in column {body.side_column!r} matched debitor_value "
                f"{deb_val!r} or kreditor_value {kre_val!r}."
            ),
        )

    try:
        deb_inserted, deb_prefixes = _commit_side_rows(
            session, admin,
            table=_SIDE_TABLE["debitor"],
            df=df_deb,
            column_map=column_map,
            entity_mode=body.entity_mode,
            entity_column=body.entity_column,
            entity_prefix=body.entity_prefix,
            fy_label=body.fy_label,
            file_id=body.file_id,
            project_id=body.project_id,
        )
        kre_inserted, kre_prefixes = _commit_side_rows(
            session, admin,
            table=_SIDE_TABLE["kreditor"],
            df=df_kre,
            column_map=column_map,
            entity_mode=body.entity_mode,
            entity_column=body.entity_column,
            entity_prefix=body.entity_prefix,
            fy_label=body.fy_label,
            file_id=body.file_id,
            project_id=body.project_id,
        )
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.exception("opos combined commit failed (file_id=%s)", body.file_id)
        raise HTTPException(
            status_code=500, detail="OPOS combined commit failed; check server logs."
        ) from exc

    prefixes = deb_prefixes | kre_prefixes
    return OposCombinedCommitResponse(
        debitor_inserted=deb_inserted,
        kreditor_inserted=kre_inserted,
        skipped_unmatched=skipped_unmatched,
        fy_label=body.fy_label,
        entity_prefixes=sorted(p for p in prefixes if p),
        project_id=body.project_id,
    )


# --------------------------------------------------------------------------- #
# POST /{side}/commit
# --------------------------------------------------------------------------- #
@router.post("/{side}/commit", response_model=OposCommitResponse)
def commit(
    body: OposCommitRequest,
    side: str = PathParam(...),
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
) -> OposCommitResponse:
    """Store PASS-THROUGH OPOS postings. is_open + aging_band remain NULL."""
    table = _table(side)
    column_map = {k: v for k, v in body.column_map.items() if k in _TARGET_FIELDS}
    if not column_map:
        raise HTTPException(
            status_code=422,
            detail=f"column_map must map at least one of: {sorted(_TARGET_FIELDS)}",
        )

    df = di.load_staged(body.file_id, body.sheet)

    try:
        inserted, prefixes = _commit_side_rows(
            session, admin,
            table=table,
            df=df,
            column_map=column_map,
            entity_mode=body.entity_mode,
            entity_column=body.entity_column,
            entity_prefix=body.entity_prefix,
            fy_label=body.fy_label,
            file_id=body.file_id,
            project_id=body.project_id,
        )
        session.commit()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.exception("opos commit failed (side=%s file_id=%s)", side, body.file_id)
        raise HTTPException(
            status_code=500, detail="OPOS commit failed; check server logs."
        ) from exc

    return OposCommitResponse(
        side=side,
        inserted=inserted,
        fy_label=body.fy_label,
        entity_prefixes=sorted(p for p in prefixes if p),
        project_id=body.project_id,
    )


# --------------------------------------------------------------------------- #
# POST /{side}/derive-aging  — EXPLICIT STUB (NOT IMPLEMENTED)
# --------------------------------------------------------------------------- #
def derive_aging(session: Session, side: str, project_id: str = "default") -> list[dict]:
    """FLAG — DRAFT STUB. Open-item + aging derivation is NOT implemented.

    When implemented (financial-calculation-engineer epic) this MUST:
      F3) determine ``is_open`` via settlement matching (RV invoice vs ZA payment
          by beleg_no / referenz) — TBD.
      F4) set ``aging_band`` by REUSING ``app.services.gl_aging.AR_BANDS`` and
          ``app.services.gl_aging._band_case_sql`` (do NOT invent new buckets);
          golden shape: backend/golden/v2/sales__{receivables,payables}-aging__*.json.

    No value is computed in this epic — see docs/financial-logic.md (F3, F4).
    """
    raise NotImplementedError(
        "OPOS open-item / aging derivation is not implemented (DRAFT). "
        "F3 settlement matching + F4 aging bucketing (reuse gl_aging.AR_BANDS) "
        "are deferred to the financial-calculation-engineer epic."
    )


@router.post("/{side}/derive-aging")
def derive_aging_endpoint(
    side: str = PathParam(...),
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> dict:
    """FLAG — DRAFT STUB endpoint. Returns 501; aging is NOT computed (F3/F4)."""
    _table(side)
    # FLAG: explicit not-implemented surface; see derive_aging() docstring.
    raise HTTPException(
        status_code=501,
        detail=(
            "OPOS aging derivation is not implemented (DRAFT). F3 open-item "
            "matching + F4 aging bucketing (reuse gl_aging.AR_BANDS) are deferred."
        ),
    )
