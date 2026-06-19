"""Seed roles + users (P0).

- Always seeds SEED_ADMIN_EMAIL (benedikt.hoffarth@finssentials.com) as admin.
- Seeds a few demo users with RANDOM admin rights (per the request).
- Idempotent: existing emails are skipped.

Run:  python -m app.seed_admins
"""
from __future__ import annotations

import random

from passlib.context import CryptContext
from sqlalchemy import text

from app.config import settings
from app.db import engine

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

DEMO_USERS = [
    ("anna.schmidt@finssentials.com", "Anna Schmidt"),
    ("lukas.mueller@finssentials.com", "Lukas Müller"),
    ("marie.weber@finssentials.com", "Marie Weber"),
    ("jonas.fischer@finssentials.com", "Jonas Fischer"),
    ("sophie.becker@finssentials.com", "Sophie Becker"),
]

ROLES = [
    ("admin", "Full access incl. role management & ingestion"),
    ("analyst", "Read access to analyses"),
]


def _upsert_role(conn, name: str, desc: str) -> int:
    conn.execute(
        text(
            "INSERT INTO dim_role (role_name, description) VALUES (:n, :d) "
            "ON CONFLICT (role_name) DO NOTHING"
        ),
        {"n": name, "d": desc},
    )
    return conn.execute(
        text("SELECT role_id FROM dim_role WHERE role_name = :n"), {"n": name}
    ).scalar_one()


def _upsert_user(conn, email: str, name: str, is_admin: bool) -> int:
    row = conn.execute(
        text("SELECT user_id FROM dim_user WHERE email = :e"), {"e": email}
    ).first()
    if row:
        return row[0]
    return conn.execute(
        text(
            "INSERT INTO dim_user (email, display_name, password_hash, is_admin) "
            "VALUES (:e, :n, :p, :a) RETURNING user_id"
        ),
        {"e": email, "n": name, "p": pwd.hash(settings.seed_default_password), "a": is_admin},
    ).scalar_one()


def _assign_role(conn, user_id: int, role_id: int) -> None:
    conn.execute(
        text(
            "INSERT INTO user_role (user_id, role_id) VALUES (:u, :r) "
            "ON CONFLICT DO NOTHING"
        ),
        {"u": user_id, "r": role_id},
    )


def main() -> None:
    rng = random.Random(42)  # deterministic random admin assignment
    with engine.begin() as conn:
        roles = {name: _upsert_role(conn, name, desc) for name, desc in ROLES}

        # Fixed admin
        uid = _upsert_user(conn, settings.seed_admin_email, "Benedikt Hoffarth", True)
        _assign_role(conn, uid, roles["admin"])
        print(f"admin   {settings.seed_admin_email}")

        # Demo users — random admin rights
        for email, name in DEMO_USERS:
            is_admin = rng.random() < 0.5
            uid = _upsert_user(conn, email, name, is_admin)
            _assign_role(conn, uid, roles["admin"] if is_admin else roles["analyst"])
            print(f"{'admin  ' if is_admin else 'analyst'} {email}")

    # Only reveal the seed password in dev; never print it in staging/production.
    if settings.app_env == "dev":
        print(f"\nDefault password for all seeded users: {settings.seed_default_password}")
        print("Change it after first login.")


if __name__ == "__main__":
    main()
