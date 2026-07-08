"""Prong A — P&L structure grain-filter realign (derivation unit tests).

DB-free / pure.  Locks the seam contract shared with the reader-side engineer:
each eligible P&L mapping row is re-pinned to the SHALLOWEST grain level at which its
category value currently appears in dim_gl_account (level_0='PL'), clearing the other
two level filters.  No financial NUMBER changes here — this re-pins a classification
FILTER — but a wrong pin makes a whole P&L position render 0, so it is locked with a
formula + worked example + edge cases (docs/financial-logic.md).
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import logging

from scripts.realign_pl_structure import (
    build_value_level_map,
    realign_pl_structure,
    realign_rows,
)


def _row(line_code, *, level_2=None, level_3=None, level_4=None,
         row_type="mapping", gl_account_id=None, source="seed", pl_line_id=1):
    return {
        "pl_line_id": pl_line_id, "line_code": line_code, "row_type": row_type,
        "level_2": level_2, "level_3": level_3, "level_4": level_4,
        "gl_account_id": gl_account_id, "source": source,
    }


# --------------------------------------------------------------------------- #
# build_value_level_map
# --------------------------------------------------------------------------- #
class TestBuildValueLevelMap:
    def test_collects_levels_per_value(self):
        coa = [
            ("Income", "Net sales", None),
            (None, "Net sales", "Net sales"),          # same name at l3 AND l4
            ("Cost of materials", "Raw materials", None),
        ]
        vm = build_value_level_map(coa)
        assert vm["Net sales"] == {"level_3", "level_4"}
        assert vm["Cost of materials"] == {"level_2"}
        assert vm["Raw materials"] == {"level_3"}
        assert vm["Income"] == {"level_2"}

    def test_blank_and_null_ignored(self):
        vm = build_value_level_map([(None, "", "   "), ("A", None, None)])
        assert vm == {"A": {"level_2"}}


# --------------------------------------------------------------------------- #
# realign_rows — the derivation
# --------------------------------------------------------------------------- #
class TestRealignRows:
    def test_a_v5_convention_is_noop(self):
        # Category sits at grain level_3 (v5) and the row is already pinned level_3.
        vm = {"Cost of materials": {"level_3"}}
        rows = [_row("COST_OF_MATERIALS", level_3="Cost of materials")]
        assert realign_rows(rows, vm) == []

    def test_a2_same_name_l3_l4_shallowest_is_noop(self):
        # The benign v5 ambiguity: value at level_3 AND level_4 → shallowest=level_3
        # == current filter → no-op (confirmed 8 such values on v5).
        vm = {"Net sales": {"level_3", "level_4"}}
        rows = [_row("NET_SALES", level_3="Net sales")]
        assert realign_rows(rows, vm) == []

    def test_b_shifted_coa_moves_filter_to_level_2(self):
        # Post round-trip the category shifted UP to grain level_2.
        vm = {"Cost of materials": {"level_2"}}
        rows = [_row("COST_OF_MATERIALS", level_3="Cost of materials")]
        out = realign_rows(rows, vm)
        assert len(out) == 1
        u = out[0]
        assert u["line_code"] == "COST_OF_MATERIALS"
        assert (u["level_2"], u["level_3"], u["level_4"]) == ("Cost of materials", None, None)

    def test_c_ambiguity_picks_shallowest_and_would_change(self):
        # Value now at BOTH level_2 and level_3; current pin is level_3 → shallowest
        # level_2 differs → a change is emitted.
        vm = {"Net sales": {"level_2", "level_3"}}
        rows = [_row("NET_SALES", level_3="Net sales")]
        out = realign_rows(rows, vm)
        assert len(out) == 1
        assert (out[0]["level_2"], out[0]["level_3"], out[0]["level_4"]) == ("Net sales", None, None)

    def test_d_absent_value_leaves_row_unchanged(self):
        # 'Own work capitalised' is not in the uploaded CoA → honest empty position.
        vm = {"Cost of materials": {"level_2"}}
        rows = [_row("OWN_WORK_CAPITALISED", level_3="Own work capitalised")]
        assert realign_rows(rows, vm) == []

    def test_d2_level_1_only_value_left_unchanged(self):
        vm: dict[str, set[str]] = {}   # value not expressible at l2/l3/l4
        rows = [_row("SOME_L1", level_3="Group total")]
        assert realign_rows(rows, vm, level_1_values={"Group total"}) == []

    def test_e_auto_extend_untouched(self):
        vm = {"Crypto trading gains": {"level_2"}}
        rows = [_row("CRYPTO", level_3="Crypto trading gains", source="auto_extend")]
        assert realign_rows(rows, vm) == []

    def test_e2_non_mapping_rows_untouched(self):
        vm = {"Whatever": {"level_2"}}
        rows = [
            _row("GROSS_PROFIT", row_type="subtotal"),
            _row("EBITDA", row_type="calc"),
            _row("TOTAL", row_type="grandtotal"),
        ]
        assert realign_rows(rows, vm) == []

    def test_e3_rows_with_gl_account_id_untouched(self):
        vm = {"X": {"level_2"}}
        rows = [_row("ACCT", level_3="X", gl_account_id="8400")]
        assert realign_rows(rows, vm) == []

    def test_source_none_is_eligible(self):
        vm = {"Cost of materials": {"level_2"}}
        rows = [_row("COST", level_3="Cost of materials", source=None)]
        assert len(realign_rows(rows, vm)) == 1

    def test_idempotent_second_pass_is_noop(self):
        vm = {"Cost of materials": {"level_2"}}
        rows = [_row("COST", level_3="Cost of materials")]
        first = realign_rows(rows, vm)
        assert len(first) == 1
        # apply the pin, then re-run: no further change.
        u = first[0]
        rows2 = [_row("COST", level_2=u["level_2"], level_3=u["level_3"], level_4=u["level_4"])]
        assert realign_rows(rows2, vm) == []

    # ----- Major 2: multi-value row collision (two source values → one target) ----- #
    def test_f_two_values_same_target_level_left_unchanged(self, caplog):
        # Legacy convention: level_2=parent group + level_3=category. If BOTH resolve to
        # the SAME target grain level, writing the second would silently overwrite the
        # first → mis-pin. Conservative policy: leave the row UNCHANGED and warn.
        vm = {"Parent": {"level_2"}, "Category": {"level_2"}}
        rows = [_row("DUP", level_2="Parent", level_3="Category")]
        with caplog.at_level(logging.WARNING):
            out = realign_rows(rows, vm)
        assert out == []
        assert any(
            "DUP" in rec.getMessage() and "both resolve" in rec.getMessage()
            for rec in caplog.records
        ), caplog.text

    def test_g_two_values_different_levels_no_loss(self):
        # Both values survive when they resolve to DISTINCT target levels: no overwrite.
        # Row carries level_3='A' + level_4='B'; A now sits at level_2, B at level_3.
        vm = {"A": {"level_2"}, "B": {"level_3"}}
        rows = [_row("PARENTCAT", level_3="A", level_4="B")]
        out = realign_rows(rows, vm)
        assert len(out) == 1
        u = out[0]
        # Both values preserved, each pinned at its own target level — nothing lost.
        assert (u["level_2"], u["level_3"], u["level_4"]) == ("A", "B", None)


# --------------------------------------------------------------------------- #
# realign_pl_structure — DB wrapper UPDATE grain (Major 1: NULL pl_line_id)
# --------------------------------------------------------------------------- #
class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeRow:
    def __init__(self, mapping):
        self._mapping = mapping


class _FakeSession:
    """Minimal SQLAlchemy-session stand-in: routes the fixed queries and captures UPDATEs."""

    def __init__(self, coa, l1, struct, has_source=True):
        self._coa = coa
        self._l1 = l1
        self._struct = struct
        self._has_source = has_source
        self.updates: list[tuple[str, dict]] = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "information_schema.columns" in sql:
            return _FakeResult([(1,)] if self._has_source else [])
        if "SELECT DISTINCT level_2, level_3, level_4" in sql:
            return _FakeResult(self._coa)
        if "SELECT DISTINCT level_1" in sql:
            return _FakeResult(self._l1)
        if sql.lstrip().startswith("SELECT pl_line_id"):
            return _FakeResult([_FakeRow(d) for d in self._struct])
        if sql.lstrip().startswith("UPDATE"):
            self.updates.append((sql, params or {}))
            return _FakeResult([])
        return _FakeResult([])


class TestRealignPlStructureUpdateGrain:
    def test_null_pl_line_id_uses_line_code_fallback(self):
        # A re-pinnable row whose primary key is NULL. `WHERE pl_line_id = NULL` never
        # matches in Postgres → the UPDATE must fall back to the unique line_code.
        struct = [{
            "pl_line_id": None, "line_code": "COST", "row_type": "mapping",
            "level_2": None, "level_3": "Cost of materials", "level_4": None,
            "gl_account_id": None, "source": "seed",
        }]
        coa = [("Cost of materials", None, None)]   # value now at level_2 → re-pin
        session = _FakeSession(coa=coa, l1=[], struct=struct)
        n = realign_pl_structure(session, scope=None)
        assert n == 1
        assert len(session.updates) == 1
        sql, params = session.updates[0]
        assert "line_code = :lc" in sql
        assert "pl_line_id" not in sql
        assert params["lc"] == "COST"
        assert (params["l2"], params["l3"], params["l4"]) == ("Cost of materials", None, None)

    def test_present_pl_line_id_uses_primary_key(self):
        # Sanity: when the PK is present the fast path (WHERE pl_line_id) is used.
        struct = [{
            "pl_line_id": 42, "line_code": "COST", "row_type": "mapping",
            "level_2": None, "level_3": "Cost of materials", "level_4": None,
            "gl_account_id": None, "source": "seed",
        }]
        coa = [("Cost of materials", None, None)]
        session = _FakeSession(coa=coa, l1=[], struct=struct)
        n = realign_pl_structure(session, scope=None)
        assert n == 1
        sql, params = session.updates[0]
        assert "pl_line_id = :id" in sql
        assert params["id"] == 42
