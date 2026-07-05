r"""Seed / extend the **account (chart-of-accounts) mapping library**
(``lib_account_mapping``).

The library is the shared, accumulating reference the fill step
(``populate_dim_gl_account_fill.py``) resolves to classify accounts in fiscal
years a project's mapping file never covered.  It is keyed by the **account
name** (``dim_gl_account.account_name``), NOT the per-entity GL number, because
the financial-statement hierarchy is really a property of the business account:
the same name recurs across legal entities and fiscal years.

WHAT THIS LOADS
---------------
One row per OBSERVED ``(account_name, level_0, level_1, level_2, level_3,
level_4, l4_sub, level_2_sort, level_3_sort, is_ic)`` with a COUNT of how many
(account_number_group, fiscal_year) carry that exact hierarchy — straight from
the CURRENT, already-classified ``dim_gl_account``:

    SELECT account_name, level_0..4, l4_sub, level_2_sort, level_3_sort, is_ic,
           COUNT(*)
    FROM dim_gl_account
    GROUP BY 1..10

The duplicates / counts are the WHOLE POINT — they are the frequency the resolver
(``etl.mapping_library.resolve.resolve_account_most_frequent``) uses to pick the
most-frequent hierarchy per name, with a deterministic tiebreaker for ties.  The
PK is the level_0..4 spine ``(account_name, level_0, level_2, level_3,
level_4)``; level_1 / l4_sub / sorts / is_ic ride along with the winning spine
(the GROUP BY includes them so a name's dominant spine carries its own metadata).

Idempotent: the UPSERT sets ``occurrences`` to the freshly-counted value (not an
increment), so re-running on the same data reproduces identical rows.  Re-running
after a data reload re-counts from the current ``dim_gl_account`` — the library
always reflects the live classification.

ADDING / EXTENDING FOR FUTURE PROJECTS
--------------------------------------
* The library is project-agnostic and keyed by account NAME, so a new project
  whose ``dim_gl_account.account_name`` values match accumulates more
  ``occurrences`` for the SAME names (run this loader after loading the new
  project's mapping) — the most-frequent hierarchy reflects the whole population.
* A name seen ONLY in a new project (no precedent) lands as a fresh single-row
  entry → its sole hierarchy wins trivially.
* To force a classification for a SPECIFIC account/year regardless of frequency,
  write a row to ``ovr_account_mapping`` (the future Project-Setup UI does this) —
  overrides take precedence over the library in the fill step.
* Source provenance is recorded in ``source`` (default ``'seed'``); pass
  ``--source`` to tag a particular load (e.g. the project id).

USAGE
-----
  $env:DB_PASSWORD='...'; $env:DB_NAME='finssentials_v2'
  backend\.venv\Scripts\python.exe backend\scripts\load_account_mapping_library.py
  # report what WOULD be loaded, write nothing:
  ... load_account_mapping_library.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from sqlalchemy import text
from sqlalchemy.orm import Session as SASession

from app.db import engine

# Source counts straight from the current classification.  Only rows with a usable
# account_name participate (the library is name-keyed); level_0..3 are NOT NULL on
# dim_gl_account so the spine is always present.
_COUNT_SQL = """
SELECT a.account_name                       AS account_name,
       COALESCE(a.level_0, '')              AS level_0,
       COALESCE(a.level_1, '')              AS level_1,
       COALESCE(a.level_2, '')              AS level_2,
       COALESCE(a.level_3, '')              AS level_3,
       COALESCE(a.level_4, '')              AS level_4,
       a.l4_sub                             AS l4_sub,
       a.level_2_sort                       AS level_2_sort,
       a.level_3_sort                       AS level_3_sort,
       COALESCE(a.is_ic, FALSE)             AS is_ic,
       COUNT(*)                             AS occurrences
FROM dim_gl_account a
WHERE a.account_name IS NOT NULL AND TRIM(a.account_name) <> ''
GROUP BY a.account_name, COALESCE(a.level_0, ''), COALESCE(a.level_1, ''),
         COALESCE(a.level_2, ''), COALESCE(a.level_3, ''), COALESCE(a.level_4, ''),
         a.l4_sub, a.level_2_sort, a.level_3_sort, COALESCE(a.is_ic, FALSE)
"""

_UPSERT = """
INSERT INTO lib_account_mapping
  (account_name, level_0, level_1, level_2, level_3, level_4, l4_sub,
   level_2_sort, level_3_sort, is_ic, occurrences, source, updated_at)
VALUES (:nm, :l0, :l1, :l2, :l3, :l4, :l4s, :s2, :s3, :ic, :occ, :src, NOW())
ON CONFLICT (account_name, level_0, level_2, level_3, level_4) DO UPDATE SET
  level_1      = EXCLUDED.level_1,
  l4_sub       = EXCLUDED.l4_sub,
  level_2_sort = EXCLUDED.level_2_sort,
  level_3_sort = EXCLUDED.level_3_sort,
  is_ic        = EXCLUDED.is_ic,
  occurrences  = EXCLUDED.occurrences,
  source       = EXCLUDED.source,
  updated_at   = NOW();
"""


def load_library(session: SASession, source: str) -> dict:
    rows = session.execute(text(_COUNT_SQL)).fetchall()
    names: set[str] = set()
    # The PK collapses on the level_0..4 spine; if two GROUP-BY rows for the same
    # name share a spine but differ on l4_sub/sort/is_ic, the later wins the UPSERT
    # (rare; the spine is the classification identity).  Sum their occurrences so
    # the surviving row carries the full frequency for that spine.
    from collections import defaultdict
    occ_by_pk: dict[tuple, int] = defaultdict(int)
    meta_by_pk: dict[tuple, dict] = {}
    for r in rows:
        names.add(r.account_name)
        pk = (r.account_name, r.level_0, r.level_2, r.level_3, r.level_4)
        occ_by_pk[pk] += int(r.occurrences)
        # keep the metadata of the highest-occurrence variant for this spine
        if pk not in meta_by_pk or int(r.occurrences) >= meta_by_pk[pk]["_occ"]:
            meta_by_pk[pk] = {
                "l1": r.level_1, "l4s": r.l4_sub,
                "s2": r.level_2_sort, "s3": r.level_3_sort,
                "ic": bool(r.is_ic), "_occ": int(r.occurrences),
            }

    for pk, occ in occ_by_pk.items():
        nm, l0, l2, l3, l4 = pk
        m = meta_by_pk[pk]
        session.execute(
            text(_UPSERT),
            {"nm": nm, "l0": l0, "l1": m["l1"], "l2": l2, "l3": l3, "l4": l4,
             "l4s": m["l4s"], "s2": m["s2"], "s3": m["s3"], "ic": m["ic"],
             "occ": occ, "src": source},
        )

    ambiguous = _count_ambiguous(occ_by_pk.keys())
    return {"rows": len(occ_by_pk), "distinct_names": len(names), "ambiguous": ambiguous}


def _count_ambiguous(pks) -> int:
    """How many distinct account_names carry >1 distinct level_0..4 spine."""
    from collections import defaultdict
    by_name: dict[str, int] = defaultdict(int)
    for pk in pks:
        by_name[pk[0]] += 1
    return sum(1 for n in by_name.values() if n > 1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed/extend lib_account_mapping from current dim_gl_account."
    )
    parser.add_argument("--source", default="seed",
                        help="provenance tag stored in lib_account_mapping.source")
    parser.add_argument("--dry-run", action="store_true",
                        help="report counts only; write nothing")
    args = parser.parse_args()

    with SASession(engine) as session:
        rows = session.execute(text(_COUNT_SQL)).fetchall()
        from collections import defaultdict
        spines: dict[str, set] = defaultdict(set)
        for r in rows:
            spines[r.account_name].add((r.level_0, r.level_2, r.level_3, r.level_4))
        distinct = len(spines)
        ambiguous = sum(1 for s in spines.values() if len(s) > 1)
        print(f"source data: {len(rows)} grouped hierarchies; {distinct} distinct "
              f"account_names; {ambiguous} ambiguous (>1 spine).")

        if args.dry_run:
            print("[dry-run] no writes performed.")
            return 0

        res = load_library(session, args.source)
        session.commit()
        total = session.execute(text("SELECT COUNT(*) FROM lib_account_mapping")).scalar()

    print(f"[lib_account_mapping] {res['rows']} rows upserted "
          f"({res['distinct_names']} distinct names, {res['ambiguous']} ambiguous).")
    print(f"lib_account_mapping now holds {total} rows total.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
