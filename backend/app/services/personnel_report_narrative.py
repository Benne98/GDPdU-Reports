"""Key-driver narratives for the payroll accounting report view."""
from __future__ import annotations

import json
import os
from datetime import date
from typing import Any, Optional

from app.services.fin_compat_narrative_core import cap_first, fmt_keur, fmt_keur_signed
from app.services.personnel_calc import aggregate_metric, row_payroll_eur

MIN_BULLETS = 2
MAX_BULLETS = 5
FTE_FLOOR = 0.5
PAYROLL_FLOOR_KEUR = 5.0
PAYROLL_PCT_FLOOR = 0.05
_SALARY_PCT_THRESHOLD = 0.05
_SALARY_ABS_THRESHOLD_EUR = 5_000.0
_CLAUDE_MODEL = "claude-3-5-sonnet-20241022"


def _col_label(d: date) -> str:
    return f"FY{str(d.year)[-2:]}A"


def _keur_to_eur(keur: float) -> float:
    return float(keur or 0) * 1000.0


def _pct_clause(delta_keur: float, base_keur: float) -> str:
    if abs(base_keur) < 0.01:
        return ""
    pct = delta_keur / abs(base_keur)
    if abs(pct) < 0.05:
        return ""
    sign = "+" if pct >= 0 else "-"
    return f", {sign}{abs(pct) * 100:.0f}% year-on-year"


def _bereich_amounts(
    rows_by_date: dict[date, list[dict[str, Any]]],
    col_dates: list[date],
    bereich: str,
    metric_id: str,
) -> dict[str, float]:
    out: dict[str, float] = {}
    for d in col_dates:
        sub = [
            r for r in rows_by_date.get(d, [])
            if (str(r.get("bereich") or "").strip() or "Unassigned") == bereich
        ]
        out[d.isoformat()] = aggregate_metric(sub, metric_id)
    return out


def _delta_pair(amounts: dict[str, float], anchor: date, prior: date) -> tuple[float, float, float]:
    anchor_v = float(amounts.get(anchor.isoformat()) or 0)
    prior_v = float(amounts.get(prior.isoformat()) or 0)
    return anchor_v, prior_v, anchor_v - prior_v


def _is_material_payroll_delta(delta_keur: float, base_keur: float) -> bool:
    floor = max(PAYROLL_FLOOR_KEUR, abs(base_keur) * PAYROLL_PCT_FLOOR)
    return abs(delta_keur) >= floor


def _is_material_fte_delta(delta: float, base: float) -> bool:
    floor = max(FTE_FLOOR, abs(base) * PAYROLL_PCT_FLOOR)
    return abs(delta) >= floor


def _movement_stats(
    anchor_rows: list[dict[str, Any]],
    prior_rows: list[dict[str, Any]],
) -> tuple[int, int, list[dict[str, Any]]]:
    cur = {str(r["personalnummer"]): r for r in anchor_rows if r.get("personalnummer")}
    prev = {str(r["personalnummer"]): r for r in prior_rows if r.get("personalnummer")}
    hires = sum(1 for pno in cur if pno not in prev)
    terms = sum(1 for pno in prev if pno not in cur)
    raises: list[dict[str, Any]] = []
    for pno, row in cur.items():
        if pno not in prev:
            continue
        old = prev[pno]
        old_pay = row_payroll_eur(old)
        new_pay = row_payroll_eur(row)
        if old_pay <= 0:
            continue
        delta = new_pay - old_pay
        if delta >= _SALARY_ABS_THRESHOLD_EUR or delta / old_pay >= _SALARY_PCT_THRESHOLD:
            raises.append({**row, "delta_eur": delta, "delta_pct": delta / old_pay * 100})
    raises.sort(key=lambda r: r.get("delta_eur", 0), reverse=True)
    return hires, terms, raises


def _count_hire_profiles(
    anchor_rows: list[dict[str, Any]],
    prior_rows: list[dict[str, Any]],
) -> dict[str, int]:
    prev = {str(r["personalnummer"]): r for r in prior_rows if r.get("personalnummer")}
    full_time = part_time = 0
    for r in anchor_rows:
        pno = str(r.get("personalnummer") or "")
        if not pno or pno in prev:
            continue
        besch = float(r.get("beschaeftigungsgrad") or 100)
        if besch >= 100:
            full_time += 1
        else:
            part_time += 1
    return {"full_time": full_time, "part_time": part_time, "total": full_time + part_time}


def _pct_phrase(delta_keur: float, base_keur: float) -> str:
    """Natural-language YoY % clause (empty when immaterial)."""
    if abs(base_keur) < 0.01:
        return ""
    pct = delta_keur / abs(base_keur)
    if abs(pct) < 0.05:
        return ""
    return f" ({abs(pct) * 100:.0f}% year-on-year)"


def _payroll_change_driver(
    fte_anchor: float,
    fte_prior: float,
    fte_delta: float,
    payroll_delta: float,
    pay_anchor_eur: float,
    pay_prior_eur: float,
) -> str:
    """Second sentence: interpret headcount vs. average-cost drivers."""
    if abs(payroll_delta) < PAYROLL_FLOOR_KEUR:
        if _is_material_fte_delta(fte_delta, fte_prior):
            if fte_delta > 0:
                return (
                    f"Average headcount rose by {abs(fte_delta):.0f} FTE to "
                    f"{fte_anchor:.0f}, while payroll costs stayed broadly flat."
                )
            return (
                f"Headcount fell by {abs(fte_delta):.0f} FTE to {fte_anchor:.0f} "
                f"without a matching payroll reduction — check timing, mix, or one-offs."
            )
        return (
            f"Average FTEs held at {fte_anchor:.0f}, with no material payroll "
            f"movement versus the prior snapshot."
        )

    cost_up = payroll_delta <= 0
    fte_material = _is_material_fte_delta(fte_delta, fte_prior)

    if fte_material and fte_delta > 0 and cost_up:
        extra = ""
        if fte_prior > 0.5 and fte_anchor > 0:
            avg_prior = pay_prior_eur / fte_prior
            avg_new = pay_anchor_eur / fte_anchor
            if abs(avg_new - avg_prior) / max(avg_prior, 1) > 0.05:
                extra = (
                    f" Average cost per FTE also moved from {fmt_keur(avg_prior)} "
                    f"to {fmt_keur(avg_new)}."
                )
        return (
            f"The step-up is partly headcount-driven: average FTEs increased from "
            f"{fte_prior:.0f} to {fte_anchor:.0f}.{extra}"
        ).strip()

    if fte_material and fte_delta < 0 and not cost_up:
        return (
            f"Lower payroll costs coincide with a headcount reduction of "
            f"{abs(fte_delta):.0f} FTE ({fte_prior:.0f} → {fte_anchor:.0f})."
        )

    if not fte_material and cost_up:
        if fte_prior > 0.5:
            return (
                "With FTEs broadly unchanged, the increase points to higher "
                "compensation per head — salary bands, bonuses, or employer contributions."
            )
        return (
            "The cost increase is not explained by headcount alone; "
            "review individual compensation components."
        )

    if not fte_material and not cost_up:
        return (
            "Despite stable headcount, payroll accounting declined — "
            "typically lower average cost per FTE or lighter variable pay."
        )

    if fte_material and fte_delta > 0 and not cost_up:
        return (
            f"Headcount rose by {abs(fte_delta):.0f} FTE, yet payroll fell — "
            f"often leavers on higher packages replaced by lower-cost hires."
        )

    if fte_material and fte_delta < 0 and cost_up:
        return (
            f"Payroll rose even as FTEs fell ({fte_prior:.0f} → {fte_anchor:.0f}), "
            f"which can reflect salary uplifts, partial-year effects, or one-off items."
        )

    return ""


def _bereich_bullet_text(
    bereich: str,
    fte_anchor: float,
    fte_prior: float,
    payroll_anchor: float,
    payroll_prior: float,
    payroll_delta: float,
    anchor_label: str,
    prior_label: str,
) -> str:
    name = cap_first(bereich)
    pay_anchor_eur = abs(_keur_to_eur(payroll_anchor))
    pay_prior_eur = abs(_keur_to_eur(payroll_prior))
    pay_delta_eur = abs(_keur_to_eur(payroll_delta))
    fte_delta = fte_anchor - fte_prior
    pct = _pct_phrase(payroll_delta, payroll_prior)

    if abs(payroll_delta) < PAYROLL_FLOOR_KEUR:
        opening = (
            f"In {name}, payroll accounting stood at {fmt_keur(pay_anchor_eur)} "
            f"at {anchor_label}, broadly in line with {prior_label} "
            f"({fmt_keur(pay_prior_eur)})."
        )
    elif payroll_delta <= 0:
        opening = (
            f"In {name}, payroll accounting rose to {fmt_keur(pay_anchor_eur)} "
            f"at {anchor_label}, up {fmt_keur(pay_delta_eur)} from {prior_label} "
            f"({fmt_keur(pay_prior_eur)}){pct}."
        )
    else:
        opening = (
            f"In {name}, payroll accounting eased to {fmt_keur(pay_anchor_eur)} "
            f"at {anchor_label}, down {fmt_keur(pay_delta_eur)} from {prior_label} "
            f"({fmt_keur(pay_prior_eur)}){pct}."
        )

    driver = _payroll_change_driver(
        fte_anchor, fte_prior, fte_delta, payroll_delta, pay_anchor_eur, pay_prior_eur,
    )
    return f"{opening} {driver}".strip() if driver else opening


def _assemble_intro(
    fte_anchor: float,
    fte_prior: float,
    payroll_anchor: float,
    payroll_prior: float,
    anchor_label: str,
    prior_label: Optional[str],
    lead_bereich: Optional[str],
) -> str:
    pay_anchor_eur = abs(_keur_to_eur(payroll_anchor))
    pay_prior_eur = abs(_keur_to_eur(payroll_prior))
    if prior_label is None:
        return (
            f"At {anchor_label}, the group carried {fte_anchor:.0f} average FTEs "
            f"and {fmt_keur(pay_anchor_eur)} in payroll accounting."
        )
    pay_delta = payroll_anchor - payroll_prior
    pay_delta_eur = abs(_keur_to_eur(pay_delta))
    fte_delta = fte_anchor - fte_prior
    pct = _pct_phrase(pay_delta, payroll_prior)
    lead = f", led by {lead_bereich}" if lead_bereich else ""

    if abs(pay_delta) < PAYROLL_FLOOR_KEUR:
        opening = (
            f"Group payroll accounting was {fmt_keur(pay_anchor_eur)} at {anchor_label}, "
            f" broadly unchanged from {prior_label} ({fmt_keur(pay_prior_eur)})."
        )
    elif pay_delta <= 0:
        opening = (
            f"Group payroll accounting reached {fmt_keur(pay_anchor_eur)} at {anchor_label}, "
            f"up {fmt_keur(pay_delta_eur)} versus {prior_label}{pct}{lead}."
        )
    else:
        opening = (
            f"Group payroll accounting totalled {fmt_keur(pay_anchor_eur)} at {anchor_label}, "
            f"down {fmt_keur(pay_delta_eur)} versus {prior_label}{pct}{lead}."
        )

    driver = _payroll_change_driver(
        fte_anchor, fte_prior, fte_delta, pay_delta, pay_anchor_eur, pay_prior_eur,
    )
    return f"{opening} {driver}".strip() if driver else opening


def _movement_bullet_text(
    hires: int,
    terms: int,
    profiles: dict[str, int],
    raises: list[dict[str, Any]],
) -> str:
    parts: list[str] = []
    if hires:
        ft = profiles.get("full_time", 0)
        pt = profiles.get("part_time", 0)
        hire_bits = [f"{hires} new hire{'s' if hires != 1 else ''}"]
        if ft or pt:
            detail = []
            if ft:
                detail.append(f"{ft} full-time")
            if pt:
                detail.append(f"{pt} part-time")
            hire_bits.append(f"({', '.join(detail)})")
        parts.append(" ".join(hire_bits))
    if terms:
        parts.append(f"{terms} departure{'s' if terms != 1 else ''}")
    if raises:
        top = raises[0]
        bereich = top.get("bereich") or "—"
        parts.append(
            f"{len(raises)} material salary increase{'s' if len(raises) != 1 else ''} "
            f"(largest +{fmt_keur(top.get('delta_eur', 0))} in {bereich})"
        )
    if not parts:
        return "Headcount and compensation were broadly stable versus the prior snapshot."
    return cap_first("; ".join(parts)) + "."


def _llm_polish_narrative(narrative: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    import anthropic  # type: ignore[import-untyped]

    client = anthropic.Anthropic()
    bullet_texts = "\n".join(f"{b['index']}. {b['text']}" for b in narrative.get("bullets") or [])
    prompt = (
        "You are editing payroll / personnel commentary for a financial due-diligence report.\n"
        "Rewrite the intro and each numbered bullet as natural, flowing English prose.\n"
        "Rules:\n"
        "- Keep every number, percentage, department name, and FTE count exactly as given.\n"
        "- One paragraph per bullet (2-3 sentences), no list fragments.\n"
        "- Intro: one short paragraph.\n"
        "- Tone: concise, professional.\n\n"
        f"Intro:\n{narrative.get('intro', '')}\n\n"
        f"Bullets:\n{bullet_texts}\n\n"
        "Return JSON only: "
        '{"intro": str, "bullets": [{"index": int, "text": str}, ...]}'
    )
    msg = client.messages.create(
        model=_CLAUDE_MODEL,
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    resp = json.loads(raw)
    polished = dict(narrative)
    polished["intro"] = str(resp.get("intro") or narrative.get("intro") or "").strip()
    by_index = {int(b.get("index")): str(b.get("text") or "").strip() for b in resp.get("bullets") or []}
    new_bullets = []
    for bullet in narrative.get("bullets") or []:
        idx = int(bullet.get("index") or 0)
        text = by_index.get(idx) or bullet.get("text") or ""
        new_bullets.append({**bullet, "text": text})
    polished["bullets"] = new_bullets
    meta = dict(polished.get("meta") or {})
    meta["llm_used"] = True
    meta["llm_model"] = _CLAUDE_MODEL
    polished["meta"] = meta
    return polished, True


def build_payroll_narrative(
    *,
    anchor_date: date,
    prior_date: Optional[date],
    col_dates: list[date],
    rows_by_date: dict[date, list[dict[str, Any]]],
    bereich_list: list[str],
    movements: Optional[dict[str, Any]] = None,
    max_bullets: int = MAX_BULLETS,
    use_llm: bool = True,
) -> dict[str, Any]:
    prior = prior_date or (col_dates[-2] if len(col_dates) >= 2 else None)
    anchor_label = _col_label(anchor_date)
    prior_label = _col_label(prior) if prior else None

    anchor_rows = rows_by_date.get(anchor_date, [])
    prior_rows = rows_by_date.get(prior, []) if prior else []

    fte_anchor = aggregate_metric(anchor_rows, "fte")
    fte_prior = aggregate_metric(prior_rows, "fte") if prior_rows else 0.0
    payroll_anchor = aggregate_metric(anchor_rows, "payroll")
    payroll_prior = aggregate_metric(prior_rows, "payroll") if prior_rows else 0.0

    scored: list[tuple[str, float, float, float, float]] = []
    for bereich in bereich_list:
        amounts = _bereich_amounts(rows_by_date, col_dates, bereich, "payroll")
        if prior is None:
            continue
        _a, _p, delta = _delta_pair(amounts, anchor_date, prior)
        fte_amts = _bereich_amounts(rows_by_date, col_dates, bereich, "fte")
        fte_a, fte_p, _ = _delta_pair(fte_amts, anchor_date, prior)
        if not _is_material_payroll_delta(delta, _p):
            continue
        score = abs(delta) * 100.0 + abs(fte_a - fte_p) * 10.0
        scored.append((bereich, _a, _p, delta, score))

    scored.sort(key=lambda x: x[4], reverse=True)
    lead_bereich = scored[0][0] if scored else None

    cap = max(MIN_BULLETS, min(int(max_bullets), MAX_BULLETS))
    bullets: list[dict[str, Any]] = []
    index = 0

    for bereich, pay_a, pay_p, pay_delta, _ in scored[:cap]:
        fte_amts = _bereich_amounts(rows_by_date, col_dates, bereich, "fte")
        fte_a, fte_p, _ = _delta_pair(fte_amts, anchor_date, prior) if prior else (0.0, 0.0, 0.0)
        index += 1
        text = _bereich_bullet_text(
            bereich, fte_a, fte_p, pay_a, pay_p, pay_delta,
            anchor_label, prior_label or "prior year",
        )
        bullets.append({
            "index": index,
            "row_id": f"payroll-{bereich}",
            "label": bereich,
            "text": text,
            "tone": "negative" if pay_delta <= -PAYROLL_FLOOR_KEUR else (
                "positive" if pay_delta >= PAYROLL_FLOOR_KEUR else "neutral"
            ),
        })

    if len(bullets) < MIN_BULLETS and prior:
        index += 1
        bullets.append({
            "index": index,
            "row_id": "payroll-total",
            "label": "Payroll accounting",
            "text": _assemble_intro(
                fte_anchor, fte_prior, payroll_anchor, payroll_prior,
                anchor_label, prior_label, lead_bereich,
            ),
            "tone": "neutral",
        })

    narrative: dict[str, Any] = {
        "headline": "Key drivers",
        "intro": "",
        "bullets": bullets[:cap],
        "meta": {
            "algorithm_version": "payroll_report_v2",
            "llm_used": False,
            "max_bullets": cap,
        },
    }

    if use_llm and bullets and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            narrative, _ = _llm_polish_narrative(narrative)
        except Exception:
            pass

    return narrative
