"""SQLAlchemy engine + session factory."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
    connect_args={
        "connect_timeout": 5,
        # Global reaper: a connection left idle *inside* a transaction past this
        # bound is closed by the server.  Never interrupts an actively running
        # query — only txns stalled between statements (e.g. a crashed client).
        "options": f"-c idle_in_transaction_session_timeout={settings.idle_in_transaction_timeout_ms}",
    },
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_read_session() -> Iterator[Session]:
    """Session for heavy report READS. Bounds each request's SQL with a scoped
    statement_timeout and forces a serial plan (parallel-gather deadlock guard).
    set_config(..., is_local=true) == SET LOCAL — scoped to this request's txn,
    never leaks to other pooled checkouts."""
    db = SessionLocal()
    try:
        db.execute(
            text("SELECT set_config('statement_timeout', :ms, true)"),
            {"ms": str(settings.read_statement_timeout_ms)},
        )
        db.execute(text("SELECT set_config('max_parallel_workers_per_gather', '0', true)"))
        yield db
    finally:
        db.close()
