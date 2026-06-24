"""Position-level GL forensic builder + plain-English explanations (Phase 2).

This is the **positions** rework of :mod:`app.services.gl_forensic`.  The legacy
``build_forensic`` returns three *per-booking / per-account* lists; that is precise
but unreadable for a finance person who is not a statistician.  This module rolls
those same findings UP to the analysis **position** (level_3, then level_4 inside
it) so the reader sees, per position, *how many* unusual postings there are, *how
much money* they move, *how many distinct* unusual counter accounts are involved,
and the single *worst example* — plus a nested ``evidence`` list with the
contributing bookings/accounts, and an entity split.

It is **additive**: the legacy :func:`gl_forensic.build_forensic` is left intact
(it is still wired in production); the new :func:`build_forensic_positions` is
wired by Phase 4.  All the pure math lives in :mod:`gl_forensic` and is reused
here unchanged — no new sign rule or monetary KPI is introduced.

================================================================================
WHAT "ROLLED UP TO A POSITION" MEANS (CLAUDE.md rule #1 — descriptive, not a KPI)
================================================================================
The hierarchy is ``level_0 → level_2 → level_3 → level_4 → account → booking``.
A *position* is an L3 node (``(level_0, level_2, level_3)``) with an L4 breakdown
(``level_4`` inside it; blank → ``"(no L4)"`` — same convention as
:mod:`gl_hierarchy`).

(1) **Unexpected counter accounts** — each flagged booking (counter account
    ``new`` or ``rare``; see :func:`gl_forensic.classify_pair_novelty`) is assigned
    to its account's position.  Per position we report:

      * ``new_count`` / ``rare_count`` — counts of the two novelty kinds,
      * ``abs_amount_keur`` — ``Σ |amount_keur|`` of the flagged bookings,
      * ``distinct_counter_accounts`` — number of DISTINCT counter accounts seen,
      * ``worst_example`` — the single flagged booking with the largest
        ``|amount_keur|`` (deterministic tie-break),
      * ``evidence`` — the contributing bookings (bounded), and
      * ``entity_split`` — ``count`` + ``abs_amount_keur`` per ``entity_prefix``.

    No arithmetic beyond counting and ``Σ|amount|``; the novelty itself is the
    existing freq_pct rule.

(2) **"Other" positions** — the matching "Other"-token accounts are SUMMED into
    their position and ``cm / pm / delta / yoy / growth_pct`` are recomputed from
    the SUMMED balances (NOT averaged), reusing :func:`gl_forensic._growth_pct`:

        delta      = Σcm − Σpm
        yoy        = Σcm − Σpy_cm
        growth_pct = (Σcm − Σpm) / |Σpm| * 100        (0 when Σpm ≈ 0)

    Worked example: two Other accounts in one position, cm 30 / 10 (Σ 40), pm
    20 / 5 (Σ 25) → delta 15, growth_pct 15/25*100 = 60.0 %.

(3) **Suspicious texts** — inherently booking-level (a free-text note on one
    line), so they stay per-booking; we only ADD ``entity_prefix`` and a
    ``position`` label so the UI can group/sort by position for display.

EDGE CASES
----------
  * A flagged booking whose account is not in the level map (no hierarchy row)
    falls into the ``"(no position)"`` bucket so nothing is silently dropped.
  * ``distinct_counter_accounts`` counts the counter ``gl_account_id`` (falling
    back to the counter name) so the same counter booked many times counts once.
  * ``entity_split`` sums reconcile to the position totals
    (Σ split counts == position count; Σ split Σ|amount| == position Σ|amount|).
  * ``growth_pct`` never divides by zero (Σpm ≈ 0 → 0.0).
  * Evidence lists are bounded to :data:`MAX_EVIDENCE_ROWS` (largest |amount|
    first) so the payload stays finite.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.services.fin_compat_sql import resolve_entity_prefix
from app.services.gl_analysis_common import (
    AccountSeries,
    build_account_monthly_series,
    latest_anchor,
)
from app.config import settings
from app.services.gl_forensic import (
    ALGORITHM_VERSION,
    FREQ_THRESHOLD_DEFAULT,
    MAX_FORENSIC_PER_SECTION,
    MIN_FLAG_AMOUNT_KEUR_DEFAULT,
    _build_other_positions,
    _build_unexpected_counter_accounts,
    _build_suspicious_texts,
    _growth_pct,
    build_level_path,
    filter_benign_counters,
    material_account_groups,
)

logger = logging.getLogger(__name__)

#: Algorithm tag for the position-level payload (distinct from the per-booking one).
#: Phase 2 forensic-targeting (whole-word matching, materiality floor, tighter
#: "Other", Top-N caps + omitted_count, headline/bullets) → v2.
#: v3 (anomaly refinement): a benignity filter narrows the flagged unexpected-counter
#: rows BEFORE rollup — a deterministic heuristic (system/control counter accounts) plus
#: an optional, default-off LLM pass; ``meta.llm_used`` + ``meta.omitted_benign_count``
#: are added.  Bumped to v3 so the ``anomaly_analysis_snapshot`` cache invalidates.
POSITIONS_ALGORITHM_VERSION = "gl_forensic_positions_v3"

#: Bucket label for a flagged booking whose account has no hierarchy row, so it is
#: never silently dropped from the rollup.
NO_POSITION_BUCKET = "(no position)"

#: Per-node cap on the nested ``evidence`` list (largest |amount| first).
MAX_EVIDENCE_ROWS = 50

#: Phase 2 forensic-targeting: user-facing cap per section (top-N by |amount| /
#: severity).  Re-exported from :mod:`gl_forensic` for callers/tests that import it
#: from the positions module.
MAX_FORENSIC_PER_SECTION = MAX_FORENSIC_PER_SECTION

#: Phase 2 forensic-targeting: materiality floor (kEUR) applied to position rollups
#: and suspicious-text rows.  Re-exported from :mod:`gl_forensic`.
MIN_FLAG_AMOUNT_KEUR = MIN_FLAG_AMOUNT_KEUR_DEFAULT

_ROUND = 3


# ===========================================================================
# Plain-English explanation + per-column help (English; finance-person friendly)
# ===========================================================================
#: One short paragraph per section: HOW the finding arose, in language a finance
#: person (not a statistician) understands.  Kept here as a single constant block.
EXPLANATIONS: dict[str, str] = {
    "unexpected_counter_positions": (
        "We learned, from your whole booking history, which counter accounts each "
        "position normally books against. The postings below used a counter account "
        "that we have never seen for this position before (\"new\"), or that this "
        "position uses in fewer than 5% of its entries (\"rare\"). We rolled the "
        "individual postings up to the position so you can see, per position, how "
        "many unusual postings there are, how much money they move, how many "
        "different unusual counter accounts are involved, and the single biggest "
        "example. Open a position to see the contributing postings."
    ),
    "other_positions": (
        "These are catch-all \"Other / Miscellaneous / Sonstige\" positions. Such "
        "buckets are where unclear or one-off items tend to be parked, so a large or "
        "fast-growing balance here is worth a look. We summed all the accounts that "
        "belong to each Other position and recomputed the current balance, the "
        "month-over-month change and the year-over-year change from those summed "
        "figures. A position is shown when it is both sizeable and growing. Open a "
        "position to see which accounts make it up."
    ),
    "suspicious_texts": (
        "These are individual postings whose free-text note contains a word that "
        "often marks a manual, corrected, reversed, provisional or test entry (for "
        "example \"Storno\", \"Korrektur\", \"manual\", \"reversal\", \"adjust\"). "
        "Unlike the other two sections this one stays at the single-posting level, "
        "because the note belongs to one line. Each row shows which position the "
        "posting belongs to so you can scan them position by position."
    ),
}

#: One-line meaning per column, per section — drives a small "?"/legend in the UI.
COLUMNS_HELP: dict[str, dict[str, str]] = {
    "unexpected_counter_positions": {
        "position": "The financial-statement position (level 3) the postings belong to.",
        "level_4": "The sub-position (level 4) inside the position.",
        "new_count": "How many postings used a counter account never seen for this position.",
        "rare_count": "How many postings used a counter account this position rarely uses (under 5%).",
        "abs_amount_keur": "Total size of the unusual postings, in thousands of euros (absolute amounts).",
        "distinct_counter_accounts": "How many different unusual counter accounts are involved.",
        "worst_example": "The single largest unusual posting in this position.",
        "entity_split": "The same counts and amounts broken down per legal entity.",
        "evidence": "The individual postings behind the numbers (largest first).",
    },
    "other_positions": {
        "position": "The catch-all \"Other\" position (level 3).",
        "level_4": "The sub-position (level 4) inside the position.",
        "balance_cm_keur": "Current-month balance, summed over the position, in thousands of euros.",
        "balance_pm_keur": "Prior-month balance, summed over the position, in thousands of euros.",
        "delta_keur": "Change versus last month (current minus prior), in thousands of euros.",
        "yoy_keur": "Change versus the same month last year, in thousands of euros.",
        "growth_pct": "Month-over-month change as a percentage of last month's balance.",
        "matched_token": "The word in the account labels that marked this as an \"Other\" position.",
        "entity_split": "The summed balance and change broken down per legal entity.",
        "evidence": "The individual accounts that make up this position.",
    },
    "suspicious_texts": {
        "position": "The financial-statement position (level 3) the posting belongs to.",
        "account_name": "The account the posting was made to.",
        "posting_date": "The date the posting was booked.",
        "amount_keur": "Size of the posting, in thousands of euros.",
        "line_note": "The free-text note on the posting line.",
        "matched_keyword": "The word in the note that flagged this posting for review.",
        "entity_prefix": "The legal entity the posting belongs to.",
    },
}


# ===========================================================================
# Pure rollup helpers (DB-free — unit-tested directly)
# ===========================================================================
def _position_key(level_2: str, level_3: str, level_0: str) -> str:
    """Display label for a position: prefer level_3, then level_2, then level_0."""
    return (level_3 or "").strip() or (level_2 or "").strip() or (level_0 or "").strip() \
        or NO_POSITION_BUCKET


def _l4_label(level_4: Optional[str]) -> str:
    lvl4 = (level_4 or "").strip()
    return lvl4 or "(no L4)"


def _counter_identity(row: dict[str, Any]) -> str:
    """Stable identity for counting DISTINCT counter accounts in a position."""
    return (
        (row.get("counter_gl_account_id") or "").strip()
        or (row.get("counter_account_name") or "").strip()
        or (row.get("counter_account_number_group") or "").strip()
    )


def rollup_counter_positions(
    flagged: list[dict[str, Any]],
    *,
    max_evidence: int = MAX_EVIDENCE_ROWS,
    min_flag_amount_keur: float = MIN_FLAG_AMOUNT_KEUR,
) -> list[dict[str, Any]]:
    """Roll per-booking novelty findings up to L3/L4 positions (pure).

    ``flagged`` is the output of :func:`gl_forensic.classify_novelty` ENRICHED by
    the orchestrator with the booking's ``entity_prefix``, ``position``,
    ``level_2/level_3/level_4``, ``level_path`` and ``account_number_group`` (the
    novelty classifier itself does not carry hierarchy).  Each row also has the
    standard novelty fields (``novelty``, ``amount_keur``, ``counter_*`` …).

    Phase 2: only flags AT OR ABOVE the materiality floor ``min_flag_amount_keur``
    (``|amount_keur| >= floor``) are counted into a position — the long tail of tiny
    novelty postings does not inflate a position's counts or Σ|amount|.

    Grouping key is ``(level_0, level_2, level_3, level_4)`` so a position with an
    L4 breakdown produces one node per L4 (blank → ``"(no L4)"``).  Per node:

      * ``new_count`` / ``rare_count`` — novelty kind counts,
      * ``abs_amount_keur`` — ``Σ |amount_keur|``,
      * ``distinct_counter_accounts`` — DISTINCT counter identities,
      * ``worst_example`` — largest |amount_keur| (tie-break: booking_line_id),
      * ``evidence`` — contributing bookings, largest |amount| first, bounded,
      * ``entity_split`` — ``{entity_prefix: {count, abs_amount_keur}}``.

    Deterministic order: nodes sorted by ``abs_amount_keur`` desc, then position,
    then L4.  Empty input → ``[]``.
    """
    flagged = [
        r for r in flagged
        if abs(float(r.get("amount_keur") or 0.0)) >= min_flag_amount_keur
    ]
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for r in flagged:
        l0 = (r.get("level_0") or "").strip()
        l2 = (r.get("level_2") or "").strip()
        l3 = (r.get("level_3") or "").strip()
        l4 = _l4_label(r.get("level_4"))
        groups.setdefault((l0, l2, l3, l4), []).append(r)

    out: list[dict[str, Any]] = []
    for (l0, l2, l3, l4), rows in groups.items():
        new_count = sum(1 for r in rows if r.get("novelty") == "new")
        rare_count = sum(1 for r in rows if r.get("novelty") == "rare")
        abs_amount = sum(abs(float(r.get("amount_keur") or 0.0)) for r in rows)
        distinct_counters = {
            cid for r in rows if (cid := _counter_identity(r))
        }

        ordered = sorted(
            rows,
            key=lambda r: (
                -abs(float(r.get("amount_keur") or 0.0)),
                int(r.get("booking_line_id") or 0),
            ),
        )
        worst = ordered[0]
        worst_example = {
            "account_name": worst.get("account_name"),
            "gl_account_id": worst.get("gl_account_id"),
            "counter_account_name": worst.get("counter_account_name"),
            "counter_gl_account_id": worst.get("counter_gl_account_id"),
            "amount_keur": worst.get("amount_keur"),
            "posting_date": worst.get("posting_date"),
            "line_note": worst.get("line_note"),
            "freq_pct": worst.get("freq_pct"),
            "novelty": worst.get("novelty"),
            "entity_prefix": worst.get("entity_prefix"),
        }

        split: dict[str, dict[str, Any]] = {}
        for r in rows:
            ep = (r.get("entity_prefix") or "").strip()
            bucket = split.setdefault(ep, {"count": 0, "abs_amount_keur": 0.0})
            bucket["count"] += 1
            bucket["abs_amount_keur"] += abs(float(r.get("amount_keur") or 0.0))
        entity_split = {
            ep: {"count": b["count"],
                 "abs_amount_keur": round(b["abs_amount_keur"], _ROUND)}
            for ep, b in sorted(split.items())
        }

        evidence = [
            {
                "booking_line_id": r.get("booking_line_id"),
                "journal_entry_number": r.get("journal_entry_number"),
                "account_name": r.get("account_name"),
                "gl_account_id": r.get("gl_account_id"),
                "counter_account_name": r.get("counter_account_name"),
                "counter_gl_account_id": r.get("counter_gl_account_id"),
                "amount_keur": r.get("amount_keur"),
                "posting_date": r.get("posting_date"),
                "line_note": r.get("line_note"),
                "freq_pct": r.get("freq_pct"),
                "pair_freq": r.get("pair_freq"),
                "novelty": r.get("novelty"),
                "entity_prefix": r.get("entity_prefix"),
            }
            for r in ordered[:max_evidence]
        ]

        out.append({
            "position": _position_key(l2, l3, l0),
            "level_0": l0,
            "level_2": l2,
            "level_3": l3,
            "level_4": l4,
            "new_count": new_count,
            "rare_count": rare_count,
            "flag_count": new_count + rare_count,
            "abs_amount_keur": round(abs_amount, _ROUND),
            "distinct_counter_accounts": len(distinct_counters),
            "worst_example": worst_example,
            "entity_split": entity_split,
            "evidence": evidence,
        })

    out.sort(
        key=lambda n: (-n["abs_amount_keur"], n["position"], n["level_4"]),
    )
    return out


def rollup_other_positions(
    accounts: list[dict[str, Any]],
    *,
    max_evidence: int = MAX_EVIDENCE_ROWS,
) -> list[dict[str, Any]]:
    """Sum per-account "Other" findings into L3/L4 positions (pure).

    ``accounts`` is the output of :func:`gl_forensic._build_other_positions`
    ENRICHED by the orchestrator with ``entity_prefix``, ``level_0/level_2/
    level_3/level_4`` and the per-account balances (``balance_cm_keur``,
    ``balance_pm_keur``, ``balance_py_cm_keur``).  Per position node:

      * ``balance_cm_keur`` / ``balance_pm_keur`` — Σ over the position,
      * ``delta_keur`` = Σcm − Σpm,
      * ``yoy_keur``   = Σcm − Σpy_cm,
      * ``growth_pct`` = recomputed from the SUMMED cm/pm (NOT averaged),
      * ``matched_token`` — the first contributing account's token,
      * ``evidence`` — the contributing accounts, largest |cm| first, bounded,
      * ``entity_split`` — summed cm/delta per ``entity_prefix``.

    Deterministic order: nodes by ``|delta_keur|`` desc, then position, then L4.
    """
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for r in accounts:
        l0 = (r.get("level_0") or "").strip()
        l2 = (r.get("level_2") or "").strip()
        l3 = (r.get("level_3") or "").strip()
        l4 = _l4_label(r.get("level_4"))
        groups.setdefault((l0, l2, l3, l4), []).append(r)

    out: list[dict[str, Any]] = []
    for (l0, l2, l3, l4), rows in groups.items():
        sum_cm = sum(float(r.get("balance_cm_keur") or 0.0) for r in rows)
        sum_pm = sum(float(r.get("balance_pm_keur") or 0.0) for r in rows)
        sum_py = sum(float(r.get("balance_py_cm_keur") or 0.0) for r in rows)
        delta = sum_cm - sum_pm
        yoy = sum_cm - sum_py
        growth_pct = _growth_pct(sum_cm, sum_pm)

        split: dict[str, dict[str, Any]] = {}
        for r in rows:
            ep = (r.get("entity_prefix") or "").strip()
            cm = float(r.get("balance_cm_keur") or 0.0)
            pm = float(r.get("balance_pm_keur") or 0.0)
            bucket = split.setdefault(ep, {"cm": 0.0, "pm": 0.0})
            bucket["cm"] += cm
            bucket["pm"] += pm
        entity_split = {
            ep: {
                "balance_cm_keur": round(b["cm"], _ROUND),
                "delta_keur": round(b["cm"] - b["pm"], _ROUND),
            }
            for ep, b in sorted(split.items())
        }

        ordered = sorted(
            rows,
            key=lambda r: (
                -abs(float(r.get("balance_cm_keur") or 0.0)),
                (r.get("gl_account_id") or ""),
            ),
        )
        evidence = [
            {
                "gl_account_id": r.get("gl_account_id"),
                "account_name": r.get("account_name"),
                "level_path": r.get("level_path"),
                "balance_cm_keur": r.get("balance_cm_keur"),
                "balance_pm_keur": r.get("balance_pm_keur"),
                "delta_keur": r.get("delta_keur"),
                "yoy_keur": r.get("yoy_keur"),
                "growth_pct": r.get("growth_pct"),
                "matched_token": r.get("matched_token"),
                "entity_prefix": r.get("entity_prefix"),
            }
            for r in ordered[:max_evidence]
        ]

        out.append({
            "position": _position_key(l2, l3, l0),
            "level_0": l0,
            "level_2": l2,
            "level_3": l3,
            "level_4": l4,
            "balance_cm_keur": round(sum_cm, _ROUND),
            "balance_pm_keur": round(sum_pm, _ROUND),
            "delta_keur": round(delta, _ROUND),
            "yoy_keur": round(yoy, _ROUND),
            "growth_pct": round(growth_pct, 2),
            "matched_token": (rows[0].get("matched_token") if rows else None),
            "account_count": len(rows),
            "entity_split": entity_split,
            "evidence": evidence,
        })

    out.sort(key=lambda n: (-abs(n["delta_keur"]), n["position"], n["level_4"]))
    return out


def annotate_suspicious_texts(
    suspicious: list[dict[str, Any]],
    level_map: dict[tuple[str, str], dict[str, str]],
) -> list[dict[str, Any]]:
    """Add ``entity_prefix`` + ``position`` to per-booking suspicious-text rows (pure).

    Suspicious texts stay booking-level (the note belongs to one line).  ``level_map``
    is keyed by ``(entity_prefix, account_number_group)`` → ``{level_0, level_2,
    level_3, level_4, level_path}`` so each row gets its position label for display
    grouping.  Rows are returned sorted by ``position`` then ``|amount|`` desc.
    """
    out: list[dict[str, Any]] = []
    for r in suspicious:
        ep = (r.get("entity_prefix") or "").strip()
        ang = (r.get("acct_ang") or "").strip()
        levels = level_map.get((ep, ang)) or {}
        position = _position_key(
            levels.get("level_2", ""), levels.get("level_3", ""),
            levels.get("level_0", ""),
        )
        row = dict(r)
        row["entity_prefix"] = ep
        row["position"] = position
        row["level_path"] = levels.get("level_path", "")
        out.append(row)

    out.sort(
        key=lambda r: (r.get("position") or "",
                       -abs(float(r.get("amount_keur") or 0.0))),
    )
    return out


# ===========================================================================
# Phase 2 — Top-N capping + plain-English Report-View (headline + bullets)
# ===========================================================================
def cap_section(
    rows: list[dict[str, Any]],
    *,
    max_rows: int = MAX_FORENSIC_PER_SECTION,
) -> tuple[list[dict[str, Any]], int]:
    """Cap a section to its top ``max_rows`` findings; return ``(kept, omitted)``.

    The rollup functions already return rows in descending importance order (by
    ``|amount|`` / ``|delta|`` / severity), so capping is just a slice.  Phase 2
    replaces the old 200-row dump with a focused short-list and carries an
    ``omitted_count`` so the UI can render "+N more".  Pure; ``max_rows <= 0`` keeps
    nothing (omitting all); empty input → ``([], 0)``.
    """
    if max_rows <= 0:
        return [], len(rows)
    kept = rows[:max_rows]
    omitted = max(0, len(rows) - len(kept))
    return kept, omitted


def _plural(n: int, singular: str, plural: Optional[str] = None) -> str:
    return singular if n == 1 else (plural or f"{singular}s")


def _keur(value: float) -> str:
    """Format a kEUR figure as a compact euro string (e.g. 42.0 → '€42k')."""
    return f"€{round(float(value or 0.0)):,}k"


def counter_section_report(
    rows: list[dict[str, Any]], omitted: int,
) -> dict[str, Any]:
    """Build the plain-English Report-View for the unexpected-counter section."""
    n = len(rows)
    if n == 0:
        return {
            "headline": "No unusual counter-accounts above the materiality floor.",
            "bullets": [
                f"Every flagged posting moved less than {_keur(MIN_FLAG_AMOUNT_KEUR)}.",
            ],
        }
    top = rows[0]
    total_abs = sum(float(r.get("abs_amount_keur") or 0.0) for r in rows)
    headline = (
        f"{n} {_plural(n, 'position')} booked against unusual counter-accounts "
        f"above {_keur(MIN_FLAG_AMOUNT_KEUR)}."
    )
    bullets = [
        f"Largest: \"{top.get('position')}\" — "
        f"{top.get('flag_count')} unusual {_plural(int(top.get('flag_count') or 0), 'posting')} "
        f"totalling {_keur(top.get('abs_amount_keur'))} across "
        f"{top.get('distinct_counter_accounts')} "
        f"{_plural(int(top.get('distinct_counter_accounts') or 0), 'counter-account')}.",
        f"{n} {_plural(n, 'position')} move {_keur(total_abs)} in total through "
        f"counter-accounts the position has rarely or never used before.",
        "Open a position to see the contributing postings and their counter-accounts.",
    ]
    if omitted:
        bullets.append(
            f"+{omitted} more material {_plural(omitted, 'position')} below the top "
            f"{MAX_FORENSIC_PER_SECTION} (smaller amounts)."
        )
    return {"headline": headline, "bullets": bullets}


def other_section_report(
    rows: list[dict[str, Any]], omitted: int,
) -> dict[str, Any]:
    """Build the plain-English Report-View for the Other-positions section."""
    n = len(rows)
    if n == 0:
        return {
            "headline": "No catch-all \"Other\" positions are materially growing.",
            "bullets": [
                "No \"Other / Miscellaneous\" bucket is both sizeable and clearly growing.",
            ],
        }
    top = rows[0]
    headline = (
        f"{n} catch-all \"Other\" {_plural(n, 'position')} "
        f"{_plural(n, 'is', 'are')} sizeable and growing."
    )
    bullets = [
        f"Largest mover: \"{top.get('position')}\" — now {_keur(top.get('balance_cm_keur'))}, "
        f"up {_keur(top.get('delta_keur'))} ({top.get('growth_pct')}%) versus last month.",
        "\"Other\" buckets are where unclear or one-off items get parked — a fast-growing "
        "balance here is worth a look.",
        "Open a position to see which accounts make it up.",
    ]
    if omitted:
        bullets.append(
            f"+{omitted} more growing \"Other\" {_plural(omitted, 'position')} below the "
            f"top {MAX_FORENSIC_PER_SECTION}."
        )
    return {"headline": headline, "bullets": bullets}


def suspicious_section_report(
    rows: list[dict[str, Any]], omitted: int,
) -> dict[str, Any]:
    """Build the plain-English Report-View for the suspicious-texts section."""
    n = len(rows)
    if n == 0:
        return {
            "headline": "No suspicious posting texts above the materiality floor.",
            "bullets": [
                f"No posting above {_keur(MIN_FLAG_AMOUNT_KEUR)} carries a manual / "
                "reversal / provisional / test keyword.",
            ],
        }
    top = rows[0]
    keywords = sorted({(r.get("matched_keyword") or "") for r in rows if r.get("matched_keyword")})
    headline = (
        f"{n} {_plural(n, 'posting')} above {_keur(MIN_FLAG_AMOUNT_KEUR)} flagged "
        f"by their free-text note."
    )
    bullets = [
        f"Largest: \"{top.get('account_name')}\" — {_keur(top.get('amount_keur'))}, "
        f"note flagged on \"{top.get('matched_keyword')}\".",
        "These notes often mark manual, corrected, reversed, provisional or test entries.",
    ]
    if keywords:
        bullets.append("Keywords seen: " + ", ".join(keywords) + ".")
    if omitted:
        bullets.append(
            f"+{omitted} more material {_plural(omitted, 'posting')} below the top "
            f"{MAX_FORENSIC_PER_SECTION}."
        )
    return {"headline": headline, "bullets": bullets}


# ===========================================================================
# DB orchestrator — enrich the per-booking builders with hierarchy, then roll up
# ===========================================================================
def _level_map(accounts: list[AccountSeries]) -> dict[tuple[str, str], dict[str, str]]:
    """``(entity_prefix, account_number_group)`` → level labels + path (pure)."""
    out: dict[tuple[str, str], dict[str, str]] = {}
    for a in accounts:
        out[(a.entity_prefix, a.account_number_group)] = {
            "level_0": a.level_0,
            "level_2": a.level_2,
            "level_3": a.level_3,
            "level_4": a.level_4,
            "level_path": build_level_path(
                a.level_2, a.level_3, a.level_4, a.l4_sub,
            ),
        }
    return out


def _enrich_counter_flags(
    flagged: list[dict[str, Any]],
    bookings: list[dict[str, Any]],
    level_map: dict[tuple[str, str], dict[str, str]],
) -> list[dict[str, Any]]:
    """Attach ``entity_prefix`` + hierarchy levels to each flagged novelty row.

    The novelty classifier keys rows by ``gl_account_id`` and does not carry the
    account_number_group / entity_prefix; we recover them from the matching booking
    (by ``booking_line_id``) and then map to levels via ``level_map``.  A flagged
    booking with no hierarchy row keeps blank levels → ``"(no position)"`` bucket.
    """
    by_line = {b["booking_line_id"]: b for b in bookings}
    enriched: list[dict[str, Any]] = []
    for r in flagged:
        bk = by_line.get(r.get("booking_line_id")) or {}
        ep = (bk.get("entity_prefix") or "").strip()
        ang = (bk.get("acct_ang") or "").strip()
        levels = level_map.get((ep, ang)) or {}
        row = dict(r)
        row["entity_prefix"] = ep
        row["account_number_group"] = ang
        row["level_0"] = levels.get("level_0", "")
        row["level_2"] = levels.get("level_2", "")
        row["level_3"] = levels.get("level_3", "")
        row["level_4"] = levels.get("level_4", "")
        enriched.append(row)
    return enriched


def build_forensic_positions(
    session: Session,
    *,
    entity_prefixes: Optional[list[str]] = None,
    freq_threshold: float = FREQ_THRESHOLD_DEFAULT,
    latest_anchor: Optional[tuple[int, int]] = None,
) -> dict[str, Any]:
    """Build the position-level forensic payload (all-history, material-bounded).

    Reads the co-occurrence CACHE (never runs the bulk learner across the whole
    ledger here) and rolls the three per-booking / per-account forensic findings up
    to L3/L4 positions, with plain-English explanations.

    Args:
        entity_prefixes: optional allow-list of 2-char entity prefixes (a restricted
            user's visible entities).  ``None`` → consolidated over all entities.
        freq_threshold: novelty "rare" cut-off (default 5%).
        latest_anchor: optional ``(year, period)`` materiality anchor; defaults to
            the last real month in the ledger (:func:`gl_analysis_common.latest_anchor`).

    Returns:
        ``{unexpected_counter_positions, other_positions, suspicious_texts, meta}``
        where ``meta`` carries ``explanations`` and ``columns_help``.
    """
    prefixes = _normalise_prefixes(entity_prefixes)

    anchor = latest_anchor if latest_anchor is not None else _default_anchor(session)
    if anchor is None:
        return _empty_payload(prefixes, freq_threshold, anchor=None)
    ay, am = int(anchor[0]), int(anchor[1])

    # SECURITY — entity scoping (fail-closed): we scope by ``entity_prefix``
    # DIRECTLY (the robust path the anomaly trees use — ``gl_anomaly_tree`` filters
    # ``a.entity_prefix in allowed``; ``list_account_bookings`` uses
    # ``entities_sql_fragment(prefixes)`` on ``entity_prefix``).  We deliberately do
    # NOT route the allowed PREFIXES through ``fin_compat_sql.resolve_entity_prefix``
    # (a ``legal_entity_code`` lookup): that path only works while ``etl/load.py``
    # seeds ``legal_entity_code == entity_prefix``; if they ever differ it returns
    # None → an EMPTY SQL fragment → an UNSCOPED query (cross-tenant leak for a
    # restricted user).  Passing the 2-char prefix as ``entity_prefix=`` makes every
    # forensic query filter ``fact_gl_line.entity_prefix`` itself, independent of the
    # code==prefix invariant.  Admin (prefixes None) → one unscoped pass = all.
    #
    # When a restricted user has several visible entities we run each prefix and
    # concatenate; an admin runs once over the consolidated ledger.
    entities: list[Optional[str]] = list(prefixes) if prefixes else [None]

    all_flagged: list[dict[str, Any]] = []
    all_other: list[dict[str, Any]] = []
    all_suspicious: list[dict[str, Any]] = []
    level_map: dict[tuple[str, str], dict[str, str]] = {}

    for ent in entities:
        # ``ent`` here is an ALLOWED 2-char entity_prefix (or None for admin/all).
        # Pull the consolidated series and filter by entity_prefix DIRECTLY (same as
        # gl_anomaly_tree) so the level map is correctly scoped even when
        # legal_entity_code != entity_prefix; passing it as ``entity=`` would route
        # through resolve_entity_prefix and could go unscoped (see note above).
        if ent is not None:
            accounts = [
                a for a in build_account_monthly_series(session, entity=None)
                if a.entity_prefix == ent
            ]
        else:
            accounts = build_account_monthly_series(session, entity=None)
        level_map.update(_level_map(accounts))

        # (1) unexpected counter accounts — cache-first per the existing builder.
        flagged = _build_unexpected_counter_accounts(
            session, None, year=ay, period=am, freq_threshold=freq_threshold,
            entity_prefix=ent,
        )
        groups = material_account_groups(
            session, None, year=ay, period=am, entity_prefix=ent,
        )
        bookings = _bookings_for_lines(session, ay, am, groups, entity_prefix=ent)
        all_flagged.extend(_enrich_counter_flags(flagged, bookings, level_map))

        # (2) "Other" positions — enrich the per-account rows with hierarchy.
        other = _build_other_positions(
            session, None, year=ay, period=am, entity_prefix=ent,
        )
        all_other.extend(_enrich_other_accounts(other, accounts))

        # (3) suspicious texts — keep per-booking, add acct_ang for position map.
        suspicious = _build_suspicious_texts(
            session, None, year=ay, period=am, account_groups=groups,
            entity_prefix=ent,
        )
        all_suspicious.extend(_attach_acct_ang(suspicious, bookings))

    # Benignity filter (anomaly refinement): drop flagged counter rows whose COUNTER
    # account is clearly a system/control account (heuristic, always on) and — only
    # when settings.forensic_use_llm AND ANTHROPIC_API_KEY are set — an optional LLM
    # pass.  Runs AFTER novelty flagging, BEFORE rollup.  llm_used is True only when the
    # LLM path actually ran (gate on + key present + survivors to evaluate).
    use_llm = bool(settings.forensic_use_llm) and bool(os.environ.get("ANTHROPIC_API_KEY"))
    llm_used = use_llm and len(all_flagged) > 0
    all_flagged, benign_dropped = filter_benign_counters(all_flagged, use_llm=use_llm)
    omitted_benign_count = len(benign_dropped)

    # Roll up the full (material-floored) findings, then cap each section to the
    # top-N most important rows and carry how many material findings were hidden.
    unexpected_full = rollup_counter_positions(all_flagged)
    other_full = rollup_other_positions(all_other)
    suspicious_full = annotate_suspicious_texts(all_suspicious, level_map)

    unexpected_positions, unexpected_omitted = cap_section(unexpected_full)
    other_positions, other_omitted = cap_section(other_full)
    suspicious_texts, suspicious_omitted = cap_section(suspicious_full)

    report_views = {
        "unexpected_counter_positions": {
            **counter_section_report(unexpected_positions, unexpected_omitted),
            "omitted_count": unexpected_omitted,
        },
        "other_positions": {
            **other_section_report(other_positions, other_omitted),
            "omitted_count": other_omitted,
        },
        "suspicious_texts": {
            **suspicious_section_report(suspicious_texts, suspicious_omitted),
            "omitted_count": suspicious_omitted,
        },
    }

    return {
        "unexpected_counter_positions": unexpected_positions,
        "other_positions": other_positions,
        "suspicious_texts": suspicious_texts,
        "meta": {
            "entity_prefixes": prefixes,
            "anchor": {"year": ay, "month": am},
            "freq_threshold_pct": freq_threshold,
            "min_flag_amount_keur": MIN_FLAG_AMOUNT_KEUR,
            "max_per_section": MAX_FORENSIC_PER_SECTION,
            "algorithm_version": POSITIONS_ALGORITHM_VERSION,
            "base_algorithm_version": ALGORITHM_VERSION,
            "explanations": dict(EXPLANATIONS),
            "columns_help": {k: dict(v) for k, v in COLUMNS_HELP.items()},
            "report_views": report_views,
            "omitted_counts": {
                "unexpected_counter_positions": unexpected_omitted,
                "other_positions": other_omitted,
                "suspicious_texts": suspicious_omitted,
            },
            # Benignity filter (anomaly refinement): how many flagged counter rows the
            # heuristic + optional LLM dropped as content-wise benign, and whether the
            # LLM path actually ran (False by default — heuristic-only, no API calls).
            "omitted_benign_count": omitted_benign_count,
            "llm_used": llm_used,
        },
    }


# ---------------------------------------------------------------------------
# Small DB / shaping helpers used only by the orchestrator
# ---------------------------------------------------------------------------
def _normalise_prefixes(entity_prefixes: Optional[list[str]]) -> list[str]:
    if not entity_prefixes:
        return []
    seen: list[str] = []
    for p in entity_prefixes:
        s = str(p).strip()[:2]
        if s and s not in seen:
            seen.append(s)
    return sorted(seen)


def _default_anchor(session: Session) -> Optional[tuple[int, int]]:
    return latest_anchor(session)


def _bookings_for_lines(
    session: Session,
    year: int,
    period: int,
    groups: list[str],
    *,
    entity_prefix: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Re-pull the current-period bookings so the novelty rows can be enriched.

    Mirrors what :func:`gl_forensic._build_unexpected_counter_accounts` pulls
    internally; we re-fetch here only to recover ``entity_prefix`` / ``acct_ang``
    per ``booking_line_id`` (the novelty output does not carry them).  Scoped by
    ``entity_prefix`` DIRECTLY (fail-closed), matching the per-entity builders.
    """
    from app.services.gl_forensic import fetch_current_period_bookings

    if not groups:
        return []
    return fetch_current_period_bookings(
        session, None, year=year, period=period, account_groups=groups,
        entity_prefix=entity_prefix,
    )


def _enrich_other_accounts(
    other: list[dict[str, Any]],
    accounts: list[AccountSeries],
) -> list[dict[str, Any]]:
    """Attach ``entity_prefix`` + levels + py_cm balance to each Other-account row.

    The per-account ``_build_other_positions`` output carries ``gl_account_id`` and
    the balances but not the entity_prefix or split level labels in a keyed form;
    we map by ``gl_account_id`` to the ``AccountSeries`` hierarchy.  ``balance_py_cm_keur``
    is recovered from ``balance_cm_keur − yoy_keur`` (yoy = cm − py_cm).
    """
    by_gid: dict[str, AccountSeries] = {}
    for a in accounts:
        if a.gl_account_id and a.gl_account_id not in by_gid:
            by_gid[a.gl_account_id] = a
    out: list[dict[str, Any]] = []
    for r in other:
        a = by_gid.get((r.get("gl_account_id") or "").strip())
        row = dict(r)
        cm = float(r.get("balance_cm_keur") or 0.0)
        yoy = float(r.get("yoy_keur") or 0.0)
        row["balance_py_cm_keur"] = round(cm - yoy, _ROUND)
        if a is not None:
            row["entity_prefix"] = a.entity_prefix
            row["level_0"] = a.level_0
            row["level_2"] = a.level_2
            row["level_3"] = a.level_3
            row["level_4"] = a.level_4
        else:
            row.setdefault("entity_prefix", "")
            row.setdefault("level_0", "")
            row.setdefault("level_2", "")
            row.setdefault("level_3", "")
            row.setdefault("level_4", "")
        out.append(row)
    return out


def _attach_acct_ang(
    suspicious: list[dict[str, Any]],
    bookings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach ``acct_ang`` + ``entity_prefix`` to suspicious-text rows by line id."""
    by_line = {b["booking_line_id"]: b for b in bookings}
    out: list[dict[str, Any]] = []
    for r in suspicious:
        bk = by_line.get(r.get("booking_line_id")) or {}
        row = dict(r)
        row["acct_ang"] = (bk.get("acct_ang") or "").strip()
        row["entity_prefix"] = (bk.get("entity_prefix") or "").strip()
        out.append(row)
    return out


def _empty_payload(
    prefixes: list[str], freq_threshold: float, *, anchor: Optional[tuple[int, int]],
) -> dict[str, Any]:
    report_views = {
        "unexpected_counter_positions": {**counter_section_report([], 0), "omitted_count": 0},
        "other_positions": {**other_section_report([], 0), "omitted_count": 0},
        "suspicious_texts": {**suspicious_section_report([], 0), "omitted_count": 0},
    }
    return {
        "unexpected_counter_positions": [],
        "other_positions": [],
        "suspicious_texts": [],
        "meta": {
            "entity_prefixes": prefixes,
            "anchor": ({"year": anchor[0], "month": anchor[1]} if anchor else None),
            "freq_threshold_pct": freq_threshold,
            "min_flag_amount_keur": MIN_FLAG_AMOUNT_KEUR,
            "max_per_section": MAX_FORENSIC_PER_SECTION,
            "algorithm_version": POSITIONS_ALGORITHM_VERSION,
            "base_algorithm_version": ALGORITHM_VERSION,
            "explanations": dict(EXPLANATIONS),
            "columns_help": {k: dict(v) for k, v in COLUMNS_HELP.items()},
            "report_views": report_views,
            "omitted_counts": {
                "unexpected_counter_positions": 0,
                "other_positions": 0,
                "suspicious_texts": 0,
            },
            "omitted_benign_count": 0,
            "llm_used": False,
        },
    }
