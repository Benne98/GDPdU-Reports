"""Tests for the Cash-Flow legacy compat layer (DB-free).

Covers the CF-specific semantics:
  (a) cf_mapping matching with the two delta glyphs unified (∆ U+2206 / Δ U+0394)
  (b) running-sum subtotals/calc over the flat CF structure (mirrors the P&L)
  (c) Net cash flow tie-out == Σ of every mapped CF leaf (per column)
  (d) sign convention (presented amount*-1 already applied in SQL → builder
      never re-signs; inflow +, outflow −)
  (e) CF drill keys (cf_l11_1 / cf_l11_2 / cf_mapping) + account children
  (f) unmatched cf_mapping ('Exclude', orphan leaves) dropped; a structure row
      with no matching grain → 0
  (g) end-to-end glue through build_cf_* with a mock Session (statement,
      consolidation, monthly, annual ER flow)

=== WORKED EXAMPLE (cm column, SECTION-STANDALONE; presented signs) ===
  Operating leaves:
    EBITDA               cm = +900
    Taxes on income      cm = −100
    Δ Inventories        cm = −50   (asset increase → cash out)
    Δ Trade payables     cm = +80   (liability increase → cash in)
  Investing leaves:
    Depreciation & amort cm = +120
    Δ Fixed assets       cm = −300
  Financing leaves:
    Profit distributions cm =   0   (no matching grain)
    Δ Equity             cm = −40
  Section-standalone subtotals:
    Gross cash flow (operating intermediate) = 900 − 100             = 800
    Cash flow from operating activities (CFO)= 900−100−50+80         = 830
    Cash flow from investing activities (CFI)= 120 − 300             = −180
    Free cash flow (= CFO + CFI)             = 830 + (−180)          = 650
    Cash flow from financing activities (CFF)= −40                   = −40
    Net cash flow (= CFO + CFI + CFF)        = 830 − 180 − 40        = 610
  Tie-out: Net cash flow == Σ leaves == 610.  ✓
  ('Exclude' and an orphan 'Δ Accounts due from affiliates' grain do NOT match
   any structure row → excluded; Net stays 610.)
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

# Delta glyphs: structure uses Greek Δ (U+0394); some grain cf_mappings use ∆ (U+2206).
_GREEK = "\u0394"
_INCR = "\u2206"


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


def _account_rows(rows):
    for r in _walk_rows(rows):
        if r.get("row_kind") == "account":
            yield r
        for a in r.get("accounts") or []:
            yield a


# ---------------------------------------------------------------------------
# Synthetic CF structure (line_code 'CF_…', kpi_code 'CF:…' / 'CF_…')
# ---------------------------------------------------------------------------
def _cf_struct(sort_order, line_code, row_type, kpi_code, balance_title, is_bold=False):
    return {
        "pl_line_id": sort_order, "sort_order": sort_order, "line_code": line_code,
        "row_type": row_type, "balance_title": balance_title,
        "details": None, "calc_type": None,
        "level_2": None, "level_3": None, "level_4": None,
        "gl_account_id": None, "invert_delta": False, "is_bold": is_bold,
        "kpi_code": kpi_code,
    }


_CF_STRUCTURE = [
    _cf_struct(2001, "CF_EBITDA", "mapping", "CF:detail", "EBITDA"),
    _cf_struct(2002, "CF_TAXES", "mapping", "CF:detail", "Taxes on income"),
    _cf_struct(2003, "CF_GROSS", "subtotal", "CF:op", "Gross cash flow", is_bold=True),
    _cf_struct(2004, "CF_INVENTORY", "mapping", "CF:detail", f"{_GREEK} Inventories"),
    _cf_struct(2005, "CF_TRADE_PAYABLES", "mapping", "CF:detail", f"{_GREEK} Trade payables"),
    _cf_struct(2006, "CF_NWC", "subtotal", "CF:op", f"{_GREEK} Net working capital"),
    _cf_struct(2007, "CF_CFO", "subtotal", "CF:op", "Cash flow from operating activities", is_bold=True),
    _cf_struct(2008, "CF_DEPRECIATION", "mapping", "CF:detail", "Depreciation & amortisation"),
    _cf_struct(2009, "CF_FIXED_ASSETS", "mapping", "CF:detail", f"{_GREEK} Fixed assets"),
    _cf_struct(2010, "CF_CFI", "calc", "CF_INV", "Cash flow from investing activities"),
    _cf_struct(2011, "CF_FCF", "calc", "CF_FREE_CASH_FLOW", "Free cash flow"),
    _cf_struct(2012, "CF_PROFIT_DIST", "mapping", "CF:detail", "Profit distributions"),
    _cf_struct(2013, "CF_EQUITY", "mapping", "CF:detail", f"{_GREEK} Equity"),
    _cf_struct(2014, "CF_CFF", "calc", "CF_FIN", "Cash flow from financing activities"),
    _cf_struct(2015, "CF_NET", "subtotal", "CF:fin", "Net cash flow", is_bold=True),
]

# A P&L row that must NEVER appear in the CF statement.
_PL_ROW = {
    "pl_line_id": 1, "sort_order": 10, "line_code": "NET_SALES", "row_type": "mapping",
    "balance_title": "Net Sales", "details": None, "calc_type": None,
    "level_2": "Income", "level_3": "Net sales", "level_4": None,
    "gl_account_id": None, "invert_delta": False, "is_bold": True, "kpi_code": None,
}

_MONTH_KEYS = ["py_cm", "pm", "cm", "ytd", "ytd_py"]


def _cf_grain(cf_mapping, gl_account_id, *, l1=None, l2=None, l3=None, **cols):
    g = {
        "cf_l11_1": l1 or cf_mapping,
        "cf_l11_2": l2 or "—",
        "cf_l11_3": l3 or "—",
        "cf_mapping": cf_mapping,
        "gl_account_id": gl_account_id,
        "account_name": cf_mapping,
        "py_cm": 0.0, "pm": 0.0, "cm": 0.0, "ytd": 0.0, "ytd_py": 0.0,
    }
    g.update(cols)
    return g


# Grain rows (cm flow). Note: cf_mapping uses the INCREMENT glyph ∆ for the WC
# leaves to test glyph-tolerant matching against the Greek-Δ structure titles.
_CF_GRAIN = [
    _cf_grain("EBITDA", "AT4000", l1="EBITDA", l2="Gross cash flow",
              cm=600, pm=550, py_cm=500, ytd=600, ytd_py=500),
    _cf_grain("EBITDA", "AT4100", l1="EBITDA", l2="Gross cash flow",
              cm=300, pm=250, py_cm=240, ytd=300, ytd_py=240),
    _cf_grain("Taxes on income", "AT8000", l1="Taxes on income", l2="Gross cash flow",
              cm=-100, pm=-90, py_cm=-80, ytd=-100, ytd_py=-80),
    _cf_grain(f"{_INCR} Inventories", "AT1400", l1=f"{_INCR} Trade working capital",
              l2=f"{_INCR} Net working capital", cm=-50, pm=-40),
    _cf_grain(f"{_INCR} Trade payables", "AT3300", l1=f"{_INCR} Trade working capital",
              l2=f"{_INCR} Net working capital", cm=80, pm=60),
    _cf_grain("Depreciation & amortisation", "AT0700",
              l1="Depreciation & amortisation", l2="Depreciation & amortisation", cm=120, pm=110),
    _cf_grain(f"{_INCR} Fixed assets", "AT0100",
              l1="Cash flow from investing activities", l2=f"{_GREEK} Fixed assets", cm=-300, pm=-200),
    _cf_grain(f"{_INCR} Equity", "AT2000",
              l1="Cash flow from financing activities", l2=f"{_INCR} Equity", cm=-40, pm=-30),
    _cf_grain("Exclude", "AT9999", cm=999),
    _cf_grain(f"{_GREEK} Accounts due from affiliates", "AT1900", cm=7),
]

# Σ of the matched leaves' cm (excludes Exclude + the orphan affiliate leaf):
#   900 - 100 - 50 + 80 + 120 - 300 + 0 - 40 = 610
_NET_CM = 610.0


def _mock_session(structure=None, grain_rows=None, entity_rows=None):
    session = MagicMock()
    structure = structure if structure is not None else _CF_STRUCTURE
    grain_rows = grain_rows if grain_rows is not None else _CF_GRAIN

    def _execute(stmt, params=None):
        sql = str(stmt)
        result = MagicMock()
        if any(t in sql for t in ("dim_pl_structure", "dim_bs_structure", "dim_cf_structure")):
            rows = [_dict_row(r) for r in structure]
        elif "dim_legal_entity" in sql:
            rows = entity_rows or [
                _dict_row({"legal_entity_code": "AT", "entity_prefix": "AT",
                           "entity_name": "Austria GmbH"}),
            ]
        else:
            rows = [_dict_row(r) for r in grain_rows]
        result.fetchall.return_value = rows
        result.fetchone.return_value = rows[0] if rows else None
        return result

    session.execute.side_effect = _execute
    return session


# ---------------------------------------------------------------------------
# (a) cf_mapping normalisation + matching (delta glyph)
# ---------------------------------------------------------------------------
class TestNormMatch:

    def test_norm_unifies_delta_glyphs(self):
        from app.services.fin_compat_cf import _norm_cf_key
        assert _norm_cf_key(f"{_GREEK} Inventories") == _norm_cf_key(f"{_INCR} Inventories")
        assert _norm_cf_key("EBITDA") == "ebitda"
        assert _norm_cf_key("  Δ  Trade   payables ") == _norm_cf_key("∆ Trade payables")
        assert _norm_cf_key(None) == ""

    def test_matched_grains_across_glyph(self):
        from app.services.fin_compat_cf import _matched_cf_grains
        # structure title uses Greek Δ; grain uses INCREMENT ∆ → still matches.
        matched = _matched_cf_grains(_CF_GRAIN, f"{_GREEK} Inventories")
        assert len(matched) == 1
        assert matched[0]["cm"] == -50

    def test_is_cf_structure_row(self):
        from app.services.fin_compat_cf import _is_cf_structure_row
        assert _is_cf_structure_row({"line_code": "CF_EBITDA", "kpi_code": "CF:detail"})
        assert _is_cf_structure_row({"line_code": "CF_CFI", "kpi_code": "CF_INV"})
        assert not _is_cf_structure_row({"line_code": "NET_SALES", "kpi_code": None})


# ---------------------------------------------------------------------------
# (b)+(c)+(d) Statement: running sum, tie-out, signs
# ---------------------------------------------------------------------------
class TestCfStatement:

    def _build(self):
        from app.services.fin_compat_cf import build_cf_statement_compat
        session = _mock_session()
        out = build_cf_statement_compat(session, period_grain="month", year=2025, month=7)
        return out["rows"], out

    def test_envelope(self):
        _, out = self._build()
        assert out["statement"] == "cf"
        assert out["period_grain"] == "month"
        assert out["year"] == 2025 and out["month"] == 7
        for k in _MONTH_KEYS:
            assert k in out["col_labels"]

    def test_leaf_values_and_signs(self):
        rows, _ = self._build()
        ebitda = _row_by_label(rows, "EBITDA")
        taxes = _row_by_label(rows, "Taxes on income")
        inv = _row_by_label(rows, f"{_GREEK} Inventories")
        pay = _row_by_label(rows, f"{_GREEK} Trade payables")
        assert ebitda is not None and ebitda["amounts"]["cm"] == 900
        assert taxes is not None and taxes["amounts"]["cm"] == -100
        assert inv is not None and inv["amounts"]["cm"] == -50
        assert pay is not None and pay["amounts"]["cm"] == 80

    def test_hierarchy_section_totals(self):
        rows, _ = self._build()
        ebitda = _row_by_label(rows, "EBITDA")
        inv_sec = _row_by_label(rows, "Cash flow from investing activities")
        fin = _row_by_label(rows, "Cash flow from financing activities")
        assert ebitda is not None and ebitda["amounts"]["cm"] == 900
        assert inv_sec is not None and inv_sec["amounts"]["cm"] == -180
        assert fin is not None and fin["amounts"]["cm"] == -40

    def test_net_cash_flow_tieout(self):
        rows, _ = self._build()
        leaf_sum = round(sum(
            r["amounts"]["cm"] for r in _account_rows(rows)
            if (r.get("drill") or {}).get("cf_mapping") != "Exclude"
            and "Accounts due from affiliates" not in (r.get("label") or "")
        ), 2)
        net = _row_by_label(rows, "Net cash flow")
        assert net is not None and net["amounts"]["cm"] == _NET_CM
        assert leaf_sum == _NET_CM

    def test_drill_keys(self):
        rows, _ = self._build()
        ebitda = _row_by_label(rows, "EBITDA")
        assert ebitda is not None
        d = ebitda.get("drill") or {}
        assert d.get("statement_type") == "CF"
        assert d.get("cf_l11_1") == "EBITDA"

    def test_account_children(self):
        rows, _ = self._build()
        ebitda = _row_by_label(rows, "EBITDA")
        assert ebitda is not None and ebitda["has_children"] is True
        accts = list(ebitda.get("accounts") or [])
        assert len(accts) == 2
        assert round(sum(a["amounts"]["cm"] for a in accts), 2) == 900

    def test_row_kind_hierarchy(self):
        rows, _ = self._build()
        ebitda = _row_by_label(rows, "EBITDA")
        gross = _row_by_label(rows, "Gross cash flow")
        assert ebitda is not None and ebitda["row_kind"] == "line"
        assert gross is not None and gross["row_kind"] == "subtotal"

    def test_deltas(self):
        rows, _ = self._build()
        ebitda = _row_by_label(rows, "EBITDA")
        assert ebitda is not None and ebitda["deltas"]["mom"] == 100

    def test_no_pl_rows_leak(self):
        from app.services.fin_compat_cf import build_cf_statement_compat
        session = _mock_session(structure=[_PL_ROW] + _CF_STRUCTURE)
        out = build_cf_statement_compat(session, period_grain="month", year=2025, month=7)
        codes = {r["line_code"] for r in _walk_rows(out["rows"])}
        assert "NET_SALES" not in codes

    def test_detail_rows_nest_under_cluster_subtotal(self):
        from app.services.fin_compat_cf import _build_cf_rows, _filter_cf_rows, _nest_cf_detail_rows
        struct = [
            _cf_struct(2004, "CF_PENSION", "mapping", "CF:detail", "Δ Pension provisions", is_bold=False),
            _cf_struct(2005, "CF_BONUS", "mapping", "CF:detail", "Δ Bonus liabilities"),
            _cf_struct(2031, "CF_OTHER_OPERATING_ITEMS", "subtotal", "CF:op",
                        "Δ Other operating items", is_bold=True),
            _cf_struct(2037, "CF_LINE_37", "mapping", "CF:detail", "\u2800\u2800"),
        ]
        for r in struct:
            if r["line_code"] in ("CF_PENSION", "CF_BONUS"):
                r["details"] = 1
            if r["line_code"] == "CF_OTHER_OPERATING_ITEMS":
                r["details"] = 0
        grains = [
            _cf_grain("Δ Pension provisions", "AT3310", cm=-10),
            _cf_grain("Δ Bonus liabilities", "AT3823", cm=5),
        ]
        keys = list(_MONTH_KEYS)
        flat = _build_cf_rows(struct, grains, keys)
        other = _row_by_label(flat, "Δ Other operating items")
        assert other is not None
        assert len(other.get("children") or []) == 2
        top_labels = {(r.get("label") or "") for r in flat}
        assert "Δ Pension provisions" not in top_labels
        assert "Δ Bonus liabilities" not in top_labels
        assert "\u2800\u2800" not in top_labels


# ---------------------------------------------------------------------------
# (g) Consolidation — per-entity running sum
# ---------------------------------------------------------------------------
def _cf_consl_grain(cf_mapping, ang, ep, cm, *, l1=None, l2=None):
    return {
        "l1": l1 or cf_mapping, "l2": l2, "l3": None, "l4": None, "l5": None,
        "cf_mapping": cf_mapping, "account_number_group": ang, "entity_prefix": ep, "cm": cm,
    }


class TestCfConsolidation:

    def test_consolidation(self):
        from app.services.fin_compat_cf import build_cf_consolidation
        grain = [
            _cf_consl_grain("EBITDA", "AT4000", "AT", 600),
            _cf_consl_grain("EBITDA", "DE4000", "DE", 300),
            _cf_consl_grain("Taxes on income", "AT8000", "AT", -100),
        ]
        entity_rows = [
            _dict_row({"legal_entity_code": "AT", "entity_prefix": "AT", "entity_name": "Austria"}),
            _dict_row({"legal_entity_code": "DE", "entity_prefix": "DE", "entity_name": "Germany"}),
        ]
        session = _mock_session(grain_rows=grain, entity_rows=entity_rows)
        out = build_cf_consolidation(session, period_grain="month", year=2025, month=7)
        rows = {r["id"]: r for r in out["rows"]}
        rows = {k.replace("cf-", "", 1): v for k, v in rows.items()}
        assert out["statement"] == "cf"
        ebitda = rows["CF_EBITDA"]
        assert ebitda["entity_amounts"]["AT"] == 600
        assert ebitda["entity_amounts"]["DE"] == 300
        assert ebitda["aggregated"] == 900
        assert ebitda["consolidation"] == 900
        # Gross cash flow running sum per entity: AT 600-100=500, DE 300
        assert rows["CF_GROSS"]["entity_amounts"]["AT"] == 500
        assert rows["CF_GROSS"]["entity_amounts"]["DE"] == 300
        # Net cash flow aggregated == Σ leaves == 800
        assert rows["CF_NET"]["aggregated"] == 800

    def test_annual_col_label_ytd(self):
        from app.services.fin_compat_cf import build_cf_consolidation
        grain = [
            _cf_consl_grain("EBITDA", "AT4000", "AT", 600),
            _cf_consl_grain("EBITDA", "DE4000", "DE", 300),
        ]
        entity_rows = [
            _dict_row({"legal_entity_code": "AT", "entity_prefix": "AT", "entity_name": "Austria"}),
            _dict_row({"legal_entity_code": "DE", "entity_prefix": "DE", "entity_name": "Germany"}),
        ]
        session = _mock_session(grain_rows=grain, entity_rows=entity_rows)
        out = build_cf_consolidation(session, period_grain="year", year=2025, month=7)
        assert out["col_label"] == "YTDJul25A"
        assert out["period_grain"] == "year"

    def test_statement_year_grain_uses_annual(self):
        from app.services.fin_compat_cf import build_cf_statement_compat
        grain = [
            _cf_annual_grain("EBITDA", "AT4000", l1="EBITDA", fy2=100, fy3=200, ytd=50, ltm=300),
        ]
        session = _mock_session(grain_rows=grain)
        out = build_cf_statement_compat(session, period_grain="year", year=2025, month=7)
        assert out["period_grain"] == "year"
        # Annual CF mirrors the P&L annual col_labels: ``ltm`` is the trailing-12
        # label and the forecast lives in its own ``fy_f`` column.
        assert out["col_labels"]["ltm"] == "LTMJul25A"
        assert out["col_labels"]["fy_f"] == "FY25F"
        assert out["col_labels"]["plan_cm"] == "FY26P"
        rows = {r["line_code"]: r for r in out["rows"]}
        ebitda = rows.get("CF_EBITDA")
        assert ebitda is not None
        assert ebitda["amounts"]["ytd"] == 50
def _cf_monthly_grain(cf_mapping, ang, **cols):
    g = {
        "cf_l11_1": cf_mapping, "cf_l11_2": "—", "cf_l11_3": "—",
        "cf_mapping": cf_mapping, "gl_account_id": ang,
        "account_number_group": ang, "account_name": cf_mapping,
    }
    g.update(cols)
    return g


class TestCfMonthly:

    def test_monthly(self):
        from app.services.fin_compat_cf import build_cf_monthly
        from app.services.fin_compat_sql import _last_12_periods, period_key
        keys = [period_key(y, m) for y, m in _last_12_periods(2025, 7)]
        anchor = keys[-1]  # 2025-07
        g_eb = _cf_monthly_grain("EBITDA", "AT4000")
        g_tx = _cf_monthly_grain("Taxes on income", "AT8000")
        for k in keys:
            g_eb[k] = 0.0
            g_tx[k] = 0.0
        g_eb[anchor] = 900.0
        g_tx[anchor] = -100.0
        session = _mock_session(grain_rows=[g_eb, g_tx])
        out = build_cf_monthly(session, year=2025, month=7)
        rows = out["rows"]
        assert out["statement"] == "cf"
        ebitda = _row_by_label(rows, "EBITDA")
        taxes = _row_by_label(rows, "Taxes on income")
        assert ebitda is not None and ebitda["amounts"][anchor] == 900
        assert taxes is not None and taxes["amounts"][anchor] == -100


# ---------------------------------------------------------------------------
# (g) Annual ER flow (FY/YTD/LTM) — flow grain, running sum, tie-out
# ---------------------------------------------------------------------------
_ER_KEYS = ["fy1", "fy2", "fy3", "ytd", "ltm", "ytd_py", "ltm_py"]


def _cf_annual_grain(cf_mapping, ang, *, l1=None, l2=None, **cols):
    g = {
        "cf_l11_1": l1 or cf_mapping,
        "cf_l11_2": l2 or "—",
        "cf_l11_3": "—",
        "cf_mapping": cf_mapping,
        "gl_account_id": ang,
        "account_number_group": ang,
        "account_name": cf_mapping,
    }
    for k in _ER_KEYS:
        g[k] = 0.0
    g.update(cols)
    return g


class TestCfAnnual:

    def test_annual_flow(self):
        from app.services.fin_compat_cf import build_cf_annual_compat
        grain = [
            _cf_annual_grain("EBITDA", "AT4000", l1="EBITDA", l2="Gross cash flow",
                             fy3=900, fy2=800, ytd=500),
            _cf_annual_grain("Taxes on income", "AT8000", l1="Taxes on income",
                             l2="Gross cash flow", fy3=-100, fy2=-90, ytd=-60),
            _cf_annual_grain(f"{_INCR} Fixed assets", "AT0100",
                             l1="Cash flow from investing activities", fy3=-300, fy2=-200, ytd=-150),
        ]
        session = _mock_session(grain_rows=grain)
        out = build_cf_annual_compat(session, year=2025, month=7)
        rows = out["rows"]
        assert out["statement"] == "cf"
        for k in ("fy2", "fy3", "ytd", "ytd_py", "ltm", "ltm_py"):
            assert k in out["col_labels"]
        ebitda = _row_by_label(rows, "EBITDA")
        fixed = _row_by_label(rows, f"{_GREEK} Fixed assets")
        assert ebitda is not None and ebitda["amounts"]["fy3"] == 900
        assert fixed is not None and fixed["amounts"]["fy3"] == -300
        # EBITDA fy3 delta_fy = 900 - 800 = 100
        assert ebitda["deltas"]["delta_fy"] == 100


# ---------------------------------------------------------------------------
# (g) Weekly breakdown — ISO week columns + section subtotals
# ---------------------------------------------------------------------------
class TestCfWeekly:

    def test_weekly_breakdown(self):
        from app.services.fin_compat_cf import build_cf_weekly_breakdown
        from app.services.fin_compat_sql import weekly_breakdown_layout

        layout = weekly_breakdown_layout(2025, 28)
        m0 = layout[2]
        wk_key = m0["weeks"][-1]["key"]
        mtd_key = m0["total"]["key"]
        g_eb = _cf_monthly_grain("EBITDA", "AT4000")
        g_tx = _cf_monthly_grain("Taxes on income", "AT8000")
        for k in (wk_key, mtd_key):
            g_eb[k] = 900.0
            g_tx[k] = -100.0
        session = _mock_session(grain_rows=[g_eb, g_tx])
        out = build_cf_weekly_breakdown(session, iso_year=2025, iso_week=28)
        assert out["statement"] == "cf"
        assert out["iso_year"] == 2025 and out["iso_week"] == 28
        assert [g["kind"] for g in out["groups"]] == ["full", "full", "partial"]
        ebitda = _row_by_label(out["rows"], "EBITDA")
        gross = _row_by_label(out["rows"], "Gross cash flow")
        assert ebitda is not None and ebitda["amounts"][wk_key] == 900
        assert gross is not None and gross["amounts"][wk_key] == 800


# ===========================================================================
# (h) DB-backed reconciliation — live v2 GL (opt-in; auto-skip when unreachable)
# ===========================================================================
# Guards the CF *value-sourcing* end-to-end against the real GL, which the
# DB-free tests above CANNOT do (they inject grains via a mock Session and so
# stay green even when the underlying dimension is empty).  The all-zero CF
# regression was caused by an UNPOPULATED ``dim_gl_cf`` (the CF mapping
# dimension) — the join in ``cf_grain_sql_month`` then matched nothing and every
# amount silently fell to 0, while the row STRUCTURE (20 rows) looked normal.
#
# These tests make that condition LOUD: if ``dim_gl_cf`` has no rows for the test
# fiscal_year, the reconciliation assertions fail (non-zero EBITDA), so the data
# gap can never again masquerade as a working-but-zero statement.  They also tie
# CF EBITDA to the P&L EBITDA and verify Gross cash flow = EBITDA + Taxes and the
# Net cash flow tie-out, exactly as the orchestrator asked.
import os

_RECON_YEAR = 2025
_RECON_MONTH = 7


def _v2_session_or_skip():
    from sqlalchemy import text

    from app.db import SessionLocal

    try:
        s = SessionLocal()
        s.execute(text("SELECT 1 FROM dim_gl_cf LIMIT 1"))
        return s
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"v2 CF recon DB not reachable: {exc}")


def _pl_row_value(out, label_or_code: str, key: str) -> float:
    for r in _walk_rows(out["rows"]):
        if r.get("line_code") == label_or_code or r.get("label") == label_or_code:
            return float((r.get("amounts") or {}).get(key) or 0.0)
    raise AssertionError(f"row {label_or_code!r} not found")


@pytest.mark.skipif(
    os.getenv("DB_NAME", "Finssentials") != "finssentials_v2",
    reason="CF reconciliation runs only against finssentials_v2 (set DB_NAME)",
)
class TestCfReconciliationV2:
    """CF value-sourcing reconciles to the P&L over the real GL (month + year)."""

    def test_dim_gl_cf_is_populated(self):
        """The CF mapping dimension MUST have rows for the test year.

        An empty ``dim_gl_cf`` is exactly the gap that zeroes the whole CF
        statement — assert it explicitly so the failure names the cause instead
        of surfacing as opaque all-zero amounts downstream.
        """
        from sqlalchemy import text
        session = _v2_session_or_skip()
        n = int(session.execute(
            text("SELECT COUNT(*) FROM dim_gl_cf WHERE fiscal_year = :fy"),
            {"fy": _RECON_YEAR},
        ).scalar() or 0)
        assert n > 0, (
            f"dim_gl_cf has no rows for FY{_RECON_YEAR}: the CF statement will be "
            "all-zero. Re-ingest the account mapping WITH the cf_l1..cf_l5/"
            "cf_mapping columns filled (etl.load.upsert_account_mapping) — the CF "
            "builder/SQL are correct; only the dimension is missing."
        )

    def test_month_grain_ebitda_reconciles_to_pl(self):
        from app.services.fin_compat_cf import build_cf_statement_compat
        from app.services.fin_compat_pl import build_pl_statement_compat
        session = _v2_session_or_skip()

        cf = build_cf_statement_compat(
            session, period_grain="month", year=_RECON_YEAR, month=_RECON_MONTH,
            entity=None,
        )
        pl = build_pl_statement_compat(
            session, period_grain="month", year=_RECON_YEAR, month=_RECON_MONTH,
            entity=None,
        )

        cf_rows = {r["line_code"]: r for r in _walk_rows(cf["rows"])}
        cf_ebitda = cf_rows.get("CF_EBITDA")
        assert cf_ebitda is not None, "CF_EBITDA row missing from CF structure"
        cf_eb_ytd = float((cf_ebitda.get("amounts") or {}).get("ytd") or 0.0)
        cf_eb_cm = float((cf_ebitda.get("amounts") or {}).get("cm") or 0.0)

        # (1) Non-zero — the regression symptom.
        assert cf_eb_ytd != 0.0, "CF EBITDA YTD is 0 (dim_gl_cf likely empty)"
        assert cf_eb_cm != 0.0, "CF EBITDA cm is 0 (dim_gl_cf likely empty)"

        # (2) Ties to the P&L EBITDA (same GL, same period; the CF EBITDA leaf is
        #     the EBITDA carried into the indirect cash flow).
        pl_eb_ytd = _pl_row_value(pl, "EBITDA", "ytd")
        pl_eb_cm = _pl_row_value(pl, "EBITDA", "cm")
        assert cf_eb_ytd == pytest.approx(pl_eb_ytd, rel=1e-6, abs=1.0)
        assert cf_eb_cm == pytest.approx(pl_eb_cm, rel=1e-6, abs=1.0)

        # (3) Gross cash flow = EBITDA + Taxes (operating intermediate).
        gross = cf_rows.get("CF_GROSS_CASH_FLOW") or _row_by_label(cf["rows"], "Gross cash flow")
        taxes = _row_by_label(cf["rows"], "Taxes on income")
        assert gross is not None and taxes is not None
        g_ytd = float((gross.get("amounts") or {}).get("ytd") or 0.0)
        t_ytd = float((taxes.get("amounts") or {}).get("ytd") or 0.0)
        assert g_ytd != 0.0
        assert g_ytd == pytest.approx(cf_eb_ytd + t_ytd, rel=1e-6, abs=1.0)

        # (4) Net cash flow non-zero and equals Σ of every mapped CF leaf (YTD).
        net = _row_by_label(cf["rows"], "Net cash flow")
        assert net is not None
        net_ytd = float((net.get("amounts") or {}).get("ytd") or 0.0)
        assert net_ytd != 0.0
        leaf_sum = sum(
            float((r.get("amounts") or {}).get("ytd") or 0.0)
            for r in _walk_rows(cf["rows"]) if r.get("row_kind") == "line"
        )
        assert net_ytd == pytest.approx(leaf_sum, rel=1e-6, abs=1.0)

    def test_year_grain_ebitda_non_zero(self):
        from app.services.fin_compat_cf import build_cf_statement_compat
        session = _v2_session_or_skip()
        out = build_cf_statement_compat(
            session, period_grain="year", year=_RECON_YEAR, month=_RECON_MONTH,
            entity=None,
        )
        assert out["period_grain"] == "year"
        rows = {r["line_code"]: r for r in _walk_rows(out["rows"])}
        eb = rows.get("CF_EBITDA")
        assert eb is not None
        # Annual ER-flow YTD column must be non-zero (year grain mirrors P&L annual).
        assert float((eb.get("amounts") or {}).get("ytd") or 0.0) != 0.0


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
