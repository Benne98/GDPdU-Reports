"""GL forensic data layer (Journal Agent, Phase 3) — "unexpected counter accounts".

This module is the DATA LAYER only.  The HTTP endpoint, the Forensic tab, and the
"Other"-position / suspicious-text signals are Phase 4 — NOT here.

It delivers three things:

  1. ``derive_counter_accounts`` — for a batch of anchor ``fact_gl_line`` rows
     (by ``booking_line_id``) find each anchor's TOP-1 counter account: the
     sibling line(s) in the SAME journal entry ``(journal_entry_group_number,
     fiscal_year)`` on the OPPOSITE side (``SIGN(amount)`` differs), aggregated per
     counter ``account_number_group`` by ``SUM(|amount|)``, top-1 per anchor.  ONE
     batched query keyed on the anchor groups (NOT N+1).

  2. ``build_cooccurrence`` — the per-account historical learner: ONE grouped
     query over ALL history producing, per ``(account A, counter B)`` pair,
     ``pair_freq = COUNT(DISTINCT (journal_entry_group_number, fiscal_year))``;
     plus ``acct_total_pairs[A] = Σ_B pair_freq`` and
     ``freq_pct(A,B) = pair_freq / acct_total_pairs[A]``.  The analysed period is
     EXCLUDED (so a current booking can't make itself "common").

  3. ``classify_novelty`` — for current-period bookings on MATERIAL accounts,
     derive each line's counter account and flag it ``new`` (the pair never
     occurred in history-excluding-the-period) or ``rare`` (``freq_pct`` below a
     threshold, default 0.05).

================================================================================
FINANCIAL / STATISTICAL LOGIC (CLAUDE.md rule #1)
================================================================================
This is NOT a monetary KPI — no new sign or period money formula.  The only new
math is the descriptive co-occurrence frequency:

    pair_freq(A, B)      = number of DISTINCT journal entries (group+year) in which
                           A and B appear together on opposite sides
    acct_total_pairs(A)  = Σ_B pair_freq(A, B)
    freq_pct(A, B)       = pair_freq(A, B) / acct_total_pairs(A)     # 0 if total 0

    novelty(A, B in current period):
        "new"  ⇔ pair_freq_history_excl_period(A, B) == 0
        "rare" ⇔ 0 < freq_pct < freq_threshold        (default 0.05)
        None   ⇔ freq_pct >= freq_threshold            (expected / common)

WORKED EXAMPLE
--------------
Account A historical counters (history excl. analysed period):
    B1 pair_freq = 80, B2 pair_freq = 15, B3 pair_freq = 5  → acct_total = 100.
    freq_pct: B1 = 0.80, B2 = 0.15, B3 = 0.05.
A current-period booking A↔B1 → freq_pct 0.80 ≥ 0.05 → not flagged (common).
A current-period booking A↔B3 → freq_pct 0.05 ≥ 0.05 → NOT "rare" (boundary is
    inclusive on the "common" side: ``rare`` ⇔ ``freq_pct < threshold``).
A current-period booking A↔B4 (never seen) → pair_freq 0 → "new".
A counter at freq_pct 0.04 (< 0.05) → "rare".

EDGE CASES
----------
  * Multi-leg entry → counter = TOP-1 by SUM(|amount|) of the opposite side; the
    diversity is still visible via ``acct_total_pairs`` on the learner.
  * Opposite-sign filter: only siblings with the OTHER sign count; same-side
    siblings (e.g. two debits) are ignored.
  * Synthetic rows (``source_system LIKE 'synthetic\\_%'``) excluded — the
    single-leg OB / net-profit rows have no sibling anyway, but excluding them
    keeps the learner clean and matches ``gl_analysis_common``.
  * exclude-current-period: ``build_cooccurrence(exclude_year, exclude_period)``
    drops journal entries in that ``(fiscal_year, fiscal_period)`` so the analysed
    booking can't inflate its own history.
  * acct_total_pairs == 0 → freq_pct 0 for all of A's counters (no divide-by-zero).
  * ``freq_pct`` boundary: a counter exactly AT the threshold is NOT rare
    (rare uses strict ``<``); a counter with pair_freq 0 is "new" (takes priority
    over rare).

================================================================================
SIGN / SYNTHETIC CONVENTIONS (reused from gl_analysis_common)
================================================================================
``fact_gl_line.amount``: ``+`` = debit, ``−`` = credit.  Counter-account siblings
are the opposite side of the same journal entry.  Synthetic exclusion uses the
ENTRY-side ``source_system NOT LIKE 'synthetic\\_%' ESCAPE '\\'`` (entry_type lives
on ``fact_gl_entry``, not the line).  Entity scoping uses
``fin_compat_sql.entity_sql_fragment`` (2-char prefix, injection-safe).
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.fin_compat_narrative_core import MOM_FLOOR_EUR, SIZE_FLOOR_EUR
from app.services.fin_compat_sql import (
    entity_sql_fragment,
    period_label,
    plan_anchor_for_week,
    pm as prior_month,
    resolve_entity_prefix,
)
from app.services.gl_analysis_common import (
    _NOT_SYNTHETIC,
    AccountSeries,
    bound_material_accounts,
    build_account_monthly_series,
)

logger = logging.getLogger(__name__)

ALGORITHM_VERSION = "gl_forensic_v2"

#: Synthetic-exclusion AND-fragment aliased to the ENTRY alias used in the learner
#: self-join (``ea``).  ``gl_analysis_common._NOT_SYNTHETIC`` is hard-aliased to
#: ``e``; the learner uses ``ea`` for the anchor leg's entry, so we mint a parallel
#: fragment instead of reusing the ``e``-aliased one (same predicate, same ESCAPE).
_NOT_SYNTHETIC_EA = "AND ea.source_system NOT LIKE 'synthetic\\_%' ESCAPE '\\'"

#: A counter account is "rare" when its share of the anchor account's historical
#: pairings is below this fraction.  Default 5% (plan decision 5).
FREQ_THRESHOLD_DEFAULT = 0.05

_EUR_PER_KEUR = 1000.0

#: Legacy cap on each of the three forensic lists.  Kept as a generous safety bound
#: on the *intermediate* per-booking / per-account lists (before rollup).  The
#: USER-FACING cap per section is the much tighter :data:`MAX_FORENSIC_PER_SECTION`
#: (Phase 2 forensic-targeting) — see ``gl_forensic_positions``.
MAX_FORENSIC_ROWS = 200

#: Phase 2 forensic-targeting: each FINISHED forensic section is capped to its top-N
#: most important findings (by |amount| / severity); everything below the cap is
#: counted into an ``omitted_count`` so the UI can show "+N more".  Replaces the old
#: 200-row dump with a focused short-list.
MAX_FORENSIC_PER_SECTION = 6

#: Phase 2 forensic-targeting: a finding (unexpected-counter novelty row, suspicious
#: text row, position rollup) is only worth surfacing when it moves at least this
#: much money.  Below the floor the long tail of tiny, mostly-noise postings is
#: dropped.  Expressed in kEUR (thousands of euros) to match ``amount_keur``.
MIN_FLAG_AMOUNT_KEUR_DEFAULT = 25.0

#: "Other"-position tokens.  An account is an Other-position when ANY of its
#: ``dim_gl_account.level_2 / level_3 / level_4 / l4_sub`` labels contains one of
#: these tokens as a WHOLE WORD (case-insensitive, unicode word boundaries — see
#: :data:`_OTHER_RE`).  DE + EN incl. umlaut variants.  Order matters: longer
#: tokens precede their prefixes (``miscellaneous`` before ``misc``) so the reported
#: token is the most specific match.
OTHER_TOKENS: tuple[str, ...] = (
    "other", "sonstige", "sonstiges", "übrige", "ubrige", "uebrige",
    "miscellaneous", "misc", "diverse",
)

#: Suspicious-text keyword tokens for ``fact_gl_line.line_note`` (matched as WHOLE
#: WORDS, case-insensitive, unicode — see :data:`_TEXT_RE`).  DE + EN incl. umlaut
#: variants.  These flag manual / reversal / provisional / test bookings an auditor
#: would want to eyeball.  Whole-word matching means "test" no longer fires on
#: "protest" / "testing", and "storno" still matches "Storno gebucht".
TEXT_TOKENS: tuple[str, ...] = (
    "storno", "korrektur", "rückbuchung", "ruckbuchung", "rueckbuchung",
    "manuell", "manual", "vorläufig", "vorlaufig", "vorlaeufig", "provisional",
    "test", "umbuchung", "reversal", "adjust",
)


#: Inflectional endings allowed AFTER a matched token (DE + EN), so a token still
#: fires on an inflected word ("vorläufige" → "vorläufig", "manuelle" → "manuell")
#: WITHOUT matching an unrelated word that merely starts with the token's letters
#: ("testing" → "test"+"ing" is NOT in this set, so it does NOT match).  Together
#: with the required LEADING ``\b`` this kills the substring false-positives the
#: legacy matcher produced ("protest"/"testing" no longer match "test") while
#: keeping German inflection working.
_INFLECTION_SUFFIX = r"(?:e|en|er|es|em|ung|ungen|t|te|ten)?"


def _compile_word_boundary_regex(tokens: tuple[str, ...]) -> re.Pattern[str]:
    """Compile ``\\b(tok1|tok2|…)<infl>\\b`` — case-insensitive, unicode, longest-first.

    A token fires only when it STARTS at a word boundary (so "protest" never matches
    "test" — "test" there is not at a word start) and ends at a word boundary,
    optionally followed by a short inflectional suffix (see
    :data:`_INFLECTION_SUFFIX`) so German/English inflected forms still match
    ("Storno gebucht" → "storno", "Vorläufige" → "vorläufig") while an unrelated
    word that merely begins with the token's letters does NOT ("testing" / "tester"
    do not match "test").  ``re.UNICODE`` makes ``\\b`` treat umlauts (ä/ö/ü/ß) as
    word characters.  Tokens are escaped and sorted longest-first so the most
    specific token wins (``miscellaneous`` before ``misc``), independent of
    declaration order.
    """
    ordered = sorted(set(tokens), key=len, reverse=True)
    alternation = "|".join(re.escape(tok) for tok in ordered)
    # Capture the TOKEN CORE (group 1) so callers report the canonical token, not the
    # inflected surface form ("vorläufige" → reports "vorläufig").
    return re.compile(
        rf"\b({alternation}){_INFLECTION_SUFFIX}\b",
        re.IGNORECASE | re.UNICODE,
    )


#: Benignity tokens (anomaly refinement): a flagged unexpected-counter row is dropped
#: when its COUNTER account is clearly a system / control / technical account — these
#: are plausible (if rarely-booked) counter-accounts, not suspicious ones.  Matched as
#: WHOLE WORDS (case-insensitive, unicode — see :data:`_BENIGN_RE`) on the counter
#: account name + the anchor's level_3 / level_4.  DE + EN incl. umlaut variants;
#: longer tokens precede their prefixes so the most specific match wins.
BENIGN_COUNTER_TOKENS: tuple[str, ...] = (
    "clearing", "reconciliation", "intercompany", "consolidation",
    "provision", "rückstellung", "ruckstellung", "rueckstellung",
    "accrual", "abgrenzung",
    "tax", "vat", "ust", "umsatzsteuer", "steuer",
    "payroll", "lohn", "gehalt",
    "rounding", "rundung",
    "interim", "verrechnung", "verrechnungskonto",
    "suspense",
)

#: Precompiled whole-word matchers (Phase 2 forensic-targeting).
_OTHER_RE: re.Pattern[str] = _compile_word_boundary_regex(OTHER_TOKENS)
_TEXT_RE: re.Pattern[str] = _compile_word_boundary_regex(TEXT_TOKENS)

#: Precompiled benign-counter matcher (anomaly refinement).
_BENIGN_RE: re.Pattern[str] = _compile_word_boundary_regex(BENIGN_COUNTER_TOKENS)

#: An Other-position is "growing" (and therefore flagged) when it is material at the
#: current month AND BOTH of: (a) its absolute MoM move is at least
#: ``MOM_FLOOR_EUR * OTHER_DELTA_MULTIPLE`` (a raised bar vs. the plain narrative
#: MoM floor) AND (b) its growth_pct is at least ``OTHER_GROWTH_PCT_THRESHOLD``.
#: Phase 2 tightened this from "delta OR growth_pct" to "delta AND growth_pct" so an
#: Other bucket is only flagged when it is clearly AND meaningfully growing — a
#: small absolute move that happens to be a big percentage (or a big absolute move
#: that is a tiny percentage) no longer trips the flag.
OTHER_GROWTH_PCT_THRESHOLD = 20.0

#: Multiple applied to ``MOM_FLOOR_EUR`` for the Other-position delta gate (Phase 2).
#: 2× raises the absolute-move bar so only sizeable Other movers surface.
OTHER_DELTA_MULTIPLE = 2.0


# ---------------------------------------------------------------------------
# Entity scoping (security): prefer the DIRECT 2-char entity_prefix
# ---------------------------------------------------------------------------
def _scope_prefix(
    session: Session,
    entity: Optional[str],
    entity_prefix: Optional[str],
) -> Optional[str]:
    """Resolve the 2-char ``entity_prefix`` used to scope a forensic query.

    SECURITY RULE (pinned): scoping is by ``fact_gl_line.entity_prefix`` DIRECTLY —
    the same robust path the anomaly trees use (``gl_anomaly_tree`` filters
    ``entity_prefix in allowed``; ``list_account_bookings`` uses
    ``entities_sql_fragment`` on ``entity_prefix``).  When the caller passes
    ``entity_prefix`` (the positions orchestrator does, from the user's ALLOWED
    prefixes) we use it verbatim — we do NOT route it through
    :func:`resolve_entity_prefix`'s ``legal_entity_code`` lookup, so scoping no
    longer depends on the ``legal_entity_code == entity_prefix`` seeding invariant
    in ``etl/load.py``.  If that invariant ever breaks, ``resolve_entity_prefix``
    would return ``None`` → an EMPTY fragment → an UNSCOPED query (cross-tenant
    leak for a restricted user); scoping by the prefix directly is fail-CLOSED.

    The legacy ``entity`` arg (a legal_entity_code) is honoured ONLY when
    ``entity_prefix`` is not supplied, for the legacy per-booking
    :func:`build_forensic` path (where code==prefix on the current data).
    """
    if entity_prefix is not None:
        s = str(entity_prefix).strip()[:2]
        return s or None
    return resolve_entity_prefix(session, entity)


# ---------------------------------------------------------------------------
# Pure helpers (DB-free, unit-tested directly)
# ---------------------------------------------------------------------------
def compute_freq_pct(pair_freq: int, acct_total_pairs: int) -> float:
    """``pair_freq / acct_total_pairs`` — 0.0 when the total is 0 (no div-by-zero)."""
    if not acct_total_pairs:
        return 0.0
    return pair_freq / acct_total_pairs


def classify_pair_novelty(
    pair_freq: int,
    freq_pct: float,
    *,
    freq_threshold: float = FREQ_THRESHOLD_DEFAULT,
) -> Optional[str]:
    """Return ``"new"`` / ``"rare"`` / ``None`` for one (A, B) pair.

      * ``"new"``  — never co-occurred in the learned history (``pair_freq == 0``).
      * ``"rare"`` — occurred but rare (``0 < freq_pct < freq_threshold``).
      * ``None``   — common (``freq_pct >= freq_threshold``); not flagged.

    "new" takes priority over "rare".  The threshold boundary is on the common
    side (a pair exactly at the threshold is common, not rare).
    """
    if pair_freq <= 0:
        return "new"
    if freq_pct < freq_threshold:
        return "rare"
    return None


def pick_top_counter(
    counters: list[tuple[str, float]],
) -> Optional[tuple[str, float]]:
    """Pick the top-1 counter ``(counter_ang, abs_weight)`` by weight.

    Deterministic tie-break: larger weight first, then smaller ``counter_ang``
    (lexicographic) so a multi-leg entry always resolves to the same counter.
    Empty list → ``None``.
    """
    if not counters:
        return None
    return sorted(counters, key=lambda t: (-t[1], t[0]))[0]


# ---------------------------------------------------------------------------
# 1) Counter-account derivation (ONE batched query — not N+1)
# ---------------------------------------------------------------------------
def derive_counter_accounts(
    session: Session,
    booking_line_ids: list[int],
) -> dict[int, dict[str, Any]]:
    """Derive each anchor line's TOP-1 counter account in ONE batched query.

    For every anchor ``fact_gl_line`` named by ``booking_line_id``, find the
    sibling lines in the SAME ``(journal_entry_group_number, fiscal_year)`` whose
    ``SIGN(amount)`` differs (the opposite side of the entry), aggregate by counter
    ``account_number_group`` via ``SUM(|amount|)``, and keep the top-1 per anchor.
    Synthetic rows are excluded on the entry side.

    Returns ``{booking_line_id: {counter_account_number_group, counter_gl_account_id,
    counter_account_name, weight}}``.  Anchors with no opposite-side sibling (e.g.
    single-leg rows) are simply absent from the result.

    ONE query: the anchors are passed as a bound array and joined to their entry's
    other lines; the per-anchor top-1 is taken with a window function so there is
    NO per-anchor round-trip.
    """
    ids = [int(x) for x in booking_line_ids if x is not None]
    if not ids:
        return {}

    sql = f"""
        WITH anchor AS (
            SELECT l.booking_line_id,
                   l.journal_entry_group_number,
                   l.fiscal_year,
                   l.line_number,
                   l.amount
            FROM fact_gl_line l
            WHERE l.booking_line_id = ANY(:ids)
        ),
        sib AS (
            SELECT
                an.booking_line_id,
                b.account_number_group           AS counter_ang,
                SUM(ABS(b.amount))               AS weight
            FROM anchor an
            JOIN fact_gl_entry e
              ON e.journal_entry_group_number = an.journal_entry_group_number
             AND e.fiscal_year = an.fiscal_year
            JOIN fact_gl_line b
              ON b.journal_entry_group_number = an.journal_entry_group_number
             AND b.fiscal_year = an.fiscal_year
             AND b.line_number <> an.line_number
            WHERE e.fiscal_period BETWEEN 1 AND 12
              {_NOT_SYNTHETIC}
              AND b.amount <> 0
              AND an.amount <> 0
              AND SIGN(b.amount) <> SIGN(an.amount)
            GROUP BY an.booking_line_id, b.account_number_group
        ),
        ranked AS (
            SELECT
                sib.booking_line_id,
                sib.counter_ang,
                sib.weight,
                ROW_NUMBER() OVER (
                    PARTITION BY sib.booking_line_id
                    ORDER BY sib.weight DESC, sib.counter_ang ASC
                ) AS rn
            FROM sib
        )
        SELECT
            r.booking_line_id,
            r.counter_ang,
            r.weight,
            MAX(a.gl_account_id)  AS counter_gl_account_id,
            MAX(a.account_name)   AS counter_account_name
        FROM ranked r
        LEFT JOIN dim_gl_account a
          ON a.account_number_group = r.counter_ang
        WHERE r.rn = 1
        GROUP BY r.booking_line_id, r.counter_ang, r.weight
    """
    rows = session.execute(text(sql), {"ids": ids}).fetchall()

    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        d = dict(r._mapping) if hasattr(r, "_mapping") else dict(r)
        out[int(d["booking_line_id"])] = {
            "counter_account_number_group": (d.get("counter_ang") or "").strip(),
            "counter_gl_account_id": (d.get("counter_gl_account_id") or "").strip(),
            "counter_account_name": (d.get("counter_account_name") or "").strip(),
            "weight": round(float(d.get("weight") or 0.0), 4),
        }
    return out


# ---------------------------------------------------------------------------
# 2) Per-account co-occurrence learner (ONE grouped query over ALL history)
# ---------------------------------------------------------------------------
def build_cooccurrence(
    session: Session,
    entity: Optional[str],
    *,
    exclude_year: Optional[int] = None,
    exclude_period: Optional[int] = None,
    entity_prefix: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Learn the per-account counter co-occurrence over ALL history (one query).

    For every ``(account A, counter B)`` pair — siblings in the same journal entry
    ``(journal_entry_group_number, fiscal_year)`` on OPPOSITE sides, synthetic
    excluded — compute:

      * ``pair_freq``  = ``COUNT(DISTINCT (journal_entry_group_number, fiscal_year))``
      * ``acct_total_pairs[A]`` = ``Σ_B pair_freq``  (window over A)
      * ``freq_pct(A, B)`` = ``pair_freq / acct_total_pairs[A]``

    When ``exclude_year``/``exclude_period`` are given, journal entries in that
    ``(fiscal_year, fiscal_period)`` are dropped from the learning set so a booking
    in the analysed period cannot make its own counter look "common".

    Entity-scoped via ``entity_sql_fragment`` (a single resolved prefix, or the
    whole ledger when ``entity`` is ``None`` / ``"all"``).

    Returns a list of dicts (one per A↔B pair):
        ``{entity_prefix, acct_ang, counter_ang, pair_freq, acct_total_pairs,
        freq_pct}``.
    """
    ep = _scope_prefix(session, entity, entity_prefix)
    # Both legs share the same journal entry, so they share the same entity_prefix;
    # scoping the anchor leg (a) is sufficient and keeps the pair self-consistent.
    ent_frag = entity_sql_fragment(ep, table_alias="a")

    excl_frag = ""
    params: dict[str, Any] = {}
    if exclude_year is not None and exclude_period is not None:
        excl_frag = "AND NOT (ea.fiscal_year = :exy AND ea.fiscal_period = :exp)"
        params = {"exy": int(exclude_year), "exp": int(exclude_period)}

    sql = f"""
        WITH pairs AS (
            SELECT
                a.entity_prefix          AS entity_prefix,
                a.account_number_group   AS acct_ang,
                b.account_number_group   AS counter_ang,
                COUNT(DISTINCT (a.journal_entry_group_number, a.fiscal_year))
                                         AS pair_freq
            FROM fact_gl_line a
            JOIN fact_gl_entry ea
              ON ea.journal_entry_group_number = a.journal_entry_group_number
             AND ea.fiscal_year = a.fiscal_year
            JOIN fact_gl_line b
              ON b.journal_entry_group_number = a.journal_entry_group_number
             AND b.fiscal_year = a.fiscal_year
             AND b.line_number <> a.line_number
            WHERE ea.fiscal_period BETWEEN 1 AND 12
              {_NOT_SYNTHETIC_EA}
              AND a.amount <> 0
              AND b.amount <> 0
              AND SIGN(a.amount) <> SIGN(b.amount)
              {excl_frag}
              {ent_frag}
            GROUP BY a.entity_prefix, a.account_number_group, b.account_number_group
        )
        SELECT
            entity_prefix,
            acct_ang,
            counter_ang,
            pair_freq,
            SUM(pair_freq) OVER (PARTITION BY entity_prefix, acct_ang)
                                 AS acct_total_pairs
        FROM pairs
        ORDER BY entity_prefix, acct_ang, pair_freq DESC, counter_ang
    """
    rows = session.execute(text(sql), params).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r._mapping) if hasattr(r, "_mapping") else dict(r)
        pf = int(d.get("pair_freq") or 0)
        tot = int(d.get("acct_total_pairs") or 0)
        out.append({
            "entity_prefix": (d.get("entity_prefix") or "").strip(),
            "acct_ang": (d.get("acct_ang") or "").strip(),
            "counter_ang": (d.get("counter_ang") or "").strip(),
            "pair_freq": pf,
            "acct_total_pairs": tot,
            "freq_pct": round(compute_freq_pct(pf, tot), 6),
        })
    return out


def cooccurrence_index(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Index learner rows by ``(entity_prefix, acct_ang, counter_ang)`` (pure)."""
    return {
        (r["entity_prefix"], r["acct_ang"], r["counter_ang"]): r
        for r in rows
    }


def acct_total_index(rows: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
    """Index ``acct_total_pairs`` by ``(entity_prefix, acct_ang)`` (pure)."""
    out: dict[tuple[str, str], int] = {}
    for r in rows:
        out[(r["entity_prefix"], r["acct_ang"])] = int(r["acct_total_pairs"])
    return out


# ---------------------------------------------------------------------------
# 3) Current-period booking pull + novelty classification
# ---------------------------------------------------------------------------
def fetch_current_period_bookings(
    session: Session,
    entity: Optional[str],
    *,
    year: int,
    period: int,
    account_groups: Optional[list[str]] = None,
    entity_prefix: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Fetch the current-period anchor lines (one query), synthetic excluded.

    Optionally restricted to ``account_groups`` (the material-account allow-list).
    Returns one dict per ``fact_gl_line`` with the fields the novelty classifier
    and the Phase-4 payload need.  ``amount_keur`` is the raw stored amount / 1000
    (PRESENTATION sign is applied by the caller / endpoint — this is the ledger
    amount used only for display alongside the flag).

    SCOPING RULE (security): when ``entity_prefix`` is given it scopes
    ``fact_gl_line.entity_prefix`` DIRECTLY (the robust path the trees use); this
    must NOT depend on the ``legal_entity_code == entity_prefix`` seeding invariant.
    The legacy ``entity`` arg (a legal_entity_code routed through
    ``resolve_entity_prefix``) is kept ONLY for the legacy per-booking
    :func:`build_forensic` path; the positions orchestrator passes ``entity_prefix``.
    """
    ep = _scope_prefix(session, entity, entity_prefix)
    ent_frag = entity_sql_fragment(ep, table_alias="l")

    ang_frag = ""
    params: dict[str, Any] = {"y": int(year), "p": int(period)}
    if account_groups:
        ang_frag = "AND l.account_number_group = ANY(:angs)"
        params["angs"] = [str(g) for g in account_groups]

    sql = f"""
        SELECT
            l.booking_line_id,
            l.journal_entry_group_number,
            l.account_number_group           AS acct_ang,
            l.entity_prefix                  AS entity_prefix,
            l.amount                         AS amount,
            l.line_note                      AS line_note,
            a.gl_account_id                  AS gl_account_id,
            a.account_name                   AS account_name,
            e.posting_date                   AS posting_date
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE e.fiscal_year = :y
          AND e.fiscal_period = :p
          {_NOT_SYNTHETIC}
          AND l.amount <> 0
          {ang_frag}
          {ent_frag}
        ORDER BY l.account_number_group, l.booking_line_id
    """
    rows = session.execute(text(sql), params).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r._mapping) if hasattr(r, "_mapping") else dict(r)
        out.append({
            "booking_line_id": int(d["booking_line_id"]),
            "journal_entry_group_number": (d.get("journal_entry_group_number") or "").strip(),
            "acct_ang": (d.get("acct_ang") or "").strip(),
            "entity_prefix": (d.get("entity_prefix") or "").strip(),
            "amount_keur": round(float(d.get("amount") or 0.0) / _EUR_PER_KEUR, 3),
            "line_note": (d.get("line_note") or "").strip(),
            "gl_account_id": (d.get("gl_account_id") or "").strip(),
            "account_name": (d.get("account_name") or "").strip(),
            "posting_date": (d["posting_date"].isoformat() if d.get("posting_date") else None),
        })
    return out


def classify_novelty(
    current_period_bookings: list[dict[str, Any]],
    cooccurrence: list[dict[str, Any]],
    counter_by_line: dict[int, dict[str, Any]],
    *,
    freq_threshold: float = FREQ_THRESHOLD_DEFAULT,
) -> list[dict[str, Any]]:
    """Flag current-period bookings whose counter account is ``new`` or ``rare``.

    Pure function (no DB): the caller supplies
      * ``current_period_bookings`` — from :func:`fetch_current_period_bookings`,
        already bounded to material accounts;
      * ``cooccurrence`` — from :func:`build_cooccurrence` (history EXCLUDING the
        analysed period);
      * ``counter_by_line`` — from :func:`derive_counter_accounts` for the same
        bookings' ``booking_line_id`` set.

    For each booking it derives the counter account, looks up the learned
    ``pair_freq`` / ``acct_total_pairs`` / ``freq_pct`` for ``(entity_prefix,
    acct_ang, counter_ang)``, classifies novelty, and returns ONLY the flagged rows
    (``new`` or ``rare``).  Bookings with no derivable counter, or a common counter,
    are dropped.

    Each returned row:
        ``{gl_account_id, account_name, counter_gl_account_id,
        counter_account_name, booking_line_id, journal_entry_number,
        posting_date, amount_keur, line_note, pair_freq, acct_total_pairs,
        freq_pct, novelty}``.
    """
    cooc = cooccurrence_index(cooccurrence)
    totals = acct_total_index(cooccurrence)

    out: list[dict[str, Any]] = []
    for bk in current_period_bookings:
        line_id = bk["booking_line_id"]
        counter = counter_by_line.get(line_id)
        if not counter or not counter.get("counter_account_number_group"):
            continue

        ep = bk["entity_prefix"]
        acct = bk["acct_ang"]
        ctr = counter["counter_account_number_group"]

        key = (ep, acct, ctr)
        learned = cooc.get(key)
        pair_freq = int(learned["pair_freq"]) if learned else 0
        acct_total = (
            int(learned["acct_total_pairs"]) if learned
            else int(totals.get((ep, acct), 0))
        )
        freq_pct = compute_freq_pct(pair_freq, acct_total)
        novelty = classify_pair_novelty(
            pair_freq, freq_pct, freq_threshold=freq_threshold,
        )
        if novelty is None:
            continue

        out.append({
            "gl_account_id": bk["gl_account_id"],
            "account_name": bk["account_name"],
            "counter_gl_account_id": counter["counter_gl_account_id"],
            "counter_account_name": counter["counter_account_name"],
            "booking_line_id": line_id,
            # journal_entry_number = the entry id WITHOUT the 2-char entity prefix
            # (journal_entry_group_number = entity_prefix(2) || journal_entry_number).
            "journal_entry_number": bk["journal_entry_group_number"][2:]
            if len(bk["journal_entry_group_number"]) > 2
            else bk["journal_entry_group_number"],
            "posting_date": bk["posting_date"],
            "amount_keur": bk["amount_keur"],
            "line_note": bk["line_note"],
            "pair_freq": pair_freq,
            "acct_total_pairs": acct_total,
            "freq_pct": round(freq_pct, 6),
            "novelty": novelty,
        })
    return out


# ---------------------------------------------------------------------------
# Convenience: material-account allow-list for the current period
# ---------------------------------------------------------------------------
def material_account_groups(
    session: Session,
    entity: Optional[str],
    *,
    year: int,
    period: int,
    top_n: Optional[int] = None,
    entity_prefix: Optional[str] = None,
) -> list[str]:
    """Return the ``account_number_group`` allow-list of material accounts.

    Reuses ``gl_analysis_common.build_account_monthly_series`` +
    ``bound_material_accounts`` (the same material floors the other analyses use)
    so "material" means the same thing everywhere.  Used to bound the forensic
    novelty scan to accounts that actually matter in the analysed period.

    SCOPING RULE (security): when ``entity_prefix`` is given we pull the
    consolidated series and filter ``entity_prefix`` DIRECTLY in Python (the same
    robust path :mod:`gl_anomaly_tree` uses), NOT via ``resolve_entity_prefix`` —
    so the material allow-list is correctly scoped even if ``legal_entity_code !=
    entity_prefix``.
    """
    if entity_prefix is not None:
        scope = str(entity_prefix).strip()[:2]
        accounts = build_account_monthly_series(session, entity=None)
        accounts = [a for a in accounts if a.entity_prefix == scope]
    else:
        ent = entity
        if ent and str(ent).strip().lower() in ("", "all"):
            ent = None
        accounts = build_account_monthly_series(session, entity=ent)
    bounded = bound_material_accounts(
        accounts, current_year=year, current_period=period, top_n=top_n,
    )
    return [a.account_number_group for a in bounded if a.account_number_group]


# ===========================================================================
# Phase 4 — forensic payload (unexpected counters + Other positions + texts)
# ===========================================================================

# ---------------------------------------------------------------------------
# Pure token matchers (DB-free, unit-tested directly)
# ---------------------------------------------------------------------------
def match_other_token(*labels: Optional[str]) -> Optional[str]:
    """Return the first ``OTHER_TOKENS`` token found as a WHOLE WORD in any label
    (case-insensitive, unicode), or ``None``.

    Phase 2: whole-word matching via :data:`_OTHER_RE` (``\\b(token|…)\\b``) so the
    token must stand on its own — "Other operating income" still matches "other",
    but a label that merely contains the letters of a token inside a larger word
    does not.  The reported token is lower-cased and is the most specific match
    (longest token wins, the regex being longest-first).
    """
    hay = " ".join((lbl or "") for lbl in labels)
    if not hay.strip():
        return None
    m = _OTHER_RE.search(hay)
    return m.group(1).lower() if m else None


def match_text_keyword(line_note: Optional[str]) -> Optional[str]:
    """Return the first ``TEXT_TOKENS`` keyword found as a WHOLE WORD in ``line_note``
    (case-insensitive, unicode), or ``None``.

    Phase 2: whole-word matching via :data:`_TEXT_RE` (``\\b(token|…)\\b``).  So
    "Storno gebucht" matches "storno", but "protest" / "testing" no longer match
    "test".  Umlaut tokens ("vorläufig", "rückbuchung") match thanks to
    ``re.UNICODE``.  The reported keyword is lower-cased.
    """
    note = line_note or ""
    if not note.strip():
        return None
    m = _TEXT_RE.search(note)
    return m.group(1).lower() if m else None


def build_level_path(*labels: Optional[str]) -> str:
    """Join non-empty level labels with ' / ' (the displayed Other-position path)."""
    return " / ".join(lbl.strip() for lbl in labels if lbl and lbl.strip())


def match_benign_counter(*labels: Optional[str]) -> Optional[str]:
    """Return the first ``BENIGN_COUNTER_TOKENS`` token found as a WHOLE WORD in any of
    ``labels`` (case-insensitive, unicode), or ``None``.

    Whole-word matching via :data:`_BENIGN_RE` (``\\b(token|…)\\b`` — same Phase-2 regex
    style as :func:`match_text_keyword`), so "Tax clearing account" matches "clearing"
    but a label that merely contains the letters does not.  The reported token is
    lower-cased and the most specific match (longest token wins).
    """
    hay = " ".join((lbl or "") for lbl in labels)
    if not hay.strip():
        return None
    m = _BENIGN_RE.search(hay)
    return m.group(1).lower() if m else None


def filter_benign_counters(
    flagged_rows: list[dict[str, Any]],
    *,
    use_llm: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Narrow flagged unexpected-counter rows by dropping the content-wise BENIGN ones.

    Runs AFTER novelty flagging (post :func:`_build_unexpected_counter_accounts`, pre
    rollup) and returns ``(kept, dropped)`` — both preserve input order.

    Two stages, fail-CLOSED throughout (when in doubt, KEEP the row):

      1. **Deterministic heuristic (always on):** a row is dropped when its COUNTER
         account is clearly a system / control / technical account — matched by
         :func:`match_benign_counter` (whole-word token match) on
         ``counter_account_name`` + the anchor's ``level_3`` / ``level_4``.  Such a
         counter (clearing, tax/VAT/USt, payroll/Lohn, provision/Rückstellung,
         intercompany, consolidation, rounding, suspense, Verrechnung, …) is a
         plausible — if rarely-booked — counterparty, not a suspicious one.

      2. **Optional LLM (gated):** only when ``use_llm`` is True AND
         ``ANTHROPIC_API_KEY`` is in the environment.  Reuses the narrative LLM pattern
         (:func:`fin_compat_narrative._llm_enhance` style — ``anthropic.Anthropic()``,
         claude-3-haiku, ``try/except`` → on ANY error KEEP the row).  For each row that
         survived the heuristic it asks whether the anchor↔counter pairing is plausibly
         benign; benign rows are dropped.  NEVER called in tests (gate off by default;
         ``settings.forensic_use_llm`` defaults to ``False`` and tests don't set the key).

    Returns ``(kept, dropped)``; ``dropped`` rows carry a ``benign_reason`` (the matched
    token, or ``"llm"``) so callers can audit / count them.
    """
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []

    for r in flagged_rows:
        token = match_benign_counter(
            r.get("counter_account_name"),
            r.get("level_3"),
            r.get("level_4"),
        )
        if token:
            row = dict(r)
            row["benign_reason"] = token
            dropped.append(row)
        else:
            kept.append(r)

    # Optional LLM pass over the heuristic survivors (gated; fail-closed).
    if use_llm and kept and os.environ.get("ANTHROPIC_API_KEY"):
        kept_after_llm: list[dict[str, Any]] = []
        for r in kept:
            try:
                benign = _llm_counter_is_benign(
                    anchor_name=str(r.get("account_name") or ""),
                    counter_name=str(r.get("counter_account_name") or ""),
                    level_3=str(r.get("level_3") or ""),
                    level_4=str(r.get("level_4") or ""),
                    novelty=str(r.get("novelty") or ""),
                )
            except Exception:
                benign = False  # fail-closed → keep the row
            if benign:
                row = dict(r)
                row["benign_reason"] = "llm"
                dropped.append(row)
            else:
                kept_after_llm.append(r)
        kept = kept_after_llm

    return kept, dropped


def _llm_counter_is_benign(
    *,
    anchor_name: str,
    counter_name: str,
    level_3: str,
    level_4: str,
    novelty: str,
) -> bool:
    """Ask the narrative LLM whether an anchor↔counter pairing is plausibly benign.

    Mirrors :func:`fin_compat_narrative._llm_enhance` (``anthropic.Anthropic()``,
    claude-3-haiku).  Raises on any failure so the caller can fail-CLOSED (keep the
    row).  NEVER invoked in tests — the caller gates on ``settings.forensic_use_llm``
    (default False) AND ``ANTHROPIC_API_KEY``.
    """
    import json

    import anthropic  # type: ignore[import-untyped]

    client = anthropic.Anthropic()
    prompt = (
        "You review unusual general-ledger counter-account pairings for an auditor. "
        "Given an anchor account and the counter account it was booked against (which "
        "is statistically new or rare for this position), decide whether the pairing is "
        "plausibly BENIGN (a sensible, non-suspicious posting that just happens to be "
        "rare) rather than worth a human review.\n\n"
        f"Anchor account: {anchor_name}\n"
        f"Counter account: {counter_name}\n"
        f"Position (level 3 / level 4): {level_3} / {level_4}\n"
        f"Novelty: {novelty}\n\n"
        'Return JSON only: {"benign": true|false}.'
    )
    msg = client.messages.create(
        model="claude-3-haiku-20240307",
        max_tokens=50,
        messages=[{"role": "user", "content": prompt}],
    )
    resp = json.loads(msg.content[0].text)
    return bool(resp.get("benign"))


def is_growing_other(
    cm_keur: float,
    delta_keur: float,
    growth_pct: float,
    *,
    size_floor_eur: float = SIZE_FLOOR_EUR,
    mom_floor_eur: float = MOM_FLOOR_EUR,
    delta_multiple: float = OTHER_DELTA_MULTIPLE,
    growth_pct_threshold: float = OTHER_GROWTH_PCT_THRESHOLD,
) -> bool:
    """An Other position is flagged when it is material AND clearly, meaningfully
    growing — Phase 2 tightened this from "OR" to "AND".

      * material : ``|cm_keur| >= size_floor``                              (kEUR), AND
      * growing  : ``|delta_keur| >= mom_floor * delta_multiple``           (kEUR), AND
                   ``growth_pct >= growth_pct_threshold``.

    All THREE conditions must hold.  Previously the "growing" half was satisfied by
    EITHER a big absolute move OR a big percentage; now a position must move a
    sizeable absolute amount (``MOM_FLOOR_EUR * OTHER_DELTA_MULTIPLE`` = 30k * 2 =
    60k by default) AND grow by at least ``growth_pct_threshold`` (20%).  This drops
    the noise of tiny-but-high-% buckets and large-but-flat buckets.

    Floors arrive in EUR and are converted to kEUR to compare against the kEUR
    figures.

    WORKED EXAMPLE
    --------------
    Defaults: size_floor 50k, mom_floor 30k, delta_multiple 2 → delta bar 60k,
    growth bar 20%.
      * cm 120k, delta 70k, growth 25%  → material(120≥50) AND delta(70≥60) AND
        growth(25≥20)  → True (flagged).
      * cm 120k, delta 70k, growth 10%  → growth 10 < 20  → False (flat-ish).
      * cm 120k, delta 40k, growth 80%  → delta 40 < 60   → False (small absolute).
      * cm 30k,  delta 70k, growth 25%  → cm 30 < 50      → False (immaterial).
    """
    size_floor_k = size_floor_eur / _EUR_PER_KEUR
    delta_floor_k = (mom_floor_eur * delta_multiple) / _EUR_PER_KEUR
    material = abs(cm_keur) >= size_floor_k
    growing = abs(delta_keur) >= delta_floor_k and growth_pct >= growth_pct_threshold
    return material and growing


def _growth_pct(cm_keur: float, pm_keur: float) -> float:
    """``(cm − pm) / |pm| * 100``; 0.0 when ``pm`` is ~0 (no divide-by-zero)."""
    if abs(pm_keur) < 1e-9:
        return 0.0
    return (cm_keur - pm_keur) / abs(pm_keur) * 100.0


# ---------------------------------------------------------------------------
# (1) Unexpected counter accounts — cache-first, single bounded learner fallback
# ---------------------------------------------------------------------------
def _read_cached_cooccurrence(
    session: Session, entity_prefix: Optional[str],
) -> list[dict[str, Any]]:
    """Read the warmed ``fact_gl_counter_cooccurrence`` cache for ONE entity prefix.

    Returns rows in the same shape :func:`build_cooccurrence` produces so the
    novelty classifier consumes either source identically.  ``entity_prefix`` is a
    resolved 2-char prefix or ``None`` (whole ledger); a bounded indexed read.
    """
    ent_frag = ""
    if entity_prefix is not None:
        safe = str(entity_prefix).replace("'", "")[:2]
        ent_frag = f"WHERE entity_prefix = '{safe}'"
    sql = f"""
        SELECT entity_prefix, acct_ang, counter_ang,
               pair_freq, acct_total_pairs, freq_pct
        FROM fact_gl_counter_cooccurrence
        {ent_frag}
    """
    rows = session.execute(text(sql)).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r._mapping) if hasattr(r, "_mapping") else dict(r)
        out.append({
            "entity_prefix": (d.get("entity_prefix") or "").strip(),
            "acct_ang": (d.get("acct_ang") or "").strip(),
            "counter_ang": (d.get("counter_ang") or "").strip(),
            "pair_freq": int(d.get("pair_freq") or 0),
            "acct_total_pairs": int(d.get("acct_total_pairs") or 0),
            "freq_pct": round(float(d.get("freq_pct") or 0.0), 6),
        })
    return out


def _build_unexpected_counter_accounts(
    session: Session,
    entity: Optional[str],
    *,
    year: int,
    period: int,
    freq_threshold: float = FREQ_THRESHOLD_DEFAULT,
    min_flag_amount_keur: float = MIN_FLAG_AMOUNT_KEUR_DEFAULT,
    entity_prefix: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Signal 1: current-period bookings whose counter account is ``new`` / ``rare``.

    Cache-first: read the warmed ``fact_gl_counter_cooccurrence`` for the resolved
    entity.  ONLY if that is empty for the entity do we fall back to a SINGLE
    bounded :func:`build_cooccurrence` for that one entity (NEVER across all
    entities live).  Then bound to material accounts, pull the current-period
    bookings, derive their counters and classify novelty.

    Phase 2: novelty rows below the materiality floor ``min_flag_amount_keur``
    (``|amount_keur| < floor``) are dropped — only money-moving postings surface.
    Capped at ``MAX_FORENSIC_ROWS`` by ``|amount|`` (a generous intermediate bound;
    the user-facing per-section cap is applied after rollup).
    """
    ep = _scope_prefix(session, entity, entity_prefix)

    cooccurrence = _read_cached_cooccurrence(session, ep)
    if not cooccurrence:
        # Cold cache for this entity → ONE bounded learner for this entity only,
        # excluding the analysed period so a current booking can't self-justify.
        # Scope by the resolved prefix DIRECTLY (passed as entity_prefix) so the
        # learner is fail-closed even when legal_entity_code != entity_prefix.
        cooccurrence = build_cooccurrence(
            session, entity, exclude_year=year, exclude_period=period,
            entity_prefix=ep,
        )

    groups = material_account_groups(
        session, entity, year=year, period=period, entity_prefix=ep,
    )
    bookings = fetch_current_period_bookings(
        session, entity, year=year, period=period, account_groups=groups,
        entity_prefix=ep,
    )
    if not bookings:
        return []

    counter_by_line = derive_counter_accounts(
        session, [b["booking_line_id"] for b in bookings],
    )
    flagged = classify_novelty(
        bookings, cooccurrence, counter_by_line, freq_threshold=freq_threshold,
    )
    # Phase 2 materiality floor: drop the long tail of tiny novelty postings.
    flagged = [
        r for r in flagged
        if abs(float(r.get("amount_keur") or 0.0)) >= min_flag_amount_keur
    ]
    flagged.sort(key=lambda r: abs(r.get("amount_keur") or 0.0), reverse=True)
    return flagged[:MAX_FORENSIC_ROWS]


# ---------------------------------------------------------------------------
# (2) Other positions — material AND growing accounts matched by Other-token
# ---------------------------------------------------------------------------
def _build_other_positions(
    session: Session,
    entity: Optional[str],
    *,
    year: int,
    period: int,
    freq_threshold: float = FREQ_THRESHOLD_DEFAULT,  # unused; kept symmetrical
    entity_prefix: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Signal 2: "Other"-token accounts that are material AND growing.

    One grouped query gives, per account, the presented cm (current month), pm
    (prior month) and py_cm (same month last year) — PL inverted, BS raw, synthetic
    excluded — plus the four level labels.  Python then token-matches the levels and
    applies :func:`is_growing_other`.  Values are kEUR; sorted by ``|delta|`` desc.

    SCOPING RULE (security): scoped by ``fact_gl_line.entity_prefix`` DIRECTLY when
    ``entity_prefix`` is given (see :func:`_scope_prefix`), not via the
    ``legal_entity_code`` lookup, so it is fail-closed for restricted users.
    """
    ep = _scope_prefix(session, entity, entity_prefix)
    ent_frag = entity_sql_fragment(ep, table_alias="l")

    pm_y, pm_m = prior_month(year, period)
    py_y = year - 1

    presented = "CASE WHEN a.level_0 = 'PL' THEN l.amount * -1 ELSE l.amount END"
    sql = f"""
        SELECT
            l.account_number_group               AS acct_ang,
            MAX(a.gl_account_id)                 AS gl_account_id,
            MAX(a.account_name)                  AS account_name,
            MAX(a.level_2)                       AS level_2,
            MAX(a.level_3)                       AS level_3,
            MAX(a.level_4)                       AS level_4,
            MAX(a.l4_sub)                        AS l4_sub,
            COALESCE(SUM(CASE WHEN e.fiscal_year = :cy AND e.fiscal_period = :cp
                              THEN ({presented}) ELSE 0 END), 0) AS cm,
            COALESCE(SUM(CASE WHEN e.fiscal_year = :py1 AND e.fiscal_period = :pp
                              THEN ({presented}) ELSE 0 END), 0) AS pmth,
            COALESCE(SUM(CASE WHEN e.fiscal_year = :pyy AND e.fiscal_period = :cp
                              THEN ({presented}) ELSE 0 END), 0) AS py_cm
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE e.fiscal_period BETWEEN 1 AND 12
          {_NOT_SYNTHETIC}
          {ent_frag}
        GROUP BY l.account_number_group
    """
    params = {
        "cy": int(year), "cp": int(period),
        "py1": int(pm_y), "pp": int(pm_m),
        "pyy": int(py_y),
    }
    rows = session.execute(text(sql), params).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r._mapping) if hasattr(r, "_mapping") else dict(r)
        token = match_other_token(
            d.get("level_2"), d.get("level_3"), d.get("level_4"), d.get("l4_sub"),
        )
        if not token:
            continue
        cm = round(float(d.get("cm") or 0.0) / _EUR_PER_KEUR, 3)
        pmth = round(float(d.get("pmth") or 0.0) / _EUR_PER_KEUR, 3)
        py_cm = round(float(d.get("py_cm") or 0.0) / _EUR_PER_KEUR, 3)
        delta = round(cm - pmth, 3)
        yoy = round(cm - py_cm, 3)
        growth_pct = round(_growth_pct(cm, pmth), 2)
        if not is_growing_other(cm, delta, growth_pct):
            continue
        out.append({
            "gl_account_id": (d.get("gl_account_id") or "").strip(),
            "account_name": (d.get("account_name") or "").strip(),
            "level_path": build_level_path(
                d.get("level_2"), d.get("level_3"),
                d.get("level_4"), d.get("l4_sub"),
            ),
            "balance_cm_keur": cm,
            "balance_pm_keur": pmth,
            "delta_keur": delta,
            "yoy_keur": yoy,
            "growth_pct": growth_pct,
            "matched_token": token,
        })
    out.sort(key=lambda r: abs(r.get("delta_keur") or 0.0), reverse=True)
    return out[:MAX_FORENSIC_ROWS]


# ---------------------------------------------------------------------------
# (3) Suspicious texts — current-period line_note matching a keyword token
# ---------------------------------------------------------------------------
def _build_suspicious_texts(
    session: Session,
    entity: Optional[str],
    *,
    year: int,
    period: int,
    account_groups: Optional[list[str]] = None,
    min_flag_amount_keur: float = MIN_FLAG_AMOUNT_KEUR_DEFAULT,
    entity_prefix: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Signal 3: current-period booking lines whose ``line_note`` hits a keyword.

    Material-bounded (``account_groups`` allow-list) and synthetic-excluded; the
    keyword match itself is done in Python via :func:`match_text_keyword` (whole-word
    matching — see Phase 2) so the DE + EN umlaut variants stay in ONE place (the SQL
    only narrows to non-empty notes in the period).

    Phase 2: suspicious-text rows below the materiality floor ``min_flag_amount_keur``
    (``|amount_keur| < floor``) are dropped.  Capped at ``MAX_FORENSIC_ROWS`` by
    ``|amount|`` (intermediate bound; the user-facing per-section cap is later).
    """
    bookings = fetch_current_period_bookings(
        session, entity, year=year, period=period, account_groups=account_groups,
        entity_prefix=entity_prefix,
    )
    out: list[dict[str, Any]] = []
    for bk in bookings:
        if abs(float(bk.get("amount_keur") or 0.0)) < min_flag_amount_keur:
            continue
        keyword = match_text_keyword(bk.get("line_note"))
        if not keyword:
            continue
        jegn = bk.get("journal_entry_group_number") or ""
        out.append({
            "booking_line_id": bk["booking_line_id"],
            "gl_account_id": bk["gl_account_id"],
            "account_name": bk["account_name"],
            "posting_date": bk["posting_date"],
            "amount_keur": bk["amount_keur"],
            "line_note": bk["line_note"],
            "matched_keyword": keyword,
            "journal_entry_number": jegn[2:] if len(jegn) > 2 else jegn,
        })
    out.sort(key=lambda r: abs(r.get("amount_keur") or 0.0), reverse=True)
    return out[:MAX_FORENSIC_ROWS]


# ---------------------------------------------------------------------------
# Period → anchor (year, month) resolution (mirrors gl_outliers._resolve_anchor)
# ---------------------------------------------------------------------------
def _resolve_anchor(
    period: dict[str, Any],
) -> tuple[Optional[int], Optional[int], str]:
    """Resolve ``period`` → (anchor_year, anchor_month, label).

    Week grain resolves to the ISO week's anchor month (its Thursday's month); year
    grain anchors on month 12.  Mirrors ``gl_outliers._resolve_anchor`` so all three
    GL analyses label the view identically.
    """
    grain = period.get("grain", "month")
    if grain == "week":
        iy, iw = period.get("iso_year"), period.get("iso_week")
        if iy is not None and iw is not None:
            ay, am = plan_anchor_for_week(int(iy), int(iw))
            return ay, am, f"CW{iw}'{str(iy)[-2:]}"
        return None, None, "week"
    year, month = period.get("year"), period.get("month")
    if grain == "year" and year is not None:
        return int(year), 12, f"FY{year}"
    if year is not None and month is not None:
        return int(year), int(month), period_label(int(year), int(month))
    return year, month, ""


# ---------------------------------------------------------------------------
# Public entry point — the forensic payload
# ---------------------------------------------------------------------------
def build_forensic(
    session: Session,
    period: dict[str, Any],
    entity: Optional[str],
    *,
    freq_threshold: float = FREQ_THRESHOLD_DEFAULT,
) -> dict[str, Any]:
    """Build the Phase-4 forensic payload for ``period`` / ``entity``.

    Returns three review lists keyed exactly to the frontend api.ts contract:

      * ``unexpected_counter_accounts`` — current-period bookings whose derived
        counter account is ``new`` or ``rare`` (cache-first co-occurrence; single
        bounded live learner fallback for the entity only).
      * ``other_positions`` — material AND growing accounts whose level labels match
        an Other-token.
      * ``suspicious_texts`` — current-period line notes matching a keyword token.

    PURE READ — builds nothing, writes nothing.  ``ValueError`` is raised when the
    period cannot be resolved to an anchor (year, month); the endpoint maps that to
    a 400.
    """
    ay, am, label = _resolve_anchor(period)
    if ay is None or am is None:
        raise ValueError("forensic analysis requires a resolvable (year, month) period")

    ent = entity
    if ent and str(ent).strip().lower() in ("", "all"):
        ent = None

    unexpected = _build_unexpected_counter_accounts(
        session, ent, year=ay, period=am, freq_threshold=freq_threshold,
    )
    other = _build_other_positions(session, ent, year=ay, period=am)

    groups = material_account_groups(session, ent, year=ay, period=am)
    suspicious = _build_suspicious_texts(
        session, ent, year=ay, period=am, account_groups=groups,
    )

    return {
        "period": {
            "grain": period.get("grain", "month"),
            "year": period.get("year"),
            "month": period.get("month"),
            "iso_year": period.get("iso_year"),
            "iso_week": period.get("iso_week"),
            "label": label,
        },
        "entity": ent or "all",
        "unexpected_counter_accounts": unexpected,
        "other_positions": other,
        "suspicious_texts": suspicious,
        "meta": {
            "freq_threshold_pct": freq_threshold,
            "algorithm_version": ALGORITHM_VERSION,
        },
    }
