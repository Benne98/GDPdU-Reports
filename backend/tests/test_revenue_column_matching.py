"""Tests for revenue column fuzzy matching."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.revenue_column_matching import (  # noqa: E402
    fast_track_defaults,
    suggest_for_slot,
    suggest_revenue_column_mapping,
)


def test_suggest_english_headers():
    headers = [
        "Invoice date",
        "Net sales",
        "Customer name",
        "Product",
        "Entity",
        "Quantity",
        "Cost",
    ]
    mapping = suggest_revenue_column_mapping(headers)
    assert mapping["revenue"] == "Net sales"
    assert mapping["invoice"] == "Invoice date"
    assert mapping["customer"] == "Customer name"
    assert mapping["product"] == "Product"
    assert mapping["entity"] == "Entity"


def test_suggest_german_headers():
    headers = [
        "Rechnungsdatum",
        "Umsatz",
        "Kunde",
        "Produkt",
        "Gesellschaft",
        "Menge",
        "Kosten",
    ]
    mapping = suggest_revenue_column_mapping(headers)
    assert mapping["revenue"] == "Umsatz"
    assert mapping["invoice"] == "Rechnungsdatum"
    assert mapping["customer"] == "Kunde"
    assert mapping["product"] == "Produkt"


def test_suggest_for_gst_slot():
    headers = ["Revenue", "Invoice Date", "Customer"]
    assert suggest_for_slot(headers, "gst_revenue_col") == "Revenue"
    assert suggest_for_slot(headers, "top_col") == "Customer"


def test_fast_track_defaults_shape():
    headers = ["Revenue", "Invoice date", "Entity", "Product", "Customer"]
    defaults = fast_track_defaults(headers)
    assert defaults["revenue_col"] == "Revenue"
    assert defaults["invoice_col"] == "Invoice date"
    assert defaults["entity_col"] == "Entity"
