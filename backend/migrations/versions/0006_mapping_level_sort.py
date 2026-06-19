"""Add level_1_sort and level_4_sort to dim_gl_account for mapping editor reorder."""
from __future__ import annotations

from alembic import op

revision = "0006_mapping_level_sort"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE dim_gl_account
            ADD COLUMN IF NOT EXISTS level_1_sort INTEGER,
            ADD COLUMN IF NOT EXISTS level_4_sort INTEGER;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE dim_gl_account
            DROP COLUMN IF EXISTS level_1_sort,
            DROP COLUMN IF EXISTS level_4_sort;
        """
    )
