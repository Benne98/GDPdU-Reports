"""Mapped trial balance (Summen- und Saldenliste) export for Account statement.

Produces account-level rows matching the legacy ``PL_all`` / ``BS_all`` Excel
layout: hierarchy columns, FY/YTD (or Dec) summary columns, then monthly columns
from Jan (anchor_year − 3) through the anchor month.

Sign convention:
  PL — presented flow (``amount * -1``): revenue +, expense −.
  BS — cumulative closing balance at month-end (raw stored sign, no ``* -1``).
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.fin_compat_bs_sql import _BS_FROM, _BS_FROM_BAL, _bal_case, _bal_case_fy, _sql_alias, _wrap_bs_bal_sql
from app.services.fin_compat_sql import (
    _MONTH_ABBR,
    entity_sql_fragment,
    label_actual,
    last_day,
    resolve_entity_prefix,
)

_PL_DIMS = """
            COALESCE(le.legal_entity_code, l.entity_prefix) AS entity,
            a.level_2,
            a.level_3,
            NULLIF(TRIM(a.level_4), '') AS level_4,
            MAX(a.gl_account_id) AS gl_account_id,
            MAX(a.account_name) AS account_name,
            l.account_number_group,
            l.entity_prefix"""

_BS_DIMS = """
            COALESCE(le.legal_entity_code, l.entity_prefix) AS entity,
            COALESCE(NULLIF(TRIM(a.level_1), ''), '—') AS level_1,
            a.level_2,
            a.level_3,
            NULLIF(TRIM(a.level_4), '') AS level_4,
            MAX(a.gl_account_id) AS gl_account_id,
            MAX(a.account_name) AS account_name,
            l.account_number_group,
            l.entity_prefix"""

_JOIN_ENTITY = """
        LEFT JOIN dim_legal_entity le
          ON le.entity_prefix = l.entity_prefix"""

_GROUP_PL = (
    "GROUP BY le.legal_entity_code, l.entity_prefix, a.level_2, a.level_3, "
    "NULLIF(TRIM(a.level_4), ''), l.account_number_group"
)
_GROUP_BS = (
    "GROUP BY le.legal_entity_code, l.entity_prefix, a.level_1, a.level_2, a.level_3, "
    "NULLIF(TRIM(a.level_4), ''), l.account_number_group"
)


def _export_periods(year: int, month: int) -> list[tuple[int, int]]:
    """Jan (year−3) … anchor month inclusive — matches PL_all / BS_all span."""
    periods: list[tuple[int, int]] = []
    for y in range(year - 3, year + 1):
        end_m = month if y == year else 12
        for m in range(1, end_m + 1):
            periods.append((y, m))
    return periods


def _month_label(y: int, m: int) -> str:
    return f"{_MONTH_ABBR[m - 1]}{str(y)[-2:]}A"


def _pl_summary_meta(year: int) -> list[dict[str, Any]]:
    return [
        {"key": f"FY{year - 3}", "label": f"FY{str(year - 3)[-2:]}A", "kind": "fy", "year": year - 3},
        {"key": f"FY{year - 2}", "label": f"FY{str(year - 2)[-2:]}A", "kind": "fy", "year": year - 2},
        {"key": f"FY{year - 1}", "label": f"FY{str(year - 1)[-2:]}A", "kind": "fy", "year": year - 1},
        {"key": f"YTD{year}", "label": f"YTD{str(year)[-2:]}A", "kind": "ytd", "year": year},
    ]


def _bs_summary_meta(year: int, month: int) -> list[dict[str, Any]]:
    anchor_lbl = label_actual(f"{_MONTH_ABBR[month - 1]}{str(year)[-2:]}")
    return [
        {"key": f"DEC{year - 3}", "label": label_actual(f"Dec{str(year - 3)[-2:]}"), "kind": "dec", "year": year - 3},
        {"key": f"DEC{year - 2}", "label": label_actual(f"Dec{str(year - 2)[-2:]}"), "kind": "dec", "year": year - 2},
        {"key": f"DEC{year - 1}", "label": label_actual(f"Dec{str(year - 1)[-2:]}"), "kind": "dec", "year": year - 1},
        {"key": f"CM{year}-{month:02d}", "label": anchor_lbl, "kind": "anchor", "year": year, "month": month},
    ]


def _pl_columns(year: int, month: int) -> list[dict[str, str]]:
    cols: list[dict[str, str]] = [
        {"key": "entity", "label": "Entity", "group": "dim"},
        {"key": "level_2", "label": "Income / Expense", "group": "dim"},
        {"key": "level_3", "label": "Position", "group": "dim"},
        {"key": "level_4", "label": "Item", "group": "dim"},
        {"key": "account", "label": "Account", "group": "dim"},
        {"key": "_spacer1", "label": "", "group": "spacer"},
    ]
    for s in _pl_summary_meta(year):
        cols.append({"key": s["key"], "label": s["label"], "group": "summary"})
    cols.append({"key": "_spacer2", "label": "", "group": "spacer"})
    for y, m in _export_periods(year, month):
        pk = f"{y:04d}-{m:02d}"
        cols.append({"key": pk, "label": _month_label(y, m), "group": "monthly"})
    return cols


def _bs_columns(year: int, month: int) -> list[dict[str, str]]:
    cols: list[dict[str, str]] = [
        {"key": "entity", "label": "Entity", "group": "dim"},
        {"key": "level_1", "label": "L1", "group": "dim"},
        {"key": "level_2", "label": "Assets / Equity & Liabilities", "group": "dim"},
        {"key": "level_3", "label": "Position", "group": "dim"},
        {"key": "level_4", "label": "Item", "group": "dim"},
        {"key": "account", "label": "Account", "group": "dim"},
        {"key": "_spacer1", "label": "", "group": "spacer"},
    ]
    for s in _bs_summary_meta(year, month):
        cols.append({"key": s["key"], "label": s["label"], "group": "summary"})
    cols.append({"key": "_spacer2", "label": "", "group": "spacer"})
    for y, m in _export_periods(year, month):
        pk = f"{y:04d}-{m:02d}"
        cols.append({"key": pk, "label": _month_label(y, m), "group": "monthly"})
    return cols


def _row_to_dict(mapping: Any, amount_keys: list[str]) -> dict[str, Any]:
    d = dict(mapping._mapping) if hasattr(mapping, "_mapping") else dict(mapping)
    gid = (d.get("gl_account_id") or "").strip()
    name = (d.get("account_name") or "").strip()
    account = f"{gid} | {name}" if gid and name else gid or name or "—"
    out: dict[str, Any] = {
        "entity": d.get("entity") or "—",
        "level_1": d.get("level_1"),
        "level_2": d.get("level_2") or "—",
        "level_3": d.get("level_3") or "—",
        "level_4": d.get("level_4"),
        "account": account,
        "gl_account_id": gid,
        "account_name": name,
        "amounts": {},
    }
    for k in amount_keys:
        raw = d.get(k)
        out["amounts"][k] = round(float(raw or 0), 2)
    return out


def build_pl_trial_balance(
    session: Session,
    year: int,
    month: int,
    entity: Optional[str] = None,
) -> dict[str, Any]:
    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)
    periods = _export_periods(year, month)
    summaries = _pl_summary_meta(year)

    cases: list[str] = []
    for s in summaries:
        if s["kind"] == "fy":
            y = int(s["year"])
            cases.append(
                f"COALESCE(SUM(CASE WHEN e.fiscal_year = {y} "
                f"AND e.fiscal_period BETWEEN 1 AND 12 "
                f"THEN l.amount * -1 ELSE 0 END), 0) AS \"{s['key']}\""
            )
        else:
            cases.append(
                f"COALESCE(SUM(CASE WHEN e.fiscal_year = {year} "
                f"AND e.fiscal_period <= {month} "
                f"THEN l.amount * -1 ELSE 0 END), 0) AS \"{s['key']}\""
            )
    for y, m in periods:
        pk = f"{y:04d}-{m:02d}"
        cases.append(
            f"COALESCE(SUM(CASE WHEN e.fiscal_year = {y} AND e.fiscal_period = {m} "
            f"THEN l.amount * -1 ELSE 0 END), 0) AS \"{pk}\""
        )

    years_set = sorted({y for y, _ in periods} | {int(s["year"]) for s in summaries})
    sql = f"""
        SELECT
            {_PL_DIMS},
            {', '.join(cases)}
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        {_JOIN_ENTITY}
        WHERE a.level_0 = 'PL'
          AND e.fiscal_year = ANY(:years)
          AND e.fiscal_period BETWEEN 1 AND 12
          {ent_frag}
        {_GROUP_PL}
        ORDER BY entity, a.level_2, a.level_3, level_4 NULLS FIRST, gl_account_id
    """
    amount_keys = [s["key"] for s in summaries] + [f"{y:04d}-{m:02d}" for y, m in periods]
    rows_raw = session.execute(text(sql), {"years": years_set}).fetchall()
    rows = [_row_to_dict(r, amount_keys) for r in rows_raw]
    return {
        "statement_type": "PL",
        "year": year,
        "month": month,
        "columns": _pl_columns(year, month),
        "rows": rows,
        "row_count": len(rows),
    }


def build_bs_trial_balance(
    session: Session,
    year: int,
    month: int,
    entity: Optional[str] = None,
) -> dict[str, Any]:
    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)
    periods = _export_periods(year, month)
    summaries = _bs_summary_meta(year, month)

    cases: list[str] = []
    for s in summaries:
        if s["kind"] == "dec":
            fy = int(s["year"])
            cutoff = last_day(fy, 12)
        else:
            fy = int(s["year"])
            cutoff = last_day(year, month)
        cases.append(_bal_case_fy(fy, cutoff, s["key"]))
    for y, m in periods:
        pk = f"{y:04d}-{m:02d}"
        cases.append(_bal_case_fy(y, last_day(y, m), pk))

    d_max = last_day(year, month)
    sql = f"""
        SELECT
            {_BS_DIMS},
            {', '.join(cases)}
        {_BS_FROM}
        {_JOIN_ENTITY}
        WHERE a.level_0 = 'BS'
          AND e.posting_date <= '{d_max.isoformat()}'
          {ent_frag}
        {_GROUP_BS}
        ORDER BY entity, a.level_1, a.level_2, a.level_3, level_4 NULLS FIRST, gl_account_id
    """
    amount_keys = [s["key"] for s in summaries] + [f"{y:04d}-{m:02d}" for y, m in periods]
    rows_raw = session.execute(text(sql)).fetchall()
    rows = [_row_to_dict(r, amount_keys) for r in rows_raw]
    return {
        "statement_type": "BS",
        "year": year,
        "month": month,
        "columns": _bs_columns(year, month),
        "rows": rows,
        "row_count": len(rows),
    }


def build_trial_balance_export(
    session: Session,
    year: int,
    month: int,
    entity: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "anchor": {"year": year, "month": month},
        "entity": entity,
        "pl": build_pl_trial_balance(session, year, month, entity),
        "bs": build_bs_trial_balance(session, year, month, entity),
    }
