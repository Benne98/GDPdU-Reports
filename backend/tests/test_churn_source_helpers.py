"""Churn source helper column registration."""
import unittest

from funktionssammlung import _is_source_helper_header


class TestChurnSourceHelpers(unittest.TestCase):
    def test_churn_arr_is_helper(self):
        self.assertTrue(_is_source_helper_header("_churn_arr_FY23A"))
        self.assertFalse(_is_source_helper_header("Amount"))

    def test_source_range_ref_uses_normalized_header_map(self):
        from openpyxl import Workbook
        from funktionssammlung import _get_sheet_header_map, source_range_ref

        wb = Workbook()
        ws = wb.active
        ws.cell(row=1, column=1).value = "End Customer Country"
        header_map = _get_sheet_header_map(ws)
        ref = source_range_ref("__SOURCE__", header_map, "End Customer Country")
        self.assertIn("__SOURCE__", ref)
        self.assertIn("$A:$A", ref)


if __name__ == "__main__":
    unittest.main()
