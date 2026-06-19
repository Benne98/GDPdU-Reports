"""Tests for etl/classify_config.py — DF2.

All fixtures are synthetic; no real client data (CLAUDE.md).

Covers:
  - DEFAULT_RULES classify the DF1 synthetic mapping correctly for all four classes.
  - 'other' class for accounts not in any label set.
  - Per-source_system OVERRIDES ('test' source_system uses level_3 for all classes).
  - Missing level column → empty frozenset (safe fallback, no KeyError).
  - classify_with_rules produces non-empty results and correct account_class values.
"""
from __future__ import annotations

import pandas as pd
import pytest

from etl.classify_config import (
    DEFAULT_RULES,
    OVERRIDES,
    ClassificationRules,
    ClassRule,
    build_account_classes,
)
from etl.derive import AccountClasses, classify, classify_with_rules
from etl.tests import fixtures as F


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _canonical_mapping_df(entity_prefix: str = "01", fiscal_year: int = 2024) -> pd.DataFrame:
    """Canonical account-mapping as returned by apply_account_mapping (from fixtures)."""
    return F.canonical_account_mapping(entity_prefix=entity_prefix, fiscal_year=fiscal_year)


# --------------------------------------------------------------------------- #
# 1. build_account_classes with DEFAULT_RULES
#    The synthetic mapping (fixtures._ACCOUNT_ROWS) uses English synonyms for
#    level_2 (e.g. "Umsatzerlöse" -> mapped to "Umsatzerlöse" in level_2; but
#    the fixture actually uses "Umsatzerlöse" in level_2 for account 80000 and
#    "Materialaufwand" for account 30000; level_1 "Umlaufvermögen" for AR and
#    "Verbindlichkeiten" for AP).  DEFAULT_RULES include English synonyms too.
# --------------------------------------------------------------------------- #

class TestBuildAccountClassesDefaultRules:
    def setup_method(self):
        self.dim_df = _canonical_mapping_df()
        self.classes = build_account_classes(self.dim_df, DEFAULT_RULES)

    def test_returns_account_classes_instance(self):
        assert isinstance(self.classes, AccountClasses)

    def test_revenue_non_empty(self):
        """level_2 'Umsatzerlöse' is in DEFAULT_RULES.revenue.labels."""
        assert len(self.classes.revenue) > 0, (
            f"revenue is empty; level_2 values in mapping: {self.dim_df['level_2'].unique()}"
        )

    def test_material_non_empty(self):
        """level_2 'Materialaufwand' is in DEFAULT_RULES.material.labels."""
        assert len(self.classes.material) > 0, (
            f"material is empty; level_2 values in mapping: {self.dim_df['level_2'].unique()}"
        )

    def test_receivable_non_empty(self):
        """level_3 'Trade receivables' is in DEFAULT_RULES.receivable.labels."""
        assert len(self.classes.receivable) > 0, (
            f"receivable is empty; level_3 values in mapping: {self.dim_df['level_3'].unique()}"
        )

    def test_payable_non_empty(self):
        """level_3 'Trade payables' is in DEFAULT_RULES.payable.labels."""
        assert len(self.classes.payable) > 0, (
            f"payable is empty; level_3 values in mapping: {self.dim_df['level_3'].unique()}"
        )

    def test_only_labels_present_in_mapping_are_included(self):
        """AccountClasses must only contain labels that actually appear in the mapping."""
        level_2_values = set(self.dim_df["level_2"].dropna().str.strip().unique())
        level_3_values = set(self.dim_df["level_3"].dropna().str.strip().unique())
        for label in self.classes.revenue:
            assert label in level_2_values
        for label in self.classes.material:
            assert label in level_2_values
        for label in self.classes.receivable:
            assert label in level_3_values
        for label in self.classes.payable:
            assert label in level_3_values

    def test_vat_accounts_not_classified_as_revenue_or_material(self):
        """VAT accounts (17760, 15760) have level_2 'Verbindlichkeiten' / 'Forderungen'
        which are not in revenue or material label sets."""
        vat_rows = self.dim_df[self.dim_df["gl_account_id"].isin(["17760", "15760"])]
        for _, row in vat_rows.iterrows():
            l2 = str(row.get("level_2", "")).strip()
            assert l2 not in self.classes.revenue, f"VAT account {row['gl_account_id']} landed in revenue"
            assert l2 not in self.classes.material, f"VAT account {row['gl_account_id']} landed in material"

    def test_vat_accounts_not_classified_as_receivable_or_payable(self):
        """VAT accounts have level_3 'VAT' which is not in receivable or payable label sets."""
        vat_rows = self.dim_df[self.dim_df["gl_account_id"].isin(["17760", "15760"])]
        for _, row in vat_rows.iterrows():
            l3 = str(row.get("level_3", "")).strip()
            assert l3 not in self.classes.receivable, f"VAT account {row['gl_account_id']} landed in receivable"
            assert l3 not in self.classes.payable, f"VAT account {row['gl_account_id']} landed in payable"


# --------------------------------------------------------------------------- #
# 2. 'test' source_system override — uses level_3 for all classes
# --------------------------------------------------------------------------- #

class TestBuildAccountClassesTestOverride:
    def setup_method(self):
        self.dim_df = _canonical_mapping_df()
        self.rules = OVERRIDES["test"]
        self.classes = build_account_classes(self.dim_df, self.rules)

    def test_revenue_matches_level_3_net_sales(self):
        assert "Net sales" in self.classes.revenue

    def test_material_matches_level_3_cost_of_materials(self):
        assert "Cost of materials" in self.classes.material

    def test_receivable_matches_level_3_trade_receivables(self):
        assert "Trade receivables" in self.classes.receivable

    def test_payable_matches_level_3_trade_payables(self):
        assert "Trade payables" in self.classes.payable

    def test_vat_level_3_not_classified(self):
        """level_3 'VAT' (for accounts 17760, 15760) is not in any class label set."""
        assert "VAT" not in self.classes.revenue
        assert "VAT" not in self.classes.material
        assert "VAT" not in self.classes.receivable
        assert "VAT" not in self.classes.payable


# --------------------------------------------------------------------------- #
# 3. Missing level column — safe fallback
# --------------------------------------------------------------------------- #

class TestMissingLevelColumn:
    def test_missing_level_column_yields_empty_frozenset(self):
        """If the configured level_column is absent from the DataFrame, the
        class gets an empty frozenset — no KeyError, no silent wrong result."""
        dim_df = pd.DataFrame({"account_number_group": ["0180000"], "fiscal_year": [2024]})
        # DEFAULT_RULES.revenue uses level_2; that column is absent here
        classes = build_account_classes(dim_df, DEFAULT_RULES)
        assert classes.revenue == frozenset()
        assert classes.material == frozenset()
        assert classes.receivable == frozenset()
        assert classes.payable == frozenset()


# --------------------------------------------------------------------------- #
# 4. Custom override (per-source_system override works end-to-end)
# --------------------------------------------------------------------------- #

class TestCustomOverride:
    def test_custom_rule_overrides_level_column(self):
        """A caller can create their own rules and override any class's level_column."""
        custom_rules = ClassificationRules(
            revenue=ClassRule(level_column="level_0", labels=frozenset({"PL"})),
            material=ClassRule(level_column="level_0", labels=frozenset({"PL"})),
            receivable=ClassRule(level_column="level_0", labels=frozenset({"BS"})),
            payable=ClassRule(level_column="level_0", labels=frozenset({"BS"})),
        )
        dim_df = _canonical_mapping_df()
        classes = build_account_classes(dim_df, custom_rules)
        # PL accounts (level_0='PL'): 80000 (revenue), 30000 (material)
        assert "PL" in classes.revenue
        assert "PL" in classes.material
        # BS accounts (level_0='BS'): 10000, 17760, 15760, 70000
        assert "BS" in classes.receivable
        assert "BS" in classes.payable


# --------------------------------------------------------------------------- #
# 5. classify_with_rules — single-function per-class multi-column classify
# --------------------------------------------------------------------------- #

class TestClassifyWithRules:
    def setup_method(self):
        self.lines = F.canonical_lines()
        # canonical_lines already has level_3 set per _L3 dict; add level_2/level_1
        # so we can test multi-column rules.
        mapping_df = _canonical_mapping_df()
        level_map = mapping_df.set_index("account_number_group")[
            ["level_1", "level_2", "level_3"]
        ].to_dict("index")
        for col in ["level_1", "level_2", "level_3"]:
            if col in self.lines.columns:
                self.lines = self.lines.drop(columns=[col])
        self.lines["level_1"] = self.lines["account_number_group"].map(
            lambda ang: level_map.get(ang, {}).get("level_1")
        )
        self.lines["level_2"] = self.lines["account_number_group"].map(
            lambda ang: level_map.get(ang, {}).get("level_2")
        )
        self.lines["level_3"] = self.lines["account_number_group"].map(
            lambda ang: level_map.get(ang, {}).get("level_3")
        )

    def test_default_rules_classifies_revenue_lines(self):
        result = classify_with_rules(self.lines, DEFAULT_RULES)
        # account_number_group = entity_prefix(2) + account.zfill(6): "01" + "080000" = "01080000"
        rev_mask = self.lines["account_number_group"] == "01080000"
        assert (result[rev_mask] == "revenue").all(), (
            f"Expected revenue for 01080000; got {result[rev_mask].tolist()}"
        )

    def test_default_rules_classifies_material_lines(self):
        result = classify_with_rules(self.lines, DEFAULT_RULES)
        mat_mask = self.lines["account_number_group"] == "01030000"
        assert (result[mat_mask] == "material").all(), (
            f"Expected material for 01030000; got {result[mat_mask].tolist()}"
        )

    def test_default_rules_classifies_receivable_lines(self):
        result = classify_with_rules(self.lines, DEFAULT_RULES)
        ar_mask = self.lines["account_number_group"] == "01010000"
        assert (result[ar_mask] == "receivable").all(), (
            f"Expected receivable for 01010000; got {result[ar_mask].tolist()}"
        )

    def test_default_rules_classifies_payable_lines(self):
        result = classify_with_rules(self.lines, DEFAULT_RULES)
        ap_mask = self.lines["account_number_group"] == "01070000"
        assert (result[ap_mask] == "payable").all(), (
            f"Expected payable for 01070000; got {result[ap_mask].tolist()}"
        )

    def test_vat_lines_are_other(self):
        result = classify_with_rules(self.lines, DEFAULT_RULES)
        vat_mask = self.lines["account_number_group"].isin(["01017760", "01015760"])
        assert (result[vat_mask] == "other").all(), (
            f"VAT accounts should be 'other'; got {result[vat_mask].tolist()}"
        )

    def test_test_override_classifies_correctly_via_level_3(self):
        result = classify_with_rules(self.lines, OVERRIDES["test"])
        rev_mask = self.lines["account_number_group"] == "01080000"
        mat_mask = self.lines["account_number_group"] == "01030000"
        ar_mask = self.lines["account_number_group"] == "01010000"
        ap_mask = self.lines["account_number_group"] == "01070000"
        assert (result[rev_mask] == "revenue").all()
        assert (result[mat_mask] == "material").all()
        assert (result[ar_mask] == "receivable").all()
        assert (result[ap_mask] == "payable").all()
