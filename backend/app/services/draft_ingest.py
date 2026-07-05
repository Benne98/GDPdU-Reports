"""Shared DRAFT ingest helpers for the D3 (Anlagen) + D4 (OPOS) pass-through facts.

These endpoints mirror the GL ingest shape (upload -> preview -> column-mapping
commit) but write RAW PASS-THROUGH rows into the new DRAFT fact tables.  No
financial value is derived here — see ``docs/financial-logic.md`` (F1–F4).

To avoid drift, the upload/preview/parse plumbing REUSES the existing GL ingest
helpers (``app.routers.ingest``): the same staged-file store (UPLOAD_DIR), the
same dialect sniffing and the same delimited/xlsx loader.  Tenant scoping mirrors
``app.routers.masters`` (restricted users may only write their entity prefixes).
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from fastapi import HTTPException, UploadFile
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import User
from app.services.entity_visibility import visible_entity_codes

# Reuse the GL ingest plumbing so the staged-file store / dialect / loader cannot
# drift between the GL path and these DRAFT paths.
from app.routers.ingest import (
    ALLOWED_EXTENSIONS,
    MAX_UPLOAD_BYTES,
    UPLOAD_DIR,
    _assert_parse_bounds,
    _detect_dialect,
    _file_path_from_id,
    _get_sheets,
    _load_file,
    _safe_filename,
    _sweep_stale_uploads,
)
from app.config import settings
from etl.transform import normalize_prefix

_SAMPLE_ROWS = 10


# --------------------------------------------------------------------------- #
# Upload + preview (no DB write)
# --------------------------------------------------------------------------- #
async def save_upload(file: UploadFile) -> dict[str, Any]:
    """Persist an uploaded register/OPOS file under UPLOAD_DIR; return a preview.

    Mirrors ``ingest.upload_file`` (size + extension gate, UUID-prefixed name, no
    path traversal) but is intentionally minimal (no headerless re-staging) — the
    DRAFT registers are spreadsheet-style master exports with a real header row.
    """
    raw_bytes = await file.read()
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_upload_mb} MB limit")

    safe_name = _safe_filename(file.filename or "upload")
    ext = Path(safe_name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type {ext!r}. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    _sweep_stale_uploads()
    file_id = uuid.uuid4().hex[:16]
    dest = UPLOAD_DIR / f"{file_id}_{safe_name}"
    dest.write_bytes(raw_bytes)

    dialect = _detect_dialect(raw_bytes, ext)
    sheets = _get_sheets(dest)
    effective_sheet = sheets[0] if sheets else None
    try:
        df = _load_file(dest, effective_sheet, dialect)
    except Exception as exc:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"Could not parse file: {exc}") from exc

    return {
        "file_id": file_id,
        "filename": safe_name,
        "sheets": sheets,
        "columns": list(df.columns),
        "sample": df.head(_SAMPLE_ROWS).fillna("").to_dict(orient="records"),
    }


def preview(file_id: str, sheet: Optional[str]) -> dict[str, Any]:
    """Read a staged file's sheet/columns/sample (no DB write)."""
    path = _file_path_from_id(file_id)
    raw_bytes = path.read_bytes()
    dialect = _detect_dialect(raw_bytes, path.suffix.lower())
    sheets = _get_sheets(path)
    effective_sheet = sheet or (sheets[0] if sheets else None)
    try:
        df = _load_file(path, effective_sheet, dialect)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not parse file: {exc}") from exc
    _assert_parse_bounds(df, what=f"file {file_id!r}")
    return {
        "file_id": file_id,
        "sheets": sheets,
        "sheet": effective_sheet,
        "columns": list(df.columns),
        "sample": df.head(_SAMPLE_ROWS).fillna("").to_dict(orient="records"),
    }


def load_staged(file_id: str, sheet: Optional[str]) -> pd.DataFrame:
    """Load a staged file to a string-typed frame (same loader as preview)."""
    path = _file_path_from_id(file_id)
    raw_bytes = path.read_bytes()
    dialect = _detect_dialect(raw_bytes, path.suffix.lower())
    sheets = _get_sheets(path)
    effective_sheet = sheet or (sheets[0] if sheets else None)
    try:
        df = _load_file(path, effective_sheet, dialect)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not parse file: {exc}") from exc
    _assert_parse_bounds(df, what=f"file {file_id!r}")
    return df


# --------------------------------------------------------------------------- #
# Value coercion (pass-through normalization only — NO financial computation)
# --------------------------------------------------------------------------- #
def to_number(value: Any) -> Optional[float]:
    """Lenient numeric pass-through: source string -> float, else None.

    German thousands/decimal ('1.234,56') are normalized to a plain float. This is
    NORMALIZATION of a single source value, not a financial calculation.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s.replace("\xa0", "").replace(" ", "")
    if "," in s and "." in s:  # 1.234,56 -> 1234.56
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:  # 1234,56 -> 1234.56
        s = s.replace(",", ".")
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def to_date(value: Any) -> Optional[str]:
    """Lenient date pass-through -> ISO 'YYYY-MM-DD' string, else None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    ts = pd.to_datetime(s, errors="coerce", dayfirst=True)
    if pd.isna(ts):
        return None
    return ts.date().isoformat()


def resolve_entity_prefix(value: Any) -> Optional[str]:
    """Best-effort 2-char entity prefix from a source 'Buchungskreis' cell.

    Numeric values go through ``etl.transform.normalize_prefix`` (zero-padded to
    2). Non-numeric labels fall back to the left 2 characters. None on empty.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return normalize_prefix(s)
    except ValueError:
        return s[:2]


# --------------------------------------------------------------------------- #
# Tenant scoping (mirrors app.routers.masters)
# --------------------------------------------------------------------------- #
def visible_prefixes_or_none(session: Session, user: User) -> Optional[set[str]]:
    """Allowed 2-char entity prefixes for *user*, or None for admin/unrestricted."""
    allowed_codes = visible_entity_codes(session, user)
    if allowed_codes is None:
        return None
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


def assert_prefixes_visible(session: Session, user: User, prefixes: set[str]) -> None:
    """Fail-closed: a restricted user may only write within their entity prefixes."""
    allowed = visible_prefixes_or_none(session, user)
    if allowed is None:
        return
    blocked = {p for p in prefixes if p and p[:2] not in allowed}
    if blocked:
        raise HTTPException(
            status_code=403,
            detail=f"Not permitted to write data for entity prefix(es): {sorted(blocked)}",
        )


def assert_entities_resolved(
    session: Session,
    user: User,
    rows: list[dict[str, Any]],
    *,
    entity_column: Optional[str],
) -> None:
    """Fail-closed: a restricted caller may not commit NULL-entity rows.

    Rows with an unresolved/blank ``entity_prefix`` carry no tenant scope and would
    escape ``role_entity_visibility`` filtering (which excludes NULL prefixes). A
    restricted caller (``visible_prefixes_or_none`` -> a concrete set) is therefore
    rejected. An unrestricted admin (-> None, the masters.py "see-all" convention)
    may legitimately persist unattributed rows, so they pass through unchanged.

    Safe to call for both entity modes: ``per_entity`` always injects a validated
    prefix, so ``unresolved`` is 0 there.
    """
    if visible_prefixes_or_none(session, user) is None:
        return  # admin / unrestricted — unattributed rows are permitted
    unresolved = sum(1 for r in rows if not str(r.get("entity_prefix") or "").strip())
    if unresolved:
        raise HTTPException(
            status_code=422,
            detail=f"{unresolved} rows have no resolvable entity in column {entity_column!r}.",
        )


# --------------------------------------------------------------------------- #
# Generic mapped-frame builder
# --------------------------------------------------------------------------- #
def build_mapped_rows(
    df: pd.DataFrame,
    *,
    column_map: dict[str, str],
    number_fields: set[str],
    date_fields: set[str],
    entity_mode: str,
    entity_column: Optional[str],
    entity_prefix: Optional[str],
    fy_label: str,
    file_id: str,
    extra_per_row: Optional[Any] = None,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Map source columns to target fields, returning (rows, distinct_prefixes).

    ``entity_mode``:
      * 'per_entity' — a single normalized ``entity_prefix`` is injected on every
        row (2-char, validated via normalize_prefix).
      * 'combined'   — the per-row prefix is resolved from ``entity_column``.

    Each output row carries lineage (``source_file_id``, ``row_no``), scope
    (``entity_prefix``, ``fy_label``) and the mapped pass-through fields. No
    derived columns are produced.  ``extra_per_row(row_dict, prefix)`` may inject
    side-specific fields (e.g. OPOS partner_id).
    """
    if entity_mode not in ("per_entity", "combined"):
        raise HTTPException(status_code=422, detail=f"Unknown entity_mode {entity_mode!r}")

    fixed_prefix: Optional[str] = None
    if entity_mode == "per_entity":
        if not entity_prefix:
            raise HTTPException(status_code=422, detail="per_entity mode requires entity_prefix")
        try:
            fixed_prefix = normalize_prefix(entity_prefix)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        if not entity_column:
            raise HTTPException(status_code=422, detail="combined mode requires entity_column")
        if entity_column not in df.columns:
            raise HTTPException(
                status_code=422,
                detail=f"entity_column {entity_column!r} not found in file columns",
            )

    # Validate mapped source columns exist.
    missing = [src for src in column_map.values() if src and src not in df.columns]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Mapped source column(s) not found in file: {sorted(set(missing))}",
        )

    rows: list[dict[str, Any]] = []
    prefixes: set[str] = set()
    records = df.to_dict(orient="records")
    for idx, src_row in enumerate(records):
        if entity_mode == "per_entity":
            prefix = fixed_prefix
        else:
            prefix = resolve_entity_prefix(src_row.get(entity_column))
        if prefix:
            prefixes.add(prefix)

        out: dict[str, Any] = {
            "source_file_id": file_id,
            "row_no": idx,
            "entity_prefix": prefix,
            "fy_label": str(fy_label),
            "dataset_version_id": None,
        }
        for target, src_col in column_map.items():
            if not src_col:
                out[target] = None
                continue
            val = src_row.get(src_col)
            if target in number_fields:
                out[target] = to_number(val)
            elif target in date_fields:
                out[target] = to_date(val)
            else:
                sval = None if val is None else str(val).strip()
                out[target] = sval or None
        if extra_per_row is not None:
            extra_per_row(out, prefix)
        rows.append(out)
    return rows, prefixes
