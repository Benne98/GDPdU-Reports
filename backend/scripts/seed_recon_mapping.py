"""Seed dim_pl_recon_mapping and dim_bs_recon_mapping from Desktop Excel files.

Usage:
  cd GDPdU-Reports
  python backend/scripts/seed_recon_mapping.py
  python backend/scripts/seed_recon_mapping.py --pl path/to/PL.xlsx --bs path/to/BS.xlsx
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
_ROOT = _BACKEND.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.db import SessionLocal  # noqa: E402
from etl.recon_mapping import seed_recon_mapping_from_excel  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed recon mapping tables from Excel.")
    parser.add_argument("--pl", dest="pl_path", default=None, help="PL_recon_Mapping.xlsx path")
    parser.add_argument("--bs", dest="bs_path", default=None, help="BS_recon_Mapping.xlsx path")
    args = parser.parse_args()

    with SessionLocal() as session:
        result = seed_recon_mapping_from_excel(
            session,
            pl_path=args.pl_path,
            bs_path=args.bs_path,
        )
    print(
        f"[seed_recon_mapping] PL: {result['pl_rows']} rows from {result['pl_path']}\n"
        f"[seed_recon_mapping] BS: {result['bs_rows']} rows from {result['bs_path']}"
    )


if __name__ == "__main__":
    main()
