"""Balance-Sheet statement builders for the legacy compat layer.

Companion to ``fin_compat_pl`` (P&L).  Turns the cumulative-balance grain rows
produced by ``fin_compat_bs_sql`` into the legacy response shapes consumed by the
verbatim-ported frontend (``StatementsPage`` with ``statement='bs'``):

  * build_bs_statement_compat   → FinancialStatementResponse (month|week)
  * build_bs_consolidation      → ConsolidationResponse
  * build_bs_monthly            → MonthlyResponse (one column per month-end)
  * build_bs_line_detail        → PlLineDetailResponse (cumulative balances)
  * build_bs_narrative          → PlNarrativeResponse (deterministic)
  * build_bs_l4_trend           → L4TrendResponse (cumulative balance trend)
  * build_bs_snapshot_annual    → ErSnapshotResponse (fy_py/fy/cm_py/cm)
  * build_bs_provision_rollforward → ProvisionRollforwardResponse

=== BS SEMANTICS — DIFFERENT FROM P&L (authoritative: app/services/balance_sheet.py) ===

1. CUMULATIVE STOCK, not period flow.  A column value is the CLOSING BALANCE at
   the column's cutoff date (Σ of every movement with posting_date <= cutoff).
   This is already done in the SQL (``SUM(l.amount)`` with a posting_date cutoff,
   NO ``* -1``); the builder never re-derives it.

2. RAW STORED SIGN + DISPLAY FLIP (NOT the P&L ``* -1``).  Grain values arrive at
   the raw stored sign: assets (debit-normal) are POSITIVE, equity & liabilities
   (credit-normal) are NEGATIVE.  The BS sign convention presents every line as a
   positive magnitude in its own section by NEGATING the credit side ONCE here
   (``_flip_row_tree`` on rows whose section is 'credit').  The section comes from
   the structure row's ``kpi_code`` ('BS:asset' | 'BS:credit', seeded by
   scripts/seed_bs_structure.py).  This is the documented place the BS sign is
   applied — deliberately different from the P&L rule.

3. SUBTOTAL / GRANDTOTAL = running sum of the mapping lines (like P&L), but
   SECTION-AWARE so the asset side and the credit side never mix (mirrors the
   authoritative ``aggregate_bs``):
     * 'mapping'     → Σ matched grain balances; accumulates into BOTH a per-section
                       running sum (reset by the next subtotal) and a per-section
                       cumulative sum (never reset).
     * 'subtotal'    → snapshot of its section's running sum, then RESET that
                       running sum (so "Total <L2>" = Σ that L2's mapping lines).
     * 'grandtotal'  → snapshot of its section's CUMULATIVE sum ("Total assets" =
                       Σ all asset mapping lines; "Total equity & liabilities" =
                       Σ all credit mapping lines).
   All sums are on the RAW signed balances; the display flip happens afterwards.

4. NET PROFIT injection (statement + monthly + snapshot): the P&L YTD result at
   each balance date (presented income +, from ``bs_net_profit_sql_*`` /
   ``bs_monthly_net_profit_sql``) is added into the equity section as a virtual
   "Net profit" child row AND added to the equity subtotal's presented amounts and
   every ancestor subtotal up to "Equity & liabilities" (mirrors legacy
   ``_inject_net_profit``).  Injected AFTER the display flip, in its natural
   presented (positive-for-profit) sign.

5. EQUITY-RATIO KPI (row_kind 'kpi', %): |equity| / |total assets| * 100 per column,
   computed from the RAW section balances (excludes the injected net profit — same
   as legacy).  Returns 0 when the asset base is ~0.

=== SHARED P&L HELPERS ===
Reuses fin_compat_pl ``_match_grain`` (level-based matching, identical grain shape),
``_round_am``, ``_deltas`` (mom=cm-pm, yoy=cm-py_cm, ytd=ytd-ytd_py), ``_sort_key_cm``
and ``_row_dict``; reuses fin_compat_sql ``col_labels_month/week`` and ISO-week math.

=== WORKED EXAMPLE (month grain, raw → display) ===
  Structure (sort_order >= 1000):
    AR        mapping  section=asset   level_3='Trade receivables'
    BS_TOTAL_CURRENT_ASSETS subtotal section=asset
    BS_GRANDTOTAL_ASSETS    grandtotal section=asset
    AP        mapping  section=credit  level_3='Trade payables'
    EQUITY    mapping  section=credit  level_3='Retained earnings'
    BS_GRANDTOTAL_EQ_LIAB   grandtotal section=credit
  Raw cumulative balances (cm column): AR=+500, AP=-300, EQUITY=-1100.
    Total current assets = +500 ; Total assets = +500 ;
    Total equity & liabilities = -300 + -1100 = -1400 (raw).
  Display flip (credit negated): AP→+300, EQUITY→+1100, Total E&L→+1400.
  Net profit cm=+200 (P&L YTD, presented) → injected into equity: equity row
    becomes +1100 + 200 = +1300, with a "Net profit" child = +200; Total E&L
    becomes +1400 + 200 = +1600.
  Equity ratio (raw, pre-injection) = |(-1100)| / |500| * 100 = 220.0%
    (toy numbers; on a real, balanced GL it lands in a sane band).

=== EDGE CASES ===
  * No movements up to a cutoff → 0 balance (COALESCE in SQL).
  * Asset base ~0 → equity-ratio KPI = 0 (no div-by-zero).
  * No equity section in the structure → net profit not injected (clean fallback).
  * Credit balance that is naturally positive (e.g. a debit-side contra) keeps its
    sign through the flip; no clamping.
  * week grain carries an extra ``mtd`` column (== cm for a stock).
"""
from __future__ import annotations

import os
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.fin_compat_pl import (
    _deltas,
    _load_plan_map,
    _match_grain,
    _round_am,
    _row_amounts,
    _row_dict,
    _sort_key_cm,
    _statement_structure_rows,
    attach_hierarchy_plan_to_rows,
    load_position_plan_map,
    load_position_plan_map_pref,
)

_BS_HIER_KEYS = ["level_1", "level_2", "level_3", "level_4"]
_BS_SORT_MAP = {
    "level_1": "level_1_sort",
    "level_2": "level_2_sort",
    "level_3": "level_3_sort",
    "level_4": "level_4_sort",
}
_BS_GL_FILTER = ["level_2", "level_3", "level_4"]

from app.services.fin_compat_hierarchy import (
    apply_bs_display_signs,
    apply_bs_monthly_display_signs,
    consolidation_hierarchy_from_grains,
    equity_ratio_monthly_row,
    equity_ratio_row_from_grains,
    hierarchy_from_grains,
    monthly_hierarchy,
    _sum_amounts,
)
from app.services.fin_compat_sql import (
    _fy_span_periods,
    _fy_span_totals,
    _last_12_periods,
    _trend_windows,
    col_labels_annual,
    col_labels_month,
    col_labels_week,
    label_forecast_fy,
    entity_sql_fragment,
    label_actual,
    period_key,
    period_label,
    plan_anchor_for_week,
    resolve_entity_prefix,
)
from app.services.fin_compat_bs_sql import (
    _BS_KEYS_MONTH,
    _BS_SNAP_KEYS,
    bs_consl_grain_sql_annual,
    bs_consl_grain_sql_month,
    bs_consl_grain_sql_week,
    bs_consl_net_profit_sql_annual,
    bs_consl_net_profit_sql_month,
    bs_grain_sql_month,
    bs_grain_sql_week,
    bs_l4_trend_sql,
    bs_monthly_grain_sql,
    bs_monthly_net_profit_sql,
    bs_net_profit_sql_month,
    bs_net_profit_sql_week,
    bs_provision_grain_sql,
    bs_snapshot_grain_sql,
    bs_snapshot_net_profit_sql,
)

# Standard balance-sheet column keys (same schema as the P&L statement).
_AM_KEYS = list(_BS_KEYS_MONTH)  # py_cm, pm, cm, ytd, ytd_py

# Fixed BS line-code aliases (seed_bs_structure) for robust BS-row detection.
_BS_ALIAS_CODES = {"AR", "INVENTORY", "AP", "CASH", "EQUITY"}


# ---------------------------------------------------------------------------
# Structure helpers (BS rows live in dim_pl_structure, sort_order >= 1000)
# ---------------------------------------------------------------------------

def _load_structure(session: Session) -> list[Any]:
    """Load dim_bs_structure rows (ordered by sort_order). Mirrors fin_compat_pl."""
    return session.execute(text(
        "SELECT pl_line_id, sort_order, line_code, row_type, balance_title, details, "
        "calc_type, level_2, level_3, level_4, gl_account_id, invert_delta, is_bold, kpi_code "
        "FROM dim_bs_structure ORDER BY sort_order"
    )).fetchall()


def _is_bs_structure_row(r: dict[str, Any]) -> bool:
    """True for balance-sheet rows only (P&L rows excluded).

    A row is BS iff ANY robust marker says so: ``sort_order >= 1000`` (the BS sort
    band) OR ``kpi_code`` starts with 'BS:' OR ``line_code`` starts with 'BS_' OR
    it is one of the fixed BS aliases (AR/INVENTORY/AP/CASH/EQUITY).  Using several
    markers keeps the split correct even if one drifts in a future structure load.
    """
    code = str(r.get("line_code") or "")
    kc = str(r.get("kpi_code") or "")
    try:
        so = int(r.get("sort_order") or 0)
    except (TypeError, ValueError):
        so = 0
    return (
        so >= 1000
        or kc.startswith("BS:")
        or code.startswith("BS_")
        or code in _BS_ALIAS_CODES
    )


def _bs_section(r: dict[str, Any]) -> str:
    """Sign section for a BS row: 'asset' (keep sign) or 'credit' (flip for display).

    Primary source is ``kpi_code`` = 'BS:asset' | 'BS:credit' (seed_bs_structure).
    Falls back to label / level keywords so an unconfigured row never silently
    flips: anything that looks like equity / liabilities / payables / provisions
    is 'credit'; everything else defaults to 'asset' (no flip).
    """
    kc = str(r.get("kpi_code") or "")
    if kc.startswith("BS:"):
        sec = kc.split(":", 1)[1].strip().lower()
        return "credit" if sec in ("credit", "liability", "equity") else "asset"
    hay = " ".join(str(r.get(k) or "") for k in ("balance_title", "level_1", "level_2")).lower()
    if "asset" in hay:
        return "asset"
    if any(w in hay for w in ("equit", "eigenkapital", "liab", "payable", "provision")):
        return "credit"
    return "asset"


def _row_kind_for_bs(row_type: str) -> str:
    """GDPdU row_type → legacy frontend row_kind (BS variant).

    mapping → 'line'; subtotal/grandtotal/calc/computed → 'subtotal'; kpi/title
    pass through.
    """
    if row_type == "mapping":
        return "line"
    if row_type in ("subtotal", "grandtotal", "calc", "computed"):
        return "subtotal"
    return row_type


# ---------------------------------------------------------------------------
# Section-aware running sum over the RAW signed balances
# ---------------------------------------------------------------------------

def _compute_bs_running_values(
    struct_bs: list[dict[str, Any]],
    mapping_vals: dict[str, dict[str, float]],
    keys: list[str],
) -> dict[str, dict[str, float]]:
    """Running-sum semantics over the ordered BS structure (mirrors aggregate_bs).

    ``struct_bs``    : BS structure rows (dicts), sorted by sort_order.
    ``mapping_vals`` : {line_code: {col: raw_balance}} for 'mapping' rows.
    ``keys``         : value columns to accumulate (py_cm/pm/cm/... or entity codes).

    Returns {line_code: {col: raw_value}}:
      * 'mapping'    → its own balances; added to its section's running + cumulative.
      * 'subtotal'   → snapshot of its section's running sum, which is then RESET.
      * 'grandtotal' → snapshot of its section's CUMULATIVE sum (never reset).
      * anything else → snapshot of its section's running sum (no reset).
    All on RAW signs (assets +, credit −); the display flip happens later.
    """
    sections = {_bs_section(r) for r in struct_bs} | {"asset", "credit"}
    run: dict[str, dict[str, float]] = {s: {k: 0.0 for k in keys} for s in sections}
    cum: dict[str, dict[str, float]] = {s: {k: 0.0 for k in keys} for s in sections}
    out: dict[str, dict[str, float]] = {}

    for r in struct_bs:
        code = r["line_code"]
        sec = _bs_section(r)
        rt = r.get("row_type", "mapping")
        if rt == "mapping":
            am = mapping_vals.get(code) or {}
            vals = {k: float(am.get(k, 0.0) or 0.0) for k in keys}
            for k in keys:
                run[sec][k] += vals[k]
                cum[sec][k] += vals[k]
            out[code] = vals
        elif rt == "subtotal":
            out[code] = dict(run[sec])
            run[sec] = {k: 0.0 for k in keys}  # reset for the next L2 group
        elif rt == "grandtotal":
            out[code] = dict(cum[sec])
        else:
            out[code] = dict(run[sec])
    return out


# ---------------------------------------------------------------------------
# Display sign flip (credit side → presented positive) + net-profit injection
# ---------------------------------------------------------------------------

def _flip_row_tree(row: dict[str, Any]) -> None:
    """Negate amounts + deltas of a row and all its children/accounts (in place).

    Applied ONLY to credit-section rows so equity & liabilities present positive.
    The single, documented BS display-sign application — never the P&L ``* -1``.
    """
    for d in (row.get("amounts"), row.get("deltas")):
        if isinstance(d, dict):
            for k in list(d.keys()):
                d[k] = -d[k]
    for child in row.get("children") or []:
        _flip_row_tree(child)
    for acc in row.get("accounts") or []:
        _flip_row_tree(acc)


def _bump_row_amounts(
    row: dict[str, Any],
    bump: dict[str, float],
    *,
    deltas_fn,
    refresh_deltas: bool,
) -> None:
    am = row.get("amounts")
    if not isinstance(am, dict):
        return
    for k, v in bump.items():
        if k in am:
            am[k] = round(float(am[k]) + float(v), 2)
    if refresh_deltas:
        row["deltas"] = _round_am(deltas_fn(am, invert=bool(row.get("invert_delta", False))))


def _inject_net_profit(
    rows: list[dict[str, Any]],
    net_profit: dict[str, float],
    *,
    deltas_fn=None,
    ancestors: Optional[list[dict[str, Any]]] = None,
) -> bool:
    """Inject Net profit into equity and roll the same bump into ancestor subtotals."""
    if deltas_fn is None:
        deltas_fn = lambda am, invert=False: _deltas(am, invert)
    if ancestors is None:
        ancestors = []
    for row in rows:
        label = (row.get("label") or "").lower()
        if ("equit" in label or "eigenkapital" in label) and "liab" not in label:
            row.setdefault("children", [])
            row["has_children"] = True
            row["children"].append({
                "id": "bs-net-profit",
                "line_code": "NET_PROFIT",
                "row_kind": "line",
                "label": "Net profit",
                "amounts": _round_am(net_profit),
                "deltas": _round_am(deltas_fn(net_profit, invert=False)),
                "invert_delta": False,
                "is_bold": False,
                "drill": None,
                "has_children": False,
                "children": [],
            })
            _bump_row_amounts(row, net_profit, deltas_fn=deltas_fn, refresh_deltas=False)
            for anc in ancestors:
                _bump_row_amounts(anc, net_profit, deltas_fn=deltas_fn, refresh_deltas=True)
            return True
        if _inject_net_profit(
            row.get("children") or [],
            net_profit,
            deltas_fn=deltas_fn,
            ancestors=ancestors + [row],
        ):
            return True
    return False


def _find_assets_equity_codes(
    struct_bs: list[dict[str, Any]],
) -> tuple[Optional[str], Optional[str]]:
    """Return (total_assets grandtotal line_code, equity subtotal line_code).

    ``total_assets`` = first asset-side 'grandtotal'; ``equity`` = first 'subtotal'
    whose label looks like equity (and is not the combined "& liabilities" total).
    Either may be None when the structure lacks that row (clean KPI fallback).
    """
    assets_code: Optional[str] = None
    equity_code: Optional[str] = None
    for r in struct_bs:
        rt = r.get("row_type")
        label = (r.get("balance_title") or "").lower()
        if assets_code is None and rt == "grandtotal" and _bs_section(r) == "asset":
            assets_code = r["line_code"]
        if equity_code is None and rt == "subtotal" and (
            ("equit" in label or "eigenkapital" in label) and "liab" not in label
        ):
            equity_code = r["line_code"]
    return assets_code, equity_code


def _equity_ratio_row(
    struct_bs: list[dict[str, Any]],
    raw_vals: dict[str, dict[str, float]],
    keys: list[str],
) -> Optional[dict[str, Any]]:
    """Build the Equity-ratio KPI row from the RAW section balances.

        equity_ratio[col] = |equity_subtotal[col]| / |total_assets[col]| * 100
                            (0 when |total_assets| ~ 0).

    ``total_assets`` is the asset-side 'grandtotal' line; ``equity`` is the first
    'subtotal' whose label looks like equity.  Returns None if either is absent
    (clean fallback — no KPI row).  Excludes the injected net profit, matching the
    legacy KPI which is computed from the raw grains.
    """
    assets_code, equity_code = _find_assets_equity_codes(struct_bs)
    if not assets_code or not equity_code:
        return None
    assets = raw_vals.get(assets_code, {})
    equity = raw_vals.get(equity_code, {})

    def _er(num: float, den: float) -> float:
        d = abs(den)
        return round(abs(num) / d * 100, 2) if d > 1e-6 else 0.0

    er_am = {k: _er(equity.get(k, 0.0), assets.get(k, 0.0)) for k in _AM_KEYS}
    return {
        "id": "bs-kpi-equity-ratio",
        "line_code": "EQUITY_RATIO",
        "row_kind": "kpi",
        "label": "Equity ratio",
        "amounts": _round_am(er_am),
        "deltas": _round_am(_deltas(er_am, invert=False)),
        "invert_delta": False,
        "is_bold": False,
        "drill": None,
        "has_children": False,
        "children": [],
    }


# ---------------------------------------------------------------------------
# L4 children + account-level rows under a level-3 BS mapping row
# ---------------------------------------------------------------------------

def _bs_accounts_under_l4(
    grains: list[dict], l2: str, l3: str, l4: str, *, keys: list[str],
) -> list[dict]:
    """Account-level rows under one level-4 grouping (raw signs; statement BS)."""
    seen: dict[str, dict[str, float]] = {}
    meta: dict[str, tuple[str, str]] = {}
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
        am = {k: float(g.get(k) or 0.0) for k in keys}
        if sum(abs(v) for v in am.values()) < 1e-9:
            continue
        if ang not in seen:
            seen[ang] = {k: 0.0 for k in keys}
            meta[ang] = ((g.get("gl_account_id") or ang).strip(),
                         (g.get("account_name") or "").strip())
        for k in keys:
            seen[ang][k] += am[k]
    out: list[dict] = []
    for ang, am in seen.items():
        gid, aname = meta[ang]
        out.append({
            "id": f"bs-acc-{ang}",
            "line_code": gid,
            "label": f"{gid} | {aname}" if aname else gid,
            "row_kind": "account",
            "amounts": _round_am(am),
            "deltas": _round_am(_deltas({k: am.get(k, 0.0) for k in _AM_KEYS}, invert=False)),
            "invert_delta": False,
            "drill": {"statement_type": "BS", "level_2": l2 or None,
                      "level_3": l3, "level_4": l4, "gl_account_id": gid},
        })
    out.sort(key=lambda x: _sort_key_cm(x.get("amounts") or {}), reverse=True)
    return out


def _bs_l4_children(
    grains: list[dict], base_code: str, l2: str, l3: str, *, keys: list[str],
) -> list[dict]:
    """L4 detail children for an L3-only BS mapping row (raw signs).

    Child ``line_code`` is the composite ``<base_code>::<L4>`` so the BS line-detail
    endpoint can resolve the parent structure row and override its level_4.
    """
    seen: dict[str, dict[str, float]] = {}
    for g in grains:
        if (g.get("level_3") or "").strip() != l3:
            continue
        if l2 and (g.get("level_2") or "").strip() != l2:
            continue
        l4v = (g.get("level_4") or "").strip()
        if not l4v:
            continue
        if l4v not in seen:
            seen[l4v] = {k: 0.0 for k in keys}
        for k in keys:
            seen[l4v][k] += float(g.get(k) or 0.0)
    children: list[dict] = []
    for l4v, am4 in sorted(seen.items(), key=lambda kv: _sort_key_cm(kv[1]), reverse=True):
        children.append({
            "id": f"bs-l4-{abs(hash((base_code, l4v))) % 100000}",
            "line_code": f"{base_code}::{l4v}",
            "label": l4v,
            "row_kind": "detail",
            "amounts": _round_am(am4),
            "deltas": _round_am(_deltas({k: am4.get(k, 0.0) for k in _AM_KEYS}, invert=False)),
            "invert_delta": False,
            "drill": {"statement_type": "BS", "level_2": l2 or None,
                      "level_3": l3, "level_4": l4v, "gl_account_id": None},
            "accounts": _bs_accounts_under_l4(grains, l2, l3, l4v, keys=keys),
        })
    return children


# ---------------------------------------------------------------------------
# Main balance-sheet statement builder (month | week)
# ---------------------------------------------------------------------------

def build_bs_statement_compat(
    session: Session,
    *,
    period_grain: str = "month",
    year: Optional[int] = None,
    month: Optional[int] = None,
    iso_year: Optional[int] = None,
    iso_week: Optional[int] = None,
    entity: Optional[str] = None,
) -> dict[str, Any]:
    """Legacy FinancialStatementResponse for the balance sheet (statement='bs').

    Cumulative balances per column; running-sum section subtotals; credit-side
    display flip; net-profit injected into equity; equity-ratio KPI.  See module
    docstring for FORMULA / WORKED EXAMPLE / EDGE CASES.
    """
    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)

    is_week = period_grain == "week"
    if is_week:
        assert iso_year is not None and iso_week is not None
        sql, params = bs_grain_sql_week(iso_year, iso_week, ent_frag)
        np_sql, np_params = bs_net_profit_sql_week(iso_year, iso_week, ent_frag)
        yr, mo = plan_anchor_for_week(iso_year, iso_week)
        labels = col_labels_week(iso_year, iso_week)
        keys = list(_BS_KEYS_MONTH) + ["mtd"]
    else:
        assert year is not None and month is not None
        sql, params = bs_grain_sql_month(year, month, ent_frag)
        np_sql, np_params = bs_net_profit_sql_month(year, month, ent_frag)
        yr, mo = year, month
        labels = col_labels_month(year, month)
        keys = list(_BS_KEYS_MONTH)

    grains = [dict(r._mapping) for r in session.execute(text(sql), params).fetchall()]

    np_row = session.execute(text(np_sql), np_params).fetchone()
    np_map = dict(np_row._mapping) if (np_row is not None and hasattr(np_row, "_mapping")) else {}
    net_profit = {k: float(np_map.get(k) or 0.0) for k in keys}

    rows_out = hierarchy_from_grains(
        grains,
        hier_keys=_BS_HIER_KEYS,
        id_prefix="bs",
        statement_type="BS",
        gl_filter_keys=_BS_GL_FILTER,
        sort_key_map=_BS_SORT_MAP,
        week_ctx=is_week,
    )
    apply_bs_display_signs(rows_out)
    _inject_net_profit(rows_out, net_profit)
    _sort_bs_display_order(rows_out)

    kpi = equity_ratio_row_from_grains(grains, keys)
    _append_bs_statement_kpi_rows(rows_out, kpi)

    bs_plan_map = load_position_plan_map_pref(session, "BS", yr, mo, ent_frag)
    if bs_plan_map:
        attach_hierarchy_plan_to_rows(
            rows_out, bs_plan_map, _statement_structure_rows(session, "BS"),
        )

    out: dict[str, Any] = {
        "statement": "bs",
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
# Monthly view builder (one cumulative-balance column per month-end)
# ---------------------------------------------------------------------------

def build_bs_monthly(
    session: Session,
    *,
    year: int,
    month: int,
    entity: Optional[str] = None,
    span: str = "12m",
    ent_frag_override: Optional[str] = None,
) -> dict[str, Any]:
    """MonthlyResponse for the balance sheet: each ``amounts[YYYY-MM]`` is that
    month-END cumulative balance.

    span='12m' (default): last 12 month-end balances ending at (year, month).
    span='fy3': all _fy_span_periods months PLUS three balance totals (FY-2/FY-1
        year-end balances + YTD balance at the anchor month-end).
    Section-aware running-sum subtotals; credit-side flip; net-profit injected
    into equity (and ancestor subtotals incl. E&L); Equity-ratio KPI per column.

    ``ent_frag_override`` (additive, golden-safe): when given, this pre-built
    entity SQL fragment is used VERBATIM instead of resolving ``entity``.  ``None``
    preserves the existing single-entity behaviour exactly.
    """
    if ent_frag_override is not None:
        ent_frag = ent_frag_override
    else:
        ep = resolve_entity_prefix(session, entity)
        ent_frag = entity_sql_fragment(ep)

    effective_span = "fy3" if span == "fy3" else "12m"
    if effective_span == "fy3":
        periods = _fy_span_periods(year, month)
        totals = _fy_span_totals(year, month)
    else:
        periods = _last_12_periods(year, month)
        totals = []

    period_keys = [period_key(y, m) for y, m in periods]
    total_keys = [t["key"] for t in totals]
    all_keys = period_keys + total_keys

    sql, params = bs_monthly_grain_sql(year, month, ent_frag, span=effective_span)
    grains = [dict(r._mapping) for r in session.execute(text(sql), params).fetchall()]

    np_sql, np_params = bs_monthly_net_profit_sql(year, month, ent_frag, span=effective_span)
    np_row = session.execute(text(np_sql), np_params).fetchone()
    np_map = dict(np_row._mapping) if (np_row is not None and hasattr(np_row, "_mapping")) else {}
    net_profit = {k: float(np_map.get(k) or 0.0) for k in all_keys}

    rows_out = monthly_hierarchy(
        grains,
        hier_keys=_BS_HIER_KEYS,
        id_prefix="bs",
        column_keys=all_keys,
        sort_key_map=_BS_SORT_MAP,
        statement_type="BS",
        gl_filter_keys=_BS_GL_FILTER,
    )
    apply_bs_monthly_display_signs(rows_out)
    _inject_net_profit(
        rows_out,
        net_profit,
        deltas_fn=lambda am, invert=False: {},
    )
    _sort_bs_display_order(rows_out)

    kpi = equity_ratio_monthly_row(grains, all_keys)
    _append_bs_statement_kpi_rows(rows_out, kpi)

    out: dict[str, Any] = {
        "statement": "bs",
        "year": year, "month": month,
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
# L4 trend (cumulative balance at each window END; magnitude, like legacy)
# ---------------------------------------------------------------------------

def build_bs_l4_trend(
    session: Session,
    *,
    year: int,
    month: int,
    grain: str,
    level_2: str = "",
    level_3: str = "",
    level_4: str = "",
    entity: Optional[str] = None,
) -> dict[str, Any]:
    """L4TrendResponse for a balance-sheet line: each point is the cumulative
    balance AT the window's end date (a stock), shown as a magnitude (``abs``) so
    credit-side lines read positive — matching the legacy ``_balance_case`` (ABS).
    """
    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)

    sql, windows = bs_l4_trend_sql(year, month, grain, level_2, level_3, level_4, ent_frag)
    rows = session.execute(text(sql)).fetchall()
    if not rows:
        return {"series": [], "col_label": period_label(year, month), "prev_label": ""}

    row = dict(rows[0]._mapping) if hasattr(rows[0], "_mapping") else dict(rows[0])
    series = []
    for w in windows:
        cur = abs(float(row.get(w["pk"]) or 0.0))
        prev = abs(float(row.get(w["prev_pk"]) or 0.0))
        delta = round((cur - prev) / abs(prev) * 100, 1) if abs(prev) > 1e-6 else None
        series.append({
            "label": w["label"],
            "current": round(cur, 2),
            "previous": round(prev, 2),
            "delta_pct": delta,
            "date_from": w["date_from"],
            "date_to": w["date_to"],
        })
    prev_end = windows[-1].get("prev_end")
    prev_label = prev_end.strftime("%b %Y") if hasattr(prev_end, "strftime") else (
        str(prev_end) if prev_end else "")
    return {"series": series, "col_label": period_label(year, month), "prev_label": prev_label}


def _build_bs_rows(
    struct_bs: list[dict[str, Any]],
    grains: list[dict],
    keys: list[str],
    *,
    is_week: bool,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, float]]]:
    """Pure builder (DB-free): RAW running-sum values + display-flipped row tree.

    Returns ``(rows_out, raw_vals)`` where ``raw_vals`` holds the pre-flip section
    balances (used by the equity-ratio KPI).
    """
    zero = {k: 0.0 for k in keys}

    # Per-mapping raw balances (Σ matched grains).
    mapping_vals: dict[str, dict[str, float]] = {}
    for r in struct_bs:
        if r.get("row_type") != "mapping":
            continue
        code = r["line_code"]
        acc = {k: 0.0 for k in keys}
        for g in grains:
            if _match_grain(g, r):
                for k in keys:
                    acc[k] += float(g.get(k) or 0.0)
        mapping_vals[code] = acc

    raw_vals = _compute_bs_running_values(struct_bs, mapping_vals, keys)

    rows_out: list[dict[str, Any]] = []
    for r in struct_bs:
        rc = r["line_code"]
        rt = r.get("row_type", "mapping")
        sec = _bs_section(r)

        if rt == "title":
            rows_out.append({
                "id": f"bs-{rc}", "parent_id": None, "line_code": rc,
                "row_kind": "title", "label": r["balance_title"],
                "amounts": None, "deltas": None,
                "invert_delta": False, "is_bold": bool(r.get("is_bold", False)),
                "drill": None, "has_children": False, "children": [],
            })
            continue

        am = {k: float(raw_vals.get(rc, zero).get(k, 0.0)) for k in keys}
        l2 = (r.get("level_2") or "").strip()
        l3 = (r.get("level_3") or "").strip()
        l4 = (r.get("level_4") or "").strip()
        gid = (r.get("gl_account_id") or "").strip() or None
        drill = {
            "statement_type": "BS",
            "level_2": l2 or None, "level_3": l3 or None,
            "level_4": l4 or None, "gl_account_id": gid,
        }

        children: list[dict] = []
        if rt == "mapping" and l3 and not l4 and not gid:
            children = _bs_l4_children(grains, rc, l2, l3, keys=keys)
            if len(children) == 1 and children[0].get("label", "") == r["balance_title"]:
                drill = children[0]["drill"]
                children = []

        row = {
            "id": f"bs-{rc}", "parent_id": None, "line_code": rc,
            "row_kind": _row_kind_for_bs(rt),
            "label": r["balance_title"],
            "amounts": _round_am(am),
            "deltas": _round_am(_deltas({k: am.get(k, 0.0) for k in _AM_KEYS}, invert=False)),
            "invert_delta": bool(r.get("invert_delta", False)),
            "is_bold": bool(r.get("is_bold", False)),
            "drill": drill,
            "has_children": len(children) > 0,
            "children": children,
        }
        # Display flip: present the credit side as a positive magnitude.
        if sec == "credit":
            _flip_row_tree(row)
        rows_out.append(row)

    return rows_out, raw_vals


# ---------------------------------------------------------------------------
# Annual exit-readiness snapshot builder (fy_py / fy / cm_py / cm)
# ---------------------------------------------------------------------------

_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


_BS_LEVEL_2_ORDER = [
    "Fixed assets",
    "Current assets",
    "Prepaid expenses",
    "Deferred tax assets",
]

_BS_LEVEL_2_EQL_ORDER = [
    "Equity",
    "Liabilities",
    "Provisions & accruals",
]

_BS_FIXED_L3_ORDER = [
    "Tangible assets",
    "Intangible assets",
    "Financial assets",
]

_BS_CURRENT_L3_ORDER = [
    "Inventories",
    "Trade receivables",
    "Receivables from affiliates",
    "Cash & cash equivalents",
    "Other assets",
]


def _label_order_index(label: str, order: list[str]) -> int:
    """Stable display order; tolerates minor label variants (e.g. Cash)."""
    lbl = (label or "").strip()
    low = lbl.lower()
    for i, token in enumerate(order):
        if lbl == token or low == token.lower():
            return i
    if "cash" in low:
        for i, token in enumerate(order):
            if "cash" in token.lower():
                return i
    return 999


def _sort_children_by_label(children: list[dict[str, Any]], order: list[str]) -> None:
    children.sort(
        key=lambda r: (_label_order_index(r.get("label") or "", order), (r.get("label") or "")),
    )


def _sort_bs_display_order(rows: list[dict[str, Any]]) -> None:
    """German BS presentation order (display only; amounts unchanged)."""

    def _walk(node_rows: list[dict[str, Any]], parent_label: str = "") -> None:
        if not node_rows:
            return
        parent = (parent_label or "").strip()
        if parent == "Assets":
            _sort_children_by_label(node_rows, _BS_LEVEL_2_ORDER)
        elif parent.lower().startswith("equity") and "liab" in parent.lower():
            _sort_children_by_label(node_rows, _BS_LEVEL_2_EQL_ORDER)
        elif parent == "Fixed assets":
            _sort_children_by_label(node_rows, _BS_FIXED_L3_ORDER)
        elif parent == "Current assets":
            _sort_children_by_label(node_rows, _BS_CURRENT_L3_ORDER)
        for row in node_rows:
            label = (row.get("label") or "").strip()
            children = row.get("children") or []
            if children:
                _walk(children, label)

    _walk(rows)


def _sort_bs_asset_level2(rows: list[dict[str, Any]]) -> None:
    """Backward-compatible alias — full BS display order."""
    _sort_bs_display_order(rows)


def _bs_kpi_header_row(*, id_prefix: str = "bs") -> dict[str, Any]:
    return {
        "id": f"{id_prefix}-kpi-header",
        "line_code": "KPI_HEADER",
        "row_kind": "kpi_header",
        "label": "KPIs",
        "is_bold": False,
        "has_children": False,
        "children": [],
    }


def _append_bs_statement_kpi_rows(
    rows_out: list[dict[str, Any]],
    kpi: Optional[dict[str, Any]],
    *,
    id_prefix: str = "bs",
) -> None:
    if kpi is None:
        return
    rows_out.append(_bs_kpi_header_row(id_prefix=id_prefix))
    rows_out.append(kpi)


def col_labels_bs_snapshot(year: int, month: int) -> dict[str, str]:
    """Snapshot column labels (Dec year-3 … anchor PY | FY forecast | anchor)."""
    abbr = _MONTH_ABBR[month - 1]
    return {
        "dec_py2": label_actual(f"Dec{str(year - 3)[-2:]}"),
        "fy_py": label_actual(f"Dec{str(year - 2)[-2:]}"),
        "fy": label_actual(f"Dec{str(year - 1)[-2:]}"),
        "cm_py": label_actual(f"{abbr}{str(year - 1)[-2:]}"),
        # Balance-sheet positions are point-in-time (Stichtag), so a full-year "FY..F"
        # forecast label is semantically wrong — use the anchor-month forecast label
        # (e.g. "Jul25F"). Kept ending in "F" so the frontend passes it through.
        "fy_f": f"{abbr}{str(year)[-2:]}F",
        "cm": label_actual(f"{abbr}{str(year)[-2:]}"),
    }


_BS_FY_F_BUMP_CODES = (
    "NET_PROFIT",
    "BS_TOTAL_EQUITY",
    "BS_GRANDTOTAL_EQ_LIAB",
    "BS_GRANDTOTAL_ASSETS",
    # ER (exit-readiness) consolidated view uses label-derived line_codes for the
    # grand totals; bump both sides + the equity subtotal by the same net-profit YTG
    # so the forecast balance sheet stays balanced (Assets == Equity+Liab). A code
    # that is absent in a given dataset simply no-ops (safe). Quick best-effort.
    "er-bs-d0-Assets",
    "er-bs-d0-Equity___liabilities",
    "er-bs-d1-Equity___liabilities-Equity",
)


def _snapshot_net_profit_ytg_plan(
    session: Session,
    year: int,
    month: int,
    ent_frag: str,
) -> float:
    """Plan YTG on the net-profit line (full-year forecast increment after anchor month).

    The BS FY-forecast bump is the projected remaining-year NET INCOME, which lives
    in the **P&L** plan — NOT the BS position plan (a balance-sheet stock has no
    meaningful year-to-go). Prefer the ``forecast`` scenario (open-period run-rate);
    fall back to ``budget``. Sign convention: a positive YTG grows equity (retained
    earnings) and total assets. NOTE: quick best-effort — pending financial review.
    """
    # Local import avoids a circular import between the BS and PL compat modules.
    from app.services.fin_compat_pl import load_position_plan_map as _pl_plan_map

    for scenario in ("forecast", "budget"):
        try:
            plan_map = _pl_plan_map(session, "PL", year, month, ent_frag, scenario=scenario)
        except Exception:  # noqa: BLE001 — a missing plan must not break the statement
            continue
        for code in ("NET_PROFIT", "NET_INCOME", "NET_RESULT"):
            pm = plan_map.get(code)
            if pm is not None:
                ytg = float(pm.get("ytg") or 0.0)
                if abs(ytg) > 1e-6:
                    return ytg
    return 0.0


def _walk_snapshot_rows(rows: list[dict[str, Any]], fn: Any) -> None:
    for row in rows:
        fn(row)
        _walk_snapshot_rows(row.get("children") or [], fn)
        _walk_snapshot_rows(row.get("accounts") or [], fn)


def _apply_snapshot_fy_forecast_amounts(
    rows: list[dict[str, Any]],
    ytg_np: float,
    *,
    bump_codes: tuple[str, ...] = _BS_FY_F_BUMP_CODES,
) -> None:
    """Set fy_f on each row (default cm); bump equity/assets totals by plan YTG net profit."""

    def _refresh_deltas(row: dict[str, Any]) -> None:
        am = row.get("amounts")
        if isinstance(am, dict):
            row["deltas"] = _round_am(_snap_deltas(am, invert=bool(row.get("invert_delta", False))))

    def _init_fy_f(row: dict[str, Any]) -> None:
        am = row.get("amounts")
        if isinstance(am, dict) and "cm" in am:
            am["fy_f"] = round(float(am.get("cm") or 0.0), 2)
        _refresh_deltas(row)

    _walk_snapshot_rows(rows, _init_fy_f)

    bump = round(float(ytg_np), 2)
    if abs(bump) < 1e-6:
        return

    def _bump_code(rows_list: list[dict[str, Any]], code: str) -> None:
        for row in rows_list:
            if row.get("line_code") == code and isinstance(row.get("amounts"), dict):
                am = row["amounts"]
                am["fy_f"] = round(float(am.get("fy_f", am.get("cm", 0.0))) + bump, 2)
                _refresh_deltas(row)
            _bump_code(row.get("children") or [], code)
            _bump_code(row.get("accounts") or [], code)

    for code in bump_codes:
        _bump_code(rows, code)


def _snap_deltas(am: dict[str, float], invert: bool = False) -> dict[str, float]:
    """Snapshot deltas (mirrors legacy _er_snapshot_deltas):

        delta_fy = fy - fy_py ;  delta_cm = cm - cm_py ;  delta_f = fy_f - cm
    """
    d = {
        "delta_fy": am.get("fy", 0.0) - am.get("fy_py", 0.0),
        "delta_cm": am.get("cm", 0.0) - am.get("cm_py", 0.0),
        "delta_f": am.get("fy_f", am.get("cm", 0.0)) - am.get("cm", 0.0),
    }
    return {k: -v for k, v in d.items()} if invert else d


def build_bs_snapshot_annual(
    session: Session,
    *,
    year: int,
    month: int,
    entity: Optional[str] = None,
    ent_frag_override: Optional[str] = None,
) -> dict[str, Any]:
    """ErSnapshotResponse for the balance sheet — cumulative balances at 4 dates
    (fy_py / fy / cm_py / cm) with snapshot deltas, credit-side flip, net-profit in
    equity, and an Equity-ratio KPI.  Mirrors legacy get_er_balance_sheet.

    ``ent_frag_override`` (additive, golden-safe): when given, this pre-built
    entity SQL fragment is used VERBATIM instead of resolving ``entity``.  ``None``
    preserves the existing single-entity behaviour exactly.
    """
    if ent_frag_override is not None:
        ent_frag = ent_frag_override
        # An override is an entity-restricted scope (single prefix or a union of a
        # caller's own prefixes); a non-empty fragment means "scoped", so use the
        # per-scope closing bridge for net profit (closes Assets = E&L), matching
        # the single-entity branch below.
        is_entity_scoped = bool(ent_frag.strip())
    else:
        ep = resolve_entity_prefix(session, entity)
        ent_frag = entity_sql_fragment(ep)
        is_entity_scoped = bool(ep)

    sql, params = bs_snapshot_grain_sql(year, month, ent_frag)
    grains = [dict(r._mapping) for r in session.execute(text(sql), params).fetchall()]

    keys = list(_BS_SNAP_KEYS)  # fy_py, fy, cm_py, cm

    np_sql, np_params = bs_snapshot_net_profit_sql(year, month, ent_frag)
    np_row = session.execute(text(np_sql), np_params).fetchone()
    np_map = dict(np_row._mapping) if (np_row is not None and hasattr(np_row, "_mapping")) else {}
    if is_entity_scoped:
        net_profit = _equity_bridge_from_grains(grains, keys)
    else:
        net_profit = {k: float(np_map.get(k) or 0.0) for k in keys}

    def _snap_am(g: dict) -> dict[str, float]:
        return {k: float(g.get(k) or 0.0) for k in keys}

    def _snap_sum(grs: list[dict]) -> dict[str, float]:
        return _sum_amounts(grs, row_amounts_fn=_snap_am, sum_keys=keys)

    rows_out = hierarchy_from_grains(
        grains,
        hier_keys=_BS_HIER_KEYS,
        id_prefix="er-bs",
        statement_type="BS",
        gl_filter_keys=_BS_GL_FILTER,
        sort_key_map=_BS_SORT_MAP,
        row_amounts_fn=_snap_am,
        deltas_fn=_snap_deltas,
        sum_amounts_fn=_snap_sum,
    )
    apply_bs_display_signs(rows_out)
    _sort_bs_display_order(rows_out)
    _inject_net_profit(rows_out, net_profit, deltas_fn=_snap_deltas)

    ytg_np = _snapshot_net_profit_ytg_plan(session, year, month, ent_frag)
    _apply_snapshot_fy_forecast_amounts(rows_out, ytg_np)

    # DISPLAY GATE (Phase 4 extension): the Forecast (fy_f) column renders ONLY when
    # a real active + include_in_reporting plan version supplies plan values.  The
    # version-gated reader returns {} for parked/no-version/no-rows (same reader the
    # BS two-view uses).  fy_f numerics (bumped by ytg_np) are unchanged — the flag
    # drives DISPLAY only.
    bs_plan_map = load_position_plan_map_pref(session, "BS", year, month, ent_frag)

    kpi = equity_ratio_row_from_grains(
        grains, keys, row_id="er-bs-kpi-equity-ratio", line_code="EQUITY_RATIO",
    )
    if kpi is not None:
        kpi["deltas"] = _round_am(_snap_deltas(kpi["amounts"], invert=False))
    _append_bs_statement_kpi_rows(rows_out, kpi, id_prefix="er-bs")

    return {
        "statement": "bs",
        "year": year, "month": month,
        "col_labels": col_labels_bs_snapshot(year, month),
        "rows": rows_out,
        "has_plan_data": bool(bs_plan_map),
    }


def _flip_consl_row(row: dict[str, Any]) -> None:
    """Negate entity_amounts + aggregated/ic_eliminations/consolidation (in place)."""
    ea = row.get("entity_amounts") or {}
    for k in list(ea.keys()):
        ea[k] = -ea[k]
    for f in ("aggregated", "ic_eliminations", "consolidation"):
        if f in row:
            row[f] = -row[f]
    ep = row.get("entity_periods")
    if isinstance(ep, dict):
        for ec in ep:
            pd = ep[ec]
            if isinstance(pd, dict):
                for k in list(pd.keys()):
                    pd[k] = -pd[k]
    for f in ("aggregated_periods", "consolidation_periods"):
        pd = row.get(f)
        if isinstance(pd, dict):
            for k in list(pd.keys()):
                pd[k] = -pd[k]
    for child in row.get("children") or []:
        _flip_consl_row(child)


def apply_bs_consl_display_signs(rows: list[dict[str, Any]]) -> None:
    """Flip credit-side consolidation rows (same rule as statement hierarchy)."""
    for row in rows:
        label = (row.get("label") or "").lower()
        if "asset" not in label:
            _flip_consl_row(row)


def _bump_consl_row_amounts(
    row: dict[str, Any],
    bump: dict[str, dict[str, float]],
    entity_codes: list[str],
    keys: list[str],
) -> None:
    primary = keys[-1]
    ea = row.get("entity_amounts") or {}
    for ec in entity_codes:
        if ec in bump:
            ea[ec] = round(float(ea.get(ec, 0.0)) + float(bump[ec].get(primary, 0.0)), 2)
    row["entity_amounts"] = ea
    agg = sum(float(ea.get(ec, 0.0)) for ec in entity_codes)
    row["aggregated"] = round(agg, 2)
    row["consolidation"] = round(agg, 2)

    if len(keys) <= 1:
        return
    ep = row.get("entity_periods")
    if not isinstance(ep, dict):
        return
    for ec in entity_codes:
        if ec not in bump:
            continue
        pd = ep.setdefault(ec, {k: 0.0 for k in keys})
        for k in keys:
            pd[k] = round(float(pd.get(k, 0.0)) + float(bump[ec].get(k, 0.0)), 2)
    agg_periods = {
        k: round(sum(float(ep.get(ec, {}).get(k, 0.0)) for ec in entity_codes), 2)
        for k in keys
    }
    row["aggregated_periods"] = agg_periods
    row["consolidation_periods"] = dict(agg_periods)


def _inject_net_profit_consl(
    rows: list[dict[str, Any]],
    net_profit_by_entity: dict[str, dict[str, float]],
    entity_codes: list[str],
    keys: list[str],
    *,
    ancestors: Optional[list[dict[str, Any]]] = None,
) -> bool:
    """Inject Net profit under Equity; roll into ancestor consolidation subtotals."""
    if ancestors is None:
        ancestors = []
    primary = keys[-1]
    for row in rows:
        label = (row.get("label") or "").lower()
        if ("equit" in label or "eigenkapital" in label) and "liab" not in label:
            np_entity_am = {
                ec: {k: float(net_profit_by_entity.get(ec, {}).get(k, 0.0)) for k in keys}
                for ec in entity_codes
            }
            np_child = _consl_make_row_from_hierarchy(
                "bs-net-profit",
                "Net profit",
                "line",
                False,
                np_entity_am,
                entity_codes,
                keys,
                line_code="NET_PROFIT",
            )
            row.setdefault("children", [])
            row["has_children"] = True
            row["children"].append(np_child)
            _bump_consl_row_amounts(row, net_profit_by_entity, entity_codes, keys)
            for anc in ancestors:
                _bump_consl_row_amounts(anc, net_profit_by_entity, entity_codes, keys)
            return True
        if _inject_net_profit_consl(
            row.get("children") or [],
            net_profit_by_entity,
            entity_codes,
            keys,
            ancestors=ancestors + [row],
        ):
            return True
    return False


def _consl_make_row_from_hierarchy(
    row_id: str,
    label: str,
    row_kind: str,
    is_bold: bool,
    entity_am: dict[str, dict[str, float]],
    entity_codes: list[str],
    keys: list[str],
    *,
    children: Optional[list[dict[str, Any]]] = None,
    line_code: Optional[str] = None,
) -> dict[str, Any]:
    from app.services.fin_compat_hierarchy import _consl_make_row

    return _consl_make_row(
        row_id, label, row_kind, is_bold, entity_am, entity_codes, keys,
        children=children, line_code=line_code,
    )


def _load_net_profit_by_entity(
    session: Session,
    np_sql: str,
    np_params: dict[str, Any],
    entity_codes: list[str],
    ep_to_code: dict[str, str],
    keys: list[str],
) -> dict[str, dict[str, float]]:
    zero = {k: 0.0 for k in keys}
    out = {ec: dict(zero) for ec in entity_codes}
    for r in session.execute(text(np_sql), np_params).fetchall():
        m = dict(r._mapping) if hasattr(r, "_mapping") else dict(r)
        lec = ep_to_code.get((m.get("entity_prefix") or "").strip())
        if lec is None:
            continue
        for k in keys:
            out[lec][k] = float(m.get(k) or 0.0)
    return out


def _equity_bridge_by_entity_from_grains(
    grains: list[dict],
    entity_codes: list[str],
    ep_to_code: dict[str, str],
    keys: list[str],
) -> dict[str, dict[str, float]]:
    """Per-entity BS closing bridge (presented equity +) = Σ raw BS balances.

    Closes Assets = Equity & liabilities per legal entity when injected as
    ``Net profit`` under Equity (after credit-side display flip).  Uses FY-scoped
    grain totals — not P&L YTD alone — so consortium / IC offsets that net at
    group level still balance each entity column.
    """
    zero = {k: 0.0 for k in keys}
    out = {ec: dict(zero) for ec in entity_codes}
    for g in grains:
        lec = ep_to_code.get((g.get("entity_prefix") or "").strip())
        if lec is None:
            continue
        for k in keys:
            out[lec][k] += float(g.get(k) or 0.0)
    return out


def _equity_bridge_from_grains(grains: list[dict], keys: list[str]) -> dict[str, float]:
    """Single-entity (or consolidated) BS closing bridge from grain rows."""
    return {k: sum(float(g.get(k) or 0.0) for g in grains) for k in keys}


def _equity_ratio_by_entity_from_grains(
    grains: list[dict],
    entity_codes: list[str],
    ep_to_code: dict[str, str],
    keys: list[str],
) -> dict[str, dict[str, float]]:
    """|equity L2| / |assets L1| * 100 per entity and period (raw grains, pre net-profit)."""
    zero = {k: 0.0 for k in keys}
    out = {ec: dict(zero) for ec in entity_codes}
    by_ec: dict[str, list[dict]] = {ec: [] for ec in entity_codes}
    for g in grains:
        lec = ep_to_code.get((g.get("entity_prefix") or "").strip())
        if lec is not None:
            by_ec[lec].append(g)
    for ec, grs in by_ec.items():
        if not grs:
            continue
        kpi = equity_ratio_row_from_grains(grs, keys)
        if kpi is None:
            continue
        for k in keys:
            out[ec][k] = float(kpi["amounts"].get(k, 0.0))
    return out


def _append_consl_equity_ratio_kpi(
    rows_out: list[dict[str, Any]],
    grains: list[dict],
    entity_codes: list[str],
    ep_to_code: dict[str, str],
    keys: list[str],
    *,
    primary: str = "cm",
) -> None:
    kpi = equity_ratio_row_from_grains(grains, keys)
    if kpi is None:
        return
    group_am = {k: float(kpi["amounts"].get(k, 0.0)) for k in keys}
    er_consl = group_am.get(primary, 0.0)
    entity_periods = _equity_ratio_by_entity_from_grains(
        grains, entity_codes, ep_to_code, keys,
    )
    entity_amounts = {ec: entity_periods[ec].get(primary, 0.0) for ec in entity_codes}

    rows_out.append({
        "id": "bs-kpi-header",
        "label": "KPIs",
        "row_kind": "kpi_header",
        "is_bold": False,
        "entity_amounts": {ec: 0.0 for ec in entity_codes},
        "aggregated": 0.0,
        "ic_eliminations": 0.0,
        "consolidation": 0.0,
        "has_children": False,
        "children": [],
        **({"entity_periods": {ec: {k: 0.0 for k in keys} for ec in entity_codes},
            "aggregated_periods": {k: 0.0 for k in keys},
            "consolidation_periods": {k: 0.0 for k in keys}} if len(keys) > 1 else {}),
    })
    kpi_row: dict[str, Any] = {
        "id": "bs-kpi-equity-ratio",
        "label": "Equity ratio",
        "row_kind": "kpi",
        "is_bold": False,
        "entity_amounts": entity_amounts,
        "aggregated": er_consl,
        "ic_eliminations": 0.0,
        "consolidation": er_consl,
        "has_children": False,
        "children": [],
    }
    if len(keys) > 1:
        kpi_row["entity_periods"] = entity_periods
        kpi_row["aggregated_periods"] = dict(group_am)
        kpi_row["consolidation_periods"] = dict(group_am)
    rows_out.append(kpi_row)


def build_bs_consolidation(
    session: Session,
    *,
    period_grain: str = "month",
    year: Optional[int] = None,
    month: Optional[int] = None,
    iso_year: Optional[int] = None,
    iso_week: Optional[int] = None,
) -> dict[str, Any]:
    """ConsolidationResponse for the balance sheet (per-entity balances).

    Row labels and nesting mirror the consolidated statement hierarchy
    (level_1 / level_2 / …), not dim_pl_structure ``balance_title`` subtotals.
    Net profit (P&L YTD per entity) is injected under Equity; credit side is
    display-flipped; Equity-ratio KPI closes the table.
    """
    if period_grain == "week":
        assert iso_year is not None and iso_week is not None
        sql, params = bs_consl_grain_sql_week(iso_year, iso_week)
        yr, mo = plan_anchor_for_week(iso_year, iso_week)
        labels = col_labels_week(iso_year, iso_week)
        keys = ["cm"]
    elif period_grain == "year":
        assert year is not None and month is not None
        sql, params = bs_consl_grain_sql_annual(year, month)
        yr, mo = year, month
        labels = col_labels_bs_snapshot(year, month)
        keys = list(_BS_SNAP_KEYS)
    else:
        assert year is not None and month is not None
        sql, params = bs_consl_grain_sql_month(year, month)
        yr, mo = year, month
        labels = col_labels_month(year, month)
        keys = ["cm"]

    grains = [dict(r._mapping) for r in session.execute(text(sql), params).fetchall()]

    ent_rows = session.execute(text(
        "SELECT legal_entity_code, entity_prefix, entity_name FROM dim_legal_entity "
        "ORDER BY legal_entity_code"
    )).fetchall()
    entity_codes = [r[0] for r in ent_rows]
    ep_to_code = {r[1]: r[0] for r in ent_rows}
    entity_dicts = [{"code": r[0], "label": r[2]} for r in ent_rows]

    rows_out = consolidation_hierarchy_from_grains(
        grains,
        entity_codes=entity_codes,
        ep_to_code=ep_to_code,
        hier_keys=_BS_HIER_KEYS,
        id_prefix="bs",
        amount_keys=keys,
        sort_key_map=_BS_SORT_MAP,
    )
    apply_bs_consl_display_signs(rows_out)
    _sort_bs_display_order(rows_out)

    net_profit = _equity_bridge_by_entity_from_grains(
        grains, entity_codes, ep_to_code, keys,
    )
    _inject_net_profit_consl(rows_out, net_profit, entity_codes, keys)
    _append_consl_equity_ratio_kpi(
        rows_out, grains, entity_codes, ep_to_code, keys, primary=keys[-1],
    )

    col_label = labels.get(keys[-1], period_label(yr, mo))
    out: dict[str, Any] = {
        "statement": "bs",
        "period_grain": period_grain,
        "year": yr,
        "month": mo,
        "col_label": col_label,
        "entities": entity_dicts,
        "rows": rows_out,
    }
    if period_grain == "year":
        out["col_labels"] = labels
    if iso_year is not None:
        out["iso_year"] = iso_year
    if iso_week is not None:
        out["iso_week"] = iso_week
    return out


# ---------------------------------------------------------------------------
# Line detail (cumulative balances; PlLineDetailResponse shape, statement BS)
# ---------------------------------------------------------------------------

def _bs_pm(year: int, month: int) -> tuple[int, int]:
    return (year, month - 1) if month > 1 else (year - 1, 12)


def _bs_last_day(year: int, month: int):
    from app.services.fin_compat_sql import last_day
    return last_day(year, month)


def _resolve_bs_mapping_row(session: Session, line_code: str) -> Optional[dict]:
    """Look up a BS structure row (supports the base::L4 composite line_code)."""
    base = line_code.split("::")[0].strip()
    l4_override = line_code.split("::", 1)[1].strip() if "::" in line_code else None
    row = session.execute(text(
        "SELECT line_code, row_type, balance_title, level_2, level_3, level_4, gl_account_id, kpi_code "
        "FROM dim_bs_structure WHERE line_code = :lc LIMIT 1"
    ), {"lc": base}).fetchone()
    if not row:
        return None
    r = dict(row._mapping)
    if l4_override:
        r["level_4"] = l4_override
        r["line_code"] = line_code
    return r


def _bs_scope_fragment(row: dict, ent_frag: str) -> tuple[str, dict[str, Any]]:
    """WHERE fragment scoping a BS mapping row's GL accounts (level_0='BS')."""
    l2 = (row.get("level_2") or "").strip()
    l3 = (row.get("level_3") or "").strip()
    l4 = (row.get("level_4") or "").strip()
    gid = (row.get("gl_account_id") or "").strip()
    parts = ["a.level_0 = 'BS'"]
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
        frag = ent_frag.strip()
        if frag.upper().startswith("AND "):
            frag = frag[4:]
        parts.append(frag)
    return " AND ".join(parts), params


def build_bs_line_detail(
    session: Session,
    line_code: str,
    year: int, month: int, entity: Optional[str],
    *,
    limit: int = 50,
    timeline_months: int = 12,
    use_llm: bool = True,
    line_mom_keur: Optional[float] = None,
    concentration_only: bool = False,
) -> dict[str, Any]:
    """PlLineDetailResponse for a BS line — CUMULATIVE balances (stock).

    Account balances are cumulative at the CM and PM month-end cutoffs (raw sign:
    assets +, credit −).  Top bookings for the current month and a 12-month
    cumulative balance timeline.  Same response shape as the P&L line detail.
    """
    row = _resolve_bs_mapping_row(session, line_code)
    if not row:
        raise ValueError(f"Unknown BS line_code: {line_code}")

    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)
    scope_sql, base_params = _bs_scope_fragment(row, ent_frag)

    d_cm = _bs_last_day(year, month).isoformat()
    pm_y, pm_m = _bs_pm(year, month)
    d_pm = _bs_last_day(pm_y, pm_m).isoformat()

    # Cumulative account balances at CM / PM month-ends (raw stored sign).
    # reporting-v2 Phase 3: exclude synthetic net-profit equity rows from the raw
    # account balances (same exclusion as the BS grain; presentation unchanged).
    _np_guard = "AND COALESCE(e.entry_type,'') <> 'net_profit'"
    acc_sql = text(f"""
        SELECT
            l.account_number_group,
            MAX(a.gl_account_id)   AS gl_account_id,
            MAX(a.account_name)    AS account_name,
            COALESCE(SUM(CASE WHEN e.posting_date <= :d_cm {_np_guard} THEN l.amount ELSE 0 END), 0)::float8 AS balance_cm,
            COALESCE(SUM(CASE WHEN e.posting_date <= :d_pm {_np_guard} THEN l.amount ELSE 0 END), 0)::float8 AS balance_pm
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE {scope_sql}
          AND e.posting_date <= :d_cm
        GROUP BY l.account_number_group
        HAVING ABS(COALESCE(SUM(CASE WHEN e.posting_date <= :d_cm {_np_guard} THEN l.amount ELSE 0 END), 0)) > 0.01
        ORDER BY ABS(COALESCE(SUM(CASE WHEN e.posting_date <= :d_cm {_np_guard} THEN l.amount ELSE 0 END), 0)) DESC
        LIMIT 30
    """)
    acc_rows = session.execute(acc_sql, {**base_params, "d_cm": d_cm, "d_pm": d_pm}).fetchall()

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

    # Top bookings posted in the current month (raw amount).
    ja = f"{year}-{month:02d}-01"
    bk_sql = text(f"""
        SELECT
            l.booking_line_id,
            e.posting_date::text AS posting_date,
            SUBSTRING(l.journal_entry_group_number FROM 3) AS journal_entry_number,
            le.legal_entity_code,
            l.fiscal_year,
            MAX(a.gl_account_id) AS gl_account_id,
            MAX(a.account_name)  AS account_name,
            l.amount::float8     AS amount,
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
          AND e.posting_date BETWEEN :ja AND :d_cm
        GROUP BY l.booking_line_id, e.posting_date, l.journal_entry_group_number,
                 le.legal_entity_code, l.fiscal_year, l.amount, l.line_note,
                 e.reference_document_number
        ORDER BY ABS(l.amount) DESC
        LIMIT :lim
    """)
    bk_rows = session.execute(bk_sql, {**base_params, "ja": ja, "d_cm": d_cm, "lim": limit}).fetchall()
    top_bookings = []
    for b in bk_rows:
        top_bookings.append({
            "booking_line_id": b[0], "posting_date": b[1],
            "journal_entry_number": b[2], "legal_entity_code": b[3],
            "fiscal_year": b[4], "gl_account_id": b[5] or "",
            "account_name": b[6], "amount": round(float(b[7] or 0) / 1000, 2),
            "line_note": b[8] or "", "reference": b[9] or "",
            "counter_gl_account_id": None, "counter_account_name": None,
        })

    # Concentration-only fast path (anomaly engine) — skip timeline/sub-lines/commentary.
    if concentration_only:
        from app.services.fin_compat_narrative_core import concentration_only_payload
        return concentration_only_payload(
            line_code, row.get("balance_title", line_code),
            year, month, entity, accounts_out, top_bookings)

    # 12-month cumulative balance timeline (one balance per month-end).
    periods = _last_12_periods(year, month)[-timeline_months:]
    period_defs = [
        {"year": y, "month": m,
         "label": f"{_MONTH_ABBR[m - 1]}{str(y)[-2:]}", "key": period_key(y, m)}
        for y, m in periods
    ]
    tl_cases = " ".join(
        f"COALESCE(SUM(CASE WHEN e.posting_date <= '{_bs_last_day(y, m).isoformat()}' "
        f"{_np_guard} THEN l.amount ELSE 0 END),0) AS \"{period_key(y, m)}\","
        for y, m in periods
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
          AND e.posting_date <= :d_cm
        GROUP BY l.account_number_group
        HAVING ABS(COALESCE(SUM(CASE WHEN e.posting_date <= :d_cm {_np_guard} THEN l.amount ELSE 0 END),0)) > 0.01
        ORDER BY ABS(COALESCE(SUM(CASE WHEN e.posting_date <= :d_cm {_np_guard} THEN l.amount ELSE 0 END),0)) DESC
        LIMIT 10
    """)
    tl_rows = session.execute(tl_sql, {**base_params, "d_cm": d_cm}).fetchall()
    accounts_timeline = []
    for t in tl_rows:
        d = dict(t._mapping) if hasattr(t, "_mapping") else dict(t)
        accounts_timeline.append({
            "gl_account_id": d.get("gl_account_id") or d.get("account_number_group") or "",
            "account_name": d.get("account_name") or "",
            "series": [
                {"label": pd["label"], "value_keur": round(float(d.get(pd["key"]) or 0) / 1000, 2)}
                for pd in period_defs
            ],
        })

    # Sub-lines (distinct L4 under the L3 BS scope).
    sub_lines: list[dict] = []
    l3_val = (row.get("level_3") or "").strip()
    l2_val = (row.get("level_2") or "").strip()
    if l3_val:
        sl_rows = session.execute(text(
            "SELECT DISTINCT NULLIF(TRIM(level_4), '') AS level_4 "
            "FROM dim_gl_account WHERE level_0 = 'BS' AND level_2 = :l2 AND level_3 = :l3 "
            "AND NULLIF(TRIM(level_4),'') IS NOT NULL ORDER BY level_4"
        ), {"l2": l2_val, "l3": l3_val}).fetchall()
        base_code = line_code.split("::")[0].strip()
        for sl in sl_rows:
            if sl[0]:
                sub_lines.append({"parent_line_code": base_code, "level_4": sl[0], "label": sl[0]})

    commentary = _bs_commentary(accounts_out, top_bookings)
    return {
        "line_code": line_code,
        "label": row.get("balance_title", line_code),
        "year": year, "month": month, "entity": entity,
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
        "meta": {"llm_used": False, "algorithm_version": "bs_outliers_v1"},
    }


def _bs_commentary(accounts: list[dict], bookings: list[dict]) -> dict[str, str]:
    if not accounts:
        return {"accounts": "No GL accounts in scope for this balance.", "postings": ""}
    top_3 = sorted(accounts, key=lambda a: abs(a["balance_cm"]), reverse=True)[:3]
    acc_text = "Top balances at period end: " + "; ".join(
        f"{a['account_name'] or a['gl_account_id']} (€{a['balance_cm']:.0f}k)" for a in top_3
    )
    post_text = ""
    if bookings:
        largest = bookings[0]
        post_text = (f"Largest movement: {largest['gl_account_id']} "
                     f"€{largest['amount']:.0f}k on {largest['posting_date']}.")
    return {"accounts": acc_text, "postings": post_text}


# ---------------------------------------------------------------------------
# Deterministic narrative (PlNarrativeResponse shape) for the balance sheet
# ---------------------------------------------------------------------------

def _bs_priority_boost(label: str) -> float:
    """Anchor boost by BS line family (mirrors legacy bs_narrative._priority_boost)."""
    low = (label or "").lower()
    if "receivable" in low or "debtor" in low:
        return 28.0
    if "payable" in low or "creditor" in low:
        return 26.0
    if "inventor" in low:
        return 22.0
    if "provision" in low or "accrual" in low or "rückstellung" in low:
        return 20.0
    if "fixed asset" in low or "property" in low or "intangible" in low:
        return 18.0
    if "cash" in low:
        return 16.0
    return 0.0


def _bs_total_assets_row(rows: list[dict]) -> Optional[dict]:
    """Find the 'Total assets' grandtotal (asset side, not equity & liabilities)."""
    for r in rows:
        lbl = (r.get("label") or "").lower()
        if "total asset" in lbl and "liab" not in lbl:
            return r
        hit = _bs_total_assets_row(r.get("children") or [])
        if hit:
            return hit
    return None


def _bs_credit_line_codes(rows: list[dict]) -> set[str]:
    """Line codes whose section is credit-normal (so detail signs must be flipped)."""
    out: set[str] = set()

    def walk(rs: list[dict]) -> None:
        for r in rs:
            kc = str(r.get("kpi_code") or "")
            if r.get("row_kind") == "line" and kc.lower() in (
                "bs:credit", "bs:liability", "bs:equity",
            ):
                out.add(r.get("line_code") or "")
            walk(r.get("children") or [])

    walk(rows)
    return out


def _assemble_bs_intro(
    *,
    cm_label: str,
    pm_label: str,
    group_label: str,
    ta_cm: float,
    ta_mom: float,
    ta_yoy: float,
    primary_drivers: list[dict[str, Any]],
    period_grain: str = "month",
) -> str:
    """Legacy-style BS overview intro (mirrors pl_narrative assemble_intro_v2 depth)."""
    from app.services import fin_compat_narrative_core as core

    group_phrase = "the group's" if not group_label or group_label == "Group" else f"{group_label}'s"
    intro = f"As of {cm_label}, {group_phrase} total assets stood at {core.fmt_keur(ta_cm)}"
    if abs(ta_mom) >= 0.5:
        td = "up" if ta_mom > 0 else "down"
        if period_grain == "week":
            prior = pm_label.replace("KW", "CW") if pm_label.upper().startswith("KW") else pm_label
            intro += f", {td} {core.fmt_keur(abs(ta_mom))} compared to {prior}"
        else:
            intro += f", {td} {core.fmt_keur(abs(ta_mom))} from the {pm_label} month-end"
    if abs(ta_yoy) >= 0.5:
        yd = "up" if ta_yoy > 0 else "down"
        intro += f" and {yd} {core.fmt_keur(abs(ta_yoy))} year-on-year"
    intro += "."
    if primary_drivers:
        parts: list[str] = []
        for d in primary_drivers[:2]:
            bit = f"{core.lower_first(d['label'])} ({core.fmt_keur_signed(d['delta'])})"
            lead = d.get("lead_account")
            if lead:
                bit += f", concentrated in {lead}"
            parts.append(bit)
        intro += f" The balance sheet was primarily shaped by {' and '.join(parts)}."
    intro += " To conclude, key drivers consist of:"
    return intro


def build_bs_narrative(
    session: Session,
    year: int, month: int, entity: Optional[str],
    *,
    period_grain: str = "month",
    iso_year: Optional[int] = None,
    iso_week: Optional[int] = None,
    max_bullets: Optional[int] = None,
    use_llm: bool = False,
) -> dict[str, Any]:
    """Deterministic balance-sheet key-drivers narrative (legacy-depth, BS-specific).

    Unlike the P&L (a flow), the BS is a *stock*: bullets are point-in-time
    ("X stood at €Yk as of <period>, up €Zk from the prior month-end"), anchored on
    total assets and ordered by the largest balance movers.  Each big mover is
    enriched with GL account / booking concentration via ``build_bs_line_detail``
    and with hidden-netting detection over its sub-lines.  Credit-side balances are
    already display-flipped in the statement; their GL detail (stored credit-
    negative) is re-signed for display via ``gl_sign_fn``.  Output shape is the
    unchanged ``PlNarrativeResponse``.

    LLM stays optional and OFF by default with a graceful fallback.
    """
    from datetime import datetime, timezone

    from app.services import fin_compat_narrative_core as core
    from app.services.fin_compat_narrative import _direction

    if period_grain == "week" and iso_year is not None and iso_week is not None:
        yr, mo = plan_anchor_for_week(iso_year, iso_week)
    else:
        yr, mo = year, month

    try:
        from app.services.fin_compat_narrative import statement_for_narrative
        rows, labels = statement_for_narrative(
            session,
            "bs",
            period_grain=period_grain,
            year=yr,
            month=mo,
            entity=entity,
            iso_year=iso_year,
            iso_week=iso_week,
        )
    except Exception:
        rows, labels = [], col_labels_month(yr, mo)
    cm_label = labels.get("cm", period_label(yr, mo))
    pm_label = labels.get("pm", "the prior month")
    cap = core.clamp_cap(max_bullets)

    ta_row = _bs_total_assets_row(rows)
    ta_am = (ta_row or {}).get("amounts", {}) or {}
    ta_cm = float(ta_am.get("cm") or 0)
    ta_pm = float(ta_am.get("pm") or 0)
    ta_py = float(ta_am.get("py_cm") or 0)
    ta_mom = float(ta_cm - ta_pm)
    ta_yoy = float(ta_cm - ta_py)
    base_cm = abs(ta_cm) or 1.0

    credit_codes = _bs_credit_line_codes(rows)

    def _gl_detail(line_code: str, signed_mom_eur: float) -> Optional[dict]:
        try:
            return build_bs_line_detail(
                session, line_code, yr, mo, entity,
                use_llm=False, line_mom_keur=signed_mom_eur / 1000.0,
            )
        except Exception:
            return None

    def _gl_sign(line_code: str) -> float:
        return -1.0 if line_code in credit_codes else 1.0

    drivers = core.analyze_statement(
        rows,
        base_cm=base_cm,
        cap=cap,
        anchor_boost_fn=lambda lc: _bs_priority_boost(
            (core_label_by_code(rows, lc) or "")
        ),
        gl_detail_fn=_gl_detail,
        gl_sign_fn=_gl_sign,
    )

    group_label = entity or "Group"
    ctx = core.ProseContext(
        statement_kind="bs",
        period_label=cm_label,
        prior_label=pm_label,
        base_label="total assets",
        base_cm=base_cm,
        balance_style=True,
        tone_mode="directional",
        movement_noun="the balance movement",
        period_grain=period_grain,
    )
    bullets = core.build_bullets(drivers, ctx)

    # Primary drivers: highest-scoring lines; bullets stay in table order.
    primary_drivers: list[dict] = []
    for f in sorted(drivers, key=lambda x: x["score"], reverse=True)[:3]:
        dm = f["display_mom"]
        conc = f.get("concentration") or {}
        lead_account: Optional[str] = None
        if conc.get("top_account_name"):
            lead_account = core.format_account(
                conc.get("top_account_name"),
                conc.get("top_account_gl_id"),
            )
        primary_drivers.append({
            "label": f["label"],
            "delta": round(dm, 2),
            "direction": _direction(dm),
            "lead_account": lead_account,
        })

    # Headline + intro (total-assets anchored, point-in-time wording).
    if abs(ta_mom) < 0.5:
        headline = f"{group_label} balance sheet at {cm_label}"
    else:
        dir_word = "rose" if ta_mom > 0 else "fell"
        lead = f", led by {primary_drivers[0]['label']}" if primary_drivers else ""
        headline = f"{group_label} total assets {dir_word} in {cm_label}{lead}"[:120]

    intro = _assemble_bs_intro(
        cm_label=cm_label,
        pm_label=pm_label,
        group_label=group_label,
        ta_cm=ta_cm,
        ta_mom=ta_mom,
        ta_yoy=ta_yoy,
        primary_drivers=primary_drivers,
        period_grain=period_grain,
    )

    # Plan overlay (golden-safe): cm_vs_plan on the headline 'Total assets' line =
    # actual ta_cm − plan_cm.  ONLY populated when the manual BS budget has signal;
    # with no budget rows load_position_plan_map returns {} → cm_vs_plan stays 0.0 →
    # byte-identical.  BS plan_cm is presented via the centralized stored_to_present
    # helper (asset +/credit −), matching the displayed BS actual convention.
    cm_vs_plan = 0.0
    try:
        bs_plan_map = load_position_plan_map_pref(
            session, "BS", yr, mo,
            entity_sql_fragment(resolve_entity_prefix(session, entity)),
        )
        if bs_plan_map and ta_row is not None:
            tpm = bs_plan_map.get(ta_row.get("line_code"))
            if tpm:
                cm_vs_plan = round(ta_cm - float(tpm.get("plan_cm") or 0.0), 2)
    except Exception:
        cm_vs_plan = 0.0  # graceful — never break the narrative on a plan-read error

    llm_used = False
    if use_llm and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from app.services.fin_compat_narrative import _llm_enhance

            headline, intro, bullets, llm_used = _llm_enhance(
                headline, intro, bullets, cm_label, group_label,
            )
        except Exception:
            pass  # graceful degradation — keep deterministic output

    return {
        "headline": headline,
        "intro": intro,
        "intro_facts": {
            "period_label": cm_label, "group_label": group_label,
            "net_profit_ytd": 0.0, "coverage_pct": None,
            "cm_month_label": cm_label, "cm_vs_plan": cm_vs_plan, "cm_vs_plan_qualifier": "",
            "primary_drivers": primary_drivers, "entity_split": None,
        },
        "bullets": bullets,
        "entity_split": None,
        "meta": {
            "algorithm_version": "bs_narrative_compat_v2", "llm_used": llm_used,
            "max_bullets": cap, "cache_hit": False,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "entity_scope": entity or "",
        },
    }


def core_label_by_code(rows: list[dict], line_code: str) -> Optional[str]:
    """Find a row label by line_code anywhere in the BS tree (anchor-boost helper)."""
    for r in rows:
        if r.get("line_code") == line_code:
            return r.get("label")
        hit = core_label_by_code(r.get("children") or [], line_code)
        if hit:
            return hit
    return None


# ---------------------------------------------------------------------------
# Provision roll-forward (ported from legacy services/provision_rollforward.py)
# ---------------------------------------------------------------------------

def build_bs_provision_rollforward(
    session: Session,
    year: int, month: int, entity: Optional[str] = None,
) -> dict[str, Any]:
    """ProvisionRollforwardResponse — GL-derived opening/closing/net_movement per
    provision category (level_2 = 'Provisions & accruals').

    Presented like the legacy with ``amount * -1`` so a provision balance reads
    positive.  No DB writes (the legacy cache refresh is intentionally omitted).
    When the GL has no such accounts the rows list is empty and the totals are 0
    (clean fallback).
    """
    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)
    sql, params = bs_provision_grain_sql(year, month, ent_frag)

    try:
        grains = [dict(r._mapping) for r in session.execute(text(sql), params).fetchall()]
    except Exception:
        grains = []

    ek = "" if (not entity or str(entity).lower() == "all") else str(entity).strip()
    rows_out: list[dict[str, Any]] = []
    for g in grains:
        opening = float(g.get("opening_balance") or 0)
        closing = float(g.get("closing_balance") or 0)
        movement = float(g.get("period_movement") or 0)
        rows_out.append({
            "legal_entity_code": ek,
            "level_3": g.get("level_3") or "—",
            "level_4": g.get("level_4") or "",
            "opening_balance": round(opening, 2),
            "additions": round(movement, 2) if movement > 0 else 0.0,
            "releases": round(-movement, 2) if movement < 0 else 0.0,
            "usage": 0.0,
            "reversals": 0.0,
            "fx_adjustment": round(closing - (opening + movement), 2),
            "closing_balance": round(closing, 2),
            "period_movement": round(movement, 2),
        })

    total_open = sum(float(r["opening_balance"]) for r in rows_out)
    total_close = sum(float(r["closing_balance"]) for r in rows_out)
    return {
        "year": year, "month": month, "entity": entity or "all",
        "rows": rows_out,
        "totals": {
            "opening_balance": round(total_open, 2),
            "closing_balance": round(total_close, 2),
            "net_movement": round(total_close - total_open, 2),
        },
        "source": "gl_derived" if rows_out else "gl_derived_empty",
    }
