"""Tests for the GL forensic data layer (Journal Agent, Phase 3).

Two layers:
  * DB-FREE unit tests on the pure functions (freq_pct math, new-vs-rare at the
    threshold boundary, top-1 counter selection, the end-to-end novelty
    classification incl. multi-leg, opposite-sign, exclude-current-period and
    synthetic-row reasoning expressed through the inputs).  These are the
    load-bearing financial-logic tests (CLAUDE.md rule #1) and always run.
  * A Postgres integration test (auto-SKIPPED when finssentials_v2 is not
    reachable) that exercises the actual SQL — the sibling self-join, opposite-sign
    filter, synthetic exclusion via the entry join, and the batched (non-N+1)
    derivation — because SQLite lacks SIGN()/COUNT(DISTINCT tuple)/window
    PARTITION BY/ANY(array) used by the production queries.

FINANCIAL LOGIC UNDER TEST (freq_pct + novelty)
-----------------------------------------------
    freq_pct(A,B)  = pair_freq(A,B) / acct_total_pairs(A)        # 0 if total 0
    novelty:  "new"  ⇔ pair_freq == 0
              "rare" ⇔ 0 < freq_pct < threshold (default 0.05)
              None   ⇔ freq_pct >= threshold (common)

Worked example: A's counters B1=80, B2=15, B3=5 → total 100 →
    freq_pct B1=0.80, B2=0.15, B3=0.05.  A booking to B3 (0.05) is NOT rare
    (boundary inclusive on the common side); to B-at-0.04 IS rare; to an unseen
    B4 is "new".
"""
from __future__ import annotations

import os

import pytest

from app.services.gl_forensic import (
    FREQ_THRESHOLD_DEFAULT,
    OTHER_GROWTH_PCT_THRESHOLD,
    build_level_path,
    classify_novelty,
    classify_pair_novelty,
    compute_freq_pct,
    cooccurrence_index,
    acct_total_index,
    is_growing_other,
    match_other_token,
    match_text_keyword,
    pick_top_counter,
)
from app.services.fin_compat_narrative_core import MOM_FLOOR_EUR, SIZE_FLOOR_EUR


# --------------------------------------------------------------------------- #
# compute_freq_pct
# --------------------------------------------------------------------------- #
def test_freq_pct_basic_math():
    assert compute_freq_pct(80, 100) == pytest.approx(0.80)
    assert compute_freq_pct(15, 100) == pytest.approx(0.15)
    assert compute_freq_pct(5, 100) == pytest.approx(0.05)


def test_freq_pct_zero_total_no_div_by_zero():
    assert compute_freq_pct(0, 0) == 0.0
    assert compute_freq_pct(7, 0) == 0.0  # defensive: never raises


# --------------------------------------------------------------------------- #
# classify_pair_novelty — the new-vs-rare boundary
# --------------------------------------------------------------------------- #
def test_novelty_new_when_pair_freq_zero():
    # never co-occurred → "new" regardless of freq_pct value
    assert classify_pair_novelty(0, 0.0) == "new"
    assert classify_pair_novelty(0, 0.99) == "new"  # freq_pct irrelevant when freq=0


def test_novelty_rare_below_threshold():
    # 0.04 < 0.05 (default) → rare
    assert classify_pair_novelty(4, 0.04) == "rare"


def test_novelty_boundary_exactly_threshold_is_common():
    # freq_pct == threshold → common (rare uses strict <)
    assert classify_pair_novelty(5, 0.05) is None
    # just below the boundary → rare
    assert classify_pair_novelty(5, 0.04999) == "rare"


def test_novelty_common_above_threshold():
    assert classify_pair_novelty(80, 0.80) is None
    assert classify_pair_novelty(15, 0.15) is None


def test_novelty_custom_threshold():
    # raise the threshold to 0.20 → 0.15 now counts as rare
    assert classify_pair_novelty(15, 0.15, freq_threshold=0.20) == "rare"
    assert classify_pair_novelty(15, 0.20, freq_threshold=0.20) is None


# --------------------------------------------------------------------------- #
# pick_top_counter — multi-leg top-1 selection
# --------------------------------------------------------------------------- #
def test_pick_top_counter_by_weight():
    counters = [("01000200", 300.0), ("01000300", 700.0), ("01000400", 50.0)]
    assert pick_top_counter(counters) == ("01000300", 700.0)


def test_pick_top_counter_tie_break_on_ang():
    # equal weight → smaller account_number_group wins (deterministic)
    counters = [("01000900", 500.0), ("01000100", 500.0)]
    assert pick_top_counter(counters) == ("01000100", 500.0)


def test_pick_top_counter_empty():
    assert pick_top_counter([]) is None


# --------------------------------------------------------------------------- #
# Index helpers
# --------------------------------------------------------------------------- #
def _cooc_rows():
    """Account 01REV (entity 01) historical counters B1=80, B2=15, B3=5."""
    return [
        {"entity_prefix": "01", "acct_ang": "01REV", "counter_ang": "01B1",
         "pair_freq": 80, "acct_total_pairs": 100, "freq_pct": 0.80},
        {"entity_prefix": "01", "acct_ang": "01REV", "counter_ang": "01B2",
         "pair_freq": 15, "acct_total_pairs": 100, "freq_pct": 0.15},
        {"entity_prefix": "01", "acct_ang": "01REV", "counter_ang": "01B3",
         "pair_freq": 5, "acct_total_pairs": 100, "freq_pct": 0.05},
    ]


def test_cooccurrence_index_keys():
    idx = cooccurrence_index(_cooc_rows())
    assert idx[("01", "01REV", "01B1")]["pair_freq"] == 80
    assert ("01", "01REV", "01B4") not in idx


def test_acct_total_index():
    tot = acct_total_index(_cooc_rows())
    assert tot[("01", "01REV")] == 100


# --------------------------------------------------------------------------- #
# classify_novelty — end-to-end pure (the worked example + edges)
# --------------------------------------------------------------------------- #
def _booking(line_id, acct="01REV", ep="01", note="", jegn="01TX0000001"):
    return {
        "booking_line_id": line_id,
        "journal_entry_group_number": jegn,
        "acct_ang": acct,
        "entity_prefix": ep,
        "amount_keur": 12.5,
        "line_note": note,
        "gl_account_id": "8400",
        "account_name": "Revenue",
        "posting_date": "2025-07-15",
    }


def _counter(line_id, ang):
    return {line_id: {
        "counter_account_number_group": ang,
        "counter_gl_account_id": f"gid-{ang}",
        "counter_account_name": f"name-{ang}",
        "weight": 999.0,
    }}


def test_classify_novelty_common_counter_dropped():
    # booking to B1 (freq_pct 0.80) → common → not returned
    out = classify_novelty([_booking(1)], _cooc_rows(), _counter(1, "01B1"))
    assert out == []


def test_classify_novelty_boundary_counter_dropped():
    # booking to B3 (freq_pct exactly 0.05) → common (boundary inclusive) → dropped
    out = classify_novelty([_booking(1)], _cooc_rows(), _counter(1, "01B3"))
    assert out == []


def test_classify_novelty_new_counter_flagged():
    # booking to B4 (never seen in history) → "new"
    out = classify_novelty([_booking(1)], _cooc_rows(), _counter(1, "01B4"))
    assert len(out) == 1
    row = out[0]
    assert row["novelty"] == "new"
    assert row["pair_freq"] == 0
    assert row["acct_total_pairs"] == 100  # falls back to the account total
    assert row["freq_pct"] == 0.0
    assert row["counter_gl_account_id"] == "gid-01B4"


def test_classify_novelty_rare_counter_flagged():
    # add a rare historical counter at 0.04 and book to it
    rows = _cooc_rows() + [
        {"entity_prefix": "01", "acct_ang": "01REV", "counter_ang": "01RARE",
         "pair_freq": 4, "acct_total_pairs": 100, "freq_pct": 0.04},
    ]
    out = classify_novelty([_booking(1)], rows, _counter(1, "01RARE"))
    assert len(out) == 1
    assert out[0]["novelty"] == "rare"
    assert out[0]["freq_pct"] == pytest.approx(0.04)


def test_classify_novelty_no_counter_dropped():
    # a booking with no derivable counter (e.g. single-leg) is skipped
    out = classify_novelty([_booking(1)], _cooc_rows(), {})
    assert out == []


def test_classify_novelty_journal_entry_number_strips_prefix():
    # journal_entry_group_number = entity_prefix(2) || journal_entry_number
    out = classify_novelty(
        [_booking(1, jegn="01TX0000099")], _cooc_rows(), _counter(1, "01B4"),
    )
    assert out[0]["journal_entry_number"] == "TX0000099"


def test_classify_novelty_exclude_period_makes_counter_new():
    # Simulate exclude-current-period: the learner ran with the analysed period
    # removed, so a counter that only appeared in the analysed period is absent
    # from the learned rows → classified "new" (can't make itself common).
    learned_excl_period = _cooc_rows()  # 01B5 NOT present (only seen this period)
    out = classify_novelty([_booking(1)], learned_excl_period, _counter(1, "01B5"))
    assert len(out) == 1
    assert out[0]["novelty"] == "new"


def test_freq_threshold_default_value():
    assert FREQ_THRESHOLD_DEFAULT == 0.05


# =========================================================================== #
# Phase 4 — Other-token matching (incl. umlauts)
# =========================================================================== #
def test_match_other_token_english():
    assert match_other_token("Other operating income", None, None, None) == "other"
    assert match_other_token(None, "Miscellaneous", None, None) == "miscellaneous"


def test_match_other_token_german_umlauts():
    # "übrige" must match on the umlaut variant; case-insensitive
    assert match_other_token("Übrige Aufwendungen", None, None, None) == "übrige"
    assert match_other_token(None, None, "SONSTIGE Erträge", None) == "sonstige"
    # ASCII fallbacks for the umlaut spellings are also covered tokens
    assert match_other_token("Uebrige Posten", None, None, None) == "ubrige" or \
        match_other_token("Uebrige Posten", None, None, None) == "uebrige"


def test_match_other_token_matches_any_level():
    # l4_sub carries the token while higher levels do not
    assert match_other_token("Revenue", "Sales", "Region", "diverse items") == "diverse"


def test_match_other_token_none_when_no_match():
    assert match_other_token("Revenue", "Materials", "Steel", "Bar") is None
    assert match_other_token(None, None, None, None) is None
    assert match_other_token("", "  ", None, "") is None


def test_match_other_token_substring():
    # token is a substring (e.g. "miscellaneous costs") and still matches
    assert match_other_token("Miscellaneous costs", None, None, None) == "miscellaneous"


def test_build_level_path_joins_non_null():
    assert build_level_path("PL", None, "Other", "") == "PL / Other"
    assert build_level_path(None, None, None, None) == ""
    assert build_level_path("  A  ", "B", None, "C") == "A / B / C"


# =========================================================================== #
# Phase 4 — suspicious-text keyword matching (DE + EN)
# =========================================================================== #
def test_match_text_keyword_german():
    assert match_text_keyword("Storno der Rechnung") == "storno"
    assert match_text_keyword("Manuelle Korrektur") in ("korrektur", "manuell")
    assert match_text_keyword("Rückbuchung Bank") == "rückbuchung"
    assert match_text_keyword("Vorläufige Buchung") == "vorläufig"


def test_match_text_keyword_english():
    assert match_text_keyword("Reversal of accrual") == "reversal"
    assert match_text_keyword("manual adjustment entry") in ("manual", "adjust")
    assert match_text_keyword("provisional posting") == "provisional"
    assert match_text_keyword("TEST booking") == "test"


def test_match_text_keyword_none():
    assert match_text_keyword("Regular sales invoice") is None
    assert match_text_keyword("") is None
    assert match_text_keyword(None) is None


def test_match_text_keyword_case_insensitive():
    assert match_text_keyword("UMBUCHUNG zwischen Konten") == "umbuchung"


# =========================================================================== #
# Phase 4 — growing-Other materiality boundary
# =========================================================================== #
# Phase 2: "Other" requires material AND a sizeable absolute move AND >=20% growth.
# Delta bar = MOM_FLOOR_EUR * OTHER_DELTA_MULTIPLE (default 30k * 2 = 60k).
from app.services.gl_forensic import OTHER_DELTA_MULTIPLE  # noqa: E402


def test_is_growing_other_material_and_growing_all_three():
    # cm above the size floor AND |delta| above the raised delta bar AND growth>=20%
    size_k = SIZE_FLOOR_EUR / 1000.0
    delta_k = (MOM_FLOOR_EUR * OTHER_DELTA_MULTIPLE) / 1000.0
    assert is_growing_other(size_k, delta_k, OTHER_GROWTH_PCT_THRESHOLD) is True


def test_is_growing_other_material_but_not_growing():
    # material in size but a tiny move and low growth_pct → NOT flagged
    size_k = SIZE_FLOOR_EUR / 1000.0
    assert is_growing_other(size_k, 0.0, 0.0) is False


def test_is_growing_other_growing_but_immaterial():
    # big mover but |cm| below the size floor → NOT flagged (needs material too)
    delta_k = (MOM_FLOOR_EUR * OTHER_DELTA_MULTIPLE) / 1000.0
    below_size_k = (SIZE_FLOOR_EUR / 1000.0) - 1.0
    assert is_growing_other(below_size_k, delta_k, 999.0) is False


def test_is_growing_other_big_delta_but_low_growth_pct_dropped():
    # Phase 2: AND now — a big absolute move that is a tiny % is NOT flagged
    size_k = SIZE_FLOOR_EUR / 1000.0
    delta_k = (MOM_FLOOR_EUR * OTHER_DELTA_MULTIPLE) / 1000.0
    assert is_growing_other(size_k, delta_k, OTHER_GROWTH_PCT_THRESHOLD - 0.01) is False


def test_is_growing_other_high_growth_pct_but_small_delta_dropped():
    # Phase 2: AND now — a big % on a small absolute move is NOT flagged
    size_k = SIZE_FLOOR_EUR / 1000.0
    small_delta_k = ((MOM_FLOOR_EUR * OTHER_DELTA_MULTIPLE) / 1000.0) - 0.001
    assert is_growing_other(size_k, small_delta_k, 999.0) is False


def test_is_growing_other_size_boundary_inclusive():
    # exactly AT the size floor counts as material (>=)
    size_k = SIZE_FLOOR_EUR / 1000.0
    delta_k = (MOM_FLOOR_EUR * OTHER_DELTA_MULTIPLE) / 1000.0
    assert is_growing_other(size_k, delta_k, OTHER_GROWTH_PCT_THRESHOLD) is True
    assert is_growing_other(size_k - 0.001, delta_k, OTHER_GROWTH_PCT_THRESHOLD) is False


# =========================================================================== #
# Phase 4 — endpoint wiring + pure-read
# =========================================================================== #
# The earlier ``TestForensicEndpoint`` class lived here.  It monkeypatched
# ``app.routers.financials_compat.build_forensic`` and exercised year/month/entity
# query params — but the ``/anomalies/forensic`` endpoint was rewired to the
# positions path (``anomaly_compute.get_forensic`` → ``build_forensic_positions``),
# takes NO params, and no longer imports/exposes ``build_forensic``.  Those tests
# therefore errored with AttributeError and are fully superseded by:
#   * ``test_anomaly_entity_auth.py::TestTreeEndpoints`` (parametrised over the
#     /anomalies/forensic route) — payload+cache_hit wiring, admin scope=None,
#     restricted-prefix scoping, zero-grant 403, and the generic-500 contract; and
#   * ``test_forensic_targeting.py`` — the rollup/report logic and
#     ``build_forensic_positions`` capping (monkeypatched).
# They were removed rather than re-pointed to avoid duplicating that coverage.


# --------------------------------------------------------------------------- #
# Postgres integration (SKIPPED when finssentials_v2 unreachable) — exercises
# the actual SQL: sibling self-join, opposite-sign, synthetic exclusion, batched
# derivation, and exclude-current-period at the query level.
# --------------------------------------------------------------------------- #
def _v2_session():
    """Return a Session on finssentials_v2, or None if unreachable."""
    os.environ.setdefault("DB_NAME", "finssentials_v2")
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        from app.config import settings
        url = settings.database_url
        if "finssentials_v2" not in url:
            url = url.rsplit("/", 1)[0] + "/finssentials_v2"
        eng = create_engine(url, connect_args={"connect_timeout": 3})
        s = Session(eng)
        s.execute  # touch
        from sqlalchemy import text
        s.execute(text("SELECT 1"))
        return s
    except Exception:
        return None


@pytest.fixture(scope="module")
def v2():
    s = _v2_session()
    if s is None:
        pytest.skip("finssentials_v2 not reachable")
    yield s
    s.close()


def test_pg_build_cooccurrence_shape_and_freq_pct(v2):
    from app.services.gl_forensic import build_cooccurrence
    rows = build_cooccurrence(v2, "01")
    assert rows, "expected co-occurrence pairs for entity 01"
    r = rows[0]
    assert set(r) >= {
        "entity_prefix", "acct_ang", "counter_ang", "pair_freq",
        "acct_total_pairs", "freq_pct",
    }
    # freq_pct reconciles with pair_freq / acct_total within each account
    for r in rows[:500]:
        if r["acct_total_pairs"]:
            assert r["freq_pct"] == pytest.approx(
                r["pair_freq"] / r["acct_total_pairs"], abs=1e-6
            )
        assert r["pair_freq"] >= 1  # every learned pair occurred at least once


def test_pg_acct_total_equals_sum_of_pairs(v2):
    from app.services.gl_forensic import build_cooccurrence
    rows = build_cooccurrence(v2, "01")
    by_acct: dict[str, list[dict]] = {}
    for r in rows:
        by_acct.setdefault(r["acct_ang"], []).append(r)
    # acct_total_pairs (window SUM) == Σ pair_freq of that account's counters
    for acct, prs in list(by_acct.items())[:200]:
        total = sum(p["pair_freq"] for p in prs)
        assert prs[0]["acct_total_pairs"] == total


def test_pg_exclude_period_reduces_or_equal(v2):
    from app.services.gl_forensic import build_cooccurrence
    full = build_cooccurrence(v2, "01")
    excl = build_cooccurrence(v2, "01", exclude_year=2025, exclude_period=7)
    full_map = {(r["acct_ang"], r["counter_ang"]): r["pair_freq"] for r in full}
    excl_map = {(r["acct_ang"], r["counter_ang"]): r["pair_freq"] for r in excl}
    # excluding a period can only lower (or keep) any pair's frequency, never raise
    for k, pf in excl_map.items():
        assert pf <= full_map.get(k, 0)


def test_pg_derive_counter_accounts_batched(v2):
    from sqlalchemy import text
    from app.services.gl_forensic import derive_counter_accounts
    # pick some real anchor lines that belong to multi-line entries
    ids = [int(r[0]) for r in v2.execute(text(
        """
        SELECT l.booking_line_id
        FROM fact_gl_line l
        JOIN (
            SELECT journal_entry_group_number, fiscal_year
            FROM fact_gl_line
            GROUP BY journal_entry_group_number, fiscal_year
            HAVING COUNT(*) >= 2
            LIMIT 50
        ) m ON m.journal_entry_group_number = l.journal_entry_group_number
           AND m.fiscal_year = l.fiscal_year
        WHERE l.entity_prefix = '01' AND l.amount <> 0
        LIMIT 50
        """
    )).fetchall()]
    if not ids:
        pytest.skip("no multi-line entries found")
    res = derive_counter_accounts(v2, ids)
    # at least some anchors have an opposite-side counter
    assert res, "expected at least one derived counter account"
    for line_id, c in res.items():
        assert c["counter_account_number_group"]
        assert c["weight"] >= 0.0
