"""D3 DRAFT — fixed-asset register (Anlagenregister) pass-through fact.

SCOPE / DRAFT GUARANTEE
-----------------------
This migration creates ONE new, EMPTY fact table ``fact_fixed_asset`` that stores
the raw rows of a client fixed-asset register (Anlagenspiegel) exactly as mapped
from the source file — every numeric column is a PASS-THROUGH of the source value.

NO financial roll-forward is computed here:
  * closing cost (AHK)            = opening + Zugang - Abgang ± Umbuchung   (F1)
  * accumulated depreciation / NBV (method TBD)                            (F2)
are DEFERRED to the financial-calculation-engineer (see the FLAGGED section in
``docs/financial-logic.md``).  No derived/closing/accumulated columns exist on
this table yet — adding them is a later, separately-approved migration.

Because the table is created empty and is read by no existing endpoint, the
golden live-vs-v2 equivalence is unaffected.

CONVENTIONS
-----------
Lineage / tenancy columns follow the project conventions already used by
``dim_project_coa_override`` (project_id VARCHAR default 'default', no hard FK so
a future ``dim_project`` FK can be added without a data backfill) and the dataset
versioning table ``org_meta_dataset_load`` (load_id).  ``source_file_id`` + ``row_no``
+ ``created_at`` are the per-row ingest lineage.
"""
from __future__ import annotations

from alembic import op

revision = "0022_draft_fixed_asset_register"
down_revision = "0021_account_mapping_library"
branch_labels = None
depends_on = None

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS fact_fixed_asset (
    id                  BIGSERIAL PRIMARY KEY,
    -- tenancy / dataset-version / lineage --------------------------------
    project_id          VARCHAR(64)  NOT NULL DEFAULT 'default',
    dataset_version_id  INTEGER      REFERENCES org_meta_dataset_load (load_id) ON DELETE SET NULL,
    source_file_id      VARCHAR(64),
    row_no              INTEGER,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- scope --------------------------------------------------------------
    entity_prefix       VARCHAR(2),
    fy_label            VARCHAR(20),
    -- asset identity (pass-through) -------------------------------------
    asset_id            VARCHAR(64),
    asset_sub_no        VARCHAR(64),
    asset_class         VARCHAR(120),
    segment             VARCHAR(120),
    bilanzposition      VARCHAR(200),
    capitalization_date DATE,
    -- roll-forward INPUTS (pass-through, NOT computed) ------------------
    opening_cost_ahk    NUMERIC(18,2),
    additions_zugang    NUMERIC(18,2),
    disposals_abgang    NUMERIC(18,2),
    transfers_umbuchung NUMERIC(18,2),
    depreciation        NUMERIC(18,2),
    nbv                 NUMERIC(18,2)
);

CREATE INDEX IF NOT EXISTS idx_fact_fixed_asset_scope
    ON fact_fixed_asset (project_id, entity_prefix, fy_label);
"""

# DRAFT marker — closing cost (AHK) and accumulated depreciation / NBV are NOT
# computed; see docs/financial-logic.md FLAGGED section (F1, F2).
_COMMENT_SQL = """
COMMENT ON TABLE fact_fixed_asset IS
 'DRAFT (D3): pass-through Anlagenregister rows. Roll-forward (closing cost,
  accumulated depreciation) NOT computed; see docs/financial-logic.md F1/F2.';
COMMENT ON COLUMN fact_fixed_asset.opening_cost_ahk IS
 'Pass-through opening AHK. Closing AHK is NOT derived (F1, deferred).';
"""


def upgrade() -> None:
    op.execute(_CREATE_SQL)
    op.execute(_COMMENT_SQL)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_fact_fixed_asset_scope;")
    op.execute("DROP TABLE IF EXISTS fact_fixed_asset;")
