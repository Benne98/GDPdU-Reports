"""P5 / Phase C1 — Statements endpoints.

Prefix: /api/v1/statements

Endpoints:
  GET /pl   Income statement (P&L) for the requested view_mode / entity / scenario.
  GET /bs   Balance sheet (cumulative balances; Assets = Liab + Equity identity).
  GET /wc   Working capital (NWC + DSO/DIO/DPO/CCC ratios).
  GET /cf   Cash flow (indirect method; CFO+CFI+CFF ties out to Δ Cash).

The service layer (app.services.{statements,balance_sheet,working_capital,cash_flow}
+ periods) holds all math; the period engine is shared as-is across all four.

Design rules:
  - Thin router: all period/aggregation math lives in app.services.*.
  - Protected with current_user (reuse app.auth).
  - Explicit Pydantic response models.
  - No secrets in logs or responses.
"""
from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import User, current_user
from app.db import get_session
from app.services.balance_sheet import build_bs_statement
from app.services.bookings import DEFAULT_LIMIT, MAX_LIMIT, build_bookings
from app.services.cash_flow import build_cf_statement
from app.services.consolidation import build_consolidation
from app.services.statements import build_pl_statement
from app.services.working_capital import build_wc_statement

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/statements", tags=["statements"])


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #
class StatementColumn(BaseModel):
    key: str
    label: str


class StatementCellModel(BaseModel):
    column_key: str
    value: Optional[float]


class StatementLineModel(BaseModel):
    line_code: str
    label: str
    row_type: str
    is_bold: bool
    kpi_code: Optional[str]
    cells: list[StatementCellModel]
    # Structure-level filters — passed back so the frontend can drill to GL bookings.
    level_2: Optional[str] = None
    level_3: Optional[str] = None
    level_4: Optional[str] = None


class PlResponse(BaseModel):
    view_mode: str
    coverage: float = Field(..., description="Fraction of current FY closed (0..1)")
    columns: list[StatementColumn]
    lines: list[StatementLineModel]
    unmapped_total: dict[str, float] = Field(
        ..., description="Σ presented over GL rows matching no line, per column (reconciliation)."
    )


class BsResponse(BaseModel):
    view_mode: str
    coverage: float
    columns: list[StatementColumn]
    lines: list[StatementLineModel]
    unmapped_total: dict[str, float] = Field(
        ..., description="Cumulative Σ over unmapped BS accounts, per column (reconciliation)."
    )
    imbalance: dict[str, float] = Field(
        ..., description="Assets − (Liabilities + Equity) per column; 0.0 when the identity ties out."
    )


class WcResponse(BaseModel):
    view_mode: str
    coverage: float
    columns: list[StatementColumn]
    lines: list[StatementLineModel]
    days_in_period: dict[str, float] = Field(
        ..., description="Days used to annualize the ratios per column (FY=365, YTD=L×365/12)."
    )


class CfResponse(BaseModel):
    view_mode: str
    coverage: float
    columns: list[StatementColumn]
    lines: list[StatementLineModel]
    tieout_residual: dict[str, float] = Field(
        ..., description="(CFO+CFI+CFF) − Δ Cash per column; 0.0 when the cash flow ties out."
    )


def _columns(stmt) -> list[StatementColumn]:
    return [StatementColumn(key=k, label=stmt.column_labels[k]) for k in stmt.column_keys]


def _lines(stmt) -> list[StatementLineModel]:
    return [
        StatementLineModel(
            line_code=ln.line_code,
            label=ln.label,
            row_type=ln.row_type,
            is_bold=ln.is_bold,
            kpi_code=ln.kpi_code,
            cells=[StatementCellModel(column_key=c.column_key, value=c.value) for c in ln.cells],
            level_2=getattr(ln, "level_2", None),
            level_3=getattr(ln, "level_3", None),
            level_4=getattr(ln, "level_4", None),
        )
        for ln in stmt.lines
    ]


# --------------------------------------------------------------------------- #
# GET /pl
# --------------------------------------------------------------------------- #
@router.get("/pl", response_model=PlResponse)
def get_pl(
    _user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    view_mode: str = Query("year", pattern="^(year|month|week)$"),
    current_fy: int = Query(..., description="Current/in-progress fiscal year (e.g. 2025)"),
    last_closed_period: int = Query(
        ..., ge=0, le=12, description="Last closed period of current_fy (0..12)"
    ),
    entity: Optional[str] = Query(
        None, min_length=2, max_length=2, description="2-char entity_prefix filter (e.g. '01')"
    ),
    scenario: Optional[str] = Query(
        None, description="Plan scenario (e.g. 'plan' or 'forecast'); omit for actuals"
    ),
    fy_start_month: int = Query(1, ge=1, le=12, description="First calendar month of the FY (month labels only)"),
) -> PlResponse:
    """Aggregate the P&L over the period columns for *view_mode*.

    Pure aggregation in app.services.statements.aggregate_pl; this router only
    validates inputs, calls the service, and shapes the response.
    """
    try:
        stmt = build_pl_statement(
            session,
            view_mode=view_mode,
            current_fy=current_fy,
            last_closed_period=last_closed_period,
            entity_prefix=entity,
            scenario=scenario,
            fy_start_month=fy_start_month,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("get_pl failed: %s", exc)
        short = str(exc).split("\n")[0][:200]
        raise HTTPException(status_code=500, detail=f"Statement build failed: {short}") from exc

    return PlResponse(
        view_mode=stmt.view_mode,
        coverage=stmt.coverage,
        columns=_columns(stmt),
        lines=_lines(stmt),
        unmapped_total=stmt.unmapped_total,
    )


# --------------------------------------------------------------------------- #
# Shared query params (kept inline; thin router)
# --------------------------------------------------------------------------- #
def _build_or_422_500(builder, name: str, **kwargs):
    """Run a statement builder, mapping validation errors → 422, others → 500."""
    try:
        return builder(**kwargs)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("%s failed: %s", name, exc)
        short = str(exc).split("\n")[0][:200]
        raise HTTPException(status_code=500, detail=f"Statement build failed: {short}") from exc


# --------------------------------------------------------------------------- #
# GET /bs — Balance sheet (cumulative)
# --------------------------------------------------------------------------- #
@router.get("/bs", response_model=BsResponse)
def get_bs(
    _user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    view_mode: str = Query("year", pattern="^(year|month|week)$"),
    current_fy: int = Query(..., description="Current/in-progress fiscal year (e.g. 2025)"),
    last_closed_period: int = Query(..., ge=0, le=12),
    entity: Optional[str] = Query(None, min_length=2, max_length=2),
    scenario: Optional[str] = Query(None),
    fy_start_month: int = Query(1, ge=1, le=12),
) -> BsResponse:
    """Cumulative balance sheet; reports the Assets = Liab + Equity imbalance per column."""
    stmt = _build_or_422_500(
        build_bs_statement, "get_bs",
        session=session, view_mode=view_mode, current_fy=current_fy,
        last_closed_period=last_closed_period, entity_prefix=entity,
        scenario=scenario, fy_start_month=fy_start_month,
    )
    return BsResponse(
        view_mode=stmt.view_mode, coverage=stmt.coverage,
        columns=_columns(stmt), lines=_lines(stmt),
        unmapped_total=stmt.unmapped_total, imbalance=stmt.imbalance,
    )


# --------------------------------------------------------------------------- #
# GET /wc — Working capital + ratios
# --------------------------------------------------------------------------- #
@router.get("/wc", response_model=WcResponse)
def get_wc(
    _user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    view_mode: str = Query("year", pattern="^(year|month|week)$"),
    current_fy: int = Query(...),
    last_closed_period: int = Query(..., ge=0, le=12),
    entity: Optional[str] = Query(None, min_length=2, max_length=2),
    scenario: Optional[str] = Query(None),
    fy_start_month: int = Query(1, ge=1, le=12),
) -> WcResponse:
    """Net working capital + DSO/DIO/DPO/CCC (ratios None on zero denominators)."""
    stmt = _build_or_422_500(
        build_wc_statement, "get_wc",
        session=session, view_mode=view_mode, current_fy=current_fy,
        last_closed_period=last_closed_period, entity_prefix=entity,
        scenario=scenario, fy_start_month=fy_start_month,
    )
    return WcResponse(
        view_mode=stmt.view_mode, coverage=stmt.coverage,
        columns=_columns(stmt), lines=_lines(stmt),
        days_in_period=stmt.days_in_period,
    )


# --------------------------------------------------------------------------- #
# GET /cf — Cash flow (indirect)
# --------------------------------------------------------------------------- #
@router.get("/cf", response_model=CfResponse)
def get_cf(
    _user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    view_mode: str = Query("year", pattern="^(year|month|week)$"),
    current_fy: int = Query(...),
    last_closed_period: int = Query(..., ge=0, le=12),
    entity: Optional[str] = Query(None, min_length=2, max_length=2),
    scenario: Optional[str] = Query(None),
    fy_start_month: int = Query(1, ge=1, le=12),
) -> CfResponse:
    """Indirect-method cash flow; reports the (CFO+CFI+CFF) − Δ Cash residual per column."""
    stmt = _build_or_422_500(
        build_cf_statement, "get_cf",
        session=session, view_mode=view_mode, current_fy=current_fy,
        last_closed_period=last_closed_period, entity_prefix=entity,
        scenario=scenario, fy_start_month=fy_start_month,
    )
    return CfResponse(
        view_mode=stmt.view_mode, coverage=stmt.coverage,
        columns=_columns(stmt), lines=_lines(stmt),
        tieout_residual=stmt.tieout_residual,
    )


# --------------------------------------------------------------------------- #
# Consolidation / entity breakdown response models
# --------------------------------------------------------------------------- #
class EntityBlockModel(BaseModel):
    entity_prefix: str
    entity_name: str
    lines: list[StatementLineModel]


class ConsolidationResponse(BaseModel):
    kind: str
    view_mode: str
    coverage: float
    columns: list[StatementColumn]
    entities: list[EntityBlockModel] = Field(
        ..., description="Per legal entity: the same line set with that entity's values."
    )
    consolidated: list[StatementLineModel] = Field(
        ..., description="Σ across entities per line per column (gross; no IC elimination)."
    )
    intercompany_eliminated: bool = Field(
        ..., description="Always False in C6 — intercompany elimination is out of scope (P10)."
    )


def _lines_from(lines) -> list[StatementLineModel]:
    return [
        StatementLineModel(
            line_code=ln.line_code,
            label=ln.label,
            row_type=ln.row_type,
            is_bold=ln.is_bold,
            kpi_code=ln.kpi_code,
            cells=[StatementCellModel(column_key=c.column_key, value=c.value) for c in ln.cells],
            level_2=getattr(ln, "level_2", None),
            level_3=getattr(ln, "level_3", None),
            level_4=getattr(ln, "level_4", None),
        )
        for ln in lines
    ]


# --------------------------------------------------------------------------- #
# GET /{kind}/consolidation — entity breakdown + consolidated total
# --------------------------------------------------------------------------- #
@router.get("/{kind}/consolidation", response_model=ConsolidationResponse)
def get_consolidation(
    _user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    kind: str,
    view_mode: str = Query("year", pattern="^(year|month|week)$"),
    current_fy: int = Query(..., description="Current/in-progress fiscal year (e.g. 2025)"),
    last_closed_period: int = Query(..., ge=0, le=12),
    scenario: Optional[str] = Query(None),
    fy_start_month: int = Query(1, ge=1, le=12),
) -> ConsolidationResponse:
    """Return *kind* (pl|bs|wc|cf) broken down by legal entity + the consolidated sum.

    Consolidated = Σ across entities per line per column.  Ratio/derived lines are
    recomputed from the consolidated base lines (not summed).  Intercompany
    elimination is out of scope (P10) — ``intercompany_eliminated`` is always False.
    """
    if kind not in ("pl", "bs", "wc", "cf"):
        raise HTTPException(status_code=422, detail=f"unknown kind {kind!r}; expected pl|bs|wc|cf")
    res = _build_or_422_500(
        build_consolidation, "get_consolidation",
        session=session, kind=kind, view_mode=view_mode, current_fy=current_fy,
        last_closed_period=last_closed_period, scenario=scenario, fy_start_month=fy_start_month,
    )
    return ConsolidationResponse(
        kind=res.kind,
        view_mode=res.view_mode,
        coverage=res.coverage,
        columns=[StatementColumn(key=k, label=res.column_labels[k]) for k in res.column_keys],
        entities=[
            EntityBlockModel(
                entity_prefix=e.entity_prefix,
                entity_name=e.entity_name,
                lines=_lines_from(e.lines),
            )
            for e in res.entities
        ],
        consolidated=_lines_from(res.consolidated),
        intercompany_eliminated=res.intercompany_eliminated,
    )


# --------------------------------------------------------------------------- #
# GL drill — cell → bookings
# --------------------------------------------------------------------------- #
class BookingRowModel(BaseModel):
    booking_line_id: int
    journal_entry_group_number: str
    fiscal_year: int
    fiscal_period: int
    line_number: int
    account_number_group: str
    account_name: Optional[str]
    level_2: Optional[str]
    level_3: Optional[str]
    level_4: Optional[str]
    posting_date: Optional[str]
    document_date: Optional[str]
    document_type_code: Optional[str]
    reference_document_number: Optional[str]
    line_note: Optional[str]
    entity_prefix: Optional[str]
    amount: float
    presented_amount: float


class BookingsResponse(BaseModel):
    kind: str
    column_key: str
    total_count: int = Field(..., description="Rows in the FULL filtered set (not just the page).")
    total: float = Field(
        ..., description="Σ presented_amount over the FULL set; == the statement cell value."
    )
    limit: int
    offset: int
    rows: list[BookingRowModel]


@router.get("/bookings", response_model=BookingsResponse)
def get_bookings(
    _user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    kind: str = Query(..., pattern="^(pl|bs)$", description="Statement kind: pl|bs"),
    col: str = Query(..., description="Statement column key (e.g. 'YTD', 'FY', '2025-05')"),
    view_mode: str = Query("year", pattern="^(year|month|week)$"),
    current_fy: int = Query(...),
    last_closed_period: int = Query(..., ge=0, le=12),
    level_2: Optional[str] = Query(None, description="Structure line level_2 filter"),
    level_3: Optional[str] = Query(None, description="Structure line level_3 filter"),
    level_4: Optional[str] = Query(None, description="Structure line level_4 filter"),
    account_number_group: Optional[str] = Query(
        None, max_length=8, description="Pin a single GL account group"
    ),
    bs_side: str = Query(
        "asset", pattern="^(asset|credit)$",
        description="BS sign side of the line (asset=+amount, credit=-amount); ignored for pl",
    ),
    entity: Optional[str] = Query(None, min_length=2, max_length=2),
    scenario: Optional[str] = Query(None),
    fy_start_month: int = Query(1, ge=1, le=12),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
) -> BookingsResponse:
    """Drill a statement CELL down to its GL booking lines.

    Maps a (line selector + column + entity) to the underlying GL lines, joined to
    fact_gl_entry + dim_gl_account.  ``total`` (Σ presented over the FULL filter)
    equals the statement cell value, so the modal reconciles to the cell.
    """
    res = _build_or_422_500(
        build_bookings, "get_bookings",
        session=session, kind=kind, column_key=col, view_mode=view_mode,
        current_fy=current_fy, last_closed_period=last_closed_period,
        level_2=level_2, level_3=level_3, level_4=level_4,
        account_number_group=account_number_group, bs_side=bs_side,
        entity_prefix=entity, scenario=scenario, fy_start_month=fy_start_month,
        limit=limit, offset=offset,
    )
    return BookingsResponse(
        kind=res.kind,
        column_key=res.column_key,
        total_count=res.total_count,
        total=res.total,
        limit=res.limit,
        offset=res.offset,
        rows=[
            BookingRowModel(
                booking_line_id=r.booking_line_id,
                journal_entry_group_number=r.journal_entry_group_number,
                fiscal_year=r.fiscal_year,
                fiscal_period=r.fiscal_period,
                line_number=r.line_number,
                account_number_group=r.account_number_group,
                account_name=r.account_name,
                level_2=r.level_2,
                level_3=r.level_3,
                level_4=r.level_4,
                posting_date=r.posting_date,
                document_date=r.document_date,
                document_type_code=r.document_type_code,
                reference_document_number=r.reference_document_number,
                line_note=r.line_note,
                entity_prefix=r.entity_prefix,
                amount=r.amount,
                presented_amount=r.presented_amount,
            )
            for r in res.rows
        ],
    )
