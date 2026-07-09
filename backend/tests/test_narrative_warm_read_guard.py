"""Regression: the background narrative warmer must bound its heavy report reads.

Root cause of the recurring DB hang: ``warm_narratives_for_period`` runs in a
background daemon thread on its OWN raw session (no FastAPI ``get_read_session``
dependency).  Without arming the read guard, the balance-sheet ``bal_mov`` scans
it fires for every entity ran UNBOUNDED with parallel workers (statement_timeout=0),
piled up for hours and starved the concurrent GL-commit rebuild.

These tests pin the durable fix: the guard (SET LOCAL statement_timeout + serial
plan) is applied on the warmer's session, and RE-ARMED before every snapshot build
(``_build_and_store`` commits each snapshot, which resets ``SET LOCAL``).
"""
from __future__ import annotations

import contextlib

from app import db as db_mod
from app.services import narrative_warm


class _FakeSession:
    """Records the SQL text of every ``execute`` (params ignored) + txn ops."""

    def __init__(self) -> None:
        self.log: list[str] = []

    def execute(self, statement, params=None):  # noqa: ANN001
        self.log.append(str(statement))
        return self

    def rollback(self) -> None:
        self.log.append("ROLLBACK")

    def close(self) -> None:
        self.log.append("CLOSE")


def test_apply_read_guard_sets_timeout_and_serial_plan():
    sess = _FakeSession()
    db_mod.apply_read_guard(sess)
    joined = " ".join(sess.log)
    assert "statement_timeout" in joined
    assert "max_parallel_workers_per_gather" in joined


def test_warm_narratives_arms_guard_before_every_build(monkeypatch):
    """Each ``_build_and_store`` must be immediately preceded by the guard SQL."""
    sess = _FakeSession()

    @contextlib.contextmanager
    def _fake_scope():
        db_mod.apply_read_guard(sess)  # mirror read_session_scope's first arm
        try:
            yield sess
        finally:
            sess.close()

    monkeypatch.setattr(narrative_warm, "read_session_scope", _fake_scope)
    monkeypatch.setattr(narrative_warm, "tables_exist", lambda _s: True)

    def _fake_build_and_store(session, **kwargs):  # noqa: ANN001, ANN003
        # Marker so we can assert the guard was armed just before this call.
        session.log.append(f"BUILD:{kwargs.get('statement')}")
        return {"ok": True}

    monkeypatch.setattr(narrative_warm, "_build_and_store", _fake_build_and_store)

    stats = narrative_warm.warm_narratives_for_period(
        2025, 7, entities=[None], grains=("month",),
    )

    assert stats["ok"] == len(narrative_warm._STATEMENTS)

    # Every BUILD marker must have the two guard statements among the three
    # entries immediately before it (ROLLBACK, statement_timeout, serial plan).
    for i, entry in enumerate(sess.log):
        if entry.startswith("BUILD:"):
            window = " ".join(sess.log[max(0, i - 3):i])
            assert "statement_timeout" in window, sess.log[max(0, i - 3):i + 1]
            assert "max_parallel_workers_per_gather" in window, sess.log[max(0, i - 3):i + 1]
