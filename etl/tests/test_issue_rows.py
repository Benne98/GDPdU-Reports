"""Tests for S1 issue row collection."""
import pandas as pd

from etl.issue_rows import collect_s1_issue_rows


def test_collect_s1_issue_rows_empty_account():
    linked = pd.DataFrame({
        "booking_line_id": [1, 2, 3],
        "account_number_group": ["01041100", "", ""],
        "amount": [100.0, 0.0, 50.0],
        "fiscal_year": [2020, 2020, 2021],
    })
    raw = pd.DataFrame({
        "Account": ["41100", "", ""],
        "Amount": [100, 0, 50],
    })

    out = collect_s1_issue_rows(linked, raw, "account_number_group")

    assert out["stats"]["row_count"] == 2
    assert out["stats"]["amount_sum"] == 50.0
    assert out["stats"]["fiscal_years"] == [2020, 2021]
    assert out["total"] == 2
    assert len(out["rows"]) == 2
    assert out["rows"][0]["booking_line_id"] == 2


def test_collect_s1_issue_rows_respects_exclusions():
    linked = pd.DataFrame({
        "booking_line_id": [1, 2],
        "account_number_group": ["", ""],
        "amount": [0.0, 0.0],
        "fiscal_year": [2020, 2020],
    })
    raw = pd.DataFrame({"Account": ["", ""]})

    out = collect_s1_issue_rows(linked, raw, "account_number_group", exclude_line_ids=[1])

    assert out["stats"]["row_count"] == 1
    assert out["rows"][0]["booking_line_id"] == 2


def test_collect_s1_issue_rows_search_filter():
    linked = pd.DataFrame({
        "booking_line_id": [1, 2],
        "account_number_group": ["", ""],
        "amount": [0.0, 0.0],
        "fiscal_year": [2020, 2021],
    })
    raw = pd.DataFrame({
        "Entity": ["A", "B"],
        "Account": ["", ""],
    })

    out = collect_s1_issue_rows(linked, raw, "account_number_group", search="a")

    assert out["total"] == 1
    assert out["rows"][0]["booking_line_id"] == 1
