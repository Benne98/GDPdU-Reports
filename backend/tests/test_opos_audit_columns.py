"""Tests for OPOS multi-column audit and n/a partner aggregation."""

import pandas as pd
import pytest

from opos import (
    AsOfPeriod,
    aggregate_opos,
    audit_opos_mapped_columns,
    build_aging_bucket_defs,
    preprocess_opos_input,
)
from scripts.source_data_audit import ISSUE_EMPTY_GROUPED_AS_NA, ISSUE_MISSING_OR_INVALID


@pytest.fixture
def opos_sample(tmp_path):
    path = tmp_path / "opos.xlsx"
    df = pd.DataFrame(
        {
            "Partner": ["P1", "", "P2"],
            "Amount": [100.0, 200.0, 300.0],
            "Due": ["2024-12-01", "2024-12-15", None],
        }
    )
    df.to_excel(path, index=False)
    return str(path)


def test_audit_opos_mapped_columns_details(opos_sample):
    snaps = [{"as_of": "Dec24A", "file_path": opos_sample, "sheet_name": ""}]
    letters = {"partner": "A", "amount": "B", "due_date": "C"}
    result = audit_opos_mapped_columns(snaps, column_letters=letters)

    assert result["total_excluded"] == 1
    assert len(result["files"]) == 1
    file_entry = result["files"][0]
    assert file_entry["label"] == "Dec24A"
    assert file_entry["excluded_rows"] == 1

    issues = {c["issue"]: c["count"] for c in file_entry["columns"]}
    assert issues[ISSUE_EMPTY_GROUPED_AS_NA] == 1
    assert issues[ISSUE_MISSING_OR_INVALID] == 1
    assert any("n/a" in n for n in file_entry.get("notes", []))


def test_opos_na_partner_sumifs_tokens_use_blank_only():
    from opos import _opos_partner_sumifs_criterion_tokens

    tokens = _opos_partner_sumifs_criterion_tokens("n/a", 10)
    assert len(tokens) == 1
    assert '""' in tokens


def test_preprocess_opos_keeps_zero_partner_id(opos_sample):
    from scripts.source_data_audit import MISSING_GROUP_LABEL

    cfg = {
        "columns": {
            "partner_id": "Partner",
            "partner_name": "Partner",
            "amount": "Amount",
            "due_date": "Due",
        },
        "filters": {"enabled": False},
        "sort": {"basis": "most_recent", "metric": "total"},
    }
    df = pd.read_excel(opos_sample)
    df["Partner"] = df["Partner"].astype(object)
    df.loc[1, "Partner"] = 0
    prepped = preprocess_opos_input(df, cfg)
    assert "0" in set(prepped["_partner_id"])
    assert MISSING_GROUP_LABEL not in set(prepped["_partner_id"])


def test_preprocess_opos_aggregates_empty_partner_as_na(opos_sample):
    cfg = {
        "columns": {
            "partner_id": "Partner",
            "partner_name": "Partner",
            "amount": "Amount",
            "due_date": "Due",
        },
        "filters": {"enabled": False},
        "sort": {"basis": "most_recent", "metric": "total"},
    }
    df = pd.read_excel(opos_sample)
    prepped = preprocess_opos_input(df, cfg)
    assert "n/a" in set(prepped["_partner_id"])

    buckets = build_aging_bucket_defs({})
    periods = [AsOfPeriod("Dec24A", pd.Timestamp("2024-12-31"))]
    snapshot_dfs = {"Dec24A": prepped}
    _grid, order, meta = aggregate_opos(snapshot_dfs, cfg, periods, buckets)
    assert "n/a" in order
    assert meta["n/a"] == "n/a"
