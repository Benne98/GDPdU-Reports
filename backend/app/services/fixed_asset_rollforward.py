"""Fixed-asset rollforward table — configurable dimension hierarchy (up to 3 levels)."""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.fin_compat_sql import resolve_entity_prefix
from app.services.fixed_asset_calc import aggregate_rows
from app.services.fixed_asset_dimensions import (
    DIMENSION_IDS,
    dimension_label,
    make_predicate,
    parse_dimensions,
    row_dim_display,
    row_dim_value,
)

def dec_label(d: date) -> str:
    return f"Dec{str(d.year)[-2:]}A"


def _col_key_year_end(y: int) -> str:
    return f"{y}-12-31"


def _col_key_move(y: int, kind: str) -> str:
    short = {"additions": "add", "disposals": "disp", "depreciation": "da"}[kind]
    return f"{y}-{short}"


def list_snapshots(session: Session, *, project_id: str = "default") -> list[dict[str, Any]]:
    rows = session.execute(
        text("""
            SELECT DISTINCT as_of_date
            FROM fact_fixed_asset
            WHERE project_id = :pid AND as_of_date IS NOT NULL
            ORDER BY as_of_date DESC
        """),
        {"pid": project_id},
    ).fetchall()
    return [
        {
            "as_of_date": r[0].isoformat(),
            "label": f"FY{str(r[0].year)[-2:]}A",
            "col_label": dec_label(r[0]),
        }
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
        FROM fact_fixed_asset
        WHERE project_id = :pid AND as_of_date = :as_of
    """
    params: dict[str, Any] = {"pid": project_id, "as_of": as_of}
    if ep:
        sql += " AND entity_prefix = :ep"
        params["ep"] = ep
    return [dict(r._mapping) for r in session.execute(text(sql), params).fetchall()]


def _ordered_years(anchor: date, compare_dates: list[date]) -> list[int]:
    return sorted({anchor.year, *(d.year for d in compare_dates)})


def _build_col_keys(years: list[int]) -> tuple[list[str], dict[str, str]]:
    keys: list[str] = []
    labels: dict[str, str] = {}
    for i, y in enumerate(years):
        if i == 0:
            k = _col_key_year_end(y)
            keys.append(k)
            labels[k] = f"Dec{str(y)[-2:]}A"
        else:
            for kind, lbl in (
                ("additions", "Add."),
                ("disposals", "Disp."),
                ("depreciation", "D&A"),
            ):
                k = _col_key_move(y, kind)
                keys.append(k)
                labels[k] = lbl
            k = _col_key_year_end(y)
            keys.append(k)
            labels[k] = f"Dec{str(y)[-2:]}A"
    return keys, labels


def _amounts_for_group(
    rows_by_year: dict[int, list[dict[str, Any]]],
    years: list[int],
    predicate,
) -> dict[str, float | None]:
    amounts: dict[str, float | None] = {}
    for i, y in enumerate(years):
        sub = [r for r in rows_by_year.get(y, []) if predicate(r)]
        if i == 0:
            amounts[_col_key_year_end(y)] = aggregate_rows(sub, "closing")
        else:
            amounts[_col_key_move(y, "additions")] = aggregate_rows(sub, "additions")
            amounts[_col_key_move(y, "disposals")] = aggregate_rows(sub, "disposals")
            amounts[_col_key_move(y, "depreciation")] = aggregate_rows(sub, "depreciation")
            amounts[_col_key_year_end(y)] = aggregate_rows(sub, "closing")
    return amounts


def _walk_dimensions(
    rows_by_year: dict[int, list[dict[str, Any]]],
    years: list[int],
    dimensions: list[str],
    path: list[tuple[str, str]],
    depth: int,
    out: list[dict[str, Any]],
) -> None:
    if depth >= len(dimensions):
        return

    dim_id = dimensions[depth]
    is_last = depth == len(dimensions) - 1
    all_rows = [r for y in years for r in rows_by_year.get(y, [])]
    keys = sorted({row_dim_value(r, dim_id) for r in all_rows if make_predicate(path)(r)})

    for key in keys:
        if not key or key == "—":
            continue
        new_path = path + [(dim_id, key)]
        pred = make_predicate(new_path)
        row_id = "-".join(f"{d}:{v}" for d, v in new_path)

        sample = next((r for y in years for r in rows_by_year.get(y, []) if pred(r)), None)
        label = row_dim_display(sample, dim_id) if sample and dim_id == "asset" else key
        group_amounts = _amounts_for_group(rows_by_year, years, pred)

        if not is_last:
            out.append({
                "id": row_id,
                "label": label,
                "row_kind": "section_header",
                "unit": "keur",
                "depth": depth,
                "dimension": dim_id,
                "amounts": group_amounts,
            })
            _walk_dimensions(rows_by_year, years, dimensions, new_path, depth + 1, out)
        else:
            out.append({
                "id": row_id,
                "label": label,
                "row_kind": "line",
                "unit": "keur",
                "depth": depth,
                "dimension": dim_id,
                "amounts": _amounts_for_group(rows_by_year, years, pred),
            })


def build_rollforward_table(
    session: Session,
    *,
    anchor_date: date,
    compare_dates: list[date],
    entity: Optional[str] = None,
    dimensions: Optional[list[str]] = None,
    dimensions_csv: Optional[str] = None,
    project_id: str = "default",
) -> dict[str, Any]:
    dims = dimensions or parse_dimensions(dimensions_csv)
    years = _ordered_years(anchor_date, compare_dates)
    col_keys, col_labels = _build_col_keys(years)
    rows_by_year: dict[int, list[dict[str, Any]]] = {
        y: _fetch_rows(session, date(y, 12, 31), entity, project_id) for y in years
    }

    table_rows: list[dict[str, Any]] = []
    _walk_dimensions(rows_by_year, years, dims, [], 0, table_rows)

    table_rows.append({
        "id": "fixed-assets-total",
        "label": "Fixed assets",
        "row_kind": "total",
        "unit": "keur",
        "depth": 0,
        "amounts": _amounts_for_group(rows_by_year, years, lambda r: True),
    })

    return {
        "anchor_date": anchor_date.isoformat(),
        "dimensions": dims,
        "col_keys": col_keys,
        "col_labels": col_labels,
        "rows": table_rows,
        "available_dimensions": [
            {"id": d, "label": dimension_label(d)} for d in DIMENSION_IDS
        ],
    }
