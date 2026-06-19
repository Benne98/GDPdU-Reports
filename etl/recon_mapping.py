"""Load BS/PL recon presentation mapping from Excel into dim_*_recon_mapping tables."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PL_PATH = PROJECT_ROOT / "Desktop" / "PL_recon_Mapping.xlsx"
DEFAULT_BS_PATH = PROJECT_ROOT / "Desktop" / "BS_recon_Mapping.xlsx"


def read_pl_recon_excel(path: str | Path) -> pd.DataFrame:
    df = pd.read_excel(str(path), sheet_name=0, engine="openpyxl")
    if "L3" not in df.columns:
        raise ValueError(f"PL recon mapping must contain column L3: {path}")
    if "L4" not in df.columns:
        df["L4"] = ""
    out = df[["L3", "L4"]].copy()
    out["L3"] = out["L3"].astype(str).str.strip()
    out["L4"] = out["L4"].fillna("").astype(str).str.strip()
    out = out[out["L3"].notna() & (out["L3"] != "") & (out["L3"] != "nan")]
    out = out.reset_index(drop=True)
    out.insert(0, "sort_order", range(1, len(out) + 1))
    return out


def read_bs_recon_excel(path: str | Path) -> pd.DataFrame:
    df = pd.read_excel(str(path), sheet_name=0, engine="openpyxl")
    for col in ("L2", "L3", "L4"):
        if col not in df.columns:
            raise ValueError(f"BS recon mapping must contain column {col}: {path}")
    out = df[["L2", "L3", "L4"]].copy()
    for col in ("L2", "L3", "L4"):
        out[col] = out[col].fillna("").astype(str).str.strip()
    out = out[out["L2"].notna() & (out["L2"] != "") & (out["L2"] != "nan")]
    out = out.reset_index(drop=True)
    out.insert(0, "sort_order", range(1, len(out) + 1))
    return out


def load_pl_recon_mapping(session: Session, df: pd.DataFrame) -> int:
    session.execute(text("DELETE FROM dim_pl_recon_mapping"))
    rows = 0
    for _, row in df.iterrows():
        session.execute(
            text(
                """
                INSERT INTO dim_pl_recon_mapping (sort_order, l3, l4)
                VALUES (:sort_order, :l3, :l4)
                """
            ),
            {
                "sort_order": int(row["sort_order"]),
                "l3": str(row["L3"]),
                "l4": str(row["L4"]),
            },
        )
        rows += 1
    return rows


def load_bs_recon_mapping(session: Session, df: pd.DataFrame) -> int:
    session.execute(text("DELETE FROM dim_bs_recon_mapping"))
    rows = 0
    for _, row in df.iterrows():
        session.execute(
            text(
                """
                INSERT INTO dim_bs_recon_mapping (sort_order, l2, l3, l4)
                VALUES (:sort_order, :l2, :l3, :l4)
                """
            ),
            {
                "sort_order": int(row["sort_order"]),
                "l2": str(row["L2"]),
                "l3": str(row["L3"]),
                "l4": str(row["L4"]),
            },
        )
        rows += 1
    return rows


def seed_recon_mapping_from_excel(
    session: Session,
    *,
    pl_path: str | Path | None = None,
    bs_path: str | Path | None = None,
) -> dict[str, Any]:
    pl_file = Path(pl_path or DEFAULT_PL_PATH)
    bs_file = Path(bs_path or DEFAULT_BS_PATH)
    if not pl_file.is_file():
        raise FileNotFoundError(f"PL recon mapping not found: {pl_file}")
    if not bs_file.is_file():
        raise FileNotFoundError(f"BS recon mapping not found: {bs_file}")

    pl_df = read_pl_recon_excel(pl_file)
    bs_df = read_bs_recon_excel(bs_file)
    pl_count = load_pl_recon_mapping(session, pl_df)
    bs_count = load_bs_recon_mapping(session, bs_df)
    session.commit()
    return {"pl_rows": pl_count, "bs_rows": bs_count, "pl_path": str(pl_file), "bs_path": str(bs_file)}
