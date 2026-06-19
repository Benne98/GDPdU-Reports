import pandas as pd

from etl.line_exclude import (
    apply_line_exclusions,
    negligible_s1_exclusion_mask,
    suggest_negligible_s1_exclusions,
)


def test_apply_line_exclusions_drops_ids():
    df = pd.DataFrame({"booking_line_id": [1, 2, 3], "amount": [10.0, 0.0, 5.0]})
    out = apply_line_exclusions(df, [2])
    assert list(out["booking_line_id"]) == [1, 3]


def test_suggest_negligible_empty_account_zero_amount():
    df = pd.DataFrame({
        "booking_line_id": [1, 2, 3, 4],
        "gl_account_id": [pd.NA, "100", pd.NA, "200"],
        "amount": [0.0, 0.0, 100.0, 5.0],
        "account_number_group": [pd.NA, "01000100", pd.NA, "01000200"],
    })
    assert suggest_negligible_s1_exclusions(df) == [1]
    mask = negligible_s1_exclusion_mask(df)
    assert mask.tolist() == [True, False, False, False]
