"""Per-user entity (Mandanten) visibility — the single source of truth.

The schema models row-level tenant isolation via ``user_role`` ×
``role_entity_visibility`` (a user, through their roles, is granted a set of
``legal_entity_code`` values).  This module resolves that allow-list for a user
so every data-read path can apply ONE identical, fail-closed rule:

    - admins                         → ``None``  (no restriction = all entities)
    - non-admin with grants          → the explicit set of granted codes
    - non-admin with ZERO grants     → EMPTY set (deny-all, fail-closed)
    - any lookup error (non-admin)   → EMPTY set (deny-all, fail-closed)

``None`` (unrestricted) is returned ONLY for admins.  A non-admin is always
scoped to exactly what their roles grant; a non-admin with no rows gets an EMPTY
set, never ``None`` — otherwise a user with no visibility rows would leak every
tenant's data.

Both :mod:`app.routers.fdd_bot` and :mod:`app.routers.financials_compat` import
:func:`visible_entity_codes` from here so the behaviour can never drift.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import User

logger = logging.getLogger(__name__)


def visible_entity_codes(session: Session, user: User) -> Optional[set[str]]:
    """Return the set of legal_entity_codes the *user* may see, or None for all.

    None means "no restriction" and is returned ONLY for admins. A non-admin is
    always scoped to exactly the codes their roles grant; a non-admin with zero
    grants returns an EMPTY set (deny-all, fail-closed), NOT None — otherwise a
    user with no visibility rows would leak every tenant's data.
    """
    if getattr(user, "is_admin", False):
        return None
    try:
        rows = session.execute(
            text(
                "SELECT DISTINCT rev.legal_entity_code "
                "FROM user_role ur "
                "JOIN role_entity_visibility rev ON rev.role_id = ur.role_id "
                "WHERE ur.user_id = :uid"
            ),
            {"uid": user.user_id},
        ).fetchall()
    except Exception:
        logger.exception("entity-visibility lookup failed; denying narrow access")
        # Fail closed for non-admins if the visibility tables are unavailable.
        return set()
    codes = {str(r[0]) for r in rows if r and r[0] is not None}
    # Non-admin with NO explicit grants → deny all (empty set), never unrestricted.
    return codes
