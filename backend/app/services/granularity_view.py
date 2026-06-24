"""Full-structure multi-period statement for the Budget granularity view.

Powers ``GET /api/v1/financials/budget/granularity-view``.  The Budget-chat
granularity panel renders the REAL Reporting statement structure (Income
Statement / Balance Sheet — subtotals, grandtotals, the L3→L4→account drill)
with a multi-period column set, so the user sees the same hierarchy they read in
Reporting and can pick the granularity to plan at:

  * grain='month' → the last 24 ledger month columns (labels "Jul25A" …).
  * grain='year'  → the last up-to-3 COMPLETE fiscal years ONLY — NO partial/YTD
                    column.  ANNUAL planning must never base a plan on a partial
                    (year-to-date) year, so the current in-progress fiscal year is
                    DROPPED.  A fiscal year is COMPLETE when it is strictly before
                    the current (latest-anchor) fiscal year (or has all 12 months
                    present); since every annual column the builders emit is for a
                    year < anchor year, all are complete.
                      - PL year: FY(y-3), FY(y-2), FY(y-1) — labels "FY22A",
                        "FY23A","FY24A" (the historical actual columns the Reporting
                        Income Statement shows).  The builder's partial ``ytd``
                        column is DROPPED.
                      - BS year: Dec(y-3), Dec(y-2), Dec(y-1) period-END balances —
                        labels "Dec22A","Dec23A","Dec24A" matching the Reporting
                        Balance Sheet's closing columns.  The builder's partial
                        ``cm`` (YTD month-end) column is DROPPED.
                    The FE appends the PLAN year columns after these historical
                    actuals.

================================================================================
WHAT THIS IS (and what it REUSES — no reinvention of statement math)
================================================================================
The full statement structure (subtotals / grandtotals / KPIs + L4→account
nesting) is produced ENTIRELY by the existing multi-period statement builders;
this module is a thin adapter that windows the columns, drops the rows we do not
plan by, and re-keys the per-period ``amounts{periodKey}`` maps into the
``values[]`` arrays the FE table renders.

  * P&L month  → ``fin_compat_pl.build_pl_monthly(span='fy3')`` windowed to the
                 last 24 month columns (the FY/YTD total columns are dropped).
  * P&L year   → ``fin_compat_pl.build_pl_annual_compat`` → the 3 COMPLETE FY
                 columns ``fy1``/``fy2``/``fy3`` (FY y-3 / y-2 / y-1).  The partial
                 ``ytd`` column is DROPPED — annual planning never uses a partial year.
  * BS  month  → ``fin_compat_bs.build_bs_monthly(span='12m'/'fy3')`` windowed to
                 the last 24 month-END balance columns.
  * BS  year   → ``fin_compat_bs.build_bs_snapshot_annual`` → the 3 COMPLETE FY-end
                 balance columns ``dec_py2``/``fy_py``/``fy`` (Dec y-3 / y-2 / y-1).
                 The partial ``cm`` (current-year YTD month-end) column is DROPPED.

Each builder emits rows with ``row_kind`` ('line'=mapping, 'subtotal'=
subtotal/calc/grandtotal/computed, 'kpi', 'title') and already nests ``children``
(L4) and ``accounts`` under the L3 mapping rows in ONE SQL pull.  We:

  1. KEEP mapping ('line') + 'subtotal'/'grandtotal' rows so the statement
     structure (Total output, Gross profit, EBITDA, EBIT, EBT, Net profit;
     BS Assets / Current-assets groups) survives in order.
  2. DROP ``row_kind in ('kpi','title')`` — ratios (``*_PCT``) and section
     headers are not plannable positions.
  3. Annotate the plannable mapping ('line') rows with the budget flags
     (``plannable`` / ``is_partner_driven`` / ``partner_kind`` / ``has_l4``) by
     joining ``budget_service._load_positions`` + ``budget_positions`` on
     ``line_code``.

================================================================================
SIGN CONVENTION (presented — exactly as the builders produce it)
================================================================================
PL values are PRESENTED flows (revenue +, expense −); BS values are PRESENTED
period-END balances (assets +, equity/liabilities flipped to + in their section,
net profit injected into equity).  This module never re-signs — it copies the
builder's presented amounts straight into ``values[]``.

This endpoint is ADDITIVE, READ-ONLY and NOT in the golden catalogue; it reuses
the statement builders unchanged (only their golden-safe ``ent_frag_override``
kwarg) and changes no existing payload.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from app.services import budget_positions
from app.services import budget_service
from app.services.fin_compat_bs import build_bs_monthly, build_bs_snapshot_annual
from app.services.fin_compat_pl import build_pl_annual_compat, build_pl_monthly
from app.services.fin_compat_sql import (
    col_labels_annual,
    entities_sql_fragment,
    entity_sql_fragment,
    label_actual,
    period_label,
    resolve_entity_prefix,
)
from app.services.gl_analysis_common import latest_anchor

_ROUND = 3
_MONTHS = 24

# Structural builder row_kinds we KEEP (everything else — 'kpi','title',
# 'kpi_header', and bare 'account'/'detail' at the top level — is dropped).  We
# present depth-0 BS structural rows (Assets / Equity & liabilities) as
# 'grandtotal' and deeper ones as 'subtotal'.
_KEEP_KINDS = {"line", "subtotal", "grandtotal"}


# =========================================================================== #
# Entity scope (fail-closed) — resolve to a builder ``ent_frag``
# =========================================================================== #
def _resolve_ent_frag(
    session: Session,
    *,
    entity: Optional[str],
    entity_prefixes: Optional[set[str]],
) -> str:
    """Resolve the caller's scope to a builder SQL fragment.

    * ``entity_prefixes is None`` (admin/unrestricted): scope is whatever
      ``entity`` resolves to (a single prefix, or '' = consolidated over all).
    * ``entity_prefixes`` provided (restricted user): scope is the INTERSECTION
      of the requested ``entity`` with the caller's own prefixes — fail-closed.
      An explicit ``entity`` outside the allowed set, or an empty intersection,
      yields a fragment that matches NOTHING (never widens to the whole ledger).
    """
    if entity_prefixes is None:
        ep = resolve_entity_prefix(session, entity)
        return entity_sql_fragment(ep)

    allowed = {str(p).strip()[:2] for p in entity_prefixes if p}
    allowed.discard("")
    if not allowed:
        # Restricted but no resolvable prefixes → match nothing (fail-closed).
        return "AND 1 = 0"

    requested = resolve_entity_prefix(session, entity)
    if requested is not None:
        # Explicit single entity: allow only if it is within the caller's scope.
        return entity_sql_fragment(requested) if requested[:2] in allowed else "AND 1 = 0"

    # No explicit entity → consolidate over the caller's OWN prefixes only.
    return entities_sql_fragment(sorted(allowed))


# =========================================================================== #
# Period columns + per-period value extraction (PURE, DB-free)
# =========================================================================== #
def _month_columns(periods: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Builder ``periods`` (each {year, month, label}) → last-24 ``[{key,label}]``.

    ``key`` is the builder's amounts key "YYYY-MM"; ``label`` carries the actual
    'A' suffix (e.g. "Jul25A").
    """
    tail = periods[-_MONTHS:]
    return [
        {
            "key": f"{int(p['year']):04d}-{int(p['month']):02d}",
            "label": label_actual(period_label(int(p["year"]), int(p["month"]))),
        }
        for p in tail
    ]


def pl_year_columns(year: int, month: int) -> list[dict[str, str]]:
    """PL annual columns: the last up-to-3 COMPLETE fiscal years — NO partial/YTD.

    Annual planning must never base a plan on a partial (year-to-date) year, so the
    current in-progress fiscal year (``year``, only Jan..``month`` booked) is NOT a
    column.  ``build_pl_annual_compat`` / ``pl_grain_sql_annual`` aggregate four FY
    columns — ``fy1``=FY(year-3), ``fy2``=FY(year-2), ``fy3``=FY(year-1) (all full,
    12-period sums) and ``ytd``=YTD(month) (the PARTIAL current year).  Every FY
    column is for a year strictly BEFORE the current (anchor) fiscal year ``year``,
    so all three are COMPLETE; we return them and DROP the partial ``ytd``.

    Labels are the historical-actual "FYyyA" form the Reporting Income Statement
    shows; ``fy2``/``fy3`` are taken VERBATIM from ``col_labels_annual`` (no label
    drift), ``fy1`` follows the same convention.  E.g. for year=2025/month=7:
    FY22A (fy1), FY23A (fy2), FY24A (fy3) — no YTDJul25A column.
    """
    labels = col_labels_annual(year, month)
    return [
        {"key": "fy1", "label": label_actual(f"FY{str(year - 3)[-2:]}")},
        {"key": "fy2", "label": labels["fy2"]},
        {"key": "fy3", "label": labels["fy3"]},
    ]


def bs_year_columns(year: int, month: int) -> list[dict[str, str]]:
    """BS annual columns: the last up-to-3 COMPLETE fiscal-year-END balances — NO
    partial/YTD column (aligned to the ``build_bs_snapshot_annual`` amounts keys
    ``dec_py2``/``fy_py``/``fy``).

    Each column is a period-END (Dec 31) closing balance of a COMPLETE fiscal year:
    ``dec_py2`` = Dec(year-3) close, ``fy_py`` = Dec(year-2) close, ``fy`` =
    Dec(year-1) close.  The builder's ``cm`` (current-year YTD month-end balance) is
    a PARTIAL year and is DROPPED — annual planning never uses a partial year.

    Labels follow the period-end "Dec<yy>A" convention matching the Reporting
    Balance Sheet's closing columns (``col_labels_bs_snapshot``); e.g. for
    year=2025/month=7: Dec22A (dec_py2), Dec23A (fy_py), Dec24A (fy).
    """
    return [
        {"key": "dec_py2", "label": label_actual(f"Dec{str(year - 3)[-2:]}")},
        {"key": "fy_py", "label": label_actual(f"Dec{str(year - 2)[-2:]}")},
        {"key": "fy", "label": label_actual(f"Dec{str(year - 1)[-2:]}")},
    ]


def _values(amounts: Optional[dict[str, Any]], periods: list[dict[str, str]]) -> list[float]:
    """Project a builder ``amounts{key}`` map onto ``periods`` order as floats.

    Missing key / ``None`` amounts (title rows) → 0.0 per column.
    """
    am = amounts if isinstance(amounts, dict) else {}
    return [round(float(am.get(p["key"]) or 0.0), _ROUND) for p in periods]


# =========================================================================== #
# Row classification + flattening of the builder's structured tree
# =========================================================================== #
def _kind_for(row: dict[str, Any]) -> Optional[str]:
    """Map a builder row to the granularity kind, or None to DROP it.

    KEEP: 'line' (plannable mapping), 'subtotal'/'grandtotal' (structural).
    DROP: 'kpi', 'title', 'kpi_header', and any non-structural top-level kind.
    """
    rk = str(row.get("row_kind") or "")
    if rk not in _KEEP_KINDS:
        return None
    return "line" if rk == "line" else "subtotal"


def _code_from(row: dict[str, Any]) -> str:
    """Best structure line_code for a builder row.

    PL annual rows carry ``line_code`` directly (e.g. 'NET_SALES'); PL monthly rows
    only carry ``id`` ('pl-NET_SALES' / 'er-pl-NET_SALES'), so strip the prefix.
    BS hierarchy rows carry a node-id (e.g. 'er-bs-d2-…'); those never resolve to a
    structure code and are matched by their LEVEL_3 LABEL instead (see assemble).
    """
    code = str(row.get("line_code") or "")
    if code and not code.startswith(("pl-", "er-pl-", "bs-", "er-bs-")):
        return code
    rid = str(row.get("id") or "")
    for pre in ("er-pl-", "pl-"):
        if rid.startswith(pre):
            return rid[len(pre):]
    return ""


def _account_node(acc: dict[str, Any], periods: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "gl_account_id": acc.get("gl_account_id") or acc.get("line_code") or "",
        "label": acc.get("label") or acc.get("line_code") or "",
        "values": _values(acc.get("amounts"), periods),
    }


def _collect_accounts(node: dict[str, Any], periods: list[dict[str, str]]) -> list[dict[str, Any]]:
    """All ``account`` leaves under a node (recursing through any L4 layer)."""
    out: list[dict[str, Any]] = []
    for ch in node.get("children") or []:
        if str(ch.get("row_kind") or "") == "account":
            out.append(_account_node(ch, periods))
        else:
            out.extend(_collect_accounts(ch, periods))
    for a in node.get("accounts") or []:
        out.append(_account_node(a, periods))
    return out


def _l4_child_node(child: dict[str, Any], periods: list[dict[str, str]]) -> dict[str, Any]:
    """An L4 grouping child with its OWN nested accounts (L3 → L4 → account)."""
    label = str(child.get("level_4") or child.get("label") or "").strip()
    return {
        "level_4": label,
        "label": child.get("label") or label,
        "values": _values(child.get("amounts"), periods),
        "accounts": _collect_accounts(child, periods),
    }


def _mapping_children(
    row: dict[str, Any], periods: list[dict[str, str]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Split a mapping ('line') row's builder children into (L4 children, accounts).

    A child is an ACCOUNT leaf (``row_kind`` 'account') → flat account list; or an
    L4 grouping (any other kind that itself contains accounts) → an L4 child node
    carrying its own nested accounts.  Direct ``accounts`` on the row are hoisted
    no-L4 accounts.  ``has_l4`` is true iff at least one L4 grouping child exists.
    """
    l4_children: list[dict[str, Any]] = []
    accounts: list[dict[str, Any]] = []
    for ch in row.get("children") or []:
        if str(ch.get("row_kind") or "") == "account":
            accounts.append(_account_node(ch, periods))
        else:
            l4_children.append(_l4_child_node(ch, periods))
    for a in row.get("accounts") or []:
        accounts.append(_account_node(a, periods))
    return l4_children, accounts, len(l4_children) > 0


# =========================================================================== #
# PURE assembly — builder tree + plannable set → flat granularity rows
# =========================================================================== #
def assemble_rows(
    builder_rows: list[dict[str, Any]],
    periods: list[dict[str, str]],
    plannable_by_code: dict[str, str],
    plannable_by_l3: Optional[dict[str, str]] = None,
) -> list[dict[str, Any]]:
    """Flatten the builder's (possibly nested) row tree into ordered granularity
    rows with an ``indent`` per hierarchy depth (PURE, DB-free).

    ``builder_rows``     : the statement builder's ``rows`` (statement order).  PL is
                           a flat list (subtotals + mapping lines, L4 children
                           inline); BS is a nested tree (Assets/E&L → groups → L3
                           mapping lines → accounts) — both are handled uniformly.
    ``periods``          : the column set (``[{key,label}]``) values align to.
    ``plannable_by_code``: {line_code: level_3} — the plannable mapping set
                           (``budget_service._load_positions``); matches PL rows by
                           code.
    ``plannable_by_l3``  : {level_3_label: line_code} — used to resolve BS mapping
                           ('line') rows whose ``line_code`` is a hierarchy node-id;
                           the row's LABEL is the level_3 label.

    Structural rows (subtotal/grandtotal) are emitted in order and their children
    recursed at the next indent.  Mapping ('line') rows are leaves: their L4
    children + accounts are attached inline (NOT recursed as separate rows).  The
    top BS structural rows (Assets / Equity & liabilities, depth 0) are presented
    as kind='grandtotal'.

    COMPONENTS (additive) — each subtotal/grandtotal row carries ``components``:
    the ordered list of mapping ('line') ``line_code``s whose (signed) values sum
    to that row's own ``values`` per period, so the FE can recompute a subtotal
    live from edited per-position plan values WITHOUT replicating the running-sum /
    section logic.  Every ``line`` row carries its own ``values`` (plannable or
    not), so ALL constituent lines are listed — the running sum accumulates EVERY
    mapping line, not only the budget-plannable subset, so listing only plannable
    lines would NOT reconcile.  ``components`` is derived from the SAME structure
    order the builders' running sum uses:

      * PL (flat list — fin_compat_pl ``_compute_running_values``): the running sum
        is a single global cumulative Σ of every mapping line in sort order, so a
        subtotal's components = ALL mapping ('line') line_codes emitted BEFORE it
        (cumulative).  PL subtotals are depth-0 siblings of the mapping lines (no
        ``children``), so we snapshot a cumulative running list.
      * BS (nested tree — fin_compat_bs ``hierarchy_from_grains``/
        ``monthly_hierarchy``): each structural node's amount is the sum of its
        OWN subtree's grain leaves, so its components = the plannable mapping
        line_codes in its subtree.  Subtotals reset per section / grandtotals are
        cumulative over the section EXACTLY because the tree nests that way (a
        section grandtotal's subtree contains all its group subtotals' leaves).

    Note: a BS equity grandtotal also carries injected net profit (not a plannable
    mapping line); on the equity side Σ(components) reconciles to the row's value
    only up to that injection — asset-side rows reconcile exactly.

    ``kind='line'`` rows carry no ``components`` (the field is omitted).
    """
    plannable_by_l3 = plannable_by_l3 or {}
    out: list[dict[str, Any]] = []
    # Cumulative running list of mapping ('line') line_codes in emit (=sort) order,
    # mirroring the PL single global running sum (which accumulates EVERY mapping
    # line).  Used for PL subtotals (flat list, no children); BS subtotals derive
    # components from their subtree instead.
    cumulative: list[str] = []

    def _emit(row: dict[str, Any], depth: int) -> list[str]:
        """Emit a row (and its structural children); return the mapping ('line')
        ``line_code``s this row's SUBTREE contributed (for the BS nested case)."""
        kind = _kind_for(row)
        if kind is None:
            return []  # drop KPI / title / header rows
        code = _code_from(row)
        label = row.get("label") or code

        if kind == "subtotal":
            # Depth-0 structural rows are the section grand totals (BS Assets /
            # Equity & liabilities; PL has no depth-0 grandtotal so this only fires
            # on the nested BS tree).
            present_kind = "grandtotal" if depth == 0 and (row.get("children")) else "subtotal"
            entry = {
                "id": row.get("id") or f"gv-{code or label}",
                "line_code": code,
                "label": label,
                "kind": present_kind,
                "indent": depth,
                "is_bold": bool(row.get("is_bold", False)),
                "values": _values(row.get("amounts"), periods),
                "plannable": False,
                "is_partner_driven": False,
                "partner_kind": None,
                "has_l4": False,
                "children": [],
                "accounts": [],
            }
            out.append(entry)
            builder_children = row.get("children") or []
            if builder_children:
                # BS NESTED case: this structural row owns a subtree.  Its
                # components = the mapping ('line') leaves in that subtree (subtotal
                # = own section group; grandtotal = whole section) — matching
                # hierarchy_from_grains, where the node's amount is the sum of its
                # subtree's grain leaves.  A section with a value but NO mapping
                # leaves (e.g. a directly-valued 'Prepaid expenses' subtotal)
                # contributes an EMPTY component list and so cannot be fully
                # decomposed into lines — that is structurally faithful, not a bug.
                subtree: list[str] = []
                for child in builder_children:
                    subtree.extend(_emit(child, depth + 1))
                entry["components"] = subtree
                return subtree
            # PL FLAT case (depth-0 sibling subtotal, no builder children):
            # components = the cumulative mapping set seen so far, matching the
            # single global running sum in _compute_running_values.
            entry["components"] = list(cumulative)
            return []

        # kind == 'line' (mapping / plannable L3 position) — a leaf row.
        resolved = code if code in plannable_by_code else plannable_by_l3.get(str(label).strip(), "")
        is_plannable = bool(resolved)
        l4_children, accounts, has_l4 = _mapping_children(row, periods)
        is_pd = budget_positions.is_partner_driven(resolved) if is_plannable else False
        out.append({
            "id": row.get("id") or f"gv-{code or label}",
            "line_code": resolved or code,
            "label": label,
            "kind": "line",
            "indent": depth,
            "is_bold": bool(row.get("is_bold", False)),
            "values": _values(row.get("amounts"), periods),
            "plannable": is_plannable,
            "is_partner_driven": is_pd,
            "partner_kind": budget_positions.partner_kind_for(resolved) if is_pd else None,
            "has_l4": has_l4,
            "children": l4_children,
            "accounts": accounts,
        })
        # EVERY mapping line accumulates into the running sum (plannable or not),
        # so list all of them as components — each carries its own ``values``.
        line_code = resolved or code
        cumulative.append(line_code)
        return [line_code]

    for row in builder_rows:
        _emit(row, 0)
    return out


# =========================================================================== #
# DB orchestrator (thin: one builder call + pure flatten + windowing)
# =========================================================================== #
def build_granularity_view(
    session: Session,
    *,
    statement: str,
    grain: str,
    entity: Optional[str] = None,
    entity_prefixes: Optional[set[str]] = None,
) -> dict[str, Any]:
    """Assemble the full-structure, multi-period granularity-view payload.

    Args:
        statement: 'PL' | 'BS'.
        grain: 'month' (last 24 month columns) | 'year' (3 FY/period-end + YTD).
        entity: legal-entity code, or None/'all' for consolidated.
        entity_prefixes: optional fail-closed restriction (a restricted user's OWN
            2-char prefixes); when provided, the scope is the intersection with
            ``entity`` and never widens to the whole ledger.

    Reuses the multi-period statement builders unchanged (only their golden-safe
    ``ent_frag_override`` kwarg) for ALL statement math, then drops KPI/title rows
    and re-keys the per-period ``amounts`` into ``values[]``.  Read-only.
    """
    ent_frag = _resolve_ent_frag(session, entity=entity, entity_prefixes=entity_prefixes)

    anchor = latest_anchor(session, ent_frag if entity_prefixes is not None else "")
    if anchor is None:
        anchor = latest_anchor(session, "")
    if anchor is None:
        # Empty ledger → no columns / rows (still a well-formed payload).
        return {"statement": statement, "grain": grain, "periods": [], "rows": []}
    year, month = anchor

    if statement == "BS":
        if grain == "year":
            built = build_bs_snapshot_annual(
                session, year=year, month=month, ent_frag_override=ent_frag
            )
            periods = bs_year_columns(year, month)
        else:
            built = build_bs_monthly(
                session, year=year, month=month, span="fy3",
                ent_frag_override=ent_frag,
            )
            periods = _month_columns(built.get("periods") or [])
    else:  # PL
        if grain == "year":
            built = build_pl_annual_compat(
                session, year=year, month=month, ent_frag_override=ent_frag
            )
            periods = pl_year_columns(year, month)
        else:
            built = build_pl_monthly(
                session, period_grain="month", year=year, month=month, span="fy3",
                ent_frag_override=ent_frag,
            )
            periods = _month_columns(built.get("periods") or [])

    # The plannable mapping positions (same set as getBudgetTree).  PL builder rows
    # carry the structure line_code; BS hierarchy rows carry a node-id, so we also
    # index by the level_3 LABEL (the BS row label) to resolve them.
    positions = budget_service._load_positions(session, statement)
    plannable_by_code = {code: level_3 for (code, _label, level_3) in positions}
    plannable_by_l3 = {
        level_3.strip(): code
        for (code, _label, level_3) in positions
        if level_3 and level_3.strip()
    }

    rows = assemble_rows(
        built.get("rows") or [], periods, plannable_by_code, plannable_by_l3
    )

    return {
        "statement": statement,
        "grain": grain,
        "periods": periods,
        "rows": rows,
    }
