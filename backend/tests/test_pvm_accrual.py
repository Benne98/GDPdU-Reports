from __future__ import annotations

import importlib
import json
import sys
import tempfile
from pathlib import Path

import pandas as pd
import pytest


def _import_pvm():
    if "pvm_verformelt" in sys.modules:
        return sys.modules["pvm_verformelt"]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump({}, handle)
        config_path = handle.name
    old_argv = sys.argv
    try:
        sys.argv = ["pvm_verformelt.py", config_path]
        return importlib.import_module("pvm_verformelt")
    finally:
        sys.argv = old_argv
        Path(config_path).unlink(missing_ok=True)


def _config(mapping_mode: str):
    return {
        "file_path": "sales.xlsx",
        "sheet_name": "Data",
        "output_file_path": ".",
        "case_id": "test",
        "group_col": "Product",
        "quantity_col": "Quantity",
        "revenue_col": "Revenue",
        "invoice_col": "Invoice",
        "cost_col": "Cost",
        "as_of_year": 2025,
        "as_of_month": 12,
        "period_mode": "FY",
        "fy_end_month": 12,
        "fy_end_day": 31,
        "pvm_method": "chicago",
        "first_fy": 2024,
        "calc_mode": "accrual",
        "invoice_mapping_mode": mapping_mode,
        "start_col": "Contract start",
        "end_col": "Contract end",
    }


@pytest.mark.parametrize(
    ("mapping_mode", "invoice_value"),
    [("date", "2024-07-01"), ("year", "FY24A")],
)
def test_pvm_accrual_allocates_contract_across_fiscal_years(mapping_mode, invoice_value):
    pvm = _import_pvm()
    cfg = pvm.normalize_config(_config(mapping_mode))
    frame = pd.DataFrame(
        {
            "Product": ["A"],
            "Quantity": [365.0],
            "Revenue": [365.0],
            "Cost": [182.5],
            "Invoice": [invoice_value],
            "Contract start": ["01.07.2024"],
            "Contract end": ["30.06.2025"],
        }
    )
    prepared = pvm.preprocess_pvm_input(frame, cfg)
    fy24 = pvm.aggregate_year(
        prepared,
        pd.Timestamp("2024-01-01"),
        pd.Timestamp("2024-12-31"),
        cfg,
    )
    fy25 = pvm.aggregate_year(
        prepared,
        pd.Timestamp("2025-01-01"),
        pd.Timestamp("2025-12-31"),
        cfg,
    )
    assert fy24.loc[0, "revenue"] == pytest.approx(184.0)
    assert fy25.loc[0, "revenue"] == pytest.approx(181.0)
