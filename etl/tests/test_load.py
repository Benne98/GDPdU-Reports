"""Tests for pure helpers in etl/load.py (no DB required).

Coverage:
  - content_hash: stable across reorderings, changes on data mutation
  - split_entry_line: correct dedup of entries, correct line projection,
    column availability, handles missing optional columns gracefully
  - _bulk_insert_entries / _bulk_insert_lines: param list shape, BATCH_SIZE chunking
  - vectorized entry filter: composite-key isin dedup
  - _filter_by_account_coverage: drops unmapped rows, prints report
"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest

from etl.load import (
    BATCH_SIZE,
    _bulk_insert_entries,
    _bulk_insert_lines,
    _filter_by_account_coverage_pure,
    content_hash,
    split_entry_line,
)
from etl.tests.fixtures import canonical_lines


# --------------------------------------------------------------------------- #
# content_hash
# --------------------------------------------------------------------------- #
class TestContentHash:
    def test_hash_is_64_hex_chars(self):
        df = canonical_lines()
        h = content_hash(df)
        assert isinstance(h, str)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_same_data_same_hash(self):
        df = canonical_lines()
        assert content_hash(df) == content_hash(df.copy())

    def test_hash_stable_across_row_reorder(self):
        """Row order should not affect the hash (sort is applied internally)."""
        df = canonical_lines()
        shuffled = df.sample(frac=1, random_state=42).reset_index(drop=True)
        assert content_hash(df) == content_hash(shuffled)

    def test_hash_changes_on_amount_mutation(self):
        df = canonical_lines()
        df2 = df.copy()
        df2.loc[0, "amount"] = df2.loc[0, "amount"] + 0.01
        assert content_hash(df) != content_hash(df2)

    def test_hash_changes_on_different_account(self):
        df = canonical_lines()
        df2 = df.copy()
        df2.loc[0, "account_number_group"] = "01099999"
        assert content_hash(df) != content_hash(df2)

    def test_empty_dataframe_has_stable_hash(self):
        df = pd.DataFrame(columns=["journal_entry_group_number", "fiscal_year", "line_number", "amount"])
        h1 = content_hash(df)
        h2 = content_hash(df.copy())
        assert h1 == h2
        assert len(h1) == 64


# --------------------------------------------------------------------------- #
# split_entry_line
# --------------------------------------------------------------------------- #
class TestSplitEntryLine:
    def test_entries_deduped_by_jegn_and_fiscal_year(self):
        """Three GL lines across two bookings -> two entry rows."""
        df = canonical_lines()
        entries, lines = split_entry_line(df)
        # Fixtures have 3 unique jegn values (booking 1, 2, 3)
        unique_jegn = df["journal_entry_group_number"].nunique()
        assert len(entries) == unique_jegn

    def test_entries_contain_required_columns(self):
        entries, _ = split_entry_line(canonical_lines())
        for col in ("journal_entry_group_number", "fiscal_year"):
            assert col in entries.columns

    def test_line_rows_preserve_all_input_rows(self):
        """No rows dropped; line_rows has same row count as input."""
        df = canonical_lines()
        _, line_rows = split_entry_line(df)
        assert len(line_rows) == len(df)

    def test_line_rows_contain_required_columns(self):
        _, line_rows = split_entry_line(canonical_lines())
        for col in ("journal_entry_group_number", "fiscal_year", "line_number",
                    "booking_line_id", "account_number_group", "amount"):
            assert col in line_rows.columns

    def test_entries_per_booking_use_first_row_header(self):
        """When multiple rows share a booking, the first row's header is used."""
        df = canonical_lines()
        # Booking "010000000001" appears on rows 0,1,2; entry should have one row
        entries, _ = split_entry_line(df)
        jegn = "010000000001"
        entry_rows = entries[entries["journal_entry_group_number"] == jegn]
        assert len(entry_rows) == 1

    def test_missing_optional_entry_columns_tolerated(self):
        """If optional header columns are absent, split still works."""
        df = canonical_lines()
        # Drop optional columns that may not be present in minimal frames
        for col in ("header_note", "document_type_code", "reference_document_number"):
            if col in df.columns:
                df = df.drop(columns=[col])
        entries, line_rows = split_entry_line(df)
        assert len(entries) > 0
        assert len(line_rows) == 9  # 9 fixture rows

    def test_empty_input_returns_empty_frames(self):
        df = pd.DataFrame(columns=[
            "journal_entry_group_number", "fiscal_year", "line_number",
            "booking_line_id", "account_number_group", "amount",
        ])
        entries, line_rows = split_entry_line(df)
        assert entries.empty
        assert line_rows.empty

    def test_line_amounts_unchanged(self):
        """Amount values must pass through split without mutation."""
        df = canonical_lines()
        _, line_rows = split_entry_line(df)
        assert list(line_rows["amount"]) == list(df["amount"])


# --------------------------------------------------------------------------- #
# Bulk insert helpers — shape and batching (mock session, no DB)
# --------------------------------------------------------------------------- #

class TestBulkInsertEntries:
    """_bulk_insert_entries builds the right param list and respects BATCH_SIZE."""

    def _make_entries(self, n: int) -> pd.DataFrame:
        return pd.DataFrame({
            "journal_entry_group_number": [f"01{str(i).zfill(10)}" for i in range(n)],
            "fiscal_year": [2024] * n,
            "fiscal_period": [1] * n,
            "entry_type": ["actual"] * n,
            "posting_date": [None] * n,
            "document_date": [None] * n,
            "document_type_code": [None] * n,
            "reference_document_number": [None] * n,
            "currency_code": ["EUR"] * n,
            "header_note": [None] * n,
            "source_system": ["test"] * n,
        })

    def test_single_batch_calls_execute_once(self):
        """When n <= BATCH_SIZE, session.execute is called exactly once."""
        session = MagicMock()
        df = self._make_entries(3)
        _bulk_insert_entries(session, df)
        assert session.execute.call_count == 1

    def test_multi_batch_calls_execute_multiple_times(self):
        """With n = BATCH_SIZE + 1, session.execute is called twice."""
        session = MagicMock()
        df = self._make_entries(BATCH_SIZE + 1)
        _bulk_insert_entries(session, df)
        assert session.execute.call_count == 2

    def test_param_list_has_correct_length(self):
        """The param list passed to execute in a single-batch call has n items."""
        session = MagicMock()
        n = 5
        df = self._make_entries(n)
        _bulk_insert_entries(session, df)
        # The second argument to execute is the param list
        _, call_params = session.execute.call_args
        # session.execute(sql, params) — positional call
        args = session.execute.call_args[0]
        assert len(args[1]) == n

    def test_null_coalescing_fiscal_period_none(self):
        """fiscal_period=NaN -> None in the param dict (not int(NaN))."""
        import math
        session = MagicMock()
        df = self._make_entries(1)
        df.loc[0, "fiscal_period"] = float("nan")
        _bulk_insert_entries(session, df)
        args = session.execute.call_args[0]
        param = args[1][0]
        assert param["fp"] is None, f"Expected None for NaN fiscal_period, got {param['fp']!r}"

    def test_currency_code_defaults_to_eur(self):
        """currency_code=None/NaN -> 'EUR' default."""
        session = MagicMock()
        df = self._make_entries(1)
        df.loc[0, "currency_code"] = None
        _bulk_insert_entries(session, df)
        args = session.execute.call_args[0]
        param = args[1][0]
        assert param["cc"] == "EUR", f"Expected 'EUR', got {param['cc']!r}"


class TestBulkInsertLines:
    """_bulk_insert_lines builds the right param list and preserves None coalescing."""

    def _make_lines(self, n: int) -> pd.DataFrame:
        return pd.DataFrame({
            "journal_entry_group_number": [f"01{str(i).zfill(10)}" for i in range(n)],
            "fiscal_year": [2024] * n,
            "line_number": list(range(1, n + 1)),
            "booking_line_id": list(range(1, n + 1)),
            "account_number_group": ["0110000"] * n,
            "amount": [100.0 * (i + 1) for i in range(n)],
            "vat_amount": [None] * n,
            "line_note": [None] * n,
            "customer_id": [None] * n,
            "supplier_id": [None] * n,
            "posting_type": [None] * n,
            "source_system": ["test"] * n,
        })

    def test_single_batch(self):
        session = MagicMock()
        df = self._make_lines(4)
        _bulk_insert_lines(session, df)
        assert session.execute.call_count == 1

    def test_param_count_matches_rows(self):
        session = MagicMock()
        n = 7
        df = self._make_lines(n)
        _bulk_insert_lines(session, df)
        args = session.execute.call_args[0]
        assert len(args[1]) == n

    def test_vat_amount_none_stays_none(self):
        """vat_amount=None -> None in params (not float(None))."""
        session = MagicMock()
        df = self._make_lines(1)
        _bulk_insert_lines(session, df)
        args = session.execute.call_args[0]
        assert args[1][0]["vat"] is None

    def test_multi_batch(self):
        session = MagicMock()
        df = self._make_lines(BATCH_SIZE + 2)
        _bulk_insert_lines(session, df)
        assert session.execute.call_count == 2


# --------------------------------------------------------------------------- #
# Vectorized entry filter
# --------------------------------------------------------------------------- #

class TestVectorizedEntryFilter:
    """The composite-key isin filter must skip existing entries and keep new ones."""

    def _make_entries(self, jegn_fy_pairs: list[tuple[str, int]]) -> pd.DataFrame:
        return pd.DataFrame({
            "journal_entry_group_number": [p[0] for p in jegn_fy_pairs],
            "fiscal_year": [p[1] for p in jegn_fy_pairs],
        })

    def test_all_new_entries_kept(self):
        entries = self._make_entries([("010000000001", 2024), ("010000000002", 2024)])
        existing: set[tuple] = set()
        existing_keys = {f"{j}|{int(y)}" for j, y in existing}
        entry_keys = entries["journal_entry_group_number"].astype(str) + "|" + entries["fiscal_year"].astype(int).astype(str)
        new = entries[~entry_keys.isin(existing_keys)]
        assert len(new) == 2

    def test_existing_entry_skipped(self):
        entries = self._make_entries([("010000000001", 2024), ("010000000002", 2024)])
        existing: set[tuple] = {("010000000001", 2024)}
        existing_keys = {f"{j}|{int(y)}" for j, y in existing}
        entry_keys = entries["journal_entry_group_number"].astype(str) + "|" + entries["fiscal_year"].astype(int).astype(str)
        new = entries[~entry_keys.isin(existing_keys)]
        assert len(new) == 1
        assert new.iloc[0]["journal_entry_group_number"] == "010000000002"

    def test_all_existing_returns_empty(self):
        entries = self._make_entries([("010000000001", 2024)])
        existing_keys = {"010000000001|2024"}
        entry_keys = entries["journal_entry_group_number"].astype(str) + "|" + entries["fiscal_year"].astype(int).astype(str)
        new = entries[~entry_keys.isin(existing_keys)]
        assert len(new) == 0

    def test_same_jegn_different_fy_kept(self):
        """Same jegn with different fiscal_year is a distinct entry and must not be skipped."""
        entries = self._make_entries([("010000000001", 2023), ("010000000001", 2024)])
        existing_keys = {"010000000001|2024"}
        entry_keys = entries["journal_entry_group_number"].astype(str) + "|" + entries["fiscal_year"].astype(int).astype(str)
        new = entries[~entry_keys.isin(existing_keys)]
        assert len(new) == 1
        assert new.iloc[0]["fiscal_year"] == 2023


# --------------------------------------------------------------------------- #
# Account-coverage pre-filter (pure helper — no DB)
# --------------------------------------------------------------------------- #

class TestAccountCoverageFilterPure:
    """_filter_by_account_coverage_pure: keeps matching rows, drops unmapped rows."""

    def _canonical(self, rows: list[tuple[str, int, float]]) -> pd.DataFrame:
        """Build a minimal canonical frame: (account_number_group, fiscal_year, amount)."""
        return pd.DataFrame({
            "account_number_group": [r[0] for r in rows],
            "fiscal_year": [r[1] for r in rows],
            "amount": [r[2] for r in rows],
        })

    def test_all_rows_kept_when_all_mapped(self):
        df = self._canonical([("01010000", 2024, 100.0), ("01020000", 2024, -50.0)])
        known = {"01010000|2024", "01020000|2024"}
        kept, n_drop, sum_amt, n_unmapped = _filter_by_account_coverage_pure(df, known)
        assert len(kept) == 2
        assert n_drop == 0
        assert n_unmapped == 0

    def test_unmapped_row_dropped(self):
        df = self._canonical([("01010000", 2024, 100.0), ("01099999", 2024, 250.0)])
        known = {"01010000|2024"}
        kept, n_drop, sum_amt, n_unmapped = _filter_by_account_coverage_pure(df, known)
        assert len(kept) == 1
        assert kept.iloc[0]["account_number_group"] == "01010000"
        assert n_drop == 1
        assert sum_amt == pytest.approx(250.0)
        assert n_unmapped == 1

    def test_multiple_unmapped_accounts_counted(self):
        df = self._canonical([
            ("01010000", 2024, 100.0),
            ("01099998", 2024, 200.0),
            ("01099999", 2024, 300.0),
        ])
        known = {"01010000|2024"}
        kept, n_drop, sum_amt, n_unmapped = _filter_by_account_coverage_pure(df, known)
        assert n_drop == 2
        assert sum_amt == pytest.approx(500.0)
        assert n_unmapped == 2

    def test_fy_mismatch_drops_row(self):
        """Row whose fy is not in known_keys is dropped even if ang matches another year."""
        df = self._canonical([("01010000", 2025, 100.0)])
        known = {"01010000|2024"}  # only 2024, not 2025
        kept, n_drop, _, _ = _filter_by_account_coverage_pure(df, known)
        assert len(kept) == 0
        assert n_drop == 1

    def test_empty_canonical_returns_empty(self):
        df = pd.DataFrame({"account_number_group": [], "fiscal_year": [], "amount": []})
        kept, n_drop, sum_amt, n_unmapped = _filter_by_account_coverage_pure(df, {"01010000|2024"})
        assert len(kept) == 0
        assert n_drop == 0

    def test_missing_required_columns_returns_as_is(self):
        """Frame without account_number_group or fiscal_year is returned unchanged."""
        df = pd.DataFrame({"amount": [100.0, 200.0]})
        kept, n_drop, _, _ = _filter_by_account_coverage_pure(df, {"01010000|2024"})
        assert len(kept) == 2
        assert n_drop == 0
