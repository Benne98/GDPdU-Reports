"""Working-Capital statement builders for the legacy compat layer.

Companion to ``fin_compat_bs`` (balance sheet).  Working capital is a SUBSET of
the balance-sheet balances — only GL accounts whose ``dim_gl_na.l6_na_mapping``
is ``'TWC'`` (Trade Working Capital) or ``'OWC'`` (Other Working Capital).  The
column maths are identical to the BS (cumulative stock, ``posting_date <=
cutoff``, NO ``* -1``) — see ``fin_compat_wc_sql`` (authoritative SQL).

Builders (all return the same legacy response shapes as the BS/P&L pendants):
  * build_wc_statement_compat  → FinancialStatementResponse (statement='wc')
  * build_wc_consolidation     → ConsolidationResponse
  * build_wc_monthly           → MonthlyResponse (one column per month-end)
  * build_wc_l4_trend          → L4TrendResponse
  * build_wc_narrative         → PlNarrativeResponse (deterministic)
  * build_wc_line_detail       → PlLineDetailResponse (cumulative balances)
  * build_wc_snapshot_annual   → ErSnapshotResponse (fy_py/fy/cm_py/cm)
  * build_wc_timeline          → WcTimelineResponse (running TWC components)

=== SEMANTICS (differ from BS — mirrors legacy routers/financials.py WC) ===

1. RAW STORED SIGN, NO DISPLAY FLIP.  Unlike the BS (which flips the credit
   side once), the legacy WC endpoints present the RAW signed balances: assets
   (inventories, receivables, other assets) POSITIVE, liabilities (payables,
   advance payments, other liabilities) NEGATIVE.  Net working capital is then
   the straight Σ of the raw TWC+OWC balances — the economically correct NWC.
   This module therefore does NOT call the BS ``_flip_row_tree``.

2. GROUPING.  WC lines are grouped by ``l6_na_mapping`` (TWC / OWC) → ``level_3``
   → ``level_4`` → account (``WC_HIER_KEYS``), NOT by ``dim_pl_structure``.  The
   two top sections are relabelled (TWC → 'Trade Working Capital', OWC → 'Other
   Working Capital') and ordered TWC-first.

3. NET WORKING CAPITAL row (``row_kind='subtotal'``, bold):
        NWC[col] = Σ over ALL TWC+OWC mapping balances[col]   (raw signs)
   i.e. (+receivables +inventories +other-assets) + (−payables −other-liab).

4. DAYS KPI rows (``row_kind='kpi'``, values in DAYS) — per column, using the
   ABSOLUTE magnitude of the relevant level_3 balance and an LTM denominator
   (revenue / COGS over the rolling-12-month window ending at that column's
   balance-cutoff date, from the P&L via ``wc_pl_window_sql`` + ``ltm_window``):

        DSO = receivables * 365 / revenue_ltm        (Days Sales Outstanding)
        DIO = inventories * 365 / cogs_ltm            (Days Inventory Outstanding)
        DPO = payables    * 365 / cogs_ltm            (Days Payable Outstanding)
        CCC = DSO + DIO − DPO                         (Cash Conversion Cycle)

   Fallback: a denominator ≈ 0 → that KPI = 0.0 (no div-by-zero); CCC is then the
   sum of whatever components are defined (0 for the undefined ones).

=== WORKED EXAMPLE (month grain, cm column; concrete numbers) ===
  Raw cumulative balances at the cm cutoff (per level_3, summed across accounts):
    Trade receivables (rec) = +5,000,000   (asset, positive)
    Inventories       (inv) = +4,000,000   (asset, positive)
    Trade payables    (pay) = −3,000,000   (liability, negative)
    Other working cap. (owc)=   +500,000   (OWC net)
  NWC = 5,000,000 + 4,000,000 + (−3,000,000) + 500,000 = +6,500,000.
  LTM denominators ending at the cm cutoff: revenue_ltm = 20,000,000,
    cogs_ltm = 12,000,000.  KPIs use the ABS magnitudes (rec/inv/pay):
    DSO = 5,000,000 * 365 / 20,000,000 = 91.25  → round(91.2) ... = 91.2 days
    DIO = 4,000,000 * 365 / 12,000,000 = 121.666… → 121.7 days
    DPO = 3,000,000 * 365 / 12,000,000 =  91.25  → 91.2 days
    CCC = 91.2 + 121.7 − 91.2 = 121.7 days
  (All KPIs round to 1 decimal, matching the legacy ``_compute_wc_kpis``.)

=== EDGE CASES ===
  * revenue_ltm ≈ 0 → DSO = 0 ; cogs_ltm ≈ 0 → DIO = DPO = 0 → CCC = 0.
  * No movements up to a cutoff → 0 balance (COALESCE in SQL) → 0 KPIs.
  * Negative stored balances feed the RAW NWC straight; the DAYS KPIs take ABS so
    they read as positive day-counts regardless of the stored sign.
  * Snapshot deltas: delta_fy = fy − fy_py ; delta_cm = cm − cm_py (raw amounts).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Callable, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.fin_compat_bs import (
    _apply_snapshot_fy_forecast_amounts,
    _snap_deltas,
    _snapshot_net_profit_ytg_plan,
    _sort_children_by_label,
    col_labels_bs_snapshot,
)
from app.services.fin_compat_pl import (
    _apply_plan_data,
    _deltas,
    _round_am,
    _statement_structure_rows,
    attach_hierarchy_plan_to_rows,
    load_position_plan_map,
    load_position_plan_map_pref,
)
from app.services.fin_compat_sql import (
    _last_12_periods,
    col_labels_annual,
    col_labels_month,
    col_labels_week,
    entities_sql_fragment,
    entity_sql_fragment,
    iso_week_bounds,
    last_day,
    period_key,
    period_label,
    plan_anchor_for_week,
    pm,
    prior_iso_week,
    resolve_entity_prefix,
    same_week_prior_year,
)
from app.services.fin_compat_wc_sql import (
    PL_COGS_L3,
    PL_REV_L3,
    WC_INV_L3,
    WC_MAPPINGS,
    WC_PAY_L3,
    WC_REC_L3,
    _WC_KEYS_MONTH,
    _WC_KEYS_WEEK,
    _WC_SNAP_KEYS,
    ltm_window,
    wc_consl_grain_sql_annual,
    wc_consl_grain_sql_month,
    wc_consl_grain_sql_week,
    wc_grain_sql_month,
    wc_grain_sql_week,
    wc_l4_trend_sql,
    wc_monthly_grain_sql,
    wc_pl_window_sql,
    wc_snapshot_grain_sql,
    wc_timeline_sql,
)

# Top-level WC section labels (l6_na_mapping → display) + ordering.
WC_LABEL_MAP = {"TWC": "Trade Working Capital", "OWC": "Other Working Capital"}
WC_TOP_SECTION_LABELS = frozenset(WC_LABEL_MAP.values())
WC_ORDER = {"TWC": 0, "OWC": 1}

# Grouping hierarchy (outer → inner). KPIs key off level_3 (Inventories / Trade
# receivables / Trade payables), so level_3 is the second grouping level.
WC_HIER_KEYS = ["l6_na_mapping", "level_3", "level_4"]

# Trade WC mapping-line presentation order (level_3 under TWC).
WC_TWC_L3_ORDER = [
    WC_INV_L3,
    WC_REC_L3,
    WC_PAY_L3,
    "Advance payments received",
]

_AM_KEYS = list(_WC_KEYS_MONTH)  # py_cm, pm, cm, ytd, ytd_py

# KPI line_code → level_3 used to resolve the WC line-detail GL scope.
_KPI_L3 = {"WC_DIO": WC_INV_L3, "WC_DSO": WC_REC_L3, "WC_DPO": WC_PAY_L3}

# KPI display rows (id, label, key).
_WC_KPI_ROWS = [
    ("wc-kpi-dio", "DIO — Days Inventory Outstanding", "DIO"),
    ("wc-kpi-dso", "DSO — Days Sales Outstanding", "DSO"),
    ("wc-kpi-dpo", "DPO — Days Payable Outstanding", "DPO"),
    ("wc-kpi-ccc", "CCC — Cash Conversion Cycle", "CCC"),
]


# ---------------------------------------------------------------------------
# Pure KPI / NWC core (DB-free, unit-tested)
# ---------------------------------------------------------------------------

def compute_wc_kpis(inv: float, rec: float, pay: float,
                    rev_ltm: float, cogs_ltm: float) -> dict[str, float]:
    """DIO / DSO / DPO / CCC in DAYS from balances + LTM denominators.

        DIO = inv * 365 / cogs_ltm     (0 when cogs_ltm ≈ 0)
        DSO = rec * 365 / rev_ltm      (0 when rev_ltm  ≈ 0)
        DPO = pay * 365 / cogs_ltm     (0 when cogs_ltm ≈ 0)
        CCC = DSO + DIO − DPO

    Mirrors legacy ``_compute_wc_kpis``.  Callers pass the ABS magnitudes of the
    balances so days read positive regardless of the stored sign.
    """
    dio = round(inv * 365 / cogs_ltm, 1) if cogs_ltm > 1e-6 else 0.0
    dso = round(rec * 365 / rev_ltm, 1) if rev_ltm > 1e-6 else 0.0
    dpo = round(pay * 365 / cogs_ltm, 1) if cogs_ltm > 1e-6 else 0.0
    ccc = round(dso + dio - dpo, 1)
    return {"DIO": dio, "DSO": dso, "DPO": dpo, "CCC": ccc}


def _safe_seg(val: Any) -> str:
    """Sanitise a hierarchy segment for use in a row id."""
    return str(val or "")[:20].replace(" ", "_").replace("/", "_").replace("&", "_")


def _zero(keys: list[str]) -> dict[str, float]:
    return {k: 0.0 for k in keys}


def _grain_am(g: dict, keys: list[str]) -> dict[str, float]:
    return {k: float(g.get(k) or 0.0) for k in keys}


def _l3_balance(grains: list[dict], l3: str, keys: list[str]) -> dict[str, float]:
    """Σ RAW balances of all grains whose level_3 == ``l3`` (per column)."""
    out = _zero(keys)
    for g in grains:
        if (g.get("level_3") or "").strip() == l3:
            for k in keys:
                out[k] += float(g.get(k) or 0.0)
    return out


def _nwc_total(grains: list[dict], keys: list[str]) -> dict[str, float]:
    """Net working capital = Σ over ALL TWC+OWC mapping balances (raw signs)."""
    out = _zero(keys)
    for g in grains:
        for k in keys:
            out[k] += float(g.get(k) or 0.0)
    return out


def _keep_wc(grains: list[dict]) -> list[dict]:
    return [g for g in grains if (g.get("l6_na_mapping") or "").strip() in WC_MAPPINGS]


def _wc_account_label(gid: str, ang: str, name: str) -> str:
    """Display label for GL account rows: ``{id} | {name}`` (consolidated WC style)."""
    disp_id = (gid or ang or "").strip()
    disp_name = (name or "").strip() or "—"
    if disp_id:
        return f"{disp_id} | {disp_name}"
    return disp_name if disp_name != "—" else "—"


def _wc_group_labels(path: dict[str, str], key: str, grouped: dict[str, list[dict]]) -> list[str]:
    """Child-group iteration order at this hierarchy level."""
    if key == "level_3" and (path.get("l6_na_mapping") or "").strip() == "TWC":
        seen: set[str] = set()
        ordered: list[str] = []
        for lbl in WC_TWC_L3_ORDER:
            if lbl in grouped:
                ordered.append(lbl)
                seen.add(lbl)
        for lbl in sorted(grouped.keys()):
            if lbl not in seen:
                ordered.append(lbl)
        return ordered
    return sorted(grouped.keys())


# ---------------------------------------------------------------------------
# Generic recursive hierarchy builder (shared by statement / consolidation /
# monthly).  ``get_am`` extracts a {key: value} dict from a grain row; ``node``
# and ``leaf`` build the row dict in the response shape for each mode.
# ---------------------------------------------------------------------------

def _wc_tree(
    grains: list[dict],
    key_idx: int,
    path: dict[str, str],
    depth: int,
    *,
    keys: list[str],
    get_am: Callable[[dict], dict[str, float]],
    node: Callable[..., dict],
    leaf: Callable[..., dict],
    id_prefix: str = "wc",
) -> list[dict[str, Any]]:
    if key_idx >= len(WC_HIER_KEYS):
        # Account-level leaves (dedupe by gl_account_id / account_number_group).
        by_acc: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for g in grains:
            gid = (g.get("gl_account_id") or "").strip()
            ang = (g.get("account_number_group") or "").strip()
            name = (g.get("account_name") or "").strip()
            akey = gid or ang or f"_{id(g)}"
            if akey not in by_acc:
                by_acc[akey] = {"gid": gid, "ang": ang, "name": name, "am": _zero(keys), "grs": []}
                order.append(akey)
            am = get_am(g)
            for k in keys:
                by_acc[akey]["am"][k] += am.get(k, 0.0)
            by_acc[akey]["grs"].append(g)
        out: list[dict[str, Any]] = []
        for akey in order:
            v = by_acc[akey]
            gid = (v["gid"] or v["ang"] or "").strip()
            label = _wc_account_label(v["gid"], v["ang"], v["name"])
            out.append(leaf(akey, label, v["am"], path, gid or None, v["grs"]))
        out.sort(key=lambda r: r["label"])
        return out

    key = WC_HIER_KEYS[key_idx]
    grouped: dict[str, list[dict]] = {}
    for g in grains:
        val = (g.get(key) or "").strip() or "—"
        grouped.setdefault(val, []).append(g)

    # Skip a level entirely if every row maps to "—" (column unused).
    if list(grouped.keys()) == ["—"]:
        return _wc_tree(grains, key_idx + 1, path, depth, keys=keys,
                        get_am=get_am, node=node, leaf=leaf, id_prefix=id_prefix)

    out = []
    for label in _wc_group_labels(path, key, grouped):
        grs = grouped[label]
        new_path = {**path, key: label}
        total = _zero(keys)
        for g in grs:
            am = get_am(g)
            for k in keys:
                total[k] += am.get(k, 0.0)
        node_id = f"{id_prefix}-d{depth}-" + "-".join(
            _safe_seg(new_path.get(k, "")) for k in WC_HIER_KEYS[: key_idx + 1]
        )
        children = _wc_tree(grs, key_idx + 1, new_path, depth + 1, keys=keys,
                            get_am=get_am, node=node, leaf=leaf, id_prefix=id_prefix)
        # Collapse a single child that duplicates the parent label.
        if len(children) == 1 and children[0].get("label", "") == label:
            child = children[0]
            child["id"] = node_id
            child["is_bold"] = depth <= 1
            if depth <= 1:
                child["row_kind"] = "subtotal"
            out.append(child)
            continue
        out.append(node(node_id, label, depth, total, children, new_path, grs))
    return out


def _wc_normalize_row_kinds(rows: list[dict[str, Any]]) -> None:
    """Promote mapping lines under TWC/OWC from subtotal to line (skip L2 display)."""
    for row in rows:
        if row.get("row_kind") in ("kpi", "kpi_header") or row.get("line_code") == "NWC":
            continue
        if (row.get("label") or "").strip() in WC_TOP_SECTION_LABELS:
            for ch in row.get("children") or []:
                if ch.get("row_kind") == "subtotal":
                    ch["row_kind"] = "line"


def _wc_apply_aggregate_bold_only(rows: list[dict[str, Any]]) -> None:
    """Only TWC / OWC / NWC top rows are bold (never the mapping children)."""
    bold = WC_TOP_SECTION_LABELS | {"Net working capital"}

    def walk(rs: list[dict[str, Any]]) -> None:
        for r in rs:
            r["is_bold"] = (r.get("label") or "").strip() in bold
            walk(r.get("children") or [])

    walk(rows)


def _rename_and_sort_sections(rows: list[dict[str, Any]]) -> None:
    """Order TWC first / OWC second, then relabel the top sections (in place)."""
    rows.sort(key=lambda r: WC_ORDER.get((r.get("label") or "").strip(), 99))
    for row in rows:
        mapped = WC_LABEL_MAP.get((row.get("label") or "").strip())
        if mapped:
            row["label"] = mapped


def _sort_wc_display_order(rows: list[dict[str, Any]]) -> None:
    """TWC mapping-line order: Inventories → Receivables → Payables → Advance payments."""

    def _walk(node_rows: list[dict[str, Any]], parent_label: str = "") -> None:
        if not node_rows:
            return
        parent = (parent_label or "").strip()
        if parent in ("TWC", "Trade Working Capital"):
            _sort_children_by_label(node_rows, WC_TWC_L3_ORDER)
        for row in node_rows:
            label = (row.get("label") or "").strip()
            children = row.get("children") or []
            if children:
                _walk(children, label)

    _walk(rows)


def _wc_kpi_header_row(*, id_prefix: str = "wc") -> dict[str, Any]:
    return {
        "id": f"{id_prefix}-kpi-header",
        "line_code": "WC_KPI_HEADER",
        "row_kind": "kpi_header",
        "label": "KPIs — working capital days",
        "is_bold": False,
        "has_children": False,
        "children": [],
    }


def _wc_consl_kpi_header_row(
    entity_codes: list[str],
    *,
    zero_periods: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "wc-kpi-header",
        "label": "KPIs — working capital days",
        "row_kind": "kpi_header",
        "is_bold": False,
        "entity_amounts": {ec: 0.0 for ec in entity_codes},
        "aggregated": 0.0,
        "ic_eliminations": 0.0,
        "consolidation": 0.0,
        "has_children": False,
        "children": [],
    }
    if zero_periods is not None:
        row["entity_periods"] = {ec: dict(zero_periods) for ec in entity_codes}
        row["aggregated_periods"] = dict(zero_periods)
        row["consolidation_periods"] = dict(zero_periods)
    return row


def _wc_drill(path: dict[str, str], gid: Optional[str] = None) -> dict[str, Any]:
    drill: dict[str, Any] = {"statement_type": "BS"}
    l6 = (path.get("l6_na_mapping") or "").strip()
    if l6 and l6 != "—":
        drill["l6_na_mapping"] = l6
    for k in ("level_2", "level_3", "level_4"):
        v = (path.get(k) or "").strip()
        if v and v != "—":
            drill[k] = v
    drill["gl_account_id"] = gid or None
    return drill


# ---------------------------------------------------------------------------
# KPI denominators (LTM revenue / COGS through a date) + KPI rows
# ---------------------------------------------------------------------------

def _ltm_through(session: Session, end: date, ent_frag: str) -> tuple[float, float]:
    """(revenue_ltm, cogs_ltm) over the rolling-12-month window ending at ``end``."""
    start, _ = ltm_window(end)
    sql, params = wc_pl_window_sql(start, end, ent_frag)
    row = session.execute(text(sql), params).fetchone()
    if row is None:
        return 0.0, 0.0
    d = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
    return float(d.get("revenue") or 0.0), float(d.get("cogs") or 0.0)


def _kpi_rows(
    grains: list[dict],
    col_ltm: dict[str, tuple[float, float]],
    keys: list[str],
    *,
    delta_fn: Callable[[dict[str, float]], dict[str, float]],
) -> list[dict[str, Any]]:
    """Build the 4 DAYS KPI rows (statement / snapshot shape) over ``keys``."""
    inv = _l3_balance(grains, WC_INV_L3, keys)
    rec = _l3_balance(grains, WC_REC_L3, keys)
    pay = _l3_balance(grains, WC_PAY_L3, keys)
    kpi_am: dict[str, dict[str, float]] = {k: {} for k in ("DIO", "DSO", "DPO", "CCC")}
    for col in keys:
        src = "cm" if col == "fy_f" else col
        rev_l, cogs_l = col_ltm.get(col, (0.0, 0.0))
        vals = compute_wc_kpis(
            abs(inv.get(src, 0.0)), abs(rec.get(src, 0.0)), abs(pay.get(src, 0.0)),
            rev_l, cogs_l,
        )
        for kk in kpi_am:
            kpi_am[kk][col] = vals[kk]
    out: list[dict[str, Any]] = [_wc_kpi_header_row()]
    for rid, label, kk in _WC_KPI_ROWS:
        out.append({
            "id": rid, "line_code": f"WC_{kk}", "row_kind": "kpi", "label": label,
            "amounts": _round_am(kpi_am[kk]), "deltas": _round_am(delta_fn(kpi_am[kk])),
            "invert_delta": False, "is_bold": False, "drill": None,
            "has_children": False, "children": [],
        })
    return out


def _statement_col_ltm(
    session: Session, ent_frag: str, *,
    is_week: bool, yr: int, mo: int,
    iso_year: Optional[int], iso_week: Optional[int], keys: list[str],
) -> dict[str, tuple[float, float]]:
    """Per-column LTM (revenue, cogs) ending at each column's balance cutoff."""
    if is_week:
        assert iso_year is not None and iso_week is not None
        _, d_cm = iso_week_bounds(iso_year, iso_week)
        pw_y, pw_w = prior_iso_week(iso_year, iso_week)
        _, d_pm = iso_week_bounds(pw_y, pw_w)
        spy_y, spy_w = same_week_prior_year(iso_year, iso_week)
        _, d_py = iso_week_bounds(spy_y, spy_w)
    else:
        d_cm = last_day(yr, mo)
        pm_y, pm_m = pm(yr, mo)
        d_pm = last_day(pm_y, pm_m)
        d_py = last_day(yr - 1, mo)
    ltm_cm = _ltm_through(session, d_cm, ent_frag)
    ltm_pm = _ltm_through(session, d_pm, ent_frag)
    ltm_py = _ltm_through(session, d_py, ent_frag)
    col_ltm = {"cm": ltm_cm, "ytd": ltm_cm, "pm": ltm_pm,
               "py_cm": ltm_py, "ytd_py": ltm_py}
    if "mtd" in keys:
        col_ltm["mtd"] = ltm_cm
    return col_ltm


# ---------------------------------------------------------------------------
# Main statement builder (month | week)
# ---------------------------------------------------------------------------

def build_wc_statement_compat(
    session: Session,
    *,
    period_grain: str = "month",
    year: Optional[int] = None,
    month: Optional[int] = None,
    iso_year: Optional[int] = None,
    iso_week: Optional[int] = None,
    entity: Optional[str] = None,
    allowed_entities: Optional[set[str]] = None,
) -> dict[str, Any]:
    """Legacy FinancialStatementResponse for working capital (statement='wc').

    Raw cumulative balances grouped TWC/OWC → level_3 → level_4 → account; a Net
    working capital subtotal (Σ TWC+OWC); DSO/DIO/DPO/CCC days KPIs per column.
    See module docstring for FORMULA / WORKED EXAMPLE / EDGE CASES.

    ``allowed_entities`` (fail-closed tenant isolation): set of 2-char
    ``entity_prefix`` values, or ``None`` for admin/unrestricted.  When provided it
    is the ONLY entity filter (``entity`` ignored — caller has already intersected
    it) and threads through the grain and the LTM denominators; an EMPTY set fails
    closed (matches nothing).  ``None`` preserves the exact legacy behaviour.
    """
    if allowed_entities is not None:
        ent_frag = (
            entities_sql_fragment(sorted(allowed_entities))
            if allowed_entities else "AND 1 = 0"
        )
    else:
        ep = resolve_entity_prefix(session, entity)
        ent_frag = entity_sql_fragment(ep)

    is_week = period_grain == "week"
    if is_week:
        assert iso_year is not None and iso_week is not None
        sql, params = wc_grain_sql_week(iso_year, iso_week, ent_frag)
        yr, mo = plan_anchor_for_week(iso_year, iso_week)
        labels = col_labels_week(iso_year, iso_week)
        keys = list(_WC_KEYS_WEEK)
    else:
        assert year is not None and month is not None
        sql, params = wc_grain_sql_month(year, month, ent_frag)
        yr, mo = year, month
        labels = col_labels_month(year, month)
        keys = list(_WC_KEYS_MONTH)

    grains = _keep_wc([dict(r._mapping) for r in session.execute(text(sql), params).fetchall()])

    def _node(node_id, label, depth, total, children, path, grs=None):
        return {
            "id": node_id, "line_code": node_id,
            "row_kind": "subtotal" if depth <= 1 else "line", "label": label,
            "amounts": _round_am(total), "deltas": _round_am(_deltas(total, invert=False)),
            "invert_delta": False, "is_bold": depth <= 1, "drill": _wc_drill(path),
            "has_children": bool(children), "children": children,
        }

    def _leaf(akey, label, am, path, gid, grs=None):
        rid = f"wc-acc-{_safe_seg(akey)}"
        return {
            "id": rid, "line_code": gid or rid, "row_kind": "account", "label": label,
            "amounts": _round_am(am), "deltas": _round_am(_deltas(am, invert=False)),
            "invert_delta": False, "is_bold": False,
            "drill": _wc_drill(path, gid) if gid else None,
            "has_children": False, "children": [],
        }

    rows = _wc_tree(grains, 0, {}, 0, keys=keys, get_am=lambda g: _grain_am(g, keys),
                    node=_node, leaf=_leaf)
    _rename_and_sort_sections(rows)
    _sort_wc_display_order(rows)
    _wc_normalize_row_kinds(rows)
    _wc_apply_aggregate_bold_only(rows)

    wc_plan_map = load_position_plan_map_pref(session, "BS", yr, mo, ent_frag)
    if wc_plan_map:
        attach_hierarchy_plan_to_rows(
            rows, wc_plan_map, _statement_structure_rows(session, "BS"),
        )

    # Net working capital subtotal (Σ all TWC+OWC).
    nwc = _nwc_total(grains, keys)
    nwc_am = _round_am(nwc)
    if wc_plan_map:
        nwc_plan = sum(
            float((r.get("amounts") or {}).get("plan_cm") or 0.0) for r in rows
        )
        if abs(nwc_plan) > 1e-6:
            nwc_am = _round_am(_apply_plan_data(nwc_am, {"plan_cm": nwc_plan}))
    rows.append({
        "id": "wc-net-total", "line_code": "NWC", "row_kind": "subtotal",
        "label": "Net working capital", "amounts": nwc_am,
        "deltas": _round_am(_deltas(nwc, invert=False)), "invert_delta": False,
        "is_bold": True, "drill": None, "has_children": False, "children": [],
    })

    # Days KPIs (per-column LTM denominators).
    col_ltm = _statement_col_ltm(session, ent_frag, is_week=is_week, yr=yr, mo=mo,
                                 iso_year=iso_year, iso_week=iso_week, keys=keys)
    rows.extend(_kpi_rows(grains, col_ltm, keys,
                          delta_fn=lambda am: _deltas(am, invert=False)))

    out: dict[str, Any] = {
        "statement": "wc", "period_grain": period_grain, "year": yr, "month": mo,
        "col_labels": labels, "rows": rows,
    }
    if iso_year is not None:
        out["iso_year"] = iso_year
    if iso_week is not None:
        out["iso_week"] = iso_week
    return out


# ---------------------------------------------------------------------------
# Consolidation builder (per-entity cm balance)
# ---------------------------------------------------------------------------

def _consl_row(rid, label, row_kind, is_bold, entity_amounts, entity_codes,
               *, children=None) -> dict[str, Any]:
    agg = round(sum(entity_amounts.get(ec, 0.0) for ec in entity_codes), 2)
    return {
        "id": rid, "label": label, "row_kind": row_kind, "is_bold": is_bold,
        "entity_amounts": {ec: round(entity_amounts.get(ec, 0.0), 2) for ec in entity_codes},
        "aggregated": agg, "ic_eliminations": 0.0, "consolidation": agg,
        "has_children": bool(children), "children": children or [],
    }


def _sum_snap_periods(
    am_multi: dict[str, dict[str, float]],
    entity_codes: list[str],
    snap_keys: list[str],
) -> dict[str, float]:
    return {
        k: round(sum(am_multi.get(ec, {}).get(k, 0.0) for ec in entity_codes), 2)
        for k in snap_keys
    }


def _am_multi_from_grains(
    grains: list[dict],
    ep_to_code: dict[str, str],
    entity_codes: list[str],
    snap_keys: list[str],
) -> dict[str, dict[str, float]]:
    sums = {ec: {k: 0.0 for k in snap_keys} for ec in entity_codes}
    for g in grains:
        code = ep_to_code.get((g.get("entity_prefix") or "").strip())
        if not code:
            continue
        for k in snap_keys:
            sums[code][k] += float(g.get(k) or 0.0)
    return sums


def _wc_balances_by_entity_l3(
    grains: list[dict],
    l3: str,
    ep_to_code: dict[str, str],
    col: str,
) -> dict[str, float]:
    """Per-entity Σ raw balances for ``l3`` at snapshot column ``col``."""
    out: dict[str, float] = {}
    for g in grains:
        if (g.get("level_3") or "").strip() != l3:
            continue
        code = ep_to_code.get((g.get("entity_prefix") or "").strip())
        if not code:
            continue
        out[code] = out.get(code, 0.0) + float(g.get(col) or 0.0)
    return out


def _wc_consl_annual_kpi_rows(
    session: Session,
    grains: list[dict],
    entity_codes: list[str],
    ep_to_code: dict[str, str],
    snap_keys: list[str],
    *,
    year: int,
    month: int,
) -> list[dict[str, Any]]:
    """Annual consolidation DAYS KPIs — one value per snapshot column (like snapshot builder)."""
    col_dates = {
        "dec_py2": last_day(year - 3, 12),
        "fy_py": last_day(year - 2, 12),
        "fy": last_day(year - 1, 12),
        "cm_py": last_day(year - 1, month),
        "cm": last_day(year, month),
    }
    col_ltm_all = {col: _ltm_through(session, col_dates[col], "") for col in snap_keys}
    code_to_ep = {v: k for k, v in ep_to_code.items()}
    ent_ltm: dict[str, dict[str, tuple[float, float]]] = {}
    for ec in entity_codes:
        ep = code_to_ep.get(ec, "")
        frag = entity_sql_fragment(ep) if ep else ""
        ent_ltm[ec] = {col: _ltm_through(session, col_dates[col], frag) for col in snap_keys}

    kpi_entity_periods: dict[str, dict[str, dict[str, float]]] = {
        kk: {ec: {} for ec in entity_codes} for kk in ("DIO", "DSO", "DPO", "CCC")
    }
    kpi_consl_periods: dict[str, dict[str, float]] = {kk: {} for kk in ("DIO", "DSO", "DPO", "CCC")}

    for col in snap_keys:
        inv_ec = _wc_balances_by_entity_l3(grains, WC_INV_L3, ep_to_code, col)
        rec_ec = _wc_balances_by_entity_l3(grains, WC_REC_L3, ep_to_code, col)
        pay_ec = _wc_balances_by_entity_l3(grains, WC_PAY_L3, ep_to_code, col)
        rev_l, cogs_l = col_ltm_all[col]
        consl_vals = compute_wc_kpis(
            abs(sum(inv_ec.values())), abs(sum(rec_ec.values())), abs(sum(pay_ec.values())),
            rev_l, cogs_l,
        )
        for ec in entity_codes:
            rev_e, cogs_e = ent_ltm[ec][col]
            ent_vals = compute_wc_kpis(
                abs(inv_ec.get(ec, 0.0)), abs(rec_ec.get(ec, 0.0)), abs(pay_ec.get(ec, 0.0)),
                rev_e, cogs_e,
            )
            for kk in kpi_entity_periods:
                kpi_entity_periods[kk][ec][col] = ent_vals[kk]
        for kk in kpi_consl_periods:
            kpi_consl_periods[kk][col] = consl_vals[kk]

    zero_periods = {k: 0.0 for k in snap_keys}
    rows_out: list[dict[str, Any]] = [
        _wc_consl_kpi_header_row(entity_codes, zero_periods=zero_periods),
    ]
    for rid, label, kk in _WC_KPI_ROWS:
        consl_periods = {k: round(kpi_consl_periods[kk].get(k, 0.0), 1) for k in snap_keys}
        rows_out.append({
            "id": rid, "label": label, "row_kind": "kpi", "is_bold": False,
            "entity_amounts": {
                ec: round(kpi_entity_periods[kk][ec].get("cm", 0.0), 1) for ec in entity_codes
            },
            "entity_periods": {
                ec: {k: round(kpi_entity_periods[kk][ec].get(k, 0.0), 1) for k in snap_keys}
                for ec in entity_codes
            },
            "aggregated": 0.0,
            "aggregated_periods": dict(consl_periods),
            "ic_eliminations": 0.0,
            "consolidation": consl_periods.get("cm", 0.0),
            "consolidation_periods": consl_periods,
            "has_children": False, "children": [],
        })
    return rows_out


def _consl_row_annual_wc(
    rid: str,
    label: str,
    row_kind: str,
    is_bold: bool,
    am_multi: dict[str, dict[str, float]],
    entity_codes: list[str],
    snap_keys: list[str],
    *,
    children: Optional[list] = None,
) -> dict[str, Any]:
    entity_periods = {
        ec: {k: round(am_multi.get(ec, {}).get(k, 0.0), 2) for k in snap_keys}
        for ec in entity_codes
    }
    agg_periods = _sum_snap_periods(am_multi, entity_codes, snap_keys)
    return {
        "id": rid,
        "label": label,
        "row_kind": row_kind,
        "is_bold": is_bold,
        "entity_amounts": {ec: entity_periods[ec]["cm"] for ec in entity_codes},
        "entity_periods": entity_periods,
        "aggregated": agg_periods["cm"],
        "aggregated_periods": agg_periods,
        "ic_eliminations": 0.0,
        "consolidation": agg_periods["cm"],
        "consolidation_periods": dict(agg_periods),
        "has_children": bool(children),
        "children": children or [],
    }


def build_wc_consolidation(
    session: Session,
    *,
    period_grain: str = "month",
    year: Optional[int] = None,
    month: Optional[int] = None,
    iso_year: Optional[int] = None,
    iso_week: Optional[int] = None,
) -> dict[str, Any]:
    """ConsolidationResponse for working capital (per-entity cm balance).

    Each ``entity_amounts[code]`` is that entity's raw cumulative cm balance;
    aggregated = Σ entities, ic_eliminations = 0, consolidation = aggregated.  An
    NWC subtotal and per-entity / consolidated DAYS KPIs close the table.
    """
    if period_grain == "week":
        assert iso_year is not None and iso_week is not None
        sql, params = wc_consl_grain_sql_week(iso_year, iso_week)
        yr, mo = plan_anchor_for_week(iso_year, iso_week)
        labels = col_labels_week(iso_year, iso_week)
        _, d_cm = iso_week_bounds(iso_year, iso_week)
    elif period_grain == "year":
        assert year is not None and month is not None
        sql, params = wc_consl_grain_sql_annual(year, month)
        yr, mo = year, month
        labels = col_labels_bs_snapshot(year, month)
        d_cm = last_day(year, month)
    else:
        assert year is not None and month is not None
        sql, params = wc_consl_grain_sql_month(year, month)
        yr, mo = year, month
        labels = col_labels_month(year, month)
        d_cm = last_day(year, month)

    grains = _keep_wc([dict(r._mapping) for r in session.execute(text(sql), params).fetchall()])

    ent_rows = session.execute(text(
        "SELECT legal_entity_code, entity_prefix, entity_name FROM dim_legal_entity "
        "ORDER BY legal_entity_code"
    )).fetchall()
    entity_codes = [r[0] for r in ent_rows]
    ep_to_code = {r[1]: r[0] for r in ent_rows}
    entity_dicts = [{"code": r[0], "label": r[2]} for r in ent_rows]

    if period_grain == "year":
        snap_keys = list(_WC_SNAP_KEYS)

        def _am_multi(grs: list[dict]) -> dict[str, dict[str, float]]:
            return _am_multi_from_grains(grs, ep_to_code, entity_codes, snap_keys)

        def _node_annual(node_id, label, depth, total, children, path, grs=None):
            return _consl_row_annual_wc(
                node_id, label, "subtotal" if depth <= 1 else "line",
                depth <= 1, _am_multi(grs or []), entity_codes, snap_keys,
                children=children,
            )

        def _leaf_annual(akey, label, am, path, gid, grs=None):
            return _consl_row_annual_wc(
                f"wc-acc-{_safe_seg(akey)}", label, "account", False,
                _am_multi(grs or []), entity_codes, snap_keys,
            )

        rows = _wc_tree(
            grains, 0, {}, 0, keys=snap_keys,
            get_am=lambda g: _grain_am(g, snap_keys),
            node=_node_annual, leaf=_leaf_annual,
        )
        _rename_and_sort_sections(rows)
        _sort_wc_display_order(rows)
        _wc_normalize_row_kinds(rows)
        _wc_apply_aggregate_bold_only(rows)

        nwc_multi = _am_multi(grains)
        rows.append(_consl_row_annual_wc(
            "wc-net-total", "Net working capital", "subtotal", True,
            nwc_multi, entity_codes, snap_keys,
        ))

        rows.extend(_wc_consl_annual_kpi_rows(
            session, grains, entity_codes, ep_to_code, snap_keys, year=yr, month=mo,
        ))

        col_label = labels.get("cm", period_label(yr, mo))
        out: dict[str, Any] = {
            "statement": "wc", "period_grain": period_grain, "year": yr, "month": mo,
            "col_label": col_label,
            "col_labels": labels,
            "entities": entity_dicts, "rows": rows,
        }
        return out

    def _get_am(g):
        code = ep_to_code.get((g.get("entity_prefix") or "").strip())
        return {code: float(g.get("cm") or 0.0)} if code else {}

    def _node(node_id, label, depth, total, children, path, grs=None):
        return _consl_row(node_id, label, "subtotal" if depth <= 1 else "line",
                          depth <= 1, total, entity_codes, children=children)

    def _leaf(akey, label, am, path, gid, grs=None):
        return _consl_row(f"wc-acc-{_safe_seg(akey)}", label, "account", False,
                          am, entity_codes)

    rows = _wc_tree(grains, 0, {}, 0, keys=entity_codes, get_am=_get_am,
                    node=_node, leaf=_leaf)
    _rename_and_sort_sections(rows)
    _sort_wc_display_order(rows)
    _wc_normalize_row_kinds(rows)
    _wc_apply_aggregate_bold_only(rows)

    total_am = {ec: 0.0 for ec in entity_codes}
    for row in rows:
        for ec in entity_codes:
            total_am[ec] += row.get("entity_amounts", {}).get(ec, 0.0)
    rows.append(_consl_row("wc-net-total", "Net working capital", "subtotal", True,
                           total_am, entity_codes))

    # Per-entity balances by level_3 (cm) + per-entity LTM (revenue, cogs).
    l3_ec: dict[str, dict[str, float]] = {}
    for g in grains:
        l3 = (g.get("level_3") or "").strip()
        code = ep_to_code.get((g.get("entity_prefix") or "").strip())
        if not code:
            continue
        l3_ec.setdefault(l3, {})
        l3_ec[l3][code] = l3_ec[l3].get(code, 0.0) + float(g.get("cm") or 0.0)
    inv_ec = l3_ec.get(WC_INV_L3, {})
    rec_ec = l3_ec.get(WC_REC_L3, {})
    pay_ec = l3_ec.get(WC_PAY_L3, {})

    start, _ = ltm_window(d_cm)
    pl_sql, pl_params = wc_pl_window_sql(start, d_cm, "", by_entity=True)
    ltm_by_ep: dict[str, tuple[float, float]] = {}
    for r in session.execute(text(pl_sql), pl_params).fetchall():
        d = dict(r._mapping)
        ltm_by_ep[(d.get("entity_prefix") or "").strip()] = (
            float(d.get("revenue") or 0.0), float(d.get("cogs") or 0.0))
    # Map LTM to legal_entity_code.
    code_to_ep = {v: k for k, v in ep_to_code.items()}
    ltm_by_ec = {ec: ltm_by_ep.get(code_to_ep.get(ec, ""), (0.0, 0.0)) for ec in entity_codes}

    rev_total = sum(v[0] for v in ltm_by_ec.values())
    cogs_total = sum(v[1] for v in ltm_by_ec.values())
    kpis_consl = compute_wc_kpis(
        abs(sum(inv_ec.values())), abs(sum(rec_ec.values())), abs(sum(pay_ec.values())),
        rev_total, cogs_total)

    ent_kpi: dict[str, dict[str, float]] = {k: {} for k in ("DIO", "DSO", "DPO", "CCC")}
    for ec in entity_codes:
        rev_l, cogs_l = ltm_by_ec.get(ec, (0.0, 0.0))
        vals = compute_wc_kpis(abs(inv_ec.get(ec, 0.0)), abs(rec_ec.get(ec, 0.0)),
                               abs(pay_ec.get(ec, 0.0)), rev_l, cogs_l)
        for kk in ent_kpi:
            ent_kpi[kk][ec] = vals[kk]

    rows.append(_wc_consl_kpi_header_row(entity_codes))
    for rid, label, kk in _WC_KPI_ROWS:
        rows.append({
            "id": rid, "label": label, "row_kind": "kpi", "is_bold": False,
            "entity_amounts": {ec: round(ent_kpi[kk].get(ec, 0.0), 1) for ec in entity_codes},
            "aggregated": 0.0, "ic_eliminations": 0.0, "consolidation": kpis_consl[kk],
            "has_children": False, "children": [],
        })

    col_label = labels.get("cm", period_label(yr, mo))
    out: dict[str, Any] = {
        "statement": "wc", "period_grain": period_grain, "year": yr, "month": mo,
        "col_label": col_label,
        "entities": entity_dicts, "rows": rows,
    }
    if iso_year is not None:
        out["iso_year"] = iso_year
    if iso_week is not None:
        out["iso_week"] = iso_week
    return out


# ---------------------------------------------------------------------------
# Monthly builder (one cumulative-balance column per month-end)
# ---------------------------------------------------------------------------

def build_wc_monthly(
    session: Session,
    *,
    year: int,
    month: int,
    entity: Optional[str] = None,
    span: str = "12m",
) -> dict[str, Any]:
    """MonthlyResponse for working capital: each ``amounts[YYYY-MM]`` is the
    month-END cumulative balance.  NWC subtotal + DAYS KPIs (fixed LTM at the
    anchor month) per column.

    span='12m' (default): last 12 month-end balances ending at (year, month).
    span='fy3': all _fy_span_periods months PLUS FY/YTD balance totals.
    """
    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)

    effective_span = "fy3" if span == "fy3" else "12m"
    if effective_span == "fy3":
        from app.services.fin_compat_sql import _fy_span_periods, _fy_span_totals
        periods = _fy_span_periods(year, month)
        totals = _fy_span_totals(year, month)
    else:
        periods = _last_12_periods(year, month)
        totals = []

    period_keys = [period_key(y, m) for y, m in periods]
    total_keys = [t["key"] for t in totals]
    all_keys = period_keys + total_keys

    sql, params = wc_monthly_grain_sql(year, month, ent_frag, span=effective_span)
    grains = _keep_wc([dict(r._mapping) for r in session.execute(text(sql), params).fetchall()])

    def _node(node_id, label, depth, total, children, path, grs=None):
        return {
            "id": node_id, "line_code": node_id,
            "row_kind": "subtotal" if depth <= 1 else "line", "label": label,
            "amounts": {k: round(total.get(k, 0.0), 2) for k in all_keys},
            "is_bold": depth <= 1, "has_children": bool(children), "children": children,
        }

    def _leaf(akey, label, am, path, gid, grs=None):
        return {
            "id": f"wc-acc-{_safe_seg(akey)}", "line_code": gid or f"wc-acc-{_safe_seg(akey)}",
            "row_kind": "account", "label": label,
            "amounts": {k: round(am.get(k, 0.0), 2) for k in all_keys},
            "is_bold": False, "has_children": False, "children": [],
        }

    rows = _wc_tree(grains, 0, {}, 0, keys=all_keys,
                    get_am=lambda g: _grain_am(g, all_keys), node=_node, leaf=_leaf)
    _rename_and_sort_sections(rows)
    _sort_wc_display_order(rows)
    _wc_normalize_row_kinds(rows)
    _wc_apply_aggregate_bold_only(rows)

    nwc = _nwc_total(grains, all_keys)
    rows.append({
        "id": "wc-net-total", "line_code": "NWC", "row_kind": "subtotal",
        "label": "Net working capital",
        "amounts": {k: round(nwc.get(k, 0.0), 2) for k in all_keys},
        "is_bold": True, "has_children": False, "children": [],
    })

    # Fixed LTM denominator (anchor month) for all period columns (mirrors legacy).
    rev_ltm, cogs_ltm = _ltm_through(session, last_day(year, month), ent_frag)
    inv = _l3_balance(grains, WC_INV_L3, all_keys)
    rec = _l3_balance(grains, WC_REC_L3, all_keys)
    pay = _l3_balance(grains, WC_PAY_L3, all_keys)

    rows.append({
        "id": "wc-kpi-header", "label": "KPIs — working capital days",
        "row_kind": "kpi_header", "is_bold": False,
        "amounts": {k: 0.0 for k in all_keys}, "has_children": False, "children": [],
    })
    for rid, label, kk in _WC_KPI_ROWS:
        kpi_am: dict[str, float] = {}
        for pk in all_keys:
            vals = compute_wc_kpis(abs(inv[pk]), abs(rec[pk]), abs(pay[pk]), rev_ltm, cogs_ltm)
            kpi_am[pk] = vals[kk]
        rows.append({
            "id": rid, "line_code": f"WC_{kk}", "row_kind": "kpi", "label": label,
            "amounts": {k: round(kpi_am.get(k, 0.0), 1) for k in all_keys},
            "is_bold": False, "has_children": False, "children": [],
        })

    out: dict[str, Any] = {
        "statement": "wc", "year": year, "month": month,
        "periods": [{"year": y, "month": m, "label": period_label(y, m)} for y, m in periods],
        "rows": rows,
    }
    if totals:
        out["totals"] = [
            {"key": t["key"], "label": t["label"], "kind": t["kind"], "year": t["year"]}
            for t in totals
        ]
    return out


# ---------------------------------------------------------------------------
# L4 trend builder (cumulative balance at each window end; magnitude)
# ---------------------------------------------------------------------------

def build_wc_l4_trend(
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
    """L4TrendResponse for a WC line — cumulative balance at each window end,
    shown as a magnitude (``abs``) so credit-side lines read positive."""
    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)

    sql, windows = wc_l4_trend_sql(year, month, grain, level_2, level_3, level_4, ent_frag)
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
            "label": w["label"], "current": round(cur, 2), "previous": round(prev, 2),
            "delta_pct": delta, "date_from": w["date_from"], "date_to": w["date_to"],
        })
    prev_end = windows[-1].get("prev_end")
    prev_label = prev_end.strftime("%b %Y") if hasattr(prev_end, "strftime") else (
        str(prev_end) if prev_end else "")
    return {"series": series, "col_label": period_label(year, month), "prev_label": prev_label}


# ---------------------------------------------------------------------------
# Annual exit-readiness snapshot builder (fy_py / fy / cm_py / cm)
# ---------------------------------------------------------------------------

def build_wc_snapshot_annual(
    session: Session,
    *,
    year: int,
    month: int,
    entity: Optional[str] = None,
    allowed_entities: Optional[set[str]] = None,
) -> dict[str, Any]:
    """ErSnapshotResponse for working capital — raw cumulative balances at 4 dates
    (fy_py / fy / cm_py / cm) with snapshot deltas (delta_fy, delta_cm), an NWC
    subtotal and DAYS KPIs (LTM ending at each column's period date).

    ``allowed_entities`` (fail-closed tenant isolation): set of 2-char
    ``entity_prefix`` values, or ``None`` for admin/unrestricted.  When provided it
    is the ONLY entity filter (``entity`` ignored — caller has already intersected
    it) and threads through both the balance grain and the LTM denominators; an
    EMPTY set fails closed (matches nothing).  ``None`` preserves legacy behaviour.
    """
    if allowed_entities is not None:
        ent_frag = (
            entities_sql_fragment(sorted(allowed_entities))
            if allowed_entities else "AND 1 = 0"
        )
    else:
        ep = resolve_entity_prefix(session, entity)
        ent_frag = entity_sql_fragment(ep)

    sql, params = wc_snapshot_grain_sql(year, month, ent_frag)
    grains = _keep_wc([dict(r._mapping) for r in session.execute(text(sql), params).fetchall()])
    keys = list(_WC_SNAP_KEYS)  # fy_py, fy, cm_py, cm

    def _node(node_id, label, depth, total, children, path, grs=None):
        return {
            "id": node_id, "line_code": node_id,
            "row_kind": "subtotal" if depth <= 1 else "line", "label": label,
            "amounts": _round_am(total), "deltas": _round_am(_snap_deltas(total)),
            "invert_delta": False, "is_bold": depth <= 1, "drill": _wc_drill(path),
            "has_children": bool(children), "children": children,
        }

    def _leaf(akey, label, am, path, gid, grs=None):
        rid = f"wc-acc-{_safe_seg(akey)}"
        return {
            "id": rid, "line_code": gid or rid, "row_kind": "account", "label": label,
            "amounts": _round_am(am), "deltas": _round_am(_snap_deltas(am)),
            "invert_delta": False, "is_bold": False,
            "drill": _wc_drill(path, gid) if gid else None,
            "has_children": False, "children": [],
        }

    rows = _wc_tree(grains, 0, {}, 0, keys=keys, get_am=lambda g: _grain_am(g, keys),
                    node=_node, leaf=_leaf)
    _rename_and_sort_sections(rows)
    _sort_wc_display_order(rows)
    _wc_normalize_row_kinds(rows)
    _wc_apply_aggregate_bold_only(rows)

    nwc = _nwc_total(grains, keys)
    rows.append({
        "id": "wc-net-total", "line_code": "NWC", "row_kind": "subtotal",
        "label": "Net working capital", "amounts": _round_am(nwc),
        "deltas": _round_am(_snap_deltas(nwc)), "invert_delta": False,
        "is_bold": True, "drill": None, "has_children": False, "children": [],
    })

    ytg_np = _snapshot_net_profit_ytg_plan(session, year, month, ent_frag)
    _apply_snapshot_fy_forecast_amounts(rows, ytg_np, bump_codes=())

    # DISPLAY GATE (Phase 4 extension): the Forecast (fy_f) column renders ONLY when
    # a real active + include_in_reporting plan version supplies plan values.  WC
    # uses the "BS" plan version (same reader the WC two-view uses); the reader
    # returns {} for parked/no-version/no-rows.  The flag drives DISPLAY only.
    wc_plan_map = load_position_plan_map_pref(session, "BS", year, month, ent_frag)

    # LTM denominators ending at each snapshot column's representative date.
    col_dates = {
        "dec_py2": last_day(year - 3, 12),
        "fy_py": last_day(year - 2, 12),
        "fy": last_day(year - 1, 12),
        "cm_py": last_day(year - 1, month),
        "fy_f": last_day(year, 12),
        "cm": last_day(year, month),
    }
    col_ltm = {col: _ltm_through(session, d, ent_frag) for col, d in col_dates.items()}
    snapshot_keys = keys + ["fy_f"]
    rows.extend(_kpi_rows(grains, col_ltm, snapshot_keys, delta_fn=_snap_deltas))

    return {
        "statement": "wc", "year": year, "month": month,
        "col_labels": col_labels_bs_snapshot(year, month), "rows": rows,
        "has_plan_data": bool(wc_plan_map),
    }


# ---------------------------------------------------------------------------
# WC timeline builder (running cumulative TWC components per period end)
# ---------------------------------------------------------------------------

def build_wc_timeline(
    session: Session,
    *,
    year: int,
    month: int,
    grain: str = "month",
    entity: Optional[str] = None,
) -> dict[str, Any]:
    """WcTimelineResponse — running cumulative TWC/OWC component magnitudes per
    period end with an average-TWC reference window.

    series[i] = {label, date, inventories, trade_receivables, trade_payables,
                 other_wc, twc, nwc} where twc = inv + rec + pay (magnitudes) and
    nwc = twc + other_wc.  avg_twc = mean TWC over the last sub-period window.
    """
    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)

    sql, periods_meta = wc_timeline_sql(year, month, grain, ent_frag)
    rows = session.execute(text(sql)).fetchall()
    if not rows:
        return {"grain": grain, "series": [], "avg_twc": 0.0,
                "avg_period_start": "", "avg_period_end": ""}

    rows_by_date: dict[str, dict] = {}
    for r in rows:
        d = dict(r._mapping) if hasattr(r, "_mapping") else dict(r)
        k = d.get("cut_date")
        rows_by_date[k.isoformat() if hasattr(k, "isoformat") else str(k)] = d

    series = []
    twc_vals: list[float] = []
    for p in periods_meta:
        r = rows_by_date.get(p["date"]) or {}
        inv = float(r.get("inventories") or 0.0)
        tr = float(r.get("trade_receivables") or 0.0)
        tp = float(r.get("trade_payables") or 0.0)
        owc = float(r.get("other_wc") or 0.0)
        twc = round(inv + tr + tp, 2)
        nwc = round(twc + owc, 2)
        twc_vals.append(twc)
        series.append({
            "label": p["label"], "date": p["date"],
            "inventories": round(inv, 2), "trade_receivables": round(tr, 2),
            "trade_payables": round(tp, 2), "other_wc": round(owc, 2),
            "twc": twc, "nwc": nwc,
        })

    avg_n = 12 if grain == "month" else 13 if grain == "week" else 30
    avg_n = min(avg_n, len(twc_vals))
    avg_vals = twc_vals[-avg_n:] if avg_n else []
    avg_twc = round(sum(avg_vals) / len(avg_vals), 2) if avg_vals else 0.0
    avg_start = periods_meta[-avg_n]["label"] if len(periods_meta) >= avg_n and avg_n else (
        periods_meta[0]["label"] if periods_meta else "")
    avg_end = periods_meta[-1]["label"] if periods_meta else ""

    return {
        "grain": grain, "series": series, "avg_twc": avg_twc,
        "avg_period_start": avg_start, "avg_period_end": avg_end,
    }


# ---------------------------------------------------------------------------
# Deterministic narrative (PlNarrativeResponse shape)
# ---------------------------------------------------------------------------

def build_wc_narrative(
    session: Session,
    year: int, month: int, entity: Optional[str],
    *,
    period_grain: str = "month",
    iso_year: Optional[int] = None,
    iso_week: Optional[int] = None,
    max_bullets: Optional[int] = None,
    use_llm: bool = False,
) -> dict[str, Any]:
    """Deterministic WC key-drivers narrative at legacy depth (narrative core)."""
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
            "wc",
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

    def _find_nwc(rs: list[dict]) -> Optional[dict]:
        for r in rs:
            if r.get("line_code") == "NWC":
                return r
            hit = _find_nwc(r.get("children") or [])
            if hit:
                return hit
        return None

    nwc_row = _find_nwc(rows)
    nwc_am = (nwc_row or {}).get("amounts", {}) or {}
    nwc_cm = float(nwc_am.get("cm") or 0)
    nwc_mom = float(nwc_cm - float(nwc_am.get("pm") or 0))
    base_cm = abs(nwc_cm) or 1.0

    def _gl_detail(line_code: str, signed_mom_eur: float) -> Optional[dict]:
        try:
            return build_wc_line_detail(
                session, line_code, yr, mo, entity,
                use_llm=False, line_mom_keur=signed_mom_eur / 1000.0,
            )
        except Exception:
            return None

    def _anchor_boost(lc: str) -> float:
        if lc in ("NWC", "TWC", "OWC"):
            return 25.0
        if lc in ("AR", "INVENTORY", "AP"):
            return 15.0
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
        statement_kind="pl",
        period_label=cm_label,
        prior_label=pm_label,
        base_label="net working capital",
        base_cm=base_cm,
        balance_style=False,
        tone_mode="favorable",
        movement_noun="the move",
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

    if abs(nwc_mom) < 0.5:
        headline = f"{group_label} working capital at {cm_label}"
    else:
        dir_word = "rose" if nwc_mom > 0 else "fell"
        lead = f", led by {primary_drivers[0]['label']}" if primary_drivers else ""
        headline = f"{group_label} net working capital {dir_word} in {cm_label}{lead}"[:120]

    intro = f"Net working capital in {cm_label} stood at {core.fmt_keur(nwc_cm)}"
    if abs(nwc_mom) >= 0.5:
        td = "up" if nwc_mom > 0 else "down"
        intro += f", {td} {core.fmt_keur(abs(nwc_mom))} from {pm_label}"
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
            "algorithm_version": "wc_narrative_compat_v2", "llm_used": llm_used,
            "max_bullets": cap, "cache_hit": False,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "entity_scope": entity or "",
        },
    }


# ---------------------------------------------------------------------------
# Line detail (cumulative balances; PlLineDetailResponse shape, WC scope)
# ---------------------------------------------------------------------------

_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _find_row_in_tree(rows: list[dict], line_code: str) -> Optional[dict]:
    for r in rows:
        if r.get("line_code") == line_code or r.get("id") == line_code:
            return r
        found = _find_row_in_tree(r.get("children") or [], line_code)
        if found:
            return found
    return None


def _wc_scope_fragment(
    session: Session, line_code: str, year: int, month: int, entity: Optional[str],
    ent_frag: str, *, prebuilt_rows: Optional[list[dict]] = None,
) -> tuple[str, dict[str, Any], str]:
    """Resolve a WC line_code to a GL WHERE fragment (level_0='BS', TWC/OWC).

    Returns ``(scope_sql, params, label)``.  ``scope_sql`` always joins
    ``dim_gl_na na`` (so the l6_na_mapping filter is available) and references the
    ``l``/``e``/``a``/``na`` aliases used by the line-detail queries.
    """
    parts = ["a.level_0 = 'BS'", "na.l6_na_mapping IN ('TWC','OWC')"]
    params: dict[str, Any] = {}
    label = line_code
    up = (line_code or "").strip().upper()

    if up in ("NWC", "WC_CCC"):
        label = "Net working capital" if up == "NWC" else "Cash conversion cycle"
    elif up in _KPI_L3:
        parts.append("TRIM(a.level_3) = :l3")
        params["l3"] = _KPI_L3[up]
        label = _KPI_L3[up]
    else:
        # Resolve drill from the WC statement tree (clicked section / line / account).
        # Reuse a prebuilt tree when the caller supplies one (anomaly engine) so we
        # do not re-run the whole WC statement build per line — the dominant N+1 cost.
        try:
            if prebuilt_rows is not None:
                row = _find_row_in_tree(prebuilt_rows, line_code)
            else:
                stmt = build_wc_statement_compat(session, period_grain="month",
                                                 year=year, month=month, entity=entity)
                row = _find_row_in_tree(stmt.get("rows") or [], line_code)
        except Exception:
            row = None
        drill = (row or {}).get("drill") or {}
        label = (row or {}).get("label") or line_code
        gid = (drill.get("gl_account_id") or "").strip()
        if gid:
            parts.append("a.gl_account_id = :gid")
            params["gid"] = gid
        else:
            l6 = (drill.get("l6_na_mapping") or "").strip()
            if l6:
                parts.append("na.l6_na_mapping = :l6")
                params["l6"] = l6
            for key, col, pkey in (
                ("level_2", "a.level_2", "dl2"),
                ("level_3", "a.level_3", "dl3"),
                ("level_4", "NULLIF(TRIM(a.level_4), '')", "dl4"),
            ):
                v = (drill.get(key) or "").strip()
                if v:
                    parts.append(f"{col} = :{pkey}")
                    params[pkey] = v

    if ent_frag:
        frag = ent_frag.strip()
        if frag.upper().startswith("AND "):
            frag = frag[4:]
        parts.append(frag)
    return " AND ".join(parts), params, label


_WC_LD_FROM = """
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        JOIN dim_gl_na na
          ON na.account_number_group = l.account_number_group
         AND na.fiscal_year = l.fiscal_year"""


def build_wc_line_detail(
    session: Session,
    line_code: str,
    year: int, month: int, entity: Optional[str],
    *,
    limit: int = 50,
    timeline_months: int = 12,
    use_llm: bool = True,
    line_mom_keur: Optional[float] = None,
    anchor_year: Optional[int] = None,
    anchor_month: Optional[int] = None,
    concentration_only: bool = False,
    prebuilt_rows: Optional[list[dict]] = None,
) -> dict[str, Any]:
    """PlLineDetailResponse for a WC line — CUMULATIVE account balances (raw sign)
    at the cm/pm month-ends, top current-month bookings, and a 12-month balance
    timeline.  Same response shape as the BS / P&L line detail.

    ``prebuilt_rows`` lets a caller (the anomaly engine) hand in the already-built
    WC statement tree so ``_wc_scope_fragment`` does not re-run
    ``build_wc_statement_compat`` per line (the dominant N+1 cost).
    """
    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)
    scope_sql, base_params, label = _wc_scope_fragment(
        session, line_code, year, month, entity, ent_frag,
        prebuilt_rows=prebuilt_rows)

    d_cm = last_day(year, month).isoformat()
    pm_y, pm_m = pm(year, month)
    d_pm = last_day(pm_y, pm_m).isoformat()

    acc_sql = text(f"""
        SELECT
            l.account_number_group,
            MAX(a.gl_account_id)   AS gl_account_id,
            MAX(a.account_name)    AS account_name,
            COALESCE(SUM(CASE WHEN e.posting_date <= :d_cm THEN l.amount ELSE 0 END), 0)::float8 AS balance_cm,
            COALESCE(SUM(CASE WHEN e.posting_date <= :d_pm THEN l.amount ELSE 0 END), 0)::float8 AS balance_pm
        {_WC_LD_FROM}
        WHERE {scope_sql}
          AND e.posting_date <= :d_cm
        GROUP BY l.account_number_group
        HAVING ABS(COALESCE(SUM(CASE WHEN e.posting_date <= :d_cm THEN l.amount ELSE 0 END), 0)) > 0.01
        ORDER BY ABS(COALESCE(SUM(CASE WHEN e.posting_date <= :d_cm THEN l.amount ELSE 0 END), 0)) DESC
        LIMIT 30
    """)
    acc_rows = session.execute(acc_sql, {**base_params, "d_cm": d_cm, "d_pm": d_pm}).fetchall()
    accounts_out = []
    for a in acc_rows:
        cm = float(a[3] or 0)
        pm_v = float(a[4] or 0)
        accounts_out.append({
            "gl_account_id": a[1] or a[0], "account_name": a[2] or "",
            "balance_cm": round(cm / 1000, 2), "balance_pm": round(pm_v / 1000, 2),
            "delta": round((cm - pm_v) / 1000, 2),
        })

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
        {_WC_LD_FROM}
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
            "booking_line_id": b[0], "posting_date": b[1], "journal_entry_number": b[2],
            "legal_entity_code": b[3], "fiscal_year": b[4], "gl_account_id": b[5] or "",
            "account_name": b[6], "amount": round(float(b[7] or 0) / 1000, 2),
            "line_note": b[8] or "", "reference": b[9] or "",
            "counter_gl_account_id": None, "counter_account_name": None,
        })

    # Concentration-only fast path (anomaly engine) — skip timeline/sub-lines/commentary.
    if concentration_only:
        from app.services.fin_compat_narrative_core import concentration_only_payload
        return concentration_only_payload(
            line_code, label, year, month, entity, accounts_out, top_bookings)

    periods = _last_12_periods(year, month)[-timeline_months:]
    period_defs = [
        {"year": y, "month": m,
         "label": f"{_MONTH_ABBR[m - 1]}{str(y)[-2:]}", "key": period_key(y, m)}
        for y, m in periods
    ]
    tl_cases = " ".join(
        f"COALESCE(SUM(CASE WHEN e.posting_date <= '{last_day(y, m).isoformat()}' "
        f"THEN l.amount ELSE 0 END),0) AS \"{period_key(y, m)}\","
        for y, m in periods
    ).rstrip(",")
    tl_sql = text(f"""
        SELECT
            l.account_number_group,
            MAX(a.gl_account_id) AS gl_account_id,
            MAX(a.account_name)  AS account_name,
            {tl_cases}
        {_WC_LD_FROM}
        WHERE {scope_sql}
          AND e.posting_date <= :d_cm
        GROUP BY l.account_number_group
        HAVING ABS(COALESCE(SUM(CASE WHEN e.posting_date <= :d_cm THEN l.amount ELSE 0 END),0)) > 0.01
        ORDER BY ABS(COALESCE(SUM(CASE WHEN e.posting_date <= :d_cm THEN l.amount ELSE 0 END),0)) DESC
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

    commentary = _wc_commentary(accounts_out, top_bookings)
    return {
        "line_code": line_code, "label": label,
        "year": year, "month": month, "entity": entity,
        "accounts": accounts_out, "top_bookings": top_bookings,
        "bridge": [{"label": a["account_name"] or a["gl_account_id"], "value": a["balance_cm"]}
                   for a in accounts_out[:12]],
        "periods": period_defs, "accounts_timeline": accounts_timeline,
        "sub_lines": [], "commentary": commentary, "outlier_facts": {},
        "suggested_prompts": [],
        "meta": {"llm_used": False, "algorithm_version": "wc_outliers_v1"},
    }


def _wc_commentary(accounts: list[dict], bookings: list[dict]) -> dict[str, str]:
    if not accounts:
        return {"accounts": "No GL accounts in scope for this working-capital line.", "postings": ""}
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
