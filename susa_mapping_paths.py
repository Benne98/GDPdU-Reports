"""
Default paths for BS/PL Kontenmapping used by SuSabyYear and the FDD backend.

Files live in the repo's Desktop folder by default:
  Desktop/BS_Kontenmapping.xlsx
  Desktop/PL_Kontenmapping.xlsx

Override via environment:
  SUSA_BS_MAPPING_PATH
  SUSA_PL_MAPPING_PATH
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent

BS_FILENAME = "BS_Kontenmapping.xlsx"
PL_FILENAME = "PL_Kontenmapping.xlsx"


def default_mapping_dir() -> Path:
    return PROJECT_ROOT / "Desktop"


def default_bs_mapping_path() -> Path:
    return default_mapping_dir() / BS_FILENAME


def default_pl_mapping_path() -> Path:
    return default_mapping_dir() / PL_FILENAME


def finssentials_sibling_mapping_dir() -> Path:
    """Local dev fallback: ../finssentials/Desktop next to GDPdU-Reports."""
    return PROJECT_ROOT.parent / "finssentials" / "Desktop"


def resolve_susa_kontenmapping_paths(
    config: dict[str, Any] | None = None,
) -> tuple[Optional[str], Optional[str]]:
    """
    Resolve BS/PL mapping file paths.

    Priority:
      1. config['bs_mapping_path'] / config['pl_mapping_path']
      2. SUSA_BS_MAPPING_PATH / SUSA_PL_MAPPING_PATH env vars
      3. <repo>/Desktop/BS_Kontenmapping.xlsx and PL_Kontenmapping.xlsx
      4. ../finssentials/Desktop/ (sibling repo, local dev)
      5. {output}/mappings/… (legacy)
    """
    cfg = config or {}
    bs = str(cfg.get("bs_mapping_path") or os.environ.get("SUSA_BS_MAPPING_PATH") or "").strip()
    pl = str(cfg.get("pl_mapping_path") or os.environ.get("SUSA_PL_MAPPING_PATH") or "").strip()

    if not bs:
        candidate = default_bs_mapping_path()
        if candidate.is_file():
            bs = str(candidate)
    if not pl:
        candidate = default_pl_mapping_path()
        if candidate.is_file():
            pl = str(candidate)

    sibling = finssentials_sibling_mapping_dir()
    if not bs and (sibling / BS_FILENAME).is_file():
        bs = str(sibling / BS_FILENAME)
        print(f"[INFO] Using BS Kontenmapping from {bs}")
    if not pl and (sibling / PL_FILENAME).is_file():
        pl = str(sibling / PL_FILENAME)
        print(f"[INFO] Using PL Kontenmapping from {pl}")

    if not bs or not pl:
        search_dirs: list[Path] = []
        for key in ("output_file_path", "output_path"):
            val = cfg.get(key)
            if val:
                p = Path(str(val))
                search_dirs.append(p if p.is_dir() else p.parent)
        for d in search_dirs:
            mdir = d / "mappings"
            if not bs and (mdir / BS_FILENAME).is_file():
                bs = str(mdir / BS_FILENAME)
            if not pl and (mdir / PL_FILENAME).is_file():
                pl = str(mdir / PL_FILENAME)

    return bs or None, pl or None
