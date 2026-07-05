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
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import User, current_user
from app.db import get_read_session, get_session
# NOTE: ``cache_anomalies`` (DELETE+INSERT on fact_anomaly) is intentionally NOT
# imported here — the anomalies GET is pure-read so a non-admin user can never
# trigger a write via GET.  Caching stays available in app.services.anomaly for an
# explicit admin/internal warm path only.
from app.services.anomaly import anomaly_to_api, detect_anomalies
from app.services.entity_visibility import visible_entity_codes
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
from app.services.fin_compat_cash_debt import build_net_debt_table, build_position_bookings
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
    build_statement_plan_response,
)
from app.services.fin_compat_sql import (
    entity_sql_fragment,
    period_label,
    pl_l4_trend_sql,
    resolve_entity_prefix,
)
from app.services import anomaly_compute
from app.services.gl_anomaly_tree import list_account_bookings

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/financials",
    tags=["financials-compat"],
)

_UserDep = Annotated[User, Depends(current_user)]
_SessionDep = Annotated[Session, Depends(get_read_session)]

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
        allowed_prefixes = _resolve_plan_allowed_prefixes(session, _user, "pl-statement/plan")
        return build_statement_plan_response(session, "PL", year, month, entity, allowed_prefixes=allowed_prefixes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("pl-statement/plan error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _resolve_plan_allowed_prefixes(session: Session, user: User, label: str):
    """Fail-closed tenant-visibility resolution for the /plan statement routes.

    Mirrors /overview/summary: ``visible_entity_codes`` → admin ``None``
    (unrestricted); non-admin granted ``set[str]``; non-admin without grants or on
    lookup error → ``set()`` (deny-all).  A prefix-mapping failure NEVER widens to
    None for a non-admin and NEVER raises a 500 — it fails closed to deny-all.
    """
    from app.services.overview_summary import map_codes_to_prefixes

    allowed_codes = visible_entity_codes(session, user)
    try:
        return map_codes_to_prefixes(session, allowed_codes)
    except Exception:
        logger.exception("%s prefix mapping failed — failing closed", label)
        return None if allowed_codes is None else set()


# ---------------------------------------------------------------------------
# GET /api/v1/financials/balance-sheet/plan
# ---------------------------------------------------------------------------
@router.get("/balance-sheet/plan")
def get_bs_plan(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
) -> dict:
    """Balance-sheet plan overlay (PlPlanResponse shape). Fail-closed tenant scope."""
    allowed_prefixes = _resolve_plan_allowed_prefixes(session, _user, "balance-sheet/plan")
    try:
        return build_statement_plan_response(
            session, "BS", year, month, entity, allowed_prefixes=allowed_prefixes
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("balance-sheet/plan error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/working-capital/plan
# ---------------------------------------------------------------------------
@router.get("/working-capital/plan")
def get_wc_plan(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
) -> dict:
    """Working-capital plan overlay (PlPlanResponse shape). Fail-closed tenant scope."""
    allowed_prefixes = _resolve_plan_allowed_prefixes(session, _user, "working-capital/plan")
    try:
        return build_statement_plan_response(
            session, "WC", year, month, entity, allowed_prefixes=allowed_prefixes
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("working-capital/plan error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/cash-flow/plan
# ---------------------------------------------------------------------------
@router.get("/cash-flow/plan")
def get_cf_plan(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
) -> dict:
    """Cash-flow plan overlay (PlPlanResponse shape). Fail-closed tenant scope."""
    allowed_prefixes = _resolve_plan_allowed_prefixes(session, _user, "cash-flow/plan")
    try:
        return build_statement_plan_response(
            session, "CF", year, month, entity, allowed_prefixes=allowed_prefixes
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("cash-flow/plan error")
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


# ---------------------------------------------------------------------------
# GET /api/v1/financials/cash-debt/net-debt
# ---------------------------------------------------------------------------
@router.get("/cash-debt/net-debt")
def get_cash_debt_net_debt(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
) -> dict:
    """Net-debt table (Cash flow tab).

    SECURITY (fail-closed tenant isolation, SAME pattern as /overview/*):
    ``visible_entity_codes()`` is resolved, mapped to the ``entity_prefix`` set and
    narrowed by the requested ``entity`` via ``_effective_prefixes``.  Admin → None
    (unrestricted); a restricted caller → only their entities; empty visibility (or
    an ``entity`` narrow outside it) → a ZEROED table (the empty-render guarantee
    still emits the Net-financial-debt / Net-debt rows), never cross-entity data.
    """
    from app.services.overview_summary import (
        _effective_prefixes,
        map_codes_to_prefixes,
    )

    allowed_codes = visible_entity_codes(session, _user)
    try:
        allowed_prefixes = map_codes_to_prefixes(session, allowed_codes)
        eff, builder_entity, _denied = _effective_prefixes(
            session, entity=entity, allowed_prefixes=allowed_prefixes,
        )
    except Exception:
        # Fail-closed (align with /overview/* + visible_entity_codes): a
        # dim_legal_entity lookup failure for a non-admin denies all (empty set →
        # zeroed table), NEVER a 500 and NEVER widened to None/all. Admin stays admin.
        logger.exception("cash-debt/net-debt visibility mapping failed — failing closed")
        if allowed_codes is None:
            eff, builder_entity = None, (entity or None)
        else:
            eff, builder_entity = set(), None
    try:
        return build_net_debt_table(
            session, year=year, month=month,
            entity=builder_entity, allowed_entities=eff,
        )
    except Exception as exc:
        logger.exception("cash-debt/net-debt error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/cash-debt/position-bookings
# ---------------------------------------------------------------------------
@router.get("/cash-debt/position-bookings")
def get_cash_debt_position_bookings(
    _user: _UserDep,
    session: _SessionDep,
    account_number_group: str = Query(...),
    fiscal_year: int = Query(..., ge=2000, le=2100),
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    months_back: int = Query(24, ge=6, le=60),
) -> dict:
    """Per-account booking series (Cash & Debt drill).

    SECURITY (fail-closed tenant isolation, SAME pattern as /overview/*):
    ``visible_entity_codes()`` → ``entity_prefix`` set → narrowed by ``entity`` via
    ``_effective_prefixes``.  Admin → None (unrestricted); a restricted caller may
    only drill an account whose entity_prefix is within their visibility — otherwise
    (empty visibility, out-of-scope ``entity``, or an out-of-scope
    ``account_number_group``) the service returns an EMPTY ``entries`` result, never
    another tenant's bookings.
    """
    from app.services.overview_summary import (
        _effective_prefixes,
        map_codes_to_prefixes,
    )

    allowed_codes = visible_entity_codes(session, _user)
    try:
        allowed_prefixes = map_codes_to_prefixes(session, allowed_codes)
        eff, builder_entity, _denied = _effective_prefixes(
            session, entity=entity, allowed_prefixes=allowed_prefixes,
        )
    except Exception:
        # Fail-closed (align with /overview/* + visible_entity_codes): a
        # dim_legal_entity lookup failure for a non-admin denies all (empty set →
        # empty entries), NEVER a 500 and NEVER widened to None/all. Admin stays admin.
        logger.exception("cash-debt/position-bookings visibility mapping failed — failing closed")
        if allowed_codes is None:
            eff, builder_entity = None, (entity or None)
        else:
            eff, builder_entity = set(), None
    try:
        return build_position_bookings(
            session,
            account_number_group=account_number_group,
            fiscal_year=fiscal_year,
            year=year,
            month=month,
            entity=builder_entity,
            months_back=months_back,
            allowed_entities=eff,
        )
    except Exception as exc:
        logger.exception("cash-debt/position-bookings error")
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
# GET /api/v1/financials/overview/summary  (Overview v2 — ONE batched payload)
# ---------------------------------------------------------------------------
# Typed response model so the frontend hook can be typed; field names are STABLE.
# Assembly + security live in app/services/overview_summary.py (fail-closed).


class _RevenueKpi(BaseModel):
    cm: float
    cm_py: float
    ytd: float
    ytd_py: Optional[float]
    yoy_pct: Optional[float]


class _EbitKpi(BaseModel):
    cm: float
    ytd: float
    margin_pct: Optional[float]


class _CashKpi(BaseModel):
    level: float
    delta_month: float
    delta_yoy: float


class _WcHeroKpi(BaseModel):
    ccc: float
    nwc: float


class _HeroBlock(BaseModel):
    revenue: _RevenueKpi
    ebit: _EbitKpi
    cash: _CashKpi
    working_capital: _WcHeroKpi


class _WcLevel(BaseModel):
    key: str
    label: str
    level: float
    delta_month: float
    delta_fy: float


class _WorkingCapitalBlock(BaseModel):
    dso: float
    dpo: float
    dio: float
    ccc: float
    nwc: float
    levels: list[_WcLevel]


class _TopEntityDelta(BaseModel):
    name: Optional[str] = None
    rank: Optional[int] = None
    cm: Optional[float] = None
    delta_cm_py: Optional[float] = None
    delta_ytd: Optional[float] = None


class _PerformanceRevenue(BaseModel):
    cm: float
    cm_py: float
    yoy_pct: Optional[float] = None
    has_plan: bool = False
    plan_cm: Optional[float] = None
    plan_vs_actual: Optional[float] = None
    var_pct: Optional[float] = None
    coverage_pct: Optional[float] = None


class _PerformanceGrossMargin(BaseModel):
    pct: Optional[float] = None
    yoy_pp: Optional[float] = None
    plan_pct: Optional[float] = None
    plan_vs_actual_pp: Optional[float] = None


class _PerformanceEbit(BaseModel):
    cm: float
    margin_pct: Optional[float] = None


class _PerformanceBlock(BaseModel):
    revenue: _PerformanceRevenue
    gross_margin: _PerformanceGrossMargin
    ebit: _PerformanceEbit


class OverviewSummaryResponse(BaseModel):
    """Batched Overview summary — hero + WC + cash + top-entities + DuPont + performance + alerts."""

    meta: dict[str, Any]
    hero: _HeroBlock
    working_capital: _WorkingCapitalBlock
    cash: _CashKpi
    top_customer: Optional[_TopEntityDelta] = None
    top_supplier: Optional[_TopEntityDelta] = None
    dupont: Optional[dict[str, Any]] = None
    performance: _PerformanceBlock
    alerts: list[dict[str, Any]] = []


@router.get("/overview/summary", response_model=OverviewSummaryResponse)
def get_financials_overview_summary(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
) -> dict:
    """OverviewSummaryResponse — ONE round-trip for the redesigned Overview page.

    Returns hero KPIs (Revenue YoY / EBIT+margin / Cash headline / CCC+NWC),
    working-capital ratios + the 3 deep-dive level rows (Δmonth, Δfy), the signed
    cash headline, top customer/supplier deltas, DuPont finding inputs (existing
    EBIT-based values only) and recent-months alerts — replacing ~6 client calls.

    SECURITY (fail-closed tenant isolation): ``visible_entity_codes()`` is resolved
    here, mapped to the ``entity_prefix`` set, and injected into EVERY sub-query.
    Non-admin with empty visibility → a zeroed summary (no cross-entity data, no
    500); admin → full.  See app/services/overview_summary.py.
    """
    from app.services.overview_summary import (
        build_overview_summary,
        map_codes_to_prefixes,
    )

    allowed_codes = visible_entity_codes(session, _user)
    try:
        allowed_prefixes = map_codes_to_prefixes(session, allowed_codes)
    except Exception:
        # Fail-closed (align with visible_entity_codes contract): a dim_legal_entity
        # lookup failure for a non-admin denies all (empty set → zeroed summary),
        # NEVER a 500 and NEVER widened to None/all.  Admin (None codes) is resolved
        # without a query, so it stays None (unrestricted) here.
        logger.exception("overview/summary prefix mapping failed — failing closed")
        allowed_prefixes = None if allowed_codes is None else set()
    try:
        return build_overview_summary(
            session,
            entity=entity,
            year=year,
            month=month,
            allowed_prefixes=allowed_prefixes,
        )
    except Exception as exc:
        logger.exception("overview/summary error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/overview/partners  (Overview v2 — Areas 4 & 5)
# ---------------------------------------------------------------------------
# ONE round-trip feeding both the CustomerBlock (Area 4) and SupplierBlock
# (Area 5).  Math lives in app/services/partner_development.py (fail-closed);
# this handler only resolves + injects the entity-visibility boundary.
# Row lists are typed as list[dict] (not strict row models) so the EXACT field
# names build_customer_development / build_supplier_development return survive to
# the frontend types (customer/supplier rows differ field-by-field).


class _CustomerDevelopment(BaseModel):
    """build_customer_development shape (Area 4) — biggest / increase / won / lost."""

    period: dict[str, Any]
    id_field: str
    biggest: list[dict[str, Any]] = []
    increase: list[dict[str, Any]] = []
    won: list[dict[str, Any]] = []
    lost: list[dict[str, Any]] = []


class _SupplierDevelopment(BaseModel):
    """build_supplier_development shape (Area 5) — biggest / increase / won (no lost)."""

    period: dict[str, Any]
    id_field: str
    biggest: list[dict[str, Any]] = []
    increase: list[dict[str, Any]] = []
    won: list[dict[str, Any]] = []


class OverviewPartnersResponse(BaseModel):
    """Batched partner development — one payload for the Customer + Supplier blocks."""

    customers: _CustomerDevelopment
    suppliers: _SupplierDevelopment


@router.get("/overview/partners", response_model=OverviewPartnersResponse)
def get_financials_overview_partners(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    top_n: int = Query(10, ge=1, le=100),
) -> dict:
    """OverviewPartnersResponse — ONE round-trip for the Customer + Supplier blocks.

    Returns ``{customers: build_customer_development, suppliers:
    build_supplier_development}`` (see app/services/partner_development.py for the
    exact per-row shapes and sign/period conventions).

    SECURITY (fail-closed tenant isolation, SAME pattern as /overview/summary):
    ``visible_entity_codes()`` is resolved, mapped to the ``entity_prefix`` set
    (:func:`overview_summary.map_codes_to_prefixes`), intersected with the requested
    ``entity`` (:func:`overview_summary._effective_prefixes`) and injected as
    ``allowed_entities`` into BOTH service calls.  Non-admin with empty/unmappable
    visibility (or an ``entity`` narrow outside it) → a zeroed partners payload (no
    cross-entity data, no 500, no service DB run); admin → unrestricted (None).
    """
    from app.services.overview_summary import (
        _effective_prefixes,
        map_codes_to_prefixes,
    )
    from app.services.partner_development import (
        _empty_development,
        build_customer_development,
        build_supplier_development,
    )

    allowed_codes = visible_entity_codes(session, _user)
    try:
        allowed_prefixes = map_codes_to_prefixes(session, allowed_codes)
        eff, builder_entity, denied = _effective_prefixes(
            session, entity=entity, allowed_prefixes=allowed_prefixes,
        )
    except Exception:
        # Fail-closed (align with /overview/summary + visible_entity_codes): a
        # dim_legal_entity lookup failure for a non-admin denies all → zeroed 200,
        # NEVER a 500 and NEVER widened to None/all.  Admin (None codes) stays admin.
        logger.exception("overview/partners visibility mapping failed — failing closed")
        if allowed_codes is None:
            eff, builder_entity, denied = None, (entity or None), False
        else:
            eff, builder_entity, denied = set(), None, True

    if denied:
        # Deny-all short-circuits BEFORE any service call → no cross-entity data,
        # no service DB run; reuse the builders' own zeroed shape (period + empties).
        return {
            "customers": _empty_development(year, month, "customer_id", with_lost=True),
            "suppliers": _empty_development(year, month, "supplier_id", with_lost=False),
        }

    try:
        customers = build_customer_development(
            session, entity=builder_entity, year=year, month=month,
            allowed_entities=eff, top_n=top_n,
        )
        suppliers = build_supplier_development(
            session, entity=builder_entity, year=year, month=month,
            allowed_entities=eff, top_n=top_n,
        )
    except Exception as exc:
        logger.exception("overview/partners error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"customers": customers, "suppliers": suppliers}


# ---------------------------------------------------------------------------
# GET /api/v1/financials/overview/liquidity  (Overview v2 — Area 2)
# ---------------------------------------------------------------------------
# Cash + AR collectibility haircut ("Zeitverkauf") + liquidity, feeding the P5
# Cash & Liquidity block.  Math + fail-closed intersection live in
# app/services/liquidity.py; this handler only resolves + injects the
# entity-visibility boundary, exactly like /overview/summary.


class OverviewLiquidityResponse(BaseModel):
    """build_liquidity_available shape (Area 2) — cash + collectible AR − AP.

    ``meta`` and ``ar_bands`` are loose dict/list[dict] so the EXACT field names
    the service returns (incl. the per-band ``credit_flag`` and the differing
    happy-path vs fail-closed ``meta`` keys) survive to the frontend types.
    """

    meta: dict[str, Any]
    cash: float
    ar_bands: list[dict[str, Any]] = []
    raw_ar: float
    collectible_ar: float
    outstanding_ap: float
    liquidity_available: float


@router.get("/overview/liquidity", response_model=OverviewLiquidityResponse)
def get_financials_overview_liquidity(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
) -> dict:
    """OverviewLiquidityResponse — Area-2 cash & liquidity ("Zeitverkauf").

    Returns ``build_liquidity_available`` (kEUR): the signed period-end cash level,
    the per-band AR collectibility breakdown, the aggregate collectible AR, the
    outstanding AP magnitude and ``liquidity_available = cash + collectible_ar −
    outstanding_ap`` (see app/services/liquidity.py for the signed-off formula,
    worked example and edge cases).

    SECURITY (fail-closed tenant isolation, SAME pattern as /overview/summary):
    ``visible_entity_codes()`` is resolved, mapped to the ``entity_prefix`` set
    (:func:`overview_summary.map_codes_to_prefixes`) and passed as
    ``allowed_entities``.  The service applies the ``entity`` intersection ONCE via
    its own ``_effective_prefixes`` (the endpoint passes the RAW mapped prefix set,
    NOT a pre-narrowed one, so the boundary is never double-applied).  A non-admin
    with empty/unmappable visibility (or an ``entity`` narrow outside it) → a zeroed
    liquidity payload (no cross-entity data, no 500); admin → unrestricted (None).
    """
    from app.config import settings
    from app.services.liquidity import build_liquidity_available
    from app.services.overview_summary import map_codes_to_prefixes

    allowed_codes = visible_entity_codes(session, _user)
    try:
        allowed_prefixes = map_codes_to_prefixes(session, allowed_codes)
    except Exception:
        # Fail-closed (align with /overview/summary + visible_entity_codes): a
        # dim_legal_entity lookup failure for a non-admin denies all (empty set →
        # the service's zeroed payload), NEVER a 500 and NEVER widened to None/all.
        # Admin (None codes) is resolved without a query, so it stays None.
        logger.exception("overview/liquidity prefix mapping failed — failing closed")
        allowed_prefixes = None if allowed_codes is None else set()
    try:
        return build_liquidity_available(
            session,
            entity=entity,
            year=year,
            month=month,
            allowed_entities=allowed_prefixes,
            use_mart=settings.overview_summary_use_mart,
        )
    except Exception as exc:
        logger.exception("overview/liquidity error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/financials/overview/bundle  (Overview v2 — Item 1 / Lever C)
# ---------------------------------------------------------------------------
# Collapses /overview/{summary,partners,liquidity} into ONE round-trip on ONE
# get_read_session — one visibility resolution, one txn — removing 2 network
# round-trips and repeated auth/visibility overhead.  REUSES the SAME builders
# the three granular endpoints call (no forked logic); the granular endpoints
# stay for the accordion lazy-loads.  Additive (reporting-v2 / :8011).


class OverviewBundleResponse(BaseModel):
    """Batched Overview bundle — summary + partners + liquidity in ONE payload."""

    summary: OverviewSummaryResponse
    partners: OverviewPartnersResponse
    liquidity: OverviewLiquidityResponse


@router.get("/overview/bundle", response_model=OverviewBundleResponse)
def get_financials_overview_bundle(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
    top_n: int = Query(10, ge=1, le=100),
) -> dict:
    """OverviewBundleResponse — summary + partners + liquidity in ONE round-trip.

    Computed on ONE ``get_read_session`` with ONE visibility resolution (one txn),
    delegating to the EXACT builders behind the three granular endpoints:
    ``build_overview_summary`` / ``build_customer_development`` +
    ``build_supplier_development`` / ``build_liquidity_available``.  The granular
    endpoints remain for the accordion lazy-loads.

    SECURITY (fail-closed tenant isolation, SAME pattern as the three granular
    endpoints): ``visible_entity_codes()`` is resolved ONCE, mapped to the
    ``entity_prefix`` set and threaded into every slice.  summary + liquidity take
    the RAW mapped prefixes (each applies its own ``entity`` intersection once);
    partners is narrowed here via ``_effective_prefixes`` exactly like
    /overview/partners.  A non-admin with empty/unmappable visibility (or an
    ``entity`` narrow outside it) → a zeroed bundle (no cross-entity data, no 500);
    admin → unrestricted (None).  The summary slice keeps its own visibility-aware
    TTL cache (keyed incl. the allowed-prefix hash); no additional bundle-level
    cache is introduced, so no cache key can cross tenants.
    """
    from app.services.overview_summary import (
        _effective_prefixes,
        build_overview_summary,
        map_codes_to_prefixes,
    )
    from app.services.partner_development import (
        _empty_development,
        build_customer_development,
        build_supplier_development,
    )
    from app.services.liquidity import build_liquidity_available
    from app.config import settings

    # ── ONE visibility resolution, reused by all three slices ─────────────────
    allowed_codes = visible_entity_codes(session, _user)
    try:
        allowed_prefixes = map_codes_to_prefixes(session, allowed_codes)
        eff, builder_entity, denied = _effective_prefixes(
            session, entity=entity, allowed_prefixes=allowed_prefixes,
        )
    except Exception:
        # Fail-closed (align with the granular endpoints + visible_entity_codes): a
        # dim_legal_entity lookup failure for a non-admin denies all → zeroed 200,
        # NEVER a 500 and NEVER widened to None/all.  Admin (None codes) stays admin.
        logger.exception("overview/bundle visibility mapping failed — failing closed")
        allowed_prefixes = None if allowed_codes is None else set()
        if allowed_codes is None:
            eff, builder_entity, denied = None, (entity or None), False
        else:
            eff, builder_entity, denied = set(), None, True

    try:
        # summary + liquidity take the RAW mapped prefixes; each fails closed on an
        # empty set / an out-of-visibility entity narrow via its own resolution.
        summary = build_overview_summary(
            session, entity=entity, year=year, month=month,
            allowed_prefixes=allowed_prefixes,
        )
        liquidity = build_liquidity_available(
            session, entity=entity, year=year, month=month,
            allowed_entities=allowed_prefixes,
            use_mart=settings.overview_summary_use_mart,
        )
        # partners: deny-all short-circuits BEFORE any service call (reuse the
        # builders' own zeroed shape) — identical to /overview/partners.
        if denied:
            partners = {
                "customers": _empty_development(year, month, "customer_id", with_lost=True),
                "suppliers": _empty_development(year, month, "supplier_id", with_lost=False),
            }
        else:
            partners = {
                "customers": build_customer_development(
                    session, entity=builder_entity, year=year, month=month,
                    allowed_entities=eff, top_n=top_n,
                ),
                "suppliers": build_supplier_development(
                    session, entity=builder_entity, year=year, month=month,
                    allowed_entities=eff, top_n=top_n,
                ),
            }
    except Exception as exc:
        logger.exception("overview/bundle error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"summary": summary, "partners": partners, "liquidity": liquidity}


# ===========================================================================
# Anomaly detection (reporting-v2 Phase 6) — additive, request-time compute
# ===========================================================================
# Scans the built PL/BS/WC/CF statements for material MoM/YoY swings, sign flips,
# BS balance breaks and GL concentration, reusing fin_compat_narrative_core
# thresholds.  Compute on request is the source of truth; results are optionally
# cached into fact_anomaly.  This endpoint is NOT in the golden catalogue and
# changes no existing payload.


# ---------------------------------------------------------------------------
# GET /api/v1/financials/anomalies
# ---------------------------------------------------------------------------
@router.get("/anomalies")
def get_financials_anomalies(
    _user: _UserDep,
    session: _SessionDep,
    period_grain: str = Query("month", pattern="^(month|week|year)$"),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None),
    entity: Optional[str] = Query(None),
) -> dict:
    """AnomalyResponse — deterministic anomalies over PL/BS/WC/CF for the period.

    Returns ``{ period, entity, anomalies: [...] }`` ordered by severity then
    magnitude.  PURE READ: request-time compute is the source of truth and this
    endpoint NEVER writes to ``fact_anomaly``.  Caching (DELETE+INSERT on
    ``fact_anomaly``) is intentionally NOT reachable from this GET — it must only be
    driven by an explicit admin/internal warm path (``cache_anomalies``), so a
    non-admin user can never trigger a write via a GET request.
    """
    _validate_period(period_grain, year, month, iso_year, iso_week)
    period = {
        "grain": period_grain, "year": year, "month": month,
        "iso_year": iso_year, "iso_week": iso_week,
    }
    try:
        anomalies = detect_anomalies(session, period, entity)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("anomalies error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    ent = entity if (entity and str(entity).strip().lower() not in ("", "all")) else "all"
    return {
        "period": {
            "grain": period_grain, "year": year, "month": month,
            "iso_year": iso_year, "iso_week": iso_week,
        },
        "entity": ent,
        "anomalies": [anomaly_to_api(a, period) for a in anomalies],
    }


# ===========================================================================
# Anomaly analysis tree endpoints (anomaly rework, Phase 4) — PARAM-FREE
# ===========================================================================
# The reworked Anomaly Detection page no longer takes entity/period/statement
# filters: the analyses ALWAYS look across all years and the entity split is
# shown INSIDE the charts/tables.  These GETs are read-through (compute-on-miss +
# cache per Phase 3); the consolidated scope is the WHOLE ledger for admins and
# the union of a restricted user's OWN entity prefixes otherwise (never
# cross-tenant).  None of these GETs writes to a statement/golden table — the only
# write is the idempotent ``anomaly_analysis_snapshot`` cache upsert in Phase 3.
#
# These REPLACE the earlier per-account/period ``/outliers`` /``/seasonality`` /
# ``/forensic`` handlers (superseded by the L3→L4→account→booking tree). The
# statement-level ``/anomalies`` list endpoint above is unchanged.


# ---------------------------------------------------------------------------
# Per-user visibility → ANALYSIS entity prefixes
# ---------------------------------------------------------------------------
def _anomaly_entity_prefixes(session: Session, user: User) -> Optional[list[str]]:
    """Map the user's visibility to the analysis scope as 2-char entity prefixes.

    - admin / unrestricted (``visible_entity_codes`` → None) → ``None`` (consolidated
      over ALL entities).
    - restricted user → the sorted set of ``entity_prefix`` values for the user's
      allowed ``legal_entity_code`` values (resolved via ``dim_legal_entity``, the
      same table :func:`resolve_entity_prefix` uses).

    The anomaly tree/forensic/overview orchestrators consolidate (sum) over the
    returned prefixes, so a restricted user with several entities sees the union of
    only their OWN entities — never the whole ledger / cross-tenant data.

    FAIL-CLOSED: a restricted user (non-None ``allowed``) who resolves to ZERO
    prefixes raises 403.  An empty list must NEVER be forwarded to the builders —
    they treat empty/None identically as "all entities" (``_normalise_prefixes``),
    so returning ``[]`` for a denied user would fall open to the WHOLE ledger.
    """
    allowed = visible_entity_codes(session, user)
    if allowed is None:
        return None  # admin / unrestricted → consolidated over all entities
    prefixes: set[str] = set()
    if allowed:
        rows = session.execute(
            text(
                "SELECT DISTINCT entity_prefix FROM dim_legal_entity "
                "WHERE legal_entity_code = ANY(:codes)"
            ),
            {"codes": sorted(allowed)},
        ).fetchall()
        prefixes = {str(r[0]).strip()[:2] for r in rows if r and r[0] is not None}
        prefixes.discard("")
    if not prefixes:
        # Restricted user with no resolvable entity prefixes → deny (never widen
        # to all entities / cross-tenant data).
        raise HTTPException(
            status_code=403,
            detail="You are not permitted to access any entities for this report.",
        )
    return sorted(prefixes)


# ---------------------------------------------------------------------------
# GET /api/v1/financials/anomalies/overview
# ---------------------------------------------------------------------------
@router.get("/anomalies/overview")
def get_anomalies_overview(_user: _UserDep, session: _SessionDep) -> dict:
    """Per-L3 overview cards (P&L then BS), each with outlier/seasonality/forensic
    flags {status, sentence, deep_link}.  Read-through (compute-on-miss + cache);
    returns the orchestrator payload plus ``cache_hit``.  PURE READ apart from the
    idempotent snapshot-cache upsert."""
    prefixes = _anomaly_entity_prefixes(session, _user)
    try:
        return anomaly_compute.get_overview(session, entity_prefixes=prefixes)
    except Exception:
        logger.exception("anomalies/overview error")
        raise HTTPException(
            status_code=500, detail="Internal error building overview."
        ) from None


# ---------------------------------------------------------------------------
# GET /api/v1/financials/anomalies/outliers
# ---------------------------------------------------------------------------
@router.get("/anomalies/outliers")
def get_anomalies_outliers(_user: _UserDep, session: _SessionDep) -> dict:
    """Hierarchical OUTLIER tree (L3→L4→account), all history, with per-node
    z-scores + severity.  Read-through (compute-on-miss + cache); returns the
    orchestrator payload plus ``cache_hit``."""
    prefixes = _anomaly_entity_prefixes(session, _user)
    try:
        return anomaly_compute.get_outliers(session, entity_prefixes=prefixes)
    except Exception:
        logger.exception("anomalies/outliers error")
        raise HTTPException(
            status_code=500, detail="Internal error building outliers."
        ) from None


# ---------------------------------------------------------------------------
# GET /api/v1/financials/anomalies/seasonality
# ---------------------------------------------------------------------------
@router.get("/anomalies/seasonality")
def get_anomalies_seasonality(_user: _UserDep, session: _SessionDep) -> dict:
    """Hierarchical SEASONALITY tree (L3→L4→account), all history, additively
    decomposed with per-node residual z-scores + severity.  Read-through
    (compute-on-miss + cache); returns the orchestrator payload plus ``cache_hit``."""
    prefixes = _anomaly_entity_prefixes(session, _user)
    try:
        return anomaly_compute.get_seasonality(session, entity_prefixes=prefixes)
    except Exception:
        logger.exception("anomalies/seasonality error")
        raise HTTPException(
            status_code=500, detail="Internal error building seasonality."
        ) from None


# ---------------------------------------------------------------------------
# GET /api/v1/financials/anomalies/forensic
# ---------------------------------------------------------------------------
@router.get("/anomalies/forensic")
def get_anomalies_forensic(_user: _UserDep, session: _SessionDep) -> dict:
    """Position-wise FORENSIC signals (unexpected counter accounts, Other positions,
    suspicious texts) rolled up to L3/L4 with explanations + entity split.
    Read-through (compute-on-miss + cache); returns the orchestrator payload plus
    ``cache_hit``."""
    prefixes = _anomaly_entity_prefixes(session, _user)
    try:
        return anomaly_compute.get_forensic(session, entity_prefixes=prefixes)
    except Exception:
        logger.exception("anomalies/forensic error")
        raise HTTPException(
            status_code=500, detail="Internal error building forensic."
        ) from None


# ---------------------------------------------------------------------------
# GET /api/v1/financials/anomalies/bookings
# ---------------------------------------------------------------------------
# The lazy leaf of the drill: the largest bookings under one account over all
# history, scoped to the user's own entity prefixes.  LIVE (never cached); each
# row carries ``booking_line_id`` for the journal-entry-by-booking drill.
@router.get("/anomalies/bookings")
def get_anomalies_bookings(
    _user: _UserDep,
    session: _SessionDep,
    account_number_group: str = Query(..., min_length=1),
    limit: int = Query(200, ge=1, le=500),
) -> dict:
    """Largest bookings under one account (all history, ABS(amount) desc), scoped to
    the caller's own entity prefixes.  LIVE read (not cached); writes nothing."""
    ang = str(account_number_group or "").strip()
    if not ang:
        raise HTTPException(
            status_code=422, detail="account_number_group is required.",
        )
    prefixes = _anomaly_entity_prefixes(session, _user)
    try:
        return list_account_bookings(
            session, ang, entity_prefixes=prefixes, limit=limit,
        )
    except Exception:
        logger.exception("anomalies/bookings error")
        raise HTTPException(
            status_code=500, detail="Internal error listing bookings."
        ) from None


# ===========================================================================
# Budget granularity view — per-position multi-period historical actuals
# ===========================================================================
# Powers the Budget-chat granularity panel: per PLANNABLE reporting position (the
# same set behind getBudgetTree), a short historical actuals series so the FE can
# pick the granularity to plan at (position / L4 / account; month-24 or FY3+YTD).
# PURE READ, additive, NOT in the golden catalogue and changes no existing payload.
# Reuses gl_analysis_common.build_account_monthly_series (one series pull) +
# gl_hierarchy roll-ups + budget_service._load_positions + budget_positions; see
# app/services/granularity_view.py for the contract / windowing / sign rules.


# ---------------------------------------------------------------------------
# GET /api/v1/financials/budget/granularity-view
# ---------------------------------------------------------------------------
@router.get("/budget/granularity-view")
def get_budget_granularity_view(
    _user: _UserDep,
    session: _SessionDep,
    statement: str = Query("PL", pattern="^(PL|BS)$"),
    grain: str = Query("month", pattern="^(month|year)$"),
    entity: Optional[str] = Query(None, description="'' / 'all' = consolidated; a code = per-entity"),
) -> dict:
    """Per-position multi-period historical actuals for the Budget granularity view.

    grain='month' → the last 24 ledger months; grain='year' → the 3 most recent
    fiscal years (full-year) + a current-YTD column.  Each plannable position
    carries its presented series plus (where meaningful) L4 children and account
    breakdowns.  Entity-scoped via the budget/anomaly visibility helper
    (admin → consolidated; restricted → own prefixes, fail-closed)."""
    from app.services import granularity_view

    prefixes = _anomaly_entity_prefixes(session, _user)  # None=admin, else fail-closed set
    try:
        return granularity_view.build_granularity_view(
            session,
            statement=statement,
            grain=grain,
            entity=entity,
            entity_prefixes=set(prefixes) if prefixes is not None else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        logger.exception("budget/granularity-view error")
        raise HTTPException(
            status_code=500, detail="Internal error building granularity view."
        ) from None


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
