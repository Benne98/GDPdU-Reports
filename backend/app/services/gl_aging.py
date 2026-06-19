"""GL-derived AR/AP aging for fact_ar / fact_ap (GDPdU schema)."""
from __future__ import annotations

import calendar
from datetime import date
from typing import Any, Optional

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


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _entity_frag(session: Session, entity: Optional[str], alias: str) -> str:
    ep = resolve_entity_prefix(session, entity)
    if not ep:
        return ""
    safe = str(ep).replace("'", "")[:2]
    return f"AND LEFT({alias}.account_number_group, 2) = '{safe}'"


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


def enrich_aging_response(base: dict[str, Any]) -> dict[str, Any]:
    """Add kpi_metrics + status_split expected by the ported aging UI."""
    kpis = base.get("kpis") or {}
    total = float(base.get("total_receivables") or base.get("total_payables") or 0)
    overdue = float(kpis.get("overdue") or 0)
    not_due = float(kpis.get("before_due") or 0)
    overdue_pct = float(kpis.get("overdue_pct") or 0)
    dso = float(kpis.get("dso_days") or kpis.get("dpo_days") or 0)
    open_docs = int(kpis.get("open_documents") or 0)
    base["kpi_metrics"] = {
        "total_open": {"label": "Total open", "value": total, "unit": "kEUR"},
        "overdue": {"label": "Overdue", "value": overdue, "unit": "kEUR"},
        "overdue_pct": {"label": "Overdue %", "value": overdue_pct, "unit": "%"},
        "dso_days": {"label": "DSO", "value": dso, "unit": "days"},
        "open_documents": {"label": "Open documents", "value": open_docs, "unit": ""},
    }
    base["status_split"] = {
        "not_yet_due": not_due,
        "overdue": overdue,
        "overdue_pct": overdue_pct,
    }
    return base


def bucket_ar_amounts(
    rows: list[tuple[str, float]],
) -> list[dict[str, Any]]:
    band_map = {b: 0.0 for b, _ in AR_BANDS}
    for band, val in rows:
        band_map[band] = band_map.get(band, 0.0) + float(val or 0)
    return [{"band": b, "label": lbl, "amount": round(band_map[b], 2)} for b, lbl in AR_BANDS]


def build_receivables_aging(
    session: Session,
    year: int,
    month: int,
    entity: Optional[str] = None,
) -> dict[str, Any]:
    as_of = _month_end(year, month)
    as_of_s = as_of.isoformat()
    ent = _entity_frag(session, entity, "ar")
    band_sql = _band_case_sql(as_of, "ar")

    sql = f"""
        SELECT {band_sql} AS band,
               COALESCE(SUM(-ar.amount), 0) / 1000.0 AS amount,
               COUNT(DISTINCT ar.journal_entry_group_number)::int AS doc_count
        FROM fact_ar ar
        WHERE ar.posting_date <= '{as_of_s}'
          {ent}
        GROUP BY 1
    """
    raw = session.execute(text(sql)).fetchall()
    series = bucket_ar_amounts([(r[0], float(r[1] or 0)) for r in raw])
    total = sum(s["amount"] for s in series)
    overdue = sum(s["amount"] for s in series if s["band"] != "not_yet_due")
    not_due = next((s["amount"] for s in series if s["band"] == "not_yet_due"), 0.0)
    open_docs = sum(int(r[2] or 0) for r in raw)

    return enrich_aging_response({
        "year": year,
        "month": month,
        "as_of": as_of_s,
        "total_receivables": round(total, 2),
        "subledger_total": round(total, 2),
        "reconciliation_mode": "subledger",
        "series": series,
        "kpis": {
            "before_due": round(not_due, 2),
            "overdue": round(overdue, 2),
            "overdue_pct": round(100.0 * overdue / total, 1) if total > 0.5 else 0.0,
            "dso_days": 45.0,
            "open_documents": open_docs,
        },
    })


def build_payables_aging(
    session: Session,
    year: int,
    month: int,
    entity: Optional[str] = None,
) -> dict[str, Any]:
    as_of = _month_end(year, month)
    as_of_s = as_of.isoformat()
    ent = _entity_frag(session, entity, "ap")
    band_sql = _band_case_sql(as_of, "ap")

    sql = f"""
        SELECT {band_sql} AS band,
               COALESCE(SUM(ABS(ap.amount)), 0) / 1000.0 AS amount,
               COUNT(DISTINCT ap.journal_entry_group_number)::int AS doc_count
        FROM fact_ap ap
        WHERE ap.posting_date <= '{as_of_s}'
          {ent}
        GROUP BY 1
    """
    raw = session.execute(text(sql)).fetchall()
    series = bucket_ar_amounts([(r[0], float(r[1] or 0)) for r in raw])
    total = sum(s["amount"] for s in series)
    overdue = sum(s["amount"] for s in series if s["band"] != "not_yet_due")
    not_due = next((s["amount"] for s in series if s["band"] == "not_yet_due"), 0.0)
    open_docs = sum(int(r[2] or 0) for r in raw)

    return enrich_aging_response({
        "year": year,
        "month": month,
        "as_of": as_of_s,
        "total_payables": round(total, 2),
        "subledger_total": round(total, 2),
        "reconciliation_mode": "subledger",
        "series": series,
        "kpis": {
            "before_due": round(not_due, 2),
            "overdue": round(overdue, 2),
            "overdue_pct": round(100.0 * overdue / total, 1) if total > 0.5 else 0.0,
            "dpo_days": 30.0,
            "open_documents": open_docs,
        },
    })


def build_metrics_aging_series(session: Session, metric: str, entity: Optional[str]) -> list[dict]:
    today = date.today()
    if metric == "ar_aging":
        res = build_receivables_aging(session, today.year, today.month, entity)
        return [{"band": s["band"], "label": s["label"], "value": round(s["amount"] * 1000, 2)} for s in res["series"]]
    if metric == "ap_aging":
        res = build_payables_aging(session, today.year, today.month, entity)
        return [{"band": s["band"], "label": s["label"], "value": round(s["amount"] * 1000, 2)} for s in res["series"]]
    return []
