"""Retained-earnings roll (year-end close) — OPTIONAL rebuild stage (v5 Phase 8).

This module owns rebuild **stage 5b** ("retained-earnings roll").  It is invoked
from ``etl.rebuild.rebuild_project`` and gated by the per-project config
``retained_earnings_roll.enabled`` (falling back to the global
``settings.retained_earnings_roll_enabled`` default = **OFF**).

WHY THIS EXISTS
───────────────
On a ``carry_forward`` + ``pl_sum`` dataset the balance-sheet imbalance surfaced
by ``fin_compat_bs.bs_imbalance_from_grains`` GROWS every year by exactly the
prior year's net profit: the completed year's P&L result lands in assets but is
never rolled into an equity **retained-earnings** account, so Assets ≠ Equity &
liabilities and the gap compounds.  This stage books that roll.

FORMULA / SIGN (PROVEN on finssentials_v5_e2e — see docstring PROOF below)
──────────────────────────────────────────────────────────────────────────
Canonical stored sign: ``fact_gl_line.amount`` ``+`` = debit (assets), ``−`` =
credit (equity & liabilities) — identical to ``fin_compat_bs_sql.py``.

Per-entity net profit STORED (credit) value for a fiscal year ``y`` (from the
P&L, exactly like ``etl.net_profit``)::

    NP_stored[e, y] = Σ amount  over level_0='PL' rows of (e, y)
                    = −NP_presented[e, y]           (negative for a profit)

For the entity's target retained-earnings account ``A_e`` and fiscal year ``N``,
the roll booked as an **opening balance** (fiscal_period=0, posting_date Jan-1 of
``N``) is the cumulative prior-year result plus an optional pre-first-year seed::

    RE_roll_stored[e, N] = opening_stored[e] + Σ_{first ≤ y < N} NP_stored[e, y]

  * first fiscal year of the entity → ``RE_roll = opening_stored[e]`` (the sum is
    empty); only booked when an ``opening`` value is configured.
  * later years → ``opening_stored[e]`` plus the cumulative prior stored result.

``opening_stored[e]`` is the pre-first-year accumulated retained earnings in the
canonical STORED sign (credit = **negative**).  To fully balance an entity it
equals ``−(FY-first imbalance residual)`` — e.g. entity 01's residual is
``+602,468.75`` so ``opening_stored['01'] = −602,468.75`` drives its imbalance to 0.

Because each year gets its OWN full-cumulative opening balance (NOT an incremental
delta) and the FY-scoped BS grain (``_bal_amount_expr_fy``) reads only that year's
Jan-1 opening balance, the rolls never stack across years.

WHY AN OPENING BALANCE (not a movement)
───────────────────────────────────────
The exit-readiness snapshot / consolidation views are FISCAL-YEAR-SCOPED: a column
for year ``N`` = (that year's Jan-1 opening balance) + (that year's movements).
Booking each year's full cumulative roll as a Jan-1 opening balance therefore
adds exactly ``RE_roll_stored[e, N]`` to the FY-``N`` column and nothing carries
into ``N+1`` on its own.  A movement row would instead accumulate in the lifetime
grain and stack.  Opening rows are single-sided (one equity credit, no balancing
counter-line) exactly like carry-forward OBs / the source Jan-1 rows, so they are
exempt from B1/B2/B3 via ``etl.checks._opening_exempt_mask`` (fiscal_period=0).

NO DOUBLE-COUNT (carry-forward interaction — CRITICAL)
──────────────────────────────────────────────────────
These rows are REAL GL rows on a BS equity account.  The carry-forward stage's
cumulative sum (``etl.opening_balance._compute_carry_forward``) previously
excluded only its OWN marker, so it would have summed these rows into the next
year's carry-forward OB and cascaded them.  ``_compute_carry_forward`` now
EXCLUDES ``SYNTHETIC_RE_SOURCE`` as well (both synthetic markers), so the roll
never feeds carry-forward.  No interaction with ``etl.net_profit`` (that sums
level_0='PL' rows; the roll is on a BS account with a DISTINCT level_3='Retained
earnings' target, and uses a distinct source marker / jegn discriminator / bid
band).

The roll rows carry ``entry_type='opening_balance'`` (NOT ``'net_profit'``), so
they are INCLUDED in the BS cumulative-balance grain — counted ONCE as opening
equity, which is exactly what drives the imbalance to a constant / zero.

MARKERS (distinct from every other synthetic band)
───────────────────────────────────────────────────
  * ``source_system = 'synthetic_retained_earnings'`` (idempotency + carry-forward
    exclusion).
  * ``journal_entry_group_number = EE 6 YY AAAAAA`` — the ``6`` discriminator is
    distinct from load ``9`` / carry-forward OB ``8`` / net-profit ``7``.
  * ``booking_line_id`` reserved band ``600_000_000_000`` (kept strictly below the
    net-profit ``700_000_000_000`` and carry-forward ``900_000_000_000`` bands).

Idempotent: a re-run first DELETEs rows carrying the marker (in scope) then
re-inserts, so the amounts recompute identically from the real ledger every time.
"""
from __future__ import annotations

import datetime
import logging
import re

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: Stable marker on ``source_system`` identifying synthetic retained-earnings roll
#: rows.  Distinct from the carry-forward OB marker and the net-profit marker.
SYNTHETIC_RE_SOURCE = "synthetic_retained_earnings"

#: entry_type / fiscal_period tag — an OPENING balance (single-sided, exempt).
RE_ENTRY_TYPE = "opening_balance"
RE_FISCAL_PERIOD = 0

#: Reserved booking_line_id band — strictly below net-profit (700e9) + OB (900e9).
_SYNTHETIC_BID_BASE = 600_000_000_000

#: Default auto-resolve heuristic: the German "profit/loss carried forward" label.
_DEFAULT_RE_ACCOUNT_NAME = "Gewinn-/Verlustvortrag"
_DEFAULT_RE_LEVEL_3 = "Retained earnings"

#: |roll| at or below this is treated as nil (no row emitted).
_ZERO_TOL = 1e-6


# --------------------------------------------------------------------------- #
# Deterministic keys
# --------------------------------------------------------------------------- #
def _synthetic_re_jegn(entity_prefix: str, fiscal_year: int, account_number_group: str) -> str:
    """Deterministic 12-char journal_entry_group_number ``<EE>6<YY><AAAAAA>``."""
    ee = re.sub(r"\D", "", str(entity_prefix)).zfill(2)[-2:]
    yy = str(int(fiscal_year)).zfill(4)[-2:]
    acct = re.sub(r"\D", "", str(account_number_group))
    acct_tail = acct[2:] if len(acct) > 2 else acct
    a6 = acct_tail.zfill(6)[-6:]
    return f"{ee}6{yy}{a6}"[:12]


def _synthetic_bid(entity_prefix: str, fiscal_year: int, account_number_group: str) -> int:
    """Collision-free synthetic booking_line_id inside the reserved 600e9 band.

    ``base + EE·10^9 + YY·10^7 + (account tail mod 10^7)`` — stays strictly below
    the net-profit 700e9 band (max ≈ 6.999e11) and is distinct per (entity, year,
    account).
    """
    ee = int(re.sub(r"\D", "", str(entity_prefix)) or "0") % 100
    yy = int(fiscal_year) % 100
    acct = re.sub(r"\D", "", str(account_number_group))
    acct_tail = acct[2:] if len(acct) > 2 else acct
    a = int(acct_tail or "0") % 10_000_000
    return _SYNTHETIC_BID_BASE + ee * 1_000_000_000 + yy * 10_000_000 + a


# --------------------------------------------------------------------------- #
# Scope helpers (mirror etl.net_profit / etl.opening_balance)
# --------------------------------------------------------------------------- #
def _scope_year_set(scope) -> set[int] | None:
    years = getattr(scope, "years", None) or []
    return {int(y) for y in years} if years else None


def _scope_prefix_set(scope) -> set[str] | None:
    prefixes = getattr(scope, "prefixes", None) or []
    cleaned = {str(p).strip()[:2] for p in prefixes if str(p).strip()}
    return cleaned or None


def _delete_synthetic_re(
    session: Session,
    year_set: set[int] | None,
    prefix_set: set[str] | None = None,
) -> int:
    """Idempotency: remove previously-synthesized retained-earnings rows in scope."""
    params: dict = {"src": SYNTHETIC_RE_SOURCE}
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


def cleanup_retained_earnings(session: Session, scope) -> dict:
    """Delete-by-marker cleanup for the DISABLED path (idempotent).

    Removes any previously-synthesized retained-earnings rows in scope but writes
    nothing.  On a database that never carried the roll this deletes 0 rows — a
    true no-op — so ``enabled=False`` stays byte-identical to today (golden parity).
    Disabling AFTER an enable therefore fully reverts the ledger to the baseline.
    """
    deleted = _delete_synthetic_re(
        session, _scope_year_set(scope), _scope_prefix_set(scope)
    )
    return {"retained_earnings_deleted": deleted, "retained_earnings_rows": 0,
            "enabled": False, "noop": True}


# --------------------------------------------------------------------------- #
# Resolution + computation
# --------------------------------------------------------------------------- #
def _auto_resolve_accounts(
    session: Session, prefix_set: set[str] | None = None
) -> dict[str, str]:
    """Auto-resolve the retained-earnings target account per entity_prefix.

    Deterministic ``MIN(account_number_group)`` over
    ``level_0='BS' AND level_3='Retained earnings' AND account_name=
    'Gewinn-/Verlustvortrag'``.  Returns ``{entity_prefix: account_number_group}``.
    """
    params: dict = {"lvl3": _DEFAULT_RE_LEVEL_3, "nm": _DEFAULT_RE_ACCOUNT_NAME}
    pfx_clause = ""
    if prefix_set:
        pph = ", ".join(f":pfx{i}" for i in range(len(prefix_set)))
        for i, p in enumerate(sorted(prefix_set)):
            params[f"pfx{i}"] = p
        pfx_clause = f" AND entity_prefix IN ({pph})"
    rows = session.execute(
        text(
            f"""
            SELECT entity_prefix, MIN(account_number_group) AS ang
              FROM dim_gl_account
             WHERE level_0 = 'BS'
               AND level_3 = :lvl3
               AND account_name = :nm{pfx_clause}
             GROUP BY entity_prefix
            """
        ),
        params,
    ).fetchall()
    return {str(r[0]): str(r[1]) for r in rows}


def _resolve_target_accounts(
    session: Session,
    accounts_cfg: dict[str, str] | None,
    prefix_set: set[str] | None,
) -> dict[str, str]:
    """Merge configured account overrides onto the auto-resolved defaults.

    A configured ``accounts[entity_prefix]`` wins; otherwise the auto-resolved
    ``Gewinn-/Verlustvortrag`` account is used.  Entities with neither are simply
    absent from the result (skipped + warned by the caller).
    """
    resolved = _auto_resolve_accounts(session, prefix_set)
    if accounts_cfg:
        for ep, ang in accounts_cfg.items():
            ep2 = str(ep).strip()[:2]
            ang2 = str(ang).strip()
            if ep2 and ang2:
                resolved[ep2] = ang2
    if prefix_set:
        resolved = {k: v for k, v in resolved.items() if k in prefix_set}
    return resolved


def _entity_years(session: Session, prefix_set: set[str] | None) -> dict[str, list[int]]:
    """Sorted distinct fiscal years per entity_prefix (from dim_gl_account)."""
    params: dict = {}
    pfx_clause = ""
    if prefix_set:
        pph = ", ".join(f":pfx{i}" for i in range(len(prefix_set)))
        for i, p in enumerate(sorted(prefix_set)):
            params[f"pfx{i}"] = p
        pfx_clause = f" WHERE entity_prefix IN ({pph})"
    rows = session.execute(
        text(
            f"SELECT DISTINCT entity_prefix, fiscal_year FROM dim_gl_account{pfx_clause}"
        ),
        params,
    ).fetchall()
    out: dict[str, list[int]] = {}
    for ep, fy in rows:
        out.setdefault(str(ep), []).append(int(fy))
    for ep in out:
        out[ep] = sorted(set(out[ep]))
    return out


def _net_profit_stored(
    session: Session, prefix_set: set[str] | None
) -> dict[tuple[str, int], float]:
    """NP_stored[e, y] = Σ amount over level_0='PL' rows of (e, y) (credit value).

    Synthetic markers are excluded defensively (they are on BS accounts, so they
    never enter a level_0='PL' sum, but the guard keeps a re-run stable).
    """
    params: dict = {
        "s_re": SYNTHETIC_RE_SOURCE,
        "s_np": "synthetic_net_profit",
        "s_ob": "synthetic_carry_forward_ob",
    }
    pfx_clause = ""
    if prefix_set:
        pph = ", ".join(f":pfx{i}" for i in range(len(prefix_set)))
        for i, p in enumerate(sorted(prefix_set)):
            params[f"pfx{i}"] = p
        pfx_clause = f" AND a.entity_prefix IN ({pph})"
    rows = session.execute(
        text(
            f"""
            SELECT a.entity_prefix, l.fiscal_year, COALESCE(SUM(l.amount), 0)
              FROM fact_gl_line l
              JOIN dim_gl_account a
                ON a.account_number_group = l.account_number_group
               AND a.fiscal_year = l.fiscal_year
             WHERE a.level_0 = 'PL'
               AND COALESCE(l.source_system, '') NOT IN (:s_re, :s_np, :s_ob){pfx_clause}
             GROUP BY a.entity_prefix, l.fiscal_year
            """
        ),
        params,
    ).fetchall()
    return {(str(r[0]), int(r[1])): float(r[2] or 0.0) for r in rows}


def _ensure_dim_account_year(session: Session, ang: str, fiscal_year: int) -> bool:
    """Guarantee a dim_gl_account row exists for (account, fiscal_year) (FK safety).

    Clones the account's OWN most-recent classification row into the missing year
    (additive, never mutates an existing row) — the SAME pattern as the
    carry-forward account fill.  Returns True when the (account, year) is present
    afterwards, False when the account has no dim row at all (caller skips).
    """
    exists = session.execute(
        text(
            "SELECT 1 FROM dim_gl_account "
            "WHERE account_number_group = :a AND fiscal_year = :y"
        ),
        {"a": ang, "y": fiscal_year},
    ).first()
    if exists is not None:
        return True

    src = session.execute(
        text(
            "SELECT * FROM dim_gl_account WHERE account_number_group = :a "
            "ORDER BY fiscal_year DESC LIMIT 1"
        ),
        {"a": ang},
    ).mappings().first()
    if src is None:
        return False

    generated_cols: set[str] = set()
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        generated_cols = {
            str(r[0])
            for r in session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'dim_gl_account' AND is_generated = 'ALWAYS'"
                )
            ).fetchall()
        }
    row = {k: v for k, v in dict(src).items() if k not in generated_cols}
    row["fiscal_year"] = fiscal_year
    if "source_system" in row:
        row["source_system"] = "retained_earnings_account_fill"
    cols = ", ".join(row.keys())
    binds = ", ".join(f":{k}" for k in row.keys())
    session.execute(
        text(
            f"INSERT INTO dim_gl_account ({cols}) VALUES ({binds}) "
            "ON CONFLICT (account_number_group, fiscal_year) DO NOTHING"
        ),
        row,
    )
    return True


# --------------------------------------------------------------------------- #
# Public entry point (called from rebuild stage 5b)
# --------------------------------------------------------------------------- #
def synthesize_retained_earnings(
    session: Session,
    scope,
    *,
    accounts: dict[str, str] | None = None,
    opening: dict[str, float] | None = None,
) -> dict:
    """Stage 5b: roll each completed FY's P&L result into retained-earnings equity.

    Only invoked when the retained-earnings roll is ENABLED.  Idempotent: deletes
    prior synthetic RE rows in scope, then re-inserts the cumulative-prior-result
    opening balance per (entity, year > first) plus an optional pre-first-year
    ``opening`` seed on the first year.

    Parameters
    ----------
    session : Session
        Open SQLAlchemy session (caller owns the transaction / commit).
    scope : RebuildScope | object with ``.years`` / ``.prefixes``
        Rebuild scope; ``years`` restricts the synthesized target years,
        ``prefixes`` restricts the entities.
    accounts : {entity_prefix: account_number_group} | None
        Per-entity target account overrides; absent entities auto-resolve to the
        ``Gewinn-/Verlustvortrag`` retained-earnings account.
    opening : {entity_prefix: number} | None
        Optional pre-first-year retained earnings in canonical STORED sign
        (credit = negative).  Absent → not booked (first year emits no row).
    """
    year_set = _scope_year_set(scope)
    prefix_set = _scope_prefix_set(scope)
    deleted = _delete_synthetic_re(session, year_set, prefix_set)

    targets = _resolve_target_accounts(session, accounts, prefix_set)
    entity_years = _entity_years(session, prefix_set)
    np_stored = _net_profit_stored(session, prefix_set)
    opening = {str(k).strip()[:2]: float(v) for k, v in (opening or {}).items()}

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
    note = "Synthetic retained-earnings roll (year-end close)"

    inserted = 0
    skipped_no_account: list[str] = []
    entities = sorted(set(entity_years) | set(targets))
    for ep in entities:
        ang = targets.get(ep)
        years = entity_years.get(ep) or []
        if not years:
            continue
        if ang is None:
            skipped_no_account.append(ep)
            logger.warning(
                "retained_earnings: no target account for entity %s; skipped", ep
            )
            continue

        open_e = opening.get(ep, 0.0)
        cum_prior_np = 0.0  # Σ_{y < N} NP_stored (rolling)
        for N in years:
            roll = open_e + cum_prior_np  # first year: cum_prior_np == 0 → == opening
            cum_prior_np += np_stored.get((ep, N), 0.0)
            if year_set is not None and N not in year_set:
                continue
            if abs(roll) <= _ZERO_TOL:
                continue
            if not _ensure_dim_account_year(session, ang, N):
                logger.warning(
                    "retained_earnings: account %s missing dim row for %s; skipped",
                    ang, N,
                )
                continue
            jegn = _synthetic_re_jegn(ep, N, ang)
            session.execute(
                entry_sql,
                {
                    "jegn": jegn, "fy": N, "fp": RE_FISCAL_PERIOD, "et": RE_ENTRY_TYPE,
                    "pd": datetime.date(N, 1, 1), "note": note, "src": SYNTHETIC_RE_SOURCE,
                },
            )
            res = session.execute(
                line_sql,
                {
                    "jegn": jegn, "fy": N, "bid": _synthetic_bid(ep, N, ang),
                    "ang": ang, "amt": roll, "note": note, "src": SYNTHETIC_RE_SOURCE,
                },
            )
            inserted += int(res.rowcount or 0)

    out = {
        "retained_earnings_deleted": deleted,
        "retained_earnings_rows": inserted,
        "skipped_no_account": skipped_no_account,
        "resolved_accounts": targets,
    }
    logger.info("retained_earnings roll: %s", out)
    return out
