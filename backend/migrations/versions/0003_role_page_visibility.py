"""Add role_page_visibility table.

Stores which pages each role can access in the frontend.
Referenced by the Admin → Role Management UI (GET /api/v1/admin/roles).

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-13
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_CREATE_SQL = """
CREATE TABLE role_page_visibility (
    role_id  INTEGER     NOT NULL REFERENCES dim_role(role_id) ON DELETE CASCADE,
    page_key VARCHAR(64) NOT NULL,
    PRIMARY KEY (role_id, page_key)
);
COMMENT ON TABLE role_page_visibility IS
    'Maps roles to the page keys they are allowed to access.
     page_key values correspond to the static catalog in app/routers/admin.py.';
"""

_DROP_SQL = """
DROP TABLE IF EXISTS role_page_visibility;
"""


def upgrade() -> None:
    op.execute(_CREATE_SQL)


def downgrade() -> None:
    op.execute(_DROP_SQL)
