"""CF mapping library — reusable background reference for populating dim_gl_cf.

PURPOSE
-------
The Cash-Flow statement reads GL movements grouped by ``dim_gl_cf.cf_mapping``
(the leaf the flat CF structure matches).  ``dim_gl_cf`` is *derived* reference
data — one row per (account_number_group, fiscal_year) — and was empty, which
zeroed the whole CF statement.

This library is the **shared, accumulating source** the populate step joins to
fill ``dim_gl_cf`` for ANY project whose accounts are classified.  It is keyed by
the *classification* (NOT the account number) so a new project that carries the
same NA / P&L level-3 classification auto-matches with no per-project re-entry:

  * BS / Net-asset accounts are keyed by the NA classification
    ``(l6_na_mapping, l7_na_description)`` — exactly the columns in
    ``dim_gl_na`` — so ``key_kind='na'``, ``key_1=na_mapping``,
    ``key_2=na_description``.
  * P&L accounts are keyed by the income-statement ``level_3`` classification
    (the workbook covers only the BS/NA side) — ``key_kind='pl_level3'``,
    ``key_1='PL'``, ``key_2=level_3``.

A discriminated (key_kind, key_1, key_2) PK keeps it ONE reusable table covering
both sides; new mappings simply accumulate as more rows (idempotent UPSERT in the
loader).

NO-OP / GOLDEN guarantee
------------------------
The table is created empty.  It feeds a separate populate step; creating it
changes no reporting output, so the golden live-vs-v2 gate is unaffected by the
migration itself.  (CF is restored by the *populate* step, applied identically to
both DBs.)
"""
from __future__ import annotations

from alembic import op

revision = "0017_cf_mapping_library"
down_revision = "0016_budget_level4"
branch_labels = None
depends_on = None

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS cf_mapping_library (
    key_kind        VARCHAR(16)  NOT NULL,           -- 'na' | 'pl_level3'
    key_1           VARCHAR(200) NOT NULL,           -- na: l6_na_mapping ; pl: 'PL'
    key_2           VARCHAR(500) NOT NULL,           -- na: l7_na_description ; pl: level_3
    l1              VARCHAR(200),
    l2              VARCHAR(200),
    l3              VARCHAR(200),
    l4              VARCHAR(200),
    l5              VARCHAR(200),
    cf_mapping      VARCHAR(200),
    l2_sort         INTEGER,
    l3_sort         INTEGER,
    source          VARCHAR(120) NOT NULL DEFAULT 'unknown',  -- provenance (workbook name / 'pl_structure_derived')
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (key_kind, key_1, key_2)
);
"""


def upgrade() -> None:
    op.execute(_CREATE_SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS cf_mapping_library;")
