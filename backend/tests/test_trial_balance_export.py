"""Tests for trial balance export column layout."""
from app.services.fin_compat_trial_balance import (
    _bs_columns,
    _export_periods,
    _pl_columns,
)


def test_export_periods_span_jul_2025():
    periods = _export_periods(2025, 7)
    assert periods[0] == (2022, 1)
    assert periods[-1] == (2025, 7)
    assert len(periods) == 43


def test_pl_columns_match_master_layout():
    cols = _pl_columns(2025, 7)
    labels = [c["label"] for c in cols if c["label"]]
    assert labels[:5] == ["Entity", "Income / Expense", "Position", "Item", "Account"]
    assert "FY22A" in labels
    assert "FY23A" in labels
    assert "FY24A" in labels
    assert "YTD25A" in labels
    assert labels[-1] == "Jul25A"
    assert labels.count("Jan22A") == 1


def test_bs_columns_match_master_layout():
    cols = _bs_columns(2025, 7)
    labels = [c["label"] for c in cols if c["label"]]
    assert labels[:6] == ["Entity", "L1", "Assets / Equity & Liabilities", "Position", "Item", "Account"]
    assert "Dec22A" in labels
    assert "Dec23A" in labels
    assert "Dec24A" in labels
    assert "Jul25A" in labels
    assert labels[-1] == "Jul25A"
