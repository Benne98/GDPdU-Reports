"""Unit tests for GL-derived sales analytics helpers."""
from datetime import date
from unittest.mock import MagicMock

import app.services.sales_analytics_compat as mod
from app.services.fin_compat_sql import last_day
from app.services.sales_analytics_compat import (
    _ALLOWED_DIMS,
    _allocate_gp,
    _dim_expr,
    _month_bounds,
    _normalize_dim,
    build_breakdown_table,
    build_churn_bridge,
    build_dimension_performance,
    build_geo_trend,
)


def test_month_bounds_uses_last_day_date():
    start, end = _month_bounds(2025, 7)
    assert start == date(2025, 7, 1)
    assert end == last_day(2025, 7)
    assert end == date(2025, 7, 31)


def test_allocate_gp_splits_com_by_revenue_share_within_entity():
    sales = [
        ("North", "E1", 100.0),
        ("South", "E1", 100.0),
        ("North", "E2", 50.0),
    ]
    com = {"E1": 40.0, "E2": 10.0}
    out = _allocate_gp(sales, com)
    assert out["North"] == (150.0, 120.0, 80.0)
    assert out["South"] == (100.0, 80.0, 80.0)


def test_allocate_gp_empty_com_yields_full_margin():
    sales = [("A", "E1", 80.0)]
    out = _allocate_gp(sales, {})
    assert out["A"] == (80.0, 80.0, 100.0)


def test_build_geo_trend_matches_frontend_chart_contract(monkeypatch):
    """GeoTrendChart expects regions: string[] and periods: {label, <seg>: number}[]."""

    def fake_metrics(_session, _d0, _d1, _dim, _entity):
        return {
            "North": (100.0, 40.0, 40.0),
            "South": (50.0, 20.0, 40.0),
        }

    monkeypatch.setattr(
        "app.services.sales_analytics_compat._period_metrics",
        fake_metrics,
    )
    out = build_geo_trend(MagicMock(), 2025, 7, grain="month", dim="end_customer_region")
    assert out["regions"] == ["North", "South"]
    assert isinstance(out["periods"], list)
    assert len(out["periods"]) == 12
    first = out["periods"][0]
    assert "label" in first
    assert first["North"] == 100.0
    assert first["South"] == 50.0


# ---------------------------------------------------------------------------
# Canonical 4-dimension vocabulary
# ---------------------------------------------------------------------------

def test_dim_vocabulary_is_the_four_canonical_keys():
    assert _ALLOWED_DIMS == frozenset({
        "end_customer_region", "end_customer_city", "end_customer_name", "entity",
    })
    # legacy alias folds into the customer dim; junk normalises to region.
    assert _normalize_dim("top_customers") == "end_customer_name"
    assert _normalize_dim("garbage") == "end_customer_region"
    assert _normalize_dim("end_customer_city") == "end_customer_city"
    assert "dc.city" in _dim_expr("end_customer_city")


# ---------------------------------------------------------------------------
# BUG 2 — breakdown leaf rows keyed l1/l2/l3
# ---------------------------------------------------------------------------

def test_build_breakdown_table_emits_leaf_rows_no_mid(monkeypatch):
    monkeypatch.setattr(mod, "_entity_frag_scoped", lambda *_a, **_k: "")

    def fake_leaf(_session, d0, _d1, _dims, _ent):
        if d0 == date(2025, 7, 1):  # CM
            return {("North", "E1"): (100.0, 60.0, 60.0), ("South", "E2"): (50.0, 30.0, 60.0)}
        return {("North", "E1"): (80.0, 50.0, 62.5)}  # PM

    monkeypatch.setattr(mod, "_leaf_metrics", fake_leaf)
    out = build_breakdown_table(
        MagicMock(), 2025, 7, dim_top="end_customer_region", dim_mid="", dim_bottom="entity",
    )
    assert out["has_mid_level"] is False
    row = out["rows"][0]
    assert {"l1", "l2", "l3", "gs_cm", "gp_cm", "gm_cm", "gm_plan_cm"}.issubset(row)
    assert row["l1"] == "North" and row["l3"] == "E1" and row["l2"] is None
    assert row["gs_cm"] == 100.0 and row["gs_pm"] == 80.0
    assert row["delta_gs_cm_pm"] == 20.0


def test_build_breakdown_table_mid_level_fills_l2(monkeypatch):
    monkeypatch.setattr(mod, "_entity_frag_scoped", lambda *_a, **_k: "")
    monkeypatch.setattr(
        mod, "_leaf_metrics",
        lambda *_a, **_k: {("North", "Berlin", "E1"): (10.0, 6.0, 60.0)},
    )
    out = build_breakdown_table(
        MagicMock(), 2025, 7,
        dim_top="end_customer_region", dim_mid="end_customer_city", dim_bottom="entity",
    )
    assert out["has_mid_level"] is True
    row = out["rows"][0]
    assert row["l1"] == "North" and row["l2"] == "Berlin" and row["l3"] == "E1"


def test_build_breakdown_table_fail_closed_empty_visibility():
    out = build_breakdown_table(MagicMock(), 2025, 7, allowed_entities=set())
    assert out["rows"] == []


# ---------------------------------------------------------------------------
# BUG 3 — dimension performance matrix + segment deltas
# ---------------------------------------------------------------------------

def test_build_dimension_performance_matrix_and_segments(monkeypatch):
    monkeypatch.setattr(mod, "_entity_frag_scoped", lambda *_a, **_k: "")
    monkeypatch.setattr(
        mod, "_period_metrics",
        lambda *_a, **_k: {"E1": (100.0, 40.0, 40.0), "E2": (50.0, 20.0, 40.0)},
    )
    out = build_dimension_performance(MagicMock(), 2025, 3, dim="entity", metric="gross_sales")
    # matrix: Jan..Mar → 3 columns; each row values aligned + total.
    assert [c["key"] for c in out["matrix"]["columns"]] == ["m1", "m2", "m3"]
    mrow = out["matrix"]["rows"][0]
    assert len(mrow["values"]) == 3
    assert mrow["total"] == round(sum(mrow["values"]), 2)
    # chart segments carry the new plan/delta fields.
    seg = out["chart"]["segments"][0]
    assert seg["has_plan"] is False and seg["plan"] == 0.0 and seg["delta_plan"] == 0.0
    assert seg["delta_prior"] == round(seg["actual"] - seg["prior"], 2)


def test_build_dimension_performance_fail_closed_empty_visibility():
    out = build_dimension_performance(MagicMock(), 2025, 7, allowed_entities=set())
    assert out["chart"]["segments"] == []
    assert out["matrix"]["rows"] == [] and out["matrix"]["columns"] == []


# ---------------------------------------------------------------------------
# BUG 4 — churn bridge aggregate + table_rows + reconciliation identity
# ---------------------------------------------------------------------------

def test_build_churn_bridge_reconciles_and_reshapes(monkeypatch):
    monkeypatch.setattr(mod, "_entity_frag_scoped", lambda *_a, **_k: "")
    # rows: seg, from_rev, to_rev, new_rev, lost_rev(+), retained_delta
    # constructed consistent: to = from + new + delta - lost
    rows = [
        ("E1", 100.0, 120.0, 30.0, 10.0, 0.0),
        ("E2", 50.0, 40.0, 0.0, 5.0, -5.0),
    ]
    session = MagicMock()
    session.execute.return_value.fetchall.return_value = rows
    out = build_churn_bridge(session, 2025, 7, dim="entity")

    assert len(out["periods"]) == 2
    from_total, to_total = out["period_totals"]
    b = out["bridge"]
    # RECONCILIATION IDENTITY (waterfall closes exactly):
    recon = from_total + b["new"] + b["upsell"] + b["cross_sell"] + b["downsell"] + b["lost"]
    assert abs(recon - to_total) < 1e-6
    # signs
    assert b["new"] >= 0 and b["upsell"] >= 0 and b["cross_sell"] >= 0
    assert b["downsell"] <= 0 and b["lost"] <= 0
    assert b["lost"] == -15.0 and b["downsell"] == -5.0 and b["new"] == 30.0
    # table_rows: one per segment, sorted by to desc
    assert [r["dim_value"] for r in out["table_rows"]] == ["E1", "E2"]
    assert out["table_rows"][0]["lost"] == -10.0
    assert "bridges" not in out  # old per-segment key is gone


def test_build_churn_bridge_fail_closed_empty_visibility():
    out = build_churn_bridge(MagicMock(), 2025, 7, allowed_entities=set())
    assert out["table_rows"] == []
    assert out["bridge"] == {
        "new": 0.0, "upsell": 0.0, "cross_sell": 0.0, "downsell": 0.0, "lost": 0.0,
    }


# ---------------------------------------------------------------------------
# GAP 1 — breakdown with dim_bottom=end_customer_name (3-level drill regression)
# ---------------------------------------------------------------------------

def test_build_breakdown_table_region_city_customer_name_non_empty(monkeypatch):
    """Regression guard: breakdown with dim_bottom=end_customer_name must not return empty rows.

    The old empty-payload bug returned rows=[] when dim_bottom was end_customer_name;
    this test locks that path with the exact 3-dim combination the router exposes
    (end_customer_region / end_customer_city / end_customer_name).

    Synthetic fact (kEUR):
        CM 2025-07 — DACH/Munich/Acme GmbH: gs=200, gp=120, gm=60 %
                       DACH/Berlin/Beta AG:   gs=80,  gp=40,  gm=50 %
        PM 2025-06 — DACH/Munich/Acme GmbH: gs=180, gp=100, gm=55.6 %
    Expected: 2 leaf rows, Acme first (higher CM gs), delta_gs_cm_pm=20 for Acme.
    """
    monkeypatch.setattr(mod, "_entity_frag_scoped", lambda *_a, **_k: "")

    def fake_leaf(_session, d0, _d1, _dims, _ent):
        # d0 is date(2025,7,1) for CM and date(2025,6,1) for PM
        if d0.month == 7:  # CM
            return {
                ("DACH", "Munich", "Acme GmbH"): (200.0, 120.0, 60.0),
                ("DACH", "Berlin", "Beta AG"):   ( 80.0,  40.0, 50.0),
            }
        return {("DACH", "Munich", "Acme GmbH"): (180.0, 100.0, 55.6)}  # PM

    monkeypatch.setattr(mod, "_leaf_metrics", fake_leaf)
    out = build_breakdown_table(
        MagicMock(), 2025, 7,
        dim_top="end_customer_region",
        dim_mid="end_customer_city",
        dim_bottom="end_customer_name",
    )

    assert out["has_mid_level"] is True
    assert len(out["rows"]) > 0, "regression: breakdown must not return empty rows"

    # All required metric fields must be present on every leaf row
    required = {
        "l1", "l2", "l3",
        "gs_cm", "gp_cm", "gm_cm",
        "gs_pm", "gp_pm", "gm_pm",
        "gs_plan_cm", "gp_plan_cm", "gm_plan_cm",
        "delta_gs_cm_pm", "delta_gp_cm_pm",
    }
    for row in out["rows"]:
        assert row["l1"] is not None, "l1 (region) must never be None"
        assert row["l3"] is not None, "l3 (customer name) must never be None"
        assert required.issubset(row), f"missing metric fields: {required - set(row)}"

    # Sorted by CM gross sales desc: Acme first
    top = out["rows"][0]
    assert top["l1"] == "DACH"
    assert top["l2"] == "Munich"
    assert top["l3"] == "Acme GmbH"
    assert top["gs_cm"] == 200.0
    assert top["gp_cm"] == 120.0
    assert top["delta_gs_cm_pm"] == 20.0   # 200 - 180 = 20


# ---------------------------------------------------------------------------
# GAP 3 — visibility positive case: allowed_entities=None (admin) returns full data
# ---------------------------------------------------------------------------

def test_visibility_admin_none_breakdown_returns_full_data(monkeypatch):
    """allowed_entities=None (admin) must not take the fail-closed branch in build_breakdown_table.

    _entity_frag_scoped routes None → _entity_frag (legacy single-entity path).
    Patching _entity_frag to '' avoids live DB; the function must return non-empty rows.
    """
    monkeypatch.setattr(mod, "_entity_frag", lambda *_a: "")
    monkeypatch.setattr(
        mod, "_leaf_metrics",
        lambda *_a, **_k: {("North", "E1"): (100.0, 60.0, 60.0)},
    )
    out = build_breakdown_table(
        MagicMock(), 2025, 7,
        allowed_entities=None,  # admin: unrestricted
    )
    assert out["rows"] != [], "admin path (allowed_entities=None) must return data, not be fail-closed"
    assert out["rows"][0]["gs_cm"] == 100.0


def test_visibility_admin_none_dim_perf_returns_full_data(monkeypatch):
    """allowed_entities=None (admin) bypasses fail-closed sentinel in build_dimension_performance.

    Asserts segments, matrix columns, and matrix rows are all non-empty — the
    opposite of the zeroed shape returned for an empty allowed set.
    """
    monkeypatch.setattr(mod, "_entity_frag", lambda *_a: "")
    monkeypatch.setattr(
        mod, "_period_metrics",
        lambda *_a, **_k: {"E1": (100.0, 40.0, 40.0)},
    )
    out = build_dimension_performance(
        MagicMock(), 2025, 3,
        allowed_entities=None,  # admin: unrestricted
    )
    assert out["chart"]["segments"] != [], "admin: chart segments must be non-empty"
    assert out["matrix"]["columns"] != [], "admin: matrix columns must be non-empty"
    assert out["matrix"]["rows"] != [], "admin: matrix rows must be non-empty"
    # Alignment invariant: values length == columns length for every row
    ncols = len(out["matrix"]["columns"])
    for row in out["matrix"]["rows"]:
        assert len(row["values"]) == ncols, "matrix values must align to columns"


def test_visibility_admin_none_churn_returns_full_data(monkeypatch):
    """allowed_entities=None (admin) bypasses fail-closed sentinel in build_churn_bridge.

    Synthetic fact: E1 — PM=100, CM=120, new=30, lost=10, retained_delta=0.
    Waterfall reconciliation: 100 + 30 + 0 + 0 + 0 - 10 = 120. bridge[new]=30.
    """
    monkeypatch.setattr(mod, "_entity_frag", lambda *_a: "")
    session = MagicMock()
    session.execute.return_value.fetchall.return_value = [
        # seg,  from_rev, to_rev, new_rev, lost_rev(+), retained_delta
        ("E1", 100.0,    120.0,  30.0,    10.0,         0.0),
    ]
    out = build_churn_bridge(session, 2025, 7, allowed_entities=None)
    assert out["table_rows"] != [], "admin: churn table_rows must be non-empty"
    assert out["bridge"]["new"] == 30.0
    assert out["bridge"]["lost"] == -10.0
    # Waterfall reconciliation identity
    from_total, to_total = out["period_totals"]
    b = out["bridge"]
    recon = from_total + b["new"] + b["upsell"] + b["cross_sell"] + b["downsell"] + b["lost"]
    assert abs(recon - to_total) < 1e-6
