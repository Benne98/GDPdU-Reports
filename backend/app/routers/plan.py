"""DF5 — Plan / Forecast endpoints.

Prefix: /api/v1/plan

Endpoints:
  POST /generate   Read actuals from DB, call generate_plan, write fact_gl_plan +
                   fact_sales_plan, return row counts.
  GET  /summary    Row counts by scenario / fiscal_year for a quick status view.

Design rules:
  - Thin router; all computation in etl.plan_synth; all DB writes in etl.load.
  - Explicit Pydantic request/response models.
  - No secrets in logs or responses.
  - DB session via Depends(get_session).
"""
from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path
from typing import Any

# etl/ lives one level above backend/; add repo root to sys.path.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import User, current_user, require_admin
from app.db import get_session
from etl.load import load_plan
from etl.plan_synth import generate_plan

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/plan", tags=["plan"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class PlanGenerateRequest(BaseModel):
    """Parameters for the synthetic plan run."""

    base_fy: int = Field(..., description="Last full fiscal year of actuals (e.g. 2024)")
    current_fy: int = Field(..., description="Current/in-progress fiscal year (e.g. 2025)")
    last_closed_period: int = Field(
        ..., ge=0, le=12,
        description="Last closed period of current_fy (0 = none closed; 12 = fully closed)",
    )
    horizon_years: int = Field(4, ge=1, le=10, description="Number of plan years after the forecast year")
    growth_rate: float = Field(0.05, ge=-1.0, description="Default annual growth rate (e.g. 0.05 = 5%)")
    group_growth: dict[str, float] | None = Field(
        None,
        description="Per P&L-group growth override, keyed by group label (e.g. {'Umsatzerlöse': 0.08})",
    )
    group_col: str | None = Field(
        None,
        description="Column name in GL actuals used for group_growth lookup (e.g. 'level_2')",
    )
    forecast_growth_rate: float = Field(
        0.0,
        description="Growth applied on top of run-rate for open periods of current_fy (default 0 = pure run-rate)",
    )


class PlanGenerateResponse(BaseModel):
    gl_plan: int
    sales_plan: int
    com_plan: int = 0
    scenarios: list[str]
    message: str


class PlanSummaryRow(BaseModel):
    table: str
    scenario: str
    fiscal_year: int
    row_count: int


class PlanSummaryResponse(BaseModel):
    rows: list[PlanSummaryRow]


# --------------------------------------------------------------------------- #
# POST /generate
# --------------------------------------------------------------------------- #
@router.post("/generate", response_model=PlanGenerateResponse)
def plan_generate(
    body: PlanGenerateRequest,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> PlanGenerateResponse:
    """Read GL + sales actuals, synthesise plan/forecast, write to DB.

    GL actuals aggregation
    ----------------------
    Joins fact_gl_line to fact_gl_entry on (journal_entry_group_number, fiscal_year)
    to obtain fiscal_period, then aggregates amount per
    (account_number_group, fiscal_year, fiscal_period).  This is the exact shape
    that plan_synth.generate_plan / seasonal_index / forecast_open_periods expect.

    If body.group_col is supplied (e.g. 'level_2'), the corresponding column from
    dim_gl_account is also included so plan_years can apply per-group growth rates.

    Sales actuals aggregation
    -------------------------
    Joins fact_sales to fact_gl_entry for fiscal_period, aggregates gross_sales per
    (customer_id, fiscal_year, fiscal_period).

    Both queries filter fiscal_year >= base_fy - 1 (prior year needed for seasonal
    index + forecast run-rate) to avoid pulling the full history.
    """
    # ------------------------------------------------------------------ GL actuals
    try:
        gl_actuals = _fetch_gl_actuals(session, body.base_fy, body.group_col)
    except Exception as exc:
        logger.error("plan_generate: GL actuals query failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail="Failed to read GL actuals from DB. Check server logs.",
        ) from exc

    if gl_actuals.empty:
        raise HTTPException(
            status_code=422,
            detail=(
                f"No GL actuals found for base_fy={body.base_fy} or surrounding years. "
                "Load GL data first (POST /api/v1/ingest/commit)."
            ),
        )

    # ------------------------------------------------------------------ Sales actuals
    try:
        sales_actuals = _fetch_sales_actuals(session, body.base_fy)
    except Exception as exc:
        logger.error("plan_generate: sales actuals query failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail="Failed to read sales actuals from DB. Check server logs.",
        ) from exc

    # Sales actuals may be empty (no revenue lines yet) — generate_plan handles this gracefully.

    # ------------------------------------------------------------------ Com (supplier) actuals
    try:
        com_actuals = _fetch_com_actuals(session, body.base_fy)
    except Exception as exc:
        logger.error("plan_generate: com actuals query failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail="Failed to read supplier (com) actuals from DB. Check server logs.",
        ) from exc

    # Com actuals may be empty (no material lines yet) — generate_plan handles this gracefully.

    # ------------------------------------------------------------------ Generate
    try:
        gl_plan_df, sales_plan_df, com_plan_df = generate_plan(
            gl_actuals,
            sales_actuals,
            com_actuals,
            base_fy=body.base_fy,
            current_fy=body.current_fy,
            last_closed_period=body.last_closed_period,
            horizon_years=body.horizon_years,
            growth_rate=body.growth_rate,
            group_growth=body.group_growth,
            group_col=body.group_col,
            forecast_growth_rate=body.forecast_growth_rate,
        )
    except Exception as exc:
        logger.error("plan_generate: generate_plan failed: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail="Plan generation failed. Check server logs.",
        ) from exc

    if gl_plan_df.empty and sales_plan_df.empty and com_plan_df.empty:
        return PlanGenerateResponse(
            gl_plan=0,
            sales_plan=0,
            com_plan=0,
            scenarios=[],
            message="No plan rows generated (actuals present but produced empty output).",
        )

    # ------------------------------------------------------------------ Write to DB
    try:
        counts = load_plan(session, gl_plan_df, sales_plan_df, com_plan_df)
    except Exception as exc:
        # load_plan already rolled back
        logger.error("plan_generate: load_plan failed: %s", exc)
        short = str(exc).split("\n")[0][:200]
        raise HTTPException(
            status_code=500,
            detail=f"Plan load failed; transaction rolled back: {short}",
        ) from exc

    scenarios = sorted(
        set(gl_plan_df["scenario"].unique().tolist() if not gl_plan_df.empty else [])
        | set(sales_plan_df["scenario"].unique().tolist() if not sales_plan_df.empty else [])
        | set(com_plan_df["scenario"].unique().tolist() if not com_plan_df.empty else [])
    )
    return PlanGenerateResponse(
        gl_plan=counts["gl_plan"],
        sales_plan=counts["sales_plan"],
        com_plan=counts.get("com_plan", 0),
        scenarios=scenarios,
        message=(
            f"Plan generated: {counts['gl_plan']} GL rows, "
            f"{counts['sales_plan']} sales rows, "
            f"{counts.get('com_plan', 0)} com rows across scenarios {scenarios}."
        ),
    )


# --------------------------------------------------------------------------- #
# GET /summary
# --------------------------------------------------------------------------- #
@router.get("/summary", response_model=PlanSummaryResponse)
def plan_summary(
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> PlanSummaryResponse:
    """Return row counts of fact_gl_plan / fact_sales_plan by scenario + fiscal_year."""
    rows: list[PlanSummaryRow] = []
    try:
        gl_rows = session.execute(
            text(
                "SELECT scenario, fiscal_year, COUNT(*) AS cnt "
                "FROM fact_gl_plan "
                "GROUP BY scenario, fiscal_year "
                "ORDER BY fiscal_year, scenario"
            )
        ).fetchall()
        for r in gl_rows:
            rows.append(PlanSummaryRow(
                table="fact_gl_plan",
                scenario=r[0],
                fiscal_year=int(r[1]),
                row_count=int(r[2]),
            ))

        sp_rows = session.execute(
            text(
                "SELECT scenario, fiscal_year, COUNT(*) AS cnt "
                "FROM fact_sales_plan "
                "GROUP BY scenario, fiscal_year "
                "ORDER BY fiscal_year, scenario"
            )
        ).fetchall()
        for r in sp_rows:
            rows.append(PlanSummaryRow(
                table="fact_sales_plan",
                scenario=r[0],
                fiscal_year=int(r[1]),
                row_count=int(r[2]),
            ))

        # Manual budget (Phase 4) — count fact_position_plan rows by scenario/year.
        # The table is additive (migration 0013); guard so a DB without it (e.g.
        # the live 5176 profile) does not break the summary.
        try:
            bp_rows = session.execute(
                text(
                    "SELECT scenario, fiscal_year, COUNT(*) AS cnt "
                    "FROM fact_position_plan "
                    "GROUP BY scenario, fiscal_year "
                    "ORDER BY fiscal_year, scenario"
                )
            ).fetchall()
            for r in bp_rows:
                rows.append(PlanSummaryRow(
                    table="fact_position_plan",
                    scenario=r[0],
                    fiscal_year=int(r[1]),
                    row_count=int(r[2]),
                ))
        except Exception:  # noqa: BLE001 — table absent on profiles without 0013
            session.rollback()
    except Exception as exc:
        logger.error("plan_summary: query failed: %s", exc)
        short = str(exc).split("\n")[0][:200]
        raise HTTPException(status_code=500, detail=f"Database error: {short}") from exc

    return PlanSummaryResponse(rows=rows)


# --------------------------------------------------------------------------- #
# Private helpers
# --------------------------------------------------------------------------- #

def _fetch_gl_actuals(
    session: Session,
    base_fy: int,
    group_col: str | None,
) -> pd.DataFrame:
    """Aggregate fact_gl_line × fact_gl_entry into the shape expected by plan_synth.

    Returns columns: account_number_group, fiscal_year, fiscal_period, amount
    (and optionally the column named by group_col from dim_gl_account).

    Filters fiscal_year >= base_fy - 1 so prior-year seasonality data is included
    without pulling the full GL history.
    """
    # Build optional dim_gl_account join only when a group column is needed.
    if group_col and group_col in (
        "level_0", "level_1", "level_2", "level_3", "level_4", "l4_sub"
    ):
        extra_select = f", a.{group_col}"
        dim_join = (
            "LEFT JOIN dim_gl_account a "
            "  ON a.account_number_group = l.account_number_group "
            "  AND a.fiscal_year = l.fiscal_year"
        )
        group_by_extra = f", a.{group_col}"
    else:
        extra_select = ""
        dim_join = ""
        group_by_extra = ""

    sql = text(f"""
        SELECT
            l.account_number_group,
            l.fiscal_year,
            e.fiscal_period,
            SUM(l.amount) AS amount
            {extra_select}
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        {dim_join}
        WHERE l.fiscal_year >= :min_fy
          AND e.fiscal_period BETWEEN 1 AND 12
        GROUP BY l.account_number_group, l.fiscal_year, e.fiscal_period
                 {group_by_extra}
        ORDER BY l.account_number_group, l.fiscal_year, e.fiscal_period
    """)
    rows = session.execute(sql, {"min_fy": base_fy - 1}).fetchall()
    if not rows:
        return pd.DataFrame(columns=["account_number_group", "fiscal_year", "fiscal_period", "amount"])

    cols = ["account_number_group", "fiscal_year", "fiscal_period", "amount"]
    if group_col and group_by_extra:
        cols = cols + [group_col]
    df = pd.DataFrame(rows, columns=cols)
    df["fiscal_year"] = df["fiscal_year"].astype(int)
    df["fiscal_period"] = df["fiscal_period"].astype(int)
    df["amount"] = df["amount"].astype(float)
    return df


def _fetch_sales_actuals(session: Session, base_fy: int) -> pd.DataFrame:
    """Aggregate fact_sales × fact_gl_entry into customer_id × fiscal_year × fiscal_period.

    Returns columns: customer_id, fiscal_year, fiscal_period, gross_sales.
    Filters fiscal_year >= base_fy - 1.
    """
    sql = text("""
        SELECT
            s.customer_id,
            s.fiscal_year,
            e.fiscal_period,
            SUM(s.gross_sales) AS gross_sales
        FROM fact_sales s
        JOIN fact_gl_line l ON l.booking_line_id = s.booking_line_id
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        WHERE s.customer_id IS NOT NULL
          AND s.fiscal_year >= :min_fy
          AND e.fiscal_period BETWEEN 1 AND 12
        GROUP BY s.customer_id, s.fiscal_year, e.fiscal_period
        ORDER BY s.customer_id, s.fiscal_year, e.fiscal_period
    """)
    rows = session.execute(sql, {"min_fy": base_fy - 1}).fetchall()
    if not rows:
        return pd.DataFrame(columns=["customer_id", "fiscal_year", "fiscal_period", "gross_sales"])

    df = pd.DataFrame(rows, columns=["customer_id", "fiscal_year", "fiscal_period", "gross_sales"])
    df["fiscal_year"] = df["fiscal_year"].astype(int)
    df["fiscal_period"] = df["fiscal_period"].astype(int)
    df["gross_sales"] = df["gross_sales"].astype(float)
    return df


def _fetch_com_actuals(session: Session, base_fy: int) -> pd.DataFrame:
    """Aggregate fact_com × fact_gl_entry into supplier_id × fiscal_year × fiscal_period.

    Mirror of :func:`_fetch_sales_actuals` on the supplier/cost-of-materials side:
    joins fact_com → fact_gl_line (booking_line_id) → fact_gl_entry for fiscal_period,
    and aggregates the positive ``cost_of_materials`` measure per
    (supplier_id, fiscal_year, fiscal_period).

    Returns columns: supplier_id, fiscal_year, fiscal_period, cost_of_materials.
    Filters supplier_id IS NOT NULL and fiscal_year >= base_fy - 1.
    """
    sql = text("""
        SELECT
            c.supplier_id,
            c.fiscal_year,
            e.fiscal_period,
            SUM(c.cost_of_materials) AS cost_of_materials
        FROM fact_com c
        JOIN fact_gl_line l ON l.booking_line_id = c.booking_line_id
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        WHERE c.supplier_id IS NOT NULL
          AND c.fiscal_year >= :min_fy
          AND e.fiscal_period BETWEEN 1 AND 12
        GROUP BY c.supplier_id, c.fiscal_year, e.fiscal_period
        ORDER BY c.supplier_id, c.fiscal_year, e.fiscal_period
    """)
    rows = session.execute(sql, {"min_fy": base_fy - 1}).fetchall()
    if not rows:
        return pd.DataFrame(columns=["supplier_id", "fiscal_year", "fiscal_period", "cost_of_materials"])

    df = pd.DataFrame(rows, columns=["supplier_id", "fiscal_year", "fiscal_period", "cost_of_materials"])
    df["fiscal_year"] = df["fiscal_year"].astype(int)
    df["fiscal_period"] = df["fiscal_period"].astype(int)
    df["cost_of_materials"] = df["cost_of_materials"].astype(float)
    return df
