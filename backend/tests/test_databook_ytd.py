"""YTD period labels and SuSa master column logic from Date Settings."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for p in (ROOT, BACKEND):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from databook_periods import (  # noqa: E402
    compute_databook_grid_labels,
    fallback_yearly_columns,
    master_ytd_label,
    ytd_reporting_fy_end_year,
)
from SuSabyYear import build_final_output, finalize_periods  # noqa: E402


class TestDatabookYtdLabels(unittest.TestCase):
    def test_july_2025_ltm_december_fy_end_adds_ytd2025(self):
        labels = compute_databook_grid_labels(2022, "2025-7", 12, 31)
        self.assertEqual(labels[:-1], ["FY2022", "FY2023", "FY2024"])
        self.assertEqual(labels[-1], "YTD2025")
        self.assertEqual(ytd_reporting_fy_end_year("2025-7", 12, 31), 2025)
        self.assertEqual(master_ytd_label(2025), "YTD25A")

    def test_december_ltm_on_fy_end_has_no_ytd(self):
        labels = compute_databook_grid_labels(2022, "2024-12", 12, 31)
        self.assertNotIn("YTD2024", labels)
        self.assertEqual(labels[-1], "FY2024")
        self.assertIsNone(ytd_reporting_fy_end_year("2024-12", 12, 31))

    def test_fallback_yearly_columns_appends_ytd(self):
        cols = fallback_yearly_columns(
            {
                "first_fy": 2022,
                "ltm_month": "2025-7",
                "fy_end_month": 12,
                "fy_end_day": 31,
            }
        )
        self.assertEqual(cols[-1], "YTD25A")
        self.assertIn("FY24A", cols)


class TestSuSaYtdColumn(unittest.TestCase):
    def _long_frame(self) -> pd.DataFrame:
        rows = []
        for month in range(1, 8):
            rows.append(
                {
                    "Entity": "E1",
                    "Account": "4000",
                    "Account description": "Revenue",
                    "L1 - BS/PL": "PL",
                    "L2": "L2",
                    "L3": "L3",
                    "L4": "L4",
                    "L5": "",
                    "L6": "Reported",
                    "NA": None,
                    "Account Type": "PL",
                    "Year": 2025,
                    "Month": month,
                    "Balance": 100.0,
                    "Source FY": 2025,
                    "Source Group": "tb",
                    "Source Order": month,
                }
            )
        rows.append(
            {
                "Entity": "E1",
                "Account": "1000",
                "Account description": "Cash",
                "L1 - BS/PL": "BS",
                "L2": "L2",
                "L3": "L3",
                "L4": "L4",
                "L5": "",
                "L6": "Reported",
                "NA": "CA",
                "Account Type": "BS",
                "Year": 2025,
                "Month": 7,
                "Balance": 5000.0,
                "Source FY": 2025,
                "Source Group": "tb",
                "Source Order": 7,
            }
        )
        return finalize_periods(pd.DataFrame(rows), fiscal_start_month=1)

    def test_build_final_output_adds_ytd25a(self):
        df_long = self._long_frame()
        master_bs, master_pl = build_final_output(
            df_long,
            fiscal_start_month=1,
            value_type="movements",
            fy_end_month=12,
            ytd_reporting_fy=2025,
            ytd_ltm_year=2025,
            ytd_ltm_month=7,
        )
        self.assertIn("YTD25A", master_pl.columns)
        self.assertIn("YTD25A", master_bs.columns)
        rev = master_pl.loc[master_pl["Account"] == "4000", "YTD25A"].iloc[0]
        self.assertEqual(float(rev), -700.0)
        cash = master_bs.loc[master_bs["Account"] == "1000", "YTD25A"].iloc[0]
        self.assertEqual(float(cash), 5000.0)


if __name__ == "__main__":
    unittest.main()
