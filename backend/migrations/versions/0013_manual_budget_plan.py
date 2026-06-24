"""Manual budget plan storage — position-grain + supplier-com plan
(Plan/Forecast extension, Phase 1 — Storage).

Adds TWO additive tables so Finssentials staff can manually plan individual
BS/PL **reporting positions** (line codes) and plan **per debtor/creditor**,
stored under a new ``scenario='budget'`` (separate from forecast/plan):

  fact_position_plan
      One row per
      ``(statement, line_code, entity_prefix, partner_id, fiscal_year,
        fiscal_period, scenario)``.  ``statement`` is 'PL' or 'BS';
      ``entity_prefix`` '' = consolidated/all; ``partner_id`` '' = a
      position-level row, else a customer/supplier id (``partner_kind``
      'customer'|'supplier'|NULL).  Sentinels '' (not NULL) keep the PK unique.
      ``amount`` is in the STORED GL sign (+debit / −credit, exactly like
      ``fact_gl_plan.amount``).  ``is_synthetic`` is TRUE only for the seed
      snapshot.  Storage is always monthly (fiscal_period 1..12; year = Σ months).

  fact_com_plan
      The missing sibling of ``fact_sales_plan`` — a per-supplier cost-of-
      materials plan.  One row per
      ``(supplier_id, fiscal_year, fiscal_period, scenario)``.
      ``cost_of_materials_plan`` is a positive magnitude, mirroring
      ``fact_com.cost_of_materials`` (= amount, material is debit).

ADDITIVE / GOLDEN-SAFE
──────────────────────
Both tables are new and created empty; they are read only by the NEW budget
reader/endpoints (later phases).  No existing endpoint output changes →
``golden_snapshot.py compare live v2`` stays EQUIVALENT (byte-identical) as long
as no budget rows exist.  ``downgrade`` drops only these two tables.
"""
from __future__ import annotations

from alembic import op

revision = "0013_manual_budget_plan"
down_revision = "0012_gl_cooccurrence"
branch_labels = None
depends_on = None

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS fact_position_plan (
    statement     VARCHAR(2)  NOT NULL,                 -- 'PL' | 'BS'
    line_code     VARCHAR(64) NOT NULL,                 -- reporting position
    entity_prefix VARCHAR(2)  NOT NULL DEFAULT '',      -- '' = consolidated/all
    partner_id    VARCHAR(32) NOT NULL DEFAULT '',      -- '' = position-level row
    partner_kind  VARCHAR(8),                           -- 'customer'|'supplier'|NULL
    fiscal_year   SMALLINT    NOT NULL,
    fiscal_period SMALLINT    NOT NULL,                 -- 1..12 (always monthly)
    scenario      VARCHAR(12) NOT NULL DEFAULT 'budget',
    amount        NUMERIC(18,6) NOT NULL,               -- STORED GL sign (+debit/-credit)
    is_synthetic  BOOLEAN     NOT NULL DEFAULT FALSE,   -- TRUE only for seed snapshot
    source_system VARCHAR(40),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by    VARCHAR(200),
    PRIMARY KEY (statement, line_code, entity_prefix, partner_id,
                 fiscal_year, fiscal_period, scenario)
);

-- Helpful secondary index for the reader queries (per statement/year/scenario/entity).
CREATE INDEX IF NOT EXISTS idx_position_plan_read
    ON fact_position_plan (statement, fiscal_year, scenario, entity_prefix);

CREATE TABLE IF NOT EXISTS fact_com_plan (
    supplier_id   VARCHAR(32) NOT NULL,
    fiscal_year   SMALLINT    NOT NULL,
    fiscal_period SMALLINT    NOT NULL,
    scenario      VARCHAR(12) NOT NULL,
    cost_of_materials_plan NUMERIC(18,6) NOT NULL,      -- positive magnitude, mirrors fact_com
    is_synthetic  BOOLEAN     NOT NULL DEFAULT TRUE,
    source_system VARCHAR(40),
    PRIMARY KEY (supplier_id, fiscal_year, fiscal_period, scenario)
);
"""


def upgrade() -> None:
    op.execute(_CREATE_SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS fact_position_plan;")
    op.execute("DROP TABLE IF EXISTS fact_com_plan;")
