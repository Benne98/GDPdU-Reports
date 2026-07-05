r"""OPOS reconciliation PROOF — Method A (open balance as-of year-end).

Phase-1 AR/AP aging rebuild.  Independently reproduces the reconciliation.xlsx
``Saldo (LuL)`` tie-out column from the loaded OPOS facts (or, with
``--from-files``, straight from the source workbooks using the identical
transformation the loader applies).

METHOD A
--------
    open balance as-of year-end(N)
      = SUM(amount_hauswaehrung)                         -- signed, gross incl. USt
        over rows at (entity, partner_key, konto) grain  -- summed to entity x year
        WHERE fy_label = N                               -- fiscal-year membership
          AND konto in the trade-LuL account set         -- see LuL classification
        summing ALL four Satzart (Bewegung / Vortrag / Fact-Ergaenzung /
        Bilanzabstimmung) — each carries a real amount.

Grouping is by ``fy_label`` (fiscal-year membership), which is how both the
subledger files and reconciliation.xlsx are constructed.  The literal
``posting_date <= as_of(Dec-31)`` clamp from the spec is a NO-OP for a full-year
tie EXCEPT where the source ``Buchungsdatum`` is corrupt: the Novara-2022 file
carries 7.6k FY-2022 LuL rows mis-stamped 2024.  Pass ``--asof-clamp`` to apply
the posting_date filter and see that divergence; it is OFF by default so the tie
matches the recon basis.

LuL ACCOUNT CLASSIFICATION
--------------------------
The authoritative account-map is reconciliation.xlsx ``Debitor_Detail`` /
``Kreditor_Detail`` (``Klasse`` contains "LuL").  That resolves to::

    AR (Forderungen LuL)     : 23500, 23502, 24000, 24905
    AP (Verbindlichkeiten LuL): 35500, 36000, 36905

Note this is BROADER than a naive konto-prefix (24xxx / 36xxx): 23500/23502 (AR)
and 35500 (AP) are LuL too, and omitting 23500 is exactly the Meridian-2024 AR
~1933 EUR break.  ``--konto-prefix`` forces the naive prefix rule instead (and
prints that it is used).

TARGET: within +/-1.00 EUR per entity-year for 2022-2024.  Jul-2025 is a partial
as-of (no full-year tie) — reported for continuity only.

USAGE::

    $env:DB_PASSWORD='...'; $env:DB_NAME='finssentials_v4'
    backend\.venv\Scripts\python.exe backend\scripts\reconcile_opos.py
    ... reconcile_opos.py --from-files      # bypass DB, compute from source xlsx
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from _db_safety import assert_not_live_db  # noqa: E402
from app.services.entities import BUKRS_TO_PREFIX, ENTITY_BUKRS, ENTITY_PREFIX  # noqa: E402

YEARS = (2022, 2023, 2024, 2025)
_DEFAULT_ROOT = r"C:\Users\bened\OneDrive\Desktop\Finssentials - Setup\subledgers"
_SIDE = {
    "debitor": {"table": "fact_opos_debitor", "detail": "Debitor_Detail",
                "checks": "Debitor_Checks", "partner": "Debitor", "prefix": "24"},
    "kreditor": {"table": "fact_opos_kreditor", "detail": "Kreditor_Detail",
                 "checks": "Kreditor_Checks", "partner": "Kreditor", "prefix": "36"},
}


def lul_kontos(recon_path: Path, detail_sheet: str, fallback_prefix: str,
               use_prefix: bool) -> tuple[set[str], str]:
    if use_prefix:
        return set(), f"konto-prefix {fallback_prefix}xxx (naive rule)"
    det = pd.read_excel(recon_path, sheet_name=detail_sheet)
    lul = det[det["Klasse"].astype(str).str.contains("LuL")]
    kontos = set(lul["konto"].astype(str).str.strip())
    if not kontos:
        return set(), f"konto-prefix {fallback_prefix}xxx (Detail had no LuL rows)"
    return kontos, f"Detail '{detail_sheet}' Klasse=LuL -> {sorted(kontos)}"


def mine_from_files(side: str, root: Path, kontos: set[str], prefix: str,
                    use_prefix: bool, asof_clamp: bool) -> dict[int, pd.Series]:
    out: dict[int, pd.Series] = {}
    for y in YEARS:
        df = pd.read_excel(root / str(y) / f"{side}.xlsx")
        df.columns = [str(c).strip() for c in df.columns]
        amt = pd.to_numeric(df["Betrag in Hauswährung"], errors="coerce")
        konto = df["konto"].astype(str).str.strip()
        ent = df["Buchungskreis"].astype(int).map(ENTITY_BUKRS)
        mask = konto.str.startswith(prefix) if use_prefix else konto.isin(kontos)
        sub = pd.DataFrame({"ent": ent, "amt": amt})[mask]
        if asof_clamp:
            pdate = pd.to_datetime(df["Buchungsdatum"], errors="coerce")
            sub = sub[pdate[mask].values <= pd.Timestamp(f"{y}-12-31")]
        out[y] = sub.groupby("ent")["amt"].sum()
    return out


def mine_from_db(side: str, kontos: set[str], prefix: str, use_prefix: bool,
                 asof_clamp: bool) -> dict[int, pd.Series]:
    from sqlalchemy import text
    from app.db import engine

    table = _SIDE[side]["table"]
    where = ["project_id = 'default'"]
    params: dict = {}
    if use_prefix:
        where.append("konto LIKE :kp")
        params["kp"] = f"{prefix}%"
    else:
        where.append("konto = ANY(:kontos)")
        params["kontos"] = sorted(kontos)
    if asof_clamp:
        where.append("posting_date <= make_date(fy_label::int, 12, 31)")
    sql = text(
        f"SELECT entity_prefix, fy_label, SUM(amount_hauswaehrung) AS s "
        f"FROM {table} WHERE {' AND '.join(where)} "
        f"GROUP BY entity_prefix, fy_label"
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    out: dict[int, pd.Series] = {y: pd.Series(dtype=float) for y in YEARS}
    buckets: dict[int, dict[str, float]] = {y: {} for y in YEARS}
    for prefix_code, fy, s in rows:
        name = ENTITY_PREFIX.get(str(prefix_code))
        try:
            y = int(fy)
        except (TypeError, ValueError):
            continue
        if name and y in buckets:
            buckets[y][name] = buckets[y].get(name, 0.0) + float(s or 0.0)
    for y in YEARS:
        out[y] = pd.Series(buckets[y], dtype=float)
    return out


def tieout(side: str, mine: dict[int, pd.Series], recon_path: Path) -> float:
    checks = pd.read_excel(recon_path, sheet_name=_SIDE[side]["checks"])
    col = "Saldo (LuL)"
    print(f"\n==== TIE-OUT {side.upper()} (mine vs recon '{col}') ====")
    print(f"{'entity':10}{'year':>6}{'mine':>16}{'recon':>16}{'delta':>12}  status")
    worst = 0.0
    for y in YEARS:
        for _, row in checks[checks["Jahr"] == y].iterrows():
            ent, rv = row["Entity"], row[col]
            mv = mine[y].get(ent, np.nan)
            delta = (mv - rv) if pd.notna(mv) else np.nan
            if y <= 2024 and pd.notna(delta):
                worst = max(worst, abs(delta))
            if y == 2025:
                st = "partial-2025"
            elif pd.notna(delta) and abs(delta) <= 1.0:
                st = "PASS"
            else:
                st = "FAIL"
            mv_s = f"{mv:16.2f}" if pd.notna(mv) else f"{'n/a':>16}"
            dl_s = f"{delta:12.2f}" if pd.notna(delta) else f"{'n/a':>12}"
            print(f"{ent:10}{y:>6}{mv_s}{rv:16.2f}{dl_s}  {st}")
    verdict = "PASS" if worst <= 1.0 else "FAIL"
    print(f"  worst |delta| 2022-2024: {worst:.4f}  -> {verdict}")
    return worst


def main() -> None:
    ap = argparse.ArgumentParser(description="OPOS Method-A reconciliation proof.")
    ap.add_argument("--root", default=_DEFAULT_ROOT)
    ap.add_argument("--from-files", action="store_true",
                    help="compute from source xlsx instead of the DB")
    ap.add_argument("--asof-clamp", action="store_true",
                    help="additionally filter posting_date<=Dec-31 (diverges on corrupt dates)")
    ap.add_argument("--konto-prefix", action="store_true",
                    help="use naive konto-prefix LuL rule instead of the Detail account-map")
    ap.add_argument("--i-know-this-is-live", action="store_true",
                    help="override the live-DB refusal for the DB read path (DANGER)")
    args = ap.parse_args()

    root = Path(args.root)
    recon_path = root / "reconciliation.xlsx"

    # Pre-flight live-DB guard: only the DB-read path touches a database, so the
    # guard is a no-op for --from-files.  Refuse to query the live "Finssentials"
    # DB unless explicitly overridden (intended target: finssentials_v4).
    if not args.from_files:
        from app.db import engine as _engine

        db = assert_not_live_db(_engine, allow_live=args.i_know_this_is_live)
        print(f"Target DB: {db!r} @ {_engine.url.host}")

    worst_overall = 0.0
    for side in ("debitor", "kreditor"):
        kontos, how = lul_kontos(recon_path, _SIDE[side]["detail"],
                                 _SIDE[side]["prefix"], args.konto_prefix)
        print(f"\n[{side}] LuL accounts via {how}")
        if args.from_files:
            mine = mine_from_files(side, root, kontos, _SIDE[side]["prefix"],
                                   args.konto_prefix, args.asof_clamp)
        else:
            try:
                mine = mine_from_db(side, kontos, _SIDE[side]["prefix"],
                                    args.konto_prefix, args.asof_clamp)
            except Exception as exc:  # noqa: BLE001
                print(f"  DB read failed ({type(exc).__name__}: {exc}).")
                print("  Re-run with --from-files, or set DB_PASSWORD/DB_NAME=finssentials_v4.")
                sys.exit(2)
        worst_overall = max(worst_overall, tieout(side, mine, recon_path))

    print(f"\n=== OVERALL 2022-2024: worst |delta| = {worst_overall:.4f} -> "
          f"{'PASS' if worst_overall <= 1.0 else 'FAIL'} (target +/-1.00 EUR) ===")


if __name__ == "__main__":
    main()
