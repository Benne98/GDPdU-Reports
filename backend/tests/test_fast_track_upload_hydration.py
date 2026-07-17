from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.fast_track_upload_hydration import hydrate_strand_sections_from_uploads


def test_hydrates_opos_fte_fa_from_session_uploads():
    session_id = "7d4ad1ef"
    upload_base = ROOT / "uploads"
    if not (upload_base / session_id).is_dir():
        return
    sections: dict = {"databook": {"included": True}, "revenue": {"included": True}}
    hydrated = hydrate_strand_sections_from_uploads(
        session_id,
        upload_base,
        sections,
        dates={"fy_end_month": 12, "fy_end_day": 31},
    )
    assert hydrated == ["opos", "fte", "fa"]
    assert sections["opos"]["columns"]["partner_id"] == "Debitor"
    assert sections["opos"]["sides"]["kreditor"]["snapshots"][0]["sheet_name"] == "Kreditor 2025"
    assert sections["opos"]["column_letters"]["partner"]
    assert sections["fte"]["columns"]["employment"] == "Beschäftigungsgrad"
    assert sections["fa"]["columns"]["opening"] == "Buchwert GJ-Beg"
    assert len(sections["fte"]["periods"]) >= 3
    assert len(sections["fa"]["periods"]) >= 3
