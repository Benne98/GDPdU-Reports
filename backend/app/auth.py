"""P4 Auth — password hashing, JWT tokens, and FastAPI dependencies.

Token design
------------
Algorithm : HS256
Claims    : sub (user_id as str), sid (session_id UUID str), exp (UTC timestamp)
TTL       : settings.auth_token_ttl_minutes (default 480 = 8 h)
Revocation: every protected request verifies the auth_session row still exists
            and has not expired → server-side logout (DELETE session row) works
            immediately even within the token TTL.

Security notes
--------------
- Passwords are never logged or returned in responses.
- Token values are never logged.
- Auth failures return generic 401/403; the response never reveals which field
  (email vs. password) was wrong.
- AUTH_SECRET default "change-me-dev-only" is dev-only. Production MUST override
  AUTH_SECRET via environment variable.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import ExpiredSignatureError, JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Return a bcrypt hash of *password*."""
    return _pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches *hashed*; False otherwise. Never raises."""
    try:
        return _pwd_context.verify(plain, hashed)
    except Exception:  # noqa: BLE001 — never leak internal passlib errors
        return False


# ---------------------------------------------------------------------------
# JWT — create / decode
# ---------------------------------------------------------------------------

_ALGORITHM = "HS256"

# HTTPBearer parses "Authorization: Bearer <token>" and raises 403 if missing.
# We set auto_error=False so we can raise our own 401.
_bearer = HTTPBearer(auto_error=False)


def create_access_token(
    user_id: int,
    session_id: uuid.UUID,
    expires: datetime,
) -> str:
    """Encode a signed JWT.

    Args:
        user_id   : integer PK from dim_user.
        session_id: UUID from auth_session row (for revocation).
        expires   : absolute UTC expiry datetime.

    Returns:
        Signed HS256 JWT string.
    """
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "exp": expires,
    }
    return jwt.encode(payload, settings.auth_secret, algorithm=_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and verify a JWT.

    Returns the claims dict (keys: sub, sid, exp).
    Raises:
        jose.ExpiredSignatureError if the token has expired.
        jose.JWTError (or subclass) if the token is invalid/tampered.
    """
    # jose raises ExpiredSignatureError (subclass of JWTError) for expired tokens
    # and JWTError for any other problem (wrong signature, bad format, etc.).
    return jwt.decode(token, settings.auth_secret, algorithms=[_ALGORITHM])


# ---------------------------------------------------------------------------
# User representation (no ORM needed — plain dataclass)
# ---------------------------------------------------------------------------

@dataclass
class User:
    user_id: int
    email: str
    display_name: str | None
    is_admin: bool


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

def current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> User:
    """Dependency: decode JWT, verify auth_session, verify user is_active.

    Raises:
        401 if Authorization header is missing, token is invalid/expired,
            session not found, session expired, or user is not active.
    """
    _401 = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None:
        raise _401

    token = credentials.credentials

    # Decode — raises JWTError variants on failure
    try:
        claims = decode_token(token)
    except ExpiredSignatureError:
        raise _401
    except JWTError:
        raise _401

    user_id_str = claims.get("sub")
    session_id_str = claims.get("sid")
    if not user_id_str or not session_id_str:
        raise _401

    try:
        user_id = int(user_id_str)
    except (ValueError, TypeError):
        raise _401

    # Verify the auth_session row exists and has not expired
    now = datetime.now(timezone.utc)
    sess_row = session.execute(
        text(
            "SELECT session_id FROM auth_session "
            "WHERE session_id = :sid AND user_id = :uid AND expires_at > :now"
        ),
        {"sid": session_id_str, "uid": user_id, "now": now},
    ).fetchone()
    if sess_row is None:
        raise _401

    # Load the user
    user_row = session.execute(
        text(
            "SELECT user_id, email, display_name, is_admin, is_active "
            "FROM dim_user WHERE user_id = :uid"
        ),
        {"uid": user_id},
    ).fetchone()
    if user_row is None or not user_row[4]:  # user_row[4] = is_active
        raise _401

    return User(
        user_id=user_row[0],
        email=user_row[1],
        display_name=user_row[2],
        is_admin=user_row[3],
    )


def require_admin(
    user: Annotated[User, Depends(current_user)],
) -> User:
    """Dependency: require an active, authenticated admin user.

    Raises:
        403 if the authenticated user is not an admin.
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user
