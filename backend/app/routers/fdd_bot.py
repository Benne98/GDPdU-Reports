"""
FDD Bot FastAPI router.

Endpoints:
  POST /api/v1/fdd/upload             — receive XLSX, save to disk, return file_id + headers
  GET  /api/v1/fdd/headers/{file_id}  — extract column headers from an uploaded XLSX
  GET  /api/v1/fdd/folders            — list immediate subdirectories at a given path
  POST /api/v1/fdd/run/gst            — execute general_sales_table_MM_verformelt.py (sync)
  POST /api/v1/fdd/run/gst/async      — start GST in background; poll GET /run/status
  POST /api/v1/fdd/run/pvm            — execute pvm_verformelt.py
  POST /api/v1/fdd/run/pvm/async      — start PVM in background
  POST /api/v1/fdd/run/top            — execute top-report script
  POST /api/v1/fdd/run/top/async      — start TOP in background
  POST /api/v1/fdd/run/bubble/async   — start bubble scatter in background
  GET  /api/v1/fdd/run/status         — poll async job status (running | done | failed)
  POST /api/v1/fdd/run/databook       — orchestrate Databook scripts
  POST /api/v1/fdd/run/databook/susa  — execute SuSabyYear.py
  GET  /api/v1/fdd/templates/{name}   — return a template XLSX for download
"""

import json
import logging
import os
import re
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

router = APIRouter(prefix="/api/v1/fdd", tags=["fdd-bot"])

# ─── Paths ────────────────────────────────────────────────────────────────────

# Repo root: backend/app/routers/fdd_bot.py → parents[3]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = PROJECT_ROOT / "backend"

# SuSabyYear + susa_column_mapping live at repo root (same as script cwd).
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Uploaded files live here: uploads/{session_id}/{file_id}_{filename}
UPLOAD_DIR = PROJECT_ROOT / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# Python interpreter used to run analytical scripts (see _script_python())

# Script paths relative to PROJECT_ROOT
SCRIPTS: dict[str, Path] = {
    "gst": PROJECT_ROOT / "general_sales_table_MM_verformelt.py",
    "pvm": PROJECT_ROOT / "pvm_verformelt.py",
    "top": PROJECT_ROOT / "top_report.py",
    "churn": PROJECT_ROOT / "churn_verformelt.py",
    "bubble": PROJECT_ROOT / "bubblescatterplot.py",
    "susa": PROJECT_ROOT / "SuSabyYear.py",
    "consolidation": PROJECT_ROOT / "Consolidation.py",
    "consolidation_template": PROJECT_ROOT / "consolidation_template.py",
    "adjustments": PROJECT_ROOT / "Adjustments.py",
    "adjustments_template": PROJECT_ROOT / "adjustments_template.py",
    "pdf_recon": PROJECT_ROOT / "PDF_Extraction_recon.py",
    "pdf_adjusted": PROJECT_ROOT / "PDF_Extraction_adjustments.py",
    "pdf_checked": PROJECT_ROOT / "PDF_Extraction_checked.py",
    "lead_is": PROJECT_ROOT / "Lead_IS.py",
    "recon_pl": PROJECT_ROOT / "Recon_tables_PL_MM.py",
    "recon_bs": PROJECT_ROOT / "Recon_tables_BS.py",
    "bs_bucket": PROJECT_ROOT / "BS_Bucket.py",
    "lead_bs": PROJECT_ROOT / "Lead_BS.py",
    "working_capital": PROJECT_ROOT / "Working_capital.py",
    "cashflow": PROJECT_ROOT / "cashflow.py",
    "fixed_assets_rollf": PROJECT_ROOT / "fixed_assets_rollf.py",
    "opos": PROJECT_ROOT / "opos.py",
    "fte_payroll": PROJECT_ROOT / "FTE_payroll.py",
}


def _workbook_matrix_preview(
    session_id: str,
    file_id: str,
    sheet_name: str = "",
    sheet_index: int = 0,
    header_row: int = 0,
    max_rows: int = 12,
) -> dict:
    """Matrix preview for column mappers (header + letter columns + sample rows)."""
    if not (file_id or "").strip():
        raise HTTPException(status_code=400, detail="file_id is required for preview.")

    path = _file_path_for_id(session_id, file_id)
    if not path or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found for session_id / file_id.")

    try:
        from openpyxl.utils import get_column_letter

        if sheet_name:
            raw = pd.read_excel(
                path, sheet_name=sheet_name, header=None, engine="openpyxl", nrows=header_row + max_rows + 1
            )
        else:
            raw = pd.read_excel(
                path, sheet_name=sheet_index, header=None, engine="openpyxl", nrows=header_row + max_rows + 1
            )
    except Exception as exc:
        logger.exception("workbook matrix preview failed")
        raise HTTPException(status_code=400, detail=f"Could not read workbook: {exc}") from exc

    if raw.empty or header_row >= len(raw):
        raise HTTPException(status_code=400, detail="header_row out of range")

    headers = [
        str(v).strip() if v is not None and not (isinstance(v, float) and pd.isna(v)) else ""
        for v in raw.iloc[header_row]
    ]
    columns = []
    for i, h in enumerate(headers):
        letter = get_column_letter(i + 1)
        samples = []
        for r in range(header_row + 1, min(len(raw), header_row + 1 + 5)):
            val = raw.iat[r, i]
            if val is None or (isinstance(val, float) and pd.isna(val)):
                samples.append("")
            else:
                samples.append(str(val))
        columns.append({"letter": letter, "header": h, "sample_values": samples})

    body_rows = []
    for r in range(header_row + 1, min(len(raw), header_row + max_rows)):
        row = []
        for c in range(len(headers)):
            val = raw.iat[r, c]
            if val is None or (isinstance(val, float) and pd.isna(val)):
                row.append("")
            else:
                row.append(str(val))
        body_rows.append(row)

    return {
        "file_id": file_id,
        "sheet_index": sheet_index,
        "sheet_name": sheet_name or None,
        "header_row_index": header_row,
        "columns": columns,
        "rows": body_rows,
    }


def _normalize_fa_rollf_config(config: dict, session_id: str) -> dict:
    """Resolve period file_ids to paths and infer sheet names."""
    cfg = dict(config)
    raw_periods = cfg.get("periods")
    if not isinstance(raw_periods, list) or not raw_periods:
        raise ValueError("Fixed assets rollforward config requires periods[]")

    from fixed_assets_rollf import _resolve_sheet_name

    normalized: list[dict] = []
    for item in raw_periods:
        if not isinstance(item, dict):
            continue
        period = dict(item)
        label = str(period.get("label") or "").strip()
        if not label:
            continue
        fid = str(period.get("file_id") or "").strip()
        fp = str(period.get("file_path") or "").strip()
        if not fp and fid:
            resolved = _file_path_for_id(session_id, fid)
            if resolved:
                fp = str(resolved)
        if not fp:
            raise ValueError(f"Fixed assets period '{label}' file not found for session {session_id}")
        period["file_path"] = fp
        sheet = str(period.get("sheet_name") or "").strip()
        period["sheet_name"] = _resolve_sheet_name(fp, sheet)
        normalized.append(period)

    if not normalized:
        raise ValueError("Fixed assets rollforward config has no valid periods")
    cfg["periods"] = normalized
    cfg["file_path"] = normalized[0]["file_path"]
    from databook_workbook import FA_ROLLF_OUTPUT_SHEET

    cfg["sheet_name"] = FA_ROLLF_OUTPUT_SHEET
    return cfg

# ─── Helpers ──────────────────────────────────────────────────────────────────


SCRIPT_RUN_TIMEOUT_SEC = 900
SCRIPT_ASYNC_TIMEOUT_SEC = 7200


def _resolve_output_folder(raw: str | None, session_id: str) -> str:
    """Normalize output folder from bot slot/config to an absolute writable path."""
    if not raw or not str(raw).strip():
        out = _session_dir(session_id) / "output"
        out.mkdir(parents=True, exist_ok=True)
        return str(out.resolve())
    text = str(raw).strip()
    p = Path(text).expanduser()
    if not p.is_absolute():
        # Bare names like "Desktop" must not resolve against the repo cwd.
        if text.lower() in ("desktop", "documents", "downloads"):
            p = Path.home() / text
        else:
            p = (PROJECT_ROOT / p).resolve()
    else:
        p = p.resolve()
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def _script_python() -> str:
    """Prefer backend/.venv for repo scripts (PDF extraction deps live there)."""
    candidates = [
        BACKEND_ROOT / ".venv" / "bin" / "python",
        BACKEND_ROOT / ".venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return sys.executable


PYTHON = _script_python()


def _check_module(python_exe: str, module: str) -> bool:
    try:
        result = subprocess.run(
            [python_exe, "-c", f"import {module}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _script_subprocess_env() -> dict:
    try:
        from dotenv import load_dotenv

        load_dotenv(BACKEND_ROOT / ".env")
    except ImportError:
        pass

    env = os.environ.copy()
    scripts_dir = str(PROJECT_ROOT / "scripts")
    existing_path = env.get("PYTHONPATH", "")
    if scripts_dir not in existing_path.split(os.pathsep):
        env["PYTHONPATH"] = (
            f"{scripts_dir}{os.pathsep}{existing_path}" if existing_path else scripts_dir
        )
    return env


def _run_status_path(session_id: str, run_id: str) -> Path:
    return _run_dir(session_id, run_id) / "status.json"


def _write_run_status(session_id: str, run_id: str, data: dict) -> None:
    path = _run_status_path(session_id, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_run_status(session_id: str, run_id: str) -> dict | None:
    path = _run_status_path(session_id, run_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _resolve_output_files(session_id: str, config: dict, output_folder: str) -> tuple[str | None, str | None]:
    explicit_out = str(config.get("output_path") or "").strip()
    if explicit_out and os.path.isfile(explicit_out):
        return explicit_out, os.path.basename(explicit_out)

    from funktionssammlung import find_session_output_file

    cfg = dict(config)
    cfg.setdefault("case_id", session_id)
    cfg["output_file_path"] = output_folder
    resolved = find_session_output_file(cfg)
    if resolved:
        return resolved, os.path.basename(resolved)
    return None, None


def _run_script(script: Path, config_path: Path, timeout: int = SCRIPT_RUN_TIMEOUT_SEC) -> dict:
    """Run a Python analytical script with a JSON config path as first argument."""
    if not script.exists():
        return {"success": False, "message": f"Script not found: {script}"}

    env = _script_subprocess_env()
    script_python = _script_python()

    if script.name.startswith("PDF_Extraction") and not _check_module(script_python, "anthropic"):
        msg = (
            f"Missing Python package 'anthropic' for {script_python}. "
            f"Install with: {script_python} -m pip install -r scripts/requirements_pdf_extraction.txt"
        )
        return {"success": False, "message": msg}

    try:
        result = subprocess.run(
            [script_python, str(script), str(config_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(PROJECT_ROOT),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "message": f"Script timed out after {timeout}s ({script.name}). "
            "Try fewer periods/metrics or a smaller file.",
            "timed_out": True,
        }
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        message = _clean_script_error_message(stderr, stdout)
        return {
            "success": False,
            "message": message,
        }
    return {"success": True, "message": result.stdout.strip()}


def _clean_script_error_message(stderr: str, stdout: str) -> str:
    """Prefer concise user-facing errors over noisy subprocess output."""
    combined = "\n".join(part for part in (stderr, stdout) if part).strip()
    for line in reversed(combined.splitlines()):
        text = line.strip()
        if not text:
            continue
        if text.startswith("Anthropic API"):
            return text
        if "ValueError:" in text:
            return text.split("ValueError:", 1)[-1].strip()
    # Drop pymupdf progress-bar backspace noise
    cleaned = re.sub(r".\x08", "", combined)
    cleaned = re.sub(r"\x1b\[[0-9;]*m", "", cleaned)
    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    for line in reversed(lines):
        if line.startswith("Traceback"):
            break
        if "Error" in line or "error" in line.lower():
            return line
    return lines[-1] if lines else "Script exited with non-zero code"


def _make_run_id(prefix: str = "") -> str:
    now = datetime.now().strftime("%Y%m%d-%H%M%S")
    short = str(uuid.uuid4())[:8]
    return f"{prefix}{now}_{short}" if prefix else f"{now}_{short}"


def _session_dir(session_id: str) -> Path:
    d = UPLOAD_DIR / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_dir(session_id: str, run_id: str) -> Path:
    d = _session_dir(session_id) / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file_path_for_id(session_id: str, file_id: str) -> Optional[Path]:
    sess = _session_dir(session_id)
    for f in sess.iterdir():
        if f.is_file() and f.stem.startswith(file_id):
            return f
    # Legacy trackers may still pass an old session_id slot while uploads use REST sender_id.
    for sub in UPLOAD_DIR.iterdir():
        if not sub.is_dir() or sub.name == session_id:
            continue
        for f in sub.iterdir():
            if f.is_file() and f.stem.startswith(file_id):
                logger.warning(
                    "fdd: file_id=%s found under session %s (requested %s)",
                    file_id,
                    sub.name,
                    session_id,
                )
                return f
    return None


def _resolve_entity_file_paths(session_id: str, entity_files: list[Any]) -> list[str]:
    """
    Build absolute paths for Databook scripts.
    Accepts plain paths (str) and Rasa /submit_card dicts: {name, file_id, year, ...}.
    """
    out: list[str] = []
    for item in entity_files or []:
        if isinstance(item, str):
            p = Path(item)
            if p.is_file():
                out.append(str(p.resolve()))
                continue
            resolved = _file_path_for_id(session_id, item)
            if resolved:
                out.append(str(resolved))
        elif isinstance(item, dict):
            fids = item.get("file_ids")
            if isinstance(fids, list) and fids:
                for fid in fids:
                    resolved = _file_path_for_id(session_id, str(fid))
                    if resolved:
                        out.append(str(resolved))
                continue
            fid = item.get("file_id")
            if not fid:
                continue
            resolved = _file_path_for_id(session_id, str(fid))
            if resolved:
                out.append(str(resolved))
    return out


class EntityYearFileGroup(BaseModel):
    """One grid cell: one entity × one fiscal year × one or many uploaded workbooks."""

    entity_index: int = 0
    entity_name: str = ""
    fy_label: str = ""
    file_ids: list[str] = Field(default_factory=list)

    @field_validator("entity_index", mode="before")
    @classmethod
    def _coerce_entity_index(cls, v: Any) -> int:
        if v is None or v == "":
            return 0
        return int(float(v))


def _fy_calendar_year_from_label(fy_label: str) -> int:
    m = re.search(r"(19|20)\d{2}", str(fy_label))
    if not m:
        raise ValueError(f"Cannot parse calendar year from fy_label: {fy_label!r}")
    return int(m.group(0))


def _resolve_entity_year_files_for_susa(session_id: str, groups: list[EntityYearFileGroup]) -> list[dict[str, Any]]:
    """Resolve upload file_ids to absolute paths per grid cell."""
    resolved: list[dict[str, Any]] = []
    for g in groups:
        paths: list[str] = []
        for fid in g.file_ids:
            p = _file_path_for_id(session_id, str(fid))
            if p:
                paths.append(str(p.resolve()))
        if not paths:
            continue
        try:
            year = _fy_calendar_year_from_label(g.fy_label)
        except ValueError:
            year = 0
        resolved.append(
            {
                "entity_index": g.entity_index,
                "entity_name": g.entity_name,
                "fy_label": g.fy_label,
                "year": year,
                "paths": paths,
                "file_ids": list(g.file_ids),
            }
        )
    return resolved


def _default_susa_workbook_config() -> dict[str, Any]:
    return {
        "columns": {
            "Account": "A",
            "Account description": "B",
            "Balance": "P",
            "S": "G",
            "H": "H",
            "Month": "C",
        },
        "output_sheet_name": "Master",
        "extensions": [".xlsx", ".xlsm"],
        "check_tolerance": 0.001,
        "check_row_label": "Check",
        "scale_to_keur": True,
        "canvas_extra_cols": 20,
        "canvas_extra_rows_below_check": 200,
    }


def _list_workbook_sheets(path: Path) -> list[str]:
    """Return visible sheet names using openpyxl (read-only)."""
    from openpyxl import load_workbook

    wb = load_workbook(filename=str(path), read_only=True, data_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def _normalize_header_cells(raw: list[str]) -> list[str]:
    """Drop blank / default Unnamed columns from pandas header row."""
    out: list[str] = []
    for cell in raw:
        s = str(cell).strip()
        if not s:
            continue
        if s.lower().startswith("unnamed"):
            continue
        out.append(s)
    return out


def _resolve_sheet_name(requested: Optional[str], sheets: list[str]) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    """
    Pick the worksheet to read. Returns (resolved_name, error_detail).
    error_detail matches the JSON shape used in HTTP 422 responses.
    """
    if not sheets:
        return None, {
            "step": "NO_SHEETS",
            "user_message": "The workbook has no readable worksheets.",
            "available_sheets": [],
        }
    if requested is None or not str(requested).strip():
        logger.info("fdd.headers: no sheet_name provided; using first sheet %r", sheets[0])
        return sheets[0], None
    req = str(requested).strip()
    if req in sheets:
        return req, None
    req_lower = req.lower().replace("\u00a0", " ")
    for s in sheets:
        if s.lower().replace("\u00a0", " ") == req_lower:
            logger.info("fdd.headers: matched sheet case-insensitively %r -> %r", req, s)
            return s, None
    logger.warning("fdd.headers: sheet not found requested=%r available=%s", req, sheets)
    return None, {
        "step": "SHEET_NOT_FOUND",
        "user_message": (
            f"The sheet «{req}» was not found. Use one of the tab names listed under "
            f"available_sheets (check spaces and special characters)."
        ),
        "requested": req,
        "available_sheets": sheets,
    }


def _read_excel_headers(path: Path, resolved_sheet: str) -> tuple[list[str], Optional[dict[str, Any]]]:
    """Read the first row as column headers via pandas + openpyxl."""
    try:
        df = pd.read_excel(
            path,
            sheet_name=resolved_sheet,
            nrows=0,
            engine="openpyxl",
        )
        raw = [str(c) for c in df.columns.astype(str)]
    except Exception as exc:
        logger.exception("fdd.headers: pandas read failed path=%s sheet=%s", path, resolved_sheet)
        return [], {
            "step": "READ_HEADERS_FAILED",
            "user_message": f"Pandas could not read the header row: {exc}",
            "resolved_sheet": resolved_sheet,
        }

    headers = _normalize_header_cells(raw)
    if not headers:
        logger.warning(
            "fdd.headers: empty header row after normalisation raw=%r sheet=%s",
            raw[:20],
            resolved_sheet,
        )
        return [], {
            "step": "EMPTY_HEADER_ROW",
            "user_message": (
                "The first row of that sheet has no usable column names "
                "(blank cells or only default «Unnamed» columns). "
                "Put headers in row 1 or choose another sheet."
            ),
            "resolved_sheet": resolved_sheet,
            "raw_preview": raw[:40],
        }
    return headers, None


# ─── File upload ──────────────────────────────────────────────────────────────


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    session_id: str = Form(...),
):
    """
    Receive an XLSX file, save it to uploads/{session_id}/, and return:
      - file_id  — short identifier for subsequent calls
      - file_path — absolute path on disk
      - headers  — column names from the first sheet (empty list if unreadable)
      - sheet_names — tab names discovered in the workbook (best-effort)
      - upload_debug — optional hints when preview read fails
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    file_id = str(uuid.uuid4())[:8]
    dest = _session_dir(session_id) / f"{file_id}_{file.filename}"

    content = await file.read()
    dest.write_bytes(content)

    sheet_names: list[str] = []
    upload_debug: dict[str, Any] = {}
    try:
        sheet_names = _list_workbook_sheets(dest)
        logger.info("fdd.upload: file_id=%s sheets=%s", file_id, sheet_names)
    except Exception as exc:
        upload_debug["list_sheets_error"] = str(exc)
        logger.warning("fdd.upload: could not list sheets: %s", exc)

    suffix = dest.suffix.lower()
    allowed = (".xlsx", ".xlsm", ".xltx", ".xltm", ".pdf")
    if suffix not in allowed:
        upload_debug["format_hint"] = (
            f"File extension is {suffix!r}; allowed: {', '.join(allowed)}."
        )

    headers: list[str] = []
    if sheet_names:
        try:
            first = sheet_names[0]
            headers, err = _read_excel_headers(dest, first)
            if err:
                upload_debug["preview_headers"] = err.get("step")
        except Exception as exc:
            upload_debug["preview_read_error"] = str(exc)
            logger.warning("fdd.upload: preview header read failed: %s", exc)
    else:
        upload_debug["preview_headers"] = "NO_SHEETS"

    return {
        "file_id": file_id,
        "file_path": str(dest),
        "headers": headers,
        "sheet_names": sheet_names,
        "upload_debug": upload_debug or None,
    }


# ─── Header extraction ────────────────────────────────────────────────────────


@router.get("/headers/{file_id}")
def get_headers(
    file_id: str,
    session_id: str = Query(...),
    sheet_name: Optional[str] = Query(None),
):
    """
    Extract column headers from a previously uploaded XLSX.
    If sheet_name is omitted or blank, the first worksheet is used.
    On failure, returns HTTP 422 with a JSON detail object (step, user_message, …).
    """
    matched = _file_path_for_id(session_id, file_id)

    if not matched:
        logger.warning("fdd.headers: file not found file_id=%s session_id=%s", file_id, session_id)
        raise HTTPException(
            status_code=404,
            detail={
                "step": "FILE_NOT_FOUND",
                "user_message": (
                    f"No file matching id «{file_id}» was found for this chat session. "
                    "Upload the workbook again (session ids must match between upload and header read)."
                ),
                "file_id": file_id,
                "session_id": session_id,
            },
        )

    suffix = matched.suffix.lower()
    if suffix not in (".xlsx", ".xlsm", ".xltx", ".xltm"):
        logger.warning("fdd.headers: unexpected extension %s path=%s", suffix, matched)
        raise HTTPException(
            status_code=422,
            detail={
                "step": "UNSUPPORTED_FORMAT",
                "user_message": (
                    f"This endpoint expects a modern Excel file (.xlsx); got {suffix!r}. "
                    "Re-save as .xlsx or upload a compatible file."
                ),
                "path_suffix": suffix,
            },
        )

    try:
        sheets = _list_workbook_sheets(matched)
    except Exception as exc:
        logger.exception("fdd.headers: failed to open workbook %s", matched)
        raise HTTPException(
            status_code=422,
            detail={
                "step": "OPEN_WORKBOOK_FAILED",
                "user_message": f"Could not open the Excel file: {exc}",
            },
        )

    resolved, err = _resolve_sheet_name(sheet_name, sheets)
    if err:
        raise HTTPException(status_code=422, detail=err)
    if not resolved:
        raise HTTPException(
            status_code=422,
            detail={"step": "INTERNAL", "user_message": "Could not determine which worksheet to read."},
        )
    headers, read_err = _read_excel_headers(matched, resolved)
    if read_err:
        read_err["available_sheets"] = sheets
        raise HTTPException(status_code=422, detail=read_err)

    logger.info("fdd.headers: ok file_id=%s sheet=%s ncols=%s", file_id, resolved, len(headers))
    return {
        "headers": headers,
        "file_path": str(matched),
        "resolved_sheet": resolved,
    }


# ─── Folder navigation ────────────────────────────────────────────────────────


@router.get("/folders")
def list_folders(path: str = Query(default="")):
    """
    List immediate subdirectories at the given path.
    Returns {"path": ..., "subfolders": [...]} or an error message.
    """
    base = Path(path) if path else Path.home()

    if not base.exists():
        return {"path": str(base), "subfolders": [], "error": "Path does not exist"}

    try:
        subfolders = sorted(
            [d.name for d in base.iterdir() if d.is_dir() and not d.name.startswith(".")]
        )
    except PermissionError:
        return {"path": str(base), "subfolders": [], "error": "Permission denied"}

    return {"path": str(base), "subfolders": subfolders}


def _is_local_fdd_enabled() -> bool:
    return os.environ.get("FDD_LOCAL", "1").strip().lower() in ("1", "true", "yes")


def _pick_folder_native() -> tuple[str, bool]:
    """Open OS folder picker in a subprocess (tkinter). Returns (path, cancelled)."""
    code = (
        "import tkinter as tk\n"
        "from tkinter import filedialog\n"
        "root = tk.Tk()\n"
        "root.withdraw()\n"
        "try:\n"
        "    root.attributes('-topmost', True)\n"
        "except tk.TclError:\n"
        "    pass\n"
        "p = filedialog.askdirectory(title='Select output folder')\n"
        "print(p or '')\n"
    )
    try:
        proc = subprocess.run(
            [PYTHON, "-c", code],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(PROJECT_ROOT),
        )
    except subprocess.TimeoutExpired:
        return "", True
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "folder picker failed").strip()
        raise HTTPException(status_code=500, detail=err)
    chosen = (proc.stdout or "").strip()
    if not chosen:
        return "", True
    return chosen, False


def _resolve_download_path(raw_path: str) -> Path:
    """Allow downloads only under uploads/ or an existing parent directory tree."""
    if not raw_path or not str(raw_path).strip():
        raise HTTPException(status_code=400, detail="path is required")
    try:
        candidate = Path(raw_path).expanduser().resolve(strict=True)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Not a file")

    upload_root = UPLOAD_DIR.resolve()
    try:
        candidate.relative_to(upload_root)
        return candidate
    except ValueError:
        pass

    # Allow outputs saved to user-selected folders (must exist on disk).
    parent = candidate.parent.resolve()
    if parent.exists() and parent.is_dir():
        return candidate

    raise HTTPException(status_code=403, detail="Path not allowed")


@router.post("/pick-folder")
def pick_folder():
    """
    Open a native folder picker (macOS / Windows) via tkinter in a subprocess.
    Only available when FDD_LOCAL is enabled (default on local dev).
    """
    if not _is_local_fdd_enabled():
        raise HTTPException(
            status_code=403,
            detail="Native folder picker is only available in local FDD mode (FDD_LOCAL=1).",
        )
    path, cancelled = _pick_folder_native()
    return {"path": path, "cancelled": cancelled}


@router.get("/download")
def download_output_file(path: str = Query(..., description="Absolute path to output file")):
    """Download a generated output workbook (path must be under uploads or an allowed folder)."""
    resolved = _resolve_download_path(path)
    return FileResponse(
        path=str(resolved),
        filename=resolved.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ─── Pydantic models ──────────────────────────────────────────────────────────


class RunRequest(BaseModel):
    session_id: str
    file_id: str
    config: dict


class OposAuditDueDatesRequest(BaseModel):
    session_id: str
    column_letters: dict[str, str] = Field(default_factory=dict)
    snapshots: list[dict[str, Any]] = Field(default_factory=list)


class FteAuditColumnsRequest(BaseModel):
    session_id: str
    periods: list[dict[str, Any]] = Field(default_factory=list)
    columns: dict[str, str] = Field(default_factory=dict)
    header_row: int = 0


class DatabookRequest(BaseModel):
    session_id: str
    entity_files: list[Any] = Field(default_factory=list)
    entity_year_files: list[EntityYearFileGroup] = Field(default_factory=list)
    entity_names: list[str] = Field(default_factory=list)
    output_folder: str
    sort_mode: str = "total_assets_desc"
    entity_order: list[Any] = Field(default_factory=list)
    layout_format: str = ""
    value_type: str = ""
    sign_mode: str = ""
    ap_ar_included: Optional[bool] = None
    ap_ar_remove_account_length: Optional[int] = None
    column_mapping: Optional[dict[str, Any]] = None


class DatabookSusaRequest(BaseModel):
    session_id: str
    entity_names: list[str] = Field(default_factory=list)
    entity_year_files: list[EntityYearFileGroup] = Field(default_factory=list)
    entity_files: list[Any] = Field(default_factory=list)
    output_folder: str = ""
    layout_format: str = ""
    value_type: str = ""
    sign_mode: str = ""
    ap_ar_included: bool = True
    ap_ar_remove_account_length: Optional[int] = None
    column_mapping: Optional[dict[str, Any]] = None
    fy_end_month: Optional[int] = None
    fy_end_day: Optional[int] = None
    fiscal_start_month: Optional[int] = None
    ltm_month: Optional[str] = None
    first_fy: Optional[int] = None
    bs_mapping_path: Optional[str] = None
    pl_mapping_path: Optional[str] = None
    strict_mapping: bool = False
    project_name: str = "Project"


def _databook_master_path(
    session_id: str,
    output_folder: str,
    *,
    project_name: str = "Project",
    master_path: str = "",
) -> Path:
    from databook_helpers import master_workbook_path, resolve_existing_master_path

    if master_path:
        explicit = Path(master_path).expanduser()
        if explicit.is_file():
            return explicit.resolve()
    existing = resolve_existing_master_path(
        output_folder,
        session_id=session_id,
        project_name=project_name,
    )
    if existing:
        return existing
    return master_workbook_path(session_id, output_folder, project_name)


def _reorder_databook_master(master_path: str) -> None:
    """Apply standard tab order to session master workbook."""
    if not master_path or not os.path.isfile(master_path):
        return
    try:
        from databook_workbook import reorder_workbook_file

        reorder_workbook_file(master_path)
    except Exception as exc:
        logger.warning("fdd: sheet reorder failed for %s: %s", master_path, exc)


def _prepare_run_artifacts(
    session_id: str,
    file_id: str,
    config: dict,
    script_key: str,
) -> dict:
    """Write config JSON for a run; return paths or an error dict."""
    matched = _file_path_for_id(session_id, file_id)
    if not matched:
        return {"success": False, "message": f"File {file_id} not found for session {session_id}"}

    run_id = _make_run_id()
    run_d = _run_dir(session_id, run_id)

    output_folder = _resolve_output_folder(config.get("output_file_path"), session_id)

    config = dict(config)
    config["file_path"] = str(matched)
    config["output_file_path"] = output_folder
    config["case_id"] = session_id
    config["run_id"] = run_id
    if script_key not in ("bubble",):
        config.setdefault("use_session_workbook", True)

    if script_key == "bubble" and not str(config.get("output_path") or "").strip():
        config["output_path"] = os.path.join(output_folder, f"{session_id}_Bubble_Output.xlsx")

    if "current_year" not in config or not config["current_year"]:
        config["current_year"] = datetime.now().year
    if "current_month" not in config or not config["current_month"]:
        config["current_month"] = datetime.now().month
    if "fiscal_year_end_month" not in config or not config["fiscal_year_end_month"]:
        config["fiscal_year_end_month"] = 12
    if "fiscal_year_end_day" not in config or not config["fiscal_year_end_day"]:
        config["fiscal_year_end_day"] = 31

    config_path = run_d / "config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    script = SCRIPTS.get(script_key)
    if not script:
        return {"success": False, "message": f"Unknown script key: {script_key}"}

    return {
        "success": True,
        "run_id": run_id,
        "config_path": config_path,
        "config": config,
        "output_folder": output_folder,
        "script": script,
        "script_key": script_key,
    }


def _monitor_script_job(
    session_id: str,
    run_id: str,
    proc: subprocess.Popen,
    script: Path,
    config: dict,
    output_folder: str,
    script_key: str,
) -> None:
    """Background thread: wait for subprocess and write final status.json."""
    started = datetime.now(timezone.utc).isoformat()
    try:
        stdout, stderr = proc.communicate(timeout=SCRIPT_ASYNC_TIMEOUT_SEC)
        finished = datetime.now(timezone.utc).isoformat()
        if proc.returncode != 0:
            message = _clean_script_error_message((stderr or "").strip(), (stdout or "").strip())
            _write_run_status(
                session_id,
                run_id,
                {
                    "status": "failed",
                    "session_id": session_id,
                    "run_id": run_id,
                    "script_key": script_key,
                    "started_at": started,
                    "finished_at": finished,
                    "message": message,
                    "output_path": output_folder,
                },
            )
            return

        output_file, output_filename = _resolve_output_files(session_id, config, output_folder)
        if not output_file:
            _write_run_status(
                session_id,
                run_id,
                {
                    "status": "failed",
                    "session_id": session_id,
                    "run_id": run_id,
                    "script_key": script_key,
                    "started_at": started,
                    "finished_at": finished,
                    "message": "Script finished but output file was not found.",
                    "output_path": output_folder,
                },
            )
            return

        _write_run_status(
            session_id,
            run_id,
            {
                "status": "done",
                "session_id": session_id,
                "run_id": run_id,
                "script_key": script_key,
                "started_at": started,
                "finished_at": finished,
                "message": (stdout or "").strip(),
                "output_path": output_folder,
                "output_file": output_file,
                "output_filename": output_filename,
            },
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        _write_run_status(
            session_id,
            run_id,
            {
                "status": "failed",
                "session_id": session_id,
                "run_id": run_id,
                "script_key": script_key,
                "started_at": started,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "message": f"Script timed out after {SCRIPT_ASYNC_TIMEOUT_SEC}s ({script.name}).",
                "output_path": output_folder,
            },
        )
    except Exception as exc:
        logger.exception("async script monitor failed for %s/%s", session_id, run_id)
        _write_run_status(
            session_id,
            run_id,
            {
                "status": "failed",
                "session_id": session_id,
                "run_id": run_id,
                "script_key": script_key,
                "started_at": started,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "message": str(exc),
                "output_path": output_folder,
            },
        )


def _start_async_script_job(
    session_id: str,
    file_id: str,
    config: dict,
    script_key: str,
) -> dict:
    """Start script in background; return immediately with run_id."""
    prep = _prepare_run_artifacts(session_id, file_id, config, script_key)
    if not prep.get("success"):
        return prep

    run_id = prep["run_id"]
    script: Path = prep["script"]
    config_path: Path = prep["config_path"]
    output_folder: str = prep["output_folder"]
    cfg: dict = prep["config"]

    if not script.exists():
        return {"success": False, "message": f"Script not found: {script}"}

    env = _script_subprocess_env()
    script_python = _script_python()

    if script.name.startswith("PDF_Extraction") and not _check_module(script_python, "anthropic"):
        return {
            "success": False,
            "message": (
                f"Missing Python package 'anthropic' for {script_python}. "
                f"Install with: {script_python} -m pip install -r scripts/requirements_pdf_extraction.txt"
            ),
        }

    started = datetime.now(timezone.utc).isoformat()
    _write_run_status(
        session_id,
        run_id,
        {
            "status": "running",
            "session_id": session_id,
            "run_id": run_id,
            "script_key": script_key,
            "started_at": started,
            "output_path": output_folder,
        },
    )

    proc = subprocess.Popen(
        [script_python, str(script), str(config_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(PROJECT_ROOT),
        env=env,
    )

    thread = threading.Thread(
        target=_monitor_script_job,
        args=(session_id, run_id, proc, script, cfg, output_folder, script_key),
        daemon=True,
    )
    thread.start()

    return {
        "success": True,
        "status": "running",
        "session_id": session_id,
        "run_id": run_id,
        "script_key": script_key,
        "output_path": output_folder,
    }


def _prepare_and_run(
    session_id: str,
    file_id: str,
    config: dict,
    script_key: str,
) -> dict:
    """
    1. Locate the uploaded file for file_id in session.
    2. Inject file_path, output_file_path, case_id, run_id into config.
    3. Write config JSON to a run directory.
    4. Call the script.
    5. Return {success, message, output_path, run_id}.
    """
    prep = _prepare_run_artifacts(session_id, file_id, config, script_key)
    if not prep.get("success"):
        return prep

    run_id = prep["run_id"]
    config_path = prep["config_path"]
    output_folder = prep["output_folder"]
    script = prep["script"]
    cfg = prep["config"]

    result = _run_script(script, config_path)
    result["output_path"] = output_folder
    result["run_id"] = run_id
    output_file, output_filename = _resolve_output_files(session_id, cfg, output_folder)
    if output_file:
        result["output_file"] = output_file
        result["output_filename"] = output_filename
    return result


# ─── Run: General Sales Table ─────────────────────────────────────────────────


@router.post("/run/gst")
def run_gst(req: RunRequest):
    """Execute general_sales_table_MM_verformelt.py with the provided config."""
    return _prepare_and_run(req.session_id, req.file_id, req.config, "gst")


@router.post("/run/gst/async")
def run_gst_async(req: RunRequest):
    """Start GST script in background; poll GET /run/status for completion."""
    return _start_async_script_job(req.session_id, req.file_id, req.config, "gst")


# ─── Run: PVM Analysis ────────────────────────────────────────────────────────


@router.post("/run/pvm")
def run_pvm(req: RunRequest):
    """Execute pvm_verformelt.py with the provided config."""
    config = req.config

    # Map field names expected by pvm_verformelt.py
    if "fy_end_month" not in config or not config["fy_end_month"]:
        config["fy_end_month"] = 12
    if "fy_end_day" not in config or not config["fy_end_day"]:
        config["fy_end_day"] = 31
    if "as_of_year" not in config or not config["as_of_year"]:
        config["as_of_year"] = datetime.now().year
    if "as_of_month" not in config or not config["as_of_month"]:
        config["as_of_month"] = datetime.now().month

    return _prepare_and_run(req.session_id, req.file_id, config, "pvm")


@router.post("/run/pvm/async")
def run_pvm_async(req: RunRequest):
    config = req.config
    if "fy_end_month" not in config or not config["fy_end_month"]:
        config["fy_end_month"] = 12
    if "fy_end_day" not in config or not config["fy_end_day"]:
        config["fy_end_day"] = 31
    if "as_of_year" not in config or not config["as_of_year"]:
        config["as_of_year"] = datetime.now().year
    if "as_of_month" not in config or not config["as_of_month"]:
        config["as_of_month"] = datetime.now().month
    return _start_async_script_job(req.session_id, req.file_id, config, "pvm")


# ─── Run: TOP Report ──────────────────────────────────────────────────────────


@router.post("/run/top")
def run_top(req: RunRequest):
    """Execute horizontal_bars_hierachy.py (TOP report) with the provided config."""
    config = req.config
    if "current_year" not in config or not config["current_year"]:
        config["current_year"] = datetime.now().year
    if "current_month" not in config or not config["current_month"]:
        config["current_month"] = datetime.now().month
    if "fiscal_year_end_month" not in config or not config["fiscal_year_end_month"]:
        config["fiscal_year_end_month"] = 12
    if "fiscal_year_end_day" not in config or not config["fiscal_year_end_day"]:
        config["fiscal_year_end_day"] = 31

    return _prepare_and_run(req.session_id, req.file_id, config, "top")


@router.post("/run/top/async")
def run_top_async(req: RunRequest):
    config = req.config
    if "current_year" not in config or not config["current_year"]:
        config["current_year"] = datetime.now().year
    if "current_month" not in config or not config["current_month"]:
        config["current_month"] = datetime.now().month
    if "fiscal_year_end_month" not in config or not config["fiscal_year_end_month"]:
        config["fiscal_year_end_month"] = 12
    if "fiscal_year_end_day" not in config or not config["fiscal_year_end_day"]:
        config["fiscal_year_end_day"] = 31
    return _start_async_script_job(req.session_id, req.file_id, config, "top")


# ─── Run: Churn / ARR Bridge ──────────────────────────────────────────────────


def _normalize_churn_config(config: dict) -> dict:
    """Ensure Churn.py CONFIG keys (fy_end_*, as_of_*, fy_end_year) are present."""
    if "fy_end_month" not in config or not config["fy_end_month"]:
        config["fy_end_month"] = 12
    if "fy_end_day" not in config or not config["fy_end_day"]:
        config["fy_end_day"] = 31
    if "as_of_year" not in config or not config["as_of_year"]:
        config["as_of_year"] = config.get("current_year") or datetime.now().year
    if "as_of_month" not in config or not config["as_of_month"]:
        config["as_of_month"] = config.get("current_month") or datetime.now().month
    if "current_year" not in config or not config["current_year"]:
        config["current_year"] = config["as_of_year"]
    if "current_month" not in config or not config["current_month"]:
        config["current_month"] = config["as_of_month"]
    if "fy_end_year" not in config or not config["fy_end_year"]:
        cy = int(config["current_year"])
        cm = int(config["current_month"])
        fy_m = int(config["fy_end_month"])
        fy_d = int(config["fy_end_day"])
        from databook_periods import as_of_is_fy_end, current_fy_end_year_containing_as_of

        cur = current_fy_end_year_containing_as_of(cy, cm, fy_m, fy_d)
        config["fy_end_year"] = cur if as_of_is_fy_end(cy, cm, fy_m, fy_d) else cur - 1
    return config


@router.post("/run/churn")
def run_churn(req: RunRequest):
    config = _normalize_churn_config(dict(req.config))
    return _prepare_and_run(req.session_id, req.file_id, config, "churn")


@router.post("/run/churn/async")
def run_churn_async(req: RunRequest):
    config = _normalize_churn_config(dict(req.config))
    return _start_async_script_job(req.session_id, req.file_id, config, "churn")


# ─── Run: Bubble Scatter Plot ─────────────────────────────────────────────────


@router.post("/run/bubble")
def run_bubble(req: RunRequest):
    """Execute bubblescatterplot.py with the provided config."""
    config = req.config
    if "current_year" not in config or not config["current_year"]:
        config["current_year"] = datetime.now().year
    if "current_month" not in config or not config["current_month"]:
        config["current_month"] = datetime.now().month
    if "fiscal_year_end_month" not in config or not config["fiscal_year_end_month"]:
        config["fiscal_year_end_month"] = 12
    if "fiscal_year_end_day" not in config or not config["fiscal_year_end_day"]:
        config["fiscal_year_end_day"] = 31

    return _prepare_and_run(req.session_id, req.file_id, config, "bubble")


@router.post("/run/bubble/async")
def run_bubble_async(req: RunRequest):
    config = req.config
    if "current_year" not in config or not config["current_year"]:
        config["current_year"] = datetime.now().year
    if "current_month" not in config or not config["current_month"]:
        config["current_month"] = datetime.now().month
    if "fiscal_year_end_month" not in config or not config["fiscal_year_end_month"]:
        config["fiscal_year_end_month"] = 12
    if "fiscal_year_end_day" not in config or not config["fiscal_year_end_day"]:
        config["fiscal_year_end_day"] = 31
    return _start_async_script_job(req.session_id, req.file_id, config, "bubble")


@router.get("/run/status")
def run_status(
    session_id: str = Query(...),
    run_id: str = Query(...),
):
    """Poll async script job status (running | done | failed)."""
    status = _read_run_status(session_id, run_id)
    if not status:
        raise HTTPException(status_code=404, detail="Run not found")
    return status


# ─── SuSa preview (column mapper) ─────────────────────────────────────────────


@router.get("/susa/preview")
def susa_preview(
    session_id: str = Query(...),
    file_id: str = Query(...),
    sheet_index: int = Query(0, ge=0),
):
    """
    Raw matrix from first uploaded trial balance (header row + letter columns + sample rows).
    Used by the interactive column-mapping wizard in the FDD bot UI.
    """
    if not (file_id or "").strip():
        raise HTTPException(status_code=400, detail="file_id is required for preview.")

    path = _file_path_for_id(session_id, file_id)
    if not path or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found for session_id / file_id.")

    from susa_column_mapping import build_preview

    try:
        raw = pd.read_excel(path, sheet_name=sheet_index, header=None, engine="openpyxl")
        preview = build_preview(raw)
        preview["file_id"] = file_id
        preview["sheet_index"] = sheet_index
        return preview
    except Exception as exc:
        logger.exception("susa preview failed")
        raise HTTPException(status_code=400, detail=f"Could not read workbook: {exc}") from exc


# ─── Run: OPOS Aging ───────────────────────────────────────────────────────────


def _normalize_opos_side_snapshots(
    raw_snaps: list,
    session_id: str,
    partner_col: str = "",
) -> list[dict]:
    from opos import resolve_snapshot_sheet_name

    normalized: list[dict] = []
    for item in raw_snaps:
        if not isinstance(item, dict):
            continue
        snap = dict(item)
        fid = str(snap.get("file_id") or "").strip()
        fp = str(snap.get("file_path") or "").strip()
        if not fp and fid:
            resolved = _file_path_for_id(session_id, fid)
            if resolved:
                fp = str(resolved)
        if not fp:
            raise ValueError(f"OPOS snapshot file not found for session {session_id}")
        snap["file_path"] = fp
        if not str(snap.get("sheet_name") or "").strip() and partner_col:
            snap["sheet_name"] = resolve_snapshot_sheet_name(fp, partner_col, "")
        normalized.append(snap)
    return normalized


def _normalize_opos_config(config: dict, session_id: str) -> dict:
    """Resolve snapshot file_ids to paths and infer sheet names."""
    cfg = dict(config)
    partner_col = str((cfg.get("columns") or {}).get("partner_id") or "").strip()
    sides = cfg.get("sides")
    if isinstance(sides, dict) and sides:
        for side in ("debitor", "kreditor"):
            block = dict(sides.get(side) or {})
            raw_snaps = block.get("snapshots")
            if not isinstance(raw_snaps, list) or not raw_snaps:
                raise ValueError(f"OPOS config sides.{side} requires snapshots[]")
            block["snapshots"] = _normalize_opos_side_snapshots(
                raw_snaps, session_id, partner_col=""
            )
            sides[side] = block
        cfg["sides"] = sides
        first = sides["debitor"]["snapshots"][0]
        cfg["file_path"] = first["file_path"]
        cfg["sheet_name"] = first.get("sheet_name", "")
        return cfg

    raw_snaps = cfg.get("snapshots")
    if not isinstance(raw_snaps, list) or not raw_snaps:
        fp = str(cfg.get("file_path") or "").strip()
        as_of = str(cfg.get("as_of") or "").strip()
        if fp and as_of:
            raw_snaps = [{"as_of": as_of, "file_path": fp, "sheet_name": cfg.get("sheet_name", "")}]
        else:
            raise ValueError("OPOS config requires snapshots[] or file_path+as_of")

    normalized = _normalize_opos_side_snapshots(raw_snaps, session_id, partner_col=partner_col)
    if not normalized:
        raise ValueError("OPOS config has no valid snapshots")
    cfg["snapshots"] = normalized
    cfg["file_path"] = normalized[0]["file_path"]
    cfg["sheet_name"] = normalized[0]["sheet_name"]
    return cfg


@router.post("/run/opos")
def run_opos(req: RunRequest):
    try:
        config = _normalize_opos_config(dict(req.config), req.session_id)
    except ValueError as exc:
        return {"success": False, "message": str(exc)}
    return _prepare_and_run(req.session_id, req.file_id, config, "opos")


@router.post("/run/opos/async")
def run_opos_async(req: RunRequest):
    try:
        config = _normalize_opos_config(dict(req.config), req.session_id)
    except ValueError as exc:
        return {"success": False, "message": str(exc)}
    return _start_async_script_job(req.session_id, req.file_id, config, "opos")


@router.get("/opos/preview")
def opos_preview(
    session_id: str = Query(...),
    file_id: str = Query(...),
    sheet_name: str = Query(""),
    sheet_index: int = Query(0, ge=0),
    header_row: int = Query(0, ge=0),
    max_rows: int = Query(12, ge=1, le=30),
):
    """Matrix preview for OPOS column mapper (header + letter columns + sample rows)."""
    return _workbook_matrix_preview(session_id, file_id, sheet_name, sheet_index, header_row, max_rows)


def _flatten_opos_snapshots_for_audit(
    snapshots: list[dict[str, Any]],
    session_id: str,
) -> list[dict[str, str]]:
    """Expand dual debitor/kreditor rows into flat snapshot dicts with resolved paths."""
    out: list[dict[str, str]] = []
    dual = bool(
        snapshots
        and isinstance(snapshots[0], dict)
        and "debitor" in snapshots[0]
        and "kreditor" in snapshots[0]
    )
    for item in snapshots:
        if not isinstance(item, dict):
            continue
        as_of = str(item.get("as_of") or "").strip()
        if not as_of:
            continue
        blocks: list[dict[str, str]]
        if dual:
            blocks = []
            for side in ("debitor", "kreditor"):
                raw = item.get(side) if isinstance(item.get(side), dict) else {}
                blocks.append(
                    {
                        "file_id": str(raw.get("file_id") or "").strip(),
                        "file_path": str(raw.get("file_path") or "").strip(),
                        "sheet_name": str(raw.get("sheet_name") or "").strip(),
                    }
                )
        else:
            blocks = [
                {
                    "file_id": str(item.get("file_id") or "").strip(),
                    "file_path": str(item.get("file_path") or "").strip(),
                    "sheet_name": str(item.get("sheet_name") or "").strip(),
                }
            ]
        for block in blocks:
            fp = block["file_path"]
            if not fp and block["file_id"]:
                resolved = _file_path_for_id(session_id, block["file_id"])
                if resolved:
                    fp = str(resolved)
            if not fp:
                continue
            out.append({"as_of": as_of, "file_path": fp, "sheet_name": block["sheet_name"]})
    return out


@router.post("/opos/audit-due-dates")
def opos_audit_due_dates(req: OposAuditDueDatesRequest):
    """Count OPOS rows with partner id but missing due date (debitor + kreditor)."""
    from opos import audit_missing_due_date_rows

    letters = dict(req.column_letters or {})
    if not letters:
        raise HTTPException(status_code=400, detail="column_letters is required")
    snaps = _flatten_opos_snapshots_for_audit(req.snapshots, req.session_id)
    if not snaps:
        raise HTTPException(status_code=400, detail="No valid snapshots to audit")
    return audit_missing_due_date_rows(snaps, column_letters=letters)


# ─── Fixed Assets Rollforward preview + run ───────────────────────────────────


@router.get("/fa_rollf/preview")
def fa_rollf_preview(
    session_id: str = Query(...),
    file_id: str = Query(...),
    sheet_name: str = Query(""),
    sheet_index: int = Query(0, ge=0),
    header_row: int = Query(0, ge=0),
    max_rows: int = Query(12, ge=1, le=30),
):
    return _workbook_matrix_preview(session_id, file_id, sheet_name, sheet_index, header_row, max_rows)


@router.post("/run/fixed_assets_rollf")
def run_fixed_assets_rollf(req: RunRequest):
    try:
        config = _normalize_fa_rollf_config(dict(req.config), req.session_id)
    except ValueError as exc:
        return {"success": False, "message": str(exc)}
    return _prepare_and_run(req.session_id, req.file_id, config, "fixed_assets_rollf")


@router.post("/run/fixed_assets_rollf/async")
def run_fixed_assets_rollf_async(req: RunRequest):
    try:
        config = _normalize_fa_rollf_config(dict(req.config), req.session_id)
    except ValueError as exc:
        return {"success": False, "message": str(exc)}
    return _start_async_script_job(req.session_id, req.file_id, config, "fixed_assets_rollf")


def _normalize_fte_payroll_config(config: dict, session_id: str) -> dict:
    """Resolve period file_ids to paths and infer sheet names."""
    cfg = dict(config)
    raw_periods = cfg.get("periods")
    if not isinstance(raw_periods, list) or not raw_periods:
        raise ValueError("FTE payroll config requires periods[]")

    from FTE_payroll import _resolve_sheet_name

    normalized: list[dict] = []
    for item in raw_periods:
        if not isinstance(item, dict):
            continue
        period = dict(item)
        label = str(period.get("label") or "").strip()
        if not label:
            continue
        fid = str(period.get("file_id") or "").strip()
        fp = str(period.get("file_path") or "").strip()
        if not fp and fid:
            resolved = _file_path_for_id(session_id, fid)
            if resolved:
                fp = str(resolved)
        if not fp:
            raise ValueError(f"FTE payroll period '{label}' file not found for session {session_id}")
        period["file_path"] = fp
        sheet = str(period.get("sheet_name") or "").strip()
        period["sheet_name"] = _resolve_sheet_name(fp, sheet)
        normalized.append(period)

    if not normalized:
        raise ValueError("FTE payroll config has no valid periods")
    cfg["periods"] = normalized
    cfg["file_path"] = normalized[0]["file_path"]
    cfg["sheet_name"] = str(cfg.get("sheet_name") or "FTE Development").strip()
    master = str(cfg.get("master_pl_path") or "").strip()
    if not master:
        master_slot = str(cfg.get("db_master_workbook_path") or "").strip()
        if master_slot and os.path.isfile(master_slot):
            cfg["master_pl_path"] = master_slot
    return cfg


@router.post("/fte_payroll/audit-columns")
def fte_audit_columns(req: FteAuditColumnsRequest):
    """Count FTE rows with invalid employment rate or annual working time."""
    from FTE_payroll import audit_invalid_fte_columns

    cols = dict(req.columns or {})
    if not cols.get("employment") or not cols.get("months_sum"):
        raise HTTPException(status_code=400, detail="columns.employment and columns.months_sum are required")

    normalized: list[dict] = []
    for item in req.periods or []:
        if not isinstance(item, dict):
            continue
        period = dict(item)
        fid = str(period.get("file_id") or "").strip()
        fp = str(period.get("file_path") or "").strip()
        if not fp and fid:
            resolved = _file_path_for_id(req.session_id, fid)
            if resolved:
                fp = str(resolved)
        if not fp:
            continue
        period["file_path"] = fp
        normalized.append(period)
    if not normalized:
        raise HTTPException(status_code=400, detail="No valid periods to audit")
    return audit_invalid_fte_columns(normalized, cols, header_row=int(req.header_row or 0))


@router.get("/fte_payroll/preview")
def fte_payroll_preview(
    session_id: str = Query(...),
    file_id: str = Query(...),
    sheet_name: str = Query(""),
    sheet_index: int = Query(0, ge=0),
    header_row: int = Query(0, ge=0),
    max_rows: int = Query(12, ge=1, le=30),
):
    return _workbook_matrix_preview(session_id, file_id, sheet_name, sheet_index, header_row, max_rows)


@router.post("/run/fte_payroll")
def run_fte_payroll_endpoint(req: RunRequest):
    try:
        config = _normalize_fte_payroll_config(dict(req.config), req.session_id)
    except ValueError as exc:
        return {"success": False, "message": str(exc)}
    return _prepare_and_run(req.session_id, req.file_id, config, "fte_payroll")


@router.post("/run/fte_payroll/async")
def run_fte_payroll_async(req: RunRequest):
    try:
        config = _normalize_fte_payroll_config(dict(req.config), req.session_id)
    except ValueError as exc:
        return {"success": False, "message": str(exc)}
    return _start_async_script_job(req.session_id, req.file_id, config, "fte_payroll")


# ─── Run: Databook — SuSa step ────────────────────────────────────────────────


@router.post("/run/databook/susa")
def run_databook_susa(req: DatabookSusaRequest):
    """
    Execute SuSabyYear.py to create the Master Sheet from structured entity × FY uploads.
    """
    sess = _session_dir(req.session_id)
    run_id = _make_run_id("db_")
    run_d = _run_dir(req.session_id, run_id)

    output_folder = _resolve_output_folder(req.output_folder, req.session_id)

    groups = list(req.entity_year_files)
    if not groups and req.entity_files:
        for it in req.entity_files:
            if isinstance(it, dict) and isinstance(it.get("file_ids"), list):
                try:
                    groups.append(EntityYearFileGroup.model_validate(it))
                except Exception:
                    logger.warning("Skipping invalid entity_year_files entry: %s", it)

    layout = (req.layout_format or "").strip()
    if layout not in ("monthly_workbooks", "monthly_sheets", "single_sheet"):
        raise HTTPException(
            status_code=400,
            detail="layout_format must be monthly_workbooks, monthly_sheets, or single_sheet.",
        )

    resolved_cells = _resolve_entity_year_files_for_susa(req.session_id, groups)
    if not resolved_cells:
        raise HTTPException(
            status_code=400,
            detail="No uploaded files could be resolved for Databook (check file_id and session_id).",
        )

    master = _databook_master_path(req.session_id, output_folder, project_name=req.project_name)
    out_xlsx = str(master)
    flat_paths = [p for cell in resolved_cells for p in cell["paths"]]

    column_mapping = req.column_mapping
    if not column_mapping:
        logger.warning("Databook SuSa run without column_mapping; using legacy default letters.")

    fy_end_month = int(req.fy_end_month or 12)
    fiscal_start_month = int(req.fiscal_start_month or ((fy_end_month % 12) + 1))
    fy_end_day = int(req.fy_end_day or 31)
    from databook_periods import _parse_ltm_month, ytd_reporting_fy_end_year

    ltm_month = str(req.ltm_month or "").strip() or None
    ytd_fy = ytd_reporting_fy_end_year(ltm_month, fy_end_month, fy_end_day)
    ltm_parsed = _parse_ltm_month(ltm_month)
    from susa_mapping_paths import resolve_susa_kontenmapping_paths

    bs_mapping_path, pl_mapping_path = resolve_susa_kontenmapping_paths(
        {
            "bs_mapping_path": (req.bs_mapping_path or "").strip() or None,
            "pl_mapping_path": (req.pl_mapping_path or "").strip() or None,
            "output_file_path": output_folder,
        }
    )

    config: dict[str, Any] = {
        **_default_susa_workbook_config(),
        "entity_year_files": resolved_cells,
        "entity_names": list(req.entity_names),
        "entity_files": flat_paths,
        "layout_format": layout,
        "value_type": (req.value_type or "balances").strip(),
        "sign_mode": (req.sign_mode or "sh_column").strip(),
        "ap_ar_included": bool(req.ap_ar_included),
        "ap_ar_remove_account_length": req.ap_ar_remove_account_length,
        "column_mapping": column_mapping,
        "fy_end_month": fy_end_month,
        "fy_end_day": fy_end_day,
        "fiscal_start_month": fiscal_start_month,
        "ltm_month": ltm_month,
        "first_fy": int(req.first_fy) if req.first_fy else None,
        "bs_mapping_path": bs_mapping_path,
        "pl_mapping_path": pl_mapping_path,
        "strict_mapping": bool(req.strict_mapping),
        "output_file_path": output_folder,
        "output_path": out_xlsx,
        "run_id": run_id,
        "session_id": req.session_id,
        "case_id": req.session_id,
    }
    if ytd_fy is not None and ltm_parsed is not None:
        config["ytd_reporting_fy_end_year"] = ytd_fy
        config["ytd_ltm_year"] = ltm_parsed[0]
        config["ytd_ltm_month"] = ltm_parsed[1]

    config_path = run_d / "susa_config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    result = _run_script(SCRIPTS["susa"], config_path)
    result["output_path"] = output_folder
    result["run_id"] = run_id
    result["session_id"] = req.session_id
    result["master_path"] = out_xlsx
    if master.is_file():
        result["output_file"] = out_xlsx
        result["output_filename"] = master.name
    return result


# ─── Run: Databook — full orchestration ─────────────────────────────────────


@router.post("/run/databook")
def run_databook(req: DatabookRequest):
    """
    Orchestrate the full Databook pipeline:
      1. SuSabyYear.py   → Master Sheet
      2. Consolidation.py
      3. Adjustments.py
      4. Lead_IS.py
    Each step runs sequentially; stops on first failure.
    """
    sess = _session_dir(req.session_id)
    run_id = _make_run_id("db_full_")
    run_d = _run_dir(req.session_id, run_id)

    output_folder = _resolve_output_folder(req.output_folder, req.session_id)

    groups = list(req.entity_year_files)
    if not groups and req.entity_files:
        for it in req.entity_files:
            if isinstance(it, dict) and isinstance(it.get("file_ids"), list):
                try:
                    groups.append(EntityYearFileGroup.model_validate(it))
                except Exception:
                    logger.warning("Skipping invalid entity_year_files entry: %s", it)

    path_items: list[Any] = [g.model_dump() for g in groups] if groups else list(req.entity_files)
    paths = _resolve_entity_file_paths(req.session_id, path_items)
    if not paths:
        raise HTTPException(
            status_code=400,
            detail="No uploaded files could be resolved for Databook (check file_id and session_id).",
        )

    layout = (req.layout_format or "").strip() or "monthly_sheets"
    value_type = (req.value_type or "balances").strip()
    sign_mode = (req.sign_mode or "sh_column").strip()
    ap_ar = True if req.ap_ar_included is None else bool(req.ap_ar_included)

    resolved_cells = _resolve_entity_year_files_for_susa(req.session_id, groups)
    if not resolved_cells:
        raise HTTPException(
            status_code=400,
            detail="No resolvable entity_year_files for Databook. Upload files via the trial balance grid and interpretation steps.",
        )
    master = _databook_master_path(req.session_id, output_folder, project_name=req.project_name)
    out_xlsx = str(master)
    fy_end_month = int(getattr(req, "fy_end_month", None) or 12)
    fiscal_start_month = int(getattr(req, "fiscal_start_month", None) or ((fy_end_month % 12) + 1))

    from susa_mapping_paths import resolve_susa_kontenmapping_paths

    bs_mapping_path, pl_mapping_path = resolve_susa_kontenmapping_paths(
        {"output_file_path": output_folder}
    )

    base_config: dict[str, Any] = {
        "entity_files": paths,
        "entity_names": req.entity_names,
        "entity_year_files": resolved_cells,
        "layout_format": layout,
        "value_type": value_type,
        "sign_mode": sign_mode,
        "ap_ar_included": ap_ar,
        "ap_ar_remove_account_length": req.ap_ar_remove_account_length,
        "column_mapping": req.column_mapping,
        "fy_end_month": fy_end_month,
        "fiscal_start_month": fiscal_start_month,
        "bs_mapping_path": bs_mapping_path,
        "pl_mapping_path": pl_mapping_path,
        "strict_mapping": bool(getattr(req, "strict_mapping", False)),
        "output_file_path": output_folder,
        "output_path": out_xlsx,
        "run_id": run_id,
        "session_id": req.session_id,
        "case_id": req.session_id,
        "sort_mode": req.sort_mode,
        "entity_order": req.entity_order,
    }

    steps = [
        ("susa", "Trial balance master sheet"),
        ("consolidation", "Consolidation"),
        ("adjustments", "Adjustments"),
        ("lead_is", "Lead IS"),
    ]

    messages: list[str] = []
    for step_key, step_label in steps:
        script = SCRIPTS.get(step_key)
        if not script or not script.exists():
            messages.append(f"[SKIP] {step_label}: script not found at {script}")
            continue

        config_path = run_d / f"{step_key}_config.json"
        if step_key == "susa":
            cfg = {**_default_susa_workbook_config(), **base_config}
        else:
            cfg = dict(base_config)
        config_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        result = _run_script(script, config_path)
        if result["success"]:
            messages.append(f"[OK] {step_label}")
        else:
            messages.append(f"[ERROR] {step_label}: {result['message']}")
            return {
                "success": False,
                "message": "\n".join(messages),
                "output_path": output_folder,
                "run_id": run_id,
            }

    result: dict[str, Any] = {
        "success": True,
        "message": "\n".join(messages),
        "output_path": output_folder,
        "run_id": run_id,
        "session_id": req.session_id,
        "master_path": out_xlsx,
    }
    if master.is_file():
        result["output_file"] = out_xlsx
        result["output_filename"] = master.name
    else:
        output_file = os.path.join(output_folder, f"{req.session_id}_Output.xlsx")
        if os.path.isfile(output_file):
            result["output_file"] = output_file
            result["output_filename"] = os.path.basename(output_file)
    return result


class DatabookConsolidationRequest(BaseModel):
    session_id: str
    consolidation_file_id: str
    output_folder: str = ""
    master_path: str = ""
    project_name: str = "Project"
    fy_end_month: Optional[int] = None
    fy_end_day: Optional[int] = None
    fiscal_start_month: Optional[int] = None


class DatabookConsolidationTemplateRequest(BaseModel):
    session_id: str
    output_folder: str = ""
    master_path: str = ""
    project_name: str = "Project"
    period_level: str = "yearly"
    first_fy: Optional[int] = None
    ltm_month: Optional[str] = None
    fy_end_month: Optional[int] = None
    fy_end_day: Optional[int] = None
    fiscal_start_month: Optional[int] = None


class DatabookAdjustmentsTemplateRequest(BaseModel):
    session_id: str
    output_folder: str = ""
    master_path: str = ""
    project_name: str = "Project"
    first_fy: Optional[int] = None
    ltm_month: Optional[str] = None
    fy_end_month: Optional[int] = None
    fy_end_day: Optional[int] = None
    fiscal_start_month: Optional[int] = None


class DatabookAdjustmentsRequest(BaseModel):
    session_id: str
    adjustments_file_id: str
    output_folder: str = ""
    master_path: str = ""
    project_name: str = "Project"


class DatabookImportMasterRequest(BaseModel):
    session_id: str
    master_file_id: str
    output_folder: str = ""
    project_name: str = "Project"


class DatabookExtractEntitiesRequest(BaseModel):
    session_id: str
    output_folder: str = ""
    master_path: str = ""
    project_name: str = "Project"


class DatabookPdfExtractionRequest(BaseModel):
    session_id: str
    pdf_file_ids: list[str] = Field(default_factory=list)
    output_folder: str = ""
    fy_end_month: int = 12


class DatabookReconPlRequest(BaseModel):
    session_id: str
    output_folder: str = ""
    master_path: str = ""
    project_name: str = "Project"
    company_name: str = "Group"
    entity_order: list[str] = Field(default_factory=list)
    display_titles: dict[str, str] = Field(default_factory=dict)
    display_label_map: dict[str, str] = Field(default_factory=dict)
    fs_review_file_id: str = ""
    show_fs_check: bool = False
    l4_sort_basis: str = "latest_fy"


class DatabookReconPipelineRequest(BaseModel):
    session_id: str
    output_folder: str = ""
    master_path: str = ""
    project_name: str = "Project"
    company_name: str = "Group"
    entity_order: list[str] = Field(default_factory=list)


class DatabookReconCoreRequest(BaseModel):
    session_id: str
    output_folder: str = ""
    master_path: str = ""
    project_name: str = "Project"
    company_name: str = "Group"
    entity_order: list[str] = Field(default_factory=list)
    l4_sort_basis: str = "latest_fy"


class DatabookExtendedTablesRequest(BaseModel):
    session_id: str
    output_folder: str = ""
    master_path: str = ""
    project_name: str = "Project"
    company_name: str = "Group"
    entity_order: list[str] = Field(default_factory=list)
    l4_sort_basis: str = "latest_fy"


# ─── Run: Databook — post-SuSa steps ─────────────────────────────────────────


@router.post("/run/databook/import-master")
def run_databook_import_master(req: DatabookImportMasterRequest):
    import shutil

    from databook_helpers import extract_master_entities

    uploaded = _file_path_for_id(req.session_id, req.master_file_id)
    if not uploaded or not uploaded.is_file():
        raise HTTPException(status_code=404, detail="Uploaded master file not found.")

    output_folder = _resolve_output_folder(req.output_folder, req.session_id)
    master = _databook_master_path(req.session_id, output_folder, project_name=req.project_name)
    master.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(uploaded, master)
    entities = extract_master_entities(master)

    return {
        "success": True,
        "master_path": str(master.resolve()),
        "output_file": str(master.resolve()),
        "output_filename": master.name,
        "entities": entities,
    }


@router.post("/run/databook/extract-entities")
def run_databook_extract_entities(req: DatabookExtractEntitiesRequest):
    """Return entity names from session master (or explicit master_path)."""
    from databook_helpers import extract_master_entities

    output_folder = _resolve_output_folder(req.output_folder, req.session_id)
    master = _databook_master_path(
        req.session_id,
        output_folder,
        project_name=req.project_name,
        master_path=req.master_path,
    )
    if not master.is_file():
        raise HTTPException(status_code=404, detail=f"Master workbook not found: {master}")
    return {"success": True, "entities": extract_master_entities(master)}


@router.post("/run/databook/consolidation-template")
def run_databook_consolidation_template(req: DatabookConsolidationTemplateRequest):
    from databook_helpers import consolidation_template_path

    sess = _session_dir(req.session_id)
    run_id = _make_run_id("db_cons_tpl_")
    run_d = _run_dir(req.session_id, run_id)

    output_folder = _resolve_output_folder(req.output_folder, req.session_id)
    master = _databook_master_path(
        req.session_id,
        output_folder,
        project_name=req.project_name,
        master_path=req.master_path,
    )
    template_out = consolidation_template_path(req.session_id, output_folder)
    period_level = str(req.period_level or "yearly").strip().lower()
    if period_level not in ("yearly", "monthly"):
        raise HTTPException(
            status_code=400,
            detail="period_level must be 'yearly' or 'monthly'.",
        )

    fy_end_month = int(req.fy_end_month or 12)
    fiscal_start_month = int(
        req.fiscal_start_month or ((fy_end_month % 12) + 1)
    )

    config: dict[str, Any] = {
        "session_id": req.session_id,
        "output_folder": output_folder,
        "master_path": str(master),
        "period_level": period_level,
        "output_path": str(template_out),
        "first_fy": req.first_fy,
        "ltm_month": req.ltm_month,
        "fy_end_month": fy_end_month,
        "fy_end_day": int(req.fy_end_day or 31),
        "fiscal_start_month": fiscal_start_month,
    }
    config_path = run_d / "consolidation_template_config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    result = _run_script(SCRIPTS["consolidation_template"], config_path)
    result["run_id"] = run_id
    result["period_level"] = period_level
    result["template_path"] = str(template_out)
    if template_out.is_file():
        result["output_file"] = str(template_out)
        result["output_filename"] = template_out.name
    return result


@router.post("/run/databook/adjustments-template")
def run_databook_adjustments_template(req: DatabookAdjustmentsTemplateRequest):
    from databook_helpers import adjustments_template_path

    sess = _session_dir(req.session_id)
    run_id = _make_run_id("db_adj_tpl_")
    run_d = _run_dir(req.session_id, run_id)

    output_folder = _resolve_output_folder(req.output_folder, req.session_id)
    master = _databook_master_path(
        req.session_id,
        output_folder,
        project_name=req.project_name,
        master_path=req.master_path,
    )
    template_out = adjustments_template_path(req.session_id, output_folder)

    fy_end_month = int(req.fy_end_month or 12)
    fiscal_start_month = int(
        req.fiscal_start_month or ((fy_end_month % 12) + 1)
    )

    config: dict[str, Any] = {
        "session_id": req.session_id,
        "output_folder": output_folder,
        "master_path": str(master),
        "output_path": str(template_out),
        "first_fy": req.first_fy,
        "ltm_month": req.ltm_month,
        "fy_end_month": fy_end_month,
        "fy_end_day": int(req.fy_end_day or 31),
        "fiscal_start_month": fiscal_start_month,
    }
    config_path = run_d / "adjustments_template_config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    result = _run_script(SCRIPTS["adjustments_template"], config_path)
    result["run_id"] = run_id
    result["template_path"] = str(template_out)
    if template_out.is_file():
        result["output_file"] = str(template_out)
        result["output_filename"] = template_out.name
    return result


@router.post("/run/databook/adjustments")
def run_databook_adjustments(req: DatabookAdjustmentsRequest):
    sess = _session_dir(req.session_id)
    run_id = _make_run_id("db_adj_")
    run_d = _run_dir(req.session_id, run_id)

    output_folder = _resolve_output_folder(req.output_folder, req.session_id)
    master = _databook_master_path(
        req.session_id,
        output_folder,
        project_name=req.project_name,
        master_path=req.master_path,
    )

    adj_path = _file_path_for_id(req.session_id, req.adjustments_file_id)
    if not adj_path or not adj_path.is_file():
        raise HTTPException(status_code=404, detail="Adjustments file not found.")

    config = {
        "master_path": str(master),
        "adjustments_path": str(adj_path),
        "session_id": req.session_id,
    }
    config_path = run_d / "adjustments_config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    result = _run_script(SCRIPTS["adjustments"], config_path)
    result["output_file"] = str(master)
    result["output_filename"] = master.name
    result["master_path"] = str(master)
    return result


@router.post("/run/databook/consolidation")
def run_databook_consolidation(req: DatabookConsolidationRequest):
    sess = _session_dir(req.session_id)
    run_id = _make_run_id("db_cons_")
    run_d = _run_dir(req.session_id, run_id)

    output_folder = _resolve_output_folder(req.output_folder, req.session_id)
    master = _databook_master_path(
        req.session_id,
        output_folder,
        project_name=req.project_name,
        master_path=req.master_path,
    )
    cons_path = _file_path_for_id(req.session_id, req.consolidation_file_id)
    if not cons_path or not cons_path.is_file():
        raise HTTPException(status_code=404, detail="Consolidation file not found.")

    fy_end_month = int(req.fy_end_month or 12)
    fiscal_start_month = int(
        req.fiscal_start_month or ((fy_end_month % 12) + 1)
    )
    config = {
        "master_path": str(master),
        "consolidation_path": str(cons_path),
        "session_id": req.session_id,
        "fy_end_month": fy_end_month,
        "fy_end_day": int(req.fy_end_day or 31),
        "fiscal_start_month": fiscal_start_month,
    }
    config_path = run_d / "consolidation_config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    result = _run_script(SCRIPTS["consolidation"], config_path)
    result["output_file"] = str(master)
    result["output_filename"] = master.name
    result["master_path"] = str(master)
    return result


@router.post("/run/databook/pdf-extraction")
def run_databook_pdf_extraction(req: DatabookPdfExtractionRequest):
    from databook_helpers import FS_EXTRACTION_TEMPLATE, TEMPLATES_DIR

    sess = _session_dir(req.session_id)
    run_id = _make_run_id("db_pdf_")
    run_d = _run_dir(req.session_id, run_id)
    output_folder = Path(_resolve_output_folder(req.output_folder, req.session_id))

    pdf_dir = run_d / "pdfs"
    pdf_dir.mkdir(exist_ok=True)
    for fid in req.pdf_file_ids:
        src = _file_path_for_id(req.session_id, fid)
        if src and src.is_file():
            dest = pdf_dir / src.name.split("_", 1)[-1]
            dest.write_bytes(src.read_bytes())

    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    template_path = FS_EXTRACTION_TEMPLATE
    if not template_path.is_file():
        from PDF_Extraction_recon import ensure_template
        ensure_template(template_path)

    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    fy_month_bs = month_names[min(max(int(req.fy_end_month), 1), 12) - 1]

    out_recon = output_folder / f"{req.session_id}_FS_extracted.xlsx"
    out_adj = output_folder / f"{req.session_id}_FS_adjusted.xlsx"
    out_checked = output_folder / f"{req.session_id}_FS_checked.xlsx"

    md_dir = output_folder / "md"
    recon_cfg = {
        "pdf_input_dir": str(pdf_dir),
        "template_path": str(template_path),
        "output_excel": str(out_recon),
        "fy_month_bs": fy_month_bs,
        "pdf_md_cache_dir": str(output_folder / ".pdf-md-cache"),
        "md_output_dir": str(md_dir),
    }
    recon_path = run_d / "pdf_recon_config.json"
    recon_path.write_text(json.dumps(recon_cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    result = _run_script(SCRIPTS["pdf_recon"], recon_path)
    if not result.get("success"):
        return result

    adj_cfg = {"input_file": str(out_recon), "output_file": str(out_adj)}
    adj_path = run_d / "pdf_adj_config.json"
    adj_path.write_text(json.dumps(adj_cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    result = _run_script(SCRIPTS["pdf_adjusted"], adj_path)
    if not result.get("success"):
        return result

    chk_cfg = {"input_file": str(out_adj), "output_file": str(out_checked)}
    chk_path = run_d / "pdf_chk_config.json"
    chk_path.write_text(json.dumps(chk_cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    result = _run_script(SCRIPTS["pdf_checked"], chk_path)
    result["output_file"] = str(out_checked)
    result["output_filename"] = out_checked.name
    result["review_file"] = str(out_checked)
    return result


@router.post("/run/databook/recon-pl")
def run_databook_recon_pl(req: DatabookReconPlRequest):
    from databook_helpers import (
        ensure_pl_mapping_file,
        extract_fs_check_values,
        prepare_master_pl_for_recon,
    )

    sess = _session_dir(req.session_id)
    run_id = _make_run_id("db_recon_")
    run_d = _run_dir(req.session_id, run_id)

    output_folder = _resolve_output_folder(req.output_folder, req.session_id)
    master = _databook_master_path(
        req.session_id,
        output_folder,
        project_name=req.project_name,
        master_path=req.master_path,
    )
    if not master.is_file():
        raise HTTPException(status_code=404, detail=f"Master workbook not found: {master}")

    prepare_master_pl_for_recon(master)
    mapping_file = ensure_pl_mapping_file()
    recon_target = master.parent / f"{req.session_id}_recon_pl.xlsx"

    titles = {
        "aggregated_title": req.display_titles.get("aggregated_title", "Aggregated"),
        "consolidation_title": req.display_titles.get("consolidation_title", "Consolidation"),
        "difference_title": req.display_titles.get("difference_title", "Difference"),
        "financial_statements_title": req.display_titles.get(
            "financial_statements_title", "Financial statements"
        ),
        "ic_display_name": req.display_titles.get("ic_display_name", "IC eliminations"),
    }

    fs_check = {"entities": [], "consolidation": []}
    if req.fs_review_file_id:
        fs_path = _file_path_for_id(req.session_id, req.fs_review_file_id)
        if fs_path and fs_path.is_file():
            fs_check = extract_fs_check_values(fs_path)

    config = {
        "project_name": req.project_name,
        "company_name": req.company_name,
        "sort_by": "custom",
        "l4_sort_basis": str(req.l4_sort_basis or "latest_fy").strip().lower(),
        "entity_order": list(req.entity_order),
        "show_fs_check": bool(req.show_fs_check),
        "fs_check_values": fs_check,
        "display": {"titles": titles},
        "pl_config": {"display_label_map": dict(req.display_label_map)},
        "paths": {
            "source_file": str(master),
            "source_sheet": "Master_PL",
            "source_engine": "openpyxl",
            "target_file": str(recon_target),
            "report_sheet": "PL_Reconciliation",
            "audit_master_sheet": "Master_PL",
            "mapping_file": str(mapping_file),
            "append_to_master": True,
            "master_file": str(master),
        },
    }
    config_path = run_d / "recon_pl_config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    result = _run_script(SCRIPTS["recon_pl"], config_path)
    result["output_file"] = str(master)
    result["output_filename"] = master.name
    result["master_path"] = str(master)
    return result


def _default_recon_display_titles() -> dict[str, str]:
    return {
        "aggregated_title": "Aggregated",
        "consolidation_title": "Consolidation",
        "difference_title": "Difference",
        "financial_statements_title": "Financial statements",
        "ic_display_name": "IC eliminations",
    }


@router.post("/run/databook/recon-core")
def run_databook_recon_core(req: DatabookReconCoreRequest):
    """Run PL then BS reconciliation and append both sheets to the session master."""
    from databook_workbook import BS_RECON_MAPPING_FILE

    from databook_helpers import (
        ensure_pl_mapping_file,
        prepare_master_pl_for_recon,
    )

    run_id = _make_run_id("db_recon_core_")
    run_d = _run_dir(req.session_id, run_id)
    output_folder = _resolve_output_folder(req.output_folder, req.session_id)
    master = _databook_master_path(
        req.session_id,
        output_folder,
        project_name=req.project_name,
        master_path=req.master_path,
    )
    if not master.is_file():
        raise HTTPException(status_code=404, detail=f"Master workbook not found: {master}")

    prepare_master_pl_for_recon(master)
    master_str = str(master)
    entity_order = list(req.entity_order)
    l4_sort_basis = str(req.l4_sort_basis or "latest_fy").strip().lower()
    titles = _default_recon_display_titles()
    pl_mapping = ensure_pl_mapping_file()
    recon_pl_target = master_str
    recon_bs_target = master_str

    pl_config = {
        "project_name": req.project_name,
        "company_name": req.company_name,
        "sort_by": "custom",
        "l4_sort_basis": l4_sort_basis,
        "entity_order": entity_order,
        "show_fs_check": True,
        "fs_check_values": {"entities": [], "consolidation": []},
        "display": {"titles": titles},
        "pl_config": {"display_label_map": {}},
        "paths": {
            "source_file": master_str,
            "source_sheet": "Master_PL",
            "source_engine": "openpyxl",
            "target_file": recon_pl_target,
            "report_sheet": "PL_Reconciliation",
            "audit_master_sheet": "Master_PL",
            "mapping_file": str(pl_mapping),
            "append_to_master": False,
            "master_file": master_str,
        },
    }
    pl_config_path = run_d / "recon_pl_config.json"
    pl_config_path.write_text(json.dumps(pl_config, ensure_ascii=False, indent=2), encoding="utf-8")
    pl_result = _run_script(SCRIPTS["recon_pl"], pl_config_path)
    if not pl_result.get("success"):
        pl_result["failed_step"] = "recon_pl"
        pl_result["master_path"] = master_str
        return pl_result

    bs_config = {
        "project_name": req.project_name,
        "company_name": req.company_name,
        "entity_order": entity_order,
        "l4_sort_basis": l4_sort_basis,
        "show_bs_checks": True,
        "display": {"titles": titles},
        "paths": {
            "source_file": master_str,
            "target_file": recon_bs_target,
            "master_file": master_str,
            "mapping_file": BS_RECON_MAPPING_FILE,
            "report_sheet": "BS_Reconciliation",
            "append_to_master": False,
        },
    }
    bs_config_path = run_d / "recon_bs_config.json"
    bs_config_path.write_text(json.dumps(bs_config, ensure_ascii=False, indent=2), encoding="utf-8")
    bs_result = _run_script(SCRIPTS["recon_bs"], bs_config_path)
    if not bs_result.get("success"):
        bs_result["failed_step"] = "recon_bs"
        bs_result["master_path"] = master_str
        return bs_result

    _reorder_databook_master(master_str)
    return {
        "success": True,
        "message": "PL and BS reconciliation tables added to the master databook.",
        "output_file": master_str,
        "output_filename": master.name,
        "master_path": master_str,
        "run_id": run_id,
        "session_id": req.session_id,
    }


@router.post("/run/databook/extended-tables")
def run_databook_extended_tables(req: DatabookExtendedTablesRequest):
    """Run BS bucket, Lead_IS, Lead_BS, Working capital, and Cashflow on session master."""
    from databook_workbook import BS_RECON_MAPPING_FILE, CF_NA_L3_ORDER_FILE, PL_RECON_MAPPING_FILE

    from databook_helpers import prepare_master_pl_for_recon

    run_id = _make_run_id("db_ext_tables_")
    run_d = _run_dir(req.session_id, run_id)
    output_folder = _resolve_output_folder(req.output_folder, req.session_id)
    master = _databook_master_path(
        req.session_id,
        output_folder,
        project_name=req.project_name,
        master_path=req.master_path,
    )
    if not master.is_file():
        raise HTTPException(status_code=404, detail=f"Master workbook not found: {master}")

    prepare_master_pl_for_recon(master)
    master_str = str(master)
    entity_order = list(req.entity_order)
    l4_sort_basis = str(req.l4_sort_basis or "latest_fy").strip().lower()
    shared_paths = {
        "mapping_source": "db",
        "master_file": master_str,
        "input_file": master_str,
        "mapping_file": BS_RECON_MAPPING_FILE,
        "pl_mapping_file": PL_RECON_MAPPING_FILE,
        "cf_na_l3_order_file": CF_NA_L3_ORDER_FILE,
    }
    base = {
        "project_name": req.project_name,
        "company_name": req.company_name,
        "entity_order": entity_order,
        "l4_sort_basis": l4_sort_basis,
        "paths": dict(shared_paths),
    }

    steps: list[tuple[str, dict]] = [
        ("bs_bucket", {**base, "paths": {**base["paths"], "input_file": master_str}}),
        ("lead_is", {**base, "paths": {**base["paths"], "input_file": master_str}}),
        (
            "lead_bs",
            {
                **base,
                "paths": {
                    **base["paths"],
                    "input_file": master_str,
                    "pl_master_file": master_str,
                },
            },
        ),
        (
            "working_capital",
            {**base, "paths": {**base["paths"], "input_file": master_str, "master_file": master_str}},
        ),
        ("cashflow", {**base, "paths": {**base["paths"], "input_file": master_str, "master_file": master_str}}),
    ]

    messages: list[str] = []
    for script_key, config in steps:
        script = SCRIPTS.get(script_key)
        if not script or not script.is_file():
            raise HTTPException(status_code=500, detail=f"Script not found: {script_key}")
        config_path = run_d / f"{script_key}_config.json"
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        result = _run_script(script, config_path)
        if not result.get("success"):
            result["failed_step"] = script_key
            result["master_path"] = master_str
            return result
        if result.get("message"):
            messages.append(str(result["message"]))

    _reorder_databook_master(master_str)
    return {
        "success": True,
        "message": "\n".join(messages) if messages else "Extended tables completed.",
        "output_file": master_str,
        "output_filename": master.name,
        "master_path": master_str,
        "run_id": run_id,
        "session_id": req.session_id,
    }


@router.post("/run/databook/recon-pipeline")
def run_databook_recon_pipeline(req: DatabookReconPipelineRequest):
    """Run PL/BS recon, BS bucket, Lead_IS, Lead_BS, Working_capital on session master."""
    from databook_helpers import prepare_master_pl_for_recon

    run_id = _make_run_id("db_recon_pipe_")
    run_d = _run_dir(req.session_id, run_id)
    output_folder = _resolve_output_folder(req.output_folder, req.session_id)
    master = _databook_master_path(
        req.session_id,
        output_folder,
        project_name=req.project_name,
        master_path=req.master_path,
    )
    if not master.is_file():
        raise HTTPException(status_code=404, detail=f"Master workbook not found: {master}")

    prepare_master_pl_for_recon(master)
    master_str = str(master)
    entity_order = list(req.entity_order)
    base = {
        "project_name": req.project_name,
        "company_name": req.company_name,
        "entity_order": entity_order,
        "paths": {"mapping_source": "db", "master_file": master_str},
    }

    recon_pl_target = master_str
    recon_bs_target = master_str

    steps: list[tuple[str, dict]] = [
        (
            "recon_pl",
            {
                **base,
                "sort_by": "custom",
                "show_fs_check": False,
                "fs_check_values": {"entities": [], "consolidation": []},
                "display": {"titles": {"ic_display_name": "IC eliminations"}},
                "pl_config": {"display_label_map": {}},
                "paths": {
                    **base["paths"],
                    "source_file": master_str,
                    "source_sheet": "Master_PL",
                    "source_engine": "openpyxl",
                    "target_file": recon_pl_target,
                    "master_file": master_str,
                    "report_sheet": "PL_Reconciliation",
                    "audit_master_sheet": "Master_PL",
                    "append_to_master": False,
                },
            },
        ),
        (
            "recon_bs",
            {
                **base,
                "display": {"titles": {"ic_display_name": "IC eliminations"}},
                "paths": {
                    **base["paths"],
                    "source_file": master_str,
                    "target_file": recon_bs_target,
                    "master_file": master_str,
                    "report_sheet": "BS_Reconciliation",
                    "append_to_master": False,
                },
            },
        ),
        (
            "bs_bucket",
            {**base, "paths": {**base["paths"], "input_file": master_str}},
        ),
        (
            "lead_is",
            {**base, "paths": {**base["paths"], "input_file": master_str}},
        ),
        (
            "lead_bs",
            {
                **base,
                "paths": {
                    **base["paths"],
                    "input_file": master_str,
                    "pl_master_file": master_str,
                },
            },
        ),
        (
            "working_capital",
            {**base, "paths": {**base["paths"], "input_file": master_str, "master_file": master_str}},
        ),
    ]

    messages: list[str] = []
    for script_key, config in steps:
        script = SCRIPTS.get(script_key)
        if not script or not script.is_file():
            raise HTTPException(status_code=500, detail=f"Script not found: {script_key}")
        config_path = run_d / f"{script_key}_config.json"
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        result = _run_script(script, config_path)
        if not result.get("success"):
            result["failed_step"] = script_key
            result["master_path"] = master_str
            return result
        if result.get("message"):
            messages.append(str(result["message"]))

    _reorder_databook_master(master_str)
    return {
        "success": True,
        "message": "\n".join(messages) if messages else "Recon pipeline completed.",
        "output_file": master_str,
        "output_filename": master.name,
        "master_path": master_str,
        "run_id": run_id,
        "session_id": req.session_id,
    }


# ─── Templates ───────────────────────────────────────────────────────────────


@router.get("/templates/{name}")
def download_template(
    name: str,
    session_id: str = Query(""),
    output_folder: str = Query(""),
    period_level: str = Query("yearly"),
    first_fy: Optional[int] = Query(None),
    ltm_month: str = Query(""),
    fy_end_month: Optional[int] = Query(None),
    fy_end_day: Optional[int] = Query(None),
    fiscal_start_month: Optional[int] = Query(None),
    project_name: str = Query("Project"),
):
    """
    Serve an XLSX template for the given name.
    For consolidation: prefer session-generated template; fallback to on-the-fly build.
    """
    if name == "consolidation" and session_id:
        from databook_helpers import (
            build_consolidation_template_from_config,
            consolidation_template_path,
        )

        resolved_folder = _resolve_output_folder(output_folder or None, session_id)
        session_template = consolidation_template_path(session_id, resolved_folder)
        if session_template.is_file():
            return FileResponse(
                path=str(session_template),
                filename="consolidation_template.xlsx",
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        master = _databook_master_path(session_id, resolved_folder, project_name=project_name)
        if master.is_file():
            master = master.resolve()
        level = str(period_level or "yearly").strip().lower()
        if level not in ("yearly", "monthly"):
            level = "yearly"
        fy_end_m = int(fy_end_month or 12)
        fiscal_start = int(fiscal_start_month or ((fy_end_m % 12) + 1))
        fallback_config: dict[str, Any] = {
            "session_id": session_id,
            "output_folder": resolved_folder,
            "master_path": str(master) if master.is_file() else "",
            "period_level": level,
            "output_path": str(session_template),
            "first_fy": first_fy,
            "ltm_month": ltm_month or None,
            "fy_end_month": fy_end_m,
            "fy_end_day": int(fy_end_day or 31),
            "fiscal_start_month": fiscal_start,
        }
        try:
            template_file = build_consolidation_template_from_config(fallback_config)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(
            path=str(template_file),
            filename="consolidation_template.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    if name == "adjustments" and session_id:
        from databook_helpers import (
            adjustments_template_path,
            build_adjustments_template_from_config,
        )

        resolved_folder = _resolve_output_folder(output_folder or None, session_id)
        session_template = adjustments_template_path(session_id, resolved_folder)
        if session_template.is_file():
            return FileResponse(
                path=str(session_template),
                filename="adjustments_template.xlsx",
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        master = _databook_master_path(session_id, resolved_folder, project_name=project_name)
        if master.is_file():
            master = master.resolve()
        fy_end_m = int(fy_end_month or 12)
        fiscal_start = int(fiscal_start_month or ((fy_end_m % 12) + 1))
        fallback_config: dict[str, Any] = {
            "session_id": session_id,
            "output_folder": resolved_folder,
            "master_path": str(master) if master.is_file() else "",
            "output_path": str(session_template),
            "first_fy": first_fy,
            "ltm_month": ltm_month or None,
            "fy_end_month": fy_end_m,
            "fy_end_day": int(fy_end_day or 31),
            "fiscal_start_month": fiscal_start,
        }
        try:
            template_file = build_adjustments_template_from_config(fallback_config)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(
            path=str(template_file),
            filename="adjustments_template.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    templates_dir = Path(__file__).parent.parent / "templates" / "fdd"
    template_file = templates_dir / f"{name}.xlsx"

    if not template_file.exists():
        raise HTTPException(status_code=404, detail=f"Template '{name}' not found")

    return FileResponse(
        path=str(template_file),
        filename=f"{name}_template.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
