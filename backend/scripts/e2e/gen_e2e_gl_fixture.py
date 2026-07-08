#!/usr/bin/env python3
"""Deterministic synthetic fixture for the v5 Project-Setup -> Reporting E2E smoke.

Emits TWO files a fresh project needs, and exposes the dataset spec as importable
constants so the driver (`run_pipeline_e2e.py`) and the assertions
(`assert_pipeline_e2e.py`) share ONE source of truth:

  1. A GL workbook  (CSV)   -> `gl_e2e.csv`
        columns: Tx, Account, Amount, PostingDate, SourceType, SourceNo
        Balanced double-entry bookings across two fiscal years (2024, 2025),
        one legal entity (prefix "01"). Same layout the ingest tests use
        (`backend/tests/test_ingest_validate_stage.py`).
  2. A CoA mapping workbook (XLSX, `bs_pl_master` format) -> `coa_e2e.xlsx`
        sheets Master_BS + Master_PL, columns
        [Account, Account description, L1(/"L1 - BS/PL"), L2, L3, L4].
        Maps every GL account to a level hierarchy. MOST accounts sit under
        ordinary paths; a FEW sit under DELIBERATELY NOVEL level_2 grains that
        exist in no seed structure, so `structure/unknown-positions` detects them
        and `structure/extend` has something to place.

The dataset is dataset-agnostic on purpose: the assertions only *hard-check* the
novel positions (guaranteed unknown because their grain uses brand-new level_2
labels), so the harness proves the auto-extension path regardless of whatever
chart the seed structure was built from.

Excel->DB level shift (see etl/bs_pl_master.py + mapping_account.py):
    Excel L1 -> DB level_0 (BS/PL marker)
    Excel L2 -> DB level_1
    Excel L3 -> DB level_2      <-- this is the "position" grain key
    Excel L4 -> DB level_3
    Excel L5 -> DB level_4 (optional; unused here)

SYNTHETIC ONLY. Never point this at, or replace its output with, real client data.

Usage:
    cd backend
    python scripts/e2e/gen_e2e_gl_fixture.py --outdir scripts/e2e/_fixture
"""
from __future__ import annotations

import argparse
from pathlib import Path

# --------------------------------------------------------------------------- #
# Dataset spec -- the single source of truth shared with the driver/asserter.
# --------------------------------------------------------------------------- #
ENTITY_PREFIX = "01"
FISCAL_YEARS = [2024, 2025]

# Per-year GL files (wizard path: upload each year -> /gl/combine).
GL_YEAR_FILES = {year: f"gl_e2e_{year}.csv" for year in FISCAL_YEARS}
COA_XLSX_NAME = "coa_e2e.xlsx"

# The GL mapping profile the driver POSTs to /validate and /commit.
# Matches the CSV columns emitted below.  The wizard combines the per-year files
# via /gl/combine, which injects a "fiscal_year" column; the profile therefore
# reads the fiscal year from that COLUMN (mode="column", value="fiscal_year"),
# exactly as GlEntityCard.buildEntityProfile does.  Signed amount, German
# day-first dates (proven shape: test_ingest_validate_stage / test_ingest_coa).
_FISCAL_YEAR_COL = "fiscal_year"  # matches etl combine _FISCAL_YEAR_COL

GL_PROFILE: dict = {
    "entity": {"mode": "fixed", "value": ENTITY_PREFIX},
    "fiscal_year": {"mode": "column", "value": _FISCAL_YEAR_COL},
    "sign": {"mode": "signed", "amount": "Amount"},
    "decimal": ".",
    "thousands": ",",
    "date_dayfirst": True,
    "columns": {
        "journal_entry_number": "Tx",
        "account_number": "Account",
        "posting_date": "PostingDate",
        "source_type": "SourceType",
        "source_no": "SourceNo",
    },
    "linking_strategy": "txn",
    "entry_type": "actual",
    "source_system": "e2e_smoke",
}

# --------------------------------------------------------------------------- #
# Chart of accounts.  Each entry:
#   (account, description, statement, xl_L2, xl_L3, xl_L4, novel?, bs_section)
# where xl_L3 (-> DB level_2) is the position grain key that detection groups on.
# bs_section is only meaningful for BS placements ("asset" | "credit").
# --------------------------------------------------------------------------- #
# KNOWN accounts -- ordinary paths (classify in a typical seed; NOT hard-asserted).
KNOWN_ACCOUNTS = [
    # account, description,        stmt, L2,           L3,                        L4,               section
    ("10000", "Bank",             "BS", "Assets",      "Cash & cash equivalents", "Bank",           "asset"),
    ("12000", "Trade receivables","BS", "Assets",      "Trade receivables",       "Trade AR",       "asset"),
    ("44000", "Trade payables",   "BS", "Liabilities", "Trade payables",          "Trade AP",       "credit"),
    ("80000", "Net sales",        "PL", "Income",      "Net sales",               "Domestic sales", None),
    ("70000", "Raw materials",    "PL", "Expenses",    "Cost of materials",       "Raw materials",  None),
    ("62000", "Wages",            "PL", "Expenses",    "Personnel expenses",      "Wages",          None),
]

# NOVEL accounts -- brand-new level_2 (Excel L3) grains that appear in NO seed
# structure, so they classify nowhere and MUST surface as unknown positions.
NOVEL_ACCOUNTS = [
    # account, description,        stmt, L2 (novel),      L3 (grain=DB level_2),   L4,                 section
    ("90001", "R&D grants",       "PL", "Research",       "Research grants",        "R&D tax credits", None),
    ("90002", "Crypto gains",     "PL", "Digital assets", "Crypto trading gains",   "",                None),
    ("90003", "Crypto holdings",  "BS", "Digital assets", "Crypto holdings",        "Wallet balances", "asset"),
]

ALL_ACCOUNTS = KNOWN_ACCOUNTS + NOVEL_ACCOUNTS

# The novel positions the harness asserts on. `level_2`/`level_3`/`level_4` here
# are the DB-grain values (= Excel L3/L4/L5) that appear in the unknown-positions
# response and the placement/reporting lines.
NOVEL_POSITIONS = [
    {
        "statement": stmt,
        "level_2": l3,      # DB level_2  (grain key)
        "level_3": l4 or None,  # DB level_3
        "level_4": None,
        "section": section,
        "account": acct,
    }
    for (acct, _desc, stmt, _l2, l3, l4, section) in NOVEL_ACCOUNTS
]

# Convenience: the set of novel grain labels the asserter looks for in responses.
NOVEL_LEVEL2_LABELS = sorted({p["level_2"] for p in NOVEL_POSITIONS})

# --------------------------------------------------------------------------- #
# Bookings -- balanced double-entry pairs.  (debit_account, credit_account, base)
# Every pair sums to zero, so booking/monthly/ledger balance checks pass cleanly.
# Amounts scale per fiscal year by index so 2024 != 2025 (still deterministic).
# --------------------------------------------------------------------------- #
_BOOKINGS = [
    # debit,   credit,  base amount, posting month
    ("12000", "80000", 20000, 3),   # sale: Dr receivables / Cr net sales
    ("70000", "44000",  9000, 4),   # purchase: Dr materials / Cr payables
    ("62000", "10000",  6000, 5),   # payroll: Dr wages / Cr cash
    ("90001", "10000",  5000, 6),   # NOVEL PL expense: Dr R&D grants / Cr cash
    ("10000", "90002",  8000, 7),   # NOVEL PL income: Dr cash / Cr crypto gains
    ("90003", "10000", 12000, 8),   # NOVEL BS asset: Dr crypto holdings / Cr cash
]


def _amount(base: int, year_index: int) -> float:
    """Deterministic per-year scaling: +10% in the second year."""
    return round(base * (1.0 + 0.10 * year_index), 2)


_GL_HEADER = ["Tx", "Account", "Amount", "PostingDate", "SourceType", "SourceNo"]


def build_gl_rows(year: int, year_index: int) -> list[dict[str, str]]:
    """Deterministic list of GL line dicts for ONE fiscal year (order-stable)."""
    rows: list[dict[str, str]] = []
    for bk, (dr, cr, base, month) in enumerate(_BOOKINGS, start=1):
        tx = f"{year}{bk:02d}"           # unique per booking, per year
        date = f"15.{month:02d}.{year}"  # dd.mm.yyyy (day-first)
        amt = _amount(base, year_index)
        rows.append({"Tx": tx, "Account": dr, "Amount": f"{amt:.2f}",
                     "PostingDate": date, "SourceType": "", "SourceNo": ""})
        rows.append({"Tx": tx, "Account": cr, "Amount": f"{-amt:.2f}",
                     "PostingDate": date, "SourceType": "", "SourceNo": ""})
    return rows


def write_gl_year_csv(path: Path, year: int, year_index: int) -> Path:
    lines = [",".join(_GL_HEADER)]
    for r in build_gl_rows(year, year_index):
        lines.append(",".join(r[h] for h in _GL_HEADER))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_all_gl(outdir: Path) -> list[tuple[int, Path]]:
    """Write one CSV per fiscal year; return [(year, path), ...] in FY order."""
    out: list[tuple[int, Path]] = []
    for yi, year in enumerate(FISCAL_YEARS):
        p = write_gl_year_csv(outdir / GL_YEAR_FILES[year], year, yi)
        out.append((year, p))
    return out


def write_coa_xlsx(path: Path) -> Path:
    from openpyxl import Workbook

    wb = Workbook()
    bs = wb.active
    bs.title = "Master_BS"
    bs.append(["Account", "Account description", "L1", "L2", "L3", "L4"])
    pl = wb.create_sheet("Master_PL")
    pl.append(["Account", "Account description", "L1 - BS/PL", "L2", "L3", "L4"])

    for (acct, desc, stmt, l2, l3, l4, _section) in ALL_ACCOUNTS:
        row = [acct, desc, stmt, l2, l3, l4]
        if stmt == "BS":
            bs.append(row)
        else:
            pl.append(row)

    wb.save(path)
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the E2E pipeline fixture.")
    ap.add_argument(
        "--outdir",
        default=str(Path(__file__).resolve().parent / "_fixture"),
        help="output directory (default: scripts/e2e/_fixture)",
    )
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    gl_files = write_all_gl(outdir)
    coa = write_coa_xlsx(outdir / COA_XLSX_NAME)

    for year, p in gl_files:
        n = len(build_gl_rows(year, FISCAL_YEARS.index(year)))
        print(f"Wrote GL {year} -> {p}  ({n} lines, {n // 2} balanced bookings)")
    print(f"Wrote CoA  -> {coa}  (Master_BS + Master_PL, "
          f"{len(ALL_ACCOUNTS)} accounts; {len(NOVEL_ACCOUNTS)} novel)")
    print(f"Entity prefix: {ENTITY_PREFIX}   Fiscal years: {FISCAL_YEARS}")
    print(f"Novel level_2 grains (must show as unknown positions): {NOVEL_LEVEL2_LABELS}")
    print("SYNTHETIC DATA - safe for tests. Never replace with real client data.")


if __name__ == "__main__":
    main()
