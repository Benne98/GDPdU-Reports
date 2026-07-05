"""Tests for the NA mapping library — resolver + totals-guard (pure, no DB).

Synthetic library rows + synthetic accounts (CLAUDE.md rule).  Covers:

  resolver  (etl.mapping_library.resolve.resolve_most_frequent)
    - argmax(occurrences) picks the most-frequent mapping.
    - 50/50 tie broken deterministically by lexicographic (mapping, description).
    - single row / empty input.
    - row-order independence (stable winner regardless of input order).

  totals-guard predicate  (backend.scripts.populate_dim_gl_na.changes_total)
    - identical mapping -> never changes a total (short-circuit).
    - WC-membership flip (ND <-> OWC) -> changes a total (pinned).
    - within-WC reshuffle with the SAME CF band -> safe (no total change).
    - within-WC reshuffle with a DIFFERENT CF band -> changes a CF total (pinned).
    - the 3 known loan boundary-crossers are detected as totals-changing.

  override precedence + NWC-identity assertion  (rederive on a synthetic fixture)
    - an ovr_na_mapping row pins an account regardless of the library winner.
    - a small synthetic fixture: the WC-membership of every account is identical
      before and after the re-derive (so NWC, = Σ over WC accounts, is identical).
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKEND = _REPO_ROOT / "backend"
for _p in (str(_REPO_ROOT), str(_BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from etl.mapping_library.resolve import NaRow, resolve_most_frequent


# ---------------------------------------------------------------------------
# resolve_most_frequent
# ---------------------------------------------------------------------------
class TestResolveMostFrequent:

    def test_argmax_occurrences(self):
        rows = [("OWC", "Other assets", 8), ("ND", "Loan to employees", 4)]
        w = resolve_most_frequent(rows)
        assert (w.na_mapping, w.na_description) == ("OWC", "Other assets")

    def test_tie_broken_lexicographically(self):
        # 50/50: "ND" < "OWC" -> ND wins the tie, deterministically.
        rows = [("OWC", "Liabilities due to affiliates", 4),
                ("ND", "Loan to BSG Seifersbach", 4)]
        w = resolve_most_frequent(rows)
        assert (w.na_mapping, w.na_description) == ("ND", "Loan to BSG Seifersbach")

    def test_tie_is_order_independent(self):
        a = [("OWC", "B", 4), ("ND", "A", 4)]
        b = [("ND", "A", 4), ("OWC", "B", 4)]
        assert resolve_most_frequent(a) == resolve_most_frequent(b)

    def test_single_row(self):
        w = resolve_most_frequent([("TWC", "Inventories", 1)])
        assert (w.na_mapping, w.na_description) == ("TWC", "Inventories")

    def test_empty_returns_none(self):
        assert resolve_most_frequent([]) is None

    def test_accepts_dicts_and_namedtuples(self):
        rows = [
            {"na_mapping": "OWC", "na_description": "Other assets", "occurrences": 8},
            NaRow("ND", "Loan to employees", 4),
        ]
        w = resolve_most_frequent(rows)
        assert (w.na_mapping, w.na_description) == ("OWC", "Other assets")

    def test_missing_occurrences_defaults_zero(self):
        # occurrences absent -> 0; still deterministic via the tiebreaker.
        w = resolve_most_frequent([("OWC", "Z"), ("ND", "A")])
        assert (w.na_mapping, w.na_description) == ("ND", "A")


# ---------------------------------------------------------------------------
# totals-guard predicate
# ---------------------------------------------------------------------------
# CF bands mirror the real lib_cf_mapping (key_kind='na') for the relevant
# (l6, l7) keys: ND loan leaves carry their OWN CF band; WC/OWC leaves share the
# "Net working capital" band.
_CF_BAND = {
    ("TWC", "Inventories"):                 ("∆ Trade working capital", "∆ Net working capital"),
    ("TWC", "Advance payments received"):   ("∆ Trade working capital", "∆ Net working capital"),
    ("TWC", "Trade receivables"):           ("∆ Trade working capital", "∆ Net working capital"),
    ("OWC", "Other assets"):                ("∆ Other working capital", "∆ Net working capital"),
    ("OWC", "Liabilities due to affiliates"): ("∆ Other working capital", "∆ Net working capital"),
    ("OWC", "Other provision & accruals"):  ("∆ Other working capital", "∆ Net working capital"),
    ("ND", "Loan to employees"):            ("∆ Other working capital", "∆ Net working capital"),
    ("ND", "Shareholder loans"):            ("∆ Other working capital", "∆ Net working capital"),
    ("ND", "Loan to BSG Seifersbach"):      ("Δ Loan to BSG Seifersbach", "Δ Other operating items"),
}


def _ct(cur, new):
    from backend.scripts.populate_dim_gl_na import changes_total
    return changes_total(cur[0], cur[1], new[0], new[1], _CF_BAND)


class TestTotalsGuard:

    def test_identical_never_changes(self):
        assert _ct(("OWC", "Other assets"), ("OWC", "Other assets")) is False

    def test_wc_membership_flip_changes_total(self):
        # Darlehen Arbeitnehm. acct 03026135: ND -> OWC enters working capital.
        assert _ct(("ND", "Loan to employees"), ("OWC", "Other assets")) is True

    def test_within_wc_same_band_is_safe(self):
        # TWC Inventories <-> TWC Advance payments received: same CF band, both WC.
        assert _ct(("TWC", "Inventories"), ("TWC", "Advance payments received")) is False

    def test_within_wc_different_cf_band_changes_total(self):
        # A reshuffle that keeps WC membership but lands on a different CF band.
        cf = dict(_CF_BAND)
        cf[("OWC", "Special leaf")] = ("∆ Other working capital", "Some other CF band")
        from backend.scripts.populate_dim_gl_na import changes_total
        assert changes_total("OWC", "Other assets", "OWC", "Special leaf", cf) is True

    def test_loan_crossers_all_detected(self):
        # The 3 known loans: each current mapping vs the name's blind winner crosses.
        # Darl. Bet. GmbH an RC HS: ND/Shareholder loans -> OWC/Other provision & accruals
        assert _ct(("ND", "Shareholder loans"), ("OWC", "Other provision & accruals")) is True
        # Darl. RC BR an BSG Seifersbach: ND/Loan to BSG -> OWC/Liabilities due to affiliates
        assert _ct(("ND", "Loan to BSG Seifersbach"),
                   ("OWC", "Liabilities due to affiliates")) is True
        # Darlehen Arbeitnehm.: ND/Loan to employees -> OWC/Other assets
        assert _ct(("ND", "Loan to employees"), ("OWC", "Other assets")) is True


# ---------------------------------------------------------------------------
# NWC-identity on a small synthetic fixture (override precedence + guard)
# ---------------------------------------------------------------------------
def _nwc_membership(accounts):
    """Set of account ids that sit in working capital (l6 in TWC/OWC)."""
    return {a["ang"] for a in accounts if a["l6"] in ("TWC", "OWC")}


class TestNwcIdentityFixture:
    """Simulate rederive's resolution on a tiny fixture and assert WC membership
    (hence NWC = Σ over WC accounts) is identical before and after."""

    def _resolve(self, accounts, library, overrides, cf_band):
        """Mirror rederive's per-account decision (pure, in-memory)."""
        from backend.scripts.populate_dim_gl_na import changes_total
        after = []
        for a in accounts:
            key = (a["ang"], a["fy"])
            if key in overrides:
                l6, l7 = overrides[key]
            else:
                w = resolve_most_frequent(library.get(a["name"], []))
                if w is None:
                    l6, l7 = a["l6"], a["l7"]
                elif changes_total(a["l6"], a["l7"], w.na_mapping, w.na_description, cf_band):
                    l6, l7 = a["l6"], a["l7"]  # guard pins to current
                else:
                    l6, l7 = w.na_mapping, w.na_description
            after.append({**a, "l6": l6, "l7": l7})
        return after

    def test_wc_membership_preserved(self):
        accounts = [
            # safe within-WC reshuffle (Inventories -> Advance payments, same band)
            {"ang": "01", "fy": 2024, "name": "Anzahlung", "l6": "TWC", "l7": "Inventories"},
            # boundary crosser that must be guarded (ND, name winner is OWC)
            {"ang": "02", "fy": 2024, "name": "Darlehen Arbeitnehm.", "l6": "ND", "l7": "Loan to employees"},
            # plain unchanged
            {"ang": "03", "fy": 2024, "name": "Forderungen", "l6": "TWC", "l7": "Trade receivables"},
        ]
        library = {
            "Anzahlung": [("TWC", "Advance payments received", 9), ("TWC", "Inventories", 2)],
            "Darlehen Arbeitnehm.": [("OWC", "Other assets", 8), ("ND", "Loan to employees", 4)],
            "Forderungen": [("TWC", "Trade receivables", 5)],
        }
        cf_band = dict(_CF_BAND)
        before = list(accounts)
        after = self._resolve(accounts, library, overrides={}, cf_band=cf_band)

        # WC membership identical -> NWC (Σ over WC accounts) is identical.
        assert _nwc_membership(after) == _nwc_membership(before)
        # The crosser stayed ND (pinned); the safe one reshuffled to Advance payments.
        amap = {a["ang"]: a for a in after}
        assert amap["02"]["l6"] == "ND"  # guarded, did NOT enter WC
        assert amap["01"]["l7"] == "Advance payments received"  # safe reshuffle applied

    def test_override_precedence(self):
        accounts = [
            {"ang": "02", "fy": 2024, "name": "Darlehen Arbeitnehm.", "l6": "ND", "l7": "Loan to employees"},
        ]
        library = {"Darlehen Arbeitnehm.": [("OWC", "Other assets", 8), ("ND", "Loan to employees", 4)]}
        # A manual override that pins to a DIFFERENT classification beats the library.
        overrides = {("02", 2024): ("OWC", "Other liabilities")}
        after = self._resolve(accounts, library, overrides, dict(_CF_BAND))
        assert (after[0]["l6"], after[0]["l7"]) == ("OWC", "Other liabilities")
