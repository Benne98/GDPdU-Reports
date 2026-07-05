"""Accelerator-equivalence tests for the Overview v2 mart (P3).

Proves the ``mart_overview`` pre-aggregation returns numbers IDENTICAL to the
existing live builders (``overview_metrics.build_ebit_rows`` /
``build_ebit_table``'s internal grain), plus fail-closed visibility.

=== WORKED EXAMPLE (year=2025, month=3) ===
Raw GL P&L lines (amount = RAW stored sign; presented = amount * -1):

  entity fy    period level_2  level_3            amount   presented
  AT     2025  1      Income   Net sales          -100      +100
  AT     2025  2      Income   Net sales          -100      +100
  AT     2025  3      Income   Net sales          -100      +100
  AT     2025  3      Expense  Cost of materials   +40       -40
  AT     2025  3      Expense  Financial result    +25       -25   (NOT in EBIT)
  AT     2024  3      Income   Net sales           -80       +80
  DE     2025  3      Income   Net sales           -50       +50

Expected AT grain: to_cm=100, to_ytd=300, to_cm_py=80,
  ebit_cm = Net sales(+100) + Cost of materials(-40) = 60  (Financial result excluded)
  ebit_ytd = 300 (Net sales YTD) - 40 (COM period3) = 260
Expected __total__ (AT+DE): to_cm=150, ebit_cm = 60 + 50 = 110.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import pytest

from app.services import mart_overview as M
from app.services.overview_metrics import build_ebit_rows


# ---------------------------------------------------------------------------
# Synthetic raw GL P&L dataset
# ---------------------------------------------------------------------------
def _pl_rows() -> list[dict]:
    def r(ep, fy, fp, l2, l3, amt):
        return {"entity_prefix": ep, "fiscal_year": fy, "fiscal_period": fp,
                "level_0": "PL", "level_2": l2, "level_3": l3, "amount": amt}
    return [
        r("AT", 2025, 1, "Income", "Net sales", -100.0),
        r("AT", 2025, 2, "Income", "Net sales", -100.0),
        r("AT", 2025, 3, "Income", "Net sales", -100.0),
        r("AT", 2025, 3, "Expense", "Cost of materials", 40.0),
        r("AT", 2025, 3, "Expense", "Financial result", 25.0),  # excluded from EBIT
        r("AT", 2024, 3, "Income", "Net sales", -80.0),
        r("DE", 2025, 3, "Income", "Net sales", -50.0),
    ]


NAMES = {"AT": ("AT", "Austria GmbH"), "DE": ("DE", "Germany GmbH")}


# ===========================================================================
# (1) Mart aggregation → grain reproduces the builder worked example
# ===========================================================================
class TestPlEquivalence:

    def _mart_grain(self):
        mart_rows = M.aggregate_pl_periods(self._raw)
        return M.ebit_grain_from_pl_rows(mart_rows, year=2025, month=3)

    _raw = _pl_rows()

    def test_at_grain_values(self):
        grain = {g["entity_prefix"]: g for g in self._mart_grain()}
        at = grain["AT"]
        assert at["to_cm"] == 100.0
        assert at["to_ytd"] == 300.0
        assert at["to_cm_py"] == 80.0
        assert at["ebit_cm"] == 60.0     # 100 - 40 (Financial result excluded)
        assert at["ebit_ytd"] == 260.0   # 300 - 40

    def test_build_ebit_rows_total(self):
        rows = build_ebit_rows(self._mart_grain(), NAMES)
        by = {r["entity_code"]: r for r in rows}
        assert by["AT"]["ebit_cm"] == 60.0
        assert by["DE"]["to_cm"] == 50.0
        assert by["__total__"]["to_cm"] == 150.0
        assert by["__total__"]["ebit_cm"] == 110.0

    def test_mart_equals_direct_from_raw(self):
        """The mart round-trip (aggregate → grain) EQUALS a grain built directly
        from the raw rows — i.e. pre-aggregation is lossless (the accelerator
        returns the SAME numbers the builder path would)."""
        mart_grain = self._mart_grain()
        # "builder truth": skip the mart materialization, reconstruct straight
        # from raw lines (amount, not amount_sum).
        raw_as_mart = [dict(r, amount_sum=r["amount"]) for r in self._raw]
        direct_grain = M.ebit_grain_from_pl_rows(raw_as_mart, year=2025, month=3)
        assert build_ebit_rows(mart_grain, NAMES) == build_ebit_rows(direct_grain, NAMES)


# ===========================================================================
# (2) BS cumulative balance aggregation
# ===========================================================================
class TestBsCumulative:

    def test_cumulative_balances(self):
        mov = [
            {"entity_prefix": "AT", "cutoff_date": date(2025, 1, 31),
             "level_0": "BS", "level_2": "Current assets",
             "level_3": "Cash & cash equivalents", "amount_sum": 100.0},
            {"entity_prefix": "AT", "cutoff_date": date(2025, 2, 28),
             "level_0": "BS", "level_2": "Current assets",
             "level_3": "Cash & cash equivalents", "amount_sum": -30.0},
            {"entity_prefix": "AT", "cutoff_date": date(2025, 3, 31),
             "level_0": "BS", "level_2": "Current assets",
             "level_3": "Cash & cash equivalents", "amount_sum": 50.0},
        ]
        out = M.cumulate_bs_movements(mov)
        by_cut = {r["cutoff_date"]: r["balance_sum"] for r in out}
        assert by_cut[date(2025, 1, 31)] == 100.0
        assert by_cut[date(2025, 2, 28)] == 70.0   # 100 - 30
        assert by_cut[date(2025, 3, 31)] == 120.0  # 70 + 50


# ===========================================================================
# (3) Fail-closed entity visibility
# ===========================================================================
class TestFailClosed:

    def test_aggregate_empty_allowlist_is_nothing(self):
        assert M.aggregate_pl_periods(_pl_rows(), allowed_entities=set()) == []

    def test_grain_empty_allowlist_is_nothing(self):
        rows = M.aggregate_pl_periods(_pl_rows())
        assert M.ebit_grain_from_pl_rows(
            rows, year=2025, month=3, allowed_entities=set()) == []

    def test_cumulate_empty_allowlist_is_nothing(self):
        assert M.cumulate_bs_movements([], allowed_entities=set()) == []

    def test_allowlist_restricts_to_subset(self):
        grain = M.ebit_grain_from_pl_rows(
            M.aggregate_pl_periods(_pl_rows()),
            year=2025, month=3, allowed_entities={"AT"})
        assert [g["entity_prefix"] for g in grain] == ["AT"]

    def test_none_allowlist_is_all(self):
        grain = M.ebit_grain_from_pl_rows(
            M.aggregate_pl_periods(_pl_rows(), allowed_entities=None),
            year=2025, month=3, allowed_entities=None)
        assert {g["entity_prefix"] for g in grain} == {"AT", "DE"}


# ===========================================================================
# (4) read_overview_period fail-closed short-circuit (no DB needed)
# ===========================================================================
class TestReadFailClosed:

    def test_read_empty_allowlist_short_circuits(self):
        # allowed_entities=set() must return empty WITHOUT touching the session.
        class _Boom:
            def execute(self, *a, **k):
                raise AssertionError("must not query the DB when denied")
        out = M.read_overview_period(
            _Boom(), entity=None, year=2025, month=3, allowed_entities=set())
        assert out["grain"] == []

    def test_read_entity_not_in_allowlist_short_circuits(self):
        class _Boom:
            def execute(self, *a, **k):
                raise AssertionError("must not query the DB when denied")
        out = M.read_overview_period(
            _Boom(), entity="DE", year=2025, month=3, allowed_entities={"AT"})
        assert out["grain"] == []


# ===========================================================================
# (5) DB-backed equivalence — mart EBIT rows == live build_ebit_table rows
# ===========================================================================
# Plan §5.2 Lever A: before the :8011 hero trusts the mart, prove it returns
# numbers IDENTICAL to the live builder on REAL data for >=2 entities x
# >=2 periods within a 0.01 kEUR (=10 EUR) tolerance.
#
# This test needs a POPULATED, FRESH mart (run backend/scripts/refresh_mart_overview.py
# against e.g. finssentials_v2 first).  It SKIPS cleanly when no such DB/mart is
# reachable, so the default `pytest -q` gate stays green without a live DB.
_TOL_EUR = 10.0  # 0.01 kEUR
_CMP_FIELDS = ["to_cm_py", "to_pm", "to_cm", "to_ytd",
               "ebit_cm_py", "ebit_pm", "ebit_cm", "ebit_ytd"]


def _mart_db_session():
    """Open an env-driven session; skip the test unless a fresh mart is present.

    The probe itself (connect + freshness + period listing) must NEVER fail the
    gate: an unreachable DB (default env has no v2 DB/password) or an empty/stale
    mart → pytest.skip, so `pytest -q` stays green without a populated mart.
    Correctness is proven when run against a refreshed DB (e.g. finssentials_v2).
    """
    from sqlalchemy import text
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        latest = M.latest_gl_load_id(session)
        if not M.mart_is_fresh(session, latest):
            session.close()
            pytest.skip("mart_overview_period is empty/stale — run refresh_mart_overview.py")
        periods = session.execute(text(
            "SELECT DISTINCT fiscal_year, fiscal_period FROM mart_overview_period "
            "WHERE project_id = 'default' AND level_0 = 'PL' "
            "ORDER BY fiscal_year DESC, fiscal_period DESC"
        )).fetchall()
    except Exception as exc:  # unreachable DB / missing table — env-dependent
        session.close()
        pytest.skip(f"no fresh mart reachable in this env: {exc}")
    if len(periods) < 2:
        session.close()
        pytest.skip("mart has < 2 periods — cannot prove 2x2 equivalence")
    return session, [(int(y), int(p)) for y, p in periods]


class TestMartEqualsLiveOnDb:
    """Mart-backed EBIT grain must equal the live build_ebit_table on real data."""

    def test_mart_equals_live_ebit_2x2(self):
        from app.services.overview_metrics import (
            build_ebit_table,
            build_ebit_rows,
            _ebit_entity_name_map,
        )

        session, periods = _mart_db_session()
        try:
            name_map = _ebit_entity_name_map(session)
            # take the 2 most recent periods that both paths can populate
            checked_periods = 0
            checked_entities = 0
            for year, month in periods:
                live = build_ebit_table(session, None, year, month, allowed_entities=None)
                live_rows = {r.get("entity_code"): r for r in (live.get("rows") or [])
                             if r.get("entity_code") not in (None, "__total__")}
                grain = M.read_overview_period(
                    session, entity=None, year=year, month=month, allowed_entities=None,
                )["grain"]
                mart_rows = {r.get("entity_code"): r
                             for r in build_ebit_rows(grain, name_map)
                             if r.get("entity_code") not in (None, "__total__")}
                common = sorted(set(live_rows) & set(mart_rows))
                if len(common) < 2:
                    continue
                for ec in common:
                    for f in _CMP_FIELDS:
                        a = float(live_rows[ec].get(f) or 0.0)
                        b = float(mart_rows[ec].get(f) or 0.0)
                        assert abs(a - b) <= _TOL_EUR, (
                            f"{ec} {year}-{month:02d} {f}: live={a} mart={b} "
                            f"(|Δ|={abs(a - b):.4f} > {_TOL_EUR} EUR)"
                        )
                    checked_entities += 1
                checked_periods += 1
                if checked_periods >= 2:
                    break

            assert checked_periods >= 2, "need >=2 periods with >=2 comparable entities"
            assert checked_entities >= 2, "need >=2 entities compared"
        finally:
            session.close()


# ===========================================================================
# (6) Phase 2 pure worked-example locks — cash / WC / top-entities readers
# ===========================================================================
# Each new financial value ships with formula + worked example + edge case
# (financial-metric-test rule).  These exercise the PURE arithmetic locks the DB
# readers delegate to, so they run without any DB.


class TestCashHeadlinePure:
    """cash_headline_from_levels — signed cash, NO ABS (overdraft survives).

    FORMULA: level=cm ; delta_month=cm−pm ; delta_yoy=cm−py.
    """

    def test_worked_example(self):
        # cm=120, pm=150, py=90 → level 120, Δm −30, Δyoy +30.
        out = M.cash_headline_from_levels(120.0, 150.0, 90.0)
        assert out == {"level": 120.0, "delta_month": -30.0,
                       "delta_yoy": 30.0, "source": "mart"}

    def test_overdraft_negative_not_floored(self):
        # EDGE: negative cash (overdraft) flows through raw, never ABS/floored.
        out = M.cash_headline_from_levels(-40.0, -10.0, 50.0)
        assert out["level"] == -40.0
        assert out["delta_month"] == -30.0
        assert out["delta_yoy"] == -90.0

    def test_read_cash_deny_short_circuits(self):
        class _Boom:
            def execute(self, *a, **k):
                raise AssertionError("must not query the DB when denied")
        out = M.read_cash_headline(_Boom(), year=2025, month=3, allowed_entities=set())
        assert out == {"level": 0.0, "delta_month": 0.0, "delta_yoy": 0.0,
                       "source": "mart"}


class TestWcSnapshotPure:
    """wc_snapshot_from_balances — DUpont days + NWC + deep-dive levels.

    FORMULA (reuses fin_compat_wc.compute_wc_kpis; ABS the AGGREGATE):
      DSO=|rec|·365/rev_ltm ; DIO=|inv|·365/cogs_ltm ; DPO=|pay|·365/cogs_ltm ;
      CCC=DSO+DIO−DPO ; NWC=Σ RAW(all TWC+OWC) at cm ; level=cm ;
      delta_month=cm−pm ; delta_fy=fy−fy_py.
    """

    _CM = {"Inventories": 4_000_000.0, "Trade receivables": 5_000_000.0,
           "Trade payables": -3_000_000.0, "Other assets": 500_000.0}
    _PM = {"Inventories": 3_500_000.0, "Trade receivables": 4_800_000.0,
           "Trade payables": -2_900_000.0, "Other assets": 500_000.0}
    _FY = {"Inventories": 3_000_000.0, "Trade receivables": 4_000_000.0,
           "Trade payables": -2_500_000.0}
    _FYPY = {"Inventories": 2_000_000.0, "Trade receivables": 3_500_000.0,
             "Trade payables": -2_000_000.0}

    def test_worked_example_kpis_and_nwc(self):
        out = M.wc_snapshot_from_balances(
            self._CM, self._PM, self._FY, self._FYPY,
            rev_ltm=20_000_000.0, cogs_ltm=12_000_000.0)
        # DSO=5e6*365/20e6=91.25→91.2 ; DIO=4e6*365/12e6=121.7 ; DPO=3e6*365/12e6=91.2
        assert out["dso"] == 91.2
        assert out["dio"] == 121.7
        assert out["dpo"] == 91.2
        assert out["ccc"] == round(91.2 + 121.7 - 91.2, 1)  # 121.7
        # NWC = 4e6 + 5e6 − 3e6 + 0.5e6 = 6.5e6 (raw signs)
        assert out["nwc"] == 6_500_000.0

    def test_worked_example_levels(self):
        out = M.wc_snapshot_from_balances(
            self._CM, self._PM, self._FY, self._FYPY,
            rev_ltm=20_000_000.0, cogs_ltm=12_000_000.0)
        lv = {r["key"]: r for r in out["levels"]}
        # inventories: level=cm 4e6 ; Δm=4e6−3.5e6=0.5e6 ; Δfy=3e6−2e6=1e6
        assert lv["inventories"]["level"] == 4_000_000.0
        assert lv["inventories"]["delta_month"] == 500_000.0
        assert lv["inventories"]["delta_fy"] == 1_000_000.0
        # payables stay NEGATIVE (raw sign), no ABS on levels
        assert lv["trade_payables"]["level"] == -3_000_000.0

    def test_edge_zero_cogs_zeros_dio_dpo(self):
        out = M.wc_snapshot_from_balances(
            self._CM, self._PM, self._FY, self._FYPY,
            rev_ltm=20_000_000.0, cogs_ltm=0.0)
        assert out["dio"] == 0.0 and out["dpo"] == 0.0
        assert out["dso"] == 91.2               # revenue side still defined
        assert out["ccc"] == round(91.2 + 0.0 - 0.0, 1)

    def test_read_wc_deny_short_circuits(self):
        class _Boom:
            def execute(self, *a, **k):
                raise AssertionError("must not query the DB when denied")
        out = M.read_wc_snapshot(_Boom(), entity=None, year=2025, month=3,
                                 allowed_entities=set())
        assert out["levels"] == [] and out["nwc"] == 0.0


class TestTopEntitiesPure:
    """aggregate_top_months — month buckets → cm/pm/py_cm/ytd/ytd_py per partner.

    FORMULA / SCALE (Bug 2 fix): rows carry ``amount_eur`` (RAW EUR, exact cents);
    the function sums RAW EUR across the month buckets THEN divides each window by
    1000 exactly ONCE (kEUR).  The single 2dp round is deferred to
    ``rank_top_entities`` (== live ``build_top_entities``, which divides
    ``SUM(value_col)/1000`` per window), so no per-month kEUR pre-rounding drift.

    WORKED EXAMPLE (year=2025, month=3 → cm=Mar25, pm=Feb25, py_cm=Mar24;
    ytd=Jan..Mar 2025 ; ytd_py=Jan..Mar 2024).  amount_eur = 10k..60k EUR →
    cm=60000/1000=60.0 kEUR ; ytd=(40+50+60)k/1000=150.0 kEUR.

    EDGE: a fractional-EUR partner-month (e.g. 12_345.67 EUR) survives as 12.34567
    kEUR unrounded here — only rank_top_entities rounds — so YTD sums exactly.
    """

    def _rows(self):
        from datetime import date as _d
        # partner A across 2024 + 2025 months (RAW EUR magnitudes)
        return [
            {"partner_id": "A", "partner_name": "Acme", "month_end": _d(2024, 1, 31), "amount_eur": 10_000.0},
            {"partner_id": "A", "partner_name": "Acme", "month_end": _d(2024, 2, 29), "amount_eur": 20_000.0},
            {"partner_id": "A", "partner_name": "Acme", "month_end": _d(2024, 3, 31), "amount_eur": 30_000.0},
            {"partner_id": "A", "partner_name": "Acme", "month_end": _d(2025, 1, 31), "amount_eur": 40_000.0},
            {"partner_id": "A", "partner_name": "Acme", "month_end": _d(2025, 2, 28), "amount_eur": 50_000.0},
            {"partner_id": "A", "partner_name": "Acme", "month_end": _d(2025, 3, 31), "amount_eur": 60_000.0},
        ]

    def test_windows(self):
        agg = M.aggregate_top_months(self._rows(), year=2025, month=3,
                                     id_field="customer_id")
        a = {r["customer_id"]: r for r in agg}["A"]
        assert a["cm"] == 60.0          # Mar25 60_000/1000
        assert a["pm"] == 50.0          # Feb25
        assert a["py_cm"] == 30.0       # Mar24
        assert a["ytd"] == 150.0        # (40+50+60)k/1000
        assert a["ytd_py"] == 60.0      # (10+20+30)k/1000
        assert a["name"] == "Acme"

    def test_fractional_eur_not_prerounded(self):
        # EDGE (Bug 2): sub-10-EUR cents survive to the final round.  Two months of
        # 12_345.67 EUR → ytd = (12_345.67 + 12_345.67)/1000 = 24.69134 kEUR, which
        # rounds ONCE (in rank_top_entities) to 24.69 — NOT 2×round(12.34567,2).
        from datetime import date as _d
        rows = [
            {"partner_id": "F", "partner_name": "Frac", "month_end": _d(2025, 1, 31), "amount_eur": 12_345.67},
            {"partner_id": "F", "partner_name": "Frac", "month_end": _d(2025, 2, 28), "amount_eur": 12_345.67},
        ]
        agg = M.aggregate_top_months(rows, year=2025, month=2, id_field="customer_id")
        a = {r["customer_id"]: r for r in agg}["F"]
        assert abs(a["ytd"] - 24.69134) < 1e-9   # unrounded kEUR
        assert a["cm"] == 12_345.67 / 1000.0

    def test_missing_name_falls_back_to_id(self):
        rows = [{"partner_id": "Z", "partner_name": None,
                 "month_end": __import__("datetime").date(2025, 3, 31),
                 "amount_eur": 5_000.0}]
        agg = M.aggregate_top_months(rows, year=2025, month=3, id_field="supplier_id")
        assert agg[0]["name"] == "Z"

    def test_read_top_deny_short_circuits(self):
        class _Boom:
            def execute(self, *a, **k):
                raise AssertionError("must not query the DB when denied")
        out = M.read_top_entities(_Boom(), year=2025, month=3, type="customer",
                                  rank_by="cm", limit=5, allowed_entities=set())
        assert out["rows"] == []


# ===========================================================================
# (7) DB-backed equivalence — Phase 2 pieces (SKIP cleanly w/o v2 mart)
# ===========================================================================
# Cash is WIRED → asserted mart==live within 10 EUR.  WC + top-entities are KEPT
# LIVE (mart out of tolerance); the DB tests below MEASURE + document the exact
# divergence (the matching sub-pieces are asserted; the out-of-tolerance ones are
# recorded, not asserted) so the keep-live decision stays evidenced + green.

_TOL_CASH_EUR = 10.0
_TOL_TOP_KEUR = 0.01
_TOL_WC_DAYS = 0.1    # dso/dpo/dio/ccc tolerance
_TOL_WC_EUR = 10.0    # nwc + level (level/delta_month/delta_fy) tolerance
# IEEE-754 slack for the ``<=`` tolerance compares.  ``mart_overview_top_entities.amount_eur``
# is NUMERIC(20,2) (cents), while the live value_col carries sub-cent precision
# (e.g. 9795.008 EUR); when the two straddle a kEUR rounding boundary the final 2dp
# values differ by EXACTLY the tolerance and the subtraction reads 0.0100000000000015.
# The true monetary divergence is <0.06 EUR (Σ sub-cent over <=12 months), so an
# epsilon-relaxed ``<=`` is the correct compare — a genuine >0.01 kEUR gap still fails.
_EPS = 1e-9


def _mart_db_session_any():
    """Session for the Phase 2 DB tests; skip cleanly without a fresh mart."""
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        latest = M.latest_gl_load_id(session)
        if not M.mart_is_fresh(session, latest):
            session.close()
            pytest.skip("mart empty/stale — run refresh_mart_overview.py")
    except Exception as exc:
        session.close()
        pytest.skip(f"no fresh mart reachable in this env: {exc}")
    return session


class TestCashMartEqualsLiveOnDb:
    """read_cash_headline == overview_summary._cash_headline within 10 EUR (WIRED)."""

    def test_cash_2x2(self):
        from app.services.overview_summary import _cash_headline

        session = _mart_db_session_any()
        try:
            checked = 0
            for year, month in ((2025, 6), (2025, 3), (2024, 12)):
                live = _cash_headline(session, year=year, month=month, ent_frag="")
                mart = M.read_cash_headline(
                    session, year=year, month=month, allowed_entities=None)
                for f in ("level", "delta_month", "delta_yoy"):
                    a = float(live.get(f) or 0.0)
                    b = float(mart.get(f) or 0.0)
                    assert abs(a - b) <= _TOL_CASH_EUR, (
                        f"cash {year}-{month:02d} {f}: live={a} mart={b}")
                checked += 1
            assert checked >= 2
        finally:
            session.close()


def _a_valid_prefix(session) -> Optional[str]:
    """Any populated entity_prefix in the top-entities mart (for restricted scope)."""
    from sqlalchemy import text
    row = session.execute(text(
        "SELECT entity_prefix FROM mart_overview_top_entities "
        "WHERE project_id = 'default' AND entity_prefix <> '' "
        "GROUP BY entity_prefix ORDER BY COUNT(*) DESC LIMIT 1"
    )).fetchone()
    return str(row[0]).strip()[:2] if row and row[0] else None


class TestTopEntitiesMartVsLiveOnDb:
    """Top-entities mart == live within 0.01 kEUR (Bug 2 FIXED, WIRED Phase 4).

    Bug 2 stored ``amount_keur`` = Σ(value_col)/1000 pre-rounded to 2dp kEUR (=10 EUR)
    per (entity_prefix, partner, month); Σ(round(...)) over up to 12 YTD buckets drifted
    ~0.03 kEUR from the live round(Σ(...)) — out of the 0.01 kEUR tolerance, so the mart
    was KEPT LIVE.  The fix stores FULL-PRECISION ``amount_eur`` = Σ(value_col) in exact
    cents; the read path sums EUR across months then /1000-rounds ONCE at the end,
    matching the live builder to the cent.

    FORMULA (per partner window w ∈ {cm,pm,py_cm,ytd,ytd_py}):
        live_w = round(Σ_months∈w value_col / 1000, 2)
        mart_w = round((Σ_months∈w amount_eur) / 1000, 2)   (amount_eur = Σ value_col)
    ⇒ |live_w − mart_w| <= 0.01 kEUR AND rank-1 partner id identical.
    """

    def _compare(self, live_rows, mart_rows, id_field, ptype, year, month):
        mart_by = {str(r.get(id_field)): r for r in mart_rows}
        assert live_rows and mart_by
        # row-0 rank exact: the rank-1 partner id must match.
        assert str(live_rows[0].get(id_field)) == str(mart_rows[0].get(id_field)), (
            f"rank-1 mismatch {ptype} {year}-{month:02d}: "
            f"live={live_rows[0].get(id_field)} mart={mart_rows[0].get(id_field)}")
        compared = 0
        for lr in live_rows:
            mr = mart_by.get(str(lr.get(id_field)))
            if mr is None:
                continue
            for f in ("cm", "pm", "py_cm", "ytd", "ytd_py"):
                d = abs(round(float(mr[f]), 2) - round(float(lr[f]), 2))
                assert d <= _TOL_TOP_KEUR + _EPS, (
                    f"top {ptype} {year}-{month:02d} {f} id={lr.get(id_field)}: "
                    f"live={lr[f]} mart={round(float(mr[f]),2)} (|Δ|={d})")
            compared += 1
        assert compared >= 1, f"no partner_id overlap for {ptype} {year}-{month:02d}"

    def test_read_path_matches_live_admin(self):
        """The FULL read path M.read_top_entities == live build_top_entities."""
        from app.services.overview_top_entities import build_top_entities

        session = _mart_db_session_any()
        try:
            checked = 0
            for ptype in ("customer", "supplier"):
                id_field = "customer_id" if ptype == "customer" else "supplier_id"
                for year, month in ((2025, 6), (2025, 3)):
                    live = build_top_entities(
                        session, year=year, month=month, type=ptype,
                        rank_by="cm", limit=5000, entity=None, allowed_entities=None,
                    )["rows"]
                    mart = M.read_top_entities(
                        session, year=year, month=month, type=ptype,
                        rank_by="cm", limit=5000, allowed_entities=None,
                    )["rows"]
                    if not live or not mart:
                        continue
                    self._compare(live, mart, id_field, ptype, year, month)
                    checked += 1
            assert checked >= 2, "need >=2 (ptype, period) comparisons"
        finally:
            session.close()

    def test_read_path_matches_live_restricted(self):
        """Restricted (fail-closed) scope: both paths filtered to ONE prefix agree."""
        from app.services.overview_top_entities import build_top_entities

        session = _mart_db_session_any()
        try:
            prefix = _a_valid_prefix(session)
            if prefix is None:
                pytest.skip("no populated entity_prefix for restricted-scope test")
            allowed = {prefix}
            checked = 0
            for ptype in ("customer", "supplier"):
                id_field = "customer_id" if ptype == "customer" else "supplier_id"
                for year, month in ((2025, 6), (2025, 3)):
                    live = build_top_entities(
                        session, year=year, month=month, type=ptype,
                        rank_by="cm", limit=5000, entity=None, allowed_entities=allowed,
                    )["rows"]
                    mart = M.read_top_entities(
                        session, year=year, month=month, type=ptype,
                        rank_by="cm", limit=5000, allowed_entities=allowed,
                    )["rows"]
                    if not live or not mart:
                        continue
                    self._compare(live, mart, id_field, ptype, year, month)
                    checked += 1
            assert checked >= 1, "need >=1 restricted comparison"
        finally:
            session.close()


class TestWcMartEqualsLiveOnDb:
    """read_wc_snapshot == the live WC statement/snapshot within tolerance (WIRED).

    The Phase-3 refresh applies the earliest-OB-only dedup so the cumulative WC
    stock reconciles to the live ``build_wc_statement_compat`` / ``build_wc_snapshot_annual``:

    FORMULA / TOLERANCE (per entity scope, per period):
        DSO=|rec|·365/rev_ltm ; DIO=|inv|·365/cogs_ltm ; DPO=|pay|·365/cogs_ltm ;
        CCC=DSO+DIO−DPO       → |mart − live| <= 0.1 days
        NWC=Σ RAW(all TWC+OWC) ; level=cm ; Δmonth=cm−pm ; Δfy=fy−fy_py
                              → |mart − live| <= 10.0 EUR
    WORKED EXAMPLE: see TestWcSnapshotPure (pure formula lock).  EDGE: cogs_ltm≈0 →
    DIO=DPO=0 (both paths), so days still reconcile.
    """

    def _live_wc(self, session, *, year, month, allowed):
        from app.services.overview_summary import _wc_block
        wc, _src = _wc_block(session, year=year, month=month, eff=allowed,
                             use_mart=False)  # force the live builder pair
        return wc

    def _assert_close(self, live, mart, year, month, scope):
        for k in ("dso", "dpo", "dio", "ccc"):
            d = abs(float(live[k]) - float(mart[k]))
            assert d <= _TOL_WC_DAYS + _EPS, (
                f"WC {scope} {year}-{month:02d} {k}: live={live[k]} mart={mart[k]} "
                f"(|Δ|={d:.4f} days)")
        assert abs(float(live["nwc"]) - float(mart["nwc"])) <= _TOL_WC_EUR + _EPS, (
            f"WC {scope} {year}-{month:02d} nwc: live={live['nwc']} mart={mart['nwc']}")
        live_lv = {r["key"]: r for r in live["levels"]}
        mart_lv = {r["key"]: r for r in mart["levels"]}
        for key in ("inventories", "trade_receivables", "trade_payables"):
            lv, mv = live_lv.get(key), mart_lv.get(key)
            if lv is None or mv is None:
                continue
            for f in ("level", "delta_month", "delta_fy"):
                d = abs(float(lv[f]) - float(mv[f]))
                assert d <= _TOL_WC_EUR + _EPS, (
                    f"WC {scope} {year}-{month:02d} {key}.{f}: "
                    f"live={lv[f]} mart={mv[f]} (|Δ|={d:.2f} EUR)")

    def test_read_wc_matches_live_admin(self):
        session = _mart_db_session_any()
        try:
            if not M.mart_wc_is_fresh(session):
                pytest.skip("mart_overview_wc_balance empty/stale")
            checked = 0
            for year, month in ((2025, 6), (2025, 3)):
                mart = M.read_wc_snapshot(
                    session, entity=None, year=year, month=month, allowed_entities=None)
                live = self._live_wc(session, year=year, month=month, allowed=None)
                if not mart["levels"] and not live["levels"]:
                    continue
                self._assert_close(live, mart, year, month, "admin")
                checked += 1
            assert checked >= 2, "need >=2 periods compared"
        finally:
            session.close()

    def test_read_wc_matches_live_restricted(self):
        from sqlalchemy import text
        session = _mart_db_session_any()
        try:
            if not M.mart_wc_is_fresh(session):
                pytest.skip("mart_overview_wc_balance empty/stale")
            row = session.execute(text(
                "SELECT entity_prefix FROM mart_overview_wc_balance "
                "WHERE project_id = 'default' AND entity_prefix <> '' "
                "GROUP BY entity_prefix ORDER BY COUNT(*) DESC LIMIT 1"
            )).fetchone()
            if not row or not row[0]:
                pytest.skip("no populated WC entity_prefix for restricted scope")
            allowed = {str(row[0]).strip()[:2]}
            checked = 0
            for year, month in ((2025, 6), (2025, 3)):
                mart = M.read_wc_snapshot(
                    session, entity=None, year=year, month=month, allowed_entities=allowed)
                live = self._live_wc(session, year=year, month=month, allowed=allowed)
                if not mart["levels"] and not live["levels"]:
                    continue
                self._assert_close(live, mart, year, month, "restricted")
                checked += 1
            assert checked >= 1, "need >=1 restricted period compared"
        finally:
            session.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
