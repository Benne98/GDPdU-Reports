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
  GET  /runs                List recent org_meta_dataset_load records.

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
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

# etl/ lives at the repo root, one level above backend/.
# Add repo root to sys.path so `etl` is importable when the backend
# is launched from the backend/ directory (e.g. `uvicorn app.main:app`).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import coa_template
from app.auth import User, current_user, require_admin
from app.column_suggest import (
    detect_has_header,
    suggest_column_names,
    synthetic_headers,
)
from app.config import settings
from app.db import get_session
from etl import checks as C
from etl import derive as D
from etl.classify_config import OVERRIDES as _CLASSIFY_OVERRIDES, DEFAULT_RULES, build_account_classes
from etl.derive import extract_partners as _extract_partners
from etl.load import (
    content_hash,
    content_hash_with_strategy,
    dedup_check,
    delete_orphan_account_mappings,
    load_account_mapping,
    load_canonical,
    load_legal_entity,
    load_partners,
    split_entry_line,
    upsert_legal_entities_for_prefixes,
)
from etl.derive import classify_with_rules as _classify_with_rules
from etl.mapping import MappingProfile, apply_profile, profile_from_dict, profile_to_dict
from etl.bs_pl_master import (
    BS_PL_REPLACE_MODES,
    BS_SHEET as _BS_SHEET,
    ENTITY_COL as _ENTITY_COL,
    PL_SHEET as _PL_SHEET,
    build_mapping_frames,
    filter_mapping_append_only,
    is_bs_pl_master_workbook,
    preview_stats,
    read_bs_pl_master,
)
from etl.mapping_account import AccountMappingProfile, REQUIRED_FIELDS as _AM_REQUIRED, account_profile_from_dict, apply_account_mapping
from app.services import structure_autoextend as _autoextend

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------  upload dir
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent  # backend/
UPLOAD_DIR = _BACKEND_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

MAX_UPLOAD_BYTES = settings.max_upload_mb * 1024 * 1024
# .txt is treated as delimited text (CSV-like). Real client GL/mapping exports are
# frequently ';'-separated .txt files, so .txt is a first-class delimited input.
ALLOWED_EXTENSIONS = {".csv", ".txt", ".xlsx", ".xls"}
# OB / partner-master uploads are spreadsheet-style master data and do NOT accept
# .txt (unlike the GL ingest path, which treats .txt as first-class delimited text).
OB_PARTNER_ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls"}
# Extensions parsed as delimited text — the delimiter is sniffed, not assumed.
DELIMITED_TEXT_EXTENSIONS = {".csv", ".txt"}

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
# Real GL exports for a single (entity, year) can exceed 1,000,000 lines, and the
# /gl/combine path stacks several years into one frame — keep generous headroom.
# Overridable via env MAX_PARSE_ROWS / MAX_PARSE_COLS for very large datasets.
MAX_PARSE_ROWS = int(os.environ.get("MAX_PARSE_ROWS", 10_000_000))
MAX_PARSE_COLS = int(os.environ.get("MAX_PARSE_COLS", 256))

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
    # Headerless support: ``has_header`` is the EFFECTIVE decision used to stage the
    # file; ``header_detected`` is the auto-detect guess (so the frontend can seed a
    # toggle). For a headerless file the columns are synthetic ``Column 1..N``.
    has_header: bool = True
    header_detected: bool = True


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
    # booking_line_id is a 62-bit hash (> 2**53). Accept ids as JSON STRINGS so the
    # exact digit sequence survives JavaScript float64; int kept for back-compat.
    exclude_line_ids: list[str | int] = Field(default_factory=list)
    # Stage-aware check selection. None => "all" (full catalog, backward-compatible).
    # "gl" at Project-Setup skips M1/R1-R4 (no chart mapping / partner tables yet).
    stage: str | None = None


class ExclusionSuggestionOut(BaseModel):
    count: int
    reason: str
    # STRINGS: booking_line_id is a 62-bit hash (> 2**53); a JSON number would lose
    # precision as JavaScript float64, so suggested ids are carried as exact digits.
    line_ids: list[str] = []


class ExclusionsOut(BaseModel):
    active_count: int = 0
    # STRINGS (same reason as ExclusionSuggestionOut.line_ids): the Finish commit
    # sources exclude_line_ids from these, so they must round-trip exactly.
    active_line_ids: list[str] = []
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
    # See ValidateRequest.exclude_line_ids — strings preserve 62-bit hash precision.
    exclude_line_ids: list[str | int] = Field(default_factory=list)
    check_id: str = "S1"
    field: str
    search: str | None = None
    limit: int = Field(10_000, ge=1, le=50_000)
    offset: int = Field(0, ge=0)
    # B1 (booking balance) drill-down: identify the offending booking.
    journal_entry_group_number: str | None = None
    fiscal_year: int | None = None


class IssueRowsStatsOut(BaseModel):
    row_count: int
    amount_sum: float | None = None
    fiscal_years: list[int] = Field(default_factory=list)


class IssueRowsResponse(BaseModel):
    stats: IssueRowsStatsOut
    columns: list[str] = Field(default_factory=list)
    rows: list[dict] = Field(default_factory=list)
    total: int = 0
    # Up to N passing rows for the same field, projected onto `columns`,
    # as a positive reference next to the failing rows.
    reference_rows: list[dict] = Field(default_factory=list)
    # B1: per-booking aggregate (e.g. {"amount": <signed sum, 2dp>}). None for S1.
    summary_row: dict | None = None
    # False when no reference rows exist at all (reference_kind == 'none').
    reference_available: bool = True
    # How the reference_rows were selected: 'same_field' (rows passing the failing
    # field), 'overall' (fully-valid / best-populated fallback), or 'none'.
    reference_kind: str = "none"
    # FULL set of offending booking_line_ids for this field across ALL pages
    # (honouring exclude_line_ids) so the UI can exclude every offender in one click.
    # STRINGS: a 62-bit hash id (> 2**53) would lose precision as a JSON number when
    # parsed by JavaScript float64, so ids are carried as exact digit strings.
    offender_ids: list[str] = Field(default_factory=list)
    total_offenders: int = 0
    # The canonical field that failed (S1), so the UI can highlight that column.
    # None for B1 (a balance drill-down has no single failing field).
    failing_field: str | None = None
    # Display labels (English) for `columns`, e.g. {"amount": "Amount"}.
    column_labels: dict[str, str] = Field(default_factory=dict)


class ValidateResponse(BaseModel):
    summary: dict
    key_preview: list[dict]
    unmapped_accounts: list[str]
    results: list[CheckResultOut]
    exclusions: ExclusionsOut = Field(default_factory=ExclusionsOut)


# --- Dataset viewer (server-paginated "full dataset" preview) --------------- #
class DatasetMember(BaseModel):
    file_id: str
    entity: str
    sheet: str | None = None


class ColumnFilter(BaseModel):
    field: str
    op: Literal["contains", "equals", "gte", "lte", "between"]
    value: str | float | None = None
    value2: float | None = None


class DatasetRowsRequest(BaseModel):
    members: list[DatasetMember] = Field(..., min_length=1, max_length=200)
    profile: dict  # SHARED group profile; entity.value is overridden per member
    offset: int = Field(0, ge=0)
    limit: int = Field(100, ge=1, le=1000)
    sort_by: str | None = None
    sort_dir: Literal["asc", "desc"] = "asc"
    filters: list[ColumnFilter] = Field(default_factory=list)


class DatasetColumn(BaseModel):
    key: str
    label: str
    type: Literal["string", "number", "date"]


class DatasetRowsResponse(BaseModel):
    columns: list[DatasetColumn]
    rows: list[dict]
    total: int
    filtered_total: int
    offset: int
    limit: int


class CommitRequest(BaseModel):
    file_id: str
    sheet: str | None = None
    profile: dict
    dataset: str = "gl"
    confirm_soft: bool = False
    # See ValidateRequest.exclude_line_ids — strings preserve 62-bit hash precision.
    exclude_line_ids: list[str | int] = Field(default_factory=list)
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
    # Idempotency short-circuit: True when a REPLACE re-commit of identical data over
    # the same rectangle was detected and skipped (no delete/re-insert). Additive and
    # backward-compatible — existing clients ignore it (defaults False on normal loads).
    unchanged: bool = False


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
    # New CoA wizard: external entity prefix (1-2 numeric) supplied by the frontend
    # for Entity-less templates; statement selects a single Master sheet ("bs"/"pl").
    entity_prefix: str | None = None
    # Multi-entity CoA: a single shared (Entity-less) Master workbook can be committed
    # to SEVERAL group members in ONE atomic transaction. When present this takes
    # precedence over the scalar entity_prefix (kept for single-entity back-compat).
    entity_prefixes: list[str] | None = None
    statement: str | None = None  # "bs" | "pl" | None (both)


class ApplyLibraryKey(BaseModel):
    account_number_group: str
    fiscal_year: int


class ApplyLibraryRequest(BaseModel):
    """No-file recovery: apply the configured mapping library to specific accounts."""

    library: str = "skr03"
    keys: list[ApplyLibraryKey]


class ApplyLibraryUnresolved(BaseModel):
    account_number_group: str
    account: str
    fiscal_year: int


class ApplyLibraryResponse(BaseModel):
    inserted: int
    resolved_keys: int
    unresolved: list[ApplyLibraryUnresolved]


class ReplicateKey(BaseModel):
    account_number_group: str
    fiscal_year: int

    @field_validator("account_number_group", mode="before")
    @classmethod
    def _validate_account_number_group(cls, v: Any) -> str:
        # The replicate loop slices [:2] (prefix) and [2:] (6-char suffix), so the
        # key must be exactly prefix(2) + zfill(account, 6) = 8 numeric chars.
        # Reject malformed keys here (422) before any slicing.
        s = str(v).strip()
        if not s.isdigit() or len(s) != 8:
            raise ValueError(
                "account_number_group must be 8 numeric characters "
                "(2-char entity prefix + 6-char zero-padded account)"
            )
        return s


class ReplicateAcrossEntitiesRequest(BaseModel):
    """No-file recovery: clone an existing entity's mapping onto group members that
    share the same canonical account suffix but have no dim_gl_account row yet."""

    keys: list[ReplicateKey]


class ReplicateUnmapped(BaseModel):
    account_number_group: str
    account: str
    fiscal_year: int


class ReplicateAcrossEntitiesResponse(BaseModel):
    created: int
    created_keys: list[ReplicateKey]
    truly_unmapped: list[ReplicateUnmapped]


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


class GLCombineInput(BaseModel):
    """One staged per-year GL file for a single entity."""

    file_id: str
    fiscal_year: int
    sheet: str | None = None


class GLCombineRequest(BaseModel):
    """Combine several staged per-year GL files (one entity) into one staged file.

    Each input is one already-uploaded GL file (via POST /upload) for one fiscal
    year of the SAME entity.  All inputs must share the same column layout (order
    may differ).  The combined frame gains a constant ``fiscal_year`` column per
    source file and is re-staged so the normal map -> validate -> commit runs once.
    """

    inputs: list[GLCombineInput] = Field(..., min_length=1)


class GLCombineResponse(BaseModel):
    # /upload-compatible shape so the frontend can reuse the same map/validate/commit flow.
    file_id: str
    filename: str
    sheet: str | None = None
    sheets: list[str] = Field(default_factory=list)
    columns: list[str]
    sample: list[dict]
    dialect: dict
    row_count: int
    # Extra: source files whose existing 'fiscal_year' column was overwritten by the slot value.
    overwritten_fiscal_year_files: list[str] = Field(default_factory=list)
    # Headerless support: content-heuristic suggested GoBD label per column, so the
    # frontend can pre-fill a rename/preview UI before /apply-headers.
    suggested_headers: dict[str, str] = Field(default_factory=dict)
    # Soft heads-up when the combined frame has an unusually high column count with
    # many (near-)empty columns — the classic signature of a year file parsed with
    # the wrong delimiter.  None when nothing looks off.
    column_warning: str | None = None


class SuggestHeadersRequest(BaseModel):
    file_id: str
    sheet: str | None = None


class SuggestHeadersResponse(BaseModel):
    columns: list[str]
    suggested_headers: dict[str, str]
    sample: list[dict] = Field(default_factory=list)


class ApplyHeadersRequest(BaseModel):
    file_id: str
    sheet: str | None = None
    headers: list[str] = Field(..., min_length=1)


class ApplyHeadersResponse(BaseModel):
    # /upload-compatible body so the frontend reuses the same map/validate/commit flow.
    file_id: str
    columns: list[str]
    sample: list[dict]
    sheet: str | None = None
    row_count: int


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #
_RE_DOTFLOAT = re.compile(r"\.0$")


def _safe_filename(name: str) -> str:
    """Strip path separators and control characters from an uploaded filename."""
    return re.sub(r'[\\/:*?"<>|]', "_", Path(name).name)[:200]


_STAGED_DIALECT = {
    "format": "csv",
    "encoding": "utf-8",
    "delimiter": ",",
    "decimal": ".",
    "thousands": ",",
}


def _restage_frame(df: pd.DataFrame, columns: list[str], filename: str) -> tuple[str, Path]:
    """Re-stage ``df`` (with explicit ``columns`` as the header) as a plain UTF-8 CSV.

    Uses the SAME staging convention as /upload and /gl/combine so every downstream
    reader (/preview, /validate, /commit) sees ``columns`` as a normal header row.
    Returns ``(file_id, dest_path)``.  No DB access.
    """
    out = df.copy()
    out.columns = columns
    file_id = uuid.uuid4().hex[:16]
    safe_name = _safe_filename(filename)
    dest = UPLOAD_DIR / f"{file_id}_{safe_name}"
    out.to_csv(dest, index=False, encoding="utf-8")
    return file_id, dest


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


_DELIMITER_CANDIDATES = [";", ",", "\t", "|"]


def _sniff_delimiter(sample_text: str) -> str:
    """Detect the field delimiter of a delimited-text sample.

    Strategy:
      1. Try ``csv.Sniffer`` restricted to our candidate set (``; , \\t |``).
         Sniffer is good at picking the right delimiter even when another
         candidate appears inside quoted fields.
      2. Fall back to a counts-based heuristic over the first few non-empty
         lines: the candidate with the highest total count wins, requiring a
         consistent (and non-zero) count across the header line.
      3. Default to ``;`` when nothing is conclusive — the client's real data
         is ';'-separated, and a comma file with no other delimiter still has
         commas, so the counts heuristic resolves it before we reach here.

    Returns one of ``; , \\t |``.
    """
    import csv as _csv

    # Lines we reason over (skip blank leading lines).
    lines = [ln for ln in sample_text.splitlines() if ln.strip()][:10]
    if not lines:
        return ","

    # 1) csv.Sniffer — restricted to our candidates so it cannot pick e.g. ' '.
    #    Sniffer can be fooled by a CONSISTENT in-field character: a HEADERLESS
    #    German GL file ("01.01.2022;..;-15.895,29;..") has the same number of
    #    decimal commas on every line, so Sniffer may return ',' even though ';'
    #    is the true field separator. Guard against that: if another candidate
    #    appears on EVERY sampled line with a strictly higher and perfectly
    #    consistent per-line count, it structures the rows better -> prefer it.
    try:
        dialect = _csv.Sniffer().sniff(
            "\n".join(lines[:5]), delimiters="".join(_DELIMITER_CANDIDATES)
        )
        sniffed = dialect.delimiter
        if sniffed in _DELIMITER_CANDIDATES:
            per_line = {
                ch: [ln.count(ch) for ln in lines] for ch in _DELIMITER_CANDIDATES
            }
            sniffed_min = min(per_line[sniffed]) if per_line[sniffed] else 0
            dominant = None
            for ch in _DELIMITER_CANDIDATES:
                if ch == sniffed:
                    continue
                counts = per_line[ch]
                # present on every line, identical count per line (a real grid),
                # and strictly more fields than the sniffed candidate.
                if counts and min(counts) >= 1 and len(set(counts)) == 1 \
                        and min(counts) > sniffed_min:
                    if dominant is None or min(counts) > min(per_line[dominant]):
                        dominant = ch
            return dominant or sniffed
    except (_csv.Error, Exception):  # noqa: BLE001 — sniff is best-effort
        pass

    # 2) Counts-based heuristic. Prefer a delimiter that appears in the header
    #    line; among those, pick the highest total count over the sample.
    header = lines[0]
    totals = {ch: sum(ln.count(ch) for ln in lines) for ch in _DELIMITER_CANDIDATES}
    header_counts = {ch: header.count(ch) for ch in _DELIMITER_CANDIDATES}
    in_header = {ch: header_counts[ch] for ch in _DELIMITER_CANDIDATES if header_counts[ch] > 0}
    if in_header:
        # Tie-break by candidate priority (; , \t |) for determinism.
        best = max(in_header, key=lambda c: (totals[c], -_DELIMITER_CANDIDATES.index(c)))
        return best
    if any(totals.values()):
        return max(totals, key=lambda c: (totals[c], -_DELIMITER_CANDIDATES.index(c)))

    # 3) Nothing found (single column). Comma is the safe canonical default.
    return ","


def _detect_dialect(raw_bytes: bytes, extension: str) -> dict:
    """Best-effort delimited-text dialect detection; safe defaults for xlsx.

    ``.csv`` and ``.txt`` are both treated as delimited text: the delimiter is
    sniffed (``; , \\t |``) rather than assumed, so a ';'-separated ``.txt``
    splits into the correct columns instead of one giant column. Comma ``.csv``
    behaviour is preserved. The returned ``decimal``/``thousands`` reflect the
    sniffed delimiter (German ';'/tab files imply ',' decimals) for downstream
    number parsing.
    """
    if extension in (".xlsx", ".xls"):
        return {"format": "xlsx", "encoding": "n/a", "delimiter": "n/a",
                "decimal": ".", "thousands": ","}
    encoding = _detect_encoding(raw_bytes)
    sample_text = raw_bytes[:8192].decode(encoding, errors="replace")
    delimiter = _sniff_delimiter(sample_text)
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


# Sentinel for _load_file(header=...): "caller did not pin a header row" -> use the
# default (first line is the header). Distinct from an explicit ``None``, which
# means HEADERLESS (no header row; first physical line is data).
_HEADER_DEFAULT = "__default__"


def _load_file(
    path: Path,
    sheet: str | None,
    dialect: dict,
    header: int | None | str = _HEADER_DEFAULT,
) -> pd.DataFrame:
    """Load an XLSX or delimited-text (.csv / .txt) file to a DataFrame.

    Delimited text uses the sniffed ``dialect['delimiter']`` (see
    ``_detect_dialect``) so a ';'-separated ``.txt`` splits into the correct
    columns. The delimiter is escaped for the regex-free C parser by passing the
    single character verbatim (pandas treats a 1-char ``sep`` as a literal).

    ``header`` semantics:
      * ``_HEADER_DEFAULT`` (default) — first line is the header (today's behaviour).
      * an int N — that row index is the header.
      * ``None`` — HEADERLESS: no header row, first physical line stays as data.
    """
    # Resolve the sentinel to pandas' actual default (header=0).
    pandas_header: int | None = 0 if header == _HEADER_DEFAULT else header  # type: ignore[assignment]
    ext = path.suffix.lower()
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(
            path, sheet_name=sheet or 0, dtype=str, na_values=[""], header=pandas_header
        )
    # Delimited text (.csv / .txt) — delimiter sniffed, never assumed.
    return pd.read_csv(
        path,
        sep=dialect.get("delimiter", ",") or ",",
        encoding=dialect.get("encoding", "utf-8") or "utf-8",
        dtype=str,
        na_values=[""],
        header=pandas_header,
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
    *,
    opening_balance: bool = False,
) -> tuple[pd.DataFrame, dict]:
    """Load file + apply profile; returns (canonical_lines, dialect).

    ``opening_balance=True`` is the OB path: OB files carry only account number +
    amount, so posting_date / journal_entry_number become optional (see
    ``etl.mapping.apply_profile``). GL/CoA/Partner callers MUST leave it False so the
    GL contract (both columns strictly required) stays byte-identical.
    """
    path = _file_path_from_id(file_id)
    raw_bytes = path.read_bytes()
    dialect = _detect_dialect(raw_bytes, path.suffix.lower())
    raw_df = _load_file(path, sheet, dialect)

    profile = profile_from_dict(profile_dict)
    # Did the CALLER explicitly force a decimal separator? '.' is the dataclass
    # default (== "not forced"), so a non-'.' value means the caller pinned it and
    # neither the dialect fill nor the OB sniff below may override it.
    caller_forced_decimal = profile.decimal != "."
    # Fill decimal/thousands from dialect if not explicitly set in profile
    if profile.decimal == "." and dialect.get("decimal"):
        profile.decimal = dialect["decimal"]
    if profile.thousands == "," and dialect.get("thousands"):
        profile.thousands = dialect["thousands"]

    # OB path ONLY: the amount column is the reliable money signal, so sniff the
    # actual amount-column sample and let a confident result win. Layered
    # precedence: confident amount-column sniff -> delimiter dialect (above) -> '.'.
    # This preserves German ';'-delimited DATEV (dialect -> ',' and OB values like
    # '52.803,84' also sniff to ',') and fixes comma-delimited/US OB files whose
    # amounts ('52803.84') the dialect would otherwise mis-parse with decimal ','.
    # The GL path (opening_balance=False) is deliberately untouched.
    if opening_balance and not caller_forced_decimal:
        from etl.mapping import sniff_decimal_separator

        sign_cfg = profile.sign or {}
        amount_cols = [
            sign_cfg.get(k)
            for k in ("amount", "soll", "haben")
            if sign_cfg.get(k) and sign_cfg.get(k) in raw_df.columns
        ]
        if amount_cols:
            sample = (
                pd.concat([raw_df[c] for c in amount_cols])
                .dropna()
                .astype(str)
                .tolist()
            )
            sniffed = sniff_decimal_separator(sample)
            if sniffed is not None:
                profile.decimal, profile.thousands = sniffed

    # Decidra GoBD: retain Jan-1 opening balances missing Transaction number
    from etl.gobd_gl_prepare import prepare_gobd_gl_frame, tag_opening_balances_in_canonical

    if "Entity No" in raw_df.columns and "Transaction number" in raw_df.columns:
        raw_df = prepare_gobd_gl_frame(raw_df)

    entity_lookup: dict[str, str] = {}
    if session is not None:
        from etl.entity_resolve import build_entity_lookup

        entity_lookup = build_entity_lookup(session)

    try:
        canonical = apply_profile(
            raw_df,
            profile,
            entity_lookup=entity_lookup,
            opening_balance=opening_balance,
            # OB is supplementary data: silently drop rows for entities that are
            # not part of the project. GL (opening_balance=False) still raises on
            # unknown entities, so GL ingestion stays byte-identical.
            drop_unknown_entities=opening_balance,
        )
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


# --------------------------------------------------------------------------- #
# Dataset viewer (server-paginated "full dataset" preview) — read-only.
# A DEDICATED concat cache, kept separate from _APPLY_CACHE so a big multi-entity
# preview never evicts the small per-file validation cache (and vice versa).
# --------------------------------------------------------------------------- #
_CONCAT_CACHE: dict[str, tuple[float, pd.DataFrame, list[dict], int]] = {}
_CONCAT_CACHE_TTL_SEC = 900
_CONCAT_CACHE_MAX = 2

# Canonical fields ALWAYS present in an apply_profile frame (derived from the
# required source columns), shown regardless of the optional column mapping.
_DATASET_REQUIRED_FIELDS = {
    "journal_entry_group_number",
    "fiscal_period",
    "line_number",
    "booking_line_id",
    "account_number_group",
    "gl_account_id",
    "amount",
    "posting_date",
}
# Optional canonical fields and the profile.columns key(s) that activate them.
# A field is "mapped" (and therefore shown) when ANY of its source keys is set.
_DATASET_OPTIONAL_FIELDS: dict[str, tuple[str, ...]] = {
    "vat_amount": ("vat_amount",),
    "line_note": ("line_note",),
    "customer_id": ("source_type", "source_no"),
    "supplier_id": ("source_type", "source_no"),
    "posting_type": ("posting_type",),
    "document_date": ("document_date",),
    "document_type_code": ("document_type",),
    "reference_document_number": ("reference_document_number",),
    "currency_code": ("currency",),
    "header_note": ("header_note",),
}
# Stable display order for the data columns (Entity + fiscal_year are prepended).
_DATASET_FIELD_ORDER = [
    "journal_entry_group_number",
    "fiscal_period",
    "line_number",
    "booking_line_id",
    "account_number_group",
    "gl_account_id",
    "amount",
    "vat_amount",
    "line_note",
    "customer_id",
    "supplier_id",
    "posting_type",
    "posting_date",
    "document_date",
    "document_type_code",
    "reference_document_number",
    "currency_code",
    "header_note",
]


def _dataset_mapped_fields(profile_dict: dict) -> list[str]:
    """Canonical data fields to show: required + optional whose mapping is set.

    apply_profile always emits the full canonical schema (null-filling unmapped
    optional columns), so "mapped only" is resolved from profile.columns here
    rather than by inspecting the frame.  ``fiscal_year`` / ``__entity__`` are
    handled separately by the caller and are NOT included.
    """
    cols = profile_dict.get("columns") or {}
    selected: set[str] = set(_DATASET_REQUIRED_FIELDS)
    for field_name, src_keys in _DATASET_OPTIONAL_FIELDS.items():
        if any(cols.get(k) for k in src_keys):
            selected.add(field_name)
    return [f for f in _DATASET_FIELD_ORDER if f in selected]


def _dataset_humanize(key: str) -> str:
    return key.replace("_", " ").strip().title()


def _dataset_label(profile_dict: dict, key: str) -> str:
    """Column label: profile-supplied field label if present, else humanized key."""
    labels = profile_dict.get("labels") or profile_dict.get("field_labels") or {}
    if isinstance(labels, dict) and labels.get(key):
        return str(labels[key])
    return _dataset_humanize(key)


def _dataset_col_type(series: pd.Series) -> str:
    if pd.api.types.is_datetime64_any_dtype(series):
        return "date"
    if pd.api.types.is_numeric_dtype(series):
        return "number"
    return "string"


def _dataset_concat_cache_key(
    members: list["DatasetMember"], profile_dict: dict
) -> str:
    """sha256 over the sorted member triples + the shared profile (entity.value removed)."""
    shared = dict(profile_dict)
    ent = dict(shared.get("entity") or {})
    ent.pop("value", None)
    shared["entity"] = ent
    payload = json.dumps(
        {
            "members": sorted(
                (m.file_id, m.entity, m.sheet or "") for m in members
            ),
            "profile": shared,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _dedupe_dataset_members(
    members: list["DatasetMember"],
) -> list["DatasetMember"]:
    """Drop repeated members by (file_id, entity, sheet), preserving first-seen order.

    Guards against a caller repeating the same large file N times to multiply peak
    memory in the concat build, and stabilizes the concat cache key.
    """
    seen: set[tuple[str, str, str]] = set()
    unique: list[DatasetMember] = []
    for m in members:
        triple = (m.file_id, m.entity, m.sheet or "")
        if triple in seen:
            continue
        seen.add(triple)
        unique.append(m)
    return unique


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
    has_header: bool | None = Form(None),
    _user: User = Depends(current_user),
) -> UploadResponse:
    """Save uploaded GL file; detect dialect + columns; return preview.

    Headerless support: when ``has_header`` is omitted the first parsed row is
    auto-checked against the body — a file whose first line is already DATA (mostly
    numeric / date / code, or pandas produced ``Unnamed: N`` / numeric column names)
    is treated as HEADERLESS.  A headerless file is re-staged with synthetic
    ``Column 1..N`` headers so every downstream reader (incl. /gl/combine) sees a
    normal header row and two same-layout headerless files match.  ``has_header`` /
    ``header_detected`` are returned so the frontend can seed a toggle.
    """
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
    effective_sheet = sheet or (sheets[0] if sheets else None)
    try:
        df = _load_file(
            dest, effective_sheet, dialect,
            header=header_row if header_row is not None else _HEADER_DEFAULT,
        )
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"Could not parse file: {exc}") from exc

    # --- headerless detection.  An explicit header_row pin keeps today's behaviour
    #     (the caller chose the header), so only auto-handle when header_row is None.
    header_detected = True
    if header_row is None:
        try:
            body = df.head(20).values.tolist()
            header_detected = detect_has_header(
                first_row=list(df.columns),
                body_rows=body,
                column_names=list(df.columns),
            )
        except Exception as exc:  # noqa: BLE001 — detection must not break upload
            logger.warning("upload: header auto-detect failed (%s); assuming header", exc)
            header_detected = True
    effective_has_header = bool(has_header) if has_header is not None else header_detected

    if not effective_has_header:
        # Re-parse with no header so the first physical line is data, then assign
        # synthetic positional names and RE-STAGE so downstream sees a normal header.
        try:
            df = _load_file(dest, effective_sheet, dialect, header=None)
        except Exception as exc:
            dest.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail=f"Could not parse file: {exc}") from exc
        columns = synthetic_headers(df.shape[1])
        new_id, new_dest = _restage_frame(df, columns, "headerless_gl.csv")
        dest.unlink(missing_ok=True)  # the original (header-promoted) staging is stale
        file_id, dest, dialect = new_id, new_dest, dict(_STAGED_DIALECT)
        sheets = []
        df.columns = columns
    else:
        columns = list(df.columns)

    sample = df.head(10).fillna("").to_dict(orient="records")

    return UploadResponse(
        file_id=file_id,
        filename=safe_name,
        sheets=sheets,
        columns=columns,
        sample=sample,
        dialect=dialect,
        has_header=effective_has_header,
        header_detected=header_detected,
    )


# --------------------------------------------------------------------------- #
# POST /gl/combine
# --------------------------------------------------------------------------- #
_FISCAL_YEAR_COL = "fiscal_year"


def _load_staged_frame(file_id: str, sheet: str | None) -> pd.DataFrame:
    """Load a staged file by file_id using the SAME loader as the preview path.

    Mirrors ``/entity/preview``: resolve the staged path, detect the dialect from
    the bytes, then parse to a string-typed DataFrame.  No DB access.
    """
    path = _file_path_from_id(file_id)
    raw_bytes = path.read_bytes()
    dialect = _detect_dialect(raw_bytes, path.suffix.lower())
    return _load_file(path, sheet, dialect)


@router.post("/gl/combine", response_model=GLCombineResponse)
def gl_combine(
    body: GLCombineRequest,
    _user: User = Depends(current_user),
) -> GLCombineResponse:
    """Combine several staged per-year GL files (one entity) into ONE staged file.

    For each input (a staged file_id + its fiscal_year for ONE entity):
      1. Load the staged frame via the shared upload/preview loader (no DB write).
      2. Verify every input shares the SAME column set (order may differ); a
         mismatch returns 422 listing the offending file and the column diff.
      3. Add/overwrite a constant ``fiscal_year`` column with the slot value (the
         slot is authoritative — an existing ``fiscal_year`` column is overwritten
         and the file is reported in ``overwritten_fiscal_year_files``).
      4. Row-stack the frames in input order (reconciled to the first file's
         column order, with ``fiscal_year`` appended).
      5. Re-stage the combined frame as a NEW staged CSV (same UPLOAD_DIR store /
         format the downstream /preview, /validate, /commit endpoints read by
         file_id) and return an /upload-compatible response.

    Read-only w.r.t. the DB — pure staging, no fact writes.  Use fiscal_year
    mode 'column' value 'fiscal_year' in the mapping profile to resolve the year.
    """
    # --- load + validate column compatibility -----------------------------
    frames: list[pd.DataFrame] = []
    base_cols: list[str] | None = None
    base_col_set: set[str] | None = None
    overwritten: list[str] = []

    for idx, item in enumerate(body.inputs):
        df = _load_staged_frame(item.file_id, item.sheet)
        _assert_parse_bounds(df, what=f"file {item.file_id!r}")

        # Compare the source columns (excluding any pre-existing fiscal_year, which
        # the slot value overwrites and which is therefore not part of the layout
        # contract between files).
        src_cols = [c for c in df.columns if c != _FISCAL_YEAR_COL]
        if base_cols is None:
            base_cols = src_cols
            base_col_set = set(src_cols)
        elif set(src_cols) != base_col_set:
            missing = sorted(base_col_set - set(src_cols))
            extra = sorted(set(src_cols) - base_col_set)
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Column mismatch in file {item.file_id!r} (input {idx}): "
                    f"the per-entity files must share the same layout. "
                    f"Missing: {missing}; unexpected: {extra}. "
                    f"Expected columns: {base_cols}."
                ),
            )

        # Reconcile to the first file's column order, then set the authoritative FY.
        reordered = df[base_cols].copy()
        if _FISCAL_YEAR_COL in df.columns:
            overwritten.append(item.file_id)
        reordered[_FISCAL_YEAR_COL] = str(int(item.fiscal_year))
        frames.append(reordered)

    combined = pd.concat(frames, axis=0, ignore_index=True)
    out_columns = list(base_cols) + [_FISCAL_YEAR_COL]

    # --- re-stage as a NEW CSV staged file (downstream reads it by file_id) -
    _sweep_stale_uploads()
    file_id = uuid.uuid4().hex[:16]
    safe_name = "combined_gl.csv"
    dest = UPLOAD_DIR / f"{file_id}_{safe_name}"
    # Plain UTF-8 comma CSV so _detect_dialect/_load_file round-trip the
    # string frame exactly on the downstream /validate + /commit reads.
    combined.to_csv(dest, index=False, encoding="utf-8")

    sample = combined.head(50).fillna("").to_dict(orient="records")
    # Content-heuristic header suggestions over the SOURCE columns (the constant
    # fiscal_year column is already named, so exclude it). Helps the frontend
    # pre-fill a rename UI when the inputs were headerless (Column 1..N).
    try:
        suggested = suggest_column_names(sample, list(base_cols))
    except Exception as exc:  # noqa: BLE001 — suggestion is advisory
        logger.warning("gl_combine: header suggestion failed (%s)", exc)
        suggested = {}

    # Soft heads-up: a year file parsed with the WRONG delimiter inflates the column
    # count with many near-empty fragment columns. A correct GL combine keeps the
    # source layout (source columns + fiscal_year), so a high column count where a
    # large share of columns are almost entirely empty signals a likely misparse.
    column_warning: str | None = None
    n_src = len(base_cols)
    if n_src >= 15 and sample:
        empty_cols = 0
        for c in base_cols:
            non_empty = sum(1 for r in sample if str(r.get(c, "")).strip())
            if non_empty <= max(1, len(sample) // 10):
                empty_cols += 1
        if empty_cols >= max(3, round(n_src * 0.4)):
            column_warning = (
                f"{n_src} columns were detected and {empty_cols} of them are almost "
                f"entirely empty. The year files may have been parsed with the wrong "
                f"delimiter — please check the source files before assigning headers."
            )

    return GLCombineResponse(
        file_id=file_id,
        filename=safe_name,
        sheet=None,
        sheets=[],
        columns=out_columns,
        sample=sample,
        dialect={"format": "csv", "encoding": "utf-8", "delimiter": ",",
                 "decimal": ".", "thousands": ","},
        row_count=int(len(combined)),
        overwritten_fiscal_year_files=overwritten,
        suggested_headers=suggested,
        column_warning=column_warning,
    )


# --------------------------------------------------------------------------- #
# POST /suggest-headers
# --------------------------------------------------------------------------- #
@router.post("/suggest-headers", response_model=SuggestHeadersResponse)
def suggest_headers(
    body: SuggestHeadersRequest,
    _user: User = Depends(current_user),
) -> SuggestHeadersResponse:
    """Content-heuristic GoBD label suggestion for a staged file's columns.

    Reads the staged file (no DB), profiles each column over a sample, and returns
    ``{column: suggested_name}`` aligned to the frontend GoBD labels.  Columns with
    no confident guess map to their existing (often synthetic ``Column N``) name.
    """
    df = _load_staged_frame(body.file_id, body.sheet)
    _assert_parse_bounds(df, what=f"file {body.file_id!r}")
    columns = list(df.columns)
    sample = df.head(20).fillna("").to_dict(orient="records")
    suggested = suggest_column_names(sample, columns)
    return SuggestHeadersResponse(
        columns=columns,
        suggested_headers=suggested,
        sample=df.head(10).fillna("").to_dict(orient="records"),
    )


# --------------------------------------------------------------------------- #
# POST /apply-headers
# --------------------------------------------------------------------------- #
@router.post("/apply-headers", response_model=ApplyHeadersResponse)
def apply_headers(
    body: ApplyHeadersRequest,
    _user: User = Depends(current_user),
) -> ApplyHeadersResponse:
    """Re-stage a file with user-confirmed column headers.

    ``headers`` length must equal the staged file's column count and contain no
    duplicates.  The file is re-staged as a normal CSV (same convention as /upload
    and /gl/combine) so the subsequent map -> validate -> commit run unchanged.
    Returns an /upload-compatible body.
    """
    df = _load_staged_frame(body.file_id, body.sheet)
    _assert_parse_bounds(df, what=f"file {body.file_id!r}")

    n = df.shape[1]
    headers = [str(h).strip() for h in body.headers]
    if len(headers) != n:
        raise HTTPException(
            status_code=422,
            detail=(
                f"headers length ({len(headers)}) must equal the column count ({n})."
            ),
        )
    seen: set[str] = set()
    dupes = sorted({h for h in headers if h in seen or seen.add(h)})
    if dupes:
        raise HTTPException(
            status_code=422,
            detail=f"headers must be unique; duplicates: {dupes}.",
        )

    new_id, _dest = _restage_frame(df, headers, "renamed_gl.csv")
    df.columns = headers
    sample = df.head(10).fillna("").to_dict(orient="records")
    return ApplyHeadersResponse(
        file_id=new_id,
        columns=headers,
        sample=sample,
        sheet=None,
        row_count=int(len(df)),
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
        text("SELECT id, name, source_system, profile_json, created_at FROM org_ingest_mapping_profile ORDER BY id DESC")
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
                "INSERT INTO org_ingest_mapping_profile (name, source_system, profile_json) "
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
        text("SELECT id, name, source_system, profile_json, created_at FROM org_ingest_mapping_profile WHERE id = :id"),
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

    # Stage-aware check selection. None/omitted => "all" (full catalog), so the
    # default response (ids + order + shape) stays byte-identical to before.
    allowed = set(C.checks_for_stage(body.stage))

    # Partner linking (always needed: balance / structural checks run on `linked`).
    strategy = body.profile.get("linking_strategy", "txn")
    linked_full = D.link_partners(canonical, strategy)
    exclusions_out = exclusion_summary(linked_full, body.exclude_line_ids)
    linked = apply_line_exclusions(linked_full, body.exclude_line_ids)

    # Derived facts — only needed for R1-R4 reconciliation. Skip the work when no
    # reconciliation check is in scope for this stage.
    _RECON_IDS = {"R1", "R2", "R3", "R4"}
    if allowed & _RECON_IDS:
        fact_ar = D.derive_ar(linked)
        fact_ap = D.derive_ap(linked)
        fact_sales = D.derive_sales(linked)
        fact_com = D.derive_com(linked)

    # Checks — appended in the canonical order (must match STAGE_CHECKS["all"]),
    # each gated by stage membership so the default ("all") case is unchanged.
    required = ["journal_entry_group_number", "fiscal_year", "line_number",
                "booking_line_id", "account_number_group", "amount", "posting_date"]
    results: list[C.CheckResult] = []
    if "S1" in allowed:
        results.append(C.check_required_fields(linked, required))
    if "S2" in allowed:
        results.append(C.check_s2_posting_fiscal_year(linked))
    if "B1" in allowed:
        results.append(C.check_booking_balance(linked))
    if "B3" in allowed:
        results.append(C.check_monthly_balance(linked))
    if "B2" in allowed:
        results.append(C.check_ledger_balance(linked))
    if "Q2" in allowed:
        results.append(C.check_unique_booking_line_id(linked))
    if "R1" in allowed:
        results.append(C.check_r1_ar(fact_ar, linked))
    if "R2" in allowed:
        results.append(C.check_r2_ap(fact_ap, linked))
    if "R3" in allowed:
        results.append(C.check_r3_sales(fact_sales, linked))
    if "R4" in allowed:
        results.append(C.check_r4_com(fact_com, linked))

    # M1: check mapping coverage via DB — only when in scope (skips the DB query
    # at GL Project-Setup, where no chart mapping exists yet).
    if "M1" in allowed:
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
    # Cast to object before filling NA — a nullable Int64 column (e.g. fiscal_year /
    # booking_line_id with NA from an unparseable posting_date) rejects fillna("").
    _preview = linked[preview_cols].head(10).astype(object)
    key_preview = _preview.where(_preview.notna(), "").to_dict(orient="records")

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
    """Return flat source rows for one validation issue (S1 fields or a B1 booking)."""
    if body.check_id not in ("S1", "B1"):
        raise HTTPException(
            status_code=400,
            detail=f"issue-rows not supported for check {body.check_id!r}",
        )

    from etl.issue_rows import collect_b1_issue_rows, collect_s1_issue_rows
    from etl.line_exclude import apply_line_exclusions

    canonical, _, raw_df = _load_and_apply_cached(body.file_id, body.sheet, body.profile, session)
    strategy = body.profile.get("linking_strategy", "txn")
    linked = D.link_partners(canonical, strategy)
    linked = apply_line_exclusions(linked, body.exclude_line_ids)

    if body.check_id == "B1":
        out = collect_b1_issue_rows(
            linked,
            raw_df,
            journal_entry_group_number=body.journal_entry_group_number,
            fiscal_year=body.fiscal_year,
            exclude_line_ids=body.exclude_line_ids,
            limit=body.limit,
            offset=body.offset,
        )
        reference_rows = out.get("reference_rows", [])
        return IssueRowsResponse(
            stats=IssueRowsStatsOut(**out["stats"]),
            columns=out["columns"],
            column_labels=out.get("column_labels", {}),
            rows=out["rows"],
            total=out["total"],
            reference_rows=reference_rows,
            summary_row=out.get("summary_row"),
            reference_available=len(reference_rows) > 0,
            failing_field=out.get("failing_field"),
        )

    # S1 required-field list — same definition validate() uses for check_required_fields.
    s1_required = [
        "journal_entry_group_number", "fiscal_year", "line_number",
        "booking_line_id", "account_number_group", "amount", "posting_date",
    ]
    out = collect_s1_issue_rows(
        linked,
        raw_df,
        body.field,
        exclude_line_ids=body.exclude_line_ids,
        search=body.search,
        limit=body.limit,
        offset=body.offset,
        required_fields=s1_required,
    )
    reference_kind = out.get("reference_kind", "none")
    return IssueRowsResponse(
        stats=IssueRowsStatsOut(**out["stats"]),
        columns=out["columns"],
        column_labels=out.get("column_labels", {}),
        rows=out["rows"],
        total=out["total"],
        reference_rows=out.get("reference_rows", []),
        reference_kind=reference_kind,
        reference_available=reference_kind != "none",
        offender_ids=out.get("offender_ids", []),
        total_offenders=out.get("total_offenders", 0),
        failing_field=out.get("failing_field"),
    )


# --------------------------------------------------------------------------- #
# POST /dataset/rows — server-paginated "full dataset" viewer (read-only)
# --------------------------------------------------------------------------- #
def _build_dataset_concat(
    members: list[DatasetMember],
    profile_dict: dict,
    session: Session,
) -> tuple[pd.DataFrame, list[dict], int]:
    """Build the projected, memory-bounded concat frame for the members.

    Each member is loaded UNCACHED (``_load_and_apply``, not the cached variant) so
    this large multi-entity preview never evicts the small per-file validation
    ``_APPLY_CACHE``.  Each full member frame is freed before the next is loaded to
    cap peak memory.  Returns ``(frame, columns_meta, total)``.
    """
    data_fields = _dataset_mapped_fields(profile_dict)
    projected: list[pd.DataFrame] = []
    estimated_bytes = 0

    for member in members:
        # Override only entity.value per member; the rest of the profile is shared.
        prof = dict(profile_dict)
        ent = dict(prof.get("entity") or {})
        ent["value"] = member.entity
        prof["entity"] = ent

        canonical, _dialect, _raw = _load_and_apply(
            member.file_id, member.sheet, prof, session
        )
        keep = [f for f in data_fields if f in canonical.columns]
        if "fiscal_year" in canonical.columns:
            keep = keep + ["fiscal_year"]
        member_proj = canonical.loc[:, keep].copy()
        member_proj["__entity__"] = member.entity
        # Free the full member frame before loading the next one (cap peak memory).
        del canonical
        # Incremental memory ceiling: bail out (413) as soon as the accumulated
        # projected frames cross the limit, before pd.concat holds them all at once.
        estimated_bytes += int(member_proj.memory_usage(deep=True).sum())
        if estimated_bytes > settings.dataset_viewer_max_bytes:
            raise HTTPException(
                status_code=413,
                detail="Dataset too large to preview in full; validate per entity or apply a filter.",
            )
        projected.append(member_proj)

    frame = pd.concat(projected, ignore_index=True)
    del projected

    # Column order: Entity, fiscal_year, then mapped data fields.
    ordered = ["__entity__"]
    if "fiscal_year" in frame.columns:
        ordered.append("fiscal_year")
    ordered += [c for c in data_fields if c in frame.columns]
    frame = frame.loc[:, ordered]

    # Column metadata from dtypes BEFORE downcasting categoricals.
    columns_meta: list[dict] = [
        {"key": "__entity__", "label": "Entity", "type": "string"}
    ]
    for key in ordered:
        if key == "__entity__":
            continue
        columns_meta.append(
            {
                "key": key,
                "label": _dataset_label(profile_dict, key),
                "type": _dataset_col_type(frame[key]),
            }
        )

    # Downcast to bound memory: numerics to smallest type; low-cardinality
    # __entity__ / fiscal_year to category.
    for key in frame.columns:
        if key in ("__entity__", "fiscal_year"):
            frame[key] = frame[key].astype("category")
        elif pd.api.types.is_float_dtype(frame[key]):
            frame[key] = pd.to_numeric(frame[key], downcast="float")
        elif pd.api.types.is_integer_dtype(frame[key]):
            frame[key] = pd.to_numeric(frame[key], downcast="integer")

    return frame, columns_meta, int(len(frame))


def _dataset_apply_filters(
    frame: pd.DataFrame, filters: list[ColumnFilter]
) -> pd.DataFrame:
    """AND-combine the column filters into one boolean mask; vectorized."""
    if not filters:
        return frame
    mask = pd.Series(True, index=frame.index)
    for flt in filters:
        if flt.field not in frame.columns:
            raise HTTPException(
                status_code=400, detail=f"unknown filter field {flt.field!r}"
            )
        col = frame[flt.field]
        if flt.op == "contains":
            term = "" if flt.value is None else str(flt.value)
            mask &= col.astype(str).str.contains(term, case=False, na=False, regex=False)
        elif flt.op == "equals":
            target = "" if flt.value is None else str(flt.value)
            mask &= col.astype(str) == target
        elif flt.op in ("gte", "lte", "between"):
            if flt.value is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"filter op {flt.op!r} on {flt.field!r} requires a numeric value",
                )
            numeric = pd.to_numeric(col, errors="coerce")
            if flt.op == "gte":
                mask &= numeric >= float(flt.value)
            elif flt.op == "lte":
                mask &= numeric <= float(flt.value)
            else:  # between
                lo = float(flt.value)
                hi = float(flt.value2) if flt.value2 is not None else float(flt.value)
                mask &= numeric.between(lo, hi)
    return frame[mask]


@router.post("/dataset/rows", response_model=DatasetRowsResponse)
def dataset_rows(
    body: DatasetRowsRequest,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> DatasetRowsResponse:
    """Server-paginated full-dataset viewer over one or more staged GL files.

    Read-only: no DB writes.  Loads each member's canonical frame (uncached),
    projects to the mapped target fields + Entity + fiscal_year, concatenates,
    then filters / sorts / slices in memory.  The projected concat frame is cached
    (TTL/size-bounded) so paging does not re-parse the source files.
    """
    # --- 1. authorize members (fail-closed for non-admins) -----------------
    from app.services.entity_visibility import visible_entity_codes

    allowed = visible_entity_codes(session, user)
    if allowed is not None:  # non-admin → restricted to granted codes
        forbidden = sorted({m.entity for m in body.members if m.entity not in allowed})
        if forbidden:
            raise HTTPException(
                status_code=403,
                detail=f"Not authorized for entit{'y' if len(forbidden) == 1 else 'ies'}: "
                + ", ".join(forbidden),
            )

    limit = min(body.limit, settings.dataset_viewer_max_page)

    # De-duplicate members (by file_id/entity/sheet) so a repeated file cannot
    # multiply peak memory in the concat build; also stabilizes the cache key.
    members = _dedupe_dataset_members(body.members)

    # --- 2. build (or reuse) the projected concat frame --------------------
    key = _dataset_concat_cache_key(members, body.profile)
    now = time.monotonic()
    hit = _CONCAT_CACHE.get(key)
    if hit and now - hit[0] < _CONCAT_CACHE_TTL_SEC:
        frame, columns_meta, total = hit[1], hit[2], hit[3]
    else:
        frame, columns_meta, total = _build_dataset_concat(
            members, body.profile, session
        )
        # Memory ceiling: refuse (and do NOT cache) an oversized preview.
        if int(frame.memory_usage(deep=True).sum()) > settings.dataset_viewer_max_bytes:
            raise HTTPException(
                status_code=413,
                detail="Dataset too large to preview in full; validate per entity or apply a filter.",
            )
        _CONCAT_CACHE[key] = (now, frame, columns_meta, total)
        if len(_CONCAT_CACHE) > _CONCAT_CACHE_MAX:
            oldest = min(_CONCAT_CACHE, key=lambda k: _CONCAT_CACHE[k][0])
            del _CONCAT_CACHE[oldest]

    # --- 3. validate sort_by against the returned column keys --------------
    valid_keys = {c["key"] for c in columns_meta}
    if body.sort_by is not None and body.sort_by not in valid_keys:
        raise HTTPException(
            status_code=400, detail=f"unknown sort_by column {body.sort_by!r}"
        )

    # --- 4. filter (vectorized AND), then count ----------------------------
    filtered = _dataset_apply_filters(frame, body.filters)
    filtered_total = int(len(filtered))

    # --- 5. sort only when requested; default keeps file/concat order ------
    if body.sort_by is not None:
        filtered = filtered.sort_values(
            by=body.sort_by, ascending=(body.sort_dir == "asc"), kind="mergesort"
        )

    # --- 6. slice the page -------------------------------------------------
    page = filtered.iloc[body.offset : body.offset + limit]

    # --- 7. serialize cells ------------------------------------------------
    keys = [c["key"] for c in columns_meta]
    rows: list[dict] = [
        {k: _serialize_cell(row[k]) for k in keys}
        for row in page.to_dict(orient="records")
    ]

    return DatasetRowsResponse(
        columns=[DatasetColumn(**c) for c in columns_meta],
        rows=rows,
        total=total,
        filtered_total=filtered_total,
        offset=body.offset,
        limit=limit,
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

    # LIVE-safety: this write path holds ONE open transaction from the first SELECT
    # (entity lookup) through load_canonical, running the full check catalog +
    # derive_* + link_partners on a frame up to MAX_PARSE_ROWS BETWEEN DB
    # statements. On a large (multi-year /gl/combine) load that idle-in-transaction
    # window can exceed the global 120s idle_in_transaction_session_timeout and get
    # the connection reaped mid-commit. Raise the bound to 600s for THIS transaction
    # only (SET LOCAL via set_config is_local=true — scoped to this txn, never leaks
    # to other pooled checkouts). Mirrors the etl.rebuild override.
    session.execute(
        text("SELECT set_config('idle_in_transaction_session_timeout', '600000', true)")
    )

    canonical, _, _raw_df = _load_and_apply_cached(body.file_id, body.sheet, body.profile, session)
    canonical = apply_line_exclusions(canonical, body.exclude_line_ids)

    # HARD-BLOCK: rows with no account number (e.g. DATEV "Datumskomprimiert"
    # date-compressed summary rows) yield a NULL account_number_group, which is
    # NOT NULL in fact_gl_line. This is unrecoverable at load time, so we block it
    # here REGARDLESS of confirm_soft (a soft-confirm cannot rescue a NULL key).
    if "account_number_group" in canonical.columns:
        _ang = canonical["account_number_group"]
        _null_mask = _ang.isna() | (_ang.astype("string").str.strip() == "")
        _null_count = int(_null_mask.sum())
        if _null_count > 0:
            # Server-side technical detail only — never leaked to the client.
            _examples = []
            for _, _row in canonical[_null_mask].head(3).iterrows():
                _examples.append(
                    (
                        _row.get("journal_entry_group_number"),
                        _row.get("line_number"),
                    )
                )
            logger.warning(
                "commit: %d row(s) have a NULL/empty account_number_group; "
                "refusing to load (NOT NULL on fact_gl_line). Examples "
                "(journal_entry_group_number, line_number): %s",
                _null_count,
                _examples,
            )
            # Natural pluralization: "1 row has" vs "N rows have". The stable
            # substring the frontend humanizer keys on ("no account number")
            # survives both paths.
            _noun = "row has" if _null_count == 1 else "rows have"
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{_null_count} {_noun} no account number (for example "
                    "date-compressed summary rows) and can't be imported. "
                    "Exclude these rows in the validation step (S1 — Required "
                    "fields), or remove them from the source file, then retry."
                ),
            )

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

    # AUTO-EXPAND dim_gl_account across sibling entities — BEFORE the DF2 enrichment
    # SELECT below (placement is critical) so newly-created sibling rows are picked up
    # by classification and THIS entity's GL lines classify correctly. A single shared
    # chart of accounts committed for one member (prefix '01') otherwise leaves a
    # sibling (prefix '02') unmapped by BARE account number; here we clone the existing
    # hierarchy / NA / CF onto the committing entity by 6-char suffix. NO-OVERWRITE
    # (on_conflict="nothing") + idempotent (a re-commit clones 0 rows). Recovery must
    # never harden into a new failure, so any error is logged TYPE-only and we proceed;
    # the FK pre-flight below re-queries and still 422s for accounts under NO entity.
    try:
        if (
            "account_number_group" in canonical.columns
            and "fiscal_year" in canonical.columns
            and not canonical.empty
        ):
            _ax = canonical.dropna(subset=["account_number_group", "fiscal_year"])
            _ax_keys = set(
                zip(
                    _ax["account_number_group"].astype(str),
                    _ax["fiscal_year"].astype(int),
                )
            )
        else:
            _ax_keys = set()
        if _ax_keys:
            _ax_angs = canonical["account_number_group"].dropna().unique().tolist()
            _ax_fys = canonical["fiscal_year"].dropna().unique().tolist()
            _ax_mapped_rows = session.execute(
                text(
                    "SELECT account_number_group, fiscal_year FROM dim_gl_account "
                    "WHERE account_number_group = ANY(:angs) AND fiscal_year = ANY(:fys)"
                ),
                {"angs": _ax_angs, "fys": [int(y) for y in _ax_fys]},
            ).fetchall()
            _ax_mapped = {(str(r[0]), int(r[1])) for r in _ax_mapped_rows}
            _ax_unmapped = _ax_keys - _ax_mapped
            if _ax_unmapped:
                _replicate_dim_across_entities(
                    session, _admin, sorted(_ax_unmapped), auto_commit=True
                )
    except Exception as exc:
        session.rollback()
        logger.warning(
            "commit: auto-expand dim_gl_account failed (%s); proceeding",
            type(exc).__name__,
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

    # FK pre-flight: verify EVERY observed (account_number_group, fiscal_year) pair in
    # this GL file is mapped in dim_gl_account before loading GL lines. An ANTI-JOIN
    # over the observed GL key pairs covers BOTH the all-unmapped case AND the
    # partial-mapped case (the latter otherwise 500s later inside load_canonical on the
    # FK). When some keys are unmapped we raise an INFORMATIVE 422 that LISTS the
    # offending accounts so the frontend can offer to apply the configured mapping
    # library or send the user to the account-mapping step.
    try:
        if (
            "account_number_group" in canonical.columns
            and "fiscal_year" in canonical.columns
            and not canonical.empty
        ):
            # Drop rows with a null account_number_group OR fiscal_year before the
            # int cast: a NaN fiscal_year (from_date mode + unparseable posting date)
            # otherwise raises IntCastingNaNError, the broad except below then SILENTLY
            # skips the whole pre-flight (user loses the structured diagnostic and
            # null-fiscal_year rows slip past). Mirror the dropna used for the SELECT
            # bounds just below.
            _pf = canonical.dropna(subset=["account_number_group", "fiscal_year"])
            gl_keys = set(
                zip(
                    _pf["account_number_group"].astype(str),
                    _pf["fiscal_year"].astype(int),
                )
            )
        else:
            gl_keys = set()
        ang_values_pf = canonical["account_number_group"].dropna().unique().tolist()
        fy_values_pf = canonical["fiscal_year"].dropna().unique().tolist()
        if gl_keys and ang_values_pf and fy_values_pf:
            mapped_rows = session.execute(
                text(
                    "SELECT account_number_group, fiscal_year FROM dim_gl_account "
                    "WHERE account_number_group = ANY(:angs) AND fiscal_year = ANY(:fys)"
                ),
                {"angs": ang_values_pf, "fys": [int(y) for y in fy_values_pf]},
            ).fetchall()
            mapped = {(str(r[0]), int(r[1])) for r in mapped_rows}
            unmapped = sorted(gl_keys - mapped)
            if unmapped:
                _has_line_note = "line_note" in canonical.columns
                # DEFECT B: surface the ORIGINAL source account number (e.g. '11701'),
                # not the zero-padded account_number_group suffix (ang[2:] -> '011701').
                # gl_account_id is always present in an apply_profile frame (the M1
                # panel reads it), so build a per-ang lookup of the raw account once.
                # Vectorized first-non-empty gl_account_id per ang (canonical can be
                # up to MAX_PARSE_ROWS, so avoid a per-row Python loop).
                _orig_acct_by_ang: dict[str, str] = {}
                if "gl_account_id" in canonical.columns:
                    _lk = canonical[["account_number_group", "gl_account_id"]].copy()
                    _lk["account_number_group"] = _lk["account_number_group"].astype(str)
                    _gid = _lk["gl_account_id"].astype(str).str.strip()
                    _lk = _lk[_gid.notna() & (_gid.str.lower() != "nan") & (_gid != "")]
                    _orig_acct_by_ang = (
                        _lk.drop_duplicates(subset=["account_number_group"])
                        .set_index("account_number_group")["gl_account_id"]
                        .astype(str).str.strip().to_dict()
                    )
                _rows_for_sort: list[tuple] = []
                for ang, fy in unmapped:
                    entity_prefix = ang[:2]
                    # Prefer the raw gl_account_id; fall back to stripping the 2-char
                    # prefix AND leading zero-pad (safe for numeric German GL accounts).
                    # Guard the all-zeros edge so at least one digit remains.
                    account = _orig_acct_by_ang.get(ang)
                    if not account:
                        _stripped = ang[2:].lstrip("0") if len(ang) > 2 else ang
                        account = _stripped or "0"
                    item: dict[str, Any] = {
                        "account_number_group": ang,
                        "account": account,
                        "entity_prefix": entity_prefix,
                        "fiscal_year": int(fy),
                    }
                    if _has_line_note:
                        _mask = (
                            (canonical["account_number_group"].astype(str) == ang)
                            & (canonical["fiscal_year"].astype(int) == int(fy))
                        )
                        _notes = canonical.loc[_mask, "line_note"].dropna()
                        if not _notes.empty:
                            item["line_note"] = str(_notes.iloc[0])
                    _rows_for_sort.append((entity_prefix, account, int(fy), item))
                _rows_for_sort.sort(key=lambda t: (t[0], t[1], t[2]))
                total_unmapped = len(_rows_for_sort)
                emitted = [t[3] for t in _rows_for_sort[:50]]
                truncated = total_unmapped > len(emitted)
                # Privacy: log ONLY counts. NEVER log the account list or line_note
                # (booking text may carry PII) — mirrors the no-str(exc) policy below.
                logger.warning(
                    "commit: %d unmapped account/year combos", total_unmapped
                )
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "gl_accounts_unmapped",
                        "message": (
                            f"{total_unmapped} account(s) in this GL file aren't "
                            "mapped yet. Apply the configured mapping library to "
                            "classify them, or add them in the account mapping step, "
                            "then retry."
                        ),
                        "unmapped": emitted,
                        "total_unmapped": total_unmapped,
                        "truncated": truncated,
                    },
                )
    except HTTPException:
        raise
    except Exception as exc:
        # Privacy: log the exception TYPE only (str(exc) may embed account data).
        logger.warning("commit: FK pre-flight check failed (%s); proceeding", type(exc).__name__)

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

    # Idempotency short-circuit (REPLACE only): if this exact rectangle is already
    # owned by a prior completed replace load whose stored content_hash equals the hash
    # of the SAME frame load_canonical would persist (enriched_lines), the delete +
    # re-insert is a provable no-op. Skip it and return the live-owning load_id.
    # A FALSE skip must be impossible: any changed cell/scope/exclusion/remapped dim, or
    # any intervening overlapping/append/partial/first-ever load, makes replace_skip_ok
    # return None and we take the full replace path below (unchanged behaviour).
    #   - derive_gl_scope reads only journal_entry_group_number / account_number_group /
    #     fiscal_year, which are identical across canonical, enriched_lines and the
    #     link_partners() frame load_canonical derives its DELETE scope from -> skip
    #     scope == delete scope.
    #   - load_canonical persists content_hash_with_strategy(lines_df, linking_strategy)
    #     where lines_df == enriched_lines and linking_strategy == strategy
    #     -> skip_hash == the exact stored (frame, strategy) hash. A different strategy
    #     changes the hash -> replace_skip_ok returns None -> full replace (no false skip).
    if commit_mode == "replace":
        from etl.versioning import derive_gl_scope, replace_skip_ok

        scope_pfx, scope_fys = derive_gl_scope(canonical)
        # Salt the skip hash with linking_strategy — IDENTICAL to the store side in
        # load_canonical (content_hash_with_strategy(lines_df, linking_strategy) where
        # lines_df == enriched_lines and linking_strategy == strategy). A strategy
        # change alters the derived facts but not content_hash(enriched_lines), so
        # without the salt a same-file re-commit under a new strategy would false-skip.
        skip_hash = content_hash_with_strategy(enriched_lines, strategy)
        if scope_pfx and scope_fys:
            owner_load_id = replace_skip_ok(
                session, body.dataset, scope_pfx, scope_fys, skip_hash
            )
            if owner_load_id is not None:
                logger.info(
                    "commit: REPLACE no-op — identical data already loaded "
                    "(owner load_id=%s); skipping delete/re-insert", owner_load_id,
                )
                return CommitResponse(
                    load_id=owner_load_id,
                    entries=0, lines=0, ar=0, ap=0, sales=0, com=0, skipped=0,
                    loaded_at=datetime.now(timezone.utc).isoformat(),
                    commit_mode="replace",
                    unchanged=True,
                )

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
        # Privacy: do NOT log str(exc). For a Postgres constraint violation the
        # message embeds psycopg2's "DETAIL: Failing row contains (...)" with real
        # fact_gl_line values (amount, account_number_group, partner ids). Log only
        # the exception class and DB error code (docs/security.md). The NOT NULL
        # detection below still inspects a local lowercased copy in-memory.
        logger.error(
            "load_canonical failed: %s (pgcode=%s)",
            type(exc).__name__,
            getattr(getattr(exc, "orig", None), "pgcode", None),
        )
        _exc_text = str(exc).lower()
        # Safety net: a NOT NULL violation on account_number_group means rows with
        # no account number slipped through (e.g. date-compressed summary rows).
        # Surface the same friendly guidance instead of a raw DB error.
        if (
            "account_number_group" in _exc_text
            and ("notnullviolation" in _exc_text
                 or "not-null" in _exc_text
                 or "null value in column" in _exc_text)
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Some rows have no account number (for example "
                    "date-compressed summary rows) and can't be imported. "
                    "Exclude these rows in the validation step (S1 — Required "
                    "fields), or remove them from the source file, then retry."
                ),
            ) from exc
        raise HTTPException(
            status_code=422,
            detail=(
                "Something went wrong importing the GL data — please check the "
                "validation step (S1) and try again."
            ),
        ) from exc

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
    *,
    entity_prefix: str | None = None,
    statement: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Load BS/PL Master workbook and build canonical mapping frame.

    When ``entity_prefix`` is supplied (new CoA wizard), it is injected on every row
    instead of resolving an Entity column.  ``statement`` ("bs"/"pl") reads a single
    Master sheet.  Both default to the legacy two-sheet, Entity-resolved behavior.
    """
    if not fiscal_years:
        raise HTTPException(status_code=422, detail="fiscal_years must contain at least one year")
    path = _file_path_from_id(file_id)
    try:
        raw_df = read_bs_pl_master(
            path, entity_prefix=entity_prefix, statement=statement
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        mapping_df, meta = build_mapping_frames(
            raw_df, session, fiscal_years, entity_prefix=entity_prefix
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return raw_df, mapping_df, meta


def _bs_pl_has_entity_column(path) -> bool:
    """True when at least one Master sheet carries an 'Entity' column (legacy layout)."""
    for sheet in (_BS_SHEET, _PL_SHEET):
        try:
            cols = pd.read_excel(path, sheet_name=sheet, header=0, nrows=0).columns
        except Exception:  # noqa: BLE001 - missing sheet is handled by callers
            continue
        if _ENTITY_COL in [str(c).strip() for c in cols]:
            return True
    return False


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
def _friendly_mapping_commit_error(exc: Exception) -> str:
    """Translate a chart-of-accounts mapping failure into plain English.

    Non-programmer users must never see raw Python text (e.g.
    "invalid literal for int() with base 10: ''"). Map known causes to natural
    guidance; fall back to a generic friendly message for anything unexpected.
    Internal column/profile details are NOT leaked.
    """
    msg = str(exc)
    low = msg.lower()
    # Blank / empty fiscal year (int('') ValueError, or an explicit empty value).
    if isinstance(exc, ValueError) and (
        "invalid literal for int()" in low or "fiscal_year" in low
    ):
        return (
            "No fiscal year was set for this chart-of-accounts mapping. "
            "Please select at least one fiscal year and try again."
        )
    # A required source column is missing from the uploaded file.
    if isinstance(exc, KeyError) or "not found in source file" in low or "column" in low:
        return (
            "The file is missing a required column needed for the chart of "
            "accounts. Please check your file and try again."
        )
    return (
        "Something went wrong saving the chart of accounts — please check "
        "your file and try again."
    )


@router.post("/mapping/commit", response_model=MappingCommitResponse)
def mapping_commit(
    body: MappingCommitRequest,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> MappingCommitResponse:
    """Load an account-mapping file into dim_gl_account / dim_gl_na / dim_gl_cf.

    Validates that all required target fields are mapped before any DB write.
    Records a org_meta_dataset_load row with dataset='mapping'.

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
        if not body.fiscal_years:
            raise HTTPException(status_code=422, detail="fiscal_years required for bs_pl_master format")
        path = _file_path_from_id(body.file_id)

        def _validate_ext_prefix(raw: str) -> str:
            """1-2 digit numeric -> zfilled 2-char prefix (else 422)."""
            p = str(raw).strip()
            if not p.isdigit() or len(p) > 2:
                raise HTTPException(
                    status_code=422,
                    detail="entity_prefix must be a 1-2 digit numeric value",
                )
            return p.zfill(2)

        # Resolve the target entity prefixes for an Entity-less Master workbook.
        # entity_prefixes (multi-entity, atomic) wins over the scalar entity_prefix
        # (single-entity back-compat). When neither is supplied, fall back to the
        # legacy Entity-column resolution path (target=[None]).
        target_prefixes: list[str | None]
        if body.entity_prefixes:
            target_prefixes = [_validate_ext_prefix(p) for p in body.entity_prefixes]
        elif body.entity_prefix is not None and str(body.entity_prefix).strip() != "":
            # New CoA wizard: external entity prefix supplied -> single Master sheet
            # allowed (skip the two-sheet gate); validate 1-2 numeric and zfill to 2.
            target_prefixes = [_validate_ext_prefix(body.entity_prefix)]
        else:
            # Legacy path: require both Master sheets AND an Entity column.
            sheets = _get_sheets(path)
            if not is_bs_pl_master_workbook(sheets):
                raise HTTPException(
                    status_code=422,
                    detail=f"File must contain sheets Master_BS and Master_PL; got {sheets}",
                )
            if not _bs_pl_has_entity_column(path):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Upload has no 'Entity' column and no entity_prefix was supplied. "
                        "Provide an entity prefix or include an Entity column."
                    ),
                )
            target_prefixes = [None]

        # Build the canonical mapping frame ONCE PER PREFIX. The raw_df read is
        # identical across prefixes (same file), so reuse the first one for the
        # content hash; concat so EVERY member lands in ONE atomic transaction.
        raw_df = None
        _frames: list[pd.DataFrame] = []
        for _p in target_prefixes:
            _rdf, _mdf, _meta = _bs_pl_mapping_from_file(
                body.file_id,
                session,
                body.fiscal_years,
                entity_prefix=_p,
                statement=body.statement,
            )
            if raw_df is None:
                raw_df = _rdf
            _frames.append(_mdf)
        mapping_df = pd.concat(_frames, ignore_index=True)
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
        # SECURITY (defense-in-depth): fail-closed entity-visibility guard BEFORE any
        # replace-mode DELETE. A restricted user must receive a 403 before orphan
        # account mappings are removed for a prefix they cannot see. Covers the
        # external-entity_prefix write path too (prefix is baked into the
        # account_number_group). The generic branch is guarded below.
        _assert_prefixes_visible(session, _admin, prefixes)

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
        raw_df = _load_file(
            path, body.sheet, dialect,
            header=body.header_row if body.header_row is not None else _HEADER_DEFAULT,
        )

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

        # Proactively catch the common wizard mistake: no fiscal_years supplied
        # and the profile carries a blank fixed fiscal_year. Without this guard
        # apply_account_mapping runs int('') and surfaces a raw Python message.
        if not body.fiscal_years:
            fy_cfg = am_profile.fiscal_year or {}
            if fy_cfg.get("mode") == "fixed" and str(fy_cfg.get("value", "")).strip() == "":
                logger.error(
                    "mapping_commit generic: blank fixed fiscal_year and no body.fiscal_years"
                )
                raise HTTPException(
                    status_code=422,
                    detail=_friendly_mapping_commit_error(
                        ValueError("invalid literal for int() with base 10: ''")
                    ),
                )

        try:
            if body.fiscal_years:
                # CoA wizard sends a placeholder profile fiscal_year (possibly
                # empty) plus an explicit fiscal_years list. Mirror the
                # bs_pl_master path: ignore the profile's fixed value and
                # replicate the mapping across each requested year.
                frames = [
                    apply_account_mapping(raw_df, am_profile, fiscal_year=int(year))
                    for year in body.fiscal_years
                ]
                mapping_df = pd.concat(frames, ignore_index=True)
            else:
                mapping_df = apply_account_mapping(raw_df, am_profile)
        except (KeyError, ValueError) as exc:
            # Log the technical detail server-side only (type + message; no file
            # rows / PII), surface a plain-English message to the user.
            logger.error("mapping_commit generic apply failed: %s: %s", type(exc).__name__, exc)
            raise HTTPException(
                status_code=422, detail=_friendly_mapping_commit_error(exc)
            ) from exc

    # ------------------------------------------------------------------ load into DB
    from etl.versioning import capture_mapping_snapshot, derive_mapping_scope

    prefixes, years = derive_mapping_scope(mapping_df)
    # entity-visibility guard for the generic branch (restricted users). The
    # bs_pl_master branch is already guarded above BEFORE its replace-mode DELETE,
    # so asserting again here would double-fire for that path; scope this call to
    # the generic write path. Mirrors the sibling write endpoints; runs before the
    # committed dim_gl_account write.
    if body.format != "bs_pl_master":
        _assert_prefixes_visible(session, _admin, prefixes)
    mapping_commit_mode = (
        body.replace_mode if body.format == "bs_pl_master" else "replace"
    )
    try:
        counts = load_account_mapping(session, mapping_df, auto_commit=False)
    except Exception as exc:
        session.rollback()
        logger.error("mapping_commit load_account_mapping failed: %s (pgcode=%s)", type(exc).__name__, getattr(getattr(exc, "orig", None), "pgcode", None))
        raise HTTPException(
            status_code=500,
            detail="Mapping load failed; transaction rolled back. Check server logs.",
        ) from exc

    # DEFECT A: register every committed entity prefix in dim_legal_entity. A single
    # shared CoA committed per group member (prefix '01', then '02', …) otherwise
    # leaves the member with dim_gl_account rows but NO dim_legal_entity row, making
    # it invisible downstream. Mirror the GL /commit path (load_legal_entity upsert);
    # idempotent ON CONFLICT so replace/append modes and repeat commits never error.
    # Uncommitted here — persisted by the snapshot block's session.commit() below.
    try:
        _mapping_ss: str | None = None
        if "source_system" in mapping_df.columns:
            _ss_vals = [
                str(v) for v in mapping_df["source_system"].dropna().unique().tolist()
            ]
            if len(_ss_vals) == 1 and _ss_vals[0].strip():
                _mapping_ss = _ss_vals[0]
        upsert_legal_entities_for_prefixes(
            session, prefixes, source_system=_mapping_ss, auto_commit=False
        )
    except Exception as exc:
        logger.warning(
            "mapping_commit: dim_legal_entity upsert failed (%s); continuing",
            type(exc).__name__,
        )

    # ------------------------------------------------------------------ audit + snapshot
    load_id: int | None = None
    try:
        entity_prefix_val = prefixes[0] if prefixes else None
        fy_val = years[0] if years else None
        load_row = session.execute(
            text("""
                INSERT INTO org_meta_dataset_load
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
                        "UPDATE org_meta_dataset_load SET snapshot_captured = TRUE WHERE load_id = :id"
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


@router.post("/mapping/apply-library", response_model=ApplyLibraryResponse)
def mapping_apply_library(
    body: ApplyLibraryRequest,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> ApplyLibraryResponse:
    """No-file recovery: classify specific GL accounts from the configured mapping
    library and upsert them into dim_gl_account so a blocked GL commit can proceed.

    Resolves each requested account against the library by its bare account id (the
    account_number_group with the 2-char entity prefix stripped). Resolvable accounts
    are written via the shared mapping writer (idempotent ON CONFLICT); accounts not
    present in the library are returned in ``unresolved`` and never written.
    """
    library_dict = coa_template.load_mapping_library(body.library)

    # Fail-closed entity-visibility guard BEFORE any write (mirrors mapping_commit).
    prefixes = sorted({k.account_number_group[:2] for k in body.keys})
    _assert_prefixes_visible(session, _admin, prefixes)

    by_id = {str(e["gl_account_id"]): e for e in library_dict.get("entries", [])}

    rows: list[dict] = []
    unresolved: list[ApplyLibraryUnresolved] = []
    for key in body.keys:
        ang = key.account_number_group
        bare = ang[2:] if len(ang) > 2 else ang
        entry = by_id.get(bare)
        if entry is None:
            unresolved.append(
                ApplyLibraryUnresolved(
                    account_number_group=ang, account=bare, fiscal_year=key.fiscal_year
                )
            )
            continue
        rows.append(
            {
                "account_number_group": ang,
                "fiscal_year": int(key.fiscal_year),
                "gl_account_id": bare,
                "account_name": entry.get("account_name"),
                "level_0": entry.get("level_0"),
                "level_1": entry.get("level_1"),
                "level_2": entry.get("level_2"),
                "level_3": entry.get("level_3"),
                "level_4": entry.get("level_4"),
                "l4_sub": entry.get("l4_sub"),
                "level_2_sort": None,
                "level_3_sort": None,
                "is_ic": False,
                "source_system": f"library:{body.library}",
            }
        )

    inserted = 0
    if rows:
        mapping_df = pd.DataFrame(rows)
        try:
            counts = load_account_mapping(session, mapping_df, auto_commit=False)
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.error("apply_library load_account_mapping failed: %s (pgcode=%s)", type(exc).__name__, getattr(getattr(exc, "orig", None), "pgcode", None))
            raise HTTPException(
                status_code=500,
                detail="Applying the mapping library failed; transaction rolled back. Check server logs.",
            ) from exc
        inserted = counts["accounts"]

    return ApplyLibraryResponse(
        inserted=inserted,
        resolved_keys=len(rows),
        unresolved=unresolved,
    )


# --------------------------------------------------------------------------- #
# Phase 5 (decision 2) — auto-extension of the statement structure
# --------------------------------------------------------------------------- #
# Detect CoA positions that classify nowhere in the (correct per-statement)
# structure, then let the Project-Setup wizard place them before commit. See
# app/services/structure_autoextend.py + docs/financial-logic.md "Phase 5".


class StructureUnknownRequest(BaseModel):
    project_id: str = "default"
    fiscal_years: list[int] = Field(default_factory=list)
    entity_prefixes: list[str] = Field(default_factory=list)
    # Optional: restrict to accounts present in the uploaded GL file.
    account_number_groups: list[str] = Field(default_factory=list)


class UnknownPositionAccountOut(BaseModel):
    gl_account_id: str
    account_name: str | None = None
    account_number_group: str


class UnknownPositionOut(BaseModel):
    id: str
    statement: str            # 'PL' | 'BS' | 'UNKNOWN' (suggested from level_0)
    level_1: str | None = None
    level_2: str | None = None
    level_3: str | None = None
    level_4: str | None = None
    account_count: int
    accounts: list[UnknownPositionAccountOut] = Field(default_factory=list)
    suggested_statement: str  # 'PL' | 'BS'
    suggested_parent_line_code: str | None = None
    suggested_after_line_code: str | None = None
    suggested_sort_order: int | None = None


class StructureUnknownResponse(BaseModel):
    positions: list[UnknownPositionOut] = Field(default_factory=list)
    total: int = 0
    counts_by_statement: dict[str, int] = Field(default_factory=dict)
    # False when the split structure tables are absent (legacy DB) → wizard step
    # auto-passes (golden-safety: no structure to extend).
    structure_available: bool = True


class StructurePlacement(BaseModel):
    statement: Literal["PL", "BS", "CF"]
    level_2: str | None = None
    level_3: str | None = None
    level_4: str | None = None
    row_type: str = "mapping"
    balance_title: str | None = None
    line_code: str | None = None
    after_line_code: str | None = None
    gl_account_id: str | None = None
    section: Literal["asset", "credit"] | None = None


class StructureExtendRequest(BaseModel):
    project_id: str = "default"
    placements: list[StructurePlacement] = Field(..., min_length=1)


class StructureExtendResponse(BaseModel):
    inserted: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    table_counts: dict[str, int] = Field(default_factory=dict)


@router.post("/structure/unknown-positions", response_model=StructureUnknownResponse)
def structure_unknown_positions(
    body: StructureUnknownRequest,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> StructureUnknownResponse:
    """Positions in the committed CoA that resolve to NO structure row.

    Read-only.  On a fresh project (no ``dim_gl_account`` rows yet) or a legacy DB
    (no split structure tables) this returns zero positions so the wizard step
    auto-passes — golden-safety."""
    struct_by_stmt = {
        stmt: _autoextend.load_structure_rows(session, stmt)
        for stmt in ("PL", "BS", "CF")
    }
    structure_available = any(struct_by_stmt[s] for s in ("PL", "BS", "CF"))

    accounts = _autoextend.load_coa_accounts(
        session,
        fiscal_years=body.fiscal_years or None,
        entity_prefixes=body.entity_prefixes or None,
        account_number_groups=body.account_number_groups or None,
    )
    positions = _autoextend.detect_unknown_positions(accounts, struct_by_stmt)

    counts: dict[str, int] = {}
    for p in positions:
        counts[p["statement"]] = counts.get(p["statement"], 0) + 1

    return StructureUnknownResponse(
        positions=[UnknownPositionOut(**p) for p in positions],
        total=len(positions),
        counts_by_statement=counts,
        structure_available=structure_available,
    )


@router.post("/structure/extend", response_model=StructureExtendResponse)
def structure_extend(
    body: StructureExtendRequest,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> StructureExtendResponse:
    """Insert user placements as mapping leaves into the correct structure table.

    The target table is chosen by ``statement`` alone (PL→dim_pl_structure,
    BS→dim_bs_structure, CF→dim_cf_structure) so the split invariant holds — a CF
    position can never land in the Income Statement.  Idempotent."""
    placements = [p.model_dump() for p in body.placements]
    try:
        result = _autoextend.extend_structure(session, placements)
    except _autoextend.PlacementError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover — defensive
        session.rollback()
        logger.error("structure_extend failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=500,
            detail="Extending the statement structure failed; transaction rolled back.",
        ) from exc
    session.commit()
    return StructureExtendResponse(**result)


def _replicate_dim_across_entities(
    session: Session,
    user: User,
    keys: list[tuple[str, int]],
    *,
    auto_commit: bool = True,
) -> tuple[int, list[tuple[str, int]], list[tuple[str, int]]]:
    """Shared, UNCAPPED replicate helper used by BOTH the recovery endpoint and the
    GL commit auto-expand.

    For each requested target key ``(account_number_group, fiscal_year)`` it finds an
    EXISTING ``dim_gl_account`` row under ANY OTHER entity prefix sharing the same
    6-char suffix (``substr(account_number_group, 3)``) and the same fiscal_year, then
    clones its hierarchy / NA / CF classification onto the target.

    NO-OVERWRITE: writes go through the shared mapping writer with
    ``on_conflict="nothing"`` so an existing target row is NEVER clobbered and only
    truly-inserted rows are counted (idempotent — a re-run inserts 0). Targets with no
    source row are returned in ``truly_unmapped`` (never written). Visibility is
    enforced INSIDE this helper (fail-closed) so the caller need not pre-check.

    Returns ``(created_count, created_keys, truly_unmapped_keys)`` where the key lists
    are ``(account_number_group, fiscal_year)`` tuples.
    """
    if not keys:
        return (0, [], [])

    # Fail-closed entity-visibility guard BEFORE any write (mirrors mapping_commit).
    target_prefixes = sorted({k[0][:2] for k in keys})
    _assert_prefixes_visible(session, user, target_prefixes)

    # SECURITY: a RESTRICTED user must not siphon a non-visible entity's mapping, so
    # constrain the SOURCE prefixes to those visible (admin -> None -> all prefixes).
    visible_src = _visible_entity_prefixes_or_none(session, user)

    src_sql = (
        "SELECT a.account_number_group, a.gl_account_id, a.account_name, "
        "a.level_0, a.level_1, a.level_2, a.level_3, a.level_4, a.l4_sub, "
        "a.level_2_sort, a.level_3_sort, a.is_ic, a.source_system, "
        "n.l6_na_mapping, n.l7_na_description, "
        "c.l1 AS cf_l1, c.l2 AS cf_l2, c.l3 AS cf_l3, c.l4 AS cf_l4, "
        "c.l5 AS cf_l5, c.cf_mapping AS cf_mapping "
        "FROM dim_gl_account a "
        "LEFT JOIN dim_gl_na n "
        "  ON n.account_number_group = a.account_number_group "
        " AND n.fiscal_year = a.fiscal_year "
        "LEFT JOIN dim_gl_cf c "
        "  ON c.account_number_group = a.account_number_group "
        " AND c.fiscal_year = a.fiscal_year "
        "WHERE substr(a.account_number_group, 3) = :suffix "
        "  AND a.fiscal_year = :fy "
        "  AND left(a.account_number_group, 2) <> :target_prefix "
    )
    if visible_src is not None:
        src_sql += "  AND left(a.account_number_group, 2) = ANY(:vis) "
    src_sql += "ORDER BY a.account_number_group LIMIT 1"

    rows: list[dict] = []
    target_keys: list[tuple[str, int]] = []
    truly_unmapped: list[tuple[str, int]] = []
    for target_ang, fiscal_year in keys:
        target_prefix = target_ang[:2]
        suffix = target_ang[2:]
        fy = int(fiscal_year)
        params: dict[str, Any] = {
            "suffix": suffix,
            "fy": fy,
            "target_prefix": target_prefix,
        }
        if visible_src is not None:
            params["vis"] = sorted(visible_src)
        src = session.execute(text(src_sql), params).fetchone()
        if src is None:
            truly_unmapped.append((target_ang, fy))
            continue
        m = src._mapping
        rows.append(
            {
                "account_number_group": target_ang,
                "fiscal_year": fy,
                # gl_account_id stays BARE (the source's), never re-prefixed.
                "gl_account_id": m["gl_account_id"],
                "account_name": m["account_name"],
                "level_0": m["level_0"],
                "level_1": m["level_1"],
                "level_2": m["level_2"],
                "level_3": m["level_3"],
                "level_4": m["level_4"],
                "l4_sub": m["l4_sub"],
                "level_2_sort": m["level_2_sort"],
                "level_3_sort": m["level_3_sort"],
                "is_ic": bool(m["is_ic"]) if m["is_ic"] is not None else False,
                "source_system": m["source_system"],
                # NA fields carried so the conditional dim_gl_na upsert fires.
                "l6_na_mapping": m["l6_na_mapping"],
                "l7_na_description": m["l7_na_description"],
                # CF fields carried so the conditional dim_gl_cf upsert fires.
                "cf_l1": m["cf_l1"],
                "cf_l2": m["cf_l2"],
                "cf_l3": m["cf_l3"],
                "cf_l4": m["cf_l4"],
                "cf_l5": m["cf_l5"],
                "cf_mapping": m["cf_mapping"],
            }
        )
        target_keys.append((target_ang, fy))

    created = 0
    created_keys: list[tuple[str, int]] = []
    if rows:
        # Which target keys already exist? Those are NO-OVERWRITE skips (DO NOTHING),
        # so they are excluded from created_keys (and never re-counted).
        existing = session.execute(
            text(
                "SELECT account_number_group, fiscal_year FROM dim_gl_account "
                "WHERE account_number_group = ANY(:angs) AND fiscal_year = ANY(:fys)"
            ),
            {
                "angs": [k[0] for k in target_keys],
                "fys": sorted({k[1] for k in target_keys}),
            },
        ).fetchall()
        preexisting = {(str(r[0]), int(r[1])) for r in existing}
        created_keys = [(a, f) for (a, f) in target_keys if (a, f) not in preexisting]
        created_prefixes = sorted(
            {a[:2] for (a, f) in target_keys if (a, f) not in preexisting}
        )

        mapping_df = pd.DataFrame(rows)
        try:
            counts = load_account_mapping(
                session, mapping_df, auto_commit=False, on_conflict="nothing"
            )
            if created_prefixes:
                upsert_legal_entities_for_prefixes(
                    session, created_prefixes, auto_commit=False
                )
            if auto_commit:
                session.commit()
        except Exception as exc:
            session.rollback()
            # Privacy: never surface str(exc); log type + DB error code only.
            logger.error(
                "replicate_dim_across_entities failed: %s (pgcode=%s)",
                type(exc).__name__,
                getattr(getattr(exc, "orig", None), "pgcode", None),
            )
            raise HTTPException(
                status_code=500,
                detail="Replicating the mapping failed; transaction rolled back. Check server logs.",
            ) from exc
        created = counts["accounts"]

    return (created, created_keys, truly_unmapped)


@router.post(
    "/mapping/replicate-across-entities",
    response_model=ReplicateAcrossEntitiesResponse,
)
def mapping_replicate_across_entities(
    body: ReplicateAcrossEntitiesRequest,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> ReplicateAcrossEntitiesResponse:
    """No-file recovery: clone an EXISTING entity's mapping onto group members that
    share the same canonical 6-char account suffix but have no dim_gl_account row.

    A shared chart of accounts committed for one member (prefix '01') leaves a
    sibling (prefix '02') unmapped until its own CoA is committed.  This endpoint
    finds, for each requested target key, a source row under ANY OTHER prefix with
    the same suffix and replicates its hierarchy / NA classification onto the target.

    NO-OVERWRITE: writes go through the shared mapping writer with
    ``on_conflict="nothing"``, so an existing target row is NEVER clobbered and only
    truly-inserted rows are counted.  Targets with no source row are returned in
    ``truly_unmapped`` (never written).  Privacy: a failure returns a generic 500 —
    the underlying exception text is never surfaced.
    """
    # Delegate to the shared, UNCAPPED helper (the single source of truth for the
    # suffix-based clone — it enforces visibility, copies account + NA + CF, and
    # writes via the shared mapping writer with on_conflict="nothing").
    keys = [(k.account_number_group, int(k.fiscal_year)) for k in body.keys]
    created, created_keys, truly_unmapped = _replicate_dim_across_entities(
        session, _admin, keys, auto_commit=True
    )
    return ReplicateAcrossEntitiesResponse(
        created=created,
        created_keys=[
            ReplicateKey(account_number_group=a, fiscal_year=f)
            for (a, f) in created_keys
        ],
        # SAME display rule as the unmapped-422: bare account, leading zeros
        # stripped, with an all-zeros guard so at least one digit remains.
        truly_unmapped=[
            ReplicateUnmapped(
                account_number_group=a,
                account=(a[2:].lstrip("0") or "0"),
                fiscal_year=f,
            )
            for (a, f) in truly_unmapped
        ],
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
    # Non-blocking: entity labels present in the uploaded file that are not part
    # of the project and were dropped from the opening-balance load.
    ignored_entities: list[str] = Field(default_factory=list)


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


def _is_fk_violation(exc: BaseException | None) -> bool:
    """True if ``exc`` (or any wrapped cause) is a Postgres foreign-key violation.

    SQLAlchemy wraps the driver error, so walk ``.orig`` / ``.__cause__`` and match
    either the psycopg2 error class name or SQLSTATE 23503 (foreign_key_violation).
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if getattr(cur, "pgcode", None) == "23503":
            return True
        if type(cur).__name__ == "ForeignKeyViolation":
            return True
        cur = getattr(cur, "orig", None) or cur.__cause__
    return False


def _pg_sqlstate(exc: BaseException | None) -> str | None:
    """Return the Postgres SQLSTATE (``pgcode``) from a wrapped DB error, if any.

    SQLAlchemy wraps the psycopg2 driver error, so walk ``.orig`` / ``.__cause__``
    and return the first ``pgcode`` found (e.g. '23505' unique_violation,
    '23502' not_null_violation, '22003' numeric_value_out_of_range). Returns
    ``None`` when the exception is not a Postgres driver error. Used to map known
    DB failure classes to friendly 422s without leaking raw error text.
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        code = getattr(cur, "pgcode", None)
        if code:
            return str(code)
        cur = getattr(cur, "orig", None) or cur.__cause__
    return None


def _pg_constraint(exc: BaseException | None) -> str | None:
    """Return the violated Postgres constraint name from a wrapped DB error, if any.

    Walks ``.orig`` / ``.__cause__`` and reads psycopg2's ``diag.constraint_name``
    (e.g. ``fact_gl_line_booking_line_id_key`` for the synthetic-id unique key).
    Used ONLY to branch to a friendlier message — the name is never returned to
    the client. Returns ``None`` when unavailable.
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        diag = getattr(cur, "diag", None)
        name = getattr(diag, "constraint_name", None) if diag is not None else None
        if name:
            return str(name)
        cur = getattr(cur, "orig", None) or cur.__cause__
    return None


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


# ``fact_gl_line.amount`` is ``NUMERIC(18,6)``: 18 total / 6 fractional digits leaves
# 12 integer digits, so Postgres rejects any ``|amount| >= 1e12`` with a raw
# "numeric field overflow" that would otherwise surface as a generic 500. A parsed OB
# amount this large is almost never real — it is the signature of a decimal/thousands
# mis-parse (e.g. a US ``52803.84`` read with a German profile that strips ``.`` as a
# thousands separator -> ~5.3e16). The largest amount in the real reference OB file is
# ~5.29e7, so a 1e12 bound sits >4 orders of magnitude above any legitimate figure and
# rejects nothing real while turning a future mis-parse into a helpful 422.
_OB_AMOUNT_ABS_LIMIT = 1e12


def _assert_amount_magnitude(canonical: pd.DataFrame) -> None:
    """Fail LOUDLY (422) on an OB amount too large for ``fact_gl_line.amount``.

    Defensive guard against a silent decimal/thousands mis-parse: rather than let a
    mis-scaled value hit Postgres and surface as a raw ``numeric field overflow`` 500,
    reject it here with a humanized message pointing at the file's number format. The
    happy path (any in-range amount) is untouched. Assumes non-finite amounts were
    already rejected by ``_assert_finite_amounts`` (call this immediately after).
    """
    if "amount" not in canonical.columns:
        return
    amt = pd.to_numeric(canonical["amount"], errors="coerce")
    over = amt.abs() >= _OB_AMOUNT_ABS_LIMIT
    if bool(over.any()):
        examples = [f"{v:.2f}" for v in amt[over].dropna().abs().head(3).tolist()]
        raise HTTPException(
            status_code=422,
            detail=(
                "An opening-balance amount looks mis-scaled and is too large to store "
                "(check the file's number format / decimal separator). "
                f"Example value(s): {', '.join(examples)}."
            ),
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
    if ext not in OB_PARTNER_ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type {ext!r}. Allowed: {', '.join(OB_PARTNER_ALLOWED_EXTENSIONS)}",
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
            body.file_id, body.sheet, body.profile, session, opening_balance=True
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("opening_balance_commit parse failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to parse opening-balance file.") from exc

    # Best-effort: surface entities that were in the file but not part of the
    # project (dropped during mapping). Non-blocking — informational only.
    try:
        ignored_entities = list(canonical.attrs.get("ignored_entity_labels") or [])
    except Exception:  # noqa: BLE001 — attrs access must never break the commit
        ignored_entities = []
    if ignored_entities:
        logger.info(
            "opening_balance_commit: ignored %d entity label(s) not in the project: %s",
            len(ignored_entities),
            ", ".join(ignored_entities),
        )

    if canonical.empty:
        raise HTTPException(status_code=422, detail="Opening-balance file has no rows.")

    # H1: bound the parsed frame BEFORE any transform/write (zip/dimension bomb).
    _assert_parse_bounds(canonical, what="opening-balance file")
    _assert_finite_amounts(canonical)
    # Defensive magnitude bound: a mis-parsed (mis-scaled) amount must fail with a
    # humanized 422, not a raw NUMERIC(18,6) overflow 500 from Postgres.
    _assert_amount_magnitude(canonical)

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
                "Some opening-balance rows have no posting date and no fiscal year "
                "to place them on (the opening stock is dated Jan 1 of its fiscal "
                "year). Select a fiscal year for the project or map a fiscal-year "
                "column, then retry."
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
    # NOTE: the jegn already encodes the 2-digit entity prefix '<EE>' (see _ob_jegn).
    # We MUST hash the FULL jegn — not the decimal value of '{jegn}{fy}' truncated by
    # '% 10^12'. That truncation dropped the high-order entity prefix, so the same
    # account number in two different entities of the same group (which share one
    # chart of accounts) collapsed to identical ids and violated the booking_line_id
    # unique constraint. A BLAKE2b digest of the full 'jegn|fy' string (same approach
    # as the GL side) keeps ids deterministic and unique per (entity, account, year).
    # Modulo 10^11 keeps every id inside the reserved file-OB band [800e9, 900e9),
    # strictly below the carry-forward OB base (900e9); collision odds for realistic
    # volumes (~5 entities x ~1000 accounts x ~4 years) are negligible (~n^2/2N ~ 0.002).
    def _ob_bid(jegn: str, fy: int) -> int:
        h = hashlib.blake2b(f"{jegn}|{int(fy)}".encode(), digest_size=8).digest()
        return _OB_BID_BASE + (int.from_bytes(h, "big") % 100_000_000_000)

    canonical["booking_line_id"] = [
        _ob_bid(j, fy)
        for j, fy in zip(
            canonical["journal_entry_group_number"],
            canonical["fiscal_year"].astype(int),
        )
    ]

    # ---- pre-flight: ensure a dim_gl_account row for every OB (account, year) ----
    # fact_gl_line has an FK (account_number_group, fiscal_year) -> dim_gl_account, so
    # an OB row for an account whose chart-of-accounts row is missing in THIS year
    # would fail the FK on insert.  Additively synthesize the missing dim rows on the
    # SAME session (NO commit — part of the OB transaction, so a later load failure
    # still rolls back atomically) by cloning the account's classification from another
    # year / the mapping library.  Accounts that cannot be classified are reported.
    from etl.account_fill import fill_account_rows_for_keys

    required_keys = sorted(
        {
            (str(a), int(fy))
            for a, fy in zip(
                canonical["account_number_group"].astype(str),
                canonical["fiscal_year"].astype(int),
            )
            if str(a).strip()
        }
    )
    fill_summary = fill_account_rows_for_keys(
        session, required_keys, source_system="opening_balance_account_fill"
    )
    unresolved = sorted(
        list(fill_summary["unresolved_no_name"])
        + list(fill_summary["unresolved_no_resolution"])
    )
    if unresolved:
        # Shared-group-chart fallback: a group member sharing ONE chart of accounts
        # (one SKR) may carry an OB for a balance-sheet account that has NO GL
        # movement in THIS entity — so it has no dim_gl_account row here and no
        # library precedent (unresolved above) — yet the SAME bare account IS
        # classified under a sibling entity's prefix (the classification is
        # entity-invariant).  Clone ONLY the chart CLASSIFICATION (level_0..4 /
        # l4_sub / sort / is_ic + NA/CF) from a sibling that shares the 6-char
        # account suffix; NO opening-balance VALUE is ever sourced from another
        # entity (the OB amount comes solely from THIS entity's own OB row).
        # Runs on the SAME session with auto_commit=False so it stays part of this
        # atomic OB transaction (a later load failure still rolls back all inserts).
        # Visibility is enforced fail-closed inside the helper, and the write is
        # additive/idempotent (on_conflict='nothing').  Only accounts absent from
        # EVERY entity remain in truly_unmapped and fall through to the 422 below.
        _clone_created, _clone_keys, truly_unmapped = _replicate_dim_across_entities(
            session, _admin, unresolved, auto_commit=False
        )
        unresolved = sorted(truly_unmapped)
    if unresolved:
        # Nothing should be written when we reject: discard the partial (resolved)
        # fill + clone inserts so a retry starts clean (idempotency contract intact).
        session.rollback()
        _pfx = sorted({str(ang)[:2] for ang, _fy in unresolved})
        name_by_prefix: dict[str, str] = {}
        try:
            _nrows = session.execute(
                text(
                    "SELECT DISTINCT entity_prefix, entity_name FROM dim_legal_entity "
                    "WHERE entity_prefix = ANY(:pfx)"
                ),
                {"pfx": _pfx or [""]},
            ).fetchall()
            for _r in _nrows:
                if _r[0] is not None and _r[1]:
                    name_by_prefix.setdefault(str(_r[0]).strip()[:2], str(_r[1]).strip())
        except Exception as _exc:  # noqa: BLE001 — naming is best-effort
            session.rollback()
            logger.warning("opening_balance_commit: entity-name lookup failed (%s)", _exc)
            name_by_prefix = {}

        def _fmt_offender(ang: str, fy: int) -> str:
            pfx = str(ang)[:2]
            bare = str(ang)[2:].lstrip("0") or str(ang)[2:] or str(ang)
            nm = name_by_prefix.get(pfx)
            ent = f"entity {pfx}" + (f" ({nm})" if nm else "")
            return f"{ent} account {bare} (FY{fy})"

        items = [_fmt_offender(a, fy) for a, fy in unresolved]
        shown = items[:30]
        listing = ", ".join(shown)
        if len(items) > len(shown):
            listing += f", …and {len(items) - len(shown)} more"
        raise HTTPException(
            status_code=422,
            detail=(
                "These opening-balance accounts aren't in the chart of accounts and "
                "couldn't be classified from other years or the group chart: "
                + listing + ". Add them in "
                "the Chart of Accounts step or remove those rows from the opening-balance "
                "file, then retry."
            ),
        )

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
                INSERT INTO org_meta_dataset_load
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
        # Privacy: never log str(exc) — a Postgres constraint error embeds psycopg2's
        # "DETAIL: Failing row contains (...)" with real OB values (amount, account
        # number, entity). Log only the exception class + SQLSTATE (docs/security.md).
        sqlstate = _pg_sqlstate(exc)
        logger.error(
            "opening_balance_commit load failed: %s (sqlstate=%s)",
            type(exc).__name__,
            sqlstate,
        )
        # Map known DB failure classes to specific, actionable 422s so the frontend
        # humanizer surfaces them unchanged (no "transaction rolled back"/"Load failed"/
        # exception-name text, which it collapses to a generic message). No raw SQL or
        # exception text ever reaches the client.

        # 23505 unique_violation — the synthetic booking_line_id key. Post-BLAKE2b this
        # should not recur; if it does, stale backend code is the likely cause.
        if sqlstate == "23505" or _pg_constraint(exc) == "fact_gl_line_booking_line_id_key":
            raise HTTPException(
                status_code=422,
                detail=(
                    "Some opening-balance rows produced duplicate internal ids (this "
                    "indicates the same account appears under multiple entities). This "
                    "should already be fixed; if you still see it, the backend may be "
                    "running old code — restart the backend and retry."
                ),
            ) from exc

        # 22003 numeric_value_out_of_range — a mis-scaled amount slipped past the
        # magnitude guard (mirrors _assert_amount_magnitude's wording).
        if sqlstate == "22003":
            raise HTTPException(
                status_code=422,
                detail=(
                    "An opening-balance amount looks mis-scaled and is too large to "
                    "store (check the file's number format / decimal separator), then "
                    "retry."
                ),
            ) from exc

        # 23502 not_null_violation — the OB row has no account number (reuse the same
        # "have no account number" wording the GL path uses; the frontend preserves it).
        if sqlstate == "23502":
            raise HTTPException(
                status_code=422,
                detail=(
                    "Some opening-balance rows have no account number and can't be "
                    "imported. Remove those rows from the opening-balance file (or fix "
                    "them), then retry."
                ),
            ) from exc

        # 23503 foreign_key_violation — honest fallback after the pre-flight fill: an
        # account still isn't in the chart of accounts for its fiscal year.
        if _is_fk_violation(exc):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Some opening-balance accounts aren't in the chart of accounts for "
                    "their fiscal year, so they couldn't be loaded. Add the missing "
                    "accounts in the Chart of Accounts step (or remove those rows from "
                    "the opening-balance file), then retry."
                ),
            ) from exc

        # Unknown/other DB error — OB-specific (non-generic) 422 that still points to
        # the right step. Deliberately avoids the humanizer's generic-collapsing tokens.
        raise HTTPException(
            status_code=422,
            detail=(
                "The opening balances couldn't be saved due to an unexpected database "
                "error. Open the validation step to review the opening-balance file, "
                "then retry; if this keeps happening the backend logs have the details."
            ),
        ) from exc

    return OpeningBalanceCommitResponse(
        load_id=load_id,
        entries=len(entries),
        lines=len(line_rows),
        fiscal_years=keep_years,
        scope=body.scope,
        loaded_at=loaded_at_dt.isoformat(),
        ignored_entities=ignored_entities,
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
    FROM org_meta_dataset_load
"""

_VERSIONS_SELECT_LEGACY = """
    SELECT load_id, dataset, legal_entity_code, fiscal_year, row_count,
           content_hash, loaded_at, loaded_by
    FROM org_meta_dataset_load
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
        logger.warning("org_meta_dataset_load versioning columns missing — using legacy query: %s", exc)
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
                INSERT INTO org_meta_dataset_load
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
