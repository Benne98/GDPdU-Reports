"""Tests for partner gap synthetic master rows."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from etl.partner_gap_sync import (
    ResolvedPartner,
    build_synthetic_master_rows,
    merge_master_csv,
)


def test_build_synthetic_master_rows_uses_dim_name() -> None:
    resolved = [
        ResolvedPartner(
            side="customer",
            partner_id="0410001",
            partner_number="10001",
            entity_name="Venturo",
            entity_prefix="04",
            journal_entry_group_number="040000000001",
            fiscal_year=2025,
            source="gobd_csv",
        ),
    ]
    df = build_synthetic_master_rows(resolved, dim_names={"0410001": "Acme GmbH"})
    assert df.iloc[0]["No."] == "10001"
    assert df.iloc[0]["entity"] == "Venturo"
    assert df.iloc[0]["BAU Name lang"] == "Acme GmbH"


def test_merge_master_csv_dedupes(tmp_path: Path) -> None:
    p = tmp_path / "Customer Master.csv"
    pd.DataFrame([{"No.": "1", "entity": "Atlas", "BAU Name lang": "A", "BAU Adresse 2 lang": "",
                   "Country/Region Code": "", "City": "", "Post Code": ""}]).to_csv(p, index=False)
    synth = pd.DataFrame([
        {"No.": "1", "entity": "Atlas", "BAU Name lang": "Dup", "BAU Adresse 2 lang": "",
         "Country/Region Code": "", "City": "", "Post Code": ""},
        {"No.": "2", "entity": "Atlas", "BAU Name lang": "New Co", "BAU Adresse 2 lang": "",
         "Country/Region Code": "", "City": "", "Post Code": ""},
    ])
    added = merge_master_csv(p, synth)
    assert added == 1
    out = pd.read_csv(p, dtype=str)
    assert len(out) == 2
