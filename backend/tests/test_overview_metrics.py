"""Tests for the overview metrics compat layer (DB-free where possible).

Covers the three overview endpoints added on the GDPdU schema:

  (1) DuPont KPI formulas           — app.services.overview_metrics.dupont_period_kpis
  (2) EBIT-by-entity aggregation    — app.services.overview_metrics.build_ebit_rows
  (3) Top-entities ranking + deltas — app.services.overview_top_entities.rank_top_entities

plus end-to-end glue through build_dupont / build_ebit_table / build_top_entities
with a mock SQLAlchemy Session.

=== DuPont WORKED EXAMPLE (ann_month = 12 → annualisation factor 1) ===
  P&L (presented): net_sales=1000, cost_of_materials=-600, ebit=150, ebitda=200
    → gross_profit = 1000 + (-600) = 400 ; gross_margin = 400/1000 = 40.0 %
      ebitda_margin = 200/1000 = 20.0 %
  BS (ABS): total_assets=1250, equity=500, trade_receivables=200,
            trade_payables=160, inventories=120
    → ROE = 150/500*100 = 30.0 % ; ROI = 150/1250*100 = 12.0 %
      ROS = 150/1000*100 = 15.0 % ; equity_multiplier = 1250/500 = 2.5
      asset_turnover = 1000/1250 = 0.8
      DSO = 200/(1000/365) = 73.0 ; DPO = 160/(600/365) = 97.33 ; DIO = 73.0

=== EBIT WORKED EXAMPLE ===
  AT: to_cm=300 ebit_cm=40 ; DE: to_cm=200 ebit_cm=10
    → __total__: to_cm=500 ebit_cm=50  (FE derives margin 50/500 = 10 %)

=== TOP-ENTITIES WORKED EXAMPLE (rank_by='cm') ===
  A: cm=120 pm=100 py_cm=90  ytd=700 ytd_py=600
  B: cm=200 pm=150 py_cm=210 ytd=900 ytd_py=950
    → ranked [B(1), A(2)] ; B.delta_cm_pm=50 delta_cm_py=-10 delta_ytd=-50
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Row helper (SQLAlchemy Row-like with _mapping)
# ---------------------------------------------------------------------------
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


def _dict_row(d: dict) -> _DictRow:
    return _DictRow(d)


# ===========================================================================
# (1) DuPont — pure KPI formulas
# ===========================================================================
class TestDuPontKpis:

    PL = {"net_sales": 1000.0, "cost_of_materials": -600.0,
          "personnel_expenses": -200.0, "depreciation": -50.0,
          "ebit": 150.0, "ebitda": 200.0}
    BS = {"total_assets": 1250.0, "fixed_assets": 500.0, "current_assets": 750.0,
          "trade_receivables": 200.0, "trade_payables": 160.0,
          "inventories": 120.0, "cash": 80.0, "equity": 500.0}

    def _kpi(self):
        from app.services.overview_metrics import dupont_period_kpis
        return dupont_period_kpis(self.PL, self.BS, 12)

    def test_margins_and_returns(self):
        k = self._kpi()
        assert k["gross_profit"] == 400.0
        assert k["gross_margin"] == 40.0
        assert k["ebitda_margin"] == 20.0
        assert k["roe"] == 30.0
        assert k["roi"] == 12.0
        assert k["ros"] == 15.0
        assert k["equity_multiplier"] == 2.5
        assert k["asset_turnover"] == 0.8

    def test_day_metrics(self):
        k = self._kpi()
        assert k["dso"] == pytest.approx(73.0, abs=0.1)
        assert k["dpo"] == pytest.approx(97.33, abs=0.1)
        assert k["dio"] == pytest.approx(73.0, abs=0.1)

    def test_annualisation_partial_year(self):
        """ann_month=6 doubles the annualised flows → asset_turnover doubles."""
        from app.services.overview_metrics import dupont_period_kpis
        k = dupont_period_kpis(self.PL, self.BS, 6)
        # ann_ns = 1000 * 12/6 = 2000 → asset_turnover = 2000/1250 = 1.6
        assert k["asset_turnover"] == 1.6
        # DSO = 200 / (2000/365) = 36.5
        assert k["dso"] == pytest.approx(36.5, abs=0.1)

    def test_zero_denominators_return_none(self):
        from app.services.overview_metrics import dupont_period_kpis
        bs0 = dict(self.BS, equity=0.0, total_assets=0.0)
        pl0 = dict(self.PL, net_sales=0.0, cost_of_materials=0.0)
        k = dupont_period_kpis(pl0, bs0, 12)
        assert k["roe"] is None
        assert k["roi"] is None
        assert k["ros"] is None
        assert k["equity_multiplier"] is None
        assert k["asset_turnover"] is None
        assert k["gross_margin"] is None
        assert k["dso"] is None  # ann_ns == 0
        assert k["dpo"] is None  # ann_com == 0
        assert k["dio"] is None  # ann_com == 0

    def test_assemble_shape(self):
        from app.services.overview_metrics import assemble_dupont
        cur = self._kpi()
        out = assemble_dupont(cur, cur, cur, "Dec24", "Dec23", "Nov24")
        assert out["col_label"] == "Dec24"
        assert out["py_label"] == "Dec23"
        assert out["pm_label"] == "Nov24"
        assert out["metrics"]["roe"] == {"value": 30.0, "py": 30.0, "pm": 30.0}
        # every documented metric key is present
        for key in ("roe", "roi", "ros", "gross_margin", "net_sales", "equity",
                    "dso", "dpo", "dio"):
            assert key in out["metrics"]


# ===========================================================================
# (2) EBIT-by-entity — pure aggregation + total row
# ===========================================================================
class TestEbitRows:

    GRAIN = [
        {"entity_prefix": "AT", "to_cm_py": 250, "to_pm": 280, "to_cm": 300,
         "to_ytd": 2000, "ebit_cm_py": 30, "ebit_pm": 35, "ebit_cm": 40, "ebit_ytd": 250},
        {"entity_prefix": "DE", "to_cm_py": 180, "to_pm": 190, "to_cm": 200,
         "to_ytd": 1400, "ebit_cm_py": 8, "ebit_pm": 9, "ebit_cm": 10, "ebit_ytd": 70},
    ]
    NAMES = {"AT": ("AT", "Austria GmbH"), "DE": ("DE", "Germany GmbH")}

    def test_rows_and_total(self):
        from app.services.overview_metrics import build_ebit_rows
        rows = build_ebit_rows(self.GRAIN, self.NAMES)
        by = {r["entity_code"]: r for r in rows}
        assert by["AT"]["entity_name"] == "Austria GmbH"
        assert by["AT"]["to_cm"] == 300
        assert by["DE"]["ebit_cm"] == 10
        total = by["__total__"]
        assert total["to_cm"] == 500
        assert total["ebit_cm"] == 50
        assert total["to_ytd"] == 3400
        assert total["ebit_ytd"] == 320

    def test_ic_elim_when_consolidated_differs(self):
        from app.services.overview_metrics import build_ebit_rows
        consol = {
            "to_cm_py": 430, "to_pm": 470, "to_cm": 480, "to_ytd": 3400,
            "ebit_cm_py": 38, "ebit_pm": 44, "ebit_cm": 45, "ebit_ytd": 320,
        }
        rows = build_ebit_rows(self.GRAIN, self.NAMES, consolidated_grain=consol)
        by = {r["entity_code"]: r for r in rows}
        assert by["__ic_elim__"]["to_cm"] == -20
        assert by["__total__"]["to_cm"] == 480
        assert by["__total__"]["ebit_cm"] == 45

    def test_unknown_prefix_fallback(self):
        from app.services.overview_metrics import build_ebit_rows
        rows = build_ebit_rows([{"entity_prefix": "XX", "to_cm": 5}], {})
        by = {r["entity_code"]: r for r in rows}
        assert by["XX"]["entity_name"] == "XX"
        assert by["XX"]["to_cm"] == 5
        assert by["__total__"]["to_cm"] == 5

    def test_empty_grain_total_only(self):
        from app.services.overview_metrics import build_ebit_rows
        rows = build_ebit_rows([], {})
        assert len(rows) == 1
        assert rows[0]["entity_code"] == "__total__"
        assert rows[0]["to_cm"] == 0


# ===========================================================================
# (3) Top-entities — pure ranking + deltas
# ===========================================================================
class TestRankTopEntities:

    AGG = [
        {"name": "A", "customer_id": "AT0001", "cm": 120, "pm": 100, "py_cm": 90,
         "ytd": 700, "ytd_py": 600},
        {"name": "B", "customer_id": "AT0002", "cm": 200, "pm": 150, "py_cm": 210,
         "ytd": 900, "ytd_py": 950},
        {"name": "Z", "customer_id": "AT0003", "cm": 0, "pm": 0, "py_cm": 0,
         "ytd": 0, "ytd_py": 0},  # all-zero → dropped
    ]

    def test_rank_by_cm(self):
        from app.services.overview_top_entities import rank_top_entities
        rows = rank_top_entities(self.AGG, id_field="customer_id", rank_by="cm")
        assert [r["name"] for r in rows] == ["B", "A"]  # Z dropped
        b = rows[0]
        assert b["rank"] == 1
        assert b["delta_cm_pm"] == 50
        assert b["delta_cm_py"] == -10
        assert b["delta_ytd"] == -50
        assert b["plan_cm"] == 0.0
        assert b["coverage"] == 0.0
        assert b["customer_id"] == "AT0002"

    def test_rank_by_ytd(self):
        from app.services.overview_top_entities import rank_top_entities
        rows = rank_top_entities(self.AGG, id_field="customer_id", rank_by="ytd")
        assert [r["name"] for r in rows] == ["B", "A"]
        assert rows[0]["ytd"] == 900

    def test_limit(self):
        from app.services.overview_top_entities import rank_top_entities
        rows = rank_top_entities(self.AGG, id_field="customer_id", rank_by="cm", limit=1)
        assert len(rows) == 1
        assert rows[0]["name"] == "B"


# ===========================================================================
# End-to-end glue with a mock Session
# ===========================================================================
def _dupont_mock_session(pl_row: dict, bs_row: dict):
    session = MagicMock()

    def _execute(stmt, params=None):
        sql = str(stmt)
        result = MagicMock()
        if "a.level_0 = 'BS'" in sql:
            row = _dict_row(bs_row)
        else:  # PL aggregate
            row = _dict_row(pl_row)
        result.fetchone.return_value = row
        result.fetchall.return_value = [row]
        return result

    session.execute.side_effect = _execute
    return session


class TestBuildDuPontGlue:

    def test_envelope_and_values(self):
        from app.services.overview_metrics import build_dupont
        # Same values for cur/py/pm so the worked example reproduces in every column.
        pl = {}
        for k, v in {"net_sales": 1000.0, "cost_of_materials": -600.0,
                     "personnel_expenses": -200.0, "depreciation": -50.0,
                     "ebit": 150.0, "ebitda": 200.0}.items():
            for sfx in ("cur", "py", "pm"):
                pl[f"{k}_{sfx}"] = v
        bs = {}
        for k, v in {"total_assets": 1250.0, "fixed_assets": 500.0,
                     "current_assets": 750.0, "trade_receivables": 200.0,
                     "trade_payables": 160.0, "inventories": 120.0,
                     "cash": 80.0, "equity": 500.0}.items():
            for sfx in ("cur", "py", "pm"):
                bs[f"{k}_{sfx}"] = v
        session = _dupont_mock_session(pl, bs)
        out = build_dupont(session, None, 2024, 12)
        assert out["col_label"] == "Dec24"
        assert out["metrics"]["gross_margin"]["value"] == 40.0
        assert out["metrics"]["roe"]["value"] == 30.0
        # pm uses ann_month = pm_month = 11 → annualised flows differ slightly
        assert out["metrics"]["roe"]["pm"] == 30.0  # ratio independent of annualisation


def _ebit_mock_session(grain_rows: list[dict], entity_rows: list[_DictRow]):
    session = MagicMock()

    def _execute(stmt, params=None):
        sql = str(stmt)
        result = MagicMock()
        if "dim_legal_entity" in sql:
            rows: list[Any] = entity_rows
        else:
            rows = [_dict_row(g) for g in grain_rows]
        result.fetchall.return_value = rows
        result.fetchone.return_value = rows[0] if rows else None
        return result

    session.execute.side_effect = _execute
    return session


class TestBuildEbitTableGlue:

    def test_month_envelope(self):
        from app.services.overview_metrics import build_ebit_table
        grain = [
            {"entity_prefix": "AT", "to_cm_py": 250, "to_pm": 280, "to_cm": 300,
             "to_ytd": 2000, "ebit_cm_py": 30, "ebit_pm": 35, "ebit_cm": 40, "ebit_ytd": 250},
        ]
        ents = [_dict_row({"entity_prefix": "AT", "legal_entity_code": "AT",
                           "entity_name": "Austria GmbH"})]
        session = _ebit_mock_session(grain, ents)
        out = build_ebit_table(session, None, 2025, 7)
        assert out["year"] == 2025 and out["month"] == 7
        assert out["period_grain"] == "month"
        assert out["col_labels"] == {"cm_py": "Jul24A", "pm": "Jun25A",
                                     "cm": "Jul25A", "ytd": "YTDJul25A"}
        codes = {r["entity_code"] for r in out["rows"]}
        assert "AT" in codes and "__total__" in codes

    def test_week_envelope_has_year_month(self):
        from app.services.overview_metrics import build_ebit_table_week
        grain = [{"entity_prefix": "AT", "to_cm_py": 1, "to_pm": 2, "to_cm": 3,
                  "to_ytd": 4, "ebit_cm_py": 1, "ebit_pm": 1, "ebit_cm": 1, "ebit_ytd": 1}]
        ents = [_dict_row({"entity_prefix": "AT", "legal_entity_code": "AT",
                           "entity_name": "Austria GmbH"})]
        session = _ebit_mock_session(grain, ents)
        out = build_ebit_table_week(session, None, 2025, 30)
        # EbitTableData type requires year + month even in week grain.
        assert isinstance(out["year"], int) and isinstance(out["month"], int)
        assert out["period_grain"] == "week"
        assert out["iso_year"] == 2025 and out["iso_week"] == 30
        assert set(out["col_labels"]) == {"cm_py", "pm", "cm", "ytd"}


def _top_mock_session(raw_rows: list[dict]):
    session = MagicMock()

    def _execute(stmt, params=None):
        result = MagicMock()
        rows = [_dict_row(r) for r in raw_rows]
        result.fetchall.return_value = rows
        result.fetchone.return_value = rows[0] if rows else None
        return result

    session.execute.side_effect = _execute
    return session


class TestBuildTopEntitiesGlue:

    def test_customer_envelope(self):
        from app.services.overview_top_entities import build_top_entities
        raw = [
            {"partner_id": "AT0002", "name": "Big Co", "cm": 200, "pm": 150,
             "py_cm": 210, "ytd": 900, "ytd_py": 950},
            {"partner_id": "AT0001", "name": "Small Co", "cm": 120, "pm": 100,
             "py_cm": 90, "ytd": 700, "ytd_py": 600},
        ]
        session = _top_mock_session(raw)
        out = build_top_entities(session, year=2025, month=7, type="customer")
        assert out["rank_by"] == "cm"
        assert out["period_grain"] == "month"
        assert out["plan_mix"] == "py_proxy"
        assert [r["name"] for r in out["rows"]] == ["Big Co", "Small Co"]
        assert out["rows"][0]["customer_id"] == "AT0002"
        assert "supplier_id" not in out["rows"][0]
        assert set(out["col_labels"]) >= {"cm", "pm", "py_cm", "ytd", "ytd_py"}

    def test_supplier_uses_supplier_id(self):
        from app.services.overview_top_entities import build_top_entities
        raw = [{"partner_id": "AT9001", "name": "Steel Ltd", "cm": 80, "pm": 70,
                "py_cm": 60, "ytd": 400, "ytd_py": 350}]
        session = _top_mock_session(raw)
        out = build_top_entities(session, year=2025, month=7, type="supplier")
        assert out["rows"][0]["supplier_id"] == "AT9001"
        assert "customer_id" not in out["rows"][0]
