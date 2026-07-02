"""Shared databook master workbook path (PL + BS + lead outputs in one file)."""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DESKTOP_DIR = PROJECT_ROOT / "Desktop"
MASTER_WORKBOOK = DESKTOP_DIR / "BS_PL_Master.xlsx"
MASTER_WORKBOOK_STR = str(MASTER_WORKBOOK)
