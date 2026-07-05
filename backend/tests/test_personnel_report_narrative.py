"""Tests for payroll accounting report narratives."""
from __future__ import annotations

import unittest
from datetime import date

from app.services.personnel_report_narrative import build_payroll_narrative


def _row(bereich: str, months: float, grad: float, gesamt: float, pno: str) -> dict:
    return {
        "personalnummer": pno,
        "bereich": bereich,
        "months_active": months,
        "beschaeftigungsgrad": grad,
        "grundgehalt": gesamt * 0.8,
        "sozialversicherung": gesamt * 0.2,
        "gesamtsumme": gesamt,
        "praemie": 0,
    }


class PayrollReportNarrativeTests(unittest.TestCase):
    def test_intro_and_department_bullets(self) -> None:
        d22 = date(2022, 12, 31)
        d23 = date(2023, 12, 31)
        d24 = date(2024, 12, 31)
        d25 = date(2025, 12, 31)
        rows_by_date = {
            d22: [_row("Sales", 12, 100, 60_000, "1")],
            d23: [_row("Sales", 12, 100, 65_000, "1")],
            d24: [
                _row("Sales", 12, 100, 70_000, "1"),
                _row("Admin", 12, 100, 40_000, "2"),
            ],
            d25: [
                _row("Sales", 12, 100, 80_000, "1"),
                _row("Admin", 12, 100, 45_000, "2"),
                _row("Admin", 6, 50, 20_000, "3"),
            ],
        }
        out = build_payroll_narrative(
            anchor_date=d25,
            prior_date=d24,
            col_dates=[d22, d23, d24, d25],
            rows_by_date=rows_by_date,
            bereich_list=["Admin", "Sales"],
            movements={
                "counts": {"hires": 1, "terms": 0, "raises": 1},
                "charts": {"top_raises": [{"bereich": "Sales", "delta_eur": 8000}]},
            },
            use_llm=False,
        )
        self.assertEqual(out.get("intro"), "")
        self.assertGreaterEqual(len(out["bullets"]), 2)
        row_ids = {b["row_id"] for b in out["bullets"]}
        self.assertTrue(row_ids & {"payroll-Sales", "payroll-Admin"})
        self.assertTrue(all(rid.startswith("payroll-") for rid in row_ids))
        self.assertNotIn("fte-total", row_ids)
        texts = " ".join(b["text"] for b in out["bullets"])
        self.assertIn("In Sales", texts)
        self.assertIn("payroll accounting", texts.lower())
        # Flowing prose: opening + driver sentence (not a single comma chain).
        for b in out["bullets"]:
            if b["row_id"] == "payroll-Sales":
                self.assertGreaterEqual(len(b["text"].split(".")), 2)
                break


if __name__ == "__main__":
    unittest.main()
