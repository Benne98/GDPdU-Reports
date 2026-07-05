"""Regression tests for net debt cash & debt table service.

Bug locks (two regressions from the receivable-exclusion fix):

  (1) EMPTY-RENDER regression — rows must NEVER be [].
      build_net_debt_table always appends a 'subtotal' (Net financial debt) and
      a 'total' (Net debt) row regardless of whether any GL accounts are present.

  (2) MAPPING regression — an ND-mapped asset-side receivable (level_3 matching
      /receivable/i, e.g. 'Receivables from affiliates') must NOT be classified
      into any bank / shareholder / debt-like bucket, and must NOT alter net debt.

Sign convention (read-only, not altered here):
  fact_gl_line.amount uses raw BS signs: + = debit/asset, - = credit/liability.
  Cash & receivables => positive balances; bank/shareholder liabilities => negative.
  kEUR = amount / 1000.

Layers tested:
  A. Pure helpers (_is_receivable_asset, _is_shareholder, _is_bank_nd)
     — no DB, no mock, sub-millisecond.

  B. build_net_debt_table via MagicMock session.
     entity=None avoids the resolve_entity_prefix DB call entirely.
     _fetch_accounts is exercised by mocking session.execute to return
     _MappingRow instances whose ._mapping is a plain dict (converted
     with dict(r._mapping) inside the service — identical to SQLAlchemy
     RowMapping behaviour).

Why not a real ephemeral DB: The backend/tests/conftest.py has no DB fixture
and no existing tests stand up Postgres infra. All tests that touch
build_net_debt_table in the existing suite use MagicMock sessions
(see test_fdd_gl_data_status.py for the canonical pattern). Adding a real
Postgres dependency would violate the lightweight-test rule in AGENTS.md.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from app.services.fin_compat_cash_debt import (
    _is_bank_nd,
    _is_receivable_asset,
    _is_shareholder,
    build_net_debt_table,
    build_position_bookings,
)


# ---------------------------------------------------------------------------
# Row helper — mirrors SQLAlchemy Row._mapping interface
# dict(row._mapping) inside _fetch_accounts becomes dict(plain_dict) == plain_dict copy
# ---------------------------------------------------------------------------
class _MappingRow:
    """Minimal SQLAlchemy Row-like: ._mapping is a plain dict."""

    def __init__(self, data: dict):
        self._mapping = data


def _acct_row(
    *,
    account_number_group: str,
    account_name: str,
    level_3: str,
    level_4: str = "",
    l6_na: str = "",
    l7_na: str = "",
    balance: float,
    balances_by_index: dict[int, float] | None = None,
) -> _MappingRow:
    """Construct a synthetic _MappingRow matching _fetch_accounts_multi SELECT columns."""
    data = {
        "account_number_group": account_number_group,
        "gl_account_id": account_number_group,
        "account_name": account_name,
        "level_3": level_3,
        "level_4": level_4,
        "l6_na": l6_na,
        "l7_na": l7_na,
    }
    for i in range(6):
        data[f"bal_{i}"] = (balances_by_index or {}).get(i, balance)
    return _MappingRow(data)


def _make_session(rows: list[_MappingRow]) -> MagicMock:
    """MagicMock Session: returns canned _MappingRow rows for the fact_gl_line
    accounts query; returns empty/None for any other SQL (e.g. dim_legal_entity
    in resolve_entity_prefix — not called when entity=None)."""
    session = MagicMock()

    def _execute(stmt, params=None):
        result = MagicMock()
        sql = str(stmt)
        if "fact_gl_line" in sql:
            result.fetchall.return_value = rows
        else:
            result.fetchall.return_value = []
            result.fetchone.return_value = None
        return result

    session.execute.side_effect = _execute
    return session


# ===========================================================================
# A. Pure helper tests — no DB, no mock
# ===========================================================================

class TestIsReceivableAsset:
    """_is_receivable_asset — locks the receivable-exclusion guard."""

    def test_true_for_receivables_from_affiliates(self):
        # Canonical intercompany receivable that triggered the original bug
        assert _is_receivable_asset("Receivables from affiliates") is True

    def test_true_case_insensitive_upper(self):
        assert _is_receivable_asset("RECEIVABLE accounts") is True

    def test_true_trade_receivable(self):
        assert _is_receivable_asset("Trade receivable") is True

    def test_false_for_liabilities_due_to_affiliates(self):
        # This is a LIABILITY — must NOT be filtered as a receivable
        assert _is_receivable_asset("Liabilities due to affiliates") is False

    def test_false_for_cash(self):
        assert _is_receivable_asset("Cash & cash equivalents") is False

    def test_false_for_bank_liability(self):
        assert _is_receivable_asset("Liabilities due to banks") is False

    def test_false_for_empty_string(self):
        assert _is_receivable_asset("") is False

    def test_false_for_none(self):
        # Guard must not raise on None; returns False
        assert _is_receivable_asset(None) is False


class TestIsShareholderHelper:
    """_is_shareholder — spot-check the three entry paths."""

    def test_affiliate_l3_constant_triggers_true(self):
        # AFFILIATE_L3 = "Liabilities due to affiliates" → always shareholder
        assert _is_shareholder("", "", "Liabilities due to affiliates") is True

    def test_shareholder_keyword_in_l7(self):
        assert _is_shareholder("Shareholder loan - Müller", "", "Other") is True

    def test_bank_liability_is_not_shareholder(self):
        assert _is_shareholder("", "Commerzbank loan", "Liabilities due to banks") is False


class TestIsBankNd:
    """_is_bank_nd — spot-check classification."""

    def test_bank_l3_constant_triggers_true(self):
        # BANK_L3 = "Liabilities due to banks" → always bank
        assert _is_bank_nd("", "", "Liabilities due to banks") is True

    def test_known_bank_name_in_account_name(self):
        assert _is_bank_nd("", "Commerzbank Kredit", "Other") is True

    def test_generic_bank_keyword(self):
        assert _is_bank_nd("", "Bankdarlehen", "Other") is True

    def test_shareholder_account_is_not_bank(self):
        # "Gesellschafter" contains no bank keyword
        assert _is_bank_nd("", "Gesellschafter Darlehen", "Other liabilities") is False


# ===========================================================================
# B. build_net_debt_table via MagicMock session
# ===========================================================================

class TestBuildNetDebtEmptyGuarantee:
    """(1) EMPTY-RENDER regression: rows must never be empty even with zero accounts.

    The service unconditionally appends:
      - row_kind='subtotal', label='Net financial debt'
      - row_kind='total',    label='Net debt'
    """

    def test_rows_is_not_empty(self):
        session = _make_session([])
        result = build_net_debt_table(session, year=2024, month=7, entity=None)
        assert result["rows"], "rows must never be [] regardless of input data"

    def test_rows_contains_at_least_two_entries(self):
        session = _make_session([])
        result = build_net_debt_table(session, year=2024, month=7, entity=None)
        assert len(result["rows"]) >= 2

    def test_subtotal_row_always_present(self):
        session = _make_session([])
        result = build_net_debt_table(session, year=2024, month=7, entity=None)
        row_kinds = {r["row_kind"] for r in result["rows"]}
        assert "subtotal" in row_kinds, "subtotal row (Net financial debt) must always be present"

    def test_total_row_always_present(self):
        session = _make_session([])
        result = build_net_debt_table(session, year=2024, month=7, entity=None)
        row_kinds = {r["row_kind"] for r in result["rows"]}
        assert "total" in row_kinds, "total row (Net debt) must always be present"

    def test_subtotal_label_is_net_financial_debt(self):
        session = _make_session([])
        result = build_net_debt_table(session, year=2024, month=7, entity=None)
        subtotals = [r for r in result["rows"] if r["row_kind"] == "subtotal"]
        labels = {r["label"] for r in subtotals}
        assert "Net financial debt" in labels

    def test_total_label_is_net_debt(self):
        session = _make_session([])
        result = build_net_debt_table(session, year=2024, month=7, entity=None)
        totals = [r for r in result["rows"] if r["row_kind"] == "total"]
        labels = {r["label"] for r in totals}
        assert "Net debt" in labels

    def test_zero_balances_on_empty_accounts(self):
        session = _make_session([])
        result = build_net_debt_table(session, year=2024, month=7, entity=None)
        assert result["net_financial_debt_keur"] == 0.0
        assert result["net_debt_keur"] == 0.0


class TestBuildNetDebtReceivableMapping:
    """(2) MAPPING regression: ND-mapped receivable must NOT enter any debt bucket
    and must NOT change net debt.

    Synthetic dataset (amounts in EUR; /1000 = kEUR):
      Cash at banks:         +5 000 EUR  → +5.0 kEUR   (asset, positive BS balance)
      Bank liability:       -10 000 EUR  → -10.0 kEUR  (liability, negative BS balance)
      Shareholder loan:      -3 000 EUR  → -3.0 kEUR   (liability, negative BS balance)
      ND receivable (excl):  +2 000 EUR  → +2.0 kEUR   (asset, EXCLUDED by _is_receivable_asset)

    Expected:
      net_financial_debt = 5.0 + (−10.0) + (−3.0) = −8.0 kEUR
      net_debt           = −8.0 kEUR  (no debt-like rows)
      Receivable row absent from all table sections.
    """

    # Build synthetic rows at class level; shared across tests (read-only)
    _SYNTHETIC = [
        _acct_row(
            account_number_group="1000",
            account_name="Bank account",
            level_3="Cash & cash equivalents",
            level_4="",
            l6_na="",
            l7_na="",
            balance=5_000.0,
        ),
        _acct_row(
            account_number_group="2000",
            account_name="Commerzbank Kredit",
            level_3="Liabilities due to banks",
            l6_na="ND",
            l7_na="",
            balance=-10_000.0,
        ),
        _acct_row(
            account_number_group="3000",
            account_name="Shareholder loan Müller",
            level_3="Liabilities due to affiliates",
            l6_na="ND",
            l7_na="Shareholder loans",
            balance=-3_000.0,
        ),
        # This account carries an ND na-mapping but is an ASSET (receivable).
        # It must be dropped by _is_receivable_asset before reaching any bucket.
        _acct_row(
            account_number_group="4000",
            account_name="Receivables from affiliates",
            level_3="Receivables from affiliates",
            l6_na="ND",
            l7_na="",
            balance=2_000.0,
        ),
    ]

    def test_net_financial_debt_is_minus_eight_keur(self):
        """5.0 cash + (-10.0) bank + (-3.0) shareholder = -8.0; receivable excluded."""
        session = _make_session(self._SYNTHETIC)
        result = build_net_debt_table(session, year=2024, month=7, entity=None)
        assert result["net_financial_debt_keur"] == -8.0, (
            f"Expected -8.0 kEUR, got {result['net_financial_debt_keur']}. "
            "Receivable (+2.0 kEUR) may have been added to or subtracted from a bucket."
        )

    def test_net_debt_is_minus_eight_keur(self):
        """No debt-like items → net_debt equals net_financial_debt."""
        session = _make_session(self._SYNTHETIC)
        result = build_net_debt_table(session, year=2024, month=7, entity=None)
        assert result["net_debt_keur"] == -8.0

    def test_receivable_absent_from_all_rows(self):
        """Receivable account label must not appear anywhere in the row tree."""
        session = _make_session(self._SYNTHETIC)
        result = build_net_debt_table(session, year=2024, month=7, entity=None)

        def _collect_labels(rows: list) -> set[str]:
            labels: set[str] = set()
            for row in rows:
                labels.add(row.get("label", ""))
                for child in row.get("children", []):
                    labels.add(child.get("label", ""))
                    for grandchild in child.get("children", []):
                        labels.add(grandchild.get("label", ""))
            return labels

        all_labels = _collect_labels(result["rows"])
        assert "Receivables from affiliates" not in all_labels, (
            f"Receivable account leaked into table rows. All labels found: {all_labels}"
        )

    def test_rows_not_empty_with_data(self):
        """Empty-guarantee holds even when data is present."""
        session = _make_session(self._SYNTHETIC)
        result = build_net_debt_table(session, year=2024, month=7, entity=None)
        assert len(result["rows"]) >= 2

    def test_cash_section_present(self):
        """Cash & cash equivalents section must appear (positive balance)."""
        session = _make_session(self._SYNTHETIC)
        result = build_net_debt_table(session, year=2024, month=7, entity=None)
        section_labels = {r["label"] for r in result["rows"] if r["row_kind"] == "section_header"}
        assert "Cash & cash equivalents" in section_labels

    def test_result_metadata(self):
        """Sanity check on the returned metadata fields."""
        session = _make_session(self._SYNTHETIC)
        result = build_net_debt_table(session, year=2024, month=7, entity=None)
        assert result["year"] == 2024
        assert result["month"] == 7
        assert result["unit"] == "keur"
        assert result["anchor_date"] == "2024-07-31"
        assert result["col_label"] == "Jul24A"


class TestMergeDuplicateAccountLabels:
    """Same account_name across entity prefixes / fiscal years → one summed row."""

    _DUPLICATE_LOANS = [
        _acct_row(
            account_number_group="011234",
            account_name="Darl. Bet. GmbH an RC HS",
            level_3="Liabilities due to affiliates",
            l6_na="ND",
            l7_na="Shareholder loans",
            balance=-1_000_000,
        ),
        _acct_row(
            account_number_group="021234",
            account_name="Darl. Bet. GmbH an RC HS",
            level_3="Liabilities due to affiliates",
            l6_na="ND",
            l7_na="Shareholder loans",
            balance=-500_000,
        ),
    ]

    def test_duplicate_account_names_collapsed_to_one_row(self):
        session = _make_session(self._DUPLICATE_LOANS)
        result = build_net_debt_table(session, year=2024, month=7, entity=None)
        sh = next(r for r in result["rows"] if r["id"] == "shareholder")
        account_labels = [
            c["label"]
            for c in sh.get("children", [])
            if c.get("row_kind") == "account"
        ]
        assert account_labels.count("Darl. Bet. GmbH an RC HS") == 1

    def test_duplicate_account_names_summed(self):
        session = _make_session(self._DUPLICATE_LOANS)
        result = build_net_debt_table(session, year=2024, month=7, entity=None)
        sh = next(r for r in result["rows"] if r["id"] == "shareholder")
        acct = next(
            c for c in sh.get("children", [])
            if c.get("label") == "Darl. Bet. GmbH an RC HS"
        )
        assert acct["amount_keur"] == -1500.0


# ===========================================================================
# C. Fail-closed entity-visibility (row-level tenant isolation)
# ===========================================================================
def _capture_session(rows: list[_MappingRow]):
    """MagicMock Session that records every executed SQL string (for fragment asserts)."""
    session = MagicMock()
    captured: list[str] = []

    def _execute(stmt, params=None):
        result = MagicMock()
        sql = str(stmt)
        captured.append(sql)
        if "fact_gl_line" in sql:
            result.fetchall.return_value = rows
        else:
            result.fetchall.return_value = []
            result.fetchone.return_value = None
        return result

    session.execute.side_effect = _execute
    return session, captured


class TestNetDebtEntityVisibilityFailClosed:
    """``allowed_entities`` fail-closed contract for build_net_debt_table.

    None = admin/unrestricted; empty set = restricted-with-no-visibility → deny-all
    (``AND 1 = 0``), NEVER the no-filter path; non-empty = filter to those prefixes.
    """

    def test_empty_visibility_injects_deny_all_fragment(self):
        session, captured = _capture_session([])
        build_net_debt_table(session, year=2024, month=7, entity=None, allowed_entities=set())
        assert any("AND 1 = 0" in s for s in captured), (
            "empty visibility must inject a row-blocking fragment, never the no-filter path"
        )

    def test_empty_visibility_preserves_empty_guarantee(self):
        session, _ = _capture_session([])
        result = build_net_debt_table(session, year=2024, month=7, entity=None, allowed_entities=set())
        row_kinds = {r["row_kind"] for r in result["rows"]}
        assert "subtotal" in row_kinds and "total" in row_kinds
        assert result["net_financial_debt_keur"] == 0.0
        assert result["net_debt_keur"] == 0.0

    def test_restricted_prefix_injects_prefix_filter(self):
        session, captured = _capture_session([])
        build_net_debt_table(session, year=2024, month=7, entity=None, allowed_entities={"12"})
        assert any("entity_prefix = '12'" in s for s in captured)
        assert not any("AND 1 = 0" in s for s in captured)

    def test_admin_none_has_no_deny_fragment(self):
        session, captured = _capture_session([])
        build_net_debt_table(session, year=2024, month=7, entity=None, allowed_entities=None)
        assert not any("AND 1 = 0" in s for s in captured)


class TestPositionBookingsEntityVisibilityFailClosed:
    """``allowed_entities`` fail-closed contract for build_position_bookings.

    The requested account's 2-char prefix must be within the allow-set; otherwise
    an empty ``entries`` result is returned WITHOUT running the booking query.
    """

    def test_account_outside_visibility_returns_empty_without_query(self):
        session = MagicMock()
        result = build_position_bookings(
            session, account_number_group="4000", fiscal_year=2024,
            year=2024, month=7, allowed_entities={"12"},
        )
        assert result["entries"] == []
        session.execute.assert_not_called()

    def test_empty_visibility_returns_empty_without_query(self):
        session = MagicMock()
        result = build_position_bookings(
            session, account_number_group="4000", fiscal_year=2024,
            year=2024, month=7, allowed_entities=set(),
        )
        assert result["entries"] == []
        session.execute.assert_not_called()

    def test_account_within_visibility_runs_query(self):
        session = MagicMock()
        exec_result = MagicMock()
        exec_result.fetchall.return_value = []
        session.execute.return_value = exec_result
        result = build_position_bookings(
            session, account_number_group="4000", fiscal_year=2024,
            year=2024, month=7, allowed_entities={"40"},
        )
        session.execute.assert_called()
        assert result["entries"] == []

    def test_admin_none_runs_query(self):
        session = MagicMock()
        exec_result = MagicMock()
        exec_result.fetchall.return_value = []
        session.execute.return_value = exec_result
        build_position_bookings(
            session, account_number_group="4000", fiscal_year=2024,
            year=2024, month=7, allowed_entities=None,
        )
        session.execute.assert_called()
