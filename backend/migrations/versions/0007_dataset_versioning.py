"""Dataset versioning: meta_dataset_load extensions + snapshot tables for restore."""
from __future__ import annotations

from alembic import op

revision = "0007_dataset_versioning"
down_revision = "0006_mapping_level_sort"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE meta_dataset_load
            ADD COLUMN IF NOT EXISTS scope_entity_prefixes TEXT[],
            ADD COLUMN IF NOT EXISTS scope_fiscal_years SMALLINT[],
            ADD COLUMN IF NOT EXISTS commit_mode VARCHAR(16) NOT NULL DEFAULT 'replace',
            ADD COLUMN IF NOT EXISTS snapshot_captured BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS superseded_by_load_id INTEGER
                REFERENCES meta_dataset_load (load_id),
            ADD COLUMN IF NOT EXISTS restored_from_load_id INTEGER
                REFERENCES meta_dataset_load (load_id);

        CREATE TABLE IF NOT EXISTS snap_fact_gl_entry (
            load_id INTEGER NOT NULL REFERENCES meta_dataset_load (load_id) ON DELETE CASCADE,
            journal_entry_group_number VARCHAR(12) NOT NULL,
            fiscal_year SMALLINT NOT NULL,
            fiscal_period SMALLINT NOT NULL,
            entry_type VARCHAR(16) NOT NULL DEFAULT 'actual',
            posting_date DATE NOT NULL,
            document_date DATE,
            document_type_code VARCHAR(20),
            reference_document_number VARCHAR(80),
            currency_code CHAR(5) NOT NULL DEFAULT 'EUR',
            header_note VARCHAR(500),
            source_system VARCHAR(40) NOT NULL DEFAULT 'unknown',
            PRIMARY KEY (load_id, journal_entry_group_number, fiscal_year)
        );
        CREATE INDEX IF NOT EXISTS idx_snap_gl_entry_load ON snap_fact_gl_entry (load_id);

        CREATE TABLE IF NOT EXISTS snap_fact_gl_line (
            load_id INTEGER NOT NULL REFERENCES meta_dataset_load (load_id) ON DELETE CASCADE,
            journal_entry_group_number VARCHAR(12) NOT NULL,
            fiscal_year SMALLINT NOT NULL,
            line_number INTEGER NOT NULL,
            booking_line_id BIGINT NOT NULL,
            account_number_group VARCHAR(8) NOT NULL,
            amount NUMERIC(18,6) NOT NULL,
            vat_amount NUMERIC(18,6),
            line_note VARCHAR(500),
            customer_id VARCHAR(32),
            supplier_id VARCHAR(32),
            posting_type VARCHAR(80),
            source_system VARCHAR(40) NOT NULL DEFAULT 'unknown',
            PRIMARY KEY (load_id, journal_entry_group_number, fiscal_year, line_number)
        );
        CREATE INDEX IF NOT EXISTS idx_snap_gl_line_load ON snap_fact_gl_line (load_id);
        CREATE INDEX IF NOT EXISTS idx_snap_gl_line_bid ON snap_fact_gl_line (load_id, booking_line_id);

        CREATE TABLE IF NOT EXISTS snap_dim_gl_account (
            load_id INTEGER NOT NULL REFERENCES meta_dataset_load (load_id) ON DELETE CASCADE,
            account_number_group VARCHAR(8) NOT NULL,
            fiscal_year SMALLINT NOT NULL,
            gl_account_id VARCHAR(32) NOT NULL,
            account_name VARCHAR(500),
            level_0 VARCHAR(200),
            level_1 VARCHAR(200),
            level_2 VARCHAR(200),
            level_3 VARCHAR(200),
            level_4 VARCHAR(500),
            l4_sub VARCHAR(200),
            level_1_sort INTEGER,
            level_2_sort INTEGER,
            level_3_sort INTEGER,
            level_4_sort INTEGER,
            is_ic BOOLEAN NOT NULL DEFAULT FALSE,
            source_system VARCHAR(40),
            entity_prefix CHAR(2) NOT NULL,
            PRIMARY KEY (load_id, account_number_group, fiscal_year)
        );
        CREATE INDEX IF NOT EXISTS idx_snap_dim_account_load ON snap_dim_gl_account (load_id);

        CREATE TABLE IF NOT EXISTS snap_dim_gl_na (
            load_id INTEGER NOT NULL REFERENCES meta_dataset_load (load_id) ON DELETE CASCADE,
            account_number_group VARCHAR(8) NOT NULL,
            fiscal_year SMALLINT NOT NULL,
            l6_na_mapping VARCHAR(120),
            l7_na_description VARCHAR(500),
            entity_prefix CHAR(2) NOT NULL,
            PRIMARY KEY (load_id, account_number_group, fiscal_year)
        );
        CREATE INDEX IF NOT EXISTS idx_snap_dim_na_load ON snap_dim_gl_na (load_id);

        CREATE TABLE IF NOT EXISTS snap_dim_gl_cf (
            load_id INTEGER NOT NULL REFERENCES meta_dataset_load (load_id) ON DELETE CASCADE,
            account_number_group VARCHAR(8) NOT NULL,
            fiscal_year SMALLINT NOT NULL,
            l1 VARCHAR(200),
            l2 VARCHAR(200),
            l3 VARCHAR(200),
            l4 VARCHAR(200),
            l5 VARCHAR(200),
            cf_mapping VARCHAR(200),
            entity_prefix CHAR(2) NOT NULL,
            PRIMARY KEY (load_id, account_number_group, fiscal_year)
        );
        CREATE INDEX IF NOT EXISTS idx_snap_dim_cf_load ON snap_dim_gl_cf (load_id);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS snap_dim_gl_cf;
        DROP TABLE IF EXISTS snap_dim_gl_na;
        DROP TABLE IF EXISTS snap_dim_gl_account;
        DROP TABLE IF EXISTS snap_fact_gl_line;
        DROP TABLE IF EXISTS snap_fact_gl_entry;
        ALTER TABLE meta_dataset_load
            DROP COLUMN IF EXISTS restored_from_load_id,
            DROP COLUMN IF EXISTS superseded_by_load_id,
            DROP COLUMN IF EXISTS snapshot_captured,
            DROP COLUMN IF EXISTS commit_mode,
            DROP COLUMN IF EXISTS scope_fiscal_years,
            DROP COLUMN IF EXISTS scope_entity_prefixes;
        """
    )
