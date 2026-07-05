"""Financial spec + golden tests — AR/AP OPOS as-of aging (F3/F4).

This file is the EXECUTABLE SPEC for the OPOS as-of (Stichtag) aging rebuild
documented in ``docs/financial-logic.md`` → "AR/AP OPOS As-of Aging (F3/F4)".
It is DB-FREE: every assertion runs against a tiny synthetic OPOS fixture
(Vortrag + Bewegung + Fact-Ergaenzung + Bilanzabstimmung rows), so the pure
Method-A / FIFO / overdue-% / clamp math PASSES today without Postgres.

The reference implementation below (``_ref_*``) is the proposed pure helper the
Phase-2 backend will import as ``app.services.opos_aging.compute_opos_aging``.
``test_phase2_backend_helper_contract`` pins that interface: it SKIPS cleanly
until Phase 2 ships the module, then becomes the acceptance gate (the production
helper must agree with this reference on the fixture).

Grounded numbers (verified against the real subledger dataset,
``…/Finssentials - Setup/subledgers/2024/debitor.xlsx``):
    Atlas AR 2024, konto 24xxx, ALL Satzart, Buchungsdatum <= 2024-12-31
        = 7,783,860.18  (Vortrag +6,047,183.63; Bewegung -4,114,067.13;
                         Fact-Ergaenzung +5,850,743.68)
    Naive alternatives are WRONG:
        - only Bewegung           = -4,114,067.13  (drops Vortrag + Fact-Erg.)
        - sum of all years <= S   = 13,681,783.28  (2023 close 5,897,923.10 +
                                    2024 close — double-counts 2024's Vortrag)
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services.gl_aging import AR_BANDS  # reuse the canonical bucket set (F4 rule)

_BANDS = tuple(b for b, _ in AR_BANDS)


# --------------------------------------------------------------------------- #
# Reference implementation = the proposed pure helper (spec, executable)
# --------------------------------------------------------------------------- #
# An OPOS row is a plain dict.  Keys mirror the fact_opos columns:
#   entity, partner_key, konto, satzart, belegart,
#   buchungsdatum (date), nettofaelligkeit (date|None), betrag (float, Hauswaehrung),
#   fy_label (int)
# Sign convention (unchanged, matches the source ledger):
#   AR (debitor) open item is a DEBIT  -> stored POSITIVE
#   AP (kreditor) open item is a CREDIT -> stored NEGATIVE
_TERMS = {"AR": 30, "AP": 45}   # README payment terms: customer 30d, supplier 45d


def _ref_net_open_by_group(rows, as_of):
    """Method A: as-of open balance at (entity, partner_key, konto) grain.

    Σ betrag over rows WHERE fy_label == year(as_of) AND buchungsdatum <= as_of,
    summing ALL Satzart.  Fiscal-year-anchored — never crosses years.
    """
    acc: dict[tuple, float] = {}
    for r in rows:
        if r["fy_label"] == as_of.year and r["buchungsdatum"] <= as_of:
            k = (r["entity"], r["partner_key"], r["konto"])
            acc[k] = acc.get(k, 0.0) + float(r["betrag"])
    return {k: round(v, 2) for k, v in acc.items()}


def _ref_due_date(row, terms_days):
    """due = COALESCE(Nettofaelligkeit, Buchungsdatum + terms)."""
    return row["nettofaelligkeit"] or (row["buchungsdatum"] + timedelta(days=terms_days))


def _ref_band_of(due, as_of):
    """Mirror gl_aging._band_case_sql boundaries EXACTLY (Postgres → Python).

    NOTE: like the SQL, due == as_of (0 days) falls through to overdue_over_180
    (the BETWEEN ranges start at 1).  Mirrored deliberately so the Python
    reference and the production SQL bucket identically.
    """
    if due > as_of:
        return "not_yet_due"
    d = (as_of - due).days
    if 1 <= d <= 30:
        return "overdue_1_30"
    if 31 <= d <= 60:
        return "overdue_31_60"
    if 61 <= d <= 90:
        return "overdue_61_90"
    if 91 <= d <= 180:
        return "overdue_91_180"
    return "overdue_over_180"


def _ref_compute_opos_aging(rows, as_of, side):
    """FIFO as-of aging per partner (F3/F4).

    1. Method A net-open magnitude per partner (sum ALL trade rows, this FY,
       <= as_of), converted to a POSITIVE magnitude via the side sign.
    2. CREDIT balance (magnitude < 0: overpayment / advance / net credit note)
       -> total_open = overdue_open = 0, credit_balance = True, in_scatter =
       False (excluded from the risk-matrix scatter).
    3. Otherwise FIFO: allocate the net-open magnitude across that partner's
       invoice-type rows (Belegart RV/RG) ordered OLDEST-first by due date;
       each residual slice buckets via AR_BANDS.  By construction
       Σ buckets == total_open and overdue_open <= total_open.
    4. overdue_pct = overdue_open / total_open, computed from the SAME FIFO
       base, then CLAMPED to [0, 100] (belt-and-suspenders — bounded already).
    """
    sign = 1.0 if side == "AR" else -1.0
    terms = _TERMS[side]

    groups: dict[str, float] = {}
    for r in rows:
        if r["fy_label"] == as_of.year and r["buchungsdatum"] <= as_of:
            groups.setdefault(r["partner_key"], 0.0)
            groups[r["partner_key"]] += float(r["betrag"]) * sign

    out: dict[str, dict] = {}
    for partner, mag in groups.items():
        mag = round(mag, 2)
        buckets = {b: 0.0 for b in _BANDS}

        if mag < 0:  # credit balance — see rule (2)
            out[partner] = {
                "total_open": 0.0, "overdue_open": 0.0, "overdue_pct": 0.0,
                "credit_balance": True, "in_scatter": False, "buckets": buckets,
            }
            continue

        invs = [
            r for r in rows
            if r["partner_key"] == partner and r["belegart"] in ("RV", "RG")
            and r["fy_label"] == as_of.year and r["buchungsdatum"] <= as_of
        ]
        invs.sort(key=lambda r: _ref_due_date(r, terms))

        remaining = mag
        for r in invs:
            if remaining <= 1e-9:
                break
            slice_amt = min(remaining, abs(float(r["betrag"])))
            buckets[_ref_band_of(_ref_due_date(r, terms), as_of)] += slice_amt
            remaining -= slice_amt

        # A5: net-open that current-FY RV/RG invoices cannot cover is the
        # carried-forward opening (Vortrag, Belegart SV — not in the FIFO pool).
        # It predates the fiscal year, so it is long-aged → overdue_over_180.
        if remaining > 1e-9:
            buckets["overdue_over_180"] += remaining

        total = round(sum(buckets.values()), 2)
        overdue = round(sum(v for b, v in buckets.items() if b != "not_yet_due"), 2)
        pct = 0.0 if total <= 1e-9 else max(0.0, min(100.0, 100.0 * overdue / total))
        out[partner] = {
            "total_open": total, "overdue_open": overdue,
            "overdue_pct": round(pct, 1),
            "credit_balance": False, "in_scatter": True,
            "buckets": {b: round(v, 2) for b, v in buckets.items()},
        }
    return out


def _clamp_pct(overdue, total):
    """The builder-output clamp (rule 3-iii)."""
    if total <= 1e-9:
        return 0.0
    return round(max(0.0, min(100.0, 100.0 * overdue / total)), 1)


def _term_proxy_days(side):
    """DSO/DPO fallback proxy when the GoBD journal is absent (Assumption A2).

    CORRECT terms per README: customer (AR) 30d, supplier (AP) 45d.
    The current gl_aging.py hardcodes the INVERSE (dso=45 / dpo=30) — see
    test_current_gl_aging_proxy_is_inverted_BUG.
    """
    return _TERMS[side]


# --------------------------------------------------------------------------- #
# Synthetic fixture (AR side, FY2024, Stichtag 2024-12-31)
# --------------------------------------------------------------------------- #
AS_OF = date(2024, 12, 31)


def _row(partner, konto, satzart, belegart, betrag, bd, due=None, fy=2024, entity="Atlas"):
    return {
        "entity": entity, "partner_key": partner, "konto": konto,
        "satzart": satzart, "belegart": belegart, "betrag": float(betrag),
        "buchungsdatum": bd, "nettofaelligkeit": due, "fy_label": fy,
    }


# P1: normal partner, net-open 600 spills FIFO across an overdue + a not-yet-due invoice.
# P2: CREDIT balance (customer overpaid) → net-open -100.
# P3: THE 237% CASE — big overdue invoice, small net-open after payment.
FIXTURE = [
    # --- P1 --- net-open = 100 + 500 + 300 - 350 + 50 = 600 ------------------
    _row("P1", 24000, "Vortrag", "SV", 100.0, date(2024, 1, 1)),
    _row("P1", 24000, "Bewegung", "RV", 500.0, date(2024, 6, 1), due=date(2024, 7, 1)),   # 183d overdue
    _row("P1", 24000, "Bewegung", "RV", 300.0, date(2024, 12, 15), due=date(2025, 1, 14)),  # not yet due
    _row("P1", 24000, "Bewegung", "ZA", -350.0, date(2024, 11, 1)),
    _row("P1", 24000, "Fact-Ergaenzung", "BA", 50.0, date(2024, 9, 1)),
    # --- P2 --- net-open = 100 + 200 - 400 = -100  (credit / overpayment) ----
    _row("P2", 24000, "Vortrag", "SV", 100.0, date(2024, 1, 1)),
    _row("P2", 24000, "Bewegung", "RV", 200.0, date(2024, 5, 1), due=date(2024, 5, 31)),
    _row("P2", 24000, "Bewegung", "ZA", -400.0, date(2024, 10, 1)),
    # --- P3 --- net-open = 130 - 80 = 50 ; overdue invoice 130 --------------
    _row("P3", 24000, "Bewegung", "RV", 130.0, date(2024, 3, 1), due=date(2024, 3, 31)),  # overdue >180
    _row("P3", 24000, "Bewegung", "ZA", -80.0, date(2024, 9, 1)),
]


# =========================================================================== #
# (a) Method A — as-of open balance per (partner, account)
# =========================================================================== #
def test_method_a_net_open_per_group():
    net = _ref_net_open_by_group(FIXTURE, AS_OF)
    assert net[("Atlas", "P1", 24000)] == 600.0
    assert net[("Atlas", "P2", 24000)] == -100.0   # credit balance
    assert net[("Atlas", "P3", 24000)] == 50.0


def test_method_a_is_fiscal_year_anchored_not_cross_year():
    """A prior-year row (fy_label 2023) must NOT enter the 2024 as-of balance,
    even though the 2024 Vortrag already carries the 2023 close."""
    rows = FIXTURE + [
        _row("P1", 24000, "Bewegung", "RV", 9999.0, date(2023, 6, 1), fy=2023),
    ]
    net = _ref_net_open_by_group(rows, AS_OF)
    assert net[("Atlas", "P1", 24000)] == 600.0   # 2023 row excluded


def test_method_a_includes_all_satzart():
    """Dropping Fact-Ergaenzung (naive 'Bewegung only') understates the balance."""
    only_bewegung = sum(
        r["betrag"] for r in FIXTURE
        if r["partner_key"] == "P1" and r["satzart"] == "Bewegung"
    )
    assert round(only_bewegung, 2) == 450.0        # 500 + 300 - 350  (wrong)
    assert _ref_net_open_by_group(FIXTURE, AS_OF)[("Atlas", "P1", 24000)] == 600.0  # right


# =========================================================================== #
# (b) FIFO aging — overdue <= total AND buckets sum to total
# =========================================================================== #
def test_fifo_buckets_sum_to_total_and_overdue_le_total():
    aging = _ref_compute_opos_aging(FIXTURE, AS_OF, side="AR")
    for partner, a in aging.items():
        assert round(sum(a["buckets"].values()), 2) == a["total_open"], partner
        assert a["overdue_open"] <= a["total_open"] + 1e-9, partner


def test_fifo_p1_spills_oldest_first():
    a = _ref_compute_opos_aging(FIXTURE, AS_OF, side="AR")["P1"]
    # 600 net-open: 500 → oldest overdue invoice (>180d), 100 → not-yet-due invoice.
    assert a["total_open"] == 600.0
    assert a["buckets"]["overdue_over_180"] == 500.0
    assert a["buckets"]["not_yet_due"] == 100.0
    assert a["overdue_open"] == 500.0
    assert a["overdue_pct"] == 83.3            # 500 / 600
    assert a["credit_balance"] is False and a["in_scatter"] is True


# =========================================================================== #
# (c) Credit-balance case → overdue_pct 0, flagged, excluded from scatter
# =========================================================================== #
def test_credit_balance_partner_is_zeroed_and_excluded():
    a = _ref_compute_opos_aging(FIXTURE, AS_OF, side="AR")["P2"]
    assert a["total_open"] == 0.0
    assert a["overdue_open"] == 0.0
    assert a["overdue_pct"] == 0.0
    assert a["credit_balance"] is True
    assert a["in_scatter"] is False


# =========================================================================== #
# (d) THE 237% FIX — same FIFO base bounds overdue_pct to [0, 100]
# =========================================================================== #
def test_overdue_pct_bounded_the_237_percent_fix():
    a = _ref_compute_opos_aging(FIXTURE, AS_OF, side="AR")["P3"]
    # OLD BROKEN method: overdue = full overdue invoice (130) over net-open (50)
    old_broken_pct = 100.0 * 130.0 / 50.0
    assert round(old_broken_pct) == 260             # > 100 (the class of the 237% bug)
    # NEW method: both from the SAME FIFO base → 50 overdue / 50 total = 100%.
    assert a["total_open"] == 50.0
    assert a["overdue_open"] == 50.0
    assert a["overdue_pct"] == 100.0


def test_clamp_helper_is_belt_and_suspenders():
    assert _clamp_pct(130.0, 50.0) == 100.0    # would be 260% → clamped
    assert _clamp_pct(-10.0, 50.0) == 0.0      # negative overdue → clamped low
    assert _clamp_pct(30.0, 100.0) == 30.0     # in-range untouched
    assert _clamp_pct(5.0, 0.0) == 0.0         # zero denominator → 0, no div-by-zero


# =========================================================================== #
# (e) DSO / DPO — term proxy is NOT inverted (AR 30d, AP 45d)
# =========================================================================== #
def test_dso_dpo_proxy_terms_not_inverted():
    assert _term_proxy_days("AR") == 30    # customer terms → DSO fallback
    assert _term_proxy_days("AP") == 45    # supplier terms → DPO fallback


def test_current_gl_aging_proxy_is_inverted_BUG():
    # Phase 2 (financial-logic F4) corrected the inversion: the legacy GL proxy
    # now uses README terms (customer/AR 30d, supplier/AP 45d) and the OPOS path
    # (opos_aging.term_proxy_days) matches. Authored as a strict-xfail by the
    # financial engineer documenting the bug; flipped to a passing regression once
    # Phase 2 swapped the literals.
    import inspect
    from app.services import gl_aging
    ar_src = inspect.getsource(gl_aging.build_receivables_aging)
    ap_src = inspect.getsource(gl_aging.build_payables_aging)
    assert '"dso_days": 30.0' in ar_src
    assert '"dpo_days": 45.0' in ap_src
    # inverted literals must be gone
    assert '"dso_days": 45.0' not in ar_src
    assert '"dpo_days": 30.0' not in ap_src


# =========================================================================== #
# Edge cases: missing Nettofaelligkeit; partial-YTD; not_yet_due-only
# =========================================================================== #
def test_missing_nettofaelligkeit_falls_back_to_posting_plus_terms():
    # No due date → due = 2024-06-01 + 30d = 2024-07-01 → 183d overdue at 2024-12-31.
    rows = [
        _row("PX", 24000, "Bewegung", "RV", 200.0, date(2024, 6, 1), due=None),
    ]
    a = _ref_compute_opos_aging(rows, AS_OF, side="AR")["PX"]
    assert a["total_open"] == 200.0
    assert a["buckets"]["overdue_over_180"] == 200.0


def test_partial_ytd_stichtag_uses_only_rows_up_to_stichtag():
    # Jul-2025 partial YTD: an August invoice must not enter a 2025-07-31 balance.
    rows = [
        _row("PY", 24000, "Vortrag", "SV", 100.0, date(2025, 1, 1), fy=2025),
        _row("PY", 24000, "Bewegung", "RV", 500.0, date(2025, 6, 1), due=date(2025, 6, 30), fy=2025),
        _row("PY", 24000, "Bewegung", "RV", 999.0, date(2025, 8, 1), due=date(2025, 8, 31), fy=2025),
    ]
    net = _ref_net_open_by_group(rows, date(2025, 7, 31))
    assert net[("Atlas", "PY", 24000)] == 600.0    # August 999 excluded


def test_not_yet_due_only_partner_has_zero_overdue_pct():
    rows = [
        _row("PZ", 24000, "Bewegung", "RV", 400.0, date(2024, 12, 20), due=date(2025, 1, 19)),
    ]
    a = _ref_compute_opos_aging(rows, AS_OF, side="AR")["PZ"]
    assert a["total_open"] == 400.0
    assert a["overdue_open"] == 0.0
    assert a["overdue_pct"] == 0.0
    assert a["buckets"]["not_yet_due"] == 400.0


# =========================================================================== #
# AP side — net-open is a CREDIT (negative); magnitude/aging mirror AR
# =========================================================================== #
def test_ap_side_credit_open_is_positive_magnitude():
    rows = [
        _row("S1", 36000, "Vortrag", "SV", -1000.0, date(2024, 1, 1)),
        _row("S1", 36000, "Bewegung", "RV", -500.0, date(2024, 6, 1), due=date(2024, 7, 16)),  # +45d
        _row("S1", 36000, "Bewegung", "ZA", 300.0, date(2024, 11, 1)),
    ]
    # Method A net-open (stored sign) = -1200 (a credit / payable).
    assert _ref_net_open_by_group(rows, AS_OF)[("Atlas", "S1", 36000)] == -1200.0
    a = _ref_compute_opos_aging(rows, AS_OF, side="AP")["S1"]
    assert a["total_open"] == 1200.0           # positive magnitude
    assert a["credit_balance"] is False
    assert round(sum(a["buckets"].values()), 2) == 1200.0
    # RV -500 (due 2024-07-16 → 168d overdue) buckets 91–180; the uncovered
    # 700 carried-forward (Vortrag) residual → overdue_over_180 (A5).
    assert a["buckets"]["overdue_91_180"] == 500.0
    assert a["buckets"]["overdue_over_180"] == 700.0
    assert a["overdue_open"] == 1200.0


# =========================================================================== #
# Phase-2 contract — the production helper must match this reference
# =========================================================================== #
def test_phase2_backend_helper_contract():
    """SKIPS until Phase 2 ships app.services.opos_aging.compute_opos_aging;
    then this is the acceptance gate against the reference spec."""
    pytest.importorskip(
        "app.services.opos_aging",
        reason="Phase 2 not yet implemented — reference contract is the target.",
    )
    from app.services.opos_aging import compute_opos_aging  # type: ignore
    got = compute_opos_aging(FIXTURE, AS_OF, side="AR")
    assert got == _ref_compute_opos_aging(FIXTURE, AS_OF, side="AR")


# =========================================================================== #
# Aging-section internal consistency: GROSS basis + credit-balance bridge
# (opos_aging.build_*_aging_opos).  Formula:
#   total_open_gross = Σ FIFO buckets / 1000  == Σ series.amount
#                      == before_due + overdue   (positive-partner-only basis)
#   credit_balances  = total_open_gross − <net headline magnitude>
#                      == Σ|net of credit-balance partners|   (the bridge, >= 0)
#   overdue_pct      = 100 × overdue / total_open_gross   (in [0,100] by construction)
# Keeps: total_receivables/total_payables = Method-A NET signed sum (reconciled,
# nets credit partners DOWN, NOT floored) — the headline is UNCHANGED.
# --------------------------------------------------------------------------- #
def _synth_partner_view(rows, as_of, side):
    """Mirror opos_aging._partner_view's return CONTRACT from synthetic rows.

    Reuses the production compute_opos_aging so the aging math is not re-derived
    here; only the DB fetch is stubbed out.
    """
    from app.services.opos_aging import compute_opos_aging  # type: ignore
    aging = compute_opos_aging(rows, as_of, side)
    per_partner = {
        pk: {**a, "partner_key": pk, "meta": {}, "open_documents": 0}
        for pk, a in aging.items()
    }
    return {
        "as_of": as_of,
        "aging": per_partner,
        "meta": {},
        "rows": rows,
        "signed_total_eur": sum(float(r["betrag"]) for r in rows),  # Method-A F5
        "open_documents": 0,
    }


def _assert_aging_invariants(res, headline_key):
    """Invariants 1–3 that MUST hold on every Stichtag (kEUR, within rounding)."""
    gross = res["total_open_gross"]
    credit = res["credit_balances"]
    before = res["kpis"]["before_due"]
    overdue = res["kpis"]["overdue"]
    pct = res["kpis"]["overdue_pct"]
    net = res[headline_key]
    series_sum = round(sum(s["amount"] for s in res["series"]), 2)

    # (1) before_due + overdue == total_open_gross == Σ series.amount
    assert round(before + overdue, 2) == gross
    assert series_sum == gross
    # (2) overdue <= gross AND 0 <= pct <= 100 WITHOUT relying on the clamp:
    #     the RAW ratio is already in range (clamp is belt-and-suspenders only).
    assert overdue <= gross + 1e-9
    raw_pct = 0.0 if gross <= 1e-9 else round(100.0 * overdue / gross, 1)
    assert 0.0 <= raw_pct <= 100.0
    assert pct == raw_pct
    # (3) total_open_gross − credit_balances == reconciled net headline (unchanged)
    assert round(gross - credit, 2) == net
    # bridge is a magnitude (>= 0)
    assert credit >= -1e-9


def test_ar_aging_gross_bridge_worked_example(monkeypatch):
    """AR 2024-12 SHAPE — net 41,456 / overdue 60,365 (kEUR) → gross + bridge.

    PA (positive): 60,365,000 EUR long-overdue + 5,000,000 EUR not-yet-due
        → gross 65,365 kEUR, overdue 60,365, before_due 5,000.
    PC (credit):   +100,000 invoice − 24,009,000 payment = −23,909,000 net
        → floored to 0 in the gross basis; nets the headline DOWN.
    signed_total = 65,365,000 − 23,909,000 = 41,456,000 → net headline 41,456 kEUR.
    ⇒ credit_balances = 65,365 − 41,456 = 23,909 ; overdue_pct = 92.4 (<=100).
    """
    from app.services import opos_aging
    rows = [
        _row("PA", 24000, "Bewegung", "RV", 60_365_000.0, date(2024, 1, 1), due=date(2024, 1, 31)),
        _row("PA", 24000, "Bewegung", "RV", 5_000_000.0, date(2024, 12, 15), due=date(2025, 1, 14)),
        _row("PC", 24000, "Bewegung", "RV", 100_000.0, date(2024, 5, 1), due=date(2024, 5, 31)),
        _row("PC", 24000, "Bewegung", "ZA", -24_009_000.0, date(2024, 10, 1)),
    ]
    monkeypatch.setattr(
        opos_aging, "_partner_view",
        lambda session, side, year, month, entity: _synth_partner_view(rows, AS_OF, "AR"),
    )
    res = opos_aging.build_receivables_aging_opos(None, 2024, 12)

    assert res["total_receivables"] == 41456.0    # net headline UNCHANGED
    assert res["total_open_gross"] == 65365.0
    assert res["credit_balances"] == 23909.0
    assert res["kpis"]["before_due"] == 5000.0
    assert res["kpis"]["overdue"] == 60365.0
    assert res["kpis"]["overdue_pct"] == 92.4
    # additive kpi_metrics surfaced for the frontend KPI strip
    assert res["kpi_metrics"]["total_open_gross"]["value"] == 65365.0
    assert res["kpi_metrics"]["credit_balances"]["value"] == 23909.0
    _assert_aging_invariants(res, "total_receivables")


def test_ap_aging_gross_bridge_and_magnitude_sign(monkeypatch):
    """AP mirror — net-open is a CREDIT (negative); magnitude reported positive.

    S1 (payable): Vortrag −700,000 + RV −500,000 (168d overdue) + carry
        → mag 1,200,000 EUR gross; RV → 91–180, Vortrag residual → >180 (A5).
    S2 (debit / supplier-prepaid): +200,000 net → floored out of gross basis.
    signed_total = −1,200,000 + 200,000 = −1,000,000 → total_payables 1,000 kEUR.
    ⇒ gross 1,200 ; credit_balances = 1,200 − 1,000 = 200 (= |prepaid magnitude|).
    """
    from app.services import opos_aging
    rows = [
        _row("S1", 36000, "Vortrag", "SV", -700_000.0, date(2024, 1, 1)),
        _row("S1", 36000, "Bewegung", "RV", -500_000.0, date(2024, 6, 1), due=date(2024, 7, 16)),
        _row("S2", 36000, "Bewegung", "RV", 200_000.0, date(2024, 5, 1), due=date(2024, 5, 31)),
    ]
    monkeypatch.setattr(
        opos_aging, "_partner_view",
        lambda session, side, year, month, entity: _synth_partner_view(rows, AS_OF, "AP"),
    )
    res = opos_aging.build_payables_aging_opos(None, 2024, 12)

    assert res["total_payables"] == 1000.0        # net magnitude UNCHANGED
    assert res["total_open_gross"] == 1200.0
    assert res["credit_balances"] == 200.0
    assert res["kpis"]["overdue"] == 1200.0        # RV 500 + A5 residual 700
    assert res["kpi_metrics"]["credit_balances"]["value"] == 200.0
    _assert_aging_invariants(res, "total_payables")


def test_all_credit_partner_set_gross_zero_bridge_is_net_magnitude(monkeypatch):
    """Edge: every partner is a credit balance → gross 0, pct 0, bridge = |net|."""
    from app.services import opos_aging
    rows = [
        _row("PC", 24000, "Bewegung", "RV", 100_000.0, date(2024, 5, 1), due=date(2024, 5, 31)),
        _row("PC", 24000, "Bewegung", "ZA", -600_000.0, date(2024, 10, 1)),
    ]  # net −500,000 → credit balance, floored to 0 gross
    monkeypatch.setattr(
        opos_aging, "_partner_view",
        lambda session, side, year, month, entity: _synth_partner_view(rows, AS_OF, "AR"),
    )
    res = opos_aging.build_receivables_aging_opos(None, 2024, 12)

    assert res["total_open_gross"] == 0.0
    assert res["kpis"]["overdue"] == 0.0
    assert res["kpis"]["overdue_pct"] == 0.0
    assert res["total_receivables"] == -500.0                 # net headline (nets down)
    assert res["credit_balances"] == 500.0                    # = |net magnitude|
    _assert_aging_invariants(res, "total_receivables")


def test_sv_vortrag_residual_counted_in_gross(monkeypatch):
    """Edge: A5 carried-forward (SV) residual with no covering RV/RG still enters
    the gross basis (→ overdue_over_180), so Σ series == total_open_gross holds."""
    from app.services import opos_aging
    rows = [
        _row("PV", 24000, "Vortrag", "SV", 800_000.0, date(2024, 1, 1)),  # no RV/RG pool
    ]
    monkeypatch.setattr(
        opos_aging, "_partner_view",
        lambda session, side, year, month, entity: _synth_partner_view(rows, AS_OF, "AR"),
    )
    res = opos_aging.build_receivables_aging_opos(None, 2024, 12)

    assert res["total_open_gross"] == 800.0
    assert res["kpis"]["overdue"] == 800.0        # residual → overdue_over_180
    assert res["credit_balances"] == 0.0          # no credit partners → net == gross
    _assert_aging_invariants(res, "total_receivables")
