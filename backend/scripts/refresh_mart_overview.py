r"""Refresh the Overview-v2 pre-aggregation mart (mart_overview_period / _bs_balance).

This is the missing CALLER for
:func:`app.services.mart_overview.refresh_mart_overview_period`.  The mart tables
(migration ``0026``) exist but ship EMPTY: nothing populates them today, so the
:8011 ``/overview/summary`` hero always falls back to the live builders even when
``OVERVIEW_SUMMARY_USE_MART=true``.  Run this once (post-ingest) to populate them.

USAGE (PowerShell)::

    $env:DB_NAME='finssentials_v2'
    $env:DB_PASSWORD='***'
    backend\.venv\Scripts\python.exe backend\scripts\refresh_mart_overview.py

USAGE (bash)::

    cd backend && DB_NAME=finssentials_v2 DB_PASSWORD='***' \
        .venv/Scripts/python.exe scripts/refresh_mart_overview.py [--dry-run]

A FULL refresh resolves ALL entities: ``allowed_entities=None`` is the mart's own
"no restriction" contract (see mart_overview.refresh_mart_overview_period /
_prefix_fragment), so the operator-run refresh writes every visible entity.  The
per-request fail-closed visibility boundary is applied at READ time by the
endpoint, never here.

SAFETY: refuses the LIVE ``Finssentials`` DB via ``_db_safety.assert_not_live_db``
(the intended target is a v2/v4 clone).  ``--dry-run`` computes + counts inside a
transaction that is ROLLED BACK, writing nothing.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from sqlalchemy import text  # noqa: E402

from _db_safety import assert_not_live_db  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.services import mart_overview  # noqa: E402


def _print_breakdown(session, project_id: str) -> None:
    """Print rows written per (entity_prefix, fiscal_year, fiscal_period).

    Runs inside the SAME transaction as the refresh, so it sees the freshly
    inserted (still-uncommitted) rows — works for both --dry-run and commit.
    """
    period_rows = session.execute(
        text(
            "SELECT entity_prefix, fiscal_year, fiscal_period, COUNT(*) AS n "
            "FROM mart_overview_period WHERE project_id = :pid "
            "GROUP BY entity_prefix, fiscal_year, fiscal_period "
            "ORDER BY entity_prefix, fiscal_year, fiscal_period"
        ),
        {"pid": project_id},
    ).fetchall()
    bs_rows = session.execute(
        text(
            "SELECT entity_prefix, COUNT(*) AS n "
            "FROM mart_overview_bs_balance WHERE project_id = :pid "
            "GROUP BY entity_prefix ORDER BY entity_prefix"
        ),
        {"pid": project_id},
    ).fetchall()

    print("\n=== mart_overview_period rows per (entity, fiscal_year, fiscal_period) ===")
    if not period_rows:
        print("  (none)")
    for ep, fy, fp, n in period_rows:
        print(f"  {ep or '??':<4} {fy}-{fp:02d}   {n:>6}")
    print(f"  {'TOTAL period rows':<20} {sum(r[3] for r in period_rows):>6}")

    print("\n=== mart_overview_bs_balance rows per entity ===")
    if not bs_rows:
        print("  (none)")
    for ep, n in bs_rows:
        print(f"  {ep or '??':<4} {n:>6}")
    print(f"  {'TOTAL bs rows':<20} {sum(r[1] for r in bs_rows):>6}")

    wc_rows = session.execute(
        text(
            "SELECT entity_prefix, COUNT(*) AS n "
            "FROM mart_overview_wc_balance WHERE project_id = :pid "
            "GROUP BY entity_prefix ORDER BY entity_prefix"
        ),
        {"pid": project_id},
    ).fetchall()
    top_rows = session.execute(
        text(
            "SELECT entity_prefix, partner_type, COUNT(*) AS n "
            "FROM mart_overview_top_entities WHERE project_id = :pid "
            "GROUP BY entity_prefix, partner_type ORDER BY entity_prefix, partner_type"
        ),
        {"pid": project_id},
    ).fetchall()

    print("\n=== mart_overview_wc_balance rows per entity ===")
    if not wc_rows:
        print("  (none)")
    for ep, n in wc_rows:
        print(f"  {ep or '??':<4} {n:>6}")
    print(f"  {'TOTAL wc rows':<20} {sum(r[1] for r in wc_rows):>6}")

    print("\n=== mart_overview_top_entities rows per (entity, partner_type) ===")
    if not top_rows:
        print("  (none)")
    for ep, pt, n in top_rows:
        print(f"  {ep or '??':<4} {pt:<9} {n:>6}")
    print(f"  {'TOTAL top rows':<20} {sum(r[2] for r in top_rows):>6}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="(Re)populate the Overview-v2 mart from the canonical GL joins."
    )
    ap.add_argument("--project-id", default="default")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute + count inside a rolled-back transaction (writes nothing).",
    )
    ap.add_argument("--i-know-this-is-live", action="store_true")
    args = ap.parse_args()

    print(f"Target DB: {engine.url.database!r} @ {engine.url.host}")
    assert_not_live_db(engine, allow_live=args.i_know_this_is_live)

    with SessionLocal() as session:
        result = mart_overview.refresh_mart_overview_period(
            session, allowed_entities=None, project_id=args.project_id
        )
        wc_result = mart_overview.refresh_mart_wc_balance(
            session, allowed_entities=None, project_id=args.project_id
        )
        top_result = mart_overview.refresh_mart_top_entities(
            session, allowed_entities=None, project_id=args.project_id
        )
        print(
            f"\nRefreshed project_id={args.project_id!r}: "
            f"pl_rows={result.get('pl_rows')} bs_rows={result.get('bs_rows')} "
            f"source_load_id={result.get('source_load_id')}"
        )
        print(
            f"  wc_rows={wc_result.get('wc_rows')} "
            f"wc_source_load_id={wc_result.get('source_load_id')}"
        )
        print(
            f"  top_rows={top_result.get('top_rows')} "
            f"(customer={top_result.get('customer_rows')} "
            f"supplier={top_result.get('supplier_rows')}) "
            f"top_source_load_id={top_result.get('source_load_id')}"
        )
        _print_breakdown(session, args.project_id)

        if args.dry_run:
            session.rollback()
            print("\n--dry-run: transaction ROLLED BACK — nothing written.")
        else:
            session.commit()
            print("\nCommitted.")


if __name__ == "__main__":
    main()
