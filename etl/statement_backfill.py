"""Backfill a NULL ``dim_gl_account.level_0`` (statement) for accounts that are
still POSTED in the GL — the root fix for the "silently dropped mapped account" bug.

WHY
---
Every P&L / BS grain reader hard-filters on the account's statement:
``WHERE a.level_0='PL'`` (``fin_compat_sql.pl_grain_sql_*``) / ``='BS'``
(``fin_compat_bs_sql``).  An account whose ``level_0`` is NULL therefore contributes
to NO statement — its GL activity is silently dropped from every report.

A NULL ``level_0`` arises from a Chart-of-Accounts export → re-ingest round-trip: an
account whose source hierarchy has DUPLICATE consecutive levels (e.g.
``level_0=level_1='PL'`` AND ``level_3=level_4='Depreciation & amortisation'``) shifts
up by one level on re-ingest, and the mapping upsert
(``etl.load.load_canonical`` ``ON CONFLICT DO UPDATE SET level_0 = EXCLUDED.level_0``)
binds the now-empty ``level_0`` as NULL, OVERWRITING the prior good 'PL'.  Confirmed on
GL account 48100 ("Afa Gebaeude"), which under-reported entity-04 depreciation.

THE FIX (deterministic, idempotent, ADDITIVE)
---------------------------------------------
For every ``dim_gl_account`` row with ``level_0 IS NULL`` whose
``account_number_group`` still has GL activity (appears in ``fact_gl_line``), resolve
the statement ('PL' | 'BS') and UPDATE **only** ``level_0`` **only** where it is
currently NULL.  Never touches a non-NULL ``level_0`` (never overwrites the mapping);
never assigns 'CF' (CF is a derived statement keyed by ``dim_gl_cf.cf_mapping``, not by
``level_0``).  A strict NO-OP when there are no NULL-``level_0`` accounts (v5 parity).

RESOLUTION PRIORITY (first hit wins)
------------------------------------
  1. SIBLING  — the SAME ``account_number_group``'s OTHER fiscal-year rows that carry a
                non-null ``level_0`` (the account's own classification in a year the
                round-trip did not corrupt).  Most frequent, deterministic tiebreak.
  2. LIBRARY  — most-frequent statement for the account's ``account_name`` in
                ``lib_account_mapping`` (``etl.mapping_library.resolve``).
  3. STRUCTURE— the account's grain (gl_account_id / level_2 / level_3 / level_4)
                matches a mapping row in EXACTLY ONE of ``dim_pl_structure`` /
                ``dim_bs_structure`` (the readers' own ``_match_grain``).  A grain that
                matches both (or neither) is AMBIGUOUS → left unresolved (never guessed).

Unresolved accounts are reported (never guessed) so the paired guardrail
(``warn_unresolved_null_statement``) can WARN that they will still be dropped.
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Iterable, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Accounts never carry a level_0='CF'; CF is derived from dim_gl_cf.cf_mapping.
_VALID_STATEMENTS = ("PL", "BS")

# Guardrail: cap how many offending account numbers we spell out in one log line.
_OFFENDER_CAP = 25


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def _norm_statement(value: Any) -> Optional[str]:
    """Map a raw ``level_0`` value to canonical 'PL' | 'BS' | None.

    Robust to a few common spellings so a slightly non-canonical CoA still
    classifies, but ONLY ever returns the exact reader tokens 'PL' / 'BS' (the
    readers filter ``level_0='PL'`` / ``='BS'`` byte-for-byte).
    """
    s = str(value or "").strip().upper()
    if not s:
        return None
    if s in ("PL", "P&L", "PANDL", "GUV", "IS", "INCOME STATEMENT", "PROFIT AND LOSS"):
        return "PL"
    if s in ("BS", "BALANCE SHEET", "BALANCESHEET", "BILANZ"):
        return "BS"
    if "BILANZ" in s or "BALANCE" in s or s.startswith("BS"):
        return "BS"
    if "GUV" in s or "PROFIT" in s or "INCOME" in s or "P&L" in s or s.startswith("PL"):
        return "PL"
    return None


def _pick_sibling_statement(raw_level_0s: Iterable[Any]) -> Optional[str]:
    """Most-frequent canonical statement across sibling-year ``level_0`` values.

    Ties (e.g. a genuinely mixed account) are broken lexicographically ('BS' < 'PL')
    so the choice is deterministic regardless of row order.  Returns None when no
    sibling value normalises to 'PL' / 'BS'.
    """
    counts: Counter[str] = Counter()
    for v in raw_level_0s:
        n = _norm_statement(v)
        if n is not None:
            counts[n] += 1
    if not counts:
        return None
    # max count, then lexicographic statement token (stable tiebreak).
    return min(counts, key=lambda s: (-counts[s], s))


def resolve_null_statement(
    sibling_level_0s: Iterable[Any],
    library_level_0: Any = None,
    structure_statement: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Resolve the statement for a NULL-``level_0`` account, first hit wins.

    Parameters
    ----------
    sibling_level_0s : iterable
        Raw ``level_0`` values of the SAME account's other fiscal-year rows (source 1).
    library_level_0 : Any
        The ``level_0`` of the account_name's most-frequent library hierarchy, already
        resolved by ``resolve_account_most_frequent`` (source 2).  None when absent.
    structure_statement : 'PL' | 'BS' | None
        The single statement whose structure the account's grain matches (source 3),
        or None when it matches both/neither (ambiguous — never guessed).

    Returns
    -------
    (statement, source) : (str|None, str|None)
        ``statement`` is 'PL' / 'BS' (never 'CF'); ``source`` is
        'sibling' / 'library' / 'structure'.  ``(None, None)`` when unresolved.
    """
    s = _pick_sibling_statement(sibling_level_0s)
    if s is not None:
        return s, "sibling"
    n = _norm_statement(library_level_0)
    if n is not None:
        return n, "library"
    if structure_statement in _VALID_STATEMENTS:
        return structure_statement, "structure"
    return None, None


# --------------------------------------------------------------------------- #
# DB loaders (SQLite-portable: no ANY(array), small tables read whole)
# --------------------------------------------------------------------------- #
def _gl_active_groups(session: Session) -> set[str]:
    """account_number_group values that appear in ``fact_gl_line`` (GL activity).

    DISTINCT over the account key — bounded by the size of the chart, not the (large)
    fact row count.
    """
    rows = session.execute(
        text("SELECT DISTINCT account_number_group FROM fact_gl_line")
    ).fetchall()
    return {str(r[0]) for r in rows if r[0] is not None}


def _load_dim_rows(session: Session) -> list[dict]:
    """All ``dim_gl_account`` rows needed to resolve a NULL statement.

    ``dim_gl_account`` is the Chart of Accounts (accounts × fiscal years) — small — so
    reading it whole keeps the resolver dialect-portable (no ``= ANY(:array)``).
    """
    rows = session.execute(text(
        "SELECT account_number_group, fiscal_year, level_0, account_name, "
        "gl_account_id, level_2, level_3, level_4 FROM dim_gl_account"
    )).fetchall()
    return [dict(r._mapping) if hasattr(r, "_mapping") else {
        "account_number_group": r[0], "fiscal_year": r[1], "level_0": r[2],
        "account_name": r[3], "gl_account_id": r[4], "level_2": r[5],
        "level_3": r[6], "level_4": r[7],
    } for r in rows]


def _load_mapping_rows(session: Session, table: str) -> list[dict]:
    """Value-bearing 'mapping' rows of a structure table (grain columns only).

    Absent table (legacy / partial schema) → [] so source 3 simply does not fire.
    """
    try:
        rows = session.execute(text(
            f"SELECT row_type, gl_account_id, level_2, level_3, level_4 FROM {table}"
        )).fetchall()
    except Exception:  # noqa: BLE001 — absent structure table => source unavailable
        return []
    out: list[dict] = []
    for r in rows:
        d = dict(r._mapping) if hasattr(r, "_mapping") else {
            "row_type": r[0], "gl_account_id": r[1],
            "level_2": r[2], "level_3": r[3], "level_4": r[4],
        }
        # Only genuine 'mapping' leaves are grain-match candidates. A NULL row_type
        # (header / subtotal / separator) is NOT coerced to 'mapping' here — guessing
        # it in would risk a false PL/BS structure hit for source 3.
        if str(d.get("row_type") or "") == "mapping":
            out.append(d)
    return out


def _load_library(session: Session) -> dict[str, list]:
    """account_name -> library rows.  Reuses ``etl.account_fill._load_library``;
    absent ``lib_account_mapping`` (partial schema) → {} so source 2 does not fire."""
    try:
        from etl.account_fill import _load_library as _lib
        return _lib(session)
    except Exception as exc:  # noqa: BLE001 — absent library table => source unavailable
        logger.debug("statement_backfill: library unavailable (%s)", exc)
        return {}


def _match_grain_fn():
    """The readers' own grain matcher (source 3).  None when backend not importable
    (pure-ETL context) so source 3 is simply skipped."""
    try:
        from app.services.fin_compat_pl import _match_grain  # type: ignore
        return _match_grain
    except Exception as exc:  # noqa: BLE001
        logger.debug("statement_backfill: grain matcher unavailable (%s)", exc)
        return None


# --------------------------------------------------------------------------- #
# Candidate assembly
# --------------------------------------------------------------------------- #
def _candidate_groups(
    dim_rows: list[dict], active: set[str], scope=None
) -> dict[str, dict]:
    """Group the CoA into per-``account_number_group`` resolution context.

    Returns ``{group: {"siblings": [level_0…], "name": str|None,
    "grain": {...}, "null_years": [fy…]}}`` for every group that (a) has at least one
    ``level_0 IS NULL`` row, (b) has GL activity, and (c) passes ``scope`` (prefix/year).
    """
    prefixes = set(getattr(scope, "prefixes", []) or [])
    years = set(int(y) for y in (getattr(scope, "years", []) or []))

    ctx: dict[str, dict] = {}
    for r in dim_rows:
        g = str(r["account_number_group"]) if r["account_number_group"] is not None else None
        if g is None:
            continue
        c = ctx.setdefault(g, {
            "siblings": [], "name": None, "grain": None, "null_years": [],
        })
        if r["level_0"] is None:
            fy = r["fiscal_year"]
            if fy is not None:
                c["null_years"].append(int(fy))
            # First NULL row's grain is representative (double-duplicate accounts share it).
            if c["grain"] is None:
                c["grain"] = {
                    "gl_account_id": r.get("gl_account_id"),
                    "level_2": r.get("level_2"),
                    "level_3": r.get("level_3"),
                    "level_4": r.get("level_4"),
                }
        else:
            c["siblings"].append(r["level_0"])
        # Borrow account_name from ANY row that has one (name-keyed resolution).
        if not c["name"]:
            nm = r.get("account_name")
            if nm is not None and str(nm).strip():
                c["name"] = str(nm)

    out: dict[str, dict] = {}
    for g, c in ctx.items():
        if not c["null_years"]:
            continue  # no NULL rows → nothing to backfill
        if g not in active:
            continue  # no GL activity → not a used account → leave alone
        if prefixes and g[:2] not in prefixes:
            continue
        if years:
            scoped = [y for y in c["null_years"] if y in years]
            if not scoped:
                continue
            c["null_years"] = scoped
        out[g] = c
    return out


def _structure_statement(grain: Optional[dict], match_fn, pl_rows, bs_rows) -> Optional[str]:
    """Statement whose structure the grain matches — EXACTLY one of PL/BS, else None."""
    if grain is None or match_fn is None:
        return None
    g = {
        "gl_account_id": grain.get("gl_account_id"),
        "level_2": grain.get("level_2"),
        "level_3": grain.get("level_3"),
        "level_4": grain.get("level_4"),
    }
    pl_hit = any(match_fn(g, row) for row in pl_rows)
    bs_hit = any(match_fn(g, row) for row in bs_rows)
    if pl_hit and not bs_hit:
        return "PL"
    if bs_hit and not pl_hit:
        return "BS"
    return None


# --------------------------------------------------------------------------- #
# The backfill (B) + the guardrail (C)
# --------------------------------------------------------------------------- #
def backfill_null_statement(session: Session, scope=None, *, dry_run: bool = False) -> dict:
    """Backfill ``level_0`` for NULL-statement accounts that still have GL activity.

    Deterministic, idempotent, ADDITIVE: UPDATEs only ``level_0`` and only where it is
    currently NULL; never overwrites a non-NULL ``level_0``; never assigns 'CF'.  A
    strict NO-OP (no UPDATE issued) when no such account exists (v5 parity).

    Returns a summary dict::

        {"backfilled": int, "from_sibling": int, "from_library": int,
         "from_structure": int, "unresolved": int, "unresolved_groups": [ang…],
         "resolved": [(ang, stmt, source)…], "noop": bool, "dry_run": bool}
    """
    summary = {
        "backfilled": 0, "from_sibling": 0, "from_library": 0, "from_structure": 0,
        "unresolved": 0, "unresolved_groups": [], "resolved": [],
        "noop": True, "dry_run": dry_run,
    }

    active = _gl_active_groups(session)
    if not active:
        return summary
    dim_rows = _load_dim_rows(session)
    candidates = _candidate_groups(dim_rows, active, scope)
    if not candidates:
        return summary  # strict no-op — nothing NULL with GL activity

    summary["noop"] = False
    library = _load_library(session)
    match_fn = _match_grain_fn()
    pl_rows = _load_mapping_rows(session, "dim_pl_structure") if match_fn else []
    bs_rows = _load_mapping_rows(session, "dim_bs_structure") if match_fn else []

    from etl.mapping_library.resolve import resolve_account_most_frequent

    years = sorted(int(y) for y in (getattr(scope, "years", []) or []))

    for group in sorted(candidates):
        c = candidates[group]
        winner = resolve_account_most_frequent(library.get(c["name"], [])) if c["name"] else None
        library_level_0 = winner.level_0 if winner is not None else None
        struct_stmt = _structure_statement(c["grain"], match_fn, pl_rows, bs_rows)

        stmt, source = resolve_null_statement(c["siblings"], library_level_0, struct_stmt)
        if stmt is None:
            # Collect only; the account_name (client-identifying free-text) is NOT
            # logged. Aggregated into ONE capped summary line after the loop (below),
            # mirroring guardrail C — never one WARNING per account, never a name.
            summary["unresolved"] += 1
            summary["unresolved_groups"].append(group)
            logger.debug("statement_backfill: %s unresolved (no precedent)", group)
            continue

        summary["resolved"].append((group, stmt, source))
        summary[f"from_{source}"] += 1
        if not dry_run:
            n = _apply_update(session, group, stmt, years)
        else:
            n = len(c["null_years"])
        summary["backfilled"] += int(n or 0)

    if summary["unresolved_groups"]:
        # ONE capped WARNING (numbers only, cap == guardrail C), not one per account.
        sample = summary["unresolved_groups"][:_OFFENDER_CAP]
        logger.warning(
            "statement_backfill: %d account group(s) with GL activity could not be "
            "resolved to a statement (no sibling/library/structure precedent) — left "
            "NULL: %s%s",
            summary["unresolved"], sample,
            " …" if len(summary["unresolved_groups"]) > _OFFENDER_CAP else "",
        )

    logger.info(
        "statement_backfill: %d NULL-level_0 row(s) backfilled "
        "(sibling=%d, library=%d, structure=%d; %d group(s) unresolved)%s",
        summary["backfilled"], summary["from_sibling"], summary["from_library"],
        summary["from_structure"], summary["unresolved"],
        " [DRY-RUN]" if dry_run else "",
    )
    return summary


def _apply_update(session: Session, group: str, statement: str, years: list[int]) -> int:
    """UPDATE only NULL ``level_0`` rows of ``group`` to ``statement`` (additive).

    Restricts to ``years`` when a scoped rebuild passes them.  Returns rowcount.
    """
    sql = (
        "UPDATE dim_gl_account SET level_0 = :stmt "
        "WHERE account_number_group = :g AND level_0 IS NULL"
    )
    params: dict[str, Any] = {"stmt": statement, "g": group}
    if years:
        placeholders = ", ".join(f":y{i}" for i in range(len(years)))
        sql += f" AND fiscal_year IN ({placeholders})"
        for i, y in enumerate(years):
            params[f"y{i}"] = int(y)
    res = session.execute(text(sql), params)
    return int(res.rowcount or 0)


def warn_unresolved_null_statement(session: Session, scope=None) -> dict:
    """Guardrail (C): WARN about accounts that STILL have GL activity but ``level_0``
    IS NULL after the backfill — mapped accounts the readers will silently drop.

    Does NOT relax any reader filter — visibility/assertion only.  Returns
    ``{"unresolved": int, "sample": [ang…]}``.
    """
    active = _gl_active_groups(session)
    if not active:
        return {"unresolved": 0, "sample": []}
    dim_rows = _load_dim_rows(session)
    still_null = sorted(_candidate_groups(dim_rows, active, scope).keys())
    if not still_null:
        return {"unresolved": 0, "sample": []}

    sample = still_null[:_OFFENDER_CAP]
    logger.warning(
        "statement_backfill GUARDRAIL: %d account group(s) still have GL activity but "
        "level_0 IS NULL — their mapped bookings are SILENTLY DROPPED from the P&L/BS "
        "readers (WHERE level_0='PL'/'BS'). Offending account_number_group(s): %s%s",
        len(still_null), sample, " …" if len(still_null) > _OFFENDER_CAP else "",
    )
    return {"unresolved": len(still_null), "sample": sample}
