"""Read-through orchestrators for the anomaly analyses (anomaly rework, Phase 3).

These wrap the Phase 1/2 PURE builders with the Lazy+TTL snapshot cache
(:mod:`app.services.anomaly_snapshot_cache`):

    get_outliers / get_seasonality / get_forensic / get_overview

Each orchestrator:

  1. derives the cache key — ``entity_scope`` from the caller's allowed entity
     prefixes (:func:`anomaly_snapshot_cache.entity_scope_key`) and
     ``algorithm_version`` from the underlying builder's version tag,
  2. ``get_cached(...)`` — on a fresh HIT returns ``{**payload, "cache_hit": True}``,
  3. on MISS computes via the Phase 1/2 builders, then BEST-EFFORT ``save(...)``
     (try/except + rollback so a cache write can never make the read 500) and
     returns ``{**payload, "cache_hit": False}``.

``get_overview`` does NOT cache the underlying trees twice — it builds the
aggregate per-L3 overview report from the outlier + seasonality trees and the
forensic positions, and caches the FINISHED overview payload under
``analysis_type='overview'``.

Bookings stay LIVE (``gl_anomaly_tree.list_account_bookings``) and are never cached.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from app.services import anomaly_snapshot_cache as cache
from app.services.anomaly_score import band as score_band
from app.services.gl_anomaly_tree import (
    ALGORITHM_VERSION as TREE_ALGORITHM_VERSION,
    build_outlier_tree,
    build_seasonality_tree,
)
from app.services.gl_forensic_positions import (
    POSITIONS_ALGORITHM_VERSION as FORENSIC_ALGORITHM_VERSION,
    build_forensic_positions,
)

logger = logging.getLogger(__name__)

#: Version tag for the overview AGGREGATE payload.  Bump when the overview shape or
#: the sentence/flag logic changes (it composes the tree + forensic versions).
#: v2 (anomaly rework, Phase 3): the per-L3 card became a layperson REPORT —
#: signal_score/band (MAX across analyses), a plain-English headline + 2-4 bullets,
#: a downsampled spark_series, and flags now carry {score, band, deep_link} (the
#: z-derived status is gone).  Only NOTABLE positions are emitted.
#: v3 (anomaly refinement): each spark point now carries {label, value, expected,
#: signal_score} (was {label, value}) — a per-point reference (outlier MEAN / seasonality
#: expected_keur) + the per-point signal_score so the FE can mark flagged points.
#: Bumped to v3 so the overview snapshot cache invalidates.
OVERVIEW_ALGORITHM_VERSION = (
    f"anomaly_overview_v3+{TREE_ALGORITHM_VERSION}+{FORENSIC_ALGORITHM_VERSION}"
)

#: Forensic flag-count → status banding (descriptive, NOT a financial KPI):
#: 0 flags → none, 1–2 → low, 3–9 → medium, 10+ → high.
_FORENSIC_HIGH = 10
_FORENSIC_MEDIUM = 3
_FORENSIC_LOW = 1

#: Forensic flag-count → 0..100 signal score (descriptive, NOT a financial KPI),
#: so the overview can fold forensic into the same MAX-score ranking as the two
#: z-based trees.  Scores are chosen to land in the SAME word band as the count
#: banding above when run through ``score_band`` (33/66 edges):
#:   1–2 flags → 20 (band 'low'),  3–9 → 50 (band 'medium'),  10+ → 90 (band 'high').
_FORENSIC_SCORE_HIGH = 90
_FORENSIC_SCORE_MEDIUM = 50
_FORENSIC_SCORE_LOW = 20


def _forensic_score(flag_count: int) -> int:
    """Map a rolled-up forensic flag COUNT to a 0..100 signal score (pure)."""
    if flag_count >= _FORENSIC_HIGH:
        return _FORENSIC_SCORE_HIGH
    if flag_count >= _FORENSIC_MEDIUM:
        return _FORENSIC_SCORE_MEDIUM
    if flag_count >= _FORENSIC_LOW:
        return _FORENSIC_SCORE_LOW
    return 0

#: A position is NOTABLE (gets a card) when its MAX signal score reaches this base
#: threshold in at least one analysis — i.e. band != 'low' (>= medium = 33) OR any
#: forensic finding.  The FE Sensitivity slider hides cards below its own min-score;
#: this server floor only drops the truly-uninteresting (low-band, no forensic) ones.
_NOTABLE_MIN_SCORE = 33


# ===========================================================================
# Generic read-through helper
# ===========================================================================
def _read_through(
    session: Session,
    *,
    analysis_type: str,
    entity_scope: str,
    algorithm_version: str,
    compute: Callable[[], dict[str, Any]],
    ttl_seconds: int,
) -> dict[str, Any]:
    """Cache-first: fresh hit → cached payload; miss → compute + best-effort save."""
    cached = cache.get_cached(
        session, analysis_type, entity_scope, algorithm_version,
        ttl_seconds=ttl_seconds,
    )
    if cached is not None:
        return {**cached, "cache_hit": True}

    payload = compute()
    try:
        cache.save(session, analysis_type, entity_scope, algorithm_version, payload)
    except Exception:
        # A cache write must never break the read.  Roll the failed write back so
        # the session is usable for whatever the caller does next.
        try:
            session.rollback()
        except Exception:
            logger.exception("anomaly_compute: rollback after save failure failed")
        logger.exception(
            "anomaly_compute: snapshot save failed (type=%s scope=%r) — serving fresh",
            analysis_type, entity_scope,
        )
    return {**payload, "cache_hit": False}


# ===========================================================================
# Per-analysis orchestrators
# ===========================================================================
def get_outliers(
    session: Session,
    *,
    entity_prefixes: Optional[list[str]] = None,
    ttl_seconds: int = cache.DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    """Read-through OUTLIER tree (cached by entity scope + tree algorithm version)."""
    scope = cache.entity_scope_key(entity_prefixes)
    return _read_through(
        session,
        analysis_type="outliers",
        entity_scope=scope,
        algorithm_version=TREE_ALGORITHM_VERSION,
        compute=lambda: build_outlier_tree(session, entity_prefixes=entity_prefixes),
        ttl_seconds=ttl_seconds,
    )


def get_seasonality(
    session: Session,
    *,
    entity_prefixes: Optional[list[str]] = None,
    ttl_seconds: int = cache.DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    """Read-through SEASONALITY tree (cached by entity scope + tree algorithm version)."""
    scope = cache.entity_scope_key(entity_prefixes)
    return _read_through(
        session,
        analysis_type="seasonality",
        entity_scope=scope,
        algorithm_version=TREE_ALGORITHM_VERSION,
        compute=lambda: build_seasonality_tree(session, entity_prefixes=entity_prefixes),
        ttl_seconds=ttl_seconds,
    )


def get_forensic(
    session: Session,
    *,
    entity_prefixes: Optional[list[str]] = None,
    ttl_seconds: int = cache.DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    """Read-through FORENSIC positions (cached by entity scope + forensic version)."""
    scope = cache.entity_scope_key(entity_prefixes)
    return _read_through(
        session,
        analysis_type="forensic",
        entity_scope=scope,
        algorithm_version=FORENSIC_ALGORITHM_VERSION,
        compute=lambda: build_forensic_positions(session, entity_prefixes=entity_prefixes),
        ttl_seconds=ttl_seconds,
    )


def get_overview(
    session: Session,
    *,
    entity_prefixes: Optional[list[str]] = None,
    ttl_seconds: int = cache.DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    """Read-through OVERVIEW aggregate (per NOTABLE L3 position, P&L then BS).

    Composes the outlier + seasonality trees and the forensic positions into one
    per-L3 layperson-REPORT card list.  Each card carries a MAX ``signal_score`` +
    ``band``, a plain-English ``headline``, 2–4 ``bullets``, a compact ``spark_series``
    and per-analysis ``flags`` ({score, band, deep_link}).  Only NOTABLE positions are
    included; cards are grouped P&L first then BS, each group sorted by signal_score
    descending.  See :func:`build_overview_payload`.

    The finished overview is what gets cached (under ``analysis_type='overview'``),
    NOT the underlying trees.
    """
    scope = cache.entity_scope_key(entity_prefixes)

    def _compute() -> dict[str, Any]:
        outliers = build_outlier_tree(session, entity_prefixes=entity_prefixes)
        seasonality = build_seasonality_tree(session, entity_prefixes=entity_prefixes)
        forensic = build_forensic_positions(session, entity_prefixes=entity_prefixes)
        return build_overview_payload(
            outliers, seasonality, forensic, entity_prefixes=entity_prefixes,
        )

    return _read_through(
        session,
        analysis_type="overview",
        entity_scope=scope,
        algorithm_version=OVERVIEW_ALGORITHM_VERSION,
        compute=_compute,
        ttl_seconds=ttl_seconds,
    )


# ===========================================================================
# Overview aggregate (PURE — composes the tree + forensic payloads)
# ===========================================================================
def _l3_nodes(tree_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Top-level (L3) nodes of an outlier/seasonality tree payload."""
    return [n for n in (tree_payload.get("tree") or []) if n.get("level") == "level_3"]


def _deep_link(view: str, node_key: str) -> str:
    return f"/anomaly-detection/{view}?node={node_key}"


#: How many points the compact sparkline series is downsampled to (cap).
SPARK_MAX_POINTS = 24


# ---------------------------------------------------------------------------
# Report-content helpers (PURE, plain-English, NON-statistical — no z/σ words)
# ---------------------------------------------------------------------------
def _node_score(node: Optional[dict[str, Any]]) -> int:
    """0..100 signal score of a tree node (0 when the node is absent)."""
    if not node:
        return 0
    try:
        return int(node.get("signal_score") or 0)
    except (TypeError, ValueError):
        return 0


def _point_value(p: dict[str, Any]) -> Optional[float]:
    """Presented value of a series point (outliers: value_keur; seasonality: actual_keur)."""
    v = p.get("value_keur")
    if v is None:
        v = p.get("actual_keur")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _keur_phrase(value_keur: float) -> str:
    """Plain-English money magnitude from a kEUR value (e.g. '€1.2m', '€400k')."""
    eur = abs(float(value_keur)) * 1000.0
    if eur >= 1_000_000:
        return f"€{eur / 1_000_000:.1f}m"
    if eur >= 1_000:
        return f"€{eur / 1_000:.0f}k"
    return f"€{eur:.0f}"


def _peak_point(node: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The series point with the largest signal_score (the most unusual month)."""
    if not node:
        return None
    series = ((node.get("payload") or {}).get("series")) or []
    best = None
    best_score = -1
    for p in series:
        try:
            s = int(p.get("signal_score") or 0)
        except (TypeError, ValueError):
            s = 0
        if s > best_score:
            best_score = s
            best = p
    return best


def _typical_value(node: Optional[dict[str, Any]]) -> Optional[float]:
    """A 'normal month' reference: the series mean (outliers) or median (distribution)."""
    if not node:
        return None
    payload = node.get("payload") or {}
    stats = payload.get("stats") or {}
    mean = stats.get("mean_keur")
    if mean is not None:
        try:
            return float(mean)
        except (TypeError, ValueError):
            pass
    dist = payload.get("distribution") or {}
    med = dist.get("median")
    try:
        return float(med) if med is not None else None
    except (TypeError, ValueError):
        return None


def _trend_word(node: Optional[dict[str, Any]]) -> Optional[str]:
    """'rising' / 'falling' / None from the node regression slope (if meaningful)."""
    if not node:
        return None
    reg = (node.get("payload") or {}).get("regression")
    if not reg:
        return None
    try:
        slope = float(reg.get("slope") or 0.0)
        r2 = float(reg.get("r_squared") or 0.0)
    except (TypeError, ValueError):
        return None
    # Only call a trend when the fit explains a fair share of the variation.
    if r2 < 0.3 or slope == 0.0:
        return None
    return "rising" if slope > 0 else "falling"


def _flag_names(out_score: int, sea_score: int, f_count: int) -> list[str]:
    """Which analyses flagged this position (for a 'Flagged in: …' bullet)."""
    names: list[str] = []
    if out_score >= _NOTABLE_MIN_SCORE:
        names.append("Outliers")
    if sea_score >= _NOTABLE_MIN_SCORE:
        names.append("Seasonality")
    if f_count > 0:
        names.append("Forensic")
    return names


def _overview_headline(
    label: str,
    *,
    strongest: str,
    out_node: Optional[dict[str, Any]],
    sea_node: Optional[dict[str, Any]],
    f_count: int,
) -> str:
    """Punchy plain-English one-liner from the STRONGEST analysis (no z/σ words)."""
    if strongest == "forensic" and f_count > 0:
        entries = "counter-account" if f_count == 1 else "counter-accounts"
        return f"{f_count} unusual {entries} to review in {label}"

    node = out_node if strongest == "outliers" else sea_node
    peak = _peak_point(node)
    if peak is not None:
        pv = _point_value(peak)
        typ = _typical_value(out_node) or _typical_value(sea_node)
        period = peak.get("label") or peak.get("period_key") or "a recent month"
        if pv is not None and typ is not None and abs(typ) > 1e-9:
            ratio = abs(pv) / abs(typ)
            if ratio >= 1.5:
                return (
                    f"{label} jumped in {period} — about {ratio:.0f}× a normal month"
                )
        if pv is not None:
            return f"{label} stood out in {period} at {_keur_phrase(pv)}"

    trend = _trend_word(out_node) or _trend_word(sea_node)
    if trend == "rising":
        return f"{label} keeps growing"
    if trend == "falling":
        return f"{label} keeps shrinking"
    return f"{label} looks unusual and is worth a look"


def _overview_bullets(
    *,
    out_node: Optional[dict[str, Any]],
    sea_node: Optional[dict[str, Any]],
    out_score: int,
    sea_score: int,
    f_count: int,
) -> list[str]:
    """2–4 short plain-English points (spike, trend, flagged-in)."""
    bullets: list[str] = []

    # Biggest spike (from whichever tree has the stronger peak).
    peak_node = out_node if out_score >= sea_score else sea_node
    peak = _peak_point(peak_node)
    if peak is not None:
        pv = _point_value(peak)
        typ = _typical_value(out_node) or _typical_value(sea_node)
        period = peak.get("label") or peak.get("period_key") or "a recent month"
        if pv is not None and typ is not None:
            bullets.append(
                f"Biggest spike: {period}, {_keur_phrase(pv)} vs ~{_keur_phrase(typ)} typical"
            )
        elif pv is not None:
            bullets.append(f"Biggest spike: {period}, {_keur_phrase(pv)}")

    # Trend direction.
    trend = _trend_word(out_node) or _trend_word(sea_node)
    if trend == "rising":
        bullets.append("Upward trend over the period")
    elif trend == "falling":
        bullets.append("Downward trend over the period")

    # Seasonality off-pattern note (only when seasonality is itself notable).
    if sea_node is not None and sea_score >= _NOTABLE_MIN_SCORE:
        bullets.append("Some months are off the usual seasonal pattern")

    # Which analyses flagged it.
    names = _flag_names(out_score, sea_score, f_count)
    if names:
        bullets.append("Flagged in: " + ", ".join(names))

    # Guarantee at least 2, cap at 4.
    if not bullets:
        bullets.append("Slightly above its usual range")
    if len(bullets) == 1:
        bullets.append("Worth a quick look in the detail views")
    return bullets[:4]


def _spark_point_signal_score(p: dict[str, Any]) -> int:
    """Per-point 0..100 signal score from a series point (0 when absent/invalid)."""
    try:
        return int(p.get("signal_score") or 0)
    except (TypeError, ValueError):
        return 0


def _spark_series(
    out_node: Optional[dict[str, Any]],
    sea_node: Optional[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compact ``[{label, value, expected, signal_score}]`` monthly series.

    Downsampled to ``<= SPARK_MAX_POINTS`` by an even stride so the FE sparkline stays
    small regardless of how many months the position has.  Each carried point:

      * ``label``        — the month label string from the SOURCE point (``p.label``,
        e.g. "Jul24") — never an index.
      * ``value``        — the actual presented value (kEUR), as before.
      * ``expected``     — a REFERENCE value per point.  For an OUTLIER-driven card
        (``out_node`` present) this is the node's MEAN (``payload.stats.mean_keur`` —
        a flat reference repeated on every point).  For a SEASONALITY-driven card
        (only ``sea_node``) it is the point's own ``expected_keur``.  ``None`` when no
        reference is available.
      * ``signal_score`` — the per-point ``signal_score`` (0..100) so the FE can mark
        flagged points by its client threshold.

    Source-node choice mirrors the prior behaviour: the outlier node's value series is
    preferred (raw monthly values, flat-mean reference); the seasonality node's actuals
    + per-point ``expected_keur`` are the fallback.
    """
    node = out_node or sea_node
    if not node:
        return []
    series = ((node.get("payload") or {}).get("series")) or []

    # Outlier-driven → flat MEAN reference; seasonality-driven → per-point expected_keur.
    outlier_driven = out_node is not None
    flat_mean: Optional[float] = None
    if outlier_driven:
        stats = ((node.get("payload") or {}).get("stats")) or {}
        m = stats.get("mean_keur")
        try:
            flat_mean = float(m) if m is not None else None
        except (TypeError, ValueError):
            flat_mean = None

    pts: list[dict[str, Any]] = []
    for p in series:
        v = _point_value(p)
        if v is None:
            continue
        if outlier_driven:
            expected = flat_mean
        else:
            exp = p.get("expected_keur")
            try:
                expected = float(exp) if exp is not None else None
            except (TypeError, ValueError):
                expected = None
        pts.append({
            "label": p.get("label") or p.get("period_key") or "",
            "value": v,
            "expected": expected,
            "signal_score": _spark_point_signal_score(p),
        })

    if len(pts) <= SPARK_MAX_POINTS:
        return pts
    # Even stride downsample, always keeping the last point (most recent month).
    stride = (len(pts) + SPARK_MAX_POINTS - 1) // SPARK_MAX_POINTS
    sampled = pts[::stride]
    if sampled and sampled[-1] is not pts[-1]:
        sampled.append(pts[-1])
    return sampled[:SPARK_MAX_POINTS]


def build_overview_payload(
    outliers: dict[str, Any],
    seasonality: dict[str, Any],
    forensic: dict[str, Any],
    *,
    entity_prefixes: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Build the per-L3 overview REPORT cards from the three analyses (PURE).

    For each NOTABLE L3 position (signal score >= ``_NOTABLE_MIN_SCORE`` in at least
    one of outliers/seasonality, OR any forensic finding) we emit ONE layperson-report
    card:

      * ``signal_score`` — the MAX 0..100 score across the three analyses, with
        ``band`` ('low'|'medium'|'high') from :func:`anomaly_score.band`.
      * ``headline`` — a punchy plain-English one-liner from the STRONGEST analysis
        (peak month + magnitude / trend / forensic count).  NON-statistical.
      * ``bullets`` — 2–4 short plain-English points (biggest spike, trend, flagged-in).
      * ``spark_series`` — ``[{label, value, expected, signal_score}]`` monthly series,
        downsampled to ``<= SPARK_MAX_POINTS`` for a FE sparkline/mini-chart.
      * ``flags.{outliers,seasonality,forensic}`` — each ``{score, band, deep_link}``
        (the z-derived status is dropped; ``score`` drives the FE Sensitivity slider).

    ALL notable positions are carried so the client can filter by a min-score slider;
    the FE hides cards below its threshold.  Cards are grouped P&L first then BS, each
    group sorted by ``signal_score`` descending then by L3 key.
    """
    out_nodes = {n["key"]: n for n in _l3_nodes(outliers)}
    sea_nodes = {n["key"]: n for n in _l3_nodes(seasonality)}

    # Forensic flag counts rolled up to the L3 position label.
    forensic_counts = _forensic_counts_by_position(forensic)

    # All L3 keys seen across both trees, preserving level_0 / label from a node.
    meta_by_key: dict[str, dict[str, Any]] = {}
    for key, node in {**sea_nodes, **out_nodes}.items():
        meta_by_key[key] = {
            "level_0": node.get("level_0", ""),
            "level_2": node.get("level_2", ""),
            "level_3": node.get("level_3", key),
            "label": node.get("label", key),
            "statement": node.get("statement", ""),
        }

    cards: list[dict[str, Any]] = []
    for key, meta in meta_by_key.items():
        out_node = out_nodes.get(key)
        sea_node = sea_nodes.get(key)
        out_score = _node_score(out_node)
        sea_score = _node_score(sea_node)
        f_count = forensic_counts.get(meta["level_3"], 0)
        f_score = _forensic_score(f_count)

        # NOTABLE gate: medium+ band in a tree OR any forensic finding.
        notable = (
            out_score >= _NOTABLE_MIN_SCORE
            or sea_score >= _NOTABLE_MIN_SCORE
            or f_count > 0
        )
        if not notable:
            continue

        signal_score = max(out_score, sea_score, f_score)
        # Strongest analysis drives the headline (forensic wins ties when present).
        if f_score == signal_score and f_count > 0:
            strongest = "forensic"
        elif out_score >= sea_score:
            strongest = "outliers"
        else:
            strongest = "seasonality"

        flags = {
            "outliers": {
                "score": out_score,
                "band": score_band(out_score),
                "deep_link": _deep_link("outliers", key),
            },
            "seasonality": {
                "score": sea_score,
                "band": score_band(sea_score),
                "deep_link": _deep_link("seasonality", key),
            },
            "forensic": {
                "score": f_score,
                "band": score_band(f_score),
                "deep_link": _deep_link("forensic", key),
            },
        }

        cards.append({
            "key": key,
            "label": meta["label"],
            "level_0": meta["level_0"],
            "level_3": meta["level_3"],
            "statement": meta["statement"],
            "signal_score": signal_score,
            "band": score_band(signal_score),
            "headline": _overview_headline(
                meta["label"], strongest=strongest,
                out_node=out_node, sea_node=sea_node, f_count=f_count,
            ),
            "bullets": _overview_bullets(
                out_node=out_node, sea_node=sea_node,
                out_score=out_score, sea_score=sea_score, f_count=f_count,
            ),
            "spark_series": _spark_series(out_node, sea_node),
            "flags": flags,
        })

    pl = _sort_cards([c for c in cards if c["level_0"] == "PL"])
    bs = _sort_cards([c for c in cards if c["level_0"] == "BS"])
    other = _sort_cards([c for c in cards if c["level_0"] not in ("PL", "BS")])
    ordered = pl + bs + other

    return {
        "analysis": "overview",
        "cards": ordered,
        "groups": {
            "pl": [c["key"] for c in pl],
            "bs": [c["key"] for c in bs],
        },
        "meta": {
            "entity_prefixes": cache.entity_scope_key(entity_prefixes),
            "algorithm_version": OVERVIEW_ALGORITHM_VERSION,
            "tree_algorithm_version": TREE_ALGORITHM_VERSION,
            "forensic_algorithm_version": FORENSIC_ALGORITHM_VERSION,
            "notable_min_score": _NOTABLE_MIN_SCORE,
            "spark_max_points": SPARK_MAX_POINTS,
        },
    }


def _forensic_counts_by_position(forensic: dict[str, Any]) -> dict[str, int]:
    """Sum forensic flag counts per L3 position label (level_3-keyed).

    Unexpected-counter positions carry ``flag_count``; suspicious-text rows are one
    flag each, grouped by their ``level_3``.  Keyed by ``level_3`` (the same field
    the trees use for an L3 node's key) so the overview can join them.
    """
    counts: dict[str, int] = {}
    for pos in forensic.get("unexpected_counter_positions") or []:
        l3 = (pos.get("level_3") or "").strip()
        if not l3:
            continue
        counts[l3] = counts.get(l3, 0) + int(pos.get("flag_count") or 0)
    for row in forensic.get("suspicious_texts") or []:
        # suspicious rows carry a position label, not a separate level_3; the
        # position label IS the level_3 display value for these.
        l3 = (row.get("position") or "").strip()
        if not l3:
            continue
        counts[l3] = counts.get(l3, 0) + 1
    return counts


def _sort_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort cards by signal_score descending then by L3 key ascending."""
    return sorted(
        cards,
        key=lambda c: (-int(c.get("signal_score") or 0), c["key"]),
    )
