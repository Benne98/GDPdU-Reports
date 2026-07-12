r"""Fill ``dim_gl_na`` (the net-asset / BS classification) so the CF NA side renders.

WHY
---
The CF working-capital deltas and the WC statement are driven by the ``(l6_na_mapping,
l7_na_description)`` NA classification on every BS account:

  * ``etl.cf_fill._NA_UPSERT`` joins ``dim_gl_na`` ⋈ ``lib_cf_mapping``
    (``key_kind='na'``, ``key_1=l6_na_mapping``, ``key_2=l7_na_description``) to fill the
    BS-account rows of ``dim_gl_cf`` — the mapping the CF reader groups against.
  * ``fin_compat_wc_sql`` selects ``WHERE na.l6_na_mapping IN ('TWC','OWC')``.

``dim_gl_na`` is populated at LOAD time (``etl.load`` ~L1291) ONLY from the CoA columns
``l6_na_mapping`` / ``l7_na_description``.  The ``bs_pl_master`` CoA that Project Setup
uses does NOT carry the NA dimension, so ``l7_na_description`` ends up NULL (and on a
truly-fresh load ``l6`` too).  The NA join then matches 0 rows → ``dim_gl_cf`` has no
BS-account rows → the CF working-capital Δ lines and the WC statement are EMPTY.

THE FIX (deterministic, idempotent, ADDITIVE)
---------------------------------------------
For every BS account this ensures ``(l6_na_mapping, l7_na_description)`` is populated,
filling ONLY missing values (never overwriting a present one) and NEVER flipping an
account's WC membership:

  l6 (WC membership) — KEPT as-is when already present (a membership flip would move a
      WC total; that decision belongs to the NA library, never a heuristic).  Filled
      ONLY when currently NULL, and ONLY from the NA library (authoritative source).

  l7 (sub-description) — when NULL, resolve in priority order:
        1. LIBRARY — the account_name's most-frequent ``lib_na_mapping`` row
           (``etl.mapping_library.resolve.resolve_most_frequent`` — the SAME resolver
           ``backend/scripts/populate_dim_gl_na.py`` uses), BUT only when the library
           winner's ``l6`` is CONSISTENT with the account's kept ``l6`` (so the refined
           ``(l6, l7)`` pair is a real ``lib_cf_mapping`` key and the WC membership the
           l7 belongs to is unchanged).  Preserves v5's ~8% NA-library refinements.
        2. FALLBACK — the BS account's ``dim_gl_account.level_3`` (confirmed ≈92% match
           to v5's ``l7`` for BS accounts).

ADDITIVE / IDEMPOTENT / GOLDEN PARITY
-------------------------------------
Only NULLs are filled; a re-run reproduces identical rows.  On a DB whose ``dim_gl_na``
is already complete (golden / live — every ``l7`` non-null) NOTHING is written, so the
golden live-vs-rebuild equivalence is preserved.  A strict NO-OP (writes nothing, never
raises) when the sources are absent — ``dim_gl_account`` / ``dim_gl_na`` absent (partial
schema) or NEITHER the NA library NOR a ``level_3`` source available.  This mirrors the
absent-source probing of ``etl.cf_fill`` and ``etl.statement_backfill``.

The account→CF-line UPSERT that CONSUMES this classification lives in ``etl.cf_fill``
(``_stage_cf_fill``); this stage runs immediately BEFORE it in the rebuild.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_WC = {"TWC", "OWC"}


def _blank(value) -> bool:
    """True when a classification value is missing (NULL or empty/whitespace)."""
    return value is None or str(value).strip() == ""


# --------------------------------------------------------------------------- #
# Pure resolver — testable without a DB.
# --------------------------------------------------------------------------- #
def resolve_na_fill(cur_l6, cur_l7, lib_winner, level_3):
    """Return the ``(l6, l7)`` to write for one BS account — fill-only, never a flip.

    Parameters
    ----------
    cur_l6, cur_l7 : str | None
        The account's CURRENT ``dim_gl_na`` classification (NULL when unmapped).
    lib_winner : NaRow | None
        The account_name's most-frequent ``lib_na_mapping`` row
        (``etl.mapping_library.resolve.resolve_most_frequent``), or None when the
        library has no precedent for the name.
    level_3 : str | None
        The account's ``dim_gl_account.level_3`` (BS side) — the l7 fallback.

    Rules (ADDITIVE — a present value is never changed):
      * ``l6`` is KEPT when present (WC membership never flips).  Filled ONLY when
        currently NULL, and ONLY from the library winner (never a level_* heuristic).
      * ``l7`` is KEPT when present.  When NULL, prefer the library winner's
        ``na_description`` — but ONLY if the winner's ``l6`` is consistent with the
        RESULTING ``l6`` (so the refinement stays inside the same WC membership and the
        ``(l6, l7)`` pair is a valid ``lib_cf_mapping`` key); otherwise fall back to
        ``level_3``.  Left NULL when neither source yields a value.
    """
    # l6 — keep when present (never flip a membership); else fill from the library.
    out_l6 = cur_l6
    if _blank(out_l6):
        out_l6 = lib_winner.na_mapping if lib_winner is not None else None
        if _blank(out_l6):
            out_l6 = None

    # l7 — keep when present; else library (membership-consistent) then level_3.
    out_l7 = cur_l7
    if _blank(out_l7):
        lib_l7 = lib_winner.na_description if lib_winner is not None else None
        lib_l6 = lib_winner.na_mapping if lib_winner is not None else None
        if (
            lib_winner is not None
            and not _blank(lib_l7)
            and (out_l6 is None or lib_l6 == out_l6)
        ):
            out_l7 = lib_l7
        elif not _blank(level_3):
            out_l7 = level_3
        else:
            out_l7 = None

    return out_l6, out_l7


# --------------------------------------------------------------------------- #
# DB loaders (SQLite-portable: small tables read whole; absent table => sentinel).
# --------------------------------------------------------------------------- #
def _table_present(session: Session, table: str) -> bool:
    """True when ``table`` is queryable; False when absent (partial schema).

    Probed up front so an absent-table reference never reaches an UPDATE/INSERT and
    aborts the transaction (matches ``etl.cf_fill._safe_count`` /
    ``etl.statement_backfill._load_mapping_rows``).
    """
    try:
        session.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
        return True
    except Exception as exc:  # noqa: BLE001 — absent table => source unavailable
        logger.debug("na_fill: table %s unavailable (%s)", table, exc)
        return False


def _load_bs_accounts(session: Session, scope=None) -> list[dict]:
    """BS ``dim_gl_account`` rows needing an NA classification (account_name + level_3).

    ``dim_gl_account`` is the (small) Chart of Accounts, so it is read whole (no
    ``= ANY(:array)``) and scoped in Python — dialect-portable for the SQLite tests.
    Restricted to ``level_0='BS'`` because the NA dimension is the net-asset (BS) side
    only; ``level_0`` is final here (na_fill runs AFTER ``statement_backfill``).
    """
    rows = session.execute(text(
        "SELECT account_number_group, fiscal_year, account_name, level_3 "
        "FROM dim_gl_account WHERE level_0 = 'BS'"
    )).fetchall()
    prefixes = set(getattr(scope, "prefixes", []) or [])
    years = set(int(y) for y in (getattr(scope, "years", []) or []))
    out: list[dict] = []
    for r in rows:
        ang = r[0]
        if ang is None:
            continue
        fy = r[1]
        if prefixes and str(ang)[:2] not in prefixes:
            continue
        if years and (fy is None or int(fy) not in years):
            continue
        out.append({
            "ang": str(ang), "fy": int(fy) if fy is not None else None,
            "account_name": r[2], "level_3": r[3],
        })
    return out


def _load_na(session: Session) -> dict[tuple, tuple]:
    """``(account_number_group, fiscal_year)`` -> current ``(l6, l7)`` from dim_gl_na."""
    rows = session.execute(text(
        "SELECT account_number_group, fiscal_year, l6_na_mapping, l7_na_description "
        "FROM dim_gl_na"
    )).fetchall()
    return {
        (str(r[0]), int(r[1]) if r[1] is not None else None): (r[2], r[3])
        for r in rows if r[0] is not None
    }


def _load_library(session: Session) -> dict:
    """account_name -> [(na_mapping, na_description, occurrences), ...] from lib_na_mapping.

    Absent/empty ``lib_na_mapping`` (partial schema / no NA library loaded) -> ``{}`` so
    the resolver simply falls back to ``level_3``.  Mirrors the read in
    ``backend/scripts/populate_dim_gl_na._load_library`` (the resolution logic itself is
    the shared ``etl.mapping_library.resolve.resolve_most_frequent``).
    """
    from collections import defaultdict

    try:
        rows = session.execute(text(
            "SELECT account_name, na_mapping, na_description, occurrences FROM lib_na_mapping"
        )).fetchall()
    except Exception as exc:  # noqa: BLE001 — absent library table => source unavailable
        logger.debug("na_fill: lib_na_mapping unavailable (%s)", exc)
        return {}
    by_name: dict[str, list] = defaultdict(list)
    for r in rows:
        by_name[r[0]].append((r[1], r[2], int(r[3] or 0)))
    return by_name


_UPDATE_NA = (
    "UPDATE dim_gl_na SET l6_na_mapping = :l6, l7_na_description = :l7 "
    "WHERE account_number_group = :ang AND fiscal_year = :fy"
)

_INSERT_NA = (
    "INSERT INTO dim_gl_na (account_number_group, fiscal_year, l6_na_mapping, l7_na_description) "
    "VALUES (:ang, :fy, :l6, :l7) "
    "ON CONFLICT (account_number_group, fiscal_year) DO UPDATE SET "
    "l6_na_mapping = EXCLUDED.l6_na_mapping, l7_na_description = EXCLUDED.l7_na_description"
)


def fill_dim_gl_na(session: Session, scope=None) -> dict:
    """Fill missing ``dim_gl_na`` NA classification for BS accounts (library, else level_3).

    Deterministic, idempotent, ADDITIVE (only fills NULLs; never flips an existing l6
    membership).  Runs in the caller's open transaction; the caller commits.  ``scope``
    (prefixes/years) restricts which BS accounts are touched.

    NO-OP (never raises):
      * ``dim_gl_account`` absent  -> no account list / level_3 source -> strict no-op.
      * ``dim_gl_na`` absent       -> nowhere to write -> strict no-op.
      * NEITHER library NOR any level_3 available -> nothing resolvable -> no rows written.

    Returns
    -------
    dict
        ``{"filled_l7": int, "filled_l6": int, "inserted": int, "total_na": int,
           "noop": bool, "skipped": bool}`` — ``filled_l6/l7`` count rows whose
        respective value was NULL and got set; ``inserted`` counts brand-new dim_gl_na
        rows (truly-fresh load, resolved from the library); ``total_na`` is the final
        ``dim_gl_na`` row count.
    """
    summary = {
        "filled_l7": 0, "filled_l6": 0, "inserted": 0,
        "total_na": 0, "noop": True, "skipped": False,
    }

    if not _table_present(session, "dim_gl_na"):
        logger.warning("na_fill: dim_gl_na absent — NA fill skipped (no-op)")
        summary["skipped"] = True
        return summary
    if not _table_present(session, "dim_gl_account"):
        logger.warning("na_fill: dim_gl_account absent — NA fill skipped (no-op)")
        summary["skipped"] = True
        summary["total_na"] = int(
            session.execute(text("SELECT COUNT(*) FROM dim_gl_na")).scalar() or 0
        )
        return summary

    from etl.mapping_library.resolve import resolve_most_frequent

    accounts = _load_bs_accounts(session, scope)
    na_cur = _load_na(session)
    library = _load_library(session)

    for acc in accounts:
        key = (acc["ang"], acc["fy"])
        cur = na_cur.get(key)
        cur_l6, cur_l7 = cur if cur is not None else (None, None)

        winner = resolve_most_frequent(library.get(acc["account_name"], []))
        out_l6, out_l7 = resolve_na_fill(cur_l6, cur_l7, winner, acc["level_3"])

        if cur is None:
            # Truly-fresh load: no dim_gl_na row.  Insert only when a WC membership (l6)
            # is known — an l6-less NA row cannot join lib_cf_mapping (join needs both
            # keys), so inserting one would be inert.  l6 here comes only from the
            # library (safe / authoritative).
            if out_l6 is not None:
                session.execute(text(_INSERT_NA), {
                    "ang": acc["ang"], "fy": acc["fy"], "l6": out_l6, "l7": out_l7,
                })
                summary["inserted"] += 1
        elif (out_l6, out_l7) != (cur_l6, cur_l7):
            # ADDITIVE: resolve_na_fill only ever fills a NULL, so this UPDATE never
            # overwrites a present value and never flips a membership.
            session.execute(text(_UPDATE_NA), {
                "ang": acc["ang"], "fy": acc["fy"], "l6": out_l6, "l7": out_l7,
            })
            if _blank(cur_l6) and not _blank(out_l6):
                summary["filled_l6"] += 1
            if _blank(cur_l7) and not _blank(out_l7):
                summary["filled_l7"] += 1

    written = summary["filled_l6"] + summary["filled_l7"] + summary["inserted"]
    summary["noop"] = written == 0
    summary["total_na"] = int(
        session.execute(text("SELECT COUNT(*) FROM dim_gl_na")).scalar() or 0
    )
    logger.info(
        "na_fill: dim_gl_na filled (filled_l7=%d, filled_l6=%d, inserted=%d, total=%d)%s",
        summary["filled_l7"], summary["filled_l6"], summary["inserted"],
        summary["total_na"], " [no-op]" if summary["noop"] else "",
    )
    return summary
