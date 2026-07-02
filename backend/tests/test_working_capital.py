"""Working capital row structure and period helpers."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
BACKEND = ROOT / "backend"
for p in (ROOT, SCRIPTS, BACKEND):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from databook_periods import (  # noqa: E402
    group_month_columns_by_reporting_fy,
    ordered_month_columns_from_df,
    parse_month_period_column,
)
from report_row_layout import (  # noqa: E402
    WC_BUCKET_LABELS,
    build_working_capital_row_structure,
    compute_monthly_avg_sort_metric,
    sort_by_signed_monthly_avg,
)


def _wc_master_fixture() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Entity": ["E1"] * 6,
            "L2": ["CA"] * 6,
            "L3": [
                "Inventories",
                "Inventories",
                "Trade receivables",
                "Trade payables",
                "Prepayments",
                "Prepayments",
            ],
            "L4": [
                "Finished goods",
                "Raw materials",
                "Trade receivables",
                "Trade payables",
                "Prepayments",
                "Accruals",
            ],
            "NA": ["TWC", "TWC", "TWC", "TWC", "OWC", "OWC"],
            "L6": ["Reported"] * 6,
            "Jan-2024": [100.0, 50.0, 200.0, -80.0, 30.0, 10.0],
            "Feb-2024": [110.0, 40.0, 180.0, -90.0, 25.0, 12.0],
            "Mar-2024": [120.0, 30.0, 220.0, -70.0, 20.0, 15.0],
        }
    )


class TestWorkingCapitalPeriods(unittest.TestCase):
    def test_month_columns_from_master(self):
        cols = ordered_month_columns_from_df(_wc_master_fixture())
        self.assertEqual(cols, ["Jan-2024", "Feb-2024", "Mar-2024"])

    def test_parse_month_period_column(self):
        self.assertEqual(parse_month_period_column("Jan-2024"), (2024, 1))

    def test_group_month_columns_by_fy(self):
        groups = group_month_columns_by_reporting_fy(
            ["Jan-2024", "Feb-2024", "Mar-2024"], fy_end_month=12
        )
        self.assertEqual(list(groups.keys()), [2024])
        self.assertEqual(len(groups[2024]), 3)


class TestWorkingCapitalRowStructure(unittest.TestCase):
    def setUp(self):
        self.master = _wc_master_fixture()
        self.month_cols = ordered_month_columns_from_df(self.master)
        self.l3_order = [
            "Inventories",
            "Trade receivables",
            "Trade payables",
            "Prepayments",
        ]

    def _structure(self):
        return build_working_capital_row_structure(
            self.master,
            self.l3_order,
            {},
            source_col="L6",
            bucket_col="NA",
            normalize_bucket_fn=lambda x: str(x).strip(),
        )

    def test_row_structure_twc_before_owc(self):
        struct = self._structure()
        labels = [r["label"] for r in struct]
        twc_idx = labels.index(WC_BUCKET_LABELS["TWC"])
        owc_idx = labels.index(WC_BUCKET_LABELS["OWC"])
        self.assertLess(twc_idx, owc_idx)

    def test_display_order_l4_before_l3_subtotal(self):
        struct = self._structure()
        inv_l4_labels = {"Finished goods", "Raw materials"}
        inv_sub_idx = next(
            i for i, r in enumerate(struct) if r["type"] == "subtotal_l3" and r["label"] == "Inventories"
        )
        l4_indices = [
            i for i, r in enumerate(struct) if r["type"] == "detail" and r["label"] in inv_l4_labels
        ]
        self.assertTrue(all(i < inv_sub_idx for i in l4_indices))

    def test_sort_by_signed_monthly_avg(self):
        metrics = {"A": 50.0, "B": 120.0, "C": -200.0}
        self.assertEqual(sort_by_signed_monthly_avg(["A", "B", "C"], metrics)[0], "B")

    def test_l4_sort_metric_signed_mean(self):
        m_high = compute_monthly_avg_sort_metric(
            self.master,
            self.month_cols,
            "L6",
            "NA",
            "TWC",
            "Inventories",
            l4="Finished goods",
            normalize_bucket_fn=lambda x: str(x).strip(),
        )
        m_low = compute_monthly_avg_sort_metric(
            self.master,
            self.month_cols,
            "L6",
            "NA",
            "TWC",
            "Inventories",
            l4="Raw materials",
            normalize_bucket_fn=lambda x: str(x).strip(),
        )
        self.assertGreater(m_high, m_low)

    def test_nwc_row_type(self):
        struct = self._structure()
        nwc = [r for r in struct if r["type"] == "total_nwc"]
        self.assertEqual(len(nwc), 1)
        self.assertEqual(nwc[0]["label"], "Net working capital")

    def test_na_bucket_rows_exist(self):
        struct = self._structure()
        na_rows = [r for r in struct if r["type"] == "total_na"]
        self.assertEqual(len(na_rows), 2)
        self.assertEqual(na_rows[0]["bucket"], "TWC")

    def test_single_label_column_structure(self):
        struct = self._structure()
        detail = [r for r in struct if r["type"] == "detail"]
        self.assertTrue(all("label" in r and "L3" in r for r in detail))
        self.assertTrue(all("NA" in r for r in detail))


class TestWorkingCapitalFormulas(unittest.TestCase):
    def test_days_in_month_formula(self):
        from databook_periods import days_in_month_formula

        self.assertIn("EOMONTH", days_in_month_formula("Jan-2024"))
        self.assertIn("DATE(2024,1,1)", days_in_month_formula("Jan-2024"))


if __name__ == "__main__":
    unittest.main()
