"""initial minimal GDPdU schema (GL + derived facts + partner/geo + plan + auth)

Realizes docs/db/gl-target-structure.md v0.7 (minimal set per docs/PLAN.md §3).
Raw-SQL migration: single source of truth for the schema.

Revision ID: 0001
Revises:
Create Date: 2026-06-11
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


SCHEMA_SQL = r"""
-- ============================================================ Entities
CREATE TABLE dim_legal_entity (
    legal_entity_code  VARCHAR(20) PRIMARY KEY,
    entity_prefix      CHAR(2) NOT NULL UNIQUE,
    entity_name        VARCHAR(200) NOT NULL,
    is_consolidation   BOOLEAN NOT NULL DEFAULT FALSE,
    country_code       CHAR(3),
    default_currency   CHAR(5) DEFAULT 'EUR',
    source_system      VARCHAR(40)
);

-- ============================================================ Geography (snowflake)
CREATE TABLE dim_country (
    country_code   CHAR(3) PRIMARY KEY,
    iso2           CHAR(2),
    name_de        VARCHAR(120),
    name_en        VARCHAR(120),
    continent      VARCHAR(40),
    economic_area  VARCHAR(40)
);
CREATE TABLE dim_region (
    country_code   CHAR(3) NOT NULL REFERENCES dim_country (country_code),
    region_code    VARCHAR(20) NOT NULL,
    name_de        VARCHAR(120),
    name_en        VARCHAR(120),
    region_type    VARCHAR(40),
    PRIMARY KEY (country_code, region_code)
);

-- ============================================================ GL account mapping (per entity+year)
CREATE TABLE dim_gl_account (
    account_number_group VARCHAR(8)  NOT NULL,
    fiscal_year          SMALLINT    NOT NULL,
    gl_account_id        VARCHAR(32) NOT NULL,
    account_name         VARCHAR(500),
    level_0              VARCHAR(200),               -- carries BS/PL (replaces statement_type)
    level_1              VARCHAR(200),
    level_2              VARCHAR(200),
    level_3              VARCHAR(200),
    level_4              VARCHAR(500),
    l4_sub               VARCHAR(200),               -- former l9
    level_2_sort         INTEGER,
    level_3_sort         INTEGER,
    is_ic                BOOLEAN NOT NULL DEFAULT FALSE,  -- former l10_ic
    source_system        VARCHAR(40),
    entity_prefix        CHAR(2) GENERATED ALWAYS AS (LEFT(account_number_group, 2)) STORED,
    PRIMARY KEY (account_number_group, fiscal_year)
);
CREATE INDEX idx_gl_account_glid ON dim_gl_account (gl_account_id);
CREATE INDEX idx_gl_account_l0   ON dim_gl_account (level_0);

CREATE TABLE dim_gl_na (
    account_number_group VARCHAR(8) NOT NULL,
    fiscal_year          SMALLINT   NOT NULL,
    l6_na_mapping        VARCHAR(120),
    l7_na_description    VARCHAR(500),
    PRIMARY KEY (account_number_group, fiscal_year),
    FOREIGN KEY (account_number_group, fiscal_year)
        REFERENCES dim_gl_account (account_number_group, fiscal_year)
);
CREATE TABLE dim_gl_cf (
    account_number_group VARCHAR(8) NOT NULL,
    fiscal_year          SMALLINT   NOT NULL,
    l1 VARCHAR(200), l2 VARCHAR(200), l3 VARCHAR(200), l4 VARCHAR(200), l5 VARCHAR(200),
    cf_mapping VARCHAR(200),
    PRIMARY KEY (account_number_group, fiscal_year),
    FOREIGN KEY (account_number_group, fiscal_year)
        REFERENCES dim_gl_account (account_number_group, fiscal_year)
);

CREATE TABLE dim_document_type (
    document_type_code VARCHAR(20) PRIMARY KEY,
    document_type_text VARCHAR(80)
);

-- P&L presentation structure (template; gl_account_id is a plain ref, not FK — not unique anymore)
CREATE TABLE dim_pl_structure (
    pl_line_id    SERIAL PRIMARY KEY,
    sort_order    INTEGER NOT NULL UNIQUE,
    line_code     VARCHAR(64) NOT NULL UNIQUE,
    row_type      VARCHAR(16) NOT NULL DEFAULT 'mapping',
    balance_title VARCHAR(500) NOT NULL DEFAULT '',
    details       INTEGER,
    calc_type     INTEGER DEFAULT 1,
    level_2       VARCHAR(200),
    level_3       VARCHAR(200),
    level_4       VARCHAR(500),
    gl_account_id VARCHAR(32),
    invert_delta  BOOLEAN NOT NULL DEFAULT FALSE,
    is_bold       BOOLEAN NOT NULL DEFAULT FALSE,
    kpi_code      VARCHAR(64)
);

-- ============================================================ Partners (entity-aware; geo as soft ref)
CREATE TABLE dim_customer (
    customer_id      VARCHAR(32) PRIMARY KEY,        -- = entity_prefix(2) || debtor_number
    debtor_number    VARCHAR(30),
    name_line_1      VARCHAR(200),
    name_line_2      VARCHAR(200),
    country_code     CHAR(3),
    region_code      VARCHAR(20),
    city             VARCHAR(120),
    postal_code      VARCHAR(20),
    default_currency CHAR(5),
    source_system    VARCHAR(40),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    entity_prefix    CHAR(2) GENERATED ALWAYS AS (LEFT(customer_id, 2)) STORED
);
CREATE INDEX idx_customer_geo ON dim_customer (country_code, region_code);

CREATE TABLE dim_supplier (
    supplier_id      VARCHAR(32) PRIMARY KEY,        -- = entity_prefix(2) || creditor_number
    creditor_number  VARCHAR(30),
    name_line_1      VARCHAR(200),
    name_line_2      VARCHAR(200),
    country_code     CHAR(3),
    region_code      VARCHAR(20),
    city             VARCHAR(120),
    postal_code      VARCHAR(20),
    default_currency CHAR(5),
    purchasing_org   VARCHAR(20),
    source_system    VARCHAR(40),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    entity_prefix    CHAR(2) GENERATED ALWAYS AS (LEFT(supplier_id, 2)) STORED
);
CREATE INDEX idx_supplier_geo ON dim_supplier (country_code, region_code);

-- ============================================================ GL facts
CREATE TABLE fact_gl_entry (
    journal_entry_group_number VARCHAR(12) NOT NULL,   -- entity_prefix(2) || journal_entry_number
    fiscal_year   SMALLINT NOT NULL,
    fiscal_period SMALLINT NOT NULL,                   -- 1-12, 13 = consolidation
    entry_type    VARCHAR(16) NOT NULL DEFAULT 'actual',  -- actual|consolidation|adjustment
    posting_date  DATE NOT NULL,
    document_date DATE,
    document_type_code VARCHAR(20),
    reference_document_number VARCHAR(80),
    currency_code CHAR(5) NOT NULL DEFAULT 'EUR',
    header_note   VARCHAR(500),
    source_system VARCHAR(40) NOT NULL DEFAULT 'unknown',
    entity_prefix CHAR(2) GENERATED ALWAYS AS (LEFT(journal_entry_group_number, 2)) STORED,
    PRIMARY KEY (journal_entry_group_number, fiscal_year)
);

CREATE TABLE fact_gl_line (
    journal_entry_group_number VARCHAR(12) NOT NULL,
    fiscal_year   SMALLINT NOT NULL,
    line_number   INTEGER  NOT NULL,
    booking_line_id BIGINT NOT NULL UNIQUE,
    account_number_group VARCHAR(8) NOT NULL,
    amount        NUMERIC(18,6) NOT NULL,             -- signed: + = debit, - = credit
    vat_amount    NUMERIC(18,6),
    line_note     VARCHAR(500),
    customer_id   VARCHAR(32),                        -- soft ref (load order); resolved via join
    supplier_id   VARCHAR(32),
    posting_type  VARCHAR(80),
    source_system VARCHAR(40) NOT NULL DEFAULT 'unknown',
    entity_prefix CHAR(2) GENERATED ALWAYS AS (LEFT(account_number_group, 2)) STORED,
    PRIMARY KEY (journal_entry_group_number, fiscal_year, line_number),
    FOREIGN KEY (journal_entry_group_number, fiscal_year)
        REFERENCES fact_gl_entry (journal_entry_group_number, fiscal_year),
    FOREIGN KEY (account_number_group, fiscal_year)
        REFERENCES dim_gl_account (account_number_group, fiscal_year)
);
CREATE INDEX idx_gl_line_ang  ON fact_gl_line (account_number_group, fiscal_year);
CREATE INDEX idx_gl_line_cust ON fact_gl_line (customer_id);
CREATE INDEX idx_gl_line_supp ON fact_gl_line (supplier_id);

-- ============================================================ Derived facts (from GL)
CREATE TABLE fact_ar (
    booking_line_id BIGINT PRIMARY KEY REFERENCES fact_gl_line (booking_line_id),
    journal_entry_group_number VARCHAR(12) NOT NULL,
    fiscal_year SMALLINT NOT NULL,
    line_number INTEGER NOT NULL,
    account_number_group VARCHAR(8) NOT NULL,
    customer_id VARCHAR(32),
    posting_date DATE, document_date DATE, due_date DATE,
    amount NUMERIC(18,6) NOT NULL,
    reference_document_number VARCHAR(80),
    entry_type VARCHAR(16),
    link_method VARCHAR(16),                          -- txn | gegenkonto | none
    source_system VARCHAR(40)
);
CREATE TABLE fact_ap (
    booking_line_id BIGINT PRIMARY KEY REFERENCES fact_gl_line (booking_line_id),
    journal_entry_group_number VARCHAR(12) NOT NULL,
    fiscal_year SMALLINT NOT NULL,
    line_number INTEGER NOT NULL,
    account_number_group VARCHAR(8) NOT NULL,
    supplier_id VARCHAR(32),
    posting_date DATE, document_date DATE, due_date DATE,
    amount NUMERIC(18,6) NOT NULL,
    reference_document_number VARCHAR(80),
    entry_type VARCHAR(16),
    link_method VARCHAR(16),
    source_system VARCHAR(40)
);
CREATE TABLE fact_sales (
    booking_line_id BIGINT PRIMARY KEY REFERENCES fact_gl_line (booking_line_id),
    journal_entry_group_number VARCHAR(12) NOT NULL,
    fiscal_year SMALLINT NOT NULL,
    account_number_group VARCHAR(8) NOT NULL,
    customer_id VARCHAR(32),
    posting_date DATE,
    gross_sales NUMERIC(18,6) NOT NULL,               -- = -amount (revenue is credit)
    link_method VARCHAR(16),
    entry_type VARCHAR(16),
    source_system VARCHAR(40)
);
CREATE TABLE fact_com (
    booking_line_id BIGINT PRIMARY KEY REFERENCES fact_gl_line (booking_line_id),
    journal_entry_group_number VARCHAR(12) NOT NULL,
    fiscal_year SMALLINT NOT NULL,
    account_number_group VARCHAR(8) NOT NULL,
    supplier_id VARCHAR(32),
    posting_date DATE,
    cost_of_materials NUMERIC(18,6) NOT NULL,         -- = amount (material is debit)
    link_method VARCHAR(16),
    entry_type VARCHAR(16),
    source_system VARCHAR(40)
);

-- ============================================================ Plan / Forecast
CREATE TABLE fact_gl_plan (
    account_number_group VARCHAR(8) NOT NULL,
    fiscal_year   SMALLINT NOT NULL,
    fiscal_period SMALLINT NOT NULL,
    scenario      VARCHAR(12) NOT NULL,               -- forecast | plan
    amount        NUMERIC(18,6) NOT NULL,             -- movement (signed)
    is_synthetic  BOOLEAN NOT NULL DEFAULT TRUE,
    source_system VARCHAR(40),
    PRIMARY KEY (account_number_group, fiscal_year, fiscal_period, scenario)
);
CREATE TABLE fact_sales_plan (
    customer_id   VARCHAR(32) NOT NULL,
    fiscal_year   SMALLINT NOT NULL,
    fiscal_period SMALLINT NOT NULL,
    scenario      VARCHAR(12) NOT NULL,
    gross_sales_plan NUMERIC(18,6) NOT NULL,
    is_synthetic  BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (customer_id, fiscal_year, fiscal_period, scenario)
);

-- ============================================================ Auth / Roles / Ops
CREATE TABLE dim_user (
    user_id       SERIAL PRIMARY KEY,
    email         VARCHAR(200) NOT NULL UNIQUE,
    display_name  VARCHAR(200),
    password_hash VARCHAR(255) NOT NULL,
    is_admin      BOOLEAN NOT NULL DEFAULT FALSE,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE dim_role (
    role_id     SERIAL PRIMARY KEY,
    role_name   VARCHAR(80) NOT NULL UNIQUE,
    description VARCHAR(500)
);
CREATE TABLE user_role (
    user_id INTEGER NOT NULL REFERENCES dim_user (user_id) ON DELETE CASCADE,
    role_id INTEGER NOT NULL REFERENCES dim_role (role_id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id)
);
CREATE TABLE role_entity_visibility (
    role_id           INTEGER NOT NULL REFERENCES dim_role (role_id) ON DELETE CASCADE,
    legal_entity_code VARCHAR(20) NOT NULL REFERENCES dim_legal_entity (legal_entity_code) ON DELETE CASCADE,
    PRIMARY KEY (role_id, legal_entity_code)
);
CREATE TABLE auth_session (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    INTEGER NOT NULL REFERENCES dim_user (user_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE meta_dataset_load (
    load_id           SERIAL PRIMARY KEY,
    dataset           VARCHAR(40) NOT NULL,           -- gl | sales | mapping | ...
    legal_entity_code VARCHAR(20),
    fiscal_year       SMALLINT,
    fiscal_period     SMALLINT,
    row_count         INTEGER,
    content_hash      VARCHAR(64),                    -- dedup key
    loaded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    loaded_by         VARCHAR(200)
);

-- ============================================================ Enriched read view
CREATE OR REPLACE VIEW v_gl_line_enriched AS
SELECT
    l.booking_line_id,
    l.journal_entry_group_number,
    LEFT(l.account_number_group, 2) AS entity_prefix,
    le.legal_entity_code, le.entity_name,
    l.fiscal_year, l.line_number,
    e.fiscal_period, e.entry_type,
    e.posting_date, e.document_date,
    e.document_type_code, dt.document_type_text,
    e.reference_document_number,
    l.account_number_group, a.gl_account_id, a.account_name,
    a.level_0, a.level_1, a.level_2, a.level_3, a.level_4, a.l4_sub,
    a.level_2_sort, a.level_3_sort, a.is_ic,
    na.l6_na_mapping, na.l7_na_description,
    cf.l1 AS cf_l1, cf.l2 AS cf_l2, cf.l3 AS cf_l3, cf.l4 AS cf_l4, cf.l5 AS cf_l5, cf.cf_mapping,
    l.amount, l.vat_amount, l.posting_type,
    l.customer_id, l.supplier_id
FROM fact_gl_line l
JOIN fact_gl_entry e
  ON e.journal_entry_group_number = l.journal_entry_group_number
 AND e.fiscal_year = l.fiscal_year
JOIN dim_gl_account a
  ON a.account_number_group = l.account_number_group
 AND a.fiscal_year = l.fiscal_year
LEFT JOIN dim_gl_na na ON na.account_number_group = l.account_number_group AND na.fiscal_year = l.fiscal_year
LEFT JOIN dim_gl_cf cf ON cf.account_number_group = l.account_number_group AND cf.fiscal_year = l.fiscal_year
LEFT JOIN dim_legal_entity le ON le.entity_prefix = LEFT(l.account_number_group, 2)
LEFT JOIN dim_document_type dt ON dt.document_type_code = e.document_type_code;
"""

DROP_SQL = r"""
DROP VIEW IF EXISTS v_gl_line_enriched;
DROP TABLE IF EXISTS meta_dataset_load;
DROP TABLE IF EXISTS auth_session;
DROP TABLE IF EXISTS role_entity_visibility;
DROP TABLE IF EXISTS user_role;
DROP TABLE IF EXISTS dim_role;
DROP TABLE IF EXISTS dim_user;
DROP TABLE IF EXISTS fact_sales_plan;
DROP TABLE IF EXISTS fact_gl_plan;
DROP TABLE IF EXISTS fact_com;
DROP TABLE IF EXISTS fact_sales;
DROP TABLE IF EXISTS fact_ap;
DROP TABLE IF EXISTS fact_ar;
DROP TABLE IF EXISTS fact_gl_line;
DROP TABLE IF EXISTS fact_gl_entry;
DROP TABLE IF EXISTS dim_pl_structure;
DROP TABLE IF EXISTS dim_supplier;
DROP TABLE IF EXISTS dim_customer;
DROP TABLE IF EXISTS dim_document_type;
DROP TABLE IF EXISTS dim_gl_cf;
DROP TABLE IF EXISTS dim_gl_na;
DROP TABLE IF EXISTS dim_gl_account;
DROP TABLE IF EXISTS dim_region;
DROP TABLE IF EXISTS dim_country;
DROP TABLE IF EXISTS dim_legal_entity;
"""


def upgrade() -> None:
    op.execute(SCHEMA_SQL)


def downgrade() -> None:
    op.execute(DROP_SQL)
