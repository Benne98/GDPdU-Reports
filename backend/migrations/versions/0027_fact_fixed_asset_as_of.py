"""0027 — Fixed-asset snapshots: as_of_date + opening NBV for rollforward."""
from __future__ import annotations

from alembic import op

revision = "0027_fact_fixed_asset_as_of"
down_revision = "0026_mart_overview_period"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE fact_fixed_asset
    ADD COLUMN IF NOT EXISTS as_of_date DATE,
    ADD COLUMN IF NOT EXISTS opening_nbv NUMERIC(18, 2);

CREATE INDEX IF NOT EXISTS idx_fact_fixed_asset_asof
    ON fact_fixed_asset (project_id, as_of_date, entity_prefix);
""")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_fact_fixed_asset_asof;")
    op.execute("""
ALTER TABLE fact_fixed_asset
    DROP COLUMN IF EXISTS opening_nbv,
    DROP COLUMN IF EXISTS as_of_date;
""")
