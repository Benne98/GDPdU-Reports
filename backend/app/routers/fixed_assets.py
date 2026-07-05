"""Fixed-assets rollforward reporting API."""
from __future__ import annotations

from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth import User, current_user
from app.db import get_session
from app.services.fixed_asset_movements import build_movements_report
from app.services.fixed_asset_report import build_report_detail
from app.services.fixed_asset_rollforward import build_rollforward_table, list_snapshots

router = APIRouter(prefix="/api/v1/fixed-assets", tags=["fixed-assets"])


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _parse_dates_csv(value: Optional[str]) -> list[date]:
    if not value or not value.strip():
        return []
    return [_parse_date(p.strip()) for p in value.split(",") if p.strip()]


@router.get("/snapshots")
def fixed_assets_snapshots(
    _user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    return {"snapshots": list_snapshots(session)}


@router.get("/rollforward")
def fixed_assets_rollforward(
    _user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    anchor_date: str = Query(...),
    compare_dates: Optional[str] = Query(None),
    entity: Optional[str] = Query(None),
    dimensions: Optional[str] = Query(None, description="Comma-separated: bilanzposition,segment,entity,asset"),
    hierarchy: Optional[str] = Query(None, description="Deprecated: flat|two_level"),
) -> dict:
    dims_csv = dimensions
    if not dims_csv and hierarchy == "two_level":
        dims_csv = "segment,bilanzposition"
    elif not dims_csv and hierarchy == "flat":
        dims_csv = "bilanzposition"
    return build_rollforward_table(
        session,
        anchor_date=_parse_date(anchor_date),
        compare_dates=_parse_dates_csv(compare_dates),
        entity=entity,
        dimensions_csv=dims_csv,
    )


@router.get("/report-detail")
def fixed_assets_report_detail(
    _user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    anchor_date: str = Query(...),
    compare_dates: Optional[str] = Query(None),
    entity: Optional[str] = Query(None),
    use_llm: bool = Query(True, description="Polish narrative copy with Claude when API key is set"),
) -> dict:
    return build_report_detail(
        session,
        anchor_date=_parse_date(anchor_date),
        compare_dates=_parse_dates_csv(compare_dates),
        entity=entity,
        use_llm=use_llm,
    )


@router.get("/movements")
def fixed_assets_movements(
    _user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    anchor_date: str = Query(...),
    prior_date: Optional[str] = Query(None),
    entity: Optional[str] = Query(None),
    bridge_entity: Optional[str] = Query(None),
    bridge_anchor_date: Optional[str] = Query(None),
    bridge_prior_date: Optional[str] = Query(None),
    bridge_dimension: Optional[str] = Query(None),
    bridge_scope: Optional[str] = Query(None),
    bridge_extra_years: Optional[str] = Query(None, description="Comma-separated ISO dates"),
    category_prior_date: Optional[str] = Query(None),
    category_dimension: str = Query("bilanzposition"),
    add_disp_dimension: str = Query("entity"),
    section: Optional[str] = Query(
        None,
        description="Optional chart section: bridge, add_disp, or category (comma-separated). Omit for all.",
    ),
) -> dict:
    sections: set[str] | None = None
    if section and section.strip():
        sections = {p.strip() for p in section.split(",") if p.strip()}
    return build_movements_report(
        session,
        anchor_date=_parse_date(anchor_date),
        prior_date=_parse_date(prior_date) if prior_date else None,
        entity=entity,
        bridge_entity=bridge_entity,
        bridge_anchor_date=_parse_date(bridge_anchor_date) if bridge_anchor_date else None,
        bridge_prior_date=_parse_date(bridge_prior_date) if bridge_prior_date else None,
        bridge_dimension=bridge_dimension,
        bridge_scope=bridge_scope,
        bridge_extra_years=_parse_dates_csv(bridge_extra_years),
        category_prior_date=_parse_date(category_prior_date) if category_prior_date else None,
        category_dimension=category_dimension,
        add_disp_dimension=add_disp_dimension,
        sections=sections,
    )
