"""DB-free tests for the shared narrative-analysis core + the P&L / BS narratives.

Project hard rule #1 (no financial logic without proof) — every analytic added in
``fin_compat_narrative_core`` is locked here with a tiny worked example and its
edge cases:

  * compute_line_facts      — display_mom / favorable_mom / share_of_base / pct_mom
  * detect_hidden_netting   — parent ~flat while children material
  * gl_concentration_from_detail — top account / booking share (kEUR, sign re-orient)
  * score_line / is_material / select_drivers — selection at legacy depth
  * build_bullet            — flow (P&L) vs point-in-time (BS) prose + tone
  * build_pl_narrative / build_bs_narrative — end-to-end response shape (mocked DB)

All statement amounts/deltas are in EUR; detail account/booking figures in kEUR.
No DB, no network — the statement and line-detail builders are monkeypatched.
"""
from __future__ import annotations

from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from app.services import fin_compat_narrative_core as core


# ---------------------------------------------------------------------------
# Synthetic-row helpers
# ---------------------------------------------------------------------------
def _line(
    code: str,
    label: str,
    cm: float,
    pm: float,
    py_cm: float = 0.0,
    *,
    ytd: float = 0.0,
    ytd_py: float = 0.0,
    kpi_code: str = "",
    mom: Optional[float] = None,
    yoy: Optional[float] = None,
    children: Optional[list] = None,
    accounts: Optional[list] = None,
    row_kind: str = "line",
    plan_cm: Optional[float] = None,
) -> dict[str, Any]:
    """A statement row.  ``mom``/``yoy`` default to the raw displayed deltas but can
    be overridden to model an already-invert-applied (favourable-signed) delta."""
    amounts = {"py_cm": py_cm, "pm": pm, "cm": cm, "ytd": ytd, "ytd_py": ytd_py}
    if plan_cm is not None:
        amounts["plan_cm"] = plan_cm
        amounts["plan_vs_actual"] = round(cm - plan_cm, 2)
    return {
        "line_code": code,
        "label": label,
        "row_kind": row_kind,
        "kpi_code": kpi_code,
        "invert_delta": False,
        "amounts": amounts,
        "deltas": {
            "mom": (cm - pm) if mom is None else mom,
            "yoy": (cm - py_cm) if yoy is None else yoy,
            "ytd": ytd - ytd_py,
        },
        "children": children or [],
        "accounts": accounts or [],
    }


# ===========================================================================
# Prose helpers
# ===========================================================================
def test_lower_first_preserves_all_caps_acronyms():
    assert core.lower_first("EBITDA") == "EBITDA"
    assert core.lower_first("D&A") == "D&A"
    assert core.lower_first("Net sales") == "net sales"


# ===========================================================================
# Stage 1 — FACTS
# ===========================================================================
def test_compute_line_facts_formula():
    """Worked example: cm=520k pm=500k py_cm=470k base=520k →
    display_mom +20k, display_yoy +50k, share 1.0, pct_mom +4%."""
    row = _line("NET_SALES", "Net sales", 520_000, 500_000, 470_000)
    f = core.compute_line_facts(row, base_cm=520_000)
    assert f["display_mom"] == pytest.approx(20_000)
    assert f["display_yoy"] == pytest.approx(50_000)
    assert f["share_of_base"] == pytest.approx(1.0)
    assert f["pct_mom"] == pytest.approx(4.0, abs=0.01)


def test_compute_line_facts_edges_zero_base_and_flat_pm():
    """Edge: pm≈0 → pct_mom None (no div-by-zero); base≈0 → share≈0 (floor 1.0)."""
    row = _line("X", "X", 1_000, 0.0, 0.0)
    f = core.compute_line_facts(row, base_cm=0.0)
    assert f["pct_mom"] is None
    assert f["share_of_base"] == pytest.approx(1_000 / 1.0)  # base floored to 1.0
    row2 = _line("Y", "Y", 50_000, 40_000, 0.0)
    f2 = core.compute_line_facts(row2, base_cm=0.0)
    assert f2["share_of_base"] == pytest.approx(50_000.0)  # |cm|/1.0


def test_favorable_mom_is_taken_verbatim_no_double_invert():
    """favorable_mom == deltas.mom verbatim (already invert-applied upstream);
    display_mom == cm-pm.  A cost line can fall in display while reading favourable."""
    # Cost magnitude rose +50k (display), but upstream marked it unfavourable (-50k).
    row = _line("MAT", "Materials", 300_000, 250_000, 260_000, mom=-50_000, yoy=-40_000)
    f = core.compute_line_facts(row, base_cm=520_000)
    assert f["display_mom"] == pytest.approx(50_000)     # the number shown rose 50k
    assert f["favorable_mom"] == pytest.approx(-50_000)  # tone signal is unfavourable


def test_detect_hidden_netting_flags_offsetting_children():
    """Parent MoM +5k; children +90k and −85k → both flagged (ratio 0.35:
    5k < 90k*0.35 and 5k < 85k*0.35)."""
    parent = _line(
        "OTHER", "Other items", 100_000, 95_000,
        children=[
            _line("OTHER::A", "Sub A", 0, 0, mom=90_000),
            _line("OTHER::B", "Sub B", 0, 0, mom=-85_000),
            _line("OTHER::tiny", "Tiny", 0, 0, mom=200),  # below child floor
        ],
    )
    flags = core.detect_hidden_netting(parent)
    labels = {x["label"] for x in flags}
    assert labels == {"Sub A", "Sub B"}
    assert flags[0]["label"] == "Sub A"  # sorted by |mom| desc


def test_detect_hidden_netting_silent_when_parent_moved():
    """Edge: a parent that genuinely moved a lot flags nothing."""
    parent = _line(
        "BIG", "Big mover", 200_000, 100_000,  # parent MoM +100k
        children=[_line("BIG::A", "Sub A", 0, 0, mom=90_000)],
    )
    assert core.detect_hidden_netting(parent) == []


def test_gl_concentration_basic_shares_and_clamp():
    """accounts A(+80) B(-10) → line_delta 70; top share 80/70 clamped to 1.0.
    largest booking +60 → booking_share 60/70 ≈ 0.86."""
    payload = {
        "accounts": [
            {"account_name": "Steel", "gl_account_id": "400100", "balance_cm": 300, "balance_pm": 220, "delta": 80},
            {"account_name": "Misc", "gl_account_id": "400900", "balance_cm": 50, "balance_pm": 60, "delta": -10},
        ],
        "top_bookings": [
            {"amount": 60, "line_note": "Invoice 7788"},
            {"amount": 5, "line_note": "small"},
        ],
    }
    conc = core.gl_concentration_from_detail(payload)
    assert conc["top_account_name"] == "Steel"
    assert conc["top_account_share"] == pytest.approx(1.0)  # 80/70 clamped
    assert conc["top_account_delta_keur"] == pytest.approx(80)
    assert conc["booking_share"] == pytest.approx(60 / 70, abs=0.01)
    assert conc["largest_booking_ref"] == "Invoice 7788"


def test_gl_concentration_sign_reorients_credit_side():
    """Edge (BS credit): raw stored negative balances; sign=-1 makes them read +.
    delta -50 → reported +50 (display sign), share unchanged."""
    payload = {
        "accounts": [
            {"account_name": "Trade payables", "gl_account_id": "160000", "balance_cm": -300, "balance_pm": -250, "delta": -50},
        ],
        "top_bookings": [{"amount": -40, "line_note": "Supplier credit"}],
    }
    conc = core.gl_concentration_from_detail(payload, sign=-1.0)
    assert conc["top_account_delta_keur"] == pytest.approx(50)
    assert conc["top_account_balance_keur"] == pytest.approx(300)
    assert conc["largest_booking_amount_keur"] == pytest.approx(40)
    assert conc["top_account_share"] == pytest.approx(1.0)  # magnitude-based


def test_gl_concentration_empty_payload():
    assert core.gl_concentration_from_detail(None) == {}
    assert core.gl_concentration_from_detail({"accounts": [], "top_bookings": []}) == {}


# ===========================================================================
# Stage 2 — SELECTION / SCORING
# ===========================================================================
def test_is_material_size_vs_pure_flat():
    """A large line that moved → 'size'; a large but perfectly flat line → not."""
    moved = core.compute_line_facts(_line("A", "A", 520_000, 500_000), base_cm=520_000)
    ok, reasons = core.is_material(moved, base_cm=520_000, peer_mom_sum=20_000)
    assert ok and "size" in reasons
    flat = core.compute_line_facts(_line("B", "B", 520_000, 520_000, 520_000), base_cm=520_000)
    ok2, reasons2 = core.is_material(flat, base_cm=520_000, peer_mom_sum=0)
    assert not ok2


def test_is_material_movement_floor():
    """MoM below max(30k, 3%*cm) is not 'movement' (and small cm/yoy → immaterial)."""
    small = core.compute_line_facts(
        _line("C", "C", 40_000, 25_000, 38_000), base_cm=2_000_000
    )
    ok, reasons = core.is_material(small, base_cm=2_000_000, peer_mom_sum=15_000)
    # cm=40k < size floor 50k; mom=15k < 30k; yoy=2k < 30k → immaterial
    assert not ok


def test_score_line_anchor_boost_worked_example():
    """revenue cm=520k base=520k mom=20k yoy=50k anchor=30 →
    10 + 15 + 50 + 30 = 105.0."""
    f = core.compute_line_facts(_line("NET_SALES", "Net sales", 520_000, 500_000, 470_000), base_cm=520_000)
    assert core.score_line(f, base_cm=520_000, anchor_boost=30.0) == pytest.approx(105.0, abs=0.01)


def test_select_drivers_table_order_top_by_score():
    """3 material lines, cap=2 → keep the 2 highest scores, emitted in table order."""
    rows = [
        _line("L1", "Low", 60_000, 55_000),       # small mover
        _line("BIG", "Big", 400_000, 300_000),    # biggest
        _line("MID", "Mid", 200_000, 150_000),    # middle
    ]
    facts = [core.compute_line_facts(r, base_cm=1_000_000) for r in rows]
    peer = sum(abs(f["display_mom"]) for f in facts)
    for f in facts:
        ok, why = core.is_material(f, base_cm=1_000_000, peer_mom_sum=peer)
        f["material"], f["reasons"] = ok, why
        f["score"] = core.score_line(f, base_cm=1_000_000)
    chosen = core.select_drivers(facts, cap=2)
    codes = [c["line_code"] for c in chosen]
    assert codes == ["BIG", "MID"]  # table order preserved, L1 dropped (lowest score)


def test_select_drivers_pads_to_min_when_immaterial():
    """Edge: nothing material → pad with largest movers up to MIN_BULLETS."""
    rows = [_line("a", "a", 1_000, 900), _line("b", "b", 2_000, 1_000), _line("c", "c", 500, 480)]
    facts = [core.compute_line_facts(r, base_cm=10_000_000) for r in rows]
    for f in facts:
        f["material"], f["reasons"], f["score"] = False, [], 0.0
    chosen = core.select_drivers(facts, cap=5, min_bullets=2)
    assert len(chosen) == 2
    assert {c["line_code"] for c in chosen} == {"a", "b"}  # two largest |mom|


# ===========================================================================
# Stage 3 — PROSE
# ===========================================================================
def _pl_ctx() -> core.ProseContext:
    return core.ProseContext(
        statement_kind="pl", period_label="Jun25", prior_label="May25",
        base_label="revenue", base_cm=520_000, balance_style=False,
        tone_mode="favorable", movement_noun="the move",
    )


def _bs_ctx() -> core.ProseContext:
    return core.ProseContext(
        statement_kind="bs", period_label="Jun25", prior_label="May25",
        base_label="total assets", base_cm=1_000_000, balance_style=True,
        tone_mode="directional", movement_noun="the balance movement",
    )


def test_build_bullet_pl_flow_uses_display_direction_and_favorable_tone():
    """Cost line: number rose (display) → verb 'rose'; favourable_mom<0 → tone negative."""
    row = _line("MAT", "Materials", 300_000, 250_000, 260_000, mom=-50_000, yoy=-40_000)
    f = core.compute_line_facts(row, base_cm=520_000)
    f["concentration"] = core.gl_concentration_from_detail({
        "accounts": [{"account_name": "Steel", "gl_account_id": "400100", "balance_cm": 300, "balance_pm": 250, "delta": 50}],
        "top_bookings": [{"amount": 40, "line_note": "Invoice 7788"}],
    })
    text, tone = core.build_bullet(f, _pl_ctx())
    assert "Materials rose €50k versus May25" in text
    assert "to €300k" in text
    assert "account 400100 *Steel*" in text
    assert "100% of the move" in text
    assert "Invoice 7788" in text          # dominant posting surfaced
    assert tone == "negative"              # favourable_mom = -50k


def test_build_bullet_bs_point_in_time_directional_tone():
    """BS: point-in-time wording; a balance going up reads 'positive' (directional)."""
    row = _line("AR", "Trade receivables", 600_000, 540_000, 500_000, kpi_code="BS:asset")
    f = core.compute_line_facts(row, base_cm=1_000_000)
    text, tone = core.build_bullet(f, _bs_ctx())
    assert "Trade receivables stood at €600k as of Jun25" in text
    assert "up €60k from the May25 month-end" in text
    assert "60% of total assets" in text
    assert tone == "positive"


def test_build_bullet_bs_week_uses_cw_prior_phrase():
    """Weekly BS bullets compare to prior calendar week, not month-end."""
    ctx = core.ProseContext(
        statement_kind="bs",
        period_label="CW12'25",
        prior_label="CW11'25",
        base_label="total assets",
        base_cm=1_000_000,
        balance_style=True,
        tone_mode="directional",
        movement_noun="the balance movement",
        period_grain="week",
    )
    row = _line("AR", "Trade receivables", 600_000, 540_000, 500_000, kpi_code="BS:asset")
    f = core.compute_line_facts(row, base_cm=1_000_000)
    text, _ = core.build_bullet(f, ctx)
    assert "as of CW12'25" in text
    assert "compared to CW11'25" in text
    assert "month-end" not in text


def test_hidden_netting_sentence_present():
    flags = [
        {"label": "Sub A", "mom": 90_000},
        {"label": "Sub B", "mom": -85_000},
    ]
    s = core.hidden_netting_sentence(flags, _pl_ctx())
    assert "nets off larger sub-line moves" in s
    assert "sub A (+€90k)" in s
    assert "sub B (-€85k)" in s


def test_subline_leader_sentence_surfaces_child_contributors():
    row = _line(
        "WC", "Working capital", 500_000, 440_000,
        children=[
            _line("AR", "Trade receivables", 0, 0, mom=60_000),
            _line("AP", "Trade payables", 0, 0, mom=-10_000),
        ],
    )
    s = core.subline_leader_sentence(row, 50_000, _bs_ctx())
    assert "led by trade receivables" in s
    assert "120% of the balance movement" in s or "120% of" in s


# ===========================================================================
# End-to-end — P&L narrative (mocked statement + line detail)
# ===========================================================================
def _pl_statement() -> dict[str, Any]:
    net_sales = _line("NET_SALES", "Net sales", 520_000, 500_000, 470_000, ytd=1_500_000, ytd_py=1_400_000)
    materials = _line("MAT", "Materials", 300_000, 250_000, 260_000, mom=-50_000, yoy=-40_000)
    other = _line(
        "OTHER", "Other operating items", 100_000, 98_000,
        children=[
            _line("OTHER::A", "Insurance", 0, 0, mom=60_000),
            _line("OTHER::B", "Rebates", 0, 0, mom=-58_000),
        ],
    )
    net_profit = _line(
        "NET_PROFIT", "Net profit", 130_000, 120_000, 110_000,
        ytd=400_000, ytd_py=360_000, row_kind="subtotal", plan_cm=125_000,
    )
    return {
        "rows": [net_sales, materials, other, net_profit],
        "col_labels": {"cm": "Jun25", "pm": "May25", "py_cm": "Jun24",
                       "ytd": "YTDJun25", "ytd_py": "YTDJun24"},
    }


def _fake_pl_line_detail(session, line_code, year, month, entity, **kw):
    if line_code == "MAT":
        return {
            "accounts": [
                {"account_name": "Raw steel", "gl_account_id": "400100", "balance_cm": 300, "balance_pm": 250, "delta": 50},
                {"account_name": "Packaging", "gl_account_id": "400200", "balance_cm": 20, "balance_pm": 18, "delta": 2},
            ],
            "top_bookings": [{"amount": 40, "line_note": "Steel delivery 7788"}],
        }
    return {"accounts": [], "top_bookings": []}


def test_build_pl_narrative_end_to_end(monkeypatch):
    from app.services import fin_compat_narrative as narr

    monkeypatch.setattr(narr, "build_pl_statement_compat", lambda *a, **k: _pl_statement())
    monkeypatch.setattr(
        "app.services.fin_compat_line_detail.build_pl_line_detail", _fake_pl_line_detail
    )
    out = narr.build_pl_narrative(MagicMock(), 2025, 6, None)

    # Shape preserved
    assert set(out) == {"headline", "intro", "intro_facts", "bullets", "entity_split", "meta"}
    assert set(out["intro_facts"]) == {
        "period_label", "group_label", "net_profit_ytd", "coverage_pct",
        "cm_month_label", "cm_vs_plan", "cm_vs_plan_qualifier",
        "primary_drivers", "entity_split",
    }
    for b in out["bullets"]:
        assert set(b) == {"index", "line_code", "label", "text", "tone", "deep_links", "facts"}

    # Net-profit anchored intro with plan context (legacy overview style)
    assert "net profit amounted to" in out["intro"]
    assert "to plan" in out["intro"]
    assert out["intro"].endswith("key drivers consist of:")
    assert out["intro_facts"]["cm_vs_plan"] == pytest.approx(5_000)  # 130k - 125k

    # Bullets: NET_SALES, MAT, OTHER all material; table order
    codes = [b["line_code"] for b in out["bullets"]]
    assert codes[:3] == ["NET_SALES", "MAT", "OTHER"]
    mat = next(b for b in out["bullets"] if b["line_code"] == "MAT")
    assert "account 400100 *Raw steel*" in mat["text"]
    assert mat["tone"] == "negative"
    other = next(b for b in out["bullets"] if b["line_code"] == "OTHER")
    assert other["facts"]["hidden_netting"] is True
    assert "nets off" in other["text"]
    # primary driver #1 is the revenue anchor (highest score)
    assert out["intro_facts"]["primary_drivers"][0]["label"] == "Net sales"
    assert out["meta"]["algorithm_version"] == "pl_narrative_compat_v2"


# ===========================================================================
# End-to-end — Balance-sheet narrative (mocked statement + line detail)
# ===========================================================================
def _bs_statement() -> dict[str, Any]:
    ar = _line("AR", "Trade receivables", 600_000, 540_000, 500_000, kpi_code="BS:asset")
    inv = _line("INVENTORY", "Inventories", 400_000, 410_000, 420_000, kpi_code="BS:asset")
    ta = _line("BS_GRANDTOTAL_ASSETS", "Total assets", 1_000_000, 950_000, 920_000,
               kpi_code="BS:asset", row_kind="subtotal")
    ap = _line("AP", "Trade payables", 300_000, 250_000, 280_000, kpi_code="BS:credit")
    eq = _line("EQUITY", "Equity", 700_000, 690_000, 640_000, kpi_code="BS:credit")
    el = _line("BS_GRANDTOTAL_EQ_LIAB", "Total equity & liabilities",
               1_000_000, 940_000, 920_000, kpi_code="BS:credit", row_kind="subtotal")
    return {
        "rows": [ar, inv, ta, ap, eq, el],
        "col_labels": {"cm": "Jun25", "pm": "May25"},
    }


def _fake_bs_line_detail(session, line_code, year, month, entity, **kw):
    if line_code == "AR":  # asset side, stored positive
        return {
            "accounts": [{"account_name": "Domestic debtors", "gl_account_id": "140000", "balance_cm": 600, "balance_pm": 540, "delta": 60}],
            "top_bookings": [{"amount": 55, "line_note": "Customer invoice batch"}],
        }
    if line_code == "AP":  # credit side, stored negative
        return {
            "accounts": [{"account_name": "Domestic creditors", "gl_account_id": "160000", "balance_cm": -300, "balance_pm": -250, "delta": -50}],
            "top_bookings": [{"amount": -45, "line_note": "Supplier invoice batch"}],
        }
    return {"accounts": [], "top_bookings": []}


def test_build_bs_narrative_end_to_end(monkeypatch):
    from app.services import fin_compat_bs as bs

    monkeypatch.setattr(bs, "build_bs_statement_compat", lambda *a, **k: _bs_statement())
    monkeypatch.setattr(bs, "build_bs_line_detail", _fake_bs_line_detail)
    out = bs.build_bs_narrative(MagicMock(), 2025, 6, None)

    assert set(out) == {"headline", "intro", "intro_facts", "bullets", "entity_split", "meta"}
    assert set(out["intro_facts"]) == {
        "period_label", "group_label", "net_profit_ytd", "coverage_pct",
        "cm_month_label", "cm_vs_plan", "cm_vs_plan_qualifier",
        "primary_drivers", "entity_split",
    }

    # Total-assets anchored, point-in-time intro with YoY context
    assert "total assets stood at €1.0m" in out["intro"]
    assert "up €50k from the May25 month-end" in out["intro"]
    assert "up €80k year-on-year" in out["intro"]
    assert "concentrated in account 140000 *Domestic debtors*" in out["intro"]
    assert out["intro"].endswith("key drivers consist of:")

    codes = [b["line_code"] for b in out["bullets"]]
    assert codes == ["AR", "INVENTORY", "AP", "EQUITY"]  # table order

    ar = next(b for b in out["bullets"] if b["line_code"] == "AR")
    assert "Trade receivables stood at €600k as of Jun25" in ar["text"]
    assert "up €60k from the May25 month-end" in ar["text"]
    assert "account 140000 *Domestic debtors*" in ar["text"]
    assert ar["tone"] == "positive"

    # Credit side: stored negative detail re-signed to display (+€50k)
    ap = next(b for b in out["bullets"] if b["line_code"] == "AP")
    assert "Trade payables stood at €300k as of Jun25" in ap["text"]
    assert "up €50k from the May25 month-end" in ap["text"]
    assert "+€50k" in ap["text"]            # concentration in display sign
    assert ap["tone"] == "positive"        # balance grew

    inv = next(b for b in out["bullets"] if b["line_code"] == "INVENTORY")
    assert inv["tone"] == "negative"       # balance fell 10k

    assert out["intro_facts"]["primary_drivers"][0]["label"] == "Trade receivables"
    assert out["meta"]["algorithm_version"] == "bs_narrative_compat_v2"


# ---------------------------------------------------------------------------
# Annual flow narrative basis (year-grain CF fix)
# ---------------------------------------------------------------------------
def _annual_flow_row(code: str, label: str, *, fy2: float, fy3: float,
                     ytd: float, ltm: float, ytd_py: float,
                     row_kind: str = "line") -> dict[str, Any]:
    """A build_cf_annual_compat-shaped row (amounts keyed by _ER_FLOW_KEYS)."""
    return {
        "line_code": code, "label": label, "row_kind": row_kind,
        "amounts": {
            "fy1": 0.0, "fy2": fy2, "fy3": fy3, "ytd": ytd, "ltm": ltm,
            "ytd_py": ytd_py, "ltm_py": 0.0, "fy_f": 0.0,
        },
    }


def test_annual_flow_ytd_basis_unchanged():
    """Default (ytd) basis maps cm<-ytd, pm<-ltm — legacy behaviour, byte-stable."""
    rows = [_annual_flow_row("EBITDA", "EBITDA",
                             fy2=14_980_000, fy3=13_770_000,
                             ytd=8_000_000, ltm=13_000_000, ytd_py=7_500_000)]
    out = core.normalize_annual_flow_rows(rows)  # default flow_basis="ytd"
    am = out[0]["amounts"]
    assert am["cm"] == 8_000_000.0 and am["pm"] == 13_000_000.0
    assert am["py_cm"] == 7_500_000.0
    raw = {"ytd": "YTDJul25A", "ltm": "LTMJul25A", "ytd_py": "YTDJul24A",
           "fy3": "FY24A", "fy2": "FY23A"}
    assert core.narrative_labels_from_flow_annual(raw)["cm"] == "YTDJul25A"


def test_annual_flow_fy_basis_reads_full_year_columns():
    """fy basis anchors on completed FYs (fy3 vs fy2).

    Regression for the year-grain CF narrative computing €0k: the current-year
    YTD/LTM window is empty (ytd=ltm=0) in GDPdU datasets, so the narrative must
    read the FY columns the annual statement shows.  Worked example:
    EBITDA fy3=13.77M, fy2=14.98M -> cm=13.77M, pm=14.98M, mom=-1.21M (not 0).
    """
    rows = [
        _annual_flow_row("NCF", "Net cash flow", fy2=3_000_000, fy3=2_500_000,
                         ytd=0.0, ltm=0.0, ytd_py=0.0, row_kind="subtotal"),
        _annual_flow_row("EBITDA", "EBITDA", fy2=14_980_000, fy3=13_770_000,
                         ytd=0.0, ltm=0.0, ytd_py=0.0),
    ]

    # OLD basis collapses to 0 (reproduces the reported bug).
    old = core.normalize_annual_flow_rows(rows)
    assert old[1]["amounts"]["cm"] == 0.0

    # NEW basis surfaces the real FY figures + a sensible delta.
    new = core.normalize_annual_flow_rows(rows, flow_basis="fy")
    ncf, ebitda = new[0]["amounts"], new[1]["amounts"]
    assert ncf["cm"] == 2_500_000.0 and ncf["pm"] == 3_000_000.0
    assert ebitda["cm"] == 13_770_000.0 and ebitda["pm"] == 14_980_000.0
    assert new[1]["deltas"]["mom"] == -1_210_000.0

    raw = {"fy3": "FY24A", "fy2": "FY23A", "ytd": "YTDJul25A", "ltm": "LTMJul25A"}
    labels = core.narrative_labels_from_flow_annual(raw, flow_basis="fy")
    assert labels["cm"] == "FY24A" and labels["pm"] == "FY23A"
