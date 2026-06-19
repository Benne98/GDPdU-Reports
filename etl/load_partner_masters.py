"""Load BC-style Customer/Vendor Master CSVs into partner dimension DataFrames.

Mirrors legacy ``backend/setup_db.py`` (load_customer_master / load_vendor_master)
for the GDPdU canonical schema consumed by ``etl.load.load_partners``.

Partner keys must match ``fact_gl_line`` / ``fact_sales``:
  customer_id = entity_prefix(2) + debtor_number  (BC ``No.``)
  supplier_id = entity_prefix(2) + creditor_number
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from etl.geo_reference import normalize_country_code
from etl.transform import build_partner_id, normalize_prefix

# Decidra Entity sheet (Account_Mapping.xlsx) — friendly name → 2-digit prefix.
_ENTITY_CSV_TO_PREFIX: dict[str, str] = {
    "Atlas": "01",
    "Meridian": "02",
    "Novara": "03",
    "Venturo": "04",
    "Calypto": "05",
}

# Legacy Decidra entity labels → canonical legal_entity_code (extend per tenant).
_ENTITY_CSV_TO_CANONICAL: dict[str, str] = {
    "Atlas": "ATLAS",
    "Meridian": "MERIDIAN",
    "Novara": "NOVARA",
    "Venturo": "VENTURO",
    "Calypto": "CALYPTO",
    "Decidra": "DECIDRA",
}

# GDPdU repo exports (see backend/scripts/export_partner_masters.py).
_REPO_PARTNER_DIR = Path(__file__).resolve().parent / "source_data" / "partner_masters"
DEFAULT_CUSTOMER_MASTER_CSV = str(_REPO_PARTNER_DIR / "Customer Master.csv")
DEFAULT_VENDOR_MASTER_CSV = str(_REPO_PARTNER_DIR / "Vendor Master.csv")

# Legacy Decidra paths (fallback when repo export not present).
_LEGACY_CUSTOMER_MASTER_CSV = (
    r"C:\Users\bened\Korsawe Consulting GmbH\Dashboards - Allgemein"
    r"\7_Decidra_Prototyp\Input\Customer Master.csv"
)
_LEGACY_VENDOR_MASTER_CSV = (
    r"C:\Users\bened\Korsawe Consulting GmbH\Dashboards - Allgemein"
    r"\7_Decidra_Prototyp\Input\Vendor Master.csv"
)


def _entity_prefix(raw_entity: str) -> Optional[str]:
    label = (raw_entity or "").strip()
    if not label:
        return None
    prefix = _ENTITY_CSV_TO_PREFIX.get(label)
    if prefix:
        return prefix
    # Allow numeric entity numbers from alternate exports ("1" → "01").
    if label.isdigit():
        return normalize_prefix(label)
    return None


def _normalize_entity(raw: str) -> Optional[str]:
    s = (raw or "").strip()
    if not s:
        return None
    return _ENTITY_CSV_TO_CANONICAL.get(s, s.upper().replace(" ", "_")[:32])


def _read_master_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [c.strip().strip('"') for c in df.columns]
    return df


def _partner_id(prefix: str, number: str) -> str:
    """Build entity-aware partner id (matches ``etl.transform.build_partner_id``)."""
    return str(build_partner_id(prefix, pd.Series([number])).iloc[0])


def load_customer_master_csv(path: str | Path) -> pd.DataFrame:
    """Parse Customer Master.csv → dim_customer-shaped DataFrame."""
    df = _read_master_csv(Path(path))
    rows: list[dict] = []
    seen: set[str] = set()
    for _, r in df.iterrows():
        debtor_no = str(r.get("No.", "")).strip()
        if not debtor_no:
            continue
        prefix = _entity_prefix(str(r.get("entity", "")))
        if not prefix:
            continue
        customer_id = _partner_id(prefix, debtor_no)
        if customer_id in seen:
            continue
        seen.add(customer_id)
        entity = _normalize_entity(str(r.get("entity", "")))
        rows.append({
            "customer_id": customer_id,
            "debtor_number": debtor_no,
            "name_line_1": (r.get("BAU Name lang", "") or "")[:200] or None,
            "name_line_2": (r.get("BAU Adresse 2 lang", "") or "")[:200] or None,
            "country_code": normalize_country_code(str(r.get("Country/Region Code", "") or "").strip() or None),
            "region_code": None,
            "city": (r.get("City", "") or "")[:120] or None,
            "postal_code": (r.get("Post Code", "") or "")[:20] or None,
            "default_currency": "EUR",
            "source_system": "ms_business_central",
            "legal_entity_code": entity,
        })
    return pd.DataFrame(rows)


def load_vendor_master_csv(path: str | Path) -> pd.DataFrame:
    """Parse Vendor Master.csv → dim_supplier-shaped DataFrame."""
    df = _read_master_csv(Path(path))
    rows: list[dict] = []
    seen: set[str] = set()
    for _, r in df.iterrows():
        creditor_no = str(r.get("No.", "")).strip()
        if not creditor_no:
            continue
        prefix = _entity_prefix(str(r.get("entity", "")))
        if not prefix:
            continue
        supplier_id = _partner_id(prefix, creditor_no)
        if supplier_id in seen:
            continue
        seen.add(supplier_id)
        entity = _normalize_entity(str(r.get("entity", "")))
        rows.append({
            "supplier_id": supplier_id,
            "creditor_number": creditor_no,
            "name_line_1": (r.get("BAU Name lang", "") or "")[:200] or None,
            "name_line_2": (r.get("BAU Adresse 2 lang", "") or "")[:200] or None,
            "country_code": normalize_country_code(str(r.get("Country/Region Code", "") or "").strip() or None),
            "region_code": None,
            "city": (r.get("City", "") or "")[:120] or None,
            "postal_code": (r.get("Post Code", "") or "")[:20] or None,
            "default_currency": "EUR",
            "source_system": "ms_business_central",
            "legal_entity_code": entity,
        })
    return pd.DataFrame(rows)
