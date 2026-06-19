"""Tests for mapping_editor service — synthetic in-memory style with fake session."""
from __future__ import annotations

import pytest

from app.services.mapping_editor import (
    SORT_BY_LEVEL,
    build_structure_tree,
    reorder_structure_siblings,
)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    @property
    def rowcount(self):
        return len(self._rows)


class _FakeSession:
    def __init__(self, accounts: list[dict]):
        self.accounts = accounts
        self.updates: list[tuple] = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        params = params or {}
        if "FROM dim_gl_account" in sql and "UPDATE" not in sql:
            rows = []
            for a in self.accounts:
                if a["fiscal_year"] != params.get("fy"):
                    continue
                if "l0" in params and a.get("level_0") != params["l0"]:
                    continue
                rows.append(_Row(a))
            return _FakeResult(rows)
        if "UPDATE dim_gl_account" in sql and "SET" in sql:
            self.updates.append((sql, dict(params)))
            return _FakeResult([1])
        if "FROM dim_pl_structure" in sql:
            return _FakeResult([])
        return _FakeResult([])


class _Row:
    def __init__(self, data: dict):
        self._mapping = data


def _sample_accounts():
    return [
        {
            "account_number_group": "01010000",
            "fiscal_year": 2024,
            "gl_account_id": "10000",
            "account_name": "Cash",
            "level_0": "BS",
            "level_1": "Assets",
            "level_2": "Current",
            "level_3": "Cash",
            "level_4": "",
            "level_1_sort": 10,
            "level_2_sort": 20,
            "level_3_sort": 30,
            "level_4_sort": None,
        },
        {
            "account_number_group": "01020000",
            "fiscal_year": 2024,
            "gl_account_id": "20000",
            "account_name": "AR",
            "level_0": "BS",
            "level_1": "Assets",
            "level_2": "Current",
            "level_3": "Receivables",
            "level_4": "",
            "level_1_sort": 10,
            "level_2_sort": 20,
            "level_3_sort": 10,
            "level_4_sort": None,
        },
    ]


def test_build_structure_tree_groups_bs():
    session = _FakeSession(_sample_accounts())
    tree = build_structure_tree(session, fiscal_year=2024, statement="BS")
    assert len(tree) == 1
    assert tree[0]["label"] == "Assets"
    l3 = tree[0]["children"][0]["children"]
    labels = [n["label"] for n in l3]
    assert labels == ["Receivables", "Cash"]


def test_reorder_requires_sortable_level():
    session = _FakeSession(_sample_accounts())
    with pytest.raises(ValueError):
        reorder_structure_siblings(
            session,
            fiscal_year=2024,
            statement="BS",
            parent_path={"level_0": "BS", "level_1": "Assets", "level_2": "Current"},
            level_key="level_0",
            ordered_labels=["BS"],
        )


def test_sort_columns_cover_l2_to_l5():
    assert "level_1" in SORT_BY_LEVEL
    assert "level_4" in SORT_BY_LEVEL
