"""Read/write pre-built fin_compat narrative payloads for overview Key drivers."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

_STMTS = frozenset({"pl", "bs", "cf", "wc"})


def entity_scope_key(entity: Optional[str]) -> str:
    if not entity or str(entity).strip().lower() in ("", "all"):
        return ""
    return str(entity).strip()


def tables_exist(session: Session) -> bool:
    row = session.execute(
        text(
            """
            SELECT 1 AS ok FROM information_schema.tables
            WHERE table_schema = current_schema()
              AND table_name = 'compat_narrative_snapshot'
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


def get_cached_snapshot(
    session: Session,
    *,
    statement: str,
    year: int,
    month: int,
    entity: Optional[str],
    period_grain: str = "month",
    use_llm: bool = False,
) -> Optional[dict[str, Any]]:
    if statement not in _STMTS or not tables_exist(session):
        return None
    scope = entity_scope_key(entity)
    row = session.execute(
        text(
            """
            SELECT payload_json, algorithm_version, generated_at, status
            FROM compat_narrative_snapshot
            WHERE fiscal_year = :year
              AND fiscal_month = :month
              AND entity_scope = :scope
              AND statement = :statement
              AND period_grain = :grain
              AND status = 'ready'
            """
        ),
        {
            "year": year,
            "month": month,
            "scope": scope,
            "statement": statement,
            "grain": period_grain,
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
    meta = dict(payload.get("meta") or {})
    if not use_llm and meta.get("llm_used"):
        return None
    meta["cache_hit"] = True
    meta["algorithm_version"] = mapping.get("algorithm_version")
    generated_at = mapping.get("generated_at")
    if generated_at is not None:
        meta["generated_at"] = generated_at.isoformat() if hasattr(generated_at, "isoformat") else str(generated_at)
    meta["entity_scope"] = scope
    payload["meta"] = meta
    return payload


def save_snapshot(
    session: Session,
    *,
    statement: str,
    year: int,
    month: int,
    entity: Optional[str],
    period_grain: str,
    payload: dict[str, Any],
) -> None:
    if statement not in _STMTS:
        raise ValueError(f"Unknown statement: {statement}")
    scope = entity_scope_key(entity)
    meta = payload.get("meta") or {}
    algo = str(meta.get("algorithm_version") or "unknown")
    now = datetime.now(timezone.utc)
    session.execute(
        text(
            """
            INSERT INTO compat_narrative_snapshot (
                fiscal_year, fiscal_month, entity_scope, statement, period_grain,
                algorithm_version, payload_json, generated_at, status
            ) VALUES (
                :year, :month, :scope, :statement, :grain,
                :algo, CAST(:payload AS jsonb), :generated_at, 'ready'
            )
            ON CONFLICT (fiscal_year, fiscal_month, entity_scope, statement, period_grain)
            DO UPDATE SET
                algorithm_version = EXCLUDED.algorithm_version,
                payload_json = EXCLUDED.payload_json,
                generated_at = EXCLUDED.generated_at,
                status = 'ready'
            """
        ),
        {
            "year": year,
            "month": month,
            "scope": scope,
            "statement": statement,
            "grain": period_grain,
            "algo": algo,
            "payload": json.dumps(payload),
            "generated_at": now,
        },
    )
    session.commit()
