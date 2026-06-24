"""Derived-fact SQL: fact_sales / fact_com / fact_ar / fact_ap from fact_gl_line.

This module is the reusable home for the DB-side derivation logic that previously
lived inline in ``backend/scripts/derive_facts.py``.  The SQL is byte-for-byte the
same as that script's so the reporting-v2 rebuild is *behaviour-preserving*:
``derive_facts.py`` now imports these functions, and ``etl/rebuild.py`` calls them
too.

Sign conventions (canonical, see backend/scripts/derive_facts.py docstring):
  amount +  = Soll/Debit ; amount - = Haben/Credit
  gross_sales       = -amount   (revenue lines are credit bookings)
  cost_of_materials = +amount   (material lines are debit bookings)
  AR amount: stored sign (debit +) ; AP amount: stored sign (credit -)

Classification is by JOIN to dim_gl_account using the label sets below.  These match
the Decidra English account mapping and are identical to the constants that were in
derive_facts.py.  Idempotent: every function does DELETE + INSERT in the open
transaction (the caller commits).
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

# ─────────────────────────────────────────────────────────────────────────────
# Account-classification label sets (verbatim from derive_facts.py)
# ─────────────────────────────────────────────────────────────────────────────
REVENUE_L3: tuple[str, ...] = ("Net sales",)
REVENUE_L2: tuple[str, ...] = ("Income",)
MATERIAL_L3: tuple[str, ...] = ("Cost of materials",)
RECEIVABLE_L3: tuple[str, ...] = ("Trade receivables",)
PAYABLE_L3: tuple[str, ...] = ("Trade payables",)


def _quote(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def derive_fact_sales(session: Session) -> int:
    """DELETE + INSERT fact_sales from revenue GL lines.  Returns rows inserted."""
    session.execute(text("DELETE FROM fact_sales"))
    rev_l3 = _quote(REVENUE_L3)
    rev_l2 = _quote(REVENUE_L2)
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


def derive_fact_com(session: Session) -> int:
    """DELETE + INSERT fact_com from cost-of-materials GL lines."""
    session.execute(text("DELETE FROM fact_com"))
    mat_l3 = _quote(MATERIAL_L3)
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


def derive_fact_ar(session: Session) -> int:
    """DELETE + INSERT fact_ar from trade-receivables GL lines (due_date NULL)."""
    session.execute(text("DELETE FROM fact_ar"))
    ar_l3 = _quote(RECEIVABLE_L3)
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


def derive_fact_ap(session: Session) -> int:
    """DELETE + INSERT fact_ap from trade-payables GL lines (due_date NULL)."""
    session.execute(text("DELETE FROM fact_ap"))
    ap_l3 = _quote(PAYABLE_L3)
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


def derive_all_facts(session: Session) -> dict[str, int]:
    """Derive all four fact tables in the open transaction.  Caller commits."""
    return {
        "sales": derive_fact_sales(session),
        "com": derive_fact_com(session),
        "ar": derive_fact_ar(session),
        "ap": derive_fact_ap(session),
    }


def preflight_account_counts(session: Session) -> list[tuple[str, int, int]]:
    """Return (label, account_count, gl_line_count) per classification label."""
    checks = [
        ("Revenue (level_3)",  "level_3", REVENUE_L3),
        ("Revenue (level_2)",  "level_2", REVENUE_L2),
        ("Material (level_3)", "level_3", MATERIAL_L3),
        ("AR (level_3)",       "level_3", RECEIVABLE_L3),
        ("AP (level_3)",       "level_3", PAYABLE_L3),
    ]
    out: list[tuple[str, int, int]] = []
    for label, col, values in checks:
        placeholders = _quote(values)
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
        out.append((label, int(row[0]), int(row[1])))
    return out
