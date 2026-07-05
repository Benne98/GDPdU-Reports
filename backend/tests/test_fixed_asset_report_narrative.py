"""Tests for fixed-asset rollforward report narratives."""
from __future__ import annotations

import unittest
from datetime import date

from app.services.fixed_asset_report_narrative import build_rollforward_narrative


def _pos(
    pid: str,
    label: str,
    closing_24: float,
    closing_25: float,
    *,
    assets: list[dict] | None = None,
) -> dict:
    return {
        "id": pid,
        "bilanzposition": label,
        "amounts": {
            "2024-12-31": closing_24,
            "2025-12-31": closing_25,
            "2025-add": 0.0,
            "2025-disp": 0.0,
            "2025-da": 0.0,
        },
        "assets": assets or [],
    }


class FixedAssetReportNarrativeTests(unittest.TestCase):
    def test_material_yoy_positions_receive_numbered_bullets(self) -> None:
        positions = [
            _pos("pos-a", "Grundstuecke", 100.0, 150.0),
            _pos("pos-b", "Andere Anlagen", 500.0, 800.0, assets=[
                {
                    "asset_id": "13410",
                    "label": "13410 | Bagger",
                    "amounts": {
                        "2024-12-31": 200.0,
                        "2025-12-31": 350.0,
                        "2025-add": 180.0,
                        "2025-disp": 0.0,
                        "2025-da": 30.0,
                    },
                },
            ]),
            _pos("pos-c", "Immaterielle", 50.0, 52.0),
        ]
        total = {
            "2024-12-31": 650.0,
            "2025-12-31": 972.0,
        }
        out = build_rollforward_narrative(
            positions,
            anchor_date=date(2025, 12, 31),
            years=[2024, 2025],
            total_amounts=total,
            max_bullets=5,
            use_llm=False,
        )
        self.assertIn("Fixed assets stood at", out["intro"])
        self.assertGreaterEqual(len(out["bullets"]), 2)
        ids = {b["position_id"] for b in out["bullets"]}
        self.assertIn("pos-b", ids)
        self.assertNotIn("pos-c", ids)
        lead = next(b for b in out["bullets"] if b["position_id"] == "pos-b")
        self.assertIn("Andere Anlagen", lead["text"])
        self.assertIn("13410 | Bagger", lead["text"])
        self.assertEqual(out["bullets"][0]["position_id"], "pos-a")

    def test_bullets_keep_table_order(self) -> None:
        positions = [
            _pos("pos-a", "Alpha", 100.0, 200.0),
            _pos("pos-b", "Beta", 100.0, 250.0),
        ]
        total = {"2024-12-31": 200.0, "2025-12-31": 450.0}
        out = build_rollforward_narrative(
            positions,
            anchor_date=date(2025, 12, 31),
            years=[2024, 2025],
            total_amounts=total,
            use_llm=False,
        )
        self.assertEqual([b["position_id"] for b in out["bullets"]], ["pos-a", "pos-b"])


if __name__ == "__main__":
    unittest.main()
