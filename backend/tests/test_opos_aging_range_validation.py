"""Unit tests for OPOS aging range validation."""

import pytest

from opos import (
    AgingRangeValidationError,
    bucket_key_for_range,
    bucket_key_for_tail,
    bucket_keys_from_ranges,
    bucket_options_from_ranges,
    build_aging_bucket_defs,
    default_aging_ranges,
    validate_aging_ranges,
)


def test_validate_aging_ranges_default():
    assert validate_aging_ranges(default_aging_ranges()) == default_aging_ranges()


def test_validate_aging_ranges_rejects_gap():
    with pytest.raises(AgingRangeValidationError, match="Gap"):
        validate_aging_ranges([[1, 30], [32, 60]])


def test_validate_aging_ranges_rejects_overlap():
    with pytest.raises(AgingRangeValidationError, match="overlap"):
        validate_aging_ranges([[1, 30], [29, 60]])


def test_validate_aging_ranges_requires_start_at_one():
    with pytest.raises(AgingRangeValidationError, match="start at day 1"):
        validate_aging_ranges([[2, 30]])


def test_validate_aging_ranges_requires_at_least_one_range():
    with pytest.raises(AgingRangeValidationError):
        validate_aging_ranges([])


def test_build_aging_bucket_defs_from_custom_ranges():
    cfg = {"aging_buckets": {"ranges": [[1, 45], [46, 90]]}}
    buckets = build_aging_bucket_defs(cfg)
    keys = [b.key for b in buckets]
    assert keys == [
        "not_yet_due",
        "overdue_1_45",
        "overdue_46_90",
        "overdue_over_90",
    ]
    assert buckets[-1].label == ">90 days"


def test_bucket_options_from_ranges():
    opts = bucket_options_from_ranges([(1, 30), (31, 60)])
    assert opts[0] == ("not_yet_due", "Not yet due")
    assert opts[1] == ("overdue_1_30", "1-30 days")
    assert opts[-1] == ("overdue_over_60", ">60 days")


def test_bucket_keys_from_ranges_includes_tail():
    keys = bucket_keys_from_ranges([(1, 30)])
    assert keys == ["not_yet_due", "overdue_1_30", "overdue_over_30"]


def test_bucket_key_helpers():
    assert bucket_key_for_range(1, 30) == "overdue_1_30"
    assert bucket_key_for_tail(180) == "overdue_over_180"
