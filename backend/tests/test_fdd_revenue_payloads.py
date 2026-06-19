"""Unit tests for FDD revenue bot payloads and config normalization."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_PY = ROOT / "backend" / ".venv" / "bin" / "python"
RASA_PY = ROOT / "rasa" / ".venv" / "bin" / "python"


def _fdd_filters_payload(rules_json: str | None) -> dict:
    """Mirror of rasa/actions/actions.py::_fdd_filters_payload (keep in sync)."""
    try:
        raw = json.loads(rules_json or "[]")
    except (json.JSONDecodeError, TypeError):
        raw = []
    rules = raw if isinstance(raw, list) else []
    return {"enabled": len(rules) > 0, "rules": rules}


class TestFddFiltersPayload(unittest.TestCase):
    def test_empty_list(self):
        self.assertEqual(_fdd_filters_payload("[]"), {"enabled": False, "rules": []})

    def test_with_rules(self):
        rules = [{"col": "Region", "op": "eq", "values": ["DACH"]}]
        out = _fdd_filters_payload(json.dumps(rules))
        self.assertTrue(out["enabled"])
        self.assertEqual(out["rules"], rules)

    def test_invalid_json(self):
        self.assertEqual(_fdd_filters_payload("not-json"), {"enabled": False, "rules": []})


def _normalize_filters_cfg(raw_filters) -> dict:
    """Mirror of normalize_config filter block in GST/PVM scripts (keep in sync)."""
    if isinstance(raw_filters, list):
        filters = {"enabled": len(raw_filters) > 0, "rules": raw_filters}
    else:
        filters = dict(raw_filters or {})
    filters.setdefault("enabled", False)
    filters.setdefault("rules", [])
    return filters


class TestNormalizeConfigFilters(unittest.TestCase):
    def test_list_filters_legacy(self):
        rules = [{"col": "x", "op": "eq", "values": [1]}]
        out = _normalize_filters_cfg(rules)
        self.assertTrue(out["enabled"])
        self.assertEqual(out["rules"], rules)

    def test_empty_dict_filters(self):
        out = _normalize_filters_cfg({})
        self.assertFalse(out["enabled"])
        self.assertEqual(out["rules"], [])


class TestActionsHelpers(unittest.TestCase):
    @unittest.skipUnless(RASA_PY.is_file(), "rasa venv required")
    def test_header_opts_includes_selected_columns(self):
        script = """
import sys
sys.path.insert(0, "rasa/actions")
from actions import _header_opts_for_review

class T:
    def get_slot(self, _):
        return None

opts = _header_opts_for_review(T(), None, "Revenue Col", "Invoice Date")
labels = [o["value"] for o in opts]
assert "Revenue Col" in labels
assert "Invoice Date" in labels
print("ok")
"""
        proc = subprocess.run(
            [str(RASA_PY), "-c", script],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)


if __name__ == "__main__":
    unittest.main()
