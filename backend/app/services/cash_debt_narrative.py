"""Key-driver narratives for the net debt table (Cash & debt sub-tab)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.fin_compat_narrative_core import cap_first, fmt_keur, fmt_keur_signed
from app.services.fin_compat_sql import last_day

MIN_BULLETS = 2
MAX_BULLETS = 5
LIABILITY_FLOOR_KEUR = 50.0
SEASONALITY_SHARE = 0.45

_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _keur_to_eur(keur: float) -> float:
    return float(keur or 0) * 1000.0


def _anchor_amount(row: dict[str, Any], anchor_key: str) -> float:
    amounts = row.get("amounts") or {}
    if anchor_key in amounts:
        return float(amounts[anchor_key] or 0)
    return float(row.get("amount_keur") or 0)


def _collect_liability_lines(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("row_kind") != "section_header":
            continue
        if row.get("id") not in ("bank", "shareholder"):
            continue
        for child in row.get("children") or []:
            if child.get("row_kind") == "account":
                out.append(child)
    return out


def _account_groups_from_row(row: dict[str, Any]) -> list[str]:
    merged = row.get("merged_account_groups")
    if merged:
        return [str(g) for g in merged if g]
    ang = row.get("account_number_group")
    return [str(ang)] if ang else []


def _entity_prefixes_for_accounts(children: list[dict[str, Any]]) -> set[str]:
    prefixes: set[str] = set()
    for acct in children:
        ang = str(acct.get("account_number_group") or "")
        if len(ang) >= 2:
            prefixes.add(ang[:2])
    return prefixes


def _resolve_entity_names(session: Session, prefixes: set[str]) -> dict[str, str]:
    if not prefixes:
        return {}
    rows = session.execute(
        text(
            "SELECT entity_prefix, entity_name FROM dim_legal_entity "
            "WHERE entity_prefix = ANY(:prefixes)"
        ),
        {"prefixes": sorted(prefixes)},
    ).fetchall()
    return {str(r[0]): str(r[1] or r[0]) for r in rows}


def _fetch_monthly_booking_totals(
    session: Session,
    *,
    account_groups: list[str],
    fiscal_year: int,
    from_date: date,
    to_date: date,
    entity: Optional[str],
    allowed_entities: Optional[set[str]],
) -> dict[int, float]:
    """Absolute monthly booking totals (EUR) for seasonality detection."""
    if not account_groups:
        return {}

    from app.services.fin_compat_cash_debt import _entity_frag

    ent_frag = _entity_frag(session, entity, allowed_entities)
    sql = f"""
        SELECT
            EXTRACT(MONTH FROM e.posting_date)::int AS m,
            COALESCE(SUM(ABS(l.amount)), 0)::float8 AS vol
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        WHERE l.account_number_group = ANY(:accts)
          AND l.fiscal_year = :fy
          AND e.posting_date BETWEEN :start AND :cutoff
          {ent_frag}
        GROUP BY EXTRACT(MONTH FROM e.posting_date)
    """
    raw = session.execute(
        text(sql),
        {
            "accts": account_groups,
            "fy": fiscal_year,
            "start": from_date.isoformat(),
            "cutoff": to_date.isoformat(),
        },
    ).fetchall()
    return {int(r[0]): float(r[1] or 0) for r in raw}


def _seasonality_clause(monthly: dict[int, float]) -> str:
    if not monthly:
        return "No material booking pattern was observed in the trailing 24 months."
    total = sum(monthly.values())
    if total < 500.0:
        return "Bookings are sparse and show no clear seasonal concentration."

    ranked = sorted(monthly.items(), key=lambda x: x[1], reverse=True)
    top_month, top_vol = ranked[0]
    top_share = top_vol / total
    if top_share >= SEASONALITY_SHARE:
        return (
            f"Bookings concentrate in {_MONTH_NAMES[top_month - 1]} "
            f"({top_share * 100:.0f}% of observed volume)."
        )

    top3_vol = sum(v for _, v in ranked[:3])
    top3_share = top3_vol / total
    if top3_share >= 0.70:
        months = ", ".join(_MONTH_NAMES[m - 1] for m, _ in ranked[:3])
        return f"Bookings cluster in {months} ({top3_share * 100:.0f}% of volume)."
    return "Bookings are spread across the year without a single dominant month."


def _counterparty_clause(
    children: list[dict[str, Any]],
    entity_names: dict[str, str],
) -> str:
    partners: list[str] = []
    seen: set[str] = set()
    for acct in children:
        groups = acct.get("merged_account_groups") or [acct.get("account_number_group")]
        for ang in groups:
            if not ang:
                continue
            prefix = str(ang)[:2] if len(str(ang)) >= 2 else ""
            name = entity_names.get(prefix) or prefix or "unknown entity"
            token = f"{name} (account {ang})"
            if token in seen:
                continue
            seen.add(token)
            partners.append(token)
    if not partners:
        return ""
    if len(partners) == 1:
        return f"Contract partner per account number: {partners[0]}."
    return "Contract partners per account number: " + "; ".join(partners[:3]) + "."


def _liability_bullet_text(
    line: dict[str, Any],
    anchor_key: str,
    anchor_label: str,
    anchor_keur: float,
    children: list[dict[str, Any]],
    seasonality: str,
    counterparty: str,
) -> str:
    label = str(line.get("label") or "Liability")
    opening = (
        f"{cap_first(label)} stood at {fmt_keur(_keur_to_eur(abs(anchor_keur)))} "
        f"as of {anchor_label}"
    )
    if anchor_keur < -0.5:
        opening += " (liability balance)"
    opening += "."
    parts = [opening, seasonality]
    if counterparty:
        parts.append(counterparty)
    acct_labels = []
    for a in children:
        amt = float((a.get("amounts") or {}).get(anchor_key, a.get("amount_keur")) or 0)
        if abs(amt) >= 0.5:
            acct_labels.append(str(a.get("label") or a.get("account_number_group") or ""))
    if acct_labels:
        parts.append(f"Underlying GL accounts: {', '.join(acct_labels[:3])}.")
    return " ".join(p for p in parts if p)


def build_net_debt_narrative(
    session: Session,
    *,
    rows: list[dict[str, Any]],
    col_keys: list[str],
    col_labels: dict[str, str],
    anchor_date: str,
    year: int,
    month: int,
    entity: Optional[str] = None,
    allowed_entities: Optional[set[str]] = None,
) -> dict[str, Any]:
    anchor_key = col_keys[-1] if col_keys else anchor_date
    anchor_label = col_labels.get(anchor_key, anchor_date)

    liability_lines = _collect_liability_lines(rows)
    scored: list[tuple[dict[str, Any], float]] = []
    for line in liability_lines:
        amt = _anchor_amount(line, anchor_key)
        if abs(amt) >= LIABILITY_FLOOR_KEUR:
            scored.append((line, amt))
    scored.sort(key=lambda x: abs(x[1]), reverse=True)

    selected = scored[:MAX_BULLETS]
    if len(selected) < MIN_BULLETS:
        selected = scored

    cutoff = last_day(year, month)
    from_date = date(year - 2, 1, 1)

    bullets: list[dict[str, Any]] = []
    for idx, (line, anchor_keur) in enumerate(selected, start=1):
        acct_groups = _account_groups_from_row(line)
        prefixes = _entity_prefixes_for_accounts([line])
        entity_names = _resolve_entity_names(session, prefixes)
        monthly = _fetch_monthly_booking_totals(
            session,
            account_groups=acct_groups,
            fiscal_year=year,
            from_date=from_date,
            to_date=cutoff,
            entity=entity,
            allowed_entities=allowed_entities,
        )
        text = _liability_bullet_text(
            line,
            anchor_key,
            anchor_label,
            anchor_keur,
            [line],
            _seasonality_clause(monthly),
            _counterparty_clause([line], entity_names),
        )
        bullets.append({
            "index": idx,
            "row_id": line["id"],
            "label": line.get("label") or "",
            "text": text,
            "tone": "negative" if anchor_keur < -0.5 else "neutral",
        })

    total_row = next((r for r in rows if r.get("id") == "net_debt"), None)
    total_keur = _anchor_amount(total_row, anchor_key) if total_row else 0.0
    intro = (
        f"Net debt totalled {fmt_keur_signed(_keur_to_eur(total_keur))} "
        f"as of {anchor_label}."
    )
    if bullets:
        lead = ", ".join(b["label"] for b in bullets[:2])
        intro += f" The largest liabilities are {lead}."

    return {
        "headline": "Key drivers",
        "intro": intro,
        "bullets": bullets,
        "meta": {"algorithm_version": "cash_debt_narrative_v1"},
    }
