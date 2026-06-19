import pandas as pd
import pytest

from etl import transform as T


def test_normalize_token_strips_dot_zero_and_space():
    s = pd.Series([" 38235.0 ", "33700", "1.0"])
    assert list(T.normalize_token(s)) == ["38235", "33700", "1"]


def test_normalize_prefix_pads_and_validates():
    assert T.normalize_prefix("1") == "01"
    assert T.normalize_prefix("5.0") == "05"
    assert T.normalize_prefix(42) == "42"
    with pytest.raises(ValueError):
        T.normalize_prefix("A1")
    with pytest.raises(ValueError):
        T.normalize_prefix("123")  # exceeds 2 digits


def test_build_account_number_group():
    acct = pd.Series(["38235", "1"])
    out = T.build_account_number_group("01", acct)
    assert list(out) == ["01038235", "01000001"]
    assert (out.str.len() == 8).all()


def test_build_journal_entry_group_number():
    jen = pd.Series(["1", "1234567890"])
    out = T.build_journal_entry_group_number("07", jen)
    assert list(out) == ["070000000001", "071234567890"]
    assert (out.str.len() == 12).all()


def test_build_partner_id():
    assert list(T.build_partner_id("01", pd.Series(["100", "200.0"]))) == ["01100", "01200"]


def test_signed_amount_signed_mode_locale():
    s = pd.Series(["1.234,56", "-7,00"])
    out = T.signed_amount("signed", amount=s, decimal=",", thousands=".")
    assert out.tolist() == pytest.approx([1234.56, -7.0])


def test_signed_amount_soll_haben():
    soll = pd.Series(["1000", "0"])
    haben = pd.Series(["0", "190"])
    out = T.signed_amount("soll_haben", soll=soll, haben=haben, thousands=None)
    assert out.tolist() == pytest.approx([1000.0, -190.0])


def test_signed_amount_amount_dc():
    amount = pd.Series(["190", "190"])
    dc = pd.Series(["S", "H"])
    out = T.signed_amount("amount_dc", amount=amount, dc_flag=dc, thousands=None)
    assert out.tolist() == pytest.approx([190.0, -190.0])


def test_signed_amount_unknown_mode():
    with pytest.raises(ValueError):
        T.signed_amount("nope", amount=pd.Series(["1"]))


def test_derive_partner_ids_splits_by_source_type():
    st = pd.Series(["Debitor", "Kreditor", "Sachkonto"])
    sn = pd.Series(["100", "200", "999"])
    cust, supp = T.derive_partner_ids("01", st, sn)
    assert cust.iloc[0] == "01100"
    assert cust.isna().tolist() == [False, True, True]
    assert supp.iloc[1] == "01200"
    assert supp.isna().tolist() == [True, False, True]


def test_assign_line_numbers_resets_per_group():
    df = pd.DataFrame({"j": ["A", "A", "B", "A"]})
    assert T.assign_line_numbers(df, ["j"]).tolist() == [1, 2, 1, 3]
