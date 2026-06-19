"""Recon presentation mapping tables for databook pipeline (PL/BS row order)."""
from __future__ import annotations

from alembic import op

revision = "0008_recon_mapping"
down_revision = "0007_dataset_versioning"
branch_labels = None
depends_on = None

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS dim_pl_recon_mapping (
    sort_order  INTEGER PRIMARY KEY,
    l3          VARCHAR(200) NOT NULL,
    l4          VARCHAR(200) NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS dim_bs_recon_mapping (
    sort_order  INTEGER PRIMARY KEY,
    l2          VARCHAR(200) NOT NULL,
    l3          VARCHAR(200) NOT NULL,
    l4          VARCHAR(200) NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_dim_pl_recon_mapping_l3 ON dim_pl_recon_mapping (l3, l4);
CREATE INDEX IF NOT EXISTS idx_dim_bs_recon_mapping_l2 ON dim_bs_recon_mapping (l2, l3, l4);
"""


def upgrade() -> None:
    op.execute(_CREATE_SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dim_bs_recon_mapping;")
    op.execute("DROP TABLE IF EXISTS dim_pl_recon_mapping;")
