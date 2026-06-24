"""Pure hierarchical aggregation over already-presented GL account series.

Hierarchical anomaly rework, Phase 0.  Given the per-account, all-history monthly
series produced by :func:`gl_analysis_common.build_account_monthly_series`
(``list[AccountSeries]``), this module rolls them up into the analysis hierarchy:

    level_0  →  level_2  →  level_3  →  level_4  →  account  →  booking

This file is **pure and DB-free** — it never touches a session, never re-reads the
ledger.  It only *sums* the ``value_keur`` that the SQL layer already presented
(PL inverted to revenue-positive, BS raw — see ``gl_analysis_common`` docstring),
so no new sign rule or monetary formula is introduced here.

================================================================================
WHY SUM THE PRESENTED VALUES (and not re-aggregate the ledger)
================================================================================
``AccountSeries.value_keur`` is the *presented* monthly value at the consolidated
grain ``(entity_prefix, account_number_group)``.  Summation is therefore a plain
addition of comparable, like-signed numbers per ``period_key``:

    parent[period] = Σ child[period]        (exact, to floating-point epsilon)

Because every account in a node shares the same presentation, the roll-up
reconciles: Σ children == parent per period (asserted in tests to 1e-6).

================================================================================
GRAIN & KEYS
================================================================================
  * Consolidated node series  → summed over ALL entity prefixes in the node.
  * ``entity_split``          → the same sum, split per ``entity_prefix`` (one
                                MonthPoint list per prefix), for in-chart entity
                                breakdowns.  Σ(entity_split) == consolidated.
  * ``members``               → the ``account_number_group``s rolled into the node
                                (sorted, de-duplicated), so callers can drill down.

L3 node key   = (level_0, level_2, level_3)
L4 node key   = (level_0, level_2, level_3, level_4)  within a chosen L3
Blank/empty ``level_4`` → bucket key ``"(no L4)"`` so its accounts stay reachable.

Determinism: nodes and members are returned in a stable, sorted order; the period
axis of every emitted series follows the union of input ``period_key``s in
chronological order (the inputs are already dense + chronologically ordered).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from app.services.gl_analysis_common import AccountSeries, MonthPoint
from app.services.fin_compat_sql import period_label

#: Bucket label for accounts whose ``level_4`` is blank/empty — keeps them
#: reachable in the L3→L4→account drill instead of silently dropping them.
NO_L4_BUCKET = "(no L4)"

_ROUND = 6


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------
@dataclass
class AggSeries:
    """An aggregated node in the L3/L4 hierarchy (consolidated + entity split)."""

    key: str                       # line_code / label of the node
    level_0: str
    level_2: str
    level_3: str
    level_4: Optional[str]         # None for L3 nodes; the L4 label for L4 nodes
    series: list[MonthPoint] = field(default_factory=list)              # consolidated
    entity_split: dict[str, list[MonthPoint]] = field(default_factory=dict)
    members: list[str] = field(default_factory=list)  # account_number_groups rolled up

    def values(self) -> list[float]:
        return [p.value_keur for p in self.series]

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "level_0": self.level_0,
            "level_2": self.level_2,
            "level_3": self.level_3,
            "level_4": self.level_4,
            "series": [p.as_dict() for p in self.series],
            "entity_split": {
                ep: [p.as_dict() for p in pts]
                for ep, pts in self.entity_split.items()
            },
            "members": list(self.members),
        }


# ---------------------------------------------------------------------------
# Period-axis helpers
# ---------------------------------------------------------------------------
def _ordered_axis(accounts: Iterable[AccountSeries]) -> list[tuple[int, int]]:
    """Union of (fiscal_year, fiscal_period) across the inputs, chronological.

    The builder emits dense, chronologically ordered series sharing one span, so in
    practice every account carries the same axis; we still take the sorted union so
    a hand-built fixture with ragged series aggregates correctly.
    """
    seen: set[tuple[int, int]] = set()
    for acc in accounts:
        for p in acc.series:
            seen.add((p.fiscal_year, p.fiscal_period))
    return sorted(seen)


def _sum_points(
    members: list[AccountSeries], axis: list[tuple[int, int]],
) -> list[MonthPoint]:
    """Sum ``value_keur`` per period across ``members`` onto the shared ``axis``."""
    # index each member's series by (fy, fp) for O(1) lookup
    indexed: list[dict[tuple[int, int], float]] = [
        {(p.fiscal_year, p.fiscal_period): p.value_keur for p in acc.series}
        for acc in members
    ]
    out: list[MonthPoint] = []
    for (y, m) in axis:
        total = 0.0
        for idx in indexed:
            total += idx.get((y, m), 0.0)
        out.append(
            MonthPoint(
                fiscal_year=y,
                fiscal_period=m,
                period_key=f"{y:04d}-{m:02d}",
                label=period_label(y, m),
                value_keur=round(total, _ROUND),
            )
        )
    return out


def _entity_split(
    members: list[AccountSeries], axis: list[tuple[int, int]],
) -> dict[str, list[MonthPoint]]:
    """Per-``entity_prefix`` summed series.  Σ over prefixes == consolidated."""
    by_prefix: dict[str, list[AccountSeries]] = {}
    for acc in members:
        by_prefix.setdefault(acc.entity_prefix, []).append(acc)
    return {
        ep: _sum_points(accs, axis)
        for ep, accs in sorted(by_prefix.items())
    }


def _l4_label(acc: AccountSeries) -> str:
    lvl4 = (acc.level_4 or "").strip()
    return lvl4 if lvl4 else NO_L4_BUCKET


def _build_node(
    *,
    key: str,
    level_0: str,
    level_2: str,
    level_3: str,
    level_4: Optional[str],
    members: list[AccountSeries],
) -> AggSeries:
    axis = _ordered_axis(members)
    return AggSeries(
        key=key,
        level_0=level_0,
        level_2=level_2,
        level_3=level_3,
        level_4=level_4,
        series=_sum_points(members, axis),
        entity_split=_entity_split(members, axis),
        members=sorted({acc.account_number_group for acc in members}),
    )


# ---------------------------------------------------------------------------
# Public aggregation API
# ---------------------------------------------------------------------------
def aggregate_l3(accounts: list[AccountSeries]) -> list[AggSeries]:
    """Roll up accounts into L3 nodes, grouped by ``(level_0, level_2, level_3)``.

    Each node's ``series`` is the per-period sum of its accounts' presented
    ``value_keur``; ``entity_split`` is the same sum per ``entity_prefix``;
    ``members`` lists the rolled-up ``account_number_group``s.  Deterministic order:
    nodes sorted by ``(level_0, level_2, level_3)``.  Empty input → ``[]``.
    """
    groups: dict[tuple[str, str, str], list[AccountSeries]] = {}
    for acc in accounts:
        groups.setdefault((acc.level_0, acc.level_2, acc.level_3), []).append(acc)

    out: list[AggSeries] = []
    for (l0, l2, l3) in sorted(groups):
        out.append(
            _build_node(
                key=l3 or l2 or l0,
                level_0=l0, level_2=l2, level_3=l3, level_4=None,
                members=groups[(l0, l2, l3)],
            )
        )
    return out


def aggregate_l4(
    accounts: list[AccountSeries], level_3: str,
) -> list[AggSeries]:
    """Roll up the L4 children of one ``level_3`` into nodes.

    Selects accounts whose ``level_3`` matches ``level_3`` and groups them by their
    ``level_4`` (blank → :data:`NO_L4_BUCKET`).  Σ of the returned L4 nodes per
    period reconciles to the matching L3 node from :func:`aggregate_l3` (to 1e-6).
    Deterministic order: nodes sorted by ``(level_0, level_2, level_4)``; the
    ``(no L4)`` bucket sorts with the empty-string key.  Empty selection → ``[]``.
    """
    selected = [acc for acc in accounts if acc.level_3 == level_3]

    groups: dict[tuple[str, str, str], list[AccountSeries]] = {}
    for acc in selected:
        groups.setdefault(
            (acc.level_0, acc.level_2, _l4_label(acc)), []
        ).append(acc)

    out: list[AggSeries] = []
    for (l0, l2, l4) in sorted(groups, key=lambda k: (k[0], k[1], k[2])):
        out.append(
            _build_node(
                key=l4,
                level_0=l0, level_2=l2, level_3=level_3, level_4=l4,
                members=groups[(l0, l2, l4)],
            )
        )
    return out
