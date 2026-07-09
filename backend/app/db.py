"""SQLAlchemy engine + session factory."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

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


def apply_read_guard(db: Session) -> None:
    """Bound heavy report READS on ``db`` and force a serial plan.

    Two per-transaction (``SET LOCAL``) guards, applied together as the single
    source of truth for "this is a heavy report read":
      * ``statement_timeout`` = ``read_statement_timeout_ms`` — a runaway BS/recv/
        bal_mov scan can NEVER run unbounded (it self-cancels at the ceiling).
      * ``max_parallel_workers_per_gather = 0`` — forces a serial plan, the
        parallel-gather deadlock guard (leaked bal_mov queries piled up on
        IPC/MessageQueueSend parallel workers and starved the GL-commit rebuild).

    ``set_config(..., is_local=true)`` == ``SET LOCAL`` — scoped to the CURRENT
    transaction only, never leaks to other pooled checkouts.  It is reset by the
    next COMMIT/ROLLBACK, so a caller that commits mid-work (e.g. a warming loop
    that persists each snapshot) must RE-ARM it per transaction.  Idempotent —
    safe to call again at the start of every read transaction.
    """
    db.execute(
        text("SELECT set_config('statement_timeout', :ms, true)"),
        {"ms": str(settings.read_statement_timeout_ms)},
    )
    db.execute(text("SELECT set_config('max_parallel_workers_per_gather', '0', true)"))


def get_read_session() -> Iterator[Session]:
    """FastAPI dependency yielding a guarded session for heavy report READS.

    See :func:`apply_read_guard` for the bounded statement_timeout + serial plan.
    """
    db = SessionLocal()
    try:
        apply_read_guard(db)
        yield db
    finally:
        db.close()


@contextmanager
def read_session_scope() -> Iterator[Session]:
    """Guarded read session for NON-request callers (background threads, warmers,
    scripts) that cannot use the :func:`get_read_session` FastAPI dependency.

    Applies the SAME bounded statement_timeout + serial plan as
    :func:`get_read_session` so heavy report reads fired OUTSIDE an HTTP request
    can never run unbounded either.  NOTE: ``SET LOCAL`` is reset by COMMIT/ROLLBACK
    — a caller whose loop commits mid-work must re-arm with
    :func:`apply_read_guard` per transaction (this scope only arms the first one).
    """
    db = SessionLocal()
    try:
        apply_read_guard(db)
        yield db
    finally:
        db.close()
