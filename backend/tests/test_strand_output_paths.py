"""Tests for strand output workbook path resolution (master vs separate)."""

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from funktionssammlung import build_output_file_path, find_session_output_file


def test_build_output_file_path_separate_aging(tmp_path):
    cfg = {
        "title": "Atlas Deal",
        "output_file_path": str(tmp_path),
        "case_id": "sess-123",
        "use_separate_workbook": True,
        "separate_workbook_suffix": "Aging",
    }
    path = build_output_file_path(cfg)
    assert path.endswith("Atlas_Deal_Aging.xlsx")


def test_build_output_file_path_separate_fa_rollf(tmp_path):
    cfg = {
        "project_name": "Acme Corp",
        "output_file_path": str(tmp_path),
        "case_id": "sess-1",
        "use_separate_workbook": True,
        "separate_workbook_suffix": "FA_Rollf",
    }
    path = build_output_file_path(cfg)
    assert path.endswith("Acme_Corp_FA_Rollf.xlsx")


def test_build_output_file_path_uses_explicit_master(tmp_path):
    master = tmp_path / "custom_master.xlsx"
    master.write_bytes(b"")
    cfg = {
        "output_file_path": str(tmp_path),
        "case_id": "sess-9",
        "output_workbook_path": str(master),
    }
    assert build_output_file_path(cfg) == str(master.resolve())


def test_build_output_file_path_session_master_regression(tmp_path):
    cfg = {
        "output_file_path": str(tmp_path),
        "case_id": "sess-abc",
        "use_session_workbook": True,
    }
    path = build_output_file_path(cfg)
    assert path.endswith("sess-abc_Master.xlsx")


def test_find_session_output_file_separate_after_write(tmp_path):
    cfg = {
        "title": "Project X",
        "output_file_path": str(tmp_path),
        "case_id": "sess-2",
        "use_separate_workbook": True,
        "separate_workbook_suffix": "FTE",
    }
    out_path = Path(build_output_file_path(cfg))
    out_path.write_bytes(b"")
    found = find_session_output_file(cfg)
    assert found == str(out_path.resolve())
