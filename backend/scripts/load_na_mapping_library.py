r"""Seed / extend the **NA mapping library** (``lib_na_mapping``).

The library is the shared, accumulating reference the populate step
(``populate_dim_gl_na.py``) resolves to fill ``dim_gl_na`` for any classified
project.  It is keyed by the **account name** (``dim_gl_account.account_name``),
NOT the per-entity GL number, because the Net-asset / Working-capital
classification is really a property of the business account: the same name
recurs across legal entities and fiscal years.

WHAT THIS LOADS
---------------
One row per OBSERVED ``(account_name, na_mapping, na_description)`` with a COUNT
of how many (account_number_group, fiscal_year) carry that exact combination —
straight from the CURRENT, already-classified data:

    SELECT a.account_name, na.l6_na_mapping, na.l7_na_description, COUNT(*)
    FROM dim_gl_na na
    JOIN dim_gl_account a USING (account_number_group, fiscal_year)
    GROUP BY 1, 2, 3

The duplicates / counts are the WHOLE POINT — they are the frequency the
resolver (``etl.mapping_library.resolve.resolve_most_frequent``) uses to pick the
most-frequent mapping per name, with a deterministic tiebreaker for 50/50 splits.

Idempotent: the UPSERT sets ``occurrences`` to the freshly-counted value (not an
increment), so re-running on the same data reproduces identical rows.  Re-running
after a data reload re-counts from the current ``dim_gl_na`` — the library always
reflects the live classification.

ADDING / EXTENDING FOR FUTURE PROJECTS
--------------------------------------
* The library is project-agnostic and keyed by account NAME, so a new project
  whose ``dim_gl_account.account_name`` values match accumulates more
  ``occurrences`` for the SAME names (run this loader after loading the new
  project's mapping) — the most-frequent mapping reflects the whole population.
* A name seen ONLY in a new project (no precedent) lands as a fresh single-row
  entry → its sole mapping wins trivially.
* To force a classification for a SPECIFIC account/year regardless of frequency,
  write a row to ``ovr_na_mapping`` (the Project-Setup UI / the populate step's
  totals-guard do this) — overrides take precedence over the library.
* Source provenance is recorded in ``source`` (default ``'seed'``); pass
  ``--source`` to tag a particular load (e.g. the project id).

USAGE
-----
  $env:DB_PASSWORD='...'; $env:DB_NAME='finssentials_v2'
  backend\.venv\Scripts\python.exe backend\scripts\load_na_mapping_library.py
  # report what WOULD be loaded, write nothing:
  ... load_na_mapping_library.py --dry-run
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

# Source counts straight from the current classification.
_COUNT_SQL = """
SELECT a.account_name        AS account_name,
       na.l6_na_mapping      AS na_mapping,
       na.l7_na_description  AS na_description,
       COUNT(*)              AS occurrences
FROM dim_gl_na na
JOIN dim_gl_account a
  ON a.account_number_group = na.account_number_group
 AND a.fiscal_year          = na.fiscal_year
WHERE a.account_name IS NOT NULL AND TRIM(a.account_name) <> ''
  AND na.l6_na_mapping IS NOT NULL
  AND na.l7_na_description IS NOT NULL
GROUP BY a.account_name, na.l6_na_mapping, na.l7_na_description
"""

_UPSERT = """
INSERT INTO lib_na_mapping
  (account_name, na_mapping, na_description, occurrences, source, updated_at)
VALUES (:nm, :map, :desc, :occ, :src, NOW())
ON CONFLICT (account_name, na_mapping, na_description) DO UPDATE SET
  occurrences = EXCLUDED.occurrences,
  source      = EXCLUDED.source,
  updated_at  = NOW();
"""


def load_library(session: SASession, source: str) -> dict:
    rows = session.execute(text(_COUNT_SQL)).fetchall()
    names: set[str] = set()
    for r in rows:
        names.add(r.account_name)
        session.execute(
            text(_UPSERT),
            {"nm": r.account_name, "map": r.na_mapping, "desc": r.na_description,
             "occ": int(r.occurrences), "src": source},
        )
    ambiguous = _count_ambiguous(rows)
    return {"rows": len(rows), "distinct_names": len(names), "ambiguous": ambiguous}


def _count_ambiguous(rows) -> int:
    """How many distinct account_names carry >1 (mapping, description) combination."""
    from collections import defaultdict
    by_name: dict[str, int] = defaultdict(int)
    for r in rows:
        by_name[r.account_name] += 1
    return sum(1 for n in by_name.values() if n > 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed/extend lib_na_mapping from current dim_gl_na.")
    parser.add_argument("--source", default="seed", help="provenance tag stored in lib_na_mapping.source")
    parser.add_argument("--dry-run", action="store_true", help="report counts only; write nothing")
    args = parser.parse_args()

    with SASession(engine) as session:
        rows = session.execute(text(_COUNT_SQL)).fetchall()
        ambiguous = _count_ambiguous(rows)
        distinct = len({r.account_name for r in rows})
        print(f"source data: {len(rows)} (name, mapping, description) combinations; "
              f"{distinct} distinct account_names; {ambiguous} ambiguous (>1 mapping).")

        if args.dry_run:
            print("[dry-run] no writes performed.")
            return 0

        res = load_library(session, args.source)
        session.commit()
        total = session.execute(text("SELECT COUNT(*) FROM lib_na_mapping")).scalar()

    print(f"[lib_na_mapping] {res['rows']} rows upserted "
          f"({res['distinct_names']} distinct names, {res['ambiguous']} ambiguous).")
    print(f"lib_na_mapping now holds {total} rows total.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
