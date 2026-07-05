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

#: Allowed values for the flags that drive the rebuild.
VALID_OPENING_BALANCE_MODES = frozenset({"in_data", "file", "carry_forward"})
VALID_NET_PROFIT_SOURCES = frozenset({"report_inject", "gl_rows"})
#: 'library'   -> fill MISSING (account, fiscal_year) dim_gl_account rows from the
#:               account mapping library (most-frequent by account_name) — DEFAULT.
#: 'exclusive' -> use ONLY the project's provided per-(account, year) mapping; the
#:               library fill is SKIPPED, so years the mapping file never covered
#:               stay unmapped.
VALID_ACCOUNT_MAPPING_MODES = frozenset({"library", "exclusive"})

#: Canonical config shape returned to the frontend wizard.  Values here are the
#: LEGACY defaults (must equal the settings.* defaults to keep golden equivalence).
DEFAULT_CONFIG: dict[str, Any] = {
    "entities": [],  # list of {code, prefix, name}
    "fy_start_month": 1,
    "opening_balance_mode": "in_data",
    "net_profit_source": "report_inject",
    "mapping_source": "library",  # 'library' | 'client_coa'
    "account_mapping_mode": "library",  # 'library' (fill missing years) | 'exclusive'
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
    if not _table_exists(session, "admin_project_config"):
        return fallback

    row = session.execute(
        text(
            """
            SELECT p.project_id, p.name, p.fy_start_month, c.config
            FROM dim_project AS p
            LEFT JOIN admin_project_config AS c ON c.project_id = p.project_id
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
    if not _table_exists(session, "admin_project_config"):
        raise RuntimeError("admin_project_config table missing — run migration 0011_project_config")

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
            INSERT INTO admin_project_config (project_id, config, updated_at)
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


def upsert_project_entities(
    session: Session,
    entities: list[dict[str, Any]] | None,
    *,
    source_system: str = "project_setup",
) -> list[str]:
    """UPSERT the wizard's project entities into ``dim_legal_entity`` up front.

    Phase-2 ordering fix: the Project Setup Finish now commits the account mapping
    (``bs_pl_master``) BEFORE the GL data, but the CoA commit resolves/validates
    entity references against ``dim_legal_entity`` (``build_entity_lookup``).  On a
    fresh DB those rows did not yet exist (they were created only at GL-commit time),
    so the CoA commit 422'd.  Creating the entities here — driven by the wizard's
    known entity list (``{code, prefix, name}``) — lets the CoA commit resolve them.

    Idempotent: re-runs upsert the same ``legal_entity_code`` rows (no duplicates).
    Uses ``load_legal_entity`` so ``legal_entity_code == entity_prefix`` and both the
    code and name become lookup keys (``build_entity_lookup`` keys on both).

    The caller owns the transaction; this function does not commit.  Returns the
    list of normalised 2-char prefixes that were upserted.
    """
    if not entities:
        return []

    # Imported lazily so etl.project_config stays importable without etl.load's deps.
    from etl.load import load_legal_entity
    from etl.transform import normalize_prefix

    upserted: list[str] = []
    for ent in entities:
        if not isinstance(ent, dict):
            continue
        raw_prefix = str(ent.get("prefix") or ent.get("code") or "").strip()
        if not raw_prefix:
            continue
        try:
            prefix = normalize_prefix(raw_prefix)
        except (ValueError, TypeError):
            logger.warning("upsert_project_entities: skipping non-numeric entity %r", ent)
            continue
        name = str(ent.get("name") or ent.get("code") or prefix).strip() or prefix
        load_legal_entity(
            session,
            entity_prefix=prefix,
            entity_name=name,
            source_system=source_system,
        )
        upserted.append(prefix)

    logger.info("upsert_project_entities: %d entity row(s) upserted", len(upserted))
    return upserted


def resolve_rebuild_flags(
    session: Session, project_id: str = DEFAULT_PROJECT_ID
) -> dict[str, str]:
    """Resolve the rebuild flags for *project_id*.

    Returns ``{"opening_balance_mode": ..., "net_profit_source": ...,
    "account_mapping_mode": ...}``.  The per-project config drives these so a
    wizard setup persists and is reused on every update.  Values are validated; an
    unknown stored value falls back to the settings/legacy default for that flag.
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

    am = cfg.get("account_mapping_mode") or DEFAULT_CONFIG["account_mapping_mode"]
    if am not in VALID_ACCOUNT_MAPPING_MODES:
        logger.warning("project %s: invalid account_mapping_mode %r; using %s",
                       project_id, am, DEFAULT_CONFIG["account_mapping_mode"])
        am = DEFAULT_CONFIG["account_mapping_mode"]

    return {
        "opening_balance_mode": ob,
        "net_profit_source": np,
        "account_mapping_mode": am,
    }


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
