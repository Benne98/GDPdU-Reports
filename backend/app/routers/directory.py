"""Internal contact directory for Action Notes."""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import User, current_user
from app.db import get_session

router = APIRouter(prefix="/api/v1/directory", tags=["directory"])

_UserDep = Annotated[User, Depends(current_user)]
_SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/contacts")
def list_contacts(
    _user: _UserDep,
    session: _SessionDep,
    department: Optional[str] = Query(None),
):
    try:
        sql = """
            SELECT c.contact_id, c.display_name, c.email, c.department, c.role_title,
                   c.role_id, c.salutation_de, c.salutation_en,
                   r.role_key, r.display_name_de AS org_role_name,
                   r.seniority_level, r.email_tone_hint
            FROM dim_internal_contact c
            LEFT JOIN dim_org_role r ON r.role_id = c.role_id
            WHERE c.is_active = TRUE
        """
        params: dict = {}
        if department:
            sql += " AND c.department = :dept"
            params["dept"] = department
        sql += " ORDER BY c.department, c.sort_order, c.display_name"
        rows = session.execute(text(sql), params).fetchall()
        session.commit()
        return {"contacts": [dict(r._mapping) for r in rows]}
    except Exception as exc:
        session.rollback()
        msg = str(exc).lower()
        if "does not exist" in msg:
            raise HTTPException(503, detail="Directory not initialized — run: alembic upgrade head") from exc
        raise HTTPException(500, detail=str(exc)) from exc


@router.get("/contacts/{contact_id}")
def get_contact(_user: _UserDep, session: _SessionDep, contact_id: str):
    try:
        row = session.execute(
            text(
                """
                SELECT c.contact_id, c.display_name, c.email, c.department, c.role_title,
                       c.role_id, c.salutation_de, c.salutation_en,
                       r.role_key, r.display_name_de AS org_role_name,
                       r.seniority_level, r.email_tone_hint
                FROM dim_internal_contact c
                LEFT JOIN dim_org_role r ON r.role_id = c.role_id
                WHERE c.contact_id = :cid AND c.is_active = TRUE
                """
            ),
            {"cid": contact_id},
        ).fetchone()
        session.commit()
        if not row:
            raise HTTPException(404, detail="Contact not found")
        return dict(row._mapping)
    except HTTPException:
        raise
    except Exception as exc:
        session.rollback()
        raise HTTPException(500, detail=str(exc)) from exc
