"""Seed dim_country and normalize dim_customer / dim_supplier country codes.

Run after partner master load so geography analytics can join dim_country and
use consistent ISO-3 keys (D/DE → DEU, NL → NLD, …).

Usage:
  cd Finssentials_GDPDU
  $env:DB_PASSWORD = "..."
  .\\backend\\.venv\\Scripts\\python.exe backend/scripts/seed_dim_country.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from sqlalchemy import text
from sqlalchemy.orm import Session as SASession

from app.db import engine
from app.services.geo_reference import COUNTRY_NAMES, normalize_country_code


def _normalize_partner_countries(session: SASession) -> tuple[int, int]:
    cust_rows = session.execute(
        text("SELECT customer_id, country_code FROM dim_customer WHERE country_code IS NOT NULL")
    ).fetchall()
    sup_rows = session.execute(
        text("SELECT supplier_id, country_code FROM dim_supplier WHERE country_code IS NOT NULL")
    ).fetchall()

    cust_up = 0
    for cid, code in cust_rows:
        norm = normalize_country_code(code)
        if norm and norm != code:
            session.execute(
                text("UPDATE dim_customer SET country_code = :c WHERE customer_id = :id"),
                {"c": norm, "id": cid},
            )
            cust_up += 1

    sup_up = 0
    for sid, code in sup_rows:
        norm = normalize_country_code(code)
        if norm and norm != code:
            session.execute(
                text("UPDATE dim_supplier SET country_code = :c WHERE supplier_id = :id"),
                {"c": norm, "id": sid},
            )
            sup_up += 1

    return cust_up, sup_up


def _seed_dim_country(session: SASession) -> int:
    codes = session.execute(
        text("""
            SELECT DISTINCT country_code FROM (
                SELECT country_code FROM dim_customer WHERE country_code IS NOT NULL
                UNION
                SELECT country_code FROM dim_supplier WHERE country_code IS NOT NULL
            ) u
        """)
    ).fetchall()
    n = 0
    for (code,) in codes:
        name = COUNTRY_NAMES.get(str(code), str(code))
        session.execute(
            text("""
                INSERT INTO dim_country (country_code, name_en, name_de)
                VALUES (:code, :en, :en)
                ON CONFLICT (country_code) DO UPDATE SET
                  name_en = COALESCE(dim_country.name_en, EXCLUDED.name_en),
                  name_de = COALESCE(dim_country.name_de, EXCLUDED.name_de)
            """),
            {"code": code, "en": name},
        )
        n += 1
    return n


def main() -> int:
    with SASession(engine) as session:
        cust_up, sup_up = _normalize_partner_countries(session)
        seeded = _seed_dim_country(session)
        session.commit()
        print(f"Normalized customer country codes: {cust_up}")
        print(f"Normalized supplier country codes: {sup_up}")
        print(f"dim_country rows upserted: {seeded}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
