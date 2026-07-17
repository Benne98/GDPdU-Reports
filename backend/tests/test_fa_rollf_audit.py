"""Tests for FA rollforward column audit and n/a grouping keys."""

import pandas as pd
import pytest

from fixed_assets_rollf import audit_fa_rollf_mapped_columns, build_row_specs
from scripts.source_data_audit import ISSUE_EMPTY_GROUPED_AS_NA, ISSUE_NON_NUMERIC


@pytest.fixture
def fa_sample(tmp_path):
    path = tmp_path / "fa.xlsx"
    df = pd.DataFrame(
        {
            "Group": ["", "Machinery"],
            "Opening": [1000.0, "bad"],
            "Additions": [100.0, 50.0],
            "Disposals": [0.0, 0.0],
            "Dep": [200.0, 100.0],
        }
    )
    df.to_excel(path, index=False)
    return str(path)


def test_audit_fa_rollf_mapped_columns(fa_sample):
    result = audit_fa_rollf_mapped_columns(
        [{"label": "FY2024", "file_path": fa_sample, "sheet_name": ""}],
        {
            "opening": "Opening",
            "additions": "Additions",
            "disposals": "Disposals",
            "depreciation_cols": ["Dep"],
        },
        group_cols=["Group"],
    )
    assert result["total_invalid"] >= 1
    file_entry = result["files"][0]
    issues = {(c["role"], c["issue"]): c["count"] for c in file_entry["columns"]}
    assert issues.get(("group_0", ISSUE_EMPTY_GROUPED_AS_NA)) == 1
    assert issues.get(("opening", ISSUE_NON_NUMERIC)) == 1
    assert any("n/a" in n for n in file_entry.get("notes", []))


def test_build_row_specs_labels_empty_key_as_na():
    specs = build_row_specs(["Group"], {("n/a",), ("A",)}, {})
    labels = [s.label for s in specs if s.row_type == "leaf"]
    assert "n/a" in labels
