"""P5 / C1 — Endpoint test for GET /api/v1/statements/pl WITHOUT a live DB.

Uses FastAPI dependency_overrides to inject:
  • a FAKE current_user (bypasses JWT/auth_session),
  • a FAKE session whose .execute(...).fetchall() returns synthetic rows for both
    the dim_pl_structure query (empty → service falls back to default structure)
    and the fact_gl_line aggregation query.

Asserts the worked-example numbers travel end-to-end through the router.
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


# --------------------------------------------------------------------------- #
# Fake DB session
# --------------------------------------------------------------------------- #
class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeSession:
    """Routes .execute() by inspecting the SQL text.

    - dim_pl_structure query → return [] (service uses default_pl_structure()).
    - fact_gl_line movements query → return synthetic aggregated rows:
        (fiscal_year, fiscal_period, level_2, level_3, level_4, amount)
      Revenue P1..P5 of 2025: -600/period ; Material P1..P5: +100/period.
    """

    def execute(self, statement, params=None):
        sql = str(statement)
        if "dim_pl_structure" in sql and "fact_gl_line" not in sql and "fact_gl_plan" not in sql:
            return _FakeResult([])  # → default structure
        if "fact_gl_line" in sql or "fact_gl_plan" in sql:
            rows = []
            for p in range(1, 6):
                rows.append((2025, p, "Umsatzerlöse", "Net sales", "Inlandsumsatz", -600.0))
                rows.append((2025, p, "Materialaufwand", "Cost of materials", "Rohstoffe", 100.0))
            return _FakeResult(rows)
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


# =========================================================================== #
# Tests
# =========================================================================== #
def test_pl_requires_auth_without_override():
    # No override → real current_user dependency → 401 (no token).
    with TestClient(m.app) as c:
        r = c.get("/api/v1/statements/pl?current_fy=2025&last_closed_period=5")
    assert r.status_code == 401


def test_pl_year_view_worked_example(client):
    r = client.get("/api/v1/statements/pl?view_mode=year&current_fy=2025&last_closed_period=5")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["view_mode"] == "year"
    assert body["coverage"] == pytest.approx(5 / 12)

    # locate YTD column values
    def cell(line_code):
        line = next(l for l in body["lines"] if l["line_code"] == line_code)
        c = next(c for c in line["cells"] if c["column_key"] == "YTD")
        return c["value"]

    assert cell("REVENUE") == pytest.approx(3000.0)
    assert cell("COGS") == pytest.approx(-500.0)
    assert cell("GROSS_PROFIT") == pytest.approx(2500.0)
    assert cell("GROSS_MARGIN_PCT") == pytest.approx(2500 / 3000 * 100)
    assert cell("EBITDA") == pytest.approx(2500.0)


def test_pl_columns_are_year_slices(client):
    r = client.get("/api/v1/statements/pl?current_fy=2025&last_closed_period=5")
    keys = [c["key"] for c in r.json()["columns"]]
    assert keys == ["FY-2", "FY-1", "FY", "YTD", "YTD_PY", "LTM", "LTM_PY"]


def test_pl_invalid_view_mode_422(client):
    r = client.get("/api/v1/statements/pl?view_mode=quarter&current_fy=2025&last_closed_period=5")
    assert r.status_code == 422


def test_pl_invalid_last_closed_period_422(client):
    r = client.get("/api/v1/statements/pl?current_fy=2025&last_closed_period=13")
    assert r.status_code == 422


def test_pl_month_view(client):
    r = client.get("/api/v1/statements/pl?view_mode=month&current_fy=2025&last_closed_period=5")
    assert r.status_code == 200
    cols = r.json()["columns"]
    assert len(cols) == 24
    assert cols[0]["key"] == "2024-01"


def test_app_main_includes_statements_route():
    paths = [r.path for r in m.app.routes if "statements" in getattr(r, "path", "")]
    assert "/api/v1/statements/pl" in paths
