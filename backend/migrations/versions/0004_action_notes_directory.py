"""Action Notes workspace + internal contact directory.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-13
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_UP = """
CREATE TABLE IF NOT EXISTS app_note_session (
    session_id    TEXT PRIMARY KEY,
    author_scope  TEXT NOT NULL,
    title         TEXT NOT NULL,
    route         TEXT,
    filters_json  JSONB NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'archived')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS app_note (
    note_id       TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES app_note_session(session_id) ON DELETE CASCADE,
    body          TEXT NOT NULL DEFAULT '',
    is_done       BOOLEAN NOT NULL DEFAULT FALSE,
    sort_order    INT NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_app_note_session ON app_note(session_id, sort_order);

CREATE TABLE IF NOT EXISTS app_note_pin (
    pin_id          TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES app_note_session(session_id) ON DELETE CASCADE,
    label           TEXT,
    snapshot_json   JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_app_note_pin_session ON app_note_pin(session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS app_email_draft (
    draft_id      TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES app_note_session(session_id) ON DELETE CASCADE,
    contact_id    TEXT,
    subject       TEXT NOT NULL,
    body_text     TEXT NOT NULL,
    body_html     TEXT,
    status        TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'sent')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS app_action_board (
    board_id      TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES app_note_session(session_id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    board_json    JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dim_org_role (
    role_id           TEXT PRIMARY KEY,
    role_key          TEXT NOT NULL UNIQUE,
    display_name_de   TEXT NOT NULL,
    display_name_en   TEXT,
    department_area   TEXT NOT NULL,
    seniority_level   TEXT NOT NULL CHECK (seniority_level IN ('executive', 'manager', 'team_lead', 'individual')),
    email_tone_hint   TEXT NOT NULL,
    sort_order        INT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS dim_internal_contact (
    contact_id      TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    email           TEXT NOT NULL,
    department      TEXT NOT NULL,
    role_title      TEXT NOT NULL,
    role_id         TEXT REFERENCES dim_org_role(role_id),
    salutation_de   TEXT NOT NULL,
    salutation_en   TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order      INT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_internal_contact_dept ON dim_internal_contact(department, sort_order);

INSERT INTO dim_org_role (role_id, role_key, display_name_de, display_name_en, department_area, seniority_level, email_tone_hint, sort_order)
VALUES
  ('role_cfo', 'cfo', 'CFO', 'CFO', 'Finance', 'executive', 'formal, concise, numbers-first', 1),
  ('role_fpa', 'fpna', 'FP&A Lead', 'FP&A Lead', 'Finance', 'manager', 'analytical, action-oriented', 2)
ON CONFLICT (role_id) DO NOTHING;

INSERT INTO dim_internal_contact (contact_id, display_name, email, department, role_title, role_id, salutation_de, salutation_en, sort_order)
VALUES
  ('ct_cfo', 'Alex Morgan', 'alex.morgan@example.com', 'Finance', 'Chief Financial Officer', 'role_cfo', 'Sehr geehrte/r', 'Dear Alex,', 1),
  ('ct_fpa', 'Jordan Lee', 'jordan.lee@example.com', 'Finance', 'Head of FP&A', 'role_fpa', 'Sehr geehrte/r', 'Dear Jordan,', 2),
  ('ct_ops', 'Sam Rivera', 'sam.rivera@example.com', 'Operations', 'Operations Manager', NULL, 'Sehr geehrte/r', 'Dear Sam,', 3)
ON CONFLICT (contact_id) DO NOTHING;
"""

_DOWN = """
DROP TABLE IF EXISTS app_action_board;
DROP TABLE IF EXISTS app_email_draft;
DROP TABLE IF EXISTS app_note_pin;
DROP TABLE IF EXISTS app_note;
DROP TABLE IF EXISTS app_note_session;
DROP TABLE IF EXISTS dim_internal_contact;
DROP TABLE IF EXISTS dim_org_role;
"""


def upgrade() -> None:
    op.execute(_UP)


def downgrade() -> None:
    op.execute(_DOWN)
