"""Tests for S1 issue row collection."""
import pandas as pd

from etl.issue_rows import collect_b1_issue_rows, collect_s1_issue_rows


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
    assert out["rows"][0]["booking_line_id"] == "2"


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
    assert out["rows"][0]["booking_line_id"] == "2"


def test_collect_s1_issue_rows_search_filter():
    # Search now filters over the projected CANONICAL row values (the contract no
    # longer surfaces arbitrary raw columns), e.g. the journal_entry_group_number.
    linked = pd.DataFrame({
        "booking_line_id": [1, 2],
        "journal_entry_group_number": ["01ALPHA", "01BETA"],
        "account_number_group": ["", ""],
        "amount": [0.0, 0.0],
        "fiscal_year": [2020, 2021],
    })
    raw = pd.DataFrame({"Account": ["", ""]})

    out = collect_s1_issue_rows(linked, raw, "account_number_group", search="alpha")

    assert out["total"] == 1
    assert out["rows"][0]["booking_line_id"] == "1"


# --------------------------------------------------------------------------- reference_rows (D3a)
def test_collect_s1_issue_rows_reference_rows_key_always_present():
    """The 'reference_rows' key must exist in the output dict even when no rows fail."""
    linked = pd.DataFrame({
        "booking_line_id": [1, 2],
        "account_number_group": ["01041100", "01050000"],  # both pass
        "amount": [100.0, -100.0],
        "fiscal_year": [2020, 2020],
    })
    raw = pd.DataFrame({"Account": ["41100", "50000"]})
    out = collect_s1_issue_rows(linked, raw, "account_number_group")
    # No failing rows -> columns=[], reference_rows=[]
    assert "reference_rows" in out
    assert isinstance(out["reference_rows"], list)


def test_collect_s1_issue_rows_reference_rows_are_non_failing_and_share_columns():
    """Reference rows must be passing (non-empty field) and projected onto the same columns as failing rows."""
    linked = pd.DataFrame({
        "booking_line_id": [1, 2, 3],
        "account_number_group": ["01041100", "", ""],  # row 1 passes; rows 2-3 fail
        "amount": [100.0, 0.0, 50.0],
        "fiscal_year": [2020, 2020, 2021],
    })
    raw = pd.DataFrame({
        "Account": ["41100", "", ""],
        "Amount": [100, 0, 50],
    })
    out = collect_s1_issue_rows(linked, raw, "account_number_group")

    assert out["stats"]["row_count"] == 2           # rows 2+3 fail
    assert len(out["reference_rows"]) == 1          # exactly 1 passing row (row 1)
    ref = out["reference_rows"][0]
    # Must be the passing row
    assert ref["booking_line_id"] == "1"
    # Must share the same column set as the failing rows
    assert set(ref.keys()) == set(out["columns"])


def test_collect_s1_issue_rows_reference_rows_capped_at_five():
    """reference_rows must be capped at the default reference_limit=5 even when many rows pass."""
    bids = list(range(1, 12))        # 11 rows: bid=1 fails, bids 2..11 all pass
    accounts = [""] + ["01041100"] * 10
    linked = pd.DataFrame({
        "booking_line_id": bids,
        "account_number_group": accounts,
        "amount": [0.0] * 11,
        "fiscal_year": [2020] * 11,
    })
    raw = pd.DataFrame({
        "Account": [""] + ["41100"] * 10,
        "Amount": [0] * 11,
    })
    out = collect_s1_issue_rows(linked, raw, "account_number_group")

    assert out["stats"]["row_count"] == 1            # only bid=1 fails
    # 10 passing rows, but reference_limit=5 (default) caps output
    assert len(out["reference_rows"]) == 5
    # All returned reference rows must be passing rows (bids 2..11)
    for ref in out["reference_rows"]:
        assert ref["booking_line_id"] in {str(i) for i in range(2, 12)}


def test_collect_s1_issue_rows_all_failing_gives_empty_reference_rows():
    """When every row fails the checked field AND no required_fields list is supplied,
    no fallback is possible so reference_rows must be an empty list (back-compat)."""
    linked = pd.DataFrame({
        "booking_line_id": [1, 2, 3],
        "account_number_group": ["", "", ""],
        "amount": [10.0, 20.0, 30.0],
        "fiscal_year": [2020, 2020, 2020],
    })
    raw = pd.DataFrame({"Account": ["", "", ""]})
    out = collect_s1_issue_rows(linked, raw, "account_number_group")

    assert out["stats"]["row_count"] == 3
    assert out["reference_rows"] == []
    assert out["reference_kind"] == "none"


# --------------------------------------------------------------------------- Bug B reproduction
_S1_REQUIRED = [
    "journal_entry_group_number",
    "fiscal_year",
    "line_number",
    "booking_line_id",
    "account_number_group",
    "amount",
    "posting_date",
]


def test_reference_rows_present_when_all_failing_rows_excluded():
    """Bug B (variant 1): when the only offending row(s) are excluded, ``rows`` is
    empty so the OLD code built ``columns=[]`` and returned ``reference_rows=[]``,
    even though passing rows for the SAME field still exist as a positive reference.
    """
    linked = pd.DataFrame({
        "booking_line_id": [1, 2, 3],
        "account_number_group": ["", "01041100", "01050000"],  # row 1 fails; 2,3 pass
        "amount": [0.0, 100.0, -100.0],
        "fiscal_year": [2020, 2020, 2020],
    })
    raw = pd.DataFrame({"Account": ["", "41100", "50000"], "Amount": [0, 100, -100]})

    out = collect_s1_issue_rows(
        linked, raw, "account_number_group", exclude_line_ids=[1]
    )

    assert out["total"] == 0                  # the sole offender was excluded
    assert out["columns"]                     # columns derived from reference candidates
    assert out["reference_rows"]              # passing rows STILL available as reference
    assert out["reference_kind"] == "same_field"
    for ref in out["reference_rows"]:
        assert ref["booking_line_id"] in ("2", "3")
        assert set(ref.keys()) == set(out["columns"])


def test_reference_rows_present_when_field_entirely_empty():
    """Bug B (variant 2): when the failing field is ENTIRELY empty (the typical S1
    failure) every row fails, so there is no same-field passing row. With the S1
    required-field list supplied, the LAST-RESORT fallback must still surface the
    best-populated rows as a reference (reference_kind='overall').
    """
    linked = pd.DataFrame({
        "booking_line_id": [1, 2, 3, 4],
        "journal_entry_group_number": ["01B1", "01B1", "01B2", "01B2"],
        "fiscal_year": [2020, 2020, 2020, 2020],
        "line_number": [1, 2, 1, 2],
        "account_number_group": ["01041100", "01070000", "01041100", "01070000"],
        "amount": [100.0, -100.0, 50.0, -50.0],
        "posting_date": ["", "", "", ""],  # required field entirely empty
    })
    raw = pd.DataFrame({
        "Doc": ["", "", "", ""],
        "Account": ["41100", "70000", "41100", "70000"],
    })

    out = collect_s1_issue_rows(
        linked, raw, "posting_date", required_fields=_S1_REQUIRED
    )

    assert out["total_offenders"] == 4
    assert out["offender_ids"] == ["1", "2", "3", "4"]
    assert out["reference_rows"]               # last-resort reference still available
    assert out["reference_kind"] == "overall"
    for ref in out["reference_rows"]:
        assert set(ref.keys()) == set(out["columns"])


def test_offender_ids_are_full_set_across_pages():
    """offender_ids/total_offenders must be the FULL offender set (all pages),
    independent of pagination (limit/offset) and of the text search filter."""
    bids = list(range(1, 13))                 # 12 rows, all fail the field
    linked = pd.DataFrame({
        "booking_line_id": bids,
        "account_number_group": [""] * 12,
        "amount": [0.0] * 12,
        "fiscal_year": [2020] * 12,
    })
    raw = pd.DataFrame({"Account": [""] * 12})

    out = collect_s1_issue_rows(
        linked, raw, "account_number_group", limit=5, offset=0
    )

    assert len(out["rows"]) == 5              # page is capped
    assert out["total"] == 12
    assert out["total_offenders"] == 12
    assert out["offender_ids"] == [str(b) for b in bids]   # full set regardless of paging


# --------------------------------------------------------------------------- huge / non-sequential booking_line_id (regression)
# booking_line_id is a 19-digit BLAKE2b hash masked to 62 bits — NOT a 1-based
# row position. The old code keyed into raw_df by (booking_line_id - 1), so every
# id fell out of range, source_row_at_line() returned None, and each row collapsed
# to {"booking_line_id": <huge int>} -> columns == ["booking_line_id"].
_HUGE_IDS = [
    2003686083711855000,
    4500000000000000001,
    1837465920564738291,
    3920184756103928475,
    882910283746500011,
    4611686018427387900,
]


def _huge_id_frames():
    """Synthetic CANONICAL `linked` frame with huge, non-sequential booking_line_ids.

    Rows for booking 01B200 (indices 2,3) have an EMPTY ``posting_date`` (the
    failing field); the other four rows are valid. A matching synthetic raw_df is
    supplied but the projection must NOT depend on it for canonical values.
    """
    linked = pd.DataFrame({
        "journal_entry_group_number": ["01B100", "01B100", "01B200", "01B200", "01B300", "01B300"],
        "fiscal_year": [2020, 2020, 2020, 2020, 2021, 2021],
        "line_number": [1, 2, 1, 2, 1, 2],
        "booking_line_id": _HUGE_IDS,
        "account_number_group": ["01041100", "01070000", "01041100", "01070000", "01041100", "01070000"],
        "amount": [100.0, -100.0, 50.0, -50.0, 25.0, -25.0],
        "posting_date": ["2020-01-15", "2020-01-15", "", "", "2021-03-01", "2021-03-01"],
        "document_type_code": ["RV", "RV", "RV", "RV", "RV", "RV"],  # extra mapped source-ish column
    })
    raw = pd.DataFrame({
        "Belegdatum": ["15.01.2020", "15.01.2020", "", "", "01.03.2021", "01.03.2021"],
        "Konto": ["41100", "70000", "41100", "70000", "41100", "70000"],
        "Betrag": [100, -100, 50, -50, 25, -25],
    })
    return linked, raw


def test_huge_ids_project_real_canonical_columns_and_failing_field():
    """Regression: with huge/non-sequential ids the failing AND reference rows must
    carry the real mapped canonical columns/values (multi-column), not collapse to
    the single booking_line_id hash column."""
    linked, raw = _huge_id_frames()

    out = collect_s1_issue_rows(
        linked, raw, "posting_date", required_fields=_S1_REQUIRED
    )

    # Multi-column projection from the canonical frame's own values.
    assert len(out["columns"]) > 1
    assert "account_number_group" in out["columns"]
    assert "amount" in out["columns"]
    assert "posting_date" in out["columns"]
    assert "document_type_code" in out["columns"]
    assert "booking_line_id" in out["columns"]  # still present for the exclusion checkbox

    # failing_field surfaced for column highlighting.
    assert out["failing_field"] == "posting_date"

    # Two offenders (01B200 lines, indices 2 and 3).
    assert out["stats"]["row_count"] == 2
    assert out["total_offenders"] == 2
    assert set(out["offender_ids"]) == {str(_HUGE_IDS[2]), str(_HUGE_IDS[3])}

    # Failing rows carry the real values, aligned to the right rows, ids as-is.
    failing = sorted(out["rows"], key=lambda r: r["booking_line_id"])
    by_id = {r["booking_line_id"]: r for r in out["rows"]}
    assert set(by_id) == {str(_HUGE_IDS[2]), str(_HUGE_IDS[3])}
    r2 = by_id[str(_HUGE_IDS[2])]
    assert r2["account_number_group"] == "01041100"
    assert r2["amount"] == 50.0
    assert r2["posting_date"] in (None, "")   # the failing field is empty
    assert r2["document_type_code"] == "RV"
    assert isinstance(r2["booking_line_id"], str)  # exact digit string (precision-safe)

    # Reference rows: 4 passing rows (posting_date present), same columns, real values.
    assert 4 <= len(out["reference_rows"]) <= 5
    assert out["reference_kind"] == "same_field"
    for ref in out["reference_rows"]:
        assert set(ref.keys()) == set(out["columns"])
        assert ref["booking_line_id"] in {str(_HUGE_IDS[0]), str(_HUGE_IDS[1]), str(_HUGE_IDS[4]), str(_HUGE_IDS[5])}
        assert ref["posting_date"] not in (None, "")  # passing rows have a posting date


def test_huge_ids_exclusions_still_drop_offenders():
    linked, raw = _huge_id_frames()
    out = collect_s1_issue_rows(
        linked, raw, "posting_date",
        exclude_line_ids=[_HUGE_IDS[2]],
        required_fields=_S1_REQUIRED,
    )
    assert out["stats"]["row_count"] == 1
    assert out["offender_ids"] == [str(_HUGE_IDS[3])]
    assert [r["booking_line_id"] for r in out["rows"]] == [str(_HUGE_IDS[3])]


# --------------------------------------------------------------------------- B1 booking drill-down
def _b1_frames():
    """Two bookings: B100 balances (0), B200 is a B1 offender (nets to +30)."""
    linked = pd.DataFrame({
        "booking_line_id": [1, 2, 3, 4, 5],
        "journal_entry_group_number": ["01B100", "01B100", "01B200", "01B200", "01B100"],
        "account_number_group": ["01041100", "01070000", "01041100", "01070000", "01080000"],
        "amount": [100.0, -100.0, 50.0, -20.0, 0.0],
        "fiscal_year": [2020, 2020, 2020, 2020, 2020],
    })
    raw = pd.DataFrame({
        "Account": ["41100", "70000", "41100", "70000", "80000"],
        "Amount": [100, -100, 50, -20, 0],
    })
    return linked, raw


def test_collect_b1_issue_rows_returns_booking_lines():
    linked, raw = _b1_frames()
    out = collect_b1_issue_rows(linked, raw, "01B100", 2020)

    # B100 has 3 lines (ids 1, 2, 5)
    assert out["stats"]["row_count"] == 3
    assert out["total"] == 3
    assert [r["booking_line_id"] for r in out["rows"]] == ["1", "2", "5"]
    # Balanced booking nets to 0
    assert out["summary_row"] == {"amount": 0.0}


def test_collect_b1_issue_rows_offender_sum_is_imbalance():
    linked, raw = _b1_frames()
    out = collect_b1_issue_rows(linked, raw, "01B200", 2020)

    assert out["stats"]["row_count"] == 2
    assert [r["booking_line_id"] for r in out["rows"]] == ["3", "4"]
    # 50 + (-20) = 30 — the non-zero imbalance, NOT 0
    assert out["summary_row"] == {"amount": 30.0}
    assert out["stats"]["amount_sum"] == 30.0


def test_collect_b1_issue_rows_respects_exclusions():
    linked, raw = _b1_frames()
    out = collect_b1_issue_rows(linked, raw, "01B200", 2020, exclude_line_ids=[4])

    # Line 4 dropped -> only line 3 remains, sum = 50
    assert out["stats"]["row_count"] == 1
    assert [r["booking_line_id"] for r in out["rows"]] == ["3"]
    assert out["summary_row"] == {"amount": 50.0}


def test_collect_b1_issue_rows_filters_by_fiscal_year():
    linked = pd.DataFrame({
        "booking_line_id": [1, 2, 3],
        "journal_entry_group_number": ["01B100", "01B100", "01B100"],
        "account_number_group": ["01041100", "01070000", "01041100"],
        "amount": [100.0, -100.0, 5.0],
        "fiscal_year": [2020, 2020, 2021],
    })
    raw = pd.DataFrame({"Account": ["41100", "70000", "41100"]})
    out = collect_b1_issue_rows(linked, raw, "01B100", 2021)

    assert out["stats"]["row_count"] == 1
    assert [r["booking_line_id"] for r in out["rows"]] == ["3"]
    assert out["summary_row"] == {"amount": 5.0}


def test_collect_b1_issue_rows_no_reference_rows():
    linked, raw = _b1_frames()
    out = collect_b1_issue_rows(linked, raw, "01B200", 2020)
    assert out["reference_rows"] == []


# --------------------------------------------------------------------------- Bug 1: float64 precision on 62-bit hash ids
def test_offender_ids_exact_for_huge_id_with_nan_in_column():
    """Bug 1 (CRITICAL): a 62-bit hash booking_line_id (> 2**53) must round-trip
    EXACTLY into ``offender_ids``, even when another row's booking_line_id is NaN.

    The old code piped the column through ``pd.to_numeric(...)`` which coerces to
    float64, rounding 2003686083711855000 -> 2003686083711855104 — a WRONG id that
    would exclude the WRONG row when fed back as ``exclude_line_ids``. An object-dtype
    column keeps the huge id as an exact Python int; the fix must preserve it.
    """
    huge = 2003686083711855000
    assert float(huge) != huge  # sanity: this id is genuinely unrepresentable in float64

    # Object dtype so the huge int stays an exact Python int alongside a NaN/None.
    linked = pd.DataFrame({
        "booking_line_id": pd.Series([huge, None], dtype=object),
        "account_number_group": ["", ""],   # both rows FAIL the checked field
        "amount": [10.0, 20.0],
        "fiscal_year": [2020, 2020],
    })
    raw = pd.DataFrame({"Account": ["", ""]})

    out = collect_s1_issue_rows(linked, raw, "account_number_group")

    # The NaN id is dropped; the huge id survives EXACTLY (no float rounding).
    assert out["offender_ids"] == [str(huge)]
    assert out["offender_ids"][0] == str(huge)     # exact, not 2003686083711855104
    assert out["total_offenders"] == 1


# --------------------------------------------------------------------------- Bug 2: B1 exclusion without a booking_line_id column
def test_collect_b1_issue_rows_exclusions_without_booking_line_id_column():
    """Bug 2: passing ``exclude_line_ids`` when the frame has NO ``booking_line_id``
    column must NOT raise (mirrors the S1 ``has_bid`` guard)."""
    linked = pd.DataFrame({
        "journal_entry_group_number": ["01B200", "01B200"],
        "account_number_group": ["01041100", "01070000"],
        "amount": [50.0, -20.0],
        "fiscal_year": [2020, 2020],
    })
    raw = pd.DataFrame({"Account": ["41100", "70000"]})

    # Must not raise even though booking_line_id is absent.
    out = collect_b1_issue_rows(
        linked, raw, "01B200", 2020, exclude_line_ids=[1, 2, 3]
    )

    assert out["stats"]["row_count"] == 2          # no rows dropped (no id column)
    assert out["summary_row"] == {"amount": 30.0}  # 50 + (-20)


# ================================================================= Precision bug: 62-bit ids must be strings (GL S1 exclude no-op)
#
# booking_line_id is a 62-bit BLAKE2b hash (see etl/mapping._BLID_MASK).
# Values > 2^53 (= JS Number.MAX_SAFE_INTEGER) are silently rounded when a
# JSON number is parsed by JavaScript float64:
#
#   e.g.  2003686083711855065  ->  int(float(2003686083711855065))
#       = 2003686083711855104  (WRONG — differs in the last digits)
#
# The browser reads offender_ids as float64, loses precision, sends the wrong
# id back in exclude_line_ids, and the server-side exclusion never matches ->
# "Exclude all N offending rows" is a no-op.
#
# The fix: offender_ids and per-row booking_line_id must be STRINGS end-to-end
# so the digit sequence round-trips through JavaScript without rounding.
#
# Tests labelled "RED today": assert the STRING contract that the current code
# violates (they will FAIL until the fix is applied).
# Tests labelled "GREEN today": document the bug or lock already-correct behaviour
# that must survive the fix.

_BID_HUGE = 2003686083711855065   # > 2^53 = 9007199254740992; float64 rounds it
_BID_HUGE_REF = _BID_HUGE + 1000  # passing row; also > 2^53, distinct from offender


def _bid_frames():
    """One failing row (empty account_number_group) with id=_BID_HUGE;
    one passing row with id=_BID_HUGE_REF that serves as a reference."""
    linked = pd.DataFrame({
        "booking_line_id": pd.Series([_BID_HUGE, _BID_HUGE_REF], dtype="int64"),
        "journal_entry_group_number": ["01B100", "01B200"],
        "fiscal_year": [2020, 2020],
        "account_number_group": ["", "01041100"],   # first fails, second passes
        "amount": [10.0, 20.0],
        "posting_date": ["2020-01-15", "2020-01-15"],
    })
    raw = pd.DataFrame({"Account": ["", "41100"]})
    return linked, raw


def test_bid_precision_offender_ids_must_be_strings():
    """RED today: collect_s1_issue_rows returns offender_ids as ints.

    After the fix offender_ids must be STRINGS so that the exact digit sequence
    round-trips through JavaScript without float64 rounding.
    """
    assert int(float(_BID_HUGE)) != _BID_HUGE, (
        "sanity: _BID_HUGE must be unrepresentable in float64 (proves the bug is real)"
    )
    linked, raw = _bid_frames()
    out = collect_s1_issue_rows(linked, raw, "account_number_group")

    assert out["offender_ids"], "expected at least one offender_id"
    for oid in out["offender_ids"]:
        assert isinstance(oid, str), (
            f"offender_ids elements must be str after the fix. "
            f"Got {type(oid).__name__}: {oid!r}. "
            "RED: current code returns int, which JSON-encodes as a number that "
            "JavaScript float64 rounds to a wrong value."
        )
    assert out["offender_ids"] == [str(_BID_HUGE)], (
        f"offender_ids must be ['{_BID_HUGE}'] (string). Got: {out['offender_ids']!r}"
    )


def test_bid_precision_rows_booking_line_id_must_be_string():
    """RED today: _booking_line_id_at returns int; per-row booking_line_id is int.

    After the fix rows[*].booking_line_id must be a STRING.
    """
    linked, raw = _bid_frames()
    out = collect_s1_issue_rows(linked, raw, "account_number_group")

    assert out["rows"], "expected at least one failing row"
    for row in out["rows"]:
        assert isinstance(row["booking_line_id"], str), (
            f"rows[*].booking_line_id must be str after the fix. "
            f"Got {type(row['booking_line_id']).__name__}: {row['booking_line_id']!r}"
        )
    assert out["rows"][0]["booking_line_id"] == str(_BID_HUGE), (
        f"rows[0].booking_line_id must equal '{_BID_HUGE}'. "
        f"Got: {out['rows'][0]['booking_line_id']!r}"
    )


def test_bid_precision_reference_rows_booking_line_id_must_be_string():
    """RED today: reference rows carry int booking_line_id via the same path.

    After the fix reference_rows[*].booking_line_id must be a STRING.
    """
    linked, raw = _bid_frames()
    out = collect_s1_issue_rows(linked, raw, "account_number_group")

    assert out["reference_rows"], "expected at least one reference row"
    for ref in out["reference_rows"]:
        assert isinstance(ref["booking_line_id"], str), (
            f"reference_rows[*].booking_line_id must be str after the fix. "
            f"Got {type(ref['booking_line_id']).__name__}: {ref['booking_line_id']!r}"
        )


def test_bid_precision_js_rounded_exclusion_is_noop():
    """GREEN today (documents the live bug) and GREEN after the fix.

    The id the browser sends back after float64 rounding does NOT match the exact
    row id -> no row is excluded. This must remain true after the fix because the
    browser will send the string id (exact), not the rounded int.
    """
    real_id = _BID_HUGE
    js_rounded = int(float(real_id))    # simulate JavaScript float64 coercion
    assert js_rounded != real_id, (
        "sanity: float64 must round this id to prove the no-op bug"
    )

    linked, raw = _bid_frames()

    out_bad = collect_s1_issue_rows(
        linked, raw, "account_number_group",
        exclude_line_ids=[js_rounded],
    )
    # The rounded id differs from the exact row id -> exclusion is a no-op
    assert out_bad["total"] == 1, (
        f"JS-rounded id {js_rounded} must NOT exclude real id {real_id}. "
        f"Got total={out_bad['total']} (expected 1 — the offender must still be present)."
    )


def test_bid_precision_exact_string_exclusion_removes_offender():
    """Locks the string-exclusion contract required after the fix.

    After the fix: collect_s1_issue_rows accepts a string in exclude_line_ids and
    matches by STRING comparison so the exact digit sequence is preserved.
    Currently: the function converts string -> int exactly (int("2003686083711855065")
    is exact in Python), so the exclusion also works — this test is GREEN today and
    must remain GREEN after the fix.
    """
    linked, raw = _bid_frames()

    out = collect_s1_issue_rows(
        linked, raw, "account_number_group",
        exclude_line_ids=[str(_BID_HUGE)],   # exact digit string
    )
    assert out["total"] == 0, (
        f"Excluding with the exact string '{_BID_HUGE}' must remove the offender. "
        f"Got total={out['total']}."
    )
    assert out["offender_ids"] == [], (
        "offender_ids must be empty after the offender is excluded"
    )
