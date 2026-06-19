"""Tests for etl/bs_pl_master.py — synthetic data only."""
from __future__ import annotations

import pandas as pd
import pytest

from etl.bs_pl_master import (
    BS_PL_REPLACE_MODES,
    ENTITY_PREFIX_COL,
    build_mapping_frames,
    bs_pl_master_profile,
    detect_level_columns,
    filter_mapping_append_only,
    is_bs_pl_master_workbook,
    preview_stats,
    read_bs_pl_master,
    resolve_entity_prefixes,
)
from etl.mapping_account import apply_account_mapping


def _raw_bs_pl() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Entity": ["Atlas", "Atlas"],
            "Account": ["16100", "81910"],
            "Account description": ["Asset acct", "PL acct"],
            "L1": ["BS", "PL"],
            "L2": ["Fixed assets", "Income"],
            "L3": ["Tangible", "Net sales"],
            "L4": ["PPE", "Reduction"],
        }
    )


def test_detect_level_columns_l4_only():
    cols = detect_level_columns(["Entity", "L1", "L2", "L3", "L4"])
    assert cols == ["L1", "L2", "L3", "L4"]


def test_detect_level_columns_with_optional_l5_l6():
    cols = detect_level_columns(["L1", "L2", "L3", "L4", "L5", "L6"])
    assert cols == ["L1", "L2", "L3", "L4", "L5", "L6"]


def test_is_bs_pl_master_workbook():
    assert is_bs_pl_master_workbook(["Master_BS", "Master_PL", "Other"])
    assert not is_bs_pl_master_workbook(["Master_BS"])
    assert not is_bs_pl_master_workbook(["PL", "BS"])


def test_resolve_entity_prefixes():
    raw = _raw_bs_pl()
    lookup = {"Atlas": "01", "Meridian": "02"}
    resolved, unknown = resolve_entity_prefixes(raw, lookup)
    assert unknown == []
    assert list(resolved[ENTITY_PREFIX_COL]) == ["01", "01"]


def test_build_account_number_group_via_profile():
    raw = _raw_bs_pl()
    lookup = {"Atlas": "01"}
    resolved, _ = resolve_entity_prefixes(raw, lookup)
    prof = bs_pl_master_profile(list(raw.columns))
    out = apply_account_mapping(resolved, prof, fiscal_year=2024)
    assert out.loc[0, "account_number_group"] == "01016100"
    assert out.loc[1, "account_number_group"] == "01081910"
    assert out.loc[0, "level_0"] == "BS"
    assert out.loc[1, "level_3"] == "Reduction"
    assert pd.isna(out.loc[1, "level_4"])


def test_optional_l5_l6_mapping():
    raw = _raw_bs_pl().copy()
    raw["L5"] = ["Detail", pd.NA]
    raw["L6"] = ["Sub", pd.NA]
    lookup = {"Atlas": "01"}
    resolved, _ = resolve_entity_prefixes(raw, lookup)
    prof = bs_pl_master_profile(list(raw.columns))
    out = apply_account_mapping(resolved, prof, fiscal_year=2024)
    assert out.loc[0, "level_4"] == "Detail"
    assert out.loc[0, "l4_sub"] == "Sub"


class _FakeSession:
    def execute(self, *_args, **_kwargs):
        class _R:
            def fetchall(self):
                return [
                    ("Atlas", "Atlas GmbH", "01"),
                    ("Meridian", "Meridian GmbH", "02"),
                ]

        return _R()


def test_build_mapping_frames_replicates_fiscal_years():
    raw = _raw_bs_pl()
    session = _FakeSession()
    frames, meta = build_mapping_frames(raw, session, [2023, 2024])
    assert len(frames) == 4
    assert set(frames["fiscal_year"].astype(int)) == {2023, 2024}
    assert meta["skipped_rows"] == 0
    assert meta["level_columns"] == ["L1", "L2", "L3", "L4"]


def test_bs_pl_master_profile_maps_l4_to_level_3():
    prof = bs_pl_master_profile()
    assert prof.columns["level_3"] == "L4"
    assert "level_4" not in prof.columns


def test_read_user_template_structure():
    """Smoke-read the L1–L4 desktop template (structure only)."""
    from pathlib import Path

    path = Path(r"C:\Users\bened\OneDrive\Desktop\BS_PL_Master Test input.xlsx")
    if not path.exists():
        pytest.skip("Desktop template not present")
    df = read_bs_pl_master(path)
    assert list(df.columns) == [
        "Entity",
        "Account",
        "Account description",
        "L1",
        "L2",
        "L3",
        "L4",
    ]
    assert len(df) > 0


def test_bs_pl_replace_modes():
    assert BS_PL_REPLACE_MODES == frozenset({"replace", "append"})


def test_filter_mapping_append_only_skips_existing():
    raw = _raw_bs_pl()
    lookup = {"Atlas": "01"}
    resolved, _ = resolve_entity_prefixes(raw, lookup)
    prof = bs_pl_master_profile(list(raw.columns))
    mapping_df = apply_account_mapping(resolved, prof, fiscal_year=2024)

    class _Session:
        def execute(self, stmt, params=None):
            sql = str(stmt)

            class _R:
                def __init__(self, rows):
                    self._rows = rows

                def fetchall(self):
                    return self._rows

            if "account_number_group = ANY" in sql and "entity_prefix" not in sql:
                return _R([("01016100", 2024)])
            return _R([])

    filtered = filter_mapping_append_only(mapping_df, _Session(), [2024])
    assert len(filtered) == 1
    assert filtered.iloc[0]["account_number_group"] == "01081910"


def test_preview_stats_append_reports_skip_count():
    raw = _raw_bs_pl()
    lookup = {"Atlas": "01"}
    resolved, _ = resolve_entity_prefixes(raw, lookup)
    prof = bs_pl_master_profile(list(raw.columns))
    mapping_df = apply_account_mapping(resolved, prof, fiscal_year=2024)

    class _Session:
        def execute(self, stmt, params=None):
            sql = str(stmt)

            class _R:
                def __init__(self, rows):
                    self._rows = rows

                def fetchall(self):
                    return self._rows

            if "dim_legal_entity" in sql:
                return _R([("Atlas", "Atlas GmbH", "01")])
            if "entity_prefix = ANY" in sql and "ORDER BY" not in sql:
                return _R([("01016100", 2024), ("01081910", 2024)])
            if "account_number_group = ANY" in sql:
                return _R([("01016100", 2024)])
            return _R([])

    stats = preview_stats(
        mapping_df,
        _Session(),
        [2024],
        1,
        1,
        replace_mode="append",
    )
    assert stats["would_insert"] == 1
    assert stats["would_skip"] == 1
    assert stats["would_delete"] == 0


def test_preview_stats_replace_reports_delete_count():
    raw = _raw_bs_pl()
    lookup = {"Atlas": "01"}
    resolved, _ = resolve_entity_prefixes(raw, lookup)
    prof = bs_pl_master_profile(list(raw.columns))
    mapping_df = apply_account_mapping(resolved, prof, fiscal_year=2024)

    class _Session:
        def execute(self, stmt, params=None):
            sql = str(stmt)

            class _R:
                def __init__(self, rows):
                    self._rows = rows

                def fetchall(self):
                    return self._rows

            if "dim_legal_entity" in sql:
                return _R([("Atlas", "Atlas GmbH", "01")])
            if "entity_prefix = ANY" in sql and "ORDER BY" not in sql:
                return _R(
                    [
                        ("01016100", 2024),
                        ("01081910", 2024),
                        ("01999999", 2024),
                    ]
                )
            if "fact_gl_line" in sql:
                return _R([])
            if "gl_account_id, account_name" in sql:
                return _R(
                    [
                        ("01999999", 2024, "01999999", "Orphan", "BS", "Assets", "Cash"),
                    ]
                )
            if "account_number_group = ANY" in sql:
                return _R([("01016100", 2024), ("01081910", 2024)])
            return _R([])

    stats = preview_stats(
        mapping_df,
        _Session(),
        [2024],
        1,
        1,
        replace_mode="replace",
    )
    assert stats["would_delete"] == 1
    assert len(stats["delete_details"]) == 1
    assert stats["delete_details"][0]["account_number_group"] == "01999999"
