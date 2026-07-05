"""Golden test for the Profitability headline KPI strip (raw-EUR contract).

These KPIs feed the frontend cockpit `financial`-variant KpiCard, which applies a
single ÷1000 (fmtKpi / fmtDelta) to display kEUR. Therefore `build_headline_kpis`
MUST return RAW EUR for every financial-variant field. A previous bug divided by
1000 in SQL, causing a double ÷1000 (net ÷1,000,000) on the cards.

=== SYNTHETIC FACT SET (raw EUR) ===
Current month (2025-07):
  fact_sales.gross_sales     Σ = 250 000 EUR   (rev_cm)
  fact_com.cost_of_materials Σ = 100 000 EUR   (com_cm)
  distinct customers          = 5              (cust_cm)
  distinct invoices           = 10             (inv_cm)
Prior month (2025-06):
  rev_pm = 200 000, com_pm = 90 000, cust_pm = 4, inv_pm = 8

=== EXPECTED (raw EUR) ===
  Gross sales           month_revenue.value          = 250 000  -> fmtKpi -> "250" kEUR
  Gross profit          month_gross_profit.value      = 150 000  -> fmtKpi -> "150" kEUR
  Avg revenue/customer  avg_revenue_per_customer.value = 50 000  -> fmtKpi -> "50"  kEUR/cust
  Gross margin          gross_margin_pct.value         = 60.0 %  (scale-invariant)
  MoM revenue delta     month_revenue.delta_pm         = 50 000  -> fmtDelta -> "€ 50" k
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.profitability_compat import build_headline_kpis

# fact_sales query column order: rev_cm, rev_pm, inv_cm, inv_pm, cust_cm, cust_pm
_SALES_ROW = [250_000.0, 200_000.0, 10, 8, 5, 4]
# fact_com query column order: com_cm, com_pm
_COM_ROW = [100_000.0, 90_000.0]


def _mock_session(sales_row=None, com_row=None, churn_rows=None) -> MagicMock:
    sales_row = _SALES_ROW if sales_row is None else sales_row
    com_row = _COM_ROW if com_row is None else com_row
    # pm_count, lost_count — default 20% logo churn (1 of 5 customers lost PM→CM)
    churn_rows = churn_rows if churn_rows is not None else [[5, 1], [4, 0], [5, 2]]
    churn_iter = iter(churn_rows)

    session = MagicMock()

    def _execute(stmt, params=None):
        sql = str(stmt)
        result = MagicMock()
        if "lost_count" in sql:
            row = next(churn_iter, churn_rows[-1])
            result.fetchone.return_value = row
        elif "fact_com" in sql:
            result.fetchone.return_value = com_row
        elif "fact_sales" in sql:
            result.fetchone.return_value = sales_row
        else:
            result.fetchone.return_value = None
        return result

    session.execute.side_effect = _execute
    return session


def _out(**kw):
    return build_headline_kpis(_mock_session(**kw), year=2025, month=7, entity=None)


class TestHeadlineKpisRawEur:
    def test_gross_sales_is_raw_eur(self):
        """Gross sales = Σ gross_sales(EUR). Raw EUR so the single frontend ÷1000 -> kEUR."""
        out = _out()
        assert out["month_revenue"]["value"] == pytest.approx(250_000.0)
        assert out["week_revenue"]["value"] == pytest.approx(250_000.0)

    def test_gross_profit_is_raw_eur(self):
        """Gross profit = Gross sales − COGS = 250000 − 100000 = 150000 EUR."""
        out = _out()
        assert out["month_gross_profit"]["value"] == pytest.approx(150_000.0)
        assert out["week_gross_profit"]["value"] == pytest.approx(150_000.0)

    def test_avg_revenue_per_customer_is_raw_eur(self):
        """Avg rev/customer = Gross sales / COUNT(DISTINCT customer) = 250000 / 5 = 50000 EUR."""
        out = _out()
        assert out["avg_revenue_per_customer"]["value"] == pytest.approx(50_000.0)
        assert out["week_avg_revenue_per_customer"]["value"] == pytest.approx(50_000.0)

    def test_gross_margin_pct_unchanged_by_eur_switch(self):
        """Margin is scale-invariant: 100 * 150000 / 250000 = 60.0 %."""
        out = _out()
        assert out["gross_margin_pct"]["value"] == pytest.approx(60.0)

    def test_customer_denominator_and_invoice_count(self):
        out = _out()
        assert out["customer_count"]["value"] == pytest.approx(5)
        assert out["units_sold"]["value"] == pytest.approx(10)

    def test_mom_deltas_are_raw_eur(self):
        """delta_pm carried in raw EUR so the frontend's single ÷1000 renders k correctly."""
        out = _out()
        assert out["month_revenue"]["delta_pm"] == pytest.approx(50_000.0)  # 250k - 200k
        assert out["month_gross_profit"]["delta_pm"] == pytest.approx(40_000.0)  # 150k - 110k

    # ---- edge cases -------------------------------------------------------
    def test_zero_customers_guards_denominator(self):
        """No customers -> avg revenue/customer = 0.0, no ZeroDivisionError."""
        out = _out(sales_row=[250_000.0, 0.0, 10, 0, 0, 0])
        assert out["avg_revenue_per_customer"]["value"] == 0.0

    def test_zero_revenue_zeros_margin(self):
        """rev_cm below the 500 EUR meaningful-revenue threshold -> margin 0.0 (no blow-up)."""
        out = _out(sales_row=[0.0, 0.0, 0, 0, 0, 0], com_row=[0.0, 0.0])
        assert out["gross_margin_pct"]["value"] == 0.0
        assert out["month_revenue"]["value"] == 0.0

    def test_sub_threshold_revenue_zeros_margin(self):
        """400 EUR revenue (< 500 EUR threshold) -> margin guarded to 0.0."""
        out = _out(sales_row=[400.0, 0.0, 1, 0, 1, 0], com_row=[100.0, 0.0])
        assert out["gross_margin_pct"]["value"] == 0.0
        # value itself is still the raw EUR figure
        assert out["month_revenue"]["value"] == pytest.approx(400.0)

    def test_negative_gross_profit_when_cogs_exceeds_sales(self):
        """COGS > sales -> negative gross profit in raw EUR (sign preserved)."""
        out = _out(sales_row=[100_000.0, 0.0, 5, 0, 3, 0], com_row=[130_000.0, 0.0])
        assert out["month_gross_profit"]["value"] == pytest.approx(-30_000.0)
        # margin negative: 100 * -30000 / 100000 = -30.0
        assert out["gross_margin_pct"]["value"] == pytest.approx(-30.0)

    def test_churn_rate_logo_churn_pct(self):
        """Churn rate = 100 * lost / pm customers for the PM→CM step."""
        out = _out(churn_rows=[[10, 2], [10, 1], [10, 3]])
        assert out["churn_rate"]["value"] == pytest.approx(20.0)
        assert out["week_churn_rate"]["value"] == pytest.approx(20.0)

    def test_churn_rate_mom_delta_in_percentage_points(self):
        """delta_pm compares current vs prior PM→CM step (percentage points)."""
        out = _out(churn_rows=[[10, 2], [10, 1], [10, 3]])
        # current 20.0% vs prior 10.0% -> +10.0 pp
        assert out["churn_rate"]["delta_pm"] == pytest.approx(10.0)
        # vs prior year 30.0% -> -10.0 pp
        assert out["churn_rate"]["delta_smly"] == pytest.approx(-10.0)

    def test_churn_rate_zero_when_no_pm_customers(self):
        out = _out(churn_rows=[[0, 0], [0, 0], [0, 0]])
        assert out["churn_rate"]["value"] == 0.0
