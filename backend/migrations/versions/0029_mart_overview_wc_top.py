"""0029 — Overview v2 mart extension: WC balances + top-entities (Phase 1).

SCOPE / ADDITIVE-INERT GUARANTEE
--------------------------------
This migration ADDS TWO new, EMPTY snapshot tables that extend the Overview-v2
(:8011) accelerator introduced in ``0026_mart_overview_period``.  Like 0026 they
are OPTIONAL accelerators: NOTHING reads them yet (no endpoint, no read helper is
wired in this phase), and nothing on the SHARED ingest/derive path writes them.
They are refreshed only by the operator script
``backend/scripts/refresh_mart_overview.py``.  Therefore this migration is additive
and inert on the 5176/8010/8012 stacks — the golden live-vs-v2 equivalence is
unaffected and the mart flag stays OFF by default.

  * ``mart_overview_wc_balance``    — Working-capital cumulative stock balances
                                      keyed (project_id, entity_prefix, cutoff_date,
                                      l6_na_mapping, level_3).  Restricted to the
                                      TWC/OWC accounts (``dim_gl_na.l6_na_mapping``),
                                      i.e. the working-capital SUBSET of the balance
                                      sheet (see ``fin_compat_wc_sql``).
  * ``mart_overview_top_entities``  — Per-partner (customer/supplier) monthly
                                      turnover magnitudes keyed
                                      (project_id, entity_prefix, partner_type,
                                      partner_id, month_end).  Feeds the top
                                      customers/suppliers view
                                      (``overview_top_entities.build_top_entities``).

SIGN / SCALE CONVENTION (documented; read helpers reproduce builder semantics)
------------------------------------------------------------------------------
* ``mart_overview_wc_balance.balance_sum`` stores the RAW stored GL sign
  (``SUM(fact_gl_line.amount)``, NO ``* -1``) — assets positive, liabilities
  negative — exactly like the live WC layer (which presents raw signed balances,
  NWC = straight Σ TWC+OWC).  NO presentation inversion is baked into the mart.
* ``mart_overview_top_entities.amount_eur`` stores the POSITIVE magnitude in RAW EUR
  (``Σ value_col``, exact cents at NUMERIC(20,2), NO ``/1000`` and NO early kEUR
  rounding): ``fact_sales.gross_sales`` (revenue, stored −amount → positive) and
  ``fact_com.cost_of_materials`` (cost, stored +amount → positive) are both naturally
  positive magnitudes, so no flip is applied (mirrors ``overview_top_entities``).
  Storing full-precision EUR lets the read path sum across months and ``/1000`` +
  round ONCE at the end, matching the live builder to the cent (Bug 2 fix).

PROVENANCE / STALENESS
----------------------
Each row carries ``source_load_id`` (the ``org_meta_dataset_load.load_id`` the mart
was built from) and ``refreshed_at``.  WC is stamped from the latest ``'gl'`` load
(fact_gl_line source); top-entities is stamped from the latest partner-fact load
(``fact_sales`` / ``fact_com`` are derived from the GL load in this stack, so it
falls back to the ``'gl'`` load when no dedicated sales/com load exists).

CONVENTIONS
-----------
Lineage / tenancy columns follow 0026 / ``fact_fixed_asset``: ``project_id VARCHAR
default 'default'`` and a soft ``source_load_id`` FK to
``org_meta_dataset_load (load_id)`` with ``ON DELETE SET NULL`` so purging a load
never deletes mart rows silently.
"""
from __future__ import annotations

from alembic import op

revision = "0029_mart_overview_wc_top"
down_revision = "0028_fact_fixed_asset_label"
branch_labels = None
depends_on = None


_CREATE_SQL = """
-- ============================================================ WC cumulative balances
CREATE TABLE IF NOT EXISTS mart_overview_wc_balance (
    id             BIGSERIAL PRIMARY KEY,
    project_id     VARCHAR(64) NOT NULL DEFAULT 'default',
    entity_prefix  VARCHAR(2)  NOT NULL,
    -- as-of cutoff (month-end): balance_sum = Σ amount WHERE posting_date <= cutoff
    cutoff_date    DATE        NOT NULL,
    -- working-capital classifier (dim_gl_na.l6_na_mapping): 'TWC' | 'OWC'
    l6_na_mapping  VARCHAR(8)  NOT NULL,
    level_2        VARCHAR(120),
    level_3        VARCHAR(120) NOT NULL,
    -- Σ RAW fact_gl_line.amount (stored sign, NO * -1) for posting_date <= cutoff.
    balance_sum    NUMERIC(20, 2) NOT NULL DEFAULT 0,
    -- provenance ---------------------------------------------------------
    source_load_id INTEGER     REFERENCES org_meta_dataset_load (load_id) ON DELETE SET NULL,
    refreshed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_mart_overview_wc_balance
        UNIQUE (project_id, entity_prefix, cutoff_date, l6_na_mapping, level_3)
);

CREATE INDEX IF NOT EXISTS idx_mart_overview_wc_lookup
    ON mart_overview_wc_balance (project_id, entity_prefix, cutoff_date);

-- ============================================================ Top customers / suppliers
CREATE TABLE IF NOT EXISTS mart_overview_top_entities (
    id             BIGSERIAL PRIMARY KEY,
    project_id     VARCHAR(64) NOT NULL DEFAULT 'default',
    entity_prefix  VARCHAR(2)  NOT NULL,
    -- 'customer' (fact_sales/Net sales) | 'supplier' (fact_com/Cost of materials)
    partner_type   VARCHAR(10) NOT NULL,
    partner_id     VARCHAR(64),
    partner_name   VARCHAR(256),
    -- month-end of date_trunc('month', posting_date)
    month_end      DATE        NOT NULL,
    -- Σ value_col in RAW EUR (positive magnitude, exact cents at NUMERIC(20,2)).
    -- Stored full-precision (NO /1000, NO early kEUR rounding) so the read path can
    -- sum EUR across months and divide by 1000 + round ONCE at the end — matching
    -- the live builder (Bug 2 fix; was ``amount_keur`` pre-rounded to 2dp kEUR).
    amount_eur     NUMERIC(20, 2) NOT NULL DEFAULT 0,
    -- provenance ---------------------------------------------------------
    source_load_id INTEGER     REFERENCES org_meta_dataset_load (load_id) ON DELETE SET NULL,
    refreshed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_mart_overview_top_entities
        UNIQUE (project_id, entity_prefix, partner_type, partner_id, month_end)
);

CREATE INDEX IF NOT EXISTS idx_mart_overview_top_lookup
    ON mart_overview_top_entities (project_id, entity_prefix, partner_type, month_end);
"""

_COMMENT_SQL = """
COMMENT ON TABLE mart_overview_wc_balance IS
 'Overview v2 accelerator (Phase 1): per (entity, cutoff_date, l6_na_mapping,
  level_3) cumulative Σ RAW fact_gl_line.amount (posting_date <= cutoff), restricted
  to TWC/OWC working-capital accounts. RAW sign (assets +, liabilities −), NO flip.
  Refreshed by scripts/refresh_mart_overview.py; read by no endpoint. STAGED / off.';
COMMENT ON TABLE mart_overview_top_entities IS
 'Overview v2 accelerator (Phase 1): per (entity, partner_type, partner_id,
  month_end) Σ value_col in RAW EUR (positive magnitude) from fact_sales (Net sales)
  / fact_com (Cost of materials). Refreshed by scripts/refresh_mart_overview.py;
  read by no endpoint. STAGED / off-by-default.';
COMMENT ON COLUMN mart_overview_wc_balance.balance_sum IS
 'Cumulative Σ RAW fact_gl_line.amount for posting_date <= cutoff_date (no * -1).
  Opening balances follow the earliest-OB-only carry-forward (fin_compat_bs_sql).';
COMMENT ON COLUMN mart_overview_top_entities.amount_eur IS
 'Σ value_col in RAW EUR (positive magnitude, exact cents). No /1000, no early
  kEUR rounding; the read path divides by 1000 and rounds ONCE at the end.';
"""


def upgrade() -> None:
    op.execute(_CREATE_SQL)
    op.execute(_COMMENT_SQL)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_mart_overview_top_lookup;")
    op.execute("DROP INDEX IF EXISTS idx_mart_overview_wc_lookup;")
    op.execute("DROP TABLE IF EXISTS mart_overview_top_entities;")
    op.execute("DROP TABLE IF EXISTS mart_overview_wc_balance;")
