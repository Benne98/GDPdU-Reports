from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from opos import normalize_config
from scripts.fast_track_upload_hydration import (
    coerce_json_dict,
    hydrate_strand_sections_from_uploads,
)


def test_coerce_mapping_dict_parses_json_string():
    raw = '{"partner":"P","amount":"L","due_date":"F"}'
    parsed = coerce_json_dict(raw)
    assert parsed == {"partner": "P", "amount": "L", "due_date": "F"}
    # dict(json_string) used to raise ValueError — ensure parsed value is a real dict
    assert parsed["partner"] == "P"


def test_hydration_does_not_overwrite_user_mapper_columns():
    sections = {
        "opos": {
            "included": True,
            "mapper_confirmed": True,
            "columns": {
                "partner_id": "Text",
                "partner_name": "Text",
                "amount": "Betrag in Hauswährung",
                "due_date": "Nettofälligkeit",
            },
            "group_cols": [],
        },
        "fte": {
            "included": True,
            "mapper_confirmed": True,
            "periods": [{"label": "FY2023", "file_path": "/tmp/personaltable_2023.xlsx"}],
            "columns": {
                "employment": "Beschäftigungsgrad",
                "months_sum": "Summe",
                "payroll_cols": ["Gesamtsumme"],
            },
            "group_cols": ["OrgEinheitenkurztext"],
        },
    }
    hydrated = hydrate_strand_sections_from_uploads("7d4ad1ef", ROOT / "uploads", sections)
    assert "opos" not in hydrated
    assert "fte" not in hydrated
    assert sections["opos"]["columns"]["partner_id"] == "Text"
    assert sections["fte"]["group_cols"] == ["OrgEinheitenkurztext"]
