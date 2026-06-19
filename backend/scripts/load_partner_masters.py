#!/usr/bin/env python3
"""CLI: load Customer/Vendor Master CSVs into dim_customer / dim_supplier.

Environment (optional):
  CUSTOMER_MASTER_CSV  path to Customer Master.csv
  VENDOR_MASTER_CSV    path to Vendor Master.csv

Example:
  python backend/scripts/load_partner_masters.py \\
    --customer-csv "path/to/Customer Master.csv" \\
    --vendor-csv "path/to/Vendor Master.csv"
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_BACKEND = Path(__file__).resolve().parent.parent
for _env in (_BACKEND / ".env", Path(r"C:\Users\bened\OneDrive\Finssentials\finssentials\backend\.env")):
    if _env.is_file():
        from dotenv import load_dotenv
        load_dotenv(_env)
        break

from app.db import SessionLocal  # noqa: E402
from etl.load import load_partners  # noqa: E402
from etl.load_partner_masters import (  # noqa: E402
    DEFAULT_CUSTOMER_MASTER_CSV,
    DEFAULT_VENDOR_MASTER_CSV,
    load_customer_master_csv,
    load_vendor_master_csv,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Load BC partner master CSVs into GDPdU DB")
    parser.add_argument("--customer-csv", default=os.environ.get("CUSTOMER_MASTER_CSV", DEFAULT_CUSTOMER_MASTER_CSV))
    parser.add_argument("--vendor-csv", default=os.environ.get("VENDOR_MASTER_CSV", DEFAULT_VENDOR_MASTER_CSV))
    args = parser.parse_args()

    if not args.customer_csv and not args.vendor_csv:
        print("Provide --customer-csv and/or --vendor-csv (or env CUSTOMER_MASTER_CSV / VENDOR_MASTER_CSV)")
        sys.exit(1)

    customers = (
        load_customer_master_csv(args.customer_csv)
        if args.customer_csv and Path(args.customer_csv).is_file()
        else None
    )
    vendors = (
        load_vendor_master_csv(args.vendor_csv)
        if args.vendor_csv and Path(args.vendor_csv).is_file()
        else None
    )

    if customers is None and vendors is None:
        print("No readable CSV files found.")
        sys.exit(1)

    session = SessionLocal()
    try:
        counts = load_partners(
            session,
            customers if customers is not None else __import__("pandas").DataFrame(),
            vendors if vendors is not None else __import__("pandas").DataFrame(),
        )
        session.commit()
        print(f"Partner masters loaded: {counts}")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
