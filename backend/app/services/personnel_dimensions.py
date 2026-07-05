"""Dimension helpers for personnel / payroll reporting."""
from __future__ import annotations

from typing import Any, Callable

from app.services.entities import ENTITY_PREFIX

DIMENSION_IDS = ("entity", "org_unit", "gew_ang", "kst_name", "bereich")

_DIM_LABELS = {
    "entity": "Entity",
    "org_unit": "Organizational unit",
    "gew_ang": "Employee type",
    "kst_name": "Cost center",
    "bereich": "Division",
}


def dimension_label(dim_id: str) -> str:
    return _DIM_LABELS.get(dim_id, dim_id)


def row_dim_value(rec: dict[str, Any], dim_id: str) -> str:
    if dim_id == "entity":
        ep = str(rec.get("entity_prefix") or "").strip()
        return ENTITY_PREFIX.get(ep, ep or "Unassigned")
    if dim_id == "org_unit":
        return str(rec.get("bereichuntergruppe") or "").strip() or "Unassigned"
    if dim_id == "gew_ang":
        return str(rec.get("gew_ang") or "").strip() or "Unassigned"
    if dim_id == "kst_name":
        return str(rec.get("kst_name") or "").strip() or "Unassigned"
    if dim_id == "bereich":
        return str(rec.get("bereich") or "").strip() or "Unassigned"
    return "Unassigned"


def parse_dimensions(raw: str | None, *, max_levels: int = 3) -> list[str]:
    if not raw or not raw.strip():
        return ["bereich"]
    dims = [d.strip() for d in raw.split(",") if d.strip()]
    out: list[str] = []
    for d in dims:
        if d in DIMENSION_IDS and d not in out:
            out.append(d)
        if len(out) >= max_levels:
            break
    return out or ["bereich"]


def make_predicate(keys: list[tuple[str, str]]) -> Callable[[dict[str, Any]], bool]:
    def pred(rec: dict[str, Any]) -> bool:
        for dim_id, val in keys:
            if row_dim_value(rec, dim_id) != val:
                return False
        return True
    return pred
