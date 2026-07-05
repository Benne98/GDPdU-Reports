"""Fill MISSING (account, fiscal_year) dim_gl_account rows from the account library.

PROBLEM
-------
``dim_gl_account`` is keyed ``(account_number_group, fiscal_year)`` and populated
from the project's mapping file.  If the mapping file only covers FY ``a..b``,
the same account in OTHER years gets no ``dim_gl_account`` row → it is
unclassified → reports miss it (and, because ``fact_gl_line`` has an FK to
``dim_gl_account``, the load path's coverage filter would even DROP those GL
lines).

THE FILL (LIBRARY MODE)
-----------------------
For every ``(account_number_group, fiscal_year)`` that has GL postings (appears in
``fact_gl_line``) but NO ``dim_gl_account`` row, synthesize one:

    ovr_account_mapping[(account, fy)]                       if a pin exists  [PRECEDENCE]
    else resolve_account_most_frequent(lib_account_mapping by account_name)   [LIBRARY]

The ``account_name`` for resolution comes from ANY existing ``dim_gl_account`` row
for the SAME ``account_number_group`` (other years).  Sets level_0..4 + l4_sub +
level_2_sort/level_3_sort + is_ic + gl_account_id + source_system.

SAFETY — ADDITIVE, NEVER MUTATES
--------------------------------
This ONLY INSERTs rows for (account, year) combinations that have NO existing
``dim_gl_account`` row.  It NEVER updates or deletes an existing row.  Therefore:

  * On v2/live, whose mapping already covers every year, the "missing" set is
    EMPTY (the FK guarantees every posted (account, fy) already has a dim row) →
    the fill inserts 0 rows → existing reports are byte-identical → the golden
    ``compare live v2`` stays EQUIVALENT.
  * It is idempotent: a second run finds nothing missing (the first run filled
    them) → 0 inserts.

EXCLUSIVE MODE
--------------
``account_mapping_mode = 'exclusive'`` (per-project config) SKIPS the library
fill entirely — only the provided per-(account, year) mapping is used; missing
years stay unmapped.  The gate is enforced by the caller (``rebuild.py`` /
``populate_dim_gl_account_fill.py``) which checks the flag and does not call
``fill_missing_account_rows`` when exclusive.

This module is DB-side but dialect-portable (Postgres + SQLite) so the resolver
fill is unit-testable on in-memory SQLite (see
``backend/tests/test_account_mapping_library.py``).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from etl.mapping_library.resolve import resolve_account_most_frequent

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# DB loaders
# --------------------------------------------------------------------------- #
def _load_library(session: Session) -> dict[str, list]:
    """account_name -> [AccountRow-compatible tuple, ...] from lib_account_mapping."""
    rows = session.execute(text(
        """
        SELECT account_name, level_0, level_1, level_2, level_3, level_4, l4_sub,
               level_2_sort, level_3_sort, is_ic, occurrences
        FROM lib_account_mapping
        """
    )).fetchall()
    by_name: dict[str, list] = defaultdict(list)
    for r in rows:
        by_name[r.account_name].append((
            r.level_0, r.level_1, r.level_2, r.level_3, r.level_4, r.l4_sub,
            r.level_2_sort, r.level_3_sort, bool(r.is_ic), int(r.occurrences or 0),
        ))
    return by_name


def _load_overrides(session: Session) -> dict[tuple[str, int], dict]:
    rows = session.execute(text(
        """
        SELECT account_number_group, fiscal_year, level_0, level_1, level_2,
               level_3, level_4, l4_sub, level_2_sort, level_3_sort, is_ic
        FROM ovr_account_mapping
        """
    )).fetchall()
    out: dict[tuple[str, int], dict] = {}
    for r in rows:
        out[(r.account_number_group, int(r.fiscal_year))] = {
            "level_0": r.level_0, "level_1": r.level_1, "level_2": r.level_2,
            "level_3": r.level_3, "level_4": r.level_4, "l4_sub": r.l4_sub,
            "level_2_sort": r.level_2_sort, "level_3_sort": r.level_3_sort,
            "is_ic": bool(r.is_ic),
        }
    return out


def _account_name_and_gl_id(session: Session) -> dict[str, tuple]:
    """account_number_group -> (account_name, gl_account_id) from ANY existing dim row.

    The hierarchy is name-keyed, so a missing-year row borrows the account_name of
    the SAME business account from a year that WAS mapped.  Deterministic: take the
    latest fiscal_year's values (MAX(fiscal_year)) so the choice is stable.
    """
    rows = session.execute(text(
        """
        SELECT account_number_group, account_name, gl_account_id, fiscal_year
        FROM dim_gl_account
        WHERE account_name IS NOT NULL AND TRIM(account_name) <> ''
        ORDER BY account_number_group, fiscal_year
        """
    )).fetchall()
    out: dict[str, tuple] = {}
    for r in rows:
        # later fiscal_year overwrites -> ends on the latest (rows are fy-ascending)
        out[r.account_number_group] = (r.account_name, r.gl_account_id)
    return out


def _missing_posted_keys(session: Session, scope=None) -> list[tuple[str, int]]:
    """(account_number_group, fiscal_year) posted in fact_gl_line with NO dim row.

    On v2/live this is EMPTY (the FK fact_gl_line -> dim_gl_account guarantees every
    posted key already has a dim row), so the fill is a no-op there.
    """
    sql = """
        SELECT DISTINCT l.account_number_group, l.fiscal_year
        FROM fact_gl_line l
        LEFT JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year          = l.fiscal_year
        WHERE a.account_number_group IS NULL
    """
    rows = session.execute(text(sql)).fetchall()
    keys = [(r.account_number_group, int(r.fiscal_year)) for r in rows]
    if scope is not None:
        prefixes = set(getattr(scope, "prefixes", []) or [])
        years = set(int(y) for y in (getattr(scope, "years", []) or []))
        if prefixes:
            keys = [(a, fy) for (a, fy) in keys if a[:2] in prefixes]
        if years:
            keys = [(a, fy) for (a, fy) in keys if fy in years]
    return keys


_INSERT = """
INSERT INTO dim_gl_account
  (account_number_group, fiscal_year, gl_account_id, account_name,
   level_0, level_1, level_2, level_3, level_4, l4_sub,
   level_2_sort, level_3_sort, is_ic, source_system)
VALUES (:ang, :fy, :gid, :nm, :l0, :l1, :l2, :l3, :l4, :l4s,
        :s2, :s3, :ic, :ss)
"""


def _derive_gl_id(account_number_group: str, existing_gl_id: Optional[str]) -> str:
    """gl_account_id for a filled row: reuse the business account's existing id,
    else fall back to the bare account number (the 8-char group minus the 2-char
    entity prefix, leading zeros stripped — the GL-side convention)."""
    if existing_gl_id:
        return str(existing_gl_id)
    tail = (account_number_group or "")[2:]
    return tail.lstrip("0") or tail or account_number_group


def _all_dim_keys(session: Session) -> set[tuple[str, int]]:
    """Every (account_number_group, fiscal_year) that ALREADY has a dim_gl_account row.

    Used to keep the fill strictly additive/idempotent for ANY caller: a key that
    already has a dim row is filtered out before the loop, so the fill never tries
    to INSERT a duplicate (and re-running is a no-op).
    """
    rows = session.execute(text(
        "SELECT account_number_group, fiscal_year FROM dim_gl_account"
    )).fetchall()
    return {(str(r[0]), int(r[1])) for r in rows if r[1] is not None}


def fill_account_rows_for_keys(
    session: Session,
    keys,
    *,
    source_system: str = "account_library_fill",
    dry_run: bool = False,
) -> dict:
    """INSERT a dim_gl_account row for each requested (account_number_group,
    fiscal_year) key that has NO existing row, resolving the classification per key.

    ``keys`` is any iterable of ``(account_number_group, fiscal_year)`` tuples.
    Keys that already have a dim_gl_account row are filtered out first (additive,
    idempotent — never an UPDATE or DELETE, never a duplicate INSERT).

    Resolution per key:
      1. ovr_account_mapping pin (precedence), else
      2. resolve_account_most_frequent(lib_account_mapping[account_name]).

    The ``account_name`` is borrowed from ANY existing dim_gl_account row for the
    SAME ``account_number_group`` (other years).  A key that can be resolved neither
    way is left UNRESOLVED and reported (never guessed).

    Returns a summary dict::

        {
          "missing_keys": int,              # keys that needed a fill (post-filter)
          "filled": int,                    # rows inserted (0 when dry_run)
          "from_override": int,             # resolved via ovr_account_mapping
          "from_library": int,              # resolved via lib_account_mapping
          "skipped_no_name": int,           # count of unresolved_no_name
          "skipped_no_resolution": int,     # count of unresolved_no_resolution
          "unresolved_no_name": [(ang, fy), ...],        # no dim row in ANY year
          "unresolved_no_resolution": [(ang, fy), ...],  # name exists, no precedent
          "dry_run": bool,
        }

    The two unresolved lists let a caller (e.g. the opening-balance commit) name the
    exact accounts it could not classify.
    """
    requested = [(str(ang), int(fy)) for ang, fy in keys]
    summary = {
        "missing_keys": 0, "filled": 0,
        "from_override": 0, "from_library": 0,
        "skipped_no_name": 0, "skipped_no_resolution": 0,
        "unresolved_no_name": [], "unresolved_no_resolution": [],
        "dry_run": dry_run,
    }
    if not requested:
        return summary

    existing = _all_dim_keys(session)
    # Strictly additive: only keys WITHOUT an existing dim row need a fill.
    worklist = [k for k in requested if k not in existing]
    summary["missing_keys"] = len(worklist)
    if not worklist:
        return summary

    library = _load_library(session)
    overrides = _load_overrides(session)
    name_map = _account_name_and_gl_id(session)

    for ang, fy in worklist:
        name_gid = name_map.get(ang)
        if not name_gid:
            # No existing dim row for this business account anywhere → no name to
            # resolve by, and no library precedent → cannot classify; skip+log.
            summary["skipped_no_name"] += 1
            summary["unresolved_no_name"].append((ang, fy))
            logger.warning("account_fill: no account_name for %s (fy %s) — skipped", ang, fy)
            continue
        account_name, existing_gid = name_gid

        ov = overrides.get((ang, fy))
        if ov is not None:
            hier = ov
            summary["from_override"] += 1
        else:
            winner = resolve_account_most_frequent(library.get(account_name, []))
            if winner is None:
                summary["skipped_no_resolution"] += 1
                summary["unresolved_no_resolution"].append((ang, fy))
                logger.warning(
                    "account_fill: no library precedent for name %r (%s fy %s) — skipped",
                    account_name, ang, fy,
                )
                continue
            hier = {
                "level_0": winner.level_0, "level_1": winner.level_1,
                "level_2": winner.level_2, "level_3": winner.level_3,
                "level_4": winner.level_4 or None, "l4_sub": winner.l4_sub or None,
                "level_2_sort": winner.level_2_sort, "level_3_sort": winner.level_3_sort,
                "is_ic": winner.is_ic,
            }
            summary["from_library"] += 1

        if not dry_run:
            session.execute(text(_INSERT), {
                "ang": ang, "fy": fy,
                "gid": _derive_gl_id(ang, existing_gid),
                "nm": account_name,
                "l0": hier["level_0"], "l1": hier["level_1"], "l2": hier["level_2"],
                "l3": hier["level_3"], "l4": hier["level_4"], "l4s": hier["l4_sub"],
                "s2": hier["level_2_sort"], "s3": hier["level_3_sort"],
                "ic": bool(hier["is_ic"]), "ss": source_system,
            })
        summary["filled"] += 1

    return summary


def fill_missing_account_rows(
    session: Session,
    scope=None,
    *,
    source_system: str = "account_library_fill",
    dry_run: bool = False,
) -> dict:
    """INSERT a dim_gl_account row for every posted (account, fy) that has none.

    Thin wrapper: derives the worklist from ``_missing_posted_keys`` (keys posted in
    fact_gl_line with no dim row) and delegates to ``fill_account_rows_for_keys``, so
    the posted-GL path is unchanged.  NEVER updates/deletes an existing row (additive
    only).  Pass ``dry_run=True`` to compute the plan without writing.
    """
    missing = _missing_posted_keys(session, scope)
    return fill_account_rows_for_keys(
        session, missing, source_system=source_system, dry_run=dry_run,
    )
