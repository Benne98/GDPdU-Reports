"""Key-driver narratives for the fixed-asset rollforward report view."""
from __future__ import annotations

import json
import os
from datetime import date
from typing import Any, Optional

from app.services.fin_compat_narrative_core import cap_first, fmt_keur, fmt_keur_signed
from app.services.fixed_asset_rollforward import _col_key_move, _col_key_year_end, dec_label

MIN_BULLETS = 2
MAX_BULLETS = 5
YOY_FLOOR_KEUR = 30.0
YOY_PCT_FLOOR = 0.05
ASSET_MOVE_FLOOR_KEUR = 5.0
ACCOUNT_CONCENTRATION_PCT = 0.50
_CLAUDE_MODEL = "claude-3-5-sonnet-20241022"


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


def _position_closing_yoy(
    amounts: dict[str, Any],
    anchor_year: int,
    prior_year: Optional[int],
) -> tuple[float, float, float]:
    anchor_key = _col_key_year_end(anchor_year)
    closing = float(amounts.get(anchor_key) or 0)
    if prior_year is None:
        return closing, closing, 0.0
    prior = float(amounts.get(_col_key_year_end(prior_year)) or 0)
    return closing, prior, closing - prior


def _asset_closing_yoy(
    asset: dict[str, Any],
    anchor_year: int,
    prior_year: Optional[int],
) -> float:
    _, _, yoy = _position_closing_yoy(asset.get("amounts") or {}, anchor_year, prior_year)
    return yoy


def _asset_anchor_movements(asset: dict[str, Any], anchor_year: int) -> tuple[float, float, float]:
    am = asset.get("amounts") or {}
    add_k = float(am.get(_col_key_move(anchor_year, "additions")) or 0)
    disp_k = float(am.get(_col_key_move(anchor_year, "disposals")) or 0)
    da_k = float(am.get(_col_key_move(anchor_year, "depreciation")) or 0)
    return add_k, disp_k, da_k


def _is_material_yoy(closing_keur: float, yoy_keur: float) -> bool:
    floor = max(YOY_FLOOR_KEUR, abs(closing_keur) * YOY_PCT_FLOOR)
    return abs(yoy_keur) >= floor


def _score_position(yoy_keur: float, closing_keur: float, total_closing_keur: float) -> float:
    base = abs(total_closing_keur) or 1.0
    return abs(yoy_keur) * 100.0 + (abs(yoy_keur) / base) * 50.0


def _asset_label(asset: dict[str, Any]) -> str:
    return str(asset.get("label") or asset.get("asset_id") or "asset")


def _asset_driver_paragraph(
    assets: list[dict[str, Any]],
    yoy_keur: float,
    anchor_year: int,
    prior_year: Optional[int],
) -> str:
    if not assets or abs(yoy_keur) < 0.01:
        return ""

    movers: list[tuple[dict[str, Any], float]] = []
    for asset in assets:
        delta = _asset_closing_yoy(asset, anchor_year, prior_year)
        if abs(delta) >= ASSET_MOVE_FLOOR_KEUR:
            movers.append((asset, delta))
    movers.sort(key=lambda x: abs(x[1]), reverse=True)

    sentences: list[str] = []
    if movers:
        lead_asset, lead_delta = movers[0]
        lead_share = min(1.0, abs(lead_delta) / abs(yoy_keur))
        lead_phrase = (
            f"Most of the movement sits in {_asset_label(lead_asset)}, "
            f"which moved {fmt_keur_signed(_keur_to_eur(lead_delta))} "
            f"({lead_share * 100:.0f}% of the net balance change)"
        )
        if len(movers) > 1:
            sec_asset, sec_delta = movers[1]
            sec_share = min(1.0, abs(sec_delta) / abs(yoy_keur))
            lead_phrase += (
                f", with {_asset_label(sec_asset)} a further "
                f"{fmt_keur_signed(_keur_to_eur(sec_delta))} ({sec_share * 100:.0f}%)"
            )
        sentences.append(lead_phrase + ".")

    add_key = _col_key_move(anchor_year, "additions")
    disp_key = _col_key_move(anchor_year, "disposals")
    add_bits: list[str] = []
    disp_bits: list[str] = []
    for asset in assets:
        add_k = float((asset.get("amounts") or {}).get(add_key) or 0)
        disp_k = float((asset.get("amounts") or {}).get(disp_key) or 0)
        if add_k >= ASSET_MOVE_FLOOR_KEUR:
            add_bits.append(f"{_asset_label(asset)} ({fmt_keur_signed(_keur_to_eur(add_k))})")
        if disp_k >= ASSET_MOVE_FLOOR_KEUR:
            disp_bits.append(f"{_asset_label(asset)} ({fmt_keur_signed(-_keur_to_eur(disp_k))})")

    if add_bits:
        sentences.append(
            "New spend in the year was driven by "
            + ", ".join(add_bits[:2])
            + ("." if len(add_bits) <= 2 else ", among others.")
        )
    if disp_bits:
        sentences.append(
            "Disposals were led by "
            + ", ".join(disp_bits[:2])
            + ("." if len(disp_bits) <= 2 else ", among others.")
        )

    da_total = sum(_asset_anchor_movements(a, anchor_year)[2] for a in assets)
    if da_total >= ASSET_MOVE_FLOOR_KEUR:
        fy = str(anchor_year)[-2:]
        sentences.append(
            f"Depreciation and amortisation in FY{fy} totalled {fmt_keur(_keur_to_eur(da_total))}."
        )

    return " ".join(sentences)


def _position_bullet_text(
    label: str,
    closing_keur: float,
    yoy_keur: float,
    anchor_label: str,
    prior_label: str,
    assets: list[dict[str, Any]],
    anchor_year: int,
    prior_year: Optional[int],
) -> str:
    if abs(yoy_keur) < 0.5:
        opening = (
            f"{cap_first(label)} closed at {fmt_keur(_keur_to_eur(closing_keur))} "
            f"as of {anchor_label}, broadly unchanged from the prior year-end."
        )
    else:
        direction = "higher" if yoy_keur >= 0 else "lower"
        opening = (
            f"{cap_first(label)} closed at {fmt_keur(_keur_to_eur(closing_keur))} "
            f"as of {anchor_label}, {fmt_keur(abs(_keur_to_eur(yoy_keur)))} {direction} "
            f"than the {prior_label} balance{_pct_clause(yoy_keur, closing_keur - yoy_keur)}."
        )
    detail = _asset_driver_paragraph(assets, yoy_keur, anchor_year, prior_year)
    return f"{opening} {detail}".strip()


def _assemble_intro(
    total_closing_keur: float,
    total_yoy_keur: float,
    anchor_label: str,
    prior_label: Optional[str],
    primary_labels: list[str],
) -> str:
    if prior_label is None:
        return (
            f"Fixed assets totalled {fmt_keur(_keur_to_eur(total_closing_keur))} "
            f"as of {anchor_label} across the selected balance-sheet lines."
        )
    direction = "up" if total_yoy_keur >= 0 else "down"
    lead = ""
    if primary_labels:
        lead = f", mainly reflecting {primary_labels[0]}"
        if len(primary_labels) > 1:
            lead += f" and {primary_labels[1]}"
    return (
        f"Fixed assets stood at {fmt_keur(_keur_to_eur(total_closing_keur))} "
        f"as of {anchor_label}, {direction} {fmt_keur(abs(_keur_to_eur(total_yoy_keur)))} "
        f"from the {prior_label} closing balance"
        f"{_pct_clause(total_yoy_keur, total_closing_keur - total_yoy_keur)}{lead}."
    )


def _llm_polish_narrative(narrative: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Rewrite deterministic FA copy into flowing prose via Claude (graceful fallback)."""
    import anthropic  # type: ignore[import-untyped]

    client = anthropic.Anthropic()
    bullet_texts = "\n".join(f"{b['index']}. {b['text']}" for b in narrative.get("bullets") or [])
    prompt = (
        "You are editing fixed-asset rollforward commentary for a financial due-diligence report.\n"
        "Rewrite the intro and each numbered bullet as natural, flowing English prose.\n"
        "Rules:\n"
        "- Keep every number, percentage, asset ID, and label exactly as given.\n"
        "- One paragraph per bullet (2-4 sentences), no list fragments or semicolon chains.\n"
        "- Intro: one short paragraph; do not end with 'Key drivers consist of'.\n"
        "- Tone: concise, professional, readable.\n\n"
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


def build_rollforward_narrative(
    position_blocks: list[dict[str, Any]],
    *,
    anchor_date: date,
    years: list[int],
    total_amounts: dict[str, Any],
    max_bullets: int = MAX_BULLETS,
    use_llm: bool = True,
) -> dict[str, Any]:
    anchor_year = anchor_date.year
    prior_year = max((y for y in years if y < anchor_year), default=None)
    anchor_label = dec_label(anchor_date)
    prior_label = dec_label(date(prior_year, 12, 31)) if prior_year else None

    total_closing, _, total_yoy = _position_closing_yoy(total_amounts, anchor_year, prior_year)

    scored: list[tuple[dict[str, Any], float, float, float]] = []
    for block in position_blocks:
        closing, _, yoy = _position_closing_yoy(block.get("amounts") or {}, anchor_year, prior_year)
        if not _is_material_yoy(closing, yoy):
            continue
        score = _score_position(yoy, closing, total_closing)
        scored.append((block, closing, yoy, score))

    cap = max(MIN_BULLETS, min(int(max_bullets), MAX_BULLETS))
    selected_ids = {
        b["id"]
        for b, _, _, _ in sorted(scored, key=lambda x: x[3], reverse=True)[:cap]
    }

    bullets: list[dict[str, Any]] = []
    index = 0
    for block in position_blocks:
        if block["id"] not in selected_ids:
            continue
        closing, _, yoy = _position_closing_yoy(block.get("amounts") or {}, anchor_year, prior_year)
        index += 1
        text = _position_bullet_text(
            str(block.get("bilanzposition") or block.get("label") or ""),
            closing,
            yoy,
            anchor_label,
            prior_label or "prior year-end",
            list(block.get("assets") or []),
            anchor_year,
            prior_year,
        )
        bullets.append({
            "index": index,
            "position_id": block["id"],
            "label": block.get("bilanzposition") or block.get("label") or "",
            "text": text,
            "tone": "positive" if yoy > 0.01 else ("negative" if yoy < -0.01 else "neutral"),
        })

    primary_labels = [
        str(block.get("bilanzposition") or block.get("label") or "")
        for block, _, _yoy, _ in sorted(scored, key=lambda x: abs(x[2]), reverse=True)[:2]
    ]
    intro = _assemble_intro(total_closing, total_yoy, anchor_label, prior_label, primary_labels)

    narrative: dict[str, Any] = {
        "headline": "Key drivers",
        "intro": intro,
        "bullets": bullets,
        "meta": {
            "algorithm_version": "fa_rollforward_v2",
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
