"""Cash & debt / net debt table for the Cash flow tab.

Net debt presentation:
  1. Cash & cash equivalents (level_3) split into cash on hand / cash at banks
  2. Bank liabilities — ND-marked and/or level_3 bank debt, grouped by bank label
  3. Shareholder loans — ND / affiliate liabilities, grouped by counterparty
  4. Net financial debt subtotal = cash + bank + shareholder (signed BS balances, kEUR)
  5. Debt-like items (optional) — remaining ND accounts not classified as bank/shareholder
  6. Net debt = net financial debt + debt-like items

Formula (kEUR, month-end cumulative BS balance):
  net_financial_debt = Σ cash + Σ bank_liabilities + Σ shareholder_loans
  net_debt = net_financial_debt + Σ debt_like   (omit section 5–6 when no debt-like rows)

Worked example (Jul-25, kEUR):
  Cash on hand 0 + Cash at banks 264 = 264
  Bank liabilities (1,793 + 563 + 1,923 + 20) = (4,299)
  Shareholder loans (100 + 256) = (357)
  Net financial debt = 264 + (−4,299) + (−357) = (4,392)
  Severances (debt-like) = (210)
  Net debt = (4,602)

Edge cases:
  * No ND mapping on an account → excluded from debt sections (cash still from level_3).
  * No debt-like rows → hide sections 5–6; show net financial debt only.
  * Zero balance accounts omitted.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.fin_compat_sql import (
    entities_sql_fragment,
    entity_sql_fragment,
    last_day,
    resolve_entity_prefix,
)

CASH_L3 = "Cash & cash equivalents"
BANK_L3 = "Liabilities due to banks"
AFFILIATE_L3 = "Liabilities due to affiliates"

KNOWN_BANKS = (
    "commerzbank", "volksbank", "volkswagen bank", "deutsche bank", "sparkasse",
    "ing", "unicredit", "hypovereinsbank", "kfw", "lbbw", "dkb", "n26",
)

_CASH_ON_HAND = re.compile(r"\b(cash on hand|kasse|kassenbestand|petty cash)\b", re.I)
_SHAREHOLDER = re.compile(r"\b(shareholder|gesellschafter|shareholders)\b", re.I)
# Asset-side positions: any level_3 denoting a receivable (e.g. intercompany
# "Receivables from affiliates", trade/other receivables). These are ASSETS and
# must never enter the bank / shareholder / debt-like debt buckets, even when the
# account carries an ND na-mapping. Matches the level_3 label only.
_RECEIVABLE_L3 = re.compile(r"receivable", re.I)
_DEBT_LIKE = re.compile(
    r"\b(severance|abfindung|provision|onerous|pension|leasing|miete)\b", re.I
)


def _to_keur(eur: float) -> float:
    return round(eur / 1000.0, 2)


def _month_label(year: int, month: int) -> str:
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{names[month - 1]}{str(year)[-2:]}A"


def _snapshot_cutoffs(anchor_year: int, anchor_month: int) -> list[date]:
    """Three prior December year-ends plus the anchor month-end."""
    cutoffs = [last_day(y, 12) for y in range(anchor_year - 3, anchor_year)]
    cutoffs.append(last_day(anchor_year, anchor_month))
    return cutoffs


def _build_col_keys(cutoffs: list[date]) -> tuple[list[str], dict[str, str]]:
    keys = [c.isoformat() for c in cutoffs]
    labels = {c.isoformat(): _month_label(c.year, c.month) for c in cutoffs}
    return keys, labels


def _bank_label(account_name: str) -> str:
    low = (account_name or "").lower()
    for bank in KNOWN_BANKS:
        if bank in low:
            return bank.title() if bank != "kfw" else "KfW"
    # First token before dash/pipe often is bank name in German COA
    head = re.split(r"[-–|/]", account_name or "", maxsplit=1)[0].strip()
    return head or account_name or "Other bank"


def _shareholder_label(account_name: str, l7: str) -> str:
    if l7 and _SHAREHOLDER.search(l7):
        return l7.replace("Shareholder loans", "").strip(" -") or account_name
    return (account_name or l7 or "Shareholder loan").strip()


def _is_cash_on_hand(account_name: str, level_4: str | None) -> bool:
    blob = f"{account_name} {level_4 or ''}"
    return bool(_CASH_ON_HAND.search(blob))


def _is_receivable_asset(level_3: str) -> bool:
    """True when level_3 denotes a receivable (asset side). Such positions are
    excluded from every debt bucket regardless of ND na-mapping."""
    return bool(_RECEIVABLE_L3.search(level_3 or ""))


def _is_shareholder(l7: str, account_name: str, level_3: str) -> bool:
    if level_3 == AFFILIATE_L3:
        return True
    blob = f"{l7} {account_name}"
    return bool(_SHAREHOLDER.search(blob))


def _is_bank_nd(l7: str, account_name: str, level_3: str) -> bool:
    if level_3 == BANK_L3:
        return True
    blob = f"{l7} {account_name}".lower()
    return any(b in blob for b in KNOWN_BANKS) or "bank" in blob


def _entity_frag(
    session: Session,
    entity: Optional[str],
    allowed_entities: Optional[set[str]],
) -> str:
    """entity_prefix AND-fragment (alias ``l``), fail-closed on empty visibility.

    Mirrors app.services.overview_summary._cash_ent_frag so the two Cash & Debt
    endpoints honour the SAME row-level tenant boundary as /overview/*:
      * ``allowed_entities is None`` → admin / unrestricted: resolve the ``entity``
        query-param exactly as before (no restriction when ``entity`` is 'all').
      * ``allowed_entities == set()`` → restricted caller with NO visible prefix →
        DENY ALL via ``AND 1 = 0`` (zero rows) — NEVER the empty "no-filter = all"
        path (which would leak every tenant).
      * ``allowed_entities`` non-empty → filter to EXACTLY those prefixes.
    """
    if allowed_entities is not None:
        if not allowed_entities:
            return "AND 1 = 0"
        return entities_sql_fragment(sorted(allowed_entities))
    ep = resolve_entity_prefix(session, entity)
    return entity_sql_fragment(ep)


def _fetch_accounts(
    session: Session,
    cutoff: date,
    entity: Optional[str],
    allowed_entities: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    """Fetch BS balances at a single cutoff (anchor classification)."""
    return _fetch_accounts_multi(session, [cutoff], entity, allowed_entities)


def _fetch_accounts_multi(
    session: Session,
    cutoffs: list[date],
    entity: Optional[str],
    allowed_entities: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    if not cutoffs:
        return []
    ent_frag = _entity_frag(session, entity, allowed_entities)
    case_exprs = ",\n            ".join(
        f"COALESCE(SUM(CASE WHEN e.posting_date <= :c{i} THEN l.amount ELSE 0 END), 0)::float8 AS bal_{i}"
        for i in range(len(cutoffs))
    )
    having_exprs = " OR ".join(
        f"ABS(COALESCE(SUM(CASE WHEN e.posting_date <= :c{i} THEN l.amount ELSE 0 END), 0)) > 0.5"
        for i in range(len(cutoffs))
    )
    params: dict[str, Any] = {
        f"c{i}": cutoffs[i].isoformat()
        for i in range(len(cutoffs))
    }
    params.update({
        "cash_l3": CASH_L3,
        "bank_l3": BANK_L3,
        "affiliate_l3": AFFILIATE_L3,
    })
    sql = f"""
        SELECT
            l.account_number_group,
            MAX(a.gl_account_id) AS gl_account_id,
            MAX(a.account_name) AS account_name,
            MAX(a.level_3) AS level_3,
            MAX(a.level_4) AS level_4,
            MAX(COALESCE(na.l6_na_mapping, '')) AS l6_na,
            MAX(COALESCE(na.l7_na_description, '')) AS l7_na,
            {case_exprs}
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        LEFT JOIN dim_gl_na na
          ON na.account_number_group = l.account_number_group
         AND na.fiscal_year = l.fiscal_year
        WHERE a.level_0 = 'BS'
          AND e.posting_date <= :c{len(cutoffs) - 1}
          {ent_frag}
          AND (
            a.level_3 = :cash_l3
            OR na.l6_na_mapping = 'ND'
            OR a.level_3 IN (:bank_l3, :affiliate_l3)
          )
        GROUP BY l.account_number_group, l.fiscal_year
        HAVING {having_exprs}
        ORDER BY MAX(a.account_name)
    """
    rows = session.execute(text(sql), params).fetchall()
    col_keys = [c.isoformat() for c in cutoffs]
    out: list[dict[str, Any]] = []
    for r in rows:
        m = dict(r._mapping)
        balances_eur: dict[str, float] = {}
        balances_keur: dict[str, float] = {}
        for i, ck in enumerate(col_keys):
            bal = float(m.get(f"bal_{i}") or 0)
            balances_eur[ck] = bal
            balances_keur[ck] = _to_keur(bal)
        anchor_key = col_keys[-1]
        out.append({
            "account_number_group": m["account_number_group"],
            "gl_account_id": m.get("gl_account_id") or m["account_number_group"],
            "account_name": m.get("account_name") or "",
            "level_3": m.get("level_3") or "",
            "level_4": m.get("level_4") or "",
            "l6_na": m.get("l6_na") or "",
            "l7_na": m.get("l7_na") or "",
            "balances_eur": balances_eur,
            "balances_keur": balances_keur,
            "balance_eur": balances_eur[anchor_key],
            "balance_keur": balances_keur[anchor_key],
        })
    return out


def _sum_amounts(accs: list[dict], col_keys: list[str]) -> dict[str, float]:
    totals = {k: 0.0 for k in col_keys}
    for acct in accs:
        for k in col_keys:
            totals[k] += float((acct.get("balances_keur") or {}).get(k, 0))
    return {k: round(v, 2) for k, v in totals.items()}


def _account_slug(label: str) -> str:
    slug = re.sub(r"\s+", " ", (label or "").strip().lower())
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return slug[:48] or "account"


def _merge_accounts_by_label(accs: list[dict], col_keys: list[str]) -> list[dict[str, Any]]:
    """Collapse duplicate GL accounts that share the same display name (e.g. per entity
    prefix or fiscal year) into one row with summed snapshot balances."""
    if not accs:
        return []
    anchor_key = col_keys[-1]
    by_label: dict[str, list[dict]] = {}
    for acct in accs:
        label = (acct.get("account_name") or "").strip() or str(acct.get("account_number_group") or "")
        by_label.setdefault(label, []).append(acct)

    merged: list[dict[str, Any]] = []
    for label, group in by_label.items():
        if len(group) == 1:
            merged.append(group[0])
            continue
        balances_keur = {
            k: round(sum(float((a.get("balances_keur") or {}).get(k, 0)) for a in group), 2)
            for k in col_keys
        }
        balances_eur = {
            k: round(sum(float((a.get("balances_eur") or {}).get(k, 0)) for a in group), 2)
            for k in col_keys
        }
        primary = max(
            group,
            key=lambda a: abs(float((a.get("balances_keur") or {}).get(anchor_key, 0))),
        )
        merged.append({
            **primary,
            "account_name": label,
            "balances_keur": balances_keur,
            "balances_eur": balances_eur,
            "balance_keur": balances_keur[anchor_key],
            "balance_eur": balances_eur[anchor_key],
            "merged_account_groups": sorted({
                str(a.get("account_number_group") or "")
                for a in group
                if a.get("account_number_group")
            }),
        })
    return merged


def _merge_buckets(buckets: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Merge bucket lists that share the same normalised counterparty/bank label."""
    out: dict[str, list[dict]] = {}
    for key, accs in buckets.items():
        norm = (key or "").strip() or "Other"
        out.setdefault(norm, []).extend(accs)
    return out


def _account_row(acct: dict[str, Any], parent_id: str, col_keys: list[str]) -> dict[str, Any]:
    label = (acct.get("account_name") or "").strip() or str(acct.get("account_number_group") or "")
    amounts = {
        k: float((acct.get("balances_keur") or {}).get(k, 0))
        for k in col_keys
    }
    anchor_key = col_keys[-1]
    row: dict[str, Any] = {
        "id": f"{parent_id}::{_account_slug(label)}",
        "label": label,
        "row_kind": "account",
        "ref": None,
        "amounts": amounts,
        "amount_keur": amounts[anchor_key],
        "account_number_group": acct["account_number_group"],
        "gl_account_id": acct["gl_account_id"],
        "clickable": True,
        "children": [],
    }
    merged_groups = acct.get("merged_account_groups")
    if merged_groups and len(merged_groups) > 1:
        row["merged_account_groups"] = merged_groups
    return row


def _section_row(
    row_id: str,
    label: str,
    ref: str | None,
    amounts: dict[str, float],
    row_kind: str,
    children: list[dict[str, Any]],
    col_keys: list[str],
) -> dict[str, Any]:
    anchor_key = col_keys[-1]
    rounded = {k: round(float(v), 2) for k, v in amounts.items()}
    return {
        "id": row_id,
        "label": label,
        "row_kind": row_kind,
        "ref": ref,
        "amounts": rounded,
        "amount_keur": rounded[anchor_key],
        "clickable": False,
        "children": children,
    }


def build_net_debt_table(
    session: Session,
    year: int,
    month: int,
    entity: Optional[str] = None,
    allowed_entities: Optional[set[str]] = None,
) -> dict[str, Any]:
    """Net-debt table.

    ``allowed_entities`` is the fail-closed entity_prefix allow-set (None = admin /
    unrestricted; empty set = restricted with no visible entity → zeroed table).
    The empty-render guarantee still holds: the "Net financial debt" subtotal and
    "Net debt" total rows are always emitted even when the (possibly restricted)
    dataset is empty — a restricted caller sees zeros, never a broken table.
    """
    cutoff = last_day(year, month)
    cutoffs = _snapshot_cutoffs(year, month)
    col_keys, col_labels = _build_col_keys(cutoffs)
    accounts = _fetch_accounts_multi(session, cutoffs, entity, allowed_entities)

    cash_on_hand: list[dict] = []
    cash_at_banks: list[dict] = []
    bank_buckets: dict[str, list[dict]] = {}
    shareholder_buckets: dict[str, list[dict]] = {}
    debt_like_buckets: dict[str, list[dict]] = {}

    for acct in accounts:
        l3 = acct["level_3"]
        l6 = acct["l6_na"]
        l7 = acct["l7_na"]
        name = acct["account_name"]

        if l3 == CASH_L3:
            if _is_cash_on_hand(name, acct["level_4"]):
                cash_on_hand.append(acct)
            else:
                cash_at_banks.append(acct)
            continue

        # Asset-side receivables (e.g. intercompany "Receivables from affiliates")
        # carry an ND na-mapping on this dataset but are ASSETS, not debt. Drop them
        # from all debt buckets so they never inflate net financial / net debt.
        if _is_receivable_asset(l3):
            continue

        if _is_shareholder(l7, name, l3) or (l6 == "ND" and _SHAREHOLDER.search(f"{l7} {name}")):
            key = _shareholder_label(name, l7)
            shareholder_buckets.setdefault(key, []).append(acct)
            continue

        if _is_bank_nd(l7, name, l3):
            key = _bank_label(name)
            bank_buckets.setdefault(key, []).append(acct)
            continue

        if l6 == "ND" or l3 in (BANK_L3, AFFILIATE_L3):
            if _DEBT_LIKE.search(f"{l7} {name}") or l6 == "ND":
                key = l7 or name
                debt_like_buckets.setdefault(key, []).append(acct)
            continue

    def _sum(accs: list[dict]) -> dict[str, float]:
        return _sum_amounts(accs, col_keys)

    def _group_children(
        buckets: dict[str, list[dict]],
        parent_prefix: str,
    ) -> tuple[list[dict[str, Any]], dict[str, float]]:
        """One clickable account row per loan/bank label — no nested duplicate lines."""
        children: list[dict[str, Any]] = []
        total = {k: 0.0 for k in col_keys}
        for label, accs in sorted(buckets.items(), key=lambda x: -abs(sum(a["balance_keur"] for a in x[1]))):
            merged = _merge_accounts_by_label(accs, col_keys)
            subtotal = _sum(merged)
            for k in col_keys:
                total[k] += subtotal[k]
            for acct in merged:
                children.append(_account_row(acct, f"{parent_prefix}::{label}", col_keys))
        return children, {k: round(v, 2) for k, v in total.items()}

    cash_children: list[dict[str, Any]] = []
    coh_total = _sum(cash_on_hand)
    cab_total = _sum(cash_at_banks)
    if cash_on_hand:
        cash_children.append(_section_row(
            "cash::on_hand", "Cash on hand", None, coh_total, "line",
            [
                _account_row(a, "cash::on_hand", col_keys)
                for a in _merge_accounts_by_label(cash_on_hand, col_keys)
            ],
            col_keys,
        ))
    if cash_at_banks:
        cash_children.append(_section_row(
            "cash::at_banks", "Cash at banks", None, cab_total, "line",
            [
                _account_row(a, "cash::at_banks", col_keys)
                for a in _merge_accounts_by_label(cash_at_banks, col_keys)
            ],
            col_keys,
        ))
    cash_total = {k: round(coh_total[k] + cab_total[k], 2) for k in col_keys}

    bank_children, bank_total = _group_children(_merge_buckets(bank_buckets), "bank")
    sh_children, sh_total = _group_children(_merge_buckets(shareholder_buckets), "shareholder")
    dl_children, dl_total = _group_children(_merge_buckets(debt_like_buckets), "debt_like")

    rows: list[dict[str, Any]] = []
    if cash_children or any(abs(v) > 0.01 for v in cash_total.values()):
        rows.append(_section_row("cash", "Cash & cash equivalents", "1", cash_total, "section_header", cash_children, col_keys))
    if bank_children or any(abs(v) > 0.01 for v in bank_total.values()):
        rows.append(_section_row("bank", "Bank liabilities", "2", bank_total, "section_header", bank_children, col_keys))
    if sh_children or any(abs(v) > 0.01 for v in sh_total.values()):
        rows.append(_section_row("shareholder", "Shareholder loan", "3", sh_total, "section_header", sh_children, col_keys))

    anchor_key = col_keys[-1]
    net_financial = {k: round(cash_total[k] + bank_total[k] + sh_total[k], 2) for k in col_keys}
    rows.append(_section_row("net_financial", "Net financial debt", None, net_financial, "subtotal", [], col_keys))

    has_debt_like = bool(dl_children)
    net_debt = dict(net_financial)
    if has_debt_like:
        rows.append(_section_row(
            "debt_like", "Debt-like items", "4", dl_total, "section_header", dl_children, col_keys,
        ))
        net_debt = {k: round(net_financial[k] + dl_total[k], 2) for k in col_keys}
        rows.append(_section_row("net_debt", "Net debt", None, net_debt, "total", [], col_keys))
    else:
        rows.append(_section_row("net_debt", "Net debt", None, net_financial, "total", [], col_keys))

    from app.services.cash_debt_narrative import build_net_debt_narrative

    try:
        narrative = build_net_debt_narrative(
            session,
            rows=rows,
            col_keys=col_keys,
            col_labels=col_labels,
            anchor_date=cutoff.isoformat(),
            year=year,
            month=month,
            entity=entity,
            allowed_entities=allowed_entities,
        )
    except Exception:
        narrative = {
            "headline": "Key drivers",
            "intro": "",
            "bullets": [],
            "meta": {"algorithm_version": "cash_debt_narrative_v1", "error": True},
        }

    return {
        "year": year,
        "month": month,
        "anchor_date": cutoff.isoformat(),
        "col_keys": col_keys,
        "col_labels": col_labels,
        "col_label": col_labels[anchor_key],
        "unit": "keur",
        "entity": entity,
        "has_debt_like": has_debt_like,
        "net_financial_debt_keur": net_financial[anchor_key],
        "net_debt_keur": net_debt[anchor_key],
        "narrative": narrative,
        "rows": rows,
    }


def build_position_bookings(
    session: Session,
    *,
    account_number_group: str,
    fiscal_year: int,
    year: int,
    month: int,
    entity: Optional[str] = None,
    months_back: int = 24,
    allowed_entities: Optional[set[str]] = None,
) -> dict[str, Any]:
    """Monthly booking series for one GL account (installment / movement view).

    ``allowed_entities`` is the fail-closed entity_prefix allow-set (None = admin /
    unrestricted). When restricted, the requested account's own 2-char prefix
    (= ``LEFT(account_number_group, 2)`` = its entity_prefix) MUST be inside the
    allow-set; otherwise an empty ``entries`` result is returned (no cross-entity
    booking leak). The prefix filter is also applied to the SQL for defence in depth.
    """
    cutoff = last_day(year, month)
    start_year = year - (months_back // 12 + 1)
    start = date(start_year, 1, 1)

    # Fail-closed: a restricted caller may only drill an account whose entity_prefix
    # is within their visibility. None = admin/unrestricted (no check).
    if allowed_entities is not None and (
        not allowed_entities or str(account_number_group)[:2] not in allowed_entities
    ):
        return {
            "account_number_group": account_number_group,
            "account_name": "",
            "fiscal_year": fiscal_year,
            "from_date": start.isoformat(),
            "to_date": cutoff.isoformat(),
            "unit": "keur",
            "entries": [],
        }

    ent_frag = _entity_frag(session, entity, allowed_entities)

    sql = f"""
        SELECT
            e.posting_date::text AS posting_date,
            l.amount::float8 AS amount,
            l.line_note,
            l.booking_line_id,
            SUBSTRING(l.journal_entry_group_number FROM 3) AS journal_entry_number,
            MAX(a.account_name) AS account_name
        FROM fact_gl_line l
        JOIN fact_gl_entry e
          ON e.journal_entry_group_number = l.journal_entry_group_number
         AND e.fiscal_year = l.fiscal_year
        JOIN dim_gl_account a
          ON a.account_number_group = l.account_number_group
         AND a.fiscal_year = l.fiscal_year
        WHERE l.account_number_group = :acct
          AND l.fiscal_year = :fy
          AND e.posting_date BETWEEN :start AND :cutoff
          {ent_frag}
        GROUP BY e.posting_date, l.amount, l.line_note, l.booking_line_id,
                 l.journal_entry_group_number
        ORDER BY e.posting_date, l.booking_line_id
    """
    raw = session.execute(
        text(sql),
        {
            "acct": account_number_group,
            "fy": fiscal_year,
            "start": start.isoformat(),
            "cutoff": cutoff.isoformat(),
        },
    ).fetchall()

    entries = []
    for r in raw:
        m = dict(r._mapping)
        amt = float(m.get("amount") or 0)
        entries.append({
            "posting_date": m.get("posting_date"),
            "amount_keur": _to_keur(amt),
            "line_note": m.get("line_note") or "",
            "booking_line_id": m.get("booking_line_id"),
            "journal_entry_number": m.get("journal_entry_number"),
        })

    account_name = ""
    if raw:
        account_name = str(dict(raw[0]._mapping).get("account_name") or "")

    return {
        "account_number_group": account_number_group,
        "account_name": account_name,
        "fiscal_year": fiscal_year,
        "from_date": start.isoformat(),
        "to_date": cutoff.isoformat(),
        "unit": "keur",
        "entries": entries,
    }
