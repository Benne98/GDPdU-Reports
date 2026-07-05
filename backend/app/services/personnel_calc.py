"""Pure personnel metric calculations (FTE / payroll) — shared by API and tests."""
from __future__ import annotations

from typing import Any

METRIC_CATALOG: list[dict[str, str]] = [
    {"id": "fte", "label": "Average FTEs #", "unit": "count"},
    {"id": "avg_cost_per_fte", "label": "Average cost per FTE", "unit": "keur"},
    {"id": "payroll", "label": "Payroll accounting", "unit": "keur"},
    {"id": "praemie_per_fte", "label": "Prämie per FTE", "unit": "keur"},
    {"id": "grundgehalt", "label": "Grundgehalt", "unit": "keur"},
    {"id": "sozialversicherung", "label": "Social Security", "unit": "keur"},
    {"id": "personnel_expenses", "label": "Personnel expenses", "unit": "keur"},
    {"id": "personnel_pct_output", "label": "Personnel expenses (% of total output)", "unit": "pct"},
]

# Numeric payroll components selectable in the metric trend chart (English labels).
TREND_METRIC_CATALOG: list[dict[str, str]] = [
    {"id": "fte", "label": "FTE", "unit": "count"},
    {"id": "payroll", "label": "Payroll accounting", "unit": "keur"},
    {"id": "avg_cost_per_fte", "label": "Average cost per FTE", "unit": "keur"},
    {"id": "praemie_per_fte", "label": "Premium per FTE", "unit": "keur"},
    {"id": "grundgehalt", "label": "Base salary", "unit": "keur"},
    {"id": "gehalt_mon", "label": "Monthly salary", "unit": "keur"},
    {"id": "praemie", "label": "Premiums", "unit": "keur"},
    {"id": "urlaubsgeld", "label": "Holiday pay", "unit": "keur"},
    {"id": "tzug", "label": "TZUG allowance", "unit": "keur"},
    {"id": "transformationsgeld", "label": "Transformation allowance", "unit": "keur"},
    {"id": "sozialversicherung", "label": "Social security", "unit": "keur"},
    {"id": "kontofuehrungsgebuehr", "label": "Account fees", "unit": "keur"},
    {"id": "schichtzulagen", "label": "Shift allowances", "unit": "keur"},
    {"id": "fahrgeldzuschuss", "label": "Mobility allowance", "unit": "keur"},
    {"id": "kosten_leihpersonal", "label": "Temporary staff costs", "unit": "keur"},
    {"id": "pausch_offene_verhandlungen", "label": "Open negotiation lump sum", "unit": "keur"},
    {"id": "tariferhoehungen", "label": "Collective agreement increases", "unit": "keur"},
]

_COMPONENT_KEUR_FIELDS = frozenset({
    "grundgehalt", "gehalt_mon", "praemie", "urlaubsgeld", "tzug", "transformationsgeld",
    "sozialversicherung", "kontofuehrungsgebuehr", "schichtzulagen", "fahrgeldzuschuss",
    "kosten_leihpersonal", "pausch_offene_verhandlungen", "tariferhoehungen",
})

_TREND_METRIC_IDS = frozenset(m["id"] for m in TREND_METRIC_CATALOG)


def row_fte(months_active: float, besch_pct: float) -> float:
    """Average FTE contribution of one employee over the fiscal year.

    Semantic (verified against personaltable.xlsx source):
      * ``months_active`` = source column "Summe" = COUNT of active months
        (sum of the 12 monthly 1/0 presence flags Januar..Dezember, range 1-12).
        It is NOT a EUR figure and NOT beschaeftigungsgrad-weighted.
      * ``besch_pct`` = "Beschaeftigungsgrad" = employment grade in percent
        (50..100), stored separately from the monthly flags.

    FTE = (months_active / 12) * (besch_pct / 100)

    Examples: 12 months @ 100% -> 1.0 FTE; 6 months @ 50% -> 0.25 FTE;
    1 month @ 100% -> 1/12 FTE. A missing besch_pct defaults to 100%.
    """
    return float(months_active or 0) * float(besch_pct or 100) / 100.0 / 12.0


def row_payroll_eur(rec: dict[str, Any]) -> float:
    """Sum payroll components in EUR (same as sum_components mode in FTE script)."""
    total = 0.0
    for key in (
        "grundgehalt", "praemie", "urlaubsgeld", "tzug", "transformationsgeld",
        "sozialversicherung", "kontofuehrungsgebuehr", "schichtzulagen",
        "fahrgeldzuschuss", "kosten_leihpersonal", "pausch_offene_verhandlungen",
        "tariferhoehungen",
    ):
        total += float(rec.get(key) or 0)
    gs = rec.get("gesamtsumme")
    if gs is not None and float(gs or 0) > 0:
        return float(gs)
    return total


def row_social_eur(rec: dict[str, Any]) -> float:
    return float(rec.get("sozialversicherung") or 0)


def aggregate_metric(
    rows: list[dict[str, Any]],
    metric_id: str,
) -> float:
    fte_sum = sum(row_fte(float(r.get("months_active") or 0), float(r.get("beschaeftigungsgrad") or 100)) for r in rows)
    payroll_eur = sum(row_payroll_eur(r) for r in rows)
    social_eur = sum(row_social_eur(r) for r in rows)
    praemie_eur = sum(float(r.get("praemie") or 0) for r in rows)
    grund_eur = sum(float(r.get("grundgehalt") or 0) for r in rows)

    if metric_id == "fte":
        return round(fte_sum)
    if metric_id == "payroll":
        return round(-payroll_eur / 1000.0, 2)
    if metric_id == "avg_cost_per_fte":
        if fte_sum <= 0:
            return 0.0
        return round(-payroll_eur / 1000.0 / fte_sum, 2)
    if metric_id == "praemie_per_fte":
        if fte_sum <= 0:
            return 0.0
        return round(-praemie_eur / 1000.0 / fte_sum, 2)
    if metric_id == "grundgehalt":
        return round(-grund_eur / 1000.0, 2)
    if metric_id == "sozialversicherung":
        return round(-social_eur / 1000.0, 2)
    if metric_id == "personnel_expenses":
        return round(-(payroll_eur) / 1000.0, 2)
    if metric_id in _COMPONENT_KEUR_FIELDS:
        total = sum(float(r.get(metric_id) or 0) for r in rows)
        return round(-total / 1000.0, 2)
    return 0.0


def trend_metric_ids() -> frozenset[str]:
    return _TREND_METRIC_IDS


def trend_metric_label(metric_id: str) -> str:
    for m in TREND_METRIC_CATALOG:
        if m["id"] == metric_id:
            return m["label"]
    return metric_id


def trend_metric_unit(metric_id: str) -> str:
    for m in TREND_METRIC_CATALOG:
        if m["id"] == metric_id:
            return m["unit"]
    return "keur"


def chart_display_value(metric_id: str, raw: float) -> float:
    """Positive display values for charts (FTE stays as-is; kEUR uses abs)."""
    if metric_id == "fte":
        return round(raw, 1)
    return round(abs(raw), 2)


def personnel_pct_of_output(personnel_expenses_keur: float, total_output_keur: float) -> float:
    """Personnel cost as % of total output — always a positive magnitude for KPI display."""
    if abs(total_output_keur) < 1e-6:
        return 0.0
    return round(abs(personnel_expenses_keur) / abs(total_output_keur) * 100.0, 1)
