"""P&L line detail service for the legacy compat layer.

Ports legacy services/pl_line_detail/pipeline.py to the GDPdU schema.

Key schema adaptations:
  - join on account_number_group + fiscal_year (not gl_account_id as PK)
  - filter entity via entity_prefix (not legal_entity_code)
  - journal_entry_number derived as RIGHT(journal_entry_group_number, -2)
"""
from __future__ import annotations

import os
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.fin_compat_sql import (
    _last_12_periods,
    entity_sql_fragment,
    period_key,
    period_label,
    resolve_entity_prefix,
)


_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _pm(year: int, month: int) -> tuple[int, int]:
    return (year, month - 1) if month > 1 else (year - 1, 12)


def _short_label(y: int, m: int) -> str:
    return f"{_MONTH_ABBR[m - 1]}{str(y)[-2:]}"


def _resolve_mapping_row(session: Session, line_code: str) -> Optional[dict]:
    """Look up dim_pl_structure row for a line_code (supports base::L4 composites)."""
    base = line_code.split("::")[0].strip()
    l4_override = line_code.split("::", 1)[1].strip() if "::" in line_code else None
    row = session.execute(text(
        "SELECT line_code, row_type, balance_title, level_2, level_3, level_4, gl_account_id "
        "FROM dim_pl_structure WHERE line_code = :lc LIMIT 1"
    ), {"lc": base}).fetchone()
    if not row:
        return None
    r = dict(row._mapping)
    if l4_override:
        r["level_4"] = l4_override
        r["line_code"] = line_code
    return r


def _scope_fragment(row: dict, ent_frag: str) -> tuple[str, dict[str, Any]]:
    """Build WHERE fragment for the GL scope of a mapping row."""
    l2 = (row.get("level_2") or "").strip()
    l3 = (row.get("level_3") or "").strip()
    l4 = (row.get("level_4") or "").strip()
    gid = (row.get("gl_account_id") or "").strip()

    parts = ["a.level_0 = 'PL'"]
    params: dict[str, Any] = {}
    if gid:
        parts.append("a.gl_account_id = :gid")
        params["gid"] = gid
    else:
        if l2:
            parts.append("a.level_2 = :l2")
            params["l2"] = l2
        if l3:
            parts.append("a.level_3 = :l3")
            params["l3"] = l3
        if l4:
            parts.append("NULLIF(TRIM(a.level_4), '') = :l4")
            params["l4"] = l4

    if ent_frag:
        # strip "AND " prefix for use inside compound WHERE
        frag = ent_frag.strip()
        if frag.upper().startswith("AND "):
            frag = frag[4:]
        parts.append(frag)

    return " AND ".join(parts), params


def build_pl_line_detail(
    session: Session,
    line_code: str,
    year: int, month: int, entity: Optional[str],
    *,
    limit: int = 50,
    timeline_months: int = 12,
    use_llm: bool = True,
    line_mom_keur: Optional[float] = None,
) -> dict[str, Any]:
    row = _resolve_mapping_row(session, line_code)
    if not row:
        raise ValueError(f"Unknown line_code: {line_code}")

    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)
    scope_sql, params_base = _scope_fragment(row, ent_frag)

    pm_y, pm_m = _pm(year, month)

    # ── Account balances for CM and PM ────────────────────────────────────────
    acc_sql = text(f"""
        SELECT
            l.account_number_group,
            MAX(a.gl_account_id)   AS gl_account_id,
            MAX(a.account_name)    AS account_name,
            COALESCE(SUM(CASE WHEN e.fiscal_year = :yr AND e.fiscal_period = :mo
                         THEN l.amount * -1 ELSE 0 END), 0)::float8 AS balance_cm,
            COALESCE(SUM(CASE WHEN e.fiscal_year = :pm_y AND e.fiscal_period = :pm_m
                         THEN l.amount * -1 ELSE 0 END), 0)::float8 AS balance_pm
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE {scope_sql}
          AND (
            (e.fiscal_year = :yr AND e.fiscal_period = :mo) OR
            (e.fiscal_year = :pm_y AND e.fiscal_period = :pm_m)
          )
        GROUP BY l.account_number_group
        HAVING ABS(SUM(CASE WHEN e.fiscal_year = :yr AND e.fiscal_period = :mo
                        THEN l.amount * -1 ELSE 0 END)) > 0.01
        ORDER BY ABS(SUM(CASE WHEN e.fiscal_year = :yr AND e.fiscal_period = :mo
                         THEN l.amount * -1 ELSE 0 END)) DESC
        LIMIT 30
    """)
    acc_rows = session.execute(acc_sql, {**params_base, "yr": year, "mo": month,
                                          "pm_y": pm_y, "pm_m": pm_m}).fetchall()

    accounts_out = []
    for a in acc_rows:
        cm = float(a[3] or 0)
        pm_v = float(a[4] or 0)
        accounts_out.append({
            "gl_account_id": a[1] or a[0],
            "account_name": a[2] or "",
            "balance_cm": round(cm / 1000, 2),
            "balance_pm": round(pm_v / 1000, 2),
            "delta": round((cm - pm_v) / 1000, 2),
        })

    # ── Top bookings for CM ───────────────────────────────────────────────────
    bk_sql = text(f"""
        SELECT
            l.booking_line_id,
            e.posting_date::text    AS posting_date,
            SUBSTRING(l.journal_entry_group_number FROM 3) AS journal_entry_number,
            le.legal_entity_code,
            l.fiscal_year,
            MAX(a.gl_account_id)   AS gl_account_id,
            MAX(a.account_name)    AS account_name,
            l.amount::float8       AS amount,
            l.line_note,
            e.reference_document_number
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        LEFT JOIN dim_legal_entity le ON le.entity_prefix = l.entity_prefix
        WHERE {scope_sql}
          AND e.fiscal_year = :yr AND e.fiscal_period = :mo
        GROUP BY l.booking_line_id, e.posting_date, l.journal_entry_group_number,
                 le.legal_entity_code, l.fiscal_year, l.amount, l.line_note,
                 e.reference_document_number
        ORDER BY ABS(l.amount) DESC
        LIMIT :lim
    """)
    bk_rows = session.execute(bk_sql, {**params_base, "yr": year, "mo": month,
                                        "lim": limit}).fetchall()

    top_bookings = []
    for b in bk_rows:
        top_bookings.append({
            "booking_line_id": b[0],
            "posting_date": b[1],
            "journal_entry_number": b[2],
            "legal_entity_code": b[3],
            "fiscal_year": b[4],
            "gl_account_id": b[5] or "",
            "account_name": b[6],
            "amount": round(float(b[7] or 0) / 1000, 2),
            "line_note": b[8] or "",
            "reference": b[9] or "",
            "counter_gl_account_id": None,
            "counter_account_name": None,
        })

    # ── Account timeline ──────────────────────────────────────────────────────
    all_periods = _last_12_periods(year, month)[-timeline_months:]
    period_defs = [
        {"year": y, "month": m, "label": _short_label(y, m), "key": period_key(y, m)}
        for y, m in all_periods
    ]
    period_keys_sql = sorted({y for y, _ in all_periods})

    tl_cases = " ".join(
        f"COALESCE(SUM(CASE WHEN e.fiscal_year = {y} AND e.fiscal_period = {m} "
        f"THEN l.amount*-1 ELSE 0 END),0) AS \"{period_key(y, m)}\","
        for y, m in all_periods
    ).rstrip(",")

    tl_sql = text(f"""
        SELECT
            l.account_number_group,
            MAX(a.gl_account_id) AS gl_account_id,
            MAX(a.account_name)  AS account_name,
            {tl_cases}
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE {scope_sql}
          AND e.fiscal_year = ANY(:tl_years)
          AND e.fiscal_period BETWEEN 1 AND 12
        GROUP BY l.account_number_group
        HAVING MAX(ABS(l.amount)) > 0.01
        ORDER BY MAX(ABS(l.amount)) DESC
        LIMIT 10
    """)
    tl_rows = session.execute(tl_sql, {**params_base, "tl_years": period_keys_sql}).fetchall()

    accounts_timeline = []
    for t in tl_rows:
        d = dict(t._mapping) if hasattr(t, "_mapping") else dict(t)
        gid = d.get("gl_account_id") or d.get("account_number_group") or ""
        aname = d.get("account_name") or ""
        series = [
            {"label": pd["label"], "value_keur": round(float(d.get(pd["key"]) or 0) / 1000, 2)}
            for pd in period_defs
        ]
        accounts_timeline.append({
            "gl_account_id": gid,
            "account_name": aname,
            "series": series,
        })

    # ── Sub-lines from dim_pl_structure ──────────────────────────────────────
    sub_lines: list[dict] = []
    l3_val = (row.get("level_3") or "").strip()
    l2_val = (row.get("level_2") or "").strip()
    if l3_val:
        sl_rows = session.execute(text(
            "SELECT DISTINCT NULLIF(TRIM(level_4), '') AS level_4 "
            "FROM dim_gl_account WHERE level_0 = 'PL' AND level_2 = :l2 AND level_3 = :l3 "
            "AND NULLIF(TRIM(level_4),'') IS NOT NULL ORDER BY level_4"
        ), {"l2": l2_val, "l3": l3_val}).fetchall()
        base_code = line_code.split("::")[0].strip()
        for sl in sl_rows:
            l4v = sl[0]
            if l4v:
                sub_lines.append({"parent_line_code": base_code, "level_4": l4v, "label": l4v})

    # ── Commentary (deterministic) ────────────────────────────────────────────
    commentary = _build_commentary(accounts_out, top_bookings)

    return {
        "line_code": line_code,
        "label": row.get("balance_title", line_code),
        "year": year, "month": month,
        "entity": entity,
        "accounts": accounts_out,
        "top_bookings": top_bookings,
        "bridge": [{"label": a["account_name"] or a["gl_account_id"], "value": a["balance_cm"]}
                   for a in accounts_out[:12]],
        "periods": period_defs,
        "accounts_timeline": accounts_timeline,
        "sub_lines": sub_lines,
        "commentary": commentary,
        "outlier_facts": {},
        "suggested_prompts": [],
        "meta": {"llm_used": False, "algorithm_version": "outliers_v2"},
    }


def _build_commentary(accounts: list[dict], bookings: list[dict]) -> dict[str, str]:
    if not accounts:
        return {"accounts": "No GL accounts in scope for this period.", "postings": ""}
    top_3 = sorted(accounts, key=lambda a: abs(a["balance_cm"]), reverse=True)[:3]
    acc_text = "Top accounts by current month: " + "; ".join(
        f"{a['account_name'] or a['gl_account_id']} (€{a['balance_cm']:.0f}k)"
        for a in top_3
    )
    post_text = ""
    if bookings:
        largest = bookings[0]
        post_text = (
            f"Largest booking: {largest['gl_account_id']} "
            f"€{largest['amount']:.0f}k on {largest['posting_date']}."
        )
    return {"accounts": acc_text, "postings": post_text}


def get_journal_entry_by_booking(session: Session, booking_line_id: int) -> Optional[dict]:
    """Fetch all lines of the journal entry containing booking_line_id."""
    anchor = session.execute(text(
        "SELECT journal_entry_group_number, fiscal_year "
        "FROM fact_gl_line WHERE booking_line_id = :id LIMIT 1"
    ), {"id": booking_line_id}).fetchone()
    if not anchor:
        return None

    je_num, fy = anchor[0], anchor[1]
    rows = session.execute(text("""
        SELECT
            l.booking_line_id,
            l.journal_entry_group_number,
            SUBSTRING(l.journal_entry_group_number FROM 3) AS journal_entry_number,
            le.legal_entity_code,
            l.fiscal_year, l.line_number,
            e.fiscal_period, e.posting_date::text, e.document_date::text,
            l.account_number_group,
            MAX(a.gl_account_id)    AS gl_account_id,
            MAX(a.account_name)     AS account_name,
            a.level_0, a.level_1, a.level_2, a.level_3, a.level_4,
            l.amount::float8,
            l.line_note
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        LEFT JOIN dim_legal_entity le ON le.entity_prefix = l.entity_prefix
        WHERE l.journal_entry_group_number = :je AND l.fiscal_year = :fy
        GROUP BY l.booking_line_id, l.journal_entry_group_number, le.legal_entity_code,
                 l.fiscal_year, l.line_number, e.fiscal_period, e.posting_date,
                 e.document_date, l.account_number_group, a.level_0, a.level_1,
                 a.level_2, a.level_3, a.level_4, l.amount, l.line_note
        ORDER BY l.line_number
    """), {"je": je_num, "fy": fy}).fetchall()

    if not rows:
        return None

    lines = []
    for r in rows:
        d = dict(r._mapping) if hasattr(r, "_mapping") else dict(r)
        lines.append({
            "booking_line_id": d.get("booking_line_id"),
            "journal_entry_group_number": d.get("journal_entry_group_number"),
            "journal_entry_number": d.get("journal_entry_number"),
            "legal_entity_code": d.get("legal_entity_code"),
            "fiscal_year": d.get("fiscal_year"),
            "line_number": d.get("line_number"),
            "fiscal_period": d.get("fiscal_period"),
            "posting_date": d.get("posting_date"),
            "document_date": d.get("document_date"),
            "account_number_group": d.get("account_number_group"),
            "gl_account_id": d.get("gl_account_id"),
            "account_name": d.get("account_name"),
            "level_0": d.get("level_0"),
            "level_1": d.get("level_1"),
            "level_2": d.get("level_2"),
            "level_3": d.get("level_3"),
            "level_4": d.get("level_4"),
            "amount": float(d.get("amount") or 0),
            "line_note": d.get("line_note"),
        })

    first = dict(rows[0]._mapping) if hasattr(rows[0], "_mapping") else dict(rows[0])
    return {
        "journal_entry_group_number": je_num,
        "journal_entry_number": je_num[2:] if len(je_num) > 2 else je_num,
        "legal_entity_code": first.get("legal_entity_code"),
        "fiscal_year": fy,
        "lines": lines,
    }
