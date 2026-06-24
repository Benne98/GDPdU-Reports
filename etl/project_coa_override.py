"""Per-project chart-of-accounts override replay (reporting-v2 Phase 4).

Owns rebuild **stage 1** ("classification refresh").  The CoA editor persists
hierarchy/sort edits into ``dim_gl_account`` directly today; to make those edits
survive a full data reload (which re-upserts ``dim_gl_account`` from the upload),
they are ALSO captured into ``dim_project_coa_override`` and **replayed** onto
``dim_gl_account`` at the start of every rebuild.

NO-OP GUARANTEE
───────────────
When ``dim_project_coa_override`` holds no rows for the project's scope, the replay
touches nothing — so the golden live-vs-v2 equivalence is preserved.  This is the
gate condition (Phase 4): with no override rows, a rebuild on ``finssentials_v2``
must keep ``compare live v2`` exit 0.

MULTI-TENANCY
─────────────
A full ``dim_project`` is out of scope.  ``project_id`` defaults to
``DEFAULT_PROJECT_ID`` (``'default'``).  The table + functions take a project_id so
a real one can be threaded through later.

SCOPE
─────
Replay is restricted to the rebuild scope's fiscal years when present (so an
incremental monthly update never rewrites other years).  Account-prefix scope is
NOT applied here because the override rows are already keyed per
``account_number_group`` (which embeds the entity prefix); replaying all override
rows in the year scope is correct and cheaper than an extra prefix filter.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

DEFAULT_PROJECT_ID = "default"

#: Hierarchy + sort columns the override carries onto dim_gl_account.  Order does
#: not matter; the set must match the override table + dim_gl_account columns.
OVERRIDE_LEVEL_FIELDS: tuple[str, ...] = (
    "level_0",
    "level_1",
    "level_2",
    "level_3",
    "level_4",
    "l4_sub",
    "level_1_sort",
    "level_2_sort",
    "level_3_sort",
    "level_4_sort",
)


def _year_set(scope) -> set[int] | None:
    """Target fiscal years from the rebuild scope, or None for all years."""
    years = getattr(scope, "years", None) or []
    return {int(y) for y in years} if years else None


def _override_table_exists(session: Session) -> bool:
    """Dialect-aware existence check for ``dim_project_coa_override``.

    Done with a SELECT (never a failing DDL/DML) so a missing table never aborts
    the caller's open transaction (critical on PostgreSQL, where a failed statement
    poisons the txn until rollback).  Returns False on any inability to confirm.
    """
    try:
        dialect = session.bind.dialect.name if session.bind is not None else ""
    except Exception:  # noqa: BLE001
        dialect = ""
    try:
        if dialect == "postgresql":
            got = session.execute(
                text("SELECT to_regclass('public.dim_project_coa_override')")
            ).scalar()
            return got is not None
        if dialect == "sqlite":
            got = session.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='dim_project_coa_override'"
                )
            ).scalar()
            return got is not None
        # Unknown dialect: probe with a bounded SELECT; failure => treat as absent.
        session.execute(text("SELECT 1 FROM dim_project_coa_override WHERE 1=0"))
        return True
    except Exception:  # noqa: BLE001
        return False


def replay_project_overrides(
    session: Session,
    scope=None,
    *,
    project_id: str = DEFAULT_PROJECT_ID,
) -> dict:
    """Replay ``dim_project_coa_override`` rows onto ``dim_gl_account`` (stage 1).

    For every override row of *project_id* (optionally restricted to the scope's
    fiscal years), UPDATE the matching ``dim_gl_account`` row's level_* / *_sort
    columns.  Rows with no matching account are silently ignored (the account may
    not exist in this dataset).  Returns ``{"classification_refreshed": <n>}`` to
    slot into the rebuild summary where the old no-op returned the same shape.

    NO-OP when the override table is empty/absent for the project — see module
    docstring.  The replay is set-based (one UPDATE…FROM) so it is cheap and
    idempotent (re-running writes the same values).
    """
    # Existence check FIRST so a missing table never aborts the open transaction
    # (preserves behaviour on a partially-migrated DB / pure-ETL test schema).
    if not _override_table_exists(session):
        logger.info("project CoA override replay skipped: table absent (no-op)")
        return {"classification_refreshed": 0, "override_table_missing": True}

    year_set = _year_set(scope)
    params: dict[str, Any] = {"pid": project_id}
    year_clause = ""
    if year_set:
        ph = ", ".join(f":y{i}" for i in range(len(year_set)))
        for i, y in enumerate(sorted(year_set)):
            params[f"y{i}"] = y
        year_clause = f" AND o.fiscal_year IN ({ph})"

    set_sql = ",\n            ".join(f"{f} = o.{f}" for f in OVERRIDE_LEVEL_FIELDS)

    # NOTE: PostgreSQL UPDATE … FROM and SQLite UPDATE … FROM (3.33+) share this
    # syntax; both are supported by the deployed stacks and the test SQLite build.
    res = session.execute(
        text(
            f"""
            UPDATE dim_gl_account AS a
            SET {set_sql}
            FROM dim_project_coa_override AS o
            WHERE o.project_id = :pid
              AND o.account_number_group = a.account_number_group
              AND o.fiscal_year = a.fiscal_year
              {year_clause}
            """
        ),
        params,
    )
    n = int(res.rowcount or 0)

    logger.info("project CoA override replay: %d account(s) refreshed (project=%s)", n, project_id)
    return {"classification_refreshed": n}


def capture_overrides_from_accounts(
    session: Session,
    accounts: Iterable[dict],
    *,
    project_id: str = DEFAULT_PROJECT_ID,
) -> int:
    """Upsert override rows from CoA-editor account dicts (persist edits).

    Each account dict carries ``account_number_group`` + ``fiscal_year`` plus any
    of the ``OVERRIDE_LEVEL_FIELDS``.  Called by the mapping-editor service after an
    edit so the change is captured into ``dim_project_coa_override`` and survives a
    later reload.  Returns the number of rows upserted.

    Idempotent: ON CONFLICT (project_id, account_number_group, fiscal_year) updates
    the level_* / *_sort columns and bumps ``updated_at``.
    """
    cols = ["project_id", "account_number_group", "fiscal_year", *OVERRIDE_LEVEL_FIELDS]
    placeholders = ", ".join(f":{c}" for c in cols)
    update_set = ",\n            ".join(
        f"{f} = EXCLUDED.{f}" for f in OVERRIDE_LEVEL_FIELDS
    )

    sql = text(
        f"""
        INSERT INTO dim_project_coa_override ({", ".join(cols)}, updated_at)
        VALUES ({placeholders}, CURRENT_TIMESTAMP)
        ON CONFLICT (project_id, account_number_group, fiscal_year) DO UPDATE SET
            {update_set},
            updated_at = CURRENT_TIMESTAMP
        """
    )

    upserted = 0
    for acc in accounts:
        ang = str(acc.get("account_number_group") or "").strip()
        fy = acc.get("fiscal_year")
        if not ang or fy is None:
            continue
        row: dict[str, Any] = {
            "project_id": project_id,
            "account_number_group": ang,
            "fiscal_year": int(fy),
        }
        for f in OVERRIDE_LEVEL_FIELDS:
            row[f] = acc.get(f)
        session.execute(sql, row)
        upserted += 1
    return upserted
