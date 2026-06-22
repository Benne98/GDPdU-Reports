"""Churn top-bucket modes (threshold + number)."""
import unittest

import pandas as pd

from Churn import apply_top_buckets, finalize_bridge_layout, normalize_config


def _sample_bridge() -> pd.DataFrame:
    cfg = normalize_config({
        "fy_end_year": 2024,
        "fy_end_month": 12,
        "fy_end_day": 31,
        "value_col": "v",
        "customer_col": "c",
        "product_col": "p",
        "start_col": "s",
        "end_col": "e",
        "invoice_col": "i",
        "group_cols": ["Entity"],
        "total_label": "Total",
    })
    raw = pd.DataFrame({
        "group_0": ["A", "B", "C", "D"],
        "FY23A": [100, 200, 300, 400],
        "Upsell": [0, 0, 0, 0],
        "Downsell": [0, 0, 0, 0],
        "Cross-sell": [0, 0, 0, 0],
        "Lost": [0, 0, 0, 0],
        "NRR": [0, 0, 0, 0],
        "New": [0, 0, 0, 0],
        "FY24A": [1000, 2000, 3000, 4000],
        "row_type": ["leaf"] * 4,
    })
    return finalize_bridge_layout(
        raw.rename(columns={"group_0": "Entity"}),
        cfg,
    )


class TestChurnBuckets(unittest.TestCase):
    def test_number_mode_buckets(self):
        bridge = _sample_bridge()
        cfg = normalize_config({
            "fy_end_year": 2024,
            "fy_end_month": 12,
            "fy_end_day": 31,
            "value_col": "v",
            "customer_col": "c",
            "product_col": "p",
            "start_col": "s",
            "end_col": "e",
            "invoice_col": "i",
            "group_cols": ["Entity"],
            "top_bucket": {
                "enabled": True,
                "bucket_mode": "number",
                "numbers": (2, 1),
                "create_other_bucket": True,
                "other_bucket_label": "Rest",
            },
        })
        out = apply_top_buckets(bridge, cfg)
        labels = out.loc[out["row_type"] == "bucket", "Entity"].tolist()
        self.assertIn("Top 2", labels)
        self.assertIn("Top 3", labels)
        self.assertIn("Rest", labels)

    def test_threshold_mode_dynamic_count(self):
        bridge = _sample_bridge()
        cfg = normalize_config({
            "fy_end_year": 2024,
            "fy_end_month": 12,
            "fy_end_day": 31,
            "value_col": "v",
            "customer_col": "c",
            "product_col": "p",
            "start_col": "s",
            "end_col": "e",
            "invoice_col": "i",
            "group_cols": ["Entity"],
            "top_bucket": {
                "enabled": True,
                "bucket_mode": "threshold",
                "thresholds": (0.25, 0.75),
                "create_other_bucket": False,
            },
        })
        out = apply_top_buckets(bridge, cfg)
        bucket_rows = out[out["row_type"] == "bucket"]
        self.assertGreaterEqual(len(bucket_rows), 2)


if __name__ == "__main__":
    unittest.main()
