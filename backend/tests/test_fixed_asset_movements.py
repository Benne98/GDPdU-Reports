"""Tests for fixed-asset movements chart helpers."""
from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from app.services.fixed_asset_dimensions import format_asset_description, row_dim_display
from app.services.fixed_asset_movements import (
    _build_nbv_rollforward_bridge,
    _group_add_disp,
    _group_nbv_by_dimension,
)


class FixedAssetMovementsTests(unittest.TestCase):
    def test_format_asset_description_id_only(self):
        self.assertEqual(format_asset_description("13410", None), "13410")

    def test_row_dim_display_asset(self):
        rec = {"asset_id": "10042", "asset_label": "CNC milling machine"}
        self.assertEqual(row_dim_display(rec, "asset"), "10042 | CNC milling machine")

    def test_group_add_disp_asset_label(self):
        rows = [
            {
                "asset_id": "10042",
                "asset_label": "CNC milling machine",
                "additions_zugang": 10_000,
                "disposals_abgang": 0,
                "depreciation": -5_000,
            },
        ]
        out = _group_add_disp(rows, "asset")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["label"], "10042 | CNC milling machine")
        self.assertEqual(out[0]["additions"], 10.0)
        self.assertEqual(out[0]["depreciation"], 5.0)

    def test_group_nbv_by_dimension_segment(self):
        anchor = [
            {"segment": "A", "nbv": 100_000},
            {"segment": "B", "nbv": 50_000},
        ]
        prior = [
            {"segment": "A", "nbv": 80_000},
            {"segment": "B", "nbv": 60_000},
        ]
        out = _group_nbv_by_dimension(anchor, prior, "segment")
        by_cat = {r["category"]: r for r in out}
        self.assertEqual(by_cat["A"]["nbv"], 100.0)
        self.assertEqual(by_cat["A"]["prior_nbv"], 80.0)

    @patch("app.services.fixed_asset_movements._fetch_rows")
    def test_multi_year_bridge_columns(self, mock_fetch):
        def side_effect(_session, as_of, _entity, _project_id):
            year = as_of.year
            nbv = 100_000 + (year - 2022) * 10_000
            return [{
                "additions_zugang": 20_000,
                "disposals_abgang": 5_000,
                "depreciation": -10_000,
                "nbv": nbv,
                "entity_prefix": "01",
                "segment": "Alpha",
            }]

        mock_fetch.side_effect = side_effect
        out = _build_nbv_rollforward_bridge(
            MagicMock(),
            opening=date(2022, 12, 31),
            closing=date(2025, 12, 31),
            entity=None,
            project_id="default",
            dimension=None,
        )
        totals = [c for c in out["columns"] if c["kind"] == "total"]
        movements = [c for c in out["columns"] if c["kind"] == "movements"]
        self.assertEqual(len(totals), 4)
        self.assertEqual(len(movements), 3)
        self.assertEqual(out["opening_label"], "Dec22A")
        self.assertEqual(out["closing_label"], "Dec25A")

    @patch("app.services.fixed_asset_movements._fetch_rows")
    def test_bridge_legs_float_and_tie(self, mock_fetch):
        # disposals/depreciation stored NEGATIVE (as in real data).
        def side_effect(_session, as_of, _entity, _project_id):
            year = as_of.year
            if year == 2022:
                return [{"additions_zugang": 0, "disposals_abgang": 0,
                         "depreciation": 0, "nbv": 100_000,
                         "entity_prefix": "01", "segment": "Alpha"}]
            return [{"additions_zugang": 30_000, "disposals_abgang": -5_000,
                     "depreciation": -10_000, "nbv": 115_000,
                     "entity_prefix": "01", "segment": "Alpha"}]

        mock_fetch.side_effect = side_effect
        out = _build_nbv_rollforward_bridge(
            MagicMock(), opening=date(2022, 12, 31), closing=date(2023, 12, 31),
            entity=None, project_id="default", dimension=None,
        )
        cols = out["columns"]
        open_total = cols[0]
        mov = next(c for c in cols if c["kind"] == "movements")
        close_total = cols[-1]
        self.assertEqual(open_total["base"], 0.0)
        self.assertEqual(open_total["value"], 100.0)
        legs = {leg["bar_type"]: leg for leg in mov["legs"]}
        # Additions float from opening 100 upward.
        self.assertEqual(legs["add"]["base"], 100.0)
        self.assertEqual(legs["add"]["delta"], 30.0)
        # Disposals/D&A are signed negative and their base is the lower edge.
        self.assertEqual(legs["disp"]["delta"], -5.0)
        self.assertEqual(legs["da"]["delta"], -10.0)
        # Running total: 100 + 30 - 5 - 10 = 115 == closing pillar.
        running = open_total["value"] + sum(leg["delta"] for leg in mov["legs"])
        self.assertAlmostEqual(running, close_total["value"], places=1)
        self.assertEqual(close_total["value"], 115.0)

    @patch("app.services.fixed_asset_movements._fetch_rows")
    def test_bridge_dimension_groups(self, mock_fetch):
        mock_fetch.return_value = [
            {
                "additions_zugang": 30_000,
                "disposals_abgang": 0,
                "depreciation": -5_000,
                "nbv": 125_000,
                "entity_prefix": "01",
                "segment": "Alpha",
            },
            {
                "additions_zugang": 10_000,
                "disposals_abgang": 2_000,
                "depreciation": -3_000,
                "nbv": 55_000,
                "entity_prefix": "02",
                "segment": "Beta",
            },
        ]
        out = _build_nbv_rollforward_bridge(
            MagicMock(),
            opening=date(2024, 12, 31),
            closing=date(2025, 12, 31),
            entity=None,
            project_id="default",
            dimension="segment",
        )
        dim_cols = [c for c in out["columns"] if c["kind"] == "movements_by_dimension"]
        self.assertEqual(len(dim_cols), 1)
        self.assertEqual(len(dim_cols[0]["groups"]), 2)
        self.assertEqual(out["scope_label"], "Business segment")


if __name__ == "__main__":
    unittest.main()
