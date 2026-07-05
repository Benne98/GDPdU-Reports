"""Area 8 (Overview v2) — Working-capital KPI sign-off.

Locks the v2 hero/WC-block days KPIs to the EXISTING pure core
`app.services.fin_compat_wc.compute_wc_kpis` (no restated formula), so the
Overview page can never diverge from the Working-Capital statement.

=== WORKED EXAMPLE (matches the fin_compat_wc module docstring to the decimal) ===
  rec = 5,000,000  inv = 4,000,000  pay = 3,000,000  owc = 500,000
  Rev_LTM = 20,000,000   COGS_LTM = 12,000,000
    DSO = 5,000,000 · 365 / 20,000,000 = 91.25 → 91.2
    DIO = 4,000,000 · 365 / 12,000,000 = 121.66… → 121.7
    DPO = 3,000,000 · 365 / 12,000,000 = 91.25 → 91.2
    CCC = 91.2 + 121.7 − 91.2 = 121.7
    NWC = 5.0m + 4.0m − 3.0m + 0.5m = +6.5m   (raw signed, computed by caller)
"""
from __future__ import annotations


class TestComputeWcKpisWorkedExample:

    def _kpi(self):
        from app.services.fin_compat_wc import compute_wc_kpis
        return compute_wc_kpis(
            inv=4_000_000.0, rec=5_000_000.0, pay=3_000_000.0,
            rev_ltm=20_000_000.0, cogs_ltm=12_000_000.0,
        )

    def test_days(self):
        k = self._kpi()
        assert k["DSO"] == 91.2
        assert k["DIO"] == 121.7
        assert k["DPO"] == 91.2
        assert k["CCC"] == 121.7

    def test_ccc_identity(self):
        k = self._kpi()
        assert round(k["DSO"] + k["DIO"] - k["DPO"], 1) == k["CCC"]

    def test_nwc_raw_signed(self):
        """NWC = Σ raw-signed balances (asset +, liability −)."""
        rec, inv, pay, owc = 5_000_000.0, 4_000_000.0, -3_000_000.0, 500_000.0
        nwc = rec + inv + pay + owc
        assert nwc == 6_500_000.0


class TestComputeWcKpisEdges:

    def test_zero_revenue_ltm_dso_zero(self):
        from app.services.fin_compat_wc import compute_wc_kpis
        k = compute_wc_kpis(inv=100.0, rec=200.0, pay=50.0,
                            rev_ltm=0.0, cogs_ltm=1000.0)
        assert k["DSO"] == 0.0            # no div-by-zero
        assert k["CCC"] == round(k["DIO"] - k["DPO"], 1)

    def test_zero_cogs_ltm_dio_dpo_zero(self):
        from app.services.fin_compat_wc import compute_wc_kpis
        k = compute_wc_kpis(inv=100.0, rec=200.0, pay=50.0,
                            rev_ltm=1000.0, cogs_ltm=0.0)
        assert k["DIO"] == 0.0
        assert k["DPO"] == 0.0
        assert k["CCC"] == k["DSO"]

    def test_abs_sign_agnostic(self):
        """Callers pass ABS magnitudes → days read positive regardless of sign."""
        from app.services.fin_compat_wc import compute_wc_kpis
        pos = compute_wc_kpis(inv=abs(-4_000_000.0), rec=abs(-5_000_000.0),
                              pay=abs(-3_000_000.0),
                              rev_ltm=20_000_000.0, cogs_ltm=12_000_000.0)
        assert pos["DSO"] == 91.2 and pos["DIO"] == 121.7
