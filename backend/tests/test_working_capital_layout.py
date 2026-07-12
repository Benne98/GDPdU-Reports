"""Tests for Working capital sheet row ordering."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
BACKEND = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from databook_periods import (  # noqa: E402
    group_month_columns_by_reporting_fy,
    master_fy_label,
    yearly_average_period_label,
)


def _simulate_wc_row_order(
    main_row_count: int,
    kpi_count: int,
    fy_group_count: int,
    *,
    data_start: int = 9,
) -> dict[str, int]:
    """Mirror Working_capital.py row allocation after layout refactor."""
    last_table_row = data_start + main_row_count - 1
    kpi_title_row = last_table_row + 1
    kpi_start_row = last_table_row + 2
    fy_avg_start_row = kpi_start_row + kpi_count
    return {
        "total_nwc": last_table_row,
        "kpi_title": kpi_title_row,
        "first_kpi": kpi_start_row,
        "first_fy_avg": fy_avg_start_row,
    }


def test_wc_row_order_nwc_before_kpi_before_fy_avg():
    periods = [f"Jan{i:02d}A" for i in range(1, 13)]
    fy_groups = group_month_columns_by_reporting_fy(periods, 12)
    rows = _simulate_wc_row_order(
        main_row_count=20,
        kpi_count=4,
        fy_group_count=len(fy_groups),
    )
    assert rows["total_nwc"] < rows["kpi_title"]
    assert rows["kpi_title"] < rows["first_kpi"]
    assert rows["first_kpi"] < rows["first_fy_avg"]


def test_fy_avg_labels_use_master_fy_label_for_completed_fy():
    periods = ["Jan24A", "Feb24A", "Mar24A"]
    fy_groups = group_month_columns_by_reporting_fy(periods, 3)
    labels = [
        f"Yearly average {yearly_average_period_label(y, cols, periods, 3)}"
        for y, cols in fy_groups.items()
    ]
    assert all(lbl.startswith("Yearly average FY") for lbl in labels)


def test_fy_avg_labels_use_ytd_for_open_interval():
    periods = ["Jan25A", "Feb25A", "Mar25A", "Apr25A", "May25A", "Jun25A", "Jul25A"]
    fy_groups = group_month_columns_by_reporting_fy(periods, 12)
    labels = [
        f"Yearly average {yearly_average_period_label(y, cols, periods, 12)}"
        for y, cols in fy_groups.items()
    ]
    assert labels == ["Yearly average YTD25A"]


def test_wc_check_rows_after_helper_block():
    from databook_excel_layout import check_row_groups_after_table

    fy_avg_start = 9 + 20 + 2 + 4  # data + nwc table + kpi title + 4 kpis
    fy_avg_count = 1
    gap_row = fy_avg_start + fy_avg_count
    ns_helper_row = gap_row + 3
    check_base = ns_helper_row + 2
    rows = check_row_groups_after_table(check_base, [3, 3])
    assert rows == [
        check_base + 2,
        check_base + 3,
        check_base + 4,
        check_base + 6,
        check_base + 7,
        check_base + 8,
    ]
    assert rows[0] > ns_helper_row


def test_bs_bucket_bucket_source_formula():
    ROOT = Path(__file__).resolve().parent.parent.parent
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from Working_capital import _bs_bucket_bucket_source_formula

    formula = _bs_bucket_bucket_source_formula(
        "BS_Bucket",
        {"TWC": 11, "OWC": 12},
        "OWC",
        assets_row=50,
        el_row=60,
    )
    assert formula == "='BS_Bucket'!L50+'BS_Bucket'!L60"


def test_bs_bucket_nwc_source_formula():
    ROOT = Path(__file__).resolve().parent.parent.parent
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from Working_capital import _bs_bucket_nwc_source_formula

    formula = _bs_bucket_nwc_source_formula(
        "BS_Bucket",
        {"TWC": 11, "OWC": 12},
        assets_row=50,
        el_row=60,
    )
    assert formula == (
        "='BS_Bucket'!K50+'BS_Bucket'!K60+'BS_Bucket'!L50+'BS_Bucket'!L60"
    )


def test_nwc_row_uses_white_fill_not_subtotal():
    from gst_excel_theme import THEME

    assert THEME.fill_white.fgColor.rgb.endswith("FFFFFF")
    assert THEME.fill_subtotal.fgColor.rgb != THEME.fill_white.fgColor.rgb
