"""Quick DB smoke-test script.  Run via: python tests/smoke_db.py"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.db import engine
from sqlalchemy import text


def check():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("DB reachable: YES")
    except Exception as e:
        print(f"DB NOT reachable: {e}")
        return False

    # Check posting_date range
    with engine.connect() as conn:
        r = conn.execute(text(
            "SELECT MIN(posting_date), MAX(posting_date) FROM fact_gl_entry"
        )).fetchone()
        if r and r[0]:
            print(f"fact_gl_entry posting_date range: {r[0]} → {r[1]}")
            return True
        else:
            print("fact_gl_entry: empty or no posting_date")
            return False


if __name__ == "__main__":
    ok = check()
    sys.exit(0 if ok else 1)
