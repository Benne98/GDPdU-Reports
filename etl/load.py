"""etl/load.py — Staging -> canonical DB load + dedup (P1f / DF1).

Pure helpers (content_hash, split_entry_line) are DB-free and fully unit-testable.
The DB-touching functions (dedup_check, load_canonical, load_account_mapping) require
a live session and are covered by integration tests (which need a real DB).

Load strategy:
  - fact_gl_entry: upsert-or-skip by (journal_entry_group_number, fiscal_year)
  - fact_gl_line:  insert-or-skip by booking_line_id (UNIQUE constraint)
  - fact_ar/ap/sales/com: derive from final lines, insert-or-skip by booking_line_id
  - meta_dataset_load: append a record regardless (audit trail)
  - dim_gl_account: ON CONFLICT (account_number_group, fiscal_year) DO UPDATE
  - dim_gl_na:      ON CONFLICT (account_number_group, fiscal_year) DO UPDATE
  - dim_gl_cf:      ON CONFLICT (account_number_group, fiscal_year) DO UPDATE

Idempotency: re-running with the same data produces no duplicate rows.
Transactional: all writes in one session.commit(); on error -> rollback.

Performance: bulk inserts use SQLAlchemy executemany (a list of param dicts passed to
session.execute) in chunks of BATCH_SIZE rows.  This avoids Python-level round-trips
and lets the DBAPI driver (psycopg2) use server-side batch protocols.  At 1.3M rows
this yields ~10-50x throughput versus per-row execute calls.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from etl import derive as D

logger = logging.getLogger(__name__)

# Rows per executemany batch.  5 000 keeps peak memory modest (~50 MB for a wide row)
# while amortising round-trip overhead well.  Tune upward if network latency is low.
BATCH_SIZE = 5_000


# --------------------------------------------------------------------------- #
# Pure helpers (no DB — fully unit-testable)
# --------------------------------------------------------------------------- #

def content_hash(df: pd.DataFrame) -> str:
    """Stable SHA-256 of the canonical lines DataFrame content.

    Rows are sorted by (journal_entry_group_number, fiscal_year, line_number)
    before hashing so that row-order differences do not affect the hash.  Only
    the data values are hashed (column names included as header).

    Returns a 64-char hex string.
    """
    sort_keys = [c for c in ("journal_entry_group_number", "fiscal_year", "line_number") if c in df.columns]
    stable = df.sort_values(sort_keys) if sort_keys else df
    # Convert to CSV bytes — deterministic (no index, na_rep='')
    csv_bytes = stable.to_csv(index=False, na_rep="").encode("utf-8")
    return hashlib.sha256(csv_bytes).hexdigest()


def split_entry_line(lines: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split a canonical lines DataFrame into (entries, lines) sub-frames.

    Returns
    -------
    entries : pd.DataFrame
        One row per unique (journal_entry_group_number, fiscal_year).
        Columns: journal_entry_group_number, fiscal_year, fiscal_period,
                 entry_type, posting_date, document_date, document_type_code,
                 reference_document_number, currency_code, header_note, source_system.
        For each group the *first* row's header fields are used (header fields
        should be consistent within a booking per check Q3).

    line_rows : pd.DataFrame
        All input rows with line-level columns selected.
        Columns: journal_entry_group_number, fiscal_year, line_number,
                 booking_line_id, account_number_group, amount, vat_amount,
                 line_note, customer_id, supplier_id, posting_type, source_system.
    """
    # ------------------------------------------------------------------ entries
    entry_cols = [
        "journal_entry_group_number", "fiscal_year", "fiscal_period",
        "entry_type", "posting_date", "document_date", "document_type_code",
        "reference_document_number", "currency_code", "header_note", "source_system",
    ]
    available_entry = [c for c in entry_cols if c in lines.columns]
    entries = (
        lines[available_entry]
        .drop_duplicates(subset=["journal_entry_group_number", "fiscal_year"], keep="first")
        .reset_index(drop=True)
    )

    # ------------------------------------------------------------------ lines
    line_cols = [
        "journal_entry_group_number", "fiscal_year", "line_number",
        "booking_line_id", "account_number_group", "amount", "vat_amount",
        "line_note", "customer_id", "supplier_id", "posting_type", "source_system",
    ]
    available_line = [c for c in line_cols if c in lines.columns]
    line_rows = lines[available_line].reset_index(drop=True)

    return entries, line_rows


# --------------------------------------------------------------------------- #
# DB-touching functions
# --------------------------------------------------------------------------- #

def dedup_check(
    session: Session,
    dataset: str,
    entity_prefix: str | None,
    fiscal_year: int | None,
    hash_value: str,
) -> bool:
    """Return True if this exact content (by hash) has already been loaded.

    Queries meta_dataset_load for a row matching (dataset, content_hash).
    Entity and fiscal_year are informational; the hash is the actual dedup key.
    """
    from sqlalchemy import text  # local import keeps module importable without SQLAlchemy

    row = session.execute(
        text(
            "SELECT 1 FROM meta_dataset_load "
            "WHERE dataset = :ds AND content_hash = :h "
            "LIMIT 1"
        ),
        {"ds": dataset, "h": hash_value},
    ).fetchone()
    return row is not None


def load_canonical(
    session: Session,
    lines_df: pd.DataFrame,
    dataset: str = "gl",
    loaded_by: str | None = None,
    linking_strategy: str = "txn",
    account_classes: "D.AccountClasses | None" = None,
    classify_level_col: str = "level_3",
    commit_mode: str = "replace",
    scope_entity_prefixes: list[str] | None = None,
    scope_fiscal_years: list[int] | None = None,
    auto_commit: bool = True,
) -> dict[str, Any]:
    """Load canonical GL lines into the DB transactionally.

    Steps:
      1. Apply partner linking (linking_strategy).
      2. Derive account_class if account_classes provided; else use existing column.
      3. Split into entries + line rows.
      4. Insert fact_gl_entry rows (skip existing PKs).
      5. Insert fact_gl_line rows (skip existing booking_line_ids).
      6. Derive fact_ar / fact_ap / fact_sales / fact_com and insert.
      7. Record meta_dataset_load.
      8. Commit.

    Parameters
    ----------
    classify_level_col : str
        Column in ``lines_df`` to pass to ``D.classify``.  Defaults to 'level_3'
        for backward compatibility.  Set to 'level_2', 'level_1', etc. when the
        ``ClassificationRules`` for the active source_system target a different
        hierarchy column.  Has no effect when ``account_classes`` is None.

    Returns
    -------
    dict with keys: entries, lines, ar, ap, sales, com, skipped, loaded_at.
    """
    from sqlalchemy import text

    from etl.versioning import (
        capture_gl_snapshot,
        delete_gl_scope,
        derive_gl_scope,
    )

    if commit_mode not in ("replace", "append"):
        raise ValueError(f"commit_mode must be 'replace' or 'append', got {commit_mode!r}")

    # ------------------------------------------------------------------ account_class (must precede partner linking)
    working = lines_df.copy()
    if account_classes is not None and classify_level_col in working.columns:
        working["account_class"] = D.classify(working[classify_level_col], account_classes)
    elif "account_class" not in working.columns:
        working["account_class"] = "other"

    # ------------------------------------------------------------------ partner linking (needs account_class for txn/gegenkonto)
    linked = D.link_partners(working, linking_strategy)

    prefixes = scope_entity_prefixes or list(derive_gl_scope(linked)[0])
    years = scope_fiscal_years or list(derive_gl_scope(linked)[1])

    if commit_mode == "replace" and prefixes and years:
        delete_gl_scope(session, prefixes, years)

    # ------------------------------------------------------------------ split
    entries, line_rows = split_entry_line(linked)

    # ------------------------------------------------------------------ fetch existing PKs (append mode only)
    existing_entries: set[tuple] = set()
    existing_line_ids: set[int] = set()

    if commit_mode == "append":
        if not entries.empty:
            rows = session.execute(
                text(
                    "SELECT journal_entry_group_number, fiscal_year FROM fact_gl_entry "
                    "WHERE (journal_entry_group_number, fiscal_year) = ANY(:pairs)"
                ),
                {"pairs": list(zip(
                    entries["journal_entry_group_number"].tolist(),
                    entries["fiscal_year"].tolist(),
                ))},
            ).fetchall()
            existing_entries = {(r[0], int(r[1])) for r in rows}

        if not line_rows.empty and "booking_line_id" in line_rows.columns:
            bid_list = line_rows["booking_line_id"].tolist()
            rows = session.execute(
                text("SELECT booking_line_id FROM fact_gl_line WHERE booking_line_id = ANY(:ids)"),
                {"ids": bid_list},
            ).fetchall()
            existing_line_ids = {int(r[0]) for r in rows}

    # ------------------------------------------------------------------ insert entries
    if not entries.empty:
        if commit_mode == "replace":
            new_entries = entries.reset_index(drop=True)
        else:
            entry_keys = (
                entries["journal_entry_group_number"].astype(str)
                + "|"
                + entries["fiscal_year"].astype(int).astype(str)
            )
            existing_entry_keys = {f"{j}|{int(y)}" for j, y in existing_entries}
            new_entries = entries[~entry_keys.isin(existing_entry_keys)].reset_index(drop=True)
    else:
        new_entries = entries

    entries_inserted = len(new_entries)
    if entries_inserted:
        _bulk_insert_entries(session, new_entries)

    # ------------------------------------------------------------------ insert lines
    if commit_mode == "replace":
        new_lines = line_rows.reset_index(drop=True)
    else:
        new_lines = line_rows[~line_rows["booking_line_id"].isin(existing_line_ids)].reset_index(drop=True)
    lines_inserted = len(new_lines)
    if lines_inserted:
        _bulk_insert_lines(session, new_lines)

    # ------------------------------------------------------------------ derived facts
    skip_bids = existing_line_ids if commit_mode == "append" else set()
    fact_ar = D.derive_ar(linked)
    fact_ap = D.derive_ap(linked)
    fact_sales = D.derive_sales(linked)
    fact_com = D.derive_com(linked)

    ar_inserted = _insert_fact_ar(session, fact_ar, skip_bids)
    ap_inserted = _insert_fact_ap(session, fact_ap, skip_bids)
    sales_inserted = _insert_fact_sales(session, fact_sales, skip_bids)
    com_inserted = _insert_fact_com(session, fact_com, skip_bids)

    # ------------------------------------------------------------------ meta + snapshot
    entity_prefix = prefixes[0] if prefixes else None
    fiscal_year_val = years[0] if years else None

    load_row = session.execute(
        text("""
            INSERT INTO meta_dataset_load
              (dataset, legal_entity_code, fiscal_year, row_count, content_hash,
               loaded_at, loaded_by, scope_entity_prefixes, scope_fiscal_years,
               commit_mode, snapshot_captured)
            VALUES (:ds, :le, :fy, :rc, :h, :la, :lb, :pfx, :fys, :cm, FALSE)
            RETURNING load_id
        """),
        {
            "ds": dataset,
            "le": entity_prefix,
            "fy": fiscal_year_val,
            "rc": len(linked),
            "h": content_hash(lines_df),
            "la": datetime.now(timezone.utc),
            "lb": loaded_by,
            "pfx": prefixes or None,
            "fys": years or None,
            "cm": commit_mode,
        },
    ).fetchone()
    load_id = int(load_row[0]) if load_row else None

    if load_id is not None and prefixes and years:
        capture_gl_snapshot(session, load_id, prefixes, years)
        session.execute(
            text("UPDATE meta_dataset_load SET snapshot_captured = TRUE WHERE load_id = :id"),
            {"id": load_id},
        )

    if auto_commit:
        session.commit()
    logger.info(
        "load_canonical: %d entries, %d lines, %d ar, %d ap, %d sales, %d com inserted",
        entries_inserted, lines_inserted, ar_inserted, ap_inserted, sales_inserted, com_inserted,
    )

    skipped = (
        len(existing_line_ids.intersection(
            line_rows["booking_line_id"].tolist() if "booking_line_id" in line_rows.columns else []
        ))
        if commit_mode == "append"
        else 0
    )

    return {
        "load_id": load_id,
        "entries": entries_inserted,
        "lines": lines_inserted,
        "ar": ar_inserted,
        "ap": ap_inserted,
        "sales": sales_inserted,
        "com": com_inserted,
        "skipped": skipped,
        "loaded_at": datetime.now(timezone.utc).isoformat(),
        "commit_mode": commit_mode,
        "scope_entity_prefixes": prefixes,
        "scope_fiscal_years": years,
    }


# --------------------------------------------------------------------------- #
# Pure account-coverage filter helper (DB-free, fully unit-testable)
# --------------------------------------------------------------------------- #

def _filter_by_account_coverage_pure(
    canonical: pd.DataFrame,
    known_keys: set[str],
) -> tuple[pd.DataFrame, int, float, int]:
    """Filter canonical rows to those whose (account_number_group, fiscal_year) is in known_keys.

    Parameters
    ----------
    canonical : pd.DataFrame
        Must have ``account_number_group`` and ``fiscal_year`` columns.
    known_keys : set[str]
        Set of ``"ang|fy"`` strings representing rows present in dim_gl_account.

    Returns
    -------
    (kept_df, n_dropped, sum_amount_dropped, n_unmapped_accounts)
    """
    if "account_number_group" not in canonical.columns or "fiscal_year" not in canonical.columns:
        return canonical, 0, 0.0, 0

    canonical_keys = (
        canonical["account_number_group"].astype(str)
        + "|"
        + canonical["fiscal_year"].astype(int).astype(str)
    )
    mask_keep = canonical_keys.isin(known_keys)
    dropped = canonical[~mask_keep]
    n_drop = len(dropped)
    amt_col = "amount" if "amount" in dropped.columns else None
    sum_amt = float(pd.to_numeric(dropped[amt_col], errors="coerce").sum()) if amt_col and n_drop else 0.0
    n_unmapped = int(dropped["account_number_group"].nunique()) if n_drop else 0
    return canonical[mask_keep].reset_index(drop=True), n_drop, sum_amt, n_unmapped


# --------------------------------------------------------------------------- #
# Private bulk-insert helpers (no iterrows — executemany via list of param dicts)
# --------------------------------------------------------------------------- #

def _n(v, default=None):
    """Return v as Python scalar or default when null/NaN.  Inline micro-helper."""
    if v is None:
        return default
    try:
        if pd.isna(v):
            return default
    except (TypeError, ValueError):
        pass
    return v


def _bulk_insert_entries(session: Session, new_entries: pd.DataFrame) -> None:
    """Bulk-insert fact_gl_entry rows via executemany in BATCH_SIZE chunks."""
    from sqlalchemy import text

    sql = text("""
        INSERT INTO fact_gl_entry
          (journal_entry_group_number, fiscal_year, fiscal_period, entry_type,
           posting_date, document_date, document_type_code, reference_document_number,
           currency_code, header_note, source_system)
        VALUES
          (:jegn, :fy, :fp, :et, :pd, :dd, :dtc, :rdn, :cc, :hn, :ss)
        ON CONFLICT (journal_entry_group_number, fiscal_year) DO NOTHING
    """)
    params = [
        {
            "jegn": row["journal_entry_group_number"],
            "fy":   int(row["fiscal_year"]),
            "fp":   int(row["fiscal_period"]) if _n(row.get("fiscal_period")) is not None else None,
            "et":   _n(row.get("entry_type"), "actual"),
            "pd":   _n(row.get("posting_date")),
            "dd":   _n(row.get("document_date")),
            "dtc":  _n(row.get("document_type_code")),
            "rdn":  _n(row.get("reference_document_number")),
            "cc":   _n(row.get("currency_code"), "EUR") or "EUR",
            "hn":   _n(row.get("header_note")),
            "ss":   _n(row.get("source_system"), "unknown"),
        }
        for row in new_entries.to_dict("records")
    ]
    for i in range(0, len(params), BATCH_SIZE):
        session.execute(sql, params[i : i + BATCH_SIZE])


def _bulk_insert_lines(session: Session, new_lines: pd.DataFrame) -> None:
    """Bulk-insert fact_gl_line rows via executemany in BATCH_SIZE chunks."""
    from sqlalchemy import text

    sql = text("""
        INSERT INTO fact_gl_line
          (journal_entry_group_number, fiscal_year, line_number, booking_line_id,
           account_number_group, amount, vat_amount, line_note,
           customer_id, supplier_id, posting_type, source_system)
        VALUES
          (:jegn, :fy, :ln, :bid, :ang, :amt, :vat, :lnote,
           :cust, :supp, :pt, :ss)
        ON CONFLICT (journal_entry_group_number, fiscal_year, line_number) DO NOTHING
    """)
    params = [
        {
            "jegn":  row["journal_entry_group_number"],
            "fy":    int(row["fiscal_year"]),
            "ln":    int(row["line_number"]),
            "bid":   int(row["booking_line_id"]),
            "ang":   row.get("account_number_group"),
            "amt":   float(row.get("amount") or 0),
            "vat":   float(row["vat_amount"]) if _n(row.get("vat_amount")) is not None else None,
            "lnote": _n(row.get("line_note")),
            "cust":  _n(row.get("customer_id")),
            "supp":  _n(row.get("supplier_id")),
            "pt":    _n(row.get("posting_type")),
            "ss":    _n(row.get("source_system"), "unknown"),
        }
        for row in new_lines.to_dict("records")
    ]
    for i in range(0, len(params), BATCH_SIZE):
        session.execute(sql, params[i : i + BATCH_SIZE])


def _insert_fact_ar(session: Session, fact_ar: pd.DataFrame, skip_bids: set[int]) -> int:
    from sqlalchemy import text

    candidates = fact_ar[~fact_ar["booking_line_id"].astype(int).isin(skip_bids)].reset_index(drop=True)
    inserted = len(candidates)
    if not inserted:
        return 0

    sql = text("""
        INSERT INTO fact_ar
          (booking_line_id, journal_entry_group_number, fiscal_year, line_number,
           account_number_group, customer_id, posting_date, document_date, due_date,
           amount, reference_document_number, entry_type, link_method, source_system)
        VALUES
          (:bid, :jegn, :fy, :ln, :ang, :cust, :pd, :dd, :du,
           :amt, :rdn, :et, :lm, :ss)
        ON CONFLICT (booking_line_id) DO NOTHING
    """)
    params = [
        {
            "bid":  int(row["booking_line_id"]),
            "jegn": row.get("journal_entry_group_number"),
            "fy":   int(row.get("fiscal_year", 0)),
            "ln":   int(row.get("line_number", 0)),
            "ang":  row.get("account_number_group"),
            "cust": _n(row.get("customer_id")),
            "pd":   _n(row.get("posting_date")),
            "dd":   _n(row.get("document_date")),
            "du":   _n(row.get("due_date")),
            "amt":  float(row.get("amount") or 0),
            "rdn":  _n(row.get("reference_document_number")),
            "et":   _n(row.get("entry_type"), "actual"),
            "lm":   _n(row.get("link_method"), "none"),
            "ss":   _n(row.get("source_system"), "unknown"),
        }
        for row in candidates.to_dict("records")
    ]
    for i in range(0, len(params), BATCH_SIZE):
        session.execute(sql, params[i : i + BATCH_SIZE])
    return inserted


def _insert_fact_ap(session: Session, fact_ap: pd.DataFrame, skip_bids: set[int]) -> int:
    from sqlalchemy import text

    candidates = fact_ap[~fact_ap["booking_line_id"].astype(int).isin(skip_bids)].reset_index(drop=True)
    inserted = len(candidates)
    if not inserted:
        return 0

    sql = text("""
        INSERT INTO fact_ap
          (booking_line_id, journal_entry_group_number, fiscal_year, line_number,
           account_number_group, supplier_id, posting_date, document_date, due_date,
           amount, reference_document_number, entry_type, link_method, source_system)
        VALUES
          (:bid, :jegn, :fy, :ln, :ang, :supp, :pd, :dd, :du,
           :amt, :rdn, :et, :lm, :ss)
        ON CONFLICT (booking_line_id) DO NOTHING
    """)
    params = [
        {
            "bid":  int(row["booking_line_id"]),
            "jegn": row.get("journal_entry_group_number"),
            "fy":   int(row.get("fiscal_year", 0)),
            "ln":   int(row.get("line_number", 0)),
            "ang":  row.get("account_number_group"),
            "supp": _n(row.get("supplier_id")),
            "pd":   _n(row.get("posting_date")),
            "dd":   _n(row.get("document_date")),
            "du":   _n(row.get("due_date")),
            "amt":  float(row.get("amount") or 0),
            "rdn":  _n(row.get("reference_document_number")),
            "et":   _n(row.get("entry_type"), "actual"),
            "lm":   _n(row.get("link_method"), "none"),
            "ss":   _n(row.get("source_system"), "unknown"),
        }
        for row in candidates.to_dict("records")
    ]
    for i in range(0, len(params), BATCH_SIZE):
        session.execute(sql, params[i : i + BATCH_SIZE])
    return inserted


def _insert_fact_sales(session: Session, fact_sales: pd.DataFrame, skip_bids: set[int]) -> int:
    from sqlalchemy import text

    candidates = fact_sales[~fact_sales["booking_line_id"].astype(int).isin(skip_bids)].reset_index(drop=True)
    inserted = len(candidates)
    if not inserted:
        return 0

    sql = text("""
        INSERT INTO fact_sales
          (booking_line_id, journal_entry_group_number, fiscal_year,
           account_number_group, customer_id, posting_date,
           gross_sales, link_method, entry_type, source_system)
        VALUES
          (:bid, :jegn, :fy, :ang, :cust, :pd, :gs, :lm, :et, :ss)
        ON CONFLICT (booking_line_id) DO NOTHING
    """)
    params = [
        {
            "bid":  int(row["booking_line_id"]),
            "jegn": row.get("journal_entry_group_number"),
            "fy":   int(row.get("fiscal_year", 0)),
            "ang":  row.get("account_number_group"),
            "cust": _n(row.get("customer_id")),
            "pd":   _n(row.get("posting_date")),
            "gs":   float(row.get("gross_sales") or 0),
            "lm":   _n(row.get("link_method"), "none"),
            "et":   _n(row.get("entry_type"), "actual"),
            "ss":   _n(row.get("source_system"), "unknown"),
        }
        for row in candidates.to_dict("records")
    ]
    for i in range(0, len(params), BATCH_SIZE):
        session.execute(sql, params[i : i + BATCH_SIZE])
    return inserted


def _insert_fact_com(session: Session, fact_com: pd.DataFrame, skip_bids: set[int]) -> int:
    from sqlalchemy import text

    candidates = fact_com[~fact_com["booking_line_id"].astype(int).isin(skip_bids)].reset_index(drop=True)
    inserted = len(candidates)
    if not inserted:
        return 0

    sql = text("""
        INSERT INTO fact_com
          (booking_line_id, journal_entry_group_number, fiscal_year,
           account_number_group, supplier_id, posting_date,
           cost_of_materials, link_method, entry_type, source_system)
        VALUES
          (:bid, :jegn, :fy, :ang, :supp, :pd, :com, :lm, :et, :ss)
        ON CONFLICT (booking_line_id) DO NOTHING
    """)
    params = [
        {
            "bid":  int(row["booking_line_id"]),
            "jegn": row.get("journal_entry_group_number"),
            "fy":   int(row.get("fiscal_year", 0)),
            "ang":  row.get("account_number_group"),
            "supp": _n(row.get("supplier_id")),
            "pd":   _n(row.get("posting_date")),
            "com":  float(row.get("cost_of_materials") or 0),
            "lm":   _n(row.get("link_method"), "none"),
            "et":   _n(row.get("entry_type"), "actual"),
            "ss":   _n(row.get("source_system"), "unknown"),
        }
        for row in candidates.to_dict("records")
    ]
    for i in range(0, len(params), BATCH_SIZE):
        session.execute(sql, params[i : i + BATCH_SIZE])
    return inserted


# --------------------------------------------------------------------------- #
# DF2 — Legal-entity dimension loader
# --------------------------------------------------------------------------- #

def load_legal_entity(
    session: Session,
    entity_prefix: str,
    entity_name: str | None = None,
    is_consolidation: bool = False,
    country_code: str | None = None,
    default_currency: str = "EUR",
    source_system: str | None = None,
) -> None:
    """Idempotent upsert of one row into ``dim_legal_entity``.

    ``legal_entity_code`` is derived as ``entity_prefix`` (the 2-char prefix is
    also a unique key per the schema).  If the row already exists the mutable
    columns are updated; the PK is never changed.

    ``entity_name`` defaults to ``entity_prefix`` when not supplied so the row
    is always non-null on ``entity_name``.

    Parameters
    ----------
    session : sqlalchemy.orm.Session
        Open session; **caller is responsible for commit/rollback**.
    entity_prefix : str
        2-character entity prefix (must match ``dim_legal_entity.entity_prefix``).
    entity_name : str | None
        Human-readable name; defaults to ``entity_prefix`` when omitted.
    is_consolidation : bool
        True for a consolidation entity (IC eliminations etc.).
    country_code : str | None
        ISO 3-char country code (soft reference; no FK enforced here).
    default_currency : str
        ISO currency code; defaults to 'EUR'.
    source_system : str | None
        Originating system identifier.
    """
    from sqlalchemy import text

    name = entity_name or entity_prefix

    session.execute(
        text("""
            INSERT INTO dim_legal_entity
              (legal_entity_code, entity_prefix, entity_name,
               is_consolidation, country_code, default_currency, source_system)
            VALUES
              (:code, :ep, :name, :ic, :cc, :cur, :ss)
            ON CONFLICT (legal_entity_code) DO UPDATE SET
              entity_name      = EXCLUDED.entity_name,
              is_consolidation = EXCLUDED.is_consolidation,
              country_code     = EXCLUDED.country_code,
              default_currency = EXCLUDED.default_currency,
              source_system    = EXCLUDED.source_system
        """),
        {
            "code": entity_prefix,   # legal_entity_code == entity_prefix (2-char natural key)
            "ep": entity_prefix,
            "name": name,
            "ic": is_consolidation,
            "cc": country_code,
            "cur": default_currency,
            "ss": source_system,
        },
    )
    logger.debug("load_legal_entity: upserted entity_prefix=%r name=%r", entity_prefix, name)


# --------------------------------------------------------------------------- #
# DF3 — Partner dimension loaders
# --------------------------------------------------------------------------- #

def _partner_name_update_sql(*, table: str, id_col: str) -> str:
    """UPSERT fragment: master names win over derive_partner_dims placeholders."""
    return f"""
                INSERT INTO {table}
                  ({id_col}, debtor_number, name_line_1, name_line_2,
                   country_code, region_code, city, postal_code,
                   default_currency, source_system, updated_at)
                VALUES
                  (:cid, :dn, :n1, :n2, :cc, :rc, :city, :pc, :cur, :ss, NOW())
                ON CONFLICT ({id_col}) DO UPDATE SET
                  debtor_number    = COALESCE(EXCLUDED.debtor_number,    {table}.debtor_number),
                  name_line_1      = CASE
                    WHEN EXCLUDED.source_system = 'ms_business_central'
                         AND EXCLUDED.name_line_1 IS NOT NULL
                      THEN EXCLUDED.name_line_1
                    WHEN EXCLUDED.name_line_1 IS NOT NULL
                         AND ({table}.name_line_1 IS NULL
                              OR {table}.name_line_1 LIKE 'Customer %'
                              OR {table}.name_line_1 LIKE 'Supplier %')
                      THEN EXCLUDED.name_line_1
                    ELSE COALESCE(EXCLUDED.name_line_1, {table}.name_line_1)
                  END,
                  name_line_2      = COALESCE(EXCLUDED.name_line_2,      {table}.name_line_2),
                  country_code     = COALESCE(EXCLUDED.country_code,     {table}.country_code),
                  region_code      = COALESCE(EXCLUDED.region_code,      {table}.region_code),
                  city             = COALESCE(EXCLUDED.city,             {table}.city),
                  postal_code      = COALESCE(EXCLUDED.postal_code,      {table}.postal_code),
                  default_currency = COALESCE(EXCLUDED.default_currency, {table}.default_currency),
                  source_system    = COALESCE(EXCLUDED.source_system,    {table}.source_system),
                  updated_at       = NOW()
            """


def _supplier_name_update_sql() -> str:
    return """
                INSERT INTO dim_supplier
                  (supplier_id, creditor_number, name_line_1, name_line_2,
                   country_code, region_code, city, postal_code,
                   default_currency, source_system, updated_at)
                VALUES
                  (:sid, :cn, :n1, :n2, :cc, :rc, :city, :pc, :cur, :ss, NOW())
                ON CONFLICT (supplier_id) DO UPDATE SET
                  creditor_number  = COALESCE(EXCLUDED.creditor_number,  dim_supplier.creditor_number),
                  name_line_1      = CASE
                    WHEN EXCLUDED.source_system = 'ms_business_central'
                         AND EXCLUDED.name_line_1 IS NOT NULL
                      THEN EXCLUDED.name_line_1
                    WHEN EXCLUDED.name_line_1 IS NOT NULL
                         AND (dim_supplier.name_line_1 IS NULL
                              OR dim_supplier.name_line_1 LIKE 'Customer %'
                              OR dim_supplier.name_line_1 LIKE 'Supplier %')
                      THEN EXCLUDED.name_line_1
                    ELSE COALESCE(EXCLUDED.name_line_1, dim_supplier.name_line_1)
                  END,
                  name_line_2      = COALESCE(EXCLUDED.name_line_2,      dim_supplier.name_line_2),
                  country_code     = COALESCE(EXCLUDED.country_code,     dim_supplier.country_code),
                  region_code      = COALESCE(EXCLUDED.region_code,      dim_supplier.region_code),
                  city             = COALESCE(EXCLUDED.city,             dim_supplier.city),
                  postal_code      = COALESCE(EXCLUDED.postal_code,      dim_supplier.postal_code),
                  default_currency = COALESCE(EXCLUDED.default_currency, dim_supplier.default_currency),
                  source_system    = COALESCE(EXCLUDED.source_system,    dim_supplier.source_system),
                  updated_at       = NOW()
            """


def load_partners(
    session: Session,
    customers_df: pd.DataFrame,
    suppliers_df: pd.DataFrame,
) -> dict[str, int]:
    """Idempotent upsert of partner rows into dim_customer and dim_supplier.

    Strategy (COALESCE-on-update, master-aware):
      - ON CONFLICT (customer_id / supplier_id) DO UPDATE only overwrites a
        column when the incoming value is non-null.  BC master loads
        (``source_system = ms_business_central``) always refresh ``name_line_1``.
        Placeholder names from ``derive_partner_dims`` (``Customer …`` /
        ``Supplier …``) are overwritten when a richer name arrives.

    Optional geo dimension rows (dim_country, dim_region) are **not** inserted
    here; those are static reference data loaded separately.

    Parameters
    ----------
    session : sqlalchemy.orm.Session
        Open session; **caller is responsible for commit/rollback**.
    customers_df : pd.DataFrame
        Output of ``etl.derive.extract_partners()[0]``.  Must contain
        ``customer_id`` and ``debtor_number``; all other columns are optional.
    suppliers_df : pd.DataFrame
        Output of ``etl.derive.extract_partners()[1]``.  Must contain
        ``supplier_id`` and ``creditor_number``; all other columns are optional.

    Returns
    -------
    dict with keys ``customers`` (int) and ``suppliers`` (int) — rows upserted.
    """
    from sqlalchemy import text

    def _v(row, col):
        """Return row[col] as Python scalar or None when absent/null."""
        v = row.get(col)
        return None if v is None or (isinstance(v, float) and pd.isna(v)) else v

    customers_upserted = 0
    for _, row in customers_df.iterrows():
        cid = _v(row, "customer_id")
        if not cid:
            continue
        session.execute(
            text(_partner_name_update_sql(table="dim_customer", id_col="customer_id")),
            {
                "cid": cid,
                "dn":  _v(row, "debtor_number"),
                "n1":  _v(row, "name_line_1"),
                "n2":  _v(row, "name_line_2"),
                "cc":  _v(row, "country_code"),
                "rc":  _v(row, "region_code"),
                "city": _v(row, "city"),
                "pc":  _v(row, "postal_code"),
                "cur": _v(row, "default_currency"),
                "ss":  _v(row, "source_system"),
            },
        )
        customers_upserted += 1

    suppliers_upserted = 0
    for _, row in suppliers_df.iterrows():
        sid = _v(row, "supplier_id")
        if not sid:
            continue
        session.execute(
            text(_supplier_name_update_sql()),
            {
                "sid": sid,
                "cn":  _v(row, "creditor_number"),
                "n1":  _v(row, "name_line_1"),
                "n2":  _v(row, "name_line_2"),
                "cc":  _v(row, "country_code"),
                "rc":  _v(row, "region_code"),
                "city": _v(row, "city"),
                "pc":  _v(row, "postal_code"),
                "cur": _v(row, "default_currency"),
                "ss":  _v(row, "source_system"),
            },
        )
        suppliers_upserted += 1

    logger.debug(
        "load_partners: %d customers, %d suppliers upserted",
        customers_upserted, suppliers_upserted,
    )
    return {"customers": customers_upserted, "suppliers": suppliers_upserted}


# --------------------------------------------------------------------------- #
# DF1 — Account-mapping dimension loader
# --------------------------------------------------------------------------- #

def delete_orphan_account_mappings(
    session: Session,
    fiscal_years: list[int],
    entity_prefixes: list[str],
    keys_to_keep: set[tuple[str, int]],
) -> int:
    """Remove dim_gl_account rows in FY/prefix scope that are absent from the upload.

    Deletes child dim_gl_na / dim_gl_cf rows first. Raises ValueError when any
    orphan still has fact_gl_line postings (replace cannot proceed).
    """
    from sqlalchemy import text

    if not fiscal_years or not entity_prefixes:
        return 0

    rows = session.execute(
        text(
            "SELECT account_number_group, fiscal_year FROM dim_gl_account "
            "WHERE fiscal_year = ANY(:fys) AND entity_prefix = ANY(:prefixes)"
        ),
        {"fys": fiscal_years, "prefixes": entity_prefixes},
    ).fetchall()

    to_delete = [
        (str(r[0]), int(r[1]))
        for r in rows
        if (str(r[0]), int(r[1])) not in keys_to_keep
    ]
    if not to_delete:
        return 0

    ang_list = list({k[0] for k in to_delete})
    fy_list = list({k[1] for k in to_delete})
    gl_rows = session.execute(
        text(
            "SELECT DISTINCT account_number_group, fiscal_year FROM fact_gl_line "
            "WHERE account_number_group = ANY(:angs) AND fiscal_year = ANY(:fys)"
        ),
        {"angs": ang_list, "fys": fy_list},
    ).fetchall()
    blocked = {(str(r[0]), int(r[1])) for r in gl_rows}
    blocked_orphans = [k for k in to_delete if k in blocked]
    if blocked_orphans:
        raise ValueError(
            f"Cannot replace mapping: {len(blocked_orphans)} account(s) have GL postings "
            "and cannot be removed."
        )

    deleted = 0
    for ang, fy in to_delete:
        session.execute(
            text(
                "DELETE FROM dim_gl_na "
                "WHERE account_number_group = :ang AND fiscal_year = :fy"
            ),
            {"ang": ang, "fy": fy},
        )
        session.execute(
            text(
                "DELETE FROM dim_gl_cf "
                "WHERE account_number_group = :ang AND fiscal_year = :fy"
            ),
            {"ang": ang, "fy": fy},
        )
        session.execute(
            text(
                "DELETE FROM dim_gl_account "
                "WHERE account_number_group = :ang AND fiscal_year = :fy"
            ),
            {"ang": ang, "fy": fy},
        )
        deleted += 1

    logger.info("delete_orphan_account_mappings: removed %d account(s)", deleted)
    return deleted


def load_account_mapping(
    session: Session,
    mapping_df: pd.DataFrame,
    auto_commit: bool = True,
) -> dict[str, int]:
    """Transactional UPSERT of a canonical mapping DataFrame into dim_gl_account,
    dim_gl_na, and dim_gl_cf.

    The *mapping_df* must be the output of
    ``etl.mapping_account.apply_account_mapping`` — i.e. it contains at minimum:
    ``account_number_group``, ``fiscal_year``, ``gl_account_id``, ``account_name``,
    ``level_0`` … ``level_4``, ``l4_sub``, ``level_2_sort``, ``level_3_sort``,
    ``is_ic``, ``source_system``, and optionally the NA/CF columns.

    Strategy:
      - dim_gl_account: ON CONFLICT (account_number_group, fiscal_year) DO UPDATE
        for all dim_gl_account columns (idempotent re-upload).
      - dim_gl_na: upserted only for rows where at least one NA field
        (l6_na_mapping, l7_na_description) is non-null.
      - dim_gl_cf: upserted only for rows where at least one CF field
        (cf_l1 … cf_l5, cf_mapping) is non-null.

    All three tables are written in a single transaction; on error the session
    is rolled back before the exception propagates.

    Parameters
    ----------
    session : sqlalchemy.orm.Session
        An open SQLAlchemy session bound to the target database.
    mapping_df : pd.DataFrame
        Canonical mapping DataFrame from apply_account_mapping.

    Returns
    -------
    dict with keys: accounts (int), na (int), cf (int) — rows upserted per table.

    Raises
    ------
    Exception
        Any DB error causes a rollback before re-raising.
    """
    from sqlalchemy import text

    from etl.mapping_account import NA_FIELDS, CF_FIELDS

    def _val(row, col, default=None):
        """Return row[col] as Python scalar, or default when null/absent."""
        v = row.get(col, default)
        return default if pd.isna(v) else v

    accounts_upserted = 0
    na_upserted = 0
    cf_upserted = 0

    try:
        for _, row in mapping_df.iterrows():
            ang = row["account_number_group"]
            fy = int(row["fiscal_year"])

            # -------------------------------------------------------- dim_gl_account
            session.execute(
                text("""
                    INSERT INTO dim_gl_account
                      (account_number_group, fiscal_year, gl_account_id, account_name,
                       level_0, level_1, level_2, level_3, level_4, l4_sub,
                       level_2_sort, level_3_sort, is_ic, source_system)
                    VALUES
                      (:ang, :fy, :glid, :aname,
                       :l0, :l1, :l2, :l3, :l4, :l4s,
                       :l2s, :l3s, :ic, :ss)
                    ON CONFLICT (account_number_group, fiscal_year) DO UPDATE SET
                      gl_account_id  = EXCLUDED.gl_account_id,
                      account_name   = EXCLUDED.account_name,
                      level_0        = EXCLUDED.level_0,
                      level_1        = EXCLUDED.level_1,
                      level_2        = EXCLUDED.level_2,
                      level_3        = EXCLUDED.level_3,
                      level_4        = EXCLUDED.level_4,
                      l4_sub         = EXCLUDED.l4_sub,
                      level_2_sort   = EXCLUDED.level_2_sort,
                      level_3_sort   = EXCLUDED.level_3_sort,
                      is_ic          = EXCLUDED.is_ic,
                      source_system  = EXCLUDED.source_system
                """),
                {
                    "ang": ang,
                    "fy": fy,
                    "glid": _val(row, "gl_account_id"),
                    "aname": _val(row, "account_name"),
                    "l0": _val(row, "level_0"),
                    "l1": _val(row, "level_1"),
                    "l2": _val(row, "level_2"),
                    "l3": _val(row, "level_3"),
                    "l4": _val(row, "level_4"),
                    "l4s": _val(row, "l4_sub"),
                    "l2s": int(row["level_2_sort"]) if pd.notna(row.get("level_2_sort")) else None,
                    "l3s": int(row["level_3_sort"]) if pd.notna(row.get("level_3_sort")) else None,
                    "ic": bool(row.get("is_ic", False)),
                    "ss": _val(row, "source_system", "unknown"),
                },
            )
            accounts_upserted += 1

            # -------------------------------------------------------- dim_gl_na (conditional)
            has_na = any(
                pd.notna(row.get(f)) and str(row.get(f, "")).strip() != ""
                for f in NA_FIELDS
            )
            if has_na:
                session.execute(
                    text("""
                        INSERT INTO dim_gl_na
                          (account_number_group, fiscal_year, l6_na_mapping, l7_na_description)
                        VALUES (:ang, :fy, :l6, :l7)
                        ON CONFLICT (account_number_group, fiscal_year) DO UPDATE SET
                          l6_na_mapping     = EXCLUDED.l6_na_mapping,
                          l7_na_description = EXCLUDED.l7_na_description
                    """),
                    {
                        "ang": ang,
                        "fy": fy,
                        "l6": _val(row, "l6_na_mapping"),
                        "l7": _val(row, "l7_na_description"),
                    },
                )
                na_upserted += 1

            # -------------------------------------------------------- dim_gl_cf (conditional)
            has_cf = any(
                pd.notna(row.get(f)) and str(row.get(f, "")).strip() != ""
                for f in CF_FIELDS
            )
            if has_cf:
                session.execute(
                    text("""
                        INSERT INTO dim_gl_cf
                          (account_number_group, fiscal_year,
                           l1, l2, l3, l4, l5, cf_mapping)
                        VALUES (:ang, :fy, :l1, :l2, :l3, :l4, :l5, :cfm)
                        ON CONFLICT (account_number_group, fiscal_year) DO UPDATE SET
                          l1         = EXCLUDED.l1,
                          l2         = EXCLUDED.l2,
                          l3         = EXCLUDED.l3,
                          l4         = EXCLUDED.l4,
                          l5         = EXCLUDED.l5,
                          cf_mapping = EXCLUDED.cf_mapping
                    """),
                    {
                        "ang": ang,
                        "fy": fy,
                        "l1": _val(row, "cf_l1"),
                        "l2": _val(row, "cf_l2"),
                        "l3": _val(row, "cf_l3"),
                        "l4": _val(row, "cf_l4"),
                        "l5": _val(row, "cf_l5"),
                        "cfm": _val(row, "cf_mapping"),
                    },
                )
                cf_upserted += 1

        if auto_commit:
            session.commit()
        logger.info(
            "load_account_mapping: %d accounts, %d na, %d cf upserted",
            accounts_upserted, na_upserted, cf_upserted,
        )

    except Exception:
        session.rollback()
        logger.exception("load_account_mapping: rollback on error")
        raise

    return {"accounts": accounts_upserted, "na": na_upserted, "cf": cf_upserted}


# --------------------------------------------------------------------------- #
# DF5 — Plan / Forecast loaders
# --------------------------------------------------------------------------- #

def load_plan(
    session: Session,
    gl_plan_df: pd.DataFrame,
    sales_plan_df: pd.DataFrame,
    com_plan_df: pd.DataFrame | None = None,
) -> dict[str, int]:
    """Transactional idempotent UPSERT of plan rows into fact_gl_plan, fact_sales_plan
    and (optionally) fact_com_plan.

    All DataFrames are the direct output of ``etl.plan_synth.generate_plan``.

    fact_gl_plan PK:    (account_number_group, fiscal_year, fiscal_period, scenario)
    fact_sales_plan PK: (customer_id, fiscal_year, fiscal_period, scenario)
    fact_com_plan PK:   (supplier_id, fiscal_year, fiscal_period, scenario)

    ON CONFLICT DO UPDATE: only ``amount`` / ``gross_sales_plan`` /
    ``cost_of_materials_plan`` and ``is_synthetic`` (and ``source_system`` for
    gl/com) are overwritten on re-run (idempotent re-generation).

    Parameters
    ----------
    session : sqlalchemy.orm.Session
        Open session; this function commits (and rolls back on error).
    gl_plan_df : pd.DataFrame
        Columns: account_number_group, fiscal_year, fiscal_period, scenario,
                 amount, is_synthetic, source_system.
    sales_plan_df : pd.DataFrame
        Columns: customer_id, fiscal_year, fiscal_period, scenario,
                 gross_sales_plan, is_synthetic, source_system.
    com_plan_df : pd.DataFrame | None
        Optional (additive).  Columns: supplier_id, fiscal_year, fiscal_period,
        scenario, cost_of_materials_plan, is_synthetic, source_system.  When None,
        fact_com_plan is left untouched and the returned dict omits the ``com_plan``
        key (backward-compatible 2-table behaviour).

    Returns
    -------
    dict with keys gl_plan (int), sales_plan (int) and — only when ``com_plan_df``
    is supplied — com_plan (int): rows upserted per table.

    Raises
    ------
    Exception
        Any DB error causes a rollback before re-raising.
    """
    from sqlalchemy import text

    gl_upserted = 0
    sales_upserted = 0
    com_upserted = 0

    try:
        # -------------------------------------------------------- fact_gl_plan
        for _, row in gl_plan_df.iterrows():
            session.execute(
                text("""
                    INSERT INTO fact_gl_plan
                      (account_number_group, fiscal_year, fiscal_period, scenario,
                       amount, is_synthetic, source_system)
                    VALUES
                      (:ang, :fy, :fp, :sc, :amt, :syn, :ss)
                    ON CONFLICT (account_number_group, fiscal_year, fiscal_period, scenario)
                    DO UPDATE SET
                      amount       = EXCLUDED.amount,
                      is_synthetic = EXCLUDED.is_synthetic,
                      source_system = EXCLUDED.source_system
                """),
                {
                    "ang": str(row["account_number_group"]),
                    "fy":  int(row["fiscal_year"]),
                    "fp":  int(row["fiscal_period"]),
                    "sc":  str(row["scenario"]),
                    "amt": float(row["amount"]),
                    "syn": bool(row.get("is_synthetic", True)),
                    "ss":  str(row.get("source_system") or "synthetic_plan"),
                },
            )
            gl_upserted += 1

        # -------------------------------------------------------- fact_sales_plan
        for _, row in sales_plan_df.iterrows():
            session.execute(
                text("""
                    INSERT INTO fact_sales_plan
                      (customer_id, fiscal_year, fiscal_period, scenario,
                       gross_sales_plan, is_synthetic)
                    VALUES
                      (:cid, :fy, :fp, :sc, :gsp, :syn)
                    ON CONFLICT (customer_id, fiscal_year, fiscal_period, scenario)
                    DO UPDATE SET
                      gross_sales_plan = EXCLUDED.gross_sales_plan,
                      is_synthetic     = EXCLUDED.is_synthetic
                """),
                {
                    "cid": str(row["customer_id"]),
                    "fy":  int(row["fiscal_year"]),
                    "fp":  int(row["fiscal_period"]),
                    "sc":  str(row["scenario"]),
                    "gsp": float(row["gross_sales_plan"]),
                    "syn": bool(row.get("is_synthetic", True)),
                },
            )
            sales_upserted += 1

        # -------------------------------------------------------- fact_com_plan (optional)
        if com_plan_df is not None:
            for _, row in com_plan_df.iterrows():
                session.execute(
                    text("""
                        INSERT INTO fact_com_plan
                          (supplier_id, fiscal_year, fiscal_period, scenario,
                           cost_of_materials_plan, is_synthetic, source_system)
                        VALUES
                          (:sid, :fy, :fp, :sc, :com, :syn, :ss)
                        ON CONFLICT (supplier_id, fiscal_year, fiscal_period, scenario)
                        DO UPDATE SET
                          cost_of_materials_plan = EXCLUDED.cost_of_materials_plan,
                          is_synthetic           = EXCLUDED.is_synthetic,
                          source_system          = EXCLUDED.source_system
                    """),
                    {
                        "sid": str(row["supplier_id"]),
                        "fy":  int(row["fiscal_year"]),
                        "fp":  int(row["fiscal_period"]),
                        "sc":  str(row["scenario"]),
                        "com": float(row["cost_of_materials_plan"]),
                        "syn": bool(row.get("is_synthetic", True)),
                        "ss":  str(row.get("source_system") or "synthetic_plan"),
                    },
                )
                com_upserted += 1

        session.commit()
        logger.info(
            "load_plan: %d fact_gl_plan, %d fact_sales_plan, %d fact_com_plan rows upserted",
            gl_upserted, sales_upserted, com_upserted,
        )

    except Exception:
        session.rollback()
        logger.exception("load_plan: rollback on error")
        raise

    result = {"gl_plan": gl_upserted, "sales_plan": sales_upserted}
    if com_plan_df is not None:
        result["com_plan"] = com_upserted
    return result
