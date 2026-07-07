"""Phase 5 (decision 2) — auto-extension of the statement structure.

Detects Chart-of-Accounts *positions* present in the committed CoA
(``dim_gl_account``) that do NOT resolve to any row in the correct per-statement
presentation structure (``dim_pl_structure`` / ``dim_bs_structure`` /
``dim_cf_structure``) — i.e. that would fall through classification into the
statement's residual/unmapped bucket — and lets the Project-Setup wizard place
them into the L1–L4 hierarchy before the final commit.

Design (mirrors the readers, so detection can never drift from presentation):
  * A *grain* is one GL account bucket keyed by ``(level_2, level_3, level_4)``
    from ``dim_gl_account``.  The statement builders match a grain to a structure
    MAPPING row via ``fin_compat_pl._match_grain`` (gl_account_id if the structure
    row pins one, else a level_2/3/4 prefix match).  This module reuses that EXACT
    matcher, so a position is "known" here iff the reader would classify it.
  * The correct statement for an account is its ``level_0`` ('BS' | 'PL').  CF is a
    derived statement (its own ``cf_mapping``); accounts do not carry a level_0='CF'
    so CF is never the *suggested* statement, but the placement writer supports all
    three tables so a user MAY route a position to CF and the split invariant
    (decision 1) is preserved — the target table is chosen by ``statement`` alone,
    so a CF position can never land in the Income Statement.

SPLIT INVARIANT + NO-DROP/NO-DOUBLE-COUNT (docs/financial-logic.md "Phase 5"):
  * ``TABLE_BY_STATEMENT`` is the single source of truth: PL→dim_pl_structure,
    BS→dim_bs_structure, CF→dim_cf_structure.  A placement's ``statement`` picks the
    table; nothing else can move a row across statements.
  * Every placement is a single ``row_type='mapping'`` LEAF.  A leaf is summed into
    exactly one section and into the running-sum subtotals BELOW its position — so a
    placed position flows into exactly one statement and is counted exactly once.
  * Placed rows carry the reader's own statement marker (line_code prefix / sort
    band / kpi tag) so the reader's ``_is_*_structure_row`` predicate ACCEPTS them —
    otherwise a physically-inserted row would be silently filtered back out.
  * Idempotent: a placement whose ``line_code`` (or identical level/account path)
    already exists is SKIPPED, never duplicated.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

# Reuse the readers' EXACT grain matcher so detection == presentation.
from app.services.fin_compat_pl import _match_grain

# ---------------------------------------------------------------------------
# Statement ↔ table mapping (the split invariant, decision 1)
# ---------------------------------------------------------------------------
TABLE_BY_STATEMENT: dict[str, str] = {
    "PL": "dim_pl_structure",
    "BS": "dim_bs_structure",
    "CF": "dim_cf_structure",
}

# sort_order bands the readers assume (post-0030 each table is its own space, but
# fin_compat_pl._is_pl_structure_row still gates PL on sort_order < 1000, and
# fin_compat_bs._is_bs_structure_row accepts sort_order >= 1000).
_PL_SORT_CEILING = 1000     # PL rows must stay strictly below this
_BS_SORT_FLOOR = 1000       # BS rows must stay at/above this
_CF_SORT_FLOOR = 2000       # CF rows conventionally start here
_SORT_STEP = 10

_STRUCT_COLUMNS = (
    "pl_line_id, sort_order, line_code, row_type, balance_title, details, "
    "calc_type, level_2, level_3, level_4, gl_account_id, invert_delta, is_bold, kpi_code"
)


# ---------------------------------------------------------------------------
# Statement normalisation
# ---------------------------------------------------------------------------

def normalize_statement(level_0: Any) -> str:
    """Map a ``dim_gl_account.level_0`` value to 'PL' | 'BS' | 'UNKNOWN'.

    level_0 "carries BS/PL" (migration 0001).  Robust to a few common spellings
    so a slightly non-canonical CoA still classifies.
    """
    s = str(level_0 or "").strip().upper()
    if not s:
        return "UNKNOWN"
    if s in ("BS", "BALANCE SHEET", "BALANCESHEET", "BILANZ"):
        return "BS"
    if s in ("PL", "P&L", "PANDL", "GUV", "IS", "INCOME STATEMENT", "PROFIT AND LOSS"):
        return "PL"
    if "BILANZ" in s or "BALANCE" in s or s.startswith("BS"):
        return "BS"
    if ("GUV" in s or "PROFIT" in s or "INCOME" in s or "P&L" in s or s.startswith("PL")):
        return "PL"
    return "UNKNOWN"


def _clean(v: Any) -> str:
    return str(v or "").strip()


# ---------------------------------------------------------------------------
# Pure detection
# ---------------------------------------------------------------------------

def _mapping_rows(struct_rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Value-bearing 'mapping' rows only — the rows a grain can classify against."""
    return [r for r in struct_rows if str(r.get("row_type") or "mapping") == "mapping"]


def grain_is_known(account: dict[str, Any], mapping_rows: list[dict[str, Any]]) -> bool:
    """True iff SOME structure mapping row matches this account's grain.

    ``account`` needs the grain keys the reader uses: gl_account_id, level_2,
    level_3, level_4.  Delegates to the readers' own ``_match_grain``.
    """
    grain = {
        "gl_account_id": _clean(account.get("gl_account_id")),
        "level_2": _clean(account.get("level_2")),
        "level_3": _clean(account.get("level_3")),
        "level_4": _clean(account.get("level_4")),
    }
    return any(_match_grain(grain, row) for row in mapping_rows)


def position_id(statement: str, l2: str, l3: str, l4: str) -> str:
    raw = "|".join((statement, l2, l3, l4))
    return "pos_" + hashlib.blake2s(raw.encode("utf-8"), digest_size=8).hexdigest()


def detect_unknown_positions(
    accounts: list[dict[str, Any]],
    struct_by_statement: dict[str, list[dict[str, Any]]],
    *,
    account_cap: int = 25,
) -> list[dict[str, Any]]:
    """Pure core: given CoA accounts + per-statement structure rows, return the
    positions (grouped by statement + level_2/3/4) that classify nowhere.

    ``accounts``: dicts with account_number_group, gl_account_id, account_name,
    level_0, level_1, level_2, level_3, level_4.
    ``struct_by_statement``: {'PL': [rows], 'BS': [rows], 'CF': [rows]} — full
    structure row dicts (row_type included).

    Returns one entry per distinct unknown (statement, level_2, level_3, level_4)
    with its member accounts and a placement suggestion.  Deterministic order.
    """
    mapping_by_stmt = {
        stmt: _mapping_rows(rows) for stmt, rows in struct_by_statement.items()
    }
    # Group unknown accounts into position buckets.
    buckets: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str, str]] = []

    for acct in accounts:
        stmt = normalize_statement(acct.get("level_0"))
        # A grain classifies against its own statement's structure.  UNKNOWN
        # level_0 accounts have no target structure → always unknown (suggest PL).
        eval_stmt = stmt if stmt in ("PL", "BS") else None
        known = (
            grain_is_known(acct, mapping_by_stmt.get(eval_stmt, []))
            if eval_stmt is not None
            else False
        )
        if known:
            continue
        l2, l3, l4 = _clean(acct.get("level_2")), _clean(acct.get("level_3")), _clean(acct.get("level_4"))
        key = (stmt, l2, l3, l4)
        if key not in buckets:
            buckets[key] = {
                "statement": stmt,
                "level_1": _clean(acct.get("level_1")) or None,
                "level_2": l2 or None,
                "level_3": l3 or None,
                "level_4": l4 or None,
                "accounts": [],
            }
            order.append(key)
        bucket = buckets[key]
        if len(bucket["accounts"]) < account_cap:
            bucket["accounts"].append({
                "gl_account_id": _clean(acct.get("gl_account_id")),
                "account_name": _clean(acct.get("account_name")) or None,
                "account_number_group": _clean(acct.get("account_number_group")),
            })
        bucket["_count"] = bucket.get("_count", 0) + 1

    out: list[dict[str, Any]] = []
    for key in order:
        b = buckets[key]
        stmt = b["statement"]
        suggested_stmt = stmt if stmt in ("PL", "BS") else "PL"
        anchor = _suggest_anchor(
            struct_by_statement.get(suggested_stmt, []), b["level_2"]
        )
        out.append({
            "id": position_id(*key),
            "statement": stmt,
            "level_1": b["level_1"],
            "level_2": b["level_2"],
            "level_3": b["level_3"],
            "level_4": b["level_4"],
            "account_count": b.get("_count", len(b["accounts"])),
            "accounts": b["accounts"],
            "suggested_statement": suggested_stmt,
            "suggested_parent_line_code": anchor["parent_line_code"],
            "suggested_after_line_code": anchor["after_line_code"],
            "suggested_sort_order": anchor["after_sort_order"],
        })
    # Stable, statement-then-path order for the UI.
    out.sort(key=lambda p: (p["statement"], p["level_2"] or "", p["level_3"] or "", p["level_4"] or ""))
    return out


def _suggest_anchor(struct_rows: list[dict[str, Any]], level_2: Optional[str]) -> dict[str, Any]:
    """Best anchor row to insert a new leaf AFTER: the last row sharing level_2,
    else the last mapping row that is not a grandtotal (so the leaf lands inside a
    section, not below the grand total)."""
    l2 = _clean(level_2)
    same_section = [r for r in struct_rows if l2 and _clean(r.get("level_2")) == l2]
    pool = same_section
    if not pool:
        pool = [r for r in struct_rows if str(r.get("row_type") or "") not in ("grandtotal",)]
    if not pool:
        return {"parent_line_code": None, "after_line_code": None, "after_sort_order": None}
    anchor = max(pool, key=lambda r: int(r.get("sort_order") or 0))
    return {
        "parent_line_code": str(anchor.get("line_code")) if same_section else None,
        "after_line_code": str(anchor.get("line_code")),
        "after_sort_order": int(anchor.get("sort_order") or 0),
    }


# ---------------------------------------------------------------------------
# line_code / marker generation (reader-faithful)
# ---------------------------------------------------------------------------

def make_line_code(statement: str, level_2: str, level_3: str, level_4: str,
                   gl_account_id: str = "") -> str:
    """Stable, reader-accepted line_code for a placed leaf.

    Prefix is chosen so the target reader's ``_is_*_structure_row`` predicate
    accepts the row:  PL → no BS_/CF_ prefix; BS → 'BS_'; CF → 'CF_'.
    """
    src = (level_4 or level_3 or level_2 or gl_account_id or "POSITION").strip()
    code = re.sub(r"[^A-Za-z0-9_]", "_", src.upper())
    code = re.sub(r"_+", "_", code).strip("_") or "POSITION"
    if gl_account_id:
        code = f"{code}_{re.sub(r'[^A-Za-z0-9]', '', gl_account_id.upper())}"
    prefix = {"PL": "", "BS": "BS_", "CF": "CF_"}[statement]
    if prefix and not code.startswith(prefix):
        code = prefix + code
    return code[:64]


def _kpi_for(statement: str, section: Optional[str]) -> Optional[str]:
    """kpi_code tag so the reader's section/statement logic classifies the leaf.

    BS uses 'BS:asset' | 'BS:credit' (fin_compat_bs._bs_section); CF uses a
    'CF:detail' leaf tag; PL leaves carry no kpi_code.
    """
    if statement == "BS":
        return "BS:credit" if _clean(section).lower() == "credit" else "BS:asset"
    if statement == "CF":
        return "CF:detail"
    return None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _table_has_column(session: Session, table: str, column: str) -> bool:
    row = session.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c LIMIT 1"
        ),
        {"t": table, "c": column},
    ).fetchone()
    return row is not None


def _row_dict(r: Any) -> dict[str, Any]:
    return dict(r._mapping) if hasattr(r, "_mapping") else dict(r)


def load_structure_rows(session: Session, statement: str) -> list[dict[str, Any]]:
    """All rows of the statement's structure table (dicts), ordered by sort_order.

    Returns [] (not an error) when the table is absent, so a legacy DB degrades to
    'no unknowns' rather than 500 (golden-safety)."""
    table = TABLE_BY_STATEMENT[statement]
    try:
        rows = session.execute(text(
            f"SELECT {_STRUCT_COLUMNS} FROM {table} ORDER BY sort_order"
        )).fetchall()
    except Exception:
        return []
    return [_row_dict(r) for r in rows]


def load_coa_accounts(
    session: Session,
    *,
    fiscal_years: Optional[list[int]] = None,
    entity_prefixes: Optional[list[str]] = None,
    account_number_groups: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Distinct CoA account grains from ``dim_gl_account`` for the given scope.

    One row per distinct (account_number_group, gl_account_id, level path); the
    account's statement/levels drive detection.  Empty filters = whole chart."""
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if fiscal_years:
        clauses.append("fiscal_year = ANY(:fys)")
        params["fys"] = list(fiscal_years)
    if entity_prefixes:
        clauses.append("entity_prefix = ANY(:pfx)")
        params["pfx"] = [str(p)[:2] for p in entity_prefixes]
    if account_number_groups:
        clauses.append("account_number_group = ANY(:angs)")
        params["angs"] = [str(a) for a in account_number_groups]
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    try:
        rows = session.execute(text(
            "SELECT DISTINCT account_number_group, gl_account_id, account_name, "
            "level_0, level_1, level_2, level_3, level_4 "
            f"FROM dim_gl_account{where}"
        ), params).fetchall()
    except Exception:
        return []
    return [_row_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Placement writer
# ---------------------------------------------------------------------------

class PlacementError(ValueError):
    """Raised for an invalid placement (caller maps to HTTP 422)."""


def _validate_placement(p: dict[str, Any]) -> None:
    stmt = str(p.get("statement") or "").upper()
    if stmt not in TABLE_BY_STATEMENT:
        raise PlacementError(f"statement must be one of PL/BS/CF, got {p.get('statement')!r}")
    if str(p.get("row_type") or "mapping") != "mapping":
        raise PlacementError("row_type must be 'mapping' (auto-extension only adds leaf rows)")
    if not any(_clean(p.get(k)) for k in ("level_2", "level_3", "level_4", "gl_account_id")):
        raise PlacementError("placement needs at least one of level_2/level_3/level_4/gl_account_id")


def _existing_line_codes(session: Session, table: str) -> set[str]:
    rows = session.execute(text(f"SELECT line_code FROM {table}")).fetchall()
    return {str(r[0]) for r in rows}


def _duplicate_mapping_exists(session: Session, table: str, l2: str, l3: str, l4: str,
                              gl_account_id: str) -> bool:
    """True if an identical mapping leaf already exists (idempotency guard)."""
    row = session.execute(
        text(
            f"SELECT 1 FROM {table} WHERE row_type = 'mapping' "
            "AND COALESCE(level_2,'') = :l2 AND COALESCE(level_3,'') = :l3 "
            "AND COALESCE(level_4,'') = :l4 AND COALESCE(gl_account_id,'') = :gid LIMIT 1"
        ),
        {"l2": l2, "l3": l3, "l4": l4, "gid": gl_account_id},
    ).fetchone()
    return row is not None


def _compute_sort_order(session: Session, table: str, statement: str,
                        after_line_code: str) -> int:
    """Free, band-valid sort_order for the new leaf.

    If ``after_line_code`` anchors an existing row: insert directly after it,
    shifting later rows down by 1 (top-down, collision-free).  Else append at the
    section end.  Guards the PL sort ceiling."""
    floor = {"PL": 0, "BS": _BS_SORT_FLOOR, "CF": _CF_SORT_FLOOR}[statement]

    anchor_so: Optional[int] = None
    if after_line_code:
        r = session.execute(
            text(f"SELECT sort_order FROM {table} WHERE line_code = :lc"),
            {"lc": after_line_code},
        ).fetchone()
        if r is not None:
            anchor_so = int(r[0])

    if anchor_so is None:
        # Append at the end of the table's band.
        r = session.execute(text(f"SELECT MAX(sort_order) FROM {table}")).fetchone()
        cur_max = int(r[0]) if r and r[0] is not None else None
        new_so = (max(cur_max, floor) if cur_max is not None else floor) + _SORT_STEP
    else:
        # Insert after the anchor: is anchor_so+1 free?
        nxt = session.execute(
            text(f"SELECT MIN(sort_order) FROM {table} WHERE sort_order > :so"),
            {"so": anchor_so},
        ).fetchone()
        nxt_so = int(nxt[0]) if nxt and nxt[0] is not None else None
        if nxt_so is None or nxt_so > anchor_so + 1:
            new_so = anchor_so + 1
        else:
            # No gap — shift every later row +1, highest-first (collision-free).
            later = session.execute(
                text(f"SELECT sort_order FROM {table} WHERE sort_order > :so ORDER BY sort_order DESC"),
                {"so": anchor_so},
            ).fetchall()
            for (so,) in later:
                session.execute(
                    text(f"UPDATE {table} SET sort_order = :new WHERE sort_order = :old"),
                    {"new": int(so) + 1, "old": int(so)},
                )
            new_so = anchor_so + 1

    if statement == "PL" and new_so >= _PL_SORT_CEILING:
        raise PlacementError(
            "P&L structure is full below the BS band — renumber dim_pl_structure before extending"
        )
    return new_so


def extend_structure(session: Session, placements: list[dict[str, Any]]) -> dict[str, Any]:
    """Insert placement leaves into the correct structure tables.  Idempotent.

    Returns {'inserted': [line_code…], 'skipped': [line_code…], 'table_counts': {…}}.
    Raises ``PlacementError`` (→ 422) on an invalid placement; the caller owns the
    transaction (commit/rollback)."""
    inserted: list[str] = []
    skipped: list[str] = []
    # Cache per-table existing line_codes so a batch is internally idempotent.
    existing: dict[str, set[str]] = {}
    has_source: dict[str, bool] = {}

    for p in placements:
        _validate_placement(p)
        stmt = str(p["statement"]).upper()
        table = TABLE_BY_STATEMENT[stmt]
        if table not in existing:
            existing[table] = _existing_line_codes(session, table)
            has_source[table] = _table_has_column(session, table, "source")

        l2 = _clean(p.get("level_2"))
        l3 = _clean(p.get("level_3"))
        l4 = _clean(p.get("level_4"))
        gid = _clean(p.get("gl_account_id"))
        line_code = _clean(p.get("line_code")) or make_line_code(stmt, l2, l3, l4, gid)
        # Re-assert the statement marker even on a caller-supplied line_code.
        line_code = make_line_code(stmt, line_code, "", "") if _clean(p.get("line_code")) else line_code

        if line_code in existing[table] or _duplicate_mapping_exists(session, table, l2, l3, l4, gid):
            skipped.append(line_code)
            continue

        balance_title = _clean(p.get("balance_title")) or (l4 or l3 or l2 or line_code)
        kpi_code = _kpi_for(stmt, p.get("section"))
        sort_order = _compute_sort_order(session, table, stmt, _clean(p.get("after_line_code")))

        cols = [
            "sort_order", "line_code", "row_type", "balance_title",
            "level_2", "level_3", "level_4", "gl_account_id", "kpi_code",
        ]
        vals = {
            "sort_order": sort_order,
            "line_code": line_code,
            "row_type": "mapping",
            "balance_title": balance_title[:500],
            "level_2": l2 or None,
            "level_3": l3 or None,
            "level_4": l4 or None,
            "gl_account_id": gid or None,
            "kpi_code": kpi_code,
        }
        if has_source[table]:
            cols.append("source")
            vals["source"] = "auto_extend"

        placeholders = ", ".join(f":{c}" for c in cols)
        session.execute(
            text(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"),
            vals,
        )
        existing[table].add(line_code)
        inserted.append(line_code)

    table_counts: dict[str, int] = {}
    for table in {TABLE_BY_STATEMENT[str(p["statement"]).upper()] for p in placements}:
        r = session.execute(text(f"SELECT COUNT(*) FROM {table}")).fetchone()
        table_counts[table] = int(r[0]) if r else 0

    return {"inserted": inserted, "skipped": skipped, "table_counts": table_counts}
