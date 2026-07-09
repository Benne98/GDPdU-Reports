"""Central configuration. Defaults let the app run locally without a .env.

DB connection follows the same DB_* env convention as the legacy backend, so it
reuses the existing local PostgreSQL (EDB) on port 5432 with a SEPARATE database
"Finssentials". (No Docker on this machine — parallel operation is kept via the
separate database + app ports 8001/5174.)
"""
from __future__ import annotations

import os

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_database_url() -> str:
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "")
    name = os.getenv("DB_NAME", "Finssentials")
    auth = f"{user}:{password}" if password else user
    return f"postgresql+psycopg2://{auth}@{host}:{port}/{name}"


_DEFAULT_SECRET = "change-me-dev-only"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    db_host: str = Field(default="localhost", validation_alias="DB_HOST")
    db_port: str = Field(default="5432", validation_alias="DB_PORT")
    db_user: str = Field(default="postgres", validation_alias="DB_USER")
    db_password: str = Field(default="", validation_alias="DB_PASSWORD")
    db_name: str = Field(default="Finssentials", validation_alias="DB_NAME")

    # ── Read-report DB safety timeouts (scoped per-request; see db.get_read_session) ──
    # Per-statement ceiling for heavy report READS (SET LOCAL statement_timeout).
    read_statement_timeout_ms: int = Field(default=90000, validation_alias="READ_STATEMENT_TIMEOUT_MS")
    # Global idle-in-transaction reaper ceiling (engine connect_args, see db.py).
    idle_in_transaction_timeout_ms: int = Field(default=120000, validation_alias="IDLE_IN_TXN_TIMEOUT_MS")

    # Full override wins; otherwise assembled from DB_* fields above.
    database_url: str = ""

    # Auth
    auth_secret: str = _DEFAULT_SECRET
    auth_token_ttl_minutes: int = 480

    # Seeding (P0)
    seed_admin_email: str = "benedikt.hoffarth@finssentials.com"
    seed_default_password: str = "changeme123"

    # API
    api_port: int = 8001
    # GL / GoBD exports for a single (entity, year) can exceed 1,000,000 rows and run
    # to several hundred MB; keep a generous default (overridable via MAX_UPLOAD_MB).
    max_upload_mb: int = Field(default=1024, validation_alias="MAX_UPLOAD_MB")

    # Environment indicator — default "dev" so existing tests run unchanged.
    app_env: str = "dev"

    # ── Reporting-v2 pipeline flags (defaults = legacy/5176 behavior) ──────────
    # Where the balance-sheet net profit comes from:
    #   "report_inject" = virtual report-layer injection (legacy, fin_compat_bs.py)
    #   "gl_rows"       = real synthetic net-profit GL bookings (reporting-v2)
    bs_net_profit_source: str = Field(default="report_inject", validation_alias="BS_NET_PROFIT_SOURCE")
    # How the balance-sheet current-year-result (the equity "Net profit" row) is
    # derived in the compat reader (fin_compat_bs.py) for the ENTITY-SCOPED ER
    # snapshot and the CONSOLIDATION views:
    #   "balancing_plug" (legacy DEFAULT) = Σ raw BS balances = Total assets −
    #        Total equity & liabilities.  Forces Assets = E&L per entity column, so
    #        the displayed imbalance is ALWAYS 0 — a data error is silently absorbed.
    #   "pl_sum" = the income-statement result Σ(PL amount × −1) (level_0='PL').
    #        Identical source as the main month/week statement; the residual
    #        (Total assets − Total E&L incl. this result) is then SURFACED via
    #        ``balance_check`` so a non-balancing dataset is visible, never hidden.
    # Default keeps legacy/golden behaviour; the v5/e2e project should set "pl_sum".
    # The main statement + monthly BS views ALWAYS use Σ P&L (never a plug) and are
    # unaffected by this flag.
    bs_current_year_result_mode: str = Field(
        default="balancing_plug", validation_alias="BS_CURRENT_YEAR_RESULT_MODE"
    )
    # Opening-balance acquisition: "in_data" | "file" | "carry_forward"
    opening_balance_mode: str = Field(default="in_data", validation_alias="OPENING_BALANCE_MODE")
    # Retained-earnings roll (year-end close) — global DEFAULT for the OPTIONAL
    # rebuild stage that rolls each completed FY's P&L result into the entity's
    # retained-earnings (Gewinnvortrag) equity account as an opening balance.  OFF
    # by default so a dataset that already carries the close booking is byte-
    # identical (golden parity); a per-project config (retained_earnings_roll) may
    # enable it and supply per-entity target accounts + a pre-first-year opening.
    retained_earnings_roll_enabled: bool = Field(
        default=False, validation_alias="RETAINED_EARNINGS_ROLL"
    )
    # Run the full deterministic rebuild on every ingest commit (reporting-v2).
    rebuild_on_commit: bool = Field(default=False, validation_alias="REBUILD_ON_COMMIT")
    # Allow the admin "reset all ingested data" capability (POST /projects/{id}/reset-data).
    # Default False so the LIVE 5176 stack — which the same frontend can target — can
    # NEVER reset data even if the endpoint is hit.  A second, independent guard in the
    # endpoint HARD-refuses whenever the connected DB is the live "Finssentials" DB,
    # regardless of this flag (belt-and-suspenders).
    allow_data_reset: bool = Field(default=False, validation_alias="ALLOW_DATA_RESET")
    # Journal Agent (Phase 6): when True, the GL findings (outliers / seasonality /
    # forensic counter-accounts + Other + suspicious texts) are merged into the
    # existing narrative bullets.  Default False = legacy/golden-identical output.
    journal_agent_narrative: bool = Field(default=False, validation_alias="JOURNAL_AGENT_NARRATIVE")
    # Forensic benignity filter (anomaly refinement): when True AND ANTHROPIC_API_KEY is
    # set, an optional LLM pass further narrows the flagged unexpected-counter rows after
    # the deterministic heuristic.  Default False = heuristic-only, no API calls (tests
    # never hit Anthropic).  Fail-closed: any LLM error keeps the row.
    forensic_use_llm: bool = Field(default=False, validation_alias="FORENSIC_USE_LLM")

    # ── Overview v2 batched summary (reporting-v2) ─────────────────────────────
    # OFF by default: the /financials/overview/summary endpoint always uses the
    # live builders (correctness source of truth).  When True AND the mart is fresh
    # (mart_overview.mart_is_fresh) the EBIT/revenue hero is served from the
    # pre-aggregated mart_overview_period snapshot.  The parent MUST apply migration
    # 0026 and validate the mart on real data before turning this on.
    overview_summary_use_mart: bool = Field(
        default=False, validation_alias="OVERVIEW_SUMMARY_USE_MART"
    )
    # Short in-process TTL (seconds) for the batched summary cache (visibility-aware
    # key). 0 disables caching (tests set 0 for determinism).
    overview_summary_cache_ttl_s: int = Field(
        default=45, validation_alias="OVERVIEW_SUMMARY_CACHE_TTL_S"
    )
    # Short in-process TTL (seconds) for the OPOS aging _partner_view cache
    # (visibility-aware key). A single Sales-aging page load fires ~8 aging
    # endpoints that each recompute the SAME Stichtag _partner_view; this cache
    # collapses those repeated full-FY OPOS scans to one. 0 disables caching
    # (tests set 0 for determinism). Same default as the overview summary cache.
    aging_cache_ttl_s: int = Field(
        default=45, validation_alias="AGING_CACHE_TTL_S"
    )

    # ── Dataset viewer (server-paginated "full dataset" preview) ───────────────
    # Hard memory ceiling for the in-memory projected concat frame built by
    # POST /api/v1/ingest/dataset/rows.  Above this the request is refused (413)
    # rather than buffering an unbounded multi-entity frame.  Default 1.5 GB.
    dataset_viewer_max_bytes: int = Field(
        default=1_500_000_000, validation_alias="DATASET_VIEWER_MAX_BYTES"
    )
    # Upper bound on the page size the dataset viewer will serve; the request
    # ``limit`` is clamped to this value.  Default 1000 rows.
    dataset_viewer_max_page: int = Field(
        default=1000, validation_alias="DATASET_VIEWER_MAX_PAGE"
    )

    @model_validator(mode="after")
    def _assemble_database_url(self) -> "Settings":
        """Build database_url from DB_* after pydantic has loaded backend/.env."""
        explicit = os.getenv("DATABASE_URL")
        if explicit:
            self.database_url = explicit
            return self
        auth = f"{self.db_user}:{self.db_password}" if self.db_password else self.db_user
        self.database_url = (
            f"postgresql+psycopg2://{auth}@{self.db_host}:{self.db_port}/{self.db_name}"
        )
        return self

    @model_validator(mode="after")
    def _guard_default_secret(self) -> "Settings":
        """Prevent startup outside dev with the default AUTH_SECRET."""
        if self.app_env != "dev" and self.auth_secret == _DEFAULT_SECRET:
            raise RuntimeError(
                "AUTH_SECRET must be overridden outside dev "
                "(set AUTH_SECRET env var or .env before starting)"
            )
        return self


settings = Settings()
