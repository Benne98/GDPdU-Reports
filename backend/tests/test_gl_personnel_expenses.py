"""gl_personnel_expenses_keur — GL auto-derivation of FTE-workbook PEX rows.

Financial proof for the helper that lets the Project-Setup wizard drop the manual
PEX grid: an empty ``pex_values`` request must be filled from the loaded GL P&L.

FORMULA (per entity e, year fy):
    GL personnel expenses (EURk) = abs( PERSONNEL_EXPENSES P&L line YTD ) / 1000

Worked example: PERSONNEL_EXPENSES ytd = -1,234,000 → 1234.0 under entity1_FY2023.
Edge cases proved here: missing line / None ytd → 0.0; single combined entity
emits only entity1_* keys; per_entity iterates entity_names in order.

build_pl_annual_compat is stubbed (it needs a live GDPdU Postgres); this test
locks the helper's OWN logic — key format, abs()+/1000 scale/sign, FY iteration.
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import app.services.personnel_accounting as pa
from app.services.personnel_accounting import gl_personnel_expenses_keur


def _stub_pl(monkeypatch, table: dict[tuple[object, int], object]) -> None:
    """table: {(entity, year): ytd_amount_or_None_or_'MISSING'} → fake PL dict."""

    def _fake(session, *, year, month, entity=None, ent_frag_override=None):
        val = table.get((entity, year), "MISSING")
        if val == "MISSING":
            return {"rows": [{"line_code": "NET_SALES", "amounts": {"ytd": 10.0}}]}
        return {
            "rows": [
                {"line_code": "NET_SALES", "amounts": {"ytd": 10.0}},
                {"line_code": "PERSONNEL_EXPENSES", "amounts": {"ytd": val}},
            ]
        }

    monkeypatch.setattr(pa, "build_pl_annual_compat", _fake)


def test_worked_example_consolidated(monkeypatch):
    # PERSONNEL_EXPENSES ytd = -1,234,000 → 1234.0 under entity1_FY2023
    _stub_pl(monkeypatch, {(None, 2023): -1_234_000.0})
    out = gl_personnel_expenses_keur(
        None, first_fy=2023, last_fy=2023, fy_end_month=12,
        entity_names=["ACME"], view_mode="consolidated",
    )
    assert out == {"entity1_FY2023": 1234.0}


def test_consolidated_multi_year_only_entity1_keys(monkeypatch):
    _stub_pl(monkeypatch, {
        (None, 2022): -1_000_000.0,
        (None, 2023): -1_234_000.0,
    })
    out = gl_personnel_expenses_keur(
        None, first_fy=2022, last_fy=2023, fy_end_month=12,
        entity_names=["ACME", "BETA"], view_mode="consolidated",
    )
    assert out == {"entity1_FY2022": 1000.0, "entity1_FY2023": 1234.0}


def test_per_entity_iterates_in_order(monkeypatch):
    _stub_pl(monkeypatch, {
        ("ACME", 2023): -500_000.0,
        ("BETA", 2023): -750_500.0,
    })
    out = gl_personnel_expenses_keur(
        None, first_fy=2023, last_fy=2023, fy_end_month=12,
        entity_names=["ACME", "BETA"], view_mode="per_entity",
    )
    assert out == {"entity1_FY2023": 500.0, "entity2_FY2023": 750.5}


def test_missing_line_and_none_ytd_yield_zero(monkeypatch):
    # 2022 → no PERSONNEL_EXPENSES row; 2023 → ytd is None.
    _stub_pl(monkeypatch, {(None, 2023): None})
    out = gl_personnel_expenses_keur(
        None, first_fy=2022, last_fy=2023, fy_end_month=12,
        entity_names=["ACME"], view_mode="consolidated",
    )
    assert out == {"entity1_FY2022": 0.0, "entity1_FY2023": 0.0}
