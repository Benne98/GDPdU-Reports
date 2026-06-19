"""
Read pre-built statement narratives for overview Key drivers (cache / snapshot only).

Overview must not run full narrative pipelines on every HTTP request — warm snapshots
via ``scripts/warm_compat_narrative_snapshots.py`` after ETL, or set
``OVERVIEW_NARRATIVE_ALLOW_REBUILD=1`` in dev to rebuild on cache miss.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from app.services.fin_compat_narrative_resolve import allow_narrative_rebuild
from app.services.fin_compat_narrative_snapshot_cache import get_cached_snapshot

_AREA_TO_STMT: dict[str, str] = {
    "earnings": "pl",
    "position": "bs",
    "finance": "cf",
    "working_capital": "wc",
    "pl": "pl",
    "bs": "bs",
    "cf": "cf",
    "wc": "wc",
}


def _trim_payload(nar: dict[str, Any], max_bullets: int) -> dict[str, Any]:
    bullets = list(nar.get("bullets") or [])
    if max_bullets > 0:
        bullets = bullets[:max_bullets]
    return {
        "intro": nar.get("intro"),
        "bullets": bullets,
        "headline": nar.get("headline"),
    }


def _build_and_store(
    session: Session,
    *,
    statement: str,
    year: int,
    month: int,
    entity: Optional[str],
    period_grain: str,
    max_bullets: int,
    iso_year: Optional[int],
    iso_week: Optional[int],
) -> Optional[dict[str, Any]]:
    from app.services.fin_compat_bs import build_bs_narrative
    from app.services.fin_compat_cf import build_cf_narrative
    from app.services.fin_compat_narrative import build_pl_narrative
    from app.services.fin_compat_narrative_snapshot_cache import save_snapshot, tables_exist
    from app.services.fin_compat_wc import build_wc_narrative

    builders = {
        "pl": build_pl_narrative,
        "bs": build_bs_narrative,
        "cf": build_cf_narrative,
        "wc": build_wc_narrative,
    }
    builder = builders.get(statement)
    if builder is None:
        return None

    narr_kw: dict[str, Any] = {
        "period_grain": period_grain,
        "max_bullets": max(5, max_bullets),
        "use_llm": False,
    }
    if period_grain == "week":
        narr_kw["iso_year"] = iso_year
        narr_kw["iso_week"] = iso_week

    try:
        nar = builder(session, year, month, entity, **narr_kw)
    except Exception:
        return None
    if not nar:
        return None

    if tables_exist(session):
        try:
            save_snapshot(
                session,
                statement=statement,
                year=year,
                month=month,
                entity=entity,
                period_grain=period_grain,
                payload=nar,
            )
        except Exception:
            session.rollback()
    return _trim_payload(nar, max_bullets)


def load_narrative_snapshot(
    session: Session,
    year: int,
    month: int,
    entity: Optional[str],
    area_or_stmt: str,
    *,
    period_grain: str = "month",
    iso_year: Optional[int] = None,
    iso_week: Optional[int] = None,
    max_bullets: int = 2,
) -> Optional[dict[str, Any]]:
    """Return narrative payload from DB snapshot if available (fast path for overview)."""
    stmt = _AREA_TO_STMT.get(area_or_stmt, area_or_stmt)
    ent = entity
    if ent and str(ent).strip().lower() in ("", "all"):
        ent = None

    nar = get_cached_snapshot(
        session,
        statement=stmt,
        year=year,
        month=month,
        entity=ent,
        period_grain=period_grain,
        use_llm=False,
    )
    if nar:
        return _trim_payload(nar, max_bullets)

    # Annual overview uses month-grain snapshots when year-grain cache is cold.
    if period_grain == "year":
        nar = get_cached_snapshot(
            session,
            statement=stmt,
            year=year,
            month=month,
            entity=ent,
            period_grain="month",
            use_llm=False,
        )
        if nar:
            return _trim_payload(nar, max_bullets)

    if not allow_narrative_rebuild():
        return None

    rebuild_grain = period_grain
    return _build_and_store(
        session,
        statement=stmt,
        year=year,
        month=month,
        entity=ent,
        period_grain=rebuild_grain,
        max_bullets=max_bullets,
        iso_year=iso_year,
        iso_week=iso_week,
    )
