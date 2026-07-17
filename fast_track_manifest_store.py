"""Persist Fast Track manifest on disk so section configs survive Rasa slot limits."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def manifest_file_path(session_id: str, upload_base: str | Path) -> Path:
    sid = str(session_id or "").strip()
    if not sid:
        raise ValueError("session_id is required for Fast Track manifest persistence")
    return Path(upload_base).expanduser() / sid / "fast_track_manifest.json"


def load_fast_track_manifest(session_id: str, upload_base: str | Path) -> dict[str, Any]:
    path = manifest_file_path(session_id, upload_base)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError, TypeError):
        return {}
    return dict(raw) if isinstance(raw, dict) else {}


def save_fast_track_manifest(
    session_id: str,
    upload_base: str | Path,
    manifest: dict[str, Any],
) -> Path:
    path = manifest_file_path(session_id, upload_base)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def merge_fast_track_manifests(
    base: dict[str, Any] | None,
    overlay: dict[str, Any] | None,
) -> dict[str, Any]:
    """Deep-merge section configs; overlay wins on key conflicts."""
    out = dict(base or {})
    overlay = dict(overlay or {})
    out_sections: dict[str, Any] = dict(out.get("sections") or {})
    for name, raw in (overlay.get("sections") or {}).items():
        if isinstance(raw, dict) and isinstance(out_sections.get(name), dict):
            merged = dict(out_sections[name])
            merged.update(raw)
            out_sections[name] = merged
        else:
            out_sections[name] = raw
    out["sections"] = out_sections
    for key, value in overlay.items():
        if key != "sections":
            out[key] = value
    return out


def resolve_master_upload_path(session_id: str, upload_base: str | Path) -> str:
    """Find the newest master workbook uploaded for this session."""
    root = Path(upload_base).expanduser() / str(session_id or "").strip()
    if not root.is_dir():
        return ""
    patterns = ("*Master*.xlsx", "*_SuSa_Master.xlsx", "*_Workbook.xlsx")
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(p for p in root.glob(pattern) if p.is_file())
    if not candidates:
        return ""
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return str(candidates[0].resolve())


def section_dir(session_id: str, upload_base: str | Path) -> Path:
    return Path(upload_base).expanduser() / str(session_id or "").strip() / "fast_track_sections"


def save_fast_track_section(
    session_id: str,
    upload_base: str | Path,
    section: str,
    config: dict[str, Any],
) -> Path:
    root = section_dir(session_id, upload_base)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{section}.json"
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_fast_track_section(
    session_id: str,
    upload_base: str | Path,
    section: str,
) -> dict[str, Any]:
    path = section_dir(session_id, upload_base) / f"{section}.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError, TypeError):
        return {}
    return dict(raw) if isinstance(raw, dict) else {}


def load_all_fast_track_sections(session_id: str, upload_base: str | Path) -> dict[str, dict[str, Any]]:
    root = section_dir(session_id, upload_base)
    if not root.is_dir():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError, TypeError):
            continue
        if isinstance(raw, dict):
            out[path.stem] = raw
    return out
