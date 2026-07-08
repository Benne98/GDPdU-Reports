#!/usr/bin/env python3
"""Headless end-to-end smoke driver for the v5 Project-Setup -> Reporting pipeline.

Provisions a FRESH synthetic GL dataset (with CoA positions deliberately absent
from the seed structure) through the REAL ingest endpoints, in the SAME order the
frontend Project-Setup wizard uses, then proves auto-extension places the unknown
positions and reporting reflects them.

This is a REAL HTTP driver (no mocks). It needs a running API and a DB; the author
(data-transformation-engineer) does NOT run it -- the coordinator runs it against a
fresh clone `finssentials_v5_e2e` on a test API (see README.md).

Endpoint sequence (derived from frontend/src/pages/ProjectSetupWizard.tsx +
components/ingest/*.tsx + lib/gdpduApi.ts):

    POST /api/v1/auth/login                         -> bearer token
    POST /api/v1/ingest/upload            (x2)      -> per-year GL file_ids
    POST /api/v1/ingest/gl/combine                  -> combined GL file_id (+ fiscal_year col)
    POST /api/v1/ingest/validate          stage=gl  -> summary + exclusions
    POST /api/v1/ingest/upload            (CoA xlsx)-> coa file_id
    POST /api/v1/ingest/mapping/commit    bs_pl_master, entity_prefixes -> dim_gl_account
    POST /api/v1/ingest/commit            dataset=gl, confirm_soft      -> fact_gl_*
    POST /api/v1/ingest/structure/unknown-positions -> novel positions (>0)
    POST /api/v1/ingest/structure/extend            -> place novel positions (source=auto_extend)
    POST /api/v1/ingest/structure/unknown-positions -> re-check: novel now resolved
    GET  /api/v1/statements/pl , /statements/bs     -> reporting reflects placed positions
    GET  /api/v1/financials/pl-statement            -> has_plan_data must be False (best-effort)

NB on ordering: the wizard runs structure/unknown-positions in step 7 (BEFORE the
step-8 commits), where a fresh project has nothing committed yet, so it auto-passes.
This harness intentionally runs detection AFTER the CoA+GL commits -- that is the only
point at which the novel CoA positions actually exist to be detected and placed.
That post-commit detect->extend->reporting loop is exactly what we are proving.

Usage:
    cd backend
    python scripts/e2e/run_pipeline_e2e.py \
        --api-base http://127.0.0.1:8016 \
        --email admin@example.com --password secret

    # or with a pre-obtained token:
    python scripts/e2e/run_pipeline_e2e.py --api-base http://127.0.0.1:8016 --token "<JWT>"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import requests

# Import the fixture spec so the driver + asserter share one source of truth.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_e2e_gl_fixture as fx  # noqa: E402

DEFAULT_TIMEOUT = 900  # seconds; large GL loads can be slow


# --------------------------------------------------------------------------- #
# Tiny logging helpers (verbose + resilient so a shape mismatch is obvious)
# --------------------------------------------------------------------------- #
class Fail(RuntimeError):
    """A pipeline step failed in a way that stops the run."""


_STEP = 0


def step(title: str) -> None:
    global _STEP
    _STEP += 1
    print(f"\n=== STEP {_STEP}: {title} ", flush=True)


def info(msg: str) -> None:
    print(f"    {msg}", flush=True)


def show_resp(resp: requests.Response, *, keys: list[str] | None = None) -> Any:
    """Log status + the 2-3 response fields that matter; return parsed JSON (or text)."""
    ctype = resp.headers.get("content-type", "")
    body: Any
    if "application/json" in ctype:
        try:
            body = resp.json()
        except Exception:
            body = resp.text
    else:
        body = resp.text
    info(f"HTTP {resp.status_code}  {resp.request.method} {resp.request.path_url}")
    if keys and isinstance(body, dict):
        for k in keys:
            if k in body:
                v = body[k]
                if isinstance(v, (list, dict)):
                    info(f"  {k}: {json.dumps(v)[:400]}")
                else:
                    info(f"  {k}: {v}")
    elif isinstance(body, dict):
        info(f"  body: {json.dumps(body)[:400]}")
    else:
        info(f"  body: {str(body)[:400]}")
    return body


class Api:
    def __init__(self, base: str, token: str | None = None):
        self.base = base.rstrip("/")
        self.s = requests.Session()
        if token:
            self.s.headers["Authorization"] = f"Bearer {token}"

    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    def post(self, path: str, *, json_body: dict | None = None,
             files: dict | None = None, data: dict | None = None) -> requests.Response:
        return self.s.post(self._url(path), json=json_body, files=files, data=data,
                           timeout=DEFAULT_TIMEOUT)

    def get(self, path: str, *, params: dict | None = None) -> requests.Response:
        return self.s.get(self._url(path), params=params, timeout=DEFAULT_TIMEOUT)


# --------------------------------------------------------------------------- #
# Pipeline steps
# --------------------------------------------------------------------------- #
def login(api: Api, email: str, password: str) -> str:
    step("Login (POST /api/v1/auth/login)")
    resp = api.post("/api/v1/auth/login", json_body={"email": email, "password": password})
    body = show_resp(resp, keys=["token_type", "user"])
    if resp.status_code != 200 or not isinstance(body, dict) or "access_token" not in body:
        raise Fail("login failed -- check --email/--password (JSON body {email,password})")
    token = body["access_token"]
    api.s.headers["Authorization"] = f"Bearer {token}"
    info("bearer token acquired")
    return token


def upload_gl_years(api: Api, fixture_dir: Path) -> list[dict]:
    step("Upload per-year GL files (POST /api/v1/ingest/upload)")
    inputs = []
    for year in fx.FISCAL_YEARS:
        p = fixture_dir / fx.GL_YEAR_FILES[year]
        if not p.exists():
            raise Fail(f"fixture missing: {p} -- run gen_e2e_gl_fixture.py first")
        with p.open("rb") as fh:
            resp = api.post(
                "/api/v1/ingest/upload",
                files={"file": (p.name, fh, "text/csv")},
                data={"has_header": "true"},
            )
        body = show_resp(resp, keys=["file_id", "columns", "has_header"])
        if resp.status_code != 200:
            raise Fail(f"GL upload for {year} failed")
        inputs.append({"file_id": body["file_id"], "fiscal_year": year})
    return inputs


def combine_gl(api: Api, inputs: list[dict]) -> str:
    step("Combine per-year GL files (POST /api/v1/ingest/gl/combine)")
    resp = api.post("/api/v1/ingest/gl/combine", json_body={"inputs": inputs})
    body = show_resp(resp, keys=["file_id", "columns", "row_count", "column_warning"])
    if resp.status_code != 200:
        raise Fail("gl/combine failed")
    cols = body.get("columns", [])
    if fx._FISCAL_YEAR_COL not in cols:
        info(f"WARNING: combined file has no '{fx._FISCAL_YEAR_COL}' column; columns={cols}")
    return body["file_id"]


def validate_gl(api: Api, file_id: str) -> list[str]:
    step("Validate GL on staging (POST /api/v1/ingest/validate, stage=gl)")
    resp = api.post("/api/v1/ingest/validate", json_body={
        "file_id": file_id,
        "profile": fx.GL_PROFILE,
        "exclude_line_ids": [],
        "stage": "gl",
    })
    body = show_resp(resp, keys=["summary"])
    if resp.status_code != 200:
        raise Fail("validate failed -- GL profile/columns likely mismatched")
    summary = body.get("summary", {}) if isinstance(body, dict) else {}
    blocking = summary.get("blocking", [])
    info(f"summary.passed={summary.get('passed')}  blocking={blocking}")
    exclusions = (body.get("exclusions") or {}) if isinstance(body, dict) else {}
    active = exclusions.get("active_line_ids", []) or []
    if active:
        info(f"validation suggests excluding {len(active)} line(s)")
    return active


def upload_coa(api: Api, fixture_dir: Path) -> str:
    step("Upload CoA mapping workbook (POST /api/v1/ingest/upload)")
    p = fixture_dir / fx.COA_XLSX_NAME
    if not p.exists():
        raise Fail(f"fixture missing: {p}")
    with p.open("rb") as fh:
        resp = api.post(
            "/api/v1/ingest/upload",
            files={"file": (
                p.name, fh,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )},
        )
    body = show_resp(resp, keys=["file_id", "sheets"])
    if resp.status_code != 200:
        raise Fail("CoA upload failed")
    return body["file_id"]


def commit_coa(api: Api, file_id: str) -> dict:
    step("Commit CoA mapping (POST /api/v1/ingest/mapping/commit, bs_pl_master)")
    resp = api.post("/api/v1/ingest/mapping/commit", json_body={
        "file_id": file_id,
        "format": "bs_pl_master",
        "entity_prefixes": [fx.ENTITY_PREFIX],
        "fiscal_years": fx.FISCAL_YEARS,
        "replace_mode": "replace",
    })
    body = show_resp(resp, keys=["accounts", "na", "cf", "commit_mode"])
    if resp.status_code != 200:
        raise Fail("mapping/commit failed -- CoA must land before GL commit (FK pre-flight)")
    if not isinstance(body, dict) or int(body.get("accounts", 0)) <= 0:
        raise Fail("mapping/commit reported 0 accounts written")
    return body


def commit_gl(api: Api, file_id: str, exclude_line_ids: list[str]) -> dict:
    step("Commit GL (POST /api/v1/ingest/commit, dataset=gl, confirm_soft=true)")
    resp = api.post("/api/v1/ingest/commit", json_body={
        "file_id": file_id,
        "profile": fx.GL_PROFILE,
        "dataset": "gl",
        "confirm_soft": True,
        "exclude_line_ids": exclude_line_ids,
        "commit_mode": "replace",
    })
    body = show_resp(resp, keys=["entries", "lines", "ar", "ap", "skipped", "unchanged"])
    if resp.status_code != 200:
        # Surface the unmapped-accounts recovery hint if that's the cause.
        if resp.status_code == 422 and isinstance(body, dict):
            detail = body.get("detail")
            if isinstance(detail, dict) and detail.get("code") == "gl_accounts_unmapped":
                raise Fail("GL commit: accounts unmapped -- CoA commit did not cover every GL account")
        raise Fail("GL commit failed")
    if int(body.get("lines", 0)) <= 0 and not body.get("unchanged"):
        raise Fail("GL commit wrote 0 lines")
    return body


def detect_unknowns(api: Api, label: str) -> dict:
    step(f"Detect unknown positions ({label}) (POST /api/v1/ingest/structure/unknown-positions)")
    resp = api.post("/api/v1/ingest/structure/unknown-positions", json_body={
        "project_id": "default",
        "fiscal_years": fx.FISCAL_YEARS,
        "entity_prefixes": [fx.ENTITY_PREFIX],
    })
    body = show_resp(resp, keys=["total", "counts_by_statement", "structure_available"])
    if resp.status_code != 200:
        raise Fail("structure/unknown-positions failed")
    if not body.get("structure_available", False):
        info("WARNING: structure_available=False -- the seed structure tables "
             "(dim_pl_structure / dim_bs_structure) are NOT populated in this DB. "
             "Auto-extension cannot be demonstrated. Seed the structure first "
             "(scripts/seed_pl_structure.py + seed_bs_structure.py) -- see README.")
    for p in body.get("positions", []):
        info(f"  unknown: stmt={p.get('statement')} L2={p.get('level_2')!r} "
             f"L3={p.get('level_3')!r} count={p.get('account_count')} "
             f"after={p.get('suggested_after_line_code')!r}")
    return body


def build_placements(unknown_body: dict) -> list[dict]:
    """Turn the detected NOVEL positions into structure/extend placements.

    Only the positions whose grain level_2 matches an injected novel label are
    placed -- mirrors the wizard's per-position placement, echoing the position's
    own statement + level_2/3/4 and its suggested anchor.
    """
    novel_by_l2 = {p["level_2"]: p for p in fx.NOVEL_POSITIONS}
    placements: list[dict] = []
    for pos in unknown_body.get("positions", []):
        l2 = pos.get("level_2")
        if l2 not in novel_by_l2:
            continue
        stmt = pos.get("suggested_statement") or pos.get("statement") or "PL"
        placement = {
            "statement": stmt,
            "level_2": pos.get("level_2"),
            "level_3": pos.get("level_3"),
            "level_4": pos.get("level_4"),
            "row_type": "mapping",
            "balance_title": pos.get("level_3") or pos.get("level_2"),
        }
        after = pos.get("suggested_after_line_code")
        if after:
            placement["after_line_code"] = after
        if stmt == "BS":
            placement["section"] = novel_by_l2[l2].get("section") or "asset"
        placements.append(placement)
    return placements


def extend_structure(api: Api, placements: list[dict]) -> dict:
    step("Place novel positions (POST /api/v1/ingest/structure/extend)")
    if not placements:
        raise Fail("no placements built -- novel positions were not detected as unknown")
    info(f"placing {len(placements)} novel position(s): "
         + ", ".join(f"{p['statement']}:{p['level_2']}" for p in placements))
    resp = api.post("/api/v1/ingest/structure/extend", json_body={
        "project_id": "default",
        "placements": placements,
    })
    body = show_resp(resp, keys=["inserted", "skipped", "table_counts"])
    if resp.status_code != 200:
        raise Fail("structure/extend failed")
    if not body.get("inserted") and not body.get("skipped"):
        raise Fail("structure/extend inserted/skipped nothing")
    return body


def read_reporting(api: Api) -> dict:
    """Read the reporting surfaces the wizard/UI use. Returns a context dict."""
    out: dict[str, Any] = {}

    # current_fy = last FY; full year closed so coverage=1.0
    current_fy = fx.FISCAL_YEARS[-1]
    params = {
        "view_mode": "year",
        "current_fy": current_fy,
        "last_closed_period": 12,
        "fy_start_month": 1,
    }

    step("Read P&L (GET /api/v1/statements/pl)")
    resp = api.get("/api/v1/statements/pl", params=params)
    pl = show_resp(resp, keys=["view_mode", "coverage", "columns"])
    out["pl_status"] = resp.status_code
    out["pl"] = pl if resp.status_code == 200 else None

    step("Read Balance Sheet (GET /api/v1/statements/bs)")
    resp = api.get("/api/v1/statements/bs", params=params)
    bs = show_resp(resp, keys=["view_mode", "coverage", "columns", "imbalance"])
    out["bs_status"] = resp.status_code
    out["bs"] = bs if resp.status_code == 200 else None

    # fin_compat surface -- this is where has_plan_data / forecast columns live
    # (out["has_plan_data"] = bool(plan_map) in fin_compat_pl.py). Params:
    # period_grain=year still REQUIRES both year AND month (_validate_period);
    # month=12 selects the closed December anchor. Best-effort: log but do not
    # hard-fail if the surface/params differ (A7 falls back to /statements columns).
    step("Read fin-compat P&L for has_plan_data (GET /api/v1/financials/pl-statement)")
    fc_params = {
        "period_grain": "year",
        "year": current_fy,
        "month": 12,
        "entity": fx.ENTITY_PREFIX,
    }
    resp = api.get("/api/v1/financials/pl-statement", params=fc_params)
    fc = show_resp(resp, keys=["has_plan_data", "columns", "coverage"])
    out["fincompat_status"] = resp.status_code
    out["fincompat_pl"] = fc if resp.status_code == 200 else None
    if resp.status_code != 200:
        info("NOTE: /financials/pl-statement not reachable with these params -- "
             "the has_plan_data assertion will be reported as SKIPPED. "
             "See README 'endpoints I was unsure about'.")
    return out


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run(args: argparse.Namespace) -> dict:
    fixture_dir = Path(args.fixture_dir).resolve()

    # (Re)generate the fixture unless told to reuse an existing one.
    if not args.no_gen:
        step("Generate synthetic fixture (deterministic)")
        fixture_dir.mkdir(parents=True, exist_ok=True)
        fx.write_all_gl(fixture_dir)
        fx.write_coa_xlsx(fixture_dir / fx.COA_XLSX_NAME)
        info(f"fixture written to {fixture_dir}")
        info(f"novel grains: {fx.NOVEL_LEVEL2_LABELS}")

    api = Api(args.api_base, token=args.token)
    if not args.token:
        login(api, args.email, args.password)

    ctx: dict[str, Any] = {"fixture_dir": str(fixture_dir)}

    inputs = upload_gl_years(api, fixture_dir)
    combined_id = combine_gl(api, inputs)
    exclude_ids = validate_gl(api, combined_id)

    coa_id = upload_coa(api, fixture_dir)
    ctx["coa_commit"] = commit_coa(api, coa_id)
    ctx["gl_commit"] = commit_gl(api, combined_id, exclude_ids)

    ctx["unknown_before"] = detect_unknowns(api, "after commits, before extend")
    placements = build_placements(ctx["unknown_before"])
    ctx["placements"] = placements
    ctx["extend"] = extend_structure(api, placements)
    ctx["unknown_after"] = detect_unknowns(api, "after extend")

    ctx.update(read_reporting(api))
    return ctx


def main() -> int:
    ap = argparse.ArgumentParser(description="v5 pipeline E2E smoke driver (real HTTP).")
    ap.add_argument("--api-base", required=True, help="e.g. http://127.0.0.1:8016")
    ap.add_argument("--email", help="login email (POST /api/v1/auth/login)")
    ap.add_argument("--password", help="login password")
    ap.add_argument("--token", help="pre-obtained bearer token (skips login)")
    ap.add_argument(
        "--fixture-dir",
        default=str(Path(__file__).resolve().parent / "_fixture"),
        help="where fixture files are written/read",
    )
    ap.add_argument("--no-gen", action="store_true",
                    help="reuse an existing fixture instead of regenerating")
    ap.add_argument("--no-assert", action="store_true",
                    help="run the pipeline but skip the assertion phase")
    args = ap.parse_args()

    if not args.token and not (args.email and args.password):
        ap.error("provide --token OR both --email and --password")

    try:
        ctx = run(args)
    except Fail as exc:
        print(f"\nPIPELINE STOPPED: {exc}", flush=True)
        return 2
    except requests.RequestException as exc:
        print(f"\nHTTP ERROR: {exc}", flush=True)
        return 3

    if args.no_assert:
        print("\nPipeline finished; assertions skipped (--no-assert).")
        return 0

    import assert_pipeline_e2e as asserts
    ok = asserts.run_assertions(ctx)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
