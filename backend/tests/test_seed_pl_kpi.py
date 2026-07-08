"""seed_pl_kpi_rows — the durable 'KPIs as % of total output' block.

These seven row_type='kpi' rows are NOT in the Decidra Account_Mapping.xlsx, so a
from-scratch rebuild / fresh DB would otherwise carry NO KPI block. seed_pl_kpi_rows
re-asserts them idempotently and is wired into the rebuild's structure refresh. This
locks: (1) all seven codes seed, in the confirmed P&L reading order; (2) they are
placed immediately AFTER the last non-KPI P&L line; (3) a second run is a no-op
(no duplicates, stable sort). No financial NUMBER is computed here — the ratio math
lives in fin_compat_pl — this only guarantees the rows EXIST after any reload.
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from scripts.seed_pl_structure import _PL_KPI_ROWS, seed_pl_kpi_rows

_EXPECTED = [
    ("GROSS_MARGIN_PCT", "Gross margin %", "GROSS_PROFIT"),
    ("PERSONNEL_EXPENSES_PCT", "Personnel expenses %", "PERSONNEL_EXPENSES"),
    ("OTHER_OPERATING_INCOME_PCT", "Other operating income %", "OTHER_OPERATING_INCOME"),
    ("OTHER_OPERATING_EXPENSES_PCT", "Other operating expenses %", "OTHER_OPERATING_EXPENSES"),
    ("EBITDA_MARGIN_PCT", "EBITDA margin %", "EBITDA"),
    ("EBIT_MARGIN_PCT", "EBIT margin %", "EBIT"),
    ("NET_PROFIT_MARGIN_PCT", "Net profit margin %", "NET_PROFIT"),
]


def _make_session() -> Session:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text(
            """
            CREATE TABLE dim_pl_structure (
                pl_line_id INTEGER PRIMARY KEY AUTOINCREMENT,
                sort_order INTEGER, line_code TEXT UNIQUE, row_type TEXT,
                balance_title TEXT, details INTEGER, calc_type INTEGER,
                level_2 TEXT, level_3 TEXT, level_4 TEXT, gl_account_id TEXT,
                invert_delta INTEGER, is_bold INTEGER, kpi_code TEXT, source TEXT
            )
            """
        ))
    return Session(engine)


def _pl_line(session, sort_order, line_code, row_type="mapping"):
    session.execute(text(
        "INSERT INTO dim_pl_structure (sort_order, line_code, row_type) "
        "VALUES (:so,:lc,:rt)"
    ), {"so": sort_order, "lc": line_code, "rt": row_type})


def _kpi_rows(session):
    return session.execute(text(
        "SELECT sort_order, line_code, balance_title, kpi_code, row_type, source "
        "FROM dim_pl_structure WHERE row_type='kpi' ORDER BY sort_order"
    )).fetchall()


class TestSeedPlKpiRows:
    def test_seeds_seven_rows_in_order_on_empty_table(self):
        s = _make_session()
        n = seed_pl_kpi_rows(s)
        assert n == 7
        rows = _kpi_rows(s)
        assert [(r[1], r[2], r[3]) for r in rows] == _EXPECTED
        # every row is a 'kpi' seed row, calc_type NULL-free is not required here
        assert all(r[4] == "kpi" and r[5] == "seed" for r in rows)

    def test_placed_after_last_non_kpi_line(self):
        s = _make_session()
        # a realistic P&L tail: Net profit at sort 20
        _pl_line(s, 4, "TOTAL_OUTPUT", "subtotal")
        _pl_line(s, 6, "GROSS_PROFIT", "calc")
        _pl_line(s, 20, "NET_PROFIT", "subtotal")
        seed_pl_kpi_rows(s)
        rows = _kpi_rows(s)
        # base = max non-kpi sort (20) → KPI block occupies 21..27, in order
        assert [r[0] for r in rows] == [21, 22, 23, 24, 25, 26, 27]
        assert rows[0][1] == "GROSS_MARGIN_PCT"
        assert rows[-1][1] == "NET_PROFIT_MARGIN_PCT"

    def test_idempotent_second_run_no_duplicates(self):
        s = _make_session()
        _pl_line(s, 20, "NET_PROFIT", "subtotal")
        seed_pl_kpi_rows(s)
        first = _kpi_rows(s)
        n2 = seed_pl_kpi_rows(s)
        second = _kpi_rows(s)
        assert n2 == 7
        assert len(second) == 7  # no duplicate rows
        # sort_order stable across re-runs (base unchanged by the KPI rows themselves)
        assert [r[0] for r in first] == [r[0] for r in second]

    def test_reasserts_after_wipe(self):
        s = _make_session()
        _pl_line(s, 20, "NET_PROFIT", "subtotal")
        seed_pl_kpi_rows(s)
        s.execute(text("DELETE FROM dim_pl_structure WHERE row_type='kpi'"))
        assert _kpi_rows(s) == []
        seed_pl_kpi_rows(s)
        assert len(_kpi_rows(s)) == 7

    def test_constant_matches_expected_contract(self):
        # guards against an accidental reorder / code drift in the module constant
        assert [(lc, bt, kc) for _, lc, bt, kc in _PL_KPI_ROWS] == _EXPECTED
        assert [off for off, *_ in _PL_KPI_ROWS] == [1, 2, 3, 4, 5, 6, 7]
