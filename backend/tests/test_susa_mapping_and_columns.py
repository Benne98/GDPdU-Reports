"""SuSa mapping path fallback and Master_BS/Master_PL column layout."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from susa_mapping_paths import (  # noqa: E402
    BS_FILENAME,
    PL_FILENAME,
    finssentials_sibling_mapping_dir,
    resolve_susa_kontenmapping_paths,
)
from SuSabyYear import (  # noqa: E402
    META_COLS_BS,
    META_COLS_PL,
    _attach_mapping,
    _normalize_mapping_frame,
    build_final_output,
)


class TestSusaMappingPaths(unittest.TestCase):
    def test_finssentials_fallback_resolves_sibling_desktop(self):
        sibling = finssentials_sibling_mapping_dir()
        with tempfile.TemporaryDirectory() as tmp:
            fake_root = Path(tmp) / "GDPdU-Reports"
            fake_finssentials = Path(tmp) / "finssentials" / "Desktop"
            fake_finssentials.mkdir(parents=True)
            (fake_finssentials / BS_FILENAME).write_bytes(b"x")
            (fake_finssentials / PL_FILENAME).write_bytes(b"x")

            with patch("susa_mapping_paths.PROJECT_ROOT", fake_root):
                bs, pl = resolve_susa_kontenmapping_paths({})

            self.assertEqual(bs, str(fake_finssentials / BS_FILENAME))
            self.assertEqual(pl, str(fake_finssentials / PL_FILENAME))
            self.assertEqual(sibling.parent.name, "finssentials")


class TestSusaMasterColumns(unittest.TestCase):
    def test_normalize_mapping_frame_recognizes_na(self):
        raw = pd.DataFrame(
            {
                "Account description": ["Cash"],
                "L1 - BS/PL": ["BS"],
                "L2": ["Assets"],
                "L3": ["CA"],
                "L4": ["Cash"],
                "NA": ["Current assets"],
            }
        )
        frame = _normalize_mapping_frame(raw)
        self.assertIn("NA", frame.columns)
        self.assertEqual(frame.iloc[0]["NA"], "Current assets")

    def test_attach_mapping_sets_l5_empty_l6_reported_and_na_for_bs(self):
        mapping = pd.DataFrame(
            {
                "Account description": ["Revenue", "Cash"],
                "L1 - BS/PL": ["PL", "BS"],
                "L2": ["Rev", "Assets"],
                "L3": ["Top", "CA"],
                "L4": ["Line", "Cash"],
                "Account Type": ["PL", "BS"],
                "NA": [None, "Current assets"],
            }
        )
        long_df = pd.DataFrame(
            {
                "Account description": ["Revenue", "Cash"],
                "Entity": ["E1", "E1"],
                "Account": ["4000", "1000"],
            }
        )
        out = _attach_mapping(long_df, mapping)
        self.assertEqual(out.loc[out["Account"] == "4000", "L5"].iloc[0], "")
        self.assertEqual(out.loc[out["Account"] == "4000", "L6"].iloc[0], "Reported")
        self.assertTrue(pd.isna(out.loc[out["Account"] == "4000", "NA"].iloc[0]))
        self.assertEqual(out.loc[out["Account"] == "1000", "NA"].iloc[0], "Current assets")

    def test_attach_mapping_bs_sign_dependent_duplicate_description(self):
        desc = "Darl. Bet. GmbH an BU BR"
        mapping = pd.DataFrame(
            {
                "Account description": [desc, desc],
                "L1 - BS/PL": ["BS", "BS"],
                "L2": ["Assets", "Liabilities"],
                "L3": ["Receivables", "Financial liabilities"],
                "L4": ["Receivables from affiliates", "Liabilities due to affiliates"],
                "Account Type": ["BS", "BS"],
                "NA": ["TWC", "ND"],
            }
        )
        long_neg = pd.DataFrame(
            {
                "Account description": [desc, desc],
                "Entity": ["E1", "E1"],
                "Account": ["1200", "1200"],
                "Month": [11, 12],
                "Year": [2024, 2024],
                "Balance": [1000.0, -500.0],
            }
        )
        out_neg = _attach_mapping(long_neg, mapping)
        self.assertEqual(out_neg.loc[out_neg["Month"] == 12, "NA"].iloc[0], "ND")
        self.assertEqual(
            out_neg.loc[out_neg["Month"] == 12, "L3"].iloc[0],
            "Financial liabilities",
        )

        long_pos = pd.DataFrame(
            {
                "Account description": [desc, desc],
                "Entity": ["E1", "E1"],
                "Account": ["1200", "1200"],
                "Month": [11, 12],
                "Year": [2024, 2024],
                "Balance": [-100.0, 800.0],
            }
        )
        out_pos = _attach_mapping(long_pos, mapping)
        self.assertEqual(out_pos.loc[out_pos["Month"] == 12, "NA"].iloc[0], "TWC")
        self.assertEqual(out_pos.loc[out_pos["Month"] == 12, "L3"].iloc[0], "Receivables")

    def test_build_final_output_column_layout(self):
        rows = []
        for desc, acct, atype, na in (
            ("Revenue", "4000", "PL", None),
            ("Cash", "1000", "BS", "Current assets"),
        ):
            rows.append(
                {
                    "Entity": "E1",
                    "Account": acct,
                    "Account description": desc,
                    "L1 - BS/PL": atype,
                    "L2": "L2",
                    "L3": "L3",
                    "L4": "L4",
                    "L5": "",
                    "L6": "Reported",
                    "NA": na,
                    "Account Type": atype,
                    "Year": 2024,
                    "Month": 12,
                    "Balance": 1000.0 if atype == "PL" else 5000.0,
                    "Source FY": 2024,
                    "Source Group": "tb",
                    "Source Order": 0,
                }
            )
        df_long = pd.DataFrame(rows)
        master_bs, master_pl = build_final_output(df_long, fiscal_start_month=1, value_type="movements")

        self.assertEqual(list(master_pl.columns[: len(META_COLS_PL)]), META_COLS_PL)
        self.assertNotIn("NA", master_pl.columns)

        self.assertEqual(list(master_bs.columns[: len(META_COLS_BS)]), META_COLS_BS)
        self.assertIn("NA", master_bs.columns)
        self.assertEqual(master_pl["L6"].iloc[0], "Reported")
        self.assertEqual(master_pl["L5"].iloc[0], "")


if __name__ == "__main__":
    unittest.main()
