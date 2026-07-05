"""Personnel movement narratives and chart payloads."""

from __future__ import annotations



from datetime import date

from typing import Any, Optional



from sqlalchemy import text

from sqlalchemy.orm import Session



from app.services.personnel_accounting import _col_label, _fetch_rows, _snapshot_label

from app.services.personnel_calc import (
    TREND_METRIC_CATALOG,
    aggregate_metric,
    chart_display_value,
    row_payroll_eur,
    trend_metric_ids,
)

from app.services.personnel_dimensions import DIMENSION_IDS, row_dim_value



_SALARY_PCT_THRESHOLD = 0.05

_SALARY_ABS_THRESHOLD_EUR = 5_000.0





def _prior_snapshot(session: Session, anchor: date, project_id: str = "default") -> date | None:

    row = session.execute(

        text("""

            SELECT MAX(as_of_date) AS d

            FROM fact_personnel_employee

            WHERE project_id = :pid AND as_of_date < :anchor

        """),

        {"pid": project_id, "anchor": anchor},

    ).fetchone()

    return row[0] if row and row[0] else None





def _index_by_pno(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:

    return {str(r["personalnummer"]): r for r in rows if r.get("personalnummer")}





def _fmt_keur(v: float) -> str:

    return f"{abs(v):,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")





def _build_headcount_waterfall(opening: float, hires: int, terms: int, closing: float) -> dict[str, Any]:

    entries: list[dict[str, Any]] = []

    cursor = opening



    entries.append({

        "name": "Opening",

        "base": 0.0,

        "value": opening,

        "raw": opening,

        "is_total": True,

    })



    if hires:

        entries.append({

            "name": "New hires",

            "base": cursor,

            "value": hires,

            "raw": hires,

            "is_total": False,

        })

        cursor += hires



    if terms:

        entries.append({

            "name": "Terminations",

            "base": cursor - terms,

            "value": terms,

            "raw": -terms,

            "is_total": False,

        })

        cursor -= terms



    entries.append({

        "name": "Closing",

        "base": 0.0,

        "value": closing,

        "raw": closing,

        "is_total": True,

    })



    return {

        "opening": opening,

        "hires": hires,

        "terms": -terms,

        "closing": closing,

        "entries": entries,

    }





def _group_payroll_by_dimension(

    anchor_rows: list[dict[str, Any]],

    prior_rows: list[dict[str, Any]],

    dimension: str,

) -> list[dict[str, Any]]:

    by_anchor: dict[str, float] = {}

    by_prior: dict[str, float] = {}

    for r in anchor_rows:

        k = row_dim_value(r, dimension)

        by_anchor[k] = by_anchor.get(k, 0) + abs(aggregate_metric([r], "payroll"))

    for r in prior_rows:

        k = row_dim_value(r, dimension)

        by_prior[k] = by_prior.get(k, 0) + abs(aggregate_metric([r], "payroll"))



    out = []

    for k in sorted(set(by_anchor) | set(by_prior), key=lambda x: -by_anchor.get(x, 0)):

        cur = round(by_anchor.get(k, 0), 1)

        py = round(by_prior.get(k, 0), 1)

        delta_pct = round((cur - py) / abs(py) * 100, 1) if abs(py) > 0.01 else None

        out.append({

            "key": k,

            "label": k,

            "anchor": cur,

            "prior": py,

            "delta_pct": delta_pct,

        })

    return out[:12]





def _metric_trend(

    session: Session,

    anchor: date,

    entity: Optional[str],

    project_id: str,

    metric_id: str = "fte",

) -> list[dict[str, Any]]:

    mid = metric_id if metric_id in trend_metric_ids() else "fte"

    rows = session.execute(

        text("""

            SELECT DISTINCT as_of_date

            FROM fact_personnel_employee

            WHERE project_id = :pid AND as_of_date <= :anchor

            ORDER BY as_of_date ASC

        """),

        {"pid": project_id, "anchor": anchor},

    ).fetchall()

    dates = [r[0] for r in rows][-6:]

    trend: list[dict[str, Any]] = []

    for d in dates:

        snap_rows = _fetch_rows(session, d, entity, project_id)

        raw = aggregate_metric(snap_rows, mid)

        prior_d = date(d.year - 1, d.month, d.day)

        prior_rows = _fetch_rows(session, prior_d, entity, project_id)

        prior_raw = aggregate_metric(prior_rows, mid) if prior_rows else 0.0

        value = chart_display_value(mid, raw)

        prior_value = chart_display_value(mid, prior_raw)

        delta_pct = (

            round((value - prior_value) / abs(prior_value) * 100, 1)

            if abs(prior_value) > 0.01 else None

        )

        trend.append({

            "period": _snapshot_label(d),

            "value": value,

            "prior_value": prior_value,

            "fte": value,

            "prior_fte": prior_value,

            "delta_pct": delta_pct,

        })

    return trend





def build_movements_report(

    session: Session,

    *,

    anchor_date: date,

    prior_date: date | None = None,

    entity: Optional[str] = None,

    chart_dimension: str = "bereich",

    trend_metric: str = "fte",

    project_id: str = "default",

) -> dict[str, Any]:

    prior = prior_date or _prior_snapshot(session, anchor_date, project_id)

    anchor_rows = _fetch_rows(session, anchor_date, entity, project_id)

    prior_rows = _fetch_rows(session, prior, entity, project_id) if prior else []



    cur = _index_by_pno(anchor_rows)

    prev = _index_by_pno(prior_rows)



    hires: list[dict[str, Any]] = []

    terms: list[dict[str, Any]] = []

    raises: list[dict[str, Any]] = []



    for pno, row in cur.items():

        if pno not in prev:

            hires.append(row)



    for pno, row in prev.items():

        if pno not in cur:

            terms.append(row)



    for pno, row in cur.items():

        if pno not in prev:

            continue

        old = prev[pno]

        old_pay = row_payroll_eur(old)

        new_pay = row_payroll_eur(row)

        if old_pay <= 0:

            continue

        delta = new_pay - old_pay

        if delta >= _SALARY_ABS_THRESHOLD_EUR or delta / old_pay >= _SALARY_PCT_THRESHOLD:

            raises.append({

                **row,

                "delta_eur": delta,

                "delta_pct": delta / old_pay * 100,

                "prior_pay_eur": old_pay,

                "current_pay_eur": new_pay,

            })



    raises.sort(key=lambda r: r.get("delta_eur", 0), reverse=True)



    anchor_label = _col_label(anchor_date)

    prior_label = _col_label(prior) if prior else "prior period"

    fte_anchor = aggregate_metric(anchor_rows, "fte")

    fte_prior = aggregate_metric(prior_rows, "fte") if prior_rows else 0

    payroll_anchor = aggregate_metric(anchor_rows, "payroll")

    payroll_prior = aggregate_metric(prior_rows, "payroll") if prior_rows else 0



    intro_parts = [

        f"Payroll snapshot {anchor_label}: {fte_anchor:.0f} FTEs and payroll accounting {payroll_anchor:,.1f} kEUR.",

    ]

    if prior:

        intro_parts.append(

            f"Versus {prior_label}: FTE {fte_prior:.0f} → {fte_anchor:.0f}; "

            f"payroll {payroll_prior:,.1f} → {payroll_anchor:,.1f} kEUR."

        )



    bullets: list[str] = []

    if hires:

        top = hires[0]

        bullets.append(

            f"New hires: {len(hires)} employees joined (e.g. {top.get('bereich', '—')} / PN {top.get('personalnummer')})."

        )

    if terms:

        top = terms[0]

        bullets.append(

            f"Terminations: {len(terms)} employees left (e.g. {top.get('bereich', '—')} / PN {top.get('personalnummer')})."

        )

    if raises:

        r0 = raises[0]

        bullets.append(

            f"Salary increases: {len(raises)} material moves; largest +{_fmt_keur(r0['delta_eur'])} EUR "

            f"({r0['delta_pct']:.1f}%) in {r0.get('bereich', '—')}."

        )

    if not bullets:

        bullets.append("No material headcount or compensation moves versus the prior snapshot.")



    dim = chart_dimension if chart_dimension in DIMENSION_IDS else "bereich"

    payroll_chart = _group_payroll_by_dimension(anchor_rows, prior_rows, dim)

    trend_mid = trend_metric if trend_metric in trend_metric_ids() else "fte"

    metric_trend = _metric_trend(session, anchor_date, entity, project_id, trend_mid)



    return {

        "anchor_date": anchor_date.isoformat(),

        "prior_date": prior.isoformat() if prior else None,

        "intro": " ".join(intro_parts),

        "bullets": bullets,

        "charts": {

            "payroll_by_dimension": payroll_chart,

            "payroll_dimension": dim,

            "fte_trend": metric_trend,

            "trend_metric": trend_mid,

            "trend_metrics": TREND_METRIC_CATALOG,

            "top_raises": [

                {

                    "personalnummer": r.get("personalnummer"),

                    "bereich": r.get("bereich"),

                    "delta_keur": round(r["delta_eur"] / 1000, 1),

                    "delta_pct": round(r["delta_pct"], 1),

                }

                for r in raises[:5]

            ],

        },

        "counts": {"hires": len(hires), "terms": len(terms), "raises": len(raises)},

    }

