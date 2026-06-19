"""Top customers / suppliers for the legacy-compat layer (GDPdU backend).

Serves ``GET /api/v1/sales/top-entities`` (api.salesTopEntities), returning the
legacy ``SalesTopEntitiesResponse`` shape consumed by the verbatim-ported sales
dashboard.

  type=customer → fact_sales  + dim_customer  (gross_sales,       customer_id)
  type=supplier → fact_com    + dim_supplier  (cost_of_materials, supplier_id)

============================================================ SCALE & SIGN
Values are reported in **kEUR** (Σ / 1000), matching the legacy
``routers/sales.py::get_top_entities`` contract.  ``fact_sales.gross_sales`` is
already stored as ``-amount`` (revenue credit → positive) and
``fact_com.cost_of_materials`` as ``+amount`` (cost debit → positive), so both
metrics are naturally positive magnitudes; no sign flip is applied here.

============================================================ PERIODS
fact_sales / fact_com carry ``posting_date`` (no fiscal_period), so the period
windows are calendar-month posting_date ranges (mirrors the legacy ``_period`` /
``_ytd`` helpers):
  cm     = [first(year, month)   .. last(year, month)]
  pm     = [first(prior month)   .. last(prior month)]
  py_cm  = [first(year-1, month) .. last(year-1, month)]
  ytd    = [first(year, 1)       .. last(year, month)]
  ytd_py = [first(year-1, 1)     .. last(year-1, month)]

Entity filter: ``LEFT(account_number_group, 2) = entity_prefix`` (resolved from
legal_entity_code).  Plan is not wired in this slim port → plan_cm / coverage are
0 (clean fallback), plan_mix = 'py_proxy'.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.fin_compat_sql import (
    col_labels_month,
    last_day,
    pm as _pm,
    resolve_entity_prefix,
)


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    return date(year, month, 1).isoformat(), last_day(year, month).isoformat()


def _ytd_bounds(year: int, month: int) -> tuple[str, str]:
    return date(year, 1, 1).isoformat(), last_day(year, month).isoformat()


# ---------------------------------------------------------------------------
# Pure ranking / row assembly (DB-free, testable)
# ---------------------------------------------------------------------------

def rank_top_entities(
    agg_rows: list[dict[str, Any]],
    *,
    id_field: str,
    rank_by: str = "cm",
    limit: int = 5000,
) -> list[dict[str, Any]]:
    """Rank aggregated partner rows and attach deltas / placeholder plan fields.

    ``agg_rows`` : one dict per partner, with keys ``name``, ``<id_field>`` and the
                   period sums (kEUR) cm / pm / py_cm / ytd / ytd_py.
    ``id_field`` : 'customer_id' or 'supplier_id'.
    ``rank_by``  : 'cm' (default) or 'ytd' — the descending sort key.

    === FORMULAS ===
      delta_cm_pm = cm  - pm
      delta_cm_py = cm  - py_cm
      delta_ytd   = ytd - ytd_py
      rank        = 1-based position after sorting by ``rank_by`` desc.

    === WORKED EXAMPLE (rank_by='cm') ===
      A: cm=120, pm=100, py_cm=90, ytd=700, ytd_py=600
      B: cm=200, pm=150, py_cm=210, ytd=900, ytd_py=950
        → sorted [B, A]; B.rank=1 delta_cm_pm=50 delta_cm_py=-10 delta_ytd=-50,
          A.rank=2 delta_cm_pm=20 delta_cm_py=30 delta_ytd=100.

    === EDGE CASES ===
      * rank_by not in {cm, ytd} → defaults to 'cm'.
      * Rows with all-zero metrics are dropped (kept only if any |metric| > 1e-6),
        matching the legacy HAVING filter.
      * limit caps the returned rows after ranking.
    """
    sort_key = "ytd" if rank_by == "ytd" else "cm"

    kept = [
        r for r in agg_rows
        if any(abs(float(r.get(k) or 0.0)) > 1e-6
               for k in ("cm", "pm", "py_cm", "ytd", "ytd_py"))
    ]
    kept.sort(key=lambda r: float(r.get(sort_key) or 0.0), reverse=True)
    kept = kept[:limit]

    out: list[dict[str, Any]] = []
    for i, r in enumerate(kept, start=1):
        cm = round(float(r.get("cm") or 0.0), 2)
        pm = round(float(r.get("pm") or 0.0), 2)
        py_cm = round(float(r.get("py_cm") or 0.0), 2)
        ytd = round(float(r.get("ytd") or 0.0), 2)
        ytd_py = round(float(r.get("ytd_py") or 0.0), 2)
        out.append({
            "rank": i,
            "name": r.get("name"),
            id_field: r.get(id_field),
            "entity": None,
            "cm": cm,
            "pm": pm,
            "py_cm": py_cm,
            "ytd": ytd,
            "ytd_py": ytd_py,
            "delta_cm_pm": round(cm - pm, 2),
            "delta_cm_py": round(cm - py_cm, 2),
            "delta_ytd": round(ytd - ytd_py, 2),
            "plan_cm": 0.0,
            "coverage": 0.0,
        })
    return out


def _top_entities_col_labels(year: int, month: int) -> dict[str, str]:
    short = col_labels_month(year, month)
    cm_lbl, pm_lbl, py_lbl = short["cm"], short["pm"], short["py_cm"]
    ytd_lbl, ytd_py_lbl = short["ytd"], short["ytd_py"]
    return {
        "cm": cm_lbl,
        "pm": pm_lbl,
        "py_cm": py_lbl,
        "ytd": ytd_lbl,
        "ytd_py": ytd_py_lbl,
        "delta_cm_pm": f"\u0394 {cm_lbl} \u2212 {pm_lbl}",
        "delta_cm_py": f"\u0394 {cm_lbl} \u2212 {py_lbl}",
        "delta_ytd": f"\u0394 {ytd_lbl} \u2212 {ytd_py_lbl}",
        "plan_cm": f"Plan {cm_lbl}",
        "coverage": "Coverage",
    }


# ---------------------------------------------------------------------------
# DB entrypoint
# ---------------------------------------------------------------------------

def build_top_entities(
    session: Session,
    *,
    year: int,
    month: int,
    type: str = "customer",
    rank_by: str = "cm",
    period_grain: str = "month",
    limit: int = 5000,
    entity: Optional[str] = None,
) -> dict[str, Any]:
    """SalesTopEntitiesResponse — top customers (revenue) or suppliers (spend).

    Month-grain windows over posting_date; values in kEUR.  ``period_grain`` is
    echoed back; the windows are always month-based (the FE always supplies
    year+month, and the slim port computes monthly windows).
    """
    is_customer = type != "supplier"
    if rank_by not in ("cm", "ytd"):
        rank_by = "cm"

    if is_customer:
        fact, dim = "fact_sales", "dim_customer"
        value_col, id_col = "gross_sales", "customer_id"
    else:
        fact, dim = "fact_com", "dim_supplier"
        value_col, id_col = "cost_of_materials", "supplier_id"

    ep = resolve_entity_prefix(session, entity)
    ent_frag = ""
    if ep is not None:
        safe = str(ep).replace("'", "")[:2]
        ent_frag = f"AND LEFT(f.account_number_group, 2) = '{safe}'"

    cm_f, cm_t = _month_bounds(year, month)
    pm_y, pm_m = _pm(year, month)
    pm_f, pm_t = _month_bounds(pm_y, pm_m)
    py_f, py_t = _month_bounds(year - 1, month)
    ytd_f, ytd_t = _ytd_bounds(year, month)
    ytd_py_f, ytd_py_t = _ytd_bounds(year - 1, month)

    def _win(d0: str, d1: str) -> str:
        return (
            f"SUM(CASE WHEN f.posting_date BETWEEN '{d0}' AND '{d1}'"
            f" THEN f.{value_col} ELSE 0 END) / 1000.0"
        )

    name_expr = "TRIM(COALESCE(d.name_line_1, '') || ' ' || COALESCE(d.name_line_2, ''))"

    if is_customer:
        # Legacy top-customer scope: trade revenue only (excludes WIP/interest/other income).
        account_filter = "AND TRIM(a.level_3) = 'Net sales'"
    else:
        account_filter = "AND TRIM(a.level_3) = 'Cost of materials'"

    sql = f"""
        SELECT
            f.{id_col} AS partner_id,
            MAX(NULLIF({name_expr}, '')) AS name,
            {_win(cm_f, cm_t)}         AS cm,
            {_win(pm_f, pm_t)}         AS pm,
            {_win(py_f, py_t)}         AS py_cm,
            {_win(ytd_f, ytd_t)}       AS ytd,
            {_win(ytd_py_f, ytd_py_t)} AS ytd_py
        FROM {fact} f
        JOIN dim_gl_account a
          ON a.account_number_group = f.account_number_group
         AND a.fiscal_year          = f.fiscal_year
        LEFT JOIN {dim} d ON d.{id_col} = f.{id_col}
        WHERE f.posting_date BETWEEN '{ytd_py_f}' AND '{ytd_t}'
          {account_filter}
          {ent_frag}
        GROUP BY f.{id_col}
    """
    raw = [dict(r._mapping) for r in session.execute(text(sql)).fetchall()]

    agg_rows: list[dict[str, Any]] = []
    for r in raw:
        pid = r.get("partner_id")
        name = r.get("name") or (str(pid) if pid is not None else "(no partner)")
        agg_rows.append({
            "name": name,
            id_col: pid,
            "cm": float(r.get("cm") or 0.0),
            "pm": float(r.get("pm") or 0.0),
            "py_cm": float(r.get("py_cm") or 0.0),
            "ytd": float(r.get("ytd") or 0.0),
            "ytd_py": float(r.get("ytd_py") or 0.0),
        })

    rows = rank_top_entities(agg_rows, id_field=id_col, rank_by=rank_by, limit=limit)

    return {
        "rows": rows,
        "col_labels": _top_entities_col_labels(year, month),
        "period_grain": "week" if period_grain == "week" else "month",
        "rank_by": rank_by,
        "plan_mix": "py_proxy",
    }
