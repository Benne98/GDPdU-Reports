"""Seed the Cash Flow statement structure into dim_cf_structure (CF rows).

NOTE (migration 0030): the CF rows now live in their OWN table ``dim_cf_structure``
(physically split out of ``dim_pl_structure``).  The INSERTs below target
``dim_cf_structure``; the historical "Option B" decision block is kept for context
but is SUPERSEDED by the split — CF no longer shares the P&L table.

═══════════════════════════════════════════════════════════════════════════════
ARCHITECTURAL DECISION (SUPERSEDED by 0030): CF rows were tagged kpi_code 'CF:%'
in dim_pl_structure; migration 0030 moved them to dim_cf_structure.
═══════════════════════════════════════════════════════════════════════════════
Two storage options were evaluated:

  Option A: separate cf_structure table.
    Pro:  clean separation from P&L.
    Con:  requires a schema migration; no existing CF-statement builder reads it;
          duplicates the row_type / line_code / sort_order machinery already in
          dim_pl_structure.

  Option B: rows in dim_pl_structure tagged kpi_code LIKE 'CF:%'.
    Pro:  zero migration (table already exists); consistent with the BS-rows
          pattern (kpi_code LIKE 'BS:%') that seed_bs_structure.py already uses;
          any future CF-statement builder can reuse fetch_structure / fetch_bs_structure
          pattern unchanged.
    Con:  dim_pl_structure now serves three statement types (P&L / BS / CF);
          mitigated by the kpi_code tag which makes filtering trivial.

DECISION: Option B — tag CF rows with kpi_code = 'CF:<section>'.

Sort-order band: 2000+ (P&L occupies 1–99; BS 1000–1999; CF starts at 2000).
This avoids the UNIQUE sort_order collision with existing rows.

═══════════════════════════════════════════════════════════════════════════════
CF STRUCTURE SHEET → dim_pl_structure MAPPING
═══════════════════════════════════════════════════════════════════════════════
Source sheet "CF Structure" (header row 0) columns:
  Balance title   → balance_title
  Sort            → sort_order (offset by _CF_SORT_BASE = 2000)
  Calc type       → row_type: 1 → 'mapping', 2 → 'subtotal'/'calc'
  Details         → details
  Dynamic name    → used to build line_code (same logic as seed_pl_structure)

kpi_code assignment (based on CF-standard three-section hierarchy):
  Rows that are SUBTOTALS / SECTION HEADERS for operating activities  → 'CF:op'
  Rows that are SUBTOTALS / SECTION HEADERS for investing activities  → 'CF:inv'
  Rows that are SUBTOTALS / SECTION HEADERS for financing activities  → 'CF:fin'
  Net change row (total cash movement)                                → 'CF:total'
  Detail mapping rows (Calc type = 1)                                 → 'CF:detail'

The section is inferred from the row's position in the sheet (Sort order) relative
to known section-header titles.  Titles containing the keywords below trigger a
section change:
  'operating' / 'laufende' / 'geschäfts'  → section 'op'
  'investing'  / 'investitions'            → section 'inv'
  'financing'  / 'finanzierungs'           → section 'fin'
  'net'        / 'gesamt' / 'change'       → section 'total' (final summary row)

Fallback: if none of the section keywords match, the row inherits the last known
section (or 'op' if the sheet starts without a header row).

Idempotency: ON CONFLICT (line_code) DO UPDATE.

Usage:
  cd C:\\Users\\bened\\OneDrive\\Finssentials\\Finssentials_GDPDU
  $env:DB_PASSWORD = "..."
  .\\backend\\.venv\\Scripts\\python.exe backend/scripts/seed_cf_structure.py
  # or with explicit xlsx path:
  .\\backend\\.venv\\Scripts\\python.exe backend/scripts/seed_cf_structure.py \\
      --xlsx "C:/path/to/Account_Mapping.xlsx"
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from sqlalchemy import text
from sqlalchemy.orm import Session as SASession

from app.db import engine

_DEFAULT_XLSX = (
    r"C:\Users\bened\OneDrive\Desktop\Project SELFMADE\Cursor"
    r"\analytics-layer\reference\decidra\Account_Mapping.xlsx"
)

_CF_SORT_BASE = 2000  # P&L: 1-99, BS: 1000-1999, CF: 2000+

# Keywords that identify a CF section header (case-insensitive)
_SECTION_KEYWORDS: dict[str, str] = {
    "op":    r"operat|laufend|geschäfts|betrieblich|betrieb",
    "inv":   r"invest",
    "fin":   r"financ|finanzier",
    "total": r"net\s+change|net\s+movement|cash\s+and\s+cash|change\s+in\s+cash|"
             r"gesamt|veränderung\s+der",
}

# ── Code-defined supplemental CF rows (NOT in the external CF Structure sheet) ──
# Some CF leaves are required for the statement to RECONCILE but are absent from the
# curated "CF Structure" sheet.  They are re-created here on every (re-)seed AND on
# every rebuild (etl.rebuild._stage_structure_recon_refresh) via
# ``seed_cf_supplemental_rows`` so any project — even one seeded from a sheet that
# predates them — carries the row.  Derived-by-precedent, same spirit as
# load_cf_mapping_library._NA_GAP_FILLERS.
#
#   * 'Other taxes' — a detail leaf placed DIRECTLY AFTER "Taxes on income" and
#     BEFORE the "Gross cash flow" subtotal, so it is PART of the Gross cash flow
#     build-up (product decision).  It carries its OWN cf_mapping ('Other taxes'), so
#     CF EBITDA == P&L EBITDA stays LOCKED (Other taxes ∉ the EBITDA leaf set); the
#     Gross cash flow subtotal now equals EBITDA + Taxes on income + Other taxes (the
#     previous "EBITDA + Taxes" identity is intentionally superseded); and Net cash
#     flow still Σ's every non-cash leaf so it ties to ΔCash (only the leaf's POSITION
#     moved).  details=0 keeps it top-level (not nested under a cluster subtotal).
#     Placement is anchored to the "Taxes on income" row, so it lands just after it
#     regardless of the sheet's numbering.
_CF_SUPPLEMENTAL_ROWS: list[dict] = [
    {
        "line_code": "CF_OTHER_TAXES",
        "balance_title": "Other taxes",
        "row_type": "mapping",
        "kpi_code": "CF:detail",
        "details": 0,
        "calc_type": 1,
        "is_bold": False,
    },
]

# Known KPI codes for subtotal rows (mirrors seed_pl_structure pattern)
_KPI_BY_TITLE: dict[str, str] = {
    # normalised title (lower): kpi_code override
    "free cash flow":        "CF_FREE_CASH_FLOW",
    "operating cash flow":   "CF_OP",
    "cash flow from investing activities": "CF_INV",
    "cash flow from financing activities": "CF_FIN",
}


def _make_cf_line_code(dynamic_name: str, balance_title: str, sort: int) -> str:
    """Build a stable line_code for a CF structure row (CF_ prefix)."""
    src = (dynamic_name or balance_title or "").strip()
    src = re.sub(r"^[⠀-⣿\s]+", "", src).strip()
    if not src:
        src = f"CF_LINE_{sort}"
    code = re.sub(r"[^A-Za-z0-9_]", "_", src.upper())
    code = re.sub(r"_+", "_", code).strip("_")
    # Ensure CF_ prefix for namespace isolation from P&L codes
    if not code.startswith("CF_"):
        code = f"CF_{code}"
    return code[:64]


def _infer_section(title: str, current_section: str) -> str:
    """Infer CF section from balance_title; return current_section if no keyword matches."""
    t = title.lower()
    for section, pattern in _SECTION_KEYWORDS.items():
        if re.search(pattern, t):
            return section
    return current_section


def _make_kpi_code(
    balance_title: str,
    row_type: str,
    section: str,
) -> str:
    """Determine kpi_code for a dim_pl_structure row.

    - calc / subtotal rows: check _KPI_BY_TITLE for named overrides; else 'CF:<section>'
    - mapping / detail rows: 'CF:detail' (tags them CF without assigning a section KPI)
    """
    if row_type == "mapping":
        return "CF:detail"
    key = balance_title.strip().lower()
    if key in _KPI_BY_TITLE:
        return _KPI_BY_TITLE[key]
    return f"CF:{section}"


def seed_cf_supplemental_rows(session: SASession) -> int:
    """Idempotently add / re-position code-defined CF rows absent from the CF sheet.

    Currently just 'Other taxes' — a detail leaf placed DIRECTLY AFTER the "Taxes on
    income" mapping row (i.e. BEFORE the "Gross cash flow" subtotal), so it is PART of
    the Gross cash flow build-up: ``Gross cash flow == EBITDA + Taxes on income +
    Other taxes``.  ``CF EBITDA == P&L EBITDA`` stays LOCKED (the leaf is its own
    cf_mapping, not in the EBITDA leaf set) and Net cash flow still Σ's every non-cash
    leaf, so it ties to ΔCash (only the leaf's POSITION moved).

    RE-POSITIONING (not just insert): the row may already exist at a STALE position
    (e.g. an earlier build placed it before CFO).  If it is not already the immediate
    successor of "Taxes on income", it is DELETED and re-inserted at the correct
    anchor via :func:`structure_autoextend._compute_sort_order` (handles the no-gap
    shift collision-free).  Idempotent: a second run detects it is already correctly
    placed and only refreshes presentation attrs (no sort drift).  Returns rows
    (re-)inserted.  Does NOT commit (the caller owns the transaction).
    """
    # Reuse the tested sort_order allocator (handles the no-gap shift collision-free).
    from app.services.structure_autoextend import _compute_sort_order

    anchor = session.execute(text(
        "SELECT sort_order, line_code FROM dim_cf_structure "
        "WHERE row_type = 'mapping' AND balance_title ILIKE '%taxes on income%' "
        "ORDER BY sort_order LIMIT 1"
    )).fetchone()
    if anchor is None:
        print("  [WARN] no 'Taxes on income' row — supplemental CF rows skipped.")
        return 0
    anchor_sort = int(anchor[0])
    anchor_lc = str(anchor[1])

    inserted = 0
    for spec in _CF_SUPPLEMENTAL_ROWS:
        lc = spec["line_code"]
        exists = session.execute(
            text("SELECT 1 FROM dim_cf_structure WHERE line_code = :lc"), {"lc": lc}
        ).fetchone()
        # Correctly placed iff it is the immediate successor of the anchor row.
        succ = session.execute(
            text("SELECT line_code FROM dim_cf_structure WHERE sort_order > :s "
                 "ORDER BY sort_order LIMIT 1"),
            {"s": anchor_sort},
        ).fetchone()
        already_ok = exists is not None and succ is not None and str(succ[0]) == lc

        if already_ok:
            # Refresh presentation attrs only; keep placement (no sort drift).
            session.execute(
                text(
                    "UPDATE dim_cf_structure SET row_type = :rt, balance_title = :bt, "
                    "kpi_code = :kpi, details = :det, calc_type = :ct, is_bold = :bold "
                    "WHERE line_code = :lc"
                ),
                {"rt": spec["row_type"], "bt": spec["balance_title"], "kpi": spec["kpi_code"],
                 "det": spec["details"], "ct": spec["calc_type"], "bold": spec["is_bold"],
                 "lc": lc},
            )
            continue

        # Present but mis-placed (stale position) → delete and re-insert at the anchor.
        if exists is not None:
            session.execute(
                text("DELETE FROM dim_cf_structure WHERE line_code = :lc"), {"lc": lc}
            )
        sort_order = _compute_sort_order(session, "dim_cf_structure", "CF", anchor_lc)
        session.execute(
            text(
                "INSERT INTO dim_cf_structure "
                "(sort_order, line_code, row_type, balance_title, details, calc_type, "
                " kpi_code, is_bold) "
                "VALUES (:so, :lc, :rt, :bt, :det, :ct, :kpi, :bold)"
            ),
            {"so": sort_order, "lc": lc, "rt": spec["row_type"], "bt": spec["balance_title"],
             "det": spec["details"], "ct": spec["calc_type"], "kpi": spec["kpi_code"],
             "bold": spec["is_bold"]},
        )
        inserted += 1
    if inserted:
        print(f"  [supplemental] {inserted} code-defined CF row(s) (re-)inserted "
              f"(e.g. CF_OTHER_TAXES directly after 'Taxes on income').")
    return inserted


def seed_cf_structure(xlsx_path: str | Path, session: SASession) -> int:
    """Read CF Structure sheet, upsert into dim_cf_structure. Returns rows upserted."""
    try:
        df = pd.read_excel(str(xlsx_path), sheet_name="CF Structure", header=0)
    except Exception as exc:
        print(f"  [WARN] Could not read 'CF Structure' sheet: {exc}")
        print("  Falling back to synthetic CF structure derived from dim_gl_cf hierarchy.")
        return _seed_cf_from_dim_gl_cf(session)

    upserted = 0
    current_section = "op"  # default: operating activities

    for _, row in df.iterrows():
        sort_raw = row.get("Sort")
        if pd.isna(sort_raw):
            continue
        sort_order = _CF_SORT_BASE + int(sort_raw)

        balance_title = str(row.get("Balance title", "") or "").strip()
        if not balance_title:
            continue

        calc_type_raw = row.get("Calc type")
        calc_type = int(calc_type_raw) if pd.notna(calc_type_raw) else 1

        dynamic_name = str(row.get("Dynamic name", "") or "").strip()
        details_raw = row.get("Details")
        details = int(details_raw) if pd.notna(details_raw) else None

        line_code = _make_cf_line_code(dynamic_name, balance_title, int(sort_raw))

        # Determine row_type: 1=mapping detail, 2=subtotal/calc
        if calc_type == 1:
            row_type = "mapping"
        else:
            title_key = balance_title.lower().strip()
            row_type = "calc" if title_key in _KPI_BY_TITLE else "subtotal"

        # Infer section (section header rows are typically calc_type=2)
        new_section = _infer_section(balance_title, current_section)
        if new_section != current_section:
            current_section = new_section

        kpi_code = _make_kpi_code(balance_title, row_type, current_section)
        is_bold = calc_type == 2

        session.execute(
            text("""
                INSERT INTO dim_cf_structure
                  (sort_order, line_code, row_type, balance_title,
                   details, calc_type, kpi_code, is_bold)
                VALUES (:so, :lc, :rt, :bt, :det, :ct, :kpi, :bold)
                ON CONFLICT (line_code) DO UPDATE SET
                  sort_order    = EXCLUDED.sort_order,
                  row_type      = EXCLUDED.row_type,
                  balance_title = EXCLUDED.balance_title,
                  details       = EXCLUDED.details,
                  calc_type     = EXCLUDED.calc_type,
                  kpi_code      = EXCLUDED.kpi_code,
                  is_bold       = EXCLUDED.is_bold
            """),
            {
                "so": sort_order,
                "lc": line_code,
                "rt": row_type,
                "bt": balance_title,
                "det": details,
                "ct": calc_type,
                "kpi": kpi_code,
                "bold": is_bold,
            },
        )
        upserted += 1

    upserted += seed_cf_supplemental_rows(session)
    session.commit()
    return upserted


def _seed_cf_from_dim_gl_cf(session: SASession) -> int:
    """Fallback: build a minimal CF structure from the l1/l2/l3 hierarchy in dim_gl_cf.

    Used when the 'CF Structure' sheet is absent from the workbook.  Generates one
    'mapping' dim_pl_structure row per distinct (l1, l2, l3) path, ordered by
    (l1, l2, l3), tagged kpi_code = 'CF:detail'.  No subtotal rows are generated
    (a future CF-statement builder can derive them on the fly from the mapping rows).
    """
    rows = session.execute(
        text("""
            SELECT DISTINCT l1, l2, l3, cf_mapping
            FROM dim_gl_cf
            WHERE l1 IS NOT NULL
            ORDER BY l1, l2, l3 NULLS LAST
        """)
    ).fetchall()

    if not rows:
        print("  [WARN] dim_gl_cf is empty — run load_cf_mapping.py first.")
        return 0

    upserted = 0
    sort_counter = _CF_SORT_BASE

    for l1, l2, l3, cf_mapping in rows:
        sort_counter += 10
        label = " / ".join(filter(None, [l1, l2, l3])) or cf_mapping or f"CF_{sort_counter}"
        code = re.sub(r"[^A-Za-z0-9_]", "_", label.upper())
        code = re.sub(r"_+", "_", code).strip("_")
        line_code = f"CF_{code}"[:64]
        section = _infer_section(str(l1 or ""), "op")

        session.execute(
            text("""
                INSERT INTO dim_cf_structure
                  (sort_order, line_code, row_type, balance_title, calc_type, kpi_code)
                VALUES (:so, :lc, 'mapping', :bt, 1, :kpi)
                ON CONFLICT (line_code) DO UPDATE SET
                  sort_order    = EXCLUDED.sort_order,
                  balance_title = EXCLUDED.balance_title,
                  kpi_code      = EXCLUDED.kpi_code
            """),
            {
                "so": sort_counter,
                "lc": line_code,
                "bt": label,
                "kpi": f"CF:{section}",
            },
        )
        upserted += 1

    upserted += seed_cf_supplemental_rows(session)
    session.commit()
    return upserted


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed CF structure into dim_cf_structure")
    parser.add_argument(
        "--xlsx",
        default=_DEFAULT_XLSX,
        help="Path to Account_Mapping.xlsx (must contain 'CF Structure' sheet)",
    )
    args = parser.parse_args()

    print(f"XLSX: {args.xlsx}")
    with SASession(engine) as session:
        # Confirm there are no conflicting CF rows already (informational only)
        existing = session.execute(
            text("SELECT COUNT(*) FROM dim_cf_structure")
        ).scalar() or 0
        print(f"Existing CF rows in dim_cf_structure: {existing}")

        n = seed_cf_structure(args.xlsx, session)

    print(f"\nSummary: {n} CF rows upserted into dim_cf_structure.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
