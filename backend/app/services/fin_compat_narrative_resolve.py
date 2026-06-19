"""Snapshot-first narrative resolution for statement + overview endpoints.

Production default: serve pre-built rows from ``compat_narrative_snapshot`` only.
Rebuild on HTTP request only when ``force_refresh`` or ``OVERVIEW_NARRATIVE_ALLOW_REBUILD=1``.
Warm via ``scripts/warm_compat_narrative_snapshots.py`` (also triggered after ETL commit).
"""
from __future__ import annotations

import os
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.services.fin_compat_narrative_snapshot_cache import get_cached_snapshot

_STMTS = frozenset({"pl", "bs", "cf", "wc"})


def allow_narrative_rebuild() -> bool:
    return os.getenv("OVERVIEW_NARRATIVE_ALLOW_REBUILD", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _trim_bullets(nar: dict[str, Any], max_bullets: Optional[int]) -> dict[str, Any]:
    if max_bullets is None or max_bullets <= 0:
        return nar
    out = dict(nar)
    bullets = list(nar.get("bullets") or [])
    if len(bullets) > max_bullets:
        out["bullets"] = bullets[:max_bullets]
    return out


def resolve_statement_narrative(
    session: Session,
    statement: str,
    year: int,
    month: int,
    entity: Optional[str],
    *,
    period_grain: str = "month",
    iso_year: Optional[int] = None,
    iso_week: Optional[int] = None,
    max_bullets: Optional[int] = None,
    use_llm: bool = False,
    force_refresh: bool = False,
) -> Optional[dict[str, Any]]:
    """Return narrative payload or None when cache is cold and rebuild is disabled."""
    if statement not in _STMTS:
        return None

    ent = entity
    if ent and str(ent).strip().lower() in ("", "all"):
        ent = None

    if not force_refresh:
        nar = get_cached_snapshot(
            session,
            statement=statement,
            year=year,
            month=month,
            entity=ent,
            period_grain=period_grain,
            use_llm=use_llm,
        )
        if not nar and period_grain == "year":
            nar = get_cached_snapshot(
                session,
                statement=statement,
                year=year,
                month=month,
                entity=ent,
                period_grain="month",
                use_llm=use_llm,
            )
        if nar:
            return _trim_bullets(nar, max_bullets)

    if not force_refresh and not allow_narrative_rebuild():
        return None

    from app.services.fin_compat_overview_narrative_snapshots import _build_and_store

    nar = _build_and_store(
        session,
        statement=statement,
        year=year,
        month=month,
        entity=ent,
        period_grain=period_grain,
        max_bullets=max(5, max_bullets or 5),
        iso_year=iso_year,
        iso_week=iso_week,
    )
    return _trim_bullets(nar, max_bullets) if nar else None
