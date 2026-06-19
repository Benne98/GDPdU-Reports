"""Dataset versioning: scope snapshots and restore by load_id."""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from etl import derive as D

logger = logging.getLogger(__name__)

COMMIT_MODES = frozenset({"replace", "append"})
DATASET_GL = "gl"
DATASET_MAPPING = "mapping"
DATASET_RESTORE = "restore"


def derive_gl_scope(lines_df: pd.DataFrame) -> tuple[list[str], list[int]]:
    """Entity prefixes and fiscal years present in canonical GL lines."""
    prefixes: set[str] = set()
    years: set[int] = set()
    if lines_df.empty:
        return [], []
    if "journal_entry_group_number" in lines_df.columns:
        for v in lines_df["journal_entry_group_number"].dropna().astype(str):
            if len(v) >= 2:
                prefixes.add(v[:2])
    if "account_number_group" in lines_df.columns:
        for v in lines_df["account_number_group"].dropna().astype(str):
            if len(v) >= 2:
                prefixes.add(v[:2])
    if "fiscal_year" in lines_df.columns:
        for v in pd.to_numeric(lines_df["fiscal_year"], errors="coerce").dropna():
            years.add(int(v))
    return sorted(prefixes), sorted(years)


def derive_mapping_scope(mapping_df: pd.DataFrame) -> tuple[list[str], list[int]]:
    """Entity prefixes and fiscal years present in mapping rows."""
    if mapping_df.empty:
        return [], []
    prefixes = sorted(
        {str(v)[:2] for v in mapping_df["account_number_group"].dropna().astype(str) if len(str(v)) >= 2}
    )
    years = sorted(
        int(v) for v in pd.to_numeric(mapping_df["fiscal_year"], errors="coerce").dropna().unique()
    )
    return prefixes, years


def get_load_meta(session: Session, load_id: int) -> dict[str, Any] | None:
    row = session.execute(
        text(
            """
            SELECT load_id, dataset, scope_entity_prefixes, scope_fiscal_years,
                   commit_mode, snapshot_captured, row_count, loaded_at, loaded_by
            FROM meta_dataset_load WHERE load_id = :id
            """
        ),
        {"id": load_id},
    ).fetchone()
    if not row:
        return None
    return {
        "load_id": row[0],
        "dataset": row[1],
        "scope_entity_prefixes": list(row[2] or []),
        "scope_fiscal_years": [int(y) for y in (row[3] or [])],
        "commit_mode": row[4],
        "snapshot_captured": bool(row[5]),
        "row_count": row[6],
        "loaded_at": row[7],
        "loaded_by": row[8],
    }


def delete_gl_scope(session: Session, prefixes: list[str], years: list[int]) -> dict[str, int]:
    """Delete GL facts (derived + lines + entries) within entity/year scope."""
    if not prefixes or not years:
        return {}
    counts: dict[str, int] = {}
    scope_filter = """
        booking_line_id IN (
            SELECT booking_line_id FROM fact_gl_line
            WHERE entity_prefix = ANY(:pfx) AND fiscal_year = ANY(:fys)
        )
    """
    for table in ("fact_sales", "fact_com", "fact_ar", "fact_ap"):
        r = session.execute(
            text(f"DELETE FROM {table} WHERE {scope_filter}"),
            {"pfx": prefixes, "fys": years},
        )
        counts[table] = r.rowcount or 0
    r = session.execute(
        text(
            "DELETE FROM fact_gl_line "
            "WHERE entity_prefix = ANY(:pfx) AND fiscal_year = ANY(:fys)"
        ),
        {"pfx": prefixes, "fys": years},
    )
    counts["fact_gl_line"] = r.rowcount or 0
    r = session.execute(
        text(
            "DELETE FROM fact_gl_entry "
            "WHERE entity_prefix = ANY(:pfx) AND fiscal_year = ANY(:fys)"
        ),
        {"pfx": prefixes, "fys": years},
    )
    counts["fact_gl_entry"] = r.rowcount or 0
    logger.info("delete_gl_scope: %s", counts)
    return counts


def delete_mapping_scope(session: Session, prefixes: list[str], years: list[int]) -> dict[str, int]:
    """Delete dim mapping rows within entity/year scope."""
    if not prefixes or not years:
        return {}
    counts: dict[str, int] = {}
    for table in ("dim_gl_cf", "dim_gl_na"):
        r = session.execute(
            text(
                f"DELETE FROM {table} "
                "WHERE entity_prefix = ANY(:pfx) AND fiscal_year = ANY(:fys)"
            ),
            {"pfx": prefixes, "fys": years},
        )
        counts[table] = r.rowcount or 0
    r = session.execute(
        text(
            "DELETE FROM dim_gl_account "
            "WHERE entity_prefix = ANY(:pfx) AND fiscal_year = ANY(:fys)"
        ),
        {"pfx": prefixes, "fys": years},
    )
    counts["dim_gl_account"] = r.rowcount or 0
    return counts


def capture_gl_snapshot(
    session: Session,
    load_id: int,
    prefixes: list[str],
    years: list[int],
) -> dict[str, int]:
    """Copy live GL entry/line rows in scope to snapshot tables."""
    if not prefixes or not years:
        return {}
    counts: dict[str, int] = {}
    r = session.execute(
        text(
            """
            INSERT INTO snap_fact_gl_entry (
                load_id, journal_entry_group_number, fiscal_year, fiscal_period,
                entry_type, posting_date, document_date, document_type_code,
                reference_document_number, currency_code, header_note, source_system
            )
            SELECT :lid, journal_entry_group_number, fiscal_year, fiscal_period,
                   entry_type, posting_date, document_date, document_type_code,
                   reference_document_number, currency_code, header_note, source_system
            FROM fact_gl_entry
            WHERE entity_prefix = ANY(:pfx) AND fiscal_year = ANY(:fys)
            """
        ),
        {"lid": load_id, "pfx": prefixes, "fys": years},
    )
    counts["snap_fact_gl_entry"] = r.rowcount or 0
    r = session.execute(
        text(
            """
            INSERT INTO snap_fact_gl_line (
                load_id, journal_entry_group_number, fiscal_year, line_number,
                booking_line_id, account_number_group, amount, vat_amount,
                line_note, customer_id, supplier_id, posting_type, source_system
            )
            SELECT :lid, journal_entry_group_number, fiscal_year, line_number,
                   booking_line_id, account_number_group, amount, vat_amount,
                   line_note, customer_id, supplier_id, posting_type, source_system
            FROM fact_gl_line
            WHERE entity_prefix = ANY(:pfx) AND fiscal_year = ANY(:fys)
            """
        ),
        {"lid": load_id, "pfx": prefixes, "fys": years},
    )
    counts["snap_fact_gl_line"] = r.rowcount or 0
    return counts


def capture_mapping_snapshot(
    session: Session,
    load_id: int,
    prefixes: list[str],
    years: list[int],
) -> dict[str, int]:
    """Copy live dim mapping rows in scope to snapshot tables."""
    if not prefixes or not years:
        return {}
    counts: dict[str, int] = {}
    r = session.execute(
        text(
            """
            INSERT INTO snap_dim_gl_account (
                load_id, account_number_group, fiscal_year, gl_account_id, account_name,
                level_0, level_1, level_2, level_3, level_4, l4_sub,
                level_1_sort, level_2_sort, level_3_sort, level_4_sort,
                is_ic, source_system, entity_prefix
            )
            SELECT :lid, account_number_group, fiscal_year, gl_account_id, account_name,
                   level_0, level_1, level_2, level_3, level_4, l4_sub,
                   level_1_sort, level_2_sort, level_3_sort, level_4_sort,
                   is_ic, source_system, entity_prefix
            FROM dim_gl_account
            WHERE entity_prefix = ANY(:pfx) AND fiscal_year = ANY(:fys)
            """
        ),
        {"lid": load_id, "pfx": prefixes, "fys": years},
    )
    counts["snap_dim_gl_account"] = r.rowcount or 0
    r = session.execute(
        text(
            """
            INSERT INTO snap_dim_gl_na (
                load_id, account_number_group, fiscal_year,
                l6_na_mapping, l7_na_description, entity_prefix
            )
            SELECT :lid, n.account_number_group, n.fiscal_year,
                   n.l6_na_mapping, n.l7_na_description, a.entity_prefix
            FROM dim_gl_na n
            JOIN dim_gl_account a
              ON a.account_number_group = n.account_number_group
             AND a.fiscal_year = n.fiscal_year
            WHERE a.entity_prefix = ANY(:pfx) AND a.fiscal_year = ANY(:fys)
            """
        ),
        {"lid": load_id, "pfx": prefixes, "fys": years},
    )
    counts["snap_dim_gl_na"] = r.rowcount or 0
    r = session.execute(
        text(
            """
            INSERT INTO snap_dim_gl_cf (
                load_id, account_number_group, fiscal_year,
                l1, l2, l3, l4, l5, cf_mapping, entity_prefix
            )
            SELECT :lid, c.account_number_group, c.fiscal_year,
                   c.l1, c.l2, c.l3, c.l4, c.l5, c.cf_mapping, a.entity_prefix
            FROM dim_gl_cf c
            JOIN dim_gl_account a
              ON a.account_number_group = c.account_number_group
             AND a.fiscal_year = c.fiscal_year
            WHERE a.entity_prefix = ANY(:pfx) AND a.fiscal_year = ANY(:fys)
            """
        ),
        {"lid": load_id, "pfx": prefixes, "fys": years},
    )
    counts["snap_dim_gl_cf"] = r.rowcount or 0
    return counts


def snapshot_counts(session: Session, load_id: int, dataset: str) -> dict[str, int]:
    tables = (
        ("snap_fact_gl_entry", "snap_fact_gl_line")
        if dataset == DATASET_GL
        else ("snap_dim_gl_account", "snap_dim_gl_na", "snap_dim_gl_cf")
    )
    out: dict[str, int] = {}
    for table in tables:
        row = session.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE load_id = :id"),
            {"id": load_id},
        ).scalar()
        out[table] = int(row or 0)
    return out


def _restore_gl_facts_from_lines(session: Session, linked: pd.DataFrame) -> dict[str, int]:
    """Insert derived facts for all lines (no skip set)."""
    from etl.load import (
        _insert_fact_ap,
        _insert_fact_ar,
        _insert_fact_com,
        _insert_fact_sales,
    )

    fact_ar = D.derive_ar(linked)
    fact_ap = D.derive_ap(linked)
    fact_sales = D.derive_sales(linked)
    fact_com = D.derive_com(linked)
    empty_skip: set[int] = set()
    return {
        "ar": _insert_fact_ar(session, fact_ar, empty_skip),
        "ap": _insert_fact_ap(session, fact_ap, empty_skip),
        "sales": _insert_fact_sales(session, fact_sales, empty_skip),
        "com": _insert_fact_com(session, fact_com, empty_skip),
    }


def _fetch_linked_lines_df(
    session: Session,
    prefixes: list[str],
    years: list[int],
    linking_strategy: str = "txn",
) -> pd.DataFrame:
    """Build canonical lines DataFrame from live GL tables in scope."""
    rows = session.execute(
        text(
            """
            SELECT
                l.journal_entry_group_number, l.fiscal_year, l.line_number,
                l.booking_line_id, l.account_number_group, l.amount, l.vat_amount,
                l.line_note, l.customer_id, l.supplier_id, l.posting_type, l.source_system,
                e.fiscal_period, e.entry_type, e.posting_date, e.document_date,
                e.document_type_code, e.reference_document_number, e.currency_code, e.header_note,
                a.level_2, a.level_3, a.level_4
            FROM fact_gl_line l
            JOIN fact_gl_entry e
              ON e.journal_entry_group_number = l.journal_entry_group_number
             AND e.fiscal_year = l.fiscal_year
            LEFT JOIN dim_gl_account a
              ON a.account_number_group = l.account_number_group
             AND a.fiscal_year = l.fiscal_year
            WHERE l.entity_prefix = ANY(:pfx) AND l.fiscal_year = ANY(:fys)
            """
        ),
        {"pfx": prefixes, "fys": years},
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    cols = [
        "journal_entry_group_number", "fiscal_year", "line_number",
        "booking_line_id", "account_number_group", "amount", "vat_amount",
        "line_note", "customer_id", "supplier_id", "posting_type", "source_system",
        "fiscal_period", "entry_type", "posting_date", "document_date",
        "document_type_code", "reference_document_number", "currency_code", "header_note",
        "level_2", "level_3", "level_4",
    ]
    df = pd.DataFrame(rows, columns=cols)
    if "account_class" not in df.columns:
        df["account_class"] = "other"
    return D.link_partners(df, linking_strategy)


def restore_gl_snapshot(session: Session, load_id: int, linking_strategy: str = "txn") -> dict[str, Any]:
    """Restore GL scope from snapshot tagged with load_id."""
    meta = get_load_meta(session, load_id)
    if not meta:
        raise ValueError(f"load_id {load_id} not found")
    if meta["dataset"] not in (DATASET_GL, DATASET_RESTORE):
        raise ValueError(f"load_id {load_id} is dataset={meta['dataset']!r}, not gl")
    if not meta["snapshot_captured"]:
        raise ValueError(f"load_id {load_id} has no captured snapshot")
    prefixes = meta["scope_entity_prefixes"]
    years = meta["scope_fiscal_years"]
    if not prefixes or not years:
        raise ValueError(f"load_id {load_id} has empty scope")

    delete_gl_scope(session, prefixes, years)

    session.execute(
        text(
            """
            INSERT INTO fact_gl_entry (
                journal_entry_group_number, fiscal_year, fiscal_period, entry_type,
                posting_date, document_date, document_type_code, reference_document_number,
                currency_code, header_note, source_system
            )
            SELECT journal_entry_group_number, fiscal_year, fiscal_period, entry_type,
                   posting_date, document_date, document_type_code, reference_document_number,
                   currency_code, header_note, source_system
            FROM snap_fact_gl_entry WHERE load_id = :id
            """
        ),
        {"id": load_id},
    )
    session.execute(
        text(
            """
            INSERT INTO fact_gl_line (
                journal_entry_group_number, fiscal_year, line_number, booking_line_id,
                account_number_group, amount, vat_amount, line_note,
                customer_id, supplier_id, posting_type, source_system
            )
            SELECT journal_entry_group_number, fiscal_year, line_number, booking_line_id,
                   account_number_group, amount, vat_amount, line_note,
                   customer_id, supplier_id, posting_type, source_system
            FROM snap_fact_gl_line WHERE load_id = :id
            """
        ),
        {"id": load_id},
    )

    linked = _fetch_linked_lines_df(session, prefixes, years, linking_strategy)
    derived = _restore_gl_facts_from_lines(session, linked) if not linked.empty else {
        "ar": 0, "ap": 0, "sales": 0, "com": 0,
    }
    return {"scope_entity_prefixes": prefixes, "scope_fiscal_years": years, **derived}


def restore_mapping_snapshot(session: Session, load_id: int) -> dict[str, Any]:
    """Restore mapping scope from snapshot tagged with load_id."""
    meta = get_load_meta(session, load_id)
    if not meta:
        raise ValueError(f"load_id {load_id} not found")
    if meta["dataset"] != DATASET_MAPPING:
        raise ValueError(f"load_id {load_id} is dataset={meta['dataset']!r}, not mapping")
    if not meta["snapshot_captured"]:
        raise ValueError(f"load_id {load_id} has no captured snapshot")
    prefixes = meta["scope_entity_prefixes"]
    years = meta["scope_fiscal_years"]
    if not prefixes or not years:
        raise ValueError(f"load_id {load_id} has empty scope")

    delete_mapping_scope(session, prefixes, years)

    session.execute(
        text(
            """
            INSERT INTO dim_gl_account (
                account_number_group, fiscal_year, gl_account_id, account_name,
                level_0, level_1, level_2, level_3, level_4, l4_sub,
                level_1_sort, level_2_sort, level_3_sort, level_4_sort,
                is_ic, source_system
            )
            SELECT account_number_group, fiscal_year, gl_account_id, account_name,
                   level_0, level_1, level_2, level_3, level_4, l4_sub,
                   level_1_sort, level_2_sort, level_3_sort, level_4_sort,
                   is_ic, source_system
            FROM snap_dim_gl_account WHERE load_id = :id
            """
        ),
        {"id": load_id},
    )
    session.execute(
        text(
            """
            INSERT INTO dim_gl_na (account_number_group, fiscal_year, l6_na_mapping, l7_na_description)
            SELECT account_number_group, fiscal_year, l6_na_mapping, l7_na_description
            FROM snap_dim_gl_na WHERE load_id = :id
            """
        ),
        {"id": load_id},
    )
    session.execute(
        text(
            """
            INSERT INTO dim_gl_cf (
                account_number_group, fiscal_year, l1, l2, l3, l4, l5, cf_mapping
            )
            SELECT account_number_group, fiscal_year, l1, l2, l3, l4, l5, cf_mapping
            FROM snap_dim_gl_cf WHERE load_id = :id
            """
        ),
        {"id": load_id},
    )
    counts = snapshot_counts(session, load_id, DATASET_MAPPING)
    return {"scope_entity_prefixes": prefixes, "scope_fiscal_years": years, **counts}
