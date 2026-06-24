"""Synthetic net-profit GL bookings for the deterministic rebuild (Phase 3).

This module owns rebuild **stage 5** ("net profit").  It is invoked from
``etl.rebuild.rebuild_project`` and gated by ``settings.bs_net_profit_source``.

TWO SOURCES (presentation is IDENTICAL in both — see GATE note)
──────────────────────────────────────────────────────────────
``report_inject`` (DEFAULT / legacy 5176 / live 8010 behaviour)
    NO-OP here.  The balance-sheet net profit stays a *virtual* report-layer
    injection: ``backend/app/services/fin_compat_bs.py::_inject_net_profit``
    appends a ``line_code='NET_PROFIT'`` child under Equity and bumps the equity /
    grandtotal subtotals, sourced from the P&L SQL (``bs_net_profit_sql_*``).  No
    GL rows are written, so the ledger does NOT net to zero per FY (the residual is
    the year's net profit).  Touching nothing keeps the golden live-vs-v2
    equivalence intact.

``gl_rows`` (reporting-v2)
    SYNTHESIZE one single-sided synthetic GL row per (entity, fiscal_year) that
    credits the year's net profit into that entity's *Net profit* equity account,
    so the stored ledger ITSELF balances (``Σ BS amount incl. NP == 0`` per FY) for
    B-checks / a future cutover.  Idempotent: re-running first deletes the synthetic
    rows it previously wrote (identified by the stable ``SYNTHETIC_NP_SOURCE``
    marker) within the scope, then re-creates them.

    GATE / NO DOUBLE COUNT: the synthetic NP rows are tagged
    ``entry_type='net_profit'`` and are EXCLUDED from the BS cumulative-balance
    grain SQL (``fin_compat_bs_sql.py``), so the equity hierarchy nodes and their
    subtotals are byte-identical to ``report_inject``.  The presentation path is
    UNCHANGED in both modes — ``_inject_net_profit`` still produces the exact
    ``NET_PROFIT`` child + bumped subtotals from the P&L SQL.  The ONLY difference
    in ``gl_rows`` is the presence of the (excluded-from-presentation) balancing GL
    rows.  Hence the displayed Balance Sheet is identical and the golden gate
    passes in both directions.

FORMULA / SIGN / BALANCE
────────────────────────
Canonical sign: ``fact_gl_line.amount`` ``+`` = debit (assets), ``-`` = credit
(equity & liabilities) — identical to ``fin_compat_bs_sql.py``.  The BS reads the
raw stored sign; the credit side is display-flipped ONCE in
``fin_compat_bs._flip_row_tree``.

Presented net profit per (entity, fy) is the P&L YTD result with the P&L sign
rule applied (income +)::

    NP_presented[e, fy] = Σ (PL amount × -1)  over level_0='PL', that (e, fy)

The synthetic equity booking is stored as the **credit** value (negative for a
profit), so that after the BS credit-side display flip it presents as
``+NP_presented`` AND the ledger balances::

    NP_stored[e, fy] = Σ PL amount  over level_0='PL', that (e, fy)
                     = -NP_presented[e, fy]

Confirmed on the live DB: per FY, ``Σ(BS amount) == NP_presented == -Σ(PL
amount)``, so ``Σ(BS amount) + NP_stored == 0`` — booking ``NP_stored`` onto an
equity account makes Assets = Equity & liabilities hold (balance identity).

EQUITY TARGET
─────────────
The booking lands on the entity's canonical *Net profit* equity account
(``dim_gl_account`` ``level_0='BS'``, ``level_1='Equity & liabilities'``,
``level_2='Equity'``, ``level_3='Net profit'``; account_number_group ends
``000001``).  When an entity has more than one such account (sub-entities), the
canonical one is chosen deterministically as ``MIN(account_number_group)`` for
that ``entity_prefix`` / ``fiscal_year``.

The synthetic row is one single-line journal entry per (entity_prefix, fy):
``fiscal_period=12`` (year-end equity result), ``entry_type='net_profit'``,
``posting_date = Dec-31 of fy`` (FY year-end), deterministic 12-char
``journal_entry_group_number = EE 7 YY 000001`` (entity, ``7`` discriminator so it
never collides with the load-path ``9`` or carry-forward OB ``8``, year, account),
``source_system=SYNTHETIC_NP_SOURCE``.  **Single-sided by design** (one equity
credit, no balancing counter-line) — exempt from B1/B2/B3 via
``etl.checks._opening_exempt_mask``.  PL accounts are never touched (the booking is
on a BS equity account, so the P&L SQL — ``WHERE level_0='PL'`` — is unaffected).
"""
from __future__ import annotations

import datetime
import logging
import re

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: Stable marker on ``fact_gl_entry.source_system`` / ``fact_gl_line.source_system``
#: identifying synthetic net-profit rows.  Used to make the synthesis idempotent
#: (delete-by-marker before re-insert) and distinct from the OB carry-forward
#: marker (``synthetic_carry_forward_ob``) and the load-path source systems.
SYNTHETIC_NP_SOURCE = "synthetic_net_profit"

#: entry_type / fiscal_period tag for net-profit rows.  ``net_profit`` is a
#: distinct marker (NOT 'opening_balance') so the BS grain SQL can exclude it and
#: the balance checks can exempt it.
NET_PROFIT_ENTRY_TYPE = "net_profit"
NET_PROFIT_FISCAL_PERIOD = 12

VALID_SOURCES = frozenset({"report_inject", "gl_rows"})

#: Reserved high range for synthetic net-profit booking_line_id, distinct from
#: real loaded ids (1-based source-row positions) AND from the carry-forward OB
#: reserved base (900_000_000_000) so synthetic ids never collide.
_SYNTHETIC_BID_BASE = 700_000_000_000

#: Zero tolerance: |net profit| at or below this is treated as nil (no row).
_ZERO_TOL = 1e-6


def _synthetic_np_jegn(entity_prefix: str, fiscal_year: int, account_number_group: str) -> str:
    """Deterministic 12-char journal_entry_group_number for a synthetic NP booking.

    Layout: ``<EE>7<YY><AAAAAA>`` capped to 12 chars (fact_gl_entry PK width).
      EE       = 2-char entity prefix
      7        = net-profit discriminator (load uses '9', carry-forward OB '8'; we
                 use '7' so synthetic NP rows never collide with either).
      YY       = last two digits of fiscal year
      AAAAAA   = account number (entity-stripped) zero-padded / truncated to 6

    Deterministic in (entity, fy, account) so a re-run targets the exact same PK.
    """
    ee = re.sub(r"\D", "", str(entity_prefix)).zfill(2)[-2:]
    yy = str(int(fiscal_year)).zfill(4)[-2:]
    acct = re.sub(r"\D", "", str(account_number_group))
    acct_tail = acct[2:] if len(acct) > 2 else acct
    a6 = acct_tail.zfill(6)[-6:]
    return f"{ee}7{yy}{a6}"[:12]


def _synthetic_bid(entity_prefix: str, fiscal_year: int, account_number_group: str) -> int:
    """Deterministic, collision-free synthetic booking_line_id in the reserved range.

    Distinct per (entity_prefix, fiscal_year, account) — the NP account tail is
    almost always ``000001`` across entities, so the booking_line_id must encode
    the ENTITY (not just the account) to avoid cross-entity collisions::

        base + EE*10^10 + YY*10^8 + (account tail mod 10^8)
    """
    ee = int(re.sub(r"\D", "", str(entity_prefix)) or "0") % 100
    yy = int(fiscal_year) % 100
    acct = re.sub(r"\D", "", str(account_number_group))
    acct_tail = acct[2:] if len(acct) > 2 else acct
    a = int(acct_tail or "0") % 100_000_000
    return _SYNTHETIC_BID_BASE + ee * 10_000_000_000 + yy * 100_000_000 + a


def _scope_year_set(scope) -> set[int] | None:
    """Target fiscal years from the rebuild scope, or None for all years."""
    years = getattr(scope, "years", None) or []
    return {int(y) for y in years} if years else None


def _scope_prefix_set(scope) -> set[str] | None:
    """Target 2-char entity prefixes from the rebuild scope, or None for global.

    When the scope carries entity prefixes (a per-entity incremental rebuild), the
    idempotency DELETE and the net-profit COMPUTE must be confined to those entities
    so an entity-scoped rebuild of A never deletes / recreates entity B's synthetic
    net-profit rows for the same year.  Empty / no prefixes => global (Phase-1)
    behaviour, preserving the golden equivalence.
    """
    prefixes = getattr(scope, "prefixes", None) or []
    cleaned = {str(p).strip()[:2] for p in prefixes if str(p).strip()}
    return cleaned or None


def _delete_synthetic_np(
    session: Session,
    year_set: set[int] | None,
    prefix_set: set[str] | None = None,
) -> int:
    """Idempotency: remove previously-synthesized net-profit rows in scope.

    Deletes lines first (FK to entry), then the now-orphaned synthetic entries.
    Identified by the stable ``source_system = SYNTHETIC_NP_SOURCE`` marker so we
    never touch real GL rows, opening balances, or any non-NP row.

    When *prefix_set* is given (entity-scoped rebuild), BOTH deletes are additionally
    confined to those entity prefixes.  Lines are matched on the synthetic row's
    equity ``account_number_group`` prefix; entries are matched via the synthetic
    deterministic ``journal_entry_group_number`` whose first two chars carry the same
    entity prefix (see ``_synthetic_np_jegn``).  Dialect-neutral ``SUBSTR`` works on
    both PostgreSQL and SQLite.  When *prefix_set* is None, the global behaviour is
    unchanged (Phase-1 / golden parity).
    """
    params: dict = {"src": SYNTHETIC_NP_SOURCE}
    year_clause = ""
    if year_set:
        ph = ", ".join(f":dy{i}" for i in range(len(year_set)))
        for i, y in enumerate(sorted(year_set)):
            params[f"dy{i}"] = y
        year_clause = f" AND fiscal_year IN ({ph})"

    line_pfx_clause = ""
    entry_pfx_clause = ""
    if prefix_set:
        pph = ", ".join(f":pfx{i}" for i in range(len(prefix_set)))
        for i, p in enumerate(sorted(prefix_set)):
            params[f"pfx{i}"] = p
        line_pfx_clause = f" AND SUBSTR(account_number_group, 1, 2) IN ({pph})"
        entry_pfx_clause = f" AND SUBSTR(journal_entry_group_number, 1, 2) IN ({pph})"

    line_res = session.execute(
        text(
            f"DELETE FROM fact_gl_line "
            f"WHERE source_system = :src{year_clause}{line_pfx_clause}"
        ),
        params,
    )
    session.execute(
        text(
            f"DELETE FROM fact_gl_entry "
            f"WHERE source_system = :src{year_clause}{entry_pfx_clause}"
        ),
        params,
    )
    return int(line_res.rowcount or 0)


def _canonical_np_accounts(session: Session) -> dict[tuple[str, int], str]:
    """Canonical Net-profit equity account per (entity_prefix, fiscal_year).

    Targets ``dim_gl_account`` rows with ``level_0='BS'`` and
    ``level_3='Net profit'`` (the seeded Equity & liabilities / Equity / Net profit
    accounts, one or more per entity).  When an entity has more than one such
    account in a year (sub-entities), the canonical one is chosen deterministically
    as ``MIN(account_number_group)``.  Pure, dialect-neutral SQL.
    """
    rows = session.execute(
        text(
            """
            SELECT entity_prefix, fiscal_year, MIN(account_number_group) AS account_number_group
              FROM dim_gl_account
             WHERE level_0 = 'BS'
               AND level_3 = 'Net profit'
             GROUP BY entity_prefix, fiscal_year
            """
        )
    ).fetchall()
    return {(str(r[0]), int(r[1])): str(r[2]) for r in rows}


def _compute_net_profit(
    session: Session, prefix_set: set[str] | None = None
) -> list[dict]:
    """Compute NP_stored[e, fy] = Σ amount over level_0='PL' rows of (e, fy).

    Pure, dialect-neutral SQL (standard SUM/GROUP BY/JOIN only).  Returns one dict
    per non-zero net profit: {entity_prefix, fiscal_year, np_stored} where
    ``np_stored`` is the credit value (= -presented; negative for a profit).
    The synthetic NP rows themselves are on BS accounts, so they never enter this
    PL sum — but we additionally guard on the marker for defensiveness.

    When *prefix_set* is given (entity-scoped rebuild), only in-scope entities are
    computed, so an entity-scoped rebuild never recreates another entity's synthetic
    NP rows.  None => all entities (global / Phase-1, golden parity).
    """
    params: dict = {"src": SYNTHETIC_NP_SOURCE}
    pfx_clause = ""
    if prefix_set:
        pph = ", ".join(f":pfx{i}" for i in range(len(prefix_set)))
        for i, p in enumerate(sorted(prefix_set)):
            params[f"pfx{i}"] = p
        pfx_clause = f" AND a.entity_prefix IN ({pph})"
    rows = session.execute(
        text(
            f"""
            SELECT a.entity_prefix,
                   l.fiscal_year,
                   COALESCE(SUM(l.amount), 0) AS np_stored
              FROM fact_gl_line l
              JOIN dim_gl_account a
                ON a.account_number_group = l.account_number_group
               AND a.fiscal_year = l.fiscal_year
             WHERE a.level_0 = 'PL'
               AND COALESCE(l.source_system, '') <> :src{pfx_clause}
             GROUP BY a.entity_prefix, l.fiscal_year
            """
        ),
        params,
    ).fetchall()

    out: list[dict] = []
    for r in rows:
        amount = float(r[2] or 0.0)
        if abs(amount) <= _ZERO_TOL:
            continue
        out.append(
            {
                "entity_prefix": str(r[0]),
                "fiscal_year": int(r[1]),
                "np_stored": amount,
            }
        )
    return out


def synthesize_net_profit(session: Session, scope) -> dict:
    """Stage 5 (gl_rows source): synthesize balancing net-profit equity bookings.

    Steps (all in the caller's open transaction):
      1. DELETE prior synthetic net-profit rows in scope (idempotency).
      2. NP_stored = Σ amount over level_0='PL' rows of (entity, fy), per entity/fy.
      3. Insert ONE single-line synthetic journal entry per non-zero NP onto that
         entity's canonical 'Net profit' equity account (fiscal_period=12,
         entry_type='net_profit', source_system marker, posting_date = Dec-31 of fy,
         deterministic group number + booking_line_id).

    Only invoked when ``settings.bs_net_profit_source == 'gl_rows'``.  An entity/fy
    with non-zero net profit but no seeded 'Net profit' account is skipped (logged).

    Parameters
    ----------
    session : Session
        Open SQLAlchemy session (caller owns the transaction / commit).
    scope : RebuildScope | None | object with ``.years``
        Rebuild scope; only ``years`` restricts the synthesized target years.

    Returns
    -------
    dict
        Result summary (row counts).
    """
    year_set = _scope_year_set(scope)
    prefix_set = _scope_prefix_set(scope)
    deleted = _delete_synthetic_np(session, year_set, prefix_set)

    targets = _canonical_np_accounts(session)
    profits = _compute_net_profit(session, prefix_set)
    if year_set is not None:
        profits = [p for p in profits if p["fiscal_year"] in year_set]

    # NOTE: ``entity_prefix`` is a GENERATED column in Postgres (derived from the
    # journal_entry_group_number); we must NOT insert into it.  The deterministic
    # ``jegn`` (EE 7 YY ...) carries the entity prefix, so the generated value is
    # correct.  (Matches etl.opening_balance, which also omits entity_prefix.)
    entry_sql = text(
        """
        INSERT INTO fact_gl_entry
          (journal_entry_group_number, fiscal_year, fiscal_period, entry_type,
           posting_date, currency_code, header_note, source_system)
        VALUES (:jegn, :fy, :fp, :et, :pd, 'EUR', :note, :src)
        ON CONFLICT (journal_entry_group_number, fiscal_year) DO NOTHING
        """
    )
    line_sql = text(
        """
        INSERT INTO fact_gl_line
          (journal_entry_group_number, fiscal_year, line_number, booking_line_id,
           account_number_group, amount, line_note, source_system)
        VALUES (:jegn, :fy, 1, :bid, :ang, :amt, :note, :src)
        ON CONFLICT (journal_entry_group_number, fiscal_year, line_number) DO NOTHING
        """
    )
    note = "Synthetic net profit (carried into equity)"
    inserted = 0
    skipped: list[tuple[str, int]] = []
    for p in profits:
        ep = p["entity_prefix"]
        fy = p["fiscal_year"]
        ang = targets.get((ep, fy))
        if ang is None:
            skipped.append((ep, fy))
            continue
        jegn = _synthetic_np_jegn(ep, fy, ang)
        session.execute(
            entry_sql,
            {
                "jegn": jegn, "fy": fy, "fp": NET_PROFIT_FISCAL_PERIOD,
                "et": NET_PROFIT_ENTRY_TYPE, "pd": datetime.date(fy, 12, 31),
                "note": note, "src": SYNTHETIC_NP_SOURCE,
            },
        )
        res = session.execute(
            line_sql,
            {
                "jegn": jegn, "fy": fy, "bid": _synthetic_bid(ep, fy, ang),
                "ang": ang, "amt": p["np_stored"],
                "note": note, "src": SYNTHETIC_NP_SOURCE,
            },
        )
        inserted += int(res.rowcount or 0)

    out = {
        "source": "gl_rows",
        "synthetic_net_profit_deleted": deleted,
        "net_profit_rows": inserted,
        "skipped_no_account": skipped,
    }
    logger.info("net_profit gl_rows: %s", out)
    return out
