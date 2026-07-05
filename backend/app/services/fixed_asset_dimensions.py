"""Dimension helpers for fixed-asset rollforward grouping."""
from __future__ import annotations

from typing import Any, Callable

from app.services.entities import ENTITY_PREFIX

DIMENSION_IDS = ("bilanzposition", "segment", "entity", "asset")

_DIM_LABELS = {
    "bilanzposition": "Balance sheet line",
    "segment": "Business segment",
    "entity": "Entity",
    "asset": "Asset description",
}


def dimension_label(dim_id: str) -> str:
    return _DIM_LABELS.get(dim_id, dim_id)


def format_asset_description(asset_id: Any, asset_label: Any) -> str:
    """Display label: Anlage | Anlagenbezeichnung (asset_id | asset_label)."""
    aid = str(asset_id or "").strip()
    lbl = str(asset_label or "").strip()
    if aid and lbl:
        return f"{aid} | {lbl}"
    if aid:
        return aid
    if lbl:
        return lbl
    return "—"


def row_dim_value(rec: dict[str, Any], dim_id: str) -> str:
    if dim_id == "bilanzposition":
        return str(rec.get("bilanzposition") or "").strip() or "Unassigned"
    if dim_id == "segment":
        return str(rec.get("segment") or "").strip() or "Unassigned"
    if dim_id == "entity":
        ep = str(rec.get("entity_prefix") or "").strip()
        return ENTITY_PREFIX.get(ep, ep or "Unassigned")
    if dim_id == "asset":
        return str(rec.get("asset_id") or "").strip() or "—"
    return "Unassigned"


def row_dim_display(rec: dict[str, Any], dim_id: str) -> str:
    if dim_id == "asset":
        return format_asset_description(rec.get("asset_id"), rec.get("asset_label"))
    return row_dim_value(rec, dim_id)


def parse_dimensions(raw: str | None, *, max_levels: int = 3) -> list[str]:
    if not raw or not raw.strip():
        return ["bilanzposition", "segment"]
    dims = [d.strip() for d in raw.split(",") if d.strip()]
    out: list[str] = []
    for d in dims:
        if d in DIMENSION_IDS and d not in out:
            out.append(d)
        if len(out) >= max_levels:
            break
    return out or ["bilanzposition", "segment"]


def make_predicate(
    keys: list[tuple[str, str]],
) -> Callable[[dict[str, Any]], bool]:
    def pred(rec: dict[str, Any]) -> bool:
        for dim_id, val in keys:
            if row_dim_value(rec, dim_id) != val:
                return False
        return True
    return pred
