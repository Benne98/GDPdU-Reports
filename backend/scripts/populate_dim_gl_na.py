r"""Re-derive ``dim_gl_na`` from the NA mapping library, with a TOTALS-GUARD.

For every (account_number_group, fiscal_year) this sets the NA classification
``(l6_na_mapping, l7_na_description)`` to:

    ovr_na_mapping if a row exists for that (account, fiscal_year) [PRECEDENCE]
    else  most-frequent mapping for the account's account_name
          (etl.mapping_library.resolve.resolve_most_frequent — argmax occurrences,
           deterministic lexicographic tiebreaker for 50/50 splits).

TOTALS-GUARD (the safety property)
----------------------------------
Sub-line reclassification is allowed, but NO roll-up TOTAL may change.  Two
totals depend on the NA classification:

  (1) Working-capital totals (NWC + every WC subtotal).  The WC statement
      (``fin_compat_wc_sql``) selects ``WHERE na.l6_na_mapping IN ('TWC','OWC')``
      and NWC = Σ(TWC) + Σ(OWC).  ⇒ a total changes iff an account's WC
      MEMBERSHIP flips: ``(l6 ∈ {TWC,OWC})`` differs between current and resolved.

  (2) Cash-flow totals (every CF subtotal/grandtotal).  CF reads
      ``dim_gl_cf.cf_mapping`` whose band is the ``lib_cf_mapping`` row joined
      on the NA ``(l6_na_mapping, l7_na_description)``.  ⇒ a CF subtotal changes
      iff the resolved mapping joins to a DIFFERENT CF band ``(l1, l2)`` than the
      current mapping (the l1/l2 are the subtotal/grandtotal grouping levels).

For ANY account whose resolved mapping would trip (1) OR (2), this script
AUTO-INSERTS an ``ovr_na_mapping`` pinning it to its CURRENT ``(l6, l7)`` with
``source='totals_guard'`` and uses that.  Result: the 16 "safe" ambiguous names
reshuffle freely (their resolution does not move a total), while the boundary
crossers (incl. the 3 known loan accounts) are pinned — NWC + every CF total stay
identical to the cent.

WORKED EXAMPLE
--------------
Account ``03026135`` ("Darlehen Arbeitnehm."): current = ND / "Loan to employees"
(NOT in WC).  The name's most-frequent mapping is OWC / "Other assets"
(occurrences 8 > 4).  Resolving blindly would move it ND→OWC ⇒ it ENTERS working
capital ⇒ NWC changes.  The guard detects the WC-membership flip, writes an
``ovr_na_mapping`` pinning ``03026135`` to ND / "Loan to employees", and the account
keeps its current classification — NWC is unchanged.

Run it identically on BOTH databases (finssentials_v2 AND Finssentials/live):
this script, then ``populate_dim_gl_cf.py`` (re-run automatically here unless
``--skip-cf``).  The golden ``compare live v2`` stays EQUIVALENT because both DBs
are re-derived the same way.

USAGE
-----
  $env:DB_PASSWORD='...'; $env:DB_NAME='finssentials_v2'
  backend\.venv\Scripts\python.exe backend\scripts\populate_dim_gl_na.py
  # preview the guard decisions, write nothing:
  ... populate_dim_gl_na.py --dry-run
  # skip the automatic dim_gl_cf re-derive:
  ... populate_dim_gl_na.py --skip-cf
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
_REPO = _BACKEND.parent
for _p in (str(_BACKEND), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sqlalchemy import text
from sqlalchemy.orm import Session as SASession

from app.db import engine
from etl.mapping_library.resolve import resolve_most_frequent

_WC = {"TWC", "OWC"}


# ---------------------------------------------------------------------------
# Pure guard predicate — testable without a DB.
# ---------------------------------------------------------------------------
def _wc_member(l6: str | None) -> bool:
    return (l6 or "") in _WC


def changes_total(
    cur_l6: str | None, cur_l7: str | None,
    new_l6: str | None, new_l7: str | None,
    cf_band: dict[tuple[str, str], tuple],
) -> bool:
    """True iff moving (cur_l6,cur_l7) -> (new_l6,new_l7) changes a roll-up total.

    ``cf_band`` maps ``(l6, l7)`` -> the CF band ``(l1, l2)`` it resolves to via
    ``lib_cf_mapping`` (key_kind='na').  Unmapped NA keys resolve to ``None``.

    A total changes iff:
      * WC membership flips: ``(cur_l6 in WC) != (new_l6 in WC)``  (NWC + WC subtotals), OR
      * the CF band differs: ``cf_band[cur] != cf_band[new]``      (CF subtotals/grandtotal).
    Identical mapping never changes a total (short-circuit).
    """
    if (cur_l6, cur_l7) == (new_l6, new_l7):
        return False
    if _wc_member(cur_l6) != _wc_member(new_l6):
        return True
    cur_band = cf_band.get((cur_l6 or "", cur_l7 or ""))
    new_band = cf_band.get((new_l6 or "", new_l7 or ""))
    return cur_band != new_band


# ---------------------------------------------------------------------------
# DB plumbing
# ---------------------------------------------------------------------------
def _load_library(session: SASession) -> dict[str, list]:
    """account_name -> [(na_mapping, na_description, occurrences), ...]."""
    rows = session.execute(text(
        "SELECT account_name, na_mapping, na_description, occurrences FROM lib_na_mapping"
    )).fetchall()
    by_name: dict[str, list] = defaultdict(list)
    for r in rows:
        by_name[r.account_name].append((r.na_mapping, r.na_description, int(r.occurrences or 0)))
    return by_name


def _load_overrides(session: SASession) -> dict[tuple[str, int], tuple[str, str]]:
    rows = session.execute(text(
        "SELECT account_number_group, fiscal_year, na_mapping, na_description FROM ovr_na_mapping"
    )).fetchall()
    return {(r.account_number_group, int(r.fiscal_year)): (r.na_mapping, r.na_description) for r in rows}


def _load_cf_band(session: SASession) -> dict[tuple[str, str], tuple]:
    """(na_mapping, na_description) -> (cf l1, l2) from lib_cf_mapping (key_kind='na')."""
    out: dict[tuple[str, str], tuple] = {}
    try:
        rows = session.execute(text(
            "SELECT key_1, key_2, l1, l2 FROM lib_cf_mapping WHERE key_kind = 'na'"
        )).fetchall()
    except Exception:  # noqa: BLE001 — cf library not present yet → no CF-band guard
        return out
    for r in rows:
        out[(r.key_1, r.key_2)] = (r.l1, r.l2)
    return out


def _load_current(session: SASession) -> list:
    """Current dim_gl_na joined to account_name."""
    return session.execute(text("""
        SELECT na.account_number_group, na.fiscal_year,
               na.l6_na_mapping, na.l7_na_description,
               a.account_name
        FROM dim_gl_na na
        JOIN dim_gl_account a
          ON a.account_number_group = na.account_number_group
         AND a.fiscal_year          = na.fiscal_year
    """)).fetchall()


_UPDATE_NA = """
UPDATE dim_gl_na
   SET l6_na_mapping = :l6, l7_na_description = :l7
 WHERE account_number_group = :ang AND fiscal_year = :fy
   AND (l6_na_mapping IS DISTINCT FROM :l6 OR l7_na_description IS DISTINCT FROM :l7)
"""

_INSERT_GUARD_OVERRIDE = """
INSERT INTO ovr_na_mapping
  (account_number_group, fiscal_year, na_mapping, na_description, source, updated_at)
VALUES (:ang, :fy, :l6, :l7, 'totals_guard', NOW())
ON CONFLICT (account_number_group, fiscal_year) DO UPDATE SET
  na_mapping = EXCLUDED.na_mapping, na_description = EXCLUDED.na_description,
  source = EXCLUDED.source, updated_at = NOW()
"""


def rederive(session: SASession, *, dry_run: bool) -> dict:
    library = _load_library(session)
    overrides = _load_overrides(session)
    cf_band = _load_cf_band(session)
    current = _load_current(session)

    n_unchanged = n_reshuffled = n_guarded = n_no_library = 0
    guarded_names: set[str] = set()
    plan: list[dict] = []  # (ang, fy, final_l6, final_l7, write_guard)

    for row in current:
        ang, fy = row.account_number_group, int(row.fiscal_year)
        cur_l6, cur_l7 = row.l6_na_mapping, row.l7_na_description
        name = row.account_name

        # 1) override precedence (manual or a pre-existing guard pin)
        ov = overrides.get((ang, fy))
        if ov is not None:
            new_l6, new_l7 = ov
            plan.append({"ang": ang, "fy": fy, "l6": new_l6, "l7": new_l7, "guard": False})
            if (new_l6, new_l7) == (cur_l6, cur_l7):
                n_unchanged += 1
            else:
                n_reshuffled += 1
            continue

        # 2) library most-frequent
        winner = resolve_most_frequent(library.get(name, []))
        if winner is None:
            # No library precedent → keep current (cannot resolve, never change a total).
            n_no_library += 1
            plan.append({"ang": ang, "fy": fy, "l6": cur_l6, "l7": cur_l7, "guard": False})
            continue
        new_l6, new_l7 = winner.na_mapping, winner.na_description

        # 3) totals-guard
        if changes_total(cur_l6, cur_l7, new_l6, new_l7, cf_band):
            # Pin to CURRENT so no total moves.
            plan.append({"ang": ang, "fy": fy, "l6": cur_l6, "l7": cur_l7, "guard": True})
            n_guarded += 1
            guarded_names.add(name)
        elif (new_l6, new_l7) == (cur_l6, cur_l7):
            n_unchanged += 1
            plan.append({"ang": ang, "fy": fy, "l6": new_l6, "l7": new_l7, "guard": False})
        else:
            n_reshuffled += 1
            plan.append({"ang": ang, "fy": fy, "l6": new_l6, "l7": new_l7, "guard": False})

    summary = {
        "accounts": len(current), "unchanged": n_unchanged,
        "reshuffled": n_reshuffled, "guarded": n_guarded,
        "no_library": n_no_library, "guarded_names": sorted(guarded_names),
    }

    if not dry_run:
        for p in plan:
            if p["guard"]:
                session.execute(text(_INSERT_GUARD_OVERRIDE),
                                {"ang": p["ang"], "fy": p["fy"], "l6": p["l6"], "l7": p["l7"]})
            session.execute(text(_UPDATE_NA),
                            {"ang": p["ang"], "fy": p["fy"], "l6": p["l6"], "l7": p["l7"]})
        session.commit()

    return summary


def _run_populate_cf() -> int:
    """Re-run populate_dim_gl_cf.py in the SAME interpreter / DB env."""
    script = _BACKEND / "scripts" / "populate_dim_gl_cf.py"
    print(f"\n--- re-deriving dim_gl_cf ({script.name}) ---")
    return subprocess.call([sys.executable, str(script)])


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-derive dim_gl_na from lib_na_mapping + ovr_na_mapping.")
    parser.add_argument("--dry-run", action="store_true", help="report the guard plan; write nothing")
    parser.add_argument("--skip-cf", action="store_true", help="do not re-run populate_dim_gl_cf.py")
    args = parser.parse_args()

    with SASession(engine) as session:
        lib_n = session.execute(text("SELECT COUNT(*) FROM lib_na_mapping")).scalar() or 0
        if lib_n == 0:
            print("ERROR: lib_na_mapping is empty — run load_na_mapping_library.py first.")
            return 2
        summary = rederive(session, dry_run=args.dry_run)

    print(f"\nSUMMARY ({'dry-run' if args.dry_run else 'applied'}):")
    print(f"  accounts processed   : {summary['accounts']}")
    print(f"  unchanged            : {summary['unchanged']}")
    print(f"  safe-reshuffled      : {summary['reshuffled']}")
    print(f"  totals-guard pinned  : {summary['guarded']}")
    print(f"  no library precedent : {summary['no_library']}")
    if summary["guarded_names"]:
        print(f"  guarded account_names ({len(summary['guarded_names'])}):")
        for nm in summary["guarded_names"]:
            print(f"      - {nm!r}")

    if args.dry_run:
        print("\n[dry-run] no writes performed; dim_gl_cf NOT re-derived.")
        return 0
    if args.skip_cf:
        print("\n[--skip-cf] dim_gl_cf NOT re-derived (run populate_dim_gl_cf.py yourself).")
        return 0
    return _run_populate_cf()


if __name__ == "__main__":
    sys.exit(main())
