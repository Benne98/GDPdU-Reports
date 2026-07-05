"""Tests for pure helpers in etl/load.py (no DB required).

Coverage:
  - content_hash: stable across reorderings, changes on data mutation
  - split_entry_line: correct dedup of entries, correct line projection,
    column availability, handles missing optional columns gracefully
  - _copy_field: NULL/empty-string/string/int/float/bool/date serialization
  - _bulk_insert_entries / _bulk_insert_lines: COPY-based contract —
    single copy_expert call (no BATCH_SIZE chunking), correct ON CONFLICT
    targets, NULL coalescing visible in the streamed CSV
  - vectorized entry filter: composite-key isin dedup
  - _filter_by_account_coverage: drops unmapped rows, prints report
"""
from __future__ import annotations

import csv
import io
from unittest.mock import MagicMock

import pandas as pd
import pytest

from etl.load import (
    BATCH_SIZE,
    _bulk_insert_entries,
    _bulk_insert_lines,
    _copy_field,
    _filter_by_account_coverage_pure,
    content_hash,
    content_hash_with_strategy,
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
# content_hash_with_strategy
# --------------------------------------------------------------------------- #

class TestContentHashWithStrategy:
    """Regression tests for the strategy-salted idempotency hash.

    Guards the fix for the false-skip bug: re-committing the SAME file with a
    DIFFERENT linking_strategy must produce a DIFFERENT hash so that
    replace_skip_ok (Clause 3) correctly refuses to skip the replace.
    """

    _STRATEGIES = ("txn", "gegenkonto", "none")

    def _df(self) -> pd.DataFrame:
        """Small synthetic GL frame — no DB required."""
        return pd.DataFrame({
            "journal_entry_group_number": ["010000000001", "010000000001", "010000000002"],
            "fiscal_year": [2024, 2024, 2024],
            "line_number": [1, 2, 1],
            "amount": [1000.0, -1000.0, 500.0],
        })

    def test_txn_differs_from_gegenkonto(self):
        """Core regression: 'txn' and 'gegenkonto' must never collide on the same df."""
        df = self._df()
        assert content_hash_with_strategy(df, "txn") != content_hash_with_strategy(df, "gegenkonto")

    def test_txn_differs_from_none(self):
        df = self._df()
        assert content_hash_with_strategy(df, "txn") != content_hash_with_strategy(df, "none")

    def test_gegenkonto_differs_from_none(self):
        df = self._df()
        assert content_hash_with_strategy(df, "gegenkonto") != content_hash_with_strategy(df, "none")

    def test_all_three_strategies_are_mutually_distinct(self):
        """All three valid strategies produce mutually distinct hashes."""
        df = self._df()
        hashes = [content_hash_with_strategy(df, s) for s in self._STRATEGIES]
        assert len(set(hashes)) == 3, (
            f"Expected 3 distinct hashes for strategies {self._STRATEGIES!r}, got {hashes}"
        )

    def test_same_strategy_is_deterministic(self):
        """Calling with the same (df, strategy) twice must return the identical hash."""
        df = self._df()
        for strategy in self._STRATEGIES:
            h1 = content_hash_with_strategy(df, strategy)
            h2 = content_hash_with_strategy(df, strategy)
            assert h1 == h2, f"Non-deterministic hash for strategy={strategy!r}"

    def test_differs_from_base_content_hash(self):
        """The strategy-salted hash must differ from the plain content_hash for all strategies.

        If this fails, the NUL-byte salt is broken and the false-skip bug is back.
        """
        df = self._df()
        base = content_hash(df)
        for strategy in self._STRATEGIES:
            h = content_hash_with_strategy(df, strategy)
            assert h != base, (
                f"content_hash_with_strategy(df, {strategy!r}) == content_hash(df): "
                "the NUL-byte salt is missing or broken — false-skip bug not fixed."
            )

    def test_hash_is_64_hex_chars(self):
        """Output is a 64-char lowercase hex string (SHA-256)."""
        df = self._df()
        h = content_hash_with_strategy(df, "txn")
        assert isinstance(h, str)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_row_order_independent(self):
        """Row order must not affect the salted hash (same as base content_hash)."""
        df = self._df()
        shuffled = df.sample(frac=1, random_state=7).reset_index(drop=True)
        for strategy in self._STRATEGIES:
            assert content_hash_with_strategy(df, strategy) == content_hash_with_strategy(shuffled, strategy)

    def test_data_mutation_changes_hash(self):
        """A change to the underlying data must also change the strategy-salted hash."""
        df = self._df()
        df2 = df.copy()
        df2.loc[0, "amount"] = df2.loc[0, "amount"] + 0.01
        for strategy in self._STRATEGIES:
            assert content_hash_with_strategy(df, strategy) != content_hash_with_strategy(df2, strategy)


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
# _copy_field serialization — unit tests (no DB, no mock)
# --------------------------------------------------------------------------- #

class TestCopyField:
    """_copy_field renders Python scalars as COPY FORMAT csv NULL '\\N' tokens.

    Postgres COPY with FORMAT csv treats an *unquoted* \\N as NULL and a
    *quoted* "" (empty) as an empty string, never NULL — which is exactly the
    distinction between None and '' that the old executemany path preserved.
    """

    def test_none_is_null_marker(self):
        assert _copy_field(None) == "\\N"

    def test_empty_string_is_double_quoted_not_null(self):
        # '""' in the CSV is an empty string; unquoted \\N would be NULL.
        assert _copy_field("") == '""'

    def test_plain_string_is_double_quoted(self):
        assert _copy_field("EUR") == '"EUR"'

    def test_embedded_comma_is_quoted(self):
        # A comma inside a quoted field is NOT a delimiter.
        assert _copy_field("a,b") == '"a,b"'

    def test_embedded_double_quote_escaped_as_double_double(self):
        # RFC-4180: " inside a quoted field → "".
        assert _copy_field('say "hi"') == '"say ""hi"""'

    def test_embedded_newline_stays_inside_quoted_field(self):
        assert _copy_field("line\nbreak") == '"line\nbreak"'

    def test_int_no_quotes(self):
        assert _copy_field(0) == "0"
        assert _copy_field(1) == "1"
        assert _copy_field(-42) == "-42"

    def test_float_full_precision_via_repr(self):
        v = 1.2345678901234567
        assert _copy_field(v) == repr(v)

    def test_bool_true_rendered_before_int_branch(self):
        # bool is a subclass of int; must be dispatched before int so that
        # True → "true" not "1".
        assert _copy_field(True) == "true"

    def test_bool_false(self):
        assert _copy_field(False) == "false"

    def test_date_is_isoformat_quoted(self):
        import datetime
        d = datetime.date(2024, 3, 15)
        assert _copy_field(d) == '"2024-03-15"'

    def test_pandas_timestamp_is_isoformat_quoted(self):
        import pandas as pd
        ts = pd.Timestamp("2024-03-15 09:30:00")
        assert _copy_field(ts) == '"' + ts.isoformat() + '"'


# --------------------------------------------------------------------------- #
# Bulk insert helpers — COPY-path contract (mock session, no DB)
# --------------------------------------------------------------------------- #
#
# The new bulk-insert path calls psycopg2 copy_expert ONCE per table (no
# BATCH_SIZE chunking) and then runs INSERT … SELECT … ON CONFLICT DO NOTHING
# to apply the dedup constraint.  The old session.execute(sql, params_list)
# executemany pattern is gone.  Tests verify:
#   - exactly 1 copy_expert call regardless of row count
#   - CSV row count == input rows
#   - NULL coalescing visible as \\N in the streamed CSV
#   - ON CONFLICT target matches the table's unique constraint


def _make_copy_session():
    """Return (session, captured) where captured['calls'] collects
    each {sql, csv} dict recorded from copy_expert invocations."""
    session = MagicMock()
    captured: dict = {"calls": []}

    def _on_copy(sql, buf):
        captured["calls"].append({"sql": sql, "csv": buf.read()})

    session.connection.return_value.connection.cursor.return_value.copy_expert.side_effect = _on_copy
    return session, captured


class TestBulkInsertEntries:
    """_bulk_insert_entries: single COPY call, correct ON CONFLICT, NULL coalescing."""

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

    def test_copy_expert_called_once_no_batching(self):
        """All rows stream in one copy_expert call — BATCH_SIZE chunking is gone."""
        session, captured = _make_copy_session()
        _bulk_insert_entries(session, self._make_entries(BATCH_SIZE + 1))
        assert len(captured["calls"]) == 1, (
            f"Expected 1 copy_expert call (COPY streams once), got {len(captured['calls'])}"
        )

    def test_csv_row_count_matches_entry_count(self):
        """Streamed CSV has exactly n data rows (no header line in COPY CSV)."""
        n = 5
        session, captured = _make_copy_session()
        _bulk_insert_entries(session, self._make_entries(n))
        csv_text = captured["calls"][0]["csv"]
        rows = [line for line in csv_text.splitlines() if line]
        assert len(rows) == n

    def test_nan_fiscal_period_renders_null_marker_in_csv(self):
        """NaN fiscal_period → NULL token \\N in the streamed CSV (field index 2)."""
        session, captured = _make_copy_session()
        df = self._make_entries(1)
        df.loc[0, "fiscal_period"] = float("nan")
        _bulk_insert_entries(session, df)
        csv_text = captured["calls"][0]["csv"]
        # param_keys order: jegn(0) fy(1) fp(2) et(3) pd(4) dd(5) dtc(6) rdn(7) cc(8) hn(9) ss(10)
        row = next(csv.reader(io.StringIO(csv_text)))
        assert row[2] == "\\N", f"Expected \\N for NaN fiscal_period, got {row[2]!r}"

    def test_none_currency_code_defaults_to_eur_in_csv(self):
        """None currency_code → 'EUR' default in the streamed CSV (field index 8)."""
        session, captured = _make_copy_session()
        df = self._make_entries(1)
        df.loc[0, "currency_code"] = None
        _bulk_insert_entries(session, df)
        csv_text = captured["calls"][0]["csv"]
        row = next(csv.reader(io.StringIO(csv_text)))
        assert row[8] == "EUR", f"Expected EUR default, got {row[8]!r}"

    def test_on_conflict_target_is_jegn_fiscal_year(self):
        """INSERT SELECT ON CONFLICT must target (journal_entry_group_number, fiscal_year)."""
        session, _ = _make_copy_session()
        _bulk_insert_entries(session, self._make_entries(2))
        insert_sqls = [
            str(c.args[0])
            for c in session.execute.call_args_list
            if "ON CONFLICT" in str(c.args[0])
        ]
        assert insert_sqls, "No INSERT ... ON CONFLICT SQL was executed"
        assert "(journal_entry_group_number, fiscal_year)" in insert_sqls[0], (
            f"ON CONFLICT target missing from: {insert_sqls[0][:300]}"
        )


class TestBulkInsertLines:
    """_bulk_insert_lines: single COPY call, correct ON CONFLICT, NULL coalescing."""

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

    def test_copy_expert_called_once_no_chunking(self):
        """All rows stream in one copy_expert call regardless of row count."""
        session, captured = _make_copy_session()
        _bulk_insert_lines(session, self._make_lines(BATCH_SIZE + 2))
        assert len(captured["calls"]) == 1

    def test_csv_row_count_matches_line_count(self):
        """Streamed CSV has exactly n data rows."""
        n = 7
        session, captured = _make_copy_session()
        _bulk_insert_lines(session, self._make_lines(n))
        csv_text = captured["calls"][0]["csv"]
        rows = [line for line in csv_text.splitlines() if line]
        assert len(rows) == n

    def test_none_vat_amount_renders_null_marker_in_csv(self):
        """None vat_amount → NULL token \\N in the streamed CSV (field index 6)."""
        session, captured = _make_copy_session()
        _bulk_insert_lines(session, self._make_lines(1))
        csv_text = captured["calls"][0]["csv"]
        # param_keys order: jegn(0) fy(1) ln(2) bid(3) ang(4) amt(5) vat(6) ...
        row = next(csv.reader(io.StringIO(csv_text)))
        assert row[6] == "\\N", f"Expected \\N for None vat_amount, got {row[6]!r}"

    def test_on_conflict_target_is_jegn_fy_line_number(self):
        """INSERT SELECT ON CONFLICT must target (journal_entry_group_number, fiscal_year, line_number)."""
        session, _ = _make_copy_session()
        _bulk_insert_lines(session, self._make_lines(2))
        insert_sqls = [
            str(c.args[0])
            for c in session.execute.call_args_list
            if "ON CONFLICT" in str(c.args[0])
        ]
        assert insert_sqls, "No INSERT ... ON CONFLICT SQL was executed"
        assert "(journal_entry_group_number, fiscal_year, line_number)" in insert_sqls[0], (
            f"ON CONFLICT target missing from: {insert_sqls[0][:300]}"
        )


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
