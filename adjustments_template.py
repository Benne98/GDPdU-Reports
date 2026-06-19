"""
Generate an empty adjustments upload template (headers only, FY columns).

Driven by JSON config (first CLI argument) from the FDD backend after the user
chooses to apply adjustments.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

from databook_helpers import build_adjustments_template_from_config  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python adjustments_template.py <config.json>")
    config_path = Path(sys.argv[1])
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    out_path = build_adjustments_template_from_config(config)
    print(f"Adjustments template written: {out_path}")


if __name__ == "__main__":
    main()
