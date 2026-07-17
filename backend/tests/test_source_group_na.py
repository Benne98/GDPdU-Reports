"""Tests for shared n/a grouping helpers."""

import pandas as pd
import pytest

from scripts.source_data_audit import (
    ISSUE_EMPTY_GROUPED_AS_NA,
    MISSING_GROUP_LABEL,
    group_sumifs_criterion,
    normalize_group_tuple,
    normalize_group_value,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "n/a"),
        ("", "n/a"),
        ("   ", "n/a"),
        ("nan", "n/a"),
        ("ACME", "ACME"),
        (123, "123"),
    ],
)
def test_normalize_group_value(raw, expected):
    assert normalize_group_value(raw) == expected


def test_normalize_group_tuple():
    assert normalize_group_tuple(("", None, "Dept A")) == ("n/a", "n/a", "Dept A")


def test_group_sumifs_criterion_blank_for_na():
    assert group_sumifs_criterion("$B$10", "n/a") == '""'
    assert group_sumifs_criterion("$B$10", "ACME") == "$B$10"


def test_missing_group_label_constant():
    assert MISSING_GROUP_LABEL == "n/a"
    assert ISSUE_EMPTY_GROUPED_AS_NA == "empty_grouped_as_na"
