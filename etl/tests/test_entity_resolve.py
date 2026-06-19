"""Tests for etl/entity_resolve.py."""
from __future__ import annotations

import pandas as pd
import pytest

from etl.entity_resolve import (
    collect_entity_labels,
    is_numeric_prefix_value,
    preview_entity_mappings,
    propose_prefixes_for_labels,
    resolve_label_to_prefix,
)


def test_is_numeric_prefix_value():
    assert is_numeric_prefix_value("1")
    assert is_numeric_prefix_value("01")
    assert not is_numeric_prefix_value("Atlas")


def test_resolve_label_existing_and_numeric():
    lookup = {"Atlas": "01", "Meridian": "02"}
    assert resolve_label_to_prefix("Atlas", lookup) == "01"
    assert resolve_label_to_prefix("1", lookup) == "01"
    assert resolve_label_to_prefix("NewCo", lookup) is None
    assert resolve_label_to_prefix("NewCo", lookup, {"NewCo": "03"}) == "03"


def test_propose_prefixes_skips_taken():
    proposed = propose_prefixes_for_labels(["Alpha", "Beta"], {"01", "02"})
    assert proposed == {"Alpha": "03", "Beta": "04"}


def test_collect_entity_labels_from_column():
    df = pd.DataFrame({"Entity": ["Atlas", "Atlas", "Meridian"]})
    labels = collect_entity_labels(df, {"mode": "column", "value": "Entity"})
    assert labels == ["Atlas", "Meridian"]


class _Session:
    def execute(self, stmt, params=None):
        sql = str(stmt)

        class _R:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows

        if "legal_entity_code" in sql:
            return _R([("Atlas", "Atlas GmbH", "01")])
        if "SELECT entity_prefix FROM dim_legal_entity" in sql:
            return _R([("01",)])
        return _R([])


def test_preview_entity_mappings_existing_and_proposed():
    stats = preview_entity_mappings(_Session(), ["Atlas", "NewCo"])
    assert stats["all_resolved"] is False
    assert stats["needs_confirmation"] is True
    by_label = {m["source_label"]: m for m in stats["mappings"]}
    assert by_label["Atlas"]["status"] == "existing"
    assert by_label["Atlas"]["entity_prefix"] == "01"
    assert by_label["NewCo"]["status"] == "proposed"
    assert by_label["NewCo"]["entity_prefix"] == "02"
