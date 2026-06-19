"""Tests for DF3 — partner extraction (pure, no DB required).

Coverage:
  - Basic extraction: customer '01100' -> debtor_number '100';
    supplier '01200' -> creditor_number '200'.
  - Cash-sale lines (no customer_id) produce an empty customers frame.
  - Missing supplier_id column is tolerated.
  - Collective bookings (multiple distinct partners) yields one row per
    distinct id.
  - Optional name/geo columns are carried through when present.
  - Optional name/geo columns are absent when not on the lines (no KeyError).
  - Explicit source_no column overrides the strip-prefix fallback.
  - Duplicate lines for the same partner id are deduplicated (first non-null
    attribute wins — mirrors the COALESCE-on-update DB strategy).
"""
from __future__ import annotations

import pandas as pd
import pytest

from etl.derive import extract_partners
from etl.tests.fixtures import canonical_lines


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _with_cols(df: pd.DataFrame, **extra_cols) -> pd.DataFrame:
    """Return df with additional constant columns added."""
    out = df.copy()
    for col, val in extra_cols.items():
        out[col] = val
    return out


# --------------------------------------------------------------------------- #
# Basic extraction from standard fixture
# --------------------------------------------------------------------------- #

class TestExtractPartnersBasic:
    def test_returns_two_dataframes(self):
        lines = canonical_lines()
        result = extract_partners(lines)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_customer_id_present(self):
        lines = canonical_lines()
        customers, _ = extract_partners(lines)
        assert "01100" in customers["customer_id"].values

    def test_debtor_number_strips_prefix(self):
        """customer_id '01100' -> debtor_number '100' (strip 2-char prefix)."""
        lines = canonical_lines()
        customers, _ = extract_partners(lines)
        row = customers[customers["customer_id"] == "01100"].iloc[0]
        assert row["debtor_number"] == "100"

    def test_supplier_id_present(self):
        lines = canonical_lines()
        _, suppliers = extract_partners(lines)
        assert "01200" in suppliers["supplier_id"].values

    def test_creditor_number_strips_prefix(self):
        """supplier_id '01200' -> creditor_number '200' (strip 2-char prefix)."""
        lines = canonical_lines()
        _, suppliers = extract_partners(lines)
        row = suppliers[suppliers["supplier_id"] == "01200"].iloc[0]
        assert row["creditor_number"] == "200"

    def test_one_distinct_customer_row(self):
        """Fixture has customer 01100 on two bookings; output should be 1 row."""
        lines = canonical_lines()
        customers, _ = extract_partners(lines)
        assert len(customers[customers["customer_id"] == "01100"]) == 1

    def test_one_distinct_supplier_row(self):
        lines = canonical_lines()
        _, suppliers = extract_partners(lines)
        assert len(suppliers[suppliers["supplier_id"] == "01200"]) == 1


# --------------------------------------------------------------------------- #
# Cash sale — no customer_id on lines
# --------------------------------------------------------------------------- #

class TestCashSale:
    def test_no_customer_id_values_yields_empty_customers(self):
        """All customer_id values are None -> customers frame is empty."""
        lines = canonical_lines().copy()
        lines["customer_id"] = None
        customers, _ = extract_partners(lines)
        assert customers.empty

    def test_empty_customers_has_expected_columns(self):
        lines = canonical_lines().copy()
        lines["customer_id"] = None
        customers, _ = extract_partners(lines)
        assert "customer_id" in customers.columns
        assert "debtor_number" in customers.columns

    def test_no_customer_id_column_at_all(self):
        """customer_id column absent entirely -> empty frame, no error."""
        lines = canonical_lines().drop(columns=["customer_id"])
        customers, _ = extract_partners(lines)
        assert customers.empty

    def test_no_supplier_id_column_at_all(self):
        """supplier_id column absent -> empty suppliers frame, no error."""
        lines = canonical_lines().drop(columns=["supplier_id"])
        _, suppliers = extract_partners(lines)
        assert suppliers.empty


# --------------------------------------------------------------------------- #
# Collective bookings (multiple distinct partners)
# --------------------------------------------------------------------------- #

class TestCollectiveBooking:
    def _multi_customer_lines(self) -> pd.DataFrame:
        """Three lines with three distinct customer ids."""
        rows = [
            {"customer_id": "01100", "supplier_id": None, "amount": -100.0},
            {"customer_id": "01101", "supplier_id": None, "amount": -200.0},
            {"customer_id": "01102", "supplier_id": None, "amount": -300.0},
        ]
        return pd.DataFrame(rows)

    def test_three_distinct_customers(self):
        lines = self._multi_customer_lines()
        customers, _ = extract_partners(lines)
        assert set(customers["customer_id"]) == {"01100", "01101", "01102"}

    def test_debtor_numbers_all_stripped(self):
        lines = self._multi_customer_lines()
        customers, _ = extract_partners(lines)
        assert set(customers["debtor_number"]) == {"100", "101", "102"}

    def test_two_distinct_suppliers(self):
        rows = [
            {"customer_id": None, "supplier_id": "01200"},
            {"customer_id": None, "supplier_id": "01201"},
            {"customer_id": None, "supplier_id": "01200"},  # duplicate
        ]
        lines = pd.DataFrame(rows)
        _, suppliers = extract_partners(lines)
        assert len(suppliers) == 2
        assert set(suppliers["supplier_id"]) == {"01200", "01201"}


# --------------------------------------------------------------------------- #
# Optional name / geo columns
# --------------------------------------------------------------------------- #

class TestOptionalAttributes:
    def test_name_col_absent_no_error(self):
        """No customer_name column -> no name_line_1, no KeyError."""
        lines = canonical_lines()
        customers, _ = extract_partners(lines)
        assert "name_line_1" not in customers.columns

    def test_name_col_present_carried_through(self):
        lines = _with_cols(canonical_lines(), customer_name="Acme GmbH")
        customers, _ = extract_partners(lines)
        assert "name_line_1" in customers.columns
        assert customers.loc[customers["customer_id"] == "01100", "name_line_1"].iloc[0] == "Acme GmbH"

    def test_supplier_name_carried_through(self):
        lines = _with_cols(canonical_lines(), supplier_name="Lieferant AG")
        _, suppliers = extract_partners(lines)
        assert "name_line_1" in suppliers.columns
        assert suppliers.loc[suppliers["supplier_id"] == "01200", "name_line_1"].iloc[0] == "Lieferant AG"

    def test_geo_columns_carried_through(self):
        lines = _with_cols(
            canonical_lines(),
            country_code="DEU",
            region_code="BY",
            city="Munich",
            postal_code="80331",
        )
        customers, _ = extract_partners(lines)
        row = customers.loc[customers["customer_id"] == "01100"].iloc[0]
        assert row["country_code"] == "DEU"
        assert row["city"] == "Munich"

    def test_geo_columns_absent_no_error(self):
        """Lines without geo columns -> no geo columns on output, no error."""
        lines = canonical_lines()
        customers, _ = extract_partners(lines)
        for col in ("country_code", "region_code", "city", "postal_code"):
            assert col not in customers.columns


# --------------------------------------------------------------------------- #
# Explicit source_no overrides strip-prefix fallback
# --------------------------------------------------------------------------- #

class TestSourceNo:
    def test_source_no_used_as_debtor_number(self):
        """When source_no is present, it replaces the strip-prefix derivation."""
        lines = canonical_lines().copy()
        # Add an explicit source_no for the customer lines.
        lines.loc[lines["customer_id"] == "01100", "source_no"] = "C-100-EXPLICIT"
        customers, _ = extract_partners(lines)
        row = customers.loc[customers["customer_id"] == "01100"].iloc[0]
        assert row["debtor_number"] == "C-100-EXPLICIT"

    def test_source_no_not_in_output_columns(self):
        """source_no should be consumed; it must not appear as a standalone column."""
        lines = _with_cols(canonical_lines(), source_no="X")
        customers, _ = extract_partners(lines)
        assert "source_no" not in customers.columns

    def test_source_no_null_falls_back_to_strip(self):
        """If source_no is present but null for a row, strip-prefix is used instead."""
        lines = canonical_lines().copy()
        lines["source_no"] = None
        customers, _ = extract_partners(lines)
        row = customers.loc[customers["customer_id"] == "01100"].iloc[0]
        assert row["debtor_number"] == "100"


# --------------------------------------------------------------------------- #
# Deduplication — first non-null attribute wins (mirrors COALESCE-on-update)
# --------------------------------------------------------------------------- #

class TestDeduplication:
    def test_duplicate_rows_same_id_yield_one_row(self):
        rows = [
            {"customer_id": "01100", "customer_name": "First Name"},
            {"customer_id": "01100", "customer_name": "Second Name"},
        ]
        lines = pd.DataFrame(rows)
        customers, _ = extract_partners(lines)
        assert len(customers) == 1

    def test_first_non_null_name_wins(self):
        """First non-null value for name_line_1 is kept (coalesce semantics)."""
        rows = [
            {"customer_id": "01100", "customer_name": None},
            {"customer_id": "01100", "customer_name": "Real Name"},
            {"customer_id": "01100", "customer_name": "Other Name"},
        ]
        lines = pd.DataFrame(rows)
        customers, _ = extract_partners(lines)
        assert customers.iloc[0]["name_line_1"] == "Real Name"

    def test_all_null_attr_stays_null(self):
        rows = [
            {"customer_id": "01100", "customer_name": None},
            {"customer_id": "01100", "customer_name": None},
        ]
        lines = pd.DataFrame(rows)
        customers, _ = extract_partners(lines)
        assert customers.iloc[0]["name_line_1"] is None


# --------------------------------------------------------------------------- #
# Output schema contracts
# --------------------------------------------------------------------------- #

class TestOutputSchema:
    def test_customer_id_is_first_column(self):
        lines = canonical_lines()
        customers, _ = extract_partners(lines)
        assert customers.columns[0] == "customer_id"

    def test_debtor_number_is_second_column(self):
        lines = canonical_lines()
        customers, _ = extract_partners(lines)
        assert customers.columns[1] == "debtor_number"

    def test_supplier_id_is_first_column(self):
        lines = canonical_lines()
        _, suppliers = extract_partners(lines)
        assert suppliers.columns[0] == "supplier_id"

    def test_creditor_number_is_second_column(self):
        lines = canonical_lines()
        _, suppliers = extract_partners(lines)
        assert suppliers.columns[1] == "creditor_number"
