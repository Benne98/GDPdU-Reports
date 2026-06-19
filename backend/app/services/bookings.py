"""P5 / Phase C7 — GL drill: statement CELL → underlying booking lines.

Given a statement CELL (a structure line + a period column + entity/scenario), this
service returns the GL booking lines that ROLL UP into that cell, so the frontend
can show a booking-journal modal whose summed presented amount TIES BACK to the cell.

======================================================================= CELL → BUCKET RESOLUTION (reuses periods.py — no forked period math)
The period engine resolves the column key to a set of (fiscal_year, fiscal_period)
buckets.  Which buckets a drill includes depends on the statement kind:

  • kind = 'pl'  (FLOW): the IN-PERIOD set = every bucket of the column.  A line
                  belongs to the cell iff its (fy, period) is one of the column's buckets.
  • kind = 'bs'  (STOCK / cumulative): the CUMULATIVE cutoff = the column's LATEST
                  bucket (fy*, p*).  A line belongs iff (fy, period) <= (fy*, p*)
                  (lexicographic), exactly like balance_sheet.aggregate_bs.

This is the SAME bucket logic the statement services use; the drill MUST resolve the
column identically or the reconciliation would break.

======================================================================= LINE SELECTOR (which accounts roll into the cell)
A statement line maps GL accounts by its level_2 / level_3 / level_4 filter (a null
level is a wildcard) — identical to statements._matches / balance_sheet._matches.
The caller passes whichever of level_2/level_3/level_4 the structure line carries,
and/or an explicit ``account_number_group`` to pin a single account.  All non-null
filters are AND-combined.  At least one selector OR an account group is required so a
drill never returns the entire ledger by accident.

======================================================================= SIGN CONVENTION (reuses the statement _present functions — never forked)
Each returned row carries BOTH:
  • ``amount``           : the RAW stored signed GL amount (+debit, −credit).
  • ``presented_amount`` : the value as the STATEMENT shows it, via the kind's own
                           sign function:
        pl → presented = −amount                      (statements._present)
        bs → presented = +amount (asset) / −amount (credit), per ``bs_side``
                                                        (balance_sheet._present_bs)
The ``total`` returned is Σ ``presented_amount`` over the FULL filtered set (NOT just
the returned page), so the modal can show "showing N of total, sum = X" and the sum
ALWAYS equals the statement cell value.

======================================================================= RECONCILIATION GUARANTEE (asserted in the golden test)
    Σ presented_amount over drill(line, column, entity)  ==  cell_value(line, column, entity)
for the same statement kind, line selector, column, and entity.  This holds because
the drill uses the SAME buckets, the SAME account match, and the SAME sign function
as the statement aggregation.  PL uses in-period buckets; BS uses the cumulative
cutoff.  The test pins both on synthetic data.

======================================================================= PAGINATION
``limit`` (default 200, hard cap 1000) and ``offset`` page the returned ROWS only.
``total_count`` (number of rows in the full filter) and ``total`` (Σ presented over
the full filter) are computed over the WHOLE set, independent of limit/offset, so the
reconciliation and the "N of total" message are correct under pagination.

======================================================================= EDGE CASES
  • Empty column (no buckets, e.g. YTD with L=0): PL → no buckets → 0 rows, total 0.0
    (== the cell, which is also 0).  BS → no cutoff → 0 rows, total 0.0.
  • offset past the end → empty page, but total_count/total still reflect the full set.
  • No level selector AND no account group → ValueError (refuse a full-ledger drill).
  • scenario given → drills fact_gl_plan movements (no per-line journal grain there);
    documented limitation: plan has no booking_line_id, so plan drill is NOT supported
    in C7 (raise a clear error). Actuals only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# Hard cap on rows returned in a single page regardless of requested limit.
MAX_LIMIT = 1000
DEFAULT_LIMIT = 200


@dataclass
class BookingRow:
    booking_line_id: int
    journal_entry_group_number: str
    fiscal_year: int
    fiscal_period: int
    line_number: int
    account_number_group: str
    account_name: Optional[str]
    level_2: Optional[str]
    level_3: Optional[str]
    level_4: Optional[str]
    posting_date: Optional[str]
    document_date: Optional[str]
    document_type_code: Optional[str]
    reference_document_number: Optional[str]
    line_note: Optional[str]
    entity_prefix: Optional[str]
    amount: float            # raw stored sign (+debit, −credit)
    presented_amount: float  # as the statement presents it (kind sign convention)


@dataclass
class BookingsResult:
    kind: str
    column_key: str
    total_count: int          # rows in the FULL filtered set
    total: float              # Σ presented_amount over the FULL filtered set
    limit: int
    offset: int
    rows: list[BookingRow]    # the current page


# --------------------------------------------------------------------------- #
# Sign — reuse the statement functions (never forked here)
# --------------------------------------------------------------------------- #
def _present_pl(amount: float) -> float:
    from app.services.statements import _present

    return _present(amount)


def _present_bs(amount: float, side: str) -> float:
    from app.services.balance_sheet import _present_bs as _pbs

    return _pbs(amount, side)


# --------------------------------------------------------------------------- #
# Column → (fy, period) predicate, reusing the period engine
# --------------------------------------------------------------------------- #
def resolve_column_buckets(
    kind: str,
    column_key: str,
    *,
    view_mode: str,
    current_fy: int,
    last_closed_period: int,
    fy_start_month: int = 1,
) -> tuple[Optional[set[tuple[int, int]]], Optional[tuple[int, int]]]:
    """Return (in_period_set, cumulative_cutoff) for the requested column.

    Exactly ONE of the two is meaningful per kind:
      • pl → (set of buckets, None)        — drill includes lines whose (fy,p) ∈ set.
      • bs → (None, cutoff)                — drill includes lines whose (fy,p) <= cutoff.
    Empty PL column → (empty set, None).  Empty BS column → (None, None) → no rows.
    Raises ValueError if column_key is not in the plan.
    """
    from app.services.periods import build_period_plan

    plan = build_period_plan(
        view_mode,  # type: ignore[arg-type]
        current_fy,
        last_closed_period,
        fy_start_month=fy_start_month,
    )
    col = next((c for c in plan.columns if c.key == column_key), None)
    if col is None:
        raise ValueError(
            f"unknown column {column_key!r} for view_mode={view_mode!r}"
        )
    if kind == "bs":
        cutoff = max(col.buckets) if col.buckets else None
        return (None, cutoff)
    # pl (and any flow kind): in-period buckets
    return (set(col.buckets), None)


# --------------------------------------------------------------------------- #
# DB drill (thin; bound params only — no f-string interpolation of user input)
# --------------------------------------------------------------------------- #
def fetch_bookings(
    session: Any,
    *,
    kind: str,
    in_period: Optional[set[tuple[int, int]]],
    cutoff: Optional[tuple[int, int]],
    level_2: Optional[str],
    level_3: Optional[str],
    level_4: Optional[str],
    account_number_group: Optional[str],
    bs_side: str = "asset",
    entity_prefix: Optional[str] = None,
    fiscal_years: tuple[int, ...] = (),
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> BookingsResult:
    """Read the GL booking lines for one statement cell.

    SQL joins fact_gl_line × fact_gl_entry × dim_gl_account.  ALL user-supplied values
    are passed as BOUND parameters; only fixed predicate fragments are concatenated.
    The aggregate (total_count, total) is computed over the FULL filter via a separate
    aggregate query so pagination never changes the reconciliation total.
    """
    from sqlalchemy import text

    # ---- WHERE fragments (static text only; values bound) ---------------------
    where: list[str] = ["a.level_0 = :level0", "e.fiscal_period BETWEEN 1 AND 12"]
    params: dict[str, Any] = {"level0": "BS" if kind == "bs" else "PL"}

    if level_2 is not None:
        where.append("a.level_2 = :level_2")
        params["level_2"] = level_2
    if level_3 is not None:
        where.append("a.level_3 = :level_3")
        params["level_3"] = level_3
    if level_4 is not None:
        where.append("a.level_4 = :level_4")
        params["level_4"] = level_4
    if account_number_group is not None:
        where.append("l.account_number_group = :ang")
        params["ang"] = account_number_group
    if entity_prefix is not None:
        where.append("a.entity_prefix = :entity")
        params["entity"] = entity_prefix

    # ---- period predicate -----------------------------------------------------
    if kind == "bs":
        if cutoff is None:
            # No cutoff → empty column → no rows, total 0.
            return BookingsResult(kind, "", 0, 0.0, limit, offset, [])
        cfy, cper = cutoff
        # (fy, period) <= (cfy, cper) lexicographically.
        where.append(
            "(e.fiscal_year < :cfy OR (e.fiscal_year = :cfy AND e.fiscal_period <= :cper))"
        )
        params["cfy"] = cfy
        params["cper"] = cper
    else:
        if not in_period:
            return BookingsResult(kind, "", 0, 0.0, limit, offset, [])
        # Build an IN list of (fy, period) via bound params (tuple membership).
        # Use a VALUES join-free approach: OR of equalities is fine for <=24 buckets.
        ors: list[str] = []
        for i, (fy, per) in enumerate(sorted(in_period)):
            ors.append(f"(e.fiscal_year = :fy{i} AND e.fiscal_period = :per{i})")
            params[f"fy{i}"] = fy
            params[f"per{i}"] = per
        where.append("(" + " OR ".join(ors) + ")")

    if fiscal_years:
        where.append("l.fiscal_year = ANY(:years)")
        params["years"] = sorted(set(int(y) for y in fiscal_years))

    where_sql = " AND ".join(where)

    # ---- aggregate over the FULL filter (count + Σ raw amount) ----------------
    agg_sql = text(f"""
        SELECT COUNT(*) AS n, COALESCE(SUM(l.amount), 0) AS sum_amount
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE {where_sql}
    """)
    agg = session.execute(agg_sql, params).fetchone()
    total_count = int(agg[0]) if agg else 0
    sum_amount = float(agg[1]) if agg and agg[1] is not None else 0.0
    # Σ presented over the full set = the kind's sign applied to Σ raw amount.
    # (Both _present and _present_bs are linear: Σ present(x) = present(Σ x).)
    if kind == "bs":
        total_presented = _present_bs(sum_amount, bs_side)
    else:
        total_presented = _present_pl(sum_amount)

    # ---- page of rows ---------------------------------------------------------
    eff_limit = max(1, min(int(limit), MAX_LIMIT))
    eff_offset = max(0, int(offset))
    page_params = dict(params)
    page_params["lim"] = eff_limit
    page_params["off"] = eff_offset

    rows_sql = text(f"""
        SELECT
            l.booking_line_id, l.journal_entry_group_number,
            l.fiscal_year, e.fiscal_period, l.line_number,
            l.account_number_group, a.account_name,
            a.level_2, a.level_3, a.level_4,
            e.posting_date, e.document_date, e.document_type_code,
            e.reference_document_number, l.line_note, a.entity_prefix,
            l.amount
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE {where_sql}
        ORDER BY e.posting_date, l.journal_entry_group_number, l.line_number
        LIMIT :lim OFFSET :off
    """)
    raw = session.execute(rows_sql, page_params).fetchall()

    out_rows: list[BookingRow] = []
    for r in raw:
        amt = float(r[16])
        presented = _present_bs(amt, bs_side) if kind == "bs" else _present_pl(amt)
        out_rows.append(
            BookingRow(
                booking_line_id=int(r[0]),
                journal_entry_group_number=r[1],
                fiscal_year=int(r[2]),
                fiscal_period=int(r[3]),
                line_number=int(r[4]),
                account_number_group=r[5],
                account_name=r[6],
                level_2=r[7],
                level_3=r[8],
                level_4=r[9],
                posting_date=str(r[10]) if r[10] is not None else None,
                document_date=str(r[11]) if r[11] is not None else None,
                document_type_code=r[12],
                reference_document_number=r[13],
                line_note=r[14],
                entity_prefix=r[15],
                amount=amt,
                presented_amount=presented,
            )
        )

    return BookingsResult(
        kind=kind,
        column_key="",  # set by the orchestrator
        total_count=total_count,
        total=total_presented,
        limit=eff_limit,
        offset=eff_offset,
        rows=out_rows,
    )


# --------------------------------------------------------------------------- #
# Orchestration (thin)
# --------------------------------------------------------------------------- #
def build_bookings(
    session: Any,
    *,
    kind: str,
    column_key: str,
    view_mode: str,
    current_fy: int,
    last_closed_period: int,
    level_2: Optional[str] = None,
    level_3: Optional[str] = None,
    level_4: Optional[str] = None,
    account_number_group: Optional[str] = None,
    bs_side: str = "asset",
    entity_prefix: Optional[str] = None,
    scenario: Optional[str] = None,
    fy_start_month: int = 1,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> BookingsResult:
    """Resolve the column → buckets, then drill the GL for the cell.

    Refuses a full-ledger drill (no selector) and refuses plan/scenario drill (plan
    has no booking-line grain in C7).  ``kind`` ∈ {pl, bs}.
    """
    if kind not in ("pl", "bs"):
        raise ValueError(f"drill supports kind pl|bs, got {kind!r}")
    if scenario:
        raise ValueError(
            "scenario/plan drill is not supported (fact_gl_plan has no booking-line grain)"
        )
    if level_2 is None and level_3 is None and level_4 is None and account_number_group is None:
        raise ValueError(
            "at least one of level_2/level_3/level_4 or account_number_group is required"
        )

    in_period, cutoff = resolve_column_buckets(
        kind,
        column_key,
        view_mode=view_mode,
        current_fy=current_fy,
        last_closed_period=last_closed_period,
        fy_start_month=fy_start_month,
    )
    # Fiscal years referenced (narrows the index scan; bound param).
    years: tuple[int, ...]
    if kind == "bs":
        years = tuple(range(2000, cutoff[0] + 1)) if cutoff else ()
        # The cutoff predicate already bounds the upper end; pass no ANY filter to
        # avoid an unbounded range — rely on the cutoff predicate instead.
        years = ()
    else:
        years = tuple(sorted({fy for fy, _ in (in_period or set())}))

    result = fetch_bookings(
        session,
        kind=kind,
        in_period=in_period,
        cutoff=cutoff,
        level_2=level_2,
        level_3=level_3,
        level_4=level_4,
        account_number_group=account_number_group,
        bs_side=bs_side,
        entity_prefix=entity_prefix,
        fiscal_years=years,
        limit=limit,
        offset=offset,
    )
    result.column_key = column_key
    return result
