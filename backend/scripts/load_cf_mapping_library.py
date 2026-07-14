r"""Seed / extend the **CF mapping library** (``lib_cf_mapping``).

The library is the shared, accumulating reference the populate step
(``populate_dim_gl_cf.py``) joins to fill ``dim_gl_cf`` for any classified
project.  It has TWO sources, both idempotent (re-runnable; new mappings
accumulate):

  1. BS / Net-asset side  — from the provided workbook ``CF Mapping.xlsx``
     (sheet ``Tabelle1``), keyed by the NA classification
     ``(NA, NA Description)`` which matches ``dim_gl_na``
     ``(l6_na_mapping, l7_na_description)`` exactly.  key_kind='na'.

  2. P&L side  — DERIVED deterministically from the existing income-statement
     ``level_3`` classification (the workbook covers ONLY the BS/NA side).  The
     CF structure consumes four P&L-sourced leaves:
        EBITDA / Taxes on income / Depreciation & amortisation / Financial result
     The assignment mirrors the P&L statement bands (everything ABOVE the EBITDA
     calc row → 'EBITDA'; D&A → its own leaf; the financial-result block →
     'Financial result'; Taxes on income → its leaf; **Other taxes → its OWN
     dedicated operating leaf** placed AFTER Gross cash flow, see below).
     key_kind='pl_level3'.

     OTHER TAXES — own leaf inside Gross cash flow, reconciles Net CF to ΔCash
     ---------------------------------------------------------------------------
     'Other taxes' (KFZ-Steuer, Grundsteuer, Grundbesitzabgaben) is a real non-cash
     P&L expense that WAS previously left UNMAPPED, so it dropped out of the indirect
     Cash Flow and the Net cash flow did NOT tie to the actual change in cash & cash
     equivalents (FY2023: Net CF 8,837 €k vs ΔCash 8,737 €k — a 100 €k gap, exactly
     the presented Other-taxes total ≈ −99.4 €k).  It is now mapped to its OWN
     ``cf_mapping='Other taxes'`` leaf that the CF structure places DIRECTLY AFTER
     "Taxes on income" and BEFORE the "Gross cash flow" subtotal — so it is PART of
     the Gross cash flow build-up (product decision).  The resulting identities:
        * CF EBITDA == P&L EBITDA                                (Other taxes ∉ EBITDA
          leaf set — still LOCKED; Other taxes is its own separate cf_mapping leaf)
        * Gross cash flow == EBITDA + Taxes on income + Other taxes   (Other taxes now
          INSIDE Gross cash flow — the previous "EBITDA + Taxes" identity is
          intentionally superseded)
        * Net cash flow == ΔCash                                 (unchanged — only the
          leaf's POSITION moved; the total still Σ's every non-cash leaf)
     See docs/financial-logic.md "Cash Flow mapping library".

═══════════════════════════════════════════════════════════════════════════════
COLUMN CORRESPONDENCE (verified against etl.mapping_account.CF_FIELDS +
backend/app/services/fin_compat_cf_sql.py — the SQL groups by cf.l1/l2/l3 and the
cf_mapping leaf):
    dim_gl_cf.l1         ← workbook 'L11 CF 1'
    dim_gl_cf.l2         ← workbook 'L11 CF 1.2'      (l2_sort ← 'L11 CF 1.2 sort')
    dim_gl_cf.l3         ← workbook 'L11 CF 1.3'      (l3_sort ← 'L11 CF 1.3 sort')
    dim_gl_cf.l4         ← workbook 'L12 CF 2'
    dim_gl_cf.l5         ← workbook 'L13 CF 3'
    dim_gl_cf.cf_mapping ← workbook 'CF Mapping'      (the matched leaf)
═══════════════════════════════════════════════════════════════════════════════

DUPLICATE / AMBIGUOUS WORKBOOK ROWS
-----------------------------------
The workbook is keyed by (NA, NA Description) but a few descriptions appear twice
(e.g. "Shareholder loans", "Loan RCLB to BUB" — once asset-side, once
liability-side; plus a handful of exact duplicates).  Because the PK is
(NA, NA Description), only one row can win.  Deterministic disambiguation:
  * exact-duplicate rows (every CF column identical) → no conflict, last wins.
  * genuine conflicts (same key, different CF columns) → keep the row whose
    ``L13 CF 3`` (the most specific side label) sorts FIRST (stable, deterministic),
    and LOG every dropped alternative so the library can be reviewed/extended.
The conflicting rows here differ only in l4/l5 *labels*; the matched ``cf_mapping``
leaf is identical, so the CF AMOUNTS are unaffected by the choice — only the
drill-path label differs.

ADDING MAPPINGS FOR FUTURE PROJECTS
-----------------------------------
* New NA classification → add a row to the workbook (or any sheet with the same
  columns) and re-run this loader; the UPSERT accumulates it.
* New P&L level_3 that should feed a CF leaf → extend ``_PL_LEVEL3_TO_CF`` below
  (keep the EBITDA build-up == the P&L EBITDA band, or the recon test fails).
* The library is project-agnostic: any project whose ``dim_gl_na`` / P&L
  ``level_3`` carry these classifications auto-matches in the populate step.

USAGE
-----
  $env:DB_PASSWORD='...'; $env:DB_NAME='finssentials_v2'
  backend\.venv\Scripts\python.exe backend\scripts\load_cf_mapping_library.py
  # custom workbook:
  ... load_cf_mapping_library.py --xlsx "C:/path/CF Mapping.xlsx" --sheet Tabelle1
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from sqlalchemy import text
from sqlalchemy.orm import Session as SASession

from app.db import engine

_DEFAULT_XLSX = r"C:\Users\bened\OneDrive\Desktop\Finssentials - Setup\CF Mapping.xlsx"
_DEFAULT_SHEET = "Tabelle1"

# Workbook column → library field.
_WB_COLS = {
    "na":          "NA",
    "na_desc":     "NA Description",
    "l1":          "L11 CF 1",
    "l2":          "L11 CF 1.2",
    "l2_sort":     "L11 CF 1.2 sort",
    "l3":          "L11 CF 1.3",
    "l3_sort":     "L11 CF 1.3 sort",
    "l4":          "L12 CF 2",
    "l5":          "L13 CF 3",
    "cf_mapping":  "CF Mapping",
}

# ── DERIVED P&L level_3 → CF leaf (confirmed by financial-calculation-engineer) ──
# EBITDA = running Σ of every P&L mapping line ABOVE the EBITDA calc row, so the
# six EBITDA-band level_3 values map to the 'EBITDA' leaf and reconcile by
# construction to the P&L statement EBITDA.  "Other taxes" is its OWN dedicated
# operating leaf placed AFTER Gross cash flow (NOT in the EBITDA/Taxes bands) so the
# Net cash flow ties to ΔCash while CF EBITDA and Gross cash flow are unchanged —
# see module docstring.
_PL_LEVEL3_TO_CF: dict[str, dict[str, object]] = {
    # --- EBITDA build-up (above the EBITDA calc row) ---
    "Net sales":                  {"cf_mapping": "EBITDA", "l1": "EBITDA", "l2": "Gross cash flow"},
    "Δ Finished goods & WIP":     {"cf_mapping": "EBITDA", "l1": "EBITDA", "l2": "Gross cash flow"},
    "Own work capitalised":       {"cf_mapping": "EBITDA", "l1": "EBITDA", "l2": "Gross cash flow"},
    "Cost of materials":          {"cf_mapping": "EBITDA", "l1": "EBITDA", "l2": "Gross cash flow"},
    "Personnel expenses":         {"cf_mapping": "EBITDA", "l1": "EBITDA", "l2": "Gross cash flow"},
    "Other operating income":     {"cf_mapping": "EBITDA", "l1": "EBITDA", "l2": "Gross cash flow"},
    "Other operating expenses":   {"cf_mapping": "EBITDA", "l1": "EBITDA", "l2": "Gross cash flow"},
    # --- Taxes on income (operating leaf) ---
    "Taxes on income":            {"cf_mapping": "Taxes on income", "l1": "Taxes on income",
                                   "l2": "Gross cash flow"},
    # --- Depreciation & amortisation (investing leaf) ---
    "Depreciation & amortisation": {"cf_mapping": "Depreciation & amortisation",
                                    "l1": "Depreciation & amortisation",
                                    "l2": "Depreciation & amortisation"},
    # --- Financial result (financing leaf) ---
    "Interest income":            {"cf_mapping": "Financial result",
                                   "l1": "Cash flow from financing activities", "l2": "Financial result"},
    "Income from investments":    {"cf_mapping": "Financial result",
                                   "l1": "Cash flow from financing activities", "l2": "Financial result"},
    "Interest expenses":          {"cf_mapping": "Financial result",
                                   "l1": "Cash flow from financing activities", "l2": "Financial result"},
    "Write-offs on financial assets": {"cf_mapping": "Financial result",
                                       "l1": "Cash flow from financing activities", "l2": "Financial result"},
    # --- Other taxes (own leaf, DIRECTLY AFTER Taxes on income → INSIDE Gross CF) --
    # Own cf_mapping leaf → NOT in the EBITDA leaf set, so CF EBITDA == P&L EBITDA
    # stays locked.  The CF structure places CF_OTHER_TAXES directly AFTER "Taxes on
    # income" (BEFORE the "Gross cash flow" subtotal), so it is now PART of the Gross
    # cash flow build-up: Gross cash flow == EBITDA + Taxes on income + Other taxes.
    # It still flows into CFO / Net cash flow, so the indirect CF ties to ΔCash
    # (only its position moved; the Net total is unchanged).  l1/l2 mirror the EBITDA
    # / Taxes-on-income leaves: l2='Gross cash flow' groups it under Gross cash flow
    # in the drill / L4-trend; l1='Other taxes' keeps its own leaf label.  key='Other
    # taxes' catches KFZ-Steuer (level_3='Other taxes') via the level_3 pass and
    # Grundsteuer/Grundbesitzabgaben (level_2='Other taxes', level_3='Other') via the
    # level_2 fallback in etl.cf_fill; level_3='Other' under OTHER level_2 categories
    # does NOT match (the key is 'Other taxes', not 'Other').
    "Other taxes":                {"cf_mapping": "Other taxes",
                                   "l1": "Other taxes",
                                   "l2": "Gross cash flow"},
}

# ── NA gap-fillers (derived BY PRECEDENT from rows already in the workbook) ──────
# The workbook is keyed by (NA, NA Description) but a couple of real dim_gl_na
# classifications have no workbook row.  These are NOT invented: each reuses the CF
# columns of an EXISTING workbook row that shares either the NA category or the
# exact description, so the matched ``cf_mapping`` leaf is the workbook's own.
#   * ('Equity','Profit distribution'): every other Equity NA in the workbook maps
#     to '∆ Equity' (financing) → follow the same Equity rule.
#   * ('ND','Provisions for onerous contracts'): the workbook already maps
#     ('OWC','Provisions for onerous contracts') → reuse that row verbatim (the
#     ND vs OWC category does not change the cf_mapping leaf).
# Loaded with source='na_gap_precedent' so they are visible/auditable; remove an
# entry once the workbook itself is extended.
_NA_GAP_FILLERS: dict[tuple[str, str], dict] = {
    ("Equity", "Profit distribution"): {
        "l1": "Cash flow from financing activities", "l2": "∆ Equity", "l2_sort": 6,
        "l3": "Cash flow from financing activities", "l3_sort": 3,
        "l4": "∆ Equity", "l5": "∆ Profit distribution", "cf_mapping": "∆ Equity",
    },
    ("ND", "Provisions for onerous contracts"): {
        "l1": "∆ Other working capital", "l2": "∆ Net working capital", "l2_sort": 2,
        "l3": "Cash flow from operating activities", "l3_sort": 1,
        "l4": "∆ Provisions & accruals", "l5": "∆ Other provision & accruals",
        "cf_mapping": "Δ Provisions for onerous contracts",
    },
}


def _clean(v: object) -> object:
    """NaN/empty → None; ints stay ints; everything else str-stripped."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, str):
        s = v.strip()
        return s or None
    return v


def _clean_int(v: object) -> object:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _upsert(session: SASession, key_kind: str, key_1: str, key_2: str,
            rec: dict, source: str) -> None:
    session.execute(
        text("""
            INSERT INTO lib_cf_mapping
              (key_kind, key_1, key_2, l1, l2, l3, l4, l5, cf_mapping,
               l2_sort, l3_sort, source, updated_at)
            VALUES
              (:kk, :k1, :k2, :l1, :l2, :l3, :l4, :l5, :cfm, :l2s, :l3s, :src, NOW())
            ON CONFLICT (key_kind, key_1, key_2) DO UPDATE SET
              l1 = EXCLUDED.l1, l2 = EXCLUDED.l2, l3 = EXCLUDED.l3,
              l4 = EXCLUDED.l4, l5 = EXCLUDED.l5, cf_mapping = EXCLUDED.cf_mapping,
              l2_sort = EXCLUDED.l2_sort, l3_sort = EXCLUDED.l3_sort,
              source = EXCLUDED.source, updated_at = NOW()
        """),
        {
            "kk": key_kind, "k1": key_1, "k2": key_2,
            "l1": rec.get("l1"), "l2": rec.get("l2"), "l3": rec.get("l3"),
            "l4": rec.get("l4"), "l5": rec.get("l5"), "cfm": rec.get("cf_mapping"),
            "l2s": rec.get("l2_sort"), "l3s": rec.get("l3_sort"),
            "src": source,
        },
    )


def load_na_side(session: SASession, xlsx: str, sheet: str) -> dict:
    """Load the BS/NA side from the workbook into lib_cf_mapping (key_kind='na')."""
    df = pd.read_excel(xlsx, sheet_name=sheet, header=0)
    source = Path(xlsx).name

    # Resolve each row to a canonical record keyed by (NA, NA Description).
    chosen: dict[tuple[str, str], dict] = {}
    conflicts: list[str] = []

    def _rec(row) -> dict:
        return {
            "l1":         _clean(row.get(_WB_COLS["l1"])),
            "l2":         _clean(row.get(_WB_COLS["l2"])),
            "l3":         _clean(row.get(_WB_COLS["l3"])),
            "l4":         _clean(row.get(_WB_COLS["l4"])),
            "l5":         _clean(row.get(_WB_COLS["l5"])),
            "cf_mapping": _clean(row.get(_WB_COLS["cf_mapping"])),
            "l2_sort":    _clean_int(row.get(_WB_COLS["l2_sort"])),
            "l3_sort":    _clean_int(row.get(_WB_COLS["l3_sort"])),
        }

    def _cf_cols(r: dict) -> tuple:
        return (r["l1"], r["l2"], r["l3"], r["l4"], r["l5"], r["cf_mapping"])

    for _, row in df.iterrows():
        na = _clean(row.get(_WB_COLS["na"]))
        desc = _clean(row.get(_WB_COLS["na_desc"]))
        if na is None or desc is None:
            continue
        key = (str(na), str(desc))
        rec = _rec(row)
        if key not in chosen:
            chosen[key] = rec
            continue
        # Conflict resolution.
        prev = chosen[key]
        if _cf_cols(prev) == _cf_cols(rec):
            continue  # exact duplicate — benign
        # Deterministic: keep the row whose L13 CF 3 (l5) sorts first; log the loser.
        cand = sorted([prev, rec], key=lambda r: (str(r["l5"] or ""), str(r["l4"] or "")))
        winner, loser = cand[0], cand[1]
        chosen[key] = winner
        conflicts.append(
            f"  CONFLICT {key}: kept l5={winner['l5']!r}/l4={winner['l4']!r} "
            f"cf={winner['cf_mapping']!r}; dropped l5={loser['l5']!r}/l4={loser['l4']!r} "
            f"cf={loser['cf_mapping']!r}"
        )

    if conflicts:
        print(f"[na] {len(conflicts)} ambiguous (NA, NA Description) key(s) disambiguated:")
        for c in conflicts:
            print(c)

    for (na, desc), rec in chosen.items():
        _upsert(session, "na", na, desc, rec, source)

    # Gap-fillers derived by workbook precedent (only those not already present).
    gaps = 0
    for (na, desc), rec in _NA_GAP_FILLERS.items():
        if (na, desc) in chosen:
            continue  # the workbook now covers it — prefer the workbook row
        _upsert(session, "na", na, desc, rec, "na_gap_precedent")
        gaps += 1
    if gaps:
        print(f"[na] {gaps} gap-filler row(s) added by workbook precedent "
              f"(source='na_gap_precedent').")

    return {"rows": len(chosen) + gaps, "conflicts": len(conflicts)}


def load_pl_side(session: SASession) -> dict:
    """Seed the derived P&L level_3 → CF leaf rows (key_kind='pl_level3')."""
    for level3, rec in _PL_LEVEL3_TO_CF.items():
        full = {"l1": rec.get("l1"), "l2": rec.get("l2"), "l3": rec.get("cf_mapping"),
                "l4": None, "l5": None, "cf_mapping": rec.get("cf_mapping"),
                "l2_sort": None, "l3_sort": None}
        _upsert(session, "pl_level3", "PL", level3, full, "pl_structure_derived")
    return {"rows": len(_PL_LEVEL3_TO_CF)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed/extend lib_cf_mapping.")
    parser.add_argument("--xlsx", default=_DEFAULT_XLSX)
    parser.add_argument("--sheet", default=_DEFAULT_SHEET)
    parser.add_argument("--skip-na", action="store_true", help="skip the workbook/NA side")
    parser.add_argument("--skip-pl", action="store_true", help="skip the derived P&L side")
    args = parser.parse_args()

    print(f"XLSX : {args.xlsx}  (sheet {args.sheet!r})")
    with SASession(engine) as session:
        na_res = {"rows": 0, "conflicts": 0}
        pl_res = {"rows": 0}
        if not args.skip_na:
            na_res = load_na_side(session, args.xlsx, args.sheet)
        if not args.skip_pl:
            pl_res = load_pl_side(session)
        session.commit()
        total = session.execute(text("SELECT COUNT(*) FROM lib_cf_mapping")).scalar()

    print(f"[na] {na_res['rows']} NA rows upserted ({na_res['conflicts']} conflicts resolved)")
    print(f"[pl] {pl_res['rows']} P&L level_3 rows upserted")
    print(f"lib_cf_mapping now holds {total} rows total.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
