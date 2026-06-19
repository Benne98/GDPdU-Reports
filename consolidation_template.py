"""
Generate an empty consolidation upload template (headers only).

Driven by JSON config (first CLI argument) from the FDD backend after the user
chooses yearly vs monthly consolidation period level.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

from databook_helpers import build_consolidation_template_from_config  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python consolidation_template.py <config.json>")
    config_path = Path(sys.argv[1])
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    out_path = build_consolidation_template_from_config(config)
    print(f"Consolidation template written: {out_path}")


if __name__ == "__main__":
    main()
