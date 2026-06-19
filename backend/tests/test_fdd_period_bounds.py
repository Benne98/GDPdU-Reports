"""Period (FY / YTD / LTM) bound consistency across GST, PVM and TOP.

Regression guard for the "Stichtag != as-of" period dilemma: with a fiscal
year-end of 31 July and an as-of date of 31 December, every strand must derive
the same YTD and LTM bounds via the shared funktionssammlung helpers.

Run with an interpreter that has pandas/openpyxl, e.g.:
    .venv/bin/python -m pytest backend/tests/test_fdd_period_bounds.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# PVM and GST load a JSON config from sys.argv[1] at import time. Point them at a
# throwaway empty config so the modules import cleanly for unit testing.
_DUMMY_CFG = Path(tempfile.gettempdir()) / "_fdd_period_test_cfg.json"
_DUMMY_CFG.write_text("{}", encoding="utf-8")
sys.argv = [sys.argv[0], str(_DUMMY_CFG)]

import funktionssammlung as fs  # noqa: E402
import pvm_verformelt as pvm  # noqa: E402
import top_report as top  # noqa: E402
import general_sales_table_MM_verformelt as gst  # noqa: E402

FY_END_M = 7
FY_END_D = 31


def _d(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


class TestSharedPeriodHelpers(unittest.TestCase):
    def test_ytd_start_follows_fiscal_year(self):
        as_of = fs.fdd_as_of_end(2024, 12)
        start, end = fs.fdd_ytd_bounds(as_of, FY_END_M, FY_END_D)
        self.assertEqual(_d(start), "2024-08-01")
        self.assertEqual(_d(end), "2024-12-31")

    def test_ltm_is_rolling_twelve_months(self):
        as_of = fs.fdd_as_of_end(2024, 12)
        start, end = fs.fdd_ltm_bounds(as_of)
        self.assertEqual(_d(start), "2024-01-01")
        self.assertEqual(_d(end), "2024-12-31")

    def test_as_of_is_fy_end_flag(self):
        self.assertFalse(fs.fdd_as_of_is_fy_end(fs.fdd_as_of_end(2024, 12), FY_END_M, FY_END_D))
        self.assertTrue(fs.fdd_as_of_is_fy_end(fs.fdd_as_of_end(2024, 7), FY_END_M, FY_END_D))


def _pvm_cfg(period_mode: str) -> dict:
    return {
        "file_path": "x.xlsx",
        "sheet_name": "Data",
        "output_file_path": "Desktop",
        "case_id": "t",
        "group_col": "Product",
        "quantity_col": "Qty",
        "revenue_col": "Rev",
        "cost_col": "Cost",
        "invoice_col": "Invoice Date",
        "as_of_year": 2024,
        "as_of_month": 12,
        "period_mode": period_mode,
        "fy_end_month": FY_END_M,
        "fy_end_day": FY_END_D,
        "pvm_method": "three_components",
        "first_fy": 2021,
    }


def _top_cfg() -> dict:
    return {
        "current_year": 2024,
        "current_month": 12,
        "calc_mode": "invoice",
        "value_col": "Rev",
        "top_col": "Product",
        "invoice_col": "Invoice Date",
        "file_path": "x.xlsx",
        "output_file_path": "Desktop",
        "sheet_name": "Data",
        "case_id": "t",
        "fiscal_year_end_month": FY_END_M,
        "fiscal_year_end_day": FY_END_D,
        "first_fy": 2021,
    }


def _gst_cfg() -> dict:
    return {
        "file_path": "x.xlsx",
        "sheet_name": "Data",
        "output_file_path": "Desktop",
        "case_id": "t",
        "calc_mode": "invoice",
        "invoice_col": "Invoice Date",
        "value_cols": {"revenue": "Rev", "cost": "Cost"},
        "group_cols": ["Product"],
        "current_year": 2024,
        "current_month": 12,
        "fiscal_year_end_month": FY_END_M,
        "fiscal_year_end_day": FY_END_D,
        "profit_mode": "cost",
        "first_fy": 2021,
        "show_ytd": True,
        "show_ltm": True,
    }


class TestStrandPeriodBounds(unittest.TestCase):
    def test_pvm_ytd_latest_period(self):
        periods, labels = pvm.build_periods(_pvm_cfg("YTD"))
        latest = labels[-1]
        start, end = periods[latest]
        self.assertEqual(_d(start), "2024-08-01")
        self.assertEqual(_d(end), "2024-12-31")

    def test_pvm_fy_excludes_unclosed_year(self):
        # As-of Dec 2024 with Jul FY end -> last CLOSED FY ends 2024-07-31 (FY24A).
        periods, labels = pvm.build_periods(_pvm_cfg("FY"))
        self.assertIn(fs.fy_label(2024), periods)
        self.assertNotIn(fs.fy_label(2025), periods)

    def test_top_auto_appends_ytd_ltm(self):
        defs = top.build_period_definitions(_top_cfg())
        pmap = defs["period_map"]
        self.assertFalse(defs["as_of_is_fy_end"])
        self.assertEqual(_d(pmap[fs.fy_label(2024)][0]), "2023-08-01")
        self.assertEqual(_d(pmap[fs.fy_label(2024)][1]), "2024-07-31")
        self.assertEqual(_d(pmap[fs.ytd_label(2024)][0]), "2024-08-01")
        self.assertEqual(_d(pmap[fs.ytd_label(2024)][1]), "2024-12-31")
        self.assertEqual(_d(pmap[fs.ltm_label(2024)][0]), "2024-01-01")
        self.assertEqual(_d(pmap[fs.ltm_label(2024)][1]), "2024-12-31")

    def test_gst_ytd_ltm_match_top(self):
        top_defs = top.build_period_definitions(_top_cfg())
        gst_defs = gst.build_period_definitions(_gst_cfg())
        for label in (fs.ytd_label(2024), fs.ltm_label(2024)):
            ts, te = top_defs["period_map"][label]
            gs, ge = gst_defs["period_map"][label]
            self.assertEqual(_d(gs), _d(ts), f"{label} start mismatch")
            self.assertEqual(_d(ge), _d(te), f"{label} end mismatch")


if __name__ == "__main__":
    unittest.main()
