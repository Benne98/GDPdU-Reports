"""Dynamic L4 discovery and amount-based sorting from master data."""
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

from report_row_layout import (  # noqa: E402
    build_bs_row_structure,
    build_pl_row_structure,
    discover_l4_under_l3,
    l2_l3_order_from_mapping,
    l3_order_from_mapping,
    sort_l4_labels,
)


def _master_pl_fixture() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Entity": ["E1", "E1", "E1", "E1"],
            "L3": ["Personnel expenses"] * 4,
            "L4": ["Wages & salaries", "Social security", "Bonuses", "Wages & salaries"],
            "L6": ["Reported"] * 4,
            "FY23A": [100.0, 50.0, 300.0, 100.0],
            "FY24A": [200.0, 40.0, 10.0, 200.0],
            "YTD25A": [30.0, 5.0, 80.0, 30.0],
        }
    )


class TestL4DiscoveryAndSort(unittest.TestCase):
    def test_discover_l4_from_master_ignores_mapping(self):
        master = _master_pl_fixture()
        l4s = discover_l4_under_l3(master, "Personnel expenses", "L6")
        self.assertEqual(set(l4s), {"Wages & salaries", "Social security", "Bonuses"})

    def test_sort_l4_abs_desc(self):
        metrics = {"A": 10.0, "B": -500.0, "C": 50.0}
        self.assertEqual(sort_l4_labels(["A", "B", "C"], metrics), ["B", "C", "A"])

    def test_build_pl_row_structure_all_fy(self):
        master = _master_pl_fixture()
        map_df = pd.DataFrame({"L3": ["Personnel expenses", "Other"]})
        l3_order = l3_order_from_mapping(map_df)
        rows = build_pl_row_structure(
            master,
            l3_order,
            {"l4_sort_basis": "all_fy"},
            source_col="L6",
            display_label_fn=lambda x: x,
        )
        detail_l4s = [r["L4"] for r in rows if r["type"] == "detail"]
        self.assertEqual(detail_l4s, ["Wages & salaries", "Bonuses", "Social security"])

    def test_build_pl_row_structure_latest_fy(self):
        master = _master_pl_fixture()
        rows = build_pl_row_structure(
            master,
            ["Personnel expenses"],
            {"l4_sort_basis": "latest_fy"},
            source_col="L6",
            display_label_fn=lambda x: x,
        )
        detail_l4s = [r["L4"] for r in rows if r["type"] == "detail"]
        self.assertEqual(detail_l4s[0], "Wages & salaries")

    def test_build_pl_row_structure_ytd(self):
        master = _master_pl_fixture()
        rows = build_pl_row_structure(
            master,
            ["Personnel expenses"],
            {"l4_sort_basis": "ytd"},
            source_col="L6",
            display_label_fn=lambda x: x,
        )
        detail_l4s = [r["L4"] for r in rows if r["type"] == "detail"]
        self.assertEqual(detail_l4s[0], "Bonuses")

    def test_build_bs_detail_single(self):
        master = pd.DataFrame(
            {
                "Entity": ["E1"],
                "L2": ["Equity"],
                "L3": ["Equity"],
                "L4": ["Equity"],
                "L6": ["Reported"],
                "FY24A": [1000.0],
            }
        )
        map_df = pd.DataFrame({"L2": ["Equity"], "L3": ["Equity"]})
        order = l2_l3_order_from_mapping(map_df)
        rows = build_bs_row_structure(master, order, {"l4_sort_basis": "latest_fy"}, source_col="L6")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["type"], "detail_single")
        self.assertEqual(rows[1]["type"], "subtotal_l2")


if __name__ == "__main__":
    unittest.main()
