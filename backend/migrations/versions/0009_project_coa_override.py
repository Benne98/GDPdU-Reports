"""Per-project chart-of-accounts override (reporting-v2 Phase 4).

Persists CoA-editor edits (hierarchy remap + sort) per project so they survive a
full ``rebuild_project``.  Rebuild stage 1 replays these rows onto
``dim_gl_account`` for the project's scope at the start of every rebuild.

Multi-tenancy note
------------------
A full ``dim_project`` table is OUT OF SCOPE for Phase 4.  ``project_id`` is a
plain ``VARCHAR`` with a single default value (``'default'``) for now; the table
is keyed on ``(project_id, account_number_group, fiscal_year)`` so a real
project_id (and an FK to a future ``dim_project``) can be added later without a
structural change.

NO-OP guarantee
---------------
The table is created empty.  When it holds no rows for a project the rebuild
replay is a true no-op, so the golden live-vs-v2 equivalence is preserved.
"""
from __future__ import annotations

from alembic import op

revision = "0009_project_coa_override"
down_revision = "0008_recon_mapping"
branch_labels = None
depends_on = None

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS dim_project_coa_override (
    project_id           VARCHAR(64)  NOT NULL DEFAULT 'default',
    account_number_group VARCHAR(8)   NOT NULL,
    fiscal_year          SMALLINT     NOT NULL,
    level_0              VARCHAR(200),
    level_1              VARCHAR(200),
    level_2              VARCHAR(200),
    level_3              VARCHAR(200),
    level_4              VARCHAR(500),
    l4_sub               VARCHAR(200),
    level_1_sort         INTEGER,
    level_2_sort         INTEGER,
    level_3_sort         INTEGER,
    level_4_sort         INTEGER,
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (project_id, account_number_group, fiscal_year)
);

CREATE INDEX IF NOT EXISTS idx_dim_project_coa_override_scope
    ON dim_project_coa_override (project_id, fiscal_year);
"""


def upgrade() -> None:
    op.execute(_CREATE_SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dim_project_coa_override;")
