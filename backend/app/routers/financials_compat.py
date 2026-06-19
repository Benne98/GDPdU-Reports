"""Financial compatibility endpoints.

Implements the exact legacy API paths consumed by the ported frontend:
  GET /api/v1/financials/pl-statement
  GET /api/v1/financials/pl-statement/plan
  GET /api/v1/financials/pl-statement/narrative
  GET /api/v1/financials/pl-statement/consolidation
  GET /api/v1/financials/pl-statement/monthly
  GET /api/v1/financials/pl-statement/l4-trend
  GET /api/v1/financials/pl-line-detail
  GET /api/v1/financials/journal-entry-by-booking

Sign convention (copied from app/services/statements.py, kept here for audit trail):
  Storage  : fact_gl_line.amount is signed — +Soll (debit), −Haben (credit).
  P&L view : presented = −amount.
             Revenue (credit, stored −3000) → +3000 (positive income).
             Cost    (debit,  stored  +500) → −500  (negative expense).
  The inversion `amount * −1` is applied ONCE in the SQL CASE WHEN expressions
  inside app/services/fin_compat_sql.py.  Never flip again elsewhere.
"""
from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import User, current_user
from app.db import get_session
from app.services.fin_compat_bs import (
    build_bs_consolidation,
    build_bs_l4_trend,
    build_bs_line_detail,
    build_bs_monthly,
    build_bs_narrative,
    build_bs_provision_rollforward,
    build_bs_statement_compat,
)
from app.services.fin_compat_line_detail import (
    build_pl_line_detail,
    get_journal_entry_by_booking,
)
from app.services.fin_compat_cf import (
    build_cf_consolidation,
    build_cf_l4_trend,
    build_cf_line_detail,
    build_cf_monthly,
    build_cf_narrative,
    build_cf_statement_compat,
    build_cf_weekly_breakdown,
)
from app.services.fin_compat_narrative import build_pl_narrative
from app.services.fin_compat_narrative_resolve import resolve_statement_narrative
from app.services.fin_compat_wc import (
    build_wc_consolidation,
    build_wc_l4_trend,
    build_wc_line_detail,
    build_wc_monthly,
    build_wc_narrative,
    build_wc_statement_compat,
    build_wc_timeline,
)
from app.services.fin_compat_overview import (
    build_entity_breakdown_narratives,
    build_entity_breakdown_response,
    build_overview_highlights,
    build_overview_response,
)
from app.services.fin_compat_pl import (
    build_pl_consolidation,
    build_pl_monthly,
    build_pl_plan_response,
    build_pl_statement_compat,
    build_pl_weekly_breakdown,
)
from app.services.fin_compat_sql import (
    entity_sql_fragment,
    period_label,
    pl_l4_trend_sql,
    resolve_entity_prefix,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/financials",
    tags=["financials-compat"],
)

_UserDep = Annotated[User, Depends(current_user)]
_SessionDep = Annotated[Session, Depends(get_session)]

_NARRATIVE_MISS = (
    "Narrative snapshot not ready — run warm_compat_narrative_snapshots.py after ETL."
)


def _narrative_snapshot_response(
    session: Session,
    statement: str,
    *,
    year: int,
    month: int,
    entity: Optional[str],
    period_grain: str,
    iso_year: Optional[int],
    iso_week: Optional[int],
    max_bullets: Optional[int],
    use_llm: bool,
    force_refresh: bool,
) -> dict:
    from app.services.fin_compat_narrative_resolve import _trim_bullets
    from app.services.fin_compat_overview_narrative_snapshots import _build_and_store

    nar = resolve_statement_narrative(
        session,
        statement,
        year,
        month,
        entity,
        period_grain=period_grain,
        iso_year=iso_year,
        iso_week=iso_week,
        max_bullets=max_bullets,
        use_llm=use_llm,
        force_refresh=force_refresh,
    )
    if nar:
        return nar

    # Cold cache (typical for week / entity scopes): build live so report views get full analysis.
    live = _build_and_store(
        session,
        statement=statement,
        year=year,
        month=month,
        entity=entity,
        period_grain=period_grain,
        max_bullets=max(5, max_bullets or 5),
        iso_year=iso_year,
        iso_week=iso_week,
    )
    if live:
        return _trim_bullets(live, max_bullets)

    raise HTTPException(status_code=404, detail=_NARRATIVE_MISS)


# ---------------------------------------------------------------------------
# GET /api/v1/financials/pl-statement
# ---------------------------------------------------------------------------
@router.get("/pl-statement")
def get_pl_statement(
    _user: _UserDep,
    session: _SessionDep,
    period_grain: str = Query("month", pattern="^(month|week|year)$"),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
    entity: Optional[str] = Query(None),
) -> dict:
    """
    Legacy FinancialStatementResponse — hierarchical P&L with amounts, deltas,
    and drill-down metadata per line_code.

    Ported from legacy routers/financials.py get_pl_statement() (~line 319).
    """
    _validate_period(period_grain, year, month, iso_year, iso_week)
    try:
        return build_pl_statement_compat(
            session,
            period_grain=period_grain,
            year=year, month=month,
            iso_year=iso_year, iso_week=iso_week,
            entity=entity,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("pl-statement error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/pl-statement/plan
# ---------------------------------------------------------------------------
@router.get("/pl-statement/plan")
def get_pl_plan(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
) -> dict:
    """
    PlPlanResponse — plan_cm, plan_vs_actual, ytd_plan, ytg per line_code.

    Ported from legacy routers/financials.py ~line 3949.
    Uses fact_gl_plan (scenario 'forecast' preferred, fallback 'plan').
    """
    try:
        return build_pl_plan_response(session, year, month, entity)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("pl-statement/plan error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/pl-statement/narrative
# ---------------------------------------------------------------------------
@router.get("/pl-statement/narrative")
def get_pl_narrative(
    _user: _UserDep,
    session: _SessionDep,
    period_grain: str = Query("month", pattern="^(month|week|year)$"),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
    entity: Optional[str] = Query(None),
    use_llm: bool = Query(True, description="When False, always use deterministic engine"),
    max_bullets: Optional[int] = Query(None, ge=2, le=8),
    force_refresh: bool = Query(False),
) -> dict:
    """
    PlNarrativeResponse — headline, intro, bullets[], meta.

    Serves pre-built snapshot from compat_narrative_snapshot when available.
    Cold cache returns 404 unless force_refresh or OVERVIEW_NARRATIVE_ALLOW_REBUILD=1.
    """
    _validate_period(period_grain, year, month, iso_year, iso_week)
    try:
        return _narrative_snapshot_response(
            session,
            "pl",
            year=year or 0,
            month=month or 0,
            entity=entity,
            period_grain=period_grain,
            iso_year=iso_year,
            iso_week=iso_week,
            max_bullets=max_bullets,
            use_llm=use_llm,
            force_refresh=force_refresh,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("pl-statement/narrative error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/pl-statement/consolidation
# ---------------------------------------------------------------------------
@router.get("/pl-statement/consolidation")
def get_pl_consolidation(
    _user: _UserDep,
    session: _SessionDep,
    period_grain: str = Query("month", pattern="^(month|week|year)$"),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
) -> dict:
    """
    ConsolidationResponse — per-entity columns + consolidated total per P&L line.

    Ported from legacy routers/financials.py ~line 2120.
    """
    _validate_period(period_grain, year, month, iso_year, iso_week)
    try:
        return build_pl_consolidation(
            session,
            period_grain=period_grain,
            year=year, month=month,
            iso_year=iso_year, iso_week=iso_week,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("pl-statement/consolidation error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/pl-statement/monthly
# ---------------------------------------------------------------------------
@router.get("/pl-statement/monthly")
def get_pl_monthly(
    _user: _UserDep,
    session: _SessionDep,
    period_grain: str = Query("month", pattern="^(month|week|year)$"),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
    entity: Optional[str] = Query(None),
    span: str = Query("12m", pattern="^(12m|fy3)$"),
) -> dict:
    """
    MonthlyResponse — 12-month sparkline for each P&L line in the anchor year.

    Ported from legacy routers/financials.py ~line 2796.

    ``span`` (month grain only):
      * '12m' (default) — unchanged legacy 12-month response.
      * 'fy3' — all months from (year-2)-01 to (year)-month PLUS three summary
        columns (added ``totals[]`` field): FY(year-2), FY(year-1), YTD(year).
    """
    _validate_period(period_grain, year, month, iso_year, iso_week)
    try:
        return build_pl_monthly(
            session,
            period_grain=period_grain,
            year=year, month=month,
            iso_year=iso_year, iso_week=iso_week,
            entity=entity,
            span=span,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("pl-statement/monthly error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/pl-statement/weekly
# ---------------------------------------------------------------------------
@router.get("/pl-statement/weekly")
def get_pl_weekly(
    _user: _UserDep,
    session: _SessionDep,
    iso_year: int = Query(..., ge=2000, le=2100),
    iso_week: int = Query(..., ge=1, le=53),
    entity: Optional[str] = Query(None),
) -> dict:
    """
    Weekly-breakdown P&L — two full preceding calendar months (M-2, M-1) plus the
    in-progress month (M0), each split into ISO-week columns with a month / MTD
    total column.

    The anchor ISO week's Sunday determines M0.  Column maths and running-sum
    subtotal semantics live in fin_compat_pl.build_pl_weekly_breakdown and
    fin_compat_sql.{weekly_breakdown_layout, pl_weekly_breakdown_sql}.
    """
    try:
        return build_pl_weekly_breakdown(
            session, iso_year=iso_year, iso_week=iso_week, entity=entity,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("pl-statement/weekly error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/pl-statement/l4-trend
# ---------------------------------------------------------------------------
@router.get("/pl-statement/l4-trend")
def get_pl_l4_trend(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    grain: str = Query("year", pattern="^(year|quarter|month)$"),
    level_2: str = Query(""),
    level_3: str = Query(""),
    level_4: str = Query(""),
    entity: Optional[str] = Query(None),
) -> dict:
    """
    L4 Trend chart data — series per window (year/quarter/month grain).

    Ported from legacy routers/financials.py _l4_trend_response() ~line 3342.
    Only pl-statement is implemented; other statement types return 404.
    """
    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)

    try:
        sql, windows = pl_l4_trend_sql(year, month, grain, level_2, level_3, level_4, ent_frag)
        rows = session.execute(text(sql)).fetchall()
    except Exception as exc:
        logger.exception("pl-statement/l4-trend query error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if not rows:
        return {
            "series": [],
            "col_label": period_label(year, month),
            "prev_label": "",
        }

    row = dict(rows[0]._mapping) if hasattr(rows[0], "_mapping") else dict(rows[0])
    series = _build_trend_series(row, windows)
    last_w = windows[-1]
    prev_end = last_w.get("prev_end")
    if hasattr(prev_end, "strftime"):
        prev_label = prev_end.strftime("%b %Y")
    else:
        prev_label = str(prev_end) if prev_end else ""

    return {
        "series": series,
        "col_label": period_label(year, month),
        "prev_label": prev_label,
    }


def _build_trend_series(row: dict, windows: list[dict]) -> list[dict]:
    """Build trend series from wide SQL row + windows meta.

    Ported directly from legacy routers/financials.py _build_trend_series() ~line 3300.
    """
    series = []
    for w in windows:
        cur = float(row.get(w["pk"]) or 0)
        prev = float(row.get(w["prev_pk"]) or 0)
        delta = round((cur - prev) / abs(prev) * 100, 1) if abs(prev) > 1e-6 else None
        series.append({
            "label": w["label"],
            "current": round(cur, 2),
            "previous": round(prev, 2),
            "delta_pct": delta,
            "date_from": w["date_from"],
            "date_to": w["date_to"],
        })
    return series


# ---------------------------------------------------------------------------
# GET /api/v1/financials/pl-line-detail
# ---------------------------------------------------------------------------
@router.get("/pl-line-detail")
def get_pl_line_detail(
    _user: _UserDep,
    session: _SessionDep,
    line_code: str = Query(..., description="P&L line_code (may be 'BASE::L4' composite)"),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    use_llm: bool = Query(True),
) -> dict:
    """
    PlLineDetailResponse — account breakdown, top bookings, timeline.

    Ported from legacy routers/financials.py ~line 3996
    + services/pl_line_detail/pipeline.py.
    """
    try:
        return build_pl_line_detail(
            session, line_code, year, month, entity,
            limit=limit, use_llm=use_llm,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("pl-line-detail error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/journal-entry-by-booking
# ---------------------------------------------------------------------------
@router.get("/journal-entry-by-booking")
def get_journal_entry_by_booking_endpoint(
    _user: _UserDep,
    session: _SessionDep,
    booking_line_id: int = Query(..., description="fact_gl_line.booking_line_id (unique)"),
) -> dict:
    """
    Full journal entry for a single booking_line_id (all lines of the same entry).

    Ported from legacy routers/financials.py ~line 4024.
    booking_line_id is a 1:1 unique key in fact_gl_line.
    """
    result = get_journal_entry_by_booking(session, booking_line_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"booking_line_id {booking_line_id} not found")
    return result


# ===========================================================================
# Balance-sheet compat endpoints (statement='bs', cumulative-balance semantics)
# ===========================================================================
# Values are cumulative balances (stocks) up to each column's cutoff — NOT period
# flows.  The credit side (equity & liabilities) is display-flipped to read
# positive; the P&L `* -1` rule is NOT used.  See app/services/fin_compat_bs.py
# (FORMULA / WORKED EXAMPLE / EDGE CASES) and fin_compat_bs_sql.py (cumulative SQL).


# ---------------------------------------------------------------------------
# GET /api/v1/financials/balance-sheet
# ---------------------------------------------------------------------------
@router.get("/balance-sheet")
def get_balance_sheet(
    _user: _UserDep,
    session: _SessionDep,
    period_grain: str = Query("month", pattern="^(month|week|year)$"),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
    entity: Optional[str] = Query(None),
) -> dict:
    """FinancialStatementResponse (statement='bs') — cumulative balances per column,
    running-sum section subtotals, credit-side display flip, net profit in equity,
    and an equity-ratio KPI.  Ported from legacy routers/financials.py
    get_balance_sheet()."""
    _validate_period(period_grain, year, month, iso_year, iso_week)
    try:
        return build_bs_statement_compat(
            session, period_grain=period_grain, year=year, month=month,
            iso_year=iso_year, iso_week=iso_week, entity=entity,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("balance-sheet error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/balance-sheet/consolidation
# ---------------------------------------------------------------------------
@router.get("/balance-sheet/consolidation")
def get_bs_consolidation(
    _user: _UserDep,
    session: _SessionDep,
    period_grain: str = Query("month", pattern="^(month|week|year)$"),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
) -> dict:
    """ConsolidationResponse (statement='bs') — per-entity cm balance, aggregated /
    ic_eliminations(0) / consolidation, plus an equity-ratio KPI."""
    _validate_period(period_grain, year, month, iso_year, iso_week)
    try:
        return build_bs_consolidation(
            session, period_grain=period_grain, year=year, month=month,
            iso_year=iso_year, iso_week=iso_week,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("balance-sheet/consolidation error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/balance-sheet/monthly
# ---------------------------------------------------------------------------
@router.get("/balance-sheet/monthly")
def get_bs_monthly(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    span: str = Query("12m", pattern="^(12m|fy3)$"),
) -> dict:
    """MonthlyResponse (statement='bs') — each amounts[YYYY-MM] is the month-END
    cumulative balance; optional span='fy3' adds FY/YTD balance totals."""
    try:
        return build_bs_monthly(session, year=year, month=month, entity=entity, span=span)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("balance-sheet/monthly error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/balance-sheet/narrative
# ---------------------------------------------------------------------------
@router.get("/balance-sheet/narrative")
def get_bs_narrative(
    _user: _UserDep,
    session: _SessionDep,
    period_grain: str = Query("month", pattern="^(month|week|year)$"),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
    entity: Optional[str] = Query(None),
    use_llm: bool = Query(False),
    max_bullets: Optional[int] = Query(None, ge=2, le=8),
    visible_rows: Optional[int] = Query(None, ge=1, le=200),
    force_refresh: bool = Query(False),
) -> dict:
    """PlNarrativeResponse — deterministic BS key drivers (snapshot-first)."""
    _validate_period(period_grain, year, month, iso_year, iso_week)
    try:
        return _narrative_snapshot_response(
            session,
            "bs",
            year=year or 0,
            month=month or 0,
            entity=entity,
            period_grain=period_grain,
            iso_year=iso_year,
            iso_week=iso_week,
            max_bullets=max_bullets,
            use_llm=use_llm,
            force_refresh=force_refresh,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("balance-sheet/narrative error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/balance-sheet/line-detail
# ---------------------------------------------------------------------------
@router.get("/balance-sheet/line-detail")
def get_bs_line_detail(
    _user: _UserDep,
    session: _SessionDep,
    line_code: str = Query(..., description="BS line_code (may be 'BASE::L4' composite)"),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    timeline_months: int = Query(12, ge=3, le=18),
    use_llm: bool = Query(True),
    line_mom_keur: Optional[float] = Query(None),
    anchor_year: Optional[int] = Query(None, ge=2000, le=2100),
    anchor_month: Optional[int] = Query(None, ge=1, le=12),
) -> dict:
    """PlLineDetailResponse (statement='bs') — cumulative account balances (cm/pm),
    top bookings, and a 12-month balance timeline."""
    try:
        return build_bs_line_detail(
            session, line_code, year, month, entity,
            limit=limit, timeline_months=timeline_months,
            use_llm=use_llm, line_mom_keur=line_mom_keur,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("balance-sheet/line-detail error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/balance-sheet/l4-trend
# ---------------------------------------------------------------------------
@router.get("/balance-sheet/l4-trend")
def get_bs_l4_trend(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    grain: str = Query("year", pattern="^(year|quarter|month)$"),
    level_2: str = Query(""),
    level_3: str = Query(""),
    level_4: str = Query(""),
    entity: Optional[str] = Query(None),
) -> dict:
    """L4TrendResponse (statement='bs') — cumulative balance at each window end."""
    try:
        return build_bs_l4_trend(
            session, year=year, month=month, grain=grain,
            level_2=level_2, level_3=level_3, level_4=level_4, entity=entity,
        )
    except Exception as exc:
        logger.exception("balance-sheet/l4-trend error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/balance-sheet/provision-rollforward
# ---------------------------------------------------------------------------
@router.get("/balance-sheet/provision-rollforward")
def get_bs_provision_rollforward(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
) -> dict:
    """ProvisionRollforwardResponse — GL-derived opening/closing/net_movement per
    provision category (read-only; clean 0-fallback when no such accounts)."""
    try:
        return build_bs_provision_rollforward(session, year, month, entity)
    except Exception as exc:
        logger.exception("balance-sheet/provision-rollforward error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ===========================================================================
# Working-capital compat endpoints (statement='wc', cumulative-balance subset)
# ===========================================================================
# Working capital = the BS balances whose dim_gl_na.l6_na_mapping is 'TWC'/'OWC'.
# Same cumulative-stock semantics as the BS, but the RAW signed balances are kept
# (no credit-side display flip): Net working capital is the straight Σ of the raw
# TWC+OWC balances.  Plus DSO/DIO/DPO/CCC day KPIs (LTM revenue/COGS denominators)
# and a running-component timeline.  See app/services/fin_compat_wc.py
# (FORMULA / WORKED EXAMPLE / EDGE CASES) and fin_compat_wc_sql.py.


# ---------------------------------------------------------------------------
# GET /api/v1/financials/working-capital
# ---------------------------------------------------------------------------
@router.get("/working-capital")
def get_working_capital(
    _user: _UserDep,
    session: _SessionDep,
    period_grain: str = Query("month", pattern="^(month|week|year)$"),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
    entity: Optional[str] = Query(None),
) -> dict:
    """FinancialStatementResponse (statement='wc') — raw cumulative TWC/OWC
    balances grouped by section/level_3, a Net working capital subtotal, and
    DSO/DIO/DPO/CCC day KPIs per column."""
    _validate_period(period_grain, year, month, iso_year, iso_week)
    try:
        return build_wc_statement_compat(
            session, period_grain=period_grain, year=year, month=month,
            iso_year=iso_year, iso_week=iso_week, entity=entity,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("working-capital error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/working-capital/consolidation
# ---------------------------------------------------------------------------
@router.get("/working-capital/consolidation")
def get_wc_consolidation(
    _user: _UserDep,
    session: _SessionDep,
    period_grain: str = Query("month", pattern="^(month|week|year)$"),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
) -> dict:
    """ConsolidationResponse (statement='wc') — per-entity cm balance, NWC
    subtotal, and per-entity / consolidated day KPIs."""
    _validate_period(period_grain, year, month, iso_year, iso_week)
    try:
        return build_wc_consolidation(
            session, period_grain=period_grain, year=year, month=month,
            iso_year=iso_year, iso_week=iso_week,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("working-capital/consolidation error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/working-capital/monthly
# ---------------------------------------------------------------------------
@router.get("/working-capital/monthly")
def get_wc_monthly(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    span: str = Query("12m", pattern="^(12m|fy3)$"),
) -> dict:
    """MonthlyResponse (statement='wc') — each amounts[YYYY-MM] is the month-END
    cumulative WC balance; NWC subtotal + day KPIs per column.
    Optional span='fy3' adds FY/YTD balance totals."""
    try:
        return build_wc_monthly(session, year=year, month=month, entity=entity, span=span)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("working-capital/monthly error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/working-capital/narrative
# ---------------------------------------------------------------------------
@router.get("/working-capital/narrative")
def get_wc_narrative(
    _user: _UserDep,
    session: _SessionDep,
    period_grain: str = Query("month", pattern="^(month|week|year)$"),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
    entity: Optional[str] = Query(None),
    use_llm: bool = Query(False),
    max_bullets: Optional[int] = Query(None, ge=2, le=8),
    visible_rows: Optional[int] = Query(None, ge=1, le=200),
    force_refresh: bool = Query(False),
) -> dict:
    """PlNarrativeResponse — deterministic WC key drivers (snapshot-first)."""
    _validate_period(period_grain, year, month, iso_year, iso_week)
    try:
        return _narrative_snapshot_response(
            session,
            "wc",
            year=year or 0,
            month=month or 0,
            entity=entity,
            period_grain=period_grain,
            iso_year=iso_year,
            iso_week=iso_week,
            max_bullets=max_bullets,
            use_llm=use_llm,
            force_refresh=force_refresh,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("working-capital/narrative error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/working-capital/line-detail
# ---------------------------------------------------------------------------
@router.get("/working-capital/line-detail")
def get_wc_line_detail(
    _user: _UserDep,
    session: _SessionDep,
    line_code: str = Query(..., description="WC line_code (NWC, WC_DSO/DIO/DPO/CCC, node id, or gl id)"),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    timeline_months: int = Query(12, ge=3, le=18),
    use_llm: bool = Query(True),
    line_mom_keur: Optional[float] = Query(None),
    anchor_year: Optional[int] = Query(None, ge=2000, le=2100),
    anchor_month: Optional[int] = Query(None, ge=1, le=12),
) -> dict:
    """PlLineDetailResponse (statement='wc') — cumulative account balances (cm/pm),
    top bookings, and a 12-month balance timeline for a working-capital line."""
    try:
        return build_wc_line_detail(
            session, line_code, year, month, entity,
            limit=limit, timeline_months=timeline_months,
            use_llm=use_llm, line_mom_keur=line_mom_keur,
            anchor_year=anchor_year, anchor_month=anchor_month,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("working-capital/line-detail error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/working-capital/l4-trend
# ---------------------------------------------------------------------------
@router.get("/working-capital/l4-trend")
def get_wc_l4_trend(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    grain: str = Query("year", pattern="^(year|quarter|month)$"),
    level_2: str = Query(""),
    level_3: str = Query(""),
    level_4: str = Query(""),
    entity: Optional[str] = Query(None),
) -> dict:
    """L4TrendResponse (statement='wc') — cumulative balance at each window end."""
    try:
        return build_wc_l4_trend(
            session, year=year, month=month, grain=grain,
            level_2=level_2, level_3=level_3, level_4=level_4, entity=entity,
        )
    except Exception as exc:
        logger.exception("working-capital/l4-trend error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/working-capital/wc-timeline
# ---------------------------------------------------------------------------
@router.get("/working-capital/wc-timeline")
def get_wc_timeline(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    grain: str = Query("month", pattern="^(month|week|day)$"),
    entity: Optional[str] = Query(None),
) -> dict:
    """WcTimelineResponse — running cumulative TWC/OWC component magnitudes per
    period end (inventories / receivables / payables / other_wc / twc / nwc) with
    an average-TWC reference window."""
    try:
        return build_wc_timeline(session, year=year, month=month, grain=grain, entity=entity)
    except Exception as exc:
        logger.exception("working-capital/wc-timeline error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ===========================================================================
# Cash-flow compat endpoints (statement='cf', period-FLOW semantics)
# ===========================================================================
# CF is a FLOW statement (like the P&L): each column is the Σ of in-period GL
# movements, mapped via dim_gl_cf (cf_mapping), presented with the single P&L
# `amount * -1` inversion (inflow +, outflow −).  Subtotals are running sums of
# the preceding mapping leaves (Net cash flow = Σ all CF leaves).  See
# app/services/fin_compat_cf.py (FORMULA / WORKED EXAMPLE / EDGE CASES) and
# fin_compat_cf_sql.py (period-flow SQL over the dim_gl_cf join).


# ---------------------------------------------------------------------------
# GET /api/v1/financials/cash-flow
# ---------------------------------------------------------------------------
@router.get("/cash-flow")
def get_cash_flow(
    _user: _UserDep,
    session: _SessionDep,
    period_grain: str = Query("month", pattern="^(month|week|year)$"),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
    entity: Optional[str] = Query(None),
) -> dict:
    """FinancialStatementResponse (statement='cf') — period-flow cash-flow lines
    grouped by the CF structure (sections + running-sum subtotals), with CF-mapping
    drill metadata and account-level children."""
    _validate_period(period_grain, year, month, iso_year, iso_week)
    try:
        return build_cf_statement_compat(
            session, period_grain=period_grain, year=year, month=month,
            iso_year=iso_year, iso_week=iso_week, entity=entity,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("cash-flow error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/cash-flow/consolidation
# ---------------------------------------------------------------------------
@router.get("/cash-flow/consolidation")
def get_cf_consolidation(
    _user: _UserDep,
    session: _SessionDep,
    period_grain: str = Query("month", pattern="^(month|week|year)$"),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
) -> dict:
    """ConsolidationResponse (statement='cf') — per-entity cm flow, aggregated /
    ic_eliminations(0) / consolidation per CF line."""
    _validate_period(period_grain, year, month, iso_year, iso_week)
    try:
        return build_cf_consolidation(
            session, period_grain=period_grain, year=year, month=month,
            iso_year=iso_year, iso_week=iso_week,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("cash-flow/consolidation error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/cash-flow/monthly
# ---------------------------------------------------------------------------
@router.get("/cash-flow/monthly")
def get_cf_monthly(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    span: str = Query("12m", pattern="^(12m|fy3)$"),
) -> dict:
    """MonthlyResponse (statement='cf') — each amounts[YYYY-MM] is that month's CF
    flow; running-sum subtotals per column. Optional span='fy3' adds FY/YTD totals."""
    try:
        return build_cf_monthly(session, year=year, month=month, entity=entity, span=span)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("cash-flow/monthly error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/cash-flow/weekly
# ---------------------------------------------------------------------------
@router.get("/cash-flow/weekly")
def get_cf_weekly(
    _user: _UserDep,
    session: _SessionDep,
    iso_year: int = Query(..., ge=2000, le=2100),
    iso_week: int = Query(..., ge=1, le=53),
    entity: Optional[str] = Query(None),
) -> dict:
    """Weekly-breakdown cash flow — M-2 / M-1 full months + M0 partial, by ISO week.

    Same column layout as the P&L weekly breakdown; CF rows follow dim_pl_structure
    order with section-aware subtotals.
    """
    try:
        return build_cf_weekly_breakdown(
            session, iso_year=iso_year, iso_week=iso_week, entity=entity,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("cash-flow/weekly error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/cash-flow/narrative
# ---------------------------------------------------------------------------
@router.get("/cash-flow/narrative")
def get_cf_narrative(
    _user: _UserDep,
    session: _SessionDep,
    period_grain: str = Query("month", pattern="^(month|week|year)$"),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
    entity: Optional[str] = Query(None),
    use_llm: bool = Query(False),
    max_bullets: Optional[int] = Query(None, ge=2, le=8),
    visible_rows: Optional[int] = Query(None, ge=1, le=200),
    force_refresh: bool = Query(False),
) -> dict:
    """PlNarrativeResponse — deterministic CF key drivers (snapshot-first)."""
    _validate_period(period_grain, year, month, iso_year, iso_week)
    try:
        return _narrative_snapshot_response(
            session,
            "cf",
            year=year or 0,
            month=month or 0,
            entity=entity,
            period_grain=period_grain,
            iso_year=iso_year,
            iso_week=iso_week,
            max_bullets=max_bullets,
            use_llm=use_llm,
            force_refresh=force_refresh,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("cash-flow/narrative error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/cash-flow/line-detail
# ---------------------------------------------------------------------------
@router.get("/cash-flow/line-detail")
def get_cf_line_detail(
    _user: _UserDep,
    session: _SessionDep,
    line_code: str = Query(..., description="CF line_code (CF_… structure code)"),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    timeline_months: int = Query(12, ge=3, le=18),
    use_llm: bool = Query(True),
    line_mom_keur: Optional[float] = Query(None),
) -> dict:
    """PlLineDetailResponse (statement='cf') — period-flow account breakdown, top
    bookings, and a 12-month flow timeline for a cash-flow line."""
    try:
        return build_cf_line_detail(
            session, line_code, year, month, entity,
            limit=limit, timeline_months=timeline_months,
            use_llm=use_llm, line_mom_keur=line_mom_keur,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("cash-flow/line-detail error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/cash-flow/l4-trend
# ---------------------------------------------------------------------------
@router.get("/cash-flow/l4-trend")
def get_cf_l4_trend(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    grain: str = Query("year", pattern="^(year|quarter|month)$"),
    level_2: str = Query(""),
    level_3: str = Query(""),
    level_4: str = Query(""),
    entity: Optional[str] = Query(None),
) -> dict:
    """L4TrendResponse (statement='cf') — period-flow at each window for a CF
    position (level_2→cf.l1, level_3→cf.l2, level_4→cf.cf_mapping)."""
    try:
        return build_cf_l4_trend(
            session, year=year, month=month, grain=grain,
            level_2=level_2, level_3=level_3, level_4=level_4, entity=entity,
        )
    except Exception as exc:
        logger.exception("cash-flow/l4-trend error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ===========================================================================
# Overview compat endpoints (statement='overview')
# ===========================================================================
# The /overview frontend page renders a "Group summary" tile (sections) and an
# "Entity breakdown" tile.  All four routes accept the same period query params
# as the P&L statement and delegate to app/services/fin_compat_overview.py.


# ---------------------------------------------------------------------------
# GET /api/v1/financials/overview
# ---------------------------------------------------------------------------
@router.get("/overview")
def get_financials_overview(
    _user: _UserDep,
    session: _SessionDep,
    period_grain: str = Query("month", pattern="^(month|week|year)$"),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
    entity: Optional[str] = Query(None),
    include_highlights: bool = Query(False),
) -> dict:
    """FinancialsOverviewResponse — Group summary sections + optional highlights.

    Derives consolidated P&L data (entity='all') from the GDPdU GL and returns
    sections (Revenue / Profitability), entity_snapshots, intro, and highlights.
    Ported from legacy routers/financials.py get_financials_overview().
    """
    _validate_period(period_grain, year, month, iso_year, iso_week)
    try:
        return build_overview_response(
            session,
            period_grain=period_grain,
            year=year, month=month,
            iso_year=iso_year, iso_week=iso_week,
            entity=entity,
            include_highlights=include_highlights,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("overview error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/overview/highlights
# ---------------------------------------------------------------------------
@router.get("/overview/highlights")
def get_financials_overview_highlights(
    _user: _UserDep,
    session: _SessionDep,
    period_grain: str = Query("month", pattern="^(month|week|year)$"),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
    entity: Optional[str] = Query(None),
) -> dict:
    """{ highlights: OverviewHighlight[] } — lazy-loaded statement summaries.

    One highlight per tab (pl / bs / cf / wc) with an intro sentence and
    data-driven bullet points derived from the consolidated P&L numbers.
    Ported from legacy services/financials_overview/build.py highlights pipeline.
    """
    _validate_period(period_grain, year, month, iso_year, iso_week)
    try:
        return build_overview_highlights(
            session,
            period_grain=period_grain,
            year=year, month=month,
            iso_year=iso_year, iso_week=iso_week,
            entity=entity,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("overview/highlights error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/overview/entity-breakdown
# ---------------------------------------------------------------------------
@router.get("/overview/entity-breakdown")
def get_financials_entity_breakdown(
    _user: _UserDep,
    session: _SessionDep,
    period_grain: str = Query("month", pattern="^(month|week|year)$"),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
    entity: Optional[str] = Query(None),
    include_narratives: bool = Query(False),
) -> dict:
    """FinancialsEntityBreakdownResponse — per-entity cm values for key P&L rows.

    Returns entities[], rows[] (NET_SALES / GROSS_PROFIT / EBITDA / EBIT /
    NET_PROFIT with cm_by_entity), and areas[] (key-driver narrative bullets).
    Ported from legacy services/financials_overview/build.py entity_breakdown().
    """
    _validate_period(period_grain, year, month, iso_year, iso_week)
    try:
        return build_entity_breakdown_response(
            session,
            period_grain=period_grain,
            year=year, month=month,
            iso_year=iso_year, iso_week=iso_week,
            entity=entity,
            include_narratives=include_narratives,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("overview/entity-breakdown error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/overview/entity-breakdown/narratives
# ---------------------------------------------------------------------------
@router.get("/overview/entity-breakdown/narratives")
def get_financials_entity_breakdown_narratives(
    _user: _UserDep,
    session: _SessionDep,
    period_grain: str = Query("month", pattern="^(month|week|year)$"),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
    entity: Optional[str] = Query(None),
) -> dict:
    """{ areas: EntityBreakdownArea[] } — async-loaded per-entity key-driver bullets.

    Each area maps to a statement tab (pl / bs / cf / wc) with an intro sentence
    and a bullet per legal entity summarising that entity's position in the period.
    Ported from legacy services/financials_overview/build.py narratives pipeline.
    """
    _validate_period(period_grain, year, month, iso_year, iso_week)
    try:
        return build_entity_breakdown_narratives(
            session,
            period_grain=period_grain,
            year=year, month=month,
            iso_year=iso_year, iso_week=iso_week,
            entity=entity,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("overview/entity-breakdown/narratives error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Period validation helper
# ---------------------------------------------------------------------------
def _validate_period(
    grain: str,
    year: Optional[int], month: Optional[int],
    iso_year: Optional[int], iso_week: Optional[int],
) -> None:
    if grain == "week":
        if iso_year is None or iso_week is None:
            raise HTTPException(
                status_code=422,
                detail="period_grain='week' requires iso_year and iso_week query parameters",
            )
    elif grain in ("month", "year"):
        if year is None or month is None:
            raise HTTPException(
                status_code=422,
                detail=f"period_grain='{grain}' requires year and month query parameters",
            )
    else:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported period_grain '{grain}'",
        )
