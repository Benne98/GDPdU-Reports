"""Add ingest_mapping_profile table.

Stores reusable per-source / per-client column mapping profiles as JSONB.
Referenced by P1 ingestion wizard (docs/P1-gl-ingestion.md §7.1 / §11 decision 4).

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-11
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_CREATE_SQL = """
CREATE TABLE ingest_mapping_profile (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(200) NOT NULL,
    source_system VARCHAR(80)  NOT NULL DEFAULT 'unknown',
    profile_json  JSONB        NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_mapping_profile_name_source
        UNIQUE (name, source_system)
);
COMMENT ON TABLE ingest_mapping_profile IS
    'Reusable column-mapping profiles for the GL ingestion wizard (P1).
     profile_json holds a serialised etl.mapping.MappingProfile dict.';
CREATE INDEX idx_mapping_profile_source ON ingest_mapping_profile (source_system);
"""

_DROP_SQL = """
DROP TABLE IF EXISTS ingest_mapping_profile;
"""


def upgrade() -> None:
    op.execute(_CREATE_SQL)


def downgrade() -> None:
    op.execute(_DROP_SQL)
