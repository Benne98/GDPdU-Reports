"""Anomaly cache table (reporting-v2 Phase 6).

Stores deterministic anomalies detected over the compat statement layer
(``backend/app/services/anomaly.py``).  Request-time compute is the SOURCE OF
TRUTH; this table is an OPTIONAL cache the endpoint may upsert into so the
frontend / narrative layer can read pre-computed anomalies without rebuilding the
statements.

Additive guarantee
------------------
The table is created empty and is never read by any existing endpoint, so the
golden live-vs-v2 equivalence is preserved (no existing payload changes).

Columns mirror the ``Anomaly`` dataclass:
  statement   'pl' | 'bs' | 'wc' | 'cf'
  kind        'mom_swing' | 'yoy_swing' | 'sign_flip' | 'balance_break'
              | 'gl_concentration'
  severity    'high' | 'medium' | 'low'
  period_grain 'year' | 'month' | 'week'
"""
from __future__ import annotations

from alembic import op

revision = "0010_fact_anomaly"
down_revision = "0009_project_coa_override"
branch_labels = None
depends_on = None

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS fact_anomaly (
    id            BIGSERIAL PRIMARY KEY,
    statement     TEXT          NOT NULL,
    line_code     TEXT          NOT NULL,
    label         TEXT,
    kind          TEXT          NOT NULL,
    severity      TEXT          NOT NULL,
    period_grain  TEXT          NOT NULL,
    year          INTEGER,
    month         INTEGER,
    iso_year      INTEGER,
    iso_week      INTEGER,
    entity        TEXT,
    value         DOUBLE PRECISION,
    delta         DOUBLE PRECISION,
    magnitude_eur DOUBLE PRECISION,
    description   TEXT,
    detected_at   TIMESTAMP     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fact_anomaly_scope
    ON fact_anomaly (statement, period_grain, year, month, entity);

CREATE INDEX IF NOT EXISTS idx_fact_anomaly_week
    ON fact_anomaly (statement, period_grain, iso_year, iso_week, entity);
"""


def upgrade() -> None:
    op.execute(_CREATE_SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS fact_anomaly;")
