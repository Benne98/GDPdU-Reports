"""Regression tests — forecast_engine.py golden constants (Tier L2).

=== WHAT IS LOCKED ===
Anchor: base_fy=2024, forecast_fy=2025, L=7 (last closed period).

PL fixture (NET_SALES):
  base_2024 monthly = {1:100,2:110,3:120,4:90,5:130,6:140,7:150,8:160,9:155,10:170,11:180,12:200} (annual 1705)
  ytd_2025 P1-7 sum = 910 ({1..7: 130 each})
  g=0.0  → plan P8-12 = {160,155,170,180,200}; fy_f = 910+865 = 1775
  g=0.03 → plan P8-12 = base*1.03; fy_f = 1800.95

BS fixture (P7 natural, assets+, L&E+):
  Cash=500, AR=300, Inv=200, PPE=1000 | AP=250, Debt=600, Share=400, RE=750 (Σ=2000 each)
  drivers DSO=60, DPO=50, DIO=60 (days=360)
  pl_forecast: revenue_f=2160, COGS_f=1440, NI_f=140
  AR_P12=360, Inv_P12=240, AP_P12=200, RE_P12=890, Cash_P12=490; A_P12=(L+E)_P12=2090

WC from BS P12: NWC=400, DSO=60, DPO=50, DIO=60.

CF indirect: ΔAR=+60,ΔInv=+40,ΔAP=-50 → ΔNWC=150; CFO=-10; EndingCash=490.

=== STRUCTURE ===
 1-13. TestPlForecastSeasonal / TestBsForecast / TestWcForecast / TestCfForecast — pure
        (DB-free) calculator assertions, 16 named methods.
   14. TestCrossTies — assert_cross_ties golden pass + three violation cases.
   15. TestGoldenIsolation — load_position_plan_map_pref fallback logic (monkeypatched).
   16. TestInProcessV2 — in-process calls to build_statement_plan_response against
        finssentials_v2 (skipped unless that DB is reachable).
"""
from __future__ import annotations

import os
from datetime import date
from unittest.mock import MagicMock

import pytest

from app.services.forecast_engine import (
    assert_cross_ties,
    forecast_bs_rollforward,
    forecast_cf_indirect,
    forecast_pl_seasonal,
    forecast_wc_from_bs,
    fy_forecast_by_line,
    weekly_flow_split,
    weekly_stock_interp,
)

_EPS = 1e-6

# =========================================================================== #
# GOLDEN FIXTURES
# =========================================================================== #

# PL — base_fy=2024, L=7
_BASE_2024 = {
    "NET_SALES": {
        1: 100.0, 2: 110.0, 3: 120.0, 4:  90.0, 5: 130.0, 6: 140.0,
        7: 150.0, 8: 160.0, 9: 155.0, 10: 170.0, 11: 180.0, 12: 200.0,
    }
}
# annual = 1705

_YTD_2025 = {
    "NET_SALES": {1: 130.0, 2: 130.0, 3: 130.0, 4: 130.0, 5: 130.0, 6: 130.0, 7: 130.0}
}
# P1-7 sum = 910

# BS — P7 natural magnitudes (both sides positive book values)
_BS_P7 = {
    "Cash": 500.0, "AR": 300.0, "Inv": 200.0, "PPE": 1000.0,  # assets
    "AP": 250.0, "Debt": 600.0, "Share": 400.0, "RE": 750.0,   # L&E
}
_SIDES = {
    "Cash": "asset", "AR": "asset", "Inv": "asset", "PPE": "asset",
    "AP": "le",     "Debt": "le",   "Share": "le",  "RE": "le",
}
_ROLES = {"AR": "AR", "INV": "Inv", "AP": "AP", "RE": "RE", "CASH": "Cash"}
_DRIVERS = {"DSO": 60.0, "DPO": 50.0, "DIO": 60.0}
_PL_FC = {"revenue_f": 2160.0, "COGS_f": 1440.0, "NI_f": 140.0}


def _run_bs(pl_fc=None):
    """Run the BS roll-forward with default (or supplied) PL forecast."""
    return forecast_bs_rollforward(
        _BS_P7, _SIDES, _ROLES, _DRIVERS, pl_fc or _PL_FC, days=360.0
    )


# =========================================================================== #
# 1-5. P&L seasonal forecast
# =========================================================================== #

class TestPlForecastSeasonal:

    def test_pl_forecast_seasonal_not_flat(self):
        """P8-12 plan is 5 DISTINCT monthly values from the base, NOT a flat run-rate."""
        plan = forecast_pl_seasonal(_BASE_2024, _YTD_2025, L=7, g=0.0)
        p8_12 = plan["NET_SALES"]
        # Exact golden values: base months verbatim
        assert p8_12 == {
            8: pytest.approx(160.0, abs=_EPS),
            9: pytest.approx(155.0, abs=_EPS),
            10: pytest.approx(170.0, abs=_EPS),
            11: pytest.approx(180.0, abs=_EPS),
            12: pytest.approx(200.0, abs=_EPS),
        }
        # Not a flat value (5 distinct amounts)
        assert len(set(round(v, 6) for v in p8_12.values())) == 5

    def test_pl_forecast_fy_equals_ytd_plus_ytg(self):
        """fy_f = ytd + plan_P8_12 equals the two golden constants 1775 and 1800.95."""
        # g=0.0: plan_P8_12 = 160+155+170+180+200 = 865; fy_f = 910+865 = 1775
        fy_g0 = fy_forecast_by_line(_BASE_2024, _YTD_2025, L=7, g=0.0)
        assert fy_g0["NET_SALES"] == pytest.approx(1775.0, abs=_EPS)

        # g=0.03: plan_P8_12 = 865 * 1.03 = 890.95; fy_f = 910 + 890.95 = 1800.95
        fy_g3 = fy_forecast_by_line(_BASE_2024, _YTD_2025, L=7, g=0.03)
        expected_ytg = (160 + 155 + 170 + 180 + 200) * 1.03  # 890.95
        assert fy_g3["NET_SALES"] == pytest.approx(910.0 + expected_ytg, abs=_EPS)
        assert fy_g3["NET_SALES"] == pytest.approx(1800.95, abs=0.01)

    def test_pl_forecast_seasonal_share_sums_one(self):
        """Seasonal shares Σ==1 (normal path); base-absent and Σ=0 both use uniform 1/12 fallback."""
        # --- normal path: shares of _BASE_2024 sum to 1 ---
        annual_base = sum(_BASE_2024["NET_SALES"].values())  # 1705
        total_share = sum(
            _BASE_2024["NET_SALES"][p] / annual_base for p in range(1, 13)
        )
        assert total_share == pytest.approx(1.0, abs=_EPS)

        # --- base-absent → run-rate fallback gives uniform open months ---
        # ytd P1-7 = 7*120 = 840; run_rate = (840/7)*12 = 1440; per-period = 120
        ytd_only = {"NEW": {p: 120.0 for p in range(1, 8)}}
        plan_absent = forecast_pl_seasonal({}, ytd_only, L=7, g=0.0)
        for p in range(8, 13):
            assert plan_absent["NEW"][p] == pytest.approx(120.0, abs=_EPS), f"P{p}"

        # --- Σ=0 base → run-rate fallback (no div/0) ---
        # ytd P1-7 = 7*14 = 98; run_rate = (98/7)*12 = 168; per-period = 14
        base_zero = {"ZERO": {p: 0.0 for p in range(1, 13)}}
        ytd_nz = {"ZERO": {p: 14.0 for p in range(1, 8)}}
        plan_zero = forecast_pl_seasonal(base_zero, ytd_nz, L=7, g=0.0)
        for p in range(8, 13):
            assert plan_zero["ZERO"][p] == pytest.approx(14.0, abs=_EPS), f"P{p}"

    def test_pl_forecast_negative_line_sign_preserved(self):
        """Negative-signed expense lines keep their sign throughout the forecast."""
        base_cogs = {
            "COGS": {
                1: -80.0,  2: -88.0,  3: -96.0,  4:  -72.0,
                5: -104.0, 6: -112.0, 7: -120.0, 8: -128.0,
                9: -124.0, 10: -136.0, 11: -144.0, 12: -160.0,
            }
        }
        ytd_cogs = {"COGS": {p: -90.0 for p in range(1, 8)}}
        plan = forecast_pl_seasonal(base_cogs, ytd_cogs, L=7, g=0.0)
        # All open-period plan values must be negative
        for p in range(8, 13):
            assert plan["COGS"][p] < 0.0, f"P{p} should be negative (expense line)"
        # g=0: plan(c,p) = base(c,p) exactly
        assert plan["COGS"][8] == pytest.approx(-128.0, abs=_EPS)
        assert plan["COGS"][12] == pytest.approx(-160.0, abs=_EPS)

    def test_forecast_not_equal_ytd_when_plan_present__pl(self):
        """When plan is present (ytg != 0), fy_f differs from ytd."""
        fy = fy_forecast_by_line(_BASE_2024, _YTD_2025, L=7, g=0.0)
        ytd = sum(_YTD_2025["NET_SALES"].values())  # 910
        # fy_f = 1775 >> ytd = 910: plan adds 865 for the open periods
        assert fy["NET_SALES"] != pytest.approx(ytd, abs=_EPS)
        assert fy["NET_SALES"] > ytd


# =========================================================================== #
# 6-9. Balance-sheet roll-forward
# =========================================================================== #

class TestBsForecast:

    def test_bs_forecast_p8_12_nonzero(self):
        """All P8-12 path values for the driven + plug lines are non-zero."""
        path = _run_bs()["path"]
        for line in ("AR", "Inv", "AP", "RE", "Cash"):
            for p in range(8, 13):
                assert abs(path[line][p]) > _EPS, f"{line} P{p} must be non-zero"

    def test_bs_forecast_balances_at_horizon(self):
        """A_P12 == (L+E)_P12 == 2090; Cash_P12 == 490; AR=360, Inv=240, AP=200."""
        closing = _run_bs()["closing"]
        assert closing["AR"] == pytest.approx(360.0, abs=_EPS)
        assert closing["Inv"] == pytest.approx(240.0, abs=_EPS)
        assert closing["AP"] == pytest.approx(200.0, abs=_EPS)
        assert closing["Cash"] == pytest.approx(490.0, abs=_EPS)

        assets = sum(closing[k] for k in closing if _SIDES[k] == "asset")
        le = sum(closing[k] for k in closing if _SIDES[k] == "le")
        assert assets == pytest.approx(le, abs=_EPS)
        assert assets == pytest.approx(2090.0, abs=_EPS)

    def test_bs_forecast_re_moves_by_net_income(self):
        """RE_P12 = RE_P7 + NI_f = 750 + 140 = 890."""
        closing = _run_bs()["closing"]
        assert closing["RE"] == pytest.approx(890.0, abs=_EPS)

    def test_bs_forecast_loss_reduces_equity(self):
        """Loss case NI_f=-140 → RE_P12 = 750-140 = 610; balance identity still holds."""
        pl_loss = {**_PL_FC, "NI_f": -140.0}
        closing = _run_bs(pl_fc=pl_loss)["closing"]
        assert closing["RE"] == pytest.approx(610.0, abs=_EPS)

        assets = sum(closing[k] for k in closing if _SIDES[k] == "asset")
        le = sum(closing[k] for k in closing if _SIDES[k] == "le")
        assert assets == pytest.approx(le, abs=_EPS)


# =========================================================================== #
# 10-11. Working-capital ratios
# =========================================================================== #

class TestWcForecast:

    def test_wc_forecast_dso_dpo_dio_coherent(self):
        """From BS P12 (AR=360,Inv=240,AP=200): NWC=400, DSO=60, DPO=50, DIO=60."""
        wc = forecast_wc_from_bs(360.0, 240.0, 200.0, 2160.0, 1440.0, days=360.0)
        assert wc["NWC"] == pytest.approx(400.0, abs=_EPS)
        assert wc["DSO"] == pytest.approx(60.0, abs=_EPS)
        assert wc["DPO"] == pytest.approx(50.0, abs=_EPS)
        assert wc["DIO"] == pytest.approx(60.0, abs=_EPS)

    def test_wc_forecast_zero_denominator_none(self):
        """Zero revenue_f → DSO=None; zero COGS_f → DPO=None, DIO=None (no div/0)."""
        # Zero revenue: DSO undefined; DPO/DIO still valid if COGS non-zero
        wc_nr = forecast_wc_from_bs(300.0, 200.0, 100.0, 0.0, 1440.0, days=360.0)
        assert wc_nr["DSO"] is None, "DSO must be None when revenue_f == 0"
        assert wc_nr["DPO"] is not None

        # Zero COGS: DPO and DIO undefined; DSO still valid if revenue non-zero
        wc_nc = forecast_wc_from_bs(300.0, 200.0, 100.0, 2160.0, 0.0, days=360.0)
        assert wc_nc["DPO"] is None, "DPO must be None when COGS_f == 0"
        assert wc_nc["DIO"] is None, "DIO must be None when COGS_f == 0"
        assert wc_nc["DSO"] is not None

        # Both zero: all ratios undefined
        wc_both = forecast_wc_from_bs(300.0, 200.0, 100.0, 0.0, 0.0, days=360.0)
        assert wc_both["DSO"] is None
        assert wc_both["DPO"] is None
        assert wc_both["DIO"] is None


# =========================================================================== #
# 12-13. Indirect cash-flow
# =========================================================================== #

class TestCfForecast:

    def test_cf_forecast_dnwc_sign(self):
        """AR increase → negative CFO contribution (ΔNWC>0 ⇒ CFO < NI_f)."""
        # AR7=300→AR12=360 (+60); Inv7=200→Inv12=240 (+40); AP7=250→AP12=200 (−50)
        # ΔNWC = 60+40−(−50) = 150 > 0 → CFO = NI_f - ΔNWC = 140-150 = -10 < 140
        cf = forecast_cf_indirect(
            ar7=300.0, inv7=200.0, ap7=250.0,
            ar12=360.0, inv12=240.0, ap12=200.0,
            ni_f=140.0, cash7=500.0,
        )
        # AR increase is a cash OUTFLOW → leaf is negative
        assert cf["Change in trade receivables"] == pytest.approx(-60.0, abs=_EPS)
        assert cf["Change in trade receivables"] < 0.0
        # CFO is below NI_f when ΔNWC > 0
        assert cf["CFO subtotal"] < cf["Net income"]
        assert cf["CFO subtotal"] == pytest.approx(-10.0, abs=_EPS)

    def test_cf_forecast_ties_to_pl_bs(self):
        """CFO=-10; EndingCash=490 ties to BS Cash_P12=490; all leaves correct."""
        cf = forecast_cf_indirect(
            ar7=300.0, inv7=200.0, ap7=250.0,
            ar12=360.0, inv12=240.0, ap12=200.0,
            ni_f=140.0, cash7=500.0,
        )
        assert cf["CFO subtotal"] == pytest.approx(-10.0, abs=_EPS)
        assert cf["Net change in cash"] == pytest.approx(-10.0, abs=_EPS)
        assert cf["Cash at end"] == pytest.approx(490.0, abs=_EPS)  # ties to BS Cash_P12

        # Presented leaf signs (indirect method)
        assert cf["Change in trade receivables"] == pytest.approx(-60.0, abs=_EPS)  # −ΔAR
        assert cf["Change in inventories"] == pytest.approx(-40.0, abs=_EPS)        # −ΔInv
        assert cf["Change in trade payables"] == pytest.approx(-50.0, abs=_EPS)     # +ΔAP = +(−50)


# =========================================================================== #
# 14-15. Weekly disaggregation
# =========================================================================== #

class TestWeeklyDisagg:

    def test_weekly_flow_sums_back_to_month(self):
        """Σ week_plan over weeks that tile a month == month_plan; straddling week splits correctly."""
        # July 2025 = 31 days; plan = 310 (10 per day, exact arithmetic)
        plan_jul = {(2025, 7): 310.0}
        weeks_tile = [
            {"key": "W1", "start": date(2025, 7,  1), "end": date(2025, 7,  7)},  # 7d
            {"key": "W2", "start": date(2025, 7,  8), "end": date(2025, 7, 14)},  # 7d
            {"key": "W3", "start": date(2025, 7, 15), "end": date(2025, 7, 21)},  # 7d
            {"key": "W4", "start": date(2025, 7, 22), "end": date(2025, 7, 28)},  # 7d
            {"key": "W5", "start": date(2025, 7, 29), "end": date(2025, 7, 31)},  # 3d
        ]
        result_tile = weekly_flow_split(plan_jul, weeks_tile)
        assert sum(result_tile.values()) == pytest.approx(310.0, abs=_EPS)

        # Straddling week CW31 (Jul28–Aug03): 4 July days + 3 August days
        plan_two = {(2025, 7): 310.0, (2025, 8): 248.0}  # Aug=31 days
        straddle = [{"key": "CW31", "start": date(2025, 7, 28), "end": date(2025, 8, 3)}]
        result_s = weekly_flow_split(plan_two, straddle)
        expected = 310.0 * 4 / 31 + 248.0 * 3 / 31  # = 64.0 exactly
        assert result_s["CW31"] == pytest.approx(expected, abs=_EPS)

    def test_weekly_stock_interpolates_month_ends(self):
        """Stock interp: month-end cutoff == balance; pre-first == p7_carry; interior between."""
        # month-end balances: Jul-31=100, Aug-31=200
        balances = {(2025, 7): 100.0, (2025, 8): 200.0}
        p7_carry = 80.0

        # Before first month-end → p7_carry
        pre = [{"key": "pre", "cutoff": date(2025, 7, 30)}]
        assert weekly_stock_interp(balances, pre, p7_carry)["pre"] == pytest.approx(p7_carry, abs=_EPS)

        # Exactly at Jul-31 → 100 (not p7_carry)
        at_jul = [{"key": "julend", "cutoff": date(2025, 7, 31)}]
        assert weekly_stock_interp(balances, at_jul, p7_carry)["julend"] == pytest.approx(100.0, abs=_EPS)

        # Aug-14: linear interp between Jul-31 (100) and Aug-31 (200)
        # frac = (Aug14-Jul31).days / (Aug31-Jul31).days = 14/31
        mid = [{"key": "mid", "cutoff": date(2025, 8, 14)}]
        expected_mid = 100.0 + (200.0 - 100.0) * 14.0 / 31.0
        result_mid = weekly_stock_interp(balances, mid, p7_carry)["mid"]
        assert result_mid == pytest.approx(expected_mid, abs=_EPS)
        assert 100.0 < result_mid < 200.0  # strictly between

        # At/after last month-end → 200
        at_aug = [{"key": "augend", "cutoff": date(2025, 8, 31)}]
        assert weekly_stock_interp(balances, at_aug, p7_carry)["augend"] == pytest.approx(200.0, abs=_EPS)


# =========================================================================== #
# 16. Cross-tie assertions
# =========================================================================== #

class TestCrossTies:

    def _golden_inputs(self):
        """Compute the full set of cross-tie inputs from the golden fixtures."""
        bs_result = _run_bs()
        closing = bs_result["closing"]
        assets = sum(closing[k] for k in closing if _SIDES[k] == "asset")
        le = sum(closing[k] for k in closing if _SIDES[k] == "le")
        cf = forecast_cf_indirect(
            ar7=_BS_P7["AR"],  inv7=_BS_P7["Inv"],  ap7=_BS_P7["AP"],
            ar12=closing["AR"], inv12=closing["Inv"], ap12=closing["AP"],
            ni_f=_PL_FC["NI_f"], cash7=_BS_P7["Cash"],
        )
        return closing, assets, le, cf

    def test_assert_cross_ties_golden_passes(self):
        """All three identities hold on the golden fixture (no exception raised)."""
        closing, assets, le, cf = self._golden_inputs()
        # Must NOT raise
        assert_cross_ties(
            re_p7=_BS_P7["RE"],    re_p12=closing["RE"],
            ni_f=_PL_FC["NI_f"],
            cf_ending_cash=cf["Cash at end"],  bs_cash_p12=closing["Cash"],
            assets_p12=assets,                 le_p12=le,
        )

    def test_cross_ties_re_violation_raises(self):
        """Perturbed RE_P12 → AssertionError on RE roll-forward tie."""
        closing, assets, le, cf = self._golden_inputs()
        with pytest.raises(AssertionError, match="RE roll-forward"):
            assert_cross_ties(
                re_p7=_BS_P7["RE"], re_p12=closing["RE"] + 1.0,  # perturb
                ni_f=_PL_FC["NI_f"],
                cf_ending_cash=cf["Cash at end"], bs_cash_p12=closing["Cash"],
                assets_p12=assets, le_p12=le,
            )

    def test_cross_ties_cash_violation_raises(self):
        """Perturbed CF EndingCash → AssertionError on cash tie."""
        closing, assets, le, cf = self._golden_inputs()
        with pytest.raises(AssertionError, match="Cash tie"):
            assert_cross_ties(
                re_p7=_BS_P7["RE"], re_p12=closing["RE"],
                ni_f=_PL_FC["NI_f"],
                cf_ending_cash=999.0,           # wrong EndingCash
                bs_cash_p12=closing["Cash"],
                assets_p12=assets, le_p12=le,
            )

    def test_cross_ties_balance_violation_raises(self):
        """Perturbed Assets_P12 → AssertionError on balance identity."""
        closing, assets, le, cf = self._golden_inputs()
        with pytest.raises(AssertionError, match="Balance identity"):
            assert_cross_ties(
                re_p7=_BS_P7["RE"], re_p12=closing["RE"],
                ni_f=_PL_FC["NI_f"],
                cf_ending_cash=cf["Cash at end"], bs_cash_p12=closing["Cash"],
                assets_p12=assets + 100.0, le_p12=le,  # perturb assets
            )

    def test_loss_case_cross_ties_still_hold(self):
        """NI_f=-140 (loss): RE_P12=610; all three identities still hold."""
        pl_loss = {**_PL_FC, "NI_f": -140.0}
        closing = _run_bs(pl_fc=pl_loss)["closing"]
        assets = sum(closing[k] for k in closing if _SIDES[k] == "asset")
        le = sum(closing[k] for k in closing if _SIDES[k] == "le")
        cf = forecast_cf_indirect(
            ar7=_BS_P7["AR"],  inv7=_BS_P7["Inv"],  ap7=_BS_P7["AP"],
            ar12=closing["AR"], inv12=closing["Inv"], ap12=closing["AP"],
            ni_f=-140.0, cash7=_BS_P7["Cash"],
        )
        # RE_P12 = 750 + (−140) = 610
        assert closing["RE"] == pytest.approx(610.0, abs=_EPS)
        # assert_cross_ties must not raise
        assert_cross_ties(
            re_p7=_BS_P7["RE"], re_p12=closing["RE"],
            ni_f=-140.0,
            cf_ending_cash=cf["Cash at end"], bs_cash_p12=closing["Cash"],
            assets_p12=assets, le_p12=le,
        )


# =========================================================================== #
# GOLDEN ISOLATION — load_position_plan_map_pref fallback logic
# =========================================================================== #

class TestGoldenIsolation:
    """Locks the LOGIC-LEVEL scenario isolation of load_position_plan_map_pref.

    On finssentials_v2, the DB DOES have forecast rows, so we monkeypatch
    load_position_plan_map to control what each scenario returns.  This proves
    the fallback chain at the code level, independent of the DB state.
    """

    def test_pref_falls_back_to_budget_when_forecast_empty(self, monkeypatch):
        """Forecast read returns {} → pref falls back to budget map (identity)."""
        import app.services.fin_compat_pl as _mod

        budget_map = {"NET_SALES": {"plan_cm": 100.0, "ytd_plan": 700.0, "ytg": 0.0}}
        call_log: list[str] = []

        def _fake_load(session, statement, year, month, ent_frag, *,
                       scenario="budget", prefixes=None):
            call_log.append(scenario)
            if scenario == "forecast":
                return {}       # simulate: no forecast rows
            return budget_map   # budget has rows

        monkeypatch.setattr(_mod, "load_position_plan_map", _fake_load)
        sess = MagicMock()

        result = _mod.load_position_plan_map_pref(sess, "PL", 2025, 7, "")

        assert result == budget_map, "pref must return budget map when forecast has no signal"
        assert "forecast" in call_log, "forecast scenario must be tried first"
        assert "budget" in call_log, "budget must be tried as fallback"
        assert call_log.index("forecast") < call_log.index("budget"), \
            "forecast must be attempted BEFORE budget"

    def test_pref_uses_forecast_when_present(self, monkeypatch):
        """Forecast read has signal → pref returns forecast; budget is never called."""
        import app.services.fin_compat_pl as _mod

        forecast_map = {"NET_SALES": {"plan_cm": 200.0, "ytd_plan": 1400.0, "ytg": 0.0}}
        budget_map = {"NET_SALES": {"plan_cm": 100.0, "ytd_plan": 700.0, "ytg": 0.0}}
        call_log: list[str] = []

        def _fake_load(session, statement, year, month, ent_frag, *,
                       scenario="budget", prefixes=None):
            call_log.append(scenario)
            if scenario == "forecast":
                return forecast_map
            return budget_map

        monkeypatch.setattr(_mod, "load_position_plan_map", _fake_load)
        sess = MagicMock()

        result = _mod.load_position_plan_map_pref(sess, "PL", 2025, 7, "")

        assert result == forecast_map, "pref must return forecast when it has signal"
        assert "forecast" in call_log
        assert "budget" not in call_log, "budget must NOT be called when forecast has signal"

    def test_pref_forwards_prefixes_to_both_scenarios(self, monkeypatch):
        """The prefixes kwarg is forwarded verbatim to both scenario reads."""
        import app.services.fin_compat_pl as _mod

        seen_prefixes: list = []

        def _fake_load(session, statement, year, month, ent_frag, *,
                       scenario="budget", prefixes=None):
            seen_prefixes.append((scenario, prefixes))
            return {}  # always no signal → both tries logged

        monkeypatch.setattr(_mod, "load_position_plan_map", _fake_load)
        sess = MagicMock()
        test_prefixes = ["AA", "BB"]

        _mod.load_position_plan_map_pref(sess, "BS", 2025, 7, "",
                                         prefixes=test_prefixes)

        assert ("forecast", test_prefixes) in seen_prefixes
        assert ("budget", test_prefixes) in seen_prefixes


# =========================================================================== #
# IN-PROCESS v2 VERIFICATION
# =========================================================================== #
# Gated: only runs when finssentials_v2 is reachable (set DB_PASSWORD env var;
# the test patches the DB name in the URL so the module-level engine is NOT used).
# Run: $env:DB_PASSWORD='Marmelade_1!'; python -m pytest -q backend/tests/test_forecast_engine.py -k TestInProcessV2

def _v2_session_or_skip():
    """Return a raw Session bound to finssentials_v2, or pytest.skip()."""
    try:
        from sqlalchemy import create_engine
        from sqlalchemy import text as _text
        from sqlalchemy.orm import Session as _Session
        from app.config import settings

        url = settings.database_url
        if "finssentials_v2" not in url:
            url = url.rsplit("/", 1)[0] + "/finssentials_v2"
        eng = create_engine(url, connect_args={"connect_timeout": 3})
        s = _Session(eng)
        row = s.execute(
            _text("SELECT 1 FROM fact_position_plan WHERE scenario='forecast' LIMIT 1")
        ).fetchone()
        if not row:
            s.close()
            pytest.skip("finssentials_v2: no scenario='forecast' rows in fact_position_plan")
        return s
    except Exception as exc:
        pytest.skip(f"finssentials_v2 not reachable: {exc}")


class TestInProcessV2:
    """In-process verification that build_statement_plan_response reads the seeded
    forecast scenario correctly.  The live :8011 API is STALE until restart — this
    calls the service functions directly against finssentials_v2.

    Skips automatically when the DB is unreachable or has no forecast rows.
    To enable: set DB_PASSWORD env var (password is never persisted to a file).
    """

    def test_has_plan_data_all_four_statements(self, capsys):
        """All four statements return has_plan_data=True with forecast rows seeded."""
        from app.services.fin_compat_pl import build_statement_plan_response

        session = _v2_session_or_skip()
        try:
            _YEAR, _MONTH = 2025, 7  # anchor: base_fy=2024, forecast_fy=2025, L=7
            results: dict = {}
            for stmt in ("PL", "BS", "WC", "CF"):
                resp = build_statement_plan_response(
                    session, stmt, _YEAR, _MONTH, entity=None, allowed_prefixes=None
                )
                results[stmt] = resp
                assert resp["has_plan_data"] is True, (
                    f"{stmt}: expected has_plan_data=True "
                    f"(sample lines: {resp['lines'][:2]})"
                )

            # Report sample non-zero lines (visible in pytest -s or capsys.readouterr)
            for stmt, resp in results.items():
                nonzero = [
                    l for l in resp["lines"]
                    if abs(l.get("plan_cm") or 0) > 1e-6 or abs(l.get("ytg") or 0) > 1e-6
                ]
                print(
                    f"\n[v2] {stmt}: has_plan_data={resp['has_plan_data']}, "
                    f"{len(nonzero)} non-zero-plan lines"
                )
                for line in nonzero[:3]:
                    print(
                        f"  {line['line_code']}: "
                        f"plan_cm={line['plan_cm']}, ytg={line['ytg']}"
                    )
        finally:
            session.close()

    def test_pl_forecast_fy_not_equal_ytd(self):
        """PL response has at least one line with non-zero ytg (plan for open periods)."""
        from app.services.fin_compat_pl import build_statement_plan_response

        session = _v2_session_or_skip()
        try:
            resp = build_statement_plan_response(
                session, "PL", 2025, 7, entity=None, allowed_prefixes=None
            )
            has_ytg = any(abs(l.get("ytg") or 0) > 1e-6 for l in resp["lines"])
            assert has_ytg, (
                "PL forecast response must have YTG != 0 for at least one line "
                "(forecast rows seeded for P8-12 should produce non-zero ytg)"
            )
        finally:
            session.close()

    def test_bs_cf_wc_non_empty_plan_lines(self):
        """BS, CF, WC each have at least one line with non-zero plan_cm (pre-fix gap closed)."""
        from app.services.fin_compat_pl import build_statement_plan_response

        session = _v2_session_or_skip()
        try:
            for stmt in ("BS", "CF", "WC"):
                resp = build_statement_plan_response(
                    session, stmt, 2025, 7, entity=None, allowed_prefixes=None
                )
                assert len(resp["lines"]) > 0, f"{stmt}: expected non-empty lines"
                nonzero = [
                    l for l in resp["lines"]
                    if abs(l.get("plan_cm") or 0) > 1e-6
                ]
                assert len(nonzero) > 0, (
                    f"{stmt}: expected at least one line with non-zero plan_cm "
                    f"(forecast rows seeded)"
                )
        finally:
            session.close()
