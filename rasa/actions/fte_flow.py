"""FTE Development chat flow v2 — adaptive cards and handlers."""
from __future__ import annotations

import json
from typing import Any

from rasa_sdk import Action, Tracker
from rasa_sdk.events import SlotSet
from rasa_sdk.executor import CollectingDispatcher

from actions.actions import (  # noqa: E402 — shared helpers from main actions module
    _as_of_year_month_from_ltm,
    _entity_year_files_from_groups,
    _fdd_post,
    _fdd_session_id,
    _first_preview_file_id,
    _fy_end_month_day_from_tracker,
    _headers_from_sources,
    _ltm_month_from_tracker,
    _parse_payload,
    _parse_susa_grid_file_groups,
    _remember_session_first_fy,
    _remember_session_headers,
    _sender_cache_key,
    _tracker_first_fy_int,
    _utter_output_file_attachment,
)

FTE_DEFAULT_ENTITY_NAMES: list[str] = [
    "Atlas",
    "Calypto",
    "Meridian",
    "Novara",
    "Venturo",
]

_SESSION_FTE_SETTINGS: dict[str, dict[str, Any]] = {}


def _fte_settings_from_tracker(tracker: Tracker) -> dict[str, Any]:
    key = _sender_cache_key(tracker) or ""
    return dict(_SESSION_FTE_SETTINGS.get(key, {}))


def _remember_fte_settings(tracker: Tracker, **kwargs: Any) -> None:
    key = _sender_cache_key(tracker) or ""
    if not key:
        return
    bucket = dict(_SESSION_FTE_SETTINGS.get(key, {}))
    for k, v in kwargs.items():
        if v is not None:
            bucket[k] = v
    _SESSION_FTE_SETTINGS[key] = bucket


def _tracker_last_fy_int(tracker: Tracker, default: int = 2025) -> int:
    from actions.actions import _last_completed_fy_end_calendar_year

    ltm_raw = _ltm_month_from_tracker(tracker)
    if not ltm_raw:
        return default
    ltm_y, ltm_m = _as_of_year_month_from_ltm(ltm_raw)
    fy_end_m, fy_end_d = _fy_end_month_day_from_tracker({}, tracker)
    return _last_completed_fy_end_calendar_year(ltm_y, ltm_m, fy_end_m, fy_end_d)


def _fte_fy_labels_from_settings(tracker: Tracker) -> list[str]:
    settings = _fte_settings_from_tracker(tracker)
    try:
        start = int(float(settings.get("fte_first_fy", _tracker_first_fy_int(tracker, default=2022))))
    except (TypeError, ValueError):
        start = _tracker_first_fy_int(tracker, default=2022)
    try:
        end = int(float(settings.get("fte_last_fy", _tracker_last_fy_int(tracker))))
    except (TypeError, ValueError):
        end = _tracker_last_fy_int(tracker)
    if end < start:
        start, end = end, start
    return [f"FY{y}" for y in range(start, end + 1)]


def _fte_grid_options(labels: list[str]) -> list[dict[str, str]]:
    return [{"label": lbl, "value": lbl} for lbl in labels]


def _fte_mapper_meta(tracker: Tracker, *, card_mode: str) -> dict[str, Any]:
    settings = _fte_settings_from_tracker(tracker)
    files = _fte_entity_year_files_from_tracker(tracker)
    preview_id = str(settings.get("fte_preview_file_id") or _first_preview_file_id(files) or "")
    return {
        "session_id": _fdd_session_id(tracker),
        "preview_file_id": preview_id,
        "card_mode": card_mode,
        "upload_mode": str(settings.get("fte_upload_mode") or "per_fy_grid"),
        "tenure_mode": str(settings.get("fte_tenure_mode") or "months_col"),
        "payroll_mode": str(settings.get("fte_payroll_mode") or "sum_components"),
        "fte_mapping": settings.get("fte_mapping") or {},
        "payroll_mapping": settings.get("fte_payroll_mapping") or {},
        "dimensions": settings.get("fte_dimensions") or [],
    }


def _fte_entity_year_files_from_tracker(tracker: Tracker) -> list[dict[str, Any]]:
    cached = _fte_settings_from_tracker(tracker).get("fte_entity_year_files")
    if isinstance(cached, list):
        return [r for r in cached if isinstance(r, dict)]
    raw_json = tracker.get_slot("fte_entity_year_files_json")
    if isinstance(raw_json, str) and raw_json.strip():
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, list):
                return [r for r in parsed if isinstance(r, dict)]
        except json.JSONDecodeError:
            pass
    single = tracker.get_slot("fte_single_file_id")
    if single:
        settings = _fte_settings_from_tracker(tracker)
        return [
            {
                "entity_index": 0,
                "entity_name": "Combined",
                "fy_label": "combined",
                "file_ids": [str(single)],
            }
        ]
    return []


def _missing_fte_grid_uploads(
    entity_year_files: list[dict[str, Any]],
    entity_count: int,
    required_labels: list[str],
    entity_names: list[Any],
    view_mode: str,
) -> list[str]:
    if view_mode == "consolidated":
        uploaded = {
            str(r.get("fy_label") or "")
            for r in entity_year_files
            if int(r.get("entity_index") or 0) == 0
        }
        return [lbl for lbl in required_labels if lbl not in uploaded]
    uploaded = set()
    for row in entity_year_files:
        uploaded.add((int(row.get("entity_index") or 0), str(row.get("fy_label") or "")))
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


def _utter_fte_entity_mode_card(dispatcher: CollectingDispatcher, tracker: Tracker) -> None:
    first_fy = _tracker_first_fy_int(tracker, default=2022)
    last_fy = _tracker_last_fy_int(tracker, default=first_fy)
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "fte_entity_mode",
            "title": "FTE Development — Scope",
            "subtitle": (
                "Configure the historical Actuals table (FYxxA). "
                "Choose how input files and output tables are organised."
            ),
            "inputs": [
                {
                    "id": "fte_input_layout",
                    "type": "radio",
                    "label": "Personaltable input layout",
                    "options": [
                        {
                            "label": "One workbook per fiscal year (and per entity if needed)",
                            "value": "per_fy_grid",
                        },
                        {
                            "label": "One combined workbook with all years",
                            "value": "single_combined_file",
                        },
                    ],
                },
                {
                    "id": "fte_view_mode",
                    "type": "radio",
                    "label": "Output table structure",
                    "options": [
                        {
                            "label": "Consolidated — one table, all entities combined",
                            "value": "consolidated",
                        },
                        {
                            "label": "Per entity — separate block per Entity",
                            "value": "per_entity",
                        },
                    ],
                },
                {
                    "id": "fte_first_fy",
                    "type": "number",
                    "label": "First historical FY",
                    "span": 2,
                    "default": str(int(first_fy)),
                },
                {
                    "id": "fte_last_fy",
                    "type": "number",
                    "label": "Last historical FY",
                    "span": 2,
                    "default": str(int(last_fy)),
                },
            ],
            "submit_label": "Continue",
        }
    )


def _utter_fte_entity_count_card(dispatcher: CollectingDispatcher, tracker: Tracker) -> None:
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "fte_entity_count",
            "title": "FTE Development — Entities",
            "subtitle": "How many entities? Use exact names from the Entity column in your files.",
            "inputs": [
                {
                    "id": "fte_entity_count",
                    "type": "number",
                    "label": "Number of entities",
                    "default": str(len(FTE_DEFAULT_ENTITY_NAMES)),
                }
            ],
            "submit_label": "Continue",
        }
    )


def _utter_fte_files_grid(dispatcher: CollectingDispatcher, tracker: Tracker, entity_count: int) -> None:
    labels = _fte_fy_labels_from_settings(tracker)
    settings = _fte_settings_from_tracker(tracker)
    view_mode = str(settings.get("fte_view_mode") or "consolidated")
    n = 1 if view_mode == "consolidated" else max(1, min(50, int(entity_count)))
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "fte_files",
            "title": "FTE Development — Upload personaltables",
            "subtitle": "Upload one personaltable .xlsx per fiscal year column.",
            "inputs": [
                {
                    "id": "susa_files",
                    "type": "susa_grid",
                    "label": "Personaltable files",
                    "options": _fte_grid_options(labels),
                    "entity_count": n,
                    "grid_mode": "single_file",
                }
            ],
            "susa_grid_note": "One workbook per FY (and per entity row when using per-entity output).",
            "submit_label": "Continue",
        }
    )


def _utter_fte_single_file_card(dispatcher: CollectingDispatcher) -> None:
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "fte_single_file",
            "title": "FTE Development — Combined personaltable",
            "subtitle": "Upload one workbook containing all fiscal years.",
            "inputs": [
                {
                    "id": "fte_combined_file",
                    "type": "file_drop",
                    "label": "Combined personaltable (.xlsx)",
                    "accept": ".xlsx,.xlsm",
                }
            ],
            "submit_label": "Continue",
        }
    )


def _utter_fte_fte_mapping_card(dispatcher: CollectingDispatcher, tracker: Tracker) -> None:
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "fte_fte_mapping",
            "title": "FTE Development — FTE calculation",
            "subtitle": "Map columns for average FTE calculation (whole numbers in output).",
            "mapper_meta": _fte_mapper_meta(tracker, card_mode="fte"),
            "submit_label": "Continue",
        }
    )


def _utter_fte_payroll_mapping_card(dispatcher: CollectingDispatcher, tracker: Tracker) -> None:
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "fte_payroll_mapping",
            "title": "FTE Development — Payroll accounting",
            "subtitle": "Map columns for personnel cost (payroll) per employee.",
            "mapper_meta": _fte_mapper_meta(tracker, card_mode="payroll"),
            "submit_label": "Continue",
        }
    )


def _utter_fte_dimensions_card(dispatcher: CollectingDispatcher, tracker: Tracker) -> None:
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "fte_dimensions",
            "title": "FTE Development — Dimensions",
            "subtitle": "Select up to 3 source columns to break down the table (with output labels).",
            "mapper_meta": _fte_mapper_meta(tracker, card_mode="dimensions"),
            "submit_label": "Continue",
        }
    )


def _utter_fte_metrics_card(dispatcher: CollectingDispatcher, tracker: Tracker) -> None:
    headers = _headers_from_sources(tracker)
    header_opts = [{"label": h, "value": h} for h in headers]
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "fte_metrics",
            "title": "FTE Development — Metrics",
            "subtitle": "Choose which metrics to include in the output table.",
            "inputs": [
                {
                    "id": "fte_preset_metrics",
                    "type": "multi_select",
                    "label": "Standard metrics",
                    "options": [
                        {"label": "Average FTEs # (whole numbers)", "value": "fte"},
                        {"label": "Payroll accounting (EURk)", "value": "payroll"},
                        {"label": "Average cost per FTE (EURk)", "value": "avg_cost_per_fte"},
                    ],
                    "default": ["fte", "payroll", "avg_cost_per_fte"],
                },
                {
                    "id": "fte_custom_metric_col",
                    "type": "dropdown",
                    "label": "Optional custom metric column (sum, EURk)",
                    "options": [{"label": "(none)", "value": ""}] + header_opts,
                    "required": False,
                },
                {
                    "id": "fte_custom_metric_label",
                    "type": "text",
                    "label": "Custom metric output label",
                    "placeholder": "e.g. Social Security",
                    "required": False,
                },
            ],
            "submit_label": "Continue",
        }
    )


def _utter_fte_pex_grid_card(dispatcher: CollectingDispatcher, tracker: Tracker) -> None:
    settings = _fte_settings_from_tracker(tracker)
    fy_labels = _fte_fy_labels_from_settings(tracker)
    view_mode = str(settings.get("fte_pex_view_mode") or settings.get("fte_view_mode") or "consolidated")
    entity_names = list(settings.get("fte_entity_names") or [])
    if view_mode == "consolidated":
        entity_names = ["Consolidated"]
    elif not entity_names:
        entity_names = FTE_DEFAULT_ENTITY_NAMES[: int(settings.get("fte_entity_count") or 1)]
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "fte_pex_grid",
            "title": "FTE Development — PEX from GL (EURk)",
            "subtitle": (
                "Enter personnel expenses from financial accounting per FY "
                "(manual reference for reconciliation)."
            ),
            "inputs": [
                {
                    "id": "fte_pex_view_mode",
                    "type": "radio",
                    "label": "PEX entry layout",
                    "options": [
                        {"label": "Consolidated — one row", "value": "consolidated"},
                        {"label": "Per entity — one row per entity", "value": "per_entity"},
                    ],
                    "default": view_mode,
                },
                {
                    "id": "fte_pex_values",
                    "type": "fte_pex_grid",
                    "label": "PEX values (EURk)",
                    "options": _fte_grid_options(fy_labels),
                    "entity_count": len(entity_names),
                    "default_entity_names": entity_names,
                },
            ],
            "submit_label": "Continue",
        }
    )


def _fte_review_lines(tracker: Tracker) -> str:
    s = _fte_settings_from_tracker(tracker)
    lines = [
        f"Input: **{s.get('fte_input_layout', 'per_fy_grid')}**",
        f"Output: **{s.get('fte_view_mode', 'consolidated')}**",
        f"FYs: **{', '.join(_fte_fy_labels_from_settings(tracker))}**",
        f"FTE mode: **{s.get('fte_tenure_mode', 'months_col')}**",
        f"Payroll mode: **{s.get('fte_payroll_mode', 'sum_components')}**",
        f"Dimensions: **{len(s.get('fte_dimensions') or [])}**",
        f"Metrics: **{', '.join(s.get('fte_preset_metrics') or [])}**",
    ]
    return "\n".join(lines)


def _utter_fte_review_card(dispatcher: CollectingDispatcher, tracker: Tracker) -> None:
    dispatcher.utter_message(
        json_message={
            "type": "adaptive_card",
            "card": "fte_review",
            "title": "FTE Development — Review",
            "review_text": _fte_review_lines(tracker),
            "inputs": [
                {
                    "id": "fte_confirm",
                    "type": "radio",
                    "label": "Ready to build the FTE table?",
                    "options": [
                        {"label": "Yes — create Excel output", "value": "yes"},
                        {"label": "Back — adjust PEX", "value": "back_pex"},
                        {"label": "Back — adjust dimensions", "value": "back_dimensions"},
                    ],
                }
            ],
            "submit_label": "Continue",
        }
    )


def _fte_after_upload(dispatcher: CollectingDispatcher, tracker: Tracker, payload: dict) -> list:
    """Cache headers and show FTE mapping after successful upload."""
    headers = payload.get("headers")
    if isinstance(headers, list) and headers:
        _remember_session_headers(tracker, headers)
    _utter_fte_fte_mapping_card(dispatcher, tracker)
    return []


class ActionProcessFteEntityMode(Action):
    def name(self) -> str:
        return "action_process_fte_entity_mode"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        try:
            first_fy = int(float(payload.get("fte_first_fy", 2022)))
            last_fy = int(float(payload.get("fte_last_fy", first_fy)))
        except (TypeError, ValueError):
            dispatcher.utter_message(text="Please enter valid fiscal years.")
            _utter_fte_entity_mode_card(dispatcher, tracker)
            return []
        if last_fy < first_fy:
            dispatcher.utter_message(text="Last FY must be on or after first FY.")
            _utter_fte_entity_mode_card(dispatcher, tracker)
            return []

        input_layout = str(payload.get("fte_input_layout") or "per_fy_grid")
        view_mode = str(payload.get("fte_view_mode") or "consolidated")
        _remember_fte_settings(
            tracker,
            fte_first_fy=first_fy,
            fte_last_fy=last_fy,
            fte_input_layout=input_layout,
            fte_upload_mode=input_layout,
            fte_view_mode=view_mode,
        )
        _remember_session_first_fy(tracker, first_fy)

        events = [
            SlotSet("fte_first_fy", float(first_fy)),
            SlotSet("fte_last_fy", float(last_fy)),
            SlotSet("fte_view_mode", view_mode),
            SlotSet("fte_input_layout", input_layout),
        ]
        if view_mode == "per_entity" and input_layout == "per_fy_grid":
            _utter_fte_entity_count_card(dispatcher, tracker)
            return events
        if input_layout == "single_combined_file":
            _utter_fte_single_file_card(dispatcher)
        else:
            n = 1 if view_mode == "consolidated" else len(FTE_DEFAULT_ENTITY_NAMES)
            _utter_fte_files_grid(dispatcher, tracker, entity_count=n)
        return events


class ActionProcessFteEntityCount(Action):
    def name(self) -> str:
        return "action_process_fte_entity_count"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        try:
            count = int(float(payload.get("fte_entity_count", len(FTE_DEFAULT_ENTITY_NAMES))))
        except (TypeError, ValueError):
            count = len(FTE_DEFAULT_ENTITY_NAMES)
        if count < 1 or count > 50:
            _utter_fte_entity_count_card(dispatcher, tracker)
            return []
        _remember_fte_settings(tracker, fte_entity_count=count)
        _utter_fte_files_grid(dispatcher, tracker, entity_count=count)
        return [SlotSet("fte_entity_count", float(count))]


class ActionProcessFteFiles(Action):
    def name(self) -> str:
        return "action_process_fte_files"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        settings = _fte_settings_from_tracker(tracker)
        view_mode = str(settings.get("fte_view_mode") or "consolidated")
        groups = _parse_susa_grid_file_groups(payload)
        entity_names: list = list(payload.get("entity_names") or [])
        entity_year_files = _entity_year_files_from_groups(groups, entity_names)
        if not entity_year_files:
            dispatcher.utter_message(text="Please upload at least one personaltable file.")
            return []

        entity_count = int(
            float(
                payload.get("fte_entity_count")
                or settings.get("fte_entity_count")
                or (1 if view_mode == "consolidated" else len(entity_names))
            )
        )
        required = _fte_fy_labels_from_settings(tracker)
        missing = _missing_fte_grid_uploads(
            entity_year_files, entity_count, required, entity_names, view_mode
        )
        if missing:
            dispatcher.utter_message(text=f"Missing uploads: {', '.join(missing)}.")
            _utter_fte_files_grid(dispatcher, tracker, entity_count=entity_count)
            return []

        serializable = [
            {
                "entity_index": r["entity_index"],
                "entity_name": r["entity_name"],
                "fy_label": r["fy_label"],
                "file_ids": r["file_ids"],
            }
            for r in entity_year_files
        ]
        preview_id = serializable[0]["file_ids"][0] if serializable else ""
        _remember_fte_settings(
            tracker,
            fte_entity_year_files=serializable,
            fte_entity_names=list(entity_names),
            fte_entity_count=entity_count,
            fte_preview_file_id=preview_id,
        )
        return _fte_after_upload(dispatcher, tracker, payload) + [
            SlotSet("fte_entity_year_files_json", json.dumps(serializable)),
        ]


class ActionProcessFteSingleFile(Action):
    def name(self) -> str:
        return "action_process_fte_single_file"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        fid = (
            payload.get("fte_combined_file")
            or payload.get("file_id")
            or payload.get("fte_combined_file_id")
        )
        if not fid:
            dispatcher.utter_message(text="Please upload the combined personaltable workbook.")
            _utter_fte_single_file_card(dispatcher)
            return []
        _remember_fte_settings(
            tracker,
            fte_single_file_id=str(fid),
            fte_preview_file_id=str(fid),
            fte_entity_year_files=[
                {
                    "entity_index": 0,
                    "entity_name": "Combined",
                    "fy_label": "combined",
                    "file_ids": [str(fid)],
                }
            ],
        )
        return _fte_after_upload(dispatcher, tracker, payload) + [
            SlotSet("fte_single_file_id", str(fid)),
        ]


class ActionProcessFteFteMapping(Action):
    def name(self) -> str:
        return "action_process_fte_fte_mapping"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        mapping = payload.get("fte_mapping") or payload.get("mapping") or {}
        tenure = str(
            mapping.get("tenure_mode")
            or payload.get("fte_tenure_mode")
            or "months_col"
        )
        _remember_fte_settings(
            tracker,
            fte_mapping=mapping,
            fte_tenure_mode=tenure,
        )
        _utter_fte_payroll_mapping_card(dispatcher, tracker)
        return [
            SlotSet("fte_tenure_mode", tenure),
            SlotSet("fte_mapping_json", json.dumps(mapping)),
        ]


class ActionProcessFtePayrollMapping(Action):
    def name(self) -> str:
        return "action_process_fte_payroll_mapping"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        mapping = payload.get("fte_payroll_mapping") or payload.get("mapping") or {}
        mode = str(mapping.get("payroll_mode") or payload.get("fte_payroll_mode") or "sum_components")
        _remember_fte_settings(
            tracker,
            fte_payroll_mapping=mapping,
            fte_payroll_mode=mode,
        )
        _utter_fte_dimensions_card(dispatcher, tracker)
        return [
            SlotSet("fte_payroll_mode", mode),
            SlotSet("fte_payroll_mapping_json", json.dumps(mapping)),
        ]


class ActionProcessFteDimensions(Action):
    def name(self) -> str:
        return "action_process_fte_dimensions"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        dims = payload.get("fte_dimensions") or payload.get("dimensions") or []
        if not isinstance(dims, list) or not dims:
            dispatcher.utter_message(text="Please select at least one dimension column.")
            _utter_fte_dimensions_card(dispatcher, tracker)
            return []
        if len(dims) > 3:
            dispatcher.utter_message(text="Maximum 3 dimensions allowed.")
            _utter_fte_dimensions_card(dispatcher, tracker)
            return []
        _remember_fte_settings(tracker, fte_dimensions=dims)
        _utter_fte_metrics_card(dispatcher, tracker)
        return [SlotSet("fte_dimensions_json", json.dumps(dims))]


class ActionProcessFteMetrics(Action):
    def name(self) -> str:
        return "action_process_fte_metrics"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        metrics = payload.get("fte_preset_metrics") or ["fte", "payroll"]
        if isinstance(metrics, str):
            metrics = [metrics]
        if not metrics:
            dispatcher.utter_message(text="Select at least one metric.")
            _utter_fte_metrics_card(dispatcher, tracker)
            return []
        custom_col = str(payload.get("fte_custom_metric_col") or "").strip()
        custom_label = str(payload.get("fte_custom_metric_label") or "").strip()
        custom = []
        if custom_col and custom_label:
            custom = [{"source_col": custom_col, "output_label": custom_label}]
        _remember_fte_settings(
            tracker,
            fte_preset_metrics=list(metrics),
            fte_custom_metrics=custom,
        )
        _utter_fte_pex_grid_card(dispatcher, tracker)
        return [
            SlotSet("fte_preset_metrics", json.dumps(list(metrics))),
            SlotSet("fte_custom_metrics_json", json.dumps(custom)),
        ]


class ActionProcessFtePexGrid(Action):
    def name(self) -> str:
        return "action_process_fte_pex_grid"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        pex_view = str(payload.get("fte_pex_view_mode") or "consolidated")
        pex_values = payload.get("fte_pex_values") or payload.get("pex_values") or {}
        _remember_fte_settings(
            tracker,
            fte_pex_view_mode=pex_view,
            fte_pex_values=pex_values,
        )
        _utter_fte_review_card(dispatcher, tracker)
        return [
            SlotSet("fte_pex_view_mode", pex_view),
            SlotSet("fte_pex_values_json", json.dumps(pex_values)),
        ]


class ActionShowFteReview(Action):
    def name(self) -> str:
        return "action_show_fte_review"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        payload = _parse_payload(tracker)
        choice = str(payload.get("fte_confirm") or "yes").lower()
        if choice == "back_pex":
            _utter_fte_pex_grid_card(dispatcher, tracker)
            return []
        if choice == "back_dimensions":
            _utter_fte_dimensions_card(dispatcher, tracker)
            return []
        return ActionRunFteDevelopmentFinal().run(dispatcher, tracker, domain)


class ActionRunFteDevelopmentFinal(Action):
    def name(self) -> str:
        return "action_run_fte_development_final"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict) -> list:
        settings = _fte_settings_from_tracker(tracker)
        session_id = _fdd_session_id(tracker)
        entity_year_files = _fte_entity_year_files_from_tracker(tracker)
        if not entity_year_files:
            dispatcher.utter_message(text="No personaltable uploads found. Please start again.")
            _utter_fte_entity_mode_card(dispatcher, tracker)
            return []

        fy_end_m, fy_end_d = _fy_end_month_day_from_tracker({}, tracker)
        body = {
            "session_id": session_id,
            "entity_year_files": entity_year_files,
            "entity_names": list(settings.get("fte_entity_names") or []),
            "output_folder": tracker.get_slot("output_folder") or "",
            "view_mode": str(settings.get("fte_view_mode") or "consolidated"),
            "upload_mode": str(settings.get("fte_upload_mode") or "per_fy_grid"),
            "first_fy": int(float(settings.get("fte_first_fy", _tracker_first_fy_int(tracker, 2022)))),
            "last_fy": int(float(settings.get("fte_last_fy", _tracker_last_fy_int(tracker)))),
            "fte_mapping": dict(settings.get("fte_mapping") or {}),
            "fte_tenure_mode": str(settings.get("fte_tenure_mode") or "months_col"),
            "payroll_mapping": dict(settings.get("fte_payroll_mapping") or {}),
            "fte_payroll_mode": str(settings.get("fte_payroll_mode") or "sum_components"),
            "dimensions": list(settings.get("fte_dimensions") or []),
            "preset_metrics": list(settings.get("fte_preset_metrics") or ["fte", "payroll"]),
            "custom_metrics": list(settings.get("fte_custom_metrics") or []),
            "pex_view_mode": str(settings.get("fte_pex_view_mode") or "consolidated"),
            "pex_values": dict(settings.get("fte_pex_values") or {}),
            "fy_end_month": fy_end_m,
            "fy_end_day": int(fy_end_d),
            "formula_mode": True,
        }
        dispatcher.utter_message(text="Building the FTE table — this may take a moment.")
        result = _fdd_post("/api/v1/fdd/run/fte_development", body)
        if result.get("success"):
            if not _utter_output_file_attachment(
                dispatcher, result, title="FTE Development output", tracker=tracker
            ):
                dispatcher.utter_message(text="FTE table created. Use the download button below.")
        else:
            dispatcher.utter_message(
                text=f"FTE Development error:\n{result.get('message', 'Unknown error')}"
            )
        return []


def utter_fte_start(dispatcher: CollectingDispatcher, tracker: Tracker) -> None:
    """Entry from Select Output — intro text + scope card inline."""
    dispatcher.utter_message(
        text=(
            "Starting **FTE Development** — configure FTE calculation, payroll mapping, "
            "dimensions, and metrics from your personaltable workbooks."
        )
    )
    _utter_fte_entity_mode_card(dispatcher, tracker)


# Legacy alias for action_run_fte_development
def utter_fte_scope_card(dispatcher: CollectingDispatcher, tracker: Tracker) -> None:
    _utter_fte_entity_mode_card(dispatcher, tracker)
