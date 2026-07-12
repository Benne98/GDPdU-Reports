r"""Populate ``dim_gl_cf`` from the CF mapping library (``lib_cf_mapping``).

Fills one ``dim_gl_cf`` row per (account_number_group, fiscal_year) by joining the
account's classification to the library:

  BS / Net-asset accounts:
      dim_gl_na (l6_na_mapping, l7_na_description)
        ⋈ lib_cf_mapping (key_kind='na', key_1=l6_na_mapping,
                              key_2=l7_na_description)

  P&L accounts:
      dim_gl_account (level_0='PL', level_3)
        ⋈ lib_cf_mapping (key_kind='pl_level3', key_1='PL', key_2=level_3)

Writes l1..l5 + cf_mapping (the CF builder/SQL filter out cf_mapping
'Exclude'/'Exlude'/NULL).  Idempotent UPSERT keyed (account_number_group,
fiscal_year) — re-running reproduces identical rows.

Run it identically on BOTH databases (finssentials_v2 AND Finssentials/live) so
the CF works on both and the golden ``compare live v2`` stays EQUIVALENT — the
populated reference data is the same on both.

USAGE
-----
  $env:DB_PASSWORD='...'; $env:DB_NAME='finssentials_v2'
  backend\.venv\Scripts\python.exe backend\scripts\populate_dim_gl_cf.py
  # report unmatched only, write nothing:
  ... populate_dim_gl_cf.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
_REPO = _BACKEND.parent
for _p in (str(_BACKEND), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sqlalchemy import text
from sqlalchemy.orm import Session as SASession

from app.db import engine

# SINGLE SOURCE OF TRUTH for the UPSERTs lives in the ETL module so the rebuild
# stage (etl.cf_fill.populate_dim_gl_cf) and this standalone script cannot drift.
from etl.cf_fill import _NA_UPSERT, _PL_UPSERT, _PL_UPSERT_L2

# Unmatched diagnostics.
_NA_UNMATCHED = """
SELECT na.l6_na_mapping, na.l7_na_description, COUNT(*) AS n
FROM dim_gl_na na
LEFT JOIN lib_cf_mapping lib
  ON lib.key_kind = 'na' AND lib.key_1 = na.l6_na_mapping
 AND lib.key_2 = na.l7_na_description
WHERE lib.key_1 IS NULL
GROUP BY 1, 2 ORDER BY n DESC;
"""

# Unmatched = a P&L account whose coarse category is at NEITHER level_3 (pass 1) NOR
# level_2 (pass 2 fallback) — i.e. genuinely uncovered by the library (some intentionally
# excluded, e.g. 'Other taxes').  An account covered via the level_2 fallback is NOT
# unmatched, so we exclude both matches.
_PL_UNMATCHED = """
SELECT a.level_2, a.level_3, COUNT(*) AS n
FROM dim_gl_account a
WHERE a.level_0 = 'PL'
  AND NOT EXISTS (SELECT 1 FROM lib_cf_mapping lib
                  WHERE lib.key_kind = 'pl_level3' AND lib.key_1 = 'PL' AND lib.key_2 = a.level_3)
  AND NOT EXISTS (SELECT 1 FROM lib_cf_mapping lib
                  WHERE lib.key_kind = 'pl_level3' AND lib.key_1 = 'PL' AND lib.key_2 = a.level_2)
GROUP BY 1, 2 ORDER BY n DESC;
"""


def _report_unmatched(session: SASession) -> tuple[int, int]:
    na_un = session.execute(text(_NA_UNMATCHED)).fetchall()
    pl_un = session.execute(text(_PL_UNMATCHED)).fetchall()
    if na_un:
        print(f"[unmatched NA] {len(na_un)} (NA, NA Description) classification(s) with NO library row "
              f"— extend lib_cf_mapping to cover these:")
        for r in na_un:
            print(f"    {r[0]!r} | {r[1]!r}  ({r[2]} accounts)")
    else:
        print("[unmatched NA] none — every dim_gl_na classification is covered.")
    if pl_un:
        print(f"[unmatched PL] {len(pl_un)} P&L (level_2, level_3) pair(s) with NO library row "
              f"at EITHER level (some are intentionally excluded, e.g. 'Other taxes'):")
        for r in pl_un:
            print(f"    level_2={r[0]!r} | level_3={r[1]!r}  ({r[2]} accounts)")
    else:
        print("[unmatched PL] none.")
    return sum(int(r[2]) for r in na_un), sum(int(r[2]) for r in pl_un)


def main() -> int:
    parser = argparse.ArgumentParser(description="Populate dim_gl_cf from lib_cf_mapping.")
    parser.add_argument("--dry-run", action="store_true",
                        help="report match/unmatched counts only; write nothing")
    args = parser.parse_args()

    with SASession(engine) as session:
        lib_n = session.execute(text("SELECT COUNT(*) FROM lib_cf_mapping")).scalar() or 0
        if lib_n == 0:
            print("ERROR: lib_cf_mapping is empty — run load_lib_cf_mapping.py first.")
            return 2
        before = session.execute(text("SELECT COUNT(*) FROM dim_gl_cf")).scalar() or 0
        print(f"lib_cf_mapping rows: {lib_n}   dim_gl_cf rows before: {before}")

        if args.dry_run:
            _report_unmatched(session)
            print("[dry-run] no writes performed.")
            return 0

        session.execute(text(_NA_UPSERT))
        session.execute(text(_PL_UPSERT))     # P&L level_3 pass (authoritative)
        session.execute(text(_PL_UPSERT_L2))  # P&L level_2 fallback (CoA level-shift)
        session.commit()

        after = session.execute(text("SELECT COUNT(*) FROM dim_gl_cf")).scalar() or 0
        # cf_mapping leaf distribution (sanity: EBITDA / Taxes on income present).
        print(f"dim_gl_cf rows after: {after}  (+{after - before})")
        print("--- dim_gl_cf cf_mapping leaf distribution ---")
        for r in session.execute(text(
            "SELECT cf_mapping, COUNT(*) FROM dim_gl_cf GROUP BY 1 ORDER BY 2 DESC"
        )).fetchall():
            print(f"    {r[0]!r:45} {r[1]}")
        na_unmatched, pl_unmatched = _report_unmatched(session)
        print(f"\nSUMMARY: dim_gl_cf={after} rows; unmatched accounts: NA={na_unmatched}, PL={pl_unmatched}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
