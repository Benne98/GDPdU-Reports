"""GP Bridge Fast Track helpers (sheet titles, normalize, sheet group)."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from databook_workbook import _classify_sheet_group, is_revenue_graph_sheet
from GP_Bridge import (
    BridgeItem,
    _waterfall_geometry,
    _make_compress_mapper,
    bridge_sheet_title,
    bridge_table_title,
    calc_mode_subtitle_suffix,
    detect_axis_break,
    get_three_periods,
    normalize_config,
)


def test_bridge_titles_by_profit_mode():
    assert bridge_sheet_title({"profit_mode": "cost"}) == "GP Bridge"
    assert bridge_sheet_title({"profit_mode": "profit"}) == "NP Bridge"
    assert bridge_table_title({"profit_mode": "cost"}) == "Gross Profit Bridge"
    assert bridge_table_title({"profit_mode": "profit"}) == "Net Profit Bridge"
    # Fast Track Net/Gross label wins over profit_mode for sheet naming
    assert bridge_sheet_title({"sales_basis": "net", "profit_mode": "cost"}) == "NP Bridge"
    assert bridge_sheet_title({"sales_basis": "gross", "profit_mode": "profit"}) == "GP Bridge"


def test_normalize_config_sheet_and_sales_basis():
    cfg = {
        "current_year": 2025,
        "current_month": 7,
        "calc_mode": "accrual",
        "period_mode": "FY",
        "invoice_col": "Invoice Date",
        "start_col": "Contract Start Date",
        "end_col": "Contract End Date",
        "revenue_col": "Contract Value After Discount",
        "cost_col": "Third-Party Contract Value",
        "profit_mode": "cost",
        "sales_basis": "gross",
        "subtitle_suffix": "by invoiced amounts",  # stale vs accrual → overwritten
        "bridge": {"dim_col": "Product Name", "top_n": 3},
    }
    out = normalize_config(cfg)
    assert out["sales_basis"] == "gross"
    assert out["table"] == "Gross Profit Bridge"
    assert out["base_sheet_name"] == "GP Bridge"
    assert out["excel"]["sheet_name"] == "GP Bridge"
    assert out["invoice_mapping_mode"] == "date"
    assert out["subtitle_suffix"] == "by accrued amounts"
    assert calc_mode_subtitle_suffix("invoice") == "by invoiced amounts"


def test_gp_bridge_sheet_in_earnings_graph_group():
    assert is_revenue_graph_sheet("GP Bridge")
    assert is_revenue_graph_sheet("NP Bridge_2")
    assert _classify_sheet_group("GP Bridge") == "earnings"


def test_detect_axis_break_for_outlier_total():
    items = [
        BridgeItem("FY23A", 20.0, is_total=True),
        BridgeItem("A", 10.0),
        BridgeItem("FY24A", 40.0, is_total=True),
        BridgeItem("B", 30.0),
        BridgeItem("Other", 20.0, is_other=True),
        BridgeItem("FY25A", 200.0, is_total=True),
    ]
    bottoms, heights = _waterfall_geometry(items)
    brk = detect_axis_break(
        bottoms,
        heights,
        items,
        {
            "axis_break": True,
            "axis_break_ratio": 1.60,
            "axis_break_headroom": 1.0,
            "axis_break_include_pad": 1.02,
        },
    )
    assert brk is not None
    assert brk["scale_low"] < items[-1].value_keUR
    assert brk["scale_low"] >= 40.0
    assert 0.55 <= brk["low_frac"] <= 0.88
    map_y, display_top = _make_compress_mapper(brk)
    # Continuous: level at scale_low matches start of steps above it
    assert abs(map_y(40.0) - 40.0) < 1e-9
    assert map_y(200.0) > map_y(40.0)
    assert map_y(200.0) <= display_top + 1e-9
    assert abs(map_y(40.0) - map_y(40.0 + 1e-15)) < 1e-6  # continuous at boundary


def test_three_periods_align_with_bars_at_fye():
    cfg = {
        "current_year": 2025,
        "current_month": 7,
        "fiscal_year_end_month": 7,
        "fiscal_year_end_day": 31,
        "period_mode": "FY",
    }
    periods, subtitle = get_three_periods(cfg)
    assert [p[2] for p in periods] == ["FY23A", "FY24A", "FY25A"]
    assert subtitle == "FY23A → FY25A"


def test_three_periods_ytd_when_not_at_fye():
    cfg = {
        "current_year": 2025,
        "current_month": 3,
        "fiscal_year_end_month": 7,
        "fiscal_year_end_day": 31,
        "period_mode": "FY",
    }
    periods, _ = get_three_periods(cfg)
    assert [p[2] for p in periods] == ["FY23A", "FY24A", "YTD25"]
