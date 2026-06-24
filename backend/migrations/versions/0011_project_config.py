"""Per-project identity + config persistence (reporting-v2 Phase 7).

Adds two additive tables so the no-code project-setup wizard can persist a
project's setup once and reuse it on every (incremental) update:

  dim_project       Project identity + light metadata (name, FY start month,
                    created/updated timestamps).  ``project_id`` is the same
                    plain VARCHAR key already used by ``dim_project_coa_override``
                    (Phase 4), with ``'default'`` as the single seeded project.

  project_config    One JSONB blob per project carrying the wizard answers that
                    drive the deterministic rebuild (entities, opening-balance
                    mode, net-profit source, mapping/partner sources, labels).
                    Kept as JSONB (not columns) so the wizard can evolve its
                    fields without a schema migration each time.

ADDITIVE / NO-OP GUARANTEE
──────────────────────────
Both tables are new and read only by the new ``/api/v1/projects`` router + the
rebuild flag-resolution helper.  The seeded ``'default'`` config mirrors the
current LEGACY flags (opening_balance_mode='in_data', net_profit_source=
'report_inject'), so resolving flags from config yields the same values the
``settings.*`` defaults produce today.  No existing endpoint output changes —
the golden live-vs-v2 equivalence is preserved.
"""
from __future__ import annotations

from alembic import op

revision = "0011_project_config"
down_revision = "0010_fact_anomaly"
branch_labels = None
depends_on = None

# Default config mirrors the current LEGACY rebuild flags so flag-resolution from
# the 'default' project equals the settings.* defaults (golden equivalence).
_DEFAULT_CONFIG_JSON = """{
  "entities": [],
  "fy_start_month": 1,
  "opening_balance_mode": "in_data",
  "net_profit_source": "report_inject",
  "mapping_source": "library",
  "partner_master_source": "files",
  "sales_label": "Sales",
  "cost_label": "Cost of materials"
}"""

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS dim_project (
    project_id      VARCHAR(64)  PRIMARY KEY,
    name            TEXT,
    fy_start_month  SMALLINT     NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS project_config (
    project_id  VARCHAR(64)  PRIMARY KEY
                REFERENCES dim_project (project_id) ON DELETE CASCADE,
    config      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
"""

_SEED_SQL = """
INSERT INTO dim_project (project_id, name, fy_start_month)
VALUES ('default', 'Default project', 1)
ON CONFLICT (project_id) DO NOTHING;

INSERT INTO project_config (project_id, config)
VALUES ('default', '{config}'::jsonb)
ON CONFLICT (project_id) DO NOTHING;
""".replace("{config}", _DEFAULT_CONFIG_JSON.replace("'", "''"))


def upgrade() -> None:
    op.execute(_CREATE_SQL)
    op.execute(_SEED_SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS project_config;")
    op.execute("DROP TABLE IF EXISTS dim_project;")
