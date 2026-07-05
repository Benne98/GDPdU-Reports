"""Shared fail-closed entity-visibility scope for OPOS aging reads.

Single home for the aging entity-prefix scoping used by every aging endpoint
(``sales_compat.py`` register/analytics + ``metrics_compat.py`` ar_aging/ap_aging
series).  Extracted verbatim from ``sales_compat._aging_scope`` so both routers
share one behaviour-preserving implementation and a RESTRICTED user can never
receive all-entity (all-tenant) aging buckets.

Behaviour matrix (mirrors financials_compat.py /overview/*):
  * admin (visible_entity_codes → None)      → scope None (all entities, unchanged).
  * restricted user                          → own entity_prefix set.
  * ``entity`` narrow inside visibility      → that single prefix.
  * ``entity`` narrow OUTSIDE visibility     → deny (empty scope → zeroed, no SQL).
  * empty visibility / mapping error         → deny (empty scope → zeroed, no SQL).

The empty (fail-closed) scope is honoured inside ``opos_aging._partner_view``,
which short-circuits to a zeroed view BEFORE issuing any SQL.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy.orm import Session

from app.auth import User
from app.services.entity_visibility import visible_entity_codes
from app.services.opos_aging import aging_visibility

logger = logging.getLogger(__name__)


@contextmanager
def aging_scope(session: Session, user: User, entity: Optional[str]) -> Iterator[Optional[str]]:
    """Resolve fail-closed entity-prefix visibility and bind it for an aging read.

    Yields the ``entity`` the builder should use (``None`` for restricted users,
    who are scoped via the bound prefix set instead).  See module docstring for
    the full behaviour matrix.
    """
    from app.services.overview_summary import (
        _effective_prefixes,
        map_codes_to_prefixes,
    )

    allowed_codes = visible_entity_codes(session, user)
    try:
        allowed_prefixes = map_codes_to_prefixes(session, allowed_codes)
        eff, builder_entity, _denied = _effective_prefixes(
            session, entity=entity, allowed_prefixes=allowed_prefixes,
        )
    except Exception:
        # Fail closed (align with /overview/*): a dim_legal_entity lookup failure
        # for a non-admin denies all (empty scope → zeroed), NEVER a 500 and NEVER
        # widened to None/all.  Admin stays admin.
        logger.exception("aging visibility mapping failed — failing closed")
        if allowed_codes is None:
            eff, builder_entity = None, (entity or None)
        else:
            eff, builder_entity = set(), None

    with aging_visibility(eff):
        yield builder_entity
