"""Defensive magnitude guard for opening-balance amounts (Wave 2).

Proves ``ingest._assert_amount_magnitude`` turns a mis-scaled (e.g. US amount parsed
with a German profile) OB amount into a friendly **422** instead of letting it hit
Postgres and surface as a raw ``NUMERIC(18,6)`` overflow **500**. In-range US and German
amounts must pass untouched (happy path preserved). Pure/DB-free — the guard is a pure
function over a canonical DataFrame.

The full end-to-end commit-amount equivalence (US 52803.84 == German 52.803,84 ==
52803.84) is owned by test-engineer (Wave 3); this file only pins the guard behaviour.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest
from fastapi import HTTPException

import app.routers.ingest as ing


class TestAssertAmountMagnitude:
    # ---- happy path: legitimate in-range amounts pass ---------------------- #
    def test_in_range_us_amount_passes(self):
        # US 52803.84 correctly parsed -> well under the 1e12 bound.
        df = pd.DataFrame({"amount": [52803.84, -52803.84, 0.0]})
        ing._assert_amount_magnitude(df)  # must not raise

    def test_in_range_german_amount_passes(self):
        # German 52.803,84 correctly parsed to 52803.84 -> passes.
        df = pd.DataFrame({"amount": [52803.84, 1234567.89]})
        ing._assert_amount_magnitude(df)  # must not raise

    def test_real_file_max_magnitude_passes(self):
        # ~5.29e7 is the largest amount in the real reference OB file — must pass,
        # with >4 orders of magnitude of headroom below the 1e12 bound.
        df = pd.DataFrame({"amount": [52_900_000.00]})
        ing._assert_amount_magnitude(df)  # must not raise

    def test_just_under_bound_passes(self):
        df = pd.DataFrame({"amount": [ing._OB_AMOUNT_ABS_LIMIT - 1.0]})
        ing._assert_amount_magnitude(df)  # must not raise

    # ---- guard: mis-scaled amount fails LOUDLY with a 422, not a 500 ------- #
    def test_misscaled_amount_raises_friendly_422(self):
        # US 52803.84 parsed with a German profile (strip '.' as thousands) ->
        # ~5.28e16, far above the NUMERIC(18,6) 1e12 ceiling.
        df = pd.DataFrame({"amount": [52_803_840_000_000_004.0]})
        with pytest.raises(HTTPException) as ei:
            ing._assert_amount_magnitude(df)
        assert ei.value.status_code == 422
        detail = str(ei.value.detail).lower()
        # Message must point the user at the number format / decimal separator.
        assert "decimal separator" in detail or "number format" in detail
        # And it must NOT leak Python/SQL internals (would trip the frontend
        # humanizer into the generic "Something went wrong").
        assert "psycopg2" not in detail and "traceback" not in detail

    def test_negative_misscaled_amount_raises(self):
        df = pd.DataFrame({"amount": [-5.3e16]})
        with pytest.raises(HTTPException) as ei:
            ing._assert_amount_magnitude(df)
        assert ei.value.status_code == 422

    def test_at_bound_raises(self):
        # DB rejects |amount| >= 1e12, so the bound is inclusive-reject.
        df = pd.DataFrame({"amount": [ing._OB_AMOUNT_ABS_LIMIT]})
        with pytest.raises(HTTPException) as ei:
            ing._assert_amount_magnitude(df)
        assert ei.value.status_code == 422

    def test_detail_includes_offending_example(self):
        df = pd.DataFrame({"amount": [1.0, 5.3e16]})
        with pytest.raises(HTTPException) as ei:
            ing._assert_amount_magnitude(df)
        # The offending value is surfaced to help the admin spot the mis-scale.
        assert "e+" in str(ei.value.detail).lower() or "5300" in str(ei.value.detail)

    # ---- robustness -------------------------------------------------------- #
    def test_missing_amount_column_is_noop(self):
        ing._assert_amount_magnitude(pd.DataFrame({"other": [1, 2]}))  # must not raise

    def test_bound_matches_numeric_18_6(self):
        # NUMERIC(18,6): 18 total / 6 fractional -> 12 integer digits -> max < 1e12.
        assert ing._OB_AMOUNT_ABS_LIMIT == 1e12
        assert math.log10(ing._OB_AMOUNT_ABS_LIMIT) == 12
