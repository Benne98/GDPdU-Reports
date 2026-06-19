"""P5 / C7 — GOLDEN tests for the GL drill (app.services.bookings).

The RECONCILIATION guarantee is the heart of this feature:
    Σ presented_amount over drill(line, column, entity)  ==  statement cell value.

We assert it two ways on SYNTHETIC data:
  • PL (in-period buckets, presented = -amount): a REVENUE cell of +3000 reconciles
    to 5 booking lines of -600 each (Σ presented = +3000).
  • BS (cumulative cutoff, presented = +amount asset): a CASH cell of +3300 reconciles
    to the cumulative booking lines up to the cutoff.
Plus column→bucket resolution, pagination invariants (total independent of
limit/offset), and the refusal edge cases (no selector, plan drill).
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
from app.services.bookings import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    build_bookings,
    resolve_column_buckets,
)


# --------------------------------------------------------------------------- #
# Synthetic booking lines.
#   PL revenue: 5 lines of stored -600 in FY2025 P1..P5 (presented +600 each → +3000).
#   BS cash:    opening +2900 (P1) + 100 in P2..P5 (cumulative +3300 @ cutoff (2025,5)).
# Each row tuple mirrors the SELECT column order of fetch_bookings' rows query:
#   (booking_line_id, jegn, fiscal_year, fiscal_period, line_number,
#    account_number_group, account_name, level_2, level_3, level_4,
#    posting_date, document_date, document_type_code, reference_document_number,
#    line_note, entity_prefix, amount)
# --------------------------------------------------------------------------- #
def _pl_rev_rows():
    rows = []
    for i, p in enumerate(range(1, 6)):
        rows.append((
            1000 + i, f"01{1000+i:08d}", 2025, p, 1,
            "01400000", "Inlandsumsatz", "Umsatzerlöse", "Net sales", "Inlandsumsatz",
            f"2025-0{p}-15", f"2025-0{p}-10", "RV", f"INV{i}", None, "01", -600.0,
        ))
    return rows


def _bs_cash_rows():
    amounts = [(1, 2900.0), (2, 100.0), (3, 100.0), (4, 100.0), (5, 100.0)]
    rows = []
    for i, (p, amt) in enumerate(amounts):
        rows.append((
            2000 + i, f"01{2000+i:08d}", 2025, p, 1,
            "01010000", "Bank", "Current assets", "Cash", "Bank",
            f"2025-0{p}-15", f"2025-0{p}-10", "SB", f"BANK{i}", None, "01", amt,
        ))
    return rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Routes the aggregate query (COUNT/SUM → fetchone) vs the rows query (fetchall).

    The aggregate query selects 'COUNT(*)'; the rows query selects booking_line_id.
    We honour LIMIT/OFFSET from params on the rows query so pagination is testable.
    """

    def __init__(self, rows):
        self._rows = rows

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        if "COUNT(*)" in sql:
            n = len(self._rows)
            sum_amount = sum(r[16] for r in self._rows)
            return _FakeResult([(n, sum_amount)])
        # rows query: apply offset/limit like the DB would
        off = params.get("off", 0)
        lim = params.get("lim", len(self._rows))
        return _FakeResult(self._rows[off:off + lim])

    def close(self):
        pass


def _fake_user():
    return User(user_id=1, email="t@example.com", display_name="T", is_admin=False)


# =========================================================================== #
# Column → bucket resolution (reuses the period engine)
# =========================================================================== #
class TestColumnResolution:
    def test_pl_ytd_in_period_buckets(self):
        in_period, cutoff = resolve_column_buckets(
            "pl", "YTD", view_mode="year", current_fy=2025, last_closed_period=5
        )
        assert cutoff is None
        assert in_period == {(2025, p) for p in range(1, 6)}

    def test_bs_ytd_cumulative_cutoff(self):
        in_period, cutoff = resolve_column_buckets(
            "bs", "YTD", view_mode="year", current_fy=2025, last_closed_period=5
        )
        assert in_period is None
        assert cutoff == (2025, 5)

    def test_pl_empty_ytd_when_nothing_closed(self):
        in_period, cutoff = resolve_column_buckets(
            "pl", "YTD", view_mode="year", current_fy=2025, last_closed_period=0
        )
        assert in_period == set()

    def test_unknown_column_raises(self):
        with pytest.raises(ValueError):
            resolve_column_buckets(
                "pl", "NOPE", view_mode="year", current_fy=2025, last_closed_period=5
            )


# =========================================================================== #
# RECONCILIATION — Σ presented(bookings) == cell value
# =========================================================================== #
class TestReconciliation:
    def test_pl_revenue_drill_sums_to_cell_3000(self):
        sess = _FakeSession(_pl_rev_rows())
        res = build_bookings(
            sess, kind="pl", column_key="YTD", view_mode="year",
            current_fy=2025, last_closed_period=5, level_3="Net sales", entity_prefix="01",
        )
        # 5 rows of stored -600 → presented +600 each → Σ +3000 == the PL REVENUE/YTD cell.
        assert res.total_count == 5
        assert res.total == pytest.approx(3000.0)
        assert sum(r.presented_amount for r in res.rows) == pytest.approx(3000.0)

    def test_bs_cash_drill_sums_to_cell_3300(self):
        sess = _FakeSession(_bs_cash_rows())
        res = build_bookings(
            sess, kind="bs", column_key="YTD", view_mode="year",
            current_fy=2025, last_closed_period=5, level_3="Cash", bs_side="asset",
            entity_prefix="01",
        )
        # cumulative stored 2900+100*4 = 3300, asset presented = +amount → +3300.
        assert res.total == pytest.approx(3300.0)
        assert sum(r.presented_amount for r in res.rows) == pytest.approx(3300.0)

    def test_bs_credit_side_flips_sign(self):
        # A credit-side line (payables) stored negative presents positive.
        rows = [(
            3000, "0100003000", 2025, 1, 1, "01160000", "Trade payables",
            "Current liabilities", "Payables", "Trade", "2025-01-15", "2025-01-10",
            "KR", "AP1", None, "01", -400.0,
        )]
        sess = _FakeSession(rows)
        res = build_bookings(
            sess, kind="bs", column_key="YTD", view_mode="year",
            current_fy=2025, last_closed_period=5, level_3="Payables", bs_side="credit",
            entity_prefix="01",
        )
        # stored -400, credit side presented = -amount = +400.
        assert res.total == pytest.approx(400.0)
        assert res.rows[0].presented_amount == pytest.approx(400.0)


# =========================================================================== #
# Pagination — total is independent of limit/offset
# =========================================================================== #
class TestPagination:
    def test_total_independent_of_limit(self):
        sess = _FakeSession(_pl_rev_rows())
        res = build_bookings(
            sess, kind="pl", column_key="YTD", view_mode="year",
            current_fy=2025, last_closed_period=5, level_3="Net sales",
            entity_prefix="01", limit=2, offset=0,
        )
        assert len(res.rows) == 2          # page is capped
        assert res.total_count == 5        # full set count
        assert res.total == pytest.approx(3000.0)  # full-set sum, not the 2-row page

    def test_offset_past_end_empty_page_total_intact(self):
        sess = _FakeSession(_pl_rev_rows())
        res = build_bookings(
            sess, kind="pl", column_key="YTD", view_mode="year",
            current_fy=2025, last_closed_period=5, level_3="Net sales",
            entity_prefix="01", limit=10, offset=100,
        )
        assert res.rows == []
        assert res.total == pytest.approx(3000.0)


# =========================================================================== #
# Refusals / edge cases
# =========================================================================== #
class TestRefusals:
    def test_no_selector_refused(self):
        sess = _FakeSession([])
        with pytest.raises(ValueError):
            build_bookings(
                sess, kind="pl", column_key="YTD", view_mode="year",
                current_fy=2025, last_closed_period=5,
            )

    def test_plan_drill_refused(self):
        sess = _FakeSession([])
        with pytest.raises(ValueError):
            build_bookings(
                sess, kind="pl", column_key="YTD", view_mode="year",
                current_fy=2025, last_closed_period=5, level_3="Net sales", scenario="plan",
            )

    def test_unknown_kind_refused(self):
        sess = _FakeSession([])
        with pytest.raises(ValueError):
            build_bookings(
                sess, kind="cf", column_key="YTD", view_mode="year",
                current_fy=2025, last_closed_period=5, level_3="X",
            )

    def test_limit_hard_capped(self):
        sess = _FakeSession(_pl_rev_rows())
        res = build_bookings(
            sess, kind="pl", column_key="YTD", view_mode="year",
            current_fy=2025, last_closed_period=5, level_3="Net sales",
            entity_prefix="01", limit=MAX_LIMIT + 5000,
        )
        assert res.limit <= MAX_LIMIT


# =========================================================================== #
# Endpoint smoke (auth + shape)
# =========================================================================== #
@pytest.fixture
def client_pl():
    m.app.dependency_overrides[get_session] = lambda: _FakeSession(_pl_rev_rows())
    m.app.dependency_overrides[current_user] = _fake_user
    yield TestClient(m.app)
    m.app.dependency_overrides.clear()


def test_bookings_requires_auth():
    with TestClient(m.app) as c:
        r = c.get("/api/v1/statements/bookings?kind=pl&col=YTD&current_fy=2025&last_closed_period=5&level_3=Net%20sales")
    assert r.status_code == 401


def test_bookings_endpoint_reconciles(client_pl):
    r = client_pl.get(
        "/api/v1/statements/bookings?kind=pl&col=YTD&current_fy=2025&last_closed_period=5"
        "&level_3=Net%20sales&entity=01"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_count"] == 5
    assert body["total"] == pytest.approx(3000.0)
    assert sum(row["presented_amount"] for row in body["rows"]) == pytest.approx(3000.0)


def test_bookings_no_selector_422(client_pl):
    r = client_pl.get(
        "/api/v1/statements/bookings?kind=pl&col=YTD&current_fy=2025&last_closed_period=5"
    )
    assert r.status_code == 422
