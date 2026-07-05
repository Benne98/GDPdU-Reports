"""Tests for etl/mapping_account.py — AccountMappingProfile -> canonical mapping DataFrame.

All data is synthetic; no real client files are used (CLAUDE.md rule).
These tests are PURE (no DB) and run without a live database.

Coverage:
  - account_profile_from_dict / account_profile_to_dict roundtrip
  - apply_account_mapping: key build (account_number_group 8 chars, correct prefix+pad)
  - apply_account_mapping: entity from fixed / column
  - apply_account_mapping: fiscal_year from fixed / column / override parameter
  - apply_account_mapping: gl_account_id normalization (.0 strip, whitespace)
  - apply_account_mapping: is_ic coercion (bool, various truthy/falsy inputs)
  - apply_account_mapping: level_2_sort / level_3_sort as nullable Int64
  - apply_account_mapping: missing required field raises KeyError
  - apply_account_mapping: missing required source column raises KeyError
  - apply_account_mapping: optional NA/CF fields yield null when unmapped
  - apply_account_mapping: NA/CF fields present when mapped
  - apply_account_mapping: source_system propagated
  - apply_account_mapping: entity_prefix / fiscal_year override parameters
  - fixtures: raw_account_mapping + canonical_account_mapping roundtrip consistency
  - fixtures: canonical_account_mapping accounts match canonical GL fixture accounts
"""
from __future__ import annotations

import pandas as pd
import pytest

from etl.mapping_account import (
    AccountMappingProfile,
    REQUIRED_FIELDS,
    NA_FIELDS,
    CF_FIELDS,
    apply_account_mapping,
    account_profile_from_dict,
    account_profile_to_dict,
)
from etl.tests.fixtures import (
    ACCOUNT_MAPPING_COLUMN_MAP,
    canonical_account_mapping,
    mapping_accounts,
    raw_account_mapping,
)


# --------------------------------------------------------------------------- #
# Synthetic raw-frame factory (independent of the fixtures module)
# --------------------------------------------------------------------------- #

def _raw_minimal(
    accounts: list[str] | None = None,
    is_ic: list | None = None,
    sort2: list | None = None,
    sort3: list | None = None,
    na6: list | None = None,
    na7: list | None = None,
    cf_mapping: list | None = None,
    entity_col: list[str] | None = None,
    year_col: list[int] | None = None,
) -> pd.DataFrame:
    """Build a minimal raw account-mapping DataFrame for unit tests."""
    n = 3
    accts = accounts or ["10000", "80000", "30000"]
    d: dict = {
        "Konto":   accts,
        "Ebene0":  ["BS", "PL", "PL"],
        "Ebene1":  ["Umlaufvermögen", "Erträge", "Aufwendungen"],
        "Ebene2":  ["Forderungen", "Umsatzerlöse", "Materialaufwand"],
        "Ebene3":  ["Trade receivables", "Net sales", "Cost of materials"],
        "Ebene4":  ["Debitorisch", "Inland", "Rohstoffe"],
        "Ebene4U": ["AllDebtors", "DE", "Raw"],
    }
    if is_ic is not None:
        d["IC"] = is_ic
    if sort2 is not None:
        d["Sort2"] = sort2
    if sort3 is not None:
        d["Sort3"] = sort3
    if na6 is not None:
        d["NA6"] = na6
    if na7 is not None:
        d["NA7"] = na7
    if cf_mapping is not None:
        d["CFM"] = cf_mapping
    if entity_col is not None:
        d["EntityCode"] = entity_col
    if year_col is not None:
        d["Jahr"] = year_col
    return pd.DataFrame(d)


def _base_profile(**overrides) -> AccountMappingProfile:
    """Return a sensible default AccountMappingProfile for the minimal raw frame."""
    p = AccountMappingProfile()
    p.entity = {"mode": "fixed", "value": "01"}
    p.fiscal_year = {"mode": "fixed", "value": 2024}
    p.columns = {
        "account_number": "Konto",
        "level_0":        "Ebene0",
        "level_1":        "Ebene1",
        "level_2":        "Ebene2",
        "level_3":        "Ebene3",
        "level_4":        "Ebene4",
        "l4_sub":         "Ebene4U",
    }
    p.source_system = "test"
    for k, v in overrides.items():
        setattr(p, k, v)
    return p


# --------------------------------------------------------------------------- #
# Serialisation roundtrip
# --------------------------------------------------------------------------- #

class TestProfileSerialization:
    def test_roundtrip(self):
        p = _base_profile()
        d = account_profile_to_dict(p)
        p2 = account_profile_from_dict(d)
        assert account_profile_to_dict(p2) == d

    def test_unknown_keys_ignored(self):
        d = account_profile_to_dict(_base_profile())
        d["future_field"] = "ignored"
        p = account_profile_from_dict(d)
        assert p.source_system == "test"

    def test_dict_contains_all_expected_keys(self):
        d = account_profile_to_dict(_base_profile())
        for key in ("entity", "fiscal_year", "columns", "source_system"):
            assert key in d


# --------------------------------------------------------------------------- #
# Key construction
# --------------------------------------------------------------------------- #

class TestKeyConstruction:
    def test_account_number_group_length_is_8(self):
        raw = _raw_minimal()
        out = apply_account_mapping(raw, _base_profile())
        assert (out["account_number_group"].str.len() == 8).all()

    def test_account_number_group_starts_with_prefix(self):
        raw = _raw_minimal()
        out = apply_account_mapping(raw, _base_profile())
        assert out["account_number_group"].iloc[0].startswith("01")

    def test_account_number_group_correct_zero_pad(self):
        """10000 -> 010000 (6 digits) -> '01010000' (prefix '01' + '010000')."""
        raw = _raw_minimal(accounts=["10000", "80000", "30000"])
        out = apply_account_mapping(raw, _base_profile())
        assert out["account_number_group"].iloc[0] == "01010000"
        assert out["account_number_group"].iloc[1] == "01080000"
        assert out["account_number_group"].iloc[2] == "01030000"

    def test_account_number_group_strip_dot_zero(self):
        """Excel float artifact '10000.0' must resolve to '01010000'."""
        raw = _raw_minimal(accounts=["10000.0", "80000.0", "30000.0"])
        out = apply_account_mapping(raw, _base_profile())
        assert out["account_number_group"].iloc[0] == "01010000"

    def test_gl_account_id_normalized(self):
        """gl_account_id should strip '.0' and whitespace."""
        raw = _raw_minimal(accounts=[" 10000.0 ", "80000", "30000"])
        out = apply_account_mapping(raw, _base_profile())
        assert out["gl_account_id"].iloc[0] == "10000"

    def test_gl_account_id_from_explicit_column(self):
        """When gl_account_id is explicitly mapped it overrides the account_number."""
        raw = _raw_minimal()
        raw["GlId"] = ["GL10000", "GL80000", "GL30000"]
        p = _base_profile()
        p.columns["gl_account_id"] = "GlId"
        out = apply_account_mapping(raw, p)
        assert out["gl_account_id"].tolist() == ["GL10000", "GL80000", "GL30000"]

    def test_entity_from_column(self):
        raw = _raw_minimal(entity_col=["1", "1", "2"])
        p = _base_profile()
        p.entity = {"mode": "column", "value": "EntityCode"}
        out = apply_account_mapping(raw, p)
        assert out["account_number_group"].iloc[0].startswith("01")
        assert out["account_number_group"].iloc[2].startswith("02")

    def test_entity_prefix_override_parameter(self):
        """entity_prefix kwarg overrides profile.entity."""
        raw = _raw_minimal()
        out = apply_account_mapping(raw, _base_profile(), entity_prefix="03")
        assert out["account_number_group"].iloc[0].startswith("03")

    def test_fiscal_year_fixed(self):
        raw = _raw_minimal()
        out = apply_account_mapping(raw, _base_profile())
        assert (out["fiscal_year"] == 2024).all()

    def test_fiscal_year_from_column(self):
        raw = _raw_minimal(year_col=[2023, 2023, 2024])
        p = _base_profile()
        p.fiscal_year = {"mode": "column", "value": "Jahr"}
        out = apply_account_mapping(raw, p)
        assert list(out["fiscal_year"]) == [2023, 2023, 2024]

    def test_fiscal_year_override_parameter(self):
        """fiscal_year kwarg overrides profile.fiscal_year."""
        raw = _raw_minimal()
        out = apply_account_mapping(raw, _base_profile(), fiscal_year=2025)
        assert (out["fiscal_year"] == 2025).all()


# --------------------------------------------------------------------------- #
# is_ic coercion
# --------------------------------------------------------------------------- #

class TestIsIcCoercion:
    def test_false_when_unmapped(self):
        """No is_ic column in profile -> all False."""
        raw = _raw_minimal()
        out = apply_account_mapping(raw, _base_profile())
        assert out["is_ic"].tolist() == [False, False, False]

    def test_bool_true_values(self):
        raw = _raw_minimal(is_ic=[True, False, True])
        p = _base_profile()
        p.columns["is_ic"] = "IC"
        out = apply_account_mapping(raw, p)
        assert out["is_ic"].tolist() == [True, False, True]

    def test_string_truthy_values(self):
        """'true', '1', 'yes', 'x', 'ja', 'wahr' should map to True."""
        for truthy in ("true", "1", "yes", "x", "ja", "wahr", "True", "YES"):
            raw = _raw_minimal(is_ic=[truthy, truthy, truthy])
            p = _base_profile()
            p.columns["is_ic"] = "IC"
            out = apply_account_mapping(raw, p)
            assert out["is_ic"].tolist() == [True, True, True], f"failed for {truthy!r}"

    def test_string_falsy_values(self):
        """'false', '0', 'no', empty, None should map to False."""
        for falsy in ("false", "0", "no", "", "False", "NO"):
            raw = _raw_minimal(is_ic=[falsy, falsy, falsy])
            p = _base_profile()
            p.columns["is_ic"] = "IC"
            out = apply_account_mapping(raw, p)
            assert out["is_ic"].tolist() == [False, False, False], f"failed for {falsy!r}"

    def test_numeric_zero_and_one(self):
        raw = _raw_minimal(is_ic=[1, 0, 1])
        p = _base_profile()
        p.columns["is_ic"] = "IC"
        out = apply_account_mapping(raw, p)
        assert out["is_ic"].tolist() == [True, False, True]


# --------------------------------------------------------------------------- #
# Sort columns (nullable Int64)
# --------------------------------------------------------------------------- #

class TestSortColumns:
    def test_sorts_are_null_when_unmapped(self):
        raw = _raw_minimal()
        out = apply_account_mapping(raw, _base_profile())
        assert out["level_2_sort"].isna().all()
        assert out["level_3_sort"].isna().all()

    def test_sorts_as_int_when_mapped(self):
        raw = _raw_minimal(sort2=[10, 20, 30], sort3=[100, 200, 300])
        p = _base_profile()
        p.columns["level_2_sort"] = "Sort2"
        p.columns["level_3_sort"] = "Sort3"
        out = apply_account_mapping(raw, p)
        assert list(out["level_2_sort"]) == [10, 20, 30]
        assert list(out["level_3_sort"]) == [100, 200, 300]
        # Must be Int64 (nullable integer)
        assert out["level_2_sort"].dtype.name == "Int64"
        assert out["level_3_sort"].dtype.name == "Int64"

    def test_sorts_strip_dot_zero(self):
        """Sort values arriving as float strings ('10.0') normalise to int."""
        raw = _raw_minimal(sort2=["10.0", "20.0", "30.0"])
        p = _base_profile()
        p.columns["level_2_sort"] = "Sort2"
        out = apply_account_mapping(raw, p)
        assert list(out["level_2_sort"]) == [10, 20, 30]

    def test_non_numeric_sort_becomes_null(self):
        raw = _raw_minimal(sort2=["n/a", "", "30"])
        p = _base_profile()
        p.columns["level_2_sort"] = "Sort2"
        out = apply_account_mapping(raw, p)
        assert pd.isna(out["level_2_sort"].iloc[0])
        assert pd.isna(out["level_2_sort"].iloc[1])
        assert out["level_2_sort"].iloc[2] == 30


# --------------------------------------------------------------------------- #
# Optional NA / CF fields
# --------------------------------------------------------------------------- #

class TestOptionalNaCfFields:
    def test_na_fields_null_when_unmapped(self):
        raw = _raw_minimal()
        out = apply_account_mapping(raw, _base_profile())
        for f in NA_FIELDS:
            assert out[f].isna().all(), f"{f} should be null when unmapped"

    def test_cf_fields_null_when_unmapped(self):
        raw = _raw_minimal()
        out = apply_account_mapping(raw, _base_profile())
        for f in CF_FIELDS:
            assert out[f].isna().all(), f"{f} should be null when unmapped"

    def test_na6_mapped(self):
        raw = _raw_minimal(na6=["AR", "Revenue", "COGS"])
        p = _base_profile()
        p.columns["l6_na_mapping"] = "NA6"
        out = apply_account_mapping(raw, p)
        assert out["l6_na_mapping"].tolist() == ["AR", "Revenue", "COGS"]

    def test_cf_mapping_mapped(self):
        raw = _raw_minimal(cf_mapping=["AR", None, "COGS"])
        p = _base_profile()
        p.columns["cf_mapping"] = "CFM"
        out = apply_account_mapping(raw, p)
        assert out["cf_mapping"].iloc[0] == "AR"
        assert pd.isna(out["cf_mapping"].iloc[1])
        assert out["cf_mapping"].iloc[2] == "COGS"


# --------------------------------------------------------------------------- #
# source_system
# --------------------------------------------------------------------------- #

class TestSourceSystem:
    def test_source_system_propagated(self):
        raw = _raw_minimal()
        p = _base_profile()
        p.source_system = "datev_mapping"
        out = apply_account_mapping(raw, p)
        assert (out["source_system"] == "datev_mapping").all()


# --------------------------------------------------------------------------- #
# Required-field validation
# --------------------------------------------------------------------------- #

class TestRequiredFieldValidation:
    @pytest.mark.parametrize("missing_field", list(REQUIRED_FIELDS))
    def test_missing_required_column_mapping_raises(self, missing_field):
        """If a required field is not in profile.columns, KeyError is raised."""
        p = _base_profile()
        del p.columns[missing_field]
        with pytest.raises(KeyError, match=missing_field):
            apply_account_mapping(_raw_minimal(), p)

    def test_missing_source_column_raises(self):
        """If profile.columns['level_0'] points to a non-existent source column."""
        raw = _raw_minimal()
        p = _base_profile()
        p.columns["level_0"] = "NonExistentCol"
        with pytest.raises(KeyError, match="NonExistentCol"):
            apply_account_mapping(raw, p)

    def test_unknown_entity_mode_raises(self):
        raw = _raw_minimal()
        p = _base_profile()
        p.entity = {"mode": "bad_mode", "value": "01"}
        with pytest.raises(ValueError, match="unknown entity mode"):
            apply_account_mapping(raw, p)

    def test_missing_entity_column_raises(self):
        raw = _raw_minimal()
        p = _base_profile()
        p.entity = {"mode": "column", "value": "NoSuchCol"}
        with pytest.raises(KeyError, match="NoSuchCol"):
            apply_account_mapping(raw, p)

    def test_unknown_fiscal_year_mode_raises(self):
        raw = _raw_minimal()
        p = _base_profile()
        p.fiscal_year = {"mode": "bad_mode", "value": 2024}
        with pytest.raises(ValueError, match="unknown fiscal_year mode"):
            apply_account_mapping(raw, p)

    def test_missing_fiscal_year_column_raises(self):
        raw = _raw_minimal()
        p = _base_profile()
        p.fiscal_year = {"mode": "column", "value": "NoSuchYearCol"}
        with pytest.raises(KeyError, match="NoSuchYearCol"):
            apply_account_mapping(raw, p)


# --------------------------------------------------------------------------- #
# Output schema
# --------------------------------------------------------------------------- #

class TestOutputSchema:
    def test_all_canonical_columns_present(self):
        """The output must contain every column that load_account_mapping will read."""
        expected = {
            "account_number_group", "fiscal_year", "gl_account_id", "account_name",
            "level_0", "level_1", "level_2", "level_3", "level_4", "l4_sub",
            "level_2_sort", "level_3_sort", "is_ic",
            "l6_na_mapping", "l7_na_description",
            "cf_l1", "cf_l2", "cf_l3", "cf_l4", "cf_l5", "cf_mapping",
            "source_system",
        }
        out = apply_account_mapping(_raw_minimal(), _base_profile())
        assert expected.issubset(set(out.columns))

    def test_row_count_preserved(self):
        raw = _raw_minimal()
        out = apply_account_mapping(raw, _base_profile())
        assert len(out) == len(raw)

    def test_empty_dataframe_produces_empty_output(self):
        raw = _raw_minimal()
        empty = raw.iloc[:0].copy()
        out = apply_account_mapping(empty, _base_profile())
        assert len(out) == 0
        assert "account_number_group" in out.columns


# --------------------------------------------------------------------------- #
# Fixture consistency
# --------------------------------------------------------------------------- #

class TestFixtureConsistency:
    def test_raw_account_mapping_has_all_source_columns(self):
        """All source column names referenced in ACCOUNT_MAPPING_COLUMN_MAP must exist
        in raw_account_mapping()."""
        raw = raw_account_mapping()
        for target, src in ACCOUNT_MAPPING_COLUMN_MAP.items():
            assert src in raw.columns, f"source col {src!r} (for {target!r}) missing"

    def test_canonical_account_mapping_covers_all_gl_accounts(self):
        """Every account in the GL fixture must appear in the mapping fixture."""
        gl_accounts = set(mapping_accounts())  # e.g. {'10000', '15760', ...}
        mapping_df = canonical_account_mapping()
        mapping_gl_ids = set(mapping_df["gl_account_id"].dropna().tolist())
        assert gl_accounts.issubset(mapping_gl_ids), (
            f"GL accounts not covered by mapping: {gl_accounts - mapping_gl_ids}"
        )

    def test_canonical_mapping_account_number_group_format(self):
        mapping_df = canonical_account_mapping()
        assert (mapping_df["account_number_group"].str.len() == 8).all()
        assert mapping_df["account_number_group"].iloc[0].startswith("01")

    def test_canonical_mapping_is_ic_type(self):
        mapping_df = canonical_account_mapping()
        assert mapping_df["is_ic"].dtype == bool

    def test_canonical_mapping_na_fields_present_for_some_accounts(self):
        """At least some accounts in the fixture have NA fields populated."""
        mapping_df = canonical_account_mapping()
        has_na = mapping_df["l6_na_mapping"].notna()
        assert has_na.any(), "expected at least one account with l6_na_mapping set"

    def test_canonical_mapping_cf_fields_present_for_some_accounts(self):
        mapping_df = canonical_account_mapping()
        has_cf = mapping_df["cf_mapping"].notna()
        assert has_cf.any(), "expected at least one account with cf_mapping set"

    def test_canonical_mapping_null_na_for_vat_accounts(self):
        """VAT accounts (17760, 15760) have no NA mapping in the synthetic fixture."""
        mapping_df = canonical_account_mapping()
        vat_rows = mapping_df[mapping_df["gl_account_id"].isin({"17760", "15760"})]
        assert vat_rows["l6_na_mapping"].isna().all()

    def test_canonical_mapping_fiscal_year(self):
        mapping_df = canonical_account_mapping(fiscal_year=2023)
        assert (mapping_df["fiscal_year"] == 2023).all()


# --------------------------------------------------------------------------- #
# Regression guard — single group CoA must fan out to every member entity
# --------------------------------------------------------------------------- #

class TestSingleCoaFansOutToAllMemberEntities:
    """Regression guard: a single group CoA (bare account numbers, no entity-prefix
    column) passed to apply_account_mapping MUST produce a DISTINCT dim_gl_account
    primary-key set for every member entity it is applied to.

    Bug context (2026-06): a single-file group CoA was committed only for entity '01'
    instead of for every member entity (01, 02, ...).  The frontend assignment grouping
    was the defect trigger and is fixed separately.  This test locks the BACKEND
    mapping math: the same raw CoA DataFrame, applied twice with different
    entity_prefix values, must produce fully disjoint account_number_group key sets.

    Formula  : account_number_group = entity_prefix(2) + zfill(account_number, 6)
    Example  : account 11701, prefix '01'  ->  '01' + '011701'  =  '01011701'  (8 chars)
               account 11701, prefix '02'  ->  '02' + '011701'  =  '02011701'  (8 chars, disjoint)
               account  4400, prefix '01'  ->  '01' + '004400'  =  '01004400'
               account  4400, prefix '02'  ->  '02' + '004400'  =  '02004400'

    A regression at the mapping layer would manifest as one of:
      - both prefixes returning identical account_number_group values (no fan-out)
      - the key sets overlapping (wrong prefix applied)
      - the source gl_account_id being lost or corrupted
    All three are asserted here.
    """

    # Two bare account numbers as they appear in a typical German group Kontenrahmen
    _ACCOUNTS = ["11701", "4400"]

    @staticmethod
    def _build_raw_coa() -> pd.DataFrame:
        """Single-source CoA DataFrame with bare account numbers and NO entity-prefix
        column — exactly how a group Kontenrahmen arrives for a multi-entity scope."""
        return pd.DataFrame({
            "Konto":  ["11701", "4400"],
            "Ebene0": ["BS", "PL"],
            "Ebene1": ["Umlaufvermögen", "Erträge"],
            "Ebene2": ["Forderungen", "Umsatzerlöse"],
            "Ebene3": ["Trade receivables", "Net sales"],
        })

    @staticmethod
    def _build_profile() -> AccountMappingProfile:
        """Profile for the bare-account CoA; entity is resolved via entity_prefix= kwarg
        at call time (the group-scope fan-out pattern)."""
        p = AccountMappingProfile()
        p.entity = {"mode": "fixed", "value": "01"}  # overridden per-entity at call time
        p.fiscal_year = {"mode": "fixed", "value": 2024}
        p.columns = {
            "account_number": "Konto",
            "level_0":        "Ebene0",
            "level_1":        "Ebene1",
            "level_2":        "Ebene2",
            "level_3":        "Ebene3",
        }
        p.source_system = "group_coa_regression_guard"
        return p

    def test_single_coa_fans_out_to_all_member_entities(self):
        """One raw CoA file applied with entity_prefix='01' and entity_prefix='02'
        must yield DISJOINT account_number_group key sets, each containing the
        correctly zero-padded, entity-prefixed keys for both source accounts.

        Regression guard: this math was correct before the 2026-06 bug; this test
        ensures it cannot silently regress at the mapping layer in the future.
        """
        raw = self._build_raw_coa()
        profile = self._build_profile()

        out_01 = apply_account_mapping(raw, profile, entity_prefix="01", fiscal_year=2024)
        out_02 = apply_account_mapping(raw, profile, entity_prefix="02", fiscal_year=2024)

        keys_01 = set(out_01["account_number_group"].tolist())
        keys_02 = set(out_02["account_number_group"].tolist())

        # 11701 -> zfill(6) -> 011701 -> '01' + '011701' = '01011701'
        # 4400  -> zfill(6) -> 004400 -> '01' + '004400' = '01004400'
        assert keys_01 == {"01011701", "01004400"}, (
            f"prefix '01' yielded unexpected account_number_group set: {keys_01}"
        )
        # Same source -> prefix '02': '02011701', '02004400'
        assert keys_02 == {"02011701", "02004400"}, (
            f"prefix '02' yielded unexpected account_number_group set: {keys_02}"
        )

        # THE CORE GUARD: the two prefixes must produce fully disjoint key sets
        # from the SAME source file.  Any overlap means the prefix was not applied.
        assert keys_01.isdisjoint(keys_02), (
            f"entity key collision detected — the same CoA produced shared keys "
            f"across entity '01' and '02': {keys_01 & keys_02}"
        )

        # Source gl_account_id must be preserved unchanged for both expansions
        assert set(out_01["gl_account_id"].tolist()) == {"11701", "4400"}, (
            "entity '01' output lost or corrupted gl_account_id values"
        )
        assert set(out_02["gl_account_id"].tolist()) == {"11701", "4400"}, (
            "entity '02' output lost or corrupted gl_account_id values"
        )

        # Row count must equal the source CoA (no duplication, no loss)
        assert len(out_01) == len(raw), "entity '01' output row count does not match source"
        assert len(out_02) == len(raw), "entity '02' output row count does not match source"

        # All account_number_group keys must be exactly 8 characters
        assert (out_01["account_number_group"].str.len() == 8).all(), (
            "entity '01': account_number_group contains non-8-char keys"
        )
        assert (out_02["account_number_group"].str.len() == 8).all(), (
            "entity '02': account_number_group contains non-8-char keys"
        )
