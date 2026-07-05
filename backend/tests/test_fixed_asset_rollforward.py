"""Golden tests for fixed-asset rollforward aggregation."""
from __future__ import annotations

import unittest

from app.services.fixed_asset_calc import (
    aggregate_rows,
    closing_from_bridge,
    to_keur,
)
from app.services.fixed_asset_rollforward import _walk_dimensions


FIXTURE_ROWS = [
    {
        "opening_nbv": 100_000,
        "additions_zugang": 20_000,
        "disposals_abgang": 5_000,
        "depreciation": -10_000,
        "nbv": 105_000,
        "bilanzposition": "Technische Anlagen und Maschinen",
        "segment": "Buntmetallguss",
    },
    {
        "opening_nbv": 50_000,
        "additions_zugang": 0,
        "disposals_abgang": 0,
        "depreciation": -5_000,
        "nbv": 45_000,
        "bilanzposition": "Grundstücke und Bauten",
        "segment": "Handel",
    },
]


class FixedAssetCalcTests(unittest.TestCase):
    def test_to_keur(self):
        self.assertEqual(to_keur(1500), 1.5)

    def test_aggregate_additions(self):
        self.assertEqual(aggregate_rows(FIXTURE_ROWS, "additions"), 20.0)

    def test_aggregate_closing(self):
        self.assertEqual(aggregate_rows(FIXTURE_ROWS, "closing"), 150.0)

    def test_bridge(self):
        self.assertEqual(closing_from_bridge(100.0, 20.0, 5.0, 10.0), 105.0)

    def test_section_header_has_group_amounts_no_subtotal_rows(self):
        rows_by_year = {
            2024: [
                {
                    **FIXTURE_ROWS[0],
                    "entity_prefix": "01",
                    "asset_id": "1",
                },
            ],
        }
        out: list[dict] = []
        _walk_dimensions(rows_by_year, [2024], ["bilanzposition", "segment"], [], 0, out)
        header = next(r for r in out if r["row_kind"] == "section_header")
        self.assertEqual(header["amounts"]["2024-12-31"], 105.0)
        self.assertFalse(any(r["row_kind"] == "subtotal" for r in out))


if __name__ == "__main__":
    unittest.main()
