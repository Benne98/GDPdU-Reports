"""Load PL/BS recon presentation mapping from PostgreSQL or Excel fallback."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_PL_MAPPING = PROJECT_ROOT / "Desktop" / "PL_recon_Mapping.xlsx"
DEFAULT_BS_MAPPING = PROJECT_ROOT / "Desktop" / "BS_recon_Mapping.xlsx"


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg2://", 1)
        return url
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "")
    name = os.getenv("DB_NAME", "Finssentials")
    auth = f"{user}:{password}" if password else user
    return f"postgresql+psycopg2://{auth}@{host}:{port}/{name}"


def _mapping_source(config: dict | None) -> str:
    if not config:
        return "db"
    paths = config.get("paths") or {}
    return str(paths.get("mapping_source") or "db").strip().lower()


def _load_pl_from_db() -> pd.DataFrame:
    from sqlalchemy import create_engine, text

    engine = create_engine(_database_url(), future=True)
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT l3, l4 FROM dim_pl_recon_mapping ORDER BY sort_order")
        ).fetchall()
    if not rows:
        raise RuntimeError(
            "dim_pl_recon_mapping is empty. Run: python backend/scripts/seed_recon_mapping.py"
        )
    df = pd.DataFrame(rows, columns=["L3", "L4"])
    df["L3"] = df["L3"].astype(str).str.strip()
    df["L4"] = df["L4"].fillna("").astype(str).str.strip()
    return df


def _load_bs_from_db() -> pd.DataFrame:
    from sqlalchemy import create_engine, text

    engine = create_engine(_database_url(), future=True)
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT l2, l3, l4 FROM dim_bs_recon_mapping ORDER BY sort_order")
        ).fetchall()
    if not rows:
        raise RuntimeError(
            "dim_bs_recon_mapping is empty. Run: python backend/scripts/seed_recon_mapping.py"
        )
    df = pd.DataFrame(rows, columns=["L2", "L3", "L4"])
    for col in ("L2", "L3", "L4"):
        df[col] = df[col].fillna("").astype(str).str.strip()
    return df.loc[:, ["L2", "L3", "L4"]]


def load_pl_recon_mapping_df(config: dict | None = None) -> pd.DataFrame:
    if _mapping_source(config) == "file":
        path = str((config or {}).get("paths", {}).get("mapping_file") or DEFAULT_PL_MAPPING)
        df = pd.read_excel(path, sheet_name=0, engine="openpyxl")
        if "L3" not in df.columns:
            raise ValueError("PL mapping file must contain column L3")
        if "L4" not in df.columns:
            df["L4"] = ""
        return df[["L3", "L4"]].copy()
    return _load_pl_from_db()


def load_bs_recon_mapping_df(config: dict | None = None) -> pd.DataFrame:
    if _mapping_source(config) == "file":
        path = str((config or {}).get("paths", {}).get("mapping_file") or DEFAULT_BS_MAPPING)
        df = pd.read_excel(path, sheet_name=0, engine="openpyxl")
        df = df.loc[:, [c for c in df.columns if not str(c).startswith("Unnamed")]]
        if not {"L2", "L3", "L4"}.issubset(df.columns):
            raise ValueError("BS mapping file must contain L2, L3, L4")
        return df[["L2", "L3", "L4"]].copy()
    return _load_bs_from_db()


def apply_pl_mapping_postprocess(map_df: pd.DataFrame, normalize_fn) -> pd.DataFrame:
    out = map_df.copy()
    out["L3"] = out["L3"].astype(str).apply(normalize_fn)
    out["L4"] = out["L4"].where(out["L4"].notna(), "").apply(normalize_fn)
    out["is_subtotal"] = out["L4"].eq(out["L3"])
    return out
