"""Tests for the Balance-Sheet legacy compat layer (DB-free).

Covers the BS-specific semantics that differ from the P&L:
  (a) section-aware running-sum subtotals/grandtotals on RAW signed balances
  (b) display sign flip of the credit side (equity & liabilities → positive)
  (c) net-profit injection into the equity section (presented income +)
  (d) equity-ratio KPI = |equity| / |total assets| * 100 (from RAW balances)
  (e) BS / P&L structure separation (P&L rows never leak into the BS statement)
  (f) annual snapshot deltas (delta_fy = fy − fy_py, delta_cm = cm − cm_py)
  (g) consolidation per-entity running sum + flip, monthly month-end balances
  (h) end-to-end glue through build_bs_* with a mock Session

=== WORKED EXAMPLE (month grain, cumulative balances; cm column) ===
  Structure (BS, sort_order >= 1000):
    AR        mapping asset  level_3='Trade receivables'  level_2='Current assets'
    INVENTORY mapping asset  level_3='Inventories'        level_2='Current assets'
    BS_TOTAL_CURRENT_ASSETS subtotal  asset  ("Total current assets")
    BS_GRANDTOTAL_ASSETS    grandtotal asset ("Total assets")
    AP        mapping credit level_3='Trade payables'     level_2='Liabilities'
    BS_TOTAL_LIABILITIES    subtotal  credit ("Total liabilities")
    EQUITY    mapping credit level_3='Retained earnings'  level_2='Equity'
    BS_TOTAL_EQUITY         subtotal  credit ("Total equity")
    BS_GRANDTOTAL_EQ_LIAB   grandtotal credit ("Total equity & liabilities")
  Raw cumulative cm balances (assets +, credit −):
    AR=+600  INVENTORY=+400  AP=−400  EQUITY=−500
  Section running sums:
    Total current assets = 600 + 400          = +1000
    Total assets         = Σ asset cum         = +1000
    Total liabilities    = −400               (raw)  → display +400
    Total equity         = −500               (raw)  → display +500
    Total equity & liab. = −400 + −500 = −900 (raw)  → display +900
  Net profit cm = +100 (P&L YTD, presented) → injected into Total equity:
    Total equity display = 500 + 100 = 600, with a "Net profit" child = +100.
    Total equity & liab. display = 900 + 100 = 1000.
  Equity ratio (RAW, pre-injection) = |−500| / |1000| * 100 = 50.0 %.
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


def _walk_rows(rows):
    for r in rows:
        yield r
        yield from _walk_rows(r.get("children") or [])


def _row_by_label(rows, label: str):
    for r in _walk_rows(rows):
        if r.get("label") == label:
            return r
    return None


def _row_by_line_code(rows, line_code: str):
    for r in _walk_rows(rows):
        if r.get("line_code") == line_code:
            return r
    return None


# ---------------------------------------------------------------------------
# Synthetic BS structure (sort_order >= 1000, kpi_code 'BS:<section>')
# ---------------------------------------------------------------------------
def _bs_struct(pl_line_id, sort_order, line_code, row_type, section, *,
               balance_title=None, level_2=None, level_3=None, level_4=None,
               is_bold=False):
    return {
        "pl_line_id": pl_line_id, "sort_order": sort_order, "line_code": line_code,
        "row_type": row_type, "balance_title": balance_title or line_code,
        "details": None, "calc_type": None,
        "level_2": level_2, "level_3": level_3, "level_4": level_4,
        "gl_account_id": None, "invert_delta": False, "is_bold": is_bold,
        "kpi_code": f"BS:{section}",
    }


_BS_STRUCTURE = [
    _bs_struct(101, 1010, "AR", "mapping", "asset",
               balance_title="Trade receivables", level_2="Current assets", level_3="Trade receivables"),
    _bs_struct(102, 1020, "INVENTORY", "mapping", "asset",
               balance_title="Inventories", level_2="Current assets", level_3="Inventories"),
    _bs_struct(103, 1030, "BS_TOTAL_CURRENT_ASSETS", "subtotal", "asset",
               balance_title="Total current assets", level_2="Current assets", is_bold=True),
    _bs_struct(104, 1040, "BS_GRANDTOTAL_ASSETS", "grandtotal", "asset",
               balance_title="Total assets", is_bold=True),
    _bs_struct(105, 1050, "AP", "mapping", "credit",
               balance_title="Trade payables", level_2="Liabilities", level_3="Trade payables"),
    _bs_struct(106, 1060, "BS_TOTAL_LIABILITIES", "subtotal", "credit",
               balance_title="Total liabilities", level_2="Liabilities", is_bold=True),
    _bs_struct(107, 1070, "EQUITY", "mapping", "credit",
               balance_title="Retained earnings", level_2="Equity", level_3="Retained earnings"),
    _bs_struct(108, 1080, "BS_TOTAL_EQUITY", "subtotal", "credit",
               balance_title="Total equity", level_2="Equity", is_bold=True),
    _bs_struct(109, 1090, "BS_GRANDTOTAL_EQ_LIAB", "grandtotal", "credit",
               balance_title="Total equity & liabilities", is_bold=True),
]

# A P&L row (sort_order < 1000, no BS marker) that must NEVER appear in the BS.
_PL_ROW = {
    "pl_line_id": 1, "sort_order": 10, "line_code": "NET_SALES", "row_type": "mapping",
    "balance_title": "Net Sales", "details": None, "calc_type": None,
    "level_2": "Income", "level_3": "Net sales", "level_4": None,
    "gl_account_id": None, "invert_delta": False, "is_bold": True, "kpi_code": None,
}


def _bs_grain(level_1, level_2, level_3, gl_account_id, **cols):
    g = {
        "level_1": level_1,
        "level_2": level_2,
        "level_3": level_3,
        "level_4": "—",
        "gl_account_id": gl_account_id,
        "account_name": level_3,
        "level_2_sort": 10,
        "level_3_sort": 10,
        "py_cm": 0.0, "pm": 0.0, "cm": 0.0, "ytd": 0.0, "ytd_py": 0.0,
    }
    g.update(cols)
    return g


# Raw cumulative balances (assets +, credit −).
_BS_GRAIN = [
    _bs_grain("Assets", "Current assets", "Trade receivables", "AT1200",
              cm=600, pm=550, py_cm=500, ytd=600, ytd_py=500),
    _bs_grain("Assets", "Current assets", "Inventories", "AT1400",
              cm=400, pm=400, py_cm=350, ytd=400, ytd_py=350),
    _bs_grain("Equity & liabilities", "Liabilities", "Trade payables", "AT3300",
              cm=-400, pm=-380, py_cm=-300, ytd=-400, ytd_py=-300),
    _bs_grain("Equity & liabilities", "Equity", "Retained earnings", "AT2800",
              cm=-500, pm=-450, py_cm=-400, ytd=-500, ytd_py=-400),
]

# P&L YTD net profit at each balance date (presented income +).
_NET_PROFIT = {"py_cm": 60.0, "pm": 80.0, "cm": 100.0, "ytd": 100.0, "ytd_py": 60.0}

_MONTH_KEYS = ["py_cm", "pm", "cm", "ytd", "ytd_py"]


# ---------------------------------------------------------------------------
# Mock Session: dim_pl_structure → structure; level_0='PL' grain → net profit;
# any other grain → BS grain.  fetchone() returns the first row (net profit).
# ---------------------------------------------------------------------------
def _mock_session(structure=None, grain_rows=None, net_profit=None,
                  net_profit_rows=None, entity_rows=None, snapshot=False):
    session = MagicMock()
    structure = structure if structure is not None else _BS_STRUCTURE
    grain_rows = grain_rows if grain_rows is not None else _BS_GRAIN
    np_map = net_profit if net_profit is not None else _NET_PROFIT

    def _execute(stmt, params=None):
        sql = str(stmt)
        result = MagicMock()
        rows: list[Any] = []
        if any(t in sql for t in ("dim_pl_structure", "dim_bs_structure", "dim_cf_structure")):
            rows = [_dict_row(r) for r in structure]
        elif "dim_legal_entity" in sql:
            rows = entity_rows or [
                _dict_row({"legal_entity_code": "AT", "entity_prefix": "AT",
                           "entity_name": "Austria GmbH"}),
            ]
        elif "a.level_0 = 'PL'" in sql:
            if "GROUP BY l.entity_prefix" in sql:
                rows = [_dict_row(r) for r in (net_profit_rows or [])]
            else:
                rows = [_dict_row(dict(np_map))]
        else:
            rows = [_dict_row(r) for r in grain_rows]
        result.fetchall.return_value = rows
        result.fetchone.return_value = rows[0] if rows else None
        return result

    session.execute.side_effect = _execute
    return session


# ---------------------------------------------------------------------------
# (a)+(b)+(c)+(d) Statement: running sum, flip, net profit, equity ratio
# ---------------------------------------------------------------------------
class TestBsStatement:

    def _build(self):
        from app.services.fin_compat_bs import build_bs_statement_compat
        session = _mock_session()
        out = build_bs_statement_compat(session, period_grain="month", year=2025, month=7)
        return out["rows"], out

    def test_envelope(self):
        _, out = self._build()
        assert out["statement"] == "bs"
        assert out["period_grain"] == "month"
        assert out["year"] == 2025 and out["month"] == 7
        for k in _MONTH_KEYS:
            assert k in out["col_labels"]

    def test_asset_side_keeps_sign(self):
        """Assets present at the raw (positive) balance — no flip."""
        rows, _ = self._build()
        ar = _row_by_label(rows, "Trade receivables")
        inv = _row_by_label(rows, "Inventories")
        assert ar is not None and ar["amounts"]["cm"] == 600
        assert inv is not None and inv["amounts"]["cm"] == 400

    def test_section_running_sum_assets(self):
        """L2 Current assets = AR + INVENTORY; L1 Assets = same total."""
        rows, _ = self._build()
        cur = _row_by_label(rows, "Current assets")
        assets = _row_by_label(rows, "Assets")
        assert cur is not None and cur["amounts"]["cm"] == 1000
        assert assets is not None and assets["amounts"]["cm"] == 1000
        assert cur["row_kind"] == "subtotal"

    def test_credit_side_display_flip(self):
        """Equity & liabilities present POSITIVE (raw negative, flipped once)."""
        rows, _ = self._build()
        ap = _row_by_label(rows, "Trade payables")
        eq_l3 = _row_by_label(rows, "Retained earnings")
        assert ap is not None and ap["amounts"]["cm"] == 400
        assert eq_l3 is not None and eq_l3["amounts"]["cm"] == 500

    def test_grandtotal_eq_liab_running_sum(self):
        """L1 Equity & liabilities includes injected net profit."""
        rows, _ = self._build()
        el = _row_by_label(rows, "Equity & liabilities")
        assert el is not None and el["amounts"]["cm"] == 1000

    def test_net_profit_injected_into_equity(self):
        """Net profit (presented +) added to Equity L2 + appended as a child."""
        rows, _ = self._build()
        eq = _row_by_label(rows, "Equity")
        el = _row_by_label(rows, "Equity & liabilities")
        assert eq is not None
        assert eq["amounts"]["cm"] == 600
        assert eq["has_children"] is True
        np_child = next(c for c in eq["children"] if c["line_code"] == "NET_PROFIT")
        assert np_child["amounts"]["cm"] == 100
        assert np_child["label"] == "Net profit"
        assert el is not None and el["amounts"]["cm"] == eq["amounts"]["cm"] + 400

    def test_equity_ratio_kpi(self):
        """Equity ratio = |equity raw| / |assets raw| * 100, EXCLUDING net profit."""
        rows, _ = self._build()
        assert rows[-2]["row_kind"] == "kpi_header"
        assert rows[-2]["label"] == "KPIs"
        er = _row_by_line_code(rows, "EQUITY_RATIO")
        assert er is not None
        assert er["row_kind"] == "kpi"
        assert er["amounts"]["cm"] == 50.0
        assert er["amounts"]["ytd"] == 50.0

    def test_bs_display_order(self):
        from app.services.fin_compat_bs import _sort_bs_display_order

        rows = [
            {
                "label": "Assets",
                "children": [
                    {
                        "label": "Current assets",
                        "children": [
                            {"label": "Other assets", "children": []},
                            {"label": "Inventories", "children": []},
                        ],
                    },
                    {
                        "label": "Fixed assets",
                        "children": [
                            {"label": "Financial assets", "children": []},
                            {"label": "Tangible assets", "children": []},
                        ],
                    },
                    {"label": "Deferred tax assets", "children": []},
                    {"label": "Prepaid expenses", "children": []},
                ],
            },
            {
                "label": "Equity & liabilities",
                "children": [
                    {"label": "Provisions & accruals", "children": []},
                    {"label": "Equity", "children": []},
                    {"label": "Liabilities", "children": []},
                ],
            },
        ]
        _sort_bs_display_order(rows)
        assets = rows[0]["children"]
        assert [c["label"] for c in assets] == [
            "Fixed assets",
            "Current assets",
            "Prepaid expenses",
            "Deferred tax assets",
        ]
        assert [c["label"] for c in assets[0]["children"]] == [
            "Tangible assets",
            "Financial assets",
        ]
        assert [c["label"] for c in assets[1]["children"]] == [
            "Inventories",
            "Other assets",
        ]
        eql = rows[1]["children"]
        assert [c["label"] for c in eql] == [
            "Equity",
            "Liabilities",
            "Provisions & accruals",
        ]

    def test_credit_deltas_flipped(self):
        """Credit deltas flip with the amounts: equity MoM = +50 (display)."""
        rows, _ = self._build()
        eq = _row_by_label(rows, "Equity")
        ar = _row_by_label(rows, "Trade receivables")
        assert eq is not None and eq["deltas"]["mom"] == 50
        assert eq["deltas"]["yoy"] == 100
        assert ar is not None and ar["deltas"]["mom"] == 50
        assert ar["deltas"]["yoy"] == 100

    def test_no_pl_rows_leak(self):
        """A P&L structure row must never appear in the BS statement."""
        from app.services.fin_compat_bs import build_bs_statement_compat
        struct = [_dict_row(_PL_ROW)] + [_dict_row(r) for r in _BS_STRUCTURE]
        # Re-wrap as plain dict rows for the mock loader.
        session = _mock_session(structure=[_PL_ROW] + _BS_STRUCTURE)
        out = build_bs_statement_compat(session, period_grain="month", year=2025, month=7)
        codes = {r["line_code"] for r in out["rows"]}
        assert "NET_SALES" not in codes


# ---------------------------------------------------------------------------
# (a2) Whole-group statement BALANCES (Total assets == Total equity & liabilities)
#
# Root-cause regression: build_bs_statement_compat (entity=None) MUST tie out the
# same way build_bs_consolidation does.  The bug was that bs_grain_sql_month/week
# used the LIFETIME-hybrid balance (_bal_case), which — under
# OPENING_BALANCE_MODE=in_data — dropped every per-FY Jan-1 retained-earnings
# carry-forward from equity while keeping all cumulative asset movements, so the
# single statement's equity base was understated (negative) and Assets ≠ E&L.  The
# fix scopes each column to its fiscal year (_bal_case_fy), matching the
# consolidation / snapshot / monthly / trial-balance grains.
#
# FORMULA (why FY-scoping ties out, per column k, raw signs assets +, credit −):
#   all_bs_raw[k] = Σ_BS[ OB(FY)@Jan-1  +  in-FY BS movements ≤ cutoff ]
#     Σ OB(FY) = 0                         (balanced opening snapshot incl. Gewinnvortrag)
#     Σ_BS(movements) = −Σ_PL(movements)   (every journal entry balances BS+PL)
#   net_profit[k] = Σ_PL(amount × −1) = −Σ_PL(raw)
#   ⇒ all_bs_raw[k] = net_profit[k]  ⇒  imbalance = all_bs_raw − net_profit = 0.
#
# WORKED EXAMPLE (cm column, balanced ledger below):
#   assets cm = 600 + 400 = 1000 ; credit raw cm = −400 + −520 = −920
#   Σ raw BS = 1000 − 920 = 80 ; Σ P&L YTD = 80  → total_eq_liab = 920 + 80 = 1000
#   → Total assets (1000) == Total equity & liabilities (1000).  imbalance = 0.
# ---------------------------------------------------------------------------
# Balanced whole-group ledger: Σ raw BS per column == the P&L net profit per column.
_BS_GRAIN_BALANCED = [
    _bs_grain("Assets", "Current assets", "Trade receivables", "AT1200",
              cm=600, pm=550, py_cm=500, ytd=600, ytd_py=500, mtd=600),
    _bs_grain("Assets", "Current assets", "Inventories", "AT1400",
              cm=400, pm=400, py_cm=350, ytd=400, ytd_py=350, mtd=400),
    _bs_grain("Equity & liabilities", "Liabilities", "Trade payables", "AT3300",
              cm=-400, pm=-350, py_cm=-300, ytd=-400, ytd_py=-300, mtd=-400),
    _bs_grain("Equity & liabilities", "Equity", "Retained earnings", "AT2800",
              cm=-520, pm=-520, py_cm=-480, ytd=-520, ytd_py=-480, mtd=-520),
]
# P&L net profit == Σ raw BS per column (the accounting identity FY-scoping yields).
_NET_PROFIT_BALANCED = {"py_cm": 70.0, "pm": 80.0, "cm": 80.0, "ytd": 80.0,
                        "ytd_py": 70.0, "mtd": 80.0}


class TestBsStatementBalances:
    """Total assets == Total equity & liabilities for the whole-group statement."""

    def _balance_check(self, out):
        return out["balance_check"]

    def _assert_balanced(self, out, keys):
        bc = self._balance_check(out)
        for k in keys:
            assert abs(bc["total_assets"][k] - bc["total_eq_liab"][k]) < 0.01, (
                f"column {k}: assets {bc['total_assets'][k]} != "
                f"E&L {bc['total_eq_liab'][k]}"
            )
            assert abs(bc["imbalance"][k]) < 0.01
        assert bc["is_balanced"] is True

    def test_monthly_statement_balances(self):
        from app.services.fin_compat_bs import build_bs_statement_compat
        session = _mock_session(
            grain_rows=_BS_GRAIN_BALANCED, net_profit=_NET_PROFIT_BALANCED,
        )
        out = build_bs_statement_compat(session, period_grain="month", year=2025, month=6)
        self._assert_balanced(out, _MONTH_KEYS)

    def test_weekly_statement_balances(self):
        from app.services.fin_compat_bs import build_bs_statement_compat
        session = _mock_session(
            grain_rows=_BS_GRAIN_BALANCED, net_profit=_NET_PROFIT_BALANCED,
        )
        out = build_bs_statement_compat(
            session, period_grain="week", iso_year=2025, iso_week=26,
        )
        self._assert_balanced(out, _MONTH_KEYS + ["mtd"])


class TestBsStatementGrainIsFyScoped:
    """The single statement grain SQL must be FY-scoped (same as consolidation).

    Guards against a regression to the lifetime-hybrid ``_bal_case`` path, which
    does NOT balance under OPENING_BALANCE_MODE=in_data.  Mirrors the snapshot's
    ``test_snapshot_dec_columns_use_year_end_cutoff``.
    """

    def test_month_grain_fy_scoped(self):
        from datetime import date

        from app.services.fin_compat_bs_sql import _bal_amount_expr_fy, bs_grain_sql_month

        sql, _ = bs_grain_sql_month(2025, 6, "")
        # cm / ytd columns are the FY(year) balance at the month-end cutoff.
        assert _bal_amount_expr_fy(2025, date(2025, 6, 30)) in sql
        assert "e.fiscal_year = 2025" in sql
        # py_cm / ytd_py columns are the FY(year-1) balance one year earlier.
        assert "e.fiscal_year = 2024" in sql
        # NOT the lifetime-hybrid path (bal_mov CTE) that caused the imbalance.
        assert "bal_mov AS" not in sql

    def test_week_grain_fy_scoped(self):
        from app.services.fin_compat_bs_sql import bs_grain_sql_week

        sql, _ = bs_grain_sql_week(2025, 26, "")
        assert "e.fiscal_year = 2025" in sql   # cm / ytd / mtd / pm
        assert "e.fiscal_year = 2024" in sql   # py_cm / ytd_py (same week prior year)
        assert "bal_mov AS" not in sql


# ---------------------------------------------------------------------------
# (e) Structure filter: BS vs P&L row classification
# ---------------------------------------------------------------------------
class TestStructureFilter:

    def test_is_bs_structure_row(self):
        from app.services.fin_compat_bs import _is_bs_structure_row
        assert _is_bs_structure_row({"line_code": "AR", "sort_order": 1010, "kpi_code": "BS:asset"})
        assert _is_bs_structure_row({"line_code": "BS_CASH", "sort_order": 5, "kpi_code": None})
        assert _is_bs_structure_row({"line_code": "CASH", "sort_order": 0, "kpi_code": None})
        assert not _is_bs_structure_row({"line_code": "NET_SALES", "sort_order": 10, "kpi_code": None})

    def test_bs_section(self):
        from app.services.fin_compat_bs import _bs_section
        assert _bs_section({"kpi_code": "BS:asset"}) == "asset"
        assert _bs_section({"kpi_code": "BS:credit"}) == "credit"
        # Fallback by keyword when kpi_code absent.
        assert _bs_section({"kpi_code": None, "balance_title": "Trade payables"}) == "credit"
        assert _bs_section({"kpi_code": None, "balance_title": "Cash"}) == "asset"


# ---------------------------------------------------------------------------
# (f) Annual snapshot (fy_py / fy / cm_py / cm) — deltas + flip + net profit
# ---------------------------------------------------------------------------
_SNAP_KEYS = ["dec_py2", "fy_py", "fy", "cm_py", "cm"]


def _bs_snap_grain(level_1, level_2, level_3, gl_account_id, **cols):
    g = {
        "level_1": level_1,
        "level_2": level_2,
        "level_3": level_3,
        "level_4": "—",
        "gl_account_id": gl_account_id,
        "account_name": level_3,
        "level_2_sort": 10,
        "level_3_sort": 10,
        "dec_py2": 0.0, "fy_py": 0.0, "fy": 0.0, "cm_py": 0.0, "cm": 0.0,
    }
    g.update(cols)
    return g


_SNAP_GRAIN = [
    _bs_snap_grain("Assets", "Current assets", "Trade receivables", "AT1200",
                   fy_py=400, fy=500, cm_py=550, cm=600),
    _bs_snap_grain("Assets", "Current assets", "Inventories", "AT1400",
                   fy_py=300, fy=350, cm_py=380, cm=400),
    _bs_snap_grain("Equity & liabilities", "Liabilities", "Trade payables", "AT3300",
                   fy_py=-250, fy=-300, cm_py=-360, cm=-400),
    _bs_snap_grain("Equity & liabilities", "Equity", "Retained earnings", "AT2800",
                   fy_py=-350, fy=-400, cm_py=-450, cm=-500),
]
_SNAP_NP = {"fy_py": 50.0, "fy": 80.0, "cm_py": 90.0, "cm": 120.0}


class TestBsSnapshot:

    def _build(self):
        from app.services.fin_compat_bs import build_bs_snapshot_annual
        session = _mock_session(grain_rows=_SNAP_GRAIN, net_profit=_SNAP_NP)
        out = build_bs_snapshot_annual(session, year=2025, month=7)
        return out["rows"], out

    def test_col_labels(self):
        _, out = self._build()
        assert out["statement"] == "bs"
        assert out["col_labels"] == {
            "dec_py2": "Dec22A",
            "fy_py": "Dec23A",
            "fy": "Dec24A",
            "cm_py": "Jul24A",
            # BS is point-in-time (Stichtag) → anchor-month forecast label, not "FY..F".
            "fy_f": "Jul25F",
            "cm": "Jul25A",
        }

    def test_assets_running_sum_and_signs(self):
        rows, _ = self._build()
        assets = _row_by_label(rows, "Assets")
        el = _row_by_label(rows, "Equity & liabilities")
        assert assets is not None and assets["amounts"]["cm"] == 1000
        assert assets["amounts"]["fy"] == 850
        assert el is not None and el["amounts"]["cm"] == 1020

    def test_snapshot_deltas(self):
        """delta_fy = fy − fy_py ; delta_cm = cm − cm_py (on display amounts)."""
        rows, _ = self._build()
        ta = _row_by_label(rows, "Assets")
        assert ta is not None
        assert ta["deltas"]["delta_fy"] == 150
        assert ta["deltas"]["delta_cm"] == 70

    def test_net_profit_into_equity(self):
        rows, _ = self._build()
        eq = _row_by_label(rows, "Equity")
        el = _row_by_label(rows, "Equity & liabilities")
        assert eq is not None and eq["amounts"]["cm"] == 620
        np_child = next(c for c in eq["children"] if c["line_code"] == "NET_PROFIT")
        assert np_child["amounts"]["cm"] == 120
        assert el is not None and el["amounts"]["cm"] == 1020

    def test_equity_ratio(self):
        rows, _ = self._build()
        er = _row_by_line_code(rows, "EQUITY_RATIO")
        assert er is not None and er["amounts"]["cm"] == 50.0

    def test_snapshot_dec_columns_use_year_end_cutoff(self):
        """Dec22A/23A/24A columns use Dec-31 balances, not Jan-1 Saldovortrag date."""
        from datetime import date

        from app.services.fin_compat_bs_sql import (
            _bal_amount_expr_fy,
            _bs_snapshot_year_end_cutoffs,
            bs_snapshot_grain_sql,
        )

        d_dec, d_fy_py, d_fy = _bs_snapshot_year_end_cutoffs(2025)
        assert d_dec == date(2022, 12, 31)
        assert d_fy_py == date(2023, 12, 31)
        assert d_fy == date(2024, 12, 31)
        sql, _ = bs_snapshot_grain_sql(2025, 7, "")
        assert "2024-12-31" in sql
        assert "e.posting_date <= '2024-12-31'" in sql or "posting_date <= '2024-12-31'" in sql
        assert "bal_mov AS" not in sql
        assert _bal_amount_expr_fy(2025, date(2025, 7, 31)) in sql
        assert "e.fiscal_year = 2025" in sql

# ---------------------------------------------------------------------------
# (g) Consolidation: per-entity running sum + flip + equity ratio
# ---------------------------------------------------------------------------
def _consl_grain(level_1, level_2, level_3, ang, entity_prefix, cm, **extra):
    return {
        "level_1": level_1,
        "level_2": level_2,
        "level_3": level_3,
        "level_4": None,
        "account_number_group": ang,
        "entity_prefix": entity_prefix,
        "gl_account_id": ang,
        "account_name": level_3,
        "level_2_sort": 10,
        "level_3_sort": 10,
        "cm": float(cm),
        **extra,
    }


_CONSL_GRAIN = [
    _consl_grain("Assets", "Current assets", "Trade receivables", "AT1200", "AT", 600),
    _consl_grain("Assets", "Current assets", "Inventories", "AT1400", "AT", 400),
    _consl_grain("Equity & liabilities", "Liabilities", "Trade payables", "AT3300", "AT", -400),
    _consl_grain("Equity & liabilities", "Equity", "Retained earnings", "AT2800", "AT", -500),
    _consl_grain("Assets", "Current assets", "Trade receivables", "DE1200", "DE", 300),
    _consl_grain("Equity & liabilities", "Liabilities", "Trade payables", "DE3300", "DE", -100),
]
_CONSL_NP_ROWS = [
    {"entity_prefix": "AT", "cm": 100.0},
    {"entity_prefix": "DE", "cm": 0.0},
]
_CONSL_ENTS = [
    _dict_row({"legal_entity_code": "AT", "entity_prefix": "AT", "entity_name": "Austria GmbH"}),
    _dict_row({"legal_entity_code": "DE", "entity_prefix": "DE", "entity_name": "Germany GmbH"}),
]


class TestBsConsolidation:

    def _build(self):
        from app.services.fin_compat_bs import build_bs_consolidation
        session = _mock_session(
            grain_rows=_CONSL_GRAIN,
            entity_rows=_CONSL_ENTS,
            net_profit_rows=_CONSL_NP_ROWS,
        )
        out = build_bs_consolidation(session, period_grain="month", year=2025, month=7)
        return out["rows"], out

    def test_per_entity_assets(self):
        rows, _ = self._build()
        assets = _row_by_label(rows, "Assets")
        assert assets is not None
        assert assets["entity_amounts"] == {"AT": 1000, "DE": 300}
        assert assets["aggregated"] == 1300
        assert assets["consolidation"] == 1300
        assert assets["ic_eliminations"] == 0.0

    def test_credit_flip(self):
        rows, _ = self._build()
        el = _row_by_label(rows, "Equity & liabilities")
        assert el is not None
        # Per-entity BS bridge closes Assets = E&L for each entity column.
        assert el["entity_amounts"] == {"AT": 1000, "DE": 300}
        assert el["aggregated"] == 1300

    def test_net_profit_injected(self):
        rows, _ = self._build()
        eq = _row_by_label(rows, "Equity")
        el = _row_by_label(rows, "Equity & liabilities")
        assert eq is not None and eq["entity_amounts"]["AT"] == 600
        np_child = next(c for c in eq["children"] if c.get("line_code") == "NET_PROFIT")
        assert np_child["entity_amounts"]["AT"] == 100
        assert el is not None and el["entity_amounts"]["AT"] == 1000

    def test_equity_ratio_consolidation(self):
        rows, _ = self._build()
        assert _row_by_label(rows, "Equity ratio") is not None
        er = next(r for r in _walk_rows(rows) if r.get("id") == "bs-kpi-equity-ratio")
        # Group: Σ equity raw = −500 (AT) + 0 (DE) ; Σ assets raw = 1300 → 38.46%
        assert er["consolidation"] == pytest.approx(38.46, abs=0.01)
        # Per entity: AT |−500|/1000 = 50% ; DE has no equity grain → 0%
        assert er["entity_amounts"]["AT"] == pytest.approx(50.0, abs=0.01)
        assert er["entity_amounts"]["DE"] == 0.0


def _consl_grain_snap(level_1, level_2, level_3, ang, entity_prefix, dec_py2, fy_py, fy, cm_py, cm):
    return {
        "level_1": level_1,
        "level_2": level_2,
        "level_3": level_3,
        "level_4": None,
        "account_number_group": ang,
        "entity_prefix": entity_prefix,
        "gl_account_id": ang,
        "account_name": level_3,
        "level_2_sort": 10,
        "level_3_sort": 10,
        "dec_py2": float(dec_py2), "fy_py": float(fy_py), "fy": float(fy),
        "cm_py": float(cm_py), "cm": float(cm),
    }


_CONSL_GRAIN_ANNUAL = [
    _consl_grain_snap("Assets", "Current assets", "Trade receivables", "AT1200", "AT", 500, 550, 600, 580, 600),
    _consl_grain_snap("Assets", "Current assets", "Inventories", "AT1400", "AT", 350, 380, 400, 390, 400),
    _consl_grain_snap("Equity & liabilities", "Liabilities", "Trade payables", "AT3300", "AT", -350, -380, -400, -390, -400),
    _consl_grain_snap("Equity & liabilities", "Equity", "Retained earnings", "AT2800", "AT", -450, -480, -500, -490, -500),
    _consl_grain_snap("Assets", "Current assets", "Trade receivables", "DE1200", "DE", 250, 280, 300, 290, 300),
    _consl_grain_snap("Equity & liabilities", "Liabilities", "Trade payables", "DE3300", "DE", -80, -90, -100, -95, -100),
]
_CONSL_NP_ANNUAL = [
    {"entity_prefix": "AT", "dec_py2": 0, "fy_py": 0, "fy": 0, "cm_py": 0, "cm": 120},
    {"entity_prefix": "DE", "dec_py2": 0, "fy_py": 0, "fy": 0, "cm_py": 0, "cm": 0},
]


class TestBsConsolidationAnnual:

    def _build(self):
        from app.services.fin_compat_bs import build_bs_consolidation
        session = _mock_session(
            grain_rows=_CONSL_GRAIN_ANNUAL,
            entity_rows=_CONSL_ENTS,
            net_profit_rows=_CONSL_NP_ANNUAL,
        )
        out = build_bs_consolidation(session, period_grain="year", year=2025, month=7)
        return out["rows"], out

    def test_col_labels_and_period_amounts(self):
        rows, out = self._build()
        assert out["col_labels"]["dec_py2"] == "Dec22A"
        assert out["col_labels"]["cm"] == "Jul25A"
        assets = _row_by_label(rows, "Assets")
        assert assets is not None
        assert assets["entity_periods"]["AT"]["dec_py2"] == 850
        assert assets["entity_periods"]["AT"]["fy"] == 1000
        assert assets["entity_periods"]["AT"]["cm"] == 1000
        assert assets["aggregated_periods"]["fy"] == 1300
        assert assets["consolidation_periods"]["cm"] == 1300

    def test_credit_flip_periods(self):
        rows, _ = self._build()
        el = _row_by_label(rows, "Equity & liabilities")
        assert el is not None
        assert el["entity_periods"]["AT"]["cm"] == 1000
        assert el["aggregated_periods"]["cm"] == 1300


# ---------------------------------------------------------------------------
# (h) Monthly: month-END cumulative balances + per-period equity ratio
# ---------------------------------------------------------------------------
def _monthly_grain(level_1, level_2, level_3, gl_account_id, vals):
    g = {
        "level_1": level_1,
        "level_2": level_2,
        "level_3": level_3,
        "level_4": "—",
        "gl_account_id": gl_account_id,
        "account_name": level_3,
        "level_2_sort": 10,
        "level_3_sort": 10,
    }
    g.update(vals)
    return g


class TestBsMonthly:

    def _build(self):
        from app.services.fin_compat_bs import build_bs_monthly
        from app.services.fin_compat_sql import _last_12_periods, period_key
        keys = [period_key(y, m) for y, m in _last_12_periods(2025, 7)]
        anchor = keys[-1]
        grains = [
            _monthly_grain("Assets", "Current assets", "Trade receivables", "AT1200", {k: 600 for k in keys}),
            _monthly_grain("Assets", "Current assets", "Inventories", "AT1400", {k: 400 for k in keys}),
            _monthly_grain("Equity & liabilities", "Equity", "Retained earnings", "AT2800", {k: -500 for k in keys}),
        ]
        session = _mock_session(grain_rows=grains)
        out = build_bs_monthly(session, year=2025, month=7)
        return out["rows"], out, anchor

    def test_month_end_balances(self):
        rows, out, anchor = self._build()
        assert len(out["periods"]) == 12
        assets = _row_by_label(rows, "Assets")
        assert assets is not None and assets["amounts"][anchor] == 1000

    def test_credit_flip_monthly(self):
        rows, _, anchor = self._build()
        eq = _row_by_label(rows, "Retained earnings")
        assert eq is not None and eq["amounts"][anchor] == 500

    def test_equity_ratio_per_period(self):
        rows, _, anchor = self._build()
        er = _row_by_line_code(rows, "EQUITY_RATIO")
        assert er is not None and er["amounts"][anchor] == 50.0

    def test_monthly_net_profit_into_equity(self):
        from app.services.fin_compat_sql import _last_12_periods, period_key

        keys = [period_key(y, m) for y, m in _last_12_periods(2025, 7)]
        anchor = keys[-1]
        np_map = {k: 120.0 if k == anchor else 80.0 for k in keys}
        grains = [
            _monthly_grain("Assets", "Current assets", "Trade receivables", "AT1200", {k: 600 for k in keys}),
            _monthly_grain("Assets", "Current assets", "Inventories", "AT1400", {k: 400 for k in keys}),
            _monthly_grain("Equity & liabilities", "Equity", "Retained earnings", "AT2800", {k: -500 for k in keys}),
        ]
        session = _mock_session(grain_rows=grains, net_profit=np_map)
        from app.services.fin_compat_bs import build_bs_monthly

        out = build_bs_monthly(session, year=2025, month=7)
        rows = out["rows"]
        eq = _row_by_label(rows, "Equity")
        el = _row_by_label(rows, "Equity & liabilities")
        assert eq is not None and eq["amounts"][anchor] == 620
        np_child = next(c for c in eq["children"] if c["line_code"] == "NET_PROFIT")
        assert np_child["amounts"][anchor] == 120
        assert el is not None and el["amounts"][anchor] == 620


# ---------------------------------------------------------------------------
# Pure running-sum core (no DB at all)
# ---------------------------------------------------------------------------
class TestBsRunningCore:

    def test_section_aware_running_sum(self):
        from app.services.fin_compat_bs import _compute_bs_running_values, _row_dict
        struct = [_row_dict(_dict_row(r)) for r in _BS_STRUCTURE]
        mapping = {
            "AR": {"cm": 600.0}, "INVENTORY": {"cm": 400.0},
            "AP": {"cm": -400.0}, "EQUITY": {"cm": -500.0},
        }
        out = _compute_bs_running_values(struct, mapping, ["cm"])
        assert out["BS_TOTAL_CURRENT_ASSETS"]["cm"] == 1000.0
        assert out["BS_GRANDTOTAL_ASSETS"]["cm"] == 1000.0
        # credit grandtotal = Σ credit mappings only (NOT mixed with assets)
        assert out["BS_TOTAL_LIABILITIES"]["cm"] == -400.0
        assert out["BS_TOTAL_EQUITY"]["cm"] == -500.0
        assert out["BS_GRANDTOTAL_EQ_LIAB"]["cm"] == -900.0


# ---------------------------------------------------------------------------
# L4 trend SQL aliases must match window pk keys (regression: double-quoted aliases → all zeros)
# ---------------------------------------------------------------------------
class TestBsL4TrendSql:

    def test_l4_trend_column_aliases_match_window_keys(self):
        from app.services.fin_compat_bs_sql import bs_l4_trend_sql

        sql, windows = bs_l4_trend_sql(2025, 7, "year", "Fixed assets", "Tangible assets", "", "")
        w = windows[0]
        assert f'AS "{w["pk"]}"' in sql
        assert f'AS "{w["prev_pk"]}"' in sql
        assert '"""' not in sql

    def test_build_bs_l4_trend_reads_row_by_window_pk(self):
        from app.services.fin_compat_bs import build_bs_l4_trend
        from app.services.fin_compat_bs_sql import bs_l4_trend_sql

        _, windows = bs_l4_trend_sql(2025, 7, "year", "Fixed assets", "Tangible assets", "", "")
        w = windows[-1]
        row_data = {w["pk"]: 1234.0, w["prev_pk"]: 1000.0}
        session = MagicMock()
        session.execute.return_value.fetchall.return_value = [_dict_row(row_data)]

        out = build_bs_l4_trend(
            session,
            year=2025,
            month=7,
            grain="year",
            level_2="Fixed assets",
            level_3="Tangible assets",
        )
        last = out["series"][-1]
        assert last["current"] == 1234.0
        assert last["previous"] == 1000.0


# ---------------------------------------------------------------------------
# Current-year result = Σ P&L (NOT a balancing plug) + visible balance check
#
# FORMULA (per column k, raw grain balances; + = debit/asset, − = credit/E&L):
#   assets_raw  = Σ grain[k]  over level_1 ~ 'asset'
#   all_bs_raw  = Σ grain[k]  over ALL BS grains
#   NP (pl_sum)         = pl_net_profit[k]              (Σ(PL amount × −1))
#   NP (balancing_plug) = all_bs_raw[k]                 (== Total assets − Total E&L)
#   total_eq_liab = −(all_bs_raw − assets_raw) + NP
#   imbalance     = assets_raw − total_eq_liab = all_bs_raw − NP
#
# WORKED EXAMPLE (single 'cm' column):
#   Assets +600 +400 → assets_raw=1000 ; E&L −400 −500 → credit_raw=−900
#   → all_bs_raw = +100.
#   Σ P&L (income statement) = +70  (deliberately ≠ the +100 BS residual).
#   pl_sum  → NP=70,  imbalance = 100 − 70 = 30  (data error VISIBLE)
#   plug    → NP=100, imbalance = 100 − 100 = 0  (error HIDDEN)
# The two derivations differ by exactly the imbalance (30) — the test that
# distinguishes "Σ P&L" from "plug" requires an UNBALANCED fixture.
# ---------------------------------------------------------------------------
class TestBsCurrentYearResultAndImbalance:

    # Unbalanced fixture: Σ P&L (70) ≠ Σ raw BS residual (100).
    _GRAINS_UNBAL = [
        {"level_1": "Assets", "cm": 600.0},
        {"level_1": "Assets", "cm": 400.0},
        {"level_1": "Equity & liabilities", "cm": -400.0},
        {"level_1": "Equity & liabilities", "cm": -500.0},
    ]

    def test_pl_sum_returns_income_statement_result_not_plug(self):
        from app.services.fin_compat_bs import bs_current_year_result
        pl_np = {"cm": 70.0}
        pl_sum = bs_current_year_result(self._GRAINS_UNBAL, pl_np, ["cm"], mode="pl_sum")
        plug = bs_current_year_result(self._GRAINS_UNBAL, pl_np, ["cm"], mode="balancing_plug")
        # pl_sum == Σ P&L (70), the income statement bottom line — NOT the plug.
        assert pl_sum["cm"] == 70.0
        # balancing_plug == Σ raw BS balances (100) == Total assets − Total E&L.
        assert plug["cm"] == 100.0
        # They MUST differ on unbalanced data (else a plug is indistinguishable).
        assert pl_sum["cm"] != plug["cm"]

    def test_imbalance_visible_when_pl_sum(self):
        from app.services.fin_compat_bs import bs_imbalance_from_grains
        bc = bs_imbalance_from_grains(self._GRAINS_UNBAL, {"cm": 70.0}, ["cm"])
        assert bc["total_assets"]["cm"] == 1000.0
        assert bc["total_eq_liab"]["cm"] == 970.0        # -(-900) + 70
        assert bc["imbalance"]["cm"] == 30.0             # 100 − 70, NOT forced to 0
        assert bc["is_balanced"] is False

    def test_imbalance_zero_on_balanced_data(self):
        from app.services.fin_compat_bs import bs_imbalance_from_grains
        # Σ P&L (100) exactly equals the BS residual → balances.
        bc = bs_imbalance_from_grains(self._GRAINS_UNBAL, {"cm": 100.0}, ["cm"])
        assert bc["total_assets"]["cm"] == 1000.0
        assert bc["total_eq_liab"]["cm"] == 1000.0
        assert bc["imbalance"]["cm"] == 0.0
        assert bc["is_balanced"] is True

    def test_plug_mode_always_balances_hiding_error(self):
        from app.services.fin_compat_bs import bs_current_year_result, bs_imbalance_from_grains
        np_plug = bs_current_year_result(self._GRAINS_UNBAL, {"cm": 70.0}, ["cm"], mode="balancing_plug")
        bc = bs_imbalance_from_grains(self._GRAINS_UNBAL, np_plug, ["cm"])
        # The plug forces imbalance to 0 even though Σ P&L (70) ≠ residual (100):
        # this is exactly the behaviour the pl_sum mode + balance_check replaces.
        assert bc["imbalance"]["cm"] == 0.0
        assert bc["is_balanced"] is True

    def test_edge_loss_negative_net_profit(self):
        from app.services.fin_compat_bs import bs_imbalance_from_grains
        bc = bs_imbalance_from_grains(self._GRAINS_UNBAL, {"cm": -50.0}, ["cm"])
        assert bc["imbalance"]["cm"] == 150.0            # 100 − (−50)
        assert bc["total_eq_liab"]["cm"] == 850.0        # 900 + (−50)

    def test_edge_missing_key_and_empty_grains(self):
        from app.services.fin_compat_bs import bs_current_year_result, bs_imbalance_from_grains
        # missing 'cm' in the P&L dict → treated as 0.0, no KeyError
        assert bs_current_year_result(self._GRAINS_UNBAL, {}, ["cm"], mode="pl_sum")["cm"] == 0.0
        # empty ledger → everything 0, trivially balanced
        bc = bs_imbalance_from_grains([], {}, ["cm"])
        assert bc["total_assets"]["cm"] == 0.0
        assert bc["imbalance"]["cm"] == 0.0
        assert bc["is_balanced"] is True

    def test_multi_column_keys(self):
        from app.services.fin_compat_bs import bs_imbalance_from_grains
        grains = [
            {"level_1": "Assets", "cm": 1000.0, "fy": 900.0},
            {"level_1": "Equity & liabilities", "cm": -800.0, "fy": -820.0},
        ]
        bc = bs_imbalance_from_grains(grains, {"cm": 150.0, "fy": 80.0}, ["cm", "fy"])
        # all_bs_raw cm=200, fy=80 ; imbalance = all_bs_raw − NP
        assert bc["imbalance"]["cm"] == 50.0             # 200 − 150
        assert bc["imbalance"]["fy"] == 0.0              # 80 − 80
        assert bc["is_balanced"] is False                # any column off → False

    def test_default_mode_is_legacy_plug(self):
        from app.services.fin_compat_bs import bs_current_year_result
        # No mode passed → reads settings (default 'balancing_plug' → golden parity).
        got = bs_current_year_result(self._GRAINS_UNBAL, {"cm": 70.0}, ["cm"])
        assert got["cm"] == 100.0                        # the plug, not Σ P&L (70)
