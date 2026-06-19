"""Seed dim_pl_structure from the Decidra Account_Mapping.xlsx 'PL Structure' sheet.

D8 rule (chosen after reading actual PL Structure values):
  Calc type = 1  ->  row_type = 'mapping',  kpi_code = NULL
  Calc type = 2  ->  row_type = 'calc' for lines whose balance_title matches a known
                     KPI (Gross profit -> GROSS_PROFIT, EBITDA -> EBITDA);
                     row_type = 'subtotal' for all other Calc type=2 lines
                     (Total output, EBIT, EBT, Net profit, Net loss).
                     kpi_code is NULL for 'subtotal' rows.

Actual Calc type=2 lines in the sheet:
  Sort 4  = Total output         -> subtotal (no KPI in statements.py)
  Sort 6  = Gross profit         -> calc, kpi_code=GROSS_PROFIT
  Sort 10 = EBITDA               -> calc, kpi_code=EBITDA
  Sort 12 = EBIT                 -> subtotal (not a tracked KPI)
  Sort 17 = EBT                  -> subtotal
  Sort 20 = Net profit           -> subtotal

line_code is built from Dynamic name stripped of leading braille/whitespace
then uppercased and spaces replaced with underscores (unique per sort_order).
If Dynamic name is empty fall back to Balance title.

Usage (needs DB_PASSWORD env var):
  cd C:\\Users\\bened\\OneDrive\\Finssentials\\Finssentials_GDPDU
  $env:DB_PASSWORD = "..."
  .\\backend\\.venv\\Scripts\\python.exe backend/scripts/seed_pl_structure.py \\
      "C:/Users/.../Account_Mapping.xlsx"
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

# Add backend/ to sys.path so app.* imports work
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


# Known KPI mapping for Calc type=2 lines (by normalised balance title)
_KPI_BY_TITLE: dict[str, tuple[str, str]] = {
    # balance_title_lower_stripped: (row_type, kpi_code)
    "gross profit": ("calc", "GROSS_PROFIT"),
    "ebitda":       ("calc", "EBITDA"),
}
# Everything else with Calc type=2 -> subtotal


def _make_line_code(dynamic_name: str, balance_title: str, sort: int) -> str:
    """Build a stable line_code from the Dynamic name or Balance title."""
    src = (dynamic_name or balance_title or "").strip()
    # Strip leading braille/invisible chars (U+2800 range used as indent in this sheet)
    src = re.sub(r"^[⠀-⣿\s]+", "", src).strip()
    if not src:
        src = f"LINE_{sort}"
    # Uppercase, replace spaces/punctuation with underscores, drop other special chars
    code = re.sub(r"[^A-Za-z0-9_]", "_", src.upper())
    code = re.sub(r"_+", "_", code).strip("_")
    return code[:64]


def seed_pl_structure(xlsx_path: str | Path, session) -> int:
    """Read PL Structure sheet, upsert into dim_pl_structure. Returns rows upserted."""
    from sqlalchemy import text

    df = pd.read_excel(str(xlsx_path), sheet_name="PL Structure")
    # Expected columns: Balance title, Sort, L0, Calc type, Details, Dynamic name
    upserted = 0
    for _, row in df.iterrows():
        sort_order = int(row["Sort"])
        calc_type_raw = row["Calc type"]
        calc_type = int(calc_type_raw) if pd.notna(calc_type_raw) else 1
        balance_title = str(row["Balance title"]).strip() if pd.notna(row["Balance title"]) else ""
        dynamic_name = str(row.get("Dynamic name", "") or "").strip()
        details_raw = row.get("Details")
        details = int(details_raw) if pd.notna(details_raw) else None

        line_code = _make_line_code(dynamic_name, balance_title, sort_order)

        # D8: determine row_type and kpi_code
        if calc_type == 1:
            row_type = "mapping"
            kpi_code = None
        else:
            # calc_type == 2
            title_key = balance_title.lower().strip()
            if title_key in _KPI_BY_TITLE:
                row_type, kpi_code = _KPI_BY_TITLE[title_key]
            else:
                row_type = "subtotal"
                kpi_code = None

        # Wire the line to the GL hierarchy: for the Decidra PL the structure's
        # Balance title equals the GL accounts' level_3 category (verified: every
        # GL level_3 maps to a structure title). Only mapping rows carry a level
        # filter; subtotal/calc rows are computed and stay unfiltered (NULL).
        level_3 = balance_title if row_type == "mapping" else None

        session.execute(
            text("""
                INSERT INTO dim_pl_structure
                  (sort_order, line_code, row_type, balance_title, details, calc_type, kpi_code, level_3)
                VALUES (:so, :lc, :rt, :bt, :det, :ct, :kpi, :l3)
                ON CONFLICT (line_code) DO UPDATE SET
                  sort_order    = EXCLUDED.sort_order,
                  row_type      = EXCLUDED.row_type,
                  balance_title = EXCLUDED.balance_title,
                  details       = EXCLUDED.details,
                  calc_type     = EXCLUDED.calc_type,
                  kpi_code      = EXCLUDED.kpi_code,
                  level_3       = EXCLUDED.level_3
            """),
            {
                "so": sort_order, "lc": line_code, "rt": row_type,
                "bt": balance_title, "det": details, "ct": calc_type, "kpi": kpi_code,
                "l3": level_3,
            },
        )
        upserted += 1
    session.commit()
    return upserted


if __name__ == "__main__":
    xlsx_path = sys.argv[1] if len(sys.argv) > 1 else (
        r"C:\Users\bened\OneDrive\Desktop\Project SELFMADE\Cursor\analytics-layer\reference\decidra\Account_Mapping.xlsx"
    )
    from app.db import engine
    from sqlalchemy.orm import Session as SASession
    with SASession(engine) as session:
        n = seed_pl_structure(xlsx_path, session)
    print(f"seed_pl_structure: {n} rows upserted into dim_pl_structure")
