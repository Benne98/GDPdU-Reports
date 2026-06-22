"""Horizontal merged churn bridge table layout."""
import unittest

import pandas as pd

from Churn import build_merged_horizontal_bridge, build_fy_bridge_tables, normalize_config


def _base_cfg(**overrides):
    cfg = {
        "fy_end_year": 2025,
        "fy_end_month": 12,
        "fy_end_day": 31,
        "first_fy": 2022,
        "current_year": 2025,
        "current_month": 12,
        "value_col": "Amount",
        "customer_col": "Customer",
        "product_col": "Product",
        "start_col": "Start",
        "end_col": "End",
        "invoice_col": "Invoice",
        "group_cols": ["Entity"],
        "total_label": "Total",
    }
    cfg.update(overrides)
    return normalize_config(cfg)


def _mini_df() -> pd.DataFrame:
    return pd.DataFrame({
        "Entity": ["A", "B"],
        "Customer": ["C1", "C2"],
        "Product": ["P1", "P2"],
        "Amount": [12000.0, 24000.0],
        "Start": pd.to_datetime(["2022-01-01", "2023-01-01"]),
        "End": pd.to_datetime(["2025-12-31", "2025-12-31"]),
        "Invoice": pd.to_datetime(["2022-01-01", "2023-01-01"]),
    })


class TestChurnHorizontalLayout(unittest.TestCase):
    def test_merged_columns_extend_horizontally(self):
        cfg = _base_cfg()
        merged = build_merged_horizontal_bridge(_mini_df(), cfg)
        bridges = build_fy_bridge_tables(_mini_df(), cfg)
        n_bridges = len(bridges)
        self.assertGreaterEqual(n_bridges, 2)
        fy_cols = [c for c in merged.columns if str(c).startswith("FY")]
        self.assertEqual(len(fy_cols), n_bridges + 1)
        upsell_cols = [c for c in merged.columns if c == "Upsell"]
        self.assertEqual(len(upsell_cols), n_bridges)

    def test_no_duplicate_start_fy_between_segments(self):
        cfg = _base_cfg()
        merged = build_merged_horizontal_bridge(_mini_df(), cfg)
        cols = list(merged.columns)
        fy_positions = [i for i, c in enumerate(cols) if str(c).startswith("FY")]
        for i in range(len(fy_positions) - 1):
            self.assertLess(fy_positions[i], fy_positions[i + 1])
        self.assertIn("row_type", cols)

    def test_row_union_includes_total(self):
        cfg = _base_cfg()
        merged = build_merged_horizontal_bridge(_mini_df(), cfg)
        self.assertIn("total", merged["row_type"].values)


if __name__ == "__main__":
    unittest.main()
