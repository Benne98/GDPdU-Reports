"""Re-derive (realign) the P&L report structure's grain filters from the live CoA.

WHY (v5-pipeline, Prong A)
--------------------------
``dim_pl_structure`` mapping rows are seeded ONCE from a curated Excel
(``seed_pl_structure.py`` hardcodes ``level_3 = balance_title``) and were NEVER
re-derived on a rebuild.  The P&L reader (``fin_compat_pl._match_grain``) matches a
structure row to a GL grain by comparing the row's pinned ``level_2/level_3/level_4``
filter against the grain's levels at the SAME slot.  After a Chart-of-Accounts
export → re-ingest round-trip the GL hierarchy can shift UP by one level (a category
that used to sit at grain ``level_3`` now appears at grain ``level_2``).  The stale
``level_3`` filter then matches nothing → the position renders 0.

Only the P&L is affected: the BS structure is already re-derived from the live GL on
every rebuild (``seed_bs_structure``) and the CF structure uses no level filters.

THE DERIVATION RULE (the seam, shared verbatim with the reader-side)
--------------------------------------------------------------------
Re-pin each eligible P&L mapping row's filter to the **SHALLOWEST occupied grain
level** at which its category value currently appears in ``dim_gl_account``
(``level_0='PL'``), clearing the other two level filters.  ``level_2`` is shallower than
``level_3`` is shallower than ``level_4``.  The reader then treats that pinned level as
the position level and builds children from the next populated grain level down.

Seam invariant: after a successful re-pin **exactly one** of the three filter columns
is set (the shallowest occupied level) and the other two are NULL.  Because a re-pinned
row carries a single filter, "shallowest occupied level" (this writer) and the reader's
"deepest populated level" coincide on that row — there is no contradiction: both name
the one and only occupied slot.  A row that would need two filters set is never re-pinned
(see the multi-value collision case below); it is left unchanged rather than mis-pinned.

Formula (per eligible row, per current non-null filter value ``v``):

    appears(v)  = { grain level in {level_2,level_3,level_4} : v is a DISTINCT value
                    of that column in dim_gl_account WHERE level_0='PL' [∩ scope.years] }
    target(v)   = argmin_level  appears(v)            (level_2 < level_3 < level_4)
    new_filter  = { target(v): v }  (the other two slots → NULL)

Eligibility: ``row_type='mapping'`` AND ``gl_account_id IS NULL`` AND
``source IN (NULL,'seed')`` (``source='auto_extend'`` rows and every non-mapping row
— subtotal / calc / grandtotal / KPI / title, all with NULL filters — are untouched).

Worked example
--------------
Row ``COST_OF_MATERIALS`` seeded with ``level_3='Cost of materials'``.
* v5 CoA: 'Cost of materials' is a DISTINCT ``level_3`` value → target=level_3 →
  new filter == current filter → **NO-OP** (parity: verified 0 rows change on v5).
* e2e CoA (post round-trip): 'Cost of materials' is a DISTINCT ``level_2`` value →
  target=level_2 → row re-pinned to ``level_2='Cost of materials'``, level_3/4 cleared
  → the reader matches again (verified 14 seed rows re-pin on e2e).

Edge cases (mirrored in the tests, per docs/financial-logic.md)
---------------------------------------------------------------
* Value at MULTIPLE grain levels (e.g. same name at level_3 AND level_4) → pick the
  SHALLOWEST; a warning names the value + levels.  On v5 this is the benign L3/L4
  same-name case (shallowest=level_3 == current filter → no-op); confirmed 8 such
  values on v5.
* Value ABSENT from the uploaded CoA → leave the row's filters UNCHANGED (an honest
  empty position that renders 0); the curated row is NEVER deleted.
* Value expressible only at grain ``level_1`` (no ``level_1`` slot in
  ``dim_pl_structure``) → leave unchanged, warn.
* ``source='auto_extend'`` rows and all non-mapping rows → untouched.

Only the three filter columns (``level_2``/``level_3``/``level_4``) are ever written;
``sort_order`` / ``line_code`` / ``row_type`` / ``kpi_code`` / ``invert_delta`` /
``is_bold`` are NEVER touched.

Standalone usage (env DB_NAME / DB_PASSWORD / DB_USER / DB_HOST):
    python backend/scripts/realign_pl_structure.py            # apply + commit
    python backend/scripts/realign_pl_structure.py --dry-run  # count only, no write
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

# Only these three grain levels have a slot in dim_pl_structure; shallowest first.
_LEVEL_ORDER: dict[str, int] = {"level_2": 2, "level_3": 3, "level_4": 4}


def _s(v: Any) -> str:
    return (v or "").strip() if isinstance(v, str) else ("" if v is None else str(v).strip())


# --------------------------------------------------------------------------- #
# Pure derivation
# --------------------------------------------------------------------------- #
def build_value_level_map(coa_rows: Iterable[tuple]) -> dict[str, set[str]]:
    """Map each PL category value → the set of grain levels it appears at.

    ``coa_rows`` is an iterable of ``(level_2, level_3, level_4)`` tuples — the result
    of ``SELECT DISTINCT level_2, level_3, level_4 FROM dim_gl_account
    WHERE level_0='PL'`` (optionally scoped to a set of fiscal years).

    Returns e.g. ``{'Cost of materials': {'level_2'}, 'Net sales': {'level_2','level_3'}}``.
    Blank / NULL values are ignored.
    """
    out: dict[str, set[str]] = {}
    for row in coa_rows:
        l2 = _s(row[0]); l3 = _s(row[1]); l4 = _s(row[2])
        if l2:
            out.setdefault(l2, set()).add("level_2")
        if l3:
            out.setdefault(l3, set()).add("level_3")
        if l4:
            out.setdefault(l4, set()).add("level_4")
    return out


def realign_rows(
    struct_rows: Iterable[dict],
    value_map: dict[str, set[str]],
    level_1_values: Optional[set[str]] = None,
) -> list[dict]:
    """Compute the filter re-pin for every eligible P&L mapping row.

    Returns a list of update descriptors (only for rows whose filters actually change)::

        {"pl_line_id": int|None, "line_code": str, "level_2": v2, "level_3": v3, "level_4": v4}

    Eligible = row_type=='mapping' AND gl_account_id is NULL AND source in (None,'seed').
    A row is left UNCHANGED (no descriptor emitted) when any of its current filter
    values is absent from ``value_map`` (absent CoA value / level_1-only value), or when
    the re-pinned filter equals the current one (no-op).
    """
    level_1_values = level_1_values or set()
    updates: list[dict] = []

    for row in struct_rows:
        if _s(row.get("row_type")) != "mapping":
            continue
        if _s(row.get("gl_account_id")):
            continue
        source = row.get("source")
        if source not in (None, "seed"):
            continue  # source='auto_extend' (and any other provenance) is user-owned

        cur = {
            "level_2": _s(row.get("level_2")) or None,
            "level_3": _s(row.get("level_3")) or None,
            "level_4": _s(row.get("level_4")) or None,
        }
        vals = [v for v in cur.values() if v]
        if not vals:
            continue  # nothing to pin (defensive: a mapping row should carry a filter)

        new = {"level_2": None, "level_3": None, "level_4": None}
        leave_unchanged = False
        for v in vals:
            levels = value_map.get(v)
            if not levels:
                if v in level_1_values:
                    logger.warning(
                        "realign_pl_structure: value %r appears only at grain level_1 "
                        "(no expressible slot in dim_pl_structure); leaving row %r unchanged",
                        v, row.get("line_code"),
                    )
                else:
                    logger.warning(
                        "realign_pl_structure: value %r is absent from the uploaded CoA "
                        "(level_0='PL'); leaving row %r unchanged (honest empty position)",
                        v, row.get("line_code"),
                    )
                leave_unchanged = True
                break
            if len(levels) > 1:
                logger.warning(
                    "realign_pl_structure: value %r appears at multiple grain levels %s; "
                    "pinning the shallowest",
                    v, sorted(levels),
                )
            target = min(levels, key=lambda lv: _LEVEL_ORDER[lv])
            if new[target] is not None:
                # Two source filter values (legacy convention level_2=parent group +
                # level_3=category) resolved to the SAME target grain level. Writing the
                # second would silently overwrite the first and mis-pin the row. Apply the
                # same conservative policy as the absent-value case: never guess — leave
                # the row UNCHANGED and warn, naming the line_code and the colliding values.
                logger.warning(
                    "realign_pl_structure: row %r has two filter values (%r and %r) that "
                    "both resolve to grain %s; cannot pin without loss — leaving row unchanged",
                    row.get("line_code"), new[target], v, target,
                )
                leave_unchanged = True
                break
            new[target] = v

        if leave_unchanged:
            continue
        if (new["level_2"], new["level_3"], new["level_4"]) == (
            cur["level_2"], cur["level_3"], cur["level_4"]
        ):
            continue  # no-op re-pin (v5 parity case)

        updates.append({
            "pl_line_id": row.get("pl_line_id"),
            "line_code": row.get("line_code"),
            "level_2": new["level_2"],
            "level_3": new["level_3"],
            "level_4": new["level_4"],
        })

    return updates


# --------------------------------------------------------------------------- #
# DB wrapper
# --------------------------------------------------------------------------- #
def _has_source_column(session) -> bool:
    from sqlalchemy import text

    row = session.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name='dim_pl_structure' AND column_name='source'"
        )
    ).fetchone()
    return row is not None


def _scope_years(scope) -> list[int]:
    """Extract fiscal years from a RebuildScope / (prefixes, years) tuple / None."""
    if scope is None:
        return []
    years = getattr(scope, "years", None)
    if years is None and isinstance(scope, tuple) and len(scope) == 2:
        years = scope[1]
    return [int(y) for y in (years or [])]


def realign_pl_structure(session, scope=None) -> int:
    """Re-pin eligible P&L structure rows to the shallowest current grain level.

    Applies the UPDATEs in-place (only the three filter columns) and returns the count
    of rows changed.  Idempotent: a second run finds every row already pinned → 0.
    """
    from sqlalchemy import text

    years = _scope_years(scope)

    coa_sql = (
        "SELECT DISTINCT level_2, level_3, level_4 FROM dim_gl_account WHERE level_0='PL'"
    )
    params: dict[str, Any] = {}
    if years:
        coa_sql += " AND fiscal_year = ANY(:years)"
        params["years"] = years
    coa_rows = session.execute(text(coa_sql), params).fetchall()
    value_map = build_value_level_map(coa_rows)

    # level_1 values (PL) — only to phrase the "not expressible" warning precisely.
    l1_sql = "SELECT DISTINCT level_1 FROM dim_gl_account WHERE level_0='PL' AND level_1 IS NOT NULL"
    if years:
        l1_sql += " AND fiscal_year = ANY(:years)"
    level_1_values = {_s(r[0]) for r in session.execute(text(l1_sql), params).fetchall() if _s(r[0])}

    has_source = _has_source_column(session)
    src_col = "source" if has_source else "NULL AS source"
    struct_rows = [
        dict(r._mapping)
        for r in session.execute(
            text(
                f"SELECT pl_line_id, line_code, row_type, level_2, level_3, level_4, "
                f"gl_account_id, {src_col} FROM dim_pl_structure ORDER BY sort_order"
            )
        ).fetchall()
    ]

    updates = realign_rows(struct_rows, value_map, level_1_values)

    for u in updates:
        params = {"l2": u["level_2"], "l3": u["level_3"], "l4": u["level_4"]}
        if u.get("pl_line_id") is not None:
            where = "pl_line_id = :id"
            params["id"] = u["pl_line_id"]
        else:
            # A NULL primary key would never match `pl_line_id = NULL` in Postgres, so the
            # UPDATE would silently no-op and leave a stale grain filter. Fall back to the
            # unique line_code (carried on every descriptor) to guarantee the row is written.
            where = "line_code = :lc"
            params["lc"] = u["line_code"]
        session.execute(
            text(
                f"UPDATE dim_pl_structure SET level_2 = :l2, level_3 = :l3, level_4 = :l4 "
                f"WHERE {where}"
            ),
            params,
        )

    logger.info(
        "realign_pl_structure: %d P&L structure row(s) re-pinned (scope years=%s)",
        len(updates), years or "all",
    )
    return len(updates)


# --------------------------------------------------------------------------- #
# Standalone entry point
# --------------------------------------------------------------------------- #
def _main() -> None:
    import os
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    dry_run = "--dry-run" in sys.argv[1:]

    db = os.getenv("DB_NAME", "").strip()
    if not db:
        db = "finssentials_v5"
        print(
            f"[WARNING] DB_NAME not set — defaulting to {db!r}. "
            "Set DB_NAME explicitly to avoid a silent mis-apply to the wrong database.",
            file=sys.stderr,
        )
    user = os.getenv("DB_USER", "postgres")
    pw = os.getenv("DB_PASSWORD", "")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as SASession

    url = f"postgresql+psycopg2://{user}:{pw}@{host}:{port}/{db}"
    engine = create_engine(url)
    with SASession(engine) as session:
        if dry_run:
            # Compute without persisting: run the derivation, then roll back.
            n = realign_pl_structure(session, scope=None)
            session.rollback()
            print(f"realign_pl_structure [DRY-RUN] on {db}: {n} row(s) would change (rolled back)")
        else:
            n = realign_pl_structure(session, scope=None)
            session.commit()
            print(f"realign_pl_structure on {db}: {n} row(s) re-pinned (committed)")


if __name__ == "__main__":
    _main()
