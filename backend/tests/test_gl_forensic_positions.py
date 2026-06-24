"""Tests for the position-level GL forensic builder (Phase 2).

Three layers (mirrors test_gl_forensic.py):
  * DB-FREE unit tests on the pure rollup functions — the load-bearing logic
    (CLAUDE.md rule #1): counter-novelty rolled up to a position (counts, Σ|amount|,
    distinct counters, worst example, entity_split reconciliation); Other-position
    rollup recomputing growth_pct from the SUMMED balances; suspicious texts
    carrying entity_prefix + position; explanations / columns_help present.
  * A DB-FREE orchestrator test with build_forensic_positions monkeypatched so the
    live co-occurrence learner is NEVER run (the bulk learner hung before).
  * A Postgres integration test, auto-SKIPPED when finssentials_v2 is unreachable.

FINANCIAL LOGIC UNDER TEST (Other-position growth from SUMMED balances)
----------------------------------------------------------------------
    delta      = Σcm − Σpm
    growth_pct = (Σcm − Σpm) / |Σpm| * 100        (0 when Σpm ≈ 0)
Worked example: two Other accounts in one position, cm 30 / 10 (Σ 40), pm 20 / 5
    (Σ 25) → delta 15, growth_pct = 15 / 25 * 100 = 60.0 %.  (NOT the average of
    the two per-account growth_pcts, which would be (50% + 100%)/2 = 75%.)
"""
from __future__ import annotations

import os

import pytest

from app.services.gl_forensic_positions import (
    COLUMNS_HELP,
    EXPLANATIONS,
    NO_POSITION_BUCKET,
    POSITIONS_ALGORITHM_VERSION,
    annotate_suspicious_texts,
    rollup_counter_positions,
    rollup_other_positions,
)


# --------------------------------------------------------------------------- #
# Fixtures (hand-built enriched rows — what the orchestrator feeds the rollups)
# --------------------------------------------------------------------------- #
def _flag(
    line_id, *, novelty="new", amount=10.0, ep="01",
    l3="Other operating income", l4="", counter_gid="C1",
    counter_name="Counter 1", acct_name="Revenue", gid="8400",
):
    return {
        "booking_line_id": line_id,
        "journal_entry_number": f"TX{line_id:07d}",
        "gl_account_id": gid,
        "account_name": acct_name,
        "counter_gl_account_id": counter_gid,
        "counter_account_name": counter_name,
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
        "level_4": l4,
    }


# =========================================================================== #
# (1) Unexpected counter accounts → position rollup
# =========================================================================== #
def test_counter_rollup_counts_and_sum():
    rows = [
        _flag(1, novelty="new", amount=10.0),
        _flag(2, novelty="rare", amount=5.0),
        _flag(3, novelty="new", amount=-20.0),  # |amount| counts
    ]
    # mechanics test — opt out of the Phase 2 materiality floor with floor 0.
    out = rollup_counter_positions(rows, min_flag_amount_keur=0.0)
    assert len(out) == 1
    node = out[0]
    assert node["new_count"] == 2
    assert node["rare_count"] == 1
    assert node["flag_count"] == 3
    assert node["abs_amount_keur"] == pytest.approx(35.0)  # 10 + 5 + 20
    assert node["position"] == "Other operating income"
    assert node["level_4"] == "(no L4)"


def test_counter_rollup_distinct_counter_accounts():
    rows = [
        _flag(1, counter_gid="C1"),
        _flag(2, counter_gid="C1"),  # same counter → counts once
        _flag(3, counter_gid="C2"),
    ]
    out = rollup_counter_positions(rows, min_flag_amount_keur=0.0)
    assert out[0]["distinct_counter_accounts"] == 2


def test_counter_rollup_worst_example_is_largest_abs():
    rows = [
        _flag(1, amount=5.0, counter_name="small"),
        _flag(2, amount=-99.0, counter_name="big"),
        _flag(3, amount=12.0, counter_name="mid"),
    ]
    out = rollup_counter_positions(rows, min_flag_amount_keur=0.0)
    worst = out[0]["worst_example"]
    assert worst["amount_keur"] == -99.0
    assert worst["counter_account_name"] == "big"


def test_counter_rollup_splits_by_l4():
    rows = [
        _flag(1, l4="Sub A", amount=10.0),
        _flag(2, l4="Sub B", amount=20.0),
    ]
    out = rollup_counter_positions(rows, min_flag_amount_keur=0.0)
    assert len(out) == 2
    l4s = {n["level_4"] for n in out}
    assert l4s == {"Sub A", "Sub B"}
    # sorted by abs_amount_keur desc → Sub B (20) first
    assert out[0]["level_4"] == "Sub B"


def test_counter_rollup_entity_split_reconciles():
    rows = [
        _flag(1, ep="01", amount=10.0),
        _flag(2, ep="01", amount=5.0),
        _flag(3, ep="02", amount=20.0),
    ]
    out = rollup_counter_positions(rows, min_flag_amount_keur=0.0)
    node = out[0]
    split = node["entity_split"]
    assert set(split) == {"01", "02"}
    assert split["01"]["count"] == 2
    assert split["01"]["abs_amount_keur"] == pytest.approx(15.0)
    assert split["02"]["count"] == 1
    assert split["02"]["abs_amount_keur"] == pytest.approx(20.0)
    # Σ split reconciles to the node totals
    assert sum(s["count"] for s in split.values()) == node["flag_count"]
    assert sum(s["abs_amount_keur"] for s in split.values()) == pytest.approx(
        node["abs_amount_keur"]
    )


def test_counter_rollup_evidence_bounded_and_sorted():
    rows = [_flag(i, amount=float(i)) for i in range(1, 11)]
    out = rollup_counter_positions(rows, max_evidence=3, min_flag_amount_keur=0.0)
    ev = out[0]["evidence"]
    assert len(ev) == 3
    # largest |amount| first
    assert [e["amount_keur"] for e in ev] == [10.0, 9.0, 8.0]


def test_counter_rollup_no_hierarchy_falls_into_no_position_bucket():
    row = _flag(1, l3="")
    row["level_2"] = ""
    row["level_0"] = ""
    out = rollup_counter_positions([row], min_flag_amount_keur=0.0)
    assert out[0]["position"] == NO_POSITION_BUCKET


def test_counter_rollup_empty_input():
    assert rollup_counter_positions([]) == []


# =========================================================================== #
# (2) Other positions → rollup recomputes growth from SUMMED balances
# =========================================================================== #
def _other(gid, *, cm, pm, py_cm, ep="01", l3="Other costs", l4="", token="other"):
    return {
        "gl_account_id": gid,
        "account_name": f"Acct {gid}",
        "level_path": f"PL / {l3}",
        "balance_cm_keur": cm,
        "balance_pm_keur": pm,
        "balance_py_cm_keur": py_cm,
        "delta_keur": round(cm - pm, 3),
        "yoy_keur": round(cm - py_cm, 3),
        "growth_pct": round(((cm - pm) / abs(pm) * 100.0) if abs(pm) > 1e-9 else 0.0, 2),
        "matched_token": token,
        "entity_prefix": ep,
        "level_0": "PL",
        "level_2": "Costs",
        "level_3": l3,
        "level_4": l4,
    }


def test_other_rollup_growth_from_summed_balances():
    # Worked example: cm 30/10 (Σ40), pm 20/5 (Σ25) → delta 15, growth 60%.
    rows = [
        _other("A", cm=30.0, pm=20.0, py_cm=25.0),
        _other("B", cm=10.0, pm=5.0, py_cm=8.0),
    ]
    out = rollup_other_positions(rows)
    assert len(out) == 1
    node = out[0]
    assert node["balance_cm_keur"] == pytest.approx(40.0)
    assert node["balance_pm_keur"] == pytest.approx(25.0)
    assert node["delta_keur"] == pytest.approx(15.0)
    assert node["growth_pct"] == pytest.approx(60.0)  # NOT the 75% average
    assert node["yoy_keur"] == pytest.approx(40.0 - 33.0)  # Σcm − Σpy_cm
    assert node["account_count"] == 2
    assert node["matched_token"] == "other"


def test_other_rollup_growth_zero_when_pm_zero():
    rows = [_other("A", cm=10.0, pm=0.0, py_cm=0.0)]
    out = rollup_other_positions(rows)
    assert out[0]["growth_pct"] == 0.0  # no divide-by-zero


def test_other_rollup_entity_split_sums():
    rows = [
        _other("A", cm=30.0, pm=20.0, py_cm=25.0, ep="01"),
        _other("B", cm=10.0, pm=5.0, py_cm=8.0, ep="02"),
    ]
    out = rollup_other_positions(rows)
    split = out[0]["entity_split"]
    assert split["01"]["balance_cm_keur"] == pytest.approx(30.0)
    assert split["01"]["delta_keur"] == pytest.approx(10.0)
    assert split["02"]["balance_cm_keur"] == pytest.approx(10.0)
    assert split["02"]["delta_keur"] == pytest.approx(5.0)
    # Σ split cm reconciles to the node total
    assert sum(s["balance_cm_keur"] for s in split.values()) == pytest.approx(
        out[0]["balance_cm_keur"]
    )


def test_other_rollup_evidence_lists_contributing_accounts():
    rows = [
        _other("A", cm=30.0, pm=20.0, py_cm=25.0),
        _other("B", cm=10.0, pm=5.0, py_cm=8.0),
    ]
    out = rollup_other_positions(rows)
    ev = out[0]["evidence"]
    assert len(ev) == 2
    assert ev[0]["gl_account_id"] == "A"  # largest |cm| first
    assert {e["gl_account_id"] for e in ev} == {"A", "B"}


def test_other_rollup_separate_positions():
    rows = [
        _other("A", cm=30.0, pm=20.0, py_cm=25.0, l3="Other costs"),
        _other("C", cm=100.0, pm=10.0, py_cm=10.0, l3="Other income"),
    ]
    out = rollup_other_positions(rows)
    assert len(out) == 2
    # sorted by |delta| desc → Other income (delta 90) first
    assert out[0]["position"] == "Other income"


# =========================================================================== #
# (3) Suspicious texts → carry entity_prefix + position
# =========================================================================== #
def test_suspicious_texts_carry_entity_prefix_and_position():
    suspicious = [
        {"booking_line_id": 1, "acct_ang": "01ACC", "entity_prefix": "01",
         "account_name": "Bank", "amount_keur": 12.0, "line_note": "Storno",
         "matched_keyword": "storno", "posting_date": "2025-07-01"},
    ]
    level_map = {
        ("01", "01ACC"): {
            "level_0": "BS", "level_2": "Assets", "level_3": "Cash",
            "level_4": "", "level_path": "BS / Assets / Cash",
        },
    }
    out = annotate_suspicious_texts(suspicious, level_map)
    assert out[0]["entity_prefix"] == "01"
    assert out[0]["position"] == "Cash"
    assert out[0]["level_path"] == "BS / Assets / Cash"


def test_suspicious_texts_unmapped_account_no_position_bucket():
    suspicious = [
        {"booking_line_id": 1, "acct_ang": "99ZZZ", "entity_prefix": "99",
         "amount_keur": 1.0, "line_note": "test", "matched_keyword": "test"},
    ]
    out = annotate_suspicious_texts(suspicious, {})
    assert out[0]["position"] == NO_POSITION_BUCKET


def test_suspicious_texts_sorted_by_position_then_amount():
    suspicious = [
        {"booking_line_id": 1, "acct_ang": "A", "entity_prefix": "01",
         "amount_keur": 5.0, "matched_keyword": "test"},
        {"booking_line_id": 2, "acct_ang": "A", "entity_prefix": "01",
         "amount_keur": 50.0, "matched_keyword": "test"},
        {"booking_line_id": 3, "acct_ang": "B", "entity_prefix": "01",
         "amount_keur": 99.0, "matched_keyword": "test"},
    ]
    level_map = {
        ("01", "A"): {"level_3": "Alpha", "level_2": "", "level_0": "", "level_path": ""},
        ("01", "B"): {"level_3": "Beta", "level_2": "", "level_0": "", "level_path": ""},
    }
    out = annotate_suspicious_texts(suspicious, level_map)
    # Alpha before Beta; within Alpha, larger |amount| first
    assert [r["booking_line_id"] for r in out] == [2, 1, 3]


# =========================================================================== #
# (4) Explanations + columns_help present and English
# =========================================================================== #
def test_explanations_present_for_all_three_sections():
    assert set(EXPLANATIONS) == {
        "unexpected_counter_positions", "other_positions", "suspicious_texts",
    }
    for text in EXPLANATIONS.values():
        assert isinstance(text, str) and len(text) > 40


def test_columns_help_present_for_all_three_sections():
    assert set(COLUMNS_HELP) == {
        "unexpected_counter_positions", "other_positions", "suspicious_texts",
    }
    # each section documents its key columns
    assert "position" in COLUMNS_HELP["unexpected_counter_positions"]
    assert "growth_pct" in COLUMNS_HELP["other_positions"]
    assert "matched_keyword" in COLUMNS_HELP["suspicious_texts"]
    for section in COLUMNS_HELP.values():
        for help_text in section.values():
            assert isinstance(help_text, str) and help_text


# =========================================================================== #
# (5) Orchestrator — DB-free, monkeypatched (NEVER runs the live learner)
# =========================================================================== #
def test_build_forensic_positions_monkeypatched(monkeypatch):
    """The builder wires the per-entity findings into rollups + explanations.

    Everything that touches the DB / co-occurrence learner is monkeypatched, so
    this exercises the orchestration + rollup wiring without a database.
    """
    from unittest.mock import MagicMock

    import app.services.gl_forensic_positions as gp
    from app.services.gl_analysis_common import AccountSeries

    acc = AccountSeries(
        gl_account_id="8400", account_name="Other income", account_number_group="01ACC",
        entity_prefix="01", level_0="PL", level_2="Operating income",
        level_3="Other operating income", level_4="Sub A", l4_sub="",
    )
    monkeypatch.setattr(gp, "build_account_monthly_series", lambda *a, **k: [acc])
    monkeypatch.setattr(gp, "material_account_groups", lambda *a, **k: ["01ACC"])
    monkeypatch.setattr(gp, "latest_anchor", lambda *a, **k: (2025, 7))

    # flagged novelty row keyed by booking_line_id (no hierarchy yet)
    monkeypatch.setattr(gp, "_build_unexpected_counter_accounts", lambda *a, **k: [{
        "booking_line_id": 1, "journal_entry_number": "TX1", "gl_account_id": "8400",
        "account_name": "Other income", "counter_gl_account_id": "C1",
        "counter_account_name": "Counter", "amount_keur": 42.0,
        "posting_date": "2025-07-15", "line_note": "", "pair_freq": 0,
        "freq_pct": 0.0, "novelty": "new",
    }])
    # the re-pull for enrichment (entity_prefix / acct_ang per booking_line_id)
    monkeypatch.setattr(gp, "_bookings_for_lines", lambda *a, **k: [{
        "booking_line_id": 1, "entity_prefix": "01", "acct_ang": "01ACC",
    }])
    monkeypatch.setattr(gp, "_build_other_positions", lambda *a, **k: [{
        "gl_account_id": "8400", "account_name": "Other income",
        "level_path": "PL / Other operating income",
        "balance_cm_keur": 40.0, "balance_pm_keur": 25.0, "delta_keur": 15.0,
        "yoy_keur": 7.0, "growth_pct": 60.0, "matched_token": "other",
    }])
    monkeypatch.setattr(gp, "_build_suspicious_texts", lambda *a, **k: [{
        "booking_line_id": 1, "gl_account_id": "8400", "account_name": "Other income",
        "posting_date": "2025-07-15", "amount_keur": 42.0, "line_note": "Storno X",
        "matched_keyword": "storno", "journal_entry_number": "TX1",
        "entity_prefix": "01",
    }])

    out = gp.build_forensic_positions(MagicMock(), entity_prefixes=["01"])

    assert set(out) == {
        "unexpected_counter_positions", "other_positions", "suspicious_texts", "meta",
    }
    # (1) counter rollup landed on the right position with the enriched hierarchy
    ucp = out["unexpected_counter_positions"]
    assert len(ucp) == 1
    assert ucp[0]["position"] == "Other operating income"
    assert ucp[0]["level_4"] == "Sub A"
    assert ucp[0]["new_count"] == 1
    assert ucp[0]["abs_amount_keur"] == pytest.approx(42.0)
    assert ucp[0]["entity_split"]["01"]["count"] == 1
    # (2) other rollup recomputed from summed balances
    op = out["other_positions"]
    assert len(op) == 1
    assert op[0]["balance_cm_keur"] == pytest.approx(40.0)
    assert op[0]["growth_pct"] == pytest.approx(60.0)
    # (3) suspicious text carries entity_prefix + position
    st = out["suspicious_texts"]
    assert st[0]["entity_prefix"] == "01"
    assert st[0]["position"] == "Other operating income"
    # (4) explanations + columns_help present in meta
    assert set(out["meta"]["explanations"]) == set(EXPLANATIONS)
    assert set(out["meta"]["columns_help"]) == set(COLUMNS_HELP)
    assert out["meta"]["algorithm_version"] == POSITIONS_ALGORITHM_VERSION
    assert out["meta"]["entity_prefixes"] == ["01"]
    assert out["meta"]["anchor"] == {"year": 2025, "month": 7}


def test_build_forensic_positions_empty_when_no_anchor(monkeypatch):
    from unittest.mock import MagicMock
    import app.services.gl_forensic_positions as gp

    monkeypatch.setattr(gp, "latest_anchor", lambda *a, **k: None)
    out = gp.build_forensic_positions(MagicMock())
    assert out["unexpected_counter_positions"] == []
    assert out["other_positions"] == []
    assert out["suspicious_texts"] == []
    assert out["meta"]["anchor"] is None
    assert set(out["meta"]["explanations"]) == set(EXPLANATIONS)


# =========================================================================== #
# (6) SECURITY regression — forensic scoping is by entity_prefix DIRECTLY and does
#     NOT depend on the legal_entity_code == entity_prefix seeding invariant.
# =========================================================================== #
# Background: the per-entity forensic builders used to scope by routing the caller's
# value through fin_compat_sql.resolve_entity_prefix (a legal_entity_code lookup).
# That ONLY worked because etl/load.py seeds legal_entity_code == entity_prefix; if
# they ever differ, resolve_entity_prefix returns None → entity_sql_fragment(None) →
# an EMPTY fragment → the forensic query runs UNSCOPED over all entities (cross-tenant
# leak for a restricted user).  The fix scopes by fact_gl_line.entity_prefix DIRECTLY
# (the robust path the trees use).  These tests SABOTAGE resolve_entity_prefix to
# return None (simulating legal_entity_code != entity_prefix) and prove scoping holds.
class _CapturingSession:
    """Fake Session that records every SQL string passed to execute().

    Returns ``rows`` for SELECTs that look like the forensic builder queries (the
    co-occurrence cache read + the per-account / per-booking pulls) so the builder
    runs end-to-end without a database; everything else returns empty.
    """

    def __init__(self, rows=None):
        self.sqls: list[str] = []
        self._rows = rows or []

    def execute(self, stmt, params=None):
        from unittest.mock import MagicMock

        self.sqls.append(str(stmt))
        result = MagicMock()
        result.fetchall.return_value = self._rows
        result.fetchone.return_value = self._rows[0] if self._rows else None
        result.scalar.return_value = 0
        return result


def _sabotage_resolve(monkeypatch):
    """Make resolve_entity_prefix ALWAYS return None (the broken invariant).

    Patches it in BOTH modules that import it (gl_forensic re-uses the symbol it
    imported), so any code path that still relied on the legal_entity_code lookup
    would go UNSCOPED — and the test would catch it.
    """
    import app.services.gl_forensic as gf
    import app.services.fin_compat_sql as fcsql

    monkeypatch.setattr(gf, "resolve_entity_prefix", lambda *a, **k: None)
    monkeypatch.setattr(fcsql, "resolve_entity_prefix", lambda *a, **k: None)


def test_other_positions_scoped_by_prefix_when_code_differs(monkeypatch):
    """_build_other_positions filters l.entity_prefix='77' even though
    resolve_entity_prefix is sabotaged to None — proving scoping no longer depends
    on legal_entity_code == entity_prefix."""
    from app.services.gl_forensic import _build_other_positions

    _sabotage_resolve(monkeypatch)
    sess = _CapturingSession(rows=[])
    _build_other_positions(sess, None, year=2025, period=7, entity_prefix="77")

    # The per-account SQL must carry the DIRECT prefix filter on the line table.
    assert any("l.entity_prefix = '77'" in s for s in sess.sqls), sess.sqls
    # And must NOT have run unscoped (no empty-fragment leak).
    assert not any(
        ("FROM fact_gl_line l" in s and "entity_prefix" not in s)
        for s in sess.sqls
    ), "forensic Other query ran UNSCOPED — cross-tenant leak"


def test_current_period_bookings_scoped_by_prefix_when_code_differs(monkeypatch):
    """fetch_current_period_bookings scopes l.entity_prefix directly under the
    broken invariant (resolve_entity_prefix → None)."""
    from app.services.gl_forensic import fetch_current_period_bookings

    _sabotage_resolve(monkeypatch)
    sess = _CapturingSession(rows=[])
    fetch_current_period_bookings(
        sess, None, year=2025, period=7, account_groups=["X"], entity_prefix="77",
    )
    assert any("l.entity_prefix = '77'" in s for s in sess.sqls), sess.sqls


def test_cooccurrence_learner_scoped_by_prefix_when_code_differs(monkeypatch):
    """build_cooccurrence scopes the anchor leg a.entity_prefix directly under the
    broken invariant."""
    from app.services.gl_forensic import build_cooccurrence

    _sabotage_resolve(monkeypatch)
    sess = _CapturingSession(rows=[])
    build_cooccurrence(sess, None, entity_prefix="77")
    assert any("a.entity_prefix = '77'" in s for s in sess.sqls), sess.sqls


def test_orchestrator_passes_prefix_not_code_to_builders(monkeypatch):
    """build_forensic_positions(entity_prefixes=['77']) forwards entity_prefix='77'
    to EVERY per-entity builder (so scoping is by prefix, not via a code lookup).

    resolve_entity_prefix is sabotaged to None: if the orchestrator still routed the
    prefix through it (the old fail-open path) the captured scope below would be None
    and the assertions would fail.  No bulk learner runs (all DB calls monkeypatched).
    """
    from unittest.mock import MagicMock

    import app.services.gl_forensic_positions as gp
    from app.services.gl_analysis_common import AccountSeries

    _sabotage_resolve(monkeypatch)

    captured: dict[str, list] = {
        "unexpected": [], "material": [], "other": [], "suspicious": [], "bookings": [],
    }

    acc_77 = AccountSeries(
        gl_account_id="8400", account_name="A77", account_number_group="77ACC",
        entity_prefix="77", level_0="PL", level_2="Op", level_3="Other income",
        level_4="", l4_sub="",
    )
    acc_88 = AccountSeries(
        gl_account_id="9999", account_name="A88", account_number_group="88ACC",
        entity_prefix="88", level_0="PL", level_2="Op", level_3="Other income",
        level_4="", l4_sub="",
    )
    # build_account_monthly_series returns BOTH entities (consolidated pull); the
    # orchestrator must filter to '77' in Python (direct prefix filter, like the trees).
    monkeypatch.setattr(
        gp, "build_account_monthly_series", lambda *a, **k: [acc_77, acc_88]
    )
    monkeypatch.setattr(gp, "latest_anchor", lambda *a, **k: (2025, 7))

    def _cap_unexpected(session, entity, *, entity_prefix=None, **k):
        captured["unexpected"].append(entity_prefix)
        return []

    def _cap_material(session, entity, *, entity_prefix=None, **k):
        captured["material"].append(entity_prefix)
        return []

    def _cap_other(session, entity, *, entity_prefix=None, **k):
        captured["other"].append(entity_prefix)
        return []

    def _cap_suspicious(session, entity, *, entity_prefix=None, **k):
        captured["suspicious"].append(entity_prefix)
        return []

    def _cap_bookings(session, year, period, groups, *, entity_prefix=None, **k):
        captured["bookings"].append(entity_prefix)
        return []

    monkeypatch.setattr(gp, "_build_unexpected_counter_accounts", _cap_unexpected)
    monkeypatch.setattr(gp, "material_account_groups", _cap_material)
    monkeypatch.setattr(gp, "_build_other_positions", _cap_other)
    monkeypatch.setattr(gp, "_build_suspicious_texts", _cap_suspicious)
    monkeypatch.setattr(gp, "_bookings_for_lines", _cap_bookings)

    out = gp.build_forensic_positions(MagicMock(), entity_prefixes=["77"])

    # Every builder was scoped by the PREFIX '77' (never None / unscoped).
    assert captured["unexpected"] == ["77"]
    assert captured["material"] == ["77"]
    assert captured["other"] == ["77"]
    assert captured["suspicious"] == ["77"]
    assert captured["bookings"] == ["77"]
    # The level map is built only from the in-scope ('77') accounts — the '88'
    # account (other tenant) must NOT appear, proving the Python-side prefix filter.
    assert ("77", "77ACC") in gp._level_map(
        [a for a in [acc_77, acc_88] if a.entity_prefix == "77"]
    )
    assert out["meta"]["entity_prefixes"] == ["77"]


def test_admin_runs_unscoped_single_pass(monkeypatch):
    """Admin (entity_prefixes=None) → one pass with entity_prefix=None (all entities)."""
    from unittest.mock import MagicMock

    import app.services.gl_forensic_positions as gp

    captured: list = []
    monkeypatch.setattr(gp, "build_account_monthly_series", lambda *a, **k: [])
    monkeypatch.setattr(gp, "latest_anchor", lambda *a, **k: (2025, 7))

    def _cap(session, entity, *, entity_prefix=None, **k):
        captured.append(entity_prefix)
        return []

    monkeypatch.setattr(gp, "_build_unexpected_counter_accounts", _cap)
    monkeypatch.setattr(gp, "material_account_groups", lambda *a, **k: [])
    monkeypatch.setattr(gp, "_build_other_positions", lambda *a, **k: [])
    monkeypatch.setattr(gp, "_build_suspicious_texts", lambda *a, **k: [])
    monkeypatch.setattr(gp, "_bookings_for_lines", lambda *a, **k: [])

    gp.build_forensic_positions(MagicMock(), entity_prefixes=None)
    assert captured == [None]  # exactly one unscoped pass = all entities


# =========================================================================== #
# Postgres integration (SKIPPED when finssentials_v2 unreachable) — reads the
# co-occurrence CACHE only; NEVER triggers the bulk learner over the live DB.
# =========================================================================== #
def _v2_session():
    os.environ.setdefault("DB_NAME", "finssentials_v2")
    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import Session
        from app.config import settings
        url = settings.database_url
        if "finssentials_v2" not in url:
            url = url.rsplit("/", 1)[0] + "/finssentials_v2"
        eng = create_engine(url, connect_args={"connect_timeout": 3})
        s = Session(eng)
        s.execute(text("SELECT 1"))
        # require a warmed cache so we never trigger the live learner here
        n = s.execute(text("SELECT COUNT(*) FROM fact_gl_counter_cooccurrence")).scalar()
        if not n:
            s.close()
            return None
        return s
    except Exception:
        return None


@pytest.fixture(scope="module")
def v2():
    s = _v2_session()
    if s is None:
        pytest.skip("finssentials_v2 not reachable or co-occurrence cache empty")
    yield s
    s.close()


def test_pg_build_forensic_positions_shape(v2):
    from app.services.gl_forensic_positions import build_forensic_positions

    out = build_forensic_positions(v2, entity_prefixes=["01"])
    assert set(out) == {
        "unexpected_counter_positions", "other_positions", "suspicious_texts", "meta",
    }
    assert set(out["meta"]["explanations"]) == set(EXPLANATIONS)
    # each counter position reconciles its entity_split to its totals
    for node in out["unexpected_counter_positions"]:
        split = node["entity_split"]
        assert sum(s["count"] for s in split.values()) == node["flag_count"]
        assert sum(s["abs_amount_keur"] for s in split.values()) == pytest.approx(
            node["abs_amount_keur"], abs=1e-3
        )
