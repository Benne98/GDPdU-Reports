"""Extended AR/AP aging analytics for the ported Sales aging UI (fact_ar / fact_ap)."""
from __future__ import annotations

import calendar
import json
from datetime import date
from typing import Any, Literal, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.fin_compat_sql import resolve_entity_prefix

AR_BANDS: tuple[tuple[str, str], ...] = (
    ("not_yet_due", "Not yet due"),
    ("overdue_1_30", "1–30 days overdue"),
    ("overdue_31_60", "31–60 days overdue"),
    ("overdue_61_90", "61–90 days overdue"),
    ("overdue_91_180", "91–180 days overdue"),
    ("overdue_over_180", ">180 days overdue"),
)

def _band_case_sql(as_of: date, alias: str = "t") -> str:
    due = f"COALESCE({alias}.due_date, ({alias}.posting_date + INTERVAL '30 days')::date)"
    as_of_s = as_of.isoformat()
    return f"""
        CASE
            WHEN {due} > '{as_of_s}'::date THEN 'not_yet_due'
            WHEN ('{as_of_s}'::date - {due}) BETWEEN 1 AND 30 THEN 'overdue_1_30'
            WHEN ('{as_of_s}'::date - {due}) BETWEEN 31 AND 60 THEN 'overdue_31_60'
            WHEN ('{as_of_s}'::date - {due}) BETWEEN 61 AND 90 THEN 'overdue_61_90'
            WHEN ('{as_of_s}'::date - {due}) BETWEEN 91 AND 180 THEN 'overdue_91_180'
            ELSE 'overdue_over_180'
        END
    """


CONCENTRATION_RANK_BANDS: tuple[tuple[str, str, int, Optional[int]], ...] = (
    ("rank_1_5", "Top 1–5", 1, 5),
    ("rank_6_10", "Top 6–10", 6, 10),
    ("rank_11_20", "Top 11–20", 11, 20),
    ("rank_21_plus", "Rank 21+", 21, None),
)


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _entity_frag(session: Session, entity: Optional[str], alias: str) -> str:
    ep = resolve_entity_prefix(session, entity)
    if not ep:
        return ""
    safe = str(ep).replace("'", "")[:2]
    return f"AND LEFT({alias}.account_number_group, 2) = '{safe}'"


def _ar_amt() -> str:
    return "(-ar.amount)"


def _ap_amt() -> str:
    return "ABS(ap.amount)"


def _due_sql(alias: str) -> str:
    return f"COALESCE({alias}.due_date, ({alias}.posting_date + INTERVAL '30 days')::date)"


def _bucket_amount_cols(as_of: date, alias: str, amt_expr: str) -> dict[str, str]:
    due = _due_sql(alias)
    as_of_s = as_of.isoformat()
    return {
        "not_yet_due": (
            f"COALESCE(SUM(CASE WHEN {due} > '{as_of_s}'::date THEN {amt_expr} ELSE 0 END), 0)"
        ),
        "overdue_1_30": (
            f"COALESCE(SUM(CASE WHEN {due} <= '{as_of_s}'::date "
            f"AND ('{as_of_s}'::date - {due}) BETWEEN 1 AND 30 THEN {amt_expr} ELSE 0 END), 0)"
        ),
        "overdue_31_60": (
            f"COALESCE(SUM(CASE WHEN ('{as_of_s}'::date - {due}) BETWEEN 31 AND 60 "
            f"THEN {amt_expr} ELSE 0 END), 0)"
        ),
        "overdue_61_90": (
            f"COALESCE(SUM(CASE WHEN ('{as_of_s}'::date - {due}) BETWEEN 61 AND 90 "
            f"THEN {amt_expr} ELSE 0 END), 0)"
        ),
        "overdue_91_180": (
            f"COALESCE(SUM(CASE WHEN ('{as_of_s}'::date - {due}) BETWEEN 91 AND 180 "
            f"THEN {amt_expr} ELSE 0 END), 0)"
        ),
        "overdue_over_180": (
            f"COALESCE(SUM(CASE WHEN ('{as_of_s}'::date - {due}) > 180 "
            f"THEN {amt_expr} ELSE 0 END), 0)"
        ),
    }


def _row_buckets(mapping: Any) -> dict[str, float]:
    d = dict(mapping._mapping) if hasattr(mapping, "_mapping") else dict(mapping)
    keys = (
        "not_yet_due", "overdue_1_30", "overdue_31_60",
        "overdue_61_90", "overdue_91_180", "overdue_over_180",
    )
    vals = {k: round(float(d.get(k) or 0), 2) for k in keys}
    total = round(sum(vals.values()), 2)
    overdue = round(total - vals["not_yet_due"], 2)
    vals["total"] = total
    vals["overdue_pct"] = round(overdue / total * 100, 1) if total > 0 else 0.0
    return vals


def _parse_hierarchy(raw: str) -> list[str]:
    try:
        if raw.strip().startswith("["):
            return json.loads(raw)
    except json.JSONDecodeError:
        pass
    return [p.strip() for p in raw.split(",") if p.strip()]


def _ar_dim_sql(dimension: str) -> tuple[str, str]:
    if dimension == "country":
        return "COALESCE(NULLIF(TRIM(c.country_code), ''), '—')", "COALESCE(NULLIF(TRIM(c.country_code), ''), '—')"
    if dimension == "entity":
        return "COALESCE(le.legal_entity_code, LEFT(ar.account_number_group, 2))", "COALESCE(le.legal_entity_code, LEFT(ar.account_number_group, 2))"
    return (
        "COALESCE(NULLIF(TRIM(c.name_line_1), ''), ar.customer_id, '(no customer)')",
        "COALESCE(NULLIF(TRIM(c.name_line_1), ''), ar.customer_id, '(no customer)')",
    )


def _ap_dim_sql(dimension: str) -> tuple[str, str]:
    if dimension == "country":
        return "COALESCE(NULLIF(TRIM(s.country_code), ''), '—')", "COALESCE(NULLIF(TRIM(s.country_code), ''), '—')"
    if dimension == "entity":
        return "COALESCE(le.legal_entity_code, LEFT(ap.account_number_group, 2))", "COALESCE(le.legal_entity_code, LEFT(ap.account_number_group, 2))"
    return (
        "COALESCE(NULLIF(TRIM(s.name_line_1), ''), ap.supplier_id, '(no supplier)')",
        "COALESCE(NULLIF(TRIM(s.name_line_1), ''), ap.supplier_id, '(no supplier)')",
    )


def build_receivables_by_dimension(
    session: Session,
    dimension: str,
    year: int,
    month: int,
    entity: Optional[str] = None,
    limit: int = 50,
    view: Literal["buckets", "due_overdue"] = "buckets",
) -> dict[str, Any]:
    as_of = _month_end(year, month)
    ent = _entity_frag(session, entity, "ar")
    dim_expr, dim_label = _ar_dim_sql(dimension)
    cols = _bucket_amount_cols(as_of, "ar", _ar_amt())
    as_of_s = as_of.isoformat()
    due = _due_sql("ar")
    amt = _ar_amt()

    if view == "due_overdue":
        sql = f"""
            SELECT {dim_label} AS label,
                   COALESCE(SUM(CASE WHEN {due} > '{as_of_s}'::date THEN {amt} ELSE 0 END), 0) / 1000.0 AS before_due,
                   COALESCE(SUM(CASE WHEN {due} <= '{as_of_s}'::date THEN {amt} ELSE 0 END), 0) / 1000.0 AS overdue
            FROM fact_ar ar
            LEFT JOIN dim_customer c ON c.customer_id = ar.customer_id
            LEFT JOIN dim_legal_entity le ON le.entity_prefix = LEFT(ar.account_number_group, 2)
            WHERE ar.posting_date <= '{as_of_s}'
              {ent}
            GROUP BY {dim_expr}
            HAVING COALESCE(SUM({amt}), 0) <> 0
            ORDER BY 2 DESC
            LIMIT {int(limit)}
        """
        rows = session.execute(text(sql)).fetchall()
        out = []
        for r in rows:
            total = float(r.before_due or 0) + float(r.overdue or 0)
            od = float(r.overdue or 0)
            out.append({
                "label": r.label,
                "before_due": round(float(r.before_due or 0), 2),
                "overdue": round(od, 2),
                "total": round(total, 2),
                "overdue_pct": round(od / total * 100, 1) if total > 0 else 0.0,
            })
        return {"dimension": dimension, "view": view, "year": year, "month": month, "rows": out}

    sql = f"""
        SELECT {dim_label} AS label,
               {cols['not_yet_due']} / 1000.0 AS not_yet_due,
               {cols['overdue_1_30']} / 1000.0 AS overdue_1_30,
               {cols['overdue_31_60']} / 1000.0 AS overdue_31_60,
               {cols['overdue_61_90']} / 1000.0 AS overdue_61_90,
               {cols['overdue_91_180']} / 1000.0 AS overdue_91_180,
               {cols['overdue_over_180']} / 1000.0 AS overdue_over_180
        FROM fact_ar ar
        LEFT JOIN dim_customer c ON c.customer_id = ar.customer_id
        LEFT JOIN dim_legal_entity le ON le.entity_prefix = LEFT(ar.account_number_group, 2)
        WHERE ar.posting_date <= '{as_of_s}'
          {ent}
        GROUP BY {dim_expr}
        HAVING COALESCE(SUM({_ar_amt()}), 0) <> 0
        ORDER BY (
            {cols['not_yet_due']} + {cols['overdue_1_30']} + {cols['overdue_31_60']} +
            {cols['overdue_61_90']} + {cols['overdue_91_180']} + {cols['overdue_over_180']}
        ) DESC
        LIMIT {int(limit)}
    """
    rows = session.execute(text(sql)).fetchall()
    out = []
    for r in rows:
        item = _row_buckets(r._mapping)
        item["label"] = r.label
        out.append(item)
    return {"dimension": dimension, "view": view, "year": year, "month": month, "rows": out}


def build_payables_by_dimension(
    session: Session,
    dimension: str,
    year: int,
    month: int,
    entity: Optional[str] = None,
    limit: int = 50,
    view: Literal["buckets", "due_overdue"] = "buckets",
) -> dict[str, Any]:
    as_of = _month_end(year, month)
    ent = _entity_frag(session, entity, "ap")
    dim_expr, dim_label = _ap_dim_sql(dimension)
    cols = _bucket_amount_cols(as_of, "ap", _ap_amt())
    as_of_s = as_of.isoformat()
    due = _due_sql("ap")
    amt = _ap_amt()

    if view == "due_overdue":
        sql = f"""
            SELECT {dim_label} AS label,
                   COALESCE(SUM(CASE WHEN {due} > '{as_of_s}'::date THEN {amt} ELSE 0 END), 0) / 1000.0 AS before_due,
                   COALESCE(SUM(CASE WHEN {due} <= '{as_of_s}'::date THEN {amt} ELSE 0 END), 0) / 1000.0 AS overdue
            FROM fact_ap ap
            LEFT JOIN dim_supplier s ON s.supplier_id = ap.supplier_id
            LEFT JOIN dim_legal_entity le ON le.entity_prefix = LEFT(ap.account_number_group, 2)
            WHERE ap.posting_date <= '{as_of_s}'
              {ent}
            GROUP BY {dim_expr}
            HAVING COALESCE(SUM({amt}), 0) <> 0
            ORDER BY 2 DESC
            LIMIT {int(limit)}
        """
        rows = session.execute(text(sql)).fetchall()
        out = []
        for r in rows:
            total = float(r.before_due or 0) + float(r.overdue or 0)
            od = float(r.overdue or 0)
            out.append({
                "label": r.label,
                "before_due": round(float(r.before_due or 0), 2),
                "overdue": round(od, 2),
                "total": round(total, 2),
                "overdue_pct": round(od / total * 100, 1) if total > 0 else 0.0,
            })
        return {"dimension": dimension, "view": view, "year": year, "month": month, "rows": out}

    sql = f"""
        SELECT {dim_label} AS label,
               {cols['not_yet_due']} / 1000.0 AS not_yet_due,
               {cols['overdue_1_30']} / 1000.0 AS overdue_1_30,
               {cols['overdue_31_60']} / 1000.0 AS overdue_31_60,
               {cols['overdue_61_90']} / 1000.0 AS overdue_61_90,
               {cols['overdue_91_180']} / 1000.0 AS overdue_91_180,
               {cols['overdue_over_180']} / 1000.0 AS overdue_over_180
        FROM fact_ap ap
        LEFT JOIN dim_supplier s ON s.supplier_id = ap.supplier_id
        LEFT JOIN dim_legal_entity le ON le.entity_prefix = LEFT(ap.account_number_group, 2)
        WHERE ap.posting_date <= '{as_of_s}'
          {ent}
        GROUP BY {dim_expr}
        HAVING COALESCE(SUM({amt}), 0) <> 0
        ORDER BY (
            {cols['not_yet_due']} + {cols['overdue_1_30']} + {cols['overdue_31_60']} +
            {cols['overdue_61_90']} + {cols['overdue_91_180']} + {cols['overdue_over_180']}
        ) DESC
        LIMIT {int(limit)}
    """
    rows = session.execute(text(sql)).fetchall()
    out = []
    for r in rows:
        item = _row_buckets(r._mapping)
        item["label"] = r.label
        out.append(item)
    return {"dimension": dimension, "view": view, "year": year, "month": month, "rows": out}


def _hierarchy_rows_ar(
    session: Session,
    hierarchy: list[str],
    year: int,
    month: int,
    entity: Optional[str],
    limit: int,
    view: str,
) -> list[dict[str, Any]]:
    as_of = _month_end(year, month)
    ent = _entity_frag(session, entity, "ar")
    cols = _bucket_amount_cols(as_of, "ar", _ar_amt())
    as_of_s = as_of.isoformat()
    selects = []
    group_bys = []
    for dim in hierarchy:
        expr, label = _ar_dim_sql(dim if dim != "invoice_number" else "customer")
        selects.append(f"{label} AS {dim}")
        group_bys.append(expr)
    sql = f"""
        SELECT {', '.join(selects)},
               {cols['not_yet_due']} / 1000.0 AS not_yet_due,
               {cols['overdue_1_30']} / 1000.0 AS overdue_1_30,
               {cols['overdue_31_60']} / 1000.0 AS overdue_31_60,
               {cols['overdue_61_90']} / 1000.0 AS overdue_61_90,
               {cols['overdue_91_180']} / 1000.0 AS overdue_91_180,
               {cols['overdue_over_180']} / 1000.0 AS overdue_over_180
        FROM fact_ar ar
        LEFT JOIN dim_customer c ON c.customer_id = ar.customer_id
        LEFT JOIN dim_legal_entity le ON le.entity_prefix = LEFT(ar.account_number_group, 2)
        WHERE ar.posting_date <= '{as_of_s}'
          {ent}
        GROUP BY {', '.join(group_bys)}
        HAVING COALESCE(SUM({_ar_amt()}), 0) <> 0
        ORDER BY (
            {cols['not_yet_due']} + {cols['overdue_1_30']} + {cols['overdue_31_60']} +
            {cols['overdue_61_90']} + {cols['overdue_91_180']} + {cols['overdue_over_180']}
        ) DESC
        LIMIT {int(limit)}
    """
    rows = session.execute(text(sql)).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        dims = {d: getattr(r, d) for d in hierarchy}
        buckets = _row_buckets(r._mapping)
        out.append({"dims": dims, **buckets})
    return out


def build_receivables_by_dimension_hierarchy(
    session: Session,
    hierarchy_raw: str,
    year: int,
    month: int,
    entity: Optional[str] = None,
    limit: int = 500,
    view: Literal["buckets", "due_overdue"] = "buckets",
    compare_pm: bool = False,
    compare_py: bool = False,
) -> dict[str, Any]:
    hierarchy = _parse_hierarchy(hierarchy_raw) or ["entity", "customer"]
    rows = _hierarchy_rows_ar(session, hierarchy, year, month, entity, limit, view)
    return {
        "hierarchy": hierarchy,
        "view": view,
        "year": year,
        "month": month,
        "compare_pm": compare_pm,
        "compare_py": compare_py,
        "rows": rows,
    }


def _hierarchy_rows_ap(
    session: Session,
    hierarchy: list[str],
    year: int,
    month: int,
    entity: Optional[str],
    limit: int,
) -> list[dict[str, Any]]:
    as_of = _month_end(year, month)
    ent = _entity_frag(session, entity, "ap")
    cols = _bucket_amount_cols(as_of, "ap", _ap_amt())
    as_of_s = as_of.isoformat()
    selects = []
    group_bys = []
    for dim in hierarchy:
        expr, label = _ap_dim_sql(dim if dim != "invoice_number" else "supplier")
        selects.append(f"{label} AS {dim}")
        group_bys.append(expr)
    sql = f"""
        SELECT {', '.join(selects)},
               {cols['not_yet_due']} / 1000.0 AS not_yet_due,
               {cols['overdue_1_30']} / 1000.0 AS overdue_1_30,
               {cols['overdue_31_60']} / 1000.0 AS overdue_31_60,
               {cols['overdue_61_90']} / 1000.0 AS overdue_61_90,
               {cols['overdue_91_180']} / 1000.0 AS overdue_91_180,
               {cols['overdue_over_180']} / 1000.0 AS overdue_over_180
        FROM fact_ap ap
        LEFT JOIN dim_supplier s ON s.supplier_id = ap.supplier_id
        LEFT JOIN dim_legal_entity le ON le.entity_prefix = LEFT(ap.account_number_group, 2)
        WHERE ap.posting_date <= '{as_of_s}'
          {ent}
        GROUP BY {', '.join(group_bys)}
        HAVING COALESCE(SUM({_ap_amt()}), 0) <> 0
        ORDER BY (
            {cols['not_yet_due']} + {cols['overdue_1_30']} + {cols['overdue_31_60']} +
            {cols['overdue_61_90']} + {cols['overdue_91_180']} + {cols['overdue_over_180']}
        ) DESC
        LIMIT {int(limit)}
    """
    rows = session.execute(text(sql)).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        dims = {d: getattr(r, d) for d in hierarchy}
        buckets = _row_buckets(r._mapping)
        out.append({"dims": dims, **buckets})
    return out


def build_payables_by_dimension_hierarchy(
    session: Session,
    hierarchy_raw: str,
    year: int,
    month: int,
    entity: Optional[str] = None,
    limit: int = 500,
    view: Literal["buckets", "due_overdue"] = "buckets",
    compare_pm: bool = False,
    compare_py: bool = False,
) -> dict[str, Any]:
    hierarchy = _parse_hierarchy(hierarchy_raw) or ["entity", "supplier"]
    rows = _hierarchy_rows_ap(session, hierarchy, year, month, entity, limit)
    return {
        "hierarchy": hierarchy,
        "view": view,
        "year": year,
        "month": month,
        "compare_pm": compare_pm,
        "compare_py": compare_py,
        "rows": rows,
    }


def _partner_rows_ar(session: Session, year: int, month: int, entity: Optional[str], limit: int) -> list[Any]:
    as_of = _month_end(year, month)
    ent = _entity_frag(session, entity, "ar")
    as_of_s = as_of.isoformat()
    due = _due_sql("ar")
    amt = _ar_amt()
    sql = f"""
        SELECT ar.customer_id,
               COALESCE(NULLIF(TRIM(c.name_line_1), ''), ar.customer_id, '(no customer)') AS customer_name,
               COALESCE(SUM({amt}), 0) / 1000.0 AS balance,
               COALESCE(SUM(CASE WHEN {due} <= '{as_of_s}'::date THEN {amt} ELSE 0 END), 0) / 1000.0 AS overdue,
               COALESCE(AVG(GREATEST(0, '{as_of_s}'::date - {due})), 0) AS avg_days_outstanding,
               COUNT(DISTINCT ar.journal_entry_group_number)::int AS open_documents
        FROM fact_ar ar
        LEFT JOIN dim_customer c ON c.customer_id = ar.customer_id
        WHERE ar.posting_date <= '{as_of_s}'
          {ent}
        GROUP BY ar.customer_id, c.name_line_1
        HAVING COALESCE(SUM({amt}), 0) > 0
        ORDER BY balance DESC
        LIMIT {int(limit)}
    """
    return session.execute(text(sql)).fetchall()


def build_receivables_customers(
    session: Session,
    year: int,
    month: int,
    entity: Optional[str] = None,
    limit: int = 50,
) -> dict[str, Any]:
    rows = _partner_rows_ar(session, year, month, entity, limit)
    register, scatter, combo = [], [], []
    for r in rows:
        bal = float(r.balance or 0)
        od = float(r.overdue or 0)
        od_pct = round(od / bal * 100, 1) if bal > 0 else 0.0
        cid = str(r.customer_id or "")
        entry = {
            "customer_id": cid,
            "customer_name": r.customer_name,
            "contact_name": None,
            "balance": round(bal, 2),
            "overdue": round(od, 2),
            "overdue_pct": od_pct,
            "days_outstanding": round(float(r.avg_days_outstanding or 0), 1),
            "payment_terms_days": 30,
            "open_documents": int(r.open_documents or 0),
            "gross_sales": 0.0,
            "documents": [],
        }
        register.append(entry)
        scatter.append({
            "customer_id": cid,
            "customer_name": r.customer_name,
            "balance": entry["balance"],
            "overdue_pct": od_pct,
        })
        combo.append({
            "customer_name": r.customer_name,
            "balance": entry["balance"],
            "days_outstanding": entry["days_outstanding"],
        })
    return {"year": year, "month": month, "register": register, "scatter": scatter, "combo": combo[:15]}


def _partner_rows_ap(session: Session, year: int, month: int, entity: Optional[str], limit: int) -> list[Any]:
    as_of = _month_end(year, month)
    ent = _entity_frag(session, entity, "ap")
    as_of_s = as_of.isoformat()
    due = _due_sql("ap")
    amt = _ap_amt()
    sql = f"""
        SELECT ap.supplier_id,
               COALESCE(NULLIF(TRIM(s.name_line_1), ''), ap.supplier_id, '(no supplier)') AS supplier_name,
               COALESCE(SUM({amt}), 0) / 1000.0 AS balance,
               COALESCE(SUM(CASE WHEN {due} <= '{as_of_s}'::date THEN {amt} ELSE 0 END), 0) / 1000.0 AS overdue,
               COALESCE(AVG(GREATEST(0, '{as_of_s}'::date - {due})), 0) AS avg_days_outstanding,
               COUNT(DISTINCT ap.journal_entry_group_number)::int AS open_documents
        FROM fact_ap ap
        LEFT JOIN dim_supplier s ON s.supplier_id = ap.supplier_id
        WHERE ap.posting_date <= '{as_of_s}'
          {ent}
        GROUP BY ap.supplier_id, s.name_line_1
        HAVING COALESCE(SUM({amt}), 0) > 0
        ORDER BY balance DESC
        LIMIT {int(limit)}
    """
    return session.execute(text(sql)).fetchall()


def build_payables_suppliers(
    session: Session,
    year: int,
    month: int,
    entity: Optional[str] = None,
    limit: int = 50,
) -> dict[str, Any]:
    rows = _partner_rows_ap(session, year, month, entity, limit)
    register, scatter, combo = [], [], []
    for r in rows:
        bal = float(r.balance or 0)
        od = float(r.overdue or 0)
        od_pct = round(od / bal * 100, 1) if bal > 0 else 0.0
        sid = str(r.supplier_id or "")
        entry = {
            "supplier_id": sid,
            "supplier_name": r.supplier_name,
            "balance": round(bal, 2),
            "overdue": round(od, 2),
            "overdue_pct": od_pct,
            "days_outstanding": round(float(r.avg_days_outstanding or 0), 1),
            "payment_terms_days": 30,
            "open_documents": int(r.open_documents or 0),
            "documents": [],
        }
        register.append(entry)
        scatter.append({
            "supplier_id": sid,
            "supplier_name": r.supplier_name,
            "balance": entry["balance"],
            "overdue_pct": od_pct,
        })
        combo.append({
            "supplier_name": r.supplier_name,
            "balance": entry["balance"],
            "days_outstanding": entry["days_outstanding"],
        })
    return {"year": year, "month": month, "register": register, "scatter": scatter, "combo": combo[:15]}


def _concentration_from_amounts(amounts: list[float], count_key: str) -> dict[str, Any]:
    total = sum(amounts)
    segments = []
    for band_id, label, rank_from, rank_to in CONCENTRATION_RANK_BANDS:
        start = rank_from - 1
        slice_amts = amounts[start:rank_to] if rank_to is not None else amounts[start:]
        amt = sum(slice_amts)
        segments.append({
            "band": band_id,
            "label": label,
            "amount": round(amt, 2),
            "pct": round(amt / total * 100, 1) if total > 0 else 0.0,
            count_key: len(slice_amts),
        })
    return {"total": round(total, 2), "segments": segments}


def build_receivables_concentration(
    session: Session,
    year: int,
    month: int,
    entity: Optional[str] = None,
) -> dict[str, Any]:
    rows = _partner_rows_ar(session, year, month, entity, 500)
    amounts = [float(r.balance or 0) for r in rows]
    data = _concentration_from_amounts(amounts, "customer_count")
    return {"year": year, "month": month, **data}


def build_payables_concentration(
    session: Session,
    year: int,
    month: int,
    entity: Optional[str] = None,
) -> dict[str, Any]:
    rows = _partner_rows_ap(session, year, month, entity, 500)
    amounts = [float(r.balance or 0) for r in rows]
    data = _concentration_from_amounts(amounts, "supplier_count")
    return {"year": year, "month": month, **data}


def build_receivables_geo(
    session: Session,
    year: int,
    month: int,
    entity: Optional[str] = None,
    limit: int = 20,
) -> dict[str, Any]:
    res = build_receivables_by_dimension(session, "country", year, month, entity, limit, "due_overdue")
    rows = []
    for r in res["rows"]:
        rows.append({
            "country_code": r["label"],
            "country_name": r["label"],
            "balance": r["total"],
            "overdue": r["overdue"],
            "overdue_pct": r["overdue_pct"],
        })
    return {"year": year, "month": month, "rows": rows}


def build_payables_geo(
    session: Session,
    year: int,
    month: int,
    entity: Optional[str] = None,
    limit: int = 20,
) -> dict[str, Any]:
    res = build_payables_by_dimension(session, "country", year, month, entity, limit, "due_overdue")
    rows = []
    for r in res["rows"]:
        rows.append({
            "country_code": r["label"],
            "country_name": r["label"],
            "balance": r["total"],
            "overdue": r["overdue"],
            "overdue_pct": r["overdue_pct"],
        })
    return {"year": year, "month": month, "rows": rows}


def build_receivables_geo_country_locations(
    session: Session,
    country_code: str,
    year: int,
    month: int,
    entity: Optional[str] = None,
) -> dict[str, Any]:
    del session, entity
    return {"country_code": country_code, "year": year, "month": month, "locations": []}


def build_payables_geo_country_locations(
    session: Session,
    country_code: str,
    year: int,
    month: int,
    entity: Optional[str] = None,
) -> dict[str, Any]:
    del session, entity
    return {"country_code": country_code, "year": year, "month": month, "locations": []}


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    m = month + delta
    y = year
    while m < 1:
        m += 12
        y -= 1
    while m > 12:
        m -= 12
        y += 1
    return y, m


def build_receivables_trend(
    session: Session,
    year: int,
    month: int,
    entity: Optional[str] = None,
    periods_back: int = 12,
    period_grain: str = "month",
) -> dict[str, Any]:
    del period_grain
    points = []
    y, m = year, month
    for _ in range(periods_back):
        from app.services.gl_aging import build_receivables_aging  # lazy — avoid cycle at import
        snap = build_receivables_aging(session, y, m, entity)
        total = float(snap.get("total_receivables") or 0)
        overdue = float((snap.get("kpis") or {}).get("overdue") or 0)
        points.append({
            "year": y,
            "month": m,
            "label": f"{m:02d}/{y}",
            "balance": total,
            "overdue": overdue,
            "net_sales": 0.0,
            "credit_sales_pct": round(overdue / total * 100, 1) if total > 0 else 0.0,
        })
        y, m = _shift_month(y, m, -1)
    points.reverse()
    return {"year": year, "month": month, "points": points}


def build_payables_trend(
    session: Session,
    year: int,
    month: int,
    entity: Optional[str] = None,
    periods_back: int = 12,
    period_grain: str = "month",
) -> dict[str, Any]:
    del period_grain
    points = []
    y, m = year, month
    for _ in range(periods_back):
        from app.services.gl_aging import build_payables_aging
        snap = build_payables_aging(session, y, m, entity)
        total = float(snap.get("total_payables") or 0)
        overdue = float((snap.get("kpis") or {}).get("overdue") or 0)
        points.append({
            "year": y,
            "month": m,
            "label": f"{m:02d}/{y}",
            "balance": total,
            "overdue": overdue,
            "procurement": 0.0,
            "overdue_pct": round(overdue / total * 100, 1) if total > 0 else 0.0,
        })
        y, m = _shift_month(y, m, -1)
    points.reverse()
    return {"year": year, "month": month, "points": points}


def build_concentration_trend_ar(
    session: Session,
    year: int,
    month: int,
    entity: Optional[str] = None,
    periods_back: int = 12,
) -> dict[str, Any]:
    points = []
    y, m = year, month
    for _ in range(periods_back):
        conc = build_receivables_concentration(session, y, m, entity)
        points.append({
            "year": y,
            "month": m,
            "label": f"{m:02d}/{y}",
            "segments": conc["segments"],
            "total": conc["total"],
        })
        y, m = _shift_month(y, m, -1)
    points.reverse()
    return {"year": year, "month": month, "points": points}


def build_concentration_trend_ap(
    session: Session,
    year: int,
    month: int,
    entity: Optional[str] = None,
    periods_back: int = 12,
) -> dict[str, Any]:
    points = []
    y, m = year, month
    for _ in range(periods_back):
        conc = build_payables_concentration(session, y, m, entity)
        points.append({
            "year": y,
            "month": m,
            "label": f"{m:02d}/{y}",
            "segments": conc["segments"],
            "total": conc["total"],
        })
        y, m = _shift_month(y, m, -1)
    points.reverse()
    return {"year": year, "month": month, "points": points}


def build_receivables_dimension_chart(
    session: Session,
    dimension: str,
    year: int,
    month: int,
    entity: Optional[str] = None,
    limit: int = 25,
    compare_pm: bool = False,
    compare_py: bool = False,
) -> dict[str, Any]:
    cur = build_receivables_by_dimension(session, dimension, year, month, entity, limit, "due_overdue")
    rows = []
    for r in cur["rows"]:
        item = {
            "label": r["label"],
            "balance": r["total"],
            "overdue": r["overdue"],
            "overdue_pct": r["overdue_pct"],
        }
        if compare_pm:
            py_m = month - 1 if month > 1 else 12
            py_y = year if month > 1 else year - 1
            # placeholder — same shape
            item["balance_pm"] = item["balance"]
        if compare_py:
            item["balance_py"] = item["balance"]
        rows.append(item)
    return {
        "dimension": dimension,
        "year": year,
        "month": month,
        "compare_pm": compare_pm,
        "compare_py": compare_py,
        "rows": rows,
    }


def build_payables_dimension_chart(
    session: Session,
    dimension: str,
    year: int,
    month: int,
    entity: Optional[str] = None,
    limit: int = 25,
    compare_pm: bool = False,
    compare_py: bool = False,
) -> dict[str, Any]:
    cur = build_payables_by_dimension(session, dimension, year, month, entity, limit, "due_overdue")
    rows = []
    for r in cur["rows"]:
        item = {
            "label": r["label"],
            "balance": r["total"],
            "overdue": r["overdue"],
            "overdue_pct": r["overdue_pct"],
        }
        if compare_pm:
            item["balance_pm"] = item["balance"]
        if compare_py:
            item["balance_py"] = item["balance"]
        rows.append(item)
    return {
        "dimension": dimension,
        "year": year,
        "month": month,
        "compare_pm": compare_pm,
        "compare_py": compare_py,
        "rows": rows,
    }


def build_receivables_portfolio_table(
    session: Session,
    dimension: str,
    year: int,
    month: int,
    entity: Optional[str] = None,
    rows_per_bucket: int = 25,
) -> dict[str, Any]:
    as_of = _month_end(year, month)
    ent = _entity_frag(session, entity, "ar")
    dim_expr, dim_label = _ar_dim_sql(dimension)
    band_sql = _band_case_sql(as_of, "ar")
    as_of_s = as_of.isoformat()
    amt = _ar_amt()
    sql = f"""
        SELECT {band_sql} AS band,
               {dim_label} AS label,
               COALESCE(SUM({amt}), 0) / 1000.0 AS amount,
               COUNT(DISTINCT ar.journal_entry_group_number)::int AS document_count
        FROM fact_ar ar
        LEFT JOIN dim_customer c ON c.customer_id = ar.customer_id
        LEFT JOIN dim_legal_entity le ON le.entity_prefix = LEFT(ar.account_number_group, 2)
        WHERE ar.posting_date <= '{as_of_s}'
          {ent}
        GROUP BY {band_sql}, {dim_expr}
        HAVING COALESCE(SUM({amt}), 0) > 0
        ORDER BY band, amount DESC
    """
    raw = session.execute(text(sql)).fetchall()
    series_rows = session.execute(text(f"""
        SELECT {band_sql} AS band, COALESCE(SUM({amt}), 0) / 1000.0 AS amount
        FROM fact_ar ar
        WHERE ar.posting_date <= '{as_of_s}' {ent}
        GROUP BY 1
    """)).fetchall()
    bucket_totals = {r.band: float(r.amount or 0) for r in series_rows}
    by_band: dict[str, list[dict[str, Any]]] = {b: [] for b, _ in AR_BANDS}
    for r in raw:
        band = str(r.band)
        if band not in by_band or len(by_band[band]) >= rows_per_bucket:
            continue
        by_band[band].append({
            "label": r.label,
            "amount": round(float(r.amount or 0), 2),
            "document_count": int(r.document_count or 0),
            "relationship_since": None,
        })
    buckets = []
    for b, lbl in AR_BANDS:
        rows = by_band[b]
        buckets.append({
            "band": b,
            "label": lbl,
            "amount": round(bucket_totals.get(b, 0.0), 2),
            "document_count": sum(x["document_count"] for x in rows),
            "rows": rows,
        })
    return {
        "dimension": dimension,
        "year": year,
        "month": month,
        "as_of": as_of_s,
        "buckets": buckets,
    }


def build_payables_portfolio_table(
    session: Session,
    dimension: str,
    year: int,
    month: int,
    entity: Optional[str] = None,
    rows_per_bucket: int = 25,
) -> dict[str, Any]:
    as_of = _month_end(year, month)
    ent = _entity_frag(session, entity, "ap")
    dim_expr, dim_label = _ap_dim_sql(dimension)
    band_sql = _band_case_sql(as_of, "ap")
    as_of_s = as_of.isoformat()
    amt = _ap_amt()
    sql = f"""
        SELECT {band_sql} AS band,
               {dim_label} AS label,
               COALESCE(SUM({amt}), 0) / 1000.0 AS amount,
               COUNT(DISTINCT ap.journal_entry_group_number)::int AS document_count
        FROM fact_ap ap
        LEFT JOIN dim_supplier s ON s.supplier_id = ap.supplier_id
        LEFT JOIN dim_legal_entity le ON le.entity_prefix = LEFT(ap.account_number_group, 2)
        WHERE ar.posting_date <= '{as_of_s}'
          {ent}
        GROUP BY {band_sql}, {dim_expr}
        HAVING COALESCE(SUM({amt}), 0) > 0
        ORDER BY band, amount DESC
    """
    # fix typo ar -> ap
    sql = sql.replace("ar.posting_date", "ap.posting_date")
    raw = session.execute(text(sql)).fetchall()
    series_rows = session.execute(text(f"""
        SELECT {band_sql} AS band, COALESCE(SUM({amt}), 0) / 1000.0 AS amount
        FROM fact_ap ap
        WHERE ap.posting_date <= '{as_of_s}' {ent}
        GROUP BY 1
    """)).fetchall()
    bucket_totals = {r.band: float(r.amount or 0) for r in series_rows}
    by_band: dict[str, list[dict[str, Any]]] = {b: [] for b, _ in AR_BANDS}
    for r in raw:
        band = str(r.band)
        if band not in by_band or len(by_band[band]) >= rows_per_bucket:
            continue
        by_band[band].append({
            "label": r.label,
            "amount": round(float(r.amount or 0), 2),
            "document_count": int(r.document_count or 0),
            "relationship_since": None,
        })
    buckets = []
    for b, lbl in AR_BANDS:
        rows = by_band[b]
        buckets.append({
            "band": b,
            "label": lbl,
            "amount": round(bucket_totals.get(b, 0.0), 2),
            "document_count": sum(x["document_count"] for x in rows),
            "rows": rows,
        })
    return {
        "dimension": dimension,
        "year": year,
        "month": month,
        "as_of": as_of_s,
        "buckets": buckets,
    }


def build_receivables_customer_documents(
    session: Session,
    customer_id: str,
    year: int,
    month: int,
    entity: Optional[str] = None,
) -> dict[str, Any]:
    as_of = _month_end(year, month)
    ent = _entity_frag(session, entity, "ar")
    as_of_s = as_of.isoformat()
    due = _due_sql("ar")
    sql = f"""
        SELECT ar.reference_document_number AS document_number,
               ar.posting_date::text AS posting_date,
               {due}::text AS due_date,
               GREATEST(0, '{as_of_s}'::date - {due})::int AS days_overdue,
               ({_ar_amt()}) / 1000.0 AS amount
        FROM fact_ar ar
        WHERE ar.customer_id = :cid
          AND ar.posting_date <= '{as_of_s}'
          {ent}
        ORDER BY ar.posting_date DESC
        LIMIT 100
    """
    rows = session.execute(text(sql), {"cid": customer_id}).fetchall()
    docs = [{
        "document_number": r.document_number,
        "posting_date": r.posting_date,
        "due_date": r.due_date,
        "days_overdue": int(r.days_overdue or 0),
        "amount": round(float(r.amount or 0), 2),
    } for r in rows]
    return {"customer_id": customer_id, "year": year, "month": month, "documents": docs}


def build_payables_supplier_documents(
    session: Session,
    supplier_id: str,
    year: int,
    month: int,
    entity: Optional[str] = None,
) -> dict[str, Any]:
    as_of = _month_end(year, month)
    ent = _entity_frag(session, entity, "ap")
    as_of_s = as_of.isoformat()
    due = _due_sql("ap")
    sql = f"""
        SELECT ap.reference_document_number AS document_number,
               ap.posting_date::text AS posting_date,
               {due}::text AS due_date,
               GREATEST(0, '{as_of_s}'::date - {due})::int AS days_overdue,
               ({_ap_amt()}) / 1000.0 AS amount
        FROM fact_ap ap
        WHERE ap.supplier_id = :sid
          AND ap.posting_date <= '{as_of_s}'
          {ent}
        ORDER BY ap.posting_date DESC
        LIMIT 100
    """
    rows = session.execute(text(sql), {"sid": supplier_id}).fetchall()
    docs = [{
        "document_number": r.document_number,
        "posting_date": r.posting_date,
        "due_date": r.due_date,
        "days_overdue": int(r.days_overdue or 0),
        "amount": round(float(r.amount or 0), 2),
    } for r in rows]
    return {"supplier_id": supplier_id, "year": year, "month": month, "documents": docs}
