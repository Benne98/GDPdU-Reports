"""Admin — role and user management endpoints.

Prefix  : /api/v1/admin
Auth    : every route requires Depends(require_admin)

Endpoints:
  GET    /pages            → static page catalog
  GET    /roles            → list all roles with page_keys + entity_codes
  POST   /roles            → create a role
  PUT    /roles/{id}       → replace a role's metadata, page_keys, entity_codes
  DELETE /roles/{id}       → delete a role (CASCADE clears junction rows)
  GET    /users            → list all users with role_ids
  PUT    /users/{id}       → update user roles / admin flag / active flag

Design notes:
- Pure raw SQL via text() — no ORM models, consistent with existing routers.
- array_agg (PostgreSQL) collapses junction rows in a single query per list op.
- entity_codes == [] means "all entities visible" (frontend contract).
- Self-lockout guard: an admin cannot demote or deactivate themselves.
"""
from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import User, require_admin
from app.db import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

# ---------------------------------------------------------------------------
# Static page catalog
# ---------------------------------------------------------------------------

_GROUP = Literal["reporting", "tools", "admin"]

# Canonical page catalog — order matters for the sidebar.
PAGES_CATALOG: list[dict] = [
    {"key": "overview",           "label": "Overview",          "group": "reporting"},
    {"key": "income-statement",   "label": "Income statement",  "group": "reporting"},
    {"key": "balance-sheet",      "label": "Balance sheet",     "group": "reporting"},
    {"key": "working-capital",    "label": "Working capital",   "group": "reporting"},
    {"key": "cash-flow",          "label": "Cash flow",         "group": "reporting"},
    {"key": "account-statement",  "label": "Account statement", "group": "reporting"},
    {"key": "fdd-bot",            "label": "FDD-Bot",           "group": "tools"},
    {"key": "ingestion",          "label": "Data Update",       "group": "tools"},
    {"key": "plan",               "label": "Plan / Forecast",   "group": "tools"},
    {"key": "role-management",    "label": "Role management",   "group": "admin"},
]


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class AdminPage(BaseModel):
    key: str
    label: str
    group: str


class AdminRole(BaseModel):
    role_id: int
    role_name: str
    description: str
    page_keys: list[str]
    entity_codes: list[str]


class RoleBody(BaseModel):
    role_name: str
    description: str = ""
    page_keys: list[str] = []
    entity_codes: list[str] = []


class AdminUser(BaseModel):
    user_id: int
    email: str
    display_name: str | None
    is_admin: bool
    is_active: bool
    role_ids: list[int]


class UserBody(BaseModel):
    role_ids: list[int] = []
    is_admin: bool
    is_active: bool


# ---------------------------------------------------------------------------
# Self-lockout guard (pure function — testable without DB)
# ---------------------------------------------------------------------------

def check_self_lockout(
    requesting_user_id: int,
    target_user_id: int,
    is_admin: bool,
    is_active: bool,
) -> None:
    """Raise HTTP 400 if the requesting admin tries to lock themselves out.

    An admin may not set their own is_admin=False or is_active=False, because
    that would prevent them from further managing the system.

    Args:
        requesting_user_id: user_id of the authenticated admin making the call.
        target_user_id    : user_id of the user record being updated.
        is_admin          : the is_admin value being written.
        is_active         : the is_active value being written.
    """
    if requesting_user_id == target_user_id and (not is_admin or not is_active):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Admins cannot demote or deactivate their own account. "
                "Ask another admin to do this."
            ),
        )


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------

_ROLE_SELECT = """
SELECT
    r.role_id,
    r.role_name,
    r.description,
    COALESCE(
        array_agg(DISTINCT rpv.page_key) FILTER (WHERE rpv.page_key IS NOT NULL),
        ARRAY[]::text[]
    ) AS page_keys,
    COALESCE(
        array_agg(DISTINCT rev.legal_entity_code) FILTER (WHERE rev.legal_entity_code IS NOT NULL),
        ARRAY[]::text[]
    ) AS entity_codes
FROM dim_role r
LEFT JOIN role_page_visibility  rpv ON rpv.role_id = r.role_id
LEFT JOIN role_entity_visibility rev ON rev.role_id = r.role_id
"""


def _load_role(session: Session, role_id: int) -> AdminRole:
    """Load a single role by ID; raise 404 if missing."""
    row = session.execute(
        text(_ROLE_SELECT + "WHERE r.role_id = :rid GROUP BY r.role_id, r.role_name, r.description"),
        {"rid": role_id},
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return AdminRole(
        role_id=row[0],
        role_name=row[1],
        description=row[2] or "",
        page_keys=list(row[3] or []),
        entity_codes=list(row[4] or []),
    )


def _replace_role_junctions(
    session: Session,
    role_id: int,
    page_keys: list[str],
    entity_codes: list[str],
) -> None:
    """Delete then re-insert role_page_visibility and role_entity_visibility rows."""
    session.execute(
        text("DELETE FROM role_page_visibility WHERE role_id = :rid"),
        {"rid": role_id},
    )
    for key in page_keys:
        session.execute(
            text(
                "INSERT INTO role_page_visibility (role_id, page_key) "
                "VALUES (:rid, :key) ON CONFLICT DO NOTHING"
            ),
            {"rid": role_id, "key": key},
        )

    session.execute(
        text("DELETE FROM role_entity_visibility WHERE role_id = :rid"),
        {"rid": role_id},
    )
    for code in entity_codes:
        session.execute(
            text(
                "INSERT INTO role_entity_visibility (role_id, legal_entity_code) "
                "VALUES (:rid, :code) ON CONFLICT DO NOTHING"
            ),
            {"rid": role_id, "code": code},
        )


# ---------------------------------------------------------------------------
# GET /pages
# ---------------------------------------------------------------------------

@router.get("/pages")
def list_pages(
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    """Return the static page catalog."""
    return {"pages": PAGES_CATALOG}


# ---------------------------------------------------------------------------
# GET /roles
# ---------------------------------------------------------------------------

@router.get("/roles")
def list_roles(
    _: Annotated[User, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    """Return all roles with their page_keys and entity_codes."""
    rows = session.execute(
        text(_ROLE_SELECT + "GROUP BY r.role_id, r.role_name, r.description ORDER BY r.role_id"),
    ).fetchall()
    roles = [
        AdminRole(
            role_id=r[0],
            role_name=r[1],
            description=r[2] or "",
            page_keys=list(r[3] or []),
            entity_codes=list(r[4] or []),
        )
        for r in rows
    ]
    return {"roles": [role.model_dump() for role in roles]}


# ---------------------------------------------------------------------------
# POST /roles
# ---------------------------------------------------------------------------

@router.post("/roles", status_code=status.HTTP_201_CREATED)
def create_role(
    body: RoleBody,
    _: Annotated[User, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    """Create a new role and return it."""
    row = session.execute(
        text(
            "INSERT INTO dim_role (role_name, description) "
            "VALUES (:name, :desc) "
            "RETURNING role_id"
        ),
        {"name": body.role_name, "desc": body.description},
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=500, detail="Role creation failed")
    role_id: int = row[0]

    _replace_role_junctions(session, role_id, body.page_keys, body.entity_codes)
    session.commit()

    return _load_role(session, role_id).model_dump()


# ---------------------------------------------------------------------------
# PUT /roles/{id}
# ---------------------------------------------------------------------------

@router.put("/roles/{role_id}")
def update_role(
    role_id: int,
    body: RoleBody,
    _: Annotated[User, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    """Replace a role's metadata, page_keys, and entity_codes."""
    result = session.execute(
        text(
            "UPDATE dim_role SET role_name = :name, description = :desc "
            "WHERE role_id = :rid"
        ),
        {"name": body.role_name, "desc": body.description, "rid": role_id},
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Role not found")

    _replace_role_junctions(session, role_id, body.page_keys, body.entity_codes)
    session.commit()

    return _load_role(session, role_id).model_dump()


# ---------------------------------------------------------------------------
# DELETE /roles/{id}
# ---------------------------------------------------------------------------

@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_role(
    role_id: int,
    _: Annotated[User, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Delete a role. CASCADE removes user_role, role_entity_visibility, role_page_visibility rows."""
    result = session.execute(
        text("DELETE FROM dim_role WHERE role_id = :rid"),
        {"rid": role_id},
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Role not found")
    session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# GET /users
# ---------------------------------------------------------------------------

@router.get("/users")
def list_users(
    _: Annotated[User, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    """Return all users with their role_ids."""
    rows = session.execute(
        text(
            "SELECT "
            "  u.user_id, u.email, u.display_name, u.is_admin, u.is_active, "
            "  COALESCE("
            "    array_agg(ur.role_id) FILTER (WHERE ur.role_id IS NOT NULL),"
            "    ARRAY[]::integer[]"
            "  ) AS role_ids "
            "FROM dim_user u "
            "LEFT JOIN user_role ur ON ur.user_id = u.user_id "
            "GROUP BY u.user_id, u.email, u.display_name, u.is_admin, u.is_active "
            "ORDER BY u.user_id"
        )
    ).fetchall()
    users = [
        AdminUser(
            user_id=r[0],
            email=r[1],
            display_name=r[2],
            is_admin=r[3],
            is_active=r[4],
            role_ids=list(r[5] or []),
        )
        for r in rows
    ]
    return {"users": [u.model_dump() for u in users]}


# ---------------------------------------------------------------------------
# PUT /users/{id}
# ---------------------------------------------------------------------------

@router.put("/users/{user_id}")
def update_user(
    user_id: int,
    body: UserBody,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    """Update user roles, admin flag, and active flag.

    Rejects the request with 400 if the requesting admin tries to demote or
    deactivate their own account (lockout prevention).
    """
    check_self_lockout(admin.user_id, user_id, body.is_admin, body.is_active)

    result = session.execute(
        text(
            "UPDATE dim_user SET is_admin = :is_admin, is_active = :is_active "
            "WHERE user_id = :uid"
        ),
        {"is_admin": body.is_admin, "is_active": body.is_active, "uid": user_id},
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="User not found")

    # Replace role assignments
    session.execute(
        text("DELETE FROM user_role WHERE user_id = :uid"),
        {"uid": user_id},
    )
    for role_id in body.role_ids:
        session.execute(
            text(
                "INSERT INTO user_role (user_id, role_id) VALUES (:uid, :rid) "
                "ON CONFLICT DO NOTHING"
            ),
            {"uid": user_id, "rid": role_id},
        )
    session.commit()

    # Reload user from DB to return the canonical state
    row = session.execute(
        text(
            "SELECT "
            "  u.user_id, u.email, u.display_name, u.is_admin, u.is_active, "
            "  COALESCE("
            "    array_agg(ur.role_id) FILTER (WHERE ur.role_id IS NOT NULL),"
            "    ARRAY[]::integer[]"
            "  ) AS role_ids "
            "FROM dim_user u "
            "LEFT JOIN user_role ur ON ur.user_id = u.user_id "
            "WHERE u.user_id = :uid "
            "GROUP BY u.user_id, u.email, u.display_name, u.is_admin, u.is_active"
        ),
        {"uid": user_id},
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")

    return AdminUser(
        user_id=row[0],
        email=row[1],
        display_name=row[2],
        is_admin=row[3],
        is_active=row[4],
        role_ids=list(row[5] or []),
    ).model_dump()
