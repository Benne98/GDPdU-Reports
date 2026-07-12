"""Lightweight databook path helpers (no openpyxl — safe for Rasa actions)."""
from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def sanitize_project_basename(project_name: str) -> str:
    """Filesystem-safe project label for workbook filenames."""
    name = str(project_name or "").strip()
    if not name:
        return "Project"
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = re.sub(r"\s+", "_", name).strip("._")
    return name or "Project"


def master_workbook_filename(project_name: str = "Project") -> str:
    return f"{sanitize_project_basename(project_name)}_Master.xlsx"


def legacy_master_workbook_filename(session_id: str) -> str:
    return f"{session_id}_SuSa_Master.xlsx"


def master_workbook_path(
    session_id: str,
    output_folder: str | None = None,
    project_name: str = "Project",
) -> Path:
    folder = Path(output_folder) if output_folder else PROJECT_ROOT / "uploads" / session_id / "output"
    return folder / master_workbook_filename(project_name)


def resolve_existing_master_path(
    output_folder: str | Path | None,
    *,
    session_id: str = "",
    project_name: str = "Project",
    explicit_path: str | Path | None = None,
) -> Path | None:
    """Return the first existing session master (new name, then legacy SuSa name)."""
    if explicit_path:
        p = Path(explicit_path).expanduser()
        if p.is_file():
            return p.resolve()
    folder = Path(output_folder) if output_folder else None
    if folder and folder.is_file():
        return folder.resolve()
    if not folder or not folder.is_dir():
        if session_id:
            folder = PROJECT_ROOT / "uploads" / session_id / "output"
        else:
            return None
    for name in (
        master_workbook_filename(project_name),
        legacy_master_workbook_filename(session_id) if session_id else "",
    ):
        if not name:
            continue
        candidate = folder / name
        if candidate.is_file():
            return candidate.resolve()
    if folder.is_dir():
        masters = sorted(
            (p for p in folder.glob("*_Master.xlsx") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if masters:
            return masters[0].resolve()
    return None
