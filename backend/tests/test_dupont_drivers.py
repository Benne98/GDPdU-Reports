"""Area 7 (Overview v2) — DuPont driver reconciliation sign-off.

Locks the reconciliation that the EXISTING EBIT-based DuPont
(`app.services.overview_metrics.dupont_period_kpis`) actually satisfies, so the
v2 "Driver / DuPont findings" block can re-route the already-computed values
without introducing any new formula.

=== WORKED EXAMPLE (ann_month = 12 → annualisation factor 1) ===
  P&L (presented): net_sales = 1000, cost_of_materials = -600, ebit = 150
  BS (ABS):        total_assets = 1250, equity = 500
    ROS(EBIT margin) = 150/1000       = 15.0 %
    AssetTurnover    = 1000/1250       = 0.80
    EquityMultiplier = 1250/500        = 2.50
    ROE              = 150/500          = 30.0 %
    identity: ROS · AT · EM = 0.15 · 0.80 · 2.50 = 0.30 = ROE  ✓ (within 1e-6)

The identity is EBIT-based (the code has no net income and no ROCE). The plan's
net-income ROE / ROCE = EBIT/(Assets−CurrentLiab) are NET-NEW and deferred to a
separate sign-off — see docs/financial-logic.md "Overview Page v2 — KPI
sign-offs → Area 7".
"""
from __future__ import annotations

import pytest

PL = {"net_sales": 1000.0, "cost_of_materials": -600.0,
      "personnel_expenses": -200.0, "depreciation": -50.0,
      "ebit": 150.0, "ebitda": 200.0}
BS = {"total_assets": 1250.0, "fixed_assets": 500.0, "current_assets": 750.0,
      "trade_receivables": 200.0, "trade_payables": 160.0,
      "inventories": 120.0, "cash": 80.0, "equity": 500.0}


class TestDuPontEbitIdentity:
    """ROE ≡ ROS · AssetTurnover · EquityMultiplier (EBIT-based, ann_month=12)."""

    def _raw_ratios(self, pl: dict, bs: dict, ann_month: int = 12):
        ns = pl["net_sales"]
        ebit = pl["ebit"]
        ta = bs["total_assets"]
        eq = bs["equity"]
        ann_ns = ns * 12 / ann_month
        ros = ebit / ns            # EBIT margin (code's "ros")
        at = ann_ns / ta           # asset turnover
        em = ta / eq               # equity multiplier
        roe = ebit / eq            # EBIT-based ROE (code's "roe")
        return ros, at, em, roe

    def test_identity_reconciles_within_1e6(self):
        ros, at, em, roe = self._raw_ratios(PL, BS, ann_month=12)
        assert ros * at * em == pytest.approx(roe, abs=1e-6)

    def test_matches_worked_example(self):
        ros, at, em, roe = self._raw_ratios(PL, BS, ann_month=12)
        assert ros == pytest.approx(0.15, abs=1e-9)
        assert at == pytest.approx(0.80, abs=1e-9)
        assert em == pytest.approx(2.50, abs=1e-9)
        assert roe == pytest.approx(0.30, abs=1e-9)

    def test_rounded_kpis_match_the_identity_inputs(self):
        """The shipped (rounded) KPI values equal the reconciliation inputs."""
        from app.services.overview_metrics import dupont_period_kpis
        k = dupont_period_kpis(PL, BS, 12)
        assert k["ros"] == 15.0                 # EBIT margin, %
        assert k["asset_turnover"] == 0.8
        assert k["equity_multiplier"] == 2.5
        assert k["roe"] == 30.0                 # EBIT-based ROE, %

    def test_zero_equity_returns_none(self):
        from app.services.overview_metrics import dupont_period_kpis
        k = dupont_period_kpis(PL, dict(BS, equity=0.0, total_assets=0.0), 12)
        assert k["roe"] is None
        assert k["equity_multiplier"] is None
        assert k["asset_turnover"] is None


class TestDuPontHasNoNetIncomeOrRoce:
    """Guard: the code must NOT silently grow a net-income ROE / ROCE.

    If a future change adds these, it MUST come with its own sign-off (a
    current-liabilities BS filter + a net-income figure). This test documents the
    current EBIT-only surface so the divergence stays visible.
    """

    def test_no_roce_or_net_margin_keys(self):
        from app.services.overview_metrics import dupont_period_kpis
        k = dupont_period_kpis(PL, BS, 12)
        assert "roce" not in k
        assert "net_margin" not in k
        assert "net_income" not in k


class TestFindingsReRouteOnly:
    """buildDuPontFindings is a frontend re-router; the Python side ships no
    finding builder today. When one lands it MUST consume existing keys only."""

    def test_future_finding_builder_reuses_existing_keys(self):
        mod = pytest.importorskip("app.services.overview_dupont_findings")
        build = getattr(mod, "build_dupont_findings", None)
        if build is None:
            pytest.skip("build_dupont_findings not implemented yet")
        from app.services.overview_metrics import dupont_period_kpis, assemble_dupont
        kpi = dupont_period_kpis(PL, BS, 12)
        data = assemble_dupont(kpi, kpi, kpi, "Dec24", "Dec23", "Nov24")
        findings = build(data, max=3)
        assert len(findings) <= 3
        allowed = set(data["metrics"].keys())
        for f in findings:
            # a finding may name the driver metric it re-routes, never a new one
            assert f.get("metric", next(iter(allowed))) in allowed
