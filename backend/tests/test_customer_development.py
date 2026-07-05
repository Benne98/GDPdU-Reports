"""Tests for customer development (Overview v2 Area 4) — DB-free.

app.services.partner_development.build_customer_development + its pure helpers.

Mirrors the mock-Session + _DictRow + docstring-worked-example convention of
test_overview_metrics.py.

=== WORKED EXAMPLES ===
  * AvgPerInvoice: 500 kEUR over 4 distinct jegn → 125.0 kEUR/invoice.
  * Lost-window classifier (T=5, floor=1, frac=0.10):
      prior 40, current 0 → ε=max(1,4)=4, 0≤4      → 'lost'
      prior 3             → 'immaterial' (3 < 5)
      prior 40, current 6 → ε=4, 6>4               → 'declining' (not lost)
  * Biggest by YTD desc: B(900), A(500), Won(300), C(200); Zero(0) dropped.
  * ΔYoY(YTD) increase excl. won: A(+100), C(0), B(-50); Won(py=0) → 'won'.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class _DictRow:
    def __init__(self, d: dict):
        self._mapping = d
        for k, v in d.items():
            setattr(self, k, v)

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self._mapping.values())[key]
        return self._mapping[key]

    def get(self, key, default=None):
        return self._mapping.get(key, default)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
class TestPureHelpers:

    def test_classify_lost_examples(self):
        from app.services.partner_development import classify_lost
        assert classify_lost(40, 0) == "lost"          # ε=4, 0≤4
        assert classify_lost(3, 0) == "immaterial"     # prior < 5
        assert classify_lost(40, 6) == "declining"     # ε=4, 6>4
        assert classify_lost(50, 2) == "lost"          # ε=5, 2≤5
        assert classify_lost(40, 4) == "lost"          # boundary: 4 == ε → lost

    def test_avg_per_txn_zero_guard(self):
        from app.services.partner_development import avg_per_txn
        assert avg_per_txn(500, 4) == 125.0
        assert avg_per_txn(620, 4) == 155.0
        assert avg_per_txn(200, 0) is None             # zero-guard
        assert avg_per_txn(0, 0) is None

    def test_lost_customer_windows_non_overlap(self):
        from app.services.partner_development import lost_customer_windows
        (pri_f, pri_t), (cur_f, cur_t) = lost_customer_windows(2026, 6)
        assert (cur_f, cur_t) == ("2025-06-01", "2026-05-31")   # [-12..-1]
        assert (pri_f, pri_t) == ("2024-06-01", "2025-05-31")   # [-24..-13]
        assert pri_t < cur_f                                    # non-overlapping

    def test_split_increase_won(self):
        from app.services.partner_development import _split_increase_won
        agg = [
            {"customer_id": "A", "name": "A", "ytd": 500, "ytd_py": 400},
            {"customer_id": "B", "name": "B", "ytd": 900, "ytd_py": 950},
            {"customer_id": "C", "name": "C", "ytd": 200, "ytd_py": 200},
            {"customer_id": "W", "name": "W", "ytd": 300, "ytd_py": 0},
            {"customer_id": "Z", "name": "Z", "ytd": 0, "ytd_py": 0},
        ]
        inc, won = _split_increase_won(agg, id_field="customer_id", top_n=10)
        assert [r["customer_id"] for r in inc] == ["A", "C", "B"]  # ΔYoY desc
        assert inc[0]["delta_yoy_keur"] == 100
        assert [r["customer_id"] for r in won] == ["W"]            # py=0 → won
        assert won[0]["delta_yoy_keur"] == 300


# ---------------------------------------------------------------------------
# Mock-session integration
# ---------------------------------------------------------------------------
_MAIN = [
    {"partner_id": "AT0001", "name": "Alpha", "cm": 50, "pm": 40, "py_cm": 45,
     "ytd": 500, "ytd_py": 400, "invoice_count": 4},
    {"partner_id": "AT0002", "name": "Beta", "cm": 80, "pm": 70, "py_cm": 90,
     "ytd": 900, "ytd_py": 950, "invoice_count": 6},
    {"partner_id": "AT0003", "name": "Won Co", "cm": 30, "pm": 0, "py_cm": 0,
     "ytd": 300, "ytd_py": 0, "invoice_count": 2},
    {"partner_id": "AT0004", "name": "Cee", "cm": 20, "pm": 20, "py_cm": 20,
     "ytd": 200, "ytd_py": 200, "invoice_count": 0},   # zero-guard → avg None
    {"partner_id": "AT0009", "name": "Zero", "cm": 0, "pm": 0, "py_cm": 0,
     "ytd": 0, "ytd_py": 0, "invoice_count": 0},        # all-zero → dropped
]
_LOST = [
    {"partner_id": "AT0100", "name": "Gone Big", "rev_prior": 50, "rev_current": 2},   # lost
    {"partner_id": "AT0101", "name": "Gone", "rev_prior": 40, "rev_current": 0},        # lost
    {"partner_id": "AT0102", "name": "Small", "rev_prior": 3, "rev_current": 0},        # immaterial
    {"partner_id": "AT0103", "name": "Declining", "rev_prior": 40, "rev_current": 6},   # declining
]


def _cust_session(main=_MAIN, lost=_LOST, captured=None):
    session = MagicMock()

    def _execute(stmt, params=None):
        sql = str(stmt)
        if captured is not None:
            captured.append(sql)
        result = MagicMock()
        if "AS rev_prior" in sql:
            rows = [_DictRow(r) for r in lost]
        else:
            rows = [_DictRow(r) for r in main]
        result.fetchall.return_value = rows
        return result

    session.execute.side_effect = _execute
    return session


class TestBuildCustomerDevelopment:

    def _out(self, **kw):
        from app.services.partner_development import build_customer_development
        return build_customer_development(_cust_session(), year=2026, month=6, **kw)

    def test_biggest_ranked_by_ytd_with_avg(self):
        out = self._out()
        assert out["id_field"] == "customer_id"
        codes = [r["customer_id"] for r in out["biggest"]]
        assert codes == ["AT0002", "AT0001", "AT0003", "AT0004"]  # Zero dropped
        alpha = next(r for r in out["biggest"] if r["customer_id"] == "AT0001")
        assert alpha["rev_ytd_keur"] == 500
        assert alpha["invoice_count"] == 4
        assert alpha["avg_per_invoice_keur"] == 125.0            # worked example
        assert alpha["fav"] == "plus"
        cee = next(r for r in out["biggest"] if r["customer_id"] == "AT0004")
        assert cee["avg_per_invoice_keur"] is None               # zero-guard

    def test_increase_and_won(self):
        out = self._out()
        assert [r["customer_id"] for r in out["increase"]] == ["AT0001", "AT0004", "AT0002"]
        assert out["increase"][0]["delta_yoy_keur"] == 100
        assert [r["customer_id"] for r in out["won"]] == ["AT0003"]
        assert out["won"][0]["rev_cur_keur"] == 300

    def test_lost_window_classification(self):
        out = self._out()
        codes = [r["customer_id"] for r in out["lost"]]
        assert codes == ["AT0100", "AT0101"]                     # sorted prior desc
        assert all(r["fav"] == "minus" for r in out["lost"])
        assert out["lost"][0]["rev_prior_keur"] == 50

    def test_period_meta(self):
        out = self._out()
        assert out["period"]["current_window"] == ["2025-06-01", "2026-05-31"]
        assert out["period"]["prior_window"] == ["2024-06-01", "2025-05-31"]


# ---------------------------------------------------------------------------
# Fail-closed tenant isolation
# ---------------------------------------------------------------------------
class TestFailClosed:

    def test_empty_set_returns_empty_no_sql(self):
        from app.services.partner_development import build_customer_development
        session = _cust_session()
        out = build_customer_development(
            session, year=2026, month=6, allowed_entities=set())
        assert out["biggest"] == [] and out["increase"] == []
        assert out["won"] == [] and out["lost"] == []
        session.execute.assert_not_called()                      # no SQL issued

    def test_restricted_set_filters_every_query(self):
        from app.services.partner_development import build_customer_development
        captured: list[str] = []
        session = _cust_session(captured=captured)
        build_customer_development(
            session, year=2026, month=6, allowed_entities={"10"})
        assert captured, "no SQL issued"
        for sql in captured:
            assert "LEFT(f.account_number_group, 2) IN ('10')" in sql

    def test_none_default_no_visibility_filter(self):
        from app.services.partner_development import build_customer_development
        captured: list[str] = []
        session = _cust_session(captured=captured)
        # resolve_entity_prefix on a MagicMock returns a MagicMock row; entity=None
        # so the resolver is invoked but no IN(...) visibility fragment is emitted.
        build_customer_development(session, year=2026, month=6)
        for sql in captured:
            assert "IN ('10')" not in sql
            assert "AND 1 = 0" not in sql
