"""Metrics compatibility — ar_aging / ap_aging for DuPont context panels."""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import User, current_user
from app.db import get_read_session
from app.services.aging_scope import aging_scope
from app.services.gl_aging import build_metrics_aging_series

router = APIRouter(prefix="/api/v1/metrics", tags=["metrics-compat"])

_UserDep = Annotated[User, Depends(current_user)]
# Aging series is a heavy full-FY OPOS read → bound with the read-session dep
# (SAME as sales_compat.py aging endpoints).
_ReadSessionDep = Annotated[Session, Depends(get_read_session)]


@router.get("")
def get_metric(
    _user: _UserDep,
    session: _ReadSessionDep,
    metric: str = Query(...),
    entity: Optional[str] = Query(None),
):
    if metric not in ("ar_aging", "ap_aging"):
        raise HTTPException(400, detail=f"Unsupported metric: {metric}")
    try:
        # Phase-2: ar_aging / ap_aging are served from the OPOS subledger as-of
        # variant (build_metrics_aging_series defaults source="opos").
        # Fail-closed entity-visibility scope (SAME as sales_compat aging): a
        # RESTRICTED user must never receive all-entity (all-tenant) buckets;
        # empty visibility short-circuits to a zeroed view with NO SQL.
        with aging_scope(session, _user, entity) as ent:
            series = build_metrics_aging_series(session, metric, ent)
        return {"metric": metric, "grain": "band", "series": series}
    except Exception as exc:
        raise HTTPException(500, detail=str(exc)) from exc
