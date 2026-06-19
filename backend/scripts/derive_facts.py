"""Derive fact_sales, fact_com, fact_ar, fact_ap from fact_gl_line (idempotent).

WHY THIS SCRIPT EXISTS
──────────────────────
load_decidra_reference.py calls load_canonical() WITHOUT passing account_classes,
so every GL line gets account_class = 'other' and none of the derived fact tables
(fact_sales / fact_com / fact_ar / fact_ap) receive any rows.  This script
fixes that by re-deriving all four tables from the loaded fact_gl_line data
using the correct account classifications from dim_gl_account.

DERIVATION LOGIC (account classification)
──────────────────────────────────────────
Classification is done via a JOIN to dim_gl_account.  The labels used match
the Decidra English account mapping loaded by load_decidra_reference.py:

  fact_sales (revenue lines):
    level_3 IN (<revenue_labels>)                    → gross_sales = -amount
    Assumption: level_3 = 'Net sales' is the primary revenue label in this GL.
    Fallback: level_2 = 'Income' captures any additional income accounts not
    labelled 'Net sales' at level_3.  Both filters are applied via OR so no
    revenue lines are missed.

  fact_com (cost-of-materials lines):
    level_3 IN (<material_labels>)                   → cost_of_materials = +amount
    Primary: level_3 = 'Cost of materials'.

  fact_ar (trade receivables open items):
    level_3 IN (<receivable_labels>)                 → amount as stored (debit = +)

  fact_ap (trade payables open items):
    level_3 IN (<payable_labels>)                    → amount as stored (credit = -)

Sign conventions (canonical):
  amount +  = Soll/Debit
  amount -  = Haben/Credit
  gross_sales       = -amount   (revenue lines are credit bookings)
  cost_of_materials = +amount   (material lines are debit bookings)
  AR amount: positive for outstanding receivables (debit side of trade-AR account)
  AP amount: negative for outstanding payables (credit side of trade-AP account)

PARTNER LINKING
───────────────
customer_id / supplier_id on revenue/material lines are populated by txn-based
propagation (receivable→revenue, payable→material within each journal entry).
``_backfill_partner_links_on_gl_lines`` runs this on stored fact_gl_line when the
original GL load skipped classification before link_partners (Decidra reference path).

due_date for AR / AP
─────────────────────
The GoBD source (Unmapped_GoBD_20260303.csv) does NOT contain a payment-term or
due-date column.  Therefore due_date is stored as NULL for all AR and AP rows.
Aging buckets for the AR/AP dashboards are computed at query time from
posting_date (days since posting) as a documented approximation:
  bucket 0 = 0-30 days, bucket 1 = 31-60, bucket 2 = 61-90, bucket 3 = 90+.
This can be replaced with real due_date values if a payment-terms extract
becomes available; the column is already present and NULL-tolerant in the schema.

IDEMPOTENCY
───────────
DELETE + INSERT (not ON CONFLICT).  All four tables are cleared and re-populated
in a single transaction.  Safe to re-run any number of times.

CONFIGURABLE LABELS
───────────────────
Edit the _REVENUE_L3 / _MATERIAL_L3 / _RECEIVABLE_L3 / _PAYABLE_L3 / _REVENUE_L2
constants below to match the actual level_3/level_2 values in your dim_gl_account.
The script prints a pre-flight summary of how many GL accounts match each label
so you can verify before the DELETE+INSERT.

Usage:
  cd C:\\Users\\bened\\OneDrive\\Finssentials\\Finssentials_GDPDU
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
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from sqlalchemy import text
from sqlalchemy.orm import Session as SASession

from app.db import engine

# ─────────────────────────────────────────────────────────────────────────────
# Account-classification label sets
# Adjust these if dim_gl_account uses different level_3/level_2 values.
# ─────────────────────────────────────────────────────────────────────────────

# Revenue: level_3 values that identify net-sales / income lines
_REVENUE_L3: tuple[str, ...] = ("Net sales",)

# Revenue fallback: level_2 value that broadly tags the income section
# (catches revenue sub-accounts whose level_3 may differ from 'Net sales')
_REVENUE_L2: tuple[str, ...] = ("Income",)

# Cost of materials: level_3 values
_MATERIAL_L3: tuple[str, ...] = ("Cost of materials",)

# AR: level_3 value for trade receivables accounts
_RECEIVABLE_L3: tuple[str, ...] = ("Trade receivables",)

# AP: level_3 value for trade payables accounts
_PAYABLE_L3: tuple[str, ...] = ("Trade payables",)

BATCH_SIZE = 10_000


def _backfill_partner_links_on_gl_lines(session: SASession) -> dict[str, int]:
    """Method-A backfill on stored fact_gl_line: receivable→revenue, payable→material.

    The Decidra reference load skipped account_class before link_partners, so txn
    linking never ran.  This SQL mirrors etl.derive.propagate_partners on the DB.
    """
    rev_l3 = ", ".join(f"'{v}'" for v in _REVENUE_L3)
    rev_l2 = ", ".join(f"'{v}'" for v in _REVENUE_L2)
    mat_l3 = ", ".join(f"'{v}'" for v in _MATERIAL_L3)
    ar_l3 = ", ".join(f"'{v}'" for v in _RECEIVABLE_L3)
    ap_l3 = ", ".join(f"'{v}'" for v in _PAYABLE_L3)

    cust_result = session.execute(
        text(f"""
            WITH recv AS (
                SELECT
                    r.journal_entry_group_number,
                    r.fiscal_year,
                    MAX(r.customer_id) AS customer_id
                FROM fact_gl_line r
                JOIN dim_gl_account ar
                  ON ar.account_number_group = r.account_number_group
                 AND ar.fiscal_year          = r.fiscal_year
                WHERE ar.level_3 IN ({ar_l3})
                  AND r.customer_id IS NOT NULL
                  AND TRIM(r.customer_id) <> ''
                GROUP BY r.journal_entry_group_number, r.fiscal_year
                HAVING COUNT(DISTINCT r.customer_id) = 1
            ),
            targets AS (
                SELECT income.booking_line_id, recv.customer_id
                FROM fact_gl_line income
                JOIN recv
                  ON recv.journal_entry_group_number = income.journal_entry_group_number
                 AND recv.fiscal_year = income.fiscal_year
                JOIN dim_gl_account ai
                  ON ai.account_number_group = income.account_number_group
                 AND ai.fiscal_year          = income.fiscal_year
                WHERE (ai.level_3 IN ({rev_l3}) OR ai.level_2 IN ({rev_l2}))
                  AND (income.customer_id IS NULL OR TRIM(income.customer_id) = '')
            )
            UPDATE fact_gl_line l
            SET customer_id = t.customer_id
            FROM targets t
            WHERE l.booking_line_id = t.booking_line_id
        """)
    )

    supp_result = session.execute(
        text(f"""
            WITH pay AS (
                SELECT
                    p.journal_entry_group_number,
                    p.fiscal_year,
                    MAX(p.supplier_id) AS supplier_id
                FROM fact_gl_line p
                JOIN dim_gl_account ap
                  ON ap.account_number_group = p.account_number_group
                 AND ap.fiscal_year          = p.fiscal_year
                WHERE ap.level_3 IN ({ap_l3})
                  AND p.supplier_id IS NOT NULL
                  AND TRIM(p.supplier_id) <> ''
                GROUP BY p.journal_entry_group_number, p.fiscal_year
                HAVING COUNT(DISTINCT p.supplier_id) = 1
            ),
            targets AS (
                SELECT mat.booking_line_id, pay.supplier_id
                FROM fact_gl_line mat
                JOIN pay
                  ON pay.journal_entry_group_number = mat.journal_entry_group_number
                 AND pay.fiscal_year = mat.fiscal_year
                JOIN dim_gl_account am
                  ON am.account_number_group = mat.account_number_group
                 AND am.fiscal_year          = mat.fiscal_year
                WHERE am.level_3 IN ({mat_l3})
                  AND (mat.supplier_id IS NULL OR TRIM(mat.supplier_id) = '')
            )
            UPDATE fact_gl_line l
            SET supplier_id = t.supplier_id
            FROM targets t
            WHERE l.booking_line_id = t.booking_line_id
        """)
    )

    return {
        "income_customer_linked": int(cust_result.rowcount or 0),
        "material_supplier_linked": int(supp_result.rowcount or 0),
    }


def _preflight_account_counts(session: SASession) -> None:
    """Print how many GL accounts (and GL lines) match each classification label."""
    checks = [
        ("Revenue (level_3)",  "level_3", _REVENUE_L3),
        ("Revenue (level_2)",  "level_2", _REVENUE_L2),
        ("Material (level_3)", "level_3", _MATERIAL_L3),
        ("AR (level_3)",       "level_3", _RECEIVABLE_L3),
        ("AP (level_3)",       "level_3", _PAYABLE_L3),
    ]
    print("\nPre-flight: account coverage per classification label:")
    for label, col, values in checks:
        placeholders = ", ".join(f"'{v}'" for v in values)
        row = session.execute(
            text(f"""
                SELECT COUNT(DISTINCT a.account_number_group) AS accts,
                       COUNT(l.booking_line_id)               AS lines
                FROM dim_gl_account a
                LEFT JOIN fact_gl_line l
                  ON l.account_number_group = a.account_number_group
                 AND l.fiscal_year          = a.fiscal_year
                WHERE a.{col} IN ({placeholders})
            """)
        ).fetchone()
        print(f"  {label:<28}: {row[0]:>6} accounts, {row[1]:>9} GL lines")
    print()


def _derive_fact_sales(session: SASession) -> int:
    """DELETE + INSERT fact_sales from revenue GL lines.

    Returns number of rows inserted.
    """
    session.execute(text("DELETE FROM fact_sales"))

    rev_l3 = ", ".join(f"'{v}'" for v in _REVENUE_L3)
    rev_l2 = ", ".join(f"'{v}'" for v in _REVENUE_L2)

    result = session.execute(
        text(f"""
            INSERT INTO fact_sales
              (booking_line_id, journal_entry_group_number, fiscal_year,
               account_number_group, customer_id, posting_date,
               gross_sales, link_method, entry_type, source_system)
            SELECT
                l.booking_line_id,
                l.journal_entry_group_number,
                l.fiscal_year,
                l.account_number_group,
                l.customer_id,
                e.posting_date,
                (-l.amount)::NUMERIC(18,6)                          AS gross_sales,
                CASE WHEN l.customer_id IS NOT NULL THEN 'txn'
                     ELSE 'none' END                                AS link_method,
                e.entry_type,
                l.source_system
            FROM fact_gl_line l
            JOIN fact_gl_entry e
              ON e.journal_entry_group_number = l.journal_entry_group_number
             AND e.fiscal_year               = l.fiscal_year
            JOIN dim_gl_account a
              ON a.account_number_group = l.account_number_group
             AND a.fiscal_year          = l.fiscal_year
            WHERE
              a.level_3 IN ({rev_l3})
              OR a.level_2 IN ({rev_l2})
            ON CONFLICT (booking_line_id) DO NOTHING
        """)
    )
    return result.rowcount


def _derive_fact_com(session: SASession) -> int:
    """DELETE + INSERT fact_com from cost-of-materials GL lines."""
    session.execute(text("DELETE FROM fact_com"))

    mat_l3 = ", ".join(f"'{v}'" for v in _MATERIAL_L3)

    result = session.execute(
        text(f"""
            INSERT INTO fact_com
              (booking_line_id, journal_entry_group_number, fiscal_year,
               account_number_group, supplier_id, posting_date,
               cost_of_materials, link_method, entry_type, source_system)
            SELECT
                l.booking_line_id,
                l.journal_entry_group_number,
                l.fiscal_year,
                l.account_number_group,
                l.supplier_id,
                e.posting_date,
                l.amount::NUMERIC(18,6)                             AS cost_of_materials,
                CASE WHEN l.supplier_id IS NOT NULL THEN 'txn'
                     ELSE 'none' END                                AS link_method,
                e.entry_type,
                l.source_system
            FROM fact_gl_line l
            JOIN fact_gl_entry e
              ON e.journal_entry_group_number = l.journal_entry_group_number
             AND e.fiscal_year               = l.fiscal_year
            JOIN dim_gl_account a
              ON a.account_number_group = l.account_number_group
             AND a.fiscal_year          = l.fiscal_year
            WHERE a.level_3 IN ({mat_l3})
            ON CONFLICT (booking_line_id) DO NOTHING
        """)
    )
    return result.rowcount


def _derive_fact_ar(session: SASession) -> int:
    """DELETE + INSERT fact_ar from trade-receivables GL lines.

    due_date is NULL (see module docstring): GoBD source has no payment-term column.
    Aging approximation: use (CURRENT_DATE - e.posting_date) at query time.
    """
    session.execute(text("DELETE FROM fact_ar"))

    ar_l3 = ", ".join(f"'{v}'" for v in _RECEIVABLE_L3)

    result = session.execute(
        text(f"""
            INSERT INTO fact_ar
              (booking_line_id, journal_entry_group_number, fiscal_year, line_number,
               account_number_group, customer_id,
               posting_date, document_date, due_date,
               amount, reference_document_number,
               entry_type, link_method, source_system)
            SELECT
                l.booking_line_id,
                l.journal_entry_group_number,
                l.fiscal_year,
                l.line_number,
                l.account_number_group,
                l.customer_id,
                e.posting_date,
                e.document_date,
                NULL::DATE                                          AS due_date,
                l.amount::NUMERIC(18,6),
                e.reference_document_number,
                e.entry_type,
                CASE WHEN l.customer_id IS NOT NULL THEN 'txn'
                     ELSE 'none' END                                AS link_method,
                l.source_system
            FROM fact_gl_line l
            JOIN fact_gl_entry e
              ON e.journal_entry_group_number = l.journal_entry_group_number
             AND e.fiscal_year               = l.fiscal_year
            JOIN dim_gl_account a
              ON a.account_number_group = l.account_number_group
             AND a.fiscal_year          = l.fiscal_year
            WHERE a.level_3 IN ({ar_l3})
            ON CONFLICT (booking_line_id) DO NOTHING
        """)
    )
    return result.rowcount


def _derive_fact_ap(session: SASession) -> int:
    """DELETE + INSERT fact_ap from trade-payables GL lines.

    due_date is NULL (same reason as fact_ar).
    Amount sign: payables are credit postings → amount is typically negative.
    The consumer (AP aging) uses ABS(amount) for outstanding balance.
    """
    session.execute(text("DELETE FROM fact_ap"))

    ap_l3 = ", ".join(f"'{v}'" for v in _PAYABLE_L3)

    result = session.execute(
        text(f"""
            INSERT INTO fact_ap
              (booking_line_id, journal_entry_group_number, fiscal_year, line_number,
               account_number_group, supplier_id,
               posting_date, document_date, due_date,
               amount, reference_document_number,
               entry_type, link_method, source_system)
            SELECT
                l.booking_line_id,
                l.journal_entry_group_number,
                l.fiscal_year,
                l.line_number,
                l.account_number_group,
                l.supplier_id,
                e.posting_date,
                e.document_date,
                NULL::DATE                                          AS due_date,
                l.amount::NUMERIC(18,6),
                e.reference_document_number,
                e.entry_type,
                CASE WHEN l.supplier_id IS NOT NULL THEN 'txn'
                     ELSE 'none' END                                AS link_method,
                l.source_system
            FROM fact_gl_line l
            JOIN fact_gl_entry e
              ON e.journal_entry_group_number = l.journal_entry_group_number
             AND e.fiscal_year               = l.fiscal_year
            JOIN dim_gl_account a
              ON a.account_number_group = l.account_number_group
             AND a.fiscal_year          = l.fiscal_year
            WHERE a.level_3 IN ({ap_l3})
            ON CONFLICT (booking_line_id) DO NOTHING
        """)
    )
    return result.rowcount


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Derive fact_sales / fact_com / fact_ar / fact_ap from fact_gl_line"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print account coverage counts only; do not modify any tables.",
    )
    args = parser.parse_args()

    with SASession(engine) as session:
        # Verify fact_gl_line has data
        gl_count = session.execute(
            text("SELECT COUNT(*) FROM fact_gl_line")
        ).scalar() or 0
        print(f"fact_gl_line row count: {gl_count:,}")
        if gl_count == 0:
            print("ERROR: fact_gl_line is empty — run load_decidra_reference.py first.")
            return 1

        _preflight_account_counts(session)

        if args.dry_run:
            print("--dry-run: no changes made.")
            return 0

        print("Deriving fact tables (DELETE + INSERT) — this may take a few minutes…\n")

        link_counts = _backfill_partner_links_on_gl_lines(session)
        print(
            f"  partner backfill: {link_counts['income_customer_linked']:,} income lines, "
            f"{link_counts['material_supplier_linked']:,} material lines"
        )

        # All four tables are derived in one transaction so they're consistent.
        n_sales = _derive_fact_sales(session)
        print(f"  fact_sales : {n_sales:>9,} rows inserted")

        n_com = _derive_fact_com(session)
        print(f"  fact_com   : {n_com:>9,} rows inserted")

        n_ar = _derive_fact_ar(session)
        print(f"  fact_ar    : {n_ar:>9,} rows inserted")

        n_ap = _derive_fact_ap(session)
        print(f"  fact_ap    : {n_ap:>9,} rows inserted")

        session.commit()
        print(
            f"\nSummary: {n_sales + n_com + n_ar + n_ap:,} total derived-fact rows "
            f"committed ({n_sales:,} sales, {n_com:,} com, {n_ar:,} AR, {n_ap:,} AP)."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
