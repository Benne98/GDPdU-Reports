"""Phase 2 forensic-targeting tests — fewer, focused findings.

DB-free / monkeypatched (mirrors test_gl_forensic*.py).  Covers the four targeting
levers and the Report-View content:

  1. Whole-word keyword matching   — "storno" yes; "protest" / "testing" → no "test".
  2. Materiality floor (>= 25 kEUR) — sub-floor novelty / suspicious / rollup rows dropped.
  3. Tighter "Other"               — needs material AND big absolute move AND >= 20% growth;
                                      a 21%-growth-but-immaterial position is dropped.
  4. Top-N cap + omitted_count     — each section capped to MAX_FORENSIC_PER_SECTION.
  5. Report-View                   — headline + bullets present per section.

These are the load-bearing financial-logic tests (CLAUDE.md rule #1) for the new
thresholds; they never touch a database.
"""
from __future__ import annotations

import pytest

from app.services.gl_forensic import (
    MAX_FORENSIC_PER_SECTION,
    MIN_FLAG_AMOUNT_KEUR_DEFAULT,
    OTHER_DELTA_MULTIPLE,
    OTHER_GROWTH_PCT_THRESHOLD,
    filter_benign_counters,
    is_growing_other,
    match_benign_counter,
    match_other_token,
    match_text_keyword,
)
from app.services.fin_compat_narrative_core import MOM_FLOOR_EUR, SIZE_FLOOR_EUR
from app.services.gl_forensic_positions import (
    MAX_FORENSIC_PER_SECTION as POS_MAX_PER_SECTION,
    MIN_FLAG_AMOUNT_KEUR,
    cap_section,
    counter_section_report,
    other_section_report,
    rollup_counter_positions,
    suspicious_section_report,
)


# =========================================================================== #
# 1) Whole-word keyword matching (the headline bug: "test" matched "protest")
# =========================================================================== #
def test_text_keyword_storno_matches_as_word():
    assert match_text_keyword("Storno gebucht") == "storno"
    assert match_text_keyword("storno") == "storno"


def test_text_keyword_test_no_longer_matches_protest_or_testing():
    # the whole point of Phase 2: substring matches are gone
    assert match_text_keyword("protest march costs") is None
    assert match_text_keyword("testing the new line") is None
    assert match_text_keyword("contestant fees") is None
    # but a standalone "test" still flags
    assert match_text_keyword("TEST booking") == "test"
    assert match_text_keyword("a quick test entry") == "test"


def test_text_keyword_umlaut_tokens_match():
    assert match_text_keyword("Vorläufige Buchung") == "vorläufig"
    assert match_text_keyword("Rückbuchung Bank") == "rückbuchung"


def test_other_token_word_boundary():
    # "other" as a word matches; embedded in a larger word it does not
    assert match_other_token("Other operating income", None, None, None) == "other"
    assert match_other_token("Brotherhood fees", None, None, None) is None
    # longest-token-wins: "miscellaneous" beats the "misc" prefix token
    assert match_other_token("Miscellaneous costs", None, None, None) == "miscellaneous"


# =========================================================================== #
# 2) Materiality floor (>= 25 kEUR) on novelty rollups + suspicious rows
# =========================================================================== #
def _flag(line_id, *, amount, novelty="new", ep="01", l3="Other operating income"):
    return {
        "booking_line_id": line_id,
        "journal_entry_number": f"TX{line_id:07d}",
        "gl_account_id": "8400",
        "account_name": "Revenue",
        "counter_gl_account_id": f"C{line_id}",
        "counter_account_name": f"Counter {line_id}",
        "amount_keur": amount,
        "posting_date": "2025-07-15",
        "line_note": "",
        "pair_freq": 0 if novelty == "new" else 3,
        "freq_pct": 0.0 if novelty == "new" else 0.03,
        "novelty": novelty,
        "entity_prefix": ep,
        "account_number_group": "01ACC",
        "level_0": "PL",
        "level_2": "Operating income",
        "level_3": l3,
        "level_4": "",
    }


def test_floor_default_is_25_keur():
    assert MIN_FLAG_AMOUNT_KEUR_DEFAULT == 25.0
    assert MIN_FLAG_AMOUNT_KEUR == 25.0


def test_rollup_drops_subfloor_novelty_rows():
    rows = [
        _flag(1, amount=30.0),   # >= 25 → kept
        _flag(2, amount=10.0),   # < 25  → dropped
        _flag(3, amount=-24.999),  # just below floor → dropped
        _flag(4, amount=-25.0),  # exactly at floor → kept (>=)
    ]
    out = rollup_counter_positions(rows)  # default floor = 25
    assert len(out) == 1
    node = out[0]
    assert node["flag_count"] == 2  # only the 30 and the -25
    assert node["abs_amount_keur"] == pytest.approx(55.0)


def test_rollup_subfloor_only_yields_no_positions():
    rows = [_flag(i, amount=float(i)) for i in range(1, 11)]  # all < 25
    assert rollup_counter_positions(rows) == []


# =========================================================================== #
# 3) Tighter "Other": material AND big absolute move AND >= 20% growth
# =========================================================================== #
def test_other_needs_material_and_big_delta_and_growth():
    size_k = SIZE_FLOOR_EUR / 1000.0
    delta_k = (MOM_FLOOR_EUR * OTHER_DELTA_MULTIPLE) / 1000.0
    # all three satisfied → flagged
    assert is_growing_other(size_k, delta_k, OTHER_GROWTH_PCT_THRESHOLD) is True


def test_other_21pct_growth_but_immaterial_is_dropped():
    # 21% growth (> 20% bar) but |cm| below the size floor → NOT flagged
    below_size_k = (SIZE_FLOOR_EUR / 1000.0) - 1.0
    delta_k = (MOM_FLOOR_EUR * OTHER_DELTA_MULTIPLE) / 1000.0
    assert is_growing_other(below_size_k, delta_k, 21.0) is False


def test_other_material_and_growing_pct_but_small_absolute_move_dropped():
    # material + 50% growth, but the absolute move is below the raised delta bar
    size_k = SIZE_FLOOR_EUR / 1000.0
    small_delta_k = ((MOM_FLOOR_EUR * OTHER_DELTA_MULTIPLE) / 1000.0) - 0.001
    assert is_growing_other(size_k, small_delta_k, 50.0) is False


def test_other_growth_pct_bar_is_20():
    assert OTHER_GROWTH_PCT_THRESHOLD == 20.0
    size_k = SIZE_FLOOR_EUR / 1000.0
    delta_k = (MOM_FLOOR_EUR * OTHER_DELTA_MULTIPLE) / 1000.0
    assert is_growing_other(size_k, delta_k, 19.99) is False
    assert is_growing_other(size_k, delta_k, 20.0) is True


# =========================================================================== #
# 4) Top-N cap + omitted_count
# =========================================================================== #
def test_cap_section_caps_to_top_n_and_counts_omitted():
    rows = [{"i": i} for i in range(10)]
    kept, omitted = cap_section(rows, max_rows=6)
    assert len(kept) == 6
    assert omitted == 4
    assert kept == rows[:6]  # order preserved (already sorted by importance)


def test_cap_section_under_cap_no_omission():
    rows = [{"i": i} for i in range(3)]
    kept, omitted = cap_section(rows, max_rows=6)
    assert kept == rows
    assert omitted == 0


def test_cap_section_default_is_six():
    assert MAX_FORENSIC_PER_SECTION == 6
    assert POS_MAX_PER_SECTION == 6
    rows = [{"i": i} for i in range(20)]
    kept, omitted = cap_section(rows)
    assert len(kept) == 6
    assert omitted == 14


def test_cap_section_empty():
    assert cap_section([]) == ([], 0)


# =========================================================================== #
# 5) Report-View: headline + bullets present per section
# =========================================================================== #
def _counter_node(position, abs_amt, flag_count=2, distinct=2):
    return {
        "position": position,
        "abs_amount_keur": abs_amt,
        "flag_count": flag_count,
        "distinct_counter_accounts": distinct,
    }


def test_counter_report_has_headline_and_bullets():
    rows = [_counter_node("Other operating income", 120.0)]
    rv = counter_section_report(rows, omitted=0)
    assert isinstance(rv["headline"], str) and rv["headline"]
    assert isinstance(rv["bullets"], list) and 2 <= len(rv["bullets"]) <= 5
    assert all(isinstance(b, str) and b for b in rv["bullets"])
    assert "Other operating income" in " ".join(rv["bullets"])


def test_counter_report_mentions_omitted():
    rows = [_counter_node("P", 120.0)]
    rv = counter_section_report(rows, omitted=7)
    assert any("+7 more" in b for b in rv["bullets"])


def test_counter_report_empty_section():
    rv = counter_section_report([], omitted=0)
    assert rv["headline"]
    assert rv["bullets"]


def test_other_report_has_headline_and_bullets():
    rows = [{
        "position": "Other costs", "balance_cm_keur": 120.0,
        "delta_keur": 70.0, "growth_pct": 25.0,
    }]
    rv = other_section_report(rows, omitted=2)
    assert rv["headline"]
    assert 2 <= len(rv["bullets"]) <= 5
    assert "Other costs" in " ".join(rv["bullets"])
    assert any("+2 more" in b for b in rv["bullets"])


def test_suspicious_report_has_headline_and_bullets():
    rows = [{
        "account_name": "Bank", "amount_keur": 80.0, "matched_keyword": "storno",
    }]
    rv = suspicious_section_report(rows, omitted=0)
    assert rv["headline"]
    assert 2 <= len(rv["bullets"]) <= 5
    assert any("storno" in b.lower() for b in rv["bullets"])


# =========================================================================== #
# 6) End-to-end orchestrator (monkeypatched — NEVER runs the live learner):
#    cap + omitted_count + report_views land in the payload, version bumped.
# =========================================================================== #
def test_build_forensic_positions_caps_and_reports(monkeypatch):
    from unittest.mock import MagicMock

    import app.services.gl_forensic_positions as gp
    from app.services.gl_analysis_common import AccountSeries

    # one account per distinct L4 so we get many counter positions to cap
    accs = [
        AccountSeries(
            gl_account_id=f"84{i:02d}", account_name=f"Other income {i}",
            account_number_group=f"01ACC{i}", entity_prefix="01", level_0="PL",
            level_2="Operating income", level_3="Other operating income",
            level_4=f"Sub {i}", l4_sub="",
        )
        for i in range(10)
    ]
    monkeypatch.setattr(gp, "build_account_monthly_series", lambda *a, **k: accs)
    monkeypatch.setattr(
        gp, "material_account_groups", lambda *a, **k: [a.account_number_group for a in accs]
    )
    monkeypatch.setattr(gp, "latest_anchor", lambda *a, **k: (2025, 7))

    # 10 material novelty flags, one per L4 → 10 counter positions, capped to 6
    flags = [{
        "booking_line_id": i, "journal_entry_number": f"TX{i}", "gl_account_id": f"84{i:02d}",
        "account_name": f"Other income {i}", "counter_gl_account_id": f"C{i}",
        "counter_account_name": f"Counter {i}", "amount_keur": 100.0 + i,
        "posting_date": "2025-07-15", "line_note": "", "pair_freq": 0,
        "freq_pct": 0.0, "novelty": "new",
    } for i in range(10)]
    monkeypatch.setattr(gp, "_build_unexpected_counter_accounts", lambda *a, **k: flags)
    monkeypatch.setattr(gp, "_bookings_for_lines", lambda *a, **k: [
        {"booking_line_id": i, "entity_prefix": "01", "acct_ang": f"01ACC{i}"}
        for i in range(10)
    ])
    monkeypatch.setattr(gp, "_build_other_positions", lambda *a, **k: [])
    monkeypatch.setattr(gp, "_build_suspicious_texts", lambda *a, **k: [])

    out = gp.build_forensic_positions(MagicMock(), entity_prefixes=["01"])

    # capped to the top 6, with 4 omitted material findings carried in meta
    assert len(out["unexpected_counter_positions"]) == MAX_FORENSIC_PER_SECTION
    assert out["meta"]["omitted_counts"]["unexpected_counter_positions"] == 4
    rv = out["meta"]["report_views"]["unexpected_counter_positions"]
    assert rv["headline"] and rv["bullets"]
    assert rv["omitted_count"] == 4
    # largest stays on top (amount 109 → "Sub 9")
    assert out["unexpected_counter_positions"][0]["level_4"] == "Sub 9"
    # version bumped so the snapshot cache invalidates
    assert out["meta"]["algorithm_version"] == "gl_forensic_positions_v3"
    assert out["meta"]["min_flag_amount_keur"] == 25.0
    assert out["meta"]["max_per_section"] == 6
    # benignity-filter meta: heuristic-only by default, no benign drops in this fixture
    assert out["meta"]["llm_used"] is False
    assert out["meta"]["omitted_benign_count"] == 0


# =========================================================================== #
# 7) Benignity filter (anomaly refinement): heuristic drops system/control
#    counter-accounts, keeps genuinely-odd ones; LLM gated off by default.
# =========================================================================== #
def _counter_flag(line_id, *, counter_name, level_3="Revenue", level_4="", novelty="new"):
    return {
        "booking_line_id": line_id,
        "account_name": "Revenue",
        "counter_gl_account_id": f"C{line_id}",
        "counter_account_name": counter_name,
        "amount_keur": 100.0,
        "novelty": novelty,
        "level_0": "PL", "level_2": "Operating income",
        "level_3": level_3, "level_4": level_4,
    }


def test_benign_token_matches_system_accounts_as_words():
    # earliest matching token in the string wins ("Tax" precedes "clearing")
    assert match_benign_counter("Tax clearing account", None, None) == "tax"
    assert match_benign_counter("Clearing account", None, None) == "clearing"
    assert match_benign_counter("USt-Verrechnungskonto", None, None) is not None
    assert match_benign_counter("Payroll liabilities", None, None) == "payroll"
    assert match_benign_counter("Intercompany receivable", None, None) == "intercompany"
    # a genuinely-odd counter name matches no benign token
    assert match_benign_counter("Owner private withdrawal", None, None) is None
    # whole-word: a token embedded in a larger word does not fire
    assert match_benign_counter("Untaxed sample", None, None) is None


def test_benign_token_matches_on_hierarchy_levels_too():
    # the counter name is neutral, but the position hierarchy marks it as a tax account
    assert match_benign_counter("Misc account", "Umsatzsteuer", "VAT payable") is not None


def test_filter_drops_clearing_and_tax_keeps_odd_counter():
    rows = [
        _counter_flag(1, counter_name="Clearing account"),          # clearing → dropped
        _counter_flag(2, counter_name="USt payable"),               # ust → dropped
        _counter_flag(3, counter_name="Owner private withdrawal"),  # odd → kept
    ]
    kept, dropped = filter_benign_counters(rows, use_llm=False)
    assert [r["booking_line_id"] for r in kept] == [3]
    assert {r["booking_line_id"] for r in dropped} == {1, 2}
    # dropped rows carry the matched token so callers can audit/count
    assert all(r.get("benign_reason") for r in dropped)
    assert dropped[0]["benign_reason"] == "clearing"


def test_filter_keeps_all_when_none_benign():
    rows = [
        _counter_flag(1, counter_name="Owner private withdrawal"),
        _counter_flag(2, counter_name="Shareholder loan"),
    ]
    kept, dropped = filter_benign_counters(rows, use_llm=False)
    assert len(kept) == 2 and dropped == []


def test_filter_llm_path_not_invoked_when_flag_off(monkeypatch):
    # use_llm=False → the LLM helper is NEVER called, even if a key were present.
    import app.services.gl_forensic as gf

    def _boom(*a, **k):
        raise AssertionError("LLM must not be called when use_llm is False")

    monkeypatch.setattr(gf, "_llm_counter_is_benign", _boom)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-matter")
    rows = [_counter_flag(1, counter_name="Owner private withdrawal")]
    kept, dropped = filter_benign_counters(rows, use_llm=False)
    assert len(kept) == 1 and dropped == []


def test_filter_llm_not_invoked_without_api_key(monkeypatch):
    # use_llm=True but no key → LLM helper NEVER called (gate requires the key).
    import app.services.gl_forensic as gf

    def _boom(*a, **k):
        raise AssertionError("LLM must not be called without ANTHROPIC_API_KEY")

    monkeypatch.setattr(gf, "_llm_counter_is_benign", _boom)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rows = [_counter_flag(1, counter_name="Owner private withdrawal")]
    kept, dropped = filter_benign_counters(rows, use_llm=True)
    assert len(kept) == 1 and dropped == []


def test_filter_llm_failure_is_fail_closed(monkeypatch):
    # use_llm=True + key set: a raising LLM keeps the row (fail-closed), never crashes.
    import app.services.gl_forensic as gf

    monkeypatch.setattr(
        gf, "_llm_counter_is_benign",
        lambda **k: (_ for _ in ()).throw(RuntimeError("api boom")),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    rows = [_counter_flag(1, counter_name="Owner private withdrawal")]
    kept, dropped = filter_benign_counters(rows, use_llm=True)
    assert len(kept) == 1 and dropped == []


def test_filter_llm_drops_when_benign(monkeypatch):
    # use_llm=True + key set: a benign verdict drops the row with reason "llm".
    import app.services.gl_forensic as gf

    monkeypatch.setattr(gf, "_llm_counter_is_benign", lambda **k: True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    rows = [_counter_flag(1, counter_name="Owner private withdrawal")]
    kept, dropped = filter_benign_counters(rows, use_llm=True)
    assert kept == []
    assert len(dropped) == 1 and dropped[0]["benign_reason"] == "llm"


def test_forensic_use_llm_default_false():
    from app.config import settings
    assert settings.forensic_use_llm is False
