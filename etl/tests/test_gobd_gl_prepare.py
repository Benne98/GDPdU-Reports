"""Tests for GoBD GL opening-balance preprocessing."""
from __future__ import annotations

import pandas as pd

from etl import checks as C
from etl.gobd_gl_prepare import prepare_gobd_gl_frame, tag_opening_balances_in_canonical


def _raw_with_opening() -> pd.DataFrame:
    return pd.DataFrame({
        "Entity No": ["1", "1", "1"],
        "Year": ["2024", "2024", "2024"],
        "Account number": ["10000", "80000", "16100"],
        "Posting date": ["15.01.2024", "15.01.2024", "01.01.2024"],
        "Amount": ["100.00", "-100.00", "5000.00"],
        "Transaction number": ["TXN1", "TXN1", ""],
        "Booking text": ["sale", "sale", ""],
        "Booking number": ["1", "1", "6"],
        "Document type": ["RE", "RE", ""],
        "Document number": ["D1", "D1", ""],
        "VAT amount": ["", "", ""],
        "Posting type": ["", "", ""],
        "Document date": ["15.01.2024", "15.01.2024", ""],
        "Source type": ["", "", ""],
        "Source No.": ["", "", ""],
        "Account number (group)": ["1010000", "1080000", "1016100"],
    })


def test_prepare_retains_jan1_opening_without_txn():
    out = prepare_gobd_gl_frame(_raw_with_opening())
    assert len(out) == 3
    ob = out[out["_is_opening_balance"]]
    assert len(ob) == 1
    assert ob.iloc[0]["Transaction number"] != ""
    assert ob.iloc[0]["Booking text"] == "Start value"


def test_tag_opening_balances_in_canonical():
    raw = prepare_gobd_gl_frame(_raw_with_opening())
    canonical = pd.DataFrame({
        "fiscal_period": [1, 1, 1],
        "entry_type": ["actual", "actual", "actual"],
    })
    tagged = tag_opening_balances_in_canonical(canonical, raw["_is_opening_balance"])
    assert tagged.iloc[2]["fiscal_period"] == 0
    assert tagged.iloc[2]["entry_type"] == "opening_balance"


def test_b1_exempts_opening_balance_rows():
    lines = pd.DataFrame({
        "journal_entry_group_number": ["01001", "01002", "01002"],
        "fiscal_year": [2024, 2024, 2024],
        "fiscal_period": [0, 1, 1],
        "entry_type": ["opening_balance", "actual", "actual"],
        "amount": [5000.0, 100.0, -100.0],
    })
    assert C.check_booking_balance(lines).passed
