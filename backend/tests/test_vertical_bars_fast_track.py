"""Vertical bars Fast Track helpers (bar config, periods, sheet group, sales_basis)."""
from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from databook_workbook import _classify_sheet_group, is_vertical_bars_sheet
from vertical_bars import (
    build_top_n_segments,
    default_bars_config,
    get_comparison_periods,
    normalize_config,
)


def test_default_bars_skip_optional_empty_dims():
    bars = default_bars_config(
        entity_col="Entity",
        segment_col="",
        customer_col="Customer Name",
        product_col="Product Name",
        region_col="",
    )
    assert [b["title"] for b in bars] == ["Entity", "Top customers", "Top products"]


def test_share_floor_keeps_large_categories():
    df = pd.DataFrame(
        {
            "label": ["A", "B", "C", "D", "E"],
            "amount": [50.0, 30.0, 12.0, 4.0, 4.0],
        }
    )
    segs, _ = build_top_n_segments(df, top_n=20, rest_label="Misc.", missing_token="__M__", min_share=0.05)
    named = [s.label for s in segs if not s.is_rest]
    assert named == ["A", "B", "C"]
    rest = next(s for s in segs if s.is_rest)
    assert abs(rest.share - 0.08) < 1e-9


def test_comparison_periods_ytd_mode():
    # March with FYE July → not at year end → FY-2, FY-1, YTD
    cfg = {
        "current_year": 2025,
        "current_month": 3,
        "fiscal_year_end_month": 7,
        "fiscal_year_end_day": 31,
    }
    periods = get_comparison_periods(cfg)
    assert [p[2] for p in periods] == ["FY23A", "FY24A", "YTD25"]


def test_comparison_periods_at_fye():
    cfg = {
        "current_year": 2025,
        "current_month": 7,
        "fiscal_year_end_month": 7,
        "fiscal_year_end_day": 31,
    }
    periods = get_comparison_periods(cfg)
    assert [p[2] for p in periods] == ["FY23A", "FY24A", "FY25A"]


def test_normalize_config_sales_basis_table_label():
    cfg = {
        "current_year": 2025,
        "current_month": 7,
        "calc_mode": "invoice",
        "period_mode": "FY",
        "value_col": "Revenue",
        "invoice_col": "Invoice Date",
        "sales_basis": "gross",
        "bars": default_bars_config(entity_col="Entity", customer_col="C", product_col="P"),
    }
    out = normalize_config(cfg)
    assert out["sales_basis"] == "gross"
    assert out["table"] == "Gross sales breakdown"


def test_vertical_bars_sheet_in_executive_group():
    assert is_vertical_bars_sheet("Net sales breakdown")
    assert is_vertical_bars_sheet("Gross sales breakdown_2")
    assert _classify_sheet_group("Net sales breakdown") == "executive"
    assert _classify_sheet_group("General sales table") == "earnings"
