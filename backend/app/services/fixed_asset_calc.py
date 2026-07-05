"""Fixed-asset rollforward metric calculations (EURk)."""
from __future__ import annotations

from typing import Any


def to_keur(eur: float) -> float:
    return round(float(eur or 0) / 1000.0, 1)


def row_opening_nbv(rec: dict[str, Any]) -> float:
    return float(rec.get("opening_nbv") or 0)


def row_additions(rec: dict[str, Any]) -> float:
    return float(rec.get("additions_zugang") or 0)


def row_disposals(rec: dict[str, Any]) -> float:
    return float(rec.get("disposals_abgang") or 0)


def row_depreciation(rec: dict[str, Any]) -> float:
    return abs(float(rec.get("depreciation") or 0))


def row_closing_nbv(rec: dict[str, Any]) -> float:
    return float(rec.get("nbv") or 0)


def aggregate_rows(rows: list[dict[str, Any]], metric: str) -> float:
    if metric == "opening":
        return to_keur(sum(row_opening_nbv(r) for r in rows))
    if metric == "additions":
        return to_keur(sum(row_additions(r) for r in rows))
    if metric == "disposals":
        return to_keur(sum(row_disposals(r) for r in rows))
    if metric == "depreciation":
        return to_keur(sum(row_depreciation(r) for r in rows))
    if metric == "closing":
        return to_keur(sum(row_closing_nbv(r) for r in rows))
    return 0.0


def closing_from_bridge(opening_keur: float, add: float, disp: float, da: float) -> float:
    """NBV rollforward: opening + additions − disposals − depreciation."""
    return round(opening_keur + add - disp - da, 1)
