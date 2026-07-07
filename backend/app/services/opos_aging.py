"""OPOS subledger as-of (Stichtag) aging — Phase-2 backend helper.

This module is the production home of the AR/AP OPOS as-of aging documented in
``docs/financial-logic.md`` → "AR/AP OPOS As-of Aging (F3/F4) — 2026-07-01" and
pinned by ``backend/tests/test_opos_aging_financial.py``.

Two layers:

1. **Pure contract helper** :func:`compute_opos_aging` — DB-free, operates on plain
   row dicts.  It MUST agree with the executable reference
   ``_ref_compute_opos_aging`` in the test module (Method-A net-open per partner →
   FIFO oldest-first bucketing → overdue_open/total_open → clamped overdue_pct →
   credit_balance flag + scatter exclusion).  Do not change its behaviour without
   updating the spec + reference together.

2. **DB read layer** (``fetch_opos_rows`` + the ``build_*`` builders) — fetches
   ``fact_opos_debitor`` / ``fact_opos_kreditor`` rows for a Stichtag and shapes
   them into the aging / register / by-dimension / concentration / geo / portfolio
   responses consumed by the ported Sales-aging UI.  Every derived figure flows
   through :func:`compute_opos_aging`, so the read path can never diverge from the
   financial contract.

As-of predicate (F1 Method A, spec rule 2) — **fiscal-year-anchored**::

    WHERE fy_label = year(S) AND (month(S) = 12 OR posting_date <= S)

For a full-year (December) Stichtag the posting_date clamp is dropped so the tie
matches reconciliation.xlsx *fy_label membership* — this is what keeps the
~7.6k FY-2022 Novara rows whose ``Buchungsdatum`` is corrupt-stamped 2024.  For a
partial YTD Stichtag (e.g. 2025-07-31) the posting_date clamp IS applied.  Because
:func:`compute_opos_aging` itself re-clamps ``buchungsdatum <= as_of`` (per the
reference), the read layer normalises the effective ``buchungsdatum`` of full-year
rows to ``min(buchungsdatum, as_of)`` so the corrupt-future-dated rows still enter
the Method-A magnitude (they are Vortrag/carry rows, never RV/RG, so they never
distort the FIFO invoice pool — they only add to the net-open, and any uncovered
residual buckets to overdue_over_180 per A5).
"""
from __future__ import annotations

import calendar
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, timedelta
from typing import Any, Iterable, Iterator, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.config import settings
from app.services.entities import ENTITY_BUKRS, ENTITY_PREFIX
from app.services.fin_compat_sql import resolve_entity_prefixes
from app.services.gl_aging import AR_BANDS  # reuse the canonical bucket set (F4 rule)

_BANDS = tuple(b for b, _ in AR_BANDS)

# README payment terms — customer (AR) 30d, supplier (AP) 45d.  These are the
# CORRECTED terms; the legacy gl_aging.py hardcoded the inverse (dso=45/dpo=30).
# They are used for (a) due-date derivation (due = buchungsdatum + terms) and
# (b) the DSO/DPO *fallback proxy* (F4) when the real period flow is unavailable.
_TERMS = {"AR": 30, "AP": 45}

# F4 REAL DSO/DPO — period-flow denominators.
#   DSO = total_AR_open / gross_sales_period × days_in_period   (fact_sales)
#   DPO = total_AP_open / purchases_period   × days_in_period   (GL-derived)
# The DPO purchase base is NOT a dedicated purchase journal (none exists in v5):
# it is the sum of the P&L "Cost of materials" postings (materials + purchased
# services).  The account set is derived at RUNTIME from the dim_pl_structure
# mapping row(s) below — never hardcoded account codes — so it tracks the loaded
# PL structure (level_3 = "Cost of materials", covering both level_4 buckets).
_PURCHASE_LINE_CODES: tuple[str, ...] = ("COST_OF_MATERIALS",)

# Upper clamp on real DSO/DPO so a pathological open/flow ratio (tiny period flow
# vs a large opening balance) can never render an absurd figure.  10 years is far
# beyond any real trade cycle; hitting it flags a data problem, not a real value.
_MAX_FLOW_DAYS = 3650.0

# LuL trade-account scope (F5 / reconcile_opos.py account-map — NOT a naive konto
# prefix).  23500/23502/35500 are LuL too; omitting 23500 is the Meridian-2024
# ~1933 EUR break.  Stored as strings to match the VARCHAR konto column.
AR_LUL_KONTOS: tuple[str, ...] = ("23500", "23502", "24000", "24905")
AP_LUL_KONTOS: tuple[str, ...] = ("35500", "36000", "36905")

_SIDE: dict[str, dict[str, str]] = {
    "AR": {"table": "fact_opos_debitor", "dim": "dim_customer", "id": "customer_id"},
    "AP": {"table": "fact_opos_kreditor", "dim": "dim_supplier", "id": "supplier_id"},
}


# --------------------------------------------------------------------------- #
# Entity-visibility scope + shared TTL cache for the OPOS read path
# --------------------------------------------------------------------------- #
# The financial-owned builders (build_*_aging_opos) call ``_partner_view`` with a
# FIXED 5-arg signature and must not be touched.  We therefore thread the per-user
# entity-prefix visibility boundary through a ContextVar (set by the aging
# endpoints via :func:`aging_visibility`) rather than a new parameter, and wrap
# ``_partner_view`` with a visibility-aware TTL cache.  States of the scope value:
#   * ``None``           — admin / unrestricted → all entities (legacy behaviour).
#   * empty ``frozenset``— fail-closed deny-all → zeroed view, NO SQL issued.
#   * non-empty          — restrict OPOS rows to these 2-char entity_prefixes.
# The DEFAULT is ``None`` so any legacy / test caller that never sets the scope
# (e.g. the financial golden tests) keeps the exact pre-existing all-entities read.
_ALLOWED_PREFIXES: ContextVar[Optional[frozenset[str]]] = ContextVar(
    "opos_allowed_prefixes", default=None
)

# key = (side, year, month, entity|None, visibility_hash) → (expiry_monotonic, view)
_CACHE: dict[tuple, tuple[float, dict[str, Any]]] = {}
# Upper bound on distinct cache keys (side × Stichtag × entity × visibility-hash).
# On overflow the write path first drops expired entries, then clears wholesale if
# still over — bounds memory without touching the visibility-hash keying.
_CACHE_MAXSIZE = 512


def _allowed_hash(allowed: Optional[frozenset[str]]) -> str:
    """Stable cache-key component so cached views never cross a visibility boundary."""
    if allowed is None:
        return "admin"
    if not allowed:
        return "deny"
    return ",".join(sorted(allowed))


@contextmanager
def aging_visibility(allowed: Optional[Iterable[str]]) -> Iterator[None]:
    """Bind the entity-prefix visibility scope for the enclosed aging builder calls.

    ``allowed`` is ``None`` (admin / unrestricted → all entities), an empty
    iterable (fail-closed deny → zeroed view, no SQL) or the set of 2-char
    ``entity_prefix`` values the caller may see.  Threaded via a ContextVar so the
    financial-owned build_* / ``_partner_view`` signatures stay UNTOUCHED.
    """
    norm = None if allowed is None else frozenset(str(p)[:2] for p in allowed if p)
    token = _ALLOWED_PREFIXES.set(norm)
    try:
        yield
    finally:
        _ALLOWED_PREFIXES.reset(token)


def clear_aging_cache() -> None:
    """Test seam — drop all memoised _partner_view results."""
    _CACHE.clear()


# --------------------------------------------------------------------------- #
# (1) PURE CONTRACT HELPER — must equal test_opos_aging_financial._ref_*
# --------------------------------------------------------------------------- #
def _due_date(row: dict, terms_days: int) -> date:
    """due = COALESCE(nettofaelligkeit, buchungsdatum + terms)."""
    return row["nettofaelligkeit"] or (row["buchungsdatum"] + timedelta(days=terms_days))


def _band_of(due: date, as_of: date) -> str:
    """Mirror gl_aging._band_case_sql boundaries EXACTLY (Postgres → Python).

    ``due == as_of`` (0 days) falls through to overdue_over_180 (the BETWEEN
    ranges start at 1), deliberately mirrored so Python and SQL bucket alike.
    """
    if due > as_of:
        return "not_yet_due"
    d = (as_of - due).days
    if 1 <= d <= 30:
        return "overdue_1_30"
    if 31 <= d <= 60:
        return "overdue_31_60"
    if 61 <= d <= 90:
        return "overdue_61_90"
    if 91 <= d <= 180:
        return "overdue_91_180"
    return "overdue_over_180"


def compute_opos_aging(rows: list[dict], as_of: date, side: str = "AR") -> dict[str, dict]:
    """FIFO as-of aging per partner (F3/F4) — the Phase-2 acceptance gate.

    See module docstring / docs financial-logic F1-F3.  Returns, per
    ``partner_key``::

        {total_open, overdue_open, overdue_pct, credit_balance, in_scatter, buckets}

    all figures in the SAME currency units as ``betrag`` (EUR, Hauswaehrung).
    """
    sign = 1.0 if side == "AR" else -1.0
    terms = _TERMS[side]

    groups: dict[str, float] = {}
    for r in rows:
        if r["fy_label"] == as_of.year and r["buchungsdatum"] <= as_of:
            groups.setdefault(r["partner_key"], 0.0)
            groups[r["partner_key"]] += float(r["betrag"]) * sign

    out: dict[str, dict] = {}
    for partner, mag in groups.items():
        mag = round(mag, 2)
        buckets = {b: 0.0 for b in _BANDS}

        if mag < 0:  # credit balance — overpayment / advance / net credit note
            out[partner] = {
                "total_open": 0.0, "overdue_open": 0.0, "overdue_pct": 0.0,
                "credit_balance": True, "in_scatter": False, "buckets": buckets,
            }
            continue

        invs = [
            r for r in rows
            if r["partner_key"] == partner and r["belegart"] in ("RV", "RG")
            and r["fy_label"] == as_of.year and r["buchungsdatum"] <= as_of
        ]
        invs.sort(key=lambda r: _due_date(r, terms))

        remaining = mag
        for r in invs:
            if remaining <= 1e-9:
                break
            slice_amt = min(remaining, abs(float(r["betrag"])))
            buckets[_band_of(_due_date(r, terms), as_of)] += slice_amt
            remaining -= slice_amt

        # A5: net-open that current-FY RV/RG invoices cannot cover is the carried
        # forward opening (Vortrag, Belegart SV — outside the FIFO pool).  It
        # predates the fiscal year → long-aged → overdue_over_180.
        if remaining > 1e-9:
            buckets["overdue_over_180"] += remaining

        total = round(sum(buckets.values()), 2)
        overdue = round(sum(v for b, v in buckets.items() if b != "not_yet_due"), 2)
        pct = 0.0 if total <= 1e-9 else max(0.0, min(100.0, 100.0 * overdue / total))
        out[partner] = {
            "total_open": total, "overdue_open": overdue,
            "overdue_pct": round(pct, 1),
            "credit_balance": False, "in_scatter": True,
            "buckets": {b: round(v, 2) for b, v in buckets.items()},
        }
    return out


def clamp_pct(overdue: float, total: float) -> float:
    """Belt-and-suspenders page-level clamp (F3 rule 3-iii)."""
    if total <= 1e-9:
        return 0.0
    return round(max(0.0, min(100.0, 100.0 * overdue / total)), 1)


def term_proxy_days(side: str) -> int:
    """DSO/DPO fallback proxy (Assumption A2) — AR 30d, AP 45d (corrected)."""
    return _TERMS[side]


# --------------------------------------------------------------------------- #
# (2) DB READ LAYER
# --------------------------------------------------------------------------- #
def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _entity_name(bukrs: Any, prefix: Any) -> Optional[str]:
    """Full entity name from buchungskreis (preferred) or entity_prefix.

    NEVER returns a bare prefix like "02" — falls back to None so callers can
    decide, keeping the "never show the prefix" rule.
    """
    try:
        if bukrs is not None and str(bukrs).strip() != "":
            return ENTITY_BUKRS.get(int(bukrs))
    except (TypeError, ValueError):
        pass
    if prefix is not None:
        return ENTITY_PREFIX.get(str(prefix).strip().zfill(2))
    return None


def _contact(name1: Any, name2: Any, postal: Any, city: Any) -> Optional[str]:
    """One display string: name_line_1 + name_line_2 + postal_code + city."""
    parts = [str(x).strip() for x in (name1, name2, postal, city) if x and str(x).strip()]
    return " · ".join(parts) if parts else None


def fetch_opos_rows(
    session: Session,
    side: str,
    year: int,
    month: int,
    entity: Optional[str] = None,
    allowed: Optional[frozenset[str]] = None,
) -> tuple[list[dict], dict[str, dict]]:
    """Fetch OPOS rows for a Stichtag + per-partner dim metadata.

    Returns ``(rows, meta)`` where ``rows`` feed :func:`compute_opos_aging`
    (keys: partner_key, konto, satzart, belegart, beleg_no, betrag,
    buchungsdatum, nettofaelligkeit, fy_label, entity, entity_prefix) and
    ``meta[partner_key]`` carries display fields (name, contact, country_code,
    region_code, city, entity name/prefix).

    ``allowed`` is the entity-visibility boundary: ``None`` = unrestricted (admin
    / legacy), a set of 2-char ``entity_prefix`` values otherwise.  The set is
    intersected with any single-entity ``entity`` narrow; an empty intersection
    fails closed (no rows, no cross-entity leak).
    """
    cfg = _SIDE[side]
    kontos = list(AR_LUL_KONTOS if side == "AR" else AP_LUL_KONTOS)
    as_of = _month_end(year, month)

    # ``entity`` may be a single legal_entity_code OR a comma-separated list
    # (multi-entity / consolidated-subset filter). ``resolve_entity_prefixes``
    # returns [] for ''/'all'/None (no narrow), one prefix for a single code, or
    # many prefixes for a comma-list.
    eps = [str(e)[:2] for e in resolve_entity_prefixes(session, entity)]
    ent_frag = ""
    params: dict[str, Any] = {"fy": str(year), "kontos": kontos}
    if allowed is not None:
        # Visibility-scoped read: restrict to the allowed entity_prefix set,
        # intersected with any entity narrow already resolved from ``entity``.
        safe = sorted({str(p).replace("'", "")[:2] for p in allowed if p})
        if eps:
            narrow = set(eps)
            safe = [p for p in safe if p in narrow]
        if not safe:
            return [], {}  # fail closed (defensive; the endpoint pre-resolves this)
        ent_frag = "AND o.entity_prefix = ANY(:allowed)"
        params["allowed"] = safe
    elif len(eps) == 1:
        ent_frag = "AND o.entity_prefix = :ep"
        params["ep"] = eps[0]
    elif len(eps) > 1:
        ent_frag = "AND o.entity_prefix = ANY(:eps)"
        params["eps"] = eps

    asof_frag = ""
    if month != 12:
        asof_frag = "AND o.posting_date <= :as_of"
        params["as_of"] = as_of.isoformat()

    sql = text(f"""
        SELECT o.entity_prefix, o.buchungskreis, o.partner_key, o.konto,
               o.satzart, o.belegart, o.beleg_no,
               o.net_due_date, o.amount_hauswaehrung, o.posting_date, o.fy_label,
               d.name_line_1, d.name_line_2, d.postal_code, d.city,
               d.country_code, d.region_code
        FROM {cfg['table']} o
        LEFT JOIN {cfg['dim']} d ON d.{cfg['id']} = o.partner_key
        WHERE o.project_id = 'default'
          AND o.fy_label::text = :fy
          AND o.konto = ANY(:kontos)
          {ent_frag}
          {asof_frag}
    """)
    raw = session.execute(sql, params).fetchall()

    rows: list[dict] = []
    meta: dict[str, dict] = {}
    for r in raw:
        m = r._mapping
        pk = m["partner_key"]
        if pk is None:
            continue

        bd = m["posting_date"]
        if isinstance(bd, str):
            try:
                bd = date.fromisoformat(bd[:10])
            except ValueError:
                bd = None
        if bd is None:
            bd = as_of
        # Full-year normalisation (see module docstring): keep corrupt future
        # dates inside the Method-A magnitude by clamping to the Stichtag.
        if month == 12 and bd > as_of:
            bd = as_of

        due = m["net_due_date"]
        if isinstance(due, str):
            try:
                due = date.fromisoformat(due[:10])
            except ValueError:
                due = None

        try:
            fy = int(str(m["fy_label"]).strip())
        except (TypeError, ValueError):
            continue

        rows.append({
            "partner_key": pk,
            "entity_prefix": m["entity_prefix"],
            "entity": _entity_name(m["buchungskreis"], m["entity_prefix"]),
            "konto": m["konto"],
            "satzart": m["satzart"],
            "belegart": m["belegart"],
            "beleg_no": m["beleg_no"],
            "betrag": float(m["amount_hauswaehrung"] or 0.0),
            "buchungsdatum": bd,
            "nettofaelligkeit": due,
            "fy_label": fy,
        })
        if pk not in meta:
            meta[pk] = {
                "name": (m["name_line_1"] or "").strip() or pk,
                "contact": _contact(m["name_line_1"], m["name_line_2"],
                                    m["postal_code"], m["city"]),
                "country_code": (str(m["country_code"]).strip() or None) if m["country_code"] else None,
                "region_code": (str(m["region_code"]).strip() or None) if m["region_code"] else None,
                "city": (str(m["city"]).strip() or None) if m["city"] else None,
                "postal_code": (str(m["postal_code"]).strip() or None) if m["postal_code"] else None,
                "entity": _entity_name(m["buchungskreis"], m["entity_prefix"]),
                "entity_prefix": m["entity_prefix"],
            }
    return rows, meta


def _empty_partner_view(year: int, month: int) -> dict[str, Any]:
    """Fail-closed zeroed view (no partners, no rows) — issues NO SQL."""
    return {
        "as_of": _month_end(year, month),
        "aging": {},
        "meta": {},
        "rows": [],
        "signed_total_eur": 0.0,
        "open_documents": 0,
    }


def _partner_view(
    session: Session, side: str, year: int, month: int, entity: Optional[str],
) -> dict[str, Any]:
    """Central OPOS computation: per-partner aging + meta + signed Method-A total.

    All builders derive from this so the read path is single-sourced.  This is the
    visibility-aware TTL-cache seam:  the enclosing entity-prefix scope (bound by
    :func:`aging_visibility`) is folded into the cache key so cached views never
    cross a visibility boundary, and a single Sales-aging page load's ~8 same-
    Stichtag builders collapse from ~8 full-FY OPOS scans to 1.  An empty
    (fail-closed) scope short-circuits to :func:`_empty_partner_view` with NO SQL.

    Signature is FIXED (session, side, year, month, entity) — the financial-owned
    build_* callers and the golden tests monkeypatch this exact shape.
    """
    allowed = _ALLOWED_PREFIXES.get()
    # Fail closed: an explicit empty visibility set → zeroed view, NO SQL issued.
    if allowed is not None and not allowed:
        return _empty_partner_view(year, month)

    ttl = settings.aging_cache_ttl_s
    cache_key = (side, year, month, (entity or None), _allowed_hash(allowed))
    if ttl > 0:
        hit = _CACHE.get(cache_key)
        if hit and hit[0] > time.monotonic():
            return hit[1]

    view = _partner_view_uncached(session, side, year, month, entity, allowed)
    if ttl > 0:
        if len(_CACHE) >= _CACHE_MAXSIZE:
            now = time.monotonic()
            for k in [k for k, (exp, _) in _CACHE.items() if exp <= now]:
                del _CACHE[k]
            if len(_CACHE) >= _CACHE_MAXSIZE:
                _CACHE.clear()
        _CACHE[cache_key] = (time.monotonic() + ttl, view)
    return view


def _partner_view_uncached(
    session: Session, side: str, year: int, month: int,
    entity: Optional[str], allowed: Optional[frozenset[str]],
) -> dict[str, Any]:
    """Uncached body of :func:`_partner_view` (single OPOS fetch + aggregation).

    Amounts in ``per_partner`` are EUR (Hauswaehrung); ``signed_total_eur`` is the
    signed Method-A net-open (positive for AR, negative for AP).
    """
    rows, meta = fetch_opos_rows(session, side, year, month, entity, allowed)
    as_of = _month_end(year, month)
    aging = compute_opos_aging(rows, as_of, side)

    signed_total_eur = sum(float(r["betrag"]) for r in rows)  # Method-A F5 total
    # Per-partner distinct open invoice documents (RV/RG with a beleg_no).  The
    # union feeds the aggregate KPI; the per-partner counts drive the register
    # drill-down expand arrow (frontend gates expand on open_documents > 0).
    docs_by_partner: dict[str, set] = {}
    for r in rows:
        if r["belegart"] in ("RV", "RG") and r["beleg_no"]:
            docs_by_partner.setdefault(r["partner_key"], set()).add(r["beleg_no"])
    open_docs = len({b for s in docs_by_partner.values() for b in s})

    per_partner: dict[str, dict] = {}
    for pk, a in aging.items():
        info = meta.get(pk, {})
        per_partner[pk] = {
            **a, "partner_key": pk, "meta": info,
            "open_documents": len(docs_by_partner.get(pk, ())),
        }

    return {
        "as_of": as_of,
        "aging": per_partner,
        "meta": meta,
        "rows": rows,
        "signed_total_eur": signed_total_eur,
        "open_documents": open_docs,
    }


def _agg_buckets(aging: dict[str, dict]) -> dict[str, float]:
    out = {b: 0.0 for b in _BANDS}
    for a in aging.values():
        for b in _BANDS:
            out[b] += a["buckets"].get(b, 0.0)
    return out


def _gross_open_eur(v: dict[str, Any]) -> float:
    """Gross open aging basis in EUR — Σ positive-partner FIFO buckets.

    This is the SAME total the aging view headlines (``total_open_gross`` =
    before_due + overdue; credit-balance partners floored to 0 in
    :func:`compute_opos_aging`), so it is the F4 DSO/DPO numerator that reconciles
    with the displayed AR/AP.  NOT the net Method-A ``signed_total_eur`` (which
    nets credit balances DOWN and cannot be tied back to the aging table).
    """
    return sum(_agg_buckets(v["aging"]).values())


# --------------------------------------------------------------------------- #
# F4 — REAL DSO / DPO (period-flow denominators) with proxy fallback
# --------------------------------------------------------------------------- #
def _days_in_fy_to_date(year: int, month: int) -> int:
    """Calendar days from the fiscal-year start (Jan 1) to the Stichtag month-end.

    Matches the aging Stichtag's period window (fy_label = year, posting_date <=
    Stichtag): a December Stichtag → the whole fiscal year (365/366), a partial
    YTD Stichtag (e.g. month=7) → Jan 1 .. Jul 31 (212).  ``+1`` makes the span
    inclusive of both endpoints.
    """
    return (_month_end(year, month) - date(year, 1, 1)).days + 1


def _safe_rollback(session: Session) -> None:
    """Best-effort rollback so a failed flow query never leaves an aborted tx."""
    try:
        session.rollback()
    except Exception:  # noqa: BLE001 — rollback is itself best-effort
        pass


def _effective_prefixes(
    session: Session, entity: Optional[str], allowed: Optional[frozenset[str]],
) -> Optional[list[str]]:
    """Resolve the effective entity_prefix scope for a flow query.

    Mirrors :func:`fetch_opos_rows` so DSO/DPO denominators are scoped to EXACTLY
    the same entities as the numerator OPOS balance.  Returns:
      * ``None``       — unrestricted (admin / no narrow) → all entities.
      * ``[]``         — fail-closed deny (empty visibility) → caller issues NO SQL.
      * ``[p, ...]``   — restrict to these 2-char prefixes.
    """
    eps = [str(e)[:2] for e in resolve_entity_prefixes(session, entity)]
    if allowed is not None:
        safe = sorted({str(p).replace("'", "")[:2] for p in allowed if p})
        if eps:
            narrow = set(eps)
            safe = [p for p in safe if p in narrow]
        return safe  # possibly [] → deny
    return eps or None


def _sales_period_eur(
    session: Session, year: int, month: int, entity: Optional[str],
    allowed: Optional[frozenset[str]],
) -> Optional[float]:
    """Gross sales (EUR) over the FY-to-date window for the DSO denominator.

    Sums ``fact_sales.gross_sales`` (stored positive; ``entry_type = 'actual'``)
    with ``posting_date`` in [Jan 1 .. Stichtag], entity-scoped by
    ``LEFT(account_number_group, 2)`` (same convention as sales_analytics_compat).
    Returns ``None`` on a deny scope OR any error (e.g. a missing ``fact_sales``
    table) so the caller falls back to the payment-term proxy WITHOUT 500-ing —
    the DSO enhancement is best-effort and must never break the aging page.
    """
    try:
        prefixes = _effective_prefixes(session, entity, allowed)
        if prefixes == []:
            return None  # fail closed — no SQL
        params: dict[str, Any] = {
            "d0": date(year, 1, 1).isoformat(),
            "d1": _month_end(year, month).isoformat(),
        }
        ent_frag = ""
        if prefixes is not None:
            ent_frag = "AND LEFT(f.account_number_group, 2) = ANY(:prefixes)"
            params["prefixes"] = prefixes
        sql = text(f"""
            SELECT COALESCE(SUM(f.gross_sales), 0) AS gross_sales
            FROM fact_sales f
            WHERE f.entry_type = 'actual'
              AND f.posting_date BETWEEN :d0 AND :d1
              {ent_frag}
        """)
        val = session.execute(sql, params).scalar()
        return float(val or 0.0)
    except Exception:  # noqa: BLE001 — degrade to the term proxy, never 500
        logger.warning("DSO gross_sales flow unavailable → proxy fallback", exc_info=True)
        _safe_rollback(session)
        return None


def _com_level_filter(session: Session) -> Optional[tuple[str, dict[str, Any]]]:
    """SQL fragment + params selecting the ``COST_OF_MATERIALS`` GL accounts.

    Derives the level-2/3/4 filter at RUNTIME from the ``dim_pl_structure``
    mapping row(s) whose ``line_code`` is a purchase code (``_PURCHASE_LINE_CODES``)
    — so the purchase base tracks the loaded PL structure and never hardcodes
    account numbers.  Returns ``None`` (→ proxy fallback) when the structure is
    absent or defines no level filter for those codes.
    """
    try:
        rows = session.execute(
            text(
                "SELECT level_2, level_3, level_4 FROM dim_pl_structure "
                "WHERE line_code = ANY(:codes)"
            ),
            {"codes": list(_PURCHASE_LINE_CODES)},
        ).fetchall()
    except Exception:  # noqa: BLE001 — no structure → proxy fallback, never 500
        _safe_rollback(session)
        return None

    clauses: list[str] = []
    params: dict[str, Any] = {}
    for i, r in enumerate(rows):
        m = r._mapping
        parts: list[str] = []
        for lvl in ("level_2", "level_3", "level_4"):
            v = m[lvl]
            if v is not None and str(v).strip() != "":
                key = f"com_{lvl}_{i}"
                parts.append(f"TRIM(a.{lvl}) = :{key}")
                params[key] = str(v).strip()
        if parts:
            clauses.append("(" + " AND ".join(parts) + ")")
    if not clauses:
        return None
    return "(" + " OR ".join(clauses) + ")", params


def _purchases_period_eur(
    session: Session, year: int, month: int, entity: Optional[str],
    allowed: Optional[frozenset[str]],
) -> Optional[float]:
    """GL-derived purchases (EUR) over the FY-to-date window for the DPO denominator.

    Sums ``fact_gl_line.amount`` (raw expense debit → positive) over the P&L
    "Cost of materials" accounts (materials + purchased services, resolved via
    :func:`_com_level_filter`) for ``fiscal_year = year`` and ``fiscal_period``
    1..month — the canonical PL YTD grain.  NOT a dedicated purchase journal.
    Returns ``None`` on a deny scope, a missing structure, or any error so the
    caller falls back to the payment-term proxy WITHOUT 500-ing (best-effort).
    """
    try:
        prefixes = _effective_prefixes(session, entity, allowed)
        if prefixes == []:
            return None
        com = _com_level_filter(session)
        if com is None:
            return None
        com_frag, params = com
        params["year"] = year
        params["month"] = month
        ent_frag = ""
        if prefixes is not None:
            ent_frag = "AND LEFT(l.account_number_group, 2) = ANY(:prefixes)"
            params["prefixes"] = prefixes
        sql = text(f"""
            SELECT COALESCE(SUM(l.amount), 0) AS purchases
            FROM fact_gl_line l
            JOIN fact_gl_entry e
              ON e.journal_entry_group_number = l.journal_entry_group_number
             AND e.fiscal_year = l.fiscal_year
            JOIN dim_gl_account a
              ON a.account_number_group = l.account_number_group
             AND a.fiscal_year = l.fiscal_year
            WHERE a.level_0 = 'PL'
              AND {com_frag}
              AND e.fiscal_year = :year
              AND e.fiscal_period BETWEEN 1 AND :month
              {ent_frag}
        """)
        val = session.execute(sql, params).scalar()
        return abs(float(val or 0.0))
    except Exception:  # noqa: BLE001 — degrade to the term proxy, never 500
        logger.warning("DPO purchases flow unavailable → proxy fallback", exc_info=True)
        _safe_rollback(session)
        return None


def real_days_value(
    open_eur: float, flow_eur: Optional[float], year: int, month: int,
) -> Optional[float]:
    """Real DSO/DPO in days, or ``None`` when it is not computable.

    ``DSO/DPO = |open| / flow × days_in_period``.  Returns ``None`` (→ proxy) when
    the period flow is missing or non-positive (divide-by-zero guard), and clamps
    an implausibly large ratio to ``_MAX_FLOW_DAYS`` rather than emitting an absurd
    figure.  ``open_eur`` is the gross open aging basis (already ≥ 0 for both AR and
    AP); ``abs`` is a defensive guard only.
    """
    if flow_eur is None or flow_eur <= 1e-6:
        return None
    val = abs(open_eur) / flow_eur * _days_in_fy_to_date(year, month)
    if val <= 0:
        return None
    return round(min(val, _MAX_FLOW_DAYS), 1)


def _side_days(
    session: Session, side: str, year: int, month: int, entity: Optional[str],
    open_eur: float,
) -> tuple[float, str]:
    """Real DSO (AR) / DPO (AP) with a payment-term proxy fallback.

    Returns ``(days, source)`` where ``source`` is ``'real'`` when computed from
    the period flow and ``'proxy'`` when it falls back to ``term_proxy_days`` (no
    flow data / missing table / 0 denominator) — the golden-safety flag so a DB
    without the flow tables behaves EXACTLY as the pre-Phase-8 proxy did.
    """
    allowed = _ALLOWED_PREFIXES.get()
    if side == "AR":
        flow = _sales_period_eur(session, year, month, entity, allowed)
    else:
        flow = _purchases_period_eur(session, year, month, entity, allowed)
    val = real_days_value(open_eur, flow, year, month)
    if val is None:
        return float(term_proxy_days(side)), "proxy"
    return val, "real"


# --------------------------------------------------------------------------- #
# F5 KPI + aging series (drop-in for gl_aging.build_*_aging)
# --------------------------------------------------------------------------- #
def _aging_series(buckets_eur: dict[str, float]) -> list[dict[str, Any]]:
    return [
        {"band": b, "label": lbl, "amount": round(buckets_eur.get(b, 0.0) / 1000.0, 2)}
        for b, lbl in AR_BANDS
    ]


def build_receivables_aging_opos(
    session: Session, year: int, month: int, entity: Optional[str] = None,
) -> dict[str, Any]:
    from app.services.gl_aging import enrich_aging_response

    v = _partner_view(session, "AR", year, month, entity)
    buckets = _agg_buckets(v["aging"])
    series = _aging_series(buckets)
    # GROSS aging basis: sum of the positive-partner FIFO buckets (credit-balance
    # partners floored to 0 in compute_opos_aging).  == Σ series.amount and, by
    # construction, == before_due + overdue.  This is the correct denominator for
    # the aging section (the net headline below nets credit partners DOWN, so it
    # is NOT a valid denominator for the bars/overdue_pct).
    total_open_gross = round(sum(buckets.values()) / 1000.0, 2)
    overdue = round(sum(a["overdue_open"] for a in v["aging"].values()) / 1000.0, 2)
    # Derive before_due from the ALREADY-ROUNDED gross/overdue so the bridge
    # footnote identity before_due + overdue == total_open_gross holds EXACTLY
    # (independent rounding of the not_yet_due bucket could drift by ±0.01 kEUR).
    before_due = round(total_open_gross - overdue, 2)
    method_a_total = round(v["signed_total_eur"] / 1000.0, 2)  # net, reconciled headline
    # Credit-balance bridge: gross − net headline magnitude = Σ|net of credit-balance
    # partners|.  Reconciles the gross aging basis back to the net headline.
    credit_balances = round(total_open_gross - method_a_total, 2)
    # F4: REAL DSO = gross_AR_open / gross_sales_period × days_in_period (fact_sales),
    # with a term-proxy fallback (30d) flagged via dso_source when the flow is absent.
    # Numerator is the GROSS aging basis (Σ positive-partner buckets == total_open_gross),
    # so DSO reconciles with the displayed AR — NOT the net Method-A signed_total_eur.
    dso_days, dso_source = _side_days(session, "AR", year, month, entity, sum(buckets.values()))

    return enrich_aging_response({
        "year": year, "month": month, "as_of": v["as_of"].isoformat(),
        "source": "opos",
        "total_receivables": method_a_total,
        "subledger_total": method_a_total,
        "total_open_gross": total_open_gross,
        "credit_balances": credit_balances,
        "reconciliation_mode": "opos_method_a",
        "series": series,
        "kpis": {
            "before_due": before_due,
            "overdue": overdue,
            # Anchored to the GROSS basis: overdue <= total_open_gross by
            # construction, so this is in [0,100] WITHOUT the clamp being load-bearing.
            "overdue_pct": clamp_pct(overdue, total_open_gross),
            "dso_days": dso_days,        # F4 real (fact_sales) or proxy fallback
            "dso_source": dso_source,    # 'real' | 'proxy' — golden-safety flag
            "open_documents": v["open_documents"],
        },
    })


def build_payables_aging_opos(
    session: Session, year: int, month: int, entity: Optional[str] = None,
) -> dict[str, Any]:
    from app.services.gl_aging import enrich_aging_response

    v = _partner_view(session, "AP", year, month, entity)
    buckets = _agg_buckets(v["aging"])
    series = _aging_series(buckets)
    # GROSS aging basis (positive-magnitude partners only) — see AR builder note.
    total_open_gross = round(sum(buckets.values()) / 1000.0, 2)
    overdue = round(sum(a["overdue_open"] for a in v["aging"].values()) / 1000.0, 2)
    # Derive before_due from the ALREADY-ROUNDED gross/overdue so the bridge
    # footnote identity before_due + overdue == total_open_gross holds EXACTLY.
    before_due = round(total_open_gross - overdue, 2)
    # F5: AP Method-A total is a credit (negative); report positive magnitude.
    method_a_mag = round(-v["signed_total_eur"] / 1000.0, 2)  # net, reconciled headline
    # Credit-balance bridge: gross − net headline magnitude = Σ|net of debit-balance
    # (supplier-overpaid / prepayment) partners floored out of the gross basis|.
    credit_balances = round(total_open_gross - method_a_mag, 2)
    # F4: REAL DPO = gross_AP_open / purchases_period × days_in_period (GL-derived
    # Cost-of-materials postings), with a term-proxy fallback (45d) flagged via
    # dpo_source.  Numerator is the GROSS aging basis (Σ positive-partner buckets ==
    # total_open_gross), so DPO reconciles with the displayed AP.
    dpo_days, dpo_source = _side_days(session, "AP", year, month, entity, sum(buckets.values()))

    return enrich_aging_response({
        "year": year, "month": month, "as_of": v["as_of"].isoformat(),
        "source": "opos",
        "total_payables": method_a_mag,
        "subledger_total": method_a_mag,
        "total_open_gross": total_open_gross,
        "credit_balances": credit_balances,
        "reconciliation_mode": "opos_method_a",
        "series": series,
        "kpis": {
            "before_due": before_due,
            "overdue": overdue,
            # Anchored to the GROSS basis (clamp no longer load-bearing).
            "overdue_pct": clamp_pct(overdue, total_open_gross),
            "dpo_days": dpo_days,        # F4 real (GL purchases) or proxy fallback
            "dpo_source": dpo_source,    # 'real' | 'proxy' — golden-safety flag
            "open_documents": v["open_documents"],
        },
    })


def build_metrics_aging_series_opos(
    session: Session, metric: str, entity: Optional[str],
) -> list[dict]:
    today = date.today()
    if metric == "ar_aging":
        res = build_receivables_aging_opos(session, today.year, today.month, entity)
    elif metric == "ap_aging":
        res = build_payables_aging_opos(session, today.year, today.month, entity)
    else:
        return []
    return [
        {"band": s["band"], "label": s["label"], "value": round(s["amount"] * 1000, 2)}
        for s in res["series"]
    ]


# --------------------------------------------------------------------------- #
# Partner register / scatter / combo
# --------------------------------------------------------------------------- #
def _register_rows(
    v: dict[str, Any], side: str, limit: int, days_outstanding: Optional[float] = None,
) -> list[dict[str, Any]]:
    terms = term_proxy_days(side)
    # days_outstanding is the side-level REAL DSO/DPO (F4) when available, else the
    # term proxy — kept SEPARATE from payment_terms_days (the due-date term stays 30/45).
    dso_dpo = float(days_outstanding) if days_outstanding is not None else float(terms)
    # AR register consumers read r.gross_sales; AP register reads r.gross_spend.
    gross_key = "gross_sales" if side == "AR" else "gross_spend"
    out = []
    for pk, a in v["aging"].items():
        info = a["meta"]
        out.append({
            "partner_key": pk,
            "customer_id": pk,
            "supplier_id": pk,
            "name": info.get("name") or pk,
            "customer_name": info.get("name") or pk,
            "supplier_name": info.get("name") or pk,
            "contact": info.get("contact"),
            "balance": round(a["total_open"] / 1000.0, 2),
            "balanceKeur": round(a["total_open"] / 1000.0, 2),
            "overdue": round(a["overdue_open"] / 1000.0, 2),
            "overdue_pct": a["overdue_pct"],
            "credit_balance": a["credit_balance"],
            "country_code": info.get("country_code"),
            "region_code": info.get("region_code"),
            "city": info.get("city"),
            gross_key: None,  # A2: per-partner GoBD gross journal not wired in read path
            "days_outstanding": dso_dpo,  # F4 side-level real DSO/DPO (or proxy)
            "payment_terms_days": terms,
            "open_documents": a.get("open_documents", 0),
            "documents": [],
        })
    out.sort(key=lambda x: x["balance"], reverse=True)
    return out[:limit]


def build_receivables_customers_opos(
    session: Session, year: int, month: int, entity: Optional[str] = None, limit: int = 50,
) -> dict[str, Any]:
    v = _partner_view(session, "AR", year, month, entity)
    dso_days, _ = _side_days(session, "AR", year, month, entity, _gross_open_eur(v))
    register = _register_rows(v, "AR", limit, days_outstanding=dso_days)
    scatter = [
        {"customer_id": r["customer_id"], "name": r["name"],
         "customer_name": r["name"], "balanceKeur": r["balanceKeur"],
         "balance": r["balance"], "overdue_pct": r["overdue_pct"],
         "credit_balance": False}
        for r in register if not r["credit_balance"]
    ]
    combo = [
        {"customer_name": r["name"], "name": r["name"],
         "balance": r["balance"], "days_outstanding": r["days_outstanding"]}
        for r in register[:15]
    ]
    return {"year": year, "month": month, "source": "opos",
            "register": register, "scatter": scatter, "combo": combo}


def build_payables_suppliers_opos(
    session: Session, year: int, month: int, entity: Optional[str] = None, limit: int = 50,
) -> dict[str, Any]:
    v = _partner_view(session, "AP", year, month, entity)
    dpo_days, _ = _side_days(session, "AP", year, month, entity, _gross_open_eur(v))
    register = _register_rows(v, "AP", limit, days_outstanding=dpo_days)
    scatter = [
        {"supplier_id": r["supplier_id"], "name": r["name"],
         "supplier_name": r["name"], "balanceKeur": r["balanceKeur"],
         "balance": r["balance"], "overdue_pct": r["overdue_pct"],
         "credit_balance": False}
        for r in register if not r["credit_balance"]
    ]
    combo = [
        {"supplier_name": r["name"], "name": r["name"],
         "balance": r["balance"], "days_outstanding": r["days_outstanding"]}
        for r in register[:15]
    ]
    return {"year": year, "month": month, "source": "opos",
            "register": register, "scatter": scatter, "combo": combo}


# --------------------------------------------------------------------------- #
# by-dimension / hierarchy
# --------------------------------------------------------------------------- #
def _dim_key(info: dict, dimension: str) -> str:
    if dimension == "country":
        return info.get("country_code") or "—"
    if dimension == "entity":
        return info.get("entity") or "—"  # FULL name, never the prefix
    return info.get("name") or "—"


def _bucket_row(buckets_eur: dict[str, float], label: str) -> dict[str, Any]:
    vals = {b: round(buckets_eur.get(b, 0.0) / 1000.0, 2) for b in _BANDS}
    total = round(sum(vals.values()), 2)
    overdue = round(total - vals["not_yet_due"], 2)
    vals["total"] = total
    vals["overdue"] = overdue
    vals["before_due"] = vals["not_yet_due"]
    vals["overdue_pct"] = clamp_pct(overdue, total)
    vals["label"] = label
    return vals


def _by_dimension(
    session: Session, side: str, dimension: str, year: int, month: int,
    entity: Optional[str], limit: int,
) -> dict[str, Any]:
    v = _partner_view(session, side, year, month, entity)
    groups: dict[str, dict[str, float]] = {}
    for a in v["aging"].values():
        key = _dim_key(a["meta"], dimension)
        g = groups.setdefault(key, {b: 0.0 for b in _BANDS})
        for b in _BANDS:
            g[b] += a["buckets"].get(b, 0.0)
    rows = [_bucket_row(g, key) for key, g in groups.items()]
    rows.sort(key=lambda x: x["total"], reverse=True)
    return {"dimension": dimension, "year": year, "month": month,
            "source": "opos", "rows": rows[:limit]}


def build_receivables_by_dimension_opos(session, dimension, year, month, entity=None, limit=50, view="buckets"):
    res = _by_dimension(session, "AR", dimension, year, month, entity, limit)
    res["view"] = view
    return res


def build_payables_by_dimension_opos(session, dimension, year, month, entity=None, limit=50, view="buckets"):
    res = _by_dimension(session, "AP", dimension, year, month, entity, limit)
    res["view"] = view
    return res


def _by_hierarchy(session, side, hierarchy, year, month, entity, limit):
    v = _partner_view(session, side, year, month, entity)
    groups: dict[tuple, dict] = {}
    for a in v["aging"].values():
        key = tuple(_dim_key(a["meta"], d) for d in hierarchy)
        g = groups.setdefault(key, {b: 0.0 for b in _BANDS})
        for b in _BANDS:
            g[b] += a["buckets"].get(b, 0.0)
    out = []
    for key, g in groups.items():
        row = _bucket_row(g, " / ".join(key))
        row["dims"] = {d: key[i] for i, d in enumerate(hierarchy)}
        out.append(row)
    out.sort(key=lambda x: x["total"], reverse=True)
    return out[:limit]


def build_receivables_by_dimension_hierarchy_opos(
    session, hierarchy_raw, year, month, entity=None, limit=500, view="buckets",
    compare_pm=False, compare_py=False,
):
    hierarchy = _parse_hierarchy(hierarchy_raw) or ["entity", "customer"]
    rows = _by_hierarchy(session, "AR", hierarchy, year, month, entity, limit)
    return {"hierarchy": hierarchy, "view": view, "year": year, "month": month,
            "source": "opos", "compare_pm": compare_pm, "compare_py": compare_py, "rows": rows}


def build_payables_by_dimension_hierarchy_opos(
    session, hierarchy_raw, year, month, entity=None, limit=500, view="buckets",
    compare_pm=False, compare_py=False,
):
    hierarchy = _parse_hierarchy(hierarchy_raw) or ["entity", "supplier"]
    rows = _by_hierarchy(session, "AP", hierarchy, year, month, entity, limit)
    return {"hierarchy": hierarchy, "view": view, "year": year, "month": month,
            "source": "opos", "compare_pm": compare_pm, "compare_py": compare_py, "rows": rows}


def _parse_hierarchy(raw: str) -> list[str]:
    import json
    try:
        if raw.strip().startswith("["):
            return json.loads(raw)
    except json.JSONDecodeError:
        pass
    return [p.strip() for p in raw.split(",") if p.strip()]


# --------------------------------------------------------------------------- #
# concentration (+ trend), geo, dimension chart, portfolio table, documents, trend
# --------------------------------------------------------------------------- #
CONCENTRATION_RANK_BANDS = (
    ("rank_1_5", "Top 1–5", 1, 5),
    ("rank_6_10", "Top 6–10", 6, 10),
    ("rank_11_20", "Top 11–20", 11, 20),
    ("rank_21_plus", "Rank 21+", 21, None),
)


def _concentration(session, side, year, month, entity, count_key):
    v = _partner_view(session, side, year, month, entity)
    amounts = sorted(
        (round(a["total_open"] / 1000.0, 2) for a in v["aging"].values() if a["total_open"] > 0),
        reverse=True,
    )
    total = sum(amounts)
    segments = []
    for band_id, label, rank_from, rank_to in CONCENTRATION_RANK_BANDS:
        sl = amounts[rank_from - 1:rank_to] if rank_to is not None else amounts[rank_from - 1:]
        amt = sum(sl)
        segments.append({
            "band": band_id, "label": label, "amount": round(amt, 2),
            "pct": round(amt / total * 100, 1) if total > 0 else 0.0,
            count_key: len(sl),
        })
    return {"year": year, "month": month, "source": "opos",
            "total": round(total, 2), "segments": segments}


def build_receivables_concentration_opos(session, year, month, entity=None):
    return _concentration(session, "AR", year, month, entity, "customer_count")


def build_payables_concentration_opos(session, year, month, entity=None):
    return _concentration(session, "AP", year, month, entity, "supplier_count")


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    m = month + delta
    y = year
    while m < 1:
        m += 12
        y -= 1
    while m > 12:
        m -= 12
        y += 1
    return y, m


def build_concentration_trend_ar_opos(session, year, month, entity=None, periods_back=12):
    return _concentration_trend(session, "AR", year, month, entity, periods_back)


def build_concentration_trend_ap_opos(session, year, month, entity=None, periods_back=12):
    return _concentration_trend(session, "AP", year, month, entity, periods_back)


def _concentration_trend(session, side, year, month, entity, periods_back):
    points = []
    y, m = year, month
    for _ in range(periods_back):
        conc = _concentration(session, side, y, m, entity,
                              "customer_count" if side == "AR" else "supplier_count")
        points.append({"year": y, "month": m, "label": f"{m:02d}/{y}",
                       "segments": conc["segments"], "total": conc["total"]})
        y, m = _shift_month(y, m, -1)
    points.reverse()
    return {"year": year, "month": month, "source": "opos", "points": points}


def _geo(session, side, year, month, entity, limit):
    res = _by_dimension(session, side, "country", year, month, entity, limit)
    rows = [{
        "country": r["label"], "country_name": r["label"],
        "balance": r["total"], "before_due": r.get("before_due", 0.0),
        "overdue": r["overdue"], "overdue_pct": r["overdue_pct"],
    } for r in res["rows"]]
    return {"year": year, "month": month, "source": "opos", "rows": rows}


def build_receivables_geo_opos(session, year, month, entity=None, limit=20):
    return _geo(session, "AR", year, month, entity, limit)


def build_payables_geo_opos(session, year, month, entity=None, limit=20):
    return _geo(session, "AP", year, month, entity, limit)


def build_receivables_geo_country_locations_opos(session, country_code, year, month, entity=None):
    return _geo_locations(session, "AR", country_code, year, month, entity)


def build_payables_geo_country_locations_opos(session, country_code, year, month, entity=None):
    return _geo_locations(session, "AP", country_code, year, month, entity)


def _geo_locations(session, side, country_code, year, month, entity):
    v = _partner_view(session, side, year, month, entity)
    locations = []
    for a in v["aging"].values():
        info = a["meta"]
        if (info.get("country_code") or "—") != country_code or a["total_open"] <= 0:
            continue
        # balance is kEUR (a["total_open"] is EUR); revenue_keur uses the same
        # unit convention as the rest of the geo builder.  Each register entry is
        # one partner, so customer_count is 1.  No geocoder wired → lat/lon null.
        balance_keur = round(a["total_open"] / 1000.0, 2)
        locations.append({
            "name": info.get("name"), "city": info.get("city"),
            "region_code": info.get("region_code"),
            "country": info.get("country_code") or country_code,
            "postal_code": info.get("postal_code"),
            "balance": balance_keur,
            "revenue_keur": balance_keur,
            "customer_count": 1,
            "overdue_pct": a["overdue_pct"],
            "lat": None, "lon": None, "geo_source": None,
        })
    locations.sort(key=lambda x: x["balance"], reverse=True)
    mapped_count = sum(1 for loc in locations if loc["lat"] is not None and loc["lon"] is not None)
    return {"country": country_code, "country_code": country_code,
            "period": v["as_of"].isoformat(), "year": year, "month": month,
            "source": "opos", "locations": locations,
            "total_count": len(locations), "mapped_count": mapped_count}


def _dimension_chart(session, side, dimension, year, month, entity, limit, compare_pm, compare_py):
    res = _by_dimension(session, side, dimension, year, month, entity, limit)
    rows = []
    for r in res["rows"]:
        item = {"label": r["label"], "balance": r["total"],
                "overdue": r["overdue"], "overdue_pct": r["overdue_pct"]}
        if compare_pm:
            item["balance_pm"] = item["balance"]
        if compare_py:
            item["balance_py"] = item["balance"]
        rows.append(item)
    return {"dimension": dimension, "year": year, "month": month, "source": "opos",
            "compare_pm": compare_pm, "compare_py": compare_py, "rows": rows}


def build_receivables_dimension_chart_opos(session, dimension, year, month, entity=None, limit=25, compare_pm=False, compare_py=False):
    return _dimension_chart(session, "AR", dimension, year, month, entity, limit, compare_pm, compare_py)


def build_payables_dimension_chart_opos(session, dimension, year, month, entity=None, limit=25, compare_pm=False, compare_py=False):
    return _dimension_chart(session, "AP", dimension, year, month, entity, limit, compare_pm, compare_py)


def _portfolio_table(session, side, dimension, year, month, entity, rows_per_bucket):
    v = _partner_view(session, side, year, month, entity)
    by_band: dict[str, list[dict]] = {b: [] for b in _BANDS}
    for a in v["aging"].values():
        label = _dim_key(a["meta"], dimension)
        for b in _BANDS:
            amt = a["buckets"].get(b, 0.0)
            if amt > 0:
                by_band[b].append({"label": label, "amount": round(amt / 1000.0, 2),
                                   "document_count": 0, "relationship_since": None})
    buckets = []
    for b, lbl in AR_BANDS:
        rows = sorted(by_band[b], key=lambda x: x["amount"], reverse=True)[:rows_per_bucket]
        buckets.append({
            "band": b, "label": lbl,
            "amount": round(sum(x["amount"] for x in by_band[b]), 2),
            "document_count": 0, "rows": rows,
        })
    return {"dimension": dimension, "year": year, "month": month,
            "as_of": v["as_of"].isoformat(), "source": "opos", "buckets": buckets}


def build_receivables_portfolio_table_opos(session, dimension, year, month, entity=None, rows_per_bucket=25):
    return _portfolio_table(session, "AR", dimension, year, month, entity, rows_per_bucket)


def build_payables_portfolio_table_opos(session, dimension, year, month, entity=None, rows_per_bucket=25):
    return _portfolio_table(session, "AP", dimension, year, month, entity, rows_per_bucket)


def _partner_documents(session, side, partner_key, year, month, entity):
    """Per-partner open-item document list (invoice-type + all trade rows)."""
    v = _partner_view(session, side, year, month, entity)
    rows = v["rows"]  # single fetch per drilldown (reuse _partner_view's rows)
    as_of = v["as_of"]
    terms = term_proxy_days(side)
    sign = 1.0 if side == "AR" else -1.0
    docs = []
    for r in rows:
        if r["partner_key"] != partner_key:
            continue
        due = _due_date(r, terms)
        docs.append({
            "document_number": r["beleg_no"],
            "belegart": r["belegart"],
            "satzart": r["satzart"],
            "posting_date": r["buchungsdatum"].isoformat(),
            "due_date": due.isoformat(),
            "days_overdue": max(0, (as_of - due).days),
            "amount": round(float(r["betrag"]) * sign / 1000.0, 2),
        })
    docs.sort(key=lambda d: d["posting_date"], reverse=True)
    return docs[:200]


def build_receivables_customer_documents_opos(session, customer_id, year, month, entity=None):
    docs = _partner_documents(session, "AR", customer_id, year, month, entity)
    return {"customer_id": customer_id, "year": year, "month": month,
            "source": "opos", "documents": docs}


def build_payables_supplier_documents_opos(session, supplier_id, year, month, entity=None):
    docs = _partner_documents(session, "AP", supplier_id, year, month, entity)
    return {"supplier_id": supplier_id, "year": year, "month": month,
            "source": "opos", "documents": docs}


def _trend(session, side, year, month, entity, periods_back):
    points = []
    y, m = year, month
    builder = build_receivables_aging_opos if side == "AR" else build_payables_aging_opos
    total_key = "total_receivables" if side == "AR" else "total_payables"
    for _ in range(periods_back):
        snap = builder(session, y, m, entity)
        kpis = snap.get("kpis") or {}
        total = float(snap.get(total_key) or 0)          # net headline (reconciled)
        gross = float(snap.get("total_open_gross") or 0)  # GROSS aging basis
        overdue = float(kpis.get("overdue") or 0)
        point = {"year": y, "month": m, "label": f"{m:02d}/{y}",
                 "balance": total, "gross_balance": gross, "overdue": overdue,
                 # Gross-anchored + clamped [0,100] — consistent with the KPI card
                 # (overdue is gross, so net-anchoring here could exceed 100%).
                 "overdue_pct": clamp_pct(overdue, gross),
                 "before_due": float(kpis.get("before_due") or 0),
                 "open_documents": int(kpis.get("open_documents") or 0)}
        if side == "AR":
            point["net_sales"] = 0.0
            point["credit_sales_pct"] = point["overdue_pct"]
            point["dso_days"] = float(kpis.get("dso_days") or term_proxy_days("AR"))
        else:
            point["procurement"] = 0.0
            point["dpo_days"] = float(kpis.get("dpo_days") or term_proxy_days("AP"))
        points.append(point)
        y, m = _shift_month(y, m, -1)
    points.reverse()
    return {"year": year, "month": month, "source": "opos", "points": points}


def build_receivables_trend_opos(session, year, month, entity=None, periods_back=12, period_grain="month"):
    del period_grain
    return _trend(session, "AR", year, month, entity, periods_back)


def build_payables_trend_opos(session, year, month, entity=None, periods_back=12, period_grain="month"):
    del period_grain
    return _trend(session, "AP", year, month, entity, periods_back)
