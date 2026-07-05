r"""Load the customer / supplier master dimensions into finssentials_v4.

Phase-1 AR/AP aging rebuild.  Reads the two semicolon-delimited master exports::

    <base>/dim_customer.csv   -> dim_customer
    <base>/dim_supplier.csv   -> dim_supplier

and UPSERTs on the primary key (customer_id / supplier_id).  ``entity_prefix`` is a
GENERATED column (LEFT(pk, 2)) so it is NEVER inserted; the CSV ``entity_name``
column is descriptive only and is NOT a table column, so it is dropped.

USAGE (PowerShell)::

    $env:DB_PASSWORD='<postgres-password>'; $env:DB_NAME='finssentials_v4'
    backend\.venv\Scripts\python.exe backend\scripts\load_opos_master_dims.py `
        --base "C:\Users\bened\OneDrive\Desktop\Finssentials - Setup"

    ... load_opos_master_dims.py --dry-run          # parse only, no DB write
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

_CHUNK = 5_000

# table -> (csv filename, pk, ordered non-generated columns present in the CSV).
_SPECS = {
    "dim_customer": {
        "file": "dim_customer.csv",
        "pk": "customer_id",
        "cols": [
            "customer_id", "debtor_number", "name_line_1", "name_line_2",
            "country_code", "region_code", "city", "postal_code",
            "default_currency", "source_system", "updated_at",
        ],
    },
    "dim_supplier": {
        "file": "dim_supplier.csv",
        "pk": "supplier_id",
        "cols": [
            "supplier_id", "creditor_number", "name_line_1", "name_line_2",
            "country_code", "region_code", "city", "postal_code",
            "default_currency", "purchasing_org", "source_system", "updated_at",
        ],
    },
}


def _load_one(table: str, base: Path, dry_run: bool) -> int:
    spec = _SPECS[table]
    path = base / spec["file"]
    if not path.exists():
        raise SystemExit(f"missing master file: {path}")
    df = pd.read_csv(path, sep=";", dtype=str)
    df.columns = [str(c).strip() for c in df.columns]

    cols = [c for c in spec["cols"] if c in df.columns]
    missing = [c for c in spec["cols"] if c not in df.columns]
    if spec["pk"] not in cols:
        raise SystemExit(f"[{table}] PK {spec['pk']} not found in {list(df.columns)}")
    if missing:
        print(f"  [{table}] NOTE: CSV missing optional columns {missing} (loaded as NULL)")

    # NaN -> None; drop rows without a PK.
    df = df.where(pd.notna(df), None)
    records = []
    for r in df[cols].to_dict(orient="records"):
        pk_val = r.get(spec["pk"])
        if pk_val is None or str(pk_val).strip() == "":
            continue
        records.append({c: r.get(c) for c in cols})

    print(f"  {table}: parsed {len(records)} rows from {path.name}")
    if dry_run:
        return len(records)

    placeholders = ", ".join(f":{c}" for c in cols)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != spec["pk"])
    upsert = text(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({spec['pk']}) DO UPDATE SET {updates}"
    )
    with engine.begin() as conn:
        for i in range(0, len(records), _CHUNK):
            conn.execute(upsert, records[i:i + _CHUNK])
    print(f"    -> upserted {len(records)} into {table}")
    return len(records)


def main() -> None:
    ap = argparse.ArgumentParser(description="Load customer/supplier masters into finssentials_v4.")
    ap.add_argument("--base", default=r"C:\Users\bened\OneDrive\Desktop\Finssentials - Setup")
    ap.add_argument("--table", choices=sorted(_SPECS), help="only this table (default: both)")
    ap.add_argument("--dry-run", action="store_true", help="parse only, no DB write")
    ap.add_argument(
        "--i-know-this-is-live", action="store_true",
        help="override the live-DB refusal (DANGER: upserts into the LIVE 'Finssentials' DB)",
    )
    args = ap.parse_args()

    base = Path(args.base)
    tables = (args.table,) if args.table else tuple(_SPECS)
    if not args.dry_run:
        print(f"Target DB: {engine.url.database!r} @ {engine.url.host}")
        assert_not_live_db(engine, allow_live=args.i_know_this_is_live)

    totals = {t: _load_one(t, base, args.dry_run) for t in tables}
    print("\n=== DIM COUNTS ===")
    for t in sorted(totals):
        print(f"  {t:16} {totals[t]:>8}")


if __name__ == "__main__":
    main()
