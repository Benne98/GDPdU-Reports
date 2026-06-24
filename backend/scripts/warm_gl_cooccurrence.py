"""Warm ``fact_gl_counter_cooccurrence`` per entity (Journal Agent, Phase 3).

Populates the co-occurrence cache table (migration ``0012_gl_cooccurrence``) so the
forensic endpoint (Phase 4) can read a fresh snapshot instead of running the
all-history sibling self-join on every request.  Pattern mirrors
``backend/app/services/narrative_warm.py::warm_narratives_for_period``: iterate the
legal entities, build per entity, upsert, summarise.

The learner (``gl_forensic.build_cooccurrence``) runs over ALL history; pass
``--exclude-year`` / ``--exclude-period`` to learn EXCLUDING the analysed period
(so a current booking can't make its own counter look "common") — the warmed
snapshot records that boundary in ``computed_through_year`` / ``…_period``.

Cache write happens ONLY here (admin / warm path), never via a GET — the forensic
endpoint reads when fresh, else computes live.

Usage:
  cd C:\\Users\\bened\\OneDrive\\Finssentials\\GDPdU-Reports
  $env:DB_PASSWORD = "..."; $env:DB_NAME = "finssentials_v2"
  .\\backend\\.venv\\Scripts\\python.exe backend/scripts/warm_gl_cooccurrence.py
  # exclude the analysed period (e.g. 2025-07):
  .\\backend\\.venv\\Scripts\\python.exe backend/scripts/warm_gl_cooccurrence.py \\
      --exclude-year 2025 --exclude-period 7
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

_BACKEND = Path(__file__).resolve().parent.parent
_ROOT = _BACKEND.parent
for _p in (_BACKEND, _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.services.gl_forensic import build_cooccurrence  # noqa: E402

logger = logging.getLogger("warm_gl_cooccurrence")

_UPSERT_SQL = text("""
INSERT INTO fact_gl_counter_cooccurrence
    (entity_prefix, acct_ang, counter_ang, pair_freq, acct_total_pairs,
     freq_pct, computed_through_year, computed_through_period, refreshed_at)
VALUES
    (:entity_prefix, :acct_ang, :counter_ang, :pair_freq, :acct_total_pairs,
     :freq_pct, :cty, :ctp, NOW())
ON CONFLICT (entity_prefix, acct_ang, counter_ang) DO UPDATE SET
    pair_freq               = EXCLUDED.pair_freq,
    acct_total_pairs        = EXCLUDED.acct_total_pairs,
    freq_pct                = EXCLUDED.freq_pct,
    computed_through_year   = EXCLUDED.computed_through_year,
    computed_through_period = EXCLUDED.computed_through_period,
    refreshed_at            = NOW()
""")


def _table_exists(session: Session) -> bool:
    return bool(
        session.execute(
            text("SELECT to_regclass('fact_gl_counter_cooccurrence')")
        ).scalar()
    )


def _list_entity_codes(session: Session) -> list[Optional[str]]:
    """Each legal entity code (per-entity scope; the cache is entity_prefix-keyed)."""
    rows = session.execute(
        text(
            "SELECT legal_entity_code FROM dim_legal_entity ORDER BY legal_entity_code"
        ),
    ).fetchall()
    return [str(r[0]) for r in rows if r and r[0]]


def warm_cooccurrence(
    *,
    exclude_year: Optional[int] = None,
    exclude_period: Optional[int] = None,
    entities: Optional[list[Optional[str]]] = None,
) -> dict[str, int]:
    """Build + upsert co-occurrence per entity.  Returns ``{ok, rows, error}``."""
    session = SessionLocal()
    stats = {"ok": 0, "rows": 0, "error": 0}
    try:
        if not _table_exists(session):
            logger.warning(
                "warm_cooccurrence: fact_gl_counter_cooccurrence table missing "
                "(run alembic upgrade head)"
            )
            return stats
        ent_list = entities if entities is not None else _list_entity_codes(session)
        for entity in ent_list:
            try:
                rows = build_cooccurrence(
                    session,
                    entity,
                    exclude_year=exclude_year,
                    exclude_period=exclude_period,
                )
                for r in rows:
                    session.execute(_UPSERT_SQL, {
                        "entity_prefix": r["entity_prefix"],
                        "acct_ang": r["acct_ang"],
                        "counter_ang": r["counter_ang"],
                        "pair_freq": r["pair_freq"],
                        "acct_total_pairs": r["acct_total_pairs"],
                        "freq_pct": r["freq_pct"],
                        "cty": exclude_year,
                        "ctp": exclude_period,
                    })
                session.commit()
                stats["ok"] += 1
                stats["rows"] += len(rows)
                logger.info(
                    "warm_cooccurrence entity=%s: %d pairs upserted", entity, len(rows)
                )
            except Exception:
                session.rollback()
                stats["error"] += 1
                logger.exception("warm_cooccurrence failed entity=%s", entity)
        logger.info(
            "warm_cooccurrence done: ok=%d rows=%d error=%d",
            stats["ok"], stats["rows"], stats["error"],
        )
        return stats
    finally:
        session.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Warm fact_gl_counter_cooccurrence per entity."
    )
    parser.add_argument("--exclude-year", type=int, default=None)
    parser.add_argument("--exclude-period", type=int, default=None)
    parser.add_argument(
        "--entity",
        action="append",
        default=None,
        help="Restrict to a legal_entity_code (repeatable). Default: all.",
    )
    args = parser.parse_args()

    stats = warm_cooccurrence(
        exclude_year=args.exclude_year,
        exclude_period=args.exclude_period,
        entities=args.entity,
    )
    print(
        f"co-occurrence warm: ok={stats['ok']} rows={stats['rows']} "
        f"error={stats['error']}"
    )
    return 0 if stats["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
