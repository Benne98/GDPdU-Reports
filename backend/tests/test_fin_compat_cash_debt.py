"""Tests for net debt table builder."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.services.fin_compat_cash_debt import _bank_label, _is_cash_on_hand, _to_keur, build_net_debt_table


class NetDebtHelpersTests(unittest.TestCase):
    def test_to_keur(self):
        self.assertEqual(_to_keur(264_000), 264.0)

    def test_cash_on_hand(self):
        self.assertTrue(_is_cash_on_hand("Kasse", None))
        self.assertFalse(_is_cash_on_hand("Commerzbank Giro", None))

    def test_bank_label(self):
        self.assertEqual(_bank_label("Commerzbank Giro"), "Commerzbank")
        self.assertIn("Volksbank", _bank_label("Volksbank Konto 123"))

    @patch("app.services.fin_compat_cash_debt._fetch_accounts_multi")
    def test_build_groups_bank_liabilities(self, mock_fetch):
        anchor = "2025-12-31"
        mock_fetch.return_value = [
            {
                "account_number_group": "3300",
                "gl_account_id": "3300",
                "account_name": "Commerzbank Giro",
                "level_3": "Liabilities due to banks",
                "level_4": "",
                "l6_na": "ND",
                "l7_na": "",
                "balances_eur": {anchor: -1_000_000},
                "balances_keur": {anchor: -1000.0},
                "balance_eur": -1_000_000,
                "balance_keur": -1000.0,
            },
        ]
        out = build_net_debt_table(MagicMock(), year=2025, month=12)
        self.assertEqual(out["net_debt_keur"], -1000.0)
        self.assertIn(anchor, out["col_keys"])
        bank_row = next(r for r in out["rows"] if r["id"] == "bank")
        self.assertEqual(bank_row["amount_keur"], -1000.0)
        self.assertIn("narrative", out)


if __name__ == "__main__":
    unittest.main()
