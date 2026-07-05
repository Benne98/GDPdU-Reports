"""Wave-3 end-to-end number-format parse tests for opening-balance commit.

Three mandatory scenarios (docs/financial-logic.md
§ "File-OB number-format parse rule + magnitude guard"):

  1. US-format OB input (52803.84 + float-artifact 52803.840000000004):
     Staged as a comma-delimited CSV; profile without explicit decimal/thousands
     (mirroring the fixed wizard).  _load_and_apply(opening_balance=True) must
     return amount ≈ 52803.84 — NOT 5280384 (German-profile mis-parse of the
     clean value) and NOT ~5.28e16 (German-profile mis-parse of the float-artifact
     that would overflow NUMERIC(18,6) and previously surfaced as a raw 500).

     'Delimiter dialect alone would not have saved it': in the OLD buggy wizard the
     profile hardcoded decimal=',', setting caller_forced_decimal=True and bypassing
     BOTH the dialect fill AND the sniff.  Only removing that hardcode (the fix)
     allows the sniff to run and rescue US amounts from comma-delimited OB files.

  2. German-format OB input (52.803,84 in a ;-delimited file):
     Same profile approach (no explicit decimal).  _load_and_apply(opening_balance=True)
     must return amount ≈ 52803.84.  Behavior-preserving proof for DATEV: the sniff
     correctly confirms the dialect's comma-decimal guess.

  3. Magnitude guard — mis-scaled OB amount raises a friendly 422:
     Craft a canonical DataFrame whose amount column holds ~5.28e16 (the value that
     a German-profile mis-parse of '52803.840000000004' would produce by stripping
     all dots as thousands separators).  _assert_amount_magnitude must raise
     HTTPException(422) with a humanized message mentioning 'number format' or
     'decimal separator' — NOT a 500 and NOT a raw DB error.  This is the safety net
     that catches anything the sniff misses.

All tests are DB-free (session=None; entity.mode='fixed' with a numeric prefix
resolves without a DB lookup via is_numeric_prefix_value).  All fixtures are synthetic.

Layer summary:
  Scenarios 1 & 2 — Layer B/C: _load_and_apply(opening_balance=True) via file
    staged directly in UPLOAD_DIR.  No HTTP overhead, no Postgres.
  Scenario 3     — Layer A: _assert_amount_magnitude called directly on a crafted
    canonical DataFrame.  Pure unit test; no file, no DB.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pandas as pd
import pytest
from fastapi import HTTPException

import app.routers.ingest as ing
from app.routers.ingest import UPLOAD_DIR


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

@contextmanager
def _staged(content: bytes, filename: str) -> Iterator[str]:
    """Write *content* to UPLOAD_DIR as ``{file_id}_{filename}``; yield the file_id;
    remove the file on exit.

    Lets tests call _load_and_apply without going through the full HTTP upload
    chain.  The file_id prefix is random so parallel test runs don't collide.
    """
    fid = uuid.uuid4().hex[:16]
    dest = UPLOAD_DIR / f"{fid}_{filename}"
    try:
        dest.write_bytes(content)
        yield fid
    finally:
        dest.unlink(missing_ok=True)


def _ob_profile_no_decimal() -> dict:
    """OB mapping profile that does NOT specify decimal/thousands — mirrors the fixed wizard.

    With no explicit 'decimal' / 'thousands' keys in the dict, profile_from_dict
    leaves ``profile.decimal = '.'`` (MappingProfile dataclass default).  This
    causes ``caller_forced_decimal = False`` inside _load_and_apply, which
    unlocks both the dialect fill AND the amount-column sniff path.

    The OLD wizard hardcoded decimal=','  which set caller_forced_decimal=True and
    bypassed both safety layers — that was the bug.  This profile mirrors the fix.
    """
    return {
        "entity": {"mode": "fixed", "value": "01"},
        "fiscal_year": {"mode": "fixed", "value": 2023},
        "sign": {"mode": "signed", "amount": "Amount"},
        # NO 'decimal' key, NO 'thousands' key  -> profile.decimal = '.' (default)
        # fiscal_year mode='fixed' so there is no need for a posting_date column
        # (OB path: posting_date is optional).
        "columns": {
            "account_number": "Account",
            # posting_date intentionally absent: OB path synthesizes Jan-1
        },
        "linking_strategy": "none",
        "entry_type": "actual",
        "source_system": "ob_sniff_test",
    }


# --------------------------------------------------------------------------- #
# Scenario 1 — US-format OB input (52803.84 + float-artifact row)
# --------------------------------------------------------------------------- #

class TestOBUSFormatParse:
    """Scenario 1: US amounts in a comma-delimited OB CSV file.

    Fixture: comma-delimited CSV (field delimiter ','), two rows:
      Account,Amount
      1200,52803.84
      1300,52803.840000000004     <- float-artifact: the dangerous case

    The amount-column sniff on these values votes ('.',',') and sets
    profile.decimal='.', so both amounts parse to ≈52803.84.

    Regression proof — old wizard (decimal=',') behaviour:
      52803.84          -> strip '.' as thousands -> 5280384     (wrong; silent)
      52803.840000000004 -> strip all dots        -> ~5.28e16    (overflow -> 422)
    The fix (no hardcoded decimal) allows the sniff to prevent both mis-parses.
    """

    _CSV = b"Account,Amount\n1200,52803.84\n1300,52803.840000000004\n"

    def test_us_clean_amount_parsed_correctly(self):
        """52803.84 (clean US value) -> committed amount ≈ 52803.84."""
        with _staged(self._CSV, "ob_us.csv") as fid:
            canonical, _dialect, _raw = ing._load_and_apply(
                fid, None, _ob_profile_no_decimal(), None, opening_balance=True
            )
        amounts = canonical["amount"].tolist()
        assert len(amounts) == 2, f"Expected 2 rows, got {len(amounts)}"
        got = amounts[0]
        assert got == pytest.approx(52803.84, abs=1e-4), (
            f"US amount mis-parsed: got {got!r}, expected ≈52803.84"
        )
        # Also confirm we did NOT get the German-strip value (5280384)
        assert abs(got - 5_280_384) > 1, (
            "Got 5280384: '.' was stripped as a thousands separator (German mis-parse)"
        )

    def test_float_artifact_parsed_correctly(self):
        """52803.840000000004 (float artifact) -> committed amount ≈ 52803.84.

        This is the high-stakes row.  With decimal=',', stripping ALL dots from
        '52803.840000000004' gives '52803840000000004' ≈ 5.28e16, which overflows
        NUMERIC(18,6) and used to surface as a raw 500.  The sniff must prevent this.
        """
        with _staged(self._CSV, "ob_us.csv") as fid:
            canonical, _dialect, _raw = ing._load_and_apply(
                fid, None, _ob_profile_no_decimal(), None, opening_balance=True
            )
        got = canonical["amount"].tolist()[1]
        assert got == pytest.approx(52803.84, abs=1e-4), (
            f"Float-artifact mis-parsed: got {got!r}, expected ≈52803.84. "
            f"If ~5.28e16 the sniff path did not run; if 5280384 decimal was wrong."
        )
        # Belt: the magnitude guard would block > 1e12, so this also proves the
        # full pipeline (sniff -> parse -> guard) would succeed.
        assert abs(got) < 1e12, (
            f"Amount {got!r} exceeds 1e12 — the magnitude guard would 422 this; "
            "the sniff failed to set decimal='.'."
        )

    def test_sniff_votes_dot_for_us_values(self):
        """Amount-column sniff on US strings votes ('.',',') — unit proof."""
        from etl.mapping import sniff_decimal_separator
        result = sniff_decimal_separator(["52803.84", "52803.840000000004"])
        assert result == (".", ","), (
            f"Sniff returned {result!r}; expected ('.',',') for US format"
        )

    def test_old_wizard_profile_would_produce_overflow_on_artifact(self):
        """Regression guard: old profile (decimal=',') mis-parses the float artifact
        to > 1e12, confirming the fix is non-trivial.  The assertion is INVERTED —
        we EXPECT the old profile to give the wrong (overflow) value here."""
        from etl import transform as T
        old_amount = T.parse_decimal(
            pd.Series(["52803.840000000004"]), decimal=",", thousands="."
        )
        # Old profile strips ALL dots -> '52803840000000004' -> ~5.28e16
        assert old_amount.iloc[0] > 1e12, (
            f"Old profile gave {old_amount.iloc[0]!r}; expected > 1e12 (the overflow). "
            "If this assertion fails, the regression test value is wrong."
        )


# --------------------------------------------------------------------------- #
# Scenario 2 — German-format OB input (52.803,84 in a ;-delimited file)
# --------------------------------------------------------------------------- #

class TestOBGermanFormatParse:
    """Scenario 2: German (DATEV) amounts in a semicolon-delimited OB CSV.

    Fixture: ';'-delimited CSV, two rows:
      Account;Amount
      1200;52.803,84
      1300;1.234.567,89     <- multi-group thousands, well-known DATEV format

    _detect_dialect detects ';' -> decimal=',', thousands='.'.  The sniff on
    ['52.803,84', '1.234.567,89'] also votes (',', '.').  Both layers agree:
    amounts parse to 52803.84 and 1234567.89 respectively.

    This is the BEHAVIOR-PRESERVING proof: DATEV German format must still work
    exactly as before after the sniff was added.
    """

    _CSV = b"Account;Amount\n1200;52.803,84\n1300;1.234.567,89\n"

    def test_german_basic_amount_parsed_correctly(self):
        """52.803,84 -> committed amount ≈ 52803.84."""
        with _staged(self._CSV, "ob_de.csv") as fid:
            canonical, dialect, _raw = ing._load_and_apply(
                fid, None, _ob_profile_no_decimal(), None, opening_balance=True
            )
        # Confirm dialect agrees (belt-and-suspenders documentation for readers)
        assert dialect.get("decimal") == ",", (
            f"Expected dialect decimal=',' for ';'-delimited CSV; got {dialect!r}"
        )
        amounts = canonical["amount"].tolist()
        assert len(amounts) == 2
        got = amounts[0]
        assert got == pytest.approx(52803.84, abs=1e-4), (
            f"German amount mis-parsed: got {got!r}, expected ≈52803.84"
        )

    def test_german_large_amount_with_multiple_thousands_groups(self):
        """1.234.567,89 -> committed amount ≈ 1234567.89 (multiple dot-thousands groups)."""
        with _staged(self._CSV, "ob_de.csv") as fid:
            canonical, _dialect, _raw = ing._load_and_apply(
                fid, None, _ob_profile_no_decimal(), None, opening_balance=True
            )
        got = canonical["amount"].tolist()[1]
        assert got == pytest.approx(1_234_567.89, abs=1e-4), (
            f"German large amount mis-parsed: got {got!r}, expected ≈1234567.89"
        )

    def test_sniff_votes_comma_for_german_values(self):
        """Amount-column sniff on German strings votes (',','.') — unit proof."""
        from etl.mapping import sniff_decimal_separator
        result = sniff_decimal_separator(["52.803,84", "1.234.567,89"])
        assert result == (",", "."), (
            f"Sniff returned {result!r}; expected (',','.') for German format"
        )


# --------------------------------------------------------------------------- #
# Scenario 3 — Magnitude guard: mis-parsed OB amount raises friendly 422
# --------------------------------------------------------------------------- #

class TestOBMagnitudeGuardScenario:
    """Scenario 3: _assert_amount_magnitude turns a mis-scaled OB amount into a 422.

    Context: The magnitude guard is the second safety layer (after the sniff).
    For amounts that slip past the sniff (e.g. an all-abstain sample where every
    value has exactly 3 trailing digits), a mis-parsed value of >= 1e12 hits this
    guard instead of reaching Postgres and surfacing as a raw 500.

    Canonical frame in this test: crafted to represent what a German-profile mis-
    parse of '52803.840000000004' would produce:
      T.parse_decimal('52803.840000000004', decimal=',', thousands='.')
      -> strip '.' (thousands) from all positions -> '52803840000000004' -> ~5.28e16

    The guard must:
      * raise HTTPException with status_code 422 (not 500, not DB error)
      * include a human-readable message about number format / decimal separator
      * surface the offending value so the admin can diagnose the file

    Testing _assert_amount_magnitude directly avoids a DB session.  The function
    is called at line ~3643 in opening_balance_commit, immediately after
    _assert_finite_amounts, before any DB write — so this is the correct chokepoint.
    """

    # Value produced by German-profile mis-parse of the float-artifact string.
    # T.parse_decimal('52803.840000000004', decimal=',', thousands='.') -> 5.28e16+
    _MISSCALED = 52_803_840_000_000_004.0  # ≈ 5.28e16 >> 1e12 bound

    def test_magnitude_guard_raises_422(self):
        """Guard raises HTTPException(422), not a 500 or a raw DB error."""
        df = pd.DataFrame({"amount": [self._MISSCALED]})
        with pytest.raises(HTTPException) as ei:
            ing._assert_amount_magnitude(df)
        assert ei.value.status_code == 422, (
            f"Expected 422; got {ei.value.status_code}. "
            "A 500 means the raw NUMERIC overflow reached Postgres (regression)."
        )

    def test_magnitude_guard_message_mentions_number_format(self):
        """422 detail mentions 'number format' or 'decimal separator' (humanized)."""
        df = pd.DataFrame({"amount": [self._MISSCALED]})
        with pytest.raises(HTTPException) as ei:
            ing._assert_amount_magnitude(df)
        detail = str(ei.value.detail).lower()
        assert "decimal separator" in detail or "number format" in detail, (
            f"Message not humanized: {ei.value.detail!r}. "
            "Should guide the admin to check the file's number format."
        )

    def test_magnitude_guard_message_not_a_raw_db_error(self):
        """422 detail does NOT contain psycopg2/traceback/overflow strings."""
        df = pd.DataFrame({"amount": [self._MISSCALED]})
        with pytest.raises(HTTPException) as ei:
            ing._assert_amount_magnitude(df)
        detail = str(ei.value.detail).lower()
        assert "psycopg2" not in detail, "psycopg2 string leaked into user-facing message"
        assert "traceback" not in detail, "traceback leaked into user-facing message"
        assert "numeric field overflow" not in detail, (
            "Raw Postgres error text leaked into user-facing message"
        )

    def test_magnitude_guard_surfaces_offending_value(self):
        """422 detail includes the offending example value for admin diagnostics."""
        df = pd.DataFrame({"amount": [1.0, self._MISSCALED]})
        with pytest.raises(HTTPException) as ei:
            ing._assert_amount_magnitude(df)
        detail = str(ei.value.detail)
        # The formatted example appears in scientific or decimal notation.
        # Enough to check it contains recognizable digits from the mis-parse.
        assert any(s in detail for s in ("e+", "E+", "5280", "28038")), (
            f"Offending value not surfaced in detail: {detail!r}. "
            "Admin should be able to see which value was rejected."
        )

    def test_magnitude_guard_in_range_amounts_pass(self):
        """In-range amounts (as correctly parsed by scenarios 1 & 2) pass the guard."""
        df = pd.DataFrame({"amount": [52803.84, -52803.84, 1_234_567.89, 0.0]})
        ing._assert_amount_magnitude(df)  # must NOT raise

    def test_connect_scenarios_sniff_vs_guard(self):
        """Integration proof: the sniff produces in-range values that pass the guard;
        the mis-parsed overflow (what the guard catches) fails it.

        This test locks all three scenarios together:
          Scenario 1/2 (sniff fixed it)  -> amounts ≈ 52803.84 -> guard passes.
          Scenario 3   (guard is needed) -> amount ~5.28e16    -> guard raises 422.
        """
        # Correctly sniff-parsed amounts from scenario 1 & 2
        df_ok = pd.DataFrame({"amount": [52803.84, 52803.84, 1_234_567.89]})
        ing._assert_amount_magnitude(df_ok)  # must NOT raise

        # Mis-parsed float-artifact (what scenario 3 catches)
        df_bad = pd.DataFrame({"amount": [self._MISSCALED]})
        with pytest.raises(HTTPException) as ei:
            ing._assert_amount_magnitude(df_bad)
        assert ei.value.status_code == 422
        detail = str(ei.value.detail).lower()
        assert "decimal separator" in detail or "number format" in detail
