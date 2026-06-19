"""End-to-end smoke test for the ingestion API (synthetic data only).

Exercises POST /auth/login -> POST /upload -> POST /validate -> GET /runs
via FastAPI TestClient (in-process, no network).
Needs DB_PASSWORD in env (validate queries dim_gl_account for M1).
Needs a seeded admin user (seed_admin_email / seed_default_password from config).
Run:  python scripts/smoke_ingest.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

CSV = """Tx,Account,Amount,PostingDate,SourceType,SourceNo
1,10000,1190.00,15.03.2024,Debitor,100
1,80000,-1000.00,15.03.2024,,
1,17760,-190.00,15.03.2024,,
2,30000,500.00,20.03.2024,,
2,15760,95.00,20.03.2024,,
2,70000,-595.00,20.03.2024,Kreditor,200
"""

PROFILE = {
    "entity": {"mode": "fixed", "value": "01"},
    "fiscal_year": {"mode": "from_date", "value": None},
    "sign": {"mode": "signed", "amount": "Amount"},
    "decimal": ".",
    "thousands": ",",
    "date_dayfirst": True,
    "columns": {
        "journal_entry_number": "Tx",
        "account_number": "Account",
        "posting_date": "PostingDate",
        "source_type": "SourceType",
        "source_no": "SourceNo",
    },
    "linking_strategy": "txn",
    "entry_type": "actual",
    "source_system": "smoke",
}


def main() -> int:
    client = TestClient(app)

    # health (open endpoint — no auth needed)
    h = client.get("/api/v1/health").json()
    print("health:", h)

    # login — obtain bearer token for protected endpoints
    login_r = client.post(
        "/api/v1/auth/login",
        json={
            "email": settings.seed_admin_email,
            "password": settings.seed_default_password,
        },
    )
    print("login:", login_r.status_code)
    assert login_r.status_code == 200, f"Login failed: {login_r.text}"
    token = login_r.json()["access_token"]
    auth_headers = {"Authorization": f"Bearer {token}"}
    print("  logged in as:", login_r.json()["user"]["email"])

    # upload (requires auth — send bearer token)
    tmp = Path(tempfile.gettempdir()) / "smoke_gl.csv"
    tmp.write_text(CSV, encoding="utf-8")
    with tmp.open("rb") as fh:
        up = client.post(
            "/api/v1/ingest/upload",
            files={"file": ("smoke_gl.csv", fh, "text/csv")},
            headers=auth_headers,
        )
    print("upload:", up.status_code)
    assert up.status_code == 200, up.text
    upj = up.json()
    print("  columns:", upj["columns"], "dialect:", upj["dialect"])
    file_id = upj["file_id"]

    # validate (requires current_user)
    val = client.post(
        "/api/v1/ingest/validate",
        json={"file_id": file_id, "profile": PROFILE},
        headers=auth_headers,
    )
    print("validate:", val.status_code)
    assert val.status_code == 200, val.text
    vj = val.json()
    print("  summary:", vj["summary"])
    for r in vj["results"]:
        print(f"    {r['id']:<3} {r['severity']:<4} {'OK ' if r['passed'] else 'FAIL'} {r['name']} | {r['detail']}")
    print("  key_preview[0]:", vj["key_preview"][0] if vj["key_preview"] else None)
    print("  unmapped_accounts:", vj["unmapped_accounts"])

    # runs (requires current_user)
    runs = client.get("/api/v1/ingest/runs", headers=auth_headers)
    print("runs:", runs.status_code, "->", len(runs.json()), "record(s)")

    # Assertions: B1/B2 balanced, only M1 (SOFT) may fail -> overall passed
    by_id = {r["id"]: r for r in vj["results"]}
    assert by_id["B1"]["passed"], "B1 per-booking balance must pass on balanced data"
    assert by_id["B2"]["passed"], "B2 ledger balance must pass"
    assert by_id["Q2"]["passed"], "Q2 unique booking_line_id must pass"
    assert vj["summary"]["passed"], f"overall should pass (only SOFT M1 may warn): {vj['summary']}"
    print("\nSMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
