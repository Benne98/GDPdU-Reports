"""P5 / C6 — GOLDEN tests for entity consolidation (app.services.consolidation).

Covers the PURE core ``consolidate_statements`` and the DB-backed orchestration
``build_consolidation`` via a fake session.  Asserts:
  • the consolidation formula  consolidated(L,C) = Σ_e value_e(L,C)  (Atlas 3000 +
    Meridian 2000 → 5000), with the documented 2-entity worked example,
  • single-entity → consolidated == that entity (identity edge case),
  • entity with no rows in a period → contributes 0,
  • ratio/derived lines are RECOMPUTED (consolidated margin from consolidated
    REVENUE/COGS), not summed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKEND = _REPO_ROOT / "backend"
for p in (str(_REPO_ROOT), str(_BACKEND)):
    if p not in sys.path:
        sys.path.insert(0, p)

import app.main as m
from app.auth import User, current_user
from app.db import get_session
from app.services import periods as P
from app.services.consolidation import consolidate_statements
from app.services.statements import StatementCell, StatementLine


# --------------------------------------------------------------------------- #
# Pure-core fixtures: two tiny PL statements (one per entity).
# --------------------------------------------------------------------------- #
def _pl_lines(rev: float, cogs: float, col: str = "FY") -> list[StatementLine]:
    gp = rev + cogs
    margin = (gp / rev * 100.0) if rev else None
    ebitda = rev + cogs
    return [
        StatementLine("REVENUE", "Revenue", "mapping", False, None, [StatementCell(col, rev)]),
        StatementLine("COGS", "Cost of materials", "mapping", False, None, [StatementCell(col, cogs)]),
        StatementLine("GROSS_PROFIT", "Gross profit", "calc", True, "GROSS_PROFIT", [StatementCell(col, gp)]),
        StatementLine("GROSS_MARGIN_PCT", "Gross margin %", "calc", False, "GROSS_MARGIN_PCT", [StatementCell(col, margin)]),
        StatementLine("EBITDA", "EBITDA", "calc", True, "EBITDA", [StatementCell(col, ebitda)]),
    ]


class _FakeStmt:
    def __init__(self, lines):
        self.lines = lines


def _cell(lines, code, col="FY"):
    ln = next(l for l in lines if l.line_code == code)
    return next(c for c in ln.cells if c.column_key == col).value


# =========================================================================== #
# Worked example — Atlas 3000 + Meridian 2000 → 5000
# =========================================================================== #
class TestConsolidationWorkedExample:
    def _result(self):
        atlas = ("01", "Atlas", _FakeStmt(_pl_lines(3000.0, -500.0)))
        meridian = ("02", "Meridian", _FakeStmt(_pl_lines(2000.0, -300.0)))
        return consolidate_statements(
            "pl", [atlas, meridian], ["FY"], {"FY": "FY 2025"}, "year", 1.0
        )

    def test_revenue_sums_to_5000(self):
        res = self._result()
        assert _cell(res.consolidated, "REVENUE") == pytest.approx(5000.0)

    def test_cogs_sums_to_negative_800(self):
        res = self._result()
        assert _cell(res.consolidated, "COGS") == pytest.approx(-800.0)

    def test_gross_profit_recomputed_4200(self):
        res = self._result()
        # 5000 + (-800) = 4200 (recomputed from consolidated bases, not summed GPs)
        assert _cell(res.consolidated, "GROSS_PROFIT") == pytest.approx(4200.0)

    def test_margin_recomputed_not_averaged(self):
        res = self._result()
        # Consolidated margin = 4200/5000*100 = 84.0  (NOT the average of 83.33 & 85.0)
        assert _cell(res.consolidated, "GROSS_MARGIN_PCT") == pytest.approx(84.0)

    def test_two_entity_blocks_present(self):
        res = self._result()
        assert [e.entity_prefix for e in res.entities] == ["01", "02"]
        assert _cell(res.entities[0].lines, "REVENUE") == pytest.approx(3000.0)
        assert _cell(res.entities[1].lines, "REVENUE") == pytest.approx(2000.0)

    def test_intercompany_not_eliminated_flag(self):
        assert self._result().intercompany_eliminated is False


# =========================================================================== #
# Edge cases
# =========================================================================== #
class TestConsolidationEdgeCases:
    def test_single_entity_equals_that_entity(self):
        atlas = ("01", "Atlas", _FakeStmt(_pl_lines(3000.0, -500.0)))
        res = consolidate_statements("pl", [atlas], ["FY"], {"FY": "FY"}, "year", 1.0)
        assert _cell(res.consolidated, "REVENUE") == pytest.approx(3000.0)
        assert _cell(res.consolidated, "COGS") == pytest.approx(-500.0)
        assert _cell(res.consolidated, "GROSS_MARGIN_PCT") == pytest.approx(2500 / 3000 * 100)

    def test_entity_with_no_rows_contributes_zero(self):
        # Empty entity → all cells 0 → consolidated == the populated entity.
        atlas = ("01", "Atlas", _FakeStmt(_pl_lines(3000.0, -500.0)))
        empty = ("02", "Meridian", _FakeStmt(_pl_lines(0.0, 0.0)))
        res = consolidate_statements("pl", [atlas, empty], ["FY"], {"FY": "FY"}, "year", 1.0)
        assert _cell(res.consolidated, "REVENUE") == pytest.approx(3000.0)

    def test_consolidated_zero_revenue_margin_none(self):
        a = ("01", "A", _FakeStmt(_pl_lines(0.0, -100.0)))
        b = ("02", "B", _FakeStmt(_pl_lines(0.0, -50.0)))
        res = consolidate_statements("pl", [a, b], ["FY"], {"FY": "FY"}, "year", 1.0)
        assert _cell(res.consolidated, "REVENUE") == pytest.approx(0.0)
        assert _cell(res.consolidated, "GROSS_MARGIN_PCT") is None


# =========================================================================== #
# Endpoint integration (fake session: two entities, PL + BS movements)
# =========================================================================== #
class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def _pl_rows_for(entity_prefix):
    # Atlas (01): -600/period revenue, +100 material ; Meridian (02): -400 / +80.
    rev = -600.0 if entity_prefix == "01" else -400.0
    mat = 100.0 if entity_prefix == "01" else 80.0
    rows = []
    for p in range(1, 6):
        rows.append((2025, p, "Umsatzerlöse", "Net sales", "X", rev))
        rows.append((2025, p, "Materialaufwand", "Cost of materials", "Y", mat))
    return rows


class _FakeSession:
    def execute(self, statement, params=None):
        sql = str(statement)
        if "dim_legal_entity" in sql:
            return _FakeResult([("01", "Atlas GmbH"), ("02", "Meridian AG")])
        is_struct = "dim_pl_structure" in sql and "fact_gl" not in sql
        if is_struct:
            return _FakeResult([])
        if "level_0 = 'PL'" in sql or "a.level_0 = 'PL'" in sql:
            entity = (params or {}).get("entity")
            return _FakeResult(_pl_rows_for(entity))
        return _FakeResult([])

    def close(self):
        pass


def _fake_user():
    return User(user_id=1, email="t@example.com", display_name="T", is_admin=False)


@pytest.fixture
def client():
    m.app.dependency_overrides[get_session] = lambda: _FakeSession()
    m.app.dependency_overrides[current_user] = _fake_user
    yield TestClient(m.app)
    m.app.dependency_overrides.clear()


def _body_cell(lines, code, col):
    ln = next(l for l in lines if l["line_code"] == code)
    return next(c for c in ln["cells"] if c["column_key"] == col)["value"]


def test_consolidation_requires_auth():
    with TestClient(m.app) as c:
        r = c.get("/api/v1/statements/pl/consolidation?current_fy=2025&last_closed_period=5")
    assert r.status_code == 401


def test_consolidation_endpoint_sums_entities(client):
    r = client.get("/api/v1/statements/pl/consolidation?current_fy=2025&last_closed_period=5")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [e["entity_prefix"] for e in body["entities"]] == ["01", "02"]
    # Atlas YTD revenue = 3000, Meridian = 2000 → consolidated 5000.
    assert _body_cell(body["entities"][0]["lines"], "REVENUE", "YTD") == pytest.approx(3000.0)
    assert _body_cell(body["entities"][1]["lines"], "REVENUE", "YTD") == pytest.approx(2000.0)
    assert _body_cell(body["consolidated"], "REVENUE", "YTD") == pytest.approx(5000.0)
    assert body["intercompany_eliminated"] is False


def test_consolidation_unknown_kind_422(client):
    r = client.get("/api/v1/statements/xx/consolidation?current_fy=2025&last_closed_period=5")
    assert r.status_code == 422


# =========================================================================== #
# Running-total consolidation — Decidra-shaped structure: consolidated
# subtotal/calc rows are the running cumulative sum of the consolidated MAPPING
# lines, so consolidated NET_PROFIT == Σ entity NET_PROFIT == Σ consolidated
# mappings (subtotals are NOT summed across entities → no double counting).
# =========================================================================== #
def _decidra_pl_lines(mappings: dict[str, float], col: str = "FY") -> list[StatementLine]:
    """Build one entity's Decidra-shaped PL with running-total subtotals/calcs.

    ``mappings`` maps line_code → presented value for the mapping lines; the
    subtotal/calc lines are filled with the running cumulative sum, mirroring
    statements.aggregate_pl, so this fixture matches what the real service emits.
    """
    order = [
        ("NET_SALES", "mapping", None),
        ("FINISHED_GOODS_WIP", "mapping", None),
        ("TOTAL_OUTPUT", "subtotal", None),
        ("COST_OF_MATERIALS", "mapping", None),
        ("GROSS_PROFIT", "calc", "GROSS_PROFIT"),
        ("PERSONNEL_EXPENSES", "mapping", None),
        ("EBITDA", "calc", "EBITDA"),
        ("DEPRECIATION_AMORTISATION", "mapping", None),
        ("EBIT", "subtotal", None),
        ("TAXES_ON_INCOME", "mapping", None),
        ("NET_PROFIT", "subtotal", None),
    ]
    lines: list[StatementLine] = []
    running = 0.0
    for code, row_type, kpi in order:
        if row_type == "mapping":
            val = mappings.get(code, 0.0)
            running += val
        else:
            val = running  # running cumulative sum (no _PCT ratios here)
        lines.append(StatementLine(code, code, row_type, False, kpi, [StatementCell(col, val)]))
    return lines


def test_consolidated_subtotals_are_running_sum_not_double_counted():
    a_map = {
        "NET_SALES": 1000.0, "FINISHED_GOODS_WIP": 100.0, "COST_OF_MATERIALS": -400.0,
        "PERSONNEL_EXPENSES": -200.0, "DEPRECIATION_AMORTISATION": -60.0,
        "TAXES_ON_INCOME": -120.0,
    }
    b_map = {
        "NET_SALES": 500.0, "FINISHED_GOODS_WIP": 50.0, "COST_OF_MATERIALS": -200.0,
        "PERSONNEL_EXPENSES": -100.0, "DEPRECIATION_AMORTISATION": -30.0,
        "TAXES_ON_INCOME": -60.0,
    }
    a = ("01", "A", _FakeStmt(_decidra_pl_lines(a_map)))
    b = ("02", "B", _FakeStmt(_decidra_pl_lines(b_map)))
    res = consolidate_statements("pl", [a, b], ["FY"], {"FY": "FY"}, "year", 1.0)

    # Consolidated mapping lines = additive sum.
    assert _cell(res.consolidated, "NET_SALES") == pytest.approx(1500.0)
    assert _cell(res.consolidated, "COST_OF_MATERIALS") == pytest.approx(-600.0)
    # TOTAL_OUTPUT = consolidated NET_SALES + FINISHED_GOODS_WIP = 1500 + 150 = 1650
    assert _cell(res.consolidated, "TOTAL_OUTPUT") == pytest.approx(1650.0)
    # GROSS_PROFIT = 1500 + 150 - 600 = 1050  (running sum of consolidated mappings)
    assert _cell(res.consolidated, "GROSS_PROFIT") == pytest.approx(1050.0)

    # KEY INVARIANT: consolidated NET_PROFIT == Σ entity NET_PROFIT.
    a_net = _cell(a[2].lines, "NET_PROFIT")
    b_net = _cell(b[2].lines, "NET_PROFIT")
    assert _cell(res.consolidated, "NET_PROFIT") == pytest.approx(a_net + b_net)

    # …and == Σ of all consolidated mapping lines (no subtotal double-counting).
    mapped = sum(
        _cell(res.consolidated, ln.line_code)
        for ln in res.consolidated if ln.row_type == "mapping"
    )
    assert _cell(res.consolidated, "NET_PROFIT") == pytest.approx(mapped)
