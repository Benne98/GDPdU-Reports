"""Synthetic GL fixtures for ETL tests. NO real client data (CLAUDE.md rule).

A tiny, balanced double-entry ledger for entity prefix '01', FY 2024:

  Booking 1 — sale 1.190 to customer 100:
    receivable +1190 (cust 100) | revenue -1000 | VAT -190
  Booking 2 — purchase 595 from supplier 200:
    material +500 | input-VAT +95 | payable -595 (supp 200)
  Booking 3 — sale 2.380 to customer 100:
    receivable +2380 (cust 100) | revenue -2000 | VAT -380

Class totals: receivable 3570 · payable -595 · revenue -3000 (gross_sales 3000) · material 500
"""
from __future__ import annotations

import pandas as pd

from etl.derive import AccountClasses

ACCOUNT_CLASSES = AccountClasses(
    revenue=frozenset({"Net sales"}),
    material=frozenset({"Cost of materials"}),
    receivable=frozenset({"Trade receivables"}),
    payable=frozenset({"Trade payables"}),
)

# level_3 label per economic class (used to test classify + as mapping source)
_L3 = {
    "receivable": "Trade receivables",
    "payable": "Trade payables",
    "revenue": "Net sales",
    "material": "Cost of materials",
    "other": "VAT",
}

# (jen, line_no, class, amount, gl_account, customer, supplier)
_ROWS = [
    ("0000000001", 1, "receivable", 1190.0, "10000", "01100", None),
    ("0000000001", 2, "revenue", -1000.0, "80000", None, None),
    ("0000000001", 3, "other", -190.0, "17760", None, None),
    ("0000000002", 1, "material", 500.0, "30000", None, None),
    ("0000000002", 2, "other", 95.0, "15760", None, None),
    ("0000000002", 3, "payable", -595.0, "70000", None, "01200"),
    ("0000000003", 1, "receivable", 2380.0, "10000", "01100", None),
    ("0000000003", 2, "revenue", -2000.0, "80000", None, None),
    ("0000000003", 3, "other", -380.0, "17760", None, None),
]


def canonical_lines() -> pd.DataFrame:
    """Balanced canonical GL lines (one row per booking line)."""
    rows = []
    for i, (jen, ln, cls, amt, acct, cust, supp) in enumerate(_ROWS, start=1):
        rows.append(
            {
                "journal_entry_group_number": f"01{jen}",  # 2-digit prefix + 10-digit jen
                "fiscal_year": 2024,
                "line_number": ln,
                "booking_line_id": i,
                "account_number_group": "01" + acct.zfill(6),
                "gl_account_id": acct,
                "amount": amt,
                "account_class": cls,
                "level_3": _L3[cls],
                "customer_id": cust,
                "supplier_id": supp,
            }
        )
    return pd.DataFrame(rows)


def mapping_accounts() -> list[str]:
    """gl_account_id values present in the (synthetic) account mapping."""
    return sorted({r[4] for r in _ROWS})


# --------------------------------------------------------------------------- #
# DF1 — Account-mapping fixture (balanced with GL fixture)
# --------------------------------------------------------------------------- #
#
# One row per unique GL account in _ROWS.  Hierarchy mimics a minimal DATEV/SKR03
# structure (level_0 = BS/PL indicator, level_1..4 = structural labels).
# Accounts that carry NA/CF data exercise the optional-column path in
# apply_account_mapping and load_account_mapping.
#
# Columns in the synthetic source file (use these as profile.columns values):
#   Konto, Kontoname, Ebene0, Ebene1, Ebene2, Ebene3, Ebene4, Ebene4U,
#   Sort2, Sort3, IC, NA6, NA7, CF1, CF2, CF3, CF4, CF5, CFM

_ACCOUNT_ROWS = [
    # (acct, name, l0, l1, l2, l3, l4, l4sub, s2, s3, ic, na6, na7, cf1, cf2, cf3, cf4, cf5, cfm)
    (
        "10000", "Forderungen aus L+L", "BS", "Umlaufvermögen", "Forderungen",
        "Trade receivables", "Debitorische Konten", "AllDebtors",
        20, 201, False,
        "AR", "Trade AR",
        "Operating", "Working Capital", None, None, None, "AR",
    ),
    (
        "80000", "Umsatzerlöse", "PL", "Erträge", "Umsatzerlöse",
        "Net sales", "Inlandsumsatz", "DE",
        10, 101, False,
        "Revenue", "Domestic revenue",
        None, None, None, None, None, None,
    ),
    (
        "17760", "USt 19%", "BS", "Umlaufvermögen", "Verbindlichkeiten",
        "VAT", "Umsatzsteuer", "USt19",
        30, 301, False,
        None, None,
        None, None, None, None, None, None,
    ),
    (
        "30000", "Materialaufwand", "PL", "Aufwendungen", "Materialaufwand",
        "Cost of materials", "Rohstoffe", "Raw",
        40, 401, False,
        "COGS", "Direct material cost",
        "Operating", "Direct Costs", None, None, None, "COGS",
    ),
    (
        "15760", "Vorsteuer 19%", "BS", "Umlaufvermögen", "Forderungen",
        "VAT", "Vorsteuer", "VSt19",
        20, 202, False,
        None, None,
        None, None, None, None, None, None,
    ),
    (
        "70000", "Verbindlichkeiten aus L+L", "BS", "Verbindlichkeiten", "Kurzfristig",
        "Trade payables", "Kreditorische Konten", "AllCreditors",
        50, 501, False,
        "AP", "Trade AP",
        "Operating", "Working Capital", None, None, None, "AP",
    ),
]

# Canonical source-column name -> target-field mapping for the synthetic mapping file
ACCOUNT_MAPPING_COLUMN_MAP: dict[str, str] = {
    "account_number": "Konto",
    "account_name":   "Kontoname",
    "level_0":        "Ebene0",
    "level_1":        "Ebene1",
    "level_2":        "Ebene2",
    "level_3":        "Ebene3",
    "level_4":        "Ebene4",
    "l4_sub":         "Ebene4U",
    "level_2_sort":   "Sort2",
    "level_3_sort":   "Sort3",
    "is_ic":          "IC",
    "l6_na_mapping":  "NA6",
    "l7_na_description": "NA7",
    "cf_l1": "CF1",
    "cf_l2": "CF2",
    "cf_l3": "CF3",
    "cf_l4": "CF4",
    "cf_l5": "CF5",
    "cf_mapping": "CFM",
}


def raw_account_mapping() -> pd.DataFrame:
    """Synthetic source-file DataFrame for the account mapping (pre-profile-apply).

    Column names are the *source* names from ACCOUNT_MAPPING_COLUMN_MAP.
    This simulates what a user would upload from a CSV/XLSX.
    """
    rows = []
    for (
        acct, name, l0, l1, l2, l3, l4, l4sub,
        s2, s3, ic,
        na6, na7,
        cf1, cf2, cf3, cf4, cf5, cfm,
    ) in _ACCOUNT_ROWS:
        rows.append({
            "Konto":    acct,
            "Kontoname": name,
            "Ebene0":   l0,
            "Ebene1":   l1,
            "Ebene2":   l2,
            "Ebene3":   l3,
            "Ebene4":   l4,
            "Ebene4U":  l4sub,
            "Sort2":    s2,
            "Sort3":    s3,
            "IC":       ic,
            "NA6":      na6,
            "NA7":      na7,
            "CF1":      cf1,
            "CF2":      cf2,
            "CF3":      cf3,
            "CF4":      cf4,
            "CF5":      cf5,
            "CFM":      cfm,
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# DF4 — Plan-synthesis fixtures (per account_number_group × fiscal_period)
# --------------------------------------------------------------------------- #
#
# A tiny, deterministic GL movement set with an explicit MONTHLY spread so the
# seasonal index and growth math have known expected values.  Sign convention is
# preserved: revenue is credit (negative amount), material is debit (positive).
#
# Base FY 2024, revenue account 0180000 (gl 80000):
#   period 1: -1000  | period 2: -2000     -> annual -3000  (seasonal 1/3, 2/3)
# Base FY 2024, material account 0130000 (gl 30000):
#   period 1:  +200  | period 2:  +300      -> annual  +500  (seasonal 2/5, 3/5)
#
# Current FY 2025 (forecast input), revenue 0180000, closed periods 1..2:
#   period 1: -550   | period 2: -1100      -> YTD -1650 (run-rate doubles vs base p1/p2)

_PLAN_GL_ROWS = [
    # (account_number_group, fiscal_year, fiscal_period, amount, pl_group)
    ("0180000", 2024, 1, -1000.0, "Umsatzerlöse"),
    ("0180000", 2024, 2, -2000.0, "Umsatzerlöse"),
    ("0130000", 2024, 1, 200.0, "Materialaufwand"),
    ("0130000", 2024, 2, 300.0, "Materialaufwand"),
    # current-year actuals (closed periods 1..2 of FY2025) for the forecast path
    ("0180000", 2025, 1, -550.0, "Umsatzerlöse"),
    ("0180000", 2025, 2, -1100.0, "Umsatzerlöse"),
]


def plan_gl_actuals() -> pd.DataFrame:
    """GL actual movements per account_number_group × fiscal_period (for plan_synth)."""
    return pd.DataFrame(
        _PLAN_GL_ROWS,
        columns=["account_number_group", "fiscal_year", "fiscal_period", "amount", "pl_group"],
    )


_PLAN_SALES_ROWS = [
    # (customer_id, fiscal_year, fiscal_period, gross_sales)
    ("01100", 2024, 1, 1000.0),
    ("01100", 2024, 2, 2000.0),
    # current-year (FY2025) closed periods for the sales forecast path
    ("01100", 2025, 1, 550.0),
    ("01100", 2025, 2, 1100.0),
]


def plan_sales_actuals() -> pd.DataFrame:
    """Sales actuals (gross_sales, positive) per customer_id × fiscal_period."""
    return pd.DataFrame(
        _PLAN_SALES_ROWS,
        columns=["customer_id", "fiscal_year", "fiscal_period", "gross_sales"],
    )


def canonical_account_mapping(entity_prefix: str = "01", fiscal_year: int = 2024) -> pd.DataFrame:
    """Canonical account-mapping DataFrame as returned by apply_account_mapping.

    Use this in tests that need the post-transform view (e.g. to feed load_account_mapping).
    Consistent with canonical_lines(): same entity_prefix, same fiscal_year.
    """
    from etl.mapping_account import AccountMappingProfile, apply_account_mapping

    profile = AccountMappingProfile(
        entity={"mode": "fixed", "value": entity_prefix},
        fiscal_year={"mode": "fixed", "value": fiscal_year},
        columns=ACCOUNT_MAPPING_COLUMN_MAP,
        source_system="test",
    )
    return apply_account_mapping(raw_account_mapping(), profile)
