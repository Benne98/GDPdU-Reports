"""Hierarchical GL anomaly trees (anomaly rework, Phase 1).

Produces, in ONE DB pull then PURE aggregation, a drillable analysis tree for the
two per-series analyses — **outliers** and **seasonality** — over the GL hierarchy:

    level_0 (PL / BS)  →  level_3 position  →  level_4 child  →  account

Every node carries its OWN analysis payload, computed by reusing the existing math
verbatim (no new statistic is invented here):

  * outliers     → :func:`gl_outliers._series_outlier_payload`
                   (mean / sample-std / per-point residual+z)
  * seasonality  → :func:`gl_seasonality._series_seasonality_payload`
                   (additive trend+seasonal+residual decomposition, with the same
                    insufficient-history fallback as the per-account builder)

Both helpers accept ANY ``MonthPoint``-like series, so the aggregated L3 / L4 node
series (summed by :mod:`gl_hierarchy`) and the leaf account series go through the
IDENTICAL code path.  Aggregation reconciles (Σ children == parent per period — see
:mod:`gl_hierarchy`), so the tree's payloads reconcile too: an L4 payload's series
equals the sum of its accounts' series, and an L3 payload's series equals the sum of
its L4 children.

================================================================================
NODE SEVERITY (CLAUDE.md rule #1 — banding the per-node z)
================================================================================
A node's anomaly strength is ``max_abs_z = max |z|`` over its analysis points
(outliers: per-point z; seasonality: residual z, NaN/None ignored).  It is banded::

    severity = "high"    if max_abs_z >= Z_HIGH    (3.0)
               "medium"  if max_abs_z >= Z_MEDIUM  (2.0)
               "low"     if max_abs_z >= Z_LOW      (1.0)
               "none"    otherwise (incl. no finite z at all)

This mirrors the σ-slider semantics the per-account analyses already use (a point
flags at ``|z| >= σ``): |z|>=3 is a ~3-sigma event (high), >=2 notable (medium),
>=1 mild (low).  Worked example — node z-list ``[0.4, -2.3, 1.1]`` → max_abs_z 2.3
→ "medium" (>=2, <3).  Edge: exactly 3.0 → high; exactly 2.0 → medium; exactly 1.0
→ low; a flat series (all z == 0 / None) → max_abs_z 0.0 → "none".

Children are sorted by severity (high→none) then by ``max_abs_z`` descending then by
key, so the most anomalous positions surface first at every level.  The account
count is bounded (``top_n_per_level``) so the precomputed payload stays finite.

================================================================================
BOOKING DRILL (lazy, live — NOT part of the tree)
================================================================================
:func:`list_account_bookings` is the leaf-of-the-leaf: given an
``account_number_group`` it returns that account's largest individual bookings over
ALL history (``ORDER BY ABS(amount) DESC LIMIT``), each carrying a per-booking z vs
the account's OWN booking-amount distribution so an unusually large single booking
can be flagged.  ``booking_line_id`` lets the existing ``journal-entry-by-booking``
endpoint resolve the full entry.  This is live + bounded — never cached, never part
of the precomputed tree.

PURE/DB SPLIT: the two ``build_*_tree`` functions do exactly ONE DB read
(:func:`gl_analysis_common.build_account_monthly_series`) plus a materiality anchor
read; everything after is pure.  :func:`list_account_bookings` is a single bounded
read.  Nothing writes.
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.fin_compat_sql import entities_sql_fragment
from app.services.gl_forensic import derive_counter_accounts
from app.services.gl_analysis_common import (
    _NOT_SYNTHETIC,
    AccountSeries,
    bound_material_accounts,
    build_account_monthly_series,
    latest_anchor,
)
from app.services.gl_hierarchy import (
    NO_L4_BUCKET,
    AggSeries,
    aggregate_l3,
    aggregate_l4,
)
from app.services.anomaly_score import band as score_band
from app.services.anomaly_score import signal_score_0to100
from app.services.gl_outliers import (
    _series_outlier_payload,
    compute_histogram,
    compute_linear_regression,
    distribution_stats,
)
from app.services.gl_seasonality import (
    MIN_YEARS,
    _series_seasonality_payload,
    booking_density_ok,
)

# v3 (seasonality density gate): the SEASONALITY tree now EXCLUDES accounts that are
# too sparsely / punctually booked to carry a real seasonal pattern (see
# gl_seasonality.booking_density_ok); excluded accounts remain in the OUTLIER tree.
# v2 added per-node + per-point signal_score/band and per-node
# regression/histogram/distribution stats to the payload.  Bumping this string
# invalidates the anomaly_snapshot_cache (keyed on algorithm_version) AND, because
# anomaly_compute.OVERVIEW_ALGORITHM_VERSION composes this tag, the overview cache.
ALGORITHM_VERSION = "gl_anomaly_tree_v3"

#: Minimum number of series points before regression/histogram/distribution stats
#: are meaningful; below this they are OMITTED (set to None) — a 2- or 3-point chart
#: would mislead.  (Plan: "Histogramm/Regression nur sinnvoll ab genug Punkten n≥6".)
MIN_STATS_POINTS = 6

#: Severity banding thresholds on ``max_abs_z`` (mirrors the σ-slider semantics).
Z_HIGH = 3.0
Z_MEDIUM = 2.0
Z_LOW = 1.0

#: Default cap on accounts (leaf nodes) kept after material bounding, so the
#: precomputed tree payload stays generous-but-finite.
TOP_N_PER_LEVEL_DEFAULT = 200

#: Default cap on bookings returned by the lazy drill.
BOOKINGS_LIMIT_DEFAULT = 200

#: Severity sort rank (high first).
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2, "none": 3}

_EUR_PER_KEUR = 1000.0


# ---------------------------------------------------------------------------
# Pure severity helpers
# ---------------------------------------------------------------------------
def severity_for_z(max_abs_z: float) -> str:
    """Band ``max_abs_z`` → ``high`` / ``medium`` / ``low`` / ``none`` (pure).

    ``|z| >= 3`` high, ``>= 2`` medium, ``>= 1`` low, else none.  Boundaries are
    inclusive on the higher band (exactly 3.0 → high).  Non-finite / negative input
    is treated as 0.0 → ``none``.
    """
    z = abs(float(max_abs_z)) if np.isfinite(max_abs_z) else 0.0
    if z >= Z_HIGH:
        return "high"
    if z >= Z_MEDIUM:
        return "medium"
    if z >= Z_LOW:
        return "low"
    return "none"


def _max_abs_z_from_points(points: list[dict[str, Any]]) -> float:
    """Largest finite ``|z|`` across analysis points; 0.0 when none are finite."""
    best = 0.0
    for p in points:
        z = p.get("z")
        if z is None:
            continue
        try:
            fz = abs(float(z))
        except (TypeError, ValueError):
            continue
        if np.isfinite(fz) and fz > best:
            best = fz
    return best


def _node_magnitude(points: list[dict[str, Any]]) -> float:
    """Largest absolute presented value across the node series (ranking tie-break)."""
    best = 0.0
    for p in points:
        v = p.get("value_keur")
        if v is None:
            v = p.get("actual_keur")
        if v is None:
            continue
        try:
            fv = abs(float(v))
        except (TypeError, ValueError):
            continue
        if fv > best:
            best = fv
    return best


def _statement_for_level0(level_0: str) -> str:
    return "pl" if level_0 == "PL" else ("bs" if level_0 == "BS" else level_0)


def _entity_split_payload(
    entity_split: dict[str, list], analysis: str, *, min_years: int,
) -> dict[str, list[dict[str, Any]]]:
    """Per-entity analysis series, so charts can break a node down by entity_prefix."""
    out: dict[str, list[dict[str, Any]]] = {}
    for ep, series in entity_split.items():
        if analysis == "outliers":
            points = _series_outlier_payload(series)["series"]
        else:
            points = _series_seasonality_payload(
                series, min_years=min_years,
            )["series"]
        # Per-point signal_score (from each point's z) so the client can re-threshold
        # an entity breakdown by score, consistently with the main node series.
        for p in points:
            z = p.get("z")
            p["signal_score"] = signal_score_0to100(z) if z is not None else 0
        out[ep] = points
    return out


# ---------------------------------------------------------------------------
# Node builders (pure)
# ---------------------------------------------------------------------------
def _analyse(series: list, analysis: str, *, min_years: int) -> dict[str, Any]:
    if analysis == "outliers":
        return _series_outlier_payload(series)
    return _series_seasonality_payload(series, min_years=min_years)


def _point_value(p: dict[str, Any]) -> Optional[float]:
    """Presented value for a payload point (outliers: value_keur; seasonality: actual_keur)."""
    v = p.get("value_keur")
    if v is None:
        v = p.get("actual_keur")
    return v


def _enrich_payload(payload: dict[str, Any], max_abs_z: float) -> None:
    """Add signal_score/band + regression/histogram/distribution to a node payload (in place).

    PURE presentation enrichment — no monetary formula.  Adds:
      * per-point ``signal_score`` (from each point's own ``z``) so the client can
        re-threshold by score instead of z;
      * ``payload.regression`` / ``payload.histogram`` / ``payload.distribution`` over
        the node's value series — only when n >= MIN_STATS_POINTS, else ``None``.
    The node-level ``signal_score``/``band`` (from ``max_abs_z``) are attached on the
    node dict by the caller (they live alongside ``severity``), not inside payload.
    """
    points = payload.get("series", [])
    values: list[float] = []
    for p in points:
        z = p.get("z")
        p["signal_score"] = signal_score_0to100(z) if z is not None else 0
        v = _point_value(p)
        if v is not None:
            try:
                values.append(float(v))
            except (TypeError, ValueError):
                pass

    if len(values) >= MIN_STATS_POINTS:
        payload["regression"] = compute_linear_regression(values)
        payload["histogram"] = compute_histogram(values)
        payload["distribution"] = distribution_stats(values)
    else:
        payload["regression"] = None
        payload["histogram"] = None
        payload["distribution"] = None


def _account_node(
    acc: AccountSeries, analysis: str, *, min_years: int,
) -> dict[str, Any]:
    payload = _analyse(acc.series, analysis, min_years=min_years)
    points = payload["series"]
    max_abs_z = _max_abs_z_from_points(points)
    _enrich_payload(payload, max_abs_z)
    node_score = signal_score_0to100(max_abs_z)
    l4 = (acc.level_4 or "").strip() or NO_L4_BUCKET
    return {
        "level": "account",
        "key": acc.account_number_group,
        "label": acc.account_name or acc.account_number_group,
        "level_0": acc.level_0,
        "level_2": acc.level_2,
        "level_3": acc.level_3,
        "level_4": l4,
        "statement": _statement_for_level0(acc.level_0),
        "gl_account_id": acc.gl_account_id,
        "account_number_group": acc.account_number_group,
        "payload": payload,
        "max_abs_z": round(max_abs_z, 4),
        "signal_score": node_score,
        "band": score_band(node_score),
        "severity": severity_for_z(max_abs_z),
        "entity_split": {acc.entity_prefix: points} if acc.entity_prefix else {},
        "members": [acc.account_number_group],
        "children": [],
    }


def _agg_node(
    agg: AggSeries,
    level: str,
    analysis: str,
    *,
    min_years: int,
    children: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = _analyse(agg.series, analysis, min_years=min_years)
    points = payload["series"]
    max_abs_z = _max_abs_z_from_points(points)
    _enrich_payload(payload, max_abs_z)
    node_score = signal_score_0to100(max_abs_z)
    return {
        "level": level,
        "key": agg.key,
        "label": agg.key,
        "level_0": agg.level_0,
        "level_2": agg.level_2,
        "level_3": agg.level_3,
        "level_4": agg.level_4,
        "statement": _statement_for_level0(agg.level_0),
        "payload": payload,
        "max_abs_z": round(max_abs_z, 4),
        "signal_score": node_score,
        "band": score_band(node_score),
        "severity": severity_for_z(max_abs_z),
        "entity_split": _entity_split_payload(
            agg.entity_split, analysis, min_years=min_years,
        ),
        "members": list(agg.members),
        "children": children,
    }


def _sort_children(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort by severity (high→none), then ``max_abs_z`` desc, then key asc."""
    return sorted(
        nodes,
        key=lambda n: (
            _SEVERITY_RANK.get(n["severity"], 9),
            -float(n["max_abs_z"]),
            n["key"],
        ),
    )


# ---------------------------------------------------------------------------
# Tree assembly (pure, given the bounded account set)
# ---------------------------------------------------------------------------
def _build_tree(
    accounts: list[AccountSeries], analysis: str, *, min_years: int,
) -> list[dict[str, Any]]:
    """Assemble the L3→L4→account tree from a bounded account set (pure).

    L3 nodes are emitted PL group first then BS group (statement order), each sorted
    within its statement by severity; L4 children and account leaves likewise.
    """
    by_account: dict[str, AccountSeries] = {}
    for acc in accounts:
        by_account.setdefault(acc.account_number_group, acc)

    l3_nodes: list[dict[str, Any]] = []
    for l3_agg in aggregate_l3(accounts):
        # L4 children of this L3 position.
        l4_nodes: list[dict[str, Any]] = []
        for l4_agg in aggregate_l4(accounts, l3_agg.level_3):
            # Account leaves of this L4 child (members → AccountSeries).
            acc_nodes = [
                _account_node(by_account[ang], analysis, min_years=min_years)
                for ang in l4_agg.members
                if ang in by_account
            ]
            acc_nodes = _sort_children(acc_nodes)
            l4_nodes.append(
                _agg_node(
                    l4_agg, "level_4", analysis,
                    min_years=min_years, children=acc_nodes,
                )
            )
        l4_nodes = _sort_children(l4_nodes)
        l3_nodes.append(
            _agg_node(
                l3_agg, "level_3", analysis,
                min_years=min_years, children=l4_nodes,
            )
        )

    # PL group first, then BS; within each, by severity (then max_abs_z, key).
    pl = _sort_children([n for n in l3_nodes if n["level_0"] == "PL"])
    bs = _sort_children([n for n in l3_nodes if n["level_0"] == "BS"])
    other = _sort_children(
        [n for n in l3_nodes if n["level_0"] not in ("PL", "BS")]
    )
    return pl + bs + other


def _bounded_accounts(
    session: Session,
    *,
    entity_prefixes: Optional[list[str]],
    top_n_per_level: int,
) -> list[AccountSeries]:
    """ONE DB pull (PL+BS, all history) → optional entity filter → material bound.

    Materiality is anchored at :func:`latest_anchor` (the most recent real month);
    when no entity filter is requested the whole consolidated ledger is pulled.
    """
    accounts = build_account_monthly_series(session, level_0=None)

    allowed = _normalise_prefixes(entity_prefixes)
    if allowed is not None:
        accounts = [a for a in accounts if a.entity_prefix in allowed]

    anchor = latest_anchor(session)
    if anchor is not None and accounts:
        ay, am = anchor
        accounts = bound_material_accounts(
            accounts, current_year=ay, current_period=am, top_n=top_n_per_level,
        )
    else:
        accounts = accounts[:top_n_per_level]
    return accounts


def _normalise_prefixes(
    entity_prefixes: Optional[list[str]],
) -> Optional[set[str]]:
    """Return a clean set of 2-char prefixes, or ``None`` for 'all entities'."""
    if not entity_prefixes:
        return None
    clean = {str(p).strip()[:2] for p in entity_prefixes if str(p).strip()}
    return clean or None


# ---------------------------------------------------------------------------
# Public tree builders (ONE DB pull each, then pure)
# ---------------------------------------------------------------------------
def build_outlier_tree(
    session: Session,
    *,
    entity_prefixes: Optional[list[str]] = None,
    top_n_per_level: int = TOP_N_PER_LEVEL_DEFAULT,
) -> dict[str, Any]:
    """Hierarchical OUTLIER tree (L3→L4→account), each node z-score analysed.

    ONE DB pull (PL+BS, all history), optionally filtered to ``entity_prefixes``
    (a restricted user's allowed prefixes; ``None`` = all entities), material-bound
    at :func:`latest_anchor`, then aggregated + analysed PURELY.  PURE READ — writes
    nothing.
    """
    accounts = _bounded_accounts(
        session, entity_prefixes=entity_prefixes, top_n_per_level=top_n_per_level,
    )
    tree = _build_tree(accounts, "outliers", min_years=MIN_YEARS)
    return {
        "analysis": "outliers",
        "tree": tree,
        "meta": {
            "top_n_per_level": top_n_per_level,
            "entity_prefixes": sorted(_normalise_prefixes(entity_prefixes) or []),
            "algorithm_version": ALGORITHM_VERSION,
        },
    }


def build_seasonality_tree(
    session: Session,
    *,
    entity_prefixes: Optional[list[str]] = None,
    top_n_per_level: int = TOP_N_PER_LEVEL_DEFAULT,
    min_years: int = MIN_YEARS,
) -> dict[str, Any]:
    """Hierarchical SEASONALITY tree (L3→L4→account), each node decomposed.

    Same shape + DB discipline as :func:`build_outlier_tree`; each node carries the
    additive trend/seasonal/residual decomposition (with the per-account
    insufficient-history fallback) instead of the z-score series.  PURE READ.

    BOOKING-DENSITY GATE (anomaly rework, Phase 1): accounts that are too sparsely /
    punctually booked to carry a real seasonal pattern (see
    :func:`gl_seasonality.booking_density_ok`) are EXCLUDED at the account level
    BEFORE aggregation.  Because the L3/L4 aggregates are summed from this filtered
    set, an L4/L3 node is dropped from the seasonality tree when NONE of its accounts
    qualify, while a position with at least one dense account stays and shows only its
    dense part — the tree (and Σ-children reconciliation) stays consistent.  The
    excluded accounts are NOT lost: they remain in :func:`build_outlier_tree`, which
    is built separately from the SAME material-bounded account set.
    """
    accounts = _bounded_accounts(
        session, entity_prefixes=entity_prefixes, top_n_per_level=top_n_per_level,
    )
    dense_accounts = [
        a for a in accounts if booking_density_ok(a.series, min_years=min_years)
    ]
    tree = _build_tree(dense_accounts, "seasonality", min_years=min_years)
    return {
        "analysis": "seasonality",
        "tree": tree,
        "meta": {
            "top_n_per_level": top_n_per_level,
            "min_years": min_years,
            "accounts_in": len(accounts),
            "accounts_dense": len(dense_accounts),
            "entity_prefixes": sorted(_normalise_prefixes(entity_prefixes) or []),
            "algorithm_version": ALGORITHM_VERSION,
        },
    }


# ---------------------------------------------------------------------------
# Pure booking-z helper
# ---------------------------------------------------------------------------
def compute_booking_zscores(amounts: list[float]) -> list[float]:
    """Per-booking z vs the account's booking-amount distribution (pure).

    ``z_i = (amount_i − mean) / std`` with SAMPLE std (``ddof=1``); 0.0 when
    ``n < 2`` or the spread is 0 (no divide-by-zero).  Flags an unusually large
    SINGLE booking within the account's own bookings — distinct from the monthly
    series outlier.  Mirrors :func:`gl_outliers.compute_point_zscores` semantics.
    """
    n = len(amounts)
    if n == 0:
        return []
    arr = np.asarray(amounts, dtype=float)
    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1)) if n >= 2 else 0.0
    if not np.isfinite(std) or std <= 0:
        return [0.0] * n
    return [round((float(a) - mean) / std, 4) for a in amounts]


# ---------------------------------------------------------------------------
# Lazy booking drill (live, bounded — never cached, never in the tree)
# ---------------------------------------------------------------------------
def list_account_bookings(
    session: Session,
    account_number_group: str,
    *,
    entity_prefixes: Optional[list[str]] = None,
    limit: int = BOOKINGS_LIMIT_DEFAULT,
) -> dict[str, Any]:
    """The lazy drill under one account: its largest bookings over ALL history.

    Reuses the :func:`gl_forensic.fetch_current_period_bookings` SQL skeleton but
    over ALL history, scoped to ``account_number_group`` (and the caller's allowed
    ``entity_prefixes``, if any), synthetic-excluded, ``ORDER BY ABS(amount) DESC
    LIMIT :limit``.  Each row carries a per-booking ``z`` vs the account's
    booking-amount distribution so an unusually large single booking is flagged
    (``z`` is computed over the RETURNED top-``limit`` amounts — the largest
    bookings, which is exactly the set being eyeballed).

    ``booking_line_id`` resolves the full entry via the existing
    ``journal-entry-by-booking`` endpoint.  Single bounded read; writes nothing.

    Each row is also enriched with its TOP-1 ``counter_account_id`` /
    ``counter_account_name`` (for the FE scatter plot) via one batched
    :func:`gl_forensic.derive_counter_accounts` query over the returned booking ids
    (NOT the bulk co-occurrence learner); both are ``None`` when the booking has no
    opposite-side sibling.  All existing fields (``booking_line_id``,
    ``journal_entry_number``, ``posting_date``, ``amount_keur``, ``line_note``,
    ``entity_prefix``, ``z``, ``large_booking``) are preserved.
    """
    ang = str(account_number_group or "").strip()
    if not ang:
        return {"account_number_group": "", "bookings": [], "stats": {"n": 0}}

    params: dict[str, Any] = {"ang": ang, "lim": int(limit)}
    ent_frag = ""
    allowed = _normalise_prefixes(entity_prefixes)
    if allowed is not None:
        # IN-list scoping (a restricted user may own several prefixes); injection-safe
        # 2-char prefixes via the shared ``entities_sql_fragment``.
        ent_frag = entities_sql_fragment(sorted(allowed), table_alias="l")

    sql = f"""
        SELECT
            l.booking_line_id                AS booking_line_id,
            l.journal_entry_group_number     AS journal_entry_group_number,
            l.entity_prefix                  AS entity_prefix,
            l.fiscal_year                    AS fiscal_year,
            e.fiscal_period                  AS fiscal_period,
            l.amount                         AS amount,
            l.line_note                      AS line_note,
            e.posting_date                   AS posting_date
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        WHERE l.account_number_group = :ang
          AND e.fiscal_period BETWEEN 1 AND 12
          {_NOT_SYNTHETIC}
          AND l.amount <> 0
          {ent_frag}
        ORDER BY ABS(l.amount) DESC, l.booking_line_id
        LIMIT :lim
    """
    rows = session.execute(text(sql), params).fetchall()

    raw: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r._mapping) if hasattr(r, "_mapping") else dict(r)
        jegn = (d.get("journal_entry_group_number") or "").strip()
        raw.append({
            "booking_line_id": int(d["booking_line_id"]),
            "journal_entry_group_number": jegn,
            # journal_entry_number = entry id WITHOUT the 2-char entity prefix
            # (matches gl_forensic.classify_novelty).
            "journal_entry_number": jegn[2:] if len(jegn) > 2 else jegn,
            "entity_prefix": (d.get("entity_prefix") or "").strip(),
            "fiscal_year": int(d["fiscal_year"]) if d.get("fiscal_year") is not None else None,
            "fiscal_period": int(d["fiscal_period"]) if d.get("fiscal_period") is not None else None,
            "amount_keur": round(float(d.get("amount") or 0.0) / _EUR_PER_KEUR, 3),
            "line_note": (d.get("line_note") or "").strip(),
            "posting_date": (
                d["posting_date"].isoformat() if d.get("posting_date") else None
            ),
        })

    amounts = [b["amount_keur"] for b in raw]
    zs = compute_booking_zscores(amounts)
    bookings: list[dict[str, Any]] = []
    for b, z in zip(raw, zs):
        b["z"] = z
        b["large_booking"] = abs(z) >= Z_LOW
        bookings.append(b)

    # Enrich each row with its TOP-1 counter-account (for the FE scatter plot): ONE
    # batched query over the returned booking_line_ids (NOT the bulk co-occurrence
    # learner).  Rows with no opposite-side sibling get null counter fields.
    _attach_counter_accounts(session, bookings)

    return {
        "account_number_group": ang,
        "bookings": bookings,
        "stats": {"n": len(bookings)},
        "meta": {"limit": int(limit), "algorithm_version": ALGORITHM_VERSION},
    }


def _attach_counter_accounts(
    session: Session, bookings: list[dict[str, Any]],
) -> None:
    """Add ``counter_account_id`` + ``counter_account_name`` to each booking row (in place).

    Derives each booking's TOP-1 counter account via
    :func:`gl_forensic.derive_counter_accounts` — ONE batched query over the returned
    ``booking_line_id`` set (the same single-query path the forensic novelty scan uses,
    NOT the bulk co-occurrence learner).  A booking with no opposite-side sibling (e.g.
    a single-leg row) gets ``None`` for both fields so the FE scatter plot can skip it.

    ``counter_account_id`` is the counter line's ``gl_account_id`` (``counter_gl_account_id``
    from the derivation); empty strings from the derivation are normalised to ``None``.
    """
    if not bookings:
        return
    line_ids = [b["booking_line_id"] for b in bookings]
    counters = derive_counter_accounts(session, line_ids)
    for b in bookings:
        c = counters.get(b["booking_line_id"])
        if c:
            cid = (c.get("counter_gl_account_id") or "").strip()
            cname = (c.get("counter_account_name") or "").strip()
            b["counter_account_id"] = cid or None
            b["counter_account_name"] = cname or None
        else:
            b["counter_account_id"] = None
            b["counter_account_name"] = None
