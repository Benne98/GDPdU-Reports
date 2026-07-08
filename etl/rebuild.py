"""Deterministic, idempotent rebuild orchestrator (reporting-v2, Phase 1).

``rebuild_project(session, scope, mode='full')`` runs the post-load derivation
chain that reproduces the CURRENT reporting behaviour.  In Phase 1 it folds in the
exact logic that ``backend/scripts/derive_facts.py`` performed manually, so the
output is behaviour-preserving (proven by the golden-equivalence gate).

STAGE LIST (Phase 1)
────────────────────
  1. classification refresh   — (Phase 4) replay dim_project_coa_override onto
                                dim_gl_account for the project's scope so CoA-editor
                                edits survive a data reload.  NO-OP when no override
                                rows exist (preserves golden equivalence).
                                etl.project_coa_override.replay_project_overrides.
  2. partner backfill         — etl.derive.backfill_partner_links_on_gl_lines
                                (receivable→revenue, payable→material on stored
                                fact_gl_line).
  3. derived facts            — fact_sales / fact_com / fact_ar / fact_ap via
                                etl.derive_facts_sql (DELETE + INSERT).
  4. opening balances         — etl.opening_balance.synthesize_opening_balances,
                                gated by settings.opening_balance_mode
                                ('in_data' default no-op | 'file' tag | 'carry_forward').
  5. net profit               — etl.net_profit.synthesize_net_profit, gated by
                                settings.bs_net_profit_source ('report_inject'
                                default no-op | 'gl_rows' synthesize balancing
                                equity bookings).
  6. structure / recon refresh — idempotent re-seed of the BS report structure
                                from the live GL hierarchy (seed_bs_structure);
                                recon mapping re-seed is best-effort (skipped when
                                its source files are absent — matches current
                                behaviour, where recon is seeded out-of-band).

IDEMPOTENCY / TRANSACTION
─────────────────────────
The whole chain runs in the caller's open transaction and commits once at the end
(or rolls back on error).  Each stage is DELETE+INSERT or upsert, so running the
rebuild twice yields identical tables.

SCOPE
─────
``scope`` may be ``None`` (full re-derive of all facts — the Phase-1 / derive_facts
behaviour) or a ``(prefixes, years)`` tuple.  In Phase 1 the derived-fact SQL is
global (matches derive_facts.py); the scope argument is recorded and passed through
for future incremental use but does not change the global DELETE in this phase.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

VALID_MODES = frozenset({"full", "incremental"})


@dataclass
class RebuildScope:
    """Entity/year scope for a rebuild.  Empty/None => full (global) rebuild."""

    prefixes: list[str] = field(default_factory=list)
    years: list[int] = field(default_factory=list)

    @property
    def is_full(self) -> bool:
        return not self.prefixes or not self.years


def _coerce_scope(scope) -> RebuildScope:
    if scope is None:
        return RebuildScope()
    if isinstance(scope, RebuildScope):
        return scope
    if isinstance(scope, tuple) and len(scope) == 2:
        prefixes, years = scope
        return RebuildScope(
            prefixes=list(prefixes or []),
            years=[int(y) for y in (years or [])],
        )
    raise TypeError(f"unsupported scope {scope!r}; pass None, RebuildScope, or (prefixes, years)")


# --------------------------------------------------------------------------- #
# Stages
# --------------------------------------------------------------------------- #
def _stage_classification_refresh(session: Session, scope: RebuildScope) -> dict:
    """Phase 4: replay the per-project CoA override onto dim_gl_account.

    The CoA editor persists hierarchy/sort edits into dim_gl_account directly; they
    are ALSO captured into ``dim_project_coa_override`` and replayed here at the
    start of every rebuild so editor edits survive a data reload (which re-upserts
    dim_gl_account from the upload).

    NO-OP when no override rows exist for the project's scope — preserving the
    golden live-vs-v2 equivalence (Phase 4 gate).  The override table being absent
    (partially-migrated DB / pure-ETL test schema) is also treated as a no-op.
    """
    from etl.project_coa_override import replay_project_overrides

    return replay_project_overrides(session, scope)


DEFAULT_ACCOUNT_MAPPING_MODE = "library"


def _stage_account_library_fill(
    session: Session, scope: RebuildScope, mode_override: Optional[str] = None
) -> dict:
    """Fill MISSING (account, fiscal_year) dim_gl_account rows from the account library.

    When the project's mapping file only covers some fiscal years, the same account
    in OTHER years has no dim_gl_account row → it is unclassified.  This stage
    INSERTs a row for every posted (account, fy) that has none, resolved from
    ``ovr_account_mapping`` (pin) else the most-frequent hierarchy for the account's
    ``account_name`` in ``lib_account_mapping`` (``etl.account_fill``).

    GATE — ``account_mapping_mode``:
      'library' (DEFAULT) -> run the fill.
      'exclusive'         -> SKIP (only the provided per-(account, year) mapping is
                             used; missing years stay unmapped).

    ADDITIVE / GOLDEN: the fill NEVER mutates an existing dim_gl_account row — it
    only INSERTs missing ones.  On v2/live (mapping covers every year, FK
    fact_gl_line->dim_gl_account guarantees no posted key is missing) it inserts 0
    rows, so ``compare live v2`` stays EQUIVALENT.  The library/override tables
    being absent (partially-migrated / pure-ETL test schema) is a no-op.
    """
    mode = mode_override or DEFAULT_ACCOUNT_MAPPING_MODE
    if mode == "exclusive":
        return {"account_mapping_mode": "exclusive", "filled": 0, "skipped": True}
    try:
        from etl.account_fill import fill_missing_account_rows

        res = fill_missing_account_rows(session, scope)
        res["account_mapping_mode"] = mode
        return res
    except Exception as exc:  # noqa: BLE001 — absent tables / partial schema => no-op
        logger.warning("rebuild: account library fill skipped (%s)", exc)
        return {"account_mapping_mode": mode, "filled": 0, "skipped": True, "error": str(exc)}


def _stage_partner_backfill(session: Session, scope: RebuildScope) -> dict:
    """Method-A partner backfill on stored fact_gl_line (receivable→revenue, payable→material)."""
    from etl.derive import backfill_partner_links_on_gl_lines

    return backfill_partner_links_on_gl_lines(session)


def _stage_derived_facts(session: Session, scope: RebuildScope) -> dict:
    """Derive fact_sales / fact_com / fact_ar / fact_ap (DELETE + INSERT, global)."""
    from etl.derive_facts_sql import derive_all_facts

    return derive_all_facts(session)


def _stage_opening_balances(
    session: Session, scope: RebuildScope, mode_override: Optional[str] = None
) -> dict:
    """Phase 2: synthesize / tag / skip opening balances, gated by config flag.

    Dispatches to ``etl.opening_balance.synthesize_opening_balances`` using
    ``settings.opening_balance_mode`` (or *mode_override* when a per-project config
    resolved the flag — Phase 7):
      'in_data'       -> no-op (default; opening balances already in the loaded GL)
      'file'          -> tag separately-loaded first-year opening rows
      'carry_forward' -> synthesize per-(entity, BS account) prior-year closing
    """
    from etl.opening_balance import synthesize_opening_balances

    mode = mode_override or _opening_balance_mode()
    return synthesize_opening_balances(session, scope, mode)


def _opening_balance_mode() -> str:
    """Read OPENING_BALANCE_MODE from settings, defaulting to legacy 'in_data'.

    Falls back to the env var / 'in_data' if the backend settings module is not
    importable (e.g. pure-ETL test contexts), so the ETL stays self-contained.
    """
    try:
        import sys
        from pathlib import Path

        _backend = Path(__file__).resolve().parent.parent / "backend"
        if str(_backend) not in sys.path:
            sys.path.insert(0, str(_backend))
        from app.config import settings  # type: ignore

        return settings.opening_balance_mode
    except Exception:  # noqa: BLE001
        import os

        return os.getenv("OPENING_BALANCE_MODE", "in_data")


def _stage_net_profit(
    session: Session, scope: RebuildScope, source_override: Optional[str] = None
) -> dict:
    """Phase 3: synthetic net-profit GL bookings, gated by config flag.

    Dispatches on ``settings.bs_net_profit_source`` (or *source_override* when a
    per-project config resolved the flag — Phase 7):
      'report_inject' -> NO-OP (default; net profit stays a virtual report-layer
                         injection in fin_compat_bs.py — live 8010 / legacy 5176
                         behaviour, so the golden equivalence stays intact).
      'gl_rows'       -> synthesize real single-sided net-profit equity bookings so
                         the ledger itself balances per FY (etl.net_profit).  The
                         BS presentation path is UNCHANGED (the rows are excluded
                         from the BS grain), hence the displayed BS is identical.
    """
    source = source_override or _bs_net_profit_source()
    if source not in _VALID_NP_SOURCES:
        raise ValueError(
            f"bs_net_profit_source must be one of {sorted(_VALID_NP_SOURCES)}, got {source!r}"
        )
    if source == "gl_rows":
        from etl.net_profit import synthesize_net_profit

        return synthesize_net_profit(session, scope)
    return {"source": "report_inject", "net_profit_rows": 0, "noop": True}


_VALID_NP_SOURCES = frozenset({"report_inject", "gl_rows"})


def _bs_net_profit_source() -> str:
    """Read BS_NET_PROFIT_SOURCE from settings, defaulting to legacy 'report_inject'.

    Falls back to the env var / 'report_inject' if the backend settings module is
    not importable (e.g. pure-ETL test contexts), mirroring ``_opening_balance_mode``
    so the ETL stays self-contained.
    """
    try:
        import sys
        from pathlib import Path

        _backend = Path(__file__).resolve().parent.parent / "backend"
        if str(_backend) not in sys.path:
            sys.path.insert(0, str(_backend))
        from app.config import settings  # type: ignore

        return settings.bs_net_profit_source
    except Exception:  # noqa: BLE001
        import os

        return os.getenv("BS_NET_PROFIT_SOURCE", "report_inject")


def _stage_structure_recon_refresh(session: Session, scope: RebuildScope) -> dict:
    """Idempotent re-derive of the BS + P&L report structure from the live GL hierarchy.

    Two independent re-derivations run here on every ``mode=='full'`` rebuild:

      * ``seed_bs_structure`` — delete-then-insert re-seed of the BS rows in
        ``dim_bs_structure`` from ``dim_gl_account`` (the BS structure has always been
        GL-derived, so it already tracks a CoA round-trip).
      * ``realign_pl_structure`` — re-pin each seeded P&L mapping row's grain filter to
        the SHALLOWEST grain level its category currently occupies (Prong A).  The P&L
        structure is seeded from a curated Excel and was NEVER re-derived, so after a
        CoA export/re-ingest round-trip its stale ``level_3`` filters matched nothing
        and the position rendered 0.  NO-OP when the CoA is unchanged (v5 parity).

    ERROR POLICY (behaviour change vs the old blanket try/except).  Only the recon
    MAPPING Excel being absent stays graceful (that file is seeded out-of-band).  A
    real DB error out of ``seed_bs_structure`` / ``realign_pl_structure`` PROPAGATES on
    ``mode=='full'`` — a broken report structure must NOT silently degrade to rows=0.
    """
    import sys
    from pathlib import Path

    _backend = Path(__file__).resolve().parent.parent / "backend"
    if str(_backend) not in sys.path:
        sys.path.insert(0, str(_backend))

    out: dict = {}
    try:
        # BS re-seed (delete-then-insert; commits internally, harmless in our txn).
        from scripts.seed_bs_structure import seed_bs_structure  # type: ignore

        out["bs_structure_rows"] = seed_bs_structure(session)

        # P&L grain-filter realign (Prong A): re-pin to the shallowest live grain.
        from scripts.realign_pl_structure import realign_pl_structure  # type: ignore

        out["pl_structure_realigned"] = realign_pl_structure(session, scope)
    except (ImportError, FileNotFoundError) as exc:
        # GRACEFUL — and ONLY here: the recon-mapping source / scripts module is
        # absent (legacy or pure-ETL test schema).  Matches historical behaviour;
        # the structure is simply not refreshed on such a DB.  A real DB error is
        # NOT caught here → it propagates (see docstring: a broken structure must not
        # silently degrade to rows=0 on a full rebuild).
        logger.warning("rebuild: structure refresh source absent (%s)", exc)
        out.setdefault("bs_structure_rows", 0)
        out.setdefault("pl_structure_realigned", 0)
        out["error"] = str(exc)

    return out


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def rebuild_project(
    session: Session,
    scope=None,
    mode: str = "full",
    *,
    project_id: Optional[str] = None,
    commit: bool = True,
) -> dict:
    """Run the deterministic rebuild chain in one transaction.

    Parameters
    ----------
    session : Session
        Open SQLAlchemy session (caller owns the connection lifecycle).
    scope : None | RebuildScope | (prefixes, years)
        Entity/year scope.  None => full (global) rebuild (Phase-1 behaviour).
    mode : 'full' | 'incremental'
        'incremental' skips classification + structure refresh (mapping-free monthly
        update); facts + partner backfill always run.  Defaults to 'full'.
    project_id : str | None
        When given (Phase 7), the project's persisted config drives the rebuild
        flags (opening_balance_mode, net_profit_source) so a wizard setup is reused
        on every update.  None => use the global ``settings.*`` defaults (LEGACY
        behaviour — preserves the golden equivalence).
    commit : bool
        Commit at the end (default).  Set False to let the caller manage the txn.

    Returns
    -------
    dict
        Per-stage result summary (row counts / link counts).
    """
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {sorted(VALID_MODES)}, got {mode!r}")

    # A legit deterministic rebuild can run long and briefly idle between heavy
    # stages; raise the idle-in-transaction ceiling LOCALLY (SET LOCAL, scoped to
    # this txn only) so the global idle reaper (db.engine) never interrupts it.
    session.execute(text("SELECT set_config('idle_in_transaction_session_timeout', '600000', true)"))

    sc = _coerce_scope(scope)
    summary: dict = {"mode": mode, "scope": {"prefixes": sc.prefixes, "years": sc.years}}

    # Phase 7: resolve per-project rebuild flags (None => settings.* defaults).
    ob_override: Optional[str] = None
    np_override: Optional[str] = None
    am_override: Optional[str] = None
    if project_id is not None:
        try:
            from etl.project_config import resolve_rebuild_flags

            flags = resolve_rebuild_flags(session, project_id)
            ob_override = flags["opening_balance_mode"]
            np_override = flags["net_profit_source"]
            am_override = flags.get("account_mapping_mode")
            summary["project_id"] = project_id
            summary["resolved_flags"] = flags
        except Exception as exc:  # noqa: BLE001 — config lookup must not break rebuild
            logger.warning("rebuild: project config lookup failed (%s); using settings defaults", exc)

    try:
        if mode == "full":
            summary["classification"] = _stage_classification_refresh(session, sc)
            # Account library fill: classify (account, fy) the mapping file missed.
            # Gated by account_mapping_mode ('library' default | 'exclusive' skip).
            # Additive (only INSERTs missing dim rows) — no-op on v2/live → golden-safe.
            if am_override is not None:
                summary["account_library_fill"] = _stage_account_library_fill(session, sc, am_override)
            else:
                summary["account_library_fill"] = _stage_account_library_fill(session, sc)

        summary["partner_backfill"] = _stage_partner_backfill(session, sc)
        summary["derived_facts"] = _stage_derived_facts(session, sc)
        # Pass the per-project flag overrides ONLY when resolved (project_id given),
        # so the default-path stage call keeps the historical (session, scope)
        # 2-arg signature that existing tests / callers rely on.
        if ob_override is not None:
            summary["opening_balances"] = _stage_opening_balances(session, sc, ob_override)
        else:
            summary["opening_balances"] = _stage_opening_balances(session, sc)
        if np_override is not None:
            summary["net_profit"] = _stage_net_profit(session, sc, np_override)
        else:
            summary["net_profit"] = _stage_net_profit(session, sc)

        if mode == "full":
            summary["structure_recon"] = _stage_structure_recon_refresh(session, sc)

        if commit:
            session.commit()
    except Exception:
        session.rollback()
        raise

    logger.info("rebuild_project(%s) summary: %s", mode, summary)
    return summary
