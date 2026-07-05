"""Overview v2 — ONE batched summary assembly (reporting-v2 / :8011, additive).

Backs ``GET /api/v1/financials/overview/summary`` (P3 of the Overview redesign,
``docs/overview-v2-redesign-plan.md`` §4/§6).  In ONE round-trip it assembles the
sections the redesigned Overview page needs, replacing ~6 client calls:

  * hero KPIs        — Revenue YoY, EBIT + margin, Cash headline, CCC / NWC
  * working capital  — DSO/DPO/DIO/CCC/NWC ratios + the 3 deep-dive level rows
                       (inventories / trade-AR / trade-AP, each Δmonth and Δfy)
  * cash headline    — signed BS stock over level_3 = 'Cash & cash equivalents'
  * top customer / supplier deltas
  * DuPont finding inputs (existing EBIT-based build_dupont values ONLY — no
    net-income / ROCE net-new logic)
  * recent-months exception alerts

It REUSES the signed-off builders verbatim (no formula/sign is re-derived here):
``overview_metrics.build_ebit_table`` / ``build_dupont``,
``fin_compat_wc.build_wc_statement_compat`` / ``build_wc_snapshot_annual``,
``overview_top_entities.build_top_entities`` and
``overview_alerts.build_recent_month_alerts``; optionally the OFF-by-default
``mart_overview`` accelerator for the EBIT/revenue hero.

================================================================================
SECURITY — MANDATORY fail-closed tenant isolation (the plan's security note)
================================================================================
The endpoint resolves ``entity_visibility.visible_entity_codes()`` (which returns
``legal_entity_code`` values) and maps them to the 2-char ``entity_prefix`` set the
services filter on (:func:`map_codes_to_prefixes`).  The resulting *prefix* set is
threaded into EVERY sub-query as ``allowed_entities`` with identical semantics
everywhere:

    None       → admin / unrestricted (no entity filter)
    empty set  → FAIL-CLOSED → a zeroed summary, NO cross-entity data, no 500
    non-empty  → every sub-query is filtered to ``entity_prefix IN <set>``

The optional ``entity`` query param narrows WITHIN that boundary; a narrow to a
prefix outside the visible set fails closed (zeroed).  A non-admin whose visible
codes map to NO prefix also fails closed.

================================================================================
PERFORMANCE
================================================================================
The read Session is synchronous SQLAlchemy (``get_read_session``); a single sync
Session is NOT safe to share across threads, so the heavy sub-queries run
SEQUENTIALLY — but EVERY one carries the visibility filter, so none scans all
entities (that unbounded ``bal_mov`` fan-out is the current slowness bug).  Results
are memoised in a small visibility-aware in-process TTL cache
(:data:`settings.overview_summary_cache_ttl_s`) keyed by
``(year, month, entity, use_mart, allowed-set-hash)`` so the cache can never cross
tenants.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.services.fin_compat_sql import last_day, pm, resolve_entity_prefix
from app.services.fin_compat_wc_sql import WC_INV_L3, WC_PAY_L3, WC_REC_L3
from app.services.overview_alerts import (
    _load_monthly_actual,
    build_recent_month_alerts,
)
from app.services.overview_metrics import (
    build_dupont,
    build_ebit_rows,
    build_ebit_table,
    _ebit_entity_name_map,
)
from app.services.overview_top_entities import build_top_entities
from app.services.fin_compat_wc import (
    build_wc_snapshot_annual,
    build_wc_statement_compat,
)
from app.services import mart_overview

EPS = 1e-6

# level_3 → deep-dive row key/label for the WC block.
_WC_DEEP_DIVES = [
    ("inventories", WC_INV_L3),
    ("trade_receivables", WC_REC_L3),
    ("trade_payables", WC_PAY_L3),
]


# ===========================================================================
# Visibility mapping — legal_entity_code set → entity_prefix set (fail-closed)
# ===========================================================================

def map_codes_to_prefixes(
    session: Session, allowed_codes: Optional[set[str]],
) -> Optional[set[str]]:
    """Map ``visible_entity_codes()`` (legal_entity_code) → ``entity_prefix`` set.

    Preserves the fail-closed contract exactly:
      * ``None``      → admin / unrestricted → ``None``
      * empty set     → FAIL-CLOSED → empty set (deny-all)
      * non-empty     → the mapped 2-char prefixes; if NOTHING maps (codes unknown)
                        → empty set (deny-all), never ``None`` (that would leak all).
    """
    if allowed_codes is None:
        return None
    if not allowed_codes:
        return set()
    rows = session.execute(
        text(
            "SELECT DISTINCT entity_prefix FROM dim_legal_entity "
            "WHERE legal_entity_code = ANY(:codes)"
        ),
        {"codes": sorted(allowed_codes)},
    ).fetchall()
    return {str(r[0]).strip()[:2] for r in rows if r and r[0]}


def _effective_prefixes(
    session: Session,
    *,
    entity: Optional[str],
    allowed_prefixes: Optional[set[str]],
) -> tuple[Optional[set[str]], Optional[str], bool]:
    """Resolve the FINAL prefix allow-set injected into every sub-query.

    Returns ``(eff, builder_entity, denied)``:
      * ``eff``            — ``None`` (admin) or the (non-empty) visible prefix set,
                             narrowed by ``entity`` when it is inside the boundary.
      * ``builder_entity`` — the legal_entity_code to pass to admin-path builders
                             (``None`` for restricted users, who filter via ``eff``).
      * ``denied``         — True when the request must fail closed (empty
                             visibility, or an ``entity`` narrow outside visibility).
    """
    # Admin / unrestricted: builders keep their native ``entity`` handling.
    if allowed_prefixes is None:
        return None, (entity or None), False

    # Non-admin with zero visible prefixes → deny all.
    if not allowed_prefixes:
        return set(), None, True

    eff = set(allowed_prefixes)
    if entity:
        ep = resolve_entity_prefix(session, entity)  # None for 'all'/comma
        if ep is not None:
            ep2 = str(ep)[:2]
            if ep2 not in eff:
                return set(), None, True  # narrow outside visibility → deny
            eff = {ep2}
    return eff, None, False


# ===========================================================================
# Small helpers over builder output
# ===========================================================================

def _pct(num: float, den: float) -> Optional[float]:
    if abs(den) < EPS:
        return None
    return round(num / abs(den) * 100.0, 2)


def _total_row(ebit_rows: list[dict[str, Any]]) -> dict[str, float]:
    for r in ebit_rows:
        if r.get("entity_code") == "__total__":
            return r
    return {}


def _walk_wc_rows(rows: Iterable[dict[str, Any]]):
    for r in rows:
        yield r
        yield from _walk_wc_rows(r.get("children") or [])


def _wc_kpi_values(statement_rows: list[dict[str, Any]]) -> dict[str, float]:
    """Extract the cm-column DSO/DPO/DIO/CCC + NWC from the WC statement tree."""
    out = {"DSO": 0.0, "DPO": 0.0, "DIO": 0.0, "CCC": 0.0, "NWC": 0.0}
    for node in _walk_wc_rows(statement_rows):
        code = node.get("line_code")
        amounts = node.get("amounts") or {}
        cm = float(amounts.get("cm") or 0.0)
        if code in ("WC_DSO", "WC_DPO", "WC_DIO", "WC_CCC"):
            out[code[3:]] = round(cm, 2)
        elif code == "NWC":
            out["NWC"] = round(cm, 2)
    return out


def _wc_level_node(rows: Iterable[dict[str, Any]], level_3: str) -> Optional[dict[str, Any]]:
    """The aggregated level_3 node (no level_4 drill) for ``level_3``, if present."""
    for node in _walk_wc_rows(rows):
        drill = node.get("drill") or {}
        if drill.get("level_3") == level_3 and not drill.get("level_4"):
            return node
    return None


# ===========================================================================
# Sub-section builders (each carries the visibility filter)
# ===========================================================================

def _hero_ebit_rows(
    session: Session, *, entity: Optional[str], year: int, month: int,
    eff: Optional[set[str]], use_mart: bool,
) -> tuple[list[dict[str, Any]], str]:
    """EBIT rows (incl. ``__total__``) from the mart when fresh+enabled, else builder."""
    if use_mart:
        latest = mart_overview.latest_gl_load_id(session)
        if mart_overview.mart_is_fresh(session, latest):
            mart_prefix = resolve_entity_prefix(session, entity) if entity else None
            grain = mart_overview.read_overview_period(
                session, entity=mart_prefix, year=year, month=month,
                allowed_entities=eff,
            )["grain"]
            rows = build_ebit_rows(grain, _ebit_entity_name_map(session))
            return rows, "mart"
    tbl = build_ebit_table(session, entity, year, month, allowed_entities=eff)
    return tbl.get("rows") or [], "builder"


def _cash_headline(
    session: Session, *, year: int, month: int, ent_frag: str,
) -> dict[str, Optional[float]]:
    """Signed cash level (BS stock over CASH_SET) + Δmonth + Δyoy — NO ABS.

    FORMULA (docs plan §2 Area 2): ``Cash[t] = Σ amount WHERE level_3 =
    'Cash & cash equivalents' AND posting_date <= month_end(t)`` (raw stored sign;
    overdraft may be negative — do NOT ABS).  Δmonth = cm − pm, Δyoy = cm − py.
    """
    pm_y, pm_m = pm(year, month)
    d_cur = last_day(year, month).isoformat()
    d_pm = last_day(pm_y, pm_m).isoformat()
    d_py = last_day(year - 1, month).isoformat()
    sql = f"""
        SELECT
          COALESCE(SUM(CASE WHEN e.posting_date <= '{d_cur}' THEN l.amount ELSE 0 END), 0) AS cash_cm,
          COALESCE(SUM(CASE WHEN e.posting_date <= '{d_pm}'  THEN l.amount ELSE 0 END), 0) AS cash_pm,
          COALESCE(SUM(CASE WHEN e.posting_date <= '{d_py}'  THEN l.amount ELSE 0 END), 0) AS cash_py
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE a.level_0 = 'BS'
          AND a.level_3 = 'Cash & cash equivalents'
          AND e.posting_date <= '{d_cur}'
          {ent_frag}
    """
    row = session.execute(text(sql)).fetchone()
    d = dict(row._mapping) if (row is not None and hasattr(row, "_mapping")) else {}
    cm = float(d.get("cash_cm") or 0.0)
    pm_v = float(d.get("cash_pm") or 0.0)
    py_v = float(d.get("cash_py") or 0.0)
    return {
        "level": round(cm, 2),
        "delta_month": round(cm - pm_v, 2),
        "delta_yoy": round(cm - py_v, 2),
    }


def _cash_ent_frag(eff: Optional[set[str]], builder_entity: Optional[str],
                   session: Session) -> str:
    """entity_prefix AND-fragment for the cash query (alias ``l``)."""
    from app.services.fin_compat_sql import entities_sql_fragment, entity_sql_fragment
    if eff is not None:
        if not eff:
            return "AND 1 = 0"
        return entities_sql_fragment(sorted(eff))
    ep = resolve_entity_prefix(session, builder_entity)
    return entity_sql_fragment(ep)


def _cash_allowed_prefixes(
    session: Session, eff: Optional[set[str]], builder_entity: Optional[str],
) -> Optional[set[str]]:
    """The entity_prefix allow-set for the MART cash read — mirrors ``_cash_ent_frag``.

    Restricted users already carry the narrowed set in ``eff``; admins (``eff`` is
    ``None``) narrow via ``builder_entity`` (resolved to a single prefix), or stay
    unrestricted (``None``) when no entity is requested.  Keeps the mart cash reader
    filtered to EXACTLY the same entities the live cash query would see.
    """
    if eff is not None:
        return eff
    ep = resolve_entity_prefix(session, builder_entity)
    return None if ep is None else {str(ep)[:2]}


def _cash_block(
    session: Session, *, year: int, month: int,
    eff: Optional[set[str]], builder_entity: Optional[str], use_mart: bool,
) -> tuple[dict[str, Optional[float]], str]:
    """Signed cash headline from the mart when fresh+enabled, else the live query.

    Returns ``({level, delta_month, delta_yoy}, source)``.  The mart path
    (``mart_overview.read_cash_headline``) is byte-equivalent to ``_cash_headline``
    (both PURE cumulative Σ amount WHERE posting_date <= cutoff), proven on
    finssentials_v2 within rounding; it is gated on ``mart_is_fresh`` (the cash
    stock lives in ``mart_overview_bs_balance``, refreshed with the period mart).
    """
    if use_mart and mart_overview.mart_is_fresh(
        session, mart_overview.latest_gl_load_id(session)
    ):
        allowed = _cash_allowed_prefixes(session, eff, builder_entity)
        c = mart_overview.read_cash_headline(
            session, year=year, month=month, allowed_entities=allowed)
        return {"level": c["level"], "delta_month": c["delta_month"],
                "delta_yoy": c["delta_yoy"]}, "mart"
    c = _cash_headline(
        session, year=year, month=month,
        ent_frag=_cash_ent_frag(eff, builder_entity, session))
    return c, "builder"


def _wc_block(
    session: Session, *, year: int, month: int,
    eff: Optional[set[str]], use_mart: bool,
) -> tuple[dict[str, Any], str]:
    """working_capital block (dso/dpo/dio/ccc/nwc + deep-dive levels) from the mart
    when fresh+enabled, else the live WC statement/snapshot pair.

    The mart path (``mart_overview.read_wc_snapshot``) reconciles to the live WC
    statement within tolerance (Phase 4 proof on finssentials_v2: dso/dpo/dio/ccc
    <=0.1 days; nwc + level deltas <=10 EUR), gated on ``mart_wc_is_fresh``.  Both
    paths see the SAME entity scope (``allowed_entities=eff``) — restricted users
    carry the narrowed set in ``eff``; admins pass ``None`` (all entities), exactly
    as the live WC statement is called below — so the numbers agree.  FAIL-CLOSED:
    the reader short-circuits an empty ``eff`` without a DB hit (deny already
    returns a zeroed summary upstream).
    """
    if use_mart and mart_overview.mart_wc_is_fresh(session):
        snap = mart_overview.read_wc_snapshot(
            session, entity=None, year=year, month=month, allowed_entities=eff)
        return {
            "dso": snap["dso"], "dpo": snap["dpo"], "dio": snap["dio"],
            "ccc": snap["ccc"], "nwc": snap["nwc"], "levels": snap["levels"],
        }, "mart"

    # ── Live pair (correctness source of truth) ───────────────────────────────
    wc_stmt = build_wc_statement_compat(
        session, period_grain="month", year=year, month=month,
        allowed_entities=eff,
    )
    wc_snap = build_wc_snapshot_annual(
        session, year=year, month=month, allowed_entities=eff,
    )
    kpis = _wc_kpi_values(wc_stmt.get("rows") or [])
    snap_rows = wc_snap.get("rows") or []
    levels: list[dict[str, Any]] = []
    for key, l3 in _WC_DEEP_DIVES:
        stmt_node = _wc_level_node(wc_stmt.get("rows") or [], l3)
        snap_node = _wc_level_node(snap_rows, l3)
        amounts = (stmt_node or {}).get("amounts") or {}
        level_cm = float(amounts.get("cm") or 0.0)
        delta_month = round(level_cm - float(amounts.get("pm") or 0.0), 2)
        delta_fy = float(((snap_node or {}).get("deltas") or {}).get("delta_fy") or 0.0)
        levels.append({
            "key": key, "label": l3,
            "level": round(level_cm, 2),
            "delta_month": delta_month,
            "delta_fy": round(delta_fy, 2),
        })
    return {
        "dso": kpis["DSO"], "dpo": kpis["DPO"], "dio": kpis["DIO"],
        "ccc": kpis["CCC"], "nwc": kpis["NWC"], "levels": levels,
    }, "builder"


def _top_block(
    session: Session, *, year: int, month: int,
    eff: Optional[set[str]], builder_entity: Optional[str], use_mart: bool,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """(top_customer_resp, top_supplier_resp, source) — mart when fresh+enabled.

    The mart path (``mart_overview.read_top_entities``) matches the live
    ``build_top_entities`` to the cent (Phase 4 Bug-2 fix: full-precision
    ``amount_eur`` + /1000-round-once), gated on ``mart_top_is_fresh``.  Entity scope
    mirrors the live call exactly: restricted users carry it in ``eff``; admins
    narrow via ``builder_entity`` (resolved to a single prefix by
    :func:`_cash_allowed_prefixes`).  FAIL-CLOSED: the reader short-circuits an empty
    allow-set without a fact scan.
    """
    if use_mart and mart_overview.mart_top_is_fresh(session):
        allowed = _cash_allowed_prefixes(session, eff, builder_entity)
        cust = mart_overview.read_top_entities(
            session, year=year, month=month, type="customer", rank_by="cm",
            limit=5, allowed_entities=allowed)
        supp = mart_overview.read_top_entities(
            session, year=year, month=month, type="supplier", rank_by="cm",
            limit=5, allowed_entities=allowed)
        return cust, supp, "mart"

    cust = build_top_entities(
        session, year=year, month=month, type="customer", rank_by="cm",
        limit=5, entity=builder_entity, allowed_entities=eff,
    )
    supp = build_top_entities(
        session, year=year, month=month, type="supplier", rank_by="cm",
        limit=5, entity=builder_entity, allowed_entities=eff,
    )
    return cust, supp, "builder"


def _group_gross_margin_pct(
    session: Session,
    *,
    year: int,
    month: int,
    prefixes: Optional[set[str]],
) -> Optional[float]:
    """Group gross margin % for one calendar month (fact_sales / fact_com, Area 1).

    ``100 · (Rev − COM) / Rev`` with Rev/COM in kEUR; returns ``None`` when
    ``|Rev| < EPS``.  Uses posting_date windows (sales-fact regime).
    """
    d0 = date(year, month, 1).isoformat()
    d1 = last_day(year, month).isoformat()
    rev_map = _load_monthly_actual(
        session, fact="fact_sales", value_col="gross_sales",
        level_3="Net sales", d0=d0, d1=d1, prefixes=prefixes,
    )
    com_map = _load_monthly_actual(
        session, fact="fact_com", value_col="cost_of_materials",
        level_3="Cost of materials", d0=d0, d1=d1, prefixes=prefixes,
    )
    rev = sum(rev_map.values())
    com = sum(com_map.values())
    if abs(rev) < EPS:
        return None
    return round(100.0 * (rev - com) / rev, 2)


def _performance_block(
    session: Session,
    *,
    year: int,
    month: int,
    builder_entity: Optional[str],
    eff: Optional[set[str]],
    revenue: dict[str, Any],
    ebit: dict[str, Any],
) -> dict[str, Any]:
    """Performance YoY / vs-Plan inputs for Overview block (1).

    Revenue + EBIT come from the already-computed hero (GL / EBIT table).
    vs-Plan reuses ``build_statement_plan_response`` (NET_SALES, GROSS_MARGIN_PCT).
    Gross margin actual uses fact_sales/com (same grain as recent-month alerts).
    """
    from app.services.fin_compat_pl import build_statement_plan_response

    plan_resp = build_statement_plan_response(
        session, "PL", year, month, builder_entity, allowed_prefixes=eff,
    )
    lines = {l["line_code"]: l for l in (plan_resp.get("lines") or [])}
    ns = lines.get("NET_SALES", {})
    gm_line = lines.get("GROSS_MARGIN_PCT", {})
    has_plan = bool(plan_resp.get("has_plan_data"))
    plan_cm = float(ns.get("plan_cm") or 0) if ns else 0.0
    plan_vs = float(ns.get("plan_vs_actual") or 0) if ns else 0.0

    gm = _group_gross_margin_pct(session, year=year, month=month, prefixes=eff)
    gm_py = _group_gross_margin_pct(
        session, year=year - 1, month=month, prefixes=eff,
    )
    plan_gm = (
        float(gm_line.get("plan_cm"))
        if gm_line.get("plan_cm") is not None
        else None
    )

    return {
        "revenue": {
            "cm": revenue["cm"],
            "cm_py": revenue["cm_py"],
            "yoy_pct": revenue["yoy_pct"],
            "has_plan": has_plan and abs(plan_cm) > EPS,
            "plan_cm": round(plan_cm, 2) if has_plan and abs(plan_cm) > EPS else None,
            "plan_vs_actual": (
                round(plan_vs, 2) if has_plan and abs(plan_cm) > EPS else None
            ),
            "var_pct": (
                _pct(plan_vs, plan_cm)
                if has_plan and abs(plan_cm) > EPS
                else None
            ),
            "coverage_pct": (
                ns.get("coverage_pct")
                if has_plan and abs(plan_cm) > EPS
                else None
            ),
        },
        "gross_margin": {
            "pct": gm,
            "yoy_pp": (
                round(gm - gm_py, 2)
                if gm is not None and gm_py is not None
                else None
            ),
            "plan_pct": plan_gm if has_plan and plan_gm is not None else None,
            "plan_vs_actual_pp": (
                round(gm - plan_gm, 2)
                if has_plan and gm is not None and plan_gm is not None
                else None
            ),
        },
        "ebit": {
            "cm": ebit["cm"],
            "margin_pct": ebit["margin_pct"],
        },
    }


def _top_entity_delta(resp: dict[str, Any]) -> Optional[dict[str, Any]]:
    rows = resp.get("rows") or []
    if not rows:
        return None
    top = rows[0]
    return {
        "name": top.get("name"),
        "rank": top.get("rank"),
        "cm": top.get("cm"),
        "delta_cm_py": top.get("delta_cm_py"),
        "delta_ytd": top.get("delta_ytd"),
    }


# ===========================================================================
# Zeroed (fail-closed) summary
# ===========================================================================

def _zeroed_summary(*, entity: Optional[str], year: int, month: int,
                    reason: str) -> dict[str, Any]:
    return {
        "meta": {
            "year": year, "month": month, "entity": entity,
            "source": "fail_closed", "reason": reason, "mart_fresh": False,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "hero": {
            "revenue": {"cm": 0.0, "cm_py": 0.0, "ytd": 0.0, "ytd_py": None,
                        "yoy_pct": None},
            "ebit": {"cm": 0.0, "ytd": 0.0, "margin_pct": None},
            "cash": {"level": 0.0, "delta_month": 0.0, "delta_yoy": 0.0},
            "working_capital": {"ccc": 0.0, "nwc": 0.0},
        },
        "working_capital": {
            "dso": 0.0, "dpo": 0.0, "dio": 0.0, "ccc": 0.0, "nwc": 0.0,
            "levels": [],
        },
        "cash": {"level": 0.0, "delta_month": 0.0, "delta_yoy": 0.0},
        "top_customer": None,
        "top_supplier": None,
        "dupont": None,
        "performance": {
            "revenue": {
                "cm": 0.0, "cm_py": 0.0, "yoy_pct": None,
                "has_plan": False, "plan_cm": None, "plan_vs_actual": None,
                "var_pct": None, "coverage_pct": None,
            },
            "gross_margin": {
                "pct": None, "yoy_pp": None, "plan_pct": None,
                "plan_vs_actual_pp": None,
            },
            "ebit": {"cm": 0.0, "margin_pct": None},
        },
        "alerts": [],
    }


# ===========================================================================
# In-process visibility-aware TTL cache
# ===========================================================================

_CACHE: dict[tuple, tuple[float, dict[str, Any]]] = {}


def _allowed_hash(allowed_prefixes: Optional[set[str]]) -> str:
    if allowed_prefixes is None:
        return "admin"
    if not allowed_prefixes:
        return "deny"
    return ",".join(sorted(allowed_prefixes))


def clear_overview_summary_cache() -> None:
    """Test seam — drop all memoised summaries."""
    _CACHE.clear()


# ===========================================================================
# Public entrypoint
# ===========================================================================

def build_overview_summary(
    session: Session,
    *,
    entity: Optional[str],
    year: int,
    month: int,
    allowed_prefixes: Optional[set[str]],
    use_mart: Optional[bool] = None,
    cache_ttl_s: Optional[int] = None,
) -> dict[str, Any]:
    """Assemble the batched Overview summary (see module docstring).

    ``allowed_prefixes`` is the visibility boundary ALREADY mapped to
    ``entity_prefix`` (via :func:`map_codes_to_prefixes`): ``None`` = admin, empty
    = fail-closed, non-empty = filter.  PURE READ — builds nothing, writes nothing.
    """
    use_mart = settings.overview_summary_use_mart if use_mart is None else use_mart
    ttl = settings.overview_summary_cache_ttl_s if cache_ttl_s is None else cache_ttl_s

    cache_key = (year, month, (entity or None), bool(use_mart),
                 _allowed_hash(allowed_prefixes))
    if ttl > 0:
        hit = _CACHE.get(cache_key)
        if hit and hit[0] > time.monotonic():
            return hit[1]

    eff, builder_entity, denied = _effective_prefixes(
        session, entity=entity, allowed_prefixes=allowed_prefixes,
    )
    if denied:
        payload = _zeroed_summary(entity=entity, year=year, month=month,
                                  reason="fail_closed_visibility")
        if ttl > 0:
            _CACHE[cache_key] = (time.monotonic() + ttl, payload)
        return payload

    # ── Hero: EBIT + revenue (mart when fresh+enabled, else builder) ──────────
    ebit_rows, ebit_source = _hero_ebit_rows(
        session, entity=builder_entity, year=year, month=month,
        eff=eff, use_mart=bool(use_mart),
    )
    total = _total_row(ebit_rows)
    to_cm = float(total.get("to_cm") or 0.0)
    to_cm_py = float(total.get("to_cm_py") or 0.0)
    to_ytd = float(total.get("to_ytd") or 0.0)
    ebit_cm = float(total.get("ebit_cm") or 0.0)
    ebit_ytd = float(total.get("ebit_ytd") or 0.0)

    revenue = {
        "cm": round(to_cm, 2), "cm_py": round(to_cm_py, 2),
        "ytd": round(to_ytd, 2), "ytd_py": None,
        "yoy_pct": _pct(to_cm - to_cm_py, to_cm_py),
    }
    ebit = {
        "cm": round(ebit_cm, 2), "ytd": round(ebit_ytd, 2),
        "margin_pct": _pct(ebit_cm, to_cm),
    }

    # ── Working capital: ratios + NWC + deep-dive levels (mart when fresh) ─────
    working_capital, wc_source = _wc_block(
        session, year=year, month=month, eff=eff, use_mart=bool(use_mart),
    )

    # ── Cash headline (signed BS stock, NO ABS) — mart when fresh+enabled ──────
    cash, cash_source = _cash_block(
        session, year=year, month=month,
        eff=eff, builder_entity=builder_entity, use_mart=bool(use_mart),
    )

    # ── Top customer / supplier deltas (mart when fresh+enabled) ──────────────
    top_cust, top_supp, top_source = _top_block(
        session, year=year, month=month, eff=eff,
        builder_entity=builder_entity, use_mart=bool(use_mart),
    )

    # ── DuPont finding inputs (existing EBIT-based values only) ────────────────
    dupont = build_dupont(session, builder_entity, year, month, allowed_entities=eff)

    # ── Recent-months alerts ──────────────────────────────────────────────────
    alerts = build_recent_month_alerts(
        session, entity=builder_entity, year=year, month=month,
        allowed_entities=eff, n_months=3, max_items=5,
    )

    performance = _performance_block(
        session, year=year, month=month, builder_entity=builder_entity,
        eff=eff, revenue=revenue, ebit=ebit,
    )

    payload = {
        "meta": {
            "year": year, "month": month, "entity": entity,
            "source": ebit_source,
            "mart_fresh": ebit_source == "mart",
            # Per-piece provenance: prove which path served each block.  All four
            # pieces (ebit/cash/wc/top) are now WIRED to the mart when fresh+enabled
            # and proven within tolerance on finssentials_v2 (Phase 4); AR/AP aging
            # alone stays live by design.
            "cash_source": cash_source,
            "wc_source": wc_source,
            "top_source": top_source,
            "visibility": ("admin" if eff is None else "restricted"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "hero": {
            "revenue": revenue,
            "ebit": ebit,
            "cash": dict(cash),
            "working_capital": {"ccc": working_capital["ccc"],
                                "nwc": working_capital["nwc"]},
        },
        "working_capital": working_capital,
        "cash": cash,
        "top_customer": _top_entity_delta(top_cust),
        "top_supplier": _top_entity_delta(top_supp),
        "dupont": dupont,
        "performance": performance,
        "alerts": alerts,
    }

    if ttl > 0:
        _CACHE[cache_key] = (time.monotonic() + ttl, payload)
    return payload
