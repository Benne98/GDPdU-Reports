"""Fuzzy column suggestions for revenue databook mappings (English + German)."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# Canonical roles used across GST/PVM/TOP/Churn/Bubble and Fast Track revenue.
ROLE_ALIASES: dict[str, list[str]] = {
    "revenue": [
        "revenue", "net sales", "sales", "amount", "value", "turnover", "net revenue",
        "invoice amount", "sales amount", "total revenue", "nettoerlös", "umsatz",
        "erlös", "nettoumsatz", "rechnungsbetrag", "betrag", "wert", "netto umsatz",
    ],
    "invoice": [
        "invoice date", "date", "billing date", "posting date", "document date",
        "rechnungsdatum", "datum", "belegdatum", "buchungsdatum", "fakturadatum",
    ],
    "period": [
        "period", "fiscal year", "fy", "year", "reporting period", "periode",
        "geschäftsjahr", "gj", "jahr", "berichtsperiode", "fy label",
    ],
    "entity": [
        "entity", "company", "legal entity", "subsidiary", "unit", "segment entity",
        "gesellschaft", "unternehmen", "einheit", "mandant", "tochtergesellschaft",
    ],
    "product": [
        "product", "sku", "item", "service", "product name", "product category",
        "produkt", "artikel", "leistung", "warengruppe", "produktname",
    ],
    "customer": [
        "customer", "client", "account", "customer name", "buyer", "debtor",
        "kunde", "kundenname", "auftraggeber", "debitor", "kunden",
    ],
    "quantity": [
        "quantity", "qty", "units", "volume", "count", "pieces",
        "menge", "anzahl", "stück", "volumen", "einheiten",
    ],
    "cost": [
        "cost", "cogs", "cost of sales", "direct cost", "kosten", "wareneinsatz",
        "herstellungskosten", "einkaufspreis",
    ],
    "profit": [
        "profit", "gross profit", "margin", "gp", "rohertrag", "deckungsbeitrag",
        "bruttoertrag", "marge",
    ],
    "fx": [
        "fx", "fx rate", "exchange rate", "currency rate", "wechselkurs", "kurs",
        "währungskurs", "devisenkurs",
    ],
    "contract_start": [
        "contract start", "start date", "service start", "begin date", "valid from",
        "vertragsbeginn", "startdatum", "laufzeitbeginn", "gültig ab",
    ],
    "contract_end": [
        "contract end", "end date", "service end", "expiry", "valid to",
        "vertragsende", "endedatum", "laufzeitende", "gültig bis",
    ],
    "segment": [
        "segment", "category", "business line", "division", "segment name",
        "segment kategorie", "sparte", "geschäftsbereich", "abteilung",
    ],
    "region": [
        "region", "end customer region", "customer region", "geo", "geography",
        "customer geography", "end customer geography", "gebiet", "region name",
        "verkaufsregion", "sales region",
    ],
}

SLOT_ROLE_MAP: dict[str, str] = {
    "gst_revenue_col": "revenue",
    "pvm_revenue_col": "revenue",
    "top_value_col": "revenue",
    "bs_revenue_col": "revenue",
    "churn_value_col": "revenue",
    "gst_invoice_col": "invoice",
    "pvm_invoice_col": "invoice",
    "top_invoice_col": "invoice",
    "bs_invoice_col": "invoice",
    "churn_invoice_col": "invoice",
    "gst_start_col": "contract_start",
    "pvm_start_col": "contract_start",
    "top_start_col": "contract_start",
    "bs_start_col": "contract_start",
    "churn_start_col": "contract_start",
    "gst_end_col": "contract_end",
    "pvm_end_col": "contract_end",
    "top_end_col": "contract_end",
    "bs_end_col": "contract_end",
    "churn_end_col": "contract_end",
    "gst_cost_col": "cost",
    "pvm_cost_col": "cost",
    "bs_cogs_col": "cost",
    "gst_profit_col": "profit",
    "pvm_profit_col": "profit",
    "bs_profit_col": "profit",
    "pvm_quantity_col": "quantity",
    "top_col": "customer",
    "pvm_group_col": "product",
    "gst_group_col_1": "entity",
    "bs_group_col_1": "product",
    "churn_customer_col": "customer",
    "churn_product_col": "product",
}

FAST_TRACK_DEFAULT_KEYS: dict[str, str] = {
    "revenue": "revenue_col",
    "invoice": "invoice_col",
    "period": "period_col",
    "entity": "entity_col",
    "product": "product_col",
    "customer": "customer_col",
    "quantity": "quantity_col",
    "cost": "cost_col",
    "profit": "profit_col",
    "fx": "fx_col",
    "contract_start": "contract_start_col",
    "contract_end": "contract_end_col",
    "segment": "segment_col",
    "region": "region_col",
}


def _normalize_header(text: str) -> str:
    raw = unicodedata.normalize("NFKD", str(text or ""))
    raw = raw.encode("ascii", "ignore").decode("ascii")
    raw = re.sub(r"[^a-z0-9]+", " ", raw.lower()).strip()
    return raw


def _score_header(header: str, alias: str) -> int:
    norm_h = _normalize_header(header)
    norm_a = _normalize_header(alias)
    if not norm_h or not norm_a:
        return 0
    if norm_h == norm_a:
        return 100
    if norm_a in norm_h.split():
        return 95
    if norm_a in norm_h:
        return 90
    return int(SequenceMatcher(None, norm_h, norm_a).ratio() * 100)


def suggest_revenue_column_mapping(headers: list[str], *, min_score: int = 72) -> dict[str, str]:
    """Return best header per role key (revenue, invoice, entity, …)."""
    clean_headers = [str(h).strip() for h in headers if str(h).strip()]
    if not clean_headers:
        return {}

    used: set[str] = set()
    out: dict[str, str] = {}
    for role, aliases in ROLE_ALIASES.items():
        best_header = ""
        best_score = 0
        for header in clean_headers:
            if header in used:
                continue
            score = max(_score_header(header, alias) for alias in aliases)
            if score > best_score:
                best_score = score
                best_header = header
        if best_header and best_score >= min_score:
            out[role] = best_header
            used.add(best_header)
    return out


def suggest_for_slot(headers: list[str], slot: str, *, min_score: int = 72) -> str:
    role = SLOT_ROLE_MAP.get(slot)
    if not role:
        return ""
    return suggest_revenue_column_mapping(headers, min_score=min_score).get(role, "")


def fast_track_defaults(headers: list[str]) -> dict[str, str]:
    suggestions = suggest_revenue_column_mapping(headers)
    return {
        target_key: suggestions[role]
        for role, target_key in FAST_TRACK_DEFAULT_KEYS.items()
        if role in suggestions
    }


def best_header_match(headers: list[str], aliases: list[str], *, min_score: int = 72) -> str:
    if not headers:
        return ""
    choices = [str(h).strip() for h in headers if str(h).strip()]
    if not choices:
        return ""
    scored: list[tuple[int, str]] = []
    for header in choices:
        score = max(_score_header(header, alias) for alias in aliases)
        scored.append((score, header))
    scored.sort(reverse=True)
    if scored and scored[0][0] >= min_score:
        return scored[0][1]
    return ""
