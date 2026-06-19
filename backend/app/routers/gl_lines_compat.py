"""GL journal lines compatibility endpoint.

Implements:
  GET /api/v1/facts/gl-journal-lines

Filterable, cursor-paginated GL lines backed by v_gl_line_enriched.
Ported from legacy routers/gl_lines.py.

Schema adaptations:
  - entity filter: legal_entity_code → dim_legal_entity → entity_prefix
  - gl_account_id: now a non-unique display column in dim_gl_account
  - statement_type param → a.level_0 filter
  - journal_entry_group_number replaces legal_entity_code+journal_entry_number
  - v_gl_line_enriched mirrors the enriched fields the legacy joined manually
"""
from __future__ import annotations

import base64
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import User, current_user
from app.db import get_session
from app.services.fin_compat_sql import resolve_entity_prefix
from app.services.fin_compat_trial_balance import build_trial_balance_export

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["gl-lines-compat"])

_DEFAULT_LIMIT = 100
_MAX_LIMIT = 1000


def _encode_cursor(booking_line_id: int) -> str:
    return base64.urlsafe_b64encode(str(booking_line_id).encode()).decode()


def _decode_cursor(cursor: str) -> int:
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc


@router.get("/facts/gl-journal-lines")
def get_gl_journal_lines(
    _user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    entity: Optional[str] = Query(None, description="Legal entity code e.g. Atlas"),
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD"),
    gl_account_id: Optional[str] = Query(None, description="Display gl_account_id (non-unique — filtered via dim_gl_account)"),
    journal_entry_number: Optional[str] = Query(None, description="Journal entry number (suffix of journal_entry_group_number)"),
    level_1: Optional[str] = Query(None, description="level_1 e.g. Income"),
    level_2: Optional[str] = Query(None, description="level_2 e.g. Net sales"),
    level_3: Optional[str] = Query(None, description="level_3 e.g. Domestic"),
    level_4: Optional[str] = Query(None),
    statement_type: Optional[str] = Query(None, description="PL or BS — maps to level_0"),
    customer_id: Optional[str] = Query(None),
    supplier_id: Optional[str] = Query(None),
    customer_name: Optional[str] = Query(None, description="Substring match on customer name"),
    supplier_name: Optional[str] = Query(None, description="Substring match on supplier name"),
    search: Optional[str] = Query(None, description="Free-text search on line_note"),
    sort_by: str = Query("date_desc", pattern="^(date_desc|date|amount_abs)$"),
    offset: int = Query(0, ge=0, description="Row offset (used with date_desc for paged tables)"),
    include_total: bool = Query(False, description="Include total_count of matching rows"),
    cursor: Optional[str] = Query(None, description="Opaque pagination cursor"),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
) -> dict:
    """
    Paginated GL lines with filters.

    Ported from legacy routers/gl_lines.py.
    Uses v_gl_line_enriched; statement_type maps to level_0.
    """
    conditions: list[str] = ["1=1"]
    params: dict = {}

    # Entity filter: legacy used l.legal_entity_code; we resolve to entity_prefix
    if entity and entity.strip().lower() not in ("", "all"):
        ep = resolve_entity_prefix(session, entity)
        if ep:
            conditions.append("v.entity_prefix = :entity_prefix")
            params["entity_prefix"] = ep

    if date_from:
        conditions.append("v.posting_date >= :date_from")
        params["date_from"] = date_from
    if date_to:
        conditions.append("v.posting_date <= :date_to")
        params["date_to"] = date_to
    if gl_account_id:
        # gl_account_id in the GDPdU schema is a non-unique display field on dim_gl_account
        conditions.append("v.gl_account_id = :gl_account_id")
        params["gl_account_id"] = gl_account_id
    if journal_entry_number and journal_entry_number.strip():
        conditions.append(
            "SUBSTRING(v.journal_entry_group_number FROM 3) = :journal_entry_number"
        )
        params["journal_entry_number"] = journal_entry_number.strip()
    if level_1:
        conditions.append("v.level_1 = :level_1")
        params["level_1"] = level_1
    if level_2:
        conditions.append("v.level_2 = :level_2")
        params["level_2"] = level_2
    if level_3:
        conditions.append("v.level_3 = :level_3")
        params["level_3"] = level_3
    if level_4:
        conditions.append("v.level_4 = :level_4")
        params["level_4"] = level_4
    if statement_type:
        # Legacy uses a.statement_type; GDPdU equivalent is level_0
        conditions.append("v.level_0 = :level_0")
        params["level_0"] = statement_type
    if customer_id:
        conditions.append("v.customer_id = :customer_id")
        params["customer_id"] = customer_id
    if supplier_id:
        conditions.append("v.supplier_id = :supplier_id")
        params["supplier_id"] = supplier_id
    if customer_name:
        conditions.append("CONCAT(c.name_line_1, ' ', COALESCE(c.name_line_2, '')) ILIKE :customer_name")
        params["customer_name"] = f"%{customer_name}%"
    if supplier_name:
        conditions.append("CONCAT(s.name_line_1, ' ', COALESCE(s.name_line_2, '')) ILIKE :supplier_name")
        params["supplier_name"] = f"%{supplier_name}%"
    if search:
        # line_note lives on fact_gl_line (not part of v_gl_line_enriched)
        conditions.append("fl.line_note ILIKE :search")
        params["search"] = f"%{search}%"

    use_cursor = sort_by == "date" and offset == 0
    use_offset = sort_by == "date_desc" or offset > 0
    if use_cursor and cursor:
        last_id = _decode_cursor(cursor)
        conditions.append("v.booking_line_id > :cursor_id")
        params["cursor_id"] = last_id

    if sort_by == "amount_abs":
        order_clause = "ABS(v.amount) DESC, v.booking_line_id"
    elif sort_by == "date_desc":
        order_clause = "v.posting_date DESC, v.booking_line_id DESC"
    else:
        order_clause = "v.booking_line_id"

    where = " AND ".join(conditions)
    params["fetch_limit"] = limit + 1 if not use_offset else limit
    if use_offset:
        params["row_offset"] = offset

    total_count: int | None = None
    if include_total:
        count_sql = text(f"""
            SELECT COUNT(*)::int
            FROM v_gl_line_enriched v
            LEFT JOIN fact_gl_line fl ON fl.booking_line_id = v.booking_line_id
            LEFT JOIN dim_customer c  ON c.customer_id = v.customer_id
            LEFT JOIN dim_supplier s  ON s.supplier_id = v.supplier_id
            WHERE {where}
        """)
        total_count = session.execute(count_sql, params).scalar()

    offset_clause = " OFFSET :row_offset" if use_offset else ""

    sql = text(f"""
        SELECT
            v.booking_line_id,
            v.legal_entity_code,
            v.fiscal_year,
            v.journal_entry_group_number,
            SUBSTRING(v.journal_entry_group_number FROM 3) AS journal_entry_number,
            v.line_number,
            v.posting_date::text                           AS posting_date,
            v.document_date::text                          AS document_date,
            v.reference_document_number,
            v.document_type_code,
            v.gl_account_id,
            v.account_name,
            v.level_0,
            v.level_1,
            v.level_2,
            v.level_3,
            v.level_4,
            v.level_0                                      AS statement_type,
            v.amount::float8                               AS amount_signed,
            fl.line_note                                   AS booking_text,
            v.posting_type,
            v.customer_id,
            v.supplier_id,
            NULLIF(TRIM(CONCAT(c.name_line_1, ' ', COALESCE(c.name_line_2, ''))), '') AS customer_name,
            NULLIF(TRIM(CONCAT(s.name_line_1, ' ', COALESCE(s.name_line_2, ''))), '') AS supplier_name
        FROM v_gl_line_enriched v
        LEFT JOIN fact_gl_line fl ON fl.booking_line_id = v.booking_line_id
        LEFT JOIN dim_customer c  ON c.customer_id = v.customer_id
        LEFT JOIN dim_supplier s  ON s.supplier_id = v.supplier_id
        WHERE {where}
        ORDER BY {order_clause}
        LIMIT :fetch_limit{offset_clause}
    """)

    try:
        rows = session.execute(sql, params).fetchall()
    except Exception as exc:
        logger.exception("gl-journal-lines query error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if use_offset:
        page = rows
        if total_count is not None:
            has_more = offset + len(page) < total_count
        else:
            has_more = len(page) >= limit
    else:
        has_more = len(rows) > limit
        page = rows[:limit]

    next_cursor = None
    if not use_offset and has_more and page:
        last_row = dict(page[-1]._mapping) if hasattr(page[-1], "_mapping") else dict(page[-1])
        next_cursor = _encode_cursor(int(last_row["booking_line_id"]))

    result_rows = []
    for r in page:
        d = dict(r._mapping) if hasattr(r, "_mapping") else dict(r)
        result_rows.append(d)

    out: dict = {
        "grain": "line",
        "total_returned": len(result_rows),
        "has_more": has_more,
        "next_cursor": next_cursor,
        "offset": offset if use_offset else None,
        "rows": result_rows,
    }
    if include_total and total_count is not None:
        out["total_count"] = total_count
    return out


@router.get("/facts/gl-journal-lines/filters")
def get_gl_journal_lines_filters(
    _user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    entity: Optional[str] = Query(None, description="Legal entity code e.g. Atlas"),
) -> dict:
    """Dropdown options and posting-date bounds for the Account statement page."""
    ep: str | None = None
    if entity and entity.strip().lower() not in ("", "all"):
        ep = resolve_entity_prefix(session, entity)

    ent_clause = "AND a.entity_prefix = :ep" if ep else ""
    params: dict = {}
    if ep:
        params["ep"] = ep

    bounds = session.execute(
        text(f"""
            SELECT
                MIN(e.posting_date)::text AS date_min,
                MAX(e.posting_date)::text AS date_max
            FROM fact_gl_entry e
            WHERE e.fiscal_period BETWEEN 1 AND 12
              {"AND e.entity_prefix = :ep" if ep else ""}
        """),
        params,
    ).fetchone()
    date_min = bounds[0] if bounds else None
    date_max = bounds[1] if bounds else None

    fy_row = session.execute(text("SELECT MAX(fiscal_year)::int FROM dim_gl_account")).fetchone()
    fiscal_year = int(fy_row[0]) if fy_row and fy_row[0] else None
    if fiscal_year:
        params["fy"] = fiscal_year

    accounts_sql = f"""
        SELECT DISTINCT
            a.gl_account_id,
            a.account_name,
            a.level_0,
            a.level_2,
            a.level_3
        FROM dim_gl_account a
        WHERE a.fiscal_year = :fy
          {ent_clause}
          AND a.gl_account_id IS NOT NULL
          AND TRIM(a.gl_account_id) <> ''
        ORDER BY a.gl_account_id
        LIMIT 8000
    """ if fiscal_year else None

    accounts = []
    if accounts_sql:
        for r in session.execute(text(accounts_sql), params).fetchall():
            accounts.append({
                "gl_account_id": r[0],
                "account_name": r[1] or "",
                "statement_type": r[2],
                "level_2": r[3],
                "level_3": r[4],
            })

    items_sql = f"""
        SELECT DISTINCT a.level_0, a.level_2, a.level_3
        FROM dim_gl_account a
        WHERE a.fiscal_year = :fy
          {ent_clause}
          AND a.level_0 IN ('PL', 'BS')
          AND a.level_2 IS NOT NULL
          AND TRIM(a.level_2) <> ''
        ORDER BY a.level_0, a.level_2, a.level_3 NULLS LAST
        LIMIT 2000
    """ if fiscal_year else None

    pl_bs_items = []
    if items_sql:
        for r in session.execute(text(items_sql), params).fetchall():
            pl_bs_items.append({
                "statement_type": r[0],
                "level_2": r[1],
                "level_3": r[2],
            })

    return {
        "date_min": date_min,
        "date_max": date_max,
        "fiscal_year": fiscal_year,
        "statement_types": ["PL", "BS"],
        "accounts": accounts,
        "pl_bs_items": pl_bs_items,
    }


@router.get("/facts/trial-balance-export")
def get_trial_balance_export(
    _user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    entity: Optional[str] = Query(None, description="Legal entity code e.g. Atlas"),
) -> dict:
    """Mapped trial balance (Summen- und Saldenliste) for PL_all / BS_all export."""
    try:
        return build_trial_balance_export(session, year, month, entity)
    except Exception:
        logger.exception("trial-balance-export error")
        raise HTTPException(status_code=500, detail="Could not build trial balance export") from None
