"""Pure-math tests for app/services/gl_hierarchy.py (hierarchical anomaly, Phase 0).

The single load-bearing property is RECONCILIATION: rolling up the already-presented
per-account series must conserve value at every level —

    Σ L4 children (of an L3)        == that L3 node           (per period, to 1e-6)
    Σ accounts in an L3             == that L3 node           (the leaves)
    Σ entity_split over prefixes    == the consolidated node  (per period)

No DB, no network: ``AccountSeries`` fixtures are hand-built (the safe-default
``level_4``/``l4_sub`` fields make this trivial), so these tests pin the pure
summation + grouping only — never the SQL.  CLAUDE.md rule #2 (synthetic only).

Worked example (consolidated, two entities under one L3 "Net sales"):
    01 / acct A (L4 "Products") : 2024-01 = 100, 2024-02 = 150
    02 / acct B (L4 "Products") : 2024-01 =  40, 2024-02 =  60
    01 / acct C (L4 ""         ) : 2024-01 =  10, 2024-02 =  20  → "(no L4)" bucket
  L4 "Products"  : 2024-01 = 140, 2024-02 = 210
  L4 "(no L4)"   : 2024-01 =  10, 2024-02 =  20
  L3 "Net sales" : 2024-01 = 150, 2024-02 = 230  ( == Σ L4 == Σ accounts )
  entity 01 split: 110, 170 ;  entity 02 split: 40, 60  ( Σ == consolidated )
"""
from __future__ import annotations

import pytest

from app.services import gl_hierarchy as H
from app.services.gl_analysis_common import AccountSeries, MonthPoint
from app.services.fin_compat_sql import period_key, period_label


# --------------------------------------------------------------------------- #
# Fixture helpers
# --------------------------------------------------------------------------- #
def _series(points: list[tuple[int, int, float]]) -> list[MonthPoint]:
    return [
        MonthPoint(
            fiscal_year=y, fiscal_period=m, period_key=period_key(y, m),
            label=period_label(y, m), value_keur=v,
        )
        for (y, m, v) in points
    ]


def _acc(
    ang: str, ep: str, *, l0="PL", l2="Income", l3="Net sales", l4="",
    points: list[tuple[int, int, float]],
) -> AccountSeries:
    return AccountSeries(
        gl_account_id=ang, account_name=f"acct {ang}", account_number_group=ang,
        entity_prefix=ep, level_0=l0, level_2=l2, level_3=l3, level_4=l4,
        l4_sub="", series=_series(points),
    )


def _fixture() -> list[AccountSeries]:
    """The worked-example ledger (one L3 'Net sales', two entities, one blank L4)."""
    return [
        _acc("01A", "01", l4="Products", points=[(2024, 1, 100.0), (2024, 2, 150.0)]),
        _acc("02B", "02", l4="Products", points=[(2024, 1, 40.0), (2024, 2, 60.0)]),
        _acc("01C", "01", l4="", points=[(2024, 1, 10.0), (2024, 2, 20.0)]),
    ]


def _at(node: H.AggSeries, y: int, m: int) -> float:
    for p in node.series:
        if p.fiscal_year == y and p.fiscal_period == m:
            return p.value_keur
    return 0.0


# =========================================================================== #
# (1) L3 aggregation sums the leaf accounts
# =========================================================================== #
def test_l3_aggregates_leaf_accounts():
    l3_nodes = H.aggregate_l3(_fixture())
    assert len(l3_nodes) == 1
    node = l3_nodes[0]
    assert node.key == "Net sales"
    assert node.level_4 is None
    assert _at(node, 2024, 1) == pytest.approx(150.0)   # 100 + 40 + 10
    assert _at(node, 2024, 2) == pytest.approx(230.0)   # 150 + 60 + 20
    # members are the rolled-up account_number_groups, sorted + de-duplicated
    assert node.members == ["01A", "01C", "02B"]


def test_l3_node_reconciles_to_its_leaves_per_period():
    accounts = _fixture()
    node = H.aggregate_l3(accounts)[0]
    for (y, m) in [(2024, 1), (2024, 2)]:
        leaf_sum = sum(a.value_at(y, m) for a in accounts)
        assert _at(node, y, m) == pytest.approx(leaf_sum, abs=1e-6)


# =========================================================================== #
# (2) L4 aggregation sums to the L3 node (the reconciliation property)
# =========================================================================== #
def test_l4_children_sum_to_l3():
    accounts = _fixture()
    l3 = H.aggregate_l3(accounts)[0]
    l4_nodes = H.aggregate_l4(accounts, level_3="Net sales")

    # Two L4 buckets: "Products" and the "(no L4)" bucket.
    keys = {n.key for n in l4_nodes}
    assert keys == {"Products", H.NO_L4_BUCKET}
    for n in l4_nodes:
        assert n.level_4 == n.key  # L4 nodes carry their level_4

    # Σ L4 == L3, per period, to 1e-6.
    for (y, m) in [(2024, 1), (2024, 2)]:
        l4_sum = sum(_at(n, y, m) for n in l4_nodes)
        assert l4_sum == pytest.approx(_at(l3, y, m), abs=1e-6)


def test_blank_level_4_lands_in_no_l4_bucket():
    accounts = _fixture()
    l4_nodes = H.aggregate_l4(accounts, level_3="Net sales")
    no_l4 = next(n for n in l4_nodes if n.key == H.NO_L4_BUCKET)
    assert no_l4.members == ["01C"]          # only the blank-L4 account
    assert _at(no_l4, 2024, 1) == pytest.approx(10.0)
    assert _at(no_l4, 2024, 2) == pytest.approx(20.0)

    products = next(n for n in l4_nodes if n.key == "Products")
    assert products.members == ["01A", "02B"]
    assert _at(products, 2024, 1) == pytest.approx(140.0)  # 100 + 40


# =========================================================================== #
# (3) entity_split reconciles to the consolidated node
# =========================================================================== #
def test_entity_split_sums_to_consolidated():
    node = H.aggregate_l3(_fixture())[0]
    assert set(node.entity_split) == {"01", "02"}

    for (y, m) in [(2024, 1), (2024, 2)]:
        split_sum = sum(
            next((p.value_keur for p in pts
                  if p.fiscal_year == y and p.fiscal_period == m), 0.0)
            for pts in node.entity_split.values()
        )
        assert split_sum == pytest.approx(_at(node, y, m), abs=1e-6)

    # entity 01 = acct A (100,150) + acct C (10,20) ; entity 02 = acct B (40,60)
    e01 = {(p.fiscal_year, p.fiscal_period): p.value_keur for p in node.entity_split["01"]}
    e02 = {(p.fiscal_year, p.fiscal_period): p.value_keur for p in node.entity_split["02"]}
    assert e01[(2024, 1)] == pytest.approx(110.0)
    assert e01[(2024, 2)] == pytest.approx(170.0)
    assert e02[(2024, 1)] == pytest.approx(40.0)


def test_l4_entity_split_reconciles():
    l4_nodes = H.aggregate_l4(_fixture(), level_3="Net sales")
    products = next(n for n in l4_nodes if n.key == "Products")
    # Products spans both entities (A under 01, B under 02).
    assert set(products.entity_split) == {"01", "02"}
    for (y, m) in [(2024, 1), (2024, 2)]:
        split_sum = sum(
            next((p.value_keur for p in pts
                  if p.fiscal_year == y and p.fiscal_period == m), 0.0)
            for pts in products.entity_split.values()
        )
        assert split_sum == pytest.approx(_at(products, y, m), abs=1e-6)


# =========================================================================== #
# (4) Deterministic ordering + grouping across multiple L3 / L2
# =========================================================================== #
def test_l3_nodes_sorted_deterministically():
    accounts = [
        _acc("01Z", "01", l2="Income", l3="Other income", points=[(2024, 1, 5.0)]),
        _acc("01A", "01", l2="Income", l3="Net sales", points=[(2024, 1, 100.0)]),
        _acc("01B", "01", l0="BS", l2="Assets", l3="Receivables", points=[(2024, 1, 7.0)]),
    ]
    nodes = H.aggregate_l3(accounts)
    keys = [(n.level_0, n.level_2, n.level_3) for n in nodes]
    assert keys == sorted(keys)  # stable, sorted by (level_0, level_2, level_3)


def test_aggregate_l4_filters_to_requested_l3():
    accounts = [
        _acc("01A", "01", l3="Net sales", l4="Products", points=[(2024, 1, 100.0)]),
        _acc("01D", "01", l3="Other income", l4="Rent", points=[(2024, 1, 9.0)]),
    ]
    l4 = H.aggregate_l4(accounts, level_3="Net sales")
    assert {n.key for n in l4} == {"Products"}  # "Other income"/Rent excluded


def test_empty_inputs_return_empty():
    assert H.aggregate_l3([]) == []
    assert H.aggregate_l4([], level_3="Net sales") == []
    assert H.aggregate_l4(_fixture(), level_3="Nonexistent") == []
