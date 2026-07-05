"""Finssentials GDPdU API.

P0 skeleton + P1 GL ingestion endpoints.
"""
from __future__ import annotations

import concurrent.futures

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.db import engine
from app.routers import admin as admin_router
from app.routers import action_notes as action_notes_router
from app.routers import anlagen as anlagen_router
from app.routers import auth as auth_router
from app.routers import budget as budget_router
from app.routers import directory as directory_router
from app.routers import fdd_bot as fdd_bot_router
from app.routers import er_compat as er_compat_router
from app.routers import financials_compat as financials_compat_router
from app.routers import gl_lines_compat as gl_lines_compat_router
from app.routers import ingest as ingest_router
from app.routers import mapping_editor as mapping_editor_router
from app.routers import masters as masters_router
from app.routers import meta_compat as meta_compat_router
from app.routers import metrics_compat as metrics_compat_router
from app.routers import opos as opos_router
from app.routers import personnel as personnel_router
from app.routers import fixed_assets as fixed_assets_router
from app.routers import plan as plan_router
from app.routers import projects as projects_router
from app.routers import sales_compat as sales_compat_router
from app.routers import statements as statements_router
from app.services.profitability_compat import build_headline_kpis
from app.auth import User, current_user
from app.db import get_session
from fastapi import Depends, Query
from sqlalchemy.orm import Session
from typing import Annotated, Optional

app = FastAPI(title="Finssentials GDPdU API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5174",
        "http://localhost:5175",
        "http://localhost:5176",
        "http://localhost:5177",
        "http://localhost:5178",
        "http://localhost:5179",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:5175",
        "http://127.0.0.1:5176",
        "http://127.0.0.1:5177",
        "http://127.0.0.1:5178",
        "http://127.0.0.1:5179",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# P4 auth
app.include_router(auth_router.router)
# Admin — role & user management
app.include_router(admin_router.router)
# P1 ingestion
app.include_router(ingest_router.router)
app.include_router(mapping_editor_router.router)
# Customer / Supplier master editor (list/search + single-row add/edit)
app.include_router(masters_router.router)
# D3/D4 DRAFT — fixed-asset register + OPOS pass-through ingest (no computed math)
app.include_router(anlagen_router.router)
app.include_router(opos_router.router)
app.include_router(personnel_router.router)
app.include_router(fixed_assets_router.router)
# DF5 plan / forecast
app.include_router(plan_router.router)
# Manual budget (Plan/Forecast extension, Phase 4) — position + partner grain
app.include_router(budget_router.router)
# Reporting-v2 Phase 7 — per-project config persistence + rebuild trigger
app.include_router(projects_router.router)
# P5 statements (P&L; bs/wc/cf later)
app.include_router(statements_router.router)
# Legacy compatibility layer — mirrors the legacy finssentials API surface
# consumed by the verbatim-ported frontend (frontend/src/lib/api.ts).
app.include_router(meta_compat_router.router)
app.include_router(financials_compat_router.router)
app.include_router(er_compat_router.router)
app.include_router(gl_lines_compat_router.router)
app.include_router(sales_compat_router.router)
app.include_router(metrics_compat_router.router)
app.include_router(action_notes_router.router)
app.include_router(directory_router.router)
# FDD Bot (Mathis stack) — uploads + script orchestration + Rasa companion
app.include_router(fdd_bot_router.router)


@app.get("/api/v1/sales-profitability-kpis", tags=["sales-compat"])
def sales_profitability_kpis(
    _user: Annotated[User, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    period_grain: str = Query("month"),
    entity: Optional[str] = Query(None),
) -> dict:
    """Profitability tab KPI strip — same path as legacy frontend."""
    return build_headline_kpis(session, year, month, entity)


@app.get("/api/v1/health")
def health() -> dict:
    """Liveness + DB connectivity check (DB probe capped at 3s)."""
    db_ok = False
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(_probe_db)
            db_ok = bool(fut.result(timeout=3))
    except Exception:  # noqa: BLE001 — health must never raise
        db_ok = False
    return {"status": "ok", "db": db_ok}


def _probe_db() -> bool:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return True
