"""Personnel / payroll reporting API."""

from __future__ import annotations



from datetime import date

from typing import Annotated, Optional



from fastapi import APIRouter, Depends, Query

from sqlalchemy.orm import Session



from app.auth import User, current_user

from app.db import get_session

from app.services.personnel_accounting import build_accounting_table, list_snapshots

from app.services.personnel_movements import build_movements_report



router = APIRouter(prefix="/api/v1/personnel", tags=["personnel"])





def _parse_date(value: str) -> date:

    return date.fromisoformat(value)





def _parse_dates_csv(value: Optional[str]) -> list[date]:

    if not value or not value.strip():

        return []

    return [_parse_date(p.strip()) for p in value.split(",") if p.strip()]





@router.get("/snapshots")

def personnel_snapshots(

    _user: Annotated[User, Depends(current_user)],

    session: Annotated[Session, Depends(get_session)],

) -> dict:

    return {"snapshots": list_snapshots(session)}





@router.get("/accounting")

def personnel_accounting(

    _user: Annotated[User, Depends(current_user)],

    session: Annotated[Session, Depends(get_session)],

    anchor_date: str = Query(..., description="ISO date of anchor snapshot"),

    compare_dates: Optional[str] = Query(None, description="Comma-separated ISO dates"),

    entity: Optional[str] = Query(None),

    layout: str = Query("flat", description="flat | column_split | row_hierarchy"),

    column_dimension: Optional[str] = Query(None),

    row_dimensions: Optional[str] = Query(None, description="Comma-separated dimension ids"),

    dimension: Optional[str] = Query(None, description="Deprecated — use layout/row_dimensions"),

    metrics: Optional[str] = Query(None, description="Comma-separated metric ids"),

) -> dict:

    metric_list = [m.strip() for m in metrics.split(",") if m.strip()] if metrics else None

    layout_mode = layout

    if dimension and dimension != "bereich" and layout == "flat":

        layout_mode = "row_hierarchy"

        row_dimensions = row_dimensions or dimension

    return build_accounting_table(

        session,

        anchor_date=_parse_date(anchor_date),

        compare_dates=_parse_dates_csv(compare_dates),

        entity=entity,

        layout=layout_mode,

        column_dimension=column_dimension,

        row_dimensions_csv=row_dimensions,

        metrics=metric_list,

    )





@router.get("/movements")

def personnel_movements(

    _user: Annotated[User, Depends(current_user)],

    session: Annotated[Session, Depends(get_session)],

    anchor_date: str = Query(...),

    prior_date: Optional[str] = Query(None),

    entity: Optional[str] = Query(None),

    chart_dimension: str = Query("bereich"),

    trend_metric: str = Query("fte", description="Metric id for the time-series chart"),

) -> dict:

    return build_movements_report(

        session,

        anchor_date=_parse_date(anchor_date),

        prior_date=_parse_date(prior_date) if prior_date else None,

        entity=entity,

        chart_dimension=chart_dimension,

        trend_metric=trend_metric,

    )

