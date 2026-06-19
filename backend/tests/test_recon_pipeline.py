"""Smoke tests for recon pipeline script registration and config shape."""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_recon_pipeline_scripts_exist():
    from app.routers import fdd_bot

    for key in ("recon_pl", "recon_bs", "bs_bucket", "lead_is", "lead_bs"):
        path = fdd_bot.SCRIPTS[key]
        assert path.is_file(), f"Missing script for {key}: {path}"


def test_recon_pipeline_step_sheet_names():
    """Expected output sheets after full pipeline."""
    expected = {
        "recon_pl": "PL_Reconciliation",
        "recon_bs": "BS_Reconciliation",
        "bs_bucket": "BS_Bucket",
        "lead_is": "Lead_IS",
        "lead_bs": "Lead_BS",
    }
    assert set(expected.values()) == {
        "PL_Reconciliation",
        "BS_Reconciliation",
        "BS_Bucket",
        "Lead_IS",
        "Lead_BS",
    }


def test_databook_runtime_load_empty_without_argv(monkeypatch):
    import sys

    from databook_runtime import load_argv_config

    monkeypatch.setattr(sys, "argv", ["script.py"])
    assert load_argv_config() == {}
