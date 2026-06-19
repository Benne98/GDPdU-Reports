"""Tests for snapshot-first narrative resolution."""
from unittest.mock import MagicMock, patch

from app.services.fin_compat_narrative_resolve import resolve_statement_narrative


def test_resolve_returns_cached_without_rebuild():
    session = MagicMock()
    cached = {
        "intro": "Cached intro",
        "bullets": [{"index": 1, "text": "A"}, {"index": 2, "text": "B"}],
        "meta": {"algorithm_version": "wc_narrative_compat_v2", "cache_hit": True},
    }
    with patch(
        "app.services.fin_compat_narrative_resolve.get_cached_snapshot",
        return_value=cached,
    ):
        out = resolve_statement_narrative(session, "wc", 2025, 7, None)
    assert out is not None
    assert out["intro"] == "Cached intro"
    assert len(out["bullets"]) == 2


def test_resolve_cold_cache_returns_none_when_rebuild_disabled(monkeypatch):
    monkeypatch.delenv("OVERVIEW_NARRATIVE_ALLOW_REBUILD", raising=False)
    session = MagicMock()
    with patch(
        "app.services.fin_compat_narrative_resolve.get_cached_snapshot",
        return_value=None,
    ):
        out = resolve_statement_narrative(session, "wc", 2025, 7, None, force_refresh=False)
    assert out is None


def test_resolve_trims_bullets_when_requested():
    session = MagicMock()
    cached = {
        "intro": "x",
        "bullets": [{"index": i, "text": f"b{i}"} for i in range(1, 6)],
        "meta": {},
    }
    with patch(
        "app.services.fin_compat_narrative_resolve.get_cached_snapshot",
        return_value=cached,
    ):
        out = resolve_statement_narrative(session, "pl", 2025, 7, None, max_bullets=2)
    assert out is not None
    assert len(out["bullets"]) == 2
