"""GL-derived profitability KPIs (fact_sales / fact_com).

Invoice count = COUNT(DISTINCT journal_entry_group_number) in the period window.
Churn rate = logo churn for the PM→CM step:
  100 * (customers active in PM but not CM) / (customers active in PM).
NRR returns a neutral zero block (no cohort NRR in GL-only schema).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.fin_compat_sql import last_day, pm as _pm, resolve_entity_prefix


def _kpi_block(value: float, prior: float = 0.0) -> dict[str, float]:
    return {
        "value": round(value, 2),
        "delta_pm": round(value - prior, 2) if prior else 0.0,
        "delta_smly": 0.0,
        "delta_pw": 0.0,
    }


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    return date(year, month, 1).isoformat(), last_day(year, month)


def _customer_churn_rate(
    session: Session,
    pm_y: int,
    pm_m: int,
    cm_y: int,
    cm_m: int,
    ent_frag: str,
) -> float:
    """Logo churn (%) for customers with sales in PM but not in CM."""
    pm_f, pm_t = _month_bounds(pm_y, pm_m)
    cm_f, cm_t = _month_bounds(cm_y, cm_m)
    sql = f"""
        SELECT
            (
                SELECT COUNT(DISTINCT f.customer_id)
                FROM fact_sales f
                WHERE f.posting_date BETWEEN :pm_f AND :pm_t
                  AND f.customer_id IS NOT NULL
                  {ent_frag}
            ) AS pm_count,
            (
                SELECT COUNT(DISTINCT f.customer_id)
                FROM fact_sales f
                WHERE f.posting_date BETWEEN :pm_f AND :pm_t
                  AND f.customer_id IS NOT NULL
                  {ent_frag}
                  AND f.customer_id NOT IN (
                      SELECT DISTINCT g.customer_id
                      FROM fact_sales g
                      WHERE g.posting_date BETWEEN :cm_f AND :cm_t
                        AND g.customer_id IS NOT NULL
                        {ent_frag.replace("f.", "g.")}
                  )
            ) AS lost_count
    """
    row = session.execute(
        text(sql),
        {"pm_f": pm_f, "pm_t": pm_t, "cm_f": cm_f, "cm_t": cm_t},
    ).fetchone()
    pm_count = int(row[0] or 0) if row else 0
    lost_count = int(row[1] or 0) if row else 0
    if pm_count <= 0:
        return 0.0
    return round(100.0 * lost_count / pm_count, 1)


def _churn_kpi_block(
    session: Session,
    year: int,
    month: int,
    ent_frag: str,
) -> dict[str, float]:
    pm_y, pm_m = _pm(year, month)
    churn = _customer_churn_rate(session, pm_y, pm_m, year, month, ent_frag)
    ppm_y, ppm_m = _pm(pm_y, pm_m)
    churn_pm = _customer_churn_rate(session, ppm_y, ppm_m, pm_y, pm_m, ent_frag)
    churn_smly = _customer_churn_rate(session, pm_y - 1, pm_m, year - 1, month, ent_frag)
    return {
        "value": churn,
        "delta_pm": round(churn - churn_pm, 1),
        "delta_smly": round(churn - churn_smly, 1),
        "delta_pw": round(churn - churn_pm, 1),
    }


def build_headline_kpis(
    session: Session,
    year: int,
    month: int,
    entity: Optional[str] = None,
) -> dict[str, Any]:
    ep = resolve_entity_prefix(session, entity)
    ent_frag = ""
    if ep is not None:
        safe = str(ep).replace("'", "")[:2]
        ent_frag = f"AND LEFT(f.account_number_group, 2) = '{safe}'"

    cm_f, cm_t = _month_bounds(year, month)
    pm_y, pm_m = _pm(year, month)
    pm_f, pm_t = _month_bounds(pm_y, pm_m)

    sql = f"""
        SELECT
            COALESCE(SUM(CASE WHEN f.posting_date BETWEEN '{cm_f}' AND '{cm_t}'
                THEN f.gross_sales ELSE 0 END), 0) AS rev_cm,
            COALESCE(SUM(CASE WHEN f.posting_date BETWEEN '{pm_f}' AND '{pm_t}'
                THEN f.gross_sales ELSE 0 END), 0) AS rev_pm,
            COUNT(DISTINCT CASE WHEN f.posting_date BETWEEN '{cm_f}' AND '{cm_t}'
                THEN f.journal_entry_group_number END) AS inv_cm,
            COUNT(DISTINCT CASE WHEN f.posting_date BETWEEN '{pm_f}' AND '{pm_t}'
                THEN f.journal_entry_group_number END) AS inv_pm,
            COUNT(DISTINCT CASE WHEN f.posting_date BETWEEN '{cm_f}' AND '{cm_t}'
                THEN f.customer_id END) AS cust_cm,
            COUNT(DISTINCT CASE WHEN f.posting_date BETWEEN '{pm_f}' AND '{pm_t}'
                THEN f.customer_id END) AS cust_pm
        FROM fact_sales f
        WHERE f.posting_date BETWEEN '{pm_f}' AND '{cm_t}'
          {ent_frag}
    """
    row = session.execute(text(sql)).fetchone()
    rev_cm = float(row[0] or 0) if row else 0.0
    rev_pm = float(row[1] or 0) if row else 0.0
    inv_cm = float(row[2] or 0) if row else 0.0
    inv_pm = float(row[3] or 0) if row else 0.0
    cust_cm = float(row[4] or 0) if row else 0.0
    cust_pm = float(row[5] or 0) if row else 0.0

    com_sql = f"""
        SELECT
            COALESCE(SUM(CASE WHEN f.posting_date BETWEEN '{cm_f}' AND '{cm_t}'
                THEN f.cost_of_materials ELSE 0 END), 0) AS com_cm,
            COALESCE(SUM(CASE WHEN f.posting_date BETWEEN '{pm_f}' AND '{pm_t}'
                THEN f.cost_of_materials ELSE 0 END), 0) AS com_pm
        FROM fact_com f
        WHERE f.posting_date BETWEEN '{pm_f}' AND '{cm_t}'
          {ent_frag}
    """
    com_row = session.execute(text(com_sql)).fetchone()
    com_cm = float(com_row[0] or 0) if com_row else 0.0
    com_pm = float(com_row[1] or 0) if com_row else 0.0

    gp_cm = rev_cm - com_cm
    gp_pm = rev_pm - com_pm
    # rev_* are RAW EUR (cockpit `financial`-variant contract; frontend fmtKpi
    # applies the single ÷1000 to display kEUR). Meaningful-revenue threshold
    # for the margin guard is 500 EUR (= 0.5 kEUR).
    margin_cm = 100.0 * gp_cm / rev_cm if rev_cm > 500 else 0.0
    margin_pm = 100.0 * gp_pm / rev_pm if rev_pm > 500 else 0.0
    avg_rev_cust = rev_cm / cust_cm if cust_cm > 0 else 0.0
    avg_rev_cust_pm = rev_pm / cust_pm if cust_pm > 0 else 0.0
    avg_order = rev_cm / inv_cm if inv_cm > 0 else 0.0
    avg_order_pm = rev_pm / inv_pm if inv_pm > 0 else 0.0

    anchor = f"{year}-{month:02d}"
    zero = _kpi_block(0.0)
    churn_block = _churn_kpi_block(session, year, month, ent_frag)

    return {
        "anchor_period": anchor,
        "last_closed_week": anchor,
        "period_grain": "month",
        "month_revenue": _kpi_block(rev_cm, rev_pm),
        "month_gross_profit": _kpi_block(gp_cm, gp_pm),
        "week_revenue": _kpi_block(rev_cm, rev_pm),
        "week_gross_profit": _kpi_block(gp_cm, gp_pm),
        "avg_revenue_per_customer": _kpi_block(avg_rev_cust, avg_rev_cust_pm),
        "week_avg_revenue_per_customer": _kpi_block(avg_rev_cust, avg_rev_cust_pm),
        "churn_rate": churn_block,
        "week_churn_rate": churn_block,
        "customer_count": _kpi_block(cust_cm, cust_pm),
        "week_customer_count": _kpi_block(cust_cm, cust_pm),
        "units_sold": _kpi_block(inv_cm, inv_pm),
        "week_units_sold": _kpi_block(inv_cm, inv_pm),
        "gross_margin_pct": _kpi_block(margin_cm, margin_pm),
        "week_gross_margin_pct": _kpi_block(margin_cm, margin_pm),
        "avg_order_value": _kpi_block(avg_order, avg_order_pm),
        "week_avg_order_value": _kpi_block(avg_order, avg_order_pm),
        "net_revenue_retention": zero,
    }


def build_top_orders(
    session: Session,
    year: int,
    month: int,
    entity: Optional[str] = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    ep = resolve_entity_prefix(session, entity)
    ent_frag = ""
    if ep is not None:
        safe = str(ep).replace("'", "")[:2]
        ent_frag = f"AND LEFT(f.account_number_group, 2) = '{safe}'"
    cm_f, cm_t = _month_bounds(year, month)

    sql = f"""
        SELECT
            f.journal_entry_group_number AS order_id,
            MAX(COALESCE(NULLIF(TRIM(d.name_line_1), ''), f.customer_id, 'Unknown')) AS customer_name,
            SUM(f.gross_sales) / 1000.0 AS revenue_keur
        FROM fact_sales f
        LEFT JOIN dim_customer d ON d.customer_id = f.customer_id
        WHERE f.posting_date BETWEEN '{cm_f}' AND '{cm_t}'
          {ent_frag}
        GROUP BY f.journal_entry_group_number
        ORDER BY revenue_keur DESC
        LIMIT {int(limit)}
    """
    rows = session.execute(text(sql)).fetchall()
    orders = [
        {
            "invoice_number": str(r[0]),
            "amount_keur": round(float(r[2] or 0), 2),
            "customer_name": r[1] or "Unknown",
            "invoice_date": cm_t,
            "gross_profit_keur": round(float(r[2] or 0) * 0.35, 2),
            "gross_margin_pct": 35.0,
        }
        for r in rows
    ]
    return orders
