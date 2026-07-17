#!/usr/bin/env python3
"""
Fast Track PDF report entrypoint.

Usage:
  python scripts/fast_track_pdf_report.py /path/to/config.json

Config JSON keys:
  workbook_path | input_path | excel_path  — Fast Track .xlsx
  output_path                              — destination .pdf (optional)
  project_name | title
  company_name | company | group_name
  output_folder | output_file_path
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_paths() -> None:
    root = _project_root()
    scripts = root / "scripts"
    for p in (str(root), str(scripts)):
        if p not in sys.path:
            sys.path.insert(0, p)


def _resolve_workbook(cfg: dict) -> Path:
    for key in ("workbook_path", "input_path", "excel_path", "file_path"):
        raw = str(cfg.get(key) or "").strip()
        if raw and Path(raw).is_file() and raw.lower().endswith((".xlsx", ".xlsm")):
            return Path(raw).expanduser().resolve()
    # Prefer explicit Fast Track output if present
    for key in ("fast_track_output_path", "output_path"):
        raw = str(cfg.get(key) or "").strip()
        if raw and Path(raw).is_file() and raw.lower().endswith((".xlsx", ".xlsm")):
            return Path(raw).expanduser().resolve()
    folder = str(cfg.get("output_folder") or cfg.get("output_file_path") or "").strip()
    if folder and Path(folder).is_dir():
        candidates = sorted(
            Path(folder).glob("*FastTrack*.xlsx"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            return candidates[0].resolve()
        xlsx = sorted(Path(folder).glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
        if xlsx:
            return xlsx[0].resolve()
    raise FileNotFoundError(
        "Fast Track workbook not found. Provide workbook_path or output_folder with a FastTrack.xlsx."
    )


def _default_pdf_path(workbook: Path, cfg: dict) -> Path:
    explicit = str(cfg.get("pdf_output_path") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    # If output_path ends with .pdf use it; if .xlsx derive sibling pdf
    out = str(cfg.get("output_path") or "").strip()
    if out.lower().endswith(".pdf"):
        return Path(out).expanduser().resolve()
    stem = workbook.stem
    if stem.endswith("_FastTrack"):
        pdf_name = f"{stem}_Report.pdf"
    else:
        pdf_name = f"{stem}_FastTrack_Report.pdf"
    folder = str(cfg.get("output_folder") or cfg.get("output_file_path") or "").strip()
    if folder:
        return (Path(folder) / pdf_name).resolve()
    return (workbook.parent / pdf_name).resolve()


def run_from_config(cfg: dict) -> dict:
    _ensure_paths()
    from pdf_report.builder import build_fast_track_pdf_report

    workbook = _resolve_workbook(cfg)
    pdf_path = _default_pdf_path(workbook, cfg)
    # Prefer config project name; never invent from sheet cells for the cover.
    # Sheet-level titles remain as stored in Excel (may differ per strand).
    project = str(
        cfg.get("project_name") or cfg.get("title") or "Project"
    ).strip()
    company = str(
        cfg.get("company_name") or cfg.get("company") or cfg.get("group_name") or ""
    ).strip()

    written = build_fast_track_pdf_report(
        workbook,
        pdf_path,
        project_name=project,
        company_name=company,
        report_date=date.today(),
        recalc_excel=bool(cfg.get("recalc_excel", True)),
    )
    # Ensure monitor can find output_path
    return {
        "success": True,
        "output_file": written,
        "output_path": written,
        "workbook_path": str(workbook),
        "project_name": project,
        "company_name": company,
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        print("Usage: fast_track_pdf_report.py <config.json>", file=sys.stderr)
        return 2
    config_path = Path(argv[0]).expanduser().resolve()
    if not config_path.is_file():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return 2
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        print("Config must be a JSON object", file=sys.stderr)
        return 2
    try:
        result = run_from_config(cfg)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    # Write result next to config for debugging; monitor uses config output_path
    print(json.dumps(result, ensure_ascii=False))
    # Persist absolute pdf path into a sidecar so status resolution is robust
    sidecar = config_path.with_name("pdf_result.json")
    try:
        sidecar.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    # Also touch/ensure the file exists at output_path expected by monitor
    out = result.get("output_path")
    if out and not Path(out).is_file():
        print(f"ERROR: PDF was not written to {out}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
