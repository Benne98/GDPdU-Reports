"""P5 / Phase C5 — Cash Flow statement (INDIRECT method).

Derives an indirect-method cash flow per period column from the P&L flow (net
income) and the change in Balance-Sheet balances over the column's window
(ΔNWC, ΔPP&E, Δequity/debt).  Reuses the BS service (cumulative balances) and the
P&L service (flow) verbatim — no formula is forked.

The pure core (``compute_cash_flow``) takes the BsStatement + PlStatement +
PeriodPlan and is fully golden-testable with no DB.

======================================================================= WINDOW MODEL — Δ over the column's OWN window (opening → closing)
The BS service produces CUMULATIVE stock at a column's cutoff (its latest bucket).
For cash flow we need the CHANGE over the column's window, so we need TWO stocks:
    closing = BS stock at the column's latest bucket (its cutoff)
    opening = BS stock at the bucket IMMEDIATELY BEFORE the column's earliest bucket
Then for any BS line X:   ΔX = closing(X) − opening(X).
The net-income flow (from the P&L) is already a flow over exactly the same window,
so NI and the ΔBS line up to the same period range → the tie-out holds.

To obtain the opening stock we ask the BS core for a SECOND, shifted plan whose
single bucket is the "period before window start"; the orchestrator builds both.
The pure core simply receives ``bs_closing`` and ``bs_opening`` BsStatements that
share the same column keys, and differences them.

======================================================================= INDIRECT DERIVATION (per column)
Sign basis (all PRESENTED): assets +, liab/equity +, P&L income +/expense −.

  Net income (NI)        = P&L net result for the window
                           (we use GROSS_PROFIT as the modelled bottom line for the
                            synthetic fixture; with a fuller P&L this is EBIT/NI —
                            documented: NI source line is configurable via ``ni_code``).
  D&A add-back (DA)      = depreciation & amortisation (non-cash) added back.
                           STUB = 0.0 unless a 'DA' line is supplied (the synthetic
                           GL has no D&A account) — clearly marked as a stub.
  ΔNWC                   = ΔAR + ΔInventory − ΔAP
                           (increase in AR/Inventory uses cash; increase in AP frees cash)

  CFO = NI + DA − ΔNWC                                   [operating]
  CFI = − ΔPP&E                                          [investing]  (STUB=0: no PP&E account in fixture)
  CFF = ΔContributedEquity + ΔDebt                       [financing]
        ΔContributedEquity = Δ(EQUITY share-capital line ONLY)
            We deliberately use the contributed-equity line by itself and do NOT
            touch RETAINED here.  This period's net income lives in RETAINED and is
            already counted in CFO via NI (NI == ΔRetained in the closed-books
            fixture), so adding ΔRetained to CFF would double count.  Equity raises
            / buy-backs move the EQUITY line and belong in financing.
        ΔDebt = 0.0 STUB (no long-term debt account in fixture).

======================================================================= TIE-OUT (reconciliation, asserted in the golden test)
By the accounting identity (Assets = Liab + Equity) holding at BOTH the opening
and the closing cutoff, the change in cash is:
    ΔCash = ΔLiab + ΔEquity − ΔOtherAssets
          = ΔAP + (ΔContributedEquity + ΔRetained) − ΔAR − ΔInventory − ΔPP&E
And
    CFO + CFI + CFF
      = (NI − ΔAR − ΔInv + ΔAP)            [DA=0]
      + (−ΔPP&E)
      + (ΔEquity_line + ΔDebt)
      = ΔRetained − ΔAR − ΔInv + ΔAP − ΔPP&E + ΔEquity_line + 0   [NI=ΔRetained, ΔDebt=0]
      = ΔAP + (ΔEquity_line + ΔRetained) − ΔAR − ΔInv − ΔPP&E
      = ΔLiab + ΔTotalEquity − ΔOtherAssets
      = ΔCash.                                            ✓  EXACT tie-out.
The golden test asserts  CFO + CFI + CFF == ΔCash  per column (the Cash BS-line Δ).

======================================================================= WORKED EXAMPLE (golden)
Two-period fixture (see test).  Window = month 2 (opening = end of month 1):
  ΔCash = +100, NI(month2) = +50, ΔAR = +30, ΔInv = 0, ΔAP = −80? … (exact numbers
  pinned in the test).  CFO + CFI + CFF reconciles to ΔCash by construction.

======================================================================= EDGE CASES
  • No opening stock (window starts at the very first period): opening = 0 for all
    lines → Δ == closing (first-period flows == stock).  Tie-out still holds.
  • Empty window (no buckets): every Δ = 0, NI = 0 → all blocks 0, ΔCash 0, ties out.
  • STUB blocks (D&A, PP&E/CFI, debt/CFF-debt) are 0 because the synthetic GL has no
    such accounts; the tie-out is asserted over the MODELLED components and the gap
    is documented here.  With a fuller chart of accounts these lines populate and
    the SAME identity continues to tie out.
  • Negative cash flow (cash outflow): values carry their sign; no clamping.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.services.statements import StatementCell, StatementLine


@dataclass
class CfStatement:
    view_mode: str
    coverage: float
    column_keys: list[str]
    column_labels: dict[str, str]
    lines: list[StatementLine]
    # ΣCF (CFO+CFI+CFF) − ΔCash per column.  0.0 when the tie-out holds.
    tieout_residual: dict[str, float]


def _cell_value(stmt: Any, line_code: str, column_key: str) -> Optional[float]:
    for ln in stmt.lines:
        if ln.line_code == line_code:
            for c in ln.cells:
                if c.column_key == column_key:
                    return c.value
    return None


def _v(stmt: Any, code: str, key: str) -> float:
    """Value or 0.0 (None-safe) for arithmetic."""
    val = _cell_value(stmt, code, key)
    return float(val) if val is not None else 0.0


# --------------------------------------------------------------------------- #
# Pure core
# --------------------------------------------------------------------------- #
def compute_cash_flow(
    bs_closing: Any,   # BsStatement at each column's closing cutoff
    bs_opening: Any,   # BsStatement at each column's opening cutoff (period before window start)
    pl: Any,           # PlStatement (flow over the window)
    period_plan: Any,
    *,
    ni_code: str = "GROSS_PROFIT",   # modelled net-income source line in the P&L
    cash_code: str = "CASH",
    ar_code: str = "AR",
    inv_code: str = "INVENTORY",
    ap_code: str = "AP",
    equity_code: str = "EQUITY",
    retained_code: str = "RETAINED",
    da_code: str = "DA",             # depreciation add-back (stub: absent → 0)
    ppe_code: str = "PPE",           # investing (stub: absent → 0)
    debt_code: str = "DEBT",         # financing debt (stub: absent → 0)
) -> CfStatement:
    """Indirect-method cash flow per column.  PURE — no DB, deterministic.

    Δ for a BS line = closing − opening (both cumulative stocks).  NI from the P&L
    flow.  CFO/CFI/CFF per the module-docstring derivation; tie-out residual =
    (CFO+CFI+CFF) − ΔCash, which is 0.0 by construction for the closed-books fixture.
    """
    columns = list(period_plan.columns)

    ni: dict[str, float] = {}
    da: dict[str, float] = {}
    d_ar: dict[str, float] = {}
    d_inv: dict[str, float] = {}
    d_ap: dict[str, float] = {}
    d_nwc: dict[str, float] = {}
    cfo: dict[str, float] = {}
    cfi: dict[str, float] = {}
    cff: dict[str, float] = {}
    d_cash: dict[str, float] = {}
    residual: dict[str, float] = {}

    def delta(code: str, key: str) -> float:
        return _v(bs_closing, code, key) - _v(bs_opening, code, key)

    for col in columns:
        k = col.key
        ni[k] = _v(pl, ni_code, k)
        da[k] = _v(bs_closing, da_code, k) - _v(bs_opening, da_code, k)  # 0 when absent

        d_ar[k] = delta(ar_code, k)
        d_inv[k] = delta(inv_code, k)
        d_ap[k] = delta(ap_code, k)
        d_nwc[k] = d_ar[k] + d_inv[k] - d_ap[k]

        cfo[k] = ni[k] + da[k] - d_nwc[k]

        d_ppe = delta(ppe_code, k)            # 0 when absent (stub)
        cfi[k] = -d_ppe

        # Contributed equity = the EQUITY (share-capital) line ONLY.  Retained
        # earnings (this period's net income) is already in CFO via NI; including
        # ΔRetained here would double count.
        d_contributed = delta(equity_code, k)
        d_debt = delta(debt_code, k)           # 0 when absent (stub)
        cff[k] = d_contributed + d_debt

        d_cash[k] = delta(cash_code, k)
        residual[k] = (cfo[k] + cfi[k] + cff[k]) - d_cash[k]

    def _line(code: str, label: str, vals: dict[str, float], *, row_type: str,
              is_bold: bool = False, kpi_code: Optional[str] = None) -> StatementLine:
        return StatementLine(
            line_code=code,
            label=label,
            row_type=row_type,
            is_bold=is_bold,
            kpi_code=kpi_code,
            cells=[StatementCell(column_key=col.key, value=vals[col.key]) for col in columns],
        )

    net_cf = {k: cfo[k] + cfi[k] + cff[k] for k in (c.key for c in columns)}

    lines = [
        _line("NI", "Net income", ni, row_type="mapping"),
        _line("DA", "Depreciation & amortisation (add-back, stub)", da, row_type="mapping"),
        _line("CHG_AR", "Δ Accounts receivable", {k: -v for k, v in d_ar.items()}, row_type="mapping"),
        _line("CHG_INV", "Δ Inventory", {k: -v for k, v in d_inv.items()}, row_type="mapping"),
        _line("CHG_AP", "Δ Accounts payable", d_ap, row_type="mapping"),
        _line("CFO", "Cash flow from operations", cfo, row_type="subtotal", is_bold=True),
        _line("CFI", "Cash flow from investing (stub)", cfi, row_type="subtotal", is_bold=True),
        _line("CFF", "Cash flow from financing", cff, row_type="subtotal", is_bold=True),
        _line("NET_CF", "Net change in cash", net_cf, row_type="subtotal", is_bold=True),
        _line("CHG_CASH_BS", "Δ Cash (BS, tie-out target)", d_cash, row_type="calc"),
    ]

    return CfStatement(
        view_mode=period_plan.view_mode,
        coverage=period_plan.coverage,
        column_keys=[c.key for c in columns],
        column_labels={c.key: c.label for c in columns},
        lines=lines,
        tieout_residual=residual,
    )


# --------------------------------------------------------------------------- #
# Opening-window plan helper
# --------------------------------------------------------------------------- #
def _opening_plan(plan: Any) -> Any:
    """Build a sibling PeriodPlan whose every column's single bucket is the period
    IMMEDIATELY BEFORE the original column's earliest bucket (its opening cutoff).

    The BS core only ever uses a column's LATEST bucket as the cutoff, so a one-
    bucket column at (opening cutoff) yields exactly the opening stock.  An empty
    or first-ever-period column has NO prior bucket → empty buckets → opening 0.
    Column KEYS are preserved so the closing/opening statements align 1:1.
    """
    from app.services.periods import N_PERIODS, PeriodColumn, PeriodPlan

    def prior_bucket(b: tuple[int, int]) -> Optional[tuple[int, int]]:
        fy, p = b
        if p > 1:
            return (fy, p - 1)
        # period 1 → last period of prior year
        return (fy - 1, N_PERIODS)

    new_cols: list[Any] = []
    for col in plan.columns:
        if not col.buckets:
            new_cols.append(PeriodColumn(col.key, col.label, ()))
            continue
        earliest = min(col.buckets)
        pb = prior_bucket(earliest)
        new_cols.append(PeriodColumn(col.key, col.label, (pb,)))
    return PeriodPlan(
        view_mode=plan.view_mode,
        current_fy=plan.current_fy,
        last_closed_period=plan.last_closed_period,
        columns=tuple(new_cols),
        coverage=plan.coverage,
        fy_start_month=plan.fy_start_month,
    )


# --------------------------------------------------------------------------- #
# Orchestration (thin; reuses BS + P&L builders, builds an opening BS too)
# --------------------------------------------------------------------------- #
def build_cf_statement(
    session: Any,
    *,
    view_mode: str,
    current_fy: int,
    last_closed_period: int,
    entity_prefix: Optional[str] = None,
    scenario: Optional[str] = None,
    fy_start_month: int = 1,
) -> CfStatement:
    """Build closing BS, opening BS (shifted plan), and P&L, then derive CF."""
    from app.services.balance_sheet import (
        aggregate_bs,
        fetch_bs_movements,
        fetch_bs_structure,
    )
    from app.services.balance_sheet import _all_years_for_cumulative
    from app.services.periods import build_period_plan
    from app.services.statements import build_pl_statement

    plan = build_period_plan(
        view_mode,  # type: ignore[arg-type]
        current_fy,
        last_closed_period,
        fy_start_month=fy_start_month,
    )
    structure = fetch_bs_structure(session)
    movements = fetch_bs_movements(
        session,
        _all_years_for_cumulative(plan),
        entity_prefix=entity_prefix,
        scenario=scenario,
    )
    bs_closing = aggregate_bs(movements, structure, plan)
    bs_opening = aggregate_bs(movements, structure, _opening_plan(plan))
    pl = build_pl_statement(
        session,
        view_mode=view_mode,
        current_fy=current_fy,
        last_closed_period=last_closed_period,
        entity_prefix=entity_prefix,
        scenario=scenario,
        fy_start_month=fy_start_month,
    )
    return compute_cash_flow(bs_closing, bs_opening, pl, plan)
