"""D4 DRAFT — OPOS (open items) Debitor + Kreditor pass-through facts.

SCOPE / DRAFT GUARANTEE
-----------------------
This migration creates TWO new, EMPTY two-sided fact tables that store the raw
OPOS postings exactly as mapped from the source file (one per side, mirroring the
customer/supplier partner split):

  * fact_opos_debitor   — receivables side (Debitoren)
  * fact_opos_kreditor  — payables side    (Kreditoren)

Every business column is a PASS-THROUGH of the source value.  The two analytic
columns are created NULLABLE and are LEFT NULL by the ingest path:

  * is_open      BOOLEAN  — open-item determination (settlement matching of RV vs
                           ZA by beleg_no / referenz) is NOT implemented (F3).
  * aging_band   VARCHAR  — AR/AP aging bucketing is NOT implemented (F4); when it
                           is, it MUST reuse ``app.services.gl_aging.AR_BANDS`` +
                           ``_band_case_sql`` (do NOT invent new buckets).

Both are DEFERRED to the financial-calculation-engineer — see the FLAGGED section
in ``docs/financial-logic.md`` (F3, F4).  Tables are created empty and read by no
existing endpoint, so the golden live-vs-v2 equivalence is unaffected.

CONVENTIONS
-----------
Lineage / tenancy columns follow the same convention as 0022 / the project
config tables: ``project_id`` VARCHAR default 'default' (no hard FK yet),
``dataset_version_id`` -> ``org_meta_dataset_load(load_id)``, plus
``source_file_id`` / ``row_no`` / ``created_at`` per-row lineage.  ``partner_id``
is ``entity_prefix(2) || konto`` — the same join-key shape as the partner flow.
"""
from __future__ import annotations

from alembic import op

revision = "0023_draft_opos_aging"
down_revision = "0022_draft_fixed_asset_register"
branch_labels = None
depends_on = None


def _create_table_sql(table: str, side: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {table} (
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
    -- open-item posting (pass-through) ----------------------------------
    partner_id          VARCHAR(64),   -- entity_prefix(2) || konto join-key
    konto               VARCHAR(64),
    belegart            VARCHAR(40),   -- Belegart / document type
    beleg_no            VARCHAR(80),   -- Belegnummer
    referenz            VARCHAR(120),  -- Referenz
    net_due_date        DATE,          -- Nettofaelligkeit
    amount_hauswaehrung NUMERIC(18,2), -- signed Betrag in Hauswaehrung
    posting_date        DATE,
    -- DRAFT analytics (NOT computed; left NULL by ingest) ---------------
    is_open             BOOLEAN,       -- F3: settlement matching NOT implemented
    aging_band          VARCHAR(40)    -- F4: reuse gl_aging.AR_BANDS (NOT implemented)
);

CREATE INDEX IF NOT EXISTS idx_{table}_scope
    ON {table} (project_id, entity_prefix, fy_label);
CREATE INDEX IF NOT EXISTS idx_{table}_partner
    ON {table} (partner_id);

COMMENT ON TABLE {table} IS
 'DRAFT (D4, {side}): pass-through OPOS postings. is_open (F3 settlement matching)
  and aging_band (F4, reuse gl_aging.AR_BANDS) NOT computed; see docs/financial-logic.md.';
"""


def upgrade() -> None:
    op.execute(_create_table_sql("fact_opos_debitor", "debitor"))
    op.execute(_create_table_sql("fact_opos_kreditor", "kreditor"))


def downgrade() -> None:
    for table in ("fact_opos_debitor", "fact_opos_kreditor"):
        op.execute(f"DROP INDEX IF EXISTS idx_{table}_partner;")
        op.execute(f"DROP INDEX IF EXISTS idx_{table}_scope;")
        op.execute(f"DROP TABLE IF EXISTS {table};")
