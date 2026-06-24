"""Per-project config persistence + rebuild-flag resolution (reporting-v2 Phase 7).

The no-code project-setup wizard persists a project's answers once into
``project_config`` (JSONB) + ``dim_project`` (identity).  Every later update
re-reads that config so the setup is reused without re-running the wizard.

This module owns:

  * ``DEFAULT_CONFIG`` — the canonical config shape + LEGACY default values.
  * ``read_project_config`` / ``upsert_project_config`` — DB CRUD used by the
    ``/api/v1/projects`` router.
  * ``resolve_rebuild_flags`` — derive the rebuild flags (opening_balance_mode,
    net_profit_source) for a project, falling back to ``settings.*`` (and thus
    the LEGACY behaviour) when the project / its config row is absent.

NO-OP / GOLDEN GUARANTEE
────────────────────────
The seeded ``'default'`` config mirrors the current ``settings.*`` defaults
(opening_balance_mode='in_data', net_profit_source='report_inject').  When a
project row is missing, every helper falls back to those same settings, so the
rebuild behaves exactly as before — the golden live-vs-v2 equivalence holds.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

DEFAULT_PROJECT_ID = "default"

#: Allowed values for the two flags that drive the rebuild.
VALID_OPENING_BALANCE_MODES = frozenset({"in_data", "file", "carry_forward"})
VALID_NET_PROFIT_SOURCES = frozenset({"report_inject", "gl_rows"})

#: Canonical config shape returned to the frontend wizard.  Values here are the
#: LEGACY defaults (must equal the settings.* defaults to keep golden equivalence).
DEFAULT_CONFIG: dict[str, Any] = {
    "entities": [],  # list of {code, prefix, name}
    "fy_start_month": 1,
    "opening_balance_mode": "in_data",
    "net_profit_source": "report_inject",
    "mapping_source": "library",  # 'library' | 'client_coa'
    "partner_master_source": "files",  # 'files' | 'gdpdu'
    "sales_label": "Sales",
    "cost_label": "Cost of materials",
}

#: Config keys persisted (anything else in a PUT body is ignored).
_CONFIG_KEYS = tuple(DEFAULT_CONFIG.keys())


def _table_exists(session: Session, table: str) -> bool:
    """Dialect-aware existence check that never poisons the open transaction."""
    try:
        dialect = session.bind.dialect.name if session.bind is not None else ""
    except Exception:  # noqa: BLE001
        dialect = ""
    try:
        if dialect == "postgresql":
            got = session.execute(
                text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}
            ).scalar()
            return got is not None
        if dialect == "sqlite":
            got = session.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
                {"t": table},
            ).scalar()
            return got is not None
        session.execute(text(f"SELECT 1 FROM {table} WHERE 1=0"))
        return True
    except Exception:  # noqa: BLE001
        return False


def _coerce_config(raw: Any) -> dict[str, Any]:
    """Merge a stored/partial config blob onto DEFAULT_CONFIG (defaults win on miss)."""
    out = dict(DEFAULT_CONFIG)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:  # noqa: BLE001
            raw = {}
    if isinstance(raw, dict):
        for k in _CONFIG_KEYS:
            if k in raw and raw[k] is not None:
                out[k] = raw[k]
    return out


def read_project_config(
    session: Session, project_id: str = DEFAULT_PROJECT_ID
) -> dict[str, Any]:
    """Return ``{project_id, name, fy_start_month, config:{...}}`` for a project.

    Falls back to a synthetic default record (DEFAULT_CONFIG) when the project /
    its config row / the tables are absent — so the API and rebuild keep working
    on a partially-migrated DB.
    """
    fallback = {
        "project_id": project_id,
        "name": None,
        "fy_start_month": int(DEFAULT_CONFIG["fy_start_month"]),
        "config": dict(DEFAULT_CONFIG),
    }
    if not _table_exists(session, "project_config"):
        return fallback

    row = session.execute(
        text(
            """
            SELECT p.project_id, p.name, p.fy_start_month, c.config
            FROM dim_project AS p
            LEFT JOIN project_config AS c ON c.project_id = p.project_id
            WHERE p.project_id = :pid
            """
        ),
        {"pid": project_id},
    ).fetchone()
    if row is None:
        return fallback

    cfg = _coerce_config(row[3])
    # dim_project.fy_start_month is the source of truth for the FY start; mirror it
    # into the config blob so the wizard always sees a consistent value.
    fy_start = int(row[2]) if row[2] is not None else int(cfg["fy_start_month"])
    cfg["fy_start_month"] = fy_start
    return {
        "project_id": row[0],
        "name": row[1],
        "fy_start_month": fy_start,
        "config": cfg,
    }


def upsert_project_config(
    session: Session,
    project_id: str,
    *,
    name: str | None = None,
    config: dict[str, Any] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Create/update a project's identity + config blob.  Returns the stored record.

    ``config`` is merged onto DEFAULT_CONFIG before persistence so every stored
    blob carries the full canonical shape.  ``fy_start_month`` is mirrored from
    the config onto ``dim_project`` (the column is the FY-start source of truth).
    """
    if not _table_exists(session, "project_config"):
        raise RuntimeError("project_config table missing — run migration 0011_project_config")

    merged = _coerce_config(config or {})
    fy_start = int(merged["fy_start_month"])
    config_json = json.dumps(merged, default=str)

    session.execute(
        text(
            """
            INSERT INTO dim_project (project_id, name, fy_start_month, updated_at)
            VALUES (:pid, :name, :fy, NOW())
            ON CONFLICT (project_id) DO UPDATE SET
                name = COALESCE(EXCLUDED.name, dim_project.name),
                fy_start_month = EXCLUDED.fy_start_month,
                updated_at = NOW()
            """
        ),
        {"pid": project_id, "name": name, "fy": fy_start},
    )
    session.execute(
        text(
            """
            INSERT INTO project_config (project_id, config, updated_at)
            VALUES (:pid, CAST(:cfg AS JSONB), NOW())
            ON CONFLICT (project_id) DO UPDATE SET
                config = EXCLUDED.config,
                updated_at = NOW()
            """
        ),
        {"pid": project_id, "cfg": config_json},
    )
    if commit:
        session.commit()

    return read_project_config(session, project_id)


def resolve_rebuild_flags(
    session: Session, project_id: str = DEFAULT_PROJECT_ID
) -> dict[str, str]:
    """Resolve the rebuild flags for *project_id*.

    Returns ``{"opening_balance_mode": ..., "net_profit_source": ...}``.  The
    per-project config drives these so a wizard setup persists and is reused on
    every update.  Values are validated; an unknown stored value falls back to the
    settings default for that flag (and thus LEGACY behaviour).
    """
    settings_ob, settings_np = _settings_defaults()
    record = read_project_config(session, project_id)
    cfg = record.get("config") or {}

    ob = cfg.get("opening_balance_mode") or settings_ob
    if ob not in VALID_OPENING_BALANCE_MODES:
        logger.warning("project %s: invalid opening_balance_mode %r; using %s",
                       project_id, ob, settings_ob)
        ob = settings_ob

    np = cfg.get("net_profit_source") or settings_np
    if np not in VALID_NET_PROFIT_SOURCES:
        logger.warning("project %s: invalid net_profit_source %r; using %s",
                       project_id, np, settings_np)
        np = settings_np

    return {"opening_balance_mode": ob, "net_profit_source": np}


def _settings_defaults() -> tuple[str, str]:
    """(opening_balance_mode, net_profit_source) from backend settings or env.

    Mirrors etl.rebuild's self-contained settings lookup so the ETL stays usable
    without the backend importable (pure-ETL test contexts).
    """
    try:
        import sys
        from pathlib import Path

        _backend = Path(__file__).resolve().parent.parent / "backend"
        if str(_backend) not in sys.path:
            sys.path.insert(0, str(_backend))
        from app.config import settings  # type: ignore

        return settings.opening_balance_mode, settings.bs_net_profit_source
    except Exception:  # noqa: BLE001
        import os

        return (
            os.getenv("OPENING_BALANCE_MODE", "in_data"),
            os.getenv("BS_NET_PROFIT_SOURCE", "report_inject"),
        )
