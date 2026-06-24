"""Opening-balance synthesis for the deterministic reporting-v2 rebuild (Phase 2).

This module owns rebuild **stage 4** ("opening balances").  It is invoked from
``etl.rebuild.rebuild_project`` and gated by ``settings.opening_balance_mode``.

THREE MODES
───────────
``in_data`` (DEFAULT / legacy 5176 behaviour)
    NO-OP.  Opening balances already exist in the loaded GL as
    ``entry_type='opening_balance'`` / ``fiscal_period=0`` rows (detected at load
    time by ``etl.gobd_gl_prepare``).  Touching nothing here keeps the golden
    live-vs-v2 equivalence intact.

``file``
    A separate first-year opening-balance file/table has already been loaded
    through the canonical loader.  This mode only *ensures the tagging* is correct
    on those rows — i.e. that their header (``fact_gl_entry``) carries
    ``entry_type='opening_balance'`` and ``fiscal_period=0`` so the balance checks
    (B1/B2/B3) exempt them and the BS layer reads them as opening stock.  It does
    NOT synthesize anything.  Input shape: ordinary canonical GL rows whose
    synthetic ``journal_entry_group_number`` is minted by
    ``etl.gobd_gl_prepare._synthetic_opening_txn`` (prefix ``9`` + yy + entity +
    account) and whose ``source_system`` marks the file origin.

``carry_forward``
    SYNTHESIZE opening balances for every fiscal year AFTER the first, per
    (entity, BS account), as the cumulative prior-year closing balance.  See the
    formula below.  Idempotent: re-running first deletes the synthetic rows it
    previously wrote (identified by the stable source marker
    ``SYNTHETIC_OB_SOURCE``) within the scope, then re-creates them.

CARRY-FORWARD FORMULA
─────────────────────
For a balance-sheet account ``a`` of entity ``e`` and fiscal year ``fy``::

    OB[e, a, fy] = Σ amount  over all fact_gl_line rows of (e, a)
                            with fiscal_year ≤ fy-1

i.e. the cumulative closing balance carried into year ``fy`` is the sum of every
posting (including prior synthetic opening balances are NOT counted — we sum
*movements*, see below) on that account up to and including the prior year.

Implementation detail — to avoid double counting we sum only the **real
movements** of prior years, which for the carry-forward target is equivalent to
the prior-year *closing* balance.  Concretely the prior-year closing balance of a
BS account already equals ``Σ(all rows fiscal_year ≤ fy-1)`` because each year's
own opening balance was itself the carry-forward of the year before.  We therefore
exclude our own synthetic rows from the cumulative sum (``source_system <>
SYNTHETIC_OB_SOURCE``) so a re-run is stable, and we recompute from the real
ledger every time.

SIGN / BALANCE
──────────────
Canonical sign: ``amount`` ``+`` = debit (assets), ``-`` = credit (equity &
liabilities) — identical to ``fin_compat_bs_sql.py``.  A correctly balanced prior
year has ``Σ amount = 0`` per entity across ALL accounts (B2).  BS accounts alone
do NOT net to zero (that residual is the P&L result of the year, which lands in
equity via the Phase-3 net-profit booking — NOT implemented here).  Therefore the
carry-forward of BS accounts ONLY is *single-sided* by design, exactly like the
real Jan-1 opening balances in the source data, and is **exempt** from B1/B2/B3 by
``etl.checks._opening_exempt_mask`` (``fiscal_period=0`` / ``entry_type=
'opening_balance'``).  Net profit / retained-earnings handling is Phase 3.

PL accounts (``level_0='PL'``) are NEVER carried forward — they reset to zero each
fiscal year.

Each synthesized opening balance is posted as ONE single-line synthetic journal
entry per (entity, account, fy) with a deterministic
``journal_entry_group_number`` (see ``_synthetic_ob_jegn``), ``fiscal_period=0``,
``entry_type='opening_balance'`` and ``source_system=SYNTHETIC_OB_SOURCE``.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: Stable marker on ``fact_gl_entry.source_system`` / ``fact_gl_line.source_system``
#: identifying carry-forward synthetic opening-balance rows.  Used to make the
#: synthesis idempotent (delete-by-marker before re-insert) and to exclude our own
#: rows from the cumulative carry-forward sum.
SYNTHETIC_OB_SOURCE = "synthetic_carry_forward_ob"

#: entry_type / fiscal_period tag for opening balances (matches gobd_gl_prepare +
#: checks._OPENING_ENTRY_TYPES).
OPENING_ENTRY_TYPE = "opening_balance"
OPENING_FISCAL_PERIOD = 0

VALID_MODES = frozenset({"in_data", "file", "carry_forward"})


def _synthetic_ob_jegn(entity_prefix: str, fiscal_year: int, account_number_group: str) -> str:
    """Deterministic 12-char journal_entry_group_number for a carry-forward OB.

    Layout: ``<EE>8<YY><AAAAAA>`` capped to 12 chars (fact_gl_entry PK width).
      EE       = 2-char entity prefix (LEFT(account_number_group, 2))
      8        = carry-forward OB discriminator (the in-data loader uses '9';
                 we use '8' so synthetic carry-forward rows never collide with
                 real Jan-1 opening rows minted by gobd_gl_prepare).
      YY       = last two digits of fiscal year
      AAAAAA   = account number (entity-stripped) zero-padded / truncated to 6

    Deterministic in (entity, fy, account) so a re-run targets the exact same PK.
    """
    ee = re.sub(r"\D", "", str(entity_prefix)).zfill(2)[-2:]
    yy = str(int(fiscal_year)).zfill(4)[-2:]
    acct = re.sub(r"\D", "", str(account_number_group))
    # account_number_group is entity_prefix(2) + account; strip the prefix for the tail
    acct_tail = acct[2:] if len(acct) > 2 else acct
    a6 = acct_tail.zfill(6)[-6:]
    return f"{ee}8{yy}{a6}"[:12]


# --------------------------------------------------------------------------- #
# Mode: in_data (no-op)
# --------------------------------------------------------------------------- #
def _mode_in_data(session: Session, scope) -> dict:
    """Legacy / default: opening balances already present in the loaded GL.  No-op."""
    return {"mode": "in_data", "opening_balance_rows": 0, "noop": True}


# --------------------------------------------------------------------------- #
# Mode: file (ensure tagging only)
# --------------------------------------------------------------------------- #
def _mode_file(session: Session, scope) -> dict:
    """Ensure separately-loaded first-year opening-balance rows are tagged.

    The opening-balance file is loaded through the canonical loader exactly like a
    normal GL file; its synthetic journal_entry_group_number is minted by
    ``gobd_gl_prepare`` (leading '9').  This mode normalises the *header* tags so
    the balance checks exempt them and the BS layer treats them as opening stock:
    sets ``entry_type='opening_balance'`` and ``fiscal_period=0`` on any
    ``fact_gl_entry`` whose group number was minted as an opening-balance txn
    (leading '9' after the 2-char entity prefix) but is not yet tagged.

    It NEVER synthesizes rows and NEVER touches ``fact_gl_line.amount``.
    """
    # Tag headers whose group number matches the gobd_gl_prepare opening pattern
    # (entity_prefix(2) + '9' + ...) and that are not already tagged.
    result = session.execute(
        text(
            """
            UPDATE fact_gl_entry
               SET entry_type    = :ot,
                   fiscal_period = :fp
             WHERE SUBSTR(journal_entry_group_number, 3, 1) = '9'
               AND (entry_type IS NULL OR entry_type <> :ot
                    OR fiscal_period IS NULL OR fiscal_period <> :fp)
            """
        ),
        {"ot": OPENING_ENTRY_TYPE, "fp": OPENING_FISCAL_PERIOD},
    )
    n = int(result.rowcount or 0)
    return {"mode": "file", "opening_balance_entries_tagged": n}


# --------------------------------------------------------------------------- #
# Mode: carry_forward (synthesize)
# --------------------------------------------------------------------------- #
#: Reserved high range for synthetic booking_line_id (real loaded ids are 1-based
#: source-row positions, far below this) so synthetic ids never collide.
_SYNTHETIC_BID_BASE = 900_000_000_000

#: Zero tolerance: |carry-forward| at or below this is treated as nil (no row).
_ZERO_TOL = 1e-6


def _scope_year_set(scope) -> set[int] | None:
    """Target fiscal years from the rebuild scope, or None for all years."""
    years = getattr(scope, "years", None) or []
    return {int(y) for y in years} if years else None


def _scope_prefix_set(scope) -> set[str] | None:
    """Target 2-char entity prefixes from the rebuild scope, or None for global.

    When the scope carries entity prefixes (a per-entity incremental rebuild), the
    idempotency DELETE and the carry-forward COMPUTE must be confined to those
    entities so an entity-scoped rebuild of A never deletes / recreates entity B's
    synthetic OB rows for the same year.  Empty / no prefixes => global (Phase-1)
    behaviour, preserving the golden equivalence.
    """
    prefixes = getattr(scope, "prefixes", None) or []
    cleaned = {str(p).strip()[:2] for p in prefixes if str(p).strip()}
    return cleaned or None


def _delete_synthetic_ob(
    session: Session,
    year_set: set[int] | None,
    prefix_set: set[str] | None = None,
) -> int:
    """Idempotency: remove previously-synthesized carry-forward OB rows in scope.

    Deletes lines first (FK to entry), then the now-orphaned synthetic entries.
    Identified by the stable ``source_system = SYNTHETIC_OB_SOURCE`` marker so we
    never touch real / in-data opening balances or any non-OB row.

    When *prefix_set* is given (entity-scoped rebuild), BOTH deletes are additionally
    confined to those entity prefixes.  Lines are matched directly on the synthetic
    row's ``account_number_group`` prefix; entries are matched via the synthetic
    deterministic ``journal_entry_group_number`` whose first two chars carry the same
    entity prefix (see ``_synthetic_ob_jegn``).  Dialect-neutral ``SUBSTR`` works on
    both PostgreSQL and SQLite.  When *prefix_set* is None, the global behaviour is
    unchanged (Phase-1 / golden parity).
    """
    params: dict = {"src": SYNTHETIC_OB_SOURCE}
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


def _compute_carry_forward(
    session: Session, prefix_set: set[str] | None = None
) -> list[dict]:
    """Compute OB[e, a, fy] = Σ amount (real rows, fiscal_year ≤ fy-1) per BS account.

    Pure, dialect-neutral SQL (standard SUM/GROUP BY/JOIN only) so it runs on both
    PostgreSQL and SQLite.  Returns one dict per non-zero carry-forward:
    {entity_prefix, account_number_group, fiscal_year, ob_amount}.  Only BS
    accounts (level_0='BS'), only years after each entity's first year.

    When *prefix_set* is given (entity-scoped rebuild), only accounts whose
    ``entity_prefix`` is in scope are synthesized, so an entity-scoped rebuild never
    recreates another entity's synthetic OB rows.  None => all entities (global /
    Phase-1, golden parity).
    """
    # Targets = (every BS account) × (every fiscal year the account's ENTITY is
    # active after its first year).  We build the year set per entity (not per
    # account) so an account whose balance exists but has no later movement still
    # carries forward into subsequent years.
    params: dict = {"src": SYNTHETIC_OB_SOURCE}
    pfx_clause = ""
    if prefix_set:
        pph = ", ".join(f":pfx{i}" for i in range(len(prefix_set)))
        for i, p in enumerate(sorted(prefix_set)):
            params[f"pfx{i}"] = p
        pfx_clause = f" WHERE entity_prefix IN ({pph})"
    rows = session.execute(
        text(
            f"""
            WITH scoped_accounts AS (
                SELECT account_number_group, entity_prefix, level_0, fiscal_year
                  FROM dim_gl_account
                  {pfx_clause}
            ),
            bs_accounts AS (
                SELECT DISTINCT account_number_group, entity_prefix
                  FROM scoped_accounts
                 WHERE level_0 = 'BS'
            ),
            entity_years AS (
                SELECT DISTINCT entity_prefix, fiscal_year
                  FROM scoped_accounts
            ),
            first_year AS (
                SELECT entity_prefix, MIN(fiscal_year) AS first_fy
                  FROM scoped_accounts
                 GROUP BY entity_prefix
            ),
            target AS (
                SELECT b.account_number_group,
                       y.fiscal_year,
                       b.entity_prefix
                  FROM bs_accounts b
                  JOIN entity_years y ON y.entity_prefix = b.entity_prefix
                  JOIN first_year f   ON f.entity_prefix = b.entity_prefix
                 WHERE y.fiscal_year > f.first_fy
            )
            SELECT t.entity_prefix,
                   t.account_number_group,
                   t.fiscal_year,
                   COALESCE(SUM(l.amount), 0) AS ob_amount
              FROM target t
              JOIN fact_gl_line l
                ON l.account_number_group = t.account_number_group
               AND l.fiscal_year < t.fiscal_year
               AND l.source_system <> :src
             GROUP BY t.entity_prefix, t.account_number_group, t.fiscal_year
            """
        ),
        params,
    ).fetchall()

    out: list[dict] = []
    for r in rows:
        amount = float(r[3] or 0.0)
        if abs(amount) <= _ZERO_TOL:
            continue
        out.append(
            {
                "entity_prefix": str(r[0]),
                "account_number_group": str(r[1]),
                "fiscal_year": int(r[2]),
                "ob_amount": amount,
            }
        )
    return out


def _synthetic_bid(jegn: str, fiscal_year: int) -> int:
    """Deterministic, collision-free synthetic booking_line_id in the reserved range.

    The full deterministic ``jegn`` (``EE 8 YY AAAAAA``) uniquely identifies the
    (entity, fy, account) target, so the id is derived from ALL of its digits — NOT
    just the trailing 8 — to avoid cross-entity collisions when two entities share
    the same account tail + year (their jegns differ only in the leading ``EE``).
    The hash is kept inside a 12-digit band so the value stays well above real
    1-based loaded ids and inside the reserved synthetic base.
    """
    h = int(re.sub(r"\D", "", jegn) or "0") % 1_000_000_000_000
    return _SYNTHETIC_BID_BASE + h


def _mode_carry_forward(session: Session, scope) -> dict:
    """Synthesize per-(entity, BS account) opening balances for years after the first.

    Steps (all in the caller's open transaction):
      1. DELETE prior synthetic carry-forward OB rows in scope (idempotency).
      2. OB = Σ amount over real rows of (entity, account), fiscal_year ≤ fy-1, for
         every BS account / fy > first_year(entity).  See ``_compute_carry_forward``.
      3. Insert ONE single-line synthetic journal entry per non-zero carry-forward
         (fiscal_period=0, entry_type='opening_balance', source_system marker,
         posting_date = Jan-1 of fy, deterministic group number + booking_line_id).

    Only ``dim_gl_account.level_0 = 'BS'`` accounts are carried forward.  PL
    accounts reset each year and are excluded.
    """
    import datetime

    year_set = _scope_year_set(scope)
    prefix_set = _scope_prefix_set(scope)
    deleted = _delete_synthetic_ob(session, year_set, prefix_set)

    carries = _compute_carry_forward(session, prefix_set)
    if year_set is not None:
        carries = [c for c in carries if c["fiscal_year"] in year_set]

    entry_sql = text(
        """
        INSERT INTO fact_gl_entry
          (journal_entry_group_number, fiscal_year, fiscal_period, entry_type,
           posting_date, currency_code, header_note, source_system)
        VALUES (:jegn, :fy, :fp, :ot, :pd, 'EUR', :note, :src)
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
    note = "Synthetic carry-forward opening balance"
    inserted = 0
    for c in carries:
        fy = c["fiscal_year"]
        jegn = _synthetic_ob_jegn(c["entity_prefix"], fy, c["account_number_group"])
        session.execute(
            entry_sql,
            {
                "jegn": jegn, "fy": fy, "fp": OPENING_FISCAL_PERIOD,
                "ot": OPENING_ENTRY_TYPE, "pd": datetime.date(fy, 1, 1),
                "note": note, "src": SYNTHETIC_OB_SOURCE,
            },
        )
        res = session.execute(
            line_sql,
            {
                "jegn": jegn, "fy": fy, "bid": _synthetic_bid(jegn, fy),
                "ang": c["account_number_group"], "amt": c["ob_amount"],
                "note": note, "src": SYNTHETIC_OB_SOURCE,
            },
        )
        inserted += int(res.rowcount or 0)

    out = {
        "mode": "carry_forward",
        "synthetic_ob_deleted": deleted,
        "opening_balance_rows": inserted,
    }
    logger.info("opening_balance carry_forward: %s", out)
    return out


# --------------------------------------------------------------------------- #
# Public entry point (called from rebuild stage 4)
# --------------------------------------------------------------------------- #
def synthesize_opening_balances(session: Session, scope, mode: str) -> dict:
    """Stage 4 dispatcher: synthesize / tag / skip opening balances by ``mode``.

    Parameters
    ----------
    session : Session
        Open SQLAlchemy session (caller owns the transaction / commit).
    scope : RebuildScope | None | (prefixes, years)
        Rebuild scope; only ``years`` restricts the synthesized target years.
    mode : 'in_data' | 'file' | 'carry_forward'
        Opening-balance acquisition mode (``settings.opening_balance_mode``).

    Returns
    -------
    dict
        Per-mode result summary (row counts).
    """
    if mode not in VALID_MODES:
        raise ValueError(
            f"opening_balance_mode must be one of {sorted(VALID_MODES)}, got {mode!r}"
        )
    if mode == "in_data":
        return _mode_in_data(session, scope)
    if mode == "file":
        return _mode_file(session, scope)
    return _mode_carry_forward(session, scope)
