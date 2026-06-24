"""Deterministic P&L narrative engine for the legacy compat layer.

Produces the PlNarrativeResponse shape expected by the ported frontend.
LLM path: when use_llm=True and ANTHROPIC_API_KEY is set in env, delegates to
the Anthropic API. If the key is missing or the call fails, falls back to the
deterministic engine without raising an error — same behaviour as the legacy.

Response shape matches frontend PlNarrativeResponse:
  headline, intro, intro_facts, bullets[], entity_split, meta
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.services.fin_compat_pl import _load_plan_map, build_pl_statement_compat
from app.services.fin_compat_sql import (
    col_labels_month,
    entity_sql_fragment,
    plan_anchor_for_week,
    period_label,
    resolve_entity_prefix,
)

_ALGO_VERSION = "pl_narrative_compat_v2"
_MAX_BULLETS = 5
_MIN_BULLETS = 2


def _tone(delta: float, invert: bool) -> str:
    adjusted = -delta if invert else delta
    if abs(adjusted) < 0.5:
        return "neutral"
    return "positive" if adjusted > 0 else "negative"


def _direction(delta: float) -> str:
    if abs(delta) < 0.5:
        return "stable"
    return "increase" if delta > 0 else "decrease"


def _fmt_keur(val: float) -> str:
    keur = val / 1000
    if abs(keur) >= 1000:
        return f"€{keur / 1000:.1f}m"
    return f"€{keur:.0f}k"


def _generate_bullet_text(label: str, am: dict, invert: bool) -> str:
    mom = float(am.get("mom") or 0)
    yoy = float(am.get("yoy") or 0)
    cm = float(am.get("cm") or 0)
    tone = _tone(mom, invert)
    dir_word = "increased" if mom > 0 else "decreased"
    if abs(mom) < 0.5:
        return f"{label} was broadly flat at {_fmt_keur(cm)} vs. prior month."
    mom_str = _fmt_keur(abs(mom))
    yoy_str = _fmt_keur(abs(yoy)) if abs(yoy) > 0.5 else None
    text = f"{label} {dir_word} by {mom_str} MoM"
    if yoy_str:
        dir_yoy = "up" if yoy > 0 else "down"
        text += f" and is {dir_yoy} {yoy_str} YoY"
    text += f" (current month: {_fmt_keur(cm)})."
    return text


def _find_row(rows: list[dict], code: str) -> Optional[dict]:
    """Depth-first lookup of a row by line_code (handles nested children)."""
    for r in rows:
        if r.get("line_code") == code:
            return r
        hit = _find_row(r.get("children") or [], code)
        if hit:
            return hit
    return None


def _plan_qualifier(plan_vs_actual: float, plan_cm: float) -> str:
    """Wording for actuals vs plan (mirrors legacy assemble.plan_qualifier)."""
    ratio = abs(plan_vs_actual) / max(abs(plan_cm), 1.0)
    ahead = plan_vs_actual > 0
    if ratio < 0.05:
        return "in line with forecast"
    if ratio < 0.15:
        return "slightly above forecast" if ahead else "slightly below forecast"
    return "ahead of forecast" if ahead else "below forecast"


def _to_plan_phrase(plan_vs_actual: float) -> str:
    """Signed plan variance phrase for intro driver clauses."""
    from app.services import fin_compat_narrative_core as core
    return f"({core.fmt_keur_signed(plan_vs_actual)} to plan)"


def _assemble_pl_intro(
    *,
    labels: dict[str, str],
    cm_label: str,
    group_label: str,
    np_ytd: float,
    np_cm: float,
    np_yoy: float,
    has_plan: bool,
    cm_vs_plan: float,
    plan_cm: float,
    qualifier: str,
    coverage_pct: Optional[float],
    primary_drivers: list[dict[str, Any]],
    period_grain: str,
) -> str:
    """Legacy-style overview intro (matches pl_narrative prose_v2 assemble_intro_v2)."""
    from app.services import fin_compat_narrative_core as core

    ytd_lbl = labels.get("ytd") or cm_label
    group_phrase = "the group's" if not group_label or group_label == "Group" else f"{group_label}'s"
    period_noun = (
        "week" if period_grain == "week"
        else "year" if period_grain == "year"
        else "month"
    )

    if has_plan:
        cov_part = f" leading to a coverage of {coverage_pct}%" if coverage_pct is not None else ""
        intro = (
            f"As of {ytd_lbl} {group_phrase} net profit amounted to "
            f"{core.fmt_keur(np_ytd)}{cov_part}, and actuals for {cm_label} were "
            f"{qualifier or 'in line with forecast'} "
            f"({core.fmt_keur_signed(cm_vs_plan)} to plan)."
        )
    else:
        intro = (
            f"As of {ytd_lbl} {group_phrase} net profit amounted to {core.fmt_keur(np_ytd)}"
        )
        if abs(np_yoy) >= 0.5:
            yd = "up" if np_yoy > 0 else "down"
            intro += f", {yd} {core.fmt_keur(abs(np_yoy))} year-on-year"
        intro += f", and actuals for {cm_label} were in line with forecast."

    if primary_drivers:
        d0 = primary_drivers[0]
        d0_txt = f"an {d0['direction']}" if d0["direction"] == "increase" else f"a {d0['direction']}"
        d0_txt += f" in {d0['label']}"
        if d0.get("to_plan"):
            d0_txt += f" {d0['to_plan']}"
        if len(primary_drivers) > 1:
            d1 = primary_drivers[1]
            d1_prefix = f"an {d1['direction']}" if d1["direction"] == "increase" else f"a {d1['direction']}"
            d1_txt = f"{d1_prefix} in {d1['label']}"
            if d1.get("to_plan"):
                d1_txt += f" {d1['to_plan']}"
            d0_txt += f" and {d1_txt}"
        intro += f" The {period_noun} was primarily shaped by {d0_txt}."
    intro += " To conclude, key drivers consist of:"
    return intro


def statement_for_narrative(
    session: Session,
    statement: str,
    *,
    period_grain: str,
    year: int,
    month: int,
    entity: Optional[str],
    iso_year: Optional[int] = None,
    iso_week: Optional[int] = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Load statement tree + column labels in narrative-compatible shape."""
    from app.services import fin_compat_narrative_core as core

    if period_grain == "year":
        if statement == "pl":
            from app.services.fin_compat_pl import build_pl_annual_compat
            stmt = build_pl_annual_compat(session, year=year, month=month, entity=entity)
            raw = stmt.get("col_labels") or {}
            return (
                core.normalize_annual_flow_rows(stmt.get("rows") or []),
                core.narrative_labels_from_flow_annual(raw),
            )
        if statement == "bs":
            from app.services.fin_compat_bs import build_bs_snapshot_annual
            stmt = build_bs_snapshot_annual(session, year=year, month=month, entity=entity)
            raw = stmt.get("col_labels") or {}
            return (
                core.normalize_annual_snapshot_rows(stmt.get("rows") or []),
                core.narrative_labels_from_snapshot_annual(raw),
            )
        if statement == "cf":
            from app.services.fin_compat_cf import build_cf_annual_compat
            stmt = build_cf_annual_compat(session, year=year, month=month, entity=entity)
            raw = stmt.get("col_labels") or {}
            return (
                core.normalize_annual_flow_rows(stmt.get("rows") or []),
                core.narrative_labels_from_flow_annual(raw),
            )
        if statement == "wc":
            from app.services.fin_compat_wc import build_wc_snapshot_annual
            stmt = build_wc_snapshot_annual(session, year=year, month=month, entity=entity)
            raw = stmt.get("col_labels") or {}
            return (
                core.normalize_annual_snapshot_rows(stmt.get("rows") or []),
                core.narrative_labels_from_snapshot_annual(raw),
            )

    if period_grain == "week" and iso_year is not None and iso_week is not None:
        yr, mo = plan_anchor_for_week(iso_year, iso_week)
    else:
        yr, mo = year, month

    if statement == "pl":
        stmt = build_pl_statement_compat(
            session,
            period_grain=period_grain,
            year=yr, month=mo,
            iso_year=iso_year, iso_week=iso_week,
            entity=entity,
        )
    elif statement == "bs":
        from app.services.fin_compat_bs import build_bs_statement_compat
        stmt = build_bs_statement_compat(
            session, period_grain=period_grain, year=yr, month=mo,
            iso_year=iso_year, iso_week=iso_week, entity=entity,
        )
    elif statement == "cf":
        from app.services.fin_compat_cf import build_cf_statement_compat
        stmt = build_cf_statement_compat(
            session, period_grain=period_grain, year=yr, month=mo,
            iso_year=iso_year, iso_week=iso_week, entity=entity,
        )
    elif statement == "wc":
        from app.services.fin_compat_wc import build_wc_statement_compat
        stmt = build_wc_statement_compat(
            session, period_grain=period_grain, year=yr, month=mo,
            iso_year=iso_year, iso_week=iso_week, entity=entity,
        )
    else:
        return [], col_labels_month(yr, mo)

    return stmt.get("rows") or [], stmt.get("col_labels") or col_labels_month(yr, mo)


def build_pl_narrative(
    session: Session,
    year: int, month: int, entity: Optional[str],
    *,
    period_grain: str = "month",
    iso_year: Optional[int] = None,
    iso_week: Optional[int] = None,
    max_bullets: Optional[int] = None,
    use_llm: bool = False,
) -> dict[str, Any]:
    """Build the deterministic P&L key-drivers narrative at legacy depth.

    Pipeline (see ``fin_compat_narrative_core``): flatten mapping lines → enrich
    each big mover with GL account/booking concentration (via
    ``build_pl_line_detail``) + hidden-netting → score (magnitude + share of
    revenue + concentration, with a revenue/COM anchor boost) → select the top-N
    drivers.  The intro is net-profit anchored with plan context when the GL plan
    scenario is present, otherwise year-on-year context (the GL-only schema has no
    guaranteed plan).  Output shape is the unchanged ``PlNarrativeResponse``.

    LLM stays optional and OFF by default: when ``use_llm`` is set and a key is
    present we attempt enhancement, falling back silently to the deterministic
    text on any failure.
    """
    from app.services import fin_compat_narrative_core as core
    from app.services.fin_compat_line_detail import build_pl_line_detail

    if period_grain == "week" and iso_year is not None and iso_week is not None:
        yr, mo = plan_anchor_for_week(iso_year, iso_week)
    else:
        yr, mo = year, month

    try:
        rows, labels = statement_for_narrative(
            session,
            "pl",
            period_grain=period_grain,
            year=year,
            month=month,
            entity=entity,
            iso_year=iso_year,
            iso_week=iso_week,
        )
    except Exception:
        rows, labels = [], col_labels_month(yr, mo)
    cm_label = labels.get("cm", period_label(yr, mo))
    pm_label = labels.get("pm", "the prior month")
    cap = core.clamp_cap(max_bullets)

    # Base for share-of-revenue and the relative score term.
    rev_row = _find_row(rows, "NET_SALES") or _find_row(rows, "TOTAL_OUTPUT")
    base_cm = abs(float((rev_row or {}).get("amounts", {}).get("cm") or 0)) or 1.0

    def _gl_detail(line_code: str, signed_mom_eur: float) -> Optional[dict]:
        try:
            return build_pl_line_detail(
                session, line_code, yr, mo, entity,
                use_llm=False, line_mom_keur=signed_mom_eur / 1000.0,
            )
        except Exception:
            return None

    def _anchor_boost(lc: str) -> float:
        if lc == "NET_SALES":
            return 30.0
        if lc == "COM":
            return 18.0
        return 0.0

    drivers = core.analyze_statement(
        rows,
        base_cm=base_cm,
        cap=cap,
        anchor_boost_fn=_anchor_boost,
        gl_detail_fn=_gl_detail,
    )

    group_label = entity or "Group"
    ctx = core.ProseContext(
        statement_kind="pl",
        period_label=cm_label,
        prior_label=pm_label,
        base_label="revenue",
        base_cm=base_cm,
        balance_style=False,
        tone_mode="favorable",
        movement_noun="the move",
    )
    bullets = core.build_bullets(drivers, ctx)

    # Journal Agent (Phase 6) — merge GL findings into the PL bullets, flag-gated.
    # Default OFF makes this a literal no-op: detect_gl_findings is NOT called and
    # the bullet list is byte-identical to the legacy output (golden guard).
    if settings.journal_agent_narrative:
        from app.services import anomaly
        period = {
            "grain": period_grain, "year": year, "month": month,
            "iso_year": iso_year, "iso_week": iso_week,
        }
        bullets = core.merge_finding_bullets(
            bullets,
            anomaly.gl_findings_as_bullets(session, period, entity, "pl"),
            cap,
        )

    # Net-profit anchor for headline + intro.
    np_row = _find_row(rows, "NET_PROFIT")
    np_am = (np_row or {}).get("amounts", {}) or {}
    np_cm = float(np_am.get("cm") or 0)
    np_ytd = float(np_am.get("ytd") or 0)
    np_pm = float(np_am.get("pm") or 0)
    np_py = float(np_am.get("py_cm") or 0)
    np_mom = np_cm - np_pm
    np_yoy = np_cm - np_py
    has_plan = "plan_cm" in np_am
    cm_vs_plan = float(np_am.get("plan_vs_actual") or 0)
    plan_cm = float(np_am.get("plan_cm") or 0)
    qualifier = _plan_qualifier(cm_vs_plan, plan_cm) if has_plan else ""

    ep = resolve_entity_prefix(session, entity)
    plan_map = _load_plan_map(session, yr, mo, entity_sql_fragment(ep))
    np_plan = plan_map.get("NET_PROFIT") or {}
    ytd_plan = float(np_plan.get("ytd_plan") or 0)
    coverage_pct: Optional[float] = None
    if abs(ytd_plan) > 1e-6:
        coverage_pct = round(np_ytd / abs(ytd_plan) * 100, 1)
    elif has_plan and abs(plan_cm) > 1e-6:
        coverage_pct = round(np_cm / abs(plan_cm) * 100, 1)

    # Primary drivers for intro (plan-anchored when forecast exists, else MoM).
    primary_drivers: list[dict[str, Any]] = []
    for f in sorted(drivers, key=lambda x: x["score"], reverse=True)[:2]:
        lc = f.get("line_code") or ""
        row = _find_row(rows, lc) if lc else None
        row_am = (row or {}).get("amounts") or {}
        if has_plan and "plan_vs_actual" in row_am:
            pva = float(row_am.get("plan_vs_actual") or 0)
            direction = "increase" if pva > 0 else ("decrease" if pva < 0 else "stable")
            primary_drivers.append({
                "label": f["label"],
                "delta": round(pva, 2),
                "direction": direction,
                "to_plan": _to_plan_phrase(pva),
            })
        else:
            dm = f["display_mom"]
            primary_drivers.append({
                "label": f["label"],
                "delta": round(dm, 2),
                "direction": _direction(dm),
            })

    # Headline (legacy depth: direction + plan/PY context + lead driver).
    if abs(np_mom) < 0.5 and abs(np_yoy) < 0.5:
        headline = f"{group_label} P&L results for {cm_label}"
    else:
        np_dir = "rose" if np_mom > 0 else ("fell" if np_mom < 0 else "held")
        lead = f", led by {primary_drivers[0]['label']}" if primary_drivers else ""
        headline = f"{group_label} net profit {np_dir} in {cm_label}{lead}"[:120]

    # Intro — legacy overview depth (YTD anchor, coverage, plan variance, driver preview).
    intro = _assemble_pl_intro(
        labels=labels,
        cm_label=cm_label,
        group_label=group_label,
        np_ytd=np_ytd,
        np_cm=np_cm,
        np_yoy=np_yoy,
        has_plan=has_plan,
        cm_vs_plan=cm_vs_plan,
        plan_cm=plan_cm,
        qualifier=qualifier,
        coverage_pct=coverage_pct,
        primary_drivers=primary_drivers,
        period_grain=period_grain,
    )

    llm_used = False
    if use_llm and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            headline, intro, bullets, llm_used = _llm_enhance(
                headline, intro, bullets, cm_label, group_label,
            )
        except Exception:
            pass  # graceful degradation — keep deterministic output

    return {
        "headline": headline,
        "intro": intro,
        "intro_facts": {
            "period_label": cm_label,
            "group_label": group_label,
            "net_profit_ytd": round(np_ytd, 2),
            "coverage_pct": coverage_pct,
            "cm_month_label": cm_label,
            "cm_vs_plan": round(cm_vs_plan, 2) if has_plan else 0.0,
            "cm_vs_plan_qualifier": qualifier,
            "primary_drivers": primary_drivers,
            "entity_split": None,
        },
        "bullets": bullets,
        "entity_split": None,
        "meta": {
            "algorithm_version": _ALGO_VERSION,
            "llm_used": llm_used,
            "max_bullets": cap,
            "cache_hit": False,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "entity_scope": entity or "",
        },
    }


def _llm_enhance(
    headline: str, intro: str, bullets: list[dict], period_lbl: str, group_label: str,
) -> tuple[str, str, list[dict], bool]:
    """Attempt LLM enhancement. Raises on failure so caller can fall back."""
    import anthropic  # type: ignore[import-untyped]

    client = anthropic.Anthropic()
    bullet_texts = "\n".join(f"- {b['text']}" for b in bullets)
    prompt = (
        f"Rewrite this executive P&L summary in concise, professional English "
        f"(max 2 sentences per item). Period: {period_lbl}, Company: {group_label}.\n\n"
        f"Headline: {headline}\nIntro: {intro}\nBullets:\n{bullet_texts}\n\n"
        f"Return JSON: {{\"headline\": str, \"intro\": str, \"bullets\": [str, ...]}}"
    )
    msg = client.messages.create(
        model="claude-3-haiku-20240307",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    import json
    resp = json.loads(msg.content[0].text)
    new_bullets = [dict(b, text=t) for b, t in zip(bullets, resp.get("bullets", []))]
    return resp["headline"], resp["intro"], new_bullets or bullets, True
