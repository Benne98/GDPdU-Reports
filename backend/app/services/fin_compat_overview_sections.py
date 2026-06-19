"""Cross-statement executive-summary rows for the Financials Overview tab.

Mirrors the legacy ``financials_overview/build.py`` layout: Earnings · Balance
sheet · Cash flow (each with KPI sub-blocks), sourced from the GDPdU compat
statement builders rather than the old journal-line schema.
"""
from __future__ import annotations

import calendar
from datetime import date
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.fin_compat_bs import build_bs_snapshot_annual, build_bs_statement_compat
from app.services.fin_compat_cf import build_cf_annual_compat, build_cf_statement_compat
from app.services.fin_compat_pl import _load_plan_map, build_pl_annual_compat, build_pl_statement_compat
from app.services.fin_compat_sql import (
    entity_sql_fragment,
    plan_anchor_for_week,
    pm as _pm,
    resolve_entity_prefix,
)
from app.services.fin_compat_wc import build_wc_snapshot_annual, build_wc_statement_compat

_AM_KEYS = ["py_cm", "pm", "cm", "ytd", "ytd_py"]


def _walk_rows(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in rows:
        out.append(r)
        out.extend(_walk_rows(r.get("children") or []))
        out.extend(_walk_rows(r.get("accounts") or []))
    return out


def _find_by_line_code(rows: list[dict], code: str) -> Optional[dict]:
    for r in _walk_rows(rows):
        if r.get("line_code") == code:
            return r
    return None


def _find_by_label(rows: list[dict], needle: str, *, exact: bool = False) -> Optional[dict]:
    n = needle.lower()
    for r in _walk_rows(rows):
        lab = (r.get("label") or "").lower()
        if (exact and lab == n) or (not exact and n in lab):
            return r
    return None


def _row_amounts(row: Optional[dict], *, period_grain: str = "month") -> dict[str, float]:
    if not row:
        return {k: 0.0 for k in _AM_KEYS}
    am = row.get("amounts") or {}
    if period_grain == "year":
        # Flow annual (P&L, CF): exit-readiness keys ytd / ltm / ytd_py
        if "ytd" in am or "ltm" in am:
            return {
                "py_cm": float(am.get("ytd_py") or 0.0),
                "pm": float(am.get("ltm") or 0.0),
                "cm": float(am.get("ytd") or 0.0),
                "ytd": float(am.get("ytd") or 0.0),
                "ytd_py": float(am.get("ytd_py") or 0.0),
            }
        # Stock snapshot annual (BS, WC): fy_py / fy / cm_py / cm balances
        if any(k in am for k in ("cm", "cm_py", "fy", "fy_py")):
            cm_val = float(am.get("cm") or 0.0)
            cm_py_val = float(am.get("cm_py") or 0.0)
            return {
                "py_cm": cm_py_val,
                "pm": cm_py_val,
                "cm": cm_val,
                "ytd": cm_val,
                "ytd_py": cm_py_val,
            }
    return {k: float(am.get(k) or 0.0) for k in _AM_KEYS}


def _deltas(am: dict[str, float], *, invert: bool = False) -> dict[str, float]:
    d = {
        "mom": round(am["cm"] - am["pm"], 2),
        "yoy": round(am["cm"] - am["py_cm"], 2),
        "ytd": round(am["ytd"] - am["ytd_py"], 2),
    }
    if invert:
        d = {k: -v for k, v in d.items()}
    return d


def _overview_row(
    row_id: str,
    label: str,
    amounts: dict[str, float],
    *,
    row_kind: str = "line",
    unit: str = "keur",
    invert_delta: bool = False,
    plan_cm: Optional[float] = None,
) -> dict[str, Any]:
    am = {k: round(float(amounts.get(k) or 0.0), 2) for k in _AM_KEYS}
    out_am = dict(am)
    if plan_cm is not None:
        out_am["plan_cm"] = round(plan_cm, 2)
        out_am["plan_vs_actual"] = round(am["cm"] - plan_cm, 2)
    return {
        "id": row_id,
        "label": label,
        "row_kind": row_kind,
        "unit": unit,
        "amounts": out_am,
        "deltas": _deltas(am, invert=invert_delta),
        "invert_delta": invert_delta,
    }


def _section_header(section_id: str, title: str) -> dict[str, Any]:
    return {
        "id": section_id,
        "label": title,
        "row_kind": "section_header",
        "unit": "none",
        "amounts": {},
        "deltas": {},
    }


def _kpi_header(section_id: str) -> dict[str, Any]:
    return {
        "id": f"{section_id}-kpi-hdr",
        "label": "KPIs",
        "row_kind": "kpi_header",
        "unit": "none",
        "amounts": {},
        "deltas": {},
    }


def _kpi_pct_row(
    row_id: str,
    label: str,
    num: dict[str, float],
    den: dict[str, float],
    *,
    plan_cm: Optional[float] = None,
) -> dict[str, Any]:
    am: dict[str, float] = {}
    for k in _AM_KEYS:
        d = den.get(k, 0.0)
        am[k] = round(num.get(k, 0.0) / abs(d) * 100, 2) if abs(d) > 1e-6 else 0.0
    return _overview_row(row_id, label, am, row_kind="kpi", unit="pct", plan_cm=plan_cm)


def _ratio_row(
    row_id: str,
    label: str,
    num: dict[str, float],
    den: dict[str, float],
    *,
    as_pct: bool = True,
) -> dict[str, Any]:
    am: dict[str, float] = {}
    for k in _AM_KEYS:
        d = den.get(k, 0.0)
        if abs(d) < 1e-6:
            am[k] = 0.0
        elif as_pct:
            am[k] = round(num.get(k, 0.0) / abs(d) * 100, 2)
        else:
            am[k] = round(num.get(k, 0.0) / abs(d), 2)
    return _overview_row(row_id, label, am, row_kind="kpi", unit="pct" if as_pct else "ratio")


def _plan_cm(plan_map: dict[str, dict[str, float]], code: str) -> Optional[float]:
    p = plan_map.get(code)
    if not p:
        return None
    v = p.get("plan_cm")
    return round(float(v), 2) if v is not None else None


def _kpi_plan_pct(
    plan_map: dict[str, dict[str, float]],
    num_code: str,
    den_code: str,
) -> Optional[float]:
    num = float((plan_map.get(num_code) or {}).get("plan_cm") or 0.0)
    den = float((plan_map.get(den_code) or {}).get("plan_cm") or 0.0)
    if abs(den) < 1e-6:
        return None
    return round(num / abs(den) * 100, 2)


def _net_financial_debt(
    session: Session,
    year: int,
    month: int,
    entity: Optional[str],
) -> dict[str, float]:
    """Bank & similar liabilities minus cash (month-end balance snapshots)."""
    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)
    py = year - 1
    pm_y, pm_m = _pm(year, month)

    def _as_of(y: int, m: int) -> date:
        return date(y, m, calendar.monthrange(y, m)[1])

    dates = {
        "cm": _as_of(year, month),
        "pm": _as_of(pm_y, pm_m),
        "py_cm": _as_of(py, month),
        "ytd": _as_of(year, month),
        "ytd_py": _as_of(py, month),
    }
    out: dict[str, float] = {}
    for key, dt in dates.items():
        sql = f"""
            SELECT
              ABS(COALESCE(SUM(CASE
                    WHEN a.level_3 IN ('Liabilities due to banks', 'Liabilities due to affiliates')
                     AND a.level_2 = 'Liabilities' THEN l.amount ELSE 0 END), 0)) AS debt,
              ABS(COALESCE(SUM(CASE
                    WHEN a.level_3 = 'Cash & cash equivalents' THEN l.amount ELSE 0 END), 0)) AS cash
            FROM fact_gl_line l
            JOIN fact_gl_entry e
              ON e.journal_entry_group_number = l.journal_entry_group_number
             AND e.fiscal_year = l.fiscal_year
            JOIN dim_gl_account a
              ON a.account_number_group = l.account_number_group
             AND a.fiscal_year = l.fiscal_year
            WHERE a.level_0 = 'BS'
              AND e.posting_date <= :cutoff
              {ent_frag}
        """
        row = session.execute(text(sql), {"cutoff": dt.isoformat()}).fetchone()
        mapping = dict(row._mapping) if row is not None else {}
        debt = float(mapping.get("debt") or 0.0)
        cash = float(mapping.get("cash") or 0.0)
        out[key] = round(debt - cash, 2)
    return out


def _statement_kwargs(
    period_grain: str,
    year: Optional[int],
    month: Optional[int],
    iso_year: Optional[int],
    iso_week: Optional[int],
    entity: Optional[str],
) -> dict[str, Any]:
    if period_grain == "week":
        return {
            "period_grain": "week",
            "iso_year": iso_year,
            "iso_week": iso_week,
            "entity": entity,
        }
    if period_grain == "year":
        return {
            "period_grain": "year",
            "year": year,
            "month": month,
            "entity": entity,
        }
    return {"year": year, "month": month, "entity": entity}


def build_overview_sections(
    session: Session,
    *,
    period_grain: str = "month",
    year: Optional[int] = None,
    month: Optional[int] = None,
    iso_year: Optional[int] = None,
    iso_week: Optional[int] = None,
    entity: Optional[str] = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return earnings / position / finance row blocks for the overview table."""
    sk = _statement_kwargs(period_grain, year, month, iso_year, iso_week, entity)

    try:
        if period_grain == "year":
            pl = build_pl_annual_compat(session, year=year, month=month, entity=entity)
        else:
            pl = build_pl_statement_compat(session, **sk)
    except Exception:
        pl = {"rows": []}
    try:
        if period_grain == "year":
            bs = build_bs_snapshot_annual(session, year=year, month=month, entity=entity)
        else:
            bs = build_bs_statement_compat(session, **sk)
    except Exception:
        bs = {"rows": []}
    try:
        if period_grain == "year":
            wc = build_wc_snapshot_annual(session, year=year, month=month, entity=entity)
        else:
            wc = build_wc_statement_compat(session, **sk)
    except Exception:
        wc = {"rows": []}
    try:
        if period_grain == "year":
            cf = build_cf_annual_compat(session, year=year, month=month, entity=entity)
        else:
            cf = build_cf_statement_compat(session, **sk)
    except Exception:
        cf = {"rows": []}

    pl_rows = pl.get("rows") or []
    bs_rows = bs.get("rows") or []
    wc_rows = wc.get("rows") or []
    cf_rows = cf.get("rows") or []

    if period_grain == "week" and iso_year is not None and iso_week is not None:
        anchor_y, anchor_m = plan_anchor_for_week(iso_year, iso_week)
    else:
        anchor_y, anchor_m = year or 0, month or 0

    ep = resolve_entity_prefix(session, entity)
    plan_map = _load_plan_map(session, anchor_y, anchor_m, entity_sql_fragment(ep))

    to = _row_amounts(_find_by_line_code(pl_rows, "TOTAL_OUTPUT"), period_grain=period_grain)
    gp = _row_amounts(_find_by_line_code(pl_rows, "GROSS_PROFIT"), period_grain=period_grain)
    ebitda = _row_amounts(
        _find_by_line_code(pl_rows, "EBITDA")
        or _find_by_line_code(pl_rows, "EBITDA_ROW"),
        period_grain=period_grain,
    )
    netp = _row_amounts(_find_by_line_code(pl_rows, "NET_PROFIT"), period_grain=period_grain)

    earnings: list[dict[str, Any]] = [
        _section_header("earnings", "Earnings"),
        _overview_row("total_output", "Total output", to, plan_cm=_plan_cm(plan_map, "TOTAL_OUTPUT")),
        _overview_row("gross_profit", "Gross profit", gp, plan_cm=_plan_cm(plan_map, "GROSS_PROFIT")),
        _overview_row("ebitda", "EBITDA", ebitda, plan_cm=_plan_cm(plan_map, "EBITDA")),
        _overview_row("net_profit", "Net profit", netp, plan_cm=_plan_cm(plan_map, "NET_PROFIT")),
        _kpi_header("earnings"),
        _kpi_pct_row(
            "gross_margin", "Gross margin", gp, to,
            plan_cm=_kpi_plan_pct(plan_map, "GROSS_PROFIT", "TOTAL_OUTPUT"),
        ),
        _kpi_pct_row(
            "ebitda_margin", "EBITDA margin", ebitda, to,
            plan_cm=_kpi_plan_pct(plan_map, "EBITDA", "TOTAL_OUTPUT"),
        ),
        _kpi_pct_row(
            "net_profit_margin", "Net profit margin", netp, to,
            plan_cm=_kpi_plan_pct(plan_map, "NET_PROFIT", "TOTAL_OUTPUT"),
        ),
    ]

    fa = _row_amounts(_find_by_label(bs_rows, "fixed assets", exact=True), period_grain=period_grain)
    ca = _row_amounts(_find_by_label(bs_rows, "current assets", exact=True), period_grain=period_grain)
    twc = _row_amounts(_find_by_label(wc_rows, "trade working capital", exact=True), period_grain=period_grain)
    nwc = _row_amounts(_find_by_line_code(wc_rows, "NWC"), period_grain=period_grain)
    equity = _row_amounts(_find_by_label(bs_rows, "equity", exact=True), period_grain=period_grain)
    provisions = _row_amounts(
        _find_by_label(bs_rows, "provisions & accruals")
        or _find_by_label(bs_rows, "provisions"),
        period_grain=period_grain,
    )
    liabilities = _row_amounts(_find_by_label(bs_rows, "liabilities", exact=True), period_grain=period_grain)
    assets_am = _row_amounts(_find_by_label(bs_rows, "assets", exact=True), period_grain=period_grain)
    equity_ratio = _find_by_line_code(bs_rows, "EQUITY_RATIO")

    try:
        nfd = _net_financial_debt(session, anchor_y, anchor_m, entity) if anchor_y and anchor_m else {
            k: 0.0 for k in _AM_KEYS
        }
    except Exception:
        nfd = {k: 0.0 for k in _AM_KEYS}

    position: list[dict[str, Any]] = [
        _section_header("position", "Balance sheet"),
        _overview_row("fixed_assets", "Fixed assets", fa),
        _overview_row("current_assets", "Current assets", ca),
        _overview_row("trade_wc", "Trade working capital", twc),
        _overview_row("nwc", "Net working capital", nwc),
        _overview_row("net_financial_debt", "Net financial debt", nfd),
        _overview_row("equity", "Equity", equity),
        _overview_row("provisions", "Provisions", provisions),
        _overview_row("liabilities", "Liabilities", liabilities),
        _kpi_header("position"),
        _ratio_row("twc_to_output", "Trade working capital / Total output", twc, to),
        _ratio_row("net_debt_to_ebitda", "Net debt / EBITDA", nfd, ebitda),
    ]
    if equity_ratio:
        position.append(
            _overview_row(
                "equity_ratio", "Equity ratio",
                _row_amounts(equity_ratio, period_grain=period_grain), row_kind="kpi", unit="pct",
            ),
        )
    else:
        position.append(_kpi_pct_row("equity_ratio", "Equity ratio", equity, assets_am))

    ocf = _row_amounts(
        _find_by_line_code(cf_rows, "CF_OP")
        or _find_by_line_code(cf_rows, "CF_CFO")
        or _find_by_label(cf_rows, "operating cash flow", exact=True),
        period_grain=period_grain,
    )
    fcf = _row_amounts(
        _find_by_line_code(cf_rows, "CF_FREE_CASH_FLOW")
        or _find_by_line_code(cf_rows, "CF_FCF")
        or _find_by_label(cf_rows, "free cash flow", exact=True),
        period_grain=period_grain,
    )
    total_cf = _row_amounts(
        _find_by_label(cf_rows, "net change in cash", exact=False)
        or _find_by_label(cf_rows, "net cash flow", exact=False)
        or _find_by_label(cf_rows, "cash flow", exact=True),
        period_grain=period_grain,
    )

    finance: list[dict[str, Any]] = [
        _section_header("finance", "Cash flow"),
        _overview_row("operating_cf", "Operating cash flow", ocf),
        _overview_row("free_cash_flow", "Free cash flow", fcf),
        _overview_row("cash_flow", "Cash flow", total_cf),
        _kpi_header("finance"),
        _ratio_row("ocf_to_ebitda", "OCF / EBITDA", ocf, ebitda),
        _ratio_row("excess_cash_margin", "Access cash margin", ocf, to),
        _ratio_row("fcf_to_ebitda", "FCF / EBITDA", fcf, ebitda),
    ]

    return {"earnings": earnings, "position": position, "finance": finance}
