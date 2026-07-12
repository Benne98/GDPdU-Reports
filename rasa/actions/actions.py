"""
Rasa custom actions for the FDD Bot.

Each action corresponds to one step in the conversation flow.
Actions call the FastAPI backend (fdd_bot router) for all I/O operations.
"""

from __future__ import annotations

import calendar
import json
import uuid
from datetime import date
from typing import Any, Optional

import requests
from rasa_sdk import Action, Tracker
from rasa_sdk.events import SlotSet, AllSlotsReset, FollowupAction
from rasa_sdk.executor import CollectingDispatcher

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_BACKEND_DIR = os.path.abspath(os.path.join(_REPO_ROOT, "backend"))

for _p in (_REPO_ROOT, _BACKEND_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from databook_periods import (  # noqa: E402
    as_of_is_fy_end,
    compute_databook_grid_labels,
    current_fy_end_year_containing_as_of,
    is_ytd_grid_label,
    ytd_reporting_fy_end_year,
)
from urllib.parse import quote

from config import FASTAPI_BASE_URL, FILTER_OPERATORS, MONTH_NAMES, UPLOAD_BASE_DIR

# Must exceed backend subprocess timeout (fdd_bot._run_script); GST with formulas often >2 min
FDD_SCRIPT_RUN_TIMEOUT_SEC = 900

_BS_SETTING_KEYS: tuple[str, ...] = (
    "project_name",
    "group_name",
    "file_id",
    "sheet_name",
    "apply_fx",
    "fx_col",
    "bs_period_mode",
    "bs_calc_mode",
    "bs_invoice_col",
    "bs_start_col",
    "bs_end_col",
    "bs_profit_mode",
    "bs_revenue_col",
    "bs_cogs_col",
    "bs_profit_col",
    "bs_group_col_1",
    "bs_group_col_2",
    "bs_max_items_parent",
    "bs_max_items_child",
    "bs_table_name",
    "bs_subtitle_suffix",
    "bs_other_bucket_parent",
    "bs_other_bucket_child",
    "bs_other_bucket_label",
    "bs_y_unit",
)

_SESSION_BS_SETTINGS: dict[str, dict[str, Any]] = {}


def _remember_bs_settings(tracker: Tracker, payload: dict | None) -> None:
    if not isinstance(payload, dict):
        return
    key = _sender_cache_key(tracker)
    if not key:
        return
    snap = dict(_SESSION_BS_SETTINGS.get(key, {}))
    for field in _BS_SETTING_KEYS:
        if field not in payload:
            continue
        val = payload[field]
        if val is None or val == "":
            continue
        snap[field] = val
    _SESSION_BS_SETTINGS[key] = snap


def _bs_settings_from_sources(tracker: Tracker, payload: dict | None = None) -> dict[str, Any]:
    out: dict[str, Any] = dict(_SESSION_BS_SETTINGS.get(_sender_cache_key(tracker) or "", {}))
    if isinstance(payload, dict):
        for field in _BS_SETTING_KEYS:
            if field in payload and payload[field] not in (None, ""):
                out[field] = payload[field]
    for field in _BS_SETTING_KEYS:
        if field in out and out[field] not in (None, ""):
            continue
        val = tracker.get_slot(field)
        if val is not None and val != "":
            out[field] = val
    return out


def _expand_output_folder(raw: str) -> str:
    """Match backend _resolve_output_folder for bare names like Desktop."""
    from pathlib import Path

    text = str(raw or "").strip()
    if not text:
        return ""
    p = Path(text).expanduser()
    if p.is_absolute():
        return str(p.resolve())
    if text.lower() in ("desktop", "documents", "downloads"):
        return str((Path.home() / text).resolve())
    return text


def _resolve_output_download_file(result: dict, tracker: Tracker | None = None) -> tuple[str, str] | None:
    """Resolve an on-disk workbook path for the chat download card."""
    candidates: list[str] = []
    for key in ("output_file", "master_path"):
        raw = result.get(key)
        if raw and os.path.isfile(str(raw)):
            candidates.append(os.path.abspath(str(raw)))

    output_path = result.get("output_path")
    if output_path and os.path.isfile(str(output_path)):
        candidates.append(os.path.abspath(str(output_path)))

    session_id = _fdd_session_id(tracker) if tracker else str(result.get("session_id") or "").strip()
    folders: list[str] = []
    if output_path:
        expanded = _expand_output_folder(str(output_path))
        if expanded and os.path.isdir(expanded):
            folders.append(expanded)
    if tracker:
        slot_folder = str(tracker.get_slot("output_folder") or "").strip()
        if slot_folder:
            expanded_slot = _expand_output_folder(slot_folder)
            if expanded_slot:
                folders.append(expanded_slot)

    if session_id:
        from databook_paths import master_workbook_filename

        project_master = master_workbook_filename(
            str(result.get("project_name") or (tracker.get_slot("project_name") if tracker else "") or "Project")
        )
        for folder in folders:
            for name in (
                project_master,
                f"{session_id}_SuSa_Master.xlsx",
                f"{session_id}_Output.xlsx",
            ):
                path = os.path.join(folder, name)
                if os.path.isfile(path):
                    candidates.append(os.path.abspath(path))
            try:
                from pathlib import Path

                for pattern in ("*_Master.xlsx", "*_Workbook.xlsx"):
                    for p in sorted(
                        Path(folder).glob(pattern),
                        key=lambda item: item.stat().st_mtime,
                        reverse=True,
                    ):
                        candidates.append(str(p.resolve()))
            except OSError:
                pass

    if not candidates:
        return None
    path = candidates[0]
    fname = str(result.get("output_filename") or os.path.basename(path))
    return path, fname


def _utter_output_file_attachment(
    dispatcher: CollectingDispatcher,
    result: dict,
    *,
    title: str = "Output Excel",
    tracker: Tracker | None = None,
) -> bool:
    """Send a download/drag card when the analytical script produced an output file."""
    resolved = _resolve_output_download_file(result, tracker)
    if not resolved:
        return False
    output_file, fname = resolved
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "file_attachment",
            "title": title,
            "filename": fname,
            "download_url": f"/api/v1/fdd/download?path={quote(output_file)}",
            "inputs": [],
        }
    )
    return True


def _build_next_action_card(subtitle: str) -> dict:
    return {
        "type": "adaptive_card",
        "card": "next_action",
        "title": "Output Complete",
        "subtitle": subtitle,
        "inputs": [
            {
                "id": "next_action",
                "type": "radio",
                "label": "What would you like to do next?",
                "options": [
                    {"label": "Create another output", "value": "create_another"},
                    {"label": "Change settings", "value": "change_settings"},
                    {"label": "Restart the full guided conversation", "value": "restart"},
                    {"label": "Change only the filters", "value": "change_filters"},
                ],
            }
        ],
        "submit_label": "Continue",
    }


def _start_async_revenue_script(
    dispatcher: CollectingDispatcher,
    body: dict,
    script_key: str,
    script_label: str,
) -> dict:
    """Start a revenue script asynchronously; frontend polls and shows download."""
    result = _fdd_post(f"/api/v1/fdd/run/{script_key}/async", body, timeout=30)
    if result.get("run_id"):
        dispatcher.utter_message(text=f"Starting {script_label}…")
        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "script_job",
                "title": script_label,
                "run_id": result["run_id"],
                "session_id": result.get("session_id") or body.get("session_id"),
                "script_key": script_key,
                "inputs": [],
            }
        )
    else:
        dispatcher.utter_message(
            text=f"Could not start {script_label}:\n{result.get('message', 'Unknown error')}"
        )
    return result

# ─── Date / FY helpers (LTM month → as-of; FY end = month + day, year-independent in UI) ─


def _parse_ltm_month_value(ltm: Any) -> tuple[int, int] | None:
    """Parse 'YYYY-MM' → (year, month); None if invalid."""
    if ltm in (None, ""):
        return None
    parts = str(ltm).strip().split("-")
    try:
        y = int(parts[0])
        m = int(parts[1])
        if 1 <= m <= 12:
            return y, m
    except (ValueError, IndexError):
        pass
    return None


def _as_of_year_month_from_ltm(ltm: Any) -> tuple[int, int]:
    """Parse slot `ltm_month` like '2026-03' → (year, month) for as-of / current_* config."""
    parsed = _parse_ltm_month_value(ltm)
    if parsed:
        return parsed
    td = date.today()
    return td.year, td.month


def _ltm_month_from_tracker(tracker: Tracker) -> str | None:
    for key in ("ltm_month", "ytd_month"):
        raw = tracker.get_slot(key)
        if raw not in (None, ""):
            return str(raw).strip()
    cached = _cached_session_ltm_month(tracker)
    if cached:
        return cached
    return None


def _ltm_month_from_sources(tracker: Tracker, payload: dict | None = None) -> str:
    """LTM as-of month from card payload (review re-submit), tracker slots, or session cache."""
    if isinstance(payload, dict):
        raw = payload.get("ltm_month")
        if raw not in (None, ""):
            return str(raw).strip()
    found = _ltm_month_from_tracker(tracker)
    return found or ""


def _slot_bool(val: Any, *, default: bool = False) -> bool:
    """Rasa bool slots may be unset (None) — treat None as default, not False."""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes", "on")
    return bool(val)


def _top_other_bucket_choice(tracker: Tracker, payload: dict | None = None) -> str:
    """Return 'include' or 'disable' for the Other bucket (review + run)."""
    if payload is not None:
        raw = payload.get("top_other_bucket")
        if raw in ("include", "disable"):
            return str(raw)
    raw_slot = tracker.get_slot("top_other_bucket")
    if raw_slot in ("include", "disable"):
        return str(raw_slot)
    if _slot_bool(tracker.get_slot("top_other_bucket_enabled"), default=True):
        return "include"
    return "disable"


def _remember_session_fy_end(tracker: Tracker, month: int, day: int) -> None:
    key = _sender_cache_key(tracker)
    if not key:
        return
    m = max(1, min(12, int(month)))
    d = max(1, min(31, int(day)))
    _SESSION_FY_END[key] = (m, d)


def _cached_session_fy_end(tracker: Tracker) -> tuple[int, int] | None:
    key = _sender_cache_key(tracker)
    if not key:
        return None
    return _SESSION_FY_END.get(key)


def _fy_end_month_day_from_tracker(payload: dict, tracker: Tracker) -> tuple[int, int]:
    """Fiscal year-end month (1–12) and day (1–31) from payload, slots, or session cache."""
    raw_m = payload.get("fy_end_month", tracker.get_slot("fy_end_month"))
    raw_d = payload.get("fy_end_day", tracker.get_slot("fy_end_day"))
    cached = _cached_session_fy_end(tracker)
    if raw_m in (None, "") and cached is not None:
        raw_m = cached[0]
    if raw_d in (None, "") and cached is not None:
        raw_d = cached[1]
    try:
        m = int(float(raw_m)) if raw_m not in (None, "") else 12
    except (ValueError, TypeError):
        m = 12
    try:
        d = int(float(raw_d)) if raw_d not in (None, "") else 31
    except (ValueError, TypeError):
        d = 31
    m = max(1, min(12, m))
    d = max(1, min(31, d))
    return m, d


def _fdd_date_params_from_tracker(tracker: Tracker) -> dict[str, int | str]:
    """Unified current_* / fiscal_year_end_* / as_of_* for GST, TOP, PVM."""
    ltm = _ltm_month_from_tracker(tracker)
    cy, cm = _as_of_year_month_from_ltm(ltm)
    fem, fed = _fy_end_month_day_from_tracker({}, tracker)
    return {
        "current_year": cy,
        "current_month": cm,
        "fiscal_year_end_month": fem,
        "fiscal_year_end_day": fed,
        "as_of_year": cy,
        "as_of_month": cm,
        "ltm_month": ltm or "",
    }


def _tracker_first_fy_int(tracker: Tracker, default: int = 2020) -> int:
    """First FY: Date-Settings-Slot `first_fy`, sonst bereits gesetzte Overrides."""
    for key in ("first_fy", "gst_first_fy_override", "pvm_first_fy_override"):
        raw = tracker.get_slot(key)
        if raw is None or raw == "":
            continue
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            continue
    cached = _cached_session_first_fy_int(tracker)
    if cached is not None:
        return cached
    return default


_SESSION_FIRST_FY: dict[str, float] = {}
_SESSION_LTM_MONTH: dict[str, str] = {}
_SESSION_FY_END: dict[str, tuple[int, int]] = {}


def _fdd_session_id(tracker: Tracker) -> str:
    """Upload folder key — must match frontend `fdd_bot_sender_id` / REST sender_id."""
    return str(getattr(tracker, "sender_id", "") or tracker.get_slot("session_id") or "").strip()


def _sender_cache_key(tracker: Tracker) -> str:
    return _fdd_session_id(tracker)


def _remember_session_first_fy(tracker: Tracker, value: float | int | None) -> None:
    if value is None:
        return
    key = _sender_cache_key(tracker)
    if not key:
        return
    try:
        _SESSION_FIRST_FY[key] = float(value)
    except (TypeError, ValueError):
        return


def _cached_session_first_fy_int(tracker: Tracker) -> int | None:
    key = _sender_cache_key(tracker)
    if not key:
        return None
    raw = _SESSION_FIRST_FY.get(key)
    if raw is None:
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _remember_session_ltm_month(tracker: Tracker, value: str | None) -> None:
    if not value:
        return
    key = _sender_cache_key(tracker)
    if not key:
        return
    ltm = str(value).strip()
    if ltm:
        _SESSION_LTM_MONTH[key] = ltm


def _cached_session_ltm_month(tracker: Tracker) -> str | None:
    key = _sender_cache_key(tracker)
    if not key:
        return None
    raw = _SESSION_LTM_MONTH.get(key)
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


_SESSION_HEADERS: dict[str, list[str]] = {}
_SESSION_COLUMN_ROLES: dict[str, dict[str, str]] = {}
_SESSION_CHURN_FLOW: dict[str, dict[str, Any]] = {}

_SESSION_DB_LAYOUT: dict[str, str] = {}
_SESSION_DB_ENTITY_YEAR_FILES: dict[str, list[dict[str, Any]]] = {}
_SESSION_DB_ENTITY_NAMES: dict[str, list[str]] = {}
_SESSION_OPOS_STATE: dict[str, dict[str, Any]] = {}
_SESSION_FA_STATE: dict[str, dict[str, Any]] = {}
_SESSION_FTE_STATE: dict[str, dict[str, Any]] = {}


def _bot_flow_state_path(session_key: str) -> "Path":
    from pathlib import Path

    return Path(UPLOAD_BASE_DIR) / session_key / "fdd_bot_flow_state.json"


def _load_bot_flow_state_file(session_key: str) -> dict[str, Any]:
    if not session_key:
        return {}
    path = _bot_flow_state_path(session_key)
    if not path.is_file():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _persist_bot_flow_state(session_key: str, data: dict[str, Any]) -> None:
    if not session_key:
        return
    path = _bot_flow_state_path(session_key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _sync_flow_state_to_disk(tracker: Tracker) -> None:
    key = _sender_cache_key(tracker)
    if not key:
        return
    data = _load_bot_flow_state_file(key)
    if key in _SESSION_OPOS_STATE:
        prev = data.get("opos") if isinstance(data.get("opos"), dict) else {}
        merged = {**prev, **_SESSION_OPOS_STATE[key]}
        _SESSION_OPOS_STATE[key] = merged
        data["opos"] = merged
    if key in _SESSION_FA_STATE:
        prev = data.get("fa") if isinstance(data.get("fa"), dict) else {}
        merged = {**prev, **_SESSION_FA_STATE[key]}
        _SESSION_FA_STATE[key] = merged
        data["fa"] = merged
    if key in _SESSION_FTE_STATE:
        prev = data.get("fte") if isinstance(data.get("fte"), dict) else {}
        merged = {**prev, **_SESSION_FTE_STATE[key]}
        _SESSION_FTE_STATE[key] = merged
        data["fte"] = merged
    _persist_bot_flow_state(key, data)


def _opos_state(tracker: Tracker) -> dict[str, Any]:
    key = _sender_cache_key(tracker)
    if not key:
        return {}
    if key not in _SESSION_OPOS_STATE:
        cached = _load_bot_flow_state_file(key).get("opos")
        _SESSION_OPOS_STATE[key] = dict(cached) if isinstance(cached, dict) else {}
    return _SESSION_OPOS_STATE.setdefault(key, {})


def _fa_state(tracker: Tracker) -> dict[str, Any]:
    key = _sender_cache_key(tracker)
    if not key:
        return {}
    if key not in _SESSION_FA_STATE:
        cached = _load_bot_flow_state_file(key).get("fa")
        _SESSION_FA_STATE[key] = dict(cached) if isinstance(cached, dict) else {}
    return _SESSION_FA_STATE.setdefault(key, {})


def _fte_state(tracker: Tracker) -> dict[str, Any]:
    key = _sender_cache_key(tracker)
    if not key:
        return {}
    if key not in _SESSION_FTE_STATE:
        cached = _load_bot_flow_state_file(key).get("fte")
        _SESSION_FTE_STATE[key] = dict(cached) if isinstance(cached, dict) else {}
    return _SESSION_FTE_STATE.setdefault(key, {})


def _opos_is_dual_snapshot_format(raw: list) -> bool:
    return bool(raw) and isinstance(raw[0], dict) and "debitor" in raw[0] and "kreditor" in raw[0]


def _opos_side_snapshots_from_stored(raw: list, side: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        as_of = str(item.get("as_of") or "").strip()
        if not as_of:
            continue
        if _opos_is_dual_snapshot_format(raw):
            block = item.get(side) if isinstance(item.get(side), dict) else {}
            out.append(
                {
                    "as_of": as_of,
                    "file_id": str(block.get("file_id") or "").strip(),
                    "file_path": str(block.get("file_path") or "").strip(),
                    "sheet_name": str(block.get("sheet_name") or "").strip(),
                }
            )
        else:
            out.append(
                {
                    "as_of": as_of,
                    "file_id": str(item.get("file_id") or "").strip(),
                    "file_path": str(item.get("file_path") or "").strip(),
                    "sheet_name": str(item.get("sheet_name") or "").strip(),
                }
            )
    return out


def _opos_snapshots_from_sources(tracker: Tracker) -> list[dict[str, Any]]:
    cached = _opos_state(tracker).get("snapshots")
    if isinstance(cached, list) and cached:
        return [r for r in cached if isinstance(r, dict)]
    raw = tracker.get_slot("opos_snapshots_json")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [r for r in parsed if isinstance(r, dict)]
        except json.JSONDecodeError:
            pass
    return []


def _fa_periods_from_sources(tracker: Tracker) -> list[dict[str, Any]]:
    cached = _fa_state(tracker).get("periods")
    if isinstance(cached, list) and cached:
        return [r for r in cached if isinstance(r, dict)]
    raw = tracker.get_slot("fa_periods_json")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [r for r in parsed if isinstance(r, dict)]
        except json.JSONDecodeError:
            pass
    return []


def _fte_periods_from_sources(tracker: Tracker) -> list[dict[str, Any]]:
    cached = _fte_state(tracker).get("periods")
    if isinstance(cached, list) and cached:
        return [r for r in cached if isinstance(r, dict)]
    raw = tracker.get_slot("fte_periods_json")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [r for r in parsed if isinstance(r, dict)]
        except json.JSONDecodeError:
            pass
    return []


def _opos_columns_from_sources(tracker: Tracker, payload: dict | None = None) -> dict[str, str]:
    st = _opos_state(tracker)
    out: dict[str, str] = {}
    cached = st.get("columns")
    if isinstance(cached, dict):
        for k, v in cached.items():
            text = str(v or "").strip()
            if text:
                out[str(k)] = text
    p = payload or {}
    for key, slot in (
        ("partner_id", "opos_partner_id_col"),
        ("partner_name", "opos_partner_name_col"),
        ("amount", "opos_amount_col"),
        ("due_date", "opos_due_date_col"),
    ):
        raw = p.get(slot)
        if raw in (None, ""):
            raw = tracker.get_slot(slot)
        text = str(raw or "").strip()
        if text:
            out[key] = text
    pid = str(p.get("opos_partner_col") or p.get("opos_partner_id_col") or tracker.get_slot("opos_partner_id_col") or "").strip()
    if pid and "partner_id" not in out:
        out["partner_id"] = pid
    if pid and "partner_name" not in out:
        out["partner_name"] = str(p.get("opos_partner_name_col") or tracker.get_slot("opos_partner_name_col") or pid).strip() or pid
    return out


def _fa_dep_cols_from_sources(tracker: Tracker, payload: dict | None = None) -> list[str]:
    st = _fa_state(tracker)
    cached = st.get("depreciation_cols")
    if isinstance(cached, list) and cached:
        return [str(c).strip() for c in cached if str(c).strip()]
    p = payload or {}
    raw_dep = p.get("fa_depreciation_cols_json", st.get("depreciation_cols_json") or tracker.get_slot("fa_depreciation_cols_json") or "[]")
    if isinstance(raw_dep, list):
        return [str(c).strip() for c in raw_dep if str(c).strip()]
    try:
        parsed = json.loads(str(raw_dep or "[]"))
    except (json.JSONDecodeError, TypeError):
        parsed = []
    return [str(c).strip() for c in parsed if str(c).strip()] if isinstance(parsed, list) else []


def _fa_columns_from_sources(tracker: Tracker, payload: dict | None = None) -> dict[str, Any]:
    st = _fa_state(tracker)
    out: dict[str, Any] = {}
    cached = st.get("columns")
    if isinstance(cached, dict):
        out.update(cached)
    p = payload or {}
    for slot, key in (
        ("fa_opening_col", "opening"),
        ("fa_additions_col", "additions"),
        ("fa_disposals_col", "disposals"),
    ):
        raw = p.get(slot)
        if raw in (None, ""):
            raw = tracker.get_slot(slot)
        text = str(raw or "").strip()
        if text:
            out[key] = text
    dep_cols = _fa_dep_cols_from_sources(tracker, p)
    if dep_cols:
        out["depreciation_cols"] = dep_cols
    return out


def _fte_payroll_cols_from_sources(tracker: Tracker, payload: dict | None = None) -> list[str]:
    st = _fte_state(tracker)
    cached = st.get("columns")
    if isinstance(cached, dict):
        pay = cached.get("payroll_cols")
        if isinstance(pay, list) and pay:
            return [str(c).strip() for c in pay if str(c).strip()]
    p = payload or {}
    raw = p.get("fte_payroll_cols_json", tracker.get_slot("fte_payroll_cols_json") or "[]")
    if isinstance(raw, list):
        return [str(c).strip() for c in raw if str(c).strip()]
    try:
        parsed = json.loads(str(raw or "[]"))
    except (json.JSONDecodeError, TypeError):
        parsed = []
    return [str(c).strip() for c in parsed if str(c).strip()] if isinstance(parsed, list) else []


def _fte_columns_from_sources(tracker: Tracker, payload: dict | None = None) -> dict[str, Any]:
    st = _fte_state(tracker)
    out: dict[str, Any] = {}
    cached = st.get("columns")
    if isinstance(cached, dict):
        out.update(cached)
    p = payload or {}
    for slot, key in (
        ("fte_employment_col", "employment"),
        ("fte_months_col", "months_sum"),
    ):
        raw = p.get(slot)
        if raw in (None, ""):
            raw = tracker.get_slot(slot)
        text = str(raw or "").strip()
        if text:
            out[key] = text
    pay_cols = _fte_payroll_cols_from_sources(tracker, p)
    if pay_cols:
        out["payroll_cols"] = pay_cols
    return out


def _remember_databook_layout(tracker: Tracker, layout: Any) -> None:
    key = _sender_cache_key(tracker)
    if not key:
        return
    s = str(layout or "").strip()
    if s:
        _SESSION_DB_LAYOUT[key] = s


def _databook_layout_from_sources(tracker: Tracker, payload: dict | None = None) -> str:
    if isinstance(payload, dict):
        raw = str(payload.get("db_susa_layout_format") or "").strip()
        if raw:
            _remember_databook_layout(tracker, raw)
            return raw
    slot = str(tracker.get_slot("db_susa_layout_format") or "").strip()
    if slot:
        _remember_databook_layout(tracker, slot)
        return slot
    cached = _SESSION_DB_LAYOUT.get(_sender_cache_key(tracker) or "", "")
    return str(cached or "").strip()


def _remember_databook_entity_uploads(
    tracker: Tracker,
    entity_year_files: list[dict[str, Any]] | None,
    entity_names: list[Any] | None,
) -> None:
    key = _sender_cache_key(tracker)
    if not key:
        return
    if isinstance(entity_year_files, list) and entity_year_files:
        cleaned = [r for r in entity_year_files if isinstance(r, dict) and r.get("file_ids")]
        if cleaned:
            _SESSION_DB_ENTITY_YEAR_FILES[key] = cleaned
    if isinstance(entity_names, list) and entity_names:
        names = [str(x) for x in entity_names if x not in (None, "")]
        if names:
            _SESSION_DB_ENTITY_NAMES[key] = names


def _normalize_header_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        s = str(item).strip() if item is not None else ""
        if s:
            out.append(s)
    return out


def _remember_session_headers(tracker: Tracker, headers: Any) -> None:
    normalized = _normalize_header_list(headers)
    if not normalized:
        return
    key = _sender_cache_key(tracker)
    if not key:
        return
    _SESSION_HEADERS[key] = normalized


def _cached_session_headers(tracker: Tracker) -> list[str]:
    key = _sender_cache_key(tracker)
    if not key:
        return []
    return list(_SESSION_HEADERS.get(key) or [])


def _headers_from_sources(tracker: Tracker, payload: dict | None = None) -> list[str]:
    """Column headers from card payload, tracker slot, session cache, or API re-fetch."""
    if isinstance(payload, dict):
        from_payload = _normalize_header_list(payload.get("headers"))
        if from_payload:
            return from_payload
    slot_raw = tracker.get_slot("headers")
    from_slot = _normalize_header_list(slot_raw)
    if from_slot:
        return from_slot
    cached = _cached_session_headers(tracker)
    if cached:
        return cached
    file_id = str(tracker.get_slot("file_id") or "").strip()
    if not file_id and isinstance(payload, dict):
        file_id = str(payload.get("file_id") or "").strip()
    sheet_name = str(tracker.get_slot("sheet_name") or "").strip()
    if isinstance(payload, dict) and payload.get("sheet_name"):
        sheet_name = str(payload.get("sheet_name") or "").strip()
    if not file_id:
        return []
    return _headers_for_period_file(tracker, file_id, sheet_name)


def _headers_for_period_file(
    tracker: Tracker,
    file_id: str,
    sheet_name: str = "",
) -> list[str]:
    """Fetch column headers for a specific uploaded file (not session cache)."""
    fid = str(file_id or "").strip()
    if not fid:
        return []
    session_id = str(_fdd_session_id(tracker) or "").strip()
    params: dict[str, str] = {"session_id": session_id}
    if sheet_name:
        params["sheet_name"] = sheet_name
    result = _fdd_get(f"/api/v1/fdd/headers/{fid}", params)
    return _normalize_header_list(result.get("headers"))


def _normalize_header_key(name: str) -> str:
    import re

    s = str(name).replace("\u00a0", " ").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _headers_intersection_for_periods(
    tracker: Tracker,
    periods: list[dict[str, Any]],
) -> list[str]:
    """Column headers present in every period file (preserving order from first file)."""
    if not periods:
        return []
    per_file: list[list[str]] = []
    for period in periods:
        fid = str(period.get("file_id") or "").strip()
        if not fid:
            continue
        sheet = str(period.get("sheet_name") or "").strip()
        headers = _headers_for_period_file(tracker, fid, sheet)
        if headers:
            per_file.append(headers)
    if not per_file:
        return []
    common = {_normalize_header_key(h) for h in per_file[0]}
    for hdrs in per_file[1:]:
        common &= {_normalize_header_key(h) for h in hdrs}
    return [h for h in per_file[0] if _normalize_header_key(h) in common]


def _headers_union_for_periods(
    tracker: Tracker,
    periods: list[dict[str, Any]],
) -> list[str]:
    """All column headers across period files (first-seen order)."""
    if not periods:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for period in periods:
        fid = str(period.get("file_id") or "").strip()
        if not fid:
            continue
        sheet = str(period.get("sheet_name") or "").strip()
        for header in _headers_for_period_file(tracker, fid, sheet):
            key = _normalize_header_key(header)
            if key and key not in seen:
                seen.add(key)
                out.append(header)
    return out


def _resolve_header_names(headers: list[str], names: list[str]) -> list[str] | None:
    """Map config labels to header strings (nbsp/case tolerant, like resolve_column_names_to_df)."""
    col_map = {
        _normalize_header_key(str(h)): str(h).replace("\u00a0", " ").strip() for h in headers
    }
    resolved: list[str] = []
    for raw in names:
        key = _normalize_header_key(str(raw).replace("\u00a0", " "))
        if key not in col_map:
            return None
        resolved.append(col_map[key])
    return resolved


def _validate_group_cols_in_periods(
    tracker: Tracker,
    periods: list[dict[str, Any]],
    group_cols: list[str],
) -> str | None:
    """Return an error message if any grouping column is missing from a period file."""
    if not group_cols:
        return None
    for period in periods:
        fid = str(period.get("file_id") or "").strip()
        sheet = str(period.get("sheet_name") or "").strip()
        label = str(period.get("label") or fid or "period").strip()
        if not fid:
            continue
        headers = _headers_for_period_file(tracker, fid, sheet)
        if _resolve_header_names(headers, group_cols) is None:
            missing = [
                col
                for col in group_cols
                if _resolve_header_names(headers, [col]) is None
            ]
            col_label = missing[0] if missing else group_cols[0]
            return (
                f"Column '{col_label}' was not found in the register file for {label}. "
                "Please choose grouping columns that exist in all uploaded period files."
            )
    return None


def _slot_int_first_hit(tracker: Tracker, keys: tuple[str, ...], default: int = 2020) -> int:
    for key in keys:
        raw = tracker.get_slot(key)
        if raw is None or raw == "":
            continue
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            continue
    cached = _cached_session_first_fy_int(tracker)
    if cached is not None:
        return cached
    return default


def _first_fy_for_pvm_labels_card(tracker: Tracker, default: int = 2020) -> int:
    """Bild 2 (PVM labels): prefer Date Settings `first_fy`, then PVM, then GST."""
    return _slot_int_first_hit(tracker, ("first_fy", "pvm_first_fy_override", "gst_first_fy_override"), default)


def _first_fy_for_pvm_review_card(tracker: Tracker, default: int = 2020) -> int:
    """Bild 3 (PVM review): prefer `pvm_first_fy_override` from Bild 2, then `first_fy`."""
    return _slot_int_first_hit(tracker, ("pvm_first_fy_override", "first_fy", "gst_first_fy_override"), default)


def _first_fy_for_top_labels_card(tracker: Tracker, default: int = 2020) -> int:
    return _slot_int_first_hit(
        tracker,
        ("first_fy", "top_first_fy_override", "gst_first_fy_override", "pvm_first_fy_override"),
        default,
    )


def _first_fy_for_top_review_card(tracker: Tracker, default: int = 2020) -> int:
    return _slot_int_first_hit(
        tracker,
        ("top_first_fy_override", "first_fy", "gst_first_fy_override", "pvm_first_fy_override"),
        default,
    )


def _first_fy_for_churn_labels_card(tracker: Tracker, default: int = 2020) -> int:
    return _slot_int_first_hit(
        tracker,
        ("first_fy", "churn_first_fy_override", "top_first_fy_override", "gst_first_fy_override"),
        default,
    )


def _fy_end_month_dropdown_opts() -> list[dict[str, str]]:
    return [{"label": name, "value": str(i + 1)} for i, name in enumerate(MONTH_NAMES)]


def _remember_churn_flow(tracker: Tracker, updates: dict[str, Any]) -> None:
    key = _sender_cache_key(tracker)
    if not key:
        return
    bucket = _SESSION_CHURN_FLOW.setdefault(key, {})
    for k, v in updates.items():
        if v is None:
            continue
        bucket[k] = v


def _churn_flow(tracker: Tracker) -> dict[str, Any]:
    key = _sender_cache_key(tracker)
    if not key:
        return {}
    return dict(_SESSION_CHURN_FLOW.get(key, {}))


def _churn_flow_val(tracker: Tracker, key: str, default: Any = "") -> Any:
    raw = tracker.get_slot(key)
    if raw not in (None, ""):
        return raw
    cached = _churn_flow(tracker).get(key)
    if cached not in (None, ""):
        return cached
    if key.startswith("churn_group_col_"):
        try:
            idx = int(key.rsplit("_", 1)[-1]) - 1
            group_cols = _churn_flow(tracker).get("group_cols")
            if isinstance(group_cols, list) and 0 <= idx < len(group_cols):
                text = str(group_cols[idx] or "").strip()
                if text:
                    return text
        except (TypeError, ValueError):
            pass
    return default


def _churn_flow_group_cols(tracker: Tracker) -> list[str]:
    flow = _churn_flow(tracker)
    cached = flow.get("group_cols")
    if isinstance(cached, list) and cached:
        cols = [str(c).strip() for c in cached if str(c).strip()]
        if cols:
            return cols
    cols: list[str] = []
    for i in range(1, CHURN_MAX_GROUP_LEVELS + 1):
        text = str(_churn_flow_val(tracker, f"churn_group_col_{i}", "") or "").strip()
        if text:
            cols.append(text)
    if cols:
        return cols
    for i in range(1, CHURN_MAX_GROUP_LEVELS + 1):
        raw = tracker.get_slot(f"churn_group_col_{i}")
        text = str(raw or "").strip()
        if text:
            cols.append(text)
    return cols


def _churn_slot_col(tracker: Tracker, slot: str) -> str:
    raw = tracker.get_slot(slot)
    if raw not in (None, ""):
        return str(raw).strip()
    role = _SLOT_COLUMN_ROLE.get(slot)
    if role:
        cached = _cached_session_column_role(tracker, role)
        if cached:
            return str(cached).strip()
    flow_val = _churn_flow_val(tracker, slot, "")
    if flow_val not in (None, ""):
        return str(flow_val).strip()
    return str(_column_default(tracker, slot) or "").strip()


def _churn_review_group_level_count(tracker: Tracker) -> int:
    raw = _churn_flow_val(tracker, "churn_group_level_count", None)
    if raw not in (None, ""):
        try:
            n = int(float(raw))
            return max(1, min(CHURN_MAX_GROUP_LEVELS, n))
        except (TypeError, ValueError):
            pass
    cols = _churn_flow_group_cols(tracker)
    if cols:
        return len(cols)
    return 1


def _churn_bucket_inputs_for_card(tracker: Tracker) -> list[dict[str, Any]]:
    bucket_mode_default = str(_churn_flow_val(tracker, "churn_bucket_mode", "threshold") or "threshold").strip().lower()
    if bucket_mode_default in ("custom_threshold", "threshold"):
        bucket_mode_default = "threshold"
    elif bucket_mode_default != "custom_numbers":
        bucket_mode_default = "threshold"
    return [
        {
            "id": "churn_top_bucket_enabled",
            "type": "radio",
            "label": "Enable top buckets",
            "layout": "split",
            "span": 2,
            "default": "on" if _slot_bool(_churn_flow_val(tracker, "churn_top_bucket_enabled", False)) else "off",
            "options": [
                {"label": "Off", "value": "off"},
                {"label": "On", "value": "on"},
            ],
        },
        {
            "id": "churn_bucket_mode",
            "type": "radio",
            "label": "Bucket mode",
            "layout": "split",
            "span": 2,
            "default": bucket_mode_default,
            "showWhen": {"field": "churn_top_bucket_enabled", "value": "on"},
            "options": [
                {"label": "Cumulative share thresholds", "value": "threshold"},
                {"label": "Items per bucket", "value": "custom_numbers"},
            ],
        },
        {
            "id": "churn_bucket_thresholds",
            "type": "text",
            "label": "Thresholds e.g. [0.2, 0.5, 0.8]",
            "hint": "You can create as many buckets as you want",
            "span": 2,
            "default": _churn_flow_val(tracker, "churn_bucket_thresholds", "[0.2, 0.5, 0.8]") or "[0.2, 0.5, 0.8]",
            "showWhen": [
                {"field": "churn_top_bucket_enabled", "value": "on"},
                {"field": "churn_bucket_mode", "value": "threshold"},
            ],
        },
        {
            "id": "churn_bucket_numbers",
            "type": "text",
            "label": "Items per bucket e.g. [3, 5, 10]",
            "hint": "You can create as many buckets as you want",
            "span": 2,
            "default": _churn_flow_val(tracker, "churn_bucket_numbers", "") or "",
            "showWhen": [
                {"field": "churn_top_bucket_enabled", "value": "on"},
                {"field": "churn_bucket_mode", "value": "custom_numbers"},
            ],
        },
        {
            "id": "churn_other_bucket",
            "type": "radio",
            "label": "'Other' bucket",
            "layout": "split",
            "span": 2,
            "default": str(_churn_flow_val(tracker, "churn_other_bucket", "include") or "include"),
            "showWhen": {"field": "churn_top_bucket_enabled", "value": "on"},
            "options": [
                {"label": "Include", "value": "include"},
                {"label": "Disable", "value": "disable"},
            ],
        },
        {
            "id": "churn_other_bucket_label",
            "type": "text",
            "label": "Label for the 'Other' bucket",
            "span": 2,
            "default": _churn_flow_val(tracker, "churn_other_bucket_label", "Other") or "Other",
            "showWhen": [
                {"field": "churn_top_bucket_enabled", "value": "on"},
                {"field": "churn_other_bucket", "value": "include"},
            ],
        },
    ]


def _churn_review_summary(tracker: Tracker) -> str:
    grp = " / ".join(_churn_group_cols_from_tracker(tracker))
    return "\n".join([
        f"Value: {_churn_slot_col(tracker, 'churn_value_col') or '—'}",
        f"Customer: {_churn_slot_col(tracker, 'churn_customer_col') or '—'}",
        f"Product: {_churn_slot_col(tracker, 'churn_product_col') or '—'}",
        f"Grouping: {grp or '—'}",
    ])


def _fdd_latest_closed_fy_end_year(cy: int, cm: int, fy_end_m: int, fy_end_d: int) -> int:
    """Latest closed FY-end calendar year for as-of month (no funktionssammlung import)."""
    cur = current_fy_end_year_containing_as_of(cy, cm, fy_end_m, fy_end_d)
    return cur if as_of_is_fy_end(cy, cm, fy_end_m, fy_end_d) else cur - 1


def _churn_sort_config_from_payload(tracker: Tracker, payload: dict | None) -> dict[str, Any]:
    p = payload or {}
    sort: dict[str, Any] = {"top_level_desc": True, "leaf_desc": True}
    s1 = str(p.get("churn_sort_by_1", tracker.get_slot("churn_sort_by_1") or "arr")).strip().lower()
    sort["top_level_by"] = "name" if s1 == "name" else "ARR2"
    s2 = str(p.get("churn_sort_by_2", tracker.get_slot("churn_sort_by_2") or "arr")).strip().lower()
    sort["leaf_within_parent"] = "name" if s2 == "name" else "ARR2"
    return sort


def _resolve_filter_context(tracker: Tracker) -> str:
    """
    Which output's filter rules are being edited (gst / pvm / top).

    `active_filter_context` can be missing on the next user turn after a follow-up
    chain (Rasa tracker persistence); `desired_output` stays set — use it as fallback.
    """
    raw = tracker.get_slot("active_filter_context")
    if raw in ("gst", "pvm", "top", "bs", "churn", "opos", "fa", "fte"):
        return str(raw)
    desired = str(tracker.get_slot("desired_output") or "").strip()
    output_type = str(tracker.get_slot("output_type") or "").strip()
    if output_type == "creditor_debitor_aging":
        return "opos"
    if output_type == "fixed_assets_rollforward":
        return "fa"
    if output_type == "fte_development":
        return "fte"
    return {
        "general_sales_table": "gst",
        "pvm_analysis": "pvm",
        "top_report": "top",
        "bubble_scatter": "bs",
        "churn": "churn",
        "fixed_assets_rollforward": "fa",
        "fte_development": "fte",
    }.get(desired, "gst")


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _parse_payload(tracker: Tracker) -> dict:
    """
    Extract the card payload from the latest message.

    The frontend sends: /submit_card{"card_payload": {"card": "...", ...}}

    Primary path: parse from the raw message text (most reliable).
    Fallback: use the entity extracted by Rasa's NLU pipeline.
    """
    lm = getattr(tracker, "latest_message", None)
    if not isinstance(lm, dict):
        lm = {}
    text = lm.get("text", "") or ""
    entities = lm.get("entities", []) or []

    # ── Primary path: parse from raw message text ──
    # Supports both /submit_card{"card_payload": {...}} and plain intent
    # payloads like /undo_to_checkpoint{"slots": {...}, "next_action": "..."}.
    if text.startswith("/"):
        try:
            brace_start = text.index("{")
            outer = json.loads(text[brace_start:])
            inner = outer.get("card_payload", outer)
            if isinstance(inner, str):
                inner = json.loads(inner)
            if isinstance(inner, dict):
                return inner
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    # ── Fallback: entity extracted by Rasa ──
    for ent in entities:
        if ent.get("entity") == "card_payload":
            val = ent.get("value", {})
            if isinstance(val, dict):
                return val
            if isinstance(val, str):
                try:
                    result = json.loads(val)
                    if isinstance(result, str):
                        result = json.loads(result)
                    return result if isinstance(result, dict) else {}
                except json.JSONDecodeError:
                    pass

    # ── Last resort: plain JSON text ──
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _inline_action(action: Action, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list[Any]:
    """Run an action in-process — avoids FollowupAction when the model lacks that action."""
    return action.run(dispatcher, tracker, domain)


def _inline_run_after_filter(
    tracker: Tracker,
    dispatcher: CollectingDispatcher,
    domain: dict,
    *,
    run_action: Action,
    pending_slot: str,
) -> list[Any]:
    events: list[Any] = []
    if str(tracker.get_slot(pending_slot) or "").strip().lower() == "filters":
        events.append(SlotSet(pending_slot, ""))
    events.extend(run_action.run(dispatcher, tracker, domain))
    return events


def _enter_strand_run_target(
    dispatcher: CollectingDispatcher,
    tracker: Tracker,
    script_key: str,
    *,
    pending_slot: str | None = None,
) -> list[Any]:
    """Show master vs separate workbook card immediately before a strand script run."""
    key = str(script_key or "").strip().lower()
    if key not in STRAND_RUN_META:
        key = "opos"
    events: list[Any] = [SlotSet("strand_run_script", key)]
    if pending_slot and str(tracker.get_slot(pending_slot) or "").strip().lower() == "filters":
        events.append(SlotSet(pending_slot, ""))
    dispatcher.utter_message(json_message=_strand_run_target_card(tracker, key))
    return events


def _get_headers_card(headers: list[str], slots_to_fill: list[dict]) -> dict:
    """Build an adaptive card with dropdowns populated from column headers."""
    inputs = []
    for slot_def in slots_to_fill:
        inputs.append({
            "id": slot_def["id"],
            "type": "dropdown",
            "label": slot_def["label"],
            "options": [{"label": h, "value": h} for h in headers],
            "required": slot_def.get("required", True),
        })
    return inputs


def _build_filter_display(rules: list[dict]) -> str:
    """Build a human-readable summary of active filter rules."""
    if not rules:
        return ""

    def _humanize_rule(r: dict) -> str:
        col = r.get("col") or "?"
        op = str(r.get("op") or "?").strip().lower()

        if op in {"eq", "=="}:
            return f"{col} equals {r.get('value')}"
        if op in {"ne", "!="}:
            return f"{col} does not equal {r.get('value')}"
        if op in {"in"}:
            vals = r.get("values", r.get("value", []))
            if not isinstance(vals, (list, tuple, set)):
                vals = [vals]
            return f"{col} is in [{', '.join(map(str, vals))}]"
        if op in {"not_in"}:
            vals = r.get("values", r.get("value", []))
            if not isinstance(vals, (list, tuple, set)):
                vals = [vals]
            return f"{col} is not in [{', '.join(map(str, vals))}]"
        if op in {"gt", ">"}:
            return f"{col} is greater than {r.get('value')}"
        if op in {"gte", ">="}:
            return f"{col} is greater than or equal to {r.get('value')}"
        if op in {"lt", "<"}:
            return f"{col} is less than {r.get('value')}"
        if op in {"lte", "<="}:
            return f"{col} is less than or equal to {r.get('value')}"
        if op == "between":
            return f"{col} is between {r.get('lo')} and {r.get('hi')}"
        if op in {"contains"}:
            return f"{col} contains “{r.get('pattern', r.get('value'))}”"
        if op in {"startswith"}:
            return f"{col} starts with “{r.get('pattern', r.get('value'))}”"
        if op in {"endswith"}:
            return f"{col} ends with “{r.get('pattern', r.get('value'))}”"
        if op in {"regex"}:
            return f"{col} matches regex “{r.get('pattern')}”"
        if op in {"isna", "isnull"}:
            return f"{col} is empty"
        if op in {"notna", "notnull"}:
            return f"{col} is not empty"
        if op == "isblank":
            return f"{col} is blank"
        if op == "notblank":
            return f"{col} is not blank"
        if op == "is_zero":
            return f"{col} equals 0"
        if op == "nonzero":
            return f"{col} is not 0"
        return f"{col} {op}"

    lines = [f"• {_humanize_rule(r)}" for r in rules if isinstance(r, dict)]
    return "\n".join([ln for ln in lines if ln.strip()])


def _fdd_filters_payload(rules_json: str | None) -> dict:
    try:
        raw = json.loads(rules_json or "[]")
    except (json.JSONDecodeError, TypeError):
        raw = []
    rules = raw if isinstance(raw, list) else []
    return {"enabled": len(rules) > 0, "rules": rules}


def _payload_is_card_submit(payload: dict, card_name: str) -> bool:
    return str(payload.get("card") or "").strip() == card_name


def _slot_events_from_payload(payload: dict, keys: tuple[str, ...]) -> list:
    """Only SlotSet keys present in the card payload (undo/redo must not wipe slots)."""
    events: list[Any] = []
    for key in keys:
        if key in payload:
            events.append(SlotSet(key, payload.get(key)))
    return events


def _header_opts_for_review(
    tracker: Tracker,
    payload: dict | None,
    *selected_cols: Any,
) -> list[dict[str, str]]:
    """Dropdown options = headers from slot/API plus any already-selected column names."""
    headers = _headers_from_sources(tracker, payload)
    seen: set[str] = set()
    opts: list[dict[str, str]] = []
    for raw in list(headers) + [c for c in selected_cols if c]:
        h = str(raw).strip()
        if h and h not in seen:
            seen.add(h)
            opts.append({"label": h, "value": h})
    return opts


def _revenue_slot(tracker: Tracker, key: str, payload: dict | None = None, default: Any = "") -> Any:
    if isinstance(payload, dict) and key in payload and payload[key] not in (None, ""):
        return payload[key]
    val = tracker.get_slot(key)
    return default if val in (None, "") else val


# Shared semantic roles for the columns that mean the same thing across strands
# (GST / PVM / TOP / Bubble Scatter). A pick in one strand pre-fills the sister
# slots in every other strand. The "dimension" role is the primary analysis
# grouping column (products / customers / ...).
_COLUMN_ROLE_SLOTS: dict[str, list[str]] = {
    "revenue": ["gst_revenue_col", "pvm_revenue_col", "top_value_col", "bs_revenue_col", "churn_value_col"],
    "invoice": ["gst_invoice_col", "pvm_invoice_col", "top_invoice_col", "bs_invoice_col", "churn_invoice_col"],
    "start": ["gst_start_col", "top_start_col", "bs_start_col", "churn_start_col"],
    "end": ["gst_end_col", "top_end_col", "bs_end_col", "churn_end_col"],
    "cost": ["gst_cost_col", "pvm_cost_col", "bs_cogs_col"],
    "profit": ["gst_profit_col", "pvm_profit_col", "bs_profit_col"],
    "quantity": ["pvm_quantity_col"],
    "dimension": ["top_col", "pvm_group_col", "gst_group_col_1", "bs_group_col_1"],
    "customer": ["churn_customer_col"],
    "product": ["churn_product_col"],
}

_SLOT_COLUMN_ROLE: dict[str, str] = {
    slot: role for role, slots in _COLUMN_ROLE_SLOTS.items() for slot in slots
}


def _remember_session_column_role(tracker: Tracker, role: str, value: Any) -> None:
    text = str(value or "").strip()
    if not text or not role:
        return
    key = _sender_cache_key(tracker)
    if not key:
        return
    bucket = _SESSION_COLUMN_ROLES.setdefault(key, {})
    bucket[role] = text


def _cached_session_column_role(tracker: Tracker, role: str) -> str | None:
    key = _sender_cache_key(tracker)
    if not key:
        return None
    return (_SESSION_COLUMN_ROLES.get(key) or {}).get(role)


def _column_default(
    tracker: Tracker,
    target_slot: str,
    payload: dict | None = None,
    default: Any = "",
) -> Any:
    """Flow-specific slot, else first non-empty sister slot in the same semantic role."""
    own = _revenue_slot(tracker, target_slot, payload, default=None)
    if own not in (None, ""):
        return own
    role = _SLOT_COLUMN_ROLE.get(target_slot)
    if not role:
        return default
    for slot in _COLUMN_ROLE_SLOTS.get(role, []):
        if slot == target_slot:
            continue
        val = tracker.get_slot(slot)
        if val not in (None, ""):
            return val
    cached = _cached_session_column_role(tracker, role)
    if cached:
        return cached
    return default


def _sync_column_role_slots(tracker: Tracker, slot: str, value: Any) -> list:
    """Mirror a column pick to all strand slots in the same semantic role."""
    events: list[Any] = [SlotSet(slot, value)]
    role = _SLOT_COLUMN_ROLE.get(slot)
    text = str(value or "").strip()
    if not role or not text:
        return events
    _remember_session_column_role(tracker, role, text)
    for sister in _COLUMN_ROLE_SLOTS.get(role, []):
        if sister == slot:
            continue
        events.append(SlotSet(sister, value))
    return events


def _hydrate_column_roles_from_session_cache(tracker: Tracker) -> list:
    """Push cached column-role picks into empty sister slots (survives action-server restart)."""
    events: list[Any] = []
    for role, slots in _COLUMN_ROLE_SLOTS.items():
        cached = _cached_session_column_role(tracker, role)
        if not cached:
            continue
        for slot in slots:
            if tracker.get_slot(slot) not in (None, ""):
                continue
            events.append(SlotSet(slot, cached))
    return events


def _header_opts_for_column_card(
    tracker: Tracker,
    payload: dict | None,
    *slot_names: str,
) -> list[dict[str, str]]:
    """Dropdown options including headers and shared column defaults (all strands)."""
    defaults = [_column_default(tracker, name, payload) for name in slot_names]
    return _header_opts_for_review(tracker, payload, *defaults)


def _first_fy_for_gst_review_card(tracker: Tracker, default: int = 2020) -> int:
    return _slot_int_first_hit(tracker, ("gst_first_fy_override", "first_fy"), default)


def _gst_review_summary(tracker: Tracker, payload: dict | None = None) -> str:
    rev = _revenue_slot(tracker, "gst_revenue_col", payload)
    inv = _revenue_slot(tracker, "gst_invoice_col", payload)
    cost = _revenue_slot(tracker, "gst_cost_col", payload)
    prof = _revenue_slot(tracker, "gst_profit_col", payload)
    start = _revenue_slot(tracker, "gst_start_col", payload)
    end = _revenue_slot(tracker, "gst_end_col", payload)
    g1 = _revenue_slot(tracker, "gst_group_col_1", payload)
    g2 = _revenue_slot(tracker, "gst_group_col_2", payload)
    g3 = _revenue_slot(tracker, "gst_group_col_3", payload)
    calc = _revenue_slot(tracker, "gst_calc_mode", payload, "invoice")
    profit = _revenue_slot(tracker, "gst_profit_mode", payload, "cost")
    lines = [
        f"Revenue: {rev or '—'}",
        f"Invoice / period: {inv or '—'}",
        f"Calc: {calc} · GP mode: {profit}",
    ]
    if calc == "accrual":
        lines.append(f"Accrual: {start or '—'} → {end or '—'}")
    if profit == "cost" and cost:
        lines.append(f"COGS: {cost}")
    elif profit == "profit" and prof:
        lines.append(f"Gross profit: {prof}")
    grp = " / ".join(x for x in (g1, g2, g3) if x)
    if grp:
        lines.append(f"Grouping: {grp}")
    return "\n".join(lines)


def _pvm_review_summary(tracker: Tracker, payload: dict | None = None) -> str:
    lines = [
        f"Revenue: {_revenue_slot(tracker, 'pvm_revenue_col', payload) or '—'}",
        f"Invoice / period: {_revenue_slot(tracker, 'pvm_invoice_col', payload) or '—'}",
        f"Quantity: {_revenue_slot(tracker, 'pvm_quantity_col', payload) or '—'}",
        f"Product: {_revenue_slot(tracker, 'pvm_group_col', payload) or '—'}",
    ]
    mode = _revenue_slot(tracker, "pvm_profit_mode", payload, "cost")
    if mode == "cost":
        lines.append(f"COGS: {_revenue_slot(tracker, 'pvm_cost_col', payload) or '—'}")
    elif mode == "profit":
        lines.append(f"GP: {_revenue_slot(tracker, 'pvm_profit_col', payload) or '—'}")
    return "\n".join(lines)


def _top_review_summary(tracker: Tracker, payload: dict | None = None) -> str:
    calc = _revenue_slot(tracker, "top_calc_mode", payload, "invoice")
    lines = [
        f"Dimension: {_revenue_slot(tracker, 'top_col', payload) or '—'}",
        f"Value: {_revenue_slot(tracker, 'top_value_col', payload) or '—'}",
        f"Invoice / period: {_revenue_slot(tracker, 'top_invoice_col', payload) or '—'}",
        f"Calc: {calc}",
    ]
    if calc == "accrual":
        lines.append(
            f"Accrual: {_revenue_slot(tracker, 'top_start_col', payload) or '—'}"
            f" → {_revenue_slot(tracker, 'top_end_col', payload) or '—'}"
        )
    return "\n".join(lines)


def _emit_gst_columns_card(
    dispatcher: CollectingDispatcher,
    tracker: Tracker,
    payload: dict | None = None,
) -> None:
    calc_mode = str(_revenue_slot(tracker, "gst_calc_mode", payload, "invoice"))
    profit_mode = str(_revenue_slot(tracker, "gst_profit_mode", payload, "cost"))
    headers = _headers_from_sources(tracker, payload)
    header_opts = _header_opts_for_column_card(
        tracker,
        payload,
        "gst_revenue_col",
        "gst_invoice_col",
        "gst_start_col",
        "gst_end_col",
        "gst_cost_col",
        "gst_profit_col",
    )
    is_accrual = calc_mode == "accrual"

    col_inputs: list[dict[str, Any]] = [
        {"id": "gst_revenue_col", "type": "dropdown", "label": "Revenue / invoice amount column",
         "options": header_opts or [{"label": h, "value": h} for h in headers],
         "default": _column_default(tracker, "gst_revenue_col", payload)},
        {"id": "gst_invoice_col", "type": "dropdown",
         "label": "Invoice / booking date (for period & accrual recognition)",
         "options": header_opts or [{"label": h, "value": h} for h in headers],
         "default": _column_default(tracker, "gst_invoice_col", payload)},
    ]
    if is_accrual:
        col_inputs += [
            {"id": "gst_start_col", "type": "dropdown", "label": "Contract start date column",
             "options": header_opts, "required": False,
             "default": _column_default(tracker, "gst_start_col", payload)},
            {"id": "gst_end_col", "type": "dropdown", "label": "Contract end date column",
             "options": header_opts, "required": False,
             "default": _column_default(tracker, "gst_end_col", payload)},
        ]
    if profit_mode == "cost":
        col_inputs.append(
            {"id": "gst_cost_col", "type": "dropdown", "label": "Cost of goods sold column",
             "options": header_opts or [{"label": h, "value": h} for h in headers],
             "default": _column_default(tracker, "gst_cost_col", payload)},
        )
    elif profit_mode == "profit":
        col_inputs.append(
            {"id": "gst_profit_col", "type": "dropdown", "label": "Gross profit column",
             "options": header_opts or [{"label": h, "value": h} for h in headers],
             "default": _column_default(tracker, "gst_profit_col", payload)},
        )

    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "gst_columns",
            "title": "Column Selection",
            "subtitle": "Map the required columns from your data source.",
            "inputs": col_inputs,
            "submit_label": "Continue",
        }
    )


def _emit_pvm_columns_card(
    dispatcher: CollectingDispatcher,
    tracker: Tracker,
    payload: dict | None = None,
) -> None:
    profit_mode = str(_revenue_slot(tracker, "pvm_profit_mode", payload, "cost"))
    is_cost = profit_mode == "cost"
    header_opts = _header_opts_for_review(
        tracker,
        payload,
        _column_default(tracker, "pvm_revenue_col", payload),
        _column_default(tracker, "pvm_invoice_col", payload),
        _column_default(tracker, "pvm_quantity_col", payload),
        _column_default(tracker, "pvm_cost_col", payload),
        _column_default(tracker, "pvm_profit_col", payload),
    )
    col_inputs: list[dict[str, Any]] = [
        {"id": "pvm_revenue_col", "type": "dropdown", "label": "Invoice / revenue amount column",
         "options": header_opts, "default": _column_default(tracker, "pvm_revenue_col", payload)},
        {"id": "pvm_invoice_col", "type": "dropdown", "label": "Invoice date column",
         "options": header_opts, "default": _column_default(tracker, "pvm_invoice_col", payload)},
        {"id": "pvm_quantity_col", "type": "dropdown", "label": "Quantity column",
         "options": header_opts, "default": _column_default(tracker, "pvm_quantity_col", payload)},
    ]
    if is_cost:
        col_inputs.append(
            {"id": "pvm_cost_col", "type": "dropdown", "label": "Cost of goods sold column",
             "options": header_opts, "default": _column_default(tracker, "pvm_cost_col", payload)},
        )
    else:
        col_inputs.append(
            {"id": "pvm_profit_col", "type": "dropdown", "label": "Gross profit column",
             "options": header_opts, "default": _column_default(tracker, "pvm_profit_col", payload)},
        )
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "pvm_columns",
            "title": "Column Selection",
            "inputs": col_inputs,
            "submit_label": "Continue",
        }
    )


def _emit_top_columns_card(
    dispatcher: CollectingDispatcher,
    tracker: Tracker,
    payload: dict | None = None,
) -> None:
    calc_mode = str(_revenue_slot(tracker, "top_calc_mode", payload, "invoice"))
    header_opts = _header_opts_for_review(
        tracker,
        payload,
        _column_default(tracker, "top_value_col", payload),
        _column_default(tracker, "top_invoice_col", payload),
        _column_default(tracker, "top_start_col", payload),
        _column_default(tracker, "top_end_col", payload),
    )
    col_inputs: list[dict[str, Any]] = [
        {"id": "top_value_col", "type": "dropdown", "label": "Value column",
         "options": header_opts, "default": _column_default(tracker, "top_value_col", payload)},
        {"id": "top_invoice_col", "type": "dropdown", "label": "Invoice / period column",
         "options": header_opts, "default": _column_default(tracker, "top_invoice_col", payload)},
    ]
    if calc_mode == "accrual":
        col_inputs += [
            {"id": "top_start_col", "type": "dropdown", "label": "Contract start date column",
             "options": header_opts, "required": False,
             "default": _column_default(tracker, "top_start_col", payload)},
            {"id": "top_end_col", "type": "dropdown", "label": "Contract end date column",
             "options": header_opts, "required": False,
             "default": _column_default(tracker, "top_end_col", payload)},
        ]
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "top_columns",
            "title": "Column Selection",
            "inputs": col_inputs,
            "submit_label": "Continue",
        }
    )


class ActionReshowGstColumns(Action):
    def name(self) -> str:
        return "action_reshow_gst_columns"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        _emit_gst_columns_card(dispatcher, tracker)
        return []


def _emit_gst_groups_card(dispatcher: CollectingDispatcher, tracker: Tracker) -> None:
    headers = _headers_from_sources(tracker)
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "gst_groups",
            "title": "Grouping",
            "subtitle": "Select up to 3 columns to group the analysis by.",
            "inputs": [
                {"id": "gst_group_col_1", "type": "dropdown", "label": "Level 1 (required)",
                 "options": [{"label": h, "value": h} for h in headers],
                 "default": _column_default(tracker, "gst_group_col_1")},
                {"id": "gst_group_col_2", "type": "dropdown", "label": "Level 2 (optional)",
                 "options": [{"label": "(none)", "value": ""}] + [{"label": h, "value": h} for h in headers],
                 "required": False, "default": tracker.get_slot("gst_group_col_2") or ""},
                {"id": "gst_group_col_3", "type": "dropdown", "label": "Level 3 (optional)",
                 "options": [{"label": "(none)", "value": ""}] + [{"label": h, "value": h} for h in headers],
                 "required": False, "default": tracker.get_slot("gst_group_col_3") or ""},
            ],
            "submit_label": "Continue",
        }
    )


class ActionReshowGstGroups(Action):
    def name(self) -> str:
        return "action_reshow_gst_groups"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        _emit_gst_groups_card(dispatcher, tracker)
        return []


class ActionReshowGstLimits(Action):
    def name(self) -> str:
        return "action_reshow_gst_limits"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "gst_limits",
                "title": "Item Limits per Level",
                "subtitle": "Configure how many items appear before grouping into 'Other'.",
                "inputs": [
                    {"id": "gst_max_items_l1", "type": "number", "label": "Max items — Level 1",
                     "placeholder": "10", "default": str(tracker.get_slot("gst_max_items_l1") or 10)},
                    {"id": "gst_other_label_l1", "type": "text", "label": "Label for 'Other' — Level 1",
                     "default": tracker.get_slot("gst_other_label_l1") or "Other"},
                    {"id": "gst_max_items_l2", "type": "number", "label": "Max items — Level 2",
                     "placeholder": "8", "default": str(tracker.get_slot("gst_max_items_l2") or 8)},
                    {"id": "gst_other_label_l2", "type": "text", "label": "Label for 'Other' — Level 2",
                     "default": tracker.get_slot("gst_other_label_l2") or "Other"},
                    {"id": "gst_max_items_l3", "type": "number", "label": "Max items — Level 3",
                     "placeholder": "5", "default": str(tracker.get_slot("gst_max_items_l3") or 5)},
                    {"id": "gst_other_label_l3", "type": "text", "label": "Label for 'Other' — Level 3",
                     "default": tracker.get_slot("gst_other_label_l3") or "Other"},
                ],
                "submit_label": "Continue",
            }
        )
        return []


class ActionReshowGstInvoiceMode(Action):
    def name(self) -> str:
        return "action_reshow_gst_invoice_mode"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        dispatcher.utter_message(json_message=_build_gst_table_name_card())
        return []


class ActionReshowGstCalcMode(Action):
    def name(self) -> str:
        return "action_reshow_gst_calc_mode"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "gst_calc_mode",
                "title": "Calculation Mode",
                "inputs": [
                    {"id": "gst_calc_mode", "type": "radio", "label": "Select calculation mode",
                     "options": [
                         {"label": "Invoiced amounts", "value": "invoice"},
                         {"label": "Accrual-based revenue", "value": "accrual"},
                     ],
                     "default": tracker.get_slot("gst_calc_mode") or "invoice"},
                    {"id": "gst_profit_mode", "type": "radio", "label": "Gross profit calculation",
                     "options": [
                         {"label": "Use a cost column (GP = Revenue − Cost)", "value": "cost"},
                         {"label": "Use an existing gross profit column", "value": "profit"},
                         {"label": "No gross profit column available", "value": "none"},
                     ],
                     "default": tracker.get_slot("gst_profit_mode") or "cost"},
                ],
                "submit_label": "Continue",
            }
        )
        return []


class ActionReshowGstPeriods(Action):
    def name(self) -> str:
        return "action_reshow_gst_periods"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "gst_periods",
                "title": "Additional Period Views",
                "inputs": [
                    {"id": "gst_periods", "type": "multi_select",
                     "label": "Would you also like to include YTD and/or LTM in the analysis?",
                     "required": False,
                     "options": [{"label": "YTD", "value": "ytd"}, {"label": "LTM", "value": "ltm"}]},
                    {"id": "gst_deltas", "type": "multi_select",
                     "label": "Show delta or annual growth rate for YTD/LTM columns?",
                     "required": False,
                     "options": [
                         {"label": "YTD delta", "value": "ytd_delta"},
                         {"label": "LTM delta", "value": "ltm_delta"},
                         {"label": "YTD CAGR", "value": "ytd_cagr"},
                         {"label": "LTM CAGR", "value": "ltm_cagr"},
                     ]},
                ],
                "submit_label": "Continue",
            }
        )
        return []


class ActionReshowGstMetrics(Action):
    def name(self) -> str:
        return "action_reshow_gst_metrics"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "gst_metrics",
                "title": "Additional Metrics",
                "inputs": [{
                    "id": "gst_metrics", "type": "multi_select",
                    "label": "Would you like to include gross profit and/or gross margin in the analysis?",
                    "required": False,
                    "options": [
                        {"label": "Gross Profit (GP)", "value": "gp"},
                        {"label": "Gross Margin % (GM)", "value": "gm"},
                    ],
                }],
                "submit_label": "Continue",
            }
        )
        return []


class ActionReshowGstSort(Action):
    def name(self) -> str:
        return "action_reshow_gst_sort"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        review_fy = _first_fy_for_gst_review_card(tracker)
        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "gst_sort",
                "title": "Sorting & Time Period",
                "inputs": [
                    {"id": "gst_sort_mode", "type": "radio",
                     "label": "How would you like the table to be sorted?",
                     "options": [
                         {"label": "By revenue for the most recent fiscal year", "value": "FY_LAST"},
                         {"label": "Alphabetically", "value": "ALPHABETICAL"},
                     ],
                     "default": tracker.get_slot("gst_sort_mode") or "FY_LAST"},
                    {"id": "gst_first_fy_override", "type": "number",
                     "label": "Please confirm the first fiscal year to include:",
                     "default": str(int(review_fy))},
                ],
                "submit_label": "Continue",
            }
        )
        return []


class ActionReshowPvmColumns(Action):
    def name(self) -> str:
        return "action_reshow_pvm_columns"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        _emit_pvm_columns_card(dispatcher, tracker)
        return []


class ActionReshowTopColumns(Action):
    def name(self) -> str:
        return "action_reshow_top_columns"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        _emit_top_columns_card(dispatcher, tracker)
        return []


def _propagate_shared_filters(tracker: Tracker) -> list:
    """
    If filter rules exist in any output context, copy them onto contexts that are still empty.
    Used when switching or adding an output so users do not have to re-enter the same rules.
    """
    template_json = ""
    template_disp = ""
    for ctx in ("gst", "pvm", "top", "bs", "fa", "opos"):
        raw = tracker.get_slot(f"{ctx}_filter_rules_json") or "[]"
        try:
            rules = json.loads(raw)
        except Exception:
            rules = []
        if rules:
            template_json = raw
            template_disp = (tracker.get_slot(f"{ctx}_filter_rules_display") or "").strip()
            break
    if not template_json or template_json == "[]":
        return []
    try:
        template_rules = json.loads(template_json)
    except Exception:
        template_rules = []
    if not template_rules:
        return []
    fallback_disp = _build_filter_display(template_rules).strip()
    events = []
    for ctx in ("gst", "pvm", "top", "bs", "fa", "opos"):
        raw = tracker.get_slot(f"{ctx}_filter_rules_json") or "[]"
        try:
            cur = json.loads(raw)
        except Exception:
            cur = []
        if cur:
            continue
        disp = template_disp or fallback_disp
        events.append(SlotSet(f"{ctx}_filter_rules_json", template_json))
        events.append(SlotSet(f"{ctx}_filter_rules_display", disp))
    return events


def _gst_filter_checkpoint_card(tracker: Tracker) -> dict[str, Any]:
    """Adaptive card shown after GST sort step and after editing filter rules."""
    disp = (tracker.get_slot("gst_filter_rules_display") or "").strip()
    if not disp:
        disp = "No active filter rules."
    return {
        "type": "adaptive_card",
        "card": "gst_filter_checkpoint",
        "title": "Filter rules",
        "subtitle": "Review your current filter rules, then choose an action.",
        "review_text": disp,
        "inputs": [
            {
                "id": "gst_filter_manage_choice",
                "type": "radio",
                "label": "What would you like to do?",
                "options": [
                    {"label": "1. Delete current rules", "value": "delete_all"},
                    {"label": "2. Add a rule", "value": "add_rule"},
                    {"label": "3. Keep rules as they are", "value": "keep"},
                ],
            }
        ],
        "submit_label": "Continue",
    }


def _pvm_filter_checkpoint_card(tracker: Tracker) -> dict[str, Any]:
    disp = (tracker.get_slot("pvm_filter_rules_display") or "").strip()
    if not disp:
        disp = "No active filter rules."
    return {
        "type": "adaptive_card",
        "card": "pvm_filter_checkpoint",
        "title": "PVM — Filter rules",
        "subtitle": "Review your current filter rules, then choose an action.",
        "review_text": disp,
        "inputs": [
            {
                "id": "pvm_filter_manage_choice",
                "type": "radio",
                "label": "What would you like to do?",
                "options": [
                    {"label": "1. Delete current rules", "value": "delete_all"},
                    {"label": "2. Add a rule", "value": "add_rule"},
                    {"label": "3. Keep rules as they are", "value": "keep"},
                ],
            }
        ],
        "submit_label": "Continue",
    }


def _top_filter_checkpoint_card(tracker: Tracker) -> dict[str, Any]:
    disp = (tracker.get_slot("top_filter_rules_display") or "").strip()
    if not disp:
        disp = "No active filter rules."
    return {
        "type": "adaptive_card",
        "card": "top_filter_checkpoint",
        "title": "TOP Report — Filter rules",
        "subtitle": "Review your current filter rules, then choose an action.",
        "review_text": disp,
        "inputs": [
            {
                "id": "top_filter_manage_choice",
                "type": "radio",
                "label": "What would you like to do?",
                "options": [
                    {"label": "1. Delete current rules", "value": "delete_all"},
                    {"label": "2. Add a rule", "value": "add_rule"},
                    {"label": "3. Keep rules as they are", "value": "keep"},
                ],
            }
        ],
        "submit_label": "Continue",
    }


def _bs_filter_checkpoint_card(tracker: Tracker) -> dict[str, Any]:
    disp = (tracker.get_slot("bs_filter_rules_display") or "").strip()
    if not disp:
        disp = "No active filter rules."
    return {
        "type": "adaptive_card",
        "card": "bs_filter_checkpoint",
        "title": "Bubble Scatter Plot — Filter rules",
        "subtitle": "Review your current filter rules, then choose an action.",
        "review_text": disp,
        "inputs": [
            {
                "id": "bs_filter_manage_choice",
                "type": "radio",
                "label": "What would you like to do?",
                "options": [
                    {"label": "1. Delete current rules", "value": "delete_all"},
                    {"label": "2. Add a rule", "value": "add_rule"},
                    {"label": "3. Keep rules as they are", "value": "keep"},
                ],
            }
        ],
        "submit_label": "Continue",
    }


def _fdd_post(path: str, body: dict, timeout: int = FDD_SCRIPT_RUN_TIMEOUT_SEC) -> dict:
    """POST to the FastAPI fdd_bot router. Returns parsed JSON."""
    try:
        resp = requests.post(f"{FASTAPI_BASE_URL}{path}", json=body, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as exc:
        detail_msg = str(exc)
        resp = exc.response
        if resp is not None:
            try:
                body_json = resp.json()
                detail = body_json.get("detail") if isinstance(body_json, dict) else None
                if isinstance(detail, list):
                    detail_msg = json.dumps(detail, ensure_ascii=False)[:500]
                elif isinstance(detail, dict):
                    detail_msg = detail.get("message") or json.dumps(detail, ensure_ascii=False)[:500]
                    out: dict[str, Any] = {"success": False, "message": f"{exc} — {detail_msg}"}
                    if detail.get("errors"):
                        out["errors"] = detail["errors"]
                    return out
                elif isinstance(detail, str):
                    detail_msg = detail
            except (ValueError, TypeError):
                detail_msg = (resp.text or str(exc))[:500]
        return {"success": False, "message": f"{exc} — {detail_msg}"}
    except requests.RequestException as exc:
        return {"success": False, "message": str(exc)}


def _fdd_get(path: str, params: dict | None = None) -> dict:
    """GET from the FastAPI fdd_bot router. Non-2xx responses return JSON fields for UI/debug."""
    try:
        resp = requests.get(f"{FASTAPI_BASE_URL}{path}", params=params, timeout=30)
        if resp.status_code < 400:
            return resp.json()
        merged: dict[str, Any] = {"success": False, "status_code": resp.status_code, "headers": []}
        try:
            body = resp.json()
        except ValueError:
            merged["message"] = resp.text or resp.reason
            return merged
        detail = body.get("detail") if isinstance(body, dict) else None
        if isinstance(detail, dict):
            merged.update(detail)
            merged["message"] = detail.get("user_message") or detail.get("message") or str(detail)
        elif isinstance(detail, str):
            merged["message"] = detail
        else:
            merged["message"] = str(body)
        return merged
    except requests.RequestException as exc:
        return {"success": False, "message": str(exc), "headers": []}


def _revenue_sales_file_upload_card() -> dict[str, Any]:
    """Adaptive card JSON for uploading the sales / revenue workbook."""
    return {
        "type": "adaptive_card",
        "card": "file_upload",
        "title": "Upload Sales Data",
        "subtitle": "Please upload the Excel file containing the sales data.",
        "inputs": [
            {
                "id": "file_upload",
                "type": "file_drop",
                "label": "Drop your XLSX file here or click to browse",
                "accept": ".xlsx",
                "required": True,
            }
        ],
        "submit_label": "Upload",
    }


def _build_output_type_card() -> dict[str, Any]:
    """Main output selector (Databook / Revenue Databook / OPOS / Fixed assets / ...)."""
    return {
        "type": "adaptive_card",
        "card": "build_databook",
        "title": "Select Output",
        "subtitle": "What would you like to build?",
        "inputs": [
            {
                "id": "output_type",
                "type": "radio",
                "label": "Choose output",
                "options": [
                    {"label": "Databook", "value": "databook"},
                    {"label": "Revenue Databook", "value": "revenue_databook"},
                    {"label": "Creditor / Debitor Aging", "value": "creditor_debitor_aging"},
                    {"label": "Fixed Assets Rollforward", "value": "fixed_assets_rollforward"},
                    {"label": "FTE Development", "value": "fte_development"},
                ],
            }
        ],
        "submit_label": "Continue",
    }


def _revenue_sales_reuse_card(tracker: Tracker, payload: dict | None = None) -> dict[str, Any]:
    fp = str(tracker.get_slot("file_path") or (payload or {}).get("file_path") or "")
    label = os.path.basename(fp) if fp else "your uploaded workbook"
    return {
        "type": "adaptive_card",
        "card": "reuse_sales_file",
        "title": "Use your existing upload?",
        "subtitle": (
            f"You already have a workbook in this session ({label}). "
            "Would you like to use it for this step, or upload a different file?"
        ),
        "inputs": [
            {
                "id": "reuse_previous_file",
                "type": "radio",
                "label": "Use the last uploaded file for this step?",
                "options": [
                    {"label": "Yes, continue with that file", "value": "yes"},
                    {"label": "No, I will upload a different file", "value": "no"},
                ],
            }
        ],
        "submit_label": "Continue",
    }


def _file_id_from_sources(tracker: Tracker, payload: dict | None = None) -> str:
    """file_id from tracker slot or enriched card payload (frontend upload context)."""
    fid = str(tracker.get_slot("file_id") or "").strip()
    if fid:
        return fid
    if isinstance(payload, dict):
        return str(payload.get("file_id") or "").strip()
    return ""


def _slot_events_for_preloaded_file(tracker: Tracker, payload: dict | None) -> list:
    """Persist pre-uploaded workbook refs from payload into tracker slots."""
    if not isinstance(payload, dict):
        return []
    fid = str(payload.get("file_id") or "").strip()
    if not fid or str(tracker.get_slot("file_id") or "").strip() == fid:
        return []
    events: list[Any] = [SlotSet("file_id", fid)]
    fp = payload.get("file_path")
    if fp:
        events.append(SlotSet("file_path", str(fp)))
    headers = _normalize_header_list(payload.get("headers"))
    if headers:
        _remember_session_headers(tracker, headers)
        events.append(SlotSet("headers", headers))
    sheet_names = payload.get("sheet_names")
    if isinstance(sheet_names, list) and sheet_names:
        events.append(SlotSet("sheet_names", json.dumps(sheet_names)))
    elif isinstance(sheet_names, str) and sheet_names:
        events.append(SlotSet("sheet_names", sheet_names))
    sid = str(payload.get("session_id") or _fdd_session_id(tracker) or "").strip()
    if sid:
        events.append(SlotSet("session_id", sid))
    return events


def _emit_sales_upload_or_reuse(
    dispatcher: CollectingDispatcher,
    tracker: Tracker,
    payload: dict | None = None,
) -> list:
    events = _slot_events_for_preloaded_file(tracker, payload)
    fid = _file_id_from_sources(tracker, payload)
    if fid:
        dispatcher.utter_message(json_message=_revenue_sales_reuse_card(tracker, payload))
    else:
        dispatcher.utter_message(json_message=_revenue_sales_file_upload_card())
    return events


# ─── Central card dispatcher ──────────────────────────────────────────────────


class ActionDispatchCard(Action):
    """
    Single entry point for every adaptive card submission.
    Reads payload["card"] and routes to the matching handler method.
    This avoids Rasa rule conflicts that arise when multiple rules share
    the same `submit_card` intent trigger.
    """

    def name(self) -> str:
        return "action_dispatch_card"

    # Map card name → handler instance (lazy-built once)
    _handlers: dict[str, Any] = {}

    @classmethod
    def _build_handlers(cls) -> dict[str, Any]:
        if cls._handlers:
            return cls._handlers
        instances: dict[str, Any] = {
            "main_welcome": ActionProcessMainCard(),
            "dates": ActionProcessDatesCard(),
            "base_path": ActionNavigateFolders(),
            "folder_select": ActionProcessFolderSelection(),
            "build_databook": ActionProcessBuildDatabook(),
            "preload_file": ActionPreloadFile(),
            "reuse_sales_file": ActionProcessReuseSalesFile(),
            "file_upload": ActionUploadFile(),
            "sheet_name": ActionGetHeaders(),
            "fx_rate": ActionProcessFxCard(),
            "fx_col_select": ActionProcessDesiredOutput(),
            "desired_output": ActionProcessDesiredOutput(),
            # GST
            "gst_invoice_mode": ActionProcessGstInvoiceMode(),
            "gst_calc_mode": ActionProcessGstCalcMode(),
            "gst_columns": ActionProcessGstColumns(),
            "gst_groups": ActionProcessGstGroups(),
            "gst_limits": ActionProcessGstLimits(),
            "gst_periods": ActionProcessGstPeriods(),
            "gst_metrics": ActionProcessGstMetrics(),
            "gst_sort": ActionProcessGstSort(),
            "gst_filter_checkpoint": ActionProcessGstFilterCheckpoint(),
            "gst_review": ActionShowGstReview(),
            "gst_proceed": ActionRunGst(),
            # PVM
            "pvm_group": ActionProcessPvmGroup(),
            "pvm_period": ActionProcessPvmPeriod(),
            "pvm_method": ActionProcessPvmMethod(),
            "pvm_columns": ActionProcessPvmColumns(),
            "pvm_labels": ActionProcessPvmLabels(),
            "pvm_filter_checkpoint": ActionProcessPvmFilterCheckpoint(),
            "pvm_review": ActionShowPvmReview(),
            "pvm_proceed": ActionRunPvm(),
            # TOP
            "top_calc_mode": ActionProcessTopCalcMode(),
            "top_columns": ActionProcessTopColumns(),
            "top_labels": ActionProcessTopLabels(),
            "top_abc": ActionProcessTopAbc(),
            "top_bucket": ActionProcessTopBucket(),
            "top_filter_checkpoint": ActionProcessTopFilterCheckpoint(),
            "top_review": ActionShowTopReview(),
            "top_proceed": ActionRunTop(),
            # Churn
            "churn_columns": ActionProcessChurnColumns(),
            "churn_groups": ActionProcessChurnGroups(),
            "churn_labels": ActionProcessChurnLabels(),
            "churn_filter_checkpoint": ActionProcessChurnFilterCheckpoint(),
            "churn_review": ActionShowChurnReview(),
            "churn_proceed": ActionRunChurn(),
            # Bubble Scatter
            "bs_period_calc": ActionProcessBsPeriodCalc(),
            "bs_value_columns": ActionProcessBsValueColumns(),
            "bs_groups": ActionProcessBsGroups(),
            "bs_labels": ActionProcessBsLabels(),
            "bs_display_options": ActionProcessBsDisplayOptions(),
            "bs_filter_checkpoint": ActionProcessBsFilterCheckpoint(),
            "bs_review": ActionShowBsReview(),
            "bs_proceed": ActionRunBubble(),
            # OPOS
            "opos_snapshots": ActionProcessOposSnapshots(),
            "opos_columns": ActionProcessOposColumns(),
            "opos_missing_due_date": ActionProcessOposMissingDueDate(),
            "opos_display_buckets": ActionProcessOposDisplayBuckets(),
            "opos_sort": ActionProcessOposSort(),
            "opos_buckets": ActionProcessOposBuckets(),
            "opos_top_bucket": ActionProcessOposBuckets(),
            "opos_filter_checkpoint": ActionProcessOposFilterCheckpoint(),
            "opos_post_output": ActionProcessOposPostOutput(),
            "strand_post_output": ActionProcessStrandPostOutput(),
            "opos_proceed": ActionRunOpos(),
            # Fixed assets rollforward
            "fa_rollf_files": ActionProcessFaRollfFiles(),
            "fa_rollf_columns": ActionProcessFaRollfColumns(),
            "fa_rollf_grouping": ActionProcessFaRollfGrouping(),
            "fa_filter_checkpoint": ActionProcessFaFilterCheckpoint(),
            # FTE
            "fte_files": ActionProcessFteFiles(),
            "fte_columns": ActionProcessFteColumns(),
            "fte_grouping": ActionProcessFteGrouping(),
            "fte_filter_checkpoint": ActionProcessFteFilterCheckpoint(),
            "strand_run_target": ActionProcessStrandRunTarget(),
            # Filters
            "filter_start": ActionStartFilter(),
            "filter_rule": ActionProcessFilterRule(),
            "filter_done": ActionFinishFilters(),
            # Post-output
            "next_action": ActionProcessNextAction(),
            # Coming soon stubs
            "coming_soon": ActionProcessComingSoon(),
            # Databook
            "databook_susa_format": ActionProcessDatabookSusaFormat(),
            "databook_master_upload": ActionProcessDatabookMasterUpload(),
            "databook_entity_count": ActionProcessDatabookEntityCount(),
            "databook_entities": ActionProcessDatabookEntities(),
            "databook_susa_options": ActionProcessDatabookSusaInterpretation(),
            "databook_ap_ar_digits": ActionProcessDatabookApArDigits(),
            "databook_susa_column_mapper": ActionProcessDatabookColumnMapping(),
            "databook_consolidation_account": ActionProcessDatabookConsolidationAccount(),
            "databook_consolidation_level": ActionProcessDatabookConsolidationLevel(),
            "databook_consolidation_upload": ActionProcessDatabookConsolidationUpload(),
            "databook_adjustments": ActionProcessDatabookAdjustments(),
            "databook_adjustments_upload": ActionProcessDatabookAdjustmentsUpload(),
            "databook_l4_sort": ActionProcessDatabookL4Sort(),
            "databook_fs_extract_choice": ActionProcessDatabookFsExtractChoice(),
            "databook_fs_extract_placeholder": ActionProcessDatabookFsExtractPlaceholder(),
            "databook_fs_upload": ActionProcessDatabookFsUpload(),
            "databook_fs_review_upload": ActionProcessDatabookFsReviewUpload(),
            "databook_recon_order": ActionProcessDatabookReconOrder(),
            "databook_recon_labels": ActionProcessDatabookReconLabels(),
            "databook_extended_tables": ActionProcessDatabookExtendedTables(),
            "databook_master_next_action": ActionProcessDatabookMasterNextAction(),
            "databook_sort": ActionProcessDatabookSort(),
            "databook_proceed": ActionRunDatabookFinal(),
        }
        cls._handlers = instances
        return instances

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        lm = getattr(tracker, "latest_message", None)
        if not isinstance(lm, dict):
            lm = {}
        raw_text = (lm.get("text") or "").strip()

        # Undo messages parse as JSON with slots/next_action but no "card". If NLU
        # mis-routes them to submit_card, do not treat as a card submit (avoids
        # "Unknown card type ''" noise in chat).
        if raw_text.startswith("/undo_to_checkpoint"):
            return []

        payload = _parse_payload(tracker)
        if not isinstance(payload, dict):
            return []
        # Parsed undo JSON without card key (same shape as mis-routed undo).
        if "slots" in payload and "next_action" in payload and "card" not in payload:
            return []

        card = str(payload.get("card") or "").strip()
        if not card:
            return []

        handlers = self._build_handlers()
        handler = handlers.get(card)

        if handler is None:
            dispatcher.utter_message(
                text=f"Unknown card type '{card}'. Please use the provided form inputs."
            )
            return []

        # Run inline so SlotSet events from the handler are returned directly.
        # FollowupAction(handler.name()) did not persist custom-slot updates in practice.
        return handler.run(dispatcher, tracker, domain)


class ActionNluFallbackDispatch(Action):
    """
    When DIET/FallbackClassifier marks a message as nlu_fallback but the text is
    actually a structured /submit_card payload, dispatch it instead of utter_default.
    """

    def name(self) -> str:
        return "action_nlu_fallback_dispatch"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        lm = getattr(tracker, "latest_message", None)
        if not isinstance(lm, dict):
            lm = {}
        text = (lm.get("text") or "").strip()
        if text.startswith("/submit_card"):
            payload = _parse_payload(tracker)
            if isinstance(payload, dict) and payload.get("card"):
                return [FollowupAction("action_dispatch_card")]
        dispatcher.utter_message(response="utter_default")
        return []


# ─── Session ──────────────────────────────────────────────────────────────────


class ActionSetSessionId(Action):
    def name(self) -> str:
        return "action_set_session_id"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        # Align with the REST channel sender_id — the frontend uses it as session_id on /fdd/upload.
        desired = (tracker.sender_id or "").strip() or str(uuid.uuid4())[:8]
        existing = tracker.get_slot("session_id")
        if existing == desired:
            return []
        return [SlotSet("session_id", desired)]


class ActionResetAllSlots(Action):
    def name(self) -> str:
        return "action_reset_all_slots"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        return [AllSlotsReset()]


_UNDO_REDO_HANDLERS: dict[str, Any] | None = None


def _undo_redo_handlers() -> dict[str, Any]:
    """Lazy map of redo action names → handler instances (inline card re-display on undo)."""
    global _UNDO_REDO_HANDLERS
    if _UNDO_REDO_HANDLERS is not None:
        return _UNDO_REDO_HANDLERS
    _UNDO_REDO_HANDLERS = {
        "action_show_main_welcome": ActionShowMainWelcome(),
        "action_ask_dates_card": ActionAskDatesCard(),
        "action_navigate_folders": ActionNavigateFolders(),
        "action_ask_upload_file": ActionAskUploadFile(),
        "action_ask_sheet_name": ActionAskSheetName(),
        "action_process_fx_card": ActionProcessFxCard(),
        "action_process_desired_output": ActionProcessDesiredOutput(),
        "action_reshow_gst_invoice_mode": ActionReshowGstInvoiceMode(),
        "action_reshow_gst_calc_mode": ActionReshowGstCalcMode(),
        "action_reshow_gst_columns": ActionReshowGstColumns(),
        "action_reshow_gst_groups": ActionReshowGstGroups(),
        "action_reshow_gst_limits": ActionReshowGstLimits(),
        "action_reshow_gst_periods": ActionReshowGstPeriods(),
        "action_reshow_gst_metrics": ActionReshowGstMetrics(),
        "action_reshow_gst_sort": ActionReshowGstSort(),
        "action_enter_gst_filter_checkpoint": ActionEnterGstFilterCheckpoint(),
        "action_show_gst_review": ActionShowGstReview(),
        "action_reshow_pvm_columns": ActionReshowPvmColumns(),
        "action_show_pvm_review": ActionShowPvmReview(),
        "action_enter_pvm_filter_checkpoint": ActionEnterPvmFilterCheckpoint(),
        "action_reshow_top_columns": ActionReshowTopColumns(),
        "action_show_top_review": ActionShowTopReview(),
        "action_enter_top_filter_checkpoint": ActionEnterTopFilterCheckpoint(),
        "action_reshow_churn_columns": ActionReshowChurnColumns(),
        "action_show_churn_review": ActionShowChurnReview(),
        "action_enter_churn_filter_checkpoint": ActionEnterChurnFilterCheckpoint(),
        "action_show_bs_review": ActionShowBsReview(),
        "action_enter_bs_filter_checkpoint": ActionEnterBsFilterCheckpoint(),
        "action_show_fa_rollf_files": ActionShowFaRollfFiles(),
        "action_reshow_fa_rollf_columns": ActionReshowFaRollfColumns(),
        "action_show_fa_rollf_grouping": ActionShowFaRollfGrouping(),
        "action_enter_fa_filter_checkpoint": ActionEnterFaFilterCheckpoint(),
        "action_show_opos_snapshots": ActionShowOposSnapshots(),
        "action_reshow_opos_columns": ActionReshowOposColumns(),
        "action_enter_opos_filter_checkpoint": ActionEnterOposFilterCheckpoint(),
        "action_show_opos_review": ActionShowOposReview(),
        "action_show_fte_files": ActionShowFteFiles(),
        "action_reshow_fte_columns": ActionReshowFteColumns(),
        "action_show_fte_grouping": ActionShowFteGrouping(),
        "action_enter_fte_filter_checkpoint": ActionEnterFteFilterCheckpoint(),
        "action_enter_strand_run_target": ActionEnterStrandRunTarget(),
    }
    return _UNDO_REDO_HANDLERS


class ActionUndoToCheckpoint(Action):
    """Restore tracker to the snapshot taken before the last card submit, then re-show that card."""

    def name(self) -> str:
        return "action_undo_to_checkpoint"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        snap = payload.get("slots") or {}
        next_action = str(payload.get("next_action") or "action_listen")
        if not isinstance(snap, dict):
            snap = {}

        # Full reset then re-apply the saved snapshot (one step back). This matches
        # the frontend stack: each undo restores the exact tracker state from
        # immediately before that card submission, so project/file/headers stay intact.
        events: list[Any] = [AllSlotsReset()]
        for key, val in snap.items():
            events.append(SlotSet(key, val))
        sid = _fdd_session_id(tracker)
        if sid:
            events.append(SlotSet("session_id", sid))

        handler = _undo_redo_handlers().get(next_action)
        if handler is not None:
            extra = handler.run(dispatcher, tracker, domain) or []
            events.extend(extra)
            return events

        events.append(FollowupAction(next_action))
        return events


class ActionShowMainWelcome(Action):
    def name(self) -> str:
        return "action_show_main_welcome"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        dispatcher.utter_message(response="utter_main_welcome")
        return []


class ActionAskDatesCard(Action):
    def name(self) -> str:
        return "action_ask_dates_card"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        dispatcher.utter_message(response="utter_ask_dates")
        return []


# ─── Preload file (silent) ────────────────────────────────────────────────────

class ActionPreloadFile(Action):
    """
    Store a pre-uploaded workbook in slots WITHOUT advancing the dialogue.
    This is used when a user uploads an Excel before starting the bot (e.g. Exit Readiness tab).
    """

    def name(self) -> str:
        return "action_preload_file"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        file_id = payload.get("file_id")
        file_path = payload.get("file_path")
        headers = payload.get("headers")
        sheet_names = payload.get("sheet_names")

        events: list[Any] = []
        sid = _fdd_session_id(tracker)
        if sid:
            events.append(SlotSet("session_id", sid))
        if file_id:
            events.append(SlotSet("file_id", file_id))
        if file_path:
            events.append(SlotSet("file_path", file_path))
        if isinstance(headers, list) and headers:
            _remember_session_headers(tracker, headers)
            events.append(SlotSet("headers", headers))
        if isinstance(sheet_names, list):
            events.append(SlotSet("sheet_names", json.dumps(sheet_names)))
        elif isinstance(sheet_names, str):
            events.append(SlotSet("sheet_names", sheet_names))
        return events


# ─── Main topic actions ────────────────────────────────────────────────────────


class ActionProcessMainCard(Action):
    def name(self) -> str:
        return "action_process_main_card"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        events = []
        if "project_name" in payload:
            events.append(SlotSet("project_name", payload["project_name"]))
        if "group_name" in payload:
            events.append(SlotSet("group_name", payload["group_name"]))
        _remember_bs_settings(tracker, payload)
        # Advance to date settings
        dispatcher.utter_message(response="utter_ask_dates")
        return events


class ActionProcessDatesCard(Action):
    def name(self) -> str:
        return "action_process_dates_card"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        events = []
        ltm_val = str(payload.get("ltm_month") or "").strip()
        if ltm_val:
            _remember_session_ltm_month(tracker, ltm_val)
            events.append(SlotSet("ltm_month", ltm_val))
            events.append(SlotSet("ytd_month", str(payload.get("ytd_month") or ltm_val).strip()))
        fy_m: int | None = None
        fy_d: int | None = None
        if "fy_end_month" in payload:
            try:
                fy_m = int(float(payload["fy_end_month"]))
                events.append(SlotSet("fy_end_month", str(fy_m)))
            except (ValueError, TypeError):
                pass
        if "fy_end_day" in payload:
            try:
                fy_d = int(float(payload["fy_end_day"]))
                events.append(SlotSet("fy_end_day", float(fy_d)))
            except (ValueError, TypeError):
                pass
        if fy_m is not None and fy_d is not None:
            _remember_session_fy_end(tracker, fy_m, fy_d)
        # Legacy: full cut-off date from older cards → store as FY month/day only
        if "cutoff_date" in payload and "fy_end_month" not in payload:
            raw = str(payload.get("cutoff_date") or "").strip().replace("/", "-")
            segs = [x for x in raw.split("-") if x]
            try:
                if len(segs) >= 3:
                    events.append(SlotSet("fy_end_month", str(int(segs[1]))))
                    events.append(SlotSet("fy_end_day", float(int(segs[2]))))
                elif len(segs) == 2:
                    events.append(SlotSet("fy_end_month", str(int(segs[0]))))
                    events.append(SlotSet("fy_end_day", float(int(segs[1]))))
            except (ValueError, TypeError, IndexError):
                pass
        fy_raw = payload.get("first_fy", tracker.get_slot("first_fy"))
        fy_val: float | None = None
        if fy_raw not in (None, ""):
            try:
                fy_val = float(str(fy_raw).strip().replace(",", "."))
            except (ValueError, TypeError):
                fy_val = None
        if fy_val is not None:
            _remember_session_first_fy(tracker, fy_val)
            events.append(SlotSet("first_fy", fy_val))
            events.append(SlotSet("gst_first_fy_override", fy_val))
            events.append(SlotSet("pvm_first_fy_override", fy_val))
        # Outputs go to session upload folder; user downloads via file_attachment cards.
        dispatcher.utter_message(json_message=_build_output_type_card())
        return events


class ActionNavigateFolders(Action):
    """Legacy undo target — folder selection removed; go straight to output type."""

    def name(self) -> str:
        return "action_navigate_folders"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        dispatcher.utter_message(json_message=_build_output_type_card())
        return []


class ActionProcessFolderSelection(Action):
    """Legacy dispatch handler — folder selection removed."""

    def name(self) -> str:
        return "action_process_folder_selection"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        dispatcher.utter_message(json_message=_build_output_type_card())
        return []


class ActionProcessBuildDatabook(Action):
    def name(self) -> str:
        return "action_process_build_databook"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        output_type = payload.get("output_type", "databook")

        if output_type == "databook":
            dispatcher.utter_message(text="Let's build the Databook.")
            return [
                SlotSet("output_type", "databook"),
                FollowupAction("action_run_databook"),
            ]
        elif output_type == "revenue_databook":
            dispatcher.utter_message(text="Jumping straight to the Revenue Databook.")
            events = _emit_sales_upload_or_reuse(dispatcher, tracker, payload)
            events.append(SlotSet("output_type", "revenue_databook"))
            return events
        elif output_type == "creditor_debitor_aging":
            dispatcher.utter_message(text="Great! Let's configure the Creditor / Debitor Aging (OPOS).")
            dispatcher.utter_message(json_message=_opos_snapshots_card(tracker))
            return [SlotSet("output_type", "creditor_debitor_aging")]
        elif output_type == "fixed_assets_rollforward":
            dispatcher.utter_message(text="Great! Let's configure the Fixed Assets Rollforward.")
            dispatcher.utter_message(json_message=_fa_rollf_files_card(tracker))
            return [SlotSet("output_type", "fixed_assets_rollforward")]
        elif output_type == "fte_development":
            dispatcher.utter_message(text="Great! Let's configure FTE Development.")
            dispatcher.utter_message(json_message=_fte_files_card(tracker))
            return [SlotSet("output_type", "fte_development")]
        else:
            dispatcher.utter_message(text=f"Unknown output type '{output_type}'. Please try again.")
            dispatcher.utter_message(json_message=_build_output_type_card())
            return []


class ActionProcessReuseSalesFile(Action):
    """Yes/No after Revenue Databook: reuse existing file_id or show upload card."""

    def name(self) -> str:
        return "action_process_reuse_sales_file"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        choice = str(payload.get("reuse_previous_file", "")).lower()

        if choice == "yes":
            if not tracker.get_slot("file_id"):
                dispatcher.utter_message(
                    text="No previous workbook is stored in this session. Please upload your Excel file."
                )
                dispatcher.utter_message(json_message=_revenue_sales_file_upload_card())
                return []
            dispatcher.utter_message(
                text="Using your existing workbook for this step."
            )
            return [FollowupAction("action_ask_sheet_name")]

        if choice == "no":
            dispatcher.utter_message(json_message=_revenue_sales_file_upload_card())
            return [
                SlotSet("file_id", None),
                SlotSet("file_path", None),
                SlotSet("sheet_names", None),
                SlotSet("headers", None),
            ]

        dispatcher.utter_message(
            text="Please pick Yes or No above so we know whether to reuse your last upload."
        )
        return []


class ActionUploadFile(Action):
    def name(self) -> str:
        return "action_upload_file"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        file_id = str(payload.get("file_id") or tracker.get_slot("file_id") or "").strip()
        file_path = payload.get("file_path") or tracker.get_slot("file_path") or ""

        if not file_id:
            dispatcher.utter_message(text="No file was received. Please try uploading again.")
            return []

        sheet_names = payload.get("sheet_names") or []
        if isinstance(sheet_names, str):
            try:
                sheet_names = json.loads(sheet_names)
            except (json.JSONDecodeError, TypeError):
                sheet_names = []
        if not isinstance(sheet_names, list):
            sheet_names = []
        if not sheet_names:
            raw_slot = tracker.get_slot("sheet_names") or "[]"
            try:
                sheet_names = json.loads(raw_slot) if isinstance(raw_slot, str) else list(raw_slot or [])
            except (json.JSONDecodeError, TypeError):
                sheet_names = []

        sid = _fdd_session_id(tracker)
        upload_headers = _normalize_header_list(payload.get("headers"))
        if upload_headers:
            _remember_session_headers(tracker, upload_headers)
        events: list[Any] = [
            SlotSet("file_id", file_id),
            SlotSet("file_path", file_path or ""),
            SlotSet("sheet_names", json.dumps(sheet_names)),
        ]
        if upload_headers:
            events.append(SlotSet("headers", upload_headers))
        _remember_bs_settings(tracker, {"file_id": file_id, "file_path": file_path or ""})
        if sid:
            events.insert(0, SlotSet("session_id", sid))

        if not sheet_names:
            dispatcher.utter_message(
                text="No worksheet names were found in the upload. Please upload the Excel file again."
            )
            dispatcher.utter_message(json_message=_revenue_sales_file_upload_card())
            return events

        options = [{"label": str(s), "value": str(s)} for s in sheet_names]
        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "sheet_name",
                "title": "Sheet Name",
                "subtitle": "Please select the data sheet in your Excel file.",
                "inputs": [
                    {
                        "id": "sheet_name",
                        "type": "dropdown",
                        "label": "What is the data sheet's name?",
                        "options": options,
                        "default": str(sheet_names[0]),
                        "required": True,
                    }
                ],
                "submit_label": "Continue",
            }
        )
        return events


class ActionAskUploadFile(Action):
    """Re-show the revenue sales file upload card (e.g. after undo)."""

    def name(self) -> str:
        return "action_ask_upload_file"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        return _emit_sales_upload_or_reuse(dispatcher, tracker, payload)


class ActionAskSheetName(Action):
    """Show sheet picker with names from the uploaded workbook."""

    def name(self) -> str:
        return "action_ask_sheet_name"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        raw = tracker.get_slot("sheet_names") or "[]"
        try:
            sheets = json.loads(raw) if isinstance(raw, str) else list(raw or [])
        except (json.JSONDecodeError, TypeError):
            sheets = []
        if not sheets:
            dispatcher.utter_message(
                text="No worksheet names were found in the upload. Please upload the Excel file again."
            )
            dispatcher.utter_message(json_message=_revenue_sales_file_upload_card())
            return []

        options = [{"label": str(s), "value": str(s)} for s in sheets]
        default_sheet = str(sheets[0])
        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "sheet_name",
                "title": "Sheet Name",
                "subtitle": "Please select the data sheet in your Excel file.",
                "inputs": [
                    {
                        "id": "sheet_name",
                        "type": "dropdown",
                        "label": "What is the data sheet's name?",
                        "options": options,
                        "default": default_sheet,
                        "required": True,
                    }
                ],
                "submit_label": "Continue",
            }
        )
        return []


class ActionGetHeaders(Action):
    def name(self) -> str:
        return "action_get_headers"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        sheet_name = payload.get("sheet_name", "")
        file_id = str(payload.get("file_id") or tracker.get_slot("file_id") or "").strip()

        if not file_id:
            dispatcher.utter_message(text="No file found. Please upload your Excel file first.")
            dispatcher.utter_message(json_message=_revenue_sales_file_upload_card())
            return []

        session_id = str(payload.get("session_id") or _fdd_session_id(tracker) or "").strip()
        params = {"sheet_name": sheet_name, "session_id": session_id}
        result = _fdd_get(f"/api/v1/fdd/headers/{file_id}", params)
        headers = result.get("headers") or []

        if result.get("success") is False or not headers:
            step = result.get("step") or "UNKNOWN"
            user_msg = result.get("user_message") or result.get("message") or ""
            avail = result.get("available_sheets") or []
            resolved = result.get("resolved_sheet")
            lines = [
                f"[Header read · step: {step}]",
                user_msg or "The server could not read column headers for that sheet.",
            ]
            if avail:
                preview = ", ".join(avail[:25])
                suffix = " …" if len(avail) > 25 else ""
                lines.append(f"Worksheets in this file: {preview}{suffix}")
            dispatcher.utter_message(text=" ".join(lines))
            return [SlotSet("sheet_name", sheet_name)]

        used_sheet = result.get("resolved_sheet") or sheet_name
        _remember_session_headers(tracker, headers)
        _remember_bs_settings(
            tracker,
            {"file_id": file_id, "sheet_name": used_sheet, "session_id": session_id},
        )
        dispatcher.utter_message(
            text=f"Successfully read {len(headers)} columns from sheet «{used_sheet}»."
        )
        # Advance: ask about FX rate
        dispatcher.utter_message(response="utter_ask_fx_rate")
        events = [
            SlotSet("file_id", file_id),
            SlotSet("sheet_name", used_sheet),
            SlotSet("headers", headers),
        ]
        if session_id:
            events.append(SlotSet("session_id", session_id))
        return events


class ActionProcessFxCard(Action):
    def name(self) -> str:
        return "action_process_fx_card"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        apply_fx = payload.get("apply_fx") in ("yes", True, "true")
        headers = _headers_from_sources(tracker, payload)
        events = [SlotSet("apply_fx", apply_fx)]

        if apply_fx and headers:
            dispatcher.utter_message(
                json_message={
                    "type": "adaptive_card",
                    "card": "fx_col_select",
                    "title": "FX Rate Column",
                    "inputs": [
                        {
                            "id": "fx_col",
                            "type": "dropdown",
                            "label": "Please select the correct FX-rate column:",
                            "options": [{"label": h, "value": h} for h in headers],
                        }
                    ],
                    "submit_label": "Continue",
                    "next_card": "desired_output",
                }
            )
        else:
            dispatcher.utter_message(
                json_message={
                    "type": "adaptive_card",
                    "card": "desired_output",
                    "title": "Output Selection",
                    "subtitle": "What kind of output would you like to create?",
                    "inputs": [
                        {
                            "id": "desired_output",
                            "type": "radio",
                            "label": "Select output type",
                            "options": [
                                {"label": "TOP Report", "value": "top_report"},
                                {"label": "PVM Analysis", "value": "pvm_analysis"},
                                {"label": "General Sales Table", "value": "general_sales_table"},
                                {"label": "Churn / ARR Bridge", "value": "churn"},
                                {"label": "Bubble Scatter Plot", "value": "bubble_scatter"},
                            ],
                        }
                    ],
                    "submit_label": "Continue",
                }
            )
        return events


class ActionProcessDesiredOutput(Action):
    def name(self) -> str:
        return "action_process_desired_output"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        fx_col = payload.get("fx_col")
        desired = payload.get("desired_output", "")
        events = [SlotSet("desired_output", desired)]
        events.extend(_hydrate_column_roles_from_session_cache(tracker))
        events.extend(_propagate_shared_filters(tracker))

        if fx_col:
            events.append(SlotSet("fx_col", fx_col))

        headers = _headers_from_sources(tracker, payload)

        if desired == "general_sales_table":
            dispatcher.utter_message(text="Great! Let's configure the General Sales Table.")
            dispatcher.utter_message(
                json_message=_build_gst_table_name_card()
            )
        elif desired == "pvm_analysis":
            dispatcher.utter_message(text="Great! Let's configure the PVM Analysis.")
            dispatcher.utter_message(
                json_message=_build_pvm_group_card(
                    headers, str(_column_default(tracker, "pvm_group_col") or "")
                )
            )
        elif desired == "top_report":
            dispatcher.utter_message(text="Great! Let's configure the TOP Report.")
            dispatcher.utter_message(
                json_message=_build_top_calc_mode_card()
            )
        elif desired == "churn":
            dispatcher.utter_message(text="Great! Let's configure the Churn / ARR Bridge.")
            _emit_churn_columns_card(dispatcher, tracker, payload)
        elif desired == "bubble_scatter":
            dispatcher.utter_message(text="Great! Let's configure the Bubble Scatter Plot.")
            dispatcher.utter_message(
                json_message=_build_bs_period_calc_card(
                    headers,
                    {
                        "bs_invoice_col": _column_default(tracker, "bs_invoice_col"),
                        "bs_start_col": _column_default(tracker, "bs_start_col"),
                        "bs_end_col": _column_default(tracker, "bs_end_col"),
                    },
                )
            )

        return events


# ─── GST flow ─────────────────────────────────────────────────────────────────


def _build_gst_table_name_card() -> dict:
    return {
        "type": "adaptive_card",
        "card": "gst_invoice_mode",
        "title": "General Sales Table Setup",
        "inputs": [
            {
                "id": "gst_table_name",
                "type": "text",
                "label": "Table name",
                "placeholder": "Sales by product groups",
                "default": "Sales by product groups",
            },
            {
                "id": "gst_total_label",
                "type": "text",
                "label": "Label for total row",
                "placeholder": "Gross sales / GP / GM",
                "default": "Gross sales / GP / GM",
            },
            {
                "id": "gst_invoice_mapping_mode",
                "type": "radio",
                "label": "How should the invoice period assignment be determined?",
                "options": [
                    {"label": "Calculate based on a date column", "value": "date"},
                    {"label": "Use an existing column that assigns invoices to a financial period", "value": "year"},
                ],
            },
        ],
        "submit_label": "Continue",
    }


class ActionProcessGstInvoiceMode(Action):
    def name(self) -> str:
        return "action_process_gst_invoice_mode"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        events = [
            SlotSet("gst_table_name", payload.get("gst_table_name", "Sales by product groups")),
            SlotSet("gst_total_label", payload.get("gst_total_label", "Gross sales / GP / GM")),
            SlotSet("gst_invoice_mapping_mode", payload.get("gst_invoice_mapping_mode", "date")),
        ]
        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "gst_calc_mode",
                "title": "Calculation Mode",
                "inputs": [
                    {
                        "id": "gst_calc_mode",
                        "type": "radio",
                        "label": "Select calculation mode",
                        "options": [
                            {"label": "Invoiced amounts", "value": "invoice"},
                            {"label": "Accrual-based revenue", "value": "accrual"},
                        ],
                    },
                    {
                        "id": "gst_profit_mode",
                        "type": "radio",
                        "label": "Gross profit calculation",
                        "options": [
                            {"label": "Use a cost column (GP = Revenue − Cost)", "value": "cost"},
                            {"label": "Use an existing gross profit column", "value": "profit"},
                            {"label": "No gross profit column available", "value": "none"},
                        ],
                    },
                ],
                "submit_label": "Continue",
            }
        )
        return events


class ActionProcessGstCalcMode(Action):
    def name(self) -> str:
        return "action_process_gst_calc_mode"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        calc_mode = payload.get("gst_calc_mode", "invoice")
        profit_mode = payload.get("gst_profit_mode", "cost")
        events = [
            SlotSet("gst_calc_mode", calc_mode),
            SlotSet("gst_profit_mode", profit_mode),
        ]
        _emit_gst_columns_card(dispatcher, tracker, payload)
        return events


class ActionProcessGstColumns(Action):
    def name(self) -> str:
        return "action_process_gst_columns"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        card = str(payload.get("card") or "").strip()
        if card != "gst_columns":
            _emit_gst_columns_card(dispatcher, tracker)
            return []

        col_keys = (
            "gst_revenue_col",
            "gst_invoice_col",
            "gst_cost_col",
            "gst_profit_col",
            "gst_start_col",
            "gst_end_col",
        )
        events: list[Any] = []
        for k in col_keys:
            if k in payload:
                events.extend(_sync_column_role_slots(tracker, k, payload.get(k)))

        _emit_gst_groups_card(dispatcher, tracker)
        return events


class ActionProcessGstGroups(Action):
    def name(self) -> str:
        return "action_process_gst_groups"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        if not _payload_is_card_submit(payload, "gst_groups"):
            return []
        events = _sync_column_role_slots(tracker, "gst_group_col_1", payload.get("gst_group_col_1"))
        events += [
            SlotSet("gst_group_col_2", payload.get("gst_group_col_2") or None),
            SlotSet("gst_group_col_3", payload.get("gst_group_col_3") or None),
        ]

        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "gst_limits",
                "title": "Item Limits per Level",
                "subtitle": "Configure how many items appear before grouping into 'Other'.",
                "inputs": [
                    {"id": "gst_max_items_l1", "type": "number", "label": "Max items — Level 1",
                     "placeholder": "10", "default": "10"},
                    {"id": "gst_other_label_l1", "type": "text", "label": "Label for 'Other' — Level 1",
                     "placeholder": "Other", "default": "Other"},
                    {"id": "gst_max_items_l2", "type": "number", "label": "Max items — Level 2",
                     "placeholder": "8", "default": "8"},
                    {"id": "gst_other_label_l2", "type": "text", "label": "Label for 'Other' — Level 2",
                     "placeholder": "Other", "default": "Other"},
                    {"id": "gst_max_items_l3", "type": "number", "label": "Max items — Level 3",
                     "placeholder": "5", "default": "5"},
                    {"id": "gst_other_label_l3", "type": "text", "label": "Label for 'Other' — Level 3",
                     "placeholder": "Other", "default": "Other"},
                ],
                "submit_label": "Continue",
            }
        )
        return events


class ActionProcessGstLimits(Action):
    def name(self) -> str:
        return "action_process_gst_limits"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        events = [
            SlotSet("gst_max_items_l1", float(payload.get("gst_max_items_l1", 10))),
            SlotSet("gst_other_label_l1", payload.get("gst_other_label_l1", "Other")),
            SlotSet("gst_max_items_l2", float(payload.get("gst_max_items_l2", 8))),
            SlotSet("gst_other_label_l2", payload.get("gst_other_label_l2", "Other")),
            SlotSet("gst_max_items_l3", float(payload.get("gst_max_items_l3", 5))),
            SlotSet("gst_other_label_l3", payload.get("gst_other_label_l3", "Other")),
        ]

        ltm_month = tracker.get_slot("ltm_month") or ""
        period_inputs = [
            {
                "id": "gst_periods",
                "type": "multi_select",
                "label": "Would you also like to include YTD and/or LTM in the analysis?",
                "required": False,
                "options": [
                    {"label": "YTD", "value": "ytd"},
                    {"label": "LTM", "value": "ltm"},
                ],
            },
            {
                "id": "gst_deltas",
                "type": "multi_select",
                "label": "Show delta or annual growth rate for YTD/LTM columns?",
                "required": False,
                "options": [
                    {"label": "YTD delta", "value": "ytd_delta"},
                    {"label": "LTM delta", "value": "ltm_delta"},
                    {"label": "YTD CAGR", "value": "ytd_cagr"},
                    {"label": "LTM CAGR", "value": "ltm_cagr"},
                ],
            },
        ]

        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "gst_periods",
                "title": "Additional Period Views",
                "inputs": period_inputs,
                "submit_label": "Continue",
            }
        )
        return events


class ActionProcessGstPeriods(Action):
    def name(self) -> str:
        return "action_process_gst_periods"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        periods = payload.get("gst_periods", [])
        deltas = payload.get("gst_deltas", [])

        events = [
            SlotSet("gst_show_ytd", "ytd" in periods),
            SlotSet("gst_show_ltm", "ltm" in periods),
            SlotSet("gst_ytd_delta", "ytd_delta" in deltas),
            SlotSet("gst_ltm_delta", "ltm_delta" in deltas),
            SlotSet("gst_ytd_cagr", "ytd_cagr" in deltas),
            SlotSet("gst_ltm_cagr", "ltm_cagr" in deltas),
        ]

        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "gst_metrics",
                "title": "Additional Metrics",
                "inputs": [
                    {
                        "id": "gst_metrics",
                        "type": "multi_select",
                        "label": "Would you like to include gross profit and/or gross margin in the analysis?",
                        "required": False,
                        "options": [
                            {"label": "Gross Profit (GP)", "value": "gp"},
                            {"label": "Gross Margin % (GM)", "value": "gm"},
                        ],
                    }
                ],
                "submit_label": "Continue",
            }
        )
        return events


class ActionProcessGstMetrics(Action):
    def name(self) -> str:
        return "action_process_gst_metrics"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        metrics = payload.get("gst_metrics", [])
        first_fy = _tracker_first_fy_int(tracker)

        events = [
            SlotSet("gst_show_gp", "gp" in metrics),
            SlotSet("gst_show_gm", "gm" in metrics),
        ]

        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "gst_sort",
                "title": "Sorting & Time Period",
                "inputs": [
                    {
                        "id": "gst_sort_mode",
                        "type": "radio",
                        "label": "How would you like the table to be sorted?",
                        "options": [
                            {"label": "By revenue for the most recent fiscal year", "value": "FY_LAST"},
                            {"label": "Alphabetically", "value": "ALPHABETICAL"},
                        ],
                    },
                    {
                        "id": "gst_first_fy_override",
                        "type": "number",
                        "label": "Please confirm the first fiscal year to include:",
                        "default": str(int(first_fy)),
                        "placeholder": str(int(first_fy)),
                    },
                ],
                "submit_label": "Continue",
            }
        )
        return events


class ActionProcessGstSort(Action):
    def name(self) -> str:
        return "action_process_gst_sort"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        events = [
            SlotSet("gst_sort_mode", payload.get("gst_sort_mode", "FY_LAST")),
            SlotSet(
                "gst_first_fy_override",
                float(payload.get("gst_first_fy_override", _tracker_first_fy_int(tracker))),
            ),
        ]
        dispatcher.utter_message(json_message=_gst_filter_checkpoint_card(tracker))
        return events


class ActionEnterGstFilterCheckpoint(Action):
    """Re-show the GST filter checkpoint card (used after delete-all or invalid choice)."""

    def name(self) -> str:
        return "action_enter_gst_filter_checkpoint"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        dispatcher.utter_message(json_message=_gst_filter_checkpoint_card(tracker))
        return []


class ActionProcessGstFilterCheckpoint(Action):
    """Handle choices on the post-sort GST filter checkpoint card."""

    def name(self) -> str:
        return "action_process_gst_filter_checkpoint"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        choice = str(payload.get("gst_filter_manage_choice", "")).lower()

        if choice == "delete_all":
            return [
                SlotSet("gst_filter_rules_json", "[]"),
                SlotSet("gst_filter_rules_display", ""),
                FollowupAction("action_enter_gst_filter_checkpoint"),
            ]
        if choice == "add_rule":
            return [
                SlotSet("active_filter_context", "gst"),
                FollowupAction("action_start_filter"),
            ]
        if choice == "keep":
            return [FollowupAction("action_show_gst_review")]

        dispatcher.utter_message(
            text="Please choose option 1, 2, or 3 so we know how to handle your filter rules."
        )
        return [FollowupAction("action_enter_gst_filter_checkpoint")]


class ActionEnterPvmFilterCheckpoint(Action):
    def name(self) -> str:
        return "action_enter_pvm_filter_checkpoint"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        dispatcher.utter_message(json_message=_pvm_filter_checkpoint_card(tracker))
        return []


class ActionProcessPvmFilterCheckpoint(Action):
    """Handle choices on the PVM filter checkpoint card (after labels, before review)."""

    def name(self) -> str:
        return "action_process_pvm_filter_checkpoint"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        choice = str(payload.get("pvm_filter_manage_choice", "")).lower()

        if choice == "delete_all":
            return [
                SlotSet("pvm_filter_rules_json", "[]"),
                SlotSet("pvm_filter_rules_display", ""),
                FollowupAction("action_enter_pvm_filter_checkpoint"),
            ]
        if choice == "add_rule":
            return [
                SlotSet("active_filter_context", "pvm"),
                FollowupAction("action_start_filter"),
            ]
        if choice == "keep":
            return [FollowupAction("action_show_pvm_review")]

        dispatcher.utter_message(
            text="Please choose option 1, 2, or 3 so we know how to handle your filter rules."
        )
        return [FollowupAction("action_enter_pvm_filter_checkpoint")]


class ActionEnterTopFilterCheckpoint(Action):
    def name(self) -> str:
        return "action_enter_top_filter_checkpoint"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        dispatcher.utter_message(json_message=_top_filter_checkpoint_card(tracker))
        return []


class ActionProcessTopFilterCheckpoint(Action):
    """Handle choices on the TOP filter checkpoint card (after bucket step, before review)."""

    def name(self) -> str:
        return "action_process_top_filter_checkpoint"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        choice = str(payload.get("top_filter_manage_choice", "")).lower()

        if choice == "delete_all":
            return [
                SlotSet("top_filter_rules_json", "[]"),
                SlotSet("top_filter_rules_display", ""),
                FollowupAction("action_enter_top_filter_checkpoint"),
            ]
        if choice == "add_rule":
            return [
                SlotSet("active_filter_context", "top"),
                FollowupAction("action_start_filter"),
            ]
        if choice == "keep":
            return [FollowupAction("action_show_top_review")]

        dispatcher.utter_message(
            text="Please choose option 1, 2, or 3 so we know how to handle your filter rules."
        )
        return [FollowupAction("action_enter_top_filter_checkpoint")]


class ActionShowGstReview(Action):
    def name(self) -> str:
        return "action_show_gst_review"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        desired = str(tracker.get_slot("desired_output") or "").strip()
        if desired == "pvm_analysis":
            return [FollowupAction("action_show_pvm_review")]
        if desired == "top_report":
            return [FollowupAction("action_show_top_review")]
        none_opt = [{"label": "(none)", "value": ""}]
        header_opts = _header_opts_for_column_card(
            tracker,
            None,
            "gst_revenue_col",
            "gst_invoice_col",
            "gst_cost_col",
            "gst_profit_col",
            "gst_start_col",
            "gst_end_col",
            "gst_group_col_1",
            "gst_group_col_2",
            "gst_group_col_3",
        )
        review_fy = _first_fy_for_gst_review_card(tracker)

        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "gst_proceed",
                "compact": True,
                "title": "GST — Review",
                "subtitle": (
                    f"{_revenue_slot(tracker, 'project_name') or '—'}"
                    f" · {_revenue_slot(tracker, 'group_name') or '—'}"
                ),
                "inputs": [
                    {"id": "project_name", "type": "text", "label": "Project", "span": 1,
                     "default": _revenue_slot(tracker, "project_name")},
                    {"id": "group_name", "type": "text", "label": "Group", "span": 1,
                     "default": _revenue_slot(tracker, "group_name")},
                    {"id": "gst_table_name", "type": "text", "label": "Table title", "span": 2,
                     "default": _revenue_slot(tracker, "gst_table_name", default="Sales by product groups")},
                    {"id": "gst_total_label", "type": "text", "label": "Total row label", "span": 2,
                     "default": _revenue_slot(tracker, "gst_total_label", default="Gross sales / GP / GM")},
                    {"id": "gst_invoice_mapping_mode", "type": "radio", "label": "Invoice period", "layout": "split", "span": 2,
                     "options": [
                         {"label": "Date column", "value": "date"},
                         {"label": "Period column", "value": "year"},
                     ],
                     "default": _revenue_slot(tracker, "gst_invoice_mapping_mode", default="date")},
                    {"id": "gst_calc_mode", "type": "radio", "label": "Calc mode", "layout": "split", "span": 2,
                     "options": [
                         {"label": "Invoice", "value": "invoice"},
                         {"label": "Accrual", "value": "accrual"},
                     ],
                     "default": _revenue_slot(tracker, "gst_calc_mode", default="invoice")},
                    {"id": "gst_profit_mode", "type": "radio", "label": "GP mode", "layout": "split", "span": 2,
                     "options": [
                         {"label": "Cost column", "value": "cost"},
                         {"label": "Profit column", "value": "profit"},
                         {"label": "No GP column", "value": "none"},
                     ],
                     "default": _revenue_slot(tracker, "gst_profit_mode", default="cost")},
                    {"id": "gst_revenue_col", "type": "dropdown", "label": "Revenue", "rowGroup": "gst_amount_cols",
                     "options": header_opts, "default": _column_default(tracker, "gst_revenue_col")},
                    {"id": "gst_invoice_col", "type": "dropdown", "label": "Date / period", "rowGroup": "gst_amount_cols",
                     "options": header_opts, "default": _column_default(tracker, "gst_invoice_col")},
                    {"id": "gst_cost_col", "type": "dropdown", "label": "COGS column", "span": 2,
                     "options": none_opt + header_opts, "required": False,
                     "default": _column_default(tracker, "gst_cost_col"),
                     "showWhen": {"field": "gst_profit_mode", "value": "cost"}},
                    {"id": "gst_profit_col", "type": "dropdown", "label": "GP column", "span": 2,
                     "options": none_opt + header_opts, "required": False,
                     "default": _column_default(tracker, "gst_profit_col"),
                     "showWhen": {"field": "gst_profit_mode", "value": "profit"}},
                    {"id": "gst_start_col", "type": "dropdown", "label": "Start (accr.)", "rowGroup": "gst_accrual_cols",
                     "options": none_opt + header_opts, "required": False,
                     "default": _column_default(tracker, "gst_start_col"),
                     "showWhen": {"field": "gst_calc_mode", "value": "accrual"}},
                    {"id": "gst_end_col", "type": "dropdown", "label": "End (accr.)", "rowGroup": "gst_accrual_cols",
                     "options": none_opt + header_opts, "required": False,
                     "default": _column_default(tracker, "gst_end_col"),
                     "showWhen": {"field": "gst_calc_mode", "value": "accrual"}},
                    {"id": "gst_group_col_1", "type": "dropdown", "label": "L1", "rowGroup": "gst_group_cols",
                     "options": header_opts, "default": _revenue_slot(tracker, "gst_group_col_1")},
                    {"id": "gst_group_col_2", "type": "dropdown", "label": "L2", "rowGroup": "gst_group_cols",
                     "options": none_opt + header_opts, "required": False,
                     "default": _revenue_slot(tracker, "gst_group_col_2")},
                    {"id": "gst_group_col_3", "type": "dropdown", "label": "L3", "rowGroup": "gst_group_cols",
                     "options": none_opt + header_opts, "required": False,
                     "default": _revenue_slot(tracker, "gst_group_col_3")},
                    {"id": "gst_sort_mode", "type": "radio", "label": "Sort", "layout": "split", "span": 2,
                     "options": [
                         {"label": "Latest FY revenue", "value": "FY_LAST"},
                         {"label": "A–Z", "value": "ALPHABETICAL"},
                     ],
                     "default": _revenue_slot(tracker, "gst_sort_mode", default="FY_LAST")},
                    {"id": "gst_first_fy_override", "type": "number", "label": "First FY", "span": 2,
                     "default": str(int(review_fy))},
                ],
                "submit_label": "Proceed",
            }
        )
        return []


class ActionRunGst(Action):
    def name(self) -> str:
        return "action_run_gst"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        s = tracker.slots
        payload = _parse_payload(tracker)

        def pick(key: str, default=None):
            v = payload.get(key, None)
            return default if v is None or v == "" else v

        # Allow editing on the review card (gst_proceed) by accepting payload overrides.
        project_name = pick("project_name", s.get("project_name", ""))
        group_name = pick("group_name", s.get("group_name", ""))
        gst_table_name = pick("gst_table_name", s.get("gst_table_name", "Sales by product groups"))
        gst_total_label = pick("gst_total_label", s.get("gst_total_label", "Gross sales / GP / GM"))
        gst_invoice_mapping_mode = pick("gst_invoice_mapping_mode", s.get("gst_invoice_mapping_mode", "date"))
        gst_calc_mode = pick("gst_calc_mode", s.get("gst_calc_mode", "invoice"))
        gst_profit_mode = pick("gst_profit_mode", s.get("gst_profit_mode", "cost"))
        gst_invoice_col = pick("gst_invoice_col", s.get("gst_invoice_col", ""))
        gst_start_col = pick("gst_start_col", s.get("gst_start_col") or "")
        gst_end_col = pick("gst_end_col", s.get("gst_end_col") or "")
        gst_revenue_col = pick("gst_revenue_col", s.get("gst_revenue_col", ""))
        gst_cost_col = pick("gst_cost_col", s.get("gst_cost_col") or "")
        gst_profit_col = pick("gst_profit_col", s.get("gst_profit_col") or "")
        gst_group_col_1 = pick("gst_group_col_1", s.get("gst_group_col_1"))
        gst_group_col_2 = pick("gst_group_col_2", s.get("gst_group_col_2"))
        gst_group_col_3 = pick("gst_group_col_3", s.get("gst_group_col_3"))
        gst_sort_mode = pick("gst_sort_mode", s.get("gst_sort_mode", "FY_LAST"))
        if gst_sort_mode == "alpha":
            gst_sort_mode = "ALPHABETICAL"
        try:
            gst_first_fy_override = int(
                float(
                    pick(
                        "gst_first_fy_override",
                        s.get("gst_first_fy_override") or _first_fy_for_gst_review_card(tracker),
                    ),
                ),
            )
        except Exception:
            gst_first_fy_override = _first_fy_for_gst_review_card(tracker)

        slot_events = [
            SlotSet("project_name", project_name),
            SlotSet("group_name", group_name),
            SlotSet("gst_table_name", gst_table_name),
            SlotSet("gst_total_label", gst_total_label),
            SlotSet("gst_invoice_mapping_mode", gst_invoice_mapping_mode),
            SlotSet("gst_calc_mode", gst_calc_mode),
            SlotSet("gst_profit_mode", gst_profit_mode),
            SlotSet("gst_revenue_col", gst_revenue_col),
            SlotSet("gst_invoice_col", gst_invoice_col),
            SlotSet("gst_cost_col", gst_cost_col),
            SlotSet("gst_profit_col", gst_profit_col),
            SlotSet("gst_start_col", gst_start_col),
            SlotSet("gst_end_col", gst_end_col),
            SlotSet("gst_group_col_1", gst_group_col_1),
            SlotSet("gst_group_col_2", gst_group_col_2),
            SlotSet("gst_group_col_3", gst_group_col_3),
            SlotSet("gst_sort_mode", gst_sort_mode),
            SlotSet("gst_first_fy_override", gst_first_fy_override),
        ]

        body = {
            "session_id": _fdd_session_id(tracker),
            "file_id": s.get("file_id"),
            "config": {
                "title": project_name,
                "table": gst_table_name,
                "company": group_name,
                "total_label": gst_total_label,
                "sheet_name": s.get("sheet_name", ""),
                "base_sheet_name": "General sales table",
                "formula_mode": True,
                "invoice_mapping_mode": gst_invoice_mapping_mode,
                "calc_mode": gst_calc_mode,
                "profit_mode": gst_profit_mode,
                "invoice_col": gst_invoice_col,
                "start_col": gst_start_col,
                "end_col": gst_end_col,
                "value_cols": {
                    "revenue": gst_revenue_col,
                    "cost": gst_cost_col,
                    "profit": gst_profit_col,
                },
                "group_cols": [c for c in [
                    gst_group_col_1,
                    gst_group_col_2,
                    gst_group_col_3,
                ] if c],
                "first_fy": gst_first_fy_override,
                **_fdd_date_params_from_tracker(tracker),
                "hierarchy_limits": {
                    "level_1": {"max_items": int(s.get("gst_max_items_l1") or 10),
                                "other_label": s.get("gst_other_label_l1") or "Other"},
                    "level_2": {"max_items": int(s.get("gst_max_items_l2") or 8),
                                "other_label": s.get("gst_other_label_l2") or "Other"},
                    "level_3": {"max_items": int(s.get("gst_max_items_l3") or 5),
                                "other_label": s.get("gst_other_label_l3") or "Other"},
                },
                "show_ytd": bool(s.get("gst_show_ytd")),
                "show_ltm": bool(s.get("gst_show_ltm")),
                "show_ytd_delta": bool(s.get("gst_ytd_delta")),
                "show_ltm_delta": bool(s.get("gst_ltm_delta")),
                "show_ytd_cagr": bool(s.get("gst_ytd_cagr")),
                "show_ltm_cagr": bool(s.get("gst_ltm_cagr")),
                "show_revenue": True,
                "show_gp": bool(s.get("gst_show_gp")),
                "show_gm": bool(s.get("gst_show_gm")),
                "sort_mode": gst_sort_mode,
                "apply_fx": bool(s.get("apply_fx")),
                "fx_col": s.get("fx_col") or "",
                "output_file_path": s.get("output_folder", ""),
                "filters": _fdd_filters_payload(s.get("gst_filter_rules_json")),
            },
        }

        _start_async_revenue_script(
            dispatcher,
            body,
            "gst",
            "General Sales Table",
        )

        return slot_events


# ─── PVM flow ─────────────────────────────────────────────────────────────────


def _build_pvm_group_card(headers: list[str], default: str = "") -> dict:
    opts = [{"label": h, "value": h} for h in headers]
    if default and not any(o["value"] == default for o in opts):
        opts = [{"label": default, "value": default}] + opts
    return {
        "type": "adaptive_card",
        "card": "pvm_group",
        "title": "PVM Analysis — Product Dimension",
        "inputs": [
            {
                "id": "pvm_group_col",
                "type": "dropdown",
                "label": "Which column (product) would you like to analyse?",
                "options": opts,
                "default": default,
            }
        ],
        "submit_label": "Continue",
    }


class ActionProcessPvmGroup(Action):
    def name(self) -> str:
        return "action_process_pvm_group"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        events = _sync_column_role_slots(tracker, "pvm_group_col", payload.get("pvm_group_col"))
        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "pvm_period",
                "title": "PVM Analysis — Period",
                "inputs": [
                    {
                        "id": "pvm_period_mode",
                        "type": "radio",
                        "label": "Which period would you like to analyse?",
                        "options": [
                            {"label": "Full Fiscal Year (FY)", "value": "FY"},
                            {"label": "Last Twelve Months (LTM)", "value": "LTM"},
                            {"label": "Year-to-Date (YTD)", "value": "YTD"},
                        ],
                    }
                ],
                "submit_label": "Continue",
            }
        )
        return events


class ActionProcessPvmPeriod(Action):
    def name(self) -> str:
        return "action_process_pvm_period"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        events = [SlotSet("pvm_period_mode", payload.get("pvm_period_mode", "FY"))]
        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "pvm_method",
                "title": "PVM Method",
                "inputs": [
                    {
                        "id": "pvm_method",
                        "type": "radio",
                        "label": "Which PVM method would you like to use?",
                        "options": [
                            {"label": "Three components", "value": "three_components"},
                            {"label": "Classical", "value": "classical"},
                            {"label": "Chicago", "value": "chicago"},
                        ],
                    },
                    {
                        "id": "pvm_invoice_mapping_mode",
                        "type": "radio",
                        "label": "How should the invoice period assignment be determined?",
                        "options": [
                            {"label": "Calculate based on a date column", "value": "date"},
                            {"label": "Use an existing financial period column", "value": "year"},
                        ],
                    },
                    {
                        "id": "pvm_profit_mode",
                        "type": "radio",
                        "label": "Profit / margin column",
                        "options": [
                            {"label": "Use a profit column", "value": "profit"},
                            {"label": "Use a cost column", "value": "cost"},
                        ],
                    },
                ],
                "submit_label": "Continue",
            }
        )
        return events


class ActionProcessPvmMethod(Action):
    def name(self) -> str:
        return "action_process_pvm_method"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        events = [
            SlotSet("pvm_method", payload.get("pvm_method", "chicago")),
            SlotSet("pvm_invoice_mapping_mode", payload.get("pvm_invoice_mapping_mode", "date")),
            SlotSet("pvm_profit_mode", payload.get("pvm_profit_mode", "cost")),
        ]
        _emit_pvm_columns_card(dispatcher, tracker, payload)
        return events


class ActionProcessPvmColumns(Action):
    def name(self) -> str:
        return "action_process_pvm_columns"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        card = str(payload.get("card") or "").strip()
        if card != "pvm_columns":
            _emit_pvm_columns_card(dispatcher, tracker)
            return []

        pvm_col_keys = (
            "pvm_revenue_col",
            "pvm_invoice_col",
            "pvm_quantity_col",
            "pvm_cost_col",
            "pvm_profit_col",
            "pvm_start_col",
            "pvm_end_col",
        )
        events: list[Any] = []
        for k in pvm_col_keys:
            if k in payload:
                events.extend(_sync_column_role_slots(tracker, k, payload.get(k)))

        first_fy = _first_fy_for_pvm_labels_card(tracker)
        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "pvm_labels",
                "title": "Total Row Labels & Sorting",
                "inputs": [
                    {"id": "pvm_total_label_revenue", "type": "text", "label": "Label for total revenue row",
                     "placeholder": "Gross sales", "default": "Gross sales"},
                    {"id": "pvm_total_label_cost", "type": "text", "label": "Label for total cost row",
                     "placeholder": "Cost of materials", "default": "Cost of materials"},
                    {"id": "pvm_total_label_gp", "type": "text", "label": "Label for total gross profit row",
                     "placeholder": "Gross profit", "default": "Gross profit"},
                    {
                        "id": "pvm_sort_by_bridge",
                        "type": "dropdown",
                        "label": "Sorting of items across bridges",
                        "options": [
                            {"label": "Revenue", "value": "revenue"},
                            {"label": "Cost of goods sold", "value": "cost"},
                            {"label": "Gross profit", "value": "gross_profit"},
                        ],
                    },
                    {"id": "pvm_first_fy_override", "type": "number",
                     "label": "Please confirm the first fiscal year to include:",
                     "default": str(int(first_fy)), "placeholder": str(int(first_fy))},
                ],
                "submit_label": "Continue",
            }
        )
        return events


class ActionProcessPvmLabels(Action):
    def name(self) -> str:
        return "action_process_pvm_labels"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        fy_slot = float(payload.get("pvm_first_fy_override", _first_fy_for_pvm_labels_card(tracker)))
        _remember_session_first_fy(tracker, fy_slot)
        return [
            SlotSet("pvm_total_label_revenue", payload.get("pvm_total_label_revenue", "Gross sales")),
            SlotSet("pvm_total_label_cost", payload.get("pvm_total_label_cost", "Cost of materials")),
            SlotSet("pvm_total_label_gp", payload.get("pvm_total_label_gp", "Gross profit")),
            SlotSet("pvm_sort_by_bridge", payload.get("pvm_sort_by_bridge", "revenue")),
            SlotSet(
                "pvm_first_fy_override",
                fy_slot,
            ),
            FollowupAction("action_enter_pvm_filter_checkpoint"),
        ]


class ActionShowPvmReview(Action):
    def name(self) -> str:
        return "action_show_pvm_review"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        review_fy = _first_fy_for_pvm_review_card(tracker)
        header_opts = _header_opts_for_column_card(
            tracker,
            None,
            "pvm_revenue_col",
            "pvm_invoice_col",
            "pvm_quantity_col",
            "pvm_cost_col",
            "pvm_profit_col",
            "pvm_group_col",
        )
        none_opt = [{"label": "(none)", "value": ""}]
        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "pvm_proceed",
                "compact": True,
                "title": "Review — PVM Analysis",
                "subtitle": (
                    f"{tracker.get_slot('project_name') or '—'}"
                    f" · {tracker.get_slot('group_name') or '—'}"
                ),
                "inputs": [
                    {"id": "project_name", "type": "text", "label": "Project", "span": 1,
                     "default": tracker.get_slot("project_name") or ""},
                    {"id": "group_name", "type": "text", "label": "Group", "span": 1,
                     "default": tracker.get_slot("group_name") or ""},
                    {"id": "pvm_group_col", "type": "dropdown", "label": "Product", "span": 2,
                     "options": header_opts, "default": tracker.get_slot("pvm_group_col") or ""},
                    {"id": "pvm_period_mode", "type": "radio", "label": "Period", "layout": "split", "span": 2,
                     "options": [{"label": "FY", "value": "FY"}, {"label": "YTD", "value": "YTD"}, {"label": "LTM", "value": "LTM"}],
                     "default": tracker.get_slot("pvm_period_mode") or "FY"},
                    {"id": "pvm_method", "type": "radio", "label": "PVM method", "layout": "split", "span": 2,
                     "options": [
                         {"label": "Chicago", "value": "chicago"},
                         {"label": "Classical", "value": "classical"},
                         {"label": "Three components", "value": "three_components"},
                     ],
                     "default": tracker.get_slot("pvm_method") or "chicago"},
                    {"id": "pvm_invoice_mapping_mode", "type": "radio", "label": "Invoice period", "layout": "split", "span": 2,
                     "options": [
                         {"label": "Date column", "value": "date"},
                         {"label": "Period column", "value": "year"},
                     ],
                     "default": tracker.get_slot("pvm_invoice_mapping_mode") or "date"},
                    {"id": "pvm_profit_mode", "type": "radio", "label": "GP mode", "layout": "split", "span": 2,
                     "options": [
                         {"label": "Profit column", "value": "profit"},
                         {"label": "Cost column", "value": "cost"},
                     ],
                     "default": tracker.get_slot("pvm_profit_mode") or "cost"},
                    {"id": "pvm_revenue_col", "type": "dropdown", "label": "Revenue", "rowGroup": "pvm_amt",
                     "options": header_opts, "default": _column_default(tracker, "pvm_revenue_col") or ""},
                    {"id": "pvm_invoice_col", "type": "dropdown", "label": "Date / period", "rowGroup": "pvm_amt",
                     "options": header_opts, "default": _column_default(tracker, "pvm_invoice_col") or ""},
                    {"id": "pvm_quantity_col", "type": "dropdown", "label": "Quantity", "span": 2,
                     "options": header_opts, "default": _column_default(tracker, "pvm_quantity_col") or ""},
                    {"id": "pvm_cost_col", "type": "dropdown", "label": "COGS column", "span": 2,
                     "options": none_opt + header_opts, "required": False,
                     "default": _column_default(tracker, "pvm_cost_col") or "",
                     "showWhen": {"field": "pvm_profit_mode", "value": "cost"}},
                    {"id": "pvm_profit_col", "type": "dropdown", "label": "GP column", "span": 2,
                     "options": none_opt + header_opts, "required": False,
                     "default": _column_default(tracker, "pvm_profit_col") or "",
                     "showWhen": {"field": "pvm_profit_mode", "value": "profit"}},
                    {"id": "pvm_sort_by_bridge", "type": "radio", "label": "Sort by", "layout": "split", "span": 2,
                     "options": [
                         {"label": "Revenue", "value": "revenue"},
                         {"label": "COGS", "value": "cost"},
                         {"label": "Gross profit", "value": "gross_profit"},
                     ],
                     "default": tracker.get_slot("pvm_sort_by_bridge") or "revenue"},
                    {"id": "pvm_first_fy_override", "type": "number", "label": "First FY", "span": 2,
                     "default": str(int(review_fy))},
                ],
                "submit_label": "Proceed",
            }
        )
        return []


class ActionRunPvm(Action):
    def name(self) -> str:
        return "action_run_pvm"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        s = tracker.slots
        payload = _parse_payload(tracker)

        def pick(key: str, default=None):
            v = payload.get(key, None)
            return default if v is None or v == "" else v

        project_name = pick("project_name", s.get("project_name", ""))
        group_name = pick("group_name", s.get("group_name", ""))
        sort_bridge = pick("pvm_sort_by_bridge", s.get("pvm_sort_by_bridge", "revenue"))
        if sort_bridge == "profit":
            sort_bridge = "gross_profit"
        pvm_revenue_col = pick("pvm_revenue_col", s.get("pvm_revenue_col", ""))
        pvm_invoice_col = pick("pvm_invoice_col", s.get("pvm_invoice_col", ""))
        pvm_quantity_col = pick("pvm_quantity_col", s.get("pvm_quantity_col", ""))
        pvm_cost_col = pick("pvm_cost_col", s.get("pvm_cost_col") or "")
        pvm_profit_col = pick("pvm_profit_col", s.get("pvm_profit_col") or "")
        pvm_group_col = pick("pvm_group_col", s.get("pvm_group_col", ""))
        pvm_period_mode = pick("pvm_period_mode", s.get("pvm_period_mode", "FY"))
        pvm_method = pick("pvm_method", s.get("pvm_method", "chicago"))
        pvm_profit_mode = pick("pvm_profit_mode", s.get("pvm_profit_mode", "cost"))
        pvm_invoice_mapping_mode = pick(
            "pvm_invoice_mapping_mode", s.get("pvm_invoice_mapping_mode", "date"),
        )
        pvm_first_fy = int(float(pick("pvm_first_fy_override", _first_fy_for_pvm_review_card(tracker))))
        total_label_revenue = pick("pvm_total_label_revenue", s.get("pvm_total_label_revenue") or "Gross sales")
        total_label_cost = pick("pvm_total_label_cost", s.get("pvm_total_label_cost") or "Cost of materials")
        total_label_gp = pick("pvm_total_label_gp", s.get("pvm_total_label_gp") or "Gross profit")

        slot_events = [
            SlotSet("project_name", project_name),
            SlotSet("group_name", group_name),
            SlotSet("pvm_group_col", pvm_group_col),
            SlotSet("pvm_period_mode", pvm_period_mode),
            SlotSet("pvm_method", pvm_method),
            SlotSet("pvm_invoice_mapping_mode", pvm_invoice_mapping_mode),
            SlotSet("pvm_profit_mode", pvm_profit_mode),
            SlotSet("pvm_revenue_col", pvm_revenue_col),
            SlotSet("pvm_invoice_col", pvm_invoice_col),
            SlotSet("pvm_quantity_col", pvm_quantity_col),
            SlotSet("pvm_cost_col", pvm_cost_col),
            SlotSet("pvm_profit_col", pvm_profit_col),
            SlotSet("pvm_sort_by_bridge", sort_bridge),
            SlotSet("pvm_first_fy_override", pvm_first_fy),
            SlotSet("pvm_total_label_revenue", total_label_revenue),
            SlotSet("pvm_total_label_cost", total_label_cost),
            SlotSet("pvm_total_label_gp", total_label_gp),
        ]

        dp = _fdd_date_params_from_tracker(tracker)
        body = {
            "session_id": _fdd_session_id(tracker),
            "file_id": s.get("file_id"),
            "config": {
                "title": project_name,
                "company": group_name,
                "table": "Price volume mix",
                "sheet_name": s.get("sheet_name", ""),
                "base_sheet_name": "pvm",
                "formula_mode": True,
                "first_fy": pvm_first_fy,
                "as_of_year": dp["as_of_year"],
                "as_of_month": dp["as_of_month"],
                "fy_end_month": dp["fiscal_year_end_month"],
                "fy_end_day": dp["fiscal_year_end_day"],
                "period_mode": pvm_period_mode,
                "profit_mode": pvm_profit_mode,
                "invoice_mapping_mode": pvm_invoice_mapping_mode,
                "group_col": pvm_group_col,
                "quantity_col": pvm_quantity_col,
                "revenue_col": pvm_revenue_col,
                "cost_col": pvm_cost_col,
                "profit_col": pvm_profit_col,
                "invoice_col": pvm_invoice_col,
                "pvm_method": pvm_method,
                "total_label_revenue": total_label_revenue,
                "total_label_cost": total_label_cost,
                "total_label_gp": total_label_gp,
                "sort_by_bridge": sort_bridge,
                "apply_fx": bool(s.get("apply_fx")),
                "fx_col": s.get("fx_col") or "",
                "output_file_path": s.get("output_folder", ""),
                "filters": _fdd_filters_payload(s.get("pvm_filter_rules_json")),
            },
        }

        _start_async_revenue_script(
            dispatcher,
            body,
            "pvm",
            "PVM Analysis",
        )
        return slot_events


# ─── TOP Report flow ──────────────────────────────────────────────────────────


def _build_top_calc_mode_card() -> dict:
    return {
        "type": "adaptive_card",
        "card": "top_calc_mode",
        "title": "TOP Report — Calculation Mode",
        "inputs": [
            {
                "id": "top_calc_mode",
                "type": "radio",
                "label": "Select calculation mode",
                "options": [
                    {"label": "Invoiced amounts", "value": "invoice"},
                    {"label": "Accrual-based revenue", "value": "accrual"},
                ],
            }
        ],
        "submit_label": "Continue",
    }


class ActionProcessTopCalcMode(Action):
    def name(self) -> str:
        return "action_process_top_calc_mode"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        calc_mode = payload.get("top_calc_mode", "invoice")
        events = [SlotSet("top_calc_mode", calc_mode)]
        _emit_top_columns_card(dispatcher, tracker, payload)
        return events


class ActionProcessTopColumns(Action):
    def name(self) -> str:
        return "action_process_top_columns"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        card = str(payload.get("card") or "").strip()
        if card != "top_columns":
            _emit_top_columns_card(dispatcher, tracker)
            return []

        top_col_keys = (
            "top_value_col",
            "top_invoice_col",
            "top_start_col",
            "top_end_col",
        )
        events: list[Any] = []
        for k in top_col_keys:
            if k in payload:
                events.extend(_sync_column_role_slots(tracker, k, payload.get(k)))

        headers = _headers_from_sources(tracker, payload)
        top_col_default = str(_column_default(tracker, "top_col", payload) or "")
        top_col_opts = [{"label": h, "value": h} for h in headers]
        if top_col_default and not any(o["value"] == top_col_default for o in top_col_opts):
            top_col_opts = [{"label": top_col_default, "value": top_col_default}] + top_col_opts
        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "top_labels",
                "title": "TOP Report — Labels & Dimension",
                "inputs": [
                    {"id": "top_col", "type": "dropdown",
                     "label": "Which TOP dimension (products, customers, suppliers)?",
                     "options": top_col_opts, "default": top_col_default},
                    {"id": "top_subtitle", "type": "text", "label": "Desired subtitle for the table",
                     "placeholder": "Top customers"},
                    {"id": "top_total_label", "type": "text", "label": "Label for the total row",
                     "placeholder": "Gross sales", "default": "Gross sales"},
                    {
                        "id": "top_first_fy_override",
                        "type": "number",
                        "label": "Please confirm the first fiscal year to include in the analysis:",
                        "default": str(int(_first_fy_for_top_labels_card(tracker))),
                        "placeholder": str(int(_first_fy_for_top_labels_card(tracker))),
                    },
                ],
                "submit_label": "Continue",
            }
        )
        return events


def _top_bucket_mode_for_script(mode: str) -> str:
    m = str(mode or "threshold").strip().lower()
    if m in {"number", "numbers", "custom_numbers"}:
        return "number"
    return "threshold"


def _parse_list(val: Any, fallback: list) -> list:
    if val in (None, ""):
        return fallback
    if isinstance(val, list):
        return val
    s = str(val).strip()
    if not s:
        return fallback
    try:
        import ast

        parsed = ast.literal_eval(s)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    if "," in s:
        out: list[Any] = []
        for part in s.split(","):
            part = part.strip().strip("[]")
            if not part:
                continue
            try:
                out.append(int(part))
            except ValueError:
                try:
                    out.append(float(part))
                except ValueError:
                    out.append(part)
        if out:
            return out
    if ";" in s:
        out = []
        for part in s.split(";"):
            part = part.strip().strip("[]")
            if not part:
                continue
            try:
                out.append(int(part))
            except ValueError:
                try:
                    out.append(float(part))
                except ValueError:
                    out.append(part)
        if out:
            return out
    try:
        return [int(s)]
    except ValueError:
        pass
    return fallback


def _top_filters_payload(rules_json: str | None) -> dict:
    return _fdd_filters_payload(rules_json)


class ActionProcessTopLabels(Action):
    def name(self) -> str:
        return "action_process_top_labels"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        fy_val = float(payload.get("top_first_fy_override", _first_fy_for_top_labels_card(tracker)))
        _remember_session_first_fy(tracker, fy_val)
        events = _sync_column_role_slots(tracker, "top_col", payload.get("top_col"))
        events += [
            SlotSet("top_subtitle", payload.get("top_subtitle")),
            SlotSet("top_total_label", payload.get("top_total_label", "Gross sales")),
            SlotSet("top_first_fy_override", fy_val),
        ]
        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "top_abc",
                "title": "ABC Threshold Settings",
                "inputs": [
                    {
                        "id": "top_abc_adjust",
                        "type": "radio",
                        "label": "Would you like to adjust the ABC thresholds?",
                        "default": "no",
                        "options": [
                            {"label": "No, keep the standard thresholds (20% / 40%)", "value": "no"},
                            {"label": "Yes, I want to change them", "value": "yes"},
                        ],
                    },
                    {
                        "id": "top_abc_thresholds",
                        "type": "text",
                        "label": "Custom thresholds — e.g. [0.2, 0.4]",
                        "placeholder": "[0.2, 0.4]",
                        "required": False,
                        "default": "[0.2, 0.4]",
                        "showWhen": {"field": "top_abc_adjust", "value": "yes"},
                    },
                ],
                "submit_label": "Continue",
            }
        )
        return events


class ActionProcessTopAbc(Action):
    def name(self) -> str:
        return "action_process_top_abc"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        adjust = payload.get("top_abc_adjust", "no") == "yes"
        thresholds = payload.get("top_abc_thresholds", "[0.2, 0.4]") if adjust else "[0.2, 0.4]"
        events = [SlotSet("top_abc_thresholds", thresholds)]

        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "top_bucket",
                "title": "Top Bucket Configuration",
                "subtitle": "Default: cum. share thresholds [20%, 50%, 80%] plus an 'Other' bucket.",
                "inputs": [
                    {
                        "id": "top_bucket_mode",
                        "type": "radio",
                        "label": "Would you like to modify the bucket settings?",
                        "default": "threshold",
                        "options": [
                            {"label": "No, keep the standard setup", "value": "threshold"},
                            {"label": "Yes — customise cum. share thresholds", "value": "custom_threshold"},
                            {"label": "Yes — specify items per bucket manually", "value": "custom_numbers"},
                        ],
                    },
                    {
                        "id": "top_bucket_thresholds",
                        "type": "text",
                        "label": "Cum. share thresholds — you can define any number of buckets, e.g. [0.2, 0.5, 0.8]",
                        "placeholder": "[0.2, 0.5, 0.8]",
                        "required": False,
                        "default": "[0.2, 0.5, 0.8]",
                        "showWhen": {"field": "top_bucket_mode", "value": "custom_threshold"},
                    },
                    {
                        "id": "top_bucket_numbers",
                        "type": "text",
                        "label": "Items per bucket — you can define any number of buckets, e.g. [3, 5, 10]",
                        "placeholder": "[3, 5, 10]",
                        "required": False,
                        "showWhen": {"field": "top_bucket_mode", "value": "custom_numbers"},
                    },
                    {
                        "id": "top_other_bucket",
                        "type": "radio",
                        "label": "'Other' bucket",
                        "default": "include",
                        "options": [
                            {"label": "Include", "value": "include"},
                            {"label": "Disable", "value": "disable"},
                        ],
                    },
                    {
                        "id": "top_other_bucket_label",
                        "type": "text",
                        "label": "Label for the 'Other' bucket",
                        "placeholder": "Other",
                        "required": False,
                        "default": "Other",
                        "showWhen": {"field": "top_other_bucket", "value": "include"},
                    },
                ],
                "submit_label": "Continue",
            }
        )
        return events


class ActionProcessTopBucket(Action):
    def name(self) -> str:
        return "action_process_top_bucket"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        mode = payload.get("top_bucket_mode", "threshold")
        other = _top_other_bucket_choice(tracker, payload)
        other_label = str(payload.get("top_other_bucket_label", "Other") or "Other").strip() or "Other"
        return [
            SlotSet("top_bucket_mode", mode),
            SlotSet("top_bucket_thresholds", payload.get("top_bucket_thresholds", "[0.2, 0.5, 0.8]")),
            SlotSet("top_bucket_numbers", payload.get("top_bucket_numbers", "")),
            SlotSet("top_other_bucket", other),
            SlotSet("top_other_bucket_enabled", other != "disable"),
            SlotSet("top_other_bucket_label", other_label if other == "include" else "Other"),
            FollowupAction("action_enter_top_filter_checkpoint"),
        ]


class ActionShowTopReview(Action):
    def name(self) -> str:
        return "action_show_top_review"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        header_opts = _header_opts_for_column_card(
            tracker,
            None,
            "top_col",
            "top_value_col",
            "top_invoice_col",
            "top_start_col",
            "top_end_col",
        )
        none_opt = [{"label": "(none)", "value": ""}]
        ltm = _ltm_month_from_sources(tracker)
        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "top_proceed",
                "compact": True,
                "title": "Review — TOP Report",
                "subtitle": (
                    f"{tracker.get_slot('project_name') or '—'}"
                    f" · {tracker.get_slot('group_name') or '—'}"
                ),
                "inputs": [
                    {"id": "ltm_month", "type": "text", "label": "", "default": ltm,
                     "required": False, "hidden": True},
                    {"id": "project_name", "type": "text", "label": "Project", "span": 1,
                     "default": tracker.get_slot("project_name") or ""},
                    {"id": "group_name", "type": "text", "label": "Group", "span": 1,
                     "default": tracker.get_slot("group_name") or ""},
                    {"id": "top_calc_mode", "type": "radio", "label": "Calc mode", "layout": "split", "span": 2,
                     "options": [{"label": "Invoice", "value": "invoice"}, {"label": "Accrual", "value": "accrual"}],
                     "default": tracker.get_slot("top_calc_mode") or "invoice"},
                    {"id": "top_col", "type": "dropdown", "label": "TOP dimension", "span": 2,
                     "options": header_opts, "default": tracker.get_slot("top_col") or ""},
                    {"id": "top_value_col", "type": "dropdown", "label": "Value", "rowGroup": "top_val",
                     "options": header_opts, "default": _column_default(tracker, "top_value_col") or ""},
                    {"id": "top_invoice_col", "type": "dropdown", "label": "Date / period", "rowGroup": "top_val",
                     "options": header_opts, "default": _column_default(tracker, "top_invoice_col") or ""},
                    {"id": "top_start_col", "type": "dropdown", "label": "Start (accr.)", "rowGroup": "top_accr",
                     "options": none_opt + header_opts, "required": False,
                     "default": _column_default(tracker, "top_start_col") or "",
                     "showWhen": {"field": "top_calc_mode", "value": "accrual"}},
                    {"id": "top_end_col", "type": "dropdown", "label": "End (accr.)", "rowGroup": "top_accr",
                     "options": none_opt + header_opts, "required": False,
                     "default": _column_default(tracker, "top_end_col") or "",
                     "showWhen": {"field": "top_calc_mode", "value": "accrual"}},
                    {"id": "top_subtitle", "type": "text", "label": "Subtitle", "span": 2,
                     "required": False,
                     "placeholder": "Top customers",
                     "default": tracker.get_slot("top_subtitle") or ""},
                    {
                        "id": "top_first_fy_override",
                        "type": "number",
                        "label": "First fiscal year to include in the analysis",
                        "span": 2,
                        "default": str(int(_first_fy_for_top_review_card(tracker))),
                    },
                    {
                        "id": "top_abc_adjust",
                        "type": "radio",
                        "label": "ABC thresholds",
                        "layout": "split",
                        "span": 2,
                        "default": "no",
                        "options": [
                            {"label": "Standard (20% / 40%)", "value": "no"},
                            {"label": "Custom", "value": "yes"},
                        ],
                    },
                    {
                        "id": "top_abc_thresholds",
                        "type": "text",
                        "label": "Custom ABC thresholds",
                        "span": 2,
                        "default": tracker.get_slot("top_abc_thresholds") or "[0.2, 0.4]",
                        "showWhen": {"field": "top_abc_adjust", "value": "yes"},
                    },
                    {
                        "id": "top_bucket_mode",
                        "type": "radio",
                        "label": "Bucket mode",
                        "layout": "split",
                        "span": 2,
                        "options": [
                            {"label": "Default setup", "value": "threshold"},
                            {"label": "Custom thresholds", "value": "custom_threshold"},
                            {"label": "Items per bucket", "value": "custom_numbers"},
                        ],
                        "default": tracker.get_slot("top_bucket_mode") or "threshold",
                    },
                    {
                        "id": "top_bucket_thresholds",
                        "type": "text",
                        "label": "Cum. share thresholds (any number of buckets)",
                        "span": 2,
                        "default": tracker.get_slot("top_bucket_thresholds") or "[0.2, 0.5, 0.8]",
                        "showWhen": {"field": "top_bucket_mode", "value": "custom_threshold"},
                    },
                    {
                        "id": "top_bucket_numbers",
                        "type": "text",
                        "label": "Items per bucket (any number of buckets)",
                        "span": 2,
                        "default": tracker.get_slot("top_bucket_numbers") or "",
                        "showWhen": {"field": "top_bucket_mode", "value": "custom_numbers"},
                    },
                    {
                        "id": "top_other_bucket",
                        "type": "radio",
                        "label": "'Other' bucket",
                        "layout": "split",
                        "span": 2,
                        "options": [
                            {"label": "Include", "value": "include"},
                            {"label": "Disable", "value": "disable"},
                        ],
                        "default": _top_other_bucket_choice(tracker),
                    },
                    {
                        "id": "top_other_bucket_label",
                        "type": "text",
                        "label": "Label for the 'Other' bucket",
                        "span": 2,
                        "default": tracker.get_slot("top_other_bucket_label") or "Other",
                        "showWhen": {"field": "top_other_bucket", "value": "include"},
                    },
                ],
                "submit_label": "Proceed",
            }
        )
        return []


class ActionRunTop(Action):
    def name(self) -> str:
        return "action_run_top"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        s = tracker.slots
        payload = _parse_payload(tracker)

        def pick(key: str, default=None):
            v = payload.get(key, None)
            return default if v is None or v == "" else v

        bucket_mode_raw = pick("top_bucket_mode", s.get("top_bucket_mode", "threshold"))
        bucket_mode = _top_bucket_mode_for_script(bucket_mode_raw)
        thresholds = _parse_list(pick("top_bucket_thresholds", s.get("top_bucket_thresholds")), [0.2, 0.5, 0.8])
        numbers = _parse_list(pick("top_bucket_numbers", s.get("top_bucket_numbers")), [])
        abc_adjust = pick("top_abc_adjust", "no") == "yes"
        abc_thresholds = _parse_list(
            pick("top_abc_thresholds", s.get("top_abc_thresholds")),
            [0.2, 0.4],
        ) if abc_adjust else [0.2, 0.4]
        other_choice = _top_other_bucket_choice(tracker, payload)
        other_label = str(
            pick("top_other_bucket_label", s.get("top_other_bucket_label") or "Other") or "Other"
        ).strip() or "Other"
        ltm_month = _ltm_month_from_sources(tracker, payload)
        if not ltm_month:
            dispatcher.utter_message(
                text=(
                    "Date settings are incomplete (last month to include is missing). "
                    "Please go back to Date Settings and select the as-of month, then run TOP again."
                )
            )
            return []
        dp = _fdd_date_params_from_tracker(tracker)
        cy, cm = _as_of_year_month_from_ltm(ltm_month)
        dp["current_year"] = cy
        dp["current_month"] = cm
        dp["as_of_year"] = cy
        dp["as_of_month"] = cm
        dp["ltm_month"] = ltm_month
        top_first_fy = int(
            float(pick("top_first_fy_override", _first_fy_for_top_review_card(tracker)))
        )
        project_name = pick("project_name", s.get("project_name", ""))
        group_name = pick("group_name", s.get("group_name", ""))
        top_subtitle = pick("top_subtitle", s.get("top_subtitle", ""))
        top_total_label = pick("top_total_label", s.get("top_total_label") or "Gross sales")
        top_calc_mode = pick("top_calc_mode", s.get("top_calc_mode", "invoice"))
        top_col = pick("top_col", s.get("top_col", ""))
        top_value_col = pick("top_value_col", s.get("top_value_col", ""))
        top_invoice_col = pick("top_invoice_col", s.get("top_invoice_col", ""))
        top_start_col = pick("top_start_col", s.get("top_start_col") or "")
        top_end_col = pick("top_end_col", s.get("top_end_col") or "")

        slot_events = [
            SlotSet("project_name", project_name),
            SlotSet("group_name", group_name),
            SlotSet("top_subtitle", top_subtitle),
            SlotSet("top_total_label", top_total_label),
            SlotSet("top_calc_mode", top_calc_mode),
            SlotSet("top_col", top_col),
            SlotSet("top_value_col", top_value_col),
            SlotSet("top_invoice_col", top_invoice_col),
            SlotSet("top_start_col", top_start_col),
            SlotSet("top_end_col", top_end_col),
            SlotSet("top_first_fy_override", top_first_fy),
            SlotSet("top_bucket_mode", bucket_mode_raw),
            SlotSet("top_bucket_thresholds", pick("top_bucket_thresholds", tracker.get_slot("top_bucket_thresholds"))),
            SlotSet("top_bucket_numbers", pick("top_bucket_numbers", tracker.get_slot("top_bucket_numbers"))),
            SlotSet("top_other_bucket", other_choice),
            SlotSet("top_other_bucket_label", other_label),
        ]

        body = {
            "session_id": _fdd_session_id(tracker),
            "file_id": tracker.get_slot("file_id"),
            "config": {
                "title": project_name,
                "company": group_name,
                "table": "TOP",
                "subtitle_suffix": top_subtitle,
                "total_label": top_total_label,
                "first_fy": top_first_fy,
                "sheet_name": s.get("sheet_name", ""),
                "base_sheet_name": "TOP",
                "formula_mode": True,
                "calc_mode": top_calc_mode,
                "top_col": top_col,
                "value_col": top_value_col,
                "invoice_col": top_invoice_col,
                "start_col": top_start_col,
                "end_col": top_end_col,
                "ltm_month": ltm_month,
                "current_year": dp["current_year"],
                "current_month": dp["current_month"],
                "fiscal_year_end_month": dp["fiscal_year_end_month"],
                "fiscal_year_end_day": dp["fiscal_year_end_day"],
                "apply_fx": bool(s.get("apply_fx")),
                "fx_col": s.get("fx_col") or "",
                "abc_thresholds": abc_thresholds,
                "top_bucket": {
                    "enabled": True,
                    "bucket_mode": bucket_mode,
                    "thresholds": thresholds,
                    "numbers": numbers,
                    "create_other_bucket": other_choice != "disable",
                    "other_bucket_label": other_label if other_choice != "disable" else "Other",
                },
                "output_file_path": s.get("output_folder", ""),
                "filters": _top_filters_payload(pick("top_filter_rules_json", s.get("top_filter_rules_json"))),
            },
        }

        _start_async_revenue_script(
            dispatcher,
            body,
            "top",
            "TOP Report",
        )

        return slot_events


# ─── Churn / ARR Bridge flow ───────────────────────────────────────────────────

CHURN_MAX_GROUP_LEVELS = 3


def _churn_group_level_count(tracker: Tracker) -> int:
    raw = tracker.get_slot("churn_group_level_count")
    try:
        n = int(float(raw)) if raw not in (None, "") else 1
    except (TypeError, ValueError):
        n = 1
    return max(1, min(CHURN_MAX_GROUP_LEVELS, n))


def _churn_filter_checkpoint_card(tracker: Tracker) -> dict[str, Any]:
    disp = (tracker.get_slot("churn_filter_rules_display") or "").strip()
    if not disp:
        disp = "No active filter rules."
    return {
        "type": "adaptive_card",
        "card": "churn_filter_checkpoint",
        "title": "Churn — Filter rules",
        "subtitle": "Review your current filter rules, then choose an action.",
        "review_text": disp,
        "inputs": [
            {
                "id": "churn_filter_manage_choice",
                "type": "radio",
                "label": "What would you like to do?",
                "options": [
                    {"label": "1. Delete current rules", "value": "delete_all"},
                    {"label": "2. Add a rule", "value": "add_rule"},
                    {"label": "3. Keep rules as they are", "value": "keep"},
                ],
            }
        ],
        "submit_label": "Continue",
    }


def _emit_churn_columns_card(dispatcher: CollectingDispatcher, tracker: Tracker, payload: dict | None = None) -> None:
    header_opts = _header_opts_for_review(
        tracker,
        payload,
        _column_default(tracker, "churn_value_col", payload),
        _column_default(tracker, "churn_invoice_col", payload),
        _column_default(tracker, "churn_start_col", payload),
        _column_default(tracker, "churn_end_col", payload),
    )
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "churn_columns",
            "title": "Churn — Core columns",
            "subtitle": "Map the contract and customer fields required for the ARR bridge.",
            "inputs": [
                {"id": "churn_value_col", "type": "dropdown", "label": "Contract value column",
                 "options": header_opts, "default": _column_default(tracker, "churn_value_col", payload)},
                {"id": "churn_customer_col", "type": "dropdown", "label": "Customer ID column",
                 "options": header_opts, "default": _column_default(tracker, "churn_customer_col", payload)},
                {"id": "churn_product_col", "type": "dropdown", "label": "Product column",
                 "options": header_opts, "default": _column_default(tracker, "churn_product_col", payload)},
                {"id": "churn_start_col", "type": "dropdown", "label": "Contract start date",
                 "options": header_opts, "default": _column_default(tracker, "churn_start_col", payload)},
                {"id": "churn_end_col", "type": "dropdown", "label": "Contract end date",
                 "options": header_opts, "default": _column_default(tracker, "churn_end_col", payload)},
                {"id": "churn_invoice_col", "type": "dropdown", "label": "Invoice date column",
                 "options": header_opts, "default": _column_default(tracker, "churn_invoice_col", payload)},
            ],
            "submit_label": "Continue",
        }
    )


def _emit_churn_groups_card(dispatcher: CollectingDispatcher, tracker: Tracker, level_count: int) -> None:
    headers = _headers_from_sources(tracker)
    opts = [{"label": h, "value": h} for h in headers]
    inputs: list[dict[str, Any]] = []
    for i in range(1, level_count + 1):
        slot = f"churn_group_col_{i}"
        default = str(_column_default(tracker, slot) or "")
        if default and not any(o["value"] == default for o in opts):
            col_opts = [{"label": default, "value": default}] + opts
        else:
            col_opts = opts
        inputs.append({
            "id": slot,
            "type": "dropdown",
            "label": f"Grouping column — level {i}",
            "options": col_opts,
            "default": default,
        })
        sort_default = tracker.get_slot(f"churn_sort_by_{i}") or "arr"
        inputs.append({
            "id": f"churn_sort_by_{i}",
            "type": "radio",
            "label": f"Sort level {i} by (descending)",
            "default": str(sort_default),
            "options": [
                {"label": "ARR (largest first)", "value": "arr"},
                {"label": "Alphabetical", "value": "name"},
            ],
        })
    card: dict[str, Any] = {
        "type": "adaptive_card",
        "card": "churn_groups",
        "title": "Churn — Grouping",
        "subtitle": (
            "Hierarchy: level 1 is the outer group; deeper levels nest inside. "
            "Sorting is always descending."
        ),
        "inputs": inputs,
        "submit_label": "Continue",
    }
    if level_count < CHURN_MAX_GROUP_LEVELS:
        card["secondary_submit_label"] = "Add another grouping column"
        card["secondary_submit_id"] = "churn_add_group_col"
    dispatcher.utter_message(json_message=card)


def _emit_churn_labels_card(
    dispatcher: CollectingDispatcher,
    tracker: Tracker,
    group_cols_override: list[str] | None = None,
) -> None:
    group_cols = group_cols_override if group_cols_override is not None else _churn_group_cols_from_tracker(tracker)
    single_group = len(group_cols) == 1
    fy_default = int(_first_fy_for_churn_labels_card(tracker))
    inputs: list[dict[str, Any]] = [
        {"id": "churn_table", "type": "text", "label": "Table name",
         "default": tracker.get_slot("churn_table") or "ARR Bridge", "placeholder": "ARR Bridge"},
        {"id": "churn_total_label", "type": "text", "label": "Total row label",
         "default": tracker.get_slot("churn_total_label") or "Total", "placeholder": "Total"},
        {
            "id": "churn_first_fy_override",
            "type": "number",
            "label": "First fiscal year to include in the data source",
            "default": str(fy_default),
            "placeholder": str(fy_default),
        },
    ]
    if single_group:
        bucket_mode_default = str(tracker.get_slot("churn_bucket_mode") or "threshold").strip().lower()
        if bucket_mode_default in ("custom_threshold", "threshold"):
            bucket_mode_default = "threshold"
        elif bucket_mode_default != "custom_numbers":
            bucket_mode_default = "threshold"
        inputs.append({
            "id": "churn_top_bucket_enabled",
            "type": "radio",
            "label": "Enable top buckets",
            "default": "off" if not _slot_bool(tracker.get_slot("churn_top_bucket_enabled")) else "on",
            "options": [
                {"label": "Off", "value": "off"},
                {"label": "On", "value": "on"},
            ],
        })
        inputs += [
            {
                "id": "churn_bucket_mode",
                "type": "radio",
                "label": "Bucket mode",
                "default": bucket_mode_default,
                "showWhen": {"field": "churn_top_bucket_enabled", "value": "on"},
                "options": [
                    {"label": "Cumulative share thresholds", "value": "threshold"},
                    {"label": "Items per bucket", "value": "custom_numbers"},
                ],
            },
            {
                "id": "churn_bucket_thresholds",
                "type": "text",
                "label": "Thresholds e.g. [0.2, 0.5, 0.8]",
                "hint": "You can create as many buckets as you want",
                "default": _churn_flow_val(tracker, "churn_bucket_thresholds", "[0.2, 0.5, 0.8]") or "[0.2, 0.5, 0.8]",
                "showWhen": [
                    {"field": "churn_top_bucket_enabled", "value": "on"},
                    {"field": "churn_bucket_mode", "value": "threshold"},
                ],
            },
            {
                "id": "churn_bucket_numbers",
                "type": "text",
                "label": "Items per bucket e.g. [3, 5, 10]",
                "hint": "You can create as many buckets as you want",
                "default": _churn_flow_val(tracker, "churn_bucket_numbers", "") or "",
                "showWhen": [
                    {"field": "churn_top_bucket_enabled", "value": "on"},
                    {"field": "churn_bucket_mode", "value": "custom_numbers"},
                ],
            },
            {
                "id": "churn_other_bucket",
                "type": "radio",
                "label": "'Other' bucket",
                "default": "include",
                "showWhen": {"field": "churn_top_bucket_enabled", "value": "on"},
                "options": [
                    {"label": "Include", "value": "include"},
                    {"label": "Disable", "value": "disable"},
                ],
            },
            {
                "id": "churn_other_bucket_label",
                "type": "text",
                "label": "Label for the 'Other' bucket",
                "default": _churn_flow_val(tracker, "churn_other_bucket_label", "Other") or "Other",
                "showWhen": [
                    {"field": "churn_top_bucket_enabled", "value": "on"},
                    {"field": "churn_other_bucket", "value": "include"},
                ],
            },
        ]
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "churn_labels",
            "title": "Churn — Labels & periods",
            "inputs": inputs,
            "submit_label": "Continue",
        }
    )


def _churn_group_cols_from_tracker(tracker: Tracker) -> list[str]:
    return _churn_flow_group_cols(tracker)


def _churn_group_cols_from_sources(tracker: Tracker, payload: dict | None = None) -> list[str]:
    p = payload or {}
    cols: list[str] = []
    for i in range(1, CHURN_MAX_GROUP_LEVELS + 1):
        raw = p.get(f"churn_group_col_{i}")
        if raw in (None, ""):
            raw = _churn_flow_val(tracker, f"churn_group_col_{i}", "")
        text = str(raw or "").strip()
        if text:
            cols.append(text)
    if cols:
        return cols
    return _churn_flow_group_cols(tracker)


def _churn_sort_config_from_tracker(tracker: Tracker) -> dict[str, Any]:
    sort: dict[str, Any] = {"top_level_desc": True, "leaf_desc": True}
    s1 = str(tracker.get_slot("churn_sort_by_1") or "arr").strip().lower()
    sort["top_level_by"] = "name" if s1 == "name" else "ARR2"
    if _churn_group_level_count(tracker) >= 2:
        s2 = str(tracker.get_slot("churn_sort_by_2") or "arr").strip().lower()
        sort["leaf_within_parent"] = "name" if s2 == "name" else "ARR2"
    return sort


def _churn_top_bucket_config(tracker: Tracker, payload: dict | None = None) -> dict[str, Any]:
    p = payload or {}
    enabled = str(
        p.get("churn_top_bucket_enabled", _churn_flow_val(tracker, "churn_top_bucket_enabled", "off") or "off")
    ).lower() == "on"
    if not enabled or len(_churn_group_cols_from_sources(tracker, p)) != 1:
        return {"enabled": False}
    mode_raw = str(p.get("churn_bucket_mode", _churn_flow_val(tracker, "churn_bucket_mode", "threshold") or "threshold")).lower()
    bucket_mode = _top_bucket_mode_for_script(mode_raw)
    thresholds = _parse_list(
        p.get("churn_bucket_thresholds", _churn_flow_val(tracker, "churn_bucket_thresholds", "[0.2, 0.5, 0.8]")),
        [0.2, 0.5, 0.8],
    )
    numbers = _parse_list(p.get("churn_bucket_numbers", _churn_flow_val(tracker, "churn_bucket_numbers", "")), [])
    other = str(p.get("churn_other_bucket", _churn_flow_val(tracker, "churn_other_bucket", "include") or "include"))
    other_label = str(
        p.get("churn_other_bucket_label", _churn_flow_val(tracker, "churn_other_bucket_label", "Other") or "Other")
    ).strip() or "Other"
    return {
        "enabled": True,
        "bucket_mode": bucket_mode,
        "thresholds": thresholds,
        "numbers": numbers,
        "create_other_bucket": other != "disable",
        "other_bucket_label": other_label if other != "disable" else "Other",
        "based_on": "current",
    }


class ActionProcessChurnColumns(Action):
    def name(self) -> str:
        return "action_process_churn_columns"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        events: list[Any] = []
        flow_updates: dict[str, Any] = {}
        for k in (
            "churn_value_col", "churn_customer_col", "churn_product_col",
            "churn_start_col", "churn_end_col", "churn_invoice_col",
        ):
            if k in payload:
                events.extend(_sync_column_role_slots(tracker, k, payload.get(k)))
                flow_updates[k] = payload.get(k)
        _remember_churn_flow(tracker, flow_updates)
        _emit_churn_groups_card(dispatcher, tracker, 1)
        return events + [SlotSet("churn_group_level_count", 1)]


class ActionReshowChurnColumns(Action):
    def name(self) -> str:
        return "action_reshow_churn_columns"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        _emit_churn_columns_card(dispatcher, tracker)
        return []


class ActionProcessChurnGroups(Action):
    def name(self) -> str:
        return "action_process_churn_groups"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        events: list[Any] = []
        for i in range(1, CHURN_MAX_GROUP_LEVELS + 1):
            gk = f"churn_group_col_{i}"
            sk = f"churn_sort_by_{i}"
            if gk in payload:
                events.append(SlotSet(gk, payload.get(gk)))
            if sk in payload:
                events.append(SlotSet(sk, payload.get(sk)))

        flow_partial: dict[str, Any] = {}
        for i in range(1, CHURN_MAX_GROUP_LEVELS + 1):
            gk = f"churn_group_col_{i}"
            sk = f"churn_sort_by_{i}"
            if gk in payload:
                flow_partial[gk] = payload.get(gk)
            if sk in payload:
                flow_partial[sk] = payload.get(sk)
        if flow_partial:
            _remember_churn_flow(tracker, flow_partial)

        if payload.get("churn_add_group_col"):
            level = min(CHURN_MAX_GROUP_LEVELS, _churn_group_level_count(tracker) + 1)
            events.append(SlotSet("churn_group_level_count", level))
            _remember_churn_flow(tracker, {"churn_group_level_count": level})
            _emit_churn_groups_card(dispatcher, tracker, level)
            return events

        payload_cols = [
            str(payload.get(f"churn_group_col_{i}") or "").strip()
            for i in range(1, CHURN_MAX_GROUP_LEVELS + 1)
            if str(payload.get(f"churn_group_col_{i}") or "").strip()
        ]
        level = max(1, min(CHURN_MAX_GROUP_LEVELS, len(payload_cols) if payload_cols else _churn_group_level_count(tracker)))
        events.append(SlotSet("churn_group_level_count", level))
        flow_updates: dict[str, Any] = {"group_cols": payload_cols, "churn_group_level_count": level}
        for i in range(1, CHURN_MAX_GROUP_LEVELS + 1):
            col = payload_cols[i - 1] if i <= len(payload_cols) else ""
            events.append(SlotSet(f"churn_group_col_{i}", col))
            flow_updates[f"churn_group_col_{i}"] = col
            sk = f"churn_sort_by_{i}"
            if sk in payload:
                events.append(SlotSet(sk, payload.get(sk)))
                flow_updates[sk] = payload.get(sk)
        _remember_churn_flow(tracker, flow_updates)
        _emit_churn_labels_card(dispatcher, tracker, group_cols_override=payload_cols or None)
        return events


class ActionProcessChurnLabels(Action):
    def name(self) -> str:
        return "action_process_churn_labels"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        fy_raw = payload.get("churn_first_fy_override")
        if fy_raw in (None, ""):
            fy_val = float(_first_fy_for_churn_labels_card(tracker))
        else:
            fy_val = float(str(fy_raw).strip().replace(",", "."))
        _remember_session_first_fy(tracker, fy_val)
        bucket_on = str(payload.get("churn_top_bucket_enabled", "off")).lower() == "on"
        events = [
            SlotSet("churn_table", payload.get("churn_table", "ARR Bridge")),
            SlotSet("churn_total_label", payload.get("churn_total_label", "Total")),
            SlotSet("churn_first_fy_override", fy_val),
            SlotSet("churn_top_bucket_enabled", bucket_on),
            SlotSet("churn_bucket_mode", payload.get("churn_bucket_mode", "threshold")),
            SlotSet("churn_bucket_thresholds", payload.get("churn_bucket_thresholds", "[0.2, 0.5, 0.8]")),
            SlotSet("churn_bucket_numbers", payload.get("churn_bucket_numbers", "")),
            SlotSet("churn_other_bucket", payload.get("churn_other_bucket", "include")),
            SlotSet("churn_other_bucket_label", payload.get("churn_other_bucket_label", "Other")),
        ]
        _remember_churn_flow(tracker, {
            "churn_table": payload.get("churn_table", "ARR Bridge"),
            "churn_total_label": payload.get("churn_total_label", "Total"),
            "churn_first_fy_override": fy_val,
            "churn_top_bucket_enabled": bucket_on,
            "churn_bucket_mode": payload.get("churn_bucket_mode", "threshold"),
            "churn_bucket_thresholds": payload.get("churn_bucket_thresholds", "[0.2, 0.5, 0.8]"),
            "churn_bucket_numbers": payload.get("churn_bucket_numbers", ""),
            "churn_other_bucket": payload.get("churn_other_bucket", "include"),
            "churn_other_bucket_label": payload.get("churn_other_bucket_label", "Other"),
            "group_cols": _churn_flow_group_cols(tracker),
        })
        dispatcher.utter_message(json_message=_churn_filter_checkpoint_card(tracker))
        return events


class ActionEnterChurnFilterCheckpoint(Action):
    def name(self) -> str:
        return "action_enter_churn_filter_checkpoint"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        dispatcher.utter_message(json_message=_churn_filter_checkpoint_card(tracker))
        return []


class ActionProcessChurnFilterCheckpoint(Action):
    def name(self) -> str:
        return "action_process_churn_filter_checkpoint"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        choice = str(payload.get("churn_filter_manage_choice", "")).lower()
        if choice == "delete_all":
            events = [
                SlotSet("churn_filter_rules_json", "[]"),
                SlotSet("churn_filter_rules_display", ""),
            ]
            dispatcher.utter_message(json_message=_churn_filter_checkpoint_card(tracker))
            return events
        if choice == "add_rule":
            events = [SlotSet("active_filter_context", "churn")]
            events += ActionStartFilter().run(dispatcher, tracker, domain)
            return events
        if choice == "keep":
            events = _hydrate_column_roles_from_session_cache(tracker)
            _emit_churn_review_card(dispatcher, tracker)
            return events
        dispatcher.utter_message(text="Please choose option 1, 2, or 3.")
        dispatcher.utter_message(json_message=_churn_filter_checkpoint_card(tracker))
        return []


def _emit_churn_review_card(dispatcher: CollectingDispatcher, tracker: Tracker) -> None:
    headers = _headers_from_sources(tracker)
    header_opts_base = [{"label": h, "value": h} for h in headers]
    selected_cols = [
        _churn_slot_col(tracker, s) for s in (
            "churn_value_col", "churn_customer_col", "churn_product_col",
            "churn_start_col", "churn_end_col", "churn_invoice_col",
            "churn_group_col_1", "churn_group_col_2", "churn_group_col_3",
        )
    ]
    header_opts = _header_opts_for_review(tracker, None, *selected_cols)
    review_fy = int(_first_fy_for_churn_labels_card(tracker))
    level_count = _churn_review_group_level_count(tracker)
    group_cols = _churn_group_cols_from_tracker(tracker)
    single_group = len(group_cols) == 1

    inputs: list[dict[str, Any]] = [
        {"id": "ltm_month", "type": "text", "hidden": True, "required": False, "label": "",
         "default": _ltm_month_from_sources(tracker)},
        {"id": "churn_table", "type": "text", "label": "Table name", "span": 2,
         "default": _churn_flow_val(tracker, "churn_table", "ARR Bridge") or "ARR Bridge"},
        {"id": "churn_total_label", "type": "text", "label": "Total row label", "span": 2,
         "default": _churn_flow_val(tracker, "churn_total_label", "Total") or "Total"},
        {"id": "churn_first_fy_override", "type": "number",
         "label": "First fiscal year to include in the data source", "span": 2,
         "default": str(review_fy)},
        {"id": "churn_value_col", "type": "dropdown", "label": "Contract value column", "span": 2,
         "options": header_opts, "default": _churn_slot_col(tracker, "churn_value_col")},
        {"id": "churn_customer_col", "type": "dropdown", "label": "Customer ID column", "span": 2,
         "options": header_opts, "default": _churn_slot_col(tracker, "churn_customer_col")},
        {"id": "churn_product_col", "type": "dropdown", "label": "Product column", "span": 2,
         "options": header_opts, "default": _churn_slot_col(tracker, "churn_product_col")},
        {"id": "churn_start_col", "type": "dropdown", "label": "Contract start date", "span": 2,
         "options": header_opts, "default": _churn_slot_col(tracker, "churn_start_col")},
        {"id": "churn_end_col", "type": "dropdown", "label": "Contract end date", "span": 2,
         "options": header_opts, "default": _churn_slot_col(tracker, "churn_end_col")},
        {"id": "churn_invoice_col", "type": "dropdown", "label": "Invoice date column", "span": 2,
         "options": header_opts, "default": _churn_slot_col(tracker, "churn_invoice_col")},
    ]

    for i in range(1, level_count + 1):
        slot = f"churn_group_col_{i}"
        default = (group_cols[i - 1] if i <= len(group_cols) else "") or _churn_slot_col(tracker, slot)
        col_opts = header_opts_base
        if default and not any(o["value"] == default for o in col_opts):
            col_opts = [{"label": default, "value": default}] + col_opts
        inputs.append({
            "id": slot,
            "type": "dropdown",
            "label": f"Grouping column — level {i}",
            "span": 2,
            "options": col_opts,
            "default": default,
        })
        inputs.append({
            "id": f"churn_sort_by_{i}",
            "type": "radio",
            "label": f"Sort level {i} by (descending)",
            "layout": "split",
            "span": 2,
            "default": str(_churn_flow_val(tracker, f"churn_sort_by_{i}", "arr") or "arr"),
            "options": [
                {"label": "ARR (largest first)", "value": "arr"},
                {"label": "Alphabetical", "value": "name"},
            ],
        })

    if single_group:
        inputs += _churn_bucket_inputs_for_card(tracker)

    if bool(tracker.get_slot("apply_fx")):
        inputs.append({
            "id": "fx_col", "type": "text", "label": "FX column", "span": 2,
            "required": False,
            "default": str(tracker.get_slot("fx_col") or ""),
        })

    filter_disp = (tracker.get_slot("churn_filter_rules_display") or "").strip()
    card: dict[str, Any] = {
        "type": "adaptive_card",
        "card": "churn_proceed",
        "wide": True,
        "title": "Review — Churn / ARR Bridge",
        "subtitle": (
            f"{tracker.get_slot('project_name') or '—'}"
            f" · {tracker.get_slot('group_name') or '—'}"
        ),
        "inputs": inputs,
        "submit_label": "Proceed",
    }
    if filter_disp:
        card["filter_info"] = {
            "label": "Active filter rules",
            "value": filter_disp,
            "actions": [],
        }
    dispatcher.utter_message(json_message=card)


class ActionShowChurnReview(Action):
    def name(self) -> str:
        return "action_show_churn_review"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        events = _hydrate_column_roles_from_session_cache(tracker)
        _emit_churn_review_card(dispatcher, tracker)
        return events


class ActionRunChurn(Action):
    def name(self) -> str:
        return "action_run_churn"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        s = tracker.current_slot_values()

        flow = _churn_flow(tracker)

        def pick(key: str, default: Any = "") -> Any:
            v = payload.get(key)
            if v is None or v == "":
                v = s.get(key)
            if v is None or v == "":
                v = flow.get(key, default)
            return default if v in (None, "") else v

        ltm_month = str(_ltm_month_from_sources(tracker, payload) or "").strip()
        if not ltm_month:
            dispatcher.utter_message(
                text="Date settings are incomplete (last month to include is missing). "
                "Please go back to Date Settings and select the as-of month."
            )
            return []

        fy_m, fy_d = _fy_end_month_day_from_tracker({}, tracker)
        cy, cm = _as_of_year_month_from_ltm(ltm_month)
        fy_end_year = _fdd_latest_closed_fy_end_year(cy, cm, fy_m, fy_d)
        churn_first_fy = int(float(pick("churn_first_fy_override", _first_fy_for_churn_labels_card(tracker))))

        group_cols: list[str] = []
        for i in range(1, CHURN_MAX_GROUP_LEVELS + 1):
            c = str(pick(f"churn_group_col_{i}", "") or "").strip()
            if c:
                group_cols.append(c)
        if not group_cols:
            cached = flow.get("group_cols")
            if isinstance(cached, list):
                group_cols = [str(c).strip() for c in cached if str(c).strip()]
        if not group_cols:
            dispatcher.utter_message(text="Please select at least one grouping column.")
            _emit_churn_review_card(dispatcher, tracker)
            return []

        project_name = s.get("project_name", "")
        group_name = s.get("group_name", "")
        churn_table = pick("churn_table", s.get("churn_table") or "ARR Bridge")
        churn_total_label = pick("churn_total_label", s.get("churn_total_label") or "Total")
        value_col = pick("churn_value_col", s.get("churn_value_col", ""))
        customer_col = pick("churn_customer_col", s.get("churn_customer_col", ""))
        product_col = pick("churn_product_col", s.get("churn_product_col", ""))
        start_col = pick("churn_start_col", s.get("churn_start_col", ""))
        end_col = pick("churn_end_col", s.get("churn_end_col", ""))
        invoice_col = pick("churn_invoice_col", s.get("churn_invoice_col", ""))
        bucket_on = str(pick("churn_top_bucket_enabled", "off")).lower() == "on"

        events: list[Any] = _hydrate_column_roles_from_session_cache(tracker)
        events += [
            SlotSet("churn_table", churn_table),
            SlotSet("churn_total_label", churn_total_label),
            SlotSet("churn_first_fy_override", float(churn_first_fy)),
            SlotSet("churn_value_col", value_col),
            SlotSet("churn_customer_col", customer_col),
            SlotSet("churn_product_col", product_col),
            SlotSet("churn_start_col", start_col),
            SlotSet("churn_end_col", end_col),
            SlotSet("churn_invoice_col", invoice_col),
            SlotSet("churn_top_bucket_enabled", bucket_on),
            SlotSet("churn_bucket_mode", pick("churn_bucket_mode", s.get("churn_bucket_mode") or "threshold")),
            SlotSet("churn_bucket_thresholds", pick("churn_bucket_thresholds", "[0.2, 0.5, 0.8]")),
            SlotSet("churn_bucket_numbers", pick("churn_bucket_numbers", "")),
            SlotSet("churn_other_bucket", pick("churn_other_bucket", "include")),
            SlotSet("churn_other_bucket_label", pick("churn_other_bucket_label", "Other")),
            SlotSet("churn_group_level_count", len(group_cols)),
        ]
        for i in range(1, CHURN_MAX_GROUP_LEVELS + 1):
            col = group_cols[i - 1] if i <= len(group_cols) else ""
            events.append(SlotSet(f"churn_group_col_{i}", col))
            events.append(SlotSet(f"churn_sort_by_{i}", pick(f"churn_sort_by_{i}", "arr")))
        _remember_session_first_fy(tracker, float(churn_first_fy))

        body = {
            "session_id": _fdd_session_id(tracker),
            "file_id": tracker.get_slot("file_id"),
            "config": {
                "title": project_name,
                "company": group_name,
                "table": churn_table,
                "total_label": churn_total_label,
                "first_fy": churn_first_fy,
                "fy_end_year": fy_end_year,
                "fy_end_month": fy_m,
                "fy_end_day": fy_d,
                "current_year": cy,
                "current_month": cm,
                "as_of_year": cy,
                "as_of_month": cm,
                "ltm_month": ltm_month,
                "sheet_name": s.get("sheet_name", ""),
                "base_sheet_name": "Churn",
                "formula_mode": True,
                "value_col": value_col,
                "customer_col": customer_col,
                "product_col": product_col,
                "start_col": start_col,
                "end_col": end_col,
                "invoice_col": invoice_col,
                "group_cols": group_cols,
                "sort": _churn_sort_config_from_payload(tracker, payload),
                "top_bucket": _churn_top_bucket_config(tracker, payload),
                "apply_fx": bool(s.get("apply_fx")),
                "fx_col": s.get("fx_col") or "",
                "output_file_path": s.get("output_folder", ""),
                "filters": _fdd_filters_payload(pick("churn_filter_rules_json", s.get("churn_filter_rules_json"))),
            },
        }
        _start_async_revenue_script(dispatcher, body, "churn", "Churn / ARR Bridge")
        return events


# ─── Bubble Scatter Plot flow ─────────────────────────────────────────────────


def _build_bs_period_calc_card(headers: list[str], defaults: dict | None = None) -> dict:
    defaults = defaults or {}

    def _opts(slot: str) -> list[dict[str, str]]:
        dv = str(defaults.get(slot) or "")
        opts = [{"label": h, "value": h} for h in headers]
        if dv and not any(o["value"] == dv for o in opts):
            opts = [{"label": dv, "value": dv}] + opts
        return opts

    return {
        "type": "adaptive_card",
        "card": "bs_period_calc",
        "title": "Bubble Scatter Plot — Period & Calculation",
        "subtitle": "Period uses your Date Settings as-of month.",
        "inputs": [
            {
                "id": "bs_period_mode",
                "type": "radio",
                "label": "Which period would you like to analyse?",
                "default": "FY",
                "options": [
                    {"label": "Full Fiscal Year (FY)", "value": "FY"},
                    {"label": "Year-to-Date (YTD)", "value": "YTD"},
                    {"label": "Last Twelve Months (LTM)", "value": "LTM"},
                ],
            },
            {
                "id": "bs_calc_mode",
                "type": "radio",
                "label": "Select calculation mode",
                "default": "invoice",
                "options": [
                    {"label": "Invoiced amounts", "value": "invoice"},
                    {"label": "Accrual-based revenue", "value": "accrual"},
                ],
            },
            {
                "id": "bs_invoice_col",
                "type": "dropdown",
                "label": "Invoice / booking date column",
                "options": _opts("bs_invoice_col"),
                "default": str(defaults.get("bs_invoice_col") or ""),
            },
            {
                "id": "bs_start_col",
                "type": "dropdown",
                "label": "Contract start date column",
                "options": _opts("bs_start_col"),
                "default": str(defaults.get("bs_start_col") or ""),
                "showWhen": {"field": "bs_calc_mode", "value": "accrual"},
            },
            {
                "id": "bs_end_col",
                "type": "dropdown",
                "label": "Contract end date column",
                "options": _opts("bs_end_col"),
                "default": str(defaults.get("bs_end_col") or ""),
                "showWhen": {"field": "bs_calc_mode", "value": "accrual"},
            },
        ],
        "submit_label": "Continue",
    }


def _bs_other_bucket_parent_choice(tracker: Tracker, payload: dict | None = None) -> str:
    if payload is not None:
        raw = payload.get("bs_other_bucket_parent")
        if raw in ("include", "disable"):
            return str(raw)
    raw_slot = tracker.get_slot("bs_other_bucket_parent")
    if raw_slot in ("include", "disable"):
        return str(raw_slot)
    legacy = tracker.get_slot("bs_other_bucket")
    if legacy in ("include", "disable"):
        return str(legacy)
    return "include"


def _bs_other_bucket_child_choice(tracker: Tracker, payload: dict | None = None) -> str:
    if payload is not None:
        raw = payload.get("bs_other_bucket_child")
        if raw in ("include", "disable"):
            return str(raw)
    raw_slot = tracker.get_slot("bs_other_bucket_child")
    if raw_slot in ("include", "disable"):
        return str(raw_slot)
    legacy = tracker.get_slot("bs_other_bucket")
    if legacy in ("include", "disable"):
        return str(legacy)
    return "include"


def _bs_filters_payload(rules_json: str | None) -> dict:
    return _fdd_filters_payload(rules_json)


class ActionProcessBsPeriodCalc(Action):
    def name(self) -> str:
        return "action_process_bs_period_calc"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        calc_mode = payload.get("bs_calc_mode", "invoice")
        events = [
            SlotSet("bs_period_mode", payload.get("bs_period_mode", "FY")),
            SlotSet("bs_calc_mode", calc_mode),
        ]
        for k in ("bs_invoice_col", "bs_start_col", "bs_end_col"):
            if k in payload:
                events.extend(_sync_column_role_slots(tracker, k, payload.get(k)))
        _remember_bs_settings(tracker, payload)

        headers = _headers_from_sources(tracker, payload)

        def _bs_val_opts(slot: str) -> list[dict[str, str]]:
            dv = str(_column_default(tracker, slot, payload) or "")
            opts = [{"label": h, "value": h} for h in headers]
            if dv and not any(o["value"] == dv for o in opts):
                opts = [{"label": dv, "value": dv}] + opts
            return opts

        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "bs_value_columns",
                "title": "Bubble Scatter Plot — Value Columns",
                "inputs": [
                    {
                        "id": "bs_profit_mode",
                        "type": "radio",
                        "label": "Gross profit calculation",
                        "default": "cost",
                        "options": [
                            {"label": "Use a cost column (GP = Revenue − Cost)", "value": "cost"},
                            {"label": "Use an existing gross profit column", "value": "profit"},
                        ],
                    },
                    {
                        "id": "bs_revenue_col",
                        "type": "dropdown",
                        "label": "Revenue column",
                        "options": _bs_val_opts("bs_revenue_col"),
                        "default": str(_column_default(tracker, "bs_revenue_col", payload) or ""),
                    },
                    {
                        "id": "bs_cogs_col",
                        "type": "dropdown",
                        "label": "Cost of goods sold column",
                        "options": _bs_val_opts("bs_cogs_col"),
                        "default": str(_column_default(tracker, "bs_cogs_col", payload) or ""),
                        "showWhen": {"field": "bs_profit_mode", "value": "cost"},
                    },
                    {
                        "id": "bs_profit_col",
                        "type": "dropdown",
                        "label": "Gross profit column",
                        "options": _bs_val_opts("bs_profit_col"),
                        "default": str(_column_default(tracker, "bs_profit_col", payload) or ""),
                        "showWhen": {"field": "bs_profit_mode", "value": "profit"},
                    },
                ],
                "submit_label": "Continue",
            }
        )
        return events


class ActionProcessBsValueColumns(Action):
    def name(self) -> str:
        return "action_process_bs_value_columns"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        events = [SlotSet("bs_profit_mode", payload.get("bs_profit_mode", "cost"))]
        for k in ("bs_revenue_col", "bs_cogs_col", "bs_profit_col"):
            if k in payload:
                events.extend(_sync_column_role_slots(tracker, k, payload.get(k)))
        _remember_bs_settings(tracker, payload)

        headers = _headers_from_sources(tracker, payload)
        none_opt = [{"label": "(none)", "value": ""}]
        bs_g1_default = str(_column_default(tracker, "bs_group_col_1", payload) or "")
        bs_g1_opts = [{"label": h, "value": h} for h in headers]
        if bs_g1_default and not any(o["value"] == bs_g1_default for o in bs_g1_opts):
            bs_g1_opts = [{"label": bs_g1_default, "value": bs_g1_default}] + bs_g1_opts
        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "bs_groups",
                "title": "Bubble Scatter Plot — Grouping",
                "subtitle": (
                    "One column = flat bubbles. Two columns = parent | child hierarchy "
                    "(shown as Parent|Child in the chart)."
                ),
                "inputs": [
                    {
                        "id": "bs_group_col_1",
                        "type": "dropdown",
                        "label": "Parent / grouping column 1 (required)",
                        "options": bs_g1_opts,
                        "default": bs_g1_default,
                    },
                    {
                        "id": "bs_max_items_parent",
                        "type": "number",
                        "label": "Max items from column 1 (overflow → Other bucket)",
                        "placeholder": "6",
                        "default": "6",
                        "required": True,
                    },
                    {
                        "id": "bs_other_bucket_parent",
                        "type": "radio",
                        "label": "Other bucket — column 1 (parent groups)",
                        "default": "include",
                        "options": [
                            {"label": "Include", "value": "include"},
                            {"label": "Disable", "value": "disable"},
                        ],
                    },
                    {
                        "id": "bs_other_bucket_label",
                        "type": "text",
                        "label": "Label for the 'Other' bucket (parent and child)",
                        "placeholder": "Other",
                        "default": "Other",
                    },
                    {
                        "id": "bs_group_col_2",
                        "type": "dropdown",
                        "label": "Child column 2 (optional — enables hierarchy)",
                        "options": none_opt + [{"label": h, "value": h} for h in headers],
                        "required": False,
                    },
                    {
                        "id": "bs_max_items_child",
                        "type": "number",
                        "label": "Max child items per parent from column 2 (max. 6)",
                        "placeholder": "6",
                        "default": "6",
                        "required": False,
                    },
                    {
                        "id": "bs_other_bucket_child",
                        "type": "radio",
                        "label": "Other bucket — column 2 (children per parent)",
                        "default": "include",
                        "options": [
                            {"label": "Include", "value": "include"},
                            {"label": "Disable", "value": "disable"},
                        ],
                    },
                ],
                "submit_label": "Continue",
            }
        )
        return events


class ActionProcessBsGroups(Action):
    def name(self) -> str:
        return "action_process_bs_groups"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)

        def _cap_items(val: Any, default: int = 6) -> float:
            try:
                n = int(float(val))
            except (TypeError, ValueError):
                n = default
            return float(max(1, min(6, n)))

        other_parent = _bs_other_bucket_parent_choice(tracker, payload)
        other_child = _bs_other_bucket_child_choice(tracker, payload)
        other_label = str(payload.get("bs_other_bucket_label", "Other") or "Other").strip() or "Other"
        events = _sync_column_role_slots(tracker, "bs_group_col_1", payload.get("bs_group_col_1"))
        events += [
            SlotSet("bs_group_col_2", payload.get("bs_group_col_2") or ""),
            SlotSet("bs_max_items_parent", _cap_items(payload.get("bs_max_items_parent", 6))),
            SlotSet(
                "bs_max_items_child",
                _cap_items(payload.get("bs_max_items_child", 6)),
            ),
            SlotSet("bs_other_bucket_parent", other_parent),
            SlotSet("bs_other_bucket_child", other_child),
            SlotSet("bs_other_bucket_label", other_label),
        ]
        _remember_bs_settings(tracker, payload)
        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "bs_labels",
                "title": "Bubble Scatter Plot — Chart Labels",
                "inputs": [
                    {
                        "id": "bs_table_name",
                        "type": "text",
                        "label": "Table / chart title",
                        "placeholder": "Top product groups",
                        "default": "Top product groups",
                    },
                    {
                        "id": "bs_subtitle_suffix",
                        "type": "text",
                        "label": "Subtitle suffix (optional)",
                        "placeholder": "GP by segment",
                        "required": False,
                    },
                ],
                "submit_label": "Continue",
            }
        )
        return events


class ActionProcessBsLabels(Action):
    def name(self) -> str:
        return "action_process_bs_labels"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        events = [
            SlotSet("bs_table_name", payload.get("bs_table_name", "Top product groups")),
            SlotSet("bs_subtitle_suffix", payload.get("bs_subtitle_suffix", "")),
        ]
        _remember_bs_settings(tracker, payload)
        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "bs_display_options",
                "title": "Bubble Scatter Plot — Display Options",
                "inputs": [
                    {
                        "id": "bs_y_unit",
                        "type": "radio",
                        "label": "Y-axis unit (gross profit)",
                        "default": "m",
                        "options": [
                            {"label": "Millions (EURm)", "value": "m"},
                            {"label": "Thousands (EURk)", "value": "k"},
                        ],
                    },
                ],
                "submit_label": "Continue",
            }
        )
        return events


class ActionProcessBsDisplayOptions(Action):
    def name(self) -> str:
        return "action_process_bs_display_options"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        _remember_bs_settings(tracker, payload)
        return [
            SlotSet("bs_y_unit", payload.get("bs_y_unit", "m")),
            FollowupAction("action_enter_bs_filter_checkpoint"),
        ]


class ActionEnterBsFilterCheckpoint(Action):
    def name(self) -> str:
        return "action_enter_bs_filter_checkpoint"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        dispatcher.utter_message(json_message=_bs_filter_checkpoint_card(tracker))
        return []


class ActionProcessBsFilterCheckpoint(Action):
    def name(self) -> str:
        return "action_process_bs_filter_checkpoint"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        choice = str(payload.get("bs_filter_manage_choice", "")).lower()

        if choice == "delete_all":
            return [
                SlotSet("bs_filter_rules_json", "[]"),
                SlotSet("bs_filter_rules_display", ""),
                FollowupAction("action_enter_bs_filter_checkpoint"),
            ]
        if choice == "add_rule":
            return [
                SlotSet("active_filter_context", "bs"),
                FollowupAction("action_start_filter"),
            ]
        if choice == "keep":
            return [FollowupAction("action_show_bs_review")]

        dispatcher.utter_message(
            text="Please choose option 1, 2, or 3 so we know how to handle your filter rules."
        )
        return [FollowupAction("action_enter_bs_filter_checkpoint")]


class ActionShowBsReview(Action):
    def name(self) -> str:
        return "action_show_bs_review"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        cfg = _bs_settings_from_sources(tracker)
        headers = _headers_from_sources(tracker)
        header_opts = [{"label": h, "value": h} for h in headers]
        none_opt = [{"label": "(none)", "value": ""}]
        filter_disp = (
            str(tracker.get_slot("bs_filter_rules_display") or "").strip()
            or "No active filter rules."
        )
        ltm_default = _ltm_month_from_tracker(tracker) or ""
        other_parent = _bs_other_bucket_parent_choice(tracker)
        other_child = _bs_other_bucket_child_choice(tracker)
        other_label = str(cfg.get("bs_other_bucket_label") or "Other")
        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "bs_proceed",
                "compact": True,
                "title": "Review — Bubble Scatter Plot",
                "subtitle": f"{(cfg.get('project_name') or '—')} · {(cfg.get('group_name') or '—')}",
                "review_text": filter_disp,
                "inputs": [
                    {"id": "ltm_month", "type": "text", "label": "", "default": ltm_default,
                     "required": False, "hidden": True},
                    {"id": "file_id", "type": "text", "label": "", "default": str(cfg.get("file_id") or ""),
                     "required": False, "hidden": True},
                    {"id": "sheet_name", "type": "text", "label": "",
                     "default": str(cfg.get("sheet_name") or ""), "required": False, "hidden": True},
                    {"id": "project_name", "type": "text", "label": "Project", "span": 1,
                     "default": str(cfg.get("project_name") or "")},
                    {"id": "group_name", "type": "text", "label": "Group", "span": 1,
                     "default": str(cfg.get("group_name") or "")},
                    {"id": "bs_period_mode", "type": "radio", "label": "Period", "layout": "split", "span": 2,
                     "options": [
                         {"label": "FY", "value": "FY"},
                         {"label": "YTD", "value": "YTD"},
                         {"label": "LTM", "value": "LTM"},
                     ],
                     "default": str(cfg.get("bs_period_mode") or "FY")},
                    {"id": "bs_calc_mode", "type": "radio", "label": "Calc mode", "layout": "split", "span": 2,
                     "options": [
                         {"label": "Invoice", "value": "invoice"},
                         {"label": "Accrual", "value": "accrual"},
                     ],
                     "default": str(cfg.get("bs_calc_mode") or "invoice")},
                    {"id": "bs_invoice_col", "type": "dropdown", "label": "Invoice date",
                     "options": header_opts, "default": str(cfg.get("bs_invoice_col") or "")},
                    {"id": "bs_start_col", "type": "dropdown", "label": "Contract start",
                     "options": header_opts, "default": str(cfg.get("bs_start_col") or ""),
                     "showWhen": {"field": "bs_calc_mode", "value": "accrual"}},
                    {"id": "bs_end_col", "type": "dropdown", "label": "Contract end",
                     "options": header_opts, "default": str(cfg.get("bs_end_col") or ""),
                     "showWhen": {"field": "bs_calc_mode", "value": "accrual"}},
                    {"id": "bs_profit_mode", "type": "radio", "label": "GP mode", "layout": "split", "span": 2,
                     "options": [
                         {"label": "Cost column", "value": "cost"},
                         {"label": "Profit column", "value": "profit"},
                     ],
                     "default": str(cfg.get("bs_profit_mode") or "cost")},
                    {"id": "bs_revenue_col", "type": "dropdown", "label": "Revenue",
                     "options": header_opts, "default": str(cfg.get("bs_revenue_col") or "")},
                    {"id": "bs_cogs_col", "type": "dropdown", "label": "COGS",
                     "options": header_opts, "default": str(cfg.get("bs_cogs_col") or ""),
                     "showWhen": {"field": "bs_profit_mode", "value": "cost"}},
                    {"id": "bs_profit_col", "type": "dropdown", "label": "Gross profit",
                     "options": header_opts, "default": str(cfg.get("bs_profit_col") or ""),
                     "showWhen": {"field": "bs_profit_mode", "value": "profit"}},
                    {"id": "bs_group_col_1", "type": "dropdown", "label": "Group column 1",
                     "options": header_opts, "default": str(cfg.get("bs_group_col_1") or "")},
                    {"id": "bs_group_col_2", "type": "dropdown", "label": "Group column 2",
                     "options": none_opt + header_opts, "default": str(cfg.get("bs_group_col_2") or ""),
                     "required": False},
                    {"id": "bs_max_items_parent", "type": "number",
                     "label": "Max items — column 1",
                     "default": str(int(float(cfg.get("bs_max_items_parent") or 6)))},
                    {"id": "bs_max_items_child", "type": "number",
                     "label": "Max children per parent — column 2",
                     "default": str(int(float(cfg.get("bs_max_items_child") or 6)))},
                    {"id": "bs_table_name", "type": "text", "label": "Chart title",
                     "default": str(cfg.get("bs_table_name") or "Top product groups")},
                    {"id": "bs_subtitle_suffix", "type": "text", "label": "Subtitle suffix",
                     "default": str(cfg.get("bs_subtitle_suffix") or ""), "required": False},
                    {"id": "bs_other_bucket_parent", "type": "text", "label": "",
                     "default": other_parent, "required": False, "hidden": True},
                    {"id": "bs_other_bucket_child", "type": "text", "label": "",
                     "default": other_child, "required": False, "hidden": True},
                    {"id": "bs_other_bucket_label", "type": "text", "label": "",
                     "default": other_label, "required": False, "hidden": True},
                    {"id": "bs_y_unit", "type": "radio", "label": "Y-axis unit",
                     "options": [
                         {"label": "Millions", "value": "m"},
                         {"label": "Thousands", "value": "k"},
                     ],
                     "default": str(cfg.get("bs_y_unit") or "m")},
                ],
                "submit_label": "Run Bubble Scatter Plot",
            }
        )
        return []


class ActionRunBubble(Action):
    def name(self) -> str:
        return "action_run_bubble"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        cfg = _bs_settings_from_sources(tracker, payload)
        _remember_bs_settings(tracker, payload)

        def pick(key: str, default=None):
            v = payload.get(key, None)
            if v is not None and v != "":
                return v
            cv = cfg.get(key)
            if cv is not None and cv != "":
                return cv
            sv = tracker.get_slot(key)
            return default if sv is None or sv == "" else sv

        ltm_month = _ltm_month_from_sources(tracker, payload)
        if not ltm_month:
            dispatcher.utter_message(
                text=(
                    "Date settings are incomplete (last month to include is missing). "
                    "Please go back to Date Settings and select the as-of month, then run again."
                )
            )
            return []

        dp = _fdd_date_params_from_tracker(tracker)
        if ltm_month and not dp.get("ltm_month"):
            cy, cm = _as_of_year_month_from_ltm(ltm_month)
            dp["current_year"] = cy
            dp["current_month"] = cm
            dp["as_of_year"] = cy
            dp["as_of_month"] = cm
            dp["ltm_month"] = ltm_month

        profit_mode = pick("bs_profit_mode", "cost")
        calc_mode = pick("bs_calc_mode", "invoice")
        other_parent = _bs_other_bucket_parent_choice(tracker, payload)
        other_child = _bs_other_bucket_child_choice(tracker, payload)
        other_label = str(pick("bs_other_bucket_label", "Other") or "Other").strip() or "Other"
        y_unit = str(pick("bs_y_unit", "m") or "m").lower()
        y_unit_label = "in EURk" if y_unit == "k" else "in EURm"

        group_cols: list[str] = []
        g1 = str(pick("bs_group_col_1", "") or "").strip()
        g2 = str(pick("bs_group_col_2", "") or "").strip()
        if g1:
            group_cols.append(g1)
        if g2:
            group_cols.append(g2)

        session_id = _fdd_session_id(tracker)
        file_id = str(pick("file_id", "") or "").strip()
        if not file_id:
            dispatcher.utter_message(
                text=(
                    "No uploaded file is linked to this session. "
                    "Please upload your Excel file again before running the Bubble Scatter Plot."
                )
            )
            return []

        output_folder = str(tracker.get_slot("output_folder") or "")
        output_path = os.path.join(output_folder, f"{session_id}_Bubble_Output.xlsx")

        revenue_col = str(pick("bs_revenue_col", "") or "").strip()
        invoice_col = str(pick("bs_invoice_col", "") or "").strip()
        if not revenue_col or not invoice_col:
            dispatcher.utter_message(
                text=(
                    "Revenue or invoice date column is missing. "
                    "Please go back and complete the Bubble column selection cards."
                )
            )
            return []

        body = {
            "session_id": session_id,
            "file_id": file_id,
            "config": {
                "title": pick("project_name", ""),
                "company": pick("group_name", ""),
                "table": pick("bs_table_name", "Top product groups"),
                "subtitle_suffix": pick("bs_subtitle_suffix", ""),
                "sheet_name": str(pick("sheet_name", "") or ""),
                "period_mode": pick("bs_period_mode", "FY"),
                "calc_mode": calc_mode,
                "invoice_col": invoice_col,
                "start_col": pick("bs_start_col", "") or "",
                "end_col": pick("bs_end_col", "") or "",
                "profit_mode": profit_mode,
                "revenue_col": revenue_col,
                "cogs_col": pick("bs_cogs_col", "") or "",
                "profit_col": pick("bs_profit_col", "") or "",
                "group_cols": group_cols,
                "ltm_month": ltm_month,
                "current_year": dp["current_year"],
                "current_month": dp["current_month"],
                "fiscal_year_end_month": dp["fiscal_year_end_month"],
                "fiscal_year_end_day": dp["fiscal_year_end_day"],
                "apply_fx": bool(pick("apply_fx", False)),
                "fx_col": str(pick("fx_col", "") or ""),
                "grouping": {
                    "create_other_bucket_parent": other_parent != "disable",
                    "create_other_bucket_child": other_child != "disable",
                    "other_label": other_label,
                    "max_items_parent": max(
                        1,
                        min(
                            6,
                            int(float(pick("bs_max_items_parent", 6) or 6)),
                        ),
                    ),
                    "max_items_child": max(
                        1,
                        min(
                            6,
                            int(float(pick("bs_max_items_child", 6) or 6)),
                        ),
                    ),
                },
                "plot": {
                    "y_unit": y_unit,
                    "y_unit_label": y_unit_label,
                },
                "output_file_path": output_folder,
                "output_path": output_path,
                "filters": _bs_filters_payload(
                    pick("bs_filter_rules_json", tracker.get_slot("bs_filter_rules_json"))
                ),
            },
        }

        _start_async_revenue_script(
            dispatcher,
            body,
            "bubble",
            "Bubble Scatter Plot",
        )

        return []


# ─── Fixed Assets Rollforward flow ─────────────────────────────────────────────

FA_MAX_GROUP_LEVELS = 2


def _fa_rollf_files_card(tracker: Tracker) -> dict[str, Any]:
    years = _compute_databook_period_labels(tracker)
    grid_options = _databook_grid_options(tracker)
    has_ytd = any(is_ytd_grid_label(y) for y in years)
    note = "One Anlagengitter (.xlsx) per fiscal year."
    if has_ytd:
        note += " YTD column is optional (upload only if available)."
    return {
        "type": "adaptive_card",
        "card": "fa_rollf_files",
        "title": "Fixed Assets — Register files",
        "subtitle": "Upload your annual fixed-asset register extracts for each period.",
        "inputs": [
            {
                "id": "susa_files",
                "type": "susa_grid",
                "label": "Register files",
                "options": grid_options,
                "entity_count": 1,
                "grid_mode": "single_file",
                "fixed_entity_name": "Fixed assets",
            }
        ],
        "susa_grid_note": note,
        "submit_label": "Continue",
    }


def _fa_periods_from_grid(groups: dict[str, list[str]], tracker: Tracker) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for key, ids in groups.items():
        if not ids:
            continue
        parts = str(key).split("_")
        if len(parts) < 3 or parts[0] != "entity":
            continue
        fy_label = "_".join(parts[2:])
        rows.append({"label": fy_label, "file_id": str(ids[0])})
    order = {label: i for i, label in enumerate(_compute_databook_period_labels(tracker))}
    rows.sort(key=lambda r: order.get(r["label"], 999))
    return rows


def _fa_mapped_column_names(tracker: Tracker) -> set[str]:
    names: set[str] = set()
    cols = _fa_columns_from_sources(tracker)
    for key in ("opening", "additions", "disposals"):
        v = str(cols.get(key) or "").strip()
        if v:
            names.add(v)
    for c in cols.get("depreciation_cols") or []:
        s = str(c).strip()
        if s:
            names.add(s)
    for slot in ("fa_opening_col", "fa_additions_col", "fa_disposals_col"):
        v = str(tracker.get_slot(slot) or "").strip()
        if v:
            names.add(v)
    raw_dep = tracker.get_slot("fa_depreciation_cols_json") or "[]"
    try:
        dep_cols = json.loads(raw_dep)
    except (json.JSONDecodeError, TypeError):
        dep_cols = []
    if isinstance(dep_cols, list):
        for c in dep_cols:
            s = str(c).strip()
            if s:
                names.add(s)
    legacy = str(tracker.get_slot("fa_depreciation_col") or "").strip()
    if legacy:
        names.add(legacy)
    return names


def _emit_fa_rollf_columns_card(
    dispatcher: CollectingDispatcher,
    tracker: Tracker,
    periods: list[dict[str, Any]] | None = None,
) -> None:
    if periods is None:
        periods = _fa_periods_from_sources(tracker)
    preview_id = ""
    preview_sheet = ""
    if periods:
        preview_id = str(periods[0].get("file_id") or tracker.get_slot("file_id") or "")
        preview_sheet = str(periods[0].get("sheet_name") or "").strip()
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "fa_rollf_columns",
            "wide": True,
            "title": "Map fixed-asset columns",
            "subtitle": (
                "Click a column header for opening, additions, and disposals. "
                "Then select one or more depreciation columns to sum and confirm."
            ),
            "mapper_meta": {
                "session_id": _fdd_session_id(tracker),
                "preview_file_id": preview_id,
                "sheet_name": preview_sheet,
                "header_row": 0,
            },
            "inputs": [],
            "submit_label": "Continue",
        }
    )


def _emit_fa_rollf_grouping_card(dispatcher: CollectingDispatcher, tracker: Tracker) -> None:
    periods = _fa_periods_from_sources(tracker)
    headers = _headers_union_for_periods(tracker, periods) if periods else []
    if not headers:
        headers = _headers_from_sources(tracker)
    mapped = _fa_mapped_column_names(tracker)
    dim_opts = [{"label": "None", "value": ""}] + [{"label": h, "value": h} for h in headers if h and h not in mapped]
    inputs: list[dict[str, Any]] = [
        {
            "id": "fa_group_col_1",
            "type": "dropdown",
            "label": "Grouping — level 1 (e.g. Bilanzposition)",
            "options": dim_opts,
            "default": str(tracker.get_slot("fa_group_col_1") or ""),
        },
        {
            "id": "fa_group_col_2",
            "type": "dropdown",
            "label": "Grouping — level 2 (optional, e.g. Anlagenklasse)",
            "options": dim_opts,
            "default": str(tracker.get_slot("fa_group_col_2") or ""),
            "required": False,
        },
    ]
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "fa_rollf_grouping",
            "title": "Fixed assets — Grouping",
            "subtitle": "Choose up to two grouping columns for the rollforward hierarchy.",
            "inputs": inputs,
            "submit_label": "Continue",
        }
    )


def _fa_group_cols_from_sources(tracker: Tracker, payload: dict | None = None) -> list[str]:
    st = _fa_state(tracker)
    cached = st.get("group_cols")
    if isinstance(cached, list) and cached:
        return [str(c).strip() for c in cached if str(c).strip()]
    p = payload or {}
    cols: list[str] = []
    for i in range(1, FA_MAX_GROUP_LEVELS + 1):
        raw = p.get(f"fa_group_col_{i}")
        if raw in (None, ""):
            raw = tracker.get_slot(f"fa_group_col_{i}")
        text = str(raw or "").strip()
        if text:
            cols.append(text)
    return cols


class ActionShowFaRollfFiles(Action):
    def name(self) -> str:
        return "action_show_fa_rollf_files"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        dispatcher.utter_message(json_message=_fa_rollf_files_card(tracker))
        return []


class ActionProcessFaRollfFiles(Action):
    def name(self) -> str:
        return "action_process_fa_rollf_files"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        groups = _parse_susa_grid_file_groups(payload)
        periods = _fa_periods_from_grid(groups, tracker)
        if not periods:
            dispatcher.utter_message(text="Please upload at least one period file in the grid.")
            dispatcher.utter_message(json_message=_fa_rollf_files_card(tracker))
            return []
        first_id = periods[0]["file_id"]
        _fa_state(tracker)["periods"] = periods
        _sync_flow_state_to_disk(tracker)
        events: list[Any] = [
            SlotSet("fa_periods_json", json.dumps(periods, ensure_ascii=False)),
            SlotSet("file_id", first_id),
        ]
        _emit_fa_rollf_columns_card(dispatcher, tracker, periods)
        return events


class ActionReshowFaRollfColumns(Action):
    def name(self) -> str:
        return "action_reshow_fa_rollf_columns"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        _emit_fa_rollf_columns_card(dispatcher, tracker)
        return []


class ActionProcessFaRollfColumns(Action):
    def name(self) -> str:
        return "action_process_fa_rollf_columns"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        required = ("fa_opening_col", "fa_additions_col", "fa_disposals_col")
        events: list[Any] = []
        for slot in required:
            val = str(payload.get(slot) or "").strip()
            if not val:
                dispatcher.utter_message(text="Please map opening, additions, and disposals before continuing.")
                _emit_fa_rollf_columns_card(dispatcher, tracker)
                return []
            events.append(SlotSet(slot, val))

        raw_dep = payload.get("fa_depreciation_cols_json", tracker.get_slot("fa_depreciation_cols_json") or "[]")
        if isinstance(raw_dep, list):
            dep_cols = [str(c).strip() for c in raw_dep if str(c).strip()]
        else:
            try:
                parsed = json.loads(str(raw_dep or "[]"))
            except (json.JSONDecodeError, TypeError):
                parsed = []
            dep_cols = [str(c).strip() for c in parsed if str(c).strip()] if isinstance(parsed, list) else []
        if not dep_cols:
            dispatcher.utter_message(text="Please select at least one depreciation column to sum.")
            _emit_fa_rollf_columns_card(dispatcher, tracker)
            return []

        events.append(SlotSet("fa_depreciation_cols_json", json.dumps(dep_cols, ensure_ascii=False)))
        _fa_state(tracker)["columns"] = {
            "opening": str(payload.get("fa_opening_col") or "").strip(),
            "additions": str(payload.get("fa_additions_col") or "").strip(),
            "disposals": str(payload.get("fa_disposals_col") or "").strip(),
            "depreciation_cols": dep_cols,
        }
        _sync_flow_state_to_disk(tracker)
        _emit_fa_rollf_grouping_card(dispatcher, tracker)
        return events


class ActionShowFaRollfGrouping(Action):
    def name(self) -> str:
        return "action_show_fa_rollf_grouping"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        _emit_fa_rollf_grouping_card(dispatcher, tracker)
        return []


class ActionProcessFaRollfGrouping(Action):
    def name(self) -> str:
        return "action_process_fa_rollf_grouping"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        events: list[Any] = []
        for i in range(1, FA_MAX_GROUP_LEVELS + 1):
            slot = f"fa_group_col_{i}"
            if slot in payload:
                events.append(SlotSet(slot, str(payload.get(slot) or "").strip()))
        group_cols = _fa_group_cols_from_sources(tracker, payload)
        if len(group_cols) >= 2 and group_cols[0] == group_cols[1]:
            dispatcher.utter_message(text="Level 1 and level 2 must be different columns.")
            _emit_fa_rollf_grouping_card(dispatcher, tracker)
            return events
        _fa_state(tracker)["group_cols"] = group_cols
        _sync_flow_state_to_disk(tracker)
        dispatcher.utter_message(json_message=_fa_filter_checkpoint_card(tracker))
        return events


def _fa_filter_checkpoint_card(tracker: Tracker) -> dict[str, Any]:
    disp = (tracker.get_slot("fa_filter_rules_display") or "").strip() or "No active filter rules."
    return {
        "type": "adaptive_card",
        "card": "fa_filter_checkpoint",
        "title": "Fixed assets — Filter rules",
        "review_text": disp,
        "inputs": [
            {
                "id": "fa_filter_manage_choice",
                "type": "radio",
                "label": "What would you like to do?",
                "options": [
                    {"label": "1. Delete current rules", "value": "delete_all"},
                    {"label": "2. Add a rule", "value": "add_rule"},
                    {"label": "3. Keep rules as they are", "value": "keep"},
                ],
            }
        ],
        "submit_label": "Continue",
    }


class ActionEnterFaFilterCheckpoint(Action):
    def name(self) -> str:
        return "action_enter_fa_filter_checkpoint"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        dispatcher.utter_message(json_message=_fa_filter_checkpoint_card(tracker))
        return []


class ActionProcessFaFilterCheckpoint(Action):
    def name(self) -> str:
        return "action_process_fa_filter_checkpoint"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        choice = str(payload.get("fa_filter_manage_choice", "")).lower()
        if choice == "delete_all":
            return [
                SlotSet("fa_filter_rules_json", "[]"),
                SlotSet("fa_filter_rules_display", ""),
                FollowupAction("action_enter_fa_filter_checkpoint"),
            ]
        if choice == "add_rule":
            return [SlotSet("active_filter_context", "fa"), FollowupAction("action_start_filter")]
        if choice == "keep":
            return _enter_strand_run_target(
                dispatcher, tracker, "fa", pending_slot="fa_pending_rerun"
            )
        return [FollowupAction("action_enter_fa_filter_checkpoint")]


class ActionRunFaRollf(Action):
    def name(self) -> str:
        return "action_run_fa_rollf"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)

        periods = _fa_periods_from_sources(tracker)
        if not periods:
            dispatcher.utter_message(text="Fixed assets period files missing — please upload register files first.")
            return _inline_action(ActionShowFaRollfFiles(), dispatcher, tracker, domain)

        cols = _fa_columns_from_sources(tracker, payload)
        dep_cols = cols.get("depreciation_cols") or _fa_dep_cols_from_sources(tracker, payload)
        if not dep_cols:
            dispatcher.utter_message(text="Depreciation columns missing — please map columns first.")
            return _inline_action(ActionReshowFaRollfColumns(), dispatcher, tracker, domain)

        group_cols = _fa_group_cols_from_sources(tracker, payload)
        col_err = _validate_group_cols_in_periods(tracker, periods, group_cols)
        if col_err:
            dispatcher.utter_message(text=col_err)
            _emit_fa_rollf_grouping_card(dispatcher, tracker)
            return []

        fy_m, fy_d = _fy_end_month_day_from_tracker({}, tracker)
        workbook_fields = _strand_workbook_fields(tracker, suffix="FA_Rollf")
        fa_cfg: dict[str, Any] = {
            "title": tracker.get_slot("project_name") or payload.get("project_name") or "",
            "company": tracker.get_slot("group_name") or payload.get("group_name") or "",
            "first_fy": _tracker_first_fy_int(tracker),
            "fy_end_month": fy_m,
            "fy_end_day": fy_d,
            "periods": periods,
            "header_row": 0,
            "columns": {
                "opening": cols.get("opening") or "",
                "additions": cols.get("additions") or "",
                "disposals": cols.get("disposals") or "",
                "depreciation_cols": dep_cols,
            },
            "group_cols": group_cols,
            "filters": _fdd_filters_payload(
                tracker.get_slot("fa_filter_rules_json") or _fa_state(tracker).get("filter_rules_json") or "[]"
            ),
            "unit_label": "kEUR",
            "amount_scale": 1000,
            "output_file_path": tracker.get_slot("output_folder") or "",
            **workbook_fields,
        }
        if not workbook_fields.get("use_separate_workbook"):
            master = (
                tracker.get_slot("db_master_workbook_path")
                or _session_output_workbook_path(tracker)
                or ""
            )
            if master:
                fa_cfg["output_workbook_path"] = master
                fa_cfg["db_master_workbook_path"] = master
                fa_cfg["master_workbook_path"] = master
        body = {
            "session_id": _fdd_session_id(tracker),
            "file_id": periods[0].get("file_id") or tracker.get_slot("file_id"),
            "config": fa_cfg,
        }
        _start_async_revenue_script(dispatcher, body, "fixed_assets_rollf", "Fixed Assets Rollforward")
        return [SlotSet("output_type", "fixed_assets_rollforward")]


# ─── FTE & Payroll flow ───────────────────────────────────────────────────────

FTE_MAX_GROUP_LEVELS = 2


def _fte_files_card(tracker: Tracker) -> dict[str, Any]:
    grid_options = _databook_grid_options(tracker)
    has_ytd = any(is_ytd_grid_label(y) for y in _compute_databook_period_labels(tracker))
    note = "One personnel table (.xlsx) per fiscal year."
    if has_ytd:
        note += " YTD column is optional."
    return {
        "type": "adaptive_card",
        "card": "fte_files",
        "title": "FTE Development — Personnel files",
        "subtitle": "Upload your personnel / payroll table for each period.",
        "inputs": [
            {
                "id": "susa_files",
                "type": "susa_grid",
                "label": "Personnel files",
                "options": grid_options,
                "entity_count": 1,
                "grid_mode": "single_file",
                "fixed_entity_name": "FTE",
            }
        ],
        "susa_grid_note": note,
        "submit_label": "Continue",
    }


def _fte_periods_from_grid(groups: dict[str, list[str]], tracker: Tracker) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for key, ids in groups.items():
        if not ids:
            continue
        parts = str(key).split("_")
        if len(parts) < 3 or parts[0] != "entity":
            continue
        fy_label = "_".join(parts[2:])
        rows.append({"label": fy_label, "file_id": str(ids[0])})
    order = {label: i for i, label in enumerate(_compute_databook_period_labels(tracker))}
    rows.sort(key=lambda r: order.get(r["label"], 999))
    return rows


def _fte_mapped_column_names(tracker: Tracker) -> set[str]:
    names: set[str] = set()
    cols = _fte_columns_from_sources(tracker)
    for key in ("employment", "months_sum"):
        v = str(cols.get(key) or "").strip()
        if v:
            names.add(v)
    for c in cols.get("payroll_cols") or []:
        s = str(c).strip()
        if s:
            names.add(s)
    for slot in ("fte_employment_col", "fte_months_col"):
        v = str(tracker.get_slot(slot) or "").strip()
        if v:
            names.add(v)
    raw = tracker.get_slot("fte_payroll_cols_json") or "[]"
    try:
        pay_cols = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pay_cols = []
    if isinstance(pay_cols, list):
        for c in pay_cols:
            s = str(c).strip()
            if s:
                names.add(s)
    return names


def _emit_fte_columns_card(
    dispatcher: CollectingDispatcher,
    tracker: Tracker,
    periods: list[dict[str, Any]] | None = None,
) -> None:
    if periods is None:
        try:
            periods = json.loads(tracker.get_slot("fte_periods_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            periods = []
    preview_id = ""
    preview_sheet = ""
    if periods:
        preview_id = str(periods[0].get("file_id") or tracker.get_slot("file_id") or "")
        preview_sheet = str(periods[0].get("sheet_name") or "").strip()
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "fte_columns",
            "wide": True,
            "title": "Map personnel columns",
            "subtitle": (
                "Click a column for employment rate and annual working time (months). "
                "Then click every payroll column to include in the total and confirm."
            ),
            "mapper_meta": {
                "session_id": _fdd_session_id(tracker),
                "preview_file_id": preview_id,
                "sheet_name": preview_sheet,
                "header_row": 0,
                "fte_periods_json": tracker.get_slot("fte_periods_json") or "[]",
            },
            "inputs": [],
            "submit_label": "Continue",
        }
    )


def _emit_fte_grouping_card(dispatcher: CollectingDispatcher, tracker: Tracker) -> None:
    periods = _fte_periods_from_sources(tracker)
    headers = _headers_union_for_periods(tracker, periods) if periods else []
    if not headers:
        headers = _headers_from_sources(tracker)
    mapped = _fte_mapped_column_names(tracker)
    dim_opts = [{"label": "None", "value": ""}] + [
        {"label": h, "value": h} for h in headers if h and h not in mapped
    ]
    inputs: list[dict[str, Any]] = [
        {
            "id": "fte_group_col_1",
            "type": "dropdown",
            "label": "Grouping — level 1 (required)",
            "options": dim_opts,
            "default": str(tracker.get_slot("fte_group_col_1") or ""),
        },
        {
            "id": "fte_group_col_2",
            "type": "dropdown",
            "label": "Grouping — level 2 (optional)",
            "options": dim_opts,
            "default": str(tracker.get_slot("fte_group_col_2") or ""),
            "required": False,
        },
    ]
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "fte_grouping",
            "title": "FTE Development — Grouping",
            "subtitle": "Choose one or two hierarchy levels for rows in each block.",
            "inputs": inputs,
            "submit_label": "Continue",
        }
    )


def _fte_group_cols_from_sources(tracker: Tracker, payload: dict | None = None) -> list[str]:
    st = _fte_state(tracker)
    cached = st.get("group_cols")
    if isinstance(cached, list) and cached:
        return [str(c).strip() for c in cached if str(c).strip()]
    p = payload or {}
    cols: list[str] = []
    for i in range(1, FTE_MAX_GROUP_LEVELS + 1):
        raw = p.get(f"fte_group_col_{i}")
        if raw is None or raw == "":
            raw = tracker.get_slot(f"fte_group_col_{i}")
        name = str(raw or "").strip()
        if name and name.lower() != "none":
            cols.append(name)
    return cols[:FTE_MAX_GROUP_LEVELS]


class ActionShowFteFiles(Action):
    def name(self) -> str:
        return "action_show_fte_files"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        dispatcher.utter_message(json_message=_fte_files_card(tracker))
        return []


class ActionProcessFteFiles(Action):
    def name(self) -> str:
        return "action_process_fte_files"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        groups = _parse_susa_grid_file_groups(payload)
        periods = _fte_periods_from_grid(groups, tracker)
        if not periods:
            dispatcher.utter_message(text="Please upload at least one personnel file in the grid.")
            dispatcher.utter_message(json_message=_fte_files_card(tracker))
            return []

        first_id = periods[0]["file_id"]
        _fte_state(tracker)["periods"] = periods
        _sync_flow_state_to_disk(tracker)
        union_headers = _headers_union_for_periods(tracker, periods)
        if union_headers:
            _remember_session_headers(tracker, union_headers)
        events: list[Any] = [
            SlotSet("fte_periods_json", json.dumps(periods)),
            SlotSet("file_id", first_id),
        ]
        if union_headers:
            events.append(SlotSet("headers", union_headers))
        _emit_fte_columns_card(dispatcher, tracker, periods)
        return events


class ActionReshowFteColumns(Action):
    def name(self) -> str:
        return "action_reshow_fte_columns"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        _emit_fte_columns_card(dispatcher, tracker)
        return []


class ActionProcessFteColumns(Action):
    def name(self) -> str:
        return "action_process_fte_columns"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        events: list[Any] = []
        for slot in ("fte_employment_col", "fte_months_col"):
            val = str(payload.get(slot) or "").strip()
            if not val:
                dispatcher.utter_message(
                    text="Please map employment rate and annual working time before continuing."
                )
                _emit_fte_columns_card(dispatcher, tracker)
                return []
            events.append(SlotSet(slot, val))

        raw_pay = payload.get("fte_payroll_cols_json", tracker.get_slot("fte_payroll_cols_json") or "[]")
        if isinstance(raw_pay, list):
            pay_cols = [str(c).strip() for c in raw_pay if str(c).strip()]
        else:
            try:
                parsed = json.loads(str(raw_pay or "[]"))
            except (json.JSONDecodeError, TypeError):
                parsed = []
            pay_cols = [str(c).strip() for c in parsed if str(c).strip()] if isinstance(parsed, list) else []

        if not pay_cols:
            dispatcher.utter_message(text="Please select at least one payroll column to sum.")
            _emit_fte_columns_card(dispatcher, tracker)
            return []

        events.append(SlotSet("fte_payroll_cols_json", json.dumps(pay_cols)))
        _fte_state(tracker)["columns"] = {
            "employment": str(payload.get("fte_employment_col") or "").strip(),
            "months_sum": str(payload.get("fte_months_col") or "").strip(),
            "payroll_cols": pay_cols,
        }
        _sync_flow_state_to_disk(tracker)
        _emit_fte_grouping_card(dispatcher, tracker)
        return events


class ActionShowFteGrouping(Action):
    def name(self) -> str:
        return "action_show_fte_grouping"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        _emit_fte_grouping_card(dispatcher, tracker)
        return []


class ActionProcessFteGrouping(Action):
    def name(self) -> str:
        return "action_process_fte_grouping"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        events: list[Any] = []
        for i in range(1, FTE_MAX_GROUP_LEVELS + 1):
            slot = f"fte_group_col_{i}"
            if slot in payload:
                events.append(SlotSet(slot, str(payload.get(slot) or "").strip()))
        group_cols = _fte_group_cols_from_sources(tracker, payload)
        if not group_cols:
            dispatcher.utter_message(text="Please choose at least one grouping column.")
            _emit_fte_grouping_card(dispatcher, tracker)
            return events
        if len(group_cols) >= 2 and group_cols[0] == group_cols[1]:
            dispatcher.utter_message(text="Level 1 and level 2 must be different columns.")
            _emit_fte_grouping_card(dispatcher, tracker)
            return events
        periods = _fte_periods_from_sources(tracker)
        col_err = _validate_group_cols_in_periods(tracker, periods, group_cols)
        if col_err:
            dispatcher.utter_message(text=col_err)
            _emit_fte_grouping_card(dispatcher, tracker)
            return events
        _fte_state(tracker)["group_cols"] = group_cols
        _sync_flow_state_to_disk(tracker)
        dispatcher.utter_message(json_message=_fte_filter_checkpoint_card(tracker))
        return events


def _fte_filter_checkpoint_card(tracker: Tracker) -> dict[str, Any]:
    disp = (tracker.get_slot("fte_filter_rules_display") or "").strip() or "No active filter rules."
    return {
        "type": "adaptive_card",
        "card": "fte_filter_checkpoint",
        "title": "FTE Development — Filter rules",
        "review_text": disp,
        "inputs": [
            {
                "id": "fte_filter_manage_choice",
                "type": "radio",
                "label": "What would you like to do?",
                "options": [
                    {"label": "1. Delete current rules", "value": "delete_all"},
                    {"label": "2. Add a rule", "value": "add_rule"},
                    {"label": "3. Keep rules as they are", "value": "keep"},
                ],
            }
        ],
        "submit_label": "Continue",
    }


class ActionEnterFteFilterCheckpoint(Action):
    def name(self) -> str:
        return "action_enter_fte_filter_checkpoint"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        dispatcher.utter_message(json_message=_fte_filter_checkpoint_card(tracker))
        return []


class ActionProcessFteFilterCheckpoint(Action):
    def name(self) -> str:
        return "action_process_fte_filter_checkpoint"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        choice = str(payload.get("fte_filter_manage_choice", "")).lower()
        if choice == "delete_all":
            return [
                SlotSet("fte_filter_rules_json", "[]"),
                SlotSet("fte_filter_rules_display", ""),
                FollowupAction("action_enter_fte_filter_checkpoint"),
            ]
        if choice == "add_rule":
            return [SlotSet("active_filter_context", "fte"), FollowupAction("action_start_filter")]
        if choice == "keep":
            return _enter_strand_run_target(
                dispatcher, tracker, "fte", pending_slot="fte_pending_rerun"
            )
        return [FollowupAction("action_enter_fte_filter_checkpoint")]


class ActionRunFtePayroll(Action):
    def name(self) -> str:
        return "action_run_fte_payroll"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        s = tracker.slots
        payload = _parse_payload(tracker)

        def pick(key: str, default=None):
            v = payload.get(key, None)
            if v is None or v == "":
                v = s.get(key)
            return default if v in (None, "") else v

        periods_raw = pick("fte_periods_json", s.get("fte_periods_json") or "[]")
        if isinstance(periods_raw, str):
            try:
                periods = json.loads(periods_raw)
            except json.JSONDecodeError:
                periods = []
        else:
            periods = list(periods_raw or [])
        if not periods:
            periods = _fte_periods_from_sources(tracker)
        if not periods:
            dispatcher.utter_message(text="Personnel period files missing — please upload files first.")
            return _inline_action(ActionShowFteFiles(), dispatcher, tracker, domain)

        raw_pay = pick("fte_payroll_cols_json", s.get("fte_payroll_cols_json") or "[]")
        cols_from_state = _fte_columns_from_sources(tracker, payload)
        if isinstance(raw_pay, list):
            pay_cols = [str(c).strip() for c in raw_pay if str(c).strip()]
        else:
            try:
                parsed = json.loads(str(raw_pay or "[]"))
            except (json.JSONDecodeError, TypeError):
                parsed = []
            pay_cols = [str(c).strip() for c in parsed if str(c).strip()] if isinstance(parsed, list) else []
        if not pay_cols:
            pay_cols = cols_from_state.get("payroll_cols") or []
        if not pay_cols:
            dispatcher.utter_message(text="Payroll columns missing — please map columns first.")
            return _inline_action(ActionReshowFteColumns(), dispatcher, tracker, domain)

        columns = {
            "employment": cols_from_state.get("employment") or pick("fte_employment_col", s.get("fte_employment_col")),
            "months_sum": cols_from_state.get("months_sum") or pick("fte_months_col", s.get("fte_months_col")),
            "payroll_cols": pay_cols,
        }
        group_cols = _fte_group_cols_from_sources(tracker, payload)
        if not group_cols:
            dispatcher.utter_message(text="Grouping columns missing — please choose grouping first.")
            return _inline_action(ActionShowFteGrouping(), dispatcher, tracker, domain)
        fy_m, fy_d = _fy_end_month_day_from_tracker({}, tracker)
        workbook_fields = _strand_workbook_fields(tracker, suffix="FTE")
        fte_cfg: dict[str, Any] = {
            "title": pick("project_name", s.get("project_name")),
            "project_name": pick("project_name", s.get("project_name")),
            "company": pick("group_name", s.get("group_name")),
            "first_fy": _tracker_first_fy_int(tracker),
            "fy_end_month": fy_m,
            "fy_end_day": fy_d,
            "periods": periods,
            "header_row": 0,
            "columns": columns,
            "group_cols": group_cols,
            "filters": _fdd_filters_payload(pick("fte_filter_rules_json", s.get("fte_filter_rules_json"))),
            "unit_label": "kEUR",
            "amount_scale": 1000,
            "output_file_path": s.get("output_folder", ""),
            "sheet_name": "FTE Development",
            **workbook_fields,
        }
        if not workbook_fields.get("use_separate_workbook"):
            master = (
                s.get("db_master_workbook_path")
                or _session_output_workbook_path(tracker)
                or ""
            )
            if master:
                fte_cfg["output_workbook_path"] = master
                fte_cfg["db_master_workbook_path"] = master
                fte_cfg["master_workbook_path"] = master
                fte_cfg["master_pl_path"] = master
        body = {
            "session_id": _fdd_session_id(tracker),
            "file_id": periods[0].get("file_id") or tracker.get_slot("file_id"),
            "config": fte_cfg,
        }
        _start_async_revenue_script(dispatcher, body, "fte_payroll", "FTE Development")
        return [SlotSet("output_type", "fte_development")]

# ─── OPOS (Creditor / Debitor Aging) ───────────────────────────────────────────

OPOS_SORT_BUCKETS: list[tuple[str, str]] = [
    ("not_yet_due", "Not yet due"),
    ("overdue_1_30", "1-30 days"),
    ("overdue_31_60", "31-60 days"),
    ("overdue_61_90", "61-90 days"),
    ("overdue_91_180", "91-180 days"),
    ("overdue_over_180", ">180 days"),
]


def _session_output_workbook_path(tracker: Tracker) -> str:
    """Prefer databook master workbook so strand outputs append to the same file."""
    return _abs_master_path_from_tracker(tracker) or ""


def _strand_separate_workbook_enabled(raw: Any, *, default: bool = False) -> bool:
    if raw in (None, ""):
        return default
    if isinstance(raw, bool):
        return raw
    token = str(raw).strip().lower()
    if token in ("on", "yes", "true", "1"):
        return True
    if token in ("off", "no", "false", "0"):
        return False
    return default


def _strand_workbook_fields(tracker: Tracker, *, suffix: str) -> dict[str, Any]:
    separate = _strand_separate_workbook_enabled(tracker.get_slot("strand_separate_workbook"))
    base = {
        "case_id": _fdd_session_id(tracker),
        "title": str(tracker.get_slot("project_name") or "").strip(),
        "project_name": str(tracker.get_slot("project_name") or "").strip(),
    }
    if separate:
        return {
            **base,
            "use_separate_workbook": True,
            "separate_workbook_suffix": suffix,
        }
    master = _session_output_workbook_path(tracker)
    fields: dict[str, Any] = {**base, "use_separate_workbook": False}
    if master:
        fields["output_workbook_path"] = master
        fields["db_master_workbook_path"] = master
        fields["master_workbook_path"] = master
    return fields


def _opos_top_bucket_enabled(raw: Any, *, default: bool = True) -> bool:
    if raw in (None, ""):
        return default
    if isinstance(raw, bool):
        return raw
    token = str(raw).strip().lower()
    if token in ("on", "yes", "true", "1"):
        return True
    if token in ("off", "no", "false", "0"):
        return False
    return default


def _default_opos_display_bucket_keys() -> list[str]:
    return [k for k, _ in OPOS_SORT_BUCKETS]


def _default_opos_sort_keys() -> list[str]:
    return [k for k, _ in OPOS_SORT_BUCKETS if k != "not_yet_due"]


def _opos_display_buckets_card(tracker: Tracker) -> dict[str, Any]:
    snapshots = _opos_snapshots_from_sources(tracker)
    multiple = len(snapshots) > 1
    defaults_raw = tracker.get_slot("opos_display_bucket_keys_json") or json.dumps(
        _default_opos_display_bucket_keys()
    )
    defaults = _parse_list(defaults_raw, _default_opos_display_bucket_keys())
    return {
        "type": "adaptive_card",
        "card": "opos_display_buckets",
        "title": "Choose buckets to display",
        "subtitle": (
            "Select which aging buckets appear in the report. "
            "Deselected buckets are omitted from the output entirely."
        ),
        "bucket_options": [{"label": lbl, "value": key} for key, lbl in OPOS_SORT_BUCKETS],
        "bucket_defaults": defaults,
        "multiple_snapshots": multiple,
        "sort_basis_default": tracker.get_slot("opos_sort_basis") or "most_recent",
        "submit_label": "Continue",
    }


def _opos_sort_card(tracker: Tracker) -> dict[str, Any]:
    defaults = tracker.get_slot("opos_sort_bucket_keys_json") or json.dumps(_default_opos_sort_keys())
    sort_metric = tracker.get_slot("opos_sort_metric") or "total"
    return {
        "type": "adaptive_card",
        "card": "opos_sort",
        "title": "Sort partners by period buckets",
        "subtitle": (
            "Choose which overdue buckets count toward partner ranking. "
            "Select Total to rank by the full open balance across all buckets."
        ),
        "inputs": [
            {
                "id": "opos_sort_basis",
                "type": "radio",
                "label": "Ranking period basis",
                "default": tracker.get_slot("opos_sort_basis") or "most_recent",
                "options": [
                    {"label": "Most recent snapshot only", "value": "most_recent"},
                    {"label": "All snapshots combined", "value": "all_dates"},
                ],
            },
            {
                "id": "opos_sort_metric",
                "type": "radio",
                "label": "Ranking metric",
                "default": sort_metric,
                "options": [
                    {"label": "Total (all buckets)", "value": "total"},
                    {"label": "Selected buckets only", "value": "buckets"},
                ],
            },
            {
                "id": "opos_sort_bucket_keys",
                "type": "multi_select",
                "label": "Buckets included in sort total",
                "default": _parse_list(defaults, _default_opos_sort_keys()),
                "options": [{"label": lbl, "value": key} for key, lbl in OPOS_SORT_BUCKETS],
                "showWhen": {"field": "opos_sort_metric", "value": "buckets"},
            },
        ],
        "submit_label": "Continue",
    }


def _opos_other_bucket_choice(tracker: Tracker, payload: dict | None = None) -> str:
    """Return 'include' or 'disable' for the OPOS Other bucket (card + run)."""
    if payload is not None:
        raw = payload.get("opos_top_other_bucket")
        if isinstance(raw, list):
            if "include" in [str(x).strip().lower() for x in raw]:
                return "include"
            if "disable" in [str(x).strip().lower() for x in raw]:
                return "disable"
        if raw in ("include", "disable"):
            return str(raw)
    st = _opos_state(tracker)
    if st.get("top_other_bucket") is True:
        return "include"
    if st.get("top_other_bucket") is False:
        return "disable"
    raw_slot = tracker.get_slot("opos_top_other_bucket")
    if raw_slot in ("include", "disable"):
        return str(raw_slot)
    if str(raw_slot or "").strip().lower() == "include":
        return "include"
    top_on = _opos_top_bucket_enabled(tracker.get_slot("opos_top_bucket_enabled"), default=True)
    return "include" if top_on else "disable"


def _opos_top_other_default(tracker: Tracker) -> str:
    return _opos_other_bucket_choice(tracker)


def _opos_top_bucket_card(tracker: Tracker) -> dict[str, Any]:
    return {
        "type": "adaptive_card",
        "card": "opos_top_bucket",
        "title": "Top bucket sorting",
        "subtitle": (
            "Group partners into Top-N buckets (default 10, 20, 50). "
            "You can enter any list of bucket sizes, e.g. [10, 25, 100]."
        ),
        "inputs": [
            {
                "id": "opos_top_bucket_enabled",
                "type": "radio",
                "label": "Enable top buckets",
                "default": tracker.get_slot("opos_top_bucket_enabled") or "on",
                "options": [
                    {"label": "Yes", "value": "on"},
                    {"label": "No", "value": "off"},
                ],
            },
            {
                "id": "opos_top_bucket_numbers",
                "type": "text",
                "label": "Partners per bucket — e.g. [10, 20, 50]",
                "default": tracker.get_slot("opos_top_bucket_numbers") or "[10, 20, 50]",
                "showWhen": {"field": "opos_top_bucket_enabled", "value": "on"},
            },
            {
                "id": "opos_top_other_bucket",
                "type": "radio",
                "label": "'Other' bucket",
                "default": _opos_top_other_default(tracker),
                "options": [
                    {"label": "Include", "value": "include"},
                    {"label": "Disable", "value": "disable"},
                ],
                "showWhen": {"field": "opos_top_bucket_enabled", "value": "on"},
            },
        ],
        "submit_label": "Continue",
    }


def _opos_snapshots_card(tracker: Tracker) -> dict[str, Any]:
    return {
        "type": "adaptive_card",
        "card": "opos_snapshots",
        "title": "OPOS snapshots",
        "subtitle": (
            "Each row is one snapshot date with a Debitor file and a Kreditor file. "
            "Use Add another date for additional snapshot dates."
        ),
        "inputs": [],
        "submit_label": "Continue",
    }


def _emit_opos_snapshots_card(dispatcher: CollectingDispatcher, tracker: Tracker) -> None:
    dispatcher.utter_message(json_message=_opos_snapshots_card(tracker))


def _parse_opos_snapshots_payload(payload: dict) -> list[dict[str, Any]]:
    raw = payload.get("opos_snapshots")
    if not isinstance(raw, list) or not raw:
        raise ValueError("opos_snapshots must be a non-empty list")
    out: list[dict[str, Any]] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        as_of = str(it.get("as_of") or "").strip()
        if not as_of:
            raise ValueError("Each snapshot needs as_of")
        deb = it.get("debitor")
        kred = it.get("kreditor")
        if isinstance(deb, dict) and isinstance(kred, dict):
            deb_id = str(deb.get("file_id") or "").strip()
            kred_id = str(kred.get("file_id") or "").strip()
            if not deb_id or not kred_id:
                raise ValueError("Each snapshot needs debitor and kreditor file_id")
            out.append(
                {
                    "as_of": as_of,
                    "debitor": {
                        "file_id": deb_id,
                        "file_path": str(deb.get("file_path") or "").strip(),
                        "sheet_name": str(deb.get("sheet_name") or "").strip(),
                    },
                    "kreditor": {
                        "file_id": kred_id,
                        "file_path": str(kred.get("file_path") or "").strip(),
                        "sheet_name": str(kred.get("sheet_name") or "").strip(),
                    },
                }
            )
            continue
        file_id = str(it.get("file_id") or "").strip()
        if not file_id:
            raise ValueError("Each snapshot needs debitor/kreditor or legacy file_id")
        out.append(
            {
                "as_of": as_of,
                "file_id": file_id,
                "file_path": str(it.get("file_path") or "").strip(),
                "sheet_name": str(it.get("sheet_name") or "").strip(),
            }
        )
    if not out:
        raise ValueError("No valid OPOS snapshots in payload")
    return out


def _emit_opos_columns_card(
    dispatcher: CollectingDispatcher, tracker: Tracker, snapshots: list[dict[str, Any]] | None = None
) -> None:
    snaps = snapshots
    if snaps is None:
        snaps = _opos_snapshots_from_sources(tracker)
    preview_id = ""
    preview_sheet = ""
    if snaps:
        first = snaps[0]
        if isinstance(first.get("debitor"), dict):
            preview_id = str(first["debitor"].get("file_id") or "")
            preview_sheet = str(first["debitor"].get("sheet_name") or "")
        else:
            preview_id = str(first.get("file_id") or tracker.get_slot("file_id") or "")
            preview_sheet = str(first.get("sheet_name") or tracker.get_slot("sheet_name") or "")
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "opos_columns",
            "wide": True,
            "title": "Map OPOS columns",
            "subtitle": (
                "Map columns on the debitor file (partner, amount, due date). "
                "The same column positions are applied to kreditor files with their own header names."
            ),
            "mapper_meta": {
                "session_id": _fdd_session_id(tracker),
                "preview_file_id": preview_id,
                "sheet_name": preview_sheet,
                "header_row": 0,
                "snapshots_json": json.dumps(snaps or [], ensure_ascii=False),
            },
            "inputs": [],
            "submit_label": "Continue",
        }
    )


STRAND_RUN_META: dict[str, tuple[str, str, str]] = {
    "opos": ("Creditor / Debitor Aging", "Aging", "action_run_opos"),
    "fa": ("Fixed Assets Rollforward", "FA_Rollf", "action_run_fa_rollf"),
    "fte": ("FTE Development", "FTE", "action_run_fte_payroll"),
}


def _strand_run_target_card(tracker: Tracker, script_key: str | None = None) -> dict[str, Any]:
    key = str(script_key or tracker.get_slot("strand_run_script") or "").strip().lower()
    if key not in STRAND_RUN_META:
        key = "opos"
    title_label, _, _ = STRAND_RUN_META[key]
    separate_default = _strand_separate_workbook_enabled(
        tracker.get_slot("strand_separate_workbook"), default=False
    )
    return {
        "type": "adaptive_card",
        "card": "strand_run_target",
        "title": f"{title_label} — Output destination",
        "subtitle": (
            "Output will be written to the session master workbook by default "
            "(for reconciliation checks)."
        ),
        "inputs": [
            {
                "id": "strand_separate_workbook",
                "type": "checkbox",
                "label": "Write to a separate file instead",
                "default": "on" if separate_default else "off",
                "required": False,
            },
            {
                "id": "strand_run_script",
                "type": "text",
                "hidden": True,
                "default": key,
            },
        ],
        "submit_label": "Run",
    }


class ActionEnterStrandRunTarget(Action):
    def name(self) -> str:
        return "action_enter_strand_run_target"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        dispatcher.utter_message(json_message=_strand_run_target_card(tracker))
        return []


class ActionProcessStrandRunTarget(Action):
    def name(self) -> str:
        return "action_process_strand_run_target"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        script_key = str(
            payload.get("strand_run_script") or tracker.get_slot("strand_run_script") or ""
        ).strip().lower()
        separate = _strand_separate_workbook_enabled(payload.get("strand_separate_workbook"))
        meta = STRAND_RUN_META.get(script_key)
        if not meta:
            dispatcher.utter_message(text="Unknown output type — please try again.")
            return _enter_strand_run_target(dispatcher, tracker, "opos")
        _, _, run_action_name = meta
        run_map = {
            "action_run_opos": ActionRunOpos(),
            "action_run_fa_rollf": ActionRunFaRollf(),
            "action_run_fte_payroll": ActionRunFtePayroll(),
        }
        run_action = run_map.get(run_action_name)
        if run_action is None:
            dispatcher.utter_message(text="Run action missing — please try again.")
            return []
        events: list[Any] = [
            SlotSet("strand_run_script", script_key),
            SlotSet("strand_separate_workbook", "on" if separate else "off"),
        ]
        events.extend(_inline_action(run_action, dispatcher, tracker, domain))
        return events


def _opos_filter_checkpoint_card(tracker: Tracker) -> dict[str, Any]:
    disp = (tracker.get_slot("opos_filter_rules_display") or "").strip() or "No active filter rules."
    return {
        "type": "adaptive_card",
        "card": "opos_filter_checkpoint",
        "title": "OPOS — Filter rules",
        "review_text": disp,
        "inputs": [
            {
                "id": "opos_filter_manage_choice",
                "type": "radio",
                "label": "What would you like to do?",
                "options": [
                    {"label": "1. Delete current rules", "value": "delete_all"},
                    {"label": "2. Add a rule", "value": "add_rule"},
                    {"label": "3. Keep rules as they are", "value": "keep"},
                ],
            }
        ],
        "submit_label": "Continue",
    }


class ActionShowOposSnapshots(Action):
    def name(self) -> str:
        return "action_show_opos_snapshots"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        _emit_opos_snapshots_card(dispatcher, tracker)
        return []


class ActionProcessOposSnapshots(Action):
    def name(self) -> str:
        return "action_process_opos_snapshots"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        try:
            snapshots = _parse_opos_snapshots_payload(payload)
        except ValueError as exc:
            dispatcher.utter_message(text=str(exc))
            _emit_opos_snapshots_card(dispatcher, tracker)
            return []
        _opos_state(tracker)["snapshots"] = snapshots
        _sync_flow_state_to_disk(tracker)
        _emit_opos_columns_card(dispatcher, tracker, snapshots)
        first = snapshots[0]
        if isinstance(first.get("debitor"), dict):
            file_id = first["debitor"].get("file_id")
            sheet_name = str(first["debitor"].get("sheet_name") or "").strip()
        else:
            file_id = first.get("file_id")
            sheet_name = str(first.get("sheet_name") or "").strip()
        events: list[Any] = [SlotSet("opos_snapshots_json", json.dumps(snapshots, ensure_ascii=False))]
        if file_id:
            events.append(SlotSet("file_id", file_id))
        if sheet_name:
            events.append(SlotSet("sheet_name", sheet_name))
        return events


class ActionReshowOposColumns(Action):
    def name(self) -> str:
        return "action_reshow_opos_columns"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        _emit_opos_columns_card(dispatcher, tracker)
        return []


class ActionProcessOposColumns(Action):
    def name(self) -> str:
        return "action_process_opos_columns"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        snapshots = _opos_snapshots_from_sources(tracker)
        if not snapshots:
            dispatcher.utter_message(
                text="OPOS snapshot file(s) missing — please add a snapshot date and upload the file(s) first."
            )
            _emit_opos_snapshots_card(dispatcher, tracker)
            return []
        pid = str(
            payload.get("opos_partner_col")
            or payload.get("opos_partner_id_col")
            or ""
        ).strip()
        pname = str(payload.get("opos_partner_name_col") or "").strip() or pid
        amount = str(payload.get("opos_amount_col") or "").strip()
        due = str(payload.get("opos_due_date_col") or "").strip()
        letters_raw = payload.get("opos_column_letters_json")
        if isinstance(letters_raw, str) and letters_raw.strip():
            try:
                column_letters = json.loads(letters_raw)
            except json.JSONDecodeError:
                column_letters = {}
        elif isinstance(letters_raw, dict):
            column_letters = letters_raw
        else:
            column_letters = {}
        if not all([pid, amount, due]) or not column_letters:
            dispatcher.utter_message(text="Please map partner, amount, and due date columns.")
            _emit_opos_columns_card(dispatcher, tracker)
            return []
        events: list[Any] = [
            SlotSet("opos_partner_id_col", pid),
            SlotSet("opos_partner_name_col", pname),
            SlotSet("opos_amount_col", amount),
            SlotSet("opos_due_date_col", due),
            SlotSet("opos_column_letters_json", json.dumps(column_letters)),
        ]
        events.append(SlotSet("opos_sort_basis", "most_recent"))
        events.append(SlotSet("opos_sort_metric", "buckets"))
        events.append(
            SlotSet(
                "opos_display_bucket_keys_json",
                json.dumps(_default_opos_display_bucket_keys()),
            )
        )
        events.append(
            SlotSet(
                "opos_sort_bucket_keys_json",
                json.dumps(_default_opos_display_bucket_keys()),
            )
        )
        events.append(SlotSet("opos_top_bucket_enabled", "on"))
        events.append(SlotSet("opos_top_bucket_numbers", "[10, 20, 50]"))
        events.append(SlotSet("opos_top_other_bucket", "include"))
        _opos_state(tracker)["columns"] = {
            "partner_id": pid,
            "partner_name": pname,
            "amount": amount,
            "due_date": due,
        }
        _opos_state(tracker)["column_letters"] = column_letters
        if not _opos_state(tracker).get("snapshots"):
            _opos_state(tracker)["snapshots"] = snapshots
        _sync_flow_state_to_disk(tracker)
        dispatcher.utter_message(json_message=_opos_display_buckets_card(tracker))
        return events


class ActionProcessOposMissingDueDate(Action):
    def name(self) -> str:
        return "action_process_opos_missing_due_date"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        choice = str(payload.get("opos_missing_due_choice") or "continue").strip().lower()
        if choice == "reupload":
            return ActionShowOposSnapshots().run(dispatcher, tracker, domain)
        dispatcher.utter_message(json_message=_opos_display_buckets_card(tracker))
        return []


class ActionProcessOposDisplayBuckets(Action):
    def name(self) -> str:
        return "action_process_opos_display_buckets"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        raw_keys = payload.get("opos_display_bucket_keys")
        if isinstance(raw_keys, list):
            keys = [str(k).strip() for k in raw_keys if str(k).strip()]
        else:
            keys = _parse_list(str(raw_keys or "[]"), _default_opos_display_bucket_keys())
        valid = {k for k, _ in OPOS_SORT_BUCKETS}
        keys = [k for k in keys if k in valid]
        if not keys:
            dispatcher.utter_message(text="Please select at least one bucket to display.")
            dispatcher.utter_message(json_message=_opos_display_buckets_card(tracker))
            return []

        basis = str(payload.get("opos_sort_basis") or tracker.get_slot("opos_sort_basis") or "most_recent").strip().lower()
        if basis in ("latest_fy", "most_recent"):
            basis = "most_recent"
        elif basis in ("all_fys", "all_dates"):
            basis = "all_dates"
        else:
            basis = "most_recent"

        events: list[Any] = [
            SlotSet("opos_display_bucket_keys_json", json.dumps(keys)),
            SlotSet("opos_sort_basis", basis),
            SlotSet("opos_sort_metric", "buckets"),
            SlotSet("opos_sort_bucket_keys_json", json.dumps(keys)),
        ]
        _opos_state(tracker)["display_bucket_keys"] = keys
        _sync_flow_state_to_disk(tracker)

        pending = str(tracker.get_slot("opos_pending_rerun") or "").strip().lower()
        if pending == "buckets":
            events.append(SlotSet("opos_pending_rerun", ""))
            events.extend(_enter_strand_run_target(dispatcher, tracker, "opos"))
            return events

        dispatcher.utter_message(json_message=_opos_top_bucket_card(tracker))
        return events


class ActionProcessOposSort(Action):
    def name(self) -> str:
        return "action_process_opos_sort"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        basis = str(payload.get("opos_sort_basis") or "most_recent").strip().lower()
        if basis in ("latest_fy", "most_recent"):
            basis = "most_recent"
        elif basis in ("all_fys", "all_dates"):
            basis = "all_dates"
        else:
            basis = "most_recent"
        keys = payload.get("opos_sort_bucket_keys")
        if not isinstance(keys, list):
            keys = _parse_list(str(keys or "[]"), _default_opos_sort_keys())
        sort_metric = str(payload.get("opos_sort_metric") or "total").strip().lower()
        if sort_metric not in ("total", "buckets"):
            sort_metric = "total"
        dispatcher.utter_message(json_message=_opos_top_bucket_card(tracker))
        return [
            SlotSet("opos_sort_basis", basis),
            SlotSet("opos_sort_metric", sort_metric),
            SlotSet("opos_sort_bucket_keys_json", json.dumps(keys)),
        ]


class ActionProcessOposBuckets(Action):
    def name(self) -> str:
        return "action_process_opos_buckets"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        enabled = str(payload.get("opos_top_bucket_enabled", "on")).lower() == "on"
        numbers = _parse_list(payload.get("opos_top_bucket_numbers"), [10, 20, 50])
        other_choice = _opos_other_bucket_choice(tracker, payload)
        other_on = other_choice == "include"
        dispatcher.utter_message(json_message=_opos_filter_checkpoint_card(tracker))
        _opos_state(tracker)["top_bucket_numbers"] = numbers
        _opos_state(tracker)["top_bucket_enabled"] = enabled
        _opos_state(tracker)["top_other_bucket"] = other_on
        _sync_flow_state_to_disk(tracker)
        return [
            SlotSet("opos_top_bucket_enabled", "on" if enabled else "off"),
            SlotSet("opos_top_bucket_numbers", json.dumps(numbers)),
            SlotSet("opos_top_other_bucket", "include" if other_on else "disable"),
        ]


class ActionEnterOposFilterCheckpoint(Action):
    def name(self) -> str:
        return "action_enter_opos_filter_checkpoint"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        dispatcher.utter_message(json_message=_opos_filter_checkpoint_card(tracker))
        return []


class ActionProcessOposFilterCheckpoint(Action):
    def name(self) -> str:
        return "action_process_opos_filter_checkpoint"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        choice = str(payload.get("opos_filter_manage_choice", "")).lower()
        if choice == "delete_all":
            return [
                SlotSet("opos_filter_rules_json", "[]"),
                SlotSet("opos_filter_rules_display", ""),
                FollowupAction("action_enter_opos_filter_checkpoint"),
            ]
        if choice == "add_rule":
            return [SlotSet("active_filter_context", "opos"), FollowupAction("action_start_filter")]
        if choice == "keep":
            return _enter_strand_run_target(
                dispatcher, tracker, "opos", pending_slot="opos_pending_rerun"
            )
        return [FollowupAction("action_enter_opos_filter_checkpoint")]


class ActionShowOposReview(Action):
    def name(self) -> str:
        return "action_show_opos_review"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "opos_proceed",
                "compact": True,
                "title": "Review — Creditor / Debitor Aging",
                "subtitle": f"{tracker.get_slot('project_name') or '—'} · {tracker.get_slot('group_name') or '—'}",
                "inputs": [],
                "submit_label": "Create output",
            }
        )
        return []


class ActionRunOpos(Action):
    def name(self) -> str:
        return "action_run_opos"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        s = tracker.slots

        def pick(key: str, default=None):
            v = payload.get(key, None)
            return default if v is None or v == "" else v

        snapshots = _opos_snapshots_from_sources(tracker)
        if not snapshots:
            dispatcher.utter_message(text="OPOS snapshots missing — please add a snapshot date and file(s) first.")
            return _inline_action(ActionShowOposSnapshots(), dispatcher, tracker, domain)

        cols = _opos_columns_from_sources(tracker, payload)
        letters_raw = pick("opos_column_letters_json", s.get("opos_column_letters_json"))
        if isinstance(letters_raw, str):
            try:
                column_letters = json.loads(letters_raw)
            except json.JSONDecodeError:
                column_letters = {}
        elif isinstance(letters_raw, dict):
            column_letters = letters_raw
        else:
            column_letters = dict(_opos_state(tracker).get("column_letters") or {})

        cached_display = _opos_state(tracker).get("display_bucket_keys")
        if isinstance(cached_display, list) and cached_display:
            display_keys = [str(k).strip() for k in cached_display if str(k).strip()]
        else:
            display_raw = pick("opos_display_bucket_keys_json", s.get("opos_display_bucket_keys_json"))
            if isinstance(display_raw, list):
                display_keys = [str(k).strip() for k in display_raw if str(k).strip()]
            else:
                display_keys = _parse_list(str(display_raw or "[]"), _default_opos_display_bucket_keys())
        valid = {k for k, _ in OPOS_SORT_BUCKETS}
        display_keys = [k for k in display_keys if k in valid]
        if not display_keys:
            display_keys = _default_opos_display_bucket_keys()

        sort_metric = "buckets"
        sort_keys_list = list(display_keys)
        top_enabled = _opos_top_bucket_enabled(
            pick(
                "opos_top_bucket_enabled",
                _opos_state(tracker).get("top_bucket_enabled")
                if _opos_state(tracker).get("top_bucket_enabled") is not None
                else s.get("opos_top_bucket_enabled"),
            ),
            default=True,
        )
        top_numbers = _parse_list(
            pick(
                "opos_top_bucket_numbers",
                _opos_state(tracker).get("top_bucket_numbers") or s.get("opos_top_bucket_numbers"),
            ),
            [10, 20, 50],
        )
        top_other = _opos_other_bucket_choice(tracker, payload) == "include"
        sort_basis = str(pick("opos_sort_basis", s.get("opos_sort_basis") or "most_recent")).lower()
        if sort_basis in ("latest_fy", "most_recent"):
            sort_basis = "most_recent"
        elif sort_basis in ("all_fys", "all_dates"):
            sort_basis = "all_dates"
        else:
            sort_basis = "most_recent"

        shared_cfg = {
            "title": tracker.get_slot("project_name") or "",
            "project_name": tracker.get_slot("project_name") or "",
            "company": tracker.get_slot("group_name") or "",
            "column_letters": column_letters,
            "columns": {
                "partner_id": cols.get("partner_id") or "",
                "partner_name": cols.get("partner_name") or "",
                "amount": cols.get("amount") or "",
                "due_date": cols.get("due_date") or "",
            },
            "sort": {
                "basis": sort_basis,
                "metric": sort_metric,
                "bucket_keys": sort_keys_list,
            },
            "display_bucket_keys": display_keys,
            "top_bucket": {
                "enabled": top_enabled,
                "numbers": top_numbers,
                "create_other_bucket": top_other,
                "other_bucket_label": "Other",
            },
            "formula_mode": True,
            "sheet_name": tracker.get_slot("sheet_name") or "",
            "output_file_path": tracker.get_slot("output_folder") or "",
            "filters": _fdd_filters_payload(tracker.get_slot("opos_filter_rules_json") or "[]"),
            **_strand_workbook_fields(tracker, suffix="Aging"),
        }

        if _opos_is_dual_snapshot_format(snapshots):
            body = {
                "session_id": _fdd_session_id(tracker),
                "file_id": snapshots[0]["debitor"].get("file_id") or tracker.get_slot("file_id"),
                "config": {
                    **shared_cfg,
                    "sides": {
                        "debitor": {"snapshots": _opos_side_snapshots_from_stored(snapshots, "debitor")},
                        "kreditor": {"snapshots": _opos_side_snapshots_from_stored(snapshots, "kreditor")},
                    },
                },
            }
        else:
            body = {
                "session_id": _fdd_session_id(tracker),
                "file_id": (snapshots[0] or {}).get("file_id") or tracker.get_slot("file_id"),
                "config": {
                    **shared_cfg,
                    "side": _opos_state(tracker).get("side") or tracker.get_slot("opos_side") or "debitor",
                    "snapshots": snapshots,
                },
            }

        _start_async_revenue_script(dispatcher, body, "opos", "Creditor / Debitor Aging")
        return [SlotSet("output_type", "creditor_debitor_aging")]


class ActionProcessOposPostOutput(Action):
    def name(self) -> str:
        return "action_process_opos_post_output"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        choice = str(payload.get("opos_post_output_choice") or "").strip().lower()

        if choice == "another_output":
            dispatcher.utter_message(json_message=_build_databook_select_card())
            return []

        if choice == "change_periods":
            dispatcher.utter_message(json_message=_opos_display_buckets_card(tracker))
            return [SlotSet("opos_pending_rerun", "buckets")]

        if choice == "change_filters":
            dispatcher.utter_message(json_message=_opos_filter_checkpoint_card(tracker))
            return [SlotSet("opos_pending_rerun", "filters")]

        return []


def _build_databook_select_card() -> dict[str, Any]:
    return {
        "type": "adaptive_card",
        "card": "build_databook",
        "title": "Select Output",
        "subtitle": "What would you like to build?",
        "inputs": [
            {
                "id": "output_type",
                "type": "radio",
                "label": "Choose output",
                "options": [
                    {"label": "Databook", "value": "databook"},
                    {"label": "Revenue Databook", "value": "revenue_databook"},
                    {"label": "Creditor / Debitor Aging", "value": "creditor_debitor_aging"},
                    {"label": "Fixed Assets Rollforward", "value": "fixed_assets_rollforward"},
                    {"label": "FTE Development", "value": "fte_development"},
                ],
            }
        ],
        "submit_label": "Continue",
    }


class ActionProcessStrandPostOutput(Action):
    def name(self) -> str:
        return "action_process_strand_post_output"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        choice = str(payload.get("strand_post_output_choice") or "").strip().lower()
        script_key = str(payload.get("script_key") or tracker.get_slot("output_type") or "").strip().lower()

        if choice == "another_output":
            dispatcher.utter_message(json_message=_build_databook_select_card())
            return []

        if choice == "change_filters":
            if script_key in ("fixed_assets_rollforward", "fixed_assets_rollf"):
                dispatcher.utter_message(json_message=_fa_filter_checkpoint_card(tracker))
                return [SlotSet("fa_pending_rerun", "filters")]
            if script_key in ("fte_development", "fte_payroll"):
                dispatcher.utter_message(json_message=_fte_filter_checkpoint_card(tracker))
                return [SlotSet("fte_pending_rerun", "filters")]

        return []


# ─── Apply Filters ────────────────────────────────────────────────────────────


class ActionStartFilter(Action):
    def name(self) -> str:
        return "action_start_filter"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        headers = _headers_from_sources(tracker)
        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "filter_rule",
                "title": "Add Filter Rule",
                "inputs": [
                    {"id": "filter_col", "type": "dropdown",
                     "label": "Which column would you like to filter?",
                     "options": [{"label": h, "value": h} for h in headers]},
                    {"id": "filter_op", "type": "dropdown",
                     "label": "Which condition shall be applied?",
                     "options": FILTER_OPERATORS},
                    {"id": "filter_value", "type": "text",
                     "label": "Filter value (comma-separated for 'is in' / 'is not in'; leave blank for blank checks)",
                     "placeholder": "e.g. Sales Invoice, Credit note",
                     "required": False},
                ],
                "submit_label": "Add Filter",
            }
        )
        ctx = _resolve_filter_context(tracker)
        return [SlotSet("active_filter_context", ctx)]


class ActionProcessFilterRule(Action):
    def name(self) -> str:
        return "action_process_filter_rule"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)

        if "add_another" in payload and payload.get("add_another") not in (None, ""):
            yn = str(payload.get("add_another")).lower()
            if yn in ("yes", "true", "1"):
                return [FollowupAction("action_start_filter")]
            return [FollowupAction("action_finish_filters")]

        context = _resolve_filter_context(tracker)

        col = payload.get("filter_col", "")
        op = payload.get("filter_op", "eq")
        raw_val = payload.get("filter_value", "")

        def _humanize(rule: dict) -> str:
            # Reuse the same wording as _build_filter_display (without bullet).
            tmp = _build_filter_display([rule]).strip()
            return tmp[2:] if tmp.startswith("• ") else tmp

        # Build the filter rule dict
        if op in ("in", "not_in"):
            values = [v.strip() for v in raw_val.split(",") if v.strip()]
            rule = {"col": col, "op": op, "values": values}
            display = _humanize(rule)
        elif op in ("isblank", "notblank"):
            rule = {"col": col, "op": op}
            display = _humanize(rule)
        elif op == "between":
            parts = [v.strip() for v in raw_val.split(",")]
            lo = parts[0] if parts else ""
            hi = parts[1] if len(parts) > 1 else ""
            rule = {"col": col, "op": op, "lo": lo, "hi": hi}
            display = _humanize(rule)
        else:
            rule = {"col": col, "op": op, "value": raw_val}
            display = _humanize(rule)

        # Append to existing rules for the active context
        slot_json = f"{context}_filter_rules_json"
        slot_disp = f"{context}_filter_rules_display"
        existing_json = tracker.get_slot(slot_json) or "[]"
        existing_display = tracker.get_slot(slot_disp) or ""

        try:
            rules = json.loads(existing_json)
        except Exception:
            rules = []
        rules.append(rule)

        new_json = json.dumps(rules)
        new_display = (existing_display + "\n" + f"• {display}").strip()

        dispatcher.utter_message(
            json_message={
                "type": "adaptive_card",
                "card": "filter_rule",
                "title": "Filter Added",
                "subtitle": display,
                "inputs": [
                    {
                        "id": "add_another",
                        "type": "radio",
                        "label": "Would you like to add another filter?",
                        "options": [
                            {"label": "Yes, add another filter", "value": "yes"},
                            {"label": "No, I'm done with filters", "value": "no"},
                        ],
                    }
                ],
                "submit_label": "Continue",
                "card_on_yes": "filter_rule",
                "card_on_no": "filter_done",
            }
        )
        return [
            SlotSet(slot_json, new_json),
            SlotSet(slot_disp, new_display),
        ]


class ActionFinishFilters(Action):
    def name(self) -> str:
        return "action_finish_filters"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        context = _resolve_filter_context(tracker)

        if context == "churn":
            dispatcher.utter_message(json_message=_churn_filter_checkpoint_card(tracker))
            return []

        follow_map = {
            "gst": "action_enter_gst_filter_checkpoint",
            "pvm": "action_enter_pvm_filter_checkpoint",
            "top": "action_enter_top_filter_checkpoint",
            "bs": "action_enter_bs_filter_checkpoint",
            "churn": "action_enter_churn_filter_checkpoint",
            "fa": "action_enter_fa_filter_checkpoint",
            "opos": "action_enter_opos_filter_checkpoint",
            "fte": "action_enter_fte_filter_checkpoint",
        }
        next_action = follow_map.get(context, "utter_default")
        return [FollowupAction(next_action)]


# ─── Post-analysis next action ────────────────────────────────────────────────


class ActionProcessNextAction(Action):
    def name(self) -> str:
        return "action_process_next_action"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        choice = payload.get("next_action", "create_another")
        desired = tracker.get_slot("desired_output") or ""

        if choice == "restart":
            return [AllSlotsReset(), FollowupAction("action_set_session_id")]

        elif choice == "create_another":
            headers = _headers_from_sources(tracker, payload)
            dispatcher.utter_message(
                json_message={
                    "type": "adaptive_card",
                    "card": "desired_output",
                    "title": "Output Selection",
                    "subtitle": "What kind of output would you like to create?",
                    "inputs": [
                        {
                            "id": "desired_output",
                            "type": "radio",
                            "label": "Select output type",
                            "options": [
                                {"label": "TOP Report", "value": "top_report"},
                                {"label": "PVM Analysis", "value": "pvm_analysis"},
                                {"label": "General Sales Table", "value": "general_sales_table"},
                                {"label": "Churn / ARR Bridge", "value": "churn"},
                                {"label": "Bubble Scatter Plot", "value": "bubble_scatter"},
                            ],
                        }
                    ],
                    "submit_label": "Continue",
                }
            )

        elif choice == "change_filters":
            ctx_map = {
                "general_sales_table": "gst",
                "pvm_analysis": "pvm",
                "top_report": "top",
                "bubble_scatter": "bs",
                "churn": "churn",
                "fixed_assets_rollforward": "fa",
            }
            ctx = ctx_map.get(desired, "gst")
            checkpoint_map = {
                "gst": "action_enter_gst_filter_checkpoint",
                "pvm": "action_enter_pvm_filter_checkpoint",
                "top": "action_enter_top_filter_checkpoint",
                "bs": "action_enter_bs_filter_checkpoint",
                "churn": "action_enter_churn_filter_checkpoint",
                "fa": "action_enter_fa_filter_checkpoint",
            }
            return [
                SlotSet("active_filter_context", ctx),
                FollowupAction(checkpoint_map.get(ctx, "action_enter_gst_filter_checkpoint")),
            ]

        elif choice == "change_settings":
            # Re-trigger the appropriate review flow
            rerun_map = {
                "general_sales_table": "action_show_gst_review",
                "pvm_analysis": "action_show_pvm_review",
                "top_report": "action_show_top_review",
                "bubble_scatter": "action_show_bs_review",
                "churn": "action_show_churn_review",
                "fixed_assets_rollforward": "action_show_fa_rollf_grouping",
            }
            next_act = rerun_map.get(desired, "utter_default")
            return [FollowupAction(next_act)]

        return []


# ─── Databook flow ────────────────────────────────────────────────────────────


class ActionProcessComingSoon(Action):
    """Handler for 'coming_soon' cards — routes back to output selection."""

    def name(self) -> str:
        return "action_process_coming_soon"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        dispatcher.utter_message(json_message=_build_output_type_card())
        return []


def _eom_date(year: int, month: int) -> date:
    last_d = calendar.monthrange(year, month)[1]
    return date(year, month, last_d)


def _safe_fy_end_date(fy_end_year: int, fy_end_m: int, fy_end_d: int) -> date:
    last_d = calendar.monthrange(fy_end_year, fy_end_m)[1]
    d = min(int(fy_end_d), last_d)
    return date(fy_end_year, fy_end_m, d)


def _last_completed_fy_end_calendar_year(
    ltm_y: int, ltm_m: int, fy_end_m: int, fy_end_d: int
) -> int:
    """
    Last fiscal year whose FY-end date falls on or before the end of ltm_month,
    matching funktionssammlung.get_period FY branch semantics.
    """
    as_of_end = _eom_date(ltm_y, ltm_m)
    fye_this_cal_year = _safe_fy_end_date(ltm_y, fy_end_m, fy_end_d)
    return ltm_y if as_of_end >= fye_this_cal_year else ltm_y - 1


def _compute_databook_period_labels(tracker: Tracker) -> list[str]:
    """
    Period column headers for the Databook trial balance grid: FY{yyyy}… and
    optionally YTD{yyyy} when as-of is not the fiscal year-end (Stichtag).
    """
    start = _tracker_first_fy_int(tracker, default=2022)
    ltm_raw = _ltm_month_from_tracker(tracker)
    fy_end_m, fy_end_d = _fy_end_month_day_from_tracker({}, tracker)
    return compute_databook_grid_labels(start, ltm_raw, fy_end_m, fy_end_d)


def _compute_databook_fy_labels(tracker: Tracker) -> list[str]:
    """FY-only grid labels (deprecated — prefer _compute_databook_period_labels)."""
    return [y for y in _compute_databook_period_labels(tracker) if not is_ytd_grid_label(y)]


def _databook_grid_options(tracker: Tracker) -> list[dict[str, Any]]:
    opts: list[dict[str, Any]] = []
    for label in _compute_databook_period_labels(tracker):
        opt: dict[str, Any] = {"label": label, "value": label}
        if is_ytd_grid_label(label):
            opt["optional"] = True
        opts.append(opt)
    return opts


def _compute_fy_years(tracker: Tracker) -> list[str]:
    """Deprecated name — use _compute_databook_fy_labels."""
    return _compute_databook_fy_labels(tracker)


def _utter_databook_susa_format_card(dispatcher: CollectingDispatcher) -> None:
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "databook_susa_format",
            "title": "Databook — Trial balance file layout",
            "inputs": [
                {
                    "id": "db_susa_layout_format",
                    "type": "radio",
                    "label": "How are monthly trial balances provided?",
                    "options": [
                        {
                            "label": "12 separate Excel files per fiscal year (one workbook per month)",
                            "value": "monthly_workbooks",
                        },
                        {
                            "label": "One Excel per fiscal year — one sheet per month (12 sheets)",
                            "value": "monthly_sheets",
                        },
                        {
                            "label": "One Excel per fiscal year — all months on a single sheet",
                            "value": "single_sheet",
                        },
                    ],
                }
            ],
            "submit_label": "Continue",
            "secondary_submit_label": (
                "I already have a finished SuSa_Master in the correct format"
            ),
            "secondary_submit_id": "db_use_existing_master",
            "secondary_submit_variant": "link",
        }
    )


def _utter_databook_master_upload_card(dispatcher: CollectingDispatcher) -> None:
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "databook_master_upload",
            "title": "Databook — Upload SuSa_Master",
            "subtitle": (
                "Upload your finished SuSa_Master workbook (Master_BS and Master_PL). "
                "Trial balance upload, consolidation and adjustments will be skipped."
            ),
            "inputs": [
                {
                    "id": "master_workbook_file",
                    "type": "file_drop",
                    "label": "SuSa_Master (.xlsx)",
                    "accept": ".xlsx",
                }
            ],
            "submit_label": "Continue",
        }
    )


class ActionRunDatabook(Action):
    def name(self) -> str:
        return "action_run_databook"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        dispatcher.utter_message(
            text=(
                "Let's set up the Databook. First, choose how your trial balances "
                "are organised in Excel."
            )
        )
        _utter_databook_susa_format_card(dispatcher)
        return []


def _utter_databook_entity_count_card(dispatcher: CollectingDispatcher, tracker: Tracker) -> None:
    default_count = tracker.get_slot("db_entity_count")
    try:
        default_n = int(float(default_count)) if default_count not in (None, "") else 1
    except (ValueError, TypeError):
        default_n = 1
    default_n = max(1, min(50, default_n))

    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "databook_entity_count",
            "title": "Databook — Number of entities",
            "subtitle": "How many legal entities should be included in the trial balance upload grid?",
            "inputs": [
                {
                    "id": "db_entity_count",
                    "type": "number",
                    "label": "Number of entities",
                    "default": str(default_n),
                    "placeholder": "1",
                    "min": 1,
                    "max": 50,
                    "required": True,
                }
            ],
            "submit_label": "Continue",
        }
    )


def _utter_databook_entities_grid(
    dispatcher: CollectingDispatcher,
    tracker: Tracker,
    layout: str,
    entity_count: int,
) -> None:
    years = _compute_databook_period_labels(tracker)
    grid_options = _databook_grid_options(tracker)
    has_ytd = any(is_ytd_grid_label(y) for y in years)
    grid_mode = "folder" if layout == "monthly_workbooks" else "single_file"
    n = max(1, min(50, int(entity_count)))
    if layout == "monthly_workbooks":
        sub = (
            "For each entity and fiscal year, select the folder that contains exactly "
            "twelve monthly .xlsx workbooks."
        )
        note = "One folder (12 .xlsx files) per entity per fiscal year. Entity names are editable."
    else:
        sub = "Enter each entity name and upload one .xlsx trial balance file per fiscal year."
        note = "One trial balance (.xlsx) per entity per fiscal year. Entity names are editable."
    if has_ytd:
        note += " YTD columns are optional (upload only where available per entity)."

    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "databook_entities",
            "title": "Databook — Entity Files",
            "subtitle": sub,
            "inputs": [
                {
                    "id": "susa_files",
                    "type": "susa_grid",
                    "label": "Entity files",
                    "options": grid_options,
                    "entity_count": n,
                    "grid_mode": grid_mode,
                }
            ],
            "susa_grid_note": note,
            "submit_label": "Continue",
        }
    )


class ActionProcessDatabookSusaFormat(Action):
    """After user selects trial balance layout, ask how many entities to include."""

    def name(self) -> str:
        return "action_process_databook_susa_format"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        if payload.get("db_use_existing_master"):
            _utter_databook_master_upload_card(dispatcher)
            return [SlotSet("db_master_imported", False)]

        layout = str(payload.get("db_susa_layout_format") or "").strip()
        if layout not in ("monthly_workbooks", "monthly_sheets", "single_sheet"):
            dispatcher.utter_message(text="Please choose one of the trial balance layout options.")
            _utter_databook_susa_format_card(dispatcher)
            return []

        _remember_databook_layout(tracker, layout)
        _utter_databook_entity_count_card(dispatcher, tracker)
        return [SlotSet("db_susa_layout_format", layout)]


class ActionShowDatabookMasterUpload(Action):
    """Re-display the finished SuSa_Master upload card (redo/undo)."""

    def name(self) -> str:
        return "action_show_databook_master_upload"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        _utter_databook_master_upload_card(dispatcher)
        return []


class ActionProcessDatabookMasterUpload(Action):
    def name(self) -> str:
        return "action_process_databook_master_upload"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        file_id = (
            payload.get("master_workbook_file_file_id")
            or payload.get("master_workbook_file_id")
            or payload.get("file_id")
            or payload.get("master_workbook_file")
        )
        if not file_id:
            dispatcher.utter_message(text="Please upload a SuSa_Master workbook (.xlsx).")
            _utter_databook_master_upload_card(dispatcher)
            return []

        body = {
            "session_id": _fdd_session_id(tracker),
            "master_file_id": str(file_id),
            "output_folder": tracker.get_slot("output_folder") or "",
            "project_name": tracker.get_slot("project_name") or "Project",
        }
        result = _fdd_post("/api/v1/fdd/run/databook/import-master", body)
        events: list = []
        if result.get("success"):
            if not _utter_output_file_attachment(
                dispatcher,
                result,
                title="Master workbook (BS & PL)",
                tracker=tracker,
            ):
                dispatcher.utter_message(text="SuSa_Master imported successfully.")
            master_path = _master_path_from_result(result, tracker)
            if master_path:
                events.append(SlotSet("db_master_workbook_path", master_path))
            entities = result.get("entities") or []
            if entities:
                events.append(SlotSet("db_entity_names", list(entities)))
            events.extend([
                SlotSet("db_susa_mapping_confirmed", True),
                SlotSet("db_master_imported", True),
            ])
            _utter_databook_l4_sort_card(dispatcher, tracker)
        else:
            dispatcher.utter_message(
                text=f"Import failed: {result.get('message', 'Unknown error')}"
            )
            _utter_databook_master_upload_card(dispatcher)
        return events


class ActionProcessDatabookEntityCount(Action):
    """After entity count is set, show the entity × FY trial balance upload grid."""

    def name(self) -> str:
        return "action_process_databook_entity_count"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        layout = _databook_layout_from_sources(tracker, payload)
        if layout not in ("monthly_workbooks", "monthly_sheets", "single_sheet"):
            dispatcher.utter_message(
                text="Trial balance file layout was not set. Please choose the layout option first."
            )
            return [FollowupAction("action_run_databook")]

        raw = payload.get("db_entity_count", tracker.get_slot("db_entity_count"))
        try:
            count = int(float(raw)) if raw not in (None, "") else 1
        except (ValueError, TypeError):
            count = 1
        if count < 1 or count > 50:
            dispatcher.utter_message(text="Please enter a number of entities between 1 and 50.")
            _utter_databook_entity_count_card(dispatcher, tracker)
            return []

        _utter_databook_entities_grid(dispatcher, tracker, layout, count)
        return [SlotSet("db_entity_count", float(count))]


class ActionShowDatabookEntityCount(Action):
    """Re-display the entity count card (e.g. after undo)."""

    def name(self) -> str:
        return "action_show_databook_entity_count"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        layout = _databook_layout_from_sources(tracker)
        if layout not in ("monthly_workbooks", "monthly_sheets", "single_sheet"):
            return [FollowupAction("action_run_databook")]
        _utter_databook_entity_count_card(dispatcher, tracker)
        return []


def _parse_susa_grid_file_groups(payload: dict) -> dict[str, list[str]]:
    """Map grid cell key -> list of file_id (length 1 for single-file modes, 12 for folders)."""
    groups = payload.get("susa_file_groups")
    if isinstance(groups, dict):
        out: dict[str, list[str]] = {}
        for k, v in groups.items():
            key = str(k)
            if isinstance(v, list):
                out[key] = [str(x) for x in v if x not in (None, "")]
            elif v not in (None, ""):
                out[key] = [str(v)]
        return out
    legacy = payload.get("susa_files") or {}
    if isinstance(legacy, dict):
        return {str(k): [str(v)] for k, v in legacy.items() if v not in (None, "")}
    return {}


def _entity_year_files_from_groups(
    groups: dict[str, list[str]],
    entity_names: list[Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, ids in groups.items():
        if not ids:
            continue
        parts = str(key).split("_")
        if len(parts) < 3 or parts[0] != "entity":
            continue
        try:
            entity_idx = int(parts[1]) - 1
        except ValueError:
            continue
        fy_label = "_".join(parts[2:])
        names_list = list(entity_names) if isinstance(entity_names, list) else []
        name = (
            names_list[entity_idx]
            if entity_idx < len(names_list)
            else f"Entity {entity_idx + 1}"
        )
        rows.append(
            {
                "entity_index": entity_idx,
                "entity_name": name,
                "fy_label": fy_label,
                "file_ids": ids,
            }
        )
    rows.sort(key=lambda r: (r["entity_index"], r["fy_label"]))
    return rows


def _db_entity_year_files_from_tracker(tracker: Tracker) -> list[dict[str, Any]]:
    cached = _SESSION_DB_ENTITY_YEAR_FILES.get(_sender_cache_key(tracker) or "", [])
    if cached:
        return [r for r in cached if isinstance(r, dict)]
    raw = tracker.get_slot("db_entity_year_files") or tracker.get_slot("db_entity_files")
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    raw_json = tracker.get_slot("db_entity_year_files_json")
    if isinstance(raw_json, str) and raw_json.strip():
        try:
            parsed = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, list):
            return [r for r in parsed if isinstance(r, dict)]
    return []


def _db_entity_names_from_tracker(tracker: Tracker) -> list[str]:
    cached = _SESSION_DB_ENTITY_NAMES.get(_sender_cache_key(tracker) or "", [])
    if cached:
        return [str(x) for x in cached if x not in (None, "")]
    raw = tracker.get_slot("db_entity_names")
    if isinstance(raw, list):
        return [str(x) for x in raw if x not in (None, "")]
    return []


def _utter_databook_susa_options(dispatcher: CollectingDispatcher) -> None:
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "databook_susa_options",
            "title": "Trial balance setup",
            "inputs": [
                {
                    "id": "db_susa_value_type",
                    "type": "radio",
                    "label": "Do the period columns show account balances or period movements?",
                    "options": [
                        {"label": "Balances", "value": "balances"},
                        {"label": "Movements", "value": "movements"},
                    ],
                },
                {
                    "id": "db_susa_sign_mode",
                    "type": "radio",
                    "label": "How are debit and credit signs represented in the file?",
                    "options": [
                        {"label": "Signed amounts", "value": "already_signed"},
                        {"label": "One indicator column per amount", "value": "sh_column"},
                        {
                            "label": "Separate debit (S) and credit (H) columns",
                            "value": "sh_two_columns",
                        },
                    ],
                },
                {
                    "id": "db_susa_ap_ar_included",
                    "type": "radio",
                    "label": "Are AP/AR sub-ledger accounts included in the trial balance?",
                    "options": [
                        {"label": "Yes", "value": "yes"},
                        {"label": "No", "value": "no"},
                    ],
                },
            ],
            "submit_label": "Continue",
        }
    )


def _first_preview_file_id(entity_year_files: list) -> str:
    rows = sorted(
        [r for r in entity_year_files if isinstance(r, dict) and r.get("file_ids")],
        key=lambda r: (r.get("entity_index", 0), str(r.get("fy_label", ""))),
    )
    if not rows:
        return ""
    ids = rows[0].get("file_ids") or []
    return str(ids[0]) if ids else ""


def _utter_databook_ap_ar_digits_card(dispatcher: CollectingDispatcher) -> None:
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "databook_ap_ar_digits",
            "title": "Databook — Debtor/creditor account number length",
            "inputs": [
                {
                    "id": "db_ap_ar_account_digits",
                    "type": "number",
                    "label": "How many digits do debtor/creditor account numbers have?",
                    "min": 1,
                    "max": 20,
                    "required": True,
                }
            ],
            "submit_label": "Continue",
            "secondary_submit_label": "Not clearly identifiable",
            "secondary_submit_id": "db_ap_ar_not_identifiable",
        }
    )


def _utter_databook_column_mapper_card(
    dispatcher: CollectingDispatcher,
    tracker: Tracker,
) -> None:
    entity_year_files = _db_entity_year_files_from_tracker(tracker)
    entity_names = _db_entity_names_from_tracker(tracker)
    preview_file_id = _first_preview_file_id(list(entity_year_files))
    session_id = _fdd_session_id(tracker)
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "databook_susa_column_mapper",
            "title": "Databook — Map trial balance columns",
            "mapper_meta": {
                "session_id": str(session_id),
                "preview_file_id": preview_file_id,
                "layout_format": _databook_layout_from_sources(tracker) or "",
                "sign_mode": tracker.get_slot("db_susa_sign_mode") or "sh_column",
                "value_type": tracker.get_slot("db_susa_value_type") or "balances",
                "entity_names": list(entity_names),
            },
        }
    )


def _db_entity_count_from_tracker(tracker: Tracker) -> int:
    raw = tracker.get_slot("db_entity_count")
    try:
        n = int(float(raw)) if raw not in (None, "") else 0
    except (ValueError, TypeError):
        n = 0
    if n > 0:
        return n
    names = tracker.get_slot("db_entity_names") or []
    return len(names) if isinstance(names, list) else 0


def _master_path_from_result(result: dict, tracker: Tracker) -> str:
    return str(
        result.get("master_path")
        or result.get("output_file")
        or tracker.get_slot("db_master_workbook_path")
        or ""
    )


def _utter_databook_consolidation_account_card(dispatcher: CollectingDispatcher) -> None:
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "databook_consolidation_account",
            "title": "Consolidation",
            "subtitle": "Is the consolidation available on account level?",
            "inputs": [
                {
                    "id": "db_consolidation_account_level",
                    "type": "radio",
                    "label": "Is the consolidation available on account level?",
                    "options": [
                        {"label": "Yes", "value": "yes"},
                        {"label": "No", "value": "no"},
                    ],
                }
            ],
            "submit_label": "Continue",
        }
    )


def _utter_databook_consolidation_level_card(dispatcher: CollectingDispatcher) -> None:
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "databook_consolidation_level",
            "title": "Consolidation period",
            "subtitle": "Is the consolidation available on monthly or yearly level?",
            "inputs": [
                {
                    "id": "db_consolidation_period_level",
                    "type": "radio",
                    "label": "Consolidation period level",
                    "options": [
                        {"label": "Monthly", "value": "monthly"},
                        {"label": "Yearly", "value": "yearly"},
                    ],
                }
            ],
            "submit_label": "Continue",
        }
    )


def _abs_master_path_from_tracker(tracker: Tracker) -> str:
    from databook_paths import master_workbook_filename, resolve_existing_master_path

    raw = str(tracker.get_slot("db_master_workbook_path") or "").strip()
    if raw and os.path.isabs(raw) and os.path.isfile(raw):
        return os.path.abspath(raw)
    if raw:
        candidate = os.path.abspath(raw)
        if os.path.isfile(candidate):
            return candidate

    expanded_folder = _expand_output_folder(str(tracker.get_slot("output_folder") or ""))
    sid = _fdd_session_id(tracker)
    project_name = str(tracker.get_slot("project_name") or "Project")
    if expanded_folder:
        resolved = resolve_existing_master_path(
            expanded_folder,
            session_id=sid,
            project_name=project_name,
        )
        if resolved:
            return str(resolved)

    if raw:
        return raw
    if expanded_folder:
        return os.path.join(expanded_folder, master_workbook_filename(project_name))
    return ""


def _consolidation_template_download_qs(tracker: Tracker) -> str:
    session_id = _fdd_session_id(tracker)
    parts = [f"session_id={quote(session_id)}"]
    output_folder = _expand_output_folder(str(tracker.get_slot("output_folder") or ""))
    if output_folder:
        parts.append(f"output_folder={quote(output_folder)}")
    period = str(tracker.get_slot("db_consolidation_period_level") or "yearly").strip().lower()
    parts.append(f"period_level={quote(period)}")
    first_fy = _tracker_first_fy_int(tracker, default=0)
    if first_fy:
        parts.append(f"first_fy={first_fy}")
    ltm = _ltm_month_from_tracker(tracker) or ""
    if ltm:
        parts.append(f"ltm_month={quote(ltm)}")
    fy_end_m, fy_end_d = _fy_end_month_day_from_tracker({}, tracker)
    parts.append(f"fy_end_month={fy_end_m}")
    parts.append(f"fy_end_day={int(fy_end_d)}")
    parts.append(f"fiscal_start_month={(fy_end_m % 12) + 1}")
    return "&".join(parts)


def _utter_databook_consolidation_upload_card(
    dispatcher: CollectingDispatcher,
    tracker: Tracker,
) -> None:
    qs = _consolidation_template_download_qs(tracker)
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "databook_consolidation_upload",
            "title": "Consolidation Template",
            "subtitle": (
                "No consolidation data available. Please download the template, fill it, and re-upload."
            ),
            "inputs": [
                {
                    "id": "consolidation_file",
                    "type": "file_drop",
                    "label": "Upload filled consolidation template",
                    "accept": ".xlsx",
                }
            ],
            "download_template": f"/api/v1/fdd/templates/consolidation?{qs}",
            "submit_label": "Upload",
        }
    )


def _adjustments_template_download_qs(tracker: Tracker) -> str:
    session_id = _fdd_session_id(tracker)
    parts = [f"session_id={quote(session_id)}"]
    output_folder = _expand_output_folder(str(tracker.get_slot("output_folder") or ""))
    if output_folder:
        parts.append(f"output_folder={quote(output_folder)}")
    first_fy = _tracker_first_fy_int(tracker, default=0)
    if first_fy:
        parts.append(f"first_fy={first_fy}")
    ltm = _ltm_month_from_tracker(tracker) or ""
    if ltm:
        parts.append(f"ltm_month={quote(ltm)}")
    fy_end_m, fy_end_d = _fy_end_month_day_from_tracker({}, tracker)
    parts.append(f"fy_end_month={fy_end_m}")
    parts.append(f"fy_end_day={int(fy_end_d)}")
    parts.append(f"fiscal_start_month={(fy_end_m % 12) + 1}")
    return "&".join(parts)


def _utter_databook_adjustments_upload_card(
    dispatcher: CollectingDispatcher,
    tracker: Tracker,
) -> None:
    qs = _adjustments_template_download_qs(tracker)
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "databook_adjustments_upload",
            "title": "Adjustments Template",
            "subtitle": (
                "Please download the adjustments template, fill it, and re-upload."
            ),
            "inputs": [
                {
                    "id": "adjustments_file",
                    "type": "file_drop",
                    "label": "Upload filled adjustments template",
                    "accept": ".xlsx",
                }
            ],
            "download_template": f"/api/v1/fdd/templates/adjustments?{qs}",
            "submit_label": "Upload",
        }
    )


def _utter_databook_l4_sort_card(dispatcher: CollectingDispatcher, tracker: Tracker) -> None:
    fy_end_m, fy_end_d = _fy_end_month_day_from_tracker({}, tracker)
    ltm = _ltm_month_from_tracker(tracker) or ""
    show_ytd = ytd_reporting_fy_end_year(ltm, fy_end_m, fy_end_d) is not None

    options = [
        {"label": "Sum of all fiscal years", "value": "all_fy"},
        {"label": "Latest fiscal year", "value": "latest_fy"},
    ]
    if show_ytd:
        options.append({"label": "Year-to-date (YTD)", "value": "ytd"})

    default = tracker.get_slot("db_l4_sort_basis") or "latest_fy"
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "databook_l4_sort",
            "title": "L4 line sorting",
            "subtitle": (
                "How should detail lines (L4) be ordered within each subtotal (L3)? "
                "Lines are sorted by absolute amount, largest first."
            ),
            "inputs": [
                {
                    "id": "db_l4_sort_basis",
                    "type": "radio",
                    "label": "Sort L4 lines by amount based on",
                    "options": options,
                    "default": str(default),
                }
            ],
            "submit_label": "Continue",
        }
    )


def _utter_databook_adjustments_card(dispatcher: CollectingDispatcher) -> None:
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "databook_adjustments",
            "title": "Adjustments",
            "subtitle": "Do you want to apply adjustments at this stage?",
            "inputs": [
                {
                    "id": "db_apply_adjustments",
                    "type": "radio",
                    "label": "Apply adjustments?",
                    "options": [
                        {"label": "Yes", "value": "yes"},
                        {"label": "No", "value": "no"},
                    ],
                }
            ],
            "submit_label": "Continue",
        }
    )


def _utter_databook_recon_order_from_tracker(
    dispatcher: CollectingDispatcher,
    tracker: Tracker,
) -> None:
    entity_names = list(
        tracker.get_slot("db_entity_names") or _db_entity_names_from_tracker(tracker)
    )
    if not entity_names:
        body = {
            "session_id": _fdd_session_id(tracker),
            "output_folder": tracker.get_slot("output_folder") or "",
            "master_path": tracker.get_slot("db_master_workbook_path") or "",
            "project_name": tracker.get_slot("project_name") or "Project",
        }
        result = _fdd_post("/api/v1/fdd/run/databook/extract-entities", body)
        if result.get("success"):
            entity_names = list(result.get("entities") or [])
    _utter_databook_recon_order_card(dispatcher, entity_names)


def _utter_databook_fs_extract_choice_card(dispatcher: CollectingDispatcher) -> None:
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "databook_fs_extract_choice",
            "title": "Financial statements",
            "subtitle": "Would you like to extract financial statements at this step?",
            "inputs": [
                {
                    "id": "db_extract_financial_statements",
                    "type": "radio",
                    "label": "Extract financial statements now?",
                    "options": [
                        {"label": "Yes", "value": "yes"},
                        {"label": "No", "value": "no"},
                    ],
                }
            ],
            "submit_label": "Continue",
        }
    )


def _utter_databook_fs_extract_placeholder_card(dispatcher: CollectingDispatcher) -> None:
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "databook_fs_extract_placeholder",
            "title": "Financial statements — extraction",
            "subtitle": (
                "Automatic financial statement extraction is not yet available here "
                "(placeholder)."
            ),
            "submit_label": "Continue",
        }
    )


def _utter_databook_fs_upload_card(dispatcher: CollectingDispatcher) -> None:
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "databook_fs_upload",
            "title": "Financial statements",
            "subtitle": "Please upload the folder containing all relevant financial statements (PDF).",
            "inputs": [
                {
                    "id": "fs_folder",
                    "type": "folder_drop",
                    "label": "Financial statements folder",
                    "accept": ".pdf",
                }
            ],
            "submit_label": "Upload & extract",
        }
    )


def _utter_databook_fs_review_upload_card(dispatcher: CollectingDispatcher) -> None:
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "databook_fs_review_upload",
            "title": "Review financial statements",
            "subtitle": "Please review the extracted Excel above and re-upload the corrected file.",
            "inputs": [
                {
                    "id": "fs_review_file",
                    "type": "file_drop",
                    "label": "Upload reviewed financial statements Excel",
                    "accept": ".xlsx",
                }
            ],
            "submit_label": "Continue",
        }
    )


def _utter_databook_recon_order_card(
    dispatcher: CollectingDispatcher,
    entity_names: list,
) -> None:
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "databook_recon_order",
            "title": "Reconciliation entity order",
            "subtitle": "Drag entities to set the order for PL and BS reconciliation tables.",
            "inputs": [
                {
                    "id": "db_recon_entity_order",
                    "type": "sortable_list",
                    "label": "Custom order",
                    "options": [{"label": n, "value": n} for n in entity_names],
                }
            ],
            "submit_label": "Continue",
        }
    )


def _utter_databook_recon_labels_card(dispatcher: CollectingDispatcher) -> None:
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "databook_recon_labels",
            "title": "Reconciliation position labels",
            "subtitle": "Confirm or adjust the section titles used in the reconciliation table.",
            "inputs": [
                {"id": "recon_title_aggregated", "type": "text", "label": "Aggregated", "default": "Aggregated"},
                {"id": "recon_title_consolidation", "type": "text", "label": "Consolidation", "default": "Consolidation"},
                {"id": "recon_title_difference", "type": "text", "label": "Difference", "default": "Difference"},
                {
                    "id": "recon_title_financial_statements",
                    "type": "text",
                    "label": "Financial statements",
                    "default": "Financial statements",
                },
                {"id": "recon_title_ic", "type": "text", "label": "IC eliminations", "default": "IC eliminations"},
            ],
            "submit_label": "Build reconciliation",
        }
    )


def _utter_databook_extended_tables_card(dispatcher: CollectingDispatcher) -> None:
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "databook_extended_tables",
            "title": "Extended tables",
            "subtitle": (
                "Do you also want to create BS_Bucket, Lead_IS, Lead_BS, "
                "Working capital and Cashflow?"
            ),
            "inputs": [
                {
                    "id": "db_create_extended_tables",
                    "type": "radio",
                    "label": "Create extended tables?",
                    "options": [
                        {"label": "Yes", "value": "yes"},
                        {"label": "No", "value": "no"},
                    ],
                }
            ],
            "submit_label": "Continue",
        }
    )


def _utter_databook_master_next_action_card(dispatcher: CollectingDispatcher) -> None:
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "databook_master_next_action",
            "title": "What would you like to do now?",
            "inputs": [
                {
                    "id": "db_master_next_action",
                    "type": "radio",
                    "label": "Next step",
                    "options": [
                        {
                            "label": "Extract financial statements",
                            "value": "fs_extract",
                        },
                        {
                            "label": "Create another output",
                            "value": "another_output",
                        },
                    ],
                }
            ],
            "submit_label": "Continue",
        }
    )


def _utter_prototype_not_supported(dispatcher: CollectingDispatcher, detail: str = "") -> None:
    msg = "This combination is not supported in the current prototype."
    if detail:
        msg = f"{msg} ({detail})"
    dispatcher.utter_message(text=msg)


def _databook_after_susa_run(
    dispatcher: CollectingDispatcher,
    tracker: Tracker,
    result: dict,
) -> list:
    events: list = []
    if result.get("success"):
        if not _utter_output_file_attachment(
            dispatcher,
            result,
            title="Master workbook (BS & PL)",
            tracker=tracker,
        ):
            dispatcher.utter_message(
                text=(
                    "Master_BS and Master_PL were created, but the workbook file "
                    f"could not be located ({result.get('output_path', '')})."
                )
            )
        master_path = _master_path_from_result(result, tracker)
        if master_path:
            events.append(SlotSet("db_master_workbook_path", master_path))
    else:
        dispatcher.utter_message(
            text=f"Error creating Master_BS / Master_PL: {result.get('message', 'Unknown error')}"
        )
        return events + [SlotSet("db_susa_mapping_confirmed", True)]

    entity_count = _db_entity_count_from_tracker(tracker)
    if entity_count > 1:
        _utter_databook_consolidation_account_card(dispatcher)
    else:
        _utter_databook_adjustments_card(dispatcher)

    return events + [SlotSet("db_susa_mapping_confirmed", True)]


def _missing_databook_grid_uploads(
    entity_year_files: list[dict[str, Any]],
    entity_count: int,
    required_labels: list[str],
    entity_names: list[Any],
) -> list[str]:
    uploaded: set[tuple[int, str]] = set()
    for row in entity_year_files:
        try:
            ei = int(row.get("entity_index", 0))
        except (TypeError, ValueError):
            continue
        uploaded.add((ei, str(row.get("fy_label") or "")))
    missing: list[str] = []
    for ei in range(max(1, entity_count)):
        name_list = list(entity_names) if isinstance(entity_names, list) else []
        label_name = (
            str(name_list[ei])
            if ei < len(name_list) and name_list[ei] not in (None, "")
            else f"Entity {ei + 1}"
        )
        for period_label in required_labels:
            if (ei, period_label) not in uploaded:
                missing.append(f"{label_name}: {period_label}")
    return missing


class ActionProcessDatabookEntities(Action):
    def name(self) -> str:
        return "action_process_databook_entities"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)

        groups = _parse_susa_grid_file_groups(payload)
        entity_names: list = list(payload.get("entity_names") or [])
        entity_year_files = _entity_year_files_from_groups(groups, entity_names)

        layout = _databook_layout_from_sources(tracker, payload)

        if layout not in ("monthly_workbooks", "monthly_sheets", "single_sheet"):
            dispatcher.utter_message(
                text="The trial balance file layout was not set. Please use Continue on the layout card, then upload again."
            )
            return []

        if not entity_year_files:
            dispatcher.utter_message(
                text="No trial balance files were submitted. Please upload at least one cell in the grid."
            )
            return []

        if layout == "monthly_workbooks":
            bad = [r for r in entity_year_files if len(r["file_ids"]) != 12]
            if bad:
                dispatcher.utter_message(
                    text=(
                        "For the selected layout (12 workbooks per year), each grid cell must "
                        "have exactly twelve .xlsx files. Please fix the highlighted selections and try again."
                    )
                )
                return []

        if layout in ("monthly_sheets", "single_sheet"):
            bad = [r for r in entity_year_files if len(r["file_ids"]) != 1]
            if bad:
                dispatcher.utter_message(
                    text="Please upload exactly one .xlsx file per filled grid cell."
                )
                return []

        try:
            entity_count = int(float(payload.get("db_entity_count", tracker.get_slot("db_entity_count") or 1)))
        except (ValueError, TypeError):
            entity_count = max(1, len(entity_names))
        entity_count = max(1, min(50, entity_count or len(entity_names) or 1))

        required_labels = [x for x in _compute_databook_period_labels(tracker) if not is_ytd_grid_label(x)]
        missing = _missing_databook_grid_uploads(
            entity_year_files, entity_count, required_labels, entity_names
        )
        if missing:
            dispatcher.utter_message(
                text=(
                    "Please upload trial balances for every entity and required FY column in the grid. "
                    f"Missing: {', '.join(missing)}."
                )
            )
            _utter_databook_entities_grid(dispatcher, tracker, layout, entity_count)
            return []

        consolidation_available = payload.get("consolidation_available") in (True, "yes", "true")

        _remember_databook_entity_uploads(tracker, entity_year_files, entity_names)
        _utter_databook_susa_options(dispatcher)

        return [
            SlotSet("db_entity_year_files", entity_year_files),
            SlotSet("db_entity_year_files_json", json.dumps(entity_year_files, ensure_ascii=False)),
            SlotSet("db_entity_names", entity_names),
            SlotSet("db_entity_files", entity_year_files),
            SlotSet("db_consolidation_available", consolidation_available),
        ]


class ActionProcessDatabookSusaInterpretation(Action):
    """Store interpretation slots; route to AP/AR digits or column mapper (no SuSa run yet)."""

    def name(self) -> str:
        return "action_process_databook_susa_interpretation"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)

        value_type = str(payload.get("db_susa_value_type") or "").strip()
        if value_type not in ("balances", "movements"):
            dispatcher.utter_message(text="Please select whether amounts are balances or movements.")
            return []

        sign_mode = str(payload.get("db_susa_sign_mode") or "").strip()
        if sign_mode not in ("already_signed", "sh_column", "sh_two_columns"):
            dispatcher.utter_message(text="Please select how debit/credit signs are represented.")
            return []

        ap_raw = str(payload.get("db_susa_ap_ar_included") or "").strip().lower()
        ap_ar_present = ap_raw in ("yes", "true", "1")

        events = [
            SlotSet("db_susa_value_type", value_type),
            SlotSet("db_susa_sign_mode", sign_mode),
            SlotSet("db_susa_ap_ar_included", ap_ar_present),
        ]

        if ap_ar_present:
            _utter_databook_ap_ar_digits_card(dispatcher)
        else:
            _utter_databook_column_mapper_card(dispatcher, tracker)
            events.append(SlotSet("db_ap_ar_account_digits", None))

        return events


class ActionProcessDatabookApArDigits(Action):
    def name(self) -> str:
        return "action_process_databook_ap_ar_digits"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)

        if payload.get("db_ap_ar_not_identifiable"):
            dispatcher.utter_message(
                text=(
                    "Please remove debtor/creditor detail accounts from your trial balance files manually, "
                    "then restart the Databook flow with the updated files."
                )
            )
            return [SlotSet("db_ap_ar_account_digits", None)]

        raw = payload.get("db_ap_ar_account_digits")
        try:
            digits = int(float(raw)) if raw not in (None, "") else 0
        except (ValueError, TypeError):
            digits = 0
        if digits < 1 or digits > 20:
            dispatcher.utter_message(
                text="Please enter how many digits debtor/creditor account numbers have (1–20)."
            )
            _utter_databook_ap_ar_digits_card(dispatcher)
            return []

        _utter_databook_column_mapper_card(dispatcher, tracker)
        return [SlotSet("db_ap_ar_account_digits", float(digits))]


class ActionShowDatabookColumnMapper(Action):
    """Re-display column mapper (undo / redo)."""

    def name(self) -> str:
        return "action_show_databook_column_mapper"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        _utter_databook_column_mapper_card(dispatcher, tracker)
        return []


class ActionProcessDatabookColumnMapping(Action):
    """After confirmed column mapping, run SuSa master build."""

    def name(self) -> str:
        return "action_process_databook_column_mapping"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        column_mapping = payload.get("column_mapping")
        if not isinstance(column_mapping, dict) or not column_mapping.get("default"):
            dispatcher.utter_message(text="Column mapping is incomplete. Please map all required columns.")
            _utter_databook_column_mapper_card(dispatcher, tracker)
            return []

        session_id = _fdd_session_id(tracker)
        entity_year_files = _db_entity_year_files_from_tracker(tracker)
        entity_names = _db_entity_names_from_tracker(tracker)
        layout = _databook_layout_from_sources(tracker, payload)

        ap_digits = tracker.get_slot("db_ap_ar_account_digits")
        ap_remove: Optional[int] = None
        if ap_digits not in (None, ""):
            try:
                ap_remove = int(float(ap_digits))
            except (ValueError, TypeError):
                ap_remove = None

        fy_end_m, fy_end_d = _fy_end_month_day_from_tracker({}, tracker)
        fiscal_start_month = (fy_end_m % 12) + 1
        ltm_month = _ltm_month_from_tracker(tracker) or ""

        body = {
            "session_id": session_id,
            "entity_names": list(entity_names),
            "entity_year_files": list(entity_year_files),
            "output_folder": tracker.get_slot("output_folder") or "",
            "layout_format": layout,
            "value_type": tracker.get_slot("db_susa_value_type") or "balances",
            "sign_mode": tracker.get_slot("db_susa_sign_mode") or "sh_column",
            "ap_ar_included": tracker.get_slot("db_susa_ap_ar_included") in (True, "yes", "true", 1),
            "ap_ar_remove_account_length": ap_remove,
            "column_mapping": column_mapping,
            "fy_end_month": fy_end_m,
            "fy_end_day": fy_end_d,
            "fiscal_start_month": fiscal_start_month,
            "ltm_month": ltm_month,
            "first_fy": _tracker_first_fy_int(tracker),
            "project_name": tracker.get_slot("project_name") or "Project",
        }
        result = _fdd_post("/api/v1/fdd/run/databook/susa", body)
        events = [
            SlotSet("db_susa_column_mapping", column_mapping),
            *_databook_after_susa_run(dispatcher, tracker, result),
        ]
        return events


class ActionProcessDatabookConsolidationAccount(Action):
    def name(self) -> str:
        return "action_process_databook_consolidation_account"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        level = str(payload.get("db_consolidation_account_level") or "no").strip().lower()
        _utter_databook_consolidation_level_card(dispatcher)
        return [SlotSet("db_consolidation_account_level", level)]


class ActionProcessDatabookConsolidationLevel(Action):
    def name(self) -> str:
        return "action_process_databook_consolidation_level"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        period = str(payload.get("db_consolidation_period_level") or "yearly").strip().lower()
        account = str(
            tracker.get_slot("db_consolidation_account_level")
            or payload.get("db_consolidation_account_level")
            or "no"
        ).strip().lower()

        events = [SlotSet("db_consolidation_period_level", period)]
        if account == "no" and period in ("yearly", "monthly"):
            fy_end_m, fy_end_d = _fy_end_month_day_from_tracker({}, tracker)
            fiscal_start_month = (fy_end_m % 12) + 1
            ltm = _ltm_month_from_tracker(tracker) or ""
            body = {
                "session_id": _fdd_session_id(tracker),
                "output_folder": tracker.get_slot("output_folder") or "",
                "master_path": _abs_master_path_from_tracker(tracker),
                "project_name": tracker.get_slot("project_name") or "Project",
                "period_level": period,
                "first_fy": _tracker_first_fy_int(tracker),
                "ltm_month": ltm,
                "fy_end_month": fy_end_m,
                "fy_end_day": fy_end_d,
                "fiscal_start_month": fiscal_start_month,
            }
            tpl_result = _fdd_post("/api/v1/fdd/run/databook/consolidation-template", body)
            if not tpl_result.get("success"):
                dispatcher.utter_message(
                    text=(
                        "Could not generate the consolidation template automatically. "
                        f"{tpl_result.get('message', 'Unknown error')} "
                        "You can still try downloading the template below."
                    )
                )
            _utter_databook_consolidation_upload_card(dispatcher, tracker)
        else:
            _utter_prototype_not_supported(
                dispatcher,
                "prototype supports account level = No with yearly or monthly period only",
            )
        return events


class ActionProcessDatabookConsolidationUpload(Action):
    def name(self) -> str:
        return "action_process_databook_consolidation_upload"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        file_id = (
            payload.get("consolidation_file_id")
            or payload.get("file_id")
            or payload.get("consolidation_file")
        )
        if not file_id:
            dispatcher.utter_message(text="Please upload the filled consolidation template.")
            _utter_databook_consolidation_upload_card(dispatcher, tracker)
            return []

        fy_end_m, fy_end_d = _fy_end_month_day_from_tracker({}, tracker)
        fiscal_start_month = (fy_end_m % 12) + 1
        body = {
            "session_id": _fdd_session_id(tracker),
            "consolidation_file_id": str(file_id),
            "output_folder": tracker.get_slot("output_folder") or "",
            "master_path": tracker.get_slot("db_master_workbook_path") or "",
            "project_name": tracker.get_slot("project_name") or "Project",
            "fy_end_month": fy_end_m,
            "fy_end_day": fy_end_d,
            "fiscal_start_month": fiscal_start_month,
        }
        result = _fdd_post("/api/v1/fdd/run/databook/consolidation", body)
        events: list = []
        if result.get("success"):
            if not _utter_output_file_attachment(
                dispatcher,
                result,
                title="Master workbook (consolidation applied)",
                tracker=tracker,
            ):
                dispatcher.utter_message(text="Consolidation applied to the master workbook.")
            master_path = _master_path_from_result(result, tracker)
            if master_path:
                events.append(SlotSet("db_master_workbook_path", master_path))
            _utter_databook_adjustments_card(dispatcher)
        else:
            dispatcher.utter_message(
                text=f"Consolidation error: {result.get('message', 'Unknown error')}"
            )
            _utter_databook_consolidation_upload_card(dispatcher, tracker)
        return events


class ActionProcessDatabookAdjustments(Action):
    def name(self) -> str:
        return "action_process_databook_adjustments"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        apply_adj = str(payload.get("db_apply_adjustments") or "no").strip().lower()
        events = [SlotSet("db_apply_adjustments", apply_adj)]
        if apply_adj == "no":
            _utter_databook_l4_sort_card(dispatcher, tracker)
        else:
            fy_end_m, fy_end_d = _fy_end_month_day_from_tracker({}, tracker)
            fiscal_start_month = (fy_end_m % 12) + 1
            ltm = _ltm_month_from_tracker(tracker) or ""
            body = {
                "session_id": _fdd_session_id(tracker),
                "output_folder": tracker.get_slot("output_folder") or "",
                "master_path": _abs_master_path_from_tracker(tracker),
                "project_name": tracker.get_slot("project_name") or "Project",
                "first_fy": _tracker_first_fy_int(tracker),
                "ltm_month": ltm,
                "fy_end_month": fy_end_m,
                "fy_end_day": fy_end_d,
                "fiscal_start_month": fiscal_start_month,
            }
            tpl_result = _fdd_post("/api/v1/fdd/run/databook/adjustments-template", body)
            if not tpl_result.get("success"):
                dispatcher.utter_message(
                    text=(
                        "Could not generate the adjustments template automatically. "
                        f"{tpl_result.get('message', 'Unknown error')} "
                        "You can still try downloading the template below."
                    )
                )
            _utter_databook_adjustments_upload_card(dispatcher, tracker)
        return events


class ActionProcessDatabookAdjustmentsUpload(Action):
    def name(self) -> str:
        return "action_process_databook_adjustments_upload"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        file_id = (
            payload.get("adjustments_file_id")
            or payload.get("file_id")
            or payload.get("adjustments_file")
        )
        if not file_id:
            dispatcher.utter_message(text="Please upload the filled adjustments template.")
            _utter_databook_adjustments_upload_card(dispatcher, tracker)
            return []

        body = {
            "session_id": _fdd_session_id(tracker),
            "adjustments_file_id": str(file_id),
            "output_folder": tracker.get_slot("output_folder") or "",
            "master_path": _abs_master_path_from_tracker(tracker),
            "project_name": tracker.get_slot("project_name") or "Project",
        }
        result = _fdd_post("/api/v1/fdd/run/databook/adjustments", body)
        events: list = []
        if result.get("success"):
            if not _utter_output_file_attachment(
                dispatcher,
                result,
                title="Master workbook (adjustments applied)",
                tracker=tracker,
            ):
                dispatcher.utter_message(text="Adjustments applied to the master workbook.")
            master_path = _master_path_from_result(result, tracker)
            if master_path:
                events.append(SlotSet("db_master_workbook_path", master_path))
            _utter_databook_l4_sort_card(dispatcher, tracker)
        else:
            dispatcher.utter_message(
                text=f"Adjustments error: {result.get('message', 'Unknown error')}"
            )
            _utter_databook_adjustments_upload_card(dispatcher, tracker)
        return events


class ActionProcessDatabookL4Sort(Action):
    def name(self) -> str:
        return "action_process_databook_l4_sort"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        basis = str(payload.get("db_l4_sort_basis") or "latest_fy").strip().lower()
        if basis not in ("all_fy", "latest_fy", "ytd"):
            dispatcher.utter_message(text="Please choose how L4 lines should be sorted.")
            _utter_databook_l4_sort_card(dispatcher, tracker)
            return []

        if basis == "ytd":
            fy_end_m, fy_end_d = _fy_end_month_day_from_tracker({}, tracker)
            ltm = _ltm_month_from_tracker(tracker) or ""
            if ytd_reporting_fy_end_year(ltm, fy_end_m, fy_end_d) is None:
                dispatcher.utter_message(
                    text="YTD sorting is only available when the as-of month is not the fiscal year-end."
                )
                _utter_databook_l4_sort_card(dispatcher, tracker)
                return []

        _utter_databook_fs_extract_choice_card(dispatcher)
        return [SlotSet("db_l4_sort_basis", basis)]


class ActionShowDatabookFsExtractChoice(Action):
    def name(self) -> str:
        return "action_show_databook_fs_extract_choice"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        _utter_databook_fs_extract_choice_card(dispatcher)
        return []


class ActionProcessDatabookFsExtractChoice(Action):
    def name(self) -> str:
        return "action_process_databook_fs_extract_choice"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        choice = str(payload.get("db_extract_financial_statements") or "").strip().lower()
        if choice not in ("yes", "no"):
            dispatcher.utter_message(text="Please choose Yes or No.")
            _utter_databook_fs_extract_choice_card(dispatcher)
            return []

        events = [SlotSet("db_extract_financial_statements", choice)]
        if choice == "no":
            _utter_databook_recon_order_from_tracker(dispatcher, tracker)
        else:
            _utter_databook_fs_extract_placeholder_card(dispatcher)
        return events


class ActionShowDatabookFsExtractPlaceholder(Action):
    def name(self) -> str:
        return "action_show_databook_fs_extract_placeholder"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        _utter_databook_fs_extract_placeholder_card(dispatcher)
        return []


class ActionProcessDatabookFsExtractPlaceholder(Action):
    def name(self) -> str:
        return "action_process_databook_fs_extract_placeholder"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        _utter_databook_recon_order_from_tracker(dispatcher, tracker)
        return []


class ActionProcessDatabookFsUpload(Action):
    def name(self) -> str:
        return "action_process_databook_fs_upload"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        pdf_ids = payload.get("pdf_file_ids") or payload.get("fs_folder_file_ids") or []
        if isinstance(pdf_ids, str):
            pdf_ids = [pdf_ids]
        if not pdf_ids:
            dispatcher.utter_message(text="Please upload at least one PDF financial statement.")
            _utter_databook_fs_upload_card(dispatcher)
            return []

        fy_end_m, _ = _fy_end_month_day_from_tracker({}, tracker)
        body = {
            "session_id": _fdd_session_id(tracker),
            "pdf_file_ids": list(pdf_ids),
            "output_folder": tracker.get_slot("output_folder") or "",
            "fy_end_month": fy_end_m,
        }
        result = _fdd_post("/api/v1/fdd/run/databook/pdf-extraction", body, timeout=900)
        events = [SlotSet("db_fs_folder_file_ids", list(pdf_ids))]
        if result.get("success"):
            if not _utter_output_file_attachment(
                dispatcher,
                result,
                title="Financial statements (extracted)",
                tracker=tracker,
            ):
                dispatcher.utter_message(
                    text="Financial statements extracted. Please review the Excel file below."
                )
            _utter_databook_fs_review_upload_card(dispatcher)
        else:
            dispatcher.utter_message(
                text=f"PDF extraction error: {result.get('message', 'Unknown error')}"
            )
            _utter_databook_fs_upload_card(dispatcher)
        return events


class ActionProcessDatabookFsReviewUpload(Action):
    def name(self) -> str:
        return "action_process_databook_fs_review_upload"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        file_id = payload.get("fs_review_file_id") or payload.get("file_id")
        if not file_id:
            dispatcher.utter_message(text="Please upload the reviewed financial statements Excel.")
            _utter_databook_fs_review_upload_card(dispatcher)
            return []

        entity_names = list(tracker.get_slot("db_entity_names") or _db_entity_names_from_tracker(tracker))
        _utter_databook_recon_order_card(dispatcher, entity_names)
        return [SlotSet("db_fs_review_file_id", str(file_id))]


class ActionProcessDatabookReconOrder(Action):
    def name(self) -> str:
        return "action_process_databook_recon_order"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        entity_order = payload.get("db_recon_entity_order") or []
        if isinstance(entity_order, str):
            entity_order = [entity_order]
        if not entity_order:
            entity_names = list(
                tracker.get_slot("db_entity_names") or _db_entity_names_from_tracker(tracker)
            )
            _utter_databook_recon_order_card(dispatcher, entity_names)
            return []

        body = {
            "session_id": _fdd_session_id(tracker),
            "output_folder": tracker.get_slot("output_folder") or "",
            "master_path": tracker.get_slot("db_master_workbook_path") or "",
            "project_name": tracker.get_slot("project_name") or "Project",
            "company_name": tracker.get_slot("group_name") or "Group",
            "entity_order": list(entity_order),
            "l4_sort_basis": tracker.get_slot("db_l4_sort_basis") or "latest_fy",
        }
        result = _fdd_post("/api/v1/fdd/run/databook/recon-core", body, timeout=900)
        events = [SlotSet("db_recon_entity_order", list(entity_order))]
        if result.get("success"):
            if not _utter_output_file_attachment(
                dispatcher,
                result,
                title="Master databook (PL & BS reconciliation)",
                tracker=tracker,
            ):
                dispatcher.utter_message(
                    text="PL and BS reconciliation tables added to the master databook."
                )
            _utter_databook_extended_tables_card(dispatcher)
        else:
            dispatcher.utter_message(
                text=f"Reconciliation error: {result.get('message', 'Unknown error')}"
            )
            _utter_databook_recon_order_card(
                dispatcher,
                list(
                    tracker.get_slot("db_entity_names") or _db_entity_names_from_tracker(tracker)
                ),
            )
        return events


class ActionProcessDatabookExtendedTables(Action):
    def name(self) -> str:
        return "action_process_databook_extended_tables"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        choice = str(payload.get("db_create_extended_tables") or "").strip().lower()
        if choice not in ("yes", "no"):
            dispatcher.utter_message(text="Please choose Yes or No.")
            _utter_databook_extended_tables_card(dispatcher)
            return []

        if choice == "no":
            dispatcher.utter_message(json_message=_build_output_type_card())
            return [SlotSet("db_create_extended_tables", choice)]

        body = {
            "session_id": _fdd_session_id(tracker),
            "output_folder": tracker.get_slot("output_folder") or "",
            "master_path": tracker.get_slot("db_master_workbook_path") or "",
            "project_name": tracker.get_slot("project_name") or "Project",
            "company_name": tracker.get_slot("group_name") or "Group",
            "entity_order": tracker.get_slot("db_recon_entity_order") or [],
            "l4_sort_basis": tracker.get_slot("db_l4_sort_basis") or "latest_fy",
        }
        result = _fdd_post("/api/v1/fdd/run/databook/extended-tables", body, timeout=1200)
        events = [SlotSet("db_create_extended_tables", choice)]
        if result.get("success"):
            if not _utter_output_file_attachment(
                dispatcher,
                result,
                title="Master databook (extended tables)",
                tracker=tracker,
            ):
                dispatcher.utter_message(text="Extended tables added to the master databook.")
            _utter_databook_master_next_action_card(dispatcher)
        else:
            dispatcher.utter_message(
                text=f"Extended tables error: {result.get('message', 'Unknown error')}"
            )
            _utter_databook_extended_tables_card(dispatcher)
        return events


class ActionShowDatabookExtendedTables(Action):
    def name(self) -> str:
        return "action_show_databook_extended_tables"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        _utter_databook_extended_tables_card(dispatcher)
        return []


class ActionProcessDatabookMasterNextAction(Action):
    def name(self) -> str:
        return "action_process_databook_master_next_action"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        choice = str(payload.get("db_master_next_action") or "").strip().lower()
        if choice == "fs_extract":
            _utter_databook_fs_extract_placeholder_card(dispatcher)
            return [SlotSet("db_master_next_action", choice)]
        if choice == "another_output":
            dispatcher.utter_message(json_message=_build_output_type_card())
            return [SlotSet("db_master_next_action", choice)]

        dispatcher.utter_message(text="Please choose a next step.")
        _utter_databook_master_next_action_card(dispatcher)
        return []


class ActionShowDatabookMasterNextAction(Action):
    def name(self) -> str:
        return "action_show_databook_master_next_action"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        _utter_databook_master_next_action_card(dispatcher)
        return []


class ActionProcessDatabookReconLabels(Action):
    def name(self) -> str:
        return "action_process_databook_recon_labels"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        display_titles = {
            "aggregated_title": payload.get("recon_title_aggregated") or "Aggregated",
            "consolidation_title": payload.get("recon_title_consolidation") or "Consolidation",
            "difference_title": payload.get("recon_title_difference") or "Difference",
            "financial_statements_title": payload.get("recon_title_financial_statements") or "Financial statements",
            "ic_display_name": payload.get("recon_title_ic") or "IC eliminations",
        }
        body = {
            "session_id": _fdd_session_id(tracker),
            "output_folder": tracker.get_slot("output_folder") or "",
            "master_path": tracker.get_slot("db_master_workbook_path") or "",
            "project_name": tracker.get_slot("project_name") or "Project",
            "company_name": tracker.get_slot("group_name") or "Group",
            "entity_order": tracker.get_slot("db_recon_entity_order") or [],
            "display_titles": display_titles,
            "fs_review_file_id": tracker.get_slot("db_fs_review_file_id") or "",
            "show_fs_check": False,
            "l4_sort_basis": tracker.get_slot("db_l4_sort_basis") or "latest_fy",
        }
        result = _fdd_post("/api/v1/fdd/run/databook/recon-pl", body, timeout=600)
        events = [SlotSet("db_recon_display_titles", json.dumps(display_titles))]
        if result.get("success"):
            if not _utter_output_file_attachment(
                dispatcher,
                result,
                title="Master databook (PL reconciliation)",
                tracker=tracker,
            ):
                dispatcher.utter_message(
                    text="PL reconciliation table added to the master databook."
                )
        else:
            dispatcher.utter_message(
                text=f"Reconciliation error: {result.get('message', 'Unknown error')}"
            )
            _utter_databook_recon_labels_card(dispatcher)
        return events


class ActionProcessDatabookSort(Action):
    def name(self) -> str:
        return "action_process_databook_sort"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        sort_mode = payload.get("db_sort_mode", "total_assets_desc")
        entity_order = payload.get("db_entity_order", [])

        if sort_mode == "custom" and not entity_order:
            entity_names = tracker.get_slot("db_entity_names") or []
            if entity_names:
                dispatcher.utter_message(
                    json_message={
                        "type": "adaptive_card",
                        "card": "databook_sort",
                        "title": "Custom Entity Order",
                        "subtitle": "Assign a position (1 = first) to each entity.",
                        "inputs": [
                            {"id": f"order_{name}", "type": "number",
                             "label": f"Position for {name}",
                             "placeholder": str(i + 1), "default": str(i + 1)}
                            for i, name in enumerate(entity_names)
                        ],
                        "submit_label": "Confirm Order",
                    }
                )
                return [SlotSet("db_sort_mode", sort_mode)]

        return [
            SlotSet("db_sort_mode", sort_mode),
            SlotSet("db_entity_order", entity_order),
            FollowupAction("action_run_databook_final"),
        ]


class ActionRunDatabookFinal(Action):
    def name(self) -> str:
        return "action_run_databook_final"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        s = tracker.slots
        ap_digits = s.get("db_ap_ar_account_digits")
        ap_remove = None
        if ap_digits not in (None, ""):
            try:
                ap_remove = int(float(ap_digits))
            except (ValueError, TypeError):
                ap_remove = None

        body = {
            "session_id": _fdd_session_id(tracker),
            "entity_files": s.get("db_entity_files") or [],
            "entity_year_files": s.get("db_entity_year_files") or s.get("db_entity_files") or [],
            "entity_names": s.get("db_entity_names") or [],
            "output_folder": s.get("output_folder") or "",
            "sort_mode": s.get("db_sort_mode") or "total_assets_desc",
            "entity_order": s.get("db_entity_order") or [],
            "layout_format": s.get("db_susa_layout_format") or "",
            "value_type": s.get("db_susa_value_type") or "",
            "sign_mode": s.get("db_susa_sign_mode") or "",
            "ap_ar_included": s.get("db_susa_ap_ar_included"),
            "ap_ar_remove_account_length": ap_remove,
            "column_mapping": s.get("db_susa_column_mapping"),
        }
        result = _fdd_post("/api/v1/fdd/run/databook", body)

        if result.get("success"):
            if not _utter_output_file_attachment(
                dispatcher,
                result,
                title="Databook output",
                tracker=tracker,
            ):
                dispatcher.utter_message(
                    text="Databook outputs created successfully. Use the download button below."
                )
        else:
            dispatcher.utter_message(
                text=f"Databook error:\n{result.get('message', 'Unknown error')}"
            )
        return []
