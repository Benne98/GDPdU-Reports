"""Workstream 4 — generic FTE/payroll -> fixed personnel schema mapping.

Pure-function coverage of ``build_personnel_rows`` (no DB, no FastAPI): the wizard
selects arbitrary source headers and this maps them 1:1 onto the fixed
``fact_personnel_employee`` fields the Payroll page reads.

Run: DB_PASSWORD=... python -m pytest -q backend/tests/test_personnel_ingest_commit.py
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from app.services.personnel_ingest import build_personnel_rows

_NAMES = {"01": "Atlas"}


def _base_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "PersonID": ["1001", "1002"],
            "EmploymentPct": ["100", "80"],
            "MonthsEmployed": ["12", "6"],
            "EntryDate": ["2023-01-01", "2023-03-01"],
            "ExitDate": ["", "2023-12-31"],
            "TotalCost": ["50000", "40000"],
            "MonthlyCost": ["5000", "4000"],
            "SocialSecurity": ["9000", "7000"],
            "SalaryComp": ["45000", "36000"],
            "BonusComp": ["5000", "4000"],
            "Department": ["IT", "Finance"],
        }
    )


def test_months_col_and_component_sum() -> None:
    rows, prefixes, cmap = build_personnel_rows(
        _base_df(),
        year=2023,
        fy_label="FY2023",
        file_id="f1",
        tenure_mode="months_col",
        payroll_mode="sum_components",
        entity_prefix="01",
        employment_pct_col="EmploymentPct",
        months_col="MonthsEmployed",
        component_cols=["SalaryComp", "BonusComp"],
        personalnummer_col="PersonID",
        bereich_col="Department",
        name_by_prefix=_NAMES,
    )

    assert prefixes == {"01"}
    assert cmap["gesamtsumme"] == "__payroll__"
    assert cmap["months_active"] == "MonthsEmployed"

    r0 = rows[0]
    assert r0["beschaeftigungsgrad"] == 100.0     # employment% -> beschaeftigungsgrad
    assert r0["months_active"] == 12.0            # months col pass-through
    assert r0["gesamtsumme"] == 50000.0           # 45000 + 5000 components summed
    assert r0["bereich"] == "IT"                  # named dim
    assert r0["personalnummer"] == "1001"         # mapped personnel number
    assert r0["entity_prefix"] == "01"
    assert r0["entity_name"] == "Atlas"
    assert r0["as_of_date"] == date(2023, 12, 31)  # year-end snapshot
    assert rows[1]["gesamtsumme"] == 40000.0       # 36000 + 4000


def test_monthly_col_annualized() -> None:
    rows, _prefixes, _cmap = build_personnel_rows(
        _base_df(),
        year=2023,
        fy_label="FY2023",
        file_id="f1",
        tenure_mode="months_col",
        payroll_mode="monthly_col",
        entity_prefix="01",
        employment_pct_col="EmploymentPct",
        months_col="MonthsEmployed",
        monthly_col="MonthlyCost",
        social_col="SocialSecurity",
        name_by_prefix=_NAMES,
    )
    assert rows[0]["gesamtsumme"] == 60000.0   # 5000 * 12
    assert rows[1]["gesamtsumme"] == 48000.0   # 4000 * 12
    assert rows[0]["sozialversicherung"] == 9000.0


def test_entry_exit_months_overlap() -> None:
    rows, _prefixes, cmap = build_personnel_rows(
        _base_df(),
        year=2023,
        fy_label="FY2023",
        file_id="f1",
        tenure_mode="entry_exit_dates",
        payroll_mode="total_col",
        entity_prefix="01",
        employment_pct_col="EmploymentPct",
        entry_col="EntryDate",
        exit_col="ExitDate",
        total_col="TotalCost",
        name_by_prefix=_NAMES,
    )
    assert cmap["months_active"] == "__months__"
    assert cmap["gesamtsumme"] == "TotalCost"
    assert rows[0]["months_active"] == 12.0   # Jan..Dec (exit blank -> year end)
    assert rows[1]["months_active"] == 10.0   # Mar..Dec inclusive
    assert rows[0]["gesamtsumme"] == 50000.0  # total col pass-through, sign preserved


def test_personalnummer_synthesised_when_unmapped() -> None:
    rows, _prefixes, _cmap = build_personnel_rows(
        _base_df(),
        year=2024,
        fy_label="FY2024",
        file_id="f1",
        tenure_mode="months_col",
        payroll_mode="total_col",
        entity_prefix="01",
        employment_pct_col="EmploymentPct",
        months_col="MonthsEmployed",
        total_col="TotalCost",
        name_by_prefix=_NAMES,
        # personalnummer_col deliberately omitted
    )
    # NOT NULL synthesis from row_no
    assert rows[0]["personalnummer"] == "0"
    assert rows[1]["personalnummer"] == "1"
    assert rows[0]["as_of_date"] == date(2024, 12, 31)
