"""Golden-snapshot harness for the reporting-v2 equivalence gate (Phase 1).

PURPOSE
───────
Phase 1 of the reporting-v2 plan is a *behaviour-preserving* refactor: moving the
post-load derivation logic out of ``backend/scripts/derive_facts.py`` into a
reusable ``etl/rebuild.py`` orchestrator, wired behind the ``REBUILD_ON_COMMIT``
flag (default OFF).  To prove the refactor changes nothing, we capture the LIVE
reporting outputs the frontend consumes, then capture the same outputs from the v2
stack after running the new rebuild on the cloned DB, and diff them numerically.

This script does THREE things:

  capture   Log in, discover entities + the latest period, and write every
            frontend-facing reporting payload to JSON under
            ``backend/golden/<capture-name>/``.  Deterministic (sorted keys,
            rounded floats) so two captures diff cleanly.

  compare   Numerically diff two capture directories with a small float epsilon.
            Exit code is non-zero on a material difference.

  (default)  ``capture`` is the default sub-command.

USAGE
─────
  # capture the live stack (8010) into backend/golden/live/
  $env:DB_PASSWORD='...'  # not needed for capture (HTTP only), kept for symmetry
  python backend/scripts/golden_snapshot.py capture --base-url http://127.0.0.1:8010 --name live

  # capture the v2 stack (8011) into backend/golden/v2/
  python backend/scripts/golden_snapshot.py capture --base-url http://127.0.0.1:8011 --name v2

  # diff them (non-zero exit on material difference)
  python backend/scripts/golden_snapshot.py compare live v2

CREDENTIALS
───────────
The ``capture`` sub-command logs in over HTTP and REQUIRES credentials — supply
them via ``--email`` / ``--password`` or env ``GOLDEN_EMAIL`` / ``GOLDEN_PASSWORD``.
There are NO committed defaults (no literal email/password in this file).  The
``compare`` sub-command never logs in, so it needs no credentials.

DESIGN NOTES
────────────
- HTTP only — never touches the database directly, so it is safe against the
  live stack (read-only by construction).
- The endpoint catalogue mirrors the real compat routes in
  backend/app/routers/financials_compat.py and sales_compat.py (NOT the plan's
  shorthand names — e.g. the BS route is ``/balance-sheet`` not ``/bs-statement``).
- Narrative / LLM endpoints are intentionally EXCLUDED: they are non-deterministic
  (timestamps, optional LLM text) and not part of the derive path under test.
- Float rounding to 6 dp on write keeps the JSON stable; ``compare`` additionally
  tolerates a 1e-6 absolute / relative epsilon.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parent.parent
GOLDEN_ROOT = _BACKEND / "golden"

DEFAULT_BASE_URL = "http://127.0.0.1:8010"
# No committed credential defaults: ``capture`` requires GOLDEN_EMAIL/GOLDEN_PASSWORD
# (or --email/--password); ``compare`` never logs in.  Empty string => "unset".
DEFAULT_EMAIL = os.getenv("GOLDEN_EMAIL", "")
DEFAULT_PASSWORD = os.getenv("GOLDEN_PASSWORD", "")

# Absolute/relative tolerance for numeric comparison.
EPSILON = 1e-6

_FLOAT_ROUND_DP = 6


# --------------------------------------------------------------------------- #
# HTTP helpers (stdlib only — no extra deps in the backend venv required)
# --------------------------------------------------------------------------- #
def _login(base_url: str, email: str, password: str) -> str:
    url = f"{base_url.rstrip('/')}/api/v1/auth/login"
    body = json.dumps({"email": email, "password": password}).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode())
    token = payload.get("access_token")
    if not token:
        raise RuntimeError(f"login to {base_url} returned no access_token")
    return token


def _get(base_url: str, path: str, token: str, params: dict | None = None) -> Any:
    rel = path if path.startswith("/") else f"/{path}"
    url = f"{base_url.rstrip('/')}{rel}"
    if params:
        from urllib.parse import urlencode

        clean = {k: v for k, v in params.items() if v is not None and v != ""}
        if clean:
            url = f"{url}?{urlencode(clean)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        return {"__http_error__": exc.code, "__detail__": detail, "__path__": path,
                "__params__": params or {}}


# --------------------------------------------------------------------------- #
# Determinism: round floats + sort keys so two captures diff cleanly
# --------------------------------------------------------------------------- #
def _canonical(obj: Any) -> Any:
    """Recursively round floats and sort dict keys for stable serialization."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return round(obj, _FLOAT_ROUND_DP)
    if isinstance(obj, dict):
        return {k: _canonical(obj[k]) for k in sorted(obj.keys(), key=str)}
    if isinstance(obj, (list, tuple)):
        return [_canonical(v) for v in obj]
    return obj


# --------------------------------------------------------------------------- #
# Endpoint catalogue
# --------------------------------------------------------------------------- #
_STATEMENTS = {
    "pl": "/api/v1/financials/pl-statement",
    "bs": "/api/v1/financials/balance-sheet",
    "wc": "/api/v1/financials/working-capital",
    "cf": "/api/v1/financials/cash-flow",
}

# Endpoints that take an `entity` query param (None => consolidated).
_CONSOLIDATION = {
    "pl": "/api/v1/financials/pl-statement/consolidation",
    "bs": "/api/v1/financials/balance-sheet/consolidation",
    "wc": "/api/v1/financials/working-capital/consolidation",
    "cf": "/api/v1/financials/cash-flow/consolidation",
}

_MONTHLY = {
    "pl": "/api/v1/financials/pl-statement/monthly",
    "bs": "/api/v1/financials/balance-sheet/monthly",
    "wc": "/api/v1/financials/working-capital/monthly",
    "cf": "/api/v1/financials/cash-flow/monthly",
}

# Weekly breakdowns only exist for PL and CF (per the compat router).
_WEEKLY = {
    "pl": "/api/v1/financials/pl-statement/weekly",
    "cf": "/api/v1/financials/cash-flow/weekly",
}


def _slug(s: str | None) -> str:
    return "all" if s is None else f"entity-{s}"


def _capture_financials(base_url: str, token: str, anchor: dict, entities: list[str]) -> dict:
    """Capture every frontend-facing financials payload for the anchor period."""
    out: dict[str, Any] = {}
    year = anchor["year"]
    month = anchor["month"]
    iso_year = anchor["iso_year"]
    iso_week = anchor["iso_week"]

    entity_scopes: list[str | None] = [None] + list(entities)

    for stmt, path in _STATEMENTS.items():
        # grain=month + grain=year (both consolidated and per entity)
        for grain in ("month", "year"):
            for ent in entity_scopes:
                key = f"{stmt}/statement/{grain}/{_slug(ent)}"
                out[key] = _get(base_url, path, token, {
                    "period_grain": grain, "year": year, "month": month, "entity": ent,
                })
        # grain=week (consolidated and per entity)
        for ent in entity_scopes:
            key = f"{stmt}/statement/week/{_slug(ent)}"
            out[key] = _get(base_url, path, token, {
                "period_grain": "week", "iso_year": iso_year, "iso_week": iso_week,
                "entity": ent,
            })

    # consolidation views (no entity param; consolidated by definition)
    for stmt, path in _CONSOLIDATION.items():
        for grain in ("month", "year"):
            out[f"{stmt}/consolidation/{grain}"] = _get(base_url, path, token, {
                "period_grain": grain, "year": year, "month": month,
            })
        out[f"{stmt}/consolidation/week"] = _get(base_url, path, token, {
            "period_grain": "week", "iso_year": iso_year, "iso_week": iso_week,
        })

    # monthly sparkline views (entity-scoped)
    for stmt, path in _MONTHLY.items():
        for ent in entity_scopes:
            out[f"{stmt}/monthly/{_slug(ent)}"] = _get(base_url, path, token, {
                "year": year, "month": month, "entity": ent,
            })

    # weekly breakdowns (PL, CF only; entity-scoped)
    for stmt, path in _WEEKLY.items():
        for ent in entity_scopes:
            out[f"{stmt}/weekly-breakdown/{_slug(ent)}"] = _get(base_url, path, token, {
                "iso_year": iso_year, "iso_week": iso_week, "entity": ent,
            })

    return out


def _capture_sales(base_url: str, token: str, anchor: dict, entities: list[str]) -> dict:
    """Capture a deterministic, representative subset of sales endpoints."""
    out: dict[str, Any] = {}
    year = anchor["year"]
    month = anchor["month"]
    entity_scopes: list[str | None] = [None] + list(entities)

    for ent in entity_scopes:
        slug = _slug(ent)
        # top entities (customers + suppliers)
        for typ in ("customer", "supplier"):
            out[f"sales/top-entities/{typ}/{slug}"] = _get(
                base_url, "/api/v1/sales/top-entities", token,
                {"year": year, "month": month, "type": typ, "entity": ent},
            )
        out[f"sales/headline-kpis/{slug}"] = _get(
            base_url, "/api/v1/sales/headline-kpis", token,
            {"year": year, "month": month, "entity": ent},
        )
        out[f"sales/receivables-aging/{slug}"] = _get(
            base_url, "/api/v1/sales/receivables-aging", token,
            {"year": year, "month": month, "entity": ent},
        )
        out[f"sales/payables-aging/{slug}"] = _get(
            base_url, "/api/v1/sales/payables-aging", token,
            {"year": year, "month": month, "entity": ent},
        )
        out[f"sales/geography/countries/{slug}"] = _get(
            base_url, "/api/v1/sales/geography/countries", token,
            {"year": year, "month": month, "entity": ent},
        )
        out[f"sales/analytics/composition/{slug}"] = _get(
            base_url, "/api/v1/sales/analytics/composition-breakdown", token,
            {"year": year, "month": month, "metric": "gross_sales", "entity": ent},
        )

    return out


# --------------------------------------------------------------------------- #
# capture sub-command
# --------------------------------------------------------------------------- #
def cmd_capture(args: argparse.Namespace) -> int:
    base_url = args.base_url
    if not args.email or not args.password:
        print(
            "[capture] ERROR: credentials required. Set GOLDEN_EMAIL and "
            "GOLDEN_PASSWORD (or pass --email/--password). 'capture' logs in over "
            "HTTP; 'compare' needs no credentials.",
            file=sys.stderr,
        )
        return 2
    token = _login(base_url, args.email, args.password)

    entities_raw = _get(base_url, "/api/v1/entities", token)
    if not isinstance(entities_raw, list):
        raise RuntimeError(f"/api/v1/entities returned {entities_raw!r}")
    entities = sorted(
        e["legal_entity_code"] for e in entities_raw
        if not e.get("is_consolidation")
    )

    latest = _get(base_url, "/api/v1/metrics", token, {"metric": "latest_period"})
    anchor = {
        "year": latest.get("year"),
        "month": latest.get("month"),
        "iso_year": latest.get("iso_year"),
        "iso_week": latest.get("iso_week"),
    }
    if anchor["year"] is None or anchor["month"] is None:
        raise RuntimeError(f"latest_period returned no usable anchor: {latest!r}")

    print(f"[capture] base_url={base_url}  entities={entities}  anchor={anchor}")

    payloads: dict[str, Any] = {}
    payloads["_meta/entities"] = entities_raw
    payloads["_meta/latest_period"] = latest
    payloads.update(_capture_financials(base_url, token, anchor, entities))
    payloads.update(_capture_sales(base_url, token, anchor, entities))

    out_dir = GOLDEN_ROOT / args.name
    out_dir.mkdir(parents=True, exist_ok=True)

    n_written = 0
    n_errors = 0
    for key, data in payloads.items():
        if isinstance(data, dict) and "__http_error__" in data:
            n_errors += 1
            print(f"[capture]  ! {key}: HTTP {data['__http_error__']} {data.get('__detail__','')[:120]}")
        fname = key.replace("/", "__") + ".json"
        path = out_dir / fname
        path.write_text(
            json.dumps(_canonical(data), indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )
        n_written += 1

    print(f"[capture] wrote {n_written} payloads to {out_dir}  ({n_errors} endpoint errors)")
    return 0


# --------------------------------------------------------------------------- #
# compare sub-command
# --------------------------------------------------------------------------- #
def _numbers_differ(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a != b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if a == b:
            return False
        diff = abs(a - b)
        scale = max(abs(a), abs(b), 1.0)
        return diff > EPSILON and (diff / scale) > EPSILON
    return a != b


# Keys whose VALUES are synthetic per-request identifiers (not data): the compat
# layer mints row ids like ``pl-NET_SALES-l4-89477`` where the numeric suffix is a
# per-process hash, so two independent server processes emit different ids for the
# SAME logical row.  These are ignored in the financial-equivalence comparison.
_VOLATILE_KEYS = frozenset({"id"})

# Candidate keys used to identify list elements for order-insensitive matching.
# Statement/account rows are returned in a SQL order that is not always fully
# tie-broken, so sibling arrays can be permuted between processes while carrying
# identical data.  We match on a stable business key when present.
_LIST_KEY_CANDIDATES = ("line_code", "gl_account_id", "code", "key", "band", "country", "name", "label")


def _list_key(elem: Any) -> Any:
    """Return a stable identity for a list element, or None if it has none."""
    if isinstance(elem, dict):
        for k in _LIST_KEY_CANDIDATES:
            if k in elem and isinstance(elem[k], (str, int, float)):
                return (k, elem[k])
        drill = elem.get("drill")
        if isinstance(drill, dict) and drill.get("gl_account_id") is not None:
            return ("drill.gl_account_id", drill["gl_account_id"])
    return None


def _diff(a: Any, b: Any, path: str, diffs: list[str]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k in _VOLATILE_KEYS:
                continue  # synthetic per-request identifier — not data
            if k not in a:
                diffs.append(f"{path}.{k}: only in B")
            elif k not in b:
                diffs.append(f"{path}.{k}: only in A")
            else:
                _diff(a[k], b[k], f"{path}.{k}", diffs)
        return
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            diffs.append(f"{path}: list length {len(a)} != {len(b)}")
            return
        # Try order-insensitive matching when every element has a stable key.
        keys_a = [_list_key(x) for x in a]
        keys_b = [_list_key(x) for x in b]
        if (
            a
            and all(k is not None for k in keys_a)
            and all(k is not None for k in keys_b)
            and len(set(keys_a)) == len(keys_a)
            and set(keys_a) == set(keys_b)
        ):
            b_by_key = {k: el for k, el in zip(keys_b, b)}
            for x, kx in zip(a, keys_a):
                _diff(x, b_by_key[kx], f"{path}[{kx[1]}]", diffs)
            return
        # Fallback: positional comparison.
        for i, (x, y) in enumerate(zip(a, b)):
            _diff(x, y, f"{path}[{i}]", diffs)
        return
    if _numbers_differ(a, b):
        diffs.append(f"{path}: {a!r} != {b!r}")


def cmd_compare(args: argparse.Namespace) -> int:
    dir_a = GOLDEN_ROOT / args.dir_a
    dir_b = GOLDEN_ROOT / args.dir_b
    if not dir_a.is_dir():
        print(f"[compare] ERROR: {dir_a} not found")
        return 2
    if not dir_b.is_dir():
        print(f"[compare] ERROR: {dir_b} not found")
        return 2

    files_a = {p.name for p in dir_a.glob("*.json")}
    files_b = {p.name for p in dir_b.glob("*.json")}

    all_diffs: list[str] = []

    only_a = sorted(files_a - files_b)
    only_b = sorted(files_b - files_a)
    for f in only_a:
        all_diffs.append(f"FILE only in {args.dir_a}: {f}")
    for f in only_b:
        all_diffs.append(f"FILE only in {args.dir_b}: {f}")

    common = sorted(files_a & files_b)
    files_with_diffs = 0
    for f in common:
        da = json.loads((dir_a / f).read_text(encoding="utf-8"))
        db = json.loads((dir_b / f).read_text(encoding="utf-8"))
        file_diffs: list[str] = []
        _diff(da, db, f[:-5], file_diffs)
        if file_diffs:
            files_with_diffs += 1
            all_diffs.extend(file_diffs)

    print(f"[compare] {args.dir_a} vs {args.dir_b}")
    print(f"[compare] common files: {len(common)}  files-with-diffs: {files_with_diffs}")
    if not all_diffs:
        print("[compare] RESULT: EQUIVALENT (no material differences within epsilon)")
        return 0

    print(f"[compare] RESULT: {len(all_diffs)} material difference(s):")
    shown = all_diffs[: args.max_diffs]
    for d in shown:
        print(f"  - {d}")
    if len(all_diffs) > len(shown):
        print(f"  ... and {len(all_diffs) - len(shown)} more")
    return 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Golden-snapshot reporting harness (Phase 1 gate).")
    sub = parser.add_subparsers(dest="cmd")

    p_cap = sub.add_parser("capture", help="Capture reporting outputs from a running stack.")
    p_cap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p_cap.add_argument("--name", required=True, help="Subdir under backend/golden/ to write into.")
    p_cap.add_argument("--email", default=DEFAULT_EMAIL)
    p_cap.add_argument("--password", default=DEFAULT_PASSWORD)
    p_cap.set_defaults(func=cmd_capture)

    p_cmp = sub.add_parser("compare", help="Diff two capture directories under backend/golden/.")
    p_cmp.add_argument("dir_a")
    p_cmp.add_argument("dir_b")
    p_cmp.add_argument("--max-diffs", type=int, default=100)
    p_cmp.set_defaults(func=cmd_compare)

    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
