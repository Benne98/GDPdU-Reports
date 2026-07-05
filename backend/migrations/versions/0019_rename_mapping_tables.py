"""Rename mapping-library & override tables to a clear prefix convention.

PURPOSE
-------
Adopt a clear, self-documenting naming convention for the GL-mapping reference
tables introduced in migrations 0017 / 0018:

  * ``lib_`` — project-independent, reusable libraries (accumulate across projects).
  * ``ovr_`` — project-specific overrides (per-account pins).

Renames (table + every explicitly- and implicitly-named index/constraint that
still carries the OLD table name, since Postgres does NOT rename constraints or
indexes when a table is renamed):

  * ``na_mapping_library`` → ``lib_na_mapping``
  * ``cf_mapping_library`` → ``lib_cf_mapping``
  * ``na_override``        → ``ovr_na_mapping``

NO-OP / GOLDEN guarantee
------------------------
These tables are referenced ONLY by the ETL / populate scripts
(``load_*_mapping_library.py``, ``populate_dim_gl_*.py``), never by the live
report SQL.  This is a pure rename: same rows, same data, same downstream
``dim_gl_na`` / ``dim_gl_cf`` output — so the golden live-vs-v2 gate stays
EQUIVALENT.
"""
from __future__ import annotations

from alembic import op

revision = "0019_rename_mapping_tables"
down_revision = "0018_na_mapping_library"
branch_labels = None
depends_on = None


# (old_table, new_table)
_TABLES = [
    ("na_mapping_library", "lib_na_mapping"),
    ("cf_mapping_library", "lib_cf_mapping"),
    ("na_override", "ovr_na_mapping"),
]

# Per-table constraint suffixes that Postgres auto-named after the OLD table.
# On a table rename Postgres keeps these old names, so we rename them too to
# avoid stale ``na_*`` / ``cf_*`` identifiers lingering on the renamed tables.
_CONSTRAINT_SUFFIXES = {
    "na_mapping_library": [
        "pkey",
        "account_name_not_null",
        "na_mapping_not_null",
        "na_description_not_null",
        "occurrences_not_null",
        "source_not_null",
        "updated_at_not_null",
    ],
    "cf_mapping_library": [
        "pkey",
        "key_kind_not_null",
        "key_1_not_null",
        "key_2_not_null",
        "source_not_null",
        "updated_at_not_null",
    ],
    "na_override": [
        "pkey",
        "account_number_group_not_null",
        "fiscal_year_not_null",
        "na_mapping_not_null",
        "na_description_not_null",
        "source_not_null",
        "updated_at_not_null",
    ],
}

# Explicitly-named secondary indexes (created by name in 0018).
# (current_table_name_AT_rename_time, old_index, new_index)
_NAMED_INDEXES = [
    ("lib_na_mapping", "ix_na_mapping_library_name", "ix_lib_na_mapping_name"),
]


def _rename_constraints(old_table: str, new_table: str) -> None:
    # NOTE: the table has already been renamed to ``new_table`` at this point,
    # but the constraints still carry the OLD ``old_table`` prefix.
    for suffix in _CONSTRAINT_SUFFIXES[old_table]:
        old_con = f"{old_table}_{suffix}"
        new_con = f"{new_table}_{suffix}"
        op.execute(
            f'ALTER TABLE "{new_table}" RENAME CONSTRAINT "{old_con}" TO "{new_con}";'
        )


def upgrade() -> None:
    for old_table, new_table in _TABLES:
        op.rename_table(old_table, new_table)
        _rename_constraints(old_table, new_table)
    for _table, old_idx, new_idx in _NAMED_INDEXES:
        op.execute(f'ALTER INDEX "{old_idx}" RENAME TO "{new_idx}";')


def downgrade() -> None:
    # Reverse order: indexes back, then constraints + tables.
    for _table, old_idx, new_idx in _NAMED_INDEXES:
        op.execute(f'ALTER INDEX "{new_idx}" RENAME TO "{old_idx}";')
    for old_table, new_table in _TABLES:
        # Restore the old constraint names while the table is still ``new_table``.
        for suffix in _CONSTRAINT_SUFFIXES[old_table]:
            old_con = f"{old_table}_{suffix}"
            new_con = f"{new_table}_{suffix}"
            op.execute(
                f'ALTER TABLE "{new_table}" RENAME CONSTRAINT "{new_con}" TO "{old_con}";'
            )
        op.rename_table(new_table, old_table)
