"""Unit tests for GL-derived sales analytics helpers."""
from datetime import date

from app.services.fin_compat_sql import last_day
from app.services.sales_analytics_compat import _allocate_gp, _month_bounds


def test_month_bounds_uses_last_day_date():
    start, end = _month_bounds(2025, 7)
    assert start == date(2025, 7, 1)
    assert end == last_day(2025, 7)
    assert end == date(2025, 7, 31)


def test_allocate_gp_splits_com_by_revenue_share_within_entity():
    sales = [
        ("North", "E1", 100.0),
        ("South", "E1", 100.0),
        ("North", "E2", 50.0),
    ]
    com = {"E1": 40.0, "E2": 10.0}
    out = _allocate_gp(sales, com)
    assert out["North"] == (150.0, 120.0, 80.0)
    assert out["South"] == (100.0, 80.0, 80.0)


def test_allocate_gp_empty_com_yields_full_margin():
    sales = [("A", "E1", 80.0)]
    out = _allocate_gp(sales, {})
    assert out["A"] == (80.0, 80.0, 100.0)
