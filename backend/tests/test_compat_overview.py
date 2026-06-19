"""DB-free tests for the Overview financial compat layer.

Tests the four public builders in app/services/fin_compat_overview.py by feeding
synthetic structure + grain rows through a mock Session and asserting:
  (a) response shape matches the TypeScript interfaces (FinancialsOverviewResponse,
      FinancialsEntityBreakdownResponse, OverviewHighlight, EntityBreakdownArea)
  (b) computed values are correct (margin %, delta arithmetic)
  (c) intro / highlights / areas are non-empty strings so the frontend never
      renders empty tiles

=== SYNTHETIC DATA (kEUR) ===
  Entities: AT, DE
  P&L structure (mapping lines only for simplicity):
    NET_SALES        mapping  level_3='Net sales'
    GROSS_PROFIT     subtotal
    EBITDA           subtotal
    NET_PROFIT       subtotal

  Month grain grain rows (entity='all', i.e. no entity filter):
    NET_SALES:   cm=500, pm=450, py_cm=400, ytd=1500, ytd_py=1200
    GROSS_PROFIT (subtotal): running sum = NET_SALES (only mapping line above)
    → GROSS_PROFIT cm = 500  (simplification: one mapping line only)

  Consolidation rows (per entity, cm column):
    AT: NET_SALES cm=300, GROSS_PROFIT cm=180, EBITDA cm=60, NET_PROFIT cm=40
    DE: NET_SALES cm=200, GROSS_PROFIT cm=120, EBITDA cm=40, NET_PROFIT cm=25
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# _DictRow helper (SQLAlchemy Row-like)
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


def _dr(d: dict) -> _DictRow:
    return _DictRow(d)


# ---------------------------------------------------------------------------
# Synthetic structure rows
# ---------------------------------------------------------------------------

def _pl_struct(pl_line_id, sort_order, line_code, row_type, *,
               balance_title=None, level_2="Income", level_3=None, level_4=None,
               is_bold=False, kpi_code=None):
    return {
        "pl_line_id": pl_line_id,
        "sort_order": sort_order,
        "line_code": line_code,
        "row_type": row_type,
        "balance_title": balance_title or line_code,
        "details": None,
        "calc_type": None,
        "level_2": level_2,
        "level_3": level_3,
        "level_4": level_4,
        "gl_account_id": None,
        "invert_delta": False,
        "is_bold": is_bold,
        "kpi_code": kpi_code,
    }


_PL_STRUCTURE = [
    _pl_struct(1, 10,  "NET_SALES",    "mapping",  balance_title="Net Sales",    level_3="Net sales"),
    _pl_struct(2, 20,  "TOTAL_OUTPUT", "subtotal", balance_title="Total Output", is_bold=True),
    _pl_struct(3, 30,  "GROSS_PROFIT", "subtotal", balance_title="Gross Profit", is_bold=True),
    _pl_struct(4, 40,  "EBITDA",       "subtotal", balance_title="EBITDA",       is_bold=True),
    _pl_struct(5, 50,  "EBIT",         "subtotal", balance_title="EBIT",         is_bold=True),
    _pl_struct(6, 100, "NET_PROFIT",   "grandtotal", balance_title="Net Income", is_bold=True),
]

# Grain row for the consolidated statement (entity=all).
_GRAIN_CONSOLIDATED = [
    {
        "level_2": "Income", "level_3": "Net sales", "level_4": None,
        "gl_account_id": None, "account_number_group": "1000",
        "account_name": "Net sales",
        "py_cm": 400.0, "pm": 450.0, "cm": 500.0, "ytd": 1500.0, "ytd_py": 1200.0,
    },
]

# Annual exit-readiness grain (pl_grain_sql_annual).
_ANNUAL_GRAIN = [
    {
        "level_2": "Income", "level_3": "Net sales", "level_4": None,
        "gl_account_id": None, "account_number_group": "1000",
        "account_name": "Net sales",
        "fy1": 100.0, "fy2": 200.0, "fy3": 300.0,
        "ytd": 500.0, "ytd_py": 400.0, "ltm": 480.0, "ltm_py": 420.0,
    },
]

_GRAIN_ENTITY = [
    {
        "level_2": "Income", "level_3": "Net sales", "level_4": None,
        "gl_account_id": None, "account_number_group": "1000",
        "entity_prefix": "AT",
        "py_cm": 240.0, "pm": 270.0, "cm": 300.0, "ytd": 900.0, "ytd_py": 720.0,
    },
    {
        "level_2": "Income", "level_3": "Net sales", "level_4": None,
        "gl_account_id": None, "account_number_group": "1001",
        "entity_prefix": "DE",
        "py_cm": 160.0, "pm": 180.0, "cm": 200.0, "ytd": 600.0, "ytd_py": 480.0,
    },
]

_ENTITY_ROWS = [
    _dr({"legal_entity_code": "AT", "entity_prefix": "AT", "entity_name": "Austria GmbH"}),
    _dr({"legal_entity_code": "DE", "entity_prefix": "DE", "entity_name": "Germany GmbH"}),
]


# ---------------------------------------------------------------------------
# Mock Session factory
# ---------------------------------------------------------------------------

def _mock_session(
    structure=None,
    grain_consolidated=None,
    grain_entity=None,
    entity_rows=None,
) -> MagicMock:
    structure       = structure        or _PL_STRUCTURE
    grain_consol    = grain_consolidated or _GRAIN_CONSOLIDATED
    grain_ent       = grain_entity     or _GRAIN_ENTITY
    ent_rows        = entity_rows      or _ENTITY_ROWS

    session = MagicMock()

    def _execute(stmt, params=None):
        sql = str(stmt)
        result = MagicMock()
        rows: list[Any] = []

        if "dim_pl_structure" in sql:
            rows = [_dr(r) for r in structure]
        elif "dim_legal_entity" in sql:
            rows = list(ent_rows)
        elif "fy1" in sql or "ltm_py" in sql:
            rows = [_dr(r) for r in _ANNUAL_GRAIN]
        elif "entity_prefix" in sql or "legal_entity_code" in sql.lower():
            # consolidation grain (pl_consl_grain_sql_month)
            rows = [_dr(r) for r in grain_ent]
        else:
            # normal PL grain (pl_grain_sql_month / pl_grain_sql_week)
            rows = [_dr(r) for r in grain_consol]

        result.fetchall.return_value = rows
        result.fetchone.return_value = rows[0] if rows else None
        return result

    session.execute.side_effect = _execute
    return session


# ---------------------------------------------------------------------------
# build_overview_response tests
# ---------------------------------------------------------------------------

from app.services.fin_compat_overview import (
    build_entity_breakdown_narratives,
    build_entity_breakdown_response,
    build_overview_highlights,
    build_overview_response,
)


class TestBuildOverviewResponse:
    def _call(self, **kw):
        session = _mock_session()
        return build_overview_response(
            session, year=2024, month=6,
            include_highlights=kw.pop("include_highlights", False),
            **kw,
        )

    def test_statement_field(self):
        result = self._call()
        assert result["statement"] == "overview"

    def test_period_fields_present(self):
        result = self._call()
        assert result["year"] == 2024
        assert result["month"] == 6
        assert "period_grain" in result

    def test_year_grain_returns_overview(self):
        result = self._call(period_grain="year")
        assert result["period_grain"] == "year"
        assert result["statement"] == "overview"
        assert len(result.get("sections") or []) >= 1

    def test_col_labels_present(self):
        result = self._call()
        labels = result["col_labels"]
        for key in ("py_cm", "pm", "cm", "ytd", "ytd_py"):
            assert key in labels, f"col_labels missing '{key}'"
        assert all(isinstance(v, str) for v in labels.values())

    def test_intro_non_empty(self):
        result = self._call()
        assert isinstance(result["intro"], str)
        assert len(result["intro"]) > 10

    def test_sections_non_empty(self):
        result = self._call()
        sections = result["sections"]
        assert isinstance(sections, list)
        assert len(sections) >= 1, "At least one section expected"

    def test_section_earnings_present(self):
        result = self._call()
        ids = [s["id"] for s in result["sections"]]
        assert "earnings" in ids

    def test_section_balance_sheet_present(self):
        result = self._call()
        ids = [s["id"] for s in result["sections"]]
        assert "position" in ids

    def test_section_cash_flow_present(self):
        result = self._call()
        ids = [s["id"] for s in result["sections"]]
        assert "finance" in ids

    def test_section_rows_shape(self):
        result = self._call()
        for section in result["sections"]:
            assert "id" in section
            assert "title" in section
            assert isinstance(section["rows"], list)
            for row in section["rows"]:
                assert "id" in row
                assert "label" in row
                assert "row_kind" in row
                assert "unit" in row
                assert isinstance(row["amounts"], dict)
                assert isinstance(row["deltas"], dict)

    def test_total_output_cm_value(self):
        """TOTAL_OUTPUT cm from the mocked PL statement (single mapping line)."""
        result = self._call()
        earnings = next(s for s in result["sections"] if s["id"] == "earnings")
        to_row = next((r for r in earnings["rows"] if r.get("id") == "total_output"), None)
        assert to_row is not None, "Total output row not found in earnings section"
        assert to_row["amounts"]["cm"] == pytest.approx(500.0, abs=0.01)

    def test_deltas_arithmetic(self):
        """mom = cm - pm = 500 - 450 = 50; yoy = cm - py_cm = 500 - 400 = 100."""
        result = self._call()
        earnings = next(s for s in result["sections"] if s["id"] == "earnings")
        to_row = next(r for r in earnings["rows"] if r.get("id") == "total_output")
        assert to_row["deltas"]["mom"] == pytest.approx(50.0, abs=0.01)
        assert to_row["deltas"]["yoy"] == pytest.approx(100.0, abs=0.01)

    def test_highlights_empty_when_not_requested(self):
        result = self._call(include_highlights=False)
        assert result["highlights"] == []

    def test_highlights_non_empty_when_requested(self):
        result = self._call(include_highlights=True)
        h = result["highlights"]
        assert isinstance(h, list)
        assert len(h) >= 1

    def test_entity_snapshots_present(self):
        result = self._call()
        snaps = result["entity_snapshots"]
        assert isinstance(snaps, list)
        assert len(snaps) == 2  # AT + DE
        for snap in snaps:
            assert "code" in snap
            assert "name" in snap
            assert "net_profit_cm" in snap
            assert "ebitda_cm" in snap


# ---------------------------------------------------------------------------
# build_overview_highlights tests
# ---------------------------------------------------------------------------

class TestBuildOverviewHighlights:
    def _call(self, **kw):
        session = _mock_session()
        return build_overview_highlights(session, year=2024, month=6, **kw)

    def test_year_grain_builds_highlights(self, monkeypatch):
        def _fake_build_and_store(session, **kwargs):
            assert kwargs.get("period_grain") == "year"
            return {
                "intro": "FY narrative intro.",
                "bullets": [{"index": 1, "text": "Annual driver bullet."}],
            }

        monkeypatch.setattr(
            "app.services.fin_compat_overview_narrative_snapshots.get_cached_snapshot",
            lambda *a, **k: None,
        )
        monkeypatch.setattr(
            "app.services.fin_compat_overview_narrative_snapshots._build_and_store",
            _fake_build_and_store,
        )
        h = self._call(period_grain="year")["highlights"]
        assert len(h) >= 1
        assert any(x.get("intro") for x in h)
        assert any(x.get("bullets") for x in h)

    def test_key_present(self):
        result = self._call()
        assert "highlights" in result

    def test_pl_tab_present(self):
        h = self._call()["highlights"]
        tabs = [x["tab"] for x in h]
        assert "pl" in tabs

    def test_pl_tab_label(self):
        h = self._call()["highlights"]
        pl = next(x for x in h if x["tab"] == "pl")
        assert pl.get("tab_label") == "Income Statement"

    def test_pl_bullets_non_empty(self):
        h = self._call()["highlights"]
        pl = next(x for x in h if x["tab"] == "pl")
        assert isinstance(pl.get("bullets"), list)
        assert len(pl["bullets"]) >= 1

    def test_pl_intro_non_empty(self):
        h = self._call()["highlights"]
        pl = next(x for x in h if x["tab"] == "pl")
        assert isinstance(pl.get("intro"), str)
        assert len(pl["intro"]) > 5

    def test_bullet_text_non_empty(self):
        h = self._call()["highlights"]
        for highlight in h:
            for bullet in highlight.get("bullets", []):
                assert isinstance(bullet.get("text"), str)
                assert len(bullet["text"]) > 5


# ---------------------------------------------------------------------------
# build_entity_breakdown_response tests
# ---------------------------------------------------------------------------

class TestBuildEntityBreakdownResponse:
    def _call(self, **kw):
        session = _mock_session()
        return build_entity_breakdown_response(session, year=2024, month=6, **kw)

    def test_cm_label_present(self):
        result = self._call()
        assert isinstance(result["cm_label"], str)
        assert len(result["cm_label"]) > 0

    def test_entities_shape(self):
        result = self._call()
        entities = result["entities"]
        assert isinstance(entities, list)
        assert len(entities) == 2
        for e in entities:
            assert "code" in e
            assert "name" in e
            assert "display_name" in e

    def test_entity_codes(self):
        result = self._call()
        codes = {e["code"] for e in result["entities"]}
        assert "AT" in codes
        assert "DE" in codes

    def test_rows_non_empty(self):
        result = self._call()
        rows = result["rows"]
        assert isinstance(rows, list)
        assert len(rows) >= 1

    def test_row_shape(self):
        result = self._call()
        for row in result["rows"]:
            assert "id" in row
            assert "label" in row
            assert "row_kind" in row
            assert "unit" in row
            assert isinstance(row["cm_by_entity"], dict)

    def test_net_sales_per_entity(self):
        """NET_SALES cm_by_entity: AT=300, DE=200."""
        result = self._call()
        ns_row = next((r for r in result["rows"] if "Net Sales" in r["label"]), None)
        assert ns_row is not None, "Net Sales row not found"
        assert ns_row["cm_by_entity"]["AT"] == pytest.approx(300.0, abs=0.01)
        assert ns_row["cm_by_entity"]["DE"] == pytest.approx(200.0, abs=0.01)

    def test_areas_non_empty(self):
        result = self._call()
        areas = result["areas"]
        assert isinstance(areas, list)
        assert len(areas) >= 1

    def test_areas_shape(self):
        result = self._call()
        for area in result["areas"]:
            assert "id" in area
            assert "title" in area
            assert "tab" in area
            assert isinstance(area.get("intro"), str)
            assert len(area["intro"]) > 0
            assert isinstance(area.get("bullets"), list)
            assert len(area["bullets"]) >= 1

    def test_area_bullets_have_text(self):
        result = self._call()
        for area in result["areas"]:
            for bullet in area["bullets"]:
                assert isinstance(bullet.get("text"), str)
                assert len(bullet["text"]) > 5


# ---------------------------------------------------------------------------
# build_entity_breakdown_narratives tests
# ---------------------------------------------------------------------------

class TestBuildEntityBreakdownNarratives:
    def _call(self):
        session = _mock_session()
        return build_entity_breakdown_narratives(session, year=2024, month=6)

    def test_areas_key_present(self):
        result = self._call()
        assert "areas" in result

    def test_areas_non_empty(self):
        areas = self._call()["areas"]
        assert isinstance(areas, list)
        assert len(areas) >= 1

    def test_areas_have_non_empty_bullets(self):
        areas = self._call()["areas"]
        for area in areas:
            assert isinstance(area["bullets"], list)
            assert len(area["bullets"]) >= 1

    def test_pl_area_has_entity_bullets(self):
        """The 'pl' area should have one bullet per legal entity."""
        areas = self._call()["areas"]
        pl_area = next((a for a in areas if a["id"] == "pl"), None)
        assert pl_area is not None, "'pl' area not found"
        entity_codes = {b.get("entity_code") for b in pl_area["bullets"] if b.get("entity_code")}
        assert "AT" in entity_codes
        assert "DE" in entity_codes

    def test_area_tabs_valid(self):
        areas = self._call()["areas"]
        valid_tabs = {"pl", "bs", "cf", "wc"}
        for area in areas:
            assert area["tab"] in valid_tabs, f"Invalid tab '{area['tab']}'"


class TestOverviewSectionsYearGrain:
    def test_snapshot_annual_bs_maps_to_overview_amounts(self, monkeypatch):
        from app.services import fin_compat_overview_sections as sec

        pl = {"rows": [_pl_struct(1, 100, "TOTAL_OUTPUT", "mapping", balance_title="Total output")]}
        bs = {
            "rows": [{
                "label": "Fixed assets",
                "line_code": "FA",
                "row_kind": "line",
                "amounts": {"fy_py": 100, "fy": 200, "cm_py": 250, "cm": 300},
                "deltas": {"delta_cm": 50},
                "children": [],
            }],
        }
        wc = {"rows": []}
        cf = {
            "rows": [
                {
                    "label": "Operating cash flow",
                    "line_code": "CF_OP",
                    "row_kind": "subtotal",
                    "amounts": {"ytd": 500, "ltm": 480, "ytd_py": 400},
                    "deltas": {},
                    "children": [],
                },
                {
                    "label": "Free cash flow",
                    "line_code": "CF_FREE_CASH_FLOW",
                    "row_kind": "subtotal",
                    "amounts": {"ytd": 120, "ltm": 100, "ytd_py": 80},
                    "deltas": {},
                    "children": [],
                },
            ],
        }

        monkeypatch.setattr(sec, "build_pl_annual_compat", lambda *a, **k: pl)
        monkeypatch.setattr(sec, "build_bs_snapshot_annual", lambda *a, **k: bs)
        monkeypatch.setattr(sec, "build_wc_snapshot_annual", lambda *a, **k: wc)
        monkeypatch.setattr(sec, "build_cf_annual_compat", lambda *a, **k: cf)
        monkeypatch.setattr(sec, "_net_financial_debt", lambda *a, **k: {k: 0.0 for k in sec._AM_KEYS})
        monkeypatch.setattr(sec, "_load_plan_map", lambda *a, **k: {})

        blocks = sec.build_overview_sections(
            MagicMock(), period_grain="year", year=2025, month=7, entity=None,
        )
        fa = next(r for r in blocks["position"] if r["id"] == "fixed_assets")
        assert fa["amounts"]["cm"] == 300
        assert fa["amounts"]["py_cm"] == 250
        ocf = next(r for r in blocks["finance"] if r["id"] == "operating_cf")
        fcf = next(r for r in blocks["finance"] if r["id"] == "free_cash_flow")
        assert ocf["amounts"]["cm"] == 500
        assert fcf["amounts"]["cm"] == 120
