"""Shared foundation for the GL-anomaly analyses (Journal Agent, Phase 0).

This module builds, in **one** grouped query, an ALL-HISTORY monthly time series
per GL account — accounts on rows, one column per ``(fiscal_year, fiscal_period)``
month actually present in the ledger — and offers a material-account bounding
helper.  Three downstream analyses (outliers, seasonality, forensic) reuse it so
the *sign convention* and the *synthetic-row exclusion* are defined exactly once.

================================================================================
WHY A NEW BUILDER (and not the trial-balance services)
================================================================================
``fin_compat_trial_balance._export_periods`` (fin_compat_trial_balance.py:62) is a
**fixed 4-year window** (``range(year - 3, year + 1)``).  Outlier / seasonality
detection needs the *whole* history, so this module discovers the min/max present
``(fiscal_year, fiscal_period)`` and groups by every month in that span instead.

================================================================================
SIGN CONVENTION (reused verbatim from the compat SQL layer)
================================================================================
``fact_gl_line.amount`` is signed: ``+`` = debit (Soll), ``−`` = credit (Haben).

  * PL accounts (``dim_gl_account.level_0 = 'PL'``) are presented as ``amount * -1``
    — revenue (credit, stored −) → positive; expense (debit, stored +) → negative.
    This is the SAME inversion applied in ``fin_compat_sql.pl_grain_sql_month`` et al.
  * BS accounts (``level_0 = 'BS'``) keep the **raw stored sign** (``amount``) —
    assets positive, equity & liabilities negative — matching
    ``fin_compat_bs_sql`` (``SUM(l.amount)``, no inversion).

A single per-row ``CASE WHEN a.level_0 = 'PL' THEN l.amount * -1 ELSE l.amount END``
encodes both rules so a mixed PL/BS pull is presented correctly in one query.

================================================================================
SYNTHETIC-ROW EXCLUSION (CRITICAL — the load-bearing correctness property)
================================================================================
``fact_gl_line`` has **no** ``entry_type`` / ``source_system``-driven marker we can
filter on alone for entry semantics — ``entry_type`` lives on ``fact_gl_entry``.
We therefore JOIN ``fact_gl_entry e`` and exclude the two synthetic GL sources:

  * ``synthetic_carry_forward_ob`` — carry-forward opening balances
    (``etl/opening_balance.py``: ``SYNTHETIC_OB_SOURCE``), at ``fiscal_period = 0``.
  * ``synthetic_net_profit``       — year-end equity net-profit bookings
    (``etl/net_profit.py``: ``SYNTHETIC_NP_SOURCE``), at ``fiscal_period = 12``.

Both set the marker on BOTH the entry header and the line, so excluding on
``e.source_system NOT LIKE 'synthetic\\_%' ESCAPE '\\'`` removes them.  Left in,
the period-0 OB rows would manufacture fake outliers and the period-12 NP rows
would manufacture a fake December peak in the seasonality decomposition.

NOTE: the trial-balance / balance-sheet services deliberately INCLUDE the opening
balances (they are part of the cumulative BS stock) — so this exclusion is NEW and
must not be copied from the TB WHERE clause.  We additionally restrict to
``fiscal_period BETWEEN 1 AND 12`` (drops the period-13 consolidation rows and the
period-0 OB rows for good measure).

================================================================================
GRAIN
================================================================================
The same business account number can exist under several entity prefixes, so for
the consolidated pull (entity ``all`` / ``None``) we group by
``(entity_prefix, account_number_group)`` and expose ``entity_prefix`` on every
account row.  For a single resolved entity we scope by that ``entity_prefix`` and
still group per ``account_number_group`` (one prefix in play).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.fin_compat_narrative_core import MOM_FLOOR_EUR, SIZE_FLOOR_EUR
from app.services.fin_compat_sql import (
    _MONTH_ABBR,
    entity_sql_fragment,
    period_key,
    period_label,
    resolve_entity_prefix,
)

# Markers written by the two synthetic GL stages (etl/opening_balance.py,
# etl/net_profit.py).  Kept as named constants for the docstring / tests; the SQL
# filter uses the shared ``synthetic\_%`` prefix so any future ``synthetic_*``
# source is also excluded.
SYNTHETIC_SOURCE_PREFIX = "synthetic_"
SYNTHETIC_OB_SOURCE = "synthetic_carry_forward_ob"
SYNTHETIC_NP_SOURCE = "synthetic_net_profit"

#: SQL AND-fragment excluding every synthetic GL source on the ENTRY side.  The
#: ``\`` escapes the ``_`` wildcard so it matches a literal underscore only.
_NOT_SYNTHETIC = "AND e.source_system NOT LIKE 'synthetic\\_%' ESCAPE '\\'"

#: Presented-amount expression: PL inverted (revenue +), BS raw (assets +).  The
#: single place this module decides PL vs BS sign — mirrors fin_compat_sql /
#: fin_compat_bs_sql.
_PRESENTED_AMOUNT = "CASE WHEN a.level_0 = 'PL' THEN l.amount * -1 ELSE l.amount END"

_EUR_PER_KEUR = 1000.0


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MonthPoint:
    """One month in an account's series (presented value, in kEUR)."""

    fiscal_year: int
    fiscal_period: int
    period_key: str          # "YYYY-MM"
    label: str               # short month label, e.g. "Jul24"
    value_keur: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "fiscal_year": self.fiscal_year,
            "fiscal_period": self.fiscal_period,
            "period_key": self.period_key,
            "label": self.label,
            "value_keur": self.value_keur,
        }


@dataclass
class AccountSeries:
    """All-history monthly series for one GL account at the resolved grain."""

    gl_account_id: str
    account_name: str
    account_number_group: str
    entity_prefix: str
    level_0: str
    level_2: str
    level_3: str
    series: list[MonthPoint] = field(default_factory=list)
    # Hierarchy levels below level_3 (L3→L4→Account drill, Phase 0).  Exposed via
    # MAX(a.level_4)/MAX(a.l4_sub) at the SAME grain — like level_2/level_3 — so the
    # consolidated row grain (entity_prefix, account_number_group) is UNCHANGED.
    # Safe defaults keep older callers / hand-built fixtures valid.
    level_4: str = ""
    l4_sub: str = ""

    # -- convenience views over the ordered series -------------------------
    def values(self) -> list[float]:
        return [p.value_keur for p in self.series]

    def value_at(self, fiscal_year: int, fiscal_period: int) -> float:
        for p in self.series:
            if p.fiscal_year == fiscal_year and p.fiscal_period == fiscal_period:
                return p.value_keur
        return 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "gl_account_id": self.gl_account_id,
            "account_name": self.account_name,
            "account_number_group": self.account_number_group,
            "entity_prefix": self.entity_prefix,
            "level_0": self.level_0,
            "level_2": self.level_2,
            "level_3": self.level_3,
            "level_4": self.level_4,
            "l4_sub": self.l4_sub,
            "series": [p.as_dict() for p in self.series],
        }


# ---------------------------------------------------------------------------
# All-history month-span discovery
# ---------------------------------------------------------------------------
def discover_month_span(
    session: Session, ent_frag: str = "",
) -> list[tuple[int, int]]:
    """Return every ``(fiscal_year, fiscal_period)`` present, chronological.

    Discovers the DISTINCT real months in the ledger (synthetic rows excluded,
    ``fiscal_period BETWEEN 1 AND 12``).  This is the all-history axis the wide
    series query pivots on — NOT a fixed window.  Empty ledger → ``[]``.
    """
    sql = f"""
        SELECT DISTINCT e.fiscal_year AS fy, e.fiscal_period AS fp
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        WHERE e.fiscal_period BETWEEN 1 AND 12
          {_NOT_SYNTHETIC}
          {ent_frag}
        ORDER BY fy, fp
    """
    rows = session.execute(text(sql)).fetchall()
    return [(int(r[0]), int(r[1])) for r in rows]


def latest_anchor(
    session: Session, ent_frag: str = "",
) -> Optional[tuple[int, int]]:
    """Return the LAST real ``(fiscal_year, fiscal_period)`` in the ledger, or ``None``.

    Convenience wrapper over :func:`discover_month_span` — the most recent month is
    the natural materiality anchor when the caller has no explicit period (the
    hierarchical anomaly trees bound materiality here).  Empty ledger → ``None``.

    NOTE: the latest month MAY be incomplete (mid-close) — callers that care about
    completeness should document it; for materiality bounding a partial last month
    still surfaces the accounts that matter.
    """
    span = discover_month_span(session, ent_frag)
    return span[-1] if span else None


# ---------------------------------------------------------------------------
# All-history per-account monthly series (one grouped query)
# ---------------------------------------------------------------------------
def build_account_monthly_series(
    session: Session,
    *,
    entity: Optional[str] = None,
    level_0: Optional[str] = None,
) -> list[AccountSeries]:
    """Build the all-history monthly series per GL account in ONE grouped query.

    Accounts are the rows; one presented-value column per month present in the
    ledger (discovered by :func:`discover_month_span`).  Values are in **kEUR**.

    Args:
        entity: legal-entity code, or ``None`` / ``"all"`` for the consolidated
            pull.  Consolidated groups by ``(entity_prefix, account_number_group)``
            so the same account number under different prefixes stays separate.
        level_0: optional ``'PL'`` / ``'BS'`` restriction (default: both).

    Returns:
        ``AccountSeries`` per (entity_prefix, account_number_group), each with the
        chronologically ordered ``series``.  Empty ledger → ``[]``.
    """
    ep = resolve_entity_prefix(session, entity)
    ent_frag = entity_sql_fragment(ep)

    span = discover_month_span(session, ent_frag)
    if not span:
        return []

    l0_frag = ""
    if level_0 in ("PL", "BS"):
        l0_frag = f"AND a.level_0 = '{level_0}'"

    # One presented-amount CASE column per present month.  Column alias = period
    # key "YYYY-MM"; double-quoted so it survives the dash.
    cases: list[str] = []
    for y, m in span:
        pk = period_key(y, m)
        cases.append(
            f"COALESCE(SUM(CASE WHEN e.fiscal_year = {y} AND e.fiscal_period = {m} "
            f"THEN ({_PRESENTED_AMOUNT}) ELSE 0 END), 0) AS \"{pk}\""
        )

    sql = f"""
        SELECT
            l.entity_prefix              AS entity_prefix,
            l.account_number_group       AS account_number_group,
            MAX(a.gl_account_id)         AS gl_account_id,
            MAX(a.account_name)          AS account_name,
            MAX(a.level_0)               AS level_0,
            MAX(a.level_2)               AS level_2,
            MAX(a.level_3)               AS level_3,
            MAX(a.level_4)               AS level_4,
            MAX(a.l4_sub)                AS l4_sub,
            {', '.join(cases)}
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE e.fiscal_period BETWEEN 1 AND 12
          {_NOT_SYNTHETIC}
          {l0_frag}
          {ent_frag}
        GROUP BY l.entity_prefix, l.account_number_group
        ORDER BY l.entity_prefix, l.account_number_group
    """
    rows = session.execute(text(sql)).fetchall()

    out: list[AccountSeries] = []
    for r in rows:
        d = dict(r._mapping) if hasattr(r, "_mapping") else dict(r)
        points: list[MonthPoint] = []
        for y, m in span:
            pk = period_key(y, m)
            raw = d.get(pk)
            points.append(
                MonthPoint(
                    fiscal_year=y,
                    fiscal_period=m,
                    period_key=pk,
                    label=period_label(y, m),
                    value_keur=round(float(raw or 0.0) / _EUR_PER_KEUR, 3),
                )
            )
        out.append(
            AccountSeries(
                gl_account_id=(d.get("gl_account_id") or "").strip(),
                account_name=(d.get("account_name") or "").strip(),
                account_number_group=(d.get("account_number_group") or "").strip(),
                entity_prefix=(d.get("entity_prefix") or "").strip(),
                level_0=(d.get("level_0") or "").strip(),
                level_2=(d.get("level_2") or "").strip(),
                level_3=(d.get("level_3") or "").strip(),
                level_4=(d.get("level_4") or "").strip(),
                l4_sub=(d.get("l4_sub") or "").strip(),
                series=points,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Material-account bounding
# ---------------------------------------------------------------------------
def _abs_cm_keur(acc: AccountSeries, fiscal_year: int, fiscal_period: int) -> float:
    return abs(acc.value_at(fiscal_year, fiscal_period))


def _abs_mom_keur(acc: AccountSeries, fiscal_year: int, fiscal_period: int) -> float:
    """|current-month − prior-month| in kEUR, using the series' own order."""
    keys = [(p.fiscal_year, p.fiscal_period) for p in acc.series]
    try:
        idx = keys.index((fiscal_year, fiscal_period))
    except ValueError:
        return 0.0
    cm = acc.series[idx].value_keur
    pm = acc.series[idx - 1].value_keur if idx > 0 else 0.0
    return abs(cm - pm)


def bound_material_accounts(
    accounts: list[AccountSeries],
    *,
    current_year: int,
    current_period: int,
    size_floor_eur: float = SIZE_FLOOR_EUR,
    mom_floor_eur: float = MOM_FLOOR_EUR,
    top_n: Optional[int] = None,
) -> list[AccountSeries]:
    """Keep accounts material at ``(current_year, current_period)``.

    An account is kept when, at the current period, EITHER
      * ``|cm| >= size_floor_eur``           (absolute size), OR
      * ``|cm − pm| >= mom_floor_eur``       (month-over-month move).

    The floors default to ``SIZE_FLOOR_EUR`` / ``MOM_FLOOR_EUR`` from
    ``fin_compat_narrative_core`` (EUR).  Series values are kEUR, so the floors
    are converted to kEUR for comparison.  ``top_n`` (optional) caps the result to
    the largest accounts by magnitude (``max(|cm|, |mom|)``), descending — a
    deterministic tie-break on ``(entity_prefix, account_number_group)`` keeps the
    order stable.  Parameterised so outliers / seasonality / forensic can each
    tune the floors and cap.
    """
    size_floor_k = size_floor_eur / _EUR_PER_KEUR
    mom_floor_k = mom_floor_eur / _EUR_PER_KEUR

    scored: list[tuple[float, AccountSeries]] = []
    for acc in accounts:
        cm = _abs_cm_keur(acc, current_year, current_period)
        mom = _abs_mom_keur(acc, current_year, current_period)
        if cm >= size_floor_k or mom >= mom_floor_k:
            scored.append((max(cm, mom), acc))

    # Deterministic ordering: magnitude desc, then stable account identity asc.
    scored.sort(
        key=lambda t: (-t[0], t[1].entity_prefix, t[1].account_number_group)
    )
    kept = [acc for _, acc in scored]
    if top_n is not None and top_n >= 0:
        kept = kept[:top_n]
    return kept
