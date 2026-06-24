"""etl/plan_synth.py — Deterministic synthetic plan / forecast generator (DF4, P3).

PURE functions only — operate on pandas DataFrames, NO DB, NO randomness
(no ``random``, no unseeded anything).  Re-running with identical input yields
byte-identical output.  Output rows are flagged ``is_synthetic=True`` and are
meant for ``fact_gl_plan`` (account granularity) and ``fact_sales_plan``
(customer granularity).  DF5 wires these into the DB endpoint.

Sign convention (FIXED — never change, Hard-Rule #1)
----------------------------------------------------
``amount`` is signed: **+ = debit (Soll), − = credit (Haben)**.  Plan ``amount``
follows the SAME convention as actuals.  Because every formula below is a
*multiplication* of the signed actual movement by non-negative weights/growth
factors, the sign is preserved (a credit revenue movement of −3000 stays
negative through growth → −3150).  ``gross_sales_plan`` is positive, mirroring
``fact_sales.gross_sales = −amount`` on revenue lines.

==================================================================== FORMULAS
Notation (per account_number_group ``a``, or per customer ``c``):
  actual(a, fy, p)  signed actual movement of ``a`` in fiscal_year ``fy``,
                    fiscal_period ``p`` (p ∈ 1..12).
  B                 base fiscal year (the last full FY of actuals).

(1) Annual base
      Annual(a) = Σ_p actual(a, B, p)                       (signed; preserves sign)

(2) Seasonal index (monthly profile from a base FY ``fy0``)
      S(a, p) = actual(a, fy0, p) / Σ_p actual(a, fy0, p)
      • Defined only when the denominator ≠ 0.  By construction Σ_p S(a, p) = 1.
      • Fallback (account has no monthly spread in fy0, i.e. annual == 0, or the
        account is absent from fy0): S(a, p) = 1/12 for every p  (UNIFORM).
      • The index can carry negative weights if a period's movement opposes the
        annual sign (e.g. a credit note); that is intentional and still sums to 1.

(3) Plan years  (scenario = 'plan')   — for future FY ``n`` ∈ {B+2 … B+1+H}
      k(n)            = n − FIRST_PLAN_FY + 1        with FIRST_PLAN_FY = B + 2
                        ⇒ FY(B+2) → k=1, FY(B+3) → k=2, …
      g(a)            = group_growth[group(a)]  if provided for a's P&L group,
                        else growth_rate                       (the per-run default)
      PlanAnnual(a,n) = Annual(a) × (1 + g(a))^k(n)
      plan(a, n, p)   = PlanAnnual(a, n) × S_base(a, p)
                        where S_base is the seasonal index of base FY B.
      ⇒ Σ_p plan(a, n, p) = PlanAnnual(a, n)   (the split reconciles to the annual).

    Why k starts at 1 for FY(B+2): FY(B+1) is the *forecast* year (FY25F), built
    from current-year run-rate (formula 4), NOT from the compounding chain.  The
    plan horizon begins at the first FULL future year FY(B+2)=FY26P and grows one
    step per year: FY26P = Annual × (1+g)^1, FY27P = Annual × (1+g)^2, etc.

(4) Forecast of OPEN periods  (scenario = 'forecast')  — current FY ``C``,
    last closed period ``L`` (periods 1..L are actuals; L+1..12 are open):
      S_prior(a, p)   = seasonal index of the PRIOR FY (C−1)        (formula 2)
      YTD(a)          = Σ_{p≤L} actual(a, C, p)                     (signed, closed periods)
      coverage(a)     = Σ_{p≤L} S_prior(a, p)                       (prior-year share of closed periods)
      ProjAnnual(a)   = YTD(a) / coverage(a)        if coverage(a) ≠ 0
                        else YTD(a) × 12 / L         (uniform fallback: pro-rata run-rate)
      forecast(a, p)  = ProjAnnual(a) × S_prior(a, p)   for OPEN periods p > L
      ⇒ closed periods are NEVER emitted (they are actuals); only p > L are filled.
      ⇒ Σ_{p>L} forecast(a,p) + YTD(a) = ProjAnnual(a)  (full-year run-rate projection).

==================================================================== WORKED EXAMPLE
Fixture (etl/tests/fixtures.py), entity '01', revenue account_number_group
``0180000`` (gl 80000), base FY B = 2024:
  Booking 1 revenue −1000  +  Booking 3 revenue −2000   ⇒ Annual = −3000  (credit).
  (gross_sales = +3000.)

Plan, growth_rate g = 0.05:
  FIRST_PLAN_FY = B+2 = 2026.  FY26P ⇒ k = 2026 − 2026 + 1 = 1.
  PlanAnnual(0180000, 2026) = −3000 × (1.05)^1 = **−3150.0**   (sign preserved: credit).
  In gross-sales terms that is +3150 — i.e. revenue grown 5 %.
  FY27P ⇒ k=2 ⇒ −3000 × 1.05² = −3307.5 ; FY28P k=3 ⇒ −3472.875 ; FY29P k=4 ⇒ −3646.51875.

Period split of FY26P (seasonal index of base FY 2024 for account 0180000):
  In 2024 the revenue movements fall in the periods carried by the fixture.  If the
  fixture spreads −1000 in period 1 and −2000 in period 2 (and 0 elsewhere), then
  S(p1) = −1000/−3000 = 1/3 , S(p2) = −2000/−3000 = 2/3 , S(else) = 0.
  ⇒ FY26P period split: p1 = −3150 × 1/3 = −1050 ;  p2 = −3150 × 2/3 = −2100 ;
     all other periods 0.  Sum = −3150 = PlanAnnual ✓  (reconciles).
  If instead the base FY booked the whole −3000 in a single period (no spread) the
  seasonal index degenerates to that one period (weight 1) — still sums to the annual.

Sales plan (customer ``01100``), base FY 2024 gross_sales = 1000 + 2000 = 3000:
  FY26P gross_sales_plan annual = 3000 × 1.05 = **3150.0** (positive), split by the
  customer's base-FY seasonal profile, summing to 3150.

==================================================================== EDGE CASES
  • No prior-year actuals (forecast): coverage(a)=0 ⇒ uniform fallback
    ProjAnnual = YTD × 12 / L (pro-rata run-rate); never divides by zero.
  • No base-FY monthly spread / annual == 0 (plan): seasonal index falls back to
    uniform 1/12; PlanAnnual = 0 × (1+g)^k = 0 ⇒ all-zero plan, no div-by-zero.
  • Zero base movement: Annual(a)=0 ⇒ plan rows are 0 for every period (still emitted
    so the account exists in the plan), no NaN, no div-by-zero.
  • Brand-new account / customer absent from the base FY: NOT invented — such an
    entity has no base movement, so it simply does not appear in the plan output
    (excluded).  Documented: synthesis only extrapolates entities present in base.
  • Partial / stub base FY (only some periods booked): the annual is the sum of the
    booked periods; the seasonal index distributes over exactly those periods (others
    weight 0).  Plan/forecast therefore inherits the stub shape deterministically.
  • Sign flip within a year (e.g. a credit note making one period oppose the annual
    sign): the seasonal weight for that period is negative; the split still sums to
    the annual and the overall sign is preserved.
  • Determinism: groupby/sort use a fixed key order; output rows are sorted by
    (account_number_group/customer_id, fiscal_year, fiscal_period, scenario).  No RNG.
"""
from __future__ import annotations

import pandas as pd

# Fiscal periods 1..12 (period 13 = consolidation in the schema; plan is monthly only).
PERIODS: tuple[int, ...] = tuple(range(1, 13))
N_PERIODS = len(PERIODS)
UNIFORM_WEIGHT = 1.0 / N_PERIODS

SCENARIO_FORECAST = "forecast"
SCENARIO_PLAN = "plan"
SCENARIO_BUDGET = "budget"  # manual-override scenario (Phase 3+); declared here as the
                           # canonical literal so later phases never re-spell it.


# --------------------------------------------------------------------------- #
# (2) Seasonal index
# --------------------------------------------------------------------------- #
def seasonal_index(
    actuals: pd.DataFrame,
    base_fy: int,
    *,
    key: str = "account_number_group",
    value_col: str = "amount",
) -> dict[str, dict[int, float]]:
    """Monthly seasonal profile per ``key`` from a base FY.

    For each entity (account_number_group or customer_id) the weights of its base-FY
    periods are ``actual(p) / Σ_p actual(p)``, summing to 1.  Entities whose base-FY
    annual is 0 (no spread) fall back to a UNIFORM 1/12 profile.

    Parameters
    ----------
    actuals : DataFrame with columns [key, 'fiscal_year', 'fiscal_period', 'amount'].
    base_fy : the fiscal year whose monthly profile defines the seasonality.
    key     : grouping column ('account_number_group' for GL, 'customer_id' for sales).

    Returns
    -------
    dict[entity -> dict[period(1..12) -> weight]]
        Every returned profile covers all 12 periods (missing periods = weight 0,
        except the uniform fallback which assigns 1/12 to all 12).
    """
    base = actuals[actuals["fiscal_year"] == base_fy]
    out: dict[str, dict[int, float]] = {}
    if base.empty:
        return out

    # Deterministic order: sort the unique keys.
    grouped = base.groupby(key, sort=True)[value_col].agg(
        annual="sum",
    )
    period_sums = (
        base.groupby([key, "fiscal_period"], sort=True)[value_col].sum()
    )

    for ent in grouped.index:
        annual = float(grouped.loc[ent, "annual"])
        profile = {p: 0.0 for p in PERIODS}
        if annual == 0.0:
            # No monthly spread → uniform fallback.
            out[str(ent)] = {p: UNIFORM_WEIGHT for p in PERIODS}
            continue
        try:
            ent_periods = period_sums.loc[ent]
        except KeyError:  # pragma: no cover - defensive
            ent_periods = pd.Series(dtype="float64")
        for p in PERIODS:
            if p in ent_periods.index:
                profile[p] = float(ent_periods.loc[p]) / annual
        out[str(ent)] = profile
    return out


def _annual_by_key(actuals: pd.DataFrame, fy: int, key: str, value_col: str = "amount") -> dict[str, float]:
    """Signed annual movement per ``key`` for fiscal year ``fy`` (deterministic order)."""
    sub = actuals[actuals["fiscal_year"] == fy]
    if sub.empty:
        return {}
    agg = sub.groupby(key, sort=True)[value_col].sum()
    return {str(k): float(v) for k, v in agg.items()}


# --------------------------------------------------------------------------- #
# (4) Forecast of OPEN periods of the current FY
# --------------------------------------------------------------------------- #
def forecast_open_periods(
    actuals: pd.DataFrame,
    current_fy: int,
    last_closed_period: int,
    prior_fy: int,
    growth_rate: float = 0.0,
    *,
    key: str = "account_number_group",
    value_col: str = "amount",
    source_system: str | None = "synthetic_plan",
) -> pd.DataFrame:
    """FY25F: forecast only the OPEN periods (> last_closed_period) of ``current_fy``.

    YTD run-rate of the closed periods is projected over the remaining months using
    the PRIOR fiscal year's seasonal index (formula 4).  ``growth_rate`` is applied
    on top of the projected annual (default 0.0 — pure run-rate, no growth, which is
    the conservative forecast).  Closed periods are never emitted.

    Returns a DataFrame with columns
    [key, 'fiscal_year', 'fiscal_period', 'scenario', value_col, 'is_synthetic',
     'source_system'] — ready for fact_gl_plan (value_col='amount') or
    fact_sales_plan (value_col='gross_sales_plan').
    """
    if last_closed_period >= N_PERIODS:
        # Nothing open — full year already closed.
        return _empty_frame(key, value_col)

    prior_profile = seasonal_index(actuals, prior_fy, key=key, value_col=value_col)
    ytd = _ytd_by_key(actuals, current_fy, last_closed_period, key, value_col)

    open_periods = [p for p in PERIODS if p > last_closed_period]
    rows: list[dict] = []
    growth = 1.0 + float(growth_rate)

    for ent in sorted(ytd.keys()):
        ytd_val = ytd[ent]
        profile = prior_profile.get(ent)
        if profile is None:
            # No prior-year seasonality → uniform.
            profile = {p: UNIFORM_WEIGHT for p in PERIODS}
        coverage = sum(profile[p] for p in PERIODS if p <= last_closed_period)
        if coverage != 0.0:
            proj_annual = ytd_val / coverage
        else:
            # Uniform fallback: pro-rata run-rate over closed months.
            proj_annual = ytd_val * N_PERIODS / last_closed_period if last_closed_period else 0.0
        proj_annual *= growth
        for p in open_periods:
            rows.append(
                _row(ent, current_fy, p, SCENARIO_FORECAST, proj_annual * profile[p],
                     key, value_col, source_system)
            )
    return _finalize(rows, key, value_col)


def _ytd_by_key(
    actuals: pd.DataFrame, fy: int, last_closed_period: int, key: str, value_col: str
) -> dict[str, float]:
    """Σ of actual movement over closed periods (1..last_closed_period) of ``fy``."""
    sub = actuals[
        (actuals["fiscal_year"] == fy)
        & (actuals["fiscal_period"] <= last_closed_period)
    ]
    if sub.empty:
        return {}
    agg = sub.groupby(key, sort=True)[value_col].sum()
    return {str(k): float(v) for k, v in agg.items()}


# --------------------------------------------------------------------------- #
# (3) Plan years
# --------------------------------------------------------------------------- #
def plan_years(
    actuals: pd.DataFrame,
    base_fy: int,
    horizon_years: int,
    growth_rate: float,
    group_growth: dict[str, float] | None = None,
    *,
    key: str = "account_number_group",
    value_col: str = "amount",
    group_col: str | None = None,
    source_system: str | None = "synthetic_plan",
) -> pd.DataFrame:
    """Plan scenario for the ``horizon_years`` full future years after the forecast year.

    The first plan year is ``base_fy + 2`` (FY26P when base_fy=2024); FY(base_fy+1)
    is the forecast year (handled by ``forecast_open_periods``).  Each plan year n
    grows the base annual by ``(1+g)^k`` with k = n − (base_fy+2) + 1 (so FY26P → 1),
    then distributes it over periods by the base-FY seasonal index (formula 3).

    ``growth_rate`` is the per-run default.  ``group_growth`` optionally overrides the
    rate for accounts whose ``group_col`` value matches a key in the dict (e.g. a P&L
    group label such as a level_2 value).  Requires ``group_col`` to be present in
    ``actuals`` for the override to take effect; otherwise the default is used.

    horizon_years : number of plan years to emit (e.g. 4 → FY26P..FY29P).
    """
    first_plan_fy = base_fy + 2
    base_annual = _annual_by_key(actuals, base_fy, key, value_col)
    profile_by_ent = seasonal_index(actuals, base_fy, key=key, value_col=value_col)
    group_of = _group_lookup(actuals, base_fy, key, group_col) if group_col else {}
    group_growth = group_growth or {}

    rows: list[dict] = []
    for ent in sorted(base_annual.keys()):
        annual = base_annual[ent]
        g = float(growth_rate)
        grp = group_of.get(ent)
        if grp is not None and grp in group_growth:
            g = float(group_growth[grp])
        profile = profile_by_ent.get(ent, {p: UNIFORM_WEIGHT for p in PERIODS})
        for i in range(horizon_years):
            n = first_plan_fy + i
            k = i + 1  # FY(base+2) -> k=1
            plan_annual = annual * (1.0 + g) ** k
            for p in PERIODS:
                rows.append(
                    _row(ent, n, p, SCENARIO_PLAN, plan_annual * profile[p],
                         key, value_col, source_system)
                )
    return _finalize(rows, key, value_col)


def _group_lookup(actuals: pd.DataFrame, fy: int, key: str, group_col: str) -> dict[str, str]:
    """Map each entity to its (single) P&L group label from the base FY rows.

    If an entity carries more than one group label, the lexicographically smallest is
    chosen (deterministic).  Entities without a group label are omitted.
    """
    if group_col not in actuals.columns:
        return {}
    sub = actuals[actuals["fiscal_year"] == fy][[key, group_col]].dropna()
    if sub.empty:
        return {}
    out: dict[str, str] = {}
    for ent, grp in sub.groupby(key, sort=True)[group_col]:
        labels = sorted({str(v) for v in grp.tolist()})
        if labels:
            out[str(ent)] = labels[0]
    return out


# --------------------------------------------------------------------------- #
# Sales plan (customer granularity)
# --------------------------------------------------------------------------- #
def sales_plan(
    fact_sales_actuals: pd.DataFrame,
    base_fy: int,
    horizon: int,
    growth_rate: float,
    group_growth: dict[str, float] | None = None,
    *,
    current_fy: int | None = None,
    last_closed_period: int | None = None,
    prior_fy: int | None = None,
    forecast_growth_rate: float = 0.0,
) -> pd.DataFrame:
    """fact_sales_plan rows by ``customer_id`` (analogous to GL plan).

    Operates on the positive ``gross_sales`` measure (sign convention: gross_sales is
    already +; plan stays +).  Emits plan years FY(base+2)..FY(base+1+horizon) and,
    if ``current_fy``/``last_closed_period``/``prior_fy`` are supplied, the forecast
    year FY(base+1) for open periods.

    Input columns: ['customer_id', 'fiscal_year', 'fiscal_period', 'gross_sales'].
    Output columns: ['customer_id', 'fiscal_year', 'fiscal_period', 'scenario',
                     'gross_sales_plan', 'is_synthetic', 'source_system'].
    """
    plan = plan_years(
        fact_sales_actuals,
        base_fy=base_fy,
        horizon_years=horizon,
        growth_rate=growth_rate,
        group_growth=group_growth,
        key="customer_id",
        value_col="gross_sales",
        source_system="synthetic_plan",
    )
    frames = [plan]
    if current_fy is not None and last_closed_period is not None and prior_fy is not None:
        fc = forecast_open_periods(
            fact_sales_actuals,
            current_fy=current_fy,
            last_closed_period=last_closed_period,
            prior_fy=prior_fy,
            growth_rate=forecast_growth_rate,
            key="customer_id",
            value_col="gross_sales",
            source_system="synthetic_plan",
        )
        frames.append(fc)
    combined = pd.concat([f for f in frames if not f.empty], ignore_index=True) if any(
        not f.empty for f in frames
    ) else _empty_frame("customer_id", "gross_sales")
    return combined.rename(columns={"gross_sales": "gross_sales_plan"})


# --------------------------------------------------------------------------- #
# Com plan (supplier granularity) — symmetric sibling of sales_plan
# --------------------------------------------------------------------------- #
def com_plan(
    fact_com_actuals: pd.DataFrame,
    base_fy: int,
    horizon: int,
    growth_rate: float,
    group_growth: dict[str, float] | None = None,
    *,
    current_fy: int | None = None,
    last_closed_period: int | None = None,
    prior_fy: int | None = None,
    forecast_growth_rate: float = 0.0,
) -> pd.DataFrame:
    """fact_com_plan rows by ``supplier_id`` (mirror of :func:`sales_plan`).

    Operates on the positive ``cost_of_materials`` measure (sign convention:
    ``fact_com.cost_of_materials = amount`` — material is a debit, so it is already
    positive; the plan stays a positive magnitude).  Because every formula is a
    multiplication of the actual movement by non-negative growth/seasonal weights,
    the positive sign is preserved exactly as ``gross_sales_plan`` stays positive.

    Emits plan years FY(base+2)..FY(base+1+horizon) and, if ``current_fy`` /
    ``last_closed_period`` / ``prior_fy`` are supplied, the forecast year
    FY(base+1) for open periods.  Reuses ``plan_years`` / ``forecast_open_periods``
    /  ``seasonal_index`` exactly as the sales path does — pure, no DB, no RNG.

    Input columns:  ['supplier_id', 'fiscal_year', 'fiscal_period', 'cost_of_materials'].
    Output columns: ['supplier_id', 'fiscal_year', 'fiscal_period', 'scenario',
                     'cost_of_materials_plan', 'is_synthetic'].
    """
    plan = plan_years(
        fact_com_actuals,
        base_fy=base_fy,
        horizon_years=horizon,
        growth_rate=growth_rate,
        group_growth=group_growth,
        key="supplier_id",
        value_col="cost_of_materials",
        source_system="synthetic_plan",
    )
    frames = [plan]
    if current_fy is not None and last_closed_period is not None and prior_fy is not None:
        fc = forecast_open_periods(
            fact_com_actuals,
            current_fy=current_fy,
            last_closed_period=last_closed_period,
            prior_fy=prior_fy,
            growth_rate=forecast_growth_rate,
            key="supplier_id",
            value_col="cost_of_materials",
            source_system="synthetic_plan",
        )
        frames.append(fc)
    combined = pd.concat([f for f in frames if not f.empty], ignore_index=True) if any(
        not f.empty for f in frames
    ) else _empty_frame("supplier_id", "cost_of_materials")
    return combined.rename(columns={"cost_of_materials": "cost_of_materials_plan"})


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def generate_plan(
    gl_actuals: pd.DataFrame,
    sales_actuals: pd.DataFrame,
    com_actuals: pd.DataFrame | None = None,
    *,
    base_fy: int,
    current_fy: int,
    last_closed_period: int,
    horizon_years: int = 4,
    growth_rate: float = 0.05,
    group_growth: dict[str, float] | None = None,
    group_col: str | None = None,
    forecast_growth_rate: float = 0.0,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Produce (fact_gl_plan_rows, fact_sales_plan_rows, fact_com_plan_rows).

    All three DataFrames carry ``is_synthetic=True``.  Horizon = forecast year
    FY(current_fy) open periods + ``horizon_years`` plan years from FY(base_fy+2).

    ``com_actuals`` (supplier_id × fiscal_period × cost_of_materials) is optional and
    additive — when omitted (or empty) the third returned frame is an empty
    fact_com_plan frame, and the GL/sales outputs are **unchanged**.

    Defaults: ``growth_rate=0.05`` (overridable, §5.3 — NOT hardcoded into the
    formula, a caller-supplied default), ``horizon_years=4`` → FY26P..FY29P.
    """
    prior_fy = current_fy - 1

    gl_forecast = forecast_open_periods(
        gl_actuals, current_fy=current_fy, last_closed_period=last_closed_period,
        prior_fy=prior_fy, growth_rate=forecast_growth_rate,
        key="account_number_group", value_col="amount",
    )
    gl_plan = plan_years(
        gl_actuals, base_fy=base_fy, horizon_years=horizon_years,
        growth_rate=growth_rate, group_growth=group_growth,
        key="account_number_group", value_col="amount", group_col=group_col,
    )
    gl_frames = [f for f in (gl_forecast, gl_plan) if not f.empty]
    gl_out = (
        pd.concat(gl_frames, ignore_index=True)
        if gl_frames else _empty_frame("account_number_group", "amount")
    )

    sales_out = sales_plan(
        sales_actuals, base_fy=base_fy, horizon=horizon_years, growth_rate=growth_rate,
        group_growth=group_growth, current_fy=current_fy,
        last_closed_period=last_closed_period, prior_fy=prior_fy,
        forecast_growth_rate=forecast_growth_rate,
    )

    if com_actuals is not None and not com_actuals.empty:
        com_out = com_plan(
            com_actuals, base_fy=base_fy, horizon=horizon_years, growth_rate=growth_rate,
            group_growth=group_growth, current_fy=current_fy,
            last_closed_period=last_closed_period, prior_fy=prior_fy,
            forecast_growth_rate=forecast_growth_rate,
        )
    else:
        com_out = _empty_frame("supplier_id", "cost_of_materials").rename(
            columns={"cost_of_materials": "cost_of_materials_plan"}
        )

    gl_out = _sort_output(gl_out, "account_number_group")
    sales_out = _sort_output(sales_out, "customer_id")
    com_out = _sort_output(com_out, "supplier_id")
    return gl_out, sales_out, com_out


# --------------------------------------------------------------------------- #
# Row / frame helpers (deterministic, no RNG)
# --------------------------------------------------------------------------- #
def _row(ent, fy, period, scenario, value, key, value_col, source_system) -> dict:
    return {
        key: str(ent),
        "fiscal_year": int(fy),
        "fiscal_period": int(period),
        "scenario": scenario,
        value_col: float(value),
        "is_synthetic": True,
        "source_system": source_system,
    }


def _columns(key: str, value_col: str) -> list[str]:
    return [key, "fiscal_year", "fiscal_period", "scenario", value_col,
            "is_synthetic", "source_system"]


def _empty_frame(key: str, value_col: str) -> pd.DataFrame:
    return pd.DataFrame(columns=_columns(key, value_col))


def _finalize(rows: list[dict], key: str, value_col: str) -> pd.DataFrame:
    if not rows:
        return _empty_frame(key, value_col)
    return _sort_output(pd.DataFrame(rows), key)


def _sort_output(df: pd.DataFrame, key: str) -> pd.DataFrame:
    if df.empty:
        return df
    sort_cols = [c for c in (key, "fiscal_year", "fiscal_period", "scenario") if c in df.columns]
    return df.sort_values(sort_cols, kind="mergesort").reset_index(drop=True)
