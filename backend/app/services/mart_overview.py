"""Overview v2 pre-aggregation mart — OPTIONAL read accelerator (P3).

STATUS: STAGED / OFF-BY-DEFAULT.  This service (re)populates and reads the
``mart_overview_period`` / ``mart_overview_bs_balance`` snapshot tables added in
migration ``0026_mart_overview_period``.  It is an ACCELERATOR only: the live
builders in :mod:`app.services.overview_metrics` remain the correctness source of
truth.  The :8011 summary endpoint must use the mart ONLY when it is fresh
(:func:`mart_is_fresh`) AND validated on real data by the parent; until then the
endpoint falls back to the builders.  Do NOT wire any of this into the shared
ingest/derive path — refresh is lazy / on-demand from the endpoint.

DESIGN
------
* ``mart_overview_period.amount_sum`` and ``mart_overview_bs_balance.balance_sum``
  store the RAW stored GL sign (``SUM(fact_gl_line.amount)``).  Presentation is
  applied HERE, in the read helper, to stay byte-identical to the builders:
    - P&L presented = ``amount_sum * -1``  (revenue +, cost −)   [as overview_metrics]
    - BS  magnitude  = raw cumulative balance (caller ABS's for DuPont).
* :func:`read_overview_period` returns the SAME per-``entity_prefix`` grain shape
  (:data:`app.services.overview_metrics._EBIT_FIELDS`) that
  :func:`app.services.overview_metrics.build_ebit_table` computes internally, so
  the endpoint can feed it straight into ``build_ebit_rows`` and get an identical
  ``EbitTableData`` — this is the accelerator equivalence proven in
  ``backend/tests/test_mart_overview_equivalence.py``.

FAIL-CLOSED ENTITY VISIBILITY
-----------------------------
Both refresh and read accept ``allowed_entities`` — a set of 2-char
``entity_prefix`` values — with the SAME fail-closed semantics as
:func:`app.services.entity_visibility.visible_entity_codes`:
    * ``None``      → no restriction (all entities)
    * a non-empty   → exactly those prefixes
    * ``set()``     → NOTHING (deny-all)
The endpoint is responsible for resolving ``visible_entity_codes()``
(legal_entity_code) → entity_prefix (via ``resolve_entity_prefixes``) before
calling; this module is prefix-native so the fail-closed rule cannot drift.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.fin_compat_bs_sql import _opening_snapshot_date_sql
from app.services.fin_compat_sql import last_day, pm

# Earliest GoBD opening-balance snapshot date (global across all accounts).  This is
# the ONLY opening_balance carry-forward the cumulative WC stock keeps — exactly the
# rule the proven live BS balance expr uses (fin_compat_bs_sql._bal_amount_expr):
# earliest-OB seed + all subsequent non-OB movements.  Later annual Jan-1 OB rows are
# dropped so the running stock does not re-add the opening every fiscal year.
_OB_SNAPSHOT_DATE_SQL = _opening_snapshot_date_sql()

# EBIT bucket definition — MUST match overview_metrics._ebit_filters() /
# _DUPONT_PL_FILTERS exactly so the accelerator stays builder-identical.
_TOTAL_OUTPUT_LEVEL_3 = "Net sales"
_EBIT_LEVEL_2 = {"Income", "Expense"}
_EBIT_EXCLUDE_LEVEL_3 = {"Financial result", "Income taxes"}

# Same field set overview_metrics.build_ebit_rows consumes.
_EBIT_FIELDS = [
    "to_cm_py", "to_pm", "to_cm", "to_ytd",
    "ebit_cm_py", "ebit_pm", "ebit_cm", "ebit_ytd",
]


# ---------------------------------------------------------------------------
# Fail-closed helpers
# ---------------------------------------------------------------------------

def _deny_all(allowed_entities: Optional[set[str]]) -> bool:
    """True when the allow-list explicitly grants nothing (empty set)."""
    return allowed_entities is not None and len(allowed_entities) == 0


def _entity_allowed(prefix: str, allowed_entities: Optional[set[str]]) -> bool:
    if allowed_entities is None:
        return True
    return prefix in allowed_entities


def _prefix_fragment(
    allowed_entities: Optional[set[str]], alias: str = "l", *,
    column: Optional[str] = None,
) -> str:
    """SQL AND-fragment restricting entity_prefix to the allow-list.

    ``None`` → no restriction ('').  Empty set is handled by the caller BEFORE
    building SQL (deny-all), never reaching here.  Prefixes are 2-char CHARs so
    inlining after sanitisation is injection-safe (same rule as
    ``fin_compat_sql.entities_sql_fragment``).

    ``column`` overrides the filtered expression for fact tables that carry no
    ``entity_prefix`` column (e.g. ``LEFT(f.account_number_group, 2)`` on
    fact_sales / fact_com); when omitted it defaults to ``{alias}.entity_prefix``
    so every existing caller keeps its exact behaviour.
    """
    if allowed_entities is None:
        return ""
    safe = sorted({str(p).replace("'", "")[:2] for p in allowed_entities if p})
    col = column or f"{alias}.entity_prefix"
    if not safe:
        # Should be unreachable (deny-all caught earlier); fail closed anyway.
        return "AND 1 = 0"
    inner = ", ".join(f"'{p}'" for p in safe)
    return f"AND {col} IN ({inner})"


# ---------------------------------------------------------------------------
# Pure aggregators (DB-free, unit-tested)
# ---------------------------------------------------------------------------

def _is_ebit(level_2: Optional[str], level_3: Optional[str]) -> bool:
    return level_2 in _EBIT_LEVEL_2 and (level_3 or "") not in _EBIT_EXCLUDE_LEVEL_3


def aggregate_pl_periods(
    rows: Iterable[dict[str, Any]], *, allowed_entities: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    """Group raw GL P&L lines into mart P&L rows (Σ RAW amount).

    ``rows`` — canonical GL join rows, each a dict with keys:
      ``entity_prefix, fiscal_year, fiscal_period, level_2, level_3, amount``
      (``amount`` = raw stored ``fact_gl_line.amount``; ``level_0`` implied 'PL').

    Returns one row per (entity_prefix, fiscal_year, fiscal_period, level_3) with
    ``amount_sum`` = Σ raw amount, plus carried ``level_0``/``level_2``.

    FAIL-CLOSED: ``allowed_entities == set()`` → ``[]``.
    """
    if _deny_all(allowed_entities):
        return []
    agg: dict[tuple, dict[str, Any]] = {}
    for r in rows:
        ep = (r.get("entity_prefix") or "").strip()
        if not _entity_allowed(ep, allowed_entities):
            continue
        fy = int(r["fiscal_year"])
        fp = int(r["fiscal_period"])
        l3 = r.get("level_3")
        key = (ep, fy, fp, l3)
        slot = agg.get(key)
        if slot is None:
            slot = {
                "entity_prefix": ep,
                "fiscal_year": fy,
                "fiscal_period": fp,
                "level_0": r.get("level_0") or "PL",
                "level_2": r.get("level_2"),
                "level_3": l3,
                "amount_sum": 0.0,
            }
            agg[key] = slot
        slot["amount_sum"] += float(r.get("amount") or 0.0)
    for slot in agg.values():
        slot["amount_sum"] = round(slot["amount_sum"], 2)
    return list(agg.values())


def cumulate_bs_movements(
    movements: Iterable[dict[str, Any]], *, allowed_entities: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    """Turn per-month BS movement sums into cumulative stock balances.

    ``movements`` — one row per (entity_prefix, level_3, cutoff_date) with
      ``amount_sum`` = Σ raw amount posted IN that month, plus ``level_0``/``level_2``.
    Cumulating over sorted ``cutoff_date`` per (entity_prefix, level_3) reproduces
    ``Σ amount WHERE posting_date <= cutoff`` exactly (the BS stock convention in
    ``overview_metrics.build_dupont``).

    Returns mart BS rows with ``balance_sum`` = running cumulative total.
    FAIL-CLOSED: ``allowed_entities == set()`` → ``[]``.
    """
    if _deny_all(allowed_entities):
        return []
    # bucket by (entity, level_3); keep level_0/level_2 from first sighting.
    buckets: dict[tuple, list[dict[str, Any]]] = {}
    for m in movements:
        ep = (m.get("entity_prefix") or "").strip()
        if not _entity_allowed(ep, allowed_entities):
            continue
        key = (ep, m.get("level_3"))
        buckets.setdefault(key, []).append(m)

    out: list[dict[str, Any]] = []
    for (ep, l3), rows in buckets.items():
        rows_sorted = sorted(rows, key=lambda r: _as_date(r["cutoff_date"]))
        running = 0.0
        for r in rows_sorted:
            running += float(r.get("amount_sum") or 0.0)
            out.append({
                "entity_prefix": ep,
                "cutoff_date": _as_date(r["cutoff_date"]),
                "level_0": r.get("level_0") or "BS",
                "level_2": r.get("level_2"),
                "level_3": l3,
                "balance_sum": round(running, 2),
            })
    return out


def cumulate_wc_movements(
    movements: Iterable[dict[str, Any]], *, allowed_entities: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    """Turn per-month WC movement sums into cumulative stock balances.

    Working-capital variant of :func:`cumulate_bs_movements`: the bucket key adds
    ``l6_na_mapping`` ('TWC'|'OWC') so TWC and OWC balances for the SAME level_3
    cumulate independently (they are distinct WC sections).  Kept SEPARATE from
    ``cumulate_bs_movements`` so the existing BS caller / equivalence test is
    untouched.

    ``movements`` — one row per (entity_prefix, l6_na_mapping, level_3, cutoff_date)
      with ``amount_sum`` = Σ RAW amount posted IN that month, plus ``level_2``.
    Cumulating over sorted ``cutoff_date`` per (entity_prefix, l6_na_mapping,
    level_3) reproduces ``Σ amount WHERE posting_date <= cutoff`` exactly (the WC
    stock convention in ``fin_compat_wc_sql``), keeping the RAW stored sign (assets
    positive, liabilities negative) — NO ``* -1``.

    Returns mart WC rows with ``balance_sum`` = running cumulative total.
    FAIL-CLOSED: ``allowed_entities == set()`` → ``[]``.
    """
    if _deny_all(allowed_entities):
        return []
    buckets: dict[tuple, list[dict[str, Any]]] = {}
    for m in movements:
        ep = (m.get("entity_prefix") or "").strip()
        if not _entity_allowed(ep, allowed_entities):
            continue
        key = (ep, m.get("l6_na_mapping"), m.get("level_3"))
        buckets.setdefault(key, []).append(m)

    out: list[dict[str, Any]] = []
    for (ep, l6, l3), rows in buckets.items():
        rows_sorted = sorted(rows, key=lambda r: _as_date(r["cutoff_date"]))
        running = 0.0
        for r in rows_sorted:
            running += float(r.get("amount_sum") or 0.0)
            out.append({
                "entity_prefix": ep,
                "cutoff_date": _as_date(r["cutoff_date"]),
                "l6_na_mapping": l6,
                "level_2": r.get("level_2"),
                "level_3": l3,
                "balance_sum": round(running, 2),
            })
    return out


def _as_date(v: Any) -> date:
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


# ---------------------------------------------------------------------------
# Grain reconstruction — builder-identical (feeds overview_metrics.build_ebit_rows)
# ---------------------------------------------------------------------------

def ebit_grain_from_pl_rows(
    pl_rows: Iterable[dict[str, Any]], *, year: int, month: int,
    allowed_entities: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    """Reconstruct the per-``entity_prefix`` EBIT grain from mart P&L rows.

    Applies the ONLY presentation inversion (``amount_sum * -1``) and the EBIT /
    total-output bucket rules that ``overview_metrics._ebit_filters`` uses, so the
    output is drop-in for ``build_ebit_rows`` — i.e. identical to
    ``build_ebit_table``'s internal grain.

    Columns produced per entity: ``_EBIT_FIELDS`` (to_cm_py/pm/cm/ytd + ebit_*).
    FAIL-CLOSED: ``allowed_entities == set()`` → ``[]``.
    """
    if _deny_all(allowed_entities):
        return []
    py = year - 1
    pm_y, pm_m = pm(year, month)

    grain: dict[str, dict[str, Any]] = {}
    for r in pl_rows:
        ep = (r.get("entity_prefix") or "").strip()
        if not _entity_allowed(ep, allowed_entities):
            continue
        fy = int(r["fiscal_year"])
        fp = int(r["fiscal_period"])
        l2 = r.get("level_2")
        l3 = r.get("level_3")
        presented = float(r.get("amount_sum") or 0.0) * -1.0

        slot = grain.get(ep)
        if slot is None:
            slot = {"entity_prefix": ep, **{f: 0.0 for f in _EBIT_FIELDS}}
            grain[ep] = slot

        is_output = (l3 == _TOTAL_OUTPUT_LEVEL_3)
        is_ebit = _is_ebit(l2, l3)

        # total output (Net sales)
        if is_output:
            if fy == py and fp == month:
                slot["to_cm_py"] += presented
            if fy == pm_y and fp == pm_m:
                slot["to_pm"] += presented
            if fy == year and fp == month:
                slot["to_cm"] += presented
            if fy == year and fp <= month:
                slot["to_ytd"] += presented
        # EBIT bucket
        if is_ebit:
            if fy == py and fp == month:
                slot["ebit_cm_py"] += presented
            if fy == pm_y and fp == pm_m:
                slot["ebit_pm"] += presented
            if fy == year and fp == month:
                slot["ebit_cm"] += presented
            if fy == year and fp <= month:
                slot["ebit_ytd"] += presented

    for slot in grain.values():
        for f in _EBIT_FIELDS:
            slot[f] = round(slot[f], 2)
    return sorted(grain.values(), key=lambda s: s["entity_prefix"])


# ---------------------------------------------------------------------------
# DB refresh / read / staleness
# ---------------------------------------------------------------------------

_PL_REFRESH_SQL = """
    SELECT
        l.entity_prefix                    AS entity_prefix,
        l.fiscal_year                      AS fiscal_year,
        e.fiscal_period                    AS fiscal_period,
        a.level_0                          AS level_0,
        a.level_2                          AS level_2,
        a.level_3                          AS level_3,
        -- Alias MUST be ``amount`` (not ``amount_sum``): aggregate_pl_periods
        -- reads r["amount"] per its documented input contract.  A prior
        -- ``AS amount_sum`` alias made r.get("amount") None → every PL mart row
        -- was written as 0.00 (silent all-zero mart).  BS path below is correct
        -- (cumulate_bs_movements reads ``amount_sum``).
        COALESCE(SUM(l.amount), 0)         AS amount
    FROM fact_gl_line l
    JOIN fact_gl_entry e
      ON e.journal_entry_group_number = l.journal_entry_group_number
     AND e.fiscal_year = l.fiscal_year
    JOIN dim_gl_account a
      ON a.account_number_group = l.account_number_group
     AND a.fiscal_year = l.fiscal_year
    WHERE a.level_0 = 'PL'
      {ent_frag}
    GROUP BY l.entity_prefix, l.fiscal_year, e.fiscal_period,
             a.level_0, a.level_2, a.level_3
"""

_BS_MOVEMENT_SQL = """
    SELECT
        l.entity_prefix                    AS entity_prefix,
        (date_trunc('month', e.posting_date)
            + INTERVAL '1 month - 1 day')::date AS cutoff_date,
        a.level_0                          AS level_0,
        a.level_2                          AS level_2,
        a.level_3                          AS level_3,
        COALESCE(SUM(l.amount), 0)         AS amount_sum
    FROM fact_gl_line l
    JOIN fact_gl_entry e
      ON e.journal_entry_group_number = l.journal_entry_group_number
     AND e.fiscal_year = l.fiscal_year
    JOIN dim_gl_account a
      ON a.account_number_group = l.account_number_group
     AND a.fiscal_year = l.fiscal_year
    WHERE a.level_0 = 'BS'
      AND e.posting_date IS NOT NULL
      {ent_frag}
    GROUP BY l.entity_prefix, cutoff_date, a.level_0, a.level_2, a.level_3
"""


_WC_MOVEMENT_SQL = f"""
    SELECT
        l.entity_prefix                    AS entity_prefix,
        (date_trunc('month', e.posting_date)
            + INTERVAL '1 month - 1 day')::date AS cutoff_date,
        na.l6_na_mapping                   AS l6_na_mapping,
        a.level_2                          AS level_2,
        a.level_3                          AS level_3,
        -- Opening-balance dedup (mirrors the proven fin_compat_bs_sql._bal_amount_expr
        -- earliest-OB-only carry-forward): keep EVERY non-opening movement, but include
        -- the opening_balance row ONLY at the EARLIEST global OB snapshot date.  Later
        -- annual Jan-1 carry-forward OB rows contribute 0, so cumulating does NOT
        -- re-add the opening each fiscal year (was: raw SUM(l.amount) over ALL OB rows,
        -- which over-counted every annual opening — Bug 1).
        COALESCE(SUM(CASE
            WHEN COALESCE(e.entry_type, '') <> 'opening_balance' THEN l.amount
            WHEN e.entry_type = 'opening_balance'
             AND e.posting_date = {_OB_SNAPSHOT_DATE_SQL} THEN l.amount
            ELSE 0
        END), 0)                           AS amount_sum
    FROM fact_gl_line l
    JOIN fact_gl_entry e
      ON e.journal_entry_group_number = l.journal_entry_group_number
     AND e.fiscal_year = l.fiscal_year
    JOIN dim_gl_account a
      ON a.account_number_group = l.account_number_group
     AND a.fiscal_year = l.fiscal_year
    JOIN dim_gl_na na
      ON na.account_number_group = l.account_number_group
     AND na.fiscal_year = l.fiscal_year
    WHERE a.level_0 = 'BS'
      AND na.l6_na_mapping IN ('TWC', 'OWC')
      AND e.posting_date IS NOT NULL
      {{ent_frag}}
    GROUP BY l.entity_prefix, cutoff_date, na.l6_na_mapping, a.level_2, a.level_3
"""

# Partner (customer/supplier) monthly turnover magnitudes.  fact_sales / fact_com
# carry no ``entity_prefix`` column (they key on ``account_number_group``), so the
# entity is derived as ``LEFT(f.account_number_group, 2)`` — the same rule the live
# ``overview_top_entities.build_top_entities`` uses.  Account filter (level_3) and
# value/id columns are also lifted verbatim from that builder.
_TOP_ENTITY_SPECS = (
    # partner_type, fact, dim, value_col, id_col, level_3 account filter
    ("customer", "fact_sales", "dim_customer", "gross_sales", "customer_id", "Net sales"),
    ("supplier", "fact_com", "dim_supplier", "cost_of_materials", "supplier_id", "Cost of materials"),
)


def latest_gl_load_id(session: Session) -> Optional[int]:
    """Newest GL load id (staleness anchor), or None if no GL load exists."""
    row = session.execute(text(
        "SELECT MAX(load_id) FROM org_meta_dataset_load WHERE dataset = 'gl'"
    )).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def latest_partner_fact_load_id(session: Session) -> Optional[int]:
    """Newest load feeding the partner facts (fact_sales / fact_com).

    In this stack fact_sales / fact_com are DERIVED from the GL load (there is no
    dedicated 'sales'/'com' dataset), so this prefers any dedicated partner load if
    one ever exists and otherwise falls back to the 'gl' load — the true provenance
    of the top-entities magnitudes.  ``None`` when nothing is loaded.
    """
    row = session.execute(text(
        "SELECT MAX(load_id) FROM org_meta_dataset_load "
        "WHERE dataset IN ('sales', 'com', 'gl')"
    )).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def mart_is_fresh(session: Session, latest_load_id: Optional[int],
                  *, project_id: str = "default") -> bool:
    """True iff the mart was built from ``latest_load_id`` (or newer).

    Empty mart (no source_load_id) → NOT fresh.  ``latest_load_id is None`` (no GL
    loaded at all) → NOT fresh (nothing to serve).  The endpoint uses this to
    decide mart-vs-builder; on False it MUST fall back to the live builders.
    """
    if latest_load_id is None:
        return False
    row = session.execute(text(
        "SELECT MAX(source_load_id) FROM mart_overview_period "
        "WHERE project_id = :pid"
    ), {"pid": project_id}).fetchone()
    if not row or row[0] is None:
        return False
    return int(row[0]) >= int(latest_load_id)


def refresh_mart_overview_period(
    session: Session, *, allowed_entities: Optional[set[str]] = None,
    project_id: str = "default",
) -> dict[str, Any]:
    """(Re)populate the mart from the canonical GL joins. Idempotent (delete+insert).

    Lazy / on-demand ONLY — never call from the shared ingest/derive path.
    Honors ``allowed_entities`` (fail-closed: ``set()`` → writes nothing).
    Scope of the delete matches the refresh scope so re-running is idempotent.
    """
    if _deny_all(allowed_entities):
        return {"pl_rows": 0, "bs_rows": 0, "skipped": "fail_closed_empty"}

    source_load_id = latest_gl_load_id(session)
    ent_frag = _prefix_fragment(allowed_entities)

    pl_raw = [dict(r._mapping) for r in
              session.execute(text(_PL_REFRESH_SQL.format(ent_frag=ent_frag))).fetchall()]
    bs_mov = [dict(r._mapping) for r in
              session.execute(text(_BS_MOVEMENT_SQL.format(ent_frag=ent_frag))).fetchall()]

    # SQL already groups P&L to the mart grain; run through the pure aggregator so
    # the allow-list is enforced identically in Python (defence in depth).
    pl_rows = aggregate_pl_periods(pl_raw, allowed_entities=allowed_entities)
    bs_rows = cumulate_bs_movements(bs_mov, allowed_entities=allowed_entities)

    # ── delete refresh scope ────────────────────────────────────────────────
    del_frag = _prefix_fragment(allowed_entities, alias="t")
    session.execute(text(
        f"DELETE FROM mart_overview_period t WHERE t.project_id = :pid {del_frag}"
    ), {"pid": project_id})
    session.execute(text(
        f"DELETE FROM mart_overview_bs_balance t WHERE t.project_id = :pid {del_frag}"
    ), {"pid": project_id})

    # ── reinsert ────────────────────────────────────────────────────────────
    if pl_rows:
        session.execute(text(
            "INSERT INTO mart_overview_period"
            " (project_id, entity_prefix, fiscal_year, fiscal_period,"
            "  level_0, level_2, level_3, amount_sum, source_load_id)"
            " VALUES (:pid, :ep, :fy, :fp, :l0, :l2, :l3, :amt, :load)"
        ), [
            {"pid": project_id, "ep": r["entity_prefix"], "fy": r["fiscal_year"],
             "fp": r["fiscal_period"], "l0": r["level_0"], "l2": r["level_2"],
             "l3": r["level_3"], "amt": r["amount_sum"], "load": source_load_id}
            for r in pl_rows
        ])
    if bs_rows:
        session.execute(text(
            "INSERT INTO mart_overview_bs_balance"
            " (project_id, entity_prefix, cutoff_date,"
            "  level_0, level_2, level_3, balance_sum, source_load_id)"
            " VALUES (:pid, :ep, :cd, :l0, :l2, :l3, :bal, :load)"
        ), [
            {"pid": project_id, "ep": r["entity_prefix"], "cd": r["cutoff_date"],
             "l0": r["level_0"], "l2": r["level_2"], "l3": r["level_3"],
             "bal": r["balance_sum"], "load": source_load_id}
            for r in bs_rows
        ])

    return {
        "pl_rows": len(pl_rows),
        "bs_rows": len(bs_rows),
        "source_load_id": source_load_id,
    }


def refresh_mart_wc_balance(
    session: Session, *, allowed_entities: Optional[set[str]] = None,
    project_id: str = "default",
) -> dict[str, Any]:
    """(Re)populate ``mart_overview_wc_balance`` from the canonical GL joins.

    Working-capital SUBSET of the BS: INNER JOIN dim_gl_na, level_0='BS' AND
    l6_na_mapping IN ('TWC','OWC'), grouped to per-month movements then cumulated
    per (entity_prefix, l6_na_mapping, level_3) into month-end cutoff stock
    balances (RAW sign — assets +, liabilities −).  Idempotent (delete+insert over
    the refresh scope).  Lazy / operator-run ONLY — never on the shared ingest path.
    Honors ``allowed_entities`` (fail-closed: ``set()`` → writes nothing).
    """
    if _deny_all(allowed_entities):
        return {"wc_rows": 0, "skipped": "fail_closed_empty"}

    source_load_id = latest_gl_load_id(session)
    ent_frag = _prefix_fragment(allowed_entities, alias="l")

    mov = [dict(r._mapping) for r in
           session.execute(text(_WC_MOVEMENT_SQL.format(ent_frag=ent_frag))).fetchall()]
    wc_rows = cumulate_wc_movements(mov, allowed_entities=allowed_entities)

    del_frag = _prefix_fragment(allowed_entities, alias="t")
    session.execute(text(
        f"DELETE FROM mart_overview_wc_balance t WHERE t.project_id = :pid {del_frag}"
    ), {"pid": project_id})

    if wc_rows:
        session.execute(text(
            "INSERT INTO mart_overview_wc_balance"
            " (project_id, entity_prefix, cutoff_date, l6_na_mapping,"
            "  level_2, level_3, balance_sum, source_load_id)"
            " VALUES (:pid, :ep, :cd, :l6, :l2, :l3, :bal, :load)"
        ), [
            {"pid": project_id, "ep": r["entity_prefix"], "cd": r["cutoff_date"],
             "l6": r["l6_na_mapping"], "l2": r["level_2"], "l3": r["level_3"],
             "bal": r["balance_sum"], "load": source_load_id}
            for r in wc_rows
        ])

    return {"wc_rows": len(wc_rows), "source_load_id": source_load_id}


def refresh_mart_top_entities(
    session: Session, *, allowed_entities: Optional[set[str]] = None,
    project_id: str = "default",
) -> dict[str, Any]:
    """(Re)populate ``mart_overview_top_entities`` from fact_sales / fact_com.

    For each partner_type (customer/supplier): Σ value_col in RAW EUR (positive
    magnitude, exact cents — NO /1000, NO early kEUR rounding) grouped by
    (entity_prefix = LEFT(account_number_group,2), partner_id, month_end), filtered to
    the live level_3 account (Net sales / Cost of materials) via dim_gl_account.
    Storing full-precision EUR lets the read path sum across months and divide by
    1000 + round ONCE at the end, matching the live builder to the cent (Bug 2 fix;
    was ``amount_keur`` = Σ/1000 pre-rounded to 2dp kEUR per partner-month).  Stamped
    from the latest partner-fact load (falls back to the 'gl' load — the derived-fact
    source).  Idempotent (delete+insert over the refresh scope).  Lazy / operator-run
    ONLY.  Honors ``allowed_entities`` (fail-closed: ``set()`` → writes nothing).
    """
    if _deny_all(allowed_entities):
        return {"top_rows": 0, "skipped": "fail_closed_empty"}

    source_load_id = latest_partner_fact_load_id(session)
    # fact_sales / fact_com carry no entity_prefix column → filter the derived prefix.
    ent_frag = _prefix_fragment(allowed_entities, column="LEFT(f.account_number_group, 2)")

    all_rows: list[dict[str, Any]] = []
    per_type: dict[str, int] = {}
    for ptype, fact, dim, value_col, id_col, level_3 in _TOP_ENTITY_SPECS:
        name_expr = (
            "TRIM(COALESCE(d.name_line_1, '') || ' ' || COALESCE(d.name_line_2, ''))"
        )
        sql = f"""
            SELECT
                LEFT(f.account_number_group, 2)                 AS entity_prefix,
                f.{id_col}                                      AS partner_id,
                MAX(NULLIF({name_expr}, ''))                    AS partner_name,
                (date_trunc('month', f.posting_date)
                    + INTERVAL '1 month - 1 day')::date         AS month_end,
                -- RAW EUR (exact cents): NO /1000, NO early kEUR rounding (Bug 2 fix).
                COALESCE(SUM(f.{value_col}), 0)                  AS amount_eur
            FROM {fact} f
            JOIN dim_gl_account a
              ON a.account_number_group = f.account_number_group
             AND a.fiscal_year          = f.fiscal_year
            LEFT JOIN {dim} d ON d.{id_col} = f.{id_col}
            WHERE f.posting_date IS NOT NULL
              AND TRIM(a.level_3) = '{level_3}'
              {ent_frag}
            GROUP BY LEFT(f.account_number_group, 2), f.{id_col}, month_end
        """
        rows = [dict(r._mapping) for r in session.execute(text(sql)).fetchall()]
        per_type[ptype] = len(rows)
        for m in rows:
            pid = m.get("partner_id")
            all_rows.append({
                "entity_prefix": (m.get("entity_prefix") or "").strip(),
                "partner_type": ptype,
                "partner_id": None if pid is None else str(pid),
                "partner_name": m.get("partner_name"),
                "month_end": _as_date(m["month_end"]),
                # RAW EUR, exact cents (native 2dp) — no /1000, no early kEUR rounding.
                "amount_eur": round(float(m.get("amount_eur") or 0.0), 2),
            })

    del_frag = _prefix_fragment(allowed_entities, alias="t")
    session.execute(text(
        f"DELETE FROM mart_overview_top_entities t WHERE t.project_id = :pid {del_frag}"
    ), {"pid": project_id})

    if all_rows:
        session.execute(text(
            "INSERT INTO mart_overview_top_entities"
            " (project_id, entity_prefix, partner_type, partner_id,"
            "  partner_name, month_end, amount_eur, source_load_id)"
            " VALUES (:pid, :ep, :pt, :partner, :pn, :me, :amt, :load)"
        ), [
            {"pid": project_id, "ep": r["entity_prefix"], "pt": r["partner_type"],
             "partner": r["partner_id"], "pn": r["partner_name"],
             "me": r["month_end"], "amt": r["amount_eur"], "load": source_load_id}
            for r in all_rows
        ])

    return {
        "top_rows": len(all_rows),
        "customer_rows": per_type.get("customer", 0),
        "supplier_rows": per_type.get("supplier", 0),
        "source_load_id": source_load_id,
    }


def read_overview_period(
    session: Session, *, entity: Optional[str], year: int, month: int,
    allowed_entities: Optional[set[str]],
) -> dict[str, Any]:
    """Index-lookup read → builder-identical EBIT grain for hero/revenue/margin.

    ``entity`` is an ``entity_prefix`` (or None for all); the endpoint resolves the
    legal_entity_code → prefix upstream.  Returns a dict whose ``grain`` is drop-in
    for ``overview_metrics.build_ebit_rows`` — identical to
    ``build_ebit_table``'s internal grain, so mart and builder yield equal numbers.

    FAIL-CLOSED: ``allowed_entities == set()`` → empty grain.
    """
    empty = {"year": year, "month": month, "period_grain": "month",
             "entity": entity, "grain": [], "source": "mart"}
    if _deny_all(allowed_entities):
        return empty

    py = year - 1
    pm_y, _pm_m = pm(year, month)

    # effective allow-list = visibility ∩ requested entity
    eff = allowed_entities
    if entity:
        req = {str(entity).strip()[:2]}
        eff = req if allowed_entities is None else (allowed_entities & req)
        if not eff:
            return empty

    ent_frag = _prefix_fragment(eff, alias="m")
    rows = [dict(r._mapping) for r in session.execute(text(
        f"""
        SELECT entity_prefix, fiscal_year, fiscal_period, level_2, level_3, amount_sum
        FROM mart_overview_period m
        WHERE m.project_id = :pid
          AND m.level_0 = 'PL'
          AND m.fiscal_year IN ({year}, {py}, {pm_y})
          {ent_frag}
        """
    ), {"pid": "default"}).fetchall()]

    grain = ebit_grain_from_pl_rows(rows, year=year, month=month, allowed_entities=eff)
    return {"year": year, "month": month, "period_grain": "month",
            "entity": entity, "grain": grain, "source": "mart"}


# ===========================================================================
# Phase 2 read helpers — cash headline / WC snapshot / top-entities
# ===========================================================================
# These mirror the SHAPE + ARITHMETIC of the live builders they accelerate so a
# gated endpoint can serve them from the snapshot when fresh.  Every helper
# threads ``allowed_entities`` through :func:`_prefix_fragment` and short-circuits
# on :func:`_deny_all` WITHOUT touching the DB (fail-closed, same as
# :func:`read_overview_period`).
#
# EQUIVALENCE STATUS (proven on finssentials_v2):
#   * read_cash_headline  → byte-equivalent to overview_summary._cash_headline
#     (both are PURE cumulative Σ amount WHERE posting_date <= cutoff).  WIRED.
#   * read_wc_snapshot    → WIRED (Phase 4).  The Phase-3 refresh fix applied the
#     hybrid GoBD opening-balance dedup (earliest OB snapshot only — see
#     _WC_MOVEMENT_SQL) so the cumulative WC stock no longer re-adds each annual
#     Jan-1 opening; levels/NWC/Δfy now reconcile to the live WC statement within
#     tolerance (dso/dpo/dio/ccc <=0.1 days; nwc + level deltas <=10 EUR).  Served
#     from the mart when ``mart_wc_is_fresh``.
#   * read_top_entities   → WIRED (Phase 4).  The Phase-3 refresh stores
#     full-precision ``amount_eur`` (exact cents); the read path sums raw EUR then
#     /1000-rounds ONCE at the end, so cm/pm/py_cm/ytd/ytd_py match the live
#     build_top_entities to the cent (<=0.01 kEUR).  Served from the mart when
#     ``mart_top_is_fresh``.

_CASH_L3 = "Cash & cash equivalents"
_WC_INV_L3 = "Inventories"
_WC_REC_L3 = "Trade receivables"
_WC_PAY_L3 = "Trade payables"
_LTM_REV_L3 = "Net sales"
_LTM_COGS_L3 = "Cost of materials"


# ---------------------------------------------------------------------------
# Pure arithmetic locks (DB-free, unit-tested)
# ---------------------------------------------------------------------------

def cash_headline_from_levels(
    cm: float, pm_v: float, py_v: float,
) -> dict[str, Any]:
    """Cash headline from the 3 signed cash STOCK levels (RAW sign, NO ABS).

    FORMULA (mirrors overview_summary._cash_headline):
        level       = cm                       (signed; overdraft may be negative)
        delta_month = cm − pm
        delta_yoy   = cm − py

    WORKED EXAMPLE: cm=120, pm=150, py=90 → level=120, delta_month=−30, delta_yoy=30.
    EDGE (overdraft): cm=−40, pm=−10, py=50 → level=−40 (NOT floored), delta_month=−30.
    """
    return {
        "level": round(cm, 2),
        "delta_month": round(cm - pm_v, 2),
        "delta_yoy": round(cm - py_v, 2),
        "source": "mart",
    }


def wc_snapshot_from_balances(
    cm_by_l3: dict[str, float], pm_by_l3: dict[str, float],
    fy_by_l3: dict[str, float], fypy_by_l3: dict[str, float],
    *, rev_ltm: float, cogs_ltm: float,
) -> dict[str, Any]:
    """WC ``working_capital`` block from per-level_3 RAW stock balances + LTM.

    Reuses the signed-off pure KPI formula ``fin_compat_wc.compute_wc_kpis``:
        DIO = |inv|·365/cogs_ltm ; DSO = |rec|·365/rev_ltm ;
        DPO = |pay|·365/cogs_ltm ; CCC = DSO + DIO − DPO      (ABS the AGGREGATE).
        NWC = Σ RAW over ALL TWC+OWC level_3 balances at cm  (raw signs, NO ABS).
    Per deep-dive level_3 (inventories/receivables/payables):
        level       = cm balance (raw signed)
        delta_month = cm − pm
        delta_fy    = fy − fy_py           (mirrors fin_compat_bs._snap_deltas)

    WORKED EXAMPLE (cm): inv=+4,000,000 rec=+5,000,000 pay=−3,000,000 owc=+500,000;
      rev_ltm=20,000,000 cogs_ltm=12,000,000 →
      DSO=5,000,000·365/20,000,000=91.2 ; DIO=4,000,000·365/12,000,000=121.7 ;
      DPO=3,000,000·365/12,000,000=91.2 ; CCC=91.2+121.7−91.2=121.7 ;
      NWC=4,000,000+5,000,000−3,000,000+500,000=6,500,000.
    EDGE: cogs_ltm≈0 → DIO=DPO=0 ; rev_ltm≈0 → DSO=0 ; CCC=Σ defined components.
    """
    from app.services.fin_compat_wc import compute_wc_kpis  # local: avoid import cycle

    inv = cm_by_l3.get(_WC_INV_L3, 0.0)
    rec = cm_by_l3.get(_WC_REC_L3, 0.0)
    pay = cm_by_l3.get(_WC_PAY_L3, 0.0)
    k = compute_wc_kpis(abs(inv), abs(rec), abs(pay), rev_ltm, cogs_ltm)
    nwc = round(sum(cm_by_l3.values()), 2)
    levels: list[dict[str, Any]] = []
    for key, l3 in (("inventories", _WC_INV_L3),
                    ("trade_receivables", _WC_REC_L3),
                    ("trade_payables", _WC_PAY_L3)):
        cmv = cm_by_l3.get(l3, 0.0)
        levels.append({
            "key": key, "label": l3,
            "level": round(cmv, 2),
            "delta_month": round(cmv - pm_by_l3.get(l3, 0.0), 2),
            "delta_fy": round(fy_by_l3.get(l3, 0.0) - fypy_by_l3.get(l3, 0.0), 2),
        })
    return {
        "dso": k["DSO"], "dpo": k["DPO"], "dio": k["DIO"], "ccc": k["CCC"],
        "nwc": nwc, "levels": levels, "source": "mart",
    }


def aggregate_top_months(
    rows: Iterable[dict[str, Any]], *, year: int, month: int, id_field: str,
) -> list[dict[str, Any]]:
    """Bucket per-``month_end`` partner rows into the 5 period sums per partner.

    ``rows`` — one dict per (partner_id, month_end) with keys ``partner_id``,
    ``partner_name``, ``month_end`` (date), ``amount_eur`` (positive RAW EUR, exact
    cents — the ``mart_overview_top_entities.amount_eur`` column, Bug 2 fix).

    Windows (mirror overview_top_entities.build_top_entities, month grain):
        cm     = month_end == last_day(year, month)
        pm     = month_end == last_day(prior month)
        py_cm  = month_end == last_day(year-1, month)
        ytd    = last_day(year,1)   .. last_day(year, month)
        ytd_py = last_day(year-1,1) .. last_day(year-1, month)

    SCALE (Bug 2 fix): raw EUR is summed across the month buckets FIRST, then each
    window is divided by 1000 exactly ONCE here (kEUR) — matching how the live
    ``build_top_entities`` divides ``SUM(value_col)/1000`` per window.  The single
    2dp round is deferred to ``rank_top_entities`` (which rounds each window once),
    so mart and live agree to the cent (no per-month kEUR pre-rounding drift).

    Returns rows in the shape ``rank_top_entities`` consumes (``name``, ``id_field``,
    cm/pm/py_cm/ytd/ytd_py — unrounded kEUR floats).
    """
    pm_y, pm_m = pm(year, month)
    d_cm = last_day(year, month)
    d_pm = last_day(pm_y, pm_m)
    d_py = last_day(year - 1, month)
    ytd_lo, ytd_hi = date(year, 1, 1), d_cm
    ytd_py_lo, ytd_py_hi = date(year - 1, 1, 1), d_py

    agg: dict[Any, dict[str, Any]] = {}
    for r in rows:
        pid = r.get("partner_id")
        slot = agg.get(pid)
        if slot is None:
            slot = {"partner_id": pid, "partner_name": r.get("partner_name"),
                    "cm": 0.0, "pm": 0.0, "py_cm": 0.0, "ytd": 0.0, "ytd_py": 0.0}
            agg[pid] = slot
        elif slot.get("partner_name") is None and r.get("partner_name"):
            slot["partner_name"] = r.get("partner_name")
        d = _as_date(r["month_end"])
        amt = float(r.get("amount_eur") or 0.0)  # RAW EUR — /1000 deferred to the end
        if d == d_cm:
            slot["cm"] += amt
        if d == d_pm:
            slot["pm"] += amt
        if d == d_py:
            slot["py_cm"] += amt
        if ytd_lo <= d <= ytd_hi:
            slot["ytd"] += amt
        if ytd_py_lo <= d <= ytd_py_hi:
            slot["ytd_py"] += amt

    out: list[dict[str, Any]] = []
    for slot in agg.values():
        pid = slot["partner_id"]
        name = slot.get("partner_name") or (str(pid) if pid is not None else "(no partner)")
        # /1000 ONCE, after summing raw EUR across months (kEUR).  NO round here —
        # rank_top_entities applies the single 2dp round per window (== live).
        out.append({
            "name": name, id_field: pid,
            "cm": slot["cm"] / 1000.0, "pm": slot["pm"] / 1000.0,
            "py_cm": slot["py_cm"] / 1000.0, "ytd": slot["ytd"] / 1000.0,
            "ytd_py": slot["ytd_py"] / 1000.0,
        })
    return out


# ---------------------------------------------------------------------------
# DB reads
# ---------------------------------------------------------------------------

def read_cash_headline(
    session: Session, *, year: int, month: int,
    allowed_entities: Optional[set[str]],
) -> dict[str, Any]:
    """Signed cash headline from ``mart_overview_bs_balance`` (RAW sign, NO ABS).

    Reads the cash STOCK at three month-ends (current / prior month / prior-year
    same month) by taking, per entity_prefix, the balance at the LATEST cutoff_date
    <= the target date (balances are cumulative and only stored where a movement
    occurred, so the last row at/before the date is the stock).  Sums across the
    visible entities, then applies :func:`cash_headline_from_levels`.

    Byte-equivalent to ``overview_summary._cash_headline`` (proven on finssentials_v2
    within rounding).  FAIL-CLOSED: ``allowed_entities == set()`` → zeros, no DB.
    """
    if _deny_all(allowed_entities):
        return {"level": 0.0, "delta_month": 0.0, "delta_yoy": 0.0, "source": "mart"}

    pm_y, pm_m = pm(year, month)
    d_cur = last_day(year, month)
    d_pm = last_day(pm_y, pm_m)
    d_py = last_day(year - 1, month)
    ent_frag = _prefix_fragment(allowed_entities, alias="b")

    def _bal(d: date) -> float:
        row = session.execute(text(
            f"""
            SELECT COALESCE(SUM(x.balance_sum), 0) AS bal
            FROM (
                SELECT DISTINCT ON (b.entity_prefix) b.entity_prefix, b.balance_sum
                FROM mart_overview_bs_balance b
                WHERE b.project_id = :pid
                  AND b.level_3 = :l3
                  AND b.cutoff_date <= :d
                  {ent_frag}
                ORDER BY b.entity_prefix, b.cutoff_date DESC
            ) x
            """
        ), {"pid": "default", "l3": _CASH_L3, "d": d.isoformat()}).fetchone()
        return float(row[0]) if (row and row[0] is not None) else 0.0

    return cash_headline_from_levels(_bal(d_cur), _bal(d_pm), _bal(d_py))


def _wc_balances_at(
    session: Session, d: date, ent_frag: str,
) -> dict[str, float]:
    """{level_3: Σ RAW balance_sum} at the LATEST cutoff <= ``d`` per (entity, l6, level_3)."""
    rows = session.execute(text(
        f"""
        WITH latest AS (
            SELECT DISTINCT ON (w.entity_prefix, w.l6_na_mapping, w.level_3)
                   w.level_3 AS level_3, w.balance_sum AS balance_sum
            FROM mart_overview_wc_balance w
            WHERE w.project_id = :pid
              AND w.cutoff_date <= :d
              {ent_frag}
            ORDER BY w.entity_prefix, w.l6_na_mapping, w.level_3, w.cutoff_date DESC
        )
        SELECT level_3, COALESCE(SUM(balance_sum), 0) AS bal
        FROM latest GROUP BY level_3
        """
    ), {"pid": "default", "d": d.isoformat()}).fetchall()
    return {r[0]: float(r[1]) for r in rows}


def _wc_ltm_from_mart(
    session: Session, *, year: int, month: int, ent_frag: str,
) -> tuple[float, float]:
    """(rev_ltm, cogs_ltm) over the trailing-12 FISCAL periods from mart_overview_period.

        rev_ltm  = Σ (amount_sum * -1)  over level_3 = 'Net sales'
        cogs_ltm = |Σ amount_sum|       over level_3 = 'Cost of materials'

    Confirmed within <0.05 EUR of the live posting-date LTM on finssentials_v2 (see
    Phase 2 sign-landmine (a)); the tiny drift is rounding, immaterial to the days.
    """
    from app.services.fin_compat_sql import _last_12_periods
    periods = _last_12_periods(year, month)
    conds = " OR ".join(
        f"(m.fiscal_year = {y} AND m.fiscal_period = {mo})" for y, mo in periods
    )
    row = session.execute(text(
        f"""
        SELECT
            COALESCE(SUM(CASE WHEN m.level_3 = :rev THEN m.amount_sum * -1 ELSE 0 END), 0) AS rev,
            ABS(COALESCE(SUM(CASE WHEN m.level_3 = :cogs THEN m.amount_sum ELSE 0 END), 0)) AS cogs
        FROM mart_overview_period m
        WHERE m.project_id = :pid AND m.level_0 = 'PL' AND ({conds})
          {ent_frag}
        """
    ), {"pid": "default", "rev": _LTM_REV_L3, "cogs": _LTM_COGS_L3}).fetchone()
    if row is None:
        return 0.0, 0.0
    return float(row[0] or 0.0), float(row[1] or 0.0)


def read_wc_snapshot(
    session: Session, *, entity: Optional[str], year: int, month: int,
    allowed_entities: Optional[set[str]],
) -> dict[str, Any]:
    """WC ``working_capital`` block (dso/dpo/dio/ccc/nwc + levels[]) from the mart.

    Balances from ``mart_overview_wc_balance`` (TWC/OWC, RAW sign) at cm / pm / fy /
    fy_py cutoffs; LTM denominators from ``mart_overview_period``.  See
    :func:`wc_snapshot_from_balances` for the formula.

    WIRED (Phase 4): the Phase-3 refresh applies the earliest-OB-only dedup so the
    cumulative WC stock reconciles to the live WC statement within tolerance
    (dso/dpo/dio/ccc <=0.1 days; nwc + level deltas <=10 EUR).  Served from the mart
    when ``mart_wc_is_fresh``.

    FAIL-CLOSED: ``allowed_entities == set()`` / entity outside visibility → empty.
    """
    empty = {"dso": 0.0, "dpo": 0.0, "dio": 0.0, "ccc": 0.0, "nwc": 0.0,
             "levels": [], "source": "mart"}
    if _deny_all(allowed_entities):
        return empty

    eff = allowed_entities
    if entity:
        req = {str(entity).strip()[:2]}
        eff = req if allowed_entities is None else (allowed_entities & req)
        if not eff:
            return empty

    ent_frag = _prefix_fragment(eff, alias="w")
    pm_y, pm_m = pm(year, month)
    cm_by_l3 = _wc_balances_at(session, last_day(year, month), ent_frag)
    pm_by_l3 = _wc_balances_at(session, last_day(pm_y, pm_m), ent_frag)
    fy_by_l3 = _wc_balances_at(session, last_day(year - 1, 12), ent_frag)
    fypy_by_l3 = _wc_balances_at(session, last_day(year - 2, 12), ent_frag)

    ltm_frag = _prefix_fragment(eff, alias="m")
    rev_ltm, cogs_ltm = _wc_ltm_from_mart(
        session, year=year, month=month, ent_frag=ltm_frag)

    return wc_snapshot_from_balances(
        cm_by_l3, pm_by_l3, fy_by_l3, fypy_by_l3,
        rev_ltm=rev_ltm, cogs_ltm=cogs_ltm)


def read_top_entities(
    session: Session, *, year: int, month: int, type: str = "customer",
    rank_by: str = "cm", limit: int = 5000,
    allowed_entities: Optional[set[str]],
) -> dict[str, Any]:
    """Top customers/suppliers from ``mart_overview_top_entities`` (build_top_entities shape).

    Sums the per-month RAW-EUR magnitudes into cm/pm/py_cm/ytd/ytd_py per partner
    (:func:`aggregate_top_months`, which /1000's ONCE at the end), then REUSES the
    pure ranker ``overview_top_entities.rank_top_entities`` (no re-implemented
    ranking; it applies the single 2dp round per window).  ``plan_cm``/``coverage``
    stay LIVE via ``_load_partner_plan_cm``.

    WIRED (Phase 4): with the ``amount_eur`` (full-precision) column + deferred
    /1000-round-once (Bug 2 fix), cm/pm/py_cm/ytd/ytd_py match the live
    ``build_top_entities`` to the cent (<=0.01 kEUR), so the summary serves this
    from the mart when ``mart_top_is_fresh``.

    FAIL-CLOSED: ``allowed_entities == set()`` → empty rows, no fact scan.
    """
    from app.services.overview_top_entities import (
        _load_partner_plan_cm, _top_entities_col_labels, rank_top_entities,
    )

    is_customer = type != "supplier"
    if rank_by not in ("cm", "ytd"):
        rank_by = "cm"
    id_field = "customer_id" if is_customer else "supplier_id"

    empty = {"rows": [], "col_labels": _top_entities_col_labels(year, month),
             "period_grain": "month", "rank_by": rank_by, "plan_mix": "py_proxy"}
    if _deny_all(allowed_entities):
        return empty

    ent_frag = _prefix_fragment(allowed_entities, alias="t")
    rows = [dict(r._mapping) for r in session.execute(text(
        f"""
        SELECT t.partner_id AS partner_id,
               MAX(t.partner_name) AS partner_name,
               t.month_end AS month_end,
               COALESCE(SUM(t.amount_eur), 0) AS amount_eur
        FROM mart_overview_top_entities t
        WHERE t.project_id = :pid AND t.partner_type = :pt
          {ent_frag}
        GROUP BY t.partner_id, t.month_end
        """
    ), {"pid": "default", "pt": ("customer" if is_customer else "supplier")}).fetchall()]

    agg_rows = aggregate_top_months(rows, year=year, month=month, id_field=id_field)

    # plan_cm / coverage stay LIVE (per Phase 2 spec) — consolidated scope.
    plan_by_id, plan_mix = _load_partner_plan_cm(
        session, year=year, month=month, is_customer=is_customer,
        ent_prefix=None, allowed_entities=allowed_entities,
    )

    ranked = rank_top_entities(
        agg_rows, id_field=id_field, rank_by=rank_by, limit=limit,
        plan_by_id=plan_by_id)
    return {
        "rows": ranked, "col_labels": _top_entities_col_labels(year, month),
        "period_grain": "month", "rank_by": rank_by, "plan_mix": plan_mix,
    }


# ---------------------------------------------------------------------------
# Freshness — per-mart staleness anchors (mirror mart_is_fresh)
# ---------------------------------------------------------------------------

def mart_wc_is_fresh(session: Session, *, project_id: str = "default") -> bool:
    """True iff ``mart_overview_wc_balance`` was built from the latest GL load."""
    latest = latest_gl_load_id(session)
    if latest is None:
        return False
    row = session.execute(text(
        "SELECT MAX(source_load_id) FROM mart_overview_wc_balance "
        "WHERE project_id = :pid"
    ), {"pid": project_id}).fetchone()
    if not row or row[0] is None:
        return False
    return int(row[0]) >= int(latest)


def mart_top_is_fresh(session: Session, *, project_id: str = "default") -> bool:
    """True iff ``mart_overview_top_entities`` was built from the latest partner load.

    Anchor is :func:`latest_partner_fact_load_id` (falls back to the 'gl' load here,
    since fact_sales/fact_com are derived from the GL load in this stack)."""
    latest = latest_partner_fact_load_id(session)
    if latest is None:
        return False
    row = session.execute(text(
        "SELECT MAX(source_load_id) FROM mart_overview_top_entities "
        "WHERE project_id = :pid"
    ), {"pid": project_id}).fetchone()
    if not row or row[0] is None:
        return False
    return int(row[0]) >= int(latest)
