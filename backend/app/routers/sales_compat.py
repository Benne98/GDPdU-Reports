"""Sales compatibility endpoints (GDPdU backend).

Implements the legacy sales API paths consumed by the verbatim-ported frontend:
  GET /api/v1/sales/top-entities   → SalesTopEntitiesResponse (api.salesTopEntities)

Values are reported in kEUR, matching the legacy routers/sales.py contract.  See
app/services/overview_top_entities.py for the FORMULA / WORKED EXAMPLE / EDGE
CASES and the documented kEUR scaling + posting_date period windows.
"""
from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import User, current_user
from app.db import get_read_session, get_session
from app.services.aging_scope import aging_scope as _aging_scope
from app.services.entity_visibility import visible_entity_codes
from app.services.overview_summary import _effective_prefixes, map_codes_to_prefixes
# Phase-2: aging + all aging-analytics endpoints are routed to the OPOS subledger
# as-of variants (Method A + FIFO, single-sourced through opos_aging). The legacy
# GL builders stay reachable via source="gl" on gl_aging.build_*_aging. The OPOS
# variants are imported under the legacy names so the endpoint bodies are unchanged.
from app.services.gl_aging import build_payables_aging, build_receivables_aging
from app.services.gl_aging_analytics import (
    build_concentration_trend_ap_opos as build_concentration_trend_ap,
    build_concentration_trend_ar_opos as build_concentration_trend_ar,
    build_payables_by_dimension_hierarchy_opos as build_payables_by_dimension_hierarchy,
    build_payables_by_dimension_opos as build_payables_by_dimension,
    build_payables_concentration_opos as build_payables_concentration,
    build_payables_dimension_chart_opos as build_payables_dimension_chart,
    build_payables_geo_country_locations_opos as build_payables_geo_country_locations,
    build_payables_geo_opos as build_payables_geo,
    build_payables_portfolio_table_opos as build_payables_portfolio_table,
    build_payables_supplier_documents_opos as build_payables_supplier_documents,
    build_payables_suppliers_opos as build_payables_suppliers,
    build_payables_trend_opos as build_payables_trend,
    build_receivables_by_dimension_hierarchy_opos as build_receivables_by_dimension_hierarchy,
    build_receivables_by_dimension_opos as build_receivables_by_dimension,
    build_receivables_concentration_opos as build_receivables_concentration,
    build_receivables_customer_documents_opos as build_receivables_customer_documents,
    build_receivables_customers_opos as build_receivables_customers,
    build_receivables_dimension_chart_opos as build_receivables_dimension_chart,
    build_receivables_geo_country_locations_opos as build_receivables_geo_country_locations,
    build_receivables_geo_opos as build_receivables_geo,
    build_receivables_portfolio_table_opos as build_receivables_portfolio_table,
    build_receivables_trend_opos as build_receivables_trend,
)
from app.services.overview_top_entities import build_top_entities
from app.services.profitability_compat import build_headline_kpis, build_top_orders
from app.services.sales_analytics_compat import (
    build_breakdown_table,
    build_churn_bridge,
    build_composition_breakdown,
    build_dimension_performance,
    build_geo_countries,
    build_geo_country_locations,
    build_geo_trend,
    build_gross_margin_matrix,
    build_gross_sales_trend,
    build_metric_bridge,
    build_profit_margin_scatter,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/sales", tags=["sales-compat"])

_UserDep = Annotated[User, Depends(current_user)]
_SessionDep = Annotated[Session, Depends(get_session)]
# Aging endpoints are heavy full-FY OPOS reads → bound each request with a scoped
# statement_timeout + serial plan (SAME pattern as financials_compat.py).
_ReadSessionDep = Annotated[Session, Depends(get_read_session)]


def _resolve_visibility(
    session: Session, user: User, entity: Optional[str],
) -> tuple[Optional[set[str]], Optional[str]]:
    """Fail-closed tenant boundary for the sales-analytics builders.

    Mirrors financials_compat.get_overview_partners: resolve ``visible_entity_codes``
    → ``entity_prefix`` set, intersect with the requested ``entity``.  Returns
    ``(allowed_entities, builder_entity)`` where:
      * admin/unrestricted → ``(None, entity or None)`` (legacy path),
      * scoped             → ``(non-empty set, None)`` (builder filters on the set),
      * denied / mapping failure for a non-admin → ``(set(), None)`` → the builder's
        own empty/zeroed shape (no cross-entity leak, no 500, no SQL).
    """
    allowed_codes = visible_entity_codes(session, user)
    try:
        allowed_prefixes = map_codes_to_prefixes(session, allowed_codes)
        eff, builder_entity, denied = _effective_prefixes(
            session, entity=entity, allowed_prefixes=allowed_prefixes,
        )
    except Exception:  # noqa: BLE001
        logger.exception("sales-analytics visibility mapping failed — failing closed")
        if allowed_codes is None:
            return None, (entity or None)
        return set(), None
    if denied:
        return set(), None
    return eff, builder_entity


@router.get("/top-entities")
def get_top_entities(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    type: str = Query("customer", description="customer | supplier"),
    rank_by: str = Query("cm", description="cm | ytd"),
    method: str = Query("invoiced", description="accepted for compat; only 'invoiced' is derived"),
    period_grain: str = Query("month", description="month | week (echoed; windows are monthly)"),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
    limit: int = Query(5000, ge=1, le=10000),
    entity: Optional[str] = Query(None),
) -> dict:
    """Top customers (fact_sales) or suppliers (fact_com) by descending CM/YTD (kEUR)."""
    try:
        return build_top_entities(
            session,
            year=year, month=month,
            type=type, rank_by=rank_by,
            period_grain=period_grain, limit=limit, entity=entity,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("sales/top-entities error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/headline-kpis")
def get_headline_kpis(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    period_grain: str = Query("month"),
    entity: Optional[str] = Query(None),
) -> dict:
    try:
        return build_headline_kpis(session, year, month, entity)
    except Exception as exc:  # noqa: BLE001
        logger.exception("sales/headline-kpis error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/top-orders")
def get_top_orders(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=200),
) -> list:
    try:
        return build_top_orders(session, year, month, entity, limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/receivables-aging")
def get_receivables_aging(
    _user: _UserDep,
    session: _ReadSessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
) -> dict:
    try:
        with _aging_scope(session, _user, entity) as ent:
            return build_receivables_aging(session, year, month, ent)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/payables-aging")
def get_payables_aging(
    _user: _UserDep,
    session: _ReadSessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
) -> dict:
    try:
        with _aging_scope(session, _user, entity) as ent:
            return build_payables_aging(session, year, month, ent)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ─── Receivables aging analytics (ported Sales UI) ─────────────────────────────

@router.get("/receivables-aging/portfolio-table")
def get_receivables_portfolio_table(
    _user: _UserDep,
    session: _ReadSessionDep,
    dimension: str = Query("customer"),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    rows_per_bucket: int = Query(25, ge=5, le=100),
) -> dict:
    with _aging_scope(session, _user, entity) as ent:
        return build_receivables_portfolio_table(session, dimension, year, month, ent, rows_per_bucket)


@router.get("/receivables-aging/by-dimension")
def get_receivables_by_dimension(
    _user: _UserDep,
    session: _ReadSessionDep,
    dimension: str = Query("customer"),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    limit: int = Query(50, ge=5, le=200),
    view: str = Query("buckets"),
) -> dict:
    with _aging_scope(session, _user, entity) as ent:
        return build_receivables_by_dimension(session, dimension, year, month, ent, limit, view)  # type: ignore[arg-type]


@router.get("/receivables-aging/by-dimension-hierarchy")
def get_receivables_by_dimension_hierarchy(
    _user: _UserDep,
    session: _ReadSessionDep,
    hierarchy: str = Query("entity,customer"),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    limit: int = Query(500, ge=10, le=2000),
    view: str = Query("buckets"),
    compare_pm: bool = Query(False),
    compare_py: bool = Query(False),
) -> dict:
    with _aging_scope(session, _user, entity) as ent:
        return build_receivables_by_dimension_hierarchy(
            session, hierarchy, year, month, ent, limit, view, compare_pm, compare_py,  # type: ignore[arg-type]
        )


@router.get("/receivables-aging/concentration")
def get_receivables_concentration(
    _user: _UserDep,
    session: _ReadSessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
) -> dict:
    with _aging_scope(session, _user, entity) as ent:
        return build_receivables_concentration(session, year, month, ent)


@router.get("/receivables-aging/concentration/trend")
def get_receivables_concentration_trend(
    _user: _UserDep,
    session: _ReadSessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    periods_back: int = Query(12, ge=3, le=24),
) -> dict:
    with _aging_scope(session, _user, entity) as ent:
        return build_concentration_trend_ar(session, year, month, ent, periods_back)


@router.get("/receivables-aging/trend")
def get_receivables_trend(
    _user: _UserDep,
    session: _ReadSessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    months_back: int = Query(12, ge=3, le=24),
    periods_back: Optional[int] = Query(None, ge=3, le=24),
    period_grain: str = Query("month"),
) -> dict:
    back = periods_back if periods_back is not None else months_back
    with _aging_scope(session, _user, entity) as ent:
        return build_receivables_trend(session, year, month, ent, back, period_grain)


@router.get("/receivables-aging/customers")
def get_receivables_customers(
    _user: _UserDep,
    session: _ReadSessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    limit: int = Query(50, ge=10, le=100),
) -> dict:
    with _aging_scope(session, _user, entity) as ent:
        return build_receivables_customers(session, year, month, ent, limit)


@router.get("/receivables-aging/customer-documents")
def get_receivables_customer_documents(
    _user: _UserDep,
    session: _ReadSessionDep,
    customer_id: str = Query(...),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
) -> dict:
    with _aging_scope(session, _user, entity) as ent:
        return build_receivables_customer_documents(session, customer_id, year, month, ent)


@router.get("/receivables-aging/dimension-chart")
def get_receivables_dimension_chart(
    _user: _UserDep,
    session: _ReadSessionDep,
    dimension: str = Query("entity"),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    limit: int = Query(25, ge=5, le=50),
    compare_pm: bool = Query(False),
    compare_py: bool = Query(False),
) -> dict:
    with _aging_scope(session, _user, entity) as ent:
        return build_receivables_dimension_chart(session, dimension, year, month, ent, limit, compare_pm, compare_py)


@router.get("/receivables-aging/geo")
def get_receivables_geo(
    _user: _UserDep,
    session: _ReadSessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    limit: int = Query(20, ge=5, le=50),
) -> dict:
    with _aging_scope(session, _user, entity) as ent:
        return build_receivables_geo(session, year, month, ent, limit)


@router.get("/receivables-aging/geo/country-locations")
def get_receivables_geo_country_locations(
    _user: _UserDep,
    session: _ReadSessionDep,
    country_code: str = Query(...),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
) -> dict:
    with _aging_scope(session, _user, entity) as ent:
        return build_receivables_geo_country_locations(session, country_code, year, month, ent)


# ─── Payables aging analytics ──────────────────────────────────────────────────

@router.get("/payables-aging/portfolio-table")
def get_payables_portfolio_table(
    _user: _UserDep,
    session: _ReadSessionDep,
    dimension: str = Query("supplier"),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    rows_per_bucket: int = Query(25, ge=5, le=100),
) -> dict:
    with _aging_scope(session, _user, entity) as ent:
        return build_payables_portfolio_table(session, dimension, year, month, ent, rows_per_bucket)


@router.get("/payables-aging/by-dimension")
def get_payables_by_dimension(
    _user: _UserDep,
    session: _ReadSessionDep,
    dimension: str = Query("supplier"),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    limit: int = Query(50, ge=5, le=200),
    view: str = Query("buckets"),
) -> dict:
    with _aging_scope(session, _user, entity) as ent:
        return build_payables_by_dimension(session, dimension, year, month, ent, limit, view)  # type: ignore[arg-type]


@router.get("/payables-aging/by-dimension-hierarchy")
def get_payables_by_dimension_hierarchy(
    _user: _UserDep,
    session: _ReadSessionDep,
    hierarchy: str = Query("entity,supplier"),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    limit: int = Query(500, ge=10, le=2000),
    view: str = Query("buckets"),
    compare_pm: bool = Query(False),
    compare_py: bool = Query(False),
) -> dict:
    with _aging_scope(session, _user, entity) as ent:
        return build_payables_by_dimension_hierarchy(
            session, hierarchy, year, month, ent, limit, view, compare_pm, compare_py,  # type: ignore[arg-type]
        )


@router.get("/payables-aging/concentration")
def get_payables_concentration(
    _user: _UserDep,
    session: _ReadSessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
) -> dict:
    with _aging_scope(session, _user, entity) as ent:
        return build_payables_concentration(session, year, month, ent)


@router.get("/payables-aging/concentration/trend")
def get_payables_concentration_trend(
    _user: _UserDep,
    session: _ReadSessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    periods_back: int = Query(12, ge=3, le=24),
) -> dict:
    with _aging_scope(session, _user, entity) as ent:
        return build_concentration_trend_ap(session, year, month, ent, periods_back)


@router.get("/payables-aging/trend")
def get_payables_trend(
    _user: _UserDep,
    session: _ReadSessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    months_back: int = Query(12, ge=3, le=24),
    periods_back: Optional[int] = Query(None, ge=3, le=24),
    period_grain: str = Query("month"),
) -> dict:
    back = periods_back if periods_back is not None else months_back
    with _aging_scope(session, _user, entity) as ent:
        return build_payables_trend(session, year, month, ent, back, period_grain)


@router.get("/payables-aging/suppliers")
def get_payables_suppliers(
    _user: _UserDep,
    session: _ReadSessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    limit: int = Query(50, ge=10, le=100),
) -> dict:
    with _aging_scope(session, _user, entity) as ent:
        return build_payables_suppliers(session, year, month, ent, limit)


@router.get("/payables-aging/supplier-documents")
def get_payables_supplier_documents(
    _user: _UserDep,
    session: _ReadSessionDep,
    supplier_id: str = Query(...),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
) -> dict:
    with _aging_scope(session, _user, entity) as ent:
        return build_payables_supplier_documents(session, supplier_id, year, month, ent)


@router.get("/payables-aging/dimension-chart")
def get_payables_dimension_chart(
    _user: _UserDep,
    session: _ReadSessionDep,
    dimension: str = Query("entity"),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    limit: int = Query(25, ge=5, le=50),
    compare_pm: bool = Query(False),
    compare_py: bool = Query(False),
) -> dict:
    with _aging_scope(session, _user, entity) as ent:
        return build_payables_dimension_chart(session, dimension, year, month, ent, limit, compare_pm, compare_py)


@router.get("/payables-aging/geo")
def get_payables_geo(
    _user: _UserDep,
    session: _ReadSessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    limit: int = Query(20, ge=5, le=50),
) -> dict:
    with _aging_scope(session, _user, entity) as ent:
        return build_payables_geo(session, year, month, ent, limit)


@router.get("/payables-aging/geo/country-locations")
def get_payables_geo_country_locations(
    _user: _UserDep,
    session: _ReadSessionDep,
    country_code: str = Query(...),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
) -> dict:
    with _aging_scope(session, _user, entity) as ent:
        return build_payables_geo_country_locations(session, country_code, year, month, ent)


@router.get("/geography/countries")
def get_geography_countries(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    method: str = Query("invoiced"),
    entity: Optional[str] = Query(None),
) -> list:
    del method
    try:
        return build_geo_countries(session, year, month, entity)
    except Exception as exc:  # noqa: BLE001
        logger.exception("sales/geography/countries error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/geography/country-locations")
def get_geography_country_locations(
    _user: _UserDep,
    session: _SessionDep,
    country: str = Query(..., min_length=1),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    period_grain: str = Query("month"),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
    method: str = Query("invoiced"),
    entity: Optional[str] = Query(None),
) -> dict:
    del period_grain, iso_year, iso_week, method
    try:
        return build_geo_country_locations(session, country, year, month, entity)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/geography/trend")
def get_geography_trend(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    grain: str = Query("month"),
    dim: str = Query("end_customer_region"),
    method: str = Query("invoiced"),
    entity: Optional[str] = Query(None),
) -> dict:
    del method
    try:
        return build_geo_trend(session, year, month, grain, dim, entity)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/analytics/gross-sales-trend")
def get_gross_sales_trend(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    dim: str = Query("end_customer_region"),
    period_grain: str = Query("month"),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
    method: str = Query("invoiced"),
    entity: Optional[str] = Query(None),
) -> dict:
    del period_grain, iso_year, iso_week, method
    try:
        return build_gross_sales_trend(session, year, month, dim, entity)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/analytics/profit-margin-scatter")
def get_profit_margin_scatter(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    dim: str = Query("end_customer_region"),
    period_grain: str = Query("month"),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
    method: str = Query("invoiced"),
    entity: Optional[str] = Query(None),
) -> dict:
    del period_grain, iso_year, iso_week, method
    try:
        return build_profit_margin_scatter(session, year, month, dim, entity)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/analytics/composition-breakdown")
def get_composition_breakdown(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    period_grain: str = Query("month"),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
    metric: str = Query("gross_sales"),
    dims: Optional[str] = Query(None),
    top_n: int = Query(8, ge=3, le=15),
    method: str = Query("invoiced"),
    entity: Optional[str] = Query(None),
) -> dict:
    del period_grain, iso_year, iso_week, method
    try:
        return build_composition_breakdown(session, year, month, metric, dims, top_n, entity)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/analytics/metric-bridge")
def get_metric_bridge(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    dim: str = Query("end_customer_region"),
    period_grain: str = Query("month"),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
    metric: str = Query("gross_sales"),
    method: str = Query("invoiced"),
    entity: Optional[str] = Query(None),
) -> dict:
    del period_grain, iso_year, iso_week, method
    try:
        return build_metric_bridge(session, year, month, dim, metric, entity)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/analytics/breakdown-table")
def get_breakdown_table(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    period_grain: str = Query("month"),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
    dim_top: str = Query("end_customer_region"),
    dim_mid: str = Query(""),
    dim_bottom: str = Query("entity"),
    method: str = Query("invoiced"),
    entity: Optional[str] = Query(None),
) -> dict:
    del period_grain, iso_year, iso_week, method
    try:
        allowed, builder_entity = _resolve_visibility(session, _user, entity)
        return build_breakdown_table(
            session, year, month, dim_top, dim_mid, dim_bottom,
            builder_entity, allowed_entities=allowed,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("sales/analytics/breakdown-table error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/analytics/dimension-performance")
def get_dimension_performance(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    dim: str = Query("entity"),
    metric: str = Query("gross_sales"),
    period_scope: str = Query("month"),
    period_grain: str = Query("month"),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
    method: str = Query("invoiced"),
    entity: Optional[str] = Query(None),
) -> dict:
    del period_grain, iso_year, iso_week, method
    try:
        allowed, builder_entity = _resolve_visibility(session, _user, entity)
        return build_dimension_performance(
            session, year, month, dim, metric, period_scope,
            builder_entity, allowed_entities=allowed,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/gross-margin-matrix")
def get_gross_margin_matrix(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    dim: str = Query("entity"),
    period_grain: str = Query("month"),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
    method: str = Query("invoiced"),
    entity: Optional[str] = Query(None),
) -> dict:
    del period_grain, iso_year, iso_week, method
    try:
        return build_gross_margin_matrix(session, year, month, dim, entity)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/churn-bridge")
def get_churn_bridge(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    grain: str = Query("month"),
    dim: str = Query("entity"),
    method: str = Query("invoiced"),
    entity: Optional[str] = Query(None),
) -> dict:
    del method
    try:
        allowed, builder_entity = _resolve_visibility(session, _user, entity)
        return build_churn_bridge(
            session, year, month, grain, dim,
            builder_entity, allowed_entities=allowed,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
