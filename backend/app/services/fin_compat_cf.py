"""Cash-Flow statement builders for the legacy compat layer.

Companion to ``fin_compat_pl`` (P&L) and ``fin_compat_bs`` (Balance Sheet).  Turns
the period-flow grain rows from ``fin_compat_cf_sql`` into the legacy response
shapes consumed by the verbatim-ported frontend (``StatementsPage`` with
``statement='cf'``):

  * build_cf_statement_compat → FinancialStatementResponse (month|week)
  * build_cf_consolidation    → ConsolidationResponse
  * build_cf_monthly          → MonthlyResponse (one column per month flow)
  * build_cf_narrative        → PlNarrativeResponse (deterministic)
  * build_cf_line_detail      → PlLineDetailResponse (period-flow account detail)
  * build_cf_l4_trend         → L4TrendResponse (period-flow trend)
  * build_cf_annual_compat    → ErFlowResponse (FY/YTD/LTM flow columns)

=== CF SEMANTICS (authoritative: legacy routers/financials.py /cash-flow) ===

1. FLOW STATEMENT (like the P&L).  A column is the Σ of in-period GL movements
   (py_cm/pm/cm/ytd/ytd_py, +mtd for week), already produced PRESENTED
   (``amount * -1``) by ``fin_compat_cf_sql`` — the single CF sign inversion that
   gives inflow(+)/outflow(−).  Never re-sign here.

2. MAPPED VIA ``dim_gl_cf`` (NOT level_2/3/4).  Each grain row carries the CF
   hierarchy ``l1, l2, l3, l4, l5, cf_mapping``.  A flat CF structure row
   (``dim_pl_structure`` line_code 'CF_…', kpi_code 'CF:…' / 'CF_…') is a *mapping*
   row that matches grains whose ``cf_mapping`` equals the row's ``balance_title``
   (normalised — the source data mixes the two delta glyphs U+2206 '∆' and
   U+0394 'Δ', and we treat them as equal; see :func:`_norm_cf_key`).

3. SUBTOTAL / CALC = SECTION-AWARE accumulation (mirrors the BS compat
   ``_compute_bs_running_values``, see :func:`_compute_cf_section_values`).  Each
   cash-flow section (operating / investing / financing) keeps its OWN running sum;
   detail rows accumulate into the currently-open section (the sections follow each
   other positionally — detail rows carry no section tag).  Results for the curated
   CF structure (sort_order 2000+):
       Gross cash flow                    = EBITDA + Taxes (operating intermediate)
       Cash flow from operating activities= Σ ONLY operating leaves   (CFO, standalone)
       Cash flow from investing activities= Σ ONLY investing leaves   (CFI, standalone)
       Cash flow from financing activities= Σ ONLY financing leaves   (CFF, standalone)
       Free cash flow                     = CFO + CFI
       Net cash flow                      = CFO + CFI + CFF (= Σ ALL leaves)     ✓
   The intra-operating subtotals (Gross cash flow, Δ Trade/Other/Net working
   capital, Δ Other operating items) read as the running operating cash flow up to
   that row (snapshot of the operating section sum, no reset).  Net cash flow ties
   out exactly to the Σ of every mapped CF leaf — asserted in the regression test.

=== SIGN CONVENTION SUMMARY ===
``presented = amount * -1`` (applied once in SQL).  inflow +, outflow −:
  EBITDA/income → + ; asset increase (e.g. Δ Inventories↑) → − ;
  liability increase (e.g. Δ Trade payables↑) → + .

=== WORKED EXAMPLE (cm column, section-standalone) ===
  Operating leaves (presented): EBITDA=+900, Taxes=−100, Δ Inventories=−50,
                                Δ Trade payables=+80.
  Investing leaves:             D&A=+120, Δ Fixed assets=−300.
  Financing leaves:             Δ Equity=−40.
    Gross cash flow (operating intermediate) = 900 − 100               = 800
    Cash flow from operating activities (CFO)= 900−100−50+80           = 830
    Cash flow from investing activities (CFI)= 120 − 300               = −180
    Free cash flow                           = 830 + (−180)            = 650
    Cash flow from financing activities (CFF)= −40                     = −40
    Net cash flow                            = 830 − 180 − 40          = 610
  Tie-out: Net == Σ all leaves == 610.

=== EDGE CASES ===
  * cf_mapping with no structure row (e.g. 'Δ Accounts due from affiliates',
    'Δ Loan to employees') or the exclusion tokens 'Exclude'/'Exlude' → not matched
    → contribute to NO line (dropped from the statement), matching a structure-
    driven presentation.  'Exclude'/'Exlude' are also filtered in SQL.
  * A structure row whose title matches no cf_mapping (e.g. 'Profit distributions',
    blank spacer 'CF_LINE_37') → 0 across all columns.
  * Delta-glyph mismatch ('Δ Inventories' vs '∆ Inventories') → resolved by
    :func:`_norm_cf_key`.
  * Empty grain / missing period → COALESCE(SUM,0) in SQL → 0; running sum 0.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.fin_compat_cf_sql import (
    _CF_KEYS_MONTH,
    cf_consl_grain_sql_annual_ytd,
    cf_consl_grain_sql_month,
    cf_consl_grain_sql_week,
    cf_grain_sql_annual,
    cf_grain_sql_month,
    cf_grain_sql_week,
    cf_l4_trend_sql,
    cf_monthly_grain_sql,
    cf_weekly_breakdown_sql,
)
from app.services.fin_compat_pl import (
    _ER_FLOW_KEYS,
    _deltas,
    _er_flow_deltas,
    _er_row_amounts,
    _round_am,
    _row_amounts,
    _row_dict,
)

_CF_HIER_KEYS = ["cf_l11_1", "cf_l11_2", "cf_l11_3", "cf_mapping"]
_CF_GL_FILTER = ["cf_l11_1", "cf_l11_2", "cf_l11_3", "cf_mapping"]

# Subtotal line_codes that collect preceding ``details=1`` mapping rows as children.
_CF_CLUSTER_PARENT_CODES = frozenset({
    "CF_TRADE_WORKING_CAPITAL",
    "CF_OTHER_WORKING_CAPITAL",
    "CF_OTHER_OPERATING_ITEMS",
})

# Braille / whitespace-only spacer labels from the CF Structure sheet (e.g. CF_LINE_37).
_CF_BLANK_LABEL_CHARS = frozenset(
    "\u2800\u2801\u2802\u2803\u2804\u2805\u2806\u2807\u2808\u2809\u280a\u280b"
)
from app.services.fin_compat_sql import (
    col_labels_annual,
    col_labels_month,
    col_labels_week,
    entity_sql_fragment,
    period_key,
    period_label,
    plan_anchor_for_week,
    resolve_entity_prefix,
    weekly_breakdown_layout,
)

_AM_KEYS = list(_CF_KEYS_MONTH)  # py_cm, pm, cm, ytd, ytd_py

# Unicode delta glyphs seen in the source data; unify both to a single token.
_DELTA_CHARS = "\u2206\u0394"  # ∆ (INCREMENT)  /  Δ (GREEK CAPITAL DELTA)
_DELTA_RE = re.compile(f"[{_DELTA_CHARS}]")


# ---------------------------------------------------------------------------
# CF key normalisation + structure helpers
# ---------------------------------------------------------------------------

def _norm_cf_key(s: Any) -> str:
    """Normalise a CF label/cf_mapping for matching.

    * unify the two delta glyphs (U+2206 '∆' / U+0394 'Δ') to a single token,
    * casefold, strip, collapse internal whitespace.
    So 'Δ Inventories' == '∆ Inventories', 'EBITDA' == 'ebitda'.
    """
    if s is None:
        return ""
    t = _DELTA_RE.sub("\u0394", str(s))
    t = re.sub(r"\s+", " ", t).strip().casefold()
    return t


def _load_cf_structure(session: Session) -> list[Any]:
    """Load dim_pl_structure rows (ordered by sort_order). Same query as P&L/BS."""
    return session.execute(text(
        "SELECT pl_line_id, sort_order, line_code, row_type, balance_title, details, "
        "calc_type, level_2, level_3, level_4, gl_account_id, invert_delta, is_bold, kpi_code "
        "FROM dim_pl_structure ORDER BY sort_order"
    )).fetchall()


def _is_cf_structure_row(r: dict[str, Any]) -> bool:
    """True for cash-flow rows only (kpi_code 'CF…' OR line_code 'CF_…')."""
    code = str(r.get("line_code") or "")
    kc = str(r.get("kpi_code") or "")
    return code.startswith("CF_") or kc.startswith("CF")


def _cf_struct_rows(session: Session) -> list[dict[str, Any]]:
    struct = [r for r in (_row_dict(row) for row in _load_cf_structure(session))
              if _is_cf_structure_row(r)]
    if not struct:
        raise ValueError(
            "No cash-flow rows in dim_pl_structure — run scripts/seed_cf_structure.py"
        )
    return struct


def _row_kind_for_cf(row_type: str) -> str:
    """GDPdU row_type → legacy frontend row_kind (CF variant)."""
    if row_type == "mapping":
        return "line"
    if row_type in ("subtotal", "calc", "grandtotal", "computed"):
        return "subtotal"
    return row_type


# ---------------------------------------------------------------------------
# Section-aware accumulation (mirrors fin_compat_bs._compute_bs_running_values)
# ---------------------------------------------------------------------------
# CF subtotals are SECTION-STANDALONE, not a single running sum from the top.
# The flat CF structure (dim_pl_structure, sort_order 2000+) does NOT tag detail
# rows with a section (they are all kpi_code 'CF:detail'); the section is implied
# POSITIONALLY by the three section-total rows.  So a leaf belongs to the section
# that is currently OPEN, and a section total closes that section.
#
# Role classification (deterministic, from row_type + kpi_code + balance_title):
#   * leaf          : row_type 'mapping'.
#   * grand_total   : "net cash flow"/"net change in cash" in title OR kpi 'CF:total'.
#   * cross_total   : "free cash flow" in title OR kpi 'CF_FREE_CASH_FLOW'  (= CFO+CFI).
#   * section_total : "<operating|investing|financing> activities" in title OR
#                     kpi in {CF_INV/CF:inv} (inv) / {CF_FIN/CF:fin} (fin).
#   * intra         : any other subtotal/calc (operating intermediates such as
#                     Gross cash flow / Δ Trade|Other|Net working capital /
#                     Δ Other operating items).
#
# NOTE on the seeded tags (scripts/seed_cf_structure.py): detail rows are tagged
# 'CF:detail', the investing/financing section totals are 'CF_INV'/'CF_FIN' (named
# kpi, NOT 'CF:inv'/'CF:fin'), and "Net cash flow" is 'CF:fin' (NOT 'CF:total').
# The classifier checks grand_total BEFORE the financing section_total so the
# 'CF:fin' "Net cash flow" row is correctly treated as the grand total, and uses
# the title keywords as the robust primary signal.  No structure re-seed needed.

_CF_NEXT_SECTION = {"op": "inv", "inv": "fin", "fin": "fin"}


def _cf_role_section(r: dict[str, Any]) -> tuple[str, Optional[str]]:
    """Classify a CF structure row → (role, section).

    role ∈ {'title','leaf','intra','section_total','cross_total','grand_total'};
    section ∈ {'op','inv','fin'} only for ``section_total`` (else None — a leaf's
    section is assigned positionally by the caller).
    """
    rt = r.get("row_type", "mapping")
    if rt == "title":
        return "title", None
    if rt == "mapping":
        return "leaf", None
    title = _norm_cf_key(r.get("balance_title"))
    kc = str(r.get("kpi_code") or "")
    if kc == "CF:total" or "net cash flow" in title or "net change in cash" in title:
        return "grand_total", None
    if kc == "CF_FREE_CASH_FLOW" or "free cash flow" in title:
        return "cross_total", None
    if "investing activities" in title or kc in ("CF_INV", "CF:inv"):
        return "section_total", "inv"
    if "financing activities" in title or kc in ("CF_FIN", "CF:fin"):
        return "section_total", "fin"
    if "operating activities" in title:
        return "section_total", "op"
    return "intra", None  # operating intermediate (Gross / Δ WC / Other op items)


def _compute_cf_section_values(
    struct_cf: list[dict[str, Any]],
    mapping_vals: dict[str, dict[str, float]],
    keys: list[str],
) -> dict[str, dict[str, float]]:
    """Section-aware accumulation over the ordered CF structure.

    Returns {line_code: {col: value}}:
      * leaf          → its own values; added to the OPEN section's running sum
                        and to a global cumulative.
      * intra         → snapshot of the open section's running sum (no reset; the
                        operating intermediates therefore read as the running
                        operating cash flow up to that row).
      * section_total → snapshot of its section's running sum (standalone section
                        figure: CFO = Σ operating leaves, CFI = Σ investing leaves,
                        CFF = Σ financing leaves), then RESET that section and
                        advance the open section op→inv→fin.
      * cross_total   → captured CFO + CFI (Free cash flow).
      * grand_total   → the global cumulative of ALL leaves (Net cash flow), which
                        equals CFO + CFI + CFF and guarantees the tie-out.
    All per-column independent.
    """
    run = {s: {k: 0.0 for k in keys} for s in ("op", "inv", "fin")}
    section_total = {s: {k: 0.0 for k in keys} for s in ("op", "inv", "fin")}
    cum_all = {k: 0.0 for k in keys}
    open_section = "op"
    out: dict[str, dict[str, float]] = {}

    for r in struct_cf:
        code = r["line_code"]
        role, sec = _cf_role_section(r)
        if role == "title":
            continue
        if role == "leaf":
            am = mapping_vals.get(code) or {}
            vals = {k: float(am.get(k, 0.0) or 0.0) for k in keys}
            for k in keys:
                run[open_section][k] += vals[k]
                cum_all[k] += vals[k]
            out[code] = vals
        elif role == "section_total":
            tgt = sec or open_section
            out[code] = dict(run[tgt])
            section_total[tgt] = dict(run[tgt])
            run[tgt] = {k: 0.0 for k in keys}
            open_section = _CF_NEXT_SECTION[open_section]
        elif role == "cross_total":  # Free cash flow = CFO + CFI
            out[code] = {k: section_total["op"][k] + section_total["inv"][k] for k in keys}
        elif role == "grand_total":  # Net cash flow = Σ all leaves (= CFO+CFI+CFF)
            out[code] = dict(cum_all)
        else:  # intra — snapshot of the open section running sum
            out[code] = dict(run[open_section])
    return out


def _matched_cf_grains(grains: list[dict], balance_title: Any) -> list[dict]:
    """Grains whose cf_mapping matches a structure row's balance_title (normalised)."""
    key = _norm_cf_key(balance_title)
    if not key:
        return []
    return [g for g in grains if _norm_cf_key(g.get("cf_mapping")) == key]


def _cf_drill(rep: Optional[dict]) -> dict[str, Any]:
    """Drill payload for a CF row from a representative matched grain.

    The frontend L4TrendChart reads ``cf_l11_1`` / ``cf_l11_2`` / ``cf_mapping``
    for CF; ``level_2/3/4`` mirror them for table compatibility.
    """
    rep = rep or {}
    l1 = rep.get("l1") or rep.get("cf_l11_1") or None
    l2 = rep.get("l2") or rep.get("cf_l11_2") or None
    l3 = rep.get("l3") or rep.get("cf_l11_3") or None
    cfm = rep.get("cf_mapping") or None
    return {
        "statement_type": "CF",
        "cf_l11_1": l1, "cf_l11_2": l2, "cf_l11_3": l3,
        "cf_mapping": cfm,
        "level_2": l1, "level_3": l2, "level_4": cfm,
        "gl_account_id": None,
    }


def _cf_grain_amounts(
    g: dict,
    keys: list[str],
    *,
    is_week: bool = False,
    use_er: bool = False,
    use_direct: bool = False,
) -> dict[str, float]:
    """Extract column amounts from one CF grain row."""
    if use_direct:
        return {k: float(g.get(k) or 0.0) for k in keys}
    if use_er:
        am = _er_row_amounts(g)
        return {k: float(am.get(k, 0.0)) for k in keys}
    am = _row_amounts(g, week_ctx=is_week)
    return {k: float(am.get(k, 0.0)) for k in keys}


def _cf_accounts(matched: list[dict], keys: list[str], *,
                 deltas_fn, id_prefix: str = "cf",
                 is_week: bool = False, use_er: bool = False,
                 use_direct: bool = False) -> list[dict]:
    """Account-level child rows under one CF mapping leaf (grouped by GL account)."""
    seen: dict[str, dict[str, float]] = {}
    meta: dict[str, tuple[str, str]] = {}
    for g in matched:
        ang = g.get("account_number_group") or g.get("gl_account_id") or ""
        if not ang:
            continue
        am = _cf_grain_amounts(
            g, keys, is_week=is_week, use_er=use_er, use_direct=use_direct,
        )
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
            "id": f"{id_prefix}-acc-{ang}",
            "line_code": gid,
            "label": f"{gid} | {aname}" if aname else gid,
            "row_kind": "account",
            "amounts": _round_am(am),
            "deltas": _round_am(deltas_fn(am)),
            "invert_delta": False,
            "drill": {"statement_type": "CF", "gl_account_id": gid},
        })
    out.sort(key=lambda x: abs(float((x.get("amounts") or {}).get(keys[2] if len(keys) > 2 else keys[0], 0) or 0)),
             reverse=True)
    return out


# ---------------------------------------------------------------------------
# Main statement builder (month | week)
# ---------------------------------------------------------------------------

def build_cf_statement_compat(
    session: Session,
    *,
    period_grain: str = "month",
    year: Optional[int] = None,
    month: Optional[int] = None,
    iso_year: Optional[int] = None,
    iso_week: Optional[int] = None,
    entity: Optional[str] = None,
) -> dict[str, Any]:
    """Legacy FinancialStatementResponse for the cash flow (statement='cf').

    Period-flow columns, running-sum subtotals, CF-mapping drill + account
    children.  See module docstring for FORMULA / WORKED EXAMPLE / EDGE CASES.

    period_grain='year' returns the annual ErFlowResponse shape (FY/YTD/LTM flow
    columns) — same as :func:`build_cf_annual_compat`.
    """
    if period_grain == "year":
        assert year is not None and month is not None
        out = build_cf_annual_compat(session, year=year, month=month, entity=entity)
        return {**out, "period_grain": "year"}

    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)

    is_week = period_grain == "week"
    if is_week:
        assert iso_year is not None and iso_week is not None
        sql, params = cf_grain_sql_week(iso_year, iso_week, ent_frag)
        yr, mo = plan_anchor_for_week(iso_year, iso_week)
        labels = col_labels_week(iso_year, iso_week)
        keys = list(_CF_KEYS_MONTH) + ["mtd"]
    else:
        assert year is not None and month is not None
        sql, params = cf_grain_sql_month(year, month, ent_frag)
        yr, mo = year, month
        labels = col_labels_month(year, month)
        keys = list(_CF_KEYS_MONTH)

    grains = [dict(r._mapping) for r in session.execute(text(sql), params).fetchall()]
    struct_cf = _cf_struct_rows(session)
    rows_out = _build_cf_rows(struct_cf, grains, keys, is_week=is_week)

    out: dict[str, Any] = {
        "statement": "cf",
        "period_grain": period_grain,
        "year": yr, "month": mo,
        "col_labels": labels,
        "rows": rows_out,
    }
    if iso_year is not None:
        out["iso_year"] = iso_year
    if iso_week is not None:
        out["iso_week"] = iso_week
    return out


def _is_cf_blank_label(label: Any) -> bool:
    """True for spacer rows (no visible label) from the CF Structure sheet."""
    if label is None:
        return True
    t = str(label).strip()
    if not t:
        return True
    return all(c in _CF_BLANK_LABEL_CHARS or c.isspace() for c in t)


def _sync_cf_has_children(row: dict[str, Any]) -> None:
    row["has_children"] = bool((row.get("children") or []) or (row.get("accounts") or []))


def _nest_cf_detail_rows(
    flat_rows: list[dict[str, Any]],
    struct_cf: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Nest ``details=1`` mapping rows under cluster subtotals (TWC / OWC / Other op).

    Leaves with ``details=0`` stay top-level.  A non-cluster row flushes any pending
    detail buffer to the top level first (e.g. Taxes before Gross cash flow).
    """
    details_by_code = {r["line_code"]: r.get("details") for r in struct_cf}
    out: list[dict[str, Any]] = []
    buffer: list[dict[str, Any]] = []

    for row in flat_rows:
        lc = row.get("line_code") or ""

        if row.get("row_kind") == "line" and details_by_code.get(lc) == 1:
            buffer.append(row)
            continue

        if lc in _CF_CLUSTER_PARENT_CODES:
            parent = dict(row)
            if buffer:
                parent["children"] = buffer
                buffer = []
            _sync_cf_has_children(parent)
            out.append(parent)
            continue

        out.extend(buffer)
        buffer = []
        out.append(row)

    out.extend(buffer)
    return out


def _filter_cf_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop blank spacer rows; recurse into children."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if _is_cf_blank_label(row.get("label")):
            continue
        children = row.get("children") or []
        if children:
            row = {**row, "children": _filter_cf_rows(children)}
            _sync_cf_has_children(row)
        out.append(row)
    return out


def _build_cf_rows(
    struct_cf: list[dict[str, Any]],
    grains: list[dict],
    keys: list[str],
    *,
    is_week: bool = False,
    use_er: bool = False,
    use_direct: bool = False,
    id_prefix: str = "cf",
) -> list[dict[str, Any]]:
    """Pure builder (DB-free): dim_pl_structure order, cf_mapping match, section subtotals."""
    zero = {k: 0.0 for k in keys}

    # Per-mapping leaf values (Σ matched grains) + a representative grain for drill.
    mapping_vals: dict[str, dict[str, float]] = {}
    rep_grain: dict[str, Optional[dict]] = {}
    matched_by_code: dict[str, list[dict]] = {}
    for r in struct_cf:
        if r.get("row_type") != "mapping":
            continue
        code = r["line_code"]
        matched = _matched_cf_grains(grains, r.get("balance_title"))
        matched_by_code[code] = matched
        rep_grain[code] = matched[0] if matched else None
        acc = {k: 0.0 for k in keys}
        for g in matched:
            am = _cf_grain_amounts(
                g, keys, is_week=is_week, use_er=use_er, use_direct=use_direct,
            )
            for k in keys:
                acc[k] += am.get(k, 0.0)
        mapping_vals[code] = acc

    line_vals = _compute_cf_section_values(struct_cf, mapping_vals, keys)

    def _dl(am: dict[str, float], invert: bool) -> dict[str, float]:
        if use_er:
            return _er_flow_deltas(am, invert)
        return _deltas({k: am.get(k, 0.0) for k in _AM_KEYS}, invert)

    rows_out: list[dict[str, Any]] = []
    for r in struct_cf:
        rc = r["line_code"]
        rt = r.get("row_type", "mapping")
        inv = bool(r.get("invert_delta", False))

        if rt == "title":
            rows_out.append({
                "id": f"{id_prefix}-{rc}", "parent_id": None, "line_code": rc,
                "row_kind": "title", "label": r["balance_title"],
                "amounts": None, "deltas": None, "invert_delta": False,
                "is_bold": bool(r.get("is_bold", False)),
                "drill": None, "has_children": False, "children": [],
            })
            continue

        am = {k: float(line_vals.get(rc, zero).get(k, 0.0)) for k in keys}
        accounts: list[dict] = []
        drill: Optional[dict] = None
        if rt == "mapping":
            drill = _cf_drill(rep_grain.get(rc))
            accounts = _cf_accounts(
                matched_by_code.get(rc, []), keys,
                deltas_fn=lambda a: _dl(a, inv),
                id_prefix=id_prefix,
                is_week=is_week, use_er=use_er, use_direct=use_direct,
            )

        rows_out.append({
            "id": f"{id_prefix}-{rc}", "parent_id": None, "line_code": rc,
            "row_kind": _row_kind_for_cf(rt),
            "label": r["balance_title"],
            "amounts": _round_am(am),
            "deltas": _dl(am, inv),
            "invert_delta": inv,
            "is_bold": bool(r.get("is_bold", False)),
            "drill": drill,
            "has_children": len(accounts) > 0,
            "children": [],
            "accounts": accounts,
        })
    rows_out = _nest_cf_detail_rows(rows_out, struct_cf)
    return _filter_cf_rows(rows_out)


# ---------------------------------------------------------------------------
# Consolidation builder (per-entity cm flow)
# ---------------------------------------------------------------------------

def build_cf_consolidation(
    session: Session,
    *,
    period_grain: str = "month",
    year: Optional[int] = None,
    month: Optional[int] = None,
    iso_year: Optional[int] = None,
    iso_week: Optional[int] = None,
) -> dict[str, Any]:
    """ConsolidationResponse for the cash flow — per-entity cm flow + running-sum
    subtotals per entity column; aggregated / ic_eliminations(0) / consolidation."""
    if period_grain == "week":
        assert iso_year is not None and iso_week is not None
        sql, params = cf_consl_grain_sql_week(iso_year, iso_week)
        yr, mo = plan_anchor_for_week(iso_year, iso_week)
        labels = col_labels_week(iso_year, iso_week)
    elif period_grain == "year":
        assert year is not None and month is not None
        sql, params = cf_consl_grain_sql_annual_ytd(year, month)
        yr, mo = year, month
        labels = col_labels_annual(year, month)
    else:
        assert year is not None and month is not None
        sql, params = cf_consl_grain_sql_month(year, month)
        yr, mo = year, month
        labels = col_labels_month(year, month)

    grains = [dict(r._mapping) for r in session.execute(text(sql), params).fetchall()]

    ent_rows = session.execute(text(
        "SELECT legal_entity_code, entity_prefix, entity_name FROM dim_legal_entity "
        "ORDER BY legal_entity_code"
    )).fetchall()
    entity_codes = [r[0] for r in ent_rows]
    ep_to_code = {r[1]: r[0] for r in ent_rows}
    entity_dicts = [{"code": r[0], "label": r[2]} for r in ent_rows]

    struct_cf = _cf_struct_rows(session)

    def _consl_amounts(matched: list[dict]) -> dict[str, float]:
        sums: dict[str, float] = {ec: 0.0 for ec in entity_codes}
        for g in matched:
            lec = ep_to_code.get(g.get("entity_prefix") or "")
            if lec is None:
                continue
            sums[lec] = sums.get(lec, 0.0) + float(g.get("cm") or 0.0)
        return sums

    mapping_entity: dict[str, dict[str, float]] = {}
    for r in struct_cf:
        if r.get("row_type") != "mapping":
            continue
        mapping_entity[r["line_code"]] = _consl_amounts(
            _matched_cf_grains(grains, r.get("balance_title"))
        )

    line_entity = _compute_cf_section_values(struct_cf, mapping_entity, entity_codes)

    def _consl_row(rid: str, label: str, row_kind: str, is_bold: bool,
                   am: dict[str, float]) -> dict:
        agg = sum(am.get(ec, 0.0) for ec in entity_codes)
        return {
            "id": rid, "label": label, "row_kind": row_kind, "is_bold": is_bold,
            "entity_amounts": {ec: round(am.get(ec, 0.0), 2) for ec in entity_codes},
            "aggregated": round(agg, 2),
            "ic_eliminations": 0.0,
            "consolidation": round(agg, 2),
            "has_children": False, "children": [],
        }

    rows_out: list[dict[str, Any]] = []
    for r in struct_cf:
        rt = r.get("row_type", "mapping")
        rc = r["line_code"]
        if rt == "title":
            rows_out.append(_consl_row(f"cf-{rc}", r["balance_title"], "title",
                                       bool(r.get("is_bold", False)),
                                       {ec: 0.0 for ec in entity_codes}))
            continue
        am = line_entity.get(rc, {ec: 0.0 for ec in entity_codes})
        rows_out.append(_consl_row(f"cf-{rc}", r["balance_title"], _row_kind_for_cf(rt),
                                   bool(r.get("is_bold", False)), am))

    col_label = (
        labels.get("ytd", period_label(yr, mo))
        if period_grain == "year"
        else labels.get("cm", period_label(yr, mo))
    )
    out: dict[str, Any] = {
        "statement": "cf",
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
# Monthly view builder (one period-flow column per month)
# ---------------------------------------------------------------------------

def build_cf_monthly(
    session: Session,
    *,
    year: int,
    month: int,
    entity: Optional[str] = None,
    span: str = "12m",
) -> dict[str, Any]:
    """MonthlyResponse for the cash flow — each ``amounts[YYYY-MM]`` is that month's
    CF flow; running-sum subtotals per column.  span='fy3' adds FY/YTD flow totals.
    """
    from app.services.fin_compat_sql import _fy_span_periods, _fy_span_totals, _last_12_periods

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
    zero = {k: 0.0 for k in all_keys}

    sql, params = cf_monthly_grain_sql(year, month, ent_frag, span=effective_span)
    grains = [dict(r._mapping) for r in session.execute(text(sql), params).fetchall()]
    struct_cf = _cf_struct_rows(session)
    rows_out = _build_cf_rows(struct_cf, grains, all_keys, use_direct=True)

    out: dict[str, Any] = {
        "statement": "cf",
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
# Weekly-breakdown view builder
# ---------------------------------------------------------------------------

def build_cf_weekly_breakdown(
    session: Session,
    *,
    iso_year: int,
    iso_week: int,
    entity: Optional[str] = None,
) -> dict[str, Any]:
    """Weekly CF breakdown: M-2 / M-1 full months + M0 partial, by ISO week.

    Same column layout and response envelope as
    :func:`fin_compat_pl.build_pl_weekly_breakdown`, but rows follow the flat CF
    structure (``dim_pl_structure`` sort_order 2000+) with section-aware
    subtotals via :func:`_compute_cf_section_values`.
    """
    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)

    layout = weekly_breakdown_layout(iso_year, iso_week)
    all_keys: list[str] = []
    for g in layout:
        all_keys.extend(w["key"] for w in g["weeks"])
        all_keys.append(g["total"]["key"])

    sql, params = cf_weekly_breakdown_sql(layout, ent_frag)
    grains = [dict(r._mapping) for r in session.execute(text(sql), params).fetchall()]
    struct_cf = _cf_struct_rows(session)
    rows_out = _build_cf_rows(struct_cf, grains, all_keys, use_direct=True, id_prefix="cf")

    groups_resp = [
        {
            "month_key": g["month_key"],
            "month_label": g["month_label"],
            "kind": g["kind"],
            "weeks": [{"key": w["key"], "label": w["label"]} for w in g["weeks"]],
            "total": {
                "key": g["total"]["key"],
                "label": g["total"]["label"],
                "kind": g["total"]["kind"],
            },
        }
        for g in layout
    ]

    return {
        "statement": "cf",
        "iso_year": iso_year,
        "iso_week": iso_week,
        "groups": groups_resp,
        "rows": rows_out,
    }


# ---------------------------------------------------------------------------
# L4 trend (period-flow at each window)
# ---------------------------------------------------------------------------

def build_cf_l4_trend(
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
    """L4TrendResponse for a cash-flow position — the in-window flow at each point
    (presented, ``amount*-1``).  Filters cf.l1=level_2, cf.l2=level_3,
    cf.cf_mapping=level_4 (the CF drill keys forwarded by the frontend)."""
    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)

    sql, windows = cf_l4_trend_sql(year, month, grain, level_2, level_3, level_4, ent_frag)
    rows = session.execute(text(sql)).fetchall()
    if not rows:
        return {"series": [], "col_label": period_label(year, month), "prev_label": ""}

    row = dict(rows[0]._mapping) if hasattr(rows[0], "_mapping") else dict(rows[0])
    series = []
    for w in windows:
        cur = float(row.get(w["pk"]) or 0.0)
        prev = float(row.get(w["prev_pk"]) or 0.0)
        delta = round((cur - prev) / abs(prev) * 100, 1) if abs(prev) > 1e-6 else None
        series.append({
            "label": w["label"], "current": round(cur, 2), "previous": round(prev, 2),
            "delta_pct": delta, "date_from": w["date_from"], "date_to": w["date_to"],
        })
    prev_end = windows[-1].get("prev_end")
    prev_label = prev_end.strftime("%b %Y") if hasattr(prev_end, "strftime") else (
        str(prev_end) if prev_end else "")
    return {"series": series, "col_label": period_label(year, month), "prev_label": prev_label}


# ---------------------------------------------------------------------------
# Annual exit-readiness flow builder (FY / YTD / LTM columns)
# ---------------------------------------------------------------------------

def build_cf_annual_compat(
    session: Session,
    *,
    year: int,
    month: int,
    entity: Optional[str] = None,
) -> dict[str, Any]:
    """ErFlowResponse for the cash flow — FY/YTD/LTM flow columns + running-sum
    subtotals.  Same shape as :func:`fin_compat_pl.build_pl_annual_compat`, with
    ``statement='cf'`` and CF-mapping matching.  CF is a FLOW (uses the annual flow
    grain, NOT a snapshot)."""
    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)

    sql, params = cf_grain_sql_annual(year, month, ent_frag)
    grains = [dict(r._mapping) for r in session.execute(text(sql), params).fetchall()]
    struct_cf = _cf_struct_rows(session)
    rows_out = _build_cf_rows(
        struct_cf,
        grains,
        list(_ER_FLOW_KEYS),
        use_er=True,
        id_prefix="er-cf",
    )

    return {
        "statement": "cf",
        "year": year, "month": month,
        "col_labels": col_labels_annual(year, month),
        "rows": rows_out,
    }


# ---------------------------------------------------------------------------
# Line detail (period-flow account breakdown for one CF leaf)
# ---------------------------------------------------------------------------

_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _resolve_cf_row(session: Session, line_code: str) -> Optional[dict]:
    base = line_code.split("::")[0].strip()
    row = session.execute(text(
        "SELECT line_code, row_type, balance_title, kpi_code "
        "FROM dim_pl_structure WHERE line_code = :lc LIMIT 1"
    ), {"lc": base}).fetchone()
    return dict(row._mapping) if row else None


def build_cf_line_detail(
    session: Session,
    line_code: str,
    year: int, month: int, entity: Optional[str],
    *,
    limit: int = 50,
    timeline_months: int = 12,
    use_llm: bool = True,
    line_mom_keur: Optional[float] = None,
) -> dict[str, Any]:
    """PlLineDetailResponse for a CF line — period-FLOW account detail.

    Scopes the GL to the line's cf_mapping (via ``dim_gl_cf``), presented
    (``amount*-1``).  Accounts carry the CM / PM period flow; top bookings are the
    current-month postings; the timeline is a 12-month flow series.  Same response
    shape as the P&L / BS line detail.
    """
    from app.services.fin_compat_sql import _last_12_periods, last_day, pm as _pm

    row = _resolve_cf_row(session, line_code)
    if not row:
        raise ValueError(f"Unknown CF line_code: {line_code}")
    cf_title = row.get("balance_title") or line_code

    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)
    # cf_mapping match is delta-glyph tolerant: compare normalised on both sides.
    norm = _norm_cf_key(cf_title)

    pm_y, pm_m = _pm(year, month)
    py = year - 1

    base_join = """
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_cf cf
          ON cf.account_number_group = l.account_number_group
         AND cf.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
    """
    # Normalise cf_mapping in SQL the same way (unify delta glyphs, lower, trim).
    cfm_norm = (
        "lower(btrim(translate(cf.cf_mapping, '\u2206', '\u0394'))) = :cfm"
    )

    # Account flow (CM / PM) presented.
    acc_sql = text(f"""
        SELECT
            l.account_number_group,
            MAX(a.gl_account_id) AS gl_account_id,
            MAX(a.account_name)  AS account_name,
            COALESCE(SUM(CASE WHEN e.fiscal_year = {year} AND e.fiscal_period = {month}
                         THEN l.amount * -1 ELSE 0 END), 0)::float8 AS flow_cm,
            COALESCE(SUM(CASE WHEN e.fiscal_year = {pm_y} AND e.fiscal_period = {pm_m}
                         THEN l.amount * -1 ELSE 0 END), 0)::float8 AS flow_pm
        {base_join}
        WHERE {cfm_norm}
          AND e.fiscal_year = ANY(:years)
          AND e.fiscal_period BETWEEN 1 AND 12
          {ent_frag}
        GROUP BY l.account_number_group
        HAVING ABS(COALESCE(SUM(CASE WHEN e.fiscal_year = {year} AND e.fiscal_period = {month}
                 THEN l.amount * -1 ELSE 0 END), 0)) > 0.01
        ORDER BY ABS(COALESCE(SUM(CASE WHEN e.fiscal_year = {year} AND e.fiscal_period = {month}
                 THEN l.amount * -1 ELSE 0 END), 0)) DESC
        LIMIT 30
    """)
    acc_rows = session.execute(
        acc_sql, {"cfm": norm, "years": sorted({py, pm_y, year})}
    ).fetchall()
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

    # Top bookings posted in the current month (presented amount).
    ja = f"{year}-{month:02d}-01"
    d_cm = last_day(year, month).isoformat()
    bk_sql = text(f"""
        SELECT
            l.booking_line_id,
            e.posting_date::text AS posting_date,
            SUBSTRING(l.journal_entry_group_number FROM 3) AS journal_entry_number,
            le.legal_entity_code,
            l.fiscal_year,
            MAX(a.gl_account_id) AS gl_account_id,
            MAX(a.account_name)  AS account_name,
            (l.amount * -1)::float8 AS amount,
            l.line_note,
            e.reference_document_number
        {base_join}
        LEFT JOIN dim_legal_entity le ON le.entity_prefix = l.entity_prefix
        WHERE {cfm_norm}
          AND e.posting_date BETWEEN :ja AND :d_cm
          {ent_frag}
        GROUP BY l.booking_line_id, e.posting_date, l.journal_entry_group_number,
                 le.legal_entity_code, l.fiscal_year, l.amount, l.line_note,
                 e.reference_document_number
        ORDER BY ABS(l.amount) DESC
        LIMIT :lim
    """)
    bk_rows = session.execute(
        bk_sql, {"cfm": norm, "ja": ja, "d_cm": d_cm, "lim": limit}
    ).fetchall()
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

    # 12-month flow timeline (one flow per month) for the top accounts.
    periods = _last_12_periods(year, month)[-timeline_months:]
    period_defs = [
        {"year": y, "month": m,
         "label": f"{_MONTH_ABBR[m - 1]}{str(y)[-2:]}", "key": period_key(y, m)}
        for y, m in periods
    ]
    tl_cases = " ".join(
        f"COALESCE(SUM(CASE WHEN e.fiscal_year = {y} AND e.fiscal_period = {m} "
        f"THEN l.amount * -1 ELSE 0 END),0) AS \"{period_key(y, m)}\","
        for y, m in periods
    ).rstrip(",")
    tl_years = sorted({y for y, _ in periods})
    tl_sql = text(f"""
        SELECT
            l.account_number_group,
            MAX(a.gl_account_id) AS gl_account_id,
            MAX(a.account_name)  AS account_name,
            {tl_cases}
        {base_join}
        WHERE {cfm_norm}
          AND e.fiscal_year = ANY(:years)
          AND e.fiscal_period BETWEEN 1 AND 12
          {ent_frag}
        GROUP BY l.account_number_group
        ORDER BY ABS(COALESCE(SUM(CASE WHEN e.fiscal_year = {year} AND e.fiscal_period = {month}
                 THEN l.amount * -1 ELSE 0 END),0)) DESC
        LIMIT 10
    """)
    tl_rows = session.execute(tl_sql, {"cfm": norm, "years": tl_years}).fetchall()
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

    commentary = _cf_commentary(accounts_out, top_bookings)
    return {
        "line_code": line_code,
        "label": cf_title,
        "year": year, "month": month, "entity": entity,
        "accounts": accounts_out,
        "top_bookings": top_bookings,
        "bridge": [{"label": a["account_name"] or a["gl_account_id"], "value": a["balance_cm"]}
                   for a in accounts_out[:12]],
        "periods": period_defs,
        "accounts_timeline": accounts_timeline,
        "sub_lines": [],
        "commentary": commentary,
        "outlier_facts": {},
        "suggested_prompts": [],
        "meta": {"llm_used": False, "algorithm_version": "cf_outliers_v1"},
    }


def _cf_commentary(accounts: list[dict], bookings: list[dict]) -> dict[str, str]:
    if not accounts:
        return {"accounts": "No GL accounts in scope for this cash-flow line.", "postings": ""}
    top_3 = sorted(accounts, key=lambda a: abs(a["balance_cm"]), reverse=True)[:3]
    acc_text = "Top contributors this period: " + "; ".join(
        f"{a['account_name'] or a['gl_account_id']} (€{a['balance_cm']:.0f}k)" for a in top_3
    )
    post_text = ""
    if bookings:
        largest = bookings[0]
        post_text = (f"Largest movement: {largest['gl_account_id']} "
                     f"€{largest['amount']:.0f}k on {largest['posting_date']}.")
    return {"accounts": acc_text, "postings": post_text}


# ---------------------------------------------------------------------------
# Deterministic narrative (PlNarrativeResponse shape) for the cash flow
# ---------------------------------------------------------------------------

def build_cf_narrative(
    session: Session,
    year: int, month: int, entity: Optional[str],
    *,
    period_grain: str = "month",
    iso_year: Optional[int] = None,
    iso_week: Optional[int] = None,
    max_bullets: Optional[int] = None,
    use_llm: bool = False,
) -> dict[str, Any]:
    """Deterministic CF key-drivers narrative at legacy depth (narrative core)."""
    import os
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
            "cf",
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

    net_row = next(
        (r for r in rows if "net cash flow" in (r.get("label") or "").lower()),
        None,
    )
    net_am = (net_row or {}).get("amounts", {}) or {}
    net_cm = float(net_am.get("cm") or 0)
    net_mom = float(net_cm - float(net_am.get("pm") or 0))
    base_cm = abs(net_cm) or abs(sum(
        float((r.get("amounts") or {}).get("cm") or 0)
        for r in rows if r.get("row_kind") == "line"
    )) or 1.0

    def _gl_detail(line_code: str, signed_mom_eur: float) -> Optional[dict]:
        try:
            return build_cf_line_detail(
                session, line_code, yr, mo, entity,
                use_llm=False, line_mom_keur=signed_mom_eur / 1000.0,
            )
        except Exception:
            return None

    def _anchor_boost(lc: str) -> float:
        if lc.upper() in ("NCF", "NET_CF", "NET_CASH_FLOW"):
            return 30.0
        if lc.upper().startswith("CFO") or "OPERATING" in lc.upper():
            return 20.0
        return 0.0

    drivers = core.analyze_statement(
        rows,
        base_cm=base_cm,
        cap=cap,
        anchor_boost_fn=_anchor_boost,
        gl_detail_fn=_gl_detail,
    )

    group_label = entity or "Group"
    ctx = core.ProseContext(
        statement_kind="cf",
        period_label=cm_label,
        prior_label=pm_label,
        base_label="net cash flow",
        base_cm=base_cm,
        balance_style=False,
        tone_mode="favorable",
        movement_noun="the move",
        period_grain=period_grain,
    )
    bullets = core.build_bullets(drivers, ctx)

    primary_drivers: list[dict] = []
    for f in sorted(drivers, key=lambda x: x["score"], reverse=True)[:3]:
        dm = f["display_mom"]
        primary_drivers.append({
            "label": f["label"],
            "delta": round(dm, 2),
            "direction": _direction(dm),
        })

    if abs(net_mom) < 0.5:
        headline = f"{group_label} cash flow at {cm_label}"
    else:
        dir_word = "rose" if net_mom > 0 else "fell"
        lead = f", led by {primary_drivers[0]['label']}" if primary_drivers else ""
        headline = f"{group_label} net cash flow {dir_word} in {cm_label}{lead}"[:120]

    intro = f"Net cash flow in {cm_label} was {core.fmt_keur(net_cm)}"
    if abs(net_mom) >= 0.5:
        td = "up" if net_mom > 0 else "down"
        intro += f", {td} {core.fmt_keur(abs(net_mom))} from {pm_label}"
    intro += "."
    if primary_drivers:
        d0 = primary_drivers[0]
        intro += (
            f" The largest driver was {core.lower_first(d0['label'])} "
            f"({core.fmt_keur_signed(d0['delta'])})."
        )
    intro += " Key drivers consist of:"

    llm_used = False
    if use_llm and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from app.services.fin_compat_narrative import _llm_enhance
            headline, intro, bullets, llm_used = _llm_enhance(
                headline, intro, bullets, cm_label, group_label,
            )
        except Exception:
            pass

    return {
        "headline": headline,
        "intro": intro,
        "intro_facts": {
            "period_label": cm_label, "group_label": group_label,
            "net_profit_ytd": 0.0, "coverage_pct": None,
            "cm_month_label": cm_label, "cm_vs_plan": 0.0, "cm_vs_plan_qualifier": "",
            "primary_drivers": primary_drivers, "entity_split": None,
        },
        "bullets": bullets,
        "entity_split": None,
        "meta": {
            "algorithm_version": "cf_narrative_compat_v2", "llm_used": llm_used,
            "max_bullets": cap, "cache_hit": False,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "entity_scope": entity or "",
        },
    }
