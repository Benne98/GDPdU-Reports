"""P1a + P1f — GL ingestion API endpoints.

Prefix: /api/v1/ingest

Endpoints:
  POST /upload              Upload a GL file; return dialect + header preview.
  GET  /profiles            List all mapping profiles.
  POST /profiles            Create a mapping profile.
  GET  /profiles/{id}       Get one mapping profile by id.
  POST /validate            Validate file + profile (no DB write).
  POST /commit              Validate + load into canonical DB.
  GET  /fiscal-years        Distinct FYs in GL data (mapping wizard).
  POST /entity/preview      Resolve entity labels to prefixes (wizard).
  POST /mapping/preview     Read-only BS/PL Master mapping preview.
  POST /mapping/commit      Load account mapping (generic or bs_pl_master).
  GET  /runs                List recent meta_dataset_load records.

Design rules (CLAUDE.md):
  - Routers are thin; logic lives in etl.*
  - Explicit Pydantic models for request + response bodies.
  - No path traversal: uploads land under UPLOAD_DIR with a UUID prefix.
  - No secrets in logs or responses; stack traces are stripped from user-facing errors.
  - DB session via Depends(get_session).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# etl/ lives at the repo root, one level above backend/.
# Add repo root to sys.path so `etl` is importable when the backend
# is launched from the backend/ directory (e.g. `uvicorn app.main:app`).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import User, current_user, require_admin
from app.config import settings
from app.db import get_session
from etl import checks as C
from etl import derive as D
from etl.classify_config import OVERRIDES as _CLASSIFY_OVERRIDES, DEFAULT_RULES, build_account_classes
from etl.derive import extract_partners as _extract_partners
from etl.load import (
    content_hash,
    dedup_check,
    delete_orphan_account_mappings,
    load_account_mapping,
    load_canonical,
    load_legal_entity,
    load_partners,
    split_entry_line,
)
from etl.derive import classify_with_rules as _classify_with_rules
from etl.mapping import MappingProfile, apply_profile, profile_from_dict, profile_to_dict
from etl.bs_pl_master import (
    BS_PL_REPLACE_MODES,
    build_mapping_frames,
    filter_mapping_append_only,
    is_bs_pl_master_workbook,
    preview_stats,
    read_bs_pl_master,
)
from etl.mapping_account import AccountMappingProfile, REQUIRED_FIELDS as _AM_REQUIRED, account_profile_from_dict, apply_account_mapping

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------  upload dir
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent  # backend/
UPLOAD_DIR = _BACKEND_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

MAX_UPLOAD_BYTES = settings.max_upload_mb * 1024 * 1024
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls"}

# H2: OB / partner-master files are small (a Sachkontenstamm / opening-balance
# extract is a few hundred rows). Cap them WELL below the large GL-ingest limit so
# an oversized body for these staging endpoints cannot buffer the GL ceiling worth
# of memory.  GL itself keeps MAX_UPLOAD_BYTES.
OB_PARTNER_MAX_UPLOAD_BYTES = 32 * 1024 * 1024  # 32 MiB
# Chunk size for the streamed, abort-early upload read (H2).
_UPLOAD_CHUNK_BYTES = 1024 * 1024  # 1 MiB
# L1: TTL for abandoned staged uploads — swept on each upload so previewed-but-
# never-committed files (real client data) do not accumulate on disk.
_UPLOAD_TTL_SECONDS = 6 * 60 * 60  # 6h

# H1: parse ceilings for the OB / partner upload+commit parse paths. GL/OB ledgers
# can be larger than a budget template, so the row ceiling is generous; the column
# ceiling bounds a dimension/zip bomb. Applied to the loaded frame BEFORE any
# transform/write — a violation is a clean 422.
MAX_PARSE_ROWS = 20_000
MAX_PARSE_COLS = 120

# Opening-balance tagging (matches etl.opening_balance / etl.gobd_gl_prepare /
# etl.checks._OPENING_ENTRY_TYPES so the balance checks exempt these rows and the
# BS layer reads them as opening stock).
OB_ENTRY_TYPE = "opening_balance"
OB_FISCAL_PERIOD = 0
# M1: synthetic booking_line_id band for the FILE-OB commit path. This is
# DELIBERATELY DISTINCT from the two other synthetic OB conventions so the three
# can coexist without colliding:
#   * load-path real lines       — 1-based source-row positions (far below any band)
#   * file-OB (this router)       — 800_000_000_000 band, JEGN '<EE>9<acct>'
#                                   (the leading-'9' file convention of
#                                    etl.gobd_gl_prepare._synthetic_opening_txn,
#                                    tagged by etl.opening_balance._mode_file)
#   * carry-forward OB (synthesized) — 900_000_000_000 band
#                                   (etl.opening_balance._SYNTHETIC_BID_BASE),
#                                   JEGN '<EE>8<YY><acct>'
# It does NOT mirror the carry-forward base; it is a separate reserved band.
_OB_BID_BASE = 800_000_000_000

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class UploadResponse(BaseModel):
    file_id: str
    filename: str
    sheets: list[str] = []
    columns: list[str]
    sample: list[dict]
    dialect: dict


class ProfileCreate(BaseModel):
    name: str = Field(..., max_length=200)
    source_system: str = Field("unknown", max_length=80)
    profile_json: dict


class ProfileResponse(BaseModel):
    id: int
    name: str
    source_system: str
    profile_json: dict
    created_at: str


class ValidateRequest(BaseModel):
    file_id: str
    sheet: str | None = None
    profile: dict  # serialised MappingProfile dict
    exclude_line_ids: list[int] = Field(default_factory=list)


class ExclusionSuggestionOut(BaseModel):
    count: int
    reason: str
    line_ids: list[int] = []


class ExclusionsOut(BaseModel):
    active_count: int = 0
    active_line_ids: list[int] = []
    suggested: ExclusionSuggestionOut | None = None


class CheckResultOut(BaseModel):
    id: str
    name: str
    severity: str
    passed: bool
    detail: str = ""
    diff: float | None = None
    offenders: list = []
    offender_count: int | None = None
    issue_groups: list | None = None


class IssueRowsRequest(BaseModel):
    file_id: str
    sheet: str | None = None
    profile: dict
    exclude_line_ids: list[int] = Field(default_factory=list)
    check_id: str = "S1"
    field: str
    search: str | None = None
    limit: int = Field(10_000, ge=1, le=50_000)
    offset: int = Field(0, ge=0)


class IssueRowsStatsOut(BaseModel):
    row_count: int
    amount_sum: float | None = None
    fiscal_years: list[int] = Field(default_factory=list)


class IssueRowsResponse(BaseModel):
    stats: IssueRowsStatsOut
    columns: list[str] = Field(default_factory=list)
    rows: list[dict] = Field(default_factory=list)
    total: int = 0


class ValidateResponse(BaseModel):
    summary: dict
    key_preview: list[dict]
    unmapped_accounts: list[str]
    results: list[CheckResultOut]
    exclusions: ExclusionsOut = Field(default_factory=ExclusionsOut)


class CommitRequest(BaseModel):
    file_id: str
    sheet: str | None = None
    profile: dict
    dataset: str = "gl"
    confirm_soft: bool = False
    exclude_line_ids: list[int] = Field(default_factory=list)
    commit_mode: str = "replace"  # replace | append
    # Reporting-v2 Phase 7 — post-commit rebuild mode (only used when
    # settings.rebuild_on_commit is ON; default OFF keeps the live stack unchanged).
    #   'auto'        -> incremental for a pure append on already-mapped accounts,
    #                    full otherwise (first-time / mapping changed / replace).
    #   'incremental' -> force the mapping-free monthly-update path.
    #   'full'        -> force the full deterministic rebuild.
    rebuild_mode: str = "auto"
    project_id: str = "default"


class CommitResponse(BaseModel):
    load_id: int | None = None
    entries: int
    lines: int
    ar: int
    ap: int
    sales: int
    com: int
    skipped: int
    loaded_at: str
    commit_mode: str = "replace"


class RunRecord(BaseModel):
    load_id: int
    dataset: str
    legal_entity_code: str | None
    fiscal_year: int | None
    row_count: int | None
    content_hash: str | None
    loaded_at: str
    loaded_by: str | None
    scope_entity_prefixes: list[str] = Field(default_factory=list)
    scope_fiscal_years: list[int] = Field(default_factory=list)
    commit_mode: str | None = None
    snapshot_captured: bool = False
    restored_from_load_id: int | None = None


class VersionDetailOut(RunRecord):
    snapshot_counts: dict[str, int] = Field(default_factory=dict)


class RestoreRequest(BaseModel):
    confirm: bool = False


class RestoreResponse(BaseModel):
    load_id: int
    restored_from_load_id: int
    dataset: str
    scope_entity_prefixes: list[str]
    scope_fiscal_years: list[int]
    snapshot_counts: dict[str, int] = Field(default_factory=dict)


class MappingCommitRequest(BaseModel):
    """Request body for POST /mapping/commit."""

    file_id: str
    sheet: str | None = None
    profile: dict | None = None  # serialised AccountMappingProfile dict (generic mode)
    header_row: int | None = None
    format: str = "generic"  # "generic" | "bs_pl_master"
    fiscal_years: list[int] | None = None
    replace_mode: str = "append"  # "replace" | "append" (bs_pl_master only)


class MappingPreviewRequest(BaseModel):
    file_id: str
    format: str = "bs_pl_master"
    fiscal_years: list[int]
    replace_mode: str = "append"


class MappingPreviewResponse(BaseModel):
    row_count_total: int
    row_count_bs: int
    row_count_pl: int
    fiscal_years: list[int]
    entity_prefixes: list[str]
    entities: list[dict]
    replace_mode: str = "append"
    would_update: int
    would_insert: int
    would_skip: int = 0
    would_delete: int = 0
    duplicate_keys: list[dict]
    duplicate_details: list[dict] = []
    insert_details: list[dict] = []
    delete_details: list[dict] = []
    sample_rows: list[dict]
    skipped_entities: list[str] = []
    skipped_rows: int = 0
    warnings: list[str] = []
    blockers: list[str]


class MappingCommitResponse(BaseModel):
    load_id: int | None = None
    accounts: int
    na: int
    cf: int
    deleted: int = 0
    skipped: int = 0
    loaded_at: str
    commit_mode: str = "replace"


class FiscalYearsResponse(BaseModel):
    fiscal_years: list[int]


class EntityPreviewRequest(BaseModel):
    file_id: str
    sheet: str | None = None
    entity: dict
    entity_assignments: dict[str, str] = Field(default_factory=dict)


class EntityMappingRow(BaseModel):
    source_label: str
    entity_prefix: str
    entity_name: str
    status: str
    row_count: int = 0


class EntityPreviewResponse(BaseModel):
    mappings: list[EntityMappingRow]
    needs_confirmation: bool
    all_resolved: bool
    proposed_assignments: dict[str, str] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #
_RE_DOTFLOAT = re.compile(r"\.0$")


def _safe_filename(name: str) -> str:
    """Strip path separators and control characters from an uploaded filename."""
    return re.sub(r'[\\/:*?"<>|]', "_", Path(name).name)[:200]


_VALID_REBUILD_MODES = {"auto", "full", "incremental"}


def _select_rebuild_mode(
    session: Session,
    requested: str,
    commit_mode: str,
    scope: tuple[list[str], list[int]],
) -> str:
    """Choose the post-commit rebuild mode (reporting-v2 Phase 7).

    'full' / 'incremental' are honoured verbatim.  'auto' (the default) picks:
      * 'incremental' for a PURE APPEND (commit_mode='append') whose accounts are
        ALREADY MAPPED in ``dim_gl_account`` for the scope — a routine monthly
        update needs no mapping/structure recompute.
      * 'full' otherwise (first-time load, replace, or any unmapped account) so
        classification + structure are (re)built.

    On any uncertainty (no scope, DB probe failure) we fall back to 'full' — the
    safe, behaviour-preserving choice.
    """
    if requested in ("full", "incremental"):
        return requested
    if requested != "auto":
        return "full"
    if commit_mode != "append":
        return "full"  # replace always rebuilds mapping/structure

    prefixes, years = scope
    if not prefixes or not years:
        return "full"

    # Incremental only when every (prefix, year) in scope already has mapped
    # accounts — i.e. this is a routine append into an existing mapped scope.
    try:
        row = session.execute(
            text(
                "SELECT COUNT(*) FROM dim_gl_account "
                "WHERE LEFT(account_number_group, 2) = ANY(:pfx) "
                "AND fiscal_year = ANY(:fys)"
            ),
            {"pfx": prefixes, "fys": [int(y) for y in years]},
        ).fetchone()
        mapped = int(row[0]) if row else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("commit: rebuild-mode probe failed (%s); defaulting to full", exc)
        return "full"
    return "incremental" if mapped > 0 else "full"


def _detect_encoding(raw_bytes: bytes) -> str:
    """Best-effort encoding detection without external dependencies.

    Checks for BOM markers first, then falls back to UTF-8 / latin-1.
    """
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if raw_bytes.startswith(b"\xff\xfe"):
        return "utf-16-le"
    if raw_bytes.startswith(b"\xfe\xff"):
        return "utf-16-be"
    # Try UTF-8 strict; fall back to latin-1 (never fails on arbitrary bytes)
    try:
        raw_bytes[:8192].decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "latin-1"


def _detect_dialect(raw_bytes: bytes, extension: str) -> dict:
    """Best-effort CSV dialect detection; returns safe defaults for xlsx."""
    if extension in (".xlsx", ".xls"):
        return {"format": "xlsx", "encoding": "n/a", "delimiter": "n/a",
                "decimal": ".", "thousands": ","}
    encoding = _detect_encoding(raw_bytes)
    sample_text = raw_bytes[:4096].decode(encoding, errors="replace")
    # Heuristic: count candidate delimiters in first lines
    candidates = {";": 0, ",": 0, "\t": 0, "|": 0}
    for line in sample_text.splitlines()[:5]:
        for ch in candidates:
            candidates[ch] += line.count(ch)
    delimiter = max(candidates, key=lambda c: candidates[c])
    # Decimal: if delimiter is ';' (German CSV) or tab, likely decimal=','
    decimal = "," if delimiter in (";", "\t") else "."
    thousands = "." if decimal == "," else ","
    return {
        "format": "csv",
        "encoding": encoding,
        "delimiter": delimiter,
        "decimal": decimal,
        "thousands": thousands,
    }


def _load_file(path: Path, sheet: str | None, dialect: dict, header: int | None = None) -> pd.DataFrame:
    """Load a CSV or XLSX file to a DataFrame."""
    ext = path.suffix.lower()
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(path, sheet_name=sheet or 0, dtype=str, na_values=[""], header=header if header is not None else 0)
    # CSV
    return pd.read_csv(
        path,
        sep=dialect.get("delimiter", ",") or ",",
        encoding=dialect.get("encoding", "utf-8") or "utf-8",
        dtype=str,
        na_values=[""],
    )


def _assert_parse_bounds(df: pd.DataFrame, *, what: str = "file") -> None:
    """H1: reject a parsed frame exceeding the OB/partner parse ceilings.

    Checks the loaded DataFrame's row/column counts BEFORE any transform or DB
    write so a crafted workbook (zip / dimension bomb) cannot blow up the parse.
    Raises a clean 422 on violation.  Mirrors
    ``app.services.budget_excel.assert_parse_bounds`` (which guards the openpyxl
    Workbook directly); here the equivalent guard runs on the pandas frame the
    OB/partner paths actually transform.
    """
    n_rows = int(len(df))
    n_cols = int(df.shape[1])
    if n_rows > MAX_PARSE_ROWS:
        raise HTTPException(
            status_code=422,
            detail=f"{what} has too many rows ({n_rows} > {MAX_PARSE_ROWS}).",
        )
    if n_cols > MAX_PARSE_COLS:
        raise HTTPException(
            status_code=422,
            detail=f"{what} has too many columns ({n_cols} > {MAX_PARSE_COLS}).",
        )


def _sweep_stale_uploads() -> None:
    """L1: best-effort TTL sweep of abandoned staged uploads in UPLOAD_DIR.

    Deletes files older than the TTL so previewed-but-never-committed files (which
    may hold real client data) do not accumulate.  Runs on each staging upload.
    Never raises — upload hygiene must not break the upload path itself.
    """
    cutoff = time.time() - _UPLOAD_TTL_SECONDS
    try:
        for p in UPLOAD_DIR.iterdir():
            try:
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
            except OSError:
                continue
    except Exception as exc:  # noqa: BLE001
        logger.warning("ingest upload TTL sweep failed: %s", type(exc).__name__)


def _get_sheets(path: Path) -> list[str]:
    if path.suffix.lower() in (".xlsx", ".xls"):
        try:
            # Close the handle (context manager) — on Windows a leaked ExcelFile
            # handle blocks a later unlink() of the staged file (WinError 32).
            with pd.ExcelFile(path) as xf:
                return list(xf.sheet_names)
        except Exception:
            return []
    return []


def _check_result_to_out(r: C.CheckResult) -> CheckResultOut:
    def _jsonify(obj):
        if isinstance(obj, dict):
            return {k: _jsonify(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_jsonify(v) for v in obj]
        if hasattr(obj, "item"):
            return obj.item()
        if isinstance(obj, float) and (obj != obj):  # NaN
            return None
        return obj

    return CheckResultOut(
        id=r.id,
        name=r.name,
        severity=r.severity,
        passed=r.passed,
        detail=r.detail,
        diff=r.diff,
        offenders=_jsonify(r.offenders[:20]),
        offender_count=r.offender_count,
        issue_groups=_jsonify(r.issue_groups) if getattr(r, "issue_groups", None) else None,
    )


def _file_path_from_id(file_id: str) -> Path:
    """Resolve a file_id to an upload path. Raises 404 if not found."""
    # file_id is the uuid prefix; find the matching file
    for p in UPLOAD_DIR.iterdir():
        if p.name.startswith(file_id + "_"):
            return p
    raise HTTPException(status_code=404, detail=f"file_id {file_id!r} not found")


def _load_and_apply(
    file_id: str,
    sheet: str | None,
    profile_dict: dict,
    session: Session | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Load file + apply profile; returns (canonical_lines, dialect)."""
    path = _file_path_from_id(file_id)
    raw_bytes = path.read_bytes()
    dialect = _detect_dialect(raw_bytes, path.suffix.lower())
    raw_df = _load_file(path, sheet, dialect)

    profile = profile_from_dict(profile_dict)
    # Fill decimal/thousands from dialect if not explicitly set in profile
    if profile.decimal == "." and dialect.get("decimal"):
        profile.decimal = dialect["decimal"]
    if profile.thousands == "," and dialect.get("thousands"):
        profile.thousands = dialect["thousands"]

    # Decidra GoBD: retain Jan-1 opening balances missing Transaction number
    from etl.gobd_gl_prepare import prepare_gobd_gl_frame, tag_opening_balances_in_canonical

    if "Entity No" in raw_df.columns and "Transaction number" in raw_df.columns:
        raw_df = prepare_gobd_gl_frame(raw_df)

    entity_lookup: dict[str, str] = {}
    if session is not None:
        from etl.entity_resolve import build_entity_lookup

        entity_lookup = build_entity_lookup(session)

    try:
        canonical = apply_profile(raw_df, profile, entity_lookup=entity_lookup)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if "_is_opening_balance" in raw_df.columns:
        canonical = tag_opening_balances_in_canonical(
            canonical, raw_df["_is_opening_balance"],
        )
    return canonical, dialect, raw_df


_APPLY_CACHE: dict[str, tuple[float, pd.DataFrame, dict, pd.DataFrame]] = {}
_APPLY_CACHE_TTL_SEC = 900
_APPLY_CACHE_MAX = 4


def _profile_cache_key(file_id: str, sheet: str | None, profile_dict: dict) -> str:
    payload = json.dumps(profile_dict, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:20]
    return f"{file_id}:{sheet or ''}:{digest}"


def _load_and_apply_cached(
    file_id: str,
    sheet: str | None,
    profile_dict: dict,
    session: Session | None = None,
) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    """Reuse parsed canonical + raw frame for repeated validate / issue-rows calls."""
    key = _profile_cache_key(file_id, sheet, profile_dict)
    now = time.monotonic()
    hit = _APPLY_CACHE.get(key)
    if hit and now - hit[0] < _APPLY_CACHE_TTL_SEC:
        return hit[1], hit[2], hit[3]

    result = _load_and_apply(file_id, sheet, profile_dict, session)
    _APPLY_CACHE[key] = (now, *result)
    if len(_APPLY_CACHE) > _APPLY_CACHE_MAX:
        oldest_key = min(_APPLY_CACHE, key=lambda k: _APPLY_CACHE[k][0])
        del _APPLY_CACHE[oldest_key]
    return result


def _serialize_cell(value) -> str | int | float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        try:
            return str(value.date()) if hasattr(value, "date") else str(value)
        except Exception:
            return str(value)
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value).strip()


def _source_row_at_line(raw_df: pd.DataFrame, booking_line_id: int) -> dict[str, str | int | float | None] | None:
    """Map 1-based booking_line_id to the aligned source row (same row order as ingest)."""
    try:
        pos = int(booking_line_id) - 1
    except (TypeError, ValueError):
        return None
    if pos < 0 or pos >= len(raw_df):
        return None
    row = raw_df.iloc[pos]
    return {str(k): _serialize_cell(row[k]) for k in raw_df.columns}


def _enrich_offenders_with_source_rows(
    raw_df: pd.DataFrame,
    results: list[C.CheckResult],
) -> None:
    """Attach full source-file columns to sample offender dicts (for validation UI drill-down)."""
    for result in results:
        enriched: list = []
        for off in result.offenders:
            if not isinstance(off, dict):
                enriched.append(off)
                continue
            item = dict(off)
            bid = item.get("booking_line_id")
            if bid is not None:
                source = _source_row_at_line(raw_df, bid)
                if source:
                    item["source_row"] = source
                    item["source_line_number"] = int(bid)
            enriched.append(item)
        result.offenders = enriched


# --------------------------------------------------------------------------- #
# POST /upload
# --------------------------------------------------------------------------- #
@router.post("/upload", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    sheet: str | None = Form(None),
    header_row: int | None = Form(None),
    _user: User = Depends(current_user),
) -> UploadResponse:
    """Save uploaded GL file; detect dialect + columns; return preview."""
    raw_bytes = await file.read()

    # --- size check
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {settings.max_upload_mb} MB limit",
        )

    # --- extension check (no path traversal)
    safe_name = _safe_filename(file.filename or "upload")
    ext = Path(safe_name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type {ext!r}. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    # --- save with UUID prefix
    file_id = uuid.uuid4().hex[:16]
    dest = UPLOAD_DIR / f"{file_id}_{safe_name}"
    dest.write_bytes(raw_bytes)

    # --- detect dialect
    dialect = _detect_dialect(raw_bytes, ext)

    # --- load preview
    sheets = _get_sheets(dest)
    try:
        df = _load_file(dest, sheet or (sheets[0] if sheets else None), dialect, header=header_row)
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"Could not parse file: {exc}") from exc

    columns = list(df.columns)
    sample = df.head(10).fillna("").to_dict(orient="records")

    return UploadResponse(
        file_id=file_id,
        filename=safe_name,
        sheets=sheets,
        columns=columns,
        sample=sample,
        dialect=dialect,
    )


# --------------------------------------------------------------------------- #
# GET /profiles — list all
# --------------------------------------------------------------------------- #
@router.get("/profiles", response_model=list[ProfileResponse])
def list_profiles(
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> list[ProfileResponse]:
    rows = session.execute(
        text("SELECT id, name, source_system, profile_json, created_at FROM ingest_mapping_profile ORDER BY id DESC")
    ).fetchall()
    return [
        ProfileResponse(
            id=r[0],
            name=r[1],
            source_system=r[2],
            profile_json=r[3],
            created_at=str(r[4]),
        )
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# POST /profiles — create
# --------------------------------------------------------------------------- #
@router.post("/profiles", response_model=ProfileResponse, status_code=201)
def create_profile(
    body: ProfileCreate,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> ProfileResponse:
    try:
        row = session.execute(
            text(
                "INSERT INTO ingest_mapping_profile (name, source_system, profile_json) "
                "VALUES (:name, :ss, :pj::jsonb) "
                "RETURNING id, name, source_system, profile_json, created_at"
            ),
            {"name": body.name, "ss": body.source_system, "pj": _json_dumps(body.profile_json)},
        ).fetchone()
        session.commit()
    except Exception as exc:
        session.rollback()
        _raise_db_error(exc, "create_profile")
    return ProfileResponse(
        id=row[0], name=row[1], source_system=row[2],
        profile_json=row[3], created_at=str(row[4]),
    )


# --------------------------------------------------------------------------- #
# GET /profiles/{id}
# --------------------------------------------------------------------------- #
@router.get("/profiles/{profile_id}", response_model=ProfileResponse)
def get_profile(
    profile_id: int,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> ProfileResponse:
    row = session.execute(
        text("SELECT id, name, source_system, profile_json, created_at FROM ingest_mapping_profile WHERE id = :id"),
        {"id": profile_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")
    return ProfileResponse(
        id=row[0], name=row[1], source_system=row[2],
        profile_json=row[3], created_at=str(row[4]),
    )


# --------------------------------------------------------------------------- #
# POST /validate
# --------------------------------------------------------------------------- #
@router.post("/validate", response_model=ValidateResponse)
def validate(
    body: ValidateRequest,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> ValidateResponse:
    """Apply profile + run full check catalog on staging (NO DB write)."""
    from etl.line_exclude import apply_line_exclusions, exclusion_summary

    canonical, _, raw_df = _load_and_apply_cached(body.file_id, body.sheet, body.profile, session)

    # Partner linking (method A by default from profile)
    strategy = body.profile.get("linking_strategy", "txn")
    linked_full = D.link_partners(canonical, strategy)
    exclusions_out = exclusion_summary(linked_full, body.exclude_line_ids)
    linked = apply_line_exclusions(linked_full, body.exclude_line_ids)

    # Derived facts
    fact_ar = D.derive_ar(linked)
    fact_ap = D.derive_ap(linked)
    fact_sales = D.derive_sales(linked)
    fact_com = D.derive_com(linked)

    # Checks
    required = ["journal_entry_group_number", "fiscal_year", "line_number",
                "booking_line_id", "account_number_group", "amount", "posting_date"]
    results: list[C.CheckResult] = [
        C.check_required_fields(linked, required),
        C.check_s2_posting_fiscal_year(linked),
        C.check_booking_balance(linked),
        C.check_monthly_balance(linked),
        C.check_ledger_balance(linked),
        C.check_unique_booking_line_id(linked),
        C.check_r1_ar(fact_ar, linked),
        C.check_r2_ap(fact_ap, linked),
        C.check_r3_sales(fact_sales, linked),
        C.check_r4_com(fact_com, linked),
    ]

    # M1: check mapping coverage via DB
    gl_accounts = linked["gl_account_id"].dropna().unique().tolist()
    try:
        db_accounts_rows = session.execute(
            text("SELECT DISTINCT gl_account_id FROM dim_gl_account")
        ).fetchall()
        db_accounts = [str(r[0]) for r in db_accounts_rows]
    except Exception:
        db_accounts = []
    results.append(C.check_unmapped_accounts(gl_accounts, db_accounts))

    _enrich_offenders_with_source_rows(raw_df, results)

    summary = C.summarize(results)
    unmapped = [r.offenders for r in results if r.id == "M1"]
    unmapped_raw = unmapped[0] if unmapped else []
    unmapped_flat = [
        str(o["account"]) if isinstance(o, dict) and "account" in o else str(o)
        for o in unmapped_raw
    ]

    # key preview
    key_cols = ["journal_entry_group_number", "fiscal_year", "account_number_group",
                "booking_line_id", "amount", "customer_id", "supplier_id"]
    preview_cols = [c for c in key_cols if c in linked.columns]
    key_preview = linked[preview_cols].head(10).fillna("").to_dict(orient="records")

    return ValidateResponse(
        summary={
            "passed": summary["passed"],
            "blocking": summary["blocking"],
            "warnings": summary["warnings"],
        },
        key_preview=key_preview,
        unmapped_accounts=unmapped_flat,
        results=[_check_result_to_out(r) for r in results],
        exclusions=ExclusionsOut(
            active_count=exclusions_out["active_count"],
            active_line_ids=exclusions_out["active_line_ids"],
            suggested=(
                ExclusionSuggestionOut(**exclusions_out["suggested"])
                if exclusions_out["suggested"]
                else None
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# POST /validate/issue-rows
# --------------------------------------------------------------------------- #
@router.post("/validate/issue-rows", response_model=IssueRowsResponse)
def validate_issue_rows(
    body: IssueRowsRequest,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> IssueRowsResponse:
    """Return flat source rows for one validation issue (S1 field failures)."""
    if body.check_id != "S1":
        raise HTTPException(status_code=400, detail=f"issue-rows not supported for check {body.check_id!r}")

    from etl.issue_rows import collect_s1_issue_rows
    from etl.line_exclude import apply_line_exclusions

    canonical, _, raw_df = _load_and_apply_cached(body.file_id, body.sheet, body.profile, session)
    strategy = body.profile.get("linking_strategy", "txn")
    linked = D.link_partners(canonical, strategy)
    linked = apply_line_exclusions(linked, body.exclude_line_ids)

    out = collect_s1_issue_rows(
        linked,
        raw_df,
        body.field,
        exclude_line_ids=body.exclude_line_ids,
        search=body.search,
        limit=body.limit,
        offset=body.offset,
    )
    return IssueRowsResponse(
        stats=IssueRowsStatsOut(**out["stats"]),
        columns=out["columns"],
        rows=out["rows"],
        total=out["total"],
    )


# --------------------------------------------------------------------------- #
# POST /commit
# --------------------------------------------------------------------------- #
@router.post("/commit", response_model=CommitResponse)
def commit(
    body: CommitRequest,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> CommitResponse:
    """Validate then load into canonical DB (transactional)."""
    from etl.line_exclude import apply_line_exclusions

    canonical, _, _raw_df = _load_and_apply_cached(body.file_id, body.sheet, body.profile, session)
    canonical = apply_line_exclusions(canonical, body.exclude_line_ids)

    strategy = body.profile.get("linking_strategy", "txn")
    linked = D.link_partners(canonical, strategy)

    # Check dedup first (Q1) — only for append mode
    h = content_hash(canonical)
    commit_mode = body.commit_mode if body.commit_mode in ("replace", "append") else "replace"
    if commit_mode == "append" and dedup_check(session, body.dataset, None, None, h):
        raise HTTPException(
            status_code=409,
            detail="Content already loaded (dedup check). Use commit_mode=replace to reload this scope.",
        )

    # Run blocking checks
    fact_ar = D.derive_ar(linked)
    fact_ap = D.derive_ap(linked)
    fact_sales = D.derive_sales(linked)
    fact_com = D.derive_com(linked)

    required = ["journal_entry_group_number", "fiscal_year", "line_number",
                "booking_line_id", "account_number_group", "amount", "posting_date"]
    results = [
        C.check_required_fields(linked, required),
        C.check_booking_balance(linked),
        C.check_monthly_balance(linked),
        C.check_ledger_balance(linked),
        C.check_unique_booking_line_id(linked),
        C.check_r1_ar(fact_ar, linked),
        C.check_r2_ap(fact_ap, linked),
        C.check_r3_sales(fact_sales, linked),
        C.check_r4_com(fact_com, linked),
    ]
    summary = C.summarize(results)

    blocking = [r for r in results if r.blocking]
    if blocking and not body.confirm_soft:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Blocking validation failures prevent commit",
                "blocking": [{"id": r.id, "detail": r.detail} for r in blocking],
            },
        )

    # DF2: enrich canonical lines with level columns from dim_gl_account
    # and build AccountClasses so derived facts populate correctly.
    source_system = body.profile.get("source_system", "unknown")
    rules = _CLASSIFY_OVERRIDES.get(source_system, DEFAULT_RULES)

    # Determine which level columns the rules need (deduplicated).
    _needed_level_cols = list({
        rules.revenue.level_column,
        rules.material.level_column,
        rules.receivable.level_column,
        rules.payable.level_column,
    })

    # Fetch dim_gl_account rows for accounts present in canonical lines.
    #
    # We query only the rows we need (by account_number_group + fiscal_year)
    # to avoid pulling the entire dim into memory.  The join is done in Python
    # because the data volume per commit is small (< 50 MB upload limit).
    enriched_lines = canonical.copy()
    try:
        ang_values = canonical["account_number_group"].dropna().unique().tolist()
        fy_values = canonical["fiscal_year"].dropna().unique().tolist()

        if ang_values and fy_values:
            # Build a temporary DataFrame of mapping rows from the DB.
            _select_cols = ", ".join(["account_number_group", "fiscal_year"] + _needed_level_cols)
            dim_rows = session.execute(
                text(
                    f"SELECT {_select_cols} FROM dim_gl_account "
                    "WHERE account_number_group = ANY(:angs) "
                    "AND fiscal_year = ANY(:fys)"
                ),
                {"angs": ang_values, "fys": [int(y) for y in fy_values]},
            ).fetchall()

            if dim_rows:
                dim_df = pd.DataFrame(
                    dim_rows,
                    columns=["account_number_group", "fiscal_year"] + _needed_level_cols,
                )
                # Drop any level columns already on canonical lines to avoid _x/_y suffixes
                for col in _needed_level_cols:
                    if col in enriched_lines.columns:
                        enriched_lines = enriched_lines.drop(columns=[col])
                enriched_lines = enriched_lines.merge(
                    dim_df,
                    on=["account_number_group", "fiscal_year"],
                    how="left",
                )
                # Pre-classify using per-class level columns (multi-column aware).
                # classify_with_rules handles the case where revenue/material use
                # level_2 while receivable/payable use level_3 (DEFAULT_RULES).
                # Writing account_class onto enriched_lines means load_canonical
                # will use the pre-classified values (account_classes=None path).
                enriched_lines["account_class"] = _classify_with_rules(enriched_lines, rules)
                _class_counts = enriched_lines["account_class"].value_counts().to_dict()
                logger.info("commit: classified lines: %s", _class_counts)
            else:
                # No mapping rows in DB yet — classification falls back to
                # existing account_class column (or 'other').
                logger.warning(
                    "commit: no dim_gl_account rows found for this entity/year; "
                    "account_class will default to 'other'. Load the account mapping first."
                )
        else:
            pass  # enriched_lines left as canonical.copy() — existing account_class or 'other'
    except Exception as exc:
        # Non-fatal: if enrichment fails we still load GL lines (facts will be empty).
        logger.warning("commit: dim_gl_account enrichment failed (%s); proceeding without classification", exc)

    # FK pre-flight: verify dim_gl_account is populated before loading GL lines.
    try:
        ang_values_pf = canonical["account_number_group"].dropna().unique().tolist()
        fy_values_pf = canonical["fiscal_year"].dropna().unique().tolist()
        if ang_values_pf and fy_values_pf:
            count_row = session.execute(
                text(
                    "SELECT COUNT(*) FROM dim_gl_account "
                    "WHERE account_number_group = ANY(:angs) AND fiscal_year = ANY(:fys)"
                ),
                {"angs": ang_values_pf, "fys": [int(y) for y in fy_values_pf]},
            ).fetchone()
            if count_row and int(count_row[0]) == 0:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "No account mapping rows found for the accounts/years in this GL file. "
                        "Load the account mapping first (POST /api/v1/ingest/mapping/commit) "
                        "before committing GL lines."
                    ),
                )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("commit: FK pre-flight check failed (%s); proceeding", exc)

    # DF2: upsert dim_legal_entity for confirmed wizard assignments and data prefixes.
    try:
        assignments = body.profile.get("entity_assignments") or {}
        for label, prefix in assignments.items():
            load_legal_entity(
                session,
                entity_prefix=str(prefix).zfill(2),
                entity_name=str(label).strip(),
                is_consolidation=False,
                source_system=source_system if source_system != "unknown" else None,
            )
        if not assignments and not canonical.empty:
            entity_prefix_val: str | None = None
            if "account_number_group" in canonical.columns:
                entity_prefix_val = str(canonical["account_number_group"].iloc[0])[:2]
            elif "journal_entry_group_number" in canonical.columns:
                entity_prefix_val = str(canonical["journal_entry_group_number"].iloc[0])[:2]
            if entity_prefix_val:
                load_legal_entity(
                    session,
                    entity_prefix=entity_prefix_val,
                    entity_name=entity_prefix_val,
                    is_consolidation=False,
                    source_system=source_system if source_system != "unknown" else None,
                )
    except Exception as exc:
        logger.warning("commit: dim_legal_entity upsert failed (%s); continuing", exc)

    # Load — account_class is already set on enriched_lines (pre-classified above),
    # so load_canonical uses the existing column (account_classes=None path).
    try:
        result = load_canonical(
            session,
            enriched_lines,
            dataset=body.dataset,
            linking_strategy=strategy,
            commit_mode=commit_mode,
        )
    except Exception as exc:
        session.rollback()
        logger.error("load_canonical failed: %s", exc)
        raise HTTPException(status_code=500, detail="Load failed; transaction rolled back. Check server logs.") from exc

    # DF3: upsert dim_customer / dim_supplier from the linked lines.
    # Use enriched_lines (post-link_partners) so partner ids are fully propagated.
    # Graceful degrade: a partner upsert failure must not roll back the GL load.
    try:
        _linked_for_partners = D.link_partners(enriched_lines, strategy)
        _customers_df, _suppliers_df = _extract_partners(_linked_for_partners)
        if not _customers_df.empty or not _suppliers_df.empty:
            _partner_counts = load_partners(session, _customers_df, _suppliers_df)
            session.commit()
            logger.info("commit: partner dims upserted: %s", _partner_counts)
    except Exception as exc:
        logger.warning("commit: dim_customer/dim_supplier upsert failed (%s); GL load already committed", exc)

    # Reporting-v2: deterministic post-load rebuild (flag-gated; default OFF).
    # When settings.rebuild_on_commit is False (legacy/5176 default) this block is
    # skipped entirely and the commit path is byte-identical to before.  When True
    # (v2 stack), run the full idempotent rebuild so derived facts / partner links /
    # structure reflect the new data without a manual derive_facts.py invocation.
    if settings.rebuild_on_commit:
        try:
            from etl.rebuild import rebuild_project
            from etl.versioning import derive_gl_scope

            scope = derive_gl_scope(canonical)
            # Phase 7: pick the rebuild mode (auto => incremental for a pure append
            # on already-mapped accounts, full otherwise) and let the project config
            # drive the rebuild flags so the persisted setup is reused every update.
            rebuild_mode = _select_rebuild_mode(
                session, body.rebuild_mode, commit_mode, scope
            )
            rebuild_summary = rebuild_project(
                session, scope=scope, mode=rebuild_mode, project_id=body.project_id
            )
            logger.info(
                "commit: rebuild_project(mode=%s, project=%s) completed: %s",
                rebuild_mode, body.project_id, rebuild_summary,
            )
        except Exception as exc:
            session.rollback()
            logger.error("commit: rebuild_project failed: %s", exc)
            raise HTTPException(
                status_code=500,
                detail="Post-commit rebuild failed; transaction rolled back. Check server logs.",
            ) from exc

    # Pre-warm narrative snapshots in background (non-blocking).
    import os
    if os.getenv("NARRATIVE_WARM_ON_INGEST", "1").strip().lower() not in ("0", "false", "no"):
        try:
            from datetime import date
            from app.services.narrative_warm import schedule_narrative_warm
            anchor = date.today()
            schedule_narrative_warm(anchor.year, anchor.month)
            logger.info("commit: scheduled background narrative warm for %d-%02d", anchor.year, anchor.month)
        except Exception as exc:
            logger.warning("commit: narrative warm schedule failed (%s)", exc)

    return CommitResponse(**result)


# --------------------------------------------------------------------------- #
# Account mapping helpers (generic + BS/PL Master)
# --------------------------------------------------------------------------- #

def _bs_pl_mapping_from_file(
    file_id: str,
    session: Session,
    fiscal_years: list[int],
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Load BS/PL Master workbook and build canonical mapping frame."""
    if not fiscal_years:
        raise HTTPException(status_code=422, detail="fiscal_years must contain at least one year")
    path = _file_path_from_id(file_id)
    try:
        raw_df = read_bs_pl_master(path)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        mapping_df, meta = build_mapping_frames(raw_df, session, fiscal_years)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return raw_df, mapping_df, meta


# --------------------------------------------------------------------------- #
# GET /fiscal-years
# --------------------------------------------------------------------------- #
@router.get("/fiscal-years", response_model=FiscalYearsResponse)
def list_fiscal_years(
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> FiscalYearsResponse:
    """Distinct fiscal years present in fact_gl_line (for mapping FY selection)."""
    rows = session.execute(
        text("SELECT DISTINCT fiscal_year FROM fact_gl_line ORDER BY fiscal_year")
    ).fetchall()
    years = [int(r[0]) for r in rows if r[0] is not None]
    if not years:
        rows = session.execute(
            text("SELECT DISTINCT fiscal_year FROM fact_gl_entry ORDER BY fiscal_year")
        ).fetchall()
        years = [int(r[0]) for r in rows if r[0] is not None]
    return FiscalYearsResponse(fiscal_years=years)


# --------------------------------------------------------------------------- #
# POST /entity/preview
# --------------------------------------------------------------------------- #
@router.post("/entity/preview", response_model=EntityPreviewResponse)
def entity_preview(
    body: EntityPreviewRequest,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> EntityPreviewResponse:
    """Resolve entity labels from the upload to prefixes (existing DB or proposed)."""
    from etl.entity_resolve import collect_entity_labels, preview_entity_mappings

    path = _file_path_from_id(body.file_id)
    raw_bytes = path.read_bytes()
    dialect = _detect_dialect(raw_bytes, path.suffix.lower())
    raw_df = _load_file(path, body.sheet, dialect)
    labels = collect_entity_labels(raw_df, body.entity)
    if not labels:
        raise HTTPException(status_code=422, detail="No entity labels found in file")

    stats = preview_entity_mappings(session, labels, body.entity_assignments or None)
    label_counts: dict[str, int] = {}
    if body.entity.get("mode") == "column":
        col = body.entity.get("value")
        if col in raw_df.columns:
            from etl.entity_resolve import normalize_entity_label

            for value in raw_df[col].dropna():
                key = normalize_entity_label(value)
                if key:
                    label_counts[key] = label_counts.get(key, 0) + 1
    else:
        label_counts[labels[0]] = len(raw_df)

    mappings = []
    for row in stats["mappings"]:
        mappings.append(
            EntityMappingRow(
                **row,
                row_count=label_counts.get(row["source_label"], 0),
            )
        )
    return EntityPreviewResponse(
        mappings=mappings,
        needs_confirmation=stats["needs_confirmation"],
        all_resolved=stats["all_resolved"],
        proposed_assignments=stats.get("proposed_assignments", {}),
    )


# --------------------------------------------------------------------------- #
# POST /mapping/preview
# --------------------------------------------------------------------------- #
@router.post("/mapping/preview", response_model=MappingPreviewResponse)
def mapping_preview(
    body: MappingPreviewRequest,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> MappingPreviewResponse:
    """Read-only preview for BS/PL Master account mapping uploads."""
    if body.format != "bs_pl_master":
        raise HTTPException(status_code=422, detail="Only format=bs_pl_master is supported for preview")
    if body.replace_mode not in BS_PL_REPLACE_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"replace_mode must be one of {sorted(BS_PL_REPLACE_MODES)}",
        )
    path = _file_path_from_id(body.file_id)
    sheets = _get_sheets(path)
    if not is_bs_pl_master_workbook(sheets):
        raise HTTPException(
            status_code=422,
            detail=f"File must contain sheets {['Master_BS', 'Master_PL']!r}; got {sheets}",
        )
    _, mapping_df, meta = _bs_pl_mapping_from_file(body.file_id, session, body.fiscal_years)
    stats = preview_stats(
        mapping_df,
        session,
        body.fiscal_years,
        meta["source_bs_rows"],
        meta["source_pl_rows"],
        replace_mode=body.replace_mode,
        skipped_entities=meta.get("skipped_entities"),
        skipped_rows=meta.get("skipped_rows", 0),
        warnings=meta.get("warnings"),
    )
    if stats["duplicate_keys"]:
        stats["blockers"].append(
            f"{len(stats['duplicate_keys'])} duplicate (account_number_group, fiscal_year) keys in file"
        )
    stats.setdefault("duplicate_details", stats.get("duplicate_keys", []))
    stats.setdefault("insert_details", [])
    stats.setdefault("delete_details", [])
    return MappingPreviewResponse(**stats)


# --------------------------------------------------------------------------- #
# POST /mapping/commit
# --------------------------------------------------------------------------- #
@router.post("/mapping/commit", response_model=MappingCommitResponse)
def mapping_commit(
    body: MappingCommitRequest,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> MappingCommitResponse:
    """Load an account-mapping file into dim_gl_account / dim_gl_na / dim_gl_cf.

    Validates that all required target fields are mapped before any DB write.
    Records a meta_dataset_load row with dataset='mapping'.

    format=bs_pl_master: reads Master_BS + Master_PL, resolves entities from
    dim_legal_entity, replicates across fiscal_years (explicit user commit only).
    """
    loaded_at_dt = datetime.now(timezone.utc)
    deleted_count = 0
    skipped_count = 0

    if body.format == "bs_pl_master":
        if body.replace_mode not in BS_PL_REPLACE_MODES:
            raise HTTPException(
                status_code=422,
                detail=f"replace_mode must be one of {sorted(BS_PL_REPLACE_MODES)}",
            )
        path = _file_path_from_id(body.file_id)
        sheets = _get_sheets(path)
        if not is_bs_pl_master_workbook(sheets):
            raise HTTPException(
                status_code=422,
                detail=f"File must contain sheets Master_BS and Master_PL; got {sheets}",
            )
        if not body.fiscal_years:
            raise HTTPException(status_code=422, detail="fiscal_years required for bs_pl_master format")
        raw_df, mapping_df, _meta = _bs_pl_mapping_from_file(
            body.file_id, session, body.fiscal_years
        )
        if mapping_df.duplicated(subset=["account_number_group", "fiscal_year"]).any():
            raise HTTPException(
                status_code=422,
                detail="Duplicate (account_number_group, fiscal_year) keys in mapping file",
            )

        keys_in_file = set(
            zip(
                mapping_df["account_number_group"].astype(str),
                mapping_df["fiscal_year"].astype(int),
            )
        )
        prefixes = sorted(
            {str(p) for p in mapping_df["account_number_group"].str[:2].unique()}
        )

        if body.replace_mode == "replace":
            try:
                deleted_count = delete_orphan_account_mappings(
                    session,
                    body.fiscal_years,
                    prefixes,
                    keys_in_file,
                )
            except ValueError as exc:
                session.rollback()
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        else:
            full_count = len(mapping_df)
            mapping_df = filter_mapping_append_only(
                mapping_df, session, body.fiscal_years
            )
            skipped_count = full_count - len(mapping_df)
    else:
        # ------------------------------------------------------------------ load file (generic)
        path = _file_path_from_id(body.file_id)
        raw_bytes = path.read_bytes()
        dialect = _detect_dialect(raw_bytes, path.suffix.lower())
        raw_df = _load_file(path, body.sheet, dialect, header=body.header_row)

        if not body.profile:
            raise HTTPException(status_code=422, detail="profile is required for generic format")

        # ------------------------------------------------------------------ parse profile
        try:
            am_profile = account_profile_from_dict(body.profile)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Invalid mapping profile: {exc}") from exc

        # ------------------------------------------------------------------ light validation
        _effective_required = [
            f for f in _AM_REQUIRED
            if not (f == "level_0" and am_profile.fixed_level_0 is not None)
        ]
        unmapped = [f for f in _effective_required if not am_profile.columns.get(f)]
        if unmapped:
            raise HTTPException(
                status_code=422,
                detail=f"Required fields not mapped in profile.columns: {unmapped}",
            )

        try:
            mapping_df = apply_account_mapping(raw_df, am_profile)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # ------------------------------------------------------------------ load into DB
    from etl.versioning import capture_mapping_snapshot, derive_mapping_scope

    prefixes, years = derive_mapping_scope(mapping_df)
    mapping_commit_mode = (
        body.replace_mode if body.format == "bs_pl_master" else "replace"
    )
    try:
        counts = load_account_mapping(session, mapping_df, auto_commit=False)
    except Exception as exc:
        session.rollback()
        logger.error("mapping_commit load_account_mapping failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Mapping load failed; transaction rolled back. Check server logs.",
        ) from exc

    # ------------------------------------------------------------------ audit + snapshot
    load_id: int | None = None
    try:
        entity_prefix_val = prefixes[0] if prefixes else None
        fy_val = years[0] if years else None
        load_row = session.execute(
            text("""
                INSERT INTO meta_dataset_load
                  (dataset, legal_entity_code, fiscal_year, row_count, content_hash,
                   loaded_at, loaded_by, scope_entity_prefixes, scope_fiscal_years,
                   commit_mode, snapshot_captured)
                VALUES (:ds, :le, :fy, :rc, :h, :la, :lb, :pfx, :fys, :cm, FALSE)
                RETURNING load_id
            """),
            {
                "ds": "mapping",
                "le": entity_prefix_val,
                "fy": fy_val,
                "rc": len(mapping_df),
                "h": content_hash(raw_df),
                "la": loaded_at_dt,
                "lb": None,
                "pfx": prefixes or None,
                "fys": years or None,
                "cm": mapping_commit_mode,
            },
        ).fetchone()
        if load_row:
            load_id = int(load_row[0])
            if prefixes and years:
                capture_mapping_snapshot(session, load_id, prefixes, years)
                session.execute(
                    text(
                        "UPDATE meta_dataset_load SET snapshot_captured = TRUE WHERE load_id = :id"
                    ),
                    {"id": load_id},
                )
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.error("mapping_commit snapshot failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Mapping snapshot failed; transaction rolled back. Check server logs.",
        ) from exc

    return MappingCommitResponse(
        load_id=load_id,
        accounts=counts["accounts"],
        na=counts["na"],
        cf=counts["cf"],
        deleted=deleted_count,
        skipped=skipped_count,
        loaded_at=loaded_at_dt.isoformat(),
        commit_mode=mapping_commit_mode,
    )


# --------------------------------------------------------------------------- #
# Opening-balance separate-file path (Project Setup) + Partner master
# --------------------------------------------------------------------------- #
# These are the thin "collect-then-commit" endpoints for the Project Setup
# wizard.  Upload = parse + preview (NO DB write); commit = admin-gated write
# confined to the respective tables (OB -> fact_gl_entry/line with synthetic
# tag; partner -> dim_customer / dim_supplier).  Upload security mirrors the
# budget-Excel hardening: .xlsx/.csv only, size cap, finite, generic 500.


class FileUploadPreviewResponse(BaseModel):
    file_id: str
    filename: str
    sheets: list[str] = []
    columns: list[str]
    sample: list[dict]


class OpeningBalanceCommitRequest(BaseModel):
    file_id: str
    sheet: str | None = None
    profile: dict  # serialised GL MappingProfile dict (reused for column mapping)
    scope: str = "first_year"  # 'first_year' | 'all'


class OpeningBalanceCommitResponse(BaseModel):
    load_id: int | None = None
    entries: int
    lines: int
    fiscal_years: list[int] = Field(default_factory=list)
    scope: str = "first_year"
    loaded_at: str


class PartnerMasterCommitRequest(BaseModel):
    file_id: str
    sheet: str | None = None
    profile: dict  # serialised PartnerMappingProfile dict


class PartnerMasterCommitResponse(BaseModel):
    side: str
    upserted: int
    loaded_at: str


def _visible_entity_prefixes_or_none(session: Session, user: User) -> set[str] | None:
    """Resolve a user's allowed 2-char entity prefixes, or None for admin/unrestricted.

    Mirrors ``budget._visible_entity_prefixes`` / ``_anomaly_entity_prefixes`` so
    multi-tenant visibility cannot drift across write paths.
    """
    from app.services.entity_visibility import visible_entity_codes

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


def _assert_prefixes_visible(
    session: Session, user: User, prefixes: list[str]
) -> None:
    """Fail-closed: a restricted user may only commit data within their entities."""
    allowed = _visible_entity_prefixes_or_none(session, user)
    if allowed is None:
        return  # admin / unrestricted
    requested = {str(p).strip()[:2] for p in prefixes if str(p).strip()}
    # M3: fail-closed for a RESTRICTED user when no prefix resolves. An empty
    # `requested` is a subset of anything, so `issubset` would WRONGLY pass — a
    # restricted user with no resolvable entity must be rejected, not waved through.
    if not requested:
        raise HTTPException(
            status_code=403,
            detail="No resolvable entity for this data; not permitted to write.",
        )
    if not requested.issubset(allowed):
        raise HTTPException(
            status_code=403,
            detail="Not permitted to write data for one or more of these entities.",
        )


def _assert_finite_amounts(canonical: pd.DataFrame) -> None:
    """Reject non-finite amounts (NaN / inf) before any DB write."""
    import math

    if "amount" not in canonical.columns:
        return
    amt = pd.to_numeric(canonical["amount"], errors="coerce")
    if amt.isna().any() or not amt.map(lambda v: math.isfinite(float(v))).all():
        raise HTTPException(
            status_code=422,
            detail="Opening-balance file contains non-finite or unparseable amounts.",
        )


def _staged_preview(file_id: str, sheet: str | None) -> FileUploadPreviewResponse:
    """Re-parse a staged file (already saved by /upload) for a preview, no write."""
    path = _file_path_from_id(file_id)
    raw_bytes = path.read_bytes()
    dialect = _detect_dialect(raw_bytes, path.suffix.lower())
    sheets = _get_sheets(path)
    try:
        df = _load_file(path, sheet or (sheets[0] if sheets else None), dialect)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not parse file: {exc}") from exc
    _assert_parse_bounds(df, what="uploaded file")  # H1: zip/dimension-bomb guard
    return FileUploadPreviewResponse(
        file_id=file_id,
        filename=path.name.split("_", 1)[-1],
        sheets=sheets,
        columns=list(df.columns),
        sample=df.head(10).fillna("").to_dict(orient="records"),
    )


async def _save_upload(file: UploadFile, *, max_bytes: int | None = None) -> str:
    """Save an uploaded file under UPLOAD_DIR with a UUID prefix; return file_id.

    Applies the budget-Excel upload-security pattern: extension guard + sanitised
    filename (no path traversal) + a size cap ENFORCED WHILE READING (H2).  The
    body is read in chunks and the read is aborted (413) as soon as the running
    total exceeds ``max_bytes`` so an oversized payload is never fully buffered.
    OB / partner files are small, so the default cap is the OB/partner-specific
    ``OB_PARTNER_MAX_UPLOAD_BYTES`` (well below the GL limit).  The cap is read at
    call time (not bound as a default) so it stays monkeypatch-friendly in tests.
    """
    if max_bytes is None:
        max_bytes = OB_PARTNER_MAX_UPLOAD_BYTES
    # H2: reject early on a declared content-length / file.size hint when present.
    declared = getattr(file, "size", None)
    if isinstance(declared, int) and declared > max_bytes:
        raise HTTPException(
            status_code=413, detail=f"File exceeds {max_bytes // (1024 * 1024)} MB limit"
        )
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413, detail=f"File exceeds {max_bytes // (1024 * 1024)} MB limit"
            )
        chunks.append(chunk)
    raw_bytes = b"".join(chunks)

    safe_name = _safe_filename(file.filename or "upload")
    ext = Path(safe_name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type {ext!r}. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )
    file_id = uuid.uuid4().hex[:16]
    dest = UPLOAD_DIR / f"{file_id}_{safe_name}"
    dest.write_bytes(raw_bytes)
    return file_id


# --------------------------------------------------------------------------- #
# POST /opening-balance/upload  (auth — stage only, NO write)
# --------------------------------------------------------------------------- #
@router.post("/opening-balance/upload", response_model=FileUploadPreviewResponse)
async def opening_balance_upload(
    file: UploadFile = File(...),
    sheet: str | None = Form(None),
    _user: User = Depends(current_user),
) -> FileUploadPreviewResponse:
    """Stage an opening-balance file; return columns + sample preview.  NO DB write."""
    _sweep_stale_uploads()  # L1: hygiene — drop abandoned staged files
    file_id = await _save_upload(file)
    try:
        return _staged_preview(file_id, sheet)
    except HTTPException:
        _file_path_from_id(file_id).unlink(missing_ok=True)
        raise


# --------------------------------------------------------------------------- #
# POST /opening-balance/commit  (admin — writes tagged OB rows)
# --------------------------------------------------------------------------- #
@router.post("/opening-balance/commit", response_model=OpeningBalanceCommitResponse)
def opening_balance_commit(
    body: OpeningBalanceCommitRequest,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> OpeningBalanceCommitResponse:
    """Load opening-balance rows into fact_gl_entry/line, tagged as opening balances.

    Parses the OB file exactly like a GL file (reusing ``_load_and_apply`` + the GL
    ``MappingProfile``), then tags every row ``entry_type='opening_balance'`` /
    ``fiscal_period=0`` and re-mints a synthetic ``journal_entry_group_number`` with
    a leading '9' (after the 2-char entity prefix), matching the 'file' OB-mode
    convention (``etl.gobd_gl_prepare._synthetic_opening_txn`` /
    ``etl.opening_balance._mode_file``).

    ``scope='first_year'`` keeps only the earliest fiscal year in the file;
    ``scope='all'`` keeps every year.  Idempotent per (entity, account, fiscal_year):
    the synthetic group number + booking_line_id are deterministic in those keys, so
    a re-commit is a no-op (ON CONFLICT DO NOTHING / replace within scope).
    """
    import re as _re

    if body.scope not in ("first_year", "all"):
        raise HTTPException(status_code=422, detail="scope must be 'first_year' or 'all'")

    try:
        canonical, _dialect, _raw = _load_and_apply(
            body.file_id, body.sheet, body.profile, session
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("opening_balance_commit parse failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to parse opening-balance file.") from exc

    if canonical.empty:
        raise HTTPException(status_code=422, detail="Opening-balance file has no rows.")

    # H1: bound the parsed frame BEFORE any transform/write (zip/dimension bomb).
    _assert_parse_bounds(canonical, what="opening-balance file")
    _assert_finite_amounts(canonical)

    canonical = canonical.copy()
    canonical["fiscal_year"] = pd.to_numeric(
        canonical["fiscal_year"], errors="coerce"
    ).astype("Int64")
    canonical = canonical[canonical["fiscal_year"].notna()].copy()
    if canonical.empty:
        raise HTTPException(status_code=422, detail="No rows with a valid fiscal year.")

    all_years = sorted({int(y) for y in canonical["fiscal_year"].dropna().tolist()})
    if body.scope == "first_year":
        keep_years = [all_years[0]]
        canonical = canonical[canonical["fiscal_year"].astype(int) == keep_years[0]].copy()
    else:
        keep_years = all_years

    # ---- entity-visibility guard (restricted users) ----
    prefixes = sorted(
        {
            str(a)[:2]
            for a in canonical["account_number_group"].dropna().tolist()
            if str(a)[:2]
        }
    )
    _assert_prefixes_visible(session, _admin, prefixes)

    # ---- M2: ensure a usable posting_date per row ----
    # The BS opening-stock read uses MIN(posting_date) per account, so every OB row
    # needs a parseable posting_date.  Where it is present we keep it; where it is
    # missing/unparseable we synthesize a deterministic Jan-1 of that row's
    # fiscal_year (the in-data / carry-forward OB convention).  If neither a parsed
    # date nor a fiscal_year is available for a row → 422 (cannot place the stock).
    if "posting_date" not in canonical.columns:
        canonical["posting_date"] = pd.NaT
    _parsed_pd = pd.to_datetime(canonical["posting_date"], errors="coerce")
    _fy_int = canonical["fiscal_year"].astype("Int64")
    _jan1 = pd.to_datetime(
        _fy_int.astype("float").astype("Int64").astype(str) + "-01-01",
        errors="coerce",
    )
    # Synthesize Jan-1 where the parsed date is missing.
    _final_pd = _parsed_pd.where(_parsed_pd.notna(), _jan1)
    if _final_pd.isna().any():
        raise HTTPException(
            status_code=422,
            detail=(
                "Opening-balance rows lack a usable posting_date and a fiscal year "
                "to synthesize one (Jan-1)."
            ),
        )
    # Store as ISO date strings (the loader/_n passthrough accepts these).
    canonical["posting_date"] = _final_pd.dt.strftime("%Y-%m-%d")

    # ---- M1: file-OB vs in-data-OB exclusivity guard ----
    # File-OB and in-data-OB are mutually exclusive: if real (non-synthetic)
    # in-data opening_balance rows already exist for an (entity_prefix, account,
    # fiscal_year) in scope, committing a second file-OB set would double-count the
    # opening stock.  Detect such collisions and SKIP those rows (warn), so a file
    # commit never creates a duplicate OB for an account that already carries one.
    try:
        # ``fact_gl_line`` carries no ``gl_account_id`` column; the account identity
        # is the full ``account_number_group`` (entity prefix + account), so key the
        # exclusivity on (account_number_group, fiscal_year).
        existing = session.execute(
            text(
                "SELECT DISTINCT l.account_number_group AS ang, l.fiscal_year AS fy "
                "FROM fact_gl_line l "
                "JOIN fact_gl_entry e "
                "  ON e.journal_entry_group_number = l.journal_entry_group_number "
                " AND e.fiscal_year = l.fiscal_year "
                "WHERE e.entry_type = :ot "
                "  AND e.fiscal_period = :fp "
                "  AND SUBSTR(l.journal_entry_group_number, 3, 1) <> '9' "
                "  AND SUBSTR(l.account_number_group, 1, 2) = ANY(:pfx) "
                "  AND l.fiscal_year = ANY(:fys)"
            ),
            {"ot": OB_ENTRY_TYPE, "fp": OB_FISCAL_PERIOD,
             "pfx": prefixes or [""], "fys": keep_years},
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 — guard must not crash the commit
        # A failed probe poisons the psycopg2 transaction; roll back so the
        # subsequent OB load can still proceed (the guard degrades to off).
        session.rollback()
        logger.warning("opening_balance_commit: in-data-OB probe failed (%s); skipping guard", exc)
        existing = []

    if existing:
        # Key on (account_number_group, fiscal_year).
        _existing_keys = {
            (str(r[0]), int(r[1])) for r in existing if r[1] is not None
        }
        _row_keys = [
            (str(a), int(fy))
            for a, fy in zip(
                canonical["account_number_group"].astype(str),
                canonical["fiscal_year"].astype(int),
            )
        ]
        _skip_mask = pd.Series(
            [k in _existing_keys for k in _row_keys], index=canonical.index
        )
        if _skip_mask.any():
            n_skip = int(_skip_mask.sum())
            logger.warning(
                "opening_balance_commit: %d row(s) skipped — in-data opening balances "
                "already exist for those (entity, account, fiscal_year); file-OB and "
                "in-data-OB are mutually exclusive.",
                n_skip,
            )
            canonical = canonical[~_skip_mask].copy()
        if canonical.empty:
            raise HTTPException(
                status_code=409,
                detail=(
                    "All opening-balance rows collide with existing in-data opening "
                    "balances (file-OB and in-data-OB are mutually exclusive)."
                ),
            )

    # ---- tag + re-mint synthetic OB journal_entry_group_number (prefix '9') ----
    def _ob_jegn(ang: str, acct_id: str) -> str:
        ee = _re.sub(r"\D", "", str(ang))[:2].zfill(2)
        acct = _re.sub(r"\D", "", str(acct_id)).zfill(9)[-9:]
        return f"{ee}9{acct}"[:12]

    acct_for_jegn = (
        canonical["gl_account_id"].astype(str)
        if "gl_account_id" in canonical.columns
        else canonical["account_number_group"].astype(str).str.slice(2)
    )
    canonical["journal_entry_group_number"] = [
        _ob_jegn(ang, acct)
        for ang, acct in zip(
            canonical["account_number_group"].astype(str), acct_for_jegn
        )
    ]
    # One line per synthetic entry (single-line OB bookings).
    canonical["line_number"] = 1
    canonical["fiscal_period"] = OB_FISCAL_PERIOD
    canonical["entry_type"] = OB_ENTRY_TYPE
    if "source_system" not in canonical.columns or canonical["source_system"].isna().all():
        canonical["source_system"] = "opening_balance_upload"

    # Deterministic, collision-free synthetic booking_line_id in the reserved band.
    def _ob_bid(jegn: str, fy: int) -> int:
        digits = _re.sub(r"\D", "", f"{jegn}{int(fy)}") or "0"
        return _OB_BID_BASE + (int(digits) % 1_000_000_000_000)

    canonical["booking_line_id"] = [
        _ob_bid(j, fy)
        for j, fy in zip(
            canonical["journal_entry_group_number"],
            canonical["fiscal_year"].astype(int),
        )
    ]

    # ---- load: confine writes to fact_gl_entry / fact_gl_line (no facts/rebuild) ----
    from etl.load import split_entry_line, _bulk_insert_entries, _bulk_insert_lines

    loaded_at_dt = datetime.now(timezone.utc)
    try:
        # Idempotency: clear any prior OB rows for the exact synthetic keys in scope.
        jegns = canonical["journal_entry_group_number"].astype(str).unique().tolist()
        session.execute(
            text(
                "DELETE FROM fact_gl_line WHERE journal_entry_group_number = ANY(:j) "
                "AND fiscal_year = ANY(:fys)"
            ),
            {"j": jegns, "fys": keep_years},
        )
        session.execute(
            text(
                "DELETE FROM fact_gl_entry WHERE journal_entry_group_number = ANY(:j) "
                "AND fiscal_year = ANY(:fys)"
            ),
            {"j": jegns, "fys": keep_years},
        )
        entries, line_rows = split_entry_line(canonical)
        if not entries.empty:
            _bulk_insert_entries(session, entries)
        if not line_rows.empty:
            _bulk_insert_lines(session, line_rows)

        load_row = session.execute(
            text("""
                INSERT INTO meta_dataset_load
                  (dataset, legal_entity_code, fiscal_year, row_count, content_hash,
                   loaded_at, loaded_by, scope_entity_prefixes, scope_fiscal_years,
                   commit_mode, snapshot_captured)
                VALUES (:ds, :le, :fy, :rc, :h, :la, :lb, :pfx, :fys, 'replace', FALSE)
                RETURNING load_id
            """),
            {
                "ds": "opening_balance",
                "le": prefixes[0] if prefixes else None,
                "fy": keep_years[0] if keep_years else None,
                "rc": len(line_rows),
                "h": content_hash(canonical),
                "la": loaded_at_dt,
                "lb": None,
                "pfx": prefixes or None,
                "fys": keep_years or None,
            },
        ).fetchone()
        load_id = int(load_row[0]) if load_row else None
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.error("opening_balance_commit load failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Opening-balance load failed; transaction rolled back. Check server logs.",
        ) from exc

    return OpeningBalanceCommitResponse(
        load_id=load_id,
        entries=len(entries),
        lines=len(line_rows),
        fiscal_years=keep_years,
        scope=body.scope,
        loaded_at=loaded_at_dt.isoformat(),
    )


# --------------------------------------------------------------------------- #
# POST /partner-master/upload  (auth — stage only, NO write)
# --------------------------------------------------------------------------- #
@router.post("/partner-master/upload", response_model=FileUploadPreviewResponse)
async def partner_master_upload(
    file: UploadFile = File(...),
    sheet: str | None = Form(None),
    _user: User = Depends(current_user),
) -> FileUploadPreviewResponse:
    """Stage a partner-master file; return columns + sample preview.  NO DB write."""
    _sweep_stale_uploads()  # L1: hygiene — drop abandoned staged files
    file_id = await _save_upload(file)
    try:
        return _staged_preview(file_id, sheet)
    except HTTPException:
        _file_path_from_id(file_id).unlink(missing_ok=True)
        raise


# --------------------------------------------------------------------------- #
# POST /partner-master/commit  (admin — UPSERT dim_customer / dim_supplier)
# --------------------------------------------------------------------------- #
@router.post("/partner-master/commit", response_model=PartnerMasterCommitResponse)
def partner_master_commit(
    body: PartnerMasterCommitRequest,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> PartnerMasterCommitResponse:
    """Column-mapped UPSERT of a partner-master file into dim_customer / dim_supplier.

    The ``profile`` is a serialised ``PartnerMappingProfile`` describing the side
    (customer/supplier), the entity, the join-key column (debtor / creditor number)
    and the descriptive columns.  customer_id / supplier_id = entity_prefix(2) +
    join-key value.  Writes are confined to the one partner table.
    """
    from etl.partner_master_mapping import (
        apply_partner_profile,
        load_partner_master,
        partner_profile_from_dict,
    )

    path = _file_path_from_id(body.file_id)
    raw_bytes = path.read_bytes()
    dialect = _detect_dialect(raw_bytes, path.suffix.lower())
    raw_df = _load_file(path, body.sheet, dialect)
    # H1: bound the parsed frame BEFORE transform/write (zip/dimension bomb).
    _assert_parse_bounds(raw_df, what="partner-master file")

    try:
        profile = partner_profile_from_dict(body.profile)
        mapped = apply_partner_profile(raw_df, profile)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if mapped.empty:
        raise HTTPException(status_code=422, detail="No partner rows with a valid join key.")

    # ---- entity-visibility guard (restricted users) ----
    id_col = "customer_id" if profile.side == "customer" else "supplier_id"
    prefixes = sorted({str(v)[:2] for v in mapped[id_col].dropna().tolist() if str(v)[:2]})
    _assert_prefixes_visible(session, _admin, prefixes)

    try:
        counts = load_partner_master(session, mapped, profile.side)
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.error("partner_master_commit load failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Partner-master load failed; transaction rolled back. Check server logs.",
        ) from exc

    upserted = counts.get("customers", 0) + counts.get("suppliers", 0)
    return PartnerMasterCommitResponse(
        side=profile.side,
        upserted=int(upserted),
        loaded_at=datetime.now(timezone.utc).isoformat(),
    )


# --------------------------------------------------------------------------- #
# Version history + restore
# --------------------------------------------------------------------------- #

_VERSIONS_SELECT = """
    SELECT load_id, dataset, legal_entity_code, fiscal_year, row_count,
           content_hash, loaded_at, loaded_by,
           scope_entity_prefixes, scope_fiscal_years,
           commit_mode, snapshot_captured, restored_from_load_id
    FROM meta_dataset_load
"""

_VERSIONS_SELECT_LEGACY = """
    SELECT load_id, dataset, legal_entity_code, fiscal_year, row_count,
           content_hash, loaded_at, loaded_by
    FROM meta_dataset_load
"""


def _run_record_from_row(r, extended: bool = True) -> RunRecord:
    if extended and len(r) > 8:
        return RunRecord(
            load_id=r[0],
            dataset=r[1],
            legal_entity_code=r[2],
            fiscal_year=r[3],
            row_count=r[4],
            content_hash=r[5],
            loaded_at=str(r[6]),
            loaded_by=r[7],
            scope_entity_prefixes=list(r[8] or []),
            scope_fiscal_years=[int(y) for y in (r[9] or [])],
            commit_mode=r[10],
            snapshot_captured=bool(r[11]) if r[11] is not None else False,
            restored_from_load_id=r[12],
        )
    return RunRecord(
        load_id=r[0],
        dataset=r[1],
        legal_entity_code=r[2],
        fiscal_year=r[3],
        row_count=r[4],
        content_hash=r[5],
        loaded_at=str(r[6]),
        loaded_by=r[7],
        scope_entity_prefixes=[],
        scope_fiscal_years=[],
        commit_mode=None,
        snapshot_captured=False,
        restored_from_load_id=None,
    )


def _fetch_version_rows(
    session: Session,
    limit: int,
    dataset: str | None = None,
) -> list:
    """Load version rows; fall back if migration 0007 columns are missing."""
    params: dict = {"lim": min(limit, 200)}
    sql = _VERSIONS_SELECT + " WHERE dataset != 'restore'"
    if dataset:
        sql += " AND dataset = :ds"
        params["ds"] = dataset
    sql += " ORDER BY load_id DESC LIMIT :lim"
    try:
        rows = session.execute(text(sql), params).fetchall()
        return [_run_record_from_row(r, extended=True) for r in rows]
    except Exception as exc:
        session.rollback()
        err = str(exc).lower()
        if "scope_entity_prefixes" not in err and "snapshot_captured" not in err and "commit_mode" not in err:
            raise
        logger.warning("meta_dataset_load versioning columns missing — using legacy query: %s", exc)
        sql_legacy = _VERSIONS_SELECT_LEGACY + " WHERE dataset != 'restore'"
        if dataset:
            sql_legacy += " AND dataset = :ds"
        sql_legacy += " ORDER BY load_id DESC LIMIT :lim"
        rows = session.execute(text(sql_legacy), params).fetchall()
        return [_run_record_from_row(r, extended=False) for r in rows]


@router.get("/versions", response_model=list[RunRecord])
def list_versions(
    limit: int = 50,
    dataset: str | None = None,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> list[RunRecord]:
    """List ingest versions (loads with optional snapshots)."""
    return _fetch_version_rows(session, limit, dataset)


@router.get("/versions/{load_id}", response_model=VersionDetailOut)
def get_version(
    load_id: int,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> VersionDetailOut:
    from etl.versioning import snapshot_counts

    row = None
    extended = True
    try:
        row = session.execute(
            text(_VERSIONS_SELECT + " WHERE load_id = :id"),
            {"id": load_id},
        ).fetchone()
    except Exception as exc:
        session.rollback()
        err = str(exc).lower()
        if "scope_entity_prefixes" in err or "snapshot_captured" in err or "commit_mode" in err:
            extended = False
            row = session.execute(
                text(_VERSIONS_SELECT_LEGACY + " WHERE load_id = :id"),
                {"id": load_id},
            ).fetchone()
        else:
            raise
    if not row:
        raise HTTPException(status_code=404, detail=f"load_id {load_id} not found")
    base = _run_record_from_row(row, extended=extended)
    ds = base.dataset if base.dataset != "restore" else "gl"
    counts = snapshot_counts(session, load_id, ds) if base.snapshot_captured else {}
    return VersionDetailOut(**base.model_dump(), snapshot_counts=counts)


@router.post("/versions/{load_id}/restore", response_model=RestoreResponse)
def restore_version(
    load_id: int,
    body: RestoreRequest,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> RestoreResponse:
    from etl.versioning import (
        DATASET_GL,
        DATASET_MAPPING,
        DATASET_RESTORE,
        get_load_meta,
        restore_gl_snapshot,
        restore_mapping_snapshot,
        snapshot_counts,
    )

    if not body.confirm:
        raise HTTPException(status_code=422, detail="Set confirm=true to restore this version")

    meta = get_load_meta(session, load_id)
    if not meta:
        raise HTTPException(status_code=404, detail=f"load_id {load_id} not found")
    if not meta["snapshot_captured"]:
        raise HTTPException(status_code=409, detail=f"load_id {load_id} has no restorable snapshot")
    if meta["dataset"] == DATASET_RESTORE:
        raise HTTPException(status_code=422, detail="Cannot restore from a restore audit entry")

    try:
        if meta["dataset"] == DATASET_MAPPING:
            restore_mapping_snapshot(session, load_id)
            audit_dataset = DATASET_MAPPING
        elif meta["dataset"] == DATASET_GL:
            restore_gl_snapshot(session, load_id)
            audit_dataset = DATASET_GL
        else:
            raise HTTPException(
                status_code=422,
                detail=f"Restore not supported for dataset={meta['dataset']!r}",
            )

        audit_row = session.execute(
            text("""
                INSERT INTO meta_dataset_load
                  (dataset, legal_entity_code, fiscal_year, row_count,
                   loaded_at, scope_entity_prefixes, scope_fiscal_years,
                   commit_mode, snapshot_captured, restored_from_load_id)
                VALUES (:ds, :le, :fy, :rc, :la, :pfx, :fys, 'restore', FALSE, :from_id)
                RETURNING load_id
            """),
            {
                "ds": DATASET_RESTORE,
                "le": meta["scope_entity_prefixes"][0] if meta["scope_entity_prefixes"] else None,
                "fy": meta["scope_fiscal_years"][0] if meta["scope_fiscal_years"] else None,
                "rc": meta["row_count"],
                "la": datetime.now(timezone.utc),
                "pfx": meta["scope_entity_prefixes"],
                "fys": meta["scope_fiscal_years"],
                "from_id": load_id,
            },
        ).fetchone()
        session.commit()
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        session.rollback()
        logger.error("restore_version failed for load_id=%s: %s", load_id, exc)
        raise HTTPException(status_code=500, detail="Restore failed; transaction rolled back.") from exc

    counts = snapshot_counts(session, load_id, audit_dataset)
    return RestoreResponse(
        load_id=int(audit_row[0]) if audit_row else load_id,
        restored_from_load_id=load_id,
        dataset=audit_dataset,
        scope_entity_prefixes=meta["scope_entity_prefixes"],
        scope_fiscal_years=meta["scope_fiscal_years"],
        snapshot_counts=counts,
    )


# --------------------------------------------------------------------------- #
# GET /runs  (legacy alias)
# --------------------------------------------------------------------------- #
@router.get("/runs", response_model=list[RunRecord])
def list_runs(
    limit: int = 50,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> list[RunRecord]:
    return _fetch_version_rows(session, limit)


# --------------------------------------------------------------------------- #
# Private helpers
# --------------------------------------------------------------------------- #
def _json_dumps(obj: Any) -> str:
    import json
    return json.dumps(obj, default=str)


def _raise_db_error(exc: Exception, ctx: str) -> None:
    """Log the full traceback server-side; surface ONLY a generic message.

    L2: the previous implementation echoed ``str(exc)`` (truncated) into the
    response detail, which can leak DB schema / SQL / connection internals to the
    client.  The full message stays in the logs; the client gets a generic string.
    """
    logger.exception("%s DB error", ctx)
    raise HTTPException(
        status_code=500, detail="Database error; check server logs."
    ) from exc
