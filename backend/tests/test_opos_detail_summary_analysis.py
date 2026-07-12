"""Document known detail vs summary total divergence causes (formula mode).

Fix direction is intentionally deferred until product confirms aggregation rules.
"""

from __future__ import annotations

from opos import (
    INTERNAL_OPOS_COLUMNS,
    _formula_cfg,
    build_aging_bucket_defs,
    filter_zero_partners,
    summary_from_grid,
)


def test_formula_cfg_uses_internal_columns_when_column_letters_set():
    cfg = {
        "column_letters": {"partner": "A", "amount": "B", "due_date": "C"},
        "columns": {
            "partner_id": "Debitoren",
            "partner_name": "Debitoren",
            "amount": "Betrag",
            "due_date": "Faellig",
        },
    }
    proc = _formula_cfg(cfg)
    assert proc["columns"]["partner_id"] == INTERNAL_OPOS_COLUMNS["partner_id"]


def test_filter_zero_partners_can_shrink_detail_partner_set():
    """Detail footer sums visible partner rows; summary SUMIFS uses full source."""
    from opos import AsOfPeriod

    periods = [AsOfPeriod("Dec24A", __import__("pandas").Timestamp("2024-12-31"))]
    buckets = build_aging_bucket_defs({})
    grid = {
        "Dec24A": {
            "P1": {"overdue_1_30": 10.0},
            "P2": {"overdue_1_30": 0.0},
        }
    }
    order = filter_zero_partners(["P1", "P2"], grid, periods, buckets)
    assert order == ["P1"]
    summary = summary_from_grid(grid, periods, buckets)
    assert summary["Dec24A"]["overdue_1_30"] == 10.0


def test_summary_from_grid_includes_all_partners_in_grid():
    """Python summary path aggregates every partner in grid (not filter_zero_partners)."""
    from opos import AsOfPeriod

    periods = [AsOfPeriod("Dec24A", __import__("pandas").Timestamp("2024-12-31"))]
    buckets = build_aging_bucket_defs({})
    grid = {
        "Dec24A": {
            "P1": {"overdue_1_30": 10.0},
            "P2": {"overdue_1_30": 5.0},
        }
    }
    summary = summary_from_grid(grid, periods, buckets)
    assert summary["Dec24A"]["overdue_1_30"] == 15.0
