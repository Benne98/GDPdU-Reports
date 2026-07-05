"""Fixed-asset report detail — per Bilanzposition assets + key-driver narratives."""

from __future__ import annotations



from datetime import date

from typing import Any, Optional



from app.services.fixed_asset_dimensions import make_predicate, row_dim_display, format_asset_description

from app.services.fixed_asset_report_narrative import build_rollforward_narrative

from app.services.fixed_asset_rollforward import (

    _amounts_for_group,

    _build_col_keys,

    _fetch_rows,

    _ordered_years,

)





def build_report_detail(

    session,

    *,

    anchor_date: date,

    compare_dates: list[date],

    entity: Optional[str] = None,

    project_id: str = "default",

    use_llm: bool = True,

) -> dict[str, Any]:

    years = _ordered_years(anchor_date, compare_dates)

    col_keys, col_labels = _build_col_keys(years)

    rows_by_year: dict[int, list[dict[str, Any]]] = {

        y: _fetch_rows(session, date(y, 12, 31), entity, project_id) for y in years

    }

    anchor_rows = rows_by_year.get(anchor_date.year, [])

    positions = sorted({

        str(r.get("bilanzposition") or "").strip() or "Unassigned"

        for r in anchor_rows

    })



    position_blocks: list[dict[str, Any]] = []



    for pos in positions:

        pred = make_predicate([("bilanzposition", pos)])

        assets_anchor = [r for r in anchor_rows if pred(r)]

        assets_anchor.sort(key=lambda r: str(r.get("asset_id") or ""))



        asset_rows: list[dict[str, Any]] = []

        for rec in assets_anchor:

            aid = str(rec.get("asset_id") or "")

            asset_pred = lambda r, p=pos, a=aid: (

                str(r.get("bilanzposition") or "").strip() == p

                and str(r.get("asset_id") or "") == a

            )

            asset_rows.append({

                "asset_id": aid,

                "asset_label": str(rec.get("asset_label") or "").strip(),

                "label": format_asset_description(aid, str(rec.get("asset_label") or "").strip()),

                "amounts": _amounts_for_group(rows_by_year, years, asset_pred),

            })



        position_blocks.append({

            "id": f"pos-{pos}",

            "bilanzposition": pos,

            "amounts": _amounts_for_group(rows_by_year, years, pred),

            "assets": asset_rows,

        })



    total_amounts = _amounts_for_group(rows_by_year, years, lambda r: True)

    narrative = build_rollforward_narrative(

        position_blocks,

        anchor_date=anchor_date,

        years=years,

        total_amounts=total_amounts,

        use_llm=use_llm,

    )



    return {

        "anchor_date": anchor_date.isoformat(),

        "col_keys": col_keys,

        "col_labels": col_labels,

        "positions": position_blocks,

        "total": {

            "label": "Fixed assets",

            "amounts": total_amounts,

        },

        "narrative": narrative,

        "intro": narrative["intro"],

        "bullets": [b["text"] for b in narrative["bullets"]],

    }

