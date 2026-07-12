"""Tests for FTE column audit."""

import pandas as pd
import pytest

from FTE_payroll import audit_invalid_fte_columns


@pytest.fixture
def fte_sample(tmp_path):
    path = tmp_path / "personnel.xlsx"
    df = pd.DataFrame(
        {
            "Employment": [1.0, None, 0.5],
            "Months": [12, 12, None],
            "Salary": [50000, 60000, 70000],
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
    assert result["periods"][0]["invalid_rows"] == 2
