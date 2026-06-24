"""Tests for the anomaly snapshot cache helpers (anomaly rework, Phase 3).

DB-FREE: a tiny fake session records the SQL it is asked to run and returns canned
rows, so the TTL/idempotency/scope logic is exercised without Postgres.  The
``entity_scope_key`` ordering tests are pure.
"""
from __future__ import annotations

import json

import pytest

from app.services import anomaly_snapshot_cache as cache


# --------------------------------------------------------------------------- #
# entity_scope_key — deterministic, order-independent, no cross-tenant mixing
# --------------------------------------------------------------------------- #
def test_entity_scope_key_none_and_empty_is_consolidated():
    assert cache.entity_scope_key(None) == ""
    assert cache.entity_scope_key([]) == ""


def test_entity_scope_key_sorts_and_dedupes():
    assert cache.entity_scope_key(["02", "01"]) == "01|02"
    assert cache.entity_scope_key(["01", "01"]) == "01"
    # order does not matter — same set → same key
    assert cache.entity_scope_key(["03", "01", "02"]) == cache.entity_scope_key(
        ["02", "03", "01"]
    )


def test_entity_scope_key_truncates_to_two_chars_and_strips():
    assert cache.entity_scope_key([" 01xx ", "02"]) == "01|02"


# --------------------------------------------------------------------------- #
# Fake session
# --------------------------------------------------------------------------- #
class _Row:
    def __init__(self, mapping):
        self._mapping = mapping


class _FakeSession:
    """Minimal Session stub: returns canned rows keyed by SQL substring."""

    def __init__(self, *, table_exists=True, select_row=None, raise_on_insert=False):
        self.table_exists = table_exists
        self.select_row = select_row
        self.raise_on_insert = raise_on_insert
        self.executed: list[tuple[str, dict]] = []
        self.committed = False
        self.rolled_back = False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.executed.append((sql, params or {}))
        if "information_schema.tables" in sql:
            return _Result(_Row({"ok": 1}) if self.table_exists else None)
        if sql.strip().upper().startswith("INSERT") or "INSERT INTO" in sql:
            if self.raise_on_insert:
                raise RuntimeError("boom")
            return _Result(None)
        if "SELECT payload_json" in sql:
            return _Result(self.select_row)
        return _Result(None)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


# --------------------------------------------------------------------------- #
# get_cached
# --------------------------------------------------------------------------- #
def test_get_cached_returns_none_when_table_missing():
    sess = _FakeSession(table_exists=False)
    assert cache.get_cached(sess, "outliers", "", "v1") is None


def test_get_cached_returns_none_for_unknown_type():
    sess = _FakeSession()
    assert cache.get_cached(sess, "bogus", "", "v1") is None


def test_get_cached_returns_none_on_no_row():
    sess = _FakeSession(select_row=None)
    assert cache.get_cached(sess, "outliers", "", "v1") is None


def test_get_cached_returns_payload_dict_on_hit():
    payload = {"analysis": "outliers", "tree": [1, 2, 3]}
    sess = _FakeSession(select_row=_Row({"payload_json": payload, "computed_at": "x"}))
    got = cache.get_cached(sess, "outliers", "", "v1")
    assert got == payload


def test_get_cached_parses_json_string_payload():
    payload = {"analysis": "forensic", "x": 1}
    sess = _FakeSession(
        select_row=_Row({"payload_json": json.dumps(payload), "computed_at": "x"})
    )
    assert cache.get_cached(sess, "forensic", "01|02", "fv1") == payload


def test_get_cached_passes_ttl_to_sql():
    sess = _FakeSession(select_row=None)
    cache.get_cached(sess, "outliers", "", "v1", ttl_seconds=3600)
    select_calls = [p for s, p in sess.executed if "SELECT payload_json" in s]
    assert select_calls and select_calls[0]["ttl"] == 3600.0
    # the freshness predicate is in the SQL (TTL checked read-side)
    select_sql = [s for s, _ in sess.executed if "SELECT payload_json" in s][0]
    assert "make_interval" in select_sql and "computed_at" in select_sql


# --------------------------------------------------------------------------- #
# save
# --------------------------------------------------------------------------- #
def test_save_rejects_unknown_type():
    sess = _FakeSession()
    with pytest.raises(ValueError):
        cache.save(sess, "bogus", "", "v1", {"a": 1})


def test_save_upserts_and_commits():
    sess = _FakeSession()
    cache.save(sess, "overview", "01", "ov1", {"cards": []})
    insert_calls = [(s, p) for s, p in sess.executed if "INSERT INTO" in s]
    assert insert_calls, "expected an INSERT"
    sql, params = insert_calls[0]
    assert "ON CONFLICT" in sql and "DO UPDATE" in sql
    assert params["atype"] == "overview"
    assert params["scope"] == "01"
    assert params["algo"] == "ov1"
    assert json.loads(params["payload"]) == {"cards": []}
    assert sess.committed is True
