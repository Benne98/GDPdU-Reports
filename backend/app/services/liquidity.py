"""Area 2 (Overview v2) — cash development & liquidity ("Zeitverkauf") service.

Backs the P5 Cash & Liquidity block of the Overview redesign
(``docs/overview-v2-redesign-plan.md`` §2 Area 2) and is pinned by
``docs/financial-logic.md`` → "Area 2 — Cash development & liquidity".

APPROVED net-new monetary logic (owner sign-off, decision #3 — do NOT re-open):
the AR *collectibility haircut* interprets Zeitverkauf as receivables realizable
over time = AR net of an aging-based doubtful-debt provision.

================================================================================
FORMULAS (all monetary in **kEUR**)
================================================================================
    CollectibleAR      = Σ_band  AR_band · (1 − AR_HAIRCUT_LADDER[band])
    LiquidityAvailable = Cash + CollectibleAR − OutstandingAP

  * ``Cash``           period-end BS stock over level_3 = 'Cash & cash
                       equivalents', RAW stored sign (an overdraft is legitimately
                       negative — NO ABS, NO *-1).  Reuses the canonical P3
                       cash-headline query (:func:`overview_summary._cash_headline`);
                       that query returns EUR, so we scale by 1/1000 → kEUR.
  * ``AR_band``        per-band open AR from the OPOS FIFO as-of aging
                       (:func:`opos_aging.build_receivables_aging_opos` ``series``,
                       already kEUR).  A partner whose net-open is a CREDIT is
                       ``total_open=0`` upstream (``credit_balance=True``) so it
                       never adds a negative "collectible".  As belt-and-suspenders
                       the pure helper ALSO floors a negative band input to 0 and
                       flags it.
  * ``OutstandingAP``  total open AP magnitude (F5) from
                       :func:`opos_aging.build_payables_aging_opos` ``total_payables``
                       (already kEUR, positive magnitude).

Favorability: cash and liquidity_available are ``FAV+`` (higher is better),
``invert_delta=False``.

=== WORKED EXAMPLE (to the cent) =============================================
    AR bands (not_yet_due / 1-30 / 31-60 / 61-90 / 91-180 / >180)
             = 400 / 100 / 50 / 40 / 20 / 10  (kEUR)
    haircut  =   0 /   0 / .10 / .25 / .50 / 1.0
      CollectibleAR = 400 + 100 + 50·.9 + 40·.75 + 20·.5 + 10·0
                    = 400 + 100 + 45 + 30 + 10 + 0 = 585
    Cash = 120, OutstandingAP = 300
      LiquidityAvailable = 120 + 585 − 300 = 405 kEUR

=== EDGE CASES ===============================================================
  * empty AR                     → CollectibleAR 0
  * all >180                     → CollectibleAR 0 (100 % haircut)
  * credit-balance band (< 0)    → that band contributes 0, ``credit_flag=True``
  * negative Cash (overdraft)    → flows through raw (liquidity may be negative —
                                   surface, do NOT floor)
  * missing AP                   → treated as 0
  * a band id absent from ladder → **raise** ``KeyError`` (never default a haircut)

================================================================================
SECURITY — MANDATORY fail-closed tenant isolation
================================================================================
``allowed_entities`` is the visibility boundary ALREADY mapped to the 2-char
``entity_prefix`` set (the endpoint — NOT this service — resolves visibility →
prefixes and passes it in), with identical semantics to the P3 overview summary:

    None       → admin / unrestricted (no entity filter)
    empty set  → FAIL-CLOSED → a zeroed structure, NO cross-entity data, no 500
    non-empty  → EVERY sub-query is filtered to ``entity_prefix IN <set>``

The optional ``entity`` narrows WITHIN that boundary; a narrow to a prefix outside
the visible set fails closed (zeroed).  Resolution reuses the P3
:func:`overview_summary._effective_prefixes` so the contract can never drift.
PURE READ — builds nothing, writes nothing.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.gl_aging import AR_BANDS  # canonical bucket ids — REUSE, never invent
from app.services.opos_aging import (
    build_payables_aging_opos,
    build_receivables_aging_opos,
)
from app.config import settings
from app.services import mart_overview
from app.services.overview_summary import (
    _cash_allowed_prefixes,
    _cash_ent_frag,
    _cash_headline,
    _effective_prefixes,
)

# ===========================================================================
# CONFIG — approved AR collectibility haircut ladder (NOT inline constants).
# Keyed to the canonical ``gl_aging.AR_BANDS`` bucket ids; each rate is the
# fraction of that band's open AR assumed UN-collectible.
# ===========================================================================
AR_HAIRCUT_LADDER: dict[str, float] = {
    "not_yet_due":      0.00,  # current — fully collectible
    "overdue_1_30":     0.00,  # ≤30 days late — still fully collectible
    "overdue_31_60":    0.10,  # 10 % doubtful
    "overdue_61_90":    0.25,  # 25 % doubtful
    "overdue_91_180":   0.50,  # 50 % doubtful
    "overdue_over_180": 1.00,  # >180 days — written off, 0 collectible
}

# Fail-fast invariant: the ladder MUST cover every canonical band exactly (F4 rule
# — reuse the canonical band set, never invent).  Guard at import so a schema drift
# in AR_BANDS can never silently default a haircut.
_CANONICAL_BANDS: tuple[str, ...] = tuple(b for b, _ in AR_BANDS)
_missing = set(_CANONICAL_BANDS) - set(AR_HAIRCUT_LADDER)
if _missing:  # pragma: no cover - config invariant
    raise RuntimeError(
        f"AR_HAIRCUT_LADDER missing a haircut for canonical bands: {sorted(_missing)}"
    )
_BAND_LABELS: dict[str, str] = {b: lbl for b, lbl in AR_BANDS}


# ===========================================================================
# PURE contract helpers (DB-free) — the signed-off math lock
# ===========================================================================
def collectible_breakdown(
    ar_bands: dict[str, float], ladder: dict[str, float] = AR_HAIRCUT_LADDER,
) -> tuple[list[dict[str, Any]], float, float]:
    """Per-band collectibility breakdown → ``(rows, raw_total, collectible_total)``.

    ``rows`` are in canonical band order, each::

        {band, label, raw, haircut, collectible, credit_flag}

    A band absent from ``ladder`` raises ``KeyError`` (never default a haircut).
    A negative (credit) band input contributes 0 collectible and is flagged
    (``credit_flag=True``) — defence in depth; OPOS already floors credit partners
    to 0 upstream.  Unit-agnostic (caller passes a single consistent unit).
    """
    rows: list[dict[str, Any]] = []
    raw_total = 0.0
    coll_total = 0.0
    # Emit in canonical order first, then any extra (unknown) bands so the KeyError
    # still fires for ids that are not in the ladder.
    ordered = [b for b in _CANONICAL_BANDS if b in ar_bands]
    ordered += [b for b in ar_bands if b not in _CANONICAL_BANDS]
    for band in ordered:
        if band not in ladder:
            raise KeyError(f"no haircut configured for aging band {band!r}")
        raw = float(ar_bands[band] or 0.0)
        credit_flag = raw < 0.0
        collectible = 0.0 if credit_flag else raw * (1.0 - ladder[band])
        rows.append({
            "band": band,
            "label": _BAND_LABELS.get(band, band),
            "raw": round(raw, 6),
            "haircut": ladder[band],
            "collectible": round(collectible, 6),
            "credit_flag": credit_flag,
        })
        raw_total += raw
        coll_total += collectible
    return rows, round(raw_total, 6), round(coll_total, 6)


def compute_collectible_ar(
    ar_bands: dict[str, float], ladder: dict[str, float] = AR_HAIRCUT_LADDER,
) -> float:
    """``CollectibleAR = Σ_band AR_band·(1−haircut)`` (credit bands floored to 0)."""
    _, _, coll = collectible_breakdown(ar_bands, ladder)
    return coll


def compute_liquidity_available(
    *, cash: float, ar_bands: dict[str, float], outstanding_ap: float,
    ladder: dict[str, float] = AR_HAIRCUT_LADDER,
) -> float:
    """``LiquidityAvailable = Cash + CollectibleAR − OutstandingAP``.

    Unit-agnostic: the caller passes cash / bands / AP in ONE consistent unit and
    gets the result back in that unit.  Cash is NOT floored (overdraft flows
    through and may make liquidity negative — surface it, do not hide it).
    """
    return round(
        float(cash) + compute_collectible_ar(ar_bands, ladder) - float(outstanding_ap),
        6,
    )


# ===========================================================================
# DB read layer
# ===========================================================================
def _representative_codes(session: Session, prefixes: set[str]) -> list[str]:
    """One representative ``legal_entity_code`` per visible ``entity_prefix``.

    The OPOS aging builders filter by prefix (derived from the code), so exactly
    ONE code per prefix reproduces the whole prefix WITHOUT double-counting when a
    prefix maps to several codes.  A prefix with no code maps to nothing → that
    prefix contributes 0 (conservative under-count, never a leak).
    """
    if not prefixes:
        return []
    rows = session.execute(
        text(
            "SELECT DISTINCT ON (entity_prefix) legal_entity_code, entity_prefix "
            "FROM dim_legal_entity WHERE entity_prefix = ANY(:prefixes) "
            "ORDER BY entity_prefix, legal_entity_code"
        ),
        {"prefixes": sorted(prefixes)},
    ).fetchall()
    return [r[0] for r in rows if r and r[0]]


def _aging_scope(
    session: Session, *, year: int, month: int,
    eff: Optional[set[str]], builder_entity: Optional[str],
) -> tuple[dict[str, float], float]:
    """Per-band open AR (kEUR) + total open AP (kEUR) over the visible scope.

    ``eff is None`` (admin) → ONE builder call with ``builder_entity`` (a
    legal_entity_code or None=all).  ``eff`` a non-empty prefix set → one builder
    call per visible prefix (representative code), band-summed.  Reuses the OPOS
    builders verbatim (no behaviour change); their ``series``/``total_payables``
    are already kEUR.
    """
    if eff is None:
        codes: list[Optional[str]] = [builder_entity]
    else:
        codes = list(_representative_codes(session, eff))

    ar_bands = {b: 0.0 for b in _CANONICAL_BANDS}
    ap_total = 0.0
    for code in codes:
        ar = build_receivables_aging_opos(session, year, month, code)
        for s in ar.get("series") or []:
            band = s.get("band")
            if band in ar_bands:
                ar_bands[band] += float(s.get("amount") or 0.0)
        ap = build_payables_aging_opos(session, year, month, code)
        ap_total += float(ap.get("total_payables") or 0.0)
    return {b: round(v, 2) for b, v in ar_bands.items()}, round(ap_total, 2)


def _zeroed_liquidity(
    *, entity: Optional[str], year: int, month: int, reason: str,
) -> dict[str, Any]:
    """Fail-closed structure — same shape as the happy path, all zeros."""
    rows, raw_ar, coll_ar = collectible_breakdown({b: 0.0 for b in _CANONICAL_BANDS})
    return {
        "meta": {
            "year": year, "month": month, "entity": entity,
            "source": "fail_closed", "reason": reason, "unit": "kEUR",
            "fav": {"cash": "FAV+", "liquidity_available": "FAV+"},
            "invert_delta": False,
        },
        "cash": 0.0,
        "ar_bands": rows,
        "raw_ar": raw_ar,
        "collectible_ar": coll_ar,
        "outstanding_ap": 0.0,
        "liquidity_available": 0.0,
    }


def build_liquidity_available(
    session: Session, *,
    entity: Optional[str], year: int, month: int,
    allowed_entities: Optional[set[str]],
    use_mart: Optional[bool] = None,
) -> dict[str, Any]:
    """Assemble the Area-2 liquidity structure (see module docstring). Units kEUR.

    ``allowed_entities`` is the ``entity_prefix`` visibility set (None=admin,
    empty=fail-closed, non-empty=filter).  Return shape::

        {
          meta: {year, month, entity, source, unit:'kEUR', fav, invert_delta},
          cash:                 kEUR (period-end BS stock, raw sign),
          ar_bands: [ {band, label, raw, haircut, collectible, credit_flag}, ...],
          raw_ar:               Σ per-band raw open AR (kEUR),
          collectible_ar:       Σ per-band collectible (kEUR),
          outstanding_ap:       total open AP magnitude (kEUR),
          liquidity_available:  cash + collectible_ar − outstanding_ap (kEUR),
        }
    """
    # FAIL-CLOSED gate (enforcement point #1): empty visibility, or an ``entity``
    # narrow outside the visible set → zeroed, no sub-query runs.
    eff, builder_entity, denied = _effective_prefixes(
        session, entity=entity, allowed_prefixes=allowed_entities,
    )
    if denied:
        return _zeroed_liquidity(
            entity=entity, year=year, month=month, reason="fail_closed_visibility",
        )

    # Cash — canonical P3 headline query, RAW EUR → kEUR (enforcement point #2:
    # ``_cash_ent_frag`` filters the cash query to ``eff``; empty eff → "AND 1=0").
    # Under the mart flag+freshness gate the cash TERM ONLY is served from the
    # snapshot (byte-equivalent to _cash_headline); AR/AP aging stays LIVE below.
    use_mart = settings.overview_summary_use_mart if use_mart is None else use_mart
    if use_mart and mart_overview.mart_is_fresh(
        session, mart_overview.latest_gl_load_id(session)
    ):
        cash_eur = mart_overview.read_cash_headline(
            session, year=year, month=month,
            allowed_entities=_cash_allowed_prefixes(session, eff, builder_entity),
        )["level"]
    else:
        cash_eur = _cash_headline(
            session, year=year, month=month,
            ent_frag=_cash_ent_frag(eff, builder_entity, session),
        )["level"]
    cash = round(float(cash_eur) / 1000.0, 2)

    # AR/AP — OPOS builders scoped to the visible prefixes (enforcement point #3).
    ar_bands, outstanding_ap = _aging_scope(
        session, year=year, month=month, eff=eff, builder_entity=builder_entity,
    )

    rows, raw_ar, collectible_ar = collectible_breakdown(ar_bands)
    liquidity_available = round(cash + collectible_ar - outstanding_ap, 2)

    return {
        "meta": {
            "year": year, "month": month, "entity": entity,
            "source": "opos+gl", "unit": "kEUR",
            "visibility": ("admin" if eff is None else "restricted"),
            "fav": {"cash": "FAV+", "liquidity_available": "FAV+"},
            "invert_delta": False,
        },
        "cash": cash,
        "ar_bands": rows,
        "raw_ar": round(raw_ar, 2),
        "collectible_ar": round(collectible_ar, 2),
        "outstanding_ap": outstanding_ap,
        "liquidity_available": liquidity_available,
    }
