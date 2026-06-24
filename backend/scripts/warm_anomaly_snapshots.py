"""Warm the anomaly analysis snapshots (anomaly rework, Phase 3).

Pre-builds the four anomaly analyses (overview / outliers / seasonality / forensic)
for the CONSOLIDATED scope ('' = all entities) and saves them into
``anomaly_analysis_snapshot`` (migration ``0014_anomaly_snapshot``), so the first
real request after an ETL serves from cache instead of computing the whole tree.

Pattern mirrors ``backend/scripts/warm_gl_cooccurrence.py``: open one session,
iterate the analyses, save, summarise.  Each analysis uses the read-through
orchestrator, so a hit is a no-op and a miss computes + saves.  This is the
ADMIN / warm path; the GET endpoints (Phase 4) also compute-on-miss.

Usage:
  cd C:\\Users\\bened\\OneDrive\\Finssentials\\GDPdU-Reports
  $env:DB_PASSWORD = "..."; $env:DB_NAME = "finssentials_v2"
  .\\backend\\.venv\\Scripts\\python.exe backend/scripts/warm_anomaly_snapshots.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
_ROOT = _BACKEND.parent
for _p in (_BACKEND, _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.services import anomaly_compute  # noqa: E402

logger = logging.getLogger("warm_anomaly_snapshots")

#: (analysis_type, orchestrator) — consolidated scope (entity_prefixes=None).
_ANALYSES = [
    ("overview", anomaly_compute.get_overview),
    ("outliers", anomaly_compute.get_outliers),
    ("seasonality", anomaly_compute.get_seasonality),
    ("forensic", anomaly_compute.get_forensic),
]


def _table_exists(session) -> bool:
    return bool(session.execute(text("SELECT to_regclass('anomaly_analysis_snapshot')")).scalar())


def warm_snapshots() -> dict[str, int]:
    """Compute + save the 4 consolidated analyses.  Returns ``{ok, error}``."""
    session = SessionLocal()
    stats = {"ok": 0, "error": 0}
    try:
        if not _table_exists(session):
            logger.warning(
                "warm_anomaly_snapshots: anomaly_analysis_snapshot table missing "
                "(run alembic upgrade head)"
            )
            return stats
        for analysis_type, fn in _ANALYSES:
            try:
                result = fn(session, entity_prefixes=None)
                stats["ok"] += 1
                logger.info(
                    "warm_anomaly_snapshots %s: cache_hit=%s",
                    analysis_type, result.get("cache_hit"),
                )
            except Exception:
                session.rollback()
                stats["error"] += 1
                logger.exception("warm_anomaly_snapshots failed type=%s", analysis_type)
        logger.info(
            "warm_anomaly_snapshots done: ok=%d error=%d", stats["ok"], stats["error"],
        )
        return stats
    finally:
        session.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    stats = warm_snapshots()
    print(f"anomaly snapshot warm: ok={stats['ok']} error={stats['error']}")
    return 0 if stats["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
