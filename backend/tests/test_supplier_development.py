"""Tests for supplier development (Overview v2 Area 5) — DB-free.

app.services.partner_development.build_supplier_development. Symmetric to the
customer side on fact_com (cost), no "lost" bucket; cost-increase rows are FAV−
and carry an invert flag (sign never mutated).

=== WORKED EXAMPLES ===
  * AvgPerPurchase: 620 kEUR over 4 distinct jegn → 155.0 kEUR/purchase.
  * Biggest by YTD cost desc: S1(620), S2(400), S3(200), S4(100).
  * ΔYoY(YTD) increase excl. new spend: S1(+120), S2(+100), S4(0);
    S3(py=0) → 'new_spend'.
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


_MAIN = [
    {"partner_id": "DE9001", "name": "Steel", "cm": 60, "pm": 55, "py_cm": 50,
     "ytd": 620, "ytd_py": 500, "purchase_txns": 4},
    {"partner_id": "DE9002", "name": "Copper", "cm": 40, "pm": 35, "py_cm": 30,
     "ytd": 400, "ytd_py": 300, "purchase_txns": 5},
    {"partner_id": "DE9003", "name": "New Sup", "cm": 20, "pm": 0, "py_cm": 0,
     "ytd": 200, "ytd_py": 0, "purchase_txns": 2},        # py=0 → new spend
    {"partner_id": "DE9004", "name": "Flat", "cm": 10, "pm": 10, "py_cm": 10,
     "ytd": 100, "ytd_py": 100, "purchase_txns": 0},       # zero-guard → avg None
    {"partner_id": "DE9009", "name": "Zero", "cm": 0, "pm": 0, "py_cm": 0,
     "ytd": 0, "ytd_py": 0, "purchase_txns": 0},           # all-zero → dropped
]


def _supp_session(main=_MAIN, captured=None):
    session = MagicMock()

    def _execute(stmt, params=None):
        sql = str(stmt)
        if captured is not None:
            captured.append(sql)
        result = MagicMock()
        result.fetchall.return_value = [_DictRow(r) for r in main]
        return result

    session.execute.side_effect = _execute
    return session


class TestBuildSupplierDevelopment:

    def _out(self, **kw):
        from app.services.partner_development import build_supplier_development
        return build_supplier_development(_supp_session(), year=2026, month=6, **kw)

    def test_no_lost_bucket(self):
        out = self._out()
        assert out["id_field"] == "supplier_id"
        assert "lost" not in out                             # AP side has no lost

    def test_biggest_ranked_by_cost_with_avg(self):
        out = self._out()
        codes = [r["supplier_id"] for r in out["biggest"]]
        assert codes == ["DE9001", "DE9002", "DE9003", "DE9004"]  # Zero dropped
        steel = out["biggest"][0]
        assert steel["cost_ytd_keur"] == 620
        assert steel["purchase_txns"] == 4
        assert steel["avg_per_purchase_keur"] == 155.0       # worked example
        assert steel["fav"] == "minus"
        flat = next(r for r in out["biggest"] if r["supplier_id"] == "DE9004")
        assert flat["avg_per_purchase_keur"] is None         # zero-guard

    def test_cost_increase_carries_invert_flag(self):
        out = self._out()
        assert [r["supplier_id"] for r in out["increase"]] == ["DE9001", "DE9002", "DE9004"]
        top = out["increase"][0]
        assert top["delta_yoy_keur"] == 120                  # raw signed, NOT mutated
        assert top["fav"] == "minus"
        assert top["invert_delta"] is True

    def test_new_spend_bucket(self):
        out = self._out()
        assert [r["supplier_id"] for r in out["won"]] == ["DE9003"]
        assert out["won"][0]["kind"] == "new_spend"
        assert out["won"][0]["cost_cur_keur"] == 200


class TestFailClosed:

    def test_empty_set_returns_empty_no_sql(self):
        from app.services.partner_development import build_supplier_development
        session = _supp_session()
        out = build_supplier_development(
            session, year=2026, month=6, allowed_entities=set())
        assert out["biggest"] == [] and out["increase"] == [] and out["won"] == []
        session.execute.assert_not_called()

    def test_restricted_set_filters_query(self):
        from app.services.partner_development import build_supplier_development
        captured: list[str] = []
        session = _supp_session(captured=captured)
        build_supplier_development(
            session, year=2026, month=6, allowed_entities={"10"})
        assert captured
        for sql in captured:
            assert "LEFT(f.account_number_group, 2) IN ('10')" in sql
