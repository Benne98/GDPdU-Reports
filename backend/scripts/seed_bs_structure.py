"""Seed the Balance-Sheet report structure into dim_bs_structure (BS rows).

NOTE (migration 0030): BS rows now live in their OWN table ``dim_bs_structure``
(physically split out of ``dim_pl_structure``).  The INSERT below targets
``dim_bs_structure``; references to dim_pl_structure in the notes describe the
pre-split layout.

There is NO "BS Structure" sheet in the Decidra workbook, so the BS report
structure is DERIVED from the live GL balance-sheet hierarchy in dim_gl_account
(level_0 = 'BS').  This mirrors what seed_pl_structure.py does for the P&L, but the
source of truth is the GL hierarchy itself rather than a spreadsheet.

============================================================ WHAT IS GENERATED
Walking the distinct (level_1, level_2, level_3) tuples of the BS accounts, ordered
by (level_2_sort, level_3_sort), we emit, per L1 side, in order:

  • one 'mapping' row per L3 category          → wired by ``level_3 = <L3 value>``
       (so aggregate_bs._matches aggregates exactly the GL accounts of that L3),
  • one 'subtotal' row per L2 group            → "Total <L2>" = Σ its L3 mapping rows,
  • one 'grandtotal' row per L1 side           → "Total assets" / "Total equity &
       liabilities" = Σ ALL that side's L3 mapping rows (= Σ its L2 subtotals).

Sign / section convention (see balance_sheet._present_bs — the single place sign is
applied):  ASSETS keep the stored sign (section 'asset', presented +amount);
EQUITY & LIABILITIES are credit-side (section 'credit', presented −amount → +).  We
put the whole "Equity & liabilities" L1 into ONE section ('credit') so the
accounting identity is simply  imbalance = Total assets − Total equity&liabilities.

============================================================ PERSISTENCE (dim_bs_structure, BS rows)
BS rows live in the dedicated dim_bs_structure table (split out of dim_pl_structure
by migration 0030); fetch_bs_structure reads that table.  Convention (unchanged):
  kpi_code      = 'BS:asset' | 'BS:credit'   (carries the section AND tags the row BS)
  row_type      = 'mapping' | 'subtotal' | 'grandtotal'
  level_3       = the GL L3 value  (mapping rows only; NULL on subtotals/grandtotals)
  level_2       = the GL L2 value  (carried on all rows for drill / grouping)
  sort_order    = 1000 + running index  (BS occupies a sort band ABOVE the P&L rows;
                  sort_order is globally UNIQUE in the table, so the bands must not
                  overlap — P&L uses the low ~1..30 band).

Line codes for the four WC/CF-critical balances are fixed ALIASES so the working
capital + cash-flow services (which key on AR / INVENTORY / AP / CASH / EQUITY) pick
up real values without any per-call wiring:
  'Trade receivables'        → AR
  'Inventories'              → INVENTORY
  'Trade payables'           → AP
  'Cash & cash equivalents'  → CASH
  'Retained earnings'        → EQUITY   (the only equity L3 in this GL; CF's
                                          contributed-equity proxy)
Every other L3 gets a derived, BS_-prefixed code (collision-free vs P&L codes).

EXPECTED IMBALANCE (honest, NOT hidden): the reference GL is GL-only — it does NOT
carry opening balances or the retained-earnings roll-forward of the P&L result — so
Total assets and Total equity&liabilities do NOT net to zero.  ``imbalance`` per
column surfaces the residual (the frontend shows an amber note); this is correct
behaviour for GL-only data, not a bug.

Usage (needs DB_PASSWORD env var):
  cd C:\\Users\\bened\\OneDrive\\Finssentials\\Finssentials_GDPDU
  $env:DB_PASSWORD = "..."
  .\\backend\\.venv\\Scripts\\python.exe backend/scripts/seed_bs_structure.py
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Add backend/ to sys.path so app.* imports work.
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# BS sort band starts here (must sit ABOVE the P&L band; sort_order is globally UNIQUE).
_BS_SORT_BASE = 1000

# Fixed line-code aliases so working_capital.py / cash_flow.py (keyed on these codes)
# pick up the real GL balances.  Keys are matched case-insensitively against L3.
_L3_CODE_ALIASES: dict[str, str] = {
    "trade receivables": "AR",
    "inventories": "INVENTORY",
    "trade payables": "AP",
    "cash & cash equivalents": "CASH",
    "cash and cash equivalents": "CASH",
    "retained earnings": "EQUITY",
}


def _section_for_level_1(level_1: str) -> str:
    """Map the GL L1 side to a BS section/sign.

    'Assets' → 'asset' (debit, presented +).  Anything else (Equity & liabilities)
    → 'credit' (credit side, presented −amount → +).  Matching is lenient (prefix /
    keyword) so minor label variants ('Assets', 'Equity & liabilities') are handled.
    """
    l = (level_1 or "").strip().lower()
    if l.startswith("asset"):
        return "asset"
    return "credit"


def _derive_code(level_3: str) -> str:
    """Stable, collision-free line_code for a non-aliased L3 (BS_ prefix)."""
    src = (level_3 or "").strip().upper()
    code = re.sub(r"[^A-Z0-9_]", "_", src)
    code = re.sub(r"_+", "_", code).strip("_")
    return f"BS_{code}"[:64]


def _code_for_l3(level_3: str) -> str:
    return _L3_CODE_ALIASES.get((level_3 or "").strip().lower(), _derive_code(level_3))


def _total_code(prefix: str, level_2_or_1: str) -> str:
    src = (level_2_or_1 or "").strip().upper()
    code = re.sub(r"[^A-Z0-9_]", "_", src)
    code = re.sub(r"_+", "_", code).strip("_")
    return f"{prefix}_{code}"[:64]


@dataclass
class _Row:
    sort_order: int
    line_code: str
    row_type: str            # 'mapping' | 'subtotal' | 'grandtotal'
    balance_title: str
    section: str             # 'asset' | 'credit'
    level_2: Optional[str]
    level_3: Optional[str]
    is_bold: bool


def fetch_bs_hierarchy(session) -> list[tuple[str, str, str, float, float]]:
    """Distinct (level_1, level_2, level_3, level_2_sort, level_3_sort) of BS accounts.

    Ordered by (level_2_sort, level_3_sort) so the emitted structure matches the GL's
    intended presentation order.  NULL sort keys sort last (coalesced to a big number).
    """
    from sqlalchemy import text

    rows = session.execute(
        text(
            """
            SELECT DISTINCT level_1, level_2, level_3,
                   COALESCE(level_2_sort, 9e9) AS l2s,
                   COALESCE(level_3_sort, 9e9) AS l3s
            FROM dim_gl_account
            WHERE level_0 = 'BS' AND level_3 IS NOT NULL
            ORDER BY l2s, l3s, level_3
            """
        )
    ).fetchall()
    return [(r[0], r[1], r[2], float(r[3]), float(r[4])) for r in rows]


def build_bs_rows(
    hierarchy: list[tuple[str, str, str, float, float]],
    occupied: Optional[set[int]] = None,
) -> list[_Row]:
    """Pure: turn the ordered BS L1/L2/L3 hierarchy into structure rows.

    Emits, per L2 group: its L3 mapping rows then a "Total <L2>" subtotal; and, when
    the L1 side changes (or at the end), a "Total assets" / "Total equity &
    liabilities" grandtotal closing that side.  Order is preserved from the input.

    ``occupied`` is the set of ``sort_order`` values already taken by SURVIVING
    ``source='auto_extend'`` rows (which the re-seed does NOT delete).  ``nxt`` skips
    them so a freshly re-seeded row can never collide with an auto_extend row on the
    UNIQUE(sort_order) constraint after a CoA round-trip.
    """
    occupied = occupied or set()
    rows: list[_Row] = []
    sort = _BS_SORT_BASE

    def nxt() -> int:
        nonlocal sort
        sort += 10
        while sort in occupied:
            sort += 10
        return sort

    cur_l1: Optional[str] = None
    cur_l2: Optional[str] = None

    def close_l2(level_2: Optional[str], section: str) -> None:
        if level_2 is None:
            return
        rows.append(_Row(
            nxt(), _total_code("BS_TOTAL", level_2), "subtotal",
            f"Total {level_2.strip().lower()}", section, level_2, None, True,
        ))

    def close_l1(level_1: Optional[str], section: str) -> None:
        if level_1 is None:
            return
        title = "Total assets" if section == "asset" else "Total equity & liabilities"
        rows.append(_Row(
            nxt(), _total_code("BS_GRANDTOTAL", level_1), "grandtotal",
            title, section, None, None, True,
        ))

    for level_1, level_2, level_3, _l2s, _l3s in hierarchy:
        section = _section_for_level_1(level_1)

        if cur_l2 is not None and level_2 != cur_l2:
            close_l2(cur_l2, _section_for_level_1(cur_l1))
        if cur_l1 is not None and level_1 != cur_l1:
            close_l1(cur_l1, _section_for_level_1(cur_l1))

        rows.append(_Row(
            nxt(), _code_for_l3(level_3), "mapping",
            level_3.strip(), section, level_2, level_3, False,
        ))
        cur_l1, cur_l2 = level_1, level_2

    # Close the final open L2 + L1.
    if cur_l2 is not None:
        close_l2(cur_l2, _section_for_level_1(cur_l1))
    if cur_l1 is not None:
        close_l1(cur_l1, _section_for_level_1(cur_l1))
    return rows


def _has_source_column(session) -> bool:
    """True when dim_bs_structure carries the Phase-5 ``source`` provenance column."""
    from sqlalchemy import text

    return session.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name='dim_bs_structure' AND column_name='source'"
        )
    ).fetchone() is not None


def seed_bs_structure(session) -> int:
    """Generate BS rows from dim_gl_account and (re-)seed them into dim_bs_structure.

    Returns the number of rows written.  Idempotent AFTER a CoA round-trip:

    ``dim_bs_structure`` carries TWO unique constraints — ``line_code`` AND
    ``sort_order``.  A plain ``ON CONFLICT (line_code) DO UPDATE`` re-seed can still
    raise ``UniqueViolation`` when a *different* line_code recomputes to a sort_order
    already occupied by another surviving row (the conflict target line_code can't
    catch it).  So we DELETE the previously-seeded rows first, keep any user-owned
    ``source='auto_extend'`` rows, and skip their sort_orders when numbering the fresh
    rows (see ``build_bs_rows(occupied=...)``).  The ON CONFLICT clause is retained as
    belt-and-suspenders for a line_code that also matches a surviving auto_extend row.
    """
    from sqlalchemy import text

    has_source = _has_source_column(session)

    # Delete the previously-seeded rows.  Preserve user-placed auto_extend rows only
    # when the provenance column exists (it only carries 'auto_extend' when present).
    if has_source:
        session.execute(
            text("DELETE FROM dim_bs_structure WHERE COALESCE(source, '') <> 'auto_extend'")
        )
        occupied = {
            int(r[0])
            for r in session.execute(
                text("SELECT sort_order FROM dim_bs_structure")
            ).fetchall()
        }
    else:
        session.execute(text("DELETE FROM dim_bs_structure"))
        occupied = set()

    hierarchy = fetch_bs_hierarchy(session)
    rows = build_bs_rows(hierarchy, occupied)

    upserted = 0
    for r in rows:
        kpi_code = f"BS:{r.section}"
        session.execute(
            text("""
                INSERT INTO dim_bs_structure
                  (sort_order, line_code, row_type, balance_title, calc_type,
                   kpi_code, level_2, level_3, is_bold)
                VALUES (:so, :lc, :rt, :bt, 1, :kpi, :l2, :l3, :bold)
                ON CONFLICT (line_code) DO UPDATE SET
                  sort_order    = EXCLUDED.sort_order,
                  row_type      = EXCLUDED.row_type,
                  balance_title = EXCLUDED.balance_title,
                  kpi_code      = EXCLUDED.kpi_code,
                  level_2       = EXCLUDED.level_2,
                  level_3       = EXCLUDED.level_3,
                  is_bold       = EXCLUDED.is_bold
            """),
            {
                "so": r.sort_order, "lc": r.line_code, "rt": r.row_type,
                "bt": r.balance_title, "kpi": kpi_code,
                "l2": r.level_2, "l3": r.level_3, "bold": r.is_bold,
            },
        )
        upserted += 1
    session.commit()
    return upserted


if __name__ == "__main__":
    from app.db import engine
    from sqlalchemy.orm import Session as SASession

    with SASession(engine) as session:
        n = seed_bs_structure(session)
    print(f"seed_bs_structure: {n} BS rows upserted into dim_bs_structure")
