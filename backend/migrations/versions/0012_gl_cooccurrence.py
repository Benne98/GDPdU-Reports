"""GL counter-account co-occurrence cache + required sibling-join index
(Journal Agent, Phase 3 — Forensic data model).

Adds TWO additive objects that the forensic data layer
(``backend/app/services/gl_forensic.py``) relies on:

  fact_gl_counter_cooccurrence
      Per-entity learned co-occurrence between a GL account ``A`` and a derived
      counter account ``B`` over ALL history.  One row per
      ``(entity_prefix, acct_ang, counter_ang)``:

        pair_freq        = COUNT(DISTINCT (journal_entry_group_number, fiscal_year))
                           journal entries in which A and B appear together on
                           opposite sides (siblings, opposite SIGN(amount)),
                           synthetic rows excluded.
        acct_total_pairs = Σ pair_freq over all counters B of A.
        freq_pct         = pair_freq / acct_total_pairs   (0 when total is 0).

      ``computed_through_year`` / ``computed_through_period`` record the
      analysed-period exclusion boundary the snapshot was learned with (the warm
      script learns over history EXCLUDING the analysed period so a current
      booking can never make itself "common").  The forensic endpoint (Phase 4)
      reads this cache when fresh, otherwise computes live.

  idx_gl_line_jeg  ON fact_gl_line (journal_entry_group_number, fiscal_year)
      REQUIRED index.  Today only ``idx_gl_line_ang`` (account_number_group,
      fiscal_year) exists; the sibling self-join that derives counter accounts and
      learns co-occurrence joins ``fact_gl_line b`` ON
      ``(journal_entry_group_number, fiscal_year)`` — without this index that side
      is a Seq Scan over the whole 1.3M-row fact table.

ADDITIVE / GOLDEN-SAFE
──────────────────────
The table is created empty and is read only by the NEW forensic data layer; the
new index is never read by any existing endpoint's query plan in a way that
changes results.  No existing payload changes → ``golden_snapshot.py compare
live v2`` stays exit 0.  ``downgrade`` drops both.
"""
from __future__ import annotations

from alembic import op

revision = "0012_gl_cooccurrence"
down_revision = "0011_project_config"
branch_labels = None
depends_on = None

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS fact_gl_counter_cooccurrence (
    entity_prefix           CHAR(2)          NOT NULL,
    acct_ang                VARCHAR(8)       NOT NULL,
    counter_ang             VARCHAR(8)       NOT NULL,
    pair_freq               INTEGER          NOT NULL DEFAULT 0,
    acct_total_pairs        INTEGER          NOT NULL DEFAULT 0,
    freq_pct                DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    computed_through_year   SMALLINT,
    computed_through_period SMALLINT,
    refreshed_at            TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    PRIMARY KEY (entity_prefix, acct_ang, counter_ang)
);

CREATE INDEX IF NOT EXISTS idx_gl_cooc_acct
    ON fact_gl_counter_cooccurrence (entity_prefix, acct_ang);

-- REQUIRED for the sibling self-join (counter-account derivation + the learner).
CREATE INDEX IF NOT EXISTS idx_gl_line_jeg
    ON fact_gl_line (journal_entry_group_number, fiscal_year);
"""


def upgrade() -> None:
    op.execute(_CREATE_SQL)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_gl_line_jeg;")
    op.execute("DROP TABLE IF EXISTS fact_gl_counter_cooccurrence;")
