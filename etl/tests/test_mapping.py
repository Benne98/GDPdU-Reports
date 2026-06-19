"""Tests for etl/mapping.py — MappingProfile -> canonical lines DataFrame.

All data is synthetic; no real client files are used (CLAUDE.md rule).

Coverage:
  - profile_from_dict / profile_to_dict roundtrip
  - apply_profile: key construction (jegn, ang, partner_id)
  - apply_profile: sign modes (signed, soll_haben, amount_dc)
  - apply_profile: fiscal_year from fixed / column / from_date
  - apply_profile: fiscal_period derived from posting_date month
  - apply_profile: optional columns (vat_amount, document_date, line_note)
  - apply_profile: account_class from lookup
  - apply_profile: partner split (customer_id / supplier_id)
  - apply_profile: missing mandatory column raises KeyError
  - apply_profile: unknown sign mode raises ValueError
  - apply_profile: currency_code defaults to 'EUR' when unmapped
"""
from __future__ import annotations

import pytest
import pandas as pd

from etl.mapping import MappingProfile, apply_profile, profile_from_dict, profile_to_dict


# --------------------------------------------------------------------------- #
# Synthetic GoBD-like raw DataFrame factory
# --------------------------------------------------------------------------- #
def _raw(
    jen: list[str] | None = None,
    acct: list[str] | None = None,
    amount: list[float] | None = None,
    posting_date: list[str] | None = None,
    source_type: list[str] | None = None,
    source_no: list[str] | None = None,
    vat_amount: list[float] | None = None,
    soll: list[float] | None = None,
    haben: list[float] | None = None,
    dc_flag: list[str] | None = None,
    year_col: list[int] | None = None,
    line_note: list[str] | None = None,
    currency: list[str] | None = None,
) -> pd.DataFrame:
    """Build a minimal raw DataFrame for testing apply_profile."""
    n = 3
    d: dict = {
        "Belegnummer": jen or ["1", "1", "2"],
        "Konto": acct or ["10000", "80000", "30000"],
        "Betrag": amount or [1190.0, -1000.0, 500.0],
        "Buchungsdatum": posting_date or ["01.01.2024", "01.01.2024", "15.03.2024"],
    }
    if source_type is not None:
        d["Art"] = source_type
    if source_no is not None:
        d["PartnerNr"] = source_no
    if vat_amount is not None:
        d["USt"] = vat_amount
    if soll is not None:
        d["Soll"] = soll
    if haben is not None:
        d["Haben"] = haben
    if dc_flag is not None:
        d["SH"] = dc_flag
    if year_col is not None:
        d["Jahr"] = year_col
    if line_note is not None:
        d["Buchungstext"] = line_note
    if currency is not None:
        d["Waehrung"] = currency
    return pd.DataFrame(d)


def _base_profile(**overrides) -> MappingProfile:
    """Return a sensible default profile for the synthetic raw frame."""
    p = MappingProfile()
    p.entity = {"mode": "fixed", "value": "01"}
    p.fiscal_year = {"mode": "from_date", "value": None}
    p.sign = {"mode": "signed", "amount": "Betrag"}
    p.decimal = "."
    p.thousands = None
    p.date_dayfirst = True
    p.columns = {
        "journal_entry_number": "Belegnummer",
        "account_number": "Konto",
        "posting_date": "Buchungsdatum",
    }
    p.linking_strategy = "none"
    p.entry_type = "actual"
    p.source_system = "test"
    for k, v in overrides.items():
        setattr(p, k, v)
    return p


# --------------------------------------------------------------------------- #
# Roundtrip serialization
# --------------------------------------------------------------------------- #
class TestProfileSerialization:
    def test_roundtrip(self):
        p = _base_profile()
        d = profile_to_dict(p)
        p2 = profile_from_dict(d)
        assert profile_to_dict(p2) == d

    def test_unknown_keys_ignored(self):
        d = profile_to_dict(_base_profile())
        d["future_field"] = "ignored"
        p = profile_from_dict(d)
        assert p.source_system == "test"  # known field preserved

    def test_dict_contains_all_expected_keys(self):
        d = profile_to_dict(_base_profile())
        for key in ("entity", "fiscal_year", "sign", "decimal", "thousands",
                    "date_dayfirst", "columns", "linking_strategy", "entry_type",
                    "source_system"):
            assert key in d


# --------------------------------------------------------------------------- #
# Key construction
# --------------------------------------------------------------------------- #
class TestKeyConstruction:
    def test_journal_entry_group_number_format(self):
        raw = _raw()
        out = apply_profile(raw, _base_profile())
        # 2-digit prefix + 10-digit jen = 12 chars
        assert (out["journal_entry_group_number"].str.len() == 12).all()
        assert out["journal_entry_group_number"].iloc[0].startswith("01")

    def test_account_number_group_format(self):
        raw = _raw()
        out = apply_profile(raw, _base_profile())
        # 2-digit prefix + 6-digit account = 8 chars
        assert (out["account_number_group"].str.len() == 8).all()
        assert out["account_number_group"].iloc[0] == "01010000"

    def test_gl_account_id_is_normalized(self):
        raw = _raw(acct=["10000", "80000.0", " 30000 "])
        out = apply_profile(raw, _base_profile())
        assert out["gl_account_id"].tolist() == ["10000", "80000", "30000"]

    def test_entity_from_column(self):
        raw = _raw()
        raw["EntityCode"] = ["1", "1", "2"]
        p = _base_profile()
        p.entity = {"mode": "column", "value": "EntityCode"}
        out = apply_profile(raw, p)
        assert out["journal_entry_group_number"].iloc[0].startswith("01")
        assert out["journal_entry_group_number"].iloc[2].startswith("02")


# --------------------------------------------------------------------------- #
# Fiscal year
# --------------------------------------------------------------------------- #
class TestFiscalYear:
    def test_fiscal_year_from_date(self):
        raw = _raw(posting_date=["01.01.2023", "15.06.2023", "31.12.2024"])
        out = apply_profile(raw, _base_profile())
        assert list(out["fiscal_year"]) == [2023, 2023, 2024]

    def test_fiscal_year_fixed(self):
        raw = _raw()
        p = _base_profile()
        p.fiscal_year = {"mode": "fixed", "value": 2025}
        out = apply_profile(raw, p)
        assert (out["fiscal_year"] == 2025).all()

    def test_fiscal_year_from_column(self):
        raw = _raw(year_col=[2022, 2022, 2023])
        p = _base_profile()
        p.fiscal_year = {"mode": "column", "value": "Jahr"}
        out = apply_profile(raw, p)
        assert list(out["fiscal_year"]) == [2022, 2022, 2023]


# --------------------------------------------------------------------------- #
# Fiscal period (from posting_date month)
# --------------------------------------------------------------------------- #
class TestFiscalPeriod:
    def test_fiscal_period_derived_from_month(self):
        raw = _raw(posting_date=["05.01.2024", "20.06.2024", "31.12.2024"])
        out = apply_profile(raw, _base_profile())
        assert list(out["fiscal_period"]) == [1, 6, 12]


# --------------------------------------------------------------------------- #
# Sign modes
# --------------------------------------------------------------------------- #
class TestSignModes:
    def test_signed_mode(self):
        raw = _raw(amount=[100.0, -200.0, 50.0])
        out = apply_profile(raw, _base_profile())
        assert list(out["amount"]) == [100.0, -200.0, 50.0]

    def test_soll_haben_mode(self):
        raw = _raw(soll=[1190.0, 0.0, 500.0], haben=[0.0, 1000.0, 0.0])
        del raw["Betrag"]
        p = _base_profile()
        p.sign = {"mode": "soll_haben", "soll": "Soll", "haben": "Haben"}
        out = apply_profile(raw, p)
        # Soll - Haben
        assert list(out["amount"]) == [1190.0, -1000.0, 500.0]

    def test_amount_dc_mode_debit(self):
        raw = _raw(amount=[100.0, 200.0, 50.0], dc_flag=["S", "H", "S"])
        p = _base_profile()
        p.sign = {"mode": "amount_dc", "amount": "Betrag", "dc_flag": "SH", "debit_value": "S"}
        out = apply_profile(raw, p)
        assert list(out["amount"]) == [100.0, -200.0, 50.0]

    def test_decimal_thousands_locale(self):
        """German locale: decimal=',', thousands='.'"""
        raw = _raw()
        raw["Betrag"] = ["1.190,00", "1.000,00", "500,00"]
        p = _base_profile()
        p.decimal = ","
        p.thousands = "."
        p.sign = {"mode": "signed", "amount": "Betrag"}
        out = apply_profile(raw, p)
        assert abs(out["amount"].iloc[0] - 1190.0) < 0.01
        assert abs(out["amount"].iloc[1] - 1000.0) < 0.01


# --------------------------------------------------------------------------- #
# Optional columns
# --------------------------------------------------------------------------- #
class TestOptionalColumns:
    def test_vat_amount_mapped(self):
        raw = _raw(vat_amount=[190.0, 0.0, 95.0])
        p = _base_profile()
        p.columns["vat_amount"] = "USt"
        out = apply_profile(raw, p)
        assert list(out["vat_amount"]) == [190.0, 0.0, 95.0]

    def test_vat_amount_absent_is_null(self):
        raw = _raw()
        out = apply_profile(raw, _base_profile())
        assert out["vat_amount"].isna().all()

    def test_line_note_mapped(self):
        raw = _raw(line_note=["Verkauf", "Erloes", "Einkauf"])
        p = _base_profile()
        p.columns["line_note"] = "Buchungstext"
        out = apply_profile(raw, p)
        assert out["line_note"].tolist() == ["Verkauf", "Erloes", "Einkauf"]

    def test_currency_defaults_to_eur_when_absent(self):
        raw = _raw()
        out = apply_profile(raw, _base_profile())
        assert (out["currency_code"] == "EUR").all()

    def test_currency_from_column(self):
        raw = _raw(currency=["EUR", "USD", "EUR"])
        p = _base_profile()
        p.columns["currency"] = "Waehrung"
        out = apply_profile(raw, p)
        assert list(out["currency_code"]) == ["EUR", "USD", "EUR"]

    def test_entry_type_default(self):
        out = apply_profile(_raw(), _base_profile())
        assert (out["entry_type"] == "actual").all()

    def test_source_system_propagated(self):
        p = _base_profile()
        p.source_system = "datev_test"
        out = apply_profile(_raw(), p)
        assert (out["source_system"] == "datev_test").all()


# --------------------------------------------------------------------------- #
# Partner split
# --------------------------------------------------------------------------- #
class TestPartnerSplit:
    def test_customer_and_supplier_from_source_type_no(self):
        raw = _raw(
            source_type=["Debitor", "None", "Kreditor"],
            source_no=["100", None, "200"],
        )
        p = _base_profile()
        p.columns["source_type"] = "Art"
        p.columns["source_no"] = "PartnerNr"
        out = apply_profile(raw, p)
        assert out["customer_id"].iloc[0] == "01100"
        assert pd.isna(out["customer_id"].iloc[1])
        assert out["supplier_id"].iloc[2] == "01200"

    def test_no_source_columns_yields_null_partners(self):
        out = apply_profile(_raw(), _base_profile())
        assert out["customer_id"].isna().all()
        assert out["supplier_id"].isna().all()


# --------------------------------------------------------------------------- #
# account_class lookup
# --------------------------------------------------------------------------- #
class TestAccountClassLookup:
    def test_account_class_resolved_from_lookup(self):
        lookup = {"10000": "receivable", "80000": "revenue", "30000": "material"}
        out = apply_profile(_raw(), _base_profile(), account_class_lookup=lookup)
        assert list(out["account_class"]) == ["receivable", "revenue", "material"]

    def test_unknown_account_defaults_to_other(self):
        lookup = {"80000": "revenue"}
        out = apply_profile(_raw(), _base_profile(), account_class_lookup=lookup)
        assert out["account_class"].iloc[0] == "other"
        assert out["account_class"].iloc[1] == "revenue"

    def test_no_lookup_all_other(self):
        out = apply_profile(_raw(), _base_profile())
        assert (out["account_class"] == "other").all()


# --------------------------------------------------------------------------- #
# Line number assignment
# --------------------------------------------------------------------------- #
class TestLineNumbers:
    def test_line_numbers_within_booking(self):
        """Lines within the same booking (same jen) should get sequential line numbers."""
        raw = _raw(
            jen=["1", "1", "1"],
            acct=["10000", "80000", "17760"],
            amount=[1190.0, -1000.0, -190.0],
        )
        out = apply_profile(raw, _base_profile())
        assert list(out["line_number"]) == [1, 2, 3]

    def test_line_numbers_reset_per_booking(self):
        raw = _raw(
            jen=["1", "1", "2"],
            acct=["10000", "80000", "30000"],
            amount=[1190.0, -1190.0, 500.0],
        )
        out = apply_profile(raw, _base_profile())
        # booking 1 gets line 1, 2; booking 2 gets line 1
        assert list(out["line_number"]) == [1, 2, 1]

    def test_booking_line_id_global_unique(self):
        out = apply_profile(_raw(), _base_profile())
        assert out["booking_line_id"].nunique() == len(out)


# --------------------------------------------------------------------------- #
# Error handling
# --------------------------------------------------------------------------- #
class TestErrors:
    def test_missing_posting_date_column_raises(self):
        raw = _raw()
        p = _base_profile()
        del p.columns["posting_date"]  # not in columns at all
        with pytest.raises(KeyError, match="posting_date"):
            apply_profile(raw, p)

    def test_missing_journal_entry_number_raises(self):
        raw = _raw()
        p = _base_profile()
        del p.columns["journal_entry_number"]
        with pytest.raises(KeyError, match="journal_entry_number"):
            apply_profile(raw, p)

    def test_missing_account_number_raises(self):
        raw = _raw()
        p = _base_profile()
        del p.columns["account_number"]
        with pytest.raises(KeyError, match="account_number"):
            apply_profile(raw, p)

    def test_unknown_sign_mode_raises(self):
        raw = _raw()
        p = _base_profile()
        p.sign = {"mode": "invalid_mode", "amount": "Betrag"}
        with pytest.raises(ValueError, match="unknown sign mode"):
            apply_profile(raw, p)

    def test_unknown_entity_mode_raises(self):
        raw = _raw()
        p = _base_profile()
        p.entity = {"mode": "unknown_mode", "value": "01"}
        with pytest.raises(ValueError, match="unknown entity mode"):
            apply_profile(raw, p)

    def test_missing_entity_column_raises(self):
        raw = _raw()
        p = _base_profile()
        p.entity = {"mode": "column", "value": "NonExistentCol"}
        with pytest.raises(KeyError, match="entity column"):
            apply_profile(raw, p)
