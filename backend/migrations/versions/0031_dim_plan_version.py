"""0031 — Versioned plans: dim_plan_version + plan_version_id FK + backfill (Phase 4).

DECISION 4 (docs/plans/v5-pipeline-rework.md)
---------------------------------------------
Introduce **versioned plans**.  A plan version is scoped to
``(project_id, statement, fiscal_year)`` and carries an ``is_active`` flag and an
``include_in_reporting`` toggle.  The invariant is **exactly ONE active version per
(project, statement, fiscal_year)** — ``scenario`` is deliberately NOT part of the
key.  Forecast stops being a separately-stored ``scenario='forecast'`` band and
becomes a DERIVED column of the single active version (actuals-YTD ⊕ the active
version's remaining-month plan); see ``docs/financial-logic.md`` "Phase 4".

This migration is ADDITIVE + BACKFILLED so an already-seeded stack keeps working:

  1. ``dim_plan_version`` — the version registry.  A PARTIAL UNIQUE INDEX over
     ``(COALESCE(project_id,-1), statement, fiscal_year) WHERE is_active`` enforces
     the "exactly one active" invariant at the DB level (``COALESCE`` folds the
     NULL/global project scope into one bucket — ``fact_position_plan`` has no
     project_id today, the tenant-isolation gap, so NULL = the default/global
     project).
  2. ``fact_position_plan.plan_version_id`` — nullable FK linking each plan cell to
     its version.  Nullable so pre-existing rows (and any future un-linked write)
     are valid.
  3. ``fact_gl_plan.plan_version_id`` — the same nullable FK for faithfulness with
     decision 4 ("link ... and fact_gl_plan if it carries plan").  NOT backfilled:
     the ``fact_gl_plan`` forecast/plan bands are the LEGACY forecast the derived
     model supersedes; the column exists for future linkage but no reader depends
     on it.
  4. BACKFILL — one default ``'v1'`` version per distinct
     ``(statement, fiscal_year)`` that already has ``fact_position_plan`` rows,
     ``is_active=TRUE``, ``include_in_reporting=TRUE`` (project_id NULL), then link
     every existing ``fact_position_plan`` row to it.  So a seeded DB resolves to
     ``active_included`` and nothing breaks.

GOLDEN-SAFETY.  A DB WITHOUT this migration (no ``dim_plan_version``, no
``plan_version_id``) must not 500: the readers gate on
``plan_version.resolve_plan_scope`` which returns ``legacy`` when the table is
absent, keeping the pre-Phase-4 behaviour byte-identical.  On a DB WITH no plan
rows the backfill inserts nothing and the columns are all NULL — also unchanged.

ROUND-TRIP.  ``downgrade`` drops the two FK columns and the table (+ indexes),
restoring the pre-Phase-4 schema exactly.
"""
from __future__ import annotations

from alembic import op

revision = "0031_dim_plan_version"
down_revision = "0030_split_statement_structures"
branch_labels = None
depends_on = None


_CREATE_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS dim_plan_version (
    plan_version_id      SERIAL PRIMARY KEY,
    project_id           INTEGER,                         -- NULL = default/global scope
    statement            VARCHAR(2)  NOT NULL,            -- 'PL' | 'BS' | 'CF'
    fiscal_year          SMALLINT    NOT NULL,
    label                VARCHAR(200) NOT NULL DEFAULT 'v1',
    is_active            BOOLEAN     NOT NULL DEFAULT FALSE,
    include_in_reporting BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by           VARCHAR(200)
);

-- Exactly ONE active version per (project, statement, fiscal_year); scenario is NOT
-- in the key.  COALESCE folds the NULL/global project scope into a single bucket.
CREATE UNIQUE INDEX IF NOT EXISTS uq_plan_version_active
    ON dim_plan_version (COALESCE(project_id, -1), statement, fiscal_year)
    WHERE is_active;

-- A label is unique within a (project, statement, fiscal_year) scope.
CREATE UNIQUE INDEX IF NOT EXISTS uq_plan_version_label
    ON dim_plan_version (COALESCE(project_id, -1), statement, fiscal_year, label);
"""


def upgrade() -> None:
    op.execute(_CREATE_VERSION_TABLE)

    # Nullable FK links (additive — existing rows stay valid with NULL).
    op.execute(
        "ALTER TABLE fact_position_plan "
        "ADD COLUMN IF NOT EXISTS plan_version_id INTEGER "
        "REFERENCES dim_plan_version(plan_version_id);"
    )
    op.execute(
        "ALTER TABLE fact_gl_plan "
        "ADD COLUMN IF NOT EXISTS plan_version_id INTEGER "
        "REFERENCES dim_plan_version(plan_version_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_position_plan_version "
        "ON fact_position_plan (plan_version_id);"
    )

    # Backfill: a default active+included 'v1' per (statement, fiscal_year) that has
    # position-plan rows (project_id NULL via column omission → default NULL).
    op.execute(
        """
        INSERT INTO dim_plan_version (statement, fiscal_year, label,
                                      is_active, include_in_reporting)
        SELECT DISTINCT fp.statement, fp.fiscal_year, 'v1', TRUE, TRUE
        FROM fact_position_plan fp
        WHERE NOT EXISTS (
            SELECT 1 FROM dim_plan_version v
            WHERE v.project_id IS NULL
              AND v.statement = fp.statement
              AND v.fiscal_year = fp.fiscal_year
        );
        """
    )

    # Link every existing position-plan row to its (statement, fiscal_year) v1.
    op.execute(
        """
        UPDATE fact_position_plan fp
        SET plan_version_id = v.plan_version_id
        FROM dim_plan_version v
        WHERE v.project_id IS NULL
          AND v.label = 'v1'
          AND v.statement = fp.statement
          AND v.fiscal_year = fp.fiscal_year
          AND fp.plan_version_id IS NULL;
        """
    )

    op.execute(
        "COMMENT ON TABLE dim_plan_version IS "
        "'Versioned plans (Phase 4). Exactly one is_active per "
        "(project_id, statement, fiscal_year); include_in_reporting gates the "
        "reporting forecast/coverage columns. Read by plan_version.py.';"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_position_plan_version;")
    op.execute("ALTER TABLE fact_position_plan DROP COLUMN IF EXISTS plan_version_id;")
    op.execute("ALTER TABLE fact_gl_plan DROP COLUMN IF EXISTS plan_version_id;")
    op.execute("DROP INDEX IF EXISTS uq_plan_version_active;")
    op.execute("DROP INDEX IF EXISTS uq_plan_version_label;")
    op.execute("DROP TABLE IF EXISTS dim_plan_version;")
