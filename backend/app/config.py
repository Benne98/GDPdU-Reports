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
    # GL / GoBD reference files can exceed 50 MB in local dev (Decidra export ~190 MB).
    max_upload_mb: int = Field(default=256, validation_alias="MAX_UPLOAD_MB")

    # Environment indicator — default "dev" so existing tests run unchanged.
    app_env: str = "dev"

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
