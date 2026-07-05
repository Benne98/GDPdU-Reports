"""Golden tests for personnel FTE/payroll aggregation."""
from __future__ import annotations

import unittest

from app.services.personnel_calc import aggregate_metric, personnel_pct_of_output, row_fte


FIXTURE_ROWS_FY22 = [
    {
        "months_active": 12,
        "beschaeftigungsgrad": 100,
        "grundgehalt": 60_000,
        "sozialversicherung": 12_000,
        "gesamtsumme": 72_000,
        "praemie": 0,
        "bereich": "Sales",
    },
    {
        "months_active": 6,
        "beschaeftigungsgrad": 50,
        "grundgehalt": 30_000,
        "sozialversicherung": 6_000,
        "gesamtsumme": 36_000,
        "praemie": 0,
        "bereich": "Admin",
    },
]


class PersonnelCalcTests(unittest.TestCase):
    def test_row_fte_sales(self):
        self.assertAlmostEqual(row_fte(12, 100), 1.0)

    def test_row_fte_admin_part_time(self):
        self.assertAlmostEqual(row_fte(6, 50), 0.25)

    def test_aggregate_fte_fy22(self):
        self.assertEqual(aggregate_metric(FIXTURE_ROWS_FY22, "fte"), 1)

    def test_aggregate_payroll_fy22(self):
        # (72k + 36k) / 1000 = 108 kEUR presented negative
        self.assertEqual(aggregate_metric(FIXTURE_ROWS_FY22, "payroll"), -108.0)

    def test_personnel_pct_output(self):
        # Costs are negative in kEUR; KPI % of output is shown as a positive magnitude.
        self.assertAlmostEqual(personnel_pct_of_output(-108.0, 500.0), 21.6)


if __name__ == "__main__":
    unittest.main()
