"""Meta compatibility endpoints.

Implements the exact legacy API paths consumed by the ported frontend:
  GET /api/v1/entities              → Entity[]
  GET /api/v1/metrics?metric=...    → { metric, period, year, month, iso_year, iso_week }
  GET /health/ready                 → { status, database }

No changes to the GDPdU schema — reads dim_legal_entity and fact_gl_entry only.
"""
from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import User, current_user
from app.db import engine, get_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["meta-compat"])


# ---------------------------------------------------------------------------
# GET /api/v1/entities
# ---------------------------------------------------------------------------
@router.get("/api/v1/entities")
def get_entities(
    _user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> list[dict]:
    """List all legal entities — matches legacy GET /api/v1/entities."""
    rows = session.execute(text(
        "SELECT legal_entity_code, entity_name, is_consolidation "
        "FROM dim_legal_entity ORDER BY entity_name"
    )).fetchall()
    return [
        {
            "legal_entity_code": r[0],
            "entity_name": r[1] or r[0],
            "is_consolidation": bool(r[2]),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# GET /api/v1/metrics
# ---------------------------------------------------------------------------
@router.get("/api/v1/metrics")
def get_metrics(
    metric: str = Query(..., description="latest_period | dupont | ebit_table"),
    entity: Optional[str] = Query(None, description="Legal entity code filter"),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
    period_grain: str = Query("month", description="month | week (ebit_table)"),
    iso_year: Optional[int] = Query(None),
    iso_week: Optional[int] = Query(None, ge=1, le=53),
    _user: Annotated[User, Depends(current_user)] = None,
    session: Annotated[Session, Depends(get_session)] = None,
) -> dict:
    """
    GET /api/v1/metrics?metric=...

    Implemented metrics:
      * latest_period[&entity=]
      * dupont&year=&month=[&entity=]                      → { metric, data: DuPontData }
      * ebit_table&year=&month=[&entity=]                  → { metric, data: EbitTableData }
      * ebit_table&period_grain=week&iso_year=&iso_week=   → { metric, data: EbitTableData }
    Other metric values return 400 with a clear message.
    """
    if metric == "latest_period":
        return _latest_period(session, entity)

    if metric == "dupont":
        if year is None or month is None:
            raise HTTPException(status_code=400, detail="year and month required for dupont")
        from app.services.overview_metrics import build_dupont
        try:
            return {"metric": metric, "data": build_dupont(session, entity, year, month)}
        except Exception as exc:  # noqa: BLE001
            logger.exception("metrics dupont error")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    if metric == "ebit_table":
        from app.services.overview_metrics import build_ebit_table, build_ebit_table_week
        try:
            if period_grain == "week":
                if iso_year is None or iso_week is None:
                    raise HTTPException(
                        status_code=400,
                        detail="iso_year and iso_week required for ebit_table week grain",
                    )
                return {"metric": metric, "data": build_ebit_table_week(session, entity, iso_year, iso_week)}
            if year is None or month is None:
                raise HTTPException(status_code=400, detail="year and month required for ebit_table")
            return {"metric": metric, "data": build_ebit_table(session, entity, year, month)}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("metrics ebit_table error")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    raise HTTPException(
        status_code=400,
        detail=f"Metric '{metric}' is not implemented. "
               "Available: latest_period, dupont, ebit_table.",
    )


def _latest_period(session: Session, entity: Optional[str]) -> dict:
    """
    Derive latest period from MAX(posting_date) in fact_gl_entry (entry_type='actual').
    Matches legacy services/period_grain.py:latest_gl_period().
    """
    ent_cond = ""
    if entity and entity.strip().lower() not in ("", "all"):
        # Resolve entity_prefix from legal_entity_code
        ep_row = session.execute(
            text("SELECT entity_prefix FROM dim_legal_entity WHERE legal_entity_code = :e LIMIT 1"),
            {"e": entity},
        ).fetchone()
        if ep_row:
            safe_ep = str(ep_row[0]).replace("'", "")[:2]
            ent_cond = f"AND e.entity_prefix = '{safe_ep}'"

    # reporting-v2 Phase 3: the synthetic net-profit equity bookings (gl_rows mode)
    # are posted at the FY year-end (Dec-31) purely to land in the cumulative BS
    # balance; they are NOT real movements and must not drive the reporting anchor.
    # Excluding them keeps latest_period identical to report_inject / live (which
    # have no such rows) — matching this metric's intent (real 'actual' postings).
    row = session.execute(text(f"""
        SELECT
            TO_CHAR(MAX(e.posting_date), 'YYYY-MM')            AS period,
            EXTRACT(ISOYEAR FROM MAX(e.posting_date))::int      AS iso_year,
            EXTRACT(WEEK   FROM MAX(e.posting_date))::int       AS iso_week
        FROM fact_gl_entry e
        WHERE e.posting_date IS NOT NULL
          AND COALESCE(e.entry_type, '') <> 'net_profit' {ent_cond}
    """)).fetchone()

    if not row or not row[0]:
        return {
            "metric": "latest_period",
            "period": "",
            "year": None, "month": None,
            "iso_year": None, "iso_week": None,
        }

    period = row[0]
    y_s, m_s = period.split("-")
    return {
        "metric": "latest_period",
        "period": period,
        "year": int(y_s),
        "month": int(m_s),
        "iso_year": int(row[1]) if row[1] else None,
        "iso_week": int(row[2]) if row[2] else None,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/meta/reporting-availability
# ---------------------------------------------------------------------------
@router.get("/api/v1/meta/reporting-availability")
def get_reporting_availability(
    _user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    """Which optional reporting modules actually have data (FE hides empty tabs).

    GL + the statements (IS/BS/CF/WC) + Cash & debt are always shown.  Everything
    reported here is CONDITIONAL — the tab is hidden until its input is loaded:
      * profitability     — needs BOTH a customer AND a supplier master (partner-
                            level margins are meaningless without partner dims).
      * payroll / fixed_assets — a personnel / fixed-asset snapshot exists.
      * receivables_aging / payables_aging — OPOS debitor / kreditor rows exist
                            (reported separately so one aging page can show while
                            the other stays hidden — either side may be loaded on
                            its own or both together).
      * opos              — legacy convenience = receivables OR payables (kept so
                            older FE builds still work).
    Each probe is a cheap existence check wrapped in try/except → False on ANY
    error: a missing table / query failure must read as "not available" (the FE
    hides the tab) rather than 500 the whole endpoint.
    """
    receivables = _has_opos_side(session, "fact_opos_debitor")
    payables = _has_opos_side(session, "fact_opos_kreditor")
    return {
        "profitability": _has_profitability(session),
        "payroll": _has_payroll(session),
        "fixed_assets": _has_fixed_assets(session),
        "receivables_aging": receivables,
        "payables_aging": payables,
        "opos": receivables or payables,
    }


def _has_profitability(session: Session) -> bool:
    """True iff BOTH a customer AND a supplier master have rows — partner-level
    profitability needs both partner dimensions to attribute margins."""
    try:
        has_cust = session.execute(
            text("SELECT EXISTS(SELECT 1 FROM dim_customer)")
        ).scalar()
        has_supp = session.execute(
            text("SELECT EXISTS(SELECT 1 FROM dim_supplier)")
        ).scalar()
        return bool(has_cust) and bool(has_supp)
    except Exception:  # noqa: BLE001 — missing table → not available
        return False


def _has_payroll(session: Session) -> bool:
    """True iff any personnel snapshot exists (reuses list_snapshots)."""
    try:
        from app.services.personnel_accounting import list_snapshots
        return bool(list_snapshots(session))
    except Exception:  # noqa: BLE001
        return False


def _has_fixed_assets(session: Session) -> bool:
    """True iff any fixed-asset snapshot exists (reuses list_snapshots)."""
    try:
        from app.services.fixed_asset_rollforward import list_snapshots
        return bool(list_snapshots(session))
    except Exception:  # noqa: BLE001
        return False


def _has_opos_side(session: Session, table: str) -> bool:
    """True iff the given OPOS side (fact_opos_debitor | fact_opos_kreditor) has an
    open-item row for the default project.  Cheap EXISTS probe — does NOT run the
    full (expensive) aging computation.  Absent table → False (tab hidden)."""
    try:
        row = session.execute(text(
            f"SELECT EXISTS(SELECT 1 FROM {table} WHERE project_id = 'default')"
        )).fetchone()
        return bool(row and row[0])
    except Exception:  # noqa: BLE001 — missing table → not available
        return False


# ---------------------------------------------------------------------------
# GET /health/ready  (no auth — like existing /api/v1/health)
# ---------------------------------------------------------------------------
@router.get("/health/ready")
def health_ready() -> dict:
    """Readiness probe — checks DB connectivity. No auth required."""
    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "database": db_ok,
    }
