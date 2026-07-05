"""Payroll accounting table — flat, column-split, or row-hierarchy layouts."""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.fin_compat_pl import build_pl_annual_compat
from app.services.fin_compat_sql import resolve_entity_prefix
from app.services.personnel_calc import (
    METRIC_CATALOG,
    aggregate_metric,
    personnel_pct_of_output,
)
from app.services.personnel_dimensions import (
    DIMENSION_IDS,
    dimension_label,
    make_predicate,
    parse_dimensions,
    row_dim_value,
)
from app.services.personnel_report_narrative import build_payroll_narrative

_TABLE_METRICS = ("fte", "payroll", "avg_cost_per_fte")
_FOOTER_METRICS = ("personnel_expenses", "sozialversicherung", "personnel_pct_output")

# Render order (top-to-bottom) of the accounting table sections:
#   Average FTEs -> Avg cost/FTE -> Payroll accounting -> footer KPIs.
# PayrollAccountingTable/Report render rows in the order received, so this list
# is the single source of truth for section ordering.
_DEFAULT_METRICS = [
    "fte", "avg_cost_per_fte", "payroll", "personnel_expenses", "personnel_pct_output",
]


def _snapshot_label(d: date) -> str:
    return f"FY{str(d.year)[-2:]}A"


def _col_label(d: date) -> str:
    return _snapshot_label(d)


def list_snapshots(session: Session, *, project_id: str = "default") -> list[dict[str, Any]]:
    rows = session.execute(
        text("""
            SELECT DISTINCT as_of_date
            FROM fact_personnel_employee
            WHERE project_id = :pid
            ORDER BY as_of_date DESC
        """),
        {"pid": project_id},
    ).fetchall()
    return [
        {"as_of_date": r[0].isoformat(), "label": _snapshot_label(r[0]), "col_label": _col_label(r[0])}
        for r in rows
    ]


def _fetch_rows(
    session: Session,
    as_of: date,
    entity: Optional[str],
    project_id: str = "default",
) -> list[dict[str, Any]]:
    ep = resolve_entity_prefix(session, entity)
    sql = """
        SELECT *
        FROM fact_personnel_employee
        WHERE project_id = :pid AND as_of_date = :as_of
    """
    params: dict[str, Any] = {"pid": project_id, "as_of": as_of}
    if ep:
        sql += " AND entity_prefix = :ep"
        params["ep"] = ep
    result = session.execute(text(sql), params)
    return [dict(r._mapping) for r in result.fetchall()]


def _total_output_keur(session: Session, as_of: date, entity: Optional[str]) -> float:
    year, month = as_of.year, as_of.month
    try:
        pl = build_pl_annual_compat(session, year=year, month=month, entity=entity)
        for row in pl.get("rows") or []:
            if row.get("line_code") == "TOTAL_OUTPUT":
                ytd = float((row.get("amounts") or {}).get("ytd") or 0)
                return ytd / 1000.0
    except Exception:
        pass
    return 0.0


def _ordered_dates(anchor: date, compare_dates: list[date]) -> list[date]:
    dates = sorted({anchor, *compare_dates})
    return dates


def _amounts_for_dates(
    rows_by_date: dict[date, list[dict[str, Any]]],
    col_dates: list[date],
    predicate,
    metric_id: str,
    col_keys: list[str],
) -> dict[str, float | None]:
    amounts: dict[str, float | None] = {}
    for d in col_dates:
        sub = [r for r in rows_by_date.get(d, []) if predicate(r)]
        val = aggregate_metric(sub, metric_id)
        iso = d.isoformat()
        if iso in col_keys:
            amounts[iso] = val
        for ck in col_keys:
            if ck.startswith(f"{iso}|"):
                dim_part = ck.split("|", 2)
                if len(dim_part) == 3:
                    _, _dim, key = dim_part
                    filtered = [r for r in sub if row_dim_value(r, _dim) == key]
                    amounts[ck] = aggregate_metric(filtered, metric_id)
    return amounts


def _dim_keys_for_dates(
    rows_by_date: dict[date, list[dict[str, Any]]],
    col_dates: list[date],
    dimension: str,
) -> list[str]:
    keys: set[str] = set()
    for d in col_dates:
        for r in rows_by_date.get(d, []):
            k = row_dim_value(r, dimension)
            if k and k != "—":
                keys.add(k)
    return sorted(keys)


def _build_flat_columns(col_dates: list[date]) -> tuple[list[str], dict[str, str], dict[str, str | None]]:
    col_keys = [d.isoformat() for d in col_dates]
    col_labels = {d.isoformat(): _col_label(d) for d in col_dates}
    col_groups = {k: None for k in col_keys}
    return col_keys, col_labels, col_groups


def _build_split_columns(
    col_dates: list[date],
    rows_by_date: dict[date, list[dict[str, Any]]],
    column_dimension: str,
) -> tuple[list[str], dict[str, str], dict[str, str | None]]:
    col_keys: list[str] = []
    col_labels: dict[str, str] = {}
    col_groups: dict[str, str | None] = {}
    for d in col_dates:
        fy = _col_label(d)
        dim_keys = _dim_keys_for_dates(rows_by_date, [d], column_dimension)
        for dk in dim_keys:
            ck = f"{d.isoformat()}|{column_dimension}|{dk}"
            col_keys.append(ck)
            col_labels[ck] = dk
            col_groups[ck] = fy
    return col_keys, col_labels, col_groups


def _walk_row_dimensions(
    rows_by_date: dict[date, list[dict[str, Any]]],
    col_dates: list[date],
    col_keys: list[str],
    dimensions: list[str],
    path: list[tuple[str, str]],
    depth: int,
    metric_id: str,
    unit: str,
    out: list[dict[str, Any]],
) -> None:
    if depth >= len(dimensions):
        return

    dim_id = dimensions[depth]
    is_last = depth == len(dimensions) - 1
    all_rows = [r for d in col_dates for r in rows_by_date.get(d, [])]
    keys = sorted({row_dim_value(r, dim_id) for r in all_rows if make_predicate(path)(r)})

    for key in keys:
        if not key or key == "—":
            continue
        new_path = path + [(dim_id, key)]
        pred = make_predicate(new_path)
        row_id = "-".join(f"{d}:{v}" for d, v in new_path)

        if not is_last:
            out.append({
                "id": f"{metric_id}-{row_id}",
                "label": key,
                "row_kind": "section_header",
                "unit": unit,
                "depth": depth,
                "dimension": dim_id,
            })
            _walk_row_dimensions(
                rows_by_date, col_dates, col_keys, dimensions, new_path, depth + 1,
                metric_id, unit, out,
            )
            out.append({
                "id": f"{metric_id}-{row_id}-sub",
                "label": f"Total {key}",
                "row_kind": "subtotal",
                "unit": unit,
                "depth": depth,
                "amounts": _amounts_for_dates(rows_by_date, col_dates, pred, metric_id, col_keys),
            })
        else:
            out.append({
                "id": f"{metric_id}-{row_id}",
                "label": key,
                "row_kind": "line",
                "unit": unit,
                "depth": depth,
                "dimension": dim_id,
                "amounts": _amounts_for_dates(rows_by_date, col_dates, pred, metric_id, col_keys),
            })


def _bereichs(rows: list[dict[str, Any]]) -> list[str]:
    return sorted({str(r.get("bereich") or "").strip() or "Unassigned" for r in rows})


def build_accounting_table(
    session: Session,
    *,
    anchor_date: date,
    compare_dates: list[date],
    entity: Optional[str] = None,
    layout: str = "flat",
    column_dimension: Optional[str] = None,
    row_dimensions: Optional[list[str]] = None,
    row_dimensions_csv: Optional[str] = None,
    dimension: Optional[str] = None,
    metrics: Optional[list[str]] = None,
    project_id: str = "default",
) -> dict[str, Any]:
    layout_mode = layout if layout in ("flat", "column_split", "row_hierarchy") else "flat"
    if dimension == "bereich" and layout == "flat" and not row_dimensions_csv:
        layout_mode = "flat"

    col_dim = column_dimension if column_dimension in DIMENSION_IDS else "bereich"
    row_dims = row_dimensions or parse_dimensions(row_dimensions_csv)
    if layout_mode == "flat":
        row_dims = ["bereich"]

    if metrics:
        requested = [m for m in metrics if m in [x["id"] for x in METRIC_CATALOG]]
        metric_ids = [m for m in _DEFAULT_METRICS if m in requested]
        metric_ids += [m for m in requested if m not in metric_ids]
    else:
        metric_ids = list(_DEFAULT_METRICS)
    col_dates = _ordered_dates(anchor_date, compare_dates)
    rows_by_date: dict[date, list[dict[str, Any]]] = {
        d: _fetch_rows(session, d, entity, project_id) for d in col_dates
    }

    if layout_mode == "column_split":
        col_keys, col_labels, col_groups = _build_split_columns(col_dates, rows_by_date, col_dim)
    else:
        col_keys, col_labels, col_groups = _build_flat_columns(col_dates)

    bereich_list = _bereichs(rows_by_date.get(anchor_date, []))
    if not bereich_list:
        for d in col_dates:
            bereich_list = _bereichs(rows_by_date.get(d, []))
            if bereich_list:
                break

    sections: list[dict[str, Any]] = []
    table_rows: list[dict[str, Any]] = []

    for metric_id in metric_ids:
        if metric_id not in [m["id"] for m in METRIC_CATALOG]:
            continue
        meta = next(m for m in METRIC_CATALOG if m["id"] == metric_id)
        is_kpi = metric_id == "personnel_pct_output"
        is_footer = metric_id in _FOOTER_METRICS

        if metric_id in _TABLE_METRICS or metric_id in ("praemie_per_fte", "grundgehalt"):
            section_id = metric_id
            sections.append({"id": section_id, "title": meta["label"], "kind": "metric_block"})
            table_rows.append({
                "id": f"{section_id}-hdr",
                "label": meta["label"],
                "row_kind": "section_header",
                "unit": meta["unit"],
            })

            if layout_mode == "row_hierarchy":
                _walk_row_dimensions(
                    rows_by_date, col_dates, col_keys, row_dims, [], 0,
                    section_id, meta["unit"], table_rows,
                )
            else:
                for lk in bereich_list:
                    pred = make_predicate([("bereich", lk)])
                    table_rows.append({
                        "id": f"{section_id}-{lk}",
                        "label": lk,
                        "row_kind": "line",
                        "unit": meta["unit"],
                        "amounts": _amounts_for_dates(
                            rows_by_date, col_dates, pred, metric_id, col_keys,
                        ),
                    })

            total_amounts = _amounts_for_dates(
                rows_by_date, col_dates, lambda r: True, metric_id, col_keys,
            )
            table_rows.append({
                "id": f"{section_id}-total",
                "label": "Total" if metric_id != "fte" else "Average FTEs #",
                "row_kind": "total",
                "unit": meta["unit"],
                "amounts": total_amounts,
            })

        elif is_footer:
            if metric_id == "personnel_pct_output":
                table_rows.append({
                    "id": "kpi-hdr",
                    "label": "KPIs — as % of total output",
                    "row_kind": "kpi_header",
                    "unit": "none",
                })
            amounts_f: dict[str, float | None] = {}
            for d in col_dates:
                all_rows = rows_by_date.get(d, [])
                iso = d.isoformat()
                if metric_id == "personnel_pct_output":
                    pe = aggregate_metric(all_rows, "personnel_expenses")
                    to = _total_output_keur(session, d, entity)
                    val = personnel_pct_of_output(pe, to)
                else:
                    val = aggregate_metric(all_rows, metric_id)
                if iso in col_keys:
                    amounts_f[iso] = val
                for ck in col_keys:
                    if ck.startswith(f"{iso}|"):
                        amounts_f[ck] = val
            table_rows.append({
                "id": metric_id,
                "label": meta["label"],
                "row_kind": "kpi" if is_kpi else "line",
                "unit": meta["unit"],
                "amounts": amounts_f,
            })

    prior_date = col_dates[-2] if len(col_dates) >= 2 else None
    narrative = build_payroll_narrative(
        anchor_date=anchor_date,
        prior_date=prior_date,
        col_dates=col_dates,
        rows_by_date=rows_by_date,
        bereich_list=bereich_list,
    )

    return {
        "anchor_date": anchor_date.isoformat(),
        "layout": layout_mode,
        "column_dimension": col_dim if layout_mode == "column_split" else None,
        "row_dimensions": row_dims if layout_mode == "row_hierarchy" else (["bereich"] if layout_mode == "flat" else row_dims),
        "col_keys": col_keys,
        "col_labels": col_labels,
        "col_groups": col_groups,
        "col_dates": [d.isoformat() for d in col_dates],
        "sections": sections,
        "rows": table_rows,
        "available_metrics": METRIC_CATALOG,
        "available_dimensions": [
            {"id": d, "label": dimension_label(d)} for d in DIMENSION_IDS
        ],
        "narrative": narrative,
        "intro": narrative["intro"] if narrative else None,
    }
