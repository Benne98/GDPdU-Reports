r"""Bulk-load the AR/AP OPOS subledger (4 fiscal years x 2 sides) into
``fact_opos_debitor`` / ``fact_opos_kreditor`` on finssentials_v4.

Phase-1 AR/AP aging rebuild.  Reads the eight source workbooks::

    <root>/{2022,2023,2024,2025}/{debitor,kreditor}.xlsx

maps every column to the 0023+0024 fact schema, resolves the entity identity via
:mod:`app.services.entities` (Buchungskreis -> entity_prefix -> partner_key), and
loads ALL four ``Satzart`` values (Bewegung / Vortrag / Fact-Ergaenzung /
Bilanzabstimmung) — each carries a real signed amount.

IDEMPOTENT per (side, fy_label): each side+year is DELETEd (by project_id +
fy_label) then re-inserted, so re-runs are safe and converge to identical rows.
``is_open`` / ``aging_band`` are left NULL (aging is a Phase-2 read-time step).

USAGE (PowerShell)::

    $env:DB_PASSWORD='<postgres-password>'; $env:DB_NAME='finssentials_v4'
    backend\.venv\Scripts\python.exe backend\scripts\load_opos_subledger.py `
        --root "C:\Users\bened\OneDrive\Desktop\Finssentials - Setup\subledgers"

    # single side / year, or a dry run (parse + map, no DB write):
    ... load_opos_subledger.py --side debitor --year 2022
    ... load_opos_subledger.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from sqlalchemy import text  # noqa: E402

from _db_safety import assert_not_live_db  # noqa: E402
from app.db import engine  # noqa: E402
from app.services.draft_ingest import to_date, to_number  # noqa: E402
from app.services.entities import BUKRS_TO_PREFIX  # noqa: E402

YEARS = (2022, 2023, 2024, 2025)
SIDES = ("debitor", "kreditor")
_SIDE_TABLE = {"debitor": "fact_opos_debitor", "kreditor": "fact_opos_kreditor"}
_SIDE_PARTNER_COL = {"debitor": "Debitor", "kreditor": "Kreditor"}

_INSERT_COLUMNS = [
    "project_id", "dataset_version_id", "source_file_id", "row_no",
    "entity_prefix", "fy_label",
    "partner_id", "konto", "belegart", "beleg_no", "referenz",
    "net_due_date", "amount_hauswaehrung", "posting_date",
    "buchungskreis", "satzart", "partner_no", "partner_key", "beleg_date",
    "mahnstufe", "waehrung", "geschaeftsbereich", "buchungsschluessel",
    "konto_gegenbuchung", "gobd_transaktionsnr",
    "is_open", "aging_band",
]

_CHUNK = 10_000


def _clean_id(value) -> str | None:
    """Normalize a partner/account/document id cell to a clean string.

    Excel often reads integer-like ids as floats (240160.0); collapse those to
    '240160' so partner_key matches the master customer_id/supplier_id format.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return None
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def _to_int(value) -> int | None:
    n = to_number(value)
    return int(round(n)) if n is not None else None


def _find_col(cols: list[str], *candidates: str) -> str | None:
    """Match a source column by exact strip; tolerant of stray whitespace."""
    norm = {str(c).strip(): c for c in cols}
    for cand in candidates:
        if cand in norm:
            return norm[cand]
    return None


def _map_rows(df: pd.DataFrame, side: str, year: int, src_name: str) -> list[dict]:
    cols = list(df.columns)
    c = {
        "bukrs": _find_col(cols, "Buchungskreis"),
        "konto": _find_col(cols, "konto", "Konto"),
        "buchungsdatum": _find_col(cols, "Buchungsdatum"),
        "belegnummer": _find_col(cols, "Belegnummer"),
        "belegdatum": _find_col(cols, "Belegdatum"),
        "nettofaelligkeit": _find_col(cols, "Nettofälligkeit", "Nettofaelligkeit"),
        "belegart": _find_col(cols, "Belegart"),
        "geschaeftsbereich": _find_col(cols, "Geschäftsbereich", "Geschaeftsbereich"),
        "buchungsschluessel": _find_col(cols, "Buchungsschlüssel", "Buchungsschluessel"),
        "mahnstufe": _find_col(cols, "Mahnstufe"),
        "waehrung": _find_col(cols, "Währung", "Waehrung"),
        "amount": _find_col(cols, "Betrag in Hauswährung", "Betrag in Hauswaehrung"),
        "gegenbuchung": _find_col(cols, "Konto Gegenbuchung"),
        "referenz": _find_col(cols, "Referenz"),
        "partner": _find_col(cols, _SIDE_PARTNER_COL[side]),
        "gobd_tx": _find_col(cols, "GoBD_Transaktionsnr"),
        "satzart": _find_col(cols, "Satzart"),
    }
    missing = [k for k, v in c.items() if v is None]
    if missing:
        raise SystemExit(f"[{side} {year}] missing source columns: {missing} in {cols}")

    records = df.to_dict(orient="records")
    out: list[dict] = []
    unresolved = 0
    for idx, r in enumerate(records):
        bukrs = _to_int(r.get(c["bukrs"]))
        prefix = BUKRS_TO_PREFIX.get(bukrs) if bukrs is not None else None
        if prefix is None:
            unresolved += 1
        konto = _clean_id(r.get(c["konto"]))
        partner_no = _clean_id(r.get(c["partner"]))
        partner_key = f"{prefix}{partner_no}" if (prefix and partner_no) else None
        partner_id = f"{prefix}{konto}" if (prefix and konto) else None
        out.append({
            "project_id": "default",
            "dataset_version_id": None,
            "source_file_id": src_name,
            "row_no": idx,
            "entity_prefix": prefix,
            "fy_label": str(year),
            "partner_id": partner_id,
            "konto": konto,
            "belegart": _clean_id(r.get(c["belegart"])),
            "beleg_no": _clean_id(r.get(c["belegnummer"])),
            "referenz": _clean_id(r.get(c["referenz"])),
            "net_due_date": to_date(r.get(c["nettofaelligkeit"])),
            "amount_hauswaehrung": to_number(r.get(c["amount"])),
            "posting_date": to_date(r.get(c["buchungsdatum"])),
            "buchungskreis": bukrs,
            "satzart": _clean_id(r.get(c["satzart"])),
            "partner_no": partner_no,
            "partner_key": partner_key,
            "beleg_date": to_date(r.get(c["belegdatum"])),
            "mahnstufe": _to_int(r.get(c["mahnstufe"])),
            "waehrung": _clean_id(r.get(c["waehrung"])),
            "geschaeftsbereich": _clean_id(r.get(c["geschaeftsbereich"])),
            "buchungsschluessel": _clean_id(r.get(c["buchungsschluessel"])),
            "konto_gegenbuchung": _clean_id(r.get(c["gegenbuchung"])),
            "gobd_transaktionsnr": _clean_id(r.get(c["gobd_tx"])),
            "is_open": None,
            "aging_band": None,
        })
    if unresolved:
        print(f"    WARNING: {unresolved} rows had an unmapped Buchungskreis (entity_prefix NULL)")
    return out


def _load_one(side: str, year: int, root: Path, dry_run: bool) -> int:
    table = _SIDE_TABLE[side]
    path = root / str(year) / f"{side}.xlsx"
    if not path.exists():
        raise SystemExit(f"missing source file: {path}")
    df = pd.read_excel(path)
    df.columns = [str(col).strip() for col in df.columns]
    rows = _map_rows(df, side, year, path.name)
    print(f"  {side} {year}: parsed {len(rows)} rows from {path.name}")
    if dry_run:
        return len(rows)

    placeholders = ", ".join(f":{col}" for col in _INSERT_COLUMNS)
    insert_sql = text(
        f"INSERT INTO {table} ({', '.join(_INSERT_COLUMNS)}) VALUES ({placeholders})"
    )
    with engine.begin() as conn:
        conn.execute(
            text(f"DELETE FROM {table} WHERE project_id = :pid AND fy_label = :fy"),
            {"pid": "default", "fy": str(year)},
        )
        for i in range(0, len(rows), _CHUNK):
            conn.execute(insert_sql, rows[i:i + _CHUNK])
    print(f"    -> loaded {len(rows)} into {table} (fy_label={year})")
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="Load OPOS subledger into finssentials_v4.")
    ap.add_argument("--root", default=r"C:\Users\bened\OneDrive\Desktop\Finssentials - Setup\subledgers")
    ap.add_argument("--side", choices=SIDES, help="only this side (default: both)")
    ap.add_argument("--year", type=int, choices=YEARS, help="only this year (default: all)")
    ap.add_argument("--dry-run", action="store_true", help="parse + map, no DB write")
    ap.add_argument(
        "--i-know-this-is-live", action="store_true",
        help="override the live-DB refusal (DANGER: writes to the LIVE 'Finssentials' DB)",
    )
    args = ap.parse_args()

    root = Path(args.root)
    sides = (args.side,) if args.side else SIDES
    years = (args.year,) if args.year else YEARS
    if not args.dry_run:
        print(f"Target DB: {engine.url.database!r} @ {engine.url.host}")
        assert_not_live_db(engine, allow_live=args.i_know_this_is_live)

    totals: dict[str, int] = {}
    for side in sides:
        for year in years:
            totals[f"{side} {year}"] = _load_one(side, year, root, args.dry_run)

    print("\n=== ROW COUNTS ===")
    for k in sorted(totals):
        print(f"  {k:20} {totals[k]:>8}")
    print(f"  {'TOTAL':20} {sum(totals.values()):>8}")


if __name__ == "__main__":
    main()
