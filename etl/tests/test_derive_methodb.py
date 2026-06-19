"""Tests for Method B (Gegenkonto) partner linking and link_partners dispatcher.

All fixtures are synthetic; no real client data (CLAUDE.md rule).

Method B contract: each row in a DATEV-style canonical DataFrame may carry two
extra columns ('counter_account_class' and 'counter_partner_id') that encode the
*other side* of the double-entry pair directly on the same row.  The function
attributes the partner to the P&L line in-row, without any cross-row grouping.

Test coverage:
  - Positive: revenue line attributed from receivable counter-account
  - Positive: material line attributed from payable counter-account
  - Edge: counter_account_class missing -> no-op (method B not applicable)
  - Edge: counter_partner_id is null -> row left unlinked
  - Edge: customer_id already set -> not overwritten (caller-wins rule)
  - Edge: ambiguous case (revenue vs. payable counter) -> left unlinked
  - derive_ar / derive_ap pass through `due_date` when present
  - link_partners dispatcher routes correctly for all three strategies
  - link_partners raises on unknown strategy
"""
from __future__ import annotations

import pytest
import pandas as pd

from etl import derive as D
from etl.tests import fixtures as F


# --------------------------------------------------------------------------- #
# Helper to build DATEV-style rows with counter-account columns
# --------------------------------------------------------------------------- #
def _datev_row(
    jen: str,
    ln: int,
    account_class: str,
    amount: float,
    ang: str,
    counter_account_class: str | None,
    counter_partner_id: str | None,
    customer_id: str | None = None,
    supplier_id: str | None = None,
    bid: int = 1,
) -> dict:
    return {
        "journal_entry_group_number": jen,
        "fiscal_year": 2024,
        "line_number": ln,
        "booking_line_id": bid,
        "account_number_group": ang,
        "amount": amount,
        "account_class": account_class,
        "customer_id": customer_id,
        "supplier_id": supplier_id,
        "counter_account_class": counter_account_class,
        "counter_partner_id": counter_partner_id,
    }


def _make_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Method B — positive cases
# --------------------------------------------------------------------------- #
class TestPropagatePartnersGegenkonto:
    def test_revenue_line_gets_customer_from_receivable_counter(self):
        """Revenue row: counter is receivable -> customer_id filled, link_method='gegenkonto'."""
        df = _make_df([
            _datev_row("010000000001", 1, "revenue", -1000.0, "0180000",
                       "receivable", "01100", bid=1),
        ])
        out = D.propagate_partners_gegenkonto(df)
        row = out.iloc[0]
        assert row["customer_id"] == "01100"
        assert row["link_method"] == "gegenkonto"

    def test_material_line_gets_supplier_from_payable_counter(self):
        """Material row: counter is payable -> supplier_id filled, link_method='gegenkonto'."""
        df = _make_df([
            _datev_row("010000000002", 1, "material", 500.0, "0130000",
                       "payable", "01200", bid=2),
        ])
        out = D.propagate_partners_gegenkonto(df)
        row = out.iloc[0]
        assert row["supplier_id"] == "01200"
        assert row["link_method"] == "gegenkonto"

    def test_mixed_rows_only_eligible_ones_attributed(self):
        """In a multi-row DataFrame, only matching account_class rows are attributed."""
        df = _make_df([
            _datev_row("010000000001", 1, "revenue", -1000.0, "0180000",
                       "receivable", "01100", bid=1),
            _datev_row("010000000001", 2, "other", -190.0, "0117760",
                       "receivable", "01100", bid=2),  # VAT row: not revenue/material
            _datev_row("010000000002", 1, "material", 500.0, "0130000",
                       "payable", "01200", bid=3),
        ])
        out = D.propagate_partners_gegenkonto(df)
        assert out.loc[0, "customer_id"] == "01100"
        assert out.loc[0, "link_method"] == "gegenkonto"
        assert pd.isna(out.loc[1, "customer_id"])   # VAT row unaffected
        assert out.loc[1, "link_method"] == "none"
        assert out.loc[2, "supplier_id"] == "01200"
        assert out.loc[2, "link_method"] == "gegenkonto"


# --------------------------------------------------------------------------- #
# Method B — edge cases
# --------------------------------------------------------------------------- #
class TestPropagatePartnersGegenkontoEdgeCases:
    def test_missing_counter_columns_is_noop(self):
        """If counter_account_class/counter_partner_id columns are absent, return unchanged."""
        df = _make_df([
            {
                "journal_entry_group_number": "010000000001",
                "fiscal_year": 2024,
                "line_number": 1,
                "booking_line_id": 1,
                "account_number_group": "0180000",
                "amount": -1000.0,
                "account_class": "revenue",
                "customer_id": None,
                "supplier_id": None,
            }
        ])
        out = D.propagate_partners_gegenkonto(df)
        assert pd.isna(out.loc[0, "customer_id"])
        assert out.loc[0, "link_method"] == "none"

    def test_null_counter_partner_id_leaves_row_unlinked(self):
        """counter_partner_id is null -> row is NOT attributed."""
        df = _make_df([
            _datev_row("010000000001", 1, "revenue", -1000.0, "0180000",
                       "receivable", None, bid=1),
        ])
        out = D.propagate_partners_gegenkonto(df)
        assert pd.isna(out.loc[0, "customer_id"])
        assert out.loc[0, "link_method"] == "none"

    def test_existing_customer_id_not_overwritten(self):
        """If customer_id is already set (e.g. from source_no), method B does not overwrite."""
        df = _make_df([
            _datev_row("010000000001", 1, "revenue", -1000.0, "0180000",
                       "receivable", "01999", customer_id="01100", bid=1),
        ])
        out = D.propagate_partners_gegenkonto(df)
        # caller-wins: existing 01100 is kept, not replaced by 01999
        assert out.loc[0, "customer_id"] == "01100"

    def test_revenue_with_payable_counter_unlinked(self):
        """Revenue row + payable counter (cross-type mismatch) -> not attributed."""
        df = _make_df([
            _datev_row("010000000001", 1, "revenue", -1000.0, "0180000",
                       "payable", "01200", bid=1),
        ])
        out = D.propagate_partners_gegenkonto(df)
        assert pd.isna(out.loc[0, "customer_id"])
        assert out.loc[0, "link_method"] == "none"

    def test_empty_dataframe(self):
        """Empty input -> empty output, no crash."""
        df = pd.DataFrame(columns=[
            "journal_entry_group_number", "fiscal_year", "line_number",
            "booking_line_id", "account_number_group", "amount", "account_class",
            "customer_id", "supplier_id", "counter_account_class", "counter_partner_id",
        ])
        out = D.propagate_partners_gegenkonto(df)
        assert out.empty


# --------------------------------------------------------------------------- #
# derive_ar / derive_ap — due_date passthrough
# --------------------------------------------------------------------------- #
class TestDeriveArApDueDate:
    def test_derive_ar_passes_through_due_date(self):
        """derive_ar carries due_date when the column is present."""
        lines = F.canonical_lines()
        lines["due_date"] = pd.to_datetime("2024-03-31")
        ar = D.derive_ar(lines)
        assert "due_date" in ar.columns
        assert ar["due_date"].notna().all()

    def test_derive_ap_passes_through_due_date(self):
        """derive_ap carries due_date when the column is present."""
        lines = F.canonical_lines()
        lines["due_date"] = pd.to_datetime("2024-04-30")
        ap = D.derive_ap(lines)
        assert "due_date" in ap.columns
        assert ap["due_date"].notna().all()

    def test_derive_ar_no_error_when_due_date_absent(self):
        """derive_ar does not crash when due_date column is absent."""
        lines = F.canonical_lines()
        ar = D.derive_ar(lines)
        assert "due_date" not in ar.columns
        assert not ar.empty

    def test_derive_ap_no_error_when_due_date_absent(self):
        """derive_ap does not crash when due_date column is absent."""
        lines = F.canonical_lines()
        ap = D.derive_ap(lines)
        assert "due_date" not in ap.columns
        assert not ap.empty


# --------------------------------------------------------------------------- #
# link_partners dispatcher
# --------------------------------------------------------------------------- #
class TestLinkPartnersDispatcher:
    def test_txn_strategy_routes_to_method_a(self):
        """'txn' strategy calls propagate_partners (method A)."""
        lines = F.canonical_lines()
        out = D.link_partners(lines, "txn")
        rev = out[out["account_class"] == "revenue"]
        assert (rev["link_method"] == "txn").all()

    def test_gegenkonto_strategy_routes_to_method_b(self):
        """'gegenkonto' strategy calls propagate_partners_gegenkonto (method B)."""
        df = _make_df([
            _datev_row("010000000001", 1, "revenue", -1000.0, "0180000",
                       "receivable", "01100", bid=1),
        ])
        out = D.link_partners(df, "gegenkonto")
        assert out.loc[0, "customer_id"] == "01100"
        assert out.loc[0, "link_method"] == "gegenkonto"

    def test_none_strategy_is_noop(self):
        """'none' strategy leaves partners unattributed, sets link_method='none'."""
        lines = F.canonical_lines()
        out = D.link_partners(lines, "none")
        assert (out["link_method"] == "none").all()
        # revenue lines should still have no customer_id (no propagation)
        rev = out[out["account_class"] == "revenue"]
        assert rev["customer_id"].isna().all()

    def test_unknown_strategy_raises(self):
        """Unknown strategy raises ValueError."""
        lines = F.canonical_lines()
        with pytest.raises(ValueError, match="unknown linking strategy"):
            D.link_partners(lines, "invalid_strategy")
