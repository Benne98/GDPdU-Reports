"""Tests for etl/mapping_suggest.py — Phase 4 library auto-suggest (pure, no DB).

Synthetic library + synthetic dataset accounts (CLAUDE.md rule).  Covers:
  - gl_account_id exact match wins (match='gl_account_id').
  - normalized account_name match when no id hit (match='name').
  - id match takes precedence over a name match.
  - unmatched accounts go to the 'unmatched' list (for the editor).
  - normalisation tolerance (case / whitespace / punctuation / '.0' artifact).
  - identity echo + proposal fields shape.
  - empty / blank inputs are safe (no false matches on '').
  - deterministic: duplicate library keys -> first entry wins.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from etl import mapping_suggest as MS


def _lib(*entries) -> dict:
    return {"version": "test_v1", "entries": list(entries)}


def _entry(gid=None, name=None, **levels):
    e = {"gl_account_id": gid, "account_name": name}
    e.update(levels)
    return e


# --------------------------------------------------------------------------- #
# Normalisation helpers
# --------------------------------------------------------------------------- #
class TestNormalize:
    def test_name_casefold_whitespace_punct(self):
        assert MS.normalize_name("Trade  Receivables.") == MS.normalize_name("trade receivables")
        assert MS.normalize_name("Über-Konto") == MS.normalize_name("uber konto") or \
            MS.normalize_name("Über-Konto") == "über konto"

    def test_name_blank_is_empty(self):
        assert MS.normalize_name(None) == ""
        assert MS.normalize_name("   ") == ""

    def test_gl_id_strip_dot_zero(self):
        assert MS.normalize_gl_id("38235.0") == "38235"
        assert MS.normalize_gl_id(" 16100 ") == "16100"
        assert MS.normalize_gl_id(None) == ""


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
class TestMatching:
    def test_gl_id_exact_match(self):
        lib = _lib(_entry(gid="16100", name="Bau", level_0="BS", level_3="Tangible assets", level_3_sort=13))
        out = MS.suggest_account_mapping(
            [{"account_number_group": "0116100", "gl_account_id": "16100", "account_name": "anything"}],
            lib,
        )
        assert out["unmatched"] == []
        p = out["proposals"][0]
        assert p["match"] == "gl_account_id"
        assert p["level_0"] == "BS"
        assert p["level_3"] == "Tangible assets"
        assert p["level_3_sort"] == 13
        # identity echoed
        assert p["account_number_group"] == "0116100"

    def test_name_match_when_no_id_hit(self):
        lib = _lib(_entry(gid="999", name="Trade receivables", level_0="BS", level_2="Current assets"))
        out = MS.suggest_account_mapping(
            [{"gl_account_id": "no-such-id", "account_name": "  trade   RECEIVABLES "}],
            lib,
        )
        p = out["proposals"][0]
        assert p["match"] == "name"
        assert p["level_2"] == "Current assets"

    def test_id_precedence_over_name(self):
        # id points at entry A, name points at entry B -> id must win
        lib = _lib(
            _entry(gid="100", name="Name A", level_2="FROM_ID"),
            _entry(gid="200", name="Shared name", level_2="FROM_NAME"),
        )
        out = MS.suggest_account_mapping(
            [{"gl_account_id": "100", "account_name": "Shared name"}],
            lib,
        )
        assert out["proposals"][0]["match"] == "gl_account_id"
        assert out["proposals"][0]["level_2"] == "FROM_ID"

    def test_unmatched_goes_to_list(self):
        lib = _lib(_entry(gid="16100", name="Bau", level_0="BS"))
        out = MS.suggest_account_mapping(
            [{"account_number_group": "0199999", "gl_account_id": "99999", "account_name": "Unknown"}],
            lib,
        )
        assert out["proposals"] == []
        assert out["unmatched"] == [
            {"account_number_group": "0199999", "gl_account_id": "99999", "account_name": "Unknown"}
        ]

    def test_blank_name_does_not_match_blank(self):
        # a library entry with no name + an account with no name must NOT match on ""
        lib = _lib(_entry(gid="500", name=None, level_0="BS"))
        out = MS.suggest_account_mapping(
            [{"gl_account_id": "no-id", "account_name": None}],
            lib,
        )
        assert out["proposals"] == []
        assert len(out["unmatched"]) == 1

    def test_proposal_carries_all_fields(self):
        lib = _lib(_entry(gid="1", name="X", level_0="PL", level_1="P&L",
                          level_2="Revenue", level_3="Net sales", level_4="L4",
                          l4_sub="sub", level_1_sort=1, level_2_sort=2,
                          level_3_sort=3, level_4_sort=4))
        out = MS.suggest_account_mapping([{"gl_account_id": "1"}], lib)
        p = out["proposals"][0]
        for f in MS.PROPOSAL_FIELDS:
            assert f in p

    def test_duplicate_library_key_first_wins(self):
        lib = _lib(
            _entry(gid="7", name="Dup", level_2="FIRST"),
            _entry(gid="7", name="Dup", level_2="SECOND"),
        )
        out = MS.suggest_account_mapping([{"gl_account_id": "7"}], lib)
        assert out["proposals"][0]["level_2"] == "FIRST"


# --------------------------------------------------------------------------- #
# Robustness
# --------------------------------------------------------------------------- #
class TestRobustness:
    def test_empty_library(self):
        out = MS.suggest_account_mapping([{"gl_account_id": "1", "account_name": "x"}], {})
        assert out["proposals"] == []
        assert len(out["unmatched"]) == 1

    def test_empty_accounts(self):
        lib = _lib(_entry(gid="1", name="x"))
        out = MS.suggest_account_mapping([], lib)
        assert out == {"proposals": [], "unmatched": []}

    def test_dot_zero_artifact_matches(self):
        lib = _lib(_entry(gid="38235", name="Konto", level_0="PL"))
        out = MS.suggest_account_mapping([{"gl_account_id": "38235.0"}], lib)
        assert out["proposals"][0]["match"] == "gl_account_id"
