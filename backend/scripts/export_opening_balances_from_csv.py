"""Export opening-balance CSVs from a GoBD CSV file (no DB required)."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

OUTPUT_COLUMNS = [
    "Account number",
    "Account description",
    "Entity",
    "Amount",
    "Posting date",
]

DEFAULT_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "etl"
    / "source_data"
    / "gobd_gl"
    / "Unmapped_GoBD_patched_account1.csv"
)


def _safe_filename(name: str) -> str:
    slug = re.sub(r"[^\w\-]+", "_", name.strip(), flags=re.UNICODE).strip("_")
    return slug or "entity"


def _is_opening(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().isin({"6", "6.0", "6,0"})


def _account_description_lookup(df: pd.DataFrame) -> dict[tuple[str, str], str]:
    non_ob = df.loc[~_is_opening(df["Booking number"])].copy()
    text = non_ob["Booking text"].fillna("").astype(str).str.strip()
    non_ob = non_ob.loc[text.ne("")]
    if non_ob.empty:
        return {}
    grouped = (
        non_ob.groupby(["Entity", "Account number"], sort=False)["Booking text"]
        .agg(lambda s: s.mode().iloc[0] if not s.mode().empty else s.iloc[0])
    )
    return {(str(ent).strip(), str(acct).strip()): str(desc).strip() for (ent, acct), desc in grouped.items()}


def export_from_csv(source: Path, out_dir: Path, first_year: int = 2022) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(source, dtype=str, encoding="utf-8-sig")
    lookup = _account_description_lookup(df)
    ob = df.loc[_is_opening(df["Booking number"])].copy()
    if ob.empty:
        raise SystemExit(f"No booking number 6 rows found in {source}")

    years = pd.to_numeric(ob["Year"], errors="coerce")
    projected = pd.DataFrame(
        {
            "Account number": ob["Account number"].astype(str).str.strip(),
            "Account description": ob.apply(
                lambda r: lookup.get(
                    (str(r["Entity"]).strip(), str(r["Account number"]).strip()),
                    "",
                ),
                axis=1,
            ),
            "Entity": ob["Entity"].astype(str).str.strip(),
            "Amount": ob["Amount"],
            "Posting date": ob["Posting date"],
            "_year": years,
        }
    )

    written: list[Path] = []
    for entity, group in projected.groupby("Entity", sort=True):
        slug = _safe_filename(str(entity))
        g_all = group.drop(columns=["_year"])
        g_2022 = group[group["_year"].eq(first_year)].drop(columns=["_year"])
        p2022 = out_dir / f"{slug}_opening_balances_2022_only.csv"
        pall = out_dir / f"{slug}_opening_balances_all_years.csv"
        g_2022[OUTPUT_COLUMNS].to_csv(p2022, index=False, encoding="utf-8-sig")
        g_all[OUTPUT_COLUMNS].to_csv(pall, index=False, encoding="utf-8-sig")
        written.extend([p2022, pall])
        print(f"{entity}: {len(g_2022)} rows (2022), {len(g_all)} rows (all years)")

    combined_2022 = out_dir / "all_entities_opening_balances_2022_only.csv"
    combined_all = out_dir / "all_entities_opening_balances_all_years.csv"
    projected.loc[projected["_year"].eq(first_year), OUTPUT_COLUMNS].to_csv(
        combined_2022, index=False, encoding="utf-8-sig"
    )
    projected[OUTPUT_COLUMNS].to_csv(combined_all, index=False, encoding="utf-8-sig")
    written.extend([combined_2022, combined_all])
    print(f"Wrote {len(written)} files to {out_dir}")
    return written


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--first-year", type=int, default=2022)
    args = parser.parse_args()
    export_from_csv(args.source, args.out_dir, args.first_year)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
