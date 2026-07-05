"""Pure NA-mapping resolver — MOST-FREQUENT with a deterministic tiebreaker.

This is the single source of truth for "given the observed ``(na_mapping,
na_description)`` rows for one ``account_name`` in ``lib_na_mapping``, which
one wins?".  It is a pure function (no DB, no I/O) so it is fully unit-testable
and is shared by the populate step (``backend/scripts/populate_dim_gl_na.py``).

FORMULA
-------
For an account_name with observed rows ``[(na_mapping, na_description,
occurrences), ...]``:

    winner = argmax_over_rows( occurrences )

Ties (equal ``occurrences`` — e.g. a 50/50 split across legal entities) are
broken DETERMINISTICALLY by sorting the tied candidates lexicographically on
``(na_mapping, na_description)`` and taking the first.  This makes the resolver
STABLE: the same library always yields the same winner regardless of row order,
so a blind 50/50 loan split never flips non-deterministically between runs.

WORKED EXAMPLE
--------------
    rows = [
        ("OWC", "Other assets",      8),   # most frequent  -> winner
        ("ND",  "Loan to employees", 4),
    ]
    resolve_most_frequent(rows) == ("OWC", "Other assets")

    # 50/50 tie -> lexicographic on (na_mapping, na_description):
    rows = [
        ("OWC", "Liabilities due to affiliates", 4),
        ("ND",  "Loan to BSG Seifersbach",       4),
    ]
    resolve_most_frequent(rows) == ("ND", "Loan to BSG Seifersbach")
    # "ND" < "OWC" lexicographically, so the ND row wins the tie, deterministically.

EDGE CASES
----------
* empty input            -> returns ``None``.
* single row             -> that row wins.
* occurrences absent/None-> treated as 0 (still deterministic via the tiebreaker).
"""
from __future__ import annotations

from typing import Iterable, NamedTuple, Optional


class NaRow(NamedTuple):
    """One observed (mapping, description) for an account_name, with a count."""

    na_mapping: str
    na_description: str
    occurrences: int


def _coerce(rows: Iterable) -> list[NaRow]:
    """Accept tuples / NamedTuples / mappings; normalise to ``NaRow``."""
    out: list[NaRow] = []
    for r in rows:
        if isinstance(r, NaRow):
            out.append(r)
            continue
        if isinstance(r, dict):
            m = r.get("na_mapping")
            d = r.get("na_description")
            occ = r.get("occurrences", 0)
        else:  # sequence (tuple/list/SQLAlchemy Row)
            m, d = r[0], r[1]
            occ = r[2] if len(r) > 2 else 0
        out.append(NaRow(str(m), str(d), int(occ or 0)))
    return out


def resolve_most_frequent(rows: Iterable) -> Optional[NaRow]:
    """Return the most-frequent ``NaRow`` for one account_name, deterministically.

    ``rows`` is any iterable of ``(na_mapping, na_description, occurrences)``
    (tuples, ``NaRow``, dicts, or SQLAlchemy ``Row``).  Returns ``None`` when the
    input is empty.

    Selection: maximise ``occurrences``; break ties by the smallest
    ``(na_mapping, na_description)`` lexicographically (stable 50/50 resolution).
    """
    coerced = _coerce(rows)
    if not coerced:
        return None
    # max occurrences (negate) then lexicographic (mapping, description) ascending.
    return min(coerced, key=lambda r: (-r.occurrences, r.na_mapping, r.na_description))


# ===========================================================================
# Account (chart-of-accounts) most-frequent resolver
# ===========================================================================
# The ACCOUNT mapping library (``lib_account_mapping``) is the COA analogue of the
# NA library above: it answers "given the observed financial-statement hierarchies
# for one ``account_name``, which full ``(level_0..4, l4_sub, sorts, is_ic)`` wins?".
# It is the source the fill step (``backend/scripts/populate_dim_gl_account_fill.py``)
# resolves to classify accounts in fiscal years the project's mapping file never
# covered.  Pure function, same FORMULA + deterministic tiebreaker as the NA case.
#
# FORMULA
# -------
# For an account_name with observed rows ``[(level_0, level_1, level_2, level_3,
# level_4, l4_sub, level_2_sort, level_3_sort, is_ic, occurrences), ...]``:
#
#     winner = argmax_over_rows( occurrences )
#
# Ties (equal ``occurrences``) are broken DETERMINISTICALLY by the smallest
# ``(level_0, level_2, level_3, level_4)`` lexicographically — the PRIMARY-KEY
# identity of a library row — so the same library always yields the same winner.
#
# WORKED EXAMPLE
# --------------
#     rows = [
#         AccountRow("PL", "...", "Revenue",  "Sales",     "Gross", "", 10, 20, False, 8),  # winner
#         AccountRow("PL", "...", "Revenue",  "Discounts", "Net",   "", 10, 30, False, 4),
#     ]
#     resolve_account_most_frequent(rows).level_3 == "Sales"   # occ 8 > 4
#
# EDGE CASES — identical to the NA resolver: empty -> None; single row wins;
# occurrences absent/None -> 0 (still deterministic via the PK tiebreaker).


class AccountRow(NamedTuple):
    """One observed financial-statement hierarchy for an account_name, with a count."""

    level_0: str
    level_1: str
    level_2: str
    level_3: str
    level_4: str
    l4_sub: str
    level_2_sort: Optional[int]
    level_3_sort: Optional[int]
    is_ic: bool
    occurrences: int


def _coerce_account(rows: Iterable) -> "list[AccountRow]":
    """Accept tuples / NamedTuples / mappings; normalise to ``AccountRow``.

    String hierarchy fields are coerced to ``str`` (empty string for None) so the
    tiebreaker is total-order safe; the two sorts stay nullable ints; ``is_ic`` is
    coerced to ``bool``; ``occurrences`` to ``int`` (None -> 0).
    """
    out: "list[AccountRow]" = []

    def _s(v) -> str:
        return "" if v is None else str(v)

    def _i(v) -> Optional[int]:
        if v is None or v == "":
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    for r in rows:
        if isinstance(r, AccountRow):
            out.append(r)
            continue
        if isinstance(r, dict):
            vals = (
                r.get("level_0"), r.get("level_1"), r.get("level_2"),
                r.get("level_3"), r.get("level_4"), r.get("l4_sub"),
                r.get("level_2_sort"), r.get("level_3_sort"),
                r.get("is_ic"), r.get("occurrences", 0),
            )
        else:  # sequence (tuple/list/SQLAlchemy Row) in PK-aligned order
            vals = tuple(r[i] if i < len(r) else None for i in range(10))
        (l0, l1, l2, l3, l4, l4s, s2, s3, ic, occ) = vals
        out.append(AccountRow(
            _s(l0), _s(l1), _s(l2), _s(l3), _s(l4), _s(l4s),
            _i(s2), _i(s3), bool(ic), int(occ or 0),
        ))
    return out


def resolve_account_most_frequent(rows: Iterable) -> Optional[AccountRow]:
    """Return the most-frequent ``AccountRow`` for one account_name, deterministically.

    ``rows`` is any iterable of the 10-field account-library tuple (tuples,
    ``AccountRow``, dicts, or SQLAlchemy ``Row``).  Returns ``None`` when empty.

    Selection: maximise ``occurrences``; break ties by the smallest
    ``(level_0, level_2, level_3, level_4)`` (the library PK) lexicographically,
    so the winner is stable across runs regardless of row order.
    """
    coerced = _coerce_account(rows)
    if not coerced:
        return None
    return min(
        coerced,
        key=lambda r: (-r.occurrences, r.level_0, r.level_2, r.level_3, r.level_4),
    )
