"""Overview KPI metrics for the legacy-compat layer (GDPdU backend).

Serves two cockpit/overview metrics consumed by the verbatim-ported frontend via
``GET /api/v1/metrics?metric=...`` (wired in routers/meta_compat.py):

  * ``metric=dupont``      → DuPontData         (api.dupont)
  * ``metric=ebit_table``  → EbitTableData      (api.ebitTable / api.ebitTablePeriod)

Ported from the legacy ``routers/metrics.py`` (`_dupont_data`, `_ebit_table`,
`_ebit_table_week`) onto the GDPdU schema, reusing the established join / entity /
period helpers in ``fin_compat_sql``.

============================================================ SIGN CONVENTION
P&L:  presented = ``l.amount * -1``  (revenue credit → +, cost debit → −).  This is
      the ONLY inversion and matches fin_compat_sql / fin_compat_pl exactly.
BS :  RAW stored signed balance (``SUM(l.amount)`` cumulative to a cutoff), then
      ``ABS()`` for the DuPont magnitudes (assets +, equity/credit also reported as
      a positive magnitude) — identical to the legacy ``_dupont_data`` which wraps
      every balance-sheet aggregate in ``ABS()``.  Never the P&L ``* -1`` here.

============================================================ SCHEMA MAPPING
  fact_gl_journal_line  → fact_gl_line l            (join key journal_entry_group_number + fiscal_year)
  fact_gl_journal_entry → fact_gl_entry e
  dim_gl_account a      → join on account_number_group + fiscal_year
  a.statement_type      → a.level_0 ('PL' | 'BS')
  legal_entity filter   → l.entity_prefix (resolved from legal_entity_code)
  P&L period            → fiscal_year / fiscal_period (BS uses posting_date cutoff)
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.fin_compat_sql import (
    _MONTH_ABBR,
    col_labels_month,
    col_labels_week,
    entity_sql_fragment,
    entities_sql_fragment,
    iso_week_bounds,
    last_day,
    pm,
    prior_iso_week,
    resolve_entity_prefix,
    resolve_entity_prefixes,
    same_week_prior_year,
    short_label,
)

# ---------------------------------------------------------------------------
# DuPont — level filters (same labels as legacy _dupont_data)
# ---------------------------------------------------------------------------
# P&L buckets (presented via amount * -1).
_DUPONT_PL_FILTERS: dict[str, str] = {
    "net_sales":          "a.level_3 = 'Net sales'",
    "cost_of_materials":  "a.level_3 = 'Cost of materials'",
    "personnel_expenses": "a.level_3 = 'Personnel expenses'",
    "depreciation":       "a.level_3 = 'Depreciation'",
    "ebit": (
        "a.level_0 = 'PL'"
        " AND a.level_2 IN ('Income','Expense')"
        " AND a.level_3 NOT IN ('Financial result','Income taxes')"
    ),
    "ebitda": (
        "a.level_0 = 'PL'"
        " AND a.level_2 IN ('Income','Expense')"
        " AND a.level_3 NOT IN ('Depreciation','Financial result','Income taxes')"
    ),
}

# Balance-sheet buckets (cumulative raw balance, reported as ABS magnitude).
_DUPONT_BS_FILTERS: dict[str, str] = {
    "total_assets":      "a.level_1 = 'Assets'",
    "fixed_assets":      "a.level_2 = 'Fixed assets'",
    "current_assets":    "a.level_2 = 'Current assets'",
    "trade_receivables": "a.level_3 = 'Trade receivables'",
    "trade_payables":    "a.level_3 = 'Trade payables'",
    "inventories":       "a.level_3 = 'Inventories'",
    "cash":              "a.level_3 = 'Cash & cash equivalents'",
    "equity":            "a.level_2 = 'Equity'",
}

# Order of metric keys emitted in the DuPontData.metrics map (ratios first, then
# the absolute P&L / BS values, then the day metrics) — mirrors the legacy.
_DUPONT_METRIC_KEYS: list[str] = [
    "roe", "roi", "ros", "equity_multiplier", "asset_turnover",
    "gross_margin", "ebitda_margin",
    "net_sales", "cost_of_materials", "gross_profit", "ebit", "ebitda",
    "personnel_expenses",
    "total_assets", "fixed_assets", "current_assets", "trade_receivables",
    "trade_payables", "inventories", "cash", "equity",
    "dso", "dpo", "dio",
]


# ---------------------------------------------------------------------------
# DuPont — pure KPI computation (DB-free, testable)
# ---------------------------------------------------------------------------

def _safe_div(num: float, den: float, *, pct: bool = False, dec: int = 1) -> Optional[float]:
    """Divide guarding against ~0 denominators (returns None like the legacy)."""
    if abs(den) < 0.001:
        return None
    r = (num / den) * (100 if pct else 1)
    return round(r, dec)


def dupont_period_kpis(pl: dict[str, float], bs: dict[str, float], ann_month: int) -> dict[str, Any]:
    """Compute one DuPont period (cur | py | pm) from presented P&L + ABS BS values.

    ``pl``  : presented P&L YTD amounts (income +, expense −) — keys of
              _DUPONT_PL_FILTERS.
    ``bs``  : ABS cumulative balance magnitudes — keys of _DUPONT_BS_FILTERS.
    ``ann_month`` : number of YTD months (1..12) used to annualise flow figures.

    === FORMULAS (presented amounts; ratios in %) ===
      gross_profit      = net_sales + cost_of_materials   (cost is negative presented)
      ann_ns            = net_sales        * 12 / ann_month
      ann_com           = |cost_of_materials| * 12 / ann_month
      ROE               = EBIT / Equity               * 100
      ROI               = EBIT / Total assets         * 100
      ROS               = EBIT / Net sales            * 100
      equity_multiplier = Total assets / Equity
      asset_turnover    = ann_ns / Total assets
      gross_margin      = gross_profit / Net sales    * 100
      ebitda_margin     = EBITDA / Net sales          * 100
      DSO               = Trade receivables / (ann_ns  / 365)
      DPO               = Trade payables    / (ann_com / 365)
      DIO               = Inventories       / (ann_com / 365)

    === WORKED EXAMPLE (ann_month=12) ===
      net_sales=1000, cost_of_materials=-600 → gross_profit=400, gross_margin=40.0%
      ebit=150, equity=500, total_assets=1250 →
        ROE=150/500*100=30.0%, ROI=150/1250*100=12.0%, ROS=150/1000*100=15.0%,
        equity_multiplier=1250/500=2.5, asset_turnover=1000/1250=0.8
      trade_receivables=200 → DSO=200/(1000/365)=73.0
      trade_payables=150, ann_com=600 → DPO=150/(600/365)=91.3
      inventories=120 → DIO=120/(600/365)=73.0

    === EDGE CASES ===
      * Any zero denominator → that KPI is None (no div-by-zero).
      * ann_month never 0 (callers pass the YTD month 1..12).
      * Negative equity (credit balance not ABS'd upstream) is impossible here:
        BS values arrive ABS'd, so ratios stay well-defined.
    """
    ns = pl.get("net_sales", 0.0)
    com = pl.get("cost_of_materials", 0.0)
    gp = ns + com
    ebit = pl.get("ebit", 0.0)
    ebitda = pl.get("ebitda", 0.0)
    pers = pl.get("personnel_expenses", 0.0)

    ta = bs.get("total_assets", 0.0)
    fa = bs.get("fixed_assets", 0.0)
    ca = bs.get("current_assets", 0.0)
    tr = bs.get("trade_receivables", 0.0)
    tp = bs.get("trade_payables", 0.0)
    inv = bs.get("inventories", 0.0)
    cash = bs.get("cash", 0.0)
    eq = bs.get("equity", 0.0)

    ann_ns = ns * 12 / ann_month if ann_month else 0.0
    ann_com = abs(com) * 12 / ann_month if ann_month else 0.0

    return {
        "roe":                _safe_div(ebit, eq, pct=True),
        "roi":                _safe_div(ebit, ta, pct=True),
        "ros":                _safe_div(ebit, ns, pct=True),
        "equity_multiplier":  _safe_div(ta, eq, dec=2),
        "asset_turnover":     _safe_div(ann_ns, ta, dec=2),
        "gross_margin":       _safe_div(gp, ns, pct=True),
        "ebitda_margin":      _safe_div(ebitda, ns, pct=True),
        "net_sales":          round(ns, 2),
        "cost_of_materials":  round(com, 2),
        "gross_profit":       round(gp, 2),
        "ebit":               round(ebit, 2),
        "ebitda":             round(ebitda, 2),
        "personnel_expenses": round(pers, 2),
        "total_assets":       round(ta, 2),
        "fixed_assets":       round(fa, 2),
        "current_assets":     round(ca, 2),
        "trade_receivables":  round(tr, 2),
        "trade_payables":     round(tp, 2),
        "inventories":        round(inv, 2),
        "cash":               round(cash, 2),
        "equity":             round(eq, 2),
        "dso":                _safe_div(tr, ann_ns / 365) if ann_ns > 0.001 else None,
        "dpo":                _safe_div(tp, ann_com / 365) if ann_com > 0.001 else None,
        "dio":                _safe_div(inv, ann_com / 365) if ann_com > 0.001 else None,
    }


def assemble_dupont(cur: dict[str, Any], py: dict[str, Any], pm_: dict[str, Any],
                    col_label: str, py_label: str, pm_label: str) -> dict[str, Any]:
    """Combine the three period KPI dicts into the DuPontData response shape."""
    return {
        "col_label": col_label,
        "py_label": py_label,
        "pm_label": pm_label,
        "metrics": {
            k: {"value": cur.get(k), "py": py.get(k), "pm": pm_.get(k)}
            for k in _DUPONT_METRIC_KEYS
        },
    }


# ---------------------------------------------------------------------------
# DuPont — DB entrypoint
# ---------------------------------------------------------------------------

def build_dupont(session: Session, entity: Optional[str], year: int, month: int,
                 *, allowed_entities: Optional[set[str]] = None) -> dict[str, Any]:
    """DuPontData for the full extended DuPont tree (GDPdU GL).

    P&L values are YTD (fiscal_period 1..selected month); BS values are cumulative
    point-in-time balances at each period's month-end.  cur = selected period,
    py = same month previous year, pm = prior month.

    ``entity`` may be a single legal_entity_code, comma-separated codes, or None/all.

    ``allowed_entities`` (fail-closed tenant isolation, see
    ``app.services.entity_visibility.visible_entity_codes``): a set of 2-char
    ``entity_prefix`` values, or ``None`` for admin/unrestricted.  When provided it
    is the ONLY entity filter (``entity`` is ignored — the caller has already
    intersected it into the set); an EMPTY set fails closed (matches nothing).
    ``None`` preserves the exact legacy ``entity``-driven behaviour.
    """
    if allowed_entities is not None:
        ent_frag = (
            entities_sql_fragment(sorted(allowed_entities))
            if allowed_entities else "AND 1 = 0"
        )
    else:
        ep = resolve_entity_prefix(session, entity)
        prefixes = resolve_entity_prefixes(session, entity)
        ent_frag = entities_sql_fragment(prefixes) if prefixes else entity_sql_fragment(ep)

    py = year - 1
    pm_y, pm_m = pm(year, month)

    col_label = short_label(year, month)
    py_label = short_label(py, month)
    pm_label = short_label(pm_y, pm_m)

    # ── P&L: YTD presented amounts for cur / py / pm ─────────────────────────
    pl_select: list[str] = []
    for key, filt in _DUPONT_PL_FILTERS.items():
        pl_select.append(
            f"COALESCE(SUM(CASE WHEN ({filt})"
            f"  AND e.fiscal_year = {year} AND e.fiscal_period <= {month}"
            f"  THEN l.amount * -1 ELSE 0 END), 0) AS {key}_cur"
        )
        pl_select.append(
            f"COALESCE(SUM(CASE WHEN ({filt})"
            f"  AND e.fiscal_year = {py} AND e.fiscal_period <= {month}"
            f"  THEN l.amount * -1 ELSE 0 END), 0) AS {key}_py"
        )
        pl_select.append(
            f"COALESCE(SUM(CASE WHEN ({filt})"
            f"  AND e.fiscal_year = {pm_y} AND e.fiscal_period <= {pm_m}"
            f"  THEN l.amount * -1 ELSE 0 END), 0) AS {key}_pm"
        )

    pl_sql = f"""
        SELECT {', '.join(pl_select)}
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE a.level_0 = 'PL'
          AND e.fiscal_year IN ({year}, {py}, {pm_y})
          AND e.fiscal_period BETWEEN 1 AND 12
          {ent_frag}
    """
    pl_row = session.execute(text(pl_sql)).fetchone()
    pl_map = dict(pl_row._mapping) if (pl_row is not None and hasattr(pl_row, "_mapping")) else {}

    # ── BS: ABS cumulative balances at the three month-end cutoffs ───────────
    d_cur = last_day(year, month).isoformat()
    d_py = last_day(py, month).isoformat()
    d_pm = last_day(pm_y, pm_m).isoformat()

    bs_select: list[str] = []
    for key, filt in _DUPONT_BS_FILTERS.items():
        bs_select.append(
            f"ABS(COALESCE(SUM(CASE WHEN ({filt}) AND e.posting_date <= '{d_cur}'"
            f"  THEN l.amount ELSE 0 END), 0)) AS {key}_cur"
        )
        bs_select.append(
            f"ABS(COALESCE(SUM(CASE WHEN ({filt}) AND e.posting_date <= '{d_py}'"
            f"  THEN l.amount ELSE 0 END), 0)) AS {key}_py"
        )
        bs_select.append(
            f"ABS(COALESCE(SUM(CASE WHEN ({filt}) AND e.posting_date <= '{d_pm}'"
            f"  THEN l.amount ELSE 0 END), 0)) AS {key}_pm"
        )

    bs_sql = f"""
        SELECT {', '.join(bs_select)}
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE a.level_0 = 'BS'
          AND e.posting_date <= '{d_cur}'
          {ent_frag}
    """
    bs_row = session.execute(text(bs_sql)).fetchone()
    bs_map = dict(bs_row._mapping) if (bs_row is not None and hasattr(bs_row, "_mapping")) else {}

    def _pl_period(suffix: str) -> dict[str, float]:
        return {k: float(pl_map.get(f"{k}_{suffix}") or 0.0) for k in _DUPONT_PL_FILTERS}

    def _bs_period(suffix: str) -> dict[str, float]:
        return {k: float(bs_map.get(f"{k}_{suffix}") or 0.0) for k in _DUPONT_BS_FILTERS}

    cur = dupont_period_kpis(_pl_period("cur"), _bs_period("cur"), month)
    py_kpi = dupont_period_kpis(_pl_period("py"), _bs_period("py"), month)
    pm_kpi = dupont_period_kpis(_pl_period("pm"), _bs_period("pm"), pm_m)

    return assemble_dupont(cur, py_kpi, pm_kpi, col_label, py_label, pm_label)


# ---------------------------------------------------------------------------
# EBIT table — pure aggregation (DB-free, testable)
# ---------------------------------------------------------------------------

_EBIT_FIELDS = [
    "to_cm_py", "to_pm", "to_cm", "to_ytd",
    "ebit_cm_py", "ebit_pm", "ebit_cm", "ebit_ytd",
]


def build_ebit_rows(
    grain_rows: list[dict[str, Any]],
    ent_name_map: dict[str, tuple[str, str]],
    *,
    consolidated_grain: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Turn per-entity_prefix grain rows into EbitTableRow[] + optional IC elim + Total.

    When ``consolidated_grain`` is supplied (group-level reported totals, no entity
    split) and there is more than one legal entity, an ``__ic_elim__`` row is appended
    so that Σ(entity rows) + IC eliminations = ``__total__`` (reported consolidation).

    === WORKED EXAMPLE ===
      AT to_cm=300, DE to_cm=200 → aggregated 500.
      Reported to_cm=480 → IC eliminations to_cm = −20.
    """
    rows_out: list[dict[str, Any]] = []
    for g in grain_rows:
        ep = (g.get("entity_prefix") or "").strip()
        code, name = ent_name_map.get(ep, (ep, ep))
        row = {
            "entity_code": code or ep,
            "entity_name": name or code or ep,
        }
        for f in _EBIT_FIELDS:
            row[f] = round(float(g.get(f) or 0.0), 2)
        rows_out.append(row)

    rows_out.sort(key=lambda r: str(r["entity_code"]))

    aggregated = {f: round(sum(float(r[f]) for r in rows_out), 2) for f in _EBIT_FIELDS}
    multi_entity = len(rows_out) > 1 and consolidated_grain is not None

    if multi_entity:
        reported = {
            f: round(float(consolidated_grain.get(f) or 0.0), 2)  # type: ignore[union-attr]
            for f in _EBIT_FIELDS
        }
        ic_row: dict[str, Any] = {
            "entity_code": "__ic_elim__",
            "entity_name": "IC eliminations",
        }
        for f in _EBIT_FIELDS:
            ic_row[f] = round(reported[f] - aggregated[f], 2)
        rows_out.append(ic_row)
        total: dict[str, Any] = {"entity_code": "__total__", "entity_name": "Total"}
        for f in _EBIT_FIELDS:
            total[f] = reported[f]
        rows_out.append(total)
    else:
        total = {"entity_code": "__total__", "entity_name": "Total"}
        for f in _EBIT_FIELDS:
            total[f] = aggregated[f]
        rows_out.append(total)
    return rows_out


def _ebit_entity_name_map(session: Session) -> dict[str, tuple[str, str]]:
    rows = session.execute(text(
        "SELECT entity_prefix, legal_entity_code, entity_name FROM dim_legal_entity"
    )).fetchall()
    out: dict[str, tuple[str, str]] = {}
    for r in rows:
        ep = (r[0] or "").strip()
        out[ep] = (r[1] or ep, r[2] or r[1] or ep)
    return out


def _ebit_filters() -> tuple[str, str]:
    """(total_output_filter, ebit_filter) — applied inside a level_0='PL' scan."""
    to_f = "a.level_3 = 'Net sales'"
    ebit_f = (
        "a.level_2 IN ('Income','Expense')"
        " AND a.level_3 NOT IN ('Financial result','Income taxes')"
    )
    return to_f, ebit_f


def _ebit_month_aggregate_cases(
    *,
    year: int,
    month: int,
    to_f: str,
    ebit_f: str,
) -> str:
    py = year - 1
    pm_y, pm_m = pm(year, month)

    def _case(extra: str, fy: int, period_expr: str) -> str:
        return (
            f"COALESCE(SUM(CASE WHEN ({extra})"
            f"  AND e.fiscal_year = {fy} AND {period_expr}"
            f"  THEN l.amount * -1 ELSE 0 END), 0)"
        )

    return f"""
            {_case(to_f,   py,    f'e.fiscal_period = {month}')}   AS to_cm_py,
            {_case(to_f,   pm_y,  f'e.fiscal_period = {pm_m}')}    AS to_pm,
            {_case(to_f,   year,  f'e.fiscal_period = {month}')}   AS to_cm,
            {_case(to_f,   year,  f'e.fiscal_period <= {month}')}  AS to_ytd,
            {_case(ebit_f, py,    f'e.fiscal_period = {month}')}   AS ebit_cm_py,
            {_case(ebit_f, pm_y,  f'e.fiscal_period = {pm_m}')}    AS ebit_pm,
            {_case(ebit_f, year,  f'e.fiscal_period = {month}')}   AS ebit_cm,
            {_case(ebit_f, year,  f'e.fiscal_period <= {month}')}  AS ebit_ytd"""


_EBIT_FROM = """
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year"""


def build_ebit_table(session: Session, entity: Optional[str], year: int, month: int,
                     *, allowed_entities: Optional[set[str]] = None) -> dict[str, Any]:
    """EbitTableData (month grain) — Total output + EBIT per legal entity.

    Columns: CM PY (same month prior year) | PM (prior month) | CM | YTD.

    ``allowed_entities`` (fail-closed tenant isolation): set of 2-char
    ``entity_prefix`` values, or ``None`` for admin/unrestricted.  When provided it
    is the ONLY entity filter (``entity`` ignored — the caller has already
    intersected it); an EMPTY set fails closed (matches nothing).  ``None``
    preserves the exact legacy ``entity``-driven behaviour.
    """
    if allowed_entities is not None:
        ent_frag = (
            entities_sql_fragment(sorted(allowed_entities))
            if allowed_entities else "AND 1 = 0"
        )
    else:
        ep = resolve_entity_prefix(session, entity)
        ent_frag = entity_sql_fragment(ep)
    to_f, ebit_f = _ebit_filters()

    py = year - 1
    pm_y, pm_m = pm(year, month)
    cases = _ebit_month_aggregate_cases(year=year, month=month, to_f=to_f, ebit_f=ebit_f)
    where = f"""
        WHERE a.level_0 = 'PL'
          AND e.fiscal_year IN ({year}, {py}, {pm_y})
          AND e.fiscal_period BETWEEN 1 AND 12
          {ent_frag}"""

    entity_sql = f"""
        SELECT
            l.entity_prefix,{cases}
        {_EBIT_FROM}
        {where}
        GROUP BY l.entity_prefix
        ORDER BY l.entity_prefix
    """
    consol_sql = f"""
        SELECT{cases}
        {_EBIT_FROM}
        {where}
    """
    grains = [dict(r._mapping) for r in session.execute(text(entity_sql)).fetchall()]
    consol_row = session.execute(text(consol_sql)).fetchone()
    consol_grain = dict(consol_row._mapping) if consol_row is not None else None
    rows = build_ebit_rows(
        grains, _ebit_entity_name_map(session), consolidated_grain=consol_grain,
    )

    lbl = col_labels_month(year, month)
    col_labels = {
        "cm_py": lbl["py_cm"],
        "pm": lbl["pm"],
        "cm": lbl["cm"],
        "ytd": lbl["ytd"],
    }
    return {
        "year": year,
        "month": month,
        "period_grain": "month",
        "col_labels": col_labels,
        "rows": rows,
    }


def build_ebit_table_week(session: Session, entity: Optional[str],
                          iso_year: int, iso_week: int) -> dict[str, Any]:
    """EbitTableData (week grain) — Total output + EBIT per legal entity by ISO week.

    Columns: CW PY (same week prior year) | PW (prior week) | CW | YTD (Jan 1 →
    anchor Sunday).  Period windows are posting_date ranges (a week is a
    posting_date span, not a fiscal_period).  An anchor (year, month) — the
    calendar month of the anchor Sunday — is echoed so the EbitTableData type
    (which requires year/month) is satisfied.
    """
    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)
    to_f, ebit_f = _ebit_filters()

    cw_f, cw_t = iso_week_bounds(iso_year, iso_week)
    pw_y, pw_w = prior_iso_week(iso_year, iso_week)
    pw_f, pw_t = iso_week_bounds(pw_y, pw_w)
    py_y, py_w = same_week_prior_year(iso_year, iso_week)
    py_f, py_t = iso_week_bounds(py_y, py_w)
    from datetime import date as _date
    ytd_f, ytd_t = _date(iso_year, 1, 1), cw_t

    def _case(extra: str, d0, d1) -> str:
        return (
            f"COALESCE(SUM(CASE WHEN ({extra})"
            f"  AND e.posting_date BETWEEN '{d0.isoformat()}' AND '{d1.isoformat()}'"
            f"  THEN l.amount * -1 ELSE 0 END), 0)"
        )

    cases = f"""
            {_case(to_f,   py_f, py_t)}   AS to_cm_py,
            {_case(to_f,   pw_f, pw_t)}   AS to_pm,
            {_case(to_f,   cw_f, cw_t)}   AS to_cm,
            {_case(to_f,   ytd_f, ytd_t)} AS to_ytd,
            {_case(ebit_f, py_f, py_t)}   AS ebit_cm_py,
            {_case(ebit_f, pw_f, pw_t)}   AS ebit_pm,
            {_case(ebit_f, cw_f, cw_t)}   AS ebit_cm,
            {_case(ebit_f, ytd_f, ytd_t)} AS ebit_ytd"""
    where = f"""
        WHERE a.level_0 = 'PL'
          AND e.posting_date BETWEEN '{py_f.isoformat()}' AND '{cw_t.isoformat()}'
          {ent_frag}"""

    entity_sql = f"""
        SELECT
            l.entity_prefix,{cases}
        {_EBIT_FROM}
        {where}
        GROUP BY l.entity_prefix
        ORDER BY l.entity_prefix
    """
    consol_sql = f"""
        SELECT{cases}
        {_EBIT_FROM}
        {where}
    """
    grains = [dict(r._mapping) for r in session.execute(text(entity_sql)).fetchall()]
    consol_row = session.execute(text(consol_sql)).fetchone()
    consol_grain = dict(consol_row._mapping) if consol_row is not None else None
    rows = build_ebit_rows(
        grains, _ebit_entity_name_map(session), consolidated_grain=consol_grain,
    )

    lbl = col_labels_week(iso_year, iso_week)
    col_labels = {
        "cm_py": lbl["py_cm"],
        "pm": lbl["pm"],
        "cm": lbl["cm"],
        "ytd": lbl["ytd"],
    }
    return {
        "year": cw_t.year,
        "month": cw_t.month,
        "iso_year": iso_year,
        "iso_week": iso_week,
        "period_grain": "week",
        "col_labels": col_labels,
        "rows": rows,
    }
