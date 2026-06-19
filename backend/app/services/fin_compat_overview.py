"""Overview page builders for the financial compat layer.

Implements the four Overview endpoints consumed by the /overview frontend page:
  1. GET /api/v1/financials/overview          → FinancialsOverviewResponse
  2. GET /api/v1/financials/overview/highlights → { highlights: OverviewHighlight[] }
  3. GET /api/v1/financials/overview/entity-breakdown → FinancialsEntityBreakdownResponse
  4. GET /api/v1/financials/overview/entity-breakdown/narratives → { areas: … }

Data source: GDPdU GL via the same grain SQL used by fin_compat_pl.py.
Sign convention: amounts are PRESENTED (revenue +, expense −) — the `* −1`
inversion lives in fin_compat_sql and is never applied again here.

=== FORMULA (FinancialsOverviewSection rows) ===
Each OverviewMetricRow carries the SAME amount keys as the P&L statement
(py_cm, pm, cm, ytd, ytd_py), taken directly from the running-sum values
produced by _compute_running_values() in fin_compat_pl.py.  Deltas:
  mom = cm − pm
  yoy = cm − py_cm
  ytd = ytd − ytd_py
KPI rows (margin %) = key_line / |TOTAL_OUTPUT| * 100 per column.

=== WORKED EXAMPLE (section 'Profitability', cm column) ===
TOTAL_OUTPUT cm=500, GROSS_PROFIT cm=200, EBITDA cm=80, NET_PROFIT cm=40:
  Gross margin cm  = 200 / |500| * 100 = 40.0 %
  EBITDA margin cm = 80  / |500| * 100 = 16.0 %

=== EDGE CASES ===
  * Missing key line (not in dim_pl_structure) → row skipped silently.
  * |TOTAL_OUTPUT| ~0 → margin KPI = 0 (no division).
  * No entities in dim_legal_entity → entity_snapshots = [], areas empty.
  * Week grain: col_labels from col_labels_week; plan_anchor_for_week for yr/mo.
"""
from __future__ import annotations

import calendar
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.fin_compat_pl import (
    _AM_KEYS,
    _compute_running_values,
    _er_row_amounts,
    _is_pl_structure_row,
    _kpi_pct,
    _load_structure,
    _match_grain,
    _row_amounts,
    _row_dict,
)
from app.services.fin_compat_overview_sections import build_overview_sections
from app.services.fin_compat_sql import (
    col_labels_annual,
    col_labels_month,
    col_labels_week,
    entity_sql_fragment,
    label_plan_fy,
    label_plan_period,
    period_label,
    pl_consl_grain_sql_month,
    pl_consl_grain_sql_week,
    pl_grain_sql_annual,
    pl_grain_sql_month,
    pl_grain_sql_week,
    plan_anchor_for_week,
    resolve_entity_prefix,
)

# ---------------------------------------------------------------------------
# Key P&L lines extracted for the Overview page
# ---------------------------------------------------------------------------
# Lines that appear in the "Group summary" sections.  Each tuple is
# (line_code_in_dim_pl_structure, display_label, unit).
# 'keur' lines show absolute amounts; 'pct' lines are margin %.
_SECTION_REVENUE: list[tuple[str, str, str]] = [
    ("NET_SALES",    "Net Sales",    "keur"),
    ("TOTAL_OUTPUT", "Total Output", "keur"),
]
_SECTION_PROFITABILITY: list[tuple[str, str, str]] = [
    ("GROSS_PROFIT", "Gross Profit", "keur"),
    ("EBITDA",       "EBITDA",       "keur"),
    ("EBIT",         "EBIT",         "keur"),
    ("NET_PROFIT",   "Net Income",   "keur"),
]
_MARGIN_LINES: list[tuple[str, str, str]] = [
    ("GROSS_PROFIT", "Gross Margin",  "pct"),
    ("EBITDA",       "EBITDA Margin", "pct"),
    ("NET_PROFIT",   "Net Margin",    "pct"),
]

# Lines that feed the entity-breakdown table (cm_by_entity per line).
_BREAKDOWN_LINES: list[tuple[str, str]] = [
    ("NET_SALES",    "Net Sales"),
    ("GROSS_PROFIT", "Gross Profit"),
    ("EBITDA",       "EBITDA"),
    ("EBIT",         "EBIT"),
    ("NET_PROFIT",   "Net Income"),
]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _zero_am() -> dict[str, float]:
    return {k: 0.0 for k in _AM_KEYS}


def _annual_to_overview_am(annual_am: dict[str, float]) -> dict[str, float]:
    """Map exit-readiness annual keys to overview executive-summary keys."""
    return {
        "py_cm": float(annual_am.get("ytd_py") or 0.0),
        "pm": float(annual_am.get("ltm") or 0.0),
        "cm": float(annual_am.get("ytd") or 0.0),
        "ytd": float(annual_am.get("ytd") or 0.0),
        "ytd_py": float(annual_am.get("ytd_py") or 0.0),
    }


def _annual_overview_labels(year: int, month: int) -> dict[str, str]:
    ann = col_labels_annual(year, month)
    return {
        "py_cm": ann["ytd_py"],
        "pm": ann["ltm"],
        "cm": ann["ytd"],
        "ytd": ann["ytd"],
        "ytd_py": ann["ytd_py"],
    }


def _deltas_overview(am: dict[str, float]) -> dict[str, float]:
    return {
        "mom": round(am["cm"] - am["pm"], 2),
        "yoy": round(am["cm"] - am["py_cm"], 2),
        "ytd": round(am["ytd"] - am["ytd_py"], 2),
    }


def _metric_row(
    row_id: str,
    label: str,
    row_kind: str,
    unit: str,
    am: dict[str, float],
    *,
    invert_delta: bool = False,
) -> dict[str, Any]:
    d = _deltas_overview(am)
    if invert_delta:
        d = {k: -v for k, v in d.items()}
    return {
        "id": row_id,
        "label": label,
        "row_kind": row_kind,
        "unit": unit,
        "amounts": {k: round(v, 2) for k, v in am.items()},
        "deltas": d,
        "invert_delta": invert_delta,
    }


def _margin_metric_row(
    row_id: str,
    label: str,
    numerator_am: dict[str, float],
    total_output_am: dict[str, float],
) -> dict[str, Any]:
    """Percentage-of-TOTAL_OUTPUT row."""
    pct_am: dict[str, float] = {}
    for k in _AM_KEYS:
        p = _kpi_pct(numerator_am, total_output_am, k)
        pct_am[k] = round(p, 2) if p is not None else 0.0
    return {
        "id": row_id,
        "label": label,
        "row_kind": "kpi",
        "unit": "pct",
        "amounts": pct_am,
        "deltas": _deltas_overview(pct_am),
        "invert_delta": False,
    }


def _fmt_keur(value: float) -> str:
    """Format a kEUR value for a human-readable sentence."""
    sign = "-" if value < 0 else ""
    return f"{sign}{abs(value):,.0f} kEUR"


def _pct_str(value: float) -> str:
    return f"{value:+.1f}%"


# ---------------------------------------------------------------------------
# Core data loader (shared by all four endpoints)
# ---------------------------------------------------------------------------

def _load_pl_data(
    session: Session,
    *,
    period_grain: str,
    year: Optional[int],
    month: Optional[int],
    iso_year: Optional[int],
    iso_week: Optional[int],
    entity: Optional[str],
) -> tuple[dict[str, dict[str, float]], dict[str, str], int, int]:
    """Load consolidated (or entity-filtered) P&L running-sum values.

    Returns:
        line_vals  : {line_code: {am_key: float}}  (running-sum per column)
        col_labels : FinancialStatementColLabels dict
        yr         : anchor calendar year
        mo         : anchor calendar month
    """
    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)

    if period_grain == "week":
        assert iso_year is not None and iso_week is not None
        sql, params = pl_grain_sql_week(iso_year, iso_week, ent_frag)
        yr, mo = plan_anchor_for_week(iso_year, iso_week)
        labels = col_labels_week(iso_year, iso_week)
    elif period_grain == "year":
        assert year is not None and month is not None
        sql, params = pl_grain_sql_annual(year, month, ent_frag)
        yr, mo = year, month
        labels = _annual_overview_labels(year, month)
    else:
        assert year is not None and month is not None
        sql, params = pl_grain_sql_month(year, month, ent_frag)
        yr, mo = year, month
        labels = col_labels_month(year, month)

    grains = [dict(r._mapping) for r in session.execute(text(sql), params).fetchall()]
    struct = _load_structure(session)
    struct_pl = [r for r in (_row_dict(r) for r in struct) if _is_pl_structure_row(r)]

    mapping_vals: dict[str, dict[str, float]] = {}
    for r in struct_pl:
        if r.get("row_type") != "mapping":
            continue
        code = r["line_code"]
        acc = _zero_am()
        for g in grains:
            if _match_grain(g, r):
                if period_grain == "year":
                    am = _annual_to_overview_am(_er_row_amounts(g))
                else:
                    am = _row_amounts(g)
                for k in _AM_KEYS:
                    acc[k] += am.get(k, 0.0)
        mapping_vals[code] = acc

    running = _compute_running_values(struct_pl, mapping_vals, _AM_KEYS)
    line_vals: dict[str, dict[str, float]] = {
        code: {k: float(v.get(k) or 0.0) for k in _AM_KEYS}
        for code, v in running.items()
    }
    return line_vals, labels, yr, mo


def _load_entity_data(
    session: Session,
    *,
    period_grain: str,
    year: Optional[int],
    month: Optional[int],
    iso_year: Optional[int],
    iso_week: Optional[int],
) -> tuple[
    list[dict[str, Any]],             # entity_dicts: [{code, name, label}]
    dict[str, dict[str, float]],      # line_entity: {line_code: {entity_code: cm_value}}
]:
    """Per-entity consolidation grain for the anchor period.

    Returns entities list and per-entity cm amounts keyed by line_code.
    """
    if period_grain == "week":
        assert iso_year is not None and iso_week is not None
        sql, params = pl_consl_grain_sql_week(iso_year, iso_week)
    else:
        assert year is not None and month is not None
        sql, params = pl_consl_grain_sql_month(year, month)

    grains = [dict(r._mapping) for r in session.execute(text(sql), params).fetchall()]

    ent_rows = session.execute(text(
        "SELECT legal_entity_code, entity_prefix, entity_name "
        "FROM dim_legal_entity ORDER BY legal_entity_code"
    )).fetchall()
    entity_codes = [r[0] for r in ent_rows]
    ep_to_code = {r[1]: r[0] for r in ent_rows}
    entity_dicts = [
        {"code": r[0], "name": r[2], "label": r[2]}
        for r in ent_rows
    ]

    struct = _load_structure(session)
    struct_pl = [r for r in (_row_dict(r) for r in struct) if _is_pl_structure_row(r)]

    def _consl_cm(matched: list[dict]) -> dict[str, float]:
        sums: dict[str, float] = {ec: 0.0 for ec in entity_codes}
        for g in matched:
            ep = g.get("entity_prefix") or ""
            lec = ep_to_code.get(ep, ep)
            if lec in sums:
                sums[lec] = sums.get(lec, 0.0) + float(g.get("cm") or 0)
        return sums

    mapping_entity: dict[str, dict[str, float]] = {}
    for r in struct_pl:
        if r.get("row_type") != "mapping":
            continue
        code = r["line_code"]
        matched = [g for g in grains if _match_grain(g, r)]
        mapping_entity[code] = _consl_cm(matched)

    running_entity = _compute_running_values(struct_pl, mapping_entity, entity_codes)
    line_entity: dict[str, dict[str, float]] = {
        code: {ec: float(v.get(ec) or 0.0) for ec in entity_codes}
        for code, v in running_entity.items()
    }
    return entity_dicts, line_entity


# ---------------------------------------------------------------------------
# Sections builder (FinancialsOverviewSection[])
# ---------------------------------------------------------------------------

def _build_sections(
    session: Session,
    *,
    period_grain: str,
    year: Optional[int],
    month: Optional[int],
    iso_year: Optional[int],
    iso_week: Optional[int],
    entity: Optional[str],
) -> list[dict[str, Any]]:
    """Earnings · Balance sheet · Cash flow (legacy overview layout)."""
    blocks = build_overview_sections(
        session,
        period_grain=period_grain,
        year=year,
        month=month,
        iso_year=iso_year,
        iso_week=iso_week,
        entity=entity,
    )
    return [
        {"id": "earnings", "title": "Earnings", "rows": blocks["earnings"]},
        {"id": "position", "title": "Balance sheet", "rows": blocks["position"]},
        {"id": "finance", "title": "Cash flow", "rows": blocks["finance"]},
    ]


def _fmt_intro_keur(v: float) -> str:
    k = round(float(v) / 1000, 1)
    sign = "+" if k > 0 else ""
    return f"{sign}{k:,.1f}k EUR" if k != 0 else "0"


def _build_intro_from_sections(
    sections: list[dict[str, Any]],
    labels: dict[str, str],
    entity_snapshots: list[dict[str, Any]],
    *,
    period_grain: str = "month",
) -> str:
    """Cross-statement intro for the Group overview header (legacy style)."""
    cm = labels.get("cm") or labels.get("ytd") or "the current period"
    is_annual = period_grain == "year"
    parts: list[str] = []

    def _rows(section_id: str) -> list[dict]:
        sec = next((s for s in sections if s.get("id") == section_id), None)
        return (sec or {}).get("rows") or []

    def _line(rows: list[dict], rid: str) -> Optional[dict]:
        return next((r for r in rows if r.get("id") == rid), None)

    earnings = _rows("earnings")
    position = _rows("position")
    finance = _rows("finance")

    np_row = _line(earnings, "net_profit")
    ebitda_row = _line(earnings, "ebitda")
    if np_row and ebitda_row:
        eb_cm = float((ebitda_row.get("amounts") or {}).get("cm") or 0)
        deltas = np_row.get("deltas") or {}
        if is_annual:
            np_delta = float(deltas.get("yoy") or 0)
            dir_np = "improved" if np_delta >= 0 else "declined"
            parts.append(
                f"For {cm}, earnings {dir_np} year-on-year "
                f"(net profit {_fmt_intro_keur(np_delta)}), with EBITDA at {_fmt_intro_keur(eb_cm)}."
            )
        else:
            np_mom = float(deltas.get("mom") or 0)
            dir_np = "improved" if np_mom >= 0 else "declined"
            parts.append(
                f"At {cm}, earnings {dir_np} month-on-month "
                f"(net profit {_fmt_intro_keur(np_mom)}), with EBITDA at {_fmt_intro_keur(eb_cm)}."
            )

    nwc_row = _line(position, "nwc")
    eq_row = _line(position, "equity")
    if nwc_row and eq_row:
        nwc_cm = float((nwc_row.get("amounts") or {}).get("cm") or 0)
        eq_cm = float((eq_row.get("amounts") or {}).get("cm") or 0)
        parts.append(
            f"The balance sheet shows net working capital of {_fmt_intro_keur(nwc_cm)} "
            f"and equity of {_fmt_intro_keur(eq_cm)} at period end."
        )

    ocf_row = _line(finance, "operating_cf")
    fcf_row = _line(finance, "free_cash_flow")
    if ocf_row and fcf_row:
        ocf_cm = float((ocf_row.get("amounts") or {}).get("cm") or 0)
        fcf_cm = float((fcf_row.get("amounts") or {}).get("cm") or 0)
        parts.append(
            f"Cash generation: operating cash flow {_fmt_intro_keur(ocf_cm)}; "
            f"free cash flow {_fmt_intro_keur(fcf_cm)}"
            + (" in the period." if is_annual else " in the month.")
        )

    if entity_snapshots:
        top = sorted(entity_snapshots, key=lambda s: float(s.get("net_profit_cm") or 0), reverse=True)
        if top:
            lead = top[0]
            parts.append(
                f"{lead.get('name') or lead.get('code')} leads on net profit "
                f"({_fmt_intro_keur(float(lead.get('net_profit_cm') or 0))} in {cm})."
            )

    if not parts:
        return (
            f"Executive summary for {cm} — review earnings, balance sheet, "
            f"and cash flow below."
        )
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Highlights builder (OverviewHighlight[])
# ---------------------------------------------------------------------------

def _build_highlights(
    session: Session,
    *,
    period_grain: str,
    year: Optional[int],
    month: Optional[int],
    iso_year: Optional[int],
    iso_week: Optional[int],
    entity: Optional[str],
    labels: dict[str, str],
    yr: int,
    mo: int,
) -> list[dict[str, Any]]:
    """Key drivers from pre-built narrative snapshots (no on-request rebuild in prod)."""
    from app.services.fin_compat_overview_narrative_snapshots import load_narrative_snapshot

    cm_label = labels.get("cm", period_label(yr, mo))
    tabs: list[tuple[str, str, str]] = [
        ("pl", "Income Statement", "pl"),
        ("bs", "Balance sheet", "bs"),
        ("cf", "Cash flow", "cf"),
        ("wc", "Working capital", "wc"),
    ]

    out: list[dict[str, Any]] = []
    for tab_id, tab_label, stmt in tabs:
        nar = load_narrative_snapshot(
            session,
            yr,
            mo,
            entity,
            stmt,
            period_grain=period_grain,
            iso_year=iso_year,
            iso_week=iso_week,
            max_bullets=2,
        )
        if not nar:
            continue
        bullets = list(nar.get("bullets") or [])[:2]
        intro = (nar.get("intro") or "").strip()
        if not bullets and not intro:
            continue
        out.append({
            "tab": tab_id,
            "tab_label": tab_label,
            "intro": intro,
            "bullets": [
                {"index": int(b.get("index") or i + 1), "text": b.get("text") or ""}
                for i, b in enumerate(bullets)
            ],
        })

    if out:
        return out

    # Minimal fallback when snapshots are not warmed yet.
    return [{
        "tab": "pl",
        "tab_label": "Income Statement",
        "intro": f"Executive summary for {cm_label}.",
        "bullets": [{"index": 1, "text": "Open the Income Statement for detailed key drivers."}],
    }]


# ---------------------------------------------------------------------------
# Entity snapshots builder (OverviewEntitySnapshot[])
# ---------------------------------------------------------------------------

def _build_entity_snapshots(
    entity_dicts: list[dict[str, Any]],
    line_entity: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    """One snapshot per legal entity: net_profit_cm, net_profit_mom, ebitda_cm."""
    snapshots = []
    np_by_entity = line_entity.get("NET_PROFIT", {})
    ebd_by_entity = line_entity.get("EBITDA", line_entity.get("EBIT", {}))

    for ent in entity_dicts:
        code = ent["code"]
        np_cm = float(np_by_entity.get(code) or 0)
        ebd_cm = float(ebd_by_entity.get(code) or 0)
        snapshots.append({
            "code": code,
            "name": ent.get("name") or ent.get("label") or code,
            "net_profit_cm": round(np_cm, 2),
            "net_profit_mom": 0.0,   # monthly MoM not in consl grain; 0 fallback
            "ebitda_cm": round(ebd_cm, 2),
        })
    return snapshots


# ---------------------------------------------------------------------------
# Entity breakdown rows (EntityBreakdownRow[])
# ---------------------------------------------------------------------------

def _build_breakdown_rows(
    entity_dicts: list[dict[str, Any]],
    line_entity: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    """Key P&L lines with cm_by_entity dict."""
    entity_codes = [e["code"] for e in entity_dicts]
    rows: list[dict[str, Any]] = []
    for code, label in _BREAKDOWN_LINES:
        by_ent = line_entity.get(code)
        if by_ent is None:
            continue
        cm_by_entity = {
            ec: (round(float(by_ent.get(ec) or 0), 2) if ec in by_ent else None)
            for ec in entity_codes
        }
        rows.append({
            "id": f"bd-{code.lower()}",
            "label": label,
            "row_kind": "line",
            "unit": "keur",
            "cm_by_entity": cm_by_entity,
        })
    return rows


# ---------------------------------------------------------------------------
# Narrative areas builder (EntityBreakdownArea[])
# ---------------------------------------------------------------------------

def _entity_pl_bullet(
    name: str,
    rev: float,
    gp: float,
    np_: float,
    group_rev: float,
    cm_label: str,
) -> str:
    """Per-entity P&L driver sentence with share-of-group and margin context."""
    parts: list[str] = [f"{name} delivered {_fmt_keur(rev)} revenue in {cm_label}"]
    if group_rev > 0.5 and abs(rev) > 0.5:
        share = 100.0 * rev / group_rev
        parts.append(f"({share:.0f}% of group revenue)")
    if abs(rev) > 0.5 and abs(gp) > 0.5:
        margin = 100.0 * gp / rev if abs(rev) > 0.5 else 0.0
        parts.append(f"with gross profit {_fmt_keur(gp)} ({margin:.0f}% margin)")
    if abs(np_) > 0.5:
        parts.append(f"and net income {_fmt_keur(np_)}")
    return " ".join(parts) + "."


def _entity_ebitda_bullet(
    name: str,
    ebd: float,
    group_ebd: float,
    cm_label: str,
    *,
    rank: int,
    n_entities: int,
) -> str:
    """Per-entity EBITDA bullet with relative standing in the group."""
    text = f"{name} reported EBITDA of {_fmt_keur(ebd)} in {cm_label}"
    if n_entities > 1 and abs(group_ebd) > 0.5 and abs(ebd) > 0.5:
        share = 100.0 * ebd / group_ebd
        text += f", contributing {share:.0f}% of group EBITDA"
    if rank == 1 and n_entities > 1:
        text += " — the strongest contributor"
    elif rank == n_entities and n_entities > 1 and ebd < group_ebd / n_entities:
        text += " — the weakest contributor"
    return text + "."


def _build_areas(
    entity_dicts: list[dict[str, Any]],
    line_entity: dict[str, dict[str, float]],
    labels: dict[str, str],
    yr: int,
    mo: int,
) -> list[dict[str, Any]]:
    """One area per statement tab; bullets = per-entity key-driver sentences."""
    cm_label = labels.get("cm", period_label(yr, mo))
    np_ent   = line_entity.get("NET_PROFIT",   {})
    rev_ent  = line_entity.get("NET_SALES") or line_entity.get("TOTAL_OUTPUT") or {}
    gp_ent   = line_entity.get("GROSS_PROFIT", {})
    ebd_ent  = line_entity.get("EBITDA",  line_entity.get("EBIT", {}))

    group_rev = sum(float(rev_ent.get(e["code"]) or 0) for e in entity_dicts)
    group_ebd = sum(float(ebd_ent.get(e["code"]) or 0) for e in entity_dicts)

    pl_ranked = sorted(
        entity_dicts,
        key=lambda e: float(rev_ent.get(e["code"]) or 0),
        reverse=True,
    )
    pl_bullets: list[dict[str, Any]] = []
    for rank, ent in enumerate(pl_ranked, start=1):
        code = ent["code"]
        name = ent.get("name") or ent.get("label") or code
        rev  = float(rev_ent.get(code) or 0)
        gp   = float(gp_ent.get(code) or 0)
        np_  = float(np_ent.get(code) or 0)
        pl_bullets.append({
            "index": rank,
            "entity_code": code,
            "entity_name": name,
            "text": _entity_pl_bullet(name, rev, gp, np_, group_rev, cm_label),
        })

    ebd_ranked = sorted(
        entity_dicts,
        key=lambda e: float(ebd_ent.get(e["code"]) or 0),
        reverse=True,
    )
    ebd_bullets: list[dict[str, Any]] = []
    for rank, ent in enumerate(ebd_ranked, start=1):
        code = ent["code"]
        name = ent.get("name") or ent.get("label") or code
        ebd  = float(ebd_ent.get(code) or 0)
        ebd_bullets.append({
            "index": rank,
            "entity_code": code,
            "entity_name": name,
            "text": _entity_ebitda_bullet(
                name, ebd, group_ebd, cm_label,
                rank=rank, n_entities=len(entity_dicts),
            ),
        })

    pl_intro = (
        f"P&L breakdown by entity for {cm_label}. "
        "Entities are ordered by revenue contribution; values are period-flow."
    )
    if pl_ranked and group_rev > 0.5:
        lead = pl_ranked[0]
        lead_name = lead.get("name") or lead.get("label") or lead["code"]
        lead_rev = float(rev_ent.get(lead["code"]) or 0)
        pl_intro += (
            f" {lead_name} leads the group at {_fmt_keur(lead_rev)} "
            f"({100.0 * lead_rev / group_rev:.0f}% of revenue)."
        )

    ebd_intro = f"EBITDA contribution per entity in {cm_label}, ranked highest to lowest."
    if ebd_ranked and abs(group_ebd) > 0.5:
        top = ebd_ranked[0]
        top_name = top.get("name") or top.get("label") or top["code"]
        ebd_intro += f" {top_name} leads at {_fmt_keur(float(ebd_ent.get(top['code']) or 0))}."

    return [
        {
            "id": "pl",
            "title": "P&L Performance",
            "tab": "pl",
            "intro": pl_intro,
            "bullets": pl_bullets or [
                {"index": 1, "entity_code": "", "entity_name": "",
                 "text": "No entity data available for this period."}
            ],
        },
        {
            "id": "ebitda",
            "title": "EBITDA by Entity",
            "tab": "pl",
            "intro": ebd_intro,
            "bullets": ebd_bullets or [
                {"index": 1, "entity_code": "", "entity_name": "",
                 "text": "No EBITDA data available for this period."}
            ],
        },
        {
            "id": "bs",
            "title": "Balance Sheet",
            "tab": "bs",
            "intro": f"Balance sheet positions as of end of {cm_label}.",
            "bullets": [
                {"index": 1, "entity_code": "", "entity_name": "",
                 "text": "See the Balance Sheet tab for a full entity-level breakdown."},
            ],
        },
        {
            "id": "cf",
            "title": "Cash Flow",
            "tab": "cf",
            "intro": f"Cash flow summary per entity for {cm_label}.",
            "bullets": [
                {"index": 1, "entity_code": "", "entity_name": "",
                 "text": "See the Cash Flow tab for detailed operating/investing/financing flows."},
            ],
        },
    ]


# ---------------------------------------------------------------------------
# Public builders
# ---------------------------------------------------------------------------

def build_overview_response(
    session: Session,
    *,
    period_grain: str = "month",
    year: Optional[int] = None,
    month: Optional[int] = None,
    iso_year: Optional[int] = None,
    iso_week: Optional[int] = None,
    entity: Optional[str] = None,
    include_highlights: bool = True,
) -> dict[str, Any]:
    """FinancialsOverviewResponse — Group summary sections + highlights.

    === RESPONSE SHAPE ===
      { statement:'overview', period_grain, year, month, [iso_year, iso_week],
        col_labels, intro, entity_snapshots, sections, highlights }
    """
    line_vals, labels, yr, mo = _load_pl_data(
        session,
        period_grain=period_grain,
        year=year, month=month,
        iso_year=iso_year, iso_week=iso_week,
        entity=entity,
    )

    sections = _build_sections(
        session,
        period_grain=period_grain,
        year=year, month=month,
        iso_year=iso_year, iso_week=iso_week,
        entity=entity,
    )
    entity_snapshots: list[dict[str, Any]] = []
    highlights: list[dict[str, Any]] = []

    # Entity snapshots — always loaded (small query, needed for the tiles)
    try:
        ent_dicts, line_entity = _load_entity_data(
            session,
            period_grain=period_grain,
            year=year, month=month,
            iso_year=iso_year, iso_week=iso_week,
        )
        entity_snapshots = _build_entity_snapshots(ent_dicts, line_entity)
    except Exception:  # noqa: BLE001
        entity_snapshots = []

    intro_str = _build_intro_from_sections(
        sections, labels, entity_snapshots, period_grain=period_grain,
    )

    earnings_sec = next((s for s in sections if s.get("id") == "earnings"), None)
    if earnings_sec and any(
        (r.get("amounts") or {}).get("plan_cm") is not None
        for r in earnings_sec.get("rows") or []
        if r.get("row_kind") == "line"
    ):
        labels = {**labels, "plan_cm": labels.get("plan_cm") or label_plan_period(labels.get("cm", ""))}

    if include_highlights:
        highlights = _build_highlights(
            session,
            period_grain=period_grain,
            year=year, month=month,
            iso_year=iso_year, iso_week=iso_week,
            entity=entity,
            labels=labels,
            yr=yr, mo=mo,
        )

    out: dict[str, Any] = {
        "statement": "overview",
        "period_grain": period_grain,
        "year": yr,
        "month": mo,
        "col_labels": labels,
        "intro": intro_str,
        "entity_snapshots": entity_snapshots,
        "sections": sections,
        "highlights": highlights,
    }
    if iso_year is not None:
        out["iso_year"] = iso_year
    if iso_week is not None:
        out["iso_week"] = iso_week
    return out


def build_overview_highlights(
    session: Session,
    *,
    period_grain: str = "month",
    year: Optional[int] = None,
    month: Optional[int] = None,
    iso_year: Optional[int] = None,
    iso_week: Optional[int] = None,
    entity: Optional[str] = None,
) -> dict[str, Any]:
    """{ highlights: OverviewHighlight[] } — standalone for lazy loading."""
    line_vals, labels, yr, mo = _load_pl_data(
        session,
        period_grain=period_grain,
        year=year, month=month,
        iso_year=iso_year, iso_week=iso_week,
        entity=entity,
    )
    highlights = _build_highlights(
        session,
        period_grain=period_grain,
        year=year, month=month,
        iso_year=iso_year, iso_week=iso_week,
        entity=entity,
        labels=labels,
        yr=yr, mo=mo,
    )
    return {"highlights": highlights}


def build_entity_breakdown_response(
    session: Session,
    *,
    period_grain: str = "month",
    year: Optional[int] = None,
    month: Optional[int] = None,
    iso_year: Optional[int] = None,
    iso_week: Optional[int] = None,
    entity: Optional[str] = None,
    include_narratives: bool = False,
) -> dict[str, Any]:
    """FinancialsEntityBreakdownResponse — entities, key rows, areas.

    === RESPONSE SHAPE ===
      { cm_label, entities, rows, areas }

    ``rows`` carry cm_by_entity (current-month cm value per entity, kEUR).
    ``areas`` bullets are per-entity key-driver sentences (always included;
    ``include_narratives`` is accepted for API compatibility but has no effect).
    """
    _line_vals, labels, yr, mo = _load_pl_data(
        session,
        period_grain=period_grain,
        year=year, month=month,
        iso_year=iso_year, iso_week=iso_week,
        entity=None,           # always consolidated for breakdown
    )

    ent_dicts, line_entity = _load_entity_data(
        session,
        period_grain=period_grain,
        year=year, month=month,
        iso_year=iso_year, iso_week=iso_week,
    )

    cm_label = labels.get("cm", period_label(yr, mo))
    entities_out = [
        {
            "code":         e["code"],
            "name":         e.get("name") or e.get("label") or e["code"],
            "display_name": e.get("name") or e.get("label") or e["code"],
        }
        for e in ent_dicts
    ]
    rows  = _build_breakdown_rows(ent_dicts, line_entity)
    areas = _build_areas(ent_dicts, line_entity, labels, yr, mo)

    return {
        "cm_label": cm_label,
        "entities": entities_out,
        "rows":     rows,
        "areas":    areas,
    }


def build_entity_breakdown_narratives(
    session: Session,
    *,
    period_grain: str = "month",
    year: Optional[int] = None,
    month: Optional[int] = None,
    iso_year: Optional[int] = None,
    iso_week: Optional[int] = None,
    entity: Optional[str] = None,
) -> dict[str, Any]:
    """{ areas: EntityBreakdownArea[] } — standalone for async narrative loading."""
    _line_vals, labels, yr, mo = _load_pl_data(
        session,
        period_grain=period_grain,
        year=year, month=month,
        iso_year=iso_year, iso_week=iso_week,
        entity=None,
    )
    ent_dicts, line_entity = _load_entity_data(
        session,
        period_grain=period_grain,
        year=year, month=month,
        iso_year=iso_year, iso_week=iso_week,
    )
    areas = _build_areas(ent_dicts, line_entity, labels, yr, mo)
    return {"areas": areas}
