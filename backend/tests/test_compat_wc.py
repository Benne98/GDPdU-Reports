"""Tests for the Working-Capital legacy compat layer (DB-free).

Covers the WC-specific semantics that differ from the BS:
  (a) RAW signed balances are carried through (NO credit-side display flip)
  (b) Net working capital = straight Σ of the raw TWC+OWC balances
  (c) section running-sum subtotals (TWC / OWC) over the raw balances
  (d) DSO / DIO / DPO / CCC day KPIs (LTM revenue/COGS denominators) + edge cases
  (e) annual snapshot deltas (delta_fy = fy − fy_py, delta_cm = cm − cm_py)
  (f) end-to-end glue through build_wc_* with a mock Session

=== WORKED EXAMPLE (month grain, cumulative balances; cm column) ===
  Raw cumulative cm balances (assets +, liabilities −):
    Trade receivables (rec) = +5,000,000
    Inventories       (inv) = +4,000,000
    Trade payables    (pay) = −3,000,000   (liability, negative — NOT flipped)
    Other working cap. (owc)=   +500,000
  Trade Working Capital subtotal = 5,000,000 + 4,000,000 − 3,000,000 = +6,000,000
  Other Working Capital subtotal = +500,000
  NWC = 6,000,000 + 500,000 = +6,500,000   (Σ TWC+OWC, raw signs)
  LTM denominators ending at the cm cutoff: revenue_ltm = 20,000,000,
    cogs_ltm = 12,000,000.  KPIs use the ABS magnitudes:
    DSO = |5,000,000| * 365 / 20,000,000 = 91.25  → 91.2 days
    DIO = |4,000,000| * 365 / 12,000,000 = 121.667 → 121.7 days
    DPO = |3,000,000| * 365 / 12,000,000 = 91.25  → 91.2 days
    CCC = 91.2 + 121.7 − 91.2 = 121.7 days
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


# ---------------------------------------------------------------------------
# Synthetic WC grains (l6_na_mapping = TWC / OWC; raw stored signs)
# ---------------------------------------------------------------------------
_MONTH_KEYS = ["py_cm", "pm", "cm", "ytd", "ytd_py"]


def _wc_grain(l6, l3, ang, gid, name, **cols):
    g = {
        "l6_na_mapping": l6, "l7_na_description": l3,
        "level_2": "Working capital", "level_3": l3, "level_4": None,
        "gl_account_id": gid, "account_number_group": ang, "account_name": name,
        "py_cm": 0.0, "pm": 0.0, "cm": 0.0, "ytd": 0.0, "ytd_py": 0.0,
    }
    g.update(cols)
    return g


_WC_GRAIN = [
    _wc_grain("TWC", "Trade receivables", "AT1200", "AT1200", "Receivables",
              cm=5_000_000, pm=4_800_000, py_cm=4_500_000, ytd=5_000_000, ytd_py=4_500_000),
    _wc_grain("TWC", "Inventories", "AT1400", "AT1400", "Inventory",
              cm=4_000_000, pm=3_900_000, py_cm=3_700_000, ytd=4_000_000, ytd_py=3_700_000),
    _wc_grain("TWC", "Trade payables", "AT3300", "AT3300", "Payables",
              cm=-3_000_000, pm=-2_900_000, py_cm=-2_700_000, ytd=-3_000_000, ytd_py=-2_700_000),
    _wc_grain("OWC", "Other working capital", "AT1900", "AT1900", "Other WC",
              cm=500_000, pm=480_000, py_cm=450_000, ytd=500_000, ytd_py=450_000),
]

# LTM revenue / COGS returned for every P&L window query in the mock.
_LTM = {"revenue": 20_000_000.0, "cogs": 12_000_000.0}


# ---------------------------------------------------------------------------
# Mock Session: dim_legal_entity → entities; level_0='PL' → LTM row(s);
# any other (level_0='BS' / TWC-OWC) grain → WC grain rows.
# ---------------------------------------------------------------------------
def _mock_session(grain_rows=None, ltm=None, ltm_by_entity=None, entity_rows=None):
    session = MagicMock()
    grain_rows = grain_rows if grain_rows is not None else _WC_GRAIN
    ltm = ltm if ltm is not None else _LTM

    def _execute(stmt, params=None):
        sql = str(stmt)
        result = MagicMock()
        rows: list[Any] = []
        if "dim_legal_entity" in sql:
            rows = entity_rows or [
                _dict_row({"legal_entity_code": "AT", "entity_prefix": "AT",
                           "entity_name": "Austria GmbH"}),
            ]
        elif any(t in sql for t in ("dim_pl_structure", "dim_bs_structure", "dim_cf_structure")):
            rows = []
        elif "a.level_0 = 'PL'" in sql:
            if "GROUP BY l.entity_prefix" in sql:
                rows = ltm_by_entity or [
                    _dict_row({"entity_prefix": "AT", **ltm}),
                ]
            else:
                rows = [_dict_row(dict(ltm))]
        else:
            rows = [_dict_row(r) for r in grain_rows]
        result.fetchall.return_value = rows
        result.fetchone.return_value = rows[0] if rows else None
        return result

    session.execute.side_effect = _execute
    return session


def _flatten(rows: list[dict]) -> dict[str, dict]:
    """Flatten the row tree to {label: row} (last write wins)."""
    out: dict[str, dict] = {}

    def walk(rs):
        for r in rs:
            out[(r.get("label") or "").strip()] = r
            walk(r.get("children") or [])
    walk(rows)
    return out


# ---------------------------------------------------------------------------
# (d) Pure KPI core — worked example + edge cases
# ---------------------------------------------------------------------------
class TestWcKpis:

    def test_pl_window_sql_matches_cogs_at_level_2(self):
        # DIO/DPO LTM denominator: "Cost of materials" (Materialaufwand) is a level_2
        # category in real datasets — its postings live under level_3 sub-buckets
        # ("Purchased services" / "Purchased goods and materials"), so the query must
        # match level_2, not only level_3, or cogs_ltm = 0 and DIO/DPO come out 0.
        from datetime import date
        from app.services.fin_compat_wc_sql import wc_pl_window_sql

        sql, _ = wc_pl_window_sql(date(2024, 7, 1), date(2025, 6, 30), "")
        assert "TRIM(a.level_2) = 'Cost of materials'" in sql
        assert "'Net sales'" in sql

    def test_worked_example(self):
        from app.services.fin_compat_wc import compute_wc_kpis
        k = compute_wc_kpis(4_000_000, 5_000_000, 3_000_000, 20_000_000, 12_000_000)
        assert k["DSO"] == 91.2     # rec 5m * 365 / 20m
        assert k["DIO"] == 121.7    # inv 4m * 365 / 12m
        assert k["DPO"] == 91.2     # pay 3m * 365 / 12m
        assert k["CCC"] == 121.7    # 91.2 + 121.7 − 91.2

    def test_zero_revenue_zeroes_dso(self):
        from app.services.fin_compat_wc import compute_wc_kpis
        k = compute_wc_kpis(100, 100, 100, 0.0, 1000.0)
        assert k["DSO"] == 0.0            # rev_ltm 0 → DSO 0
        assert k["DIO"] == round(100 * 365 / 1000, 1)
        assert k["DPO"] == round(100 * 365 / 1000, 1)
        assert k["CCC"] == 0.0            # DSO 0 + DIO − DPO (equal) = 0

    def test_zero_cogs_zeroes_dio_dpo(self):
        from app.services.fin_compat_wc import compute_wc_kpis
        k = compute_wc_kpis(100, 100, 100, 1000.0, 0.0)
        assert k["DIO"] == 0.0
        assert k["DPO"] == 0.0
        assert k["DSO"] == round(100 * 365 / 1000, 1)
        assert k["CCC"] == k["DSO"]       # DSO + 0 − 0

    def test_all_zero_denominators(self):
        from app.services.fin_compat_wc import compute_wc_kpis
        assert compute_wc_kpis(100, 100, 100, 0.0, 0.0) == {
            "DIO": 0.0, "DSO": 0.0, "DPO": 0.0, "CCC": 0.0}


# ---------------------------------------------------------------------------
# (b) Pure NWC + (c) section balances
# ---------------------------------------------------------------------------
class TestWcNwcCore:

    def test_nwc_is_raw_sum(self):
        from app.services.fin_compat_wc import _nwc_total
        nwc = _nwc_total(_WC_GRAIN, _MONTH_KEYS)
        # 5m + 4m − 3m + 0.5m = 6.5m (raw signs, payables subtracted naturally)
        assert nwc["cm"] == 6_500_000
        assert nwc["py_cm"] == 4_500_000 + 3_700_000 - 2_700_000 + 450_000

    def test_l3_balance_keeps_sign(self):
        from app.services.fin_compat_wc import _l3_balance
        pay = _l3_balance(_WC_GRAIN, "Trade payables", _MONTH_KEYS)
        assert pay["cm"] == -3_000_000   # raw negative (no flip)


# ---------------------------------------------------------------------------
# (a)+(c)+(d)+(f) Statement end-to-end
# ---------------------------------------------------------------------------
class TestWcStatement:

    def _build(self):
        from app.services.fin_compat_wc import build_wc_statement_compat
        session = _mock_session()
        out = build_wc_statement_compat(session, period_grain="month", year=2025, month=7)
        return _flatten(out["rows"]), {r.get("line_code"): r for r in out["rows"]}, out

    def test_envelope(self):
        _, _, out = self._build()
        assert out["statement"] == "wc"
        assert out["period_grain"] == "month"
        assert out["year"] == 2025 and out["month"] == 7
        for k in _MONTH_KEYS:
            assert k in out["col_labels"]

    def test_raw_signs_no_flip(self):
        """Liabilities keep their RAW negative sign (no BS credit-side flip)."""
        rows, _, _ = self._build()
        assert rows["Trade payables"]["amounts"]["cm"] == -3_000_000
        assert rows["Trade receivables"]["amounts"]["cm"] == 5_000_000

    def test_section_running_sum(self):
        rows, _, _ = self._build()
        # Trade Working Capital = 5m + 4m − 3m = 6m ; Other = 0.5m
        assert rows["Trade Working Capital"]["amounts"]["cm"] == 6_000_000
        assert rows["Other Working Capital"]["amounts"]["cm"] == 500_000
        assert rows["Trade Working Capital"]["row_kind"] == "subtotal"

    def test_nwc_row(self):
        _, by_code, _ = self._build()
        nwc = by_code["NWC"]
        assert nwc["amounts"]["cm"] == 6_500_000
        assert nwc["row_kind"] == "subtotal"
        assert nwc["is_bold"] is True

    def test_kpi_rows(self):
        _, by_code, out = self._build()
        assert by_code["WC_DSO"]["amounts"]["cm"] == 91.2
        assert by_code["WC_DIO"]["amounts"]["cm"] == 121.7
        assert by_code["WC_DPO"]["amounts"]["cm"] == 91.2
        assert by_code["WC_CCC"]["amounts"]["cm"] == 121.7
        assert by_code["WC_DSO"]["row_kind"] == "kpi"
        hdr = next(r for r in out["rows"] if r.get("row_kind") == "kpi_header")
        assert hdr["label"] == "KPIs — working capital days"

    def test_twc_mapping_line_order(self):
        grains = [
            _wc_grain("TWC", "Trade payables", "AT3300", "AT3300", "Payables", cm=-1.0),
            _wc_grain("TWC", "Advance payments received", "AT3400", "AT3400", "Advances", cm=-0.5),
            _wc_grain("TWC", "Trade receivables", "AT1200", "AT1200", "Receivables", cm=2.0),
            _wc_grain("TWC", "Inventories", "AT1400", "AT1400", "Inventory", cm=1.0),
        ]
        from app.services.fin_compat_wc import build_wc_statement_compat
        session = _mock_session(grain_rows=grains)
        out = build_wc_statement_compat(session, year=2025, month=7)
        twc = next(r for r in out["rows"] if r["label"] == "Trade Working Capital")
        assert [c["label"] for c in twc["children"]] == [
            "Inventories",
            "Trade receivables",
            "Trade payables",
            "Advance payments received",
        ]

    def test_account_label_pipe_format(self):
        from app.services.fin_compat_wc import _wc_account_label
        assert _wc_account_label("38154", "38154", "Baustoffe") == "38154 | Baustoffe"
        assert _wc_account_label("", "20200", "Baustoffe") == "20200 | Baustoffe"
        assert _wc_account_label("38154", "", "") == "38154 | —"

    def test_section_order_twc_first(self):
        _, _, out = self._build()
        top_labels = [r["label"] for r in out["rows"]
                      if r.get("label") in ("Trade Working Capital", "Other Working Capital")]
        assert top_labels == ["Trade Working Capital", "Other Working Capital"]


# ---------------------------------------------------------------------------
# (e) Annual snapshot — raw balances + snapshot deltas
# ---------------------------------------------------------------------------
_SNAP_KEYS = ["dec_py2", "fy_py", "fy", "cm_py", "cm"]


def _wc_snap_grain(l6, l3, ang, **cols):
    g = {
        "l6_na_mapping": l6, "l7_na_description": l3,
        "level_2": "Working capital", "level_3": l3, "level_4": None,
        "gl_account_id": ang, "account_number_group": ang, "account_name": l3,
        "fy_py": 0.0, "fy": 0.0, "cm_py": 0.0, "cm": 0.0,
        "dec_py2": 0.0,
    }
    g.update(cols)
    return g


_SNAP_GRAIN = [
    _wc_snap_grain("TWC", "Trade receivables", "AT1200",
                   dec_py2=3_800_000, fy_py=4_000_000, fy=4_500_000, cm_py=4_800_000, cm=5_000_000),
    _wc_snap_grain("TWC", "Inventories", "AT1400",
                   dec_py2=3_300_000, fy_py=3_500_000, fy=3_700_000, cm_py=3_900_000, cm=4_000_000),
    _wc_snap_grain("TWC", "Trade payables", "AT3300",
                   dec_py2=-2_300_000, fy_py=-2_500_000, fy=-2_700_000, cm_py=-2_900_000, cm=-3_000_000),
    _wc_snap_grain("OWC", "Other working capital", "AT1900",
                   dec_py2=350_000, fy_py=400_000, fy=450_000, cm_py=480_000, cm=500_000),
]


class TestWcSnapshot:

    def _build(self):
        from app.services.fin_compat_wc import build_wc_snapshot_annual
        session = _mock_session(grain_rows=_SNAP_GRAIN)
        out = build_wc_snapshot_annual(session, year=2025, month=7)
        return {r.get("line_code"): r for r in out["rows"]}, out

    def test_col_labels(self):
        _, out = self._build()
        assert out["statement"] == "wc"
        assert out["col_labels"]["dec_py2"] == "Dec22A"
        assert out["col_labels"]["fy_py"] == "Dec23A"
        assert out["col_labels"]["fy"] == "Dec24A"
        assert out["col_labels"]["cm_py"] == "Jul24A"
        assert out["col_labels"]["cm"] == "Jul25A"

    def test_dec_py2_amounts(self):
        by_code, _ = self._build()
        nwc = by_code["NWC"]
        assert nwc["amounts"]["dec_py2"] == 3_800_000 + 3_300_000 - 2_300_000 + 350_000

    def test_nwc_and_snapshot_deltas(self):
        by_code, _ = self._build()
        nwc = by_code["NWC"]
        # cm = 5m + 4m − 3m + 0.5m = 6.5m ; fy = 4.5m + 3.7m − 2.7m + 0.45m = 5.95m
        assert nwc["amounts"]["cm"] == 6_500_000
        assert nwc["amounts"]["fy"] == 5_950_000
        # delta_fy = fy − fy_py ; delta_cm = cm − cm_py
        fy_py = 4_000_000 + 3_500_000 - 2_500_000 + 400_000   # = 5.4m
        cm_py = 4_800_000 + 3_900_000 - 2_900_000 + 480_000   # = 6.28m
        assert nwc["deltas"]["delta_fy"] == round(5_950_000 - fy_py, 2)
        assert nwc["deltas"]["delta_cm"] == round(6_500_000 - cm_py, 2)


# ---------------------------------------------------------------------------
# (f) Consolidation end-to-end (per-entity raw cm balance + NWC)
# ---------------------------------------------------------------------------
def _wc_consl_grain(l6, l3, ang, entity_prefix, cm, *, gid=None, name=None, **snap_cols):
    row = {
        "l6_na_mapping": l6, "l7_na_description": l3,
        "level_2": "Working capital", "level_3": l3, "level_4": None,
        "account_number_group": ang,
        "gl_account_id": gid or ang,
        "account_name": name or l3,
        "entity_prefix": entity_prefix, "cm": float(cm),
        "dec_py2": 0.0, "fy_py": 0.0, "fy": 0.0, "cm_py": 0.0,
    }
    row.update({k: float(v) for k, v in snap_cols.items()})
    return row


_CONSL_GRAIN = [
    _wc_consl_grain("TWC", "Trade receivables", "AT1200", "AT", 5_000_000, name="Receivables"),
    _wc_consl_grain("TWC", "Trade payables", "AT3300", "AT", -3_000_000, name="Payables"),
    _wc_consl_grain("TWC", "Trade receivables", "DE1200", "DE", 2_000_000, name="DE Receivables"),
    _wc_consl_grain("OWC", "Other working capital", "DE1900", "DE", 300_000, name="Other WC"),
]
_CONSL_ENTS = [
    _dict_row({"legal_entity_code": "AT", "entity_prefix": "AT", "entity_name": "Austria GmbH"}),
    _dict_row({"legal_entity_code": "DE", "entity_prefix": "DE", "entity_name": "Germany GmbH"}),
]
_CONSL_LTM_BY_ENT = [
    _dict_row({"entity_prefix": "AT", "revenue": 10_000_000.0, "cogs": 6_000_000.0}),
    _dict_row({"entity_prefix": "DE", "revenue": 5_000_000.0, "cogs": 3_000_000.0}),
]


class TestWcConsolidation:

    def _build(self):
        from app.services.fin_compat_wc import build_wc_consolidation
        session = _mock_session(grain_rows=_CONSL_GRAIN, entity_rows=_CONSL_ENTS,
                                ltm_by_entity=_CONSL_LTM_BY_ENT)
        out = build_wc_consolidation(session, period_grain="month", year=2025, month=7)
        return {r["id"]: r for r in out["rows"]}, out

    def test_nwc_per_entity(self):
        rows, _ = self._build()
        nwc = rows["wc-net-total"]
        # AT: 5m − 3m = 2m ; DE: 2m + 0.3m = 2.3m
        assert nwc["entity_amounts"] == {"AT": 2_000_000, "DE": 2_300_000}
        assert nwc["aggregated"] == 4_300_000
        assert nwc["ic_eliminations"] == 0.0
        assert nwc["consolidation"] == 4_300_000

    def test_kpi_rows_present(self):
        rows, _ = self._build()
        assert "wc-kpi-header" in rows
        assert rows["wc-kpi-header"]["row_kind"] == "kpi_header"
        assert rows["wc-kpi-header"]["label"] == "KPIs — working capital days"
        assert "wc-kpi-ccc" in rows
        # consolidated DSO = |rec 7m| * 365 / rev 15m = 170.3
        assert rows["wc-kpi-dso"]["consolidation"] == round(7_000_000 * 365 / 15_000_000, 1)

    def test_consol_account_labels_include_name(self):
        _, out = self._build()
        twc = next(r for r in out["rows"] if r.get("label") == "Trade Working Capital")
        rec = next(c for c in twc["children"] if c["label"] == "Trade receivables")
        labels = {a["label"] for a in rec.get("children", [])}
        assert "AT1200 | Receivables" in labels
        assert "DE1200 | DE Receivables" in labels

    def test_annual_col_label_ytd(self):
        from app.services.fin_compat_wc import build_wc_consolidation
        session = _mock_session(grain_rows=_CONSL_GRAIN, entity_rows=_CONSL_ENTS,
                                ltm_by_entity=_CONSL_LTM_BY_ENT)
        out = build_wc_consolidation(session, period_grain="year", year=2025, month=7)
        assert out["col_label"] == "Jul25A"
        assert out["period_grain"] == "year"
        assert out["col_labels"]["dec_py2"] == "Dec22A"
        twc = next(r for r in out["rows"] if r["label"] == "Trade Working Capital")
        assert twc["has_children"]
        assert twc["consolidation_periods"]["cm"] == 4_000_000

    def test_annual_kpi_periods_all_snapshot_columns(self):
        annual_grains = [
            _wc_consl_grain(
                "TWC", "Trade receivables", "AT1200", "AT", 7_000_000,
                fy=6_000_000, fy_py=5_000_000, name="Receivables",
            ),
            _wc_consl_grain(
                "TWC", "Trade payables", "AT3300", "AT", -3_000_000,
                fy=-2_500_000, fy_py=-2_000_000, name="Payables",
            ),
        ]
        session = _mock_session(
            grain_rows=annual_grains,
            entity_rows=_CONSL_ENTS[:1],
            ltm={"revenue": 20_000_000.0, "cogs": 12_000_000.0},
        )
        from app.services.fin_compat_wc import build_wc_consolidation
        out = build_wc_consolidation(session, period_grain="year", year=2025, month=7)
        dso = next(r for r in out["rows"] if r["id"] == "wc-kpi-dso")
        assert dso["consolidation_periods"]["fy"] == round(6_000_000 * 365 / 20_000_000, 1)
        assert dso["consolidation_periods"]["cm"] == round(7_000_000 * 365 / 20_000_000, 1)
        assert dso["consolidation_periods"]["fy"] != dso["consolidation_periods"]["cm"]


class TestWcMonthlyFy3:

    def test_monthly_grain_sql_is_fy_scoped(self):
        from app.services.fin_compat_wc_sql import wc_monthly_grain_sql

        sql, _ = wc_monthly_grain_sql(2025, 7, "", span="fy3")
        assert "entry_type = 'opening_balance'" in sql
        assert "e.fiscal_year = 2025" in sql
        assert "e.fiscal_year = 2024" in sql

    def test_fy3_span_periods_and_totals(self):
        from app.services.fin_compat_sql import _fy_span_periods, period_key
        from app.services.fin_compat_wc import build_wc_monthly

        keys = [period_key(y, m) for y, m in _fy_span_periods(2025, 7)]
        grains = [
            {
                "l6_na_mapping": "TWC", "l7_na_description": "Trade receivables",
                "level_2": "Working capital", "level_3": "Trade receivables", "level_4": None,
                "account_number_group": "AT1200", "entity_prefix": "AT",
                **{k: 1_000_000.0 for k in keys},
                "FY2023": 900_000.0, "FY2024": 950_000.0, "YTD2025": 1_000_000.0,
            },
        ]
        session = _mock_session(grain_rows=grains)
        out = build_wc_monthly(session, year=2025, month=7, span="fy3")
        assert len(out["periods"]) == len(keys)
        assert out["totals"] is not None and len(out["totals"]) == 3
        nwc = next(r for r in out["rows"] if r["id"] == "wc-net-total")
        assert nwc["amounts"]["YTD2025"] == 1_000_000.0
