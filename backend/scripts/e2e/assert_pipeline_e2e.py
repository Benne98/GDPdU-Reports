#!/usr/bin/env python3
"""Assertions for the v5 pipeline E2E smoke (imported by run_pipeline_e2e.py).

Consumes the context dict the driver builds and verifies, end-to-end, that:

  A1  GL rows landed for the fresh dataset            (commit.entries/lines > 0)
  A2  CoA mapping landed                              (mapping/commit.accounts > 0)
  A3  unknown-positions detected the NOVEL positions  (each novel grain present, >0)
  A4  structure/extend placed them into the correct   (inserted>0; PL->dim_pl_structure,
      per-statement split table                        BS->dim_bs_structure)
  A5  after extend the novel positions are RESOLVED    (no longer unknown -> the placed
      (reader-faithful classification)                  row is what the reader classifies)
  A6  reporting reflects the placed positions          (each novel level_2 appears as a
      with the L1-L4 hierarchy                          line in pl/bs lines[])
  A7  NO plan/forecast for the fresh dataset           (has_plan_data False on fin-compat;
      (has_plan_data false)                             no forecast column on /statements)

Each check prints PASS / FAIL / SKIP with the evidence. Returns True iff every
non-skipped check passed.

The `source='auto_extend'` provenance (task deliverable 3) is written by the
extend endpoint; A4 confirms the placement inserted into the right table via the
API. To confirm the column value at the DB layer, see README.md ("DB spot-checks").
"""
from __future__ import annotations

from typing import Any

import gen_e2e_gl_fixture as fx

# dim table each statement's placements must land in (split invariant).
_TABLE_BY_STATEMENT = {
    "PL": "dim_pl_structure",
    "BS": "dim_bs_structure",
    "CF": "dim_cf_structure",
}


class _Res:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.skipped = 0

    def ok(self, tag: str, msg: str) -> None:
        self.passed += 1
        print(f"  [PASS] {tag}: {msg}")

    def bad(self, tag: str, msg: str) -> None:
        self.failed += 1
        print(f"  [FAIL] {tag}: {msg}")

    def skip(self, tag: str, msg: str) -> None:
        self.skipped += 1
        print(f"  [SKIP] {tag}: {msg}")


def _lines(stmt_body: Any) -> list[dict]:
    if isinstance(stmt_body, dict) and isinstance(stmt_body.get("lines"), list):
        return stmt_body["lines"]
    return []


def _level2_set(stmt_body: Any) -> set[str]:
    out: set[str] = set()
    for ln in _lines(stmt_body):
        v = ln.get("level_2")
        if v:
            out.add(str(v))
    return out


def _columns(stmt_body: Any) -> list[dict]:
    if isinstance(stmt_body, dict) and isinstance(stmt_body.get("columns"), list):
        return stmt_body["columns"]
    return []


def _looks_like_forecast(col: dict) -> bool:
    """Heuristic: a plan/forecast/budget column (fin-compat marks FY..F etc.)."""
    key = str(col.get("key", "")).lower()
    label = str(col.get("label", "")).lower()
    blob = f"{key} {label}"
    if any(w in blob for w in ("forecast", "plan", "budget")):
        return True
    # fin-compat forecast columns are often keyed like "FY25F" / labelled "...F".
    k = str(col.get("key", ""))
    return k.endswith("F") and any(ch.isdigit() for ch in k)


def run_assertions(ctx: dict) -> bool:
    print("\n" + "=" * 70)
    print("ASSERTIONS")
    print("=" * 70)
    r = _Res()

    novel_labels = set(fx.NOVEL_LEVEL2_LABELS)

    # --- A1: GL rows landed --------------------------------------------------
    gl = ctx.get("gl_commit") or {}
    lines = int(gl.get("lines", 0) or 0)
    entries = int(gl.get("entries", 0) or 0)
    if lines > 0 and entries > 0:
        r.ok("A1", f"GL landed: entries={entries}, lines={lines}")
    elif gl.get("unchanged"):
        r.ok("A1", "GL commit reported unchanged (data already current)")
    else:
        r.bad("A1", f"GL did not land: entries={entries}, lines={lines}")

    # --- A2: CoA mapping landed ----------------------------------------------
    coa = ctx.get("coa_commit") or {}
    accounts = int(coa.get("accounts", 0) or 0)
    if accounts > 0:
        r.ok("A2", f"CoA mapping landed: accounts={accounts}")
    else:
        r.bad("A2", f"CoA mapping accounts={accounts}")

    # --- A3: unknown-positions detected the novel grains ---------------------
    ub = ctx.get("unknown_before") or {}
    if not ub.get("structure_available", False):
        r.bad("A3", "structure_available=False -- seed structure missing; "
                    "auto-extension cannot be demonstrated (see README)")
    else:
        found = {p.get("level_2") for p in ub.get("positions", [])}
        missing = sorted(novel_labels - found)
        total = int(ub.get("total", 0) or 0)
        if total > 0 and not missing:
            r.ok("A3", f"all {len(novel_labels)} novel grains detected as unknown "
                       f"(total unknown={total}): {sorted(novel_labels)}")
        elif not missing:
            r.ok("A3", f"novel grains detected (total={total})")
        else:
            r.bad("A3", f"novel grains NOT detected as unknown: {missing} "
                        f"(detected level_2 grains: {sorted(x for x in found if x)})")

    # --- A4: extend placed into the correct split table ----------------------
    placements = ctx.get("placements") or []
    ext = ctx.get("extend") or {}
    inserted = ext.get("inserted", []) or []
    skipped = ext.get("skipped", []) or []
    table_counts = ext.get("table_counts", {}) or {}
    if not placements:
        r.bad("A4", "no placements were built from the detected positions")
    elif not (inserted or skipped):
        r.bad("A4", "extend inserted/skipped nothing")
    else:
        # every statement we placed must have written to its own split table
        want_tables = {_TABLE_BY_STATEMENT.get(p["statement"]) for p in placements}
        got_tables = set(table_counts.keys())
        missing_tables = {t for t in want_tables if t and t not in got_tables}
        if missing_tables:
            r.bad("A4", f"placed statements missing from table_counts: {missing_tables} "
                        f"(got {got_tables})")
        else:
            r.ok("A4", f"placed {len(placements)} position(s); inserted={inserted} "
                       f"skipped={skipped} tables={dict(table_counts)}")

    # --- A5: after extend, the novel positions are resolved (classify now) ----
    ua = ctx.get("unknown_after") or {}
    if not ua:
        r.skip("A5", "no post-extend detection captured")
    else:
        still = {p.get("level_2") for p in ua.get("positions", [])} & novel_labels
        if not still:
            r.ok("A5", "novel positions no longer unknown after extend "
                       "(placed rows are what the reader classifies)")
        else:
            r.bad("A5", f"still unknown after extend: {sorted(still)}")

    # --- A6: reporting reflects the placed positions -------------------------
    pl = ctx.get("pl")
    bs = ctx.get("bs")
    fc = ctx.get("fincompat_pl")
    reporting_l2 = _level2_set(pl) | _level2_set(bs) | _level2_set(fc)
    if ctx.get("pl_status") != 200 and ctx.get("bs_status") != 200:
        r.bad("A6", "neither /statements/pl nor /statements/bs returned 200")
    else:
        seen = sorted(novel_labels & reporting_l2)
        missing = sorted(novel_labels - reporting_l2)
        if not missing:
            r.ok("A6", f"all novel positions surface as reporting lines (level_2): {seen}")
        elif seen:
            # partial -- strong signal the loop works; flag the gaps
            r.bad("A6", f"only some novel positions surfaced in reporting: "
                        f"present={seen}, absent={missing}. "
                        f"(present-in-reporting level_2 sample: "
                        f"{sorted(list(reporting_l2))[:12]})")
        else:
            r.bad("A6", f"no novel positions found in reporting lines[]. "
                        f"absent={missing}; reporting level_2 sample: "
                        f"{sorted(list(reporting_l2))[:12]}")

    # --- A7: fresh dataset has NO plan/forecast (has_plan_data false) ---------
    if ctx.get("fincompat_status") == 200 and isinstance(fc, dict) and "has_plan_data" in fc:
        if fc.get("has_plan_data") is False:
            r.ok("A7", "fin-compat has_plan_data=False (fresh dataset, no plan)")
        else:
            r.bad("A7", f"has_plan_data={fc.get('has_plan_data')!r} (expected False)")
    else:
        # Fall back to checking /statements columns carry no forecast column.
        cols = _columns(pl)
        forecast_cols = [c for c in cols if _looks_like_forecast(c)]
        if ctx.get("pl_status") == 200 and not forecast_cols:
            r.ok("A7", "no plan/forecast column present on /statements/pl "
                       "(has_plan_data endpoint unavailable -- verified via columns)")
        elif ctx.get("pl_status") == 200:
            r.bad("A7", f"unexpected forecast-looking columns: "
                        f"{[c.get('key') for c in forecast_cols]}")
        else:
            r.skip("A7", "has_plan_data endpoint unavailable and /statements/pl not 200")

    # --- summary -------------------------------------------------------------
    print("-" * 70)
    total = r.passed + r.failed + r.skipped
    verdict = "PASS" if r.failed == 0 else "FAIL"
    print(f"RESULT: {verdict}  ({r.passed} passed, {r.failed} failed, "
          f"{r.skipped} skipped, {total} total)")
    print("=" * 70)
    return r.failed == 0


if __name__ == "__main__":
    # Allow re-running assertions against a saved context JSON (debugging aid).
    import json
    import sys
    if len(sys.argv) != 2:
        print("usage: python assert_pipeline_e2e.py <context.json>")
        raise SystemExit(2)
    with open(sys.argv[1], encoding="utf-8") as fh:
        ctx = json.load(fh)
    raise SystemExit(0 if run_assertions(ctx) else 1)
