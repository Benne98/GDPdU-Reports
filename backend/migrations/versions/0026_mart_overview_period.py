"""0026 — Overview v2 pre-aggregation mart (P3 accelerator).

SCOPE / ADDITIVE-INERT GUARANTEE
--------------------------------
This migration creates TWO new, EMPTY materialized snapshot tables used ONLY by
the new reporting-v2 (:8011) Overview summary endpoint as an OPTIONAL accelerator:

  * ``mart_overview_period``      — P&L period sums (Σ raw amount) keyed
                                    (project_id, entity_prefix, fiscal_year,
                                     fiscal_period, level_3).
  * ``mart_overview_bs_balance``  — Balance-sheet cumulative stock balances keyed
                                    (project_id, entity_prefix, cutoff_date,
                                     level_3).

No existing endpoint reads these tables, and nothing on the SHARED ingest/derive
path writes them — the mart is refreshed LAZILY / on-demand by the new :8011
summary endpoint (``app.services.mart_overview.refresh_mart_overview_period``)
when it detects staleness vs the latest GL load.  Therefore this migration is
additive and inert on the 5176/8010/5178 stacks: the golden live-vs-v2
equivalence is unaffected.

WHY TWO TABLES (not one discriminator column)
---------------------------------------------
The two grains have DIFFERENT natural keys: P&L rows are keyed by
(fiscal_year, fiscal_period) period buckets, while BS balances are cumulative
stocks keyed by a (cutoff_date) as-of date.  A single table with a discriminator
would force half of every row's key columns to be NULL and blur the semantics of
``amount_sum`` (period movement) vs ``balance_sum`` (cumulative stock).  Two
narrow tables keep each unique key meaningful and each index tight.

SIGN CONVENTION (documented; the read helper reproduces builder semantics)
--------------------------------------------------------------------------
Both ``amount_sum`` and ``balance_sum`` store the RAW stored GL sign
(``SUM(fact_gl_line.amount)``), exactly as persisted — NO presentation inversion
is baked into the mart.  Presentation is applied by the read helper to stay
byte-identical to the existing builders:
  * P&L  presented = amount_sum * -1   (revenue credit → +, cost debit → −)
  * BS   magnitude  = ABS(balance_sum) (DuPont magnitudes), or raw for WC signing.

PROVENANCE / STALENESS
----------------------
Each row carries ``source_load_id`` (the ``org_meta_dataset_load.load_id`` the
mart was built from) and ``refreshed_at``.  ``mart_is_fresh(session, latest)``
compares MAX(source_load_id) against the latest GL load so the endpoint can fall
back to the live builders whenever the mart is stale.

CONVENTIONS
-----------
Lineage / tenancy columns follow ``fact_fixed_asset`` (0022) /
``fact_personnel_employee`` (0025): ``project_id VARCHAR default 'default'`` and a
soft ``source_load_id`` FK to ``org_meta_dataset_load (load_id)`` with
``ON DELETE SET NULL`` so purging a load never deletes mart rows silently.
"""
from __future__ import annotations

from alembic import op

revision = "0026_mart_overview_period"
down_revision = "0025_fact_personnel_employee"
branch_labels = None
depends_on = None


_CREATE_SQL = """
-- ============================================================ P&L period sums
CREATE TABLE IF NOT EXISTS mart_overview_period (
    id             BIGSERIAL PRIMARY KEY,
    project_id     VARCHAR(64) NOT NULL DEFAULT 'default',
    entity_prefix  VARCHAR(2)  NOT NULL,
    fiscal_year    SMALLINT    NOT NULL,
    fiscal_period  SMALLINT    NOT NULL,
    -- statement hierarchy carried so the read helper can reconstruct the EBIT
    -- bucket (level_2 IN ('Income','Expense') minus certain level_3) without a
    -- re-join to dim_gl_account.  Functionally dependent on level_3 within a
    -- (fiscal_year), so included in GROUP BY at refresh time.
    level_0        VARCHAR(8)  NOT NULL DEFAULT 'PL',
    level_2        VARCHAR(120),
    level_3        VARCHAR(120) NOT NULL,
    -- Σ raw fact_gl_line.amount for the (entity, fy, period, level_3) bucket.
    amount_sum     NUMERIC(20, 2) NOT NULL DEFAULT 0,
    -- provenance ---------------------------------------------------------
    source_load_id INTEGER     REFERENCES org_meta_dataset_load (load_id) ON DELETE SET NULL,
    refreshed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_mart_overview_period
        UNIQUE (project_id, entity_prefix, fiscal_year, fiscal_period, level_3)
);

CREATE INDEX IF NOT EXISTS idx_mart_overview_period_lookup
    ON mart_overview_period (project_id, entity_prefix, fiscal_year, fiscal_period);

-- ============================================================ BS cumulative balances
CREATE TABLE IF NOT EXISTS mart_overview_bs_balance (
    id             BIGSERIAL PRIMARY KEY,
    project_id     VARCHAR(64) NOT NULL DEFAULT 'default',
    entity_prefix  VARCHAR(2)  NOT NULL,
    -- as-of cutoff (month-end): balance_sum = Σ amount WHERE posting_date <= cutoff
    cutoff_date    DATE        NOT NULL,
    level_0        VARCHAR(8)  NOT NULL DEFAULT 'BS',
    level_2        VARCHAR(120),
    level_3        VARCHAR(120) NOT NULL,
    balance_sum    NUMERIC(20, 2) NOT NULL DEFAULT 0,
    -- provenance ---------------------------------------------------------
    source_load_id INTEGER     REFERENCES org_meta_dataset_load (load_id) ON DELETE SET NULL,
    refreshed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_mart_overview_bs_balance
        UNIQUE (project_id, entity_prefix, cutoff_date, level_3)
);

CREATE INDEX IF NOT EXISTS idx_mart_overview_bs_lookup
    ON mart_overview_bs_balance (project_id, entity_prefix, cutoff_date);
"""

_COMMENT_SQL = """
COMMENT ON TABLE mart_overview_period IS
 'Overview v2 accelerator (P3): per (entity, fy, period, level_3) Σ RAW
  fact_gl_line.amount. Presentation = amount_sum * -1. Refreshed lazily by the
  port 8011 summary endpoint; read by no other endpoint. STAGED / off-by-default.';
COMMENT ON TABLE mart_overview_bs_balance IS
 'Overview v2 accelerator (P3): per (entity, cutoff_date, level_3) cumulative
  Σ RAW fact_gl_line.amount (posting_date <= cutoff). Magnitude = ABS(balance_sum).
  Refreshed lazily by the port 8011 summary endpoint. STAGED / off-by-default.';
COMMENT ON COLUMN mart_overview_period.amount_sum IS
 'Σ RAW fact_gl_line.amount (stored sign). Presented P&L value = amount_sum * -1.';
COMMENT ON COLUMN mart_overview_bs_balance.balance_sum IS
 'Cumulative Σ RAW fact_gl_line.amount for posting_date <= cutoff_date.';
"""


def upgrade() -> None:
    op.execute(_CREATE_SQL)
    op.execute(_COMMENT_SQL)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_mart_overview_bs_lookup;")
    op.execute("DROP INDEX IF EXISTS idx_mart_overview_period_lookup;")
    op.execute("DROP TABLE IF EXISTS mart_overview_bs_balance;")
    op.execute("DROP TABLE IF EXISTS mart_overview_period;")
