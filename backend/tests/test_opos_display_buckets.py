"""Tests for OPOS aging bucket configuration."""

from opos import build_aging_bucket_defs, default_aging_ranges, normalize_config, validate_aging_ranges


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


def test_build_aging_bucket_defs_always_outputs_all_buckets():
    """display_bucket_keys no longer filters output — all buckets are always emitted."""
    cfg = {
        "display_bucket_keys": ["overdue_1_30", "overdue_over_180"],
        "aging_buckets": {"include_current": True, "ranges": default_aging_ranges()},
    }
    buckets = build_aging_bucket_defs(cfg)
    keys = [b.key for b in buckets]
    assert len(keys) == 6
    assert "overdue_31_60" in keys


def test_build_aging_bucket_defs_custom_range_count():
    cfg = {"aging_buckets": {"ranges": [[1, 30], [31, 90]]}}
    buckets = build_aging_bucket_defs(cfg)
    keys = [b.key for b in buckets]
    assert keys == ["not_yet_due", "overdue_1_30", "overdue_31_90", "overdue_over_90"]


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


def test_normalize_single_side_config_stores_aging_ranges():
    from opos import _normalize_single_side_config

    cfg = _normalize_single_side_config(
        {
            "side": "debitor",
            "aging_buckets": {"ranges": [[1, 30], [31, 60]]},
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
    assert cfg["aging_buckets"]["ranges"] == [[1, 30], [31, 60]]
    assert validate_aging_ranges(cfg["aging_buckets"]["ranges"]) == [(1, 30), (31, 60)]


def test_normalize_top_bucket_cfg_defaults_other_when_enabled():
    from opos import _normalize_top_bucket_cfg

    tb = _normalize_top_bucket_cfg({"top_bucket": {"enabled": True, "numbers": (10, 20)}})
    assert tb["create_other_bucket"] is False
    tb2 = _normalize_top_bucket_cfg(
        {"top_bucket": {"enabled": True, "numbers": (10, 20), "create_other_bucket": True}}
    )
    assert tb2["create_other_bucket"] is True


def test_normalize_combined_config_preserves_aging_buckets(tmp_path):
    import pandas as pd

    df = pd.DataFrame(
        {
            "Partner": ["A"],
            "Amount": [100000.0],
            "Due": ["2024-12-15"],
        }
    )
    src = tmp_path / "source.xlsx"
    df.to_excel(src, index=False)
    snap = {"as_of": "2024-12-31", "file_path": str(src), "sheet_name": "Sheet1"}
    cfg = normalize_config(
        {
            "column_letters": {"partner": "A", "amount": "B", "due_date": "C"},
            "aging_buckets": {"ranges": [[1, 15], [16, 45]]},
            "sides": {
                "debitor": {"snapshots": [snap]},
                "kreditor": {"snapshots": [snap]},
            },
            "output_file_path": str(tmp_path / "out.xlsx"),
            "case_id": "test",
        }
    )
    deb_ranges = cfg["sides"]["debitor"]["aging_buckets"]["ranges"]
    assert deb_ranges == [[1, 15], [16, 45]]
    buckets = build_aging_bucket_defs(cfg["sides"]["debitor"])
    labels = [b.label for b in buckets]
    assert "1-15 days" in labels
    assert "16-45 days" in labels
    assert "1-30 days" not in labels
