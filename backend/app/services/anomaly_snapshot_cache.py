"""Read/write pre-built anomaly analysis payloads (anomaly rework, Phase 3).

Lazy + TTL (24h) snapshot cache backing the read-through orchestrators in
:mod:`app.services.anomaly_compute`.  Mirrors
:mod:`app.services.fin_compat_narrative_snapshot_cache`:

  * :func:`get_cached` returns the stored ``payload_json`` only when a row exists
    AND it is within the TTL (``computed_at`` newer than ``NOW() - ttl_seconds``);
    the TTL is checked READ-side so an expired snapshot transparently forces a
    recompute.
  * :func:`save` is an idempotent UPSERT on the primary key
    ``(analysis_type, entity_scope, algorithm_version)``; the caller wraps it in
    try/except + rollback so a cache write can never make a read 500.
  * :func:`entity_scope_key` derives the ``entity_scope`` part of the key from the
    caller's allowed entity prefixes — ``''`` for an admin / all entities, else the
    sorted ``'|'``-joined prefixes (no cross-tenant mixing).

Backed by table ``anomaly_analysis_snapshot`` (migration ``0014_anomaly_snapshot``).
Bookings (the lazy live drill) are NEVER cached.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

ANALYSIS_TYPES = frozenset({"overview", "outliers", "seasonality", "forensic"})

#: Default time-to-live for a snapshot (24h), checked read-side.
DEFAULT_TTL_SECONDS = 86400


def entity_scope_key(entity_prefixes: Optional[Iterable[str]]) -> str:
    """Snapshot scope key for a set of allowed entity prefixes.

    ``None`` / empty (admin → consolidated over all entities) → ``''``.  Otherwise
    the prefixes are cleaned (2-char, de-duplicated), sorted and ``'|'``-joined so
    the key is deterministic regardless of input order — a restricted user always
    keys to the SAME scope for the SAME set of visible entities, never crossing into
    another tenant's snapshot.

    Examples::

        entity_scope_key(None)              -> ""
        entity_scope_key([])                -> ""
        entity_scope_key(["02", "01"])      -> "01|02"
        entity_scope_key(["01", "01"])      -> "01"
    """
    if not entity_prefixes:
        return ""
    clean = {str(p).strip()[:2] for p in entity_prefixes if str(p).strip()}
    return "|".join(sorted(clean))


def tables_exist(session: Session) -> bool:
    """True iff the ``anomaly_analysis_snapshot`` table exists in the current schema."""
    row = session.execute(
        text(
            """
            SELECT 1 AS ok FROM information_schema.tables
            WHERE table_schema = current_schema()
              AND table_name = 'anomaly_analysis_snapshot'
            LIMIT 1
            """
        ),
    ).fetchone()
    if row is None:
        return False
    try:
        val = row[0] if not hasattr(row, "_mapping") else next(iter(row._mapping.values()))
        return val == 1
    except Exception:
        return False


def get_cached(
    session: Session,
    analysis_type: str,
    entity_scope: str,
    algorithm_version: str,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> Optional[dict[str, Any]]:
    """Return the cached ``payload_json`` if a FRESH ready row exists, else ``None``.

    A row qualifies when it matches the full primary key, has ``status = 'ready'``
    and is within the TTL — ``computed_at >= NOW() - ttl_seconds`` (the freshness
    test runs in SQL against the DB clock).  Missing table, missing row, or an
    expired snapshot all return ``None`` (→ the orchestrator recomputes).
    """
    if analysis_type not in ANALYSIS_TYPES or not tables_exist(session):
        return None
    row = session.execute(
        text(
            """
            SELECT payload_json, computed_at
            FROM anomaly_analysis_snapshot
            WHERE analysis_type = :atype
              AND entity_scope = :scope
              AND algorithm_version = :algo
              AND status = 'ready'
              AND computed_at >= NOW() - make_interval(secs => :ttl)
            """
        ),
        {
            "atype": analysis_type,
            "scope": entity_scope,
            "algo": algorithm_version,
            "ttl": float(ttl_seconds),
        },
    ).fetchone()
    if not row:
        return None
    mapping = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
    payload = mapping.get("payload_json")
    if payload is None:
        return None
    if isinstance(payload, str):
        payload = json.loads(payload)
    return payload


def save(
    session: Session,
    analysis_type: str,
    entity_scope: str,
    algorithm_version: str,
    payload: dict[str, Any],
) -> None:
    """Idempotent UPSERT of a snapshot on the primary key.

    On conflict (same analysis_type / entity_scope / algorithm_version) the payload
    is refreshed and ``computed_at`` is reset to ``NOW()`` (re-arming the TTL).
    Best-effort: the caller wraps this in try/except + rollback so a write failure
    never propagates to a read.  Commits on success.
    """
    if analysis_type not in ANALYSIS_TYPES:
        raise ValueError(f"Unknown analysis_type: {analysis_type}")
    session.execute(
        text(
            """
            INSERT INTO anomaly_analysis_snapshot (
                analysis_type, entity_scope, algorithm_version,
                payload_json, computed_at, status
            ) VALUES (
                :atype, :scope, :algo, CAST(:payload AS jsonb), NOW(), 'ready'
            )
            ON CONFLICT (analysis_type, entity_scope, algorithm_version)
            DO UPDATE SET
                payload_json = EXCLUDED.payload_json,
                computed_at = NOW(),
                status = 'ready'
            """
        ),
        {
            "atype": analysis_type,
            "scope": entity_scope,
            "algo": algorithm_version,
            "payload": json.dumps(payload),
        },
    )
    session.commit()
