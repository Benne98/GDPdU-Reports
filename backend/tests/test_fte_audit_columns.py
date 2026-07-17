"""Tests for FTE column audit."""

import pandas as pd
import pytest

from FTE_payroll import audit_invalid_fte_columns, aggregate_period_keys
from scripts.source_data_audit import ISSUE_EMPTY_GROUPED_AS_NA, ISSUE_MISSING_OR_INVALID


@pytest.fixture
def fte_sample(tmp_path):
    path = tmp_path / "personnel.xlsx"
    df = pd.DataFrame(
        {
            "Employment": [1.0, None, 0.5],
            "Months": [12, 12, None],
            "Salary": [50000, 60000, 70000],
            "Dept": ["", "Sales", "Sales"],
        }
    )
    df.to_excel(path, index=False)
    return str(path)


def test_audit_invalid_fte_columns_counts_bad_rows(fte_sample):
    result = audit_invalid_fte_columns(
        [{"label": "FY2024", "file_path": fte_sample, "sheet_name": ""}],
        {"employment": "Employment", "months_sum": "Months"},
    )
    assert result["total_invalid"] == 2
    assert result["periods"][0]["excluded_rows"] == 2


def test_audit_invalid_fte_columns_payroll_and_group_detail(fte_sample):
    result = audit_invalid_fte_columns(
        [{"label": "FY2024", "file_path": fte_sample, "sheet_name": ""}],
        {"employment": "Employment", "months_sum": "Months"},
        payroll_cols=["Salary"],
        group_cols=["Dept"],
    )
    file_entry = result["files"][0]
    assert file_entry["label"] == "FY2024"
    issues = {(c["role"], c["issue"]): c["count"] for c in file_entry["columns"]}
    assert issues.get(("employment", ISSUE_MISSING_OR_INVALID)) == 1
    assert issues.get(("months_sum", ISSUE_MISSING_OR_INVALID)) == 1
    assert issues.get(("group_0", ISSUE_EMPTY_GROUPED_AS_NA)) == 1
    assert any("n/a" in n for n in file_entry.get("notes", []))


def test_aggregate_period_keys_normalizes_empty_group_to_na(fte_sample):
    df = pd.read_excel(fte_sample)
    cfg = {
        "columns": {
            "employment": "Employment",
            "months_sum": "Months",
            "payroll_cols": ["Salary"],
        },
        "group_cols": ["Dept"],
        "filters": {"enabled": False},
    }
    keys, _work, _col_map = aggregate_period_keys(df, cfg)
    assert ("n/a",) in keys
