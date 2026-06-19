"""Metrics compatibility — ar_aging / ap_aging for DuPont context panels."""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import User, current_user
from app.db import get_session
from app.services.gl_aging import build_metrics_aging_series

router = APIRouter(prefix="/api/v1/metrics", tags=["metrics-compat"])

_UserDep = Annotated[User, Depends(current_user)]
_SessionDep = Annotated[Session, Depends(get_session)]


@router.get("")
def get_metric(
    _user: _UserDep,
    session: _SessionDep,
    metric: str = Query(...),
    entity: Optional[str] = Query(None),
):
    if metric not in ("ar_aging", "ap_aging"):
        raise HTTPException(400, detail=f"Unsupported metric: {metric}")
    try:
        series = build_metrics_aging_series(session, metric, entity)
        return {"metric": metric, "grain": "band", "series": series}
    except Exception as exc:
        raise HTTPException(500, detail=str(exc)) from exc
