"""Export per-entity / per-FY GL entry CSVs for Project Setup (port 5178).

Source: GoBD ``Unmapped_GoBD`` CSV (or SQL table with the same columns).

Exclusions (per user spec):
  - Booking number 6  → opening balances (separate OB upload in wizard)
  - BS Net profit accounts (Account_Mapping L3 = 'Net profit')

Output layout::

    <out-dir>/GL data/<Entity>/<YYYY>/gl_entry.csv

Columns match the standard GoBD export so ``suggestGlMapping`` auto-fills in the wizard.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

_BACKEND = Path(__file__).resolve().parent.parent
_REPO = _BACKEND.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

DEFAULT_SOURCE = (
    _REPO / "etl" / "source_data" / "gobd_gl" / "Unmapped_GoBD_patched_account1.csv"
)
DEFAULT_MAPPING = Path(r"C:\Users\bened\OneDrive\Desktop\Finssentials - Setup\Account_Mapping.xlsx")
DEFAULT_OUT = Path(r"C:\Users\bened\OneDrive\Desktop\Finssentials - Setup")

ENTITIES = ["Atlas", "Calypto", "Meridian", "Novara", "Venturo"]
YEARS = [2022, 2023, 2024, 2025]

# GoBD columns kept in export (wizard-friendly).
EXPORT_COLUMNS = [
    "Entity",
    "Booking number",
    "Account number",
    "Posting date",
    "Document type",
    "Document number",
    "Amount",
    "VAT amount",
    "Posting type",
    "Transaction number",
    "Document date",
    "Source type",
    "Source No.",
    "Year",
    "Booking text",
]

_ENTITY_NO_TO_NAME = {
    "1": "Atlas",
    "2": "Meridian",
    "3": "Novara",
    "4": "Venturo",
    "5": "Calypto",
}


def _is_opening_booking(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().isin({"6", "6.0", "6,0"})


def _net_profit_accounts(mapping_xlsx: Path) -> dict[str, set[str]]:
    """Entity name → raw GoBD account numbers classified as BS Net profit."""
    if not mapping_xlsx.is_file():
        return {e: set() for e in ENTITIES}

    bs = pd.read_excel(mapping_xlsx, sheet_name="BS", header=0)
    np_rows = bs[bs["L3"].astype(str).str.strip().eq("Net profit")]
    # Mapping file uses accounts 20001..50001 → Entity No 2..5; Atlas → 10001 inferred.
    acct_to_entity_no: dict[str, str] = {}
    for _, row in np_rows.iterrows():
        acct = str(row["Account number"]).strip()
        if not acct or acct == "nan":
            continue
        m = re.match(r"^([1-5])0001$", acct)
        if m:
            acct_to_entity_no[acct] = m.group(1)
    acct_to_entity_no.setdefault("10001", "1")

    by_entity: dict[str, set[str]] = {e: set() for e in ENTITIES}
    for acct, eno in acct_to_entity_no.items():
        name = _ENTITY_NO_TO_NAME.get(eno)
        if name:
            by_entity[name].add(acct)
    return by_entity


def _filter_frame(df: pd.DataFrame, np_by_entity: dict[str, set[str]]) -> pd.DataFrame:
    out = df.copy()
    if "Booking number" in out.columns:
        out = out.loc[~_is_opening_booking(out["Booking number"])]

    if "Account number" in out.columns and "Entity" in out.columns:
        ent = out["Entity"].fillna("").astype(str).str.strip()
        acct = out["Account number"].fillna("").astype(str).str.strip()
        is_np = pd.Series(False, index=out.index)
        for entity, accounts in np_by_entity.items():
            if accounts:
                is_np |= (ent == entity) & acct.isin(accounts)
        out = out.loc[~is_np]

    return out.reset_index(drop=True)


def export_gl_data(
    source: Path,
    out_root: Path,
    mapping_xlsx: Path,
    years: list[int] | None = None,
) -> list[Path]:
    years = years or YEARS
    year_set = {str(y) for y in years}
    np_by_entity = _net_profit_accounts(mapping_xlsx)
    gl_root = out_root / "GL data"
    gl_root.mkdir(parents=True, exist_ok=True)

    header = pd.read_csv(source, nrows=0, encoding="utf-8-sig").columns.tolist()
    read_cols = [c for c in EXPORT_COLUMNS if c in header]
    missing = [c for c in EXPORT_COLUMNS if c not in header]
    if missing:
        raise SystemExit(f"Source missing columns: {missing}")

    buckets: dict[tuple[str, str], list[pd.DataFrame]] = {}
    for entity in ENTITIES:
        for year in years:
            (gl_root / entity / str(year)).mkdir(parents=True, exist_ok=True)

    for chunk in pd.read_csv(
        source,
        usecols=read_cols,
        dtype=str,
        encoding="utf-8-sig",
        chunksize=250_000,
    ):
        chunk = chunk[
            chunk["Entity"].astype(str).str.strip().isin(ENTITIES)
            & chunk["Year"].astype(str).str.strip().isin(year_set)
        ]
        if chunk.empty:
            continue
        chunk = _filter_frame(chunk, np_by_entity)
        if chunk.empty:
            continue
        chunk["Entity"] = chunk["Entity"].astype(str).str.strip()
        chunk["Year"] = chunk["Year"].astype(str).str.strip()
        for (entity, year), sub in chunk.groupby(["Entity", "Year"], sort=False):
            if entity not in ENTITIES or year not in year_set:
                continue
            buckets.setdefault((entity, year), []).append(sub)

    written: list[Path] = []
    for entity in ENTITIES:
        for year in years:
            dest = gl_root / entity / str(year) / "gl_entry.csv"
            parts = buckets.get((entity, str(year)), [])
            if not parts:
                pd.DataFrame(columns=read_cols).to_csv(dest, index=False, encoding="utf-8-sig")
                written.append(dest)
                print(f"{entity}/{year}: 0 rows (empty file)")
                continue
            combined = pd.concat(parts, ignore_index=True)
            combined.to_csv(dest, index=False, encoding="utf-8-sig")
            written.append(dest)
            print(f"{entity}/{year}: {len(combined):,} rows -> {dest}")

    print(f"\nWrote {len(written)} files under {gl_root}")
    for ent, accts in np_by_entity.items():
        if accts:
            print(f"  Net profit filter {ent}: accounts {sorted(accts)}")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Export GL data for Project Setup wizard")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--years", type=int, nargs="*", default=YEARS)
    args = parser.parse_args()
    if not args.source.is_file():
        raise SystemExit(f"Source not found: {args.source}")
    export_gl_data(args.source, args.out_dir, args.mapping, list(args.years))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
