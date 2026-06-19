"""Background warming of compat narrative snapshots after ETL."""
from __future__ import annotations

import logging
import threading
from typing import Optional

from sqlalchemy import text

from app.db import SessionLocal
from app.services.fin_compat_narrative_snapshot_cache import entity_scope_key, tables_exist
from app.services.fin_compat_overview_narrative_snapshots import _build_and_store

logger = logging.getLogger(__name__)

_STATEMENTS = ("pl", "bs", "cf", "wc")
_GRAINS = ("month", "year")


def _list_entity_codes(session) -> list[Optional[str]]:
    """Consolidated (None) + each legal entity code."""
    rows = session.execute(
        text("SELECT legal_entity_code FROM dim_legal_entity ORDER BY legal_entity_code"),
    ).fetchall()
    codes = [str(r[0]) for r in rows if r and r[0]]
    return [None, *codes]


def warm_narratives_for_period(
    year: int,
    month: int,
    *,
    entities: Optional[list[Optional[str]]] = None,
    grains: tuple[str, ...] = _GRAINS,
) -> dict[str, int]:
    """Build and persist snapshots for all statements × entities × grains."""
    session = SessionLocal()
    stats = {"ok": 0, "skip": 0, "error": 0}
    try:
        if not tables_exist(session):
            logger.warning("warm_narratives: compat_narrative_snapshot table missing")
            return stats
        ent_list = entities if entities is not None else _list_entity_codes(session)
        for entity in ent_list:
            for grain in grains:
                for stmt in _STATEMENTS:
                    try:
                        nar = _build_and_store(
                            session,
                            statement=stmt,
                            year=year,
                            month=month,
                            entity=entity,
                            period_grain=grain,
                            max_bullets=8,
                            iso_year=None,
                            iso_week=None,
                        )
                        if nar:
                            stats["ok"] += 1
                        else:
                            stats["skip"] += 1
                    except Exception:
                        session.rollback()
                        stats["error"] += 1
                        logger.exception(
                            "warm_narratives failed stmt=%s entity=%s grain=%s",
                            stmt,
                            entity_scope_key(entity) or "consolidated",
                            grain,
                        )
        logger.info(
            "warm_narratives %d-%02d: ok=%d skip=%d error=%d",
            year,
            month,
            stats["ok"],
            stats["skip"],
            stats["error"],
        )
        return stats
    finally:
        session.close()


def schedule_narrative_warm(year: int, month: int) -> None:
    """Fire-and-forget background warm (non-blocking for HTTP handlers)."""

    def _run() -> None:
        try:
            warm_narratives_for_period(year, month)
        except Exception:
            logger.exception("background narrative warm failed")

    threading.Thread(
        target=_run,
        name=f"narrative-warm-{year}-{month:02d}",
        daemon=True,
    ).start()
