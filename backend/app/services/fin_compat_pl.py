"""P&L statement builder for the legacy compat layer.

Converts GDPdU SQL grain rows (from fin_compat_sql) into the exact legacy
FinancialStatementResponse row tree expected by the ported frontend.

The response shape matches legacy routers/financials.py get_pl_statement():
  rows[]:
    id, line_code, row_kind ('line'|'subtotal'|'kpi'|'title'),
    label, amounts {py_cm,pm,cm,ytd,ytd_py,[plan_cm,plan_vs_actual,mtd]},
    deltas {mom,yoy,ytd}, invert_delta, is_bold, drill, has_children,
    children[], accounts[]

Sign convention: amounts are PRESENTED (revenue +, expense −).
                 = same rule as the service layer in app/services/statements.py.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.fin_compat_sql import (
    _MONTH_ABBR,
    _fy_span_periods,
    _fy_span_totals,
    _last_12_periods,
    col_labels_annual,
    col_labels_month,
    col_labels_week,
    entity_sql_fragment,
    period_key,
    period_label,
    plan_anchor_for_week,
    col_labels_annual,
    pl_consl_grain_sql_annual_ytd,
    pl_consl_grain_sql_month,
    pl_consl_grain_sql_week,
    pl_grain_sql_annual,
    pl_grain_sql_month,
    pl_grain_sql_week,
    pl_monthly_grain_sql,
    pl_weekly_breakdown_sql,
    plan_grain_sql,
    resolve_entity_prefix,
    weekly_breakdown_layout,
)

# ---------------------------------------------------------------------------
# P&L subtotal semantics
# ---------------------------------------------------------------------------
# Subtotals/calcs/grandtotals are NOT computed from a hard-coded formula over
# legacy line codes.  They use the SAME running-sum semantics as the service
# layer (app/services/statements.py):
#
#   * 'mapping'    → Σ presented GL amounts matching the line's level filter.
#   * 'subtotal' / 'grandtotal' / non-ratio 'calc'
#                  → running cumulative sum of EVERY preceding 'mapping' line
#                    from the TOP of the statement (subtotals never re-counted).
#   * 'calc' with a *_PCT kpi_code → ratio (GROSS_MARGIN_PCT); the GDPdU
#                    structure currently defines no such row, but the path is
#                    kept for completeness and matches statements.py.
#
# Amounts arrive already PRESENTED from fin_compat_sql (revenue +, expense −;
# the single `amount * -1` lives in the SQL).  Never flip the sign here.

_AM_KEYS = ["py_cm", "pm", "cm", "ytd", "ytd_py"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_amounts(r: Any, *, week_ctx: bool = False) -> dict[str, float]:
    d = dict(r._mapping) if hasattr(r, "_mapping") else dict(r)
    out: dict[str, float] = {k: float(d.get(k) or 0) for k in _AM_KEYS}
    if week_ctx and "mtd" in d:
        out["mtd"] = float(d.get("mtd") or 0)
    return out


def _deltas(am: dict[str, float], invert: bool) -> dict[str, float]:
    mom = am["cm"] - am["pm"]
    yoy = am["cm"] - am["py_cm"]
    ytd_d = am["ytd"] - am["ytd_py"]
    if invert:
        mom, yoy, ytd_d = -mom, -yoy, -ytd_d
    return {"mom": round(mom, 2), "yoy": round(yoy, 2), "ytd": round(ytd_d, 2)}


def _round_am(d: dict[str, float]) -> dict[str, float]:
    return {k: round(v, 2) for k, v in d.items()}


def _kpi_pct(num: dict[str, float], den: dict[str, float], key: str) -> Optional[float]:
    d = den.get(key, 0)
    if abs(d) < 1e-6:
        return None
    return round(num.get(key, 0) / abs(d) * 100, 2)


def _resolve_kpi_line_code(kpi_ref: Any, fallback: str) -> str:
    """Resolve a kpi_code reference to a structure line_code.

    GDPdU kpi_code values ARE real line codes (e.g. 'EBITDA', 'NET_PROFIT'),
    so resolution is the identity; fall back when the ref is empty/non-str.
    """
    if kpi_ref is None or (isinstance(kpi_ref, str) and not kpi_ref.strip()):
        return str(fallback)
    return str(kpi_ref)


def _sort_key_cm(am: dict[str, float]) -> float:
    return abs(float(am.get("cm") or 0))


# ---------------------------------------------------------------------------
# P&L vs Balance-sheet row separation
# ---------------------------------------------------------------------------

def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row._mapping) if hasattr(row, "_mapping") else dict(row)


def _is_pl_structure_row(r: dict[str, Any]) -> bool:
    """True for P&L rows only (balance-sheet rows excluded).

    The GDPdU ``dim_pl_structure`` stores both P&L (sort_order < 1000) and the
    balance sheet (sort_order >= 1010, line_code prefixed 'BS_') in one table.
    We keep a row only when BOTH markers agree it is below the BS block:
    ``sort_order < 1000`` AND ``line_code`` does not start with 'BS_'.  Using
    both criteria is robust if either marker drifts in a future structure load.
    """
    code = str(r.get("line_code") or "")
    try:
        so = int(r.get("sort_order") or 0)
    except (TypeError, ValueError):
        so = 0
    return so < 1000 and not code.startswith("BS_")


def _is_ratio_calc(r: dict[str, Any]) -> bool:
    """A 'calc' row is a ratio iff its kpi_code ends with '_PCT'."""
    if r.get("row_type") != "calc":
        return False
    kc = str(r.get("kpi_code") or "")
    return kc.upper().endswith("_PCT")


def _row_kind_for(row_type: str) -> str:
    """Map GDPdU row_type → legacy frontend row_kind.

    mapping → 'line'; subtotal/calc/grandtotal (and legacy 'computed') →
    'subtotal'.  Legacy 'kpi'/'title' pass through unchanged.
    """
    if row_type == "mapping":
        return "line"
    if row_type in ("subtotal", "calc", "grandtotal", "computed"):
        return "subtotal"
    return row_type


def _compute_running_values(
    struct_pl: list[dict[str, Any]],
    mapping_vals: dict[str, dict[str, float]],
    keys: list[str],
) -> dict[str, dict[str, Optional[float]]]:
    """Running-sum semantics over an ordered P&L structure (mirrors statements.py).

    ``struct_pl``     : P&L structure rows as dicts, already sorted by sort_order.
    ``mapping_vals``  : {line_code: {col: presented_value}} for 'mapping' rows.
    ``keys``          : the value columns to accumulate (e.g. py_cm/pm/cm/...).

    Returns {line_code: {col: value}}:
      * 'mapping'  → its own amounts; also added to the per-column running sum.
      * 'subtotal'/'grandtotal'/non-ratio 'calc'/'computed' → running-sum snapshot.
      * ratio 'calc' (*_PCT) → GROSS_PROFIT / NET_SALES × 100 (None when denom 0).
    Only 'mapping' rows accumulate, so subtotals are never double-counted and
    NET_PROFIT == Σ of all P&L mapping lines.
    """
    running = {k: 0.0 for k in keys}
    out: dict[str, dict[str, Optional[float]]] = {}
    for r in struct_pl:
        code = r["line_code"]
        rt = r.get("row_type", "mapping")
        if rt == "mapping":
            am = mapping_vals.get(code) or {}
            vals = {k: float(am.get(k, 0.0) or 0.0) for k in keys}
            for k in keys:
                running[k] += vals[k]
            out[code] = vals  # type: ignore[assignment]
        elif _is_ratio_calc(r):
            gp = out.get("GROSS_PROFIT", {})
            ns = out.get("NET_SALES", {})
            ratio: dict[str, Optional[float]] = {}
            for k in keys:
                denom = ns.get(k)
                ratio[k] = (gp.get(k, 0.0) / denom * 100.0) if denom else None
            out[code] = ratio
        else:
            # subtotal / grandtotal / non-ratio calc / legacy 'computed' /
            # anything else → snapshot of the running sum so far.
            out[code] = {k: running[k] for k in keys}
    return out


# ---------------------------------------------------------------------------
# Structure row helpers
# ---------------------------------------------------------------------------

def _load_structure(session: Session) -> list[Any]:
    """Load dim_pl_structure; returns list of Row objects."""
    rows = session.execute(text(
        "SELECT pl_line_id, sort_order, line_code, row_type, balance_title, details, "
        "calc_type, level_2, level_3, level_4, gl_account_id, invert_delta, is_bold, kpi_code "
        "FROM dim_pl_structure ORDER BY sort_order"
    )).fetchall()
    return rows


def _match_grain(g: dict, row: Any) -> bool:
    """Match a grain row to a dim_pl_structure row (level-based; gl_account_id is display only)."""
    r = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
    # If dim_pl_structure has a gl_account_id, try to match via a.gl_account_id in grain
    gid = (r.get("gl_account_id") or "").strip()
    if gid:
        return (g.get("gl_account_id") or "").strip() == gid

    l2 = (r.get("level_2") or "").strip()
    l3 = (r.get("level_3") or "").strip()
    l4 = (r.get("level_4") or "").strip()
    if not l2 and not l3 and not l4:
        return False

    g2 = (g.get("level_2") or "").strip()
    g3 = (g.get("level_3") or "").strip()
    g4 = (g.get("level_4") or "").strip()

    if l2 and g2 != l2:
        return False
    if l3 and g3 != l3:
        return False
    if l4:
        return g4 == l4
    return True


# ---------------------------------------------------------------------------
# Account-level rows under a level-4 grouping
# ---------------------------------------------------------------------------

def _accounts_under_l4(grains: list[dict], l2: str, l3: str, l4: str, invert: bool,
                       week_ctx: bool = False) -> list[dict]:
    out = []
    seen_ang: dict[str, dict[str, float]] = {}
    seen_meta: dict[str, tuple[str, str]] = {}  # ang → (gl_account_id, account_name)
    for g in grains:
        if (g.get("level_3") or "").strip() != l3:
            continue
        if (g.get("level_4") or "").strip() != l4:
            continue
        if l2 and (g.get("level_2") or "").strip() != l2:
            continue
        ang = g.get("account_number_group") or g.get("gl_account_id") or ""
        if not ang:
            continue
        am = _row_amounts(g, week_ctx=week_ctx)
        if sum(abs(v) for v in am.values()) < 1e-9:
            continue
        if ang not in seen_ang:
            seen_ang[ang] = {k: 0.0 for k in am}
            gid = (g.get("gl_account_id") or ang).strip()
            aname = (g.get("account_name") or "").strip()
            seen_meta[ang] = (gid, aname)
        for k in am:
            seen_ang[ang][k] = seen_ang[ang].get(k, 0.0) + am[k]
    for ang, am in seen_ang.items():
        gid, aname = seen_meta[ang]
        acc_label = f"{gid} | {aname}" if aname else gid
        out.append({
            "id": f"acc-{ang}",
            "line_code": gid,
            "label": acc_label,
            "row_kind": "account",
            "amounts": _round_am(am),
            "deltas": _deltas(am, invert),
            "invert_delta": invert,
            "drill": {"statement_type": "PL", "level_2": l2 or None,
                      "level_3": l3, "level_4": l4, "gl_account_id": gid},
        })
    out.sort(key=lambda x: _sort_key_cm(x.get("amounts") or {}), reverse=True)
    return out


# ---------------------------------------------------------------------------
# Plan attachment
# ---------------------------------------------------------------------------

def _attach_plan(
    am: dict[str, float],
    plan_map: dict[str, dict[str, float]],
    line_code: str,
    week_ctx: Optional[dict] = None,
) -> dict[str, float]:
    out = dict(am)
    pm_data = plan_map.get(line_code)
    if not pm_data:
        return out
    plan_cm = float(pm_data.get("plan_cm") or 0)
    out["plan_cm"] = round(plan_cm, 2)
    out["plan_vs_actual"] = round(float(am.get("cm") or 0) - plan_cm, 2)
    if week_ctx and "mtd" in am:
        # Prorate plan_cm over the week's MTD fraction
        anchor_month_days = 20  # approximate
        mtd = float(am.get("mtd") or 0)
        out["mtd_plan"] = round(plan_cm * 0.5, 2)  # rough MTD plan proxy
    return out


def _load_plan_map(
    session: Session,
    year: int, month: int,
    ent_frag: str,
) -> dict[str, dict[str, float]]:
    """Load plan data from fact_gl_plan; prefer 'forecast', fallback to 'plan'.

    Subtotal/calc/grandtotal plan values use the SAME running-sum semantics as
    actuals — the cumulative sum of the mapping-line plan values above them — so
    there is no hard-coded formula over legacy codes.
    """
    struct_pl = [r for r in (_row_dict(r) for r in _load_structure(session))
                 if _is_pl_structure_row(r)]
    plan_keys = ["plan_cm", "ytd_plan", "ytg"]

    for scenario in ("forecast", "plan"):
        sql, params = plan_grain_sql(year, month, ent_frag, scenario)
        plan_grains = [
            dict(r._mapping) for r in session.execute(text(sql), params).fetchall()
        ]
        if not plan_grains:
            continue

        mapping_plan: dict[str, dict[str, float]] = {}
        for r in struct_pl:
            if r.get("row_type") != "mapping":
                continue
            code = r["line_code"]
            acc: dict[str, float] = {k: 0.0 for k in plan_keys}
            for g in plan_grains:
                if _match_grain(g, r):
                    for k in plan_keys:
                        acc[k] += float(g.get(k) or 0)
            mapping_plan[code] = acc

        running = _compute_running_values(struct_pl, mapping_plan, plan_keys)
        line_plan = {
            code: {k: float(v.get(k) or 0.0) for k in plan_keys}
            for code, v in running.items()
        }

        has_signal = any(abs(float(v.get("plan_cm") or 0)) > 1e-6 for v in line_plan.values())
        if has_signal:
            return line_plan

    return {}


# ---------------------------------------------------------------------------
# Main P&L builder
# ---------------------------------------------------------------------------

def build_pl_statement_compat(
    session: Session,
    *,
    period_grain: str = "month",
    year: Optional[int] = None,
    month: Optional[int] = None,
    iso_year: Optional[int] = None,
    iso_week: Optional[int] = None,
    entity: Optional[str] = None,
) -> dict[str, Any]:
    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)

    week_ctx: Optional[dict] = None
    if period_grain == "week":
        assert iso_year is not None and iso_week is not None
        sql, params = pl_grain_sql_week(iso_year, iso_week, ent_frag)
        yr, mo = plan_anchor_for_week(iso_year, iso_week)
        labels = col_labels_week(iso_year, iso_week)
        week_ctx = {"iso_year": iso_year, "iso_week": iso_week}
    else:
        assert year is not None and month is not None
        sql, params = pl_grain_sql_month(year, month, ent_frag)
        yr, mo = year, month
        labels = col_labels_month(year, month)

    grains = [dict(r._mapping) for r in session.execute(text(sql), params).fetchall()]

    struct = _load_structure(session)
    if not struct:
        raise ValueError("dim_pl_structure is empty — run setup or load PL Structure")

    plan_map = _load_plan_map(session, yr, mo, ent_frag)

    return _build_rows(struct, grains, plan_map, yr, mo, labels, week_ctx=week_ctx,
                       period_grain=period_grain, iso_year=iso_year, iso_week=iso_week)


def _build_rows(
    struct: list[Any],
    grains: list[dict],
    plan_map: dict[str, dict[str, float]],
    yr: int, mo: int,
    labels: dict[str, str],
    *,
    week_ctx: Optional[dict],
    period_grain: str,
    iso_year: Optional[int],
    iso_week: Optional[int],
) -> dict[str, Any]:
    is_week = week_ctx is not None
    keys = _AM_KEYS + (["mtd"] if is_week else [])
    zero = {k: 0.0 for k in keys}

    # P&L rows only (exclude balance-sheet rows) — must happen BEFORE the running
    # sum so subtotals never accumulate BS movements.
    struct_pl = [r for r in (_row_dict(row) for row in struct) if _is_pl_structure_row(r)]

    mapping_vals: dict[str, dict[str, float]] = {}
    for r in struct_pl:
        if r.get("row_type") != "mapping":
            continue
        code = r["line_code"]
        acc = {k: 0.0 for k in keys}
        for g in grains:
            if _match_grain(g, r):
                am = _row_amounts(g, week_ctx=is_week)
                for k in keys:
                    acc[k] += am.get(k, 0.0)
        mapping_vals[code] = acc

    # Subtotals/calcs via running-sum (no hard-coded legacy formula).
    running = _compute_running_values(struct_pl, mapping_vals, keys)
    line_vals: dict[str, dict[str, float]] = {
        code: {k: float(v.get(k) or 0.0) for k in keys} for code, v in running.items()
    }

    # KPI helpers (% of TOTAL_OUTPUT)
    to = line_vals.get("TOTAL_OUTPUT", zero)

    def kpi_row_am(kpi_code_str: str) -> dict[str, float]:
        line = _resolve_kpi_line_code(kpi_code_str, "NET_PROFIT")
        num = line_vals.get(line, zero)
        return {k: (_kpi_pct(num, to, k) or 0.0) for k in _AM_KEYS}

    rows_out: list[dict[str, Any]] = []
    for r in struct_pl:
        rc = r["line_code"]
        rt = r.get("row_type", "mapping")
        inv = bool(r.get("invert_delta", False))

        if rt == "kpi":
            kc = r.get("kpi_code") or rc
            am = kpi_row_am(str(kc))
            am_full = _attach_plan({**am}, plan_map, rc, week_ctx)
            rows_out.append({
                "id": f"pl-{rc}", "parent_id": None,
                "line_code": rc, "row_kind": "kpi",
                "label": r["balance_title"],
                "amounts": _round_am(am_full),
                "deltas": _deltas(am, invert=False),
                "invert_delta": False, "is_bold": False,
                "drill": None, "has_children": False, "children": [],
            })
            continue

        if rt == "title":
            rows_out.append({
                "id": f"pl-{rc}", "parent_id": None,
                "line_code": rc, "row_kind": "title",
                "label": r["balance_title"],
                "amounts": None, "deltas": None,
                "invert_delta": False, "is_bold": bool(r.get("is_bold", False)),
                "drill": None, "has_children": False, "children": [],
            })
            continue

        am = line_vals.get(rc, {**zero})
        deltas = _deltas(am, inv)
        l3 = (r.get("level_3") or "").strip()
        l4 = (r.get("level_4") or "").strip()
        l2 = (r.get("level_2") or "").strip()
        gid = (r.get("gl_account_id") or "").strip() or None
        drill = {
            "statement_type": "PL",
            "level_2": l2 or None, "level_3": l3 or None,
            "level_4": l4 or None, "gl_account_id": gid,
        }

        # L4 children for L3-only mapping rows
        children: list[dict] = []
        hoisted_accounts: list[dict] = []

        if rt == "mapping" and l3 and not l4 and not gid:
            seen_l4: dict[str, dict[str, float]] = {}
            for g in grains:
                if (g.get("level_3") or "").strip() != l3:
                    continue
                if l2 and (g.get("level_2") or "").strip() != l2:
                    continue
                l4v = (g.get("level_4") or "").strip()
                if not l4v:
                    continue
                if l4v not in seen_l4:
                    seen_l4[l4v] = {k: 0.0 for k in keys}
                am4 = _row_amounts(g, week_ctx=is_week)
                for k in keys:
                    seen_l4[l4v][k] = seen_l4[l4v].get(k, 0.0) + am4.get(k, 0.0)
            for l4v, am4 in sorted(seen_l4.items(), key=lambda kv: _sort_key_cm(kv[1]), reverse=True):
                d4 = _deltas(am4, inv)
                children.append({
                    "id": f"pl-{rc}-l4-{abs(hash(l4v)) % 100000}",
                    "line_code": f"{rc}::{l4v}",
                    "label": l4v,
                    "row_kind": "detail",
                    "amounts": _round_am(am4),
                    "deltas": d4,
                    "invert_delta": inv,
                    "drill": {"statement_type": "PL", "level_2": l2 or None,
                              "level_3": l3, "level_4": l4v, "gl_account_id": None},
                    "accounts": _accounts_under_l4(grains, l2, l3, l4v, inv, week_ctx=is_week),
                })
            # Dedup: single L4 with same label as parent → hoist
            if len(children) == 1 and children[0].get("label", "") == r["balance_title"]:
                child = children[0]
                drill = child["drill"]
                hoisted_accounts = child.get("accounts", [])
                children = []

        am_full = _attach_plan(_round_am(am), plan_map, rc, week_ctx)
        row_kind = _row_kind_for(rt)

        rows_out.append({
            "id": f"pl-{rc}", "parent_id": None,
            "line_code": rc,
            "row_kind": row_kind,
            "label": r["balance_title"],
            "amounts": am_full,
            "deltas": deltas,
            "invert_delta": inv,
            "is_bold": bool(r.get("is_bold", False)),
            "drill": drill,
            "has_children": len(children) > 0 or len(hoisted_accounts) > 0,
            "children": children,
            "accounts": hoisted_accounts,
        })

    out: dict[str, Any] = {
        "statement": "pl",
        "period_grain": period_grain,
        "year": yr,
        "month": mo,
        "col_labels": labels,
        "rows": rows_out,
    }
    if iso_year is not None:
        out["iso_year"] = iso_year
    if iso_week is not None:
        out["iso_week"] = iso_week
    return out


# ---------------------------------------------------------------------------
# Annual exit-readiness flow view (FY / YTD / LTM columns)
# ---------------------------------------------------------------------------
# Column keys for the annual statement.  ``fy1`` is aggregated and carried in
# each row's amounts but — like the legacy ErFlowResponse — is not given a
# col_label.  Subtotals/calcs/grandtotals use the SAME running-sum semantics as
# the monthly statement (mapping lines accumulate; subtotals snapshot the running
# sum) — NOT the legacy COMPUTED_FORMULA over FG_WIP/COM/EBITDA_ROW codes.
# Amounts already arrive PRESENTED (income +, expense −); never re-sign here.

_ER_FLOW_KEYS = ["fy1", "fy2", "fy3", "ytd", "ltm", "ytd_py", "ltm_py", "fy_f"]


def _er_row_amounts(r: Any) -> dict[str, float]:
    d = dict(r._mapping) if hasattr(r, "_mapping") else dict(r)
    return {k: float(d.get(k) or 0) for k in _ER_FLOW_KEYS}


def _er_flow_deltas(am: dict[str, float], invert: bool) -> dict[str, float]:
    """Annual flow deltas (mirrors legacy _er_flow_deltas, lines 79-85):

        delta_fy  = fy3 - fy2
        delta_ytd = ytd - ytd_py
        delta_ltm = fy_f - ltm_py  (FY forecast vs prior LTM; mirrors legacy ErFlow)

    Negated when ``invert_delta`` is set (e.g. cost lines, where a smaller
    expense is a positive development).
    """
    d = {
        "delta_fy":  am["fy3"] - am["fy2"],
        "delta_ytd": am["ytd"] - am["ytd_py"],
        "delta_ltm": float(am.get("fy_f") or am["ltm"]) - am["ltm_py"],
    }
    return {k: -v for k, v in d.items()} if invert else d


def _er_sort_key(am: dict[str, float]) -> float:
    """Magnitude proxy for child ordering — the most recent full year (fy3)."""
    return abs(float(am.get("fy3") or 0))


def _er_accounts_under_l4(
    grains: list[dict], l2: str, l3: str, l4: str, invert: bool,
) -> list[dict]:
    """Account-level rows under one level-4 grouping (annual flow columns).

    Mirrors :func:`_accounts_under_l4` but accumulates the 7 flow keys and uses
    :func:`_er_flow_deltas`.
    """
    out = []
    seen_ang: dict[str, dict[str, float]] = {}
    seen_meta: dict[str, tuple[str, str]] = {}  # ang → (gl_account_id, account_name)
    for g in grains:
        if (g.get("level_3") or "").strip() != l3:
            continue
        if (g.get("level_4") or "").strip() != l4:
            continue
        if l2 and (g.get("level_2") or "").strip() != l2:
            continue
        ang = g.get("account_number_group") or g.get("gl_account_id") or ""
        if not ang:
            continue
        am = _er_row_amounts(g)
        if sum(abs(v) for v in am.values()) < 1e-9:
            continue
        if ang not in seen_ang:
            seen_ang[ang] = {k: 0.0 for k in _ER_FLOW_KEYS}
            gid = (g.get("gl_account_id") or ang).strip()
            aname = (g.get("account_name") or "").strip()
            seen_meta[ang] = (gid, aname)
        for k in _ER_FLOW_KEYS:
            seen_ang[ang][k] = seen_ang[ang].get(k, 0.0) + am[k]
    for ang, am in seen_ang.items():
        gid, aname = seen_meta[ang]
        acc_label = f"{gid} | {aname}" if aname else gid
        out.append({
            "id": f"er-acc-{ang}",
            "line_code": gid,
            "label": acc_label,
            "row_kind": "account",
            "amounts": _round_am(am),
            "deltas": _round_am(_er_flow_deltas(am, invert)),
            "invert_delta": invert,
            "drill": {"statement_type": "PL", "level_2": l2 or None,
                      "level_3": l3, "level_4": l4, "gl_account_id": gid},
        })
    out.sort(key=lambda x: _er_sort_key(x.get("amounts") or {}), reverse=True)
    return out


def build_pl_annual_compat(
    session: Session,
    *,
    year: int,
    month: int,
    entity: Optional[str] = None,
) -> dict[str, Any]:
    """Annual (exit-readiness flow) P&L — FY/YTD/LTM columns over the GDPdU GL.

    Returns the legacy ErFlowResponse shape consumed by the verbatim-ported
    frontend (GET /api/v1/exit-readiness/pl-statement):

        {"statement": "pl", "year", "month",
         "col_labels": {fy2, fy3, ytd_py, ytd, ltm_py, ltm},
         "rows": [ {id, line_code, row_kind, label, amounts{fy1..ltm_py},
                    deltas{delta_fy, delta_ytd, delta_ltm}, invert_delta,
                    is_bold, drill, has_children, children[], accounts[]} ]}

    Subtotals/calcs/grandtotals are running cumulative sums of the preceding
    *mapping* lines — the corrected GDPdU running-sum semantics from
    :func:`build_pl_statement_compat`, NOT the legacy COMPUTED_FORMULA over
    FG_WIP/COM/EBITDA_ROW codes.

    === WORKED EXAMPLE (fy3 column) ===
      NET_SALES fy3=10, FINISHED_GOODS_WIP fy3=6, OWN_WORK_CAPITALISED fy3=0
        → TOTAL_OUTPUT fy3 = 10 + 6 + 0 = 16
      COST_OF_MATERIALS fy3=-10
        → GROSS_PROFIT fy3 = 16 + (-10) = 6
      delta_fy(GROSS_PROFIT) = fy3 - fy2.

    === EDGE CASES ===
      * Missing FY → grain column 0 → that FY's subtotal column 0.
      * Balance-sheet rows (sort_order >= 1000 / 'BS_' prefix) are dropped BEFORE
        the running sum, so BS movements never inflate a P&L subtotal.
      * invert_delta lines have their three deltas negated.
      * entity=None → no entity filter; fiscal_period 13 excluded in the SQL.
      * month == 12 → LTM/LTM_PY are full calendar years (see pl_grain_sql_annual).
    """
    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)

    sql, params = pl_grain_sql_annual(year, month, ent_frag)
    grains = [dict(r._mapping) for r in session.execute(text(sql), params).fetchall()]

    struct = _load_structure(session)
    if not struct:
        raise ValueError("dim_pl_structure is empty — run setup or load PL Structure")

    plan_map = _load_plan_map(session, year, month, ent_frag)
    return _build_annual_rows(struct, grains, year, month, plan_map=plan_map)


def _apply_annual_fy_forecast(
    line_vals: dict[str, dict[str, float]],
    plan_map: dict[str, dict[str, float]],
) -> None:
    """Set ``fy_f`` (FY forecast column, labelled FY25F) = YTD + plan for months after anchor.

    ``ltm`` stays the rolling-12-month actual from SQL (LTMJul25A).  When no plan
  exists, forecast equals YTD actuals.
    """
    has_ytg_plan = any(
        abs(float(pm.get("ytg") or 0)) > 1e-6 for pm in plan_map.values()
    )
    for code, am in line_vals.items():
        ytd_v = float(am.get("ytd") or 0)
        ytg = float(plan_map.get(code, {}).get("ytg") or 0)
        if has_ytg_plan:
            am["fy_f"] = round(ytd_v + ytg, 2)
        else:
            am["fy_f"] = round(ytd_v, 2)


def _build_annual_rows(
    struct: list[Any],
    grains: list[dict],
    year: int,
    month: int,
    *,
    plan_map: Optional[dict[str, dict[str, float]]] = None,
) -> dict[str, Any]:
    keys = _ER_FLOW_KEYS
    zero = {k: 0.0 for k in keys}

    # P&L rows only (exclude balance-sheet rows) — must happen BEFORE the running
    # sum so subtotals never accumulate BS movements.
    struct_pl = [r for r in (_row_dict(row) for row in struct) if _is_pl_structure_row(r)]

    mapping_vals: dict[str, dict[str, float]] = {}
    for r in struct_pl:
        if r.get("row_type") != "mapping":
            continue
        code = r["line_code"]
        acc = {k: 0.0 for k in keys}
        for g in grains:
            if _match_grain(g, r):
                am = _er_row_amounts(g)
                for k in keys:
                    acc[k] += am.get(k, 0.0)
        mapping_vals[code] = acc

    # Subtotals/calcs/grandtotals via running-sum (no hard-coded legacy formula).
    running = _compute_running_values(struct_pl, mapping_vals, keys)
    line_vals: dict[str, dict[str, float]] = {
        code: {k: float(v.get(k) or 0.0) for k in keys} for code, v in running.items()
    }

    _apply_annual_fy_forecast(line_vals, plan_map or {})

    # KPI helper (% of TOTAL_OUTPUT) — GDPdU PL has no kpi rows today, but the
    # path is kept for parity with the legacy ErFlow builder.
    to = line_vals.get("TOTAL_OUTPUT", zero)

    def kpi_row_am(kpi_code_str: str) -> dict[str, float]:
        line = _resolve_kpi_line_code(kpi_code_str, "NET_PROFIT")
        num = line_vals.get(line, zero)
        return {k: (_kpi_pct(num, to, k) or 0.0) for k in keys}

    rows_out: list[dict[str, Any]] = []
    kpi_header_appended = False
    for r in struct_pl:
        rc = r["line_code"]
        rt = r.get("row_type", "mapping")
        inv = bool(r.get("invert_delta", False))

        if rt == "title":
            rows_out.append({
                "id": f"er-pl-{rc}", "parent_id": None, "line_code": rc,
                "row_kind": "title", "label": r["balance_title"],
                "amounts": None, "deltas": None,
                "invert_delta": False, "is_bold": bool(r.get("is_bold", False)),
                "drill": None, "has_children": False, "children": [],
            })
            continue

        if rt == "kpi":
            if not kpi_header_appended:
                rows_out.append({
                    "id": "er-pl-kpi-header", "parent_id": None, "line_code": "KPI_HEADER",
                    "row_kind": "kpi_header", "label": "KPIs — as % of total output",
                    "amounts": None, "deltas": None,
                    "invert_delta": False, "is_bold": False,
                    "drill": None, "has_children": False, "children": [],
                })
                kpi_header_appended = True
            kc = r.get("kpi_code") or rc
            am = kpi_row_am(str(kc))
            rows_out.append({
                "id": f"er-pl-{rc}", "parent_id": None, "line_code": rc,
                "row_kind": "kpi", "label": r["balance_title"],
                "amounts": _round_am(am),
                "deltas": _round_am(_er_flow_deltas(am, invert=False)),
                "invert_delta": False, "is_bold": False,
                "drill": None, "has_children": False, "children": [],
            })
            continue

        am = line_vals.get(rc, {**zero})
        deltas = _round_am(_er_flow_deltas(am, inv))
        l2 = (r.get("level_2") or "").strip()
        l3 = (r.get("level_3") or "").strip()
        l4 = (r.get("level_4") or "").strip()
        gid = (r.get("gl_account_id") or "").strip() or None
        drill = {
            "statement_type": "PL",
            "level_2": l2 or None, "level_3": l3 or None,
            "level_4": l4 or None, "gl_account_id": gid,
        }

        # L4 children for L3-only mapping rows (+ accounts-under-L4 + hoisting).
        children: list[dict] = []
        hoisted_accounts: list[dict] = []

        if rt == "mapping" and l3 and not l4 and not gid:
            seen_l4: dict[str, dict[str, float]] = {}
            for g in grains:
                if (g.get("level_3") or "").strip() != l3:
                    continue
                if l2 and (g.get("level_2") or "").strip() != l2:
                    continue
                l4v = (g.get("level_4") or "").strip()
                if not l4v:
                    continue
                if l4v not in seen_l4:
                    seen_l4[l4v] = {k: 0.0 for k in keys}
                am4 = _er_row_amounts(g)
                for k in keys:
                    seen_l4[l4v][k] = seen_l4[l4v].get(k, 0.0) + am4.get(k, 0.0)
            for l4v, am4 in sorted(seen_l4.items(),
                                   key=lambda kv: _er_sort_key(kv[1]), reverse=True):
                children.append({
                    "id": f"er-pl-{rc}-l4-{abs(hash(l4v)) % 100000}",
                    "line_code": f"{rc}::{l4v}",
                    "label": l4v,
                    "row_kind": "detail",
                    "amounts": _round_am(am4),
                    "deltas": _round_am(_er_flow_deltas(am4, inv)),
                    "invert_delta": inv,
                    "drill": {"statement_type": "PL", "level_2": l2 or None,
                              "level_3": l3, "level_4": l4v, "gl_account_id": None},
                    "accounts": _er_accounts_under_l4(grains, l2, l3, l4v, inv),
                })
            # Dedup: single L4 with same label as parent → hoist its accounts.
            if len(children) == 1 and children[0].get("label", "") == r["balance_title"]:
                child = children[0]
                drill = child["drill"]
                hoisted_accounts = child.get("accounts", [])
                children = []

        rows_out.append({
            "id": f"er-pl-{rc}", "parent_id": None, "line_code": rc,
            "row_kind": _row_kind_for(rt),
            "label": r["balance_title"],
            "amounts": _round_am(am),
            "deltas": deltas,
            "invert_delta": inv,
            "is_bold": bool(r.get("is_bold", False)),
            "drill": drill,
            "has_children": len(children) > 0 or len(hoisted_accounts) > 0,
            "children": children,
            "accounts": hoisted_accounts,
        })

    return {
        "statement": "pl",
        "year": year,
        "month": month,
        "col_labels": col_labels_annual(year, month),
        "rows": rows_out,
    }


# ---------------------------------------------------------------------------
# Plan response builder (standalone /pl-statement/plan endpoint)
# ---------------------------------------------------------------------------

def build_pl_plan_response(
    session: Session,
    year: int, month: int, entity: Optional[str],
) -> dict[str, Any]:
    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)

    struct_pl = [r for r in (_row_dict(r) for r in _load_structure(session))
                 if _is_pl_structure_row(r)]
    # Emit value-bearing P&L rows (mapping + subtotal/calc/grandtotal); skip
    # presentation-only legacy 'title'/'kpi' rows.
    struct_codes = [
        r["line_code"] for r in struct_pl
        if r.get("row_type") in ("mapping", "subtotal", "calc", "grandtotal", "computed")
    ]

    plan_map = _load_plan_map(session, year, month, ent_frag)

    # Actuals CM for plan_vs_actual — same running-sum semantics as the statement.
    sql, params = pl_grain_sql_month(year, month, ent_frag)
    grains_raw = [dict(r._mapping) for r in session.execute(text(sql), params).fetchall()]

    mapping_actual: dict[str, dict[str, float]] = {}
    for r in struct_pl:
        if r.get("row_type") != "mapping":
            continue
        code = r["line_code"]
        cm = 0.0
        for g in grains_raw:
            if _match_grain(g, r):
                cm += float(g.get("cm") or 0)
        mapping_actual[code] = {"cm": cm}
    running_actual = _compute_running_values(struct_pl, mapping_actual, ["cm"])
    actual_vals: dict[str, float] = {
        code: float(v.get("cm") or 0.0) for code, v in running_actual.items()
    }

    has_plan = bool(plan_map)
    lines: list[dict[str, Any]] = []
    for code in struct_codes:
        pm_data = plan_map.get(code, {})
        plan_cm = float(pm_data.get("plan_cm") or 0)
        ytd_plan = float(pm_data.get("ytd_plan") or 0)
        ytg = float(pm_data.get("ytg") or 0)
        actual_cm = actual_vals.get(code, 0.0)
        coverage_pct: Optional[float] = None
        if abs(plan_cm) > 1e-6:
            coverage_pct = round(actual_cm / abs(plan_cm) * 100, 2)
        lines.append({
            "line_code": code,
            "plan_cm": round(plan_cm, 2),
            "plan_vs_actual": round(actual_cm - plan_cm, 2),
            "ytd_plan": round(ytd_plan, 2),
            "ytg": round(ytg, 2),
            "coverage_pct": coverage_pct,
        })

    return {"year": year, "month": month, "entity": entity, "has_plan_data": has_plan, "lines": lines}


# ---------------------------------------------------------------------------
# Consolidation builder
# ---------------------------------------------------------------------------

def build_pl_consolidation(
    session: Session,
    *,
    period_grain: str = "month",
    year: Optional[int] = None,
    month: Optional[int] = None,
    iso_year: Optional[int] = None,
    iso_week: Optional[int] = None,
) -> dict[str, Any]:
    if period_grain == "week":
        assert iso_year is not None and iso_week is not None
        sql, params = pl_consl_grain_sql_week(iso_year, iso_week)
        yr, mo = plan_anchor_for_week(iso_year, iso_week)
        labels = col_labels_week(iso_year, iso_week)
    else:
        assert year is not None and month is not None
        sql, params = pl_consl_grain_sql_month(year, month)
        yr, mo = year, month
        labels = col_labels_month(year, month)

    grains = [dict(r._mapping) for r in session.execute(text(sql), params).fetchall()]

    # Fetch entities (legal_entity_code → entity_prefix mapping)
    ent_rows = session.execute(text(
        "SELECT legal_entity_code, entity_prefix, entity_name FROM dim_legal_entity ORDER BY legal_entity_code"
    )).fetchall()
    entity_codes = [r[0] for r in ent_rows]
    ep_to_code = {r[1]: r[0] for r in ent_rows}  # entity_prefix → legal_entity_code
    entity_dicts = [{"code": r[0], "label": r[2]} for r in ent_rows]

    struct = _load_structure(session)
    if not struct:
        raise ValueError("dim_pl_structure is empty")
    struct_pl = [r for r in (_row_dict(r) for r in struct) if _is_pl_structure_row(r)]

    def _consl_amounts(matched_grains: list[dict]) -> dict[str, float]:
        """Sum per entity_prefix; key by legal_entity_code."""
        ep_sums: dict[str, float] = {}
        for g in matched_grains:
            ep = g.get("entity_prefix") or ""
            lec = ep_to_code.get(ep, ep)
            ep_sums[lec] = ep_sums.get(lec, 0.0) + float(g.get("cm") or 0)
        return {ec: ep_sums.get(ec, 0.0) for ec in entity_codes}

    # Mapping: collect per entity, then subtotals via running-sum per entity column.
    mapping_entity: dict[str, dict[str, float]] = {}
    for r in struct_pl:
        if r.get("row_type") != "mapping":
            continue
        code = r["line_code"]
        matched = [g for g in grains if _match_grain(g, r)]
        mapping_entity[code] = _consl_amounts(matched)

    running_entity = _compute_running_values(struct_pl, mapping_entity, entity_codes)
    line_entity: dict[str, dict[str, float]] = {
        code: {ec: float(v.get(ec) or 0.0) for ec in entity_codes}
        for code, v in running_entity.items()
    }

    to_agg = sum(line_entity.get("TOTAL_OUTPUT", {}).get(ec, 0.0) for ec in entity_codes)
    entity_to = line_entity.get("TOTAL_OUTPUT", {ec: 0.0 for ec in entity_codes})

    def _consl_row(row_id: str, label: str, row_kind: str, is_bold: bool,
                   am: dict[str, float], has_children: bool = False,
                   children: Optional[list] = None) -> dict:
        agg = sum(am.get(ec, 0.0) for ec in entity_codes)
        return {
            "id": row_id, "label": label, "row_kind": row_kind, "is_bold": is_bold,
            "entity_amounts": {ec: round(am.get(ec, 0.0), 2) for ec in entity_codes},
            "aggregated": round(agg, 2),
            "ic_eliminations": 0.0,
            "consolidation": round(agg, 2),
            "has_children": has_children,
            "children": children or [],
        }

    rows_out: list[dict[str, Any]] = []
    kpi_header_appended = False
    for r in struct_pl:
        rt = r.get("row_type", "mapping")
        rc = r["line_code"]

        if rt == "kpi":
            if not kpi_header_appended:
                rows_out.append({
                    "id": "pl-kpi-header", "label": "KPIs — as % of total output",
                    "row_kind": "kpi_header", "is_bold": False,
                    "entity_amounts": {ec: 0.0 for ec in entity_codes},
                    "aggregated": 0.0, "ic_eliminations": 0.0, "consolidation": 0.0,
                    "has_children": False, "children": [],
                })
                kpi_header_appended = True
            kpi_ref = r.get("kpi_code") or rc
            ref_code = _resolve_kpi_line_code(str(kpi_ref), str(rc or "NET_PROFIT"))
            num_agg = sum(line_entity.get(ref_code, {}).get(ec, 0.0) for ec in entity_codes)
            consl_kpi = round(num_agg / abs(to_agg) * 100, 2) if abs(to_agg) > 1e-6 else 0.0
            entity_kpi = {
                ec: round(line_entity.get(ref_code, {}).get(ec, 0.0) / abs(entity_to.get(ec, 0.0)) * 100, 2)
                if abs(entity_to.get(ec, 0.0)) > 1e-6 else 0.0
                for ec in entity_codes
            }
            rows_out.append({
                "id": f"pl-{rc}", "label": r["balance_title"],
                "row_kind": "kpi", "is_bold": bool(r.get("is_bold", False)),
                "entity_amounts": entity_kpi,
                "aggregated": 0.0, "ic_eliminations": 0.0,
                "consolidation": consl_kpi,
                "has_children": False, "children": [],
            })
            continue

        if rt == "title":
            rows_out.append({
                "id": f"pl-{rc}", "label": r["balance_title"],
                "row_kind": "title", "is_bold": bool(r.get("is_bold", False)),
                "entity_amounts": {ec: 0.0 for ec in entity_codes},
                "aggregated": 0.0, "ic_eliminations": 0.0, "consolidation": 0.0,
                "has_children": False, "children": [],
            })
            continue

        am = line_entity.get(rc, {ec: 0.0 for ec in entity_codes})

        # L4 children
        children: list[dict] = []
        if rt == "mapping":
            l3_val = (r.get("level_3") or "").strip()
            l4_val = (r.get("level_4") or "").strip()
            gid_val = (r.get("gl_account_id") or "").strip()
            if l3_val and not l4_val and not gid_val:
                matched = [g for g in grains if _match_grain(g, r)]
                l4_dict: dict[str, list[dict]] = {}
                for g in matched:
                    l4v = (g.get("level_4") or "").strip()
                    if l4v:
                        l4_dict.setdefault(l4v, []).append(g)
                for l4v, l4g in sorted(l4_dict.items()):
                    l4_am = _consl_amounts(l4g)
                    children.append(_consl_row(
                        f"pl-{rc}-l4-{abs(hash(l4v)) % 100000}", l4v, "detail", False, l4_am,
                    ))
                if len(children) == 1 and children[0]["label"] == r["balance_title"]:
                    children = []

        row_kind = _row_kind_for(rt)
        rows_out.append(_consl_row(
            f"pl-{rc}", r["balance_title"], row_kind,
            bool(r.get("is_bold", False)), am,
            has_children=len(children) > 0, children=children,
        ))

    col_label = labels.get("cm", period_label(yr, mo))
    out: dict[str, Any] = {
        "statement": "pl",
        "period_grain": period_grain,
        "year": yr, "month": mo,
        "col_label": col_label,
        "entities": entity_dicts,
        "rows": rows_out,
    }
    if iso_year is not None:
        out["iso_year"] = iso_year
    if iso_week is not None:
        out["iso_week"] = iso_week
    return out


# ---------------------------------------------------------------------------
# Annual consolidation (entity breakdown) builder
# ---------------------------------------------------------------------------
# Per-legal-entity YTD columns for the anchor fiscal year (through ``month``),
# plus aggregated / consolidation totals.  Produces the legacy ConsolidationResponse
# shape used by GET /api/v1/exit-readiness/pl-consolidation.
#
# === FORMULA ===
#   mapping line, entity E:
#     entity_amounts[E] = Σ presented_ytd(g) over grain rows g matching the line's
#                         level filter with g.entity_prefix → E
#                         (presented_ytd = Σ amount*-1 over year, period 1..month).
#   subtotal/calc/grandtotal: running cumulative sum per entity column.
#   aggregated / consolidation = Σ_E entity_amounts[E]; ic_eliminations = 0.

# Value-bearing P&L row types emitted in the flat consolidation breakdown.
_CONSL_VALUE_ROW_TYPES = ("mapping", "subtotal", "calc", "grandtotal", "computed")


def build_pl_annual_consolidation(
    session: Session,
    *,
    year: int,
    month: int,
) -> dict[str, Any]:
    """Annual entity breakdown (Consolidation) for YTD of the anchor fiscal year.

    Returns the legacy ConsolidationResponse shape consumed by the annual
    income-statement view (GET /api/v1/exit-readiness/pl-consolidation):

        {"statement": "pl", "year", "month",
         "col_label": "YTDJul25A",              # YTD through anchor month
         "entities": [{"code", "label"}, ...],
         "rows": [...]}

    KPI rows render as margins % of total output per entity column.
    """
    sql, params = pl_consl_grain_sql_annual_ytd(year, month)
    grains = [dict(r._mapping) for r in session.execute(text(sql), params).fetchall()]

    ent_rows = session.execute(text(
        "SELECT legal_entity_code, entity_prefix, entity_name FROM dim_legal_entity "
        "ORDER BY legal_entity_code"
    )).fetchall()

    struct = _load_structure(session)
    if not struct:
        raise ValueError("dim_pl_structure is empty — run setup or load PL Structure")

    out = _build_annual_consolidation_rows(struct, grains, ent_rows, year, month)
    out["col_label"] = col_labels_annual(year, month).get("ytd", f"YTD{str(year)[-2:]}A")
    return out


def _build_annual_consolidation_rows(
    struct: list[Any],
    grains: list[dict],
    ent_rows: list[Any],
    year: int,
    month: int,
) -> dict[str, Any]:
    """Pure builder for the annual consolidation breakdown (DB-free, testable).

    ``ent_rows`` : iterable of (legal_entity_code, entity_prefix, entity_name)
                   tuples/Rows (as returned by the dim_legal_entity query).
    ``grains``   : grain dicts with level_2/3/4, account_number_group,
                   entity_prefix, ``ytd`` (already PRESENTED — income +, expense −).
    """
    # Stable entity order: sort by legal_entity_code.
    ent_meta = sorted(
        ((r[0], r[1], r[2]) for r in ent_rows),
        key=lambda t: str(t[0]),
    )
    entity_codes = [code for code, _ep, _name in ent_meta]
    ep_to_code = {ep: code for code, ep, _name in ent_meta}  # entity_prefix → code
    entity_dicts = [{"code": code, "label": name} for code, _ep, name in ent_meta]

    # P&L rows only — drop BS rows BEFORE the running sum so they never inflate
    # a subtotal.
    struct_pl = [r for r in (_row_dict(row) for row in struct) if _is_pl_structure_row(r)]

    def _consl_ytd(matched_grains: list[dict]) -> dict[str, float]:
        """Sum the ``ytd`` column per entity_prefix; key by legal_entity_code."""
        sums: dict[str, float] = {ec: 0.0 for ec in entity_codes}
        for g in matched_grains:
            ep = g.get("entity_prefix") or ""
            lec = ep_to_code.get(ep)
            if lec is None:
                continue
            sums[lec] = sums.get(lec, 0.0) + float(g.get("ytd") or 0)
        return sums

    # Per-entity mapping values, then running-sum per entity column.
    mapping_entity: dict[str, dict[str, float]] = {}
    for r in struct_pl:
        if r.get("row_type") != "mapping":
            continue
        code = r["line_code"]
        matched = [g for g in grains if _match_grain(g, r)]
        mapping_entity[code] = _consl_ytd(matched)

    running_entity = _compute_running_values(struct_pl, mapping_entity, entity_codes)
    line_entity: dict[str, dict[str, float]] = {
        code: {ec: float(v.get(ec) or 0.0) for ec in entity_codes}
        for code, v in running_entity.items()
    }

    # KPI denominators: per-entity TOTAL_OUTPUT and the aggregated Σ over entities.
    entity_to = line_entity.get("TOTAL_OUTPUT", {ec: 0.0 for ec in entity_codes})
    to_agg = sum(entity_to.get(ec, 0.0) for ec in entity_codes)

    rows_out: list[dict[str, Any]] = []
    kpi_header_appended = False
    for r in struct_pl:
        rt = r.get("row_type", "mapping")
        rc = r["line_code"]

        if rt == "kpi":
            # KPI rows are margins ``% of total output``, computed independently
            # per entity column and once at group level:
            #   entity_amounts[E] = line(kpi_ref)[E] / |TOTAL_OUTPUT[E]| * 100
            #   aggregated = consolidation = Σ_E line(kpi_ref)[E]
            #                                / |Σ_E TOTAL_OUTPUT[E]| * 100
            # (0 when the denominator is ~0). ic_eliminations = 0.
            if not kpi_header_appended:
                rows_out.append({
                    "id": "pl-kpi-header",
                    "label": "KPIs — as % of total output",
                    "row_kind": "kpi_header", "is_bold": False,
                    "entity_amounts": {ec: 0.0 for ec in entity_codes},
                    "aggregated": 0.0, "ic_eliminations": 0.0, "consolidation": 0.0,
                    "has_children": False, "children": [],
                })
                kpi_header_appended = True
            kpi_ref = r.get("kpi_code") or rc
            ref_code = _resolve_kpi_line_code(str(kpi_ref), "NET_PROFIT")
            ref_am = line_entity.get(ref_code, {ec: 0.0 for ec in entity_codes})
            entity_kpi = {
                ec: round(ref_am.get(ec, 0.0) / abs(entity_to.get(ec, 0.0)) * 100, 2)
                if abs(entity_to.get(ec, 0.0)) > 1e-6 else 0.0
                for ec in entity_codes
            }
            num_agg = sum(ref_am.get(ec, 0.0) for ec in entity_codes)
            kpi_val = round(num_agg / abs(to_agg) * 100, 2) if abs(to_agg) > 1e-6 else 0.0
            rows_out.append({
                "id": f"pl-{rc}",
                "label": r["balance_title"],
                "row_kind": "kpi", "is_bold": bool(r.get("is_bold", False)),
                "entity_amounts": entity_kpi,
                "aggregated": kpi_val, "ic_eliminations": 0.0,
                "consolidation": kpi_val,
                "has_children": False, "children": [],
            })
            continue

        if rt not in _CONSL_VALUE_ROW_TYPES:
            continue  # skip presentation-only 'title' rows (flat table)
        am = line_entity.get(rc, {ec: 0.0 for ec in entity_codes})
        agg = sum(am.get(ec, 0.0) for ec in entity_codes)
        rows_out.append({
            "id": f"pl-{rc}",
            "label": r["balance_title"],
            "row_kind": "line" if rt == "mapping" else "subtotal",
            "is_bold": bool(r.get("is_bold", False)),
            "entity_amounts": {ec: round(am.get(ec, 0.0), 2) for ec in entity_codes},
            "aggregated": round(agg, 2),
            "ic_eliminations": 0.0,
            "consolidation": round(agg, 2),
            "has_children": False,
            "children": [],
        })

    return {
        "statement": "pl",
        "year": year,
        "month": month,
        "col_label": col_labels_annual(year, month).get("ytd", f"YTD{str(year)[-2:]}A"),
        "entities": entity_dicts,
        "rows": rows_out,
    }


# ---------------------------------------------------------------------------
# Monthly view builder
# ---------------------------------------------------------------------------

def build_pl_monthly(
    session: Session,
    *,
    period_grain: str = "month",
    year: Optional[int] = None,
    month: Optional[int] = None,
    iso_year: Optional[int] = None,
    iso_week: Optional[int] = None,
    entity: Optional[str] = None,
    span: str = "12m",
) -> dict[str, Any]:
    """Monthly sparkline view per P&L line.

    span='12m' (default): last 12 months ending at the anchor — UNCHANGED legacy
        response (keys: statement, year, month, periods[], rows[]).
    span='fy3' (month grain only): ALL months from (year-2)-01 to (year)-month
        PLUS three summary columns (totals[]) — FY(year-2), FY(year-1), YTD(year).
        rows[].amounts then carry BOTH the monthly period keys (YYYY-MM) and the
        three total keys ('FY2023','FY2024','YTD2025'). Running-sum subtotals and
        KPI rows (% of total output) cover the total keys too.

    === KPI total column ===
        kpi_total = line(kpi_ref)[total_key] / |TOTAL_OUTPUT[total_key]| * 100.

    === WORKED EXAMPLE (year=2025, month=7, span='fy3') ===
        31 month columns (Jan2023..Jul2025) + totals FY2023/FY2024/YTD2025.
        FY2024 TOTAL_OUTPUT = Σ months 2024-01..2024-12 of TOTAL_OUTPUT.
        YTD2025 = Σ months 2025-01..2025-07.

    === EDGE CASES ===
        * week grain ignores span (always last 3 months, no totals).
        * span='fy3' with month==12 → anchor year contributes all 12 months.
        * total denominator ~0 → KPI total column 0.
    """
    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)

    totals: list[dict[str, Any]] = []
    effective_span = "12m"

    if period_grain == "week":
        assert iso_year is not None and iso_week is not None
        yr, mo = plan_anchor_for_week(iso_year, iso_week)
        # For week grain: show last 3 months ending at anchor (span ignored).
        periods: list[tuple[int, int]] = []
        y, m = yr, mo
        for _ in range(3):
            periods.insert(0, (y, m))
            m -= 1
            if m < 1:
                m = 12
                y -= 1
    else:
        assert year is not None and month is not None
        yr, mo = year, month
        if span == "fy3":
            effective_span = "fy3"
            periods = _fy_span_periods(year, month)
            totals = _fy_span_totals(year, month)
        else:
            periods = _last_12_periods(year, month)

    period_keys = [period_key(y, m) for y, m in periods]
    total_keys = [t["key"] for t in totals]
    all_keys = period_keys + total_keys
    zero = {k: 0.0 for k in all_keys}

    sql, params = pl_monthly_grain_sql(yr, mo, ent_frag, span=effective_span)
    grains = [dict(r._mapping) for r in session.execute(text(sql), params).fetchall()]

    struct = _load_structure(session)
    if not struct:
        raise ValueError("dim_pl_structure is empty")
    struct_pl = [r for r in (_row_dict(r) for r in struct) if _is_pl_structure_row(r)]

    mapping_monthly: dict[str, dict[str, float]] = {}
    for r in struct_pl:
        if r.get("row_type") != "mapping":
            continue
        code = r["line_code"]
        matched = [g for g in grains if _match_grain(g, r)]
        am: dict[str, float] = {k: 0.0 for k in all_keys}
        for g in matched:
            for k in all_keys:
                am[k] = am.get(k, 0.0) + float(g.get(k) or 0)
        mapping_monthly[code] = am

    running_monthly = _compute_running_values(struct_pl, mapping_monthly, all_keys)
    line_monthly: dict[str, dict[str, float]] = {
        code: {k: float(v.get(k) or 0.0) for k in all_keys}
        for code, v in running_monthly.items()
    }

    total_output = line_monthly.get("TOTAL_OUTPUT", zero)

    def _monthly_row(rid, label, row_kind, is_bold, am, has_children=False, children=None):
        return {
            "id": rid, "label": label, "row_kind": row_kind,
            "is_bold": is_bold,
            "amounts": {k: round(am.get(k, 0.0), 2) for k in all_keys},
            "has_children": has_children,
            "children": children or [],
        }

    rows_out: list[dict[str, Any]] = []
    kpi_header_appended = False
    for r in struct_pl:
        rt = r.get("row_type", "mapping")
        rc = r["line_code"]

        if rt == "title":
            rows_out.append(_monthly_row(f"pl-{rc}", r["balance_title"], "title",
                                         bool(r.get("is_bold", False)), dict(zero)))
            continue

        if rt == "kpi":
            if not kpi_header_appended:
                rows_out.append(_monthly_row("pl-kpi-header", "KPIs — as % of total output",
                                             "kpi_header", False, dict(zero)))
                kpi_header_appended = True
            kpi_ref = r.get("kpi_code") or rc
            ref_code = _resolve_kpi_line_code(str(kpi_ref), str(rc or "NET_PROFIT"))
            ref_am = line_monthly.get(ref_code, zero)
            kpi_am = {
                k: round(ref_am.get(k, 0) / abs(total_output.get(k, 0)) * 100, 2)
                if abs(total_output.get(k, 0)) > 1e-6 else 0.0
                for k in all_keys
            }
            rows_out.append(_monthly_row(f"pl-{rc}", r["balance_title"], "kpi",
                                         bool(r.get("is_bold", False)), kpi_am))
            continue

        am = line_monthly.get(rc, dict(zero))

        # L4 children
        children: list[dict] = []
        if rt == "mapping":
            l3_val = (r.get("level_3") or "").strip()
            l4_val = (r.get("level_4") or "").strip()
            gid_val = (r.get("gl_account_id") or "").strip()
            if l3_val and not l4_val and not gid_val:
                matched = [g for g in grains if _match_grain(g, r)]
                l4_dict: dict[str, dict[str, float]] = {}
                for g in matched:
                    l4v = (g.get("level_4") or "").strip()
                    if l4v:
                        if l4v not in l4_dict:
                            l4_dict[l4v] = {k: 0.0 for k in all_keys}
                        for k in all_keys:
                            l4_dict[l4v][k] = l4_dict[l4v].get(k, 0.0) + float(g.get(k) or 0)
                for l4v, l4_am in sorted(l4_dict.items()):
                    children.append(_monthly_row(
                        f"pl-{rc}-l4-{abs(hash(l4v)) % 100000}", l4v, "line", False, l4_am,
                    ))
                if len(children) == 1 and children[0]["label"] == r["balance_title"]:
                    children = []

        row_kind = _row_kind_for(rt)
        rows_out.append(_monthly_row(
            f"pl-{rc}", r["balance_title"], row_kind, bool(r.get("is_bold", False)), am,
            has_children=len(children) > 0, children=children,
        ))

    out: dict[str, Any] = {
        "statement": "pl",
        "year": yr, "month": mo,
        "periods": [{"year": y, "month": m, "label": period_label(y, m)} for y, m in periods],
        "rows": rows_out,
    }
    if totals:
        out["totals"] = [
            {"key": t["key"], "label": t["label"], "kind": t["kind"], "year": t["year"]}
            for t in totals
        ]
    return out


# ---------------------------------------------------------------------------
# Weekly-breakdown view builder
# ---------------------------------------------------------------------------

def build_pl_weekly_breakdown(
    session: Session,
    *,
    iso_year: int,
    iso_week: int,
    entity: Optional[str] = None,
) -> dict[str, Any]:
    """Weekly P&L breakdown: M-2 / M-1 full months + M0 partial, by ISO week.

    The anchor ISO week's Sunday sets M0 (the in-progress calendar month).  The
    view groups, left→right: M-2 (full), M-1 (full), M0 (partial).  Each full
    month group shows one column per ISO week whose Sunday is in that month plus
    a month-total column (Σ that calendar month).  M0 shows the weeks up to and
    including the anchor week plus an MTD total (Σ from the 1st of M0 to the
    anchor Sunday).

    === RESPONSE SHAPE ===
        {statement:"pl", iso_year, iso_week,
         groups:[{month_key, month_label, kind('full'|'partial'),
                  weeks:[{key,label}],
                  total:{key,label,kind('month'|'mtd')}}],
         rows:[{id,label,row_kind,is_bold,
                amounts:{<every week key AND total key>: float},
                has_children, children:[...]}]}

    Running-sum subtotals span ALL column keys (week + total).  KPI rows (% of
    total output, per column).  Balance-sheet rows excluded; L4 children mirror
    :func:`build_pl_monthly`.

    === FORMULAS (presented amounts, sign applied once in SQL) ===
        week column[w]   = Σ posting_date ∈ [Monday(w) .. Sunday(w)]
        full-month total = Σ fiscal_period == month of fiscal_year
        MTD total        = Σ posting_date ∈ [1st of M0 .. anchor Sunday]
        subtotal[k]      = running Σ of preceding mapping lines (per key k)
        kpi[k]           = line(kpi_ref)[k] / |TOTAL_OUTPUT[k]| * 100 (0 if ~0)

    === WORKED EXAMPLE ===
        NET_SALES week W2025-28 = 100, FINISHED_GOODS_WIP W2025-28 = 20,
        OWN_WORK_CAPITALISED 0 → TOTAL_OUTPUT W2025-28 = 120.
        COST_OF_MATERIALS W2025-28 = -40 → GROSS_PROFIT W2025-28 = 80.

    === EDGE CASES ===
        * A month with no ISO-week Sundays inside it → empty weeks list, total
          column still present.
        * Denominator |TOTAL_OUTPUT[k]| ~0 → KPI column 0.
        * fiscal_period 13 (consolidation) excluded in the SQL.
    """
    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)

    layout = weekly_breakdown_layout(iso_year, iso_week)
    all_keys: list[str] = []
    for g in layout:
        all_keys.extend(w["key"] for w in g["weeks"])
        all_keys.append(g["total"]["key"])
    zero = {k: 0.0 for k in all_keys}

    sql, params = pl_weekly_breakdown_sql(layout, ent_frag)
    grains = [dict(r._mapping) for r in session.execute(text(sql), params).fetchall()]

    struct = _load_structure(session)
    if not struct:
        raise ValueError("dim_pl_structure is empty")
    struct_pl = [r for r in (_row_dict(r) for r in struct) if _is_pl_structure_row(r)]

    mapping_weekly: dict[str, dict[str, float]] = {}
    for r in struct_pl:
        if r.get("row_type") != "mapping":
            continue
        code = r["line_code"]
        matched = [g for g in grains if _match_grain(g, r)]
        am: dict[str, float] = {k: 0.0 for k in all_keys}
        for g in matched:
            for k in all_keys:
                am[k] = am.get(k, 0.0) + float(g.get(k) or 0)
        mapping_weekly[code] = am

    running_weekly = _compute_running_values(struct_pl, mapping_weekly, all_keys)
    line_weekly: dict[str, dict[str, float]] = {
        code: {k: float(v.get(k) or 0.0) for k in all_keys}
        for code, v in running_weekly.items()
    }

    total_output = line_weekly.get("TOTAL_OUTPUT", zero)

    def _wk_row(rid, label, row_kind, is_bold, am, has_children=False, children=None):
        return {
            "id": rid, "label": label, "row_kind": row_kind, "is_bold": is_bold,
            "amounts": {k: round(am.get(k, 0.0), 2) for k in all_keys},
            "has_children": has_children, "children": children or [],
        }

    rows_out: list[dict[str, Any]] = []
    kpi_header_appended = False
    for r in struct_pl:
        rt = r.get("row_type", "mapping")
        rc = r["line_code"]

        if rt == "title":
            rows_out.append(_wk_row(f"pl-{rc}", r["balance_title"], "title",
                                    bool(r.get("is_bold", False)), dict(zero)))
            continue

        if rt == "kpi":
            if not kpi_header_appended:
                rows_out.append(_wk_row("pl-kpi-header", "KPIs — as % of total output",
                                        "kpi_header", False, dict(zero)))
                kpi_header_appended = True
            kpi_ref = r.get("kpi_code") or rc
            ref_code = _resolve_kpi_line_code(str(kpi_ref), str(rc or "NET_PROFIT"))
            ref_am = line_weekly.get(ref_code, zero)
            kpi_am = {
                k: round(ref_am.get(k, 0) / abs(total_output.get(k, 0)) * 100, 2)
                if abs(total_output.get(k, 0)) > 1e-6 else 0.0
                for k in all_keys
            }
            rows_out.append(_wk_row(f"pl-{rc}", r["balance_title"], "kpi",
                                    bool(r.get("is_bold", False)), kpi_am))
            continue

        am = line_weekly.get(rc, dict(zero))

        # L4 children (mirrors build_pl_monthly).
        children: list[dict] = []
        if rt == "mapping":
            l3_val = (r.get("level_3") or "").strip()
            l4_val = (r.get("level_4") or "").strip()
            gid_val = (r.get("gl_account_id") or "").strip()
            if l3_val and not l4_val and not gid_val:
                matched = [g for g in grains if _match_grain(g, r)]
                l4_dict: dict[str, dict[str, float]] = {}
                for g in matched:
                    l4v = (g.get("level_4") or "").strip()
                    if l4v:
                        if l4v not in l4_dict:
                            l4_dict[l4v] = {k: 0.0 for k in all_keys}
                        for k in all_keys:
                            l4_dict[l4v][k] = l4_dict[l4v].get(k, 0.0) + float(g.get(k) or 0)
                for l4v, l4_am in sorted(l4_dict.items()):
                    children.append(_wk_row(
                        f"pl-{rc}-l4-{abs(hash(l4v)) % 100000}", l4v, "line", False, l4_am,
                    ))
                if len(children) == 1 and children[0]["label"] == r["balance_title"]:
                    children = []

        rows_out.append(_wk_row(
            f"pl-{rc}", r["balance_title"], _row_kind_for(rt),
            bool(r.get("is_bold", False)), am,
            has_children=len(children) > 0, children=children,
        ))

    groups_resp = [
        {
            "month_key": g["month_key"],
            "month_label": g["month_label"],
            "kind": g["kind"],
            "weeks": [{"key": w["key"], "label": w["label"]} for w in g["weeks"]],
            "total": {"key": g["total"]["key"], "label": g["total"]["label"],
                      "kind": g["total"]["kind"]},
        }
        for g in layout
    ]

    return {
        "statement": "pl",
        "iso_year": iso_year,
        "iso_week": iso_week,
        "groups": groups_resp,
        "rows": rows_out,
    }
