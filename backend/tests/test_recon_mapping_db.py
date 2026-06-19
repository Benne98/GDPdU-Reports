"""Tests for recon mapping ETL and file loader."""
from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_read_pl_recon_excel_from_desktop():
    from etl.recon_mapping import read_pl_recon_excel

    path = PROJECT_ROOT / "Desktop" / "PL_recon_Mapping.xlsx"
    if not path.is_file():
        pytest.skip("Desktop PL_recon_Mapping.xlsx not present")
    df = read_pl_recon_excel(path)
    assert len(df) >= 10
    assert "L3" in df.columns
    assert "sort_order" in df.columns


def test_read_bs_recon_excel_from_desktop():
    from etl.recon_mapping import read_bs_recon_excel

    path = PROJECT_ROOT / "Desktop" / "BS_recon_Mapping.xlsx"
    if not path.is_file():
        pytest.skip("Desktop BS_recon_Mapping.xlsx not present")
    df = read_bs_recon_excel(path)
    assert len(df) >= 10
    assert {"L2", "L3", "L4"}.issubset(df.columns)


def test_load_pl_recon_mapping_from_file():
    from recon_mapping_loader import load_pl_recon_mapping_df

    path = PROJECT_ROOT / "Desktop" / "PL_recon_Mapping.xlsx"
    if not path.is_file():
        pytest.skip("Desktop PL_recon_Mapping.xlsx not present")
    df = load_pl_recon_mapping_df(
        {"paths": {"mapping_source": "file", "mapping_file": str(path)}}
    )
    assert not df.empty
    assert "L3" in df.columns and "L4" in df.columns
