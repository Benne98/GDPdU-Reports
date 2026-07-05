"""0028 — asset_label on fact_fixed_asset (Anlagenbezeichnung)."""
from __future__ import annotations

from alembic import op

revision = "0028_fact_fixed_asset_label"
down_revision = "0027_fact_fixed_asset_as_of"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE fact_fixed_asset
    ADD COLUMN IF NOT EXISTS asset_label VARCHAR(200);
""")


def downgrade() -> None:
    op.execute("ALTER TABLE fact_fixed_asset DROP COLUMN IF EXISTS asset_label;")
