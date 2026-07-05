"""Customer (Area 4) & Supplier (Area 5) development — Overview Page v2.

Pure, DB-facing service functions for the reporting-v2 Overview redesign (P4).
Both entrypoints are additive read-only aggregations over the derived sales/cost
facts and never mutate any existing financial result.

============================================================ SIGN & SCALE
``fact_sales.gross_sales`` is stored as ``-amount`` (revenue credit → positive)
and ``fact_com.cost_of_materials`` as ``+amount`` (material debit → positive), so
both metrics are naturally **positive magnitudes** — NO sign flip is applied here
(same convention as ``overview_top_entities``).  All monetary values are reported
in **kEUR = Σ/1000**.

============================================================ SIGN CONVENTION (FAV)
- Customer revenue / "won" / YoY increase → **FAV+** (larger is better).
- Lost customers → **FAV−** (a lost customer is unfavourable).
- Supplier cost / cost-increase → **FAV−**: the cost-increase rows carry an
  ``invert_delta=True`` / ``fav="minus"`` flag so the findings/UI layer can display
  a rising cost as unfavourable.  The stored ``delta_yoy_keur`` keeps its **raw
  signed** value (positive = cost rose) — the sign is *never* mutated here.

============================================================ PERIODS
The derived facts carry ``posting_date`` but NOT ``fiscal_period``, so every window
is a calendar ``posting_date`` range built only from the ``fin_compat_sql`` helpers
(``pm`` / ``last_day``) — no inline fiscal-year math:

  cm     = [first(year, month)   .. last(year, month)]          (MTD)
  pm     = [first(prior month)   .. last(prior month)]
  py_cm  = [first(year-1, month) .. last(year-1, month)]
  ytd    = [first(year, 1)       .. last(year, month)]
  ytd_py = [first(year-1, 1)     .. last(year-1, month)]

Lost-customer APPROVED windows (relative to the anchor month, offset 0 = anchor):
  current window = months [-12 .. -1]  (the 12 months ending the month before anchor)
  prior   window = months [-24 .. -13] (the preceding, non-overlapping 12 months)

============================================================ FAIL-CLOSED SECURITY
``allowed_entities`` (tenant isolation), a set of 2-char ``entity_prefix`` values:
  * ``None``       → admin / unrestricted; the ``entity`` arg is resolved instead
                     (byte-identical to legacy single-entity behaviour).
  * empty ``set()``→ **fail closed**: return the empty response shape immediately,
                     issuing NO SQL (nothing can leak).
  * non-empty set  → the ONLY entity filter (``entity`` ignored — the caller has
                     already intersected it); every SQL query is scoped by
                     ``LEFT(f.account_number_group, 2) IN (...)``.
The endpoint (backend-engineer) resolves ``visible_entity_codes()`` → prefixes and
passes them in; visibility is NOT resolved here.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.fin_compat_sql import (
    col_labels_month,
    last_day,
    pm as _pm,
    resolve_entity_prefix,
)
from app.services.overview_top_entities import rank_top_entities

# Lost-customer thresholds (APPROVED, docs/financial-logic.md Area 4 #3).
_T_MATERIAL_KEUR = 5.0    # prior-window revenue must be at least this to qualify
_EPS_FLOOR_KEUR = 1.0     # absolute floor of the current-window collapse epsilon
_EPS_FRACTION = 0.10      # fraction of prior-window revenue for the epsilon
_ZERO = 1e-6


# ---------------------------------------------------------------------------
# Pure helpers (DB-free, unit-testable)
# ---------------------------------------------------------------------------

def _month_bounds(year: int, month: int) -> tuple[str, str]:
    return date(year, month, 1).isoformat(), last_day(year, month).isoformat()


def _ytd_bounds(year: int, month: int) -> tuple[str, str]:
    return date(year, 1, 1).isoformat(), last_day(year, month).isoformat()


def _month_back(year: int, month: int, n: int) -> tuple[int, int]:
    """(year, month) ``n`` whole months before the anchor — via ``pm`` only."""
    y, m = year, month
    for _ in range(n):
        y, m = _pm(y, m)
    return y, m


def lost_customer_windows(year: int, month: int) -> tuple[tuple[str, str], tuple[str, str]]:
    """Return ((prior_from, prior_to), (current_from, current_to)) ISO date bounds.

    current = months [-12 .. -1]  (12 months ending the month BEFORE the anchor);
    prior   = months [-24 .. -13] (the preceding, non-overlapping 12 months).

    Worked (anchor 2026-06): current 2025-06-01..2026-05-31, prior 2024-06-01..2025-05-31.
    """
    cur_from = date(*_month_back(year, month, 12), 1).isoformat()
    cy, cm = _month_back(year, month, 1)
    cur_to = last_day(cy, cm).isoformat()
    pri_from = date(*_month_back(year, month, 24), 1).isoformat()
    py, pmn = _month_back(year, month, 13)
    pri_to = last_day(py, pmn).isoformat()
    return (pri_from, pri_to), (cur_from, cur_to)


def classify_lost(
    rev_prior: float,
    rev_current: float,
    *,
    t_material: float = _T_MATERIAL_KEUR,
    eps_floor: float = _EPS_FLOOR_KEUR,
    eps_fraction: float = _EPS_FRACTION,
) -> str:
    """Classify a partner's prior-vs-current window revenue (kEUR).

    Returns one of:
      * ``'immaterial'`` — ``rev_prior < t_material`` (too small to call "lost").
      * ``'lost'``       — material prior AND current collapsed:
                           ``rev_current <= max(eps_floor, eps_fraction·rev_prior)``.
      * ``'declining'``  — material prior but current still above the epsilon.

    Worked (t=5, floor=1, frac=0.10):
      prior 40, current 0 → ε=max(1, 4)=4, 0≤4 → 'lost'.
      prior 3             → 'immaterial' (3 < 5).
      prior 40, current 6 → ε=4, 6>4       → 'declining' (not lost).
    """
    p = float(rev_prior or 0.0)
    c = float(rev_current or 0.0)
    if p < t_material:
        return "immaterial"
    eps = max(eps_floor, eps_fraction * p)
    return "lost" if c <= eps else "declining"


def avg_per_txn(rev: float, count: int) -> Optional[float]:
    """``rev / count`` in kEUR, or ``None`` when ``count == 0`` (zero-guard).

    Worked: 500 kEUR / 4 → 125.0 ; 620 / 4 → 155.0 ; anything / 0 → None.
    """
    n = int(count or 0)
    if n == 0:
        return None
    return round(float(rev or 0.0) / n, 2)


def _split_increase_won(
    agg_rows: list[dict[str, Any]],
    *,
    id_field: str,
    top_n: int,
    val_key: str = "rev",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition partners into (increase, won) on the YTD YoY basis.

    ΔYoY = ytd − ytd_py (kEUR).  A partner with ``|ytd_py| < 1e-6`` and positive
    current YTD is **new** → the ``won`` bucket (Δ = +ytd).  The rest are ranked by
    ΔYoY desc and capped at ``top_n``; ``won`` is sorted by current YTD desc.

    ``val_key`` names the value field: ``'rev'`` (customer) → ``rev_cur_keur`` /
    ``rev_py_keur``; ``'cost'`` (supplier) → ``cost_cur_keur`` / ``cost_py_keur``.
    """
    cur_key = f"{val_key}_cur_keur"
    py_key = f"{val_key}_py_keur"
    increase: list[dict[str, Any]] = []
    won: list[dict[str, Any]] = []
    for r in agg_rows:
        ytd = float(r.get("ytd") or 0.0)
        ytd_py = float(r.get("ytd_py") or 0.0)
        if abs(ytd) < _ZERO and abs(ytd_py) < _ZERO:
            continue  # all-zero → not a development signal
        row = {
            id_field: r.get(id_field),
            "name": r.get("name"),
            cur_key: round(ytd, 2),
            py_key: round(ytd_py, 2),
            "delta_yoy_keur": round(ytd - ytd_py, 2),
        }
        if abs(ytd_py) < _ZERO and ytd > _ZERO:
            won.append(row)
        elif abs(ytd_py) >= _ZERO:
            increase.append(row)
    increase.sort(key=lambda r: r["delta_yoy_keur"], reverse=True)
    won.sort(key=lambda r: r[cur_key], reverse=True)
    return increase[:top_n], won[:top_n]


# ---------------------------------------------------------------------------
# SQL fragment helpers
# ---------------------------------------------------------------------------

def _resolve_entity_frag(
    session: Session,
    entity: Optional[str],
    allowed_entities: Optional[set[str]],
) -> Optional[str]:
    """Return the entity SQL fragment, or ``None`` to signal fail-closed (deny-all).

    * ``allowed_entities`` non-empty → ``AND LEFT(f.account_number_group,2) IN (...)``.
    * ``allowed_entities`` empty     → ``None`` (caller returns the empty shape).
    * ``allowed_entities is None``   → resolve ``entity`` (legacy single-entity path).
    """
    if allowed_entities is not None:
        safe = sorted({str(p).replace("'", "")[:2] for p in allowed_entities if p})
        if not safe:
            return None  # fail closed
        inner = ", ".join(f"'{p}'" for p in safe)
        return f"AND LEFT(f.account_number_group, 2) IN ({inner})"
    ep = resolve_entity_prefix(session, entity)
    if ep is not None:
        safe = str(ep).replace("'", "")[:2]
        return f"AND LEFT(f.account_number_group, 2) = '{safe}'"
    return ""


_NAME_EXPR = "TRIM(COALESCE(d.name_line_1, '') || ' ' || COALESCE(d.name_line_2, ''))"


def _win(value_col: str, d0: str, d1: str) -> str:
    return (
        f"SUM(CASE WHEN f.posting_date BETWEEN '{d0}' AND '{d1}'"
        f" THEN f.{value_col} ELSE 0 END) / 1000.0"
    )


def _empty_development(
    year: int,
    month: int,
    id_field: str,
    *,
    with_lost: bool,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "period": _period_meta(year, month),
        "id_field": id_field,
        "biggest": [],
        "increase": [],
        "won": [],
    }
    if with_lost:
        out["lost"] = []
    return out


def _period_meta(year: int, month: int) -> dict[str, Any]:
    labels = col_labels_month(year, month)
    (pri_f, pri_t), (cur_f, cur_t) = lost_customer_windows(year, month)
    return {
        "year": year,
        "month": month,
        "cm_label": labels["cm"],
        "ytd_label": labels["ytd"],
        "prior_window": [pri_f, pri_t],
        "current_window": [cur_f, cur_t],
    }


# ---------------------------------------------------------------------------
# Shared aggregation (customer / supplier symmetric)
# ---------------------------------------------------------------------------

def _development(
    session: Session,
    *,
    entity: Optional[str],
    year: int,
    month: int,
    allowed_entities: Optional[set[str]],
    top_n: int,
    is_customer: bool,
) -> dict[str, Any]:
    if is_customer:
        fact, dim = "fact_sales", "dim_customer"
        value_col, id_col = "gross_sales", "customer_id"
        level3, count_alias = "Net sales", "invoice_count"
    else:
        fact, dim = "fact_com", "dim_supplier"
        value_col, id_col = "cost_of_materials", "supplier_id"
        level3, count_alias = "Cost of materials", "purchase_txns"

    ent_frag = _resolve_entity_frag(session, entity, allowed_entities)
    if ent_frag is None:  # fail-closed: empty visibility set
        return _empty_development(year, month, id_col, with_lost=is_customer)

    cm_f, cm_t = _month_bounds(year, month)
    pm_y, pm_m = _pm(year, month)
    pm_f, pm_t = _month_bounds(pm_y, pm_m)
    py_f, py_t = _month_bounds(year - 1, month)
    ytd_f, ytd_t = _ytd_bounds(year, month)
    ytd_py_f, ytd_py_t = _ytd_bounds(year - 1, month)

    # Main per-partner aggregation: the 5 period sums + a jegn count over YTD.
    count_expr = (
        f"COUNT(DISTINCT CASE WHEN f.posting_date BETWEEN '{ytd_f}' AND '{ytd_t}'"
        f" THEN f.journal_entry_group_number END)"
    )
    main_sql = f"""
        SELECT
            f.{id_col} AS partner_id,
            MAX(NULLIF({_NAME_EXPR}, '')) AS name,
            {_win(value_col, cm_f, cm_t)}         AS cm,
            {_win(value_col, pm_f, pm_t)}         AS pm,
            {_win(value_col, py_f, py_t)}         AS py_cm,
            {_win(value_col, ytd_f, ytd_t)}       AS ytd,
            {_win(value_col, ytd_py_f, ytd_py_t)} AS ytd_py,
            {count_expr}                          AS {count_alias}
        FROM {fact} f
        JOIN dim_gl_account a
          ON a.account_number_group = f.account_number_group
         AND a.fiscal_year          = f.fiscal_year
        LEFT JOIN {dim} d ON d.{id_col} = f.{id_col}
        WHERE f.posting_date BETWEEN '{ytd_py_f}' AND '{ytd_t}'
          AND TRIM(a.level_3) = '{level3}'
          {ent_frag}
        GROUP BY f.{id_col}
    """
    raw = [dict(r._mapping) for r in session.execute(text(main_sql)).fetchall()]

    agg_rows: list[dict[str, Any]] = []
    count_by_id: dict[Any, int] = {}
    for r in raw:
        pid = r.get("partner_id")
        name = r.get("name") or (str(pid) if pid is not None else "(no partner)")
        agg_rows.append({
            "name": name,
            id_col: pid,
            "cm": float(r.get("cm") or 0.0),
            "pm": float(r.get("pm") or 0.0),
            "py_cm": float(r.get("py_cm") or 0.0),
            "ytd": float(r.get("ytd") or 0.0),
            "ytd_py": float(r.get("ytd_py") or 0.0),
        })
        count_by_id[pid] = int(r.get(count_alias) or 0)

    # Biggest by YTD revenue/cost desc (reuse rank_top_entities: drop-zero + deltas).
    ranked = rank_top_entities(agg_rows, id_field=id_col, rank_by="ytd", limit=top_n)
    fav = "plus" if is_customer else "minus"
    val_key = "rev" if is_customer else "cost"
    biggest: list[dict[str, Any]] = []
    for r in ranked:
        pid = r.get(id_col)
        cnt = count_by_id.get(pid, 0)
        row = {
            "rank": r["rank"],
            id_col: pid,
            "name": r["name"],
            f"{val_key}_ytd_keur": r["ytd"],
            f"{val_key}_cm_keur": r["cm"],
            count_alias: cnt,
            ("avg_per_invoice_keur" if is_customer else "avg_per_purchase_keur"):
                avg_per_txn(r["ytd"], cnt),
            "fav": fav,
        }
        biggest.append(row)

    increase, won = _split_increase_won(
        agg_rows, id_field=id_col, top_n=top_n, val_key=val_key)
    if not is_customer:
        # Cost increase is FAV− → carry the invert flag; NEVER mutate the sign.
        for row in increase:
            row["fav"] = "minus"
            row["invert_delta"] = True
        for row in won:
            row["kind"] = "new_spend"

    out: dict[str, Any] = {
        "period": _period_meta(year, month),
        "id_field": id_col,
        "biggest": biggest,
        "increase": increase,
        "won": won,
    }

    if is_customer:
        out["lost"] = _lost_customers(
            session, id_col=id_col, fact=fact, dim=dim, value_col=value_col,
            level3=level3, ent_frag=ent_frag, year=year, month=month, top_n=top_n,
        )
    return out


def _lost_customers(
    session: Session,
    *,
    id_col: str,
    fact: str,
    dim: str,
    value_col: str,
    level3: str,
    ent_frag: str,
    year: int,
    month: int,
    top_n: int,
) -> list[dict[str, Any]]:
    (pri_f, pri_t), (cur_f, cur_t) = lost_customer_windows(year, month)
    lost_sql = f"""
        SELECT
            f.{id_col} AS partner_id,
            MAX(NULLIF({_NAME_EXPR}, '')) AS name,
            {_win(value_col, pri_f, pri_t)} AS rev_prior,
            {_win(value_col, cur_f, cur_t)} AS rev_current
        FROM {fact} f
        JOIN dim_gl_account a
          ON a.account_number_group = f.account_number_group
         AND a.fiscal_year          = f.fiscal_year
        LEFT JOIN {dim} d ON d.{id_col} = f.{id_col}
        WHERE f.posting_date BETWEEN '{pri_f}' AND '{cur_t}'
          AND TRIM(a.level_3) = '{level3}'
          AND f.{id_col} IS NOT NULL
          {ent_frag}
        GROUP BY f.{id_col}
    """
    rows = [dict(r._mapping) for r in session.execute(text(lost_sql)).fetchall()]
    lost: list[dict[str, Any]] = []
    for r in rows:
        rev_prior = float(r.get("rev_prior") or 0.0)
        rev_current = float(r.get("rev_current") or 0.0)
        if classify_lost(rev_prior, rev_current) != "lost":
            continue
        pid = r.get("partner_id")
        lost.append({
            id_col: pid,
            "name": r.get("name") or (str(pid) if pid is not None else "(no partner)"),
            "rev_prior_keur": round(rev_prior, 2),
            "rev_current_keur": round(rev_current, 2),
            "fav": "minus",
        })
    lost.sort(key=lambda r: r["rev_prior_keur"], reverse=True)
    return lost[:top_n]


# ---------------------------------------------------------------------------
# Public entrypoints
# ---------------------------------------------------------------------------

def build_customer_development(
    session: Session,
    *,
    entity: Optional[str] = None,
    year: int,
    month: int,
    allowed_entities: Optional[set[str]] = None,
    top_n: int = 10,
) -> dict[str, Any]:
    """Customer development (Overview v2 Area 4) — biggest / increase / won / lost.

    See module docstring for sign, period and fail-closed contracts.  Returns::

      {
        "period": {year, month, cm_label, ytd_label,
                   prior_window: [iso, iso], current_window: [iso, iso]},
        "id_field": "customer_id",
        "biggest":  [ {rank, customer_id, name, rev_ytd_keur, rev_cm_keur,
                       invoice_count, avg_per_invoice_keur|None, fav:"plus"} ],  # YTD desc, top_n
        "increase": [ {customer_id, name, rev_cur_keur, rev_py_keur,
                       delta_yoy_keur, fav-less} ],   # ΔYoY(YTD) desc, top_n, excl. won
        "won":      [ {customer_id, name, rev_cur_keur, rev_py_keur, delta_yoy_keur} ],  # py≈0
        "lost":     [ {customer_id, name, rev_prior_keur, rev_current_keur, fav:"minus"} ],
      }

    ``allowed_entities=set()`` fails closed → every list empty, no SQL issued.
    """
    return _development(
        session, entity=entity, year=year, month=month,
        allowed_entities=allowed_entities, top_n=top_n, is_customer=True,
    )


def build_supplier_development(
    session: Session,
    *,
    entity: Optional[str] = None,
    year: int,
    month: int,
    allowed_entities: Optional[set[str]] = None,
    top_n: int = 10,
) -> dict[str, Any]:
    """Supplier development (Overview v2 Area 5) — biggest / cost-increase / new spend.

    Symmetric to :func:`build_customer_development` on ``fact_com`` (cost side, no
    "lost" bucket).  Cost is ``FAV−``: the cost-increase rows carry
    ``invert_delta=True`` / ``fav="minus"`` — the raw signed ``delta_yoy_keur`` is
    NEVER mutated.  Returns::

      {
        "period": {...},
        "id_field": "supplier_id",
        "biggest":  [ {rank, supplier_id, name, cost_ytd_keur, cost_cm_keur,
                       purchase_txns, avg_per_purchase_keur|None, fav:"minus"} ],  # YTD desc
        "increase": [ {supplier_id, name, cost_cur_keur, cost_py_keur,
                       delta_yoy_keur, fav:"minus", invert_delta:true} ],  # ΔYoY desc
        "won":      [ {supplier_id, name, cost_cur_keur, cost_py_keur,
                       delta_yoy_keur, kind:"new_spend"} ],
      }

    ``allowed_entities=set()`` fails closed → every list empty, no SQL issued.
    """
    return _development(
        session, entity=entity, year=year, month=month,
        allowed_entities=allowed_entities, top_n=top_n, is_customer=False,
    )
