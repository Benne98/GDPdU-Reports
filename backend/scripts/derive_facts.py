"""Thin CLI wrapper around the reporting-v2 rebuild (etl.rebuild.rebuild_project).

HISTORY
───────
This script used to hold the derivation SQL inline.  As of reporting-v2 Phase 1 the
logic was folded into reusable ``etl`` modules so the ingest-commit rebuild and this
CLI share ONE implementation (behaviour-preserving — proven by the golden gate):

  * derived-fact SQL          → etl/derive_facts_sql.py
  * stored-partner backfill   → etl/derive.py::backfill_partner_links_on_gl_lines
  * orchestration             → etl/rebuild.py::rebuild_project

This wrapper keeps the original CLI contract (``--dry-run`` prints the per-label
account coverage; a normal run re-derives all four fact tables idempotently).

DERIVATION LOGIC + SIGN CONVENTIONS
───────────────────────────────────
See etl/derive_facts_sql.py (module docstring) for the canonical sign conventions,
classification label sets, and the DELETE+INSERT idempotency contract.  due_date is
stored NULL for AR/AP (GoBD source has no payment-term column); aging buckets are
computed at query time from posting_date.

Usage:
  cd C:\\Users\\bened\\OneDrive\\Finssentials\\GDPdU-Reports
  $env:DB_PASSWORD = "..."
  .\\backend\\.venv\\Scripts\\python.exe backend/scripts/derive_facts.py
  # dry-run (shows account counts without writing):
  .\\backend\\.venv\\Scripts\\python.exe backend/scripts/derive_facts.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
_ROOT = _BACKEND.parent
for _p in (_BACKEND, _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from sqlalchemy import text
from sqlalchemy.orm import Session as SASession

from app.db import engine
from etl.derive_facts_sql import preflight_account_counts


def _print_preflight(session: SASession) -> None:
    print("\nPre-flight: account coverage per classification label:")
    for label, accts, lines in preflight_account_counts(session):
        print(f"  {label:<28}: {accts:>6} accounts, {lines:>9} GL lines")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Derive fact_sales / fact_com / fact_ar / fact_ap from fact_gl_line "
                    "(thin wrapper around etl.rebuild.rebuild_project)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print account coverage counts only; do not modify any tables.",
    )
    args = parser.parse_args()

    with SASession(engine) as session:
        gl_count = session.execute(text("SELECT COUNT(*) FROM fact_gl_line")).scalar() or 0
        print(f"fact_gl_line row count: {gl_count:,}")
        if gl_count == 0:
            print("ERROR: fact_gl_line is empty — load GL data first.")
            return 1

        _print_preflight(session)

        if args.dry_run:
            print("--dry-run: no changes made.")
            return 0

        print("Deriving fact tables via rebuild_project (DELETE + INSERT)…\n")

        from etl.rebuild import rebuild_project

        summary = rebuild_project(session, scope=None, mode="full")

        link = summary["partner_backfill"]
        print(
            f"  partner backfill: {link['income_customer_linked']:,} income lines, "
            f"{link['material_supplier_linked']:,} material lines"
        )
        facts = summary["derived_facts"]
        print(f"  fact_sales : {facts['sales']:>9,} rows inserted")
        print(f"  fact_com   : {facts['com']:>9,} rows inserted")
        print(f"  fact_ar    : {facts['ar']:>9,} rows inserted")
        print(f"  fact_ap    : {facts['ap']:>9,} rows inserted")
        bs = summary.get("structure_recon", {}).get("bs_structure_rows", 0)
        print(f"  bs_structure: {bs:>8,} rows upserted")

        total = facts["sales"] + facts["com"] + facts["ar"] + facts["ap"]
        print(
            f"\nSummary: {total:,} total derived-fact rows committed "
            f"({facts['sales']:,} sales, {facts['com']:,} com, "
            f"{facts['ar']:,} AR, {facts['ap']:,} AP)."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
