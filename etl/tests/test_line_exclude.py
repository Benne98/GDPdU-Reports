import pandas as pd

from etl.line_exclude import (
    apply_line_exclusions,
    exclusion_summary,
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
    # Suggested ids are exact digit STRINGS (precision-safe for the JS boundary).
    assert suggest_negligible_s1_exclusions(df) == ["1"]
    mask = negligible_s1_exclusion_mask(df)
    assert mask.tolist() == [True, False, False, False]


# --------------------------------------------------------------------------- #
# Precision: 62-bit booking_line_id (> 2**53) must be carried as exact STRINGS.
#
# A JSON number is parsed by JavaScript float64, so a hash id > 2**53 is silently
# rounded (e.g. 2003686083711855065 -> 2003686083711855104). The Finish commit
# sources exclude_line_ids from exclusion_summary's active_line_ids / suggested
# line_ids, so these MUST be exact digit strings or the wrong row is excluded.
# --------------------------------------------------------------------------- #
_BID_HUGE = 2003686083711855065      # > 2**53; float64 rounds it
_BID_HUGE_2 = _BID_HUGE + 1000       # another huge id, distinct after rounding too


def test_suggest_negligible_huge_id_is_exact_string():
    """suggest_negligible_s1_exclusions must return the exact huge id as a string."""
    assert int(float(_BID_HUGE)) != _BID_HUGE  # sanity: genuinely float64-unsafe
    df = pd.DataFrame({
        "booking_line_id": pd.Series([_BID_HUGE, _BID_HUGE_2], dtype="int64"),
        "gl_account_id": [pd.NA, "100"],
        "amount": [0.0, 0.0],
        "account_number_group": [pd.NA, "01000100"],
    })
    out = suggest_negligible_s1_exclusions(df)
    assert out == [str(_BID_HUGE)]
    assert all(isinstance(x, str) for x in out)


def test_exclusion_summary_active_and_suggested_are_exact_strings():
    """active_line_ids and suggested.line_ids must be exact digit strings."""
    df = pd.DataFrame({
        "booking_line_id": pd.Series([_BID_HUGE, _BID_HUGE_2], dtype="int64"),
        "gl_account_id": [pd.NA, pd.NA],
        "amount": [0.0, 0.0],
        "account_number_group": [pd.NA, pd.NA],
    })
    # Active id supplied as a string (as the UI would echo it back); the other
    # negligible row stays a suggestion. Both must surface as exact strings.
    summary = exclusion_summary(df, [str(_BID_HUGE)])

    assert summary["active_line_ids"] == [str(_BID_HUGE)]
    assert all(isinstance(x, str) for x in summary["active_line_ids"])
    assert summary["active_count"] == 1

    assert summary["suggested"] is not None
    assert summary["suggested"]["line_ids"] == [str(_BID_HUGE_2)]
    assert all(isinstance(x, str) for x in summary["suggested"]["line_ids"])


def test_exclusion_summary_accepts_int_input_and_normalises_to_string():
    """An int in exclude_line_ids must normalise to the exact string (no rounding)."""
    df = pd.DataFrame({
        "booking_line_id": pd.Series([_BID_HUGE], dtype="int64"),
        "gl_account_id": ["100"],
        "amount": [0.0],
        "account_number_group": ["01000100"],
    })
    summary = exclusion_summary(df, [_BID_HUGE])
    assert summary["active_line_ids"] == [str(_BID_HUGE)]


def test_apply_line_exclusions_drops_huge_id_via_exact_string():
    """Commit path: the exact string id (as active_line_ids would carry it) drops
    the offending row; a JS-rounded id must NOT match (no-op)."""
    df = pd.DataFrame({
        "booking_line_id": pd.Series([_BID_HUGE, _BID_HUGE_2], dtype="int64"),
        "amount": [0.0, 10.0],
    })
    out = apply_line_exclusions(df, [str(_BID_HUGE)])
    assert list(out["booking_line_id"]) == [_BID_HUGE_2]

    # The browser-rounded id must NOT remove the offender (proves the no-op bug).
    js_rounded = int(float(_BID_HUGE))
    assert js_rounded != _BID_HUGE
    out_noop = apply_line_exclusions(df, [str(js_rounded)])
    assert list(out_noop["booking_line_id"]) == [_BID_HUGE, _BID_HUGE_2]
