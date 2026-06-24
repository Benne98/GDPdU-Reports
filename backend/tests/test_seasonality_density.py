"""Tests for the seasonality booking-density gate (anomaly rework, Phase 1).

PURE + DB-FREE.  Two halves:

  * ``booking_density_ok`` / ``insufficient_booking_density`` — the 8/12 month rule,
    the multi-year active-ratio, the < ``min_years`` short-circuit, and a dense pass.
    These lock the CLAUDE.md rule-#1 worked example documented on the function.
  * ``build_seasonality_tree`` EXCLUDES a sparse account/position while
    ``build_outlier_tree`` (built from the SAME bounded set) still INCLUDES it — i.e.
    the sparse account is not silently lost, it just moves to the outlier tree.

Synthetic fixtures only (CLAUDE.md rule #2).  The tree entry points are exercised by
monkeypatching the single DB pull (``build_account_monthly_series``), the materiality
anchor (``latest_anchor``), and neutralising material bounding — mirroring
``test_gl_anomaly_tree.py``.
"""
from __future__ import annotations

from app.services import gl_anomaly_tree as T
from app.services import gl_seasonality as S
from app.services.gl_analysis_common import AccountSeries, MonthPoint
from app.services.fin_compat_sql import period_key, period_label


# --------------------------------------------------------------------------- #
# Fixture helpers (same shape as test_gl_anomaly_tree.py)
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
    ang: str, ep: str = "01", *, l0="PL", l2="Income", l3="Net sales", l4="",
    name=None, points: list[tuple[int, int, float]],
) -> AccountSeries:
    return AccountSeries(
        gl_account_id=ang, account_name=name or f"acct {ang}",
        account_number_group=ang, entity_prefix=ep,
        level_0=l0, level_2=l2, level_3=l3, level_4=l4, l4_sub="",
        series=_series(points),
    )


def _months(year: int, months: list[int], value: float = 100.0):
    """Booked months (1..12) within one fiscal year, each at ``value``."""
    return [(year, m, value) for m in months]


def _full_year(year: int, value: float = 100.0):
    return _months(year, list(range(1, 13)), value)


# =========================================================================== #
# (1) booking_density_ok — the 8/12 month rule
# =========================================================================== #
def test_density_active_year_needs_min_months():
    # Two years, BOTH active in exactly 8 distinct months → 8 >= 8 → active.
    pts = _months(2023, list(range(1, 9))) + _months(2024, list(range(1, 9)))
    assert S.booking_density_ok(_series(pts)) is True

    # Two years, BOTH active in only 7 months → 7 < 8 → no active year → ratio 0.
    pts7 = _months(2023, list(range(1, 8))) + _months(2024, list(range(1, 8)))
    assert S.booking_density_ok(_series(pts7)) is False


# =========================================================================== #
# (2) Worked example from the docstring: 3 years 9/10/2 → ratio 0.67 ≥ 0.5 → OK
# =========================================================================== #
def test_density_worked_example_three_years_ratio():
    pts = (
        _months(2022, list(range(1, 10)))    # 9 months → active
        + _months(2023, list(range(1, 11)))  # 10 months → active
        + _months(2024, list(range(1, 3)))   # 2 months → NOT active
    )
    # 2 of 3 years active → ratio 0.667 ≥ 0.5 AND 3 years ≥ 2 → OK
    assert S.booking_density_ok(_series(pts)) is True


def test_density_punctual_account_fails():
    # 1 booked month in each of 3 years → 0 active years → ratio 0 < 0.5 → FAILS.
    pts = _months(2022, [6]) + _months(2023, [6]) + _months(2024, [6])
    assert S.booking_density_ok(_series(pts)) is False
    assert S.insufficient_booking_density(_series(pts)) is True


# =========================================================================== #
# (3) < min_years history → always False (even if that one year is fully dense)
# =========================================================================== #
def test_density_single_year_fails_min_years():
    pts = _full_year(2024)               # 12 months but ONE year only
    assert S.booking_density_ok(_series(pts)) is False
    assert S.insufficient_booking_density(_series(pts)) is True


def test_density_empty_series_fails():
    assert S.booking_density_ok([]) is False
    assert S.insufficient_booking_density([]) is True


# =========================================================================== #
# (4) Dense multi-year account → True; zero-valued months don't count as booked
# =========================================================================== #
def test_density_dense_account_ok():
    pts = _full_year(2022) + _full_year(2023) + _full_year(2024)
    assert S.booking_density_ok(_series(pts)) is True
    assert S.insufficient_booking_density(_series(pts)) is False


def test_density_zero_value_months_are_not_booked():
    # 2 years each "present" in all 12 months but only 3 of them non-zero → 3 < 8
    # → not active → fails.  Confirms a zero booking is not a real booking month.
    def _yr(year):
        return [(year, m, (100.0 if m <= 3 else 0.0)) for m in range(1, 13)]

    pts = _yr(2023) + _yr(2024)
    assert S.booking_density_ok(_series(pts)) is False


def test_density_custom_thresholds():
    # Loosen the gate: min_months=3, ratio 0.5 → the 3-non-zero-month case passes.
    def _yr(year):
        return [(year, m, 100.0) for m in range(1, 4)]

    pts = _yr(2023) + _yr(2024)
    assert S.booking_density_ok(_series(pts), min_months=3) is True


# =========================================================================== #
# (5) Tree: sparse account EXCLUDED from seasonality but KEPT in outliers
# =========================================================================== #
def _patch_pull(monkeypatch, accounts, anchor=(2024, 12)):
    monkeypatch.setattr(
        T, "build_account_monthly_series",
        lambda session, *, level_0=None: list(accounts),
    )
    monkeypatch.setattr(T, "latest_anchor", lambda session, ent_frag="": anchor)
    monkeypatch.setattr(
        T, "bound_material_accounts",
        lambda accs, *, current_year, current_period, top_n=None: (
            list(accs)[:top_n] if top_n is not None else list(accs)
        ),
    )


def _all_keys(tree):
    keys = set()

    def _walk(node):
        if node["level"] == "account":
            keys.add(node["key"])
        for c in node["children"]:
            _walk(c)

    for l3 in tree:
        _walk(l3)
    return keys


def test_seasonality_excludes_sparse_account_outliers_keeps_it(monkeypatch):
    # DENSE account: full 3-year monthly history under L3 "Net sales".
    dense = _acc(
        "01100", l3="Net sales", l4="Products",
        points=_full_year(2022) + _full_year(2023) + _full_year(2024),
    )
    # SPARSE account: booked once a year in 3 years under a DIFFERENT L3 position.
    sparse = _acc(
        "01900", l3="Rare provisions", l4="Misc",
        points=_months(2022, [6]) + _months(2023, [6]) + _months(2024, [6]),
    )
    _patch_pull(monkeypatch, [dense, sparse])

    seas = T.build_seasonality_tree(object(), top_n_per_level=50)
    out = T.build_outlier_tree(object(), top_n_per_level=50)

    seas_keys = _all_keys(seas["tree"])
    out_keys = _all_keys(out["tree"])

    # Sparse account dropped from seasonality, dense one kept.
    assert "01100" in seas_keys
    assert "01900" not in seas_keys
    # Its whole L3 position (no dense account) is gone from the seasonality tree.
    assert "Rare provisions" not in {n["level_3"] for n in seas["tree"]}
    # Outlier tree (built from the same bounded set) still has BOTH — not lost.
    assert {"01100", "01900"} <= out_keys
    # Transparency counters on the seasonality meta.
    assert seas["meta"]["accounts_in"] == 2
    assert seas["meta"]["accounts_dense"] == 1


def test_seasonality_position_keeps_only_dense_account(monkeypatch):
    # One L3/L4 position with a dense AND a sparse account → position stays but shows
    # only the dense account (rolled-up: at least one dense child keeps the node).
    dense = _acc(
        "01100", l3="Net sales", l4="Products",
        points=_full_year(2022) + _full_year(2023) + _full_year(2024),
    )
    sparse = _acc(
        "01200", l3="Net sales", l4="Products",
        points=_months(2022, [3]) + _months(2023, [3]) + _months(2024, [3]),
    )
    _patch_pull(monkeypatch, [dense, sparse])

    seas = T.build_seasonality_tree(object(), top_n_per_level=50)
    seas_keys = _all_keys(seas["tree"])
    assert "01100" in seas_keys           # dense kept
    assert "01200" not in seas_keys       # sparse dropped
    # The position itself survives (it has a dense account).
    assert "Net sales" in {n["level_3"] for n in seas["tree"]}
    assert seas["meta"]["accounts_dense"] == 1
