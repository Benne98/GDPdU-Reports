"""Widen anomaly_analysis_snapshot.algorithm_version VARCHAR(40) -> VARCHAR(120).

WHY
───
The overview orchestrator (:mod:`app.services.anomaly_compute`) keys its snapshot
on a COMPOSITE algorithm version that concatenates the overview, tree and forensic
version tags::

    OVERVIEW_ALGORITHM_VERSION =
        "anomaly_overview_v1+gl_anomaly_tree_v1+gl_forensic_positions_v1"   # 63 chars

That string does not fit the original ``algorithm_version VARCHAR(40)`` column
(migration ``0014_anomaly_snapshot``), so the best-effort UPSERT in
``anomaly_snapshot_cache.save`` raised ``value too long for type character
varying(40)``.  The orchestrator swallows the save error (a cache write must never
500 a read), so the overview never persisted and was recomputed (~18s) on EVERY
call.  Outliers / seasonality / forensic key on the short single-tag versions and
fit fine, so only the overview cache was broken.

FIX
───
Widen the column to ``VARCHAR(120)`` (covers the composite version with headroom).
Migration ``0014`` was also edited so fresh installs create the wide column; this
migration repairs databases that already applied ``0014`` (e.g. finssentials_v2).
Keeping the composite version string intact preserves auto-invalidation: any
builder version bump still lands on a NEW key, so a stale-algorithm payload can
never be served.

ADDITIVE / GOLDEN-SAFE
──────────────────────
A pure column-type widening — no data is dropped, no row is rewritten in a way
that changes output, and no endpoint payload changes.  ``golden_snapshot.py
compare live v2`` stays EQUIVALENT.  ``downgrade`` narrows back to VARCHAR(40)
(only safe while no stored value exceeds 40 chars).
"""
from __future__ import annotations

from alembic import op

revision = "0015_widen_anomaly_algo_version"
down_revision = "0014_anomaly_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE anomaly_analysis_snapshot "
        "ALTER COLUMN algorithm_version TYPE VARCHAR(120);"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE anomaly_analysis_snapshot "
        "ALTER COLUMN algorithm_version TYPE VARCHAR(40);"
    )
