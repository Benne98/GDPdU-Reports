"""Deterministic anomaly detection over the GDPdU compat statement layer.

reporting-v2 Phase 6.  Scans the already-built P&L / BS / WC / CF statements for
*material, explainable* anomalies and returns a deterministically-ordered list.

WHY THIS IS NOT NEW FINANCIAL LOGIC
-----------------------------------
All materiality thresholds are REUSED verbatim from
``fin_compat_narrative_core`` (``MOM_FLOOR_EUR``, ``SIZE_FLOOR_EUR``,
``MOM_PCT_OF_CM``, ``YOY_REL_FACTOR``, ``ACCOUNT_CONCENTRATION_PCT``,
``BOOKING_SHARE_PCT``).  The per-line facts (display_mom / display_yoy /
favorable_mom / share_of_base / pct_mom) come from
``core.compute_line_facts`` and the GL concentration from
``core.gl_concentration_from_detail`` — the exact same engine the narratives use.
This module only *classifies* those facts into anomaly KINDS and a SEVERITY band;
it introduces no new monetary formula.

SIGN CONVENTIONS
----------------
Statement rows arrive already presented & invert/flip-applied (see
``fin_compat_narrative_core`` module docstring).  We therefore use
``display_mom = cm - pm`` and ``display_yoy = cm - py_cm`` directly and never
re-apply ``invert_delta``.  All amounts/deltas are EUR.

ANOMALY KINDS
-------------
  mom_swing        |display_mom| >= max(MOM_FLOOR_EUR, MOM_PCT_OF_CM*|cm|)
  yoy_swing        |display_yoy| >= max(MOM_FLOOR_EUR, MOM_PCT_OF_CM*|cm|*YOY_REL)
  sign_flip        cm and pm (or cm and py_cm) have opposite signs and the
                   crossing magnitude (|cm|+|prior|) is material (>= MOM_FLOOR_EUR)
  balance_break    BS only: |TotalAssets.cm - TotalEquity&Liab.cm| >= BALANCE_BREAK_FLOOR_EUR
  gl_concentration a single GL account drives >= ACCOUNT_CONCENTRATION_PCT of a
                   material move, OR a single posting >= BOOKING_SHARE_PCT.

SEVERITY (deterministic banding on the anomaly's magnitude_eur)
  high   >= 10 * MOM_FLOOR_EUR   (300k by default)  OR balance_break / gl_concentration
  medium >= 3  * MOM_FLOOR_EUR   (90k  by default)
  low    otherwise (still above the materiality floor)

ORDERING
--------
Deterministic: severity rank (high<medium<low) then magnitude_eur desc, then
statement, line_code, kind (stable tiebreak).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from app.services import fin_compat_narrative_core as core

# ---------------------------------------------------------------------------
# Severity banding — multiples of the REUSED narrative-core MoM floor (no new
# constant invented; everything is derived from core.MOM_FLOOR_EUR).
# ---------------------------------------------------------------------------
SEVERITY_HIGH_FACTOR = 10.0   # >= 10x MoM floor → high
SEVERITY_MEDIUM_FACTOR = 3.0  # >= 3x  MoM floor → medium

# Balance-sheet imbalance floor: a balanced BS should be (near) zero.  Reuse the
# MoM floor as the materiality bar for an Assets-(Equity+Liab) break.
BALANCE_BREAK_FLOOR_EUR = core.MOM_FLOOR_EUR

_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}

_BS_TOTAL_ASSETS = "BS_GRANDTOTAL_ASSETS"
_BS_TOTAL_EQ_LIAB = "BS_GRANDTOTAL_EQ_LIAB"


@dataclass
class Anomaly:
    """One detected anomaly.  ``id`` is None until cached into ``fact_anomaly``."""

    statement: str
    line_code: str
    label: str
    kind: str
    severity: str
    period_grain: str
    value: float                      # the current value (cm) the anomaly is about
    delta: float                      # the move that triggered it (mom / yoy / break)
    magnitude_eur: float              # |delta| used for ranking & severity
    description: str
    entity: Optional[str] = None
    year: Optional[int] = None
    month: Optional[int] = None
    iso_year: Optional[int] = None
    iso_week: Optional[int] = None
    id: Optional[int] = None
    detected_at: Optional[str] = None

    def sort_key(self) -> tuple:
        return (
            _SEVERITY_RANK.get(self.severity, 9),
            -abs(self.magnitude_eur),
            self.statement,
            self.line_code,
            self.kind,
        )


# ---------------------------------------------------------------------------
# Severity helper
# ---------------------------------------------------------------------------
def _severity_for_magnitude(mag: float) -> str:
    """Band an EUR magnitude into high/medium/low using MoM-floor multiples.

    Worked example (MOM_FLOOR_EUR=30k): 350k → high (>=300k); 120k → medium
    (>=90k); 40k → low.  Edge: exactly 300k → high; exactly 90k → medium.
    """
    a = abs(mag)
    if a >= SEVERITY_HIGH_FACTOR * core.MOM_FLOOR_EUR:
        return "high"
    if a >= SEVERITY_MEDIUM_FACTOR * core.MOM_FLOOR_EUR:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Per-line scans
# ---------------------------------------------------------------------------
def _scan_line(
    statement: str,
    facts: dict[str, Any],
    *,
    period_grain: str,
    gl_conc: Optional[dict[str, Any]],
) -> list[Anomaly]:
    """Classify one line's facts into zero-or-more anomalies (deterministic)."""
    out: list[Anomaly] = []
    line_code = facts["line_code"]
    label = facts["label"]
    cm = float(facts["cm"])
    pm = float(facts["pm"])
    py_cm = float(facts["py_cm"])
    mom = float(facts["display_mom"])
    yoy = float(facts["display_yoy"])

    cm_abs = abs(cm)
    mom_floor = max(core.MOM_FLOOR_EUR, core.MOM_PCT_OF_CM * max(cm_abs, 1.0))
    yoy_floor = max(core.MOM_FLOOR_EUR, core.MOM_PCT_OF_CM * max(cm_abs, 1.0) * core.YOY_REL_FACTOR)

    # --- MoM swing -------------------------------------------------------
    if abs(mom) >= mom_floor:
        pct = facts.get("pct_mom")
        pct_clause = f" ({pct:+.0f}%)" if pct is not None else ""
        out.append(Anomaly(
            statement=statement, line_code=line_code, label=label,
            kind="mom_swing", severity=_severity_for_magnitude(mom),
            period_grain=period_grain, value=round(cm, 2), delta=round(mom, 2),
            magnitude_eur=round(abs(mom), 2),
            description=(
                f"{label} moved {core.fmt_keur_signed(mom)} vs prior period"
                f"{pct_clause}, to {core.fmt_keur(cm)}."
            ),
        ))

    # --- YoY swing -------------------------------------------------------
    if abs(yoy) >= yoy_floor:
        out.append(Anomaly(
            statement=statement, line_code=line_code, label=label,
            kind="yoy_swing", severity=_severity_for_magnitude(yoy),
            period_grain=period_grain, value=round(cm, 2), delta=round(yoy, 2),
            magnitude_eur=round(abs(yoy), 2),
            description=(
                f"{label} is {core.fmt_keur_signed(yoy)} year-on-year, "
                f"to {core.fmt_keur(cm)}."
            ),
        ))

    # --- Sign flip (vs prior month preferred, else prior year) ----------
    flip_prior, flip_label = (pm, "prior period") if _crossed_zero(cm, pm) else (
        (py_cm, "prior year") if _crossed_zero(cm, py_cm) else (None, "")
    )
    if flip_prior is not None:
        crossing = abs(cm) + abs(flip_prior)
        if crossing >= core.MOM_FLOOR_EUR:
            out.append(Anomaly(
                statement=statement, line_code=line_code, label=label,
                kind="sign_flip", severity=_severity_for_magnitude(crossing),
                period_grain=period_grain, value=round(cm, 2),
                delta=round(cm - flip_prior, 2),
                magnitude_eur=round(crossing, 2),
                description=(
                    f"{label} flipped sign vs {flip_label}: "
                    f"{core.fmt_keur(flip_prior)} → {core.fmt_keur(cm)}."
                ),
            ))

    # --- GL concentration (only meaningful for a material mover) ---------
    if gl_conc and abs(mom) >= core.MOM_FLOOR_EUR * 0.5:
        acct_share = float(gl_conc.get("top_account_share") or 0)
        booking_share = float(gl_conc.get("booking_share") or 0)
        if acct_share >= core.ACCOUNT_CONCENTRATION_PCT or booking_share >= core.BOOKING_SHARE_PCT:
            acct = core.format_account(
                gl_conc.get("top_account_name"), gl_conc.get("top_account_gl_id")
            )
            out.append(Anomaly(
                statement=statement, line_code=line_code, label=label,
                kind="gl_concentration", severity="high",
                period_grain=period_grain, value=round(cm, 2), delta=round(mom, 2),
                magnitude_eur=round(abs(mom), 2),
                description=(
                    f"{label} move is concentrated: {acct} drives "
                    f"{acct_share * 100:.0f}% of the move"
                    + (f"; a single posting drives {booking_share * 100:.0f}%."
                       if booking_share >= core.BOOKING_SHARE_PCT else ".")
                ),
            ))
    return out


def _crossed_zero(a: float, b: float, *, eps: float = 1e-6) -> bool:
    """True when ``a`` and ``b`` have strictly opposite signs (both non-trivial)."""
    if abs(a) < eps or abs(b) < eps:
        return False
    return (a > 0) != (b > 0)


def _scan_balance_break(rows: list[dict], period_grain: str) -> list[Anomaly]:
    """BS only: flag |TotalAssets - Total Equity&Liabilities| over the floor."""
    ta = _find_row(rows, _BS_TOTAL_ASSETS)
    el = _find_row(rows, _BS_TOTAL_EQ_LIAB)
    if not ta or not el:
        return []
    a_cm = float((ta.get("amounts") or {}).get("cm") or 0)
    e_cm = float((el.get("amounts") or {}).get("cm") or 0)
    imbalance = a_cm - e_cm
    if abs(imbalance) < BALANCE_BREAK_FLOOR_EUR:
        return []
    return [Anomaly(
        statement="bs", line_code=_BS_TOTAL_ASSETS, label="Balance-sheet identity",
        kind="balance_break", severity="high", period_grain=period_grain,
        value=round(a_cm, 2), delta=round(imbalance, 2),
        magnitude_eur=round(abs(imbalance), 2),
        description=(
            f"Balance sheet does not balance: total assets {core.fmt_keur(a_cm)} "
            f"vs total equity & liabilities {core.fmt_keur(e_cm)} "
            f"({core.fmt_keur_signed(imbalance)})."
        ),
    )]


def _find_row(rows: list[dict], code: str) -> Optional[dict]:
    for r in rows:
        if r.get("line_code") == code:
            return r
        hit = _find_row(r.get("children") or [], code)
        if hit:
            return hit
    return None


# ---------------------------------------------------------------------------
# Statement scan (pure — DB only enters via the optional gl_detail callback)
# ---------------------------------------------------------------------------
def detect_for_statement(
    statement: str,
    rows: list[dict],
    *,
    base_cm: float,
    period_grain: str = "month",
    gl_detail_fn: Optional[Callable[[str, float], Optional[dict]]] = None,
    gl_sign_fn: Optional[Callable[[str], float]] = None,
) -> list[Anomaly]:
    """Detect anomalies for one already-built statement tree.

    Pure & DB-free unless ``gl_detail_fn`` is supplied (it reuses the same line
    detail the narratives use, only for the biggest movers, to bound DB work).
    """
    candidates = core.flatten_candidate_lines(rows)
    facts = [core.compute_line_facts(r, base_cm) for r in candidates]

    out: list[Anomaly] = []
    for f in facts:
        gl_conc: Optional[dict[str, Any]] = None
        if gl_detail_fn is not None and abs(f["display_mom"]) >= core.MOM_FLOOR_EUR:
            try:
                payload = gl_detail_fn(f["line_code"], f["favorable_mom"])
            except Exception:
                payload = None
            if payload:
                sign = gl_sign_fn(f["line_code"]) if gl_sign_fn else 1.0
                gl_conc = core.gl_concentration_from_detail(payload, sign=sign)
        out.extend(_scan_line(statement, f, period_grain=period_grain, gl_conc=gl_conc))

    if statement == "bs":
        out.extend(_scan_balance_break(rows, period_grain))

    return out


# ---------------------------------------------------------------------------
# Public entry point — build all four statements then scan
# ---------------------------------------------------------------------------
def detect_anomalies(
    session: Session,
    period: dict[str, Any],
    entity: Optional[str] = None,
    *,
    with_gl_concentration: bool = True,
) -> list[Anomaly]:
    """Build PL + BS for ``period`` and return ordered anomalies.

    ``period`` = {grain, year, month, iso_year, iso_week}.  ``entity`` None/'all'
    → consolidated.  Output is deterministically ordered (severity, magnitude).

    Scope is PL + BS only (Phase 0 of the hierarchical anomaly rework): WC/CF are
    derived statements whose moves already surface via PL/BS, so they are no longer
    scanned.  The WC/CF statement endpoints themselves are unchanged.
    """
    from app.services.fin_compat_bs import build_bs_line_detail, build_bs_statement_compat
    from app.services.fin_compat_line_detail import build_pl_line_detail
    from app.services.fin_compat_pl import build_pl_statement_compat
    from app.services.fin_compat_sql import plan_anchor_for_week

    grain = period.get("grain", "month")
    year = period.get("year")
    month = period.get("month")
    iso_year = period.get("iso_year")
    iso_week = period.get("iso_week")

    ent = entity
    if ent and str(ent).strip().lower() in ("", "all"):
        ent = None

    if grain == "week" and iso_year is not None and iso_week is not None:
        det_year, det_month = plan_anchor_for_week(iso_year, iso_week)
    else:
        det_year, det_month = year, month

    # Hierarchical anomaly rework (Phase 0): PL + BS ONLY.  WC/CF are derived
    # statements (working-capital + cash-flow are mechanical roll-ups of PL/BS),
    # so scanning them would double-count the same underlying GL moves.  The WC/CF
    # statement endpoints themselves are unchanged — only this detector stops
    # reading them.
    builders = {
        "pl": build_pl_statement_compat,
        "bs": build_bs_statement_compat,
    }
    line_detail = {
        "pl": build_pl_line_detail,
        "bs": build_bs_line_detail,
    }

    all_anoms: list[Anomaly] = []
    for stmt, builder in builders.items():
        try:
            payload = builder(
                session,
                period_grain=grain,
                year=year, month=month,
                iso_year=iso_year, iso_week=iso_week,
                entity=ent,
            )
        except Exception:
            continue
        rows = payload.get("rows") or []

        # Base for share-of-base — revenue for PL/CF, total assets for BS/WC.
        if stmt in ("pl", "cf"):
            base_row = _find_row(rows, "NET_SALES") or _find_row(rows, "TOTAL_OUTPUT")
        else:
            base_row = _find_row(rows, _BS_TOTAL_ASSETS)
        base_cm = abs(float((base_row or {}).get("amounts", {}).get("cm") or 0)) or 1.0

        gl_fn = None
        if with_gl_concentration and det_year is not None and det_month is not None:
            detail_builder = line_detail[stmt]
            # WC line detail otherwise re-builds the whole WC statement per line to
            # resolve the drill scope (an N+1 explosion); hand it the tree we just
            # built so it resolves the scope in-memory instead.
            _wc_rows = rows if stmt == "wc" else None

            def _gl(
                line_code: str, signed_mom_eur: float,
                _b=detail_builder, _stmt=stmt, _pre=_wc_rows,
            ) -> Optional[dict]:
                try:
                    kwargs: dict[str, Any] = {
                        "use_llm": False,
                        "line_mom_keur": signed_mom_eur / 1000.0,
                        # concentration_only: skip the timeline / sub-line /
                        # commentary queries the GL-concentration classifier never
                        # reads — keeps accounts + top_bookings (the only inputs to
                        # core.gl_concentration_from_detail) so results are identical.
                        "concentration_only": True,
                    }
                    if _stmt == "wc":
                        kwargs["prebuilt_rows"] = _pre
                    return _b(session, line_code, det_year, det_month, ent, **kwargs)
                except Exception:
                    return None

            gl_fn = _gl

        anoms = detect_for_statement(
            stmt, rows, base_cm=base_cm, period_grain=grain, gl_detail_fn=gl_fn,
        )
        for a in anoms:
            a.entity = ent
            a.year = year
            a.month = month
            a.iso_year = iso_year
            a.iso_week = iso_week
        all_anoms.extend(anoms)

    all_anoms.sort(key=lambda a: a.sort_key())
    return all_anoms


# ---------------------------------------------------------------------------
# Materiality anchor (hierarchical rework, Phase 0)
# ---------------------------------------------------------------------------
def latest_anchor(session: Session) -> Optional[tuple[int, int]]:
    """Return the latest available ``(fiscal_year, fiscal_period)`` in the ledger.

    The hierarchical anomaly views are param-free (all years, no period filter), so
    materiality is anchored at the most recent real month present.  This is the last
    element of :func:`gl_analysis_common.discover_month_span` (synthetic rows already
    excluded, ``fiscal_period BETWEEN 1 AND 12``).  Empty ledger → ``None``.

    NOTE: the latest month may be partially booked; downstream callers should treat
    the anchor as "newest available", not "complete period".
    """
    from app.services.gl_analysis_common import discover_month_span

    span = discover_month_span(session)
    return span[-1] if span else None


# ---------------------------------------------------------------------------
# Narrative-basis adapter (Task 4) — anomalies as a basis for narrative bullets
# ---------------------------------------------------------------------------
def anomalies_for_narrative(
    session: Session,
    statement: str,
    *,
    period_grain: str = "month",
    year: Optional[int] = None,
    month: Optional[int] = None,
    iso_year: Optional[int] = None,
    iso_week: Optional[int] = None,
    entity: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Reusable adapter the narrative layer can consume to enrich bullets.

    Returns the anomalies for ONE statement as plain dicts keyed by ``line_code``
    semantics the narrative engine already speaks (line_code, kind, severity,
    delta, magnitude_eur, description).  Does NOT replace the existing narrative
    endpoints — it only makes the anomaly signal available alongside them.
    """
    period = {
        "grain": period_grain, "year": year, "month": month,
        "iso_year": iso_year, "iso_week": iso_week,
    }
    anoms = detect_anomalies(session, period, entity)
    return [
        {
            "line_code": a.line_code,
            "label": a.label,
            "kind": a.kind,
            "severity": a.severity,
            "delta": a.delta,
            "magnitude_eur": a.magnitude_eur,
            "description": a.description,
        }
        for a in anoms
        if a.statement == statement
    ]


# ---------------------------------------------------------------------------
# Journal Agent (Phase 6) — GL findings as Anomaly records + narrative bullets
# ---------------------------------------------------------------------------
# New anomaly KINDS sourced from the three GL analysis services (gl_outliers /
# gl_seasonality / gl_forensic).  These carry NO new monetary formula: the
# magnitude (EUR, for ranking + severity) is taken directly from the service
# outputs (residual / amount / delta, already in kEUR → ×1000), severity uses the
# existing ``_severity_for_magnitude`` band, and ordering uses the existing
# ``Anomaly.sort_key()``.  Each finding is bounded by reusing the services' own
# caps (MAX_ACCOUNTS / MAX_FORENSIC_ROWS) and a per-kind TOP-N below.
_GL_FINDING_KINDS = frozenset(
    {"outlier", "seasonality", "counter_account", "other_position", "suspicious_text"}
)

# Per-kind TOP-N taken from each (already bounded + sorted) service list so the
# narrative is enriched with the most material findings only, never the long tail.
_GL_FINDING_TOP_N = 8

_EUR_PER_KEUR = 1000.0


def _series_peak_finding(account: dict[str, Any]) -> Optional[tuple[float, dict[str, Any]]]:
    """Return ``(abs_z, point)`` for the account's largest-|z| series point, or None.

    Used by both the outlier and seasonality mappers: the single most off-series
    month per account (|z| maximal).  ``None`` when the account has no finite z
    (flat / insufficient series).  The slider default σ=1.0 is the inclusion bar.
    """
    best: Optional[tuple[float, dict[str, Any]]] = None
    for p in account.get("series") or []:
        z = p.get("z")
        if z is None:
            continue
        az = abs(float(z))
        if best is None or az > best[0]:
            best = (az, p)
    return best


def detect_gl_findings(
    session: Session,
    period: dict[str, Any],
    entity: Optional[str],
) -> list[Anomaly]:
    """Run the 3 GL analysis services and map their TOP/material findings to
    ``Anomaly`` records (deterministically ordered).

    Bounded: each service already caps + material-bounds its output; we additionally
    take the most-material ``_GL_FINDING_TOP_N`` per kind.  New ``kind`` values:
    ``outlier`` / ``seasonality`` / ``counter_account`` / ``other_position`` /
    ``suspicious_text``.  ``magnitude_eur`` comes straight from the service figures
    (kEUR → EUR); severity via ``_severity_for_magnitude``; order via ``sort_key()``.

    Statement tagging: outlier / seasonality findings carry the account's own
    statement ('pl' | 'bs'); forensic findings (GL bookings) are tagged 'pl' — the
    first integration target — so the PL narrative consumes them.  Pure read.
    """
    from app.services.gl_forensic import build_forensic
    from app.services.gl_outliers import SIGMA_DEFAULT as OUT_SIGMA, build_outliers
    from app.services.gl_seasonality import SIGMA_DEFAULT as SEA_SIGMA, build_seasonality

    grain = period.get("grain", "month")
    ent = entity
    if ent and str(ent).strip().lower() in ("", "all"):
        ent = None

    out: list[Anomaly] = []

    # -- 1) Outliers (largest off-series month per account, |z| >= σ default) ---
    try:
        op = build_outliers(session, period, ent, "all")
    except Exception:
        op = None
    if op:
        cand: list[tuple[float, Anomaly]] = []
        for acc in op.get("accounts") or []:
            peak = _series_peak_finding(acc)
            if peak is None or peak[0] < OUT_SIGMA:
                continue
            az, pt = peak
            val_eur = float(pt.get("value_keur") or 0.0) * _EUR_PER_KEUR
            resid_eur = float(pt.get("residual_keur") or 0.0) * _EUR_PER_KEUR
            mag = abs(resid_eur)
            acct = core.format_account(acc.get("account_name"), acc.get("gl_account_id"))
            cand.append((mag, Anomaly(
                statement=acc.get("statement") or "pl",
                line_code=str(acc.get("gl_account_id") or acc.get("account_name") or ""),
                label=acc.get("account_name") or acc.get("gl_account_id") or "",
                kind="outlier", severity=_severity_for_magnitude(mag),
                period_grain=grain, value=round(val_eur, 2),
                delta=round(resid_eur, 2), magnitude_eur=round(mag, 2),
                description=(
                    f"{acct} shows an outlier month at {pt.get('label')}: "
                    f"{core.fmt_keur(val_eur)} ({core.fmt_keur_signed(resid_eur)} vs its "
                    f"mean, z={az:.1f})."
                ),
            )))
        cand.sort(key=lambda t: -t[0])
        out.extend(a for _, a in cand[:_GL_FINDING_TOP_N])

    # -- 2) Seasonality (largest off-season month per account) ------------------
    try:
        sp = build_seasonality(session, period, ent, "all")
    except Exception:
        sp = None
    if sp:
        cand = []
        for acc in sp.get("accounts") or []:
            if acc.get("insufficient_history"):
                continue
            peak = _series_peak_finding(acc)
            if peak is None or peak[0] < SEA_SIGMA:
                continue
            az, pt = peak
            val_eur = float(pt.get("actual_keur") or 0.0) * _EUR_PER_KEUR
            resid_eur = float(pt.get("residual_keur") or 0.0) * _EUR_PER_KEUR
            mag = abs(resid_eur)
            acct = core.format_account(acc.get("account_name"), acc.get("gl_account_id"))
            direction = "above" if resid_eur >= 0 else "below"
            cand.append((mag, Anomaly(
                statement=acc.get("statement") or "pl",
                line_code=str(acc.get("gl_account_id") or acc.get("account_name") or ""),
                label=acc.get("account_name") or acc.get("gl_account_id") or "",
                kind="seasonality", severity=_severity_for_magnitude(mag),
                period_grain=grain, value=round(val_eur, 2),
                delta=round(resid_eur, 2), magnitude_eur=round(mag, 2),
                description=(
                    f"{acct} is off-season at {pt.get('label')}: "
                    f"{core.fmt_keur(val_eur)}, {core.fmt_keur(abs(resid_eur))} {direction} "
                    f"the expected seasonal level (z={az:.1f})."
                ),
            )))
        cand.sort(key=lambda t: -t[0])
        out.extend(a for _, a in cand[:_GL_FINDING_TOP_N])

    # -- 3) Forensic (counter accounts + Other positions + suspicious texts) ----
    try:
        fp = build_forensic(session, period, ent)
    except Exception:
        fp = None
    if fp:
        # 3a) unexpected counter accounts (new / rare)
        cand = []
        for r in (fp.get("unexpected_counter_accounts") or [])[:_GL_FINDING_TOP_N]:
            amt_eur = float(r.get("amount_keur") or 0.0) * _EUR_PER_KEUR
            mag = abs(amt_eur)
            acct = core.format_account(r.get("account_name"), r.get("gl_account_id"))
            ctr = core.format_account(
                r.get("counter_account_name"), r.get("counter_gl_account_id")
            )
            novelty = r.get("novelty") or "unexpected"
            cand.append((mag, Anomaly(
                statement="pl",
                line_code=str(r.get("gl_account_id") or ""),
                label=r.get("account_name") or r.get("gl_account_id") or "",
                kind="counter_account", severity=_severity_for_magnitude(mag),
                period_grain=grain, value=round(amt_eur, 2),
                delta=round(amt_eur, 2), magnitude_eur=round(mag, 2),
                description=(
                    f"{acct} booked against a {novelty} counter {ctr} "
                    f"({core.fmt_keur_signed(amt_eur)}, "
                    f"{float(r.get('freq_pct') or 0.0) * 100:.0f}% of its history)."
                ),
            )))
        cand.sort(key=lambda t: -t[0])
        out.extend(a for _, a in cand[:_GL_FINDING_TOP_N])

        # 3b) Other positions (material AND growing)
        cand = []
        for r in (fp.get("other_positions") or [])[:_GL_FINDING_TOP_N]:
            delta_eur = float(r.get("delta_keur") or 0.0) * _EUR_PER_KEUR
            cm_eur = float(r.get("balance_cm_keur") or 0.0) * _EUR_PER_KEUR
            mag = abs(delta_eur)
            acct = core.format_account(r.get("account_name"), r.get("gl_account_id"))
            cand.append((mag, Anomaly(
                statement="pl",
                line_code=str(r.get("gl_account_id") or ""),
                label=r.get("account_name") or r.get("gl_account_id") or "",
                kind="other_position", severity=_severity_for_magnitude(mag),
                period_grain=grain, value=round(cm_eur, 2),
                delta=round(delta_eur, 2), magnitude_eur=round(mag, 2),
                description=(
                    f'{acct} ("Other" position) is material and growing: '
                    f"{core.fmt_keur(cm_eur)} "
                    f"({core.fmt_keur_signed(delta_eur)} vs prior period)."
                ),
            )))
        cand.sort(key=lambda t: -t[0])
        out.extend(a for _, a in cand[:_GL_FINDING_TOP_N])

        # 3c) suspicious texts (keyword-matched line notes)
        cand = []
        for r in (fp.get("suspicious_texts") or [])[:_GL_FINDING_TOP_N]:
            amt_eur = float(r.get("amount_keur") or 0.0) * _EUR_PER_KEUR
            mag = abs(amt_eur)
            acct = core.format_account(r.get("account_name"), r.get("gl_account_id"))
            kw = r.get("matched_keyword") or "flagged"
            note = core._sanitize_account_name(r.get("line_note") or "")[:80]
            cand.append((mag, Anomaly(
                statement="pl",
                line_code=str(r.get("gl_account_id") or ""),
                label=r.get("account_name") or r.get("gl_account_id") or "",
                kind="suspicious_text", severity=_severity_for_magnitude(mag),
                period_grain=grain, value=round(amt_eur, 2),
                delta=round(amt_eur, 2), magnitude_eur=round(mag, 2),
                description=(
                    f'{acct} has a flagged posting (keyword "{kw}"): '
                    f'"{note}" ({core.fmt_keur_signed(amt_eur)}).'
                ),
            )))
        cand.sort(key=lambda t: -t[0])
        out.extend(a for _, a in cand[:_GL_FINDING_TOP_N])

    for a in out:
        a.entity = ent
        a.year = period.get("year")
        a.month = period.get("month")
        a.iso_year = period.get("iso_year")
        a.iso_week = period.get("iso_week")

    out.sort(key=lambda a: a.sort_key())
    return out


def gl_findings_as_bullets(
    session: Session,
    period: dict[str, Any],
    entity: Optional[str],
    statement: str,
) -> list[dict[str, Any]]:
    """GL findings (Journal Agent) for ONE statement as narrative bullets.

    Runs :func:`detect_gl_findings`, filters to ``statement``, and renders each as a
    bullet in the EXISTING bullet shape
    (``index, line_code, label, text, tone, deep_links, facts``) used by
    ``fin_compat_narrative_core.build_bullets`` — so the merge step needs no special
    casing.  ``index`` is provisional (re-assigned by ``merge_finding_bullets``).
    Tone follows the finding's directional ``delta`` sign.  Pure read; only called
    behind the ``journal_agent_narrative`` flag.
    """
    stmt = (statement or "").strip().lower()
    findings = [a for a in detect_gl_findings(session, period, entity) if a.statement == stmt]

    bullets: list[dict[str, Any]] = []
    for i, a in enumerate(findings):
        if a.delta > 1e-3:
            tone = "positive"
        elif a.delta < -1e-3:
            tone = "negative"
        else:
            tone = "neutral"
        bullets.append({
            "index": i,
            "line_code": a.line_code,
            "label": a.label,
            "text": a.description,
            "tone": tone,
            "deep_links": [],
            "facts": {
                "cm": round(a.value, 2),
                "mom": round(a.delta, 2),
                "yoy": 0.0,
                "share_of_base": 0.0,
                "top_account": None,
                "top_account_share": None,
                "hidden_netting": False,
                "reasons": [a.kind],
                "finding_kind": a.kind,
                "severity": a.severity,
                "magnitude_eur": a.magnitude_eur,
            },
        })
    return bullets


# ---------------------------------------------------------------------------
# Serialization + optional cache upsert
# ---------------------------------------------------------------------------
def _period_label(period: dict[str, Any]) -> str:
    grain = period.get("grain", "month")
    if grain == "week":
        return f"CW{period.get('iso_week')}'{str(period.get('iso_year') or '')[-2:]}"
    if grain == "year":
        return f"FY{period.get('year')}"
    return f"{period.get('year')}-{(period.get('month') or 0):02d}"


def anomaly_to_api(a: Anomaly, period: dict[str, Any]) -> dict[str, Any]:
    """Frontend-facing anomaly dict (the documented endpoint contract)."""
    return {
        "id": a.id,
        "statement": a.statement,
        "line_code": a.line_code,
        "label": a.label,
        "kind": a.kind,
        "severity": a.severity,
        "period_label": _period_label(period),
        "entity": a.entity or "all",
        "value": a.value,
        "delta": a.delta,
        "magnitude_eur": a.magnitude_eur,
        "description": a.description,
    }


def cache_anomalies(
    session: Session,
    anomalies: list[Anomaly],
    period: dict[str, Any],
    entity: Optional[str],
) -> None:
    """OPTIONAL upsert into ``fact_anomaly`` (cache only — request compute is truth).

    Deletes the prior rows for this exact scope then inserts the fresh set, inside
    the caller's transaction.  Best-effort: any failure (e.g. table absent on an
    un-migrated DB) is swallowed so it never breaks the live response.
    """
    from sqlalchemy import text

    grain = period.get("grain", "month")
    ent = None if (not entity or str(entity).strip().lower() in ("", "all")) else entity
    scope = {
        "grain": grain,
        "year": period.get("year"),
        "month": period.get("month"),
        "iso_year": period.get("iso_year"),
        "iso_week": period.get("iso_week"),
        "entity": ent,
    }
    try:
        session.execute(text(
            "DELETE FROM fact_anomaly WHERE period_grain = :grain "
            "AND year IS NOT DISTINCT FROM :year "
            "AND month IS NOT DISTINCT FROM :month "
            "AND iso_year IS NOT DISTINCT FROM :iso_year "
            "AND iso_week IS NOT DISTINCT FROM :iso_week "
            "AND entity IS NOT DISTINCT FROM :entity"
        ), scope)
        now = datetime.now(timezone.utc)
        for a in anomalies:
            session.execute(text(
                "INSERT INTO fact_anomaly "
                "(statement, line_code, label, kind, severity, period_grain, "
                " year, month, iso_year, iso_week, entity, value, delta, "
                " magnitude_eur, description, detected_at) "
                "VALUES (:statement, :line_code, :label, :kind, :severity, "
                " :period_grain, :year, :month, :iso_year, :iso_week, :entity, "
                " :value, :delta, :magnitude_eur, :description, :detected_at)"
            ), {
                "statement": a.statement, "line_code": a.line_code, "label": a.label,
                "kind": a.kind, "severity": a.severity, "period_grain": grain,
                "year": scope["year"], "month": scope["month"],
                "iso_year": scope["iso_year"], "iso_week": scope["iso_week"],
                "entity": ent, "value": a.value, "delta": a.delta,
                "magnitude_eur": a.magnitude_eur, "description": a.description,
                "detected_at": now,
            })
        session.commit()
    except Exception:
        session.rollback()
