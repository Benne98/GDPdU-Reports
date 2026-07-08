"""
Direct unit tests for the grain-relative P&L reader-side helpers in
``app.services.fin_compat_pl`` (Prong A read-side + Prong B zero-exclusion).

Context: these helpers were previously covered only indirectly via the
builder-level integration tests in ``test_compat_layer.py``.  Hard-Rule #1
(financial logic needs a direct proof) requires direct unit coverage, so this
module exercises each helper in isolation with tiny synthetic grains — NO real
client data, no DB.  These tests assert the SEAM CONTRACT documented above
``matched_level`` in fin_compat_pl.py; they do not change any behavior.

Helpers under test:
  * matched_level              — shallowest/deepest populated grain level (M)
  * _pl_child_grouping         — grain-relative child bucketing (child_lvl = M+1)
  * _pl_hierarchy_children     — child expansion + single-child==title hoist
  * _exclude_zero_rows         — Prong B all-zero 'line' + empty kpi_header prune
  * _accounts_under            — account leaves under a level filter (dedup/zero)

Uses the synthetic-grain idioms from test_compat_layer.py (plain dicts).
"""
from __future__ import annotations

from app.services.fin_compat_pl import (
    _accounts_under,
    _exclude_zero_rows,
    _pl_child_grouping,
    _pl_hierarchy_children,
    matched_level,
)

# ---------------------------------------------------------------------------
# Synthetic-grain helpers (mirror test_compat_layer.py idioms)
# ---------------------------------------------------------------------------

_KEYS = ["cm"]


def _amounts_fn(g: dict) -> dict:
    return {"cm": float(g.get("cm") or 0.0)}


def _sort_key(am: dict) -> float:
    return abs(float(am.get("cm") or 0.0))


def _grain(level_2=None, level_3=None, level_4=None, *, ang=None,
           gl_account_id=None, account_name=None, cm=0.0) -> dict:
    return {
        "level_2": level_2, "level_3": level_3, "level_4": level_4,
        "gl_account_id": gl_account_id,
        "account_number_group": ang,
        "account_name": account_name,
        "cm": cm,
    }


def _srow(*, level_2=None, level_3=None, level_4=None,
          gl_account_id=None, balance_title="Row") -> dict:
    """A minimal dim_pl_structure mapping-row dict."""
    return {
        "level_2": level_2, "level_3": level_3, "level_4": level_4,
        "gl_account_id": gl_account_id, "balance_title": balance_title,
    }


# ---------------------------------------------------------------------------
# (1) matched_level(row)
# ---------------------------------------------------------------------------

class TestMatchedLevel:
    """M = shallowest re-pinned grain level; account-pinned / empty → None."""

    def test_account_pinned_returns_none(self):
        """gl_account_id set → None (account leaf, no level expansion)."""
        assert matched_level(_srow(level_3="Net sales", gl_account_id="AT40001")) is None

    def test_all_empty_returns_none(self):
        """No level filter at all → None."""
        assert matched_level(_srow()) is None

    def test_repinned_single_filter_level_2(self):
        """Re-pinned single filter at level_2 (shifted e2e convention) → M == 2."""
        assert matched_level(_srow(level_2="Income")) == 2

    def test_repinned_single_filter_level_3(self):
        """Re-pinned single filter at level_3 → M == 3."""
        assert matched_level(_srow(level_3="Net sales")) == 3

    def test_legacy_level_2_plus_level_3_returns_3(self):
        """Legacy v5 convention: level_2 parent + level_3 value both set →
        deepest populated is level_3 → M == 3 (children still level_4)."""
        assert matched_level(_srow(level_2="Income", level_3="Net sales")) == 3

    def test_level_4_returns_4(self):
        """Deepest populated level_4 → M == 4 (no further expansion downstream)."""
        assert matched_level(
            _srow(level_2="Income", level_3="Net sales", level_4="Domestic")
        ) == 4


# ---------------------------------------------------------------------------
# (2) _pl_child_grouping — bucketing at child_lvl = M+1
# ---------------------------------------------------------------------------

class TestPlChildGrouping:

    def test_m3_buckets_at_level_4(self):
        """M == 3 → child_level == level_4; grains bucketed by their level_4 value."""
        grains = [
            _grain("Income", "Net sales", "Domestic", ang="AT4000", cm=100),
            _grain("Income", "Net sales", "Export", ang="AT4001", cm=300),
            _grain("Income", "Other", "X", ang="AT4009", cm=999),  # filtered out
        ]
        row = _srow(level_2="Income", level_3="Net sales", balance_title="Net sales")
        grouping = _pl_child_grouping(grains, row)
        assert grouping is not None
        assert grouping["matched_level"] == 3
        assert grouping["child_level"] == "level_4"
        assert grouping["filters"] == {"level_2": "Income", "level_3": "Net sales"}
        assert set(grouping["buckets"].keys()) == {"Domestic", "Export"}
        # 'Other' grain does not match the level_3 filter → excluded from matched.
        assert all(g["level_3"] == "Net sales" for g in grouping["matched"])

    def test_m2_buckets_at_level_3(self):
        """M == 2 → child_level == level_3; grains bucketed by their level_3 value."""
        grains = [
            _grain("Income", "Net sales", ang="AT4000", cm=100),
            _grain("Income", "Other income", ang="AT4800", cm=50),
            _grain("Expense", "Cost of materials", ang="AT5000", cm=-40),  # filtered
        ]
        row = _srow(level_2="Income", balance_title="Income")
        grouping = _pl_child_grouping(grains, row)
        assert grouping is not None
        assert grouping["matched_level"] == 2
        assert grouping["child_level"] == "level_3"
        assert grouping["filters"] == {"level_2": "Income"}
        assert set(grouping["buckets"].keys()) == {"Net sales", "Other income"}

    def test_level_4_pinned_no_expansion(self):
        """M == 4 → grouping is None (row does not expand)."""
        grains = [_grain("Income", "Net sales", "Domestic", ang="AT4000", cm=100)]
        row = _srow(level_2="Income", level_3="Net sales", level_4="Domestic")
        assert _pl_child_grouping(grains, row) is None

    def test_account_pinned_no_expansion(self):
        """gl_account_id set → grouping is None (account leaf)."""
        grains = [_grain("Income", "Net sales", ang="AT4000", cm=100)]
        row = _srow(level_3="Net sales", gl_account_id="AT40001")
        assert _pl_child_grouping(grains, row) is None


# ---------------------------------------------------------------------------
# (3) _pl_hierarchy_children — child expansion + hoist
# ---------------------------------------------------------------------------

def _children(grains, row, **overrides):
    kwargs = dict(
        keys=_KEYS, invert=False, rc="RC", balance_title=row["balance_title"],
        id_base="er-pl-RC", amounts_fn=_amounts_fn, sort_key=_sort_key,
    )
    kwargs.update(overrides)
    return _pl_hierarchy_children(grains, row, **kwargs)


class TestPlHierarchyChildren:

    def test_m3_children_at_level_4_sorted_desc(self):
        """M == 3 → detail children at level_4, ordered by sort_key DESC, each with
        account leaves; no hoist when no child label equals the parent title."""
        grains = [
            _grain("Income", "Net sales", "Domestic", ang="AT4000",
                   account_name="Rev DE", cm=100),
            _grain("Income", "Net sales", "Export", ang="AT4001",
                   account_name="Rev EX", cm=300),
        ]
        row = _srow(level_2="Income", level_3="Net sales",
                    balance_title="Net sales revenue")
        children, hoisted, hoist_drill = _children(grains, row)
        assert hoisted == [] and hoist_drill is None
        assert [c["label"] for c in children] == ["Export", "Domestic"]  # 300 > 100
        assert children[0]["amounts"]["cm"] == 300
        assert children[0]["drill"]["level_4"] == "Export"
        # Each child carries its account leaves.
        assert [a["line_code"] for a in children[0]["accounts"]] == ["AT4001"]

    def test_m2_children_at_level_3(self):
        """M == 2 → detail children bucketed at level_3."""
        grains = [
            _grain("Income", "Net sales", ang="AT4000", account_name="Rev", cm=100),
            _grain("Income", "Other income", ang="AT4800", account_name="Oth", cm=50),
        ]
        row = _srow(level_2="Income", balance_title="Income")
        children, hoisted, hoist_drill = _children(grains, row)
        assert hoisted == [] and hoist_drill is None
        assert [c["label"] for c in children] == ["Net sales", "Other income"]  # 100>50
        assert children[0]["drill"]["level_3"] == "Net sales"

    def test_single_child_equals_title_hoist(self):
        """Exactly one child whose label == parent balance_title → HOIST: the
        parent adopts the child's accounts + drill and the child collapses."""
        grains = [
            _grain("Income", "Net sales", "Domestic", ang="AT4000",
                   account_name="Rev", cm=100),
        ]
        row = _srow(level_2="Income", level_3="Net sales", balance_title="Domestic")
        children, hoisted, hoist_drill = _children(grains, row)
        assert children == []                       # child collapsed
        assert [a["line_code"] for a in hoisted] == ["AT4000"]  # accounts hoisted up
        assert hoist_drill is not None
        assert hoist_drill["level_4"] == "Domestic"  # parent adopts child's drill

    def test_level_4_pinned_no_expansion(self):
        """M == 4 → ([], [], None)."""
        grains = [_grain("Income", "Net sales", "Domestic", ang="AT4000", cm=100)]
        row = _srow(level_2="Income", level_3="Net sales", level_4="Domestic",
                    balance_title="Domestic")
        assert _children(grains, row) == ([], [], None)

    def test_account_pinned_no_expansion(self):
        """gl_account_id set → ([], [], None)."""
        grains = [_grain("Income", "Net sales", ang="AT4000", cm=100)]
        row = _srow(level_3="Net sales", gl_account_id="AT40001",
                    balance_title="Net sales")
        assert _children(grains, row) == ([], [], None)


# ---------------------------------------------------------------------------
# (4) _exclude_zero_rows — Prong B
# ---------------------------------------------------------------------------

def _row(row_kind, code, cm=0.0, ytd=0.0):
    return {"row_kind": row_kind, "line_code": code,
            "amounts": {"cm": cm, "ytd": ytd}}


class TestExcludeZeroRows:
    _KEYS = ["cm", "ytd"]

    def test_keeps_line_nonzero_in_any_period(self):
        """A 'line' row non-zero in ANY period column survives (zero cm, non-zero ytd)."""
        rows = [_row("line", "A", cm=0.0, ytd=5.0)]
        out = _exclude_zero_rows(rows, self._KEYS)
        assert [r["line_code"] for r in out] == ["A"]

    def test_drops_all_zero_line(self):
        """A 'line' row zero across EVERY period column is dropped."""
        rows = [_row("line", "A", cm=0.0, ytd=0.0), _row("line", "B", cm=1.0)]
        out = _exclude_zero_rows(rows, self._KEYS)
        assert [r["line_code"] for r in out] == ["B"]

    def test_never_drops_structural_rows_even_when_zero(self):
        """subtotal/calc/grandtotal/kpi rows are NEVER dropped even when all-zero
        (calc/grandtotal surface as row_kind 'subtotal'; kpi stays 'kpi')."""
        rows = [
            _row("subtotal", "SUB", cm=0.0, ytd=0.0),
            _row("grandtotal", "GT", cm=0.0, ytd=0.0),
            _row("kpi_header", "KH"),
            _row("kpi", "KPI", cm=0.0, ytd=0.0),
        ]
        out = _exclude_zero_rows(rows, self._KEYS)
        assert [r["line_code"] for r in out] == ["SUB", "GT", "KH", "KPI"]

    def test_prunes_kpi_header_with_no_surviving_kpi(self):
        """A kpi_header with no kpi row under it (all-zero line dropped, no kpi
        rows present) is pruned as an empty section header."""
        rows = [_row("line", "A", cm=0.0, ytd=0.0), _row("kpi_header", "KH")]
        out = _exclude_zero_rows(rows, self._KEYS)
        assert out == []  # line dropped → no kpi → header pruned too

    def test_keeps_kpi_header_when_kpi_survives(self):
        """The kpi_header stays when at least one kpi row is present (kpi kept even
        when zero) — the header is NOT pruned."""
        rows = [_row("kpi_header", "KH"), _row("kpi", "KPI", cm=0.0, ytd=0.0)]
        out = _exclude_zero_rows(rows, self._KEYS)
        assert [r["line_code"] for r in out] == ["KH", "KPI"]


# ---------------------------------------------------------------------------
# (5) _accounts_under — account leaves under a level filter
# ---------------------------------------------------------------------------

class TestAccountsUnder:

    def test_filter_dedup_and_zero_exclusion(self):
        """Keeps grains matching EVERY (level,value); dedups by
        account_number_group; drops all-zero accounts."""
        filters = {"level_2": "Income", "level_3": "Net sales"}
        grains = [
            # AT4000 appears twice → deduped/summed to 150.
            _grain("Income", "Net sales", ang="AT4000", account_name="Rev", cm=100),
            _grain("Income", "Net sales", ang="AT4000", account_name="Rev", cm=50),
            # All-zero account → excluded.
            _grain("Income", "Net sales", ang="AT4001", account_name="Zero", cm=0.0),
            # Fails the level_3 filter → excluded.
            _grain("Income", "Other", ang="AT4002", account_name="Other", cm=999),
        ]
        out = _accounts_under(
            grains, filters, False, keys=_KEYS,
            amounts_fn=_amounts_fn, sort_key=_sort_key,
        )
        assert [a["line_code"] for a in out] == ["AT4000"]  # dedup + only match
        assert out[0]["amounts"]["cm"] == 150               # 100 + 50
        assert out[0]["row_kind"] == "account"
        assert out[0]["drill"]["level_2"] == "Income"
        assert out[0]["drill"]["level_3"] == "Net sales"

    def test_empty_when_no_grain_matches(self):
        """No grain satisfies the full filter → empty leaf list."""
        filters = {"level_2": "Income", "level_3": "Net sales", "level_4": "Nope"}
        grains = [_grain("Income", "Net sales", "Domestic", ang="AT4000", cm=100)]
        out = _accounts_under(
            grains, filters, False, keys=_KEYS,
            amounts_fn=_amounts_fn, sort_key=_sort_key,
        )
        assert out == []
