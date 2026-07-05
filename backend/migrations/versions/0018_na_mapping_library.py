"""NA mapping library + per-account override — reusable, name-keyed NA classification.

PURPOSE
-------
The Net-asset / Working-capital classification ``dim_gl_na``
``(l6_na_mapping, l7_na_description)`` is currently stored **per account**
(``account_number_group``, ``fiscal_year``).  The same business account
(same ``dim_gl_account.account_name``) recurs across legal entities and fiscal
years, so the classification is really a property of the *account name*, not of
the per-entity GL number.  This migration introduces the reusable LIBRARY that
turns the per-account classification into a name-keyed reference — exactly the
shape of the CF mapping library (migration 0017) one layer up.

  * ``na_mapping_library`` — one row per **observed** ``(account_name, na_mapping,
    na_description)`` with an ``occurrences`` count.  The populate step resolves
    each account to the MOST-FREQUENT mapping for its ``account_name`` (with a
    deterministic tiebreaker), so a new project whose accounts carry the same
    names auto-classifies with no per-account re-entry.

  * ``na_override`` — a per-account PIN keyed ``(account_number_group,
    fiscal_year)`` that takes PRECEDENCE over the library.  Two writers:
      - the future Project-Setup reclassification UI (``source='manual'`` etc.);
      - the populate step's TOTALS-GUARD (``source='totals_guard'``), which
        auto-pins any account whose most-frequent resolution would change a
        roll-up total (WC membership flip or CF band change) back to its CURRENT
        mapping — guaranteeing NWC + every CF subtotal stay identical.

NO-OP / GOLDEN guarantee
------------------------
Both tables are created EMPTY.  They feed a separate populate step
(``backend/scripts/populate_dim_gl_na.py``); creating them changes no reporting
output, so the golden live-vs-v2 gate is unaffected by the migration itself.
The classification is re-derived (and CF re-derived from it) by the populate
step, applied identically to BOTH databases.
"""
from __future__ import annotations

from alembic import op

revision = "0018_na_mapping_library"
down_revision = "0017_cf_mapping_library"
branch_labels = None
depends_on = None

_CREATE_LIBRARY_SQL = """
CREATE TABLE IF NOT EXISTS na_mapping_library (
    account_name    VARCHAR(500) NOT NULL,           -- dim_gl_account.account_name
    na_mapping      VARCHAR(200) NOT NULL,           -- observed l6_na_mapping (TWC/OWC/ND/...)
    na_description  VARCHAR(500) NOT NULL,           -- observed l7_na_description
    occurrences     INTEGER      NOT NULL DEFAULT 0, -- how often (account x fy) carried this
    source          VARCHAR(120) NOT NULL DEFAULT 'seed',
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (account_name, na_mapping, na_description)
);
"""

# Index for the resolver's GROUP/argmax-by-name lookup.
_CREATE_LIBRARY_IDX = """
CREATE INDEX IF NOT EXISTS ix_na_mapping_library_name
    ON na_mapping_library (account_name);
"""

_CREATE_OVERRIDE_SQL = """
CREATE TABLE IF NOT EXISTS na_override (
    account_number_group VARCHAR(64)  NOT NULL,
    fiscal_year          SMALLINT     NOT NULL,
    na_mapping           VARCHAR(200) NOT NULL,      -- pinned l6_na_mapping
    na_description       VARCHAR(500) NOT NULL,      -- pinned l7_na_description
    source               VARCHAR(120) NOT NULL DEFAULT 'manual',  -- 'manual' | 'totals_guard' | ...
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (account_number_group, fiscal_year)
);
"""


def upgrade() -> None:
    op.execute(_CREATE_LIBRARY_SQL)
    op.execute(_CREATE_LIBRARY_IDX)
    op.execute(_CREATE_OVERRIDE_SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS na_override;")
    op.execute("DROP INDEX IF EXISTS ix_na_mapping_library_name;")
    op.execute("DROP TABLE IF EXISTS na_mapping_library;")
