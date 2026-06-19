"""Tests for etl/versioning.py scope derivation (DB-free)."""
from __future__ import annotations

import pandas as pd

from etl.versioning import derive_gl_scope, derive_mapping_scope
from etl.tests.fixtures import canonical_lines


class TestDeriveGlScope:
    def test_from_canonical_fixture(self):
        df = canonical_lines()
        prefixes, years = derive_gl_scope(df)
        assert "01" in prefixes
        assert 2024 in years

    def test_empty_dataframe(self):
        df = pd.DataFrame()
        assert derive_gl_scope(df) == ([], [])

    def test_prefixes_from_jegn_and_account(self):
        df = pd.DataFrame(
            {
                "journal_entry_group_number": ["0300000001", "0400000002"],
                "account_number_group": ["03010000", "04020000"],
                "fiscal_year": [2023, 2024],
            }
        )
        prefixes, years = derive_gl_scope(df)
        assert prefixes == ["03", "04"]
        assert years == [2023, 2024]


class TestDeriveMappingScope:
    def test_from_mapping_rows(self):
        df = pd.DataFrame(
            {
                "account_number_group": ["01010000", "02020000", "01030000"],
                "fiscal_year": [2024, 2024, 2025],
            }
        )
        prefixes, years = derive_mapping_scope(df)
        assert prefixes == ["01", "02"]
        assert years == [2024, 2025]

    def test_empty_mapping(self):
        df = pd.DataFrame(columns=["account_number_group", "fiscal_year"])
        assert derive_mapping_scope(df) == ([], [])
