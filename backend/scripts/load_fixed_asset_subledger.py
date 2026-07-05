r"""Bulk-load fixed-asset subledger (anlagengitter.xlsx per year) into fact_fixed_asset.

USAGE (PowerShell)::

    $env:DB_NAME='finssentials_v2'
    backend\.venv\Scripts\python.exe backend\scripts\load_fixed_asset_subledger.py `
        --root "C:\Users\bened\OneDrive\Desktop\Finssentials - Setup\subledgers"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from sqlalchemy import text  # noqa: E402

from _db_safety import assert_not_live_db  # noqa: E402
from app.db import engine  # noqa: E402
from app.services.fixed_asset_ingest import (  # noqa: E402
    _INSERT_COLUMNS,
    as_of_date_for_year,
    map_fixed_asset_rows,
)

YEARS = (2022, 2023, 2024, 2025)
_CHUNK = 500


def _load_one(year: int, root: Path, dry_run: bool, project_id: str) -> int:
    path = root / str(year) / "anlagengitter.xlsx"
    if not path.exists():
        raise SystemExit(f"missing source file: {path}")
    df = pd.read_excel(path)
    rows = map_fixed_asset_rows(df, year=year, source_file_id=path.name, project_id=project_id)
    print(f"  {year}: parsed {len(rows)} rows from {path.name}")
    if dry_run:
        return len(rows)

    as_of = as_of_date_for_year(year)
    placeholders = ", ".join(f":{col}" for col in _INSERT_COLUMNS)
    insert_sql = text(
        f"INSERT INTO fact_fixed_asset ({', '.join(_INSERT_COLUMNS)}) VALUES ({placeholders})"
    )
    with engine.begin() as conn:
        conn.execute(
            text("""
                DELETE FROM fact_fixed_asset
                WHERE project_id = :pid AND as_of_date = :as_of
            """),
            {"pid": project_id, "as_of": as_of},
        )
        for i in range(0, len(rows), _CHUNK):
            conn.execute(insert_sql, rows[i:i + _CHUNK])
    print(f"    -> loaded {len(rows)} (as_of_date={as_of.isoformat()})")
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="Load fixed-asset subledger into Postgres.")
    ap.add_argument("--root", default=r"C:\Users\bened\OneDrive\Desktop\Finssentials - Setup\subledgers")
    ap.add_argument("--year", type=int, choices=YEARS, help="only this year (default: all)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--project-id", default="default")
    ap.add_argument("--i-know-this-is-live", action="store_true")
    args = ap.parse_args()

    root = Path(args.root)
    years = (args.year,) if args.year else YEARS
    if not args.dry_run:
        print(f"Target DB: {engine.url.database!r} @ {engine.url.host}")
        assert_not_live_db(engine, allow_live=args.i_know_this_is_live)

    totals: dict[str, int] = {}
    for year in years:
        totals[str(year)] = _load_one(year, root, args.dry_run, args.project_id)

    print("\n=== ROW COUNTS ===")
    for k in sorted(totals):
        print(f"  {k:8} {totals[k]:>8}")
    print(f"  {'TOTAL':8} {sum(totals.values()):>8}")


if __name__ == "__main__":
    main()
