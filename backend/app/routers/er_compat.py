"""Exit-readiness compatibility endpoints.

Implements the legacy API path consumed by the ported frontend's
exit-readiness (annual / "Jahresscheiben") income-statement view:

  GET /api/v1/exit-readiness/pl-statement?year=&month=&entity=

The response is the legacy ErFlowResponse shape (FY/YTD/LTM columns) — see
legacy routers/exit_readiness.py::get_er_pl_statement (lines 539-714).  The
column maths and running-sum subtotal semantics live in
app/services/fin_compat_pl.py::build_pl_annual_compat and
app/services/fin_compat_sql.py::pl_grain_sql_annual.

Sign convention (audit trail): the single `amount * -1` inversion lives in the
SQL CASE WHEN expressions inside fin_compat_sql.py — never flip again here.
"""
from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import User, current_user
from app.db import get_read_session
from app.services.fin_compat_bs import build_bs_snapshot_annual
from app.services.fin_compat_cf import build_cf_annual_compat
from app.services.fin_compat_pl import (
    build_pl_annual_compat,
    build_pl_annual_consolidation,
)
from app.services.fin_compat_wc import build_wc_snapshot_annual

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/exit-readiness",
    tags=["exit-readiness-compat"],
)

_UserDep = Annotated[User, Depends(current_user)]
_SessionDep = Annotated[Session, Depends(get_read_session)]


# ---------------------------------------------------------------------------
# GET /api/v1/exit-readiness/pl-statement
# ---------------------------------------------------------------------------
@router.get("/pl-statement")
def get_er_pl_statement(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
) -> dict:
    """
    Annual P&L (exit-readiness "Jahresscheiben") — ErFlowResponse with FY/YTD/LTM
    columns and running-sum subtotals over the GDPdU GL schema.

    Ported from legacy routers/exit_readiness.py get_er_pl_statement().
    """
    try:
        return build_pl_annual_compat(session, year=year, month=month, entity=entity)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("exit-readiness/pl-statement error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/exit-readiness/pl-consolidation
# ---------------------------------------------------------------------------
@router.get("/pl-consolidation")
def get_er_pl_consolidation(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
) -> dict:
    """
    Annual entity breakdown (Consolidation) — per-legal-entity YTD columns for
    the anchor fiscal year (through ``month``) plus a Group total, in the legacy
    ConsolidationResponse shape.

    The breakdown always covers ALL entities, so the optional ``entity`` query
    parameter is accepted for URL symmetry with /pl-statement but ignored.
    Column maths and running-sum subtotal semantics live in
    fin_compat_pl.build_pl_annual_consolidation and
    fin_compat_sql.pl_consl_grain_sql_annual_ytd.
    """
    try:
        return build_pl_annual_consolidation(session, year=year, month=month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("exit-readiness/pl-consolidation error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/exit-readiness/cash-flow
# ---------------------------------------------------------------------------
@router.get("/cash-flow")
def get_er_cash_flow(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
) -> dict:
    """Annual cash flow (exit-readiness "Jahresscheiben") — ErFlowResponse with
    FY/YTD/LTM FLOW columns and running-sum subtotals over the GDPdU GL, mapped
    via dim_gl_cf.  CF is a FLOW (uses the annual flow grain, NOT a snapshot).

    Ported from legacy routers/exit_readiness.py get_er_cash_flow().
    """
    try:
        return build_cf_annual_compat(session, year=year, month=month, entity=entity)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("exit-readiness/cash-flow error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/exit-readiness/balance-sheet
# ---------------------------------------------------------------------------
@router.get("/balance-sheet")
def get_er_balance_sheet(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
) -> dict:
    """Annual balance-sheet snapshot (exit-readiness) — ErSnapshotResponse with
    cumulative balances at fy_py / fy / cm_py / cm, snapshot deltas (delta_fy,
    delta_cm), credit-side display flip and net profit in equity.

    Ported from legacy routers/exit_readiness.py get_er_balance_sheet().
    """
    try:
        return build_bs_snapshot_annual(session, year=year, month=month, entity=entity)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("exit-readiness/balance-sheet error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/v1/exit-readiness/working-capital
# ---------------------------------------------------------------------------
@router.get("/working-capital")
def get_er_working_capital(
    _user: _UserDep,
    session: _SessionDep,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None),
) -> dict:
    """Annual working-capital snapshot (exit-readiness) — ErSnapshotResponse with
    raw cumulative TWC/OWC balances at fy_py / fy / cm_py / cm, snapshot deltas
    (delta_fy, delta_cm), a Net working capital subtotal and DSO/DIO/DPO/CCC day
    KPIs (LTM denominators ending at each column's period date)."""
    try:
        return build_wc_snapshot_annual(session, year=year, month=month, entity=entity)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("exit-readiness/working-capital error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
