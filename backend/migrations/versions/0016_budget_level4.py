"""Budget L4 grain — add ``fact_position_plan.level_4`` + extend the PK
(Budget-Planung rework, Phase 0 — Storage).

WHY
───
``fact_position_plan`` currently keys a budget cell at the L3 reporting position
(``line_code``) grain::

    PK (statement, line_code, entity_prefix, partner_id, fiscal_year,
        fiscal_period, scenario)

The rework lets staff plan a position EITHER as one L3-level number OR as its set
of L4 sub-positions (e.g. "Gross sales" under NET_SALES).  The reader
(:func:`fin_compat_sql.position_plan_grain_sql`, ``_fetch_bs_budget_movements``)
already ``GROUP BY line_code`` and SUMS every row for a line_code → the L4 rows
fold into the position total automatically, so NO reader change is needed in this
phase.

STORAGE CHANGE (this migration)
───────────────────────────────
Add ``level_4 VARCHAR(120) NOT NULL DEFAULT ''`` — ``''`` marks the L3-level row,
a non-empty value is the L4 sub-position key.  Extend the PK to include
``level_4`` so an L3-level row and its L4 rows can coexist physically::

    PK (statement, line_code, entity_prefix, partner_id, level_4,
        fiscal_year, fiscal_period, scenario)

Existing rows get ``level_4 = ''`` (the column default), so the new PK is unique
over the existing data.  The double-count guard (a line_code is planned as EITHER
its '' row OR its L4 rows, never both) is enforced in the WRITE path
(``budget_service._clear_complementary_level``), NOT by the schema.

ADDITIVE / GOLDEN-SAFE
──────────────────────
A new column with a constant default and a widened PK — no existing row's output
changes and the readers are untouched.  With no budget rows (the golden capture
has none) ``golden_snapshot.py compare live v2`` stays EQUIVALENT (byte-identical).
``downgrade`` drops the column and restores the original PK.

The PK constraint is dropped/re-added by NAME discovered at runtime (a DO block
reads pg_constraint), so it works regardless of the auto-generated constraint
name (``fact_position_plan_pkey`` on a fresh install).
"""
from __future__ import annotations

from alembic import op

revision = "0016_budget_level4"
down_revision = "0015_widen_anomaly_algo_version"
branch_labels = None
depends_on = None


# Drop whichever PRIMARY KEY constraint currently exists on fact_position_plan,
# then add the given column list as the new PK.  Discovering the name at runtime
# avoids hard-coding the auto-generated ``*_pkey`` name.
def _repk_sql(new_pk_columns: str) -> str:
    return f"""
DO $$
DECLARE
    pk_name text;
BEGIN
    SELECT c.conname INTO pk_name
    FROM pg_constraint c
    JOIN pg_class t ON c.conrelid = t.oid
    WHERE t.relname = 'fact_position_plan' AND c.contype = 'p';

    IF pk_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE fact_position_plan DROP CONSTRAINT %I', pk_name);
    END IF;

    ALTER TABLE fact_position_plan
        ADD PRIMARY KEY ({new_pk_columns});
END
$$;
"""


_L4_PK = (
    "statement, line_code, entity_prefix, partner_id, level_4, "
    "fiscal_year, fiscal_period, scenario"
)
_ORIG_PK = (
    "statement, line_code, entity_prefix, partner_id, "
    "fiscal_year, fiscal_period, scenario"
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE fact_position_plan "
        "ADD COLUMN IF NOT EXISTS level_4 VARCHAR(120) NOT NULL DEFAULT '';"
    )
    op.execute(_repk_sql(_L4_PK))


def downgrade() -> None:
    # Restore the original PK first (drops the L4-aware PK), then drop the column.
    op.execute(_repk_sql(_ORIG_PK))
    op.execute("ALTER TABLE fact_position_plan DROP COLUMN IF EXISTS level_4;")
