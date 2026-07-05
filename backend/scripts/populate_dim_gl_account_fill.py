r"""Fill MISSING (account, fiscal_year) ``dim_gl_account`` rows from the account
mapping library — standalone runner around ``etl.account_fill``.

For every ``(account_number_group, fiscal_year)`` that appears in ``fact_gl_line``
but has NO ``dim_gl_account`` row, this INSERTs a row resolved from:

    ovr_account_mapping[(account, fy)]                       if a pin exists  [PRECEDENCE]
    else resolve_account_most_frequent(lib_account_mapping by account_name)   [LIBRARY]

It NEVER updates or deletes an existing ``dim_gl_account`` row (additive only), so
on v2/live — whose mapping already covers every year (and where the FK
``fact_gl_line -> dim_gl_account`` guarantees no posted key is unmapped) — it fills
0 rows and the golden ``compare live v2`` stays EQUIVALENT.

EXCLUSIVE MODE
--------------
The per-project flag ``admin_project_config.config -> account_mapping_mode``
(``'library'`` default | ``'exclusive'``) gates the fill.  When ``'exclusive'``
the fill is SKIPPED (only the provided per-(account, year) mapping is used).  Pass
``--project-id`` to resolve the flag from config; ``--force-library`` /
``--exclusive`` override the flag explicitly.

USAGE
-----
  $env:DB_PASSWORD='...'; $env:DB_NAME='finssentials_v2'
  backend\.venv\Scripts\python.exe backend\scripts\populate_dim_gl_account_fill.py
  # resolve the mode from a project's config:
  ... populate_dim_gl_account_fill.py --project-id default
  # preview only, write nothing:
  ... populate_dim_gl_account_fill.py --dry-run
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
from etl.account_fill import fill_missing_account_rows


def _resolve_mode(session: SASession, project_id: str | None,
                  force_library: bool, force_exclusive: bool) -> str:
    if force_exclusive:
        return "exclusive"
    if force_library:
        return "library"
    if project_id is None:
        return "library"  # default
    try:
        from etl.project_config import resolve_rebuild_flags
        return resolve_rebuild_flags(session, project_id).get("account_mapping_mode", "library")
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] project config lookup failed ({exc}); using 'library'.")
        return "library"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fill missing dim_gl_account rows from lib_account_mapping + ovr_account_mapping."
    )
    parser.add_argument("--project-id", default=None,
                        help="resolve account_mapping_mode from this project's config")
    parser.add_argument("--exclusive", action="store_true",
                        help="force exclusive mode (skip the library fill)")
    parser.add_argument("--force-library", action="store_true",
                        help="force library mode regardless of project config")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the fill plan; write nothing")
    args = parser.parse_args()

    with SASession(engine) as session:
        lib_n = session.execute(text("SELECT COUNT(*) FROM lib_account_mapping")).scalar() or 0
        mode = _resolve_mode(session, args.project_id, args.force_library, args.exclusive)
        print(f"account_mapping_mode = {mode!r}; lib_account_mapping holds {lib_n} rows.")

        if mode == "exclusive":
            print("[exclusive] library fill SKIPPED — only the provided mapping is used.")
            return 0
        if lib_n == 0:
            print("ERROR: lib_account_mapping is empty — run load_account_mapping_library.py first.")
            return 2

        summary = fill_missing_account_rows(session, dry_run=args.dry_run)
        if not args.dry_run:
            session.commit()

    print(f"\nSUMMARY ({'dry-run' if args.dry_run else 'applied'}):")
    print(f"  missing (account, fy) keys : {summary['missing_keys']}")
    print(f"  filled                     : {summary['filled']}")
    print(f"    from override            : {summary['from_override']}")
    print(f"    from library             : {summary['from_library']}")
    print(f"  skipped (no account_name)  : {summary['skipped_no_name']}")
    print(f"  skipped (no resolution)    : {summary['skipped_no_resolution']}")
    if args.dry_run:
        print("\n[dry-run] no writes performed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
