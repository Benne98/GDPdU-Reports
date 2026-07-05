"""Account (chart-of-accounts) mapping library + per-account override.

PURPOSE
-------
The financial-statement hierarchy ``dim_gl_account``
``(level_0..4, l4_sub, level_2_sort, level_3_sort, is_ic)`` is stored **per
account** keyed ``(account_number_group, fiscal_year)``, populated from the
project's mapping file (``etl.mapping_account.apply_account_mapping``).  When that
mapping file only covers fiscal years ``a..b``, accounts that appear in
``fact_gl_line`` in OTHER years get no ``dim_gl_account`` row → they are
unclassified → reports miss them.

The same business account (same ``dim_gl_account.account_name``) recurs across
legal entities and fiscal years, so the hierarchy is really a property of the
*account name*.  This migration introduces the reusable LIBRARY that turns the
per-account classification into a name-keyed reference — exactly the shape of the
NA mapping library (migration 0018 → renamed ``lib_na_mapping`` in 0019) one
concern over.

  * ``lib_account_mapping`` — one row per **observed** ``(account_name, level_0,
    level_2, level_3, level_4)`` hierarchy with an ``occurrences`` count.  The
    fill step resolves each missing-year account to the MOST-FREQUENT hierarchy
    for its ``account_name`` (with a deterministic tiebreaker), so an account
    mapped in FY1 only auto-classifies in FY2/FY3 with no per-account re-entry.

  * ``ovr_account_mapping`` — a per-account PIN keyed ``(account_number_group,
    fiscal_year)`` that takes PRECEDENCE over the library.  The future
    Project-Setup reclassification UI writes here.

NO-OP / GOLDEN guarantee
------------------------
Both tables are created EMPTY.  They feed separate scripts
(``backend/scripts/load_account_mapping_library.py`` seeds the library,
``backend/scripts/populate_dim_gl_account_fill.py`` fills MISSING (account, year)
rows only — it never touches an existing ``dim_gl_account`` row).  Creating the
tables changes no reporting output, so the golden live-vs-v2 gate is unaffected
by the migration itself.  On v2/live, whose mapping already covers every year,
the fill is a no-op (no missing rows) → ``compare live v2`` stays EQUIVALENT.
"""
from __future__ import annotations

from alembic import op

revision = "0021_account_mapping_library"
down_revision = "0020_prefix_admin_org_tables"
branch_labels = None
depends_on = None

# Most-frequent reference, keyed by account NAME.  The PK collapses one row per
# DISTINCT presented hierarchy ``(account_name, level_0, level_2, level_3,
# level_4)`` — duplicates across (account x fy) accumulate into ``occurrences``,
# which is what the resolver maximises.  level_1 / l4_sub / sorts / is_ic ride
# along with the winning hierarchy (not part of the key — they are determined by
# the level_0..4 spine).
_CREATE_LIBRARY_SQL = """
CREATE TABLE IF NOT EXISTS lib_account_mapping (
    account_name  VARCHAR(500) NOT NULL,            -- dim_gl_account.account_name
    level_0       VARCHAR(50)  NOT NULL DEFAULT '', -- BS | PL
    level_1       VARCHAR(200) NOT NULL DEFAULT '',
    level_2       VARCHAR(200) NOT NULL DEFAULT '',
    level_3       VARCHAR(200) NOT NULL DEFAULT '',
    level_4       VARCHAR(200) NOT NULL DEFAULT '',
    l4_sub        VARCHAR(200),
    level_2_sort  INTEGER,
    level_3_sort  INTEGER,
    is_ic         BOOLEAN      NOT NULL DEFAULT FALSE,
    occurrences   INTEGER      NOT NULL DEFAULT 0,   -- how many (account x fy) carry this
    source        VARCHAR(120) NOT NULL DEFAULT 'seed',
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (account_name, level_0, level_2, level_3, level_4)
);
"""

# Index for the resolver's GROUP/argmax-by-name lookup.
_CREATE_LIBRARY_IDX = """
CREATE INDEX IF NOT EXISTS ix_lib_account_mapping_name
    ON lib_account_mapping (account_name);
"""

_CREATE_OVERRIDE_SQL = """
CREATE TABLE IF NOT EXISTS ovr_account_mapping (
    account_number_group VARCHAR(64) NOT NULL,
    fiscal_year          SMALLINT    NOT NULL,
    level_0       VARCHAR(50)  NOT NULL DEFAULT '',
    level_1       VARCHAR(200) NOT NULL DEFAULT '',
    level_2       VARCHAR(200) NOT NULL DEFAULT '',
    level_3       VARCHAR(200) NOT NULL DEFAULT '',
    level_4       VARCHAR(200) NOT NULL DEFAULT '',
    l4_sub        VARCHAR(200),
    level_2_sort  INTEGER,
    level_3_sort  INTEGER,
    is_ic         BOOLEAN     NOT NULL DEFAULT FALSE,
    source        VARCHAR(120) NOT NULL DEFAULT 'manual',  -- 'manual' | 'project_setup' | ...
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (account_number_group, fiscal_year)
);
"""


def upgrade() -> None:
    op.execute(_CREATE_LIBRARY_SQL)
    op.execute(_CREATE_LIBRARY_IDX)
    op.execute(_CREATE_OVERRIDE_SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ovr_account_mapping;")
    op.execute("DROP INDEX IF EXISTS ix_lib_account_mapping_name;")
    op.execute("DROP TABLE IF EXISTS lib_account_mapping;")
