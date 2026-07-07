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
    entities_sql_fragment,
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
    position_plan_grain_sql,
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

    Post-0030 ``dim_pl_structure`` is PL-only (BS/CF rows were split into
    dim_bs_structure / dim_cf_structure), so this predicate is now a no-op guard —
    kept as belt-and-braces.  It keeps a row only when BOTH markers agree it is
    below the (former) BS block: ``sort_order < 1000`` AND ``line_code`` does not
    start with 'BS_', which is robust if a stray BS/CF row ever reappears here.
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


_PLAN_KEYS = ["plan_cm", "ytd_plan", "ytg"]


def _apply_plan_data(am: dict[str, float], pm_data: dict[str, float]) -> dict[str, float]:
    """Merge presented plan grains into an amounts dict (no account-level planning)."""
    out = dict(am)
    plan_cm = float(pm_data.get("plan_cm") or 0)
    out["plan_cm"] = round(plan_cm, 2)
    out["plan_vs_actual"] = round(float(am.get("cm") or 0) - plan_cm, 2)
    for k in ("ytd_plan", "ytg"):
        if k in pm_data:
            out[k] = round(float(pm_data.get(k) or 0), 2)
    return out


def attach_hierarchy_plan_to_rows(
    rows: list[dict[str, Any]],
    plan_map: dict[str, dict[str, float]],
    struct_rows: list[dict[str, Any]],
) -> None:
    """Attach plan_cm to hierarchy-based BS/WC rows (skips account leaves).

    Interior nodes inherit the sum of their children's plan so subtotals stay
    aligned with actual roll-ups.  Leaf mapping rows match ``dim_pl_structure``
    mapping lines via drill ``level_2/3/4``.
    """
    if not plan_map:
        return

    mapping_by_drill: dict[tuple[str, str, str], str] = {}
    for r in struct_rows:
        if r.get("row_type") != "mapping":
            continue
        key = (
            (r.get("level_2") or "").strip(),
            (r.get("level_3") or "").strip(),
            (r.get("level_4") or "").strip(),
        )
        mapping_by_drill[key] = str(r["line_code"])

    def _sum_child_plan(children: list[dict[str, Any]]) -> dict[str, float]:
        acc = {k: 0.0 for k in _PLAN_KEYS}
        for ch in children:
            if ch.get("row_kind") == "account":
                continue
            am = ch.get("amounts") or {}
            for k in _PLAN_KEYS:
                acc[k] += float(am.get(k) or 0.0)
        return acc

    def _walk(row_list: list[dict[str, Any]]) -> None:
        for row in row_list:
            children = row.get("children") or []
            if children:
                _walk(children)
            if row.get("row_kind") in ("account", "kpi", "kpi_header", "title"):
                continue
            am = row.get("amounts")
            if not isinstance(am, dict):
                continue

            pm_data: Optional[dict[str, float]] = None
            if children:
                summed = _sum_child_plan(children)
                if any(abs(float(summed.get(k) or 0)) > 1e-6 for k in _PLAN_KEYS):
                    pm_data = summed
            if pm_data is None:
                drill = row.get("drill") or {}
                dkey = (
                    (drill.get("level_2") or "").strip(),
                    (drill.get("level_3") or "").strip(),
                    (drill.get("level_4") or "").strip(),
                )
                code = mapping_by_drill.get(dkey)
                if code and code in plan_map:
                    pm_data = plan_map[code]
                else:
                    lc = str(row.get("line_code") or "")
                    if lc in plan_map:
                        pm_data = plan_map[lc]

            if pm_data is None:
                continue
            row["amounts"] = _round_am(_apply_plan_data(am, pm_data))

    _walk(rows)


def _statement_structure_rows(session: Session, statement: str) -> list[dict[str, Any]]:
    """Value-bearing structure rows for a statement, in sort order.

    Reuses each module's OWN structure loader + row predicate so the plan map is
    aligned with the SAME rows the actual statement builder uses:
      * PL → ``_is_pl_structure_row`` over ``dim_pl_structure``.
      * BS → ``fin_compat_bs._is_bs_structure_row``.
      * CF → ``fin_compat_cf._cf_struct_rows`` (already filtered).
    Imports for BS/CF are LOCAL to avoid a circular import (both import this module).
    """
    if statement == "PL":
        return [r for r in (_row_dict(r) for r in _load_structure(session))
                if _is_pl_structure_row(r)]
    if statement == "BS":
        from app.services import fin_compat_bs as _bs
        return [r for r in (_row_dict(r) for r in _bs._load_structure(session))
                if _bs._is_bs_structure_row(r)]
    if statement == "CF":
        from app.services import fin_compat_cf as _cf
        return list(_cf._cf_struct_rows(session))
    raise ValueError(f"unknown statement {statement!r}")


def load_position_plan_map(
    session: Session,
    statement: str,
    year: int, month: int,
    ent_frag: str,
    *,
    scenario: str = "budget",
    prefixes: Optional[list[str]] = None,
) -> dict[str, dict[str, float]]:
    """Statement-generic position-plan map: ``{line_code: {plan_cm, ytd_plan, ytg}}``.

    Reads ``fact_position_plan`` at POSITION grain via
    :func:`position_plan_grain_sql` (which applies the single ``amount * -1``
    presentation flip ONCE), maps the grains onto the statement's structure rows,
    runs the running-sum snapshot (:func:`_compute_running_values`), and returns the
    map ONLY when a plan signal is present — else ``{}`` (the golden-safe
    fall-through: an empty plan store leaves every downstream statement byte-identical
    because ``{}`` adds no keys via :func:`_attach_plan` and keeps ``cm_vs_plan`` at
    ``0.0``).

    Grain → structure matching is per statement:
      * PL / BS : grain ``line_code`` matches the structure ``line_code`` (identity).
      * CF      : grain ``line_code`` (= the ``cf_mapping`` leaf) matches a structure
                  mapping row's ``balance_title`` via :func:`_norm_cf_key` — the SAME
                  normalisation the actual CF body uses.

    SIGN.  ``position_plan_grain_sql`` returns PRESENTED values with the single
    ``* -1`` flip, which is correct as-is for PL and CF (both present ``amount * -1``).
    For BS the presented convention is asset(+)/credit(−), so we DO NOT flip again:
    we recover the stored amount (``stored = -sql_value``) and re-present it through
    the centralized :func:`budget_service.stored_to_present` helper — no new sign
    literal is introduced, only the sanctioned BS helper.

    ``prefixes`` (default ``None``) forwards a restricted multi-prefix visibility set
    to :func:`position_plan_grain_sql`, which BOUND-restricts the plan read to EXACTLY
    those entity prefixes (``= ANY(:eps)``) instead of relying on the ``ent_frag``
    string sniff — closing the cross-tenant plan leak for users granted ≥2 entities.
    ``None`` keeps the single-prefix / consolidated ``ent_frag`` behaviour unchanged.
    """
    struct = _statement_structure_rows(session, statement)

    sql, params = position_plan_grain_sql(
        year, month, ent_frag, statement, scenario=scenario, prefixes=prefixes
    )
    plan_grains = [dict(r._mapping) for r in session.execute(text(sql), params).fetchall()]
    if not plan_grains:
        return {}

    if statement == "CF":
        from app.services.fin_compat_cf import _norm_cf_key
        # Primary index: norm(grain.line_code) — matches a plan row whose line_code
        # was stored as the balance_title (e.g. "Δ Trade receivables").
        by_key = {_norm_cf_key(g.get("line_code")): g for g in plan_grains}
        # Fallback index: direct structure line_code match — the seeder
        # (seed_plan_forecast_budget.py) stores line_code = structure.line_code
        # (e.g. "CF_TRADE_RECEIVABLES") via _cf_leaf_map; the balance_title norm
        # does NOT match that key, so without this fallback CF forecast plan is
        # invisible to the reader.  BUG FLAG: both conventions are in-flight; the
        # direct-code path closes the seeder→reader key-space gap without breaking
        # any legacy balance_title-keyed rows.
        by_direct = {str(g.get("line_code", "")): g for g in plan_grains}
    else:
        by_code = {str(g.get("line_code")): g for g in plan_grains}

    mapping_plan: dict[str, dict[str, float]] = {}
    for r in struct:
        if r.get("row_type") != "mapping":
            continue
        code = r["line_code"]
        if statement == "CF":
            from app.services.fin_compat_cf import _norm_cf_key
            # Try balance_title norm first (legacy path), then direct line_code.
            g = by_key.get(_norm_cf_key(r.get("balance_title")))
            if g is None:
                g = by_direct.get(str(code))
        else:
            g = by_code.get(str(code))
        if g is None:
            mapping_plan[code] = {k: 0.0 for k in _PLAN_KEYS}
        elif statement == "BS":
            # position_plan_grain_sql already applied the single *-1 → recover the
            # stored amount and re-present via the BS asset/credit helper (no re-flip).
            from app.services.budget_service import stored_to_present as _stp
            mapping_plan[code] = {
                k: _stp(-float(g.get(k) or 0.0), "BS", str(code)) for k in _PLAN_KEYS
            }
        else:  # PL, CF — SQL output is already the correct presented value.
            mapping_plan[code] = {k: float(g.get(k) or 0.0) for k in _PLAN_KEYS}

    running = _compute_running_values(struct, mapping_plan, _PLAN_KEYS)
    line_plan = {
        code: {k: float(v.get(k) or 0.0) for k in _PLAN_KEYS}
        for code, v in running.items()
    }

    # BUG FIX: original gate checked only plan_cm (current-month plan).
    # Forecast rows are seeded for OPEN periods only (p > L), so plan_cm == 0
    # for the closed current month — the gate was always False for a pure-forecast
    # band.  We extend the signal check to include ytg (open-period plan) so that a
    # scenario seeded for future months is recognised as having plan data.
    # Golden-safety is preserved: with NO rows at all, both plan_cm and ytg are 0.0
    # (computed via _compute_running_values over an empty mapping_plan), so {} is
    # still returned for a genuinely empty plan store.
    has_signal = any(
        abs(float(v.get("plan_cm") or 0)) > 1e-6
        or abs(float(v.get("ytg") or 0)) > 1e-6
        for v in line_plan.values()
    )
    return line_plan if has_signal else {}


def load_position_plan_map_pref(
    session: Session,
    statement: str,
    year: int, month: int,
    ent_frag: str,
    *,
    prefixes: Optional[list[str]] = None,
) -> dict[str, dict[str, float]]:
    """Forecast-preferred position-plan reader for the two-view Forecast/Coverage cols.

    Resolves the ``scenario='forecast'`` band in ``fact_position_plan`` first and only
    falls back to ``scenario='budget'`` when forecast has NO signal.  Delegates entirely
    to the primitive :func:`load_position_plan_map` — same sign conventions, same tenant
    scoping (``ent_frag`` / ``prefixes`` forwarded verbatim), same ``has_signal`` gate.

    GOLDEN-SAFETY: on a DB WITHOUT forecast rows the forecast read returns ``{}`` (the
    ``has_signal`` gate), so this wrapper is byte-identical to today's budget read.
    """
    fc = load_position_plan_map(
        session, statement, year, month, ent_frag,
        scenario="forecast", prefixes=prefixes,
    )
    if fc:
        return fc
    return load_position_plan_map(
        session, statement, year, month, ent_frag,
        scenario="budget", prefixes=prefixes,
    )


def _load_plan_map(
    session: Session,
    year: int, month: int,
    ent_frag: str,
    *,
    prefixes: Optional[list[str]] = None,
) -> dict[str, dict[str, float]]:
    """Load plan data; resolution order budget → forecast → plan (first with signal).

    * ``budget``   reads ``fact_position_plan`` (Phase 3 manual budget) at POSITION
                   grain — partner rows already rolled into their line_code — keyed
                   by ``line_code`` directly (no level-based ``_match_grain``).
    * ``forecast`` / ``plan`` read ``fact_gl_plan`` via :func:`plan_grain_sql` and
                   map grain → structure by level_2/3/4 (``_match_grain``).

    GOLDEN-SAFETY: every scenario is gated by ``has_signal``; an EMPTY budget read
    produces no signal and falls through to forecast/plan → byte-identical to the
    pre-budget behaviour whenever no budget rows exist.  The budget position-grain
    SQL applies the SAME ``amount * -1`` presentation flip as ``plan_grain_sql`` so
    a budget value never desyncs the sign vs the fact_gl_plan path.

    Subtotal/calc/grandtotal plan values use the SAME running-sum semantics as
    actuals — the cumulative sum of the mapping-line plan values above them — so
    there is no hard-coded formula over legacy codes.
    """
    struct_pl = [r for r in (_row_dict(r) for r in _load_structure(session))
                 if _is_pl_structure_row(r)]
    plan_keys = ["plan_cm", "ytd_plan", "ytg"]

    for scenario in ("budget", "forecast", "plan"):
        if scenario == "budget":
            # Position-grain budget: delegate to the statement-generic loader, which
            # reproduces this branch's output exactly for "PL" (same SQL, same
            # line_code identity match, same running-sum + has_signal gate).
            budget_map = load_position_plan_map(
                session, "PL", year, month, ent_frag, prefixes=prefixes
            )
            if budget_map:
                return budget_map
            continue
        else:
            sql, params = plan_grain_sql(year, month, ent_frag, scenario)
            plan_grains = [
                dict(r._mapping) for r in session.execute(text(sql), params).fetchall()
            ]
            if not plan_grains:
                continue

            mapping_plan = {}
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
    ent_frag_override: Optional[str] = None,
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

    ``ent_frag_override`` (additive, golden-safe): when given, this pre-built
    entity SQL fragment is used VERBATIM instead of resolving ``entity`` — lets
    callers scope to a multi-prefix union (e.g. a restricted user's own
    entities).  ``None`` preserves the existing single-entity behaviour exactly.
    """
    if ent_frag_override is not None:
        ent_frag = ent_frag_override
    else:
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

def _statement_actual_running(
    session: Session,
    statement: str,
    year: int, month: int,
    ent_frag: str,
    struct: list[dict[str, Any]],
) -> dict[str, float]:
    """{line_code: presented actual CM} for a statement, with running-sum subtotals.

    Presentation is aligned with :func:`load_position_plan_map` so plan and actual
    share ONE convention per statement (no re-flip):
      * PL : the ``cm`` grain column is already presented (``amount * -1`` in SQL).
      * CF : the ``cm`` grain column is already presented (``dim_gl_cf.amount * -1``);
             grains match a structure row by the ``cf_mapping`` leaf.
      * BS : the ``cm`` grain column is the RAW stored balance (Σ GL amounts); we
             present it through the SAME centralized ``stored_to_present`` BS helper
             the plan side uses, so asset(+)/credit(−) reconcile line-for-line.
    """
    mapping_actual: dict[str, dict[str, float]] = {}
    if statement == "CF":
        from app.services.fin_compat_cf import _matched_cf_grains
        from app.services.fin_compat_cf_sql import cf_grain_sql_month
        sql, params = cf_grain_sql_month(year, month, ent_frag)
        grains = [dict(r._mapping) for r in session.execute(text(sql), params).fetchall()]
        for r in struct:
            if r.get("row_type") != "mapping":
                continue
            cm = sum(float(g.get("cm") or 0.0)
                     for g in _matched_cf_grains(grains, r.get("balance_title")))
            mapping_actual[r["line_code"]] = {"cm": cm}
    elif statement == "BS":
        from app.services.budget_service import stored_to_present as _stp
        from app.services.fin_compat_bs_sql import bs_grain_sql_month
        sql, params = bs_grain_sql_month(year, month, ent_frag)
        grains = [dict(r._mapping) for r in session.execute(text(sql), params).fetchall()]
        for r in struct:
            if r.get("row_type") != "mapping":
                continue
            code = r["line_code"]
            raw = sum(float(g.get("cm") or 0.0) for g in grains if _match_grain(g, r))
            mapping_actual[code] = {"cm": _stp(raw, "BS", str(code))}
    else:  # PL
        sql, params = pl_grain_sql_month(year, month, ent_frag)
        grains = [dict(r._mapping) for r in session.execute(text(sql), params).fetchall()]
        for r in struct:
            if r.get("row_type") != "mapping":
                continue
            cm = sum(float(g.get("cm") or 0.0) for g in grains if _match_grain(g, r))
            mapping_actual[r["line_code"]] = {"cm": cm}

    running_actual = _compute_running_values(struct, mapping_actual, ["cm"])
    return {code: float(v.get("cm") or 0.0) for code, v in running_actual.items()}


def build_statement_plan_response(
    session: Session,
    statement: str,
    year: int, month: int, entity: Optional[str],
    *,
    allowed_prefixes: Optional[set[str]] = None,
) -> dict[str, Any]:
    """Per-line plan overlay (PlPlanResponse shape) for PL | BS | CF | WC.

    Shape: ``{year, month, entity, has_plan_data, lines:[{line_code, plan_cm,
    plan_vs_actual, ytd_plan, ytg, coverage_pct}, ...]}`` — identical to the legacy
    ``build_pl_plan_response``.  For ``statement="PL"`` with ``allowed_prefixes=None``
    the output is BYTE-IDENTICAL to that legacy function (same scope, same
    budget→forecast→plan resolution, same running-sum actuals).

    ``plan_vs_actual = actual − plan`` in PRESENTED terms.  ``position_plan_grain_sql``
    already applies the single ``* -1`` flip, so NOTHING here flips again.

    WC derives from the BS position plan (``effstmt = "BS"``) — there is no separate
    WC plan store; the caller/frontend projects the returned BS-keyed lines onto the
    WC TWC/OWC subset (the same subset the actual WC body uses).

    ``allowed_prefixes`` (fail-closed visibility scope, mirrors
    ``build_pl_annual_compat``'s ``ent_frag_override``).  The requested ``entity`` is
    intersected with the visible set HERE via
    :func:`overview_summary._effective_prefixes` (this function does the narrowing —
    the caller does NOT pre-intersect):
      * ``None``      → unrestricted (admin); resolve the single ``entity`` as today.
      * empty ``set`` → DENY-ALL: ``has_plan_data=False, lines=[]`` (never leaks).
      * non-empty set, no ``entity``     → scope to the FULL allowed set.
      * non-empty set, ``entity`` inside → narrow to that single prefix.
      * non-empty set, ``entity`` OUTSIDE the set → intersection empty → DENY-ALL
        (never widen, never fall back to consolidated).
    The resulting scope is applied to BOTH the plan-map and the actuals.  A single
    resolved prefix uses the existing ``= 'XX'`` ``ent_frag`` path; a set of ≥2 also
    threads the FULL prefix set into the plan read as ``prefixes`` so
    :func:`position_plan_grain_sql` BOUND-restricts to ``= ANY(:eps)`` — no consolidated
    fallback, no cross-tenant leak (a user granted ``{AA,BB}`` sees ONLY AA+BB).
    """
    restrict_prefixes: Optional[list[str]] = None
    if allowed_prefixes is not None:
        if not allowed_prefixes:  # fail closed — deny-all
            return {"year": year, "month": month, "entity": entity,
                    "has_plan_data": False, "lines": []}
        # Intersect the requested entity with the visible set (reuse the canonical
        # narrowing helper — do NOT re-derive it).  ``denied`` covers both an empty
        # visibility and an ``entity`` narrowed outside the boundary → fail closed.
        from app.services.overview_summary import _effective_prefixes
        eff, _builder_entity, denied = _effective_prefixes(
            session, entity=entity, allowed_prefixes=allowed_prefixes
        )
        if denied or not eff:
            return {"year": year, "month": month, "entity": entity,
                    "has_plan_data": False, "lines": []}
        if len(eff) == 1:
            # Single visible/narrowed prefix → existing ``= 'XX'`` path (byte-identical
            # to the single-prefix behaviour; plan read keeps prefixes=None).
            ent_frag = entity_sql_fragment(next(iter(eff)))
        else:
            # ≥2 visible prefixes → restrict actuals via IN(...) AND bind the full set
            # into the plan read so it can never fall back to the consolidated total.
            restrict_prefixes = sorted(eff)
            ent_frag = entities_sql_fragment(restrict_prefixes)
    else:
        ep = resolve_entity_prefix(session, entity)
        ent_frag = entity_sql_fragment(ep)

    effstmt = "BS" if statement == "WC" else statement
    struct = _statement_structure_rows(session, effstmt)
    # Emit value-bearing rows (mapping + subtotal/calc/grandtotal); skip
    # presentation-only legacy 'title'/'kpi' rows.
    struct_codes = [
        r["line_code"] for r in struct
        if r.get("row_type") in ("mapping", "subtotal", "calc", "grandtotal", "computed")
    ]

    # PL keeps the full budget→forecast→plan resolution (fact_gl_plan fallback);
    # BS/CF/WC read the manual budget position store only.
    # Pass ``prefixes`` ONLY for the restricted ≥2-prefix scope; admin / single-prefix
    # call byte-identically to before (no kwarg → default None → unchanged behaviour).
    if effstmt == "PL":
        # Forecast-preferred PRE-CHECK: try the position-grain forecast band (falls back
        # to budget internally).  Only when it has signal do we use it; otherwise the
        # existing budget→gl_plan(forecast)→plan fallback stays UNCHANGED.  On a DB with
        # no forecast rows this pref == today's budget read, so PL is byte-identical.
        pref = load_position_plan_map_pref(
            session, "PL", year, month, ent_frag, prefixes=restrict_prefixes
        )
        if pref:
            plan_map = pref
        elif restrict_prefixes is not None:
            plan_map = _load_plan_map(session, year, month, ent_frag, prefixes=restrict_prefixes)
        else:
            plan_map = _load_plan_map(session, year, month, ent_frag)
    else:
        if restrict_prefixes is not None:
            plan_map = load_position_plan_map_pref(
                session, effstmt, year, month, ent_frag, prefixes=restrict_prefixes
            )
        else:
            plan_map = load_position_plan_map_pref(session, effstmt, year, month, ent_frag)

    actual_vals = _statement_actual_running(session, effstmt, year, month, ent_frag, struct)

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


def build_pl_plan_response(
    session: Session,
    year: int, month: int, entity: Optional[str],
) -> dict[str, Any]:
    """Thin PL wrapper — preserved verbatim for existing callers/route.

    Byte-identical to the pre-generalization behaviour: PL scope, unrestricted.
    """
    return build_statement_plan_response(session, "PL", year, month, entity)


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

    def _annual_child_row(row_id: str, label: str, am: dict[str, float]) -> dict:
        """Build a DISPLAY-ONLY L4 detail child in the annual flat-row shape."""
        agg = sum(am.get(ec, 0.0) for ec in entity_codes)
        return {
            "id": row_id, "label": label, "row_kind": "detail", "is_bold": False,
            "entity_amounts": {ec: round(am.get(ec, 0.0), 2) for ec in entity_codes},
            "aggregated": round(agg, 2),
            "ic_eliminations": 0.0,
            "consolidation": round(agg, 2),
            "has_children": False, "children": [],
        }

    def _annual_l4_children(r_line: dict, base_rc: str) -> list[dict]:
        """L4 detail children for an L3-only mapping row (mirrors the monthly builder).

        Groups the SAME grains that feed the parent's ``entity_amounts`` by their
        non-empty ``level_4``.  Because an L3-only structure row also matches grains
        that LACK a level_4 (see ``_match_grain``: with l4 empty it returns True on
        an l2/l3 match regardless of the grain's l4), those grains would otherwise
        be dropped and the children would not reconcile to the parent.  We capture
        them in a residual ``(no L4)`` bucket so, per entity column,
        Σ children == parent — but only when a real L4 breakdown exists (otherwise
        there is nothing to break down and we stay flat, like the monthly builder).
        """
        matched = [g for g in grains if _match_grain(g, r_line)]
        l4_dict: dict[str, list[dict]] = {}
        no_l4: list[dict] = []
        for g in matched:
            l4v = (g.get("level_4") or "").strip()
            if l4v:
                l4_dict.setdefault(l4v, []).append(g)
            else:
                no_l4.append(g)
        kids: list[dict] = []
        for l4v, l4g in sorted(l4_dict.items()):
            kids.append(_annual_child_row(
                f"pl-{base_rc}-l4-{abs(hash(l4v)) % 100000}", l4v, _consl_ytd(l4g),
            ))
        # Collapse a single self-referential child (matches monthly behaviour).
        if len(kids) == 1 and kids[0]["label"] == r_line["balance_title"]:
            kids = []
        # Residual bucket: only when a real L4 breakdown exists, so children
        # reconcile to the parent per entity without emitting a lone "(no L4)".
        if kids and no_l4:
            res_am = _consl_ytd(no_l4)
            if any(abs(v) > 1e-9 for v in res_am.values()):
                kids.append(_annual_child_row(f"pl-{base_rc}-l4-none", "(no L4)", res_am))
        return kids

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
        # L4 detail children for L3-only mapping rows — same gate as the monthly
        # builder (see ``build_pl_monthly``): an L3 mapping row that carries no
        # level_4 / gl_account_id of its own.  The children reconcile to the
        # parent per entity column (residual "(no L4)" bucket, see helper docstring).
        l3 = (r.get("level_3") or "").strip()
        l4 = (r.get("level_4") or "").strip()
        gid = (r.get("gl_account_id") or "").strip() or None
        kids = (
            _annual_l4_children(r, rc)
            if (rt == "mapping" and l3 and not l4 and not gid)
            else []
        )
        rows_out.append({
            "id": f"pl-{rc}",
            "label": r["balance_title"],
            "row_kind": "line" if rt == "mapping" else "subtotal",
            "is_bold": bool(r.get("is_bold", False)),
            "entity_amounts": {ec: round(am.get(ec, 0.0), 2) for ec in entity_codes},
            "aggregated": round(agg, 2),
            "ic_eliminations": 0.0,
            "consolidation": round(agg, 2),
            "has_children": len(kids) > 0,
            "children": kids,
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
    ent_frag_override: Optional[str] = None,
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

    ``ent_frag_override`` (additive, golden-safe): when given, this pre-built
    entity SQL fragment is used VERBATIM instead of resolving ``entity``.  ``None``
    preserves the existing single-entity behaviour exactly.
    """
    if ent_frag_override is not None:
        ent_frag = ent_frag_override
    else:
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
