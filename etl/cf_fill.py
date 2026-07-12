r"""Populate ``dim_gl_cf`` from the CF mapping library (``lib_cf_mapping``).

SINGLE SOURCE OF TRUTH for the two set-based UPSERTs that fill ``dim_gl_cf`` — the
account→CF-line mapping the CF reader (``fin_compat_cf`` / ``fin_compat_cf_sql``)
joins GL grains against.  When ``dim_gl_cf`` is empty the CF statement renders
NOTHING, so this must run as part of every ``mode='full'`` rebuild after a fresh
Project-Setup data load (previously the population lived ONLY in the standalone
``backend/scripts/populate_dim_gl_cf.py``, which the rebuild never invoked).

Two disjoint UPSERTs, keyed ``(account_number_group, fiscal_year)``:

  BS / Net-asset accounts (``_NA_UPSERT``):
      dim_gl_na (l6_na_mapping, l7_na_description)
        ⋈ lib_cf_mapping (key_kind='na', key_1=l6_na_mapping,
                              key_2=l7_na_description)

  P&L accounts (``_PL_UPSERT``):
      dim_gl_account (level_0='PL', level_3)
        ⋈ lib_cf_mapping (key_kind='pl_level3', key_1='PL', key_2=level_3)

Both are ``ON CONFLICT (account_number_group, fiscal_year) DO UPDATE`` → strictly
idempotent: re-running reproduces identical rows (row COUNT unchanged), so a DB
where ``dim_gl_cf`` was already correct (golden / live) is unchanged in effect.

NO-OP / GOLDEN PARITY
─────────────────────
A strict NO-OP (writes nothing, never raises) when the CF library is absent/empty
or the source dims are absent (partial schema / pure-ETL test DB / a project with
no CF library loaded).  This mirrors the other rebuild stages' handling of absent
source tables (``_stage_account_library_fill`` / ``statement_backfill``) so the
golden live-vs-rebuild equivalence is preserved.

``backend/scripts/populate_dim_gl_cf.py`` imports ``_NA_UPSERT`` / ``_PL_UPSERT``
from here so the SQL lives in ONE place and cannot drift.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Set-based UPSERT for the BS/NA side.
_NA_UPSERT = """
INSERT INTO dim_gl_cf (account_number_group, fiscal_year, l1, l2, l3, l4, l5, cf_mapping)
SELECT na.account_number_group, na.fiscal_year,
       lib.l1, lib.l2, lib.l3, lib.l4, lib.l5, lib.cf_mapping
FROM dim_gl_na na
JOIN lib_cf_mapping lib
  ON lib.key_kind = 'na'
 AND lib.key_1 = na.l6_na_mapping
 AND lib.key_2 = na.l7_na_description
ON CONFLICT (account_number_group, fiscal_year) DO UPDATE SET
  l1 = EXCLUDED.l1, l2 = EXCLUDED.l2, l3 = EXCLUDED.l3,
  l4 = EXCLUDED.l4, l5 = EXCLUDED.l5, cf_mapping = EXCLUDED.cf_mapping;
"""

# Set-based UPSERT for the P&L side (level_3 keyed).
_PL_UPSERT = """
INSERT INTO dim_gl_cf (account_number_group, fiscal_year, l1, l2, l3, l4, l5, cf_mapping)
SELECT a.account_number_group, a.fiscal_year,
       lib.l1, lib.l2, lib.l3, lib.l4, lib.l5, lib.cf_mapping
FROM dim_gl_account a
JOIN lib_cf_mapping lib
  ON lib.key_kind = 'pl_level3'
 AND lib.key_1 = 'PL'
 AND lib.key_2 = a.level_3
WHERE a.level_0 = 'PL'
ON CONFLICT (account_number_group, fiscal_year) DO UPDATE SET
  l1 = EXCLUDED.l1, l2 = EXCLUDED.l2, l3 = EXCLUDED.l3,
  l4 = EXCLUDED.l4, l5 = EXCLUDED.l5, cf_mapping = EXCLUDED.cf_mapping;
"""


def _safe_count(session: Session, table: str) -> Optional[int]:
    """COUNT(*) of ``table``; None when the table is absent (partial schema).

    Probing existence BEFORE the UPSERTs matters on Postgres: a statement that
    errors on a missing table aborts the whole transaction, so we never let an
    absent-table reference reach the UPSERT — we detect it up front and skip that
    side gracefully (matching ``statement_backfill._load_mapping_rows``).
    """
    try:
        return int(session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
    except Exception as exc:  # noqa: BLE001 — absent table => source unavailable
        logger.debug("cf_fill: table %s unavailable (%s)", table, exc)
        return None


def populate_dim_gl_cf(session: Session, scope=None) -> dict:
    """Fill ``dim_gl_cf`` from ``lib_cf_mapping`` via the NA + P&L UPSERTs.

    Deterministic, idempotent (``ON CONFLICT DO UPDATE``) and additive-in-effect:
    on a DB whose ``dim_gl_cf`` is already correct it reproduces the same rows (row
    count unchanged).  Runs BOTH UPSERTs (``_NA_UPSERT`` then ``_PL_UPSERT``) in the
    caller's open transaction; the caller commits.

    ``scope`` is accepted for signature parity with the other rebuild stages and is
    recorded only — the UPSERTs are global set-based statements (they mirror the
    standalone ``populate_dim_gl_cf.py``); no per-scope filtering is applied here.

    NO-OP (never raises):
      * ``lib_cf_mapping`` absent or empty  → skip both sides (no CF library loaded).
      * ``dim_gl_na`` absent                → skip the NA side.
      * ``dim_gl_account`` absent           → skip the P&L side.

    Returns
    -------
    dict
        ``{"na_rows": int, "pl_rows": int, "total": int, "noop": bool,
           "skipped": bool}`` where ``na_rows`` / ``pl_rows`` are the rows the
        respective UPSERT touched (insert+update) and ``total`` is the final
        ``dim_gl_cf`` row count.
    """
    summary = {"na_rows": 0, "pl_rows": 0, "total": 0, "noop": True, "skipped": False}

    lib_n = _safe_count(session, "lib_cf_mapping")
    if not lib_n:  # None (table absent) OR 0 (empty) => strict no-op, golden parity.
        reason = "absent" if lib_n is None else "empty"
        logger.warning(
            "cf_fill: lib_cf_mapping is %s — dim_gl_cf population skipped (no-op)", reason
        )
        summary["skipped"] = True
        summary["total"] = _safe_count(session, "dim_gl_cf") or 0
        return summary

    ran = False

    # NA / BS side — requires dim_gl_na (populated at load time).
    if _safe_count(session, "dim_gl_na") is not None:
        res = session.execute(text(_NA_UPSERT))
        summary["na_rows"] = int(res.rowcount or 0)
        ran = True
    else:
        logger.warning("cf_fill: dim_gl_na absent — NA/BS side of dim_gl_cf skipped")

    # P&L side — requires dim_gl_account (level_0='PL' set by classification/backfill).
    if _safe_count(session, "dim_gl_account") is not None:
        res = session.execute(text(_PL_UPSERT))
        summary["pl_rows"] = int(res.rowcount or 0)
        ran = True
    else:
        logger.warning("cf_fill: dim_gl_account absent — P&L side of dim_gl_cf skipped")

    summary["noop"] = not ran
    summary["total"] = _safe_count(session, "dim_gl_cf") or 0
    logger.info(
        "cf_fill: dim_gl_cf populated (na_rows=%d, pl_rows=%d, total=%d)%s",
        summary["na_rows"], summary["pl_rows"], summary["total"],
        " [no-op]" if summary["noop"] else "",
    )
    return summary
