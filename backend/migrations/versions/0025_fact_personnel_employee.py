"""0025 — Personnel employee snapshots (personaltable pass-through).

One row per employee per entity per as-of date.  Populated by
``backend/scripts/load_personnel_subledger.py`` from yearly personaltable.xlsx
files; upload flow deferred to a later epic.
"""
from __future__ import annotations

from alembic import op

revision = "0025_fact_personnel_employee"
down_revision = "0024_opos_asof_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE IF NOT EXISTS fact_personnel_employee (
    id                  BIGSERIAL PRIMARY KEY,
    project_id          VARCHAR(64)  NOT NULL DEFAULT 'default',
    dataset_version_id  INTEGER      REFERENCES org_meta_dataset_load (load_id) ON DELETE SET NULL,
    source_file_id      VARCHAR(256),
    row_no              INTEGER,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    as_of_date          DATE         NOT NULL,
    entity_prefix       VARCHAR(2),
    entity_name         VARCHAR(64),
    fy_label            VARCHAR(20),
    personalnummer      VARCHAR(32)  NOT NULL,
    bereich             VARCHAR(80),
    bereichuntergruppe  VARCHAR(80),
    gew_ang             VARCHAR(40),
    zugehoerigkeit      NUMERIC(6, 1),
    beschaeftigungsgrad NUMERIC(6, 2),
    kommentar           TEXT,
    kst_name            VARCHAR(80),
    kostenstelle        VARCHAR(32),
    months_active       NUMERIC(6, 2),
    lohnstunden         NUMERIC(12, 2),
    era_gruppe          VARCHAR(32),
    gehalt_mon          NUMERIC(18, 2),
    grundgehalt         NUMERIC(18, 2),
    praemie             NUMERIC(18, 2),
    urlaubsgeld         NUMERIC(18, 2),
    tzug                NUMERIC(18, 2),
    transformationsgeld NUMERIC(18, 2),
    sozialversicherung  NUMERIC(18, 2),
    kontofuehrungsgebuehr NUMERIC(18, 2),
    schichtzulagen      NUMERIC(18, 2),
    fahrgeldzuschuss    NUMERIC(18, 2),
    kosten_leihpersonal NUMERIC(18, 2),
    pausch_offene_verhandlungen NUMERIC(18, 2),
    tariferhoehungen    NUMERIC(18, 2),
    gesamtsumme         NUMERIC(18, 2),
    quelle              VARCHAR(64),
    CONSTRAINT uq_personnel_employee_snapshot
        UNIQUE (project_id, entity_prefix, as_of_date, personalnummer)
);

CREATE INDEX IF NOT EXISTS idx_personnel_employee_scope
    ON fact_personnel_employee (project_id, as_of_date, entity_prefix);
CREATE INDEX IF NOT EXISTS idx_personnel_employee_bereich
    ON fact_personnel_employee (project_id, as_of_date, bereich);

COMMENT ON TABLE fact_personnel_employee IS
 'Personaltable employee-grain snapshots keyed by as_of_date (year-end or future monthly).';
""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS fact_personnel_employee CASCADE;")
