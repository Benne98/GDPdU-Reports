"""Prefix admin / org runtime tables for a clear naming convention.

PURPOSE
-------
Adopt a clear, self-documenting prefix convention for five RUNTIME tables so
their owning concern is obvious from the name:

  * ``admin_`` — admin-managed configuration (role visibility, project config).
  * ``org_``   — org-wide ingestion bookkeeping (dataset loads, mapping profiles).

Renames::

  * ``role_entity_visibility`` → ``admin_role_entity_visibility``
  * ``role_page_visibility``   → ``admin_role_page_visibility``
  * ``project_config``         → ``admin_project_config``
  * ``meta_dataset_load``      → ``org_meta_dataset_load``
  * ``ingest_mapping_profile`` → ``org_ingest_mapping_profile``

Also renames the two explicitly-named identifiers on
``ingest_mapping_profile`` (created in migration 0002) so they no longer carry
the bare ``mapping_profile`` prefix:

  * index ``idx_mapping_profile_source`` → ``org_idx_mapping_profile_source``
  * constraint ``uq_mapping_profile_name_source`` → ``org_uq_mapping_profile_name_source``

FOREIGN KEYS
------------
``meta_dataset_load`` is referenced by FK from the ``snap_*`` tables
(``ON DELETE CASCADE``) and has two self-FKs (``superseded_by_load_id``,
``restored_from_load_id``), all created in migration 0007.  Postgres
``ALTER TABLE ... RENAME TO`` automatically retargets these foreign keys to the
new table name, so no explicit FK action is needed here (verified post-migration
via information_schema).

NO-OP / GOLDEN guarantee
------------------------
These tables are accessed only via raw-SQL ``text()`` strings on the API/ETL
paths (auth visibility, ingest versioning, mapping profiles, project config);
they do NOT feed any catalogued report number.  This is a pure rename — same
rows, same data — so the golden live-vs-v2 gate stays EQUIVALENT once every code
reference is updated.

NOTE: the old creating-migrations (0001 / 0002 / 0003 / 0007 / 0011) are left
unedited; this rename operates on the live schema.
"""
from __future__ import annotations

from alembic import op

revision = "0020_prefix_admin_org_tables"
down_revision = "0019_rename_mapping_tables"
branch_labels = None
depends_on = None


# (old_table, new_table)
_TABLES = [
    ("role_entity_visibility", "admin_role_entity_visibility"),
    ("role_page_visibility", "admin_role_page_visibility"),
    ("project_config", "admin_project_config"),
    ("meta_dataset_load", "org_meta_dataset_load"),
    ("ingest_mapping_profile", "org_ingest_mapping_profile"),
]

# Explicitly-named identifiers on ingest_mapping_profile (migration 0002).
# Postgres does NOT rename indexes/constraints when a table is renamed, so we
# rename them too to avoid stale ``mapping_profile`` identifiers.
# (old_index, new_index)
_NAMED_INDEXES = [
    ("idx_mapping_profile_source", "org_idx_mapping_profile_source"),
]
# (current_table_at_rename_time, old_constraint, new_constraint)
_NAMED_CONSTRAINTS = [
    (
        "org_ingest_mapping_profile",
        "uq_mapping_profile_name_source",
        "org_uq_mapping_profile_name_source",
    ),
]


def upgrade() -> None:
    for old_table, new_table in _TABLES:
        op.rename_table(old_table, new_table)
    for old_idx, new_idx in _NAMED_INDEXES:
        op.execute(f'ALTER INDEX "{old_idx}" RENAME TO "{new_idx}";')
    for table, old_con, new_con in _NAMED_CONSTRAINTS:
        op.execute(
            f'ALTER TABLE "{table}" RENAME CONSTRAINT "{old_con}" TO "{new_con}";'
        )


def downgrade() -> None:
    for table, old_con, new_con in _NAMED_CONSTRAINTS:
        op.execute(
            f'ALTER TABLE "{table}" RENAME CONSTRAINT "{new_con}" TO "{old_con}";'
        )
    for old_idx, new_idx in _NAMED_INDEXES:
        op.execute(f'ALTER INDEX "{new_idx}" RENAME TO "{old_idx}";')
    for old_table, new_table in _TABLES:
        op.rename_table(new_table, old_table)
