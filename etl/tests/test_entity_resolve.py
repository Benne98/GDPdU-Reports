"""Tests for etl/entity_resolve.py."""
from __future__ import annotations

import pandas as pd
import pytest

from etl.entity_resolve import (
    collect_entity_labels,
    is_numeric_prefix_value,
    prefix_series_from_entity_config,
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


# --------------------------------------------------------------------------- #
# prefix_series_from_entity_config — drop_unknown kwarg
# --------------------------------------------------------------------------- #

def test_prefix_series_drop_unknown_true_yields_na_for_unknown():
    """drop_unknown=True: resolved labels get a prefix; unresolved labels get NA.

    This is the OB-path contract: the caller can then filter NA rows and drop
    them rather than blocking the whole commit.
    """
    df = pd.DataFrame({"E": ["Atlas", "Meridian"]})
    lookup = {"Atlas": "01"}
    s = prefix_series_from_entity_config(
        df, {"mode": "column", "value": "E"}, lookup, drop_unknown=True
    )
    assert s.iloc[0] == "01", f"Atlas should resolve to '01'; got {s.iloc[0]!r}"
    assert pd.isna(s.iloc[1]), f"Meridian (unknown) should be NA; got {s.iloc[1]!r}"


def test_prefix_series_drop_unknown_false_raises_on_unknown():
    """drop_unknown=False (GL default): any unresolved label raises ValueError.

    The error message must match 'Unknown entities' so the router converts it
    to HTTP 422 with a user-readable message.
    """
    df = pd.DataFrame({"E": ["Atlas", "Meridian"]})
    lookup = {"Atlas": "01"}
    with pytest.raises(ValueError, match="Unknown entities"):
        prefix_series_from_entity_config(
            df, {"mode": "column", "value": "E"}, lookup, drop_unknown=False
        )
