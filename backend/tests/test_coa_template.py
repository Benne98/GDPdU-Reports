"""CoA Master template export — workbook shape, round-trip-into-importer, prefill.

DB-free for the builder/empty/library/round-trip paths; the dim_gl_account prefill
path is exercised with a fake session (no real DB) and is v2-safe (read-only SELECT).
"""
from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "backend", ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from app import coa_template  # noqa: E402
from etl.bs_pl_master import (  # noqa: E402
    BS_SHEET,
    PL_SHEET,
    bs_pl_master_profile,
    detect_level_columns,
    is_bs_pl_master_workbook,
    read_bs_pl_master,
)


def _save(wb) -> io.BytesIO:
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


class TestWorkbookShape(unittest.TestCase):
    def test_two_master_sheets_with_exact_headers(self):
        wb = coa_template.build_coa_template_workbook(
            coa_template.empty_template_rows("Acme GmbH")
        )
        # Both Master sheets present (a hidden "Lists" sheet for the dropdowns may
        # also be appended — the importer gate tolerates it).
        self.assertTrue({BS_SHEET, PL_SHEET}.issubset(wb.sheetnames))
        # is_bs_pl_master_workbook is the importer's own gate.
        self.assertTrue(is_bs_pl_master_workbook(wb.sheetnames))

        bs_headers = [c.value for c in wb[BS_SHEET][1]][: len(coa_template.BS_HEADERS)]
        pl_headers = [c.value for c in wb[PL_SHEET][1]][: len(coa_template.PL_HEADERS)]
        self.assertEqual(bs_headers, coa_template.BS_HEADERS)
        self.assertEqual(pl_headers, coa_template.PL_HEADERS)
        # PL sheet uses the "L1 - BS/PL" first level column (importer renames it).
        self.assertIn("L1 - BS/PL", pl_headers)

    def test_empty_template_builds_with_examples(self):
        # Build WITHOUT cosmetic canvas styling so row counts reflect data only.
        wb = coa_template.build_coa_template_workbook(
            coa_template.empty_template_rows(""), style=False
        )
        # A few clearly-marked example rows per sheet beyond the header.
        self.assertEqual(self._data_account_count(wb[BS_SHEET]), 2)
        self.assertEqual(self._data_account_count(wb[PL_SHEET]), 2)
        # Examples are flagged as such (EXAMPLE-prefixed account number).
        for sheet in (BS_SHEET, PL_SHEET):
            # Account is now index 0 (col A) — no Entity column prepended.
            accounts = [c[0].value for c in wb[sheet].iter_rows(min_row=2)
                        if c[0].value]
            self.assertTrue(all(str(a).startswith("EXAMPLE") for a in accounts))


    def test_hierarchy_dropdowns_present(self):
        wb = coa_template.build_coa_template_workbook(
            coa_template.empty_template_rows("Acme GmbH")
        )
        # Hidden lookup sheet with our canonical labels.
        self.assertIn("Lists", wb.sheetnames)
        self.assertEqual(wb["Lists"].sheet_state, "hidden")
        # L2/L3/L4 columns (E/F/G) carry list data validations on both Master sheets.
        for sheet in (BS_SHEET, PL_SHEET):
            dvs = wb[sheet].data_validations.dataValidation
            cols = {str(dv.sqref).split(":")[0][:1] for dv in dvs}
            # D/E/F (level_1/level_2/level_3) — shifted one left with no Entity col.
            self.assertTrue({"D", "E", "F"}.issubset(cols))
            self.assertTrue(all(dv.type == "list" for dv in dvs))

    def test_pl_rows_route_to_pl_sheet(self):
        rows = [
            coa_template.CoaRow(entity="E", account="100", level_0="BS",
                                level_1="Assets", level_2="Fixed", level_3="Intang"),
            coa_template.CoaRow(entity="E", account="800", level_0="PL",
                                level_1="Revenue", level_2="Sales", level_3="Dom"),
        ]
        wb = coa_template.build_coa_template_workbook(rows, style=False)
        self.assertEqual(self._data_account_count(wb[BS_SHEET]), 1)  # 1 BS
        self.assertEqual(self._data_account_count(wb[PL_SHEET]), 1)  # 1 PL

    @staticmethod
    def _data_account_count(ws) -> int:
        """Count data rows (non-empty Account col) below the header row.

        No Entity column in new templates: Account is the first column (col A, index 0).
        """
        count = 0
        for row in ws.iter_rows(min_row=2, max_col=1, values_only=True):
            if row[0] not in (None, ""):  # Account is first col (no Entity)
                count += 1
        return count


class TestRoundTripIntoImporter(unittest.TestCase):
    """The produced workbook must parse through read_bs_pl_master unchanged."""

    def _build_filled_workbook(self):
        rows = [
            coa_template.CoaRow(
                entity="Acme GmbH", account="10000",
                account_name="Intangible", level_0="BS",
                level_1="Assets", level_2="Fixed assets",
                level_3="Intangible assets",
            ),
            coa_template.CoaRow(
                entity="Acme GmbH", account="80000",
                account_name="Revenue", level_0="PL",
                level_1="Revenue", level_2="Sales", level_3="Domestic",
            ),
        ]
        return coa_template.build_coa_template_workbook(rows)

    def test_read_bs_pl_master_parses_template(self):
        wb = self._build_filled_workbook()
        buf = _save(wb)
        with Path(self._tmp("rt.xlsx")) as path:
            path.write_bytes(buf.getvalue())
            df = read_bs_pl_master(path)
        # Both rows survived; ID + level columns present.
        self.assertEqual(len(df), 2)
        # New templates have no Entity column — prefix injected externally at commit.
        self.assertNotIn("Entity", df.columns)
        for col in ("Account", "Account description", "L1", "L2", "L3", "L4"):
            self.assertIn(col, df.columns)
        self.assertEqual(set(df["L1"]), {"BS", "PL"})

    def test_profile_maps_template_columns(self):
        """bs_pl_master_profile over the template columns yields the canonical fields."""
        wb = self._build_filled_workbook()
        buf = _save(wb)
        path = Path(self._tmp("rt2.xlsx"))
        path.write_bytes(buf.getvalue())
        df = read_bs_pl_master(path)

        level_cols = detect_level_columns(list(df.columns))
        self.assertEqual(level_cols[:4], ["L1", "L2", "L3", "L4"])
        prof = bs_pl_master_profile(list(df.columns))
        # The required hierarchy fields the importer needs are all mapped.
        for field in ("account_number", "account_name", "level_0", "level_1", "level_2", "level_3"):
            self.assertIn(field, prof.columns)
        self.assertEqual(prof.columns["account_number"], "Account")
        self.assertEqual(prof.columns["level_0"], "L1")

    def test_library_prefill_round_trips(self):
        lib = {
            "version": "t",
            "entries": [
                {"gl_account_id": "10000", "account_name": "Intang", "level_0": "BS",
                 "level_1": "Assets", "level_2": "Fixed", "level_3": "Intang",
                 "level_4": None, "l4_sub": None},
                {"gl_account_id": "80000", "account_name": "Rev", "level_0": "PL",
                 "level_1": "Revenue", "level_2": "Sales", "level_3": "Dom",
                 "level_4": None, "l4_sub": None},
            ],
        }
        rows = coa_template.rows_from_library(lib, "Acme GmbH")
        self.assertEqual(len(rows), 2)
        wb = coa_template.build_coa_template_workbook(rows)
        path = Path(self._tmp("lib.xlsx"))
        wb.save(path)
        df = read_bs_pl_master(path)
        self.assertEqual(len(df), 2)
        # No Entity column in new templates: prefix is supplied externally at commit.
        self.assertNotIn("Entity", df.columns)
        self.assertEqual(set(df["Account"]), {"10000", "80000"})
        self.assertEqual(set(df["L1"]), {"BS", "PL"})

    # -- tmp dir helper --------------------------------------------------- #
    def setUp(self):
        import tempfile

        self._td = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._td.cleanup()

    def _tmp(self, name: str) -> str:
        return str(Path(self._td.name) / name)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeSession:
    """Minimal session stub: returns canned rows for the dim_gl_account SELECT."""

    def __init__(self, rows):
        self._rows = rows
        self.last_sql = None
        self.last_params = None

    def execute(self, sql, params=None):
        self.last_sql = str(sql)
        self.last_params = params
        return _FakeResult(self._rows)


class TestDimGlAccountPrefill(unittest.TestCase):
    def test_prefill_returns_rows_and_is_read_only(self):
        # (entity_name, legal_entity_code, gl_account_id, account_name,
        #  level_0, level_1, level_2, level_3, level_4, l4_sub)
        canned = [
            ("Acme GmbH", "ACME", "10000", "Intangible", "BS",
             "Assets", "Fixed assets", "Intangible assets", None, None),
            ("Acme GmbH", "ACME", "80000", "Revenue", "PL",
             "Revenue", "Sales", "Domestic", None, None),
        ]
        sess = _FakeSession(canned)
        rows = coa_template.rows_from_dim_gl_account(sess, "01", 2024)
        self.assertEqual(len(rows), 2)
        self.assertTrue(any(r.is_pl() for r in rows))
        self.assertEqual(rows[0].entity, "Acme GmbH")
        # Read-only: the only statement issued is a SELECT.
        self.assertIn("SELECT", sess.last_sql.upper())
        self.assertNotIn("INSERT", sess.last_sql.upper())
        self.assertNotIn("UPDATE", sess.last_sql.upper())
        self.assertEqual(sess.last_params.get("fy"), 2024)
        self.assertEqual(sess.last_params.get("pfx"), "01")

    def test_prefill_all_entities_omits_prefix_param(self):
        sess = _FakeSession([])
        rows = coa_template.rows_from_dim_gl_account(sess, None, 2023)
        self.assertEqual(rows, [])
        self.assertNotIn("pfx", sess.last_params)
        self.assertEqual(sess.last_params.get("fy"), 2023)


class TestWorkbookSchema(unittest.TestCase):
    """New entity-less header contract and statement-filter behaviour."""

    def test_bs_headers_contain_no_entity(self):
        """BS_HEADERS must not include 'Entity' — prefix supplied externally."""
        self.assertNotIn("Entity", coa_template.BS_HEADERS)
        self.assertEqual(coa_template.BS_HEADERS[0], "Account")

    def test_pl_headers_contain_no_entity_and_use_l1_bs_pl(self):
        """PL_HEADERS must not include 'Entity'; first level col is 'L1 - BS/PL'."""
        self.assertNotIn("Entity", coa_template.PL_HEADERS)
        self.assertEqual(coa_template.PL_HEADERS[0], "Account")
        self.assertIn("L1 - BS/PL", coa_template.PL_HEADERS)
        # Plain "L1" must NOT appear — importer renames the PL col on read.
        self.assertNotIn("L1", coa_template.PL_HEADERS)

    def test_statement_bs_yields_only_master_bs(self):
        """build_coa_template_workbook(statement='bs') emits only Master_BS."""
        rows = [
            coa_template.CoaRow(account="100", level_0="BS",
                                level_1="Assets", level_2="Fixed", level_3="Intang"),
            coa_template.CoaRow(account="800", level_0="PL",
                                level_1="Revenue", level_2="Sales", level_3="Dom"),
        ]
        wb = coa_template.build_coa_template_workbook(rows, style=False, statement="bs")
        self.assertIn(BS_SHEET, wb.sheetnames)
        self.assertNotIn(PL_SHEET, wb.sheetnames)

    def test_statement_pl_yields_only_master_pl(self):
        """build_coa_template_workbook(statement='pl') emits only Master_PL."""
        rows = [
            coa_template.CoaRow(account="100", level_0="BS",
                                level_1="Assets", level_2="Fixed", level_3="Intang"),
            coa_template.CoaRow(account="800", level_0="PL",
                                level_1="Revenue", level_2="Sales", level_3="Dom"),
        ]
        wb = coa_template.build_coa_template_workbook(rows, style=False, statement="pl")
        self.assertIn(PL_SHEET, wb.sheetnames)
        self.assertNotIn(BS_SHEET, wb.sheetnames)

    def test_statement_none_yields_both_sheets(self):
        """build_coa_template_workbook(statement=None) emits both sheets."""
        rows = coa_template.empty_template_rows("X")
        wb = coa_template.build_coa_template_workbook(rows, style=False, statement=None)
        self.assertIn(BS_SHEET, wb.sheetnames)
        self.assertIn(PL_SHEET, wb.sheetnames)


if __name__ == "__main__":
    unittest.main()
