"""Create the target database in an existing PostgreSQL instance (no Docker needed).

Connection via DB_* env vars (same convention as the legacy backend):
  DB_HOST (localhost) · DB_PORT (5432) · DB_USER (postgres) · DB_PASSWORD ('') · DB_NAME (Finssentials)

Run:  python scripts/create_database.py
Idempotent: skips creation if the database already exists.
"""
from __future__ import annotations

import os
import sys

import psycopg2
from psycopg2 import sql

HOST = os.getenv("DB_HOST", "localhost")
PORT = int(os.getenv("DB_PORT", "5432"))
USER = os.getenv("DB_USER", "postgres")
PASSWORD = os.getenv("DB_PASSWORD", "")
TARGET = os.getenv("DB_NAME", "Finssentials")

# Maintenance DB to connect to for the CREATE DATABASE statement.
MAINT_CANDIDATES = [c for c in [os.getenv("DB_MAINT"), "postgres", "edb", "fissentials", "template1"] if c]


def _connect():
    last = None
    for maint in MAINT_CANDIDATES:
        try:
            conn = psycopg2.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, dbname=maint)
            print(f"connected via maintenance db '{maint}' as user '{USER}' on {HOST}:{PORT}")
            return conn
        except Exception as e:  # noqa: BLE001
            last = e
    print("CONNECT_FAILED:", last, file=sys.stderr)
    sys.exit(2)


def main() -> None:
    conn = _connect()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TARGET,))
    if cur.fetchone():
        print(f"EXISTS: database {TARGET!r}")
    else:
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TARGET)))
        print(f"CREATED: database {TARGET!r}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
