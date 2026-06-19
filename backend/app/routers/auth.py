"""P4 Auth — login / me / logout endpoints.

Prefix: /api/v1/auth

Endpoints:
  POST /login   {email, password} -> {access_token, token_type, user}
  GET  /me      (Requires valid token) -> current user info.
  POST /logout  (Requires valid token) -> revoke session (DELETE auth_session row).

Security rules:
  - Login failures always return generic 401; never reveal which field was wrong.
  - Tokens and passwords are never logged or returned beyond the login response.
  - Auth logic stays in app.auth; this router is thin.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import (
    User,
    _pwd_context,
    create_access_token,
    current_user,
    decode_token,
    verify_password,
)
from app.config import settings
from app.db import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# Precomputed real bcrypt hash used on the unknown-email path so that both
# the found-user and not-found-user paths spend one full bcrypt verify cycle,
# eliminating the timing/enumeration oracle that a malformed hash would create.
_DUMMY_HASH: str = _pwd_context.hash("timing-equalizer")

_bearer = HTTPBearer(auto_error=False)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    user_id: int
    email: str
    display_name: str | None
    is_admin: bool


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---------------------------------------------------------------------------
# POST /login
# ---------------------------------------------------------------------------

@router.post("/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    session: Annotated[Session, Depends(get_session)],
) -> LoginResponse:
    """Authenticate with email + password; return a bearer token.

    Always returns HTTP 401 on failure — never reveals whether email or
    password was the problem.
    """
    _401 = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Look up user by email
    row = session.execute(
        text(
            "SELECT user_id, email, display_name, is_admin, is_active, password_hash "
            "FROM dim_user WHERE email = :email"
        ),
        {"email": body.email},
    ).fetchone()

    # Verify password even when user not found (timing mitigation via dummy verify).
    # _DUMMY_HASH is a real bcrypt hash computed at module import, so the not-found
    # path pays the same ~188 ms bcrypt cost as the found-user path.
    stored_hash = row[5] if row is not None else _DUMMY_HASH

    if not verify_password(body.password, stored_hash):
        raise _401

    # Reject inactive users after successful hash check (still generic 401)
    if row is None or not row[4]:  # row[4] = is_active
        raise _401

    user_id: int = row[0]
    email: str = row[1]
    display_name: str | None = row[2]
    is_admin: bool = row[3]

    # Create session row
    session_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.auth_token_ttl_minutes)

    session.execute(
        text(
            "INSERT INTO auth_session (session_id, user_id, created_at, expires_at) "
            "VALUES (:sid, :uid, :now, :exp)"
        ),
        {
            "sid": str(session_id),
            "uid": user_id,
            "now": now,
            "exp": expires_at,
        },
    )
    session.commit()

    token = create_access_token(user_id, session_id, expires_at)

    return LoginResponse(
        access_token=token,
        token_type="bearer",
        user=UserOut(
            user_id=user_id,
            email=email,
            display_name=display_name,
            is_admin=is_admin,
        ),
    )


# ---------------------------------------------------------------------------
# GET /me
# ---------------------------------------------------------------------------

@router.get("/me", response_model=UserOut)
def me(
    user: Annotated[User, Depends(current_user)],
) -> UserOut:
    """Return the currently authenticated user."""
    return UserOut(
        user_id=user.user_id,
        email=user.email,
        display_name=user.display_name,
        is_admin=user.is_admin,
    )


# ---------------------------------------------------------------------------
# POST /logout
# ---------------------------------------------------------------------------

@router.post("/logout", status_code=204, response_class=Response)
def logout(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Revoke the caller's session by deleting the auth_session row.

    Returns 204 on success. Returns 204 even if the token is already expired
    or the session is gone (idempotent revocation). Returns 401 only when
    no Authorization header is provided at all.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # Best-effort: decode to get the session_id, then delete.
    # If the token is expired we still try to clean up via sub claim.
    try:
        claims = decode_token(token)
        session_id_str = claims.get("sid")
        if session_id_str:
            session.execute(
                text("DELETE FROM auth_session WHERE session_id = :sid"),
                {"sid": session_id_str},
            )
            session.commit()
    except JWTError:
        # Token is invalid/tampered — nothing to revoke, but don't surface an error.
        pass
    return Response(status_code=204)
