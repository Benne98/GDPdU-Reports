"""Versioned-plan resolution (Phase 4, decision 4).

A **plan version** (``dim_plan_version``) is scoped to
``(project_id, statement, fiscal_year)``.  Exactly ONE version per scope is
``is_active`` (enforced by a partial unique index — ``scenario`` is NOT in the key),
and only a version with ``include_in_reporting = TRUE`` feeds the reporting
forecast/coverage columns.  Forecast is a DERIVED column of that single active
version (actuals-YTD ⊕ the active version's remaining-month plan) — there is no
separately-stored ``scenario='forecast'`` band in the read path anymore.  See
``docs/financial-logic.md`` "Phase 4 — Versioned plans".

This module is a THIN, side-effect-free (except the explicit CRUD helpers)
resolution layer with a **graceful legacy escape hatch**: on a DB WITHOUT the
``dim_plan_version`` table (un-migrated merged stack) every resolver returns the
``legacy`` state, and the callers keep their pre-Phase-4 behaviour byte-identical —
a DB without the new table must never 500.

Scope states (``resolve_plan_scope``):
    ``legacy``           — table absent → callers keep the pre-Phase-4 path.
    ``active_included``  — active version exists, include_in_reporting=TRUE →
                           forecast/coverage read the active version's plan.
    ``active_parked``    — active version exists, include_in_reporting=FALSE →
                           forecast/coverage read as None (parked, never stale).
    ``no_version``       — table present, no active version → parked (None).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

# Scope-state sentinels (string literals kept simple for the callers' branching).
LEGACY = "legacy"
ACTIVE_INCLUDED = "active_included"
ACTIVE_PARKED = "active_parked"
NO_VERSION = "no_version"


@dataclass(frozen=True)
class PlanVersion:
    """One ``dim_plan_version`` row (the fields the readers/endpoints need)."""

    plan_version_id: int
    project_id: Optional[int]
    statement: str
    fiscal_year: int
    label: str
    is_active: bool
    include_in_reporting: bool


def table_exists(session: Session) -> bool:
    """True iff ``dim_plan_version`` exists in the current schema (graceful gate).

    Any DB error (e.g. a broken/rolled-back transaction) is treated as "absent" so
    the caller falls back to the legacy path rather than propagating a 500.
    """
    try:
        row = session.execute(
            text(
                """
                SELECT 1 AS ok FROM information_schema.tables
                WHERE table_schema = current_schema()
                  AND table_name = 'dim_plan_version'
                LIMIT 1
                """
            )
        ).fetchone()
    except Exception:
        return False
    if row is None:
        return False
    # STRICT value check (mirror anomaly_snapshot_cache.tables_exist): a permissive
    # mock session that answers any SELECT with fixture rows must NOT be mistaken for
    # a real table — only a literal ``1`` counts, so such tests resolve to LEGACY and
    # stay byte-identical.
    try:
        val = row[0] if not hasattr(row, "_mapping") else next(iter(row._mapping.values()))
        return val == 1
    except Exception:
        return False


def active_version(
    session: Session,
    statement: str,
    fiscal_year: int,
    *,
    project_id: Optional[int] = None,
) -> Optional[PlanVersion]:
    """The single active version for the scope, or None (also None if table absent).

    ``project_id=None`` resolves the default/global scope (``project_id IS NULL``) —
    the current tenant model has no per-project plan facts.  Never raises on a
    missing table; returns None so the caller can pick its own fallback.
    """
    if not table_exists(session):
        return None
    proj_clause = "project_id IS NULL" if project_id is None else "project_id = :pid"
    params: dict[str, Any] = {"stmt": statement, "fy": int(fiscal_year)}
    if project_id is not None:
        params["pid"] = int(project_id)
    try:
        row = session.execute(
            text(
                f"""
                SELECT plan_version_id, project_id, statement, fiscal_year, label,
                       is_active, include_in_reporting
                FROM dim_plan_version
                WHERE {proj_clause}
                  AND statement = :stmt
                  AND fiscal_year = :fy
                  AND is_active
                LIMIT 1
                """
            ),
            params,
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    m = row._mapping
    return PlanVersion(
        plan_version_id=int(m["plan_version_id"]),
        project_id=(int(m["project_id"]) if m["project_id"] is not None else None),
        statement=str(m["statement"]),
        fiscal_year=int(m["fiscal_year"]),
        label=str(m["label"]),
        is_active=bool(m["is_active"]),
        include_in_reporting=bool(m["include_in_reporting"]),
    )


def resolve_plan_scope(
    session: Session,
    statement: str,
    fiscal_year: int,
    *,
    project_id: Optional[int] = None,
) -> tuple[str, Optional[PlanVersion]]:
    """Resolve the reporting scope for a (statement, fiscal_year) — the read gate.

    Returns ``(state, version_or_None)`` where ``state`` is one of ``LEGACY``,
    ``ACTIVE_INCLUDED``, ``ACTIVE_PARKED``, ``NO_VERSION``.  Callers branch:

      * ``LEGACY``          → keep the pre-Phase-4 path (byte-identical).
      * ``ACTIVE_INCLUDED`` → read the active version's plan (``scenario='budget'``).
      * ``ACTIVE_PARKED`` / ``NO_VERSION`` → forecast/coverage read as None (parked).
    """
    if not table_exists(session):
        return LEGACY, None
    ver = active_version(session, statement, fiscal_year, project_id=project_id)
    if ver is None:
        return NO_VERSION, None
    return (ACTIVE_INCLUDED if ver.include_in_reporting else ACTIVE_PARKED), ver


def reporting_included(
    session: Session,
    statement: str,
    fiscal_year: int,
    *,
    project_id: Optional[int] = None,
) -> Optional[bool]:
    """Tri-state reporting gate: True (included), False (parked), None (legacy).

    ``True``  — an active, include_in_reporting version exists → read its plan.
    ``False`` — parked (active-but-excluded OR no active version) → forecast None.
    ``None``  — legacy (table absent) → caller keeps the pre-Phase-4 behaviour.
    """
    state, _ver = resolve_plan_scope(session, statement, fiscal_year, project_id=project_id)
    if state == LEGACY:
        return None
    return state == ACTIVE_INCLUDED


# =========================================================================== #
# CRUD (thin) — used by the /api/v1/budget/versions endpoints.  Each commits.
# =========================================================================== #
def list_versions(
    session: Session,
    *,
    statement: Optional[str] = None,
    fiscal_year: Optional[int] = None,
    project_id: Optional[int] = None,
) -> list[PlanVersion]:
    """Every version matching the (optional) scope, newest first.  [] if table absent."""
    if not table_exists(session):
        return []
    clauses: list[str] = ["project_id IS NULL" if project_id is None else "project_id = :pid"]
    params: dict[str, Any] = {}
    if project_id is not None:
        params["pid"] = int(project_id)
    if statement:
        clauses.append("statement = :stmt")
        params["stmt"] = statement
    if fiscal_year is not None:
        clauses.append("fiscal_year = :fy")
        params["fy"] = int(fiscal_year)
    where = " AND ".join(clauses)
    rows = session.execute(
        text(
            f"""
            SELECT plan_version_id, project_id, statement, fiscal_year, label,
                   is_active, include_in_reporting
            FROM dim_plan_version
            WHERE {where}
            ORDER BY statement, fiscal_year, is_active DESC, plan_version_id DESC
            """
        ),
        params,
    ).fetchall()
    return [
        PlanVersion(
            plan_version_id=int(r._mapping["plan_version_id"]),
            project_id=(int(r._mapping["project_id"]) if r._mapping["project_id"] is not None else None),
            statement=str(r._mapping["statement"]),
            fiscal_year=int(r._mapping["fiscal_year"]),
            label=str(r._mapping["label"]),
            is_active=bool(r._mapping["is_active"]),
            include_in_reporting=bool(r._mapping["include_in_reporting"]),
        )
        for r in rows
    ]


def create_version(
    session: Session,
    *,
    statement: str,
    fiscal_year: int,
    label: str,
    project_id: Optional[int] = None,
    activate: bool = False,
    include_in_reporting: bool = True,
    updated_by: Optional[str] = None,
) -> int:
    """Create a version; when ``activate`` it becomes THE active one (others cleared
    for the scope first, so the partial-unique invariant holds).  Returns the new id.
    Commits."""
    try:
        if activate:
            _deactivate_scope(session, statement, fiscal_year, project_id)
        row = session.execute(
            text(
                """
                INSERT INTO dim_plan_version
                    (project_id, statement, fiscal_year, label, is_active,
                     include_in_reporting, updated_by)
                VALUES (:pid, :stmt, :fy, :label, :active, :incl, :ub)
                RETURNING plan_version_id
                """
            ),
            {
                "pid": (int(project_id) if project_id is not None else None),
                "stmt": statement,
                "fy": int(fiscal_year),
                "label": label,
                "active": bool(activate),
                "incl": bool(include_in_reporting),
                "ub": updated_by,
            },
        ).fetchone()
        session.commit()
        return int(row[0])
    except Exception:
        session.rollback()
        raise


def _deactivate_scope(
    session: Session,
    statement: str,
    fiscal_year: int,
    project_id: Optional[int],
) -> None:
    """Clear is_active for every version in the scope (no commit — caller owns txn)."""
    proj_clause = "project_id IS NULL" if project_id is None else "project_id = :pid"
    params: dict[str, Any] = {"stmt": statement, "fy": int(fiscal_year)}
    if project_id is not None:
        params["pid"] = int(project_id)
    session.execute(
        text(
            f"""
            UPDATE dim_plan_version SET is_active = FALSE, updated_at = NOW()
            WHERE {proj_clause} AND statement = :stmt AND fiscal_year = :fy
              AND is_active
            """
        ),
        params,
    )


def set_active(session: Session, plan_version_id: int, *, updated_by: Optional[str] = None) -> None:
    """Make ``plan_version_id`` the sole active version for its scope.  Commits."""
    try:
        ver = session.execute(
            text(
                "SELECT project_id, statement, fiscal_year FROM dim_plan_version "
                "WHERE plan_version_id = :id"
            ),
            {"id": int(plan_version_id)},
        ).fetchone()
        if ver is None:
            raise ValueError(f"plan_version_id {plan_version_id} not found")
        m = ver._mapping
        _deactivate_scope(
            session, str(m["statement"]), int(m["fiscal_year"]),
            (int(m["project_id"]) if m["project_id"] is not None else None),
        )
        session.execute(
            text(
                "UPDATE dim_plan_version SET is_active = TRUE, updated_at = NOW(), "
                "updated_by = :ub WHERE plan_version_id = :id"
            ),
            {"id": int(plan_version_id), "ub": updated_by},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise


def set_include_in_reporting(
    session: Session,
    plan_version_id: int,
    include: bool,
    *,
    updated_by: Optional[str] = None,
) -> None:
    """Toggle include_in_reporting for one version.  Commits.

    Un-checking parks the reporting forecast/coverage columns immediately (the
    reader resolves ``active_parked`` → None), so they never show a stale value.
    """
    try:
        session.execute(
            text(
                "UPDATE dim_plan_version SET include_in_reporting = :incl, "
                "updated_at = NOW(), updated_by = :ub WHERE plan_version_id = :id"
            ),
            {"incl": bool(include), "ub": updated_by, "id": int(plan_version_id)},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
