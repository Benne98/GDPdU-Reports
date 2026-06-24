"""Tests for the Budget granularity-view service (app/services/granularity_view.py).

The granularity view now returns the FULL statement structure (subtotals +
grandtotals + the L3→L4→account drill) WITH a multi-period column set, by
REUSING the multi-period statement builders (fin_compat_pl / fin_compat_bs) and
then dropping the rows we do not plan by.  These tests pin the ADAPTER layer
(pure, DB-free) plus a v2-gated live shape test:

  1. period columns: PL year → 4 (FY1/FY2/FY3/YTD); BS year → 3 FY-end/YTD;
     month grain → last 24 columns; ``values[]`` aligned to ``periods``.
  2. KPI / title rows excluded; mapping ('line') + subtotal/grandtotal KEPT in
     order, so the statement structure (Gross profit, EBITDA, …) survives.
  3. mapping rows carry plannable + partner flags + has_l4; subtotal rows do not.
  4. L4 children carry their OWN nested accounts; presented values copied straight
     from the builder amounts.
  5. entity scope (fail-closed) → restricted prefixes never widen to all entities.

CLAUDE.md rule #2: synthetic fixtures only; read-only (cleans up nothing).
"""
from __future__ import annotations

import os

import pytest

from app.services import granularity_view as GV
from app.services.fin_compat_sql import period_label


# --------------------------------------------------------------------------- #
# Fixture helpers — hand-built builder ``rows`` (mirror the builder output shape)
# --------------------------------------------------------------------------- #
def _amounts(periods, vals):
    """Map a builder ``amounts{key}`` dict over the given period keys."""
    return {p["key"]: v for p, v in zip(periods, vals)}


def _line(code, label, periods, vals, *, children=None, accounts=None, is_bold=False):
    return {
        "id": f"pl-{code}", "line_code": code, "label": label,
        "row_kind": "line", "is_bold": is_bold,
        "amounts": _amounts(periods, vals),
        "children": children or [], "accounts": accounts or [],
    }


def _subtotal(code, label, periods, vals, *, is_bold=True):
    return {
        "id": f"pl-{code}", "line_code": code, "label": label,
        "row_kind": "subtotal", "is_bold": is_bold,
        "amounts": _amounts(periods, vals), "children": [],
    }


def _kpi(code, label, periods, vals):
    return {
        "id": f"pl-{code}", "line_code": code, "label": label,
        "row_kind": "kpi", "is_bold": False,
        "amounts": _amounts(periods, vals), "children": [],
    }


def _title(code, label):
    return {
        "id": f"pl-{code}", "line_code": code, "label": label,
        "row_kind": "title", "is_bold": True, "amounts": None, "children": [],
    }


def _l4_child(label, periods, vals, accounts):
    return {
        "id": f"l4-{label}", "line_code": f"X::{label}", "label": label,
        "row_kind": "detail", "level_4": label,
        "amounts": _amounts(periods, vals), "accounts": accounts,
    }


def _account(gid, label, periods, vals):
    return {
        "id": f"acc-{gid}", "line_code": gid, "gl_account_id": gid, "label": label,
        "row_kind": "account", "amounts": _amounts(periods, vals),
    }


def _month_periods(n):
    """n month columns [{key,label}] ending at 2025-07."""
    y, m = 2025, 7
    cols = []
    for _ in range(n):
        cols.insert(0, {"key": f"{y:04d}-{m:02d}",
                        "label": period_label(y, m) + "A"})
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return cols


# =========================================================================== #
# 1) period columns + value alignment
# =========================================================================== #
def test_pl_year_columns_three_complete_fys_no_ytd():
    """PL year columns = the last up-to-3 COMPLETE fiscal years — NO partial/YTD.

    Annual planning must never base a plan on a partial (year-to-date) year, so the
    current in-progress year is dropped.  The builder aggregates fy1=FY(y-3)/
    fy2=FY(y-2)/fy3=FY(y-1)/ytd — all three FY columns are COMPLETE (years strictly
    before the anchor), so we keep fy1/fy2/fy3 and DROP the partial ``ytd``.
    Labels are the historical-actual "FYyyA" form; fy2/fy3 match the Reporting
    Income Statement labels verbatim.
    """
    from app.services.fin_compat_sql import col_labels_annual

    cols = GV.pl_year_columns(2025, 7)
    # Three complete FYs, no ``ytd`` partial column.
    assert [c["key"] for c in cols] == ["fy1", "fy2", "fy3"]
    assert "ytd" not in {c["key"] for c in cols}
    # fy1=FY(2022), fy2=FY(2023), fy3=FY(2024) — most-recent full FY.
    assert [c["label"] for c in cols] == ["FY22A", "FY23A", "FY24A"]
    # fy2/fy3 labels are taken VERBATIM from the Reporting builder labels (no drift).
    labels = col_labels_annual(2025, 7)
    assert [c["label"] for c in cols][1:] == [labels["fy2"], labels["fy3"]]


def test_bs_year_columns_three_complete_fy_ends_no_ytd():
    cols = GV.bs_year_columns(2025, 7)
    # Period-END closing balances of the 3 complete FYs; no YTD month-end column.
    assert [c["key"] for c in cols] == ["dec_py2", "fy_py", "fy"]
    assert "cm" not in {c["key"] for c in cols}
    # Labelled by period-end (Dec) matching the Reporting Balance Sheet.
    assert [c["label"] for c in cols] == ["Dec22A", "Dec23A", "Dec24A"]


def test_month_columns_takes_last_24():
    # 31 builder month-periods → only the last 24 survive as columns.
    builder_periods = []
    y, m = 2023, 1
    for _ in range(31):
        builder_periods.append({"year": y, "month": m, "label": period_label(y, m)})
        m += 1
        if m == 13:
            m, y = 1, y + 1
    cols = GV._month_columns(builder_periods)
    assert len(cols) == 24
    assert cols[-1]["key"] == "2025-07"
    assert cols[-1]["label"] == "Jul25A"
    assert cols[0]["key"] == "2023-08"


def test_values_aligned_to_periods_missing_is_zero():
    periods = [{"key": "fy1", "label": "FY23A"}, {"key": "fy3", "label": "FY25A"}]
    # amounts only carries fy1; fy3 missing → 0.0
    assert GV._values({"fy1": 12.5}, periods) == [12.5, 0.0]
    assert GV._values(None, periods) == [0.0, 0.0]  # title rows have None amounts


# =========================================================================== #
# 2) KPI/title excluded; mapping + subtotal/grandtotal kept in order
# =========================================================================== #
def _pl_statement(periods):
    # Three columns now (FY23A, FY24A, YTDJul25A) — one value per period.
    return [
        _title("REVENUE_TITLE", "Revenue"),
        _line("NET_SALES", "Net sales", periods, [10.0, 20.0, 30.0]),
        _line("FINISHED_GOODS_WIP", "Finished goods / WIP", periods, [1.0, 2.0, 3.0]),
        _subtotal("TOTAL_OUTPUT", "Total output", periods, [11.0, 22.0, 33.0]),
        _line("COST_OF_MATERIALS", "Cost of materials", periods, [-5.0, -6.0, -7.0]),
        _subtotal("GROSS_PROFIT", "Gross profit", periods, [6.0, 16.0, 26.0]),
        _kpi("GROSS_MARGIN_PCT", "Gross margin %", periods, [54.5, 72.7, 78.8]),
        # A mapping line NOT in the plannable set — still accumulates into the
        # running sum, so it MUST appear in EBITDA's components (EBITDA = 6 + -2 = 4).
        _line("PERSONNEL", "Personnel expense", periods, [-2.0, -4.0, -6.0]),
        _subtotal("EBITDA", "EBITDA", periods, [4.0, 12.0, 20.0]),
    ]


def _plannable():
    return {
        "NET_SALES": "Net sales",
        "FINISHED_GOODS_WIP": "Finished goods / WIP",
        "COST_OF_MATERIALS": "Cost of materials",
    }


def test_kpi_and_title_excluded_structure_preserved():
    periods = GV.pl_year_columns(2025, 7)
    rows = GV.assemble_rows(_pl_statement(periods), periods, _plannable(), {})
    codes = [r["line_code"] for r in rows]
    # KPI ratio + section title dropped; mapping + subtotals kept IN ORDER.
    assert "GROSS_MARGIN_PCT" not in codes
    assert "REVENUE_TITLE" not in codes
    assert codes == [
        "NET_SALES", "FINISHED_GOODS_WIP", "TOTAL_OUTPUT",
        "COST_OF_MATERIALS", "GROSS_PROFIT", "PERSONNEL", "EBITDA",
    ]


def test_subtotal_rows_present_with_kind_subtotal():
    periods = GV.pl_year_columns(2025, 7)
    rows = GV.assemble_rows(_pl_statement(periods), periods, _plannable(), {})
    by_code = {r["line_code"]: r for r in rows}
    for code in ("GROSS_PROFIT", "EBITDA", "TOTAL_OUTPUT"):
        assert by_code[code]["kind"] == "subtotal"
        assert by_code[code]["plannable"] is False
        assert by_code[code]["is_partner_driven"] is False
    # subtotal values are copied straight from the builder amounts, aligned.
    assert by_code["GROSS_PROFIT"]["values"] == [6.0, 16.0, 26.0]


def _bs_nested_tree(periods):
    """Hand-built BS hierarchy tree (mirrors build_bs_snapshot_annual shape):
    Assets(d0) → Current assets(d1) → Trade receivables(d2 mapping line) → accounts.
    Row line_codes are hierarchy node-ids; the mapping line resolves by LABEL.
    """
    ar = {
        "id": "er-bs-d2-Assets-Current_assets-Trade_receivables",
        "line_code": "er-bs-d2-Assets-Current_assets-Trade_receivables",
        "label": "Trade receivables", "row_kind": "line", "is_bold": False,
        "amounts": _amounts(periods, [5.0, 6.0, 7.0]),
        "children": [_account("24000", "AR domestic", periods, [5.0, 6.0, 7.0])],
    }
    current = {
        "id": "er-bs-d1-Assets-Current_assets",
        "line_code": "er-bs-d1-Assets-Current_assets",
        "label": "Current assets", "row_kind": "subtotal", "is_bold": True,
        "amounts": _amounts(periods, [5.0, 6.0, 7.0]), "children": [ar],
    }
    assets = {
        "id": "er-bs-d0-Assets", "line_code": "er-bs-d0-Assets",
        "label": "Assets", "row_kind": "subtotal", "is_bold": True,
        "amounts": _amounts(periods, [5.0, 6.0, 7.0]), "children": [current],
    }
    kpi = {
        "id": "er-bs-kpi-equity-ratio", "line_code": "EQUITY_RATIO",
        "label": "Equity ratio", "row_kind": "kpi", "is_bold": False,
        "amounts": _amounts(periods, [40.0, 41.0, 42.0]), "children": [],
    }
    return [assets, kpi]


def test_bs_nested_tree_flattens_with_indent_and_grandtotal():
    periods = GV.bs_year_columns(2025, 7)
    # AR resolves by level_3 label "Trade receivables" → structure code 'AR'.
    rows = GV.assemble_rows(
        _bs_nested_tree(periods), periods, {"AR": "Trade receivables"},
        {"Trade receivables": "AR"},
    )
    by_label = {r["label"]: r for r in rows}
    # KPI excluded.
    assert "Equity ratio" not in by_label
    # depth-0 section row → grandtotal; deeper group → subtotal; leaf → line.
    assert by_label["Assets"]["kind"] == "grandtotal"
    assert by_label["Assets"]["indent"] == 0
    assert by_label["Current assets"]["kind"] == "subtotal"
    assert by_label["Current assets"]["indent"] == 1
    ar = by_label["Trade receivables"]
    assert ar["kind"] == "line"
    assert ar["indent"] == 2
    # resolved to the structure code + partner flags via the level_3 label join.
    assert ar["line_code"] == "AR"
    assert ar["plannable"] is True
    assert ar["is_partner_driven"] is True
    assert ar["partner_kind"] == "customer"
    # accounts attach to the mapping leaf; values aligned to the 3 BS columns.
    assert [a["gl_account_id"] for a in ar["accounts"]] == ["24000"]
    assert ar["accounts"][0]["values"] == [5.0, 6.0, 7.0]
    assert len(ar["values"]) == len(periods)


# =========================================================================== #
# 2b) components: subtotals/grandtotals carry the plannable mapping line_codes
#     that sum to their value; lines carry none.  Σ(component values) reconciles
#     to the subtotal's own values per period.
# =========================================================================== #
def _reconciles(subtotal_row, by_code, periods, *, tol=1e-6):
    """Σ(component rows' values[i]) == subtotal_row['values'][i] for every i."""
    comps = subtotal_row["components"]
    for i in range(len(periods)):
        got = sum(by_code[c]["values"][i] for c in comps)
        if abs(got - subtotal_row["values"][i]) > tol:
            return False, i, got, subtotal_row["values"][i]
    return True, None, None, None


def test_pl_subtotals_carry_components_that_reconcile():
    periods = GV.pl_year_columns(2025, 7)
    rows = GV.assemble_rows(_pl_statement(periods), periods, _plannable(), {})
    by_code = {r["line_code"]: r for r in rows}
    line_codes = {r["line_code"] for r in rows if r["kind"] == "line"}

    for code in ("TOTAL_OUTPUT", "GROSS_PROFIT", "EBITDA"):
        row = by_code[code]
        assert row["kind"] in ("subtotal", "grandtotal")
        assert "components" in row and row["components"], f"{code} has no components"
        # components are all mapping ('line') line_codes.
        assert all(c in line_codes for c in row["components"]), f"{code} non-line component"
        ok, i, got, want = _reconciles(row, by_code, periods)
        assert ok, f"{code} period {i}: Σ components {got} != value {want}"

    # PL running sum is CUMULATIVE over EVERY mapping line (plannable or not):
    # Gross profit = all mapping lines through Cost of materials; EBITDA also
    # includes the non-plannable PERSONNEL line that follows Gross profit.
    assert by_code["TOTAL_OUTPUT"]["components"] == ["NET_SALES", "FINISHED_GOODS_WIP"]
    assert by_code["GROSS_PROFIT"]["components"] == [
        "NET_SALES", "FINISHED_GOODS_WIP", "COST_OF_MATERIALS",
    ]
    assert by_code["EBITDA"]["components"] == [
        "NET_SALES", "FINISHED_GOODS_WIP", "COST_OF_MATERIALS", "PERSONNEL",
    ]
    # e.g. Gross profit value = 10 + 1 + (-5) = 6 in FY1; EBITDA = 6 + (-2) = 4.
    assert by_code["GROSS_PROFIT"]["values"][0] == 6.0
    assert by_code["EBITDA"]["values"][0] == 4.0


def test_pl_line_rows_have_no_components():
    periods = GV.pl_year_columns(2025, 7)
    rows = GV.assemble_rows(_pl_statement(periods), periods, _plannable(), {})
    for r in rows:
        if r["kind"] == "line":
            assert "components" not in r or r["components"] == []


def test_bs_subtotal_and_grandtotal_components_reconcile():
    periods = GV.bs_year_columns(2025, 7)
    rows = GV.assemble_rows(
        _bs_nested_tree(periods), periods, {"AR": "Trade receivables"},
        {"Trade receivables": "AR"},
    )
    by_label = {r["label"]: r for r in rows}
    by_code = {r["line_code"]: r for r in rows if r["kind"] == "line"}

    # Section-aware: the Current-assets subtotal's components are the mapping
    # leaves in its group; the Assets grandtotal's are the cumulative section set.
    current = by_label["Current assets"]   # subtotal
    assets = by_label["Assets"]            # grandtotal
    assert current["kind"] == "subtotal"
    assert assets["kind"] == "grandtotal"
    assert current["components"] == ["AR"]
    assert assets["components"] == ["AR"]

    for row in (current, assets):
        for i in range(len(periods)):
            got = sum(by_code[c]["values"][i] for c in row["components"])
            assert abs(got - row["values"][i]) <= 1e-6


# =========================================================================== #
# 3) mapping-row flags: plannable + partner + has_l4
# =========================================================================== #
def test_mapping_rows_carry_plannable_and_partner_flags():
    periods = GV.pl_year_columns(2025, 7)
    rows = GV.assemble_rows(_pl_statement(periods), periods, _plannable())
    by_code = {r["line_code"]: r for r in rows}

    net = by_code["NET_SALES"]
    assert net["kind"] == "line"
    assert net["plannable"] is True
    assert net["is_partner_driven"] is True
    assert net["partner_kind"] == "customer"
    assert net["values"] == [10.0, 20.0, 30.0]

    com = by_code["COST_OF_MATERIALS"]
    assert com["plannable"] is True
    assert com["is_partner_driven"] is True
    assert com["partner_kind"] == "supplier"

    fg = by_code["FINISHED_GOODS_WIP"]
    assert fg["plannable"] is True
    assert fg["is_partner_driven"] is False    # not a partner-driven position
    assert fg["partner_kind"] is None


def test_mapping_row_not_in_plannable_set_is_not_plannable():
    periods = GV.pl_year_columns(2025, 7)
    # NET_SALES omitted from the plannable set → plannable False, no partner flags.
    rows = GV.assemble_rows(_pl_statement(periods), periods, {})
    net = next(r for r in rows if r["line_code"] == "NET_SALES")
    assert net["plannable"] is False
    assert net["is_partner_driven"] is False
    assert net["partner_kind"] is None


# =========================================================================== #
# 4) L4 children + nested accounts; flat accounts; has_l4
# =========================================================================== #
def test_mapping_row_l4_children_carry_nested_accounts():
    periods = GV.pl_year_columns(2025, 7)
    products = _l4_child("Products", periods, [6.0, 12.0, 18.0], [
        _account("01A", "Product sales", periods, [6.0, 12.0, 18.0]),
    ])
    services = _l4_child("Services", periods, [4.0, 8.0, 12.0], [
        _account("01B", "Service sales", periods, [4.0, 8.0, 12.0]),
    ])
    remainder = _account("01N", "Unmapped sales", periods, [0.0, 0.0, 0.0])
    net_builder = _line(
        "NET_SALES", "Net sales", periods, [10.0, 20.0, 30.0],
        children=[products, services], accounts=[remainder],
    )
    rows = GV.assemble_rows([net_builder], periods, _plannable())
    net = rows[0]
    assert net["has_l4"] is True
    assert {c["level_4"] for c in net["children"]} == {"Products", "Services"}
    # each L4 child keeps its OWN accounts (L3 → L4 → account drill).
    by_l4 = {c["level_4"]: c for c in net["children"]}
    assert [a["gl_account_id"] for a in by_l4["Products"]["accounts"]] == ["01A"]
    assert [a["gl_account_id"] for a in by_l4["Services"]["accounts"]] == ["01B"]
    assert by_l4["Products"]["values"] == [6.0, 12.0, 18.0]
    assert by_l4["Products"]["accounts"][0]["values"] == [6.0, 12.0, 18.0]
    # position-level (no-L4) account survives on the row's flat accounts list.
    assert [a["gl_account_id"] for a in net["accounts"]] == ["01N"]
    # values length aligns to the period columns everywhere.
    assert len(net["values"]) == len(periods)
    for c in net["children"]:
        assert len(c["values"]) == len(periods)
        for a in c["accounts"]:
            assert len(a["values"]) == len(periods)


def test_mapping_row_without_l4_has_flag_false():
    periods = GV.pl_year_columns(2025, 7)
    row = _line("OTHER_OPEX", "Other operating expenses", periods,
                [-1.0, -2.0, -3.0],
                accounts=[_account("70A", "Sundry", periods, [-1.0, -2.0, -3.0])])
    rows = GV.assemble_rows([row], periods, {"OTHER_OPEX": "Other operating expenses"})
    opex = rows[0]
    assert opex["has_l4"] is False
    assert opex["children"] == []
    assert [a["gl_account_id"] for a in opex["accounts"]] == ["70A"]


# =========================================================================== #
# 5) partner-kind mapping (still pinned for the known plannable positions)
# =========================================================================== #
def test_partner_kind_for_known_positions():
    from app.services import budget_positions as BP
    assert BP.partner_kind_for("NET_SALES") == "customer"
    assert BP.partner_kind_for("COST_OF_MATERIALS") == "supplier"
    assert BP.partner_kind_for("AR") == "customer"
    assert BP.partner_kind_for("AP") == "supplier"
    assert BP.partner_kind_for("OTHER_OPEX") is None


# =========================================================================== #
# 6) entity scope (fail-closed) — restricted prefixes never widen
# =========================================================================== #
class _FakeSession:
    """Minimal session stub: resolve_entity_prefix issues one SELECT on
    dim_legal_entity; map a couple of codes to prefixes, everything else None."""

    _MAP = {"ACME": ("01",), "BETA": ("02",)}

    def execute(self, _stmt, params=None):
        code = (params or {}).get("e")
        prefix = self._MAP.get(code)

        class _R:
            def __init__(self, row):
                self._row = row

            def fetchone(self):
                return self._row

        return _R(prefix)


def test_resolve_ent_frag_admin_uses_entity_only():
    sess = _FakeSession()
    # admin (prefixes None), explicit entity → that entity's fragment.
    frag = GV._resolve_ent_frag(sess, entity="ACME", entity_prefixes=None)
    assert frag == "AND l.entity_prefix = '01'"
    # admin, no entity → consolidated (no filter).
    assert GV._resolve_ent_frag(sess, entity=None, entity_prefixes=None) == ""


def test_resolve_ent_frag_restricted_intersects_and_fails_closed():
    sess = _FakeSession()
    # restricted to {01}; no explicit entity → union over own prefixes only.
    frag = GV._resolve_ent_frag(sess, entity=None, entity_prefixes={"01"})
    assert frag == "AND l.entity_prefix = '01'"
    # restricted to {01}; explicit entity within scope → that entity.
    assert GV._resolve_ent_frag(sess, entity="ACME", entity_prefixes={"01"}) == \
        "AND l.entity_prefix = '01'"
    # restricted to {01}; explicit entity OUTSIDE scope → match nothing (fail-closed).
    assert GV._resolve_ent_frag(sess, entity="BETA", entity_prefixes={"01"}) == "AND 1 = 0"
    # restricted but empty prefix set → match nothing (never widen to all entities).
    assert GV._resolve_ent_frag(sess, entity=None, entity_prefixes=set()) == "AND 1 = 0"


def test_resolve_ent_frag_restricted_multi_prefix_union():
    sess = _FakeSession()
    frag = GV._resolve_ent_frag(sess, entity=None, entity_prefixes={"01", "02"})
    assert frag == "AND l.entity_prefix IN ('01', '02')"


# =========================================================================== #
# v2-gated live SHAPE test (skipped unless the v2 Postgres DB is reachable)
# =========================================================================== #
def _v2_session_or_skip():
    if os.environ.get("DB_NAME") != "finssentials_v2":
        pytest.skip("v2 DB not selected (set DB_NAME=finssentials_v2 to run)")
    try:
        from app.db import get_session
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"app.db.get_session unavailable: {exc!r}")
    gen = get_session()
    try:
        session = next(gen)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"v2 DB not reachable: {exc!r}")
    return session, gen


@pytest.mark.parametrize("statement,grain,n_cols", [
    ("PL", "month", 24),
    ("PL", "year", 3),
    ("BS", "month", 24),
    ("BS", "year", 3),
])
def test_v2_live_shape(statement, grain, n_cols):
    session, gen = _v2_session_or_skip()
    try:
        payload = GV.build_granularity_view(
            session, statement=statement, grain=grain, entity=None,
            entity_prefixes=None,
        )
    finally:
        try:
            next(gen)
        except StopIteration:
            pass

    assert payload["statement"] == statement
    assert payload["grain"] == grain
    if not payload["periods"]:
        return  # empty ledger → well-formed empty payload
    assert len(payload["periods"]) == n_cols

    kinds = {r["kind"] for r in payload["rows"]}
    codes = {r["line_code"] for r in payload["rows"]}
    # structure preserved: at least one structural (subtotal/grandtotal) row.
    assert kinds & {"subtotal", "grandtotal"}
    # KPIs / titles excluded.
    assert not (codes & {"GROSS_MARGIN_PCT", "EBITDA_MARGIN", "EQUITY_RATIO"})

    for row in payload["rows"]:
        assert row["kind"] in ("line", "subtotal", "grandtotal")
        assert len(row["values"]) == len(payload["periods"])
        # budget flags only meaningful on plannable mapping rows.
        if row["kind"] != "line":
            assert row["plannable"] is False
            assert row["is_partner_driven"] is False
        for child in row["children"]:
            assert len(child["values"]) == len(payload["periods"])
            for acc in child.get("accounts", []):
                assert len(acc["values"]) == len(payload["periods"])
        for acc in row["accounts"]:
            assert len(acc["values"]) == len(payload["periods"])

    # PL income statement must carry the well-known subtotals when populated.
    if statement == "PL":
        assert {"GROSS_PROFIT", "EBITDA"} <= codes

    # components: every subtotal/grandtotal carries mapping ('line') line_codes,
    # all referencing real line rows.  Where a structural row FULLY decomposes into
    # mapping lines (non-empty components, no injected synthetics), Σ(components'
    # values) must reconcile to its own values per period.  Each emitted line value
    # is rounded to _ROUND decimals, so summing N components admits up to ~N steps
    # of rounding error.  Some BS rows are not fully decomposable (a directly-valued
    # section with no mapping leaves → empty components; the equity grandtotal
    # carries injected net profit) — those are not asserted to reconcile.
    line_by_code = {r["line_code"]: r for r in payload["rows"] if r["kind"] == "line"}
    line_codes = set(line_by_code)
    rstep = 10.0 ** (-GV._ROUND)
    n_reconciled = 0
    for row in payload["rows"]:
        if row["kind"] not in ("subtotal", "grandtotal"):
            continue
        assert "components" in row
        comps = row["components"]
        assert all(c in line_codes for c in comps), (row["line_code"], comps)
        # Each line value is independently rounded to _ROUND decimals before being
        # summed here, while the subtotal value is rounded once — so accumulated
        # display-rounding error grows with the component count.  A small absolute
        # floor (0.05) covers it and is negligible against the (millions-scale)
        # values; this is a presentation-rounding allowance, not a logic tolerance.
        tol = max(0.05, (len(comps) + 1) * rstep)
        reconciles = comps and all(
            abs(sum(line_by_code[c]["values"][i] for c in comps) - row["values"][i]) <= tol
            for i in range(len(payload["periods"]))
        )
        if statement == "PL":
            # PL has no leafless sections: every cumulative subtotal/grandtotal
            # MUST reconcile exactly to its components (within rounding).
            assert reconciles, (row["line_code"], comps)
            n_reconciled += 1
        else:
            # BS: assert reconciliation on the fully-decomposable, non-equity rows
            # (a directly-valued section with no leaves, or the equity grandtotal
            # with injected net profit, is not expected to reconcile).
            lbl = (row.get("label") or "").lower()
            is_equity_side = any(w in lbl for w in ("equit", "eigenkapital", "liab"))
            if reconciles and not is_equity_side:
                n_reconciled += 1
    # At least the well-known fully-decomposable subtotals must reconcile.
    assert n_reconciled >= (2 if statement == "PL" else 3)


# =========================================================================== #
# v2-gated RECONCILIATION: granularity-view == Reporting statement builders.
#   The Reporting Income Statement / Balance Sheet (pl-statement / balance-sheet)
#   are the SOURCE OF TRUTH.  We reuse the SAME builders Reporting calls and
#   assert the granularity-view value per labelled period EQUALS the builder's
#   amount for the corresponding column — for the FULL-year columns AND the YTD
#   column (year grain), and for every month (month grain).  This pins the bug:
#   the year columns must NOT be shifted by a year and must carry no spurious
#   FY(y-3) column.
# =========================================================================== #
_PL_RECON_CODES = ("NET_SALES", "TOTAL_OUTPUT", "NET_PROFIT")


def _row_amounts_by_code(built_rows):
    """{line_code: amounts dict} for a (possibly nested) builder row tree.

    Mirrors ``granularity_view._code_from``: the monthly builder carries a None
    ``line_code`` and the structure code only in ``id`` ('pl-NET_SALES' /
    'er-pl-NET_SALES'), while the annual builder carries ``line_code`` directly.
    """
    out = {}

    def _code_of(r):
        code = str(r.get("line_code") or "")
        if code and not code.startswith(("pl-", "er-pl-", "bs-", "er-bs-")):
            return code
        rid = str(r.get("id") or "")
        for pre in ("er-pl-", "pl-"):
            if rid.startswith(pre):
                return rid[len(pre):]
        return ""

    def _walk(rows):
        for r in rows:
            code = _code_of(r)
            if code and isinstance(r.get("amounts"), dict):
                out.setdefault(code, r["amounts"])
            _walk(r.get("children") or [])
            _walk(r.get("accounts") or [])

    _walk(built_rows)
    return out


def _gv_values_by_code(payload):
    return {r["line_code"]: r["values"] for r in payload["rows"]}


def test_v2_pl_year_reconciles_to_reporting_annual_builder():
    """PL year: each COMPLETE-FY column value == the Reporting annual builder column.

    fy1 → FY(y-3), fy2 → FY(y-2), fy3 → FY(y-1) — all FULL fiscal years.  The
    partial ``ytd`` column is DROPPED (annual planning never uses a partial year),
    and labels are NOT shifted by a year.
    """
    from app.services.fin_compat_pl import build_pl_annual_compat
    from app.services.fin_compat_sql import col_labels_annual
    from app.services.gl_analysis_common import latest_anchor

    session, gen = _v2_session_or_skip()
    try:
        year, month = latest_anchor(session, "")
        built = build_pl_annual_compat(
            session, year=year, month=month, ent_frag_override="",
        )
        payload = GV.build_granularity_view(
            session, statement="PL", grain="year", entity=None, entity_prefixes=None,
        )
    finally:
        try:
            next(gen)
        except StopIteration:
            pass

    # Three complete-FY columns; the partial YTD column must NOT appear.
    labels = col_labels_annual(year, month)
    assert [p["key"] for p in payload["periods"]] == ["fy1", "fy2", "fy3"]
    assert "ytd" not in {p["key"] for p in payload["periods"]}
    # fy2/fy3 labels match the Reporting Income-Statement labels exactly.
    assert [p["label"] for p in payload["periods"]][1:] == [labels["fy2"], labels["fy3"]]

    builder_am = _row_amounts_by_code(built["rows"])
    gv_vals = _gv_values_by_code(payload)
    period_keys = [p["key"] for p in payload["periods"]]  # fy1, fy2, fy3
    for code in _PL_RECON_CODES:
        assert code in builder_am, f"{code} missing from reporting builder"
        assert code in gv_vals, f"{code} missing from granularity view"
        for i, key in enumerate(period_keys):
            want = round(float(builder_am[code].get(key) or 0.0), GV._ROUND)
            got = gv_vals[code][i]
            assert abs(got - want) <= 0.01, (
                f"PL year {code} @ {key}: GV {got} != reporting {want}"
            )
    # The third (most-recent COMPLETE) column equals the builder's fy3 (FY y-1) —
    # the full prior year — NOT the partial current-year ytd.
    ns = builder_am["NET_SALES"]
    assert abs(gv_vals["NET_SALES"][2] - round(float(ns["fy3"]), GV._ROUND)) <= 0.01
    if abs(float(ns.get("ytd") or 0.0) - float(ns["fy3"])) > 0.01:
        assert abs(gv_vals["NET_SALES"][2] - round(float(ns["ytd"]), GV._ROUND)) > 0.01


def test_v2_pl_month_reconciles_to_reporting_monthly_builder():
    """PL month: each of the 24 monthly values == the Reporting monthly builder's
    amount for that exact YYYY-MM month (no column shift)."""
    from app.services.fin_compat_pl import build_pl_monthly
    from app.services.gl_analysis_common import latest_anchor

    session, gen = _v2_session_or_skip()
    try:
        year, month = latest_anchor(session, "")
        built = build_pl_monthly(
            session, period_grain="month", year=year, month=month, span="fy3",
            ent_frag_override="",
        )
        payload = GV.build_granularity_view(
            session, statement="PL", grain="month", entity=None, entity_prefixes=None,
        )
    finally:
        try:
            next(gen)
        except StopIteration:
            pass

    builder_am = _row_amounts_by_code(built["rows"])
    gv_vals = _gv_values_by_code(payload)
    period_keys = [p["key"] for p in payload["periods"]]  # last 24 YYYY-MM
    assert len(period_keys) == 24
    for code in _PL_RECON_CODES:
        for i, key in enumerate(period_keys):
            want = round(float(builder_am.get(code, {}).get(key) or 0.0), GV._ROUND)
            got = gv_vals[code][i]
            assert abs(got - want) <= 0.01, (
                f"PL month {code} @ {key}: GV {got} != reporting {want}"
            )


def test_v2_bs_year_reconciles_to_reporting_snapshot_builder():
    """BS year: dec_py2 → Dec(y-3) close, fy_py → Dec(y-2) close, fy → Dec(y-1)
    close — each a COMPLETE fiscal-year-END balance equal to the Reporting snapshot
    builder column for a known BS position.  The partial ``cm`` (current-year YTD
    month-end) column is DROPPED."""
    from app.services.fin_compat_bs import build_bs_snapshot_annual
    from app.services.gl_analysis_common import latest_anchor

    session, gen = _v2_session_or_skip()
    try:
        year, month = latest_anchor(session, "")
        built = build_bs_snapshot_annual(
            session, year=year, month=month, ent_frag_override="",
        )
        payload = GV.build_granularity_view(
            session, statement="BS", grain="year", entity=None, entity_prefixes=None,
        )
    finally:
        try:
            next(gen)
        except StopIteration:
            pass

    assert [p["key"] for p in payload["periods"]] == ["dec_py2", "fy_py", "fy"]
    assert "cm" not in {p["key"] for p in payload["periods"]}

    # BS builder rows carry hierarchy node-ids as line_code, so match by LABEL
    # (the stable level-1/2/3 labels the GV preserves) instead of code.
    def _amounts_by_label(rows):
        out = {}

        def _walk(rs):
            for r in rs:
                lbl = (r.get("label") or "").strip()
                if lbl and isinstance(r.get("amounts"), dict):
                    out.setdefault(lbl, r["amounts"])
                _walk(r.get("children") or [])
                _walk(r.get("accounts") or [])

        _walk(rows)
        return out

    builder_by_label = _amounts_by_label(built["rows"])
    period_keys = [p["key"] for p in payload["periods"]]
    n_checked = 0
    for row in payload["rows"]:
        lbl = (row.get("label") or "").strip()
        if lbl not in builder_by_label:
            continue
        # The injected "Net profit" / equity grandtotals differ by the closing
        # bridge; skip equity-side rows (same exclusion as the shape test).
        if any(w in lbl.lower() for w in ("equit", "eigenkapital", "liab", "net profit")):
            continue
        bam = builder_by_label[lbl]
        for i, key in enumerate(period_keys):
            want = round(float(bam.get(key) or 0.0), GV._ROUND)
            assert abs(row["values"][i] - want) <= 0.01, (
                f"BS year '{lbl}' @ {key}: GV {row['values'][i]} != reporting {want}"
            )
        n_checked += 1
    assert n_checked >= 3, f"only {n_checked} BS rows reconciled"


# Working-capital / fixed-asset BS labels we expect to find on a populated ledger;
# at least one must reconcile so the BS history is locked on a REAL position, not
# only structural subtotals.  Matched case-insensitively against the GV row labels.
_BS_WC_HINTS = ("inventor", "receivab", "cash", "bank", "tangible", "property", "fixed")


def _bs_label_matches_wc(label: str) -> bool:
    low = (label or "").lower()
    return any(h in low for h in _BS_WC_HINTS)


def test_v2_bs_month_reconciles_to_reporting_monthly_builder():
    """BS month: each of the last 24 month-END balance columns == the Reporting
    monthly snapshot builder's balance for that exact YYYY-MM (no column shift).

    Reuses ``build_bs_monthly(span='fy3')`` — the SAME builder the granularity view
    windows — and asserts equality per month for known BS positions (matched by the
    stable level-3 LABEL, since BS rows carry hierarchy node-ids as line_code)."""
    from app.services.fin_compat_bs import build_bs_monthly
    from app.services.gl_analysis_common import latest_anchor

    session, gen = _v2_session_or_skip()
    try:
        year, month = latest_anchor(session, "")
        built = build_bs_monthly(
            session, year=year, month=month, span="fy3", ent_frag_override="",
        )
        payload = GV.build_granularity_view(
            session, statement="BS", grain="month", entity=None, entity_prefixes=None,
        )
    finally:
        try:
            next(gen)
        except StopIteration:
            pass

    if not payload["periods"]:
        pytest.skip("empty ledger")
    period_keys = [p["key"] for p in payload["periods"]]  # last 24 YYYY-MM
    assert len(period_keys) == 24

    def _amounts_by_label(rows):
        out = {}

        def _walk(rs):
            for r in rs:
                lbl = (r.get("label") or "").strip()
                if lbl and isinstance(r.get("amounts"), dict):
                    out.setdefault(lbl, r["amounts"])
                _walk(r.get("children") or [])
                _walk(r.get("accounts") or [])

        _walk(rows)
        return out

    builder_by_label = _amounts_by_label(built["rows"])
    n_checked = 0
    wc_checked = 0
    for row in payload["rows"]:
        lbl = (row.get("label") or "").strip()
        if lbl not in builder_by_label:
            continue
        if any(w in lbl.lower() for w in ("equit", "eigenkapital", "liab", "net profit")):
            continue
        bam = builder_by_label[lbl]
        for i, key in enumerate(period_keys):
            want = round(float(bam.get(key) or 0.0), GV._ROUND)
            assert abs(row["values"][i] - want) <= 0.01, (
                f"BS month '{lbl}' @ {key}: GV {row['values'][i]} != reporting {want}"
            )
        n_checked += 1
        if _bs_label_matches_wc(lbl):
            wc_checked += 1
    assert n_checked >= 3, f"only {n_checked} BS rows reconciled (month)"
    # Lock the history on at least one real working-capital / fixed-asset position
    # (Inventory / Receivables / Cash / Tangible), not just structural subtotals.
    assert wc_checked >= 1, "no inventory/receivables/cash/tangible BS row reconciled"
