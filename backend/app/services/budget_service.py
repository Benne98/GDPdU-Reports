"""Manual-budget service — pure financial logic + CRUD persistence (Phase 4).

This module backs ``/api/v1/budget``.  It has two clearly separated layers:

  PURE (no DB, golden-testable, financial-metric gated):
    * ``seasonalize``                 — split an annual value into 12 months.
    * ``rollup_partners_to_position`` — Top-N partners + 'Other' = position.
    * ``present_to_stored`` / ``stored_to_present`` — the SINGLE presented↔stored
      GL sign flip (mirror of the readers).

  DB (thin; transactional UPSERT / DELETE into fact_position_plan and, for the
  PL partner pair, the synthetic plan tables the Phase-3 readers consult):
    * seed compute (on-the-fly, NOT persisted on GET) from actuals via plan_synth.
    * ``upsert_cell`` / ``patch_position`` / ``delete_budget`` / ``seed_budget``.

=============================================================== SIGN CONVENTION
``fact_position_plan.amount`` is stored in the GL sign (+debit / −credit), exactly
like ``fact_gl_plan``.  The grid exchanges PRESENTED values with the client; the
flip happens EXACTLY ONCE on write (and is the inverse of the readers' flip):

  PL  : presented = −stored        → stored = −presented        (``amount * -1``)
  CF  : presented = −stored        → stored = −presented        (IDENTICAL to PL —
        the CF statement presents ``dim_gl_cf.amount * -1``, the same single flip,
        so a CF inflow +900 stores as −900 and re-presents as +900; no double flip)
  BS asset  (e.g. AR): presented = +stored → stored = +presented
  BS credit (e.g. AP): presented = −stored → stored = −presented

This mirrors ``fin_compat_sql.plan_grain_sql`` / ``position_plan_grain_sql``
(``amount * -1`` for PL) and ``balance_sheet._present_bs`` (asset +, credit −).
A divergence here would desync plan-vs-actual, so it is unit-tested.

=============================================================== seasonalize FORMULA
Given an annual value ``A`` and 12 weights ``w[1..12]``:
    norm(p) = w[p] / Σ_q w[q]          (Σ norm == 1)
    month(p) = A * norm(p)             ⇒ Σ_p month(p) == A   (to 1e-6)
Fallback: if Σ w == 0 (or weights absent/all-zero) use uniform norm(p) = 1/12.

  WORKED EXAMPLE
    A = 1200, weights = [2,1,1,1,1,1,1,1,1,1,1,1] (Σ=13)
      norm(1) = 2/13, month(1) = 1200*2/13 = 184.615…; the other 11 months are
      1200*1/13 = 92.307… each.  Σ = 184.615 + 11*92.307 = 1200.0 ✓.
    A = 1200, weights uniform → every month = 100.0, Σ = 1200.0.

  EDGE CASES
    * A = 0           → every month 0.0 (Σ = 0), no div-by-zero.
    * Σ w = 0         → uniform 1/12 fallback (Σ months == A).
    * sign-flip month: a NEGATIVE weight (a period opposing the annual sign, e.g.
      a credit-note month) is preserved; norm can be negative for that month and
      the split still sums to A.  E.g. A=100, w=[2,-1,1,...] keeps the −1 weight.

=============================================================== rollup FORMULA
A partner-driven position has a position total ``P`` and named Top-N partners with
values ``s_i``.  The reconciling 'Other' remainder is:
    Other = P − Σ_i s_i           ⇒ Σ_i s_i + Other == P   (the invariant)

Two edit directions, both re-establish the invariant server-side:
    edit a partner  → P stays; recompute Other = P − Σ(named).
    edit the total  → the delta moves into Other (named partners untouched):
                      Other_new = P_new − Σ(named).
'Other' may be negative (a new partner with no base, or named > total) — NOT
clamped (documented edge case).

  WORKED EXAMPLE
    P = 1000, named A=300 B=200 → Other = 1000 − 500 = 500; Σ = 300+200+500 ✓.
    edit B→250                  → Other = 1000 − 550 = 450; Σ = 1000 ✓.
    edit P→900 (total edit)     → Other = 900 − 550 = 350; named unchanged; Σ ✓.

  EDGE CASES
    * no named partners → Other = P (the whole position is unallocated).
    * Σ(named) > P      → Other negative (surfaced, never clamped).
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

# etl/ lives one level above backend/; add repo root to sys.path (mirrors plan.py).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.services import budget_positions
from etl.plan_synth import (
    PERIODS,
    SCENARIO_BUDGET,
    UNIFORM_WEIGHT,
    seasonal_index,
)

N_PERIODS = len(PERIODS)
_EPS = 1e-6

BUDGET_SOURCE_SYSTEM = "manual_budget"
# Sentinel partner_id for the 'Other' reconciling remainder row (Σ partners + Other
# == position).  Stored as a partner row but NEVER shown as a named partner.
OTHER_PARTNER_ID = "__OTHER__"


# =========================================================================== #
# Sign flip — presented <-> stored (the SINGLE place on the write path).
# =========================================================================== #
def _bs_side_for(line_code: str) -> str:
    """'asset' | 'credit' for a BS line_code (mirror of balance_sheet bs_side).

    AR (Trade receivables) is an asset; AP (Trade payables) is credit-side.  We
    derive the side from the partner kind of the position: customer→asset (AR),
    supplier→credit (AP).  Position-level BS rows that are not partner-driven
    default to 'asset' (no flip) — matching ``_present_bs``'s default.
    """
    kind = budget_positions.partner_kind_for(line_code)
    if kind == budget_positions.SUPPLIER:
        return "credit"
    return "asset"


def present_to_stored(presented: float, statement: str, line_code: str) -> float:
    """Convert a PRESENTED grid value to the STORED GL-sign ``amount``.

    PL          : stored = −presented              (inverse of ``amount * -1``).
    CF          : stored = −presented              (identical to PL — see below).
    BS asset    : stored = +presented              (inverse of ``+amount``).
    BS credit   : stored = −presented              (inverse of ``−amount``).

    CF EQUALS PL (proven).  The CF statement presents ``amount * -1`` (inflow +,
    outflow −), the SAME single flip ``position_plan_grain_sql`` applies.  So a CF
    plan round-trips exactly like a PL plan: a presented inflow of +900 stores as
    −900, which ``position_plan_grain_sql`` re-presents as −900 * -1 = +900 — matching
    the CF ACTUAL sign for the same cf_mapping leaf.  We add an explicit 'CF' branch
    for clarity (no double flip; identical to the PL fall-through).
    """
    if statement == "BS":
        return -float(presented) if _bs_side_for(line_code) == "credit" else float(presented)
    if statement == "CF":
        return -float(presented)  # CF == PL (single flip mirrors dim_gl_cf amount * -1)
    return -float(presented)  # PL


def stored_to_present(stored: float, statement: str, line_code: str) -> float:
    """Inverse of :func:`present_to_stored` (STORED amount → PRESENTED grid value)."""
    if statement == "BS":
        return -float(stored) if _bs_side_for(line_code) == "credit" else float(stored)
    if statement == "CF":
        return -float(stored)  # CF == PL
    return -float(stored)  # PL


# =========================================================================== #
# PURE: seasonalize
# =========================================================================== #
def seasonalize(annual: float, weights: Optional[dict[int, float]] = None) -> dict[int, float]:
    """Split ``annual`` into 12 monthly values via seasonal ``weights``.

    ``weights`` is ``{period(1..12): weight}``; missing periods default to 0.  The
    weights are normalised to Σ=1 (uniform 1/12 fallback when Σ==0).  Returns
    ``{period: value}`` with Σ values == ``annual`` (to 1e-6).  See module docstring
    for the formula, worked example and edge cases (annual=0, sign-flip month).
    """
    a = float(annual)
    w = {p: float((weights or {}).get(p, 0.0)) for p in PERIODS}
    total = sum(w.values())
    if abs(total) < _EPS:
        return {p: a * UNIFORM_WEIGHT for p in PERIODS}
    return {p: a * (w[p] / total) for p in PERIODS}


def annual_of(months: dict[int, float]) -> float:
    """Σ of a {period: value} month map (the annual total)."""
    return float(sum(float(months.get(p, 0.0)) for p in PERIODS))


def _all_zero_months(months: dict[int, float]) -> bool:
    """True when a {period: value} map is effectively all-zero.

    Used by the SEED materialize-suggestion path to decide whether a heuristic
    suggestion is worth persisting.  A map is treated as zero only when BOTH the
    annual total AND every individual month are within ``_EPS`` of 0 — so a
    position that nets to ~0 from offsetting non-zero months (e.g. +100/−100) is
    NOT skipped (it carries a real budget signal).
    """
    if abs(annual_of(months)) >= _EPS:
        return False
    return all(abs(float(months.get(p, 0.0))) < _EPS for p in PERIODS)


# =========================================================================== #
# PURE: distribute an L3 value across its L4 children (top-down planning).
# =========================================================================== #
def distribute_to_l4(
    parent_value: float, l4_weights: dict[str, float]
) -> dict[str, float]:
    """Split an L3 ``parent_value`` across its L4 children by historical share.

    Top-down planning: the user edits a position (L3) and the value is allocated
    to its discovered L4 sub-positions in proportion to each L4's historical share
    of the parent.  This is the L4 analogue of :func:`seasonalize` (which splits an
    annual value across 12 months) — here we split a value across named L4 keys.

    =================================================================== FORMULA
    Given ``parent_value = V`` and L4 share weights ``w[k]`` (k over L4 keys):
        share(k) = w[k] / Σ_j w[j]            (Σ share == 1)
        out[k]   = V * share(k)               ⇒ Σ_k out[k] == V   (to 1e-6)
    Fallback: if ``Σ w == 0`` (or weights absent/all-zero) use a UNIFORM split
    ``share(k) = 1/n`` over the n L4 keys.  Sign is preserved: a negative
    ``parent_value`` produces negative L4 amounts; a single L4 with a sign opposite
    the parent (negative weight) is kept (norm may be negative for that key) and the
    split still sums to ``V``.

    WORKED EXAMPLE
        V = 1000, weights = {"Gross sales": 800, "Discounts": -100, "Freight": 300}
        Σ w = 1000 → shares 0.8 / -0.1 / 0.3
        out = {"Gross sales": 800.0, "Discounts": -100.0, "Freight": 300.0}
        Σ out = 1000.0 ✓ (the opposing 'Discounts' weight is preserved).

        V = 600, weights = {"A": 1, "B": 1, "C": 1}   → {200, 200, 200}, Σ = 600 ✓.

    EDGE CASES
        * empty ``l4_weights``  → {} (no L4 children to distribute to).
        * Σ w == 0  → uniform 1/n split (Σ out == V).
        * V == 0    → every L4 0.0 (Σ == 0), no div-by-zero.
        * negative V → negative L4 amounts (sign preserved).
    """
    keys = list(l4_weights.keys())
    if not keys:
        return {}
    v = float(parent_value)
    total = sum(float(l4_weights[k]) for k in keys)
    if abs(total) < _EPS:
        share = 1.0 / len(keys)
        return {k: v * share for k in keys}
    return {k: v * (float(l4_weights[k]) / total) for k in keys}


# =========================================================================== #
# PURE: resolve an input mode (absolute / growth, annual / monthly) → 12 months.
# =========================================================================== #
def resolve_input(
    mode: str,
    value: Any,
    base: Optional[dict[str, Any]] = None,
    weights: Optional[dict[int, float]] = None,
) -> dict[int, float]:
    """Resolve a budget input of one of four MODES to absolute monthly amounts.

    Storage is ALWAYS absolute (like ``seasonalize`` today); this resolves growth
    rates against a prior-year ``base`` BEFORE the write so the stored fact never
    carries a percentage.  ``base`` is supplied by the caller (prior-year actual)
    as ``{"annual": float, "months": {period: float}}`` (either key optional).

    =================================================================== FORMULAS
      mode = 'absolute_annual'  : out = seasonalize(value, weights)
                                  (value is the presented annual; split by weights).
      mode = 'absolute_monthly' : out[p] = value[p]   (12 values taken as-is).
      mode = 'growth_annual'    : A = base_annual * (1 + value); out = seasonalize(A, weights)
                                  (value is a fractional growth rate, e.g. 0.10 = +10%).
      mode = 'growth_monthly'   : out[p] = base_month[p] * (1 + g[p])
                                  (g is scalar → same rate every month, or per-month list).

    WORKED EXAMPLE
      growth_annual, base_annual = 1000, value = 0.10, weights uniform
        A = 1000 * 1.10 = 1100 → out = {p: 91.666…} (Σ = 1100) ✓.
      growth_monthly, base_months = {1:100, 2:200, …}, value = 0.05 (scalar)
        out[1] = 100*1.05 = 105, out[2] = 200*1.05 = 210, … (per-month base preserved).
      absolute_monthly, value = [10,20,…,120] → out = {1:10, 2:20, …, 12:120}.

    EDGE CASES
      * base_annual == 0 and growth_annual → A = 0*(1+g) = 0 → all months 0.0.
        (Growth off a zero base yields ZERO, NOT the rate — documented; the caller
        should surface a note "no prior-year base, growth resolves to 0".)
      * negative base → sign preserved: base_annual = -1000, g = 0.10 → A = -1100.
      * missing weights → seasonalize's uniform 1/12 fallback.
      * growth_monthly with a missing base month → that month's base is 0 → 0.0.
    """
    base = base or {}
    if mode == "absolute_annual":
        a = float(value)
        if not math.isfinite(a):
            raise ValueError("absolute_annual value must be finite")
        return seasonalize(a, weights)

    if mode == "absolute_monthly":
        vals = list(value)
        if len(vals) != N_PERIODS:
            raise ValueError(f"absolute_monthly value must have exactly {N_PERIODS} entries")
        out = {p: float(vals[p - 1]) for p in PERIODS}
        if not all(math.isfinite(v) for v in out.values()):
            raise ValueError("absolute_monthly values must be finite")
        return out

    if mode == "growth_annual":
        g = float(value)
        if not math.isfinite(g):
            raise ValueError("growth_annual value must be finite")
        base_annual = float(base.get("annual", 0.0))
        return seasonalize(base_annual * (1.0 + g), weights)

    if mode == "growth_monthly":
        base_months = {p: float((base.get("months") or {}).get(p, 0.0)) for p in PERIODS}
        if isinstance(value, (list, tuple)):
            if len(value) != N_PERIODS:
                raise ValueError(f"growth_monthly per-month value must have {N_PERIODS} entries")
            g = {p: float(value[p - 1]) for p in PERIODS}
        else:
            gv = float(value)
            g = {p: gv for p in PERIODS}
        out = {p: base_months[p] * (1.0 + g[p]) for p in PERIODS}
        if not all(math.isfinite(v) for v in out.values()):
            raise ValueError("growth_monthly resolved to non-finite values")
        return out

    raise ValueError(f"unknown input mode: {mode!r}")


# =========================================================================== #
# PURE: partner roll-up
# =========================================================================== #
@dataclass
class PartnerRow:
    partner_id: str
    name: str
    annual: float = 0.0
    months: dict[int, float] = field(default_factory=dict)


@dataclass
class RollupResult:
    """Σ(named partners) + Other == position (the invariant)."""

    position_annual: float
    partners: list[PartnerRow]
    other_annual: float

    def invariant_holds(self) -> bool:
        s = sum(p.annual for p in self.partners) + self.other_annual
        return abs(s - self.position_annual) < 1e-6


def rollup_partners_to_position(
    partner_rows: list[PartnerRow],
    position_seed: float,
    *,
    top_n: int = 20,
) -> RollupResult:
    """Top-N named partners + an 'Other' remainder reconciling to ``position_seed``.

    The named partners (already the rows the caller wants to show, capped to
    ``top_n`` by absolute annual) are kept as-is; ``Other = position − Σ(named)``.
    Editing a partner and re-running keeps ``position_seed`` and re-derives Other;
    editing the position total (passing a new ``position_seed``) moves the delta
    into Other.  Invariant: Σ(partners)+Other == position (see module docstring).
    """
    ranked = sorted(partner_rows, key=lambda p: abs(p.annual), reverse=True)
    named = ranked[: max(0, top_n)]
    other = float(position_seed) - sum(p.annual for p in named)
    return RollupResult(
        position_annual=float(position_seed),
        partners=named,
        other_annual=other,
    )


# =========================================================================== #
# Seed compute (on-the-fly; NOT persisted on GET)
# =========================================================================== #
def _position_actual_profile(
    session: Session,
    *,
    line_code: str,
    statement: str,
    fiscal_year: int,
    entity_prefix: Optional[str],
) -> tuple[float, dict[int, float]]:
    """(annual, seasonal weights) for a POSITION from its actuals in ``fiscal_year``.

    Aggregates GL movements for the position's level_3 (level_0 = statement) per
    fiscal_period, in the PRESENTED sign (so the seed is a presented annual + a
    seasonal profile).  Returns (0.0, uniform) when there are no actuals.
    """
    pos = budget_positions.position_for(line_code)
    level_3 = pos.level_3 if pos else None
    ent_clause = ""
    params: dict[str, Any] = {"fy": fiscal_year, "stmt": statement}
    if level_3:
        params["l3"] = level_3
        l3_clause = "AND TRIM(a.level_3) = :l3"
    else:
        params["lc_l3"] = line_code
        l3_clause = "AND TRIM(a.level_3) = :lc_l3"
    if entity_prefix:
        params["ep"] = str(entity_prefix)[:2]
        ent_clause = "AND l.entity_prefix = :ep"

    sql = text(f"""
        SELECT e.fiscal_period AS p, SUM(l.amount) AS amt
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE a.level_0 = :stmt
          AND e.fiscal_year = :fy
          AND e.fiscal_period BETWEEN 1 AND 12
          {l3_clause}
          {ent_clause}
        GROUP BY e.fiscal_period
    """)
    rows = session.execute(sql, params).fetchall()
    # Present each period (stored→presented) then build annual + weights.
    months_presented: dict[int, float] = {p: 0.0 for p in PERIODS}
    for r in rows:
        p = int(r[0])
        if 1 <= p <= 12:
            months_presented[p] = stored_to_present(float(r[1] or 0.0), statement, line_code)
    annual = sum(months_presented.values())
    if abs(annual) < _EPS:
        return 0.0, {p: UNIFORM_WEIGHT for p in PERIODS}
    weights = {p: months_presented[p] / annual for p in PERIODS}
    return annual, weights


def _l4_actual_profiles(
    session: Session,
    *,
    line_code: str,
    statement: str,
    fiscal_year: int,
    entity_prefix: Optional[str],
) -> dict[str, tuple[float, dict[int, float]]]:
    """{level_4: (presented_annual, seasonal weights)} for the L4 children of a
    position, from its actuals in ``fiscal_year``.

    Mirror of :func:`_position_actual_profile` but grouped by
    ``dim_gl_account.level_4`` under the position's level_3 (the dynamically
    discovered L4 keys, exactly like the statement does in ``fin_compat_pl``).
    Each L4 key gets BOTH its presented annual (used to derive the L4 SHARE for
    top-down :func:`distribute_to_l4`) and its own seasonal weights (used to split
    that L4's value across 12 months).

    Only non-empty level_4 values are returned (the L3 grain itself is the '' row,
    handled by ``_position_actual_profile``).  Returns {} when no L4 actuals exist
    (caller falls back to a single L3 row).
    """
    pos = budget_positions.position_for(line_code)
    level_3 = pos.level_3 if pos else None
    ent_clause = ""
    params: dict[str, Any] = {"fy": fiscal_year, "stmt": statement}
    if level_3:
        params["l3"] = level_3
        l3_clause = "AND TRIM(a.level_3) = :l3"
    else:
        params["lc_l3"] = line_code
        l3_clause = "AND TRIM(a.level_3) = :lc_l3"
    if entity_prefix:
        params["ep"] = str(entity_prefix)[:2]
        ent_clause = "AND l.entity_prefix = :ep"

    sql = text(f"""
        SELECT TRIM(a.level_4) AS l4, e.fiscal_period AS p, SUM(l.amount) AS amt
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE a.level_0 = :stmt
          AND e.fiscal_year = :fy
          AND e.fiscal_period BETWEEN 1 AND 12
          AND COALESCE(TRIM(a.level_4), '') <> ''
          {l3_clause}
          {ent_clause}
        GROUP BY TRIM(a.level_4), e.fiscal_period
    """)
    rows = session.execute(sql, params).fetchall()
    by_l4: dict[str, dict[int, float]] = {}
    for r in rows:
        l4 = (r[0] or "").strip()
        if not l4:
            continue
        p = int(r[1])
        if 1 <= p <= 12:
            by_l4.setdefault(l4, {q: 0.0 for q in PERIODS})[p] = stored_to_present(
                float(r[2] or 0.0), statement, line_code
            )
    out: dict[str, tuple[float, dict[int, float]]] = {}
    for l4, months in by_l4.items():
        annual = sum(months.values())
        if abs(annual) < _EPS:
            weights = {p: UNIFORM_WEIGHT for p in PERIODS}
        else:
            weights = {p: months[p] / annual for p in PERIODS}
        out[l4] = (annual, weights)
    return out


def _partner_actual_profiles(
    session: Session,
    *,
    line_code: str,
    fiscal_year: int,
    entity_prefix: Optional[str],
) -> dict[str, tuple[str, float, dict[int, float]]]:
    """{partner_id: (name, presented_annual, seasonal weights)} for a partner-driven
    position, from its actuals fact (fact_sales / fact_com) in ``fiscal_year``.

    The fact measures (gross_sales / cost_of_materials) are already POSITIVE
    magnitudes, so no sign flip is needed for the partner seed.  Seasonality uses
    plan_synth.seasonal_index over the partner's monthly profile.
    """
    pos = budget_positions.position_for(line_code)
    if pos is None:
        return {}
    fact, id_col, val_col = pos.actual_fact, pos.actual_id_col, pos.actual_value
    dim = "dim_customer" if pos.partner_kind == budget_positions.CUSTOMER else "dim_supplier"

    ent_clause = ""
    params: dict[str, Any] = {"fy": fiscal_year, "l3": pos.level_3}
    if entity_prefix:
        params["ep"] = str(entity_prefix)[:2]
        ent_clause = "AND LEFT(f.account_number_group, 2) = :ep"

    name_expr = "TRIM(COALESCE(d.name_line_1,'') || ' ' || COALESCE(d.name_line_2,''))"
    sql = text(f"""
        SELECT f.{id_col} AS pid,
               MAX(NULLIF({name_expr}, '')) AS name,
               e.fiscal_period AS p,
               SUM(f.{val_col}) AS amt
        FROM {fact} f
        JOIN fact_gl_line l ON l.booking_line_id = f.booking_line_id
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = f.account_number_group
         AND a.fiscal_year = f.fiscal_year
        LEFT JOIN {dim} d ON d.{id_col} = f.{id_col}
        WHERE f.{id_col} IS NOT NULL
          AND f.fiscal_year = :fy
          AND TRIM(a.level_3) = :l3
          AND e.fiscal_period BETWEEN 1 AND 12
          {ent_clause}
        GROUP BY f.{id_col}, e.fiscal_period
    """)
    rows = session.execute(sql, params).fetchall()
    by_partner: dict[str, dict[str, Any]] = {}
    for r in rows:
        pid = str(r[0])
        rec = by_partner.setdefault(pid, {"name": r[1], "months": {p: 0.0 for p in PERIODS}})
        if r[1] and not rec["name"]:
            rec["name"] = r[1]
        p = int(r[2])
        if 1 <= p <= 12:
            rec["months"][p] = float(r[3] or 0.0)
    out: dict[str, tuple[str, float, dict[int, float]]] = {}
    for pid, rec in by_partner.items():
        months = rec["months"]
        annual = sum(months.values())
        if abs(annual) < _EPS:
            weights = {p: UNIFORM_WEIGHT for p in PERIODS}
        else:
            weights = {p: months[p] / annual for p in PERIODS}
        out[pid] = (rec["name"] or pid, annual, weights)
    return out


def _partner_actual_profiles_with_fallback(
    session: Session,
    *,
    line_code: str,
    fiscal_year: int,
    entity_prefix: Optional[str],
) -> dict[str, tuple[str, float, dict[int, float]]]:
    """Partner profiles for the grid — plan year first, then prior FYs / ledger anchor."""
    from app.services.gl_analysis_common import latest_anchor

    candidates: list[int] = [fiscal_year]
    for delta in (1, 2, 3):
        candidates.append(fiscal_year - delta)
    anchor = latest_anchor(session, entity_prefix or "")
    if anchor is not None:
        candidates.append(int(anchor[0]))
    seen: set[int] = set()
    for fy in candidates:
        if fy in seen or fy < 2000:
            continue
        seen.add(fy)
        profiles = _partner_actual_profiles(
            session, line_code=line_code, fiscal_year=fy, entity_prefix=entity_prefix,
        )
        if profiles:
            return profiles
    return {}


# =========================================================================== #
# Read existing budget rows (presented), for GET overlay.
# =========================================================================== #
def _read_budget_position_months(
    session: Session,
    *,
    statement: str,
    fiscal_year: int,
    entity_prefix: Optional[str],
) -> dict[str, dict[str, dict[int, float]]]:
    """Read persisted budget rows, presented, grouped by line_code then partner.

    Returns {line_code: {partner_id_or_'': {period: presented_value}}}.  Entity
    precedence: per-entity rows override consolidated ('') for the same
    (line_code, partner).  Consolidated view (entity None): the '' row per
    (line_code, partner) when one exists (explicit override), ELSE the SUM of the
    per-entity rows for that (line_code, partner) — mirroring the statement readers.

    The GROUP BY does NOT key on ``level_4`` — it SUMS all level_4 rows (the ''
    L3-level row OR its L4 set, never both, per the write-path XOR guard) into the
    position total, mirroring the reader (``position_plan_grain_sql`` GROUPs BY
    line_code).  So a position reflects either its '' row or the sum of its L4 rows.

    GOLDEN-SAFETY: with no budget rows the WHERE matches nothing → {} (the
    consolidated entity-sum only changes how EXISTING rows aggregate).
    """
    params: dict[str, Any] = {"stmt": statement, "fy": fiscal_year}
    if entity_prefix:
        params["ep"] = str(entity_prefix)[:2]
        ent_clause = (
            "AND ( p.entity_prefix = :ep "
            "      OR ( p.entity_prefix = '' AND NOT EXISTS ( "
            "        SELECT 1 FROM fact_position_plan e "
            "        WHERE e.statement = p.statement AND e.scenario = 'budget' "
            "          AND e.line_code = p.line_code AND e.partner_id = p.partner_id "
            "          AND e.fiscal_year = p.fiscal_year AND e.entity_prefix = :ep ) ) )"
        )
    else:
        # Consolidated: '' row per (line_code, partner) when present (explicit
        # override), ELSE Σ of the per-entity rows for that (line_code, partner).
        ent_clause = (
            "AND ( p.entity_prefix = '' "
            "      OR ( p.entity_prefix <> '' AND NOT EXISTS ( "
            "        SELECT 1 FROM fact_position_plan c "
            "        WHERE c.statement = p.statement AND c.scenario = 'budget' "
            "          AND c.line_code = p.line_code AND c.partner_id = p.partner_id "
            "          AND c.fiscal_year = p.fiscal_year AND c.entity_prefix = '' ) ) )"
        )

    sql = text(f"""
        SELECT p.line_code, p.partner_id, p.fiscal_period, SUM(p.amount) AS amt
        FROM fact_position_plan p
        WHERE p.statement = :stmt AND p.scenario = 'budget'
          AND p.fiscal_year = :fy
          AND p.fiscal_period BETWEEN 1 AND 12
          {ent_clause}
        GROUP BY p.line_code, p.partner_id, p.fiscal_period
    """)
    out: dict[str, dict[str, dict[int, float]]] = {}
    for r in session.execute(sql, params).fetchall():
        lc, pid, period = str(r[0]), str(r[1]), int(r[2])
        present = stored_to_present(float(r[3] or 0.0), statement, lc)
        out.setdefault(lc, {}).setdefault(pid, {})[period] = present
    return out


def _read_budget_l4_months(
    session: Session,
    *,
    statement: str,
    fiscal_year: int,
    entity_prefix: Optional[str],
) -> dict[str, dict[str, dict[int, float]]]:
    """Persisted budget L4 rows (``level_4 <> ''``), presented, grouped
    ``{line_code: {level_4: {period: presented_value}}}``.

    Same entity precedence/consolidated-sum logic as
    :func:`_read_budget_position_months`, but keyed on ``level_4`` (the position-
    level '' rows are excluded — those are handled by the parent overlay).  Used to
    overlay saved L4 child cells onto the discovered-L4 tree nodes.  With no budget
    rows the WHERE matches nothing → ``{}`` (golden-safe).
    """
    params: dict[str, Any] = {"stmt": statement, "fy": fiscal_year}
    if entity_prefix:
        params["ep"] = str(entity_prefix)[:2]
        ent_clause = (
            "AND ( p.entity_prefix = :ep "
            "      OR ( p.entity_prefix = '' AND NOT EXISTS ( "
            "        SELECT 1 FROM fact_position_plan e "
            "        WHERE e.statement = p.statement AND e.scenario = 'budget' "
            "          AND e.line_code = p.line_code AND e.level_4 = p.level_4 "
            "          AND e.fiscal_year = p.fiscal_year AND e.entity_prefix = :ep ) ) )"
        )
    else:
        ent_clause = (
            "AND ( p.entity_prefix = '' "
            "      OR ( p.entity_prefix <> '' AND NOT EXISTS ( "
            "        SELECT 1 FROM fact_position_plan c "
            "        WHERE c.statement = p.statement AND c.scenario = 'budget' "
            "          AND c.line_code = p.line_code AND c.level_4 = p.level_4 "
            "          AND c.fiscal_year = p.fiscal_year AND c.entity_prefix = '' ) ) )"
        )

    sql = text(f"""
        SELECT p.line_code, p.level_4, p.fiscal_period, SUM(p.amount) AS amt
        FROM fact_position_plan p
        WHERE p.statement = :stmt AND p.scenario = 'budget'
          AND p.fiscal_year = :fy
          AND p.fiscal_period BETWEEN 1 AND 12
          AND COALESCE(p.level_4, '') <> ''
          AND p.partner_id = ''
          {ent_clause}
        GROUP BY p.line_code, p.level_4, p.fiscal_period
    """)
    out: dict[str, dict[str, dict[int, float]]] = {}
    for r in session.execute(sql, params).fetchall():
        lc, l4, period = str(r[0]), str(r[1]), int(r[2])
        present = stored_to_present(float(r[3] or 0.0), statement, lc)
        out.setdefault(lc, {}).setdefault(l4, {})[period] = present
    return out


def has_budget_rows(
    session: Session,
    *,
    statement: Optional[str] = None,
    fiscal_year: Optional[int] = None,
    entity_prefix: Optional[str] = None,
) -> int:
    """Count budget rows matching the (optional) scope — used by /plan summary."""
    clauses = ["scenario = 'budget'"]
    params: dict[str, Any] = {}
    if statement:
        clauses.append("statement = :stmt")
        params["stmt"] = statement
    if fiscal_year is not None:
        clauses.append("fiscal_year = :fy")
        params["fy"] = fiscal_year
    if entity_prefix is not None:
        clauses.append("entity_prefix = :ep")
        params["ep"] = str(entity_prefix)[:2]
    where = " AND ".join(clauses)
    return int(
        session.execute(
            text(f"SELECT COUNT(*) FROM fact_position_plan WHERE {where}"), params
        ).scalar()
        or 0
    )


# =========================================================================== #
# Grid build (GET) — synthetic seed overlaid by persisted budget (pure-read).
# =========================================================================== #
def build_grid(
    session: Session,
    *,
    statement: str,
    fiscal_year: int,
    entity_prefix: Optional[str] = None,
    top_n: int = 20,
    heuristic: str = "prior_year",
    growth_pct: float = 0.0,
    level: str = "L4",
    light: bool = False,
) -> dict[str, Any]:
    """Read-only budget grid (TREE): the statement's reporting positions (L3 nodes
    in ``dim_pl_structure`` sort order, mirroring the IS/BS), each seeded from
    actuals and overlaid by any saved budget rows.  NEVER writes.  See router for
    the response contract.

    Each position carries:
      * ``level_3`` — the structure reporting-position label (tree alignment).
      * ``suggestion`` + ``explanation`` from :mod:`budget_heuristics` (the
        Finssentials-heuristic proposal + a "how we derived this" link).
      * ``children`` — the discovered L4 sub-positions (PL only), present ONLY when
        ``level='L4'`` (``level='L3'`` omits them so the payload stays bounded).
        Each child: ``{level_4, label, annual, months[12]}`` from the historical L4
        profile, overlaid by any saved L4 budget rows.
      * ``partners`` + ``other`` for partner-driven positions (Top-N + 'Other').

    ``heuristic`` selects the suggestion method (default ``prior_year``) and
    ``growth_pct`` the growth assumption (default +0%); the suggestion is the value
    the user can ``Apply`` (materialise via :func:`seed_budget`).  Extra keys are
    additive — the write/Excel paths read only ``line_code`` / ``months``.
    """
    # Local import to avoid a circular import (budget_heuristics imports this module).
    from app.services import budget_heuristics
    want_l4 = str(level or "").upper() == "L4" and statement == "PL"
    structure = _load_positions(session, statement)
    saved = _read_budget_position_months(
        session, statement=statement, fiscal_year=fiscal_year, entity_prefix=entity_prefix
    )
    saved_l4 = (
        _read_budget_l4_months(
            session, statement=statement, fiscal_year=fiscal_year,
            entity_prefix=entity_prefix,
        )
        if want_l4
        else {}
    )

    positions: list[dict[str, Any]] = []
    for line_code, label, level_3 in structure:
        pd = budget_positions.is_partner_driven(line_code)
        seed_annual, seed_weights = _position_actual_profile(
            session, line_code=line_code, statement=statement,
            fiscal_year=fiscal_year, entity_prefix=entity_prefix,
        )
        synthetic_annual = round(seed_annual, 2)

        saved_pos = saved.get(line_code, {})
        pos_months_saved = saved_pos.get("")
        if pos_months_saved is not None:
            months = {p: round(pos_months_saved.get(p, 0.0), 2) for p in PERIODS}
        else:
            months = {p: round(v, 2) for p, v in seasonalize(seed_annual, seed_weights).items()}
        annual = round(annual_of(months), 2)

        sug = (
            {
                "suggestion_annual": 0.0,
                "weights": {p: 1.0 / 12.0 for p in PERIODS},
                "explanation": {
                    "method": "blank",
                    "note": "Light grid — heuristic suggestions skipped.",
                },
            }
            if light
            else budget_heuristics.suggest(
                session, line_code=line_code, statement=statement,
                fiscal_year=fiscal_year, entity_prefix=entity_prefix,
                method=heuristic, growth_pct=growth_pct,
            )
        )
        sug_annual = round(float(sug["suggestion_annual"]), 2)
        sug_months = {p: round(v, 2) for p, v in seasonalize(sug_annual, sug["weights"]).items()}

        entry: dict[str, Any] = {
            "line_code": line_code,
            "label": label,
            "level_3": level_3,
            "annual": annual,
            "months": [months[p] for p in PERIODS],
            "synthetic_annual": synthetic_annual,
            "is_partner_driven": pd,
            "suggestion": {
                "annual": sug_annual,
                "months": [sug_months[p] for p in PERIODS],
            },
            "explanation": sug["explanation"],
        }

        if want_l4:
            entry["children"] = _build_l4_children(
                session, line_code=line_code, statement=statement,
                fiscal_year=fiscal_year, entity_prefix=entity_prefix,
                saved_l4=saved_l4.get(line_code, {}),
            )

        if pd:
            profiles = _partner_actual_profiles_with_fallback(
                session, line_code=line_code, fiscal_year=fiscal_year,
                entity_prefix=entity_prefix,
            )
            partner_rows: list[PartnerRow] = []
            # Union of partners with actuals and partners with saved budget rows.
            # Exclude the position-level ('') and the synthetic 'Other' remainder row
            # (__OTHER__) — Other is re-derived by the roll-up, never shown as a named
            # partner (else it would be double-counted).
            pids = set(profiles.keys()) | {
                k for k in saved_pos.keys() if k not in ("", OTHER_PARTNER_ID)
            }
            for pid in pids:
                name, p_annual, p_weights = profiles.get(pid, (pid, 0.0, None))
                p_saved = saved_pos.get(pid)
                if p_saved is not None:
                    p_months = {p: round(p_saved.get(p, 0.0), 2) for p in PERIODS}
                else:
                    p_months = {p: round(v, 2) for p, v in seasonalize(p_annual, p_weights).items()}
                partner_rows.append(
                    PartnerRow(
                        partner_id=pid, name=name,
                        annual=round(annual_of(p_months), 2), months=p_months,
                    )
                )
            roll = rollup_partners_to_position(partner_rows, annual, top_n=top_n)
            entry["partners"] = [
                {
                    "partner_id": pr.partner_id,
                    "name": pr.name,
                    "annual": round(pr.annual, 2),
                    "months": [pr.months.get(p, 0.0) for p in PERIODS],
                }
                for pr in roll.partners
            ]
            entry["other"] = round(roll.other_annual, 2)

        positions.append(entry)

    return {
        "statement": statement,
        "fiscal_year": fiscal_year,
        "entity": entity_prefix or "",
        "top_n": top_n,
        "level": "L4" if want_l4 else "L3",
        "positions": positions,
    }


def _build_l4_children(
    session: Session,
    *,
    line_code: str,
    statement: str,
    fiscal_year: int,
    entity_prefix: Optional[str],
    saved_l4: dict[str, dict[int, float]],
) -> list[dict[str, Any]]:
    """L4 child nodes ``[{level_4, label, annual, months[12]}]`` for a PL position.

    Children are the dynamically discovered L4 keys (from
    :func:`_l4_actual_profiles`) UNION any saved-L4 budget keys.  Each child's
    months are the saved L4 budget row when present, else the seasonalized
    historical L4 profile.  Stable sort by ``level_4`` key.  Returns ``[]`` when the
    position has no L4 history and no saved L4 rows (caller renders the L3 leaf).
    """
    profiles = _l4_actual_profiles(
        session, line_code=line_code, statement=statement,
        fiscal_year=fiscal_year, entity_prefix=entity_prefix,
    )
    keys = sorted(set(profiles.keys()) | set(saved_l4.keys()))
    children: list[dict[str, Any]] = []
    for l4 in keys:
        l4_annual, l4_weights = profiles.get(l4, (0.0, None))
        saved_months = saved_l4.get(l4)
        if saved_months is not None:
            months = {p: round(saved_months.get(p, 0.0), 2) for p in PERIODS}
        else:
            months = {p: round(v, 2) for p, v in seasonalize(l4_annual, l4_weights).items()}
        children.append({
            "level_4": l4,
            "label": l4,
            "annual": round(annual_of(months), 2),
            "months": [months[p] for p in PERIODS],
        })
    return children


def _load_positions(session: Session, statement: str) -> list[tuple[str, str, str]]:
    """(line_code, label, level_3) mapping positions for the statement, in sort order.

    ``level_3`` is the structure's reporting-position label (mirrors the IS/BS tree
    nodes); it is surfaced on each tree node so the FE can align the budget grid with
    the Income Statement / Balance Sheet hierarchy.
    """
    if statement == "BS":
        sql = text(
            "SELECT line_code, COALESCE(balance_title, line_code) AS label, "
            "       COALESCE(TRIM(level_3), '') AS level_3 "
            "FROM dim_pl_structure "
            "WHERE row_type = 'mapping' AND kpi_code LIKE 'BS:%' "
            "ORDER BY sort_order"
        )
    else:
        sql = text(
            "SELECT line_code, COALESCE(balance_title, line_code) AS label, "
            "       COALESCE(TRIM(level_3), '') AS level_3 "
            "FROM dim_pl_structure "
            "WHERE row_type = 'mapping' "
            "  AND (kpi_code IS NULL OR kpi_code NOT LIKE 'BS:%') "
            "ORDER BY sort_order"
        )
    return [(str(r[0]), str(r[1]), str(r[2] or "")) for r in session.execute(sql).fetchall()]


# =========================================================================== #
# WRITE: upsert / patch / delete / seed (transactional)
# =========================================================================== #
def _upsert_rows(
    session: Session,
    rows: list[dict[str, Any]],
    *,
    updated_by: Optional[str],
    is_synthetic: bool,
    source_system: str = BUDGET_SOURCE_SYSTEM,
    scenario: str = "budget",
) -> int:
    """UPSERT a batch of fact_position_plan rows.  ``rows`` carry STORED amounts.

    Each row dict: statement, line_code, entity_prefix, partner_id, partner_kind,
    fiscal_year, fiscal_period, amount (stored).  Does NOT commit (caller manages
    the transaction).

    ``scenario`` selects the ``fact_position_plan.scenario`` band written (part of the
    ON CONFLICT key).  Default ``'budget'`` keeps every existing caller BYTE-IDENTICAL;
    the forecast seeder passes ``scenario='forecast'`` to materialise the forecast band.
    """
    n = 0
    for row in rows:
        session.execute(
            text("""
                INSERT INTO fact_position_plan
                  (statement, line_code, entity_prefix, partner_id, level_4,
                   partner_kind, fiscal_year, fiscal_period, scenario, amount,
                   is_synthetic, source_system, updated_at, updated_by)
                VALUES
                  (:stmt, :lc, :ep, :pid, :l4, :pk, :fy, :fp, :scenario, :amt,
                   :syn, :ss, NOW(), :ub)
                ON CONFLICT (statement, line_code, entity_prefix, partner_id,
                             level_4, fiscal_year, fiscal_period, scenario)
                DO UPDATE SET
                  amount        = EXCLUDED.amount,
                  partner_kind  = EXCLUDED.partner_kind,
                  is_synthetic  = EXCLUDED.is_synthetic,
                  source_system = EXCLUDED.source_system,
                  updated_at    = NOW(),
                  updated_by    = EXCLUDED.updated_by
            """),
            {
                "stmt": row["statement"],
                "lc": row["line_code"],
                "ep": row.get("entity_prefix", "") or "",
                "pid": row.get("partner_id", "") or "",
                "l4": row.get("level_4", "") or "",
                "pk": row.get("partner_kind"),
                "fy": int(row["fiscal_year"]),
                "fp": int(row["fiscal_period"]),
                "amt": float(row["amount"]),
                "syn": bool(is_synthetic),
                "ss": source_system,
                "ub": updated_by,
                "scenario": scenario,
            },
        )
        n += 1
    return n


def _cell_rows(
    *,
    statement: str,
    line_code: str,
    entity_prefix: str,
    partner_id: str,
    partner_kind: Optional[str],
    fiscal_year: int,
    months_presented: dict[int, float],
    level_4: str = "",
) -> list[dict[str, Any]]:
    """12 stored-sign rows for one (line_code, partner, level_4) cell from presented
    months.  ``level_4=''`` is the L3-level row; a non-empty value is an L4 row."""
    rows: list[dict[str, Any]] = []
    for p in PERIODS:
        presented = float(months_presented.get(p, 0.0))
        rows.append({
            "statement": statement,
            "line_code": line_code,
            "entity_prefix": entity_prefix,
            "partner_id": partner_id,
            "level_4": level_4 or "",
            "partner_kind": partner_kind,
            "fiscal_year": fiscal_year,
            "fiscal_period": p,
            "amount": present_to_stored(presented, statement, line_code),
        })
    return rows


def _clear_complementary_level(
    session: Session,
    *,
    statement: str,
    line_code: str,
    entity_prefix: str,
    fiscal_year: int,
    writing_level4: str,
    partner_id: Optional[str] = None,
) -> int:
    """XOR guard against double-counting (the reader SUMS all rows for a line_code).

    A position is planned EITHER as one L3-level row (``level_4=''``) OR as its set
    of L4 rows (``level_4<>''``), never both.  Before an upsert we delete the rows of
    the COMPLEMENTARY level for the same scope:

      writing an L4 row (``writing_level4<>''``) → delete the ``level_4=''`` rows.
      writing the L3-level row (``writing_level4==''``) → delete all ``level_4<>''``.

    Scope = (statement, line_code, entity_prefix, fiscal_year) and, when
    ``partner_id`` is given, that partner only (so editing one partner's L4 cell does
    not wipe other partners' L3 rows).  Does NOT commit (caller owns the txn)."""
    params: dict[str, Any] = {
        "stmt": statement, "lc": line_code, "ep": entity_prefix or "",
        "fy": int(fiscal_year),
    }
    if (writing_level4 or "") != "":
        level_clause = "AND level_4 = ''"          # writing L4 → drop the L3-level row
    else:
        level_clause = "AND level_4 <> ''"         # writing L3 → drop all L4 rows
    partner_clause = ""
    if partner_id is not None:
        params["pid"] = partner_id or ""
        partner_clause = "AND partner_id = :pid"
    res = session.execute(
        text(f"""
            DELETE FROM fact_position_plan
            WHERE statement = :stmt AND scenario = 'budget'
              AND line_code = :lc AND entity_prefix = :ep AND fiscal_year = :fy
              {partner_clause}
              {level_clause}
        """),
        params,
    )
    return int(res.rowcount or 0)


def _months_from_payload(
    *, months: Optional[list[float]], annual: Optional[float], weights: Optional[dict[int, float]]
) -> dict[int, float]:
    """Resolve a {period: presented} map from either explicit months[12] or an
    annual value seasonalized server-side by ``weights``.

    Rejects non-finite amounts (NaN/Infinity) — they would poison the stored fact
    and any downstream aggregation (mirrors the Pydantic ``allow_inf_nan=False``).
    """
    if months is not None:
        if len(months) != N_PERIODS:
            raise ValueError(f"months must have exactly {N_PERIODS} entries")
        vals = {p: float(months[p - 1]) for p in PERIODS}
        if not all(math.isfinite(v) for v in vals.values()):
            raise ValueError("months must be finite numbers (no NaN/Infinity)")
        return vals
    if annual is not None:
        a = float(annual)
        if not math.isfinite(a):
            raise ValueError("annual must be a finite number (no NaN/Infinity)")
        return seasonalize(a, weights)
    raise ValueError("either months[12] or annual must be provided")


def _resolve_months_presented(
    *,
    months: Optional[list[float]],
    annual: Optional[float],
    weights: Optional[dict[int, float]],
    input_mode: Optional[str],
    growth_value: Any = None,
    base: Optional[dict[str, Any]] = None,
) -> dict[int, float]:
    """Resolve a {period: presented} map for a cell from EITHER the legacy
    months/annual payload OR an ``input_mode`` (absolute / growth, annual / monthly).

    When ``input_mode`` is None (the legacy path) this is exactly
    :func:`_months_from_payload`.  Otherwise it routes to :func:`resolve_input`,
    resolving growth against the caller-supplied prior-year ``base`` BEFORE the write
    (storage stays absolute).  ``value`` for resolve_input is taken from
    ``growth_value`` for growth modes, else from ``months`` (monthly) / ``annual``.
    """
    if input_mode is None:
        return _months_from_payload(months=months, annual=annual, weights=weights)
    if input_mode in ("growth_annual", "growth_monthly"):
        value: Any = growth_value
    elif input_mode == "absolute_monthly":
        if months is None:
            raise ValueError("absolute_monthly requires months[12]")
        value = months
    else:  # absolute_annual
        if annual is None:
            raise ValueError("absolute_annual requires annual")
        value = annual
    return resolve_input(input_mode, value, base=base, weights=weights)


def upsert_cell(
    session: Session,
    *,
    statement: str,
    line_code: str,
    entity: str,
    partner_id: Optional[str],
    partner_kind: Optional[str],
    fiscal_year: int,
    months: Optional[list[float]] = None,
    annual: Optional[float] = None,
    weights: Optional[dict[int, float]] = None,
    level_4: str = "",
    input_mode: Optional[str] = None,
    growth_value: Any = None,
    base: Optional[dict[str, Any]] = None,
    distribute_l4: bool = False,
    updated_by: Optional[str] = None,
) -> dict[str, Any]:
    """Persist ONE cell (a position-level or a single-partner row), then recompute
    'Other' for partner-driven positions.  Annual edits are seasonalized
    server-side.  is_synthetic=FALSE.  Commits the transaction.

    ``level_4=''`` writes the L3-level row; a non-empty value writes an L4 row.  The
    XOR guard (``_clear_complementary_level``) clears the complementary level for the
    scope first so the reader (which SUMS all rows per line_code) never double-counts.

    INPUT MODE (Phase 1): when ``input_mode`` is set the cell value is resolved to
    absolute monthly amounts via :func:`resolve_input` (absolute/growth × annual/
    monthly) against the prior-year ``base`` BEFORE the write (storage stays
    absolute).  ``input_mode=None`` keeps the legacy months/annual path verbatim.

    TOP-DOWN L4 (Phase 1): when ``distribute_l4`` is True and this is an L3 edit
    (``level_4==''``, no partner), the resolved annual is split across the position's
    discovered L4 children by historical share (:func:`distribute_to_l4`, each L4
    seasonalized by its own profile); L4 rows are written and the '' row is XOR-
    cleared, so the reader's per-line_code SUM reproduces the L3 total exactly once.
    With no L4 history the position is written as a single L3 ('') row (fallback).
    """
    ep = (entity or "")[:2]
    pid = (partner_id or "")
    l4 = (level_4 or "")
    # The reserved 'Other' sentinel is derived server-side (Σ partners + Other ==
    # position); never let a client write it directly.
    if pid == OTHER_PARTNER_ID:
        raise ValueError(f"partner_id '{OTHER_PARTNER_ID}' is reserved and cannot be written directly")
    months_presented = _resolve_months_presented(
        months=months, annual=annual, weights=weights,
        input_mode=input_mode, growth_value=growth_value, base=base,
    )

    # Top-down: an L3 edit distributed to its L4 children by historical share.
    if distribute_l4 and not pid and not l4:
        profiles = _l4_actual_profiles(
            session, line_code=line_code, statement=statement,
            fiscal_year=fiscal_year, entity_prefix=(ep or None),
        )
        if profiles:
            return _write_distributed_l4(
                session, statement=statement, line_code=line_code, entity_prefix=ep,
                partner_kind=partner_kind, fiscal_year=fiscal_year,
                parent_annual=annual_of(months_presented), profiles=profiles,
                updated_by=updated_by,
            )
        # else: no L4 history → fall through and write a single L3 ('') row.

    try:
        # XOR: writing an L4 cell drops the L3-level '' row for this scope (and vice
        # versa) so the per-line_code SUM in the reader is not double-counted.
        _clear_complementary_level(
            session, statement=statement, line_code=line_code, entity_prefix=ep,
            fiscal_year=fiscal_year, writing_level4=l4, partner_id=pid,
        )
        rows = _cell_rows(
            statement=statement, line_code=line_code, entity_prefix=ep,
            partner_id=pid, partner_kind=partner_kind, fiscal_year=fiscal_year,
            months_presented=months_presented, level_4=l4,
        )
        n = _upsert_rows(session, rows, updated_by=updated_by, is_synthetic=False)

        # Editing a single partner re-derives 'Other' so Σ(partners)+Other == position.
        if pid and budget_positions.is_partner_driven(line_code):
            _recompute_other(
                session, statement=statement, line_code=line_code, entity_prefix=ep,
                fiscal_year=fiscal_year, level_4=l4, updated_by=updated_by,
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return {"rows_upserted": n}


def _write_distributed_l4(
    session: Session,
    *,
    statement: str,
    line_code: str,
    entity_prefix: str,
    partner_kind: Optional[str],
    fiscal_year: int,
    parent_annual: float,
    profiles: dict[str, tuple[float, dict[int, float]]],
    updated_by: Optional[str],
) -> dict[str, Any]:
    """Top-down L3 → L4 write: split ``parent_annual`` across the L4 children by
    historical share, seasonalize each L4 by its OWN weights, and persist as L4 rows.

    The XOR guard clears the L3-level ('') row for the scope first, so the reader's
    per-line_code SUM = Σ(L4) == parent_annual exactly once (no double count).
    ``Σ L4 == parent_annual`` to 1e-6 by :func:`distribute_to_l4`.  Commits.
    """
    l4_weights = {l4: prof[0] for l4, prof in profiles.items()}  # share = historical annual
    l4_annuals = distribute_to_l4(parent_annual, l4_weights)
    try:
        # XOR: writing the L4 set drops the L3-level '' row for the scope (all partners).
        _clear_complementary_level(
            session, statement=statement, line_code=line_code, entity_prefix=entity_prefix,
            fiscal_year=fiscal_year, writing_level4="nonempty", partner_id=None,
        )
        rows: list[dict[str, Any]] = []
        for l4, a in l4_annuals.items():
            _annual, seas_weights = profiles[l4]
            months_presented = seasonalize(a, seas_weights)
            rows += _cell_rows(
                statement=statement, line_code=line_code, entity_prefix=entity_prefix,
                partner_id="", partner_kind=partner_kind, fiscal_year=fiscal_year,
                months_presented=months_presented, level_4=l4,
            )
        n = _upsert_rows(session, rows, updated_by=updated_by, is_synthetic=False)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return {"rows_upserted": n}


def _recompute_other(
    session: Session,
    *,
    statement: str,
    line_code: str,
    entity_prefix: str,
    fiscal_year: int,
    level_4: str = "",
    updated_by: Optional[str],
) -> None:
    """Re-derive the 'Other' partner row (partner_id='__OTHER__') so that
    Σ(named partners) + Other == position, per fiscal_period (stored sign).

    Position total = the persisted position-level row (partner_id=''); if none
    exists yet, Other is left as-is (no position to reconcile to).  Scoped to a
    single ``level_4`` so an L3 ('') reconciliation never mixes with L4 rows (the
    XOR guard already ensures only ONE level exists for the scope).  Does NOT commit.
    """
    rows = session.execute(
        text("""
            SELECT partner_id, fiscal_period, SUM(amount) AS amt
            FROM fact_position_plan
            WHERE statement = :stmt AND scenario = 'budget'
              AND line_code = :lc AND entity_prefix = :ep AND fiscal_year = :fy
              AND level_4 = :l4
              AND fiscal_period BETWEEN 1 AND 12
            GROUP BY partner_id, fiscal_period
        """),
        {"stmt": statement, "lc": line_code, "ep": entity_prefix, "fy": fiscal_year,
         "l4": level_4 or ""},
    ).fetchall()

    pos_stored: dict[int, float] = {p: 0.0 for p in PERIODS}
    named_stored: dict[int, float] = {p: 0.0 for p in PERIODS}
    has_position = False
    for r in rows:
        pid, period, amt = str(r[0]), int(r[1]), float(r[2] or 0.0)
        if period < 1 or period > 12:
            continue
        if pid == "":
            pos_stored[period] += amt
            has_position = True
        elif pid != OTHER_PARTNER_ID:
            named_stored[period] += amt
    if not has_position:
        return
    partner_kind = budget_positions.partner_kind_for(line_code)
    other_rows = [
        {
            "statement": statement, "line_code": line_code,
            "entity_prefix": entity_prefix, "partner_id": OTHER_PARTNER_ID,
            "level_4": level_4 or "",
            "partner_kind": partner_kind, "fiscal_year": fiscal_year,
            "fiscal_period": p, "amount": pos_stored[p] - named_stored[p],
        }
        for p in PERIODS
    ]
    _upsert_rows(session, other_rows, updated_by=updated_by, is_synthetic=False)


def patch_position(
    session: Session,
    *,
    statement: str,
    line_code: str,
    entity: str,
    fiscal_year: int,
    position: Optional[dict[str, Any]] = None,
    partners: Optional[list[dict[str, Any]]] = None,
    weights: Optional[dict[int, float]] = None,
    level_4: str = "",
    updated_by: Optional[str] = None,
) -> dict[str, Any]:
    """Bulk-save a position total + its partners in ONE transaction, with roll-up.

    ``position`` and each partner dict carry either ``months[12]`` or ``annual``,
    and OPTIONALLY ``input_mode`` (+ ``growth_value`` / ``base``) to resolve growth
    against a prior-year base via :func:`resolve_input` before the write (storage
    stays absolute; omitting ``input_mode`` keeps the legacy months/annual path).
    The 'Other' remainder is recomputed so Σ(partners)+Other == position.  Commits.

    ``level_4`` (default '') is the L4 sub-position key for the position cell.  The
    XOR guard clears the complementary level for the scope first so the per-line_code
    reader SUM never double-counts an L3 row together with its L4 rows.
    """
    ep = (entity or "")[:2]
    l4 = (level_4 or "")
    partner_kind = budget_positions.partner_kind_for(line_code)
    try:
        # XOR: clear the complementary level for the whole position scope (all
        # partners) before writing.  patch_position writes the position-level '' and
        # named partners together as ONE level, so the guard is scoped to the line.
        _clear_complementary_level(
            session, statement=statement, line_code=line_code, entity_prefix=ep,
            fiscal_year=fiscal_year, writing_level4=l4, partner_id=None,
        )
        rows: list[dict[str, Any]] = []
        if position is not None:
            pm = _resolve_months_presented(
                months=position.get("months"), annual=position.get("annual"),
                weights=weights, input_mode=position.get("input_mode"),
                growth_value=position.get("growth_value"), base=position.get("base"),
            )
            rows += _cell_rows(
                statement=statement, line_code=line_code, entity_prefix=ep,
                partner_id="", partner_kind=None, fiscal_year=fiscal_year,
                months_presented=pm, level_4=l4,
            )
        for pr in partners or []:
            partner_id = str(pr["partner_id"])
            # The reserved 'Other' sentinel is derived server-side; reject direct writes.
            if partner_id == OTHER_PARTNER_ID:
                raise ValueError(
                    f"partner_id '{OTHER_PARTNER_ID}' is reserved and cannot be written directly"
                )
            pm = _resolve_months_presented(
                months=pr.get("months"), annual=pr.get("annual"), weights=weights,
                input_mode=pr.get("input_mode"), growth_value=pr.get("growth_value"),
                base=pr.get("base"),
            )
            rows += _cell_rows(
                statement=statement, line_code=line_code, entity_prefix=ep,
                partner_id=partner_id, partner_kind=partner_kind,
                fiscal_year=fiscal_year, months_presented=pm, level_4=l4,
            )
        n = _upsert_rows(session, rows, updated_by=updated_by, is_synthetic=False)
        if budget_positions.is_partner_driven(line_code):
            _recompute_other(
                session, statement=statement, line_code=line_code, entity_prefix=ep,
                fiscal_year=fiscal_year, level_4=l4, updated_by=updated_by,
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return {"rows_upserted": n}


def delete_budget(
    session: Session,
    *,
    statement: str,
    fiscal_year: int,
    entity: Optional[str] = None,
) -> dict[str, int]:
    """Drop budget rows for the scope → readers revert to forecast/plan.  Commits.

    When ``entity`` is None all entity scopes for (statement, fiscal_year) are
    dropped; otherwise only that entity_prefix.
    """
    params: dict[str, Any] = {"stmt": statement, "fy": fiscal_year}
    ent_clause = ""
    if entity is not None and entity != "":
        params["ep"] = str(entity)[:2]
        ent_clause = "AND entity_prefix = :ep"
    elif entity == "":
        ent_clause = "AND entity_prefix = ''"
    try:
        res = session.execute(
            text(f"""
                DELETE FROM fact_position_plan
                WHERE statement = :stmt AND scenario = 'budget' AND fiscal_year = :fy
                {ent_clause}
            """),
            params,
        )
        session.commit()
        return {"deleted": int(res.rowcount or 0)}
    except Exception:
        session.rollback()
        raise


def seed_budget(
    session: Session,
    *,
    statement: str,
    fiscal_year: int,
    entity: Optional[str] = None,
    top_n: int = 20,
    updated_by: Optional[str] = None,
    materialize_suggestion: bool = False,
    heuristic: str = "prior_year",
    growth_pct: float = 0.0,
) -> dict[str, int]:
    """Materialise the grid seed into fact_position_plan (is_synthetic=TRUE),
    idempotent (UPSERT).  Persists the same numbers the GET grid shows.  Commits.

    This is the "Apply" path of the Finssentials heuristics.  By default it
    materialises the legacy synthetic seed (the ``months`` of each grid position —
    byte-identical to the prior behaviour).  When ``materialize_suggestion`` is True
    it instead writes each position's ``suggestion`` for the chosen ``heuristic`` /
    ``growth_pct`` (so "Apply" lands the proposed prior-year / CAGR / run-rate
    numbers, which the user can then adjust per position).  Partner rows are only
    seeded in the legacy synthetic mode (the suggestion is a position-level value).
    The XOR write path is honoured implicitly: seeding writes only the L3-level
    ('') row, and any later L4 edit XOR-clears it.

    SKIP-ZERO (materialize_suggestion only): positions whose suggestion resolves to
    all-zero months (:func:`_all_zero_months`) are NOT written.  Because the plan
    reader resolution (budget→forecast→plan) selects the budget map for EVERY
    line_code once any budget signal exists, persisting a 0 suggestion would
    override that position's forecast/plan value with 0 in the IS/BS plan columns.
    Skipping zero suggestions lets un-suggested positions keep falling through to
    forecast/plan, while genuinely budgeted positions still flow into reporting.
    This does NOT change the legacy synthetic seed (materialize_suggestion=False),
    nor the manual upsert_cell/patch_position paths — a user-entered 0 is a real
    budget and still persists; only the AUTO-materialize of a zero suggestion skips.
    """
    ep = (entity or "")[:2]
    grid = build_grid(
        session, statement=statement, fiscal_year=fiscal_year,
        entity_prefix=(ep or None), top_n=top_n,
        heuristic=heuristic, growth_pct=growth_pct, level="L3",
    )
    try:
        rows: list[dict[str, Any]] = []
        for pos in grid["positions"]:
            line_code = pos["line_code"]
            source = pos["suggestion"]["months"] if materialize_suggestion else pos["months"]
            months_presented = {p: source[p - 1] for p in PERIODS}
            # SKIP-ZERO (materialize_suggestion only): a position whose heuristic
            # suggestion resolves to all-zero months is NOT written.  The plan reader
            # resolution (budget→forecast→plan) selects the budget map for ALL
            # line_codes once ANY budget signal exists, so persisting a 0 here would
            # override the position's forecast/plan value with 0 in the IS/BS plan
            # columns.  Un-suggested positions must keep falling through to
            # forecast/plan, so we write rows ONLY for non-zero suggestions.  The
            # legacy synthetic seed (materialize_suggestion=False) is unaffected —
            # it writes every position exactly as before.  A user-entered 0 via
            # upsert_cell/patch_position is a real budget and still persists; only
            # the AUTO-materialize of a zero SUGGESTION is skipped.
            if materialize_suggestion and _all_zero_months(months_presented):
                continue
            rows += _cell_rows(
                statement=statement, line_code=line_code, entity_prefix=ep,
                partner_id="", partner_kind=None, fiscal_year=fiscal_year,
                months_presented=months_presented,
            )
            # In suggestion mode we write the position-level value only (the
            # suggestion is not partner-allocated); the legacy synthetic seed also
            # snapshots the partner roll-up so its Σ(partners)+Other == position.
            partner_kind = budget_positions.partner_kind_for(line_code)
            seed_partners = pos.get("partners", []) if not materialize_suggestion else []
            for pr in seed_partners:
                p_months = {p: pr["months"][p - 1] for p in PERIODS}
                rows += _cell_rows(
                    statement=statement, line_code=line_code, entity_prefix=ep,
                    partner_id=str(pr["partner_id"]), partner_kind=partner_kind,
                    fiscal_year=fiscal_year, months_presented=p_months,
                )
            if seed_partners:
                # Persist the 'Other' remainder as a synthetic partner row too.
                other_annual = float(pos.get("other") or 0.0)
                # Seasonalize 'Other' uniformly (no own profile) for the seed snapshot.
                o_months = seasonalize(other_annual, None)
                rows += _cell_rows(
                    statement=statement, line_code=line_code, entity_prefix=ep,
                    partner_id=OTHER_PARTNER_ID, partner_kind=partner_kind,
                    fiscal_year=fiscal_year, months_presented=o_months,
                )
        n = _upsert_rows(session, rows, updated_by=updated_by, is_synthetic=True)
        session.commit()
        return {"rows_seeded": n}
    except Exception:
        session.rollback()
        raise
