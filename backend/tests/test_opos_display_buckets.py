"""Tests for OPOS display-bucket filtering."""

from opos import build_aging_bucket_defs


def test_build_aging_bucket_defs_all_buckets_by_default():
    buckets = build_aging_bucket_defs({"aging_buckets": {"include_current": True}})
    keys = [b.key for b in buckets]
    assert keys == [
        "not_yet_due",
        "overdue_1_30",
        "overdue_31_60",
        "overdue_61_90",
        "overdue_91_180",
        "overdue_over_180",
    ]


def test_build_aging_bucket_defs_filters_display_keys():
    cfg = {
        "display_bucket_keys": ["overdue_1_30", "overdue_over_180"],
        "aging_buckets": {"include_current": True},
    }
    buckets = build_aging_bucket_defs(cfg)
    keys = [b.key for b in buckets]
    assert keys == ["overdue_1_30", "overdue_over_180"]


def test_build_aging_bucket_defs_includes_not_yet_due_when_selected():
    cfg = {"display_bucket_keys": ["not_yet_due", "overdue_1_30"]}
    buckets = build_aging_bucket_defs(cfg)
    keys = [b.key for b in buckets]
    assert keys == ["not_yet_due", "overdue_1_30"]


def test_opos_pos_col_short_helper_layout():
    from opos import POS_COL

    assert POS_COL == 4


def test_all_dates_sort_descending_by_combined_totals():
    import pandas as pd

    from opos import (
        AsOfPeriod,
        aggregate_opos,
        build_aging_bucket_defs,
        normalize_config,
        preprocess_opos_input,
    )

    cfg = normalize_config(
        {
            "side": "debitor",
            "columns": {
                "partner_id": "Debitor",
                "partner_name": "Text",
                "amount": "Betrag",
                "due_date": "Due",
            },
            "snapshots": [
                {"as_of": "2024-12-31", "file_path": "x", "sheet_name": "S"},
                {"as_of": "2025-07-31", "file_path": "x", "sheet_name": "S"},
            ],
            "sort": {"basis": "all_dates", "metric": "total"},
            "output_file_path": "/tmp/opos_test.xlsx",
            "case_id": "test",
        }
    )
    buckets = build_aging_bucket_defs(cfg)
    periods = [
        AsOfPeriod("Dec24A", pd.Timestamp("2024-12-31")),
        AsOfPeriod("Jul25A", pd.Timestamp("2025-07-31")),
    ]
    df_dec = pd.DataFrame(
        {
            "Debitor": ["A", "B"],
            "Text": ["Alpha", "Beta"],
            "Betrag": [1000.0, 20000.0],
            "Due": ["2024-11-01", "2024-11-01"],
        }
    )
    df_jul = pd.DataFrame(
        {
            "Debitor": ["A", "B"],
            "Text": ["Alpha", "Beta"],
            "Betrag": [5000.0, 1000.0],
            "Due": ["2025-06-01", "2025-06-01"],
        }
    )
    snapshot_dfs = {
        "Dec24A": preprocess_opos_input(df_dec, cfg),
        "Jul25A": preprocess_opos_input(df_jul, cfg),
    }
    _grid, order, _meta = aggregate_opos(snapshot_dfs, cfg, periods, buckets)
    assert order == ["B", "A"]

    cfg_kred = normalize_config(
        {
            **cfg,
            "side": "kreditor",
            "output_file_path": "/tmp/opos_test.xlsx",
            "case_id": "test",
        }
    )
    _grid2, order_kred, _ = aggregate_opos(snapshot_dfs, cfg_kred, periods, buckets)
    assert order_kred == ["B", "A"]


def test_resolve_bucket_for_due_respects_selected_buckets():
    from opos import AgingBucket, resolve_bucket_for_due
    import pandas as pd

    buckets = [
        AgingBucket("overdue_1_30", "1-30 days", 1, 30),
        AgingBucket("overdue_over_180", ">180 days", 181, None),
    ]
    as_of = pd.Timestamp("2024-12-31")
    assert resolve_bucket_for_due(pd.Timestamp("2024-12-15"), as_of, buckets) == "overdue_1_30"
    assert resolve_bucket_for_due(pd.Timestamp("2024-06-01"), as_of, buckets) == "overdue_over_180"
    assert resolve_bucket_for_due(pd.Timestamp("2024-10-15"), as_of, buckets) is None


def test_compute_top_bucket_range_ends_includes_other():
    from opos import compute_top_bucket_range_ends

    cfg = {
        "top_bucket": {
            "enabled": True,
            "numbers": (10, 20),
            "create_other_bucket": True,
            "other_bucket_label": "Other",
        }
    }
    partners = [f"P{i}" for i in range(35)]
    segments = compute_top_bucket_range_ends(partners, [1.0] * 35, cfg)
    assert segments[-1] == (35, "Other")


def test_clear_opos_workbook_sheets_preserves_fa_sources():
    from openpyxl import Workbook

    from opos import SIDE_LABELS, _clear_opos_workbook_sheets

    wb = Workbook()
    wb.remove(wb.active)
    wb.create_sheet("__SOURCE__Dec25A")
    wb.create_sheet("__SOURCE__AR_Jul25A")
    wb.create_sheet("__SOURCE__AP_Jul25A")
    for labels in SIDE_LABELS.values():
        wb.create_sheet(labels["detail_sheet"])
        wb.create_sheet(labels["summary_sheet"])

    _clear_opos_workbook_sheets(wb)
    assert "__SOURCE__Dec25A" in wb.sheetnames
    assert "__SOURCE__AR_Jul25A" not in wb.sheetnames
    assert "__SOURCE__AP_Jul25A" not in wb.sheetnames
    for labels in SIDE_LABELS.values():
        assert labels["detail_sheet"] not in wb.sheetnames
        assert labels["summary_sheet"] not in wb.sheetnames


def test_build_opos_detail_lines_other_bucket_last_line():
    import pandas as pd

    from opos import AsOfPeriod, build_aging_bucket_defs, build_opos_detail_lines

    cfg = {
        "top_bucket": {
            "enabled": True,
            "numbers": (10, 20),
            "create_other_bucket": True,
            "other_bucket_label": "Other",
        },
        "sort": {"basis": "latest", "metric": "total", "bucket_keys": ["__total__"]},
    }
    partners = [f"P{i}" for i in range(35)]
    grid = {pid: {} for pid in partners}
    periods = [AsOfPeriod("Dec24A", pd.Timestamp("2024-12-31"))]
    buckets = build_aging_bucket_defs(cfg)
    lines = build_opos_detail_lines(partners, grid, cfg, periods, buckets)
    assert lines[-1].kind == "top_bucket"
    assert lines[-1].bucket_label == "Other"


def test_build_aging_bucket_defs_excludes_not_yet_due_when_not_selected():
    cfg = {
        "display_bucket_keys": ["overdue_1_30"],
        "aging_buckets": {"include_current": True},
    }
    buckets = build_aging_bucket_defs(cfg)
    assert [b.key for b in buckets] == ["overdue_1_30"]


def test_normalize_single_side_config_include_current_follows_display_keys():
    from opos import _normalize_single_side_config

    cfg = _normalize_single_side_config(
        {
            "side": "debitor",
            "display_bucket_keys": ["overdue_1_30", "overdue_31_60"],
            "columns": {
                "partner_id": "Konto",
                "partner_name": "Konto",
                "amount": "Betrag",
                "due_date": "Faellig",
            },
            "snapshots": [{"as_of": "2024-12-31", "file_path": "/x/a.xlsx", "sheet_name": "S1"}],
            "output_file_path": "/tmp/out.xlsx",
            "case_id": "c1",
        }
    )
    assert cfg["display_bucket_keys"] == ["overdue_1_30", "overdue_31_60"]
    assert cfg["aging_buckets"]["include_current"] is False


def test_normalize_top_bucket_cfg_defaults_other_when_enabled():
    from opos import _normalize_top_bucket_cfg

    tb = _normalize_top_bucket_cfg({"top_bucket": {"enabled": True, "numbers": (10, 20)}})
    assert tb["create_other_bucket"] is True


def test_combined_side_inherits_display_bucket_keys():
    from opos import _SHARED_SIDE_KEYS, build_aging_bucket_defs

    parent = {
        "display_bucket_keys": ["overdue_1_30", "not_yet_due"],
        "top_bucket": {"enabled": True, "numbers": (10,), "create_other_bucket": True},
        "sides": {"debitor": {}, "kreditor": {}},
    }
    for side in ("debitor", "kreditor"):
        side_cfg = dict(parent["sides"][side])
        for key in _SHARED_SIDE_KEYS:
            if key in parent:
                side_cfg[key] = parent[key]
        buckets = build_aging_bucket_defs(side_cfg)
        assert [b.key for b in buckets] == ["overdue_1_30", "not_yet_due"]
