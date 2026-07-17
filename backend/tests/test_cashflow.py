"""Cashflow period helpers and row structure tests."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
BACKEND = ROOT / "backend"
for p in (ROOT, SCRIPTS, BACKEND):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from cashflow import (  # noqa: E402
    EBITDA_ADJ_LABEL,
    EBITDA_ADJUSTED_LABEL,
    EBITDA_REPORTED_LABEL,
    FIXED_ROWS_AFTER_EBITDA_BRIDGE,
    FIXED_ROWS_BEFORE_WC,
    bucket_l3_level_refs,
    build_financing_rows,
    delta_sumifs_for_period,
    display_label,
    financing_nd_row_refs,
    normalize_cf_na_bucket,
)
from databook_periods import (  # noqa: E402
    CashflowDeltaSpec,
    cashflow_delta_spec,
    cashflow_display_periods,
    display_reporting_columns_from_headers,
    ordered_reporting_columns_from_headers,
    prior_year_month_column,
    ytd_month_columns_for_period,
)
from report_row_layout import (  # noqa: E402
    CF_BUCKET_ORDER,
    CF_BUCKET_TOTAL_LABELS,
    CF_FINANCING_NA_BUCKETS,
    build_cf_na_bucket_row_structure,
    is_cf_excluded_l3,
    l3_order_from_na_mapping,
)


def _cf_bs_fixture() -> pd.DataFrame:
    rows = {
        "Entity": ["E1"] * 12,
        "L2": ["CA"] * 10 + ["Equity", "Equity"],
        "L3": [
            "Inventories",
            "Inventories",
            "Receivables",
            "Trade payables",
            "Other assets",
            "Cash & cash equivalents",
            "Tangible assets",
            "Tangible assets",
            "Financial liabilities",
            "Financial liabilities",
            "Subscribed capital",
            "Retained earnings",
        ],
        "L4": [
            "Finished goods",
            "Raw materials",
            "Trade receivables",
            "Trade payables",
            "Prepayments",
            "Bank liabilities",
            "PPE",
            "Other FA",
            "Bank overdraft",
            "Shareholder loan",
            "Subscribed capital",
            "Retained earnings",
        ],
        "NA": ["TWC", "TWC", "TWC", "TWC", "OWC", "DL", "FA", "FA", "DL", "ND", "Equity", "Equity"],
        "L6": ["Reported"] * 12,
        "FY22A": [100, 50, 200, -80, 30, -100, 500, 100, -40, -60, -200, -300],
        "FY23A": [110, 40, 180, -90, 25, -120, 520, 110, -45, -65, -210, -320],
        "FY24A": [120, 30, 220, -70, 20, -130, 540, 120, -50, -70, -220, -340],
        "YTD25A": [125, 28, 210, -75, 18, -125, 545, 125, -55, -75, -225, -345],
    }
    for y in (22, 23, 24, 25):
        for m, abbr in enumerate(
            ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul"], start=1
        ):
            col = f"{abbr}{str(y)[-2:]}A"
            rows[col] = [10 + m] * 12
    return pd.DataFrame(rows)


def _na_l3_order() -> dict[str, list[str]]:
    return {
        "TWC": ["Inventories", "Receivables", "Trade payables"],
        "OWC": ["Other assets"],
        "Other": [],
        "DL": ["Financial liabilities", "Cash & cash equivalents"],
        "ND": ["Financial liabilities"],
        "Equity": ["Subscribed capital", "Retained earnings"],
    }


class TestCashflowPeriods(unittest.TestCase):
    def test_display_periods_skip_first(self):
        all_p = ["FY22A", "FY23A", "FY24A", "YTD25A"]
        display, priors = cashflow_display_periods(all_p)
        self.assertEqual(display, ["FY23A", "FY24A", "YTD25A"])
        self.assertEqual(priors, ["FY22A", "FY23A", "FY24A"])

    def test_display_periods_too_short(self):
        display, priors = cashflow_display_periods(["FY22A"])
        self.assertEqual(display, [])
        self.assertEqual(priors, [])

    def test_display_reporting_columns_hides_prior_ytd(self):
        headers = [
            "Entity",
            "FY22A",
            "FY23A",
            "FY24A",
            "YTD24A",
            "YTD25A",
            "Jan25A",
        ]
        self.assertEqual(
            ordered_reporting_columns_from_headers(headers),
            ["FY22A", "FY23A", "FY24A", "YTD24A", "YTD25A"],
        )
        self.assertEqual(
            display_reporting_columns_from_headers(headers),
            ["FY22A", "FY23A", "FY24A", "YTD25A"],
        )

    def test_prior_year_month_column(self):
        self.assertEqual(prior_year_month_column("Jul25A"), "Jul24A")

    def test_ytd_month_columns(self):
        cols = list(_cf_bs_fixture().columns)
        months = ytd_month_columns_for_period("YTD25A", cols)
        self.assertIn("Jan25A", months)
        self.assertIn("Jul25A", months)
        self.assertNotIn("Aug25A", months)

    def test_cashflow_delta_spec_ytd(self):
        cols = list(_cf_bs_fixture().columns) + ["YTD24A"]
        spec = cashflow_delta_spec("YTD25A", "FY24A", cols)
        self.assertEqual(spec.kind, "ytd")
        self.assertEqual(spec.curr_col, "YTD25A")
        self.assertEqual(spec.prior_col, "YTD24A")
        self.assertEqual(spec.curr_months, ())

    def test_cashflow_delta_spec_ytd_month_fallback(self):
        cols = list(_cf_bs_fixture().columns)
        spec = cashflow_delta_spec("YTD25A", "FY24A", cols)
        self.assertEqual(spec.kind, "ytd")
        self.assertIn("Jul25A", spec.curr_months)
        self.assertIn("Jul24A", spec.prior_months)

    def test_cashflow_delta_spec_fy(self):
        cols = list(_cf_bs_fixture().columns)
        spec = cashflow_delta_spec("FY24A", "FY23A", cols)
        self.assertEqual(spec.kind, "fy")
        self.assertEqual(spec.curr_col, "FY24A")
        self.assertEqual(spec.prior_col, "FY23A")


class TestCashflowRowStructure(unittest.TestCase):
    def test_ebitda_row_order(self):
        labels = [r["label"] for r in FIXED_ROWS_BEFORE_WC] + [
            r["label"] for r in FIXED_ROWS_AFTER_EBITDA_BRIDGE
        ]
        self.assertEqual(labels[0], EBITDA_REPORTED_LABEL)
        self.assertEqual(labels[1], EBITDA_ADJ_LABEL)
        self.assertEqual(labels[2], EBITDA_ADJUSTED_LABEL)

    def test_l3_order_from_na_mapping(self):
        map_df = pd.DataFrame(
            {
                "NA": ["TWC", "TWC", "OWC"],
                "L3": ["Inventories", "Receivables", "Other assets"],
                "sort_order": [2, 1, 1],
            }
        )
        self.assertEqual(
            l3_order_from_na_mapping(map_df, "TWC"),
            ["Receivables", "Inventories"],
        )

    def test_is_cf_excluded_l3_exact_match_only(self):
        self.assertTrue(is_cf_excluded_l3("Cash & cash equivalents"))
        self.assertFalse(is_cf_excluded_l3("Financial liabilities"))
        self.assertFalse(is_cf_excluded_l3("Cash"))

    def test_cf_operating_bucket_structure(self):
        struct = build_cf_na_bucket_row_structure(
            _cf_bs_fixture(),
            [
                "Inventories",
                "Receivables",
                "Trade payables",
                "Other assets",
                "Financial liabilities",
                "Cash & cash equivalents",
            ],
            {"l4_sort_basis": "latest_fy"},
            source_col="L6",
            bucket_col="NA",
            bucket_order=CF_BUCKET_ORDER,
            bucket_total_labels=CF_BUCKET_TOTAL_LABELS,
            normalize_bucket_fn=normalize_cf_na_bucket,
            na_l3_order=_na_l3_order(),
        )
        types = [r["type"] for r in struct]
        self.assertIn("detail", types)
        self.assertIn("subtotal_l3", types)
        self.assertIn("total_na", types)
        totals = [r["label"] for r in struct if r["type"] == "total_na"]
        self.assertIn("Δ Trade working capital", totals)
        self.assertIn("Δ Other working capital", totals)
        self.assertIn("Δ Debt-like items", totals)
        self.assertIn("Δ Other", totals)
        labels = [r.get("label", "") for r in struct]
        self.assertFalse(any("Cash & cash equivalents" in lbl for lbl in labels))
        dl_rows = [r for r in struct if r.get("NA") == "DL"]
        self.assertTrue(dl_rows)
        nd_rows = [r for r in struct if r.get("NA") == "ND"]
        self.assertFalse(nd_rows)

    def test_cf_dl_includes_l3_not_in_na_template(self):
        df = _cf_bs_fixture()
        df = pd.concat(
            [
                df,
                pd.DataFrame(
                    {
                        "Entity": ["E1"],
                        "L2": ["CL"],
                        "L3": ["Other liabilities"],
                        "L4": ["Bonus liabilities"],
                        "NA": ["DL"],
                        "L6": ["Reported"],
                        "FY22A": [-5.0],
                        "FY23A": [-6.0],
                        "FY24A": [-7.0],
                        "YTD25A": [-8.0],
                    }
                ),
            ],
            ignore_index=True,
        )
        struct = build_cf_na_bucket_row_structure(
            df,
            [
                "Inventories",
                "Receivables",
                "Trade payables",
                "Other assets",
                "Financial liabilities",
                "Other liabilities",
                "Cash & cash equivalents",
            ],
            {"l4_sort_basis": "latest_fy"},
            source_col="L6",
            bucket_col="NA",
            bucket_order=CF_BUCKET_ORDER,
            bucket_total_labels=CF_BUCKET_TOTAL_LABELS,
            normalize_bucket_fn=normalize_cf_na_bucket,
            na_l3_order=_na_l3_order(),
        )
        dl_labels = [r.get("label", "") for r in struct if r.get("NA") == "DL" and r["type"] == "detail"]
        self.assertIn("Bonus liabilities", dl_labels)

    def test_cf_financing_nd_structure_without_bucket_total(self):
        struct = build_cf_na_bucket_row_structure(
            _cf_bs_fixture(),
            ["Financial liabilities", "Cash & cash equivalents"],
            {"l4_sort_basis": "latest_fy"},
            source_col="L6",
            bucket_col="NA",
            bucket_order=CF_FINANCING_NA_BUCKETS,
            bucket_total_labels={},
            normalize_bucket_fn=normalize_cf_na_bucket,
            na_l3_order=_na_l3_order(),
            include_bucket_total=False,
        )
        self.assertFalse(any(r["type"] == "total_na" for r in struct))
        nd_rows = [r for r in struct if r.get("NA") == "ND"]
        self.assertTrue(nd_rows)
        labels = [r.get("label", "") for r in struct]
        self.assertFalse(any("Cash & cash equivalents" in lbl for lbl in labels))

    def test_cf_financing_equity_structure(self):
        struct = build_cf_na_bucket_row_structure(
            _cf_bs_fixture(),
            ["Financial liabilities", "Subscribed capital", "Retained earnings"],
            {"l4_sort_basis": "latest_fy"},
            source_col="L6",
            bucket_col="NA",
            bucket_order=CF_FINANCING_NA_BUCKETS,
            bucket_total_labels={},
            normalize_bucket_fn=normalize_cf_na_bucket,
            na_l3_order=_na_l3_order(),
            include_bucket_total=False,
        )
        equity_rows = [r for r in struct if r.get("NA") == "Equity"]
        self.assertTrue(equity_rows)
        self.assertEqual(
            [r["type"] for r in equity_rows],
            ["detail", "detail", "subtotal_l3"],
        )
        self.assertEqual(equity_rows[-1]["label"], "Equity")
        self.assertEqual(display_label(equity_rows[-1]), "Δ Equity")
        self.assertEqual(equity_rows[0]["L3"], "Subscribed capital")
        self.assertEqual(equity_rows[0]["L4"], "Subscribed capital")
        self.assertEqual(equity_rows[0]["L2"], "Equity")

    def test_build_financing_rows_includes_equity(self):
        df_bs = _cf_bs_fixture()
        df_pl = pd.DataFrame(
            {
                "L3": ["Financial result"],
                "L4": ["Interest expense"],
                "L6": ["Reported"],
                "FY24A": [-10.0],
            }
        )
        pl_map = pd.DataFrame({"L3": ["Financial result"], "L4": ["Interest expense"]})
        rows = build_financing_rows(
            pl_map,
            df_pl,
            df_bs,
            mapping_l3_order=["Financial liabilities"],
            source_col="L6",
            na_col="NA",
            na_l3_order=_na_l3_order(),
        )
        labels = [r.get("label", "") for r in rows]
        self.assertIn("Equity", labels)
        fin_row = next(r for r in rows if r["type"] == "financial_result")
        self.assertEqual(fin_row["label"], "Cash flow from financing activities")

    def test_build_financing_rows_order(self):
        df_bs = _cf_bs_fixture()
        df_pl = pd.DataFrame(
            {
                "L3": ["Financial result", "Financial result"],
                "L4": ["Interest expense", "Interest income"],
                "L6": ["Reported", "Reported"],
                "FY24A": [-10.0, 2.0],
            }
        )
        pl_map = pd.DataFrame(
            {
                "L3": ["Financial result", "Financial result"],
                "L4": ["Interest expense", "Interest income"],
            }
        )
        rows = build_financing_rows(
            pl_map,
            df_pl,
            df_bs,
            mapping_l3_order=["Financial liabilities"],
            source_col="L6",
            na_col="NA",
            na_l3_order=_na_l3_order(),
        )
        types = [r["type"] for r in rows]
        fr_idx = types.index("fr_detail")
        subtotal_idx = types.index("subtotal_fr")
        fin_idx = types.index("financial_result")
        self.assertLess(fr_idx, subtotal_idx)
        self.assertLess(subtotal_idx, fin_idx)
        self.assertNotIn("total_na", types)
        self.assertIn("detail", types[subtotal_idx:fin_idx])

    def test_display_label_delta_on_subtotal(self):
        self.assertEqual(
            display_label({"type": "subtotal_l3", "label": "Inventories"}),
            "Δ Inventories",
        )

    def test_normalize_cf_na_bucket(self):
        self.assertEqual(normalize_cf_na_bucket("ND"), "ND")
        self.assertEqual(normalize_cf_na_bucket("DL"), "DL")
        self.assertEqual(normalize_cf_na_bucket("FA"), "FA")
        self.assertEqual(normalize_cf_na_bucket("TWC"), "TWC")
        self.assertEqual(normalize_cf_na_bucket("Equity"), "Equity")
        self.assertEqual(normalize_cf_na_bucket("Equity & liabilities"), "Equity")

    def test_bucket_l3_level_refs_excludes_l4_details(self):
        struct = build_cf_na_bucket_row_structure(
            _cf_bs_fixture(),
            [
                "Inventories",
                "Receivables",
                "Trade payables",
                "Other assets",
                "Financial liabilities",
            ],
            {"l4_sort_basis": "latest_fy"},
            source_col="L6",
            bucket_col="NA",
            bucket_order=CF_BUCKET_ORDER,
            bucket_total_labels=CF_BUCKET_TOTAL_LABELS,
            normalize_bucket_fn=normalize_cf_na_bucket,
            na_l3_order=_na_l3_order(),
        )
        for i, row in enumerate(struct):
            row["_excel_row"] = 10 + i
        twc_refs = bucket_l3_level_refs(struct, "TWC", "I")
        twc_rows = {int(ref[1:]) for ref in twc_refs}
        detail_rows = {
            row["_excel_row"]
            for row in struct
            if row.get("NA") == "TWC" and row["type"] == "detail"
        }
        self.assertTrue(twc_refs)
        self.assertTrue(detail_rows)
        self.assertTrue(detail_rows.isdisjoint(twc_rows))
        self.assertTrue(
            any(row["type"] == "subtotal_l3" and row["_excel_row"] in twc_rows for row in struct)
        )

    def test_financing_nd_row_refs(self):
        struct = build_cf_na_bucket_row_structure(
            _cf_bs_fixture(),
            ["Financial liabilities"],
            {"l4_sort_basis": "latest_fy"},
            source_col="L6",
            bucket_col="NA",
            bucket_order=CF_FINANCING_NA_BUCKETS,
            bucket_total_labels={},
            normalize_bucket_fn=normalize_cf_na_bucket,
            na_l3_order=_na_l3_order(),
            include_bucket_total=False,
        )
        for i, row in enumerate(struct):
            row["_excel_row"] = 20 + i
        refs = financing_nd_row_refs(struct, "H")
        ref_rows = {int(ref[1:]) for ref in refs}
        detail_rows = {
            row["_excel_row"] for row in struct if row["type"] == "detail"
        }
        self.assertTrue(refs)
        self.assertTrue(detail_rows.isdisjoint(ref_rows))


class TestCashflowDeltaFormula(unittest.TestCase):
    def test_delta_sumifs_shape_fy(self):
        from cashflow import delta_sumifs

        f = delta_sumifs(
            "Master_BS!$N$2:$N$10",
            "Master_BS!$M$2:$M$10",
            src_rng="Master_BS!$I$2:$I$10",
            rep_crit="$E$9",
            extra_criteria=[("Master_BS!$G$2:$G$10", "$G$9")],
        )
        self.assertIn("SUMIFS", f)
        self.assertIn("-", f)
        self.assertTrue(f.endswith("/1000"))

    def test_delta_sumifs_for_period_ytd(self):
        spec = CashflowDeltaSpec(
            kind="ytd",
            curr_months=("Jan25A", "Jul25A"),
            prior_months=("Jan24A", "Jul24A"),
        )
        period_rng = {
            "Jan25A": "Master_BS!$A$2:$A$10",
            "Jul25A": "Master_BS!$B$2:$B$10",
            "Jan24A": "Master_BS!$C$2:$C$10",
            "Jul24A": "Master_BS!$D$2:$D$10",
        }
        f = delta_sumifs_for_period(
            spec,
            period_rng,
            src_rng="Master_BS!$I$2:$I$10",
            rep_crit="$E$9",
            extra_criteria=[("Master_BS!$G$2:$G$10", "$G$9")],
        )
        self.assertIn("Master_BS!$A$2:$A$10", f)
        self.assertIn("Master_BS!$D$2:$D$10", f)
        self.assertTrue(f.endswith("/1000"))


class TestBsBucketNaRollup(unittest.TestCase):
    def test_na_bucket_rollup_includes_dl_for_nd(self):
        from report_row_layout import NA_BUCKET_ROLLUP

        self.assertEqual(NA_BUCKET_ROLLUP["ND"], ("ND", "DL"))


if __name__ == "__main__":
    unittest.main()
