r"""Export opening-balance datasets from ``Unmapped_GoBD`` for Project Setup.

For each entity, writes two CSV files:
  1. ``{entity}_opening_balances_2022_only.csv`` — booking number 6, year 2022 only
  2. ``{entity}_opening_balances_all_years.csv`` — booking number 6, all years

Columns: Account number, Account description, Entity, Amount, Posting date

USAGE
-----
  $env:DB_PASSWORD='...'; $env:DB_NAME='Finssentials'
  python backend/scripts/export_opening_balances_unmapped_gobd.py

Optional:
  --out-dir exports/opening_balances
  --table Unmapped_GoBD
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
_REPO = _BACKEND.parent
for _p in (str(_BACKEND), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session as SASession

from app.db import engine

OUTPUT_COLUMNS = [
    "Account number",
    "Account description",
    "Entity",
    "Amount",
    "Posting date",
]

BOOKING_ALIASES = ("booking number", "booking_number", "booking no", "booking no.")
YEAR_ALIASES = ("year", "fiscal_year", "fiscal year")
ENTITY_ALIASES = ("entity", "entity no", "entity no.", "entity_name", "legal entity")
ACCOUNT_ALIASES = ("account number", "account_number", "account no", "account no.")
DESC_ALIASES = (
    "account description",
    "account_description",
    "account name",
    "account",
    "account text",
)
AMOUNT_ALIASES = ("amount", "betrag")
POSTING_ALIASES = ("posting date", "posting_date", "buchungsdatum")


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", str(name).strip().lower())


def _resolve_column(columns: list[str], aliases: tuple[str, ...]) -> str | None:
    by_norm = {_norm(c): c for c in columns}
    for alias in aliases:
        hit = by_norm.get(_norm(alias))
        if hit:
            return hit
    return None


def _resolve_table(session: SASession, preferred: str) -> str:
    insp = inspect(engine)
    names = insp.get_table_names()
    if preferred in names:
        return preferred
    lower_map = {n.lower(): n for n in names}
    hit = lower_map.get(preferred.lower())
    if hit:
        return hit
    raise SystemExit(
        f"Table {preferred!r} not found. Available tables containing 'gobd' or 'unmapped': "
        f"{[n for n in names if 'gobd' in n.lower() or 'unmapped' in n.lower()]}"
    )


def _load_table(session: SASession, table: str) -> pd.DataFrame:
    q = text(f'SELECT * FROM "{table}"' if table != table.lower() else f"SELECT * FROM {table}")
    return pd.read_sql(q, session.bind)


def _booking_mask(series: pd.Series) -> pd.Series:
    s = series.fillna("").astype(str).str.strip()
    return s.isin({"6", "6.0", "6,0"})


def _year_series(df: pd.DataFrame, year_col: str | None, posting_col: str) -> pd.Series:
    if year_col and year_col in df.columns:
        y = pd.to_numeric(df[year_col], errors="coerce")
        if y.notna().any():
            return y
        y = df[year_col].astype(str).str.extract(r"(\d{4})")[0]
        return pd.to_numeric(y, errors="coerce")
    dates = pd.to_datetime(df[posting_col], dayfirst=True, errors="coerce")
    return dates.dt.year


def _entity_label(df: pd.DataFrame, entity_col: str) -> pd.Series:
    return df[entity_col].astype(str).str.strip()


def _safe_filename(entity: str) -> str:
    slug = re.sub(r"[^\w\-]+", "_", entity.strip(), flags=re.UNICODE).strip("_")
    return slug or "entity"


def _project(df: pd.DataFrame, cols: dict[str, str]) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            "Account number": df[cols["account"]].astype(str).str.strip(),
            "Account description": df[cols["desc"]].fillna("").astype(str).str.strip(),
            "Entity": _entity_label(df, cols["entity"]),
            "Amount": df[cols["amount"]],
            "Posting date": df[cols["posting"]],
        }
    )
    return out.sort_values(["Entity", "Posting date", "Account number"], kind="stable")


def _enrich_description(session: SASession, df: pd.DataFrame) -> pd.DataFrame:
    missing = df["Account description"].eq("") | df["Account description"].eq("nan")
    if not missing.any():
        return df
    try:
        names = session.execute(
            text(
                """
                SELECT DISTINCT account_number_group, account_name
                FROM dim_gl_account
                WHERE account_name IS NOT NULL AND account_name <> ''
                """
            )
        ).fetchall()
    except Exception:
        return df
    if not names:
        return df
    lookup = {str(r[0]).strip(): str(r[1]).strip() for r in names}
    filled = df.copy()
    for idx, row in filled.loc[missing].iterrows():
        acct = str(row["Account number"]).strip()
        for key, name in lookup.items():
            if key.endswith(acct) or acct in key:
                filled.at[idx, "Account description"] = name
                break
    return filled


def main() -> int:
    parser = argparse.ArgumentParser(description="Export opening balances from Unmapped_GoBD.")
    parser.add_argument("--table", default="Unmapped_GoBD")
    parser.add_argument("--first-year", type=int, default=2022)
    parser.add_argument(
        "--out-dir",
        default=str(_REPO / "exports" / "opening_balances"),
        help="Output directory for per-entity CSV files",
    )
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with SASession(engine) as session:
        table = _resolve_table(session, args.table)
        raw = _load_table(session, table)
        if raw.empty:
            print(f"Table {table} is empty.")
            return 1

        cols = raw.columns.tolist()
        resolved = {
            "booking": _resolve_column(cols, BOOKING_ALIASES),
            "year": _resolve_column(cols, YEAR_ALIASES),
            "entity": _resolve_column(cols, ENTITY_ALIASES),
            "account": _resolve_column(cols, ACCOUNT_ALIASES),
            "desc": _resolve_column(cols, DESC_ALIASES),
            "amount": _resolve_column(cols, AMOUNT_ALIASES),
            "posting": _resolve_column(cols, POSTING_ALIASES),
        }
        missing = [k for k, v in resolved.items() if v is None and k not in {"year", "desc"}]
        if missing:
            raise SystemExit(
                f"Could not resolve columns {missing} in {table}. Found: {cols}"
            )
        if resolved["desc"] is None:
            ob["__account_description__"] = ""
            resolved["desc"] = "__account_description__"

        ob = raw.loc[_booking_mask(raw[resolved["booking"]])].copy()
        if ob.empty:
            print(f"No rows with booking number 6 in {table}.")
            return 1

        projected = _project(ob, resolved)
        projected = _enrich_description(session, projected)

        # Attach year for filtering (not exported)
        projected["_year"] = _year_series(ob, resolved["year"], resolved["posting"])

        print(f"Source: {table} — {len(ob)} opening-balance rows (booking number 6)")
        print(f"Entities: {sorted(projected['Entity'].unique().tolist())}")
        print(f"Writing to: {out_dir}")

        written: list[Path] = []
        for entity, group in projected.groupby("Entity", sort=True):
            slug = _safe_filename(str(entity))
            g = group.drop(columns=["_year"])
            g2022 = group[group["_year"].eq(args.first_year)].drop(columns=["_year"])
            path_2022 = out_dir / f"{slug}_opening_balances_2022_only.csv"
            path_all = out_dir / f"{slug}_opening_balances_all_years.csv"
            g2022[OUTPUT_COLUMNS].to_csv(path_2022, index=False, encoding="utf-8-sig")
            g[OUTPUT_COLUMNS].to_csv(path_all, index=False, encoding="utf-8-sig")
            written.extend([path_2022, path_all])
            print(f"  {entity}: {len(g2022)} rows (2022), {len(g)} rows (all years)")

        combined_2022 = out_dir / "all_entities_opening_balances_2022_only.csv"
        combined_all = out_dir / "all_entities_opening_balances_all_years.csv"
        projected.loc[projected["_year"].eq(args.first_year), OUTPUT_COLUMNS].to_csv(
            combined_2022, index=False, encoding="utf-8-sig"
        )
        projected[OUTPUT_COLUMNS].to_csv(combined_all, index=False, encoding="utf-8-sig")
        written.extend([combined_2022, combined_all])
        print(f"Combined files: {combined_2022.name}, {combined_all.name}")
        print(f"Done — {len(written)} file(s) written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
