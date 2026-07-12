"""Shared JSON config bootstrap for databook recon scripts."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def load_argv_config() -> dict[str, Any]:
    if len(sys.argv) < 2:
        return {}
    path = Path(sys.argv[1])
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def path_value(cfg: dict[str, Any], key: str, default: str) -> str:
    paths = cfg.get("paths") or {}
    val = paths.get(key)
    return str(val) if val else default


def apply_custom_entity_order(
    individual_entities: list[str],
    entity_order: list[str],
) -> list[str]:
    """Apply drag-and-drop entity order; append any entities not in the list."""
    order = [e for e in entity_order if e in individual_entities]
    remaining = [e for e in individual_entities if e not in order]
    return order + remaining
