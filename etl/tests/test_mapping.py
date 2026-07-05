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

from etl.mapping import (
    MappingProfile,
    apply_profile,
    profile_from_dict,
    profile_to_dict,
    sniff_decimal_separator,
)
from etl import transform as T


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


def test_empty_sign_amount_raises_clear_message():
    """Regression: an unmapped amount column must raise a CLEAR ValueError, not a
    cryptic KeyError('') — which surfaced to the user in the wizard as just "''"."""
    raw = _raw()
    p = _base_profile(sign={"mode": "signed", "amount": ""})
    with pytest.raises(ValueError, match="'Amount' column is not mapped"):
        apply_profile(raw, p)


def test_empty_soll_haben_columns_raise_clear_message():
    raw = _raw(soll=[100.0, 0.0, 50.0], haben=[0.0, 100.0, 0.0])
    p = _base_profile(sign={"mode": "soll_haben", "soll": "", "haben": "Haben"})
    with pytest.raises(ValueError, match="'Debit .Soll.' column is not mapped"):
        apply_profile(raw, p)


# --------------------------------------------------------------------------- #
# sniff_decimal_separator (money-column oriented decimal detection)
# --------------------------------------------------------------------------- #
class TestSniffDecimalSeparator:
    def test_us_single_dot(self):
        # US amounts: single '.', non-3 trailing digits -> decimal '.'
        assert sniff_decimal_separator(["52803.84", "10.5", "0.99"]) == (".", ",")

    def test_us_float_artifact(self):
        # >3 trailing digits still votes decimal '.'
        assert sniff_decimal_separator(["52803.840000000004"]) == (".", ",")

    def test_german_single_comma(self):
        assert sniff_decimal_separator(["52803,84", "10,5"]) == (",", ".")

    def test_german_both_separators(self):
        # '52.803,84' / '1.234.567,89' -> ',' is rightmost -> decimal ','
        assert sniff_decimal_separator(["52.803,84", "1.234.567,89"]) == (",", ".")

    def test_us_both_separators(self):
        # '1,234,567.89' -> '.' is rightmost -> decimal '.'
        assert sniff_decimal_separator(["1,234,567.89"]) == (".", ",")

    def test_ambiguous_three_trailing_digits_abstains(self):
        # '1,234' could be thousands-grouped 1234 or the decimal 1.234 -> no vote.
        assert sniff_decimal_separator(["1,234"]) is None

    def test_thousands_grouping_only_no_vote(self):
        # Multiple '.' => grouping only, no decimal signal -> inconclusive.
        assert sniff_decimal_separator(["1.234.567"]) is None

    def test_empty_and_none_inconclusive(self):
        assert sniff_decimal_separator([]) is None
        assert sniff_decimal_separator([None, "", "   "]) is None

    def test_currency_and_sign_stripped(self):
        assert sniff_decimal_separator(["-€52803.84", " $10.5 "]) == (".", ",")

    def test_roundtrip_parse_decimal_us(self):
        dec, thou = sniff_decimal_separator(["52803.84"])
        out = T.parse_decimal(pd.Series(["52803.84"]), dec, thou)
        assert out.iloc[0] == pytest.approx(52803.84)

    def test_roundtrip_parse_decimal_german(self):
        dec, thou = sniff_decimal_separator(["52.803,84"])
        out = T.parse_decimal(pd.Series(["52.803,84"]), dec, thou)
        assert out.iloc[0] == pytest.approx(52803.84)


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

    def test_booking_line_id_distinct_across_separate_entity_files(self):
        """Two SEPARATE per-entity GL files with identical jen/account/line layout
        must NOT collide on booking_line_id (Phase-2 multi-file blocker)."""
        raw = _raw(jen=["1", "1", "2"], acct=["10000", "80000", "30000"])
        out_01 = apply_profile(raw, _base_profile(entity={"mode": "fixed", "value": "01"}))
        out_02 = apply_profile(raw, _base_profile(entity={"mode": "fixed", "value": "02"}))
        ids_01 = set(out_01["booking_line_id"].tolist())
        ids_02 = set(out_02["booking_line_id"].tolist())
        # No overlap → committing the second file cannot violate UNIQUE(booking_line_id).
        assert ids_01.isdisjoint(ids_02)

    def test_booking_line_id_idempotent_on_rerun(self):
        """Re-running the same file (commit_mode='replace') reproduces identical ids."""
        raw = _raw(jen=["1", "1", "2"], acct=["10000", "80000", "30000"])
        first = apply_profile(raw, _base_profile())
        second = apply_profile(raw, _base_profile())
        assert first["booking_line_id"].tolist() == second["booking_line_id"].tolist()

    def test_booking_line_id_positive_bigint(self):
        out = apply_profile(_raw(), _base_profile())
        # Must fit a positive signed bigint (never negative; within 2**62 headroom).
        assert (out["booking_line_id"] > 0).all()
        assert (out["booking_line_id"] < (1 << 62)).all()

    def test_booking_line_id_preserves_source_value(self):
        """A mapped, present source booking_line_id is PRESERVED (not regenerated)."""
        raw = _raw(jen=["1", "1", "2"], acct=["10000", "80000", "30000"])
        raw["SrcBlid"] = [111, 222, 333]
        p = _base_profile()
        p.columns["booking_line_id"] = "SrcBlid"
        out = apply_profile(raw, p)
        assert out["booking_line_id"].tolist() == [111, 222, 333]


# --------------------------------------------------------------------------- #
# Error handling
# --------------------------------------------------------------------------- #
class TestErrors:
    def test_missing_posting_date_column_raises(self):
        raw = _raw()
        p = _base_profile()
        del p.columns["posting_date"]  # not in columns at all
        with pytest.raises(KeyError, match="Posting date"):
            apply_profile(raw, p)

    def test_missing_journal_entry_number_raises(self):
        raw = _raw()
        p = _base_profile()
        del p.columns["journal_entry_number"]
        with pytest.raises(KeyError, match="Transaction/journal number"):
            apply_profile(raw, p)

    def test_missing_account_number_raises(self):
        raw = _raw()
        p = _base_profile()
        del p.columns["account_number"]
        with pytest.raises(KeyError, match="Account number"):
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


# --------------------------------------------------------------------------- #
# Opening-balance path: posting_date / journal_entry_number optional
# --------------------------------------------------------------------------- #
class TestOpeningBalancePath:
    """OB files carry only account number + amount (no posting date, no txn no.).
    apply_profile(..., opening_balance=True) must tolerate the missing date/txn
    columns; GL (opening_balance=False) stays strictly required."""

    def _ob_raw(self) -> pd.DataFrame:
        # ONLY account_number + amount columns — no posting_date, no txn number.
        return pd.DataFrame({"Konto": ["10000", "80000", "30000"],
                             "Betrag": [1190.0, -1000.0, 500.0]})

    def _ob_profile(self, **overrides) -> MappingProfile:
        p = MappingProfile()
        p.entity = {"mode": "fixed", "value": "01"}
        p.fiscal_year = {"mode": "fixed", "value": "2021"}
        p.sign = {"mode": "signed", "amount": "Betrag"}
        p.decimal = "."
        p.thousands = None
        p.date_dayfirst = True
        p.columns = {"account_number": "Konto"}  # no posting_date, no jen
        p.linking_strategy = "none"
        p.entry_type = "actual"
        p.source_system = "test"
        for k, v in overrides.items():
            setattr(p, k, v)
        return p

    def test_ob_fixed_fy_no_date_no_txn_succeeds(self):
        out = apply_profile(self._ob_raw(), self._ob_profile(), opening_balance=True)
        # posting_date is all-NaT here (Jan-1 synthesis happens later in the router).
        assert out["posting_date"].isna().all()
        assert (out["fiscal_year"] == 2021).all()
        assert out["gl_account_id"].tolist() == ["10000", "80000", "30000"]
        assert out["amount"].tolist() == [1190.0, -1000.0, 500.0]

    def test_gl_default_still_requires_posting_date(self):
        # SAME profile, opening_balance=False (GL default) → unchanged KeyError.
        with pytest.raises(KeyError, match="Posting date"):
            apply_profile(self._ob_raw(), self._ob_profile(), opening_balance=False)

    def test_ob_from_date_fy_raises_valueerror(self):
        p = self._ob_profile(fiscal_year={"mode": "from_date", "value": None})
        with pytest.raises(ValueError, match="fiscal year"):
            apply_profile(self._ob_raw(), p, opening_balance=True)


# --------------------------------------------------------------------------- #
# drop_unknown_entities — OB drop / GL regression guard
# --------------------------------------------------------------------------- #
class TestDropUnknownEntities:
    """Tests for apply_profile(..., drop_unknown_entities=True/False).

    Spec: when entity mode='column' and drop_unknown_entities=True, rows whose
    entity label is not in the lookup are silently dropped (the OB path — extra
    entities not in the project should not block the commit). The dropped labels
    are exposed on the returned DataFrame via
    out.attrs["ignored_entity_labels"] (sorted list of original label strings).

    When drop_unknown_entities=False (GL default), the same frame raises
    ValueError("Unknown entities ...") — this is the byte-identical GL
    regression guard that must never silently lose rows during GL ingestion.
    """

    # Known labels map to prefixes "01" / "02"; unknown labels have no entry.
    _LOOKUP: dict = {"Atlas": "01", "Calypto": "02"}

    def _make_raw(self) -> pd.DataFrame:
        """Four-row frame: two known entity labels + two unknown ones."""
        return pd.DataFrame({
            "Entity": ["Atlas", "Calypto", "Meridian", "Novara"],
            "Konto":  ["10000", "80000",  "30000",   "40000"],
            "Betrag": [500.0,   300.0,    999.0,     888.0],
        })

    def _make_profile(self) -> MappingProfile:
        """OB-style profile using entity column mode.

        No posting_date / journal_entry_number: those are optional on the OB
        path (opening_balance=True) and unnecessary for testing entity drop.
        Entity resolution is the FIRST step in apply_profile, so the ValueError
        from drop_unknown_entities=False fires before any posting_date check —
        the same profile works for both the drop=True and drop=False tests.
        """
        p = MappingProfile()
        p.entity = {"mode": "column", "value": "Entity"}
        p.fiscal_year = {"mode": "fixed", "value": 2022}
        p.sign = {"mode": "signed", "amount": "Betrag"}
        p.decimal = "."
        p.thousands = None
        p.date_dayfirst = True
        p.columns = {"account_number": "Konto"}
        p.linking_strategy = "none"
        p.entry_type = "actual"
        p.source_system = "test"
        return p

    def test_drop_unknown_true_keeps_known_rows_only(self):
        """Only the resolvable-entity rows survive; unknown rows are filtered out."""
        out = apply_profile(
            self._make_raw(),
            self._make_profile(),
            entity_lookup=self._LOOKUP,
            opening_balance=True,
            drop_unknown_entities=True,
        )
        assert len(out) == 2, f"expected 2 rows (Atlas+Calypto); got {len(out)}"
        assert out["gl_account_id"].tolist() == ["10000", "80000"]
        assert out["amount"].tolist() == [500.0, 300.0]

    def test_drop_unknown_true_reports_dropped_labels_sorted(self):
        """ignored_entity_labels attr carries the dropped labels in sorted order."""
        out = apply_profile(
            self._make_raw(),
            self._make_profile(),
            entity_lookup=self._LOOKUP,
            opening_balance=True,
            drop_unknown_entities=True,
        )
        ignored = out.attrs.get("ignored_entity_labels", [])
        assert ignored == ["Meridian", "Novara"], (
            f"expected ['Meridian', 'Novara']; got {ignored!r}"
        )

    def test_drop_unknown_false_raises_value_error(self):
        """drop_unknown_entities=False raises ValueError — GL regression guard.

        Entity resolution is the first step in apply_profile, so this raises
        before posting_date / journal_entry_number are checked (no need for
        those columns in the test frame).
        """
        with pytest.raises(ValueError, match="Unknown entities"):
            apply_profile(
                self._make_raw(),
                self._make_profile(),
                entity_lookup=self._LOOKUP,
                opening_balance=False,
                drop_unknown_entities=False,
            )

    def test_drop_unknown_true_all_known_no_rows_dropped(self):
        """When every entity label resolves, attrs is an empty list (no drops)."""
        raw = pd.DataFrame({
            "Entity": ["Atlas", "Calypto"],
            "Konto":  ["10000", "80000"],
            "Betrag": [500.0, -200.0],
        })
        out = apply_profile(
            raw,
            self._make_profile(),
            entity_lookup=self._LOOKUP,
            opening_balance=True,
            drop_unknown_entities=True,
        )
        assert len(out) == 2
        assert out.attrs.get("ignored_entity_labels") == []
