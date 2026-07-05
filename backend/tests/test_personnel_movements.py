"""Tests for personnel movement detection."""
from __future__ import annotations

import unittest

from app.services.personnel_calc import row_payroll_eur


class PersonnelMovementsLogicTests(unittest.TestCase):
    def test_row_payroll_uses_gesamtsumme(self):
        rec = {"gesamtsumme": 50_000, "grundgehalt": 40_000}
        self.assertEqual(row_payroll_eur(rec), 50_000)

    def test_hire_detection_logic(self):
        prev = {"1001": {"personalnummer": "1001"}}
        cur = {"1001": {"personalnummer": "1001"}, "1002": {"personalnummer": "1002"}}
        hires = [p for p in cur if p not in prev]
        self.assertEqual(hires, ["1002"])

    def test_component_field_keur(self):
        from app.services.personnel_calc import aggregate_metric, chart_display_value

        rows = [{"praemie": 10_000, "months_active": 12, "beschaeftigungsgrad": 100}]
        raw = aggregate_metric(rows, "praemie")
        self.assertEqual(chart_display_value("praemie", raw), 10.0)


if __name__ == "__main__":
    unittest.main()
