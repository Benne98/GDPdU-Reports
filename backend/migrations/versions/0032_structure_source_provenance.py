"""0032 — provenance column on the split structure tables (Phase 5, decision 2).

DECISION 2 (docs/plans/v5-pipeline-rework.md) — auto-extension of the statement
structure.  The Project-Setup wizard detects CoA positions that classify nowhere
and lets the user place them as new mapping leaves (POST /ingest/structure/extend).

This migration is PURELY ADDITIVE: it adds a ``source`` marker to each of the
three split structure tables so user-placed rows are distinguishable from seeded
rows (audit + a future "undo auto-extensions").  It changes NO existing value and
NO reader:

  * ``source VARCHAR(16) NOT NULL DEFAULT 'seed'`` — existing rows become 'seed';
    the extend endpoint stamps 'auto_extend' on rows it inserts.

GOLDEN-SAFETY.  Readers ``SELECT`` an explicit column list that does NOT include
``source`` (fin_compat_pl/bs/cf ``_load_structure``), so adding it is invisible to
presentation.  The extend endpoint probes ``information_schema`` and only writes
``source`` when the column exists, so it works on a DB with OR without this
migration.  A DB with no unknown positions never calls extend → structure
unchanged.

ROUND-TRIP.  ``downgrade`` drops the three columns, restoring the pre-Phase-5
schema exactly.
"""
from __future__ import annotations

from alembic import op

revision = "0032_structure_source_provenance"
down_revision = "0031_dim_plan_version"
branch_labels = None
depends_on = None

_TABLES = ("dim_pl_structure", "dim_bs_structure", "dim_cf_structure")


def upgrade() -> None:
    for table in _TABLES:
        op.execute(
            f"ALTER TABLE {table} "
            f"ADD COLUMN IF NOT EXISTS source VARCHAR(16) NOT NULL DEFAULT 'seed';"
        )
    op.execute(
        "COMMENT ON COLUMN dim_pl_structure.source IS "
        "'Provenance (Phase 5): ''seed'' = seeded structure, ''auto_extend'' = "
        "placed via POST /ingest/structure/extend.';"
    )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS source;")
