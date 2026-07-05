"""0024 — OPOS as-of columns (additive) for fact_opos_debitor / fact_opos_kreditor.

Phase-1 AR/AP aging rebuild.  The 0023 DRAFT tables stored only a thin
pass-through slice of the OPOS posting.  To load the full 4-year x 2-side
subledger and reconcile it (Method A, open balance as-of year-end) we need the
remaining source fields plus the identity columns resolved via
``app.services.entities``.

ADDITIVE ONLY — every statement is ``ADD COLUMN IF NOT EXISTS`` / ``CREATE INDEX
IF NOT EXISTS`` on the two existing tables.  No column is dropped or retyped, so
the change is safe to run on a populated DB and leaves the existing pass-through
API and any existing rows untouched.  ``is_open`` / ``aging_band`` remain NULL
(aging is computed at READ time in Phase 2, not persisted here).

New columns (both tables):
  buchungskreis        INTEGER      -- company code (1000/2000/3000/4000/5000)
  satzart              VARCHAR(24)  -- Bewegung|Vortrag|Fact-Ergaenzung|Bilanzabstimmung
  partner_no           VARCHAR(32)  -- Debitor / Kreditor partner number
  partner_key          VARCHAR(32)  -- entity_prefix(2) || partner_no
  beleg_date           DATE         -- Belegdatum
  mahnstufe            SMALLINT     -- dunning level
  waehrung             VARCHAR(3)   -- transaction currency
  geschaeftsbereich    VARCHAR(64)  -- business area
  buchungsschluessel   VARCHAR(8)   -- posting key
  konto_gegenbuchung   VARCHAR(64)  -- contra account
  gobd_transaktionsnr  VARCHAR(64)  -- GoBD transaction number (lineage to fact_sales_auto)

Indexes:
  (project_id, entity_prefix, fy_label, konto)  -- Method-A recon / read scans
  (partner_key)                                 -- partner rollups / joins to dims
"""
from __future__ import annotations

from alembic import op

revision = "0024_opos_asof_columns"
down_revision = "0023_draft_opos_aging"
branch_labels = None
depends_on = None

_TABLES = ("fact_opos_debitor", "fact_opos_kreditor")

# column name -> SQL type (ADD COLUMN IF NOT EXISTS is additive/idempotent).
_ADD_COLUMNS: list[tuple[str, str]] = [
    ("buchungskreis", "INTEGER"),
    ("satzart", "VARCHAR(24)"),
    ("partner_no", "VARCHAR(32)"),
    ("partner_key", "VARCHAR(32)"),
    ("beleg_date", "DATE"),
    ("mahnstufe", "SMALLINT"),
    ("waehrung", "VARCHAR(3)"),
    ("geschaeftsbereich", "VARCHAR(64)"),
    ("buchungsschluessel", "VARCHAR(8)"),
    ("konto_gegenbuchung", "VARCHAR(64)"),
    ("gobd_transaktionsnr", "VARCHAR(64)"),
]


def upgrade() -> None:
    for table in _TABLES:
        for col, sqltype in _ADD_COLUMNS:
            op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {sqltype};")
        op.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_scope_konto "
            f"ON {table} (project_id, entity_prefix, fy_label, konto);"
        )
        op.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_partner_key "
            f"ON {table} (partner_key);"
        )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP INDEX IF EXISTS idx_{table}_partner_key;")
        op.execute(f"DROP INDEX IF EXISTS idx_{table}_scope_konto;")
        for col, _sqltype in _ADD_COLUMNS:
            op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {col};")
