"""P5 / C3-C5 — Endpoint tests for GET /bs, /wc, /cf WITHOUT a live DB.

FastAPI dependency_overrides inject a fake current_user and a fake session whose
.execute() routes by SQL text:
  • dim_pl_structure (PL)  → []  → default PL structure
  • dim_pl_structure WHERE kpi_code LIKE 'BS:%' → [] → default BS structure
  • fact_gl_line / fact_gl_plan with level_0='BS' → synthetic BS movements
  • fact_gl_line / fact_gl_plan with level_0='PL' → synthetic PL movements
Asserts the worked-example numbers and the reconciliations travel end-to-end.
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


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


# Synthetic BS movements — CONSISTENT closed-books set so the CF ties out end-to-end:
# net income (P&L GROSS_PROFIT over FY = 2500) is carried into Retained earnings
# (−2500), and the offsetting cash inflow lifts cumulative cash to 3300.  The set is
# balanced (Σ stored amount == 0) and the accounting identity holds.
# Shape: (fiscal_year, fiscal_period, level_2, level_3, level_4, amount)
#   Cash:      opening 2900 (P1) + 100×4 (P2..5)  → cum 3300
#   AR:        300 (P1) + 200 (P3)                 → cum 500
#   Inventory: 200 (P1)                            → cum 200
#   Payables:  −400 (P1) − 100 (P4)                → cum −500 → presented 500
#   Equity:    −1000 (P1)                          → presented 1000
#   Retained:  −2500 (P1)  (= − net income)        → presented 2500
# Σ stored = 3300 + 500 + 200 − 500 − 1000 − 2500 = 0  → balanced.
def _bs_rows():
    rows = [
        (2025, 1, "Current assets", "Cash", "Bank", 2900.0),
        (2025, 2, "Current assets", "Cash", "Bank", 100.0),
        (2025, 3, "Current assets", "Cash", "Bank", 100.0),
        (2025, 4, "Current assets", "Cash", "Bank", 100.0),
        (2025, 5, "Current assets", "Cash", "Bank", 100.0),
        (2025, 1, "Current assets", "Receivables", "Trade", 300.0),
        (2025, 3, "Current assets", "Receivables", "Trade", 200.0),
        (2025, 1, "Current assets", "Inventory", "Raw", 200.0),
        (2025, 1, "Current liabilities", "Payables", "Trade", -400.0),
        (2025, 4, "Current liabilities", "Payables", "Trade", -100.0),
        (2025, 1, "Equity", "Equity", "Share capital", -1000.0),
        (2025, 1, "Equity", "Retained earnings", "P/L", -2500.0),
    ]
    return rows


def _pl_rows():
    rows = []
    for p in range(1, 6):
        rows.append((2025, p, "Umsatzerlöse", "Net sales", "X", -600.0))
        rows.append((2025, p, "Materialaufwand", "Cost of materials", "Y", 100.0))
    return rows


class _FakeSession:
    def execute(self, statement, params=None):
        sql = str(statement)
        is_struct = "dim_pl_structure" in sql and "fact_gl" not in sql
        if is_struct:
            return _FakeResult([])  # both PL and BS fall back to defaults
        if "level_0 = 'BS'" in sql:
            return _FakeResult(_bs_rows())
        if "level_0 = 'PL'" in sql:
            return _FakeResult(_pl_rows())
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


def _cell(body, line_code, column_key):
    line = next(l for l in body["lines"] if l["line_code"] == line_code)
    return next(c for c in line["cells"] if c["column_key"] == column_key)["value"]


# =========================================================================== #
# Auth
# =========================================================================== #
@pytest.mark.parametrize("path", ["bs", "wc", "cf"])
def test_requires_auth(path):
    with TestClient(m.app) as c:
        r = c.get(f"/api/v1/statements/{path}?current_fy=2025&last_closed_period=5")
    assert r.status_code == 401


# =========================================================================== #
# /bs
# =========================================================================== #
def test_bs_worked_example_and_identity(client):
    r = client.get("/api/v1/statements/bs?current_fy=2025&last_closed_period=5")
    assert r.status_code == 200, r.text
    body = r.json()
    assert _cell(body, "CASH", "YTD") == pytest.approx(3300.0)
    assert _cell(body, "TOTAL_ASSETS", "YTD") == pytest.approx(4000.0)
    assert _cell(body, "TOTAL_LIABILITIES", "YTD") == pytest.approx(500.0)
    assert _cell(body, "TOTAL_EQUITY", "YTD") == pytest.approx(3500.0)
    assert body["imbalance"]["YTD"] == pytest.approx(0.0)


# =========================================================================== #
# /wc
# =========================================================================== #
def test_wc_ratios_worked_example(client):
    r = client.get("/api/v1/statements/wc?current_fy=2025&last_closed_period=5")
    assert r.status_code == 200, r.text
    body = r.json()
    # FY column: AR 500, INV 200, AP 500, REV 3000, COGS 500, D=365
    assert _cell(body, "NWC", "FY") == pytest.approx(200.0)
    assert _cell(body, "DSO", "FY") == pytest.approx(500 / 3000 * 365)
    assert _cell(body, "DIO", "FY") == pytest.approx(200 / 500 * 365)
    assert _cell(body, "DPO", "FY") == pytest.approx(500 / 500 * 365)
    assert _cell(body, "CCC", "FY") == pytest.approx(
        500 / 3000 * 365 + 200 / 500 * 365 - 500 / 500 * 365
    )
    assert body["days_in_period"]["FY"] == pytest.approx(365.0)


# =========================================================================== #
# /cf
# =========================================================================== #
def test_cf_ties_out_every_column(client):
    r = client.get("/api/v1/statements/cf?current_fy=2025&last_closed_period=5")
    assert r.status_code == 200, r.text
    body = r.json()
    for key, residual in body["tieout_residual"].items():
        assert residual == pytest.approx(0.0), key


# =========================================================================== #
# Registration + validation
# =========================================================================== #
def test_routes_registered():
    paths = [r.path for r in m.app.routes if "statements" in getattr(r, "path", "")]
    for p in ("/api/v1/statements/bs", "/api/v1/statements/wc", "/api/v1/statements/cf"):
        assert p in paths


def test_bs_invalid_period_422(client):
    r = client.get("/api/v1/statements/bs?current_fy=2025&last_closed_period=13")
    assert r.status_code == 422
