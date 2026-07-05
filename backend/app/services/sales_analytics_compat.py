"""GL-derived sales analytics for the Profitability tab (fact_sales / fact_com).

Dimensions available without CRM data (the canonical 4-dim vocabulary):
  end_customer_region (Region), end_customer_city (City),
  end_customer_name (Customer), entity (Entity)

Gross profit by customer/region uses entity-level CoM allocated by revenue share
within each entity (GL has no customer-level cost linkage).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.fin_compat_sql import (
    col_labels_month,
    iso_week_of,
    last_day,
    pm as _pm,
    prior_iso_week,
    resolve_entity_prefix,
    week_range,
)
from app.services.geo_reference import sql_country_label_expr, sql_end_customer_region_expr

_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Canonical 4-dimension vocabulary for this GL-only dataset (finssentials_v2):
# Region / City / Customer / Entity.  These four keys are the ONLY dims any sales
# analytics endpoint accepts; unknown dims normalise to ``end_customer_region``.
# ``top_customers`` is a legacy alias folded into ``end_customer_name`` (see
# :func:`_normalize_dim`); it is intentionally absent here.
_DIM_LABELS = {
    "end_customer_region": "Region",
    "end_customer_city": "City",
    "end_customer_name": "Customer",
    "entity": "Entity",
}

_ALLOWED_DIMS = frozenset(_DIM_LABELS)


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), last_day(year, month)


def trailing_iso_weeks(
    year: int, month: int, n: int = 12,
) -> list[dict[str, Any]]:
    """The ``n`` ISO weeks ending at the anchor week, oldest→newest (FLOW windows).

    The anchor week is the ISO week that contains the anchor month-END date
    (``last_day(year, month)``).  Each entry is a true ISO-week FLOW window from
    :func:`week_range` — so a sales week sums ``fact_sales.gross_sales`` whose
    ``posting_date`` falls in that Monday..Sunday span, the same grain BS/PL/WC/CF
    weekly already use.  This REPLACES the previous rolling-7-day-from-month-end
    approximation (windows that were not Monday-aligned and not ISO weeks).

    == WORKED EXAMPLE (year=2025, month=7, n=3) ==
        anchor month-end 2025-07-31 → ISO 2025-W31 (Mon 2025-07-28..Sun 2025-08-03).
        Walking back: 2025-W31, 2025-W30, 2025-W29.  Returned oldest→newest:
        [W29 (2025-07-14..20), W30 (2025-07-21..27), W31 (2025-07-28..08-03)].

    == EDGE CASES ==
        * Year boundary: an anchor in early January walks back across the ISO-year
          boundary via :func:`prior_iso_week` (e.g. 2026-W01 → 2025-W52/W53),
          handled by date arithmetic, not by decrementing the week number.
        * ISO week 53 long years are produced naturally when stepped over.
    """
    anchor_iy, anchor_iw = iso_week_of(last_day(year, month))
    weeks: list[tuple[int, int]] = []
    iy, iw = anchor_iy, anchor_iw
    for _ in range(max(1, n)):
        weeks.append((iy, iw))
        iy, iw = prior_iso_week(iy, iw)
    weeks.reverse()  # oldest → newest
    out: list[dict[str, Any]] = []
    for wy, ww in weeks:
        d0, d1 = week_range(wy, ww)
        out.append({
            "iso_year": wy, "iso_week": ww,
            "label": f"CW{ww:02d}'{str(wy)[-2:]}",
            "d0": d0, "d1": d1,
        })
    return out


def _entity_frag(session: Session, entity: Optional[str]) -> str:
    ep = resolve_entity_prefix(session, entity)
    if ep is None:
        return ""
    safe = str(ep).replace("'", "")[:2]
    return f"AND LEFT(f.account_number_group, 2) = '{safe}'"


def _entity_frag_scoped(
    session: Session,
    entity: Optional[str],
    allowed_entities: Optional[set[str]],
) -> Optional[str]:
    """Entity SQL fragment honouring fail-closed tenant visibility.

    Contract (identical to partner_development._resolve_entity_frag):
      * ``allowed_entities is None``   → admin/unrestricted; resolve ``entity``
                                         (legacy single-entity path, "" for all).
      * ``allowed_entities`` non-empty → ``AND LEFT(...,2) IN (...)`` (``entity``
                                         already intersected by the caller → ignored).
      * ``allowed_entities`` empty     → **None** = fail-closed sentinel; the caller
                                         MUST short-circuit to the empty response and
                                         issue NO SQL (nothing can leak).
    """
    if allowed_entities is not None:
        safe = sorted({str(p).replace("'", "")[:2] for p in allowed_entities if p})
        if not safe:
            return None  # fail closed
        inner = ", ".join(f"'{p}'" for p in safe)
        return f"AND LEFT(f.account_number_group, 2) IN ({inner})"
    return _entity_frag(session, entity)


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
    if dim == "end_customer_city":
        return "COALESCE(NULLIF(TRIM(dc.city), ''), 'Unknown')"
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
    allowed_entities: Optional[set[str]] = None,
) -> dict[str, tuple[float, float, float]]:
    ent_frag = _entity_frag_scoped(session, entity, allowed_entities)
    if ent_frag is None:
        return {}  # fail closed: empty visibility → no rows, no SQL leak
    sales_sql = _sales_from(dim, ent_frag)
    com_sql = _com_by_entity(ent_frag)
    params = {"d0": d0.isoformat(), "d1": d1.isoformat()}
    sales_rows = session.execute(text(sales_sql), params).fetchall()
    com_rows = session.execute(text(com_sql), params).fetchall()
    com_map = {str(r[0]): float(r[1] or 0) for r in com_rows}
    tuples = [(str(r[0]), str(r[1] or ""), float(r[2] or 0)) for r in sales_rows]
    return _allocate_gp(tuples, com_map)


def _leaf_metrics(
    session: Session,
    d0: date,
    d1: date,
    dims: list[str],
    ent_frag: str,
) -> dict[tuple[str, ...], tuple[float, float, float]]:
    """Revenue + entity-allocated gross profit grouped by an N-dim leaf key.

    Groups ``fact_sales`` by the chosen dim expressions PLUS ``entity_prefix`` so
    CoM (only known at entity level) can be allocated by revenue share within each
    entity — the SAME allocation as :func:`_allocate_gp`, keyed on the leaf tuple.
    Returns ``{(l0, l1, ...): (rev_keur, gp_keur, margin_pct)}``.
    """
    exprs = [_dim_expr(d) for d in dims]
    n = len(exprs)
    select_cols = ", ".join(f"{e} AS l{i}" for i, e in enumerate(exprs))
    group_cols = ", ".join(str(i + 1) for i in range(n))
    sales_sql = f"""
        SELECT {select_cols},
               LEFT(f.account_number_group, 2) AS entity_prefix,
               SUM(f.gross_sales) / 1000.0 AS rev_keur
        FROM fact_sales f
        {_customer_geo_joins()}
        WHERE f.posting_date BETWEEN :d0 AND :d1
          {ent_frag}
        GROUP BY {group_cols}, {n + 1}
    """
    params = {"d0": d0.isoformat(), "d1": d1.isoformat()}
    sales_rows = session.execute(text(sales_sql), params).fetchall()
    com_rows = session.execute(text(_com_by_entity(ent_frag)), params).fetchall()
    com_map = {str(r[0]): float(r[1] or 0) for r in com_rows}
    tuples: list[tuple[Any, str, float]] = []
    for r in sales_rows:
        seg = tuple(str(r[i]) for i in range(n))
        ent = str(r[n] or "")
        rev = float(r[n + 1] or 0)
        tuples.append((seg, ent, rev))
    # _allocate_gp keys on the seg value (here a tuple) — tuples hash fine.
    return _allocate_gp(tuples, com_map)  # type: ignore[arg-type]


def _invoice_counts(
    session: Session,
    d0: date,
    d1: date,
    dim: str,
    ent_frag: str,
) -> dict[str, float]:
    """Distinct invoice (journal-entry-group) counts per dim segment."""
    sql = f"""
        SELECT {_dim_expr(dim)} AS seg,
               COUNT(DISTINCT f.journal_entry_group_number) AS inv
        FROM fact_sales f
        {_customer_geo_joins()}
        WHERE f.posting_date BETWEEN :d0 AND :d1
          {ent_frag}
        GROUP BY 1
    """
    rows = session.execute(
        text(sql), {"d0": d0.isoformat(), "d1": d1.isoformat()},
    ).fetchall()
    return {str(r[0]): float(r[1] or 0) for r in rows}


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
        # TRUE ISO-week granularity (reporting-v2 Phase 5): each bucket sums
        # gross_sales whose posting_date falls in a Monday..Sunday ISO week via
        # ``week_range`` — NOT the previous rolling-7-day-from-month-end windows.
        for wk in trailing_iso_weeks(year, month, n=12):
            periods.append({
                "label": wk["label"], "d0": wk["d0"], "d1": wk["d1"],
            })
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

    region_names = [
        seg
        for seg in sorted(seg_set, key=lambda s: sum(region_series.get(s, [0])), reverse=True)[:12]
    ]
    period_rows: list[dict[str, Any]] = []
    for i, label in enumerate(period_labels):
        row: dict[str, Any] = {"label": label}
        for seg in region_names:
            series = region_series.get(seg, [0.0] * len(period_labels))
            row[seg] = series[i] if i < len(series) else 0.0
        period_rows.append(row)
    return {"periods": period_rows, "regions": region_names}


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
    allowed_entities: Optional[set[str]] = None,
) -> dict[str, Any]:
    """PM→CM leaf table grouped by (top[, mid], bottom) dims — all money in kEUR.

    Emits one LEAF row per (l1, l2, l3) combination (l2 is ``None`` when no mid
    dim is chosen) with gross-sales / gross-profit / margin for PM & CM and their
    deltas.  Capped at the top 500 leaves by CM gross sales.  ``allowed_entities``
    (fail-closed tenant visibility) — see :func:`_entity_frag_scoped`.
    """
    dim_top = _normalize_dim(dim_top)
    dim_bottom = _normalize_dim(dim_bottom)
    dim_mid = _normalize_dim(dim_mid) if dim_mid else ""

    pm_y, pm_m = _pm(year, month)
    pm_f, pm_t = _month_bounds(pm_y, pm_m)
    cm_f, cm_t = _month_bounds(year, month)
    short = col_labels_month(year, month)

    col_labels = {
        "pm": short["pm"],
        "cm": short["cm"],
        "plan_cm": f"Plan {short['cm']}",
        "delta_cm_pm": f"{short['cm']} - {short['pm']}",
    }
    dim_labels = {
        "top": _DIM_LABELS.get(dim_top, dim_top),
        "mid": _DIM_LABELS.get(dim_mid, dim_mid) if dim_mid else None,
        "bottom": _DIM_LABELS.get(dim_bottom, dim_bottom),
    }

    ent_frag = _entity_frag_scoped(session, entity, allowed_entities)
    if ent_frag is None:  # fail-closed empty visibility
        return {
            "period_grain": "month",
            "col_labels": col_labels,
            "dim_labels": dim_labels,
            "has_mid_level": bool(dim_mid),
            "rows": [],
        }

    dims = [dim_top] + ([dim_mid] if dim_mid else []) + [dim_bottom]
    pm_leaf = _leaf_metrics(session, pm_f, pm_t, dims, ent_frag)
    cm_leaf = _leaf_metrics(session, cm_f, cm_t, dims, ent_frag)
    keys = sorted(
        set(pm_leaf) | set(cm_leaf),
        key=lambda k: -cm_leaf.get(k, (0.0, 0.0, 0.0))[0],
    )

    rows: list[dict[str, Any]] = []
    for key in keys[:500]:
        gs_pm, gp_pm, gm_pm = pm_leaf.get(key, (0.0, 0.0, 0.0))
        gs_cm, gp_cm, gm_cm = cm_leaf.get(key, (0.0, 0.0, 0.0))
        if dim_mid:
            l1, l2, l3 = key[0], key[1], key[2]
        else:
            l1, l2, l3 = key[0], None, key[1]
        rows.append({
            "l1": l1,
            "l2": l2,
            "l3": l3,
            "gs_pm": gs_pm,
            "gs_cm": gs_cm,
            "gp_pm": gp_pm,
            "gp_cm": gp_cm,
            "gm_pm": gm_pm,
            "gm_cm": gm_cm,
            "gs_plan_cm": 0.0,
            "gp_plan_cm": 0.0,
            "gm_plan_cm": 0.0,
            "delta_gs_cm_pm": round(gs_cm - gs_pm, 2),
            "delta_gp_cm_pm": round(gp_cm - gp_pm, 2),
            "delta_gm_cm_pm": round(gm_cm - gm_pm, 1),
        })

    return {
        "period_grain": "month",
        "col_labels": col_labels,
        "dim_labels": dim_labels,
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
    allowed_entities: Optional[set[str]] = None,
) -> dict[str, Any]:
    """Per-dimension chart (actual vs prior) + a gross-sales-by-period matrix.

    ``chart.segments`` = top-15 dim values for the scope period, each carrying
    ``actual`` (scope-period metric), ``prior`` (previous-month actual of the same
    metric), ``delta_prior = actual - prior`` and zeroed plan fields (no plan data).
    ``matrix`` = months Jan..``month`` of ``year`` (columns) × top-15 dim values
    (rows) of gross sales in kEUR, each row with a ``total``.  ``allowed_entities``
    (fail-closed tenant visibility) — see :func:`_entity_frag_scoped`.
    """
    dim = _normalize_dim(dim)
    if metric not in ("gross_sales", "gross_profit", "units_sold"):
        metric = "gross_sales"

    short = col_labels_month(year, month)
    metric_label = {
        "gross_sales": "Gross sales", "gross_profit": "Gross profit", "units_sold": "# Invoices",
    }[metric]

    def _envelope(segments: list[dict[str, Any]], matrix: dict[str, Any]) -> dict[str, Any]:
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
            "matrix": matrix,
        }

    ent_frag = _entity_frag_scoped(session, entity, allowed_entities)
    if ent_frag is None:  # fail-closed empty visibility
        return _envelope([], {"columns": [], "rows": []})

    cm_f, cm_t = _month_bounds(year, month)
    if period_scope == "ytd":
        d0, d1 = date(year, 1, 1), cm_t
    elif period_scope == "py_month":
        d0, d1 = _month_bounds(year - 1, month)
    else:
        d0, d1 = cm_f, cm_t

    # Prior comparison window = the month before the anchor month (matches prior_label).
    pm_y, pm_m = _pm(year, month)
    pm_f, pm_t = _month_bounds(pm_y, pm_m)

    metrics = _period_metrics(session, d0, d1, dim, entity, allowed_entities)
    prior_metrics = _period_metrics(session, pm_f, pm_t, dim, entity, allowed_entities)

    inv_map: dict[str, float] = {}
    prior_inv_map: dict[str, float] = {}
    if metric == "units_sold":
        inv_map = _invoice_counts(session, d0, d1, dim, ent_frag)
        prior_inv_map = _invoice_counts(session, pm_f, pm_t, dim, ent_frag)

    def _val(m: dict[str, tuple[float, float, float]], inv: dict[str, float], seg: str) -> float:
        rev, gp, _margin = m.get(seg, (0.0, 0.0, 0.0))
        if metric == "units_sold":
            return inv.get(seg, 0.0)
        if metric == "gross_profit":
            return gp
        return rev

    segments: list[dict[str, Any]] = []
    for seg, _vals in sorted(metrics.items(), key=lambda x: -x[1][0])[:15]:
        actual = _val(metrics, inv_map, seg)
        prior = _val(prior_metrics, prior_inv_map, seg)
        segments.append({
            "name": seg,
            "actual": round(actual, 2),
            "prior": round(prior, 2),
            "plan": 0.0,
            "delta_prior": round(actual - prior, 2),
            "delta_plan": 0.0,
            "has_plan": False,
        })

    # Matrix: gross sales (kEUR) per month Jan..month of the anchor year.
    columns: list[dict[str, str]] = []
    month_metrics: list[dict[str, tuple[float, float, float]]] = []
    for m in range(1, month + 1):
        mf, mt = _month_bounds(year, m)
        columns.append({"key": f"m{m}", "label": f"{_ABBR[m - 1]}'{str(year)[-2:]}"})
        month_metrics.append(_period_metrics(session, mf, mt, dim, entity, allowed_entities))

    seg_totals: dict[str, float] = {}
    for mm in month_metrics:
        for seg, (rev, _gp, _margin) in mm.items():
            seg_totals[seg] = seg_totals.get(seg, 0.0) + rev
    top_segs = sorted(seg_totals, key=lambda s: -seg_totals[s])[:15]

    matrix_rows: list[dict[str, Any]] = []
    for seg in top_segs:
        values = [round(mm.get(seg, (0.0, 0.0, 0.0))[0], 2) for mm in month_metrics]
        matrix_rows.append({"name": seg, "values": values, "total": round(sum(values), 2)})

    return _envelope(segments, {"columns": columns, "rows": matrix_rows})


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
    allowed_entities: Optional[set[str]] = None,
) -> dict[str, Any]:
    """PM→CM customer revenue bridge (kEUR): new / upsell / cross_sell / downsell / lost.

    Returns ONE aggregate ``bridge`` transition plus a per-dimension ``table_rows``
    breakdown (exactly 2 ``periods``: PM, CM).  ``grain`` is accepted for API compat
    but a churn bridge is always a single PM→CM month step.

    RECONCILIATION IDENTITY (waterfall closes exactly):
        from_total + new + upsell + cross_sell + downsell + lost == to_total
    Signs: new/upsell/cross_sell >= 0; downsell/lost <= 0.  ``lost`` is the negated
    PM revenue of customers absent in CM; ``new`` is CM revenue of customers absent
    in PM; upsell/downsell are the positive/negative parts of the retained delta.
    ``allowed_entities`` (fail-closed tenant visibility) — see :func:`_entity_frag_scoped`.
    """
    dim = _normalize_dim(dim)
    pm_y, pm_m = _pm(year, month)
    pm_f, pm_t = _month_bounds(pm_y, pm_m)
    cm_f, cm_t = _month_bounds(year, month)
    short = col_labels_month(year, month)

    ent_frag = _entity_frag_scoped(session, entity, allowed_entities)
    if ent_frag is None:  # fail-closed empty visibility
        return {
            "periods": [short["pm"], short["cm"]],
            "period_totals": [0.0, 0.0],
            "bridge": {
                "new": 0.0, "upsell": 0.0, "cross_sell": 0.0, "downsell": 0.0, "lost": 0.0,
            },
            "table_rows": [],
            "dim": dim,
            "dim_label": _DIM_LABELS.get(dim, dim),
        }

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

    table_rows: list[dict[str, Any]] = []
    agg = {"new": 0.0, "upsell": 0.0, "cross_sell": 0.0, "downsell": 0.0, "lost": 0.0}
    total_from = 0.0
    total_to = 0.0
    for r in rows:
        seg = str(r[0])
        from_r = float(r[1] or 0)
        to_r = float(r[2] or 0)
        new_r = float(r[3] or 0)
        lost_r = -float(r[4] or 0)  # PM revenue of customers gone in CM → negative
        delta = float(r[5] or 0)    # retained CM-PM delta
        upsell = max(delta, 0.0)
        downsell = min(delta, 0.0)
        cross_sell = 0.0            # GL has no product linkage → cross-sell unknown
        table_rows.append({
            "dim_value": seg,
            "from_keur": round(from_r, 2),
            "to_keur": round(to_r, 2),
            "new": round(new_r, 2),
            "upsell": round(upsell, 2),
            "cross_sell": cross_sell,
            "downsell": round(downsell, 2),
            "lost": round(lost_r, 2),
        })
        agg["new"] += new_r
        agg["upsell"] += upsell
        agg["cross_sell"] += cross_sell
        agg["downsell"] += downsell
        agg["lost"] += lost_r
        total_from += from_r
        total_to += to_r

    table_rows.sort(key=lambda x: -x["to_keur"])
    return {
        "periods": [short["pm"], short["cm"]],
        "period_totals": [round(total_from, 2), round(total_to, 2)],
        "bridge": {k: round(v, 2) for k, v in agg.items()},
        "table_rows": table_rows,
        "dim": dim,
        "dim_label": _DIM_LABELS.get(dim, dim),
    }
