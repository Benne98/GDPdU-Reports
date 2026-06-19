"""Discover and close partner gaps for Top Customer / Top Supplier analytics.

When ``fact_sales.customer_id`` or ``fact_com.supplier_id`` is NULL, the cockpit
shows "(no partner)".  Master CSVs alone only enrich ``dim_*`` — this module:

  1. Backfills ``fact_gl_line`` partner IDs (trade AR/AP, then journal-unique).
  2. Resolves remaining gaps from the GoBD GL CSV (Source type / Source No.).
  3. Appends synthetic BC master rows to ``Customer Master.csv`` / ``Vendor Master.csv``.
  4. Loads dims and re-derives ``fact_sales`` / ``fact_com``.

Synthetic master rows use ``BAU Name lang = "Customer <No.>"`` / ``"Supplier <No.>"``
when no richer name exists in ``dim_customer`` / ``dim_supplier``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from etl.load_partner_masters import load_customer_master_csv, load_vendor_master_csv
from etl.transform import build_journal_entry_group_number, build_partner_id, normalize_prefix

# Top-entity account scope (matches overview_top_entities + derive_facts).
REVENUE_L3 = ("Net sales",)
MATERIAL_L3 = ("Cost of materials",)
RECEIVABLE_L3 = ("Trade receivables",)
PAYABLE_L3 = ("Trade payables",)

_PREFIX_TO_ENTITY: dict[str, str] = {
    "01": "Atlas",
    "02": "Meridian",
    "03": "Novara",
    "04": "Venturo",
    "05": "Calypto",
}

BC_COLUMNS = [
    "No.",
    "entity",
    "BAU Name lang",
    "BAU Adresse 2 lang",
    "Country/Region Code",
    "City",
    "Post Code",
]

PartnerSide = Literal["customer", "supplier"]


@dataclass(frozen=True)
class ResolvedPartner:
    """One partner discovered for gap closure."""

    side: PartnerSide
    partner_id: str
    partner_number: str
    entity_name: str
    entity_prefix: str
    journal_entry_group_number: str
    fiscal_year: int
    source: str


def _sql_in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def backfill_gl_trade_ar_ap(session: Session) -> dict[str, int]:
    """Strict txn linking: trade receivable→net sales, trade payable→material."""
    rev_l3 = _sql_in(REVENUE_L3)
    mat_l3 = _sql_in(MATERIAL_L3)
    ar_l3 = _sql_in(RECEIVABLE_L3)
    ap_l3 = _sql_in(PAYABLE_L3)

    cust = session.execute(
        text(f"""
            WITH recv AS (
                SELECT r.journal_entry_group_number, r.fiscal_year, MAX(r.customer_id) AS customer_id
                FROM fact_gl_line r
                JOIN dim_gl_account ar
                  ON ar.account_number_group = r.account_number_group
                 AND ar.fiscal_year = r.fiscal_year
                WHERE ar.level_3 IN ({ar_l3})
                  AND r.customer_id IS NOT NULL AND TRIM(r.customer_id) <> ''
                GROUP BY 1, 2 HAVING COUNT(DISTINCT r.customer_id) = 1
            ),
            targets AS (
                SELECT income.booking_line_id, recv.customer_id
                FROM fact_gl_line income
                JOIN recv ON recv.journal_entry_group_number = income.journal_entry_group_number
                         AND recv.fiscal_year = income.fiscal_year
                JOIN dim_gl_account ai
                  ON ai.account_number_group = income.account_number_group
                 AND ai.fiscal_year = income.fiscal_year
                WHERE ai.level_3 IN ({rev_l3})
                  AND (income.customer_id IS NULL OR TRIM(income.customer_id) = '')
            )
            UPDATE fact_gl_line l SET customer_id = t.customer_id
            FROM targets t WHERE l.booking_line_id = t.booking_line_id
        """)
    )
    supp = session.execute(
        text(f"""
            WITH pay AS (
                SELECT p.journal_entry_group_number, p.fiscal_year, MAX(p.supplier_id) AS supplier_id
                FROM fact_gl_line p
                JOIN dim_gl_account ap
                  ON ap.account_number_group = p.account_number_group
                 AND ap.fiscal_year = p.fiscal_year
                WHERE ap.level_3 IN ({ap_l3})
                  AND p.supplier_id IS NOT NULL AND TRIM(p.supplier_id) <> ''
                GROUP BY 1, 2 HAVING COUNT(DISTINCT p.supplier_id) = 1
            ),
            targets AS (
                SELECT mat.booking_line_id, pay.supplier_id
                FROM fact_gl_line mat
                JOIN pay ON pay.journal_entry_group_number = mat.journal_entry_group_number
                        AND pay.fiscal_year = mat.fiscal_year
                JOIN dim_gl_account am
                  ON am.account_number_group = mat.account_number_group
                 AND am.fiscal_year = mat.fiscal_year
                WHERE am.level_3 IN ({mat_l3})
                  AND (mat.supplier_id IS NULL OR TRIM(mat.supplier_id) = '')
            )
            UPDATE fact_gl_line l SET supplier_id = t.supplier_id
            FROM targets t WHERE l.booking_line_id = t.booking_line_id
        """)
    )
    return {
        "trade_ar_customer": int(cust.rowcount or 0),
        "trade_ap_supplier": int(supp.rowcount or 0),
    }


def backfill_gl_journal_unique_partner(session: Session) -> dict[str, int]:
    """Relaxed linking: one distinct partner anywhere in the booking."""
    rev_l3 = _sql_in(REVENUE_L3)
    mat_l3 = _sql_in(MATERIAL_L3)

    cust = session.execute(
        text(f"""
            WITH jcust AS (
                SELECT journal_entry_group_number, fiscal_year, MAX(customer_id) AS customer_id
                FROM fact_gl_line
                WHERE customer_id IS NOT NULL AND TRIM(customer_id) <> ''
                GROUP BY 1, 2 HAVING COUNT(DISTINCT customer_id) = 1
            ),
            targets AS (
                SELECT income.booking_line_id, jc.customer_id
                FROM fact_gl_line income
                JOIN jcust jc ON jc.journal_entry_group_number = income.journal_entry_group_number
                             AND jc.fiscal_year = income.fiscal_year
                JOIN dim_gl_account ai
                  ON ai.account_number_group = income.account_number_group
                 AND ai.fiscal_year = income.fiscal_year
                WHERE ai.level_3 IN ({rev_l3})
                  AND (income.customer_id IS NULL OR TRIM(income.customer_id) = '')
            )
            UPDATE fact_gl_line l SET customer_id = t.customer_id
            FROM targets t WHERE l.booking_line_id = t.booking_line_id
        """)
    )
    supp = session.execute(
        text(f"""
            WITH jsup AS (
                SELECT journal_entry_group_number, fiscal_year, MAX(supplier_id) AS supplier_id
                FROM fact_gl_line
                WHERE supplier_id IS NOT NULL AND TRIM(supplier_id) <> ''
                GROUP BY 1, 2 HAVING COUNT(DISTINCT supplier_id) = 1
            ),
            targets AS (
                SELECT mat.booking_line_id, js.supplier_id
                FROM fact_gl_line mat
                JOIN jsup js ON js.journal_entry_group_number = mat.journal_entry_group_number
                            AND js.fiscal_year = mat.fiscal_year
                JOIN dim_gl_account am
                  ON am.account_number_group = mat.account_number_group
                 AND am.fiscal_year = mat.fiscal_year
                WHERE am.level_3 IN ({mat_l3})
                  AND (mat.supplier_id IS NULL OR TRIM(mat.supplier_id) = '')
            )
            UPDATE fact_gl_line l SET supplier_id = t.supplier_id
            FROM targets t WHERE l.booking_line_id = t.booking_line_id
        """)
    )
    return {
        "journal_unique_customer": int(cust.rowcount or 0),
        "journal_unique_supplier": int(supp.rowcount or 0),
    }


def backfill_gl_from_counterparty_lines(session: Session) -> dict[str, int]:
    """Link net sales / material to partner on trade AR/AP lines in the same booking."""
    rev_l3 = _sql_in(REVENUE_L3)
    mat_l3 = _sql_in(MATERIAL_L3)
    ar_l3 = _sql_in(RECEIVABLE_L3)
    ap_l3 = _sql_in(PAYABLE_L3)

    cust = session.execute(
        text(f"""
            WITH pairs AS (
                SELECT income.journal_entry_group_number AS jegn,
                       income.fiscal_year AS fy,
                       MIN(recv.customer_id) AS customer_id
                FROM fact_gl_line income
                JOIN dim_gl_account ai
                  ON ai.account_number_group = income.account_number_group
                 AND ai.fiscal_year = income.fiscal_year
                JOIN fact_gl_line recv
                  ON recv.journal_entry_group_number = income.journal_entry_group_number
                 AND recv.fiscal_year = income.fiscal_year
                JOIN dim_gl_account ar
                  ON ar.account_number_group = recv.account_number_group
                 AND ar.fiscal_year = recv.fiscal_year
                WHERE TRIM(ai.level_3) IN ({rev_l3})
                  AND TRIM(ar.level_3) IN ({ar_l3})
                  AND (income.customer_id IS NULL OR TRIM(income.customer_id) = '')
                  AND recv.customer_id IS NOT NULL AND TRIM(recv.customer_id) <> ''
                GROUP BY 1, 2
            )
            UPDATE fact_gl_line l
            SET customer_id = p.customer_id
            FROM pairs p, dim_gl_account a
            WHERE l.journal_entry_group_number = p.jegn
              AND l.fiscal_year = p.fy
              AND a.account_number_group = l.account_number_group
              AND a.fiscal_year = l.fiscal_year
              AND TRIM(a.level_3) IN ({rev_l3})
              AND (l.customer_id IS NULL OR TRIM(l.customer_id) = '')
        """)
    )
    supp = session.execute(
        text(f"""
            WITH pairs AS (
                SELECT mat.journal_entry_group_number AS jegn,
                       mat.fiscal_year AS fy,
                       MIN(pay.supplier_id) AS supplier_id
                FROM fact_gl_line mat
                JOIN dim_gl_account am
                  ON am.account_number_group = mat.account_number_group
                 AND am.fiscal_year = mat.fiscal_year
                JOIN fact_gl_line pay
                  ON pay.journal_entry_group_number = mat.journal_entry_group_number
                 AND pay.fiscal_year = mat.fiscal_year
                JOIN dim_gl_account ap
                  ON ap.account_number_group = pay.account_number_group
                 AND ap.fiscal_year = pay.fiscal_year
                WHERE TRIM(am.level_3) IN ({mat_l3})
                  AND TRIM(ap.level_3) IN ({ap_l3})
                  AND (mat.supplier_id IS NULL OR TRIM(mat.supplier_id) = '')
                  AND pay.supplier_id IS NOT NULL AND TRIM(pay.supplier_id) <> ''
                GROUP BY 1, 2
            )
            UPDATE fact_gl_line l
            SET supplier_id = p.supplier_id
            FROM pairs p, dim_gl_account a
            WHERE l.journal_entry_group_number = p.jegn
              AND l.fiscal_year = p.fy
              AND a.account_number_group = l.account_number_group
              AND a.fiscal_year = l.fiscal_year
              AND TRIM(a.level_3) IN ({mat_l3})
              AND (l.supplier_id IS NULL OR TRIM(l.supplier_id) = '')
        """)
    )
    return {
        "counterparty_customer": int(cust.rowcount or 0),
        "counterparty_supplier": int(supp.rowcount or 0),
    }


def _unmapped_journal_keys(session: Session, side: PartnerSide) -> set[tuple[str, int]]:
    rev_l3 = _sql_in(REVENUE_L3)
    mat_l3 = _sql_in(MATERIAL_L3)
    if side == "customer":
        sql = f"""
            SELECT DISTINCT f.journal_entry_group_number, f.fiscal_year
            FROM fact_sales f
            JOIN dim_gl_account a
              ON a.account_number_group = f.account_number_group
             AND a.fiscal_year = f.fiscal_year
            WHERE TRIM(a.level_3) IN ({rev_l3})
              AND (f.customer_id IS NULL OR TRIM(f.customer_id) = '')
        """
    else:
        sql = f"""
            SELECT DISTINCT f.journal_entry_group_number, f.fiscal_year
            FROM fact_com f
            JOIN dim_gl_account a
              ON a.account_number_group = f.account_number_group
             AND a.fiscal_year = f.fiscal_year
            WHERE TRIM(a.level_3) IN ({mat_l3})
              AND (f.supplier_id IS NULL OR TRIM(f.supplier_id) = '')
        """
    rows = session.execute(text(sql)).fetchall()
    return {(str(r[0]), int(r[1])) for r in rows}


def build_gobd_partner_index(
    gl_path: Path,
    *,
    require_unique: bool = True,
) -> tuple[dict[tuple[str, int], str], dict[tuple[str, int], str]]:
    """Map (journal_entry_group_number, fiscal_year) → customer_id / supplier_id from GoBD CSV."""
    usecols = ["Entity No", "Year", "Transaction number", "Source type", "Source No."]
    df = pd.read_csv(gl_path, usecols=usecols, dtype=str, keep_default_na=False)

    df["entity_no"] = df["Entity No"].astype(str).str.strip()
    df["txn"] = df["Transaction number"].astype(str).str.strip()
    df["src_no"] = df["Source No."].astype(str).str.strip()
    df["st"] = df["Source type"].astype(str).str.strip().str.lower()
    df["fy"] = pd.to_numeric(df["Year"], errors="coerce")

    valid = (
        df["entity_no"].str.isdigit()
        & df["txn"].ne("")
        & df["src_no"].ne("")
        & df["fy"].notna()
    )
    df = df.loc[valid].copy()
    if df.empty:
        return {}, {}

    def _prefix_series(s: pd.Series) -> pd.Series:
        return s.map(normalize_prefix).astype("string")

    prefix = _prefix_series(df["entity_no"])
    df["jegn"] = build_journal_entry_group_number(prefix, df["txn"])
    df["pid"] = build_partner_id(prefix, df["src_no"])
    df["fy"] = df["fy"].astype(int)

    def _index(side_mask: pd.Series) -> dict[tuple[str, int], str]:
        sub = df.loc[side_mask, ["jegn", "fy", "pid"]].drop_duplicates()
        if sub.empty:
            return {}
        first = sub.groupby(["jegn", "fy"], sort=False)["pid"].first()
        if require_unique:
            nuniq = sub.groupby(["jegn", "fy"], sort=False)["pid"].nunique()
            good = nuniq[nuniq == 1].index
            return {(str(jegn), int(fy)): str(first.loc[(jegn, fy)]) for jegn, fy in good}
        return {(str(jegn), int(fy)): str(pid) for (jegn, fy), pid in first.items()}

    cust_mask = df["st"].isin(["debitor", "debtor", "customer"])
    supp_mask = df["st"].isin(["kreditor", "creditor", "vendor", "supplier"])
    return _index(cust_mask), _index(supp_mask)


def _apply_resolved_to_gl(
    session: Session,
    resolved: list[ResolvedPartner],
    *,
    side: PartnerSide,
) -> None:
    if not resolved:
        return
    rev_l3 = _sql_in(REVENUE_L3)
    mat_l3 = _sql_in(MATERIAL_L3)
    chunk = 1000
    for i in range(0, len(resolved), chunk):
        batch = resolved[i : i + chunk]
        values_sql = ", ".join(
            f"('{r.journal_entry_group_number.replace(chr(39), '')}', {int(r.fiscal_year)}, '{r.partner_id.replace(chr(39), '')}')"
            for r in batch
        )
        if side == "customer":
            session.execute(
                text(f"""
                    WITH mapping (jegn, fy, pid) AS (VALUES {values_sql})
                    UPDATE fact_gl_line l
                    SET customer_id = m.pid
                    FROM mapping m, dim_gl_account a
                    WHERE l.journal_entry_group_number = m.jegn
                      AND l.fiscal_year = m.fy
                      AND a.account_number_group = l.account_number_group
                      AND a.fiscal_year = l.fiscal_year
                      AND TRIM(a.level_3) IN ({rev_l3})
                      AND (l.customer_id IS NULL OR TRIM(l.customer_id) = '')
                """)
            )
        else:
            session.execute(
                text(f"""
                    WITH mapping (jegn, fy, pid) AS (VALUES {values_sql})
                    UPDATE fact_gl_line l
                    SET supplier_id = m.pid
                    FROM mapping m, dim_gl_account a
                    WHERE l.journal_entry_group_number = m.jegn
                      AND l.fiscal_year = m.fy
                      AND a.account_number_group = l.account_number_group
                      AND a.fiscal_year = l.fiscal_year
                      AND TRIM(a.level_3) IN ({mat_l3})
                      AND (l.supplier_id IS NULL OR TRIM(l.supplier_id) = '')
                """)
            )


def _resolve_gaps_with_index(
    session: Session,
    index: dict[tuple[str, int], str],
    *,
    side: PartnerSide,
) -> list[ResolvedPartner]:
    gaps = _unmapped_journal_keys(session, side)
    if not gaps or not index:
        return []

    # Fallback: match by journal id only when fiscal-year key misses (GoBD year drift).
    jegn_only = {j: pid for (j, _fy), pid in index.items()}

    resolved: list[ResolvedPartner] = []
    for jegn, fy in sorted(gaps):
        pid = index.get((jegn, fy)) or jegn_only.get(jegn)
        if not pid:
            continue
        prefix = pid[:2]
        number = pid[2:]
        entity_name = _PREFIX_TO_ENTITY.get(prefix, prefix)
        resolved.append(
            ResolvedPartner(
                side=side,
                partner_id=pid,
                partner_number=number,
                entity_name=entity_name,
                entity_prefix=prefix,
                journal_entry_group_number=jegn,
                fiscal_year=fy,
                source="gobd_csv",
            )
        )
    _apply_resolved_to_gl(session, resolved, side=side)
    return resolved


def resolve_gaps_from_gobd(
    session: Session,
    gl_path: Path,
    *,
    side: PartnerSide,
) -> list[ResolvedPartner]:
    """Resolve unmapped fact journals using GoBD Source No."""
    gaps = _unmapped_journal_keys(session, side)
    if not gaps:
        return []

    cust_idx, supp_idx = build_gobd_partner_index(gl_path)
    index = cust_idx if side == "customer" else supp_idx

    resolved: list[ResolvedPartner] = []
    for jegn, fy in sorted(gaps):
        pid = index.get((jegn, fy))
        if not pid:
            continue
        prefix = pid[:2]
        number = pid[2:]
        entity_name = _PREFIX_TO_ENTITY.get(prefix, prefix)
        resolved.append(
            ResolvedPartner(
                side=side,
                partner_id=pid,
                partner_number=number,
                entity_name=entity_name,
                entity_prefix=prefix,
                journal_entry_group_number=jegn,
                fiscal_year=fy,
                source="gobd_csv",
            )
        )

    _apply_resolved_to_gl(session, resolved, side=side)
    return resolved


def _existing_dim_names(session: Session, side: PartnerSide) -> dict[str, str]:
    table = "dim_customer" if side == "customer" else "dim_supplier"
    id_col = "customer_id" if side == "customer" else "supplier_id"
    rows = session.execute(
        text(f"SELECT {id_col}, name_line_1 FROM {table} WHERE name_line_1 IS NOT NULL")
    ).fetchall()
    return {str(r[0]): str(r[1]) for r in rows if r[1]}


def build_synthetic_master_rows(
    resolved: list[ResolvedPartner],
    *,
    dim_names: dict[str, str],
) -> pd.DataFrame:
    """BC-style master rows for partners not yet in the CSV."""
    records: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in resolved:
        key = (item.entity_name, item.partner_number)
        if key in seen:
            continue
        seen.add(key)
        label_prefix = "Customer" if item.side == "customer" else "Supplier"
        if item.source == "synthetic_journal":
            name = dim_names.get(item.partner_id) or f"{label_prefix} (synthetic) {item.partner_number}"
        else:
            name = dim_names.get(item.partner_id) or f"{label_prefix} {item.partner_number}"
        records.append({
            "No.": item.partner_number,
            "entity": item.entity_name,
            "BAU Name lang": name[:200],
            "BAU Adresse 2 lang": "",
            "Country/Region Code": "",
            "City": "",
            "Post Code": "",
        })
    return pd.DataFrame(records, columns=BC_COLUMNS)


def _partner_rows_from_ids(partner_ids: set[str], side: PartnerSide) -> list[ResolvedPartner]:
    out: list[ResolvedPartner] = []
    for pid in sorted(partner_ids):
        if not pid or len(pid) < 3:
            continue
        prefix = pid[:2]
        out.append(
            ResolvedPartner(
                side=side,
                partner_id=pid,
                partner_number=pid[2:],
                entity_name=_PREFIX_TO_ENTITY.get(prefix, prefix),
                entity_prefix=prefix,
                journal_entry_group_number="",
                fiscal_year=0,
                source="discovered",
            )
        )
    return out


def _partner_ids_on_gl(session: Session, side: PartnerSide) -> set[str]:
    rev_l3 = _sql_in(REVENUE_L3)
    mat_l3 = _sql_in(MATERIAL_L3)
    if side == "customer":
        sql = f"""
            SELECT DISTINCT l.customer_id
            FROM fact_gl_line l
            JOIN dim_gl_account a
              ON a.account_number_group = l.account_number_group
             AND a.fiscal_year = l.fiscal_year
            WHERE TRIM(a.level_3) IN ({rev_l3})
              AND l.customer_id IS NOT NULL AND TRIM(l.customer_id) <> ''
        """
    else:
        sql = f"""
            SELECT DISTINCT l.supplier_id
            FROM fact_gl_line l
            JOIN dim_gl_account a
              ON a.account_number_group = l.account_number_group
             AND a.fiscal_year = l.fiscal_year
            WHERE TRIM(a.level_3) IN ({mat_l3})
              AND l.supplier_id IS NOT NULL AND TRIM(l.supplier_id) <> ''
        """
    return {str(r[0]) for r in session.execute(text(sql)).fetchall()}


def _partner_ids_in_csv(csv_path: Path, side: PartnerSide) -> set[str]:
    if not csv_path.is_file():
        return set()
    df = load_customer_master_csv(csv_path) if side == "customer" else load_vendor_master_csv(csv_path)
    col = "customer_id" if side == "customer" else "supplier_id"
    return set(df[col].astype(str).tolist()) if col in df.columns else set()


def append_missing_master_rows(
    session: Session,
    *,
    customer_csv: Path,
    vendor_csv: Path,
) -> dict[str, int]:
    """Add synthetic BC rows for GL partner IDs not yet present in master CSVs."""
    cust_names = _existing_dim_names(session, "customer")
    supp_names = _existing_dim_names(session, "supplier")

    missing_c = _partner_ids_on_gl(session, "customer") - _partner_ids_in_csv(customer_csv, "customer")
    missing_s = _partner_ids_on_gl(session, "supplier") - _partner_ids_in_csv(vendor_csv, "supplier")

    cust_rows = build_synthetic_master_rows(
        _partner_rows_from_ids(missing_c, "customer"),
        dim_names=cust_names,
    )
    supp_rows = build_synthetic_master_rows(
        _partner_rows_from_ids(missing_s, "supplier"),
        dim_names=supp_names,
    )
    return {
        "csv_added_customers": merge_master_csv(customer_csv, cust_rows),
        "csv_added_suppliers": merge_master_csv(vendor_csv, supp_rows),
        "missing_customer_ids": len(missing_c),
        "missing_supplier_ids": len(missing_s),
    }


def merge_master_csv(path: Path, synthetic: pd.DataFrame) -> int:
    """Append synthetic rows to an existing BC master CSV (dedupe by entity + No.)."""
    if synthetic.empty:
        return 0
    if path.is_file():
        existing = pd.read_csv(path, dtype=str, keep_default_na=False)
        existing.columns = [c.strip().strip('"') for c in existing.columns]
    else:
        existing = pd.DataFrame(columns=BC_COLUMNS)

    existing_keys = {
        (str(r.get("entity", "")).strip(), str(r.get("No.", "")).strip())
        for _, r in existing.iterrows()
    }
    to_add: list[dict[str, str]] = []
    for _, r in synthetic.iterrows():
        key = (str(r["entity"]).strip(), str(r["No."]).strip())
        if key in existing_keys:
            continue
        existing_keys.add(key)
        to_add.append({c: str(r[c]) for c in BC_COLUMNS})

    if not to_add:
        return 0

    merged = pd.concat([existing, pd.DataFrame(to_add)], ignore_index=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(path, index=False, encoding="utf-8")
    return len(to_add)


def resolve_synthetic_journal_partners(
    session: Session,
    *,
    side: PartnerSide,
) -> list[ResolvedPartner]:
    """Last resort: pseudo debtor/creditor from journal_entry_group_number (txn suffix)."""
    gaps = _unmapped_journal_keys(session, side)
    if not gaps:
        return []

    resolved: list[ResolvedPartner] = []
    for jegn, fy in sorted(gaps):
        prefix = jegn[:2]
        number = jegn[2:].lstrip("0") or jegn[2:]
        pid = f"{prefix}{number}"
        entity_name = _PREFIX_TO_ENTITY.get(prefix, prefix)
        resolved.append(
            ResolvedPartner(
                side=side,
                partner_id=pid,
                partner_number=number,
                entity_name=entity_name,
                entity_prefix=prefix,
                journal_entry_group_number=jegn,
                fiscal_year=fy,
                source="synthetic_journal",
            )
        )

    _apply_resolved_to_gl(session, resolved, side=side)
    return resolved


def count_unmapped_facts(session: Session) -> dict[str, float | int]:
    """Rows + kEUR still without partner in top-entity scope."""
    rev_l3 = _sql_in(REVENUE_L3)
    mat_l3 = _sql_in(MATERIAL_L3)
    sales = session.execute(
        text(f"""
            SELECT COUNT(*), COALESCE(SUM(f.gross_sales), 0) / 1000.0
            FROM fact_sales f
            JOIN dim_gl_account a ON a.account_number_group = f.account_number_group
                               AND a.fiscal_year = f.fiscal_year
            WHERE TRIM(a.level_3) IN ({rev_l3})
              AND (f.customer_id IS NULL OR TRIM(f.customer_id) = '')
        """)
    ).one()
    com = session.execute(
        text(f"""
            SELECT COUNT(*), COALESCE(SUM(f.cost_of_materials), 0) / 1000.0
            FROM fact_com f
            JOIN dim_gl_account a ON a.account_number_group = f.account_number_group
                               AND a.fiscal_year = f.fiscal_year
            WHERE TRIM(a.level_3) IN ({mat_l3})
              AND (f.supplier_id IS NULL OR TRIM(f.supplier_id) = '')
        """)
    ).one()
    return {
        "sales_rows": int(sales[0]),
        "sales_keur": float(sales[1] or 0),
        "com_rows": int(com[0]),
        "com_keur": float(com[1] or 0),
    }


def sync_partner_gaps(
    session: Session,
    *,
    gl_path: Path,
    customer_csv: Path,
    vendor_csv: Path,
    load_dims: bool = True,
    rederive_facts: bool = True,
) -> dict[str, object]:
    """Full gap-closure pipeline."""
    from etl.load import load_partners
    from scripts.derive_facts import _derive_fact_com, _derive_fact_sales

    before = count_unmapped_facts(session)
    stats: dict[str, object] = {"before": before}

    stats["backfill_trade"] = backfill_gl_trade_ar_ap(session)
    stats["backfill_journal"] = backfill_gl_journal_unique_partner(session)
    stats["backfill_counterparty"] = backfill_gl_from_counterparty_lines(session)

    if rederive_facts:
        stats["fact_sales_rows"] = _derive_fact_sales(session)
        stats["fact_com_rows"] = _derive_fact_com(session)

    stats["after_backfill"] = count_unmapped_facts(session)

    cust_idx, supp_idx = build_gobd_partner_index(gl_path, require_unique=False)
    resolved_customers = _resolve_gaps_with_index(session, cust_idx, side="customer")
    resolved_suppliers = _resolve_gaps_with_index(session, supp_idx, side="supplier")
    stats["resolved_gobd_customers"] = len(resolved_customers)
    stats["resolved_gobd_suppliers"] = len(resolved_suppliers)

    if rederive_facts:
        stats["fact_sales_rows_post_gobd"] = _derive_fact_sales(session)
        stats["fact_com_rows_post_gobd"] = _derive_fact_com(session)

    synth_cust = resolve_synthetic_journal_partners(session, side="customer")
    synth_supp = resolve_synthetic_journal_partners(session, side="supplier")
    stats["synthetic_journal_customers"] = len(synth_cust)
    stats["synthetic_journal_suppliers"] = len(synth_supp)

    if rederive_facts and (synth_cust or synth_supp):
        stats["fact_sales_rows_post_synth"] = _derive_fact_sales(session)
        stats["fact_com_rows_post_synth"] = _derive_fact_com(session)

    stats["master_csv"] = append_missing_master_rows(
        session,
        customer_csv=customer_csv,
        vendor_csv=vendor_csv,
    )
    stats["csv_added_customers"] = stats["master_csv"]["csv_added_customers"]
    stats["csv_added_suppliers"] = stats["master_csv"]["csv_added_suppliers"]

    if load_dims and (customer_csv.is_file() or vendor_csv.is_file()):
        cust_dim = load_customer_master_csv(customer_csv) if customer_csv.is_file() else pd.DataFrame()
        supp_dim = load_vendor_master_csv(vendor_csv) if vendor_csv.is_file() else pd.DataFrame()
        if not cust_dim.empty or not supp_dim.empty:
            stats["dim_load"] = load_partners(session, cust_dim, supp_dim)

    if rederive_facts:
        stats["fact_sales_rows_final"] = _derive_fact_sales(session)
        stats["fact_com_rows_final"] = _derive_fact_com(session)

    stats["after"] = count_unmapped_facts(session)
    return stats
