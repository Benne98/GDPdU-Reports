"""Churn bridge period list from first_fy and LTM date settings."""
import unittest

from Churn import bridge_year_pairs, normalize_config, resolve_fy_years


class TestChurnPeriods(unittest.TestCase):
    def test_july_2025_ltm_two_bridges(self):
        cfg = normalize_config({
            "fy_end_year": 2025,
            "fy_end_month": 12,
            "fy_end_day": 31,
            "first_fy": 2023,
            "current_year": 2025,
            "current_month": 7,
            "value_col": "v",
            "customer_col": "c",
            "product_col": "p",
            "start_col": "s",
            "end_col": "e",
            "invoice_col": "i",
            "group_cols": ["Entity"],
        })
        first, latest = resolve_fy_years(cfg)
        self.assertEqual(first, 2023)
        self.assertEqual(latest, 2024)
        self.assertEqual(bridge_year_pairs(cfg), [(2023, 2024)])

    def test_december_on_fy_end_includes_current_fy(self):
        cfg = normalize_config({
            "fy_end_year": 2024,
            "fy_end_month": 12,
            "fy_end_day": 31,
            "first_fy": 2022,
            "current_year": 2024,
            "current_month": 12,
            "value_col": "v",
            "customer_col": "c",
            "product_col": "p",
            "start_col": "s",
            "end_col": "e",
            "invoice_col": "i",
            "group_cols": ["Entity"],
        })
        _, latest = resolve_fy_years(cfg)
        self.assertEqual(latest, 2024)
        self.assertEqual(bridge_year_pairs(cfg), [(2022, 2023), (2023, 2024)])

    def test_three_year_span_three_bridges(self):
        cfg = normalize_config({
            "fy_end_year": 2025,
            "fy_end_month": 12,
            "fy_end_day": 31,
            "first_fy": 2022,
            "current_year": 2025,
            "current_month": 12,
            "value_col": "v",
            "customer_col": "c",
            "product_col": "p",
            "start_col": "s",
            "end_col": "e",
            "invoice_col": "i",
            "group_cols": ["Entity"],
        })
        pairs = bridge_year_pairs(cfg)
        self.assertEqual(pairs, [(2022, 2023), (2023, 2024), (2024, 2025)])

    def test_normalize_fills_fy_end_year_from_as_of(self):
        cfg = normalize_config({
            "fy_end_month": 12,
            "fy_end_day": 31,
            "first_fy": 2023,
            "current_year": 2025,
            "current_month": 7,
            "value_col": "v",
            "customer_col": "c",
            "product_col": "p",
            "start_col": "s",
            "end_col": "e",
            "invoice_col": "i",
            "group_cols": ["Entity"],
        })
        self.assertEqual(cfg["fy_end_year"], 2024)


if __name__ == "__main__":
    unittest.main()
