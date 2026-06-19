"""GL-derived sales analytics for the Profitability tab (fact_sales / fact_com).

Dimensions available without CRM data:
  end_customer_region, end_customer_name / top_customers, entity

Gross profit by customer/region uses entity-level CoM allocated by revenue share
within each entity (GL has no customer-level cost linkage).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.fin_compat_sql import col_labels_month, last_day, pm as _pm, resolve_entity_prefix
from app.services.geo_reference import sql_country_label_expr, sql_end_customer_region_expr

_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

_DIM_LABELS = {
    "end_customer_region": "End-customer region",
    "end_customer_name": "End customer",
    "top_customers": "Top customers",
    "entity": "Entity",
}

_ALLOWED_DIMS = frozenset(_DIM_LABELS)


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), last_day(year, month)


def _entity_frag(session: Session, entity: Optional[str]) -> str:
    ep = resolve_entity_prefix(session, entity)
    if ep is None:
        return ""
    safe = str(ep).replace("'", "")[:2]
    return f"AND LEFT(f.account_number_group, 2) = '{safe}'"


def _customer_geo_joins() -> str:
    return """
        LEFT JOIN dim_customer dc ON dc.customer_id = f.customer_id
        LEFT JOIN dim_region dr
          ON dr.country_code = dc.country_code AND dr.region_code = dc.region_code
        LEFT JOIN dim_country dco ON dco.country_code = dc.country_code
        LEFT JOIN dim_legal_entity le ON le.entity_prefix = LEFT(f.account_number_group, 2)
    """


def _dim_expr(dim: str) -> str:
    if dim in ("end_customer_name", "top_customers"):
        return "COALESCE(NULLIF(TRIM(dc.name_line_1), ''), f.customer_id, 'Unknown')"
    if dim == "entity":
        return "COALESCE(le.legal_entity_code, LEFT(f.account_number_group, 2), 'Unknown')"
    return sql_end_customer_region_expr("dc")


def _sales_from(dim: str, ent_frag: str) -> str:
    seg = _dim_expr(dim)
    return f"""
        SELECT
            {seg} AS seg,
            LEFT(f.account_number_group, 2) AS entity_prefix,
            SUM(f.gross_sales) / 1000.0 AS rev_keur
        FROM fact_sales f
        {_customer_geo_joins()}
        WHERE f.posting_date BETWEEN :d0 AND :d1
          {ent_frag}
        GROUP BY 1, 2
    """


def _com_by_entity(ent_frag: str) -> str:
    return f"""
        SELECT
            LEFT(f.account_number_group, 2) AS entity_prefix,
            SUM(f.cost_of_materials) / 1000.0 AS com_keur
        FROM fact_com f
        WHERE f.posting_date BETWEEN :d0 AND :d1
          {ent_frag}
        GROUP BY 1
    """


def _allocate_gp(
    sales_rows: list[tuple[str, str, float]],
    com_by_ent: dict[str, float],
) -> dict[str, tuple[float, float, float]]:
    """Return seg → (rev, gp, margin_pct) with entity-level CoM allocation."""
    ent_rev: dict[str, float] = {}
    seg_ent_rev: dict[str, dict[str, float]] = {}
    for seg, ent, rev in sales_rows:
        ent_rev[ent] = ent_rev.get(ent, 0.0) + rev
        by_ent = seg_ent_rev.setdefault(seg, {})
        by_ent[ent] = by_ent.get(ent, 0.0) + rev

    out: dict[str, tuple[float, float, float]] = {}
    for seg, by_ent in seg_ent_rev.items():
        rev = sum(by_ent.values())
        gp = 0.0
        for ent, r in by_ent.items():
            total = ent_rev.get(ent, 0.0)
            com = com_by_ent.get(ent, 0.0)
            if total > 1e-9:
                gp += r - com * (r / total)
        margin = 100.0 * gp / rev if rev > 1e-9 else 0.0
        out[seg] = (round(rev, 2), round(gp, 2), round(margin, 1))
    return out


def _period_metrics(
    session: Session,
    d0: date,
    d1: date,
    dim: str,
    entity: Optional[str],
) -> dict[str, tuple[float, float, float]]:
    ent_frag = _entity_frag(session, entity)
    sales_sql = _sales_from(dim, ent_frag)
    com_sql = _com_by_entity(ent_frag)
    params = {"d0": d0.isoformat(), "d1": d1.isoformat()}
    sales_rows = session.execute(text(sales_sql), params).fetchall()
    com_rows = session.execute(text(com_sql), params).fetchall()
    com_map = {str(r[0]): float(r[1] or 0) for r in com_rows}
    tuples = [(str(r[0]), str(r[1] or ""), float(r[2] or 0)) for r in sales_rows]
    return _allocate_gp(tuples, com_map)


def _normalize_dim(dim: str) -> str:
    if dim in ("top_customers", "end_customer_name"):
        return "end_customer_name"
    return dim if dim in _ALLOWED_DIMS else "end_customer_region"


def build_geo_countries(
    session: Session,
    year: int,
    month: int,
    entity: Optional[str] = None,
) -> list[dict[str, Any]]:
    ent_frag = _entity_frag(session, entity)
    cm_f, cm_t = _month_bounds(year, month)
    py_f, py_t = _month_bounds(year - 1, month)

    sql = f"""
        SELECT
            {sql_country_label_expr("dc")} AS country,
            COALESCE(SUM(CASE WHEN f.posting_date BETWEEN :cm_f AND :cm_t
                THEN f.gross_sales ELSE 0 END), 0) / 1000.0 AS revenue_keur,
            COALESCE(SUM(CASE WHEN f.posting_date BETWEEN :py_f AND :py_t
                THEN f.gross_sales ELSE 0 END), 0) / 1000.0 AS py_revenue_keur
        FROM fact_sales f
        {_customer_geo_joins()}
        WHERE f.posting_date BETWEEN :py_f AND :cm_t
          {ent_frag}
        GROUP BY 1
        HAVING SUM(CASE WHEN f.posting_date BETWEEN :cm_f AND :cm_t
            THEN f.gross_sales ELSE 0 END) > 0
        ORDER BY revenue_keur DESC
    """
    rows = session.execute(
        text(sql),
        {
            "cm_f": cm_f.isoformat(), "cm_t": cm_t.isoformat(),
            "py_f": py_f.isoformat(), "py_t": py_t.isoformat(),
        },
    ).fetchall()

    cm_metrics = _period_metrics(session, cm_f, cm_t, "end_customer_region", entity)
    margin_by_country: dict[str, float] = {
        seg: margin for seg, (_rev, _gp, margin) in cm_metrics.items()
    }

    out = []
    for r in rows:
        country = str(r[0])
        rev = float(r[1] or 0)
        py_rev = float(r[2] or 0)
        out.append({
            "country": country,
            "revenue_keur": round(rev, 2),
            "py_revenue_keur": round(py_rev, 2),
            "delta_keur": round(rev - py_rev, 2),
            "gross_margin_pct": margin_by_country.get(country, 0.0),
        })
    return out


def build_geo_country_locations(
    session: Session,
    country: str,
    year: int,
    month: int,
    entity: Optional[str] = None,
) -> dict[str, Any]:
    ent_frag = _entity_frag(session, entity)
    cm_f, cm_t = _month_bounds(year, month)
    country_esc = country.replace("'", "''")
    country_label = sql_country_label_expr("dc")
    sql = f"""
        SELECT
            TRIM(dc.city) AS city,
            {country_label} AS country,
            SUM(f.gross_sales) / 1000.0 AS revenue_keur,
            COUNT(DISTINCT f.customer_id) AS customer_count
        FROM fact_sales f
        {_customer_geo_joins()}
        WHERE f.posting_date BETWEEN :d0 AND :d1
          AND {country_label} = '{country_esc}'
          AND dc.city IS NOT NULL AND TRIM(dc.city) <> ''
          {ent_frag}
        GROUP BY 1, 2
        HAVING SUM(f.gross_sales) > 0
        ORDER BY revenue_keur DESC
    """
    rows = session.execute(text(sql), {"d0": cm_f.isoformat(), "d1": cm_t.isoformat()}).fetchall()
    locations = [
        {
            "city": r[0],
            "country": r[1],
            "postal_code": None,
            "revenue_keur": round(float(r[2] or 0), 2),
            "customer_count": int(r[3] or 0),
            "lat": None,
            "lon": None,
            "geo_source": None,
        }
        for r in rows
    ]
    return {
        "country": country,
        "period": f"{cm_f.isoformat()}..{cm_t.isoformat()}",
        "locations": locations,
        "mapped_count": 0,
        "total_count": len(locations),
    }


def build_geo_trend(
    session: Session,
    year: int,
    month: int,
    grain: str = "month",
    dim: str = "end_customer_region",
    entity: Optional[str] = None,
) -> dict[str, Any]:
    dim = _normalize_dim(dim)
    periods: list[dict[str, Any]] = []
    if grain == "year":
        for dy in range(4, -1, -1):
            fy = year - dy
            periods.append({
                "label": f"FY{str(fy)[-2:]}",
                "d0": date(fy, 1, 1),
                "d1": date(fy, 12, 31),
            })
    elif grain == "week":
        cm_t = last_day(year, month)
        for w in range(11, -1, -1):
            we = cm_t - timedelta(weeks=w)
            ws = we - timedelta(days=6)
            periods.append({"label": f"W{we.isocalendar()[1]:02d}", "d0": ws, "d1": we})
    else:
        y_p, m_p = year, month
        for _ in range(12):
            d0 = date(y_p, m_p, 1)
            d1 = last_day(y_p, m_p)
            periods.insert(0, {"label": f"{_ABBR[m_p - 1]}'{str(y_p)[-2:]}", "d0": d0, "d1": d1})
            m_p -= 1
            if m_p == 0:
                m_p, y_p = 12, y_p - 1

    seg_set: set[str] = set()
    period_labels: list[str] = []
    region_series: dict[str, list[float]] = {}

    for p in periods:
        period_labels.append(p["label"])
        metrics = _period_metrics(session, p["d0"], p["d1"], dim, entity)
        for seg in metrics:
            seg_set.add(seg)
        for seg in seg_set:
            region_series.setdefault(seg, [])
        for seg in seg_set:
            rev = metrics.get(seg, (0.0, 0.0, 0.0))[0]
            region_series[seg].append(rev)
        for seg in list(seg_set):
            if seg not in metrics:
                region_series[seg][-1] = 0.0

    regions = [
        {"name": seg, "values": region_series.get(seg, [0.0] * len(period_labels))}
        for seg in sorted(seg_set, key=lambda s: sum(region_series.get(s, [0])), reverse=True)[:12]
    ]
    return {"periods": period_labels, "regions": regions}


def build_gross_sales_trend(
    session: Session,
    year: int,
    month: int,
    dim: str = "end_customer_region",
    entity: Optional[str] = None,
) -> dict[str, Any]:
    dim = _normalize_dim(dim)
    periods_data: list[dict[str, Any]] = []
    y_p, m_p = year, month
    for _ in range(12):
        d0 = date(y_p, m_p, 1)
        d1 = last_day(y_p, m_p)
        metrics = _period_metrics(session, d0, d1, dim, entity)
        periods_data.insert(0, {
            "label": f"{_ABBR[m_p - 1]}'{str(y_p)[-2:]}",
            "values": {seg: v[0] for seg, v in metrics.items()},
        })
        m_p -= 1
        if m_p == 0:
            m_p, y_p = 12, y_p - 1

    seg_set: set[str] = set()
    for p in periods_data:
        seg_set.update(p["values"].keys())
    segments = sorted(seg_set, key=lambda s: sum(p["values"].get(s, 0) for p in periods_data), reverse=True)[:10]

    return {
        "period_grain": "month",
        "dim": dim,
        "range_label": {"from": periods_data[0]["label"], "to": periods_data[-1]["label"]},
        "segments": segments,
        "periods": [
            {"label": p["label"], "values": {s: p["values"].get(s, 0.0) for s in segments}}
            for p in periods_data
        ],
    }


def build_profit_margin_scatter(
    session: Session,
    year: int,
    month: int,
    dim: str = "end_customer_region",
    entity: Optional[str] = None,
) -> dict[str, Any]:
    dim = _normalize_dim(dim)
    cm_f, cm_t = _month_bounds(year, month)
    metrics = _period_metrics(session, cm_f, cm_t, dim, entity)
    short = col_labels_month(year, month)
    points = [
        {
            "segment": seg,
            "gross_sales_keur": rev,
            "gross_profit_keur": gp,
            "gross_margin_pct": margin,
        }
        for seg, (rev, gp, margin) in sorted(metrics.items(), key=lambda x: -x[1][0])
        if rev > 0.01
    ][:40]
    return {
        "period_grain": "month",
        "dim": dim,
        "period_label": short["cm"],
        "points": points,
    }


def build_composition_breakdown(
    session: Session,
    year: int,
    month: int,
    metric: str = "gross_sales",
    dims: Optional[str] = None,
    top_n: int = 8,
    entity: Optional[str] = None,
) -> dict[str, Any]:
    if metric not in ("gross_sales", "gross_profit", "gross_margin"):
        metric = "gross_sales"
    dim_keys = [d.strip() for d in (dims or "entity,end_customer_region,top_customers").split(",") if d.strip()]
    dim_keys = [_normalize_dim(d) for d in dim_keys if _normalize_dim(d) in _ALLOWED_DIMS]
    if not dim_keys:
        dim_keys = ["entity", "end_customer_region", "end_customer_name"]

    cm_f, cm_t = _month_bounds(year, month)
    short = col_labels_month(year, month)
    charts: list[dict[str, Any]] = []

    for dk in dim_keys[:4]:
        metrics = _period_metrics(session, cm_f, cm_t, dk, entity)
        items = sorted(metrics.items(), key=lambda x: -x[1][0])
        total_rev = sum(v[0] for _, v in items) or 1.0
        segments: list[dict[str, Any]] = []
        other = 0.0
        for i, (name, (rev, gp, margin)) in enumerate(items):
            if metric == "gross_profit":
                val = gp
            elif metric == "gross_margin":
                val = margin
            else:
                val = rev
            if i < top_n:
                segments.append({
                    "name": name,
                    "value_keur": round(val if metric != "gross_margin" else val, 2),
                    "share_pct": round((rev / total_rev) * 100, 1) if metric != "gross_margin" else val,
                })
            else:
                other += val
        if other > 0.01 and metric != "gross_margin":
            segments.append({
                "name": "Other",
                "value_keur": round(other, 2),
                "share_pct": round(other / total_rev * 100, 1) if total_rev else 0,
            })
        charts.append({
            "dim": dk,
            "dim_label": _DIM_LABELS.get(dk, dk),
            "segments": segments,
        })

    return {
        "metric": metric,
        "period_grain": "month",
        "period_label": short["cm"],
        "charts": charts,
    }


def build_metric_bridge(
    session: Session,
    year: int,
    month: int,
    dim: str = "end_customer_region",
    metric: str = "gross_sales",
    entity: Optional[str] = None,
) -> dict[str, Any]:
    dim = _normalize_dim(dim)
    if metric not in ("gross_sales", "gross_profit", "gross_margin"):
        metric = "gross_sales"

    pm_y, pm_m = _pm(year, month)
    pm_f, pm_t = _month_bounds(pm_y, pm_m)
    cm_f, cm_t = _month_bounds(year, month)
    pm_metrics = _period_metrics(session, pm_f, pm_t, dim, entity)
    cm_metrics = _period_metrics(session, cm_f, cm_t, dim, entity)

    def pick(m: dict[str, tuple[float, float, float]], seg: str) -> float:
        rev, gp, margin = m.get(seg, (0.0, 0.0, 0.0))
        if metric == "gross_profit":
            return gp
        if metric == "gross_margin":
            return margin
        return rev

    segs = set(pm_metrics) | set(cm_metrics)
    deltas = [(seg, pick(cm_metrics, seg) - pick(pm_metrics, seg)) for seg in segs]
    deltas.sort(key=lambda x: -abs(x[1]))
    top = deltas[:8]
    short = col_labels_month(year, month)
    pm_total = sum(pick(pm_metrics, s) for s in segs)
    cm_total = sum(pick(cm_metrics, s) for s in segs)

    return {
        "metric": metric,
        "dim": dim,
        "period_grain": "month",
        "period_label": short["cm"],
        "range_label": {"from": short["pm"], "to": short["cm"]},
        "periods": [short["pm"], short["cm"]],
        "period_totals": [round(pm_total, 2), round(cm_total, 2)],
        "period_display": [short["pm"], short["cm"]],
        "bridges": [{
            "from": short["pm"],
            "to": short["cm"],
            "segments": [{"name": seg, "delta_keur": round(d, 2)} for seg, d in top],
        }],
        "value_unit": "keur",
    }


def build_breakdown_table(
    session: Session,
    year: int,
    month: int,
    dim_top: str = "end_customer_region",
    dim_mid: str = "",
    dim_bottom: str = "entity",
    entity: Optional[str] = None,
) -> dict[str, Any]:
    dim_top = _normalize_dim(dim_top)
    dim_bottom = _normalize_dim(dim_bottom)
    dim_mid = _normalize_dim(dim_mid) if dim_mid else ""

    pm_y, pm_m = _pm(year, month)
    pm_f, pm_t = _month_bounds(pm_y, pm_m)
    cm_f, cm_t = _month_bounds(year, month)
    short = col_labels_month(year, month)

    pm_top = _period_metrics(session, pm_f, pm_t, dim_top, entity)
    cm_top = _period_metrics(session, cm_f, cm_t, dim_top, entity)
    segs = sorted(set(pm_top) | set(cm_top), key=lambda s: -cm_top.get(s, (0, 0, 0))[0])

    rows: list[dict[str, Any]] = []
    for seg in segs[:50]:
        gs_pm, gp_pm, gm_pm = pm_top.get(seg, (0.0, 0.0, 0.0))
        gs_cm, gp_cm, gm_cm = cm_top.get(seg, (0.0, 0.0, 0.0))
        rows.append({
            "level": 0,
            "dim_top": seg,
            "dim_mid": None,
            "dim_bottom": None,
            "gs_pm": gs_pm,
            "gs_cm": gs_cm,
            "gp_pm": gp_pm,
            "gp_cm": gp_cm,
            "gm_pm": gm_pm,
            "gm_cm": gm_cm,
            "gs_plan_cm": 0.0,
            "gp_plan_cm": 0.0,
            "delta_gs_cm_pm": round(gs_cm - gs_pm, 2),
            "delta_gp_cm_pm": round(gp_cm - gp_pm, 2),
            "delta_gm_cm_pm": round(gm_cm - gm_pm, 1),
        })

    return {
        "period_grain": "month",
        "col_labels": {
            "pm": short["pm"],
            "cm": short["cm"],
            "plan_cm": f"Plan {short['cm']}",
            "delta_cm_pm": f"{short['cm']} - {short['pm']}",
        },
        "dim_labels": {
            "top": _DIM_LABELS.get(dim_top, dim_top),
            "mid": _DIM_LABELS.get(dim_mid, dim_mid) if dim_mid else None,
            "bottom": _DIM_LABELS.get(dim_bottom, dim_bottom),
        },
        "has_mid_level": bool(dim_mid),
        "rows": rows,
    }


def build_dimension_performance(
    session: Session,
    year: int,
    month: int,
    dim: str = "entity",
    metric: str = "gross_sales",
    period_scope: str = "month",
    entity: Optional[str] = None,
) -> dict[str, Any]:
    dim = _normalize_dim(dim)
    if metric not in ("gross_sales", "gross_profit", "units_sold"):
        metric = "gross_sales"

    cm_f, cm_t = _month_bounds(year, month)
    if period_scope == "ytd":
        d0 = date(year, 1, 1)
    elif period_scope == "py_month":
        d0, d1 = _month_bounds(year - 1, month)
        cm_f, cm_t = d0, d1
    else:
        d0 = cm_f
    metrics = _period_metrics(session, d0 if period_scope == "ytd" else cm_f, cm_t, dim, entity)

    inv_sql = f"""
        SELECT {_dim_expr(dim)} AS seg,
               COUNT(DISTINCT f.journal_entry_group_number) AS inv
        FROM fact_sales f
        {_customer_geo_joins()}
        WHERE f.posting_date BETWEEN :d0 AND :d1
          {_entity_frag(session, entity)}
        GROUP BY 1
    """
    inv_rows = session.execute(
        text(inv_sql),
        {"d0": (d0 if period_scope == "ytd" else cm_f).isoformat(), "d1": cm_t.isoformat()},
    ).fetchall()
    inv_map = {str(r[0]): float(r[1] or 0) for r in inv_rows}

    segments = []
    for seg, (rev, gp, _margin) in sorted(metrics.items(), key=lambda x: -x[1][0])[:15]:
        actual = inv_map.get(seg, 0) if metric == "units_sold" else (gp if metric == "gross_profit" else rev)
        segments.append({"name": seg, "actual": round(actual, 2), "plan": 0.0, "prior": 0.0})

    short = col_labels_month(year, month)
    metric_label = {"gross_sales": "Gross sales", "gross_profit": "Gross profit", "units_sold": "# Invoices"}[metric]
    return {
        "dim": dim,
        "dim_label": _DIM_LABELS.get(dim, dim),
        "metric": metric,
        "metric_label": metric_label,
        "period_scope": period_scope,
        "period_grain": "month",
        "period_label": short["cm"] if period_scope == "month" else short["ytd"],
        "prior_label": short["pm"],
        "value_unit": "units" if metric == "units_sold" else "keur",
        "chart": {"segments": segments},
        "matrix": {"columns": [], "rows": []},
    }


def build_gross_margin_matrix(
    session: Session,
    year: int,
    month: int,
    dim: str = "entity",
    entity: Optional[str] = None,
) -> dict[str, Any]:
    dim = _normalize_dim(dim)
    cm_f, cm_t = _month_bounds(year, month)
    metrics = _period_metrics(session, cm_f, cm_t, dim, entity)
    short = col_labels_month(year, month)
    col_key = "cm"
    rows = []
    for seg, (rev, gp, margin) in sorted(metrics.items(), key=lambda x: -x[1][0])[:20]:
        rows.append({
            "segment": seg,
            "periods": {
                col_key: {
                    "gross_sales_keur": rev,
                    "gross_profit_keur": gp,
                    "gross_margin_pct": margin,
                    "units_sold": 0,
                },
            },
        })
    return {
        "dim": dim,
        "period_grain": "month",
        "columns": [{"key": col_key, "label": short["cm"]}],
        "rows": rows,
    }


def build_churn_bridge(
    session: Session,
    year: int,
    month: int,
    grain: str = "month",
    dim: str = "entity",
    entity: Optional[str] = None,
) -> dict[str, Any]:
    """Customer cohort bridge: new / lost / retained by revenue (kEUR)."""
    dim = _normalize_dim(dim)
    pm_y, pm_m = _pm(year, month)
    pm_f, pm_t = _month_bounds(pm_y, pm_m)
    cm_f, cm_t = _month_bounds(year, month)
    ent_frag = _entity_frag(session, entity)

    sql = f"""
        WITH pm AS (
            SELECT f.customer_id, {_dim_expr(dim)} AS seg,
                   SUM(f.gross_sales) / 1000.0 AS rev
            FROM fact_sales f
            {_customer_geo_joins()}
            WHERE f.posting_date BETWEEN :pm_f AND :pm_t AND f.customer_id IS NOT NULL {ent_frag}
            GROUP BY 1, 2
        ),
        cm AS (
            SELECT f.customer_id, {_dim_expr(dim)} AS seg,
                   SUM(f.gross_sales) / 1000.0 AS rev
            FROM fact_sales f
            {_customer_geo_joins()}
            WHERE f.posting_date BETWEEN :cm_f AND :cm_t AND f.customer_id IS NOT NULL {ent_frag}
            GROUP BY 1, 2
        )
        SELECT
            COALESCE(cm.seg, pm.seg) AS seg,
            COALESCE(SUM(pm.rev), 0) AS from_rev,
            COALESCE(SUM(cm.rev), 0) AS to_rev,
            COALESCE(SUM(CASE WHEN pm.customer_id IS NULL THEN cm.rev END), 0) AS new_rev,
            COALESCE(SUM(CASE WHEN cm.customer_id IS NULL THEN pm.rev END), 0) AS lost_rev,
            COALESCE(SUM(CASE WHEN pm.customer_id IS NOT NULL AND cm.customer_id IS NOT NULL
                THEN cm.rev - pm.rev END), 0) AS retained_delta
        FROM pm
        FULL OUTER JOIN cm ON pm.customer_id = cm.customer_id
        GROUP BY 1
    """
    rows = session.execute(
        text(sql),
        {
            "pm_f": pm_f.isoformat(), "pm_t": pm_t.isoformat(),
            "cm_f": cm_f.isoformat(), "cm_t": cm_t.isoformat(),
        },
    ).fetchall()

    short = col_labels_month(year, month)
    bridges = []
    for r in rows:
        seg = str(r[0])
        new_r = float(r[3] or 0)
        lost_r = float(r[4] or 0)
        delta = float(r[5] or 0)
        upsell = max(delta, 0.0)
        downsell = min(delta, 0.0)
        bridges.append({
            "segment": seg,
            "from_rev_keur": round(float(r[1] or 0), 2),
            "to_rev_keur": round(float(r[2] or 0), 2),
            "new": round(new_r, 2),
            "upsell": round(upsell, 2),
            "cross_sell": 0.0,
            "downsell": round(downsell, 2),
            "lost": round(lost_r, 2),
        })

    total_from = sum(b["from_rev_keur"] for b in bridges)
    total_to = sum(b["to_rev_keur"] for b in bridges)
    return {
        "periods": [short["pm"], short["cm"]],
        "period_totals": [round(total_from, 2), round(total_to, 2)],
        "bridges": bridges,
        "dim": dim,
        "dim_label": _DIM_LABELS.get(dim, dim),
    }
