"""BS structure re-seed idempotency after a CoA round-trip (Deliverable 3).

Root cause under test: ``dim_bs_structure`` has TWO unique constraints — ``line_code``
AND ``sort_order``.  A plain ``ON CONFLICT (line_code) DO UPDATE`` re-seed raises
``UniqueViolation`` when a *different* line_code recomputes to a ``sort_order`` already
occupied (the conflict target can't catch a sort_order clash).  ``seed_bs_structure``
now DELETE-then-INSERTs (preserving ``source='auto_extend'`` rows) and skips the
sort_orders those surviving rows still occupy.

No financial number changes — this is a structure (classification/presentation)
re-seed; the guarantee under test is: re-seed after a hierarchy reorder does NOT raise,
seed rows are re-created, and user-placed ``auto_extend`` rows survive untouched.

The fake session enforces BOTH unique constraints so "no UniqueViolation" is a real
assertion (a plain insert onto an occupied sort_order raises).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scripts.seed_bs_structure import seed_bs_structure, build_bs_rows


class FakeUniqueViolation(Exception):
    """Stands in for psycopg2.errors.UniqueViolation on the fake session."""


class _Result:
    def __init__(self, fetchone=None, fetchall=None):
        self._one = fetchone
        self._all = fetchall or []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


class _FakeBsSession:
    """Stateful fake enforcing UNIQUE(line_code) AND UNIQUE(sort_order) on
    dim_bs_structure, plus the GL hierarchy + information_schema probe used by
    seed_bs_structure."""

    def __init__(self, hierarchy, *, has_source=True, rows=None):
        self.hierarchy = list(hierarchy)          # (l1, l2, l3, l2s, l3s) tuples
        self.has_source = has_source
        self.rows: list[dict] = [dict(r) for r in (rows or [])]
        self.committed = False

    # -- lookups ------------------------------------------------------------
    def _by_lc(self, lc):
        for r in self.rows:
            if r["line_code"] == lc:
                return r
        return None

    def execute(self, stmt, params=None):
        sql = str(stmt)
        params = params or {}

        if "information_schema.columns" in sql:
            return _Result(fetchone=(1,) if self.has_source else None)

        if sql.strip().startswith("DELETE FROM dim_bs_structure"):
            if "COALESCE(source" in sql:
                self.rows = [r for r in self.rows if r.get("source") == "auto_extend"]
            else:
                self.rows = []
            return _Result()

        if "SELECT sort_order FROM dim_bs_structure" in sql:
            return _Result(fetchall=[(r["sort_order"],) for r in self.rows])

        if "FROM dim_gl_account" in sql:
            return _Result(fetchall=list(self.hierarchy))

        if sql.strip().startswith("INSERT INTO dim_bs_structure"):
            lc, so = params["lc"], params["so"]
            existing = self._by_lc(lc)
            # UNIQUE(sort_order): a DIFFERENT line_code already holding `so` is a clash.
            clash = any(r["sort_order"] == so and r["line_code"] != lc for r in self.rows)
            if clash:
                raise FakeUniqueViolation(
                    f"duplicate key value violates unique constraint (sort_order)={so}"
                )
            if existing:  # ON CONFLICT (line_code) DO UPDATE
                existing.update({"sort_order": so, "row_type": params["rt"],
                                 "balance_title": params["bt"], "kpi_code": params["kpi"],
                                 "level_2": params["l2"], "level_3": params["l3"]})
            else:
                self.rows.append({
                    "line_code": lc, "sort_order": so, "row_type": params["rt"],
                    "balance_title": params["bt"], "kpi_code": params["kpi"],
                    "level_2": params["l2"], "level_3": params["l3"],
                    "source": "seed" if self.has_source else None,
                })
            return _Result()

        return _Result()

    def commit(self):
        self.committed = True


# Hierarchy A: Assets side with three L3 categories (→ mapping rows at 1010/1020/1030).
_HIER_A = [
    ("Assets", "Current assets", "Trade receivables", 1.0, 1.0),
    ("Assets", "Current assets", "Inventories", 1.0, 2.0),
    ("Assets", "Current assets", "Cash & cash equivalents", 1.0, 3.0),
]
# Hierarchy B: the SAME categories, REORDERED (Cash first) — so a different line_code
# recomputes onto sort_order 1010 that A's first category held.
_HIER_B = [
    ("Assets", "Current assets", "Cash & cash equivalents", 1.0, 1.0),
    ("Assets", "Current assets", "Inventories", 1.0, 2.0),
    ("Assets", "Current assets", "Trade receivables", 1.0, 3.0),
]


def _mapping_rows(session):
    return [r for r in session.rows if r["row_type"] == "mapping"]


class TestReseedAfterReorder:
    def test_reseed_after_reorder_no_unique_violation(self):
        # Seed A.
        s = _FakeBsSession(_HIER_A)
        n1 = seed_bs_structure(s)
        assert n1 > 0
        first_after_a = min(_mapping_rows(s), key=lambda r: r["sort_order"])
        assert first_after_a["level_3"] == "Trade receivables"  # A's first at 1010

        # Re-seed with the reordered hierarchy — must NOT raise UniqueViolation.
        s.hierarchy = _HIER_B
        seed_bs_structure(s)   # would raise on the old ON CONFLICT(line_code)-only path
        first_after_b = min(_mapping_rows(s), key=lambda r: r["sort_order"])
        assert first_after_b["level_3"] == "Cash & cash equivalents"  # B's first now at 1010
        # Seed rows re-created for every B category.
        got = {r["level_3"] for r in _mapping_rows(s)}
        assert got == {"Trade receivables", "Inventories", "Cash & cash equivalents"}

    def test_reseed_is_idempotent(self):
        s = _FakeBsSession(_HIER_A)
        seed_bs_structure(s)
        sig1 = sorted((r["line_code"], r["sort_order"]) for r in s.rows)
        seed_bs_structure(s)
        sig2 = sorted((r["line_code"], r["sort_order"]) for r in s.rows)
        assert sig1 == sig2


class TestAutoExtendSurvives:
    def test_auto_extend_row_survives_and_sort_order_skipped(self):
        # A user-placed auto_extend row occupies 1010 — the slot the re-seed wants first.
        auto = {"line_code": "BS_CUSTOM_POSITION", "sort_order": 1010,
                "row_type": "mapping", "balance_title": "Custom", "kpi_code": "BS:asset",
                "level_2": "Current assets", "level_3": "Custom position",
                "source": "auto_extend"}
        s = _FakeBsSession(_HIER_A, rows=[auto])
        seed_bs_structure(s)   # occupied={1010} → fresh rows must start at 1020

        surviving = s._by_lc("BS_CUSTOM_POSITION")
        assert surviving is not None
        assert surviving["sort_order"] == 1010            # untouched
        assert surviving["source"] == "auto_extend"
        # No freshly-seeded row landed on the occupied slot.
        fresh = [r for r in s.rows if r["source"] != "auto_extend"]
        assert fresh and all(r["sort_order"] != 1010 for r in fresh)
        assert min(r["sort_order"] for r in fresh) == 1020

    def test_no_source_column_deletes_all_and_reseeds(self):
        # Legacy schema without a `source` column: DELETE all, re-seed cleanly.
        s = _FakeBsSession(_HIER_A, has_source=False,
                           rows=[{"line_code": "STALE", "sort_order": 1010,
                                  "row_type": "mapping", "balance_title": "x",
                                  "kpi_code": "BS:asset", "level_2": "y",
                                  "level_3": "z", "source": None}])
        seed_bs_structure(s)
        assert s._by_lc("STALE") is None                  # legacy row cleared
        assert _mapping_rows(s)                            # fresh rows exist


class TestBuildBsRowsOccupied:
    def test_nxt_skips_occupied_sort_orders(self):
        rows = build_bs_rows(_HIER_A, occupied={1010, 1020})
        sorts = [r.sort_order for r in rows]
        assert 1010 not in sorts and 1020 not in sorts
        assert sorts == sorted(sorts)                     # still monotonic

    def test_default_occupied_none_is_backward_compatible(self):
        rows = build_bs_rows(_HIER_A)
        first_mapping = next(r for r in rows if r.row_type == "mapping")
        assert first_mapping.sort_order == 1010
