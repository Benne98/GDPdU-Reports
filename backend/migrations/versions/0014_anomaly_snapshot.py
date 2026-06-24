"""Anomaly analysis snapshot cache (anomaly rework, Phase 3 — Caching Lazy+TTL).

Adds ONE additive table the new anomaly compute orchestrators
(``backend/app/services/anomaly_compute.py``) read-through:

  anomaly_analysis_snapshot
      One row per ``(analysis_type, entity_scope, algorithm_version)``:

        analysis_type     'overview' | 'outliers' | 'seasonality' | 'forensic'
        entity_scope      '' = consolidated/all (admin); else the sorted
                          '|'-joined allowed entity prefixes of a restricted user
                          (see ``anomaly_snapshot_cache.entity_scope_key``).
        algorithm_version the underlying builder's version tag — a version bump
                          lands on a NEW key, so a stale-algorithm payload can
                          never be served.
        payload_json      the precomputed analysis payload (JSONB).
        computed_at       when the snapshot was built; the read side compares it
                          to NOW() against a TTL (24h) so a snapshot older than
                          the TTL forces a recompute.
        status            'ready' once written (mirrors compat_narrative_snapshot).

      The cache is written compute-on-miss by the read-through orchestrators
      (best-effort, idempotent UPSERT on the PK) and by the optional warm script;
      bookings (the lazy live drill) are NEVER cached.

ADDITIVE / GOLDEN-SAFE
──────────────────────
The table is created empty and is read only by the NEW anomaly compute layer; no
existing endpoint output changes → ``golden_snapshot.py compare live v2`` stays
EQUIVALENT.  ``downgrade`` drops only this table.
"""
from __future__ import annotations

from alembic import op

revision = "0014_anomaly_snapshot"
down_revision = "0013_manual_budget_plan"
branch_labels = None
depends_on = None

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS anomaly_analysis_snapshot (
    analysis_type     VARCHAR(16) NOT NULL,              -- 'overview'|'outliers'|'seasonality'|'forensic'
    entity_scope      VARCHAR(64) NOT NULL DEFAULT '',   -- '' = consolidated/all; else sorted '|'-joined prefixes
    algorithm_version VARCHAR(120) NOT NULL,
    payload_json      JSONB       NOT NULL,
    computed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status            VARCHAR(16) NOT NULL DEFAULT 'ready',
    PRIMARY KEY (analysis_type, entity_scope, algorithm_version)
);
"""


def upgrade() -> None:
    op.execute(_CREATE_SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS anomaly_analysis_snapshot;")
