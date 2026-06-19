"""Tests for BC partner master CSV loaders."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from etl.load_partner_masters import load_customer_master_csv, load_vendor_master_csv


def _write_customer_csv(tmp_path: Path) -> Path:
    p = tmp_path / "Customer Master.csv"
    pd.DataFrame([
        {
            "No.": "10001",
            "entity": "Venturo",
            "BAU Name lang": "Acme Retail GmbH",
            "BAU Adresse 2 lang": "",
            "Country/Region Code": "DE",
            "City": "Berlin",
            "Post Code": "10115",
        },
    ]).to_csv(p, index=False)
    return p


def _write_vendor_csv(tmp_path: Path) -> Path:
    p = tmp_path / "Vendor Master.csv"
    pd.DataFrame([
        {
            "No.": "70001",
            "entity": "Venturo",
            "BAU Name lang": "Steel Works AG",
            "BAU Adresse 2 lang": "Plant 2",
            "Country/Region Code": "DE",
            "City": "Duisburg",
            "Post Code": "47051",
        },
    ]).to_csv(p, index=False)
    return p


def test_load_customer_master_csv(tmp_path: Path) -> None:
    p = _write_customer_csv(tmp_path)
    df = load_customer_master_csv(p)
    assert len(df) == 1
    assert df.iloc[0]["customer_id"] == "0410001"
    assert df.iloc[0]["name_line_1"] == "Acme Retail GmbH"
    assert df.iloc[0]["debtor_number"] == "10001"


def test_load_vendor_master_csv(tmp_path: Path) -> None:
    p = _write_vendor_csv(tmp_path)
    df = load_vendor_master_csv(p)
    assert len(df) == 1
    assert df.iloc[0]["supplier_id"] == "0470001"
    assert "Steel Works" in (df.iloc[0]["name_line_1"] or "")
