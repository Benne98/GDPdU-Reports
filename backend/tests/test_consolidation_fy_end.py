"""Fiscal-year derivation in Consolidation.py (fy_end_month, not hard-coded December)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import Consolidation as cons  # noqa: E402


class TestConsolidationFyEnd(unittest.TestCase):
    def test_reporting_fy_july_year_end(self):
        self.assertEqual(cons.reporting_fy_from_period(2023, 8, 7), 2024)
        self.assertEqual(cons.reporting_fy_from_period(2024, 7, 7), 2024)
        self.assertEqual(cons.reporting_fy_from_period(2024, 8, 7), 2025)

    def test_bs_uses_july_not_december(self):
        part = pd.DataFrame(
            {
                "Aug-2023": [1],
                "Jul-2024": [100],
                "Dec-2023": [999],
            }
        )
        month_cols = ["Aug-2023", "Dec-2023", "Jul-2024"]
        derived = cons.derive_fy_values_from_months(
            part=part,
            month_cols=month_cols,
            l1_value="BS",
            target_headers=["FY24A"],
            fy_end_month=7,
            fiscal_start_month=8,
        )
        self.assertEqual(derived, ["FY24A"])
        self.assertEqual(part.loc[0, "FY24A"], 100)

    def test_pl_sums_fiscal_year_months(self):
        part = pd.DataFrame(
            {
                "Aug-2023": [10],
                "Sep-2023": [20],
                "Jul-2024": [30],
                "Aug-2024": [1000],
            }
        )
        month_cols = ["Aug-2023", "Sep-2023", "Jul-2024", "Aug-2024"]
        derived = cons.derive_fy_values_from_months(
            part=part,
            month_cols=month_cols,
            l1_value="PL",
            target_headers=["FY24A", "FY25A"],
            fy_end_month=7,
            fiscal_start_month=8,
        )
        self.assertEqual(derived, ["FY24A", "FY25A"])
        self.assertEqual(part.loc[0, "FY24A"], 60)
        self.assertEqual(part.loc[0, "FY25A"], 1000)


if __name__ == "__main__":
    unittest.main()
