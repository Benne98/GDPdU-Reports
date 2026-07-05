"""Pure forecast calculators (DB-free) — the 'forecast' scenario band math.

Companion to :mod:`budget_service` (which owns the manual 'budget' band and the
single presented<->stored sign flip).  This module is 100% pure: every function
takes plain dicts / floats and returns plain dicts / floats, so each calculator is
golden-testable in isolation and carries a formula + worked example + edge cases
(``docs/financial-logic.md`` iron rule).

=================================================================== CONVENTIONS
All internal math is done in **PRESENTED / NATURAL** sign and only converted to the
stored GL sign ONCE, by the CALLER (the seeder), via
``budget_service.present_to_stored``.  Two presented conventions appear and MUST NOT
be mixed inside one calculator:

  * P&L / CF presented: income / inflow **+**, expense / outflow **-**
    (mirror of ``amount * -1``).  ``forecast_pl_seasonal`` and
    ``forecast_cf_indirect`` speak this convention.
  * BS **natural** magnitudes: assets **+**, liabilities & equity **+** (both
    positive book values).  ``forecast_bs_rollforward`` / ``forecast_wc_from_bs``
    speak this convention so the working-capital ratios (NWC = AR + Inv - AP) and
    the balancing plug (Cash = Σ L&E - Σ non-cash assets) are unambiguous.  The
    seeder maps each BS line's natural value to/from the stored GL sign using the
    line's asset/credit side (``dim_pl_structure.kpi_code`` 'BS:asset' / 'BS:credit').

=================================================================== ANCHOR (v2)
base_fy = 2024 (P1-12 booked); forecast fy = 2025; last closed period L = 7; the
open periods are P8-12.  Nothing here is hard-coded to those numbers — L and the
base/ytd maps are parameters — but the worked examples use them.

Full-precision float throughout; the CALLER rounds to 2 dp only on WRITE (after the
cash plug, so the balance ties still hold to 1e-6).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

_EPS = 1e-6
_PERIODS = tuple(range(1, 13))


# =========================================================================== #
# 1. P&L seasonal forecast
# =========================================================================== #
def forecast_pl_seasonal(
    base_2024_by_line_period: dict[str, dict[int, float]],
    ytd_2025_by_line_period: dict[str, dict[int, float]],
    L: int = 7,
    g: float = 0.0,
) -> dict[str, dict[int, float]]:
    """Forecast the OPEN periods (p > L) of every P&L line from its base-FY seasonal
    shape, returning ``{line_code: {period: presented_amount}}`` for p in ``L+1..12``.

    ================================================================== FORMULA
    For a line ``c`` with base-FY monthly presented values ``base(c,p)`` (p=1..12):

        annual_base(c)      = Σ_{p=1..12} base(c,p)
        seasonal_share(c,p) = base(c,p) / annual_base(c)          (Σ_p share == 1)
        plan(c,p)           = base(c,p) · (1 + g)   for p > L
                            = annual_base(c) · seasonal_share(c,p) · (1 + g)

    So with g = 0 the open-period plan is IDENTICAL to the base-FY monthly values —
    the 5 DISTINCT monthly figures for P8-12, NOT a flat run-rate constant.

    FALLBACK (never divide by zero): if ``annual_base(c) == 0`` OR the line is absent
    from the base map, the seasonal shape is unknown, so use the current-FY YTD
    run-rate as the annual level and a UNIFORM 1/12 share:

        run_rate(c)         = (Σ_{p<=L} ytd(c,p) / L) · 12
        plan(c,p)           = run_rate(c) · (1/12) · (1 + g)     for p > L

    Signs are preserved: a negative-signed line (expense presented '-') keeps its
    sign; a single sign-flip month (a month opposing the annual sign, e.g. a credit
    note) is carried because the share is taken on the SIGNED base (share may be
    negative for that month) and the reconstruction ``annual·share`` reproduces it.

    ================================================================== EXAMPLE
    base(c) = {1:100, 2:200, ... , 12:400}, annual_base = 3000, g = 0, L = 7.
      share(c,8) = base(c,8)/3000; plan(c,8) = base(c,8) = the base month-8 value.
      → plan for P8..12 = the 5 base monthly values verbatim (distinct).
    g = 0.10 → each plan month = base·1.10.
    Fallback: base absent, ytd P1-7 sum = 700, L = 7 → run_rate = 100·12 = 1200;
      plan(c,p) = 1200/12 = 100 for each of P8-12 (uniform).

    ================================================================== EDGES
      * annual_base == 0 or line absent → run-rate fallback (never div/0).
      * ytd absent too → run_rate 0 → plan 0 for that line (no noise).
      * L <= 0 → no ytd periods; run-rate 0 (guarded).
      * negative line / sign-flip month → sign preserved (signed share).
    """
    lines = set(base_2024_by_line_period) | set(ytd_2025_by_line_period)
    out: dict[str, dict[int, float]] = {}
    open_periods = [p for p in _PERIODS if p > L]
    for c in lines:
        base = base_2024_by_line_period.get(c) or {}
        ytd = ytd_2025_by_line_period.get(c) or {}
        annual_base = sum(float(base.get(p, 0.0)) for p in _PERIODS)
        if c in base_2024_by_line_period and abs(annual_base) >= _EPS:
            # Normal path: plan(c,p) = base(c,p) · (1+g).
            out[c] = {p: float(base.get(p, 0.0)) * (1.0 + g) for p in open_periods}
        else:
            # Fallback: YTD run-rate, uniform 1/12 share.
            ytd_sum = sum(float(ytd.get(p, 0.0)) for p in _PERIODS if p <= L)
            run_rate = (ytd_sum / L) * 12.0 if L and L > 0 else 0.0
            out[c] = {p: run_rate * (1.0 / 12.0) * (1.0 + g) for p in open_periods}
    return out


def fy_forecast_by_line(
    base_2024_by_line_period: dict[str, dict[int, float]],
    ytd_2025_by_line_period: dict[str, dict[int, float]],
    L: int = 7,
    g: float = 0.0,
) -> dict[str, float]:
    """Full-year forecast per P&L line: ``fy_f(c) = Σ_{p<=L} ytd(c,p) + Σ_{p>L} plan(c,p)``.

    Combines the BOOKED YTD actuals (p <= L) with the seasonal open-period plan
    (:func:`forecast_pl_seasonal`).  Presented sign preserved (income +, expense -).
    """
    plan = forecast_pl_seasonal(base_2024_by_line_period, ytd_2025_by_line_period, L=L, g=g)
    lines = set(base_2024_by_line_period) | set(ytd_2025_by_line_period)
    out: dict[str, float] = {}
    for c in lines:
        ytd = ytd_2025_by_line_period.get(c) or {}
        ytd_part = sum(float(ytd.get(p, 0.0)) for p in _PERIODS if p <= L)
        plan_part = sum(plan.get(c, {}).values())
        out[c] = ytd_part + plan_part
    return out


# =========================================================================== #
# 2. Balance-sheet roll-forward (P7 -> P12) with a balancing cash plug
# =========================================================================== #
def forecast_bs_rollforward(
    bs_p7_natural: dict[str, float],
    sides: dict[str, str],
    roles: dict[str, str],
    drivers: dict[str, float],
    pl_forecast: dict[str, float],
    days: float = 360.0,
) -> dict[str, Any]:
    """Roll every BS line forward from its P7 closing to a P12 closing, then emit a
    linear P8-12 PATH per line.  All values NATURAL (assets +, L&E +).

    ``bs_p7_natural`` : {line: natural P7 closing}.
    ``sides``         : {line: 'asset' | 'le'} (from dim_pl_structure kpi_code).
    ``roles``         : {'AR','INV','AP','RE','CASH': <line_key>} — the driver /
                        retained-earnings / cash lines; every OTHER line is carried
                        flat (X_P12 = X_P7): PPE, debt, share capital, other assets…
    ``drivers``       : {'DSO','DPO','DIO'} activity ratios (days).
    ``pl_forecast``   : {'revenue_f','COGS_f','NI_f'} (COGS_f a POSITIVE magnitude).

    ================================================================== FORMULA
        AR_P12   = DSO/days · revenue_f
        Inv_P12  = DIO/days · COGS_f
        AP_P12   = DPO/days · COGS_f
        RE_P12   = RE_P7 + NI_f
        X_P12    = X_P7                       (every other line — carried flat)
        CASH_P12 = Σ(L&E incl RE)_P12 − Σ(non-cash asset lines)_P12   (PLUG)
        ⇒ Σ assets_P12 == Σ (L&E)_P12   (Assets = Equity & liabilities, exactly)

    PATH (p = 8..12), linear from the P7 close to the P12 close, so every open month
    is non-zero and monotone toward the target:
        path(line, p) = X_P7 + (X_P12 − X_P7) · (p − L) / (12 − L)     (L = 7)

    ================================================================== EXAMPLE
    revenue_f = 3600, COGS_f = 1800, NI_f = 200, days = 360, DSO = 30, DIO = 60,
    DPO = 45.  AR_P12 = 30/360·3600 = 300; Inv_P12 = 60/360·1800 = 300;
    AP_P12 = 45/360·1800 = 225.  RE_P7 = 100 → RE_P12 = 300.  One flat asset PPE = 500.
      Σ non-cash assets_P12 = 300+300+500 = 1100; Σ L&E_P12 = 225 + 300 = 525.
      CASH_P12 = 525 − 1100 = −575 (plug; may be negative — surfaced, not clamped).
      Check Σ assets (incl cash) = 1100 + (−575) = 525 == Σ L&E. ✓

    ================================================================== EDGES
      * revenue_f == 0 / COGS_f == 0 → the driven line is 0 (no div; multiply only).
      * a role line missing from ``bs_p7`` → treated as P7 = 0.
      * negative cash plug → kept (a real funding gap signal), not clamped.
      * only p = 8..12 emitted; P7 is the actual close (not overwritten).
      * L derived as 12 − len(open) implicitly via the interp denominator (7).
    """
    L = 7
    ar = roles["AR"]; inv = roles["INV"]; ap = roles["AP"]; re = roles["RE"]; cash = roles["CASH"]
    p7 = {k: float(bs_p7_natural.get(k, 0.0)) for k in bs_p7_natural}
    for k in (ar, inv, ap, re, cash):
        p7.setdefault(k, float(bs_p7_natural.get(k, 0.0)))

    revenue_f = float(pl_forecast.get("revenue_f", 0.0))
    cogs_f = float(pl_forecast.get("COGS_f", 0.0))
    ni_f = float(pl_forecast.get("NI_f", 0.0))
    dso = float(drivers.get("DSO", 0.0))
    dpo = float(drivers.get("DPO", 0.0))
    dio = float(drivers.get("DIO", 0.0))

    closing: dict[str, float] = {}
    # Every line carried flat first (X_P12 = X_P7); the driven lines overwrite below.
    for line, val in p7.items():
        closing[line] = float(val)
    closing[ar] = (dso / days) * revenue_f
    closing[inv] = (dio / days) * cogs_f
    closing[ap] = (dpo / days) * cogs_f
    closing[re] = p7.get(re, 0.0) + ni_f

    # Cash plug so Assets == L&E exactly at P12.
    le_sum = sum(closing[k] for k in closing if sides.get(k) == "le")
    noncash_asset_sum = sum(
        closing[k] for k in closing if sides.get(k) == "asset" and k != cash
    )
    closing[cash] = le_sum - noncash_asset_sum

    # Linear P8-12 path per line.
    span = float(12 - L)
    path: dict[str, dict[int, float]] = {}
    for line in closing:
        start = p7.get(line, 0.0)
        end = closing[line]
        path[line] = {
            p: start + (end - start) * (float(p - L) / span) for p in range(L + 1, 13)
        }
    return {"path": path, "closing": closing}


# =========================================================================== #
# 3. Working-capital ratios from a BS snapshot
# =========================================================================== #
def forecast_wc_from_bs(
    ar: float,
    inv: float,
    ap: float,
    revenue_f: float,
    cogs_f: float,
    days: float = 360.0,
) -> dict[str, Optional[float]]:
    """Net working capital + the three activity ratios from a BS snapshot (natural).

        NWC = AR + Inv − AP
        DSO = AR / revenue_f · days   (None if revenue_f == 0)
        DPO = AP / COGS_f  · days     (None if COGS_f  == 0)
        DIO = Inv / COGS_f · days     (None if COGS_f  == 0)

    COGS_f is a POSITIVE magnitude.  ``None`` (not 0) signals an undefined ratio when
    the denominator is 0 — the caller decides how to present it.  (Computed for tests
    / diagnostics; NOT seeded into fact_position_plan.)

    Example: AR = 300, Inv = 300, AP = 225, revenue_f = 3600, COGS_f = 1800, days=360
      NWC = 375; DSO = 30; DPO = 45; DIO = 60.
    """
    nwc = float(ar) + float(inv) - float(ap)
    dso = (float(ar) / revenue_f) * days if abs(revenue_f) >= _EPS else None
    dpo = (float(ap) / cogs_f) * days if abs(cogs_f) >= _EPS else None
    dio = (float(inv) / cogs_f) * days if abs(cogs_f) >= _EPS else None
    return {"NWC": nwc, "DSO": dso, "DPO": dpo, "DIO": dio}


# =========================================================================== #
# 4. Indirect cash-flow bridge (P7 -> P12)
# =========================================================================== #
# Semantic keys the CF calculator emits -> the canonical CF structure leaf label
# (dim_pl_structure 'CF_…' rows' balance_title, matched via fin_compat_cf._norm_cf_key).
# The seeder resolves each label against the LIVE CF structure and seeds ONLY the
# leaves that resolve (subtotals such as CFO/CFI/CFF and 'Net income'/'Cash at end'
# are computed rows / absent leaves and are intentionally NOT seeded).
CF_SEMANTIC_LEAF_LABELS: dict[str, str] = {
    "D&A add-back": "Depreciation & amortisation",
    "Change in trade receivables": "Δ Trade receivables",
    "Change in inventories": "Δ Inventories",
    "Change in trade payables": "Δ Trade payables",
}


def forecast_cf_indirect(
    ar7: float, inv7: float, ap7: float,
    ar12: float, inv12: float, ap12: float,
    ni_f: float,
    cash7: float,
    da: float = 0.0,
    capex: float = 0.0,
    d_debt: float = 0.0,
    d_equity_ext: float = 0.0,
) -> dict[str, float]:
    """Indirect-method cash-flow bridge from P7 to P12, all values PRESENTED
    (inflow +, outflow −).  AR/Inv/AP are NATURAL magnitudes (assets +, AP +).

    ================================================================== FORMULA
        ΔAR = AR12 − AR7 ; ΔInv = Inv12 − Inv7 ; ΔAP = AP12 − AP7
        ΔNWC = ΔAR + ΔInv − ΔAP
        CFO  = NI_f + da − ΔNWC        (an AR increase is a cash OUTFLOW)
        CFI  = −CapEx                  (0 when PPE is carried flat)
        CFF  = ΔDebt + ΔEquity_ext     (0 when debt / external equity flat)
        ΔCash      = CFO + CFI + CFF
        EndingCash = Cash_P7 + ΔCash

    Presented leaf signs (indirect):
        Change in trade receivables = −ΔAR   (AR up ⇒ outflow, negative)
        Change in inventories       = −ΔInv
        Change in trade payables    = +ΔAP   (AP up ⇒ inflow, positive)
        D&A add-back                = +da

    ================================================================== EXAMPLE
    AR7 = 250, AR12 = 300 (ΔAR = 50); Inv7 = 280, Inv12 = 300 (ΔInv = 20);
    AP7 = 200, AP12 = 225 (ΔAP = 25); NI_f = 200, da = 0, Cash7 = 100.
      ΔNWC = 50 + 20 − 25 = 45 ; CFO = 200 − 45 = 155 ; CFI = 0 ; CFF = 0.
      ΔCash = 155 ; EndingCash = 100 + 155 = 255.
      Leaves: Δ trade receivables = −50, Δ inventories = −20, Δ trade payables = +25.

    ================================================================== EDGES
      * flat PPE/debt/equity → CapEx = ΔDebt = ΔEquity_ext = 0 → CFI = CFF = 0.
      * NI_f negative (loss) → CFO reduced accordingly (sign preserved).
      * returns SEMANTIC keys; the seeder maps them to CF leaf line_codes via
        :data:`CF_SEMANTIC_LEAF_LABELS` and the live CF structure.
    """
    d_ar = float(ar12) - float(ar7)
    d_inv = float(inv12) - float(inv7)
    d_ap = float(ap12) - float(ap7)
    d_nwc = d_ar + d_inv - d_ap
    cfo = float(ni_f) + float(da) - d_nwc
    cfi = -float(capex)
    cff = float(d_debt) + float(d_equity_ext)
    d_cash = cfo + cfi + cff
    ending_cash = float(cash7) + d_cash
    return {
        "Net income": float(ni_f),
        "D&A add-back": float(da),
        "Change in trade receivables": -d_ar,
        "Change in inventories": -d_inv,
        "Change in trade payables": d_ap,
        "CFO subtotal": cfo,
        "CFI": cfi,
        "CFF": cff,
        "Net change in cash": d_cash,
        "Cash at end": ending_cash,
    }


# =========================================================================== #
# 5. Weekly disaggregation (pure; tested; NOT wired into endpoints this task)
# =========================================================================== #
def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    return (nxt - date(year, month, 1)).days


def weekly_flow_split(
    monthly_by_period: dict[tuple[int, int], float],
    weeks: list[dict[str, Any]],
) -> dict[str, float]:
    """Calendar-day-weighted split of a monthly FLOW into ISO weeks.

    ``monthly_by_period`` : {(year, month): month_plan}.
    ``weeks``             : [{'key', 'start': date, 'end': date}, …] (inclusive).

        week_plan(w) = Σ_m month_plan(m) · days(w ∩ m) / days_in_month(m)

    A straddling week draws proportionally from BOTH months it overlaps.  By
    construction, Σ over the weeks that overlap a given month of the fraction
    days(w∩m)/days_in_month(m) == 1 when the weeks fully tile the month, so
    Σ week_plan over a month reproduces month_plan.

    Example: month plan Jul = 3100 (31 days → 100/day).  A week Jul-28..Aug-03 with
    4 July days contributes 3100·4/31 = 400 to that week from July's flow.
    """
    out: dict[str, float] = {}
    for w in weeks:
        start: date = w["start"]
        end: date = w["end"]
        total = 0.0
        for (yr, mo), plan in monthly_by_period.items():
            m_start = date(yr, mo, 1)
            m_end = date(yr, mo, _days_in_month(yr, mo))
            ov_start = max(start, m_start)
            ov_end = min(end, m_end)
            overlap = (ov_end - ov_start).days + 1
            if overlap <= 0:
                continue
            total += float(plan) * overlap / _days_in_month(yr, mo)
        out[str(w["key"])] = total
    return out


def weekly_stock_interp(
    month_end_balances: dict[tuple[int, int], float],
    week_cutoffs: list[dict[str, Any]],
    p7_carry: float,
) -> dict[str, float]:
    """Linear interpolation of a month-end STOCK series onto ISO-week cutoff dates.

    ``month_end_balances`` : {(year, month): closing_balance} at each month-END.
    ``week_cutoffs``       : [{'key', 'cutoff': date}, …] (the week's Sunday).
    ``p7_carry``           : the carried balance for any cutoff at/BEFORE the first
                             forecast month-end (the P7 actual close).

    A cutoff is interpolated linearly between the two bracketing month-ends; a cutoff
    before the earliest month-end returns ``p7_carry``; after the latest returns the
    latest month-end balance.

    Example: Jul-31 = 100, Aug-31 = 200.  A week cutoff Aug-14 (14/31 into August) →
    100 + (200 − 100)·14/31 ≈ 145.16.  A cutoff before Jul-31 → p7_carry.
    """
    pts = sorted(
        ((date(y, m, _days_in_month(y, m)), float(bal)) for (y, m), bal in month_end_balances.items()),
        key=lambda t: t[0],
    )
    out: dict[str, float] = {}
    for wc in week_cutoffs:
        cutoff: date = wc["cutoff"]
        key = str(wc["key"])
        if not pts or cutoff <= pts[0][0]:
            out[key] = float(p7_carry) if not pts or cutoff < pts[0][0] else pts[0][1]
            continue
        if cutoff >= pts[-1][0]:
            out[key] = pts[-1][1]
            continue
        # Find the bracketing month-ends.
        val = pts[-1][1]
        for (d0, v0), (d1, v1) in zip(pts, pts[1:]):
            if d0 <= cutoff <= d1:
                frac = (cutoff - d0).days / max((d1 - d0).days, 1)
                val = v0 + (v1 - v0) * frac
                break
        out[key] = val
    return out


# =========================================================================== #
# 6. Cross-tie assertions (raise on violation)
# =========================================================================== #
def assert_cross_ties(
    re_p7: float, re_p12: float,
    ni_f: float,
    cf_ending_cash: float, bs_cash_p12: float,
    assets_p12: float, le_p12: float,
    eps: float = 1e-6,
) -> None:
    """Raise ``AssertionError`` unless the three forecast identities all hold:

        1. RE_P12 − RE_P7 == NI_f            (retained-earnings roll-forward)
        2. CF EndingCash == BS Cash_P12      (the two statements agree on cash)
        3. Assets_P12 == (L&E)_P12           (the balance sheet balances)

    Called by the seeder AFTER computing the forecast and BEFORE the 2 dp rounding on
    write, so full-precision floats tie to ``eps``.
    """
    if abs((re_p12 - re_p7) - ni_f) > eps:
        raise AssertionError(
            f"RE roll-forward tie broken: (RE_P12 - RE_P7)={re_p12 - re_p7} != NI_f={ni_f}"
        )
    if abs(cf_ending_cash - bs_cash_p12) > eps:
        raise AssertionError(
            f"Cash tie broken: CF EndingCash={cf_ending_cash} != BS Cash_P12={bs_cash_p12}"
        )
    if abs(assets_p12 - le_p12) > eps:
        raise AssertionError(
            f"Balance identity broken: Assets_P12={assets_p12} != (L&E)_P12={le_p12}"
        )
