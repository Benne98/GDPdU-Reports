"""Pre-built statement narratives for overview Key drivers (read-only on request).

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-13
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_UP = """
CREATE TABLE IF NOT EXISTS compat_narrative_snapshot (
    fiscal_year       INT NOT NULL,
    fiscal_month      INT NOT NULL,
    entity_scope      TEXT NOT NULL DEFAULT '',
    statement         TEXT NOT NULL CHECK (statement IN ('pl', 'bs', 'cf', 'wc')),
    period_grain      TEXT NOT NULL DEFAULT 'month',
    algorithm_version TEXT NOT NULL,
    payload_json      JSONB NOT NULL,
    generated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status            TEXT NOT NULL DEFAULT 'ready',
    PRIMARY KEY (fiscal_year, fiscal_month, entity_scope, statement, period_grain)
);

CREATE INDEX IF NOT EXISTS idx_compat_narrative_snapshot_lookup
    ON compat_narrative_snapshot (fiscal_year, fiscal_month, entity_scope, period_grain);
"""

_DOWN = """
DROP TABLE IF EXISTS compat_narrative_snapshot;
"""


def upgrade() -> None:
    op.execute(_UP)


def downgrade() -> None:
    op.execute(_DOWN)
